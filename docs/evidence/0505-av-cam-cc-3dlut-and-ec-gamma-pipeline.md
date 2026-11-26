# E0505 — `av-cam.bin` `CC:` 3D LUT and `EC_Gamma` Pre-8-Bit Pipeline Analysis

- **Date:** 2026-11-14
- **Target:** `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked/av-cam.bin`
  - **SHA-256:** `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`
  - **Size:** `16,674,456` bytes (`0x635C6000`–`0x645ACE98`)
  - **Format / Base:** `ORIL` v4 raw ARM/Thumb-2 PIC, `ORIL_BASE = 0x635C6000`
- **Tools:**
  - Ghidra `12.1.4` via REA (`rea.mjs --mcp` in WSL Ubuntu, `format=raw-binary`, `architecture=arm`, `entry_point=0x635C6000`, `REA_GHIDRA_DISABLED_ANALYZERS="Decompiler Parameter ID"`)
  - Capstone `5.0.7` (Thumb-2 disassembly verification)
- **Preserved REA artifacts:**
  - `artifacts/tools/rea/cc-3dlut-rea-2/` (`FUN_63ad04dc`, `FUN_63ad0520`, `FUN_63ad08c8`, `FUN_63ad0930`, `FUN_63ad0fc8`, `FUN_63d8fec6`, `FUN_63d8f6dc`)
  - `artifacts/tools/rea/cc-3dlut-rea-3/` (`FUN_63acec0c`, `FUN_63ad136c`, `FUN_63ad2178`, `FUN_63ad2e3c`, `FUN_63ad4300`, `FUN_63d8edb4`, `FUN_63d8f044`, `FUN_63d90122`)
  - `artifacts/tools/rea/cc-3dlut-rea-4/` (`FUN_63b1f614`, `FUN_63ba8158`, `FUN_63dba69e`, `FUN_63ba8cf6`)
- **Scope & constraints:** Strictly read-only static analysis. No physical camera access and no firmware modifications.

---

## 1. Located Functions and String Cross-References

| String VA | String | Referencing Function |
|---|---|---|
| `0x63f6b63a` | `CC: LutMode Change SA %02X <- %02X` | `FUN_63ad0520` (`0x63ad06a4`..`0x63ad06aa`) |
| `0x63f6b65d` | `CC: LutMode Change CAP %02X <- %02X` | `FUN_63ad0520` (`0x63ad06aa`..`0x63ad06b0`) |
| `0x63f6b681` | `CC: LutMode Change MOV/MON %02X <- %02X` | `FUN_63ad0520` (`0x63ad06b0`..`0x63ad06b6`) |
| `0x63f6b77c` | `CC: Create3DLUT 0x%X > 0x%X Level:%d ConvSD:%d` | `FUN_63ad0930` (`0x63ad0ae2`..`0x63ad0aee`) |
| `0x63f6b7ab` | `CC: Create3DLUT uc_SRAM=[%d], SRAM addr=0x[%X] ` | `FUN_63ad0930` (`0x63ad0af6`..`0x63ad0b08`) |
| `0x63f6b80e` | `CC: Copy3DLUT 0x%X > 0x%X ` | `FUN_63ad0930` (`0x63ad0e20`..`0x63ad0e2a`) |
| `0x63f6b887` | `CC: Copy2DLUT 0x%X > 0x%X ` | `FUN_63ad0930` (`0x63ad0ee8`..`0x63ad0ef2`) |
| `0x63f6b900` | `CC: LUT Write Cnt:%d ` | `FUN_63ad0fc8` (`0x63ad12b2`..`0x63ad12b8`) |
| `0x63f732cf` | `SetGammaTable Error GammaSize :%d` | `FUN_63b1f614` (`0x63b1f622`) |
| `0x63f7a1e1` | `Gamma/EC_Gamma.cpp` | `FUN_63ba8158` (`0x63ba81c6`), `FUN_63dba69e` (`0x63ba8474`), `0x63ba8cf6` |

---

## 2. Verified `CC:` 3D LUT Findings

### 2.1 ISP Pipeline Position & Color Domain
- `FUN_63d8fec6` (called by `MOV/MON` handlers `FUN_63acec0c`, `FUN_63d8edb4` and `SA/CAP` handler `FUN_63d8f044`) executes in sequence:
  1. `FUN_63ad4418()` — selects `LutMode` (`cc_out + 4`) via `FUN_63ad396c` from CC tuning table (`+0x150..+0x15d`).
  2. `FUN_63ad2178()` — computes 8-channel / 4-quadrant gain & phase color adjustments (`local_44[8]`).
  3. `FUN_63ad2e3c()` — computes the **3×3 color matrix** (`local_48[9]`, 16-bit `short` coefficients written to `cc_out + 0x50` / `*param_2 + 0xae`).
  4. `FUN_63ad0fc8()` — 3D/2D LUT controller (calls `FUN_63ad0520` and `FUN_63ad0930`).
  5. `FUN_63ad136c()`, `FUN_63d90122()`.
- **Color space of the 3D LUT:** **YCbCr**, not linear RGB or post-gamma RGB.
  - `FUN_63ad0930` (`ConvSD == 1`) reads the grid axis tables at `0x63e69562` and `0x63e6956c`:
    - Luma `Y` (5 points): `[0, 64, 128, 192, 256]`
    - Chroma `Cb` (9 points): `[-128, -96, -64, -32, 0, 32, 64, 96, 128]`
    - Chroma `Cr` (9 points): `[-128, -96, -64, -32, 0, 32, 64, 96, 128]`
  - `ConvSD == 1` applies a Q14 fixed-point YCbCr matrix (`0x4000`, `0xd17`, `0x6eb`, `0x3f45`, `-0x71e`, `-0x4b1`, `0x3ef2`) with non-linearization tables `0x63ad049c` and `0x63ad0460` (BT.601 ↔ BT.709 conversion).

### 2.2 `MOV` + Picture Profile / S-Log3 Activity (`FUN_63ad0fc8`)
- `FUN_63ad0fc8` reads `LutMode = *(byte *)(cc_out + 4)`:
  - `0xFF`: `cc_out + 0x68 = 0`, `RefAdr = 0` (disabled).
  - `(LutMode >> 5) & 3 == 0` and `(LutMode & 0xF) <= 2`: **3D LUT** (`cc_out + 0x68 = 1`).
  - `(LutMode >> 5) & 3 == 1` and `(LutMode & 0xF) <= 2`: **2D LUT** (`cc_out + 0x68 = 2`).
- `cc_out + 0x0c` (`1` = enabled, `0` = bypassed) is cleared to `0` if:
  1. `*(short *)(cc_out + 0x68) == 0 && *(char *)(*param_1 + 0x30) != 1`,
  2. `*(byte *)(*param_1 + 0x28) < 0x1c` matches bitmasks `0x3400000` / `0xc000000` with tuning flags `*(iVar4 + 0x11)` bits 6/7,
  3. On `MOV/MON` (`*(char *)(param_1[1] + 6) != 1`), the SRAM bank flag `*(param_2 + 0x18)[uc_SRAM] == 0`,
  4. `*(char *)(*param_1 + 0x190) != 0`.
- `LutMode` values come from runtime-populated CC tuning tables (`+0x150..+0x15d`), not hardcoded constants in `.text`.

### 2.3 SRAM Table Format, Grid Size, and Address
- **Grid size:** **`5 × 9 × 9 = 405` (`0x195`) entries** (5 `Y` × 9 `Cb` × 9 `Cr`), `405 × 4 = 1620 = 0x654` bytes per bank. (Not `17³` or `33³`.)
- **Permutation:** `FUN_63ad04dc` maps `(y, cb, cr)` via `uint16_t[405]` at `0x63e69238` (`min=0`, `max=404`, `405` unique indices).
- **Entry format (`FUN_63ad04dc` & `FUN_63ad0930`):** Each 32-bit word packs **three signed 7-bit deltas** clamped to `[-64, +63]` (`0xffffffc0..0x3f`):
  - `word32 = ((ΔY & 0x7F) << 16) | ((ΔCb & 0x7F) << 8) | (ΔCr & 0x7F)`
- **Parameters:**
  - `Level:%d` = `*(byte *)(*param_1 + 0x29)`: `1..7` scales deltas by `(Level * delta) >> 3`; `>= 8` copies unscaled (when `ConvSD == 0` and `*param_1 + 0x30 != 1`).
  - `ConvSD:%d` = `*(byte *)(cc_out + 0x69)` (`0` or `1` from `FUN_63ad08c8`): enables BT.601 ↔ BT.709 YCbCr conversion in `Create3DLUT`.
  - `uc_SRAM=[%d]` = `*(byte *)(*param_1 + 0x222)`: ping-pong bank index (`0` or `1`) in `MOV/MON`.
- **SRAM address:** Dynamically allocated in `FUN_63ad0808` via `0x63940be4` (`0x39d` units) and mapped via `0x63c01d7c`, stored in `*(param_2 + 0x10) + 0x6b8` (`MOV/MON`, indexed by `uc_SRAM * 0x654`), or `*(param_2 + 0x10) + 0x64` (`CAP`).

### 2.4 `RefAdr` / `BufAdr` Sources and Overwriting
- **`RefAdr` pointers (GOT base `0x645a21e4`, pointing into `.bss` `0x6449xxxx`, zero-filled in the static `.bin`):**
  - 3D LUT (`cc_out + 0x68 == 1`): `0x645a4d64 -> 0x64490025` (`0`), `0x645a9cd8 -> 0x64490659` (`1`), `0x645a6ba8 -> 0x64490cf9` (`2`).
  - 2D LUT (`cc_out + 0x68 == 2`): `0x645a8fa4 -> 0x64491461` + `(LutMode & 0xf) * 0xa2` (`9×9×2 = 162` bytes).
- **Overwriting:** `FUN_63ad0fc8` invokes `FUN_63ad0930` whenever `FUN_63ad0520` detects a parameter/mode/sync change (`LutMode`, `Level`, `ConvSD`, 6-axis `+0x30..+0x37`, `uc_sync`/`IsSplit`) or during `MOV/MON` state transitions (`cVar7 == 1`), rewriting the `0x654`-byte buffer.

---

## 3. Verified `EC_Gamma` Pre-8-Bit Findings (`FUN_63b1f614`, `FUN_63dba69e`, `FUN_63ba8158`)

- **`SetGammaTable` (`FUN_63b1f614`):**
  - Enforces exact table size **`0x60` = 96 entries**, **`uint16_t` each** (`0xc0 = 192` bytes).
  - Copies the 192-byte table to a staging buffer (`DAT_63b1f674 + 0x63b1f642`), translates both addresses via `FUN_63c01d7c(..., 0x12, 0)`, and programs hardware via `FUN_63b1ac74(0xd, ...)`.
- **`Gamma/EC_Gamma.cpp` (`FUN_63dba69e`, `FUN_63ba8158`):**
  - Selects among up to `0x24` (`36`) gamma curves (`param_3 + 0x639` clamped `<= 0x23`) depending on shooting mode, Picture Profile, Creative Style, and S-Log2/3 flags (`*param_1 + 0x13c`, `+0x634`, `+0x640`, `+0x648`, `+0x83c`, `+0x1bd`).

---

## 4. Technical Conclusion on Feasibility

1. **Direct `SLog3 -> custom 3D LUT (33³/17³ RGB) -> 8-bit MOV` via `CC: 3D LUT` is NOT feasible:** the `CC:` 3D LUT hardware block operates in post-gamma/post-matrix **YCbCr** on a coarse **`5 × 9 × 9`** grid with **7-bit signed deltas `[-64, +63]`**, which cannot represent a Log-to-Rec.709 tone curve or gamut transform.
2. **LUT Decomposition before 8-bit encoding IS architecturally feasible in principle:** a `.cube` LUT can be decomposed into:
   - **1D Gamma (`EC_Gamma` / `SetGammaTable`, `96 × uint16_t`)** + **`YKNEE`** for the tone/contrast curve before 8-bit quantization,
   - **3×3 Matrix (`FUN_63ad2e3c`, `9 × int16_t`)** for gamut/primary color conversion,
   - **`5 × 9 × 9` YCbCr delta 3D LUT (`FUN_63ad0930`)** for smooth secondary look/split-tone residual adjustments within `[-64, +63]`.
   - Sharp, localized color discontinuities in an arbitrary `.cube` LUT cannot be reproduced due to the `5 × 9 × 9` grid resolution and `±63` delta clamp.

## 2026-11-16 supplement — ARRI converter and offline chart validation

The firmware subject was rehashed to the SHA-256 above. Capstone 5.0.7 decoded `FUN_63ad04dc`:
`0x63ad04de` computes `9*cb`, `0x63ad04e8` adds `81*y`, and `0x63ad04f0` adds `cr`.
The `ldrh.w` at `0x63ad04f6` indexes the permutation with that natural index. The
`str.w` at `0x63ad0516` stores the final 7-bit fields at destination plus four times that slot.
The literal at `0x63ad051c` is `0x398d48`, relative to `0x63ad04f0` for the table at
`0x63e69238`. The `FUN_63ad0930` copy path in the retained REA receipt independently
reads natural-index bytes at offsets `0`, `0x195`, and `0x32a`. All 405 slots of each
export were checked directly from the firmware table against packed RefAdr bytes. Both
A6LT containers round-tripped byte-for-byte. This verifies software layout for the
identity-matched firmware and the unscaled copy path.

From `a6400-lut-converter` on Windows with Python 3.14, NumPy 2.4.3 and Pillow 12.2.0:

```powershell
python -m lut_cli --arri both --classic-cube "$env:TEMP/ARRI_LogC3-to-Gamma24_Rec709_D65-Classic_33.cube" --raw --charts -o ../artifacts/tests/arri-isp-final-2026-11-16
```

The Classic LUT was obtained from ARRI's LogC3 v1.2.1 download and recorded by SHA-256
`29212a71808ab22f2819b904b3730fa4231b5841d883e43ec17610096dc38879`. LogC3 uses
Sony code decoding, a linear S-Gamut3.Cine-to-AWG3 matrix and the EI 800 exposure formula.
Classic composes that transform with the official cube. Both fits use tetrahedral interpolation.
The export directory holds raw streams, A6LT, chart PNGs and JSON receipts including output
range, CIE76 reference space and artifact hashes. The Classic source cube stayed outside the
repository.

| Preset | ColorChecker mean / max | Gray mean / max | Skin mean / max | Highlights mean / max | Pooled mean / max | Projected / at rail |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| LogC3/AWG3 | 1.65 / 3.25 | 0.33 / 1.71 | 1.41 / 4.65 | 5.33 / 16.53 | 0.84 / 16.53 | 0 / 5 |
| Classic Rec.709 | 1.19 / 2.98 | 0.21 / 1.63 | 1.17 / 2.55 | 1.83 / 7.60 | 0.44 / 7.60 | 0 / 0 |

The fixture is a nominal 24-patch ColorChecker, 512 neutral samples, 45 synthetic skin samples
and 48 saturated highlights. The simulated Classic gray codes at four successive two-stop
exposures were 0.397, 0.676, 0.904 and 0.991 versus the reference 0.396, 0.675, 0.907 and 0.988.
Mean Classic highlight chroma magnitude decreased from 63.37 to 21.22 modeled YCbCr units,
versus 62.70 to 22.01 for the reference. PNG inspection showed the progression in both rows
and visible differences on some saturated LogC3 patches. The trilinear trial recorded 2/405
projected LogC3 nodes. The tetrahedral result uses the same model for both presets and came
in at 0/405 for each. The LogC3 export still has five Y components exactly at a signed
rail, recorded separately from projection.

Ten focused converter tests passed, covering independent slot addresses, RefAdr offsets,
range rejection, Q10 arithmetic, odd-size 4:2:0, interpolation and Lab-related transforms.
The wider project suite did not complete cleanly. Its prescribed environment failed to load
`_ctypes`. A system-Python retry with the existing dependencies ran 1,896 tests and reported
6 failures, 52 errors and 15 skips. Its failures include stale status documentation and its
errors include temporary-file access denials. The retry log is under
`artifacts/tests/arri-isp-presets-2026-11-16/project-suite.log`.

No physical capture was made. The next hardware boundary is a matched source chart and movie
capture with identified PP and LUT state, measured gamma scaling and interpolation, and
confirmed output range and 4:2:0 chroma siting.

## 2026-11-16 supplement — built-in ARRI models and raw preview

