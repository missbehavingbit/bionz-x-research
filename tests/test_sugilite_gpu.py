"""Focused unit tests for SugiliteGpuEngine and GuestPhysicalRam L3 contracts."""

import struct
import unittest

import unicorn

from src.a6400.execution.guest_physical_ram import GuestPhysicalRam
from src.a6400.execution.sugilite_gpu import (
    ST_P3D,
    ST_PPF,
    ST_PSC0,
    SUGILITE_IRQ_NUMBER,
    SugiliteGpuEngine,
)


def _f32_u32(val: float) -> int:
    return struct.unpack('<I', struct.pack('<f', val))[0]


def _cmd_pair(reg: int, val: int, mask: int = 0xF) -> bytes:
    hdr = ((reg & 0xFFF) << 16) | ((mask & 0xF) << 12)
    return struct.pack('<II', hdr, val & 0xFFFFFFFF)


class SugiliteGpuAndPhysicalRamTests(unittest.TestCase):
    def setUp(self):
        self.mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_ARM)

    def tearDown(self):
        del self.mu

    def test_p3d_shader_triangle_draw_irq76_and_ppf_detile_to_osd(self):
        ram = GuestPhysicalRam()
        osd_alias_va = 0x62000000
        ram.map_alias(self.mu, osd_alias_va, 0x23A80000, 0x96000, unicorn.UC_PROT_ALL, 'stream')
        gpu = SugiliteGpuEngine(ram.read_physical, ram.write_physical)

        # Clear 16x16 RGBA4444 tiled FBO at 0x202F0200 using PSC0.
        gpu.write_mmio(0x0010, 0x202F0200 >> 3)
        gpu.write_mmio(0x0014, (0x202F0200 + 512) >> 3)
        gpu.write_mmio(0x0018, 0x00000000)
        gpu.write_mmio(0x001C, 0x00000001)
        self.assertEqual(gpu.read_mmio(0x0034), ST_PSC0)
        dev_a8 = gpu.service_interrupt()
        self.assertEqual(dev_a8, ST_PSC0)
        self.assertEqual(gpu.interrupts[-1]['irq'], SUGILITE_IRQ_NUMBER)
        self.assertEqual(gpu.read_mmio(0x001C), 0)

        # 3 NDC vertices covering the lower-left half of a 16x16 RGBA4444 target.
        vbo_pa = 0x202E2000
        verts = struct.pack(
            '<12f',
            -1.0, -1.0, 0.0, 1.0,
             1.0, -1.0, 0.0, 1.0,
            -1.0,  1.0, 0.0, 1.0,
        )
        ram.write_physical(vbo_pa, verts)

        # Unified DMP shader program:
        #   pc 0 (VS): MOV o0, v0 ; pc 1: END
        #   pc 2 (FS): MOV o1, c0 ; pc 3: END
        opdesc0 = (0xF << 26) | (0xE4 << 13) | 0xE4
        vs_mov_w0 = (2 << 24) | 0
        vs_mov_w1 = (0x0C << 20) | 0x00080000 | (0 << 8)
        end_w0, end_w1 = 0, (0x4 << 12)
        fs_mov_w0 = 0
        fs_mov_w1 = (0x0C << 20) | 0x00080000 | (1 << 8) | 0x80

        cmds = b''.join([
            _cmd_pair(0x460, 0),
            _cmd_pair(0x461, opdesc0),
            _cmd_pair(0x458, 0),
            _cmd_pair(0x459, vs_mov_w0),
            _cmd_pair(0x459, vs_mov_w1),
            _cmd_pair(0x459, end_w0),
            _cmd_pair(0x459, end_w1),
            _cmd_pair(0x459, fs_mov_w0),
            _cmd_pair(0x459, fs_mov_w1),
            _cmd_pair(0x459, end_w0),
            _cmd_pair(0x459, end_w1),
            # funif[0] = (1.0, 0.0, 0.0, 1.0) -> opaque red RGBA4444 (0xF00F)
            _cmd_pair(0x450, 0),
            _cmd_pair(0x451, _f32_u32(1.0)),
            _cmd_pair(0x452, _f32_u32(0.0)),
            _cmd_pair(0x453, _f32_u32(0.0)),
            _cmd_pair(0x454, _f32_u32(1.0)),
            _cmd_pair(0x442, 0),
            _cmd_pair(0x443, 2),
            # Viewport & scissor for 16x16
            _cmd_pair(0x042, _f32_u32(8.0)),
            _cmd_pair(0x043, _f32_u32(8.0)),
            _cmd_pair(0x047, ((-16 & 0xFFFF) << 16) | (-16 & 0xFFFF)),
            _cmd_pair(0x048, (16 << 16) | 16),
            _cmd_pair(0x049, (16 << 16) | 16),
            # Render target: 16x16 RGBA4444 (t_fmt=4) at 0x202F0200
            _cmd_pair(0x110, 4 << 16),
            _cmd_pair(0x112, 0x202F0200 >> 4),
            _cmd_pair(0x113, (16 << 16) | 16),
            _cmd_pair(0x107, 0x0F00),
            _cmd_pair(0x10C, 0x000F),
            # Vertex buffer 0: 1 float4 attribute -> v[0], stride 16
            _cmd_pair(0x204, 0x0),
            _cmd_pair(0x206, 0x3),
            _cmd_pair(0x208, 0x3),
            _cmd_pair(0x209, 0x0),
            _cmd_pair(0x20D, 0x0),
            _cmd_pair(0x20E, 0x0),
            _cmd_pair(0x210, 0x0),
            _cmd_pair(0x212, 15),
            _cmd_pair(0x216, vbo_pa),
            _cmd_pair(0x203, 0),
            _cmd_pair(0x401, 0x3020),
            _cmd_pair(0x404, 0),
            _cmd_pair(0x202, 3),
        ])
        cmd_pa = 0x202E1800
        ram.write_physical(cmd_pa, cmds)
        gpu.write_mmio(0x1804, cmd_pa)
        gpu.write_mmio(0x1800, len(cmds) // 8)
        self.assertEqual(gpu.read_mmio(0x0034), ST_P3D)
        self.assertEqual(gpu.ndraw, 1)
        self.assertGreater(gpu.npix, 0)
        self.assertEqual(gpu.prog.unimplemented, [])
        dev_a8 |= gpu.service_interrupt()
        self.assertEqual(dev_a8, ST_PSC0 | ST_P3D)
        self.assertEqual(gpu.read_mmio(0x1000), 0)

        # Detile 16x16 RGBA4444 (fmt=4) from 0x202F0200 -> OSD A 0x23A80000 via PPF.
        gpu.write_mmio(0x0C00, 0x202F0200 >> 2)
        gpu.write_mmio(0x0C04, 0x23A80000 >> 2)
        gpu.write_mmio(0x0C08, (16 << 16) | 16)
        gpu.write_mmio(0x0C0C, (16 << 16) | 16)
        gpu.write_mmio(0x0C10, (4 << 12) | (4 << 8))
        gpu.write_mmio(0x0C18, 1)
        self.assertEqual(gpu.read_mmio(0x0034), ST_PPF)
        dev_a8 |= gpu.service_interrupt()
        self.assertEqual(dev_a8, ST_PSC0 | ST_P3D | ST_PPF)
        self.assertEqual(gpu.read_mmio(0x0C18), 0)

        # Lower-left pixel (x=1, y=1) inside triangle is red (0xF00F); upper-right (14, 14) is clear (0x0000).
        osd_bytes = bytes(self.mu.mem_read(osd_alias_va, 16 * 16 * 2))
        px_inside = struct.unpack_from('<H', osd_bytes, (1 * 16 + 1) * 2)[0]
        px_outside = struct.unpack_from('<H', osd_bytes, (14 * 16 + 14) * 2)[0]
        self.assertEqual(px_inside, 0xF00F)
        self.assertEqual(px_outside, 0x0000)

    def test_physical_ram_checkpoint_roundtrip_preserves_pages_and_aliases(self):
        ram = GuestPhysicalRam()
        ram.map_alias(self.mu, 0x60000000, 0x226E0000, 0x2000, unicorn.UC_PROT_ALL, 'stream')
        ram.map_alias(self.mu, 0x61000000, 0x226E0000, 0x2000, unicorn.UC_PROT_ALL, 'mem')
        self.mu.mem_write(0x60000100, b'UXC_ATLAS_DATA')
        self.assertEqual(bytes(self.mu.mem_read(0x61000100, 14)), b'UXC_ATLAS_DATA')

        state = ram.to_state()
        mu2 = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_ARM)
        try:
            restored = GuestPhysicalRam()
            restored.from_state(mu2, state)
            self.assertEqual(bytes(mu2.mem_read(0x61000100, 14)), b'UXC_ATLAS_DATA')
            restored.write_physical(0x226E0200, b'LDEC_DMA')
            self.assertEqual(bytes(mu2.mem_read(0x60000200, 8)), b'LDEC_DMA')
        finally:
            del mu2


if __name__ == '__main__':
    unittest.main()
