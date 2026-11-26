# E9104 — Porting `PhotonBin 6MP` to `ILCE-6100` (FW `1.00`) and Hardware Verification

- **Date:** 2026-11-24
- **Scope:** offline Unicorn verification + physical `ILCE-6100` (FW `1.00`) still-capture and SD-boot verification.
- **Code:** [mods/photon_bin_6mp/photon_bin_6mp.c](../../mods/photon_bin_6mp/photon_bin_6mp.c), [mods/photon_bin_6mp/photon_bin_6mp_trampoline.S](../../mods/photon_bin_6mp/photon_bin_6mp_trampoline.S), [mods/photon_bin_6mp/test_mod_v2_unicorn.py](../../mods/photon_bin_6mp/test_mod_v2_unicorn.py), [mods/ilce_6100/PhotonBin6MP.mod](../../mods/ilce_6100/PhotonBin6MP.mod) (`6,612 B`, SHA-256 `f759afcf97c3ce381420e5c82a451979a54c971aeed81269558f402add7e2db4`).

## Target Coordinates (`ILCE-6100` FW `1.00`)

The `av-cam.bin` LIRO `.text` Option 3 sites and `libObj.so` `ImageBuilder::MakeRawInfoArea` Option 1 hook were ported and verified against `ILCE-6100` `1.00`, building `mods/ilce_6100/PhotonBin6MP.mod` cleanly and passing all tests in `mods/photon_bin_6mp/test_mod_v2_unicorn.py`.

## Fixes Discovered During Port & Hardware Bring-Up

1. **`rawAdditionalInfo` IFD-relative out-of-line offsets:** `update_additional_ifd` previously wrote out-of-line payloads for tags `0xC61F` (`DefaultCropOrigin`), `0xC620` (`DefaultCropSize`), and `0xC68D` at `base + val` instead of `base + ifd_off + val`, overwriting the `DevInfo` IFD and crashing on hardware. Fixed with bounds-checked `base + ifd_off + val` writes and byte-wise `volatile uint8_t *` stores in `wr_u16_le` / `wr_u32_le` so `-Os -mthumb` cannot coalesce unaligned `strh`/`str` into uncached DMM memory.
2. **4 KiB DMM extent alignment:** Trimmed physical DMM extent sizes (`ext.size`) are rounded up to 4 KiB (`0x1000`) and capped at the original extent size, with the kept extent count saved in `last_ext_cnt`.
3. **Call 2 stack `RawInfoArea` re-application:** On the second call to `MakeRawInfoArea`, `src_data` is already `3008x2008`, but the caller rebuilds a fresh stack `RawInfoArea` with stock sizes. Call 2 now re-applies `last_out_bytes` (`+0x114`), `last_ext_cnt` (`+0x11c`), and `add_buf` (`+0x120`) when `add_valid` is set.

## Physical `ILCE-6100` Hardware Verification

- **6 MP 16-bit Uncompressed `.ARW` (`_DSC5765.ARW`):** `12,588,032 B` (vs `24 MB` stock), `3008x2008`, `bps=16`, `comp=1`, `WhiteLevel=63996`, `DefaultCropOrigin=(4, 4)`, `DefaultCropSize=(3000, 2000)`, `StripOffsets + StripByteCounts == file_len`.
- **`FD Conv Gain: Force HCG` (`_DSC5769.ARW` vs `_DSC5770.ARW`):** Both shot at `1/60s, f/2.8` in `24 MP` `14-bit cRAW`: `_DSC5769.ARW` at **ISO 100** (`11-bit` center patch `min=314.2, max=339.1, span=24.9`) matched `_DSC5770.ARW` at **ISO 320** (`min=313.9, max=338.6, span=24.7`), consistent with `+9.805 dB` (`3.092x`) HCG analog amplification.
- **SD-Boot & `SMNU` Submenu:** `CTX_VERSION=3` (`9,140 B` `core.bin`) renders `PhotonBin6MP.mod` only as a plain toggle and refuses hot-loading `v4` (`core_refuse 00000003`). `CTX_VERSION=4` (`10,572 B` `core.bin` from `artifacts/tests/mlv2-a6100-build/core.bin`) is required for `SMNU`. Updating `MLV2.TAR` on the SD card also requires updating the pinned SHA-256 (`set -- <sha256>`) in `/setting/mlv2_sd_boot.sh`.
