"""Fit the Arrivederci B8 matrix to the official ARRI Classic look, in the camera's own structure.

Camera model (E0505, E0555; assumptions stated in the receipt):
  scene-linear S-Gamut3.Cine x  ->  y = K x  (K = M * B8^-1, M = the table entry written in place
  of B8, rows of M sum to 1024)  ->  per-channel EC curve: the camera-tested ARRI Classic record 31
  (96 knots at the stock S-Log3 positions, linear interpolation in sensor-linear position)  ->
  10-bit Gamma 2.4 Rec.709 codes  ->  8-bit.
Reference: the official ARRI LogC3 Classic 33-point LUT (SHA-256 29212a71...) applied to
LogC3 / AWG3 of the same x (lut_core.arri.ArriReference.from_classic).

Only the matrix is optimized. The curve stays the record-31 block that was verified on the
camera, so neutral gray is unchanged by construction (K preserves white). The weighting follows
the converter (lut_core.metrics): ColorChecker at three exposures, skin tones weighted x2, the
neutral gradient, saturated highlights at low weight, plus a small pull toward the plain primary
conversion that limits noise gain in 8-bit.

Run with a Python that has NumPy, from the repository root:
  py mods/arri_isp/arrivederci/fit_matrix.py CLASSIC_CUBE OUT_DIR
"""
import hashlib
import json
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "a6400-lut-converter"))
from lut_core.arri import ArriReference, SONY_TO_AWG3, logc3_encode  # noqa: E402
from lut_core.color import (COLORCHECKER_SRGB, delta_e00, delta_e76, display_rgb_to_lab,  # noqa: E402
                            rgb_to_xyz_matrix, slog3_decode, srgb_eotf, lin709_to_source)
from lut_core.metrics import validation_samples  # noqa: E402
from lut_core.movie_gamma import POSITIONS, STOCK_CODES  # noqa: E402

SOURCE = "S-Log3 / S-Gamut3.Cine"
GDIR = ROOT / "artifacts/analysis/arri-gamma-test-block-2026-11-16"
SHA_CURVE = "93d69d065c0547abdf680bc104a80780360111b2e312163e99a74f34659de744"
B8 = np.array([[1181, -188, 31], [51, 1024, -51], [118, -356, 1263]], float)
LAMBDA = 0.02          # pull toward the primary conversion (per squared coefficient, Lab units)

cube_path, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=False)
ref = ArriReference.from_classic(cube_path)

blob = (GDIR / "record31-arri-classic-rec709.bin").read_bytes()
assert hashlib.sha256(blob).hexdigest() == SHA_CURVE
codes = np.array(struct.unpack("<105I", blob)[:96]) >> 6
pos = np.array(POSITIONS, float)
stock_lin = slog3_decode(np.array(STOCK_CODES) / 1023)
live = np.arange(96) < 87                     # stock plateau (803) starts at knot 87
a, b = np.polyfit(pos[live & (pos > 64)], stock_lin[live & (pos > 64)], 1)
knot_lin = a * pos + b
affine_err = float(np.max(np.abs(knot_lin[live & (pos > 64)] - stock_lin[live & (pos > 64)])
                          / np.maximum(stock_lin[live & (pos > 64)], 1e-3)))


def curve(y):
    """Record-31 ARRI curve as the hardware interpolates it (linear in sensor position)."""
    return np.interp(y, knot_lin, codes, left=codes[0], right=codes[-1]) / 1023.0


def q8(v):
    return np.round(np.clip(v, 0, 1) * 255) / 255


def sim(x, K):
    return curve(np.asarray(x) @ K.T)


def reference(x):
    return np.clip(ref.sample_linear(x), 0, 1)


def lab(rgb):
    return display_rgb_to_lab(rgb, "gamma24-rec709")


# ---- sample sets (scene-linear S-Gamut3.Cine) --------------------------------------------------
vs = {k: slog3_decode(v) for k, v in validation_samples(SOURCE).items()}
cc = srgb_eotf(COLORCHECKER_SRGB)
sets = {
    "colorchecker": slog3_decode(lin709_to_source(cc, SOURCE)),
    "colorchecker_-1ev": slog3_decode(lin709_to_source(cc * 0.5, SOURCE)),
    "colorchecker_+1ev": slog3_decode(lin709_to_source(np.minimum(cc * 2, 1.0), SOURCE)),
    "skin_tones": vs["skin_tones"],
    "gray_gradient": vs["gray_gradient"],
    "saturated_highlights": vs["saturated_highlights"],
}
weights = {"colorchecker": 1.0, "colorchecker_-1ev": 0.5, "colorchecker_+1ev": 0.5,
           "skin_tones": 2.0, "gray_gradient": 0.5, "saturated_highlights": 0.15}
