# Camera Mods & Installation Guide

Runtime mods for Sony **BIONZ X (`CXD90045`)** cameras load through a guarded RAM-only loader (`Mods` tab 7 in the Sony menu). The loader does not write firmware to flash. Sony can still save settings that a mod changes, as with the Arrivederci color matrix.

> [!TIP]
> **Windows GUI (1-Click Install & ON/OFF Toggles):** Double-click [`scripts/launch_mod_manager.bat`](../scripts/launch_mod_manager.bat) or see **[BIONZ X Mod Manager GUI](../docs/MOD_MANAGER_GUI.md)** to toggle runtime mods & PMCA tweaks ON/OFF, sync SD cards, or build a standalone `.exe` bundle.

> [!IMPORTANT]
> **Target boundary:** The current loader and production mods target the **captured `ILCE-6400` 2.00 library layout**. The official 2.00 package and `ILCE-6400A` 1.01 do not match the shipped hook and data preimages. A matching version label or relocatable `.mod` does not qualify different library bytes.

See the [firmware qualification matrix and replay receipts](../docs/evidence/mods/2026-11-19-firmware-qualification.md) for what was verified on each build.

> [!WARNING]
> **Sony ZV-E10 Support (Direct RAM Injection — Mod Loader Untested):**
> Mod Loader v1 and v2 are **currently untested on the ZV-E10** (SD-card `.mod` loading and the 7th `Mods` tab have not yet been ported to ZV-E10). At the moment, `MenuTouchFull` (including Fn menu touch in live preview) is injected directly into `im.elf` volatile DRAM via USB Senser terminal mode. See [`tools/native_camera/zve10/README.md`](../tools/native_camera/zve10/README.md).

---

## Available Mods

