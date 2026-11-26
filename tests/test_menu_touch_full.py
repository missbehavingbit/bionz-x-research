"""Offline verification for the v2 dual-hook full-menu touch payload and guard binary.

The checks read local build outputs under ``artifacts/hardware/`` (gitignored, built by
``tools/research/build_menu_touch_full.py``). When those outputs are absent -- as on a
clean CI checkout -- each check skips instead of failing.
"""
import json
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "hardware"


def fnv1a_32(data: bytes) -> int:
    h = 2166136261
    for b in data:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


class MenuTouchFullTests(unittest.TestCase):
    def _require(self, *names):
        missing = [name for name in names if not (ARTIFACTS / name).exists()]
        if missing:
            self.skipTest("build artifacts not present: " + ", ".join(missing))

    def test_build_manifest_and_bounds(self):
        self._require("menu-touch-full-build.json")
        manifest_path = ARTIFACTS / "menu-touch-full-build.json"
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["version"], 2)
        self.assertEqual(manifest["code_address"], "0x700d708")
        self.assertEqual(manifest["state_address"], "0x7143920")
        self.assertEqual(manifest["hook_rel"], "0x6309d48")
        self.assertEqual(manifest["hook_prs"], "0x6309e54")
        self.assertLessEqual(manifest["code_bytes"], 2296)
        self.assertEqual(manifest["code_bytes"] % 4, 0)
        self.assertEqual(manifest["state_bytes"], 632)
        self.assertLessEqual(manifest["state_bytes"], 1760)
        for key in ("rel_entry", "prs_entry"):
            entry = int(manifest[key], 16)
            self.assertEqual(entry & 1, 1)
            self.assertGreaterEqual(entry & ~1, 0x0700D708)
            self.assertLess(entry & ~1, 0x0700D708 + manifest["code_bytes"])

    def test_req_headers_and_checksums(self):
        self._require(
            "menu-touch-full-payload.bin",
            "menu-touch-full-check.req",
            "menu-touch-full-install.req",
            "menu-touch-full-remove.req",
        )
        payload = (ARTIFACTS / "menu-touch-full-payload.bin").read_bytes()
        expected_sum = fnv1a_32(payload)
        for op, name in ((0, "check"), (1, "install"), (2, "remove")):
            req_path = ARTIFACTS / f"menu-touch-full-{name}.req"
            raw = req_path.read_bytes()
            hdr = struct.unpack_from("<10I", raw, 0)
            self.assertEqual(hdr[0], 0x4D544652)
            self.assertEqual(hdr[1], 2)
            self.assertEqual(hdr[2], op)
            self.assertEqual(hdr[3], len(payload))
            self.assertEqual(hdr[6], expected_sum)
            self.assertEqual(hdr[7], 632)
            self.assertEqual(hdr[8], 0x06309D48)
            self.assertEqual(hdr[9], 0x06309E54)
            self.assertEqual(raw[40:], payload)

    def test_guard_binary_is_valid_arm_elf(self):
        self._require("native_menu_touch_full_guard")
        guard = ARTIFACTS / "native_menu_touch_full_guard"
        hdr = guard.read_bytes()[:20]
        self.assertEqual(hdr[:4], b"\x7fELF")
        self.assertEqual(hdr[4], 1)
        self.assertEqual(hdr[5], 1)
        e_machine = struct.unpack_from("<H", hdr, 18)[0]
        self.assertEqual(e_machine, 40)


if __name__ == "__main__":
    unittest.main()
