# ZV-E10 firmware + bootloader dump — findings (2026-11-14)

User dumped their Sony **ZV-E10** (serial <serial>, FW 2.02) connected in MSC mode.
Everything was captured read-only; the camera survived one reboot during a service-terminal session (below) and is back in normal mode.

## What was done
1. **Verified identity** — MSC mode, PID `0x0D95`, libusb-win32 bound, FW 2.02, lens `0xa0c139`.
2. **Official updater analysis (offline)** — `Update_ZVE10V203.exe` (343 MB→376 MB, located at `D:\Downloads\Update_ZVE10V203.exe`): Packman EXE with an inverted-MSCF inner CAB (`XOR 0xff` from offset `0x71a24`, 375,877,134 bytes); recovered with `7z l`. Payload `FirmwareData_ZVE10V203.dat` (372,454,920 B) decrypted by fwtool (`crypterCXD90045`, model `0x1030010`, region 0, version 2.03). Complete firmware unpacked:
   - `0400_updater/nflasha1` — updater FAT (loadaddr `0x00108000,0x00700000,0x00103000`); Thumb-2 updater kernel + busybox rootfs; **stripped of kallsyms**.
   - `0700_part_image/nflasha3` — main kernel **Linux 3.0.27_nl-rt106+ PREEMPT RT Jun 5 2026 (Sony CE Linux 8.2.7.6, gcc 4.5.1)**, **24,519 kallsyms decoded** (`zv-e10-vmlinux_kallsyms.json`); stext `0x60108160`.
   - `0700_part_image/nflasha3` also holds `initrd.img`, `av-cam.bin`, `bonobo.bin`, etc.
   - `nflasha5` = WBI warm-boot; `nflasha7`/ `nflasha15` = ext4 system roots (973 + 222 files extracted to `zv-e10-flasha15/`, `zv-e10-flasha7-full/`).
   - `0800_appli` — lens files, `setting`, `tmp`; `1030_genlock` — genlock.
   - Boot PIDs: MSC `0x0D94/0x0D95`, updater mode `0x3E2` (same as HX50V).
3. **Full on-device dump** — via `serviceshell` (the ZV-E10 only exposes the service shell on stock firmware; the v0.18 release has no ZV-E10 updater fdat —"Unknown device", nothing written). Read-only commands:
   - `pull /dev/nflasha` → **`zv-e10-nflasha`, 1,954,545,664 bytes** — SDM partition table `8246/1.00`, 30 partitions (updater 8 MB at `0x10000`, settings/Backup.bin 20 MB at `0x800000`, system image 48 MB at `0x1c00000`, calibration area `CMMEx` at `0x4c00000`, WBI `0x5800000`, empty 16 MB, ext4 8 MB, 500 MB FAT at `0xb800000`, 300 MB ext4 at `0x2d800000`, exFAT 64 MB at `0x50800000`, …). No EXBL exists anywhere in it (verified by full-flash scan).
   - `pull /dev/nflashaB0` → **`zv-e10-bootloaderB0`, 4,194,304 bytes** — the hidden bootloader block devices (extbl-style, not in the SDM table). Starts with **`EXBL`**. Contains Sony's **"Astra LPDDR3 Initialize Ver.40.00.01"**, `V85.00.03`, `(C)2016 Sony Corp. DIG/PD/SD-2`, INFS signature verifier (`INS_E:Sign INFS_SAFE:%X`), DGU manufacturing test handlers, ARM Thumb code. Dual EXBL tables (offsets `0x000` and `0xced4`). Code extends to ~`0x040000`; remainder is zeros.
   - `pull /dev/nflashaB1` → **`zv-e10-bootloaderB1`, 4,194,304 bytes, all zeros** (empty slot — likely the factory/test slot or future use).
4. **Incident** — during the attempt to list `/proc/partitions` via the raw service terminal, the session hung; the camera dropped off USB and ended in a dark/rebooting state. A power-cycle brought it back to normal mode. Nothing was written to flash; the dump files were already written to disk by then and are intact. **The raw manufacturing terminal is fragile — avoid it; use protocol `pull`/`exit` only.**

## GitHub update check (2026-11-16)

