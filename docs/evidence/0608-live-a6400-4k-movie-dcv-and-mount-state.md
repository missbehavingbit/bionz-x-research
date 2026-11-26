# E0608 — Live ILCE-6400 4K Movie Mode Read-Only Check (`dcv_psf` & `/usr` Mount State)

- **Date:** 2026-11-18
- **Target:** Live ILCE-6400 in 4K Movie mode via `tools/camera_debugger.py` (system Python)
- **Preserved artifacts:**
  - `artifacts/analysis/arri-sdk-pipeline-e0608/e0608_page_0435d000.bin` (4 KiB page at `PA 0x0435d000`)
  - `artifacts/analysis/arri-sdk-pipeline-e0608/e0608_page_0435f000.bin` (4 KiB page at `PA 0x0435f000`)
  - `artifacts/analysis/arri-sdk-pipeline-e0608/e0608_page_0435a000.bin` (4 KiB page at `PA 0x0435a000`)
  - `artifacts/analysis/arri-sdk-pipeline-e0608/e0608_page_0435b000.bin` (4 KiB page at `PA 0x0435b000`)
  - `artifacts/analysis/arri-sdk-pipeline-e0608/e0608_page_04518000.bin` (4 KiB page at `PA 0x04518000`)
- **Safety & responsiveness receipts:**
  - `/proc/uptime`: `12913.06` → `12944.25` → `12959.90` → `12979.39` (monotonic, camera responsive)
  - `/tmp_bt`: temporary page dumps `e0608_*.bin` removed after `read`; `/tmp_bt` verified clean.

---

## 1. Observations (4K Movie Mode)

1. **`PA 0x0435dd44..0x0435de10` (`dcv_psf` region):**
   - `dcv_psf_desc` (`PA 0x0435dd44`): `02 00 00 00 01 00 00 00 01 00 00 00 e4 05 00 00 0f 00 00 00`
   - `dcv_psf_selector` (`PA 0x0435ddb8`): `0` (`0x0`)
   - `dcv_psf_buf_ptrs` (`PA 0x0435dde0`): `['0x6435ace0', '0x6435b77c', '0x6435a128', '0x6435b5b4', '0x6', '0x6', '0x1', '0xd301770']`
   - Entries `[4]` and `[5]` changed from `0x3, 0x3` (USB MSC mode, `E0605`) to **`0x6, 0x6`** in 4K Movie mode.
   - `0x6435ace0`, `0x6435b77c`, `0x6435a128`, `0x6435b5b4` contain pointer arrays into `0x6451....` (`0x64518138` holds `av-cam` descriptor/function table entries `0x6376c0f1`, `0x63ce06f5`, `0x6454a898`), not the raw `dcv_psf_table_movie_bayer.bin` deconvolution kernel bytes.
2. **`PA 0x0435fda0..0x0435fea0` (`MovBayer` & `spica` region):**
   - `IsSplit` (`PA 0x0435fdac`) = `0`, `SetterValidBayer` (`PA 0x0435fdad`) = `0`.
   - `spica_4k_ptrs` (`PA 0x0435fe80`) = `['0x6a4007d', '0x1d551056', '0x3bd92c49', '0x1e0']`.
3. **Filesystem / `/usr/sabin` mount state:**
   - `/dev/nflasha15 on /usr type ext2 (ro,relatime,errors=continue)`
   - `/usr/sabin/dcv_psf_table_movie.bin` (`10,728` B)
   - `/usr/sabin/dcv_psf_table_movie_4k.bin` (`10,728` B)
   - `/usr/sabin/dcv_psf_table_movie_bayer.bin` (`18,288` B)
   - `/usr/sabin/dcv_psf_table_still.bin` (`57,848` B)

---

## 2. Conclusion & Next Boundary

`/usr/sabin/dcv_psf_table_movie*.bin` resides on a read-only `ext2` partition (`/dev/nflasha15`). Applying `dcv_psf_table_movie_bayer_arri_00pct.bin` non-destructively without touching internal flash requires either:
1. Locating the exact `av-cam.bin` (`0x6395bd84`) runtime buffer address where `/usr/sabin/dcv_psf_table_movie_bayer.bin` is loaded into RAM, OR
2. Testing whether a temporary RAM-backed `mount --bind` over `/usr/sabin/dcv_psf_table_movie_bayer.bin` is supported and picked up on mode transition (requires explicit user approval before any mount/write action).
