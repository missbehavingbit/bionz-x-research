import hashlib
import struct
import unittest

from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R3

from tools.research.a6400a_service_contracts import DATA, IMAGE, RETURN, Library, ThumbWindow
from tools.research.a6400a_service_contracts import HASHES, PACKAGE, SYSTEM, adjustment_gate
from tools.research.a6400a_reader_controls import (
    JIRITSU_SHA, block_capacity, jiritsu_read, nflasha5_lifecycle, scratch_read,
)
from tools.research.a6400a_userspace_prefix import INPUTS, run_prefix


def fixture_library(code: bytes, *, machine: int = 40, thumb: bool = True) -> bytes:
    strings = b"\0fixture\0"
    section_names = b"\0.text\0.dynstr\0.dynsym\0.shstrtab\0"
    text_offset = 0x100
    string_offset = text_offset + len(code)
    symbol_offset = (string_offset + len(strings) + 3) & ~3
    symbols = bytes(16) + struct.pack(
        "<IIIBBH", 1, 0x1000 | int(thumb), len(code), 0x12, 0, 1)
    names_offset = symbol_offset + len(symbols)
    section_offset = (names_offset + len(section_names) + 3) & ~3
    raw = bytearray(section_offset + 5 * 40)
    header = struct.pack(
        "<16sHHIIIIIHHHHHH", b"\x7fELF\x01\x01\x01" + bytes(9),
        3, machine, 1, 0x1000 | int(thumb), 52, section_offset, 0x05000000,
        52, 32, 1, 40, 5, 4)
    raw[:len(header)] = header
    raw[52:84] = struct.pack("<8I", 1, text_offset, 0x1000, 0x1000,
                             len(code), 0x1000, 5, 0x100)
    raw[text_offset:text_offset + len(code)] = code
    raw[string_offset:string_offset + len(strings)] = strings
    raw[symbol_offset:symbol_offset + len(symbols)] = symbols
    raw[names_offset:names_offset + len(section_names)] = section_names
    sections = (
        (1, 1, 6, 0x1000, text_offset, len(code), 0, 0, 2, 0),
        (7, 3, 0, 0, string_offset, len(strings), 0, 0, 1, 0),
        (15, 11, 0, 0, symbol_offset, len(symbols), 2, 1, 4, 16),
        (23, 3, 0, 0, names_offset, len(section_names), 0, 0, 1, 0),
    )
    for index, row in enumerate(sections, 1):
        struct.pack_into("<10I", raw, section_offset + index * 40, *row)
    return bytes(raw)


class ServiceContractWindowTests(unittest.TestCase):
    def window(self, code: bytes) -> ThumbWindow:
        raw = fixture_library(code)
        return ThumbWindow(Library(raw, hashlib.sha256(raw).hexdigest()), "fixture")

    def test_identity_change_and_other_cpu_are_rejected_before_execution(self):
        raw = fixture_library(b"\x70\x47")
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            Library(raw, hashlib.sha256(raw + b"changed").hexdigest())
        raw = fixture_library(b"\x70\x47", machine=3)
        with self.assertRaisesRegex(ValueError, "ARM shared library"):
            Library(raw, hashlib.sha256(raw).hexdigest())

    def test_arm_entry_cannot_be_treated_as_thumb(self):
        raw = fixture_library(b"\x70\x47", thumb=False)
        library = Library(raw, hashlib.sha256(raw).hexdigest())
        with self.assertRaisesRegex(ValueError, "unsupported original Thumb"):
            ThumbWindow(library, "fixture")

    def test_original_return_is_distinct_from_a_declared_pre_io_stop(self):
        window = self.window(b"\x08\x60\x70\x47")
        window.cpu.mem_write(DATA, b"\xA5" * 4)
        result = window.run({UC_ARM_REG_R0: 7, UC_ARM_REG_R1: DATA},
                            stop_before=IMAGE + 0x1000)
        self.assertEqual(result["stop"], "declared_boundary")
        self.assertEqual(bytes(window.cpu.mem_read(DATA, 4)), b"\xA5" * 4)
        result = window.run({UC_ARM_REG_R0: 7, UC_ARM_REG_R1: DATA})
        self.assertEqual(result["stop"], "bounded_return")
        self.assertEqual(window.read32(DATA), 7)
        self.assertEqual(result["non_stack_writes"], [{"address": DATA, "bytes": 4, "value": 7}])

    def test_branch_outside_the_allowlist_stops_before_execution(self):
        window = self.window(b"\x98\x47\x70\x47")
        with self.assertRaisesRegex(RuntimeError, "unsupported_pc"):
            window.run({UC_ARM_REG_R3: RETURN + 0x101})

    def test_instruction_limit_is_a_failure_not_a_successful_control(self):
        window = self.window(b"\xFE\xE7")
        window.limit = 8
        with self.assertRaisesRegex(RuntimeError, "declared stop"):
            window.run({})

    def test_prefix_rejects_filesystem_policy_before_loading_any_original_image(self):
        with self.assertRaisesRegex(ValueError, "offline profile"):
            run_prefix("files")


