# E0700 — QEMU Virtual Touchscreen, Peripherals (`BCM4339`, HDMI/CEC, AudioIp, E-Mount Lens Motors), and 8-Model `CXD90045` Compatibility

```text
ID: E0700
Date: 2026-11-25
Claim: Expands the out-of-tree QEMU v11.1.3 CXD90045 emulator (qemu-a6400/) with opt-in models for (1) the FocalTech 0x20 capacitive touchscreen IC on i2c0 (0xF2401000, IRQ 199) + GPIO P0.12 INT (IRQ 133) + VPH LCD pointer input and rear_panel.py /touch click/drag forwarding, (2) Broadcom BCM4339 SDIO Wi-Fi (mmc1 @ 0xF0301000, IRQ 221) and Bluetooth 4.1 H4 HCI + HCD firmware controller (uart3 @ 0xF2003000, IRQ 120 = /dev/ttyBT), (3) HDMI TX (0xF2A40000, 256-byte EDID DDC + HPD/RSEN + TMDS -> VPH Console 1) and CEC (0xF3C10000, virtual TV responder), (4) AudioIp (0xF3420000..0xF3428FFF, capture/playback DMA ring buffers + 440 Hz stereo PCM mic + jack detect), and (5) Sony E-mount BL physical bus (UART1 @ 0xF2001000, IRQ 151 + PL080 M2P/P2M @ 0xF0102000, IRQ 174 + P1.4 detect) with stateful focus, iris, zoom, and OSS lens motors, parameterized across all 8 registered CXD90045 camera models (sony-a6400, sony-a6400a, sony-zve10, sony-a6100, sony-a6600, sony-a7c, sony-a7m3, sony-a7rm4a).
Status: verified-offline (8/8 native qtest/QMP peripheral + multi-model suites PASS; live captured ILCE-6400 2.00 QEMU firmware boot with paired MenuTouchFull OFF vs ON pure virtual-touchscreen tap and swipe PASS; baseline -M sony-a6400 regressions preserved)
```

## Input and Tool Identities

| Artifact / Binary | Size | SHA-256 |
| --- | ---: | --- |
| Built QEMU `qemu-system-arm` (v11.1.3 + overlay) | — | built from pinned upstream `005c61c0ef6f6ae615283440b4bb7be51b2c6ddd` (`v11.1.3`) + `qemu-a6400/patches/qemu-v11.1.3-sony-a6400.patch` |
| Captured `ILCE-6400` `emmc.img` (`inputs-pr-preflight-20261116-1555`) | 2,147,483,648 B | `18e37ec3b3e56d26…` (verified identical before and after live run) |
| Powered sub-micro extract (`wbi-powered-submicro-384.bin`) | 384 B | `3385ec340e931aa7b7dbb954da99e5b6239174ba90441eb2c905f01bcf5bbda9` |
| Mod Loader v2 `mlv2_guard_arm` / `mlv2.req` / `core.bin` | 692,532 / 1,124 / 8,128 B | `740c480cbd67…` / `99d255c58dc8…` / `db869c73e249…` |
| Relocatable `MenuTouchFull.mod` (`mtf-heap-aware-20261119`) | 3,020 B | `0d0105e622c944776a3787964f75ce6798f0f7340d867ee47515e12eb6e8df54` |
| Static RE sources (`ILCE-6400` 2.00 & `ILCE-6400A` 1.01) | — | `vmlinux.bin`, `i2c.ko`, `hdmi.ko`, `cec.ko`, `mmc_drv.ko`, `mmcioWrapper.ko`, `wlanIrq.ko`, `bcmdhd.ko`, `av-cam.bin`, `libObj.so`, `CX62600_UC2.bin`, `CX77600_UC2.bin` |

## Reverse-Engineered Hardware Contracts [C]

