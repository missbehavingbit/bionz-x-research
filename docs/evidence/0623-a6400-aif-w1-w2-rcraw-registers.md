# E0623 — AIF0/1 W1/W2 register semantics and RCRAW DDR path on ILCE-6400

Date: 2026-11-19. Offline firmware analysis only. No camera, SD, or USB access.

## Inputs

- `av-cam.bin` (`16,674,456` B, SHA-256 `90d515cfec8b936f…`, `ORIL v4`, linked at `0x635C6000`)

## Summary

Disassembly of `0x63756FB0..0x63757044` and inspection of its PC-relative log format strings supersede the tentative `+0x10` / `+0x24` / `+0x2C` field labels in [E0622](0622-a6400-aif-dma-channel-map-and-raw-external-gate.md):

1. **Channel identity (`0x63757020..0x63757044`):**
   - `0xF3002400` / `0xF3102400`: **`AIF0/1 W1`** (`FW: AIF%d W1 en:%d,run:%d,cuAdrs:%08x,size:%08x,loop:%04x`)
   - `0xF3002480` / `0xF3102480`: **`AIF0/1 W2`** (`FW: AIF%d W2 en:%d,run:%d,cuAdrs:%08x,size:%08x,loop:%04x`)
2. **Exact register fields (`0x63756FD2..0x63756FF0`):**
   - `+0x00` (bit 0): `en` (channel enable)
   - `+0x04` (bit 31): `run` (active transfer)
   - `+0x10`: **`cuAdrs`** (current DDR physical write address)
   - `+0x24`: **`size`** (transfer size in bytes)
   - `+0x2C`: **`loop`** (ring-buffer loop counter)
3. **Link to `RCRAW`:**
   - The string `[NSX_OPD]: PBL RCRAW AND SDEV AIF CONFLICT ERROR. W2.` (`0x63EED202` in [E0621](0621-a6400-10bit-and-raw-video-paths.md)) identifies **`W2` (`0xF3002480` / `0xF3102480`)** as the AIF DMA write channel used by `RCRAW` to stream RAW frames into DDR RAM.

## Verification

- Static disassembly of `0x63756FB0..0x63757044` and string resolution (`0x63756FC2`, `0x63756FEC`) in `av-cam.bin`.
- `$env:PYTHONPATH = "venv\Lib\site-packages"; python -m unittest tests/test_sensor_tables.py -v`: **10/10 PASS** (`0.453 s`).
- `check_markdown_links`: **PASS**.
