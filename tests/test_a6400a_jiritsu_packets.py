import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from project_paths import PROJECT_ROOT
from tools.research.a6400a_dmm import (
    DMM_ADDRESS, DMM_COMPLETION, DMM_HANDLE, DMM_UNIT_TYPE, MAX_DMM_BYTES,
    DmmBufferFixture, DmmMetadata, kernel_lifetime_controls,
)
from tools.research.a6400a_jiritsu_packets import (
    BASE, HEADER, INLINE_SIZE, PHYSICAL_SEND, RESPONSE_FUNCTIONS,
    DmmReplay, LibraryImage, OfflineReplay, main,
    request_packet, unpack_request,
)


class JiritsuPacketTests(unittest.TestCase):
    def test_exact_little_endian_request_and_command_flags(self):
        packet = request_packet(0x11, b"\x01\xff\x00", sequence=0x1234)
        self.assertEqual(packet.hex(), "0700000040003412000000000110114001ff00")
        self.assertEqual(unpack_request(packet), (0x11, 0x1234, b"\x01\xff\x00"))

    def test_inline_profile_boundary_and_empty_arguments(self):
        for payload in (b"", b"x" * INLINE_SIZE):
            packet = request_packet(0xF, payload, sequence=0xFFFF)
            self.assertEqual(unpack_request(packet), (0xF, 0xFFFF, payload))
        with self.assertRaisesRegex(ValueError, "292-byte"):
            request_packet(0xF, b"x" * (INLINE_SIZE + 1))

    def test_invalid_commands_and_sequences_are_not_truncated(self):
        for command in (0, 0x13, 0x400F, -1, True):
            with self.subTest(command=command), self.assertRaises(ValueError):
                request_packet(command, b"")
        for sequence in (-1, 0x10000, True):
            with self.subTest(sequence=sequence), self.assertRaises(ValueError):
                request_packet(0xF, b"", sequence=sequence)

    def test_truncation_length_and_other_protocol_namespaces_are_rejected(self):
        valid = request_packet(0xF, b"test")
        invalid = [valid[:11], valid[:15], valid[:-1], valid + b"\0"]
        for offset, value in ((4, b"\x10\x00"), (8, b"\x03"), (11, b"\x01"),
                              (12, b"\x01\x06"), (14, b"\x0f\x00"), (14, b"\xff\x40")):
            packet = bytearray(valid)
            packet[offset:offset + len(value)] = value
            invalid.append(bytes(packet))
        for packet in invalid:
            with self.subTest(packet=packet.hex()), self.assertRaises(ValueError):
                unpack_request(packet)

    def test_declared_oversized_body_is_not_accepted_as_an_inline_packet(self):
        body = struct.pack("<HH", 0x1001, 0x400F) + b"x" * (INLINE_SIZE + 1)
        packet = HEADER.pack(len(body), 0x40, 1, 0, 0, 0, 0) + body
        with self.assertRaisesRegex(ValueError, "292-byte"):
            unpack_request(packet)


class OfflineAdmissionTests(unittest.TestCase):
    def test_changed_library_is_rejected_before_elf_parsing_or_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "changed.so"
            source.write_bytes(b"not the analyzed library")
            with patch("tools.research.a6400a_jiritsu_packets.ELFFile") as parse:
                with self.assertRaisesRegex(ValueError, "identity mismatch"):
                    LibraryImage(source, "0" * 64)
                parse.assert_not_called()

    def test_usb_option_is_rejected_before_input_or_output_access(self):
        for policy in ([], ["--dmm-fixtures"]):
            with self.subTest(policy=policy), patch(
                    "tools.research.a6400a_jiritsu_packets.digest") as read_input:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    main([*policy, "--usb", "--report-dir", "unused"])
                self.assertEqual(caught.exception.code, 2)
                read_input.assert_not_called()

    def test_missing_input_leaves_failed_receipt_and_no_packet_fixtures(self):
        root = PROJECT_ROOT / "artifacts" / "tests"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            output = Path(directory) / "receipt"
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(["--updater", str(Path(directory) / "missing.exe"),
                             "--report-dir", str(output)])
            self.assertEqual(code, 2)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "failed")
            self.assertFalse(manifest["usb_enabled"])
            self.assertIn("FileNotFoundError", manifest["error"])
            self.assertFalse((output / "fixtures.json").exists())


