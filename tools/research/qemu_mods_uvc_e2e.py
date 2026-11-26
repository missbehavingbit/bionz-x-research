#!/usr/bin/env python3
"""End-to-end QEMU test for triggering USB Streaming Test from the MODS tab via rear_panel.py."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def http_get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_get_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.read()


def press_key(base_url: str, key: str) -> None:
    req = urllib.request.Request(
        f"{base_url}/press/{key}",
        data=b"",
        method="POST",
        headers={"Origin": base_url, "X-Camera-Panel": "1"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        resp.read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu-dir", type=Path, default=Path("/home/administrator/sony-a6400-qemu-11.1.3"))
    parser.add_argument("--inputs", type=Path, default=Path("/home/administrator/a6400-inputs/inputs-moddev-1"))
    parser.add_argument("--loader", type=Path, default=ROOT / "artifacts/tests/mlv2-action-build-1")
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()

    args.report_dir.mkdir(parents=True, exist_ok=True)
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_path = args.report_dir / "panel.log"
    env = dict(os.environ, A6400_QEMU_DIR=str(args.qemu_dir))
    cmd = [
        sys.executable, "-u", str(ROOT / "qemu-a6400/tools/rear_panel.py"),
        "--port", str(port),
        "--log", str(log_path),
        "--inputs", str(args.inputs),
        "--loader", str(args.loader),
        "--uvc-test",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    report: dict = {"status": "failed", "port": port}
    try:
        # 1. Wait for ModDev to finish installing & loading core
        deadline = time.monotonic() + 300.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"rear_panel exited early ({proc.returncode})")
            try:
                st = http_get_json(f"{base_url}/moddev")
                if st.get("state") == "watching for rebuilt mods":
                    report["moddev_ready"] = st
                    break
            except (urllib.error.URLError, OSError, ValueError):
                pass
            time.sleep(1.0)
        else:
            raise TimeoutError("Timed out waiting for ModDev ready")

        # 2. Navigate from open MENU (Network2 item 5) -> UP x5 to tab bar -> RIGHT x4 to MODS tab -> DOWN -> CENTER
        time.sleep(1.5)
        (args.report_dir / "lcd-0-ready.png").write_bytes(http_get_bytes(f"{base_url}/lcd.png"))
        for _ in range(5):
            press_key(base_url, "disp")
            time.sleep(0.8)
        (args.report_dir / "lcd-1-tabbar.png").write_bytes(http_get_bytes(f"{base_url}/lcd.png"))
        for _ in range(4):
            press_key(base_url, "iso")
            time.sleep(0.8)
        (args.report_dir / "lcd-2-modstab.png").write_bytes(http_get_bytes(f"{base_url}/lcd.png"))
        press_key(base_url, "exposure")
        time.sleep(0.8)
        (args.report_dir / "lcd-3-selected.png").write_bytes(http_get_bytes(f"{base_url}/lcd.png"))
        press_key(base_url, "center")
        time.sleep(1.5)
        (args.report_dir / "lcd-4-activated.png").write_bytes(http_get_bytes(f"{base_url}/lcd.png"))

        # 3. Wait for 054c:0322 enumeration and >= 3 valid 720p MJPEG frames
        stream_deadline = time.monotonic() + 60.0
        uvc = {}
        while time.monotonic() < stream_deadline:
            uvc = http_get_json(f"{base_url}/uvc")
            if uvc.get("vid_pid") == "054c:0322" and uvc.get("valid_frames", 0) >= 3:
                break
            time.sleep(0.5)
        else:
            raise TimeoutError(f"Timed out waiting for UVC frames: {uvc}")

        jpeg = http_get_bytes(f"{base_url}/uvc/frame.jpg")
        (args.report_dir / "uvc-frame.jpg").write_bytes(jpeg)
        report["uvc_streaming"] = uvc
        report["frame_bytes"] = len(jpeg)

        # 4. Reopen MENU -> toggle USB Streaming Test OFF -> verify streaming stops
        press_key(base_url, "menu")
        time.sleep(2.0)
        press_key(base_url, "center")
        time.sleep(1.0)
        press_key(base_url, "center")
        time.sleep(2.0)
        uvc_after = http_get_json(f"{base_url}/uvc")
        report["uvc_after_stop"] = uvc_after
        report["status"] = "passed"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
        (args.report_dir / "panel-stdout.log").write_text(out or "")
        (args.report_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
