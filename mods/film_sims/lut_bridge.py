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


def fit_cube_to_isp(
    cube: Cube,
    stock: dict[str, list[int]],
    name: str | None = None,
    edge_compression: float = 0.0,
) -> FittedISPTables:
    """Fit a Cube into EC/FC gamma, A0/A1/B2 matrices, and 1215-byte RefAdr[0]."""
    label = name or cube.title or "Custom LUT"
    preset, decomp_report = decompose(
        cube,
        a6400_profile,
        source=None,
        edge_compression=edge_compression,
        interpolation="trilinear",
    )
    ec_sim, fc_sim = _apply_fitted_gamma_to_stock(stock, preset.gamma)
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
