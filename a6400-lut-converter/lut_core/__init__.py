"""lut_core: OS-agnostic .cube -> 3-layer ISP converter (numpy only)."""
from dataclasses import dataclass, replace

import numpy as np
from .cube import Cube, load_cube, parse_cube, cube_to_text
from .profile import Profile
from .model import Preset, apply_preset
from .alexa35 import Alexa35Reference
from .movie_gamma import METADATA
from .decompose import decompose
from .metrics import evaluate
from .color import SOURCES
from . import pack

__all__ = ["Cube", "load_cube", "parse_cube", "cube_to_text", "Profile", "Preset", "apply_preset",
           "decompose", "evaluate", "SOURCES", "pack", "convert", "ConversionResult"]


@dataclass
class ConversionResult:
    preset: Preset
    report: dict
    metrics: dict
    warnings: list


def convert(cube, profile, source, clamp_warn_pct=5.0, de_warn=5.0, *,
            video_range="legal", interpolation="trilinear", edge_compression=0.0):
    profile.validate()
    preset, rep = decompose(cube, profile, source, edge_compression, interpolation)
    met = evaluate(cube, preset, profile, source, video_range=video_range, interpolation=interpolation)
    if isinstance(cube, Alexa35Reference):
        base = replace(preset, delta=np.zeros_like(preset.delta))
        met["gamma_matrix_only"] = evaluate(cube, base, profile, source, video_range=video_range,
                                             interpolation=interpolation)
        rep["gamma_model"] = "movie-record-31" if profile.gamma_positions else "uniform"
        if profile.gamma_source_codes:
            rep["movie_gamma_payload"] = {"format": "105 little-endian uint32", "positions": profile.gamma_positions,
                                          "metadata": METADATA}
            rep["stock_source_10bit_extent"] = [profile.gamma_source_codes[0], profile.gamma_source_codes[-1]]
    w = []
    if rep["clamped_nodes_pct"] > clamp_warn_pct:
        w.append(f"{rep['clamped_nodes_pct']:.1f}% of 3D-LUT nodes hit the delta clamp {profile.delta_range}")
    if (met["typical"] and met["typical"]["mean"] > de_warn) or met["colorchecker"]["mean"] > de_warn:
        w.append("approximation error is high; the .cube is probably outside what the 3 layers can express")
    if not profile.permutation_verified:
        w.append("eSRAM permutation table is NOT verified for this profile; eSRAM/preset ordering may be wrong")
    return ConversionResult(preset, rep, met, w)
