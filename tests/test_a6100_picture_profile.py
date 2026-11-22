"""Picture Profile gate audit for the ILCE-6100 dump (offline, read-only).

The image-dependent cases run against the prepared artifacts in
`artifacts/a6100-pp/` and skip when those local-only files are absent, so the
suite still runs on a fresh clone.
"""

import json
import os
import struct
import tempfile
import unittest
from pathlib import Path

from tools.research import a6100_picture_profile as pp

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / 'artifacts/a6100-pp/defs'
ROOTFS = ROOT / 'artifacts/a6100-pp/extract/nflasha15-a6100.img'
A6400_BACKUP = Path('/home/alex/a6400-qemu-runs/runs/run-3/Backup.bin')
DUMP = Path(os.environ.get('A6100_DUMP', '')) if os.environ.get('A6100_DUMP') else None


def artifacts_ready() -> bool:
    return all((DEFS / rel).is_file() for rel in pp.APP_FILES.values()) \
        and (DEFS / 'av-cam.bin').is_file() and (DEFS / 'Backup.bin').is_file() \
        and ROOTFS.is_file()


class TestDumpHashes(unittest.TestCase):
    def test_stored_hash_mismatch_is_reported_not_refreshed(self):
        with tempfile.TemporaryDirectory() as tmp:
            dump = Path(tmp)
            (dump / 'SHA256SUMS').write_text('0' * 64 + ' *nflasha7.img\n')
            (dump / 'nflasha7.img').write_bytes(b'not the dumped bytes')
            result = pp.verify_dump(dump)
            self.assertFalse(result['ok'])
            row = result['files']['nflasha7.img']
            self.assertEqual(row['expected'], '0' * 64)
            self.assertNotEqual(row['actual'], row['expected'])
            self.assertFalse(row['ok'])
            # the stored hash must be untouched by the check
            self.assertEqual((dump / 'SHA256SUMS').read_text(), '0' * 64 + ' *nflasha7.img\n')

    def test_real_dump_verifies_when_supplied(self):
        if DUMP is None or not (DUMP / 'SHA256SUMS').is_file():
            self.skipTest('set A6100_DUMP to the read-only dump to check its hashes')
        result = pp.verify_dump(DUMP)
        self.assertTrue(result['ok'], json.dumps(result['files'], indent=1))
        self.assertEqual(len(result['files']), 19)


class TestGateLayers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not artifacts_ready():
            raise unittest.SkipTest('a6100 artifacts are not present in this checkout')
        cls.report = pp.report(
            DUMP if (DUMP and (DUMP / 'SHA256SUMS').is_file()) else None,
            ROOTFS, DEFS,
            a6400_backup=A6400_BACKUP if A6400_BACKUP.is_file() else None)

    def test_every_data_layer_carries_picture_profile(self):
        layers = self.report['layers']
        for name in ('strings', 'nodes', 'registry', 'menu', 'pipeline', 'settings'):
            self.assertTrue(layers[name]['ok'], f'{name}: {layers[name]}')
        self.assertEqual(self.report['verdict']['gate'], 'runtime-validity-flag')

    def test_report_carries_the_opt_in_unlock_plan(self):
        unlock = self.report['unlock']
        self.assertTrue(unlock['found'], unlock)
        self.assertEqual(unlock['plan']['new_st_value'], '0xc5d530')
        self.assertEqual(unlock['stock'], 'jp-1.00')

    def test_provenance_note_says_the_content_is_stock(self):
        # The dumped file has a 2026-11-19 mtime but matches the stock pull.
        self.assertEqual(self.report['provenance']['caution_content']['stock'], 'jp-1.00')
        self.assertTrue(any('content is stock' in w for w in self.report['warnings']))

    def test_registry_lists_picture_profile_at_its_item_index(self):
        registry = self.report['layers']['registry']
        self.assertEqual(registry['pp_index'], 251)
        self.assertEqual(registry['entries'], 416)
        self.assertEqual(registry['pp_entries'], 13)
        self.assertIn(251, registry['sub_item_indices'])

    def test_menu_trees_reference_the_pp_nodes(self):
        menu = self.report['layers']['menu']
        self.assertEqual(menu['pp_references'], 21)
        self.assertEqual(menu['slog_references'], 15)

    def test_a6100_settings_are_a_superset_of_a6400(self):
        settings = self.report['layers']['settings']
        if 'reference_units' not in settings:
            self.skipTest('no a6400 Backup.bin supplied for the differential')
        self.assertTrue(settings['superset'])
        self.assertEqual(settings['missing_from_a6100'], 0)
        self.assertEqual(settings['extra_in_a6100'], 16)

    def test_string_table_is_the_shared_a6400_resource(self):
        # Byte-identical to the a6400 file recorded in evidence E0515.
        self.assertEqual(
            self.report['inputs']['strings']['sha256'],
            'b62c58521eca7b0443c5e570aaf8554ae5b05d94f46940dce7d9bc01a94b2921')


