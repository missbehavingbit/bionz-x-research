"""Bridge between a6400-lut-converter (3D LUT fitter) and mods/film_sims.

Fits any 3D .cube LUT or nonlinear 33^3 FilmRecipe3D onto the three-stage
BIONZ X color pipeline (96-knot EC/FC gamma, row-normalized Q10 3x3 matrix,
and full 5x9x9 RefAdr[0] 3-plane dY/dCb/dCr residual LUT).
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

from lut_core.color import delta_e76, display_rgb_to_lab  # noqa: E402
from lut_core.cube import Cube, load_cube  # noqa: E402
from lut_core.decompose import decompose  # noqa: E402
from lut_core.model import apply_ycc, simulate_8bit  # noqa: E402
from lut_core.pack import pack_refadr  # noqa: E402
from lut_profiles.a6400 import a6400_profile  # noqa: E402


@dataclass(frozen=True)
class FittedISPTables:
    name: str
    ec_sim: list[int]
    fc_sim: list[int]
    a0_sim: list[int]
    a1_sim: list[int]
    b2_sim: list[int]
    refadr_bytes: bytes
    report: dict[str, Any]


def _compose_stock_matrix(stock9: list[int], fit_matrix_3x3: np.ndarray) -> list[int]:
    """Compose fitted relative Q10 matrix onto stock 3x3 while preserving exact row sums."""
    s = np.asarray(stock9, dtype=np.float64).reshape(3, 3)
    m_rel = np.asarray(fit_matrix_3x3, dtype=np.float64) / float(a6400_profile.matrix_q)
    composed = m_rel @ s
    out9: list[int] = []
    for r in range(3):
        target_sum = int(sum(stock9[r * 3 : r * 3 + 3]))
        row = [int(round(float(v))) for v in composed[r]]
        row[r] -= sum(row) - target_sum
        out9.extend(row)
    return out9


def _apply_fitted_gamma_to_stock(
    stock: dict[str, list[int]], fitted_gamma_u16: np.ndarray
) -> tuple[list[int], list[int]]:
    """Map stock EC_REC28_V1 (10-bit << 6) and FC_TBL0 (10-bit) through fitted 96-knot curve."""
    g_norm = np.asarray(fitted_gamma_u16, dtype=np.float64) / float(a6400_profile.gamma_max_val)
    x_grid = np.linspace(0.0, 1.0, len(g_norm), dtype=np.float64)

    fc_in = np.asarray(stock["fc_stock"], dtype=np.float64) / 1023.0
    fc_mapped = np.clip(np.rint(np.interp(fc_in, x_grid, g_norm) * 1023.0), 0, 1023).astype(np.int64)
    fc_mono = np.maximum.accumulate(fc_mapped).tolist()

    ec_in = np.asarray([v >> 6 for v in stock["ec_stock"]], dtype=np.float64) / 1023.0
    ec_mapped = np.clip(np.rint(np.interp(ec_in, x_grid, g_norm) * 1023.0), 0, 1023).astype(np.int64)
    ec_mono = [(int(v) << 6) for v in np.maximum.accumulate(ec_mapped)]
    return ec_mono, fc_mono


def apply_movie_gamma(fitted_gamma_u16: np.ndarray, exponents: tuple[int, ...] | None = None) -> list[int]:
    """Evaluate a fitted 96-knot display-referred curve directly at linear_to_srgb(knot / 8191)."""
    g_norm = np.asarray(fitted_gamma_u16, dtype=np.float64) / float(a6400_profile.gamma_max_val)
    x_grid = np.linspace(0.0, 1.0, len(g_norm), dtype=np.float64)
    knots = _ec_knot_positions(exponents or RECORD31_EXPONENTS)
    srgb_in = np.array([_linear_to_srgb(k / 8191.0) for k in knots], dtype=np.float64)
    ec_mapped = np.clip(np.rint(np.interp(srgb_in, x_grid, g_norm) * 1023.0), 0, 1023).astype(np.int64)
    return [(int(v) << 6) for v in np.maximum.accumulate(ec_mapped)]


RECORD31_EXPONENTS = (3, 3, 4, 4, 3, 3, 3, 4, 3)
_EC_STARTS = (0, 32, 64, 128, 256, 512, 1024, 2048, 4096)
_EC_EXTENTS = (32, 32, 64, 128, 256, 512, 1024, 2048, 4096)


def _ec_knot_positions(exponents: tuple[int, ...]) -> list[int]:
    """Right endpoints of the 96 EC knots, mirroring bionz_core::ec_knot_positions."""
    out: list[int] = []
    for seg, e in enumerate(exponents):
        step = _EC_EXTENTS[seg] >> e
        for k in range(1 << e):
            out.append(min(8191, _EC_STARTS[seg] + step * (k + 1)))
    if len(out) != 96:
        raise ValueError(f"EC exponents give {len(out)} knots")
    return out


def _linear_to_srgb(x: float) -> float:
    x = min(1.0, max(0.0, x))
    if x <= 0.0031308:
        return 12.92 * x
    return 1.055 * (x ** (1.0 / 2.4)) - 0.055


def movie_domain_cube(cube: Cube, stock_ec: list[int]) -> Cube:
    """Resample a display sRGB cube into the uniform EC-index domain for Movie record-31.

    The Movie chain looks the EC table up at record-31 knot positions (`linear` scene input).
    Invert `stock_ec` back to linear `x`, convert `linear_to_srgb(x)` for the display-referred
    cube, and sample the cube there so midtones are not crushed into shadow codes.
    """
    knots = _ec_knot_positions(RECORD31_EXPONENTS)
    value = [min(1.0, max(0.0, (int(v) >> 6) / 1023.0)) for v in stock_ec[:96]]
    for i in range(1, 96):
        value[i] = max(value[i], value[i - 1])
    xpos = [k / 8191.0 for k in knots]

    def to_input(u: float) -> float:
        u = min(1.0, max(0.0, u))
        if u <= value[0]:
            return xpos[0]
        if u >= value[95]:
            return xpos[95]
        i = 0
        while i + 2 < 96 and value[i + 1] <= u:
            i += 1
        j = i + 1
        while j < 96 and value[j] == value[i]:
            j += 1
        if j >= 96:
            return xpos[95]
        t = (u - value[i]) / (value[j] - value[i])
        return xpos[i] + t * (xpos[j] - xpos[i])

    n = cube.size
    ax = np.linspace(0.0, 1.0, n)
    grid = np.stack(np.meshgrid(ax, ax, ax, indexing="ij"), -1).reshape(-1, 3)
    mapped_input = np.array([[_linear_to_srgb(to_input(v)) for v in row] for row in grid], dtype=np.float64)
    table = np.clip(cube.sample(mapped_input), 0.0, 1.0).reshape(n, n, n, 3)
    return Cube(size=n, domain_min=cube.domain_min.copy(), domain_max=cube.domain_max.copy(), table=table, title=f"{cube.title} (movie)")


def fit_cube_to_isp(
    cube: Cube,
    stock: dict[str, list[int]],
    name: str | None = None,
    edge_compression: float = 0.0,
    movie_ec: bool = False,
) -> FittedISPTables:
    """Fit a Cube into EC/FC gamma, A0/A1/B2 matrices, and 1215-byte RefAdr[0]."""
    preset = None if edge_compression != 0.0 else _rust_preset(cube)
    return _fit_cube_python(
        cube,
        stock,
        name=name,
        edge_compression=edge_compression,
        preset=preset,
        movie_ec=movie_ec,
    )


def _rust_preset(cube: Cube):
    """Return a Preset from bionz_isp when that module is installed."""
    try:
        import bionz_isp
        from lut_core.cube import cube_to_text
        from lut_core.model import Preset
    except (ImportError, ModuleNotFoundError):
        return None
    data = bionz_isp.fit_cube(cube_to_text(cube))
    if not isinstance(data, dict):
        return None
    grid = tuple(a6400_profile.grid)
    delta = np.asarray(data["delta"], dtype=np.int8)
    if delta.size != int(np.prod(grid) * 3):
        return None
    return Preset(
        np.asarray(data["gamma"], dtype=np.uint16),
        np.asarray(data["matrix"], dtype=np.int16).reshape(3, 3),
        delta.reshape(*grid, 3),
    )


def _fit_cube_python(
    cube: Cube,
    stock: dict[str, list[int]],
    name: str | None = None,
    edge_compression: float = 0.0,
    preset=None,
    movie_ec: bool = False,
) -> FittedISPTables:
    label = name or cube.title or "Custom LUT"
    if preset is None:
        preset, decomp_report = decompose(
            cube,
            a6400_profile,
            source=None,
            edge_compression=edge_compression,
            interpolation="trilinear",
        )
        source = "python"
    else:
        lo, hi = a6400_profile.delta_range
        shaped = np.asarray(preset.delta)
        decomp_report = {
            "clamped_nodes": 0,
            "at_rail_nodes": int(((shaped == lo) | (shaped == hi)).any(axis=-1).sum()),
        }
        source = "bionz_isp"
    ec_sim, fc_sim = _apply_fitted_gamma_to_stock(stock, preset.gamma)
    if movie_ec:
        ec_sim = apply_movie_gamma(preset.gamma)
    a0_sim = _compose_stock_matrix(stock["a0_stock"], preset.matrix)
    a1_sim = _compose_stock_matrix(stock["a1_stock"], preset.matrix)
    b2_sim = _compose_stock_matrix(stock["b2_stock"], preset.matrix)
    refadr_bytes = pack_refadr(preset)

    ax = np.linspace(0.0, 1.0, 17, dtype=np.float64)
    u = np.stack(np.meshgrid(ax, ax, ax, indexing="ij"), -1).reshape(-1, 3)
    ref = np.clip(cube.sample(u), 0.0, 1.0)
    app = simulate_8bit(apply_ycc(preset, a6400_profile, u, "trilinear"), a6400_profile, "full")
    de = delta_e76(
        display_rgb_to_lab(ref, a6400_profile.output_space),
        display_rgb_to_lab(app, a6400_profile.output_space),
    )
    delta = np.asarray(preset.delta, dtype=np.int8).reshape(-1, 3)
    report: dict[str, Any] = {
        "name": label,
        "mean_de76": float(de.mean()),
        "p95_de76": float(np.percentile(de, 95)),
        "max_de76": float(de.max()),
        "nonzero_dy_nodes": int(np.count_nonzero(delta[:, 0])),
        "nonzero_chroma_nodes": int(np.count_nonzero((delta[:, 1] != 0) | (delta[:, 2] != 0))),
        "clamped_nodes": int(decomp_report["clamped_nodes"]),
        "at_rail_nodes": int(decomp_report["at_rail_nodes"]),
        "source": source,
    }
    return FittedISPTables(
        name=label,
        ec_sim=ec_sim,
        fc_sim=fc_sim,
        a0_sim=a0_sim,
        a1_sim=a1_sim,
        b2_sim=b2_sim,
        refadr_bytes=refadr_bytes,
        report=report,
    )


def fit_cube_file_to_isp(
    cube_path: Path,
    stock: dict[str, list[int]],
    name: str | None = None,
    edge_compression: float = 0.0,
) -> FittedISPTables:
    """Load a .cube file and fit it onto the BIONZ X ISP tables."""
    cube = load_cube(cube_path)
    return fit_cube_to_isp(
        cube,
        stock,
        name=name or cube.title or cube_path.stem,
        edge_compression=edge_compression,
    )
