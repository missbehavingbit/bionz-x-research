"""Optional native QEMU eMMC regressions using constructed disks and paused CPUs."""
import os
from pathlib import Path
import re
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from project_paths import PROJECT_ROOT
from tools.research.qemu_boot_probe import Qmp
from tools.research.toolchain import toolchain_config

VERSION = toolchain_config()['qemu']['version']
QEMU = Path(os.environ.get(
    'A6400_QEMU_DIR', str(Path.home() / f'sony-a6400-qemu-{VERSION}'))) / 'build/qemu-system-arm'
SDHCI = 0xF0304000
NATIVE_AVAILABLE = os.name == 'posix' and QEMU.is_file()


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Requires WSL/Linux bash')
class QemuSubmicroPreflightTests(unittest.TestCase):
    def test_bad_fixture_is_rejected_before_staging_a_run(self):
        run = PROJECT_ROOT / 'qemu-a6400/run.sh'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / 'not-staged'
            missing = root / 'missing.bin'
            wrong = root / 'wrong frames.bin'
            wrong.write_bytes(bytes(384))
            for page, frames, reason in (
                (str(missing), '', b'fixture is missing or unreadable'),
                ('', str(wrong), b'SHA-256 mismatch'),
                (str(missing), str(wrong), b'not both'),
            ):
                with self.subTest(page=page, frames=frames):
                    result = subprocess.run(
                        ['bash', str(run)],
                        env=dict(os.environ, RUN_DIR=str(run_dir), A6400_QEMU_DATA_DIR='',
                                 SUBMICRO_PAGE=page, SUBMICRO_FRAMES=frames),
                        capture_output=True, timeout=5)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(reason, result.stderr)
                    self.assertFalse(run_dir.exists())


