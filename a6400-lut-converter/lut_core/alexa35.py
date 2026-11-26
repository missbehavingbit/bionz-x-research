"""ALEXA 35 color-space targets for a calibrated Sony input."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import numpy as np

from .arri import ArriReference
from .color import rgb_to_xyz_matrix
from .cube import parse_cube

# ARRI LogC4 specification, equations 2, 3 and 5a.
AWG4_TO_XYZ = np.array([
    [0.704858320407232064, 0.129760295170463003, 0.115837311473976537],
    [0.254524176404027025, 0.781477732712002049, -0.036001909116029039],
    [0.0, 0.0, 1.089057750759878429],
])
SONY_TO_AWG4 = np.linalg.solve(AWG4_TO_XYZ, rgb_to_xyz_matrix("S-Gamut3.Cine"))
OFFICIAL_REC709_SHA256 = frozenset((
    "ab9618bc72dc3fab3e1d23a0c25ffd9cfb0491ed715448a629e44939cbd3f4b4",
    "a498fc5190341e4a573308135a28ebff2db6844e652b4704371511954c6d4622",
))
_A = (2**18 - 16) / 117.45
_B = 928 / 1023
_C = 95 / 1023
_T = (2 ** (6 - 14 * _C / _B) - 64) / _A
_S = 7 * np.log(2) * 2 ** (7 - 14 * _C / _B) / (_A * _B)


def logc4_encode(linear):
    x = np.asarray(linear, dtype=float)
    return np.where(x < _T, (x - _T) / _S,
                    (np.log2(_A * np.maximum(x, _T) + 64) - 6) * _B / 14 + _C)


def logc4_decode(code):
    x = np.asarray(code, dtype=float)
    return np.where(x < 0, x * _S + _T, (np.exp2(14 * (x - _C) / _B + 6) - 64) / _A)


@dataclass(frozen=True)
class Alexa35Reference(ArriReference):
    kind: str = "logc4"
    display_lut: object = None
    lut_name: str = ""
    lut_sha256: str = ""

    @property
    def output_space(self):
        return "logc4-awg4" if self.kind == "logc4" else "gamma24-rec709"

    @property
    def video_range(self):
        return "full" if self.kind == "logc4" else "legal"

    @property
    def edge_compression(self):
        return 0.0

    def sample_linear(self, rgb):
        log = np.clip(logc4_encode(np.asarray(rgb, dtype=float) @ SONY_TO_AWG4.T), 0, 1)
        return self.display_lut.sample(log) if self.kind == "alexa35-rec709" else log

    def display(self, codes):
        return np.clip(self.display_lut.sample(codes) if self.kind == "logc4" else codes, 0, 1)

    def provenance(self):
        return {"model": self.kind, "display_lut": self.lut_name, "display_lut_sha256": self.lut_sha256,
                "display_assumption": "Rec.709 Gamma 2.4, full-range RGB LUT domain"}

    @classmethod
    def from_lut(cls, path, kind="logc4"):
        path = Path(path)
        data = path.read_bytes()
        digest = sha256(data).hexdigest()
        if digest not in OFFICIAL_REC709_SHA256:
            raise ValueError("Expected the official ARRI LogC4-to-Gamma24_Rec709-D65 v1 33- or 65-point LUT")
        cube = parse_cube(data.decode("utf-8-sig"))
        if (not np.isfinite(cube.table).all() or not np.allclose(cube.domain_min, 0)
                or not np.allclose(cube.domain_max, 1)):
            raise ValueError("Expected a finite 3D Rec.709 LUT with normalized full-range LogC4 input")
        return cls(kind=kind, display_lut=cube, lut_name=path.name, lut_sha256=digest)
