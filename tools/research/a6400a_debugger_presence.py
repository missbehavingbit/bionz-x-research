"""One-shot native debugger-channel observation through unchanged Windows USBSTOR.

No debugger request, shell command, upload, mode switch or storage-sector command
is sent. An IN response, BUSY or illegal command is not a verified root debugger.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from project_paths import PROJECT_ROOT
from tools.camera_debugger import FRAME_SIZE, host_lock
from tools.research.a6400a_native_readonly import (
    NativeTransport,
    ScsiCommandError,
    SonyBusy,
    admit_request,
    discover,
    probe_identity,
    select_target,
)

DEBUGGER_CDB = b"\x7a\x23" + bytes(10)


class PresenceTransport(NativeTransport):
    @staticmethod
    def _admit_request(command: bytes, data_out: bytes | None, read_size: int) -> str:
        if command == DEBUGGER_CDB and data_out is None and read_size == FRAME_SIZE:
            return "native_debugger_channel_presence_read"
        return admit_request(command, data_out, read_size)


def probe_debugger_channel(transport) -> dict:
    try:
        raw = transport.exchange(DEBUGGER_CDB, read_size=FRAME_SIZE)
    except SonyBusy:
        return {
            "status": "sony_busy_no_in_response",
            "debugger_verified": False,
            "reason": "One IN was BUSY; no polling, debugger request or launch attempted",
        }
    except ScsiCommandError as error:
        if (error.scsi_status != 2 or error.sense != (5, 0x20, 0)
                or error.transferred_bytes != 0):
            raise
        return {
            "status": "illegal_command_no_usable_channel",
            "debugger_verified": False,
            "reason": "One IN returned 05/20/00 without data; no usable debugger channel observed",
        }
    if len(raw) != FRAME_SIZE:
        raise ValueError("Short debugger-channel observation; no retry")
    return {
        "status": "response_available_not_a_handshake",
        "debugger_verified": False,
        "response_bytes": len(raw),
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "reason": "No matching debugger request was sent; response contents and root access are unverified",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-channel", action="store_true",
                        help="Verify model identity, then issue exactly one feature-0x23 IN")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    out = args.output_dir.resolve()
    if not out.is_relative_to(PROJECT_ROOT / "artifacts"):
        parser.error("Receipts must remain under artifacts")
    out.mkdir(parents=True, exist_ok=False)
    report = {
        "scope": "Channel observation only; not debugger qualification, launch or file access",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "status": "in_progress",
        "operations": [],
        "debugger_verified": False,
        "debugger_request_payloads_sent": 0,
        "presence_read_limit": 1,
        "mode_changes": False,
        "driver_changes": False,
        "sector_io": False,
        "camera_file_writes": False,
        "helper_staging_or_launch": False,
        "serials_retained": False,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "transport_source_sha256": hashlib.sha256(
            (PROJECT_ROOT / "tools" / "research" / "a6400a_native_readonly.py").read_bytes()
        ).hexdigest(),
    }
    try:
        with host_lock():
            report["metadata"] = discover()
            index = select_target(report["metadata"])
            report["selected_disk_index"] = index
            if args.check_channel:
                with PresenceTransport(index, report["operations"]) as transport:
                    report["identity"] = {}
                    probe_identity(transport, model_info=True, result=report["identity"])
                    report["channel"] = probe_debugger_channel(transport)
                report["status"] = "presence_checked_not_a_verified_debugger"
            else:
                report["status"] = "metadata_only"
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        report.update(status="blocked", error_type=type(error).__name__, error=str(error),
                      winerror=getattr(error, "winerror", None))
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    (out / "receipt.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "metadata_only" else 2


if __name__ == "__main__":
    raise SystemExit(main())
