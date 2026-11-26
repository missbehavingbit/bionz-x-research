#!/usr/bin/env python3
"""L0154 static check: dwc3-gadget is on by default.

The E0590 DWC3 model at 0xF020C000 serves the guest's GCTL (0x110),
GUSB2PHYCFG0 (0x200), GUSB3PIPECTL0 (0x2C0) and DCTL (0x704) accesses.
It used to be opt-in (DWC3_GADGET=1); this check asserts that the machine
now enables it by default and that run.sh can still turn it off with
DWC3_GADGET=0.

Static checks only (no QEMU boot):
  S.default_on       sony_a6400_dwc3_gadget is initialized to true
  S.run_sh_off       run.sh passes dwc3-gadget=off when DWC3_GADGET=0

Run from the repository root:
  python3 -m unittest qemu-a6400/tests/test_qemu_l0154_dwc3_default.py -q
"""
import re
import unittest
from pathlib import Path

SOURCE = Path("qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c")
RUN_SH = Path("qemu-a6400/run.sh")


class Dwc3DefaultTest(unittest.TestCase):
    def test_default_on(self):
        text = SOURCE.read_text(errors="replace")
        match = re.search(r"static bool sony_a6400_dwc3_gadget\s*=\s*(true|false)\s*;", text)
        self.assertIsNotNone(match, "dwc3-gadget default declaration not found")
        self.assertEqual(match.group(1), "true", "dwc3-gadget must default on")

    def test_run_sh_off(self):
        text = RUN_SH.read_text(errors="replace")
        block = re.search(
            r'if \[\s*"\$\{DWC3_GADGET:-1\}"\s*=\s*0\s*\];\s*then(.*?)fi',
            text, re.DOTALL)
        self.assertIsNotNone(block, "run.sh DWC3_GADGET=0 branch not found")
        self.assertIn("dwc3-gadget=off", block.group(1),
                      "run.sh must pass dwc3-gadget=off for DWC3_GADGET=0")
        self.assertNotIn("dwc3-gadget=on", text,
                         "run.sh should not force dwc3-gadget=on")


if __name__ == "__main__":
    unittest.main()
