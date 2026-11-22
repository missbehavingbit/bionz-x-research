#!/usr/bin/env python3
"""Serve the live QEMU LCD and its physical controls on WSL loopback.

With --loader the panel also runs the mod development loop (moddev.py): it boots the prepared
camera-captured inputs (copied to the Linux filesystem for speed), installs the guarded Mod
Loader v2 build in the guest and hot-reloads every --mod file and the loader core when they are
rebuilt. The page itself is re-read on every load, so panel edits show on a browser refresh.

The page toolbar (POST, same-origin with X-Camera-Panel: 1):
  /mods/upload?name=NAME.mod   body = SMOD file (saved under ~/a6400-moddev/mods, then loaded)
  /mods/watch                  body = {"path": host or Windows path of a build output to watch}
  /mods/reload/NAME, /mods/remove/NAME, /loader/core, /loader/rescan
  /emu/pause, /emu/resume, /emu/restart (reboots QEMU; loaded mods are kept)
GET /emu reports the run state.
"""

import argparse
import gzip
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import socket
from struct import pack
import subprocess
import shutil
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit
import zlib

import moddev


def start_uvc_host(usbhost_sock: Path, state: dict, stop_event: threading.Event, plug_wait=None):
    """Connect to the DWC3 usbhost unix socket and service UVC enumeration + Bulk IN frames."""
    import socket
    import sys
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from tools.research.qemu_ep0_host import Ep0Host
    from tools.research.qemu_uvc_smoke import enumerate_uvc

    state.update({"connected": False, "enumerated": False, "streaming": False,
                  "frames": 0, "valid_frames": 0, "vid_pid": "", "last_error": "", "jpeg": b""})
    if plug_wait is not None and not plug_wait():
        return
    deadline = time.monotonic() + 120.0
    while not stop_event.is_set() and not usbhost_sock.exists():
        if time.monotonic() > deadline:
            state["last_error"] = "usbhost socket timeout"
            return
        time.sleep(0.1)
    if stop_event.is_set():
        return
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(str(usbhost_sock))
        sock.settimeout(0.2)
    except OSError as exc:
        state["last_error"] = str(exc)
        return
    try:
        conn_sent = False
        while not stop_event.is_set():
            state["connected"] = False
            state["enumerated"] = False
            state["streaming"] = False
            try:
                if not conn_sent:
                    sock.sendall(b"CONN\n")
                    conn_sent = True
                    time.sleep(1.0)
                client = Ep0Host(sock)
                dev = client.control("8006000100001200")
                vid_pid = f"{int.from_bytes(dev[8:10], 'little'):04x}:{int.from_bytes(dev[10:12], 'little'):04x}" if len(dev) == 18 else ""
                state["vid_pid"] = vid_pid
                if vid_pid != "054c:0322":
                    time.sleep(1.0)
                    continue
                info = enumerate_uvc(client)
                state["connected"] = True
                state["vid_pid"] = info.get("vid_pid", "")
                if state["vid_pid"] == "054c:0322" and info.get("configuration_len", 0) >= 200:
                    state["enumerated"] = True
                    probe = client.control("a181000101001a00")
                    client.control("2101000101001a00", data_out=probe)
                    client.control("2101000201001a00", data_out=probe)
                    state["streaming"] = True
                    while not stop_event.is_set():
                        try:
                            pkt = client.bulk_in(3)
                        except (TimeoutError, RuntimeError):
                            break
                        if not pkt:
                            break
                        hdr_len = pkt[0]
                        payload = pkt[hdr_len:] if hdr_len >= 2 and len(pkt) > hdr_len else b""
                        valid_jpeg = len(payload) > 4 and payload[:2] == b"\xff\xd8" and payload[-2:] == b"\xff\xd9"
                        state["frames"] += 1
                        if valid_jpeg:
                            state["valid_frames"] += 1
                            state["jpeg"] = payload
                    state["streaming"] = False
                else:
                    time.sleep(1.0)
            except (TimeoutError, RuntimeError, ValueError, OSError, EOFError) as exc:
                state["last_error"] = str(exc)
                state["streaming"] = False
                state["connected"] = False
                if not state["vid_pid"]:
                    conn_sent = False
                time.sleep(1.0)
    finally:
        sock.close()


