#!/usr/bin/env python3
"""Mod development runtime for the Sony A7C QEMU guest (WSL side).

Runs Mod Loader v2 inside the emulated A7C and keeps it in sync with host build outputs:

  1. After the guest shell answers and the main firmware reaches SetContentTarget, a tmpfs is
     mounted at /tmp_bt, mlv2_guard_arm / mlv2.req / core.bin / mods are pushed over the serial
     console (using gzip + raw terminal transfer for speed, verified by FNV-1a), and the guard
     runs `check` then `install`.
  2. A watcher polls the host files. A changed `.mod` is pushed and handed to `/tmp_bt/mlv2/reload`;
     a changed `core.bin` is pushed and picked up by the shim at the next menu root.
  3. The panel toolbar queues actions (`request`): load or watch another mod file, force a reload,
     stop watching and delete a mod file in the guest, re-push the core, or press MENU.
"""
from __future__ import annotations

import gzip
import os
import queue
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

GUEST_GUARD, GUEST_REQ = "/tmp_bt/mlv2_guard", "/tmp_bt/mlv2.req"
RELOAD_REQ, RELOAD_RESULT = "/tmp_bt/mlv2/reload", "/tmp_bt/mlv2/reload.result"
MOD_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,40}\.mod$")
RAW_CHUNK = 3584        # bytes per raw transfer step: below the guest 4 KiB terminal buffer
CHUNK = 200             # bytes per printf line: 4 chars each


def fnv1a_32(data: bytes) -> int:
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def push_commands(path: str, data: bytes) -> list[str]:
    """Shell lines that recreate `data` at `path` with the ash printf builtin."""
    if not path.startswith("/") or any(c in path for c in " '\"$`\\\n"):
        raise ValueError(f"unsupported guest path {path!r}")
    chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]
    lines = []
    for i, c in enumerate(chunks):
        escaped = "".join("\\%03o" % b for b in c)
        lines.append("printf '" + escaped + "' " + (">" if i == 0 else ">>") + " " + path)
    return lines


def parse_fnv(text: str) -> tuple[int, int] | None:
    m = re.search(r"FNV size=(\d+) fnv=([0-9a-f]{8})", text or "")
    return (int(m.group(1)), int(m.group(2), 16)) if m else None


def raw_push_command(tmp: str, size: int, tag: int, append: bool = False) -> tuple[str, bytes, bytes]:
    if not tmp.startswith("/") or any(c in tmp for c in " '\"$`\\\n"):
        raise ValueError(f"unsupported guest path {tmp!r}")
    ready, done = f"@@R{tag}@@", f"@@D{tag}@@"
    cmd = (
        f"busybox stty raw -echo; echo {ready[:3]}\"\"{ready[3:]}; "
        f"busybox dd bs=1 count={size} 2>/dev/null {'>>' if append else '>'} {tmp}; "
        f"busybox stty sane; echo {done[:3]}\"\"{done[3:]}"
    )
    return cmd, ready.encode(), done.encode()


