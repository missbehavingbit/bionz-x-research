"""Unicorn and source-contract checks for a6400_uvc_ptp_translate_setup."""
from __future__ import annotations

import io
from pathlib import Path
import unittest

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_ARM, Uc
from unicorn.arm_const import (
    UC_ARM_REG_LR,
    UC_ARM_REG_PC,
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R2,
    UC_ARM_REG_SP,
)

ROOT = Path(__file__).resolve().parents[2]
ADAPTER_SRC = ROOT / "tools/native_camera/uvc_ptp_adapter.c"
BUILD_DIR = ROOT / "artifacts/native/uvc-transport-b26-windex-fix"
ADAPTER_OBJ = BUILD_DIR / "uvc_ptp_adapter.o"
REF_OBJ = BUILD_DIR / "reference.rebound.o"


class UvcPtpAdapterTests(unittest.TestCase):
    def test_source_maps_non_primary_vs_shadow_to_native_zero(self) -> None:
        text = ADAPTER_SRC.read_text(encoding="utf-8")
        self.assertIn("native[4]=0u;", text)
        self.assertNotIn("native[4]=(unsigned char)(wire_interface-1);", text)

    @unittest.skipUnless(ADAPTER_OBJ.is_file() and REF_OBJ.is_file(), "windex-fix build artifacts not present")
    def test_unicorn_translate_and_reference_vs_dispatch(self) -> None:
        adapter_elf = ELFFile(io.BytesIO(ADAPTER_OBJ.read_bytes()))
        adapter_text = adapter_elf.get_section_by_name(".text").data()
        symtab = adapter_elf.get_section_by_name(".symtab")
        fn_off = next(
            s["st_value"]
            for s in symtab.iter_symbols()
            if s.name == "a6400_uvc_ptp_translate_setup"
        )
        ref_elf = ELFFile(io.BytesIO(REF_OBJ.read_bytes()))
        ref_text = ref_elf.get_section_by_name(".text").data()

        cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        cpu.mem_map(0x100000, 0x20000)
        cpu.mem_map(0x200000, 0x10000)
        cpu.mem_map(0x300000, 0x10000)
        cpu.mem_write(0x100000, adapter_text)
        cpu.mem_write(0x110000, ref_text)

        stop_addr = [0]

        def hook(uc, address, size, user_data):
            if address == stop_addr[0]:
                uc.emu_stop()

        cpu.hook_add(UC_HOOK_CODE, hook)

        def call(addr: int, end: int, r0: int, r1: int, r2: int) -> int:
            stop_addr[0] = end
            cpu.reg_write(UC_ARM_REG_R0, r0)
            cpu.reg_write(UC_ARM_REG_R1, r1)
            cpu.reg_write(UC_ARM_REG_R2, r2)
            cpu.reg_write(UC_ARM_REG_SP, 0x30F000)
            cpu.reg_write(UC_ARM_REG_LR, 0x11F000)
            cpu.emu_start(addr, 0x11F000, count=500)
            return cpu.reg_read(UC_ARM_REG_R0)

        for wire_iface in (1, 2):
            wire_pkt = bytes([0xA1, 0x81, 0x00, 0x01, wire_iface, 0x00, 0x1A, 0x00])
            cpu.mem_write(0x200000, wire_pkt)
            cpu.mem_write(0x200100, b"\xA5" * 8)
            rc = call(0x100000 + fn_off, 0x11F000, 0x200000, wire_iface, 0x200100)
            self.assertEqual(rc, 0)
            mapped = bytes(cpu.mem_read(0x200100, 8))
            self.assertEqual(mapped[4], 0)
            self.assertEqual(mapped[:4] + mapped[5:], wire_pkt[:4] + wire_pkt[5:])

            # Feed translated packet into reference.rebound.o 0x1390 and verify it
            # reaches 0x16a4 (VS_PROBE_CONTROL -> 0x11bc/0x131c), not 0x1410.
            call(0x110000 + 0x1390, 0x110000 + 0x16A4, 0x200100, 0x200800, 0)
            self.assertEqual(cpu.reg_read(UC_ARM_REG_PC), 0x110000 + 0x16A4)

            # Entity ID in wIndex high byte must be preserved.
            ent_pkt = bytes([0xA1, 0x81, 0x00, 0x01, wire_iface, 0x02, 0x1A, 0x00])
            cpu.mem_write(0x200000, ent_pkt)
            rc = call(0x100000 + fn_off, 0x11F000, 0x200000, wire_iface, 0x200100)
            self.assertEqual(rc, 0)
            self.assertEqual(bytes(cpu.mem_read(0x200100, 8))[4:6], b"\x00\x02")


if __name__ == "__main__":
    unittest.main()
