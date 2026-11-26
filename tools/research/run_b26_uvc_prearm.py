"""Supervised Run B26 harness for the E0476 EP0 status pre-arm candidate (Phase 4).

Subcommands:
  dry-run         Offline artifact, hash, and host-tool readiness check (no camera access).
  prep            Read-only baseline check; aborts if camera or debugger state is unclean.
  arm             Stage E0476 module + a6400-lived into /tmp_bt, insmod, mknod, arm daemon.
  wait-composite  Wait for authored composite 054C:0322 (UVC MI_01 + MTP MI_00).
  ffmpeg          Execute one DirectShow capture attempt and record timing/frame.
  wait-restore    Wait for automatic return to stock MSC 054C:0CA8 after teardown.
  collect         Recover dmesg/ring/usb-state, rmmod, clean /tmp_bt, verify uptime.
  verify-logs     Offline check of Run B26 acceptance criteria (PREARM r=1, RAMRING).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN_DIR = ROOT / "artifacts/analysis/e0476-runb26-prearm"

MODULE_PATH = ROOT / "artifacts/native/uvc-transport-e0472-status-prearm/usbg_video_a6400_shared_candidate.ko"
MODULE_SHA256 = "dcc2f64f944790dd1b8519b29ee6f6ce709a03129fb92d363fdafe14f16915cd"
MODULE_SIZE = 83644

DAEMON_PATH = ROOT / "camera-daemon/build/a6400-lived"
DAEMON_SHA256 = "1eda841852ddd6ed856db040df57d48673cdf1e0f6c3def35281ec364aa00533"
DAEMON_SIZE = 632812

R_MODULE = "/tmp_bt/e0476_prearm.ko"
R_DAEMON = "/tmp_bt/a6400-lived"
R_NODE = "/tmp_bt/video"
R_JSONL = "/tmp_bt/a6400_lived.jsonl"
R_OUT = "/tmp_bt/e0476_daemon.out"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(run_dir: Path, rel: str, obj: dict) -> Path:
    path = run_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    return path


def verify_local_artifacts() -> dict:
    """Verify E0476 module and daemon identities offline without touching any camera."""
    checks = {}
    for label, path, exp_sha, exp_size in (
        ("module", MODULE_PATH, MODULE_SHA256, MODULE_SIZE),
        ("daemon", DAEMON_PATH, DAEMON_SHA256, DAEMON_SIZE),
    ):
        exists = path.is_file()
        size = path.stat().st_size if exists else 0
        digest = sha256_file(path) if exists else ""
        ok = exists and size == exp_size and digest == exp_sha
        checks[label] = {
            "path": str(path),
            "exists": exists,
            "size": size,
            "expected_size": exp_size,
            "sha256": digest,
            "expected_sha256": exp_sha,
            "ok": ok,
        }
    checks["ffmpeg_on_path"] = shutil.which("ffmpeg") is not None
    checks["all_ok"] = checks["module"]["ok"] and checks["daemon"]["ok"]
    return checks


def parse_b26_log_evidence(log_text: str) -> dict:
    """Parse dmesg/A6400K.LOG lines for E0476 PREARM and RAMRING acceptance markers."""
    prearm_lines = re.findall(r"\[a6400_uvc\] PREARM d=(\d+) st=(\d+) nx=(\d+) r=(\d+)", log_text)
    prearm_events = [
        {"dir_out": int(d), "state": int(st), "next": int(nx), "armed": int(r)}
        for d, st, nx, r in prearm_lines
    ]
    ramring_matches = re.findall(
        r"\[a6400_uvc\] RAMRING n=(\d+) dropped=(\d+) start=(\d+) class=(\d+) "
        r"get=(\d+) set=(\d+) queue=(\d+) complete=(\d+) prearm=(\d+)/(\d+)/(\d+) st=(\d+) nx=(\d+)",
        log_text,
    )
    ramring = None
    if ramring_matches:
        m = [int(x) for x in ramring_matches[-1]]
        ramring = {
            "n": m[0], "dropped": m[1], "start": m[2], "class": m[3],
            "get": m[4], "set": m[5], "queue": m[6], "complete": m[7],
            "prearm_attempts": m[8], "prearm_armed": m[9], "prearm_skipped": m[10],
            "last_state": m[11], "last_next": m[12],
        }
    has_get_prearm = any(e["dir_out"] == 0 and e["state"] == 2 and e["next"] == 3 and e["armed"] == 1
                         for e in prearm_events)
    return {
        "prearm_events": prearm_events,
        "ramring": ramring,
        "has_get_cur_prearm_r1": has_get_prearm,
        "accepted": has_get_prearm and (ramring is None or ramring["prearm_armed"] >= 1),
    }


def pnp_sony() -> list[dict]:
    ps = (
        "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
        "Where-Object { $_.InstanceId -like '*VID_054C*' } | "
        "Select-Object Status,Class,InstanceId | ConvertTo-Json -Compress"
    )
    proc = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                          capture_output=True, text=True, timeout=60)
    text = proc.stdout.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [{"status": d.get("Status"), "class": d.get("Class"), "instance": d.get("InstanceId")}
            for d in data]


def composite_state(devices: list[dict]) -> tuple[bool, bool, bool, bool]:
    def has(pid: str, mi: str | None = None) -> bool:
        return any(pid in (d.get("instance") or "") and (mi is None or mi in (d.get("instance") or ""))
                   and d.get("status") == "OK" for d in devices)
    return (has("PID_0322"), has("PID_0322", "MI_01"), has("PID_0322", "MI_00"), has("PID_0CA8"))


def _connect_debugger():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tools.camera_debugger import connect, OP_EXEC
    return connect, OP_EXEC


def cmd_dry_run(args: argparse.Namespace) -> int:
    result = {"status": "dry_run", "utc": utc_now(), "physical_camera_access": False, **verify_local_artifacts()}
    save_json(args.run_dir, "preflight/dry-run.json", result)
    print(json.dumps(result, indent=2))
    return 0 if result["all_ok"] else 2


def cmd_prep(args: argparse.Namespace) -> int:
    connect, OP_EXEC = _connect_debugger()
    prep: dict = {"status": "started", "utc": utc_now(), "steps": {}}
    devices = pnp_sony()
    prep["pnp_before"] = devices
    with connect() as dbg:
        prep["steps"]["uptime"] = dbg.read_all("/proc/uptime")[0].decode("latin1", "replace").strip()
        modules = dbg.read_all("/proc/modules", 1 << 16)[0]
        status149 = dbg.read_all("/proc/149/status", 16384)[0]
        prep["steps"]["modules_sha256"] = hashlib.sha256(modules).hexdigest()
        (args.run_dir / "preflight").mkdir(parents=True, exist_ok=True)
        (args.run_dir / "preflight/modules-before.txt").write_bytes(modules)
        prep["steps"]["tmp_bt"] = dbg.request(OP_EXEC, b"ls -la /tmp_bt")["payload"].decode("latin1", "replace")
        info = dbg.request(1)["payload"].decode("utf-8", "replace")
        prep["steps"]["debugger"] = info
    loaded = any(line.startswith(b"usbg_video ") for line in modules.splitlines())
    traced = not bool(re.search(rb"^TracerPid:\s+0$", status149, re.M))
    _, _, _, msc = composite_state(devices)
    problems = []
    if loaded:
        problems.append("usbg_video already loaded")
    if traced:
        problems.append("PID149 already traced")
    if not msc:
        problems.append("camera is not in stock MSC (054C:0CA8)")
    prep["status"] = "ready" if not problems else "not_ready"
    prep["problems"] = problems
    save_json(args.run_dir, "preflight/prepare.json", prep)
    print(json.dumps({"status": prep["status"], "problems": problems, "uptime": prep["steps"]["uptime"]}, indent=2))
    return 0 if not problems else 2


def cmd_arm(args: argparse.Namespace) -> int:
    mod_path = Path(args.module_path) if getattr(args, "module_path", None) else MODULE_PATH
    exp_sha = getattr(args, "module_sha256", None) or MODULE_SHA256
    exp_size = getattr(args, "module_size", None) or MODULE_SIZE
    if not mod_path.is_file() or mod_path.stat().st_size != exp_size or sha256_file(mod_path) != exp_sha:
        save_json(args.run_dir, "preflight/arm.json", {"status": "module_identity_mismatch", "path": str(mod_path)})
        return 3
    if not DAEMON_PATH.is_file() or DAEMON_PATH.stat().st_size != DAEMON_SIZE or sha256_file(DAEMON_PATH) != DAEMON_SHA256:
        save_json(args.run_dir, "preflight/arm.json", {"status": "daemon_identity_mismatch"})
        return 3
    connect, OP_EXEC = _connect_debugger()
    arm: dict = {"status": "started", "utc": utc_now(), "module_sha256": exp_sha, "steps": {}}
    with connect() as dbg:
        arm["steps"]["push_module"] = dbg.push(R_MODULE, mod_path.read_bytes())
        arm["steps"]["push_daemon"] = dbg.push(R_DAEMON, DAEMON_PATH.read_bytes())
        arm["steps"]["chmod"] = dbg.request(OP_EXEC, f"chmod 755 {R_DAEMON}".encode())["status"]
        res = dbg.request(OP_EXEC, f"insmod {R_MODULE}".encode())
        arm["steps"]["insmod"] = {"status": res["status"], "output": res["payload"].decode("latin1", "replace")}
        modules = dbg.read_all("/proc/modules", 1 << 16)[0]
        visible = any(line.startswith(b"usbg_video ") for line in modules.splitlines())
        arm["steps"]["module_visible"] = visible
        if res["status"] != 0 or not visible:
            arm["status"] = "insmod_failed"
            save_json(args.run_dir, "preflight/arm.json", arm)
            return 3
        node_res = dbg.request(OP_EXEC, f"test -c {R_NODE} || mknod {R_NODE} c 248 33; ls -l {R_NODE}".encode())
        arm["steps"]["mknod"] = node_res["payload"].decode("latin1", "replace")
        daemon_args = (
            "--arm-transport --wait-pcremote --timeout 900 --duration 45 "
            "--stats-interval 5 --sd-log --sd-mount --dmesg-sd --verbose"
        )
        launch = dbg.request(OP_EXEC, f"{R_DAEMON} {daemon_args} >{R_OUT} 2>&1 & echo pid=$!".encode())
        arm["steps"]["launch"] = launch["payload"].decode("latin1", "replace")
        time.sleep(3.0)
        arm["steps"]["ps"] = dbg.request(OP_EXEC, b"ps | grep a6400-lived")["payload"].decode("latin1", "replace")
        jsonl = dbg.read_all(R_JSONL, 65536)[0]
        arm["steps"]["armed"] = b"device_open" in jsonl and b"usb_identity" in jsonl
        (args.run_dir / "preflight").mkdir(parents=True, exist_ok=True)
        (args.run_dir / "preflight/daemon-jsonl-before-switch.txt").write_bytes(jsonl)
        if not arm["steps"]["armed"]:
            arm["status"] = "daemon_not_armed"
            save_json(args.run_dir, "preflight/arm.json", arm)
            return 4
    arm["status"] = "armed"
    save_json(args.run_dir, "preflight/arm.json", arm)
    print(json.dumps({"status": "armed", "ps": arm["steps"]["ps"]}, indent=2))
    return 0


def cmd_wait_composite(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        devices = pnp_sony()
        c0322, uvc, mtp, _ = composite_state(devices)
        if c0322 and uvc and mtp:
            save_json(args.run_dir, "preflight/composite-detected.json",
                      {"utc": utc_now(), "instances": [d["instance"] for d in devices]})
            print(json.dumps({"status": "composite_detected", "uvc_mi01": uvc, "mtp_mi00": mtp}, indent=2))
            return 0
        time.sleep(1.0)
    print(json.dumps({"status": "composite_timeout", "seconds": args.timeout}, indent=2))
    return 2


def cmd_ffmpeg(args: argparse.Namespace) -> int:
    devices = pnp_sony()
    c0322, uvc, _, _ = composite_state(devices)
    if not (c0322 and uvc):
        print(json.dumps({"status": "composite_missing"}))
        return 2
    frame = args.run_dir / "runb26-frame.png"
    cmd = ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-f", "dshow",
           "-rtbufsize", "256M", "-i", "video=ILCE-6400",
           "-frames:v", "1", "-update", "1", str(frame)]
    start = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
    elapsed = round(time.monotonic() - start, 3)
    result = {
        "status": "attempted",
        "utc": utc_now(),
        "duration_s": elapsed,
        "returncode": proc.returncode,
        "frame_exists": frame.is_file(),
        "stderr_tail": proc.stderr.splitlines()[-12:] if proc.stderr else [],
    }
    save_json(args.run_dir, "preflight/ffmpeg.json", result)
    print(json.dumps(result, indent=2))
    return 0 if proc.returncode == 0 and frame.is_file() else 1


def cmd_wait_restore(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        devices = pnp_sony()
        c0322, _, _, msc = composite_state(devices)
        if msc and not c0322:
            save_json(args.run_dir, "preflight/restore-detected.json", {"utc": utc_now(), "auto_restored": True})
            print(json.dumps({"status": "msc_restored", "auto_restored": True}, indent=2))
            return 0
        time.sleep(1.0)
    print(json.dumps({"status": "restore_timeout", "seconds": args.timeout}, indent=2))
    return 2


def cmd_collect(args: argparse.Namespace) -> int:
    connect, OP_EXEC = _connect_debugger()
    out: dict = {"status": "started", "utc": utc_now(), "steps": {}}
    with connect() as dbg:
        out["steps"]["uptime"] = dbg.read_all("/proc/uptime")[0].decode("latin1", "replace").strip()
        modules = dbg.read_all("/proc/modules", 1 << 16)[0]
        out["steps"]["module_loaded"] = any(line.startswith(b"usbg_video ") for line in modules.splitlines())
        ps = dbg.request(OP_EXEC, b"ps | grep a6400-lived")["payload"].decode("latin1", "replace")
        out["steps"]["daemon_running"] = bool(re.search(r"a6400-lived", ps))
        (args.run_dir / "preflight").mkdir(parents=True, exist_ok=True)
        for name, fn in (("dmesg", lambda: dbg.dmesg()[0]), ("ring", lambda: dbg.ring_capture()[0])):
            data = fn()
            (args.run_dir / "preflight" / f"{name}-after-run.bin").write_bytes(data)
            out["steps"][f"{name}_bytes"] = len(data)
        if out["steps"]["module_loaded"] and not out["steps"]["daemon_running"]:
            res = dbg.request(OP_EXEC, b"rmmod usbg_video")
            out["steps"]["rmmod"] = res["status"]
        dbg.request(OP_EXEC, f"rm -f {R_MODULE} {R_DAEMON} {R_NODE} {R_OUT}".encode())
        out["steps"]["tmp_bt_after"] = dbg.request(OP_EXEC, b"ls -la /tmp_bt")["payload"].decode("latin1", "replace")
        out["steps"]["uptime_after"] = dbg.read_all("/proc/uptime")[0].decode("latin1", "replace").strip()
    out["status"] = "completed"
    save_json(args.run_dir, "preflight/collect.json", out)
    print(json.dumps(out, indent=2))
    return 0


def cmd_verify_logs(args: argparse.Namespace) -> int:
    text = Path(args.log).read_text(encoding="utf-8", errors="replace")
    verdict = parse_b26_log_evidence(text)
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["accepted"] else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dry-run")
    sub.add_parser("prep")
    p_arm = sub.add_parser("arm")
    p_arm.add_argument("--module-path", type=Path)
    p_arm.add_argument("--module-sha256", type=str)
    p_arm.add_argument("--module-size", type=int)
    p = sub.add_parser("wait-composite")
    p.add_argument("--timeout", type=int, default=150)
    p = sub.add_parser("ffmpeg")
    p.add_argument("--timeout", type=int, default=60)
    p = sub.add_parser("wait-restore")
    p.add_argument("--timeout", type=int, default=150)
    sub.add_parser("collect")
    p = sub.add_parser("verify-logs")
    p.add_argument("--log", type=Path, required=True)
    args = ap.parse_args(argv)
    return {
        "dry-run": cmd_dry_run,
        "prep": cmd_prep,
        "arm": cmd_arm,
        "wait-composite": cmd_wait_composite,
        "ffmpeg": cmd_ffmpeg,
        "wait-restore": cmd_wait_restore,
        "collect": cmd_collect,
        "verify-logs": cmd_verify_logs,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