@unittest.skipUnless(NATIVE_AVAILABLE, 'Optional WSL/Linux sony-a6400 QEMU build absent')
class QemuEmmcAddressingTests(unittest.TestCase):
    image_bytes = 16 * 1024 ** 2

    def setUp(self):
        directory = PROJECT_ROOT / 'artifacts/tests'
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        image = root / 'emmc.img'
        self.marker = (b'Original sector contents, not a synthesized controller response.\n' * 9)[:512]
        self.marker = self.marker.ljust(512, b'\0')
        with image.open('xb') as stream:
            stream.truncate(self.image_bytes)
            stream.seek(8 * 1024 ** 2 + 1024)
            stream.write(self.marker)
        self.log = (root / 'qemu.log').open('wb')
        self.addCleanup(self.log.close)
        self.process = subprocess.Popen(
            [str(QEMU), '-M', 'sony-a6400', '-accel', 'qtest', '-S', '-display', 'none',
             '-monitor', 'none', '-serial', 'none', '-net', 'none',
             '-drive', f'if=sd,format=raw,file={image}',
             '-qtest', 'stdio', '-qtest-log', str(root / 'qtest.log')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, bufsize=0)
        self.addCleanup(self.stop)
        self.pending = b''
        self.request(f'writeb {SDHCI + 0x29:#x} 0xf')
        self.request(f'writew {SDHCI + 0x2c:#x} 0x7')
        self.request(f'writew {SDHCI + 0x34:#x} 0xffff')
        self.request(f'writew {SDHCI + 0x36:#x} 0xffff')

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
                self.fail('Owned QEMU process required a forced kill')
        self.process.stdin.close()
        self.process.stdout.close()

    def request(self, text):
        self.process.stdin.write((text + '\n').encode('ascii'))
        deadline = time.monotonic() + 5
        while b'\n' not in self.pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                self.fail(f'qtest timed out: {text}')
            chunk = os.read(self.process.stdout.fileno(), 4096)
            if not chunk:
                self.fail(f'qtest exited before replying: {text}')
            self.pending += chunk
        line, self.pending = self.pending.split(b'\n', 1)
        self.assertTrue(line.startswith(b'OK'), f'{text}: {line!r}')
        fields = line.split()
        return int(fields[1], 0) if len(fields) > 1 else None

    def command(self, index, argument, flags, *, read=False):
        self.request(f'writew {SDHCI + 0x30:#x} 0xffff')
        self.request(f'writew {SDHCI + 0x32:#x} 0xffff')
        self.request(f'writew {SDHCI + 0x04:#x} {512 if read else 0}')
        self.request(f'writew {SDHCI + 0x06:#x} {1 if read else 0}')
        self.request(f'writel {SDHCI + 0x08:#x} {argument:#x}')
        self.request(f'writew {SDHCI + 0x0c:#x} {0x10 if read else 0}')
        self.request(f'writew {SDHCI + 0x0e:#x} {(index << 8) | flags:#x}')
        for _ in range(100):
            status = self.request(f'readw {SDHCI + 0x30:#x}')
            self.assertFalse(status & 0x8000, f'SDHCI command {index} error {status:#x}')
            if status & 1:
                break
            self.request('clock_step 100000')
        else:
            self.fail(f'SDHCI command {index} did not complete')
        return self.request(f'readl {SDHCI + 0x10:#x}')

    def initialize_card(self, sector_addressed):
        self.command(0, 0, 0)
        ocr = self.command(1, 0x40000080 if sector_addressed else 0x80, 2)
        self.assertTrue(ocr & 0x80000000, 'eMMC did not power up')
        self.command(2, 0, 9)
        self.command(3, 0x10000, 0x1A)
        self.command(7, 0x10000, 0x1B)
        self.request('clock_step 1000000')
        return ocr

    def read_block(self, command, argument):
        self.command(command, argument, 0x3A, read=True)
        data = bytearray()
        for _ in range(128):
            data.extend(struct.pack('<I', self.request(f'readl {SDHCI + 0x20:#x}')))
        return bytes(data)

    def test_cmd1_sector_mode_reads_the_requested_512_byte_sector(self):
        ocr = self.initialize_card(True)
        self.assertTrue(ocr & 0x40000000, 'Requested eMMC sector mode was not acknowledged')
        self.assertEqual(self.read_block(17, 2), self.marker)

    def test_cmd1_byte_mode_keeps_byte_addressed_reads(self):
        ocr = self.initialize_card(False)
        self.assertFalse(ocr & 0x40000000)
        self.assertEqual(self.read_block(17, 1024), self.marker)

    def test_ext_csd_preserves_user_capacity_without_resizing_the_backend(self):
        self.initialize_card(True)
        ext_csd = self.read_block(8, 0)
        self.assertEqual(ext_csd[192], 7)
        self.assertEqual(struct.unpack_from('<I', ext_csd, 212)[0],
                         (self.image_bytes - 8 * 1024 ** 2) // 512)


class QemuLegacyCapacityTests(QemuEmmcAddressingTests):
    image_bytes = 2 * 1024 ** 3


@unittest.skipUnless(NATIVE_AVAILABLE, 'Optional WSL/Linux sony-a6400 QEMU build absent')
class QemuCapacityGuardTests(unittest.TestCase):
    def test_legacy_policy_still_rejects_an_unqualified_backend(self):
        directory = PROJECT_ROOT / 'artifacts/tests'
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            image = Path(temporary) / 'invalid.img'
            for size in (15 * 1024 ** 2, 2 * 1024 ** 3 - 512):
                with self.subTest(backend_bytes=size):
                    with image.open('wb') as stream:
                        stream.truncate(size)
                    result = subprocess.run(
                        [str(QEMU), '-M', 'sony-a6400', '-S', '-display', 'none',
                         '-monitor', 'none', '-serial', 'none', '-net', 'none',
                         '-drive', f'if=sd,format=raw,file={image}'],
                        capture_output=True, timeout=10)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertRegex(result.stderr, rb'Invalid (?:eMMC|SD card) size')


@unittest.skipUnless(NATIVE_AVAILABLE, 'Optional WSL/Linux sony-a6400 QEMU build absent')
class QemuBoardIoTests(unittest.TestCase):
    def test_rear_keys_and_both_display_callbacks_use_native_interfaces(self):
        with patch.object(sys, 'path', [str(PROJECT_ROOT / 'qemu-a6400/tools'), *sys.path]):
            from rear_panel import press_control

        directory = PROJECT_ROOT / 'artifacts/tests'
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as temporary, \
                tempfile.TemporaryDirectory(prefix='a6400-qmp-') as runtime:
            root = Path(temporary)
            socket_path = Path(runtime) / 'qmp.sock'
            with (root / 'qemu.log').open('wb') as log:
                process = subprocess.Popen(
                    [str(QEMU), '-M', 'sony-a6400,rear-controls=on', '-accel', 'qtest',
                     '-S', '-display', 'none', '-monitor', 'none', '-serial', 'none',
                     '-net', 'none', '-qmp', f'unix:{socket_path},server=on,wait=off'],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
                qmp = None
                try:
                    deadline = time.monotonic() + 10
                    while not socket_path.exists():
                        self.assertIsNone(process.poll(), (root / 'qemu.log').read_text())
                        self.assertLess(time.monotonic(), deadline, 'QMP socket did not become ready')
                        time.sleep(0.01)
                    qmp = Qmp(socket_path, time.monotonic() + 20)
                    qmp.request('cont')

                    def word(address, unit='w'):
                        output = qmp.request('human-monitor-command', {
                            'command-line': f'xp /1{unit}x {address:#x}'})
                        match = re.search(r':\s*(0x[0-9a-fA-F]+)', output)
                        self.assertIsNotNone(match, output)
                        return int(match.group(1), 16)

                    def key(code, down):
                        qmp.request('input-send-event', {'events': [{
                            'type': 'key', 'data': {
                                'key': {'type': 'qcode', 'data': code}, 'down': down}}]})

                    self.assertEqual(word(0xF2407020), 1023)
                    key('ret', True)
                    self.assertEqual(word(0xF2407020), 45)
                    key('ret', False)
                    self.assertEqual(word(0xF2407020), 1023)
                    panel = SimpleNamespace(command=lambda name, **args: qmp.request(name, args))
                    press_control(panel, 'exposure')
                    self.assertEqual(word(0xF2407020), 520)
                    # The virtual clock has not advanced; a second timed key
                    # would sit behind the pending release of this down press.
                    press_control(panel, 'wheel-right')
                    self.assertEqual(word(0xF2404014, 'h'), 4)
                    press_control(panel, 'wheel-left')
                    self.assertEqual(word(0xF2404014, 'h'), 0)
                    press_control(panel, 'wheel-left')
                    self.assertEqual(word(0xF2404014, 'h'), 0xFFFC)
                    press_control(panel, 'wheel-right')
                    self.assertEqual(word(0xF2404014, 'h'), 0)
                    self.assertNotEqual(word(0xF2910010) & (1 << 11), 0)
                    press_control(panel, 'af-lever')
                    self.assertEqual(word(0xF2910010) & (1 << 11), 0)
                    press_control(panel, 'af-lever')
                    self.assertNotEqual(word(0xF2910010) & (1 << 11), 0)
                    self.assertNotEqual(word(0xF2910010) & (1 << 2), 0)
                    press_control(panel, 'eye-sensor')
                    self.assertEqual(word(0xF2910010) & (1 << 2), 0)
                    press_control(panel, 'eye-sensor')
                    self.assertNotEqual(word(0xF2910010) & (1 << 2), 0)
                    s1, s2 = 1 << 1, 1 << 5
                    p0, p1 = 0xF2910010, 0xF2910110
                    self.assertTrue(word(p0) & s1)
                    self.assertTrue(word(p1) & s2)
                    key('s', True)
                    self.assertFalse(word(p0) & s1)
                    self.assertTrue(word(p1) & s2)
                    key('spc', True)
                    self.assertFalse(word(p0) & s1)
                    self.assertFalse(word(p1) & s2)
                    key('spc', False)
                    self.assertFalse(word(p0) & s1)
                    self.assertTrue(word(p1) & s2)
                    key('s', False)
                    self.assertTrue(word(p0) & s1)
                    key('spc', True)
                    self.assertFalse(word(p0) & s1)
                    self.assertFalse(word(p1) & s2)
                    key('spc', False)
                    self.assertTrue(word(p0) & s1)
                    self.assertTrue(word(p1) & s2)
                    self.assertEqual(word(0xF2407020), 520)
                    qmp.request('stop')
                    for head in (0, 1):
                        image = root / f'head-{head}.ppm'
                        qmp.request('screendump', {'filename': str(image), 'device': 'vph', 'head': head})
                        data = image.read_bytes()
                        self.assertTrue(data.startswith(b'P6\n640 480\n255\n'), data[:64])
                        self.assertEqual(len(data), len(b'P6\n640 480\n255\n') + 640 * 480 * 3)
                    qmp.request('quit')
                    self.assertEqual(process.wait(timeout=5), 0)
                finally:
                    if qmp is not None:
                        qmp.close()
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                            self.fail('Owned QEMU process required a forced kill')


if __name__ == '__main__':
    unittest.main()