`ivica00/sony-a6400-research` was force-updated `8b8313a → ffa974b` (and tag `usb-debugger-binaries-v1` added). Our local clone was rebased to the new tip (nothing else changed; venv intact).

**New milestones / additions:**
- **E0510 (2026-11-14): updater `nflasha1` kernel boots to interactive BusyBox `/bin/ash` on `ttyAM0`** — the updater-kernel boot we attempted for the HX50V.
- **E0521/E0522: out-of-tree QEMU `sony-a6400` machine — main firmware (`nflasha3`) boots on 4 SMP cores; av-cam `entry_main` reached; BusyBox shell.**
- **E0523: RAM-only Mod Loader on a live ILCE-6400 (reversible, no flash writes).**
- `TargetProfile` registry now holds **9 bodies incl. `zv_e10`** (plus ilce_6400, ilce_6400a, ilce_6100, ilce_6600, ilce_7m3, ilce_7c); **13 bodies / 9 kernels mapped**. ZV-E10 = `STATUS_REFERENCE` (UVC chain pinned; not yet an execution target).
- `src/a6400/targets/catalog.py`, `profile.py` (`TargetProfile`, `BootLayoutSpec`); `usb_debugger_compat.py` + `tools/research/onboard_debugger_bundle.py` (unknown-body onboarding); tag `usb-debugger-binaries-v1` = full source snapshot.
- Test suite ≈1903 tests (per E0523 sync commit). README headline: updater kernel boots to serial console/root fs; full camera boot not yet reached.

**Cross-validation of this dump:** our independently decoded 2.03 `vmlinux.bin` kallsyms match the repo's ZV-E10 profile exactly (`usb_gadgetcore_register_driver = 0x602BCAAC`, `startFuncDrv_fromIfClasses = 0x602BD2EC`). The repo's ZV-E10 profile is built from the official 2.03 package; ours is the same bytes — the profile + our dump are mutually consistent. Note the repo's ZV-E10 work is **UVC/reference analysis** (E0316-E0346), not an emulator boot milestone; the updater boot (E0510) and QEMU main-firmware boot (E0521) are ILCE-6400-specific. A ZV-E10 updater/main boot in the emulator is unattempted. Full ZV-E10 schematics remain gated (see §Schematics).

## Emulator boot (2026-11-16, Unicorn CXD90045 machine)

The **ZV-E10 updater kernel (`nflasha1`, v2.03) boots fully in the a6400's UnicornMachine** — the same offline machine used for the a6400's E0510 milestone, now pushed to the ZV-E10's own updater. Two labelled bring-up patches were required (symbol-free): (1) rewrite the baked-in `init=/sbin/init` CONFIG_CMDLINE token in the decompressed image to `init=/bin/ash` (the updater is stripped of kallsyms); (2) `bne`→`b` at `0x60421fd8` (the `vfp_init` VFP-is-not-D16 `BUG()` branch — the identical class of bug the a6400 needed E0510's `__boottime_vfp_init` patch for).

**Verified on serial (`ttyAM0`, PL011 @ 0xf2000000, IRQ 150):** ARM-CXD900X0 machine, all 4 CPUs, full memory init, initrd/cramfs rootfs mount, Sony USB gadget code (`sony_usb_init: CXD4132`, `usb_gcw_register_driver: gcore prototype for Astra`, gadget registration fails harmlessly with ENODEV as expected), and **spawned `BusyBox v1.29.3` in user mode (20 user-mode entries)**. `uname -a` executed and printed: `Linux (none) 3.0.27_nl-rt106 #2 SMP PREEMPT RT Thu Dec 25 17:17:03 JST 2025 armv7l GNU/Linux`.

**Where it stops:** `do_page_fault+0x108` (PC `0x60116e9c`, CPSR `0x60000053` FIQ, LR `0x6012c214`) with an abort the machine cannot deliver/complete — the identical class of low-level abort-handling blocker the a6400 had before E0510's kuser COW/context fixes. The emulated kuser high-vector page (0xFFFF0000–0xFFFF1000) is hooked, but the exact fault semantics for this kernel build are unresolved. This is a solvable problem with the same techniques used on the a6400, but it was not resolved in this session; BusyBox executing `uname -a` is the milestone reached.