class Console:
    """The guest shell on a QEMU unix-socket serial port."""

    def __init__(self, path: Path, log: Path | None = None, process=None):
        self.buf = bytearray()
        self.lock = threading.Condition()
        self.sock = socket.socket(socket.AF_UNIX)
        deadline = time.monotonic() + 30
        while True:
            try:
                self.sock.connect(str(path))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if (process and process.poll() is not None) or time.monotonic() > deadline:
                    raise RuntimeError("QEMU serial socket did not start")
                time.sleep(0.2)
        self.log = open(log, "ab", buffering=0) if log else None
        self.tag = 0
        self.closed = False
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
            if self.log:
                self.log.write(data)
            with self.lock:
                self.buf += data
                if len(self.buf) > 4 << 20:
                    del self.buf[:2 << 20]
                self.lock.notify_all()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

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
    loader: Path
    mods: list[Path]
    nudge: callable = None
    raw: bool = None
    gunzip: bool = None
    hostwin: Path = None            # host side of the machine's hostwin window (or None)
    hostwin_helper: Path = None     # tools/hostwin_get (guest ARM binary)
    hostwin_ready: bool = False
    hostwin_seq: int = 0
    status: dict = field(default_factory=lambda: {"state": "waiting for the guest shell", "events": []})
    seen: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    actions: queue.Queue = field(default_factory=queue.Queue)
    last: dict = field(default_factory=dict)

    def __post_init__(self):
        self.mods = list(self.mods)
        self.publish()

    def publish(self):
        self.status["mods"] = [
            {"name": m.name, "path": str(m), "last": self.last.get(m.name, "")}
            for m in self.mods
        ]

    def request(self, action: str, arg=None):
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
        if self.raw is None:
            try:
                lst = self.sh("busybox --list 2>/dev/null", 15)
                if "dd" not in lst.splitlines() or "stty" not in lst.splitlines():
                    self.raw = False
                else:
                    out = self.sh(
                        "echo x | busybox dd bs=1 count=1 >/dev/null 2>&1 && "
                        "busybox stty -g >/dev/null 2>&1 && echo RAW\"\"OK",
                        15,
                    )
                    self.raw = "RAWOK" in out
            except RuntimeError:
                self.raw = False
            self.event(f"raw serial transfer {'available' if self.raw else 'unavailable: printf fallback'}")
        return self.raw

    def gunzip_ok(self) -> bool:
        if self.gunzip is None:
            out = self.sh("busybox --list 2>/dev/null | grep -x gunzip")
            self.gunzip = "gunzip" in out
        return self.gunzip

    def _raw_or_printf(self, dst: str, data: bytes) -> str:
        how = "raw"
        sent = False
        if self.raw_ok():
            with self.lock:
                sent = self.console.run_raw(dst, data)
            if not sent:
                self.sh("busybox stty sane")
        if not sent:
            how = "printf"
            for line in push_commands(dst, data):
                self.sh(line, 30)
        return how

    HOSTWIN_GET = "/tmp_bt/hostwin_get"

    def hostwin_ok(self) -> bool:
        """Push the hostwin_get helper once (small, over serial) and test the window."""
        if self.hostwin_ready or not self.hostwin or not self.hostwin_helper:
            return self.hostwin_ready
        try:
            helper = self.hostwin_helper.read_bytes()
            for line in push_commands(self.HOSTWIN_GET, helper):     # small: printf is robust
                self.sh(line, 30)
            self.sh(f"chmod 700 {self.HOSTWIN_GET}")
            probe = b"hostwin probe\n"
            self.hostwin_ready = self._hostwin_put("/tmp_bt/.hwprobe", probe)
            self.sh("rm -f /tmp_bt/.hwprobe")
        except (OSError, RuntimeError) as e:
            self.event(f"hostwin unavailable ({e}); using the serial console")
            self.hostwin = None
        self.event(f"hostwin window {'ready: fast transfers' if self.hostwin_ready else 'not answering: serial transfers'}")
        if not self.hostwin_ready:
            self.hostwin = None
        return self.hostwin_ready

    def _hostwin_put(self, dst: str, data: bytes) -> bool:
        import struct
        if len(data) > (16 << 20) - 0x1000:
            return False
        self.hostwin_seq += 1
        with open(self.hostwin, "r+b") as f:
            f.seek(0)
            f.write(struct.pack("<4I", 0, 0, 0, 0))     # invalid while the data changes
            f.seek(0x1000)
            f.write(data)
            f.flush()
            f.seek(0)
            f.write(struct.pack("<4I", 0x4E495748, self.hostwin_seq, len(data), fnv1a_32(data)))
            f.flush()
            os.fsync(f.fileno())
        out = self.sh(f"{self.HOSTWIN_GET} {dst}", 60)
        return f"HWIN OK len={len(data):08x} fnv={fnv1a_32(data):08x}" in out

    def push(self, guest_path: str, data: bytes, verify_guard: bool = True):
        part = guest_path.rsplit("/", 1)
        tmp = f"{part[0]}/.{part[1]}.part"
        t0 = time.monotonic()
        if self.hostwin_ok() and self._hostwin_put(tmp, data):
            how = "hostwin"
        elif len(data) > 16 * 1024 and self.gunzip_ok():
            gz = gzip.compress(data, 9)
            how = self._raw_or_printf(tmp + ".gz", gz) + "+gz"
            self.sh(f"busybox gunzip -c {tmp}.gz > {tmp}; rm -f {tmp}.gz", 120)
        else:
            how = self._raw_or_printf(tmp, data)
        if verify_guard:
            got = self.guest_fnv(tmp)
            if got != (len(data), fnv1a_32(data)):
                self.sh(f"rm -f {tmp}")
                raise RuntimeError(f"transfer check failed for {guest_path}: {got}")
        self.sh(f"mv -f {tmp} {guest_path}")
        if not verify_guard:
            self.sh(f"chmod 700 {guest_path}")
            got = self.guest_fnv(guest_path)
            if got != (len(data), fnv1a_32(data)):
                raise RuntimeError(f"self-check failed for {guest_path}: {got}")
        self.event(f"pushed {guest_path} ({len(data)} B, {time.monotonic() - t0:.1f}s, {how}, fnv ok)")

    def wait_menu(self, timeout: float = 600):
        self.status["state"] = "booting"
        if self.console.wait_for(b"built-in shell (ash)", timeout) < 0:
            raise RuntimeError("no guest shell")
        self.console.wait_for(b"# ", 30, self.console.buf.find(b"built-in shell (ash)"))
        self.console.run("dmesg -n 1 2>/dev/null", 15)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            out = self.console.run("grep -c SetContentTarget /proc/snsc_boot_time 2>/dev/null", 60) or ""
            if re.search(r"^[1-9]\d*$", out.strip().split("\n")[-1] if out.strip() else "", re.M):
                self.event("main firmware reached SetContentTarget")
                return
            time.sleep(2)
        raise RuntimeError("menu checkpoint not reached")

    def install(self):
        self.status["state"] = "installing"
        # Pushing files (and the guard) while live view starts (guest ~65-80 s) stalls the
        # live-view pipeline and the OSD flips for good: black LCD, MENU draws nothing
        # (seen 2026-11-27; a boot without pushes is fine).  Wait until live view has settled.
        settle = float(os.environ.get("A7C_MOD_INSTALL_AFTER", "120"))
        while True:
            import re
            m = re.search(r"(\d+\.\d+) \d+\.\d+", self.sh("cat /proc/uptime"))
            t = float(m.group(1)) if m else 0.0
            if t >= settle:
                break
            self.status["state"] = f"installing: waiting for live view to settle ({t:.0f}/{settle:.0f} s)"
            time.sleep(15)
        self.sh("grep -q ' /tmp_bt ' /proc/mounts || mount -t tmpfs tmpfs /tmp_bt; mkdir -p /tmp_bt/mlv2 /tmp_bt/mods")
        self.push(GUEST_GUARD, (self.loader / "mlv2_guard_arm").read_bytes(), verify_guard=False)
        self.push(GUEST_REQ, (self.loader / "mlv2.req").read_bytes())
        self.push("/tmp_bt/mlv2/core.bin", (self.loader / "core.bin").read_bytes())
        self.seen["core"] = (self.loader / "core.bin").stat().st_mtime_ns
        for m in self.mods:
            self.push(f"/tmp_bt/mods/{m.name}", m.read_bytes())
            self.seen[m] = m.stat().st_mtime_ns
        check = self.sh(f"{GUEST_GUARD} check {GUEST_REQ}; echo GUARD_EXIT=$?")
        self.event(" ".join(l for l in check.splitlines() if l.startswith(("TARGET", "STATE", "MLV2_RESULT"))))
        if "hooks=stock" not in check:
            raise RuntimeError(f"view hooks are not stock; not installing ({check.strip()})")
        out = self.sh(f"{GUEST_GUARD} install {GUEST_REQ}; echo GUARD_EXIT=$?", 120)
        result = next((l for l in out.splitlines() if l.startswith("MLV2_RESULT")), "")
        self.event(f"guard install: {result}")
        if "MLV2_RESULT=0" not in result:
            raise RuntimeError(f"guard install failed: {out}")
        self.status["state"] = "installed: open MENU to load the core"

    def loader_log(self) -> str:
        return self.sh("cat /tmp_bt/mods/mod_loader.log 2>/dev/null")

    def report(self) -> dict:
        if self.status["state"].startswith(("waiting", "booting", "stopped")):
            return {"state": self.status["state"]}
        out = self.sh(
            f"cat /tmp_bt/mods/mod_loader.log 2>/dev/null; echo @@RESULT; cat {RELOAD_RESULT} 2>/dev/null;"
            f" test -f {RELOAD_REQ} && echo PENDING"
        )
        log, _, rest = out.partition("@@RESULT")
        mod_logs = {}
        names = self.sh("cd /tmp_bt/mods 2>/dev/null && for f in *.log; do [ \"$f\" = mod_loader.log ] || echo \"$f\"; done")
        for name in [n.strip() for n in names.splitlines() if n.strip().endswith(".log") and "*" not in n]:
            mod_logs[name] = self.sh(f"cat /tmp_bt/mods/{name} 2>/dev/null").strip().splitlines()[-400:]
        return {
            "state": self.status["state"],
            "loader_log": log.strip().splitlines(),
            "reload_result": rest.replace("PENDING", "").strip(),
            "reload_pending": "PENDING" in rest,
            "mod_logs": mod_logs,
        }

    def reload(self, mod: Path, timeout: float = 30):
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
                time.sleep(0.3)
                self.seen[m] = m.stat().st_mtime_ns
                self.last[m.name] = self.reload(m) or "pending"

    def run(self, poll: float = 0.5):
        try:
            self.wait_menu()
            self.install()
            if self.nudge:
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
        except Exception as error:
            self.status["state"] = f"stopped: {error}"
            self.event(f"stopped: {error}")


def host_path(text: str) -> Path:
    """A Windows path typed in the browser (C:\\...) as seen from WSL."""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", text.strip().strip('"'))
    if m:
        return Path("/mnt") / m.group(1).lower() / m.group(2).replace("\\", "/")
    return Path(text.strip())
