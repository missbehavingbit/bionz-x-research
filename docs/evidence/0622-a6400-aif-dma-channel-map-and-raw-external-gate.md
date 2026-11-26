# E0622 — AIF DMA channel map and SET_RAW_EXTERNAL_OUTPUT gate on ILCE-6400

Date: 2026-11-19. Offline firmware analysis and Unicorn verification only. No camera, SD, or USB access.

## Inputs

- `av-cam.bin` (`16,674,456` B, SHA-256 `90d515cfec8b936f…`, `ORIL v4`, linked at `0x635C6000`)

## Summary

1. **Hardware `AIF` DMA channel table (`0x63E23764..0x63E238A0` in `av-cam.bin`):**
   - `0xF3002000` (`CamPig 0` AIF): channels `+0x300`, `+0x380`, `+0x400`, `+0x480`
   - `0xF3102000` (`CamPig 1` AIF): channels `+0x200`, `+0x280`, `+0x300`, `+0x380`, `+0x400`, `+0x480`
   - `0xF3020000` (`PQD` / `CamCore` AIF): channels `+0x200`, `+0x280`, `+0x300`, `+0x380`, `+0x400`, `+0x480`
   - `0xF3041000`: channels `+0x200`, `+0x280`
   - `0xF3881000` (`RC0` AIF): channels `+0x200`, `+0x280`, `+0x300`, `+0x380`
2. **Per-channel AIF DMA register layout (`0x63756FB0..0x63756FF4` for `0xF3002400`):**
   - `+0x00` (bit 0): status/busy
   - `+0x04` (bit 31): flag
   - `+0x10`: configuration word
   - `+0x24`: physical DDR DMA buffer base address
   - `+0x2C`: DMA stride/pitch
3. **`SET_RAW_EXTERNAL_OUTPUT` / `SET_RAW_EXTERNAL_OUTPUT_COMP` gates (`0x63BE06E0`, `0x63BDC8D8`):**
   - `0x63BE06E0` resolves GOT base `0x645A21E4`, checks `[GOT+0x49FC]+0xE0`, and compares `[GOT+0x44C]` against constant key `0x00BF0274`, clearing `[r0] = 0` on mismatch.
   - `0x63BDC8D8` compares `[r4, #0x6D4]` against `[r2, #0x878]`, returning `0xC` on match (and logging `0xF000` when `[r3, #0x32] & 1`).

## Verification

- `$env:PYTHONPATH = "venv\Lib\site-packages"; python -m unittest tests/test_sensor_tables.py -v`: **10/10 PASS** (`0.458 s`), including `test_real_a6400_aif_dma_table_and_raw_ext_gate`.
- `check_markdown_links`: **PASS**.