The Rec.709 fitting target no longer requires the official cube. `ArriReference.rec709()` decodes
LogC3, applies the AWG-to-Rec.709 matrix from the published primaries, samples a 24-knot shoulder
on neutral gray, and lifts weaker channels toward the peak after display code 0.45. Wide gamut is
unchanged: S-Log3 to EI 800 LogC3 in ALEXA Wide Gamut. The GUI exposes both with **ARRI Wide Gamut**
and **ARRI Rec.709**. `--classic-cube` remains an optional override and still checks SHA-256
`29212a71808ab22f2819b904b3730fa4231b5841d883e43ec17610096dc38879`.

Against that official cube, on the same ColorChecker, gray, skin and highlight samples, the built-in
Rec.709 model measured CIE76 mean/max of 2.14/7.34, 0.12/0.70, 1.87/4.57 and 10.57/34.95. The
highlight gap is the weaker channels, which the cube moves closer to white.

The ISP fit of the built-in targets, from `a6400-lut-converter`:

```powershell
python -m lut_cli --arri both --raw --charts -o ../artifacts/tests/arri-isp-builtin-2026-11-16
```

| Preset | ColorChecker mean / max | Gray mean / max | Skin mean / max | Highlights mean / max | Pooled mean / max | Projected / at rail |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| LogC3/AWG3 | 1.65 / 3.25 | 0.33 / 1.71 | 1.41 / 4.65 | 5.33 / 16.53 | 0.84 / 16.53 | 0 / 5 |
| Built-in Rec.709 | 1.86 / 5.98 | 0.26 / 1.37 | 1.21 / 2.88 | 5.01 / 15.25 | 0.75 / 15.25 | 0 / 0 |

These ISP numbers are distance to the built-in model. They do not include the model-to-cube gap above.
Chart PNGs place the reference above the 8-bit 4:2:0 simulation. Wide-gamut charts are shown through
the built-in Rec.709 view so the monitor picture is comparable; the exported preset remains LogC3.

The GUI loads PNG, JPEG, TIFF and raw files, including ARW, through `lut_core.image_source`. Stills
are inverse-encoded as sRGB. Raw files use LibRaw (`rawpy` 0.28.0 in this run): half-size develop,
camera white balance, linear Rec.709, highlight blend, no auto-bright. Eleven converter tests passed,
including the shoulder, highlight-chroma direction, PNG load and rejection of a corrupt ARW. No
camera capture was made.

## 2026-11-16 supplement — integer ISP stages and unblended XYZ develop

The forward model now keeps the gamma lookup and the Q10 matrix in integer codes. The 96-entry
table is interpolated with a Q16 segment fraction and rounded to the table scale. The matrix is
`(R*m0 + G*m1 + B*m2 + 512) // 1024`, then clamped to the gamma full scale. YCbCr is the BT.709
conversion of those codes, with Y in `[0, 256]` and Cb/Cr in `[-128, 128]`. The 5×9×9 blend uses
the stored int8 nodes. This is still a model of the documented table sizes: the firmware capture
does not yet prove the interpolator fraction or the matrix rounding mode.

Raw preview develop changed to LibRaw CIE XYZ, camera white balance, and highlight mode Clip.
The selected source primaries convert that XYZ. Exposure, including the embedded-JPEG midtone
scale, is applied in that linear gamut and hard-clipped to the S-Log encode range. On
`_DSC1093.ARW` the JPEG match scale was 2.35. LibRaw's camera-to-XYZ matrix is not Sony's
internal S-Gamut matrix.

The frame CIE76 now runs the reference through the same 8-bit 4:2:0 encoder as the simulation.
On that ARW, at +0.6 EV, the shared-encoder Cube vs ISP figures were:

| Preset | Frame mean / max | Chart highlights mean / max | Projected / at rail |
| --- | ---: | ---: | ---: |
| LogC3/AWG3 | 0.67 / 6.47 | 4.94 / 14.56 | 0% / 0% |
| Built-in Rec.709 | 0.77 / 3.12 | 5.27 / 13.09 | 0% / 0% |

Comparing the full-resolution reference with the 4:2:0 simulation on this frame still reaches
about 22 CIE76, on a 2×2 block that mixes a saturated pixel with a neutral neighbor. That number
is the chroma subsampling, not a residual the 5×9×9 table can remove. The chart highlight max
stays above 8 because the weak channel of a saturated color still asks for more than the signed
7-bit residual after the 1D curve. Thirteen converter tests passed. No camera capture was made.

## 2026-11-16 supplement — physical MSC preflight for a conditional ARRI hook

- The read-only debugger used MSC ext-command protocol 2. PID 149 was `im.elf`.
  `viewUnified2.so` was at `0x155e6000`, and there was no mapping in the `0x63xxxxxx–0x64xxxxxx`
  window in that process. `/proc/modules` showed `liro` live. E0521 independently shows the
  LIRO loader owning the preloaded av-cam image.
- The firmware file on the camera, `/system/av-cam.bin`, is 16,803,048 bytes. The retained
  camera image has SHA-256 `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`.
  This differs from the packaged E0505 subject. The camera had no `sha256sum`; bounded
  regular-file reads matched the retained subject at the ORIL header, gamma setter and
  dispatcher, color matrix entry, LUT controller, CC coordinator, LUT packer and entire
  810-byte permutation. These observations identify sampled **file bytes**. Runtime
  instructions and state were outside the scope of this read.
- Capstone identified the CC coordinator at `0x63da2274` in the retained image. It calls
  the matrix calculator `0x63ae2714` at `0x63da228a`, the LUT controller `0x63ae08a0` at
  `0x63da2292`, and then further CC functions. Elsewhere, the Thumb BL at `0x63b22e8a`
  calls the gamma setter `0x63b2eeec`. The setter requires 96 samples, copies `0xc0` bytes,
  translates both buffer addresses and calls the programming function with operation
  `0x0d` at `0x63b2ef3a`.
- The retained packer at `0x63adfdb4` uses the table at `0x63e7fe9c`. The live file
  table matches the packaged image's 405 entries byte-for-byte. The existing exports
  therefore agree with the camera file's observed eSRAM ordering. This result does
  not establish runtime buffer address, inactive bank, bank ownership, or safe timing.
- Receipts are under `artifacts/tests/arri-camera-preflight-2026-11-16/`.
  `msc-bounded-identity.txt` and `msc-final.txt` contain bounded reads, monotonic uptime,
  responsive debugger and the original `/tmp_bt` listing. The first attempt was blocked
  by the host COM generation cache. The retry used in-memory COM wrapper generation.
  No files or patches were written to the camera. Existing `/tmp_bt` files were left alone.

The existing SMOD loader writes a series of executable patches in `im.elf` and presents
its own ON/OFF state. It provides neither a LIRO code installer nor an atomic three-bank
ISP commit. The CC coordinator is downstream of separate calculations, and the gamma
setter translates and programs addresses on its own path. These findings leave three
specific boundaries before activation:

1. Resolve the source and latch point of profile identity, S-Log3/S-Gamut3.Cine, Knee Off,
   and Neutral Color Phase/Saturation on the LIRO side. Capture stock refresh coverage
   for PP switching, ISO, white balance and playback return using a matched trace.
2. Establish whether the upstream EC_Gamma input can be composed with the factory
   curve to reproduce the converter's normalized S-Log3 input. Measure knots, working
   scale, and the relationship of gamma and CC updates to the same frame generation.
3. Identify a firmware-owned commit and original-table recovery path across all three
   layers, including bank ownership, cache maintenance, and completion fence. Then
   bind a Mods control message and display status from acknowledged firmware state.

The component under `mods/arri_isp/` implements preset validation and conditional copying
only into private staging. Both final exports passed native host checks and it also
built as a freestanding Thumb ARM object without unresolved runtime symbols via
`wsl.exe --exec bash mods/arri_isp/build_wsl.sh artifacts/tests/arri-camera-arm-stage-2026-11-16 artifacts/tests/arri-isp-final-2026-11-16`.
Repeated calls in host tests verify stateless staging; they do not verify physical
ISO, white-balance or playback-triggered refresh. No ISP hook or SMOD was activated.

## 2026-11-16 follow-up — FC gamma publication and EC supplier

The initial E0505 description of operation 0x0d as hardware programming is superseded.
In the retained camera image (SHA-256 `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`),
the exact Thumb instructions show the following path:

- The function at `0x63bdcb70` calls `0x63dd3406` at `0x63bdcb9c` to obtain a
  pointer and length. Two veneers transfer control to the supplier at `0x63bb85ac`.
  This supplier belongs to `Gamma/EC_Gamma.cpp` (diagnostic reference at `0x63bb85d2`).
  It sets the length to 96 and initializes the pointer to a linked fallback at
  `0x63e8c888`, with override and helper paths. The fallback is 96 monotone uint16
  samples spanning 2–1023. Its file bytes match the camera. The fallback does not
  establish active S-Log3 samples, gamma knot positions or the Movie ISP scale.
- The same caller passes the pointer/length pair into `0x63dd33d6` at
  `0x63bdcbac`. The veneers lead to `0x63b2eeec`. For a 96-sample argument,
  its loop copies 192 bytes to linked staging at `0x644599d2`, requests two
  translations, and calls `0x63b2a54c` with selector 0x0d. The callee writes two
  adjacent 32-bit words in descriptor slot 0x0d at `0x64186220` and returns.
  The descriptor table starts at `0x641861b8`. The registration path at
  `0x63b2a560` references `FC/Lib/fc_lib_dds.cpp` and the log string
  `FC Set data:0x%lx add:0x%x`. The getter reached through `0x63dbabdc` at
  `0x63b2a5ac` requests a translation for the whole descriptor table.
  This establishes an EC-to-FC descriptor path. Its effect on recorded video is
  unverified. The CC tail at `0x63ae30e4` branches through a separate conditional
  calculation and writes state at output+0x30.
- A bounded Unicorn 2.1.4 run used the original retained Thumb setter and descriptor
  writer, a real Classic preset gamma table and explicit fixtures for the two
  address translations. The size-96 run made 96 halfword stores and two word
  stores, then returned with r0=1 at the synthetic return address `0x13000`.
  The size-95 negative control stopped at `0x63b2ef00` before logging with no
  non-stack writes. This replay exercised publication in CPU memory. Hardware and
  the descriptor consumer were outside the replay.
- Bounded ordinary-file reads of `/system/av-cam.bin` on the camera matched the
  original instruction samples and the entire fallback. The debugger answered
  in MSC, uptime increased and the `/tmp_bt` listing was stable. No camera
  writes, mode changes or physical address reads were issued.

Reference receipts and replay code are under
`artifacts/tools/rea/arri-live-isp-commit-2026-11-16/`. From the repository root,
run `gamma_replay.py` with system Python and the established Unicorn package.
Capstone 5.0.7 cross-reference scans list candidate direct branches; indirect
dispatch remains a separate boundary. The next executable boundary is the Movie
gamma consumer and the relationship between its buffer generation and the CC
bank switch. The 4095-scale converter exports remain simulator presets pending
evidence of the selected recording path and its working scale.

REA/Ghidra 12.1.4 opened the identity-matched raw image but its first query
exceeded the 330-second full-image analysis deadline. The timeout receipt is
`0x63da2275-failed.json`. The static conclusions above use original instructions and
the repeated bounded CPU replay.

## 2026-11-16 follow-up — external gamma control and EC working layout

This pass used the retained camera image with SHA-256
`dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`.
The bounded programs and disassembly receipts are in
`artifacts/analysis/arri-exgamma-contract-2026-11-16/`.

- The parameter handler at `0x63beb930`, paired with the check at `0x63be9c88`,
  decodes message bytes +8 and +9 and a little-endian word at +0x0a. It stores
  them as three words at state `0x6442451c` offsets +0x2c8, +0x2cc, +0x2d0.
  The paired log labels these fields ExGammaTableOn, PictureEffectGammaForceOFF
  and ExGammaTablePointer. The static dispatch record contains handler/check
  pointers at `0x645976c0` and `0x645976c8`. The transport, permissions,
  message envelope and pointer lifetime are outside the verified contract.
  Offline replay ran the original Thumb handler with logging suppressed by a
  memory fixture. Enabled and cleared messages made exactly three state stores
  and returned 1. The pointer was a fixture. It was never dereferenced or sent
  to the camera.
- EC copies those three fields at `0x63bb7e4a–0x63bb7e8e` into a work object at
  +0x6e4 (enable), +0x6e8 (force off) and +0x6ec (pointer). Intervening branches
  can override the flags and pointer. At `0x63bb7eb4–0x63bb7ed8`, two flag paths
  require a value equal to 1 and a nonzero pointer before setting work-object
  bytes +0x6dd and +0x6dc. This establishes conditional EC state, before any
  recorded-frame claim.
- Original EC routines `0x63bb7908` and `0x63bb795c` widen 96 halfwords
  into 96 words and append nine additional words from source halfwords
  96–104, in order 1,0,3,2,5,4,7,6,8. This is a 420-byte work block.
  Both paths were replayed with explicit table and copy fixtures, with all
  105 words compared. The stock pointer resolves to a linked work table at
  `0x64446124` that is zero-filled in the retained image, so runtime content
  was not inferred. The nine words' meanings and the external pointer's
  payload layout require separate evidence.
- The Picture Profile verifier identifies the paired message/state fields
  for Color Mode (+8 / +0x19c), Saturation (+0x15 / +0x1b2, signed byte),
  Hue (+0x16 / +0x1b3, signed byte), Gamma Type (+0x1a / +0x1bd),
  Knee Mode (+0x1b / +0x1be), and PP Mode (+0x35 / +0x198).
  These are parameter fields. The numeric settings, selected menu slot, and
  per-frame latch still require a matched stock trace.
- Six bounded read-only spans of the camera's `/system/av-cam.bin` matched the
  retained file, including the setter/check pair, EC control transfer and
  working-table expansion. `camera.json` records the hashes and monotonic
  uptime, an unchanged `/tmp_bt` listing and a successful MSC debugger ping.
  No camera commands from the external-gamma control path were sent.

This provides a firmware-owned gamma-control candidate and a verified EC work format.
The Movie consumer, stock gamma input samples and coupling to the CC bank switch
are still open. A 192-byte converter export alone does not supply the demonstrated
EC work block. The build and the camera remain outside live hook activation.

## 2026-11-16 follow-up — EC curve arithmetic and external control forwarding

This pass used the retained camera image with SHA-256
`dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`.
Receipts and replay scripts are in `artifacts/analysis/arri-gamma-consumer-2026-11-16/`.

- At `0x63b97a78`, firmware copies the EC work field +0x6ec to a downstream
  parameter object at +0x1f8. The enable and force fields are copied nearby
  into +0x1f0, +0x1f4 and +0x1fc. A further serializer at `0x63ba2274` copies
  a source object's +0x1f8 to a destination at +0x410. Its end-to-end object
  identity with the EC copy remains a candidate connection.
- The bounded original branch at `0x63b5bf20–0x63b5bf38` reads a +0x410
  field as a numeric selector. Values 1–13 index the retained halfword table
  at `0x63e52ca0`, yielding 0–1536 in steps of 128. Larger or zero values
  return zero at this boundary. Replay exercised 0, 1, 2, 13, 14 and
  `0x11223344`. The address-shaped fixture was never dereferenced. This is
  evidence for a numeric use of that field on the sampled branch, with the
  original external parameter's transport and possible other uses still open.
- The separate EC curve builder calls `0x63dcdf4e` at `0x63bb8b24`, passing a
  mode halfword at work+0x646, a destination, sample positions and a selected
  halfword curve. The builder reads 96 low halfwords with **four-byte stride**,
  uses each value's high bits to index a curve at 64-code intervals, and calls
  original `0x63dcdf22` for rounded linear interpolation. It writes 96 words.
  When mode equals 1 it writes the interpolated result. Otherwise it shifts
  right by 6 and increments on bit 5 only when the shifted result is at most
  1022. On the tested monotone fixture, that branch reached 1023.
- `replay.py` maps the original Thumb interpolation and divide instructions,
  an explicit monotone curve and 96 sample positions, and stops at a synthetic
  return address. The initial packed-halfword input assertion failed
  (`replay-first-failure.txt`). Using the four-byte stride seen in the
  instructions, with nonzero upper halfwords to check the load size, produced
  an exact match for 96 outputs in each mode (`replay-verified.json`).
  The fixture does not supply the active Movie mode or its stock sample positions.
