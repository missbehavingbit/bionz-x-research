# E0656 - Unicorn main-firmware (`nflasha3`) boot through stock `/sbin/init`, 39 kernel modules, LIRO `entry_main`, and `im.elf` `DMP_2D_Initialize`

Date: 2026-11-20. **Strictly offline.** Captured `ILCE-6400` main-firmware set
(`artifacts/qemu-a6400/inputs-pr-preflight-20261116-1555`) under `UnicornMachine`
(`--mmio stop`). No camera access, no USB, and no writes to `emmc.img` or any
firmware file on disk (`emmc.img` SHA-256 verified unchanged after all runs).
Follows [E0653](0653-unicorn-updater-child-cow-and-hwcap.md) and
[E0521](0521-qemu-a6400-main-firmware-boots-to-avcam.md).

Labels: `[C]` confirmed by a run receipt or verified binary bytes, `[I]` inference,
`[A]` assumption.

## 1. Summary of verified result

Running the captured `nflasha3` main Linux kernel (`3.0.27_nl-rt106+`,
`init=/sbin/init`) on `UnicornMachine` with `qemu_peripherals=True`,
`vfp_d16_mvfr0=True`, and `--mmio stop` now boots through stock user-space
initialization, kernel-module loading, LIRO RTOS startup, main application
(`im.elf`) startup, and interactive `/bin/ash` commands with zero crashes and
zero unmapped MMIO accesses `[C]`:

1. **Stock `/sbin/init` and partition mounts `[C]`:** The dynamic linker
   (`ld-2.11.2.so`) enters `/sbin/init` `_start` (`0x00008C10`, `CPSR=0x60000030`),
   mounts `/proc`, loads `devno.ko`, `dmac.ko`, `ldec.ko`, and `emmc.ko`
   (`ALFReD v161102p2`), enumerates all 16 `nflasha*` partitions on the 2,040 MiB
   captured eMMC image, falls back cleanly from `dnflasha*` (`ENXIO`) to
   uncompressed mounts, and mounts `/dev/nflasha7` (`/`), `/dev/nflasha15`
   (`/usr`), `/dev/nflasha10` (`/tmp`, `/etc`), `/dev/nflasha3` (`/system`),
   `/dev/nflasha2` (`/setting`), `/dev/nflasha11` (`/log`), `/dev/nflasha18`
   (`/lens`), `/dev/nflasha12` (`/cert`), `/sys`, and `/dev/pts`.
2. **39 live `.ko` modules + `wlanMemoryAllocator` `[C]`:** `/proc/modules` in
   `strict-shell-03` lists 39 live modules across `0x5F000000..0x5F53C000`:
   `devno`, `dmac`, `ldec`, `emmc`, `osal_utm`, `osal_uipc`, `utimer`, `backup`,
   `osal_ulogio`, `input`, `udate`, `indctr`, `i2c`, `sircs`, `stream`, `stream2`,
   `usb_portMonitor`, `usbg_sen`, `usb_extcmd`, `kikilog`, `dmm`, `upm`, `nfc`,
   `grm_ma`, `grm_gles`, `accel`, `mag`, `compass`, `mcmn_drv`, `ms_drv`,
   `mmc_drv`, `dma330`, `codec_drv`, `liro`, `usbg_buspower`, `usb_darwin`,
   `usb_buspwr`, `hdmi`, and `cec`, followed by `wlanMemoryAllocator` buffer
   allocations (`0x608e0000`, `0x68e00000`, `0x68e20000`, `0x608c8000`).
3. **LIRO (`av-cam.bin`) and `/usr/bin/im.elf` startup `[C]`:** `/sbin/init`
   launches `/usr/bin/bootin.elf`, `/usr/bin/net_cmdd.elf`, and
   `/usr/bin/launch_shell.elf` (`/bin/ash`). `bootin.elf` `execve`s
   `/usr/bin/im.elf`, which maps its monitor buffer (`FixedAreaTop : 20098000`,
   `FixedAreaSize : 0007C000`, `Physical Addr : 200EB400`), completes OpenSSL's
   `SIGILL`-guarded capability probes (`_armv7_neon_probe` and `_armv7_tick`),
   loads `liro.ko` (`adrs=035c6000`), runs `LIRO (liro) rev:4-MUSASHI` through
   `start entry_pre(635c6044) -> end entry_pre` and
   `start entry_main(635c6058) -> end entry_main`, prints
   `!!! GLES Vecgen disabled.`, `!!! GPE HW: PSC dummywrite disabled.`, and
   `===== DMP_2D_Initialize : 0`, and continues running cleanly to the 2.0B /
   2.8B instruction bounds (`stop_reason = 'instruction_limit'`, `stop_error = None`).
