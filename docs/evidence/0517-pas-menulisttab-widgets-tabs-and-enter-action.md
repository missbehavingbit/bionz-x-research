# E0517 — `PAS_MenuListTab` Object Layout, Tab/Row/Page Widget Arrays, and Offline Touch Baseline

- **Date:** 2026-11-14
- **Scope:** Offline disassembly of `PAS_MenuListTab` constructor and initialization helpers in `viewUnified2.so`, plus offline verification of the existing touch policy and native binding modules. No camera access was performed.

## 1. Input Identities & Artifacts

- `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/lib/viewUnified2.so` (`sha256: 1e2867b6bff2d4fd4d3b93bacf8c7da0b9a86f266b4ba1763230e33badb6e7f2`)
- `tools/native_camera/menu_touch_native.c`
- `tests/test_menu_touch_policy.py`
- Generated disassembly & test artifacts:
  - `artifacts/analysis/e0517_pas_menulisttab_init.txt` (`viewUnified2.so` `0x005aa560..0x005ab424`)
  - `artifacts/tests/menu_touch_native_test.elf` (`56,944 B`)

## 2. `PAS_MenuListTab` C++ Object Layout (`0x005ab090`)

- **[C] Constructor (`0x005ab090..0x005ab20c`):**
  - Initializes base `WidgetMode` at `+0x1e0` to `2` (`WidgetMode = 2`, passive).
  - Initializes tab/selection indices at `+0x1e4..+0x1fc` to `-1` (`+0x1ec` holds tab count, returned by `0x005aa46c`).
  - Constructs child widget containers:
    - `+0x0204`: `GEN_Icon`
    - `+0x0424`: `GEN_Icon`
    - `+0x0644` (size `0x42f8`): sub-array via `0x005a9a7a`
    - `+0x493c` (size `0x3a1c`): sub-array via `0x005a9ca0`
    - `+0x8358` (size `0x7688`): sub-array via `0x005a9ee6`, containing:
      - **`+0x88b0`**: `5`-slot widget array (`0x005aa920: cmp r5, #5`, stride `0x0220` = `544` B, widget ID table at `0x00802714`).
      - **`+0x9350`** (`0x9200 + 0x150`): `6`-slot row `PAS_BtnPopup` container array (`0x005aa948: cmp r5, #6`, stride `0x1118` = `4,376` B, widget ID table at `0x0080277c`).
    - `+0xf9e0` (size `0x65e0`): sub-array via `0x005aa068` (`PAS_MenuListPopup` at `+0xfb20`, `E0131`).
    - `+0x15fc0` (size `0x26d4`): sub-array via `0x005aa0aa`, containing **`15`** page indicator `GEN_Icon` widgets at **`+0x166b4`** (`0x005ab064: cmp r6, #15`).
    - `+0x186ac`: `CmnViewSettingNode*` root model pointer (`binding` at `0x005ab430`).

## 3. Offline Touch Verification

- **[C] Policy unit tests:** `python -m unittest discover -s tests -p "*touch*" -q` -> **Ran 28 tests in 0.066s — OK**.
- **[C] Native ARM Thumb build:** `arm-linux-gnueabi-gcc` (`-std=gnu11 -march=armv7-a -mthumb -mfpu=vfpv3-d16 -mfloat-abi=softfp -Os -Wall -Wextra -Werror -nostdlib -ffreestanding`) compiled `tools/native_camera/menu_touch_native.c` cleanly (`artifacts/tests/menu_touch_native_test.elf`, `56,944 B`, `0` warnings).
- **Environment note:** In this Windows host environment, `.venv\Scripts\python.exe` fails to import `_ctypes` (blocking `unicorn` probes in `.venv`), and nested `wsl.exe` invocations from Python fail with exit `0xC0000142`; running `python` (`C:\Python314\python.exe`) for `unittest` and `wsl.exe` directly from PowerShell succeeded.
