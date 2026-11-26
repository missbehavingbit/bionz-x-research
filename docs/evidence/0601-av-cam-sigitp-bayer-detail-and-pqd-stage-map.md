# E0601 — `av-cam.bin` `SigItpBayer`, `MovItp`, `PP_Detail*`, and `CamCore` Stage Map

- **Date:** 2026-11-18
- **Target:** `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked/av-cam.bin`
  - **SHA-256:** `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`
  - **Format / Base:** `ORIL` v4 raw ARM/Thumb-2 PIC, `ORIL_BASE = 0x635C6000`
- **Tools:**
  - `arm-linux-gnueabi-objdump -D -b binary -m arm -M force-thumb --adjust-vma=0x635C6000` (WSL)
  - Python Thumb-2 PIC literal-pool and instruction-offset scanner
- **Scope & constraints:** Strictly read-only static analysis. No physical camera access and no firmware modifications.

---

## 1. Movie Bayer & Signal Interpolation (`MovItp` / `SigItpBayerSetter`)

1. **`SigItpBayerSetter` (`0x63eeba81`, vtable `0x644fa368`) & `SigItpArSetter` (`0x63eeba5f`, vtable `0x644fa358`):**
   - Multiple constructor variants at `0x63ccef56..0x63ccefc0` (`SigItpBayerSetter`) and `0x6375d7d8..0x6375d858` install their respective vtables via `SetterBase` (`0x6375cda4`) and finalize via `0x63ab27b0` (`0x635dd35c`).
   - Simple accessors at `0x63ccefc2..0x63ccefd4` expose the validity flag at `this + 8` (`ldrb/strb [r0, #8]`) and payload pointers/words at `this + 12` (`+0x0c`) and `this + 16` (`+0x10`).
2. **`YC: calcMovBayerSetParam` (`0x6396a900..0x6396ab2c`, string `0x63f23ffd`):**
   - Copies Movie Bayer tuning fields (`uint8_t` / `uint16_t`) from source struct `[r3 + 0xd43..0xd8b]` into destination `[r4 + 0xc4..0x11e]`.
   - Computes `SetterValidBayer` (`+0x3175` on the GOT-referenced global) and `IsSplit` (`+0x3174`) via `0x63d40fb4` and `0x63d422d6` when `[r6 + 28] & 4` or `[r6 + 31] == 11`.
3. **`YC: job_YC_MovItp_CalcParam_Sync` (`0x6396c980..0x6396cbe0`, string `0x63f2409e`):**
   - Calls `0x6395aab8` to obtain the hardware-parameter staging object `r0`, and populates the Movie Interpolation (`MovItp`) block at **`r0 + 0x2016c..0x20180+`** (halfwords and bytes copied from `[r5 + 0x18..0x48]`).

---

## 2. `PP_Detail*` Validation and Consumers

1. **Validation (`0x63bd8190..0x63bd82f8`, strings `0x63f7f52f..0x63f7f6ba`):**
   - Validates `PP_DetailMode`, `PP_DetailLevel`, `PP_DetailVHBalance`, `PP_DetailBWBalance`, `PP_DetailLimit`, `PP_DetailCrispening`, and `PP_DetailHighlightDetailSet` between the Picture Profile request struct (`r4 + 0x20..0x25` / `+0x2c..0x34`) and live state (`r5 + 0x1c6..0x1cb` / `+0x1dc..0x1e3`).
2. **Register-Packing Consumers (`0x6383d1b0..0x6383d270` and `0x6384aa98..0x6384ab18`):**
   - `0x6383d1d8..0x6383d234` reads `[r0 + 0x1c6..0x1cd]` (including all `PP_Detail*` bytes) and packs them into 8-byte hardware command packets (`[r6, #0..7]`) via `0x6383bdb8`.
   - `0x6384aa98..0x6384aad0` reads `[r3 + 0x1bb, 0x1c0, 0x1c1, 0x1c8, 0x1c9, 0x1ca, 0x1cb, 0x1cc]` from the GOT-referenced state block and packs them into `[r0, #0..7]` in reverse byte order.

---

## 3. `CamCore` Hardware Transport & MMIO Registers (`0x63cd6e00..0x63cd71dc`)

- Disassembly of `0x63cd6e00..0x63cd71dc` and `0x637802d0..0x637803c0` confirms:
  - `0x63cd6e22` validates 8-byte alignment (`lsrs r6, r1, #3`) via `0x63780344` / `0x637802d0` and dispatches through `0x63cd6c34` / `0x63cd6c54`.
  - `0x63780388` indexes a 16-byte-stride descriptor table at `0x637803d0` and invokes `0x6377daac`.
  - `0x63cd7180..0x63cd71c8` programs bitfields directly in CamCore MMIO **`0xFC000000 + 0x29000 + 4*ch`** (`+0` and `+0x20`), confirming the live hardware register window base `0xFC000000`.
- Disassembly of `0x63bfa754..0x63bfa91c` shows the grouped PQD coordinator reads stage enable flags `[r6 + 0..7]` and `[r6 + 0x60..0x61]`, conditionally invokes `0x63bf3fd4`, `0x63dc9304`, `0x63bfa998`, `0x63dc816a`, `0x63dc80a4`, and `0x63dc8862`.

---

## 4. Next Boundary (`E0602`)
Decode the `/usr/sabin/` `dcv_psf_table_movie*.bin` and `spica_musashi_mov_4k_*.bin` binary tables and compare their spatial MTF and overshoot directly against the ARRI `E0600` filters.
