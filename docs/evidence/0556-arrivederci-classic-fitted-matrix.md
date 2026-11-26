# E0556 - Arrivederci uses a matrix fitted to the official ARRI Classic LUT

Date: 2026-11-17. Offline fit and build, then a RAM-only stage on the ILCE-6400 (MSC, extcmd
debugger pid 703, Mod Loader v2 resident from the same day). Requested by the user. Receipts:
`artifacts/analysis/arrivederci-matrix-fit-2/` and `artifacts/tests/arrivederci-v3-build-1/`.

## What Arrivederci renders

Arrivederci changes two camera stages in Movie with PP S-Log3 / S-Gamut3.Cine: the per-channel EC
curve (record 31) and the 3×3 matrix B8. The curve is the ARRI Classic Rec.709 neutral axis,
sampled at the stock knots (E0505). It was not changed here. Before this record, B8 held the plain
S-Gamut3.Cine → Rec.709 primary conversion composed with stock B8. The 3D LUT stage is idle in
S-Log3 (E0505), so ARRI's hue and saturation rendering beyond what a 3×3 matrix and a shared
curve can express is not reproduced. The camera still encodes 8 bits after these stages.

## Fit (`fit_matrix.py`)

- Reference: official `ARRI_LogC3-to-Gamma24_Rec709_D65-Classic_33.cube`, SHA-256
  `29212a71808ab22f2819b904b3730fa4231b5841d883e43ec17610096dc38879` (not shipped), applied by
  `lut_core.arri.ArriReference.from_classic` to LogC3 / AWG3 of the scene-linear input.
- Camera model: y = K·x with K = M·B8⁻¹ (scene-linear S-Gamut3.Cine x, rows of M sum to 1024),
  then the camera-tested record 31 curve (block SHA-256 `93d69d06…de744`), interpolated linearly
  in sensor position. Knot linear values come from the stock S-Log3 codes. They are affine in
  position to within 0.57 % above position 64. Both sides are quantized to 8 bits and compared as
  Gamma 2.4 Rec.709.
- Assumptions: B8 maps white-balanced camera RGB to linear S-Gamut3.Cine and is applied before
  the EC curve (supported by the 2026-11-16 visual results, not isolated). Encoder range and
  chroma subsampling are not modeled.
- Weights follow the converter's validation sets: ColorChecker (1.0, plus −1 EV and +1 EV at
  0.5), skin ×2, gray gradient 0.5, saturated highlights 0.15. A pull of λ = 0.02 toward the
  primary conversion limits noise gain. Levenberg-Marquardt converged in 16 iterations.
- The script asserts that the baseline equals the camera-tested matrix.

| Set (8-bit ΔE00 mean) | Previous (primaries) | Fitted |
| --- | ---: | ---: |
| ColorChecker | 1.31 | 1.07 |
| ColorChecker −1 EV | 0.90 | 1.11 |
| ColorChecker +1 EV | 1.99 | 0.89 |
| Skin tones | 1.20 | 0.58 |
| Gray gradient | 0.18 | 0.18 |
| Saturated highlights | 11.54 | 4.72 |
| Skin hue error (deg) | 1.85 | 1.33 |
| Row norms (noise gain) | 1.72 / 1.45 / 1.26 | 1.52 / 1.31 / 1.12 |

Fitted Q10 matrix: `[[1698,-670,-4],[-105,1408,-279],[90,-478,1412]]` (row sums 1024, max
|coefficient| 1698). These are model errors against the reference, not camera measurements.

## Build and offline tests

`build_v3_wsl.sh artifacts/tests/arrivederci-v3-build-1 …/arrivederci-matrix-fit.json` produced
`Arrivederci.mod` (SMOD v2, 1516 B, code 1448 B, SHA-256
`c09693b8272db15672e9ae1665181094b3c517aea5ea0c9aa7569c49c992a88a`). `make_data.py` emits the
earlier matrix as `COLOR_LEGACY`. ON and OFF also accept a B8 that still holds the previous
Arrivederci matrix, and log `color=l` for that case. `test_mod_v2_unicorn.py` passed seven cases
at a non-zero load base: ON/OFF from stock and ARRI, ON and OFF over the legacy matrix, repeated
ON, a foreign matrix and a foreign curve left untouched, and all neighboring bytes unchanged. The
core harness (`mlv2-sd-build-3`) passed all ten scenarios with this file.

## Camera stage (`camera-on-readback.json`)

- Preflight: B8 stock, record 31 word 0 `0x1500`, Arrivederci OFF.
- `Arrivederci.mod` was replaced in `/tmp_bt/mods` (readback verified, atomic rename). The v2
  core reused the existing mapping `0x05d15000`.
- The user toggled ON and left the menu (Movie, S-Log3 / S-Gamut3.Cine) and reported that it
  "looks better" than the previous version. Readback: `mod_on 05d15000`,
  `arrivederci on=1 gamma=w color=w`. B8 and both live copies (+0x12c, +0x686) hold the fitted
  matrix, index 23, record 31 word 0 `0x300`. The 1448 resident code bytes equal the file.
- Uptime advanced from 21984.41 s to 22117.93 s, and the debugger answered `PONG`.

## State and limits

Arrivederci was left **ON** at the user's request to judge it. B8 is backed up by Sony at
power-off (E0505), so it has to be turned OFF before power-off. After this build, OFF also
restores stock from either matrix. The visual judgement is the user's. No capture was compared
with the reference.

## Next boundary

The remaining gap to the Classic look is mainly saturated highlights (ΔE00 4.7). That is beyond a
3×3 matrix with one shared curve. Closing it needs the CamCore 3D LUT stage in Movie, which is
idle in S-Log3, or a per-channel curve.
