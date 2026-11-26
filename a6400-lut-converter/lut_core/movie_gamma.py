"""Movie S-Log3 gamma profile based on E0505 record 31, variant 1."""
from dataclasses import replace
import struct

import numpy as np

STARTS = (0, 32, 64, 128, 256, 512, 1024, 2048, 4096)
EXTENTS = (32, 32, 64, 128, 256, 512, 1024, 2048, 4096)
EXPONENTS = (3, 3, 4, 4, 3, 3, 3, 4, 3)
METADATA = tuple(EXPONENTS[i] for i in (1, 0, 3, 2, 5, 4, 7, 6, 8))
POSITIONS = tuple(min(8191, start + (extent >> exp) * i)
                  for start, extent, exp in zip(STARTS, EXTENTS, EXPONENTS)
                  for i in range(1, 1 + (1 << exp)))
STOCK_CODES = (
    84, 132, 167, 193, 215, 233, 249, 262, 274, 286, 296, 305, 313, 321, 329, 336,
    342, 348, 354, 360, 365, 370, 375, 380, 384, 389, 393, 397, 401, 405, 408, 412,
    418, 425, 431, 437, 442, 447, 452, 457, 461, 466, 470, 474, 478, 482, 486, 489,
    502, 514, 525, 535, 544, 552, 560, 567, 581, 592, 603, 613, 622, 631, 638, 646,
    659, 671, 682, 692, 701, 709, 717, 724, 731, 738, 744, 750, 755, 760, 765, 770,
    775, 779, 784, 788, 792, 796, 799, 803, 803, 803, 803, 803, 803, 803, 803, 803,
)


def movie_profile(profile):
    return replace(profile, gamma_max_val=1023, gamma_positions=POSITIONS, gamma_source_codes=STOCK_CODES)


def gamma_fit_inputs(profile):
    if profile.gamma_source_codes:
        return np.asarray(profile.gamma_source_codes) / 1023
    return np.linspace(0, 1, profile.gamma_size)


def gamma_segment_positions(profile, codes):
    if not profile.gamma_source_codes:
        return codes * (profile.gamma_size - 1)
    stock = gamma_fit_inputs(profile)
    positions = np.asarray(profile.gamma_positions)
    # The stock plateau is ambiguous in log space. Use its first sensor knot.
    unique, indices = np.unique(stock, return_index=True)
    sensor = np.interp(codes, unique, positions[indices])
    return np.interp(sensor, positions, np.arange(profile.gamma_size))


def pack_movie_gamma(preset):
    if len(preset.gamma) != 96 or np.any((preset.gamma < 0) | (preset.gamma > 1023)):
        raise ValueError("Movie gamma requires 96 unsigned 10-bit codes")
    return struct.pack("<105I", *preset.gamma, *METADATA)
