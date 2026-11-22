"""Core math, presets, image preview simulator, SMOD v2 builder, and USB deployer
for the multi-target BIONZ X Film Sim Studio (ILCE-6100 Ver.1.00 and ILCE-6400 Ver.2.00).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class TargetProfile:
    """Verified av-cam LIRO RAM and libObj target addresses for a camera firmware build."""

    key: str
    label: str
    avcam_default_path: Path
    avcam_sha256: str
    live_json_path: Path
    default_out_root: Path
    sabin_stock_dir: Path
    ec_gamma_pa: int
    fc_gamma_pa: int
    color_a0_pa: int
    color_a1_pa: int
    color_b2_pa: int
    cc_tuning_pa: int
    refadr0_pa: int
    cc_out_pa: int
    event_push_ref_va: int
    event_push_resume_va: int


TARGETS: dict[str, TargetProfile] = {
    "a6100": TargetProfile(
        key="a6100",
        label="ILCE-6100 Ver.1.00",
        avcam_default_path=ROOT / "artifacts/a6100-inputs/av-cam.bin",
        avcam_sha256="58b2118db858f3eb1594ae6cef3f628b7fa3cc6db9173b094565535d48e6ff9e",
        live_json_path=ROOT / "artifacts/a6100-pp/active-tables-live.json",
        default_out_root=ROOT / "artifacts/a6100-film-sims",
        sabin_stock_dir=ROOT / "artifacts/a6100-pp/sabin_stock",
        ec_gamma_pa=0x03E553B0,
        fc_gamma_pa=0x03E8F1D4,
        color_a0_pa=0x0444A254,
        color_a1_pa=0x0444A266,
        color_b2_pa=0x0444B178,
        cc_tuning_pa=0x0444A1AE,
        refadr0_pa=0x044ACD37,
        cc_out_pa=0x043CC38C,
        event_push_ref_va=0x0655A528,
        event_push_resume_va=0x0655A535,
    ),
    "a6400": TargetProfile(
        key="a6400",
        label="ILCE-6400 Ver.2.00",
        avcam_default_path=ROOT / "artifacts/qemu-a6400/inputs-moddev-1/av-cam.bin",
        avcam_sha256="dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209",
        live_json_path=ROOT / "artifacts/analysis/arri-movie-color-2026-11-16/colormode-matrix-table.json",
        default_out_root=ROOT / "artifacts/a6400-film-sims",
        sabin_stock_dir=ROOT / "firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/sabin",
        ec_gamma_pa=0x03E55330,
        fc_gamma_pa=0x03E8F154,
        color_a0_pa=0x0444A244,
        color_a1_pa=0x0444A256,
        color_b2_pa=0x0444B168,
        cc_tuning_pa=0x0444A19E,
        refadr0_pa=0x044ACD27,
        cc_out_pa=0x043CC384,
        event_push_ref_va=0x0655A528,
        event_push_resume_va=0x0655A535,
    ),
}

# Backwards-compatible module constants default to ILCE-6100 Ver.1.00.
_DEFAULT_TARGET = TARGETS["a6100"]
AVCAM_PATH = _DEFAULT_TARGET.avcam_default_path
LIVE_JSON = _DEFAULT_TARGET.live_json_path
AVCAM_SHA256 = _DEFAULT_TARGET.avcam_sha256

EC_REC28_V1_PA = _DEFAULT_TARGET.ec_gamma_pa
FC_TBL0_PA = _DEFAULT_TARGET.fc_gamma_pa
COLOR_A0_PA = _DEFAULT_TARGET.color_a0_pa
COLOR_A1_PA = _DEFAULT_TARGET.color_a1_pa
COLOR_B2_PA = _DEFAULT_TARGET.color_b2_pa
CC_TUNING_PA = _DEFAULT_TARGET.cc_tuning_pa
REFADR0_PA = _DEFAULT_TARGET.refadr0_pa

STOCK_A0 = (1241, -205, -12, 62, 1116, -154, 30, -251, 1245)
STOCK_A1 = (1161, -205, 68, 43, 1134, -153, 16, -236, 1243)
STOCK_B2 = (1024, -88, 88, 51, 1024, -51, 48, -289, 1264)


def get_target(target: str = "a6100") -> TargetProfile:
    """Return the validated TargetProfile for ``a6100`` or ``a6400``."""
    if target not in TARGETS:
        raise ValueError(f"Unsupported camera target {target!r}; expected one of {tuple(TARGETS)}")
    return TARGETS[target]


def detect_available_target(preferred: str = "a6100") -> str:
    """Return ``preferred`` if its av-cam.bin exists locally, else any available target."""
    if get_target(preferred).avcam_default_path.is_file():
        return preferred
    for key, prof in TARGETS.items():
        if prof.avcam_default_path.is_file():
            return key
    return preferred


@dataclass
class FilmSimParams:
    name: str = "Classic Chrome"
    # Tone curve controls (post-gamma 0..1 domain)
    shadow_toe: float = -0.14        # -0.40..+0.40 (negative = deeper shadows)
    mid_contrast: float = 1.16       # 0.60..1.50 (1.0 = stock slope)
    crossover: float = 0.26          # 0.12..0.65 (anchor where shadow meets midtone)
    highlight_rolloff: float = 0.35  # 0.00..1.00 (0 = stock, 1 = heavy shoulder)
    black_fade: int = 0              # 0..40 (10-bit black lift)
    white_ceiling: int = 1018        # 920..1023 (10-bit peak white)
    # Color matrix controls
    saturation: float = 0.84         # 0.00..1.50 (0 = monochrome)
    red_amber: float = 0.12          # -0.25..+0.25 (+ = warmer amber reds)
    green_olive: float = 0.08        # -0.25..+0.25 (+ = muted olive greens)
    blue_cyan: float = 0.12          # -0.25..+0.25 (+ = slate cyan blues)
    warmth: float = 0.02             # -0.15..+0.15 (+ = warm, - = cool)
    # 5x9x9 YCbCr 3D LUT Split-Toning & Hue controls
    shadow_cb: float = 0.0           # -16..+16 (+ = cool blue/teal shadows)
    shadow_cr: float = 0.0           # -16..+16 (- = cyan/green shadows, + = magenta)
    highlight_cb: float = 0.0        # -16..+16 (- = warm golden highlights)
    highlight_cr: float = 0.0        # -16..+16 (+ = warm red/copper highlights)
    hue_shift: float = 0.0           # -20..+20 degrees chroma rotation

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FilmSimParams":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


PRESETS: dict[str, FilmSimParams] = {
    "Classic Chrome": FilmSimParams(
        name="Classic Chrome",
        shadow_toe=-0.14,
        mid_contrast=1.16,
        crossover=0.26,
        highlight_rolloff=0.35,
        black_fade=0,
        white_ceiling=1016,
        saturation=0.84,
        red_amber=0.12,
        green_olive=0.08,
        blue_cyan=0.12,
        warmth=0.02,
    ),
    "Classic Neg": FilmSimParams(
        name="Classic Neg",
        shadow_toe=-0.22,
        mid_contrast=1.28,
        crossover=0.28,
        highlight_rolloff=0.45,
        black_fade=4,
        white_ceiling=1012,
        saturation=0.92,
        red_amber=0.16,
        green_olive=0.14,
        blue_cyan=0.16,
        warmth=-0.03,
        shadow_cb=4.0,
        shadow_cr=-5.0,
        highlight_cb=-4.0,
        highlight_cr=5.0,
    ),
    "Velvia": FilmSimParams(
        name="Velvia",
        shadow_toe=-0.18,
        mid_contrast=1.24,
        crossover=0.24,
        highlight_rolloff=0.20,
        black_fade=0,
        white_ceiling=1023,
        saturation=1.28,
        red_amber=-0.04,
        green_olive=-0.06,
        blue_cyan=-0.04,
        warmth=0.02,
    ),
    "Nostalgic Neg": FilmSimParams(
        name="Nostalgic Neg",
        shadow_toe=-0.05,
        mid_contrast=1.08,
        crossover=0.24,
        highlight_rolloff=0.42,
        black_fade=10,
        white_ceiling=1008,
        saturation=0.94,
        red_amber=0.18,
        green_olive=0.10,
        blue_cyan=0.15,
        warmth=0.08,
        highlight_cb=-6.0,
        highlight_cr=4.0,
    ),
    "ETERNA": FilmSimParams(
        name="ETERNA",
        shadow_toe=0.12,
        mid_contrast=0.82,
        crossover=0.28,
        highlight_rolloff=0.65,
        black_fade=14,
        white_ceiling=995,
        saturation=0.72,
        red_amber=0.06,
        green_olive=0.08,
        blue_cyan=0.10,
        warmth=-0.02,
    ),
    "ACROS B&W": FilmSimParams(
        name="ACROS B&W",
        shadow_toe=-0.20,
        mid_contrast=1.26,
        crossover=0.26,
        highlight_rolloff=0.35,
        black_fade=0,
        white_ceiling=1020,
        saturation=0.00,
        red_amber=0.00,
        green_olive=0.00,
        blue_cyan=0.00,
        warmth=0.00,
    ),
    "Portra 400": FilmSimParams(
        name="Portra 400",
        shadow_toe=-0.04,
        mid_contrast=1.06,
        crossover=0.25,
        highlight_rolloff=0.52,
        black_fade=6,
        white_ceiling=1016,
        saturation=0.94,
        red_amber=0.06,
        green_olive=0.12,
        blue_cyan=0.08,
        warmth=0.03,
        highlight_cb=-3.0,
        highlight_cr=2.0,
    ),
    "Gold 200": FilmSimParams(
        name="Gold 200",
        shadow_toe=-0.08,
        mid_contrast=1.12,
        crossover=0.26,
        highlight_rolloff=0.38,
        black_fade=10,
        white_ceiling=1018,
        saturation=1.06,
        red_amber=0.14,
        green_olive=0.08,
        blue_cyan=0.06,
        warmth=0.055,
        highlight_cb=-5.0,
        highlight_cr=3.0,
    ),
    "Vision3 800T": FilmSimParams(
        name="Vision3 800T",
        shadow_toe=-0.16,
        mid_contrast=1.18,
        crossover=0.27,
        highlight_rolloff=0.50,
        black_fade=12,
        white_ceiling=1008,
        saturation=0.95,
        red_amber=0.15,
        green_olive=0.06,
        blue_cyan=0.16,
        warmth=-0.035,
        shadow_cb=5.0,
        shadow_cr=-3.0,
        highlight_cb=-5.0,
        highlight_cr=4.0,
    ),
    "Stock (Reset)": FilmSimParams(
        name="Stock Standard",
        shadow_toe=0.0,
        mid_contrast=1.0,
        crossover=0.26,
        highlight_rolloff=0.0,
        black_fade=0,
        white_ceiling=1023,
        saturation=1.0,
        red_amber=0.0,
        green_olive=0.0,
        blue_cyan=0.0,
        warmth=0.0,
    ),
}

LIVE_PICKER_ORDER: tuple[str, ...] = (
    "Classic Chrome",
    "Classic Neg",
    "Portra 400",
    "Gold 200",
    "Vision3 800T",
    "ETERNA",
    "ACROS B&W",
)


def has_3dlut_params(p: FilmSimParams) -> bool:
    return (
        abs(p.shadow_cb) > 1e-6
        or abs(p.shadow_cr) > 1e-6
        or abs(p.highlight_cb) > 1e-6
        or abs(p.highlight_cr) > 1e-6
        or abs(p.hue_shift) > 1e-6
    )


def is_stock_params(p: FilmSimParams) -> bool:
    return (
        abs(p.shadow_toe) < 1e-6
        and abs(p.mid_contrast - 1.0) < 1e-6
        and abs(p.highlight_rolloff) < 1e-6
        and p.black_fade == 0
        and p.white_ceiling == 1023
        and abs(p.saturation - 1.0) < 1e-6
        and abs(p.red_amber) < 1e-6
        and abs(p.green_olive) < 1e-6
        and abs(p.blue_cyan) < 1e-6
        and abs(p.warmth) < 1e-6
        and not has_3dlut_params(p)
    )


def _mode0_10bit(v: int) -> int:
    s = v >> 6
    if s <= 1022 and (v & 0x20):
        s += 1
    return s


def _load_matrix_row(data: dict[str, Any], key: str, default: tuple[int, ...]) -> list[int]:
    if "matrix_table" in data:
        mt = data["matrix_table"]
        for k in (f"{key}_idx0", f"{key}_idx1", f"{key}_idx17"):
            if k in mt and "coeffs" in mt[k]:
                return list(mt[k]["coeffs"])
    if "live" in data and key in data["live"]:
        return list(data["live"][key])
    return list(default)


def load_stock_tables(target: str = "a6100", avcam_path: Path | None = None) -> dict[str, list[int]]:
    prof = get_target(target)
    path = avcam_path or prof.avcam_default_path
    av = path.read_bytes()
    if avcam_path is None:
        actual_sha = hashlib.sha256(av).hexdigest()
        assert actual_sha == prof.avcam_sha256, f"{prof.key} av-cam.bin hash mismatch: {actual_sha}"
    ec_off = prof.ec_gamma_pa - 0x035C6000
    fc_off = prof.fc_gamma_pa - 0x035C6000
    ec_stock = list(struct.unpack_from("<96I", av, ec_off))
    fc_stock = list(struct.unpack_from("<96H", av, fc_off))

    a0_stock = list(STOCK_A0)
    a1_stock = list(STOCK_A1)
    b2_stock = list(STOCK_B2)
    if prof.live_json_path.is_file():
        live = json.loads(prof.live_json_path.read_text(encoding="utf-8"))
        a0_stock = _load_matrix_row(live, "A0", STOCK_A0)
        a1_stock = _load_matrix_row(live, "A1", STOCK_A1)
        b2_stock = _load_matrix_row(live, "B2", STOCK_B2)
    return {
        "ec_stock": ec_stock,
        "fc_stock": fc_stock,
        "a0_stock": a0_stock,
        "a1_stock": a1_stock,
        "b2_stock": b2_stock,
    }


def eval_tone_transfer(x_norm: float, p: FilmSimParams) -> float:
    """Evaluate parametric filmic transfer on normalized [0, 1] post-gamma input."""
    if is_stock_params(p):
        return max(0.0, min(1.0, x_norm))
    x = max(0.0, min(1.0, x_norm))
    xc = max(0.10, min(0.75, p.crossover))

    # Piece-wise smooth S-curve anchored at xc:
    # 1. Below xc: shadow toe bend
    # 2. Above xc: midtone contrast slope + soft highlight shoulder above 0.72
    if x <= xc:
        u = x / xc
        # Smooth shadow power/polynomial controlled by shadow_toe and mid_contrast
        gamma_toe = max(0.45, 1.0 - p.shadow_toe * 1.45)
        y = xc * math.pow(u, gamma_toe)
        # Blend slope near xc to match mid_contrast smoothly
        slope_blend = (p.mid_contrast - 1.0) * 0.28 * xc * (u * u * (1.0 - u))
        y = y - slope_blend
    else:
        u = (x - xc) / (1.0 - xc)
        # Midtone contrast expansion using smooth tanh-like/sine blend
        gain = p.mid_contrast
        y_lin = xc + (x - xc) * gain
        # Smooth highlight rolloff compressing values approaching 1.0
        if p.highlight_rolloff > 0.0:
            k = 1.0 + 3.8 * p.highlight_rolloff
            shoulder = (1.0 - math.exp(-k * u)) / (1.0 - math.exp(-k))
            y_sh = xc + (1.0 - xc) * shoulder
            # Weight highlight shoulder smoothly toward upper end
            w = math.pow(u, 1.6)
            y = (1.0 - w) * y_lin + w * y_sh
        else:
            y = y_lin
        # Gently blend back into 1.0 at top
        if y > 1.0:
            y = 1.0 - 0.15 * math.exp(-(y - 1.0) * 4.0) + 0.15
            y = min(1.0, y)
    return max(0.0, min(1.0, y))


def compute_curves(stock: dict[str, list[int]], p: FilmSimParams) -> tuple[list[int], list[int]]:
    """Compute monotone 96-knot EC (uint32 << 6) and FC (uint16 0..1023) curves."""
    ec_stock = stock["ec_stock"]
    fc_stock = stock["fc_stock"]
    if is_stock_params(p):
        return list(ec_stock), list(fc_stock)

    # FC curve (96 x uint16)
    fc_lo, fc_hi = fc_stock[0], fc_stock[-1]
    fc_span = float(max(1, fc_hi - fc_lo))
    out_lo = min(fc_hi - 50, fc_lo + p.black_fade)
    out_hi = max(out_lo + 50, min(1023, p.white_ceiling))
    fc_out: list[int] = []
    prev = 0
    for h in fc_stock:
        t = (h - fc_lo) / fc_span
        y_t = eval_tone_transfer(t, p)
        val = int(round(out_lo + y_t * (out_hi - out_lo)))
        val = max(prev, min(1023, max(0, val)))
        prev = val
        fc_out.append(val)

    # EC curve (96 x uint32 << 6)
    ec_10 = [_mode0_10bit(w & 0xFFFF) for w in ec_stock]
    ec_lo, ec_hi = ec_10[0], ec_10[-1]
    ec_span = float(max(1, ec_hi - ec_lo))
    ec_out_lo = min(ec_hi - 50, ec_lo + p.black_fade)
    ec_out_hi = max(ec_out_lo + 50, min(1023, int(round(ec_hi * (out_hi / 1023.0)))))
    ec_out: list[int] = []
    prev_ec = 0
    for c10 in ec_10:
        t = (c10 - ec_lo) / ec_span
        y_t = eval_tone_transfer(t, p)
        val = int(round(ec_out_lo + y_t * (ec_out_hi - ec_out_lo)))
        val = max(prev_ec, min(1023, max(0, val)))
        prev_ec = val
        ec_out.append(val << 6)

    return ec_out, fc_out


def _matmul3x3(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [
        [sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)]
        for r in range(3)
    ]


def build_look_matrix_3x3(p: FilmSimParams) -> list[list[float]]:
    """Build 3x3 look transform in post-gamma RGB space with exact row sums = 1.0."""
    if is_stock_params(p):
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

    # 1. Saturation matrix (BT.709 luma weights)
    s = p.saturation
    wr, wg, wb = 0.2126, 0.7152, 0.0722
    m_sat = [
        [wr + (1.0 - wr) * s, wg * (1.0 - s), wb * (1.0 - s)],
        [wr * (1.0 - s), wg + (1.0 - wg) * s, wb * (1.0 - s)],
        [wr * (1.0 - s), wg * (1.0 - s), wb + (1.0 - wb) * s],
    ]

    # 2. Hue / primary character matrix (each row sums to 1.0)
    ra = p.red_amber
    go = p.green_olive
    bc = p.blue_cyan
    w = p.warmth
    raw_hue = [
        [1.0 - ra + w * 0.5, ra, -w * 0.5],
        [go * 0.65, 1.0 - go, go * 0.35],
        [-bc * 0.15 - w * 0.5, bc * 1.15, 1.0 - bc + w * 0.5],
    ]
    m_hue = []
    for row in raw_hue:
        row_sum = sum(row)
        m_hue.append([v / row_sum for v in row])

    return _matmul3x3(m_hue, m_sat)


def compute_q10_matrix(stock9: list[int], p: FilmSimParams) -> list[int]:
    """Compute row-sum-invariant Q10 3x3 int16 matrix for A0 or A1."""
    if is_stock_params(p):
        return list(stock9)
    m_look = build_look_matrix_3x3(p)
    s = [[float(stock9[r * 3 + c]) for c in range(3)] for r in range(3)]
    m_new = _matmul3x3(m_look, s)
    out9: list[int] = []
    for r in range(3):
        target_sum = int(sum(stock9[r * 3 : r * 3 + 3]))
        row = [int(round(v)) for v in m_new[r]]
        row[r] -= sum(row) - target_sum
        out9.extend(row)
    return out9


def build_3dlut_1215(p: FilmSimParams) -> bytes:
    """Build 5x9x9 YCbCr 3D LUT (1215 bytes: 405 dY + 405 dCb + 405 dCr, int8 clamped [-64, 63])."""
    y_axis = (0, 64, 128, 192, 256)
    c_axis = (-128, -96, -64, -32, 0, 32, 64, 96, 128)
    theta = math.radians(p.hue_shift)
    ct, st = math.cos(theta), math.sin(theta)
    dy_plane, dcb_plane, dcr_plane = [], [], []
    for y in y_axis:
        w_shad = max(0.0, 1.0 - y / 128.0)
        w_high = max(0.0, (y - 128.0) / 128.0)
        for cb in c_axis:
            for cr in c_axis:
                dcb = p.shadow_cb * w_shad + p.highlight_cb * w_high
                dcr = p.shadow_cr * w_shad + p.highlight_cr * w_high
                if abs(theta) > 1e-6:
                    dcb += (cb * ct - cr * st) - cb
                    dcr += (cb * st + cr * ct) - cr
                dy_plane.append(0)
                dcb_plane.append(max(-64, min(63, int(round(dcb)))) & 0xFF)
                dcr_plane.append(max(-64, min(63, int(round(dcr)))) & 0xFF)
    return bytes(dy_plane + dcb_plane + dcr_plane)


def simulate_image(rgb_u8, stock: dict[str, list[int]], p: FilmSimParams):
    """Apply the exact relative ISP delta (matrix + FC curve + optional 5x9x9 YCbCr 3D LUT)."""
    if is_stock_params(p):
        return rgb_u8
    import numpy as np

    a1_stock = np.array(stock["a1_stock"], dtype=np.float64).reshape(3, 3)
    a1_sim = np.array(compute_q10_matrix(stock["a1_stock"], p), dtype=np.float64).reshape(3, 3)
    # Relative matrix delta M_sim * M_stock^-1
    delta_m = a1_sim @ np.linalg.inv(a1_stock)

    # Apply matrix in float32
    flat = rgb_u8.reshape(-1, 3).astype(np.float32)
    mapped = np.clip(flat @ delta_m.T.astype(np.float32), 0.0, 255.0)

    # Apply exact 1D FC curve delta via 256-entry LUT
    fc_stock = np.array(stock["fc_stock"], dtype=np.float64)
    _, fc_sim = compute_curves(stock, p)
    fc_sim_arr = np.array(fc_sim, dtype=np.float64)
    knots = np.arange(96, dtype=np.float64)

    x_10 = np.arange(256, dtype=np.float64) * (1023.0 / 255.0)
    k_pos = np.interp(x_10, fc_stock, knots)
    y_10 = np.interp(k_pos, knots, fc_sim_arr)
    lut_u8 = np.clip(np.rint(y_10 * (255.0 / 1023.0)), 0, 255).astype(np.uint8)

    idx = np.clip(np.rint(mapped), 0, 255).astype(np.uint8)
    out = lut_u8[idx].reshape(rgb_u8.shape)

    # Apply 5x9x9 YCbCr 3D LUT split-toning & hue twist if enabled
    if has_3dlut_params(p):
        f = out.astype(np.float32)
        r, g, b = f[:, :, 0], f[:, :, 1], f[:, :, 2]
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        cb = -0.11457 * r - 0.38543 * g + 0.5 * b
        cr = 0.5 * r - 0.45415 * g - 0.04585 * b
        w_shad = np.clip(1.0 - y / 128.0, 0.0, 1.0)
        w_high = np.clip((y - 128.0) / 128.0, 0.0, 1.0)
        dcb = p.shadow_cb * w_shad + p.highlight_cb * w_high
        dcr = p.shadow_cr * w_shad + p.highlight_cr * w_high
        if abs(p.hue_shift) > 1e-6:
            th = math.radians(p.hue_shift)
            ct, st = math.cos(th), math.sin(th)
            cb_new = cb * ct - cr * st + dcb
            cr_new = cb * st + cr * ct + dcr
        else:
            cb_new = cb + dcb
            cr_new = cr + dcr
        cb_new = np.clip(cb_new, -128.0, 128.0)
        cr_new = np.clip(cr_new, -128.0, 128.0)
        r_o = y + 1.5748 * cr_new
        g_o = y - 0.18732 * cb_new - 0.46812 * cr_new
        b_o = y + 1.8556 * cb_new
        out = np.clip(np.rint(np.stack((r_o, g_o, b_o), axis=2)), 0, 255).astype(np.uint8)
    return out


def image_metrics(rgb_u8) -> dict[str, float]:
    """Compute luma, relative chroma, and local detail gradient on a strided sample."""
    import numpy as np
    s = rgb_u8[::2, ::2].astype(np.float32)
    r, g, b = s[:, :, 0], s[:, :, 1], s[:, :, 2]
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    chroma = np.max(s, axis=2) - np.min(s, axis=2)
    dx = np.abs(luma[:, 1:] - luma[:, :-1]).mean()
    dy = np.abs(luma[1:, :] - luma[:-1, :]).mean()
    mean_l = float(luma.mean())
    mean_c = float(chroma.mean())
    return {
        "luma": mean_l,
        "chroma": mean_c,
        "sat_pct": (mean_c / max(1e-3, mean_l)) * 100.0,
        "detail_grad": float(dx + dy),
    }


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", name.strip()).strip("_").lower()
    return s or "custom_sim"


def _camel_filename(name: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", name)
    return ("".join(p[:1].upper() + p[1:] for p in parts) or "CustomSim") + ".mod"


def _fmt(vals: list[int], as_hex: bool = False) -> str:
    return ",".join(hex(v) if as_hex else str(v) for v in vals)


def resolve_isp_tables(
    p: FilmSimParams,
    stock: dict[str, list[int]],
    pipeline: str = "full",
    cube: Any = None,
) -> tuple[list[int], list[int], list[int], list[int], list[int], bytes | None, dict[str, Any] | None]:
    """Resolve EC/FC gamma, A0/A1/B2 Q10 matrices, and optional 1215-byte RefAdr[0]."""
    if cube is None and is_stock_params(p):
        return (
            list(stock["ec_stock"]),
            list(stock["fc_stock"]),
            list(stock["a0_stock"]),
            list(stock["a1_stock"]),
            list(stock["b2_stock"]),
            None,
            None,
        )
    if cube is not None or pipeline == "full":
        try:
            from mods.film_sims.film_recipes_3d import RECIPES_3D, build_recipe_cube
            from mods.film_sims.lut_bridge import fit_cube_to_isp
        except ModuleNotFoundError as exc:
            if cube is not None or exc.name != "numpy":
                raise
        else:
            target_cube = cube if cube is not None else (
                build_recipe_cube(p.name) if p.name in RECIPES_3D else None
            )
            if target_cube is not None:
                fitted = fit_cube_to_isp(target_cube, stock, name=p.name)
                return (
                    fitted.ec_sim,
                    fitted.fc_sim,
                    fitted.a0_sim,
                    fitted.a1_sim,
                    fitted.b2_sim,
                    fitted.refadr_bytes,
                    fitted.report,
                )

    ec_sim, fc_sim = compute_curves(stock, p)
    a0_sim = compute_q10_matrix(stock["a0_stock"], p)
    a1_sim = compute_q10_matrix(stock["a1_stock"], p)
    b2_sim = compute_q10_matrix(stock["b2_stock"], p)
    lut_bytes = build_3dlut_1215(p) if has_3dlut_params(p) else None
    return ec_sim, fc_sim, a0_sim, a1_sim, b2_sim, lut_bytes, None


def emit_header(out_header: Path, stock: dict[str, list[int]], p: FilmSimParams,
                target: str = "a6100", pipeline: str = "full", cube: Any = None) -> dict[str, Any] | None:
    prof = get_target(target)
    ec_sim, fc_sim, a0_sim, a1_sim, b2_sim, lut_bytes, fit_report = resolve_isp_tables(
        p, stock, pipeline=pipeline, cube=cube
    )
    slug = _slug(p.name)
    lut_block = ""
    if lut_bytes is not None:
        hex_b = ",".join(f"0x{b:02x}" for b in lut_bytes)
        lut_block = (
            "#define REFADR_ENABLED 1\n"
            f"#define CC_TUNING_PA 0x{prof.cc_tuning_pa:08x}u\n"
            f"#define REFADR0_PA 0x{prof.refadr0_pa:08x}u\n"
            f"static const uint8_t REFADR_SIM[1215] = {{{hex_b}}};\n"
        )
    hdr = (
        f"/* Auto-generated by mods/film_sims/studio_core.py for {p.name} ({prof.label}) */\n"
        "#include <stdint.h>\n\n"
        f"#define EC_GAMMA_PA 0x{prof.ec_gamma_pa:08x}u\n"
        f"#define FC_GAMMA_PA 0x{prof.fc_gamma_pa:08x}u\n"
        f"#define COLOR_A0_PA 0x{prof.color_a0_pa:08x}u\n"
        f"#define COLOR_A1_PA 0x{prof.color_a1_pa:08x}u\n"
        f"#define COLOR_B2_PA 0x{prof.color_b2_pa:08x}u\n"
        f'#define MOD_TAG "{slug}"\n'
        f'#define MOD_LOG_PATH "/tmp_bt/mods/{slug}.log"\n\n'
        f"static const uint32_t EC_GAMMA_STOCK[96] = {{{_fmt(stock['ec_stock'], True)}}};\n"
        f"static const uint32_t EC_GAMMA_SIM[96] = {{{_fmt(ec_sim, True)}}};\n"
        f"static const uint16_t FC_GAMMA_STOCK[96] = {{{_fmt(stock['fc_stock'], False)}}};\n"
        f"static const uint16_t FC_GAMMA_SIM[96] = {{{_fmt(fc_sim, False)}}};\n"
        f"static const int16_t COLOR_A0_STOCK[9] = {{{_fmt(stock['a0_stock'], False)}}};\n"
        f"static const int16_t COLOR_A0_SIM[9] = {{{_fmt(a0_sim, False)}}};\n"
        f"static const int16_t COLOR_A1_STOCK[9] = {{{_fmt(stock['a1_stock'], False)}}};\n"
        f"static const int16_t COLOR_A1_SIM[9] = {{{_fmt(a1_sim, False)}}};\n"
        f"static const int16_t COLOR_B2_STOCK[9] = {{{_fmt(stock['b2_stock'], False)}}};\n"
        f"static const int16_t COLOR_B2_SIM[9] = {{{_fmt(b2_sim, False)}}};\n"
        + lut_block
    )
    out_header.write_text(hdr, encoding="utf-8")
    return fit_report


def _to_wsl_path(win_or_posix: Path) -> str:
    s = str(win_or_posix.resolve())
    if s.startswith("\\\\wsl.localhost\\Ubuntu"):
        return s.replace("\\\\wsl.localhost\\Ubuntu", "").replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        rest = s[2:].replace("\\", "/").lstrip("/")
        return f"/mnt/{s[0].lower()}/{rest}"
    return s.replace("\\", "/")


def build_mod(p: FilmSimParams, out_dir: Path | None = None,
              target: str = "a6100", stock: dict[str, list[int]] | None = None,
              pipeline: str = "full", cube: Any = None) -> dict[str, Any]:
    prof = get_target(target)
    if stock is None:
        stock = load_stock_tables(target=target)
    slug = _slug(p.name)
    mod_filename = _camel_filename(p.name)
    if out_dir is None:
        out_dir = prof.default_out_root / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    fit_report = emit_header(out_dir / "film_sim_data.h", stock, p, target=target, pipeline=pipeline, cube=cube)
    recipe = {**p.to_dict(), "target": prof.key, "pipeline": "cube" if cube is not None else pipeline}
    if fit_report is not None:
        recipe["fit_report"] = fit_report
    (out_dir / "recipe.json").write_text(json.dumps(recipe, indent=2), encoding="utf-8")

    import shlex
    if os.name == "nt":
        run_root = _to_wsl_path(ROOT)
        run_out = _to_wsl_path(out_dir)
        py_bin = "python3"
    else:
        run_root = str(ROOT.resolve())
        run_out = str(out_dir.resolve())
        py_bin = shlex.quote(sys.executable or shutil.which("python3") or "python3")

    cmd_str = (
        f"set -euo pipefail; cd {shlex.quote(run_root)}; "
        f"export PATH={shlex.quote(run_root + '/artifacts/toolchain/arm-linux-gnueabi/bin')}:\"$PATH\"; "
        f"arm-linux-gnueabi-gcc -mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin "
        f"-nostdlib -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror "
        f"-fpie -fvisibility=hidden -I{shlex.quote(run_out)} -c mods/film_sims/template/film_sim.c -o {shlex.quote(run_out + '/film_sim.o')}; "
        f"if arm-linux-gnueabi-readelf -rW {shlex.quote(run_out + '/film_sim.o')} | grep -E 'R_ARM_(ABS32|GOT|TARGET1|PLT32)'; then "
        "echo 'absolute relocations' >&2; exit 1; fi; "
        f"arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld {shlex.quote(run_out + '/film_sim.o')} -o {shlex.quote(run_out + '/film_sim.elf')}; "
        f"{py_bin} -m tools.research.pack_mod_v2 --elf {shlex.quote(run_out + '/film_sim.elf')} --name {shlex.quote(p.name[:31])} "
        f"--on sim_life --off sim_life --action --out {shlex.quote(run_out + '/' + mod_filename)}"
    )
    try:
        if os.name == "nt":
            proc = subprocess.run(
                [r"C:\Program Files\WSL\wsl.exe", "-d", "Ubuntu", "--", "bash", "-c", cmd_str],
                capture_output=True, text=True, check=True,
            )
        else:
            proc = subprocess.run(["bash", "-c", cmd_str], capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError((exc.stderr or exc.stdout or str(exc)).strip()) from exc
    info = json.loads(proc.stdout)
    info["local_path"] = str(out_dir / mod_filename)
    info["mod_filename"] = mod_filename
    info["slug"] = slug
    info["target"] = prof.key
    return info


VENV_PYTHON = ROOT / "venv/Scripts/python.exe"


def _has_win32file() -> bool:
    try:
        import win32file  # noqa: F401
        return True
    except ImportError:
        return False


def connect_camera(target: str = "a6100"):
    prof = get_target(target)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if prof.key == "a6100" and (ROOT / "artifacts/a6100-pp/a6100_usb_dbg.py").is_file():
        dbg_dir = str(ROOT / "artifacts/a6100-pp")
        if dbg_dir not in sys.path:
            sys.path.insert(0, dbg_dir)
        from a6100_usb_dbg import DirectScsiExchange
        from tools.camera_debugger import Debugger, OP_INFO

        for drive in (r"\\.\F:", r"\\.\E:", r"\\.\G:", r"\\.\PHYSICALDRIVE3", r"\\.\PHYSICALDRIVE4"):
            try:
                dbg = Debugger(DirectScsiExchange(drive, timeout=3.0))
                dbg.request(OP_INFO)
                return dbg, drive
            except Exception:
                continue
    raise RuntimeError(
        f"{prof.label} not found via DirectScsiExchange (use open_camera_debugger for tools.camera_debugger)"
    )


@contextlib.contextmanager
def open_camera_debugger(target: str = "a6100"):
    """Yield (Debugger, transport_label) for ILCE-6100 or ILCE-6400."""
    prof = get_target(target)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if prof.key == "a6100" and (ROOT / "artifacts/a6100-pp/a6100_usb_dbg.py").is_file():
        try:
            dbg, drive = connect_camera(target=target)
            yield dbg, drive
            return
        except RuntimeError:
            pass
    from tools.camera_debugger import OP_INFO, connect

    try:
        with connect() as dbg:
            dbg.request(OP_INFO)
            yield dbg, f"{prof.key} (camera_debugger)"
    except Exception as exc:
        raise RuntimeError(
            f"{prof.label} not found over USB Debugger (ensure camera is connected and USB Debugger is active)"
        ) from exc


def push_mod_to_camera(mod_path: Path, mod_filename: str | None = None,
                       target: str = "a6100") -> dict[str, Any]:
    prof = get_target(target)
    fname = mod_filename or mod_path.name
    if prof.key == "a6100" and not _has_win32file() and VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
        code = (
            "import json, sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(ROOT)!r}); "
            "from mods.film_sims.studio_core import push_mod_to_camera; "
            f"print(json.dumps(push_mod_to_camera(Path({str(mod_path)!r}), {fname!r}, {prof.key!r})))"
        )
        try:
            proc = subprocess.run([str(VENV_PYTHON), "-c", code], capture_output=True, text=True, check=True)
            return json.loads(proc.stdout)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError((exc.stderr or exc.stdout or str(exc)).strip().splitlines()[-1]) from exc

    from tools.camera_debugger import OP_EXEC
    with open_camera_debugger(target=prof.key) as (dbg, drive):
        remote = f"/tmp_bt/mods/{fname}"
        data = mod_path.read_bytes()
        res = dbg.push(remote, data)
        # Request live reload in case the same mod name is already active
        dbg.request(OP_EXEC, f"echo {fname} > /tmp_bt/mlv2/reload".encode())
        ls_out = dbg.request(OP_EXEC, b"ls -la /tmp_bt/mods")["payload"].decode(errors="replace")
        return {"drive": drive, "remote": remote, "target": prof.key, **res, "ls": ls_out.strip()}


def read_camera_logs(slug: str, target: str = "a6100") -> str:
    prof = get_target(target)
    if prof.key == "a6100" and not _has_win32file() and VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
        code = (
            "import sys; "
            f"sys.path.insert(0, {str(ROOT)!r}); "
            "from mods.film_sims.studio_core import read_camera_logs; "
            f"print(read_camera_logs({slug!r}, {prof.key!r}))"
        )
        try:
            proc = subprocess.run([str(VENV_PYTHON), "-c", code], capture_output=True, text=True, check=True)
            return proc.stdout
        except subprocess.CalledProcessError as exc:
            raise RuntimeError((exc.stderr or exc.stdout or str(exc)).strip().splitlines()[-1]) from exc

    from tools.camera_debugger import OP_EXEC
    with open_camera_debugger(target=prof.key) as (dbg, drive):
        cmd = f"echo '=== {drive} /tmp_bt/mods/{slug}.log ==='; cat /tmp_bt/mods/{slug}.log 2>/dev/null || echo '(empty)'; echo '=== mod_loader.log ==='; tail -n 10 /tmp_bt/mods/mod_loader.log 2>/dev/null || true"
        return dbg.request(OP_EXEC, cmd.encode())["payload"].decode(errors="replace")


def _pa_block_for_header(target: str, multi_target: bool) -> str:
    if not multi_target:
        prof = get_target(target)
        return (
            f"#define EC_GAMMA_PA 0x{prof.ec_gamma_pa:08x}u\n"
            f"#define FC_GAMMA_PA 0x{prof.fc_gamma_pa:08x}u\n"
            f"#define COLOR_A0_PA 0x{prof.color_a0_pa:08x}u\n"
            f"#define COLOR_A1_PA 0x{prof.color_a1_pa:08x}u\n"
            f"#define COLOR_B2_PA 0x{prof.color_b2_pa:08x}u\n"
            f"#define CC_TUNING_PA 0x{prof.cc_tuning_pa:08x}u\n"
            f"#define REFADR0_PA 0x{prof.refadr0_pa:08x}u\n"
        )
    a64 = get_target("a6400")
    a61 = get_target("a6100")
    return (
        "#if defined(TARGET_A6400)\n"
        f"#define EC_GAMMA_PA 0x{a64.ec_gamma_pa:08x}u\n"
        f"#define FC_GAMMA_PA 0x{a64.fc_gamma_pa:08x}u\n"
        f"#define COLOR_A0_PA 0x{a64.color_a0_pa:08x}u\n"
        f"#define COLOR_A1_PA 0x{a64.color_a1_pa:08x}u\n"
        f"#define COLOR_B2_PA 0x{a64.color_b2_pa:08x}u\n"
        f"#define CC_TUNING_PA 0x{a64.cc_tuning_pa:08x}u\n"
        f"#define REFADR0_PA 0x{a64.refadr0_pa:08x}u\n"
        "#elif defined(TARGET_A6400_V1)\n"
        "#define EC_GAMMA_PA 0x03e3e6ccu\n"
        "#define FC_GAMMA_PA 0x03e784f0u\n"
        "#define COLOR_A0_PA 0x0442d968u\n"
        "#define COLOR_A1_PA 0x0442d97au\n"
        "#define COLOR_B2_PA 0x0442e88cu\n"
        "#define CC_TUNING_PA 0x0442d8c2u\n"
        "#define REFADR0_PA 0x04490025u\n"
        "#else\n"
        f"#define EC_GAMMA_PA 0x{a61.ec_gamma_pa:08x}u\n"
        f"#define FC_GAMMA_PA 0x{a61.fc_gamma_pa:08x}u\n"
        f"#define COLOR_A0_PA 0x{a61.color_a0_pa:08x}u\n"
        f"#define COLOR_A1_PA 0x{a61.color_a1_pa:08x}u\n"
        f"#define COLOR_B2_PA 0x{a61.color_b2_pa:08x}u\n"
        f"#define CC_TUNING_PA 0x{a61.cc_tuning_pa:08x}u\n"
        f"#define REFADR0_PA 0x{a61.refadr0_pa:08x}u\n"
        "#endif\n"
    )


def emit_live_picker_header(out_header: Path, stock: dict[str, list[int]] | None = None,
                            presets: list[FilmSimParams] | None = None,
                            target: str = "a6100",
                            pipeline: str = "full",
                            multi_target: bool = False) -> Path:
    """Emit film_sim_live_data.h bundling Stock (0) + presets for FilmSimLive.mod."""
    prof = get_target(target)
    if stock is None:
        stock = load_stock_tables(target=target)
    if presets is None:
        presets = [PRESETS[n] for n in LIVE_PICKER_ORDER]

    names = ["Stock (Off)"] + [p.name[:15] for p in presets]
    ecs, fcs, a0s, a1s, b2s, luts, lut_idxs = [], [], [], [], [], [], [-1]
    for p in presets:
        ec_sim, fc_sim, a0_sim, a1_sim, b2_sim, lut_bytes, _ = resolve_isp_tables(
            p, stock, pipeline=pipeline
        )
        ecs.append("{" + _fmt(ec_sim, True) + "}")
        fcs.append("{" + _fmt(fc_sim, False) + "}")
        a0s.append("{" + _fmt(a0_sim, False) + "}")
        a1s.append("{" + _fmt(a1_sim, False) + "}")
        b2s.append("{" + _fmt(b2_sim, False) + "}")
        if lut_bytes is not None:
            lut_idxs.append(len(luts))
            luts.append("{" + ",".join(f"0x{b:02x}" for b in lut_bytes) + "}")
        else:
            lut_idxs.append(-1)

    name_rows = ",".join(f'"{n}"' for n in names)
    lut_block = (
        f"const uint8_t LIVE_REFADR[{max(1, len(luts))}][1215] = {{"
        + (",".join(luts) if luts else "{0}")
        + "};\n"
    )
    pa_block = _pa_block_for_header(target, multi_target)
    hdr = (
        f"/* Auto-generated by mods/film_sims/studio_core.py for FilmSimLive.mod ({prof.label}) */\n"
        "#include <stdint.h>\n\n"
        + pa_block
        + f"#define LIVE_N_PRESETS {len(names)}u\n\n"
        f"const char LIVE_NAMES[{len(names)}][16] = {{{name_rows}}};\n"
        f"const int8_t LIVE_LUT_IDX[{len(names)}] = {{{_fmt(lut_idxs, False)}}};\n\n"
        f"const uint32_t EC_GAMMA_STOCK[96] = {{{_fmt(stock['ec_stock'], True)}}};\n"
        f"const uint16_t FC_GAMMA_STOCK[96] = {{{_fmt(stock['fc_stock'], False)}}};\n"
        f"const int16_t COLOR_A0_STOCK[9] = {{{_fmt(stock['a0_stock'], False)}}};\n"
        f"const int16_t COLOR_A1_STOCK[9] = {{{_fmt(stock['a1_stock'], False)}}};\n"
        f"const int16_t COLOR_B2_STOCK[9] = {{{_fmt(stock['b2_stock'], False)}}};\n\n"
        f"const uint32_t LIVE_EC[{len(presets)}][96] = {{{','.join(ecs)}}};\n"
        f"const uint16_t LIVE_FC[{len(presets)}][96] = {{{','.join(fcs)}}};\n"
        f"const int16_t LIVE_A0[{len(presets)}][9] = {{{','.join(a0s)}}};\n"
        f"const int16_t LIVE_A1[{len(presets)}][9] = {{{','.join(a1s)}}};\n"
        f"const int16_t LIVE_B2[{len(presets)}][9] = {{{','.join(b2s)}}};\n"
        + lut_block
    )
    out_header.parent.mkdir(parents=True, exist_ok=True)
    out_header.write_text(hdr, encoding="utf-8")
    return out_header


def main() -> None:
    ap = argparse.ArgumentParser(description="Build BIONZ X Film Sim .mod files or live-picker headers.")
    ap.add_argument("--target", choices=tuple(TARGETS), default="a6100")
    ap.add_argument("--pipeline", choices=("full", "legacy"), default="full",
                    help="full: 3D-fitted EC/FC + Q10 + 5x9x9 RefAdr[0]; legacy: parametric sliders")
    ap.add_argument("--emit-live-header", type=Path, help="Write film_sim_live_data.h to PATH")
    ap.add_argument("--multi-target", action="store_true",
                    help="Emit #ifdef TARGET_A6400 / TARGET_A6400_V1 / a6100 PA block in live header")
    ap.add_argument("--build", metavar="PRESET", help="Build SMOD v2 .mod for PRESET name")
    ap.add_argument("--cube", type=Path, help="Fit a 3D .cube LUT file into a standalone SMOD v2 .mod")
    ap.add_argument("--name", help="Display name for --cube mod (default: cube title or filename)")
    ap.add_argument("--out", type=Path, help="Output directory for --build or --cube")
    args = ap.parse_args()
    if args.emit_live_header:
        out = emit_live_picker_header(
            args.emit_live_header,
            target=args.target,
            pipeline=args.pipeline,
            multi_target=args.multi_target,
        )
        print(f"Wrote {out} for {args.target} (pipeline={args.pipeline})")
    if args.cube:
        from lut_core.cube import load_cube

        cube_obj = load_cube(args.cube)
        mod_name = (args.name or cube_obj.title or args.cube.stem)[:31]
        res = build_mod(
            FilmSimParams(name=mod_name),
            out_dir=args.out,
            target=args.target,
            pipeline="full",
            cube=cube_obj,
        )
        print(json.dumps(res, indent=2))
    if args.build:
        if args.build not in PRESETS:
            raise SystemExit(f"Unknown preset {args.build!r}; choices: {', '.join(PRESETS)}")
        res = build_mod(PRESETS[args.build], out_dir=args.out, target=args.target, pipeline=args.pipeline)
        print(json.dumps(res, indent=2))
    if not args.emit_live_header and not args.build and not args.cube:
        ap.print_help()


if __name__ == "__main__":
    main()