class TestUnlock(unittest.TestCase):
    """The opt-in: Soft Skin menu node redirected to the Picture Profile node."""

    @classmethod
    def setUpClass(cls):
        cls.path = DEFS / 'lib/CautionConfig.so'
        if not cls.path.is_file():
            raise unittest.SkipTest('a6100 CautionConfig.so is not present')
        cls.stock = cls.path.read_bytes()

    def test_patch_changes_only_the_symbol_value(self):
        patched, plan = pp.apply_unlock(self.stock)
        self.assertEqual(plan['patch_offset'], 0x138b4c)
        self.assertEqual(plan['old_st_value'], '0xc5b5c8')
        self.assertEqual(plan['new_st_value'], '0xc5d530')
        self.assertEqual(plan['softskin_dynsym_index'], 49587)
        changed = [i for i in range(len(self.stock)) if self.stock[i] != patched[i]]
        self.assertEqual(changed, [0x138b4c, 0x138b4d])
        self.assertEqual(len(patched), len(self.stock))

    def test_patch_matches_the_reference_patched_file(self):
        reference = Path('/mnt/c/Users/alex/Desktop/a6100-recovery/a6100-fw-recovery/'
                         'restore/CautionConfig.so.patched')
        if not reference.is_file():
            self.skipTest('reference patched file is not available')
        patched, _ = pp.apply_unlock(self.stock)
        self.assertEqual(patched, reference.read_bytes())
        self.assertEqual(__import__('hashlib').md5(patched).hexdigest(),
                         'a91f2f2871b5abf6d468cfa9661fc0f0')

    def test_patch_refuses_an_already_redirected_image(self):
        patched, _ = pp.apply_unlock(self.stock)
        with self.assertRaises(ValueError):
            pp.unlock_plan(patched)

    def test_symbol_lookup_rejects_a_missing_node(self):
        with self.assertRaises(KeyError):
            pp.dynsym_entry(self.stock, 'cmnViewSettingNodeRootDoesNotExist')

    def test_disk_offset_round_trips(self):
        if not ROOTFS.is_file():
            self.skipTest('a6100 rootfs image is not present')
        _, plan = pp.apply_unlock(self.stock)
        where = pp.disk_block_offset(ROOTFS, '/lib/CautionConfig.so', plan['patch_offset'])
        with ROOTFS.open('rb') as stream:
            stream.seek(where['image_offset'])
            on_disk = stream.read(4)
        self.assertEqual(on_disk, self.stock[plan['patch_offset']:plan['patch_offset'] + 4])


class TestProvenance(unittest.TestCase):
    def test_late_mtime_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / 'rootfs.img'
            image.write_bytes(b'')
            calls = []

            def fake_stat(_image, path):
                calls.append(path)
                if path.endswith('CautionConfig.so'):
                    return dict(mode='0644', mtime='Thu Nov 19 19:55:03 2026', stock=False)
                return dict(mode='0555', mtime='Mon Apr  5 20:50:56 2021', stock=True)

            original = pp.debugfs_stat
            pp.debugfs_stat = fake_stat
            try:
                result = pp.provenance(image)
            finally:
                pp.debugfs_stat = original
            self.assertEqual(result['suspect'], ['lib/CautionConfig.so'])
            self.assertFalse(result['ok'])

    def test_real_rootfs_has_one_non_stock_app_file(self):
        if not ROOTFS.is_file():
            self.skipTest('a6100 rootfs image is not present in this checkout')
        result = pp.provenance(ROOTFS)
        # The dumped rootfs carries a CautionConfig.so written on 2026-11-19,
        # long after the 2021 firmware build. It is reported, never "fixed".
        self.assertEqual(result['suspect'], ['lib/CautionConfig.so'])
        self.assertIn('2026', result['files']['lib/CautionConfig.so']['mtime'])


class TestValiditySetter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = DEFS / 'lib/viewUnified2.so'
        if not cls.path.is_file():
            raise unittest.SkipTest('a6100 viewUnified2.so is not present')

    def test_setter_is_the_single_byte_store(self):
        found = pp.validity_setter(self.path.read_bytes())
        self.assertIsNotNone(found)
        self.assertEqual(found['function_va'], '0x413940')
        self.assertEqual(found['store_va'], '0x41394c')
        self.assertEqual(found['store_encoding'], '1870')
        self.assertFalse(found['object_resolved'])

    def test_unexpected_body_is_refused(self):
        data = bytearray(self.path.read_bytes())
        found = pp.validity_setter(bytes(data))
        fn = int(found['function_va'], 16)
        offset = pp.va_to_offset(pp.elf_sections(bytes(data)), fn)
        data[offset + 12:offset + 14] = b'\x00\x00'
        with self.assertRaises(ValueError):
            pp.validity_setter(bytes(data))


if __name__ == '__main__':
    unittest.main()
