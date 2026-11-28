#!/usr/bin/env python3
"""A7C web panel: the camera screen and its controls in a browser.

    python3 tools/a7c_panel.py [--port 8642] [--card out/sdcard.img] [--min-hold 1500]
    then open http://localhost:8642   (start_a7c_panel.bat does all of this from Windows)

Starts ./run.sh in the full mode (A7C_FULL=1, no QEMU window) with an extra QMP socket,
shows QEMU's console 0 (the VPH screen) and sends the camera keys as real press/release
events. The emulator holds every press for at least A7C_KEY_MS (150) ms of the camera's
own time (v1.19), so a click is a short press and holding the mouse button is a long press.
A fixed hold in host time was unreliable: the guest's time runs unevenly under icount, so
it was missed at times and auto-repeated in the menus at others. Only listens on 127.0.0.1 (WSL forwards it to Windows' localhost).

GET  /                 the page (tools/a7c_panel.html, re-read on every load)
GET  /lcd.png          current screen (PNG, header X-A7C-Lcd: blank|visible)
GET  /status           JSON: emulator state, uptime, last console line
POST /key/down/NAME, /key/up/NAME, /key/tap/NAME    NAME = QEMU qcode (up, ret, m, ...)
POST /emu/pause, /emu/resume, /emu/restart, /emu/screenshot
Every POST needs the header X-A7C-Panel: 1 (blocks other web pages from sending keys).
"""

import argparse
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import moddev  # noqa: E402

SMOD_MAGIC = 0x444F4D53
MAX_MOD = 4 << 20

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "out"

# Keys the emulator's rear-control model understands (sony_a7c.c a7c_rear_key).
# Held keys are levels the camera samples; tapped keys act on the press.
HELD = {"ret", "up", "down", "left", "right", "f", "delete", "m", "p", "r",
        "h", "spc", "a", "c"}
TAPPED = {"bracket_left", "bracket_right", "equal", "minus",
          "1", "2", "3", "4", "5", "6", "7", "8", "9", "0"}


class Qmp:
    def __init__(self, path, deadline=30):
        end = time.monotonic() + deadline
        while True:
            try:
                self.sock = socket.socket(socket.AF_UNIX)
                self.sock.settimeout(20)
                self.sock.connect(str(path))
                break
            except OSError:
                self.sock.close()
                if time.monotonic() > end:
                    raise RuntimeError("QEMU's QMP socket did not appear")
                time.sleep(0.3)
        self.stream = self.sock.makefile("rb")
        self.lock = threading.Lock()
        self._read()                        # greeting
        self.cmd("qmp_capabilities")

    def _read(self):
        line = self.stream.readline()
        if not line:
            raise RuntimeError("QEMU disconnected")
        return json.loads(line)

    def cmd(self, name, **args):
        with self.lock:
            msg = {"execute": name}
            if args:
                msg["arguments"] = args
            self.sock.sendall(json.dumps(msg).encode() + b"\n")
            while True:
                r = self._read()
                if "error" in r:
                    raise RuntimeError(r["error"].get("desc", "QMP error"))
                if "return" in r:
                    return r["return"]
                # asynchronous events (RESET, STOP, ...) are skipped

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def ppm_to_png(data):
    """QEMU PPM screendump -> (PNG bytes, blank?)."""
    parts = data.split(b"\n", 3)
    if len(parts) != 4 or parts[0] != b"P6":
        raise ValueError("unexpected screendump format")
    w, h = map(int, parts[1].split())
    px = parts[3]
    stride = w * 3
    raw = b"".join(b"\0" + px[i:i + stride] for i in range(0, stride * h, stride))

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body +
                struct.pack(">I", zlib.crc32(kind + body)))
    png = (b"\x89PNG\r\n\x1a\n" +
           chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
           chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))
    return png, px.count(0) == len(px)


