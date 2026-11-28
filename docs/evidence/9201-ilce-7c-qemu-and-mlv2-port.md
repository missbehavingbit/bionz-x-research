# E9201 — Sony ILCE-7C (a7C) QEMU board, hostwin transfers, and Mod Loader v2 port

- **Date:** 2026-11-26
- **Scope:** Sony ILCE-7C (`sony-a7c`) QEMU machine, `hostwin` fast file transfer, sub-micro slot-B lens reply, and Mod Loader v2 (`mlv2`) target `a7c` (`--target a7c`).
- **Code:** [qemu-a7c/qemu-overlay/hw/arm/sony_a7c.c](../../qemu-a7c/qemu-overlay/hw/arm/sony_a7c.c), [tools/native_camera/mlv2/a7c_layout.py](../../tools/native_camera/mlv2/a7c_layout.py), [tests/test_mlv2_a7c_layout.py](../../tests/test_mlv2_a7c_layout.py).

## Question

Can the Sony ILCE-7C (`sony-a7c`) QEMU emulator run the full Mod Loader v2 (`mlv2`) stack (`mlv2_guard_arm`, `shim`, `core.bin`, and SMOD v2 `.mod` payloads) with fast host-to-guest file transfer (`hostwin`)?

## Result: `sony-a7c` boots to live view/OSD and installs `mlv2` (`MLV2_RESULT=0 changed=1`)

### 1. ILCE-7C QEMU machine (`qemu-a7c`)

- **Board & SoC peripherals:** Ported `CxdDfsControl` (`0xF2916000`), `CxdDdrbControl` (`0xF0105000`), `CxdWorkDma` (`0xF0102000`), `cxd90045_cam`, `cxd90045_camcore` (`0xF3020000` windows), `cxd90014_sa` (`ZMA1` startup), `cxd90045_dwc3`, `SonyUsbVbus`, `pl330-dma`, `PL011 UART3` (`0xF2003000`, IRQ 120), `/system/sabin/idt_cam.bin` preload, `cxd90045_shutter_stub`, `a7c_submicro_lens_reply` (attached on slot B `0x31` and `len > 128` exchanges), and `sd_erase()` 1 MiB chunking.
- **Host window (`hostwin`):** Added `-M sony-a7c,hostwin=FILE` mapping a 16 MiB shared RAM file at `PA 0x50000000`, paired with `qemu-a7c/tools/hostwin_get.c` and `qemu-a7c/tools/moddev.py`. Uploading `mlv2_guard` (594,960 B) dropped from 328.6 s over serial raw/gzip to **2.67 s** (`fnv ok`), and `core.bin` (10,572 B) transferred in **0.25 s**.

### 2. Mod Loader v2 (`mlv2`) A7C layout and build (`--target a7c`)

Verified against extracted ILCE-7C binaries:

| Component | SHA-256 |
| --- | --- |
| `viewUnified2.so` | `1e524d4e97e6f0a1fc45e45dbbabec2f5d1a84d59d27e2d22e76f5fad73e2abe` |
| `libObj.so` | `ac07a5dfb140381bf95813a76794d83437726231a740928e590be8d4ea5dfdbd` |
| `libtestcmd.so` | `ebd2afaab57d26a2e3d93637768a8bb2e0fd2467f4ccbcf27707b53a64545ad5` |
| `im.elf` | `fca372d437333271f5d21c880768c02a99509bcc2629e3ba00122fb7a9f716af` |

| Symbol / Site | A7C offset | Notes |
| --- | --- | --- |
| `set_root` hook | `0x7fa2e0` | 16-byte replay identical to a6400/a6100 |
| `refresh` hook | `0x7fa718` | 14-byte replay identical to a6400/a6100 |
| `open_item` hook | `0x7facdc` | 8-byte replay identical to a6400/a6100 |
| `RECT_TBL1_SLOT6` / `RECT_TBL2_SLOT6` | `0x9ff308` / `0x9ff388` | 6-slot prefix verified at `0x9ff2d8` / `0x9ff358` |
| `SET_TEXT` PLT stub | `0x181c44` | `_ZN9GEN_Label13setTextDirectEPKc` |
| `SET_MODE` PLT stub | `0x188b3c` | `_ZN2ux6wgtsys6Widget7setModeENS1_10WidgetModeE` |
| `V1_ANCHOR` | `0xd9e550` | End of `viewUnified2.so` `.bss` (`0x00d00068 + 0x09e4e8`) |
| `libtestcmd.so` shim cave | `0x2800..0x3000` | 2048 B zero tail past `rx_filesz = 0x203c` |
| `event_new` (`_Znwj` PLT) | `0x001084d0` | `libObj.so` |
| `event_ctor` (`_ZN5EventC1Emhh`) | `0x00898b11` | `libObj.so` |
| `event_push` (`_ZN12EventManager4pushEP5Eventb`) | `0x0089a0bd` | `libObj.so` |
| `g_pEvtMgr` | `0x01488ea0` | `libObj.so` `.bss` direct |

### 3. Live QEMU verification

Running `tools/a7c_panel.py --card out/sdcard_fmt_test.img --mod out/mlv2-a7c/ActionTest.mod`:
- `mlv2_guard check /tmp_bt/mlv2.req` reported:
  `TARGET /proc/158 view_bias=feb06000 testcmd_bias=00081000 STATE hooks=stock v1_build=-1 shim=zero MLV2_RESULT=0 changed=0`
- `mlv2_guard install /tmp_bt/mlv2.req` succeeded:
  `MLV2_RESULT=0 changed=1`

## Remaining boundaries

- **Post-live-view `MENU`/`PLAY` key delivery:** `a7c_sm_slot_a` writes `CAkey1` (`out[28] = 0x05`), but once live view starts `av-cam` reads from its internal key-state copies rather than `out[28]` alone (requiring a direct `av-cam` RAM poke or `EventManager::push(0x15010102u)` trigger).
- **Focus bracketing:** `libInfraRemote.so` exposes `MrpwPropertyFocusStepNear` / `MrpwPropertyFocusStepFar` (`INFRAREMOTE_KEY_STEP_FOCUS_NEAR/FAR` via `SendSyncControlEvt` `0x610c`) and `libObj.so` exposes `WriteStillComp`, while stock A7C firmware has no `BracketFocus` menu node.
