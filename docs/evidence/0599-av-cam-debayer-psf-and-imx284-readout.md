# E0599 — `av-cam.bin` Debayering, PSF/Tap Tables, and `IMX284` Readout / Binning Survey

- **Date:** 2026-11-18
- **Target:**
  - `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked/av-cam.bin`
    - **SHA-256:** `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`
    - **Format / Base:** `ORIL` v4 raw ARM/Thumb-2 PIC, `ORIL_BASE = 0x635C6000`
  - `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/sabin/` (`dcv_psf_table_movie*.bin`, `spica_musashi_mov_*.bin`, `sa_dcv.bin`, `sa_spica.bin`)
- **Tools:**
  - Python static string, RTTI/vtable, Thumb-2 PIC xref, and binary table extraction
  - `arm-linux-gnueabi-objdump` (WSL) Thumb-2 disassembly
- **Preserved artifact:** `artifacts/analysis/arri-debayer-binning-2026-11-18/findings.json`
- **Scope & constraints:** Strictly read-only static analysis. No physical camera access and no firmware modifications.

---

## 1. Debayering (`SigItpBayerSetter`), Detail, and `/usr/sabin/` PSF / Tap Tables

### 1.1 CamCore Signal Interpolation & Detail Interfaces
- **RTTI & Vtables located in `av-cam.bin`:**
  - `Camera::CamCore::SigItpBayerSetter` (`0x63eeba81`): RTTI `0x6452b920`, vtable `0x644fa368` (`0x6375d7f9`, `0x63ccef57`).
  - `Camera::CamCore::SigItpArSetter` (`0x63eeba5f`): RTTI `0x6452b914`, vtable `0x644fa358` (`0x6375d7d9`, `0x63ccef45`).
  - Both construct via `SetterBase` (`0x6375cda4`) and act as CamCore parameter setter payloads (not CPU pixel-processing loops).
  - `YC: calcMovBayerSetParam: SetterValidBayer = %d, IsSplit = %d` (`0x63f24008`) computes Movie Bayer setter validity.
- **Picture Profile Detail (`PP_Detail*`) & `YDET` / `YKNEE` clarification:**
  - `0x63bd80d0..0x63bd8210` (`FW: LiroSeq ParamCheckErr@%s[PP_DetailMode]` at `0x63bd8110`) validates `PP_DetailMode`, `PP_DetailLevel`, `PP_DetailVHBalance`, `PP_DetailBWBalance`, `PP_DetailLimit`, and `PP_DetailCrispening` from the Picture Profile struct (`r4+0x20..0x25`) against live state (`r5+0x1c6..0x1cb`).
  - `YDET/EC_YDET.cpp` (`0x63bc62a0..0x63bc6400`, xrefs `0x63bc62e4`, `0x63bc64a8`, `0x63bc64fc`) is a **luminance (`Y`) histogram/detection** module (40-element table summed and normalized into 32 8-bit bins at `r4+0x290`), not an edge-sharpening filter.
  - `PQD_PgaDrv_DfeLeo.cpp` (`0x63be9120..0x63be92e0`, xrefs `0x63be9180`, `0x63be920a`, `0x63be928e`) is the **DFE PGA gain** driver using Q12 unity (`0x1000 = 4096`).

### 1.2 External `/usr/sabin/` PSF and `spica` Tap Coefficient Tables
- The loader/selector at `0x6395c840..0x6395c910` selects among `/usr/sabin/dcv_psf_table_*.bin` files (`0x6395c870`, `0x6395c8f8`) depending on runtime flags (`[r6, #31] == 8`, `[r8, #3052]` bits 2/4, `[r3, #18] == 1`).
- Inspected headers in `nflasha15_unpacked/sabin/`:
  - `dcv_psf_table_movie_bayer.bin` (`18,288` B, `0x4770`): sensor tag `IMX094`, `SAL50_CenGch_1`, `20170719_`, kernel sizes `(9, 7, 9, 9, 5, 5, 5, 9, 5, 5)`, `int16_t` range `[-1520, 26723]`.
  - `dcv_psf_table_movie_4k.bin` (`10,728` B, `0x29e8`): sensor tag `IMX210`, `SAL50_CenGch_3`, `20141016_2013060`, kernel sizes `(9, 7, 9, 9, 9, 0, 0, 9, 0, 0)`.
  - `dcv_psf_table_movie.bin` (`10,728` B, `0x29e8`): sensor tag `IMX210`, `SAL50 CenGch_3`, `20130607_130709`.
  - `spica_musashi_mov_4k_y_tap_step1/2.bin` (`48,000` B each) and `spica_musashi_mov_4k_c_tap_step1/2.bin` (`40,000` B each): `int16_t` coefficient tables (`[0, 32767]`) for SA-accelerated (`sa_spica.bin`) Y and C filtering.

