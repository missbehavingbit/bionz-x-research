# Archived README Latest Progress (2026-11-18)

This archive preserves the detailed chronological progress section from `README.md` prior to the 2026-11-18 README restructure.

## Latest progress (as of E0578)

### Reproducible captured QEMU boot inputs ([E0524](../evidence/0524-reproducible-captured-qemu-boot-and-liro-waits.md))

The [QEMU workflow](../../qemu-a6400/README.md) rebuilds its inputs from hash-checked camera dumps without mixing in updater-package libraries or default settings. Two independent preparations have identical output hashes, and the original captures remain unchanged. The native eMMC backing layout and CMD1 sector-mode negotiation are corrected and regression-tested.

Original firmware reaches four cores, LIRO/stella startup and a working shell. Qualified snapshots identify Task 43 waiting on flag 10 (`0x3373`), with its IRQ 101/102 pathways enabled but never asserted by the model. Task 65 is dormant, not a demonstrated sub-micro deadlock. The next step is the actual event-producer contract, not an injected completion. No camera access or emulator GUI frame is claimed.

### Continued QEMU machine work ([E0525](../evidence/0525-qemu-a6400-ulogio-queue-unblocks-appframework.md)–[E0544](../evidence/0544-qemu-a6400-mode-sequencer-waits-at-step-12.md))

The same captured-input boot continues through Appframework, a replayed sub-micro link, and a Sugilite console. Graphic console 0 shows the viewfinder readout (program mark, battery, exposure scale). A disposable overlay that selects device 1 in the firmware display controller activates the original rear-LCD layout on console 0 ([E0572](../evidence/0572-qemu-a6400-rear-lcd-layout-on-panel.md)). An interactive rear panel routes its buttons through the emulated camera inputs ([E0573](../evidence/0573-qemu-a6400-rear-panel-controls.md)). The panel reports when the live output is blank ([E0574](../evidence/0574-qemu-a6400-blank-panel-recovery.md)).

The mod development track has a measured QEMU process and address baseline ([E0575](../evidence/0575-qemu-mod-loader-v2-compatibility-boundary.md)). An offline reload request lets the v2 core replace an enabled mod in one verified step ([E0576](../evidence/0576-mod-loader-v2-reload-request.md)). In QEMU the loader installs in the original `im.elf`, loads the camera MenuTouchFull build through per-library relocation and hot-reloads rebuilt mods in the Sony menu ([E0577](../evidence/0577-qemu-real-mods-relocation-and-hot-reload.md)). The YUV live image remains a separate investigation. Offline firmware analysis and Unicorn verification also map a staged Open Gate (3:2) recording development path ([E0578](../evidence/0578-a6400-open-gate-3-2-recording-feasibility.md)).

### ILCE-7RM4A reference profile ([E0570](../evidence/0570-ilce-7rm4a-offline-sourced-profile.md))

The a7R IVA row of the E0477 platform map is a registered reference profile, sourced from that body's own packaged 1.13 updater. Seven gadget-core symbols come from its own `vmlinux.bin`. Its kernel addresses are not the a6400's, and `probe_interpreter` does not reuse the a6400 inventory or the ILCE-7C manifest for it. No camera was accessed.

### RAM-only Mod Loader ([E0523](../evidence/0523-mod-loader-ok-routing-diagnostics.md))

A reversible 7th `Mods` tab in the camera menu plus an SD-card `.mod` loader, housed in `/libmprctrl.so` RX padding with a writable anchor and three hooks in `viewUnified2.so`, installed through the Mass Storage debugger. It scans `/mnt/mmca1/mods` (and `/media/mmca1/mods`, `/mnt/sd/mods`, `/tmp_bt/mods`) for SMOD v1 `.mod` files, lists up to 12 of them as ON/OFF rows, and toggles a row only after verifying the original hook bytes: it copies the payload and its state block into RAM and writes Thumb trampolines over the three hooks; toggling off restores the original bytes. The loader copies a per-mod state-init blob instead of zero-filling state, and flushes every CPU (`sched_setaffinity` + `cacheflush`) because the camera runs a 4-core SMP kernel. It is fail-closed: an invalid mod is shown as `ERR` and nothing is patched.

Sources: [`mod_loader.c`](../../tools/native_camera/mod_loader.c), [`native_mod_loader_guard.c`](../../tools/native_camera/native_mod_loader_guard.c), [`pack_mod.py`](../../tools/research/pack_mod.py).

The Mod Loader includes [the Mods tab artwork](../evidence/0522-mods-tab-icon-and-uxd-image-container.md). The camera's menu selects inactive, active, and tab-bar focus images through its existing resource properties. Each image has 192-byte rows clipped to the stock tab rectangle.

### MTF2 full-menu touch mod ([E0520](../evidence/0520-mtf2-full-menu-touch-verified-on-a6400.md))

A RAM-only touch layer for the main menu, housed in `/libObj.so` RX padding. Two hooks on the press/release handlers in `/libObj.so` feed the camera's own event queue, so taps and swipes drive the original state machine: tap tabs and page dots, swipe horizontally to page, first tap selects a row and a second tap opens it, and in a popup a tap selects and applies the option without closing, a vertical swipe scrolls, and an outside tap closes only the popup. Verified on a physical ILCE-6400 with all 175 `im.elf` threads resumed; not handled: My Menu (tab 6), the injected Mods tab (7), and tab-bar focus.

Sources: [`menu_touch_full.c`](../../tools/native_camera/menu_touch_full.c), [`native_menu_touch_full_guard.c`](../../tools/native_camera/native_menu_touch_full_guard.c), verified snapshot in [`mods/menu_touch_full/`](../../mods/menu_touch_full/README.md).

### Arrivederci look mod

[Arrivederci](../../mods/arri_isp/arrivederci/README.md) is an SMOD v1/v2 Mods-tab entry for the same loader. The loader calls its Thumb entry with 1 on ON and 0 on OFF when the mod's flag bit 1 is set. It was checked on a live ILCE-6400 on 2026-11-16 with Picture Profile gamma S-Log3 and color mode S-Gamut3.Cine, and on 2026-11-17 under Mod Loader v2 in RAM ([E0555](../evidence/0555-arrivederci-menu-exit-recompute-and-cc8-refill.md), [E0556](../evidence/0556-arrivederci-classic-fitted-matrix.md)).
