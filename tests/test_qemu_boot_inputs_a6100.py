import unittest

from tools.research.qemu_boot_inputs_a6100 import parse_partitions


class TestQemuBootInputsA6100(unittest.TestCase):
    def test_parse_partitions_skips_whole_disk_row(self):
        rows = parse_partitions(
            "nflashaB0 start=00000000 size=00400000 dev=00000001 \n"
            "nflasha1 start=00010000 size=007f0000 dev=00000000 \n"
            "nflasha start=00000000 size=50a00000 dev=00000000 \n"
        )
        self.assertEqual(
            [(r["device"], r["start"], r["bytes"], r["dev"]) for r in rows],
            [("nflashaB0", 0, 0x400000, 1), ("nflasha1", 0x10000, 0x7F0000, 0)],
        )


if __name__ == "__main__":
    unittest.main()
