#!/usr/bin/env python3
"""Mod development runtime for the sony-a6400 QEMU guest (WSL side).

Runs the unchanged ARM Mod Loader v2 inside the emulated camera and keeps it in sync with host
build outputs:

  1. Before boot, the guard and its request are staged on the run's disposable /setting overlay
     (they are too large for the serial line). See ``setting_files``.
  2. After the guest shell answers and the main firmware reaches its menu checkpoint, a tmpfs is
     mounted at /tmp_bt (guest only), the core and every mod are pushed over the serial console,
     each file is checked with the guard's ``fnv`` operation and published by rename, and the
     guard runs ``check`` then ``install``. The guard relocates the shim and hooks for this
     process layout; nothing here writes guest memory directly.
  3. A watcher polls the host files. A changed ``.mod`` is pushed and handed to the core's reload
     request (``/tmp_bt/mlv2/reload``, see docs/evidence/0576-mod-loader-v2-reload-request.md); a changed ``core.bin`` is
     pushed and picked up by the shim at the next menu root. The core acts on the Sony UI thread
     at the next menu refresh, so a ``nudge`` callback can send menu keys when nothing happens.
  4. The panel toolbar queues actions (``request``): load or watch another mod file, force a
     reload, stop watching and delete a mod file in the guest, re-push the core. The watcher
     thread runs them one at a time between polls, so the serial console has one user.

The serial console uses QEMU's ``-serial unix:PATH,server=on,wait=off`` (run.sh ``SERIAL``).
"""
from __future__ import annotations

import os
import queue
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

GUARD_NAME, REQ_NAME = "MLV2GRD.ELF", "MLV2.REQ"
GUEST_GUARD, GUEST_REQ = "/tmp_bt/mlv2_guard", "/tmp_bt/mlv2.req"
RELOAD_REQ, RELOAD_RESULT = "/tmp_bt/mlv2/reload", "/tmp_bt/mlv2/reload.result"
MOD_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,40}\.mod$")
RAW_CHUNK = 3584        # bytes per raw transfer step: below the guest 4 KiB terminal buffer
CHUNK = 200            # bytes per printf line: 4 chars each; the guest tty truncates lines near 1 KiB


def fnv1a_32(data: bytes) -> int:
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def push_commands(path: str, data: bytes) -> list[str]:
    """Shell lines that recreate ``data`` at ``path`` with the ash printf builtin."""
    if not path.startswith("/") or any(c in path for c in " '\"$`\\\n"):
        raise ValueError(f"unsupported guest path {path!r}")
    chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]
    lines = []
    for i, c in enumerate(chunks):
        escaped = "".join("\\%03o" % b for b in c)     # octal escapes in the format: no literal %
        lines.append("printf '" + escaped + "' " + (">" if i == 0 else ">>") + " " + path)
    return lines


def parse_fnv(text: str) -> tuple[int, int] | None:
    m = re.search(r"FNV size=(\d+) fnv=([0-9a-f]{8})", text or "")
    return (int(m.group(1)), int(m.group(2), 16)) if m else None


def setting_files(guard: Path, request: Path, extra: list[tuple[Path, str]] = ()) -> str:
    """run.sh SETTING_FILES value (host paths must not contain spaces; names are FAT 8.3, or
    ``dir/name`` inside an existing /setting directory, e.g. ``mode/dmode`` (fat_add_file.py))."""
    items = [(guard, GUARD_NAME), (request, REQ_NAME), *extra]
    for p, name in items:
        if " " in str(p):
            raise ValueError(f"stage {p} from a path without spaces")
        if not (re.fullmatch(r"[A-Z0-9_]{1,8}\.[A-Z0-9]{1,3}", name)
                or re.fullmatch(r"[a-z0-9_]{1,8}\.[a-z0-9]{1,3}", name)      # lowercase 8.3 (env.txt)
                or re.fullmatch(r"[a-z0-9_]{1,8}/[a-z0-9_]{1,8}(\.[a-z0-9]{1,3})?", name)):
            raise ValueError(f"staged name {name!r} is not an 8.3 name")
    return " ".join(f"{p}:{name}" for p, name in items)


