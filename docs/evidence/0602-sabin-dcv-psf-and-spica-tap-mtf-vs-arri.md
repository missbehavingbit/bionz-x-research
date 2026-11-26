# E0602 — Sony `/usr/sabin/` `dcv_psf_table` & `spica` Tap Analysis vs. ARRI `E0600`

- **Date:** 2026-11-18
- **Targets:**
  - `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked/av-cam.bin` (SHA-256 `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`)
  - `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/sabin/`:
    - `dcv_psf_table_movie_bayer.bin` (`18,288` B)
    - `dcv_psf_table_movie_4k.bin` (`10,728` B)
    - `dcv_psf_table_movie.bin` (`10,728` B)
    - `spica_musashi_mov_4k_y_tap_step1.bin` (`48,000` B), `spica_musashi_mov_4k_y_tap_step2.bin` (`48,000` B)
    - `spica_musashi_mov_4k_c_tap_step1.bin` (`40,000` B), `spica_musashi_mov_4k_c_tap_step2.bin` (`40,000` B)
- **Tools:** `tools/research/analyze_sabin_vs_arri_e0602.py`, `arm-linux-gnueabi-objdump` (WSL)
- **Preserved artifact:** `artifacts/analysis/arri-sdk-pipeline-e0602/e0602_sabin_mtf_analysis.json`

---

## 1. Runtime Selection of `dcv_psf_table_*.bin` (`0x6395c842..0x6395c90e`)

Resolving all PC-relative string literals in the selector at `0x6395c842..0x6395c90e` yields the exact file selection:
- **`/usr/sabin/dcv_psf_table_movie_bayer.bin`** (`0x63f2bb3f`, sets selector byte `[r3 + 0x1180] = 4` when `[r8 + 0xbec] & 4` and `r1 == 0` and `[r6 + 0x1f] == 8`, or at fallback `0x6395c8f8`).
- **`/usr/sabin/dcv_psf_table_macro_movie.bin`** (`0x63f2bb9f`, sets `[r3 + 0x1180] = 3` when `[r8 + 0xbec] & 0x10`).
- **`/usr/sabin/dcv_psf_table_movie_4k.bin`** (`0x63f2bcbf`, selected at `0x6395c8ec`).
- **`/usr/sabin/dcv_psf_table_movie.bin`** (`0x63f2bbff`, sets `[r3 + 0x1180] = 1` or default).

---

## 2. Binary Structure of `dcv_psf_table` and `spica_musashi_mov_4k`

1. **`dcv_psf_table_movie_bayer.bin` (`18,288` B) & `dcv_psf_table_movie_4k.bin` (`10,728` B):**
   - Header `0x30..0x60` stores total size (`0x4770` / `0x29e8`), kernel-bank dimensions `(9×7, 9×9, 5×5, 5×9, 5×5)`, and bank stride (`0x0bd0 = 3,024` B in `movie_bayer.bin`, `0x14ac = 5,292` B in `movie_4k.bin`).
   - Inspection of the first `48`-halfword (`96`-byte) deconvolution block (`0x0ec..0x14c` in `movie_bayer.bin`) shows a **near-zero-sum (`+36`) symmetric high-pass / deconvolution delta kernel** centered at **`+7812`** with strong negative lobes **`-1520`** and **`-504`**.
   - Architectural implication: Sony's DCV block applies an additive deconvolution boost (`output = input + conv(input, K_dcv)`), which directly produces high-frequency micro-contrast boost and edge *ringing/halo* compared to ARRI's smooth `table_a` (`-0.045` max negative tap).

2. **`spica_musashi_mov_4k_y_tap_step1/2.bin` (`48,000` B) & `c_tap_step1/2.bin` (`40,000` B):**
   - Values are stored as **15-bit two's complement** (`0..32767`, where `v >= 16384` represents `v - 32768`):
     - `y_tap_step1`: `[-4557, +2747]`, `y_tap_step2`: `[-1973, +1285]` (delta/residual polyphase taps for 6K→4K luma scaling).
     - `c_tap_step1`: `[-4559, +8533]` with **10-tap / 20-halfword periodic rows** (`40,000` B = `20,000` `int16_t`); `c_tap_step2`: `[-5449, +4744]`.

---

## 3. Direct Comparison with ARRI `E0600`

| Aspect | Sony a6400 (`/usr/sabin/` + `PP_Detail`) | ARRI SDK (`ADA-7` + `ALEV4_AFATexture_09T` + `AfaSmoothChroma_09T`) | Emulation Direction for a6400 |
| --- | --- | --- | --- |
| **Edge / Deconvolution** | `dcv_psf` zero-sum delta with `+7812` center and `-1520` lobes | `table_a` Smooth (`-0.045` min tap) + contrast-gated `table_b` Texture (`-0.397` at `±2`, Nyquist `0.0854`) | Attenuate `dcv_psf` negative lobes / `PP_DetailLevel` & `BWBalance` to remove digital halo |
| **Shadow Noise / Overshoot** | `PP_DetailCrispening` + `PP_DetailLimit` | `G_avg < 1024` Laplacian damping + `4×` chroma-edge penalty below `2048` | Raise `PP_DetailCrispening` (higher coring threshold in shadows) |
| **Chroma Anti-Aliasing** | `spica_4k_c_tap` (`[-5449, +8533]`) | `GENERIC_AfaSmoothChroma_09T_0.0.7` (`MTF50 = 0.0696 cpp` at `0.56`, Gaussian-like roll-off) + `ada7Hw` `[1,2,1]/4` LP | Smooth chroma transitions and suppress chroma-edge peaking |
