# E0609 — Static Proof of Dynamic 4K `dcv_psf` Reload and `0x6435ddb8` Behavior

- **Date:** 2026-11-18
- **Target:** `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked/av-cam.bin` (`ORIL_BASE = 0x635C6000`)
- **Tools:** `arm-linux-gnueabi-objdump` (WSL), Python `struct`

---

## 1. Findings

1. **Single dynamic caller (`0x6397149c` → `0x6395c414`):**
   - `0x6395c414` (the `dcv_psf` loader/parser wrapper) has only one caller in `av-cam.bin`: **`0x6397149c`**, bracketed by profiler stage marker `65` (`0x63d41af0`) inside the Movie ISP parameter calculation path (`job_YC_MovItp_CalcParam_Sync` sequence). Thus `/usr/sabin/dcv_psf_table_movie_*.bin` is loaded dynamically during Movie parameter calculation, not only at boot.
2. **Why `PA 0x0435ddb8` (`dcv_psf_selector`) remained `0` in 4K Movie (`E0608`):**
   - In `0x6395c880..0x6395c90e`, `dcv_psf_selector` (`r8 + 0x1180 = 0x6435ddb8`) is only written on the `[r6, #31] == 8` branches (`0x6395c886`, `0x6395c8b6`, `0x6395c8e4` for `movie.bin` / `macro_movie.bin`).
   - The `4K` and `movie_bayer` branches **`0x6395c8ee`** (`/usr/sabin/dcv_psf_table_movie_4k.bin`) and **`0x6395c8fa`** (`/usr/sabin/dcv_psf_table_movie_bayer.bin`) jump directly to `0x6395c97c` → `0x6395c9b8` (`bl 0x63d41b74` file loader) → `0x6395c9e8` (`bl 0x6395bd84` parser) **without writing `0x6435ddb8`**.
3. **Implication for `E0608` (`C0028.MP4`):**
   - Because both `/usr/sabin/dcv_psf_table_movie_4k.bin` and `/usr/sabin/dcv_psf_table_movie_bayer.bin` are bind-mounted to `tmpfs` (`0% DCV flat` tables) on the live camera, `0x6395c414` reads the bind-mounted RAM tables when Movie parameter calculation runs, explaining both `PA 0x0435ddb8 == 0` and the clean halo-free PCB edges observed in `C0028_crop_pcb.png`.
