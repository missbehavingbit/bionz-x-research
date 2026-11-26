# Sony ILCE-6400 (a6400) Silent Shooting Limitations & Mod Feasibility

This document records the stock limitations of **Silent Shooting** (fully electronic shutter) on the Sony **ILCE-6400 (`CXD90045` / `IMX284`)** and evaluates the feasibility of lifting each restriction via RAM-only `Mod Loader v2` (`SMOD v2`) patches.

---

## 1. Stock Silent Shooting Limitations on ILCE-6400

### A. Firmware-Disabled Functions (UI / Exclusion Engine)
When `[Silent Shooting]` is set to `[On]` in the stock firmware, the following features are blocked or forced off:
- **Picture Profile** (`PP1`–`PP10`: S-Log2, S-Log3, HLG, Cine1–4, etc.)
- **Picture Effect** (Toy Camera, Pop Color, Posterization, Partial Color, etc.)
- **Auto HDR** (and DRO/HDR interactions)
- **Multi Frame NR** (Multi-Shot Noise Reduction)
- **Exposure Mode Lock:** `[Silent Shooting]` can only be selected in **P / A / S / M** modes (locked out in Auto, SCN, Sweep Panorama, etc.)
- **Flash Shooting** (built-in flash and Multi Interface Shoe external flash)
- **BULB Shooting**
- **Long Exposure NR** (dark-frame subtraction)
- **e-Front Curtain Shutter** (superseded by full electronic readout)
- **Superior Auto Img. Extract.**

### B. Performance & Bit-Depth Limits
- **12-bit RAW cap:** Still RAW drops from 14-bit (mechanical/EFCS) to **12-bit** in Silent mode.
- **Burst rate cap:** Maximum continuous shooting drops from **11 fps** (`Hi+` mechanical) to **8 fps**.
- **Shutter speed ceiling:** Maximum shutter speed remains **1/4000 s** in standard stills modes despite having no mechanical curtain.
- **Startup delay:** Camera power-on to first shot takes ~0.5 s longer.

### C. Hardware / Sensor Physics (`IMX284` Non-Stacked CMOS)
- **Rolling shutter distortion:** Full-sensor 24 MP electronic readout takes ~30–35 ms (~1/30 s), compared to ~3–4 ms mechanical curtain travel.
- **Flicker / LED banding:** Artificial pulsed lighting (LED, fluorescent) causes horizontal brightness/color bands.
- **Bright light source clipping artifact:** Very bright light sources at low ISO can darken/clip on the monitor.
- **Not 100% silent:** Aperture blades, lens AF actuators, and special operations (e.g., Custom WB white capture, Face Registration) can still actuate mechanically.

---

## 2. Firmware Root Cause (`libObj.so` Exclusion Control)

Static analysis of the captured `ILCE-6400` (`2.00` layout) `/usr/lib/libObj.so` (reference base `0x05d18000` in [reference_layout_ilce6400.txt](../tools/native_camera/mlv2/reference_layout_ilce6400.txt)) establishes that feature mutual-exclusion rules are evaluated centrally by `ExcMgr`:

- **Master exclusion evaluator:** `libObj.so + 0x00c708a0` (reference VA `0x069888a0`, vtable entry `0x00c708a1` at `0x1368db8`).
  - Signature: `uint32_t CheckExclusion(void *self, uint32_t src_param, uint32_t src_val, uint32_t dst_param, uint32_t dst_val, uint32_t *out_buf)`
  - Return `0`: no exclusion (feature allowed).
  - Return `0x3a` (`58`) or `1`: exclusion active (`out_buf` populated with forced value/reason).
  - Dispatches `src_param == 0x9983` (`PARAMID_SILENT_SHUTTER_MODE`) to `0xbe4600` (which handles Silent Shutter ON `0x451` / OFF `0x450` at `0xbf53d0..0xbf6132`) and `src_param == 0x95c8` (`PARAMID_EXPOSURE_MODE`) to `0xb02acc`.
