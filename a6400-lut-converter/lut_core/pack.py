"""Explicit little-endian serialisation. No struct native-alignment, no platform I/O."""
from __future__ import annotations
import struct
import zlib
import numpy as np
from .model import Preset

MAGIC = b"A6LT"
VERSION = 1
_HDR = struct.Struct("<4sHHIHHHBBBBIIH")     # 32 bytes
assert _HDR.size == 32
FLAG_PERM_VERIFIED = 1


def pack_gamma(p: Preset) -> bytes:
    return p.gamma.astype("<u2").tobytes()


def pack_matrix(p: Preset) -> bytes:
    return p.matrix.astype("<i2").tobytes()


def _checked_delta(p):
    d = np.asarray(p.delta)
    if d.dtype.kind not in "iu" or d.shape[-1:] != (3,) or np.any((d < -64) | (d > 63)):
        raise ValueError("delta entries must be signed 7-bit integers")
    return d.reshape(-1, 3)


def pack_refadr(p: Preset) -> bytes:
    """3 planes (dY, dCb, dCr) x n_nodes int8, natural node order (Y-major, then Cb, Cr)."""
    d = _checked_delta(p).astype(np.int8)
    return d[:, 0].tobytes() + d[:, 1].tobytes() + d[:, 2].tobytes()


def esram_words_natural(p: Preset) -> np.ndarray:
    d = _checked_delta(p).astype(np.int32)
    return (((d[:, 0] & 0x7F) << 16) | ((d[:, 1] & 0x7F) << 8) | (d[:, 2] & 0x7F)).astype(np.uint32)


def pack_esram(p: Preset, profile) -> bytes:
    perm = np.asarray(profile.permutation, dtype=np.int64)
    if p.delta.shape != (*profile.grid, 3):
        raise ValueError("delta shape does not match profile")
    return esram_words_natural(p)[perm].astype("<u4").tobytes()      # slot s <- node perm[s]


def _sx7(v):
    v = v & 0x7F
    return np.where(v & 0x40, v - 128, v)


def unpack_esram(buf: bytes, profile) -> np.ndarray:
    w = np.frombuffer(buf, dtype="<u4")
    nat = np.empty_like(w)
    nat[np.asarray(profile.permutation, dtype=np.int64)] = w
    d = np.stack([_sx7((nat >> 16).astype(np.int64)), _sx7((nat >> 8).astype(np.int64)),
                  _sx7(nat.astype(np.int64))], -1).astype(np.int8)
    return d.reshape(*profile.grid, 3)


def pack_preset(p: Preset, profile) -> bytes:
    if profile.gamma_positions:
        raise ValueError("Movie gamma does not use the legacy A6LT format")
    payload = pack_gamma(p) + pack_matrix(p) + b"\0\0" + pack_esram(p, profile)
    ny, ncb, ncr = profile.grid
    flags = FLAG_PERM_VERIFIED if profile.permutation_verified else 0
    hdr = _HDR.pack(MAGIC, VERSION, _HDR.size, flags, profile.gamma_size, profile.gamma_max_val,
                    profile.matrix_q, ny, ncb, ncr, 0, len(payload), zlib.crc32(payload) & 0xFFFFFFFF, 0)
    return hdr + payload


def unpack_preset(buf: bytes, profile) -> Preset:
    if profile.gamma_positions:
        raise ValueError("Movie gamma does not use the legacy A6LT format")
    (magic, ver, hsz, flags, gs, gmax, q, ny, ncb, ncr, _r, plen, crc, _r2) = _HDR.unpack_from(buf, 0)
    if magic != MAGIC or ver != VERSION:
        raise ValueError("not an A6LT v1 preset")
    if hsz != _HDR.size or len(buf) != hsz + plen or plen != 2 * gs + 20 + 4 * profile.n_nodes:
        raise ValueError("invalid preset size")
    # Old default a6400 files stored natural-order words under an unverified flag.
    if profile.permutation_verified and not flags & FLAG_PERM_VERIFIED:
        raise ValueError("legacy unverified ordering: supply the profile used to write this preset")
    payload = bytes(buf[hsz:hsz + plen])
    if len(payload) != plen or (zlib.crc32(payload) & 0xFFFFFFFF) != crc:
        raise ValueError("preset truncated or CRC mismatch")
    if (gs, gmax, q, (ny, ncb, ncr)) != (profile.gamma_size, profile.gamma_max_val,
                                          profile.matrix_q, profile.grid):
        raise ValueError("preset does not match profile constants")
    o = 2 * gs
    gamma = np.frombuffer(payload[:o], dtype="<u2").astype(np.uint16)
    matrix = np.frombuffer(payload[o:o + 18], dtype="<i2").astype(np.int16).reshape(3, 3)
    delta = unpack_esram(payload[o + 20:], profile)
    return Preset(gamma, matrix, delta)
