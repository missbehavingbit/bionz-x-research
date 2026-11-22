# ZV-E10 porting work (Sony ZV-E10 / CXD90045, BIONZ X)

**Status:** static analysis and Ghidra RE complete; updater kernel booted offline
(BusyBox shell); **native USB debugger NOT compatible — body onboarding collected.**

> **No camera was ever modified.** Every dump was read-only (on-device service-shell
> pulls + offline analysis). The artifacts stay in the workstation `<workspace>`;
> this branch carries only the derived analysis.

Sony **ZV-E10** (BIONZ X / CXD90045, ARM Thumb-2, Sony CE Linux 8.x). This branch
contains the offline analysis of the body and the artifacts for planning — not yet
running — a port of the a6400's RAM-only touch mod (`MenuTouchFull`) to it.

> The **DSC-HX50V** is a different BIONZ generation (ARMv6, CXD4132-class) and lives
> in the separate `experimental-hx50v` branch. Do not mix the two.

## Quick status

| Item | State |
|---|---|
| Official 2.03 firmware package unpacked | ✅ `FirmwareData_ZVE10V203.dat` (crypter CXD90045, model 0x1030010, region 0) |
| Main kernel (Sony CE Linux 8.2.7.6, `3.0.27_nl-rt106+`, GCC 4.5.1) | ✅ 24,519 symbols decoded |
| On-device read-only dump (service shell) | ✅ `/dev/nflasha` (1.95 GB, 30 partitions), `/dev/nflashaB0` (4 MB **EXBL** "Astra LPDDR3 Initialize Ver.40.00.01", `V85.00.03`), `/dev/nflashaB1` (zeros) |
| Updater kernel boots in Unicorn CXD90045 machine | ✅ reaches BusyBox `/bin/ash`; `uname -a` prints correctly (offline, `zv_e10_boot.py`) |
| Touch-mod RE (viewUnified2.so, libObj.so, CautionConfig.so) | ✅ complete — **RX zero padding exists: ~45 KB @ libObj.so, ~30 KB @ viewUnified2.so**, largest run 16,254 B @ `0x011964ee`; 148-case TBH Action dispatcher (idx75, case 1 = enter, cases 2–5 = move dirs 1/0/3/2); `Action1 0x304114`; `openLevel2Popup` `0x86292c`; `ViewSettingMenu` vtable `0xbaabb0`; `EventManager::push 0x008a1794`; `g_pEvtMgr` .bss `0x014920e0`; `CmnViewSettingNode` vtable `0x00fab960` (CautionConfig.so) |
| Native USB debugger qualification | ❌ **not compatible** — `usb_extcmd.ko` (14,796 B, norm `1083cadd…`) and `libusbcmd.so` (15,996 B) differ from the ILCE-6400/7C pair (norm `9d7c97bd…`) |
| Onboarding bundle for the debugger author | ✅ `ZV-E10-FW2.02-20261116-134044.zip` (drivers + libc + identity; `/proc/*` reads return 0 bytes — Senser file channel cannot read procfs) |
| `CmnViewSettingNode` vtable (CautionConfig.so) | ✅ `0xfab960` (addr pt `0xfab968`); idx48 = `setItemSelectedBySRNum` |

## What is in this branch

| File | What it is |
|---|---|
| `docs/porting/zv-e10/zv-e10-firmware-dump.md` | Firmware unpack + on-device dump analysis: updater/main kernels, rootfs inventory, read-only live-probe transcript, RAW verdict (not enabling RAW), artifacts list |
| `docs/porting/zv-e10/zv-e10-touch-mod-blueprint.md` | Every a6400 absolute address → verified ZV-E10 equivalent, with confidence, effort estimate, loader-injection problem, and what live work would require |
| `docs/porting/zv-e10/zv-e10-viewUnified2-RE-report.md` | Full RE of `viewUnified2.so` (14 MB): `ViewSettingMenu` vtable, 148-case TBH Action dispatcher, `Action1`, move handlers, `PAS_MenuListTab` vtable |
| `docs/porting/zv-e10/zv-e10-libObj-RE-report.md` | Full RE of `libObj.so` (21 MB): `ViewBase::Action/Main`, DefRootView::ActionCheckerListBase, `EventManager::push`, `Event` ctor, `g_pEvtMgr`, `sys_getInstance` |
| `scripts/porting/zv_e10_boot.py` | Boots the extracted updater kernel in the Unicorn CXD90045 machine (E0510-style); offline |
| `scripts/porting/extract_ext4.py` | Extract ext2/3/4 Sony partition images with 1024-byte boot record |
| `scripts/porting/read_fat.py` | List Sony FAT12/16 partition contents |
| `tools/camera_dump_gui.py` | **Fork patch** — new *Body onboarding* tab: `Detect camera` (MSC, read-only) checks the debugger qualification registry; `Collect read-only onboarding bundle` does the author's Senser read-only pass and writes `artifacts/onboarding/<model>-FW<ver>-<ts>.zip`. Native-debugger-qualified bodies (ILCE-6400/7C 2.00) are unchanged |

## The debugger incompatibility (why this matters)

The prebuilt a6400/ILCE-7C native USB debugger payloads are gated on exact
`usb_extcmd.ko` / `libusbcmd.so` matches (byte-identical `libusbcmd.so`,
metadata-equivalent `usb_extcmd.ko`). The ZV-E10's pair matches neither:

- `usb_extcmd.ko`: 14,796 B (ILCE-6400/7C: 14,808 B); normalized `1083cadd…` ≠ `9d7c97bd…`
- `libusbcmd.so`: 15,996 B (ILCE-6400/7C: 15,948 B); sha256 `23b94deb…` ≠ `9fbf3339…`

So the prebuilt payload cannot be deployed safely — its own integrity check would
reject it. Onboarding bundle above gives the author everything needed to decide if
a ZV-E10-specific build is feasible. Until then **this body cannot use the native
USB debugger** — which is exactly what the touch-mod install/deploy path requires.

## How this work was done (safety)

- Official updater package: analyzed offline, never executed — LayoutM-style strings
  like `Uncompressing Linux` were never run on a live body.
- On-device dumps: service-shell read-only pulls (`pull /dev/nflasha`,
  `pull /dev/nflashaB0/B1`); no flash writes, no settings changes, camera verified
  healthy afterwards.
- All RE work ran on the extracted files on a workstation. **Nothing was written to
  any camera.**
- Live work, if ever approved: body-specific reviewed procedure, one step at a time,
  explicit go-ahead per step (see the ILCE-6400 evidence for why the general debugger
  does not transfer).

## What is deliberately NOT in this branch

- Firmware images, flash dumps, bootloaders, updater executables (proprietary)
- Sony service manuals (copyrighted; cited, not redistributed)
- Kernel `kallsyms` dumps (derived artifacts)
- Any a6400-private artifacts beyond what upstream already publishes

## Known limitations

- RX zero padding exists in libObj.so (~45 KB, largest 16,254 B @ `0x011964ee`) and
  viewUnified2.so (~30 KB) — the a6400's injection pattern applies. Whether the
  zeros are dead data cannot be proven without live testing on a camera.
- The ZV-E10's vlog-focused menu layout may not accommodate a 7th "Mods" tab design
  (the a6400's design fits its menu; the ZV-E10 needs a UX rethink)
- Load bases (runtime) are not knowable offline; every offset here is `base + file`
- The onboarding bundle's `/proc/*` files are 0 bytes (Senser file channel cannot
  read procfs) — the driver binaries are what make it useful
