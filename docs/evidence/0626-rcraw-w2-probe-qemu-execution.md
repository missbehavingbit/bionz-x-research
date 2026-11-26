# E0626 — RCRAW_W2_Probe SMOD v2 installed and auto-enabled in QEMU im.elf

Date: 2026-11-19. Offline QEMU (`sony-a6400`) only. No camera, SD, or USB access.

## Summary

Packed `RCRAW_W2_Probe.mod` ([E0625](0625-rcraw-w2-probe-smod-v2-unicorn.md)) with `--auto-on` (`768` B, SHA-256 `d47d0ed975d0d31f…`) and executed it inside QEMU (`a6400-qemu-moddev`, `inputs-moddev-1`, `mlv2-layout-build-4`, `EVSEL_LATE_US=500`, `100 s` bounded run):

- `im.elf` reached `SetContentTarget` (`PID 145`, `view_bias=ff725000`, `testcmd_bias=ff2b5000`).
- `Mod Loader v2` installed cleanly (`MLV2_RESULT=0 changed=1`), pushed `/tmp_bt/mods/RCRAW_W2_Probe.mod` (`768 B, fnv ok`), loaded `core.bin` (`19d28661`), and auto-enabled the mod on the Sony UI thread (`mod_on 0404d000 | auto_on 0404d000`).

## Verification

- `python3 -m tools.research.pack_mod_v2 --elf artifacts/tests/rcraw_w2_probe/rcraw_w2_probe.elf --name RCRAW_W2_Probe --on probe_life --off probe_life --auto-on --out artifacts/tests/rcraw_w2_probe_autoon/RCRAW_W2_Probe.mod`: **PASS** (`768` B, SHA-256 `d47d0ed975d0d31f…`).
- Bounded `100 s` `rear_panel.py` run (`artifacts/qemu-a6400/rcraw_w2_probe/stdout.log`): **PASS** (`guard install: MLV2_RESULT=0 changed=1`, `mod_on 0404d000 | auto_on 0404d000`).
- `check_markdown_links`: **PASS**.