- Six bounded ordinary-file reads of `/system/av-cam.bin` confirmed the selected
  instructions and selector-table bytes, including the code at
  `0x63dcdf22`. The camera answered in MSC, uptime advanced and the
  `/tmp_bt` listing matched. No camera writes or mode changes were performed.

The observed EC arithmetic provides a 10-bit output branch and a separate raw branch.
The native Movie mode selection, active sample positions and same-frame CC commit remain
open. The simulator's uniform gamma grid and 4095 scale were not changed based on this
branch alone.

## 2026-11-16 follow-up — stock EC sample positions

The retained camera image is SHA-256
`dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`.
Receipts and reproducible scripts are in `artifacts/analysis/arri-ec-knot-layout-2026-11-16/`.

- At `0x63bb8796`, the builder selects a stock record with index
  `2 * work[0x639] + work[0x636]` from the linked pointer array at `0x6458443c`.
  In the 36-by-2 domain, 36 entries have nonzero pointers. In
  `0x63bb89aa–0x63bb8aa6` the low bytes at record offset +0x180 provide nine
  subdivision exponents. The firmware reads linked segment starts
  `[0,32,64,128,256,512,1024,2048,4096]` and extents
  `[32,32,64,128,256,512,1024,2048,4096]` at `0x63e8c864` and `0x63e8c876`.
  Each segment with exponent e emits 2^e right endpoints at spacing extent
  shifted right by e. The upper position is saturated to 8191.
- Unicorn replay used the original segment instructions and integer divider,
  explicit copy/clear fixtures and the original static table bytes. All 96
  words matched predicted positions in 72 runs (two modes on every initialized
  record). Mode zero writes positions at work+0x330 and nonzero at work+0x4b0.
  The probe stops at `0x63bb8aa6`. Its first left-endpoint hypothesis failed
  (retained as `knot-left-endpoint-failure.txt`).
- `0x63bb7d76–0x63bb7da8` indexes the paired PP gamma table at `0x63f9257c`
  with the nonzero PP gamma value and the context byte at work+0x6c4.
  Both context columns map PP value 10 to curve index 28, and PP value 12
  to index 31. The diagnostic at `0x63bafe9c–0x63bafeb6` groups 28 and 31 as
  S-Log2/3. It does not distinguish the two by label. The static input
  pairs establish candidate records without a physical Movie PP selection.
  Callers at `0x63bb8da2`, `0x63bb90bc` and `0x63bb9370` explicitly
  initialize mode to zero before calling the builder. This does not prove
  which caller and output branch drive the selected Movie PP.
- An offline neutral-only comparison sampled each existing converter gamma
  curve through stock entries 28 and 31. It assumed normalized stock
  samples, piecewise linear evaluation and hold beyond the endpoints.
  Naively placing the existing 96 output values at the recovered sensor
  positions differed by 101–214 normalized 10-bit codes at the maximum.
  Composing stock gamma with the converter curve at those same positions
  reduced the model difference to 0.26–1.93 codes. The reference is a
  modeled composition of the tables, without a camera capture, matrix,
  hardware quantization or color assessment. No replacement preset was exported.
- Eight bounded regular-file reads of the camera matched the retained bytes
  at the table directory, segment arrays, PP selector lookup, stock mode
  init paths and S-Log diagnostic. The MSC debugger responded, uptime
  advanced and the `/tmp_bt` listing matched. No camera writes were made.

The primary next boundary is a stock Movie S-Log3 observation tying live PP
state to selector 31 or 28, the working mode and the CC publication fence.
The original converter outputs remain simulator-domain assets.

## 2026-11-16 follow-up — direct linear ARRI comparison

The ARRI reference now exposes `sample_linear` for calibrated, scene-linear
S-Gamut3.Cine RGB. Its S-Log3 entry point decodes Sony codes then invokes the
same reference. The input to this reference is color-calibrated RGB with a
scene exposure convention. An uncharacterized Sony sensor signal does not
supply those primaries or that convention.

Ran `python artifacts/analysis/arri-linear-path-2026-11-16/compare.py` from
the repository root. The final result is `comparison-matched-training.json`.
Both variants fit the same training colors, weights and ARRI targets. The
report retains an earlier unmatched-training run in `comparison.json`.
The matched trial compares the 629 existing validation samples with a 1023
gamma working scale, tetrahedral YCbCr residual interpolation and final
8-bit quantization. Linear trials use the recovered native positions from
candidate records 28 and 31, with 8191 modeled as exposure 16 or 32. Their
positions are identical. The source matrix for the early-matrix trial is
calibrated S-Gamut3.Cine to AWG3. It represents an assumed upstream
linear stage and does not establish a programmable camera path.

| Reference | Via S-Log3 mean / max CIE76 | Linear with post-gamma matrix, scale 16 | Linear with early AWG3 matrix, scale 16 |
| --- | ---: | ---: | ---: |
| LogC3/AWG3 | 0.77 / 14.68 | 1.69 / 16.69 | 1.46 / 12.28 |
| Official Classic Rec.709 | 0.38 / 7.92 | 0.46 / 9.29 | 0.42 / 7.37 |

Without intermediate quantization the linear reference and S-Log3 encode/decode
round trip agree to below 1e-12 in normalized RGB. Artificial quantization in
the middle at 8 bits adds up to 7.24 CIE76 for AWG3 and 1.54 for Classic, while
10 bits adds up to 2.31 and 0.47 respectively. These are isolated solid-patch
model results. The current physical Movie pipeline has no verified intermediate
S-Log3 quantization or cost from an additional pass. The original converter fits
one ISP operation chain, so removing the preparation domain alone does not
establish a frame-time reduction.

All 14 converter tests passed after directing the existing temporary image fixture
to the workspace. The broader suite reported 7 failures and 52 errors in 1896
tests. See `project-suite.log`. The new changes are limited to the offline
reference and its test. No camera session or writes were used for this comparison.

Before targeting the direct architecture, identify the stock Movie signal after
black-level correction, white balance and RGB reconstruction, its color transform,
its exposure scaling at EC_Gamma, and the CC bank publication boundary. Preserve
the original controls and use the native sample positions when that signal is known.

## 2026-11-16 follow-up — programmable RGB matrix candidate

The retained camera image is SHA-256
`dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`.
Receipts are in `artifacts/analysis/arri-front-end-matrix-2026-11-16/`.
The scope was offline Thumb analysis and read-only MSC file verification.

- The RGB Matrix parameter handler at `0x63bea660` stores nine consecutive
  little-endian halfwords from message +8 through +0x19 into the LIRO state
  at `0x6442451c` offsets +0x656 through +0x666. Message byte +0x1a is
  stored at +0x668. The checker at `0x63be9a58` compares signed values and
  labels nine fields RR, RG, RB, GR, GG, GB, BR, BG and BB, followed by
  RGBMatrixSW. The raw dispatch table contains the handler and checker
  pointers. No message was sent to the camera.
- Instructions at `0x63af746c–0x63af74b8` forward the values from those
  offsets into a parameter object at +0x80 through +0x90, with the switch
  at +0x92. At `0x63af86ea`, the consumer skips the optional multiplication
  when the switch is zero. Otherwise it calls the signed matrix helper
  `0x63da4562`, which computes the request matrix times the current working
  matrix and uses a 64-bit accumulator, adds 512 and shifts right by 10.
  The result is in-place in that work matrix. The consumer continues through
  clamp and output packing. The known CC matrix path at `0x63ae2714`
  has separate output fields. The investigated matrix's ordering relative to
  EC_Gamma in Movie mode is unverified.
- In `replay.py` the original handler and conditional consumer ran with
  explicit fixture matrices. The handler made exactly nine halfword
  stores and one switch-byte store and returned 1 for each value of the
  switch. The consumer stopped at `0x63af86fa`. With switch zero it
  preserved all nine original working coefficients. With switch one all
  nine signed Q10 coefficients matched independent calculation. This is
  a verified bypass in the calculation, with physical rollback and frame
  generation still unverified. The handler's instruction range and every write destination were bounded
  by the harness.
- `awg3-matrix-fixture.json` contains a host-only Q10 matrix for calibrated,
  linear S-Gamut3.Cine input to AWG3. Its coefficients are
  `[[998,24,2],[-91,1097,18],[-36,39,1021]]` with row sums of 1024.
  This is a numerical fixture. Use on the camera depends on the stock
  matrix's input/output domain and the verified consumer.
- Six bounded ordinary-file reads confirmed the handler, checker, forwarded
  fields, multiplication, switch branch and downstream callsite. Uptime
  advanced from 20372.70 s to 20372.86 s, the debugger responded and the
  `/tmp_bt` listing matched. The camera remained in MSC. This run made
  no camera or SD writes.

The next boundary is to identify the matrix output packet's active Movie consumer,
its signal domain and its publication order relative to the gamma and eSRAM
bank. The global RGBMatrixSW parameter by itself has no verified PP scope.

## 2026-11-16 follow-up — RGB matrix buffer lineage and capture pre-review

This pass used the retained camera image with SHA-256
`dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`.
Evidence and replay scripts are in
`artifacts/analysis/arri-matrix-publication-2026-11-16/`.

- The block at *bundle[+0x34] receives the output of `0x63af8540` at +8.
  `0x63da46cc` copies the 36-byte matrix-related record to bundle[+0x1c]+0x0c.
  `0x63da47ac` copies it back in the opposite direction. Both invoke
  `0x63da44c2`. The exact original Thumb copy of 18 halfwords was replayed
  both ways with distinct source and destination storage and fixture values.
  Two unrelated memcpy calls per direction were explicitly serviced using
  byte-copy fixtures. Both runs stopped at the synthetic return address.
  The cached block does not by itself establish a hardware transfer.
- The caller at `0x63add3e4` references
  `CC: SetPreReviewDevelopParam()` at `0x63f81fb4`, including FrameID and
  IsAsyncLV diagnostics. Its call at `0x63add474` enters `0x63ade7bc`.
  At `0x63adeace` that routine calls the CC coordinator `0x63da2274`
  with output+0x58, then tail-calls `0x63af955c` with output+0x108.
  The latter calls `0x63af88b0`, which invokes the programmable matrix
  calculation. A bounded replay of the final dispatch fragment confirmed
  both paths through the second predicate, plus its disabled case. Calls
  were fixtures and execution stopped at the builder entry. This proves
  ordered calls in capture pre-review, before any hardware claim.
- Hash-matched packaged references from the original E0505 analysis
  locate separate MOV/MON implementations at retained-image addresses
  `0x63ade4e4` and `0x63da1162`. Their pointers are adjacent at
  `0x6457ffdc`. Both use a different same-offset setup routine
  `0x63af7de0`, then reach the CC coordinator. The reviewed direct
  call chains have no edge to the programmable RGB matrix calculation.
  The broader indirect Movie dispatch and frame consumer remain
  unresolved. Do not use pre-review evidence to assert Movie availability.
- Nine bounded reads of ordinary `/system/av-cam.bin` confirmed the
  caller's log text, static callsites, shared copies and MOV/MON
  dispatch bytes. Neither runtime addresses nor device memory were
  touched. The MSC debugger answered, uptime increased from 20729.41 to
  20729.63 seconds and the pre-existing `/tmp_bt` listing matched.

The programmable branch is now linked to the capture pre-review path.
The next live Movie boundary is ownership of the separate MOV/MON
parameter bundle and its consumers. The startup/cached parameter copies
do not establish Movie applicability, same-frame gamma and CC publication,
or physical rollback. No physical update was attempted.

## 2026-11-16 follow-up — CamCore 3D LUT publisher

This pass used the retained camera image with SHA-256
`dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`.
Receipts and reproducers are in `artifacts/analysis/arri-movmon-consumer-2026-11-16/`.

- The CC producer at `0x63ae097e–0x63ae099c` prepares the table and stores
  the software request at CC block +0x18. It stores
  `sram_base + bank_index * 0x654` at +0x14. The downstream
  CamCore function `0x63bf9f2c` consumes a descriptor with these fields.
  With a resolved CamCore object and nonzero request and pointer, its
  default branch calls `0x63cd76a0` with the pointer in the second
  word of a local descriptor. The optional alternate branch copies
  exactly 0x654 bytes to the supplied destination pointer instead.
  Retained log literals label the default path `CamCore 3D-Lut table Write
  Start` and `CamCore 3D-Lut table Write End`. The name of the request
  byte does not establish a physical completion indication.
- `0x63cd76a0` writes 1 at object base +0x15d40, uses `0x63cd7550`
  to split the 1620-byte input across eight windows, then writes 0
  at base+0x15d40. The destinations are base plus
  `[0x17000,0x17200,0x17300,0x17400,0x17500,0x17600,0x17700,0x17800]`
  and lengths are `[0x12c,0xf0,0xf0,0xc0,0xc8,0xa0,0xa0,0x80]`.
  The helper takes the base from CamCore object+4. Adjacent accessor
  code reads bit 0 at base+0x15d44, but its semantics and synchronizing
  role are unverified. The bounded publisher itself does not read it.
- `camcore_replay.py` runs the original Thumb publisher, getter,
  eight-way dispatcher and control-register stores with an explicit
  fixture at the generic copy veneer. The window base is a synthetic
  mapping. The input is the existing Classic Rec.709 direct eSRAM export
  (SHA-256 `1807407a1278fdaae50e3631d8ebe64af524143d2bfe9ead6467a0c85a10b451`).
  All planned copy ranges and both control-register stores were observed
  in original instruction order. Reassembling the fragments recovered
  every input byte. Execution stopped at the synthetic return address.
  This verifies CPU-side sequencing and packed-byte transport. It does
  not establish MMIO effects, active-bank selection or frame timing.
- The wrapper at `0x63dd8f60` calls a signed matrix-register writer path
  (`0x63bf9d48` to `0x63cd70e6`) before the LUT reader. This provides a
  neighboring matrix transport candidate, separate from the earlier
  capture pre-review RGBMatrixSW calculation. The stock source and
  context binding for this register packet, and the Movie gamma consumer,
  require separate proof.
- Seven bounded regular-file MSC reads matched selected producer,
  consumer, dispatcher, wrapper and string spans in `/system/av-cam.bin`.
  The debugger responded, uptime increased from 21148.04 s to 21148.20 s,
  and the `/tmp_bt` listing was stable. The probe performed no camera
  writes, mode changes or device-memory access.

The next boundary is to tie the Movie parameter owner and EC gamma register
path to the CamCore wrapper, then establish what makes simultaneous
publication observable. No uploadable hook or physical test was attempted.

## 2026-11-16 follow-up — PQD CamCore gamma transport

The retained camera image is SHA-256
`dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`,
mapped at `0x635c6000`. The disassembly, cross-references and replay receipt
are under `artifacts/analysis/arri-transform-catalog-2026-11-16/`.
The [transform catalog](../ISP_TRANSFORM_CATALOG.md) indexes this and earlier stages.

- The `PQD_Gamma.cpp` wrapper at `0x63bf9ccc` resolves an object from its
  third argument. With an object available, it passes four halfwords
  [0,0,0,1] to `0x63cd6e06` and its first two inputs as a pointer pair to
  `0x63cd6e44`. The base getter reads object+4. The first setter writes
  the controls as words at base+[0x15480,0x15484,0x15488,0x154b8].
- The second setter calls the copy veneer at `0x63dba238` with the first
  pointer, destination base+0x15300 and length 0x180. It reads nine words
  from the second pointer and writes them at base+0x15494 through
  base+0x154b4. Both setters tail-call `0x63cd64e2`, which writes byte 1
  at object+8. The observed combined payload length is 420 bytes. This
  matches the size of the EC working block, with producer identity and
  field semantics still open.
- The caller at `0x63dd913a` passes context+0x3c54, +0x3dd4 and +0x9b0.
  The other direct tail-call at `0x63bfa814` derives its second pointer
  from the first plus 0x180. Direct branch scans leave indirect dispatch
  unresolved. The matrix and 3D-LUT calls are in a separate wrapper at
  `0x63dd8f60`. The selected Movie owner and common frame trigger
  remain to be established.
- From the repository root, run
  `python artifacts/analysis/arri-transform-catalog-2026-11-16/replay_gamma.py`
  with Unicorn 2.1.4 on the Python import path. With a synthetic CamCore
  object and register window, original setters executed at most 200
  instructions per call. A single copy veneer fixture serviced the exact
  384-byte transfer. All transferred bytes, four ordered controls, nine
  metadata stores, two object+8 stores and both synthetic returns matched.
  No camera I/O or hardware effects were involved.

