# E0522 — QEMU `sony-a6400`: four Cortex-A5 cores online, per-CPU CXD4115 tick timers, av-cam task binding succeeds

Date: 2026-11-15. **Strictly offline.** No camera access, no USB, no writes to firmware
inputs (eMMC through a fresh qcow2 overlay). Builds on E0521.

Labels: [C] confirmed by a run receipt or disassembly, [I] inference, [A] assumption.

## 0. Short answer

1. **Secondary-core release contract (kernel disassembly) [C].** Kernel entry `0x60108004`
   calls `0x6011ee00`, a built-in pre-boot routine. With MPIDR≠0 it goes to `secondary_entry`
   (`0x6011ed60`), which enables the GIC CPU interface (`0xF8000100` = 1, PMR `0xF0`), writes `0xFF` to
   SRAM `0xFE00501C + cpu`, and loops on `WFI` until the physical `pen_release` (`0x604d9080`) equals
   its CPU number, then branches to `secondary_startup` (`0x603bfd00`). On CPU0 the routine resets the
   SCU, then `cxd900x0_smp_init` (`0x6011edc8`) stores the physical `cxd900x0_secondary_entry` in the
   SRAM mailboxes `0xFE005010/14/18` (CPU1..3), clears `0xFE00501D..1F`, and issues `SEV`.
   `boot_secondary` (`0x603c08ac`) is the standard pen-release handshake (`write_pen_release`, SGI via
   `gic_raise_softirq`, wait for -1). Core count comes from the SCU (`scu_get_core_count`,
   `(SCU+4 & 3) + 1`). This is the CXD90014 pattern from `Oein/a5000-emulator` (IRAM `0xC0005010`)
   with SRAM at `0xFE000000` [C].
2. **SPI targets must be preset [C].** Sony's `gic_dist_init` (`0x604951c8`) does not write
   `GICD_ITARGETSR`, and `irq_select_affinity` (`0x6011feb0`) only reads the target byte
   (`0xF8001800 + irq`) back into the affinity mask. None of the three CPU0 pre-boot helpers
   (`0x6011f9a4` UART, `0x6011fdec` timer ch1, `0x60120df4` L2C) writes it either. On QEMU's 4-CPU GIC,
   zero targets would drop every SPI.
3. **Per-CPU timer map from the kernel tables [C/I].** Clockevent table `0x604d8d80` (stride 0x60):
   CPU0..3 use channel 0/2/3/4 at `0xF2403000/200/300/400`, IRQ `153 + ch` = 153/155/156/157.
   `hwtimer_devs` (`0x603c9354`) resources: hwtimer0 = `0xF2403500` IRQ 158, hwtimer1 =
   `0xF2403600` IRQ 159, and so on up to ch8 / IRQ 161. hwtimerN serving CPU N is an inference [I] that matches the
   a5000 real-camera GICD dump (158..161 → CPU0..3).
4. **Timer model replaced [C].** The previous `cxd90045_timer` was a free-running 100 Hz IRQ-153 tick
   that ignored programming. It is replaced by `cxd4115_timer.c`, adapted from
   `Oein/a5000-emulator` (GPL-2.0, trace points removed). Channel n → IRQ 153 + n. This kernel's
   protocol matches it: clocksource ch1 CTRL `0x1031` (2 MHz, `sched_clock: … 2000kHz`), periodic
   `0x1102` + CMP 10000, one-shot `0x112`, `set_next_event` CTRL |= `0x1000`, CLR `0x11`.
5. **Machine changes (`sony_a6400.c`) [C].** `max_cpus = default_cpus = 4`. Each CPU's IRQ/FIQ is wired
   to the A9MPCore private block (`num-cpu = ncpu`). Two boot stubs live in the reserved first MiB
   (`memrsv=1M@0`). CPU0 at `0x1000` programs `GICD_ITARGETSR8..63`, sets `r0=0, r1=0xDA9,
   r2=0x103000`, and jumps to `0x108000`. CPU1..3 at `0x1200` loop on `WFE` until their
   `0xFE005010 + 4*(cpu-1)` mailbox is non-zero, then `bx` there. The mailboxes are cleared on reset.
   Stub encodings were assembled with `arm-linux-gnueabi-as` (`artifacts/qemu-a6400/e0522/stubs.S`).
   SPI targets: 153/158 → CPU0, 155/159 → CPU1, 156/160 → CPU2, 157/161 → CPU3, **all other SPIs → CPU0
   [A: no CXD90045 GICD dump yet]**.
