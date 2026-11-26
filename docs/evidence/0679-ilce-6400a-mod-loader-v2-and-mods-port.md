# E0679 - Mod Loader v2 and Full 9-Mod `SMOD v2` Port to `ILCE-6400A` 1.01

```text
ID: E0679
Date: 2026-11-22
Claim: Mod Loader v2 (MLCR / SMOD v2, mlv2_guard) and all nine available ILCE-6400 runtime mods (MenuTouchFull, ModsTabLook, Arrivederci v5, OpenGate32, OpenGateSensor, OpenGate_5400_Test, RCRAW_Enable_Test, RCRAW_W2_Probe, and UsbStreamingTest) are ported to Sony ILCE-6400A firmware 1.01 with strict target isolation in the BIONZ X Mod Manager, passing 12/12 Unicorn suites across both bodies and a live 9-mod QEMU ILCE-6400A 1.01 rear_panel.py end-to-end session with read-only emmc.img integrity preserved.
Status: verified (offline Unicorn + live captured ILCE-6400A 1.01 QEMU guest; physical ILCE-6400A camera execution not run)
Firmware hash / camera configuration:
  - Target body: Sony ILCE-6400A firmware 1.01 (CXD90045, Sony CE Linux 8.2.7.6)
  - Captured QEMU image: ~/a6400-inputs/a6400a-v101-captured/emmc.img (3,909,091,328 B, SHA-256 ed231fc020c4e43a1bc7ef7d62738049a988622e7cdf7271bb3543b3e4e7e22f, verified identical before and after run)
  - Official updater package: firmware/extracted_a6400a_v101/ (verified bit-for-bit identical to captured nflasha7 / nflasha15 / nflasha16 / nflasha12 target binaries):
      viewUnified2.so   9,087,228 B  SHA-256 9d61ce612028cd7c225a15d0ba90b17f70b88d8797b7ca776aa876c45340ac4d
      libObj.so        13,701,604 B  SHA-256 26d9514fcfa3e0a4a3a7a93a6731f3b4d2fa68740bc59985a8b099b885e6a40b
      libtestcmd.so         6,936 B  SHA-256 98bb1d78038ac946a4da8a773c0223e3980c20172b850c6b35f43c415950ad4e
      libsyscommon.so     156,192 B  SHA-256 68f1e6b6b94592c3e89a2520f240343a403fc67dc506bd09da62b5c4902267b6
      libevsel.so          50,368 B  SHA-256 e47f5d2da1200da171ef971da76c7e22e9ed5b762a7cfbf452ed634f77b7f5a3
      libbackup.so         31,568 B  SHA-256 a5a2bd40eb851d691d46e83559560be415aa0c182491f16cd37cd495af0d15eb
      libmpr.so        11,363,404 B  SHA-256 0195ef63c01e4a652ef94370e5e466f9da18dfab7a14746e97d3c6abca91baaf
      libUsbControl.so     94,220 B  SHA-256 87b04eb5f7277926638a1624c07b1cdd84beec4bc3c5e7d89e3c4f207379b3b5
      libMWF.so           238,108 B  SHA-256 4189dc2c88f7b4bc22537d42f6a35c0e3db8a78749557bda058b086360d238dc
      libc-2.11.2.so      984,292 B  SHA-256 bc93700df79ac8f67356e4dbf5ab5c4c656865455e18ef2860acc178f5186bc2
      av-cam.bin       15,497,728 B  SHA-256 ab627564a17d45a6b54f43cc47bf4038f5578f257bcfe98a7dc1f7a38756262a
Source:
  - tools/native_camera/mlv2/{mlv2.h,mlv2_trampoline.S,core.c,mlv2_guard.c,package.py,build_wsl.sh,reference_layout_ilce6400a.txt,test_mlv2_unicorn.py}
  - tools/native_camera/{mods_tab_look_layout.h,mods_tab_look_trampoline.S}
  - mods/ilce_6400a/{Arrivederci,MenuTouchFull,ModsTabLook,OpenGate32,OpenGateSensor,OpenGate_5400_Test,RCRAW_Enable_Test,RCRAW_W2_Probe,UsbStreamingTest}.mod
  - src/a6400/mod_manager/{catalog.py,tools_bundle.py,camera_service.py}, tools/mod_manager_gui.py, tests/test_mod_manager.py
  - Receipts: artifacts/qemu-a6400a/mlv2-maps-20261121/, artifacts/tests/mlv2-ilce6400a-build-2/, artifacts/qemu-a6400a/mods-e2e-20261122/
Method:
  1. Verified via debugfs and SHA-256 that captured ILCE-6400A 1.01 user-space libraries and av-cam.bin match Update_ILCE6400AV101.exe bit-for-bit.
  2. Booted offline QEMU ILCE-6400A 1.01 (E0678 configuration) to capture /proc/151/maps (72 shared libraries -> reference_layout_ilce6400a.txt) and a QMP pmemsave of initialized LIRO RAM (0x04350000..0x045D0000), cross-verifying every Thumb-2 hook site, vtable slot, GOT/PLT veneer, and physical RAM preimage against live instructions and RAM bytes.
  3. Implemented --target ilce_6400|ilce_6400a across Mod Loader v2 and all 9 SMOD v2 mod build pipelines, keeping ILCE-6400 2.00 default builds unchanged.
  4. Added target-aware catalog, loader validation/isolation, and debugger hook verification to BIONZ X Mod Manager + unit tests.
  5. Executed all 6 Unicorn test harnesses across both targets (12/12 runs PASS) and a live 9-mod QEMU ILCE-6400A 1.01 rear_panel.py session toggling every mod ON/OFF and verifying logs, LCD renders, and unchanged emmc.img SHA-256.
Observation:
  - Mod Loader v2 ILCE-6400A build (shim @ 0x05d76800, 1,028 B, SHA-256 4363ae4f...; core.bin 8,220 B, FNV-1a 0x707bba7f, SHA-256 0157bd1b...; mlv2.req SHA-256 2650aa3f...; mlv2_guard_arm SHA-256 c6c13c56...) installs cleanly into live im.elf across non-zero ASLR biases (e.g. view_bias=0x00615000, testcmd_bias!=0): MLV2_RESULT=0 changed=1.
  - Live QEMU ILCE-6400A 1.01 9-mod run (artifacts/qemu-a6400a/mods-e2e-20261122/):
      * ModsTabLook.mod auto-enables (auto_on) and renders the teal MODS tab icon on the 7th tab across Mods 1/2 and Mods 2/2.
      * Arrivederci.mod: on=1 gamma=w color=w sabin=m lut3d=x; on=0 gamma=w color=w sabin=u lut3d=x.
      * MenuTouchFull.mod: ON -> OFF -> ON (0 errors) with dynamic relocation across viewUnified2.so, libsyscommon.so, and libevsel.so.
      * OpenGate32.mod: 51/51 writes & restores (ok=0x33: 27 av-cam u32, 14 DMM/VPI/VENC u16, 10 runtime-located libmpr.so u16; m16w 0x1518 <-> 0x1770, m16h 0x0e10 <-> 0x0d30, mprw 0x0ca8 <-> 0x0f00).
      * OpenGateSensor.mod: 16/16 writes & restores (ok=0x10).
      * OpenGate_5400_Test.mod: 41/41 writes & restores (ok=0x29).
      * RCRAW_Enable_Test.mod: sw=w val=1 -> 0 at PA 0x043F2B04.
      * RCRAW_W2_Probe.mod: read-only descriptor/RAW_EXT_PA log recorded cleanly.
      * UsbStreamingTest.mod (--action): verified all 3 file hashes and 9 function prologues, drove ILCE-6400A UsbControl state 1 -> 5 -> 7 (configure_result=0, start_result=0, service_state=7), and auto-closed the menu via EventManager::push (action_close).
Alternative explanations:
  - None for the verified instruction, relocation, and QEMU runtime behavior. Physical ILCE-6400A sensor/ISP recording behavior for experimental OpenGate/RCRAW mods remains subject to the same hardware pipeline limits documented on ILCE-6400 (E0578).
Confidence and limitations:
  - High confidence on binary/instruction parity, relocation safety, and live guest im.elf + LIRO RAM execution on ILCE-6400A 1.01.
  - Physical ILCE-6400A deployment requires an active native debugger session or service-shell bootstrap (ILCE-6400A blocks the unauthenticated USB Senser FileControl installer used on ILCE-6400, which Mod Manager continues to report as msc_blocked when no debugger is active).
  - QEMU sony-a6400 has no emulated touch controller, so MenuTouchFull's touch dispatch path on ILCE-6400A is verified via its Unicorn harness (test_mtf_v2_unicorn.py --target ilce_6400a) plus live QEMU hook installation/removal.
Dependent implementation/scenarios:
  - tools/native_camera/mlv2/, mods/ilce_6400a/, src/a6400/mod_manager/, tests/test_mod_manager.py
Next experiment:
  - Optional supervised RAM-only run on physical ILCE-6400A 1.01 once a native debugger session is active, or porting Mod Loader v2 to ZV-E10 2.02/2.03 using the same target-parameterized pipeline.
```

