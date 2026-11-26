# E0605 — Live ILCE-6400 MSC Read-Only Check of `dcv_psf` and `spica` State

- **Date:** 2026-11-18
- **Target:** Live ILCE-6400 in USB MSC mode via `tools/camera_debugger.py` (system Python)
- **Preserved artifacts:**
  - `artifacts/analysis/arri-sdk-pipeline-e0605/e0605_got.bin` (`16` B at `PA 0x045a6094`)
  - `artifacts/analysis/arri-sdk-pipeline-e0605/e0605_dcv.bin` (`204` B at `PA 0x0435dd44..0x0435de10`)
  - `artifacts/analysis/arri-sdk-pipeline-e0605/e0605_spica.bin` (`256` B at `PA 0x0435fda0..0x0435fea0`)
- **Safety & responsiveness receipts:**
  - `/proc/uptime`: `9577.07` → `9584.80` → `9591.44` → `9639.59` → `9661.10` (monotonic, camera responsive)
  - `/tmp_bt`: temporary slices `e0605_*.bin` removed after `read`; `/tmp_bt` verified clean.

---

## 1. Observations (MSC Mode)

1. **`PA 0x0435dd44..0x0435de00` (`dcv_psf` region):**
   - `dcv_psf_desc` (`PA 0x0435dd44`): `02 00 00 00 01 00 00 00 01 00 00 00 e4 05 00 00 0f 00 00 00 ...` (`0x5e4 = 1508`).
   - `dcv_psf_selector` (`PA 0x0435ddb8`): `0` (inactive in MSC).
   - `dcv_psf_buf_ptrs` (`PA 0x0435dde0`): `[0x6435ace0, 0x6435b77c, 0x6435a128, 0x6435b5b4, 0x3, 0x3, 0x1, 0x0d301770]`.
2. **`PA 0x0435fda0..0x0435fea0` (`MovBayer` & `spica` region):**
   - `IsSplit` (`PA 0x0435fdac`) = `0`, `SetterValidBayer` (`PA 0x0435fdad`) = `0`.
   - `spica_4k_sizes` (`PA 0x0435fe90`) = `(65129, 64973, 65213, 521)` (not yet populated with `48000, 40000, 48000, 40000` in MSC mode).

---

## 2. Conclusion & Next Boundary

In USB MSC mode, the 4K Movie `dcv_psf` selector (`0x0435ddb8`), `calcMovBayerSetParam` flags (`0x0435fdac/ad`), and `spica_4k_sizes` (`0x0435fe90`) are in their idle/non-Movie state. Capturing their active values requires observing these same physical addresses while 4K Movie mode is active (or proceeding directly to a paired 4K S-Log3 chart capture with `PP_Detail` tuning + `Arrivederci`).