## 2026-11-16 follow-up — Movie-candidate EC gamma buffer and grouped CamCore publication

Retained camera image SHA-256
`dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`, mapped at
`0x635c6000`. Disassembly, string cross-references, replay and MSC receipts are in
`artifacts/analysis/arri-movie-publication-2026-11-16/`. The
[transform catalog](../ISP_TRANSFORM_CATALOG.md) has the corresponding rows.

- **EC to static buffer.** `0x63dcdfa4` calls `0x63dcddf8` (stores fixed selector
  bytes, including variant +0x636 = 1), then `0x63bb9368` (mode +0x646 = 0, then
  the EC builder `0x63bb8760`). It obtains the linked buffer `0x64427840` from
  getter `0x63bfa890`, copies 0x180 bytes from the work object, and copies nine
  further words from work+0x180 to buffer+0x180. The buffer is zero in the image.
  The only other direct getter caller is `0x63baf106` in
  `ExtInfo/EC_SetExtInfo.cpp`, which copies 0x1a4 bytes from an external-info
  record at +0x278 into the same buffer. That is a firmware-owned 420-byte gamma
  writer distinct from the 192-byte FC setter.
- **Static buffer to CamCore.** `0x63930704` logs `EC::PQD_CamE`. Its neighbors in
  the same unit log Movie target fields (`Proxy`, `IsSuperStableMov`, `ImgFr`,
  `[vd]`). It passes a handle to `0x63d4f73e`, which calls `0x63bfa808`. That
  function loads buffer `0x64427840` and tail-calls the PQD gamma wrapper
  `0x63bf9ccc` with buffer and buffer+0x180. The Movie label is inferred from
  surrounding log text; a live trace has not tied it to the recording state.
- **Replay.** `movie_gamma_chain_replay.py` maps the whole image and runs original
  instructions for `0x63dcdfa4`, `0x63bfa890`, `0x63d4f73e`, `0x63bfa808`,
  `0x63bf9ccc`, `0x63cd6e06`, `0x63cd6e44` and `0x63cd64e2`. Fixtures serviced the
  selector init, the EC builder call, two memcpy veneers and two CamCore object
  resolvers, which returned a synthetic object whose window is synthetic memory.
  A 420-byte fixture block reached the buffer exactly. The 384 curve bytes then
  reached window+0x15300 and the nine words reached window+0x15494, with controls
  [0,0,0,1] and object+8 = 1. Result `movie-gamma-chain-replay.json`: PASS. It
  verifies CPU data movement only.
- **Grouped publication.** The wrapper `0x63dd8f60` calls eight PQD setters in a
  fixed order: `0x63bfc5b4`, `0x63bfc804`, `0x63bf74a0`, `0x63bf9d48` (matrix),
  `0x63bfc938`, `0x63bf72c8`, `0x63bf9f2c` (3D LUT) and tail `0x63bfcb60`. Six
  short entries at `0x63dd9228–0x63dd92e0` reach it. A second publisher at
  `0x63bfa630` reads the pending-flag record at `0x64426b20` and gates each of
  the same setters with words +0x00 to +0x18 (matrix +0x08, LUT +0x14). It then
  copies byte +0x20 to `0x644eefdb`. Its only direct caller is the client
  `0x63d4f636`, reached from `0x639123be` (`SetDevPrm`, `StbRCV`). The pending-flag
  writer and the Movie caller of that client are unresolved.
- **Capture develop ordering.** In `CaptureUtilityModuleAPI.cpp` at
  `0x638f0042–0x638f005a`, firmware publishes gamma through `0x63dd913a`, then runs
  `CC: SetPreReviewDevelopParam` (`0x63add3e4`), then the grouped wrapper through
  `0x63dd92dc`. At `0x63d48964–0x63d489fe`, after `CC: SetPlayRawDevelopParam`
  (`0x63add230`), the grouped wrapper runs before gamma. Both are capture or
  playback develop paths, not Movie.
- **Camera check.** Nine bounded reads of `/system/av-cam.bin` in MSC matched the
  copy routine, getter, `EC::PQD_CamE` caller, helper, static-buffer publisher,
  ExtInfo copy, flag-gated setter block, capture ordering and gamma wrapper. The
  debugger answered (`PONG protocol=2`), uptime advanced from 22166.59 s to
  22166.85 s and the `/tmp_bt` listing was unchanged. There were no writes, mode
  changes or device-memory reads.

This identifies a concrete buffer that likely carries Movie gamma, and its 420-byte
format: 96 words on the EC output scale plus nine metadata words. Before any
on-camera use, these points still need proof: a live Movie S-Log3 trace through
`0x63930704`, the selector record `0x63dcddf8` fixes against the PP-selected
record, the meaning of the nine words, the pending-flag writer and the
gamma-versus-matrix/LUT frame relationship. No hook was built or activated.

## 2026-11-16 follow-up — stage pointer table, per-frame CC refresh and matrix stage gain

Same retained image (SHA-256
`dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`). Receipts are in
`artifacts/analysis/arri-movie-publication-2026-11-16/` (`stage-*.txt`,
`vd-*.txt`, `matrix_stage_replay.py`, `matrix-stage-replay.json`,
`msc-stage-blocks.json`).

- **Correction.** The previous supplement called `0x64426b20` a pending-flag record.
  Initializer `0x63bfa36c` (reached from `0x63bfa4a0`) stores pointers there:
  +0x00 `0x6446e0f0`, +0x04 `0x6446e19a`, +0x08 `0x6446e152`, +0x0c `0x6446e178`,
  +0x10 `0x644eefdc`, +0x14 `0x64426af8`, +0x18 `0x6446e0e8`, +0x1c `0x64426b14`.
  `0x63bfa630` tests each pointer for null and passes it to the matching setter as
  that stage's parameter block. It skips all stages when control byte +1 is nonzero.
  The superseded wording was corrected in the transform catalog.
- **Per-frame refresh.** The VD sequence at `0x63934980–0x63934c40` logs
  `EC::VD_Pos1`, `EC::VD_Pos2`, `LC::VD_Pos`, `LM::VD_Pos`, `TC::VD_Pos` and
  `VD_Post: VD_Sequence wait timeout`. At `0x63934b5c` it calls CC function
  `0x63addc7c`, which takes eight accessors (`0x63dd9382–0x63dd93c8`) to the stage
  table and fills each block from the CC output object at ctx+0x98. The fillers are
  control `0x63af9ea0`, `0x63da5332`, `0x63da53c2`, matrix `0x63af9f38`,
  `0x63da53ce`, `0x63da541c`, LUT `0x63da542a` and `0x63da544a`. The LUT block
  receives 0x12 bytes from CC+0x99, the bank pointer from CC+0xac at +0x14 and the
  request byte from CC+0xb0 at +0x18. `0x63bfa4b8` follows at `0x63934b6e`.
  The VD naming indicates a frame-rate sequence, which matters for state
  persistence. A stage override placed before this filler would be replaced on the
  next pass. A live trace in Movie recording is still needed to confirm per-frame
  execution.
- **Publisher client.** The only direct caller of `0x63bfa630` is `0x63d4f636`,
  reached from `0x63912364`. That function's address appears at table entry
  `0x645c6d00`, near `SetDevPrm` and `StbRCV` diagnostics. `EC::PQD_CamE`
  (`0x63930704`) has neither direct calls nor absolute pointers; it is dispatched
  indirectly.
- **Matrix stage gain.** Filler `0x63af9f38` writes 18 signed halfwords (3 rows of
  6) and halfword +0x24 from CC+0x2c. It tests bit 3 of a global byte through
  `0x63da007c`. With the bit clear, it copies CC+8 unchanged. With the bit set:
  gain g = clamp(0xFFF00 / (4095 - L), 256, 511) in Q8 when enable byte CC+0x40 is
  set and level L <= 0xffe; otherwise g = 256. L is the halfword at +0x46 of the
  third argument. Columns 0–2 become coef * g / 256. Columns 3–5 become
  coef * g^2 / 65536, and each row's diagonal is replaced by the negated sum of
  the other two entries, so that half has zero row sums. Values clamp to
  [-0x8000, 0x7fff]. g is written to the record at CC+0xc0, +0x46.
- **Replay.** `matrix_stage_replay.py` ran the original filler and helpers (division,
  rounding, clamp) with the whole image mapped. The feature-bit test was serviced
  as a fixture, and page 0 was mapped for zero GOT targets in the static image.
  Five runs: bit clear; bit set with enable 0; enable 1 at L = 1024, 2048 and
  4095. Results matched the model exactly (g = 256, 256, 341, 511, 256).
  Rounding agreed on these fixtures; boundary rounding is not characterized.
  The signal roles of the two halves, the live feature bit and L, and their
  relation to PP Saturation remain to be confirmed.
- **Camera check.** Nine bounded reads of `/system/av-cam.bin` matched the table
  initializer, CC filler, VD call site, matrix filler (both parts), gain helper,
  LUT filler, post-filler call and accessors. The debugger answered, uptime went
  from 22485.13 s to 22485.24 s and `/tmp_bt` was unchanged. No writes.

For ARRI tuning, this places the matrix and LUT overrides after `0x63addc7c` in the
VD sequence, and requires g = 256 for a saturation-neutral matrix. No hook was built
or activated.

## 2026-11-16 follow-up — matrix gain switch and level source

Same retained image. Receipts are in `artifacts/analysis/arri-cc-matrix-gain-2026-11-16/`.

- **Feature switch.** The matrix filler loads GOT base `0x645c1798` and offset
  0x1af0, giving slot `0x645c3288`, whose linked value is `0x64351160`. It reads
  byte +0x13 and tests bit 3 through `0x63da007c` (`(r0 >> r1) & 1`). The global
  is zero in the image. The code has 351 loads of GOT offset 0x1af0. Four nearby
  readers of +0x13 were found (`0x63af9d4e`, `0x63af9e54`, `0x63af9f54`,
  `0x63afad34`), all CC functions, with no store to +0x13 near them. The switch is
  therefore runtime state, likely a body or mode configuration. Its value needs a
  live read.
- **Second gain consumer.** CC dispatch item 21 at `0x64580564` (key 0x127800)
  points to `0x63afad20`. With the same bit set, it calls the gain helper
  `0x63af9efc` with the halfword at +0x16a of its first record and the enable byte
  at +3 of its second record; otherwise it uses 256. It calls `0x63ae4df0` and
  writes rounded, clamped bytes at +6, +7, +0x0c and +0x0d of its output. That
  makes the gain a shared CC control, not only a matrix-stage detail.
- **Level source.** The only static store of halfword +0x16a in `0x63a00000–
  0x63e00000` is `0x63af6a58`. It copies the halfword at source context +0x4ae2
  in a routine that also fills +0x15a–+0x170. The same field is read at
  `0x63aee562`, `0x63aee570`, `0x63af6686` and `0x63bae02e`. The source context's
  identity and its relation to the PP Saturation field (verifier state +0x1b2)
  are unresolved.
- **Camera check.** Six bounded reads of `/system/av-cam.bin` matched the bit-test
  helper, GOT offset load, gain callback, level copy, GOT slot and dispatch entry.
  The debugger answered, uptime went from 22745.13 s to 22745.28 s and `/tmp_bt`
  was unchanged. No writes.

Static analysis has reached the limit for these controls: the feature byte and level
are runtime values. Determining them needs a read-only runtime observation of LIRO
memory, which requires a verified physical mapping for the av-cam image. Bulk
`/dev/mem` scans are excluded (E0454), so the next step needs a single identified page.

## 2026-11-16 follow-up — live read-only matrix-gain state in MSC

Receipts: `artifacts/analysis/arri-cc-matrix-gain-2026-11-16/live-feature-byte.json`
and `live-cc-matrix-gain.json`. Scope: standing read-only authorization. Every
`/dev/mem` read was bounded and stayed within one 4 KiB page, through
`camera_debugger read('/dev/mem', VA - 0x60000000, n)` as in `capture_gyro_state.py`.
This was not a sweep. No helper files were created and nothing was written.

- **Mapping.** E0031 gives LIRO PA `0x035c6000` for VA `0x635c6000`. A 200-byte read
  at PA `0x03af9f38` matched the image's matrix filler exactly. GOT slot `0x645c3288`
  read `0x64351160`, equal to the linked value. The live av-cam image therefore
  follows this alias for the sampled code and data.
- **Feature byte.** Two samples of `0x64351160..+0x1f` were identical:
  `003362c09e8280481a00860501339822bd3b3f1f` followed by zeros. Byte +0x13 is
  `0x1f`, so bit 3 is set and the gain path in `0x63af9f38` and `0x63afad20` is
  active on this body. The surrounding bytes look like a configuration bitmap
  (inference).
- **CC output.** CC context `0x643691fc` (from the VD call site) has ctx+0x98 =
  `0x643cc384`. Two samples of CC+8 were identical: rows `[1024,0,0 | 0,0,0]`,
  `[0,1024,0 | 0,0,0]` and `[0,0,1024 | 0,0,0]`. That is a Q10 identity first half
  and a zero second half. CC+0x2c = 0, enable CC+0x40 = 0 (so g = 256), level
  CC+0x446 = 128 and record pointer CC+0xc0 = 0. In this state the filler copies or
  scales by unity, and the gain write-back target is absent.
- **Interpretation.** The identity confirms that the first half of the matrix stage
  uses Q10 with 1.0 = 1024, the same format as the converter's matrix. The camera
  was in MSC, so these are idle or default values, not Movie S-Log3 values.
- **Responsiveness.** Uptime advanced (22863.31 → 22863.77 s and 22890.82 →
  22891.29 s), the debugger answered `PONG` (pid 703) after each set, and the
  `/tmp_bt` listing was unchanged.

Next: repeat these same single-page reads with the camera in Movie, PP set to S-Log3,
at two Saturation values. That would bind enable, level and the CC matrix to PP state.
Switching modes is a user action, not part of the read-only scope.

## 2026-11-16 follow-up — live Movie S-Log3 gamma identified as record 31

Receipts: `artifacts/analysis/arri-cc-matrix-gain-2026-11-16/live-movie-slog3.json`,
`compare_live_gamma.py` and `live-gamma-comparison.txt`. Scope: standing read-only
authorization. Every read was bounded and stayed within one 4 KiB page
(`/dev/mem`, VA - 0x60000000). No writes, no helper files.

- **State.** The user switched the camera to Movie with PP S-Log3. The debugger
  stayed reachable over the MSC ext-command transport (`PONG`, pid 703). Uptime was
  48.5 s at the first contact, so the camera had restarted since the previous read
  at 22891 s; the cause is not recorded here. The mode is user-reported; no firmware
  mode field was read.
- **Mapping re-verified after the restart.** 200 bytes at PA `0x03af9f38` matched the
  image again.
- **Gamma buffer `0x64427840` (420 bytes).** Populated. All 96 words equal stock EC
  record 31, variant 1 (directory `0x6458443c`), passed through the mode-0 branch
  `(v >> 6) + round bit` with max |difference| 0. The next-best record (28) differs
  by up to 115 codes. Values rise from 84 to 803 and stay at 803 at the top. The
  nine metadata words `[3,3,4,4,3,3,4,3,3]` equal record 31's exponent words
  `[3,3,4,4,3,3,3,4,3]` permuted 1,0,3,2,5,4,7,6,8. That is the halfword-pair swap
  of the EC widening routines and resolves the meaning of the nine words. Record 31's
  knot positions are 4, 8, 12, … 7168, 7680, 8191.
- This confirms on hardware that Movie S-Log3 (PP value 12 → record 31) uses the
  `0x64427840` buffer and the 10-bit branch, superseding the candidate status in the
  earlier supplements for this mode.
- **CC and stage blocks.** Feature byte `0x1f`; CC matrix (CC `0x643cc384` +8) is the
  Q10 identity in all three samples; enable 0; level 128; record pointer null. The
  stage pointer table matches the static initializer. The matrix stage block
  `0x6446e152` holds the same identity, with +0x24 = 0. The LUT stage block
  `0x64426af8` has bank pointer 0 and request 0, with one nonzero byte at +0x0f. In
  this state the CC matrix stage is unity and no 3D LUT bank is requested. Where
  S-Gamut3.Cine primaries are formed is therefore outside this matrix (inference).
