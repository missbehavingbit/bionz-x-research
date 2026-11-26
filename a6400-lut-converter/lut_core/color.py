"""Log curves, gamuts, ColorChecker, CIELAB. numpy only."""
import numpy as np

# ---- primaries -> matrices -------------------------------------------------
D65 = (0.3127, 0.3290)
PRIMARIES = {
    "AWG3":           ((0.6840, 0.3130), (0.2210, 0.8480), (0.0861, -0.1020)),
    "Rec.709":        ((0.640, 0.330), (0.300, 0.600), (0.150, 0.060)),
    "S-Gamut3.Cine":  ((0.766, 0.275), (0.225, 0.800), (0.089, -0.087)),
    "S-Gamut3":       ((0.730, 0.280), (0.140, 0.855), (0.100, -0.050)),
    "S-Gamut":        ((0.730, 0.280), (0.140, 0.855), (0.100, -0.050)),
}


def rgb_to_xyz_matrix(name):
    p = np.array([[x / y, 1.0, (1 - x - y) / y] for x, y in PRIMARIES[name]]).T
    w = np.array([D65[0] / D65[1], 1.0, (1 - D65[0] - D65[1]) / D65[1]])
    s = np.linalg.solve(p, w)
    return p * s[None, :]


def lin709_to_gamut_matrix(name):
    return np.linalg.inv(rgb_to_xyz_matrix(name)) @ rgb_to_xyz_matrix("Rec.709")


def xyz_to_rgb(xyz, gamut):
    """CIE XYZ (D65, row vectors) to the named linear RGB primaries."""
    return np.asarray(xyz, float) @ np.linalg.inv(rgb_to_xyz_matrix(gamut)).T


# ---- Sony log curves (code value 0..1 <-> scene linear, 0.18 = mid grey) ----
def slog3_encode(x):
    x = np.asarray(x, float)
    hi = (420.0 + np.log10(np.maximum(x + 0.01, 1e-12) / 0.19) * 261.5) / 1023.0
    lo = (x * (171.2102946929 - 95.0) / 0.01125 + 95.0) / 1023.0
    return np.where(x >= 0.01125, hi, lo)


def slog3_decode(y):
    y = np.asarray(y, float)
    hi = np.power(10.0, (y * 1023.0 - 420.0) / 261.5) * 0.19 - 0.01
    lo = (y * 1023.0 - 95.0) * 0.01125 / (171.2102946929 - 95.0)
    return np.where(y >= 171.2102946929 / 1023.0, hi, lo)


_S2_LIN_SLOPE = 3.53881278538813
_S2_LIN_OFF = 0.030001222851889303


def slog2_encode(x):
    x = np.asarray(x, float) / 0.9
    hi = 0.432699 * np.log10(np.maximum(155.0 * x / 219.9 + 0.037584, 1e-12)) + 0.616596 + 0.03
    lo = x * _S2_LIN_SLOPE + _S2_LIN_OFF
    return np.where(x >= 0, hi, lo)


def slog2_decode(y):
    y = np.asarray(y, float)
    hi = (np.power(10.0, (y - 0.616596 - 0.03) / 0.432699) - 0.037584) * 219.9 / 155.0
    lo = (y - _S2_LIN_OFF) / _S2_LIN_SLOPE
    return 0.9 * np.where(y >= _S2_LIN_OFF, hi, lo)


_CURVES = {"slog3": (slog3_encode, slog3_decode), "slog2": (slog2_encode, slog2_decode)}

# label -> (curve, gamut)
SOURCES = {
    "S-Log3 / S-Gamut3.Cine": ("slog3", "S-Gamut3.Cine"),
    "S-Log3 / S-Gamut3": ("slog3", "S-Gamut3"),
    "S-Log2 / S-Gamut": ("slog2", "S-Gamut"),
}


def source_log_extent(source):
    """Scene-linear values that encode to code 0 and code 1 for this source curve."""
    decode = _CURVES[SOURCES[source][0]][1]
    ends = np.asarray(decode(np.array([0.0, 1.0])), float)
    return float(ends[0]), float(ends[1])


def clip_source_linear(linear, source):
    """Hard-clip linear source RGB to the curve's encode range."""
    lo, hi = source_log_extent(source)
    return np.clip(np.asarray(linear, float), lo, hi)


def encode_source_linear(linear, source):
    curve = SOURCES[source][0]
    return np.clip(_CURVES[curve][0](clip_source_linear(linear, source)), 0.0, 1.0)


def lin709_to_source(rgb_lin, source):
    curve, gamut = SOURCES[source]
    g = np.asarray(rgb_lin, float) @ lin709_to_gamut_matrix(gamut).T
    return np.clip(_CURVES[curve][0](g), 0.0, 1.0)


def source_to_lin709(code, source):
    curve, gamut = SOURCES[source]
    lin = _CURVES[curve][1](np.asarray(code, float))
    return lin @ np.linalg.inv(lin709_to_gamut_matrix(gamut)).T


