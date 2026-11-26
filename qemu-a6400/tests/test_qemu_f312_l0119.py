#!/usr/bin/env python3
"""L0119 static check: write-only offset 0x3000 is a CAM_ZERO row in camcore_rules_f312.

Parses the table in qemu-a6400/qemu-overlay/hw/misc/cxd90045_camcore.c and checks that
offset 0x3000 (PA 0xF3123000, relative to the 0xF3120000 base) has a CAM_ZERO row with
mask 0xffffffff, that the table stays sorted by offset with no duplicates, and that the
existing rows at 0x2160 and 0x0410 are unchanged.

Run from the worktree root:
  python3 -m unittest qemu-a6400/tests/test_qemu_f312_l0119.py -q
"""
import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "qemu-overlay/hw/misc/cxd90045_camcore.c"
BASE = 0xF3120000
PA = [0xF3123000]
ROW = re.compile(r"\{\s*(0x[0-9a-fA-F]+),\s*(CAM_\w+),\s*(0x[0-9a-fA-F]+),")


def f312_rows():
    text = SRC.read_text()
    start = text.index("static const CamCoreRule camcore_rules_f312[] = {")
    end = text.index("};", start)
    return [(int(o, 16), k, int(m, 16)) for o, k, m in ROW.findall(text[start:end])]


class F312WriteOnlyRows(unittest.TestCase):
    def test_write_only_row_present(self):
        rows = {off: (kind, mask) for off, kind, mask in f312_rows()}
        for pa in PA:
            off = pa - BASE
            self.assertIn(off, rows, f"missing row for offset 0x{off:X}")
            self.assertEqual(rows[off], ("CAM_ZERO", 0xFFFFFFFF), f"offset 0x{off:X}")

    def test_table_sorted_and_unique(self):
        offs = [off for off, _, _ in f312_rows()]
        self.assertEqual(offs, sorted(offs))
        self.assertEqual(len(offs), len(set(offs)))

    def test_neighbour_rows_unchanged(self):
        rows = {off: (kind, mask) for off, kind, mask in f312_rows()}
        self.assertEqual(rows[0x2160], ("CAM_RF", 0xFFFFFFFF))
        self.assertEqual(rows[0x0410], ("CAM_ZERO", 0xFFFFFFFF))


if __name__ == "__main__":
    unittest.main()
