> Historical snapshot; current status is in the root README.

# Archived README "Latest progress" (through 2026-11-15)

The previous `Latest progress` list from `README.md`. It was replaced on 2026-11-15 by
the RAM-only Mod Loader and MTF2 touch-mod summary. Entries are preserved verbatim; only
relative link prefixes changed (`docs/` to `../`, `src/` to `../../src/`) because this
file lives inside `docs/archives/`.

- **Sugilite GPU & Sub-Micro Cross-Gen Parity ([cxd90045-sugilite-cross-gen-parity.md](../architecture/cxd90045-sugilite-cross-gen-parity.md)):**
  Disassembly of `CXD90045` `grm_gles.ko` (`sugilite_interrupt` `0x658`, `sugilite_clock_up_internal` `0x248`, `sugilite_register_init` `0x54`) confirms byte-for-byte parity with `Oein/a5000-emulator` (`CXD90014`) for `BAR 0xF1000000`, IRQ `76`, `+0x34` completion bits (`31/30/26`), ISR acks, and `0xF3400080` clock readback (`0x15`). Added clean-room `dmp_p3d.py`, `submicro_link.py`, `GuestGles.poll_p3d_bar()`, and `UnicornMachine` Sugilite MMIO/clock wiring.
- **ILCE-6400A Reference Qualification ([E0506](../evidence/0506-a6400a-offline-qualification-and-debugger-boundary.md)–[E0509](../evidence/0509-a6400a-msc-reply-lifetime-and-verified-native-identity.md)):**
  Qualified the signed `ILCE-6400A 1.01` updater package offline (`bionzx_machine_map`: 30 partition rows, 8 MMIO mappings, moved kernel hook sites) and added a narrowly allowlisted read-only USBSTOR identity probe (`a6400a_native_readonly` + `msc_extcmd_trace`), verifying normal-mode `GetModelInfo` (`0032858730` in `2.727 ms`) while keeping `ilce_6400a` at `reference`.
- **ISP Color & Gamma Pipeline ([E0505](../evidence/0505-av-cam-cc-3dlut-and-ec-gamma-pipeline.md)):**
  Decoded the `av-cam.bin` `CC:` 3D LUT controller (`FUN_63ad0fc8` / `FUN_63ad0930`, YCbCr 5×9×9 LUT with Q14 BT.601/BT.709 matrix) and `EC_Gamma` pre-8-bit pipeline (`FUN_63ba8158` / `FUN_63b1f614`) statically, and added typed sensor/LUT table models in [`src/a6400/sensor/`](../../src/a6400/sensor/__init__.py).
- **Update chain measured ([E0504](../evidence/0504-cxd90045-0x0402f666-pc-ldr-decode-fast-r0-vfp-and-busybox-entry.md), and the IPC edge inventory in [E0483](../evidence/0483-ipc-edge-inventory-libobj.md), and the platform model in [E0490](../evidence/0490-platform-model-and-flash-boundary.md)):**
  The image is **not** AES-encrypted. 484 files under the a6400 tree are ELF objects readable
  as-is; integrity is CRC32 (`uc_crc32sum.elf`, which imports no crypto at all) and MD5
  (`md5sum -c` in `loader_writer.sh`), and none of the updater tree's 18 scripts verifies a
  signature. The one AES helper uses the first 16 bytes of `sha256('')` as its key in ECB, and
  that key is identical on all thirteen bodies. Whether the bootloader accepts a recomputed CRC
  is still unknown and needs a camera.
- **a6400A Normal Identity Verified ([E0509](../evidence/0509-a6400a-msc-reply-lifetime-and-verified-native-identity.md)):**
  Original ARM/Thumb controls identify a finite reply lifetime, correcting the earlier
  split-process test procedure. One fresh, uninterrupted query now returns **ILCE-6400A**
  in **2.727 ms**, with no BUSY and no driver, mode or storage changes. Delayed response-only
  continuation is refused. This verifies the normal USB identity API, **not debugger,
  `/proc` or partition-dump access**; the a6400A remains a reference profile.
- **a6400A Read-only Access Preflight ([E0508](../evidence/0508-a6400a-native-readonly-transport-and-model-info-busy.md)):**
  A parent-verified native Windows probe works with the existing USBSTOR driver:
  SCSI inquiry succeeds when the owner runs the narrow probe as Administrator.
  The normal model-info query remains **BUSY**, including 40 bounded completion-only
  reads, so physical probing stopped. No driver change, service mode, helper upload,
  sector access or camera file write was used. This is a **historical failed procedure**:
  E0509 supersedes the normal-query block, while debugger/root/dump access remains unverified.
