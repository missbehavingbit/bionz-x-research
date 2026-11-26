# E0651 — QEMU a6400 stock FINDER/MONITOR rear-LCD selection without diagnostic overlay

Indexed: 2026-11-19.

Date: 2026-11-18. Strictly offline static analysis and QEMU firmware replays following E0571–E0573. No physical camera, SD card, or USB device was accessed.

Labels: `[C]` confirmed by original binary instructions or replay receipts, `[I]` inference, `[A]` explicit assumption.

## Result

Stock Sony ILCE-6400 firmware selects and renders the rear-LCD shooting layouts (`LG_viewstlrec::LayoutST_NORMAL_WITH_INFO_LR`, `LayoutST_NORMAL_INFO_OFF_LR`, `LayoutST_NORMAL_LEVEL_LR`) on QEMU VPH head 0 without the E0572 `viewUnified2.so` sector patch (`REAR_LCD_DIAGNOSTIC=0`), through both `FINDER/MONITOR = Auto` and `Camera Settings2 -> FINDER/MONITOR -> Monitor(Manual)` `[C]`.

Previously, `sony_a6400.c` omitted GPIO `P0.2` from `CXD90045_GPIO_RESET_HIGH`, leaving the active-low EVF eye-contact sensor (`KEY_OTHER_EYE_CONTACT = 0x708B`) asserted low at reset `[C]`. Adding `0.2` to `CXD90045_GPIO_RESET_HIGH` sets the idle level to no eye contact, and `rear_panel.py` now uses stock firmware display selection by default (retaining `--diagnostic-lcd` as an opt-in diagnostic and `eye-sensor` on key `e`).

The integrated board follows E0591 and idles `P0.2` high only when `REAR_CONTROLS=1`, which the rear panel enables. The hashes below identify the original replay build.

## Input and binary identities

| Input / binary | Bytes | SHA-256 |
| --- | ---: | --- |
| Captured `viewUnified2.so` (inode 22532 in `nflasha15.bin`) | 11,530,508 | `fa534aa2160fc3e9c93119a85bdd9f1988b5f9f64b62c43fad178fabe4fa823d` |
| Update `viewUnified2.so` | 11,530,552 | `1e2867b6bff2d4fd4d3b93bacf8c7da0b9a86f266b4ba1763230e33badb6e7f2` |
| Update `CautionConfig.so` | 12,186,012 | `82dfef6a428ef6cb61904f80fb8c210e58dc08b1b321cd1cc5d2cb30b9d9996b` |
| Update `libObj.so` | 20,860,436 | `60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1` |
| Main `av-cam.bin` (`nflasha3`) | 16,803,048 | `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e` |
| Captured `Backup.bin` (`inputs-emmc-v2-20261116-124021-a`) | 1,171,036 | `13345a908433d7130adb382ee01a951ac8e691d091defd666d4d8a8af081232d` |
| Captured `emmc.img` (`inputs-emmc-v2-20261116-124021-a`) | 2,147,483,648 | `18e37ec3b3e56d26d31c0b0a4fd91ff10d0d1cb8a23308e49c35abd5cd3cc94b` |
| Captured `nflasha5.bin` (WBI1 warm-boot snapshot) | 30,277,632 | `a4724f7d7b7691d6a8cabc61ae18de247c2a24dea8dffdbf134c553a952fbbe5` |
| WBI-derived 384 B sub-micro frames (`wbi-powered-submicro-384.bin`) | 384 | `3385ec340e931aa7b7dbb954da99e5b6239174ba90441eb2c905f01bcf5bbda9` |
| Baseline QEMU binary (`P0.2` reset low) | 101,400,328 | `f88c1a77c80f636eef82cf20cd9aeb566614bd03f844c10b1a60a71feb5508f4` |
| Isolated `P0.2` reset-high QEMU binary | 101,400,328 | `8c042f45492b7c4a372ea6548b97f110aaf6033e612f2705cc9143a2d8e454f8` |
| Integrated `sony-a6400-qemu-11.1.3` binary | 101,497,496 | `8b34dce5f5ea65065c5310faab85834767b653079f97c20b4359a0a8245128e5` |

## Original instruction contract

