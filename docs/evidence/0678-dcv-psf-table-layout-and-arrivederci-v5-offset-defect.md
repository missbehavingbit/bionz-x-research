# E0678 — `dcv_psf_table` layout decoded; Arrivederci v4/v5 DCV tables built at the wrong offsets

- **Date:** 2026-11-22
- **Inputs (read-only):**
  - Stock ILCE-6400 `/usr/sabin/` tables from `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/sabin/` (sizes as in [E0602](0602-sabin-dcv-psf-and-spica-tap-mtf-vs-arri.md)):
    - `dcv_psf_table_movie_bayer.bin` 18,288 B, SHA-256 `8fdc685767baecbf42049fbf990fbe0b65773ea05f47ece1a9f936b4c04d7cb4`
    - `dcv_psf_table_movie_4k.bin` 10,728 B, SHA-256 `4e0d8847b9f721d281c9d92080b1c49662eb85867b53f38d6a7d5e9a69d470a0`
    - `dcv_psf_table_movie.bin` 10,728 B, SHA-256 `1e7f6bb2842bc828143473ae4f9e39e3ceaa407bb1e003215d237014b82d2004`
  - Arrivederci v5 build `artifacts/tests/arrivederci-v5-build-1/` (`Arrivederci.mod` SHA-256 `46c41981e7b37231a6cadb0d033f514c5b05a17ecb38c85f68d909d64a0b64b7`, equal to the released v5 asset and to `mods/arri_isp/arrivederci/Arrivederci.mod`; `arrivederci_data.h` arrays `SABIN_DCV_BAYER`, `SABIN_DCV_4K`).
  - Builder `tools/research/build_arri_v4_sabin.py` (`build_symmetric_dcv`, `header_size=0xEC`, 48-value blocks, `target_gain=0.075`).
  - Clip `E:\PRIVATE\M4ROOT\CLIP\C0029.MP4` (Arrivederci v4 ON per [E0610](0610-live-a6400-arrivederci-v4-zebra-ab-test.md), `AVC_3840_2160_HP@L51`, S-Log3 / S-Gamut3.Cine), frame at 1 s extracted with `ffmpeg -ss 1 -frames:v 1 -pix_fmt rgb24`. C0030 (v4 OFF) the same way.
- **Tools:** `isp-studio` `bionz_core::spatial::DcvTable` (parser, `health`, `zone_centre_range`, `rescale_table`), `bionz-cli dcv`, `bionz-cli render`; host Python scripts for the first dumps. Host-only; no camera access.

## 1. Table layout (stock files)

- Bytes `0x00..0x30`: ASCII tags (`IMX094` / `IMX210`, `SAL50_CenGch_1` / `_3`, a date).
- u16 `0x30`: total file size. u16 pairs from `0x40`: zone grid `(9, 7)`, then kernel shapes (`movie_bayer`: `(9,9) (5,5) (5,9) (5,5)`; `movie_4k`/`movie`: `(9,9) …`). u16 `0x54..`: group strides (`3024` ×3 for `movie_bayer`, `5292` for `movie_4k`). u32 from `0x78`: group byte offsets `144, 6192, 12240` (`movie_bayer`).
- **Kernel data starts at byte 144 (`0x90`)**, not `0xEC`. Sizes close exactly: `144 + 63·84·2 = 10,728` and `144 + 3·63·48·2 = 18,288`.
- `movie_4k` / `movie`: 63 zones (9 columns × 7 rows) × 84 halfwords = a row-major 9×9 kernel + 3 zero pad. Stock: all 63 zones identical, mirror-symmetric, sums `+36` (`movie_4k`, centre `7684`) and `+104` (`movie`, centre `5624`).
- `movie_bayer`: 3 groups × 63 zones × 48 halfwords.
  - Group 0: rows of 10 = a 5×5 same-phase kernel row (5) + a 4×4 kernel row on the other green phase (4) + 1 zero; row 4 holds only the 5×5 row; 3 zero pad. Stock centre `7812`, cross `-504`, 4×4 inner `-1520`; 5×5 sum `5908`, 4×4 sum `-5872`, total `+36`.
  - Groups 1 and 2: a row-major 5×5 kernel then zeros (centre `5572`, sum `+4`).
  - Read as: group 0 = green (quincunx partner on the 4×4 half-offset grid), groups 1/2 = red/blue. All stock zones identical and symmetric.
- **Inference:** near-zero sums make these additive high-pass ("deconvolution") kernels, `out = in + conv(in, K)/2^q`. The fixed-point scale `q`, the zone interpolation and the exact stage position are not established.

## 2. Arrivederci v4/v5 tables

The builder starts at `0xEC` and treats the body as 48-value blocks. The first 46 halfwords of zone data (`0x90..0xEC`) are therefore never scaled, and in `movie_4k` the 48-blocks cut across the 84-value zone records.

| Table | Asymmetric kernels | Kernels with \|sum\| > 256 | Zone centre taps |
| --- | ---: | ---: | --- |
| stock `movie_bayer` | 0 | 0 | 7812..7812 |
| v5 `movie_bayer` | 0 | 0 | **580..7812** (green zone (0,0) left at stock strength) |
| stock `movie_4k` | 0 | 0 | 7684..7684 |
| v5 `movie_4k` | **47** | **1** (zone (0,0) sum `+1965`) | **428..7684** |
| rebuilt (`rescale_table`, gain 0.075) | 0 | 0 | 583..583 / 576..576 |

Zone (0,0) is the top-left of the 9×7 grid.

## 3. Footage check of the DC term

- Model prediction (additive, Q14, DC kept): v4/v5 `movie_4k` zone (0,0) adds `1965/16384 ≈ +12 %` linear, about +3 8-bit codes in S-Log3 at the zone-(0,0) boundaries (x = 426, y = 308 at 3840×2160).
- C0029 (v4 ON): mean step across x = 426 inside zone row 0 = **+0.04** codes (row 1: −0.01); across y = 308 inside zone column 0 = **−0.05** (column 1: +0.32). C0030 (v4 OFF) shows scene steps of the same sign at both rows/columns, so the test is not blind to edges.
- **Inference:** the hardware does not appear to pass the kernel DC at this scale. Possible readings: DC is removed in hardware, `q` is much larger, or the bind-mounted `movie_4k` table is not used in this mode. A pure-`q` reading would need about Q20, which would make the stock sharpening negligible and conflict with the halo-free edges reported in [E0609](0609-av-cam-4k-dcv-dynamic-reload-proof.md). One frame, one clip; not a measurement of the sharpening itself.

## 4. Consequences

- The v4/v5 attenuation applies to zones other than (0,0). Zone (0,0) keeps Sony's full green Bayer kernel, and in 4K the top half of its stock 9×9 kernel. The other 4K zones carry small asymmetric kernels.
- `rescale_table` rebuilds the intended tables (header and layout preserved, each kernel's rounding folded into its centre so sums scale exactly). The Look Lab "Repair preset DCV tables" action and `bionz-cli dcv --gain` produce them on the host. No camera build was made or run.
- The Look Lab models the DC term as removed by default (`dc_free`), labelled as an assumption.

## 5. Next boundary

- Measure sharpening per zone in matched footage: stock vs v5 vs rebuilt tables, same scene, 4K S-Log3. Expect zone (0,0) to stay crisper under v5 if the table is used.
- Locate the consumer of the parsed `dcv_psf` buffers (`0x6435dde0`, E0604) to settle `q`, DC handling and zone interpolation.
- The `dcv_psf_table_still.bin` layout (grid `(5,4)`, strides `1600`) is not decoded.
