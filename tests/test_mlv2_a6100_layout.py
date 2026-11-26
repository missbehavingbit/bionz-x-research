"""The v2 loader's view hooks, shim window and PLT constants, retargeted to the
ILCE-6100 V1.00 build."""

import hashlib
import unittest
from pathlib import Path

from tools.native_camera.mlv2 import a6100_layout as layout

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / 'artifacts/a6100-pp/extract/a6100-lib/viewUnified2.so'
TESTCMD = ROOT / 'artifacts/a6100-pp/extract/a6100-lib/libtestcmd.so'


class TestA6100Layout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not IMAGE.is_file():
            raise unittest.SkipTest('a6100 viewUnified2.so is not present in this checkout')
        cls.image = IMAGE.read_bytes()

    def test_three_view_hooks_resolve(self):
        hooks = layout.verify(self.image)
        self.assertEqual(hooks['set_root']['offset'], 0x5acc0c)
        self.assertEqual(hooks['refresh']['offset'], 0x5ad038)
        self.assertEqual(hooks['open_item']['offset'], 0x5ad5fc)
        for name in ('set_root', 'refresh', 'open_item'):
            self.assertEqual(hooks[name]['trap_after'], hooks[name]['replay'])

    def test_plt_constants_resolve_to_stubs(self):
        hooks = layout.verify(self.image)
        self.assertEqual(hooks['SET_TEXT']['offset'], 0x150030)
        self.assertEqual(hooks['SET_MODE']['offset'], 0x1563c8)
        self.assertEqual(hooks['SET_TEXT']['symbol'],
                         '_ZN9GEN_Label13setTextDirectEPKc')
        self.assertEqual(hooks['SET_MODE']['symbol'],
                         '_ZN2ux6wgtsys6Widget7setModeENS1_10WidgetModeE')

    def test_originals_match_the_a6400_trampoline_source(self):
        text = (ROOT / 'tools/native_camera/mlv2/mlv2_trampoline.S').read_text()
        for off in ('0x5ab654', '0x5aba80', '0x5ac044'):
            self.assertIn(off, text)
        hooks = layout.verify(self.image)
        for name in ('set_root', 'refresh', 'open_item'):
            self.assertEqual(self.image.count(hooks[name]['signal']), 1, name)

    def test_unknown_build_is_refused(self):
        with self.assertRaises(layout.LayoutMismatch):
            layout.verify(self.image, expect_sha256='0' * 64)

    def test_moved_site_is_refused(self):
        data = bytearray(self.image)
        off = layout.HOOKS['set_root']['a6100']
        data[off:off + 2] = b'\x00\x00'
        with self.assertRaises(layout.LayoutMismatch):
            layout.verify(bytes(data), expect_sha256=None)

    def test_a6400_offsets_are_not_silently_used(self):
        for row in layout.HOOKS.values():
            self.assertNotEqual(row['a6100'], row['a6400'])
        for row in layout.CONSTANTS.values():
            self.assertNotEqual(row['a6100'], row['a6400'])
        for row in layout.RECT_TABLES.values():
            self.assertNotEqual(row['a6100'], row['a6400'])
        self.assertNotEqual(layout.V1_ANCHOR['a6100'], layout.V1_ANCHOR['a6400'])

    def test_rect_tables_and_v1_anchor_resolve(self):
        hooks = layout.verify(self.image)
        self.assertEqual(hooks['rect_tbl1_slot6']['offset'], 0x803984)
        self.assertEqual(hooks['rect_tbl2_slot6']['offset'], 0x803a04)
        self.assertEqual(hooks['v1_anchor']['offset'], 0xb7a658)
        self.assertLessEqual(hooks['v1_anchor']['offset'] + hooks['v1_anchor']['size'],
                             hooks['v1_anchor']['page_end'])

    def test_non_zero_rect_slot_is_refused(self):
        data = bytearray(self.image)
        off = layout.RECT_TABLES['rect_tbl1_slot6']['a6100']
        data[off] = 1
        with self.assertRaises(layout.LayoutMismatch):
            layout.verify(bytes(data), expect_sha256=None)