---

## 1. Library Identity & Reference Layout (`ILCE-6400A` 1.01)

Comparing every target ELF in the 17-partition `ILCE-6400A` 1.01 capture (`E0643`/`E0678`) against Sony's official `Update_ILCE6400AV101.exe` extraction (`firmware/extracted_a6400a_v101/`) confirmed **100% SHA-256 identity** across `nflasha7`, `nflasha12`, `nflasha15`, and `nflasha16`.

From a live `ILCE-6400A` 1.01 QEMU boot (`artifacts/qemu-a6400a/mlv2-maps-20261121/im_elf_maps.txt`, `im.elf` PID `151`), `tools/native_camera/mlv2/reference_layout_ilce6400a.txt` records all 72 `r-xp` library bases and trailing anonymous `.bss` spans, including:

| Library | Reference `r-xp` Base | Size / Cave Notes |
| :--- | :--- | :--- |
| `/usr/lib/libtestcmd.so` | `0x05D75000` | `0x05D75000..0x05D77000`; RX zero-tail shim cave at **`0x05D76800`** (`2,048` zero bytes `0x1800..0x1FFF`) |
| `/usr/lib/viewUnified2.so` | `0x12F46000` | `9,087,228` B (`0x12F46000..0x137C3000`) |
| `/usr/lib/libObj.so` | `0x0FA90000` | `13,701,604` B |
| `/usr/lib/libsyscommon.so` | `0x04A3F000` | `156,192` B |
| `/usr/lib/libevsel.so` | `0x04DC8000` | `50,368` B |
| `/usr/lib/libbackup.so` | `0x05DF0000` | `31,568` B |
| `/usr/lib/libmpr.so` | `0x111E2000` | `11,363,404` B |
| `/usr/lib/libUsbControl.so` | `0x058E1000` | `94,220` B |
| `/usr/lib/libMWF.so` | `0x04656000` | `238,108` B |
| `/lib/libc-2.11.2.so` | `0x042A0000` | `984,292` B |

