"""Build ARRI-tuned /usr/sabin/ RAM tables for Arrivederci v4:
1. Symmetric ~3.2% micro-texture dcv_psf (matching ARRI ALEV4_AFATexture_0.80_smooth).
2. ARRI Chroma Smooth spica_musashi_mov_4k_c_tap (zeroed chroma-edge peaking) &
   attenuated y_tap (0.15x luma ringing suppression).
"""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import numpy as np

SABIN_DIR = Path("firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/sabin")
OUT_DIR = Path("artifacts/analysis/arri-v4-sabin")


def build_symmetric_dcv(src_bytes: bytes, header_size: int = 0xEC, target_gain: float = 0.145) -> tuple[bytes, dict]:
    """Attenuate and symmetrize dcv_psf blocks to ~3.2% symmetric step overshoot/undershoot."""
    out = bytearray(src_bytes)
    n_i16 = (len(src_bytes) - header_size) // 2
    vals = np.array(struct.unpack_from(f"<{n_i16}h", src_bytes, header_size), dtype=np.int32)
    # Process in 48-int16 blocks (0x60 bytes) where stock blocks reside; zero-sum & symmetric.
    n_blk = n_i16 // 48
    mod = vals.copy()
    for b in range(n_blk):
        s = b * 48
        blk = vals[s:s + 48]
        if np.any(blk):
            scaled = np.rint(blk * target_gain).astype(np.int32)
            # Enforce exact zero DC sum on center tap so flat fields never shift brightness.
            c = int(np.argmax(np.abs(scaled)))
            dc_err = int(scaled.sum())
            scaled[c] -= dc_err
            mod[s:s + 48] = np.clip(scaled, -32768, 32767)
    # Remainder (if any) scaled cleanly
    rem_start = n_blk * 48
    if rem_start < n_i16:
        mod[rem_start:] = np.clip(np.rint(vals[rem_start:] * target_gain), -32768, 32767).astype(np.int32)

    struct.pack_into(f"<{n_i16}h", out, header_size, *[int(x) for x in mod])

    # Verify 1D step response (0 -> 1 edge) on first non-zero 48-tap block (9x5-ish 1D slice around peak)
    first_nz = next((b for b in range(n_blk) if np.any(mod[b * 48:(b + 1) * 48])), 0)
    k = mod[first_nz * 48:(first_nz + 1) * 48].astype(np.float64) / 16384.0
    # 7-tap horizontal slice centered at peak (indices 21..27 around center 24)
    c_idx = int(np.argmax(np.abs(k)))
    h_slice = k[max(0, c_idx - 3):min(48, c_idx + 4)]
    step = np.concatenate([np.zeros(16), np.ones(16)])
    resp = step + np.convolve(step, h_slice, mode="same")
    overshoot = float(max(0.0, resp.max() - 1.0)) * 100.0
    undershoot = float(max(0.0, -resp.min())) * 100.0
    return bytes(out), {
        "overshoot_pct": round(overshoot, 2),
        "undershoot_pct": round(undershoot, 2),
        "peak": round(float(resp.max()), 4),
    }


def build_spica_15bit(src_bytes: bytes, gain: float) -> tuple[bytes, dict]:
    """Scale 15-bit two's complement spica delta table by gain (0.0 = flat ARRI chroma smooth)."""
    raw = np.frombuffer(src_bytes, dtype="<u2")
    signed = np.where(raw >= 16384, raw.astype(np.int32) - 32768, raw.astype(np.int32))
    scaled = np.clip(np.rint(signed * gain), -16384, 16383).astype(np.int32)
    encoded = np.where(scaled < 0, scaled + 32768, scaled).astype("<u2")
    return encoded.tobytes(), {
        "gain": gain,
        "orig_min": int(signed.min()),
        "orig_max": int(signed.max()),
        "new_min": int(scaled.min()),
        "new_max": int(scaled.max()),
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"tables": []}

    for fname in ("dcv_psf_table_movie_bayer.bin", "dcv_psf_table_movie_4k.bin"):
        src = (SABIN_DIR / fname).read_bytes()
        mod, stats = build_symmetric_dcv(src, header_size=0xEC, target_gain=0.075)
        assert len(mod) == len(src)
        (OUT_DIR / fname).write_bytes(mod)
        manifest["tables"].append({
            "name": fname,
            "size": len(mod),
            "sha256": hashlib.sha256(mod).hexdigest(),
            **stats,
        })

    for fname, gain in (
        ("spica_musashi_mov_4k_c_tap_step1.bin", 0.0),
        ("spica_musashi_mov_4k_c_tap_step2.bin", 0.0),
        ("spica_musashi_mov_4k_y_tap_step1.bin", 0.15),
        ("spica_musashi_mov_4k_y_tap_step2.bin", 0.15),
    ):
        src = (SABIN_DIR / fname).read_bytes()
        mod, stats = build_spica_15bit(src, gain=gain)
        assert len(mod) == len(src)
        (OUT_DIR / fname).write_bytes(mod)
        manifest["tables"].append({
            "name": fname,
            "size": len(mod),
            "sha256": hashlib.sha256(mod).hexdigest(),
            **stats,
        })

    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