4. **Interactive shell and reconciled `vfpv3d16` capabilities `[C]`:** Notice
   `nflasha3-Image`'s `__boottime_vfp_init` (`0x60490F0C`) explicitly tests
   `elf_hwcap & 0x4000` (`HWCAP_VFPv3D16`) and calls `BUG()` if absent `[C]`.
   With `vfp_d16_mvfr0=True`, live `/proc/cpuinfo` in `strict-shell-02` reports
   `Features : swp half thumb fastmult vfp edsp vfpv3 vfpv3d16 tls` on
   `Hardware : ARM-CXD900X0`, and `uname -a`, `mount`, and `cat /proc/modules`
   complete over `ttyAM0` after `DMP_2D_Initialize : 0`.

## 2. Nine emulator and peripheral defects resolved

Each defect was isolated on the captured `nflasha3` boot path and covered by
focused unit tests in `tests/emulator/`:

1. **Mid-TB Thumb `ITSTATE` leak (`strict-01` -> `strict-itstate-01`) `[C]`:**
   Unicorn 2.1.4's `unicorn_fill_tlb` and memory hooks invoke
   `cpu_restore_state(..., will_exit=false)` mid-TB, writing `CPSR.IT` into
   `env->condexec_bits`. When an `IT` block (`0x04074E3E: itttt ne` in
   `ld-2.11.2.so`) finished before TB exit (`dc->condexec_mask == 0`), QEMU's
   translator did not emit a zeroing store, leaving stale `ITSTATE` across the
   taken branch to `0x04075346` and skipping four instructions (`lr=0` fault at
   `0x2C`). `_clear_mid_tb_itstate()` clears `CPSR & 0x0600FC00` on handled data
   TLB fills (`access in (16, 17)`, never `UC_MEM_FETCH=18`) and MMIO callbacks.
2. **`MODULES_VADDR` (`0x5F000000..0x60000000`) permissions and SVC `stm` aborts
   (`strict-itstate-01` -> `strict-modules-01`) `[C]`:**
   `_walk_page_tables_phys_with_perms` required `ap10 == 3` for writes, rejecting
   privileged writes to `PAGE_KERNEL_EXEC` (`AP[2:0] = 001`) `.ko` pages, and
   SVC `stm` faults on stale `TTBR0` module PGD entries lacked operand decoding
   and `svc_exit` (`+0x44`) SoftMMU invalidation.
3. **Kernel thread (`kthread`) return clobber (`strict-modules-01` ->
   `strict-kthread-01`) `[C]`:** `_ret_ldmdb_hook` (`0x6010DF58`) skipped
   `ldmdb sp, {r0-r12, sp, lr}^` on `dmac.ko`'s kthread creation (`SPSR=0x93`)
   when `pt_frame_last` held an already-consumed user frame, zeroing `r4..r7`
   before `kernel_thread_helper`.
4. **Multi-page `.ko` demand-paging circuit breaker (`strict-kthread-01` ->
   `strict-emmc-01`) `[C]`:** `_abort_last_pc` keyed only on `pc`
   (`0x60256DF0` in `__copy_from_user`), rejecting the 9th consecutive 4 KiB
   page fault during `ldec.ko`/`emmc.ko` `init_module` with `-EFAULT` (`error(14)`),
   and `_fault_guard_count` never reset after fault-free slices. `_abort_last_pc`
   now keys on `(pc, address & ~0xFFF)` with slice-completion counter resets.
5. **`CxdSdhciEmmc` (`0xF0304000`) `ALFReD` compatibility and read-only COW
   overlay (`strict-emmc-01` -> `strict-mounts-03`) `[C]`:** Added full 32-bit
   RW1C across `0x30..0x33` (`SDHCI_INT_STATUS` + `SDHCI_ERR_INT_STATUS`) for
   8/16/32-bit accesses, `0x1000` vendor register window, `SDHCI_PRESENT_STATE =
   0x01F70000` (`DAT[0]` idle high), QEMU-matched `EXT_CSD` (`PART_CONFIG = 0x08`,
   `BOOT_SIZE_MULT = 32`, `PARTITION_SUPPORT = 0b111`), R1b `CMD6`
   `INT_XFER_COMPLETE`, `CMD21` tuning, `CMD23`, and an in-memory COW sector
   overlay (`_cow_sectors`) for `CMD24`/`CMD25` (`emmc_sectors_written = 2` in
   RAM; `emmc.img` on disk remains `'rb'`).