---

## 2. Instruction- & RAM-Verified Target Coordinates (`ILCE-6400` 2.00 vs `ILCE-6400A` 1.01)

### 2.1 Mod Loader v2 (`mlv2`) & `ModsTabLook` (`viewUnified2.so` + `libevsel.so`)

| Symbol / Site | `ILCE-6400` 2.00 (captured) | `ILCE-6400A` 1.01 (ref `0x12F46000`) | Verification |
| :--- | :--- | :--- | :--- |
| Shim RX cave (`libtestcmd.so + 0x1800`) | `0x05662800` | **`0x05D76800`** | 2,048 zero bytes in RX PT_LOAD tail |
| `ML_PATCH_SET_ROOT` (`+0x5AA654`) | `0x15B91654` (`16` B) | **`0x134F0654`** (`16` B) | Identical Thumb-2 prologue `ef f3 09 82 20 68 ...` |
| `ML_PATCH_REFRESH` (`+0x5AAA80`) | `0x15B91A80` (`16` B) | **`0x134F0A80`** (`16` B) | Identical Thumb-2 prologue `2d e9 f0 4f ad f5 ...` |
| `ML_PATCH_OPEN_ITEM` (`+0x5AB044`) | `0x15B92044` (`12` B) | **`0x134F1044`** (`12` B) | Identical Thumb-2 prologue `2d e9 f0 4f 85 b0 ...` |
| `ML_FN_GET_TAB_MGR` / `ML_FN_GET_TAB_COUNT` | `0x15B92CC5` / `0x15BA4929` | **`0x134F1CC5`** / **`0x13503929`** | Exact Thumb-2 function match |
| `ML_FN_BUILD_PAGE` / `ML_FN_SET_TEXT` | `0x15B91AF5` / `0x15861D95` | **`0x134F0AF5`** / **`0x131C0D95`** | Exact Thumb-2 function match |
| `ML_GOT_REALLOC` / `ML_GOT_CALLOC` | `0x15E6FF3C` / `0x15E6FDAC` | **`0x137CEF3C`** / **`0x137CEDAC`** | `.rel.plt` `realloc` / `calloc` slots |
| `ML_TAB_META_STR` (`"MyMenu1"`) | `0x15DEE178` | **`0x1374D178`** | Verified in `.rodata` |
| `MTL_PATCH_DRAW` (`ModsTabLook`, `+0x5ADBB4`) | `0x15B94BB4` (`12` B) | **`0x134F3BB4`** (`12` B) | Custom `MODS` tab draw hook + `+0x5AE729` / `+0x5ADCF5` |
| `EventManager::getInstance` / `push` (`libevsel.so`) | `0x04DC85FD` / `0x04DC9BC5` | **`0x04DCF5BD`** / **`0x04DD0B85`** | Used by `SMOD_FLAG_ACTION` menu auto-close |

