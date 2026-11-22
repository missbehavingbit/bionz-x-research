import ctypes as c
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from project_paths import PROJECT_ROOT
from tools.research.a6400a_native_readonly import (
    IOCTL_SCSI_PASS_THROUGH_DIRECT, IOCTL_STORAGE_GET_DEVICE_NUMBER,
    NativeTransport, ScsiPacket, admit_request,
)
from tools.research.a6400a_msc_lun_readonly import (
    CHUNK_BYTES, CHUNK_SECTORS, IOCTL_SCSI_GET_ADDRESS, LUN_SECTORS,
    READ_CAPACITY, ReadOnlyLunTransport, ScsiAddress, capture_lun,
    main, read_command, select_lun1,
)


class FakeLun(NativeTransport):
    def __init__(self, data, capacity=None, short_at=None):
        self.data = data
        self.capacity = capacity if capacity is not None else struct.pack(">II", LUN_SECTORS - 1, 512)
        self.short_at = short_at
        self.calls = []

    def exchange(self, command, *, data_out=None, read_size=0):
        ReadOnlyLunTransport._admit_request(command, data_out, read_size)
        self.calls.append(command)
        if command == READ_CAPACITY:
            return self.capacity
        offset = struct.unpack_from(">I", command, 2)[0] * 512
        data = self.data[offset:offset + read_size]
        return data[:-512] if offset == self.short_at else data


