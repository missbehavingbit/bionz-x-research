# E0600 — ARRI Image SDK v7.1.1 Debayer, PrepDebayer XTC, AFA Texture, and ANR2 Pipeline

- **Date:** 2026-11-18
- **Targets (`artifacts/ARRI binaries/`):**
  - `ArriImageSdk.7.dll` — `15,773,696` bytes, SHA-256 `773c5cd5ea52db55c4afdd78b9e995a5789c5a90293136184253e36c8b6ff5f3`
  - `ArriImageSdkTransformsCpu_module.7.1.1.dll` — `3,020,288` bytes, SHA-256 `cddb3328da302c012f66a3d949ada32254252d63270f9bc2c9ba04d5e9ed138b`
  - `ArriImageSdkTransformsCuda_module.7.1.1.dll` — `9,122,816` bytes, SHA-256 `4eef251a065c90a4013da4b4d7ff627e8e9a21ea72c9a303fd1bc4300611ce9d`
  - `ArriImageSdkTransformsOpenCl_module.7.1.1.dll` — `2,993,152` bytes, SHA-256 `15723d12ad77c5b9e072e8ac5c7b4b2ee655d7ce15dca6f20fbf73d8cc`
- **Tools & scripts:**
  - `pefile` PE/export inspection, pure-Python NVIDIA fatbin (`0xba55ed50`) LZ4 PTX decompressor (`sm_75`)
  - `objdump -d -M intel` (WSL) on `ArriImageSdkTransformsCpu_module.7.1.1.dll` (`0x18008d920`)
  - `tools/research/analyze_arri_sdk_e0600.py`
- **Preserved artifacts:**
  - `artifacts/analysis/arri-sdk-pipeline-e0600/ptx/*_sm75.ptx`
  - `artifacts/analysis/arri-sdk-pipeline-e0600/e0600_arri_debayer_and_afa_tables.json`
- **Scope & constraints:** Strictly read-only static analysis of host SDK binaries.

---

## 1. End-to-End Spatial & Debayer Pipeline in ARRI Image SDK v7.1.1

Extracting all 13 NVIDIA fatbins from `ArriImageSdkTransformsCuda_module.7.1.1.dll` reveals the complete kernel suite:

1. **Unpack & Defect:** `UnpackKernelASH` (bit unpacking) and `ApplyUDP_kernel` (User Dead Pixel correction).
2. **Pre-Debayer Domain (`ApplyPrepDeBayerXTC_kernel`, fatbin `0x876a60`):**
   - Operates on 2×2 Bayer quads (`uint16_t`) across 4 Bayer patterns (`PREPDEBAYER_BAYER_PATTERN0..3`) and 2 boolean variants (`8` kernel entries).
   - Applies a 1D input LUT (`qlut_compress` / linearization), locally interpolates 4 neighbors to form `(R, G, B)` per site, applies a second 1D LUT, and evaluates a **15×15×15 3D LUT** (`tex.3d.v4.s32.f32`, trilinear weights) before outputting modified 16-bit Bayer (`ada5_in` / `outputLut`).
3. **Denoise (`ANR1SWKernel` at `0x742ac0`, `ANR2DenoiseKernel` at `0x74e4d0`):**
   - `ANR2DenoiseKernel` takes two 16-bit Bayer/luma inputs, `float3` per-channel `KGain` (`arri3SDKAnr2KGainRed/Green/Blue`), `float` preset strength, and a `float*` noise-LUT curve.
4. **Debayer (`ada5SwKernel`, `ada5HwKernel`, `ada7HwKernel`, `standardProxyKernel`, `fastProxyKernel`):**
   - All full-resolution ADA kernels process 16-bit Bayer (`RGGB`, `BGGR`, `GRBG`, `GBRG`), require even image width and overscan borders, and stream rows through a shared-memory `ringBuffer[5324]` (3 planes of `6 × 132` `uint16_t` = `1584` bytes/plane, `±3` horizontal overscan, `-5` vertical overscan) outputting `ushort4` RGBA (`A = 0xFFFF`).
5. **AFA Texture & Scaling (`scale_kernel`, fatbin `0x2cbc00`):**
   - Runs 9-tap separable filtering (`tdFilterYLuma`/`sFilterYLuma`, `tdFilterYColor`/`sFilterYColor`) with contrast-adaptive blending (`max - min` over the 9-tap window) and `RenoiseLuts` (`norm_noise = 2^-8 = 0.00390625`).

---

## 2. Exact Debayer Algorithm Comparison