def raw_push_command(tmp: str, size: int, tag: int, append: bool = False) -> tuple[str, bytes, bytes]:
    """Guest command for a raw binary transfer over the serial console, plus the ready and done
    markers. The terminal goes raw (no echo, no line editing, no signals) before the ready marker
    is printed, so the host sends exactly ``size`` bytes after it; ``busybox dd bs=1`` writes them
    (the guest BusyBox 1.29.3 has no ``head`` applet and its ``dd`` no ``iflag=fullblock``).
    The markers are split with "" so the echoed command line never matches them."""
    if not tmp.startswith("/") or any(c in tmp for c in " '\"$`\\\n"):
        raise ValueError(f"unsupported guest path {tmp!r}")
    ready, done = f"@@R{tag}@@", f"@@D{tag}@@"
    cmd = (f"busybox stty raw -echo; echo {ready[:3]}\"\"{ready[3:]}; busybox dd bs=1 count={size} 2>/dev/null {">>" if append else ">"} {tmp}; "
           f"busybox stty sane; echo {done[:3]}\"\"{done[3:]}")
    return cmd, ready.encode(), done.encode()


class Console:
    """The guest shell on a QEMU unix-socket serial port. Output is mirrored to a log file."""

    def __init__(self, path: Path, log: Path, process=None):
        self.buf = bytearray()
        self.lock = threading.Condition()
        self.sock = socket.socket(socket.AF_UNIX)
        deadline = time.monotonic() + 20
        while True:
            try:
                self.sock.connect(str(path))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if (process and process.poll() is not None) or time.monotonic() > deadline:
                    raise RuntimeError("QEMU serial socket did not start")
                time.sleep(0.1)
        self.log = open(log, "ab", buffering=0)
        self.tag = 0
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        while True:
            try:
                data = self.sock.recv(65536)
            except OSError:
                data = b""
            if not data:
                with self.lock:
                    self.closed = True
                    self.lock.notify_all()
                return
            self.log.write(data)
            with self.lock:
                self.buf += data
                if len(self.buf) > 4 << 20:
                    del self.buf[:2 << 20]
                self.lock.notify_all()

    closed = False

    def wait_for(self, needle: bytes, timeout: float, start: int = 0) -> int:
        deadline = time.monotonic() + timeout
        with self.lock:
            while True:
                i = self.buf.find(needle, start)
                if i >= 0:
                    return i
                left = deadline - time.monotonic()
                if left <= 0 or self.closed:
                    return -1
                self.lock.wait(min(left, 0.5))

    def mark(self) -> int:
        with self.lock:
            return len(self.buf)

    def send(self, line: str):
        self.sock.sendall(line.encode() + b"\r")

    def run_raw(self, tmp: str, data: bytes, timeout: float = 30) -> bool:
        """Raw binary transfer of ``data`` into guest file ``tmp`` (see ``raw_push_command``), in
        chunks of RAW_CHUNK bytes. A raw terminal has no flow control and ``dd bs=1`` reads one byte
        per system call, so anything beyond the guest's 4 KiB terminal buffer is dropped (a 726 KB
        single send stalled); each chunk fits the buffer and is appended by its own ``dd``."""
        for off in range(0, len(data), RAW_CHUNK) or [0]:
            chunk = data[off:off + RAW_CHUNK]
            self.tag += 1
            cmd, ready, done = raw_push_command(tmp, len(chunk), self.tag, append=off > 0)
            start = self.mark()
            self.send(cmd)
            r = self.wait_for(ready, 30, start)
            if r < 0:
                return False
            self.sock.sendall(chunk)
            if self.wait_for(done, timeout, r) < 0:
                return False
        return True

    def run(self, command: str, timeout: float = 60) -> str | None:
        """Run one shell command and return its output (between unique markers)."""
        self.tag += 1
        begin, end = f"@@B{self.tag}@@", f"@@E{self.tag}@@"
        start = self.mark()
        self.send(f'echo {begin[:3]}""{begin[3:]}; {command}; echo {end[:3]}""{end[3:]}')
        b = self.wait_for(begin.encode(), timeout, start)
        if b < 0:
            return None
        e = self.wait_for(end.encode(), timeout, b)
        if e < 0:
            return None
        with self.lock:
            return self.buf[b + len(begin):e].decode("latin1").replace("\r", "")


