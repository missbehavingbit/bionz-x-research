"""Opt-in ILCE-6400A 1.01 fixed-nflasha5 acquisition; never a general flash reader."""
from __future__ import annotations

import argparse
from array import array
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from threading import Event
import time
from typing import TYPE_CHECKING

from project_paths import PROJECT_ROOT
from tools.research import a6400a_native_readonly as native
from tools.research import a6400a_senser_acquire as senser
from tools.research.a6400a_fixed_reader import (
    CAPACITY, COMMAND, FRAME_BYTES, HEADER, TRANSFER_BYTES, FixedPartitionReader,
)

if TYPE_CHECKING:
    from usb.core import Device

REQUEST = HEADER.pack(len(COMMAND), 0x40, 1, 0, 0, 0, 0) + COMMAND
ATTEMPT_ROOT = PROJECT_ROOT / "artifacts" / "camera-sessions" / "a6400a-fixed-nflasha5"


class FixedUsbTransport:
    """Non-owning adapter for the exact primary endpoints already used for authentication."""

    def __init__(self, device: Device, driver, metadata: dict, baseline: dict,
                 events: list, *, clock: Callable[[], float] = time.monotonic):
        senser.admit_device(metadata, baseline, "read")
        if (metadata["pid"] != 0x0336 or driver.getId() != (0x054C, 0x0336)
                or (device.idVendor, device.idProduct) != (0x054C, 0x0336)
                or driver.backend.dev is not device):
            raise ValueError("Fixed-node transport requires the same admitted 054C:0336 handle")
        interfaces = list(device.get_active_configuration())
        if (len(interfaces) != 1 or interfaces[0].bInterfaceNumber != 0
                or interfaces[0].bAlternateSetting != 0 or interfaces[0].bInterfaceClass != 0xFF):
            raise ValueError("Fixed-node transport requires vendor interface 0, alternate 0")
        endpoints = list(interfaces[0])
        inputs = [ep for ep in endpoints if ep.bmAttributes == 2 and ep.bEndpointAddress & 0x80]
        outputs = [ep for ep in endpoints if ep.bmAttributes == 2 and not ep.bEndpointAddress & 0x80]
        if (not inputs or not outputs or not driver.epIn or not driver.epOut
                or driver.epIn[0] != inputs[0].bEndpointAddress
                or driver.epOut[0] != outputs[0].bEndpointAddress
                or inputs[0].wMaxPacketSize != 512 or outputs[0].wMaxPacketSize != 512):
            raise ValueError("Primary bulk endpoints differ from authentication or are not high-speed 512-byte endpoints")
        self.device = device
        self.ep_in = inputs[0].bEndpointAddress
        self.ep_out = outputs[0].bEndpointAddress
        self.clock = clock
        self.events = events
        self.attempted = False
        self.acknowledged = False
        self.deadline = 0.0
        self.remaining = CAPACITY + math.ceil(CAPACITY / FRAME_BYTES) * HEADER.size
        self.calls = 0
        self.max_calls = 128 + 64 * math.ceil(CAPACITY / TRANSFER_BYTES)
        events.append({"operation": "fixed_transport_admitted", "pid": 0x0336,
                       "ep_in": self.ep_in, "ep_out": self.ep_out, "max_packet_bytes": 512})

    def _timeout(self, timeout_ms: int) -> int:
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 2000:
            raise ValueError("Fixed-node I/O timeout outside the 1..2000-ms profile")
        remaining = self.deadline - self.clock()
        if remaining <= 0 or self.calls >= self.max_calls:
            raise TimeoutError("Fixed-node transport deadline or I/O budget exhausted; no retry")
        self.calls += 1
        return min(timeout_ms, max(1, math.ceil(remaining * 1000)))

    def write(self, data: bytes, *, timeout_ms: int) -> int:
        if self.attempted or data != REQUEST:
            raise ValueError("Only one exact fixed-nflasha5 request is permitted")
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 2000:
            raise ValueError("Fixed-node write timeout outside the declared profile")
        self.attempted = True
        self.deadline = self.clock() + 120
        timeout = self._timeout(timeout_ms)
        self.events.append({"operation": "fixed_request_attempted", "bytes": len(data)})
        count = self.device.write(self.ep_out, data, timeout=timeout)
        if type(count) is not int or not 0 <= count <= len(data):
            raise OSError("USB write did not return a valid acknowledged byte count")
        self.events.append({"operation": "fixed_request_result", "acknowledged_bytes": count})
        self.acknowledged = count == len(data)
        return count

    def read(self, length: int, *, timeout_ms: int) -> bytes:
        if not self.acknowledged or type(length) is not int or not 0 < length <= TRANSFER_BYTES:
            raise ValueError("Fixed-node read requires a complete request and a bounded primary transfer")
        timeout = self._timeout(timeout_ms)
        raw = self.device.read(self.ep_in, length, timeout=timeout)
        if isinstance(raw, array):
            if raw.typecode != "B":
                raise OSError("USB read returned a non-byte array")
            value = raw.tobytes()
        elif isinstance(raw, (bytes, bytearray)):
            value = bytes(raw)
        else:
            raise OSError("USB read returned a non-byte buffer")
        if len(value) > length or len(value) > self.remaining:
            raise OSError("USB read exceeded the fixed transfer/capacity budget")
        self.remaining -= len(value)
        self.events.append({"operation": "fixed_read", "requested_bytes": length,
                            "received_bytes": len(value), "timeout_ms": timeout})
        return value


