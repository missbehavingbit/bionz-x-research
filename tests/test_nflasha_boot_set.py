"""Constructed + real-dump checks for the nflasha boot-set unpacker (E0415)."""
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from project_paths import PROJECT_ROOT, a6400_dump_dir
from src.a6400.execution.nflasha_boot_set import (
    BOOT_SETS,
    KERNEL_VIRTUAL_BASE,
    BootSetError,
    NflashaBootSet,
    detect_image_format,
    parse_loadaddr,
)

ROOT = Path(__file__).resolve().parents[1]
DUMP_DIR = a6400_dump_dir(ROOT)
DUMPS_PRESENT = all((DUMP_DIR / identity['image']).is_file()
                    for identity in BOOT_SETS.values())


def zimage(size=0x100):
    raw = bytearray(size)
    struct.pack_into('<I', raw, 0x24, 0x016F2818)
    return bytes(raw)


def raw_arm_kernel(size=0x100):
    raw = bytearray(size)
    struct.pack_into('<I', raw, 0, 0xE321F0D3)
    return bytes(raw)


class CaptureDirectoryTests(unittest.TestCase):
    def setUp(self):
        base = PROJECT_ROOT / 'artifacts' / 'tests' / 'capture-directory'
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.download = self.root / 'download'
        self.download.mkdir()
        self.config_path = self.root / 'qemu-a6400' / 'inputs.local.json'
        self.config_path.parent.mkdir()
        self.config = {'schema_version': 1, 'dump_dir': str(self.download)}
        for name, field, content in (
                ('manifest.json', 'manifest_sha256', b'{"status":"complete"}'),
                ('SHA256SUMS.txt', 'checksums_sha256', b'constructed checksums\n')):
            (self.download / name).write_bytes(content)
            self.config[field] = hashlib.sha256(content).hexdigest()

    def declare(self):
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')

    def test_unconfigured_root_retains_legacy_evidence_location(self):
        self.assertEqual(a6400_dump_dir(self.root),
                         self.root / 'artifacts' / 'dumps' / 'a6400-nflasha-readonly')

    def test_declared_capture_takes_precedence_over_partial_legacy_directory(self):
        legacy = self.root / 'artifacts' / 'dumps' / 'a6400-nflasha-readonly'
        legacy.mkdir(parents=True)
        (legacy / 'manifest.json').write_bytes(b'old partial capture')
        self.declare()
        self.assertEqual(a6400_dump_dir(self.root), self.download)

    def test_changed_metadata_rejects_configuration_instead_of_falling_back(self):
        self.declare()
        (self.download / 'manifest.json').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            a6400_dump_dir(self.root)

    def test_missing_capture_relative_root_and_wrong_target_are_rejected(self):
        for update, error in (
                ({'dump_dir': str(self.root / 'missing')}, FileNotFoundError),
                ({'dump_dir': 'relative-capture'}, ValueError),
                ({'target_id': 'zv_e10'}, ValueError),
                ({'manifest_sha256': ''}, ValueError)):
            with self.subTest(update=update):
                original = dict(self.config)
                self.config.update(update)
                self.declare()
                with self.assertRaises(error):
                    a6400_dump_dir(self.root)
                self.config = original


class LoadAddressTests(unittest.TestCase):
    def test_parses_the_observed_nflasha1_contract(self):
        parsed = parse_loadaddr('0x00108000,0x00700000,0x00103000\n')
        self.assertEqual(parsed, {'kernel_load': 0x00108000,
                                  'initrd_load': 0x00700000,
                                  'boot_params': 0x00103000})

    def test_malformed_input_is_an_explicit_error(self):
        with self.assertRaises(BootSetError):
            parse_loadaddr('0x00108000,0x00700000')
        with self.assertRaises(BootSetError):
            parse_loadaddr('0x00108000,0x00700000,zzz')
        with self.assertRaises(BootSetError):
            parse_loadaddr('0x108000,0x700000,0x103000,0x0')


class ImageFormatTests(unittest.TestCase):
    def test_kernel_and_initrd_formats(self):
        self.assertEqual(detect_image_format(zimage()), 'zimage')
        self.assertEqual(detect_image_format(raw_arm_kernel()), 'raw_arm_kernel')
        ext2 = bytearray(0x500)
        ext2[0x438:0x43A] = b'\x53\xef'
        self.assertEqual(detect_image_format(bytes(ext2)), 'ext2')
        cramfs = bytearray(8)
        cramfs[0:4] = b'\x45\x3d\xcd\x28'
        self.assertEqual(detect_image_format(bytes(cramfs)), 'cramfs')
        self.assertEqual(detect_image_format(b'\x7fELF' + bytes(0x100)), 'elf')
        self.assertEqual(detect_image_format(bytes(0x100)), 'unknown')

    def test_virtual_base_matches_the_observed_page_offset(self):
        self.assertEqual(KERNEL_VIRTUAL_BASE, 0x60108000)


@unittest.skipUnless(DUMPS_PRESENT, 'E0414 boot-set dumps not present')
class RealBootSetTests(unittest.TestCase):
    def test_nflasha1_updater_boot_set(self):
        boot_set = NflashaBootSet.from_evidence(ROOT, 'nflasha1')
        summary = boot_set.summary()
        self.assertEqual(summary['load_addresses'],
                         {'kernel_load': 0x00108000, 'initrd_load': 0x00700000,
                          'boot_params': 0x00103000})
        self.assertEqual(summary['kernel']['format'], 'zimage')
        self.assertEqual(summary['kernel']['bytes'], 1697068)
        self.assertEqual(summary['initrd']['format'], 'cramfs')
        self.assertEqual(summary['kernel_virtual_base'], 0x60108000)
        self.assertIn('boot/kemco.txt', boot_set.files)
        self.assertEqual(boot_set.read('dat2').hex(), '11000381')

    def test_nflasha3_main_boot_set(self):
        boot_set = NflashaBootSet.from_evidence(ROOT, 'nflasha3')
        summary = boot_set.summary()
        self.assertIsNone(summary['load_addresses'])
        self.assertEqual(summary['kernel']['format'], 'raw_arm_kernel')
        self.assertEqual(summary['kernel']['bytes'], 4085888)
        self.assertEqual(summary['initrd']['format'], 'ext2')
        self.assertEqual(len(boot_set.files), 13)
        self.assertEqual(len([path for path in boot_set.files]), 13)
        self.assertEqual(summary['kernel_virtual_base'], 0x60108000)
        kernel = boot_set.read('vmlinux.bin')
        self.assertEqual(kernel[:4], b'\xd3\xf0\x21\xe3')
        self.assertIn(b'Linux version 3.0.27_nl-rt106+', kernel)
        self.assertIn(b'Sony CE Linux 8.2.6.0', kernel)
        banner_offset = kernel.find(b'Linux version')
        self.assertEqual(banner_offset, 0x2C0028)

    def test_unknown_label_is_rejected(self):
        with self.assertRaises(BootSetError):
            NflashaBootSet.from_evidence(ROOT, 'nflasha2')


if __name__ == '__main__':
    unittest.main()
