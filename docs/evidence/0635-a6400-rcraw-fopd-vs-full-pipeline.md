# E0635 — RCRAW_SET (0x63B2D560) and 0x63767ACC mapped to 160..640 FOPD sub-path; hardware G1/G2 readback

Date: 2026-11-19. Offline `av-cam.bin` analysis + read-only physical `ILCE-6400` MSC observation.

## Summary

Traced all callers of `RCRAW_SET` (`0x63B39D2C`, writing `PA 0x043D1810`) and `0x63767ACC`, and read the remaining `RCRAW` state words from the physical `ILCE-6400` via `ro_rcraw_snap` (`2052` B, removed after execution):

- **Static `av-cam.bin` (`0x635C6000`) findings:**
  - `0x63B2D560` is the **only** caller of `0x63B39D2C` that writes `RCRAW_EN = 1` (`0x63B2D5C2`); all other callers (`0x63B383F2`, `0x63B38928`, `0x63B38F8C`, `0x63B390EC`) write `0`.
  - When `RCRAW_EN = 1`, `0x63B2D560` also calls `0x63768874` to populate `G1` (`VA 0x641E2DC0` / `PA 0x041E2DC0`) and `G2` (`VA 0x641E2E14..0x641E2E18` / `PA 0x041E2E14..0x041E2E18`), plus `0x63B264F4(r4 + 0x14)`.
  - `0x63767ACC` indexes a 3×3 dimension cap table at `0x63E467B2` (`(m0=0..2, m1=0..2)` -> `160×120/106/90`, `320×240/212/180`, `640×480/424/360`), proving that the `0x63B3CAC4` (`PA 0x043D1810`) -> `0x63767ACC` branch is the **small FOPD/AF/AE `RCRAW` path** (`<= 640×480`), not the full-sensor >8-bit video RAW path.
  - Meanwhile, the other two `RCRAW CALC` (`0x63767774`) callers (`0x63B3BD70` and `0x63B3BF66`) feed `0x63B398C4` and the `0x63B340F8` compressor quant/threshold selector (`E0629`).
- **Physical `ILCE-6400` MSC readback (`ro_rcraw_snap`):**
  - `ERR_FLG(0445E2D8) = 0x00000000`, `G1_BUF(041E2DC0) = 0x00000003`, `G2_M0(041E2E14) = 0x00000001`, `G2_M1(041E2E18) = 0xbbbbbb05` (uninitialized `0xbb` poison, confirming `0x63768874` had never run), `/proc/uptime` `731.72 2531.16`.

## Verification

- `ro_rcraw_snap` via `tools/camera_debugger.py`: **PASS** (`2052` B, `readback_verified: true`, `/proc/uptime` `731.72`, `/tmp_bt/ro_rcraw_snap` removed).
- `check_markdown_links`: **PASS**.
