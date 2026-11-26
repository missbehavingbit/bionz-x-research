"""Hardware-constraint container. lut_core never hardcodes camera constants;
everything comes from a Profile (see lut_profiles/)."""
from __future__ import annotations
import json
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Profile:
    name: str
    gamma_size: int                 # entries in the 1D EC_Gamma table
    gamma_max_val: int              # output full-scale of the gamma table (<= 65535)
    matrix_q: int                   # fixed-point 1.0 of the 3x3 matrix (1024 = Q10)
    y_axis: tuple                   # 3D-LUT Y grid nodes, e.g. (0,64,128,192,256)
    c_axis: tuple                   # 3D-LUT Cb/Cr grid nodes, e.g. (-128,...,128)
    delta_range: tuple              # (lo, hi) signed residual range, e.g. (-64, 63)
    permutation: tuple              # eSRAM slot s holds natural node permutation[s]
    permutation_verified: bool      # False => hardware ordering not confirmed
    ycc_kr_kb: tuple                # luma coefficients (Kr, Kb), BT.709 = (0.2126, 0.0722)
    output_space: str = "srgb"      # display space used for Delta-E

    gamma_positions: tuple = ()
    gamma_source_codes: tuple = ()

    @property
    def grid(self):
        return (len(self.y_axis), len(self.c_axis), len(self.c_axis))

    @property
    def n_nodes(self):
        a, b, c = self.grid
        return a * b * c

    @property
    def y_scale(self):
        return float(self.y_axis[-1])

    def validate(self):
        n = self.n_nodes
        if sorted(self.permutation) != list(range(n)):
            raise ValueError(f"permutation must be a permutation of 0..{n - 1}")
        if not (0 < self.gamma_max_val <= 0xFFFF):
            raise ValueError("gamma_max_val must fit uint16")
        if self.gamma_size < 2 or self.matrix_q < 1:
            raise ValueError("bad gamma_size / matrix_q")
        if self.gamma_positions or self.gamma_source_codes:
            if (len(self.gamma_positions) != self.gamma_size
                    or len(self.gamma_source_codes) != self.gamma_size or self.gamma_max_val != 1023
                    or any(a >= b for a, b in zip(self.gamma_positions, self.gamma_positions[1:]))
                    or any(a > b for a, b in zip(self.gamma_source_codes, self.gamma_source_codes[1:]))
                    or not 0 <= self.gamma_source_codes[0] < self.gamma_source_codes[-1] <= 1023):
                raise ValueError("invalid Movie gamma coordinates")
        if not (-64 <= self.delta_range[0] < 0 < self.delta_range[1] <= 63):
            raise ValueError("delta_range must fit signed 7-bit and bracket zero")
        for axis in (self.y_axis, self.c_axis):
            if len(axis) < 2 or any(a >= b for a, b in zip(axis, axis[1:])):
                raise ValueError("LUT axes must be strictly increasing")
        if self.y_scale <= 0:
            raise ValueError("Y full scale must be positive")
        return self

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        for k in ("y_axis", "c_axis", "delta_range", "permutation", "ycc_kr_kb"):
            d[k] = tuple(d[k])
        for k in ("gamma_positions", "gamma_source_codes"):
            if k in d:
                d[k] = tuple(d[k])
        return cls(**d).validate()

    @classmethod
    def from_json(cls, path):
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def to_json(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=1)