def _fresh(timestamp: str, now: datetime) -> None:
    if not isinstance(timestamp, str):
        raise ValueError("Identity/baseline timestamp is missing")
    measured = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if measured.utcoffset() is None or not 0 <= (now - measured).total_seconds() <= 1800:
        raise ValueError("Fresh identity and original-driver receipts from the last 30 minutes are required")


def load_receipt(path: Path) -> tuple[dict, str]:
    if path.drive and path.drive.lower() != PROJECT_ROOT.drive.lower():
        raise ValueError("Receipt input must be a regular file under repository artifacts")
    resolved = path.resolve()
    if not resolved.is_relative_to(PROJECT_ROOT / "artifacts") or not resolved.is_file():
        raise ValueError("Receipt input must be a regular file under repository artifacts")
    with resolved.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Receipt input exceeds the 64-KiB metadata bound")
    record = json.loads(raw)
    if not isinstance(record, dict):
        raise ValueError("Receipt input must contain a JSON object")
    return record, hashlib.sha256(raw).hexdigest()


def admit_receipts(baseline_record: dict, identity_record: dict) -> tuple[dict, dict]:
    now = datetime.now(timezone.utc)
    if not isinstance(baseline_record, dict) or not isinstance(identity_record, dict):
        raise ValueError("Identity and baseline receipts must be JSON objects")
    if (baseline_record.get("status") != "baseline_observed"
            or baseline_record.get("action") != "inspect"):
        raise ValueError("An original-driver inspect receipt is required")
    _fresh(baseline_record.get("started_utc"), now)
    baseline = baseline_record.get("metadata_before")
    if not isinstance(baseline, dict) or not baseline.get("locations"):
        raise ValueError("Original-driver receipt has no physical USB location")
    if identity_record.get("status") != "passed" or identity_record.get("updater_initialization") is not False:
        raise ValueError("A successful updater-free native identity receipt is required")
    index = native.select_target(identity_record.get("metadata"))
    if identity_record.get("selected_disk_index") != index:
        raise ValueError("Identity receipt disk association is inconsistent")
    result = identity_record.get("result")
    if not isinstance(result, dict):
        raise ValueError("Native identity result is missing")
    info, timing = result.get("model_info"), result.get("model_transaction")
    if (not isinstance(info, dict) or not isinstance(timing, dict)
            or info.get("model") != "ILCE-6400A"
            or info.get("product_code_wire_hex") != "0032858730"
            or timing.get("status") != "model_reply_received"):
        raise ValueError("The live normal-mode identity does not match ILCE-6400A")
    _fresh(timing.get("ended_utc"), now)
    return baseline, {"model": info["model"], "product_code_wire_hex": info["product_code_wire_hex"],
                      "measured_utc": timing["ended_utc"], "firmware": "1.01",
                      "firmware_provenance": "Explicit owner confirmation, not a live module-byte attestation"}


