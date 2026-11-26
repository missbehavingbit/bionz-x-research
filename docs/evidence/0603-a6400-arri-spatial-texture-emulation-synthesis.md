# E0603 — Sony a6400 ARRI Spatial, Debayer & Texture Emulation Synthesis

- **Date:** 2026-11-18
- **Synthesized Evidence:**
  - [E0505](0505-av-cam-cc-3dlut-and-ec-gamma-pipeline.md) — `CC:` 3D LUT (`5×9×9` YCbCr), `3×3` Q10 color matrix, and `EC_Gamma` (`96 × uint16_t`)
  - [E0599](0599-av-cam-debayer-psf-and-imx284-readout.md) — `SigItpBayerSetter`, `SigItpArSetter`, `/usr/sabin/` headers, and `IMX284` 19 readout modes
  - [E0600](0600-arri-sdk-debayer-afa-texture-and-anr-pipeline.md) — ARRI SDK v7.1.1 (`PrepDebayerXTC`, `ada5Hw`/`ada5Sw`/`ada7Hw`, `ALEV4_AFATexture_09T`, `AfaSmoothLuma/Chroma_09T`, `ANR2`)
  - [E0601](0601-av-cam-sigitp-bayer-detail-and-pqd-stage-map.md) — `av-cam.bin` `calcMovBayerSetParam`, `job_YC_MovItp_CalcParam_Sync`, `PP_Detail*` packing, and `CamCore` MMIO `0xFC000000`
  - [E0602](0602-sabin-dcv-psf-and-spica-tap-mtf-vs-arri.md) — `/usr/sabin/dcv_psf_table_movie*.bin` and `spica_musashi_mov_4k_*` vs. ARRI MTF

---

## 1. Combined ARRI Emulation Architecture on ILCE-6400

Combining the color/tone layer (`E0505`) with the spatial/debayer/texture findings (`E0600`–`E0602`) yields a complete 4-layer map for emulating ARRI rendering on `CXD90045`:

| Layer | ARRI Reference Behavior (`E0600`) | a6400 Hardware / Firmware Surface (`E0505`, `E0601`, `E0602`) | Actionable Tuning Target |
| --- | --- | --- | --- |
| **1. Color & Tone** | LogC3/LogC4 + AWG3/AWG4 + Classic Rec.709 | `EC_Gamma` rec 31 (`0x64427840`), `B8` matrix (`0x6444b1d4`), `5×9×9` YCbCr 3D LUT | Already implemented in `Arrivederci` & `a6400-lut-converter` (`E0505`). |
| **2. Debayer & Shadow Damping** | `ada7HwKernel`: `G_avg < 1024` Laplacian damping; `[1,2,1]/4` chroma LP + gated `(G - G_lp)` with `4×` penalty `< 2048` | `PP_Detail*` (`+0x1c6..+0x1cb` packed at `0x6383d1d8`, `0x6384aa98`), `MovItp` (`0x6396c980` → `r0+0x2016c`), `SigItpBayerSetter` (`0x644fa368`) | Suppress digital edge peaking (`PP_DetailLevel` / `BWBalance` / `Limit`) and raise shadow coring (`PP_DetailCrispening`). |
| **3. Deconvolution & Micro-Contrast** | `ALEV4_AFATexture_09T_0.1.0` (`table_a` smooth, `table_b` contrast-gated mid-band boost with Nyquist `0.0854`) | `/usr/sabin/dcv_psf_table_movie_bayer.bin` & `movie_4k.bin` (selector `0x6395c842`, zero-sum `+7812`/`-1520` delta kernel) | Attenuate `dcv_psf` negative lobes in RAM/overlay to eliminate Sony's deconvolution ringing. |
| **4. 6K→4K Chroma & Luma Scaling** | `GENERIC_AfaSmoothChroma_09T_0.0.7` (`MTF50 = 0.0696 cpp`) + `RenoiseLuts` | `spica_musashi_mov_4k_y_tap_step1/2.bin` (`48 KB`) & `c_tap_step1/2.bin` (`40 KB`, 15-bit two's complement) | Smooth chroma `c_tap` roll-off to suppress 4:2:0 color moiré and match ARRI chroma roll-off. |

---

## 2. Next Verification Steps (Hardware / Paired Capture)

1. Locate the exact runtime RAM buffers populated by the `dcv_psf_table` loader (`0x6395c97c`) and `spica_musashi_mov_4k` loader in a read-only live camera observation (`tools/camera_debugger.py`) in 4K Movie mode.
2. Perform a paired chart + resolution-wedge capture comparing stock S-Log3 vs. `Arrivederci` + attenuated `PP_Detail` / `dcv_psf` to measure edge overshoot, chroma aliasing, and shadow noise floor.
