"""Contract tests for building QEMU inputs from the public a6400 updater.

What these protect: the SDM table the kernel parses (a wrong magic, count or row order makes
the guest reject every partition), the power-of-two backing size the eMMC model requires, and
the powered sub-micro transformation, which must change slot B TLV 0x03 and its CRC and
nothing else, or the replay would claim a camera state the frames do not carry.
"""
from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.a6400.execution.submicro_link import (  # noqa: E402
    decode_slot_b_frame, encode_slot_b_frame, stamp_frame_crc, verify_frame_crc)
from tools.research.qemu_packaged_inputs import (  # noqa: E402
    TLV03_HIBERNATE, TLV03_POWERED, USER_AREA_OFFSET, backing_size, encode_sdm_header,
    parse_mount_conf, parse_partinf, power_slot_b)

PARTINF = """
# /dev/nflasha1    7.9375MB(System Group  SYSTEM (Updater)  /dev/nflasha1  )
0x00010000,0x007F0000,0x00000001,0xFFFFFFFF

# /dev/nflasha2        20MB(System Group  SYSTEM (Setting)  /dev/nflasha2  )
0x00800000,0x01400000,0x00000001,0xFFFFFFFF
# /dev/nflasha3 reserved, size = 0
0x01C00000,0x00000000,0x00000000,0x00000000
"""


def _frames(tlv03: bytes) -> bytes:
    slot_a = bytearray(128)
    slot_a[0] = 0x20
    slot_c = bytearray(128)
    slot_c[0] = 0x60
    slot_b = encode_slot_b_frame(0x30, [(0x00, bytes.fromhex("01020138")), (0x03, tlv03),
                                        (0x0A, bytes(8))])
    return stamp_frame_crc(slot_a) + stamp_frame_crc(slot_c) + slot_b


class PartitionTableTests(unittest.TestCase):
    def test_rows_keep_device_order_and_values(self) -> None:
        rows = parse_partinf(PARTINF)
        self.assertEqual([r["device"] for r in rows], ["nflasha1", "nflasha2", "nflasha3"])
        self.assertEqual((rows[1]["start"], rows[1]["bytes"]), (0x00800000, 0x01400000))
        self.assertEqual(rows[2]["flags"], 0)

    def test_header_is_what_the_kernel_parser_reads(self) -> None:
        table = encode_sdm_header(parse_partinf(PARTINF))
        self.assertEqual(table[:8], b"82461.00")
        self.assertEqual(struct.unpack_from("<I", table, 8)[0], 3)
        self.assertEqual(struct.unpack_from("<4I", table, 32 + 16),
                         (0x00800000, 0x01400000, 1, 0xFFFFFFFF))

    def test_a_table_that_overlaps_the_first_partition_is_refused(self) -> None:
        rows = [dict(device="nflasha1", start=0x20, bytes=0x200, type=1, flags=1)]  # table is 48 bytes
        with self.assertRaises(ValueError):
            encode_sdm_header(rows)

    def test_backing_file_is_a_power_of_two_holding_every_partition(self) -> None:
        rows = parse_partinf(PARTINF)
        size = backing_size(rows)
        self.assertEqual(size & (size - 1), 0)
        self.assertGreaterEqual(size, USER_AREA_OFFSET + 0x00800000 + 0x01400000)

    def test_mount_conf_names_the_vfat_geometry(self) -> None:
        table = parse_mount_conf("/dev/nflasha2 /setting vfat 20971520 16384\n")
        self.assertEqual(table["nflasha2"], dict(mount="/setting", fstype="vfat",
                                                 bytes=20971520, cluster=16384))


class PoweredFramesTests(unittest.TestCase):
    def test_only_tlv03_and_the_slot_b_crc_change(self) -> None:
        packaged = _frames(TLV03_HIBERNATE)
        powered = power_slot_b(packaged)
        changed = [i for i in range(384) if packaged[i] != powered[i]]
        self.assertTrue(changed)
        self.assertTrue(all(i >= 256 for i in changed))
        self.assertTrue(verify_frame_crc(powered[256:]))
        records = {r.tlv_id: r.data for r in decode_slot_b_frame(powered[256:]).records}
        self.assertEqual(records[0x03], TLV03_POWERED)
        self.assertEqual(records[0x0A], bytes(8))

    def test_frames_not_in_hibernate_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            power_slot_b(_frames(TLV03_POWERED))

    def test_a_bad_crc_is_refused(self) -> None:
        broken = bytearray(_frames(TLV03_HIBERNATE))
        broken[5] ^= 0xFF
        with self.assertRaises(ValueError):
            power_slot_b(bytes(broken))


if __name__ == "__main__":
    unittest.main()
