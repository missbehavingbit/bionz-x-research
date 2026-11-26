from __future__ import annotations
import numpy as np
from .alexa35 import Alexa35Reference
from .color import delta_e00
from .arri import classic_rec709
from .color import (
    SOURCES, delta_e76, display_rgb_to_lab, encode_source_linear, lin709_to_gamut_matrix,
    lin709_to_source, source_to_lin709, srgb_eotf, srgb_oetf, xyz_to_rgb,
)
from .interp import tetrahedral, trilinear
from .model import lut_indices, matrix_stage, rgb_to_ycc, simulate_8bit


def _match_encoder(rgb, profile, video_range):
    """Run reference RGB through the same 8-bit 4:2:0 encoder as the simulation."""
    return simulate_8bit(rgb_to_ycc(np.clip(rgb, 0, 1), profile), profile, video_range, chroma_420=True)


def gamut_linear(img01, source, encoding, ev=0.0, exposure_scale=1.0):
    """Exposed linear RGB in the source gamut, before the log encode.

    RAW input is CIE XYZ. Exposure, including the JPEG midtone scale, is applied
    after the published Sony matrix and before the hard clip.
    """
    gain = 2.0 ** float(ev)
    image = np.asarray(img01, float)
    gamut = SOURCES[source][1]
    if encoding == "linear-xyz":
        from .color import clip_source_linear
        linear = xyz_to_rgb(image, gamut) * float(exposure_scale) * gain
        return clip_source_linear(linear, source)
    if encoding == "log":
        linear = source_to_lin709(np.clip(image, 0, 1), source) * gain
    elif encoding == "srgb":
        linear = srgb_eotf(np.clip(image, 0, 1)) * gain
    elif encoding == "linear-rec709":
        linear = np.maximum(image, 0) * gain
    else:
        raise ValueError(f"unsupported image encoding {encoding!r}")
    return np.maximum(linear, 0) @ lin709_to_gamut_matrix(gamut).T


def scene_linear(img01, source, encoding, ev=0.0, exposure_scale=1.0):
    """Exposed scene-linear Rec.709. EV is applied before the source log."""
    linear = gamut_linear(img01, source, encoding, ev, exposure_scale)
    gamut = SOURCES[source][1]
    return linear @ np.linalg.inv(lin709_to_gamut_matrix(gamut)).T


def source_codes(img01, source, encoding, ev=0.0, exposure_scale=1.0):
    """Map a loaded image into the source log codes the ISP model consumes."""
    if encoding == "log" and ev == 0:
        return np.clip(np.asarray(img01, float), 0, 1)
    if encoding == "linear-xyz":
        return encode_source_linear(gamut_linear(img01, source, encoding, ev, exposure_scale), source)
    return lin709_to_source(scene_linear(img01, source, encoding, ev, exposure_scale), source)


def appearance_rgb(rgb, output_space):
    """Preset output as Gamma 2.4 Rec.709 codes, before the monitor OETF."""
    if output_space == "logc3-awg3":
        return classic_rec709(rgb)
    return np.clip(rgb, 0, 1)


def preview_rgb(rgb, output_space):
    """sRGB bytes' linear source for the monitor.

    Wide-gamut presets are viewed through the built-in Rec.709 rendering.
    Rec.709 presets are Gamma 2.4 and are converted to sRGB for the display.
    """
    shown = appearance_rgb(rgb, output_space)
    if output_space in ("gamma24-rec709", "logc3-awg3"):
        return srgb_oetf(np.clip(shown, 0, 1) ** 2.4)
    return shown


def frame_delta_e(ref, sim, output_space, stride=4):
    """CIE76 of the picture the monitor shows, on a pixel stride."""
    space = "gamma24-rec709" if output_space == "logc3-awg3" else output_space
    left = appearance_rgb(ref, output_space)[::stride, ::stride]
    right = appearance_rgb(sim, output_space)[::stride, ::stride]
    delta = delta_e76(display_rgb_to_lab(left, space), display_rgb_to_lab(right, space))
    return {"count": int(delta.size), "mean": float(delta.mean()), "max": float(delta.max())}


def render_pair(cube, preset, profile, source, img01, is_log=False, *,
                video_range="legal", interpolation="trilinear", encoding=None, ev=0.0,
                exposure_scale=1.0):
    """Returns reference and simulated monitor RGB, plus the frame CIE76 report."""
    if encoding is None:
        encoding = "log" if is_log else "srgb"
    views = compose_preview("ideal", cube, preset, profile, source, img01, encoding=encoding,
                            video_range=video_range, interpolation=interpolation, ev=ev,
                            exposure_scale=exposure_scale)
    return views["left"], views["right"], views["cube_report"]


def _isp(preset, profile, codes, video_range, interpolation):
    ycc = rgb_to_ycc(matrix_stage(preset, profile, codes), profile)
    method = {"trilinear": trilinear, "tetrahedral": tetrahedral}[interpolation]
    delta = method(np.clip(preset.delta, *profile.delta_range), lut_indices(ycc, profile))
    rgb = simulate_8bit(ycc + delta, profile, video_range, chroma_420=True)
    return rgb, delta


def pixel_delta_e(ideal, simulated, output_space):
    """Per-pixel CIE76 after the shared display transform."""
    space = "gamma24-rec709" if output_space == "logc3-awg3" else output_space
    left = appearance_rgb(ideal, output_space)
    right = appearance_rgb(simulated, output_space)
    return delta_e76(display_rgb_to_lab(left, space), display_rgb_to_lab(right, space))


def _stats(delta):
    return {"count": int(delta.size), "mean": float(delta.mean()), "max": float(delta.max())}