### 2.2 `MenuTouchFull` (`MTF2`)

| Symbol / Site | `ILCE-6400A` 1.01 Offset / Ref VA | Verification |
| :--- | :--- | :--- |
| `MTF_PATCH_APPLY_TOUCH` | `viewUnified2 + 0x005AAE80` (`0x134F0E80`, `12` B) | Identical `f0 b5 03 af 4d f8 04 8d 87 b0 04 46` |
| `MTF_PATCH_EVSEL_PUSH` | `libevsel + 0x00008BE8` (`0x04DD0BE8`, `12` B) | Identical `2d e9 f0 41 06 46 8a b0 0d 46 98 46` |
| `MTF_FN_MOVE_FOCUS` / `OPEN_POPUP` / `APPLY_POPUP` | `+0x5AE1ED` / `+0x5AB159` / `+0x2156AD` | `0x1315B6AD` verified calling `+0x5AE5BE` (`menu + 0x1FC`) |
| `MTF_GOT_TOUCH_PRESSED` / `RELEASED` (`libsyscommon`) | `+0x000282FC` (`0x04A672FC`) / `+0x00028490` (`0x04A67490`) | `.rel.plt` `_ZN3uxd20getTouchPressedPoint...` / `Released` |

### 2.3 `Arrivederci` v5 (`av-cam.bin` 1.01 LIRO RAM + `/usr/sabin/*` Tap Tables)

Cross-checked against both `av-cam.bin` (`0x63600000` load base) Thumb-2 instructions (`0x63AE3C6C`, `0x63AE3424`, `0x63ADF720`) and live QEMU `ILCE-6400A` 1.01 `pmemsave` RAM:

