"""Unit tests for ported CXD90045 peripheral models."""
import unittest

from src.a6400.devices.cxd90045_peripherals import (
    ADC_BASE,
    CxdPeripheralBus,
    CLKGEN_BASE,
    ENGINE_FRAME_US,
    EVSEL_BASE,
    F2A_BASES,
    GPIO_BASE,
    IRQ_EVSEL_A,
    IRQ_SYSV,
    IRQ_VSYNC_A,
    LDEC_BASE,
    LDEC_IN_BASE,
    LDEC_IRQ,
    LEO_CLK_BASE,
    SIO_BASE,
    SIO_IRQ0,
    SYSV_BASE,
    VPH_BASE,
    VSYNC_US,
    XDMAC0_BASE,
    XDMAC0_IRQ_BASE,
    ldec_lz77_decode,
    submicro_reply,
)


class CxdPeripheralsTests(unittest.TestCase):
    def test_f2a_clk_and_leo_clk_status_bits(self):
        irqs = {}
        bus = CxdPeripheralBus(
            set_irq=lambda irq, lvl: irqs.__setitem__(irq, lvl),
            mem_read=lambda addr, n: b'\x00' * n,
            mem_write=lambda addr, data: None,
        )
        for base in F2A_BASES:
            self.assertEqual(bus.read(base + 0x04) & 0x1, 0x1)
            self.assertEqual(bus.read(base + 0x2C) & 0x2, 0x2)
            bus.write(base + 0x30, 0x1)
            self.assertEqual(bus.read(base + 0x30), 0)
        self.assertEqual(bus.read(LEO_CLK_BASE + 0x50) & 0x1, 0x1)
        self.assertEqual(bus.read(LEO_CLK_BASE + 0x54) & 0x1, 0x1)
        self.assertEqual(bus.read(LEO_CLK_BASE + 0x58) & 0x1, 0x1)
        # DWC3 unmodelled window must read 0 after writes (matches QEMU E0579)
        self.assertTrue(bus.write(0xF020C704, 0x40000000))
        self.assertEqual(bus.read(0xF020C704), 0)

    def test_ldec_lz77_decode_and_xdmac_transfer(self):
        # Literal block kind=0x0F: 1 skip byte, 4-byte count LE, payload b'SONY'
        compressed = bytes([0x0F, 0x00, 0x04, 0x00]) + b'SONY'
        self.assertEqual(ldec_lz77_decode(compressed, 64), b'SONY')

        ram = bytearray(0x4000)
        ram[0x100:0x100 + len(compressed)] = compressed
        irqs = {}

        def mem_read(addr, n):
            return bytes(ram[addr:addr + n])

        def mem_write(addr, data):
            ram[addr:addr + len(data)] = data

        bus = CxdPeripheralBus(
            set_irq=lambda irq, lvl: irqs.__setitem__(irq, lvl),
            mem_read=mem_read,
            mem_write=mem_write,
        )
        # Program LDEC records=1, dst_phys=0x1000
        bus.write(LDEC_BASE + 0x0C, 1)
        bus.write(LDEC_BASE + 0x10, 0x1000)
        # Program XDMAC0 ch0: TBC=len-1, SSA=0x100, DSA=LDEC_IN_BASE, kick +0x18
        bus.write(XDMAC0_BASE + 0x10 + 0x00, len(compressed) - 1)
        bus.write(XDMAC0_BASE + 0x10 + 0x04, 0x100)
        bus.write(XDMAC0_BASE + 0x10 + 0x08, LDEC_IN_BASE)
        bus.write(XDMAC0_BASE + 0x10 + 0x18, 1)

        self.assertEqual(bytes(ram[0x1000:0x1004]), b'SONY')
        self.assertTrue(irqs.get(LDEC_IRQ))
        self.assertTrue(irqs.get(XDMAC0_IRQ_BASE))
        # Acknowledge LDEC and XDMAC0 ch0 IRQs
        bus.write(LDEC_BASE + 0x30, 0x1)
        bus.write(XDMAC0_BASE + 0x10 + 0x20, 0)
        self.assertFalse(irqs.get(LDEC_IRQ))
        self.assertFalse(irqs.get(XDMAC0_IRQ_BASE))

    def _bus(self, ram=None):
        store = ram if ram is not None else bytearray()

        def mem_read(addr, n):
            return bytes(store[addr:addr + n])

        return CxdPeripheralBus(
            set_irq=lambda irq, lvl: None,
            mem_read=mem_read,
            mem_write=lambda addr, data: None,
        )

    def test_sysv_frame_and_vph_vsync_follow_the_qemu_clock(self):
        bus = self._bus()
        bus.write(EVSEL_BASE + 0xEC, 1)
        bus.write(EVSEL_BASE + 0x10, 1)
        pulses = bus.advance_us(VSYNC_US)
        self.assertIn(IRQ_VSYNC_A, pulses)
        self.assertEqual(bus.vph.vsyncs, 1)
        self.assertEqual(bus.read(SYSV_BASE + 0x98), 0)
        bus.write(VPH_BASE + 0x1200, 1)
        pulses = bus.advance_us(ENGINE_FRAME_US - VSYNC_US)
        self.assertIn(IRQ_SYSV, pulses)
        self.assertEqual(bus.read(SYSV_BASE + 0x98), 1)
        self.assertEqual(bus.read(SYSV_BASE + 0x28) & 1, 1)
        self.assertEqual(bus.read(VPH_BASE + 0x1200) & 1, 0)
        self.assertEqual(bus.read(VPH_BASE + 0x1204) & 0x80000000, 0)
        self.assertTrue(bus.evsel.irq_a())
        self.assertEqual(bus.level_irqs()[0], (IRQ_EVSEL_A, True))
        bus.write(EVSEL_BASE + 0x08, 1)
        self.assertFalse(bus.evsel.irq_a())

    def test_evsel_comparator_fires_once_the_counter_passes(self):
        bus = self._bus()
        bus.write(EVSEL_BASE + 0xF8, 1)
        bus.write(EVSEL_BASE + 0x114, 10)
        bus.write(EVSEL_BASE + 0x110, 1)
        self.assertFalse(bus.evsel.irq_b())
        bus.advance_us(10)
        self.assertTrue(bus.evsel.irq_b())
        self.assertEqual(bus.read(EVSEL_BASE + 0xE0), 10)

    def test_vph_composes_rgba4444_osd(self):
        # Opaque red RGBA4444 0xF00F, one pixel, one line.
        ram = bytearray(16)
        ram[4:6] = (0xF00F).to_bytes(2, 'little')
        bus = self._bus(ram)
        bus.write(VPH_BASE + 0x1320, 4)  # ch53 base
        bus.write(VPH_BASE + 0x1324, 2)  # bytes per line
        bus.write(VPH_BASE + 0x132C, 0)  # lines - 1
        bus.vph._compose_panel()
        self.assertIsNotNone(bus.vph.panel)
        self.assertEqual(bus.vph.panel[2], 255)
        self.assertEqual(bus.vph.panel[0], 0)
        self.assertEqual(bus.vph.panel[1], 0)

    def test_oracle_sio_status_and_gpio_port1_input(self):
        irqs = {}
        bus = CxdPeripheralBus(
            set_irq=lambda irq, level: irqs.__setitem__(irq, level),
            mem_read=lambda addr, n: b'',
            mem_write=lambda addr, data: None,
        )
        # Oracle e0677: ch3 status writes, then the config word reads back.
        channel3 = SIO_BASE + 3 * 0x200
        bus.write(channel3, 0x80000000)
        self.assertEqual(bus.read(channel3), 0)
        self.assertIs(irqs.get(SIO_IRQ0 + 3), False)
        bus.write(channel3, 0x40000000)
        self.assertEqual(bus.read(channel3), 0)
        bus.write(channel3, 0x18000000)
        self.assertEqual(bus.read(channel3), 0x18000000)
        bus.write(channel3, 0x38000000)
        self.assertEqual(bus.read(channel3), 0x38000000)
        self.assertIs(irqs.get(SIO_IRQ0 + 3), True)
        # Port 1 pins 1, 4 and 5 are high at reset: 0x32.
        self.assertEqual(bus.read(GPIO_BASE + 0x110), 0x32)

    def test_submicro_slot_selects_captured_frame(self):
        frames = bytes([0x20]) + b'\x11' * 127 + bytes([0x60]) + b'\x22' * 127
        frames += bytes([0x30]) + b'\x33' * 127
        self.assertEqual(submicro_reply(frames, bytes([0x21]) + b'\x00' * 127, 128)[0], 0x20)
        self.assertEqual(submicro_reply(frames, bytes([0x61]) + b'\x00' * 127, 128)[0], 0x60)
        self.assertEqual(submicro_reply(frames, bytes([0x31]) + b'\x00' * 127, 128)[0], 0x30)

    def test_captured_submicro_page_matches_qemu_default(self):
        from project_paths import PROJECT_ROOT
        from src.a6400.devices.cxd90045_peripherals import load_submicro_frames
        frames = load_submicro_frames(PROJECT_ROOT)
        if frames is None:
            self.skipTest("local captured sub-micro binary not present")
        self.assertEqual(len(frames), 384)
        self.assertEqual(frames[:4], bytes.fromhex('20002200'))

    def test_load_submicro_frames_returns_none_when_absent(self):
        import tempfile
        from pathlib import Path
        from src.a6400.devices.cxd90045_peripherals import load_submicro_frames
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_submicro_frames(Path(tmp)))

    def test_adc_unit1_done_and_released_keys(self):
        irqs = {}
        bus = CxdPeripheralBus(
            set_irq=lambda irq, level: irqs.__setitem__(irq, level),
            mem_read=lambda addr, n: b'',
            mem_write=lambda addr, data: None,
        )
        unit1 = ADC_BASE + 0x1000
        self.assertEqual(bus.read(unit1 + 0x14), 0x3FF)
        self.assertEqual(bus.read(unit1 + 0x10), 0)
        bus.write(unit1 + 0x0C, 0x110)
        self.assertEqual(bus.read(unit1 + 0x0C), 0x100)
        self.assertIs(irqs.get(216), False)
        bus.write(unit1 + 0x04, 1)
        self.assertEqual(bus.read(unit1 + 0x0C), 0x101)
        self.assertIs(irqs.get(216), True)
        bus.write(unit1 + 0x0C, 0x110)
        self.assertEqual(bus.read(unit1 + 0x0C), 0x100)
        self.assertIs(irqs.get(216), False)

    def test_clkgen_readback_keeps_divider_upper_bits(self):
        bus = CxdPeripheralBus(
            set_irq=lambda irq, level: None,
            mem_read=lambda addr, n: b'',
            mem_write=lambda addr, data: None,
        )
        bus.write(CLKGEN_BASE + 0x84, 0x40000070)
        self.assertEqual(bus.read(CLKGEN_BASE + 0x84), 0x40000070)

    def test_submicro_strobe_pulses_port0_pin8(self):
        irqs = {}
        bus = CxdPeripheralBus(
            set_irq=lambda irq, level: irqs.__setitem__(irq, level),
            mem_read=lambda addr, n: b'',
            mem_write=lambda addr, data: None,
        )
        port0 = GPIO_BASE + 0x10
        self.assertEqual(bus.read(port0) & 0x100, 0)
        bus.advance_us(49_999)
        self.assertEqual(bus.read(port0) & 0x100, 0)
        bus.advance_us(1)
        self.assertEqual(bus.read(port0) & 0x100, 0x100)
        self.assertIs(irqs.get(129), True)
        bus.advance_us(1_000)
        self.assertEqual(bus.read(port0) & 0x100, 0)

    def test_sa_core_id_and_go_completes_after_100us(self):
        irqs = {}
        bus = CxdPeripheralBus(
            set_irq=lambda irq, level: irqs.__setitem__(irq, level),
            mem_read=lambda addr, n: b'',
            mem_write=lambda addr, data: None,
        )
        # Oracle e0677: ZMA0 ID words at 0xF34A00F0.
        self.assertEqual(bus.read(0xF34A00F0), 0x30414D5A)
        self.assertEqual(bus.read(0xF34A00F4), 0x302E3156)
        ident = b''.join(bus.read(0xF35C00F0 + 4 * i).to_bytes(4, 'little') for i in range(4))
        self.assertEqual(ident, b'SA1_V1.0SA5_V1.0')
        bus.write(0xF34A0108, 0x01)
        bus.write(0xF34A0000, 1)
        bus.write(0xF34A0004, 1)
        self.assertEqual(bus.read(0xF34A0004), 1)
        self.assertEqual(bus.read(0xF34A0104), 0)
        bus.advance_us(100)
        self.assertEqual(bus.read(0xF34A0004), 0)
        self.assertEqual(bus.read(0xF34A0104), 1)
        self.assertIs(irqs.get(51), True)
        bus.write(0xF34A0104, 1)
        self.assertEqual(bus.read(0xF34A0104), 0)
        self.assertIs(irqs.get(51), False)

    def test_imager_lwaitstat_bit_stays_set(self):
        bus = CxdPeripheralBus(
            set_irq=lambda irq, level: None,
            mem_read=lambda addr, n: b'',
            mem_write=lambda addr, data: None,
        )
        self.assertEqual(bus.read(0xFC02A080) & 1, 1)
        bus.write(0xFC02A080, 0)
        self.assertEqual(bus.read(0xFC02A080) & 1, 1)

    def test_timer_channel11_oneshot_clears_start(self):
        irqs = {}
        bus = CxdPeripheralBus(
            set_irq=lambda irq, level: irqs.__setitem__(irq, level),
            mem_read=lambda addr, n: b'',
            mem_write=lambda addr, data: None,
        )
        ctrl = 0xF2403000 + 0xB00
        bus.write(ctrl + 0x8, 1000)
        bus.write(ctrl, 0x1112)
        self.assertEqual(bus.read(ctrl), 0x1112)
        self.assertEqual(bus.read(ctrl + 4), 0)
        bus.advance_us(999)
        self.assertEqual(bus.read(ctrl) & (1 << 12), 1 << 12)
        bus.advance_us(1)
        self.assertEqual(bus.read(ctrl) & (1 << 12), 0)
        self.assertEqual(bus.read(ctrl + 4), 1)
        self.assertIs(irqs.get(164), True)

    def test_p2s_start_clears_busy_and_raises_done(self):
        irqs = {}
        bus = CxdPeripheralBus(
            set_irq=lambda irq, level: irqs.__setitem__(irq, level),
            mem_read=lambda addr, n: b'',
            mem_write=lambda addr, data: None,
        )
        bus.write(0xF3C00908, 1)
        self.assertEqual(bus.read(0xF3C00908) & 1, 1)
        self.assertEqual(bus.read(0xF3C00904) & 1, 0)
        bus.advance_us(50)
        self.assertEqual(bus.read(0xF3C00908) & 1, 0)
        self.assertEqual(bus.read(0xF3C00904) & 1, 1)
        self.assertIs(irqs.get(38), True)
        bus.write(0xF3C00904, 1)
        self.assertEqual(bus.read(0xF3C00904) & 1, 0)
        self.assertIs(irqs.get(38), False)


if __name__ == '__main__':
    unittest.main()
