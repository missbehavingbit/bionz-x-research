"""Constructed saved-frame controls; no firmware, emulator or camera access."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from project_paths import PROJECT_ROOT
from tools.research.liro_flag_wait import (
    RecordedMemory, _section, decode_flag_wait, replay,
)


class LiroFlagWaitTests(unittest.TestCase):
    def setUp(self):
        self.bss, self.text = 0x5F009000, 0x5F000000
        self.control, self.task, self.stack, self.flag = (
            0x64001000, 0x60800000, 0x60802000, 0x64002000)
        self.fp = self.stack + 0x1F60
        self.memory = {
            self.bss + 0x780 + 42 * 8: bytearray(struct.pack("<2I", 0, self.control)),
            self.control: bytearray(160), self.task: bytearray(12),
            self.stack: bytearray(8192), self.flag: bytearray(32),
        }
        for offset, value in ((8, 4), (12, 8), (16, 10), (20, self.task), (24, 43),
                              (0x4C, 0x63D8B805), (0x90, self.flag + 0x10),
                              (0x94, self.bss + 0xF78 + 9 * 8)):
            self.word(self.control, offset, value)
        struct.pack_into("<3I", self.memory[self.task], 0, 2, 0, self.stack)
        struct.pack_into("<3I", self.memory[self.stack], 0x38,
                         self.fp, self.fp - 0x54, self.text + 0x3BE8)
        offset = self.fp - self.stack
        struct.pack_into("<3I", self.memory[self.stack], offset - 0x3C, self.flag, 3, 1)
        struct.pack_into("<I", self.memory[self.stack], offset + 4, 0xFFFFFFFF)
        struct.pack_into("<I", self.memory[self.stack], offset + 0x18, 0x63A67DC3)

    def word(self, base, offset, value):
        struct.pack_into("<I", self.memory[base], offset, value)

    def read(self, address, size):
        for base, data in self.memory.items():
            if base <= address and address + size <= base + len(data):
                return bytes(data[address - base:address - base + size])
        raise ValueError(f"Unobserved constructed RAM at {address:#x}")

    def test_decodes_the_actual_initialization_mask_not_the_main_loop_constant(self):
        result = decode_flag_wait(self.read, self.bss, self.text)
        self.assertEqual((result["flag_id"], result["requested_mask"], result["wait_mode"],
                          result["timeout"]), (10, 3, 1, -1))
        self.assertEqual(result["caller_return"], 0x63A67DC3)
        self.assertEqual(result["phase"], "first_display_initialization_wait")
        self.assertFalse(result["predicate_satisfied"])
        self.assertEqual(result["flag_object"], self.flag)
        self.word(self.stack, self.fp - self.stack - 0x38, 0x3373)
        self.word(self.stack, self.fp - self.stack + 0x18, 0x63D8B81F)
        later = decode_flag_wait(self.read, self.bss, self.text)
        self.assertEqual(later["requested_mask"], 0x3373)
        self.assertEqual(later["phase"], "display_task_main_loop")

    def test_predicate_uses_observed_pattern_and_original_and_or_modes(self):
        for pattern, mode, satisfied in ((0x100, 1, False), (1, 1, True),
                                         (1, 0, False), (3, 0, True)):
            with self.subTest(pattern=pattern, mode=mode):
                self.word(self.flag, 12, pattern)
                self.word(self.stack, self.fp - self.stack - 0x34, mode)
                self.assertEqual(decode_flag_wait(self.read, self.bss, self.text)
                                 ["predicate_satisfied"], satisfied)

    def test_refuses_a_mismatched_object_or_unconfirmed_stack(self):
        self.word(self.control, 0x90, self.flag + 0x14)
        with self.assertRaisesRegex(ValueError, "wait linkage"):
            decode_flag_wait(self.read, self.bss, self.text)
        self.word(self.control, 0x90, self.flag + 0x10)
        self.word(self.stack, 0x38, self.stack - 4)
        with self.assertRaisesRegex(ValueError, "locals escape"):
            decode_flag_wait(self.read, self.bss, self.text)

    def test_refuses_a_runnable_task_or_wrong_original_frame(self):
        self.word(self.task, 0, 0)
        with self.assertRaisesRegex(ValueError, "runnable"):
            decode_flag_wait(self.read, self.bss, self.text)
        self.word(self.task, 0, 2)
        self.word(self.stack, 0x40, self.text + 0x3BEC)
        with self.assertRaisesRegex(ValueError, "twai_flg sleep frame"):
            decode_flag_wait(self.read, self.bss, self.text)

    def test_current_run_markers_can_be_interleaved_but_must_be_unique(self):
        line = "echo ___LIRO_BSS__:0x5f009000U\n"
        self.assertEqual(_section(line, "BSS"), self.bss)
        with self.assertRaisesRegex(ValueError, "one current-run"):
            _section(line + line, "BSS")

    def test_memory_receipts_reject_changes_escapes_holes_and_conflicts(self):
        root = PROJECT_ROOT / "artifacts" / "tests"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            path = Path(temporary)
            (path / "a.bin").write_bytes(b"abcd")
            row = dict(file="a.bin", bytes=4, address=0x64000000,
                       space="guest-virtual-cpu0",
                       sha256=hashlib.sha256(b"abcd").hexdigest())
            memory = RecordedMemory(path, [row, row])
            self.assertEqual(memory.read(0x64000000, 4), b"abcd")
            with self.assertRaisesRegex(ValueError, "Unobserved"):
                memory.read(0x64000000, 8)
            with self.assertRaisesRegex(ValueError, "namespace"):
                RecordedMemory(path, [dict(row, file="../a.bin")])
            (path / "a.bin").write_bytes(b"ABCD")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                RecordedMemory(path, [row])
            (path / "b.bin").write_bytes(b"abcd")
            second = dict(row, file="b.bin")
            conflict = RecordedMemory(path, [dict(row, sha256=hashlib.sha256(b"ABCD").hexdigest()),
                                              second])
            with self.assertRaisesRegex(ValueError, "Conflicting"):
                conflict.read(0x64000000, 4)
            (path / "c.bin").write_bytes(b"abcdWXYZ")
            partial = RecordedMemory(path, [
                dict(row, file="c.bin", bytes=8,
                     sha256=hashlib.sha256(b"abcdWXYZ").hexdigest()),
                dict(second, address=0x64000004)])
            with self.assertRaisesRegex(ValueError, "Conflicting"):
                partial.read(0x64000000, 8)

    def test_incomplete_probe_is_not_promoted_to_an_observation(self):
        root = PROJECT_ROOT / "artifacts" / "tests"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            path = Path(temporary)
            probe = path / "probe.json"
            probe.write_text(json.dumps(dict(status="failed")), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "completed, stopped"):
                replay(probe, path, path / "absent", path / "absent")


if __name__ == "__main__":
    unittest.main()