- **a6400A Machine Map ([E0507](../evidence/0507-a6400a-kernel-partition-mmio-and-hook-map.md)):**
  Main and updater kernels have separate build identities and BusyBox versions.
  Their recovered partition configuration and eight static MMIO mappings match the
  original package, while several exception/TTBR hook addresses moved. A reusable,
  hash-checked offline mapper preserves those differences and capture-only gaps.
  With an explicitly selected PL011 fixture, the original updater stops at timer
  MMIO after 3,017 instructions; **no boot, live `/proc` inventory or physical dump
  is claimed**, and no camera was accessed.
- **a6400A Qualification ([E0506](../evidence/0506-a6400a-offline-qualification-and-debugger-boundary.md)):**
  The signed 1.01 package matches the modeled CXD90045 / ARM EABI5 family and machine
  ID `0xDA9`. Its USB packing code passes offline; an original updater prefix executes
  3,001 instructions before an unsupported UART access, without patches or MMIO replies.
  Windows read-only metadata confirms Mass Storage `054C:0F0F`. A separately sourced
  **reference** profile is registered, but all seven USB kernel addresses moved and the
  driver differs. **Do not reuse the original a6400 installer, hooks or checkpoints**
  without body-specific qualification; no debugger installation or partition dump was attempted.
  Scoped checks pass; the full-suite rerun here is blocked by missing historical inputs.
- **Cross-Model BIONZ X Platform Map ([E0477](../evidence/0477-bionz-x-platform-map-13-updaters.md)–[E0482](../evidence/0482-three-stubs-gain-sourced-values.md)):**
  Thirteen official updaters unpacked offline: a6400, a6600, a6100A, ZV-E10, A7 III, A7C,
  A7R III, A7R IV, A7R IVA, A9, A9 II, ZV-1 and RX0 II. They run **nine kernels**, not thirteen:
  ZV-E10 and A7C put all 14,460 function symbols at the same address and differ in 9 data
  addresses, while a6400/a6600 share all 14,401 function symbols; a6100A/A7 III/A7R IVA share
  one symbol set but 4,033 of their 4,042 differing addresses are functions, so they are three
  separate kernels. The `av-cam` command table is a monotone ladder (285 records on the a6400,
  286 on the a6600 with exactly `0x413b` added, 337 on the ZV-E10/A7C). Six USB-side kernel
  symbols now resolve for every body **without a camera**, and for the a6400 the offline
  decode matches the existing live `/proc/kallsyms` read on all seven symbols. The
  `ilce_6100`, `ilce_6600` and `ilce_7m3` profiles stop being stubs and carry sourced values.
- **Offline Linux Kernel Boot ([E0414](../evidence/0414-nflasha-readonly-device-check-and-boot-area-dumps.md)–[E0510](../evidence/0510-nflasha1-busybox-ash-ttyam0-shell.md)):**
  Decoded the camera's first-stage bootloader and updater boot images, and emulated the
  interrupt controller, system timers, clock hardware, and serial console (`PL011` IRQ `150`).
  The updater Linux kernel now boots through delay calibration, mounts its compressed root
  filesystem, enables `ttyAM0` via `amba2.console=1`, preserves per-process `SP_usr` across
  `sys_clone` and `kuser` (`0xFFFF0000..0xFFFF1000`) returns, and boots cleanly into an
  interactive **`BusyBox v1.13.4` `/bin/ash`** shell (`/ # `), executing `uname -a` and `ls -la /`
  with zero unmapped accesses (`cpu_v7_do_idle+0x8`, completing Phase 1).
- **Persistent USB Debugger ([E0400](../evidence/0400-usb-debugger-and-runtime-probe-infra.md)–[E0405ab](../evidence/0405ab-device-desc-builder-and-msos2-subset.md)):**
  A v2 USB debugger runs in Mass Storage mode, letting us read kernel logs (including
  logs preserved across warm reboots), check USB state, and recover cleanly without pulling
  the SD card or battery. Offline contracts and a safe RAM-only round-trip check for
  automatic Windows WinUSB driver binding are also verified.
- **USB Video (UVC) Teardown & Stall Diagnosis ([E0352](../evidence/0352-offline-teardown-soft-disconnect-fix.md)–[E0476](../evidence/0476-status-prearm-fix-build-audit-unicorn.md)):**
  Fixed the USB disconnect sequence so the camera returns to Mass Storage mode cleanly
  without needing a battery pull. Live tests confirmed the camera can serve 29.8 FPS JPEG
  frames alongside PTP, and USB packet/kernel traces narrowed the remaining DirectShow
  streaming timeout down to the final status stage of a USB control handshake. The latest
  analysis shows the camera prepares that final step in time but the PC's reply never completes
  it; a fix that prepares it earlier is built and passes offline checks, awaiting a supervised
  camera test.
- **Wi-Fi & Bluetooth Chip Analysis ([E0406](../evidence/0406-bcm4339-wifi-bt-tuning-surface-readonly.md), [E0408](../evidence/0408-clm-blob-and-wlan-baseline.md)):**
  Identified and cataloged the Broadcom Wi-Fi/Bluetooth firmware, kernel drivers, and
  regulatory configuration blobs offline against the ZV-E10.