@unittest.skipUnless(
    all((PACKAGE / SYSTEM / name).is_file() for name in ("libsencore.so", "libJiritsu.so")),
    "Private a6400A consumer inputs unavailable",
)
class OriginalReaderConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = Library((PACKAGE / SYSTEM / "libsencore.so").read_bytes(), HASHES["libsencore.so"])
        cls.jiritsu = Library((PACKAGE / SYSTEM / "libJiritsu.so").read_bytes(), JIRITSU_SHA)

    def test_scratch_read_exact_end_and_overrun_are_distinct(self):
        for offset, size, accepted in ((0, 256, True), (255, 1, True),
                                       (255, 2, False), (256, 0, False),
                                       (0xFFFFFFFF, 4, False)):
            with self.subTest(offset=offset, size=size):
                result = scratch_read(self.core, offset, size)
                self.assertEqual(result["accepted"], accepted)
                self.assertEqual(result["payload_bytes"], size if accepted else 0)
                self.assertTrue(result["scratch_source_unchanged"])

    def test_zero_stat_size_does_not_become_a_successful_nonempty_dump(self):
        result = jiritsu_read(self.jiritsu, size=0)
        reads = [row for row in result["operations"] if row["operation"] == "read_fixture"]
        self.assertEqual(result["response_bytes_including_status"], 4)
        self.assertEqual(reads[0]["requested"], 0)
        self.assertEqual(reads[0]["returned"], 0)

    def test_short_file_read_reports_error_and_still_closes(self):
        result = jiritsu_read(self.jiritsu, size=12, read_result=11)
        self.assertEqual(result["status"], 0x1000000A)
        self.assertEqual(result["response_bytes_including_status"], 4)
        self.assertEqual([row["operation"] for row in result["operations"]][-2:],
                         ["fsync_fixture", "close_fixture"])

    def test_internal_file_mode_reaches_mount_before_any_file_read(self):
        result = jiritsu_read(self.jiritsu, mode=0)
        self.assertEqual(result["status"], 0x10000004)
        self.assertEqual([row["operation"] for row in result["operations"]],
                         ["mount_boundary_fixture"])

    def test_block_capacity_uses_sector_ioctl_not_zero_stat_size(self):
        success = block_capacity(self.core, 122880)
        failure = block_capacity(self.core, 122880, ioctl_result=-1)
        self.assertEqual(success["bytes"], 62_914_560)
        self.assertEqual(success["operations"][0]["ioctl"], 0x1260)
        self.assertEqual(failure["bytes"], 0)

    def test_fixed_partition_explicit_release_closes_only_its_owned_resources(self):
        result = nflasha5_lifecycle(self.core)
        self.assertEqual(result["response_bytes"], 62_914_560)
        self.assertEqual([row["operation"] for row in result["operations"]][-3:],
                         ["munmap_fixture", "flock_fixture", "close_fixture"])
        self.assertEqual((result["descriptor_after"], result["mapping_pointer_after"],
                          result["mapping_size_after"]), (0xFFFFFFFF, 0, 0))
        busy = nflasha5_lifecycle(self.core, busy=True)
        self.assertEqual(busy["status"], 0xA0)
        self.assertEqual(busy["operations"], [])
        self.assertEqual(busy["descriptor_after"], 42)

    def test_failed_mapping_keeps_its_original_residue_visible(self):
        result = nflasha5_lifecycle(self.core, map_result=-1)
        self.assertEqual(result["status"], 0x88)
        self.assertEqual(result["descriptor_after"], 0xFFFFFFFF)
        self.assertEqual(result["mapping_pointer_after"], 0xFFFFFFFF)
        self.assertGreater(result["mapping_size_after"], 0)
        self.assertIsNone(result["release_execution"])

    def test_missing_exact_command_still_uses_the_level_three_block_fallback(self):
        low = adjustment_gate(self.core, 0x601, 0x32, 1, b"")
        high = adjustment_gate(self.core, 0x601, 0x32, 3, b"")
        self.assertEqual(low["transaction_type"], 0xFF)
        self.assertEqual(high["transaction_type"], 0)
        self.assertNotEqual(low["selected_table_index"], high["selected_table_index"])


@unittest.skipUnless(all(path.is_file() for path, _ in INPUTS.values()),
                     "Private a6400A own-image prefix inputs unavailable")
class OwnImagePrefixTests(unittest.TestCase):
    def test_default_policy_preserves_first_brk_boundary_without_a_reply(self):
        report = run_prefix()
        self.assertEqual(report["result"]["status"], "syscall_boundary")
        self.assertEqual(report["result"]["boundary"]["r7"], 45)
        self.assertEqual(report["result"]["syscall_responses_supplied"], 0)

    def test_compiled_identity_policy_still_stops_at_the_unsupplied_preload_namespace(self):
        report = run_prefix("brk-uname")
        result = report["result"]
        self.assertEqual(result["status"], "syscall_boundary")
        self.assertEqual([row["syscall"] for row in result["serviced_syscalls"]], ["brk", "uname"])
        self.assertEqual(result["boundary"]["r7"], 33)
        self.assertEqual(result["boundary"]["path_preview"]["value"], "/etc/ld.so.preload")
        self.assertEqual(set(result["uname_fields_read"]), {"release"})


if __name__ == "__main__":
    unittest.main()