X = np.concatenate([sets[k] for k in sets])
W = np.concatenate([np.full(len(sets[k]), weights[k] / len(sets[k])) for k in sets])
REF_LAB = lab(reference(X))

A = np.linalg.solve(rgb_to_xyz_matrix("Rec.709"), rgb_to_xyz_matrix("S-Gamut3.Cine"))


def k_from(p):
    K = np.zeros((3, 3))
    off = [(0, 1), (0, 2), (1, 0), (1, 2), (2, 0), (2, 1)]
    for v, (r, c) in zip(p, off):
        K[r, c] = v
    for r in range(3):
        K[r, r] = 1 - K[r].sum()
    return K


def p_from(K):
    return np.array([K[0, 1], K[0, 2], K[1, 0], K[1, 2], K[2, 0], K[2, 1]])


def residual(p):
    K = k_from(p)
    d = (lab(sim(X, K)) - REF_LAB) * np.sqrt(W)[:, None]
    return np.concatenate([d.ravel(), np.sqrt(LAMBDA) * 100 * (p - p_from(A))])


def cost(p):
    r = residual(p)
    return float(r @ r)


# ---- Levenberg-Marquardt with a numerical Jacobian ---------------------------------------------
p = p_from(A)
mu = 1e-2
for it in range(200):
    r0 = residual(p)
    J = np.empty((r0.size, p.size))
    for j in range(p.size):
        h = np.zeros_like(p)
        h[j] = 1e-5
        J[:, j] = (residual(p + h) - r0) / 1e-5
    g, H = J.T @ r0, J.T @ J
    while True:
        step = np.linalg.solve(H + mu * np.diag(np.diag(H) + 1e-9), -g)
        if cost(p + step) < r0 @ r0:
            p, mu = p + step, max(mu / 3, 1e-7)
            break
        mu *= 4
        if mu > 1e8:
            break
    if np.max(np.abs(step)) < 1e-7 or mu > 1e8:
        break
K_fit = k_from(p)


def q10(m):
    """Round to Q10 and force row sums to 1024 by adjusting the largest element (producer rule)."""
    q = np.round(m * 1024).astype(int)
    for r in range(3):
        q[r, np.argmax(np.abs(q[r]))] += 1024 - q[r].sum()
    return q


def report(K):
    res = {}
    for name, x in sets.items():
        r8, s8 = q8(reference(x)), q8(sim(x, K))
        d00, d76 = delta_e00(lab(r8), lab(s8)), delta_e76(lab(r8), lab(s8))
        res[name] = {"de00_mean": round(float(d00.mean()), 3), "de00_max": round(float(d00.max()), 3),
                     "de76_mean": round(float(d76.mean()), 3)}
    skin = sets["skin_tones"]
    lr, ls = lab(q8(reference(skin))), lab(q8(sim(skin, K)))
    dh = np.degrees(np.arctan2(ls[:, 2], ls[:, 1])) - np.degrees(np.arctan2(lr[:, 2], lr[:, 1]))
    hue = np.abs((dh + 180) % 360 - 180)
    res["skin_hue_err_deg_mean"] = round(float(hue.mean()), 3)
    res["noise_gain_row_norms"] = [round(float(v), 3) for v in np.linalg.norm(K, axis=1)]
    return res


B8f = B8 / 1024
cands = {}
for name, K in (("primaries_current", A), ("classic_fit", K_fit)):
    M = q10(K @ B8f)
    Kq = (M / 1024) @ np.linalg.inv(B8f)
    cands[name] = {"q10": M.tolist(), "row_sums": M.sum(1).tolist(), "max_abs": int(np.abs(M).max()),
                   "K": np.round(Kq, 5).tolist(), "metrics": report(Kq)}
current = json.loads((ROOT / "artifacts/analysis/arri-movie-color-2026-11-16/arri-matrix-candidates.json")
                     .read_text())["candidates"]["rec709"]["q10"]
assert cands["primaries_current"]["q10"] == current, "baseline must equal the camera-tested matrix"

result = {
    "model": __doc__.strip(),
    "reference": {"file": cube_path.name, "sha256": hashlib.sha256(cube_path.read_bytes()).hexdigest()},
    "curve_block_sha256": SHA_CURVE,
    "knot_linear_fit": {"slope": a, "offset": b, "max_rel_err": round(affine_err, 5)},
    "weights": weights, "lambda": LAMBDA, "iterations": it + 1,
    "candidates": cands,
}
(out / "arrivederci-matrix-fit.json").write_text(json.dumps(result, indent=1) + "\n")
print(json.dumps({k: {"q10": v["q10"], **{s: v["metrics"][s] for s in
                  ("colorchecker", "skin_tones", "saturated_highlights", "skin_hue_err_deg_mean",
                   "noise_gain_row_norms")}} for k, v in cands.items()}, indent=1))
print("knot affine max rel err", round(affine_err, 5), "iterations", it + 1)
