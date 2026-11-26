# E0630 — RCRAW global enable gate mapped and RCRAW_Enable_Test SMOD v2 verified in Unicorn and QEMU

Date: 2026-11-19. Offline analysis, Unicorn emulation, and QEMU (`sony-a6400`) only. No camera, SD, or USB access.

## Summary

Traced the active runtime `RCRAW` enable gate in `ILCE-6400` `av-cam.bin` (base `0x635C6000`), built the first functional `SMOD v2` test mod (`RCRAW_Enable_Test.mod`), and verified it in both Unicorn and QEMU `im.elf`:

- **Global enable words in LIRO RAM (`av-cam.bin`):**
  - `0x63B3CAC4()` reads `VA 0x643D1810` (`PA 0x043D1810`, initial value `0x00000000`) — `RCRAW` global enable word.
  - `0x63B3CAD0()` reads `VA 0x643D1BCC` (`PA 0x043D1BCC`, initial value `0x00000000`) — `RDRAW` global enable word.
  - Active runtime callers `0x63B38168` and `0x63B386CA` pass `(0x63B3CAC4(), 0x63B3CAD0())` through `0x63DB2312` into `0x6376873C`; when `r5 = r1 & 0xFF != 0`, `0x6376873C` invokes `0x63767ACC`, which programs `RCRAW` (`0x63CD278C`, `0x63CD28A2`, `0x63CD270C`).
- **`RCRAW_Enable_Test.mod` (`mods/rcraw_enable_test/rcraw_enable_test.c`):**
  - Built with `mods/rcraw_enable_test/build_v2_wsl.sh` (`936` code bytes, `1004` B total, `0` absolute relocations, SHA-256 `6476d5942c2aed4a…`; `--auto-on` build `a035698990da7965…`).
  - Performs a guarded 32-bit swap on `PA 0x043D1810` (`0 -> 1` on `rcraw_on`, `1 -> 0` on `rcraw_off`, readback verified, foreign values untouched) and logs `RDRAW` (`PA 0x043D1BCC`), `AIF0 W2` (`0xF3002480`), and `0xF30317C8` read-only to `/tmp_bt/mods/rcraw_enable_test.log`.
- **Unicorn (`mods/rcraw_enable_test/test_mod_v2_unicorn.py`):**
  - **PASS (6 runs):** verified `ON` (`0 -> 1`, `sw=w`), `ON` idempotency (`sw=a`), `OFF` restore (`1 -> 0`, `sw=w`), `OFF` idempotency (`sw=a`), foreign-value guard (`0xdeadbeef` untouched, `sw=x`), and untouched neighboring words (`+0x80C`, `+0x814`).
- **QEMU (`a6400-qemu-moddev`, `inputs-moddev-1`, `mlv2-layout-build-4`, `EVSEL_LATE_US=500`, `100 s` bounded run):**
  - `im.elf` reached `SetContentTarget` (`PID 145`, `view_bias=fd94d000`, `testcmd_bias=002e9000`).
  - `Mod Loader v2` installed cleanly (`MLV2_RESULT=0 changed=1`), pushed `/tmp_bt/mods/RCRAW_Enable_Test.mod` (`1004 B, fnv ok`), loaded `core.bin` (`19d28661`), and auto-enabled `RCRAW_Enable_Test` on the Sony UI thread (`mod_on 04035000 | auto_on 04035000`), keeping the UI responsive (`menu opened: the Mods tab is the last tab`).

## Next Boundary

- Trigger a video/capture pipeline transition in QEMU (or inspect `/tmp_bt/mods/rcraw_enable_test.log` and `AIF0 W2` `0xF3002480` / `0xF30317C8` post-transition) to verify whether setting `PA 0x043D1810 = 1` alone drives `0x63767ACC` / `AIF0 W2` to arm a compressed RAW DDR buffer, or whether an additional mode/DMM flag is required.

## Verification

- `bash mods/rcraw_enable_test/build_v2_wsl.sh artifacts/tests/rcraw_enable_test`: **PASS** (`1004` B, `0` absolute relocations, SHA-256 `6476d5942c2aed4a…`).
- `python mods/rcraw_enable_test/test_mod_v2_unicorn.py artifacts/tests/rcraw_enable_test`: **PASS** (`UNICORN RCRAW_ENABLE_TEST PASS (6 runs)`).
- Bounded `100 s` `rear_panel.py` run (`artifacts/qemu-a6400/rcraw_enable_test/stdout.log`): **PASS** (`guard install: MLV2_RESULT=0 changed=1`, `mod_on 04035000 | auto_on 04035000`).
- `python -m unittest tests/test_sensor_tables.py -v`: **PASS** (`12` tests).
- `check_markdown_links`: **PASS**.
