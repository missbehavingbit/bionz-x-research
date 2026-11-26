# E0681 - Standalone Option B SD-Card Auto-Boot (`MLV2.TAR`, FAT32/exFAT) for `ILCE-6400A` 1.01

```text
ID: E0681
Date: 2026-11-22
Claim: Mod Loader v2's standalone SD-card auto-boot companion (mlv2_card_read.elf, mlv2_sd_boot_hook.so, mlv2_sd_boot.sh, mlv2_guard_compact.elf, and MLV2.TAR) is upgraded to support arbitrary FAT32 and exFAT SD card geometries, standalone /usr/bin/im.elf preload without a USB debugger daemon, dynamic im.elf PID discovery, and BusyBox 1.34.1 CLI compatibility, verified via FAT32/exFAT/fragmentation/kill-switch tests, live captured ILCE-6400A 1.01 QEMU auto-boot + hot-removal runs, and a fail-closed Jiritsu installer/uninstaller (tools/research/a6400a_sd_boot_install.py).
Status: verified (offline FAT32/exFAT/shell suites + live captured ILCE-6400A 1.01 QEMU guest; physical ILCE-6400A installation awaits owner execution)
Firmware hash / camera configuration:
  - Target body: Sony ILCE-6400A firmware 1.01 (CXD90045, Sony CE Linux 8.2.7.6)
  - Captured QEMU image: ~/a6400-inputs/a6400a-v101-captured/emmc.img (2,147,483,648 B, SHA-256 ed231fc020c4e43a1bc7ef7d62738049a988622e7cdf7271bb3543b3e4e7e22f, unchanged before and after QEMU verification)
  - Built Option B bundle (artifacts/tests/mlv2-ilce6400a-sd-1/):
      companion/mlv2_card_read.elf      4,972 B  SHA-256 dfdb3d0bb4055bfa7de6b7ac1a02468804c11adb8825e50e7017fa4eb31bf970
      companion/mlv2_sd_boot_hook.so    4,872 B  SHA-256 18c4e1e1baae401b57933dd78722ba2ed7c677146fd6bdcb6a837d658481e213
      companion/mlv2_sd_boot.sh         3,936 B  SHA-256 a1f3c82d7ee1a3c51286d1cf6ec1769bd9bde843a17ceee6f46971d2af6c97dc
      companion/mlv2_guard_compact.elf 13,404 B  SHA-256 5a55ef98e470e3fac40723080c9fe959618ffe8f9e0b204fdae3ba1f048076f6
      card/MLV2.TAR                   184,320 B  SHA-256 54dc772f6c9877fba9ec0bb0a911f11c4c433f0277b3e0d4be225555464cec4e
Source:
  - tools/native_camera/mlv2/{card_read.c,sd_boot_hook.c,sd_boot_ilce6400a.sh,mlv2_guard.c,build_wsl.sh}
  - tools/research/{package_mlv2_sd.py,a6400a_sd_boot_install.py}
  - tests/test_mlv2_sd_boot.py, mods/ilce_6400a/README.md
  - Receipts: artifacts/tests/mlv2-ilce6400a-sd-1/, artifacts/qemu-a6400a/sd-boot-e2e-20261122/
Method:
  1. Replaced the fixed-8GB FAT32 geometry check in card_read.c with general FAT32 (tot16/tot32, 8.3 + LFN) and exFAT (0x85/0xC0/0xC1 directory sets, both NoFatChain contiguous and FAT-chained fragmented clusters) parsing on /dev/mmca1 (block 250:11), compiled freestanding (-DMLV2_BARE_ARM -nostdlib, 4,972 B) to fit single-packet Jiritsu uploads (<16 KB).
  2. Rewrote sd_boot_hook.c as a freestanding ARM syscall shared library (4,872 B, zero libc imports) matching /usr/bin/im.elf (or /setting/a6400_usb_debugd.elf) on ILCE-6400A while keeping ILCE-6400 gated to /setting/a6400_usb_debugd.elf, with /setting/disable_sd_boot kill-switch.
  3. Added sd_boot_ilce6400a.sh (leaving ILCE-6400's sd_boot.sh E0522 Airplane-Mode /tmp_bt contract untouched) for dynamic im.elf PID discovery, BusyBox 1.34.1 vs legacy timeout syntax auto-detection, automatic 4 MiB /tmp_bt tmpfs mount fallback, and unpacking all physical ILCE-6400A .mod files (linking mlv2_guard_compact.elf against 1.01 libc-2.11.2.so so MLV2.TAR is 180 KiB).
  4. Verified two end-to-end QEMU ILCE-6400A 1.01 boots on disposable /setting + /etc overlays (no-card clean stock boot and FAT32 MLV2.TAR auto-boot -> MENU -> MODS Tab 7 + hot card removal deactivation) and implemented tools/research/a6400a_sd_boot_install.py + tests/test_mlv2_sd_boot.py.
Observation:
  - QEMU Run 1 (no SD card): mlv2_sd_boot_hook.so mapped into im.elf, exited cleanly with /tmp_bt/mlv2 removed, and rendered stock 6-tab Setup3 menu (01_nocard_stock_6tabs.png).
  - QEMU Run 2 (FAT32 MLV2.TAR): mlv2_card_read.elf extracted MLV2.TAR, sha256sum verified card.tar: OK, guard froze 173 im.elf threads (PID 151, view_bias=0x01143000, testcmd_bias=0xffae2000) and installed shim/hooks (MLV2_SD_INSTALLED), MENU -> MODS rendered Tab 7 with ModsTabLook ON (02_sd_autoboot_mods1.png, 03_sd_autoboot_mods2.png), and unmounting /dev/mmca1 set sd-disabled + MLV2_SD_PACKAGE_UNAVAILABLE within 4 seconds.
  - Host test suite (tests/test_mlv2_sd_boot.py): 3/3 PASS covering FAT32, contiguous/fragmented exFAT, MLV2.OFF/disable_hook/>1 MiB rejections, prep-card DCIM preservation, closed-by-default camera entry, and install/uninstall /etc/ld.so.preload round-trips.
Confidence and limitations:
  - High confidence on FAT32/exFAT reader correctness, dynamic PID/relocation safety, and QEMU guest behavior; physical ILCE-6400A one-time Jiritsu installation and physical SD slot boot await supervised owner execution.
  - Post-review update (PR #76): `tools/research/package_mlv2_sd.py` and `tools/research/a6400a_sd_boot_install.py` now exclude `Arrivederci.mod` (alongside `UsbStreamingTest.mod`) from default `MLV2.TAR` builds unless `--include-arrivederci` / `--allow-arrivederci` is explicitly passed, preserving the `Backup.bin` color-matrix persistence guard (~55 KiB default `MLV2.TAR`; the initial `mlv2-ilce6400a-sd-1` QEMU receipt above included `Arrivederci.mod` at 184,320 B).
```