- **ObjMsg parameter ID encoding:** `ObjMsgParamID = 0x9500 + PARAMID_table_index` (verified against `libObj.so` pointer table at `0x130104c`, 2,854 entries):
  - **Silent Shutter:** `0x9983` (`PARAMID_SILENT_SHUTTER_MODE`), `0xa013` (`PARAMID_EXC_SILENT_SHUTTER_MODE`)
  - **Picture Profile:** `0x9a53` (`PARAMID_PP_MODE`), `0x9673..0x9691` (`PARAMID_PP_COLOR_MODE_TYPE`..`PARAMID_PP_DETAIL_HIGHLIGHT_DETAIL`), `0x9a47..0x9a52` (`PARAMID_PP_COLOR_CORRECT_*`)
  - **Picture Effect:** `0x9754` (`PARAMID_PICTURE_EFFECT`), `0x9a19` (`PARAMID_PICTURE_EFFECT_GAMMA_MODE`)
  - **Auto HDR / DRO:** `0x95ce` (`PARAMID_HDR_LEVEL`), `0x9818` (`PARAMID_AUTO_HDR`), `0x95cb` (`PARAMID_DRO_LEVEL`)
  - **Multi Frame NR:** `0x951e` (`PARAMID_ISO_SPEED_RATE`), `0x9811` (`PARAMID_MULTI_FRAME_NR`), `0x9989` (`PARAMID_MULTI_SHOT_NR_LEVEL`), `0x9999` (`PARAMID_MULTI_FRAME_NR_LEVEL`)
  - **Exposure / Recording Mode (P/A/S/M lock & Auto/SCN):** `0x95c8` (`PARAMID_EXPOSURE_MODE`), `0x95bd` (`PARAMID_MANUAL_EXPOSURE_MODE`), `0x963f` (`PARAMID_PSEUDO_REC_MODE`), `0x9813` (`PARAMID_SCENE_SELECT`), `0x9b7f` (`PARAMID_CAMERA_RECORDING_MODE`), `0xc00d` (ExcMgr master shoot mode forcing `0x9983` off in Auto/SCN), `0xc0f1` (ExcMgr mode dial)
  - **Preserved stock (not bypassed in Tier 1):** `0x956f` (`PARAMID_STROBE_MODE`), `0x9f62` (`PARAMID_STROBE_WIRELESS_MODE`), `0x95a4` (`PARAMID_LONG_EXP_NR_MODE`), `0x9b50` (`PARAMID_FLICKERLESS_MODE`)

### UI Grey-Out Layer (`libSysDef.so` `DefInh::sm_refTbl`)
Bypassing `libObj.so` `ExcMgr` alone keeps the active settings working when enabled beforehand, but menu items stay greyed out in `viewUnified2.so` (`WrapperSettingUtil::isInhOn` → `MWF::InhInfo::SubMakeInhFactorMsg` at `libMWF.so + 0x1e16c`), which reads `libSysDef.so`'s writable `DefInh::sm_refTbl` (`libSysDef.so + 0x000208b8`, ref VA `0x056918b8`, `2190 × 160` `uint32_t` words) live on every check:
- **Five Silent Shooting `FactorId`s:** `0x0a83` (`VALUEID_SILENT_SHUTTER_ON`), `0x0de2`, `0x114f`, `0x1296`, `0x13bb` across **69 target `FeatureId`s** (Picture Profile `0x509..0x50a`, PP sub-items `0x7a2..0x7b7`, Picture Effect `0x379`, `0x37b..0x387`, `0x3e8..0x3e9`, Auto HDR/DRO `0xb6`, `0xb8..0xc4`, MFNR/ISO `0x36d..0x376`, `0x41b..0x41f`) = `345` bits.
- **Auto / Superior Auto / SCN / Panorama `FactorId`s on Silent Shooting (`FeatureId 0x0832`):** **80 factors** (`0x094b..0x0962`, `0x0967..0x0968`, `0x0b1f..0x0b4b`, `0x0b8b`, `0x0b8d`, `0x0cdb..0x0ce1`) = `80` bits.