def save_fixed_stream(transport: FixedUsbTransport, output: Path, report: dict, cancel: Event) -> None:
    if shutil.disk_usage(output).free < CAPACITY + 1024 * 1024:
        raise OSError("Insufficient host space for the fixed-node capture and receipts; no data request sent")
    partial = output / "nflasha5.bin.partial"
    report["partial_capture"] = {"file": partial.name, "expected_bytes": CAPACITY}
    reader = FixedPartitionReader(transport, expected_bytes=CAPACITY)
    report["transfer"] = asdict(reader.receipt)
    journal = {"stage": "before_fixed_request", "request_hex": REQUEST.hex(),
               "expected_bytes": CAPACITY, "cleanup_verified": False,
               "recovery_if_interrupted": "Do not replay; preserve the partial file and perform owner-controlled power-cycle recovery"}
    ATTEMPT_ROOT.mkdir(parents=True, exist_ok=True)
    marker = ATTEMPT_ROOT / (report["baseline_sha256"] + ".json")
    with marker.open("x", encoding="utf-8") as stream:
        json.dump({**journal, "baseline_sha256": report["baseline_sha256"],
                   "output": str(output.relative_to(PROJECT_ROOT))}, stream, indent=2)
    report["one_shot_marker"] = str(marker.relative_to(PROJECT_ROOT))
    with (output / "before-fixed-request.json").open("x", encoding="utf-8") as stream:
        json.dump(journal, stream, indent=2)
    with partial.open("xb") as stream:
        try:
            reader.read(stream, cancel=cancel)
        finally:
            report["transfer"] = asdict(reader.receipt)
            try:
                stream.flush()
                os.fsync(stream.fileno())
                report["host_output_flushed"] = True
            except OSError as error:
                report["host_flush_error"] = type(error).__name__ + ": " + str(error)
                if reader.receipt.status == "stream_complete_cleanup_unverified":
                    raise
    digest = senser.host_digest(partial)
    if (partial.stat().st_size != CAPACITY or reader.receipt.output_bytes != CAPACITY
            or digest != reader.receipt.output_sha256
            or digest != reader.receipt.payload_sha256):
        raise ValueError("Fixed-node output size/readback hash differs from the verified stream")
    final = output / "nflasha5.bin"
    if final.exists():
        raise FileExistsError("Refusing to overwrite an existing fixed-node capture")
    os.link(partial, final)
    partial.unlink()
    report["captures"].append({"device": "/dev/nflasha5", "file": final.name, "bytes": CAPACITY,
                               "sha256": digest, "host_hash_verified": True,
                               "cleanup_verified": False})