1. **Touchscreen (`i2c.ko` + `av-cam.bin` Driver Table Entry 5 @ `0x645206f4`):**
   - Across all inspected `SYSASTRA-DSLR` `BK4` presets (`DX2`, `DX2C`, `DX2C_INX`, `DX2C_INX_HIBIKI2`, `DX2C_JDI_HIBIKI2`), Backup units `0x00F90819 = 0` (`i2c0` @ `0xF2401000`, IRQ `199`), `0x0101003E = 0x20` (7-bit slave address `0x20`), and `0x00F9037B/37C = 5.9` (`P5.9` reset) are identical.
   - `av-cam.bin` (`0x636d90de`, `0x636da190`) registers its touch interrupt on Linux IRQ `0x85` (`133`, GPIO `P0.12`), reads a 13-byte FocalTech burst at `reg 0x02..0x0E` (`0x636d95c8`), polls `reg 0x91` as an ESD live counter (`0x636d9be8`), and forwards 16-byte `[0x01000000, y | (x << 16), contact, aux]` reports (`x, y` in `0..255`) to UIPC queue `0x007b0026` (`0x636da604`), consumed by `libObj.so` `I-TpMain` (`InputService::callback_tp`).
   - `i2c.ko` (`+0x0d44`, `+0x1380`, `+0x15ac`) splits reads into an address-only TX sub-transfer (`I2C_ADDR = 0x85`) followed by a data RX sub-transfer (`I2C_ADDR = 0x88 | STOP`), checking `((I2C_LEN >> 4) & 0xF) + 1 == expected_len` and clearing `I2C_IRQ` (`+0x28` bit 0) with write-1-to-clear.

2. **Wi-Fi / Bluetooth (`BCM4339`):**
   - `/dev/ttyBT` symlinks to `ttyAM1` (`uart3` PL011 @ `0xF2003000`, IRQ `120`), driven by `startBTCore.sh` (`bsa_server -d /dev/ttyBT -p /usr/bin/bt_firm.hcd`).
   - `mmc1` (`0xF0301000`, IRQ `221`, Backup `0x013E000C = 0x01`) hosts the `BCM4339` SDIO card (`0x02D0:0x4339`, `ChipCommon` `0x18000000 = 0x10044339` `SOCI_AI` rev 4).

3. **HDMI TX & CEC (`hdmi.ko` @ `0xF2A40000`, `cec.ko` @ `0xF3C10000`):**
   - `hdmi.ko` accesses 32-bit word-strided registers: `0x3004` (`HPD` `0x40`, `RSEN` `0x80`, bit 1 `0x02`), `0x3005`/`0x3007` event/clear, `0x4004` PLL lock (`0x01`), `0x4001` TMDS output enable (wired to VPH `"hdmi-tmds"` Console 1), and `0x7E00..0x7E3F` DDC EDID reader (1 EDID byte per 32-bit word at `0x7E20+i`, completion bit `0x02` in `0x0105`).
   - `cec.ko` uses `0x00..0x11` TX buffer/length/start, `0x15..0x25` RX buffer/length, `0x27`/`0x28` logical address, and `0x60`/`0x61` RX-ready (`0x01`), TX-ACK (`0x02`), and TX-NACK (`0x04`) status/clear.

4. **AudioIp (`0xF3420000..0xF3428FFF`):**
   - `av-cam.bin` (`0x639e93f6`) accesses `0xF3428000..0xF3428038` above the earlier 32 KiB regfile, alongside capture (`+0x3000..+0x300C`), playback (`+0x3100..+0x310C`), and VU meter (`+0x3C00..+0x3C04`) registers.

5. **Physical E-Mount Lens Bus (`UART1` @ `0xF2001000` + `PL080` @ `0xF0102000` + `P1.4`):**
   - `av-cam.bin` (`0x63d0ccc6`, `0x6382911c`, `0x63a5fc04`, `0x63a59aec`) frames E-mount `BL` commands as `0xF0 <len16> <flags> <opcode> <payload...> <csum16> 0x55` and transfers them through `PL080` Unit 0 (`0xF0102000`, IRQ `174`) M2P/P2M to `UART1` (`0xF2001000`, IRQ `151`). `UART1` PrimeCell IDs at `+0xFE0..+0xFFC` remain `0` so Linux's AMBA probe skips `uart1` and keeps `ttyAM1` on `uart3`.

6. **8-Model `CXD90045` Compatibility:**
   - Dedicated QEMU machine types `sony-a6400`, `sony-a6400a`, `sony-zve10`, `sony-a6100`, `sony-a6600`, `sony-a7c`, `sony-a7m3`, and `sony-a7rm4a` (plus `camera-model=...`) parameterize EVF-less `P0.2` idle-high on `zv_e10`, 6-axis stationary IMU responses on `SIO ch4` (`0xF2405800`), dual-SD `mmc2` (`0xF0302000`, IRQ `224`) on `ilce_7m3`/`ilce_7rm4a`, and APS-C (`selp1650`) vs Full-Frame (`fe50f18`) default lenses when `peripherals=on`.

