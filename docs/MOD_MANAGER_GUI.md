# Sony BIONZ X Mod Manager (Windows GUI)

The **BIONZ X Mod Manager** provides a modern Windows desktop interface (Edge WebView2) for toggling camera mods **ON / OFF** and installing them in one click.

## Quick Launch

Double-click [`scripts/launch_mod_manager.bat`](../scripts/launch_mod_manager.bat), or run directly from the repository root:

```powershell
# Desktop mode (Edge WebView2, auto-falls back to --web if pywebview is absent)
.\.venv-gui\Scripts\python.exe -m tools.mod_manager_gui

# Browser fallback mode (bound strictly to 127.0.0.1)
.\.venv\Scripts\python.exe -m tools.mod_manager_gui --web
```

`launch_mod_manager.bat` isolates optional GUI/USB packages (`requirements-gui.txt`) in `.venv-gui/` so the preserved offline test `.venv/` stays untouched.

## What You Can Toggle

1. **Runtime Camera Mods (`RAM-Only · Mod Loader v2`):**
   - **Menu Touch Full (`MTF2`)** — verified full touch navigation in Sony's menu.
   - **Arrivederci v5** — camera-verified (`mlv2-layout-build-4`, SMOD v2) ARRI Classic look for S-Log3 / S-Gamut3.Cine (`Movie`), automatically fetched and SHA-256 verified by **Download / Prepare Tools** and the standalone packager. Always toggle **OFF** in camera `MENU -> Mods` before powering off so Sony's backup does not keep the matrix.
   - **Mods Tab Icon (`ModsTabLook`)** — custom `MODS` tab icon (requires built `.mod` output).
   - **Open Gate 3:2 (`Sensor Stage` / `Full 3240x2160 Canvas`)** — mutually exclusive, experimental offline-verified builds.
   - **Custom `.mod` binaries** — drag and drop onto the window or click **+ Add Custom .mod**; validated against the SMOD v1/v2 header and FNV-1a checksum before staging.
2. **Permanent Firmware Tweaks (`PMCA Flash`):**
   - **Unlimited Video Recording** (`recLimit`)
   - **Unlimited 4K Recording** (`recLimit4k`)
   - **Unlock All 35 Languages** (`language`)
   - **PAL / NTSC Selector** (`palNtscSelector`)
   - Click **Read Current from Camera** to read live states via PMCA Senser before making changes. Only changed tweaks are written, and every permanent write requires explicit confirmation.

## 1-Click Install Flow

1. Connect your Sony camera via USB in **Mass Storage** mode and click **Scan Camera**.
2. Toggle desired runtime mods and/or PMCA tweaks **ON** or **OFF**.
3. Click **1-Click Install to Camera**:
   - **First-time camera setup:** verifies `usb_extcmd.ko` and `libusbcmd.so` via Senser (`ILCE-6400` / `ILCE-7C`), installs the pinned native USB debugger to `/setting` with read-back verification, and guides you through a single camera power-cycle.
   - **Unsupported or blocked bodies:** `ILCE-6400A` is blocked from Senser FileControl; unrecognized models are routed to a read-only onboarding `.zip` without writing to the camera.
   - **Mod Loader v2 & runtime sync:** runs `mlv2_guard_arm check`, atomically pushes `core.bin` and toggled-ON `.mod` files to `/tmp_bt/mods/` with read-back verification, removes toggled-OFF managed `.mod` files, applies hooks (or hot-reloads), verifies branch targets, and saves a timestamped JSON receipt.

## SD Card Sync

Switch to the **SD Card Sync** tab to stage toggled-ON `.mod` files directly onto a removable FAT32/exFAT SD card (`<Drive>:\mods\`) with SHA-256 read-back verification and `SHA256SUMS.txt` output. Fixed system drives are never listed.

## Standalone Windows Package (.exe)

Build a self-contained Windows bundle that packs Python, WebView2 UI, `Sony-PMCA-RE`, `libusb-1.0.dll`, official Mod Loader v2 (`mlv2-layout-build-4`), Arrivederci v5, and shipped `.mod` binaries:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_mod_manager_exe.ps1
```

Optional switches:
- `-Mlv2Dir <path>` — override the bundled Mod Loader v2 build directory instead of using `mlv2-layout-build-4`.
- `-SkipReleaseDownload` — skip downloading `mlv2-layout-build-4` / Arrivederci v5 from GitHub Releases.
- `-IncludeDebuggerBinaries` — bundle local `devcrashday_a6400/` USB debugger payloads for offline bootstrap.
- `-OneFile` — emit a single `dist\BionzX-Mod-Manager.exe` instead of `dist\BionzX-Mod-Manager\`.
