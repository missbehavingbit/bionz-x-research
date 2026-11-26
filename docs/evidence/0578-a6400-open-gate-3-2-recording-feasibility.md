# E0578 — Open Gate (3:2) recording feasibility and development path on ILCE-6400

Date: 2026-11-18. Offline analysis and Unicorn/QEMU verification only. No camera, SD, or USB access.

## Inputs

- `av-cam.bin` (`16,674,456` B, SHA-256 `90d515cfec8b936f…`, `ORIL v4`, linked at `0x635C6000`)
- `libObj.so` (`20,860,436` B, SHA-256 `60ffd2b0f31f4bc1…`)
- `CAMERA_SCN_EXTERNAL_RAW_RECORD.so` (`7,793` B, SHA-256 `842fa541b4c2c600…`)
- `sabin/` (`90` files across `nflasha3_unpacked/sabin` and `nflasha15_unpacked/sabin`)

## Summary

Offline firmware analysis and Unicorn memory verification of `av-cam.bin` (`IMX28419C_ImagerCtrl_IMX284E`) establish that:

1. **Sensor readout (`IMX284`, 19 modes `0..18` at `0x63e129e8` + timing table `0x63e13284`):**
   - Full **3:2** (`6000×4000`) is present in modes `0, 1, 2, 3, 18` using full-height `v=4148` (`4835` in mode `18`) and reaches at most `10.44 fps` at 36 MHz (`3,446,988` clocks/frame in modes `2, 3`), matching continuous still burst (~11 fps).
   - Full-width **16:9** (`6000×3376`, modes `16, 17`) uses `v=1740` (2H readout: `3376` active lines + blanking = `3480 = 2×1740`) to reach `24.00 fps` (`862×1740`) and `25.02 fps` (`827×1740`). Scaling 2H readout to 4000 lines (`2074` in 2H units) at `h=827` yields `~20.99 fps`, so 24p/25p in full `6000×4000` is not reached without reducing horizontal blanking (`h <= 724`), raising sensor clock, or applying a slight crop (e.g., `5400×3600`).
   - **1/3 subsampled 3:2 (`1996×1332`, mode `10`)** already runs at **`29.97 fps`** (`660×1820 = 1,201,200` clocks @ 36 MHz; `59.94 fps` @ 72 MHz) and drives stock 3:2 LiveView. Mode `14` (`1024×684`, 3:2 crop) also runs at `29.97 fps` (`1232×975`).

2. **Buffer allocator (`0x63ddf414`, referenced at `0x6370ca82` → `0x637038a0`):**
   - Alongside 16:9 (`1920×1080`, `3840×2160`) and 4:3 (`1440×1080`, `2880×2160`), `av-cam.bin` already includes **3:2** DMM buffer entries:
     - **`1620×1080`** (aligned `1664×1080`, pool `0x50b3f`)
     - **`3240×2160`** (aligned `3264×2160`, pool `665`)

3. **Encoder (`ZIMA_AVC` / `ZIMA DVENC`) and application gates (`libObj.so`):**
   - Active H.264 encoder engines are `ZIMA_AVC/ZMA` (`sa_avce_quality_zma.bin`, `sa_avce_performance_zma.bin`, `sa_avce_performance_plus_zma.bin`) and `IMOLA_SEI`. `dv_venc_initialize()` accepts `canvas_width`, `canvas_height`, `valid_horizontal_offset`, `valid_vertical_offset`, and `zma_cfg_info`.
   - `ObjMovieRecorder` / `ModelMovieRec` in `libObj.so` and `VPI` stream types in `av-cam.bin` (`0x63eab272..0x63eab517`) expose only 16:9 (`3840×2160`, `1920×1080`, `1280×720`) and legacy `1440×1080` / `640×480` profiles, with no `OPEN_GATE` or `3:2` recording option. `CAMERA_SCN_EXTERNAL_RAW_RECORD.so` (`RAW_EXTERNAL_OUTPUT`) is present but unexposed in stock menus.

## Proposed Development Path

1. **Path A — 3:2 LiveView / YUV / MJPEG capture (`1024×680` @ 30/60 fps, lowest risk):**
   - Uses stock sensor mode `10` (`1996×1332` 3:2) in Still 3:2 mode and existing `VPH` / PTP / UVC output (`1024×680`), requiring no encoder or sensor timing changes.
2. **Path B — Internal `1620×1080` (3:2 HD) recording mod (medium complexity):**
   - Pair stock sensor mode `10` (`1996×1332` @ 29.97p/59.94p) with the existing `1620×1080` entry in `0x63ddf414` and `ZIMA DVENC` (`dv_venc_initialize` with `canvas_width=1620, canvas_height=1080`), bypassing `libObj.so` `MovieRec` aspect/resolution checks via Mod Loader v2.
3. **Path C — Internal `3240×2160` (3:2 4K) recording mod (high complexity):**
   - Uses the `3240×2160` entry in `0x63ddf414`, but requires either modifying `IMX284` MMIO/timing tables (`0xFC09xxxx..0xFC0Axxxx` / `h_param`) for ≥24 fps 3:2 readout (full or cropped, e.g., `5400×3600`) or upscaling from mode `10`.
4. **Verification roadmap in Unicorn and QEMU:**
   - **Unicorn:** Emulate `IMX284` vtable methods (`0x644fb4a0`), `ImagerMmioBank`, and `ZIMA DVENC` parameter validation (`dv_venc_initialize` / `dv_venc_open`) offline to confirm whether `sa_avce_*_zma.bin` accepts `1620×1080` and `3240×2160`.
   - **QEMU (`sony-a6400`):** Unblock the live-image pipeline past step 32 (`PQD Ydet` / `CamPig` IRQ 35/36, see [qemu-a6400/STATUS.md](../../qemu-a6400/STATUS.md)) and exercise `libObj.so` / `ZIMA DVENC` mods through `moddev.py` before any physical camera test.

## Verification

- `.\venv\Scripts\python.exe -m tests.test_sensor_tables` (`unittest tests/test_sensor_tables.py -q`): **7/7 PASS** (`0.426 s`).
- `python -m unittest tests/test_qemu_boot_inputs.py tests/emulator/test_unicorn_machine.py -q` (`PYTHONPATH=venv\Lib\site-packages`): **19/19 PASS** (`0.977 s`).
- `.\venv\Scripts\python.exe -m tools.research.map_sensor_tables`: mapped `90` `sabin/` files, `dfe_app.bin`, and `av-cam.bin` (`IMX28419C_ImagerCtrl_IMX284E`, `81` MMIO tables, `326` unique registers).
- Unicorn `UC_ARCH_ARM | UC_MODE_THUMB` guest-memory readback of `av-cam.bin` mapped at `0x635C6000` confirmed modes `2`, `10`, `14`, `15`, `16`, `17` across `0x63e129e8` and `0x63e13284`.
