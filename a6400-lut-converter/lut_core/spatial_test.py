"""Offline visual & quantitative comparison of ARRI ADA-7/AFA vs Sony DCV PSF & attenuated fixes."""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import numpy as np

# Default paths relative to repository root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARRI_E0600_JSON = _REPO_ROOT / "artifacts" / "analysis" / "arri-sdk-pipeline-e0600" / "e0600_arri_debayer_and_afa_tables.json"
_SABIN_BAYER_BIN = _REPO_ROOT / "firmware" / "extracted_a6400" / "firmware.tar_unpacked" / "0700_part_image" / "dev" / "nflasha15_unpacked" / "sabin" / "dcv_psf_table_movie_bayer.bin"


def _load_arri_tables():
    data = json.loads(_ARRI_E0600_JSON.read_text(encoding="utf-8"))
    wmat = np.array(data["weight_matrices"]["ada7WeightMatrix"]["rows_32x32"], dtype=np.float64) / 32768.0
    afa_smooth = np.array(data["afa_filter_banks"]["ALEV4_AFATexture_09T_0.1.0_variant1"]["table_a_rows"][-1], dtype=np.float64)
    afa_chroma = np.array(data["afa_filter_banks"]["GENERIC_AfaSmoothChroma_09T_0.0.7"]["table_a_rows"][0], dtype=np.float64)
    return wmat, afa_smooth, afa_chroma


def _load_sony_dcv_center_1d(gain: float = 1.0) -> np.ndarray:
    b = _SABIN_BAYER_BIN.read_bytes()
    vals = struct.unpack_from(f"<{len(b) // 2}h", b, 0)
    idx = 0x11C // 2  # center 7812
    delta = np.array(vals[idx - 4 : idx + 5], dtype=np.float64) * (gain / 4096.0)
    k = delta.copy()
    k[4] += 1.0
    return k / k.sum()


def _convolve_sep_2d(img: np.ndarray, k1d: np.ndarray) -> np.ndarray:
    """Separable 2D convolution on (H, W) or (H, W, C) float64 image."""
    r = len(k1d) // 2
    if img.ndim == 2:
        pad_x = np.pad(img, ((0, 0), (r, r)), mode="edge")
        tmp = sum(k1d[i] * pad_x[:, i : i + img.shape[1]] for i in range(len(k1d)))
        pad_y = np.pad(tmp, ((r, r), (0, 0)), mode="edge")
        return sum(k1d[i] * pad_y[i : i + img.shape[0], :] for i in range(len(k1d)))
    out = np.empty_like(img)
    for c in range(img.shape[2]):
        out[..., c] = _convolve_sep_2d(img[..., c], k1d)
    return out


def make_synthetic_bayer_chart(height: int = 256, width: int = 256, seed: int = 42) -> tuple[np.ndarray, np.ndarray]:
    """Build a synthetic 16-bit RGGB Bayer chart (sharp edges, zone plate, color stripes, dark noise ramp)."""
    rng = np.random.default_rng(seed)
    rgb = np.zeros((height, width, 3), dtype=np.float64)

    # Top-left: high-contrast sharp step edges & diagonal bar
    rgb[:128, :128] = 0.08
    rgb[24:104, 48:112] = 0.88
    yy, xx = np.mgrid[:128, :128]
    diag = (np.abs((xx - 16) - (yy - 16)) < 6) & (xx > 10) & (xx < 42)
    rgb[:128, :128][diag] = 0.88

    # Top-right: high-frequency fine stripes (moire / false-color stress test)
    for x in range(128, 256):
        v = 0.5 + 0.42 * math.sin(2.0 * math.pi * (x - 128) * (0.12 + 0.33 * (x - 128) / 128.0))
        rgb[:128, x, :] = v

    # Bottom-left: saturated color edges (red/cyan/blue patches)
    rgb[128:, :128] = 0.15
    rgb[144:240, 16:60] = [0.85, 0.12, 0.12]
    rgb[144:240, 68:112] = [0.12, 0.18, 0.85]

    # Bottom-right: dark shadow ramp + realistic sensor read noise
    ramp = np.linspace(0.005, 0.12, 128)[None, :]
    rgb[128:, 128:] = ramp[..., None]
    noise = rng.normal(0.0, 0.004, size=rgb.shape)
    rgb = np.clip(rgb + noise, 0.0, 1.0)

    # Sample RGGB Bayer mosaic in 14-bit-in-16-bit scale (0..16383)
    bayer = np.zeros((height, width), dtype=np.float64)
    bayer[0::2, 0::2] = rgb[0::2, 0::2, 0]
    bayer[0::2, 1::2] = rgb[0::2, 1::2, 1]
    bayer[1::2, 0::2] = rgb[1::2, 0::2, 1]
    bayer[1::2, 1::2] = rgb[1::2, 1::2, 2]
    return np.round(bayer * 16383.0), rgb


