"""Unit tests for BIONZ X sensor configuration table mapping and emulation."""
from __future__ import annotations

import hashlib
import struct
import unittest
from pathlib import Path

from project_paths import PROJECT_ROOT
from src.a6400.sensor import (
    DfeAppModel,
    DfeError,
    ImagerMmioBank,
    ImagerTableError,
    MmioWrite,
    analyze_avcam_imager,
    parse_dcv_psf,
    parse_dfe_app,
    parse_idt_ssboot,
    parse_sa_module,
    scan_sabin_tree,
)
from src.a6400.sensor.dfe_app import DFE_LINK_BASE, DFE_SRAM_BASE, DfeXdmacDescriptor
from src.a6400.sensor.imager_tables import ORIL_BASE
from src.a6400.sensor.sabin import SA_MAGIC, SabinParseError


class TestSabinParsers(unittest.TestCase):
    def test_parse_sa_module_valid_and_invalid(self) -> None:
        buf = bytearray(0x40)
        struct.pack_into("<5I", buf, 0, SA_MAGIC, 0x02000005, 1, 2, 0)
        buf[0x14:0x20] = b"SA_DCV__0x13"
        hdr = parse_sa_module(bytes(buf), "sa_dcv.bin")
        self.assertEqual(hdr.name_tag, "SA_DCV__0x13")
        self.assertEqual(hdr.magic, SA_MAGIC)

        bad = bytearray(buf)
        bad[0] = 0
        with self.assertRaises(SabinParseError):
            parse_sa_module(bytes(bad), "bad.bin")

    def test_parse_dcv_psf_valid(self) -> None:
        payload = b"\x01\x02\x03\x04" * 8
        buf = bytearray(0x40 + len(payload))
        buf[0:4] = b"IMX2"
        buf[4:8] = b"10\x00\x00"
        buf[16:31] = b"SAL50 CeGenCh_3"
        buf[32:47] = b"20130607_130709"
        struct.pack_into("<4I", buf, 0x30, len(buf), 0, 0x00010003, 0x00040004)
        buf[0x40:] = payload
        hdr = parse_dcv_psf(bytes(buf), "dcv_psf_table_movie.bin")
        self.assertEqual(hdr.payload_size, len(payload))
        self.assertEqual(hdr.generator_tag, "SAL50 CeGenCh_3")

    def test_parse_idt_ssboot_valid(self) -> None:
        buf = bytearray(0x40)
        struct.pack_into("<8I", buf, 0, 0xB03F003D, 0x004F3442, 0x10080001, 0x40, 0x20, 0x40, 0x40, 0)
        hdr = parse_idt_ssboot(bytes(buf), "ssboot.bin")
        self.assertEqual(hdr.declared_size, 0x40)
        self.assertEqual(hdr.code_offset, 0x40)


class TestDfeAppModel(unittest.TestCase):
    def _make_synthetic_dfe(self) -> bytes:
        buf = bytearray(0x100)
        struct.pack_into("<4I", buf, 0, 0xEA000000, 0xE12FFF1E, 0xE28FC03B, 0xE12FFF1C)
        struct.pack_into("<2I", buf, 0x40, DFE_SRAM_BASE, DFE_LINK_BASE + 0xA1)
        buf[0x80:0x95] = b"DFE:XDMAC STATUS ERR "
        return bytes(buf)

    def test_parse_and_emulate_dfe_app(self) -> None:
        raw = self._make_synthetic_dfe()
        info = parse_dfe_app(raw)
        self.assertIn(DFE_SRAM_BASE, info.sram_regions)
        self.assertIn(DFE_LINK_BASE + 0xA1, info.entrypoints)

        model = DfeAppModel.from_bytes(raw)
        self.assertEqual(
            model.create_list([DfeXdmacDescriptor(0x1000, 0x2000, 512)]),
            1,
        )
        self.assertEqual(model.run_xdmac(), 512)
        header_count, header_flag = struct.unpack_from("<II", model.read_sram(DFE_SRAM_BASE, 8), 0)
        self.assertEqual((header_count, header_flag), (1, 1))

    def test_dfe_padding_and_bounds_errors(self) -> None:
        model = DfeAppModel.from_bytes(self._make_synthetic_dfe())
        with self.assertRaises(DfeError):
            model.run_xdmac()
        with self.assertRaises(DfeError):
            model.create_list([DfeXdmacDescriptor(0x1000, 0x2000, 7, padding_mode="cpu")])
        with self.assertRaises(DfeError):
            model.read_sram(DFE_SRAM_BASE - 4, 4)