# ---- display side: sRGB -> CIELAB (D65) -----------------------------------
def srgb_eotf(v):
    v = np.clip(np.asarray(v, float), 0, 1)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def srgb_oetf(l):
    l = np.clip(np.asarray(l, float), 0, 1)
    return np.where(l <= 0.0031308, l * 12.92, 1.055 * np.power(l, 1 / 2.4) - 0.055)


def display_rgb_to_lab(rgb, space="srgb"):
    rgb = np.clip(np.asarray(rgb, float), 0, 1)
    if space == "srgb":
        linear, gamut = srgb_eotf(rgb), "Rec.709"
    elif space == "gamma24-rec709":
        linear, gamut = rgb ** 2.4, "Rec.709"
    elif space == "logc3-awg3":
        from .arri import logc3_decode
        linear, gamut = logc3_decode(rgb), "AWG3"
    else:
        raise ValueError(f"unsupported output_space {space!r}")
    xyz = linear @ rgb_to_xyz_matrix(gamut).T
    wp = np.array([D65[0] / D65[1], 1.0, (1 - D65[0] - D65[1]) / D65[1]])
    t = xyz / wp
    d = 6.0 / 29.0
    f = np.where(t > d ** 3, np.cbrt(t), t / (3 * d * d) + 4.0 / 29.0)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], -1)


def delta_e76(lab1, lab2):
    return np.linalg.norm(lab1 - lab2, axis=-1)


# ---- X-Rite ColorChecker (sRGB 8-bit, commonly published values) ------------
COLORCHECKER_SRGB = np.array([
    (115, 82, 68), (194, 150, 130), (98, 122, 157), (87, 108, 67), (133, 128, 177), (103, 189, 170),
    (214, 126, 44), (80, 91, 166), (193, 90, 99), (94, 60, 108), (157, 188, 64), (224, 163, 46),
    (56, 61, 150), (70, 148, 73), (175, 54, 60), (231, 199, 31), (187, 86, 149), (8, 133, 161),
    (243, 243, 242), (200, 200, 200), (160, 160, 160), (122, 122, 121), (85, 85, 85), (52, 52, 52),
], dtype=np.float64) / 255.0


def srgb_image_to_source(img01, source):
    """Display-referred sRGB image (0..1) -> pretend-scene log code values."""
    return lin709_to_source(srgb_eotf(img01), source)


def colorchecker_image(patch=96, gap=8):
    h, w = 4 * patch + 5 * gap, 6 * patch + 7 * gap
    img = np.full((h, w, 3), 0.12)
    for i, c in enumerate(COLORCHECKER_SRGB):
        r, k = divmod(i, 6)
        y0, x0 = gap + r * (patch + gap), gap + k * (patch + gap)
        img[y0:y0 + patch, x0:x0 + patch] = c
    return img


def typical_mask(u, source, hi=1.0, lo=-0.02):
    """Log-code samples that represent plausible scene colours: reflectance <= hi and inside
    Rec.709 gamut (wide-gamut log cubes contain huge regions nobody ever shoots)."""
    lin = source_to_lin709(u, source)
    return ((lin >= lo) & (lin <= hi)).all(-1)

def delta_e00(lab1, lab2):
    """Vectorized CIEDE2000 with unit parametric factors."""
    l1, a1, b1 = np.moveaxis(np.asarray(lab1, float), -1, 0)
    l2, a2, b2 = np.moveaxis(np.asarray(lab2, float), -1, 0)
    c = (np.hypot(a1, b1) + np.hypot(a2, b2)) / 2
    g = 0.5 * (1 - np.sqrt(c**7 / (c**7 + 25**7)))
    a1, a2 = (1 + g) * a1, (1 + g) * a2
    c1, c2 = np.hypot(a1, b1), np.hypot(a2, b2)
    h1, h2 = np.degrees(np.arctan2(b1, a1)) % 360, np.degrees(np.arctan2(b2, a2)) % 360
    dh = h2 - h1
    neutral = (c1 * c2) == 0
    short = np.abs(dh) <= 180
    dh = np.where(neutral, 0, np.where(short, dh, (dh + 180) % 360 - 180))
    dl, dc = l2 - l1, c2 - c1
    d_h = 2 * np.sqrt(c1 * c2) * np.sin(np.radians(dh / 2))
    l, c = (l1 + l2) / 2, (c1 + c2) / 2
    h = h1 + h2
    h = np.where(neutral, h, np.where(short, h / 2, (h / 2 + 180) % 360))
    t = (1 - 0.17 * np.cos(np.radians(h - 30)) + 0.24 * np.cos(np.radians(2 * h))
         + 0.32 * np.cos(np.radians(3 * h + 6)) - 0.20 * np.cos(np.radians(4 * h - 63)))
    ls = 1 + 0.015 * (l - 50)**2 / np.sqrt(20 + (l - 50)**2)
    cs, hs = 1 + 0.045 * c, 1 + 0.015 * c * t
    rt = -2 * np.sqrt(c**7 / (c**7 + 25**7)) * np.sin(np.radians(60 * np.exp(-((h - 275) / 25)**2)))
    x, y, z = dl / ls, dc / cs, d_h / hs
    return np.sqrt(np.maximum(0, x*x + y*y + z*z + rt*y*z))
