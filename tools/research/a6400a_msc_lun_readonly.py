"""Opt-in, bounded capture of the a6400A's existing 2 MiB Mass Storage LUN 1.

This captures exported LUN bytes, not an attested arbitrary nflasha partition.
No mode change, upload, storage write, dismount or device reset is available.
The existing inquiry/model-info transport's command allowlist is unchanged.
"""
from __future__ import annotations

import argparse
import ctypes as c
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import time

from project_paths import PROJECT_ROOT
from tools.camera_debugger import host_lock
from tools.research.a6400a_native_readonly import (
    IOCTL_SCSI_PASS_THROUGH_DIRECT,
    IOCTL_STORAGE_GET_DEVICE_NUMBER,
    NativeTransport,
    ScsiPacket,
    discover,
    probe_identity,
    select_target,
)
from tools.research.a6400a_senser_acquire import host_digest

IOCTL_SCSI_GET_ADDRESS = 0x41018
READ_CAPACITY = b"\x25" + bytes(9)
SECTOR_BYTES = 512
LUN_BYTES = 2 * 1024 * 1024
CHUNK_SECTORS = 64
CHUNK_BYTES = CHUNK_SECTORS * SECTOR_BYTES
LUN_SECTORS = LUN_BYTES // SECTOR_BYTES


def select_lun1(metadata: dict) -> int:
    select_target(metadata)
    candidates = [
        row for row in metadata["disks"]
        if row.get("lun") == 1 and row.get("parent_identity_verified") is True
        and row.get("model") == "Sony DSC USB Device"
    ]
    if len(candidates) != 1:
        raise ValueError("Expected one parent-verified camera LUN 1")
    index = candidates[0].get("disk_index")
    if type(index) is not int or not 0 <= index <= 255:
        raise ValueError("Invalid parent-verified LUN 1 disk index")
    if index == select_target(metadata):
        raise ValueError("LUN 1 must not alias the SD/model-query LUN 0")
    return index


def read_command(lba: int) -> bytes:
    if type(lba) is not int or lba % CHUNK_SECTORS or not 0 <= lba <= LUN_SECTORS - CHUNK_SECTORS:
        raise ValueError("READ(10) lies outside the fixed LUN 1 acquisition profile")
    return struct.pack(">BBIBHB", 0x28, 0, lba, 0, CHUNK_SECTORS, 0)


class ScsiAddress(c.Structure):
    _fields_ = [
        ("Length", c.c_uint32), ("PortNumber", c.c_uint8),
        ("PathId", c.c_uint8), ("TargetId", c.c_uint8), ("Lun", c.c_uint8),
    ]


class ReadOnlyLunTransport(NativeTransport):
    def __init__(self, disk_index: int, operations: list):
        super().__init__(disk_index, operations)
        self.scsi_address: tuple[int, int, int] | None = None

    @staticmethod
    def _admit_request(command: bytes, data_out: bytes | None, read_size: int) -> str:
        if data_out is not None:
            raise ValueError("The LUN capture transport refuses every OUT request")
        if command == READ_CAPACITY and read_size == 8:
            return "lun1_read_capacity"
        if len(command) == 10 and command[0] == 0x28 and read_size == CHUNK_BYTES:
            lba = struct.unpack_from(">I", command, 2)[0]
            if command == read_command(lba):
                return "lun1_read_10"
        raise ValueError("SCSI command is outside the fixed read-only LUN 1 allowlist")

    def __enter__(self):
        super().__enter__()
        try:
            address = ScsiAddress()
            returned = self._ioctl(
                IOCTL_SCSI_GET_ADDRESS, None, 0, c.byref(address), c.sizeof(address))
            if returned != c.sizeof(address) or address.Length != c.sizeof(address) or address.Lun != 1:
                raise ValueError("Opened storage object is not a verified SCSI LUN 1")
            self.scsi_address = (address.PathId, address.TargetId, address.Lun)
        except (OSError, ValueError, RuntimeError):
            super().__exit__(None, None, None)
            raise
        return self

    def _ioctl(self, code, input_buffer, input_size, output_buffer, output_size):
        if code == IOCTL_SCSI_PASS_THROUGH_DIRECT:
            if self.scsi_address is None:
                raise RuntimeError("SCSI address has not been observed on the admitted handle")
            packet = c.cast(input_buffer, c.POINTER(ScsiPacket)).contents
            packet.sptd.PathId, packet.sptd.TargetId, packet.sptd.Lun = self.scsi_address
        elif code not in (IOCTL_STORAGE_GET_DEVICE_NUMBER, IOCTL_SCSI_GET_ADDRESS):
            raise ValueError("Device ioctl is outside the read-only LUN acquisition profile")
        return super()._ioctl(code, input_buffer, input_size, output_buffer, output_size)