def debayer_arri_ada7_sim(bayer: np.ndarray, wmat: np.ndarray, afa_smooth: np.ndarray, afa_chroma: np.ndarray) -> np.ndarray:
    """Simulate ARRI ADA-7 HW directional green + shadow damping + gated chroma + AFA 9-tap filtering."""
    h, w = bayer.shape
    p = np.pad(bayer, 2, mode="edge")
    c = p[2:-2, 2:-2]

    # Horizontal and vertical 5x5 gradient approximations
    gh = np.abs(p[2:-2, 1:-3] - p[2:-2, 3:-1]) * 2.0 + np.abs(p[1:-3, 1:-3] - p[1:-3, 3:-1]) + np.abs(p[3:-1, 1:-3] - p[3:-1, 3:-1])
    gv = np.abs(p[1:-3, 2:-2] - p[3:-1, 2:-2]) * 2.0 + np.abs(p[1:-3, 1:-3] - p[3:-1, 1:-3]) + np.abs(p[1:-3, 3:-1] - p[3:-1, 3:-1])
    ih = np.clip((gh / 256.0).astype(int), 0, 31)
    iv = np.clip((gv / 256.0).astype(int), 0, 31)
    weight = wmat[iv, ih]

    # Horizontal & vertical green estimates with <1024 shadow damping
    g_base_h = 0.5 * (p[2:-2, 1:-3] + p[2:-2, 3:-1])
    g_base_v = 0.5 * (p[1:-3, 2:-2] + p[3:-1, 2:-2])
    lap_h = (2.0 * c - p[2:-2, 0:-4] - p[2:-2, 4:]) / 8.0
    lap_v = (2.0 * c - p[0:-4, 2:-2] - p[4:, 2:-2]) / 8.0
    damp_h = np.minimum(1.0, g_base_h / 1024.0)
    damp_v = np.minimum(1.0, g_base_v / 1024.0)
    g_h = np.clip(g_base_h + damp_h * lap_h, 0.0, 16383.0)
    g_v = np.clip(g_base_v + damp_v * lap_v, 0.0, 16383.0)
    g_est = g_v + weight * (g_h - g_v)

    g = bayer.copy()
    g[0::2, 0::2] = g_est[0::2, 0::2]
    g[1::2, 1::2] = g_est[1::2, 1::2]

    # Color-difference bilinear R and B
    r_diff = np.zeros_like(bayer)
    b_diff = np.zeros_like(bayer)
    r_diff[0::2, 0::2] = bayer[0::2, 0::2] - g[0::2, 0::2]
    b_diff[1::2, 1::2] = bayer[1::2, 1::2] - g[1::2, 1::2]

    # Interpolate R-G and B-G
    k_rb = np.array([[0.25, 0.5, 0.25], [0.5, 1.0, 0.5], [0.25, 0.5, 0.25]], dtype=np.float64)
    pr = np.pad(r_diff, 1, mode="edge")
    pb = np.pad(b_diff, 1, mode="edge")
    rg = sum(k_rb[dy, dx] * pr[dy : dy + h, dx : dx + w] for dy in range(3) for dx in range(3))
    bg = sum(k_rb[dy, dx] * pb[dy : dy + h, dx : dx + w] for dy in range(3) for dx in range(3))
    r = np.clip(g + rg, 0.0, 16383.0)
    b = np.clip(g + bg, 0.0, 16383.0)

    # ada7Hw-style [1,2,1]/4 chroma LP + shadow-gated (G - G_lp) transfer
    lp = np.array([0.25, 0.5, 0.25], dtype=np.float64)
    g_lp = _convolve_sep_2d(g, lp)
    r_lp = _convolve_sep_2d(r, lp)
    b_lp = _convolve_sep_2d(b, lp)
    penalty = np.abs(r - g) + np.abs(b - g)
    shadow_mult = np.where(g < 2048.0, 4.0, np.where(g < 4096.0, 2.0, 1.0))
    gate = np.clip(1.0 - (shadow_mult * penalty) / 4096.0, 0.0, 1.0)
    r = np.clip(r_lp + gate * (g - g_lp), 0.0, 16383.0)
    b = np.clip(b_lp + gate * (g - g_lp), 0.0, 16383.0)

    rgb = np.stack([r, g, b], axis=-1) / 16383.0
    # Apply ARRI 9-tap AFA Smooth on Luma and SmoothChroma on Cb/Cr
    y = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    cb = rgb[..., 2] - y
    cr = rgb[..., 0] - y
    y_f = _convolve_sep_2d(y, afa_smooth)
    cb_f = _convolve_sep_2d(cb, afa_chroma)
    cr_f = _convolve_sep_2d(cr, afa_chroma)
    out = np.stack([y_f + cr_f, y_f - (0.2126 / 0.7152) * cr_f - (0.0722 / 0.7152) * cb_f, y_f + cb_f], axis=-1)
    return np.clip(out, 0.0, 1.0)


