"""Unit tests for the multi-target (ILCE-6100 / ILCE-6400) Film Sim Studio core & builders."""
import os
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
except ImportError:
    np = None

from mods.film_sims import studio_core as sc

ROOT = Path(__file__).resolve().parents[1]


def _synthetic_stock() -> dict[str, list[int]]:
    return {
        "ec_stock": [((38 + i * 9) << 6) for i in range(96)],
        "fc_stock": [min(1023, 9 + i * 11) for i in range(96)],
        "a0_stock": list(sc.STOCK_A0),
        "a1_stock": list(sc.STOCK_A1),
        "b2_stock": list(sc.STOCK_B2),
    }


def _has_arm_gcc() -> bool:
    local_bin = ROOT / "artifacts/toolchain/arm-linux-gnueabi/bin/arm-linux-gnueabi-gcc"
    return os.name != "nt" and (shutil.which("arm-linux-gnueabi-gcc") is not None or local_bin.is_file())


class TestFilmSimStudioCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stock = _synthetic_stock()

    def test_all_presets_monotone_and_row_sum_invariant(self):
        for name, p in sc.PRESETS.items():
            ec, fc = sc.compute_curves(self.stock, p)
            self.assertEqual(len(ec), 96, name)
            self.assertEqual(len(fc), 96, name)
            self.assertTrue(all(ec[i] >= ec[i - 1] for i in range(1, 96)), name)
            self.assertTrue(all(fc[i] >= fc[i - 1] for i in range(1, 96)), name)
            self.assertTrue(all(0 <= v <= 1023 for v in fc), name)
            self.assertTrue(all((v & 0x3F) == 0 and 0 <= (v >> 6) <= 1023 for v in ec), name)

            a0 = sc.compute_q10_matrix(self.stock["a0_stock"], p)
            a1 = sc.compute_q10_matrix(self.stock["a1_stock"], p)
            b2 = sc.compute_q10_matrix(self.stock["b2_stock"], p)
            for r in (0, 3, 6):
                self.assertEqual(sum(a0[r:r + 3]), sum(self.stock["a0_stock"][r:r + 3]), name)
                self.assertEqual(sum(a1[r:r + 3]), sum(self.stock["a1_stock"][r:r + 3]), name)
                self.assertEqual(sum(b2[r:r + 3]), sum(self.stock["b2_stock"][r:r + 3]), name)

    def test_stock_reset_is_exact_identity(self):
        p = sc.PRESETS["Stock (Reset)"]
        ec, fc = sc.compute_curves(self.stock, p)
        self.assertEqual(ec, self.stock["ec_stock"])
        self.assertEqual(fc, self.stock["fc_stock"])
        self.assertEqual(sc.compute_q10_matrix(self.stock["a1_stock"], p), self.stock["a1_stock"])

        if np is not None:
            rng = np.random.default_rng(42)
            img = rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8)
            out = sc.simulate_image(img, self.stock, p)
            self.assertTrue(np.array_equal(img, out))

    def test_multi_target_address_bounds_and_headers(self):
        a61, a64 = sc.get_target("a6100"), sc.get_target("a6400")
        self.assertNotEqual(a61.ec_gamma_pa, a64.ec_gamma_pa)
        self.assertNotEqual(a61.cc_tuning_pa, a64.cc_tuning_pa)
        self.assertNotEqual(a61.refadr0_pa, a64.refadr0_pa)
        for key in ("a6100", "a6400"):
            prof = sc.get_target(key)
            for pa, size in (
                (prof.ec_gamma_pa, 384),
                (prof.fc_gamma_pa, 192),
                (prof.color_a0_pa, 18),
                (prof.color_a1_pa, 18),
                (prof.color_b2_pa, 18),
                (prof.cc_tuning_pa, 2),
            ):
                self.assertTrue(0x035C6000 <= pa and pa + size <= 0x045CC4E8, f"{key} {pa:#x}")
                self.assertEqual(pa >> 12, (pa + size - 1) >> 12, f"{key} {pa:#x} crosses page")
            # RefAdr0 is explicitly split across one 4 KiB page boundary (2 pages max)
            self.assertLessEqual((prof.refadr0_pa + 1214) >> 12, (prof.refadr0_pa >> 12) + 1)

        with tempfile.TemporaryDirectory() as tmp:
            hdr64 = Path(tmp) / "film_sim_a6400.h"
            sc.emit_header(hdr64, self.stock, sc.PRESETS["Classic Neg"], target="a6400")
            t64 = hdr64.read_text(encoding="utf-8")
            self.assertIn("#define EC_GAMMA_PA 0x03e55330u", t64)
            self.assertIn("#define COLOR_A1_PA 0x0444a256u", t64)
            self.assertIn("#define CC_TUNING_PA 0x0444a19eu", t64)
            self.assertIn("#define REFADR0_PA 0x044acd27u", t64)

            live64 = sc.emit_live_picker_header(Path(tmp) / "live_a6400.h", stock=self.stock, target="a6400")
            l64 = live64.read_text(encoding="utf-8")
            self.assertIn("#define LIVE_N_PRESETS 8u", l64)
            self.assertIn("#define EC_GAMMA_PA 0x03e55330u", l64)
            expected_luts = 7 if np is not None else 4
            self.assertIn(f"const uint8_t LIVE_REFADR[{expected_luts}][1215]", l64)

            live61 = sc.emit_live_picker_header(Path(tmp) / "live_a6100.h", stock=self.stock, target="a6100")
            l61 = live61.read_text(encoding="utf-8")
            self.assertIn("#define EC_GAMMA_PA 0x03e553b0u", l61)
            self.assertIn("#define REFADR0_PA 0x044acd37u", l61)

    def test_live_picker_sources_encode_a6100_and_a6400_targets(self):
        live_hdr = (ROOT / "mods/film_sims/live_picker/film_sim_live_data.h").read_text(encoding="utf-8")
        for needle in (
            "TARGET_A6400",
            "0x03e55330u",
            "0x0444a19eu",
            "TARGET_A6400_V1",
            "0x03e3e6ccu",
            "0x03e553b0u",
            "#define LIVE_N_PRESETS 8u",
            "const uint8_t LIVE_REFADR[7][1215]",
        ):
            self.assertIn(needle, live_hdr)
        tramp = (ROOT / "mods/film_sims/live_picker/film_sim_live_trampoline.S").read_text(encoding="utf-8")
        self.assertIn("0x0655a535", tramp)
        self.assertIn("TARGET_A6400_V1", tramp)
        live_c = (ROOT / "mods/film_sims/live_picker/film_sim_live.c").read_text(encoding="utf-8")
        self.assertIn("cc_bypass = 0xffffu", live_c)
        self.assertIn("write_refadr_bytes(fd, 0)", live_c)

    @unittest.skipIf(np is None, "numpy required for full 3D LUT bridge tests")
    def test_full_3d_lut_bridge_presets_and_cube_import(self):
        from mods.film_sims.film_recipes_3d import build_recipe_cube
        from mods.film_sims.lut_bridge import fit_cube_file_to_isp, fit_cube_to_isp

        for name in sc.LIVE_PICKER_ORDER:
            cube = build_recipe_cube(name)
            if name != "ACROS B&W":
                red_sample = cube.sample([[1.0, 0.0, 0.0]])[0]
                blue_sample = cube.sample([[0.0, 0.0, 1.0]])[0]
                self.assertGreater(red_sample[0], red_sample[2] + 0.3, f"{name} R/B swapped on cube")
                self.assertGreater(blue_sample[2], blue_sample[0] + 0.3, f"{name} B/R swapped on cube")
            fitted = fit_cube_to_isp(cube, self.stock, name=name)
            if name != "ACROS B&W":
                self.assertGreater(fitted.a1_sim[0], 500, f"{name} A1[0,0]={fitted.a1_sim[0]}")
                self.assertGreater(fitted.a1_sim[8], 500, f"{name} A1[2,2]={fitted.a1_sim[8]}")
            self.assertEqual(len(fitted.ec_sim), 96)
            self.assertEqual(len(fitted.fc_sim), 96)
            self.assertTrue(all(fitted.ec_sim[i] >= fitted.ec_sim[i - 1] for i in range(1, 96)), name)
            self.assertTrue(all(fitted.fc_sim[i] >= fitted.fc_sim[i - 1] for i in range(1, 96)), name)
            self.assertEqual(len(fitted.refadr_bytes), 1215, name)
            self.assertGreater(fitted.report["nonzero_dy_nodes"], 0, name)
            self.assertEqual(fitted.report["at_rail_nodes"], 0, name)
            for r in (0, 3, 6):
                self.assertEqual(sum(fitted.a0_sim[r:r + 3]), sum(self.stock["a0_stock"][r:r + 3]), name)
                self.assertEqual(sum(fitted.a1_sim[r:r + 3]), sum(self.stock["a1_stock"][r:r + 3]), name)
                self.assertEqual(sum(fitted.b2_sim[r:r + 3]), sum(self.stock["b2_stock"][r:r + 3]), name)

        # Round-trip .cube import
        with tempfile.TemporaryDirectory() as tmp:
            cube_path = Path(tmp) / "test_warm.cube"
            lines = ['TITLE "Test Warm"', "LUT_3D_SIZE 5"]
            for b in range(5):
                for g in range(5):
                    for r in range(5):
                        rf = min(1.0, (r / 4.0) * 1.05)
                        gf = g / 4.0
                        bf = (b / 4.0) * 0.92
                        lines.append(f"{rf:.6f} {gf:.6f} {bf:.6f}")
            cube_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            res = fit_cube_file_to_isp(cube_path, self.stock)
            self.assertEqual(res.name, "Test Warm")
            self.assertEqual(len(res.refadr_bytes), 1215)
            self.assertLess(res.report["mean_de76"], 2.0)

    @unittest.skipUnless(_has_arm_gcc(), "arm-linux-gnueabi-gcc not installed in this environment")
    def test_build_mod_produces_valid_smod_v2(self):
        with tempfile.TemporaryDirectory() as tmp:
            for target in ("a6100", "a6400"):
                out_dir = Path(tmp) / target
                res = sc.build_mod(sc.PRESETS["Classic Neg"], out_dir=out_dir, target=target, stock=self.stock)
                self.assertEqual(res["target"], target)
                mod = Path(res["local_path"]).read_bytes()
                magic, ver, flags, name, code_size, _fnv, _bss, on_off, off_off, hooks = struct.unpack_from("<III32s6I", mod)
                self.assertEqual(magic, 0x444F4D53)
                self.assertEqual(ver, 2)
                self.assertEqual(flags, 16)
                self.assertEqual(name.rstrip(b"\0"), b"Classic Neg")
                self.assertEqual(hooks, 0)
                self.assertEqual(len(mod), 68 + code_size)
                self.assertEqual(on_off, off_off)
                self.assertGreater(on_off, 0)
                lut = sc.build_3dlut_1215(sc.PRESETS["Classic Neg"])
                self.assertEqual(len(lut), 1215)

    @unittest.skipUnless(
        sc.get_target("a6100").avcam_default_path.is_file()
        and sc.get_target("a6400").avcam_default_path.is_file(),
        "local a6100 and a6400 av-cam.bin artifacts required for cross-body stock table parity",
    )
    def test_real_avcam_stock_tables_match_across_a6100_and_a6400(self):
        s61 = sc.load_stock_tables(target="a6100")
        s64 = sc.load_stock_tables(target="a6400")
        self.assertEqual(s61["ec_stock"], s64["ec_stock"])
        self.assertEqual(s61["fc_stock"], s64["fc_stock"])
        self.assertEqual(s61["a0_stock"], s64["a0_stock"])
        self.assertEqual(s61["a1_stock"], s64["a1_stock"])
        self.assertEqual(s61["b2_stock"], s64["b2_stock"])


if __name__ == "__main__":
    unittest.main()