def capture_lun(transport: NativeTransport, directory: Path) -> dict:
    partial = directory / "msc-lun1.bin.partial"
    final = directory / "msc-lun1.bin"
    if final.exists() or partial.exists():
        raise FileExistsError("LUN capture output already exists; no request sent")
    capacity = transport.exchange(READ_CAPACITY, read_size=8)
    if len(capacity) != 8:
        raise ValueError("Short READ CAPACITY response")
    last_lba, sector_bytes = struct.unpack(">II", capacity)
    if (last_lba, sector_bytes) != (LUN_SECTORS - 1, SECTOR_BYTES):
        raise ValueError("Observed capacity is not the fixed 2 MiB/512-byte LUN profile")
    started = time.monotonic()
    digest = hashlib.sha256()
    with partial.open("xb") as stream:
        for lba in range(0, LUN_SECTORS, CHUNK_SECTORS):
            if time.monotonic() - started >= 30:
                raise TimeoutError("LUN capture budget exceeded; partial output retained")
            data = transport.exchange(read_command(lba), read_size=CHUNK_BYTES)
            if len(data) != CHUNK_BYTES:
                raise ValueError("Short LUN read; no retry or padding")
            if stream.write(data) != len(data):
                raise OSError("Short host-file write; partial output retained")
            digest.update(data)
        stream.flush()
        os.fsync(stream.fileno())
    elapsed = time.monotonic() - started
    if elapsed >= 30:
        raise TimeoutError("LUN capture completed outside its budget; output remains partial")
    if partial.stat().st_size != LUN_BYTES or host_digest(partial) != digest.hexdigest():
        raise ValueError("Closed-file size/hash differs from the complete streaming capture")
    partial.rename(final)
    return {
        "file": final.name, "bytes": LUN_BYTES, "sha256": digest.hexdigest(),
        "host_hash_verified": True, "last_lba": last_lba, "sector_bytes": sector_bytes,
        "read_commands": LUN_SECTORS // CHUNK_SECTORS, "elapsed_seconds": elapsed,
        "backing_partition_attested": False,
        "backing_partition_candidate": "/dev/nflasha23",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-lun1", action="store_true",
                        help="Opt in to READ CAPACITY and exactly 2 MiB of LUN 1 reads")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    out = args.output_dir.resolve()
    if not out.is_relative_to(PROJECT_ROOT / "artifacts"):
        parser.error("Receipts must remain under artifacts")
    out.mkdir(parents=True, exist_ok=False)
    report = {
        "scope": "Existing Mass Storage LUN 1 only; not arbitrary flash or complete firmware",
        "started_utc": datetime.now(timezone.utc).isoformat(), "status": "in_progress",
        "operations": [], "identity_operations": [], "camera_file_writes": False,
        "storage_writes": False, "mode_changes": False, "driver_changes": False,
        "uploads": False, "device_resets": False, "lun0_sector_reads": False,
        "nflasha5_requested": False, "full_firmware_captured": False,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "transport_source_sha256": hashlib.sha256(
            (PROJECT_ROOT / "tools" / "research" / "a6400a_native_readonly.py").read_bytes()
        ).hexdigest(),
    }
    try:
        with host_lock():
            metadata = report["metadata_before"] = discover()
            lun0, lun1 = select_target(metadata), select_lun1(metadata)
            report["lun1_disk_index"] = lun1
            if args.read_lun1:
                with NativeTransport(lun0, report["identity_operations"]) as identity:
                    report["identity"] = {}
                    probe_identity(identity, model_info=True, result=report["identity"])
                if report["identity"]["model_info"]["product_code_wire_hex"] != "0032858730":
                    raise ValueError("Unexpected native camera product code; no sector read sent")
                current = discover()
                if current != metadata:
                    raise ValueError("Camera association changed after identity verification")
                with ReadOnlyLunTransport(lun1, report["operations"]) as transport:
                    if transport.scsi_address is None:
                        raise RuntimeError("LUN 1 SCSI address is unverified")
                    report["scsi_address"] = list(transport.scsi_address)
                    report["capture"] = capture_lun(transport, out)
                report["handles_released"] = True
                report["metadata_after"] = discover()
                if report["metadata_after"] != metadata:
                    raise ValueError("Post-capture camera association/driver/status changed")
                report["status"] = "lun1_captured_not_complete_firmware"
            else:
                report["status"] = "metadata_only"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report.update(status="blocked", error_type=type(error).__name__, error=str(error),
                      winerror=getattr(error, "winerror", None))
    finally:
        partial = out / "msc-lun1.bin.partial"
        if partial.exists():
            report["partial_bytes"] = partial.stat().st_size
            report["partial_sha256"] = host_digest(partial)
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        with (out / "receipt.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] in ("metadata_only", "lun1_captured_not_complete_firmware") else 2


if __name__ == "__main__":
    raise SystemExit(main())
