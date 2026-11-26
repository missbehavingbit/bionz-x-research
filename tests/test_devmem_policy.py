"""tools/devmem_policy.py: /dev/mem rules for the ILCE-6400 (E0647)."""
import unittest

from tools import devmem_policy as p


class DevmemPolicyTests(unittest.TestCase):
    def test_proven_fatal_and_reserved_mib_are_forbidden(self):
        for pa in (0x00000000, 0x000F8000, 0x000FF000):
            self.assertEqual(p.classify(pa).level, "FORBIDDEN", hex(pa))
        self.assertIn("WARMBOOT", p.classify(0x000F8000).reason)
        self.assertEqual(p.classify(0x000F8000).level, "FORBIDDEN")     # uncaught readers

    def test_dram_is_forbidden_in_both_banks(self):
        for pa in (0x00100000, 0x10000000, 0x23A80000, 0x3FFFF000, 0x80000000, 0x8FFFF000):
            self.assertEqual(p.classify(pa).level, "FORBIDDEN", hex(pa))

    def test_verified_register_page_is_allowed(self):
        self.assertEqual(p.classify(0xF2915000).level, "ALLOWED")

    def test_device_map_and_sram_need_go(self):
        self.assertEqual(p.classify(0xF2402000).level, "GO")
        self.assertEqual(p.classify(0xFE005000).level, "GO")
        self.assertEqual(p.classify(0xBF800000).level, "GO")

    def test_holes_sram_tail_bulk_and_read_are_forbidden(self):
        self.assertEqual(p.classify(0xC0000000).level, "FORBIDDEN")      # undecoded
        self.assertEqual(p.classify(0xFE800000).level, "FORBIDDEN")      # beyond 1 MiB SRAM
        self.assertEqual(p.classify(0xF2915000, size=0x2000).level, "FORBIDDEN")
        self.assertEqual(p.classify(0xF2915004).level, "FORBIDDEN")      # unaligned
        self.assertEqual(p.classify(0xF2915000, method="read").level, "FORBIDDEN")

    def test_catching_reader_unlocks_abort_pages_but_not_dram(self):
        self.assertEqual(p.classify(0x000F8000, catching=True).level, "GO")
        self.assertEqual(p.classify(0x00000000, catching=True).level, "GO")
        self.assertEqual(p.classify(0xC0000000, catching=True).level, "GO")
        self.assertEqual(p.classify(0x10000000, catching=True).level, "FORBIDDEN")
        self.assertEqual(p.classify(0xF2915000, method="read", catching=True).level, "FORBIDDEN")
        self.assertIn("caught", p.classify(0x000F8000).reason)

    def test_go_list_only_unlocks_go_pages(self):
        res = dict((pa, ok) for pa, _, ok in p.check_pages([0xFE005000, 0x000F8000], go={0xFE005000, 0x000F8000}))
        self.assertTrue(res[0xFE005000])
        self.assertFalse(res[0x000F8000])


if __name__ == "__main__":
    unittest.main()
