"""Constructed native boot-layout checks; no firmware or physical device access."""
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import time
import unittest
import uuid

from project_paths import PROJECT_ROOT
from tools.research.qemu_boot_probe import Qmp

BINARY = os.environ.get('BIONZX_QEMU_TEST_BINARY')
INITIAL_RGB_SHA256 = '37d9e8d2c79e60b79d802b3ba1a454ddbab323dae9fc18ac8f71b822f50abfa8'


@unittest.skipUnless(BINARY, 'Set BIONZX_QEMU_TEST_BINARY to the built upstream QEMU')
class NativeMenuBootLayoutTests(unittest.TestCase):
    def setUp(self):
        self.output = PROJECT_ROOT / 'artifacts' / 'tests' / ('native-menu-' + uuid.uuid4().hex)
        self.output.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(self.output))
        self.kernel = self.output / 'Image'
        self.initrd = self.output / 'initrd'
        self.kernel.write_bytes(b'constructed kernel fixture')
        self.initrd.write_bytes(b'constructed initrd fixture')

    def start(self, profile, smp=1, append=None):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        argv = [BINARY, '-M', 'sony-a6400' + profile, '-cpu', 'cortex-a9',
                '-smp', str(smp), '-display', 'none', '-monitor', 'none',
                '-serial', 'none', '-nic', 'none', '-S',
                '-kernel', str(self.kernel), '-initrd', str(self.initrd),
                '-qmp', f'tcp:127.0.0.1:{port},server=on,wait=off']
        if append is not None:
            argv += ['-append', append]
        log = (self.output / 'stderr.log').open('wb')
        self.addCleanup(log.close)
        process = subprocess.Popen(argv, stdout=log, stderr=log)
        self.addCleanup(lambda: self.cleanup_process(process))
        deadline = time.monotonic() + 15
        while process.poll() is None and time.monotonic() < deadline:
            try:
                qmp = Qmp(('127.0.0.1', port), deadline)
                self.addCleanup(qmp.close)
                return process, qmp
            except ConnectionRefusedError:
                time.sleep(0.05)
        return process, None

    @staticmethod
    def cleanup_process(process):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)

    def read(self, qmp, address, size):
        path = self.output / 'read.raw'
        qmp.request('pmemsave', dict(val=address, size=size, filename=str(path)))
        return path.read_bytes()

    def check_layout(self, profile, kernel, initrd, instruction):
        process, qmp = self.start(profile)
        self.assertIsNotNone(qmp, (self.output / 'stderr.log').read_text())
        self.assertEqual(self.read(qmp, kernel, self.kernel.stat().st_size), self.kernel.read_bytes())
        self.assertEqual(self.read(qmp, initrd, self.initrd.stat().st_size), self.initrd.read_bytes())
        self.assertEqual(struct.unpack('<I', self.read(qmp, 0x1034, 4))[0], instruction)
        if profile:
            self.assertEqual(struct.unpack('<I', self.read(qmp, 0x104c, 4))[0], 0x100)
            self.assertEqual(struct.unpack('<4I', self.read(qmp, 0x124, 16)),
                             (4, 0x54420005, 0x10000000, self.initrd.stat().st_size))
        self.assertEqual(qmp.quit(process, timeout=5), 0)

    def test_stock_layout_is_unchanged(self):
        self.check_layout('', 0x108000, 0x700000, 0xe3a0f942)

    def test_adapted_layout_loads_large_initrd_and_relocates_entry(self):
        with self.initrd.open('r+b') as stream:
            stream.truncate(9 * 1024 * 1024)
        self.check_layout(',zve10-menu-boot=on', 0x138000, 0x10000000, 0xe3a0f94e)

    def test_stock_layout_rejects_large_initrd(self):
        with self.initrd.open('r+b') as stream:
            stream.truncate(9 * 1024 * 1024)
        process, qmp = self.start('')
        self.assertIsNone(qmp)
        self.assertNotEqual(process.wait(timeout=5), 0)

    def test_adapted_atags_cannot_overwrite_boot_stub(self):
        process, qmp = self.start(',zve10-menu-boot=on', append='x' * 5000)
        self.assertIsNone(qmp)
        self.assertNotEqual(process.wait(timeout=5), 0)
        self.assertIn('ATAGS exceed reserved space', (self.output / 'stderr.log').read_text())

    def test_adapted_layout_rejects_multiple_cpus(self):
        process, qmp = self.start(',zve10-menu-boot=on', smp=2)
        self.assertIsNone(qmp)
        self.assertNotEqual(process.wait(timeout=5), 0)
        self.assertIn('requires -smp 1', (self.output / 'stderr.log').read_text())


@unittest.skipUnless(BINARY and os.environ.get('BIONZX_ZVE10_MENU_INPUTS'),
                     'Private menu replay requires explicit binary and input declaration')
class PrivateMenuReplayTests(unittest.TestCase):
    def test_english_initial_menu_and_right_page(self):
        import hashlib
        from PIL import Image
        from tools.research.qemu_zve10_menu import load_inputs, run

        inputs = load_inputs(Path(os.environ['BIONZX_ZVE10_MENU_INPUTS']))
        self.assertEqual(inputs['language'], 'english')
        output = PROJECT_ROOT / 'artifacts' / 'tests' / ('private-menu-' + uuid.uuid4().hex)
        # Keep receipts, including failures, for review. Never publish raw frames.
        receipt = run(Path(BINARY), inputs, output, 180, ['RIGHT'], initial_rgb=INITIAL_RGB_SHA256,
                      event_titles=('d08a1a729c8ce0da06e6e8c1727dcc0ad5cc1a5820f037a732849b2b6cf5120c',))
        self.assertTrue(receipt['completed'])
        with Image.open(output / 'initial-menu.png') as image:
            initial_rgb = image.convert('RGB').tobytes()
        self.assertEqual(hashlib.sha256(initial_rgb).hexdigest(), INITIAL_RGB_SHA256)
        with Image.open(output / 'event-01-right.png') as image:
            title = image.convert('RGB').crop((0, 0, 640, 125))
        self.assertEqual(hashlib.sha256(title.tobytes()).hexdigest(),
                         'd08a1a729c8ce0da06e6e8c1727dcc0ad5cc1a5820f037a732849b2b6cf5120c')
        self.assertGreater(receipt['events'][0]['completed_frame'], receipt['ready']['frame'])
        self.assertEqual(receipt['stopped']['marker'], 'NE_STOPPED')
        self.assertEqual(receipt['returncode'], 0)


if __name__ == '__main__':
    unittest.main()