class TestCave(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not TESTCMD.is_file():
            raise unittest.SkipTest('a6100 libtestcmd.so is not present in this checkout')
        cls.lib = TESTCMD.read_bytes()

    def test_shim_window_is_past_eof_and_fits_the_page(self):
        cave = layout.verify_cave(self.lib)
        self.assertEqual(cave['start'], 0x2800)
        self.assertEqual(cave['size'], 2048)
        self.assertEqual(cave['page_end'], 0x3000)
        self.assertGreater(cave['start'], len(self.lib))

    def test_wrong_libtestcmd_is_refused(self):
        mutated = self.lib[:8] + b'\xff' + self.lib[9:]
        with self.assertRaises(layout.LayoutMismatch):
            layout.verify_cave(mutated)

    def test_cave_hash_matches_the_module_list(self):
        self.assertEqual(hashlib.sha256(self.lib).hexdigest(), layout.TESTCMD_SHA256)


class TestLibObjEvents(unittest.TestCase):
    """The a6400 core's five opaque libObj addresses are four named symbols."""

    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / 'artifacts/a6100-pp/extract/a6100-lib/libObj.so'
        if not cls.path.is_file():
            raise unittest.SkipTest('a6100 libObj.so is not present in this checkout')
        cls.image = cls.path.read_bytes()

    def test_all_four_references_resolve(self):
        events = layout.verify_libobj(self.image)
        self.assertEqual(events['event_new']['offset'], 0xfd974)
        self.assertEqual(events['event_ctor']['offset'], 0x840f7d)
        self.assertEqual(events['event_push']['offset'], 0x842529)
        self.assertEqual(events['event_mgr']['offset'], 0x1425c70)

    def test_symbols_are_the_a6400_ones(self):
        events = layout.verify_libobj(self.image)
        self.assertEqual(events['event_new']['symbol'], '_Znwj')
        self.assertEqual(events['event_ctor']['symbol'], '_ZN5EventC1Emhh')
        self.assertEqual(events['event_push']['symbol'],
                         '_ZN12EventManager4pushEP5Eventb')
        self.assertEqual(events['event_mgr']['symbol'], 'g_pEvtMgr')

    def test_unknown_build_is_refused(self):
        with self.assertRaises(layout.LayoutMismatch):
            layout.verify_libobj(self.image + b'\0')

    def test_moved_site_is_refused(self):
        data = bytearray(self.image)
        off = layout.LIBOBJ_EVENTS['event_push']['a6100']
        data[off:off + 2] = b'\x00\x00'
        with self.assertRaises(layout.LayoutMismatch):
            layout.verify_libobj(bytes(data))

    def test_a6400_definitions_are_not_reused(self):
        for name in ('event_ctor', 'event_push', 'event_mgr'):
            row = layout.LIBOBJ_EVENTS[name]
            self.assertNotEqual(row['a6100'], row['a6400'], name)

    def test_runtime_rule_reproduces_the_shipped_a6400_constant(self):
        # The shipped loader addresses g_pEvtMgr's GOT entry as 0x07089984; the
        # same cell is ELF 0x1371984 in the a6400 image, with the RW maps line
        # 0x07016000 / pgoff 0x012f6000.
        self.assertEqual(layout.libobj_runtime(0x1371984, 0x07016000, 0x012f6000),
                         0x07089984)
        # The singleton itself is 0x940 lower in .bss and lands in the anonymous
        # mapping that continues libObj's RW segment.
        self.assertEqual(layout.libobj_runtime(0x1424d70, 0x07016000, 0x012f6000),
                         0x0713cd70)

    def test_a6100_segment_is_page_rounded_like_the_a6400_one(self):
        seg = layout.LIBOBJ_SEGMENT
        self.assertEqual(seg['p_vaddr'] - seg['p_offset'], seg['gap'])
        self.assertEqual((seg['p_offset'] + 0xfff) & ~0xfff, 0x12f7000)


if __name__ == '__main__':
    unittest.main()
