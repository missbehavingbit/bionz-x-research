#!/usr/bin/env python3
"""Warm-guest test runner for the sony-a6400 QEMU mod-dev panel (run from the repository root, in WSL).

Boot the guest once (about 40 s), then send each test into the running QEMU in seconds instead of
rebooting: files go over the panel's raw serial push (POST /moddev/put, FNV-checked), commands over
POST /moddev/sh, mods over the existing hot-reload watcher.

    python3 qemu-a6400/tools/warm.py start [--mod X.mod ...] [--guest-file DST=SRC ...]
    python3 qemu-a6400/tools/warm.py status
    python3 qemu-a6400/tools/warm.py put SRC DST          # large files: DST under /setting
    python3 qemu-a6400/tools/warm.py sh 'COMMAND'
    python3 qemu-a6400/tools/warm.py lcd OUT.png
    python3 qemu-a6400/tools/warm.py press KEY [KEY ...]   # menu, center, fn, disp, iso, ...
    python3 qemu-a6400/tools/warm.py stop

The guest is disposable: /setting is the run's qcow2 overlay, never the camera.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORT = int(os.environ.get("A6400_WARM_PORT", "8650"))
BASE = f"http://127.0.0.1:{PORT}"
STATE = Path(os.environ.get("A6400_WARM_STATE", "/tmp/a6400-warm"))
HEADERS = {"Origin": BASE, "X-Camera-Panel": "1"}


def http(path: str, data: bytes | None = None, timeout: float = 120) -> bytes:
    req = urllib.request.Request(BASE + path, data=data, method="POST" if data is not None else "GET",
                                 headers=HEADERS if data is not None else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def state() -> dict:
    try:
        return json.loads(http("/moddev/log", timeout=30))
    except Exception as e:
        return {"state": f"down ({type(e).__name__})"}


def wait_ready(timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = state()
        if s.get("state", "").startswith(("watching", "installed")):
            return s
        if s.get("state", "").startswith("stopped"):
            raise SystemExit(f"panel stopped: {s['state']}")
        time.sleep(1)
    raise SystemExit("guest not ready in time")


def cmd_start(a):
    s = state()
    if not s.get("state", "").startswith("down"):
        print(f"already running: {s.get('state')}")
        return
    STATE.mkdir(parents=True, exist_ok=True)
    # The panel creates its logs exclusively ("xb"); keep the previous run's logs as *.prev instead.
    for name in ("panel.log", "panel.serial.log"):
        old = STATE / name
        if old.exists():
            old.replace(STATE / (name + ".prev"))
    args = [sys.executable, "-u", str(ROOT / "qemu-a6400/tools/rear_panel.py"), "--port", str(PORT),
            "--log", str(STATE / "panel.log"), "--inputs", a.inputs, "--loader", a.loader]
    for m in a.mod:
        args += ["--mod", m]
    for g in a.guest_file:
        args += ["--guest-file", g]
    for c in a.guest_cmd:
        args += ["--guest-cmd", c]
    if a.hostwin_helper:
        args += ["--hostwin-helper", a.hostwin_helper]
    if a.dmode is not None:
        # bootin's mode file (/setting/mode/dmode, first byte '0'..'3'): 0 kernel only, 1 global infra,
        # 2 LIRO (infrastructure + av-cam, no application), 3 application (default).
        mode_file = STATE / "dmode"
        mode_file.write_text(str(a.dmode))
        args += ["--setting-file", f"mode/dmode={mode_file}", "--bare"]
    for spec in a.setting_file:
        args += ["--setting-file", spec]
    env = dict(os.environ)
    # The host window needs a QEMU built from the current overlay (hostwin machine option).
    env.setdefault("A6400_QEMU_DIR", os.path.expanduser("~/sony-a6400-qemu-11.1.3"))
    env.setdefault("EVSEL_LATE_US", "500")
    t0 = time.monotonic()
    out = open(STATE / "panel.out", "ab")
    p = subprocess.Popen(args, cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    (STATE / "panel.pid").write_text(str(p.pid))
    s = wait_ready(a.timeout)
    print(f"ready in {time.monotonic() - t0:.1f}s: {s.get('state')}")


def cmd_status(a):
    print(json.dumps(state(), indent=1))


def cmd_put(a):
    data = Path(a.src).read_bytes()
    r = json.loads(http(f"/moddev/put?path={a.dst}", data, timeout=max(60, len(data) / 1000)))
    print(f"put {a.dst}: {r['bytes']} B ({r['sent']} B sent) in {r['seconds']}s")


def cmd_sh(a):
    sys.stdout.write(http("/moddev/sh", a.command.encode()).decode(errors="replace"))


def cmd_lcd(a):
    Path(a.out).write_bytes(http("/lcd.png", timeout=30))
    print(a.out)


def cmd_press(a):
    for k in a.keys:
        http(f"/press/{k}", b"", timeout=20)
        time.sleep(a.gap)


def cmd_stop(a):
    pid_file = STATE / "panel.pid"
    if pid_file.exists():
        try:
            os.killpg(int(pid_file.read_text()), 15)
        except ProcessLookupError:
            pass
        pid_file.unlink()
    print("stopped")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--inputs", default="artifacts/qemu-a6400/inputs-moddev-1")
    s.add_argument("--loader", default="artifacts/tests/mlv2-layout-build-4")
    s.add_argument("--mod", action="append", default=[])
    s.add_argument("--guest-file", action="append", default=[])
    s.add_argument("--guest-cmd", action="append", default=[])
    s.add_argument("--timeout", type=float, default=240)
    s.add_argument("--setting-file", action="append", default=[], metavar="NAME=SRC",
                   help="add SRC to the run's /setting overlay before boot (8.3 NAME or dir/name)")
    s.add_argument("--dmode", type=int, choices=(0, 1, 2, 3), default=None,
                   help="boot im.elf only up to this bootin debug mode (2 = infrastructure + av-cam, no "
                        "application); implies the panel's --bare mode (no Sony UI, no mod loader)")
    s.add_argument("--hostwin-helper", default="artifacts/tests/hostwin-build-1/hostwin_get",
                   help="hostwin_get build ('' disables the host window: serial transfers only)")
    sub.add_parser("status")
    p = sub.add_parser("put"); p.add_argument("src"); p.add_argument("dst")
    c = sub.add_parser("sh"); c.add_argument("command")
    l = sub.add_parser("lcd"); l.add_argument("out")
    k = sub.add_parser("press"); k.add_argument("keys", nargs="+"); k.add_argument("--gap", type=float, default=1.5)
    sub.add_parser("stop")
    a = ap.parse_args()
    {"start": cmd_start, "status": cmd_status, "put": cmd_put, "sh": cmd_sh, "lcd": cmd_lcd,
     "press": cmd_press, "stop": cmd_stop}[a.cmd](a)


if __name__ == "__main__":
    main()
