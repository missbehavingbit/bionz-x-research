"""Parse Sony a6400 /usr/sabin/ DCV PSF and Spica tap tables and compare MTF against ARRI AFA filters."""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

SABIN_DIR = Path("firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/sabin")
ARRI_JSON = Path("artifacts/analysis/arri-sdk-pipeline-e0600/e0600_arri_debayer_and_afa_tables.json")
OUT_DIR = Path("artifacts/analysis/arri-sdk-pipeline-e0602")


def compute_2d_mtf(kernel_2d: list[list[float]], scale: float = 1.0, n_freqs: int = 33) -> dict:
    """Compute horizontal (fx, 0) and diagonal (f, f) 2D magnitude frequency response."""
    h = len(kernel_2d)
    w = len(kernel_2d[0])
    cy, cx = h // 2, w // 2
    horiz = []
    diag = []
    for k in range(n_freqs):
        f = 0.5 * k / (n_freqs - 1)
        om = 2.0 * math.pi * f
        re_h = sum(
            (kernel_2d[y][x] / scale) * math.cos((x - cx) * om)
            for y in range(h)
            for x in range(w)
        )
        re_d = sum(
            (kernel_2d[y][x] / scale) * math.cos(((x - cx) + (y - cy)) * om / math.sqrt(2.0))
            for y in range(h)
            for x in range(w)
        )
        horiz.append({"f_cpp": round(f, 4), "mag": round(abs(re_h), 6)})
        diag.append({"f_cpp": round(f, 4), "mag": round(abs(re_d), 6)})
    return {
        "dc_gain": round(sum(sum(r) for r in kernel_2d) / scale, 6),
        "peak_horiz_gain": round(max(p["mag"] for p in horiz), 6),
        "nyquist_horiz_gain": horiz[-1]["mag"],
        "peak_diag_gain": round(max(p["mag"] for p in diag), 6),
        "horiz": horiz,
    }


def decode_dcv_psf(path: Path) -> dict:
    """Decode DCV PSF header and extract 2D kernels from dcv_psf_table_*.bin."""
    b = path.read_bytes()
    vals = struct.unpack_from(f"<{len(b) // 2}h", b, 0)
    u32_hdr = [struct.unpack_from("<I", b, off)[0] for off in range(0x30, 0x80, 4)]
    # Header sizes at 0x40..0x50: packed (h << 16) | w
    # Find peaks (> 3000) in vals to locate kernel centers
    peaks = [(idx, v) for idx, v in enumerate(vals) if idx >= 0x40 and v > 3000]
    kernels = []
    for idx, v in peaks[:6]:
        # Test 5x5, 7x7, 9x9 centered at idx for sum close to 4096 (Q12)
        best = None
        for kh in (5, 7, 9):
            for kw in (5, 7, 9):
                half = (kh * kw) // 2
                if idx - half >= 0 and idx + half < len(vals):
                    sub = vals[idx - half : idx + half + 1]
                    s = sum(sub)
                    if abs(s - 4096) <= 16:
                        k2d = [list(sub[r * kw : (r + 1) * kw]) for r in range(kh)]
                        best = (kh, kw, s, k2d)
                        break
            if best:
                break
        if best:
            kh, kw, s, k2d = best
            kernels.append({
                "center_byte_off": hex(idx * 2),
                "shape": f"{kh}x{kw}",
                "sum": s,
                "min_tap": min(min(r) for r in k2d),
                "max_tap": max(max(r) for r in k2d),
                "mtf": compute_2d_mtf(k2d, scale=4096.0),
                "center_row": k2d[kh // 2],
            })
    return {
        "file": path.name,
        "size": len(b),
        "u32_0x30_to_0x60": [hex(x) for x in u32_hdr[:12]],
        "decoded_kernels_count": len(kernels),
        "kernels": kernels,
    }


def decode_spica(path: Path) -> dict:
    """Decode 15-bit two's complement Spica polyphase tap tables."""
    sb = path.read_bytes()
    u16 = struct.unpack_from(f"<{len(sb) // 2}H", sb, 0)
    s15 = [v if v < 16384 else v - 32768 for v in u16]
    # Find tap stride where consecutive rows sum to ~1024, ~2048, ~4096, or ~16384
    found = {}
    for stride in (4, 6, 8, 10, 12, 16, 20, 24):
        if len(s15) % stride == 0:
            sums = [sum(s15[i * stride : (i + 1) * stride]) for i in range(min(16, len(s15) // stride))]
            if all(abs(s - 1024) <= 16 for s in sums) or all(abs(s - 2048) <= 16 for s in sums) or all(abs(s - 4096) <= 16 for s in sums):
                found[stride] = sums[:8]
    return {
        "file": path.name,
        "size": len(sb),
        "n_taps_total": len(s15),
        "min_s15": min(s15),
        "max_s15": max(s15),
        "first_24_s15": s15[:24],
        "matching_strides": found,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    psf_res = [
        decode_dcv_psf(SABIN_DIR / f)
        for f in ("dcv_psf_table_movie_bayer.bin", "dcv_psf_table_movie_4k.bin", "dcv_psf_table_movie.bin")
    ]
    spica_res = [
        decode_spica(SABIN_DIR / f)
        for f in (
            "spica_musashi_mov_4k_y_tap_step1.bin",
            "spica_musashi_mov_4k_y_tap_step2.bin",
            "spica_musashi_mov_4k_c_tap_step1.bin",
            "spica_musashi_mov_4k_c_tap_step2.bin",
        )
    ]
    arri = json.loads(ARRI_JSON.read_text(encoding="utf-8"))
    # Compare ARRI 0.65 / 0.80 vs Sony DCV PSF
    report = {"dcv_psf": psf_res, "spica_tap": spica_res}
    out_path = OUT_DIR / "e0602_sabin_mtf_analysis.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Wrote", out_path)
    for d in psf_res:
        print(f"\n=== {d['file']} ({d['size']} B) ===")
        for k in d["kernels"]:
            m = k["mtf"]
            print(
                f"  @{k['center_byte_off']} {k['shape']} sum={k['sum']} "
                f"range=[{k['min_tap']}, {k['max_tap']}] "
                f"peak_horiz={m['peak_horiz_gain']} nyq_horiz={m['nyquist_horiz_gain']} "
                f"row={k['center_row']}"
            )
    for s in spica_res:
        print(f"\n=== {s['file']} ({s['size']} B) s15=[{s['min_s15']}, {s['max_s15']}] ===")
        print("  first_24_s15:", s["first_24_s15"])
        print("  matching_strides:", s["matching_strides"])


if __name__ == "__main__":
    main()
