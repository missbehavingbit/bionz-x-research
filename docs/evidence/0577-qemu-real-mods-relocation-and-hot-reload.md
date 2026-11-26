# E0577 — Real mods and hot reload in the QEMU Sony menu

Date: 2026-11-17. Offline QEMU only. No camera, SD or USB access.

## Result

The unchanged-source Mod Loader v2 ran in the QEMU guest's original `im.elf`.
It installed through its ptrace guard, added the Mods tab to the original menu
and loaded the camera build of MenuTouchFull. The mod turned ON, was hot-reloaded
twice from rebuilt host files and turned OFF. QEMU did not restart.
Boot to the menu checkpoint dropped from 63 s to 35 s.

| Step | Observation |
| --- | --- |
| Guard check / install | `TARGET /proc/145 view_bias=fd591000 testcmd_bias=ffcc9000 STATE hooks=stock`; `MLV2_RESULT=0 changed=1` |
| Core | `core_init 2`, `core_view_bias fd591000`, `core_load ce47b383` |
| Menu | The original menu drew a seventh "Mods" page listing `Menu Touch Full OFF` (the tab icon is not drawn) |
| ON | `mod_on 049d0000` |
| Hot reload 1 (new display name) | `mod_off 049d0000`, `mod_on 05334000`, `reload_ok 05334000`; the menu showed `Menu Touch Full (hot) ON` |
| Hot reload 2 (original bytes) | `mod_off 05334000`, `mod_on 049cf000`, `reload_ok 049cf000` |
| OFF | `mod_off 049cf000` (logged only after the original bytes read back) |

An earlier boot of the same inputs reported `view_bias=fcdc2000` and
`testcmd_bias=0017e000`, with the same install and ON results.
Receipts are under `artifacts/qemu-a6400/moddev/run2/`. They include the
panel and serial logs, the loader-log JSON and LCD frames for both states.

## Findings

1. **Library build.** The panel's default disk `qemu-a6400/data/emmc.img`
   (SHA-256 `ffdfbe36…`) carries a different `libObj.so` from the camera:
   20,860,436 B, and its text segment is 0x48 bytes shorter. The camera-captured
   prepared disk is the E0524 build, `emmc` `18e37ec3…`. It carries the
   camera's `libObj.so`; all four sampled 4 KiB blocks around the MenuTouchFull
   hooks match the live capture. In the guest, the inode numbers of
   `libtestcmd.so`, `libObj.so` and `viewUnified2.so` (22470, 22409 and 22532)
   equal those in the E0261 camera maps.
2. **Process layout.** This guest runs with `randomize_va_space=1`. Library
   bases changed between boots, while the load order matched the camera.
   With `norandmaps` the order changed (libObj first), so it does not reproduce
   the camera layout. Fixed-address code therefore cannot run unchanged.
   Relocating by library base is exact for identical binaries.
3. **Speed.** The guest's eMMC backing on `/mnt/c` is read through WSL's 9P
   bridge, synchronously in the vCPU thread. That thread ran at about 43% CPU.
   With the inputs copied to the Linux filesystem, `SetContentTarget` arrived
   after 31.6–35.3 s instead of 56–65 s. Without icount (`-accel tcg,thread=multi`),
   the shell went silent at 69 s, so icount stays.
   At the menu the guest runs at 0.45–0.53× real time. Neither icount shift 1
   nor shift 2 changed that ratio. Guest `/proc/stat` showed real load of about
   0.7 CPU, mostly system time. Idle CPUs sit in the kernel `wfi` at `0x6011a338`.
   Sub-micro SIO interrupts (`liro-129`) ran at about 1.4 k/s.

## Change

Reference addresses stay those of the ILCE-6400 camera layout. The guard, shim,
core and packer now relocate by library, and a zero bias reproduces the camera behavior.

- **Guard** (`mlv2_guard.c`). It selects the single process whose comm is `im.elf`.
  It reads each library base from the target's maps (the first mapping at file
  offset 0) and moves every hook, table, shim and entry address by that difference.
  Request ML2Q version 2 carries the shim relocation list. Version 1 is accepted
  only at zero bias. The original-byte checks are unchanged. A new `fnv FILE`
  operation checks transfers on guests without hash tools.
- **Shim** (`shim.c`, `mlv2_trampoline.S`, `package.py`). The shim is linked twice,
  with the shim moved by 0x1000 and the view base by 0x10000. Every word that
  differs must move by exactly one of these, or the build stops. The result is
  15 relocations: 9 for libtestcmd and 6 for viewUnified2. The shim publishes
  both differences in `ShimState` (layout ABI 1).
- **Core**. The view difference and the relocated shim slot live in two words of
  the core image (`.text.zdata`), written once by `core_init`. Mods with
  `SMOD_V2_FLAG_RELOC` carry a table of library ranges and literal words.
  `/proc/self/maps` supplies the bases. Words are moved after the code hash check,
  and hook targets move by their library. A library that is not mapped shows `ERR`.
- **Packer** (`pack_mod_v2.py --reloc-layout`). It lists `.text` words inside ARM
  `$d` mapping-symbol regions whose value lies in a library range of
  `tools/native_camera/mlv2/reference_layout_ilce6400.txt`. That file holds the
  E0261 library mappings plus each directly following bss mapping.
  For MenuTouchFull it listed 32 words: all 28 library addresses in the source,
  across libObj, viewUnified2 and CautionConfig. Event codes in anonymous memory
  and heap objects were not relocated. The disassembly has no `movt`.
  The relocatable file is byte-identical to the camera-tested `mtf-v2-build-1`
  except the flags byte and the appended 236-byte table.
