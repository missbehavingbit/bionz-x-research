# E0606 — Offline Step-Response Verification and Systemic `dcv_psf` / `PP_Detail` Contract

- **Date:** 2026-11-18
- **Targets:**
  - `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked/av-cam.bin` (`0x6395bd84..0x6395c23e`, `0x6398e8a8`, `0x6383d1d8`, `0x6384aa98`)
  - `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/sabin/dcv_psf_table_movie_bayer.bin`
  - `artifacts/analysis/arri-sdk-pipeline-e0604/dcv_psf_table_movie_bayer_arri_*.bin`
  - `artifacts/analysis/arri-sdk-pipeline-e0600/e0600_arri_debayer_and_afa_tables.json`
- **Tools:** `tools/research/verify_arri_imitation_offline_e0606.py`, `arm-linux-gnueabi-objdump` (WSL)
- **Preserved artifact:** `artifacts/analysis/arri-sdk-pipeline-e0606/e0606_offline_step_response.json`

---

## 1. Systemic Firmware Compatibility Confirmation

1. **`dcv_psf` Parser (`0x6395bd84..0x6395c23e`):**
   - Disassembly of the parser tail (`0x6395c13a..0x6395c23e`) confirms it computes the 66-byte descriptor (`r5 + 0..64`) from lens/sensor parameters (`0x63948988`, `0x63d41c1c`) and performs **no CRC or cryptographic signature check** on the `int16_t` deconvolution body. Preserving the `0xEC`-byte header (`E0604`) keeps the binary layout compatible with `av-cam.bin`'s parser.
2. **`spica_musashi_mov_4k_*` Loader (`0x6398e8a8`):**
   - Enqueues raw `48,000`-byte (`y_tap`) and `40,000`-byte (`c_tap`) 15-bit two's-complement tables with no embedded signature/CRC in the descriptor.
3. **`PP_Detail*` Hardware Packet Path (`0x6383d1d8`, `0x6384aa98`):**
   - `PP_DetailMode`, `PP_DetailLevel`, `PP_DetailVHBalance`, `PP_DetailBWBalance`, `PP_DetailLimit`, and `PP_DetailCrispening` (`+0x1c6..+0x1cb`) are packed directly into 8-byte `CamCore` command packets (`[r6, #0..7]`).

---

## 2. Offline Step-Response Verification (`0 -> 1` Edge)

| Filter / Profile | Overshoot (%) | Undershoot (%) | Peak |
| --- | ---: | ---: | ---: |
| `ARRI_ALEV4_AFATexture_0.80_smooth` | `3.22%` | `3.22%` | `1.0322` |
| `ARRI_ALEV4_AFATexture_0.80_detail` | `32.38%` | `32.38%` | `1.3238` |
| `ARRI_GENERIC_AfaSmoothLuma_0.62` | `3.98%` | `3.98%` | `1.0398` |
| `Sony_dcv_movie_bayer_stock` | `5.04%` | `21.15%` | `1.0504` |
| `Sony_dcv_movie_bayer_25pct` (`E0604`) | `2.19%` | `9.19%` | `1.0219` |
| `Sony_dcv_movie_bayer_00pct` (`E0604`) | `0.00%` | `0.00%` | `1.0000` |

- **Finding:** Sony's stock `dcv_psf_table_movie_bayer.bin` produces **`21.15%` dark-side undershoot** on a unit step edge due to its negative deconvolution lobes (`-1520`, `-504`), whereas ARRI's `ALEV4_AFATexture_0.80_smooth` and `GENERIC_AfaSmoothLuma_0.62` stay at **`3.22%`–`3.98%`**. Attenuating the `dcv_psf` body to `25%` (`Sony_dcv_movie_bayer_25pct`) reduces undershoot by **2.3×** (to `9.19%`) and overshoot to `2.19%`, while `0%` eliminates deconvolution ringing completely.