1. **Eye-sensor GPIO binding (`P0.2`) `[C]`.** Both `captured_Backup.bin` and the default preset `CX62600_UC2.bin` define BK4 unit `0x00F90427 = 0x00000000` (GPIO port 0) and `0x00F90428 = 0x00000002` (pin 2, `P0.2`), while `0x00F90861` and `0x00F90862` are absent (`-1`). In `av-cam.bin` (`VA = PA + 0x60000000`), task `0x6361ac28` reads `0x00F90427`/`0x00F90428` via `0x635dd754`, branches at `0x6361ae02` to `0x6361ae86`, and samples `P0.2` via `0x63d7a296(0, 2)`:
   - When `P0.2 == 0` (`0x6361ae96`), `0x6361ae9a` (`movw r1, #0x708b`) posts `KEY_OTHER_EYE_CONTACT = 1`, which `InputService::changeTactStatus` maps to status `2` (eye detected).
   - When `P0.2 != 0` (`0x6361aeaa`), `0x6361aeae` (`movw r1, #0x708b`) posts `KEY_OTHER_EYE_CONTACT = 0`, which maps to status `1` (no eye detected).
   - Key catalog entry `0x13e1cd4` in `libObj.so` pairs `0x708b` with string `KEY_OTHER_EYE_CONTACT` (`0x100eac6`), and `ObjDisplay` registers `0x708b` in `ExecCommandSettingInfraInput` (`0xdb68b0`, table `0x1160b64`).

2. **`FINDER/MONITOR` setting and policy evaluation `[C]`.**
   - In `CautionConfig.so`, `CmnViewSettingNodeCustomFinderLCD` (`0x7f6664`) exposes `Auto` (`0`, `0x96050c`), `Viewfinder(Manual)` (`5`, `0xa205dc`), and `Monitor(Manual)` (`6`, `0x9b550c`).
   - In `viewUnified2.so`, `0x452f68` / `0x453158` read and write BK4 units `0x01070795` (`0` = Auto, `1` = Manual) and `0x010708e0` (`1` = EVF, `2` = Monitor), then invoke `requestModelExecute("model/DISPLAY", 0x2f, ...)` (`0x45309e` / `0x4530c4`).
   - Note on E0540: run `e0549` wrote `2` into `0x01070795`; `0x40a1f4` (`cmp r1, #1; bhi 0x40a206`) normalizes values above `1` back to `0` (`Auto`), where `P0.2 == 0` still forced `iVar12 = 1` (`EVF`).
   - In `0x40ae08` (`DISPLAY_read_output_model_and_apply`) and `0x2dc3ae` (`DISPLAY_calculate_output_device`):
     - Under `Auto` (`0x01070795 = 0, 0x010708e0 = 2`), `*param_2` (`uStack_38`, used when `0x40a350` returns `1`) is `1` (Monitor), while `*param_3` (`uStack_3c`, used when `0x40a350` returns `2`) is `2` (EVF).
     - Under `Monitor(Manual)` (`0x01070795 = 1, 0x010708e0 = 2`), both `uStack_38` and `uStack_3c` evaluate to `1` (Monitor).
     - Downstream `0x40a9b0` (`0x40aa5c`) passes output device `1` to `0x40ad26` -> `0x401828(0)` -> `0x40fde4(mgr, 0)`, publishing event `0x11014003` with active device `0`, so `0x1a44dc` selects the monitor leaf `0x1a0950`.

## Controlled replays and GDB RSP verification

Because the live-camera page `camera-page-0x03443000.bin` from E0527 is not stored in git, the 384-byte SIO ch3 sub-micro RX frames (`PA 0x034433A4..0x03443523`) were extracted from the camera's own WBI1 warm-boot partition `nflasha5.bin` (raw SHA-256 `570d565adf2c768abca285d93ba331875e12abf6ee37aaec2328b7e54d30215d`), with Slot B TLV `0x03` changed from hibernate `00 40 00 02` to powered `00 00 00 03` per E0527 section 0.2 and re-CRC'd (`3385ec34...`) `[A: powered TLV 0x03 matches E0527]`. All runs used `REAR_LCD_DIAGNOSTIC=0` (unmodified `emmc.img` backing), `EVSEL_LATE_US=500`, and `-icount shift=0,sleep=on`.

