# E0629 — RCRAW quantization/threshold selector (0x63B340F8) and 0xF3031xxx block

Date: 2026-11-19. Offline firmware analysis and Unicorn verification only. No camera, SD, or USB access.

## Inputs

- `av-cam.bin` (`16,674,456` B, SHA-256 `90d515cfec8b936f…`, `ORIL v4`, linked at `0x635C6000`)

## Summary

1. **`IDT` / `RCRAW` hardware compressor block (`0xF3031xxx`):**
   - Literal scans across `0x63768000..0x63769000` and `0x63B34000..0x63B3C500` map the `0xF3031xxx` register set: `0xF30310B0`, `0xF3031570`, `0xF303159C`, `0xF30315A0`, `0xF30317B0`, `0xF30317C0`, `0xF30317C4`, `0xF30317C8`, `0xF30317CC`.
   - Also maps the `0xF3020600..0xF3020622` and `0xF3020C00..0xF3020C70` `PQD` / `CamCore` register windows in `0x63B3xxxx`.
2. **Unicorn verification of `0x63B340F8(out, mode, r2_val, r3_val)`:**
   - Called by `0x63B3BD70` and `0x63B3BF66` ([E0628](0628-a6400-rcraw-callers-and-0xf30317c8.md)) with `mode ∈ {0, 1, 2}`.
   - Sweeping `r2_val` in Unicorn (`UC_ARCH_ARM | UC_MODE_THUMB`) yields the exact threshold buckets:
     - `mode=0`: `200 → (0, 0x0004, 200, 1)`, `300 → (0, 0x0010, 286, 1)`, `350 → (1, 0x0020, 342, 1)`, `450 → (1, 0x0010, 366, 1)`
     - `mode=1`: `450 → (1, 0x0010, 404, 1)`
     - `mode=2`: `450 → (1, 0x0010, 440, 1)`

## Verification

- `$env:PYTHONPATH = "venv\Lib\site-packages"; python -m unittest tests/test_sensor_tables.py -v`: **12/12 PASS** (`0.620 s`), including `test_real_a6400_rcraw_quant_selector_unicorn`.
- `check_markdown_links`: **PASS**.
