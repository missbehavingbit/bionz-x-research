"""Sony S-Log3 / S-Gamut3.Cine to ARRI LogC3 wide gamut and ARRI Rec.709.

The wide-gamut path uses the published EI 800 LogC3 curve and the ALEXA Wide Gamut
primaries that current tooling labels ARRI Wide Gamut 3. The Rec.709 path is a
built-in rendering: that linear AWG signal, the published AWG-to-Rec.709 matrix,
a monotone shoulder, and a highlight lift that pulls weaker channels toward the
peak. It does not embed ARRI's Classic cube.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path

import numpy as np

from .color import rgb_to_xyz_matrix, slog3_decode
from .cube import parse_cube

SONY_SOURCE = "S-Log3 / S-Gamut3.Cine"
CLASSIC_SHA256 = "29212a71808ab22f2819b904b3730fa4231b5841d883e43ec17610096dc38879"
_AWG_TO_REC709 = np.linalg.inv(rgb_to_xyz_matrix("Rec.709")) @ rgb_to_xyz_matrix("AWG3")
SONY_TO_AWG3 = np.linalg.solve(rgb_to_xyz_matrix("AWG3"), rgb_to_xyz_matrix("S-Gamut3.Cine"))
_CUT, _A, _B, _C, _D, _E, _F = 0.010591, 5.555556, 0.052272, 0.247190, 0.385537, 5.367655, 0.092809

# Scene exposure -> display code. Knots are geomspace(1e-5, 64, 24); codes are the
# built-in shoulder sampled on neutral AWG gray and rounded to 0.0001.
_SHOULDER_SCENE = np.geomspace(1e-5, 64.0, 24)
_SHOULDER_CODE = np.array([
    0.0148, 0.0148, 0.0149, 0.0149, 0.0151, 0.0155, 0.0164, 0.0182,
    0.0216, 0.0285, 0.0452, 0.0813, 0.1374, 0.2228, 0.3457, 0.4832,
    0.6190, 0.7531, 0.8705, 0.9406, 0.9778, 0.9940, 0.9993, 1.0000,
])
_HIGHLIGHT_START = 0.45
_HIGHLIGHT_AMOUNT = 0.65


def logc3_encode(linear):
    return np.where(linear > _CUT, _C * np.log10(np.maximum(_A * linear + _B, 1e-12)) + _D,
                    _E * linear + _F)


def logc3_decode(code):
    return np.where(code > _E * _CUT + _F, (10 ** ((code - _D) / _C) - _B) / _A,
                    (code - _F) / _E)


def shoulder(linear):
    """Monotone Rec.709 shoulder. Non-positive light stays at code 0."""
    linear = np.asarray(linear, float)
    curved = np.interp(np.log10(np.maximum(linear, 1e-5)), np.log10(_SHOULDER_SCENE),
                       _SHOULDER_CODE, left=_SHOULDER_CODE[0], right=1.0)
    return np.where(linear <= 0, 0.0, curved)


def compress_highlights(rgb):
    """Lift weaker channels toward the peak once the peak code passes the shoulder."""
    rgb = np.asarray(rgb, float)
    peak = np.max(rgb, axis=-1, keepdims=True)
    span = max(1e-6, 1.0 - _HIGHLIGHT_START)
    weight = np.clip((peak - _HIGHLIGHT_START) / span, 0, 1) ** 2
    scale = 1 - _HIGHLIGHT_AMOUNT * weight
    return np.clip(peak - (peak - rgb) * scale, 0, 1)


def classic_rec709(logc_codes):
    """LogC3 / AWG3 code values to Gamma 2.4 Rec.709 code values."""
    awg = logc3_decode(np.clip(logc_codes, 0, 1))
    rec709 = np.maximum(awg @ _AWG_TO_REC709.T, 0)
    return compress_highlights(np.clip(shoulder(rec709), 0, 1))


@dataclass(frozen=True)
class ArriReference:
    kind: str = "wide-gamut"
    classic: object = None

    @property
    def output_space(self):
        return "logc3-awg3" if self.kind == "wide-gamut" else "gamma24-rec709"

    @property
    def video_range(self):
        return "full" if self.kind == "wide-gamut" else "legal"

    @property
    def interpolation(self):
        return "trilinear"

    @property
    def edge_compression(self):
        return 0.12 if self.kind == "wide-gamut" else 0.0

    def sample(self, sony_codes):
        return self.sample_linear(slog3_decode(sony_codes))

    def sample_linear(self, rgb):
        """Map calibrated, scene-linear S-Gamut3.Cine RGB to the selected ARRI output."""
        awg = np.asarray(rgb, dtype=float) @ SONY_TO_AWG3.T
        logc = np.clip(logc3_encode(awg), 0, 1)
        if self.kind == "wide-gamut":
            return logc
        if self.classic is not None:
            return self.classic.sample(logc)
        return classic_rec709(logc)

    @classmethod
    def rec709(cls):
        return cls(kind="rec709")

    @classmethod
    def from_classic(cls, path):
        data = Path(path).read_bytes()
        if hashlib.sha256(data).hexdigest() != CLASSIC_SHA256:
            raise ValueError("expected the official ARRI LogC3 Classic 33-point LUT with the documented SHA-256")
        return cls(kind="rec709", classic=parse_cube(data.decode("utf-8-sig")))
