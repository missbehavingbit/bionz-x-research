"""Opt-in QEMU sony-a6400 0xF30A memory-fill engine (E0642, E0681).

Replays av-cam ``entry_pre``'s register sequence on a constructed buffer and checks that only the
observed zero fill executes: ``CTRL=0x20`` prepares, destination/length/fill words are programmed,
``CTRL=0x21`` starts a synchronous fill, and busy reads 0. Non-zero fill words and a zero length
are refused, and the default machine keeps the engine unmodelled.
"""

import unittest

from tests.test_qemu_emmc import NATIVE_AVAILABLE
from tests import test_qemu_liveview

F30A = 0xF30A0000
CTRL = F30A + 0x1280
STATUS = F30A + 0x1284
DST = F30A + 0x12A0
LEN = F30A + 0x12A4
FILL_WORDS = [F30A + off for off in (0x12AC, 0x12B4, 0x12BC, 0x12C4)]
BUFFER = 0x00500000
DIRTY = 0xDEAD0000


@unittest.skipUnless(NATIVE_AVAILABLE, 'Optional WSL/Linux sony-a6400 QEMU build absent')
class QemuF30aFillTests(unittest.TestCase):
    start_qtest = test_qemu_liveview.QemuLiveViewPipelineTests.start_qtest
    _stop_proc = test_qemu_liveview.QemuLiveViewPipelineTests._stop_proc
    qtest = test_qemu_liveview.QemuLiveViewPipelineTests.qtest

    def dirty(self, proc, pending, words=8):
        for i in range(words):
            self.qtest(proc, pending, f'writel {BUFFER + 4 * i:#x} {DIRTY:#x}')

    def program(self, proc, pending, dst=BUFFER, length=32, words=(0, 0, 0, 0), start=True):
        q = lambda text: self.qtest(proc, pending, text)
        q(f'writel {CTRL:#x} 0x20')
        q(f'writel {DST:#x} {dst:#x}')
        q(f'writel {LEN:#x} {length:#x}')
        for register, value in zip(FILL_WORDS, words):
            q(f'writel {register:#x} {value:#x}')
        if start:
            q(f'writel {CTRL:#x} 0x21')

    def test_zero_fill_clears_the_destination(self):
        proc, _ = self.start_qtest('sony-a6400,f30a-fill=on')
        pending = [b'']
        self.dirty(proc, pending)
        self.program(proc, pending)
        for i in range(8):
            self.assertEqual(self.qtest(proc, pending, f'readl {BUFFER + 4 * i:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {STATUS:#x}') & 0x80000000, 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {CTRL:#x}'), 0x21)
        self.assertEqual(self.qtest(proc, pending, f'readl {DST:#x}'), BUFFER)
        self.assertEqual(self.qtest(proc, pending, f'readl {LEN:#x}'), 32)

    def test_non_zero_fill_word_is_refused(self):
        proc, _ = self.start_qtest('sony-a6400,f30a-fill=on')
        pending = [b'']
        self.dirty(proc, pending)
        self.program(proc, pending, words=(0, 0, 0, 1))
        for i in range(8):
            self.assertEqual(self.qtest(proc, pending, f'readl {BUFFER + 4 * i:#x}'), DIRTY)

    def test_zero_length_is_refused(self):
        proc, _ = self.start_qtest('sony-a6400,f30a-fill=on')
        pending = [b'']
        self.dirty(proc, pending)
        self.program(proc, pending, length=0)
        for i in range(8):
            self.assertEqual(self.qtest(proc, pending, f'readl {BUFFER + 4 * i:#x}'), DIRTY)

    def test_unaligned_destination_is_refused(self):
        proc, _ = self.start_qtest('sony-a6400,f30a-fill=on')
        pending = [b'']
        self.dirty(proc, pending)
        self.program(proc, pending, dst=BUFFER + 2)
        for i in range(8):
            self.assertEqual(self.qtest(proc, pending, f'readl {BUFFER + 4 * i:#x}'), DIRTY)

    def test_default_machine_keeps_the_engine_unmodelled(self):
        proc, _ = self.start_qtest('sony-a6400')
        pending = [b'']
        self.dirty(proc, pending)
        self.program(proc, pending)
        for i in range(8):
            self.assertEqual(self.qtest(proc, pending, f'readl {BUFFER + 4 * i:#x}'), DIRTY)
        # The default board maps no device here, so the prepare write does not read back.
        self.assertEqual(self.qtest(proc, pending, f'readl {CTRL:#x}'), 0)


if __name__ == '__main__':
    unittest.main()
