# E0600 — `av-cam.bin` Still RAW Develop Selectors (`Stage_Raw`) and `IMX284` Register Writers

- **Date:** 2026-11-18
- **Target:** `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked/av-cam.bin` (`ORIL_BASE = 0x635C6000`, SHA-256 `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`)
- **Tool:** `arm-linux-gnueabi-objdump` (WSL) Thumb-2 disassembly + Python PIC xref scan
- **Scope & constraints:** Strictly read-only static analysis. No physical camera access and no firmware modifications.

---

## 1. Phase 1 — Still RAW Capture, Develop, and Encode Pipeline

### 1.1 Selectors (`0x638bea90..0x638bec00`)
- **`CaptureCorrectRaw_Selector.cpp` (`0x638bea90..0x638beaf0`, xref `0x638beab6`):**
  - Reads `[r3+0x88]+0x10` (fallback `[r3+0x8c]+0x10` if `== 9`), passes `(r0, r1=8, r2=index)` to `0x63d35966` / `0x638ddf50`, and copies a 20-byte selector descriptor (`mla r5, 20, r6, r3`) to `r4`.
- **`CaptureDevelop_Selector.cpp` (`0x638beb04..0x638beb6c`, xref `0x638beb32`):**
  - Reads `[r3+0x88]+0x18` (fallback `[r3+0x8c]+0x18` if `== 30`), passes `(r0, r1=10, r2=index)` to `0x63d35966` / `0x638ddf50`, and copies a 20-byte selector descriptor to `r4`.
- **`CaptureEncode_Selector.cpp` (`0x638beb80..0x638bec74`, xref `0x638bed9e`):**
  - Reads `[r3+0x88]+0x08` (fallback `== 21`), dispatches via `tbb` on `r5 - 17` (`0..4`).
- **CamCore Develop & SACC Entry Points:**
  - `[SDF][INT_ERROR] ExecCamCoreDevelopStart Err!` (`0x63eca3d3`): xrefs at `0x636bb64c` and `0x636bf3fa`.
  - `Stage_SACC.cpp` (`0x63f0d9a1`): xrefs at `0x638d78de` and `0x638d7912`.

### 1.2 `Stage_Raw.cpp` (`0x638d5070..0x638d51a0`, `0x638d5540..0x638d567e`)
- `0x638d50e4..0x638d519a` branches on mode `[r0+0x774]` (`5` or `6`), sub-mode `[r0+0x840]` (`1` or `2`), and `[r0+0x200]`, updating `[r4+0x6730]`.
- `0x638d5540..0x638d567e` retrieves RAW `(width, height)` via `0x638df224` (`[sp+0xbc]`) / `0x638def74` (`[sp+0xb8]`), computes pixel area ratio (`10.0 * (w * h) / (r5 * [r4+0x6b00])`), and aligns/crops output dimensions against `[r4+0x22c]` and `[r4+0x230]` before calling `0x63d3579e`.

---

## 2. Phase 2 — `C_ImagerCtrl_IMX284` Hardware Register & `1H1H`/`1H2H` Writers

- **`0x6378ef18` (Exposure / Gain Register Packet Packer):**
  - Packs 12-bit / 16-bit exposure (`r8`, `r9`) and gain/mode (`r6`) byte-by-byte into sensor register buffers (`[reg_buf + 2]` via `strb.w` and `bfi`).
- **`0x637902f4` (Mode Switch Coordinator):**
  - Validates `[this+0x11c]` (`!= 17`), checks `[this+0x11c]` against `{11, 55, 104, 78, 47}`, resolves `drive_mode_idx` (`r7`) via `0x63cdde38(this+28, [this+0x11c], r2, 19)`, queries `ImagerDrvParam_IMX284` vtable `[+28]` (`0x6378e538`), then invokes:
    - `0x6378fbe4(this, r7)` — reads `ImagerDrvParam` `[+20]` (`120`-byte table), checks `[r0+27]` (`11`, `12`, `14`-bit / lane mode), compares `r6` against `1440` (`0x5a0`) / `1280` (`0x500`), and stores `[this+0x416]` and `[this+0x417]`.
    - `0x6378ffb4(this, r9)` — **`1H1H` / `1H2H` packet builder**: for mode keys `{11, 55, 47}`, uses option `0` or `1` to select one table byte per packet destination (with optional override at `[r2+125]`). The option number is not itself the byte sent; see the follow-up below.
    - `0x63790004(this, [r5+6])` — checks `[ImagerDrvParam_18B + 0x2a] == 0x1000` (`4096`), setting/clearing bit 1 (`bfi r3, ..., #1, #1`) in the target register packet `[r4+2]`.

