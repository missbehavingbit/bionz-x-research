# E0679 — Still `dcv_psf_table_still.bin` layout, Look Lab still-path calibration, and `StillDetail` mod

- **Date:** 2026-11-22
- **Status:** Offline verified (`bionz-core`, `bionz-raw --no-default-features`, `bionz-cli still-eval`, `StillDetail.mod` ARM ELF/SMOD v2 build); no camera access performed.
- **Related:** [E0678](0678-dcv-psf-table-layout-and-arrivederci-v5-offset-defect.md), [E0600 (ARRI)](0600-arri-sdk-debayer-afa-texture-and-anr-pipeline.md), [E0600 (Still RAW)](0600-av-cam-still-raw-develop-and-imx284-reg-writers.md), [E0601](0601-av-cam-sigitp-bayer-detail-and-pqd-stage-map.md), [E0602](0602-av-cam-stage-encode-jpeg-main-yc.md).

## 1. Inputs & Identities

- `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/sabin/dcv_psf_table_still.bin` — 57,848 bytes, SHA-256 `b3b82d875733c17f9c4193a1208a996421d16eeb97208a92939e9a0c21624a17`.
- `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/sabin/sa_dcv.bin` — 18,564 bytes (`SA_DCV__0x13`).
- `artifacts/qemu-a6400/inputs-moddev-1/av-cam.bin` (captured ILCE-6400 2.00, `ORIL_BASE = 0x635C6000`).
- Calibration & held-out ARW+JPG pairs from `C:\Users\Administrator\Pictures\Sony a6400\` (253 matched pairs total; 24 calibration + 10 held-out pairs recorded with per-file SHA-256 in `isp-studio/target/still-calib/pairs.json`).

## 2. Decoded `dcv_psf_table_still.bin` Layout (Badge: V/M)

Cross-checked against `av-cam.bin` header parser (`0x6395b07c`), interpolator (`0x6395b458`), and still selector (`0x6395cb70` / `0x6395ce68`, called from `0x639a14ca`):

- **Header (`0x00..0xF8`, 248 bytes):**
  - `0x00..0x10`: sensor `"IMX210"`, `0x10..0x20`: lens `"SAL50"`.
  - `0x40..0x44`: zone grid `(5, 4)` (20 zones).
  - `0x44..0x54`: kernel shapes `(21,13), (11,11), (11,7), (7,7)`.
  - `0x54..0x60`: block strides `1600` halfwords (`20 zones * 80 halfwords/zone`).
  - `0x70..`: 6 axis entries (`[4608, 5369, 5632, 5867, 6144, 6379]`) + channel block byte offsets.
- **Coefficient Data (`0xF8..0xE1F8`):**
  - 6 axis entries × 3 channels × 20 zones `(5×4)` × 80 `i16` halfwords (`77` taps for `11×7` kernel, centre tap index `38`, plus `3` zero-pad halfwords).
  - Stock kernels are symmetric and near zero-sum (`sum = 4..80`, centre taps `2784..8092`; entry 0 centre tap `5624` with `r=1` negative taps `-1480/-1384` and `r=2` positive overshoot `+92/+76`).
  - `DcvTable::parse`, `DcvTable::encode_still_table`, `DcvTable::coefficients_at`, `rescale_table`, and `design_natural_still_table` implemented in `isp-studio/crates/bionz-core/src/spatial.rs` and verified by `bionz-core` unit tests (exact 57,848-byte round-trip).

## 3. Still-Path Detail & NR Controls in `av-cam.bin`

- `PP_DetailMode`, `PP_DetailLevel`, `PP_DetailVHBalance`, `PP_DetailBWBalance`, `PP_DetailLimit`, `PP_DetailCrispening`, `PP_DetailHighlightDetailSet`: validated at `0x63bd8190..0x63bd82f8` (`r5 + 0x1c6..0x1cd`), packed at `0x6383d1b0..0x6383d25e` and `0x6384aa98..0x6384ab18`; GOT slot `0x645aae04` -> `0x642fdc04` (`+0x1c6 = 0x642fddca`, **Badge O/A** — not live-verified on camera, so `StillDetail.mod` performs NO RAM writes).
- Still NR paths: `CAMNRSTL` (`0x63f24f92..0x63f2526f`), `CalcCnrCapParamBase: IsoSensitivity_3Stage` (`0x63f25825`), `KKNR` (`sa_kknr.bin`, `0x63f24104..0x63f247bd`), `LFNR` (`sa_lfnr_*.bin`, `0x63f244af..0x63f24541`) (**Badge O/A**).

## 4. Calibration Results (`bionz-cli still-eval`)

Command:
```powershell
.\isp-studio\target\release\bionz-cli.exe still-eval <24 ARWs> \
  --dcv .../dcv_psf_table_still.bin --avcam artifacts/qemu-a6400/inputs-moddev-1/av-cam.bin \
  --gamma-path srgb --matrix a0 --ev 0.5 --dcv-entry 0 --dcv-q 13 --dcv-gain 1.0
