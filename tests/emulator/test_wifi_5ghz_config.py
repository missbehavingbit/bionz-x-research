"""Offline tests for ILCE-6400 5 GHz BCM4339 CLM, NVRAM profiles, and bootstrap script."""
from __future__ import annotations

from pathlib import Path
import unittest

from tools.research.probe_a6400_wifi_5ghz import BCM_BIN, NVRAM_HIGH_DIR, inspect_offline

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "tools/native_camera/wifi_5ghz_bootstrap.sh"


class Wifi5GhzConfigTests(unittest.TestCase):
    def test_clm_and_high_nvram_profiles_present(self) -> None:
        if not BCM_BIN.is_file():
            self.skipTest("extracted a6400 firmware not present")
        info = inspect_offline()
        self.assertEqual(info["high_profiles_count"], 15)
        self.assertEqual(info["clm"]["xr_revisions"], list(range(75, 90)))
        self.assertIn("EU", info["clm"]["countries_present"])
        self.assertIn("US", info["clm"]["countries_present"])
        eu = info["recommended_eu_cec"]
        self.assertIsNotNone(eu)
        self.assertEqual(eu["ccode"], "XR")
        self.assertEqual(eu["regrev"], 88)
        self.assertEqual(eu["aa5g"], "1")
        self.assertTrue(eu["has_vht80"])

    def test_bootstrap_script_supports_sta_and_ap_in_ram_only(self) -> None:
        text = BOOTSTRAP.read_text(encoding="utf-8")
        self.assertIn("--mode", text)
        self.assertIn('if [ "$MODE" = "ap" ]', text)
        self.assertIn('elif [ "$MODE" = "sta" ]', text)
        self.assertIn("nvram_path=", text)
        self.assertIn("/tmp_bt/wifi_run", text)
        self.assertIn("ampdu 1", text)
        code_lines = [l for l in text.splitlines() if not l.lstrip().startswith("#")]
        self.assertFalse(any("/dev/nflasha" in l for l in code_lines))


if __name__ == "__main__":
    unittest.main()
