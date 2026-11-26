"""USB-debugger launcher for RAM-only 5 GHz Wi-Fi LiveView streaming (/tmp_bt).

Subcommands:
  dry-run         Validate local artifacts (daemon + bootstrap script) offline.
  prep            Read-only camera health check in MSC.
  bootstrap-wifi  Push and run wifi_5ghz_bootstrap.sh (STA or AP mode) in /tmp_bt.
  start-stream    Push and start a6400-lived --wifi-stream in /tmp_bt.
  stop-stream     Stop a6400-lived, collect logs, and clean /tmp_bt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
DAEMON_PATH = ROOT / "camera-daemon/build/a6400-lived"
BOOTSTRAP_PATH = ROOT / "tools/native_camera/wifi_5ghz_bootstrap.sh"

R_DAEMON = "/tmp_bt/a6400-lived"
R_BOOTSTRAP = "/tmp_bt/wifi_5ghz_bootstrap.sh"
R_OUT = "/tmp_bt/wifi_daemon.out"
R_JSONL = "/tmp_bt/a6400_lived.jsonl"


def _connect():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tools.camera_debugger import OP_EXEC, connect
    return connect, OP_EXEC


def cmd_dry_run(_args: argparse.Namespace) -> int:
    items = {}
    all_ok = True
    for key, path in (("daemon", DAEMON_PATH), ("bootstrap", BOOTSTRAP_PATH)):
        exists = path.is_file()
        all_ok = all_ok and exists
        items[key] = {
            "path": str(path),
            "exists": exists,
            "size": path.stat().st_size if exists else 0,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if exists else None,
        }
    print(json.dumps({"status": "dry_run", "all_ok": all_ok, "artifacts": items}, indent=2))
    return 0 if all_ok else 2


def cmd_prep(_args: argparse.Namespace) -> int:
    connect, OP_EXEC = _connect()
    with connect() as dbg:
        uptime = dbg.read_all("/proc/uptime")[0].decode("latin1", "replace").strip()
        tmp_bt = dbg.request(OP_EXEC, b"ls -la /tmp_bt")["payload"].decode("latin1", "replace")
        wlan = dbg.request(OP_EXEC, b"lsmod | grep -E 'bcmdhd|cfg80211|wlan' || true")["payload"].decode("latin1", "replace")
    print(json.dumps({"status": "ready", "uptime": uptime, "wlan_modules": wlan, "tmp_bt": tmp_bt}, indent=2))
    return 0


def cmd_bootstrap_wifi(args: argparse.Namespace) -> int:
    if not BOOTSTRAP_PATH.is_file():
        return 3
    connect, OP_EXEC = _connect()
    with connect() as dbg:
        dbg.push(R_BOOTSTRAP, BOOTSTRAP_PATH.read_bytes())
        dbg.request(OP_EXEC, f"chmod 755 {R_BOOTSTRAP}".encode())
        cmd = (
            f"{R_BOOTSTRAP} --mode {args.mode} --ssid '{args.ssid}' --psk '{args.psk}' "
            f"--freq {args.freq} --nvram '{args.nvram}' >/tmp_bt/wifi_boot.log 2>&1; "
            f"echo RC=$?; cat /tmp_bt/wifi_boot.log"
        )
        res = dbg.request(OP_EXEC, cmd.encode())
    print(res["payload"].decode("latin1", "replace"))
    return 0 if res["status"] == 0 else 1


def cmd_start_stream(args: argparse.Namespace) -> int:
    if not DAEMON_PATH.is_file():
        return 3
    connect, OP_EXEC = _connect()
    with connect() as dbg:
        dbg.push(R_DAEMON, DAEMON_PATH.read_bytes())
        dbg.request(OP_EXEC, f"chmod 755 {R_DAEMON}".encode())
        dbg.request(
            OP_EXEC,
            b"echo 524288 > /proc/sys/net/core/wmem_max 2>/dev/null || true; "
            b"echo '4096 131072 524288' > /proc/sys/net/ipv4/tcp_wmem 2>/dev/null || true",
        )
        dargs = (
            f"--splice-scalar --wifi-stream --arm-transport "
            f"--path-width {args.path_width} --comp-level {args.comp_level} "
            f"--fps {args.fps} --duration {args.duration} "
            f"--wifi-stream-port {args.stream_port} --wifi-http-port {args.http_port} "
            f"--wifi-ctrl-port {args.ctrl_port}"
        )
        res = dbg.request(OP_EXEC, f"{R_DAEMON} {dargs} >{R_OUT} 2>&1 & echo pid=$!".encode())
        time.sleep(2.0)
        ps = dbg.request(OP_EXEC, b"ps | grep a6400-lived")["payload"].decode("latin1", "replace")
        out = dbg.request(OP_EXEC, f"cat {R_OUT} 2>/dev/null || true".encode())["payload"].decode("latin1", "replace")
    print(json.dumps({"launch": res["payload"].decode("latin1", "replace"), "ps": ps, "out": out}, indent=2))
    return 0


def cmd_stop_stream(_args: argparse.Namespace) -> int:
    connect, OP_EXEC = _connect()
    with connect() as dbg:
        dbg.request(OP_EXEC, b"busybox killall -TERM a6400-lived 2>/dev/null || true")
        time.sleep(1.5)
        jsonl = dbg.read_all(R_JSONL, 65536)[0].decode("latin1", "replace")
        dbg.request(OP_EXEC, f"rm -f {R_DAEMON} {R_BOOTSTRAP} {R_OUT} {R_JSONL} /tmp_bt/a6400_lived_receipt.json".encode())
        uptime = dbg.read_all("/proc/uptime")[0].decode("latin1", "replace").strip()
    print(json.dumps({"status": "stopped", "uptime": uptime, "jsonl_tail": jsonl[-1000:]}, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dry-run")
    sub.add_parser("prep")
    bw = sub.add_parser("bootstrap-wifi")
    bw.add_argument("--mode", choices=("sta", "ap"), required=True)
    bw.add_argument("--ssid", required=True)
    bw.add_argument("--psk", required=True)
    bw.add_argument("--freq", default="5180")
    bw.add_argument("--nvram", default="/usr/share/network/nvram/high/bcm4339_H_14.txt")
    ss = sub.add_parser("start-stream")
    ss.add_argument("--path-width", type=int, default=1024)
    ss.add_argument("--comp-level", type=int, default=30)
    ss.add_argument("--fps", type=int, default=30)
    ss.add_argument("--duration", type=int, default=0)
    ss.add_argument("--stream-port", type=int, default=9640)
    ss.add_argument("--http-port", type=int, default=8080)
    ss.add_argument("--ctrl-port", type=int, default=9641)
    sub.add_parser("stop-stream")
    args = p.parse_args()
    return {
        "dry-run": cmd_dry_run,
        "prep": cmd_prep,
        "bootstrap-wifi": cmd_bootstrap_wifi,
        "start-stream": cmd_start_stream,
        "stop-stream": cmd_stop_stream,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