Full serial log: `zv_e10_boot/serial.log` (10,099 B). Boot report: `zv_e10_boot/boot-report.json` (`user_mode_entries = 20`, `unmapped = []`, `stub_hits = {0xf800277c: 1}`, `stop_reason = unicorn_error` at `0x60116e9c`). Driver: `zv_e10_boot.py` (read-only; firmware untouched). The ZV-E10 is the third kernel family shown to boot to a shell in this emulator lineage, with two command-support differences from a6400 E0510 (shell reached, one command executed).

## Schematics (2026-11-14)

**The official note exists but I could not pull it automatically.** `SONY ZV-E10/E10L SERVICE MANUAL, Ver. 1.0 (2023.12), Sony doc 989690961, ©2023.12` — 135 pages, **43.7 MB** — hosted as a free public document on **iFixit**: https://www.ifixit.com/Document/KicJdJe4NKagGBlQ/Sony-ZV-E10-E10L-Service-Manual.pdf (documentid 2572676; API: `https://www.ifixit.com/api/2.0/documents/KicJdJe4NKagGBlQ`). Page-1 preview image (`zv-e10-service_docs/page1-thumbnail.png`) shows the genuine Sony cover ("ZV-E10/E10L SERVICE MANUAL ... DIGITAL STILL CAMERA", model table ZV-E10 / ZV-E10L, sections SERVICE NOTE / ASSEMBLY / FRAME SCHEMATIC DIAGRAMS).

**Why not downloaded:** the raw PDF is behind iFixit's browser session — direct downloads return 403 / "connection closed" (server closes the connection for any non-browser request; cookies didn't help). Sony pages are bot-blocked (403 everywhere). `eserviceinfo.com` and the Elektrotanya links for it are Cloudflare/free-account gated. No public mirror was found (manuals.plus / device.report / B&H / sony.jp all carry only the *user* manual / brochures).

**To get it (30 seconds, free):** open the iFixit link above in a browser and click **Download** — it serves to real browsers without an account. Or register a free account on `eserviceinfo.com` / `elektrotanya.com` and search "ZV-E10".

Once the PDF is local it can be processed exactly like the HX50V note (text extraction, block diagrams, SoC ball map, UART/JTAG/debug pads). Expect the same structure as the HX50V note: overall block diagram, power block diagram, SY-* board sheets (SoC marking will tell us the BIONZ-X silicon family — same CXD90045 platform as the a6400), parts list and board layouts.

## Artifacts
- `zv-e10-nflasha` — 1.95 GB raw device dump (256 MB-class partitions ++)
- `zv-e10-bootloaderB0`, `zv-e10-bootloaderB1` — 4 MB each (EXBL)
- `zv-e10-bootloaderB0_unpacked/` (fwtool: 0 files — ZV-E10 EXBL has no filenames; it is a raw component image)
- `zv-e10-v203-unpacked/` — complete offline firmware tree (config.yaml: crypterCXD90045, model 0x1030010, region 0, version 2.03)
- `zv-e10-vlinux_kallsyms.json` — 24,519 symbols (main kernel)
- `zv-e10-updater_kallsyms.json` — updater kernel: no kallsyms (stripped)
- `zv-e10-flasha7/`, `zv-e10-flasha7-full/`, `zv-e10-flasha15/` — extracted ext4 trees
- Logs: `zv_e10_service_log.txt` (dump), `zv_e10_service_log2/3/4/5.txt` (sessions), `zv_e10_dump_log.txt` (failed updatershell attempt)

## What the bootloader is
EXBL = Sony's first-stage loader for BIONZ X. B0 layout: `00000000 EXBL` + descriptor table (six 0x40-byte records at 0x040… offsets `0x38/0x04/0x18/0x13c/0x140/0x138` — likely flash offsets/counts), then the real code from `0x8000` (Thumb, `Astra LPDDR3 Initialize Ver.40.00.01`, `V85.00.03`, `MIS for DGU4`, INFS signature verification, DGU wake/jump commands, `fail_stop_mode`). Primary table at 0x000, backup table at 0xced4. B1 is all zeros.
