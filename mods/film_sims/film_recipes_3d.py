"""Nonlinear 33^3 3D film-simulation target models for BIONZ X full-ISP fitting.

Each recipe outputs a 33x33x33 Cube in post-gamma [0, 1] display space that
encodes coupled tone curve, color-matrix cross-talk, highlight desaturation,
luminance-dependent split-toning, and selective hue-vs-hue / hue-vs-sat /
hue-vs-luma (dY) sculpting with skin-tone protection.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

_CONVERTER_ROOT = Path(__file__).resolve().parents[2] / "a6400-lut-converter"
if str(_CONVERTER_ROOT) not in sys.path:
    sys.path.insert(0, str(_CONVERTER_ROOT))

from lut_core.cube import Cube  # noqa: E402


@dataclass(frozen=True)
class FilmRecipe3D:
    name: str
    # Filmic tone curve in [0, 1]
    shadow_toe: float = 0.0         # negative crushes shadows, positive lifts
    mid_contrast: float = 1.0       # S-curve slope around crossover
    crossover: float = 0.28         # anchor point
    highlight_rolloff: float = 0.0  # 0..1 Hermite shoulder slope reduction
    shoulder: float = 0.0           # 0..1 extra ARRI-style highlight knee (0 = off)
    black_fade: float = 0.0         # 0..0.05 lifted black floor
    white_ceiling: float = 1.0      # 0.90..1.0 peak white
    # Global color & crosstalk
    saturation: float = 1.0
    warmth: float = 0.0             # + = warm (R+, B-), - = cool
    red_amber: float = 0.0          # + = reds shift warmer toward orange/amber
    green_olive: float = 0.0        # + = greens shift warmer/olive
    blue_cyan: float = 0.0          # + = blues shift toward slate/cyan
    highlight_desat: float = 0.0    # 0..0.6 highlight chroma roll-off
    # Nonlinear hue-sector sculpting (3D LUT domain: hue/luma/sat)
    sky_luma: float = 0.0           # negative darkens deep blue/cyan sky (dY < 0)
    sky_sat: float = 0.0            # negative desaturates blue/cyan
    foliage_luma: float = 0.0       # luma shift on greens
    foliage_sat: float = 0.0        # saturation shift on greens
    red_luma: float = 0.0           # luma shift on reds (e.g., B&W filter lift)
    red_highlight_hue: float = 0.0  # + pushes bright reds toward copper/orange
    skin_protect: float = 0.65      # 0..1 reduces hue twist / desat on skin tones
    # Luminance-dependent split toning (Cb/Cr style in opponent space)
    shadow_warmth: float = 0.0      # + warm amber shadows, - cool cyan/blue
    shadow_tint: float = 0.0        # + magenta shadows, - green shadows
    highlight_warmth: float = 0.0   # + warm amber/gold highlights, - cool
    highlight_tint: float = 0.0     # + magenta/copper, - green
    monochrome: bool = False
    bw_weights: tuple[float, float, float] = (0.30, 0.59, 0.11)


RECIPES_3D: dict[str, FilmRecipe3D] = {
    "Classic Chrome": FilmRecipe3D(
        name="Classic Chrome",
        shadow_toe=-0.15,
        mid_contrast=1.18,
        crossover=0.28,
        highlight_rolloff=0.44,
        shoulder=0.40,
        black_fade=0.004,
        white_ceiling=0.990,
        saturation=0.78,
        warmth=0.012,
        red_amber=0.10,
        green_olive=0.14,
        blue_cyan=0.16,
        highlight_desat=0.32,
        sky_luma=-0.16,
        sky_sat=-0.18,
        foliage_luma=-0.04,
        foliage_sat=-0.20,
        red_luma=-0.03,
        red_highlight_hue=0.05,
        skin_protect=0.82,
        shadow_warmth=-0.012,
        highlight_warmth=0.010,
    ),
    "Classic Neg": FilmRecipe3D(
        name="Classic Neg",
        shadow_toe=-0.18,
        mid_contrast=1.24,
        crossover=0.30,
        highlight_rolloff=0.48,
        shoulder=0.35,
        black_fade=0.008,
        white_ceiling=0.985,
        saturation=0.88,
        warmth=-0.015,
        red_amber=0.16,
        green_olive=0.12,
        blue_cyan=0.16,
        highlight_desat=0.28,
        sky_luma=-0.12,
        sky_sat=-0.12,
        foliage_luma=-0.06,
        foliage_sat=-0.12,
        red_luma=0.03,
        red_highlight_hue=0.18,
        skin_protect=0.80,
        shadow_warmth=-0.035,
        shadow_tint=-0.025,
        highlight_warmth=0.038,
        highlight_tint=0.015,
    ),
    "Portra 400": FilmRecipe3D(
        name="Portra 400",
        shadow_toe=0.05,
        mid_contrast=0.98,
        crossover=0.26,
        highlight_rolloff=0.52,
        shoulder=0.50,
        black_fade=0.006,
        white_ceiling=0.995,
        saturation=0.95,
        warmth=0.030,
        red_amber=0.08,
        green_olive=0.09,
        blue_cyan=0.07,
        highlight_desat=0.30,
        sky_luma=-0.03,
        sky_sat=-0.08,
        foliage_luma=0.02,
        foliage_sat=-0.10,
        red_luma=0.02,
        red_highlight_hue=0.05,
        skin_protect=0.92,
        shadow_warmth=0.010,
        highlight_warmth=0.025,
    ),
    "Gold 200": FilmRecipe3D(
        name="Gold 200",
        shadow_toe=-0.06,
        mid_contrast=1.10,
        crossover=0.27,
        highlight_rolloff=0.36,
        shoulder=0.35,
        black_fade=0.006,
        white_ceiling=0.995,
        saturation=1.08,
        warmth=0.045,
        red_amber=0.14,
        green_olive=0.05,
        blue_cyan=0.08,
        highlight_desat=0.20,
        sky_luma=-0.07,
        sky_sat=-0.06,
        foliage_luma=0.04,
        foliage_sat=0.03,
        red_luma=0.04,
        red_highlight_hue=0.10,
        skin_protect=0.88,
        shadow_warmth=0.015,
        highlight_warmth=0.038,
    ),
    "Vision3 800T": FilmRecipe3D(
        name="Vision3 800T",
        shadow_toe=-0.12,
        mid_contrast=1.15,
        crossover=0.28,
        highlight_rolloff=0.52,
        shoulder=0.55,
        black_fade=0.012,
        white_ceiling=0.985,
        saturation=0.90,
        warmth=-0.055,
        red_amber=0.14,
        green_olive=0.08,
        blue_cyan=0.20,
        highlight_desat=0.32,
        sky_luma=-0.10,
        sky_sat=0.06,
        foliage_luma=-0.06,
        foliage_sat=-0.14,
        red_luma=0.03,
        red_highlight_hue=0.16,
        skin_protect=0.80,
        shadow_warmth=-0.045,
        shadow_tint=-0.010,
        highlight_warmth=0.045,
        highlight_tint=0.012,
    ),
    "ETERNA": FilmRecipe3D(
        name="ETERNA",
        shadow_toe=0.12,
        mid_contrast=0.85,
        crossover=0.28,
        highlight_rolloff=0.65,
        shoulder=0.55,
        black_fade=0.014,
        white_ceiling=0.965,
        saturation=0.70,
        warmth=-0.010,
        red_amber=0.06,
        green_olive=0.10,
        blue_cyan=0.12,
        highlight_desat=0.42,
        sky_luma=-0.06,
        sky_sat=-0.12,
        foliage_luma=0.0,
        foliage_sat=-0.12,
        red_luma=0.0,
        red_highlight_hue=0.03,
        skin_protect=0.88,
        shadow_warmth=-0.015,
        highlight_warmth=0.015,
    ),
    "Eterna Bleach Bypass": FilmRecipe3D(
        name="Eterna Bleach Bypass",
        shadow_toe=-0.20,
        mid_contrast=1.30,
        crossover=0.30,
        highlight_rolloff=0.30,
        shoulder=0.30,
        black_fade=0.014,
        white_ceiling=0.990,
        saturation=0.40,
        warmth=-0.020,
        red_amber=0.02,
        green_olive=0.02,
        blue_cyan=0.08,
        highlight_desat=0.30,
        sky_luma=-0.10,
        sky_sat=-0.06,
        foliage_luma=-0.04,
        foliage_sat=-0.10,
        red_luma=0.02,
        red_highlight_hue=0.02,
        skin_protect=0.55,
        shadow_warmth=-0.010,
        shadow_tint=-0.020,
        highlight_warmth=0.0,
        highlight_tint=0.015,
    ),
    "Pro Negative": FilmRecipe3D(
        name="Pro Negative",
        shadow_toe=0.10,
        mid_contrast=0.92,
        crossover=0.26,
        highlight_rolloff=0.50,
        shoulder=0.55,
        black_fade=0.004,
        white_ceiling=0.995,
        saturation=0.94,
        warmth=0.018,
        red_amber=0.05,
        green_olive=0.06,
        blue_cyan=0.05,
        highlight_desat=0.20,
        sky_luma=-0.02,
        sky_sat=-0.04,
        foliage_luma=0.02,
        foliage_sat=-0.06,
        red_luma=0.01,
        red_highlight_hue=0.03,
        skin_protect=0.95,
        shadow_warmth=0.008,
        highlight_warmth=0.015,
    ),
    "Reala Ace": FilmRecipe3D(
        name="Reala Ace",
        shadow_toe=-0.08,
        mid_contrast=1.14,
        crossover=0.27,
        highlight_rolloff=0.42,
        shoulder=0.40,
        black_fade=0.004,
        white_ceiling=0.995,
        saturation=1.02,
        warmth=0.012,
        red_amber=0.06,
        green_olive=0.05,
        blue_cyan=0.08,
        highlight_desat=0.18,
        sky_luma=-0.06,
        sky_sat=0.02,
        foliage_luma=0.01,
        foliage_sat=0.02,
        red_luma=0.01,
        red_highlight_hue=0.04,
        skin_protect=0.90,
        shadow_warmth=0.004,
        highlight_warmth=0.012,
    ),
    "Astia": FilmRecipe3D(
        name="Astia",
        shadow_toe=0.06,
        mid_contrast=0.96,
        crossover=0.26,
        highlight_rolloff=0.48,
        shoulder=0.50,
        black_fade=0.004,
        white_ceiling=0.995,
        saturation=1.02,
        warmth=0.010,
        red_amber=0.06,
        green_olive=0.04,
        blue_cyan=0.04,
        highlight_desat=0.22,
        sky_luma=-0.02,
        sky_sat=0.06,
        foliage_luma=0.01,
        foliage_sat=0.04,
        red_luma=0.01,
        red_highlight_hue=0.03,
        skin_protect=0.94,
        shadow_warmth=0.006,
        highlight_warmth=0.012,
    ),
    "Acros": FilmRecipe3D(
        name="Acros",
        shadow_toe=-0.08,
        mid_contrast=1.08,
        crossover=0.27,
        highlight_rolloff=0.36,
        shoulder=0.45,
        black_fade=0.0,
        white_ceiling=0.994,
        saturation=0.0,
        sky_luma=-0.12,
        foliage_luma=-0.03,
        red_luma=0.06,
        skin_protect=0.0,
        monochrome=True,
        bw_weights=(0.34, 0.55, 0.11),
    ),
    "Velvia": FilmRecipe3D(
        name="Velvia",
        shadow_toe=-0.16,
        mid_contrast=1.26,
        crossover=0.25,
        highlight_rolloff=0.28,
        shoulder=0.25,
        black_fade=0.0,
        white_ceiling=1.0,
        saturation=1.30,
        warmth=0.018,
        red_amber=-0.04,
        green_olive=-0.06,
        blue_cyan=-0.04,
        highlight_desat=0.12,
        sky_luma=-0.12,
        sky_sat=0.14,
        foliage_luma=-0.04,
        foliage_sat=0.10,
        red_luma=-0.02,
        skin_protect=0.72,
    ),
    "Nostalgic Neg": FilmRecipe3D(
        name="Nostalgic Neg",
        shadow_toe=-0.06,
        mid_contrast=1.08,
        crossover=0.24,
        highlight_rolloff=0.42,
        black_fade=0.010,
        white_ceiling=0.985,
        saturation=0.94,
        warmth=0.06,
        red_amber=0.18,
        green_olive=0.10,
        blue_cyan=0.15,
        highlight_desat=0.30,
        sky_luma=-0.10,
        sky_sat=-0.12,
        foliage_luma=-0.02,
        foliage_sat=-0.10,
        red_luma=0.04,
        red_highlight_hue=0.14,
        skin_protect=0.75,
        highlight_warmth=0.060,
        highlight_tint=0.025,
    ),
    "Stock (Reset)": FilmRecipe3D(name="Stock (Reset)"),
}


def _shoulder_vec(y: np.ndarray, strength: float, knee: float = 0.55) -> np.ndarray:
    """ARRI-style highlight knee: identity below `knee`, then a C1 cubic that starts with slope 1
    and ends with slope `1 - strength` at white. No clipping, no plateau: the top of the range is
    compressed but every step keeps a positive slope, like the soft shoulder of a film print.
    """
    s = float(np.clip(strength, 0.0, 1.0))
    if s <= 1e-9:
        return y
    k = float(knee)
    u = np.clip((y - k) / max(1.0 - k, 1e-9), 0.0, 1.0)
    g = u + s * u * u * (1.0 - u)
    return np.where(y <= k, y, k + (1.0 - k) * g)


def _filmic_tone_vec(x: np.ndarray, r: FilmRecipe3D) -> np.ndarray:
    """C1-continuous filmic curve: two cubic Hermite segments with a matched slope at `crossover`.

    Segment slopes: `m0` at black (shadow_toe), `m1` at the anchor (mid_contrast), `m2` at white
    (highlight_rolloff). The anchor height is the average of the two slopes, which keeps every
    Hermite segment monotone (Fritsch-Carlson bounded) and removes the flat kink the old power
    curve had at `crossover`.
    """
    x = np.clip(x, 0.0, 1.0)
    xc = float(np.clip(r.crossover, 0.08, 0.80))
    m0 = float(np.clip(1.0 + 1.30 * r.shadow_toe, 0.35, 2.0))
    m1 = float(np.clip(r.mid_contrast, 0.50, 1.70))
    m2 = float(np.clip(1.0 - 0.85 * r.highlight_rolloff, 0.08, 1.0))
    yc = float(np.clip(xc * 0.5 * (m0 + m1), 0.02, 0.95))
    span = 1.0 - xc

    def hermite(t: np.ndarray, y1: float, slope1: float, y2: float, slope2: float, h: float) -> np.ndarray:
        h00 = 2.0 * t**3 - 3.0 * t**2 + 1.0
        h10 = t**3 - 2.0 * t**2 + t
        h01 = -2.0 * t**3 + 3.0 * t**2
        h11 = t**3 - t**2
        return h00 * y1 + h10 * h * slope1 + h01 * y2 + h11 * h * slope2

    t_shad = np.clip(x / max(xc, 1e-9), 0.0, 1.0)
    y_shad = hermite(t_shad, 0.0, m0, yc, m1, xc)
    t_hi = np.clip((x - xc) / max(span, 1e-9), 0.0, 1.0)
    y_hi = hermite(t_hi, yc, m1, 1.0, m2, span)
    y = np.where(x <= xc, y_shad, y_hi)
    y = _shoulder_vec(np.clip(y, 0.0, 1.0), r.shoulder)
    return np.clip(r.black_fade + (r.white_ceiling - r.black_fade) * np.clip(y, 0.0, 1.0), 0.0, 1.0)


def _hue_weight(hue_deg: np.ndarray, center_deg: float, half_width: float) -> np.ndarray:
    """Raised-cosine angular sector weight in [0, 1]."""
    diff = np.abs(((hue_deg - center_deg + 180.0) % 360.0) - 180.0)
    u = np.clip(diff / half_width, 0.0, 1.0)
    return 0.5 * (1.0 + np.cos(np.pi * u))


def _look_matrix(r: FilmRecipe3D) -> np.ndarray:
    """White-point-preserving matrix: saturation plus primary hue rotation, each row sums to 1.

    Warmth is *not* folded into this matrix (the old row-normalization cancelled it on neutrals and
    distorted cross-channel ratios); it is applied later as a luminance-tapered tint.
    """
    sat = r.saturation
    lr, lg, lb = 0.2126, 0.7152, 0.0722
    m_sat = np.array([
        [lr + (1.0 - lr) * sat, lg * (1.0 - sat), lb * (1.0 - sat)],
        [lr * (1.0 - sat), lg + (1.0 - lg) * sat, lb * (1.0 - sat)],
        [lr * (1.0 - sat), lg * (1.0 - sat), lb + (1.0 - lb) * sat],
    ], dtype=np.float64)
    ra = float(np.clip(r.red_amber, -0.35, 0.35))
    go = float(np.clip(r.green_olive, -0.35, 0.35))
    bc = float(np.clip(r.blue_cyan, -0.35, 0.35))
    m_hue = np.array([
        [1.0 - ra, ra, 0.0],
        [go * 0.5, 1.0 - go, go * 0.5],
        [0.0, bc, 1.0 - bc],
    ], dtype=np.float64)
    return m_hue @ m_sat


def evaluate_recipe_3d(rgb: np.ndarray, r: FilmRecipe3D) -> np.ndarray:
    """Evaluate nonlinear 3D film simulation over rgb[..., 3] in [0, 1]."""
    rgb = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
    if r.name == "Stock (Reset)":
        return rgb

    # 1. Filmic tone curve per channel (preserves neutral gray)
    toned = _filmic_tone_vec(rgb, r)

    # Compute opponent hue/chroma from toned input for selective sculpting
    luma = 0.2126 * toned[..., 0] + 0.7152 * toned[..., 1] + 0.0722 * toned[..., 2]
    cb = toned[..., 2] - luma
    cr = toned[..., 0] - luma
    chroma = np.hypot(cb, cr)
    sat_mask = np.clip(chroma / 0.12, 0.0, 1.0)
    hue_deg = (np.degrees(np.arctan2(cb, cr)) + 360.0) % 360.0
    # Skin tones sit around cr > 0, cb < 0 -> hue ~ 325..360 / 0..35
    skin_w = r.skin_protect * _hue_weight(hue_deg, 350.0, 38.0) * np.clip(chroma / 0.06, 0.0, 1.0) * (1.0 - np.clip((chroma - 0.28) / 0.15, 0.0, 1.0))

    # Hue sectors in (Cr, Cb) degrees: Red ~0/360, Green ~215, Cyan ~180, Blue ~105
    w_sky = np.maximum(_hue_weight(hue_deg, 105.0, 48.0), _hue_weight(hue_deg, 155.0, 42.0)) * sat_mask
    w_foliage = _hue_weight(hue_deg, 225.0, 52.0) * sat_mask * (1.0 - skin_w)
    w_red = _hue_weight(hue_deg, 5.0, 40.0) * sat_mask

    if r.monochrome:
        wr, wg, wb = r.bw_weights
        mono = wr * toned[..., 0] + wg * toned[..., 1] + wb * toned[..., 2]
        dy = (r.sky_luma * w_sky + r.foliage_luma * w_foliage + r.red_luma * w_red) * np.clip(chroma / 0.10, 0.0, 1.0)
        mono = np.clip(mono + dy, 0.0, 1.0)
        return np.repeat(mono[..., None], 3, axis=-1)

    # 2. Base look matrix + highlight desaturation
    mat = _look_matrix(r)
    out = np.clip(toned @ mat.T, 0.0, 1.0)

    y_out = 0.2126 * out[..., 0] + 0.7152 * out[..., 1] + 0.0722 * out[..., 2]
    if r.highlight_desat > 0.0:
        hi_desat = r.highlight_desat * np.clip((y_out - 0.62) / 0.35, 0.0, 1.0)
        out = y_out[..., None] + (1.0 - hi_desat[..., None]) * (out - y_out[..., None])

    # 2b. Chromatic warmth: luminance-tapered tint that peaks in the midtones and is zero at the
    # black and white anchors. Coefficients keep Rec.709 luma unchanged; skin protection scales it.
    if abs(r.warmth) > 1e-9:
        mid = 4.0 * np.clip(y_out, 0.0, 1.0) * (1.0 - np.clip(y_out, 0.0, 1.0))
        w = r.warmth * mid * (1.0 - 0.35 * skin_w)
        out[..., 0] = np.clip(out[..., 0] + 0.5 * w, 0.0, 1.0)
        out[..., 1] = np.clip(out[..., 1] - 0.098 * w, 0.0, 1.0)
        out[..., 2] = np.clip(out[..., 2] - 0.5 * w, 0.0, 1.0)

    # 3. Selective hue-vs-luma (dY) & hue-vs-sat
    dy = (r.sky_luma * w_sky + r.foliage_luma * w_foliage + r.red_luma * w_red) * (0.25 + 0.75 * np.sin(np.pi * np.clip(y_out, 0.0, 1.0)))
    sat_adj = 1.0 + (r.sky_sat * w_sky + r.foliage_sat * w_foliage) * (1.0 - 0.7 * skin_w)
    y_adj = np.clip(y_out + dy, 0.0, 1.0)
    out = y_adj[..., None] + sat_adj[..., None] * (out - y_out[..., None])

    # 4. Highlight red -> copper/orange twist (Classic Neg / Kodak signature)
    if abs(r.red_highlight_hue) > 1e-6:
        hi_w = np.clip((y_out - 0.35) / 0.50, 0.0, 1.0) * w_red * (1.0 - 0.55 * skin_w)
        twist = r.red_highlight_hue * hi_w
        out[..., 1] = np.clip(out[..., 1] + twist * 0.22 * (out[..., 0] - out[..., 1]), 0.0, 1.0)
        out[..., 2] = np.clip(out[..., 2] - twist * 0.12 * (out[..., 0] - out[..., 2]), 0.0, 1.0)

    # 5. Luminance-dependent split toning (shadows vs highlights)
    w_shad = np.clip(1.0 - y_adj / 0.45, 0.0, 1.0) ** 1.4
    w_hi = np.clip((y_adj - 0.48) / 0.48, 0.0, 1.0) ** 1.3
    d_r = r.shadow_warmth * w_shad + r.highlight_warmth * w_hi + 0.5 * (r.shadow_tint * w_shad + r.highlight_tint * w_hi)
    d_g = -0.6 * (r.shadow_tint * w_shad + r.highlight_tint * w_hi)
    d_b = -(r.shadow_warmth * w_shad + r.highlight_warmth * w_hi) + 0.5 * (r.shadow_tint * w_shad + r.highlight_tint * w_hi)
    # Taper split toning near pure black and pure white so 0 and 1 stay anchored
    anchor = np.clip(np.minimum(y_adj / 0.06, (1.0 - y_adj) / 0.04), 0.0, 1.0)
    out[..., 0] += d_r * anchor
    out[..., 1] += d_g * anchor
    out[..., 2] += d_b * anchor
    return np.clip(out, 0.0, 1.0)


def build_recipe_cube(name: str, size: int = 33) -> Cube:
    """Build a 33^3 Cube target for the named 3D film simulation recipe."""
    if name not in RECIPES_3D:
        raise KeyError(f"Unknown 3D film recipe {name!r}; choices: {', '.join(RECIPES_3D)}")
    r = RECIPES_3D[name]
    ax = np.linspace(0.0, 1.0, size, dtype=np.float64)
    # Cube.table shape is [r, g, b, 3]
    rr, g, b = np.meshgrid(ax, ax, ax, indexing="ij")
    grid_rgb = np.stack([rr, g, b], axis=-1)
    table = evaluate_recipe_3d(grid_rgb, r)
    return Cube(
        size=size,
        domain_min=np.zeros(3, dtype=np.float64),
        domain_max=np.ones(3, dtype=np.float64),
        table=table,
        title=name,
    )


def list_recipes_3d() -> list[str]:
    return list(RECIPES_3D.keys())


def describe_recipe_3d(name: str) -> dict[str, Any]:
    r = RECIPES_3D[name]
    return {
        "name": r.name,
        "monochrome": r.monochrome,
        "sky_luma": r.sky_luma,
        "highlight_desat": r.highlight_desat,
        "red_highlight_hue": r.red_highlight_hue,
    }