---

## 2. `IMX284` Sensor Readout, `1H1H`/`1H2H`, and Pixel Binning / Skipping

- **Sensor Driver Identity:**
  - `Camera::Imager::IMX284::C_ImagerCtrl_IMX284` (`0x63ef95eb`, vtable `0x644fb4a0`) embeds `ImagerDrvParam_IMX284` (`0x63ef9628`, vtable `0x644fb5c8`) at `this + 0x3c0` (`0x6378d4a0`).
  - Sensor interface uses **SLVS-EC / SubLVDS** (`ComboPHY SLVS-EC`, `SUBLCNTL_DFE1`), and `0x63ee9f3e` enforces `ImagerDriveMode must be 1H1H or 1H2H`.
  - `Camera::Sync::SyncUtilityUpm::ConvertImagerMode(Camera::Imager::IMAGER_DRIVEMODE)` (`0x63f1cb6c`) and `IMG:ZIT ConvThin` (`0x63ef60c0`) handle mode translation and SA-based thinning.
- **Static `IMX284` Mode Tables (`0x6378f4e6..0x6378f548`):**
  - `C_ImagerCtrl_IMX284` initializes `ImagerDrvParam_IMX284` (`0x63790e08` -> `0x6378e5e0`) with **`count = 19` (`0x13`)** static drive modes across parallel arrays:
    - Geometry array at `0x63e129e8` (76-byte stride): `width` at `+0x1c`, `height` at `+0x3c`.
    - Ratio/timing array at `0x63e1260c` (32-byte stride): packed H/V factors at `+0x08` (`0x0101` = 1:1, `0x0103` = 3×, `0x0105` = 5×, `0x0107` = 7×, `0x0109` = 9×) and float rate/value at `+0x18`.
  - **Observed 19 modes:**
    - **Modes `0..3`, `18`:** `6000 × 4000`, `(0x0101, 0x0101)` (full 24 MP 3:2 stills readout).
    - **Modes `16..17`:** `6000 × 3376`, `(0x0101, 0x0101)` (full-width 6K 16:9 readout for 4K 24p/25p).
    - **Mode `15`:** `4896 × 2760`, `(0x0101, 0x0101)` (cropped 16:9 readout for 4K 30p).
    - **Modes `11..12`:** `1996 × 1122`, `(0x0103, 0x0103)` (3×3 binned/skipped 16:9 readout for 1080p/S&Q).
    - **Mode `13`:** `1752 × 986`, `(0x0103, 0x0103)` (cropped 3×3 readout).
    - **Modes `4..10`:** `1996 × 570` (`1×7`), `1996 × 438` (`1×9`), `1996 × 800` (`1×5`), `1996 × 1332` (`1×3`) (3× horizontal, 3×–9× vertical live-view/AF readout).
    - **Mode `14`:** `1024 × 684`, `(0x0101, 0x0101)` (high-rate 3:2 readout).

---

## 3. Assessment & Next Boundary

1. **Custom ADA-7 debayering:** Not feasible in real-time video on the Cortex-A5 CPU; CamCore demosaicing is fixed-function hardware. However, `/usr/sabin/dcv_psf_table_movie_bayer.bin`, `dcv_psf_table_movie_4k.bin`, `spica_musashi_mov_*_tap_*.bin`, and `PP_Detail*` / `SigItpBayerSetter` are concrete, tunable surfaces for edge/texture character.
2. **Pixel binning for 4K brightness boost:** The `IMX284` already uses `3×3` binning/skipping (`1996 × 1122`) for 1080p/live-view modes. A `3×3` (or `2×2`) binned readout yields `<= 2000` (or `3000`) pixels horizontally, which is below 4K (`3840 × 2160`), and `IMX284` is a standard Bayer CFA (not Quad-Bayer). Creating an undocumented sensor readout mode requires `IMX284` SLVS-EC register specs and matching `DfeCore` timing.
3. **Next boundary:** If pursuing edge/texture tuning, inspect how `dcv_psf_table_movie_bayer.bin` is loaded into RAM at runtime (`0x6395c840`) and whether `PP_Detail*` or `dcv_psf` RAM buffers can be swapped cleanly like `EC_Gamma` record 31.
