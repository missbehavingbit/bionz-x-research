"""Offline tests for the Classic Chrome SMOD v2 film simulation mod (a6100 & a6400)."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mods.film_sims.classic_chrome import make_data as cc

ROOT = Path(__file__).resolve().parents[1]


def _has_arm_gcc() -> bool:
    local_bin = ROOT / "artifacts/toolchain/arm-linux-gnueabi/bin/arm-linux-gnueabi-gcc"
    return os.name != "nt" and (shutil.which("arm-linux-gnueabi-gcc") is not None or local_bin.is_file())


class TestA6100ClassicChrome(unittest.TestCase):
    def test_tone_curve_monotonicity_and_bounds(self):
        stock_ec = [((38 + i * 9) << 6) for i in range(96)]
        chrome_ec = cc.transform_ec_curve(stock_ec)
        self.assertEqual(len(chrome_ec), 96)
        self.assertTrue(all(chrome_ec[i] >= chrome_ec[i - 1] for i in range(1, 96)))
        self.assertTrue(all((v & 0x3F) == 0 and 0 <= (v >> 6) <= 1023 for v in chrome_ec))

        stock_fc = [min(1023, 9 + i * 11) for i in range(96)]
        chrome_fc = cc.transform_fc_curve(stock_fc)
        self.assertEqual(len(chrome_fc), 96)
        self.assertTrue(all(chrome_fc[i] >= chrome_fc[i - 1] for i in range(1, 96)))
        self.assertTrue(all(0 <= v <= 1023 for v in chrome_fc))

    def test_q10_matrix_preserves_exact_row_sums(self):
        for stock in (
            [1241, -205, -12, 62, 1116, -154, 30, -251, 1245],
            [1161, -205, 68, 43, 1134, -153, 16, -236, 1243],
        ):
            out = cc.transform_cc_matrix(stock)
            self.assertEqual(len(out), 9)
            self.assertNotEqual(out, stock)
            for r in (0, 3, 6):
                self.assertEqual(sum(out[r:r + 3]), sum(stock[r:r + 3]))

    def test_target_pages_do_not_cross_4k_boundaries(self):
        for pa, size in (
            (cc.EC_REC28_V1_PA, 384),
            (cc.FC_TBL0_PA, 192),
            (cc.COLOR_A0_PA, 18),
            (cc.COLOR_A1_PA, 18),
        ):
            self.assertTrue(0x035C6000 <= pa and pa + size <= 0x045CC4E8)
            self.assertEqual(pa >> 12, (pa + size - 1) >> 12)

    def test_a6400_target_pages_do_not_cross_4k_boundaries(self):
        prof = cc.sc.get_target("a6400")
        for pa, size in (
            (prof.ec_gamma_pa, 384),
            (prof.fc_gamma_pa, 192),
            (prof.color_a0_pa, 18),
            (prof.color_a1_pa, 18),
        ):
            self.assertTrue(0x035C6000 <= pa and pa + size <= 0x045CC4E8)
            self.assertEqual(pa >> 12, (pa + size - 1) >> 12)

    @unittest.skipUnless(_has_arm_gcc(), "arm-linux-gnueabi-gcc not installed in this environment")
    def test_classic_chrome_c_compiles_for_a6400_without_abs_relocs(self):
        gcc = shutil.which("arm-linux-gnueabi-gcc") or str(
            ROOT / "artifacts/toolchain/arm-linux-gnueabi/bin/arm-linux-gnueabi-gcc"
        )
        readelf = shutil.which("arm-linux-gnueabi-readelf") or str(
            ROOT / "artifacts/toolchain/arm-linux-gnueabi/bin/arm-linux-gnueabi-readelf"
        )
        prof = cc.sc.get_target("a6400")
        stock_ec = [((38 + i * 9) << 6) for i in range(96)]
        stock_fc = [min(1023, 9 + i * 11) for i in range(96)]
        a0 = list(cc.sc.STOCK_A0)
        a1 = list(cc.sc.STOCK_A1)
        hdr = (
            "#include <stdint.h>\n"
            f"#define EC_GAMMA_PA 0x{prof.ec_gamma_pa:08x}u\n"
            f"#define FC_GAMMA_PA 0x{prof.fc_gamma_pa:08x}u\n"
            f"#define COLOR_A0_PA 0x{prof.color_a0_pa:08x}u\n"
            f"#define COLOR_A1_PA 0x{prof.color_a1_pa:08x}u\n"
            f"static const uint32_t EC_GAMMA_STOCK[96] = {{{cc.fmt(stock_ec, True)}}};\n"
            f"static const uint32_t EC_GAMMA_CHROME[96] = {{{cc.fmt(cc.transform_ec_curve(stock_ec), True)}}};\n"
            f"static const uint32_t EC_GAMMA_LEGACY[96] = {{{cc.fmt(cc.transform_ec_curve(stock_ec, cc.classic_chrome_tone_v1), True)}}};\n"
            f"static const uint16_t FC_GAMMA_STOCK[96] = {{{cc.fmt(stock_fc, False)}}};\n"
            f"static const uint16_t FC_GAMMA_CHROME[96] = {{{cc.fmt(cc.transform_fc_curve(stock_fc), False)}}};\n"
            f"static const uint16_t FC_GAMMA_LEGACY[96] = {{{cc.fmt(cc.transform_fc_curve(stock_fc, cc.classic_chrome_tone_v1), False)}}};\n"
            f"static const int16_t COLOR_A0_STOCK[9] = {{{cc.fmt(a0, False)}}};\n"
            f"static const int16_t COLOR_A0_CHROME[9] = {{{cc.fmt(cc.transform_cc_matrix(a0), False)}}};\n"
            f"static const int16_t COLOR_A0_LEGACY[9] = {{{cc.fmt(cc.transform_cc_matrix(a0, cc.M_CHROME_V1), False)}}};\n"
            f"static const int16_t COLOR_A1_STOCK[9] = {{{cc.fmt(a1, False)}}};\n"
            f"static const int16_t COLOR_A1_CHROME[9] = {{{cc.fmt(cc.transform_cc_matrix(a1), False)}}};\n"
            f"static const int16_t COLOR_A1_LEGACY[9] = {{{cc.fmt(cc.transform_cc_matrix(a1, cc.M_CHROME_V1), False)}}};\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "classic_chrome_data.h").write_text(hdr, encoding="utf-8")
            obj = t / "classic_chrome.o"
            subprocess.run(
                [
                    gcc, "-mthumb", "-mcpu=cortex-a5", "-mfloat-abi=softfp", "-Os",
                    "-ffreestanding", "-fno-builtin", "-nostdlib", "-nostartfiles",
                    "-fno-stack-protector", "-fomit-frame-pointer", "-Wall", "-Wextra", "-Werror",
                    "-fpie", "-fvisibility=hidden", f"-I{t}",
                    "-c", str(ROOT / "mods/film_sims/classic_chrome/classic_chrome.c"),
                    "-o", str(obj),
                ],
                check=True,
            )
            rel = subprocess.run([readelf, "-rW", str(obj)], capture_output=True, text=True, check=True)
            self.assertNotRegex(rel.stdout, r"R_ARM_(ABS32|GOT|TARGET1|PLT32)")


if __name__ == "__main__":
    unittest.main()
