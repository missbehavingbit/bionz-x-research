"""Stage FilmTexture.mod and /tmp_bt/mods/sabin/* on the connected ILCE-6100 or ILCE-6400
over USB, and read live CC_OUT LutMode for 5x9x9 3D LUT readiness.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mods.film_sims.studio_core import TARGETS, get_target, open_camera_debugger  # noqa: E402
from tools.camera_debugger import OP_EXEC  # noqa: E402

SABIN_FILES = (
    "dcv_psf_table_still.bin",
    "dcv_psf_table_movie_bayer.bin",
    "dcv_psf_table_movie_4k.bin",
    "spica_musashi_still_y_tap_step1.bin",
    "spica_musashi_still_y_tap_step2.bin",
    "spica_musashi_mov_4k_y_tap_step1.bin",
    "spica_musashi_mov_4k_y_tap_step2.bin",
)


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage FilmTexture.mod and sabin tables over USB.")
    ap.add_argument("--target", choices=tuple(TARGETS), default="a6100")
    ap.add_argument("--out-root", type=Path, default=None)
    args = ap.parse_args()

    prof = get_target(args.target)
    out_root = args.out_root or prof.default_out_root
    sabin_dir = out_root / "sabin_film"
    mod_path = out_root / "film_texture/FilmTexture.mod"

    with open_camera_debugger(target=prof.key) as (dbg, drive):
        print(f"[+] Connected to {prof.label} via {drive}")

        cc = dbg.read("/dev/mem", prof.cc_out_pa, 0x80)
        lut_mode = cc[4]
        lut_en = cc[0xC]
        lut_type = struct.unpack_from("<H", cc, 0x68)[0]
        conv_sd = cc[0x69]
        print(f"[+] CC_OUT ({prof.cc_out_pa:#010x}) LutMode={lut_mode:#x} en={lut_en} lut_type={lut_type} ConvSD={conv_sd}")

        dbg.request(OP_EXEC, b"mkdir -p /tmp_bt/mods/sabin")
        for fname in SABIN_FILES:
            data = (sabin_dir / fname).read_bytes()
            dbg.push(f"/tmp_bt/mods/sabin/{fname}", data)
            print(f"  pushed /tmp_bt/mods/sabin/{fname} ({len(data)} B)")

        res = dbg.push("/tmp_bt/mods/FilmTexture.mod", mod_path.read_bytes())
        print(f"[+] Staged /tmp_bt/mods/FilmTexture.mod ({res['bytes']} B, sha256={res['sha256'][:12]}...)")


if __name__ == "__main__":
    main()