def replay_fixture(code: bytes, *, plt=None, functions=None):
    class Segment(dict):
        def data(self):
            return bytes(0x100) + code + bytes(0x400 - 0x100 - len(code))

    class ELF:
        def iter_segments(self):
            return iter([Segment(p_type="PT_LOAD", p_vaddr=0, p_memsz=0x1000, p_flags=5)])

        def get_section_by_name(self, name):
            return None

    image = SimpleNamespace(elf=ELF(), plt=plt or {}, functions=functions or {}, values={})
    return OfflineReplay(image, windows=((0x100, 0x100 + len(code)),))


class ClosedReplayTests(unittest.TestCase):
    def test_handler_plt_binding_stops_before_the_declared_original_entry(self):
        run = replay_fixture(bytes.fromhex("7ee0"), plt={0x200: "cmd_handler"},
                             functions={"cmd_handler": (0x301, 2)})
        run.entries[BASE + 0x300] = "before_handler:cmd_handler"
        report = run.call(0x101, (7,), stop="before_handler:cmd_handler")
        self.assertEqual(report["pc"], BASE + 0x300)
        self.assertEqual(report["args_or_return"][0], 7)

    def test_an_unlisted_import_cannot_receive_an_implicit_success(self):
        run = replay_fixture(bytes.fromhex("7ee0"), plt={0x200: "file_open"})
        with self.assertRaisesRegex(RuntimeError, "unsupported_import:file_open"):
            run.call(0x101, (1, 2, 3))

    def test_physical_sender_stays_disabled_without_the_dmm_fixture_policy(self):
        run = replay_fixture(bytes.fromhex("7ee0"), plt={0x200: PHYSICAL_SEND})
        with self.assertRaisesRegex(RuntimeError, f"unsupported_import:{PHYSICAL_SEND}"):
            run.call(0x101, (DMM_ADDRESS, 1))

    def test_limits_and_unmapped_memory_are_not_completion(self):
        for code, error in (("fee7", "instruction_or_time_limit"),
                            ("00200068", "memory=.*'address': 0")):
            with self.subTest(code=code), self.assertRaisesRegex(RuntimeError, error):
                replay_fixture(bytes.fromhex(code)).call(0x101, ())


def dmm_completion(**changes):
    fields = dict(operation=0x8002, status=0, handle=DMM_HANDLE, token=0,
                  count=1, detail=0, unit_type=DMM_UNIT_TYPE,
                  address=DMM_ADDRESS, capacity=8)
    fields.update(changes)
    return DMM_COMPLETION.pack(*fields.values())