def _session(args: argparse.Namespace, output: Path, report: dict, cancel: Event | None) -> None:
    device = modules = driver = None
    reader_cancel = cancel if cancel is not None else Event()
    old_signal = None
    signal_installed = False
    protocol_error = RuntimeError
    try:
        baseline_record, report["baseline_sha256"] = load_receipt(args.baseline)
        identity_record, report["identity_receipt_sha256"] = load_receipt(args.identity_receipt)
        baseline, report["identity"] = admit_receipts(baseline_record, identity_record)
        if (ATTEMPT_ROOT / (report["baseline_sha256"] + ".json")).exists():
            report["prior_attempt_unresolved"] = True
            raise ValueError("This baseline was already used; no replay. Owner recovery and fresh normal-mode receipts are required")
        metadata = report["metadata_before"] = senser.discover()
        senser.admit_device(metadata, baseline, "read")
        if metadata["pid"] != 0x0336:
            raise ValueError("Only the 054C:0336 service identity is supported")
        modules = senser.load_pmca()
        protocol_error = modules["generic"].GenericUsbException
        device, raw_driver = senser.connect(metadata, modules)
        transport = FixedUsbTransport(device, raw_driver, metadata, baseline, report["events"])
        driver = senser.BoundedDriver(raw_driver, report["events"])
        if cancel is None:
            old_signal = signal.signal(signal.SIGINT, lambda *_: reader_cancel.set())
            signal_installed = True
        report["start_control_attempted"] = True
        senser.control_request(driver, senser.START, protocol_error, modules["core"].USBError,
                               args.permit_control_pipe, report)
        report["start_control_acknowledged"] = not any(
            row["operation"] == "start" for row in report.get("unacknowledged_controls", []))
        driver.begin("auth")
        senser.authenticate(driver, modules["constants"], modules["crypto"])
        report["authentication_accepted"] = True
        save_fixed_stream(transport, output, report, reader_cancel)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError,
            protocol_error, KeyboardInterrupt) as error:
        report.update(status="blocked", error_type=type(error).__name__, error=str(error))
    finally:
        transfer = report.get("transfer", {})
        safe_exit = (not report.get("prior_attempt_unresolved")
                     and (not transfer.get("request_attempted") or transfer.get("wire_complete") is True))
        if driver is not None and report.get("start_control_attempted") and safe_exit:
            report["exit_control_attempted"] = True
            try:
                driver.begin("control")
                senser.control_request(driver, senser.STOP, protocol_error, modules["core"].USBError,
                                       args.permit_control_pipe, report)
                report["exit_control_acknowledged"] = not any(
                    row["operation"] == "stop" for row in report.get("unacknowledged_controls", []))
            except (OSError, ValueError, RuntimeError, protocol_error) as error:
                report["exit_control_acknowledged"] = False
                report["exit_error"] = type(error).__name__ + ": " + str(error)
        elif transfer.get("request_attempted"):
            report["exit_control_attempted"] = False
            report["exit_deferred"] = "Incomplete/unknown transfer; no invented abort or STOP; owner-controlled power cycle required"
        if device is not None and modules is not None:
            try:
                modules["util"].dispose_resources(device)
                report["host_handle_released"] = True
            except (OSError, ValueError, RuntimeError, protocol_error) as error:
                report["host_release_error"] = type(error).__name__ + ": " + str(error)
        if report.get("exit_control_attempted"):
            try:
                final = senser.wait_mode((senser.NORMAL_PID,), baseline["locations"])
                report["metadata_final"] = final
                if final.get("status") != "OK" or final.get("error") != 0:
                    raise ValueError("Normal USB identity is not healthy after exit")
                report["normal_usb_observed"] = True
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                report["metadata_final_error"] = type(error).__name__ + ": " + str(error)
        if signal_installed:
            signal.signal(signal.SIGINT, old_signal)
        report["cancel_requested"] = reader_cancel.is_set()
        if report["captures"]:
            report["status"] = (
                "fixed_partition_captured_exit_acknowledged"
                if report.get("exit_control_acknowledged") and report.get("normal_usb_observed")
                and not report.get("host_release_error")
                else "fixed_partition_captured_recovery_required"
            )


def main(argv: list[str] | None = None, *, cancel: Event | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--identity-receipt", type=Path, required=True)
    parser.add_argument("--confirm-firmware-101", action="store_true",
                        help="Confirm the owner-reported installed firmware is ILCE-6400A 1.01")
    parser.add_argument("--permit-control-pipe", action="store_true",
                        help="Record only original PMCA start/stop PIPE -9 as unacknowledged, never successful")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.confirm_firmware_101:
        parser.error("Explicit ILCE-6400A 1.01 confirmation is required; no discovery or request performed")
    output = args.output_dir.resolve()
    if not output.is_relative_to(PROJECT_ROOT / "artifacts"):
        parser.error("Acquisition receipts/captures must remain under artifacts")
    output.mkdir(parents=True, exist_ok=False)
    report = {"started_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "Supervised stock fixed-nflasha5 read only; not all firmware or all partitions",
              "status": "in_progress", "events": [], "captures": [], "cleanup_verified": False,
              "all_partitions_captured": False, "camera_file_writes": False,
              "updater_operations": False, "driver_changes": False, "shell_commands": False,
              "memory_scans": False, "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "reader_sha256": hashlib.sha256(
                  (PROJECT_ROOT / "tools" / "research" / "a6400a_fixed_reader.py").read_bytes()).hexdigest()}
    try:
        with senser.host_lock():
            _session(args, output, report, cancel)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        report.update(status="blocked", error_type=type(error).__name__, error=str(error))
    finally:
        active_error = sys.exc_info()[1]
        if active_error is not None and report["status"] == "in_progress":
            report.update(status="blocked_unhandled_exception",
                          error_type=type(active_error).__name__, error=str(active_error))
        report["recovery_required"] = (
            bool(report.get("metadata_before"))
            and (report["metadata_before"].get("pid") != senser.NORMAL_PID
                 or report.get("start_control_attempted", False))
            and not (report.get("exit_control_acknowledged") and report.get("normal_usb_observed"))
        )
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        with (output / "receipt.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "fixed_partition_captured_exit_acknowledged" else 2


if __name__ == "__main__":
    raise SystemExit(main())
