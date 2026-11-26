# E0602 — `av-cam.bin` `ExecCamCoreDevelopStart` and `Stage_EncodeJpeg_MainYC` Mapping

- **Date:** 2026-11-18
- **Target:** `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked/av-cam.bin` (`ORIL_BASE = 0x635C6000`, SHA-256 `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`)
- **Tool:** `arm-linux-gnueabi-objdump` (WSL) Thumb-2 disassembly + Python PIC xref scan
- **Scope & constraints:** Strictly read-only static analysis.

## 1. `ExecCamCoreDevelopStart` Caller (`0x636bb5e0..0x636bb67c`)
- Populates stack parameter block `[sp+0x128..0x164]` from `[r3+0x14..0x24]`, `[r3+0x34..0x44]`, `[r3+0x114..0x118]`, `[r6+0x18]`, and `[r6+0x48e]`.
- Calls `0x636b16ac`, then invokes `0x63c6a31a(r0=r6, r1=sp+0x18, r2=sp+0x168)` (returns `0` on success, logging `[SDF][INT_ERROR] ExecCamCoreDevelopStart Err!` at `0x636bb64c` otherwise).

## 2. `Stage_EncodeJpeg_MainYC` (`0x638c9880..0x638c9980`, xref `0x638c994e`)
- Acquires/prepares the Main YCbCr buffer via `0x638e67a4(r7, r8, ...)`, returning `r7 = yc_id`.
- Builds the JPEG encode request block on stack `[sp+0xd8..0xfa]`:
  - `[sp+0xd8]` (`u16`): `yc_id` (`r7`)
  - `[sp+0xdc]` (`u32`): `[r4 + 0x6ab8]` (Main YC descriptor/pointer)
  - `[sp+0xe0]` (`u16`): `[r4 + 0x66ac]`
  - `[sp+0xe8]` (`u32`): `0x638dfe6c(r4)`
  - `[sp+0xf0]`, `[sp+0xf4]` (4 bytes each): copied from `r4 + 0x6b06`
  - `[sp+0xf8]` (`u8`): `r6` (`2`, `3`, or `0x638e61f4([r4+0x38])`)
  - `[sp+0xf9]` (`u8`): `0x638e6180([r4+0x34])`
- Invokes `0x638c9688(r0=r4, r1=sp+0xd8, r2=sp+0xfc, r3=0)` (`Stage_EncodeJpeg_MainYC` encode) and finalizes via `0x638da390`.

## 3. `Stage_EncodeJpeg_MainYC` Worker (`0x638c9688..0x638c97f8`)
- Acquires an SDF/SA handle at `[sp+0x70]` (`0x63cd3af6` / `0x63d325a0`) and resolves channel via `0x638e6e90(6, [r5+0x20], r5+0x24)`.
- Packs the 35-byte `r4` descriptor (`[sp+0xd8]` from `0x638c9880`) into the SA/SDF JPEG encode message (`[sp+0x0c..0x5c]`):
  - `[r4+0x00]` (`u16`) -> `[sp+0x28]` (`yc_id`)
  - `[r4+0x04]` (`u32`) -> `[sp+0x30]` (`[r4+0x6ab8]`)
  - `[r4+0x14]` (`u32`) -> `[sp+0x44]`, with `[sp+0x40] = [r4+0x14] + [r4+0x10] + 0x1000`
  - `[r4+0x18..0x1e]` (`4 × u16`) -> `[sp+0x4c..0x52]` (dimensions copied from `r4 + 0x6b06`)
  - `[r4+0x20..0x22]` (`3 × u8`) -> `[sp+0x55..0x57]` (format/subsampling/quality flags)
- Dispatches the async JPEG encode job via `0x638c95b4` and stores the output handle from `[sp+0x6c]` to `*r7`.

## 4. `Main YC` Allocator / Mapper (`0x638e67a4`, `0x638e66ec`) & `Stage_Develop` (`0x638c6520`)
- **`0x638e67a4`:** Calls `0x638e7830(fp, 0xe0b00, 1, sp+48)` which yields the allocated `yc_id` (`[sp+52]`, `u16`) and the two plane pointers (`[sp+64]` = `Y` plane, `[sp+68]` = `CbCr` plane).
- **`0x638e66ec`:** Receives `(Y_ptr, CbCr_ptr, ...)` plus the `r4` size/stride struct (`r4+0..r4+12`) and registers the `YC` frame via `0x638e645c`, after which `0x638e67a4` publishes `yc_id` via `0x63d37eee`.
- **`Stage_Develop` (`0x638c6520..0x638c6570`):** Locks/maps `[r4+0x9e]` via `0x63d36734([r0+0x6aac], [r4+0x9e], 0, sp)` into `[r5+0x51e4]` / `[r5+0x51e6]`.

## 5. `0xe0b00` Pool Allocator (`0x638e7830`) & `YC` Frame Registrar (`0x638e645c`)
- **`0x638e7830`:** Manages up to `10` concurrent handles (`cmp r3, #10`) via `0x63d3681a`, recording each `yc_id` (`[r5+4]`) and mode flag (`2` vs `0`) into `[r4 + r3*4]`.
- **`0x638e645c`:** Translates `Y_ptr` (`r0`) and `CbCr_ptr` (`r1`) via `0x635dd850` / `0x63a925a4`, populates 12 `uint16_t` geometry/stride fields (`[sp+0x178..0x18e]`), and dispatches on pixel-format enum `r2 in 0..5` (`tbb` at `0x638e651a`).

## 6. `YC` Format `tbb` (`0x638e651a`) & `ExecCamCoreDevelopStart` Retry Loop (`0x63c6a31a`)
- **`0x638e651a`:** Maps `r2 in {0, 1, 2, 3}` to `{2, 1, 3, 6}` at `[sp+0x192]` (`[sp+0x194..0x198] = {1, 1, 2, 0, 0}`), followed by `0x63d867d0` / `0x63d8708e`, and a second `tbb` (`0x638e6598`) mapping `0..22` to `{6, 7, 1, 4, 5}` at `[sp+0x251]`.
- **`0x63c6a31a`:** Initializes `sp+0x14` via `0x636ced1c` and calls `0x636cf25c(sp+0x14, r7, r5)` (retrying up to `20` times with `10000` us sleep via `0x636b2ed4` while `[r5+4] != 0`).

## 7. `CamCore` Still Develop Command (`0x636cf25c..0x636cf2fc`)
- Calls `0x636cf098` to populate input RAW descriptor (`sp+0x134`), output YC descriptor (`sp+0xa0`), and output geometry (`r5 = sp+0x10`).
- Calls `0x636cf1b4(r9, [r7+0], sp+0x134, sp+0xa0, r5, sp+0x1b8, [r7+0x14c], r6+4)` to execute the CamCore develop pass, then stores output `(width, height)` from `[r5+4]` / `[r5+8]` into `[r6+0]` / `[r6+2]`.

## 8. Next Boundary
- Inspect `0x636cf098` / `0x636cf1b4` to extract the exact input RAW buffer address and output YC buffer address fields inside `sp+0x134` and `sp+0xa0`.
