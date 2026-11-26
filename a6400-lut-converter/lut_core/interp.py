import numpy as np


def trilinear(table, fi):
    """table[a,b,c,k]; fi[...,3] fractional indices along the 3 axes."""
    dims = np.array(table.shape[:3])
    fi = np.clip(np.asarray(fi, dtype=np.float64), 0, dims - 1)
    i0 = np.minimum(np.floor(fi).astype(np.int64), dims - 2)
    f = fi - i0
    out = 0.0
    for dx in (0, 1):
        wx = f[..., 0] if dx else 1 - f[..., 0]
        for dy in (0, 1):
            wy = f[..., 1] if dy else 1 - f[..., 1]
            for dz in (0, 1):
                wz = f[..., 2] if dz else 1 - f[..., 2]
                v = table[i0[..., 0] + dx, i0[..., 1] + dy, i0[..., 2] + dz]
                out = out + (wx * wy * wz)[..., None] * v
    return out


def tetrahedral(table, fi):
    dims = np.array(table.shape[:3])
    fi = np.clip(np.asarray(fi, dtype=float), 0, dims - 1)
    base = np.minimum(np.floor(fi).astype(np.int64), dims - 2)
    frac = fi - base
    order = np.argsort(-frac, axis=-1, kind="stable")
    weights = np.take_along_axis(frac, order, axis=-1)
    point = base
    previous = table[tuple(np.moveaxis(point, -1, 0))].astype(float)
    output = previous.copy()
    for axis, weight in zip(np.moveaxis(order, -1, 0), np.moveaxis(weights, -1, 0)):
        point = point + np.eye(3, dtype=int)[axis]
        current = table[tuple(np.moveaxis(point, -1, 0))].astype(float)
        output += weight[..., None] * (current - previous)
        previous = current
    return output
