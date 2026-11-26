#!/usr/bin/env python3
"""L0111 static check: the 13 write-only F-0109 offsets are Z0 rows in camcore_rules_f3c0.

Parses the table in qemu-a6400/qemu-overlay/hw/misc/cxd90045_camcore.c and checks that
each of the 13 offsets inside the f3c0 window (0xF3C04208..0xF3C0422C and
0xF3C08280..0xF3C0828C) has a CAM_ZERO row with mask 0xffffffff, and that the table
stays sorted by offset.

Run from the worktree root:
  python3 -m unittest qemu-a6400/tests/test_qemu_f3c0.py -q
"""
import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "qemu-overlay/hw/misc/cxd90045_camcore.c"
BASE = 0xF3C00000
PA = [0xF3C04208, 0xF3C0420C, 0xF3C04210, 0xF3C04214, 0xF3C04218, 0xF3C0421C,
      0xF3C04224, 0xF3C04228, 0xF3C0422C, 0xF3C08280, 0xF3C08284, 0xF3C08288,
      0xF3C0828C]
ROW = re.compile(r"\{\s*(0x[0-9a-fA-F]+),\s*(CAM_\w+),\s*(0x[0-9a-fA-F]+),")


def f3c0_rows():
    text = SRC.read_text()
    start = text.index("static const CamCoreRule camcore_rules_f3c0[] = {")
    end = text.index("};", start)
    return [(int(o, 16), k, int(m, 16)) for o, k, m in ROW.findall(text[start:end])]


class F3C0WriteOnlyRows(unittest.TestCase):
    def test_thirteen_write_only_rows_present(self):
        rows = {off: (kind, mask) for off, kind, mask in f3c0_rows()}
        for pa in PA:
            off = pa - BASE
            self.assertIn(off, rows, f"missing row for offset 0x{off:X}")
            self.assertEqual(rows[off], ("CAM_ZERO", 0xFFFFFFFF), f"offset 0x{off:X}")

    def test_table_sorted_by_offset(self):
        offs = [off for off, _, _ in f3c0_rows()]
        self.assertEqual(offs, sorted(offs))
        self.assertEqual(len(offs), len(set(offs)))


if __name__ == "__main__":
    unittest.main()
