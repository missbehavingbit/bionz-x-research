"""Build Film Micro-Texture /usr/sabin/ RAM tables for ILCE-6100 and ILCE-6400 (Still + Movie):
1. Symmetric micro-texture dcv_psf (removes harsh digital edge haloing while keeping 48-tap DC sum 0).
2. Zeroed chroma-edge peaking (spica c_tap = 0.0) & gentle luma roll-off (y_tap = 0.15).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mods.film_sims.studio_core import TARGETS, get_target  # noqa: E402

STOCK_DIR = get_target("a6100").sabin_stock_dir


def build_symmetric_dcv(src_bytes: bytes, header_size: int = 0xEC, target_gain: float = 0.12) -> tuple[bytes, dict]:
    out = bytearray(src_bytes)
    n_i16 = (len(src_bytes) - header_size) // 2
    vals = list(struct.unpack_from(f"<{n_i16}h", src_bytes, header_size))
    n_blk = n_i16 // 48
    mod = list(vals)
    for b in range(n_blk):
        s = b * 48
        blk = vals[s:s + 48]
        if any(blk):
            scaled = [int(round(v * target_gain)) for v in blk]
            c = max(range(48), key=lambda idx: abs(scaled[idx]))
            scaled[c] -= sum(scaled)
            mod[s:s + 48] = [max(-32768, min(32767, x)) for x in scaled]
    rem_start = n_blk * 48
    if rem_start < n_i16:
        mod[rem_start:] = [max(-32768, min(32767, int(round(v * target_gain)))) for v in vals[rem_start:]]
    struct.pack_into(f"<{n_i16}h", out, header_size, *mod)
    return bytes(out), {"size": len(out), "target_gain": target_gain}


def build_spica_15bit(src_bytes: bytes, gain: float) -> tuple[bytes, dict]:
    n_u16 = len(src_bytes) // 2
    raw = struct.unpack_from(f"<{n_u16}H", src_bytes, 0)
    encoded = []
    for u in raw:
        signed = u - 32768 if u >= 16384 else u
        scaled = max(-16384, min(16383, int(round(signed * gain))))
        encoded.append(scaled + 32768 if scaled < 0 else scaled)
    return struct.pack(f"<{n_u16}H", *encoded), {"size": len(encoded) * 2, "gain": gain}


def build_preset(out_dir: Path, dcv_gain: float, y_gain: float, c_gain: float = 0.0,
                 stock_dir: Path | None = None) -> None:
    src_dir = stock_dir or STOCK_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"dcv_gain": dcv_gain, "y_gain": y_gain, "c_gain": c_gain, "tables": []}

    for fname in ("dcv_psf_table_still.bin", "dcv_psf_table_movie_bayer.bin", "dcv_psf_table_movie_4k.bin"):
        src = (src_dir / fname).read_bytes()
        mod, stats = build_symmetric_dcv(src, header_size=0xEC, target_gain=dcv_gain)
        (out_dir / fname).write_bytes(mod)
        manifest["tables"].append({"name": fname, "sha256": hashlib.sha256(mod).hexdigest(), **stats})

    for fname, gain in (
        ("spica_musashi_still_c_tap_step1.bin", c_gain),
        ("spica_musashi_still_c_tap_step2.bin", c_gain),
        ("spica_musashi_still_y_tap_step1.bin", y_gain),
        ("spica_musashi_still_y_tap_step2.bin", y_gain),
        ("spica_musashi_mov_4k_c_tap_step1.bin", c_gain),
        ("spica_musashi_mov_4k_c_tap_step2.bin", c_gain),
        ("spica_musashi_mov_4k_y_tap_step1.bin", y_gain),
        ("spica_musashi_mov_4k_y_tap_step2.bin", y_gain),
    ):
        src = (src_dir / fname).read_bytes()
        mod, stats = build_spica_15bit(src, gain=gain)
        (out_dir / fname).write_bytes(mod)
        manifest["tables"].append({"name": fname, "sha256": hashlib.sha256(mod).hexdigest(), **stats})

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Built {len(manifest['tables'])} sabin tables (dcv={dcv_gain}, y={y_gain}) in {out_dir}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build Film Micro-Texture /usr/sabin/ tables.")
    ap.add_argument("--target", choices=tuple(TARGETS), default="a6100")
    ap.add_argument("--stock-dir", type=Path, default=None, help="Directory holding stock /usr/sabin/*.bin")
    ap.add_argument("--out-root", type=Path, default=None, help="Output parent directory for sabin_film / sabin_grain")
    ap.add_argument("--mode", choices=("soft", "grain", "both"), default="both")
    args = ap.parse_args()
    prof = get_target(args.target)
    stock_dir = args.stock_dir or prof.sabin_stock_dir
    out_root = args.out_root or prof.default_out_root
    if args.mode in ("soft", "both"):
        build_preset(out_root / "sabin_film", dcv_gain=0.12, y_gain=0.15, stock_dir=stock_dir)
    if args.mode in ("grain", "both"):
        build_preset(out_root / "sabin_grain", dcv_gain=1.35, y_gain=1.35, stock_dir=stock_dir)


if __name__ == "__main__":
    main()
