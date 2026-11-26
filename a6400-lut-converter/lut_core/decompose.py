"""Joint bounded fit of gamma and Q10 matrix, followed by YCbCr residual fitting."""
from __future__ import annotations

from itertools import product

import numpy as np

from .color import lin709_to_source, typical_mask
from .movie_gamma import gamma_fit_inputs
from .model import gamma_codes, Preset, lut_indices, matrix_stage, rgb_to_ycc, ycc_to_rgb, round_code


def _grid(*axes):
    return np.stack(np.meshgrid(*axes, indexing="ij"), -1)


def _gamma(values, profile):
    return round_code(np.maximum.accumulate(np.clip(values, 0, 1)) *
                      profile.gamma_max_val).astype(np.uint16)


def _matrix(first_two, profile):
    q = profile.matrix_q
    first_two = round_code(first_two * q)
    coeff = np.column_stack([first_two, q - first_two.sum(axis=1)])
    if np.any((coeff < -32768) | (coeff > 32767)):
        raise ValueError("matrix coefficient does not fit int16")
    return coeff.astype(np.int16)


def _project(raw, profile):
    lo, hi = profile.delta_range
    chroma = raw[..., 1:]
    demand = np.where(chroma >= 0, chroma / hi, chroma / lo).max(axis=-1, keepdims=True)
    return np.concatenate([np.clip(raw[..., :1], lo, hi),
                           chroma / np.maximum(1, demand)], axis=-1)


def _fit_samples(cube, profile, source):
    axes = np.linspace(0, 1, 13)
    grid = _grid(axes, axes, axes).reshape(-1, 3)
    gray = np.linspace(0, 1, 129)
    if source:
        grid = grid[typical_mask(grid, source, hi=16)]
        scene = _grid(*(np.geomspace(0.003, 1, 9),) * 3).reshape(-1, 3)
        skin = np.array([[0.22, 0.11, 0.075], [0.48, 0.29, 0.21], [0.09, 0.04, 0.025]])
        skin = (skin[:, None, :] * np.geomspace(0.2, 5, 11)[None, :, None]).reshape(-1, 3)
        gray = lin709_to_source(np.repeat(np.geomspace(0.00001, 16, 129)[:, None], 3, axis=1), source)[:, 0]
        highlight_hues = _grid(*(np.array([0.01, 0.12, 0.45, 1]),) * 3).reshape(-1, 3)
        highlights = (highlight_hues[:, None, :] * np.array([1.6, 3.2, 6.4, 12.8])[None, :, None])
        grid = np.concatenate([grid, lin709_to_source(scene, source),
                               lin709_to_source(highlights.reshape(-1, 3), source),
                               lin709_to_source(skin, source)])
    else:
        skin = []
    n_skin = len(skin)
    n = len(grid)
    samples = np.concatenate([grid, np.repeat(gray[:, None], 3, axis=1)])
    weight = np.ones(len(samples))
    weight[n - n_skin:n] = 6
    weight[n:] = 8
    target = np.clip(cube.sample(samples), 0, 1)
    target_ycc = rgb_to_ycc(target, profile)
    if profile.output_space == "logc3-awg3":
        weight *= 1 + 3 * (target_ycc[:, 0] / profile.y_scale) ** 2
    return samples, target, target_ycc, weight