HOSTWIN_HELPER = "/tmp_bt/hostwin_get"

KEYS = {
    "flash": "l",
    "menu": "m",
    "af-button": "a",
    "af-lever": "b",
    "movie": "r",
    "fn": "f",
    "disp": "up",
    "drive": "left",
    "center": "ret",
    "iso": "right",
    "exposure": "down",
    "wheel-left": "bracket_left",
    "wheel-right": "bracket_right",
    "play": "p",
    "delete": "delete",
    "eye-sensor": "e",
    "shutter-half": "s",
    "shutter-full": "spc",
    "power-lever": "power",
}


def press_control(qmp, name):
    key = {"type": "qcode", "data": KEYS[name]}
    if name in ("wheel-left", "wheel-right", "af-lever", "eye-sensor", "power-lever"):
        # These latch on key-down; don't queue them behind virtual-time key holds.
        qmp.command("input-send-event", events=[
            {"type": "key", "data": {"key": key, "down": down}}
            for down in (True, False)
        ])
    else:
        qmp.command("send-key", keys=[key], **{"hold-time": 180})


class Qmp:
    def __init__(self, path, process):
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.settimeout(5)
        deadline = time.monotonic() + 15
        while True:
            try:
                self.socket.connect(str(path))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if process.poll() is not None or time.monotonic() >= deadline:
                    self.socket.close()
                    raise RuntimeError("QEMU monitor did not start. See console.log.")
                time.sleep(0.1)
        self.stream = self.socket.makefile("rwb", buffering=0)
        self.lock = threading.Lock()
        self.read()
        self.command("qmp_capabilities")

    def read(self):
        line = self.stream.readline()
        if not line:
            raise RuntimeError("QEMU monitor disconnected")
        return json.loads(line)

    def command(self, name, **arguments):
        request = {"execute": name, "arguments": arguments}
        with self.lock:
            self.socket.sendall(json.dumps(request).encode() + b"\n")
            while True:
                reply = self.read()
                if "error" in reply:
                    raise RuntimeError(reply["error"]["desc"])
                if "return" in reply:
                    return reply["return"]

    def close(self):
        self.stream.close()
        self.socket.close()


def png_from_ppm(image):
    magic, size, depth, pixels = image.split(b"\n", 3)
    width, height = map(int, size.split())
    stride = width * 3
    if magic != b"P6" or depth != b"255" or len(pixels) != stride * height:
        raise ValueError("Unexpected QEMU screendump format")

    def chunk(kind, data):
        return pack(">I", len(data)) + kind + data + pack(">I", zlib.crc32(kind + data))

    scanlines = b"".join(b"\0" + pixels[i:i + stride]
                         for i in range(0, len(pixels), stride))
    header = pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
           + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b""))
    return png, pixels.count(0) == len(pixels)


UPLOADS = Path.home() / "a6400-moddev" / "mods"
MAX_MOD = 1 << 20


def check_smod(data):
    if len(data) < 68 or data[:4] != b"SMOD" or data[4:8] not in (b"\1\0\0\0", b"\2\0\0\0"):
        raise ValueError("not an SMOD v1/v2 file")


