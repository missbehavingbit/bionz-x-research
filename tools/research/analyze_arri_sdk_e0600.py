"""Extract and analyze ARRI Image SDK v7.1.1 debayer, AFA texture, scaler, and ANR2 kernels/tables."""

from __future__ import annotations

import json
import math
import re
import struct
from pathlib import Path

import pefile

SDK_DLL = Path("artifacts/ARRI binaries/ArriImageSdk.7.dll")
CPU_DLL = Path("artifacts/ARRI binaries/ArriImageSdkTransformsCpu_module.7.1.1.dll")
PTX_DIR = Path("artifacts/analysis/arri-sdk-pipeline-e0600/ptx")
OUT_DIR = Path("artifacts/analysis/arri-sdk-pipeline-e0600")


def extract_weight_matrices() -> dict:
    """Decode ada5WeightMatrix (int16 Q15) and ada7WeightMatrix (uint16 Q15) from PTX."""
    ptx_path = PTX_DIR / "ada5HwKernel_sm75.ptx"
    txt = ptx_path.read_text(encoding="utf-8", errors="replace")
    result = {}
    for name, fmt in (("ada5WeightMatrix", "<1024h"), ("ada7WeightMatrix", "<1024H")):
        m = re.search(rf"\.const \.align 2 \.b8 {name}\[2048\] = \{{([^}}]+)\}};", txt)
        if not m:
            continue
        raw = bytes(int(x.strip()) for x in m.group(1).split(","))
        mat = struct.unpack(fmt, raw)
        rows = [list(mat[r * 32 : (r + 1) * 32]) for r in range(32)]
        sym_sums = [rows[r][c] + rows[c][r] for r in range(32) for c in range(32) if r != c]
        result[name] = {
            "dtype": "int16" if fmt.endswith("h") else "uint16",
            "scale": 32768,
            "diag_values": sorted(set(rows[i][i] for i in range(32))),
            "off_diag_pair_sum_min": min(sym_sums),
            "off_diag_pair_sum_max": max(sym_sums),
            "sample_row0": rows[0][:8],
            "sample_row1": rows[1][:8],
            "sample_row4": rows[4][:8],
            "rows_32x32": rows,
        }
    return result


def compute_1d_mtf(taps: list[float], n_freqs: int = 33) -> dict:
    """Compute 1D magnitude frequency response |H(f)| for symmetric 9-tap FIR filter."""
    # taps[0..8], center at index 4
    mtf = []
    for k in range(n_freqs):
        f = 0.5 * k / (n_freqs - 1)  # cycles per pixel (0 .. 0.5 Nyquist)
        omega = 2.0 * math.pi * f
        re_sum = sum(taps[i] * math.cos((i - 4) * omega) for i in range(len(taps)))
        im_sum = sum(-taps[i] * math.sin((i - 4) * omega) for i in range(len(taps)))
        mag = math.hypot(re_sum, im_sum)
        mtf.append({"f_cpp": round(f, 4), "mag": round(mag, 6)})
    # Find MTF50 (first freq where mag <= 0.5)
    mtf50 = None
    for idx in range(1, len(mtf)):
        if mtf[idx]["mag"] <= 0.5:
            f0, m0 = mtf[idx - 1]["f_cpp"], mtf[idx - 1]["mag"]
            f1, m1 = mtf[idx]["f_cpp"], mtf[idx]["mag"]
            mtf50 = round(f0 + (0.5 - m0) * (f1 - f0) / (m1 - m0), 4) if m1 != m0 else f1
            break
    return {
        "dc_gain": round(sum(taps), 6),
        "nyquist_gain": mtf[-1]["mag"],
        "peak_gain": round(max(p["mag"] for p in mtf), 6),
        "mtf50_cpp": mtf50,
        "curve": mtf,
    }


