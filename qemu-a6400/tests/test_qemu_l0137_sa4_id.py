#!/usr/bin/env python3
"""L0137 static check: ID-only window for SA core 4 at 0xF35D00F0.

av-cam 0x63A7ED50 reads SA core 4's four ID words (0xF35D00F0..0xFC) and
byte-compares them with the firmware constant "SA1_V1.0SA5_V1.0" (F-0136).
F-0136 evidences only the ID, so the model must be a 16-byte read-only
window returning that string, and the sa_cores table must still have the
four full cxd90014-sa cores (no core 4 instance).

Static checks only (no QEMU boot):
  S.id_string               the model holds exactly "SA1_V1.0SA5_V1.0"
  S.window_created_once     one 0x10 window at 0xF35D00F0, own name
  S.window_priority         mapped at -600 (above the -1000 catch-all)
  S.no_sa_core_4            sa_cores still has four entries, none at 0xF35D0000

Run from the repository root:
  python3 -m unittest qemu-a6400/tests/test_qemu_l0137_sa4_id.py -q
"""
import re
import unittest
from pathlib import Path

SOURCE = Path("qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c")

ID_STRING = "SA1_V1.0SA5_V1.0"
BASE = 0xF35D00F0
SIZE = 0x10
WINDOW_PRIORITY = -600
REGION_NAME = "cxd90045.sa4-id"


def source_text():
    return SOURCE.read_text(errors="replace")


def id_bytes():
    text = source_text()
    marker = "cxd90045_sa4_id[16]"
    start = text.index(marker)
    body = text[text.index("{", start):text.index("};", start)]
    chars = re.findall(r"'(\\.|[^'])'", body)
    return "".join(c if not c.startswith("\\") else chr(ord(c[1]))
                   for c in chars)


def window_creation():
    text = source_text()
    start = text.index("L0137: ID-only window for SA core 4")
    block = text[start:find_block_end(text, start)]
    init = re.search(r'memory_region_init_io\([^;]*?"cxd90045\.sa4-id"\s*,'
                     r'\s*(0x[0-9A-Fa-f]+)\)', block)
    add = re.search(r'memory_region_add_subregion_overlap\(sysmem,\s*'
                    r'(0x[0-9A-Fa-f]+)(?:ULL)?,\s*\w+,\s*(-?\d+)\)', block)
    if not init or not add:
        return None
    return (int(init.group(1), 16), int(add.group(1), 16),
            int(add.group(2)))


def find_block_end(text, start):
    depth = 0
    seen = False
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
            seen = True
        elif text[i] == "}":
            depth -= 1
            if seen and depth == 0:
                return i
    return len(text)


def sa_core_entries():
    text = source_text()
    marker = "} sa_cores[] = {"
    start = text.index(marker)
    body = text[start:text.index("};", start)]
    return [int(base, 16) for base in
            re.findall(r"\{\s*(0x[0-9A-Fa-f]+)ULL,", body)]


class L0137StaticTest(unittest.TestCase):
    def test_id_string(self):
        self.assertEqual(id_bytes(), ID_STRING)

    def test_window_created_once(self):
        creation = window_creation()
        self.assertIsNotNone(creation)
        size, base, _ = creation
        self.assertEqual(size, SIZE)
        self.assertEqual(base, BASE)

    def test_window_priority(self):
        creation = window_creation()
        self.assertIsNotNone(creation)
        self.assertEqual(creation[2], WINDOW_PRIORITY)

    def test_no_sa_core_4(self):
        entries = sa_core_entries()
        self.assertEqual(len(entries), 4)
        self.assertNotIn(0xF35D0000, entries)


if __name__ == "__main__":
    unittest.main()