- **Responsiveness.** Uptime went from 72.08 s to 72.60 s, `PONG` followed and
  `/tmp_bt` was unchanged.

For ARRI fine-tuning, this gives the Movie gamma replacement format. It is 96 uint32
10-bit codes evaluated at record 31's knot positions, plus the nine pair-swapped
exponents. It also shows that CC matrix and LUT are idle in S-Log3, so an ARRI matrix
and LUT would add stages rather than replace stock ones. The Saturation binding still
needs a second reading with a non-zero Saturation.

## 2026-11-16 follow-up — Saturation reading (negative) and ARRI gamma-only test blocks

Receipts: `artifacts/analysis/arri-cc-matrix-gain-2026-11-16/live-movie-slog3-saturation.json`
and `artifacts/analysis/arri-gamma-test-block-2026-11-16/`. The read scope was the same
as the previous supplement (bounded single-page `/dev/mem` reads, no writes).

- **Saturation reading.** The user set a non-zero PP Saturation in Movie S-Log3.
  Uptime 381.64 → 382.25 s (no restart since the previous reading), the mapping check
  passed, `PONG` followed and `/tmp_bt` was unchanged. Every observed field was
  byte-identical to the Saturation 0 reading: feature byte `0x1f`; CC `0x643cc384` +8
  Q10 identity in three samples; enable 0; level 128; record null; matrix stage block
  identity; LUT stage block unchanged; gamma buffer and metadata unchanged.
- **Interpretation (inference).** This CC object, its gain controls and the stage
  blocks at `0x64426b20` do not respond to Movie PP Saturation and stay at defaults in
  both MSC and Movie. They probably belong to the still or live-view develop path. The
  Movie PP chroma controls likely act through the separate MOV/MON bundle
  (`0x63ade4e4` / `0x63da1162`) or a later stage. The live Movie gamma buffer
  `0x64427840` remains the only Movie table confirmed on hardware.
- **Test blocks (offline).** `build_gamma_block.py` keeps the stock knot layout and
  nine pair-swapped exponent words. It replaces each stock word c (a 10-bit S-Log3
  code, record 31) with round(1023 * ARRI_neutral(c / 1023)), monotone and clamped
  to 0–1023. Assumption: the stock words are S-Log3 code values on a 0–1023 scale
  that reaches the encoder unchanged. Outputs:
  - `stock-record31.gamma420.bin` (SHA-256 `6f863fd4…a216`, 84–803), for restoration.
  - `arri-logc3-awg3.gamma420.bin` (`ea7fb6c5…71fc`, 86–771, max change 32 codes).
  - `arri-classic-rec709.gamma420.bin` (`14b2c462…579e`, 12–981, max change 199
    codes). This one is the most visible gamma-only check.
  These are neutral-axis tone curves only. Color (AWG3 primaries, Classic chroma
  roll-off) is not included, because the Movie matrix and LUT targets are unconfirmed.
- **Write path.** The debugger has no write operation (ops 1–13, 255). A buffer write
  needs a new helper or a mod, plus explicit user approval. Proposed minimal test:
  verify the stock block live, write the Classic block once, read it back
  immediately and after about 1 s to see whether EC rewrites the buffer, observe the
  LCD, restore the stock block and verify. Open risks: refresh cadence, cache
  coherence between Linux and LIRO, and whether `EC::PQD_CamE` republishes without a
  trigger.

## 2026-11-16 follow-up — approved gamma buffer write test: `/dev/mem` write does not land

Scope: the user explicitly approved a one-time write of the Classic gamma-only block
into the live Movie gamma buffer, followed by restoring the stock block. Receipts:
`artifacts/analysis/arri-gamma-test-block-2026-11-16/write-test-classic.json`,
`write-test-classic-retry.json` and `write-test-cleanup.json`.

- **Method.** The debugger `push` uploaded `stock.bin` and `classic.bin` (SHA-256
  `6f863fd4…a216` and `14b2c462…579e`) into the owned folder `/tmp_bt/arri_gt`, with
  readback verification. A host-side guard read PA `0x04427840` (420 bytes) and
  required it to equal the stock block before writing. The camera's BusyBox 1.29.3
  `dd` has no `conv=`. With `seek=17866256` (blocks of 4 bytes = `0x04427840`) it
  skips `O_TRUNC`, and its `ftruncate` fails harmlessly on a character device.
  Write command: `dd if=/tmp_bt/arri_gt/classic.bin of=/dev/mem bs=4 seek=17866256
  count=105`.
- **Result.** `dd` reported 105 records and 420 bytes out, exit status 0. Host reads
  at 0.07, 0.56, 1.02, 2.05, 4.03 and 8.04 s all returned the exact stock block.
  One diagnostic retry, also guarded, read back in the same shell command right after
  the write and again after a short delay. Both readbacks were the stock block.
- **Interpretation.** The write path through `write()` on `/dev/mem` does not modify
  this memory, as observed through `read()`, despite reporting success. A firmware
  rewrite within about a millisecond, twice, is implausible but not excluded. Possible
  causes are a Sony kernel `/dev/mem` write policy or a non-coherent or alternate
  mapping for LIRO memory. Without kernel source, the cause is undetermined.
- **No visible effect is expected**, because the buffer content never changed. The
  stock block was confirmed in place after the test, so no restore write was needed.
- **Cleanup and responsiveness.** All four owned files and `/tmp_bt/arri_gt` were
  removed (rc 0). `/tmp_bt` matched the baseline listing. Uptime advanced through
  642.87 → 651.09 → 680.79 → 696.45 s, with no restart, and the debugger answered
  `PONG` (pid 703) after every step.

Next boundary: a write path that actually reaches LIRO RAM. Candidates are a small
native helper that maps the page with `mmap(/dev/mem, MAP_SHARED, PROT_WRITE)` and
uses explicit word stores (with the same guard), or a firmware-owned path such as
`EC_SetExtInfo`, which already copies a 420-byte block into this buffer. Either one
needs separate approval.

## 2026-11-16 follow-up — mmap write lands; EC refills the gamma buffer every frame

Scope: continuation of the user-approved gamma buffer write test. Receipts:
`artifacts/analysis/arri-gamma-test-block-2026-11-16/mmap-write-test.json` and
`mmap-write-test-cleanup.json`. Helper sources:
`mods/arri_isp/gamma_poke/` (`gamma_poke.c`, `start.S`, `build_wsl.sh`).

- **Helper.** `gamma_poke` is a freestanding static ARM executable built with raw
  EABI syscalls, so it has no libc and no interpreter. The tested build
  (`artifacts/tests/arri-gamma-poke-build-2026-11-16/gamma_poke`, SHA-256
  `46222688…e4bc`) maps one 4 KiB page of `/dev/mem` with `O_SYNC` and
  `MAP_SHARED`. It requires the 420-byte buffer to equal the stock block exactly,
  makes 105 word stores followed by `dsb`, and reports readbacks through the mapping
  and through `pread64`. A later build (v3, `5705ba99…def9`) takes the physical
  address as an argument. It enforces 4-byte alignment, a single page and the
  `0x035c6000–0x045cc4e8` LIRO alias window. That build was never run on the camera.
- **Result.** Guard passed (stock). Output: `WROTE 105 words`, `map+0ms new`,
  `map+20ms other`, `map+200ms expected`, `pread expected`, exit 5. Host reads at
  0.27–10.03 s were all stock.
- **Interpretation.** The mmap write reached memory. Within 20 ms the buffer was
  partly overwritten, and by 200 ms it was fully stock again. EC
  (`0x63dcdfa4` → builder → copy) refills `0x64427840` at frame cadence from stock
  record 31, so the buffer is not a persistent target. This also explains the earlier
  `dd` result as plausibly being overwritten before its readback, although `write()`
  semantics remain unverified. A one-frame flash of the Classic curve, if any, was too
  short to observe.
- **Persistent target (prepared, not written).** Stock record 31 variant 1 is at VA
  `0x63e5363c` (PA `0x03e5363c`, inside one page). Only directory entry 63 points to
  it. Words 0–95 are 10-bit codes shifted left by 6; words 96–104 are the exponents.
  `build_record31_blocks.py` writes `record31-stock.bin` (`bc1d67e3…ed58`),
  `record31-arri-classic-rec709.bin` (`93d69d06…e744`) and
  `record31-arri-logc3-awg3.bin` (`d2accfa1…39e6`), keeping the exponent words.
  Replacing the record would make every EC rebuild produce the ARRI curve, and only
  for the S-Log3 selection. If LIRO holds the record in a data cache, the change may
  not be seen until eviction. An attempt to run this write was stopped by the session
  permission check before any camera action, because it is a new target beyond the
  approved buffer test.
- **Cleanup.** Owned files (`gamma_poke`, `stock.bin`, `classic.bin`) and
  `/tmp_bt/arri_gt` were removed. The gamma buffer and record 31 both read as stock.
  `/tmp_bt` matched baseline, uptime advanced to 984.95 s with no restart, and the
  debugger answered `PONG`.

## 2026-11-16 follow-up — first live ARRI change: record 31 drives the Movie gamma buffer

Scope: the user approved writing the Classic curve into record 31, on the condition
that a battery pull reverts the test. Receipts in
`artifacts/analysis/arri-gamma-test-block-2026-11-16/`: `revert-preflight.json`,
`revert-snapshot-check.txt`, `wbi_record31_check.py`, `wbi-record31-check.json` and
`record31-write-test.json`.

**Revert analysis (before the write).**
- `/proc/mounts`: root and `/usr` are read-only ext2. `/system` (nflasha3) is vfat,
  mounted read-write with sync. The write method only maps `/dev/mem` and never
  opens a file, so `/system/av-cam.bin` is untouched. Its bytes at file offset
  `0x88d63c` equal the stock record, and its date is still Aug 10 2021 while the
  Linux clock reads 1970.
- `/proc/cmdline` shows snapshot boot (`warm.mode=2`, `cmpr.path=/system/sabin/ssboot.bin`).
  `ssboot.bin` is the 11,280-byte loader (E0415), unchanged since 2021. The RAM
  snapshot is the WBI on `nflasha5` (E0043, E0413). The packaged WBI
  (`19a702b0…001b`, 644 sections) has **no section in the av-cam range**
  `0x035c6000–0x045cc4e8`. Av-cam RAM is therefore not restored from the snapshot;
  it is loaded at boot through LIRO (`liro.ko adr=0x35c6000`, E0032). The earlier
  restart today (uptime 48.5 s) came back with code identical to the file. Abrupt
  power loss also prevents any shutdown-time save. Conclusion: a battery pull
  reloads stock record 31 from flash. Residual assumption: the camera's `nflasha5`
  equals the package and is not regenerated at runtime. Firmware update tools
  write it; the observed timestamps show no runtime writes in `/system`.

**Write.**
- Uptime 1250.89 s. Guard: the live record equalled the image (stock), and the
  buffer was stock. `gamma_poke 0x3e5363c rec_stock.bin rec_arri.bin` (v3,
  `5705ba99…def9`) printed `WROTE 105 words; map+0ms new; map+20ms new;
  map+200ms new; pread new`, exit 0.
- Host samples at 0.27, 0.52, 1.03, 2.04, 4.04, 8.05 and 15.03 s: record =
  `record31-arri-classic-rec709.bin` (`93d69d06…e744`), and gamma buffer
  `0x64427840` = `arri-classic-rec709.gamma420.bin` (`14b2c462…579e`) in every
  sample. The debugger answered `PONG` and uptime reached 1266.31 s.
- This shows on hardware that EC rebuilds the Movie gamma buffer from record 31 each
  pass, that the record is not cached stale on the LIRO side, and that a RAM-only
  change to record 31 persists while scoped to the S-Log3 selection. Visual and
  recorded-output effects are pending the user's observation. Encoder scale and
  range remain to be measured.

**State left on the camera (intentional, for observation).** Record 31 holds the
Classic curve. `/tmp_bt/arri_gt/` contains `gamma_poke`, `rec_stock.bin` and
`rec_arri.bin` for the restore. Restore with
`/tmp_bt/arri_gt/gamma_poke 0x3e5363c /tmp_bt/arri_gt/rec_arri.bin /tmp_bt/arri_gt/rec_stock.bin`,
then remove the folder, or pull the battery.

## 2026-11-16 follow-up — ARRI gamma as a lifecycle mod; loader swap blocked until restart

The user reported that the record-31 test looked right in Movie and in photo mode (not
washed out like S-Log2; colors similar to ARRI). They asked for it as a mod.

- **Loader contract gap.** On OFF, SMOD v1 only restores hook bytes; it never runs mod
  code. A data swap therefore cannot restore stock. `tools/native_camera/mod_loader.c`
  now adds an optional lifecycle flag (SMOD flag bit 1). The loader calls the Thumb entry
  at `code_addr` with 1 after ON and 0 before OFF. Mods without the flag behave exactly as
  before; an older loader ignores the bit. To fit the 3120-byte slot, the two toggle log
  lines became one `mod <code_addr | state>` entry (low bit 1 = ON). The build is exactly
  3120/3120 bytes. A rebuild without the change reproduced the live 3096-byte loader
  byte for byte.
- **Mod.** `mods/arri_isp/arri_gamma_mod/` (source, linker script, data generator,
  packer, Unicorn test, WSL build). Code lives in the `/usr/lib/libtestcmd.so` RX zero
  tail. The live process-memory read found 2212 zero bytes from `0x0566275c`; the mod
  is linked at `0x05662760` (2208 bytes available). The E0522 page-end rule alone was
  not sufficient: the first 1824 bytes after that library's executable segment end are
  nonzero in memory (`cave-live-scan.json`). `ARRIClassic.mod` is 1228 bytes (SHA-256
  `20f6eee7…b495`), code 1160 bytes, flag 2, no hooks. ON writes the 96 ARRI curve
  words only if record 31 equals stock; OFF writes stock back only if it equals ARRI.
  Exponent words are never touched. Each call appends
  `arri_gamma on=N result=C` to `/tmp_bt/mods/arri_gamma.log`
  (w written, a already, x foreign content untouched, v verify failed, o/m open/mmap
  failure). The Unicorn test passed ON, repeated ON, OFF, foreign content and
  OFF-when-stock.
- **Live loader identification.** The boot receipt
  `/tmp_bt/mod_loader_card_boot.once/receipt` shows the SD boot package installed the
  loader at 18.92 s (theme `0x15e91b68..0x15e91d68`). The matching requests are
  `artifacts/publish/mod-loader-sd/artifacts/hardware/sd-standalone-v1/` (loader code
  equals live; theme 512 bytes at `0x15e91b6d`; guard `e72040bc…`). Lifecycle requests
  reuse that theme unchanged (`artifacts/tests/arri-mod-loader-size-2026-11-16/lifecycle-reqs/`,
  install `9544f214…9d74`).
- **Camera run (receipt `artifacts/tests/arri-gamma-mod-classic-2026-11-16/camera-install.json`).**
  First, record 31 was restored to stock with the guarded helper (`WROTE`, all reads
  `new`, exit 0). The test folder was removed and the record read back stock. Then the
  guard ran with the matching `remove.req`. It returned `MOD_LOADER_RESULT=-24
  changed=0`: the anchor at `0x1615ec40` is nonzero after the Mods tab has been used,
  and by design the guard refuses runtime unload ("restart to unload"). Threads resumed
  (175). The open-item hook still pointed to the original loader. The guard and request
  were removed; `/tmp_bt` matched baseline. No loader or mod change took effect.
- **Next.** After a restart, perform the swap before the menu is opened (anchor still
  zero): run the sd-standalone `remove.req`, then the lifecycle `check` and `install`
  requests, then stage `ARRIClassic.mod` in `/tmp_bt/mods`. Everything stays RAM-only.
  The SD package reinstalls its own loader on later boots.

## 2026-11-16 follow-up — lifecycle loader installed after restart; ARRI mod staged

The user restarted the camera into MSC without opening the menu. Run:
`python mods/arri_isp/arri_gamma_mod/install_after_boot.py`. Receipts:
`artifacts/tests/arri-gamma-mod-classic-2026-11-16/install-after-boot-20261116-150926.json`
and `post-install-verify.json`.

- The SD boot receipt showed `SD_BOOT_INSTALLED`, and the anchor `0x1615ec40` read zero.
  The guard (`e72040bc…`) removed the sd-standalone-v1 loader (`REMOVED`, exit 0). The
  lifecycle `check.req` reported `CHECK_OK` and `THEME_OK`, and `install.req` reported
  `INSTALLED`. The open-item hook now reads `dff800f0 014f5e04`, which jumps to the new
  entry `0x045e4f01`. No rollback was needed. `ARRIClassic.mod` was pushed to
  `/tmp_bt/mods` with readback verification. The guard and request were removed.
