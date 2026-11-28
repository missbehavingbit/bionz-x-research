"""The v2 loader's view hooks, shim window, and PLT constants, retargeted to the
ILCE-7C (a7C) build."""

import os
import unittest
from pathlib import Path

from tools.native_camera.mlv2 import a7c_layout as layout

ROOT = Path(__file__).resolve().parents[1]
BINS_ROOT = Path(os.environ.get('A7C_BINS_ROOT', '')) if os.environ.get('A7C_BINS_ROOT') else None
IMAGE = (BINS_ROOT / 'viewUnified2.so') if BINS_ROOT else (ROOT / 'artifacts/a7c/viewUnified2.so')


class TestA7CLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not IMAGE.is_file():
            raise unittest.SkipTest('a7c viewUnified2.so is not present in this checkout')
        cls.image = IMAGE.read_bytes()

    def test_three_view_hooks_resolve(self):
        hooks = layout.verify(self.image)
        self.assertEqual(hooks['set_root']['offset'], 0x7fa2e0)
        self.assertEqual(hooks['refresh']['offset'], 0x7fa718)
        self.assertEqual(hooks['open_item']['offset'], 0x7facdc)
        for name in ('set_root', 'refresh', 'open_item'):
            self.assertEqual(hooks[name]['trap_after'], hooks[name]['replay'])

    def test_plt_constants_resolve_to_stubs(self):
        hooks = layout.verify(self.image)
        self.assertEqual(hooks['SET_TEXT']['offset'], 0x181c44)
        self.assertEqual(hooks['SET_MODE']['offset'], 0x188b3c)

    def test_unknown_build_is_refused(self):
        with self.assertRaises(layout.LayoutMismatch):
            layout.verify(self.image, expect_sha256='0' * 64)

    def test_moved_site_is_refused(self):
        data = bytearray(self.image)
        off = layout.HOOKS['set_root']['a7c']
        data[off:off + 2] = b'\x00\x00'
        with self.assertRaises(layout.LayoutMismatch):
            layout.verify(bytes(data), expect_sha256=None)


class TestA7CTargetWiring(unittest.TestCase):
    def test_mlv2_sources_define_a7c_target(self):
        mlv2_dir = ROOT / 'tools/native_camera/mlv2'
        self.assertIn('MLV2_TARGET_A7C', (mlv2_dir / 'mlv2.h').read_text(encoding='utf-8'))
        self.assertIn('MLV2_TARGET_A7C', (mlv2_dir / 'mlv2_trampoline.S').read_text(encoding='utf-8'))
        self.assertIn('MLV2_TARGET_A7C', (mlv2_dir / 'mlv2_guard.c').read_text(encoding='utf-8'))
        self.assertIn('MLV2_TARGET_A7C', (mlv2_dir / 'core.c').read_text(encoding='utf-8'))
        self.assertIn('a7c', (mlv2_dir / 'package.py').read_text(encoding='utf-8'))
        self.assertIn('a7c', (mlv2_dir / 'build_wsl.sh').read_text(encoding='utf-8'))

    def test_a7c_offsets_differ_from_a6400(self):
        for row in layout.HOOKS.values():
            self.assertNotEqual(row['a7c'], row['a6400'])
        for row in layout.CONSTANTS.values():
            self.assertNotEqual(row['a7c'], row['a6400'])
