"""Guard tests for the WiFiSync camera-side AP script and configuration.

These are static contract checks: the script must keep the physically verified
module order, AP-mode wpa_supplicant settings and the no-internet DHCP shape,
must only write under /tmp_bt/wifisync, and must stay POSIX-shell clean.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "mods" / "wifisync"
SCRIPT = MOD / "wifisync_ap.sh"
CFG = MOD / "wifisync.cfg"


class WifiSyncScriptTest(unittest.TestCase):
    def test_script_exists_and_is_posix(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        shell = shutil.which("sh")
        if shell:
            subprocess.run([shell, "-n", str(SCRIPT)], check=True, timeout=30)

    def test_module_order_matches_verified_sequence(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        order = [
            "ins_if wlanIrq /usr/kmod/wlanIrq.ko",
            "ins_if compat /usr/kmod/compat.ko",
            "ins_if cfg80211 /usr/kmod/cfg80211.ko",
            "ins_if mmc_core /usr/kmod/mmc_core.ko",
            "ins_if mmcioWrapper /usr/kmod/mmcioWrapper.ko",
            "ins_if bcmdhd /usr/kmod/bcmdhd.ko",
        ]
        positions = [text.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("firmware_path=/usr/wlan/bcm/bcm4339.bin", text)
        self.assertNotIn("dhd_irq=144", text)

    def test_ap_mode_wpa_settings(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        for needle in (
            "mode=2",
            "max_num_sta=1",
            "key_mgmt=WPA-PSK",
            "proto=RSN",
            "pairwise=CCMP",
            "group=CCMP",
            "beacon_int=100",
            "ctrl_interface=$DIR/ctrl",
        ):
            self.assertIn(needle, text)

    def test_non_dfs_channels_and_width_fallback(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("36|40|44|48", text)
        self.assertIn("for w in 40 20", text)
        self.assertIn('spec="$chan/$width"', text)

    def test_no_internet_dhcp_shape(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("192.168.122.1", text)
        self.assertIn("range 192.168.122.10 192.168.122.20;", text)
        self.assertNotIn("option routers", text)
        self.assertNotIn("option domain-name-servers", text)

    def test_standby_and_active_use_wl_pm(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('standby) wl -i "$IFACE" pm 2', text)
        self.assertIn('active) wl -i "$IFACE" pm 0', text)

    def test_writes_stay_in_tmp_bt(self) -> None:
        import re

        text = SCRIPT.read_text(encoding="utf-8")
        for target in re.findall(r'>>?\s*("[^"]+"|[^\s;&|)]+)', text):
            clean = target.strip('"')
            self.assertTrue(
                clean.startswith("$DIR/") or clean in ("$LOG", "$STATE", "/dev/null"),
                f"unexpected redirect target: {target}",
            )

    def test_config_example_covers_every_key(self) -> None:
        text = CFG.read_text(encoding="utf-8")
        for key in (
            "ssid=", "psk=", "channel=", "width=", "idle_sec=", "standby=",
            "folder=", "nvram=", "country=", "allow_2g=", "min_jpg=", "min_arw=",
        ):
            self.assertIn(key, text)
        values = dict(
            line.split("=", 1) for line in text.splitlines()
            if "=" in line and not line.strip().startswith("#")
        )
        self.assertTrue(8 <= len(values["psk"]) <= 63)
        self.assertIn(values["channel"], ("36", "40", "44", "48"))
        self.assertIn(values["width"], ("20", "40", "80"))
        self.assertEqual(values["standby"], "low")


if __name__ == "__main__":
    unittest.main()