- Verification: the live loader code at `0x045e43a0` equals the 3120-byte lifecycle build.
  The staged mod equals the build (`20f6eee7…`). The mod cave `0x05662760` (2208 bytes)
  is still zero. **Record 31 read stock after the restart**, confirming that a restart
  reverts the earlier RAM-only record test. At uptime 95.42 s the debugger answered
  `PONG`, and `/tmp_bt` held only the expected entries.
- Pending: the user toggles **ARRI Classic Gamma** in the Mods tab. Expected log lines:
  `mod 05662761` / `arri_gamma on=1 result=w` (ON) and `mod 05662760` /
  `arri_gamma on=0 result=w` (OFF).

## 2026-11-16 follow-up — Mods-tab toggle verified; remaining stages need a Movie color target

Receipt: `artifacts/tests/arri-gamma-mod-classic-2026-11-16/toggle-verify.json`.
The user reported the mod "works perfectly". At uptime 257.97 s the mod log held five
calls: `on=1 result=w`, `on=0 result=w`, `on=1 result=w`, `on=0 result=w` and
`on=1 result=w`. The loader log alternated `enter_idx 0` with `mod 05662761` (ON) and
`mod 05662760` (OFF). Record 31 read the ARRI block, consistent with the final ON. This
verifies the lifecycle flag, the mod's guarded swap in both directions, repeatability,
and the user-visible result for the tone curve.

**Matrix and 3D LUT status.** No Movie target is confirmed for either. In Movie S-Log3,
the CC object at `0x643cc384` and the stage blocks at `0x64426b20` held identity and
idle values, and they did not change with PP Saturation. The 3D LUT stage had no bank
pointer or request. Adding ARRI matrix and LUT data to those blocks would most likely
have no effect in Movie. The Movie chroma path is probably the separate MOV/MON bundle
(`0x63ade4e4` / `0x63da1162`, setup `0x63af7de0`) or a later stage. Proposed next step:
differential single-page reads of candidate MOV/MON objects across two user-set PP
states (Color Mode S-Gamut3.Cine vs. Movie, and Saturation 0 vs. +16), after statically
resolving those objects' addresses.

## 2026-11-16 follow-up — Movie color path research: develop dispatch and live PP fields

Receipts are in `artifacts/analysis/arri-movie-color-2026-11-16/` (`movmon-*.txt`,
`dispatch-*.txt`, `snapshot.py` and `snapshots/baseline.json`).

- **Dispatch.** The MOV/MON table at `0x6457ffdc` (`0x63ade4e5`, `0x63da1163`, …) is
  indexed at `0x63ade1aa` by a mode byte that `0x63addfe8` computes and stores at
  `bundle[0xc]+0xb`. Its caller is `CC: SetDevelopParam()` (`0x63adcb6c`, call at
  `0x63adcbfc`), which passes a **stack** bundle (sp+0x264). It then copies the results
  into the static stage blocks: `0x63da5332` writes `base+8`, and the next block is at
  `base+0x6a`. With base `0x6446e0e8` these are the `+0x00` block `0x6446e0f0` and the
  matrix block `0x6446e152` from the earlier stage table. MON calls the CC coordinator
  `0x63da2274` with bundle+0x58. This chain is a develop path that feeds the same idle
  stage blocks, so it does not explain Movie color.
- **Differential method.** `snapshot.py` reads seven fixed single pages: PP state
  (2 pages), CC context, CC output, stage blocks, stage table/LUT and gamma buffer. It
  first verifies the code mapping. The baseline was taken at uptime 525.26 → 525.35 s,
  with `PONG` afterwards.
- **Live PP state** (`0x6442451c`, offsets from the verifier): PP mode +0x198 = 1, Color
  Mode +0x19c = 11, Saturation +0x1b2 = 0, Hue +0x1b3 = 0, Gamma +0x1bd = 12, Knee
  +0x1be = 0. RGBMatrix +0x656..+0x666 = 0 and RGBMatrixSW +0x668 = 0. Gamma 12
  matches the PP value that the EC selector maps to record 31 (S-Log3), now observed
  live. Color Mode 11 is probably S-Gamut3.Cine; this is to be confirmed by the
  differential.

**Saturation +16 round** (`snapshots/sat16*.json`, `diff-baseline-sat16.txt`,
`stable-baseline-vs-sat16.txt`). The PP state read Saturation +0x1b2 = 16, which confirms
the field. The same byte change appears in a PP-state copy inside the CC context page
(`0x643696fe`), and a neighbouring byte at state+0x226 went from 14 to 16. Two repeats
with no setting change were stable against each other. Against the baseline, however,
hundreds of bytes changed in the CC output page and dozens in the stage-block page. Most
look like scene-driven exposure and white-balance statistics (halfword pairs and
histogram bins). The scene was not controlled between the baseline and the change.
No Q10-matrix-like field changed. This single A→B pair cannot separate Saturation
effects from scene effects. Revised protocol: a static scene (lens cap or a fixed
target, with exposure and WB unchanged), and an A/B/A sequence with repeats, accepting
only bytes that follow the setting in both transitions.

**Controlled A/B round, lens capped** (`snapshots/capA*.json`, `capB*.json`,
`stable-capA-vs-capB.txt`). The two Saturation-0 repeats differed by 2 bytes (PP
state), 18 bytes (CC context) and 0 bytes in the CC output, stage blocks, LUT table and
gamma buffer. After Saturation 0 → +16 (two repeats), the stable changes were: the PP
field +0x1b2 = 16, its CC-context copy, and a family of counters that grow by 2 on every
PP change (state+0x250, +0x664, +0x748, +0x92c and their CC-context copies, 4→6→8→10→12
over the session). These are update-generation counters relevant to refresh, and not
color values. **The CC output object, stage-block contents and gamma buffer did not
change.** The remaining 9 stage-page bytes and 11 table-page bytes are pointers and
time-like values. Two signed words at `0x64426c3c` and `0x64426c44` stepped by -1;
whether they relate to Saturation is unresolved.

**Chroma controls and forwarders** (`chroma-*.txt`, `sat-readers-cc.txt`,
`pp-subrecord-readers.txt`). The LIRO state also holds separate controls:
`SET_SATURATION` +0x639 (handler at `0x63bed6ce`) with a sibling at +0x638;
`CheckSetColorDepth` R/G/B/C/M/Y at +0x650..+0x655; and `uc_color_correct_saturation`
at +0x68e onward. All read zero, and none changed with PP Saturation. Twenty code sites
read the PP fields +0x1b2 and +0x1b3 together. `0x63af725a–0x63af7276` copies +0x639,
PP saturation, PP hue and +0x638 into a CC parameter object at +0x2a..+0x2d; that
object is the one receiving the RGBMatrix fields at +0x80..+0x92. `0x63ae6da4–0x63ae6dc8`
copies state +0x1b0..+0x1b4 into an object at +0x88..+0x8c. `0x63aecf36` and the EC
site `0x63bb9ce2` (feeding `0x63dc7718`) are further consumers. Their destination
objects are runtime allocations that are not in the watched pages. **Conclusion so far:**
Movie chroma follows PP Saturation through CC parameter objects whose live addresses
are still unknown. Next: resolve the owners of those destinations, specifically the
caller of `0x63af725a` and the CC LUT descriptor used in Movie, and add their pages to
the snapshot set.

**Forwarder owner** (`fwd-*.txt`). The saturation and RGBMatrix forwarder lives in
`0x63af6edc`. Its only direct caller is `0x63addbe0`, which passes records built on the
stack (`sp+0x450`, `sp+0x420+0xc`, `sp+0x25c`). The fifth argument is a context; helper
`0x63da4030` returns context+0x234. The second forwarder `0x63ae6a84` is tail-called
from `0x63da32bc` after `0x63af1198` and `0x63af0fbc`. Like `SetDevelopParam`, this is a
develop-style chain with stack-temporary parameter records, and per the earlier
supplements its results land in the static stage blocks. Open contradiction: PP
Saturation is forwarded into this chain, yet with the lens capped the stage blocks and
CC output did not change. Possible explanations: (a) the live Movie preview in this USB
MSC session does not re-run the develop chain on a PP change (the generation counters
advance, but no rebuild happens); (b) Movie applies PP chroma in a stage not represented
in these blocks. Discriminating check: whether Saturation 0 ↔ +16 visibly changes the
live Movie S-Log3 image in this session.

**Black & White round** (`snapshots/bw*.json`, `stable-capB-vs-bw.txt`, `bw-decode.txt`,
`bw-regions.txt`, `bw-twin-tables.txt`). Saturation +16 gave no visible change. The user
then set PP Color Mode to Black & White; the live Movie S-Log3 image went monochrome, so
the live pipeline recomputes color on a PP change in this session. The PP state read
Color Mode +0x19c = 8 (it was 11 in S-Gamut3.Cine) and Gamma 12. Two B&W repeats were
stable against each other. Unchanged: CC matrix (Q10 identity), gain controls (enable 0,
level 128), matrix stage block (identity), LUT stage block (idle) and the gamma curve
(the ARRI Classic words, because the mod is ON). **None of the known matrix or LUT
targets carries the monochrome conversion.** Changed regions include two twin 506-byte
tables at `0x644270b0` and `0x644273dc`. They hold 32-bit words near 4096 (Q12 = 1.0)
and 16384 (Q14 = 1.0) that rise with index: S-Gamut3.Cine ends at 4123/16440, B&W at
5492/19372. Other changed regions are `0x644266e0` (326 bytes), `0x64426d64` (189),
`0x64426f34` (130) and many in the CC output and stage pages. The scene-control state
for this step was user-reported. Next: return to S-Gamut3.Cine (A/B/A) and keep only
regions that revert, then decode the survivors.

**A/B/A Color Mode result: a live Q10 color matrix** (`snapshots/sg*.json`,
`aba-colormode.txt`, `cc-coeff-decode.txt`, `q10-matrix-tables.txt`,
`matrix-table-scan2.txt`, `matrix-producers.txt`). After switching back to
S-Gamut3.Cine (Color Mode 11, Saturation 16, two repeats), the A/B/A filter kept only
bytes that changed for B&W and returned exactly. These are the Color Mode field
(state+0x19c and its CC-context copy), a few flag bytes in the CC output object
(`0x643cc470`, `0x643cc6ac`, `0x643cc72c`, `0x643ccce4`) and part of a coefficient
block. The twin Q12/Q14 tables did not return exactly, so they follow the scene rather
than Color Mode.

Decoding the CC output object (base `0x643cc384`) found **a 3×3 signed Q10 matrix with
every row summing to 1024** at +0x12c, with a copy at +0x684:
- S-Gamut3.Cine, capped: `[1241,-205,-12; 62,1116,-154; 30,-251,1245]`
- Black & White: `[1341,-205,-112; -61,1116,-31; 27,-170,1167]`
- S-Gamut3.Cine, second reading: `[1181,-188,31; 51,1024,-51; 118,-356,1263]`

It changes with Color Mode, and also between two S-Gamut3.Cine readings, which is
consistent with interpolation by white balance or scene (inference). The earlier CC+8
block stays a Q10 identity and is a different field. Scans of the whole image for
row-normalized 3×3 tables (int16 and int32; Q8, Q10, Q12, Q14) found no candidates.
The matrix is therefore computed at runtime, not copied from a normalized static table.
Fixed-offset store searches for +0x12c and +0x684 are ambiguous (138 and 6 sites), so
the producer probably writes through a loop or copy. Whether this matrix is applied to
the recorded Movie signal, and its input and output domains, are unverified. Next:
locate its producer and inputs, then decide between overriding the producer's
per-Color-Mode coefficients (a data patch, as for record 31) and a post-computation
hook.

## 2026-11-16 follow-up — color matrix producer and live per-Color-Mode matrix table

Receipts in `artifacts/analysis/arri-movie-color-2026-11-16/`: `cc-matrix-mid.txt`,
`cc-matrix-out.txt`, `colormode-matrix-table.json` and the earlier
`arri-front-end-matrix-2026-11-16/cc-matrix.txt` / `cc-selection.txt`. (An intermediate
candidate, `0x63bb9f74` in EC, analyzes a 30-column gradient grid and writes statistics
to `0x64186748`. Its +0x686 store is an offset coincidence; it is not the matrix producer.)

- **Producer.** The CC coordinator `0x63da2274` calls `0x63ae2714(ctx, out)` at
  `0x63da228a`. The calculator calls `0x63ae4df0(sp+0x58, mode, ctx)` to fetch a base
  3×3 matrix, applies YCbCr-domain fixed-point steps (constants `0x48ee`, `0xb716`,
  `0xa8a`, `0xf574`) on a branch, and forces each row to sum to `0x400` by adjusting
  the largest element (`0x63ae2a0c–0x63ae2b38`). It can transform the result with
  `0x63ae2670`, copies it with `0x63da22e8` to `[out+0xc]+0x50`, and copies it again
  element by element (index `0x28 + 3·(i mod 3) + i/3`) to `[[out]]+0xae`. With CC
  output base `0x643cc384` these correspond to the live copies at +0x12c and +0x686.
- **Selector.** `0x63ae4df0` accepts mode ≤ 0x1a. For mode ≤ 14 it reads
  `table + 0x200 + 0x12·mode`; for 15–23 it reads `table + 0x1100 + 0x12·(mode−15)`.
  Entries are 0x12 bytes (9 int16). `table` comes from GOT slot `0x645c630c`, which
  links to `0x6444a044` (also read live). The table area is zero in the image, so it is
  filled at runtime.
- **Live table** (bounded `/dev/mem` reads after a code-mapping check). A0–A11 and A14
  plus B0–B8 are row-normalized Q10 matrices; A12 and A13 are zero. The observed live
  matrices equal table entries exactly: S-Gamut3.Cine (first reading) = A0
  `[1241,-205,-12; 62,1116,-154; 30,-251,1245]`; Black & White = A4
  `[1341,-205,-112; -61,1116,-31; 27,-170,1167]`; S-Gamut3.Cine after the B&W round
  trip = B8 (mode 23) `[1181,-188,31; 51,1024,-51; 118,-356,1263]`. PP Saturation +16
  did not alter the matrix. The mapping from PP Color Mode (and context) to the selector
  index is not yet resolved: S-Gamut3.Cine selected A0 once and B8 once.
- **Implication for ARRI.** As with record 31 for gamma, a RAM-only replacement of the
  S-Gamut3.Cine table entries is a data-only override. One option is M′ = A·M, with A
  the S-Gamut3.Cine→AWG3 Q10 matrix `[[998,24,2],[-91,1097,18],[-36,39,1021]]` and rows
  renormalized to 1024. Open before any write: the exact index mapping; whether the
  table is refilled at boot (inferred from the zero image area, not yet observed); and
  that this matrix acts on the recorded Movie signal (supported by the visible B&W
  change, not yet isolated).

**Matrix index** (`matrix-index-byte.txt`, `colormode-index-lut.txt`,
`index-writer-candidates.txt`). At entry, `0x63ae2714` reads the selector index from byte
`[out+0xc]+0`. Indices 24 and 25 take an interpolation path (tables at +0x332 / +0x368
from a GOT object); all others call `0x63ae4df0`. Since `[out+0xc]+0x50` is CC output
+0x12c, the index byte is at `0x643cc460`. Across the snapshots it reads 0 (baseline,
sat16, capA2, capB2; Color Mode 11, matrix A0), 4 (B&W, Color Mode 8, matrix A4) and
23 (sg2, Color Mode 11 after the B&W round trip, matrix B8). The next byte reads 0, 4
and 0x13 in the same states. The index therefore fully explains the live matrix. The
same Color Mode 11 produced index 0 before any Color Mode change in this boot, and 23
afterwards. Inference: the index is recomputed on a Color Mode change and sits at 0
until then. A byte-pattern search for a static Color Mode → index table (entry 8 = 4,
entry 11 = 23) found no convincing table, and the writer was not isolated in
`0x63ae3cf0` / `0x63ae1a50`. Next: confirm that Color Mode 11 consistently maps to 23
with one more round trip; an ARRI matrix override would then target entry B8
(`0x6444a044 + 0x1100 + 0x12·8 = 0x6444b1d4`).