| Kernel | Fatbin Offset | Arithmetic | Gradient Quantization & Weight LUT | Shadow Damping | Chroma / Sharpness Stage | Applicability |
| --- | --- | --- | --- | --- | --- | --- |
| `ada5HwKernel` | `0x536270` | Mixed `int32` + `float32` | `G_H, G_V >> 10` (`/1024`, clamped `[0,31]`) → `ada5WeightMatrix` (`int16` Q15, diag `16383..16384`) | `G_avg < 1024.0` scales Laplacian by `G_avg / 1024.0` | Color-difference (`R-G`, `B-G`) interpolation | `ImageProcessingModel` Gen1/Gen2 |
| `ada5SwKernel` | `0x4062a0` | `float32` division (`__cuda_sm3x_div_rn_noftz_f32`) | Direct `float32` gradient ratio (no 32×32 LUT) | `G_avg < 1024.0` damping | 3×3 box-average adaptive sharpen (`ada5Sw_sharpnessRed/Green/Blue`), gated by `|C_3x3 - G_3x3|` (`1.0` below `256`, linear ramp to `0.0` at `512`) | `ImageProcessingModel` Gen1/Gen2 |
| `ada7HwKernel` | `0x60fe10` | **Pure `int32` / fixed-point** (zero `.f32` registers) | `G_H, G_V >> 8` (`/256`, clamped `[0,31]`, 4× finer) → `ada7WeightMatrix` (`uint16` Q15, diag `16384`, `w[r,c]+w[c,r]=32768`) | `G[-1]+G[+1]+1 < 2048` (`G_avg < 1024`) scales Laplacian by `>> 10` | Horizontal `[1,2,1]/4` LP on `R, G, B` + gated `(G - G_lp)` transfer (`clamp(512 - k*penalty, 0, 256)`), with `4×` penalty below `2048`, `2×` below `4096`, `1×` above | Gen3 / ALEV4 (`ImageProcessingGen3`) |
| `standardProxyKernel` | `0x70fcc0` | `uint32` shifts | None (bilinear) | None | Full-res bilinear (`G` avg of 4 `>> 2`, `R/B` avg of 2 or 4) | Proxy / fast preview |
| `fastProxyKernel` | `0x72b7d0` | `uint32` shifts | None (2×2 box) | None | Half-res (`(G1+G2) >> 1`, direct `R, B`) | Fast half-res proxy |

### 2.1 Green Interpolation Core (`ada5HwKernel` & `ada7HwKernel`)
- **5×5 Directional Gradients:**
  - $G_H$ sums 8 horizontal absolute differences across rows `[-2..+2]`, with `2×` weight on the center row `0`.
  - $G_V$ sums 8 vertical absolute differences across columns `[-2..+2]`, with `2×` weight on the center column `0`.
- **Directional Estimates with Shadow-Damped Laplacian:**
  - $G_{\text{horiz}} = \frac{G_{0,-1} + G_{0,+1} + 1}{2} + s_H \cdot \frac{2C_0 - C_{0,-2} - C_{0,+2}}{8}$, where $s_H = \min(1, G_{\text{avg},H} / 1024)$.
  - $G_{\text{vert}}$ is symmetric along the vertical axis.
  - Final green is $G_{\text{vert}} + \frac{w}{32768}(G_{\text{horiz}} - G_{\text{vert}})$ clamped to `[0, maxVal]`.

---

## 3. Extracted 9-Tap AFA Texture & Smooth Filter Banks (`Cpu_module`)

Disassembly of `0x18008d920` in `ArriImageSdkTransformsCpu_module.7.1.1.dll` located the `float64` 9-tap filter tables for `scale_kernel` (`e0600_arri_debayer_and_afa_tables.json`):

1. **`ALEV4_AFATexture_09T_0.1.0`** (`13` scaling ratios: `0.25, 0.30, …, 0.65, 0.666667, 0.70, 0.75, 0.80`):
   - Every 9-tap row sums to `1.000000` (one `0.8`-domain row `table_b` sums to `1.000811`).
   - At `ratio = 0.25`: Gaussian-like low-pass (`MTF50 = 0.0811 cpp`, Nyquist `0.0452`).
   - At `ratio = 0.80`:
     - `table_a` (Smooth): `[ -0.001125, 0.014032, -0.045141, 0.082621, 0.899225, ... ]` — minimal negative lobes (`-0.045`), Nyquist gain `0.6134`.
     - `table_b` (Texture/Detail): `[ -0.020703, 0.094130, -0.397188, 0.177211, 1.293098, ... ]` — mid-band micro-contrast boost (`center = 1.293`, `-0.397` at `±2`) with strong Nyquist roll-off (`0.0854`, `MTF50 = 0.4247 cpp`).
2. **`GENERIC_AfaSmoothLuma_09T_0.1.1`** (`5` ratios: `0.56, 0.62, 0.68, 0.74, 0.80`):
   - At `0.56`: `table_a` (`MTF50 = 0.215 cpp`, Nyquist `0.0304`), `table_b` (`MTF50 = 0.2564 cpp`, Nyquist `0.0990`).
   - At `0.80`: `table_a` (`MTF50 = 0.3915 cpp`, Nyquist `0.1333`), `table_b` (`MTF50 = 0.4479 cpp`, Nyquist `0.2812`).
3. **`GENERIC_AfaSmoothChroma_09T_0.0.7`** (`2` ratios: `0.56, 0.80`):
   - At `0.56`, `table_a` is a heavy Gaussian-like chroma anti-aliasing filter (`MTF50 = 0.0696 cpp`, Nyquist `0.0948`), while `table_b` has `MTF50 = 0.289 cpp`, Nyquist `0.0058`.

---

## 4. Next Boundary (`E0601`)
Decompile `av-cam.bin` `SigItpBayerSetter` (`0x644fa368`), `SigItpArSetter` (`0x644fa358`), `PP_Detail*` consumers (`0x63bd80d0`), and the 6 unmapped `CamCore` PQD setters in `0x63dd8f60` to map Sony's hardware demosaic, detail, and false-color controls.
