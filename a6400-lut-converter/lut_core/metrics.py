from __future__ import annotations
import numpy as np
from .color import (display_rgb_to_lab, delta_e76, COLORCHECKER_SRGB, srgb_eotf,
                    lin709_to_source, typical_mask)
from .alexa35 import Alexa35Reference
from .color import delta_e00
from .model import apply_ycc, encode_8bit, simulate_8bit, ycc_to_rgb


def _stats(de):
    return {"count": int(de.size), "mean": float(de.mean()), "p95": float(np.percentile(de, 95)),
            "max": float(de.max())}


def validation_samples(source):
    gray = np.repeat(np.r_[0, np.geomspace(0.00001, 16, 511)][:, None], 3, axis=1)
    skin = np.array([[0.17, 0.085, 0.056], [0.33, 0.18, 0.12], [0.49, 0.32, 0.23],
                     [0.077, 0.037, 0.025], [0.63, 0.38, 0.28]])
    skin = (skin[:, None, :] * np.geomspace(0.3, 3.6, 9)[None, :, None]).reshape(-1, 3)
    hues = np.array([[1, 0.04, 0.01], [1, 0.6, 0.02], [0.03, 1, 0.02],
                     [0.01, 0.8, 1], [0.02, 0.05, 1], [1, 0.02, 0.53]])
    highlights = (hues[:, None, :] * np.geomspace(1.1, 15, 8)[None, :, None]).reshape(-1, 3)
    return {name: lin709_to_source(rgb, source) for name, rgb in {
        "colorchecker": srgb_eotf(COLORCHECKER_SRGB), "gray_gradient": gray,
        "skin_tones": skin, "saturated_highlights": highlights}.items()}


def evaluate(cube, preset, profile, source, grid=33, video_range="legal",
             interpolation="trilinear"):
    """Compare solid patches in the reference space or the shared Alexa 35 display view."""
    ax = np.linspace(0, 1, grid)
    u = np.stack(np.meshgrid(ax, ax, ax, indexing="ij"), -1).reshape(-1, 3)

    alexa = isinstance(cube, Alexa35Reference)

    def errors(samples):
        ref = np.clip(cube.sample(samples), 0, 1)
        ycc = apply_ycc(preset, profile, samples, interpolation)
        app = simulate_8bit(ycc, profile, video_range)
        if alexa:
            return delta_e00(display_rgb_to_lab(cube.display(ref), "gamma24-rec709"),
                             display_rgb_to_lab(cube.display(app), "gamma24-rec709"))
        return delta_e76(display_rgb_to_lab(ref, profile.output_space),
                         display_rgb_to_lab(app, profile.output_space))

    de_grid = errors(u)
    tm = typical_mask(u, source)
    samples = validation_samples(source)
    suites = {name: errors(values) for name, values in samples.items()}
    diagnostics = {}
    if alexa:
        ycc = apply_ycc(preset, profile, np.concatenate(list(samples.values())), interpolation)
        rgb = ycc_to_rgb(ycc, profile)
        skin = samples["skin_tones"]
        skin_ref = display_rgb_to_lab(cube.display(cube.sample(skin)), "gamma24-rec709")
        skin_sim = display_rgb_to_lab(cube.display(simulate_8bit(
            apply_ycc(preset, profile, skin, interpolation), profile, video_range)), "gamma24-rec709")
        hue_ref = np.degrees(np.arctan2(skin_ref[:, 2], skin_ref[:, 1]))
        hue_sim = np.degrees(np.arctan2(skin_sim[:, 2], skin_sim[:, 1]))
        hue = np.abs((hue_sim - hue_ref + 180) % 360 - 180)
        gray = samples["gray_gradient"]
        gray_ycc = apply_ycc(preset, profile, gray, interpolation)
        gray_rgb = simulate_8bit(gray_ycc, profile, video_range)
        gray_lab = display_rgb_to_lab(cube.display(gray_rgb), "gamma24-rec709")
        gray_y, _ = encode_8bit(gray_ycc, profile, video_range)
        diagnostics = {"rgb_out_of_range_pct": float(100 * np.any((rgb < -1e-12) | (rgb > 1 + 1e-12), axis=-1).mean()),
                       "gray_repeated_luma_steps": int(np.count_nonzero(np.diff(gray_y) == 0)),
                       "gray_distinct_luma_codes": int(np.unique(gray_y).size),
                       "skin_hue_error_degrees": _stats(hue),
                       "gray_chroma_cielab": _stats(np.hypot(gray_lab[..., 1], gray_lab[..., 2]))}
        if profile.gamma_source_codes:
            low, high = np.asarray(profile.gamma_source_codes)[[0, -1]] / 1023
            chart_codes = np.concatenate(list(samples.values()))
            diagnostics["chart_below_stock_extent_pct"] = float(100 * np.any(chart_codes < low, axis=-1).mean())
            diagnostics["chart_above_stock_extent_pct"] = float(100 * np.any(chart_codes > high, axis=-1).mean())
    return {**diagnostics, "typical": _stats(de_grid[tm]) if tm.any() else None,
            "typical_fraction": float(tm.mean()), "grid": _stats(de_grid),
            **{name: _stats(de) for name, de in suites.items()},
            "validation_combined": _stats(np.concatenate(list(suites.values()))),
            "colorchecker_per_patch": suites["colorchecker"].tolist(),
            "output_space": profile.output_space, "video_range": video_range,
            "interpolation": interpolation, "metric": ("CIEDE2000 after Rec.709 view" if alexa else "CIE76 (D65)") + ", 8-bit solid patches"}