**Index confirmation and ARRI matrix candidates** (`snapshots/rt1.json`,
`arri_matrix_candidates.py`, `arri-matrix-candidates.json`). After a further Color Mode
round trip (S-Gamut3 → S-Gamut3.Cine), the index byte `0x643cc460` read 23 (next byte
19), and both live copies (+0x12c and +0x686) equal B8. The mapping Color Mode 11 →
index 23 → entry B8 (`0x6444b1d4`) is stable after a Color Mode change. Offline
candidates, assuming B8 is a linear camera-RGB → S-Gamut3.Cine-like matrix applied before
the EC gamma (unverified), use left-multiplication by a linear primary conversion and
then the producer's own row-normalization rule:
- stock B8: `[[1181,-188,31],[51,1024,-51],[118,-356,1263]]`
- AWG3: `[[1152,-159,31],[-49,1108,-35],[78,-309,1255]]` (a subtle change)
- Rec.709 primaries (for pairing with the Classic tone curve):
  `[[1884,-828,-32],[-167,1571,-380],[84,-634,1574]]`

All rows sum to 1024; the maximum |coefficient| is 1884, which fits int16. Before any
write: obtain user approval for the B8 swap (same guarded single-page mmap helper);
check, after the next restart, that B8 is refilled with stock values (battery-pull
revert); and judge the result visually and with one capture.

## 2026-11-16 follow-up — approved Rec.709 matrix write into table entry B8

The user approved a RAM-only test write of the Rec.709 candidate into B8. Receipt:
`artifacts/analysis/arri-movie-color-2026-11-16/b8-write-test.json`. The helper
`mods/arri_isp/gamma_poke/gamma_poke.c` is now v4 (variable length, a multiple of 4 up
to 420 bytes, same single-page, alias-window and exact-match guards; build
`artifacts/tests/arri-gamma-poke-build-2026-11-16-v4/gamma_poke`, SHA-256 `89f6f412…8c4b`).

- Before the write: B8 at PA `0x0444b1d4` was stock, the index byte was 23, and both
  live copies (+0x12c and +0x686) held stock B8. Because the helper writes words, the
  block was 20 bytes: the 18-byte matrix plus the 2 following bytes, read live and
  written back unchanged (`b8-expected20.bin`, `b8-rec709-20.bin`). Helper output:
  `WROTE; map+0ms new; map+20ms new; map+200ms new; pread new`, exit 0.
- Samples at 0.27, 1.03, 3.03 and 8.05 s: entry = Rec.709 candidate; index = 23;
  **both live copies still stock.** The CC matrix is not recomputed per frame in this
  state; it is refreshed on events such as a Color Mode change, consistent with the
  earlier observations. Applying the entry requires a recompute trigger (for example a
  Color Mode round trip).
- State left for the observation step: B8 holds the Rec.709 matrix; `/tmp_bt/arri_mx`
  holds `poke`, `stock.bin` and `rec709.bin` for the restore. Uptime 3064.29 s; the
  debugger answered `PONG`.

**Applied after a Color Mode round trip** (`b8-after-roundtrip.json`). After the user's
S-Gamut3 → S-Gamut3.Cine round trip, the user reported visibly changed colors. Live
reads: entry B8 = Rec.709 candidate, index 23, and **both live copies (+0x12c and
+0x686) = `[1884,-828,-32; -167,1571,-380; 84,-634,1574]`**; the debugger answered
`PONG`. This shows on hardware that the live Movie color matrix is copied from the
per-Color-Mode table on a recompute event, and that a RAM-only replacement of the
S-Gamut3.Cine entry changes the visible image. Together with the record-31 gamma change,
two of the three ISP layers now have working data-only overrides. Still open: the exact
recompute triggers (Color Mode change confirmed; others unverified); the post-boot index
0 (entry A0) until the first Color Mode change; that the table is refilled at boot (to
be checked after the next restart); the matrix's precise position relative to the EC
gamma (the visual result supports pre-gamma); and the 3D LUT, still without a Movie
target. The restore files remain in `/tmp_bt/arri_mx`.

## 2026-11-16 follow-up — ARRI Classic Color lifecycle mod staged

Sources: `mods/arri_isp/arri_color_mod/` (source, linker script, data generator,
packer, Unicorn test, WSL build). Receipts: `artifacts/tests/arri-color-mod-rec709-2026-11-16/`
(`build.json`, `unicorn-test.txt`, `camera-stage.json`) and
`artifacts/tests/arri-mod-cave-2026-11-16/cave2-live-read.json`.

- **Cave.** `/usr/lib/libcompassutil.so` RX zero tail, `0x0414f7f0..0x04150000`
  (2064 bytes). It read all zero in live `im.elf` memory in this boot. The gamma mod's
  code is resident in its own cave.
- **Mod.** `ARRIColor.mod` ("ARRI Classic Color"), 496 bytes (SHA-256 `fe90690e…fcd8`),
  428-byte code, flag 2, no hooks. ON writes the Rec.709-primary matrix into entry B8
  (PA `0x0444b1d4`) with 9 halfword stores, only if the entry equals stock; OFF restores
  stock only if the entry equals the ARRI matrix. Each call logs
  `arri_color on=N result=C` to `/tmp_bt/mods/arri_color.log`. The data generator
  requires the stock matrix to equal the live table entry, whose third row sums to 1025
  in Sony's data, and requires 1024 row sums for the new matrix. The Unicorn test passed
  ON, repeated ON, OFF and foreign content, and it confirmed that the bytes around the
  18-byte entry stay untouched.
- **Camera.** The test write was restored with the guarded helper (`WROTE`, all reads
  `new`, exit 0), and B8 then read stock. `/tmp_bt/arri_mx` was removed (rc 0).
  `ARRIColor.mod` was pushed to `/tmp_bt/mods` with readback verification. Uptime was
  3546.85 s and the debugger answered `PONG`. The live matrix copies still held the
  Rec.709 values until the next recompute. Pending: toggle in the Mods tab, then a Color
  Mode round trip for the change to take effect.

**Mod not listed: scan-once loader.** The user reported that "ARRI Classic Color" did not
appear in the Mods tab. `ml_build_tree` (`tools/native_camera/mod_loader.c`) scans the
mod directories only while `st->scanned` is zero, then sets it, so a loader instance
never sees mods added after its first menu build. A restart is required: `/tmp_bt` is a
RAM filesystem, and the guard refuses runtime unload after menu use (-24).
`mods/arri_isp/arri_gamma_mod/install_after_boot.py` now stages both mods and also
records B8, the matrix index and record 31 word 0 after boot, to check the refill-at-boot
behavior.

**Post-restart install of both mods**
(`artifacts/tests/arri-gamma-mod-classic-2026-11-16/install-after-boot-20261116-161202.json`).
After the user's restart (menu not opened), the SD loader was removed, then the
lifecycle loader passed check and install (open-item hook `dff800f0 014f5e04` →
`0x045e4f01`). `ARRIClassic.mod` and `ARRIColor.mod` were staged with readback
verification. Post-boot reads:
- Record 31 word 0 = `0x1500`, i.e. stock. The gamma mod was ON before the restart, so
  this is a second observation that a restart reverts the record.
- B8 = stock. It had already been restored before the restart, so this does not test
  the refill.
- **Matrix index = 23 at uptime ~87 s, before any Color Mode change.** This supersedes
  the earlier inference that the index sits at 0 until the first Color Mode change. The
  earlier index-0 readings were taken in an earlier boot, and their cause is unresolved
  (possibly a CC state that had not yet been initialized).

The debugger answered `PONG`, and `/tmp_bt` held only the expected entries. Pending:
the Mods-tab toggle of "ARRI Classic Color" with a Color Mode round trip, then a
restart with the color mod ON to test the table refill.

## 2026-11-16 follow-up — Arrivederci combined mod

The user confirmed that both mods worked. Logs
(`artifacts/tests/arri-color-mod-rec709-2026-11-16/toggle-logs.txt`) show
`arri_gamma on=1 result=w` and `arri_color` on=1, on=0, on=1, all `w`, with loader
entries `mod 0414f7f1/0414f7f0` (color) and `mod 05662761` (gamma). At the user's
request, both layers were packed as one mod, **Arrivederci**
(`mods/arri_isp/arrivederci/`; deliverable `mods/arri_isp/arrivederci/Arrivederci.mod`,
1404 bytes, SHA-256 `6b3d383a…cf36`; build receipt
`artifacts/tests/arrivederci-2026-11-16/`). One lifecycle entry runs two independent,
guarded single-page swaps: record 31 words 0–95 (word stores; exponent words untouched)
and matrix entry B8 (9 halfword stores). It logs
`arrivederci on=N gamma=C color=C`. The code is 1336 bytes in the libtestcmd.so cave
(2208 bytes), so it shares that cave with the earlier gamma-only test mod. The Unicorn
test passed ON from stock (`w/w`), OFF from ARRI (`w/w`) and a mixed state (`a/x`), with
all surrounding bytes untouched. The mod requires the lifecycle loader; the current SD
boot-package loader would list it without calling it. Not yet run on the camera as a
combined file.

## 2026-11-16 follow-up — the color matrix table is persisted by Sony's backup system

**This supersedes the "RAM-only / reverts on restart" claim for the color matrix.** Receipts:
`artifacts/tests/arrivederci-2026-11-16/install-after-boot-20261116-162038.json`,
`artifacts/analysis/arri-movie-color-2026-11-16/persistence-check-backup.json` and
`persistence-restore-b8.json`.

- **Observation.** The user restarted while the ARRI Classic Color mod was ON (B8 =
  ARRI). After the boot, at uptime ~115 s, B8 read the **ARRI** matrix
  `[1884,-828,-32; …]`, while record 31 read stock (`0x1500`). The matrix index read 0
  this time; the index behavior after boot is therefore not consistent (23 in the
  previous boot) and remains unresolved.
- **Cause found.** `/setting/Backup.bin` (1,171,036 bytes, SHA-256 `1793518c…c530`)
  contains the **stock** B8 bytes at file offset 207840 (A0 at 203856).
  `/setting/Backup.bak` (same size, `d3a76e55…5fb8`) contains the **ARRI** matrix at
  the same offset 207840 (A0 at 203856). The table at `0x6444a044` is part of the
  camera's persistent backup data (compare E0030/E0031 backup units). The firmware
  wrote the RAM table into a backup file on the user's power cycle and restored it at
  boot. No host or helper tool wrote flash; the persistence came from Sony's own backup
  mechanism acting on the modified RAM. The record-31 gamma override is not affected,
  because it comes from the read-only image.
- **Containment.** With the guarded v4 helper (it found the ARRI matrix, then wrote
  stock with all read paths `new`, exit 0), B8 is stock in RAM again. The staged
  `Arrivederci.mod` was removed from `/tmp_bt/mods` before the first menu scan, so it
  could not be enabled in this boot. The menu anchor was still zero and the debugger
  answered `PONG` at uptime 216.85 s. No flash write was attempted.
- **Pending.** After the next normal power cycle, verify that the camera's own backup
  save wrote stock back (both files at offset 207840) and that B8 reads stock after
  boot. Until the backup write timing and the .bin/.bak roles are understood, any
  color-matrix override must not use the backup-backed table. Alternatives to evaluate:
  patch only the computed live copies (CC output +0x12c / +0x686, not backed up; they
  are overwritten on recompute) under a recompute-aware trigger; or restore the table
  before every power-off and verify the backup.

**Backup restored by the camera's own save** (`persistence-after-powercycle.json`). With
stock B8 in RAM, the user power-cycled normally. At uptime 250.57 s:
`/setting/Backup.bin` (`67e4112f…ecba`) and `/setting/Backup.bak` (`7f1ab318…974a`) both
hold the **stock** B8 bytes at offset 207840. Both hashes differ from the previous boot,
so the camera rewrote both files during the power cycle. Live B8 = stock; record 31
word 0 = `0x1500` (stock); the debugger answered `PONG`. The camera is back to its
original color state with no host-side flash write. Observed behavior: the backup
captures the RAM table at power-off and restores it at boot. Restoring RAM before a
normal power-off is therefore a working recovery path, while a battery pull with
modified RAM would preserve whatever the last backup save held (inference).

## 2026-11-16 follow-up — backup-safe color layer: the computed copies are not backed up

Receipts: `artifacts/analysis/arri-movie-color-2026-11-16/copies-backup-check.json`
and `copies-backup-check2.json` (read-only).

- In this boot the index byte read **0** with Color Mode S-Gamut3.Cine, and both live
  copies (CC output +0x12c and +0x686) equal entry **A0**
  `[1241,-205,-12; 62,1116,-154; 30,-251,1245]`. Two of three boots now showed index 0
  after power-on; one showed 23. Every Color Mode change so far has produced 23 (B8) for
  S-Gamut3.Cine.
- `/setting/Backup.bin` contains the stock B8 bytes exactly once (offset 207840) and
  the A0 bytes exactly once (offset 203856). These are the table entries; their
  3984-byte separation matches the RAM table layout. The live copies, which held these
  same values at the time of the reads, do not appear separately, so the computed copies
  are not part of the backup. A simple linear RAM→file offset does not hold (256-byte
  check failed), so the copies' exclusion rests on content, not on address range.
- Open: whether the hardware consumes the copies continuously or only at recompute time.
  Proposed test (needs approval): a guarded RAM write of the ARRI matrix into both copies
  without any PP change, followed by a visual check. Restore by writing back the saved
  original, or by any Color Mode change (which recomputes the copies).

**Copies write test** (`copies-write-test.json`, `copy-*-orig20.bin`, `copy-*-arri20.bin`).
With user approval and no PP change, the guarded v4 helper wrote the Rec.709-primary ARRI
matrix into both CC output copies. Blocks were 20 bytes: +0x12c at PA `0x033cc4b0`
(matrix + 2 trailing bytes unchanged), and +0x686 via aligned PA `0x033cca08` (2 leading
bytes unchanged + matrix). Before the write both copies held A0 and the index was 0.
Each helper call reported `WROTE` with all reads `new`, exit 0. Samples at 0.53, 1.04,
3.02 and 8.04 s: both copies ARRI, index 0; no automatic overwrite. The originals are
staged in `/tmp_bt/arri_cp` for the restore. Pending: the user's visual check.

**Copies test result: no visible effect** (`copies-restore.json`). The user saw no color
change with the ARRI matrix in both copies and no PP change. The hardware therefore does
not read these copies continuously; the matrix reaches the image when CC recomputes and
publishes. When the restore ran later (uptime 592.63 s), both guarded writes refused
with `GUARD mismatch` (exit 3), because both copies already equalled the saved
originals: the camera had recomputed and overwritten them in the meantime. The trigger
is unknown (possibly a menu interaction). The helper files were removed and `/tmp_bt`
matched baseline. Conclusion: a data-only color override must be in place **at recompute
time**. The only data source found so far is the backup-backed table, so a backup-safe
color layer needs one of: (a) the hardware publication path written after recompute,
(b) a recompute trigger the mod can invoke while the table is patched only transiently,
or (c) a controlled table patch that the same mod reverses, accepting backup persistence
until it is turned OFF.

## 2026-11-16 follow-up — tracing the matrix publication path

Receipts are in `artifacts/analysis/arri-matrix-publish-2026-11-16/`.

- **Coordinator order** (`coordinator.txt`): `0x63da2274` calls `0x63ae3cf0`, `0x63ae1a50`,
  the matrix calculator `0x63ae2714`, the 3D LUT controller `0x63ae08a0`, `0x63ae0c44`,
  `0x63da24d0`, and finally tail-calls `0x63ae30e4`. None of the post-matrix routines
  loads the matrix offsets directly (`matrix-readers.txt`). The LUT controller head
  selects LUT presets (0xa2-byte stride) rather than visibly consuming the matrix
  (`lut-controller-head.txt`). That "the matrix is baked into the 3D LUT" is
  therefore **not shown**.
- **CC output dispatch table** (`dispatch-matrix-scan.txt`): none of the 77 callbacks at
  `0x645804bc` loads +0x12c, +0x686, +0xae, +0x50, +0x5d8 or +0xdc from its arguments,
  apart from two coincidental hits.
- **Grouped CamCore publisher hypothesis rejected** (`publisher-ctx-check.json`).
  Assuming the matrix setter `0x63bf9d48` in `0x63dd8f60` reads ctx+0x41a0+0x72 = X+0xae
  implies ctx = `0x643c87f8`. Live, its CamCore handle slot ctx+0x9b0 and first 16
  bytes are zero, so the hypothesis is not supported.