class DmmFixtureTests(unittest.TestCase):
    def decode(self, completion, raw_size=8):
        return DmmMetadata.decode(completion, handle=DMM_HANDLE,
                                  unit_type=DMM_UNIT_TYPE, raw_size=raw_size)

    def test_metadata_requires_exact_successful_single_extent_identity(self):
        self.assertEqual(self.decode(dmm_completion()).capacity, 8)
        invalid = [dmm_completion()[:-1], dmm_completion() + b"\0"]
        for field, value in (("operation", 0x8004), ("status", -1), ("detail", -3),
                             ("token", 1), ("handle", DMM_HANDLE ^ 0x400),
                             ("unit_type", DMM_UNIT_TYPE + 1), ("count", 0), ("count", 2)):
            invalid.append(dmm_completion(**{field: value}))
        for completion in invalid:
            with self.subTest(completion=completion.hex()), self.assertRaises(ValueError):
                self.decode(completion)

    def test_capacity_raw_length_and_arm32_end_are_independent_bounds(self):
        for raw_size in (0, 8):
            self.decode(dmm_completion(), raw_size=raw_size)
        self.decode(dmm_completion(address=0xFFFFFFF8), raw_size=7)
        self.decode(dmm_completion(capacity=MAX_DMM_BYTES), raw_size=MAX_DMM_BYTES)
        for completion, raw_size in (
                (dmm_completion(address=0), 8), (dmm_completion(address=0xFFFFFFFC), 8),
                (dmm_completion(address=0xFFFFFFF8), 8),
                (dmm_completion(capacity=0), 0),
                (dmm_completion(capacity=MAX_DMM_BYTES + 1), 8),
                (dmm_completion(), 9), (dmm_completion(), -1), (dmm_completion(), True)):
            with self.subTest(raw_size=raw_size), self.assertRaises(ValueError):
                self.decode(completion, raw_size=raw_size)

    def test_raw_header_cannot_substitute_another_buffer_or_larger_length(self):
        buffer = DmmBufferFixture(dmm_completion(), b"payload!", raw_size=7)
        header = struct.pack("<BBHBBHII", 8, 1, 0x1234, 1, 0,
                             DMM_HANDLE, DMM_UNIT_TYPE, 7)
        buffer.bind_reply(header)
        invalid = [header[:-1], header + b"\0"]
        for offset, value in ((0, b"\x02"), (1, b"\x81"), (4, b"\x02"), (5, b"\x01"),
                              (6, b"\0\0"), (8, b"\0\0\0\0"), (12, b"\x08\0\0\0")):
            bad = bytearray(header)
            bad[offset:offset + len(value)] = value
            invalid.append(bytes(bad))
        for bad in invalid:
            with self.subTest(header=bad.hex()), self.assertRaises(ValueError):
                buffer.bind_reply(bad)
        with self.assertRaisesRegex(ValueError, "exactly cover"):
            DmmBufferFixture(dmm_completion(), b"short", raw_size=5)

    def test_borrow_survives_consumer_release_but_not_owner_retirement(self):
        buffer = DmmBufferFixture(dmm_completion(), b"payload!", raw_size=7)
        with self.assertRaisesRegex(ValueError, "lifetime"):
            buffer.read(DMM_ADDRESS, 1)
        buffer.acquire()
        with self.assertRaisesRegex(ValueError, "lifetime"):
            buffer.read(DMM_ADDRESS, 1)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            buffer.acquire()
        buffer.release_consumer()
        self.assertEqual(buffer.read(DMM_ADDRESS, 7), b"payload")
        self.assertEqual(buffer.read(DMM_ADDRESS + 7, 0), b"")
        for address, size in ((DMM_ADDRESS - 1, 1), (DMM_ADDRESS, 8),
                              (DMM_ADDRESS + 7, 1), (DMM_ADDRESS, -1),
                              ("bad", 1), (DMM_ADDRESS, True)):
            with self.subTest(address=address, size=size), self.assertRaises(ValueError):
                buffer.read(address, size)
        with self.assertRaisesRegex(ValueError, "unbalanced"):
            buffer.release_consumer()
        buffer.retire()
        with self.assertRaisesRegex(ValueError, "lifetime"):
            buffer.read(DMM_ADDRESS, 0)
        with self.assertRaisesRegex(ValueError, "unbalanced"):
            buffer.retire()
        with self.assertRaisesRegex(ValueError, "stale"):
            buffer.acquire()

    def test_short_sink_cannot_hide_a_read_beyond_the_raw_reply(self):
        buffer = DmmBufferFixture(dmm_completion(), b"payload!", raw_size=7)
        buffer.acquire()
        buffer.release_consumer()
        image = replay_fixture(
            bytes.fromhex("7ee0"), plt={0x200: PHYSICAL_SEND},
            functions={name: (0x101, 2) for name in (
                *RESPONSE_FUNCTIONS, "_ZN10MemoryData7ReleaseEv")}).image
        run = DmmReplay(image, buffer)
        run.sink = bytearray()
        run.sink_chunk = 1
        with self.assertRaisesRegex(RuntimeError, "unsupported_dmm_fixture_read") as caught:
            run.call(0x101, (DMM_ADDRESS, 8))
        self.assertIn(f"'pc': {BASE + 0x200}", str(caught.exception))
        self.assertIn(f"'args_or_return': [{DMM_ADDRESS}, 8,", str(caught.exception))
        self.assertEqual(run.sink, b"")

    def test_changed_kernel_cannot_execute_the_lifetime_control(self):
        root = PROJECT_ROOT / "artifacts" / "tests"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            source = Path(directory) / "changed-dmm.ko"
            source.write_bytes(b"not the analyzed DMM module")
            with patch("tools.research.dmm_config_trace.DmmConfigProbe") as probe:
                with self.assertRaisesRegex(ValueError, "identity mismatch"):
                    kernel_lifetime_controls(source)
                probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
