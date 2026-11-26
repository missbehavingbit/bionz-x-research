# L2 Full-System Kernel Boot & MMU (`UnicornMachine`)

Consolidated technical reference from `E0415` through [E0656](../evidence/0656-unicorn-main-firmware-boot.md) for booting original `CXD90045` Linux 3.0.27 updater and main kernels under `UnicornMachine`.

## 1. Boot Contract & Memory Layout

- **Raw ARM kernel**: physical load `0x00108000`, `PAGE_OFFSET = 0x60000000`, virtual base `0x60108000`.
- **Machine ID**: `0x00000DA9` (3497, `ARM-CXD900X0`), recovered from `.arch.info.init` (`struct machine_desc`).
- **Initrd & Boot Params**: `initrd_load = 0x00700000`, `boot_params = 0x00103000` (`boot/loadaddr.txt`).
- **RAM Banks**: `(0x00000000, 0x40000000)` (1 GiB) and `(0x80000000, 0x40000000)` (1016 MiB usable via `mem=1016M@0x80000000@1`).
- **Sony `kemco` Override (`E0425`)**: `setup_arch` validates `strncmp("kemco", (char*)0x60103000, 5)`, strips `*`, and parses from `ptr+5`. Standard ATAGS fail this check; `--sony-override` writes the raw `kemco` payload so `init=/bin/ash` or `init=/bin/busybox` is honored.
- **EXBL Bootloader (`E0415`)**: `nflashaB0` contains a `0x200`-byte `EXBL` container (`0x4C425845`) loading the DGU5 loader at `0xFE040000`, entry `0xFE047000`.

## 2. SoC Peripherals & Interrupts (`E0416–E0424`)

- **GICv1**: Distributor `0xF8001000`, CPU interface `0xF8000100` (`GICD_TYPER = 0x1F` for 1024 lines, `ICCIAR`/`ICCEOIR`, spurious `1023`).
- **Timer**: `0xF2403000` (`IRQ 153 / 0x99`, 1 MHz clock, `0x2710` ticks/jiffy = 100 Hz, free-running counter at `+0x10C`).
- **PL011 UART0 (`ttyAM0`)**: `0xF2000000`, PrimeCell IDs `+0xFE0..+0xFFC` (`0x00141011` / `0xB105F00D`), bidirectional RX/TX FIFO.
- **CRCDC Clock (`E0423`)**: `0xF2A31000`, status bit 0 at `+0x30` ready after control write.
- **VFP check (`E0420`, `E0653`)**: The default runner patches the D16 assertion branch at `0x6041FFD8`. `--vfp-d16-mvfr0` instead supplies the measured Cortex-A5 MVFR0 at the kernel's probe. Without that branch patch, the kernel advertises `0x0000E0D7` in `AT_HWCAP`, matching QEMU; Unicorn still executes on Cortex-A9.

## 3. Virtual TLB & Exception Delivery

- **Deferred `UC_TLB_VIRTUAL` (`E0448–E0457`)**: Kernel early boot runs in standard `UC_TLB_CPU` mode through `start_kernel`, `init_section_page_cgroup`, `do_initcalls`, and `cramfs` mount. On the first real user return at `ret_to_user`, the machine switches to `UC_TLB_VIRTUAL` with `UC_HOOK_TLB_FILL` walking `TTBR1` (`swapper_pg_dir` at `0x00104000`) for kernel/vmalloc and `TTBR0` (`current->mm->pgd`, e.g. `0x1DC1C000`) for user VAs.
- **ARMv7 Fault Delivery**:
  - `UC_HOOK_INTR` re-dispatches `intno == 2` (SWI to `0xFFFF0008`), `intno == 3` (Prefetch Abort), and `intno == 4` (Data Abort).
  - CP15 `DFAR`/`DFSR` and `IFAR`/`IFSR` MRC instructions in `v7_early_abort` / `v7_early_pabort` are hooked to inject saved fault registers.
  - `kuser` helpers mapped at `0xFFFF0FA0` (barrier), `0xFFFF0FC0` (cmpxchg), `0xFFFF0FE0` (get_tls), `0xFFFF0FFC` (version).
- **Hook-safe maintenance (`E0533`)**: TTBR0/TTBR1 and fault-register access use Unicorn's CP-register API with the active `SCR.NS` bank. SoftMMU invalidation uses `UC_CTL_TLB_FLUSH`; translated-code invalidation remains separate. These operations must not execute a nested `emu_start`, change CPU mode or overwrite banked SP/LR.
- **Native user continuation (`E0533`)**: the historical `0x0402E798` boundary is covered by a native Thumb regression. The original entry's two four-byte Thumb-2 loads are followed by a two-byte `mov`; the earlier four-byte PC deltas were not sufficient proof of ARM decoding. Fixed-address prologue execution and forced-Thumb invalid-instruction recovery are removed. Unsupported resident instructions stop explicitly in their original mode.
- **Child/COW continuation (`E0653`)**: A live `handle_mm_fault` return hook used an old IFAR as a cache range. Unicorn's address-based TB removal fetch-translated that address in the current process. The hook is now passive; the dirty-PTE return still invalidates SoftMMU and translated code without a per-page fetch.
- **Main-firmware (`nflasha3`) boot (`E0656`)**: Clearing mid-TB `CPSR.IT` on data TLB fills/MMIO callbacks, honoring privileged `AP[2:0]` on `MODULES_VADDR` (`0x5F000000`), isolating `kthread`/`ret_fast_syscall`/`no_work_pending` frames, flushing SoftMMU on `switch_mm`, keeping unmapped `_guest_read` out of prefetch `IFAR`, removing the nested `_pending_user_resume` slice call, delivering `__und_usr`/`__und_svc` (lazy VFP + `SIGILL` probes + Thumb-2 `av-cam.bin` VFP), and completing `CxdSdhciEmmc` `ALFReD` RW1C/`EXT_CSD`/RAM-COW contracts let captured `nflasha3` boot through stock `/sbin/init`, 9 mounts, 39 `.ko` modules, `LIRO` `entry_main`, `im.elf` `DMP_2D_Initialize : 0`, and `/bin/ash` past 2.8B instructions under `--mmio stop`.
- **Current L2 frontier**: Both `nflasha1` updater (`E0653`) and captured `nflasha3` main firmware (`E0656`) reach interactive `/bin/ash` on `UnicornMachine`, with `nflasha3` running stock `/sbin/init`, `LIRO` `entry_main`, and `im.elf` `DMP_2D_Initialize : 0`. Single-core Cortex-A9 execution leaves `av-cam` CPUs 1–3 offline and does not render a Unicorn GUI frame.
