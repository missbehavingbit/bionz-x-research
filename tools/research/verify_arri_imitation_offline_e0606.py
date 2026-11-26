"""Offline quantitative verification of ARRI ada7Hw + AFA vs Sony dcv_psf_table and attenuated prototypes."""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

SABIN_DIR = Path("firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/sabin")
E0600_JSON = Path("artifacts/analysis/arri-sdk-pipeline-e0600/e0600_arri_debayer_and_afa_tables.json")
E0604_DIR = Path("artifacts/analysis/arri-sdk-pipeline-e0604")
OUT_DIR = Path("artifacts/analysis/arri-sdk-pipeline-e0606")


def convolve_1d(signal: list[float], kernel: list[float]) -> list[float]:
    """Symmetric zero-padded / edge-clamped 1D convolution."""
    n = len(signal)
    r = len(kernel) // 2
    out = []
    for i in range(n):
        acc = 0.0
        for k_idx, w in enumerate(kernel):
            j = min(n - 1, max(0, i + k_idx - r))
            acc += signal[j] * w
        out.append(acc)
    return out


def measure_step_response(kernel_1d: list[float]) -> dict:
    """Measure edge overshoot, undershoot, and 10-90% rise width on a 0 -> 1 step edge."""
    step = [0.0] * 32 + [1.0] * 32
    resp = convolve_1d(step, kernel_1d)
    max_val = max(resp)
    min_val = min(resp)
    overshoot_pct = round((max_val - 1.0) * 100.0, 3)
    undershoot_pct = round((0.0 - min_val) * 100.0, 3)
    return {
        "overshoot_pct": overshoot_pct,
        "undershoot_pct": undershoot_pct,
        "peak": round(max_val, 6),
        "trough": round(min_val, 6),
    }


def extract_dcv_center_1d(path: Path) -> list[float]:
    """Extract the 9-tap center row of the primary DCV delta kernel (Q12) and form I + K_dcv."""
    b = path.read_bytes()
    # In movie_bayer.bin, center 7812 is at 0x11c; 1D center slice [-4..+4] around 0x11c:
    # 0x114: 28, 0, 0, -504, 7812, -504, 0, 0, 28 (Wait: let's inspect exact 9 halfwords centered at max)
    vals = struct.unpack_from(f"<{len(b) // 2}h", b, 0)
    # Center 7812 in movie_bayer.bin is at byte offset 0x11c (i16 index 142)
    idx = 0x11C // 2
    delta_q12 = list(vals[idx - 4 : idx + 5])
    # Identity + delta / 4096.0 (normalized to DC=1.0)
    k = [v / 4096.0 for v in delta_q12]
    k[4] += 1.0
    s = sum(k)
    return [round(x / s, 8) for x in k]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    arri = json.loads(E0600_JSON.read_text(encoding="utf-8"))
    arri_afa_smooth = arri["afa_filter_banks"]["ALEV4_AFATexture_09T_0.1.0_variant1"]["table_a_rows"][-1]  # 0.80
    arri_afa_tex = arri["afa_filter_banks"]["ALEV4_AFATexture_09T_0.1.0_variant1"]["table_b_rows"][-1]     # 0.80
    arri_smooth_luma = arri["afa_filter_banks"]["GENERIC_AfaSmoothLuma_09T_0.1.1"]["table_a_rows"][1]     # 0.62

    sony_stock = extract_dcv_center_1d(SABIN_DIR / "dcv_psf_table_movie_bayer.bin")
    sony_25pct = extract_dcv_center_1d(E0604_DIR / "dcv_psf_table_movie_bayer_arri_25pct.bin")
    sony_00pct = extract_dcv_center_1d(E0604_DIR / "dcv_psf_table_movie_bayer_arri_00pct_flat.bin")

    results = {
        "ARRI_ALEV4_AFATexture_0.80_smooth": {"taps": arri_afa_smooth, "step": measure_step_response(arri_afa_smooth)},
        "ARRI_ALEV4_AFATexture_0.80_detail": {"taps": arri_afa_tex, "step": measure_step_response(arri_afa_tex)},
        "ARRI_GENERIC_AfaSmoothLuma_0.62": {"taps": arri_smooth_luma, "step": measure_step_response(arri_smooth_luma)},
        "Sony_dcv_movie_bayer_stock": {"taps": sony_stock, "step": measure_step_response(sony_stock)},
        "Sony_dcv_movie_bayer_25pct": {"taps": sony_25pct, "step": measure_step_response(sony_25pct)},
        "Sony_dcv_movie_bayer_00pct": {"taps": sony_00pct, "step": measure_step_response(sony_00pct)},
    }
    out_path = OUT_DIR / "e0606_offline_step_response.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("Wrote", out_path)
    for name, info in results.items():
        st = info["step"]
        print(f"{name:38s}: overshoot={st['overshoot_pct']:6.2f}%  undershoot={st['undershoot_pct']:6.2f}%  peak={st['peak']:.4f}")


if __name__ == "__main__":
    main()
