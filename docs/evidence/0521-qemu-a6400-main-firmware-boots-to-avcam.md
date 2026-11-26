# E0521 — QEMU `sony-a6400`: main `nflasha3` firmware boots through init, 40 kernel modules and av-cam `entry_main` to an interactive shell

Date: 2026-11-15. **Strictly offline.** No camera access, no USB, no writes to firmware
inputs. The eMMC image is attached through a fresh qcow2 overlay on every run
(`qemu-a6400/data/emmc.img` mtime unchanged, 2026-11-15 02:06).

Labels: [C] confirmed by a run receipt, [I] inference, [A] assumption.

## 0. Short answer

1. **Root cause #1: wrong kernel entry contract (no serial output at all) [C].** QEMU's generic
   `arm_load_kernel()` put the Image at `0x8000` with ATAGS. The Sony kernel runs at its
   `TEXT_OFFSET` (`boot/loadaddr.txt` = `0x00108000,0x00700000,0x00103000`) and reads a
   `"kemco"+cmdline` block from `r2 = 0x00103000` (`setup_arch`: `strncmp(p, "kemco", 5)`, E0425).
   `sony_a6400.c` now has `sony_a6400_load_boot_contract()`: it loads the Image at `0x00108000`, the
   initrd at `0x00700000`, writes `"kemco " + -append` at `0x00103000`, and a CPU reset handler sets
   `r0=0, r1=0xDA9, r2=0x00103000, pc=0x00108000`. The kernel then prints
   `cmdline override at 60103005` / `Machine: ARM-CXD900X0` and boots [C].
2. **Root cause #2: `backup.ko` `file_load` error, cascading into ~20 module failures [C].**
   Disassembly of `/usr/kmod/backup.ko` (`backupfile_init` @`0x4614`, `Data_check` @`0x3e04`):
   it reads the first 0x100 bytes of `/setting/Backup.bin`, requires cookie `0xAAAAAAAA` at +4,
   treats header word 0 as a **physical address** (`arch_phys_to_cache`), and with
   `Backup_load=0` expects the bootloader to have **already copied the whole file there**; then
   `Data_init` checks the cookie in RAM. QEMU RAM at that address was zero, so it returned `-EIO`
   (`init ko_insmod: error(5)`). `Backup.bin` (= `CX62600_UC2.bin`, `241c8521…`) has word 0 =
   `0x02E80000`. Preloading it there (`-device loader,…,addr=0x02E80000,force-raw=on`) makes
   `backup.ko` load, used by 16 modules [C].
