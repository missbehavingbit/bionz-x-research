# E0621 — 10-bit and 11/12/14-bit RAW/YCC video paths on ILCE-6400

Date: 2026-11-19. Offline firmware analysis and Unicorn verification only. No camera, SD, or USB access.

## Inputs

- `av-cam.bin` (`16,674,456` B, SHA-256 `90d515cfec8b936f…`, `ORIL v4`, linked at `0x635C6000`)

## Summary

Static analysis and Unicorn emulation of `av-cam.bin` map the four candidate paths for >8-bit video on ILCE-6400 (`CXD90045` + `IMX284`):

1. **`220_422_10BIT` (`0x63EB6E4B`) is enum-only:**
   - Located inside the shared Sony debug string table (`SR_SQ_444`, `PRO_RES_422HQ`, `XAVC_CLASS_300`) read by `0x63697792`, not an active `ZIMA_AVC` encoder profile on ILCE-6400.
2. **`SET_RAW_EXTERNAL_OUTPUT` (`0x63F85108`, `0x63BD7370`, `0x63BD73A0`, `0x63BE06E0`):**
   - Executing singleton getter `0x63BE0C7C` in Unicorn returns `state_va = 0x64407C40` (`[+0x4AC] = 0x0`, `[+0x4B4] = 0x0` initially).
   - Mode dispatcher `0x63BE0C88` (`0x11C` entries) ends at `0x63BE06E0`, which checks `[r2, #0xE0]` and compares `[r3]` against a constant key, clearing `[r0] = 0` on mismatch.
3. **`IDT` `RCRAW` (`0x63B38410`, `0x63B3756C`) and `4KSuperSlowMotion[Buffering]` (`0x63854210`, `0x63853AD0`, `0x638F68C0`):**
   - `0x63B38410` dispatches bitmasks `0x400..0x8000` into `0x63B3756C..0x63B37814`.
   - `0x63B3756C` snapshots the `CamPig` / `IDT` MMIO block **`0xF3000200..0xF3000422`** into a `0x34`-byte descriptor (`tag=10`, `u32` at `+0x04` and `+0x14`, `u16` fields through `+0x30`) passed to `0x6376472C`.
   - `4KSuperSlowMotion` (`0x63853AD0`) reads buffer bounds from `[r1, #0x48]` / `[r1, #0x50]` and records the active ring-buffer index at `[r4, #0x6C]` / `[r4, #0x6E]`.
   - Together with `IMX284` Mode `10` (`1996×1332`, 12-bit, `29.97 fps`) and Modes `2, 3` (`6000×4000`, 12-bit, `10.44 fps`) from [E0620](0620-a6400-open-gate-4k-and-6k-paths.md), `RCRAW` (`0xF3000200..0xF3000422`) provides the hardware-backed path for >8-bit RAW capture.
4. **`PQD` tail setter (`0x63BFCB60`):**
   - Copies control bytes `[r3, #0xC5..#0xD5]` into the PQD output block `[r5, #0..#7]`.

## Verification

- `$env:PYTHONPATH = "venv\Lib\site-packages"; python -m unittest tests/test_sensor_tables.py -v`: **9/9 PASS** (`0.441 s`), including `test_real_a6400_idt_rcraw_unicorn_snapshot` (`0x63B3756C` reading `0xF3000200..0xF3000422` in Unicorn).
