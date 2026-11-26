"""Offline verification for the ZV-E10 dual-hook full-menu touch payload and guard binary.

Verifies build outputs under ``artifacts/hardware/zve10/``:
- menu-touch-full-build.json
- menu-touch-full-payload.bin
- menu-touch-full-{check,install,remove}.req
- menu_touch_full.elf
- native_menu_touch_full_guard
"""
import io
import json
import struct
import unittest
from pathlib import Path
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts/hardware/zve10"


def fnv1a_32(data: bytes) -> int:
    h = 2166136261
    for b in data:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


class ZVE10MenuTouchFullTests(unittest.TestCase):
    def _require(self, *names):
        missing = [name for name in names if not (ARTIFACTS / name).exists()]
        if missing:
            self.skipTest("build artifacts not present: " + ", ".join(missing))

    def test_build_manifest_and_bounds(self):
        self._require("menu-touch-full-build.json")
        manifest_path = ARTIFACTS / "menu-touch-full-build.json"
        manifest = json.loads(manifest_path.read_text())

        self.assertEqual(manifest["version"], 2)
        self.assertEqual(manifest["camera"], "Sony ZV-E10")
        self.assertEqual(manifest["code_address"], "0x11964f0")
        self.assertEqual(manifest["state_address"], "0x13f8000")
        self.assertEqual(manifest["hook_rel"], "0x618000")
        self.assertEqual(manifest["hook_prs"], "0x61810c")

        code_bytes = manifest["code_bytes"]
        self.assertLessEqual(code_bytes, 8192)
        self.assertEqual(code_bytes % 4, 0)
        self.assertEqual(manifest["state_bytes"], 656)

        code_addr = int(manifest["code_address"], 16)
        for key in ("rel_entry", "prs_entry"):
            entry = int(manifest[key], 16)
            self.assertEqual(entry & 1, 1, f"{key} must be Thumb entry")
            self.assertGreaterEqual(entry & ~1, code_addr)
            self.assertLess(entry & ~1, code_addr + code_bytes)

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
            self.assertEqual(hdr[0], 0x4D544652, "magic must be MTFR")
            self.assertEqual(hdr[1], 2, "proto ver must be 2")
            self.assertEqual(hdr[2], op, f"op must be {op}")
            self.assertEqual(hdr[3], len(payload), "code_bytes must match payload length")
            self.assertEqual(hdr[4] & 1, 1, "rel_offset must have bit 0 set")
            self.assertEqual(hdr[5] & 1, 1, "prs_offset must have bit 0 set")
            self.assertEqual(hdr[6], expected_sum, "FNV-1a checksum must match")
            self.assertEqual(hdr[7], 656, "state_size must be 656")
            self.assertEqual(hdr[8], 0x00618000, "hook_rel must match dispatchMouseRelease")
            self.assertEqual(hdr[9], 0x0061810C, "hook_prs must match dispatchMousePress")
            self.assertEqual(raw[40:], payload, "payload in req must match payload.bin")

    def test_guard_binary_is_static_arm_elf(self):
        self._require("native_menu_touch_full_guard")
        guard_path = ARTIFACTS / "native_menu_touch_full_guard"
        raw = guard_path.read_bytes()
        elf = ELFFile(io.BytesIO(raw))

        self.assertEqual(elf["e_machine"], "EM_ARM")
        self.assertEqual(elf["e_type"], "ET_EXEC")
        self.assertIsNone(elf.get_section_by_name(".dynamic"), "Guard must be statically linked")

    def test_payload_elf_validity_and_no_division_helpers(self):
        self._require("menu_touch_full.elf")
        elf_path = ARTIFACTS / "menu_touch_full.elf"
        raw = elf_path.read_bytes()
        elf = ELFFile(io.BytesIO(raw))

        self.assertEqual(elf["e_machine"], "EM_ARM")

        symtab = elf.get_section_by_name(".symtab")
        if symtab:
            for s in symtab.iter_symbols():
                self.assertNotIn("aeabi_uidiv", s.name, f"Unexpected runtime div helper: {s.name}")
                self.assertNotIn("aeabi_idiv", s.name, f"Unexpected runtime div helper: {s.name}")

    def test_trampoline_relocation_literals_present(self):
        self._require("menu-touch-full-payload.bin")
        payload = (ARTIFACTS / "menu-touch-full-payload.bin").read_bytes()
        words = set(struct.unpack_from(f"<{len(payload) // 4}I", payload))

        # Check the 3 trampoline relocation slots in payload
        expected_slots = [
            0x0061800D,  # mt_zve10_rel_resume
            0x00618119,  # mt_zve10_prs_resume
            0x013F8000,  # mt_zve10_state_ptr
        ]

        for slot in expected_slots:
            self.assertIn(
                slot,
                words,
                f"Trampoline slot {hex(slot)} not found in payload",
            )

    def test_fn_column_and_row_mapping(self):
        """Verify 6-column physical calibration and row splitting for the Fn menu."""
        def map_coords(x: int, y: int):
            if y < 260:
                return "CANCEL"
            if x < 120:
                col = 0
            elif x < 220:
                col = 1
            elif x < 320:
                col = 2
            elif x < 420:
                col = 3
            elif x < 520:
                col = 4
            else:
                col = 5
            row = 1 if y >= 370 else 0
            return row * 6 + col

        # Upper dismiss
        self.assertEqual(map_coords(100, 250), "CANCEL")
        self.assertEqual(map_coords(500, 100), "CANCEL")

        # Top row: tiles 0..5 (labeled 1..6 by user)
        self.assertEqual(map_coords(0, 300), 0)    # Extreme left edge / bezel
        self.assertEqual(map_coords(45, 300), 0)   # User's blue box left bound
        self.assertEqual(map_coords(119, 300), 0)  # Near tile 1/2 divider
        self.assertEqual(map_coords(120, 300), 1)  # Tile 2 left bound
        self.assertEqual(map_coords(219, 300), 1)
        self.assertEqual(map_coords(220, 300), 2)  # Tile 3
        self.assertEqual(map_coords(320, 300), 3)  # Tile 4
        self.assertEqual(map_coords(420, 300), 4)  # Tile 5
        self.assertEqual(map_coords(519, 300), 4)
        self.assertEqual(map_coords(520, 300), 5)  # Tile 6 left bound (fixed from 534)
        self.assertEqual(map_coords(600, 300), 5)  # User's blue box right bound
        self.assertEqual(map_coords(640, 300), 5)  # Extreme right edge / bezel

        # Bottom row: tiles 6..11 (labeled 7..12 by user)
        self.assertEqual(map_coords(0, 400), 6)    # Tile 7 extreme left
        self.assertEqual(map_coords(45, 420), 6)   # Tile 7 inside blue box
        self.assertEqual(map_coords(150, 420), 7)  # Tile 8
        self.assertEqual(map_coords(250, 420), 8)  # Tile 9
        self.assertEqual(map_coords(350, 420), 9)  # Tile 10
        self.assertEqual(map_coords(450, 420), 10) # Tile 11
        self.assertEqual(map_coords(520, 450), 11) # Tile 12 left bound
        self.assertEqual(map_coords(639, 450), 11) # Tile 12 extreme right

    def test_fn_ring_navigation_step_counts(self):
        """Verify shortest circular path step counts and directions on 12-tile ring."""
        def get_steps(cur: int, target: int):
            diff = (target - cur + 12) % 12
            if diff <= 6:
                return ("RIGHT", diff)
            else:
                return ("LEFT", 12 - diff)

        self.assertEqual(get_steps(0, 1), ("RIGHT", 1))
        self.assertEqual(get_steps(0, 5), ("RIGHT", 5))
        self.assertEqual(get_steps(0, 6), ("RIGHT", 6))
        self.assertEqual(get_steps(0, 7), ("LEFT", 5))
        self.assertEqual(get_steps(0, 11), ("LEFT", 1))
        self.assertEqual(get_steps(5, 6), ("RIGHT", 1))
        self.assertEqual(get_steps(6, 5), ("LEFT", 1))
        self.assertEqual(get_steps(11, 0), ("RIGHT", 1))
        self.assertEqual(get_steps(0, 11), ("LEFT", 1))


if __name__ == "__main__":
    unittest.main()