3. **Root cause #3: the updater cmdline was used for the main boot [C].**
   `data/nflasha3-kemco.bin` is the updater `kemco.txt` (`memrsv=0x75e000@0x02E68000`, which ends
   exactly at av-cam's `0x035C6000`). With it, the av-cam preload was overwritten before `liro.ko`
   ran (`Magic unmatched 681b9a03 != 4c49524f`). The main-boot cmdline is `/setting/kemco.txt`
   (`memrsv=0x1A5E000@0x02E68000` covers Backup and av-cam, plus `wbimem=`, `cmpr.*`, `alog.*`,
   `mem=256M@0x80000000@1`). `run.sh` uses it, swapping only `console=ttynull` for
   `console=ttyAM0,115200n8 amba2.console=1` [C].
4. **av-cam preload [C].** `/system/av-cam.bin` (`90d515cf…`, 16,674,456 B, `LIRO` magic at +4) is
   copied to `0x035C6000` (`liro.ko adrs=035c6000`). LIRO accepts it and runs `entry_pre` and
   `entry_main` [C].
5. **Prior art check.** `Oein/a5000-emulator` `docs/BOOT.md` documents the same bootloader duties on
   CXD90014 (kemco block, `Backup.bin` to its header word-0 PA for `Backup_load=0`, `av-cam.bin`
   preload for `liro.ko`, real cmdline with a `memrsv` covering both). The a6400 fixes here were
   derived independently from the a6400 binaries and agree with it [C for a6400, reference for a5000].

## 1. Command and inputs

```bash
# WSL2, from qemu-a6400/ (overlay copied into ~/sony-a6400-qemu and rebuilt with ninja)
TIMEOUT=80 ./run.sh   # serial input fed by a timed script: ps, cat /proc/meminfo, ls /proc/udm
```

| Input / output | SHA-256 | Notes |
| --- | --- | --- |
| `qemu-a6400/data/nflasha3-Image` | `d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618` | `Linux 3.0.27_nl-rt106+ … Tue Aug 10 2021` |
| `qemu-a6400/data/nflasha3-initrd` | `db95861f6c56418a216c4805f1f76f2cf2a0e74fcbe0d3350b87cf6296254673` | ext2, `/sbin/init` |
| `qemu-a6400/data/setting.tar` | `5bbc32e11a2096bf1fd8b235afbd02dd4de9a02c3413b0e45ea3413739f30114` | source of `kemco.txt` and `Backup.bin` |
| `Backup.bin` (from `setting.tar`) | `241c8521035bc9ff1edbe66cf3d42d81879a4bef8ff9e21cbfb84530a1446154` | = `0110_backup/SYSASTRA-DSLR/DX2/CX62600_UC2.bin` |
| `av-cam.bin` (`extracted_a6400 … nflasha3_unpacked`) | `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e` | LIRO image |
| `qemu-a6400/run.sh` | `e952e5376ff415f81f44ebfa5cedf51ffd44f43f58409185bf3181a036b9a727` | |
| `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` | `bc595fccbce89ab23cbac7633c4c55787890668be5cd1045251f14e14eaf4e24` | |
| `artifacts/qemu-a6400/e0521/run-sh.log` | `164ad372ef598fe7cfb5c8ae661ebf29215ad65bbf9676576b440b99129ed8f2` | serial transcript (`exit=124`, timeout) |
| `artifacts/qemu-a6400/e0521/backup.dis` | — | `arm-linux-gnueabi-objdump -dr backup.ko` (a6400 `nflasha15`) |

Version mix [C]: the kernel Image is the 2021 build, while the eMMC rootfs/`/usr` are the
2019 `extracted_a6400` partition images (`/version.txt` = `38111_03.2019052701`). Modules load
(same vermagic `3.0.27_nl-rt106+`), but this is not a single-release image set.

## 2. Observed boot (serial `ttyAM0`)

- Kernel: 2 memory banks, 7 `memrsv` areas from the main cmdline, `Memory: 1024MB 256MB` [C].
- `init`: `dnflasha*` (compressed) mounts fail with `ENXIO`, the no-compress fallback mounts
  `/dev/nflasha7` on `/`, `nflasha15` on `/usr`, and vfat `/setting`, `/system`, `/tmp`, `/etc`,
  `/log`, `/lens`, `/cert` [C].
- 40 modules in `lsmod` (`backup`, `dmm`, `upm`, `mcmn_drv`, `ms_drv`, `mmc_drv`, `dma330`, `liro`,
  `hdmi`, `cec`, `grm_gles`, …). Still failing: `osal_ulogio.ko` (`error(1)`, `ulogio.bin`
  missing on `/setting`), `sata_drv.ko`/`msen_drv.ko` (not present) [C].
- `LIRO (liro) rev:4-MUSASHI`, `Param:adrs=035c6000`, `API support list: OSAL, Backup 2`,
  `end entry_pre`, `end entry_main` [C].
- Userland: `stella HeapAllocator`, `DMP_2D_Initialize : 0`, `im.elf` monitor buffer mapped
  (`FixedAreaTop 20098000`), BusyBox `ash` on `ttyAM0` answers commands [C].

## 3. Failures preserved

- First runs with the updater cmdline: `im.elf` NULL fault in `libMWF.so MonQueue::IsFull`
  (exception monitor), and `Magic unmatched 7fff9b54 / 681b9a03 != 4c49524f` before the main
  cmdline was used. Superseded by §0.3 [C].
- Build fixes: QEMU v10.1 `load_image_targphys()` takes 3 arguments [C].

## 4. Assumptions and limits

- The preloads model bootloader behavior; the bootloader itself is not executed [A].
- `0x035C6000` comes from `liro.ko adrs=` as passed by init in this run [C]; it is a constant in
  `run.sh`, not derived from the image [A].
- No display, sensor, sub-micro or timing model is claimed. A shell prompt is not a booted camera
  application.

## 5. Next boundary

- **SMP:** av-cam binds tasks to CPUs 1–3 (`os_thread_bind: cpu N is offline`, tids 2, 3, 17–61,
  65, 124–130). The machine models one Cortex-A5. Next is a 4-core `sony-a6400` with the CXD90045
  secondary-core release contract (a5000 reference: `cxd90014_smp_init`, IRAM `0xC0005010` + SEV;
  the CXD90045 mechanism must be recovered from this kernel).
- Smaller: `/setting/ulogio.bin` absent; eMMC `execute_tuning` warning on every access (927 lines
  per run); compressed `dnflasha*` path (`AL_EMMC_BLK_UZ` info table invalid).