| Run | QEMU build / `P0.2` reset | `FINDER/MONITOR` mode | Observation after dismissing NTSC banner (`ret`) |
| --- | --- | --- | --- |
| `run04-old-p02low` | `f88c1a77...` (`P0.2 = 0`) | Stock `Auto` (`0x01070795=0, 0x010708e0=2`) | EVF shooting layout rendered: `799` nonblack pixels, head 0 SHA-256 `fa3d3b173960...`. |
| `run05-new-p02high` | `8c042f45...` (`P0.2 = 1`) | Stock `Auto` (`0x01070795=0, 0x010708e0=2`) | Rear-LCD shooting layout rendered without diagnostic overlay: `1,905` nonblack pixels (`fe2817156703...`) alternating with `2,892` nonblack pixels (`40b1f76791ad...`) as `NO CARD` blinks. |
| `run07-integrated-verify` | `8b34dce5...` (`P0.2 = 1`) | Stock `Auto` (`0x01070795=0, 0x010708e0=2`) | Bit-exact repeat on the integrated QEMU binary: banner `4,368` px (`b2705bc0c328...`), rear-LCD shooting `1,905` px (`fe2817156703...`) and `2,892` px (`40b1f76791ad...`). |
| `finder-menu-01` | `8c042f45...` (`P0.2 = 1`) | Interactive menu: `Viewfinder(Manual)` -> `Monitor(Manual)` -> `Auto` + `DISP` | Navigated `MENU -> Camera Settings2 (6/9) -> FINDER/MONITOR` (`step06_finder_submenu.png`), selected `Monitor(Manual)` (`step07_selected_monitor.png`), exited `MENU` (`step08_shooting_monitor.png`, `3,375` px `e1896d9492e3...`), cycled `DISP` to `No Disp. Info` (`1,471` px `16dc5d1a5a42...`) and `Level` (`1,587` px `d3996afa5ea6...`) under `Auto`. |

In `finder-menu-01`, `im.elf` (`PID 146`) mapped captured `viewUnified2.so` (inode 22532) at `0x128ca000`. Read-only `rsp_hitcount.py` (`rsp-monitor-manual.log`) recorded:

- `0x12cd4374` (`viewUnified2+0x40a374`, unpatched `mov r0, r4`): 3 hits, `r4 = 0x00000001` in all samples `[C]`.
- `0x12cd4a5c` (`viewUnified2+0x40aa5c`): `r0 = 0x00000001` (`r4 = 1`, Monitor output device) `[C]`.
- `0x12cd9de4` (`viewUnified2+0x40fde4`): `r1 = 0x00000000` (manager active device set to `0` = Monitor) `[C]`.
- `0x12a37624` and `0x12a37618` (`LG_viewstlrec::LayoutST_NORMAL_WITH_INFO_LR` methods `0x16d624` and `0x16d618`): 3 hits and 1 hit on object `0x26f45480` with group `0xcd53a632` and layout key `0xf84c57e5` `[C]`.
- `0x12a36be0` (`LG_viewstlrec::LayoutST_NORMAL_INFO_OFF_LR` method `0x16cbe0`): 1 hit on object `0x0b2a75f0` with key `0x421d6f27` after pressing `DISP`; EVF layout methods `0x16bccc` and `0x16b2f4` had `0` hits `[C]`.

## Limitations and next boundary

- Runtime `1 -> 0` eye-sensor transitions in `av-cam` `0x6361b032` also poll an unmodelled I2C proximity threshold at slave `0x44` when `0x00BF0023 == 1`; modelling that I2C sensor is separate from boot/menu `FINDER/MONITOR` selection.
- In `Viewfinder(Manual)` with `P0.2 = 1` (no eye at EVF), `0x2dc324` returns `0` (`uStack_38 = 0`, EVF idle when eye is absent), which maps to manager device `0` until `P0.2` goes low; physical EVF panel power gating is separate from the shared VPH `ch53` OSD channel.
- [E0591](0591-qemu-a6400-first-live-view-frame.md) qualifies synthetic luma through the native CamPig/RC0 path and distinguishes the original packaged step-32 and captured step-26 predicates. It does not establish real sensor timing or color/RAW processing.

Receipts, observer scripts, RSP logs, and PNG captures are retained under `artifacts/qemu-a6400/stock-rear-lcd-20261118/`.
