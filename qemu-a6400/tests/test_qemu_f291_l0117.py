#!/usr/bin/env python3
"""L0117 static check: three write-only f291 offsets are Z0 rows in camcore_rules_f291.

Parses the table in qemu-a6400/qemu-overlay/hw/misc/cxd90045_camcore.c and checks that
the offsets 0x4048 (PA 0xF2914048), 0x5044 (PA 0xF2915044) and 0x5114 (PA 0xF2915114),
relative to the 0xF2910000 base, each have a CAM_ZERO row with mask 0xffffffff, that
the table stays sorted by offset with no duplicates, and that the existing eFuse /
MODEREAD row at 0x5010 is still present and unchanged.

Run from the worktree root:
  python3 -m unittest qemu-a6400/tests/test_qemu_f291_l0117.py -q
"""
import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "qemu-overlay/hw/misc/cxd90045_camcore.c"
BASE = 0xF2910000
PA = [0xF2914048, 0xF2915044, 0xF2915114]
ROW = re.compile(r"\{\s*(0x[0-9a-fA-F]+),\s*(CAM_\w+),\s*(0x[0-9a-fA-F]+),")


def f291_rows():
    text = SRC.read_text()
    start = text.index("static const CamCoreRule camcore_rules_f291[] = {")
    end = text.index("};", start)
    return [(int(o, 16), k, int(m, 16)) for o, k, m in ROW.findall(text[start:end])]


class F291WriteOnlyRows(unittest.TestCase):
    def test_three_write_only_rows_present(self):
        rows = {off: (kind, mask) for off, kind, mask in f291_rows()}
        for pa in PA:
            off = pa - BASE
            self.assertIn(off, rows, f"missing row for offset 0x{off:X}")
            self.assertEqual(rows[off], ("CAM_ZERO", 0xFFFFFFFF), f"offset 0x{off:X}")

    def test_table_sorted_and_unique(self):
        offs = [off for off, _, _ in f291_rows()]
        self.assertEqual(offs, sorted(offs))
        self.assertEqual(len(offs), len(set(offs)))

    def test_modread_row_unchanged(self):
        rows = {off: (kind, mask) for off, kind, mask in f291_rows()}
        self.assertEqual(rows[0x5010], ("CAM_ZERO", 0xFFFFFFFF))


if __name__ == "__main__":
    unittest.main()
