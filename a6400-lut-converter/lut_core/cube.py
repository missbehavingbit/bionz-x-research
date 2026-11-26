from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .interp import trilinear


@dataclass
class Cube:
    size: int
    domain_min: np.ndarray
    domain_max: np.ndarray
    table: np.ndarray            # [r, g, b, 3]
    title: str = ""

    def sample_unit(self, u):
        """u: input already normalised to the cube domain, shape (...,3)."""
        return trilinear(self.table, np.asarray(u, float) * (self.size - 1))

    def sample(self, raw):
        u = (np.asarray(raw, float) - self.domain_min) / (self.domain_max - self.domain_min)
        return self.sample_unit(u)


def parse_cube(text: str) -> Cube:
    size = None
    dmin = np.zeros(3)
    dmax = np.ones(3)
    title = ""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        key = parts[0].upper()
        if key == "TITLE":
            title = line[5:].strip().strip('"')
        elif key == "LUT_3D_SIZE":
            size = int(parts[1])
        elif key == "LUT_1D_SIZE":
            raise ValueError("1D .cube LUTs are not supported (need LUT_3D_SIZE)")
        elif key == "LUT_3D_INPUT_RANGE":
            low, high = map(float, parts[1:])
            dmin, dmax = np.full(3, low), np.full(3, high)
        elif key == "DOMAIN_MIN":
            dmin = np.array([float(x) for x in parts[1:4]])
        elif key == "DOMAIN_MAX":
            dmax = np.array([float(x) for x in parts[1:4]])
        elif key.startswith("LUT_") or key.startswith("DOMAIN_"):
            continue
        else:
            rows.append([float(x) for x in parts[:3]])
    if size is None or size < 2:
        raise ValueError("LUT_3D_SIZE must be at least 2")
    arr = np.asarray(rows, dtype=np.float64)
    if arr.shape != (size ** 3, 3):
        raise ValueError(f"expected {size ** 3} rows, got {arr.shape[0]}")
    if not np.isfinite(arr).all() or not np.isfinite([dmin, dmax]).all() or np.any(dmax <= dmin):
        raise ValueError("cube data and domain must be finite, with a positive domain span")
    # .cube data order: red fastest -> reshape gives [b, g, r]
    table = arr.reshape(size, size, size, 3).transpose(2, 1, 0, 3).copy()
    return Cube(size, dmin, dmax, table, title)


def load_cube(path) -> Cube:
    with open(path, "r", encoding="utf-8-sig") as f:
        return parse_cube(f.read())


def cube_to_text(cube: Cube) -> str:
    out = [f'TITLE "{cube.title}"', f"LUT_3D_SIZE {cube.size}",
           "DOMAIN_MIN " + " ".join(f"{v:g}" for v in cube.domain_min),
           "DOMAIN_MAX " + " ".join(f"{v:g}" for v in cube.domain_max)]
    flat = cube.table.transpose(2, 1, 0, 3).reshape(-1, 3)
    out += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in flat]
    return "\n".join(out) + "\n"
