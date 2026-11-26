# E0620 — Open Gate (3:2) 4K and 6K low-fps recording paths on ILCE-6400

Date: 2026-11-19. Offline firmware analysis and Unicorn verification only. No camera, SD, or USB access.

## Inputs

- `av-cam.bin` (`16,674,456` B, SHA-256 `90d515cfec8b936f…`, `ORIL v4`, linked at `0x635C6000`)
- `libObj.so` (`20,860,436` B, SHA-256 `60ffd2b0f31f4bc1…`)
- `sabin/sa_avce_quality_zma.bin` (`231,044` B, tag `ZM_AVCEQ0x00`)
- `sabin/sa_avce_performance_zma.bin` (`227,332` B, tag `ZM_AVCEP0x00`)
- `sabin/sa_jpeg_enc_1core.bin` (`13,956` B, tag `SAJPGE_C0x02`) and `sabin/sa_jpeg_enc_1unit.bin` (`22,532` B, tag `SAJPGE_U0x04`)

## Findings

1. **Unicorn verification of `0x63C9D13A` selector and `IMX284` bit-depth/channel table (`0x63E12610`):**
   - Added `test_real_a6400_opengate_3_2_unicorn_tables` in [tests/test_sensor_tables.py](../../tests/test_sensor_tables.py), which maps `av-cam.bin` at `0x635C6000` in Unicorn (`UC_ARCH_ARM | UC_MODE_THUMB`) and executes `0x63C9D13A`:
     - `aspect=1` (3:2) with `3840×1080` or `1920×1080` returns index `6`: **`3240×2160`** (aligned `3264×2160`, DMM pool `0x299` / `665`).
     - `aspect=1` (3:2) with `3840×2160` returns index `3`: **`1620×1080`** (aligned `1664×1080`, DMM pool `0x50B3F`).
     - `aspect=2` (16:9) with `3840×1080` returns index `4`: **`3840×2160`** (DMM pool `0x299`).
   - `libObj.so` at offset `0xe98bf6` contains the matching `(3840, 2160, 2880, 2160, 3240, 2160)` tuple.
   - Decoding the 19-entry `IMX284` configuration table at `0x63E12610` (stride `0x20`, from constructor `0x6378F4C4`) establishes per-mode bit depth and channel count:
     - Modes `0, 1, 18` (`6000×4000`): 14-bit (`0x0E00`), `1×1`, `w3=0x010000` (1-channel, `5.91–6.89 fps` @ 36 MHz).
     - Modes `2, 3` (`6000×4000` burst): 12-bit (`0x0C00`), `1×1`, `w3=0x010000` (`10.44 fps` @ 36 MHz).
     - Mode `10` (`1996×1332` 3:2 LiveView): 12-bit (`0x0C00`), `1×3` subsampling (`0x01030103`), `29.97 fps` @ 36 MHz.
     - Modes `15, 16, 17` (`4896×2760`, `6000×3376` 4K Movie): 11-bit (`0x0B00`), `1×1`, `w3=0x020000` (2-channel video readout, `w0=0`).

2. **PDAF ZPD fitting check (`SB_ZpdFittingTbl_IMX284_DMHH.cpp`):**
   - Strings around `0x63F7A7C0` confirm separate ZPD fitting table checks for `APS-C & 3:2` (expects `4430`) and `APS-C & 16:9` (expects `4626`).

3. **H.264 encoder limits (`enc/zima_dv_enc_ctrl.cpp` and `sa_avce_*_zma.bin`):**
   - `enc/zima_dv_enc_ctrl.cpp` (`0x639D0000..0x639E3000`, `VENC_IS_CLOSED` at `0x639E087A`) contains no hardcoded `mov`/`cmp` resolution whitelist for `1080`, `1920`, `2160`, `3840`, `4096`, or `6000`; the stock Movie mode table sits separately at `0x63E24408..0x63E24AFC` (`1440×1080`, `1920×1080`, `3840×2160`).
   - Both `sa_avce_quality_zma.bin` and `sa_avce_performance_zma.bin` contain `u32` constants **`4096`** (9 and 11 hits) and **`2304`** (5 and 3 hits), matching the H.264 Level 5.1/5.2 **`4096×2304`** 4K frame envelope.
   - Consequently, H.264 (`ZIMA_AVC`) 3:2 recording is bounded at **`3240×2160`** (existing DMM entry) or at most **`3456×2304`** if the ZIMA DSP accepts `height=2304`; taller 3:2 formats (`3840×2560`, `4096×2730`, `6000×4000`) exceed the `2304`-line H.264 DSP envelope.

4. **Formats larger than `3240×2160` at low fps (for post-production speed-up / conforming):**
   - **`6000×4000` (24 MP, 3:2) via `SA_JPEG_ENC` (`0x63F5F535`, `0x638F81CA..0x638F9E27`):** `sa_jpeg_enc_1core.bin` and `sa_jpeg_enc_1unit.bin` have no 2304-line cap and already encode `6000×4000` frames in stock burst mode. Pairing `SA_JPEG_ENC` with stock `IMX284` Mode `2/3` (`10.44 fps`, 12-bit) or Mode `0/1` (`6.89 fps`, 14-bit) provides a full-sensor 6K Open Gate MJPEG/JPEG-stream path without sensor overclocking.
   - **`6000×4000` RAW sequence:** Uses the same stock `IMX284` Modes `2/3` or `0/1` without JPEG/H.264 encoding, bounded by UHS-I SD write bandwidth.

## Verification

- `$env:PYTHONPATH = "venv\Lib\site-packages"; python -m unittest tests/test_sensor_tables.py -v`: **8/8 PASS** (`1.006 s`), including `test_real_a6400_opengate_3_2_unicorn_tables`.
- `python -m tools.research.ci_check --summary`: `py_compile` (`783` files), `binary_and_size_guard` (`1946` files), and `markdown_links` (`601` links) **PASS**; `status_svg_sync` and `guardrail_tests` failed on pre-existing uncommitted `E0619` / `Arrivederci` working-tree changes unrelated to this record.
