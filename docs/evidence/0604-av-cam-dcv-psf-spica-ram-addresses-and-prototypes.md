# E0604 — `av-cam.bin` `dcv_psf` / `spica` RAM State Map and Attenuated Prototypes

- **Date:** 2026-11-18
- **Target:** `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked/av-cam.bin` (SHA-256 `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`, `ORIL_BASE = 0x635C6000`)
- **Tools:** `arm-linux-gnueabi-objdump` (WSL), `tools/research/build_dcv_psf_arri_prototype.py`
- **Preserved artifacts:** `artifacts/analysis/arri-sdk-pipeline-e0604/` (`manifest.json` + 4 prototype `.bin` files)
- **Scope & constraints:** Strictly read-only static analysis and offline prototype generation.

---

## 1. Exact RAM State Addresses (`GOT[0x3eb0]` → `0x6435cc38`)

Disassembly of `0x6395c978..0x6395ca8a` (`dcv_psf_table` loader), `0x6398e8a8..0x6398e994` (`spica_musashi_mov_4k` loader), and `0x6396aaee..0x6396ab06` (`calcMovBayerSetParam`) all reference the same global pointer at `GOT[0x3eb0]` (`0x645a21e4 + 0x3eb0 = 0x645a6094`), which resolves to **`0x6435cc38`** (**PA `0x0435cc38`**):

| Field | Offset from `0x6435cc38` | VA | PA (`VA - 0x60000000`) | Meaning |
| --- | --- | --- | --- | --- |
| `dcv_psf_desc` | `+0x110c` | `0x6435dd44` | `0x0435dd44` | 66-byte active `dcv_psf` descriptor (copied when `[r6, #0x1f]` is `8` or `22`) |
| `dcv_psf_selector` | `+0x1180` | `0x6435ddb8` | `0x0435ddb8` | Active selector byte (`1` = `movie.bin`, `3` = `macro_movie.bin`, `4` = `movie_bayer.bin`) |
| `dcv_psf_buf_ptrs` | `+0x11a8` | `0x6435dde0` | `0x0435dde0` | Loaded `dcv_psf` buffer pointers / descriptors (`r5 + 8..36` from `0x63d41b74`) |
| `IsSplit` | `+0x3174` | `0x6435fdac` | `0x0435fdac` | `calcMovBayerSetParam` split flag |
| `SetterValidBayer` | `+0x3175` | `0x6435fdad` | `0x0435fdad` | `calcMovBayerSetParam` Bayer setter validity flag |
| `spica_4k_sizes` | `+0x3258` | `0x6435fe90` | `0x0435fe90` | Four `uint16_t` sizes (`48000, 40000, 48000, 40000`) for `spica_musashi_mov_4k_y/c_tap_step1/2.bin` |

- `0x6395c9b8` invokes `0x63d41b74` to load the selected `/usr/sabin/dcv_psf_table_*.bin` file, and `0x6395c9e8` invokes `0x6395bd84` to parse it before copying the 66-byte descriptor and buffer pointers into `0x6435dd44` / `0x6435dde0`.
- `0x6398e8a8` populates a 4-slot file-load descriptor (`4 × (path[96], size)`) for `/usr/sabin/spica_musashi_mov_4k_y_tap_step1/2.bin` (`48,000` B) and `c_tap_step1/2.bin` (`40,000` B) when `[r0, #4] == 4` (4K mode).

---

## 2. Offline Attenuated `dcv_psf_table` Prototypes

`tools/research/build_dcv_psf_arri_prototype.py` generated 4 offline prototypes in `artifacts/analysis/arri-sdk-pipeline-e0604/` preserving the `0xEC`-byte header and scaling the `int16_t` deconvolution delta body by `0.25×` and `0.00×`:
- `dcv_psf_table_movie_bayer_arri_25pct.bin` & `dcv_psf_table_movie_bayer_arri_00pct_flat.bin`
- `dcv_psf_table_movie_4k_arri_25pct.bin` & `dcv_psf_table_movie_4k_arri_00pct_flat.bin`

---

## 3. Next Boundary
Perform a read-only live camera observation (`tools/camera_debugger.py`) in 4K Movie mode at `PA 0x0435ddb8` (`dcv_psf_selector`), `PA 0x0435dde0` (`dcv_psf_buf_ptrs`), and `PA 0x0435fe90` (`spica_4k_sizes`) to confirm the live runtime buffer addresses.