class LunReadOnlyTests(unittest.TestCase):
    def metadata(self):
        return {
            "usb_pair": "054c:0f0f", "service": "USBSTOR", "status": "OK",
            "config_error": 0, "camera_count": 1,
            "disks": [
                {"disk_index": 7, "lun": 0, "model": "Sony DSC USB Device",
                 "parent_identity_verified": True},
                {"disk_index": 8, "lun": 1, "model": "Sony DSC USB Device",
                 "parent_identity_verified": True},
            ],
        }

    def test_lun1_selection_cannot_alias_the_sd_or_use_an_unverified_parent(self):
        self.assertEqual(select_lun1(self.metadata()), 8)
        for field, value in (("disk_index", 7), ("parent_identity_verified", False),
                             ("lun", 2), ("disk_index", True)):
            metadata = self.metadata()
            metadata["disks"][1][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                select_lun1(metadata)
        metadata = self.metadata()
        metadata["disks"].append(metadata["disks"][1].copy())
        with self.assertRaises(ValueError):
            select_lun1(metadata)

    def test_raw_commands_remain_forbidden_in_the_existing_identity_transport(self):
        for command, size in ((READ_CAPACITY, 8), (read_command(0), CHUNK_BYTES)):
            with self.subTest(command=command), self.assertRaises(ValueError):
                admit_request(command, None, size)

    def test_new_allowlist_refuses_writes_flags_wrong_extents_and_unaligned_reads(self):
        changed = bytearray(read_command(0))
        changed[1] = 8
        for command, data_out, size in (
                (read_command(0), b"", CHUNK_BYTES),
                (b"\x2a" + bytes(9), None, CHUNK_BYTES),
                (bytes(changed), None, CHUNK_BYTES),
                (struct.pack(">BBIBHB", 0x28, 0, 1, 0, CHUNK_SECTORS, 0), None, CHUNK_BYTES),
                (struct.pack(">BBIBHB", 0x28, 0, LUN_SECTORS, 0, CHUNK_SECTORS, 0), None, CHUNK_BYTES),
                (read_command(0), None, CHUNK_BYTES - 512),
                (READ_CAPACITY, None, 512)):
            with self.subTest(command=command, size=size), self.assertRaises(ValueError):
                ReadOnlyLunTransport._admit_request(command, data_out, size)

    def fixture_directory(self):
        root = PROJECT_ROOT / "artifacts" / "tests"
        root.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(prefix="msc-lun-readonly-", dir=root)

    def payload(self):
        return b"".join(bytes([index]) * CHUNK_BYTES
                        for index in range(LUN_SECTORS // CHUNK_SECTORS))

    def test_exact_capture_preserves_every_chunk_and_independently_verifies_disk_hash(self):
        data = self.payload()
        transport = FakeLun(data)
        with self.fixture_directory() as name:
            directory = Path(name)
            result = capture_lun(transport, directory)
            self.assertEqual((directory / "msc-lun1.bin").read_bytes(), data)
            self.assertFalse((directory / "msc-lun1.bin.partial").exists())
            self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
            self.assertTrue(result["host_hash_verified"])
            self.assertFalse(result["backing_partition_attested"])
        self.assertEqual(transport.calls, [READ_CAPACITY] + [
            read_command(lba) for lba in range(0, LUN_SECTORS, CHUNK_SECTORS)])

    def test_unknown_capacity_never_starts_a_sector_read(self):
        for capacity in (struct.pack(">II", LUN_SECTORS, 512),
                         struct.pack(">II", LUN_SECTORS - 1, 4096), bytes(4)):
            transport = FakeLun(self.payload(), capacity=capacity)
            with self.subTest(capacity=capacity), self.fixture_directory() as name:
                directory = Path(name)
                with self.assertRaises(ValueError):
                    capture_lun(transport, directory)
                self.assertEqual(transport.calls, [READ_CAPACITY])
                self.assertEqual(list(directory.iterdir()), [])

    def test_short_read_retains_only_completed_bytes_without_retry_or_padding(self):
        data = self.payload()
        transport = FakeLun(data, short_at=3 * CHUNK_BYTES)
        with self.fixture_directory() as name:
            directory = Path(name)
            with self.assertRaisesRegex(ValueError, "Short LUN read"):
                capture_lun(transport, directory)
            self.assertEqual((directory / "msc-lun1.bin.partial").read_bytes(), data[:3 * CHUNK_BYTES])
            self.assertFalse((directory / "msc-lun1.bin").exists())
        self.assertEqual(len(transport.calls), 5)

    def test_existing_output_is_not_overwritten_or_followed_by_a_request(self):
        transport = FakeLun(self.payload())
        with self.fixture_directory() as name:
            directory = Path(name)
            (directory / "msc-lun1.bin").write_bytes(b"preserved")
            with self.assertRaises(FileExistsError):
                capture_lun(transport, directory)
            self.assertEqual((directory / "msc-lun1.bin").read_bytes(), b"preserved")
            self.assertEqual(transport.calls, [])

    def test_default_invocation_creates_only_metadata_without_opening_a_device(self):
        with self.fixture_directory() as name:
            output = Path(name) / "metadata"
            with mock.patch("tools.research.a6400a_msc_lun_readonly.discover",
                            return_value=self.metadata()), \
                    mock.patch("tools.research.a6400a_msc_lun_readonly.NativeTransport") as identity, \
                    mock.patch("tools.research.a6400a_msc_lun_readonly.ReadOnlyLunTransport") as reader, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--output-dir", str(output)]), 0)
            identity.assert_not_called()
            reader.assert_not_called()
            receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "metadata_only")
            self.assertEqual(receipt["operations"], [])

    def transport_with_kernel(self, lun):
        transport = object.__new__(ReadOnlyLunTransport)
        transport.disk_index = 8
        transport.handle = None
        transport.operations = []
        transport.scsi_address = None
        transport.api = mock.Mock()
        transport.api.CreateFileW.return_value = 123
        transport.api.CloseHandle.return_value = 1

        def ioctl(handle, code, input_buffer, input_size, output_buffer, output_size, returned, overlap):
            c.cast(returned, c.POINTER(c.c_uint32)).contents.value = output_size
            if code == IOCTL_STORAGE_GET_DEVICE_NUMBER:
                number = c.cast(output_buffer, c.POINTER(c.c_uint32 * 3)).contents
                number[1] = 8
            elif code == IOCTL_SCSI_GET_ADDRESS:
                address = c.cast(output_buffer, c.POINTER(ScsiAddress)).contents
                address.Length = c.sizeof(ScsiAddress)
                address.PathId, address.TargetId, address.Lun = 2, 3, lun
            elif code == IOCTL_SCSI_PASS_THROUGH_DIRECT:
                packet = c.cast(input_buffer, c.POINTER(ScsiPacket)).contents
                self.assertEqual((packet.sptd.PathId, packet.sptd.TargetId, packet.sptd.Lun), (2, 3, 1))
                self.assertEqual(packet.sptd.DataIn, 1)
                c.memmove(packet.sptd.DataBuffer, struct.pack(">II", LUN_SECTORS - 1, 512), 8)
            else:
                self.fail(f"Unexpected device ioctl {code:#x}")
            return 1

        transport.api.DeviceIoControl.side_effect = ioctl
        return transport

    def test_sptd_uses_the_address_observed_on_the_same_lun1_handle(self):
        transport = self.transport_with_kernel(1)
        with transport:
            self.assertEqual(transport.exchange(READ_CAPACITY, read_size=8),
                             struct.pack(">II", LUN_SECTORS - 1, 512))
        transport.api.CloseHandle.assert_called_once_with(123)

    def test_wrong_scsi_lun_closes_the_handle_before_any_sector_command(self):
        transport = self.transport_with_kernel(0)
        with self.assertRaisesRegex(ValueError, "verified SCSI LUN 1"):
            transport.__enter__()
        transport.api.CloseHandle.assert_called_once_with(123)
        self.assertEqual(transport.operations, [])


if __name__ == "__main__":
    unittest.main()
