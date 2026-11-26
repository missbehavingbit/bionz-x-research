#!/usr/bin/env python3
"""L0159 static check: the CXD4115 hardware watchdog is on by default.

The E0636 one-channel CXD4115 watchdog at 0xF2402000 serves the stock
kernel's wdt_setup/start/kick writes that previously fell into the
cxd90045.unmodelled catch-all. It used to be opt-in (WATCHDOG=1); this check
asserts that the machine now enables it by default and that run.sh can still
turn it off with WATCHDOG=0.

Static checks only (no QEMU boot):
  S.default_on       sony_a6400_watchdog is initialized to true
  S.run_sh_off       run.sh passes watchdog=off when WATCHDOG=0

Run from the repository root:
  python3 -m unittest qemu-a6400/tests/test_qemu_l0159_watchdog_default.py -q
"""
import re
import unittest
from pathlib import Path

SOURCE = Path("qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c")
RUN_SH = Path("qemu-a6400/run.sh")


class WatchdogDefaultTest(unittest.TestCase):
    def test_default_on(self):
        text = SOURCE.read_text(errors="replace")
        match = re.search(r"static bool sony_a6400_watchdog\s*=\s*(true|false)\s*;", text)
        self.assertIsNotNone(match, "watchdog default declaration not found")
        self.assertEqual(match.group(1), "true", "watchdog must default on")

    def test_run_sh_off(self):
        text = RUN_SH.read_text(errors="replace")
        block = re.search(
            r'if \[\s*"\$\{WATCHDOG:-1\}"\s*=\s*0\s*\];\s*then(.*?)fi',
            text, re.DOTALL)
        self.assertIsNotNone(block, "run.sh WATCHDOG=0 branch not found")
        self.assertIn("watchdog=off", block.group(1),
                      "run.sh must pass watchdog=off for WATCHDOG=0")
        self.assertNotIn("watchdog=on", text,
                         "run.sh should not force watchdog=on")


if __name__ == "__main__":
    unittest.main()