6. **`ret_fast_syscall` / `no_work_pending` banked-frame isolation and exact
   hook bounds (`strict-mounts-03` -> `strict-uipc-01`) `[C]`:** Guarded
   `_capture_fast_syscall_frame` (`0x6010DEDC`),
   `_restore_fast_syscall_user_return` (`0x6010DEF8`), and `no_work_pending`
   (`0x6010DF48`) with `SPSR` mode checks, `svc_sp` stack-page matching, and
   single-instruction `begin == end` bounds (Unicorn `UC_HOOK_CODE` ranges are
   inclusive).
7. **Multi-process `switch_mm` SoftMMU flush and unmapped Prefetch Abort `IFAR`
   (`strict-uipc-01` -> `strict-app-04`) `[C]`:** Added immediate
   `_flush_cputlb()` when `TTBR0` changes in `cpu_v7_switch_mm` / `MCR TTBR0`
   hooks, and stopped `_guest_read` from falling back to physical `PA == VA`
   when `_mmu_enabled` is active so unmapped user instruction fetches report
   `IFAR = pc` rather than decoded page-cache bytes at `PA = pc` (which had
   killed `bootin.elf` with `SEGV_ACCERR`).
8. **Nested `_pending_user_resume` leak across slices (`strict-app-04` ->
   `strict-app-05`) `[C]`:** `run()` previously executed a nested `emu_start`
   inside `if _resume is not None:` without re-checking `_pending_user_resume`.
   When that nested `emu_start` exited at `_ret_tail_hook` for Task A (`ash`)
   and the next iteration's timer IRQ switched `TTBR0` to Task B (`im.elf`), the
   unconsumed `_pending_user_resume` overwrote `im.elf`'s registers with `ash`'s
   `PC = 0x04094C5C`. Applying `_pending_user_resume` immediately to CPU state
   and continuing the outer loop eliminates the nested `emu_start`.
9. **Architectural `__und_usr` and `__und_svc` delivery (`strict-app-05` ->
   `strict-app-06` -> `strict-app-07`) `[C]`:** Linux 3.0 clears `FPEXC.EN` on
   task switches for lazy VFP context switching, OpenSSL `libcrypto.so` probes
   `_armv7_neon_probe` (`vorr q0, q0, q0` at `0x07DAD7A0`) and `_armv7_tick`
   (`mrrc p15, #1, r0, r1, c14` at `0x07DAD7A6`) expecting `SIGILL` on
   Cortex-A5, and Thumb-2 `av-cam.bin` executes VFP (`0x63CEFF54: vmov.f64 d6, #1.0`)
   in kernel `SVC` mode (`CPSR=0x60000033`), relying on Sony's `bl call_fpe` at
   `__und_svc+0x64` (`0x6010DB44`). `_deliver_undef_exception` delivers raw
   `LR_und = PC + (2 if T else 4)` to `__und_usr` / `__und_svc` (`vector_und` at
   `0x60491200` has zero `lr` correction), servicing 211–325 lazy-VFP and
   `SIGILL` traps cleanly per boot.

## 3. Inputs, receipts, and reproduction

