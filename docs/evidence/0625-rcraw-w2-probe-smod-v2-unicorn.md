# E0625 — RCRAW_W2_Probe SMOD v2 build and Unicorn verification

Date: 2026-11-19. Offline ARM cross-build and Unicorn verification only. No camera, SD, or USB access.

## Summary

Built and Unicorn-verified a read-only diagnostic `SMOD v2` module ([mods/rcraw_w2_probe/rcraw_w2_probe.c](../../mods/rcraw_w2_probe/rcraw_w2_probe.c), [build_v2_wsl.sh](../../mods/rcraw_w2_probe/build_v2_wsl.sh)) that opens `/dev/mem` strictly read-only (`O_RDONLY | O_SYNC`, `PROT_READ`), snapshots `AIF0 W1` (`0xF3002400`), `AIF0 W2` (`0xF3002480`), `AIF1 W2` (`0xF3102480`), and `SET_RAW_EXTERNAL_OUTPUT` state (`0x04407C40+0x4AC/+0x4B4`, page `0x04408000`), and logs one formatted line to `/tmp_bt/mods/rcraw_w2_probe.log`.

- **Build artifact (`artifacts/tests/rcraw_w2_probe/RCRAW_W2_Probe.mod`):** `768` B (`700` code bytes, `0` hooks, `0` absolute relocations in `readelf -rW`, SHA-256 `45ab68b4e559e42d…`).
- **Unicorn harness ([test_mod_v2_unicorn.py](../../mods/rcraw_w2_probe/test_mod_v2_unicorn.py)):** Executes both `probe_life(1)` and `probe_life(0)` at relocatable base `0x40AB3000`, asserting `O_RDONLY` `/dev/mem` opens, `PROT_READ` `mmap2` calls, return code `0`, and exact formatted log lines (`UNICORN RCRAW_W2_PROBE PASS`).

## Verification

- `bash mods/rcraw_w2_probe/build_v2_wsl.sh artifacts/tests/rcraw_w2_probe`: **PASS** (`768` B, SHA-256 `45ab68b4e559e42d…`).
- `$env:PYTHONPATH = "venv\Lib\site-packages"; python mods/rcraw_w2_probe/test_mod_v2_unicorn.py artifacts/tests/rcraw_w2_probe`: **PASS** (`UNICORN RCRAW_W2_PROBE PASS`).