@dataclass
class ModDev:
    console: Console
    loader: Path                     # Mod Loader v2 build: core.bin, mlv2.req, mlv2_guard_arm
    mods: list[Path]
    nudge: callable = None           # called when the core has not acted yet (e.g. MENU twice)
    extra_files: list[tuple[str, Path, bool]] = field(default_factory=list)
    setup_cmds: list[str] = field(default_factory=list)
    staged: dict = field(default_factory=dict)    # guest path -> (8.3 name on /setting, host path)
    raw: bool = None                             # raw serial transfer available (probed once)
    status: dict = field(default_factory=lambda: {"state": "waiting for the guest shell", "events": []})
    seen: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)   # one shell command at a time
    actions: queue.Queue = field(default_factory=queue.Queue)       # toolbar requests
    last: dict = field(default_factory=dict)                        # mod name -> last result
    bare: bool = False   # boots without Sony's UI (e.g. dmode=2): shell + files only, no loader

    def __post_init__(self):
        self.mods = list(self.mods)
        self.publish()

    def publish(self):
        self.status["mods"] = [{"name": m.name, "path": str(m), "last": self.last.get(m.name, "")}
                               for m in self.mods]

    def request(self, action: str, arg=None):
        """Queue a toolbar action: add (Path), reload (name), remove (name), core, rescan."""
        if action not in ("add", "reload", "remove", "core", "rescan"):
            raise ValueError(f"unknown action {action}")
        if action == "add" and not MOD_NAME.match(Path(arg).name):
            raise ValueError("a mod file name is NAME.mod (letters, digits, _ . -)")
        if action in ("reload", "remove") and arg not in {m.name for m in self.mods}:
            raise ValueError(f"no mod named {arg}")
        self.actions.put((action, arg))
        self.status["queued"] = self.actions.qsize()

    def find(self, name: str) -> Path:
        return next(m for m in self.mods if m.name == name)

    def run_action(self, action: str, arg):
        if action == "add":
            path = Path(arg)
            old = next((m for m in self.mods if m.name == path.name), None)
            if old is not None:
                self.mods[self.mods.index(old)] = path
                self.seen[path] = path.stat().st_mtime_ns
                self.last[path.name] = self.reload(path) or "pending"
            else:
                self.mods.append(path)
                self.push(f"/tmp_bt/mods/{path.name}", path.read_bytes())
                self.seen[path] = path.stat().st_mtime_ns
                self.last[path.name] = "loaded: listed at the next menu open"
                self.event(f"{path.name} added; it is listed when the menu root is set next")
                if self.nudge:
                    self.nudge()
        elif action == "reload":
            m = self.find(arg)
            self.seen[m] = m.stat().st_mtime_ns
            self.last[m.name] = self.reload(m) or "pending"
        elif action == "remove":
            m = self.find(arg)
            self.mods.remove(m)
            self.last.pop(m.name, None)
            self.sh(f"rm -f /tmp_bt/mods/{m.name}")
            self.event(f"{m.name}: no longer watched; guest file deleted (an ON copy stays until switched OFF)")
        elif action == "core":
            self.seen.pop("core", None)
        elif action == "rescan" and self.nudge:
            self.nudge()
            self.event("MENU pressed: the menu root is set again when the menu opens")
        self.publish()

    def event(self, text: str):
        stamp = time.strftime("%H:%M:%S")
        self.status["events"] = (self.status["events"] + [f"{stamp} {text}"])[-30:]
        print(f"[moddev] {text}", flush=True)

    def sh(self, command: str, timeout: float = 60) -> str:
        with self.lock:
            out = self.console.run(command, timeout)
        if out is None:
            raise RuntimeError(f"guest shell did not answer: {command[:60]}")
        return out

    def guest_fnv(self, path: str) -> tuple[int, int] | None:
        return parse_fnv(self.sh(f"{GUEST_GUARD} fnv {path}"))

    def raw_ok(self) -> bool:
        """The raw transfer needs busybox stty and dd; probed once, before any raw byte is sent
        (raw bytes reaching the shell instead of dd would be run as commands)."""
        if self.raw is None:
            out = self.sh("busybox dd bs=1 count=0 </dev/null 2>/dev/null && busybox stty -g >/dev/null && echo RAW\"\"OK")
            self.raw = "RAWOK" in out
            self.event(f"raw serial transfer {'available' if self.raw else 'unavailable: printf fallback'}")
        return self.raw

    def push(self, guest_path: str, data: bytes):
        part = guest_path.rsplit("/", 1)
        tmp = f"{part[0]}/.{part[1]}.part"
        t0 = time.monotonic()
        how = "raw"
        sent = False
        if self.raw_ok():
            with self.lock:
                sent = self.console.run_raw(tmp, data)
            if not sent:
                self.sh("busybox stty sane")
        if not sent:
            how = "printf"
            for line in push_commands(tmp, data):
                self.sh(line, 30)
        got = self.guest_fnv(tmp)
        if got != (len(data), fnv1a_32(data)):
            self.sh(f"rm -f {tmp}")
            raise RuntimeError(f"transfer check failed for {guest_path}: {got}")
        self.sh(f"mv -f {tmp} {guest_path}")
        self.event(f"pushed {guest_path} ({len(data)} B, {time.monotonic() - t0:.1f}s, {how}, fnv ok)")

    def wait_menu(self, timeout: float = 240):
        self.status["state"] = "booting"
        if self.console.wait_for(b"built-in shell (ash)", timeout) < 0:
            raise RuntimeError("no guest shell")
        self.console.wait_for(b"# ", 30, self.console.buf.find(b"built-in shell (ash)"))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            out = self.console.run("grep -c SetContentTarget /proc/snsc_boot_time", 60) or ""
            if re.search(r"^[1-9]\d*$", out.strip().split("\n")[-1] if out.strip() else "", re.M):
                self.event("main firmware reached SetContentTarget")
                return
            time.sleep(2)
        raise RuntimeError("menu checkpoint not reached")

    def place(self, guest_path: str, host_path: Path):
        """Copy a startup file from its pre-boot /setting staging (no serial transfer), checked by
        size and FNV; files that were not staged are pushed."""
        data = host_path.read_bytes()
        name = self.staged.get(guest_path, (None,))[0]
        if name is None:
            self.push(guest_path, data)
            return
        t0 = time.monotonic()
        if len(data) > 64 * 1024:
            # Large files stay on the overlay's /setting (vfat, executable with fmask=0022) behind a
            # symlink: the guest /tmp_bt tmpfs (1 MiB) already holds the guard and Sony's files.
            self.sh(f"ln -sf /setting/{name} {guest_path}")
        else:
            self.sh(f"cp /setting/{name} {guest_path}")
        if self.guest_fnv(guest_path) != (len(data), fnv1a_32(data)):
            raise RuntimeError(f"staged {guest_path} (/setting/{name}) does not match {host_path}")
        self.event(f"placed {guest_path} ({len(data)} B, {time.monotonic() - t0:.1f}s, staged, fnv ok)")

    def install(self):
        self.status["state"] = "installing"
        self.sh("grep -q ' /tmp_bt ' /proc/mounts || mount -t tmpfs tmpfs /tmp_bt; mkdir -p /tmp_bt/mlv2 /tmp_bt/mods")
        self.sh(f"cp /setting/{GUARD_NAME} {GUEST_GUARD} && chmod 700 {GUEST_GUARD} && cp /setting/{REQ_NAME} {GUEST_REQ}")
        for host, guest in ((self.loader / "mlv2_guard_arm", GUEST_GUARD), (self.loader / "mlv2.req", GUEST_REQ)):
            data = host.read_bytes()
            if self.guest_fnv(guest) != (len(data), fnv1a_32(data)):
                raise RuntimeError(f"staged {guest} does not match {host}")
        self.place("/tmp_bt/mlv2/core.bin", self.loader / "core.bin")
        self.seen["core"] = (self.loader / "core.bin").stat().st_mtime_ns
        for m in self.mods:
            self.place(f"/tmp_bt/mods/{m.name}", m)
            self.seen[m] = m.stat().st_mtime_ns
        for guest_path, host_path, executable in self.extra_files:
            self.place(guest_path, host_path)
            if executable:
                self.sh(f"chmod 755 {guest_path}")
        for cmd in self.setup_cmds:
            self.sh(cmd)
        check = self.sh(f"{GUEST_GUARD} check {GUEST_REQ}; echo GUARD_EXIT=$?")
        self.event(" ".join(l for l in check.splitlines() if l.startswith(("TARGET", "STATE", "MLV2_RESULT"))))
        if "hooks=stock" not in check:
            raise RuntimeError("view hooks are not stock; not installing")
        out = self.sh(f"{GUEST_GUARD} install {GUEST_REQ}; echo GUARD_EXIT=$?", 120)
        result = next((l for l in out.splitlines() if l.startswith("MLV2_RESULT")), "")
        self.event(f"guard install: {result}")
        if "MLV2_RESULT=0" not in result:
            raise RuntimeError(f"guard install failed: {out}")
        self.status["state"] = "installed: open MENU to load the core"

    def loader_log(self) -> str:
        return self.sh("cat /tmp_bt/mods/mod_loader.log 2>/dev/null")

    def report(self) -> dict:
        """Loader log, last reload result and pending request, read from the guest."""
        if self.status["state"].startswith(("waiting", "booting")):
            return {"state": self.status["state"]}
        out = self.sh(f"cat /tmp_bt/mods/mod_loader.log 2>/dev/null; echo @@RESULT; cat {RELOAD_RESULT} 2>/dev/null;"
                      f" test -f {RELOAD_REQ} && echo PENDING")
        log, _, rest = out.partition("@@RESULT")
        # Other mods' own logs in /tmp_bt/mods (for example keytap.log), last 400 lines each.
        mod_logs = {}
        names = self.sh("cd /tmp_bt/mods 2>/dev/null && for f in *.log; do [ \"$f\" = mod_loader.log ] || echo \"$f\"; done")
        for name in [n.strip() for n in names.splitlines() if n.strip().endswith(".log") and "*" not in n]:
            mod_logs[name] = self.sh(f"cat /tmp_bt/mods/{name} 2>/dev/null").strip().splitlines()[-400:]
        return {"state": self.status["state"], "loader_log": log.strip().splitlines(),
                "reload_result": rest.replace("PENDING", "").strip(), "reload_pending": "PENDING" in rest,
                "mod_logs": mod_logs}

    def reload(self, mod: Path, timeout: float = 30):
        """Push and request a reload. The core acts at the next menu refresh or root setup; without
        one, the nudge presses MENU (opening sets the root) and once more if that closed the menu."""
        self.push(f"/tmp_bt/mods/{mod.name}", mod.read_bytes())
        self.sh(f"rm -f {RELOAD_RESULT}; echo {mod.name} > {RELOAD_REQ}.part && mv -f {RELOAD_REQ}.part {RELOAD_REQ}")
        start, nudges = time.monotonic(), 0
        while time.monotonic() < start + timeout:
            out = self.sh(f"cat {RELOAD_RESULT} 2>/dev/null").strip()
            if out.startswith("reload_"):
                self.event(f"{mod.name}: {out}")
                return out
            if self.nudge and nudges < 2 and time.monotonic() > start + 2 + 3 * nudges:
                self.nudge()
                nudges += 1
            time.sleep(0.5)
        self.event(f"{mod.name}: reload request still pending (open or move in the Sony menu)")
        return None

    def watch_once(self):
        core = self.loader / "core.bin"
        if core.exists() and core.stat().st_mtime_ns != self.seen.get("core"):
            time.sleep(0.3)
            self.seen["core"] = core.stat().st_mtime_ns
            self.push("/tmp_bt/mlv2/core.bin", core.read_bytes())
            self.event("core.bin updated; the shim loads it at the next menu root")
            if self.nudge:
                self.nudge()
        for m in self.mods:
            if m.exists() and m.stat().st_mtime_ns != self.seen.get(m):
                time.sleep(0.3)                       # let the build finish writing
                self.seen[m] = m.stat().st_mtime_ns
                self.last[m.name] = self.reload(m) or "pending"

    def bare_setup(self):
        """Shell and helper files only: no SetContentTarget checkpoint and no Mod Loader install."""
        self.status["state"] = "booting"
        if self.console.wait_for(b"built-in shell (ash)", 240) < 0:
            raise RuntimeError("no guest shell")
        self.console.wait_for(b"# ", 30, self.console.buf.find(b"built-in shell (ash)"))
        # im.elf mounts /setting (its mount list); the staged files are there only after that.
        deadline = time.monotonic() + 120
        while " /setting " not in self.sh("grep ' /setting ' /proc/mounts", 30):
            if time.monotonic() > deadline:
                raise RuntimeError("/setting not mounted")
            time.sleep(2)
        self.sh("grep -q ' /tmp_bt ' /proc/mounts || mount -t tmpfs tmpfs /tmp_bt; mkdir -p /tmp_bt/mlv2 /tmp_bt/mods")
        # The guard binary provides the FNV check used by place(); it is copied, not run as a loader.
        self.sh(f"cp /setting/{GUARD_NAME} {GUEST_GUARD} && chmod 700 {GUEST_GUARD}")
        for guest_path, host_path, executable in self.extra_files:
            self.place(guest_path, host_path)
            if executable:
                self.sh(f"chmod 755 {guest_path}")
        for cmd in self.setup_cmds:
            self.sh(cmd)
        self.status["state"] = "installed: bare (no Sony UI, no mod loader)"

    def run(self, poll: float = 0.5):
        try:
            if self.bare:
                self.bare_setup()
                while not self.console.closed:
                    time.sleep(poll)
                return
            self.wait_menu()
            self.install()
            if self.nudge:
                # MENU opens the menu: the shim loads the core at this root setup. The menu instance
                # opened in that same step can still show six tabs, so close and reopen it once.
                self.nudge()
                time.sleep(4)
                self.nudge()
                time.sleep(2)
                self.nudge()
                time.sleep(3)
            log = self.loader_log()
            self.event("loader log: " + " | ".join(log.split("\n")[-5:]))
            if "core_load" not in log:
                self.event("core not loaded yet: press MENU in the panel to open the Sony menu")
            else:
                self.event("menu opened: the Mods tab is the last tab (left of the first one)")
            self.status["state"] = "watching for rebuilt mods"
            while not self.console.closed:
                try:
                    action, arg = self.actions.get(timeout=poll)
                except queue.Empty:
                    action = None
                if action:
                    self.status["queued"] = self.actions.qsize()
                    try:
                        self.run_action(action, arg)
                    except (OSError, RuntimeError, ValueError, StopIteration) as error:
                        self.event(f"{action} failed: {error}")
                self.watch_once()
                self.publish()
        except Exception as error:      # reported in the panel; QEMU keeps running
            self.status["state"] = f"stopped: {error}"
            self.event(f"stopped: {error}")


def host_path(text: str) -> Path:
    """A Windows path typed in the browser (C:\\...) as seen from WSL."""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", text.strip().strip('"'))
    if m:
        return Path("/mnt") / m.group(1).lower() / m.group(2).replace("\\", "/")
    return Path(text.strip())


def ensure_linux_inputs(inputs: Path) -> Path:
    """Prepared inputs on /mnt/* are read through WSL's 9P bridge on every guest disk access
    (boot took 63 s instead of 35 s). Copy them once to the Linux filesystem and reuse the copy."""
    if not str(inputs.resolve()).startswith("/mnt/"):
        return inputs
    dest = Path.home() / "a6400-inputs" / inputs.name
    manifest = inputs / "boot-manifest.json"
    if not (dest / "boot-manifest.json").exists() or (dest / "boot-manifest.json").read_bytes() != manifest.read_bytes():
        dest.mkdir(parents=True, exist_ok=True)
        for f in inputs.iterdir():
            if f.is_file():
                tmp = dest / (f.name + ".part")
                with open(f, "rb") as src, open(tmp, "wb") as dst:
                    while chunk := src.read(16 << 20):
                        dst.write(chunk)
                os.replace(tmp, dest / f.name)
    return dest
