"""Constructed capture checks; no camera, USB, real dumps, or QEMU dependency."""
import hashlib
import argparse
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest

from project_paths import PROJECT_ROOT
from tests.test_guest_vfat import dir_entry
from tools.research import qemu_boot_inputs as inputs
from tools.research.qemu_boot_probe import (
    console_snapshot, guest_statuses, physical_range, serial_readiness,
    source_reference_hashes, split_qmp_reply,
)


def fat_image(files):
    image = bytearray(8192)
    image[:3], image[3:11] = b'\xeb\x3c\x90', b'MSDOS5.0'
    struct.pack_into('<HBHBHH', image, 11, 512, 1, 1, 1, 16, 16)
    struct.pack_into('<H', image, 22, 1)
    image[510:512] = b'\x55\xaa'
    entries, cluster = {0: 0xFF8, 1: 0xFFF}, 2
    for index, (name, data) in enumerate(files.items()):
        base, ext = name.rsplit('.', 1)
        image[1024 + index * 32:1056 + index * 32] = dir_entry(base, ext, 0x20, cluster, len(data))
        count = (len(data) + 511) // 512
        for offset in range(count):
            entries[cluster + offset] = cluster + offset + 1 if offset + 1 < count else 0xFFF
        start = 1536 + (cluster - 2) * 512
        image[start:start + len(data)] = data
        cluster += count
    for index, value in entries.items():
        offset = 512 + index + index // 2
        old = struct.unpack_from('<H', image, offset)[0]
        value = (old & 0x000F) | value << 4 if index & 1 else (old & 0xF000) | value
        struct.pack_into('<H', image, offset, value)
    return bytes(image)