| Artifact / Source | SHA-256 | Notes |
| --- | --- | --- |
| `boot-manifest.json` | `68ff406b8279eeae8c9f9ae2647de3a201d89000df875dba40719ec5e866a6b5` | Prepared `inputs-pr-preflight-20261116-1555` manifest |
| `nflasha3-Image` | `d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618` | `Linux 3.0.27_nl-rt106+ ... Tue Aug 10 13:03:39 JST 2021` |
| `nflasha3-initrd` | `db95861f6c56418a216c4805f1f76f2cf2a0e74fcbe0d3350b87cf6296254673` | Stock ext2 initrd (`/sbin/init`, `ld-2.11.2.so` `bea521ec...`) |
| `Backup.bin` | `241c8521035bc9ff1edbe66cf3d42d81879a4bef8ff9e21cbfb84530a1446154` | Preloaded at `0x02E80000` (`[A]` bootloader contract from `E0521`) |
| `av-cam.bin` | `90d515cfec8b936f1ba9a69dcac462816b880297752a6952474d67c4bba39125e` (manifest `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`) | Preloaded at `0x035C6000` (`liro.ko adrs=035c6000`) |
| `emmc.img` (before & after) | `18e37ec3b3e56d26d31c0b0a4fd91ff10d0d1cb8a23308e49c35abd5cd3cc94b` | Verified byte-identical after all 13 runs |
| `src/a6400/execution/unicorn_machine.py` | `6184ebe3adf591c2d2e18fbc7bf80689c4f63ca9d0d49c8d40b29a5f1074abac` | L2 full-system Unicorn machine |
| `src/a6400/devices/cxd90045_sdhci.py` | `f605534a6ad399a9c228ba70e6e61a5f4650814a577e2b3f748acdacfd85118b` | SDHCI v3 + eMMC model (`0xF0304000`) |
| `strict-app-07/boot.json` | `a7f2d0e685ecd9f82c904a457e54f37b421b8a684a19b6cd3a47423fd9b199ee` | 2.0B hands-off stock boot (`130,653` UART B, `68,181` sectors read) |
| `strict-app-07/ttyAM0.bin` | `a8d3e17bd79a9cd7ba4ac295be87b45d9b2952219a7f2aa473089d777348f5ef` | Raw UART capture through `DMP_2D_Initialize : 0` |
| `strict-shell-02/ttyAM0.bin` | `1c2fdc3e1c220d97f8aa1ad08ccc6b937ecd6f6e9f208bc8757a9b4dd8799a2c` | 2.8B boot + `uname -a`, `/proc/cpuinfo`, `mount` (`140,088` B) |
| `strict-shell-03/ttyAM0.bin` | `999b2912bc39b608467faf757d863383963f5b3e16e08dddc53675eedfd589d2` | 2.8B boot + `uname -a`, `/proc/modules` (`141,048` B) |
| `updater-regression-d16/serial.log` | `8810833a2e182c850ae6439aab495276e439c0dc4748ec4ed967cda95064c600` | `nflasha1` regression (`10,109` B, `COW_DONE`, `cpu_v7_do_idle+0x8`) |

Tool versions: Windows Python `3.15.2`, WSL Reverify-env Python `3.14.4`,
Unicorn `2.1.4`, Capstone `5.0.7`, Reverify `0.11.0` (`16` verified facts in
`artifacts/analysis/unicorn-main-20261119/kernel-byte-ledger.json`, including
`vector_und` `@ 0x389200`, `__und_usr` `@ 0x5D74`, `__und_svc` `@ 0x5B10`, and
`__boottime_vfp_init` `@ 0x388F0C`).

Reproduce from the repository root (WSL recommended for Linux file-cache speed on
the 2 GiB read-only `emmc.img`):

```bash
PYTHONPATH=. /home/administrator/a6400-mcp-tools/20261117/envs/reverify/bin/python \
  artifacts/analysis/unicorn-main-20261119/bounded_replay.py \
  artifacts/analysis/unicorn-main-20261119/NEW-MAIN-REPLAY \
  --limit 2000000000 --mmio stop
```

Offline test validation (`artifacts/analysis/unicorn-main-20261119/full-suite.log`
and `ci-check.log`): `2,182` discovered, `2,178` run, `129` skipped, `0` failures,
`0` errors with `PhysicalVolumeSource` explicitly denied; `ci_check` `5/5 PASS`.
All intermediate failure receipts (`strict-01` through `strict-app-06`, plus
`strict-shell-01` host swap `ENOMEM`) are preserved in
`artifacts/analysis/unicorn-main-20261119/`.

## 4. Retained assumptions and next boundaries

- **Single-core Cortex-A9 execution `[C]`:** `UnicornMachine` models CPU 0 only
  (Cortex-A9 with `MVFR0` D16 boot-probe override). As in single-core QEMU
  `E0521`, `av-cam` logs `os_thread_bind: cpu 1..3 is offline` (`tids 2, 3,
  17–61, 65, 124–130`).
- **Preloaded `Backup.bin` and `av-cam.bin` `[A]`:** Preloading `Backup.bin` at
  `0x02E80000` and `av-cam.bin` at `0x035C6000` models bootloader duties rather
  than executing `EXBL`/`DGU5` directly, and the captured input set mixes the
  2021 `nflasha3-Image` with 2019 user partitions (`E0521`).
- **No Unicorn display/GUI or sensor output claimed `[C]`:** Reaching
  `DMP_2D_Initialize : 0`, 39 loaded `.ko` modules, and an interactive shell on
  `UnicornMachine` verifies kernel, storage, module, RTOS entry, and `im.elf`
  process bring-up, not rear-LCD frame rendering (which requires SMP + VPH/SYSV
  display scanout as in QEMU `E0591`/`E0651`) or physical hardware safety.
