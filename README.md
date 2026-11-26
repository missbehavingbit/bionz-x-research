# Sony BIONZ X Mods & Camera Emulator

An open-source toolkit for Sony **BIONZ X** cameras (such as the **a6400**, **a6400A**, **a6100**, and **ZV-E10**) that lets you:

1. **Install safe, reversible camera mods** — full touchscreen menus, cinema film looks, 6 MP low-light RAW capture, and background Wi-Fi photo transfer, without writing to internal camera flash.
2. **Run Sony camera software on your PC** — boot real camera firmware in our **QEMU** and **Unicorn** emulators to test mods and research hardware without even owning a camera.

> **Upstream attribution:** Built upon [ivica00/bionz-x-research](https://github.com/ivica00/bionz-x-research), retaining original upstream research, code, and contributor credits.

**[1-Click Windows Mod Manager](docs/MOD_MANAGER_GUI.md)** · **[Camera Mods](mods/README.md)** · **[QEMU Emulator](qemu-a6400/README.md)** · **[Add a Camera](docs/ADDING_A_BIONZX_CAMERA.md)** · **[UVC / PTP Liveview](docs/CONTRIBUTING_UVC_PTP.md)** · **[Full Technical Docs](docs/INDEX.md)**

---

## For Camera Owners — Install Mods in 5 Minutes (No Coding Needed)

If you just want to add mods to your Sony **`ILCE-6400`** (or **`ILCE-6400A` / `ILCE-6100`**), use the graphical **BIONZ X Mod Manager** on Windows:

1. **Install Python (one time):** Download [Python 3.11+](https://www.python.org/downloads/) and check **"Add python.exe to PATH"** during setup (or open a prebuilt `BionzX-Mod-Manager.exe` release if you have one).
2. **Launch the app:** Double-click [`scripts/launch_mod_manager.bat`](scripts/launch_mod_manager.bat). Wait a few seconds for the **BIONZ X Mod Manager** window to open.
3. **Download the mod pack:** If prompted at the top, click **Download Official Release** (or **Tools & Setup → Download / Prepare Tools**).
4. **Plug in your camera:** On the camera, set `MENU → Setup → USB Connection` to **Mass Storage**, connect via USB, and click **Scan Camera**.
5. **Choose mods & click Install:** Switch the mods you want **ON**, click **1-Click Install to Camera**, and follow the prompt. Unplug the cable and open `MENU → Mods` on your camera!

> [!NOTE]
> **Safe & reversible:** Runtime mods run in temporary camera memory (**RAM**) only — they make **zero writes** to internal firmware flash. Turning the camera off restores completely stock Sony software. *(Important: always toggle **Arrivederci** OFF in `MENU → Mods` before powering off.)*

### Available Mods

| Mod | What it does | Status |
| :--- | :--- | :--- |
| **[MenuTouchFull](mods/menu_touch_full/README.md)** | Lets you tap tabs, swipe pages, and use dropdowns across the Sony menu with the touchscreen | Hardware-verified on `ILCE-6400` ([E0520](docs/evidence/0520-mtf2-full-menu-touch-verified-on-a6400.md)) and `ZV-E10` ([ZV-E10 guide](tools/native_camera/zve10/README.md)) |
| **[Arrivederci](mods/arri_isp/arrivederci/README.md)** | Adds an ARRI LogC3 Classic cinema tone curve and color look right in-camera | Usable on `ILCE-6400` / `ILCE-6400A` ([E0556](docs/evidence/0556-arrivederci-classic-fitted-matrix.md)); turn OFF before power-off |
| **Film Simulations & Studio** | Custom Picture Profile film looks and live preview picker | Hardware-verified on `ILCE-6100` & `ILCE-6400` ([E9103](docs/evidence/9103-a6100-mod-loader-port.md)) |
| **PhotonBin 6MP** | 6 MP low-light 16-bit `.ARW` capture with high-conversion-gain mode | Hardware-verified on `ILCE-6100` ([E9104](docs/evidence/9104-a6100-photonbin-6mp-port.md)); QEMU-verified on `ILCE-6400A` ([E0680](docs/evidence/0680-photonbin-6mp-and-mlv2-nested-submenus.md)) |
| **[WiFiSync](mods/wifisync/README.md)** | Sends `.JPG` and `.ARW` photos in the background over 5 GHz Wi-Fi (`:8765`) | Faster than stock image transfer, but still slow to our liking |
| **ModsTabLook & Open Gate 3:2** | Custom `MODS` tab icon and experimental 3:2 full-sensor recording research | Details & installation steps in [mods/README.md](mods/README.md) |

---

## Supported Cameras

All listed cameras share Sony's **BIONZ X (`CXD90045`)** processor ([platform overview](docs/PLATFORM_MODEL.md)). Want to add another Sony model? See **[Adding a BIONZ X Camera](docs/ADDING_A_BIONZX_CAMERA.md)**.

| Camera | Support Level | What works today |
| :--- | :--- | :--- |
| **`ILCE-6400`** | **Verified (Hardware + Emulator)** | Full QEMU & Unicorn emulation, Mod Loader v2, MenuTouchFull, Arrivederci, Film Sims |
| **`ILCE-6400A`** | **Verified (QEMU + SD Auto-Boot)** | 4-core QEMU stock UI boot, Mod Loader v2 + 10 `.mod` packages, FAT32/exFAT SD auto-boot ([`mods/ilce_6400a/`](mods/ilce_6400a/README.md)) |
| **`ILCE-6100`** | **Verified (Hardware Mods)** | Mod Loader v2, Film Simulations, Picture Profile unlock, PhotonBin 6MP ([E9104](docs/evidence/9104-a6100-photonbin-6mp-port.md)) |
| **`ZV-E10`** | **Verified (RAM Touch Mod)** | Full Main Menu + 12-tile Fn touch on physical hardware via direct RAM injection ([ZV-E10 guide](tools/native_camera/zve10/README.md)) |
| **`ILCE-6600` · `ILCE-7C` · `ILCE-7M3` · `ILCE-7RM4A`** | **Reference Profiles** | Firmware symbols & USB IDs mapped offline; ready for contributors to verify mods |

---

## For Developers — Run the Camera on Your PC

Roughly 90% of development happens completely offline in Python and C using two complementary emulators:

- **QEMU (`qemu-a6400/`) — Full Virtual Camera:** Boots all 4 CPU cores, eMMC partitions, 40 Linux kernel modules, `av-cam.bin`, and the real camera menu (`im.elf`) across all 8 modeled `CXD90045` bodies (`sony-a6400`, `sony-a6400a`, `sony-zve10`, `sony-a6100`, `sony-a6600`, `sony-a7c`, `sony-a7m3`, `sony-a7rm4a`) with a live browser rear screen (`rear_panel.py`), clickable virtual touchscreen (`MenuTouchFull` taps & swipes), opt-in `BCM4339` Wi-Fi/Bluetooth, HDMI/CEC output, `AudioIp` DMA I/O, E-mount lens motors, and instant mod hot-reload ([E0700](docs/evidence/0700-qemu-touchscreen-peripherals-and-multimodel.md)). See [`qemu-a6400/README.md`](qemu-a6400/README.md) and [`qemu-a6400/MOD_DEVELOPMENT.md`](qemu-a6400/MOD_DEVELOPMENT.md).
- **Unicorn (`src/a6400/`) — Fast ARM Engine:** Runs isolated kernel (`L2`) and app (`L3`) slices in Python to verify mods, memory safety, and OS contracts automatically in CI without booting a full machine.

### What Does Not Work in the Emulator Yet
- **Real sensor & SD photo/video capture:** QEMU renders a synthetic test pattern and models E-mount `BL` focus/iris/zoom/OSS actuator positions without optical defocus or saved RAW/AVCHD/JPEG files ([QEMU status](qemu-a6400/STATUS.md)).
- **Live USB webcam (UVC) stream:** EP0 control enumeration works, while real USB video streaming endpoints remain unfinished ([CONTRIBUTING_UVC_PTP.md](docs/CONTRIBUTING_UVC_PTP.md)).
- **Over-the-air RF & BLE links:** `BCM4339` emulates SDIO enumeration, Silicon Backplane/TCM upload, `BCDC` control `ioctl`s, and Bluetooth H4 HCI + `.hcd` firmware loading, not live 802.11 WPA2/TCP traffic or BLE peer connections ([E0700](docs/evidence/0700-qemu-touchscreen-peripherals-and-multimodel.md)).

### Quick Start & Repository Map

Requires Python 3.11+ (tested up to 3.15.2; toolchain details in [`docs/TOOLCHAIN.md`](docs/TOOLCHAIN.md)). Camera dumps and private artifacts stay local and are excluded from Git.

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -q
```

| Folder | What's inside |
| :--- | :--- |
| [`qemu-a6400/`](qemu-a6400/README.md) | Virtual `sony-a6400` QEMU machine, browser rear panel, and mod hot-reload |
| [`mods/`](mods/README.md) | RAM-only camera mods, Mod Loader v2 binaries, and install guides |
| `src/a6400/` | Unicorn ARM emulators (`execution/`), camera profiles (`targets/`), USB debugger (`debug/`) |
| `tools/` | Firmware analyzers, Mod Loader builder (`mlv2/`), C helpers, and ZV-E10 deployers |
| [`a6400-lut-converter/`](a6400-lut-converter/README.md) | Offline color/tone LUT fitting and ARRI presets |
| `tests/` & `docs/` | Automated checks, engineering docs, and dated evidence logs |

---

## Project Status & Verification

This project never reports fake "compatibility percentages." Every claim is backed by a dated evidence file and tracked against the **2091 tests** recorded in the integrated offline suite (2026-11-18, [`docs/TOOLCHAIN.md`](docs/TOOLCHAIN.md); skip counts are tracked honestly and never counted as passes). Latest synced milestone: **[E9104](docs/evidence/9104-a6100-photonbin-6mp-port.md)**.

![Research status: demonstrated results and remaining boundaries](docs/images/research-status.svg?v=7e685d497ac2)

[Daily automated test suite growth: 2091 tests across 32 days](docs/images/test-growth.svg)

For deep reverse-engineering logs, memory maps, and USB traces, see:
[Current Technical State](docs/CURRENT_STATE.md) · [Knowledge Map](docs/map/README.md) · [Evidence Archive](docs/evidence/README.md) · [UVC / PTP Progress](docs/UVC_PTP_STATUS.md) · [USB Debugger v2](docs/NATIVE_USB_DEBUGGER.md)

---

## Safety Rules

- **Honest claims only:** Host-written UI or camera logic is **never** presented as real firmware compatibility.
- **Zero internal flash writes:** Never flash custom firmware or modify internal boot partitions.
- **Clean-room emulation:** Original firmware owns camera logic; unimplemented hardware stops cleanly instead of faking success.
- **Safe hardware inspection:** Read-only probes are allowed; bulk `/dev/mem` sweeps that can reboot the camera are strictly banned ([`AGENTS.MD`](AGENTS.MD)).

---

## Links & Attribution

[Docs Index](docs/INDEX.md) · [Architecture](docs/ARCHITECTURE.md) · [Roadmap](docs/ROADMAP.md) · [Repository Layout](docs/REPOSITORY.md) · [Contributing](CONTRIBUTING.MD) · [Agent Rules](AGENTS.MD) · [Related Repositories](docs/RELATED_REPOSITORIES.md)

- **Cross-generation BIONZ X research:** [Oein/a5000-emulator](https://github.com/Oein/a5000-emulator) by Park SungHyun (Oein) — clean-room reference for DMP SMAPH/P3D GPU commands and tiling (`docs/RE_GPU_P3D_CMD.md`, CC BY 4.0), interrupt pre-routing, and `im.elf` UI bringup.

Independent research; not affiliated with or endorsed by Sony. Sony, a6400, ILCE-6400, and BIONZ X are trademarks of their respective owners.