def serve(args, scratch, server):
    """Run one QEMU instance. Returns True when the toolbar asked for a restart."""
    directory = Path(__file__).resolve().parent
    monitor = scratch / "qmp"
    frame = scratch / "lcd.ppm"
    origin = f"http://127.0.0.1:{server.server_port}"
    timing = os.environ.get("A6400_TIMING", "-icount shift=0,sleep=on")
    env = dict(os.environ, RUN_DIR=str(scratch / "run"), TIMEOUT="0",
               REAR_CONTROLS="1",
               REAR_LCD_DIAGNOSTIC=str(int(bool(getattr(args, "diagnostic_lcd", False)))),
               QEMU_EXTRA=f"{timing} -qmp unix:{monitor},server=on,wait=off")
    if args.inputs:
        env["A6400_QEMU_DATA_DIR"] = str(moddev.ensure_linux_inputs(args.inputs))
        env["ULOGIO"] = ""                    # the captured /setting already has its ulogio.bin
    staged = {}
    if args.loader:
        for name in ("mlv2_guard_arm", "mlv2.req"):
            shutil.copy(args.loader / name, scratch / name)
        # Startup files go onto the run's /setting overlay before boot instead of over the serial
        # line (about 1.3 s per KiB): guest path -> (8.3 name, host path), copied in the guest.
        stage_dir = scratch / "stage"
        stage_dir.mkdir(exist_ok=True)
        wanted = [("/tmp_bt/mlv2/core.bin", args.loader / "core.bin")]
        if args.hostwin_helper:
            # Host window (PA 0x50000000, see run.sh HOSTWIN): a 16 MiB shared file per run, and
            # the guest helper that copies files out of it (POST /moddev/put uses both).
            hostwin = scratch / "hostwin.bin"
            with open(hostwin, "wb") as f:
                f.truncate(16 << 20)
            env["HOSTWIN"] = str(hostwin)
            wanted.append((HOSTWIN_HELPER, args.hostwin_helper))
        wanted += [(f"/tmp_bt/mods/{m.name}", m) for m in args.mod]
        wanted += [(spec.partition("=")[0], Path(spec.partition("=")[2])) for spec in args.guest_file]
        for i, (guest, host) in enumerate(wanted):
            name = f"STG{i:02d}.BIN"
            shutil.copy(host, stage_dir / name)
            staged[guest] = (name, host)
        extra_setting = []
        for k, spec in enumerate(args.setting_file):
            dst, _, src = spec.partition("=")
            copy = stage_dir / f"SET{k:02d}"             # staged copy: run.sh needs paths without spaces
            shutil.copy(src, copy)
            extra_setting.append((copy, dst))
        env["SETTING_FILES"] = moddev.setting_files(scratch / "mlv2_guard_arm", scratch / "mlv2.req",
                                                    [(stage_dir / n, n) for n, _ in staged.values()]
                                                    + extra_setting)
        env["SERIAL"] = f"unix:{scratch / 'serial'},server=on,wait=off"
    uvc_state = {}
    uvc_stop = threading.Event()
    if args.uvc_test:
        usbhost_sock = scratch / "usbhost.sock"
        env["DWC3_GADGET"] = "1"
        env["PL330_DMA"] = "1"
        env["USB_VBUS_TEST"] = "1"
        env["USB_CID_HIGH"] = "1"
        env["QEMU_EXTRA"] += (
            f" -chardev socket,id=usbhost,path={usbhost_sock},server=on,wait=off"
            " -global cxd90045-dwc3.chardev=usbhost"
            " -global cxd90045-dwc3.captured-hwparams=on"
            " -global cxd90045-dwc3.captured-start-gate=on"
            " -global cxd90045-dwc3.strict-ep0=on"
            " -global cxd90045-dwc3.strict-bulk=on"
        )
    with args.log.open("xb") as log:
        process = subprocess.Popen(["bash", str(directory.parent / "run.sh")],
                                   stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, env=env)
        qmp = None
        dev = None
        server.restart = False
        started = time.time()
        try:
            qmp = Qmp(monitor, process)
            if args.loader:
                console = moddev.Console(scratch / "serial", args.log.with_suffix(".serial.log"), process)

                def nudge():
                    press_control(qmp, "menu")

                extra_files = []
                setup_cmds = []
                for spec in args.guest_file:
                    dst, _, src = spec.partition("=")
                    extra_files.append((dst, Path(src), dst.endswith((".elf", "_gui")) or "/bin" in dst))
                setup_cmds.extend(args.guest_cmd)
                if args.hostwin_helper:
                    extra_files.append((HOSTWIN_HELPER, args.hostwin_helper, True))
                if args.uvc_test:
                    extra_files = [
                        ("/tmp_bt/usbg_video_a6400.ko", args.uvc_ko, False),
                        ("/tmp_bt/uvc_mod_daemon", args.uvc_daemon, True),
                    ]
                    setup_cmds = [
                        "insmod /tmp_bt/usbg_video_a6400.ko",
                        "mknod /tmp_bt/video c 248 33",
                        "(/tmp_bt/uvc_mod_daemon </dev/null >/tmp_bt/uvc_daemon.log 2>&1 &)",
                    ]
                dev = moddev.ModDev(console, args.loader, list(args.mod), nudge=None if args.bare else nudge,
                                    extra_files=extra_files, setup_cmds=setup_cmds, staged=staged,
                                    bare=args.bare)
                threading.Thread(target=dev.run, daemon=True).start()
            if args.uvc_test:
                plug_wait = None
                if args.uvc_plug_delay > 0 and dev is not None:
                    def plug_wait():
                        while not uvc_stop.is_set() and dev.status.get("state") != "watching for rebuilt mods":
                            time.sleep(0.5)
                        if uvc_stop.is_set():
                            return False
                        baseline = dev.loader_log().count("uvc_mod_on")
                        while not uvc_stop.is_set():
                            if dev.loader_log().count("uvc_mod_on") > baseline:
                                break
                            time.sleep(0.5)
                        if uvc_stop.is_set():
                            return False
                        time.sleep(args.uvc_plug_delay)
                        if uvc_stop.is_set():
                            return False
                        qmp.command("qom-set", path="/machine", property="usb-vbus", value=True)
                        return True
                else:
                    qmp.command("qom-set", path="/machine", property="usb-vbus", value=True)
                threading.Thread(target=start_uvc_host,
                                 args=(scratch / "usbhost.sock", uvc_state, uvc_stop, plug_wait),
                                 daemon=True).start()

            def get(handler):
                if handler.path == "/":
                    handler.reply("text/html; charset=utf-8", (directory / "rear_panel.html").read_bytes())
                elif handler.path == "/moddev":
                    if not dev:
                        handler.send_error(404)
                    else:
                        handler.reply("application/json", json.dumps(dev.status).encode())
                elif handler.path == "/emu":
                    handler.reply("application/json", json.dumps(emu_state()).encode())
                elif handler.path == "/uvc":
                    view = {k: v for k, v in uvc_state.items() if k != "jpeg"}
                    view["jpeg_bytes"] = len(uvc_state.get("jpeg", b""))
                    handler.reply("application/json", json.dumps(view).encode())
                elif handler.path == "/uvc/frame.jpg":
                    jpg = uvc_state.get("jpeg", b"")
                    if not jpg:
                        handler.send_error(404)
                    else:
                        handler.reply("image/jpeg", jpg)
                elif handler.path == "/moddev/log" and dev:
                    handler.reply("application/json", json.dumps(dev.report(), indent=1).encode())
                elif handler.path == "/lcd.png":
                    qmp.command("screendump", filename=str(frame), device="vph", head=0)
                    png, blank = png_from_ppm(frame.read_bytes())
                    handler.reply("image/png", png, lcd_state="blank" if blank else "visible")
                else:
                    handler.send_error(404)

            def emu_state():
                status = qmp.command("query-status")
                return {"running": status.get("running"), "state": status.get("status"),
                        "uptime_s": int(time.time() - started), "moddev": bool(dev),
                        "log": str(args.log)}

            def body(handler, limit):
                size = int(handler.headers.get("Content-Length") or 0)
                if size > limit:
                    raise ValueError("request body too large")
                return handler.rfile.read(size)

            def post(handler):
                url = urlsplit(handler.path)
                path = url.path
                if handler.headers.get("Origin") != origin or handler.headers.get("X-Camera-Panel") != "1":
                    handler.send_error(403)
                    return
                name = path.removeprefix("/press/")
                if path == f"/press/{name}" and name in KEYS:
                    press_control(qmp, name)
                    handler.reply("text/plain", b"")
                elif path in ("/emu/pause", "/emu/resume"):
                    qmp.command("stop" if path == "/emu/pause" else "cont")
                    handler.reply("application/json", json.dumps(emu_state()).encode())
                elif path == "/emu/restart":
                    server.restart = True
                    handler.reply("application/json", b'{"restarting": true}')
                elif not dev:
                    handler.send_error(404, "panel started without --loader")
                else:
                    try:
                        if path == "/mods/upload":
                            name = parse_qs(url.query).get("name", [""])[0]
                            if not moddev.MOD_NAME.match(name):
                                raise ValueError("a mod file name is NAME.mod (letters, digits, _ . -)")
                            data = body(handler, MAX_MOD)
                            check_smod(data)
                            UPLOADS.mkdir(parents=True, exist_ok=True)
                            tmp = UPLOADS / (name + ".part")
                            tmp.write_bytes(data)
                            os.replace(tmp, UPLOADS / name)
                            dev.request("add", UPLOADS / name)
                        elif path == "/moddev/put":
                            # Warm-guest file transfer: raw serial push into the running guest,
                            # FNV-checked. Large files belong on the overlay's /setting (vfat).
                            dst = parse_qs(url.query).get("path", [""])[0]
                            data = body(handler, 16 << 20)
                            t0 = time.monotonic()
                            sent = len(data)
                            hostwin = scratch / "hostwin.bin"
                            if args.hostwin_helper and hostwin.exists() and len(data) <= (16 << 20) - 4096:
                                # Host window: write header + data into the shared file, the guest
                                # helper copies it out and checks length and FNV-1a.
                                with open(hostwin, "r+b") as f:
                                    f.seek(4096)
                                    f.write(data)
                                    f.seek(0)
                                    f.write(pack("<4I", 0x4E495748, int(time.time()) & 0xFFFFFFFF,
                                                 len(data), moddev.fnv1a_32(data)))
                                    f.flush()
                                    os.fsync(f.fileno())
                                out = dev.sh(f"{HOSTWIN_HELPER} {dst}")
                                if "HWIN OK" not in out:
                                    raise RuntimeError(f"hostwin_get failed: {out.strip()[-200:]}")
                                sent = 0
                            elif len(data) > 16 * 1024 and "gunzip" in dev.sh("busybox --list | grep -x gunzip"):
                                gz = gzip.compress(data, 9)
                                sent = len(gz)
                                dev.push(dst + ".gz", gz)
                                dev.sh(f"busybox gunzip -c {dst}.gz > {dst}; rm -f {dst}.gz")
                                if dev.guest_fnv(dst) != (len(data), moddev.fnv1a_32(data)):
                                    raise RuntimeError(f"unpacked {dst} does not match")
                            else:
                                dev.push(dst, data)
                            handler.reply("application/json", json.dumps(
                                {"path": dst, "bytes": len(data), "sent": sent,
                                 "seconds": round(time.monotonic() - t0, 2)}).encode())
                            return
                        elif path == "/moddev/sh":
                            # Developer access to the disposable guest shell (same-origin + header
                            # checked above): the body is one command, the reply its output.
                            cmd = body(handler, 4096).decode("utf-8")
                            out = dev.sh(cmd, 60)
                            handler.reply("text/plain; charset=utf-8", out.encode())
                            return
                        elif path == "/mods/watch":
                            target = moddev.host_path(json.loads(body(handler, 4096) or b"{}").get("path", ""))
                            if not target.is_file():
                                raise ValueError(f"{target} is not a file visible from WSL")
                            check_smod(target.read_bytes()[:68])
                            dev.request("add", target)
                        elif path.startswith(("/mods/reload/", "/mods/remove/")):
                            dev.request(path.split("/")[2], path.split("/", 3)[3])
                        elif path == "/loader/core":
                            dev.request("core")
                        elif path == "/loader/rescan":
                            dev.request("rescan")
                        else:
                            handler.send_error(404)
                            return
                    except ValueError as error:
                        handler.reply("application/json", json.dumps({"error": str(error)}).encode(), code=400)
                        return
                    handler.reply("application/json", json.dumps(dev.status).encode())

            server.get = get
            server.post = post
            print(f"{origin}/\nQEMU log: {args.log}", flush=True)
            while process.poll() is None and not server.restart:
                server.handle_request()
            if dev:
                args.mod = list(dev.mods)       # a restart reloads the same mods
            return server.restart
        finally:
            uvc_stop.set()
            if qmp:
                qmp.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


