"""Unit tests for the zero-camera WiFiSync Phase 0 contract extraction."""
from __future__ import annotations

import unittest

from tools.research.wifisync_contracts import build_manifest


class WifiSyncContractsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.manifest = build_manifest()
        except FileNotFoundError as exc:
            raise unittest.SkipTest(f"extracted firmware unavailable: {exc}") from exc

    def test_all_contract_checks_pass(self) -> None:
        self.assertEqual(self.manifest["failed"], [], self.manifest["failed"])
        self.assertGreaterEqual(len(self.manifest["checks"]), 20)

    def test_library_identities_pinned(self) -> None:
        libs = self.manifest["libraries"]
        self.assertEqual(libs["libmpr.so"]["size"], 12740352)
        self.assertEqual(libs["libInfraFsys.so"]["size"], 411548)
        self.assertEqual(libs["libmpr.so"]["fnv1a_4096"], "0x7a4ed0d1")
        self.assertEqual(libs["libInfraFsys.so"]["fnv1a_4096"], "0x61e41f02")

    def test_hook_prologue_is_twelve_bytes(self) -> None:
        hook = self.manifest["hook"]
        self.assertEqual(hook["reference_address"], "0x079bd1a4")
        self.assertEqual(hook["prologue_hex"], "2de9f0410d4600af04464049")

    def test_fsys_open_read_flag_contract(self) -> None:
        open_contract = self.manifest["fsys_open"]
        self.assertTrue(open_contract["flags_read_is_bit0"])
        self.assertEqual(open_contract["address"], "0x0001bf84")

    def test_stat_size_is_64bit_at_offset_zero(self) -> None:
        self.assertTrue(self.manifest["pffile_stat"]["stores_64bit_size_at_r1"])
        self.assertTrue(self.manifest["fsys_stat"]["calls_PrFILE_fstat"])

    def test_ap_tools_and_bsa_recorded(self) -> None:
        wlan = self.manifest["wlan"]
        self.assertTrue(wlan["checks"]["all_tools_present"])
        self.assertTrue(wlan["checks"]["bsa_server_present"])
        self.assertTrue(wlan["nvram_5ghz_frontend"])


if __name__ == "__main__":
    unittest.main()
