# Sony ILCE-7C (α7C) Emulator — Shareable Snapshot

This folder contains the **source, QEMU overlay, patches, tools, docs, and GUI launcher** for the Sony α7C QEMU emulator (`sony-a7c` machine).

> [!IMPORTANT]
> **No private camera data or firmware binaries are included.** `fw/`, `img/`, `dump/`, `out/`, and the compiled `qemu/` build tree are excluded so no personal settings, Wi-Fi/Bluetooth keys, certificates, or copyrighted firmware files are shared.

---

## Setup (with your own Sony ILCE-7C dump)

1. **Build QEMU in WSL2 Ubuntu (or Linux):**
   ```sh
   sudo apt install git build-essential ninja-build python3-venv pkg-config \
        libglib2.0-dev libpixman-1-dev libfdt-dev flex bison libgtk-3-dev libsdl2-dev
   ./setup-qemu.sh
   ```
2. **Extract your own A7C dump** (in `camera_dump_gui` layout):
   ```sh
   python3 tools/a7c_extract.py "/path/to/your/Sony A7C dump"
   ```
3. **Run (Windows GUI Launcher — no CMD window):**
   - Double-click **`Sony A7C Emulator.pyw`** (or **`Sony A7C Emulator.vbs`**).
   - It shows a progress bar and automatically opens the web button panel at `http://localhost:8642` when the screen is ready.
   - Alternatively, run `./run.sh` in WSL (`A7C_FULL=1` is now the default; pass `A7C_FULL=0` for minimal boot) or `python3 tools/a7c_panel.py`.

---

## What Has Been Done So Far

### Core Machine & Firmware Bring-Up (v1.0 – v1.20)
- **SoC & Boot:** 4 × Cortex-A5, GIC, PL011 UART0 (with `tx-level-irq` + root `ash` shell), CXD4115 timers, LDEC, XDMAC, PCIe RC0/RC1 + O2 Micro SD card reader (`A7C_SD_IMAGE`), eMMC (`sector-addressing`, copy-on-write `out/emmc.qcow2`).
- **Bootloader Stand-In (`sony_a7c.c`):** Loads `/setting/Backup.bin`, `/system/av-cam.bin`, and `/system/sabin/idt_cam.bin` (PA `0x20152000`) directly from the emulated eMMC at every reset.
- **Display & GPU:** DMP SMAPH-S30 GPU + shader interpreter, CXD90014 VPH + SYSV frame clock, `sony_fbview` OSD viewer.
- **Controls & Web Panel:** Rear buttons, control wheel, EV dial, mode dial, shutter S1/S2 (`shutter_stub` GIC edge-latching for IRQ 122 / 142), and `tools/a7c_panel.py` web GUI (`http://localhost:8642`).
- **Power-Micro & Camera Units:** Sub-micro HLE (`submicro_frames.bin` / `submicro_frames_live.bin`, plus opt-in `lens-reply`), ADC, P2S, SA DSP cores (`cxd90014_sa.c` with `ZMA1` startup & JPEG fill).

### Recent Additions (ported from `bionz-x-research` + launcher)
- **DFS & DDR-B Controllers (`sony_a7c.c`):** Added `CxdDfsControl` (`0xF2916000`) and `CxdDdrbControl` (`0xF0105000`), unblocking `sdcard_photo.img` boot past the `Of 008_ObjCamera` → `EV onStopping` stall so the camera reaches the `P` screen and `MENU` opens.
- **Live-View Front-End & CamCore (`cxd90045_cam.c`, `cxd90045_camcore.c`):** Ported Sgc1+Iif, CamPig, AIF DMA (`A7C_LIVEVIEW=1` / `liveview-fe=on`), and the 7 CamCore register-rule windows (`0xF3010000`, `0xF3000000`, `0xF3120000`, `0xF3C04000`, `0xF2912000/4000/5000`).
- **USB & DMA (`cxd90045_dwc3.c`, `SonyUsbVbus`, `pl330-dma`):** Ported the updated DWC3 gadget model with host-driven EP0 `CharBackend` socket, `SonyUsbVbus` (`0xF2920010`, IRQ 230, `usb-vbus-test` / `usb-cid-high` / `usb-vbus`), and opt-in `pl330-dma` (`0xF0030000`).
- **Windows GUI Launcher (`Sony A7C Emulator.pyw`, `Sony A7C Emulator.vbs`):** Launches silently with a progress bar, stage status, SD card selector, and clean QMP `quit` shutdown (`tools/a7c_panel.py`).

---

## What Is Still Missing

1. **JPEG Playback Display:** Pressing Playback (`p`) with `sdcard_photo.img` still hits `SeqMgr) ##### WARNING!!! Not Found Seq. Rsv SeqID:[0x8000000f] TypeID:[0x01] EventID:[0x4049] UnMatch. #####` and blanks the LCD (same open `Unable to display` / recorder teardown boundary as in `bionz-x-research`).
2. **In-Camera Format Verification:** End-to-end verification that `Setup` → `Format` on an empty `out/sdcard.img` now completes and writes FAT32 + `PRIVATE/SONY/SONYCARD.IND`.
3. **Live-View YUV Layer & Full Still Capture:** Rendering the YUV test bars/PGM image behind the OSD, resolving `[IDT]` / live frame timing if needed, and completing `STILL_CAPTURE_START_CMD` → JPEG encode → writing a new photo to the SD card.
4. **Stock USB Host Connection:** Connecting `cxd90045-dwc3` + `SonyUsbVbus` to a host USB bridge and clearing the stock USB startup policy gate so Mass Storage / MTP / PTP enumerates on a PC.
5. **Rear LCD vs. EVF Eye-Sensor Lock:** Verifying the eye-sensor GPIO keeps the rear LCD selected across all mode transitions.
6. **Auto Power Off (`OFF_BY_APO1`):** The camera still auto-powers-off after ~5 minutes of idle guest time if no buttons are pressed.