| Mod | Format | What it does | Status & Notes |
| :--- | :--- | :--- | :--- |
| **[MenuTouchFull (`MTF2`)](menu_touch_full/README.md)** | `SMOD v1` / `SMOD v2` / Direct RAM (`ZV-E10`) | Adds full touch support to Sony's main menu: tab taps, page-dot taps, horizontal swipe paging, 1st-tap select / 2nd-tap open, popup option tap & vertical swipe scroll, outside-tap close | v1 verified on `ILCE-6400` ([E0520](../docs/evidence/0520-mtf2-full-menu-touch-verified-on-a6400.md)); v2 navigation, popup highlights, and dismissal verified in [guest ON/OFF replays](../docs/evidence/mods/2026-11-19-firmware-qualification.md#native-touch-follow-up). **`ZV-E10`**: hardware-verified full main menu + 12-tile Fn menu touch via direct RAM injection ([details](../tools/native_camera/zve10/README.md); *Mod Loader is untested on ZV-E10 at the moment*) |
| **[Arrivederci](arri_isp/arrivederci/README.md)** | `SMOD v1` / `SMOD v2` | ARRI LogC3 Classic tone curve (EC record 31) + fitted Q10 color matrix (entry B8) for Picture Profile **S-Log3 / S-Gamut3.Cine** | **Usable in Modset v2** ([E0555](../docs/evidence/0555-arrivederci-menu-exit-recompute-and-cc8-refill.md), [E0556](../docs/evidence/0556-arrivederci-classic-fitted-matrix.md)). **Turn OFF before power-off** so Sony's backup does not retain the matrix |
| **ModsTabLook** | `SMOD v2` | Renders the custom `MODS` tab icon artwork (`E0522`) instead of reusing the 6th tab's ★ icon | Auto-enabled in QEMU mod-dev mode ([E0522](../docs/evidence/0522-mods-tab-icon-and-uxd-image-container.md), [MOD_DEVELOPMENT.md](../qemu-a6400/MOD_DEVELOPMENT.md)) |
| **Open Gate (3:2) Recording** | Shelved | Candidate `IMX284` 3:2 (`1620×1080` / `3240×2160`) recording path via `DMM` & `ZIMA DVENC` | [Original buffer selection and recording limits](../docs/evidence/mods/2026-11-19-firmware-qualification.md#stock-recording-and-open-gate). No runnable recording mod or playable camera/guest clip has been demonstrated. |

---

### Command items (`SMOD_V2_FLAG_ACTION`)

A mod packed with `pack_mod_v2 --action` appears in the `Mods` tab as a command instead of an ON/OFF switch, like the ZV-E10's "USB Streaming" entry. Pressing it starts the mod, and the menu closes to the shooting screen. The row then reads `Active`, and pressing it again stops the mod. Verified in Unicorn and QEMU only ([E0592](../docs/evidence/0592-mod-loader-command-items-usb-streaming-entry.md)). A UVC streaming mod that uses it does not exist yet.

---

## Option 1: Install & Run in QEMU (No Camera Needed)

You can develop, install, and hot-reload real `SMOD v2` mods inside the emulated `sony-a6400` machine before touching hardware:

1. Follow the setup and launch steps in **[Developing camera mods in QEMU](../qemu-a6400/MOD_DEVELOPMENT.md)**.
2. On Windows, double-click `a6400_QEMU.bat` (or run `rear_panel.py` in WSL) to boot into mod-development mode.
3. Drop `.mod` files onto the browser panel or watch a build path—the panel stages them via the unchanged guard and hot-reloads them live in the Sony `Mods` menu ([E0577](../docs/evidence/0577-qemu-real-mods-relocation-and-hot-reload.md)).

---

## Option 2: Install on a Physical `ILCE-6400` (RAM-Only)

### Using Mod Loader v2 (`MLCR` / `SMOD v2`, Recommended)

Mod Loader v2 ([design & status](../docs/MOD_LOADER_V2_DESIGN.md)) splits a tiny permanent RAM shim from a replaceable position-independent core (`core.bin`), supports relocatable `SMOD v2` mods without fixed code caves, rescans mods live when opening the `Mods` tab, and can be installed regardless of whether the menu was already opened.

1. **Build the loader & mods (WSL):**
   ```bash
   wsl.exe --exec bash tools/native_camera/mlv2/build_wsl.sh artifacts/tests/mlv2-build
   wsl.exe --exec bash mods/menu_touch_full/build_mtf_v2_wsl.sh artifacts/tests/mtf-v2-build
   wsl.exe --exec bash mods/arri_isp/arrivederci/build_v3_wsl.sh artifacts/tests/arrivederci-v3-build
   ```
2. **Stage `.mod` files:** Copy your built `.mod` files (e.g., `MenuTouchFull.mod`, `Arrivederci.mod`) into `mods/` on your SD card (`E:\mods\`) or push them to `/tmp_bt/mods/` over the [Mass Storage debugger](../docs/NATIVE_USB_DEBUGGER.md).
3. **Install RAM-only via the debugger:**
   Use `tools/research/install_mlv2.py` against a fresh Mass Storage (MSC) boot—the guard freezes `im.elf` threads via `ptrace`, verifies stock hooks and library hashes, installs the shim + core with readback/rollback, and resumes the camera.
4. **Toggle in the camera menu:** Open `MENU` → tab 7 (`Mods`) and press the center button on any listed mod to toggle it `ON` or `OFF`.

### Using Mod Loader v1 (`SMOD v1` / SD-Card Quick Tutorial)

If you are using the legacy `SMOD v1` build ([E0523](../docs/evidence/0523-mod-loader-ok-routing-diagnostics.md)):

1. **Get the touch mod:** Use the tested [`MenuTouchFull.mod`](menu_touch_full/MenuTouchFull.mod) or follow its [rebuild instructions](menu_touch_full/README.md#rebuild).
2. **Copy it to the SD card:** Create a `mods/` folder at the SD card root (`E:\mods\`) and place the `.mod` file inside (the v1 loader scans up to 12 `.mod` files).
3. **Build the v1 loader & guard:**
   ```powershell
   wsl.exe --exec bash tools/native_camera/build_mod_loader_wsl.sh
   ```
   This emits `mod-loader-{check,install,remove}.req` and `native_mod_loader_guard` under `artifacts/hardware/`.
4. **Install RAM-only via the [Mass Storage debugger](../docs/NATIVE_USB_DEBUGGER.md):**
   - After a fresh MSC boot (before opening the camera menu), `push` `native_mod_loader_guard` to `/tmp_bt/native_mod_loader_guard` and `mod-loader-check.req` to `/tmp_bt/mod_loader.req`. Confirm readback for both.
   - Run `chmod 700 /tmp_bt/native_mod_loader_guard` and execute it. Verify it reports both `CHECK_OK` and `THEME_OK`.
   - Push `mod-loader-install.req` to `/tmp_bt/mod_loader.req`, confirm readback, and run the guard again. It reports `INSTALLED` and resumes camera threads.
5. **Toggle & use:** Open `MENU` → tab 7 (`Mods`) and press the center button on `Menu Touch Full` to switch it on or off.
6. **Undo:** Restart the camera to unload the loader and all mods. (Or push `mod-loader-remove.req` during the same boot before the menu is first built.)

---

## Universal Relocation (`--reloc-layout`)

To make a single `SMOD v2` `.mod` binary run across different memory layouts (such as QEMU vs. physical camera, or future `CXD90045` bodies) without recompiling per address:
- Write mod sources against the canonical `ILCE-6400` reference addresses (`tools/native_camera/mlv2/reference_layout_ilce6400.txt`).
- Pack with `--reloc-layout tools/native_camera/mlv2/reference_layout_ilce6400.txt` (use `--no-reloc VALUE` for constants that merely resemble addresses).
- At load time, Mod Loader v2 shifts every relocated literal word and hook target by each library's live runtime base ([E0577](../docs/evidence/0577-qemu-real-mods-relocation-and-hot-reload.md)).

---

## Safety Rules

- **RAM-only, zero flash writes:** Never modify internal flash or boot partitions. Restarting the camera restores stock code.
- **Fail-closed guards:** Both `mlv2_guard` and the v1 guard verify firmware mappings and original hook bytes before patching, refuse on any mismatch, and roll back on error.
- **Arrivederci power-off rule:** Sony saves the color matrix table to `/setting/Backup.bin` on normal power-off. Always toggle **Arrivederci OFF** in the `Mods` tab before powering off the camera ([details](arri_isp/arrivederci/README.md#color-matrix-persistence-reversible-by-design)).