def _fit_base(cube, profile, samples, target, target_ycc, weight):
    x = gamma_fit_inputs(profile)
    neutral = np.clip(cube.sample(np.repeat(x[:, None], 3, axis=1)).mean(axis=-1), 0, 1)
    gamma = _gamma(neutral, profile)
    g = (gamma_codes(Preset(gamma, None, None), profile, samples) / profile.gamma_max_val
         if profile.gamma_positions else np.interp(samples, x, gamma / profile.gamma_max_val))
    unclipped = ((target > 0.005) & (target < 0.995)).all(-1)
    a = g[:, :2] - g[:, [2]]
    b = target - g[:, [2]]
    w = np.sqrt(weight * unclipped)[:, None]
    if np.linalg.matrix_rank(a * w) == 2:
        coeff = np.linalg.lstsq(a * w, b * w, rcond=None)[0].T
    else:
        coeff = np.eye(3)[:, :2]

    def candidate(parameters):
        curve = neutral + np.interp(x, (0, 0.25, 0.5, 0.75, 1),
                                    (0, *parameters[:3], 0))
        gamma = _gamma(curve, profile)
        matrix = _matrix(parameters[3:].reshape(3, 2), profile)
        mid_ycc = rgb_to_ycc(matrix_stage(Preset(gamma, matrix, None), profile, samples), profile)
        residual = target_ycc - mid_ycc
        errors = residual * np.sqrt(weight[:, None])
        lo, hi = profile.delta_range
        excess = np.maximum(np.maximum(residual - hi, lo - residual), 0)
        cost = np.abs(errors).max() + 3 * excess.max() + 0.15 * np.sqrt(np.mean(errors ** 2))
        return cost, gamma, matrix

    params = np.concatenate([np.zeros(3), np.clip(coeff, -4, 4).ravel()])
    best = candidate(params)
    initial = best[0]
    # Quantized coordinate search with coupled curve/matrix trials on each sweep.
    directions = np.eye(len(params))
    coupled = np.concatenate([np.repeat(np.eye(3), 6, axis=0), np.tile(np.eye(6), (3, 1))], axis=1)
    for step in (0.040, 0.025, 0.012, 0.006, 0.003, 0.0015):
        for direction in np.concatenate([directions, coupled, coupled * [1, 1, 1, -1, -1, -1, -1, -1, -1]]):
            change = step * direction * [0.45, 0.45, 0.45, 1, 1, 1, 1, 1, 1]
            trials = [(candidate(params + sign * change), sign) for sign in (1, -1)]
            trial, sign = min(trials, key=lambda item: item[0][0])
            if trial[0] < best[0] - 1e-9:
                best = trial
                params += sign * change
    return best[1], best[2], initial, best[0]


def _blend_matrix(mid_ycc, profile, interpolation):
    """Sample weights of the hardware interpolant. Each row sums to 1."""
    fi = lut_indices(mid_ycc, profile)
    base = np.minimum(fi.astype(int), np.array(profile.grid) - 2)
    frac = fi - base
    if interpolation == "tetrahedral":
        order = np.argsort(-frac, axis=-1, kind="stable")
        ordered = np.take_along_axis(frac, order, axis=-1)
        steps = np.eye(3, dtype=int)[order].cumsum(axis=1)
        offsets = np.pad(steps, ((0, 0), (1, 0), (0, 0)))
        blend = -np.diff(np.column_stack([np.ones(len(fi)), ordered, np.zeros(len(fi))]), axis=1)
    elif interpolation == "trilinear":
        offsets = np.array(list(product((0, 1), repeat=3)))
        blend = np.prod(np.where(offsets, frac[:, None, :], 1 - frac[:, None, :]), axis=-1)
    else:
        raise ValueError(f"unsupported interpolation {interpolation!r}")
    indices = np.ravel_multi_index(tuple(np.moveaxis(base[:, None, :] + offsets, -1, 0)), profile.grid)
    return blend, indices


def _node_fit(mid_ycc, target_ycc, weight, seed, profile, edge_compression, interpolation):
    blend, indices = _blend_matrix(mid_ycc, profile, interpolation)
    indices_flat = indices.ravel()
    regularization = 0.25
    degrees = np.bincount(indices_flat, (blend * weight[:, None]).ravel(), profile.n_nodes)
    fitted = seed.copy()
    raw = fitted
    for _ in range(180):
        error = target_ycc - mid_ycc - np.einsum("si,sic->sc", blend, fitted[indices])
        gradient = (blend * weight[:, None])[..., None] * error[:, None, :]
        correction = np.stack([np.bincount(indices_flat, column.ravel(), profile.n_nodes)
                               for column in np.moveaxis(gradient, -1, 0)], axis=-1)
        raw = fitted + 0.8 * (correction + regularization * (seed - fitted)) / (
            degrees[:, None] + regularization)
        fitted = _project(raw, profile)
        fitted[:, 1:] *= edge_compression
    return raw, _inset_rail(fitted, profile)


