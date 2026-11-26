"""The reader count behind E0478 must not miss the two commonest shapes of a read.

Synthetic images, so no firmware is needed: a base is materialised with Sony's PIC idiom
(``ldr r3, [pc, #k]`` ; ``add r3, pc``) and one access follows. Before these tests the counter
checked "is the base overwritten?" first, so ``ldrb.w r3, [r3, #field]`` ended the walk without
being counted, and it compared widths for equality, so a word load over a byte field and every
signed load were invisible. The E0478 result was re-run with the fix on eleven bodies and did not
change; these tests keep the counter able to see a reader if one exists.
"""
from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "research"))

from av_eyeaf_field import ORIL_BASE, count_accesses  # noqa: E402

FIELD = 0x5D0
LITERAL = 0x1000
#: ldr r3, [pc, #4] at 0; add r3, pc at 2: base = literal + address of the add + 4.
BASE = (LITERAL + ORIL_BASE + 2 + 4) & 0xFFFFFFFF


def image(first: int, second: int) -> bytes:
    """Materialise r3, perform one 32-bit access, then the literal word."""
    return struct.pack("<HHHHI", 0x4B01, 0x447B, first, second, LITERAL)


class ReaderShapes(unittest.TestCase):
    def test_a_load_into_the_base_register_is_counted(self) -> None:
        # ldrb.w r3, [r3, #0x5d0]
        self.assertEqual(count_accesses(image(0xF893, 0x3000 | FIELD), 3, BASE, FIELD, 1), (0, 1))

    def test_a_word_load_over_a_byte_field_is_a_read(self) -> None:
        # ldr.w r0, [r3, #0x5d0] reads bytes 0x5d0..0x5d3, the byte field included.
        self.assertEqual(count_accesses(image(0xF8D3, FIELD), 3, BASE, FIELD, 1), (0, 1))

    def test_a_signed_load_is_a_read(self) -> None:
        # ldrsb.w r0, [r3, #0x5d0]
        self.assertEqual(count_accesses(image(0xF993, FIELD), 3, BASE, FIELD, 1), (0, 1))

    def test_an_access_beside_the_field_is_not_counted(self) -> None:
        # ldrb.w r3, [r3, #0x5d4]: a word field at 0x5d0 covers 0x5d0..0x5d3 only.
        self.assertEqual(count_accesses(image(0xF893, FIELD + 4), 3, BASE, FIELD, 4), (0, 0))

    def test_the_handler_store_is_counted_as_a_write(self) -> None:
        # str.w r0, [r3, #0x5d0]
        self.assertEqual(count_accesses(image(0xF8C3, FIELD), 3, BASE, FIELD, 4), (1, 0))


if __name__ == "__main__":
    unittest.main()