def apply_chroma_smooth(rgb: np.ndarray, afa_chroma: np.ndarray) -> np.ndarray:
    """Apply ARRI GENERIC_AfaSmoothChroma_09T-style chroma anti-aliasing in YCbCr."""
    y = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    cb = rgb[..., 2] - y
    cr = rgb[..., 0] - y
    cb_f = _convolve_sep_2d(cb, afa_chroma)
    cr_f = _convolve_sep_2d(cr, afa_chroma)
    out = np.stack([y + cr_f, y - (0.2126 / 0.7152) * cr_f - (0.0722 / 0.7152) * cb_f, y + cb_f], axis=-1)
    return np.clip(out, 0.0, 1.0)


def apply_spatial_mode(rgb: np.ndarray, spatial_mode: str) -> np.ndarray:
    """Apply a spatial/DCV profile ('off', 'sony_stock', 'arri_25pct', 'arri_00pct', 'arri_full') to RGB [0..1]."""
    if spatial_mode in (None, "off"):
        return rgb
    _wmat, afa_smooth, afa_chroma = _load_arri_tables()
    if spatial_mode == "sony_stock":
        return np.clip(_convolve_sep_2d(rgb, _load_sony_dcv_center_1d(1.0)), 0.0, 1.0)
    if spatial_mode in ("arri_25pct", "arri_00pct"):
        gain = 0.25 if spatial_mode == "arri_25pct" else 0.0
        sharp = _convolve_sep_2d(rgb, _load_sony_dcv_center_1d(gain)) if gain > 0.0 else rgb
        luma = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
        w_core = np.clip(luma / 0.08, 0.0, 1.0)[..., None]
        cored = np.clip(rgb + w_core * (sharp - rgb), 0.0, 1.0)
        return apply_chroma_smooth(cored, afa_chroma)
    if spatial_mode == "arri_full":
        y = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
        cb = rgb[..., 2] - y
        cr = rgb[..., 0] - y
        y_f = _convolve_sep_2d(y, afa_smooth)
        cb_f = _convolve_sep_2d(cb, afa_chroma)
        cr_f = _convolve_sep_2d(cr, afa_chroma)
        out = np.stack([y_f + cr_f, y_f - (0.2126 / 0.7152) * cr_f - (0.0722 / 0.7152) * cb_f, y_f + cb_f], axis=-1)
        return np.clip(out, 0.0, 1.0)
    raise ValueError(f"unsupported spatial_mode {spatial_mode!r}")


def debayer_sony_dcv_sim(bayer: np.ndarray, dcv_k1d: np.ndarray, shadow_coring: float = 0.0,
                         chroma_smooth: np.ndarray | None = None, directional_g: bool = False) -> np.ndarray:
    """Simulate demosaic + Sony DCV PSF kernel (with optional directional G, shadow coring, and chroma smooth)."""
    h, w = bayer.shape
    g = bayer.copy()
    if directional_g:
        p = np.pad(bayer, 2, mode="edge")
        gh = np.abs(p[2:-2, 1:-3] - p[2:-2, 3:-1]) + np.abs(p[2:-2, 2:-2] - 0.5 * (p[2:-2, 0:-4] + p[2:-2, 4:]))
        gv = np.abs(p[1:-3, 2:-2] - p[3:-1, 2:-2]) + np.abs(p[2:-2, 2:-2] - 0.5 * (p[0:-4, 2:-2] + p[4:, 2:-2]))
        g_h = 0.5 * (p[2:-2, 1:-3] + p[2:-2, 3:-1])
        g_v = 0.5 * (p[1:-3, 2:-2] + p[3:-1, 2:-2])
        g_interp = np.where(gh < gv, g_h, np.where(gv < gh, g_v, 0.5 * (g_h + g_v)))
    else:
        p = np.pad(bayer, 1, mode="edge")
        g_interp = 0.25 * (p[0:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, 0:-2] + p[1:-1, 2:])
    g[0::2, 0::2] = g_interp[0::2, 0::2]
    g[1::2, 1::2] = g_interp[1::2, 1::2]

    r_diff = np.zeros_like(bayer)
    b_diff = np.zeros_like(bayer)
    r_diff[0::2, 0::2] = bayer[0::2, 0::2] - g[0::2, 0::2]
    b_diff[1::2, 1::2] = bayer[1::2, 1::2] - g[1::2, 1::2]
    k_rb = np.array([[0.25, 0.5, 0.25], [0.5, 1.0, 0.5], [0.25, 0.5, 0.25]], dtype=np.float64)
    pr = np.pad(r_diff, 1, mode="edge")
    pb = np.pad(b_diff, 1, mode="edge")
    r = np.clip(g + sum(k_rb[dy, dx] * pr[dy : dy + h, dx : dx + w] for dy in range(3) for dx in range(3)), 0.0, 16383.0)
    b = np.clip(g + sum(k_rb[dy, dx] * pb[dy : dy + h, dx : dx + w] for dy in range(3) for dx in range(3)), 0.0, 16383.0)

    rgb = np.stack([r, g, b], axis=-1) / 16383.0
    sharp = _convolve_sep_2d(rgb, dcv_k1d)
    if shadow_coring > 0.0:
        luma = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
        w_core = np.clip(luma / shadow_coring, 0.0, 1.0)[..., None]
        sharp = rgb + w_core * (sharp - rgb)
    out = np.clip(sharp, 0.0, 1.0)
    if chroma_smooth is not None:
        out = apply_chroma_smooth(out, chroma_smooth)
    return out