- **`GAMMA_PA`**: `0x03E560C4` (`1,024 x u16` S-Log3 EC record 31, identical 2,048-byte stock table).
- **`COLOR_PA`**: `0x0444FDC8` (`0x0444EC38 + 0x1190`, unique match for all `9 x s16` `COLOR_STOCK` entries across 2.5 MiB of live LIRO RAM; backed by Sony's shutdown `Backup.bin`, so `Arrivederci.mod` must be turned OFF before power-off and stays excluded from automatic `MLV2.TAR` SD packaging by default).
- **`CC_TUNING_PA`**: `0x0444ED96` (`0x0444EC38 + 0x15E`, live `0xFFFF` for S-Gamut3.Cine, cleared to `0x0000` while ON).
- **`REFADR0_PA`**: `0x044B191B` (`RefAdr1 = +0x634 = 0x044B1F4F`, `RefAdr2 = +0xCD4 = 0x044B25EF`, `LUT2D = +0x143C = 0x044B2D57`).
- **`SABIN` (`/usr/sabin/*`)**: `MS_BIND`-mounts the embedded SPICA/DCV 4K tap tables from `/tmp_bt/mods/sabin/*` onto `/usr/sabin/*.bin` on enable (`sabin=m`) and detaches them on disable (`sabin=u`); `libbackup.so` is not hooked (`hook_count = 0`).

### 2.4 `OpenGate32`, `OpenGateSensor`, `OpenGate_5400_Test`, and `RCRAW_*`

- **`av-cam.bin` 1.01 OpenGate sensor/crop table (`27 x u32`)**: `PA 0x0454388C .. 0x04543B28` (constant `-0x4A0` delta from `ILCE-6400` `0x04543D2C .. 0x04543FC8`, verified word-for-word in live QEMU RAM).
- **`av-cam.bin` 1.01 DMM / VPI / VENC 4K width table (`14 x u16`)**: `PA 0x0446C5F8 .. 0x0446C6F8` (`DMM`), `0x0446CD54 / 0x0446CDB4 / 0x0446CE14 / 0x0446CE74` (`VPI`), `0x04569AC4 / 0x04569ACC / 0x04569CA4 / 0x04569CAC` (`VENC`).
- **`libmpr.so` 1.01 4K width table (`10 x u16`)**: runtime-resolved via `/proc/self/maps` (`find_libmpr_base()`) at file/VA offsets `+0xA2F3D4`, `+0xA2F494`, `+0xA2FE60`, `+0xA2FEC4`, `+0xA5A114`, `+0xA5A130` (`3840 -> 3240`) and `+0x7C81E0`, `+0x7C84F0`, `+0xA5A358`, `+0xA5A37C` (`4096 -> 3264`), avoiding any numerical collision between physical `/dev/mem` addresses (`0x0454xxxx`) and virtual library mappings.
- **`RCRAW_Enable_Test` & `RCRAW_W2_Probe`**: `RCRAW_EN_PA = 0x043F2B04` (`0x63B4E370`), `RDRAW_EN_PA = 0x043F2EC0` (`0x63B4E37C`, exact `+0x3BC` stride), `D1534_PA = 0x043F2828` (`0x63B4FBC8`), `W1_PA = 0x04422FE8`, `W2_PA = 0x0442304C`, `W2B_PA = 0x04423240`, `Q7C8_PA = 0x043F2E28`, `RAW_EXT_PA = 0x044295BC` (`0x63BECDB0` `0x04429110 + 0x4AC`).

---

## 3. Verification Summary

1. **Unicorn isolated instruction suites (12/12 PASS across `ilce_6400` and `ilce_6400a`):**
   - `test_mlv2_unicorn.py` (all 15 scenarios including relocation, hot-reload, v1->v2 upgrade, `ModsTabLook` auto-ON, and `--action` auto-close)
   - `mods/menu_touch_full/test_mtf_v2_unicorn.py`
   - `mods/arri_isp/arrivederci/test_mod_v2_unicorn.py`
   - `mods/opengate_5400_test/test_mod_v2_unicorn.py` (`OpenGate32`, `OpenGateSensor`, `OpenGate_5400_Test`)
   - `mods/rcraw_enable_test/test_mod_v2_unicorn.py`
   - `mods/rcraw_w2_probe/test_mod_v2_unicorn.py`
2. **Live QEMU `ILCE-6400A` 1.01 9-mod `rear_panel.py` session (`artifacts/qemu-a6400a/mods-e2e-20261122/`):**
   - Guard verified & installed shim/core into relocated `im.elf` (`view_bias=0x00615000`); `Mods 1/2` and `Mods 2/2` rendered with custom `MODS` tab icon; all 9 mods toggled ON/OFF (or triggered via `--action`) with zero crashes/exceptions and exact stock value restoration; `emmc.img` SHA-256 remained `ed231fc020c4e43a1bc7ef7d62738049a988622e7cdf7271bb3543b3e4e7e22f`.
3. **Host unit tests:** `tests/test_mod_manager.py` (`20/20` PASS including `TestIlce6400aSupport`).