class PanelRequest(BaseHTTPRequestHandler):
    def reply(self, content_type, body, *, lcd_state=None, code=200):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if lcd_state is not None:
            self.send_header("X-Camera-LCD", lcd_state)
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self, action):
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self.send_error(403)
            return
        try:
            action(self)
        except (OSError, RuntimeError, ValueError) as error:
            self.log_error("%s", error)
            self.send_error(503, "QEMU display or input unavailable")

    def do_GET(self):
        self.dispatch(self.server.get)

    def do_POST(self):
        self.dispatch(self.server.post)

    def log_request(self, code="-", size="-"):
        if str(code) != "200":
            super().log_request(code, size)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8640)
    parser.add_argument("--log", type=Path, required=True, help="New QEMU serial log path")
    parser.add_argument("--stock", action="store_true", help="Use the stock firmware display selection (default)")
    parser.add_argument("--diagnostic-lcd", action="store_true",
                        help="Apply the E0572 diagnostic display-controller sector edit on the disposable overlay")
    parser.add_argument("--inputs", type=Path, help="Prepared camera-captured inputs (qemu_boot_inputs prepare)")
    parser.add_argument("--loader", type=Path, help="Mod Loader v2 build directory to install and watch")
    parser.add_argument("--mod", type=Path, action="append", default=[], help="SMOD file to load and hot-reload")
    parser.add_argument("--guest-file", action="append", default=[], metavar="DST=SRC",
                        help="with --loader: push host file SRC to guest path DST before the install")
    parser.add_argument("--hostwin-helper", type=Path, metavar="HOSTWIN_GET",
                        help="with --loader: enable the host window (needs a QEMU with the hostwin "
                             "machine option) and stage this hostwin_get build as " "/tmp_bt/hostwin_get")
    parser.add_argument("--setting-file", action="append", default=[], metavar="DIR/NAME=SRC",
                        help="with --loader: add host file SRC to the run's /setting overlay before boot at "
                             "DIR/NAME inside an existing directory (e.g. mode/dmode=FILE, bootin's mode file)")
    parser.add_argument("--bare", action="store_true",
                        help="with --loader: the boot has no Sony UI (e.g. dmode=2); wait for the shell, "
                             "place the files, skip the SetContentTarget checkpoint and the loader install")
    parser.add_argument("--guest-cmd", action="append", default=[], metavar="CMD",
                        help="with --loader: guest shell command run after the files are pushed")
    parser.add_argument("--uvc-test", action="store_true",
                        help="Enable DWC3 gadget, PL330 DMA, UVC host listener, and stage USB Streaming Test")
    parser.add_argument("--uvc-plug-delay", type=float, default=0.0,
                        help="Wait for uvc_mod_on and delay N seconds before asserting usb-vbus and connecting host")
    repo_root = Path(__file__).resolve().parents[2]
    parser.add_argument("--uvc-ko", type=Path,
                        default=repo_root / "artifacts/native/qemu-uvc-coherent-15/usbg_video_a6400.ko")
    parser.add_argument("--uvc-daemon", type=Path,
                        default=repo_root / "artifacts/mods/usb-streaming-test-4/uvc_mod_daemon")
    parser.add_argument("--uvc-mod", type=Path,
                        default=repo_root / "artifacts/mods/usb-streaming-test-4/UsbStreamingTest.mod")
    args = parser.parse_args()
    if args.loader and not args.inputs:
        parser.error("--loader needs --inputs: mods target the camera's own library builds")
    if args.uvc_test:
        if not args.loader or not args.inputs:
            parser.error("--uvc-test needs --loader and --inputs")
        if args.uvc_mod.name not in {m.name for m in args.mod}:
            args.mod.append(args.uvc_mod)
    base_log = args.log
    with HTTPServer(("127.0.0.1", args.port), PanelRequest) as server:
        server.timeout = 1
        for run in range(1, 1000):
            args.log = base_log if run == 1 else base_log.with_name(f"{base_log.stem}-r{run}{base_log.suffix}")
            with tempfile.TemporaryDirectory(prefix="a6400-panel-") as directory:
                try:
                    if not serve(args, Path(directory), server):
                        break
                except KeyboardInterrupt:
                    break
            print("restarting QEMU", flush=True)


if __name__ == "__main__":
    main()