def _inset_rail(fitted, profile):
    """Keep exported nodes one count inside the signed rail without moving the rest.

    Chroma that has reached the rail is scaled as one (dCb, dCr) vector, so the hue stays.
    """
    lo, hi = profile.delta_range
    out = np.array(fitted, dtype=float, copy=True)
    chroma = out[:, 1:]
    peak = np.max(np.abs(chroma), axis=1, keepdims=True)
    scale = np.where(peak >= 62.0, 61.0 / np.maximum(peak, 1e-6), 1.0)
    out[:, 1:] = chroma * scale
    luma = out[:, 0]
    out[:, 0] = np.where(luma >= hi, hi - 1, np.where(luma <= lo, lo + 1, luma))
    return out


def decompose(cube, profile, source=None, edge_compression=0.0, interpolation="trilinear"):
    """Returns a quantized preset and a report on residual demand and saturation."""
    if not 0 <= edge_compression <= 1:
        raise ValueError("edge_compression must be in [0, 1]")
    samples, target, target_ycc, weight = _fit_samples(cube, profile, source)
    gamma, matrix, initial, final = _fit_base(cube, profile, samples, target, target_ycc, weight)
    nodes = _grid(profile.y_axis, profile.c_axis, profile.c_axis).reshape(-1, 3)
    mid = ycc_to_rgb(nodes, profile)
    oog = ((mid < -1e-6) | (mid > 1 + 1e-6)).any(axis=-1)
    x = gamma_fit_inputs(profile)
    g = gamma / profile.gamma_max_val
    inv = np.linalg.pinv(matrix.astype(float) / profile.matrix_q)
    signal = np.interp(np.clip(mid, 0, 1) @ inv.T, g + np.arange(len(g)) * 1e-10, x)
    reached = rgb_to_ycc(matrix_stage(Preset(gamma, matrix, None), profile, signal), profile)
    seed = rgb_to_ycc(np.clip(cube.sample(signal), 0, 1), profile) - reached
    seed[oog] = 0
    edge = np.max(np.abs(nodes[:, 1:]), axis=-1)
    compression = 1 - edge_compression * np.clip((edge - 64) / 64, 0, 1)[:, None]
    mid_ycc = rgb_to_ycc(matrix_stage(Preset(gamma, matrix, None), profile, samples), profile)
    raw, fitted = _node_fit(mid_ycc, target_ycc, weight, _project(seed, profile),
                            profile, compression, interpolation)
    lo, hi = profile.delta_range
    hit = (raw < lo - 1e-8) | (raw > hi + 1e-8)
    node_hit = hit.any(axis=-1)
    delta = round_code(fitted).astype(np.int8).reshape(*profile.grid, 3)
    rail = ((delta == lo) | (delta == hi)).any(axis=-1)
    return Preset(gamma, matrix, delta), {
        "nodes": profile.n_nodes,
        "clamped_nodes": int(node_hit.sum()),
        "clamped_nodes_pct": float(100 * node_hit.mean()),
        "clamped_values_pct": float(100 * hit.mean()),
        "at_rail_nodes": int(rail.sum()),
        "at_rail_nodes_pct": float(100 * rail.mean()),
        "out_of_gamut_nodes": int(oog.sum()),
        "clamped_in_gamut_pct": float(100 * node_hit[~oog].mean()),
        "raw_delta_min": float(raw.min()),
        "raw_delta_max": float(raw.max()),
        "joint_fit_cost_initial": float(initial),
        "joint_fit_cost_final": float(final),
        "edge_chroma_residual_compression": edge_compression,
        "matrix": matrix.tolist(),
    }
