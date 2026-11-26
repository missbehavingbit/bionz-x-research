"""Forward model of the 3-layer ISP chain (also used for simulation/preview):
   rgb(log code, 0..1) -> integer 1D gamma -> Q10 int16 matrix
   -> BT.709 YCbCr -> trilinear int8 residual -> YCbCr->RGB
Gamma and the matrix stay in integer codes. YCbCr is the exact conversion of those
codes into Y [0, 256] and Cb/Cr [-128, 128]. The 5x9x9 blend uses the stored int8 nodes.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .interp import tetrahedral, trilinear
from .movie_gamma import gamma_segment_positions


@dataclass
class Preset:
    gamma: np.ndarray     # uint16 [gamma_size]
    matrix: np.ndarray    # int16  [3,3]
    delta: np.ndarray     # int8   [ny,ncb,ncr,3]  (dY,dCb,dCr)


def rgb_to_ycc(rgb, profile):
    kr, kb = profile.ycc_kr_kb
    kg = 1 - kr - kb
    s = profile.y_scale
    rgb = np.asarray(rgb, float)
    yn = kr * rgb[..., 0] + kg * rgb[..., 1] + kb * rgb[..., 2]
    cb = (rgb[..., 2] - yn) / (2 * (1 - kb))
    cr = (rgb[..., 0] - yn) / (2 * (1 - kr))
    return np.stack([yn * s, cb * s, cr * s], -1)


def ycc_to_rgb(ycc, profile):
    kr, kb = profile.ycc_kr_kb
    kg = 1 - kr - kb
    s = profile.y_scale
    ycc = np.asarray(ycc, float)
    yn, cb, cr = ycc[..., 0] / s, ycc[..., 1] / s, ycc[..., 2] / s
    b = yn + 2 * (1 - kb) * cb
    r = yn + 2 * (1 - kr) * cr
    g = (yn - kr * r - kb * b) / kg
    return np.stack([r, g, b], -1)


def round_code(x):
    """Round to nearest, with ties toward positive infinity (model assumption)."""
    return np.floor(x + 0.5)


def gamma_codes(preset, profile, u):
    """Piecewise-linear lookup of the 96-entry uint16 table.

    Knots follow the selected profile. The segment fraction is Q16, and the output is
    rounded to the table's integer scale with ties toward positive infinity.
    """
    u = np.clip(np.asarray(u, float), 0.0, 1.0)
    span = profile.gamma_size - 1
    pos = gamma_segment_positions(profile, u)
    i0 = np.minimum(np.floor(pos).astype(np.int64), span - 1)
    quant = 1 << 16
    frac = np.rint((pos - i0) * quant).astype(np.int64)
    low = preset.gamma[i0].astype(np.int64)
    high = preset.gamma[i0 + 1].astype(np.int64)
    return (low * (quant - frac) + high * frac + quant // 2) // quant


def matrix_codes(preset, profile, u):
    """Q10 matrix on integer gamma codes: (R*m0 + G*m1 + B*m2 + q/2) // q, then clamp."""
    rgb = gamma_codes(preset, profile, u)
    acc = np.asarray(rgb, np.int64) @ preset.matrix.astype(np.int64).T
    codes = (acc + profile.matrix_q // 2) // profile.matrix_q
    return np.clip(codes, 0, profile.gamma_max_val)


def matrix_stage(preset, profile, u):
    return matrix_codes(preset, profile, u) / profile.gamma_max_val


def lut_indices(ycc, profile):
    ya, ca = profile.y_axis, profile.c_axis
    return np.stack([np.interp(ycc[..., 0], ya, np.arange(len(ya))),
                     np.interp(ycc[..., 1], ca, np.arange(len(ca))),
                     np.interp(ycc[..., 2], ca, np.arange(len(ca)))], -1)


def apply_ycc(preset, profile, u, interpolation="trilinear"):
    # Layer 2 emits integer RGB. YCbCr below is that integer signal in LUT units.
    mid = matrix_codes(preset, profile, u) / profile.gamma_max_val
    ycc = rgb_to_ycc(mid, profile)
    interpolate = {"trilinear": trilinear, "tetrahedral": tetrahedral}[interpolation]
    delta = np.clip(preset.delta, *profile.delta_range).astype(np.float64)
    return ycc + interpolate(delta, lut_indices(ycc, profile))


def apply_preset(preset, profile, u, interpolation="trilinear"):
    """Pre-encoder RGB, before clipping or 8-bit quantization."""
    return ycc_to_rgb(apply_ycc(preset, profile, u, interpolation), profile)


def encode_8bit(ycc, profile, video_range="legal", chroma_420=False):
    """Return Y and centered-chroma code planes. 4:2:0 uses a 2x2 box with edge padding."""
    if video_range == "legal":
        y_gain, c_gain, offset = 219, 224, 16
        y_bounds, c_bounds = (16, 235), (16, 240)
    elif video_range == "full":
        y_gain, c_gain, offset = 255, 255, 0
        y_bounds = c_bounds = (0, 255)
    else:
        raise ValueError(f"unsupported video range {video_range!r}")
    y = ycc[..., 0]
    c = ycc[..., 1:]
    if chroma_420:
        h, w, _ = c.shape
        c = np.pad(c, ((0, h % 2), (0, w % 2), (0, 0)), mode="edge")
        c = c.reshape((h + 1) // 2, 2, (w + 1) // 2, 2, 2).mean(axis=(1, 3))
    scale = profile.y_scale
    return (np.clip(round_code(y * y_gain / scale + offset), *y_bounds).astype(np.uint8),
            np.clip(round_code(c * c_gain / scale + 128), *c_bounds).astype(np.uint8))


def simulate_8bit(ycc, profile, video_range="legal", chroma_420=False):
    y, c = encode_8bit(ycc, profile, video_range, chroma_420)
    if chroma_420:
        h, w = y.shape
        c = c.repeat(2, axis=0).repeat(2, axis=1)[:h, :w]
    y_gain, c_gain, offset = (219, 224, 16) if video_range == "legal" else (255, 255, 0)
    normalized = np.concatenate([((y.astype(float) - offset) / y_gain)[..., None],
                                 (c.astype(float) - 128) / c_gain], axis=-1)
    return np.clip(ycc_to_rgb(normalized * profile.y_scale, profile), 0, 1)