```

- **24-pair calibration aggregate (`calib_q13_g10.json`):**
  - `dE_mean = 9.19`, `dE_p95 = 18.89`
  - `dE_matched (3x4 affine colour-matched) = 8.56 / 17.50` (clean aligned low-DR pairs `_DSC0392`, `_DSC0338`, `_DSC0328`: `3.12..3.63`)
  - `HF_ratio = 1.286` (low/mid ISO `<= 1250`: `~0.95`; high ISO `4000..16000`: `1.82..2.14`)
  - `emu_over = 17.86%` vs `cam_over = 19.28%`
  - `emu_under = 20.46%` vs `cam_under = 17.01%`
  - `emu_halo = 2.68` vs `cam_halo = 2.81`
- **Explained residuals:** High-ISO `HF_ratio` (`1.82..2.14`) and higher `flat_noise` (`12..18` vs camera `5..10`) come from unmodeled `CAMNRSTL`/`KKNR`/`LFNR` noise reduction and `PP_DetailCrispening`. Remaining colour `dE_matched` on high-DR or wide-angle shots comes from Dynamic Range Optimizer (DRO) local tone mapping, Sony Creative Style S-curve, and in-camera lens distortion warp (`shift` up to `[0, 23]` at 1/4 scale).

### 4b. Sensor-RGB -> sRGB (`cam_to_srgb`) + Fitted Still Creative Style Standard `FC` Curve

- `decode_raw_frame` in `bionz-raw` now extracts `cam_to_xyz_normalized()` from `rawler` and computes the D65-white-normalized 3×3 `cam_to_srgb` Q10 matrix (`base_matrix`).
- `bionz-cli still-fit-tone` fitted a 96-point 10-bit `STILL_STANDARD_FC` curve (`[0, 84, 173, 241, ..., 955, ..., 1023]`, anchored at `0`, stored in `bionz-core::STILL_STANDARD_FC`) across the 24 calibration pairs at `ev = 0.0`.
- **24-pair calibration (`calib_still_standard.json`, `--raw-matrix --matrix identity --ev 0.0 --dcv-q 13`):**
  - Unmatched `dE2000`: **`6.42 / 15.78`** (down from `9.19 / 18.89` with `srgb + a0 + ev 0.5`; clean aligned low-DR shots `_DSC0338`: `1.94 / 4.32`, `_DSC0392`: `2.70 / 5.44`, `_DSC0477`: `3.04 / 8.10`).
  - Colour-matched `dE2000`: **`6.12 / 14.38`** (within `0.30` of unmatched `dE_mean`, confirming `cam_to_srgb` + `STILL_STANDARD_FC` captures global colour and tone).
- **10-pair held-out (`heldout_still_standard.json`):**
  - Unmatched `dE2000`: **`5.82 / 13.82`** (down from `9.38 / 17.06`; `_DSC0329`: `2.05 / 4.13`, `_DSC0416`: `3.41 / 6.31`, `_DSC0455`: `4.93 / 11.64`, `_DSC0378`: `4.96 / 8.24`).
  - Colour-matched `dE2000`: **`5.15 / 12.52`** (down from `6.95 / 14.85`); `emu_halo = 2.53` vs `cam_halo = 2.49`.

## 5. Improved Natural Table & Held-Out Comparison

Generated via `bionz-cli still-design ... --strength 1.0 -o isp-studio/target/still-calib/dcv_psf_table_still_natural.bin` (zero-DC compact 5×5 Difference-of-Gaussians `DoG(sigma1=0.45, sigma2=0.90)` eliminating the stock `r=2` positive ringing ring, uniform across all 20 zones, `health=(0, 0)`).

**10-pair held-out aggregate (`heldout_stock.json` vs `heldout_natural.json`, 400×300 crops in `isp-studio/target/still-calib/crops/`):**
- **Camera JPEG:** `over = 14.13%`, `under = 13.14%`, `halo = 2.49`
- **Emulated Stock (`q=13`):** `dE_matched = 6.95 / 14.85`, `HF_ratio = 1.283`, `over = 13.13%`, `under = 12.88%`, `halo = 2.14`
- **Emulated Natural (`q=13`):** `dE_matched = 6.97 / 14.88`, `HF_ratio = 1.197`, `over = 13.36%`, `under = 13.44%`, `halo = 2.27` (still below camera JPEG `14.13%` / `2.49`), with **lower flat-area noise on all 10 held-out pairs** (e.g. `_DSC0378`: `1.65` vs `1.72`, `_DSC0549`: `10.10` vs `10.33`, `_DSC0597`: `9.53` vs `9.77`).

## 6. Offline Mod Candidate (`StillDetail.mod`) & Staging Plan

- Source: `mods/arri_isp/still_detail/{still_detail.c, make_data.py, build_wsl.sh, test_mod_unicorn.py}`.
- Built artifact: `isp-studio/target/still-calib/mod_build_v2/StillDetail.mod` (58,620 bytes, SHA-256 `983818d86be87872f37feab8790b81a78a995de9fd3c76fc6ec6e4f483bddb6b`, `hooks: 0`).
- Lifecycle: On `ON`, writes embedded 57,848-byte `dcv_psf_table_still.bin` to `/tmp_bt/mods/sabin/dcv_psf_table_still.bin` and `MS_BIND`-mounts it over `/usr/sabin/dcv_psf_table_still.bin`. On `OFF`, calls `umount2(..., MNT_DETACH)`. Logs to `/tmp_bt/mods/still_detail.log`.
- **Assumptions & Next Boundary:** Built and ELF-relocation checked (`-fpie`, zero `R_ARM_ABS32/GOT/TARGET1/PLT32`); Unicorn host execution was blocked in this headless Windows session by `ImportError: DLL load failed while importing _ctypes`. Before camera deployment: (1) run `.\venv\Scripts\python.exe mods/arri_isp/still_detail/test_mod_unicorn.py isp-studio/target/still-calib/mod_build_v2` in an interactive desktop session, (2) stage `StillDetail.mod` to the SD card Mod Loader directory, (3) toggle ON in still mode, capture ARW+JPG pairs, verify `/tmp_bt/mods/still_detail.log` shows `mount=0`, and toggle OFF to verify `umount=0`.