6. **Timing [C].** Without `-icount`, av-cam's ~15 µs hwtimer deadlines were already past when armed:
   3,254 `hwtimer_on: counter(...) has caught up expire(...)` lines garbled the console.
   `run.sh` now defaults to `-icount shift=0,sleep=on` (1 ns of virtual time per instruction, idle
   skips ahead). That gives zero such lines [C]. This is a timing assumption, not a cycle model [A].

## 1. Command and receipts

```bash
# WSL2, qemu-a6400/ ; overlay copied into ~/sony-a6400-qemu, ninja qemu-system-arm
TIMEOUT=110 ./run.sh   # timed serial input: cat /proc/cpuinfo, cat /proc/interrupts, ps
```

| Artifact | SHA-256 | Notes |
| --- | --- | --- |
| `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` | `1ad359f2ba44e691b8aa71f650bf7a500bf7343f59117775fdb7424e2d923f86` | |
| `qemu-a6400/qemu-overlay/hw/timer/cxd4115_timer.c` | `a9797e8ed3e9c26c95cfd5645c01665dacdd06e218b025721af45502ee22a951` | adapted from a5000 |
| `qemu-a6400/patches/qemu-v10.1.0-sony-a6400.patch` | `4885578cdd324ef51769434284a2106dee998fb731484fc1bd9d2ac343bf9392` | `CXD4115_TIMER` Kconfig/meson |
| `qemu-a6400/run.sh` | `08748462273d38e7f10dbae39ab9524ebdd105fb128de17d5c080e5aa0039a64` | `-icount` default |
| `artifacts/qemu-a6400/e0522/e0522-run.log` | `307b664be0f8a31b970555a48865e7ffac7a045d55f6da32860c882271ebbda4` | default run, `exit=124` (timeout) |
| `artifacts/qemu-a6400/e0522/smp-no-icount.log` | `c9dc21a21b054303cf5bde4dbeaabd2863a9ccec5e27a6b5f43e61261ec028c7` | preserved failure: hwtimer flood |
| `artifacts/qemu-a6400/e0522/stubs.S` | `af79e8cc3f3ce27a5868b17333b8c6e6b14a94ec50a992e8511df93f714bfa2a` | stub source |
| `artifacts/qemu-a6400/e0521/nflasha3-syms.json` | — | kallsyms of `nflasha3-Image` (`tools.research.decode_kernel_kallsyms`) |

Inputs (kernel, initrd, `setting.tar`, `Backup.bin`, `av-cam.bin`) are unchanged from E0521.

## 2. Observed (default run)

- `CPU1/CPU2/CPU3: Booted secondary processor`, `Brought up 4 CPUs`, `SMP: Total of 4 processors
  activated`; `/proc/cpuinfo` lists processors 0–3 [C].
- `hwtimer cpu0..3 OK`, LIRO `end entry_main` at 0.62 s, no `os_thread_bind: cpu N is offline` [C].
- `/proc/interrupts` after ~33 s guest time: `tick0..3` = 3389/3376/3376/3375, each only on its own
  CPU; `hwtimer0` on CPU0, `hwtimer2` on CPU2; `emmc0`, `dmpgles2`, `xdmac5` and UART on CPU0;
  IPI1/IPI2 on all four CPUs; `Err: 0` [C].
- All `liro-*` IRQs (35–75, …) are registered and still at 0 [C].

## 3. Regression baselines

- `-smp 1` still boots: `Brought up 1 CPUs` and the E0521 `cpu N is offline` binds [C].
- Shell, mounts, 40 modules and `DMP_2D_Initialize : 0` are unchanged from E0521 [C].

## 4. Assumptions and limits

- The CPU0/secondary stubs model boot-ROM/bootloader behavior that is not executed [A].
- Non-timer SPI targets are CPU0 until a CXD90045 `GICD_ITARGETSR` dump exists [A]. A single
  4 KiB read of the distributor page (`0xF8001000`, no read side effects) is within the standing
  read-only camera authorization, but it was not run here.
- `-icount shift=0` fixes virtual time at 1 ns per instruction. It is not the camera's clock or CPI [A].

## 5. Next boundary

- av-cam waits on camera hardware. No `liro-*` interrupt has fired yet. The a5000 analogue
  (`docs/RE_AVCAM_IRQ_BOOT.md`, `RE_SUBMICRO_LINK.md`) points at the sub-micro link (SIO), ADC, and
  front-end units. Recover the CXD90045 equivalents next, checking the a5000 models first.
- Optional verification: read the real `GICD_ITARGETSR` page to replace the CPU0 default.