- **Table pointer users** (`table-got-users.txt`, `backup-descriptor.txt`): GOT slot
  `0x645c630c` (→ `0x6444a044`) is loaded at 91 sites (`0x63ae1042`–`0x63b0981a`), so it
  is the base of a whole CC adjustment structure, with the matrix table at +0x200..+0x11a2.
  The only other absolute reference is `0x64561444`, inside a pointer list that also
  holds `0x64351160` (the feature global) and `0x6444c424` (the next object). That
  bounds the structure at ≤ 0x23e0 = 9184 bytes. If the backup system locates the
  structure through that list while CC uses the GOT slot, then redirecting the GOT slot
  (image data, so it reverts on restart) to a modified copy would change the matrix at
  recompute without changing backed-up data. This is untested and needs (1) proof that
  the backup does not use the GOT slot, and (2) a verified-unused 9 KiB LIRO RAM region
  for the copy. Neither has been established.
- **Status.** The hardware write of the matrix has not been located; the computed copies
  are not consumed continuously.

## 2026-11-16 follow-up — GOT-redirect feasibility

Receipts are in `artifacts/analysis/arri-got-redirect-2026-11-16/`.

- **CC only reads the adjustment structure** (`got-slot-writes.txt`,
  `store-0x63ae3570.txt`). Across the 91 GOT-slot sites there are 276 direct reads
  through the structure pointer (maximum direct displacement 0xf00; the matrix table is
  reached through added offsets). The single flagged store (`0x63ae3570`) targets an
  output buffer (`r6+r3`), not the structure. A byte-identical copy would therefore
  behave the same for CC.
- **The backup system does not use the GOT slot** (`descriptor-list-refs.txt`,
  `list-got-users.txt`, `list-users-strings.txt`). The 169-entry pointer list at
  `0x64561440` (index 1 = `0x6444a044`) is reached through GOT entry `0x645cbf54` (offset
  0xa7bc) by six sites in `0x638ffbfc–0x639004f4`. Their diagnostics are
  `CamShadowReadBackupAll`, `CamShadowWriteBackupAll`, `MRReadBackup`, `MRWriteBackup`,
  `Backup_nread_multi` and `Backup_write_multi`. The backup reads and writes the
  structure through the list's static pointer, so a GOT redirect would leave backed-up
  data on the original structure.
- **No verified-free region** (`zero-runs.txt`, `zero-run-candidates.json`,
  `zero-run-owner.txt`, `stack-check.txt`). The image has three zero runs of at least
  9216 bytes. `0x64051839–0x644f1000` is runtime data (it contains the CC structure).
  `0x644f643c–0x644f9000` (11204 B) and `0x644fd384–0x64500fe0` (15452 B) are live-zero.
  However, they sit inside a separately linked blob that starts at `0x644f1000`: ARM
  exception-vector code with literals `0x0100d000`, `0x0100d800`, `0x0100e700`, an MMU
  page table at `0x644f9000` (entries `0x00000c12`, `0x00001c12`, …), and the second run
  referenced from within that table (`0x644fa914`). With the blob linked at
  `0x01000000`, those literals map to `0x644fe000`, `0x644fe800` and `0x644ff700`
  (stacks), so the first run is plausibly that core's zero-initialized data (inference).
  Neither run is safe to use.
- **Status.** The GOT redirect is sound in principle (read-only consumer, list-based
  backup) but lacks a safe 9 KiB placement. Not attempted.

## 2026-11-16 decision — Arrivederci uses the reversible table patch

The user chose the reversible table patch for the color layer. The existing
`mods/arri_isp/arrivederci/Arrivederci.mod` (SHA-256 `6b3d383a…cf36`, Unicorn receipt
`artifacts/tests/arrivederci-2026-11-16/unicorn-test.txt`) already implements it, so it was
not rebuilt. Its README replaces the earlier "do not use" warning with explicit semantics:
the tone curve reverts at boot; the color matrix persists through Sony's backup until the
mod is turned OFF (content-checked, so this works in a later boot) and a normal power-off
saves stock; a battery pull keeps the last saved backup. The recovery sequence was verified
earlier the same day (both backup files rewritten with stock after restoring RAM and a
normal power cycle). The combined file has not yet been toggled on the camera; its two
layers were verified separately.

## 2026-11-16 follow-up — ALEXA 35 converter targets

The converter now fits S-Log3 / S-Gamut3.Cine to AWG4/LogC4 and to the
official ALEXA 35 Gamma 2.4 Rec.709 display transform. AWG4 follows the
D65 XYZ matrix and LogC4 equations in the
[ARRI specification](https://www.arri.com/resource/blob/278790/f3318e8c9c65617d8c5ca3f8b3e32051/2023-05-arri-logc4-specification-data.pdf).
The run used the 33-point LUT from the
[ARRI package](https://www.arri.com/en/learn/camera-systems/image-science/log-c).
ZIP SHA-256 `1e2eda8a5273f205a76473f024de24b182cbd0c9fde877d27b4cfb2c0ec7547d`.
Cube SHA-256 `ab9618bc72dc3fab3e1d23a0c25ffd9cfb0491ed715448a629e44939cbd3f4b4`.

From the converter directory, using Python 3.14, NumPy 2.4.3 and Pillow 12.2.0:

```powershell
python -B -m lut_cli --arri alexa35 --gamma-model compare --logc4-display-cube ../artifacts/analysis/ARRI_LogC4-to-Gamma24_Rec709-D65_v1_33.cube --charts --raw -o ../artifacts/tests/alexa35-converter-final-2026-11-16
```

| Target | Gamma model | Mean Delta E00, gamma + matrix | Mean with LUT | P95 with LUT | Max with LUT | Exported LUT nodes at rails |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| AWG4/LogC4 | Uniform | 0.44 | 0.28 | 1.29 | 3.72 | 0/405 |
| AWG4/LogC4 | Movie record 31 | 1.07 | 0.83 | 2.75 | 11.33 | 0/405 |
| ALEXA 35 Rec.709 | Uniform | 1.49 | 0.42 | 1.51 | 5.09 | 0/405 |
| ALEXA 35 Rec.709 | Movie record 31 | 1.47 | 0.53 | 1.56 | 8.06 | 0/405 |

The metric uses 629 solid patches in the same Rec.709 display view, with
8-bit simulated output. Preview charts use the 4:2:0 box-filter model.
All four runs had zero nodes with raw demand beyond the signed 7-bit
range. The synthetic samples do not represent paired camera captures.
The Movie inverse holds at stock endpoints and selects the first knot for
the 803 plateau. At least one input channel exceeded the stock extent
in 8.43% of chart samples. The signal order and encoder behavior on camera
remain separate verification tasks.

`compare_arrivederci.py` in the output directory matched all 96 positions
to the retained knot replay and all stock codes and metadata words to the
live read receipt. It evaluated the existing Arrivederci Classic gamma and
its matrix in simulator order against the Rec.709 target. Mean/p95/max
Delta E00 were 1.51/7.38/24.69. The Arrivederci source record uses values
shifted left by six and exponents in record order. The converter Movie
export is a working-format block with unshifted codes and pair-swapped
metadata. Each Movie gamma output is 420 bytes.

The converter suite passed 18 tests with pytest 9.2.0. An initial
reference test incorrectly expected 117.45 linear to encode to 1.
Equation 2 gives 0.87042628, and the corrected reference test passed.
A Tk smoke run exercised both preset selectors and the Movie toggle with
the official cube. The broader repository suite ran 1,189 tests and
reported 7 failures, 144 errors and 36 skips, including emulator dependency
errors and status snapshot mismatches.

**Arrivederci staged** (`artifacts/tests/arrivederci-2026-11-16/install-after-boot-20261116-202316.json`).
A first restart was not usable: at uptime 43.70 s the anchor was already nonzero
(the menu had been displayed), so the install was not attempted. After a second restart
with the menu closed, the SD loader was removed and the lifecycle loader was checked and
installed (open-item hook → `0x045e4f01`). `Arrivederci.mod` was staged in `/tmp_bt/mods`
with readback. Post-boot: B8 = stock (consistent with the backup restored earlier),
matrix index 0, record 31 word 0 = `0x1500` (stock). Uptime 90.60 s; the debugger answered
`PONG`. Pending: the user's on-camera toggle of the combined file.

**Arrivederci on-camera result (user photos).** The user reported that the combined mod
"looks right" and shared two stills from the camera (EXIF model ILCE-6400):
`_DSC1739.JPG` (20:27:59, Arrivederci ON) and `_DSC1740.JPG` (20:28:19, no PP). The
photos stay outside the repository because they show a person; only statistics are
recorded. Whole-frame statistics on 6000×4000 sRGB JPEGs:

| | Arrivederci | No PP |
| --- | ---: | ---: |
| mean RGB | 0.177 / 0.165 / 0.163 | 0.226 / 0.198 / 0.179 |
| luma p1 / p50 / p99 | 0.035 / 0.122 / 0.475 | 0.001 / 0.097 / 0.665 |
| mean HSV-style saturation | 0.201 | 0.381 |

The Arrivederci frame shows lifted blacks, compressed highlights and lower saturation,
with less red skin. This is consistent with a low-contrast ARRI Classic-style rendering
versus Sony's default. Framing and pose differ between the frames, so this is a
qualitative comparison, not a controlled measurement. The camera was disconnected from USB
when this was checked, so the mod log and layer state were not read after the toggle.
Still stills, not Movie recording.

## 2026-11-16 follow-up — Arrivederci load check and SD-card inventory

Receipt: `artifacts/tests/arrivederci-2026-11-16/toggle-check.json` (read-only, MSC,
uptime 1045.87 s).

- **Load check: correct.** `/tmp_bt/mods/arrivederci.log` = `arrivederci on=1 gamma=w
  color=w`. The loader log shows `enter_idx 0` / `mod 05662761`. Mod code is resident at
  `0x05662760` (`f0b50646…`). Record 31 = ARRI curve, and table entry B8 = ARRI matrix.
- **Color layer not in effect at the time of the check.** The live matrix copy (CC
  output +0x12c) = A0 `[1241,-205,-12; 62,1116,-154; 30,-251,1245]` with index 0, so the
  camera was not selecting B8. The user's photos are stills. Whether stills use index 0
  (A0) while Movie after a Color Mode change uses 23 (B8), or whether the round trip was
  not performed, is unresolved. The photo look may therefore come from the tone curve
  alone. Because B8 currently holds the ARRI matrix with the mod ON, a normal power-off
  would save it to the backup (see the persistence section).
- **SD write held.** The user asked to save the mod to the SD card and mark it functional
  "if everything is fine". Because of the open color-layer question, neither was done.
- **SD inventory (`E:\`, read-only).** The boot reader
  (`artifacts/publish/mod-loader-sd/mods/sd_mod_loader/card_boot_read.c`) reads exactly
  `boot-mod-loader\{native_mod_loader_guard_arm (692516), mod-loader-check.req (3680),
  mod-loader-install.req (3680), ModsTabIcon.rle (1486)}` and `mods\MenuTouchFull.mod
  (2940)`. These are **in use**, together with `boot-mod-loader\SHA256SUMS`. Not read by
  the boot path, so test leftovers: `mod-loader\` (15.11 stage package: `start.sh`, guard,
  5016-byte check/install requests) with root `SHA256SUMS` (covering `mod-loader/` and
  `mods/`), and the root logs `A6400LD.LOG` (25189 B), `A6400K.LOG` (80313 B) and
  `A6400RCPT.JSON` (2389 B; an `a6400-lived` UVC-session receipt with `sd_log`). `DCIM\`,
  `PRIVATE\`, `AVF_INFO\` and `System Volume Information\` belong to the camera and OS.
  Nothing was deleted or written. Note: an `Arrivederci.mod` on the card would not be
  auto-loaded. The reader stages only `MenuTouchFull.mod`, the camera does not mount the
  card at `/mnt/mmca1`, and the SD package installs the non-lifecycle loader.

**Photo-mode mapping.** The user confirmed the stills were taken in photo mode, after a
Color Mode round trip. With index 0 and live copy A0 at the check, **S-Gamut3.Cine selects
A0 in photo mode**, while the earlier Movie-mode round trips selected 23 (B8). Arrivederci
v1 patches only B8, so its color layer applies to Movie only; the photo look came from the
tone curve. Before extending the mod to A0: verify that no other photo-mode Color Mode
also maps to index 0 (pending a read with another Color Mode selected).

**Photo mode: the index does not follow Color Mode** (`photo-colormode-movie.json`). In
photo mode with PP Color Mode set to Movie (state+0x19c = 5, Gamma 12), the index byte read
0 (next byte 0), and the live copy was A0, the same as with S-Gamut3.Cine in photo mode.
In photo mode this CC instance's index therefore does not track Color Mode, so A0 is not
exclusive to S-Gamut3.Cine: patching A0 would affect every photo-mode Color Mode. A0 was
not patched. The still-capture color is likely selected at develop time (the
`SetDevelopParam` / pre-review path with stack-temporary bundles), which has not been
traced. Scope of Arrivederci v1 as built: tone curve in Movie and photo; color matrix in
Movie only (B8, after a Color Mode change).

## 2026-11-16 — Arrivederci verified functional (scoped) and saved to the SD card

Receipt: `artifacts/tests/arrivederci-2026-11-16/movie-check.json`. In Movie mode, after the
user's Color Mode round trip (Color Mode 11, Gamma 12): the log shows `arrivederci on=1
gamma=w color=w`; index 23; **both live matrix copies (+0x12c, +0x686) = ARRI**
`[1884,-828,-32; …]`; B8 = ARRI; record 31 word 0 = `0x300` (the first ARRI curve word,
12 << 6). The combined file is therefore verified on hardware: tone curve in Movie and photo
mode, color matrix in Movie mode. Photo-mode color is out of scope (A0 is shared across
photo-mode Color Modes).

At the user's request the mod was saved to the SD card. The camera-side `/proc/mounts`
showed no mmc/`/tmp_bt/sd` mount, so the host had sole access to `E:\` in MSC mode.
`E:\mods\Arrivederci.mod` was created without overwriting anything (SHA-256 identical to the
repository file `6b3d383a…cf36`), together with `E:\mods\Arrivederci.txt` (status note:
functional, scope, requirements, persistence). No other card content changed.
`mods/arri_isp/arrivederci/README.md` is marked functional with the same scope. Nothing was
committed.

**SD leftovers: backed up, removal blocked.** The user approved removing the test leftovers.
All eight files were first copied to `artifacts/hardware/sd-leftovers-removed-2026-11-16/`
with per-file SHA-256 verification (`SHA256-copied.txt`): `mod-loader/{start.sh,
native_mod_loader_guard_arm, mod-loader-check.req, mod-loader-install.req}`, `SHA256SUMS`,
`A6400LD.LOG`, `A6400K.LOG` and `A6400RCPT.JSON`. The host tool's path protection then
blocked `Remove-Item` on `E:\mod-loader` before any deletion ran. A follow-up listing showed
the card unchanged. The block was not bypassed. The user can delete these items manually;
the boot package (`boot-mod-loader\`) and `mods\` must stay.

**Arrivederci not shown after a normal boot (expected).** At uptime 96.10 s the SD boot
receipt showed the SD package's standard sequence (`SD_BOOT_INSTALLED`, theme
`0x15e91b68`, the 3096-byte loader). `/tmp_bt/mods` held only `MenuTouchFull.mod` and
`ModsTabIcon.rle`; the anchor was nonzero (menu used); the hook pointed to the original
loader (`0x045e4edd`). The card's `E:\mods\Arrivederci.mod` is not used, because the reader
stages a fixed list (only `MenuTouchFull.mod` from `mods\`), the camera does not mount the
card at `/mnt/mmca1`, and the loader is the non-lifecycle build. A card-only change cannot
fix this: the lifecycle requests have the same size (3680 B) but `boot.sh` verifies
hard-coded SHA-256 values of the staged files (for example line 73 for `touch.mod`), and
both `boot.sh` and the reader live in `/setting` (internal flash). Options: per boot, run
`install_after_boot.py` before the menu is opened; or update the SD boot package (`boot.sh`
hash list, the reader's file list, the card's lifecycle requests plus `Arrivederci.mod`),
which writes `/setting` and needs explicit user approval.
