# a6400 LUT converter

The Tk GUI in `lut_gui` is deprecated. Use `isp-studio` (`bionz-look-lab`) for preview
and fitting. `lut_core` remains the numeric oracle for the Rust port.

Fit Sony BIONZ X ISP tables from a 3D .cube or one of two ARRI references. The converter exports
an A6LT container plus optional EC_Gamma, Q10 matrix, RefAdr and direct eSRAM buffers.

## Run

From this directory with NumPy installed. Pillow is required for the GUI and PNG charts.
`rawpy` is required to open ARW and other camera raw files.

```powershell
python -m lut_gui
python -m lut_cli look.cube -s "S-Log3 / S-Gamut3.Cine" -o out --raw
python -m lut_cli --arri both --raw --charts -o out
python -m pytest tests
```

The GUI buttons **ARRI Wide Gamut** and **ARRI Rec.709** use the built-in models. No external LUT
is required. `--classic-cube` still accepts the official 33-point Classic file when you want that
file, instead of the built-in Rec.709 model, as the fitting target. Its SHA-256 is
`29212a71808ab22f2819b904b3730fa4231b5841d883e43ec17610096dc38879`.
[Official ARRI LogC3 package](https://www.arri.com/en/software/postproduction-software/legacy/lut-generator)

## Reference transforms and preview

Both ARRI presets accept normalized Sony S-Log3 / S-Gamut3.Cine code values. Wide gamut decodes
S-Log3, converts scene-linear primaries to ALEXA Wide Gamut (the primaries current tooling calls
ARRI Wide Gamut 3) and applies the EI 800 LogC3 curve. Its chroma residual tapers by 6% at the
±96 grid boundary and 12% at ±128. Rec.709 uses the same LogC3/AWG front end, then the published
AWG-to-Rec.709 matrix, a 24-knot monotone shoulder, and a highlight lift that moves weaker channels
toward the peak once the peak display code passes 0.45. That lift is 65% at code 1. The shoulder
follows the neutral axis of ARRI's Classic Gamma 2.4 look. The rest of the Rec.709 model is this
project's rendering, not a copy of the Classic cube.
[Sony S-Log3 documentation](https://www.sony.com/electronics/support/software/00263050)
[ARRI LogC3 specification](https://www.arri.com/resource/blob/31918/66f56e6abb6e5b6553929edf9aa7483e/2017-03-alexa-logc-curve-in-vfx-data.pdf)

The simulator looks up 96 uniformly spaced `uint16` gamma knots with a Q16 segment fraction and
rounds back to an integer code, ties toward positive infinity. The Q10 matrix is nine `int16`
coefficients: `(R*m0 + G*m1 + B*m2 + 512) // 1024`, then a clamp to the gamma full scale. Those
integer RGB codes are converted to BT.709 YCbCr with Y in `[0, 256]` and Cb/Cr in `[-128, 128]`.
The 5×9×9 stage is a trilinear blend of the stored `int8` nodes on
`Y = {0, 64, 128, 192, 256}` and `Cb/Cr = {-128, -96, -64, -32, 0, 32, 64, 96, 128}`, added in
YCbCr before the 8-bit 4:2:0 encoder. ARRI generation uses that trilinear hardware model.
`--interpolation tetrahedral` remains available for a cube whose author specifies it.

The encoder model supports full and legal ranges. Wide gamut defaults to full range and Rec.709 to
legal range (16–235 luma and 16–240 chroma). The image preview uses 2 × 2 chroma averaging before
8-bit quantization, edge padding on odd dimensions and nearest-neighbor chroma reconstruction. The
reference side of a cube-versus-ISP comparison passes through that same encoder, so the frame
CIE76 measures the color approximation rather than chroma siting. It
omits video compression. PNG comparisons put the reference above the simulation. Both rows are
converted for an sRGB monitor: Rec.709 from Gamma 2.4, and wide gamut by passing both LogC3 images
through the built-in Rec.709 view. The wide-gamut preset itself stays LogC3.

**Open image or RAW** accepts PNG, JPEG, TIFF and camera raw, including ARW. A still image is
treated as display-referred sRGB and inverse-encoded into the source log, so it contains no light
above diffuse white. A raw file is developed with LibRaw at half resolution to linear CIE XYZ
(D65) using the camera white balance and highlight mode Clip, so saturated photosites stay clipped
instead of being blended back into gamut. The selected Source primaries (published S-Gamut,
S-Gamut3, or S-Gamut3.Cine) convert that XYZ. Exposure, including the embedded-JPEG midtone scale,
is applied to that linear signal and the result is hard-clipped to the S-Log encode range before
S-Log3 or S-Log2. The log checkbox forces either file to be read as source log codes. The EV slider
runs from -3 to +3 in steps of 0.1. The status line reports the base JPEG scale, the EV gain, and
the frame CIE76 values.

The split mode chooses the two pictures. Source versus ISP shows the exposed photograph
against the 8-bit simulation. Ideal .cube versus ISP shows the reference and the simulation
through the same display view, including the Rec.709 view used for wide-gamut LogC3. ARRI
Rec.709 versus ARRI Wide Gamut shows the two fitted ISP outputs, with wide gamut viewed
through that same Rec.709 display. The overlay draws cube-versus-ISP CIE76 on the right
side, or marks pixels whose interpolated 5x9x9 residual is within two counts of the signed
7-bit rail.

The fitter puts the neutral tone curve in the 96-point gamma table, then solves the row-normalized
Q10 matrix in that gamma-coded space. Neutral gray and skin samples carry more weight than the
bulk of the cube. The 5×9×9 residual is an iterative fit of the same interpolant the simulator
will run. When a chroma component reaches the signed rail, `(dCb, dCr)` is scaled as one vector
so the hue is kept and the exported node sits one count inside `[-64, +63]`. A luma component on
that rail is moved one count inward. Reports still separate nodes whose update wanted a value
outside the range from nodes whose exported integers equal a rail.

CIE76 metrics compare the 8-bit simulation for solid patches to the selected reference. Rec.709
uses Gamma 2.4 Rec.709 linearized to D65 XYZ. Wide gamut uses decoded AWG scene exposure with D65
reference white at exposure 1, so highlight Lab values can exceed the diffuse-white domain. The
pooled average includes all patches and is dominated by the 512 gray-gradient samples. The full
code cube includes implausible log colors and is reported separately.

On the validation set, the built-in Rec.709 model versus the official Classic cube (same SHA-256
as above, not shipped) is:

| Set | Mean / max CIE76 |
| --- | ---: |
| ColorChecker | 2.14 / 7.34 |
| Gray | 0.12 / 0.70 |
| Skin | 1.87 / 4.57 |
| Saturated highlights | 10.57 / 34.95 |

Neutral gray and skin are close. Saturated highlights are the gap: the official cube lifts the
weaker channels further toward white than this shoulder-and-peak model does. The ISP table is then
fit to the built-in model, so a small ISP residual and this model gap are separate errors.

## Binary outputs and evidence

- EC_Gamma contains 96 little-endian uint16 values (192 bytes). The matrix contains nine
  little-endian int16 values in row order (18 bytes). Every fitted row sums to 1024.
- RefAdr contains three 405-byte signed planes in dY, dCb, dCr order. Each uses index
  `81*y + 9*cb + cr`. Direct eSRAM contains 405 little-endian uint32 words in the
  firmware slot order from `av-cam.bin` at VA `0x63e69238`.
- A6LT v1 contains a 32-byte header, gamma, matrix, two padding bytes and eSRAM payload.
  Decode a legacy unverified file using the same profile that wrote it. Range and interpolation
  are simulation settings recorded in the accompanying JSON and are outside the A6LT format.

Firmware analysis establishes table sizes, signed fields, slot direction and RefAdr plane order.
The gamma knot placement and full scale, matrix rounding and saturation, runtime YCbCr arithmetic,
LUT interpolation, encoder range and chroma filtering remain model assumptions. The Sony log-code
input is an interface to the fitting model. Its relation to a physical pre-quantization EC_Gamma
tap still needs measurement. The chart comparisons establish simulator agreement with the
reference transforms. Camera parity requires a matched capture.
[E0505 firmware evidence and simulator validation](../docs/evidence/0505-av-cam-cc-3dlut-and-ec-gamma-pipeline.md)

## ALEXA 35 conversion and validation

The ALEXA 35 presets accept S-Log3 / S-Gamut3.Cine. The LogC4/AWG4 preset decodes
S-Log3, converts linear RGB through D65 XYZ, and applies the published LogC4
transfer function. Its Rec.709 counterpart uses the official ARRI display LUT
as the fit target. The same LUT renders both sides of the LogC4 preview and is
used for display-referred Delta E00. The primary conversion models a color-space
transform. A sensor match would require paired measurements.

In the GUI, select **Alexa 35 LogC4** or **Alexa 35 Rec.709** and open either
official full-range Gamma 2.4 Rec.709 D65 v1 cube (33 or 65 points) from the
[ARRI LogC4 LUT package](https://www.arri.com/en/learn/camera-systems/image-science/log-c#related-downloads).
For the CLI, run from this converter directory:

```powershell
python -m lut_cli --arri alexa35 --logc4-display-cube "path/to/ARRI_LogC4-to-Gamma24_Rec709-D65_v1_33.cube" --gamma-model compare --charts --raw -o out/alexa35
```

Use `--arri logc4` or `--arri alexa35-rec709` for individual targets. The
converter validates the LUT checksum and records it in each JSON report.
LogC4 defaults to full video range and Rec.709 to legal video range.

The uniform model writes a standard simulator `.a6lt` preset. The experimental
Movie gamma option uses 96 record-31 positions and the stock 10-bit codes as
the coordinate bridge back to the source log domain. Its inverse holds at
the stock endpoints and maps the 803 plateau to its first position. This loses
the distinction between sensor values that share a stock code. The Movie
export is a standalone 420-byte block containing 96 10-bit values in
little-endian uint32 words plus nine metadata words. The matrix and LUT
from `--raw` are simulator comparison assets. The Movie export has no
verified camera publication path.

Compare JSON results with and without the residual LUT. The four charts
cover ColorChecker, the gray gradient, skin, and saturated highlights.
The top half shows the reference and the bottom half shows the simulated
8-bit 4:2:0 result. The numerical report uses solid patches and records
the encoder range, interpolation, signed LUT rail count, repeated
gray luma codes, skin hue error, and pre-encoder RGB excursions.
