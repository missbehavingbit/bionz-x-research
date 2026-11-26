"""Offline checks for tools.research.reload_mod (no camera access)."""
import struct
import unittest
from pathlib import Path

from tools.research.reload_mod import fnv1a_32, hook_bytes, parse_result, parse_smod_v2

ROOT = Path(__file__).resolve().parents[1]
MTF = ROOT / "artifacts/tests/mtf-v2-build-1/MenuTouchFull.mod"


def make_mod(code=b"\x70\x47\x00\xbf", fixup=8, target=0x06309D48):
    tmpl = b"\xdf\xf8\x04\xf0\x00\xbf\x00\xbf" + struct.pack("<I", 1)
    orig = bytes.fromhex("2de9f04788b04ff014080122")
    hdr = struct.pack("<3I32s6I", 0x444F4D53, 2, 0, b"Test", len(code), fnv1a_32(code), 16, 1, 1, 1)
    hook = struct.pack("<IBB2x16s16s", target, 12, fixup, orig, tmpl)
    return hdr + hook + code


class ReloadModTest(unittest.TestCase):
    def test_parse_and_hook_bytes(self):
        m = parse_smod_v2(make_mod())
        self.assertEqual((m["name"], m["code_size"], m["bss_size"], len(m["hooks"])), ("Test", 4, 16, 1))
        self.assertEqual(hook_bytes(m["hooks"][0], 0x40010000)[8:], struct.pack("<I", 0x40010001))

    def test_rejects_bad_hash_and_fixup(self):
        bad = bytearray(make_mod())
        bad[-1] ^= 0xFF
        with self.assertRaises(ValueError):
            parse_smod_v2(bytes(bad))
        with self.assertRaises(ValueError):
            parse_smod_v2(make_mod(fixup=10))

    def test_parse_result(self):
        self.assertEqual(parse_result("reload_ok 40010000\n"), ("reload_ok", 0x40010000))
        self.assertIsNone(parse_result(""))
        self.assertIsNone(parse_result("PENDING"))

    @unittest.skipUnless(MTF.exists(), "MenuTouchFull v2 build not present")
    def test_menu_touch_full_build(self):
        m = parse_smod_v2(MTF.read_bytes())
        self.assertEqual([h["target"] for h in m["hooks"]], [0x06309D48, 0x06309E54])
        self.assertEqual(m["bss_size"], 632)


if __name__ == "__main__":
    unittest.main()
