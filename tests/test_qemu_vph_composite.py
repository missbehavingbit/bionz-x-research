"""QEMU ``sony-a6400`` VPH panel composition: layer enables, +0x140 stage mode and OSD re-arm.

The panel output is composed from AIF ``ch51`` (YUV/luma, live view) under AIF ``ch53``
(RGBA4444 OSD).  These tests program both channels through qtest, ask QEMU for a
``screendump`` of the ``vph`` device and read the composed pixels back, so they check the
picture a viewer would see, not just register shadow state.

Contracts under test:

* ``+0x144`` bit1 enables the OSD layer and bit0 enables the YUV layer (RE_DISPLAY_SCANOUT.md;
  E0652 records the guest clearing both bits when it blanks the panel).
* A physical A6400 keeps ``+0x144 = 0x01000232`` (bit0 clear) and
  ``+0x140 = 0x30000000`` yet shows live view, so a set panel-stage mode also composes the YUV
  layer.
* ``+0x140`` bits 28-29 are write-1-to-set: a guest clear does not stick, because the camera kept
  reading ``0x30000000`` after a guest writer executed ``0x140 &= ~0x30000000``.
* The OSD is composed only for a frame the guest armed (``AIF ch53 +0x00`` bit0); a guest that
  stops re-arming loses the OSD, while a channel armed between vsyncs belongs to the frame being
  scanned.
"""

import os
import select
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from project_paths import PROJECT_ROOT
from tests.test_qemu_emmc import NATIVE_AVAILABLE, QEMU

sys.path.insert(0, str(PROJECT_ROOT))
from tools.research.qemu_boot_probe import Qmp  # noqa: E402

VPH = 0xF3440000
EN144 = VPH + 0x144
R140 = VPH + 0x140
FMT404 = VPH + 0x404
CH51 = VPH + 0x1200
CH53 = VPH + 0x1300
YUV_PA = 0x00400000        # one 640-byte luma row; ch51 lines-1 = 0 maps every panel row to it
OSD_PA = 0x00500000        # 48 rows of RGBA4444
OSD_ROWS = 48
PW, PH = 640, 480
# SMPTE-style luma for eight 80-pixel bars, distinct enough to separate from a dark OSD.
BAR_LUMA = [235, 210, 170, 145, 106, 81, 41, 16]
OSD_RED = 0xF00F           # RGBA4444: r=15 g=0 b=0 a=15

# Two raw ARM stubs loaded at the board's kernel address (0x00108000).  ``ARM_REARM`` is the
# guest supervisor: it writes AIF ch53 control = 3 (enable + start) on every iteration, the way
# a guest writer re-arms the OSD each millisecond.  ``ARM_IDLE`` only spins, so a single qtest arm
# expires at the next vsync and the per-frame contract can be observed.
ARM_REARM = struct.pack('<8I', 0xE3010300, 0xE34F0344, 0xE3A01003, 0xE5801000,
                        0xE3022000, 0xE3033123, 0xE5823000, 0xEAFFFFF7)
ARM_IDLE = struct.pack('<I', 0xEAFFFFFE)


def read_ppm(path: Path):
    data = path.read_bytes()
    if not data.startswith(b'P6\n'):
        raise AssertionError(f'{path}: not a P6 PPM: {data[:16]!r}')
    fields = data[3:].split(b'\n', 2)
    width, height = (int(value) for value in fields[0].split())
    pixels = fields[2]
    if len(pixels) < width * height * 3:
        raise AssertionError(f'{path}: short PPM pixel data')
    return width, height, pixels


def pixel(pixels, width, x, y):
    off = (y * width + x) * 3
    return pixels[off], pixels[off + 1], pixels[off + 2]