---

## 3. Architectural Assessment for Phase 3

1. **In-camera `2×2` Super-Pixel Binning (Photo Mod):**
   - `Stage_Raw.cpp` (`0x638d5540`) and `ExecCamCoreDevelopStart` (`0x636bb64c`, `0x636bf3fa`) bracket the exact point where the 24 MP (`6000 × 4000`) RAW buffer is sized and handed to CamCore for Still development.
   - **Important constraint:** CamCore's Still develop path (`ExecCamCoreDevelopStart`) expects Bayer input unless bypassed/replaced up to `CaptureEncode_Selector` (`0x638bed9e`). A `2×2` Super-Pixel step produces **6 MP (`3000 × 2000`) linear RGB** (no longer Bayer!), so either:
     - **(Option A — Cleanest on-camera):** hook after `Stage_Raw` / before `CaptureEncode_Selector` (or in `Stage_RawPlay` / `RAW -> JPEG` develop) to output a `3000 × 2000` YCbCr/RGB frame directly to JPEG encode, **or**
     - **(Option B — Offline `.ARW` first):** validate the exact `IMX284` black level, `2×2` Super-Pixel math, and `A0`/ARRI color transform on a real a6400 `.ARW` file in `a6400-lut-converter` before writing ARM/Thumb code.
2. **Hardware `IMX284` `1H1H`/`1H2H` & `Mod 10` (`1996 × 1332`):**
   - Whether `Mod 10` (`1996 × 1332`) performs charge addition or line skipping remains open. The follow-up below rules out that mode using this `1H1H`/`1H2H` packet builder.

## 4. 2026-11-20 follow-up: charge summing is not established by this selector

The captured ILCE-6400 2.00 ORIL (`dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`,
base `0x635C6000`) uses Thumb entry `0x6379eccc`. Its mode guard and
write loop match the packaged instructions, but the descriptor pointer was
resolved independently (`0x6454d154` captured, `0x6452e074` packaged).
The loop writes eleven bytes into prepared packets (`strb` at `0x6379ed00`).
For example, the first packet field receives `0x62` or `0x49`, not `0` or `1`.

The bounded Unicorn 2.1.4 probe checked all 19 keys from `0x63e25514` with
both requested options and two fixture override values (`0xFF` and `0x00`):
**76/76** cases returned normally. Only keys `0x0B`, `0x37`, and `0x2F`
(rows 0, 1, and 18, all `6000 × 4000`) wrote packets through this loop.
`0x44` (row 10, `1996 × 1332`) and the observed preview key `0x01`
(row 4, `1996 × 570`) returned without those writes. Other paths may
program packets for those modes.

The separate 32-byte receiver rows copied by `0x6379fe7c` at base
`0x63e26100` are identical for rows 2, 4, and 10 despite different
readout geometry. Reverify 0.11.0 refuted equating row 0 (file offset
`0x860100`) with row 2 (`0x860140`); both rows and the corrected
comparison are retained in the `imx284-charge-sum-20261120` ledger.

Replay from the repository root with
`.\venv\Scripts\python.exe -B artifacts\analysis\imx284-charge-sum-20261120\verify_mode_guard.py`.
The local `mode-guard.json` receipt records fixture state, bounds, and
outcomes; the source hash was unchanged. No devices were mapped. This is
not evidence of an outbound sensor transaction, photodiode summing, or
a low-light gain. Establish on-sensor register semantics or measure
matched RAW read noise before attempting a `3000 × 2000` hardware mode.
