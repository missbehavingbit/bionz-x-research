"""Tests for the ModsTabLook SMOD packer (E0522); no firmware data."""

from __future__ import annotations

import struct
import unittest

from tools.research import build_mods_tab_look as b
from tools.research import pack_mod


class ModsTabLookTests(unittest.TestCase):
    def test_hook_bytes_for_2mod4_site(self) -> None:
        hb = b.hook_bytes(0x11434399)
        self.assertEqual(len(hb), len(b.HOOK_ORIG))
        self.assertEqual(hb[:2], bytes.fromhex("00bf"))          # nop (site is 2 mod 4)
        self.assertEqual(hb[2:6], bytes.fromhex("dff800f0"))     # ldr.w pc, [pc, #0] at a 4-aligned address
        self.assertEqual(struct.unpack_from("<I", hb, 6)[0], 0x11434399)
        self.assertEqual((b.HOOK_ADDR + 2) % 4, 0)

    def test_rejects_arm_entry(self) -> None:
        with self.assertRaises(ValueError):
            b.hook_bytes(0x11434398)

    def test_packed_layout(self) -> None:
        code = b"\x00\xbf" * 8
        blob = pack_mod.pack_smod("Mods Tab Look", b.CODE_ADDR, code, [(b.HOOK_ADDR, b.HOOK_ORIG, b.hook_bytes(b.CODE_ADDR | 1))])
        hdr = struct.unpack_from(pack_mod.SMOD_HEADER_FMT, blob, 0)
        self.assertEqual(hdr[4], b.CODE_ADDR)
        self.assertEqual(hdr[8], 1)
        hk = struct.unpack_from(pack_mod.SMOD_HOOK_FMT, blob, pack_mod.SMOD_HEADER_SIZE)
        self.assertEqual(hk[0], b.HOOK_ADDR)
        self.assertEqual(hk[2][:12], b.HOOK_ORIG)


if __name__ == "__main__":
    unittest.main()