@unittest.skipUnless(NATIVE_AVAILABLE, 'Optional WSL/Linux sony-a6400 QEMU build absent')
class VphPanelTest(unittest.TestCase):
    def start(self, machine='sony-a6400', kernel='rearm'):
        directory = PROJECT_ROOT / 'artifacts/tests'
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        log = (root / 'qemu.log').open('wb')
        self.addCleanup(log.close)
        image = root / 'stub.bin'
        image.write_bytes(ARM_REARM if kernel == 'rearm' else ARM_IDLE)
        runtime = tempfile.TemporaryDirectory(prefix='a6400-vph-')
        self.addCleanup(runtime.cleanup)
        endpoint = Path(runtime.name) / 'qmp.sock'
        proc = subprocess.Popen(
            [str(QEMU), '-M', machine, '-accel', 'tcg', '-smp', '1',
             '-kernel', str(image), '-display', 'none',
             '-monitor', 'none', '-serial', 'none', '-net', 'none',
             '-qmp', f'unix:{endpoint},server=on,wait=off',
             '-qtest', 'stdio', '-qtest-log', str(root / 'qtest.log')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, bufsize=0)
        self.addCleanup(self._stop, proc)
        deadline = time.monotonic() + 10
        while not endpoint.exists():
            self.assertIsNone(proc.poll(), (root / 'qemu.log').read_text())
            self.assertLess(time.monotonic(), deadline, 'QMP socket did not appear')
            time.sleep(0.01)
        qmp = Qmp(endpoint, time.monotonic() + 20)
        self.addCleanup(qmp.close)
        self.proc, self.root, self.qmp, self.pending = proc, root, qmp, [b'']
        return self

    def _stop(self, proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                self.fail('Owned QEMU process required a forced kill')
        proc.stdin.close()
        proc.stdout.close()

    def qtest(self, text):
        self.proc.stdin.write((text + '\n').encode('ascii'))
        deadline = time.monotonic() + 5
        while b'\n' not in self.pending[0]:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.proc.stdout], [], [], remaining)[0]:
                self.fail(f'qtest timed out: {text}')
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                self.fail(f'qtest exited before replying: {text}')
            self.pending[0] += chunk
        line, self.pending[0] = self.pending[0].split(b'\n', 1)
        self.assertTrue(line.startswith(b'OK'), f'{text}: {line!r}')
        return line.decode('ascii', 'replace')

    def write_bytes(self, address, data):
        for start in range(0, len(data), 2048):
            chunk = data[start:start + 2048]
            # qtest's "write" parser reads the hex payload from words[3][2*i+2], i.e. it expects
            # an "0x" prefix; without it every byte is shifted by one nibble.
            self.qtest(f'write {address + start:#x} {len(chunk)} 0x{chunk.hex()}')

    def dump(self, name='panel'):
        ppm = self.root / f'{name}.ppm'
        reply = self.qmp.request('screendump', {'filename': str(ppm), 'device': 'vph', 'head': 0})
        self.assertNotIn('error', str(reply), reply)
        return read_ppm(ppm)

    def program(self, yuv=True, osd=True, en144=0x01000232, r140=0x30000000):
        q = self.qtest
        q(f'writel {VPH + 0x028:#x} 0x111')
        q(f'writel {FMT404:#x} 0x0')
        if yuv:
            row = bytearray()
            for luma in BAR_LUMA:
                row.extend(bytes([luma]) * 80)
            self.write_bytes(YUV_PA, bytes(row))
            q(f'writel {CH51 + 0x20:#x} {YUV_PA:#x}')
            q(f'writel {CH51 + 0x24:#x} {PW:#x}')
            q(f'writel {CH51 + 0x28:#x} 0x0')
            q(f'writel {CH51 + 0x2c:#x} 0x0')       # lines-1 = 0
        if osd:
            row = struct.pack('<H', OSD_RED) * PW
            self.write_bytes(OSD_PA, row * OSD_ROWS)
            q(f'writel {CH53 + 0x20:#x} {OSD_PA:#x}')
            q(f'writel {CH53 + 0x24:#x} {PW * 2:#x}')
            q(f'writel {CH53 + 0x2c:#x} {OSD_ROWS - 1:#x}')
            q(f'writel {CH53 + 0x48:#x} {PW * 2:#x}')
        if r140 is not None:
            q(f'writel {R140:#x} {r140:#x}')
        q(f'writel {EN144:#x} {en144:#x}')
        if yuv:
            q(f'writel {CH51 + 0x00:#x} 0x3')       # enable + start arm
        if osd:
            q(f'writel {CH53 + 0x00:#x} 0x3')
        # No sleep: the arm write is still pending at the next screendump, so the dump
        # composes this frame deterministically (the model counts a pending arm as the
        # frame being scanned; a re-arm loop is what the real supervisor does).

    def read_word(self, address):
        line = self.qtest(f'readl {address:#x}')
        return int(line.split()[-1], 16)

    def assert_bars(self, width, pixels, y=200):
        seen = [pixel(pixels, width, 40 + 80 * i, y)[0] for i in range(8)]
        normalized = [round(value / 16) * 16 for value in seen]
        self.assertGreaterEqual(len(set(normalized)), 5,
                                f'live-view bars are not visible at row {y}: {seen}')

    def arm(self):
        """Arm both AIF channels once (used by the test that lets a single arm expire)."""
        self.qtest(f'writel {CH51 + 0x00:#x} 0x3')
        self.qtest(f'writel {CH53 + 0x00:#x} 0x3')

    def dump_when(self, name, want_osd, attempts=80):
        """Return the first dump whose OSD row matches ``want_osd``.

        The panel scans one frame per 16.7 ms and the OSD is fetched only for an armed frame,
        exactly like the hardware (E0639: +0x00 bit0 is start/busy and clears at frame end).  The
        ``rearm`` stub keeps the channel armed the way the guest supervisor does, so the OSD is
        present on every frame; the ``idle`` stub lets a single arm expire, so its absence is
        stable.  The bound keeps a broken model from hanging instead of reporting the pixels it
        produced.
        """
        last = None
        for _ in range(attempts):
            width, height, pixels = self.dump(name)
            last = pixel(pixels, width, 320, 20)
            if self.is_red(last) == want_osd:
                return width, height, pixels
            time.sleep(0.005)
        self.fail(f'OSD row never matched want_osd={want_osd}; last pixel {last}')

    @staticmethod
    def is_red(rgb):
        return rgb[0] > 200 and rgb[1] < 24 and rgb[2] < 24

    def test_camera_values_compose_live_view_and_ui(self):
        """0x140=0x30000000 with the stock 0x144=0x01000232 shows both layers."""
        self.start().program()
        width, height, pixels = self.dump_when('both', want_osd=True)
        self.assertEqual((width, height), (PW, PH))
        self.assert_bars(width, pixels)     # ch51 through the panel-stage mode, bit0 clear

    def test_without_panel_stage_mode_and_bit0_the_yuv_layer_stays_off(self):
        """The YUV layer is not painted unconditionally: clear +0x140 and +0x144 bit0."""
        self.start().program(r140=0x0)
        width, _, pixels = self.dump_when('no-yuv', want_osd=True)
        middle = pixel(pixels, width, 320, 200)
        self.assertLess(sum(middle), 24, f'YUV layer appeared without an enable: {middle}')

    def test_osd_layer_needs_0x144_bit1(self):
        """Clearing +0x144 bit1 hides the OSD; the live view underneath stays."""
        self.start().program(en144=0x01000230)
        time.sleep(0.1)                     # let the last armed frame expire
        width, _, pixels = self.dump_when('no-osd', want_osd=False)
        self.assert_bars(width, pixels)

    def test_osd_disappears_when_the_guest_stops_arming_ch53(self):
        """The per-frame arm contract: one armed frame, then no OSD without re-arming."""
        self.start(kernel='idle').program()
        time.sleep(0.1)                     # the single arm expires after one vsync
        width, _, pixels = self.dump_when('unarmed', want_osd=False)
        self.assert_bars(width, pixels)

    def test_140_mode_bits_are_write_one_to_set(self):
        """A guest clear of +0x140 bits 28-29 does not stick, like the camera."""
        self.start()
        self.qtest(f'writel {R140:#x} 0x30000000')
        self.assertEqual(self.read_word(R140), 0x30000000)
        self.qtest(f'writel {R140:#x} 0x0')
        self.assertEqual(self.read_word(R140), 0x30000000)
        self.qtest(f'writel {R140:#x} 0x50000000')
        self.assertEqual(self.read_word(R140), 0x70000000)
        # Low bits are ordinary read/write.
        self.qtest(f'writel {R140:#x} 0x00000005')
        self.assertEqual(self.read_word(R140), 0x70000005)


if __name__ == '__main__':
    unittest.main()
