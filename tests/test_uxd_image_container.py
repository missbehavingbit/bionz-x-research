"""Constructed-fixture tests for the uxd image container parser (no firmware data)."""

from __future__ import annotations

import struct
import unittest

from tools.research import uxd_image_container as uxd


def _fixture() -> bytes:
    header = uxd.MAGIC + struct.pack("<6H", 0, 0, 8, 2, 0, 0)
    index = b"\x00\x00\x05\x00"  # opaque u16 index words before the record table
    raw = b"\x11\x22" * 4  # 2x2 raw 16 bpp
    comp = bytes(range(8))
    recs_start = len(header) + len(index)
    data = recs_start + 2 * uxd.RECORD.size  # first block starts at end of table
    # offsets are relative to each record's address + 4
    rec0 = uxd.RECORD.pack(0x1A0CC117, 0x80000000, 2, 2, len(raw), data - (recs_start + 4))
    rec1 = uxd.RECORD.pack(0x1A0CC118, 0xE0000000, 3, 1, len(comp), data + len(raw) - (recs_start + 20 + 4))
    return header + index + rec0 + rec1 + raw + comp


class UxdContainerTests(unittest.TestCase):
    def test_parse_records_and_blocks(self) -> None:
        blob = _fixture()
        table, recs = uxd.parse(blob)
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0].ui_res_id, 0x020CC117)
        self.assertEqual(table, recs[0].record_pos)
        self.assertEqual(uxd.block(blob, recs[0]), b"\x11\x22" * 4)
        self.assertEqual(uxd.block(blob, recs[1]), bytes(range(8)))

    def test_rejects_non_uxd(self) -> None:
        with self.assertRaises(ValueError):
            uxd.find_record_table(b"uxc\x07" + bytes(64))

    def test_block_bounds(self) -> None:
        blob = _fixture()
        _, recs = uxd.parse(blob)
        bad = uxd.ImageRecord(recs[1].res_id, recs[1].fmt, 3, 1, 10_000, recs[1].offset, recs[1].record_pos)
        with self.assertRaises(ValueError):
            uxd.block(blob, bad)


class UxdExtendedRecordTests(unittest.TestCase):
    def test_40_byte_record(self) -> None:
        header = uxd.MAGIC + struct.pack("<6H", 0, 0, 8, 2, 0, 0)
        start = len(header)
        data = start + 20 + 40
        r0 = uxd.RECORD.pack(0x1A000001, 0xE0000000, 4, 4, 4, data - (start + 4))
        ext = bytes(range(20))
        r1 = struct.pack("<IIHH", 0x1A000002, 0xE8000000, 12, 12) + ext + struct.pack("<II", 4, data + 4 - (start + 20 + 4))
        blob = header + r0 + r1 + b"AAAA" + b"BBBB"
        table, recs = uxd.parse(blob)
        self.assertEqual(table, start)
        self.assertEqual([r.res_id for r in recs], [0x1A000001, 0x1A000002])
        self.assertEqual(recs[1].ext, ext)
        self.assertEqual(uxd.block(blob, recs[1]), b"BBBB")


if __name__ == "__main__":
    unittest.main()