---

## 3. Potential Mods & Feasibility Backlog

### Tier 1 — Implemented & Hardware-Verified (`SilentShutterUnlock.mod`)
- **Scope:** Bypass `ExcMgr` mutual-exclusion checks between Silent Shutter (`0x9983`, `0xa013`) and **Picture Profile**, **Picture Effect**, **Auto HDR / DRO**, **Multi Frame NR**, and **Exposure / Shoot Mode (`0x95c8`, `0x95bd`, `0x963f`, `0x9813`, `0x9b7f`, `0xc00d`, `0xc0f1`)**, plus reversibly clear the `425` verified `DefInh::sm_refTbl` grey-out bits (restored byte-for-byte on `OFF`).
- **Feasibility:** **Verified.** All five are post-readout ISP/UI software gates in `libObj.so` / `libSysDef.so` / `viewUnified2.so`; `av-cam.bin` accepts Silent Shooting with these modes active.
- **Implementation:** [mods/silent_shutter_unlock/](../mods/silent_shutter_unlock/README.md) (RAM-only `SMOD v2`, relocatable via `RLC1`, SHA-256 `951e7417197bcd609fdd7515e1eb8fb14e23f5d5919c4e974d150be8bc540d0e`).
- **Physical hardware verification (`ILCE-6400` `2.00` captured layout, RAM-only):** Verified live via Mod Loader v2 — Picture Profile (+ sub-items, main menu & C1), Picture Effect, Auto HDR / DRO, and Multi Frame NR are all un-greyed and controllable while Silent Shooting is ON, and Silent Shooting can be enabled and shot in Auto mode.

### Tier 2 — Candidate: Faster Electronic Shutter Speeds (> 1/4000 s)
- **Goal:** Extend electronic shutter ceiling from 1/4000 s to 1/8000 s or 1/16000 s.
- **Feasibility:** **Moderate–High.** Electronic integration time is controlled by `IMX284` shutter registers rather than mechanical curtain travel. Requires locating the shutter-speed clamp tables in `viewUnified2.so`, `libObj.so`, and `av-cam.bin` (`IMX284` exposure writer) and confirming sensor register acceptance without banding/clipping regressions.

### Tier 3 — Candidates: Deep Sensor / Sequencer Mods
1. **14-bit Single-Shot RAW in Silent Mode**
   - **Feasibility:** **Low–Moderate.** Sony selects a 12-bit `IMX284` readout mode for Silent Shooting to reduce rolling shutter. Unlocking 14-bit requires switching the silent-capture `reglist` / FE / `CAMOUT` configuration in `av-cam.bin` to the 14-bit readout mode while preserving `Lost CAMOUT_V` / `S-Ele Refresh` frame-commit consistency (see [imaging-pipeline.md](map/imaging-pipeline.md)). Trade-off: slower sensor readout (more rolling shutter).
2. **Silent BULB Mode**
   - **Feasibility:** **Low–Moderate.** Requires removing the BULB exclusion and ensuring `av-cam.bin` sequencer holds electronic integration open without triggering `S-Ele Refresh` frame watchdog timeouts.
3. **Silent Flash Sync Unlock**
   - **Feasibility:** **Software Low–Moderate / Practically Limited.** Bypassing the flash exclusion (`0x956f`, `0x9f62`) and strobe trigger block in `av-cam.bin` may allow firing a flash electronically, **but physical sensor rolling readout (~30–35 ms) limits full-frame flash sync to ~1/15–1/25 s or slower**; faster shutter speeds will produce partially illuminated dark bands.

### Not Feasible / Counterproductive
- **Long Exposure NR in Silent Mode:** Requires closing the mechanical shutter for an equal-duration dark frame, which contradicts silent operation.
- **> 8 fps Continuous Burst in Silent Mode:** Capped by full-frame 24 MP `IMX284` electronic readout bandwidth and AF/AE cycle time.
- **Eliminating Rolling Shutter / LED Banding:** Inherent physical limitation of the non-stacked `IMX284` sensor.
