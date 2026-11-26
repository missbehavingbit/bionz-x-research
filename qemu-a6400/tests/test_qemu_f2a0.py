#!/usr/bin/env python3
"""L0118 static check: the f2a_clk offset-0 pages, ZERO-only rule windows.

F-0107 identifies 0xF2A00000 and 0xF2A10000 as offset-0 pages of the
CXD90045 clock/reset group.  L0118 maps four per-offset ZERO-only windows,
cxd90045.f2a0..f2a3 (size 0x1000, priority -600, same helper as L0116):

  0xF2A00000  +0x008 +0x024 +0x028 +0x0A4
  0xF2A10000  +0x004 +0x008 +0x024 +0x028
  0xF2A20000  +0x024 +0x028
  0xF2A32000  +0x030 +0x1A0

Every row is CXD90045_F292_ZERO (no RF row), and the windows must not overlap
the existing f2a_clk sibling pages in f2a_bases[] (0xF2A01000, 0xF2A11000,
0xF2A12000, 0xF2A21000, 0xF2A22000, 0xF2A30000).

Static checks only (no QEMU boot):
  S.rows_per_window          each table holds exactly the listed offsets
  S.all_rows_ZERO            no CXD90045_F292_RF row in any L0118 table
  S.windows_created_once     one create call per base, size 0x1000, own table
  S.no_overlap_with_f2a_clk  the four bases do not overlap the sibling pages

Run from the repository root:
  python3 -m unittest qemu-a6400/tests/test_qemu_f2a0.py -q
"""
import re
import unittest
from pathlib import Path

SOURCE = Path("qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c")

WINDOWS = {
    "f2a0": (0xF2A00000, [0x008, 0x024, 0x028, 0x0A4]),
    "f2a1": (0xF2A10000, [0x004, 0x008, 0x024, 0x028]),
    "f2a2": (0xF2A20000, [0x024, 0x028]),
    "f2a3": (0xF2A32000, [0x030, 0x1A0]),
}
F2A_SIBLINGS = [0xF2A01000, 0xF2A11000, 0xF2A12000,
                0xF2A21000, 0xF2A22000, 0xF2A30000]
PAGE = 0x1000


def table_rows(name):
    text = SOURCE.read_text(errors="replace")
    start = text.index(f"static const Cxd90045F292Rule cxd90045_{name}_rules[]")
    body = text[start:text.index("};", start)]
    rows = re.findall(r"\{\s*(0x[0-9a-fA-F]+),\s*(CXD90045_F292_\w+),", body)
    return [(int(off, 16), kind) for off, kind in rows]


def create_calls():
    text = SOURCE.read_text(errors="replace")
    return re.findall(
        r"cxd90045_f292_create\(sysmem,\s*(0x[0-9A-Fa-f]+)ULL,\s*"
        r"(0x[0-9A-Fa-f]+)ULL,\s*\"cxd90045\.(f2a\d)\",\s*"
        r"(cxd90045_f2a\d_rules),\s*ARRAY_SIZE\(cxd90045_f2a\d_rules\)\)",
        text)


class F2A0StaticTest(unittest.TestCase):
    def test_rows_per_window(self):
        for name, (_, offs) in WINDOWS.items():
            with self.subTest(window=name):
                got = sorted(off for off, _ in table_rows(name))
                self.assertEqual(got, sorted(offs))

    def test_all_rows_ZERO(self):
        for name in WINDOWS:
            with self.subTest(window=name):
                kinds = [k for _, k in table_rows(name)]
                self.assertNotIn("CXD90045_F292_RF", kinds)
                self.assertEqual(kinds.count("CXD90045_F292_ZERO"),
                                 len(WINDOWS[name][1]))

    def test_windows_created_once(self):
        calls = create_calls()
        self.assertEqual(len(calls), len(WINDOWS))
        by_name = {name: (base, size, table)
                   for base, size, name, table in calls}
        for name, (base, _) in WINDOWS.items():
            with self.subTest(window=name):
                self.assertIn(name, by_name)
                got_base, got_size, table = by_name[name]
                self.assertEqual(int(got_base, 16), base)
                self.assertEqual(int(got_size, 16), PAGE)
                self.assertEqual(table, f"cxd90045_{name}_rules")

    def test_no_overlap_with_f2a_clk_siblings(self):
        for name, (base, _) in WINDOWS.items():
            for sib in F2A_SIBLINGS:
                with self.subTest(window=name, sibling=hex(sib)):
                    self.assertTrue(base + PAGE <= sib or sib + PAGE <= base)


if __name__ == "__main__":
    unittest.main()