## Verification and Receipts

1. **Automated Unit & Native `qtest`/QMP Suites:**
   - `A6400_QEMU_DIR=$HOME/sony-a6400-qemu-11.1.3 python3 -m unittest tests/test_qemu_peripherals_and_models.py -v` -> **8/8 PASS** (`1.919 s`), covering all 8 `DEFAULT_REGISTRY` models, `cxd90045-touch` (click, drag, sub-20 ms tap, ESD heartbeat, I2C NACK), `BCM4339` SDIO + Bluetooth H4/`.hcd`, HDMI EDID/TMDS/VPH Console 1 + CEC TV, AudioIp 440 Hz PCM DMA, and E-mount lens motor slewing + `work-dma` channel 6 coexistence.
   - Baseline regressions (`tests/test_qemu_work_dma.py`, `test_qemu_f30a_fill.py`, `test_qemu_watchdog.py`, `test_qemu_vph_composite.py`, `test_qemu_liveview.py`, `test_qemu_emmc.py`, `qemu-a6400/tests/test_qemu_uart3.py`, `qemu-a6400/tests/test_qemu_f3c0.py`) -> **PASS** with default `-M sony-a6400` behavior unchanged.

2. **Live Unmodified `ILCE-6400` 2.00 Firmware + `MenuTouchFull.mod` Virtual Touchscreen Run (`artifacts/qemu-a6400/e0683-peripherals-touch-3/`):**
   - Booted captured `ILCE-6400` 2.00 firmware via `rear_panel.py --peripherals` with `MenuTouchFull.mod` (`0d0105e6…`) and `ModsTabLook.mod`.
   - Guest `/proc/interrupts` recorded **1,030 `i2c0` (IRQ 199)** interrupts and **77 `liro-133` (`P0.12` touch INT)** interrupts serviced by original `i2c.ko` and `av-cam.bin`, with `hdmi`, `cec`, `i2c`, `mmc_drv`, `codec_drv`, and `/dev/ttyBT -> ttyAM1` active.
   - **Paired `MenuTouchFull` OFF vs ON control:**
     - With `MenuTouchFull` **OFF**, `POST /touch` tap left the rendered `640×480` Sony menu frame byte-for-byte identical (`01_mtf_off_before.png` == `02_mtf_off_after.png`, header `417e0c602a`, tab `e2dec5e1b2`, body `13261fd103`).
     - After enabling `MenuTouchFull` **ON** (`mod_on 040a3000` on `MODS` tab, `03_mods_tab_mtf_on.png`), **pure HTTP `POST /touch` gestures with zero key presses** switched the Sony menu from `MODS` straight to `Network2` (`04_after_pure_touch_tab_tap.png`: tab `e2dec5e1b2`, body `13261fd103`) on a tab tap at `(94/255, 20/255)` and swiped horizontally from `Network2` to `Playback1` (`05_after_pure_touch_swipe.png`: tab `d05e9c1a2d`, body `2506b4c8bd`) on a `(185 -> 126 -> 74, 128)/255` drag.
   - Source `emmc.img` SHA-256 (`18e37ec3b3e56d26…`) verified unchanged after teardown.

## Boundaries and Limitations

- New peripherals are opt-in (`peripherals=on` or individual `touchscreen=on`, `bcm4339=on`, `hdmi-cec=on`, `audio-io=on`, `emount-lens=...`) so baseline `-M sony-a6400` unmodelled register counts stay intact; `rear_panel.py` enables `TOUCHSCREEN=1` by default (`--no-touchscreen` disables).
- `BCM4339` models SDIO enumeration, Silicon Backplane cores, TCM firmware upload, `BCDC` `ioctl` control replies, and Bluetooth H4 HCI + `.hcd` loading; it does not emulate 802.11 RF association/WPA2 data frames or live BLE peer connections.
- `cxd90045-emount-lens` models the `BL` serial/DMA bus, mount detect, and focus/iris/zoom/OSS motor kinematics, not optical defocus or exposure brightness changes on synthetic `liveview-source` frames.
- On captured `ILCE-6400` rear-LCD sub-micro replays where sub-micro reports HDMI unplugged, `hdmi.ko` loads and `cxd90045-hdmi-cec` serves HPD/EDID/TMDS/CEC, while stock UI HDMI display-mode switching still depends on sub-micro cable-state reporting.