class TestImagerMmioAndRealFirmware(unittest.TestCase):
    def test_mmio_bank_bounds_and_writes(self) -> None:
        bank = ImagerMmioBank()
        n = bank.apply_table([MmioWrite(ORIL_BASE, 0xFC0A1208, 0x12345678)])
        self.assertEqual(n, 1)
        self.assertEqual(bank.read32(0xFC0A1208), 0x12345678)
        with self.assertRaises(ImagerTableError):
            bank.write32(0xFC000000, 1)

    def test_real_a6400_sensor_tables_if_present(self) -> None:
        dev_dir = (
            PROJECT_ROOT
            / "firmware"
            / "extracted_a6400"
            / "firmware.tar_unpacked"
            / "0700_part_image"
            / "dev"
        )
        if not dev_dir.exists():
            self.skipTest("unpacked a6400 firmware not present")

        inv = scan_sabin_tree([dev_dir / "nflasha3_unpacked/sabin", dev_dir / "nflasha15_unpacked/sabin"])
        self.assertGreater(len(inv.sa_modules), 40)
        self.assertGreaterEqual(len(inv.dcv_tables), 3)
        self.assertEqual(len(inv.idt_ssboot), 3)

        dfe = parse_dfe_app((dev_dir / "nflasha3_unpacked/dfe_app.bin").read_bytes())
        self.assertEqual(dfe.size, 1956)
        self.assertEqual(len(dfe.error_strings), 5)

        avcam = analyze_avcam_imager((dev_dir / "nflasha3_unpacked/av-cam.bin").read_bytes())
        self.assertIn("IMX284E", avcam.imx_identifiers)
        self.assertGreater(len(avcam.geometries), 4)
        self.assertGreater(len(avcam.timing_entries), 10)
        self.assertIn("ZIMA_AVC/ZMA", avcam.active_encoder_engines)
        self.assertGreater(len(avcam.mmio_tables), 10)

    def test_real_a6400_opengate_3_2_unicorn_tables(self) -> None:
        from unicorn import UC_ARCH_ARM, UC_MODE_THUMB, UC_PROT_EXEC, UC_PROT_READ, UC_PROT_WRITE, Uc
        from unicorn.arm_const import (
            UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
            UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP,
        )

        firmware = PROJECT_ROOT / "firmware"
        builds = (
            (firmware / "extracted_a6400" / "firmware.tar_unpacked" / "0700_part_image"
             / "dev" / "nflasha3_unpacked" / "av-cam.bin",
             "90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e",
             0x63C9D13A, 0x63DDF414, 0x63E129E8, 0x63E13284, 0x63E12610),
            (firmware / "captured_a6400_20261113_analysis" / "av-cam.bin",
             "dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209",
             0x63CAEADA, 0x63DF199C, 0x63E24F70, 0x63E2580C, 0x63E24B98),
        )
        for path, digest, selector, buffers, geometry, timing, depth in builds:
            with self.subTest(sha256=digest):
                if not path.is_file():
                    self.skipTest(f"Local a6400 ORIL not present: {path.name} ({digest[:8]})")

                avcam = path.read_bytes()
                self.assertEqual(hashlib.sha256(avcam).hexdigest(), digest)
                uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
                uc.mem_map(ORIL_BASE, (len(avcam) + 0xFFF) & ~0xFFF, UC_PROT_READ | UC_PROT_EXEC)
                uc.mem_write(ORIL_BASE, avcam)
                stack = 0x10000000
                uc.mem_map(stack, 0x1000, UC_PROT_READ | UC_PROT_WRITE)

                for args, expected in (
                    ((6, 1, 3840, 2160), (3, 1620, 1080, 1664, 1080, 0x50B3F)),
                    ((6, 1, 3840, 1080), (6, 3240, 2160, 3264, 2160, 0x299)),
                    ((6, 2, 3840, 1080), (4, 3840, 2160, 3840, 2160, 0x299)),
                ):
                    r1, r2, r3, sp0 = args
                    uc.reg_write(UC_ARM_REG_SP, stack + 0x800)
                    uc.mem_write(stack + 0x800, struct.pack("<I", sp0))
                    uc.reg_write(UC_ARM_REG_R1, r1)
                    uc.reg_write(UC_ARM_REG_R2, r2)
                    uc.reg_write(UC_ARM_REG_R3, r3)
                    uc.reg_write(UC_ARM_REG_LR, ORIL_BASE | 1)
                    uc.emu_start(selector | 1, ORIL_BASE, timeout=100000, count=200)
                    self.assertEqual(uc.reg_read(UC_ARM_REG_PC), ORIL_BASE, "selector did not return")
                    idx = uc.reg_read(UC_ARM_REG_R0)
                    self.assertEqual(idx, expected[0])
                    width, height, aw, ah, _, pool = struct.unpack("<6I", uc.mem_read(buffers + idx * 24, 24))
                    self.assertEqual((idx, width, height, aw, ah, pool), expected)

                for mode, expected in (
                    (2, (6000, 4000, 831, 4148, 12, 0x010000)),
                    (10, (1996, 1332, 660, 1820, 12, 0x010000)),
                    (16, (6000, 3376, 862, 1740, 11, 0x020000)),
                ):
                    gw = struct.unpack("<19I", uc.mem_read(geometry + mode * 0x4C, 76))
                    h1, v1 = struct.unpack("<2I", uc.mem_read(timing + mode * 24, 8))
                    bw = struct.unpack("<8I", uc.mem_read(depth + mode * 0x20, 32))
                    self.assertEqual((gw[7], gw[15], h1, v1, (bw[4] >> 8) & 0xFF, bw[3]), expected)

    def test_real_a6400_idt_rcraw_unicorn_snapshot(self) -> None:
        avcam_path = (
            PROJECT_ROOT
            / "firmware"
            / "extracted_a6400"
            / "firmware.tar_unpacked"
            / "0700_part_image"
            / "dev"
            / "nflasha3_unpacked"
            / "av-cam.bin"
        )
        if not avcam_path.is_file():
            self.skipTest("unpacked a6400 av-cam.bin not present")

        from unicorn import UC_ARCH_ARM, UC_MODE_THUMB, UC_PROT_EXEC, UC_PROT_READ, UC_PROT_WRITE, Uc
        from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_SP

        avcam = avcam_path.read_bytes()
        mapped_size = ((len(avcam) + 0xFFF) // 0x1000) * 0x1000
        uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        uc.mem_map(ORIL_BASE, mapped_size, UC_PROT_READ | UC_PROT_EXEC)
        uc.mem_write(ORIL_BASE, avcam)

        uc.mem_map(0xF3000000, 0x1000, UC_PROT_READ | UC_PROT_WRITE)
        regs = [
            0xF3000200, 0xF3000210, 0xF3000212, 0xF3000218, 0xF300021A, 0xF3000220, 0xF3000222,
            0xF3000300, 0xF3000310, 0xF3000312, 0xF3000318, 0xF3000320, 0xF3000322,
            0xF3000400, 0xF3000410, 0xF3000412, 0xF3000418, 0xF300041A, 0xF3000420, 0xF3000422,
        ]
        for idx, reg in enumerate(regs):
            if reg in (0xF3000200, 0xF3000300):
                uc.mem_write(reg, struct.pack("<I", 0xA0000000 + idx))
            else:
                uc.mem_write(reg, struct.pack("<H", 0x2000 + idx))

        stack = 0x10000000
        uc.mem_map(stack, 0x10000, UC_PROT_READ | UC_PROT_WRITE)
        uc.reg_write(UC_ARM_REG_SP, stack + 0x8000)
        uc.reg_write(UC_ARM_REG_LR, ORIL_BASE | 1)
        uc.emu_start(0x63B3756C | 1, 0x63B3761C, timeout=100000, count=200)

        r0 = uc.reg_read(UC_ARM_REG_R0)
        r1 = uc.reg_read(UC_ARM_REG_R1)
        self.assertEqual(r1, 0x34)
        buf = bytes(uc.mem_read(r0, r1))
        self.assertEqual(struct.unpack_from("<H", buf, 0)[0], 10)
        self.assertEqual(struct.unpack_from("<I", buf, 4)[0], 0xA0000000)
        self.assertEqual(struct.unpack_from("<I", buf, 0x14)[0], 0xA0000007)
        self.assertEqual(struct.unpack_from("<H", buf, 0x30)[0], 0x2013)

    def test_real_a6400_aif_dma_table_and_raw_ext_gate(self) -> None:
        avcam_path = (
            PROJECT_ROOT
            / "firmware"
            / "extracted_a6400"
            / "firmware.tar_unpacked"
            / "0700_part_image"
            / "dev"
            / "nflasha3_unpacked"
            / "av-cam.bin"
        )
        if not avcam_path.is_file():
            self.skipTest("unpacked a6400 av-cam.bin not present")

        from unicorn import UC_ARCH_ARM, UC_MODE_THUMB, UC_PROT_EXEC, UC_PROT_READ, UC_PROT_WRITE, Uc
        from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0

        avcam = avcam_path.read_bytes()
        mapped_size = ((len(avcam) + 0xFFF) // 0x1000) * 0x1000
        uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        uc.mem_map(ORIL_BASE, mapped_size, UC_PROT_READ | UC_PROT_EXEC)
        uc.mem_write(ORIL_BASE, avcam)

        # Verify AIF DMA channel bases from 0x63E23764..0x63E238A0 in guest memory.
        aif_words = struct.unpack_from("<80I", uc.mem_read(0x63E23764, 320))
        aif_regs = {w for w in aif_words if 0xF3000000 <= w <= 0xF3FFFFFF}
        for expected in (0xF3002400, 0xF3020200, 0xF3020280, 0xF3041200, 0xF3881200, 0xF3881300):
            self.assertIn(expected, aif_regs)

        # Verify SET_RAW_EXTERNAL_OUTPUT state getter 0x63BE0C7C in Unicorn.
        uc.reg_write(UC_ARM_REG_LR, ORIL_BASE | 1)
        uc.emu_start(0x63BE0C7C | 1, ORIL_BASE, timeout=10000, count=20)
        state_va = uc.reg_read(UC_ARM_REG_R0)
        self.assertEqual(state_va, 0x64407C40)

    def test_real_a6400_rcraw_calc_unicorn(self) -> None:
        avcam_path = (
            PROJECT_ROOT
            / "firmware"
            / "extracted_a6400"
            / "firmware.tar_unpacked"
            / "0700_part_image"
            / "dev"
            / "nflasha3_unpacked"
            / "av-cam.bin"
        )
        if not avcam_path.is_file():
            self.skipTest("unpacked a6400 av-cam.bin not present")

        from unicorn import UC_ARCH_ARM, UC_MODE_THUMB, UC_PROT_EXEC, UC_PROT_READ, UC_PROT_WRITE, Uc
        from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_SP

        avcam = avcam_path.read_bytes()
        mapped_size = ((len(avcam) + 0xFFF) // 0x1000) * 0x1000
        uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        uc.mem_map(ORIL_BASE, mapped_size, UC_PROT_READ | UC_PROT_EXEC)
        uc.mem_write(ORIL_BASE, avcam)

        stack = 0x10000000
        uc.mem_map(stack, 0x10000, UC_PROT_READ | UC_PROT_WRITE)

        for flag, expected in ((1, (0x400, 0x20)), (0, (0x10, 0x10))):
            uc.reg_write(UC_ARM_REG_SP, stack + 0x8000)
            uc.reg_write(UC_ARM_REG_R0, flag)
            uc.reg_write(UC_ARM_REG_R1, stack + 0x1000)
            uc.reg_write(UC_ARM_REG_R2, stack + 0x1004)
            uc.reg_write(UC_ARM_REG_LR, ORIL_BASE | 1)
            uc.emu_start(0x63CD278C | 1, ORIL_BASE, timeout=10000, count=50)
            self.assertEqual(struct.unpack("<2I", uc.mem_read(stack + 0x1000, 8)), expected)

        for h, shift_mode, expected in ((1332, 0, (21310, 21311)), (4000, 1, (62460, 62461))):
            buf = bytearray(0x24)
            struct.pack_into("<H", buf, 2, h)
            struct.pack_into("<4I", buf, 8, shift_mode, 0, 0, 1024)
            buf[0x1E] = 16
            uc.mem_write(stack + 0x2000, bytes(buf))
            uc.reg_write(UC_ARM_REG_SP, stack + 0x8000)
            uc.reg_write(UC_ARM_REG_R0, stack + 0x2000)
            uc.reg_write(UC_ARM_REG_LR, ORIL_BASE | 1)
            uc.emu_start(0x63CD27CA | 1, ORIL_BASE, timeout=10000, count=200)
            self.assertEqual(struct.unpack("<2H", uc.mem_read(stack + 0x201A, 4)), expected)

    def test_real_a6400_rcraw_quant_selector_unicorn(self) -> None:
        avcam_path = (
            PROJECT_ROOT
            / "firmware"
            / "extracted_a6400"
            / "firmware.tar_unpacked"
            / "0700_part_image"
            / "dev"
            / "nflasha3_unpacked"
            / "av-cam.bin"
        )
        if not avcam_path.is_file():
            self.skipTest("unpacked a6400 av-cam.bin not present")

        from unicorn import UC_ARCH_ARM, UC_MODE_THUMB, UC_PROT_EXEC, UC_PROT_READ, UC_PROT_WRITE, Uc
        from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP

        avcam = avcam_path.read_bytes()
        mapped_size = ((len(avcam) + 0xFFF) // 0x1000) * 0x1000
        uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        uc.mem_map(ORIL_BASE, mapped_size, UC_PROT_READ | UC_PROT_EXEC)
        uc.mem_write(ORIL_BASE, avcam)

        stack = 0x10000000
        uc.mem_map(stack, 0x10000, UC_PROT_READ | UC_PROT_WRITE)

        def call_sel(mode: int, r2_val: int, r3_val: int) -> tuple[int, int, int, int]:
            uc.mem_write(stack + 0x2000, b"\x00" * 16)
            uc.reg_write(UC_ARM_REG_SP, stack + 0x8000)
            uc.reg_write(UC_ARM_REG_R0, stack + 0x2000)
            uc.reg_write(UC_ARM_REG_R1, mode)
            uc.reg_write(UC_ARM_REG_R2, r2_val)
            uc.reg_write(UC_ARM_REG_R3, r3_val)
            uc.reg_write(UC_ARM_REG_LR, ORIL_BASE | 1)
            uc.emu_start(0x63B340F8 | 1, ORIL_BASE, timeout=10000, count=200)
            return struct.unpack("<4H", uc.mem_read(stack + 0x2000, 8))

        self.assertEqual(call_sel(0, 200, 1000), (0, 0x0004, 200, 1))
        self.assertEqual(call_sel(0, 300, 1000), (0, 0x0010, 286, 1))
        self.assertEqual(call_sel(0, 350, 1000), (1, 0x0020, 342, 1))
        self.assertEqual(call_sel(0, 450, 1000), (1, 0x0010, 366, 1))
        self.assertEqual(call_sel(1, 450, 1000), (1, 0x0010, 404, 1))
        self.assertEqual(call_sel(2, 450, 1000), (1, 0x0010, 440, 1))


if __name__ == "__main__":
    unittest.main()