def heatmap_color(delta_e):
    """RGBA overlay. Below 1.5 is transparent. Higher bands are blue, orange, then magenta."""
    de = np.asarray(delta_e, float)
    color = np.zeros(de.shape + (3,))
    alpha = np.zeros(de.shape)
    low = (de >= 1.5) & (de < 3.0)
    mid = (de >= 3.0) & (de < 6.0)
    high = de >= 6.0
    blend = (de[low] - 1.5) / 1.5
    color[low] = (1 - blend)[:, None] * (0.12, 0.35, 0.95) + blend[:, None] * (0.05, 0.82, 0.38)
    blend = (de[mid] - 3.0) / 3.0
    color[mid] = (1 - blend)[:, None] * (0.98, 0.86, 0.12) + blend[:, None] * (1.0, 0.42, 0.05)
    color[high] = (1.0, 0.05, 0.72)
    alpha[low | mid | high] = 0.75
    return color, alpha


def apply_overlay(image, kind, delta_e, clamp, split):
    """Draw the diagnostic on the right side of the split at 75% opacity."""
    out = np.array(image, copy=True)
    if kind in (None, "off"):
        return out
    width = out.shape[1]
    start = int(np.clip(split, 0, 1) * width)
    if kind == "de":
        color, alpha = heatmap_color(delta_e)
        region = out[:, start:]
        cover = alpha[:, start:, None]
        out[:, start:] = region * (1 - cover) + color[:, start:] * cover
        return out
    if kind == "clamp":
        yy, xx = np.indices(clamp.shape)
        stripe = ((yy + xx) // 5) % 2 == 0
        mask = clamp.copy()
        mask[:, :start] = False
        mask &= stripe
        out[mask] = out[mask] * 0.25 + np.array([1.0, 0.04, 0.18]) * 0.75
        return out
    raise ValueError(f"unsupported overlay {kind!r}")


def compose_preview(mode, cube, preset, profile, source, img01, *, encoding, video_range,
                    interpolation, ev=0.0, exposure_scale=1.0, rec709=None, wide=None,
                    spatial_mode="off"):
    """Build the two monitor images for a split mode, plus cube-vs-ISP diagnostics.

    Wide-gamut output and the ideal cube pass through the same Rec.709 display view.
    """
    linear = scene_linear(img01, source, encoding, ev, exposure_scale)
    codes = source_codes(img01, source, encoding, ev, exposure_scale)
    if mode == "arri":
        if rec709 is None or wide is None:
            raise ValueError("ARRI comparison needs both fitted presets")
        rec_ref, rec_preset, rec_profile, rec_range = rec709
        wide_ref, wide_preset, wide_profile, wide_range = wide
        rec_sim, _rec_residual = _isp(rec_preset, rec_profile, codes, rec_range, rec_ref.interpolation)
        wide_sim, residual = _isp(wide_preset, wide_profile, codes, wide_range, wide_ref.interpolation)
        if spatial_mode not in (None, "off"):
            from .spatial_test import apply_spatial_mode
            wide_sim = apply_spatial_mode(wide_sim, spatial_mode)
        left = preview_rgb(rec_sim, rec_profile.output_space)
        right = preview_rgb(wide_sim, wide_profile.output_space)
        wide_ideal = _match_encoder(wide_ref.sample(codes), wide_profile, wide_range)
        cube_delta = pixel_delta_e(wide_ideal, wide_sim, wide_profile.output_space)
    else:
        ideal = np.clip(cube.sample(codes), 0, 1)
        simulated, residual = _isp(preset, profile, codes, video_range, interpolation)
        if spatial_mode not in (None, "off"):
            from .spatial_test import apply_spatial_mode
            simulated = apply_spatial_mode(simulated, spatial_mode)
        ideal_encoded = _match_encoder(ideal, profile, video_range)
        if isinstance(cube, Alexa35Reference):
            display_space = "gamma24-rec709"
            ideal_view, simulated_view = cube.display(ideal_encoded), cube.display(simulated)
            cube_delta = delta_e00(display_rgb_to_lab(ideal_view, display_space),
                                   display_rgb_to_lab(simulated_view, display_space))
        else:
            display_space = profile.output_space
            ideal_view, simulated_view = ideal_encoded, simulated
            cube_delta = pixel_delta_e(ideal_view, simulated_view, display_space)
        right = preview_rgb(simulated_view, display_space)
        if mode == "source":
            left = srgb_oetf(np.clip(linear, 0, 1))
        elif mode == "ideal":
            left = preview_rgb(ideal_view, display_space)
        else:
            raise ValueError(f"unsupported split mode {mode!r}")
    shown = delta_e76(display_rgb_to_lab(left, "srgb"), display_rgb_to_lab(right, "srgb"))
    return {"left": left, "right": right, "cube_delta": cube_delta,
            "clamp": np.max(np.abs(residual), axis=-1) >= 62,
            "cube_report": _stats(cube_delta), "split_report": _stats(shown)}


def write_charts(base, cube, preset, profile, source, video_range, interpolation):
    from PIL import Image
    from .metrics import validation_samples

    for name, u in validation_samples(source).items():
        if name == "gray_gradient":
            img = np.repeat(u[None, :, :], 72, axis=0)
        else:
            columns = {"colorchecker": 6, "skin_tones": 9, "saturated_highlights": 8}[name]
            img = np.repeat(np.repeat(u.reshape(-1, columns, 3), 32, axis=0), 48, axis=1)
        ref, sim, _report = render_pair(cube, preset, profile, source, img, True,
                                        video_range=video_range, interpolation=interpolation)
        joined = np.concatenate([ref, sim], axis=0)
        Image.fromarray(np.floor(joined * 255 + 0.5).astype(np.uint8)).save(
            base.with_name(f"{base.name}.{name}.png"))
