"""Opt-in QEMU sony-a6400 hardware watchdog (CXD4115 channel at 0xF2402000, E0636).

The register sequence is the one the stock kernel writes (nflasha3-Image
cxd900x0_wdt_setup/start/stop and watchdog_touch, observed 7,527 times per
run in the E0524 unimp access log).
"""

import unittest

from tests.test_qemu_emmc import NATIVE_AVAILABLE
from tests import test_qemu_liveview

WDT = 0xF2402000
CTRL_ARMED = 0x80000012   # cxd900x0_wdt_setup
CTRL_RUN = 0x80001012     # cxd900x0_wdt_start
CMP_500MS = 0x7A314       # wdt_setup_counts: 1001 * 500
TIMEOUT_NS = 500_500_000  # 0x7A314 ticks at 4 MHz >> 2 [I: prescaler from the CXD4115 timer]


@unittest.skipUnless(NATIVE_AVAILABLE, 'Optional WSL/Linux sony-a6400 QEMU build absent')
class QemuWatchdogTests(unittest.TestCase):
    # Reuse the qtest process helpers only, not the live-view test methods.
    start_qtest = test_qemu_liveview.QemuLiveViewPipelineTests.start_qtest
    _stop_proc = test_qemu_liveview.QemuLiveViewPipelineTests._stop_proc
    qtest = test_qemu_liveview.QemuLiveViewPipelineTests.qtest

    def start_wdt(self, machine):
        proc, root = self.start_qtest(machine)
        pending = [b'']
        self.qtest(proc, pending, f'writel {WDT:#x} 0')
        self.qtest(proc, pending, f'writel {WDT + 4:#x} 0x11')
        self.qtest(proc, pending, f'writel {WDT:#x} {CTRL_ARMED:#x}')
        self.qtest(proc, pending, f'writel {WDT + 8:#x} {CMP_500MS:#x}')
        self.qtest(proc, pending, f'writel {WDT:#x} {CTRL_RUN:#x}')
        return proc, root, pending

    def kicked_until_last_step(self, proc, pending):
        # Kicks (CLR bit 4) restart the count: 2 x 400 ms with a kick between stays alive.
        self.qtest(proc, pending, 'clock_step 400000000')
        self.qtest(proc, pending, f'writel {WDT + 4:#x} 0x10')
        self.qtest(proc, pending, 'clock_step 400000000')
        self.assertEqual(self.qtest(proc, pending, f'readl {WDT:#x}'), CTRL_RUN)
        self.assertEqual(self.qtest(proc, pending, f'readl {WDT + 8:#x}'), CMP_500MS)
        self.qtest(proc, pending, f'clock_step {TIMEOUT_NS - 400000000 - 1000}')
        self.assertEqual(self.qtest(proc, pending, f'readl {WDT:#x}'), CTRL_RUN)

    def test_default_machine_drops_watchdog_writes(self):
        proc, root, pending = self.start_wdt('sony-a6400')
        self.assertEqual(self.qtest(proc, pending, f'readl {WDT:#x}'), 0)
        self.qtest(proc, pending, f'clock_step {2 * TIMEOUT_NS}')
        self.assertEqual(self.qtest(proc, pending, f'readl {WDT:#x}'), 0)
        self.assertNotIn(b'cxd4115-wdt', (root / 'qemu.log').read_bytes())

    def test_missed_kick_resets_machine(self):
        proc, root, pending = self.start_wdt('sony-a6400,watchdog=on')
        self.kicked_until_last_step(proc, pending)
        self.qtest(proc, pending, 'clock_step 2000')
        # Default -action watchdog=reset: the device reset clears CTRL and CMP.
        self.assertEqual(self.qtest(proc, pending, f'readl {WDT:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {WDT + 8:#x}'), 0)
        log = (root / 'qemu.log').read_bytes()
        self.assertIn(b'cxd4115-wdt: ch0 expired (ctrl=0x80001012 cmp=0x0007a314)', log)

    def test_stopped_watchdog_never_expires(self):
        proc, root, pending = self.start_wdt('sony-a6400,watchdog=on')
        # cxd900x0_wdt_stop: CTRL 0, CLR 0x11.
        self.qtest(proc, pending, f'writel {WDT:#x} 0')
        self.qtest(proc, pending, f'writel {WDT + 4:#x} 0x11')
        self.qtest(proc, pending, f'clock_step {3 * TIMEOUT_NS}')
        self.assertEqual(self.qtest(proc, pending, f'readl {WDT + 8:#x}'), CMP_500MS)
        self.assertNotIn(b'cxd4115-wdt', (root / 'qemu.log').read_bytes())


if __name__ == '__main__':
    unittest.main()
