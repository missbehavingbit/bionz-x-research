"""Stage and toggle Arrivederci v4 (gamma + matrix + RAM sabin tables) on a live camera.

Uses tools/camera_debugger.py exclusively over RAM (/tmp_bt/mods/); never writes to flash.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SABIN_DIR = Path("artifacts/analysis/arri-v4-sabin")
MOD_PATH = Path("mods/arri_isp/arrivederci/Arrivederci.mod")
TABLES = (
    "dcv_psf_table_movie_bayer.bin",
    "dcv_psf_table_movie_4k.bin",
    "spica_musashi_mov_4k_c_tap_step1.bin",
    "spica_musashi_mov_4k_c_tap_step2.bin",
    "spica_musashi_mov_4k_y_tap_step1.bin",
    "spica_musashi_mov_4k_y_tap_step2.bin",
)


def _dbg(*args: str) -> str:
    proc = subprocess.run(
        [sys.executable, "tools/camera_debugger.py", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def stage() -> None:
    _dbg("exec", "mkdir -p /tmp_bt/mods/sabin")
    if MOD_PATH.exists():
        _dbg("push", str(MOD_PATH), "/tmp_bt/mods/Arrivederci.mod")
    for name in TABLES:
        local = SABIN_DIR / name
        if not local.exists():
            raise FileNotFoundError(f"missing {local}; run python -m tools.research.build_arri_v4_sabin")
        _dbg("push", str(local), f"/tmp_bt/mods/sabin/{name}")
    print("Staged Arrivederci.mod and 6 sabin tables to /tmp_bt/mods/.")


def mount_on() -> None:
    cmds = [f"mount --bind /tmp_bt/mods/sabin/{n} /usr/sabin/{n}" for n in TABLES]
    print(_dbg("exec", "; ".join(cmds) + "; mount | grep sabin; cat /proc/uptime"))


def mount_off() -> None:
    cmds = [f"umount /usr/sabin/{n} 2>/dev/null || true" for n in TABLES]
    print(_dbg("exec", "; ".join(cmds) + "; mount | grep sabin || echo 'no sabin bind mounts'; cat /proc/uptime"))


def status() -> None:
    print(_dbg("exec", "ls -la /tmp_bt/mods /tmp_bt/mods/sabin 2>/dev/null; mount | grep sabin || true; cat /proc/uptime"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("stage", "on", "off", "status"))
    args = ap.parse_args()
    {"stage": stage, "on": mount_on, "off": mount_off, "status": status}[args.action]()


if __name__ == "__main__":
    main()
