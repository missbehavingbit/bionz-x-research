# E0631 — RCRAW_Enable_Test ON/OFF/reload cycle and AIF0 W1/W2 baseline in QEMU

Date: 2026-11-19. Offline QEMU (`sony-a6400`) and Unicorn only. No camera, SD, or USB access.

## Summary

Extended `RCRAW_Enable_Test.mod` ([E0630](0630-rcraw-enable-test-smod-v2.md)) to also log `AIF0 W1` (`PA 0xF3002400`) and `PA 0x043D1534` (`[0x643D152C + 8..11]`, containing the `[r3+9]` gate byte at `0x643D1535` checked by `0x63B3E31C` when `RCRAW_EN != 0`), rebuilt (`1112` code bytes, `1180` B total, SHA-256 `603c1fdf24ed8775…`; `--auto-on` `7b64d9f5a38efe64…`), and ran a full `ON -> close MENU -> MOVIE -> reload (OFF -> ON)` cycle in QEMU (`a6400-qemu-moddev`, `inputs-moddev-1`, `mlv2-layout-build-4`, `EVSEL_LATE_US=500`):

- **Lifecycle & guarded RAM swap verified in QEMU guest (`/tmp_bt/mods/rcraw_enable_test.log`):**
  1. `auto_on` (`0415b000`): `rcraw_en on=1 sw=w val=00000001 rdraw=00000000 d534=00000000 w1=0,0,00000000 w2=0,0,00000000,00000000,0000 q7c8=00000000`
  2. After closing Menu (`m`) + pressing `MOVIE` (`r`) + hot-reloading (`reload_ok 04887000`):
     - `mod_off`: `rcraw_en on=0 sw=w val=00000000 ...` (cleanly restored `PA 0x043D1810` to stock `0`)
     - `mod_on`: `rcraw_en on=1 sw=w val=00000001 ...` (cleanly re-armed `PA 0x043D1810` to `1`)
- **QEMU `AIF0` / `0xF30317C8` baseline observation:**
  - Both `AIF0 W1` (`0xF3002400`) and `AIF0 W2` (`0xF3002480`), as well as `0xF30317C8` and `PA 0x043D1534`, read `0` before and after `m` + `r` in `inputs-moddev-1`, confirming that `inputs-moddev-1` in `a6400-qemu-moddev` does not run an active `AIF0` DMA capture/live-view pipeline.
  - Also confirmed that `busybox` in `inputs-moddev-1` does not include the `devmem` applet (`devmem: applet not found`), so `/dev/mem` inspection requires native SMOD probes.

## Verification

- `bash mods/rcraw_enable_test/build_v2_wsl.sh artifacts/tests/rcraw_enable_test`: **PASS** (`1180` B, `0` absolute relocations, SHA-256 `603c1fdf24ed8775…`).
- `python mods/rcraw_enable_test/test_mod_v2_unicorn.py artifacts/tests/rcraw_enable_test`: **PASS** (`UNICORN RCRAW_ENABLE_TEST PASS (6 runs)`).
- QEMU `ModDev` cycle (`auto_on` -> `m` -> `r` -> `dev.reload`, `/tmp_bt/mods/rcraw_enable_test.log`): **PASS** (`mod_on 0415b000`, `mod_off 0415b000`, `mod_on 04887000`, `reload_ok 04887000`, `sw=w` on all 3 transitions).
- `python -m unittest tests/test_sensor_tables.py -v`: **PASS** (`12` tests).
- `check_markdown_links`: **PASS**.
