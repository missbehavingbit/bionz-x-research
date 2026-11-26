# E0624 — Unicorn verification of RCRAW line/block calculation on ILCE-6400

Date: 2026-11-19. Offline firmware analysis and Unicorn verification only. No camera, SD, or USB access.

## Inputs

- `av-cam.bin` (`16,674,456` B, SHA-256 `90d515cfec8b936f…`, `ORIL v4`, linked at `0x635C6000`)

## Summary

Executed `0x63CD278C` and `0x63CD27CA..0x63CD281C` (`RCRAW` line/block bound helpers called from `0x63767890` in [E0621](0621-a6400-10bit-and-raw-video-paths.md)) directly in Unicorn (`UC_ARCH_ARM | UC_MODE_THUMB`):

1. **`0x63CD278C(flag, out_block, out_step)`:**
   - `flag=1` writes `(0x400, 0x20)` (`1024`, `32`).
   - `flag=0` writes `(0x10, 0x10)` (`16`, `16`).
2. **`0x63CD27CA(ctx)`:**
   - Reads height `h` at `+0x02`, `shift_mode` at `+0x08`, `unit` at `+0x14`, and `step` at `+0x1E`, writing computed `RCRAW` bounds to `+0x1A` and `+0x1C`:
     - `h=1332` (Mod 10 `1996×1332` 3:2), `shift_mode=0`, `unit=1024`, `step=16` → `+0x1A=21310`, `+0x1C=21311`.
     - `h=4000` (Mod 2 `6000×4000` 3:2), `shift_mode=1`, `unit=1024`, `step=16` → `+0x1A=62460`, `+0x1C=62461`.

## Verification

- `$env:PYTHONPATH = "venv\Lib\site-packages"; python -m unittest tests/test_sensor_tables.py -v`: **11/11 PASS** (`0.520 s`), including `test_real_a6400_rcraw_calc_unicorn`.
- `check_markdown_links`: **PASS** (`601` relative links across `52` docs before adding `E0624`, re-checked below).