def extract_cpu_afa_tables() -> dict:
    """Extract complete 9-tap AFA texture and smooth filter banks from Cpu_module."""
    pe = pefile.PE(str(CPU_DLL), fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    data = CPU_DLL.read_bytes()

    def va2off(va: int) -> int:
        return pe.get_offset_from_rva(va - base)

    specs = [
        ("ALEV4_AFATexture_09T_0.1.0_variant1", 0x1802cfeb0, 0x1802cfeb4, 0x1802cfec0, 0x1802cff30, 0x1802d0300),
        ("ALEV4_AFATexture_09T_0.1.0_variant2", 0x1802cfeb8, 0x1802cfebc, 0x1802d06b0, 0x1802d0720, 0x1802d0ad0),
        ("GENERIC_AfaSmoothLuma_09T_0.1.1", 0x1802cff28, 0x1802cff2c, 0x1802d02d8, 0x1802d0e80, 0x1802d0ff0),
        ("GENERIC_AfaSmoothChroma_09T_0.0.7", 0x1802d06a8, 0x1802d06ac, 0x1802d1158, 0x1802d1170, 0x1802d1200),
    ]
    out = {}
    for name, va_n, va_taps, va_ratios, va_tbl_a, va_tbl_b in specs:
        n_ratios = struct.unpack_from("<I", data, va2off(va_n))[0]
        n_taps = struct.unpack_from("<I", data, va2off(va_taps))[0]
        ratios = [round(x, 6) for x in struct.unpack_from(f"<{n_ratios}d", data, va2off(va_ratios))]
        tbl_a = [
            [round(x, 8) for x in struct.unpack_from(f"<{n_taps}d", data, va2off(va_tbl_a) + i * n_taps * 8)]
            for i in range(n_ratios)
        ]
        tbl_b = [
            [round(x, 8) for x in struct.unpack_from(f"<{n_taps}d", data, va2off(va_tbl_b) + i * n_taps * 8)]
            for i in range(n_ratios)
        ]
        out[name] = {
            "n_ratios": n_ratios,
            "n_taps": n_taps,
            "ratios": ratios,
            "table_a_rows": tbl_a,
            "table_b_rows": tbl_b,
            "table_a_row_sums": [round(sum(r), 6) for r in tbl_a],
            "table_b_row_sums": [round(sum(r), 6) for r in tbl_b],
            "mtf_first_ratio_a": compute_1d_mtf(tbl_a[0]),
            "mtf_first_ratio_b": compute_1d_mtf(tbl_b[0]),
            "mtf_last_ratio_a": compute_1d_mtf(tbl_a[-1]),
            "mtf_last_ratio_b": compute_1d_mtf(tbl_b[-1]),
        }
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wmat = extract_weight_matrices()
    afa = extract_cpu_afa_tables()
    report = {
        "weight_matrices": wmat,
        "afa_filter_banks": afa,
    }
    out_path = OUT_DIR / "e0600_arri_debayer_and_afa_tables.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Wrote", out_path)
    for k, v in afa.items():
        print(f"\n=== {k} (ratios={v['ratios']}, taps={v['n_taps']}) ===")
        print("  table_a sums:", v["table_a_row_sums"])
        print("  table_b sums:", v["table_b_row_sums"])
        print("  ratio[0] A:", v["table_a_rows"][0], "MTF50=", v["mtf_first_ratio_a"]["mtf50_cpp"], "nyq=", v["mtf_first_ratio_a"]["nyquist_gain"])
        print("  ratio[0] B:", v["table_b_rows"][0], "MTF50=", v["mtf_first_ratio_b"]["mtf50_cpp"], "nyq=", v["mtf_first_ratio_b"]["nyquist_gain"])
        print("  ratio[-1] A:", v["table_a_rows"][-1], "MTF50=", v["mtf_last_ratio_a"]["mtf50_cpp"], "nyq=", v["mtf_last_ratio_a"]["nyquist_gain"])
        print("  ratio[-1] B:", v["table_b_rows"][-1], "MTF50=", v["mtf_last_ratio_b"]["mtf50_cpp"], "nyq=", v["mtf_last_ratio_b"]["nyquist_gain"])


if __name__ == "__main__":
    main()
