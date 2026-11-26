#!/usr/bin/env python3
"""L0116 static check: the clkrst3 0xF2900000 rule window, ZERO-only rows.

F-0104 identifies 0xF2900000..0xF2900FFF as the CXD90045 clock/reset bank
(`clkrst3`).  L0116 maps it as a per-offset window cxd90045.f290 (size 0x1000,
priority -600) whose nine rows are all CXD90045_F292_ZERO: +0x004, +0x008,
+0x028, +0x034, +0x038, +0x044, +0x0A4, +0x0A8, +0x0C4.  +0x030 deliberately
has no row (its read is branched on, F-0104), so it must keep logging as
unmodelled.

Static checks only (no QEMU boot):
  S.rows_are_the_nine_offsets   the table holds exactly the nine offsets
  S.no_RF_row                   no CXD90045_F292_RF row
  S.all_rows_ZERO               every row is CXD90045_F292_ZERO
  S.no_row_for_0x030            +0x030 is not ruled
  S.window_created_once         one cxd90045_f292_create call at 0xF2900000,
                                size 0x1000, using cxd90045_f290_rules

Run from the repository root:
  python3 -m unittest qemu-a6400/tests/test_qemu_f290.py -q
"""
import re
import unittest
from pathlib import Path

SOURCE = Path("qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c")
RULED_OFF = [0x004, 0x008, 0x028, 0x034, 0x038, 0x044, 0x0A4, 0x0A8, 0x0C4]
UNRULED_OFF = 0x030


def table_rows():
    text = SOURCE.read_text(errors="replace")
    start = text.index("static const Cxd90045F292Rule cxd90045_f290_rules[]")
    body = text[start:text.index("};", start)]
    rows = re.findall(r"\{\s*(0x[0-9a-fA-F]+),\s*(CXD90045_F292_\w+),", body)
    return [(int(off, 16), kind) for off, kind in rows]


def create_calls():
    text = SOURCE.read_text(errors="replace")
    return re.findall(
        r"cxd90045_f292_create\(sysmem,\s*(0x[0-9A-Fa-f]+)ULL,\s*"
        r"(0x[0-9A-Fa-f]+)ULL?,\s*\"cxd90045\.f290\",\s*"
        r"cxd90045_f290_rules,\s*ARRAY_SIZE\(cxd90045_f290_rules\)\)",
        text)


class F290StaticTest(unittest.TestCase):
    def test_rows_are_the_nine_offsets(self):
        offs = sorted(off for off, _ in table_rows())
        self.assertEqual(offs, sorted(RULED_OFF))

    def test_no_RF_row(self):
        kinds = [k for _, k in table_rows()]
        self.assertEqual(kinds.count("CXD90045_F292_RF"), 0)

    def test_all_rows_ZERO(self):
        kinds = [k for _, k in table_rows()]
        self.assertEqual(kinds.count("CXD90045_F292_ZERO"), len(RULED_OFF))

    def test_no_row_for_0x030(self):
        offs = [off for off, _ in table_rows()]
        self.assertNotIn(UNRULED_OFF, offs)

    def test_window_created_once(self):
        calls = create_calls()
        self.assertEqual(len(calls), 1)
        base, size = calls[0]
        self.assertEqual(int(base, 16), 0xF2900000)
        self.assertEqual(int(size, 16), 0x1000)


if __name__ == "__main__":
    unittest.main()
