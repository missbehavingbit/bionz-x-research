"""Opt-in QEMU sony-a6400 work DMA copy channel at 0xF0102000 (E0641).

Replays the register sequence of av-cam 0x635DC8B8..0x635DC93E (E0640) on a
constructed buffer and checks that only that configuration copies data.
"""

import unittest

from tests.test_qemu_emmc import NATIVE_AVAILABLE
from tests import test_qemu_liveview

WDMA = 0xF0102000
SRC = 0x00500000
DST = 0x00600000
WORDS = [0x11111111 * (i % 15 + 1) ^ i for i in range(0x1000 + 3)]


@unittest.skipUnless(NATIVE_AVAILABLE, 'Optional WSL/Linux sony-a6400 QEMU build absent')
class QemuWorkDmaTests(unittest.TestCase):
    start_qtest = test_qemu_liveview.QemuLiveViewPipelineTests.start_qtest
    _stop_proc = test_qemu_liveview.QemuLiveViewPipelineTests._stop_proc
    qtest = test_qemu_liveview.QemuLiveViewPipelineTests.qtest

    def program(self, proc, pending, cfg, src=SRC, dst=DST, enable=True):
        q = lambda text: self.qtest(proc, pending, text)
        if enable:
            q(f'writel {WDMA + 0x030:#x} 1')
        q(f'writel {WDMA + 0x008:#x} 0x40')
        q(f'writel {WDMA + 0x00C:#x} 0x40')
        q(f'writel {WDMA + 0x1C8:#x} 0')
        q(f'writel {WDMA + 0x1C0:#x} {src:#x}')
        q(f'writel {WDMA + 0x1C4:#x} {dst:#x}')
        q(f'writel {WDMA + 0x1CC:#x} {cfg:#x}')
        q(f'writel {WDMA + 0x1D0:#x} 1')
        return q(f'readl {WDMA + 0x01C:#x}')

    def fill_source(self, proc, pending, count):
        for i in range(count):
            self.qtest(proc, pending, f'writel {SRC + 4 * i:#x} {WORDS[i]:#x}')

    def test_full_and_partial_chunks_copy_words(self):
        proc, root = self.start_qtest('sony-a6400,work-dma=on')
        pending = [b'']
        self.fill_source(proc, pending, 0x1000 + 3)
        # Full chunk: count field 0 means 0x1000 words (16 KiB).
        self.assertEqual(self.program(proc, pending, 0x1C491000) & 0x40, 0)
        for i in (0, 1, 0x7FF, 0xFFF):
            self.assertEqual(self.qtest(proc, pending, f'readl {DST + 4 * i:#x}'), WORDS[i])
        self.assertEqual(self.qtest(proc, pending, f'readl {DST + 0x4000:#x}'), 0)
        # Last chunk: 0x1C491000 | 3 words, continuing where the full chunk stopped.
        self.program(proc, pending, 0x1C491003, SRC + 0x4000, DST + 0x4000)
        for i in range(3):
            self.assertEqual(self.qtest(proc, pending, f'readl {DST + 0x4000 + 4 * i:#x}'),
                             WORDS[0x1000 + i])
        self.assertEqual(self.qtest(proc, pending, f'readl {DST + 0x400C:#x}'), 0)
        self.qtest(proc, pending, f'writel {WDMA + 0x030:#x} 0')
        self.assertEqual(self.qtest(proc, pending, f'readl {WDMA + 0x030:#x}'), 0)

    def test_unknown_mode_or_disabled_engine_copies_nothing(self):
        proc, _ = self.start_qtest('sony-a6400,work-dma=on')
        pending = [b'']
        self.fill_source(proc, pending, 4)
        self.program(proc, pending, 0x1C591004)                 # different mode bits
        self.assertEqual(self.qtest(proc, pending, f'readl {DST:#x}'), 0)
        self.qtest(proc, pending, f'writel {WDMA + 0x030:#x} 0')
        self.program(proc, pending, 0x1C491004, enable=False)   # engine disabled
        self.assertEqual(self.qtest(proc, pending, f'readl {DST:#x}'), 0)
        self.qtest(proc, pending, f'writel {WDMA + 0x090:#x} 1')  # start on another block
        self.assertEqual(self.qtest(proc, pending, f'readl {DST:#x}'), 0)

    def test_default_machine_keeps_engine_unmodelled(self):
        proc, _ = self.start_qtest('sony-a6400')
        pending = [b'']
        self.fill_source(proc, pending, 4)
        self.program(proc, pending, 0x1C491004)
        self.assertEqual(self.qtest(proc, pending, f'readl {DST:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {WDMA + 0x1C0:#x}'), 0)


if __name__ == '__main__':
    unittest.main()
