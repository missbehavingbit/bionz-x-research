"""Load a preview image as the ISP model's input codes.

JPEG, PNG and TIFF are display-referred sRGB. Camera raw files are developed to
linear CIE XYZ with LibRaw highlight clipping. The selected source primaries then
convert that XYZ to linear S-Gamut, S-Gamut3, or S-Gamut3.Cine before the log encode.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .color import srgb_eotf, xyz_to_rgb

RAW_SUFFIXES = {".arw", ".dng", ".nef", ".cr2", ".cr3", ".raf", ".rw2", ".orf"}


@dataclass(frozen=True)
class LoadedImage:
    rgb: np.ndarray
    encoding: str
    note: str
    exposure_scale: float = 1.0


def _luma(rgb):
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def _proxy(rgb, edge=200):
    step = max(1, int(max(rgb.shape[:2]) / edge))
    return np.asarray(rgb, np.float64)[::step, ::step]


def exposure_scale(linear, jpeg_srgb=None):
    """Scale sensor-clip linear RGB into scene units where the JPEG midtones sit.

    A raw file stores clip at 1. Scene middle gray for S-Log3 is 0.18, far below
    clip on a normal exposure. The embedded JPEG carries the camera's exposure.
    """
    scene = _luma(_proxy(linear))
    bright = np.percentile(scene, 95)
    if jpeg_srgb is not None:
        jpeg = _proxy(np.clip(jpeg_srgb, 0, 1))
        if jpeg.shape[:2] != scene.shape[:2]:
            from PIL import Image
            code = np.clip(np.rint(jpeg * 255), 0, 255).astype(np.uint8)
            code = np.asarray(Image.fromarray(code, mode="RGB").resize(
                (scene.shape[1], scene.shape[0]), Image.Resampling.BOX), np.float64) / 255.0
            jpeg = code
        target = _luma(srgb_eotf(jpeg))
        mask = scene >= np.percentile(scene, 20)
        base = np.median(scene[mask])
        goal = np.median(target[mask])
        if base > 1e-5 and np.isfinite(goal):
            return float(np.clip(goal / base, 0.25, 64.0))
    if bright <= 1e-5:
        return 1.0
    return float(np.clip(0.45 / bright, 0.25, 64.0))


def _limit_edge(rgb, max_edge):
    """Box-average linear RGB so the long edge fits. Pillow has no 16-bit RGB mode."""
    rgb = np.asarray(rgb, dtype=np.float64)
    height, width = rgb.shape[:2]
    long_edge = max(height, width)
    if long_edge <= max_edge:
        return rgb
    factor = int(np.ceil(long_edge / max_edge))
    height -= height % factor
    width -= width % factor
    cropped = rgb[:height, :width]
    return cropped.reshape(height // factor, factor, width // factor, factor, rgb.shape[-1]).mean(axis=(1, 3))


def _develop_raw(path, max_edge):
    try:
        import rawpy
    except ImportError as error:
        raise ValueError("RAW preview needs the rawpy package (LibRaw). Install it with: python -m pip install rawpy") from error
    try:
        with rawpy.imread(str(path)) as raw:
            balance = tuple(float(v) for v in list(raw.camera_whitebalance)[:3])
            developed = raw.postprocess(
                use_camera_wb=True,
                use_auto_wb=False,
                no_auto_bright=True,
                gamma=(1.0, 1.0),
                output_bps=16,
                output_color=rawpy.ColorSpace.XYZ,
                highlight_mode=rawpy.HighlightMode.Clip,
                half_size=True,
            )
            thumb = _embedded_jpeg(raw)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f"cannot develop {path.name}: {error}") from error
    if developed.dtype != np.uint16 or developed.ndim != 3 or developed.shape[-1] != 3:
        raise ValueError(f"unexpected raw develop shape {developed.shape} {developed.dtype}")
    try:
        xyz = developed.astype(np.float64) / 65535.0
        # The JPEG match is only a scene-exposure estimate. The stored pixels stay XYZ.
        rec709 = np.maximum(xyz_to_rgb(xyz, "Rec.709"), 0.0)
        scale = exposure_scale(rec709, thumb)
        linear = _limit_edge(xyz, max_edge)
    except Exception as error:
        raise ValueError(f"cannot scale {path.name}: {error}") from error
    if thumb is not None:
        exposure = f"Exposure scale {scale:.2f} puts the raw midtones on the embedded JPEG."
    else:
        exposure = f"No embedded JPEG. Exposure scale {scale:.2f} puts the 95th percentile near 0.45 scene linear."
    note = (
        "True Sony S-Gamut3.Cine / Unblended Highlights. "
        f"LibRaw half-resolution develop: camera white balance {balance}, linear CIE XYZ (D65), "
        "highlight mode Clip. The selected Source primaries convert that XYZ to linear "
        "S-Gamut, S-Gamut3, or S-Gamut3.Cine. "
        f"{exposure} The EV slider then scales that linear signal, which is hard-clipped to the "
        "S-Log encode range before S-Log3 or S-Log2."
    )
    return linear, note, scale


def _embedded_jpeg(raw):
    try:
        import rawpy
        thumb = raw.extract_thumb()
    except Exception:
        return None
    if getattr(thumb, "format", None) != rawpy.ThumbFormat.JPEG:
        return None
    try:
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(thumb.data)) as image:
            return np.asarray(image.convert("RGB"), np.float64) / 255.0
    except Exception:
        return None


def load_image(path, max_edge=900) -> LoadedImage:
    """Return RGB in 0..1 and the encoding the preview should apply."""
    file = Path(path)
    if file.suffix.lower() in RAW_SUFFIXES:
        rgb, note, scale = _develop_raw(file, max_edge)
        return LoadedImage(rgb, "linear-xyz", note, scale)
    try:
        from PIL import Image
    except ImportError as error:
        raise ValueError("image preview needs Pillow") from error
    try:
        with Image.open(file) as image:
            rgb = np.asarray(image.convert("RGB"), dtype=np.float64) / 255.0
    except Exception as error:
        raise ValueError(f"cannot open {file.name}: {error}") from error
    rgb = _limit_edge(rgb, max_edge)
    note = (
        "Display-referred sRGB. The preview inverse-encodes it into the source log. "
        "A camera JPEG has already been developed, and this file has no light above diffuse white."
    )
    return LoadedImage(rgb, "srgb", note)