- **QEMU tools**. `qemu-a6400/tools/moddev.py`:
  - stages the guard and request on the run's disposable `/setting` overlay
    (`run.sh SETTING_FILES`);
  - drives the guest shell over a unix-socket serial port (`run.sh SERIAL`);
  - mounts a guest tmpfs at `/tmp_bt`;
  - pushes the core and mods as 200-byte printf chunks, because the guest tty
    truncates lines near 1 KiB;
  - checks each push with the guard's `fnv` and publishes it by rename;
  - runs the guard `check` then `install`;
  - watches the host files and uses the E0576 reload request.

  The nudge presses MENU up to twice, so the next root setup processes the request.
  `rear_panel.py` gains `--inputs`, `--loader` and `--mod` options, a `/moddev`
  status page and a `/moddev/log` page. It copies the prepared inputs to the
  Linux filesystem once. It also re-reads the page on each load.
  `rear_lcd_overlay.py` now knows the E0572 sector on both disks.
  The captured disk holds it at `0x37ea5200`; both copies patch the same
  instruction at `viewUnified2.so+0x40a374`.

## Identities and checks

| File | SHA-256 |
| --- | --- |
| `mlv2-layout-build-2/core.bin` (7412 B) | `0ad35328e45c55df5a87f36cc06e126e80e7cbee7421c29fe8efc8a893eca5f6` |
| `shim.bin` | `87b9f1c546c375fd4ea22048b53d8131003d4a6ad362f243ea25367f569bd5fc` |
| `mlv2_guard_arm` | `740c480cbd676a128fafa728a6b8396d33662a0fff9166505b076e589b3436c7` |
| `mlv2.req` (ML2Q v2) | `99d255c58dc8547b38e1c2a835f122fa88f28a86666e11384b25187b25512f85` |
| `mtf-v2-reloc-build-1/MenuTouchFull.mod` | `2c658af25a0f2e9d6daa1f608185975e868db238dfbf60db5f94c3ce9a0b4dd6` |
| QEMU build of the current overlay (`~/a6400-qemu-moddev`) | `982d80a4bee41797f27412bb228b9db0e1d4fdcab04f23d84c8df3e0a8c644e8` |

The Unicorn harness passes scenarios 1–12 on the new build. It also passes new
scenario 13, which models a moved layout:

- the shim is relocated from the request as the guard does it;
- `SET_TEXT` and the tab anchor are reached at the moved view base;
- the MenuTouchFull hooks land at the moved libObj targets;
- all 32 words moved by their library and no other word moved;
- OFF restores both moved prologues;
- without maps the mod shows `ERR`.

The `mlv2-sd-build-3` core still passes. The host ptrace guard test passes its
eight earlier cases, the no-op reinstall and a new moved-layout install.
`mods.menu_touch_full.test_mtf_v2_unicorn` passes on both MenuTouchFull builds.
`tests/test_qemu_moddev.py` covers the push encoding, the `fnv` parsing, the
staging paths, the relocation table and the overlay table. The full suite passed
1988 tests with 18 skipped. Receipts are `unicorn.txt`, `guard-host-test.txt` and
`full-suite.txt` in `mlv2-layout-build-2`.

## Not shown

- MenuTouchFull's touch handlers did not run. The QEMU board has no touch-panel
  input, so the guest test covers loading, hooking, lifecycle, reload and rollback.
- The new loader was not run on the camera. At zero bias it is designed to act as
  before, and the camera-layout Unicorn and host scenarios pass. The context layout
  and version are unchanged, so a running v2 loader can adopt the new core in place.
  The new shim and guard need a fresh install after a restart. Camera use needs an
  explicit go-ahead.
- The first menu open after install shows six tabs; the Mods page appears from the
  next root setup. The Mods tab icon is not drawn in QEMU.
  Follow-up (same day): the icon resources `0x020cc012/19/1a` exist only while the
  ModsTabLook hook (libObj `+0x79d4b6`, E0522) is installed, which only the camera's v1
  loader does. Core `mlv2-layout-build-3` (`c0fcbb35…`) checks that hook at the live
  libObj base and otherwise uses the sixth tab's icon (`tab_icon_tab6` in the loader log).
  The panel now closes and reopens the menu once after install. Through the Desktop
  launcher path, the first visible menu showed seven tabs, and the seventh opened
  the Mods page. Receipts: `artifacts/qemu-a6400/moddev/launcher-tab-test.txt` and
  `launcher-mods-tab.png`.
  Second follow-up: ModsTabLook was rebuilt from its unchanged C source and trampoline as a
  relocatable SMOD v2 (`mtl-v2-build-1/ModsTabLook.mod`, `3d3e2b0b…`). It carries exactly two
  relocations, the libObj return addresses `0x064b5487` and `0x064b54c3`. The embedded icon
  RLE is excluded with the new packer `--data-symbol` option. The packer now refuses relocations
  from any `$d` region over 256 bytes unless that region is named. The new SMOD flag
  `SMOD_V2_FLAG_AUTO_ON` (8) lets core `mlv2-layout-build-4` (`f5833556…`) switch a mod ON when it
  first lists the file; a user OFF sticks (Unicorn scenario 14). Through the launcher, the log
  showed `mod_on 0413e000` and `auto_on 0413e000`, and the tab bar showed the MODS artwork in
  both the inactive and selected states (`mods-tab-icon-active.png`,
  `launcher-tablook-test.txt`).
- Arrivederci is not repacked with a relocation table, and its ISP path is not modeled in QEMU.
- The guest tmpfs at `/tmp_bt` and the `/setting` staging are guest-only development
  policy on disposable overlays, not camera procedures.
