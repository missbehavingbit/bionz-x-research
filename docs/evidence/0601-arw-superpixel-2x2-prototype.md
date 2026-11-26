# E0601 — Offline `2×2` Super-Pixel Binning Prototype on `_DSC0347.ARW`

- **Date:** 2026-11-18
- **Input:** `C:\Users\Administrator\Pictures\Sony a6400\_DSC0347.ARW` (`4024 × 6024` visible raw, `RGGB` (`RGBG`), black level `512` per channel, 14-bit white level `16383`, camera WB `[2052, 1024, 2292, 1024]`)
- **Tools:** Python (`rawpy 0.28.0`, `numpy 2.4.3`, `PIL 12.2.0`)
- **Preserved artifacts:**
  - `artifacts/analysis/superpixel-arw-2026-11-18/metrics.json`
  - `artifacts/analysis/superpixel-arw-2026-11-18/superpixel_3012x2012.png`
  - `artifacts/analysis/superpixel-arw-2026-11-18/ahd_downscaled_3012x2012.png`

## Observations (10th-percentile quiet 32×32 block luma std-dev in sRGB)

| Pipeline | Output size | `p10_noise_std_srgb` | `mean_luma` |
| --- | --- | ---: | ---: |
| Full-res AHD (`rawpy`) | `4024 × 6024` | `0.021518` | `0.1885` |
| AHD + 2×2 box downscale | `2012 × 3012` | `0.016431` | `0.1909` |
| LibRaw `half_size=True` (2×2 Super-Pixel) | `2012 × 3012` | `0.022018` | `0.1882` |
| Custom 2×2 Super-Pixel (D65-normalized `cam_to_srgb`) | `2012 × 3012` | `0.022456` | `0.1868` |
| Custom 2×2 Super-Pixel (un-normalized `cam_to_srgb`) | `2012 × 3012` | `0.036608` | `0.1823` |

## Conclusions

1. **Verified `IMX284` 14-bit RAW parameters:** `4024 × 6024` visible raw (`6000 × 4000` active), `RGGB` pattern, black level `512`, white level `16383`, Q10-scaled WB `[2052, 1024, 2292, 1024]`.
2. **Noise vs. simplicity trade-off:** Pure 2×2 Super-Pixel (`half_size`, `0.0220`) avoids all Bayer interpolation/zipper artifacts and is trivial to implement in ARM/Thumb, but does **not** beat AHD + 2×2 box downscaling (`0.0164`) in luma noise because each 2×2 quad only samples `1×R`, `1×B`, and `2×G` (instead of averaging 4 demosaiced pixels). Custom `cam_to_srgb` without matrix noise-damping amplifies noise further (`0.0366`).
