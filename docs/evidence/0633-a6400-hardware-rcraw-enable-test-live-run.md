# E0633 — Real ILCE-6400 RAM-only Mod Loader v2 + RCRAW_Enable_Test.mod execution and live AIF0 W1/W2 readout

Date: 2026-11-19. Physical `ILCE-6400` in RAM-only `/tmp_bt` (`Mod Loader v2` + `RCRAW_Enable_Test.mod`, no SD or flash writes).

## Summary

Installed `Mod Loader v2` (`mlv2-layout-build-4`: `mlv2_guard_arm`, `mlv2.req`, `core.bin`, `ModsTabIcon.rle`) and `RCRAW_Enable_Test.mod` (`1180` B, `--auto-on`, SHA-256 `7b64d9f5a38efe64…`) into camera RAM (`/tmp_bt/mlv2`, `/tmp_bt/mods`), installed hooks into `im.elf` (`/proc/149`, `FREEZE threads=174`, `MLV2_RESULT=0 changed=1`), opened `MENU` on the physical camera without an SD card, and read back logs + hardware registers upon reconnecting USB MSC:

- **`/tmp_bt/mods/mod_loader.log`:**
  - `core_init 00000002` -> `tab_icon_tab6 00000000` -> `core_load 19d28661` -> `mod_on 05d17000` -> `auto_on 05d17000`.
- **`/tmp_bt/mods/rcraw_enable_test.log` (captured on UI thread in Live View / Menu):**
  - `rcraw_en on=1 sw=w val=00000001 rdraw=00000000 d534=00000000 w1=1,1,ff000000 w2=1,1,fe40d190,00000148,018b q7c8=018000d0`
  - Confirms `RCRAW_EN` (`PA 0x043D1810`) transitioned `0 -> 1` (`sw=w`) on real hardware and `AIF0 W1` (`en=1, run=1, cuAdrs=0xFF000000`) + `AIF0 W2` (`en=1, run=1, cuAdrs=0xFE40D190, size=0x00000148, loop=0x018b`) were actively running in Live View.
- **Post-MSC `ro_rcraw_snap` readback (when sensor pipeline parked for USB MSC):**
  - `RCRAW_EN(043D1810) = 0x00000001` (persisted in LIRO RAM), `RDRAW_EN(043D1BCC) = 0x00000000`, `D1534(043D1534) = 0x00000000`, `AIF0 W1/W2 RUN = 0`, `RCRAW_7C8(F30317C8) = 0x018000D0`.
  - Note: `0xF30317C8 = 0x018000D0` and `AIF0 W2 SZ = 0x0148` match the pre-mod `E0632` baseline; verifying whether `PA 0x043D1810 = 1` alone re-arms `RCRAW` dimensions/buffer during a movie/capture start requires a capture or mode-reinit transition (not run here as no SD card was inserted).
- **Camera responsiveness & cleanup:** `/proc/uptime` progressed monotonically (`262.94 -> 400.87`), temporary `/tmp_bt/ro_rcraw_snap` removed.

## Verification

- `/tmp_bt/mlv2/guard check /tmp_bt/mlv2/mlv2.req`: **PASS** (`STATE hooks=stock v1_build=-1 shim=zero`, `MLV2_RESULT=0 changed=0`).
- `/tmp_bt/mlv2/guard install /tmp_bt/mlv2/mlv2.req`: **PASS** (`MLV2_RESULT=0 changed=1`, `RESUME count=174 result=0`).
- `/tmp_bt/mods/mod_loader.log` & `/tmp_bt/mods/rcraw_enable_test.log`: **PASS** (`auto_on 05d17000`, `sw=w val=00000001`, `w2=1,1,fe40d190,00000148,018b`).
- `check_markdown_links`: **PASS**.