class QemuBootInputTests(unittest.TestCase):
    def setUp(self):
        directory = PROJECT_ROOT / 'artifacts/tests'
        directory.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.capture = self.root / 'capture'
        self.capture.mkdir()
        kernel = b'\xd3\xf0\x21\xe3' + bytes(252)
        initrd = bytearray(1280)
        initrd[0x438:0x43A] = b'\x53\xef'
        avcam = b'\x0a\x00\x00\xeaORIL' + bytes(56)
        backup = struct.pack('<4I', 0x02E80000, 0xAAAAAAAA, 1, 0)
        kemco = b'kemco console=ttynull memrsv=0x1A5E000@0x02E68000 *\n'
        self.main = {'vmlinux.bin': kernel, 'initrd.img': bytes(initrd), 'av-cam.bin': avcam}
        settings = {'Backup.bin': backup, 'kemco.txt': kemco}
        header = bytearray(4096)
        header[:8] = b'82461.00'
        struct.pack_into('<I', header, 8, 7)
        rows = [(4096, 4096), (8192, 8192), (16384, 8192),
                (24576, 0), (24576, 0), (24576, 0), (24576, 4096)]
        for index, (start, size) in enumerate(rows):
            struct.pack_into('<4I', header, 32 + index * 16, start, size, 1, 0xFFFFFFFF if size else 0)
        (self.capture / 'nflasha-head-1m.bin').write_bytes(header)
        partitions = {1: b'U' * 4096, 2: fat_image(settings), 3: fat_image(self.main), 7: b'R' * 4096,
                      'B0': b'B' * (4 * 1024 ** 2), 'B1': b'C' * (4 * 1024 ** 2)}
        self.manifest = {'devices': {}}
        for number, data in partitions.items():
            device, name = f'nflasha{number}', f'nflasha{number}.bin'
            (self.capture / name).write_bytes(data)
            self.manifest['devices'][device] = dict(status='complete', image=name,
                declared_size=len(data), sha256=hashlib.sha256(data).hexdigest(), error=None)
        inventory = {'entries': {'proc_partitions': {
            'status': 0, 'truncated': False, 'output': '252 0 64 nflasha\n'}}}
        (self.capture / 'system-inventory.json').write_text(json.dumps(inventory), encoding='utf-8')
        self.config = self.root / 'inputs.json'
        self.refresh_controls()

    def refresh_controls(self):
        (self.capture / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')
        files = sorted(path for path in self.capture.iterdir() if path.name != 'SHA256SUMS.txt')
        sums = ''.join(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n' for path in files)
        (self.capture / 'SHA256SUMS.txt').write_text(sums, encoding='ascii')
        config = dict(schema_version=1, dump_dir=str(self.capture),
                      manifest_sha256=hashlib.sha256((self.capture / 'manifest.json').read_bytes()).hexdigest(),
                      checksums_sha256=hashlib.sha256((self.capture / 'SHA256SUMS.txt').read_bytes()).hexdigest())
        self.config.write_text(json.dumps(config), encoding='utf-8')

    def prepare(self, name='prepared', **kwargs):
        return inputs.prepare(self.config, self.root / name,
                              allow_unacquired_zero_fill=True, **kwargs)

    def test_roundtrip_is_byte_reproducible_and_sources_remain_unchanged(self):
        before = {path.name: path.read_bytes() for path in self.capture.iterdir()}
        first = self.prepare('first')
        second = self.prepare('second')
        self.assertEqual(first, second)
        for name in (*inputs.OUTPUTS, 'boot-manifest.json'):
            self.assertEqual((self.root / 'first' / name).read_bytes(),
                             (self.root / 'second' / name).read_bytes())
        disk = (self.root / 'first/emmc.img').read_bytes()
        self.assertEqual(disk[:4 * 1024 ** 2], before['nflashaB0.bin'])
        self.assertEqual(disk[4 * 1024 ** 2:8 * 1024 ** 2], before['nflashaB1.bin'])
        user_area = 8 * 1024 ** 2
        self.assertEqual(disk[user_area:user_area + 8], b'82461.00')
        self.assertEqual(disk[user_area + 16384:user_area + 24576], before['nflasha3.bin'])
        self.assertEqual((self.root / 'first/nflasha3-Image').read_bytes(), self.main['vmlinux.bin'])
        self.assertEqual(inputs.verify(self.root / 'first', check_sources=True), first)
        self.assertEqual(first['unacquired_ranges'], [
            {'start': user_area + 28672, 'bytes': first['emulated_disk_bytes'] - user_area - 28672}])
        self.assertEqual(first['target_id'], 'ilce_6400')
        self.assertIn('console=ttyAM0,115200n8 amba2.console=1', first['boot_contract']['cmdline'])
        with tarfile.open(self.root / 'first/setting.tar') as archive:
            self.assertEqual(archive.getnames(), ['./Backup.bin', './kemco.txt'])
            self.assertTrue(all(member.mtime == 0 for member in archive.getmembers()))
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.capture.iterdir()})

    def test_unacquired_ranges_require_explicit_permission(self):
        with self.assertRaisesRegex(ValueError, 'allow-unacquired-zero-fill'):
            inputs.prepare(self.config, self.root / 'refused')
        with self.assertRaisesRegex(ValueError, 'not completed'):
            inputs.verify(self.root / 'refused')

    def test_corrupt_source_fails_without_refreshing_hashes(self):
        source = self.capture / 'nflasha7.bin'
        source.write_bytes(b'X' + source.read_bytes()[1:])
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            self.prepare()
        report = json.loads((self.root / 'prepared/boot-manifest.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(self.manifest['devices']['nflasha7']['sha256'],
                         json.loads((self.capture / 'manifest.json').read_text())['devices']['nflasha7']['sha256'])

    def test_partial_and_escaping_capture_entries_are_refused(self):
        row = self.manifest['devices']['nflasha2']
        row['status'] = 'partial'
        self.refresh_controls()
        with self.assertRaisesRegex(ValueError, 'not complete'):
            self.prepare('partial')
        row['status'], row['image'] = 'complete', '../outside.bin'
        self.refresh_controls()
        with self.assertRaisesRegex(ValueError, 'basename'):
            self.prepare('escape')

    def test_existing_reports_are_not_overwritten(self):
        self.prepare()
        path = self.root / 'prepared/boot-manifest.json'
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(path.read_bytes(), before)

    def test_capture_overlap_is_refused_before_any_output_write(self):
        output = self.capture / 'nested' / 'prepared'
        with self.assertRaisesRegex(ValueError, 'overlap'):
            inputs.prepare(self.config, output, allow_unacquired_zero_fill=True)
        self.assertFalse(output.parent.exists())

    def test_other_body_does_not_reuse_the_a6400_capture_builder(self):
        config = json.loads(self.config.read_text(encoding='utf-8'))
        config['target_id'] = 'zv_e10'
        self.config.write_text(json.dumps(config), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'not reused'):
            self.prepare('other-body')
        self.assertFalse((self.root / 'other-body').exists())

    def test_verify_detects_output_and_original_input_changes(self):
        self.prepare()
        path = self.root / 'prepared/av-cam.bin'
        original = path.read_bytes()
        path.write_bytes(b'X' + original[1:])
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            inputs.verify(self.root / 'prepared')
        path.write_bytes(original)
        source = self.capture / 'nflasha7.bin'
        source.write_bytes(b'X' + source.read_bytes()[1:])
        with self.assertRaisesRegex(ValueError, 'Original capture changed'):
            inputs.verify(self.root / 'prepared', check_sources=True)

    def test_sdm_overlaps_and_truncated_tables_are_rejected(self):
        header = bytearray((self.capture / 'nflasha-head-1m.bin').read_bytes())
        struct.pack_into('<I', header, 48, 4096)
        with self.assertRaisesRegex(ValueError, 'overlapping'):
            inputs.parse_sdm_partitions(header)
        with self.assertRaisesRegex(ValueError, 'truncated'):
            inputs.parse_sdm_partitions(header[:40])

    def test_updater_cmdline_cannot_overwrite_main_boot_preloads(self):
        files = {'nflasha3-Image': self.main['vmlinux.bin'],
                 'nflasha3-initrd': self.main['initrd.img'], 'av-cam.bin': self.main['av-cam.bin'],
                 'Backup.bin': struct.pack('<4I', 0x02E80000, 0xAAAAAAAA, 1, 0),
                 'kemco.txt': b'kemco console=ttynull memrsv=0x75e000@0x02E68000 *'}
        with self.assertRaisesRegex(ValueError, 'av-cam.bin preload'):
            inputs.boot_contract(files)


class QemuProbeProtocolTests(unittest.TestCase):
    def test_source_reference_patch_matches_the_running_qmp_version(self):
        for version in ((10, 1, 0), (11, 1, 3)):
            with self.subTest(version=version):
                greeting = {'QMP': {'version': {
                    'qemu': dict(zip(('major', 'minor', 'micro'), version))}}}
                references = source_reference_hashes(greeting)
                patch = 'qemu-a6400/patches/qemu-v%s-sony-a6400.patch' % '.'.join(map(str, version))
                self.assertEqual(references[patch],
                                 hashlib.sha256((PROJECT_ROOT / patch).read_bytes()).hexdigest())
                self.assertEqual(len([name for name in references if name.endswith('.patch')]), 1)

    def test_source_references_refuse_unknown_or_malformed_native_versions(self):
        for major, error in ((999, 'No pinned Sony'), ('../11', 'integer')):
            with self.subTest(major=major), self.assertRaisesRegex(ValueError, error):
                source_reference_hashes({'QMP': {'version': {
                    'qemu': {'major': major, 'minor': 1, 'micro': 3}}}})

    def test_serial_gate_survives_old_prompt_scrolling_out_of_the_tail(self):
        shell, marker = serial_readiness('BusyBox built-in shell (ash)\n/ # ', False, False, 'end entry_main')
        self.assertTrue(shell)
        self.assertFalse(marker)
        shell, marker = serial_readiness('[1.0] end entry_main\n', shell, marker, 'end entry_main')
        self.assertTrue(shell and marker)

    def test_only_guest_responses_not_echoed_commands_count_as_statuses(self):
        transcript = ('/ # echo __QEMU_STATUS_0__:$?\r\n__QEMU_STATUS_0__:0\r\n'
                      '/ # missing\r\n__QEMU_STATUS_1__:127\r\n')
        self.assertEqual(guest_statuses(transcript), {0: 0, 1: 127})

    def test_console_snapshot_keeps_a_status_outside_a_128_kib_tail(self):
        blob = b'__QEMU_STATUS_0__:0\n' + (b'x' * (128 * 1024)) + b'\n__QEMU_STATUS_1__:1\n'
        self.assertEqual(guest_statuses(console_snapshot(blob)), {0: 0, 1: 1})
        with self.assertRaisesRegex(RuntimeError, '32 MiB'):
            console_snapshot(b'x' * (32 * 1024 * 1024 + 1))

    def test_qmp_events_stay_with_the_matching_reply(self):
        result, events = split_qmp_reply(
            [{'event': 'STOP'}, {'return': {'running': False}, 'id': 3}, {'event': 'SHUTDOWN'}], 3)
        self.assertEqual(result, {'running': False})
        self.assertEqual([item['event'] for item in events], ['STOP', 'SHUTDOWN'])
        with self.assertRaisesRegex(RuntimeError, 'message bound'):
            split_qmp_reply([{'event': 'STOP'}], 3)

    def test_memory_observations_cannot_cross_pages_or_touch_mmio(self):
        self.assertEqual(physical_range('0x20021000:4096'), (0x20021000, 4096))
        for value in ('0x20021ffc:8', '0xF2403000:4', '0x1000:8192', '0x1001:4'):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                physical_range(value)


if __name__ == '__main__':
    unittest.main()