def measure_image_metrics(img: np.ndarray) -> dict:
    """Measure step edge overshoot/undershoot, false-color chroma on B&W stripes, and shadow noise."""
    # Row 64 across step edge x=36..68 (0.08 -> 0.88)
    luma = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]
    prof = luma[64, 36:68]
    lo, hi = 0.08, 0.88
    span = hi - lo
    overshoot = max(0.0, (float(prof.max()) - hi) / span * 100.0)
    undershoot = max(0.0, (lo - float(prof.min())) / span * 100.0)
    # Top-right B&W stripes false-color energy
    chroma_err = np.std(img[16:112, 136:248, 0] - img[16:112, 136:248, 2])
    # Bottom-right dark shadow noise std (after removing linear ramp)
    patch = luma[144:240, 144:200]
    shadow_noise = float(np.std(patch - patch.mean(axis=0, keepdims=True)))
    return {
        "edge_overshoot_pct": round(overshoot, 2),
        "edge_undershoot_pct": round(undershoot, 2),
        "bw_stripe_false_color_std": round(float(chroma_err), 5),
        "shadow_noise_std": round(shadow_noise, 5),
    }


def run_spatial_comparison(out_dir: Path) -> Path:
    """Run the 4-way offline spatial test and write PNG + JSON report."""
    from PIL import Image, ImageDraw

    out_dir.mkdir(parents=True, exist_ok=True)
    wmat, afa_smooth, afa_chroma = _load_arri_tables()
    bayer, _ = make_synthetic_bayer_chart()

    im_arri = debayer_arri_ada7_sim(bayer, wmat, afa_smooth, afa_chroma)
    im_sony_stock = debayer_sony_dcv_sim(bayer, _load_sony_dcv_center_1d(1.0), shadow_coring=0.0)
    im_sony_25 = debayer_sony_dcv_sim(bayer, _load_sony_dcv_center_1d(0.25), shadow_coring=0.08,
                                      chroma_smooth=afa_chroma, directional_g=True)
    im_sony_00 = debayer_sony_dcv_sim(bayer, _load_sony_dcv_center_1d(0.0), shadow_coring=0.08,
                                      chroma_smooth=afa_chroma, directional_g=True)

    panels = [
        ("1. ARRI (ADA7 + AFA09T)", im_arri),
        ("2. Sony Stock (DCV 100%)", im_sony_stock),
        ("3. Sony Fix (DCV 25%+DirG+Chroma)", im_sony_25),
        ("4. Sony Fix (DCV 0%+DirG+Chroma)", im_sony_00),
    ]
    report = {name: measure_image_metrics(im) for name, im in panels}
    (out_dir / "spatial_comparison_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    # Build 2x2 visual chart (560x620) with 256x256 panels + edge zoom strip
    pad = 16
    pw, ph = 256, 256
    canvas = Image.new("RGB", (pad * 3 + pw * 2, pad * 3 + ph * 2 + 80), (24, 24, 24))
    draw = ImageDraw.Draw(canvas)
    for idx, (label, arr) in enumerate(panels):
        col, row = idx % 2, idx // 2
        x0 = pad + col * (pw + pad)
        y0 = pad + 24 + row * (ph + pad + 28)
        u8 = np.clip(np.round((arr ** (1.0 / 2.2)) * 255.0), 0, 255).astype(np.uint8)
        canvas.paste(Image.fromarray(u8, mode="RGB"), (x0, y0))
        m = report[label]
        title = f"{label} | under={m['edge_undershoot_pct']:.1f}% fc={m['bw_stripe_false_color_std']:.3f}"
        draw.text((x0, y0 - 16), title, fill=(235, 235, 235))

    out_png = out_dir / "spatial_comparison.png"
    canvas.save(out_png)
    return out_png