class Emulator:
    def __init__(self, args):
        self.args = args
        self.proc = None
        self.qmp = None
        self.started = 0.0
        self.lock = threading.Lock()
        self.frame_lock = threading.Lock()
        self.frame = (None, True, 0.0)
        self.down_at = {}
        self.log = OUT / "panel-console.log"
        self.qmp_path = OUT / "panel-qmp.sock"
        self.console_sock = OUT / "console.sock"
        self.console = None
        self.dev = None
        self.mods = [Path(m) for m in args.mod]

    def start(self):
        OUT.mkdir(exist_ok=True)
        for p in (self.qmp_path, self.console_sock):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        env = dict(os.environ, A7C_FULL="1", A7C_DISPLAY="none",
                   A7C_SERIAL="chardev:con")
        if self.args.card and self.args.card != "none":
            env["A7C_SD_IMAGE"] = self.args.card
        for kv in self.args.env:
            k, _, v = kv.partition("=")
            env[k] = v
        hostwin = None
        if not self.args.no_loader and env.get("A7C_HOSTWIN", "auto") != "0":
            # shared 16 MiB window at PA 0x50000000: mods and the loader reach the
            # guest in a second instead of minutes over the serial console
            hostwin = Path(env.get("A7C_HOSTWIN") if env.get("A7C_HOSTWIN", "auto") != "auto"
                           else OUT / "hostwin.bin")
            if not hostwin.exists() or hostwin.stat().st_size != 16 << 20:
                with open(hostwin, "wb") as f:
                    f.truncate(16 << 20)
            env["A7C_HOSTWIN"] = str(hostwin.resolve())
        elif env.get("A7C_HOSTWIN", "auto") in ("auto", "0"):
            env.pop("A7C_HOSTWIN", None)
        self.proc = subprocess.Popen(
            ["bash", str(HERE / "run.sh"),
             "-chardev", f"socket,id=con,path={self.console_sock},server=on,wait=off,logfile={self.log}",
             "-qmp", f"unix:{self.qmp_path},server=on,wait=off", *self.args.qemu_arg],
            cwd=HERE, env=env, stdout=open(OUT / "panel-qemu.log", "wb"),
            stderr=subprocess.STDOUT, start_new_session=True)
        self.started = time.monotonic()
        self.qmp = Qmp(self.qmp_path)
        self.down_at.clear()
        loader = None if self.args.no_loader else Path(self.args.loader)
        if loader and all((loader / f).is_file() for f in ("core.bin", "mlv2.req", "mlv2_guard_arm")):
            self.console = moddev.Console(self.console_sock, log=None, process=self.proc)
            self.dev = moddev.ModDev(
                self.console, loader=loader, mods=self.mods,
                nudge=lambda: self.key("tap", "m"), hostwin=hostwin,
                hostwin_helper=HERE / "tools" / "hostwin_get")
            threading.Thread(target=self.dev.run, daemon=True).start()
        else:
            self.dev = None
        print(f"Emulator started (pid {self.proc.pid}); the camera screen appears after a few minutes.",
              flush=True)

    def stop(self):
        if self.dev:
            self.mods = list(self.dev.mods)
            self.dev = None
        if self.console:
            self.console.close()
            self.console = None
        if self.qmp:
            try:
                self.qmp.cmd("quit")
            except Exception:          # noqa: BLE001
                pass
            self.qmp.close()
            self.qmp = None
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                    self.proc.wait(10)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(self.proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        time.sleep(1)                        # qemu's image lock

    def restart(self):
        with self.lock:
            self.stop()
            self.start()

    def alive(self):
        return self.proc is not None and self.proc.poll() is None and self.qmp

    # --- screen ---------------------------------------------------------------------------
    def grab(self):
        """Screendump of console 0; cached for 150 ms so several viewers do not pile up."""
        with self.frame_lock:
            png, blank, t = self.frame
            if png is not None and time.monotonic() - t < 0.15:
                return png, blank
            if not self.alive():
                raise RuntimeError("emulator not running")
            path = OUT / "panel-lcd.ppm"
            self.qmp.cmd("screendump", filename=str(path))
            png, blank = ppm_to_png(path.read_bytes())
            self.frame = (png, blank, time.monotonic())
            return png, blank

    # --- keys -----------------------------------------------------------------------------
    def _send(self, key, down):
        self.qmp.cmd("input-send-event", events=[
            {"type": "key", "data": {"down": down, "key": {"type": "qcode", "data": key}}}])

    def key(self, action, key):
        if key not in HELD and key not in TAPPED:
            raise ValueError(f"unknown key {key}")
        if not self.alive():
            raise RuntimeError("emulator not running")
        if action == "tap" or key in TAPPED:
            if action in ("tap", "down"):
                self._send(key, True)
                threading.Timer(max(0.05, self.args.min_hold / 1000) if key in HELD else 0.05,
                                self._send, (key, False)).start()
            return
        if action == "down":
            if key not in self.down_at:
                self.down_at[key] = time.monotonic()
                self._send(key, True)
        elif action == "up":
            t = self.down_at.pop(key, None)
            if t is None:
                return
            wait = self.args.min_hold / 1000 - (time.monotonic() - t)
            if wait > 0:
                threading.Timer(wait, self._send, (key, False)).start()
            else:
                self._send(key, False)

    def status(self):
        st = {"running": bool(self.alive()), "uptime": 0, "state": "stopped",
              "console": "", "min_hold": self.args.min_hold}
        if self.alive():
            st["uptime"] = int(time.monotonic() - self.started)
            try:
                st["state"] = self.qmp.cmd("query-status")["status"]
            except Exception as e:      # noqa: BLE001 - shown on the page
                st["state"] = f"error: {e}"
        try:
            with open(self.log, "rb") as f:
                f.seek(0, 2)
                start = max(0, f.tell() - 65536)
                f.seek(start)
                lines = f.read().decode(errors="replace").splitlines()
                if start:
                    lines = lines[1:]           # first line is cut
                lines = [l for l in lines
                         if l.strip() and "Software Tuning is forbiden" not in l]
                st["console"] = lines[-1][:160] if lines else ""
        except FileNotFoundError:
            pass
        return st


def make_handler(emu):
    page = Path(__file__).resolve().parent / "a7c_panel.html"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            pass

        def _send(self, code, body, ctype, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            try:
                if path == "/":
                    self._send(200, page.read_bytes(), "text/html; charset=utf-8")
                elif path == "/lcd.png":
                    png, blank = emu.grab()
                    self._send(200, png, "image/png",
                               {"X-A7C-Lcd": "blank" if blank else "visible"})
                elif path == "/status":
                    self._json(emu.status())
                elif path == "/moddev":
                    self._json(emu.dev.status if emu.dev else {"state": "disabled", "mods": [], "events": []})
                elif path == "/moddev/log":
                    self._json(emu.dev.report() if emu.dev else {"state": "disabled"})
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:      # noqa: BLE001
                self._json({"error": str(e)}, 503)

        def _body(self, limit):
            size = int(self.headers.get("Content-Length") or 0)
            if size > limit:
                raise ValueError("request body too large")
            return self.rfile.read(size)

        def do_POST(self):
            if self.headers.get("X-A7C-Panel") != "1":
                return self._json({"error": "missing X-A7C-Panel header"}, 403)
            url = urlsplit(self.path)
            path = url.path
            parts = path.strip("/").split("/")
            try:
                if len(parts) == 3 and parts[0] == "key" and parts[1] in ("down", "up", "tap"):
                    emu.key(parts[1], parts[2])
                elif parts == ["emu", "pause"]:
                    emu.qmp.cmd("stop")
                elif parts == ["emu", "resume"]:
                    emu.qmp.cmd("cont")
                elif parts == ["emu", "restart"]:
                    threading.Thread(target=emu.restart, daemon=True).start()
                elif parts == ["emu", "screenshot"]:
                    png, _ = emu.grab()
                    name = OUT / time.strftime("panel-%Y%m%d-%H%M%S.png")
                    name.write_bytes(png)
                    return self._json({"saved": str(name.relative_to(HERE))})
                elif path.startswith(("/mods/", "/loader/", "/moddev/")):
                    if not emu.dev:
                        return self._json({"error": "mod loader disabled or not built (out/mlv2-a7c)"}, 404)
                    if path == "/mods/upload":
                        name = parse_qs(url.query).get("name", [""])[0]
                        if not moddev.MOD_NAME.match(name):
                            raise ValueError("a mod file name is NAME.mod (letters, digits, _ . -)")
                        data = self._body(MAX_MOD)
                        if len(data) < 68 or struct.unpack_from("<I", data, 0)[0] != SMOD_MAGIC:
                            raise ValueError("not an SMOD file (missing SMOD magic)")
                        uploads = OUT / "moddev_uploads"
                        uploads.mkdir(parents=True, exist_ok=True)
                        tmp = uploads / (name + ".part")
                        tmp.write_bytes(data)
                        os.replace(tmp, uploads / name)
                        emu.dev.request("add", uploads / name)
                    elif path == "/mods/watch":
                        raw = json.loads(self._body(4096) or b"{}").get("path", "")
                        target = moddev.host_path(raw)
                        if not target.is_file():
                            raise ValueError(f"{target} is not a file visible from WSL")
                        hdr = target.read_bytes()[:68]
                        if len(hdr) < 68 or struct.unpack_from("<I", hdr, 0)[0] != SMOD_MAGIC:
                            raise ValueError("not an SMOD file (missing SMOD magic)")
                        emu.dev.request("add", target)
                    elif path.startswith(("/mods/reload/", "/mods/remove/")):
                        emu.dev.request(parts[1], unquote(parts[2]))
                    elif path == "/loader/core":
                        emu.dev.request("core")
                    elif path == "/loader/rescan":
                        emu.dev.request("rescan")
                    elif path == "/moddev/sh":
                        cmd = self._body(4096).decode("utf-8")
                        out = emu.dev.sh(cmd, 60)
                        return self._send(200, out.encode("utf-8"), "text/plain; charset=utf-8")
                    else:
                        return self._json({"error": "not found"}, 404)
                    return self._json(emu.dev.status)
                else:
                    return self._json({"error": "not found"}, 404)
                self._json({"ok": True})
            except Exception as e:      # noqa: BLE001
                self._json({"error": str(e)}, 400)

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8642)
    ap.add_argument("--card", default="out/sdcard.img",
                    help="memory-card image (default out/sdcard.img; 'none' = no card)")
    ap.add_argument("--min-hold", type=int, default=0,
                    help="extra shortest press in host ms (default 0: the emulator itself holds "
                         "every press for A7C_KEY_MS = 150 ms of the camera's time)")
    ap.add_argument("--env", action="append", default=[],
                    help="extra run.sh setting, e.g. --env A7C_MODE_DIAL=A (repeatable)")
    ap.add_argument("--qemu-arg", action="append", default=[],
                    help="extra QEMU argument (repeatable), e.g. --qemu-arg=-d --qemu-arg=guest_errors")
    ap.add_argument("--loader", default=str(OUT / "mlv2-a7c"),
                    help="Mod Loader v2 build directory (default out/mlv2-a7c)")
    ap.add_argument("--no-loader", action="store_true",
                    help="disable Mod Loader v2 even if out/mlv2-a7c is built")
    ap.add_argument("--mod", action="append", default=[],
                    help="SMOD file to load and hot-reload (repeatable)")
    args = ap.parse_args()
    if args.card != "none" and not (HERE / args.card).exists() and not Path(args.card).exists():
        print(f"Memory card {args.card} not found: starting without a card.")
        args.card = "none"

    emu = Emulator(args)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(emu))
    emu.start()
    print(f"Panel: http://localhost:{args.port}   (Ctrl+C stops the panel and the emulator)",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        emu.stop()


if __name__ == "__main__":
    main()
