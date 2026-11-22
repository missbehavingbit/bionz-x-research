"""Minimal Unicorn ARM machine for the CXD90045 updater boot (offline, E0416).

QEMU is not available on this host (checked during E0416), so the L2 bring-up uses a
small, explicit Unicorn machine model built from observed facts:

* RAM identity-mapped at the observed physical banks ``0x00000000 + 0x40000000`` and
  ``0x80000000 + 0x40000000`` (nflasha1 ``boot/kemco.txt``: ``mem=1G@0`` and
  ``mem=1016M@0x80000000@1``);
* the raw ARM kernel (zImage gzip payload decompressed offline) at ``0x00108000``,
  the cramfs initramfs at ``0x00700000`` and the reconstructed ATAGS at ``0x00103000``
  (nflasha1 ``boot/loadaddr.txt``);
* boot registers r0 = 0, r1 = recovered machine ID (``0x00000DA9``), r2 = ATAGS,
  CPSR = SVC with IRQ/FIQ masked and an opt-in async-abort mask at entry.
  MMU off (the kernel enables its own page tables,
  which Unicorn translates);
* PL011 UART0 at ``0xF2000000``: writes to DR are captured as the ``ttyAM0`` serial
  log, FR reads report TXFE|RXFE, DR reads are 0; every access is counted;
* MMIO windows are mapped as RAM so accesses reach the hooks; a small explicit
  ``mmio_read_stubs`` table serves *labelled* ready values for registers whose
  hardware semantics are not modelled (all hits are reported);
* any unmapped access stops the run with PC/address/registers — never silently
  replaced by zero.

This is a partial machine model: it is not a booted camera, not a timing model and
every stubbed register is listed in the run report.
"""
from __future__ import annotations

import json
import struct
from collections import Counter

from unicorn import (UC_ARCH_ARM, UC_CTL_IO_WRITE, UC_CTL_TLB_FLUSH,
                     UC_HOOK_CODE, UC_HOOK_INTR,
                     UC_HOOK_MEM_FETCH_PROT, UC_HOOK_MEM_READ,
                     UC_HOOK_MEM_READ_PROT, UC_HOOK_MEM_READ_UNMAPPED,
                     UC_HOOK_MEM_UNMAPPED, UC_HOOK_MEM_WRITE,
                     UC_HOOK_MEM_WRITE_PROT, UC_HOOK_MEM_WRITE_UNMAPPED,
                     UC_MODE_ARM, Uc)
from unicorn.arm_const import (UC_ARM_REG_C13_C0_3, UC_ARM_REG_CPSR,
                               UC_ARM_REG_LR, UC_ARM_REG_PC,
                               UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                               UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5,
                               UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8,
                               UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
                               UC_ARM_REG_FPEXC, UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_SPSR)

UART0 = 0xF2000000
PL011_DR = 0x00
PL011_FR = 0x18
PL011_FR_TXFE = 0x80
PL011_FR_RXFE = 0x10
PL011_IMSC = 0x38
PL011_RIS = 0x3C
PL011_MIS = 0x40
PL011_ICR = 0x44

KERNEL_LOAD = 0x00108000
INITRD_LOAD = 0x00700000
BOOT_PARAMS = 0x00103000

#: Recovered in E0416 from the machine_desc/init code.
GIC_DIST = 0xF8001000
GIC_CPU = 0xF8000100
TIMER_BASE = 0xF2403000
TIMER_COUNTER_OFFSET = 0x10C
TIMER_IRQ = 153  # 0x99: ext_timer_setup setup_irq(irq = context[4] + 0x99)
UART_IRQ = 150   # ttyAM0 PL011 UART0 interrupt
TIMER_HZ = 100  # clock_event period 0x2710 on a 1 MHz timer
TIMER_TICKS_PER_JIFFY = 0x2710
VECTORS_BASE = 0xFFFF0000
CRCDC_BASE = 0xF2A31000
#: CXD90045 Sugilite GPU BAR0 and clock/reset block (verified in grm_gles.ko E0510).
SUGILITE_BASE = 0xF1000000
SUGILITE_SIZE = 0x4000
SUGILITE_IRQ = 76
SUGILITE_CLK_BASE = 0xF3400000
SUGILITE_CLK_SIZE = 0x1000
# ARM PL011 PrimeCell identification registers (+0xFE0..+0xFFC).
PL011_ID_REGS = {
    0xFE0: 0x11, 0xFE4: 0x10, 0xFE8: 0x14, 0xFEC: 0x00,   # 0x00141011
    0xFF0: 0x0D, 0xFF4: 0xF0, 0xFF8: 0x05, 0xFFC: 0xB1,   # 0xB105F00D
}
CRCDC_STATUS = 0xF2A31030

GIC_ICCIAR = 0x0C
GIC_ICCEOIR = 0x10
GIC_ICCICR = 0x00
GIC_ICCPMR = 0x04
GIC_SPURIOUS = 1023

DEFAULT_RAM_BANKS = ((0x00000000, 0x40000000), (0x80000000, 0x40000000))
DEFAULT_MMIO_RANGES = ((0xF0000000, 0xFDFFFFFF), (0xFE100000, 0xFFFFFFFF))
DEFAULT_SRAM = (0xFE000000, 0x00100000)
DEFAULT_STUBS = {0xF800277C: 0x100}  # observed early clock/power status poll


_R_REG = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
          UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
          UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
          UC_ARM_REG_R12]


class MachineError(RuntimeError):
    pass


class UnicornMachine:
    """Bounded Unicorn machine model for the updater Linux bring-up."""

    @classmethod
    def from_target(cls, profile, kernel_raw: bytes, initrd: bytes, atags: bytes, *,
                    machine_id: int | None = None,
                    symbols: dict[str, int] | None = None,
                    **kwargs) -> 'UnicornMachine':
        """Construct a ``UnicornMachine`` configured from a ``TargetProfile``.

        Boot layout parameters (``machine_id``, ``kernel_load``, ``initrd_load``,
        ``boot_params``, ``ram_banks``, ``uart_base``) are taken from
        ``profile.boot_layout`` when present, and ``profile.kallsyms`` is merged
        as the base symbol table before applying any decoded ``symbols``.
        """
        layout = getattr(profile, 'boot_layout', None)
        resolved_machine_id = machine_id if machine_id is not None else (
            layout.machine_id if layout is not None else None
        )
        if not resolved_machine_id:
            raise MachineError(
                f'target {profile.target_id!r} has no boot_layout.machine_id; pass machine_id explicitly'
            )
        merged_symbols = {
            name: addr for name, addr in (getattr(profile, 'kallsyms', None) or {}).items()
            if addr is not None
        }
        if symbols:
            merged_symbols.update(symbols)
        if layout is not None:
            kwargs.setdefault('kernel_load', layout.kernel_load)
            kwargs.setdefault('initrd_load', layout.initrd_load)
            kwargs.setdefault('boot_params', layout.boot_params)
            kwargs.setdefault('ram_banks', layout.ram_banks)
            kwargs.setdefault('uart_base', layout.uart_base)
        return cls(
            kernel_raw,
            initrd,
            atags,
            machine_id=resolved_machine_id,
            symbols=merged_symbols,
            target_id=profile.target_id,
            **kwargs,
        )

    def __init__(self, kernel_raw: bytes, initrd: bytes, atags: bytes, *,
                 machine_id: int, kernel_load: int = KERNEL_LOAD,
                 initrd_load: int = INITRD_LOAD, boot_params: int = BOOT_PARAMS,
                 ram_banks=DEFAULT_RAM_BANKS, mmio_ranges=DEFAULT_MMIO_RANGES,
                 sram=DEFAULT_SRAM, uart_base: int = UART0,
                 mmio_read_stubs: dict[int, int] | None = None,
                 timer_irq_model: bool = True, virtual_tlb_enabled: bool = False,
                 virtual_tlb_on_userspace: bool = False,
                 enable_svc_dispatch: bool = False,
                 timer_ticks_per_instruction: int = 1,
                 timer_ticks_per_jiffy: int = TIMER_TICKS_PER_JIFFY,
                 timer_irq_start_instructions: int = 5_000_000,
                 timer_irq: int = TIMER_IRQ,
                 vectors_base: int = VECTORS_BASE,
                 unmapped_mmio_policy: str = 'stop',
                 exclusive_fallback: bool = True,
                 exclusive_patch: bool = False,
                 data_abort_delivery: bool = True,
                 data_abort_limit: int = 65536,
                 data_abort_fsr: int = 0x5,
                 ex_table_fixup: bool = True,
                 probe_write_ranges: tuple = (),
                 serial_input: bytes = b'',
                 boot_params_payload: bytes | None = None,
                 trace_user_mode: bool = False,
                 trace_regs: dict | None = None,
                 trace_printk_at: int | None = None,
                 trace_points: dict | None = None,
                 instruction_limit: int = 40_000_000,
                 sample_interval: int = 250_000,
                 use_code_hook: bool = False,
                 boot_patches: dict | None = None,
                 symbols: dict[str, int] | None = None,
                 target_id: str | None = None,
                 mask_async_abort_at_entry: bool = False,
                 mask_async_abort_on_irq: bool = False,
                 allow_nonsecure_af_writes: bool = False,
                 vfp_d16_mvfr0: bool = False,
                 qemu_cpu_parity: bool = False,
                 qemu_peripherals: bool = False,
                 oracle_mmio_trace: dict[int, list[int]] | None = None,
                 emmc_image=None):
        if qemu_cpu_parity:
            mask_async_abort_at_entry = True
            mask_async_abort_on_irq = True
            allow_nonsecure_af_writes = True
            vfp_d16_mvfr0 = True
        if int.from_bytes(kernel_raw[0:4], 'little') != 0xE321F0D3:
            raise MachineError('kernel_raw does not start with the ARM kernel head')
        if not machine_id:
            raise MachineError('machine ID required')
        if instruction_limit <= 0:
            raise MachineError('positive instruction limit required')
        self.target_id = target_id
        self.kernel_raw = kernel_raw
        self.initrd = initrd
        self.atags = atags
        self.machine_id = machine_id
        self.entry_cpsr = 0xD3 | (0x100 if mask_async_abort_at_entry else 0)
        self.mask_async_abort_on_irq = mask_async_abort_on_irq
        self.vfp_d16_mvfr0 = vfp_d16_mvfr0
        self.vfp_mvfr0_d16_hits = 0
        self.vfp_mvfr0_sites: list[str] = []
        self.qemu_peripherals = qemu_peripherals
        self.emmc_image = emmc_image
        self._cxd_bus = None
        self._oracle_mmio_queues: dict[int, list[int]] = {
            int(addr): list(vals) for addr, vals in (oracle_mmio_trace or {}).items() if vals
        }
        self._oracle_mmio_last: dict[int, int] = {}
        self.oracle_mmio_hits: Counter = Counter()
        self.kernel_load = kernel_load
        self.initrd_load = initrd_load
        self.boot_params = boot_params
        self.uart_base = uart_base
        self.stubs = dict(mmio_read_stubs if mmio_read_stubs is not None else DEFAULT_STUBS)
        self.instruction_limit = instruction_limit
        self.sample_interval = sample_interval
        self.slice_size = sample_interval if sample_interval > 0 else 250_000
        self.progress_every = 0
        self.use_code_hook = use_code_hook
        # One-shot runner patches: {pc: [(virtual_address, size, value), ...]}.  They
        # let a bring-up experiment pre-seed runtime variables (e.g. the observed
        # ``lpj`` value) without modifying the kernel image bytes; every patch hit is
        # counted and reported.
        self.boot_patches = dict(boot_patches or {})
        self.patch_hits: Counter = Counter()
        # Timer/GIC model state (E0417).
        self.timer_irq_model = timer_irq_model
        self.virtual_tlb_enabled = virtual_tlb_enabled
        self.virtual_tlb_on_userspace = virtual_tlb_on_userspace
        self.enable_svc_dispatch = enable_svc_dispatch
        self._svc_hook_installed = False
        self._vtlb_requested = False
        self._user_refresh_requested = False
        self._user_refresh_done = False
        self._activation_pgd = None
        self.pt_regs_frames: list[dict] = []
        self.pt_regs_user_frames: list[dict] = []
        self.ret_tail_hits = 0
        self.ret_tail_trace: list[dict] = []
        self.ret_user_returns: list[dict] = []
        self.pending_user_spsr = None
        self.pending_user_lr = None
        self.user_restores = 0
        self.user_entry_trace: list[dict] = []
        self._pending_user_resume = None
        self.insn_invalid_recoveries = 0
        self.insn_invalid_unsupported: list[dict] = []
        self._step_last_error = None
        self.e0475_trace: list[dict] = []
        self.e0483_trace: list[dict] = []
        self.e0483_entry_stack: dict = {}
        self.pt_frame_gen = 0
        self.pt_frame_last = None
        self.pt_frame_consumed = -1
        self.e0492_frames: list[dict] = []
        self.e0492_msr: list[dict] = []
        self.e0493_trace: list[dict] = []
        self.e0495_trace: list[dict] = []
        self.e0496_kernel: list[dict] = []
        self.e0497_svc = None
        self.e0497_trace: list[dict] = []
        self.e0499_trace: list[dict] = []
        self.e0500_frames: list[dict] = []
        self.e0504_trace: list[dict] = []
        self.timer_ticks_per_instruction = timer_ticks_per_instruction
        self.timer_ticks_per_jiffy = timer_ticks_per_jiffy
        self.timer_irq_start_instructions = timer_irq_start_instructions
        self._instructions_executed = 0
        self.timer_irq = timer_irq
        self.vectors_base = vectors_base
        self.unmapped_mmio_policy = unmapped_mmio_policy
        # Unicorn's ARM target raises UC_ERR_EXCEPTION on the ARMv7 exclusive
        # instructions (LDREX/STREX/DMB/CLREX).  A small local exclusive-monitor
        # model emulates them so the real kernel can keep running; every emulated
        # instruction is counted in the report.
        self.exclusive_fallback = exclusive_fallback
        self.exclusive_patch = exclusive_patch
        self.exclusive_patched: dict = {}
        # ARMv7 data-abort delivery (E0419).
        self.data_abort_delivery = data_abort_delivery
        self.data_abort_limit = data_abort_limit
        self.data_abort_fsr = data_abort_fsr
        # Direct __ex_table fixup application (E0420): a faulting kernel PC
        # with an exception-table entry jumps to its fixup, matching the
        # kernel's native fixup semantics without relying on CP15 DFAR/DFSR
        # visibility in the emulated abort entry.
        self.ex_table_fixup = ex_table_fixup
        self.probe_write_ranges = tuple(probe_write_ranges)
        self.ex_table: dict = {}
        self.ex_table_fixups = 0
        self.ex_table_trace: list[dict] = []
        self.data_aborts_delivered = 0
        self.direct_map_completions = 0
        self.data_abort_trace: list[dict] = []
        self._abort_last_pc = None
        self._abort_same_pc = 0
        self.undef_exceptions_delivered = 0
        self.undef_exception_trace: list[dict] = []
        self._undef_last_key = None
        self._undef_same_pc = 0
        self._exclusive_monitor: tuple[int, int] | None = None
        self._exclusive_cache: dict = {}
        self.exclusive_emulated = 0
        self.exclusive_trace: list[dict] = []
        self._timer_counter = 0
        self._next_jiffy_tick = timer_ticks_per_jiffy
        self._timer_irq_pending = 0
        self._soft_pending_irqs: set[int] = set()
        self._edge_pending: set[int] = set()
        self._gic_active_irq = None
        self._gic_iar_served = False
        self._gic_regs: dict[tuple[str, int], int] = {
            # Bootloader pre-routes all SPIs (IRQ >= 32, GICD_ITARGETSR 0x820..0xBFF)
            # to CPU0; Sony's gic_dist_init only toggles GICD_CTLR without setting
            # ITARGETSR (cross-checked with Oein/a5000-emulator docs/HARDWARE.md).
            ('dist', offset): 0x01010101
            for offset in range(0x820, 0xC00, 4)
        }
        self._timer_regs: dict[int, int] = {}
        from src.a6400.execution.dmp_p3d import SugiliteMmioModel
        self._sugilite_clk_regs: dict[int, int] = {}
        self._sugilite_mmio = SugiliteMmioModel()
        self._gic_enabled = False
        self._jiffies_injected = 0
        self._timer_counter_reads = 0
        self._gic_iar_reads = 0
        self._gic_iar_pcs: list[str] = []
        self._injection_pcs: list[dict] = []
        self._gic_eoir_writes = 0
        self._unmapped_mmio_mapped: list[int] = []
        # E0421: per-MMIO-address byte stream probe to identify the live
        # console UART data register without guessing its base address.
        self.mmio_write_bytes: dict[int, bytearray] = {}
        # Bidirectional PL011: RX FIFO for injected console input.
        self.serial_input = bytearray(serial_input)
        self.boot_params_payload = boot_params_payload
        self.trace_user_mode = trace_user_mode
        self.trace_regs = trace_regs or {}
        self.trace_reg_values: dict[int, list] = {}
        self.user_mode_entries: list[dict] = []
        # E0429: explicit per-mode SP banking (Unicorn shares R13 across
        # modes) and prefetch-abort support.
        self.mode_sp: dict[str, int | None] = {'svc': None, 'irq': None,
                                              'abt': None, 'usr': None}
        self.tls_value = 0
        self.do_page_fault_frames: list[dict] = []
        self.capture_page_fault = False
        self.svc_hits = 0
        self.svc_trace: list[dict] = []
        self.kuser_hits = 0
        self.kuser_entries: list[dict] = []
        self.mode_lr: dict[str, int | None] = {'svc': None, 'irq': None,
                                              'abt': None, 'usr': None}
        self.user_sp: int | None = None
        self._restore_user_sp = False
        self._pending_exception = ''
        self.last_ifar = 0
        self.last_ifsr = 0
        self.last_dfar = 0
        self.last_dfsr = 0
        self.pabt_mrc_hits = 0
        self.dabt_mrc_hits = 0
        self._tlb_flushes = 0
        self._svc_stack_refreshes = 0
        self._last_thread_info = 0
        self._last_thread_task = 0
        self.stack_delivery_trace: list[dict] = []
        self.max_same_fault_repeats = 8
        self._fault_guard_key = None
        self._fault_guard_count = 0
        self._last_unmapped = None
        self._last_unmapped_write = False
        self._last_tlb_fault: tuple[int, int] | None = None
        self.live_fault_dumps: list[dict] = []
        self.pte_rewalks: list[dict] = []
        self.cputlb_flushes = 0
        self._last_fault_page = None
        self.virtual_tlb = False
        self.virtual_tlb_error = ''
        self._mmu_enabled = False
        self._cached_ttbr0 = None
        self._cached_ttbr1 = None
        self.switch_mm_hooked = False
        self.switch_mm_hits = 0
        self.ttbr0_write_sites: list[int] = []
        self.ttbr0_write_hits = 0
        self.switch_mm_values: list[int] = []
        self._last_switch_mm_pgd = None
        self.mm_resolve_trace: list[dict] = []
        self.tlb_fills = 0
        self.tlb_fill_misses = 0
        self.tlb_fill_samples: list[dict] = []
        self.mid_tb_itstate_clears = 0




        self.fault_classification_trace: list[dict] = []

        self._pte_dirty = False
        self.mm_fault_returns: list[dict] = []
        self._page_cache_drops = 0
        self._pending_evict_pages = set()
        self._pending_cputlb_flush = False
        self.e0502_fill: list[dict] = []
        self._fast_frame = None




        self.banked_store_hits = 0
        self.banked_restore_hits = 0
        self.prefetch_aborts_delivered = 0
        self.prefetch_abort_trace: list[dict] = []
        # E0428: user-access helpers whose faults must be delivered to the
        # kernel do_DataAbort (handle_mm_fault) instead of being fixed up
        # directly; the exec BSS/stack demand-paging path runs through them.
        self.demand_page_helpers = {
            '__clear_user_std', '__copy_to_user_std', '__copy_from_user_std',
            '__clear_user', '__copy_to_user', '__copy_from_user',
            'strncpy_from_user', 'strnlen_user', 'copy_from_user',
            'copy_to_user', '__get_user', '__put_user',
            '__strncpy_from_user', '__strnlen_user',
            '__get_user_1', '__get_user_2', '__get_user_4',
            '__put_user_1', '__put_user_2', '__put_user_4', '__put_user_8',
            'csum_partial_copy_from_user', 'copy_siginfo_to_user',
            'copy_mount_options', 'file_read_actor', 'filldir', 'filldir64',
            'iov_fault_in_pages_read', 'iov_iter_fault_in_readable',
            'pipe_read', 'pipe_to_user', 'sys_getdents64', 'sys_sigaction',
            'do_sigaltstack', 'do_signal', 'setup_sigframe',
            'restore_sigframe', 'setup_return', 'do_futex',
            'do_sys_poll', 'sys_pselect6', 'v7_coherent_user_range',
            'vfp_preserve_user_clear_hwstate', 'vfp_restore_user_hwstate',
            'padzero', 'copy_strings', 'copy_strings_kernel',
            'create_elf_tables', 'load_elf_binary'}
        self._uart_regs: dict[int, int] = {}
        self._crcdc_regs: dict[int, int] = {}
        self.trace_printk_at = trace_printk_at
        self.trace_points = dict(trace_points or {})
        self.trace_point_hits: list[dict] = []
        self.printk_trace: list[dict] = []
        self.symbols = symbols or {}
        self._symbol_addresses = sorted(self.symbols.values())
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        # Cortex-A9 supplies ARMv7 and the MMU, but is not the Sony D16 CPU.
        try:
            import unicorn.arm_const as arm_const
            self.uc.ctl_set_cpu_model(arm_const.UC_CPU_ARM_CORTEX_A9)
            self.cpu_model = 'cortex-a9'
        except Exception:
            self.cpu_model = 'default'
        self.entry_scr = None
        if allow_nonsecure_af_writes:
            # E0559: the QEMU reference disables EL3. On Unicorn's A9,
            # enable just the non-secure CPSR.A/F writes via SCR.AW/FW.
            if self.cpu_model != 'cortex-a9':
                raise MachineError('non-secure A/F writes require the Cortex-A9 model')
            scr_register = (15, 0, 1, 1, 0, 3, False)
            if self.uc.cpr_read(*scr_register) != 0x01:
                raise MachineError('non-secure A/F writes require reset SCR=0x01')
            self.uc.cpr_write(*scr_register, 0x31)
            self.entry_scr = self.uc.cpr_read(*scr_register)
            if self.entry_scr != 0x31:
                raise MachineError('SCR.AW/FW readback did not match')
        self._mapped: list[list[int]] = []
        self.serial = bytearray()
        self.mmio_counts: Counter = Counter()
        self.mmio_writes: Counter = Counter()
        self.stub_hits: Counter = Counter()
        self.pc_samples: Counter = Counter()
        self.unmapped: list[dict] = []
        self.unmapped_counts: Counter = Counter()
        self._instructions = 0
        self._build(ram_banks, mmio_ranges, sram)
        if self.qemu_peripherals:
            from project_paths import PROJECT_ROOT
            from src.a6400.devices.cxd90045_peripherals import (
                CxdPeripheralBus, load_submicro_frames)

            def set_soft_irq(irq: int, level: bool) -> None:
                # Latch until GICD_ISENABLER and the CPU/distributor enables
                # actually allow the line. A GPIO edge or SIO done pulse must
                # not enter the guest before that.
                if level:
                    self._edge_pending.add(irq)
                else:
                    self._edge_pending.discard(irq)
                    self._soft_pending_irqs.discard(irq)
                self._sync_frame_irqs(set())

            self._cxd_bus = CxdPeripheralBus(
                set_irq=set_soft_irq,
                mem_read=lambda addr, n: bytes(self.uc.mem_read(addr, n)),
                mem_write=lambda addr, data: self.uc.mem_write(addr, data),
                emmc_image=self.emmc_image,
                submicro_frames=load_submicro_frames(PROJECT_ROOT),
            )
            self._sugilite_clk_regs.setdefault(0x80, 0x15)

    # ------------------------------------------------------------------ helpers
    def _ensure_mapped(self, begin: int, end: int, perms: int = 7) -> None:
        begin, end = begin & ~0xFFF, (end + 0xFFF) & ~0xFFF
        for existing in self._mapped:
            if begin >= existing[0] and end <= existing[1]:
                return
            if begin < existing[1] and existing[0] < end:
                raise MachineError(f'mapping 0x{begin:x}..0x{end:x} overlaps '
                                   f'0x{existing[0]:x}..{existing[1]:x}')
        self.uc.mem_map(begin, end - begin, perms)
        self._mapped.append([begin, end])

    def nearest_symbol(self, address: int) -> str | None:
        import bisect
        if not self._symbol_addresses:
            return None
        index = bisect.bisect_right(self._symbol_addresses, address) - 1
        if index < 0:
            return None
        symbol_address = self._symbol_addresses[index]
        name = next(name for name, value in self.symbols.items()
                    if value == symbol_address)
        delta = address - symbol_address
        return name if not delta else f'{name}+0x{delta:x}'

    # -------------------------------------------------------------------- build
    def _build(self, ram_banks, mmio_ranges, sram) -> None:
        for base, size in ram_banks:
            self._ensure_mapped(base, base + size)
        for start, end in mmio_ranges:
            self._ensure_mapped(start, end + 1)
        self._ensure_mapped(sram[0], sram[0] + sram[1])
        self._ensure_mapped(self.kernel_load, self.kernel_load + len(self.kernel_raw))
        self._ensure_mapped(self.initrd_load, self.initrd_load + len(self.initrd))
        self.uc.mem_write(self.kernel_load, self.kernel_raw)
        self.uc.mem_write(self.initrd_load, self.initrd)
        payload = self.boot_params_payload if self.boot_params_payload is not None else self.atags
        self.uc.mem_write(self.boot_params, payload)
        self.uc.reg_write(UC_ARM_REG_R0, 0)
        self.uc.reg_write(UC_ARM_REG_R1, self.machine_id)
        self.uc.reg_write(UC_ARM_REG_R2, self.boot_params)
        self.uc.reg_write(UC_ARM_REG_CPSR, self.entry_cpsr)
        self.uc.reg_write(UC_ARM_REG_PC, self.kernel_load)
        self._patch_exclusive_instructions()
        self._build_ex_table()
        self._install_hooks()
        if self.virtual_tlb_enabled and not self.virtual_tlb_on_userspace:
            self._install_virtual_tlb()
        self._install_switch_mm_hook()
        self._install_ttbr0_write_hooks()

        try:
            _e0483_pcs = (0x04031ABA, 0x04031ABC, 0x04031AC4, 0x04031AD0,
                          0x04031AD8, 0x04031AEA, 0x04031B00, 0x04031A00,
                          0x04031A30, 0x04031A42, 0x04031A68,
                          0x0403D6A8, 0x0403D6B2)

            def _e0483_hook(uc, address, size, user):
                if len(self.e0483_trace) >= 40:
                    return
                try:
                    self.e0483_trace.append({
                        'tag': f'0x{address:08x}',
                        'r0': f'0x{uc.reg_read(UC_ARM_REG_R0):08x}',
                        'r1': f'0x{uc.reg_read(UC_ARM_REG_R1):08x}',
                        'r2': f'0x{uc.reg_read(UC_ARM_REG_R2):08x}',
                        'r3': f'0x{uc.reg_read(UC_ARM_REG_R3):08x}',
                        'r7': f'0x{uc.reg_read(UC_ARM_REG_R7):08x}',
                        'r8': f'0x{uc.reg_read(UC_ARM_REG_R8):08x}',
                        'sp': f'0x{uc.reg_read(UC_ARM_REG_SP):08x}',
                        'lr': f'0x{uc.reg_read(UC_ARM_REG_LR):08x}',
                        'cpsr': f'0x{uc.reg_read(UC_ARM_REG_CPSR):08x}'})
                except Exception:
                    pass
            for _pc in _e0483_pcs:
                self.uc.hook_add(UC_HOOK_CODE, _e0483_hook,
                                 begin=_pc, end=_pc + 4)
        except Exception:
            pass

        try:
            _e0493_pcs = (0x04031AB6, 0x04031BDE, 0x04031BE8, 0x04031A0C,
                          0x04031A42, 0x04031A68, 0x04031AA8, 0x0403FEA0,
                          0x0403609C)

            def _e0493_hook(uc, address, size, user):
                if len(self.e0493_trace) >= 24:
                    return
                try:
                    sp = uc.reg_read(UC_ARM_REG_SP)
                    slot = None
                    try:
                        slot = self._read_user32(sp + 0x20)
                    except Exception:
                        slot = None
                    self.e0493_trace.append({
                        'tag': f'0x{address:08x}',
                        'r0': f'0x{uc.reg_read(UC_ARM_REG_R0):08x}',
                        'r4': f'0x{uc.reg_read(UC_ARM_REG_R4):08x}',
                        'r7': f'0x{uc.reg_read(UC_ARM_REG_R7):08x}',
                        'r8': f'0x{uc.reg_read(UC_ARM_REG_R8):08x}',
                        'sp': f'0x{sp:08x}',
                        'lr': f'0x{uc.reg_read(UC_ARM_REG_LR):08x}',
                        'sp20': (f'0x{slot:08x}' if slot is not None else None)})
                except Exception:
                    pass
            for _pc in _e0493_pcs:
                self.uc.hook_add(UC_HOOK_CODE, _e0493_hook,
                                 begin=_pc, end=_pc + 4)
        except Exception:
            pass

        try:
            _e0495_pcs = (0x0403EB14, 0x0403EB16, 0x0403EB20, 0x0403EB24,
                          0x0403EB26, 0x0403EB28)

            def _e0495_hook(uc, address, size, user):
                if len(self.e0495_trace) >= 24:
                    return
                try:
                    self.e0495_trace.append({
                        'tag': f'0x{address:08x}',
                        'r0': f'0x{uc.reg_read(UC_ARM_REG_R0):08x}',
                        'r1': f'0x{uc.reg_read(UC_ARM_REG_R1):08x}',
                        'r2': f'0x{uc.reg_read(UC_ARM_REG_R2):08x}',
                        'r3': f'0x{uc.reg_read(UC_ARM_REG_R3):08x}',
                        'r4': f'0x{uc.reg_read(UC_ARM_REG_R4):08x}',
                        'r7': f'0x{uc.reg_read(UC_ARM_REG_R7):08x}',
                        'sp': f'0x{uc.reg_read(UC_ARM_REG_SP):08x}',
                        'lr': f'0x{uc.reg_read(UC_ARM_REG_LR):08x}',
                        'cpsr': f'0x{uc.reg_read(UC_ARM_REG_CPSR):08x}'})
                except Exception:
                    pass
            for _pc in _e0495_pcs:
                self.uc.hook_add(UC_HOOK_CODE, _e0495_hook,
                                 begin=_pc, end=_pc + 4)
        except Exception:
            pass

        try:
            _e0496_pcs = (0x6010DEE0, 0x6010DEE4, 0x6010DEEC, 0x6010DEF8,
                          0x6010DF34, 0x6010DF50, 0x6010DF58, 0x6010DF64)

            def _e0496_hook(uc, address, size, user):
                if len(self.e0496_kernel) >= 24:
                    return
                try:
                    frame = self.pt_frame_last or {}
                    self.e0496_kernel.append({
                        'tag': f'0x{address:08x}',
                        'sp': f'0x{uc.reg_read(UC_ARM_REG_SP):08x}',
                        'lr': f'0x{uc.reg_read(UC_ARM_REG_LR):08x}',
                        'pc': f'0x{uc.reg_read(UC_ARM_REG_PC):08x}',
                        'cpsr': f'0x{uc.reg_read(UC_ARM_REG_CPSR):08x}',
                        'spsr': f'0x{uc.reg_read(UC_ARM_REG_SPSR):08x}',
                        'frame_gen': frame.get('gen'),
                        'frame_cpsr': (f"0x{frame['cpsr']:08x}"
                                       if frame else None)})
                except Exception:
                    pass
            for _pc in _e0496_pcs:
                self.uc.hook_add(UC_HOOK_CODE, _e0496_hook,
                                 begin=_pc, end=_pc + 4)
        except Exception:
            pass

        try:
            _e0499_pcs = (0x0403E980, 0x0403E98C, 0x0403E992, 0x0403E9A2,
                          0x0403EB10, 0x0403EB20, 0x0403EB22, 0x0403EB26,
                          0x0403EB28, 0x0403EB2C)

            def _e0499_hook(uc, address, size, user):
                if len(self.e0499_trace) >= 32:
                    return
                try:
                    self.e0499_trace.append({
                        'tag': f'0x{address:08x}',
                        'r0': f'0x{uc.reg_read(UC_ARM_REG_R0):08x}',
                        'r1': f'0x{uc.reg_read(UC_ARM_REG_R1):08x}',
                        'r2': f'0x{uc.reg_read(UC_ARM_REG_R2):08x}',
                        'r3': f'0x{uc.reg_read(UC_ARM_REG_R3):08x}',
                        'r4': f'0x{uc.reg_read(UC_ARM_REG_R4):08x}',
                        'r7': f'0x{uc.reg_read(UC_ARM_REG_R7):08x}',
                        'sp': f'0x{uc.reg_read(UC_ARM_REG_SP):08x}',
                        'lr': f'0x{uc.reg_read(UC_ARM_REG_LR):08x}',
                        'cpsr': f'0x{uc.reg_read(UC_ARM_REG_CPSR):08x}'})
                except Exception:
                    pass
            for _pc in _e0499_pcs:
                self.uc.hook_add(UC_HOOK_CODE, _e0499_hook,
                                 begin=_pc, end=_pc + 4)
        except Exception:
            pass

        try:
            def _fast_entry_hook(uc, address, size, user):
                self._capture_fast_syscall_frame(uc)
            self.uc.hook_add(UC_HOOK_CODE, _fast_entry_hook,
                             begin=0x6010DEDC, end=0x6010DEDC)

            def _fast_return_hook(uc, address, size, user):
                if self._restore_fast_syscall_user_return(uc):
                    try:
                        uc.emu_stop()
                    except Exception:
                        pass
            self.uc.hook_add(UC_HOOK_CODE, _fast_return_hook,
                             begin=0x6010DEF8, end=0x6010DEF8)
        except Exception:
            pass

        try:
            def _e0500_hook(uc, address, size, user):
                if len(self.e0500_frames) >= 8:
                    return
                try:
                    sp = uc.reg_read(UC_ARM_REG_SP)
                    words = [self._read32_phys((sp + 4 * i) & 0x1FFFFFFF)
                             for i in range(20)]
                    self.e0500_frames.append({
                        'tag': f'0x{address:08x}',
                        'sp': f'0x{sp:08x}',
                        'lr': f'0x{uc.reg_read(UC_ARM_REG_LR):08x}',
                        'cpsr': f'0x{uc.reg_read(UC_ARM_REG_CPSR):08x}',
                        'words': [f'0x{w:08x}' for w in words]})
                except Exception:
                    pass
            for _pc in (0x6010DEC0, 0x6010DEE0, 0x6010DEEC, 0x6010DEF8):
                self.uc.hook_add(UC_HOOK_CODE, _e0500_hook,
                                 begin=_pc, end=_pc + 4)
        except Exception:
            pass

        try:
            def _e0504_hook(uc, address, size, user):
                if len(self.e0504_trace) >= 32:
                    return
                try:
                    cpsr = uc.reg_read(UC_ARM_REG_CPSR)
                    self.e0504_trace.append({
                        'pc': f'0x{address:08x}',
                        'cpsr': f'0x{cpsr:08x}',
                        'thumb': bool(cpsr & 0x20),
                        'mode': f'0x{cpsr & 0x1F:02x}',
                        'sp': f'0x{uc.reg_read(UC_ARM_REG_SP):08x}',
                        'lr': f'0x{uc.reg_read(UC_ARM_REG_LR):08x}',
                    })
                except Exception:
                    pass
            self.uc.hook_add(UC_HOOK_CODE, _e0504_hook,
                             begin=0x0402F660, end=0x0402F670)
        except Exception:
            pass

        try:
            _regmap = ((0, UC_ARM_REG_R0), (1, UC_ARM_REG_R1), (2, UC_ARM_REG_R2),
                       (3, UC_ARM_REG_R3), (4, UC_ARM_REG_R4), (5, UC_ARM_REG_R5),
                       (6, UC_ARM_REG_R6), (7, UC_ARM_REG_R7),
                       (8, UC_ARM_REG_R8), (9, UC_ARM_REG_R9),
                       (10, UC_ARM_REG_R10), (11, UC_ARM_REG_R11),
                       (12, UC_ARM_REG_R12))

            def _e0475_hook(uc, address, size, user):
                if len(self.e0475_trace) >= 6:
                    return
                try:
                    sp = uc.reg_read(UC_ARM_REG_SP)
                    regs = {}
                    for name, rid in _regmap:
                        regs[f'r{name}'] = f'0x{uc.reg_read(rid):08x}'
                    regs['sp'] = f'0x{sp:08x}'
                    regs['lr'] = f'0x{uc.reg_read(UC_ARM_REG_LR):08x}'
                    regs['cpsr'] = f'0x{uc.reg_read(UC_ARM_REG_CPSR):08x}'
                    stack = []
                    if self._cached_ttbr0 is not None:
                        stack = [f'0x{self._read_user32(sp + 4 * i) or 0:08x}'
                                 for i in range(16)]
                    self.e0475_trace.append({
                        'tag': f'0x{address:08x}', 'regs': regs,
                        'stack': stack})
                except Exception:
                    pass
            for _pc, _tag in ((0x0402E790, 'entry'), (0x0403D6A8, 'd6a8'),
                              (0x0403D6B2, 'd6b2')):
                self.uc.hook_add(UC_HOOK_CODE, _e0475_hook,
                                 begin=_pc, end=_pc + 4)
        except Exception:
            pass


        try:
            def _user_entry_trace_hook(uc, address, size, user):
                if len(self.user_entry_trace) >= 8:
                    return
                try:
                    cpsr = uc.reg_read(UC_ARM_REG_CPSR)
                except Exception:
                    return
                if (cpsr & 0x1F) != 0x10:
                    return
                self.user_entry_trace.append({
                    'pc': f'0x{address:08x}',
                    'cpsr': f'0x{cpsr:08x}',
                    'thumb': bool(cpsr & 0x20)})
            self.uc.hook_add(UC_HOOK_CODE, _user_entry_trace_hook,
                             begin=0x0402E790, end=0x0402E7A8)
        except Exception:
            pass


        try:
            def _ret_ldmdb_hook(uc, address, size, user):
                # E0496: `ldmdb sp,{r0-r12,sp,lr}^` must not load the user PC in
                # SVC mode (that resumed user code in SVC/ARM). Skip it only for
                # an unconsumed user-mode return on the matching kernel stack;
                # kernel threads (SPSR=0x93) rely on this instruction to load r4..r7.
                frame = self.pt_frame_last
                if (frame is not None
                        and frame['gen'] > self.pt_frame_consumed
                        and (frame['cpsr'] & 0x1F) == 0x10
                        and (uc.reg_read(UC_ARM_REG_SPSR) & 0x1F) == 0x10
                        and ('svc_sp' not in frame
                             or (uc.reg_read(UC_ARM_REG_SP) & ~0x1FFF) == (frame['svc_sp'] & ~0x1FFF))):
                    try:
                        uc.reg_write(UC_ARM_REG_PC, 0x6010DF60)
                    except Exception:
                        pass
            self.uc.hook_add(UC_HOOK_CODE, _ret_ldmdb_hook,
                             begin=0x6010DF58, end=0x6010DF58)
        except Exception:
            pass

        try:
            def _vector_stmdb_hook(uc, address, size, user):
                if self._emulate_vector_stmdb(uc):
                    try:
                        uc.reg_write(UC_ARM_REG_PC, 0x6010DFB4)
                    except Exception:
                        pass
            self.uc.hook_add(UC_HOOK_CODE, _vector_stmdb_hook,
                             begin=0x6010DFB0, end=0x6010DFB0)
        except Exception:
            pass

        try:
            def _ret_msr_hook(uc, address, size, user):
                # E0463: 0x6010DF50 msr spsr_fsxc, r1 (lr already = user PC)
                try:
                    r1 = uc.reg_read(UC_ARM_REG_R1)
                    lr = uc.reg_read(UC_ARM_REG_LR)
                except Exception:
                    return
                if (r1 & 0x1F) != 0x10:
                    return
                self.pending_user_spsr = r1
                self.pending_user_lr = lr
                if len(self.e0492_msr) < 12:
                    self.e0492_msr.append({
                        'r1': f'0x{r1:08x}', 'lr': f'0x{lr:08x}',
                        'usr': bool((r1 & 0x1F) == 0x10),
                        'frame_gen': (self.pt_frame_last or {}).get('gen'),
                        'frame_cpsr': (f"0x{self.pt_frame_last['cpsr']:08x}"
                                       if self.pt_frame_last else None)})
                if len(self.ret_tail_trace) < 16:
                    self.ret_tail_trace.append({
                        'tag': 'msr50', 'spsr': f'0x{r1:08x}',
                        'lr': f'0x{lr:08x}'})
            self.uc.hook_add(UC_HOOK_CODE, _ret_msr_hook,
                             begin=0x6010DF50, end=0x6010DF50)
        except Exception:
            pass

        try:
            def _ret_tail_hook(uc, address, size, user):
                # E0492: consume the pt_regs frame captured at 0x6010DF34 at
                # most once. A second 0x6010DF64 hit without a new frame is not
                # a user return - never replay it (that set SP_usr = 0).
                frame = self._take_user_frame()
                if frame is None:
                    return
                if ('svc_sp' in frame
                        and (uc.reg_read(UC_ARM_REG_SP) & ~0x1FFF) != (frame['svc_sp'] & ~0x1FFF)):
                    return
                spsr = frame['cpsr']
                target = frame['pc']
                if (spsr & 0x1F) != 0x10:
                    return
                if not ((0x1000 <= (target or 0) < 0x60000000)
                        or (0xFFFF0000 <= (target or 0) < 0xFFFF1000)):
                    self.pt_frame_consumed = frame['gen']
                    return
                if not (0x1000 <= (frame['sp'] or 0) < 0x60000000):
                    self.pt_frame_consumed = frame['gen']
                    return
                self.user_restores += 1
                if len(self.ret_tail_trace) < 16:
                    self.ret_tail_trace.append({
                        'tag': 'restore', 'gen': frame['gen'],
                        'spsr': f'0x{spsr:08x}',
                        'lr': f'0x{(frame["lr"] or 0):08x}',
                        'pc': f'0x{target:08x}',
                        'sp': f'0x{frame["sp"]:08x}',
                        'frame_cpsr': f'0x{spsr:08x}'})
                try:
                    # E0496: switch to the USR bank *before* restoring GPRs so
                    # the writes land in the user register bank.
                    uc.reg_write(UC_ARM_REG_CPSR, spsr)
                    for i in range(13):
                        uc.reg_write(UC_ARM_REG_R0 + i, frame['words'][i])
                    uc.reg_write(UC_ARM_REG_SP, frame['sp'])
                    uc.reg_write(UC_ARM_REG_LR, frame['lr'])
                    self._set_pc_and_cpsr(target, spsr)
                    self._pending_cputlb_flush = True
                    self._pte_dirty = False
                    self._pending_user_resume = (
                        self.thumb_pc(target, spsr), spsr)
                    self._pending_user_frame = frame
                    self._restore_user_sp = False
                    try:
                        uc.emu_stop()
                    except Exception:
                        pass
                except Exception:
                    return
            self.uc.hook_add(UC_HOOK_CODE, _ret_tail_hook,
                             begin=0x6010DF64, end=0x6010DF64)
        except Exception:
            pass

        except Exception:
            pass

        try:
            for _pc, _tag in ((0x601085EC, 'do_PrefetchAbort'),
                              (0x60116D94, 'do_page_fault')):
                def _abort_frame_hook(uc, address, size, user, tag=_tag):
                    try:
                        self._record_user_frame(tag, uc.reg_read(UC_ARM_REG_SP))
                    except Exception:
                        pass
                self.uc.hook_add(UC_HOOK_CODE, _abort_frame_hook,
                                 begin=_pc, end=_pc + 4)
        except Exception:
            pass

        if self.virtual_tlb_on_userspace:
            def first_user_hook(uc, address, size, user):
                if self._user_refresh_done or not self.virtual_tlb:
                    return
                try:
                    if (uc.reg_read(UC_ARM_REG_CPSR) & 0x1F) != 0x10:
                        return
                except Exception:
                    return
                self._user_refresh_done = True
                self._user_refresh_requested = True
                try:
                    uc.emu_stop()
                except Exception:
                    pass
            try:
                self.uc.hook_add(UC_HOOK_CODE, first_user_hook,
                                 begin=0x1000, end=0x60000000)
            except Exception:
                pass


    # ------------------------------------------------- exclusive pre-translation
    #: RAM area used for per-site STREX trampolines (VA = PA + PAGE_OFFSET).
    EXCLUSIVE_STUB_PA = 0x02000000

    def _patch_exclusive_instructions(self) -> None:
        """Translate ARMv7 exclusives/barriers in the loaded kernel image.

        Unicorn 2.1.4 raises UC_ERR_EXCEPTION on LDREX/STREX/CLREX/barriers and an
        exception-per-instruction path is far too slow for an SMP kernel (every
        spinlock/atomic uses them).  On our deterministic single-core machine the
        exclusive monitor can only ever be free, so the translation is exact:
        LDREX* -> LDR*, STREX* -> STR* + MOV Rd,#0 via a per-site trampoline, and
        DMB/DSB/ISB/CLREX -> NOP.
        """
        if not self.exclusive_patch:
            return
        start = self.kernel_load
        length = len(self.kernel_raw)
        try:
            data = bytearray(self.uc.mem_read(start, length))
        except Exception:
            return
        page_offset = self.PAGE_OFFSET
        stub_pa = self.EXCLUSIVE_STUB_PA
        counts = {'ldrex': 0, 'strex': 0, 'barrier': 0, 'stubs': 0}

        def branch_word(cond, target_va, site_va):
            offset = ((target_va - (site_va + 8)) >> 2) & 0xFFFFFF
            return (cond & 0xF0000000) | 0x0A000000 | offset

        for off in range(0, length - 3, 4):
            word = int.from_bytes(data[off:off + 4], 'little')
            site_pa = start + off
            site_va = site_pa + page_offset
            if (word & 0x0FF00FFF) == 0x01900F9F:  # LDREX Rd,[Rn]
                cond = word & 0xF0000000
                rn = (word >> 16) & 0xF
                rd = (word >> 12) & 0xF
                data[off:off + 4] = (cond | 0x05900000 | (rn << 16)
                                     | (rd << 12)).to_bytes(4, 'little')
                counts['ldrex'] += 1
                continue
            if (word & 0x0FF00FF0) == 0x01800F90:  # STREX Rd,Rm,[Rn]
                cond = word & 0xF0000000
                rn = (word >> 16) & 0xF
                rd = (word >> 12) & 0xF
                rm = word & 0xF
                stub_va = stub_pa + page_offset
                data[off:off + 4] = branch_word(cond, stub_va, site_va).to_bytes(
                    4, 'little')
                stub = [(cond | 0x05800000 | (rn << 16) | (rm << 12)),
                        (cond | 0x03A00000 | (rd << 12)),
                        (0xEA000000
                         | ((((site_va + 4) - (stub_va + 8)) >> 2) & 0xFFFFFF))]
                self.uc.mem_write(stub_pa, struct.pack('<3I', *stub))
                stub_pa += 16
                counts['strex'] += 1
                counts['stubs'] += 1
                continue
            if word in (0xF57FF05F, 0xF57FF04F, 0xF57FF06F, 0xF57FF01F):
                data[off:off + 4] = (0xE320F000).to_bytes(4, 'little')  # NOP
                counts['barrier'] += 1
        self.uc.mem_write(start, bytes(data))
        self.exclusive_patched = counts

    def _install_hooks(self) -> None:
        uart_start, uart_end = self.uart_base, self.uart_base + 0xFFF
        mmio_starts = [(start, end) for start, end in DEFAULT_MMIO_RANGES]

        def in_range(address, base, size=0x1000):
            return base <= address < base + size

        def uart_reads(address):
            return in_range(address, self.uart_base)

        def uart_register_value(offset):
            if offset == PL011_DR:
                if self.serial_input:
                    ch = self.serial_input.pop(0)
                    if self.serial_input:
                        self._uart_regs[PL011_RIS] = self._uart_regs.get(PL011_RIS, 0) | 0x10
                    else:
                        self._uart_regs[PL011_RIS] = self._uart_regs.get(PL011_RIS, 0) & ~0x10
                    self._sync_uart_irq()
                    return ch
                return 0
            if offset == PL011_FR:
                rxfe = 0x10 if not self.serial_input else 0
                return PL011_FR_TXFE | rxfe
            if offset in PL011_ID_REGS:
                if offset == 0xFE0:
                    self.id_reads = getattr(self, 'id_reads', 0) + 1
                return PL011_ID_REGS[offset]
            if offset == PL011_RIS:
                ris = self._uart_regs.get(PL011_RIS, 0) | 0x20  # TXRIS always ready
                if self.serial_input:
                    ris |= 0x10
                return ris
            if offset == PL011_MIS:
                return uart_register_value(PL011_RIS) & self._uart_regs.get(PL011_IMSC, 0)
            return self._uart_regs.get(offset, 0)

        def mmio_read(uc, access, address, size, value, user):
            self._clear_mid_tb_itstate()
            self.mmio_counts[address] += 1
            if address in self.stubs:
                self.stub_hits[address] += 1
                stub_value = self.stubs[address] & ((1 << (size * 8)) - 1)
                uc.mem_write(address, stub_value.to_bytes(size, 'little'))
                return True
            if in_range(address, CRCDC_BASE, 0x1000):
                # CRCDC/clock block: update_crdm writes +0x30=1 and polls bit0;
                # report completion immediately (bit0 clear) instead of timing out.
                uc.mem_write(address, struct.pack('<I', 0))
                return True
            if in_range(address, SUGILITE_CLK_BASE, SUGILITE_CLK_SIZE):
                offset = address - SUGILITE_CLK_BASE
                stored = self._sugilite_clk_regs.get(offset, 0)
                uc.mem_write(address, (stored & ((1 << (size * 8)) - 1))
                             .to_bytes(size, 'little'))
                return True
            if in_range(address, SUGILITE_BASE, SUGILITE_SIZE):
                offset = address - SUGILITE_BASE
                stored = self._sugilite_mmio.read_reg(offset)
                uc.mem_write(address, (stored & ((1 << (size * 8)) - 1))
                             .to_bytes(size, 'little'))
                return True
            if uart_reads(address):
                offset = address - self.uart_base
                if offset in (PL011_DR, PL011_FR, PL011_RIS, PL011_MIS) or offset in PL011_ID_REGS:
                    raw = uart_register_value(offset)
                else:
                    raw = self._uart_regs.get(offset, 0)
                uc.mem_write(address, (raw & ((1 << (size * 8)) - 1))
                             .to_bytes(size, 'little'))
                return True
            if in_range(address, TIMER_BASE, 0x200):
                offset = address - TIMER_BASE
                if offset == TIMER_COUNTER_OFFSET:
                    self._timer_counter_reads += 1
                    uc.mem_write(address,
                                 (self._timer_counter & ((1 << (size * 8)) - 1))
                                 .to_bytes(size, 'little'))
                    return True
                stored = self._timer_regs.get(offset, 0)
                uc.mem_write(address, (stored & ((1 << (size * 8)) - 1))
                             .to_bytes(size, 'little'))
                return True
            if in_range(address, GIC_DIST) or in_range(address, GIC_CPU):
                # The distributor window starts at CPU+0xF00, so classify it first.
                if in_range(address, GIC_DIST):
                    key = ('dist', address - GIC_DIST)
                else:
                    key = ('cpu', address - GIC_CPU)
                if key == ('dist', 0x004):
                    # GICD_TYPER: report 1024 interrupt lines so the kernel accepts
                    # the observed CXD timer IRQ 153 (setup_irq validation).
                    uc.mem_write(address, struct.pack('<I', 0x0000001F))
                    return True
                if key == ('cpu', GIC_ICCIAR):
                    self._gic_iar_reads += 1
                    if len(self._gic_iar_pcs) < 20:
                        self._gic_iar_pcs.append(
                            f'0x{uc.reg_read(UC_ARM_REG_PC):08x}')
                    if self._gic_active_irq is not None and not self._gic_iar_served:
                        value = self._gic_active_irq
                        self._gic_iar_served = True
                    else:
                        value = GIC_SPURIOUS
                    uc.mem_write(address, value.to_bytes(size, 'little'))
                    return True
                stored = self._gic_regs.get(key, 0)
                uc.mem_write(address, (stored & ((1 << (size * 8)) - 1))
                             .to_bytes(size, 'little'))
                return True
            if self._cxd_bus is not None:
                bus_val = self._cxd_bus.read(address, size=size)
                if bus_val is not None:
                    uc.mem_write(address, (bus_val & ((1 << (size * 8)) - 1))
                                 .to_bytes(size, 'little'))
                    return True
            if address in self._oracle_mmio_queues:
                queue = self._oracle_mmio_queues[address]
                oracle_val = queue.pop(0) if queue else self._oracle_mmio_last.get(address, 0)
                self._oracle_mmio_last[address] = oracle_val
                self.oracle_mmio_hits[address] += 1
                uc.mem_write(address, (oracle_val & ((1 << (size * 8)) - 1))
                             .to_bytes(size, 'little'))
                return True
            # Remaining MMIO reads fall through to the mapped zero/register image
            # (PL011 FR is pre-seeded to TXFE|RXFE).
            return True

        def mmio_write(uc, access, address, size, value, user):
            self._clear_mid_tb_itstate()
            self.mmio_counts[address] += 1
            self.mmio_writes[address] += 1
            if in_range(address, TIMER_BASE, 0x200):
                self._timer_regs[address - TIMER_BASE] = value & 0xFFFFFFFF
                return True
            if in_range(address, GIC_DIST) or in_range(address, GIC_CPU):
                cpu_side = not in_range(address, GIC_DIST)
                offset = address - (GIC_CPU if cpu_side else GIC_DIST)
                key = ('cpu', offset) if cpu_side else ('dist', offset)
                if not cpu_side and 0x100 <= offset < 0x180:
                    # GICD_ISENABLER is write-1-to-set. A later single-bit enable
                    # must keep the frame-clock lines already enabled.
                    self._gic_regs[key] = self._gic_regs.get(key, 0) | (value & 0xFFFFFFFF)
                    self._sync_frame_irqs(set())
                    return True
                if not cpu_side and 0x180 <= offset < 0x200:
                    en_key = ('dist', offset - 0x80)
                    self._gic_regs[en_key] = (
                        self._gic_regs.get(en_key, 0) & ~(value & 0xFFFFFFFF))
                    self._gic_regs[key] = value & 0xFFFFFFFF
                    return True
                self._gic_regs[key] = value & 0xFFFFFFFF
                if cpu_side and offset == GIC_ICCEOIR:
                    self._gic_active_irq = None
                    self._gic_eoir_writes += 1
                    self._sync_uart_irq()
                    self._sync_frame_irqs(set())
                if cpu_side and offset == GIC_ICCICR:
                    self._gic_enabled = bool(
                        (value & 1) and (self._gic_regs.get(('dist', 0x000), 0) & 1))
                if not cpu_side and offset == 0x000:
                    self._gic_enabled = bool(
                        (value & 1) and (self._gic_regs.get(('cpu', GIC_ICCICR), 0) & 1))
                if not cpu_side and 0x200 <= offset < 0x280:
                    base_irq = ((offset - 0x200) // 4) * 32
                    for bit in range(32):
                        if (value >> bit) & 1:
                            self._soft_pending_irqs.add(base_irq + bit)
                if not cpu_side and 0x280 <= offset < 0x300:
                    base_irq = ((offset - 0x280) // 4) * 32
                    for bit in range(32):
                        if (value >> bit) & 1:
                            self._soft_pending_irqs.discard(base_irq + bit)
                return True
            if in_range(address, CRCDC_BASE, 0x1000):
                self._crcdc_regs[address - CRCDC_BASE] = value & 0xFFFFFFFF
                return True
            if in_range(address, SUGILITE_CLK_BASE, SUGILITE_CLK_SIZE):
                offset = address - SUGILITE_CLK_BASE
                val32 = value & 0xFFFFFFFF
                if offset in (0x080, 0x280):
                    self._sugilite_clk_regs[offset] = self._sugilite_clk_regs.get(offset, 0) | val32
                elif offset in (0x084, 0x284):
                    base_off = offset - 4
                    self._sugilite_clk_regs[base_off] = self._sugilite_clk_regs.get(base_off, 0) & ~val32
                else:
                    self._sugilite_clk_regs[offset] = val32
                return True
            if in_range(address, SUGILITE_BASE, SUGILITE_SIZE):
                offset = address - SUGILITE_BASE
                self._sugilite_mmio.write_reg(offset, value & 0xFFFFFFFF)
                if self._sugilite_mmio.irq_asserted:
                    self._soft_pending_irqs.add(SUGILITE_IRQ)
                else:
                    self._soft_pending_irqs.discard(SUGILITE_IRQ)
                return True
            if in_range(address, self.uart_base):
                offset = address - self.uart_base
                if offset == PL011_DR:
                    self.serial.append(value & 0xFF)
                elif offset == PL011_ICR:
                    self._uart_regs[PL011_RIS] = self._uart_regs.get(PL011_RIS, 0) & ~value
                    self._sync_uart_irq()
                else:
                    self._uart_regs[offset] = value & 0xFFFFFFFF
                    if offset == PL011_IMSC:
                        self._sync_uart_irq()
            if self._cxd_bus is not None and self._cxd_bus.write(address, value, size=size):
                return True
            if size <= 4:
                stream = self.mmio_write_bytes.setdefault(address, bytearray())
                if len(stream) < 2048:
                    stream.append(value & 0xFF)
            return True

        def unmapped(uc, access, address, size, value, user):
            entry = {'pc': uc.reg_read(UC_ARM_REG_PC), 'address': address, 'size': size,
                     'access': access,
                     'r0': uc.reg_read(UC_ARM_REG_R0), 'r1': uc.reg_read(UC_ARM_REG_R1),
                     'r2': uc.reg_read(UC_ARM_REG_R2),
                     'sp': uc.reg_read(UC_ARM_REG_SP), 'lr': uc.reg_read(UC_ARM_REG_LR)}
            self.unmapped.append(entry)
            self.unmapped_counts[address] += 1
            if (self.unmapped_mmio_policy == 'observe-zero'
                    and address >= 0xF0000000 and address < 0x100000000):
                page = address & ~0xFFF
                if page not in self._unmapped_mmio_mapped and len(self._mapped) < 200:
                    self.uc.mem_map(page, 0x1000, 7)
                    self._mapped.append([page, page + 0x1000])
                    self._unmapped_mmio_mapped.append(page)
                return True  # retry: the page is now zero-backed and logged
            uc.emu_stop()
            return False

        def code(uc, address, size, user):
            self._instructions += 1
            if self._instructions % self.sample_interval == 0:
                self.pc_samples[address] += 1

        def user_code(uc, address, size, user):
            cpsr = uc.reg_read(UC_ARM_REG_CPSR)
            if (cpsr & 0x1F) != 0x10:
                return
            if self._restore_user_sp and self.user_sp is not None:
                uc.reg_write(UC_ARM_REG_SP, self.user_sp)
                self._restore_user_sp = False
            if len(self.user_mode_entries) < 20:
                self.user_mode_entries.append({'pc': address, 'cpsr': cpsr})


        _reg_getters = {
            'r0': UC_ARM_REG_R0, 'r1': UC_ARM_REG_R1, 'r2': UC_ARM_REG_R2,
            'r3': UC_ARM_REG_R3, 'r4': UC_ARM_REG_R4, 'r5': UC_ARM_REG_R5,
            'r6': UC_ARM_REG_R6, 'r7': UC_ARM_REG_R7, 'r8': UC_ARM_REG_R8,
            'r9': UC_ARM_REG_R9, 'r10': UC_ARM_REG_R10, 'r11': UC_ARM_REG_R11,
            'r12': UC_ARM_REG_R12, 'sp': UC_ARM_REG_SP, 'lr': UC_ARM_REG_LR,
            'pc': UC_ARM_REG_PC, 'cpsr': UC_ARM_REG_CPSR,
        }
        for pc, reg_names in self.trace_regs.items():
            def capture(uc, address, size, user, pc=pc, reg_names=reg_names):
                values = {name: uc.reg_read(_reg_getters[name])
                          for name in reg_names if name in _reg_getters}
                self.trace_reg_values.setdefault(pc, []).append(values)
            self.uc.hook_add(UC_HOOK_CODE, capture, begin=pc, end=pc + 3)
        for begin, end in mmio_starts:
            self.uc.hook_add(UC_HOOK_MEM_READ, mmio_read, begin=begin, end=end)
            self.uc.hook_add(UC_HOOK_MEM_WRITE, mmio_write, begin=begin, end=end)
        # The kernel console may reach PL011 through an ioremap/vmalloc virtual
        # address rather than the physical UART base; hook the configured block
        # explicitly so serial capture works for both mappings.
        if not any(begin <= self.uart_base <= end for begin, end in mmio_starts):
            try:
                self.uc.hook_add(UC_HOOK_MEM_READ, mmio_read,
                                 begin=self.uart_base, end=self.uart_base + 0xFFF)
                self.uc.hook_add(UC_HOOK_MEM_WRITE, mmio_write,
                                 begin=self.uart_base, end=self.uart_base + 0xFFF)
            except Exception:
                pass
        self.uc.hook_add(UC_HOOK_MEM_UNMAPPED, unmapped)
        self.uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED, self._on_unmapped_read)
        self.uc.hook_add(UC_HOOK_MEM_WRITE_UNMAPPED, self._on_unmapped_write)
        self.uc.hook_add(UC_HOOK_MEM_READ_PROT, self._on_unmapped_read)
        self.uc.hook_add(UC_HOOK_MEM_WRITE_PROT, self._on_unmapped_write)
        self.uc.hook_add(UC_HOOK_MEM_FETCH_PROT, self._on_unmapped_read)
        for begin, end in self.probe_write_ranges:
            def probe_write(uc, access, address, size, value, user):
                self._clear_mid_tb_itstate()
                stream = self.mmio_write_bytes.setdefault(address, bytearray())
                if len(stream) < 2048:
                    stream.append(value & 0xFF)
                return True
            self.uc.hook_add(UC_HOOK_MEM_WRITE, probe_write, begin=begin, end=end)
        # Code hook is optional: it makes per-instruction sampling possible but is
        # much slower than slice-based PC sampling, so the runner defaults to slices.
        if self.sample_interval and self.use_code_hook:
            self.uc.hook_add(UC_HOOK_CODE, code)
        # E0429: capture the kernel SVC stack pointer right before the kernel
        # returns to user mode; it is used as the ABT-mode stack for delivered
        # user faults so abort frames never land on the user stack.
        ret_to_user = None
        for symbol_name, value in self.symbols.items():
            if symbol_name == 'ret_to_user':
                ret_to_user = value
                break
        if ret_to_user is not None:
            def capture_svc_sp(uc, address, size, user):
                self.mode_sp['svc'] = uc.reg_read(UC_ARM_REG_SP)
            self.uc.hook_add(UC_HOOK_CODE, capture_svc_sp,
                             begin=ret_to_user, end=ret_to_user + 3)
        # E0431: emulate the usr_entry banked store `stmdb r0, {sp, lr}^` in the
        # __pabt_usr/__dabt_usr handlers (Unicorn has no banked R13/R14).
        for handler_name in ('__pabt_usr', '__dabt_usr', '__irq_usr'):
            handler_pc = self._symbol_address(handler_name)
            if handler_pc is not None:
                banked_pc = handler_pc + 0x1C
                def banked_store(uc, address, size, user, pc=banked_pc):
                    self.banked_store_hits += 1
                    self._emulate_banked_store(pc)
                self.uc.hook_add(UC_HOOK_CODE, banked_store,
                                 begin=banked_pc, end=banked_pc + 3)
        for svc_handler_name in ('__dabt_svc', '__pabt_svc'):
            svc_handler_pc = self._symbol_address(svc_handler_name)
            if svc_handler_pc is not None:
                svc_exit_pc = svc_handler_pc + 0x44
                def svc_abort_exit_flush(uc, address, size, user):
                    if self._pte_dirty:
                        self._flush_cputlb()
                        self._pte_dirty = False
                self.uc.hook_add(UC_HOOK_CODE, svc_abort_exit_flush,
                                 begin=svc_exit_pc, end=svc_exit_pc)
        # ret_to_user's banked-restore instruction: ldmdb sp, {r0..ip, sp, lr}^
        if False and ret_to_user is not None:  # E0432: gated off (early-boot crash)
            restore_pc = ret_to_user + 0x24
            def banked_restore(uc, address, size, user, pc=restore_pc):
                self._emulate_banked_restore(pc)
            self.uc.hook_add(UC_HOOK_CODE, banked_restore,
                             begin=restore_pc, end=restore_pc + 3)
        # Consume CPU exceptions inside emu_start. Without this hook Unicorn
        # returns UC_ERR_EXCEPTION and the next emu_start re-raises before the
        # redirected handler instruction retires. Exclusives are emulated in
        # the hook so they are not delivered as data aborts (E0436).
        if self.data_abort_delivery and not self._svc_hook_installed:
            self.uc.hook_add(UC_HOOK_INTR, self._on_interrupt)
            self._svc_hook_installed = True
        ret_to_user = self._symbol_address('ret_to_user')
        if ret_to_user is not None:
            def ret_flush_hook(uc, address, size, user, pc=ret_to_user):
                try:
                    self._record_frame('ret', uc.reg_read(UC_ARM_REG_SP))
                    self._record_user_frame('ret', uc.reg_read(UC_ARM_REG_SP))
                except Exception:
                    pass
                # E0492: generation-tagged pt_regs snapshot (base == SP at
                # ret_to_user entry, proven in E0461). Consumed once at DF64.
                try:
                    _sp = uc.reg_read(UC_ARM_REG_SP)
                    _words = [self._read32_phys(_sp + 4 * i) for i in range(18)]
                except Exception:
                    _words = None
                if _words is not None:
                    _w_sp = _words[0x34 // 4]
                    _w_lr = _words[0x38 // 4]
                    _w_pc = _words[0x3C // 4]
                    _w_cpsr = _words[0x40 // 4]
                    if 0x1000 <= (_w_pc or 0) < 0x60000000:
                        self._pending_evict_pages.add(_w_pc & ~0xFFF)
                        self._pending_cputlb_flush = True
                    if (_w_cpsr & 0x1F) == 0x10:
                        self.pt_frame_gen += 1
                        self.pt_frame_last = {
                            'gen': self.pt_frame_gen, 'words': _words,
                            'sp': _w_sp, 'lr': _w_lr,
                            'pc': _w_pc, 'cpsr': _w_cpsr, 'svc_sp': _sp}
                        if len(self.e0492_frames) < 8:
                            self.e0492_frames.append({
                                'gen': self.pt_frame_gen,
                                'sp': f'0x{(_w_sp or 0):08x}',
                                'lr': f'0x{(_w_lr or 0):08x}',
                                'pc': f'0x{(_w_pc or 0):08x}',
                                'cpsr': f'0x{(_w_cpsr or 0):08x}',
                                'user': True,
                                'words': [f'0x{w:08x}' for w in _words]})
                    else:
                        self.pt_frame_last = None
                # E0452: keep UC_TLB_CPU through kernel init; switch to the
                # virtual TLB on the first ret_to_user (userspace entry).
                if (self.virtual_tlb_on_userspace and not self.virtual_tlb
                        and not self._vtlb_requested):
                    # E0457: only switch on a real user-mode return.  Kernel
                    # thread/usermodehelper returns still have TTBR0 =
                    # swapper_pg_dir and a kernel user_pc; skip those.
                    try:
                        sp = uc.reg_read(UC_ARM_REG_SP)
                        user_pc = self._read32_phys(sp + 0x3C)
                        pgd = self._resolve_user_pgd()
                    except Exception:
                        user_pc, pgd = 0x60000000, None
                    if 0x1000 <= user_pc < 0x60000000 and pgd:
                        self._activation_pgd = pgd
                        self._vtlb_requested = True
                        try:
                            uc.emu_stop()
                        except Exception:
                            pass
                        return
                if self._pte_dirty:
                    # E0446: prefetch-abort page installs need the same real
                    # SoftMMU flush as decoded Data Aborts.
                    page = self._last_fault_page
                    if page is not None and len(self.pte_rewalks) < 128:
                        try:
                            self.pte_rewalks.append(self._pte_report(page))
                        except Exception:
                            pass
                    self._flush_cputlb()
                    self._flush_tlb()
                    self._pte_dirty = False
                    if self.virtual_tlb:
                        try:
                            self._refresh_user_ttbr0()
                            self._read_cp15_ttbr1()
                        except Exception:
                            pass
                # E0440: refresh the live SVC stack and validate thread_info->task
                sp = uc.reg_read(UC_ARM_REG_SP)
                self.mode_sp['svc'] = sp
                self._svc_stack_refreshes += 1
                ti = sp & ~0x1FFF
                try:
                    raw = bytes(uc.mem_read(self._kernel_phys(ti + 0xC), 4))
                    task = struct.unpack('<I', raw)[0]
                except Exception:
                    task = 0
                self._last_thread_info = ti
                self._last_thread_task = task
            self.uc.hook_add(UC_HOOK_CODE, ret_flush_hook,
                             begin=ret_to_user, end=ret_to_user)
            # Also hook no_work_pending (ret_to_user + 0x14, IRQs off, after
            # work_pending/schedule) so ret_to_user_from_irq and preempted
            # returns always snapshot the actual post-schedule thread's pt_regs.
            no_work_pending = ret_to_user + 0x14
            self.uc.hook_add(UC_HOOK_CODE, ret_flush_hook,
                             begin=no_work_pending, end=no_work_pending)
        mm_return = 0x60116E9C  # return PC after `bl handle_mm_fault` in do_page_fault
        def mm_return_hook(uc, address, size, user, pc=mm_return):
            # Keep this trace passive. Range TB removal fetch-translates its VA
            # in Unicorn; a previous executable's IFAR need not be mapped here.
            if len(self.mm_fault_returns) < 32:
                self.mm_fault_returns.append({
                    'pc': f'0x{pc:08x}',
                    'ret': f'0x{uc.reg_read(UC_ARM_REG_R0):08x}',
                    'addr': f'0x{uc.reg_read(UC_ARM_REG_R4):08x}',
                    'task': f'0x{self._last_thread_task:08x}'})
        self.uc.hook_add(UC_HOOK_CODE, mm_return_hook, begin=mm_return, end=mm_return + 3)
        page_fault = self._symbol_address('do_page_fault')
        if page_fault is not None:
            def page_fault_hook(uc, address, size, user, pc=page_fault):
                self._capture_fault_frame(pc)
            self.uc.hook_add(UC_HOOK_CODE, page_fault_hook,
                             begin=page_fault, end=page_fault + 3)
        self._install_fault_mrc_hooks()
        if self.vfp_d16_mvfr0:
            self._install_vfp_mvfr0_hook()
        if self.enable_svc_dispatch and (
                self.virtual_tlb or self.virtual_tlb_enabled
                or self.virtual_tlb_on_userspace):
            self._install_kuser_hooks(tls_only=True)
        else:
            self._install_kuser_hooks()
        if self.trace_user_mode:
            # Wide low-user window: ld-linux (0x04000000+) and busybox (0x8000+).
            self.uc.hook_add(UC_HOOK_CODE, user_code, begin=0x1000, end=0x60000000)
        # Pre-seed the PL011 FR value so reads hit a defined register image.
        try:
            self.uc.mem_write(self.uart_base + PL011_FR,
                              struct.pack('<I', PL011_FR_TXFE | PL011_FR_RXFE))
        except Exception:
            pass
        # One-shot runner patches at exact kernel PCs (single-address code hooks).
        for pc, writes in self.boot_patches.items():
            def apply(uc, address, size, user, pc=pc, writes=writes):
                for virtual_address, write_size, value in writes:
                    uc.mem_write(virtual_address,
                                 value.to_bytes(write_size, 'little'))
                self.patch_hits[pc] += 1
            self.uc.hook_add(UC_HOOK_CODE, apply, begin=pc, end=pc + 3)
        # Optional printk format-string tracer (offline log visibility without a
        # registered console). Kernel lowmem is linear: VA - 0x60000000 = PA.
        if self.trace_printk_at:
            page_offset = 0x60000000

            def printk_hook(uc, address, size, user):
                if len(self.printk_trace) >= 4000:
                    return
                va = uc.reg_read(UC_ARM_REG_R0)
                pa = va - page_offset if va >= page_offset else va
                text = ''
                try:
                    raw = bytes(uc.mem_read(pa, 200))
                    text = raw.split(b'\x00', 1)[0].decode('latin1', 'replace')
                except Exception:
                    text = ''
                self.printk_trace.append({
                    'pc': address, 'format_va': va, 'format': text,
                    'r1': uc.reg_read(UC_ARM_REG_R1),
                    'r2': uc.reg_read(UC_ARM_REG_R2)})

            hook_handle = self.uc.hook_add(UC_HOOK_CODE, printk_hook,
                                           begin=self.trace_printk_at,
                                           end=self.trace_printk_at + 3)
            self._printk_hook = hook_handle
        for pc, label in self.trace_points.items():
            def trace_point(uc, address, size, user, label=label):
                if len(self.trace_point_hits) < 64:
                    self.trace_point_hits.append({
                        'label': label, 'pc': f'0x{address:08x}',
                        'r0': f'0x{uc.reg_read(UC_ARM_REG_R0):08x}',
                        'r1': f'0x{uc.reg_read(UC_ARM_REG_R1):08x}',
                        'r2': f'0x{uc.reg_read(UC_ARM_REG_R2):08x}',
                        'r3': f'0x{uc.reg_read(UC_ARM_REG_R3):08x}'})
            self.uc.hook_add(UC_HOOK_CODE, trace_point, begin=pc, end=pc + 3)

    # ------------------------------------------------------------------ timer/IRQ
    def _gic_spi_enabled(self, irq: int) -> bool:
        if not self._gic_irq_enabled():
            return False
        word = self._gic_regs.get(('dist', 0x100 + (irq // 32) * 4), 0)
        return bool((word >> (irq % 32)) & 1)

    def _frame_clock_sliced(self) -> bool:
        """Shrink slices once av-cam is actually taking frame or vsync IRQs."""
        if self._cxd_bus is None:
            return False
        return any(self._gic_spi_enabled(irq) for irq in (83, 98, 101, 102, 129))

    def _sync_frame_irqs(self, pulses: set[int]) -> None:
        self._edge_pending.update(pulses)
        for irq in list(self._edge_pending):
            if self._gic_spi_enabled(irq) and irq != self._gic_active_irq:
                self._soft_pending_irqs.add(irq)
                self._edge_pending.discard(irq)
        if self._cxd_bus is None:
            return
        for irq, level in self._cxd_bus.level_irqs():
            if level and self._gic_spi_enabled(irq) and irq != self._gic_active_irq:
                self._soft_pending_irqs.add(irq)
            elif not level:
                self._soft_pending_irqs.discard(irq)

    def _advance_timer(self, instructions: int) -> None:
        if not self.timer_irq_model:
            return
        self._instructions_executed += instructions
        delta = instructions * self.timer_ticks_per_instruction
        self._timer_counter += delta
        if self._cxd_bus is not None and delta:
            self._sync_frame_irqs(self._cxd_bus.advance_us(delta))
        if self._instructions_executed < self.timer_irq_start_instructions:
            return
        if self._timer_counter >= self._next_jiffy_tick:
            # One pending timer interrupt at a time; keep virtual time monotonic.
            self._timer_irq_pending = 1
            while self._timer_counter >= self._next_jiffy_tick:
                self._next_jiffy_tick += self.timer_ticks_per_jiffy

    def _gic_irq_enabled(self) -> bool:
        cpu_ctlr = self._gic_regs.get(('cpu', GIC_ICCICR), 0)
        dist_ctlr = self._gic_regs.get(('dist', 0x000), 0)
        return bool((cpu_ctlr & 1) and (dist_ctlr & 1))

    def _sync_uart_irq(self) -> None:
        """Assert or clear PL011 UART0 IRQ 150 from masked interrupt status."""
        ris = self._uart_regs.get(PL011_RIS, 0) | 0x20  # TXRIS always ready
        if self.serial_input:
            ris |= 0x10  # RXRIS while RX FIFO non-empty
        mis = ris & self._uart_regs.get(PL011_IMSC, 0)
        if mis and self._gic_irq_enabled():
            self._soft_pending_irqs.add(UART_IRQ)
        else:
            self._soft_pending_irqs.discard(UART_IRQ)

    def _try_inject_irq(self) -> bool:
        """Deliver a pending software or timer IRQ by emulating ARM IRQ entry."""
        has_soft = bool(self._soft_pending_irqs)
        has_timer = bool(self.timer_irq_model and self._timer_irq_pending)
        if not has_soft and not has_timer:
            return False
        if not self._gic_irq_enabled():
            return False
        cpsr = self.uc.reg_read(UC_ARM_REG_CPSR)
        if cpsr & 0x80:  # CPSR.I: interrupts masked
            # ARMv7 WFI wakes on a pending IRQ even with CPSR.I=1; the kernel
            # then executes the post-WFI return + `cpsie i` to take the IRQ.
            # Unicorn exits emu_start right after WFI, so step up to 8 insns
            # until CPSR.I clears rather than stalling indefinitely at WFI+4.
            pc_now = self.uc.reg_read(UC_ARM_REG_PC)
            is_wfi = False
            try:
                if cpsr & 0x20:
                    is_wfi = (pc_now >= 2 and int.from_bytes(
                        self._guest_read(pc_now - 2, 2), 'little') == 0xBF30)
                else:
                    is_wfi = (pc_now >= 4 and int.from_bytes(
                        self._guest_read(pc_now - 4, 4), 'little') == 0xE320F003)
            except Exception:
                is_wfi = False
            if is_wfi:
                for _ in range(8):
                    cur_pc = self.uc.reg_read(UC_ARM_REG_PC)
                    cur_cpsr = self.uc.reg_read(UC_ARM_REG_CPSR)
                    if not (cur_cpsr & 0x80):
                        cpsr = cur_cpsr
                        break
                    thumb = cur_cpsr & 0x20
                    self.uc.emu_start(cur_pc | (1 if thumb else 0), 0, count=1)
                else:
                    cpsr = self.uc.reg_read(UC_ARM_REG_CPSR)
            if cpsr & 0x80:
                return False
        if has_soft:
            irq = min(self._soft_pending_irqs)
            self._soft_pending_irqs.discard(irq)
        else:
            irq = self.timer_irq
            self._timer_irq_pending = 0
            self._jiffies_injected += 1
        pc = self.uc.reg_read(UC_ARM_REG_PC)
        if (cpsr & 0x1F) == 0x10:
            self.user_sp = self.uc.reg_read(UC_ARM_REG_SP)
            self.mode_sp['usr'] = self.user_sp
            self.mode_lr['usr'] = self.uc.reg_read(UC_ARM_REG_LR)
        # Enter ARM IRQ mode first, then write the *banked* SPSR_irq/LR_irq; SPSR is
        # per-mode, so writing it before the CPSR switch would hit SPSR_svc instead.
        # QEMU's AArch32 IRQ entry sets CPSR.A. Isolate that bit from the
        # other differences in the legacy synthetic IRQ entry (E0559).
        irq_cpsr = 0xD2 | (0x100 if self.mask_async_abort_on_irq else 0)
        self.uc.reg_write(UC_ARM_REG_CPSR, irq_cpsr)
        self.uc.reg_write(UC_ARM_REG_SPSR, cpsr)
        self.uc.reg_write(UC_ARM_REG_LR, pc + 4)
        self.uc.reg_write(UC_ARM_REG_PC, self.vectors_base + 0x18)
        # ARM exception entry clears the exclusive monitor (spec); our IRQ entry
        # must do the same for a faithful LDREX/STREX retry loop.
        self._exclusive_monitor = None
        self._gic_active_irq = irq
        self._gic_iar_served = False
        if len(self._injection_pcs) < 20:
            self._injection_pcs.append({'pc': f'0x{pc:08x}',
                                        'cpsr': f'0x{cpsr:08x}',
                                        'irq_cpsr': f'0x{irq_cpsr:08x}'})
        return True

    # ------------------------------------------------------- exclusive fallback
    PAGE_OFFSET = 0x60000000

    def _guest_read(self, va: int, size: int) -> bytes:
        if self._mmu_enabled:
            pa = self._translate_virtual(va)
            if pa is None:
                raise MachineError(f'unmapped guest VA 0x{va:08x}')
        else:
            pa = va - self.PAGE_OFFSET if va >= self.PAGE_OFFSET else va
        return bytes(self.uc.mem_read(pa, size))

    def _guest_write(self, va: int, data: bytes) -> None:
        pa = va - self.PAGE_OFFSET if va >= self.PAGE_OFFSET else va
        self.uc.mem_write(pa, data)

    def _emulate_exclusive(self, pc: int, cpsr: int) -> bool:
        """Emulate ARMv7 LDREX/STREX/CLREX/DMB when Unicorn raises an exception."""
        from capstone import CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, Cs
        from capstone.arm import ARM_OP_MEM, ARM_OP_REG

        import unicorn.arm_const as arm_const

        def register_index(name):
            name = name.lower()
            aliases = {'sb': 9, 'sl': 10, 'fp': 11, 'ip': 12,
                       'sp': 13, 'lr': 14, 'pc': 15}
            if name in aliases:
                return aliases[name]
            if name.startswith('r') and name[1:].isdigit():
                return int(name[1:])
            return None

        def read_register(index):
            if index == 13:
                return self.uc.reg_read(UC_ARM_REG_SP)
            if index == 14:
                return self.uc.reg_read(UC_ARM_REG_LR)
            if index == 15:
                return self.uc.reg_read(UC_ARM_REG_PC)
            return self.uc.reg_read(getattr(arm_const, f'UC_ARM_REG_R{index}'))

        def write_register(index, value):
            if index == 13:
                self.uc.reg_write(UC_ARM_REG_SP, value)
            elif index == 14:
                self.uc.reg_write(UC_ARM_REG_LR, value)
            elif index == 15:
                self.uc.reg_write(UC_ARM_REG_PC, value)
            else:
                self.uc.reg_write(getattr(arm_const, f'UC_ARM_REG_R{index}'), value)

        thumb = bool(cpsr & 0x20)
        try:
            raw = self._guest_read(pc, 4)
        except Exception:
            return False
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB if thumb else CS_MODE_ARM)
        md.detail = True
        instruction = next(md.disasm(raw, pc), None)
        if instruction is None:
            return False
        mnemonic = instruction.mnemonic
        size = instruction.size
        if mnemonic in ('dmb', 'dsb', 'isb', 'clrex'):
            if mnemonic == 'clrex':
                self._exclusive_monitor = None
            self.uc.reg_write(UC_ARM_REG_PC, pc + size)
            return True
        memory = next((op for op in instruction.operands if op.type == ARM_OP_MEM),
                      None)
        registers = [register_index(instruction.reg_name(op.reg))
                     for op in instruction.operands if op.type == ARM_OP_REG]
        registers = [index for index in registers if index is not None]
        if memory is None or not registers:
            return False
        address = read_register(register_index(instruction.reg_name(memory.mem.base)))
        address = (address + memory.mem.disp) & 0xFFFFFFFF
        try:
            widths = {'ldrex': 4, 'ldrexb': 1, 'ldrexh': 2, 'ldrexd': 8,
                      'strex': 4, 'strexb': 1, 'strexh': 2, 'strexd': 8}
            if mnemonic in ('ldrex', 'ldrexb', 'ldrexh', 'ldrexd'):
                width = widths[mnemonic]
                data = self._guest_read(address, width)
                value = int.from_bytes(data, 'little')
                self._exclusive_monitor = (address, value)
                write_register(registers[0], value & 0xFFFFFFFF)
                if width == 8 and len(registers) > 1:
                    write_register(registers[1], (value >> 32) & 0xFFFFFFFF)
                self.uc.reg_write(UC_ARM_REG_PC, pc + size)
                return True
            if mnemonic in ('strex', 'strexb', 'strexh', 'strexd'):
                width = widths[mnemonic]
                data_reg = registers[1] if len(registers) > 1 else registers[0]
                value = read_register(data_reg)
                if width == 8:
                    pair = [read_register(data_reg), read_register(data_reg + 1)]
                    value |= pair[1] << 32
                monitor_ok = (self._exclusive_monitor is not None
                              and self._exclusive_monitor[0] == address)
                if monitor_ok:
                    self._guest_write(address, value.to_bytes(width, 'little'))
                    write_register(registers[0], 0)
                else:
                    write_register(registers[0], 1)
                self._exclusive_monitor = None
                self.uc.reg_write(UC_ARM_REG_PC, pc + size)
                return True
        except Exception:
            return False
        return False

    def _in_demand_page_helper(self, pc: int) -> bool:
        if not self._symbol_addresses:
            return False
        import bisect
        index = bisect.bisect_right(self._symbol_addresses, pc) - 1
        if index < 0:
            return False
        symbol_pc = self._symbol_addresses[index]
        name = None
        for candidate, value in self.symbols.items():
            if value == symbol_pc:
                name = candidate
                break
        return name in self.demand_page_helpers

    #: Scratch frame used to emulate the ARM Linux vector-stub entry when the
    #: CPU's per-mode SP banking cannot be modelled by Unicorn (E0430).
    VECTOR_SCRATCH_PA = 0x02002000

    def _symbol_address(self, name: str) -> int | None:
        for symbol_name, value in self.symbols.items():
            if symbol_name == name:
                return value
        return None

    def _emulate_banked_restore(self, pc: int) -> None:
        """Emulate ret_to_user's `ldmdb sp, {r0..ip, sp, lr}^` (E0432).

        On real ARMv7 the `^` form loads the USR-bank SP/LR, leaving the SVC LR
        (which holds pt_regs->ARM_pc) intact so `movs pc, lr` returns to user.
        Unicorn shares the registers, so we restore r0..r12 directly and keep
        the user SP/LR in our banked state for the user-mode restore hook.
        """
        sp = self.uc.reg_read(UC_ARM_REG_SP)
        count = 15
        pa = self._kernel_phys(sp)
        try:
            raw = self.uc.mem_read(pa - 4 * count, 4 * count)
        except Exception:
            self.uc.reg_write(UC_ARM_REG_PC, pc + 4)
            return
        values = list(struct.unpack('<%dI' % count, bytes(raw)))
        for index in range(13):
            self.uc.reg_write(_R_REG[index], values[index])
        self.mode_sp['usr'] = values[13]
        self.mode_lr['usr'] = values[14]
        self.user_sp = values[13]
        self._restore_user_sp = True
        self.banked_restore_hits += 1
        self.uc.reg_write(UC_ARM_REG_PC, pc + 4)

    def _install_kuser_hooks(self, tls_only: bool = False) -> None:
        """Emulate the ARM kuser helpers (E0434).

        The vector page helpers are user-callable code at 0xFFFF0FA0..0xFFFF0FFC.
        Under Unicorn the page is zero-backed, so a user call would fault; the
        common helpers (memory barrier, cmpxchg, get_tls, helper version) are
        emulated and returned to LR.
        """
        def record(pc, name):
            self.kuser_hits += 1
            if len(self.kuser_entries) < 32:
                self.kuser_entries.append({'pc': f'0x{pc:08x}', 'name': name})

        def memory_barrier(uc, address, size, user):
            record(0xFFFF0FA0, 'memory_barrier')
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

        def cmpxchg(uc, address, size, user):
            ptr = uc.reg_read(UC_ARM_REG_R0)
            old = uc.reg_read(UC_ARM_REG_R1)
            new = uc.reg_read(UC_ARM_REG_R2)
            try:
                current = struct.unpack('<I', uc.mem_read(ptr, 4))[0]
            except Exception:
                uc.reg_write(UC_ARM_REG_R0, 0xFFFFFFFF)
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
                return
            if current == old:
                try:
                    uc.mem_write(ptr, struct.pack('<I', new))
                except Exception:
                    uc.reg_write(UC_ARM_REG_R0, 0xFFFFFFFF)
                else:
                    uc.reg_write(UC_ARM_REG_R0, 0)
            else:
                uc.reg_write(UC_ARM_REG_R0, 0xFFFFFFFF)
            record(0xFFFF0FC0, 'cmpxchg')
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

        def get_tls(uc, address, size, user):
            record(0xFFFF0FE0, 'get_tls')
            try:
                tpidruro = uc.reg_read(UC_ARM_REG_C13_C0_3) & 0xFFFFFFFF
            except Exception:
                tpidruro = 0
            val = tpidruro if tpidruro else (self.tls_value & 0xFFFFFFFF)
            uc.reg_write(UC_ARM_REG_R0, val)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

        def helper_version(uc, address, size, user):
            record(0xFFFF0FFC, 'helper_version')
            uc.reg_write(UC_ARM_REG_R0, 5)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

        hooks = ((0xFFFF0FE0, get_tls),) if tls_only else (
            (0xFFFF0FA0, memory_barrier), (0xFFFF0FC0, cmpxchg),
            (0xFFFF0FE0, get_tls), (0xFFFF0FFC, helper_version))
        for pc, handler in hooks:
            self.uc.hook_add(UC_HOOK_CODE, handler, begin=pc, end=pc + 3)

    def _svc_return_address(self, pc: int, thumb: bool) -> int:
        """E0496/E0500: compute the true SVC return address.

        Unicorn reports the PC *after* the SVC instruction in UC_HOOK_INTR.  The
        probe must use the guest page-table walk (direct uc.mem_read from a hook
        can fail for user VAs, which previously fell back to pc+2 and skipped
        the user epilogue `pop {r7, pc}` at 0x0403EB16).
        """
        def _read16(addr: int):
            # E0500: never run a nested MRC/emu_start from inside the INT hook.
            if self._cached_ttbr0:
                try:
                    value = self._read_user16(addr)
                    if value is not None:
                        return value
                except Exception:
                    pass
            try:
                return struct.unpack('<H', bytes(self.uc.mem_read(addr, 2)))[0]
            except Exception:
                return None

        def _read32(addr: int):
            if self._cached_ttbr0:
                try:
                    value = self._read_user32(addr)
                    if value is not None:
                        return value
                except Exception:
                    pass
            try:
                return struct.unpack('<I', bytes(self.uc.mem_read(addr, 4)))[0]
            except Exception:
                return None

        if thumb:
            before = _read16((pc - 2) & ~1)
            if before is not None and (before & 0xFF00) == 0xDF00:
                return pc
            after = _read16(pc & ~1)
            if after is not None and (after & 0xFF00) == 0xDF00:
                return pc + 2
            return pc + 2
        before = _read32((pc - 4) & ~3)
        if before is not None and (before & 0xFF000000) == 0xEF000000:
            return pc
        after = _read32(pc & ~3)
        if after is not None and (after & 0xFF000000) == 0xEF000000:
            return pc + 4
        return pc + 4

    def _emulate_vector_stmdb(self, uc) -> bool:
        """E0498/E0500: emulate `stmdb r8,{sp,lr}^` inside vector_swi from the
        pre-switch USR bank capture; returns True after a single application."""
        sp = self._pending_swi_usr_sp
        lr = self._pending_swi_usr_lr
        if sp is None or lr is None:
            return False
        try:
            r8 = uc.reg_read(UC_ARM_REG_R8)
            pa_sp = self._kernel_phys((r8 - 8) & 0xFFFFFFFF)
            pa_lr = self._kernel_phys((r8 - 4) & 0xFFFFFFFF)
            uc.mem_write(pa_sp, struct.pack('<I', sp & 0xFFFFFFFF))
            uc.mem_write(pa_lr, struct.pack('<I', lr & 0xFFFFFFFF))
        except Exception:
            return False
        self._pending_swi_usr_sp = None
        self._pending_swi_usr_lr = None
        if len(self.e0497_trace) < 16:
            self.e0497_trace.append({
                'tag': 'vector_stmdb', 'r8': f'0x{r8:08x}',
                'sp_usr': f'0x{sp:08x}', 'lr_usr': f'0x{lr:08x}'})
        return True

    def _request_direct_map_tlb(self, uc, address) -> bool:
        """Keep the kernel direct map after paging_init switches TTBR.

        QEMU at the same ``paging_init`` store (``0x60494b50``) still reads
        the direct-map page. A TTBR write does not drop that TLB entry, and
        the new L1 is empty. Unicorn's CPU walker faults. The virtual TLB
        already maps ``PAGE_OFFSET`` linearly, which is that retained entry.
        """
        if self.virtual_tlb or getattr(self, '_direct_map_vtlb', False):
            return False
        if not (self.PAGE_OFFSET <= address < self.PAGE_OFFSET + 0x40000000):
            return False
        if not self._sctlr_mmu_on():
            return False
        try:
            ttbr = self._read_cp15_ttbr0() & ~0x3FFF
            l1 = struct.unpack('<I', uc.mem_read(
                ttbr + ((address >> 20) & 0xFFF) * 4, 4))[0]
        except Exception:
            return False
        if l1 & 3:
            return False
        if not self._pa_mapped(address - self.PAGE_OFFSET):
            return False
        self._direct_map_vtlb = True
        self.direct_map_completions += 1
        uc.emu_stop()
        return True

    def _on_interrupt(self, uc, intno, user_data=None) -> None:
        """Dispatch ARM supervisor calls to the kernel SWI vector (E0436).

        Unicorn raises UC_ERR_EXCEPTION for SVC/SWI instead of modelling the
        exception; without this hook a user syscall is misclassified as an
        abort.  Real ARM semantics: switch to SVC, LR_svc = pc+4,
        SPSR_svc = old CPSR, PC = 0xFFFF0008 (vector_swi).
        """
        if intno in (1, 3, 4):
            # UC_HOOK_INTR consumes the exception. Leaving it pending makes the
            # next emu_start re-raise before the redirected PC retires.
            pc = uc.reg_read(UC_ARM_REG_PC)
            cpsr = uc.reg_read(UC_ARM_REG_CPSR)
            key = (pc & ~1, intno)
            if key == getattr(self, '_intr_key', None):
                self._intr_same = getattr(self, '_intr_same', 0) + 1
            else:
                self._intr_key = key
                self._intr_same = 1
            if self._intr_same > 8:
                self._intr_stopped = True
                uc.emu_stop()
                return
            if intno != 3 and self.exclusive_fallback and self._emulate_exclusive(pc, cpsr):
                # The exception stays pending inside this emu_start, so the
                # retired PC is fetched on the next slice.
                self._intr_key = None
                self._intr_same = 0
                self._exclusive_resume = True
                uc.emu_stop()
                return
            if intno == 3:
                if self._request_direct_map_tlb(uc, pc & ~1):
                    self._intr_key = None
                    self._intr_same = 0
                    return
                self._deliver_prefetch_abort(pc, cpsr, pc)
            else:
                pa = self._kernel_phys(pc) if pc >= self.PAGE_OFFSET else pc
                decoded = self._decode_fault_address(pc, pa)
                if decoded is not None:
                    address, write = decoded
                else:
                    address, write = self.last_dfar, False
                if self._request_direct_map_tlb(uc, address):
                    self._intr_key = None
                    self._intr_same = 0
                    return
                delivered = self._deliver_data_abort(pc, cpsr, address, write)
                # Leave the faulting TB. A fresh emu_start fetches the handler
                # after this hook has consumed the data abort.
                if delivered:
                    uc.emu_stop()
            return
        if intno != 2:  # UC_ARM_EXCEPTION_SUPERVISOR_CALL
            return
        pc = uc.reg_read(UC_ARM_REG_PC)
        cpsr = uc.reg_read(UC_ARM_REG_CPSR)
        self.svc_hits += 1
        if len(self.svc_trace) < 32:
            self.svc_trace.append({'pc': f'0x{pc:08x}', 'r7': uc.reg_read(UC_ARM_REG_R7)})
        thumb = bool(cpsr & 0x20)
        user_cpsr = (cpsr & ~0x3F) | 0x10 | (0x20 if thumb else 0)
        user_mode = (cpsr & 0x1F) == 0x10
        # E0498: capture the USER bank values *before* switching CPSR to SVC.
        self._pending_swi_usr_sp = None
        self._pending_swi_usr_lr = None
        if user_mode and 0x1000 <= (pc & ~1) < 0x60000000:
            try:
                self._pending_swi_usr_sp = uc.reg_read(UC_ARM_REG_SP)
                self._pending_swi_usr_lr = uc.reg_read(UC_ARM_REG_LR)
            except Exception:
                self._pending_swi_usr_sp = None
                self._pending_swi_usr_lr = None
        if len(self.e0497_trace) < 16:
            self.e0497_trace.append({'tag': 'swi_entry', 'pc': f'0x{pc:08x}',
                                     'cpsr': f'0x{cpsr:08x}',
                                     'mode': f'0x{cpsr & 0x1F:02x}',
                                     'user_mode': user_mode,
                                     'pend_sp': (f'0x{self._pending_swi_usr_sp:08x}'
                                                 if self._pending_swi_usr_sp is not None
                                                 else None)})
        target = (cpsr & ~0x3F) | 0x13 | 0x80   # SVC, IRQ masked, ARM state
        uc.reg_write(UC_ARM_REG_CPSR, target)
        uc.reg_write(UC_ARM_REG_SPSR, user_cpsr if user_mode else (cpsr & 0xFFFFFFFF))
        _ret_pc = self._svc_return_address(pc, thumb) & 0xFFFFFFFF
        uc.reg_write(UC_ARM_REG_LR, _ret_pc)
        _vector = self._symbol_address('vector_swi') or 0x6010DFA0
        uc.reg_write(UC_ARM_REG_PC, _vector)

    def _capture_fault_frame(self, pc: int) -> None:
        """Dump r0/r1/r2, SP and the 18-word pt_regs [r2] at do_page_fault entry."""
        if len(self.do_page_fault_frames) >= 16:
            return
        r2 = self.uc.reg_read(UC_ARM_REG_R2)
        frame = {
            'pc': f'0x{pc:08x}',
            'r0': f'0x{self.uc.reg_read(UC_ARM_REG_R0):08x}',
            'r1': f'0x{self.uc.reg_read(UC_ARM_REG_R1):08x}',
            'r2': f'0x{r2:08x}',
            'sp': f'0x{self.uc.reg_read(UC_ARM_REG_SP):08x}',
            'cpsr': f'0x{self.uc.reg_read(UC_ARM_REG_CPSR):08x}',
        }
        # pt_regs lives in kernel virtual space (0x60000000+); translate the
        # kernel linear map to its physical address for Unicorn mem_read.
        pa = r2
        if self.PAGE_OFFSET and self.PAGE_OFFSET <= r2 < self.PAGE_OFFSET + 0x80000000:
            pa = r2 - self.PAGE_OFFSET
        try:
            raw = bytes(self.uc.mem_read(pa, 0x48))
            frame['pt_regs'] = [f'0x{v:08x}'
                                for v in struct.unpack('<18I', raw)]
        except Exception as error:
            frame['pt_regs_error'] = str(error)
        self.do_page_fault_frames.append(frame)

    def _install_fault_mrc_hooks(self) -> None:
        """Inject saved IFAR/IFSR/DFAR/DFSR into the kernel v7_* helpers (E0433).

        Unicorn does not expose the CP15 fault registers written by our emulated
        exceptions to the kernel's later MRC reads, so patch the four read sites
        directly: r0/r1 are filled from the saved fault state and the MRC is
        skipped.
        """
        pabt_base = self._symbol_address('v7_pabort') or 0x60119B60
        dabt_base = self._symbol_address('v7_early_abort') or 0x60119B40
        pabt_mrc_r0, pabt_mrc_r1 = pabt_base, pabt_base + 4
        dabt_mrc_r1, dabt_mrc_r0 = dabt_base + 4, dabt_base + 8

        def make_hook(reg, getter, counter):
            def hook(uc, address, size, user):
                uc.reg_write(reg, getter() & 0xFFFFFFFF)
                setattr(self, counter, getattr(self, counter) + 1)
                uc.reg_write(UC_ARM_REG_PC, address + 4)
            return hook

        self.uc.hook_add(UC_HOOK_CODE,
                         make_hook(UC_ARM_REG_R0, lambda: self.last_ifar,
                                   'pabt_mrc_hits'),
                         begin=pabt_mrc_r0, end=pabt_mrc_r0 + 3)
        self.uc.hook_add(UC_HOOK_CODE,
                         make_hook(UC_ARM_REG_R1, lambda: self.last_ifsr,
                                   'pabt_mrc_hits'),
                         begin=pabt_mrc_r1, end=pabt_mrc_r1 + 3)
        self.uc.hook_add(UC_HOOK_CODE,
                         make_hook(UC_ARM_REG_R1, lambda: self.last_dfsr,
                                   'dabt_mrc_hits'),
                         begin=dabt_mrc_r1, end=dabt_mrc_r1 + 3)
        self.uc.hook_add(UC_HOOK_CODE,
                         make_hook(UC_ARM_REG_R0, lambda: self.last_dfar,
                                   'dabt_mrc_hits'),
                         begin=dabt_mrc_r0, end=dabt_mrc_r0 + 3)

    def _install_vfp_mvfr0_hook(self) -> None:
        """Report D16 at the boot-time probe, without changing the execution CPU."""
        vfp_base = self._symbol_address('__boottime_vfp_init')
        if vfp_base is None:
            raise MachineError('D16 capability override requires __boottime_vfp_init')
        offset = vfp_base - self.PAGE_OFFSET - self.kernel_load
        if offset < 0 or offset % 4 or offset + 4 > len(self.kernel_raw):
            raise MachineError('D16 capability override requires a kernel ARM probe')
        window = self.kernel_raw[offset:min(len(self.kernel_raw), offset + 0x160)]
        sites = []
        for rel in range(0, len(window) - 3, 4):
            word = int.from_bytes(window[rel:rel + 4], 'little')
            if word & 0xFFFF0FFF == 0xEEF70A10:
                sites.append((vfp_base + rel, (word >> 12) & 0xF))
        if len(sites) != 1 or sites[0][1] >= len(_R_REG):
            raise MachineError('D16 capability override requires one supported VMRS MVFR0 site')
        site, rt = sites[0]

        def mvfr0_hook(uc, address, size, user):
            if size != 4 or uc.reg_read(UC_ARM_REG_CPSR) & 0x3F != 0x13:
                return
            # Cortex-A5's value has MVFR0[3:0]=1. The original guest branch
            # derives HWCAP_VFPv3D16; neither elf_hwcap nor auxv is patched.
            uc.reg_write(_R_REG[rt], 0x10110221)
            self.vfp_mvfr0_d16_hits += 1
            uc.reg_write(UC_ARM_REG_PC, address + 4)

        self.uc.hook_add(UC_HOOK_CODE, mvfr0_hook, begin=site, end=site)
        self.vfp_mvfr0_sites.append(f'0x{site:08x}')

    def _deliver_svc_exception(self, pc: int, cpsr: int, kind: str) -> bool:
        """Emulate vector_stub entry for SVC-mode aborts (E0435).

        __dabt_svc/__pabt_svc expect r0 = 12-byte scratch frame {r0, pc, spsr}
        and run on the live SVC kernel stack.  Unicorn banks SP, so the current
        SP is the correct kernel stack and must be preserved; LR still holds the
        fault-time kernel LR and must not be touched.
        """
        if kind == 'dabt':
            handler = self._symbol_address('__dabt_svc')
        elif kind == 'und':
            handler = self._symbol_address('__und_svc')
        else:
            handler = self._symbol_address('__pabt_svc')
        if handler is None:
            return False
        r0 = self.uc.reg_read(UC_ARM_REG_R0)
        saved_pc = (((pc & ~1) + (2 if (cpsr & 0x20) else 4))
                    if kind == 'und' else pc)
        frame = struct.pack(
            '<3I', r0 & 0xFFFFFFFF, saved_pc & 0xFFFFFFFF, cpsr & 0xFFFFFFFF)
        scratch_pa = self.VECTOR_SCRATCH_PA + 0x10
        scratch_va = scratch_pa + self.PAGE_OFFSET
        # The fixed scratch sits at PA 0x02002010 (VA 0x62002010). Early
        # paging has not mapped that linear-map page yet, so __dabt_svc's
        # `ldm r0` faults and the exception sticks. Park the frame under the
        # live SVC stack, which is already mapped, when the guest walk misses.
        if self._sctlr_mmu_on() and self._walk_kernel_pt(scratch_va) is None:
            sp = self.uc.reg_read(UC_ARM_REG_SP)
            scratch_va = (sp - 0x60) & ~7
            scratch_pa = self._kernel_phys(scratch_va)
        self.uc.mem_write(scratch_pa, frame)
        current_mode = cpsr & 0x1F
        if current_mode == 0x10:
            self.user_sp = self.uc.reg_read(UC_ARM_REG_SP)
            self._restore_user_sp = True
        # Keep CPSR.A set. This boot enters with async aborts masked
        # (QEMU parity); clearing A here makes the next fetch raise a
        # prefetch abort before __dabt_svc's first instruction retires.
        self.uc.reg_write(UC_ARM_REG_CPSR, 0x13 | 0x1C0)
        self.uc.reg_write(UC_ARM_REG_R0, scratch_va)
        self.uc.reg_write(UC_ARM_REG_PC, handler)
        return True

    def _flush_tlb(self) -> None:
        """Invalidate translated code; SoftMMU entries are flushed separately."""
        self.uc.ctl_flush_tb()
        self._tlb_flushes += 1

    def _sctlr_mmu_on(self) -> bool:
        try:
            sctlr = self.uc.cpr_read(15, 0, 1, 0, 0, self._cp15_security(), False)
        except Exception:
            return False
        return bool(sctlr & 1)

    def _cp15_security(self) -> int:
        # Unicorn's CP-register selector is secure=1, non-secure=0, not CPSR.M.
        scr = self.uc.cpr_read(15, 0, 1, 1, 0, 1, False)
        return int(not (scr & 1))

    def _read_cp15_ttbr0(self) -> int:
        """Read the active TTBR0 without executing guest code from a hook."""
        value = self.uc.cpr_read(15, 0, 2, 0, 0, self._cp15_security(), False)
        self._cached_ttbr0 = value & 0xFFFFC000
        return value

    _REG_BY_NAME = {'r0': UC_ARM_REG_R0, 'r1': UC_ARM_REG_R1, 'r2': UC_ARM_REG_R2,
                    'r3': UC_ARM_REG_R3, 'r4': UC_ARM_REG_R4, 'r5': UC_ARM_REG_R5,
                    'r6': UC_ARM_REG_R6, 'r7': UC_ARM_REG_R7, 'r8': UC_ARM_REG_R8,
                    'r9': UC_ARM_REG_R9, 'r10': UC_ARM_REG_R10, 'r11': UC_ARM_REG_R11,
                    'r12': UC_ARM_REG_R12, 'sp': UC_ARM_REG_SP, 'lr': UC_ARM_REG_LR,
                    'sb': UC_ARM_REG_R9, 'sl': UC_ARM_REG_R10, 'fp': UC_ARM_REG_R11,
                    'ip': UC_ARM_REG_R12}

    def _decode_fault_address(self, pc: int, pa: int) -> tuple[int, bool] | None:
        """Decode the faulting load/store [base,offset] and return (VA, is_write)."""
        thumb = bool(self.uc.reg_read(UC_ARM_REG_CPSR) & 0x20)
        if pa is None:
            return None
        try:
            import capstone
            raw = bytes(self.uc.mem_read(int(pa) & ~1, 4))
            md = capstone.Cs(capstone.CS_ARCH_ARM,
                             capstone.CS_MODE_THUMB if thumb else capstone.CS_MODE_ARM)
            insn = next(iter(md.disasm(raw, pc & ~1)), None)
            if insn is None:
                return None
            mnemonic = insn.mnemonic.lower()
            is_write = mnemonic.startswith(('str', 'stm', 'push', 'vst'))
            if '[' not in insn.op_str:
                if mnemonic.startswith(('push', 'pop', 'vpush', 'vpop')):
                    sp = self.uc.reg_read(UC_ARM_REG_SP)
                    nregs = insn.op_str.count(',') + 1
                    addr = (sp - 4 * nregs) if is_write else sp
                    return addr & 0xFFFFFFFF, is_write
                if mnemonic.startswith(('stm', 'ldm', 'vst', 'vld')) and '{' in insn.op_str:
                    bname = insn.op_str.split('{', 1)[0].rstrip(', !').strip().lower()
                    breg = self._REG_BY_NAME.get(bname)
                    if breg is not None:
                        bval = self.uc.reg_read(breg)
                        nregs = insn.op_str.split('{', 1)[1].count(',') + 1
                        if 'db' in mnemonic:
                            addr = bval - 4 * nregs
                        elif 'ib' in mnemonic:
                            addr = bval + 4
                        elif 'da' in mnemonic:
                            addr = bval - 4 * (nregs - 1)
                        else:
                            addr = bval
                        return addr & 0xFFFFFFFF, is_write
                return None
            mem = insn.op_str.split('[', 1)[1].split(']', 1)[0]
            parts = [part.strip() for part in mem.split(',')]
            base_name = parts[0].lower()
            if base_name == 'pc':
                address = (((pc & ~1) + 4) & ~3) if thumb else ((pc & ~3) + 8)
            else:
                base_reg = self._REG_BY_NAME.get(base_name)
                if base_reg is None:
                    return None
                address = self.uc.reg_read(base_reg)
            if len(parts) > 1:
                off = parts[1].strip()
                if off.startswith('#'):
                    address += int(off[1:], 0)
                else:
                    neg = off.startswith('-')
                    rname = (off[1:].strip() if neg else off).lower()
                    if rname not in self._REG_BY_NAME:
                        return None
                    idx_val = self.uc.reg_read(self._REG_BY_NAME[rname]) & 0xFFFFFFFF
                    if len(parts) > 2:
                        spart = parts[2].strip().lower()
                        if spart.startswith('lsl #'):
                            idx_val = (idx_val << int(spart.split('#', 1)[1], 0)) & 0xFFFFFFFF
                        elif spart.startswith('lsr #'):
                            idx_val = (idx_val >> int(spart.split('#', 1)[1], 0)) & 0xFFFFFFFF
                    address += (-idx_val if neg else idx_val)
            return address & 0xFFFFFFFF, is_write
        except Exception:
            return None

    def _pte_report(self, va: int) -> dict:
        """Re-walk TTBR0 -> L1 -> L2 for a VA and decode the ARMv7 PTE bits."""
        ttbr0 = ((self._cached_ttbr0
                  or self._read_cp15_ttbr0()) & 0xFFFFC000)
        report = {'va': f'0x{va:08x}', 'ttbr0': f'0x{ttbr0:08x}'}
        try:
            l1 = struct.unpack('<I', self.uc.mem_read(
                ttbr0 + ((va >> 20) & 0xFFF) * 4, 4))[0]
            report['l1'] = f'0x{l1:08x}'
            if l1 & 2:
                pa = (l1 & 0xFFF00000) | (va & 0x000FFFFF)
                report.update({'section': True, 'pa': f'0x{pa:08x}'})
                return report
            if not (l1 & 1):
                report['valid'] = False
                return report
            l2pa = l1 & 0xFFFFFC00
            l2 = struct.unpack('<I', self.uc.mem_read(
                l2pa + ((va >> 12) & 0xFF) * 4, 4))[0]
            report['l2'] = f'0x{l2:08x}'
            report['valid'] = bool(l2 & 2)
            if l2 & 2:
                pa = (l2 & 0xFFFFF000) | (va & 0xFFF)
                report['pa'] = f'0x{pa:08x}'
                hw = l2 - 0x800 if (l2 & 0xFFF) >= 0x800 else l2
                report['hw_pte'] = f'0x{hw:08x}'
                report['ap'] = f'0x{(l2 >> 4) & 3:02x}'
                report['tex'] = f'0x{(l2 >> 12) & 7:02x}'
                report['xn'] = bool(l2 & (1 << 4)) if False else None
                report['c'] = bool(l2 & 8)
                report['b'] = bool(l2 & 4)
        except Exception as error:
            report['error'] = str(error)
        return report

    def _flush_cputlb(self) -> None:
        """Invalidate SoftMMU entries without nested emu_start or bank changes."""
        self.uc.ctl(UC_CTL_TLB_FLUSH, UC_CTL_IO_WRITE)
        self.cputlb_flushes += 1

    def _walk_kernel_pt(self, vaddr: int) -> int | None:
        """Walk the kernel L1 tables (TTBR1 and/or swapper_pg_dir) for a high VA.

        The exact TTBR1 L1 base/index encoding depends on TTBCR.N, so a small set
        of candidate bases/indices is tried and only a translation that lands in
        mapped RAM/MMIO is accepted (E0449).
        """
        candidates = []
        idx = (vaddr >> 20) & 0xFFF
        for base in (self._cached_ttbr1,):
            if base:
                candidates.append((base & ~0x3FFF, idx))
                candidates.append((base & ~0x3FFF, idx & 0x3FF))
        sym = self._symbol_address('swapper_pg_dir')
        if sym is not None:
            try:
                pgd = self._kernel_phys(sym) & 0xFFFFC000
                candidates.append((pgd, idx))
                candidates.append((pgd, idx & 0x3FF))
                candidates.append((pgd - 0x2000, idx))
                candidates.append((pgd - 0x4000, idx))
            except Exception:
                pass
        for base, index in candidates:
            try:
                entry_addr = (base + index * 4) & 0xFFFFFFFF
                l1 = struct.unpack('<I', self.uc.mem_read(entry_addr, 4))[0]
            except Exception:
                continue
            pa = None
            if l1 & 2:
                pa = (l1 & 0xFFF00000) | (vaddr & 0x000FFFFF)
            elif l1 & 1:
                try:
                    l2 = struct.unpack('<I', self.uc.mem_read(
                        (l1 & 0xFFFFFC00) + ((vaddr >> 12) & 0xFF) * 4, 4))[0]
                except Exception:
                    l2 = 0
                if l2 & 2:
                    pa = (l2 & 0xFFFFF000) | (vaddr & 0xFFF)
            if pa is not None and self._pa_mapped(pa):
                return pa
        return None

    def _read32_phys(self, va: int) -> int:
        try:
            return struct.unpack('<I', self.uc.mem_read(self._kernel_phys(va), 4))[0]
        except Exception:
            return 0

    def _read32_kernel(self, va: int) -> int:
        return struct.unpack('<I', self.uc.mem_read(self._kernel_phys(va), 4))[0]

    def _user_pa(self, va: int):
        try:
            r = self._walk_user_va(va)
        except Exception:
            return None
        if r is None:
            return None
        if isinstance(r, dict):
            v = r.get('pa')
            if isinstance(v, str):
                try:
                    return int(v, 16)
                except Exception:
                    return None
            return v
        return r

    def _read_user32(self, va: int):
        pa = self._user_pa(va)
        if pa is None:
            return None
        try:
            return struct.unpack('<I', self.uc.mem_read(pa, 4))[0]
        except Exception:
            return None

    def _read_user_bytes(self, va: int, size: int):
        pa = self._user_pa(va)
        if pa is None:
            return None
        try:
            return bytes(self.uc.mem_read(pa, size))
        except Exception:
            return None

    def _read_user16(self, va: int):
        pa = self._user_pa(va)
        if pa is None:
            return None
        try:
            return struct.unpack('<H', self.uc.mem_read(pa, 2))[0]
        except Exception:
            return None

    def _read_user8(self, va: int):
        pa = self._user_pa(va)
        if pa is None:
            return None
        try:
            return self.uc.mem_read(pa, 1)[0]
        except Exception:
            return None

    def _write_user32(self, va: int, val: int) -> bool:
        pa = self._user_pa(va)
        if pa is None:
            return False
        try:
            self.uc.mem_write(pa, struct.pack('<I', val & 0xFFFFFFFF))
            return True
        except Exception:
            return False

    @staticmethod
    def _cond_true(cc: int, cpsr: int) -> bool:
        n = bool(cpsr & 0x80000000)
        z = bool(cpsr & 0x40000000)
        c = bool(cpsr & 0x20000000)
        v = bool(cpsr & 0x10000000)
        return {
            0: z, 1: not z, 2: c, 3: not c, 4: n, 5: not n,
            6: v, 7: not v, 8: c and not z, 9: (not c) or z,
            10: n == v, 11: n != v, 12: (not z) and (n == v),
            13: z or (n != v), 14: True,
        }.get(cc, True)

    @staticmethod
    def _nz(cpsr: int, val: int) -> int:
        cpsr &= ~0xC0000000
        if val & 0x80000000:
            cpsr |= 0x80000000
        elif val == 0:
            cpsr |= 0x40000000
        return cpsr

    def _reg_name_to_id(self, md):
        cache = getattr(self, '_cap_reg_ids', None)
        if cache is None:
            cache = {}
            for rid in range(0, 260):
                try:
                    name = md.reg_name(rid)
                except Exception:
                    name = None
                if name:
                    cache[name] = rid
            self._cap_reg_ids = cache
        return cache

    def _take_user_frame(self):
        """E0492: consume the generation-tagged pt_regs frame at most once."""
        frame = self.pt_frame_last
        if frame is None:
            return None
        if frame['gen'] <= self.pt_frame_consumed:
            return None
        self.pt_frame_consumed = frame['gen']
        return frame

    def _should_recover_insn_invalid(self, pc: int, message: str) -> bool:
        """E0473: top-of-classifier decode-failure recovery gate."""
        if not (0x1000 <= (pc & ~1) < 0x60000000):
            return False
        if 'INSN' not in (message or '').upper():
            return False
        return self._user_pa(pc & ~1) is not None

    def _sync_user_bank_for_recovery(self, pc: int, usr_cpsr: int) -> bool:
        """E0494: if the live SP is a kernel pointer, force USR|Thumb and load
        r0-r12/SP/LR from the active pt_regs frame before manual stepping."""
        try:
            sp_live = self.uc.reg_read(UC_ARM_REG_SP)
        except Exception:
            sp_live = 0
        if sp_live < 0x60000000:
            return False
        frame = self.pt_frame_last
        self.uc.reg_write(UC_ARM_REG_CPSR, usr_cpsr)
        if (frame is None or (frame['cpsr'] & 0x1F) != 0x10
                or not 0x1000 <= frame['sp'] < 0x60000000):
            return False
        for i in range(13):
            self.uc.reg_write(UC_ARM_REG_R0 + i, frame['words'][i])
        self.uc.reg_write(UC_ARM_REG_SP, frame['sp'])
        self.uc.reg_write(UC_ARM_REG_LR, frame['lr'])
        if frame['pc'] == 0x04031AA8 and pc >= 0x04031AAC:
            # 0x04031AA8 push.w {r4-r11,lr} already ran before the fault.
            self.uc.reg_write(UC_ARM_REG_SP, (frame['sp'] - 0x24) & 0xFFFFFFFF)
        if len(self.e0493_trace) < 40:
            self.e0493_trace.append({
                'tag': 'recover_usr_sync', 'frame_gen': frame['gen'],
                'frame_pc': f"0x{frame['pc']:08x}",
                'sp': f'0x{self.uc.reg_read(UC_ARM_REG_SP):08x}',
                'r0': f'0x{self.uc.reg_read(UC_ARM_REG_R0):08x}',
                'r8': f'0x{self.uc.reg_read(UC_ARM_REG_R8):08x}'})
        return True

    def _step_user_thumb_insns(self, pc: int, cpsr: int, max_steps: int = 6):
        """E0470: bounded general Thumb/Thumb-2 user stepper."""
        from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs
        from capstone.arm import (ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG,
                                  ARM_REG_SP)
        if not (cpsr & 0x20):
            return None
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
        md.detail = True
        reg_ids = self._reg_name_to_id(md)
        trace = []
        cur = pc & ~1
        for _ in range(max_steps):
            code = self._read_user_bytes(cur, 4) or self._read_user_bytes(cur, 2)
            if not code:
                return (cur, cpsr, trace) if trace else None
            insns = list(md.disasm(code, cur))
            if not insns:
                return None
            insn = insns[0]
            op = insn.mnemonic
            ops = insn.operands
            nxt = cur + insn.size
            try:
                if op.startswith(('push', 'stmdb')):
                    names = insn.op_str.replace('push.w', '').replace(
                        'push', '').strip().strip('{}').replace(' ', '')
                    regs = [reg_ids[n] for n in names.split(',') if n in reg_ids]
                    if not regs:
                        return None
                    sp = self.uc.reg_read(ARM_REG_SP) - 4 * len(regs)
                    for i, rid in enumerate(sorted(regs)):
                        if not self._write_user32(sp + 4 * i,
                                                  self.uc.reg_read(rid)):
                            return (cur, cpsr, trace) if trace else None
                    self.uc.reg_write(ARM_REG_SP, sp)
                    trace.append(('push', f'0x{cur:08x}', insn.op_str))
                    cur = nxt
                    continue
                if op.startswith(('pop', 'ldm')):
                    names = insn.op_str.replace('pop.w', '').replace(
                        'pop', '').replace('ldm', '').replace('ia', '')
                    names = names.strip().strip('{}').replace(' ', '')
                    regs = [reg_ids[n] for n in names.split(',') if n in reg_ids]
                    if not regs:
                        return None
                    sp = self.uc.reg_read(ARM_REG_SP)
                    for i, rid in enumerate(sorted(regs)):
                        if rid == 15:
                            val = self._read_user32(sp + 4 * i)
                            if val is None:
                                return None
                            trace.append(('pop-pc', f'0x{cur:08x}', f'0x{val:08x}'))
                            return val, cpsr, trace
                        val = self._read_user32(sp + 4 * i)
                        if val is None:
                            return None
                        self.uc.reg_write(rid, val)
                    self.uc.reg_write(ARM_REG_SP, sp + 4 * len(regs))
                    trace.append(('pop', f'0x{cur:08x}', insn.op_str))
                    cur = nxt
                    continue
                if op in ('mov', 'movs', 'mov.w') and len(ops) == 2:
                    if ops[0].type != ARM_OP_REG:
                        return None
                    if ops[1].type == ARM_OP_REG:
                        val = self.uc.reg_read(ops[1].reg)
                    elif ops[1].type == ARM_OP_IMM:
                        val = ops[1].imm & 0xFFFFFFFF
                    else:
                        return None
                    self.uc.reg_write(ops[0].reg, val)
                    if op.endswith('s'):
                        cpsr = self._nz(cpsr, val)
                    trace.append(('mov', f'0x{cur:08x}', insn.op_str))
                    cur = nxt
                    continue
                if op.startswith(('add', 'sub')) and len(ops) >= 2:
                    if ops[0].type != ARM_OP_REG or ops[1].type != ARM_OP_REG:
                        return None
                    a = self.uc.reg_read(ops[1].reg)
                    if len(ops) == 3 and ops[2].type == ARM_OP_IMM:
                        b = ops[2].imm & 0xFFFFFFFF
                    elif len(ops) == 2:
                        b = 0
                    else:
                        return None
                    val = (a + b) if op.startswith('add') else (a - b)
                    val &= 0xFFFFFFFF
                    self.uc.reg_write(ops[0].reg, val)
                    if op.startswith('sub') and ops[0].reg == ARM_REG_SP:
                        pass
                    trace.append(('alu', f'0x{cur:08x}', insn.op_str))
                    cur = nxt
                    continue
                if op.startswith('ldr') and len(ops) == 2 and ops[1].type == ARM_OP_MEM:
                    base = ops[1].mem.base
                    disp = ops[1].mem.disp
                    if base == reg_ids.get('pc'):
                        addr = ((cur + 4) & ~3) + disp
                    else:
                        addr = (self.uc.reg_read(base) + disp) & 0xFFFFFFFF
                    if insn.size >= 4 and 'b' in op.split('.')[0] and not op.startswith(('ldrb', 'ldrh')):
                        val = self._read_user8(addr)
                    elif op.startswith('ldrh'):
                        val = self._read_user16(addr)
                    elif op.startswith('ldrb'):
                        val = self._read_user8(addr)
                    else:
                        val = self._read_user32(addr)
                    if val is None:
                        # E0474: unmapped literal (e.g. ld.so data page not resident
                        # yet) - keep the partial progress instead of aborting.
                        return (cur, cpsr, trace) if trace else None
                    self.uc.reg_write(ops[0].reg, val)
                    trace.append(('ldr', f'0x{cur:08x}', insn.op_str))
                    cur = nxt
                    continue
                if op.startswith('str') and len(ops) == 2 and ops[1].type == ARM_OP_MEM:
                    base = ops[1].mem.base
                    addr = (self.uc.reg_read(base) + ops[1].mem.disp) & 0xFFFFFFFF
                    val = self.uc.reg_read(ops[0].reg)
                    if not self._write_user32(addr, val):
                        return (cur, cpsr, trace) if trace else None
                    trace.append(('str', f'0x{cur:08x}', insn.op_str))
                    cur = nxt
                    continue
                if op.startswith('cmp') and len(ops) == 2:
                    a = self.uc.reg_read(ops[0].reg)
                    b = (ops[1].imm if ops[1].type == ARM_OP_IMM
                         else self.uc.reg_read(ops[1].reg))
                    res = (a - (b & 0xFFFFFFFF)) & 0xFFFFFFFF
                    cpsr = self._nz(cpsr, res)
                    cpsr &= ~0x20000000
                    if a >= (b & 0xFFFFFFFF):
                        cpsr |= 0x20000000
                    trace.append(('cmp', f'0x{cur:08x}', insn.op_str))
                    cur = nxt
                    continue
                if op in ('b', 'b.w'):
                    cur = ops[0].imm & 0xFFFFFFFF
                    trace.append(('b', f'0x{cur:08x}', insn.op_str))
                    continue
                if op.startswith('b') and op not in ('bic', 'bl', 'blx', 'bx') \
                        and len(ops) == 1 and ops[0].type == ARM_OP_IMM:
                    if self._cond_true(insn.cc, cpsr):
                        cur = ops[0].imm & 0xFFFFFFFF
                    else:
                        cur = nxt
                    trace.append(('bcond', f'0x{cur:08x}', insn.op_str))
                    continue
                if op in ('bl', 'blx') and ops and ops[0].type == ARM_OP_IMM:
                    self.uc.reg_write(UC_ARM_REG_LR, nxt | 1)
                    cur = ops[0].imm & 0xFFFFFFFF
                    trace.append(('bl', f'0x{cur:08x}', insn.op_str))
                    return cur, cpsr, trace
                if op == 'bx':
                    cur = self.uc.reg_read(ops[0].reg) & 0xFFFFFFFF
                    trace.append(('bx', f'0x{cur:08x}', insn.op_str))
                    return cur, cpsr, trace
                if op.startswith('cbz') or op.startswith('cbnz'):
                    if len(ops) == 2:
                        if op.startswith('cbz'):
                            taken = self.uc.reg_read(ops[0].reg) == 0
                        else:
                            taken = self.uc.reg_read(ops[0].reg) != 0
                        cur = (ops[1].imm & 0xFFFFFFFF) if taken else nxt
                        trace.append(('cbz', f'0x{cur:08x}', insn.op_str))
                        continue
                if op == 'nop':
                    cur = nxt
                    continue
                return None
            except Exception as _e:
                self._step_last_error = repr(_e)
                return None
        return cur, cpsr, trace

    def _emulate_thumb_prologue(self, pc: int, cpsr: int,
                                max_insns: int = 8):
        """E0469: bounded interpreter for the ld.so Thumb entry prologue."""
        from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs
        from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG, ARM_REG_PC
        if not (cpsr & 0x20):
            return None
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
        md.detail = True
        trace = []
        cur = pc & ~1
        for _ in range(max_insns):
            code = self._read_user_bytes(cur, 4)
            if code is None:
                return None
            insns = list(md.disasm(code, cur))
            if not insns:
                return None
            insn = insns[0]
            ops = insn.operands
            if insn.mnemonic in ('ldr', 'ldr.w') and len(ops) == 2 \
                    and ops[0].type == ARM_OP_REG and ops[1].type == ARM_OP_MEM \
                    and ops[1].mem.base == ARM_REG_PC:
                addr = ((cur + 4) & ~3) + ops[1].mem.disp
                val = self._read_user32(addr)
                if val is None:
                    return None
                self.uc.reg_write(ops[0].reg, val)
                trace.append(('ldr', f'0x{cur:08x}', f'0x{val:08x}'))
                cur += insn.size
                continue
            if insn.mnemonic == 'mov' and len(ops) == 2 \
                    and ops[0].type == ARM_OP_REG and ops[1].type == ARM_OP_REG:
                self.uc.reg_write(ops[0].reg, self.uc.reg_read(ops[1].reg))
                trace.append(('mov', f'0x{cur:08x}', insn.op_str))
                cur += insn.size
                continue
            if insn.mnemonic in ('bl', 'blx') and len(ops) == 1 \
                    and ops[0].type == ARM_OP_IMM:
                self.uc.reg_write(UC_ARM_REG_LR, (cur + insn.size) | 1)
                trace.append(('bl', f'0x{cur:08x}', f'0x{ops[0].imm:08x}'))
                cur = ops[0].imm
                return cur, cpsr, trace
            return None
        return None

    def _set_pc_and_cpsr(self, pc: int, cpsr: int) -> None:
        """E0464: Unicorn needs the PC LSB to reflect T and the CPSR write last."""
        lsb = 1 if (cpsr & 0x20) else 0
        self.uc.reg_write(UC_ARM_REG_CPSR, cpsr)
        self.uc.reg_write(UC_ARM_REG_PC, (pc & ~1) | lsb)
        try:
            if (self.uc.reg_read(UC_ARM_REG_CPSR) & 0x20) != (cpsr & 0x20):
                self.uc.reg_write(UC_ARM_REG_CPSR, cpsr)
        except Exception:
            pass

    @staticmethod
    def thumb_pc(pc: int, cpsr: int) -> int:
        """E0462: Unicorn couples CPSR.T to the PC LSB (even PC clears Thumb)."""
        return pc | 1 if (cpsr & 0x20) else pc & ~1

    def _find_pt_regs_base(self, sp: int) -> int | None:
        """E0461: locate the user pt_regs frame base near SP."""
        candidates = []
        ti = sp & ~0x1FFF
        candidates.extend((ti + 0x1FB0, ti + 0x1FB8, ti + 0x1FA8))
        for delta in range(-0x100, 0x104, 4):
            candidates.append(sp + delta)
        seen = set()
        for base in candidates:
            if base in seen:
                continue
            seen.add(base)
            try:
                cpsr = self._read32_phys(base + 0x40)
                pc = self._read32_phys(base + 0x3C)
            except Exception:
                continue
            if (cpsr & 0x1F) != 0x10:
                continue
            if 0x8000 <= pc < 0x60000000:
                return base
        return None

    def _record_user_frame(self, tag: str, sp: int) -> None:
        if len(self.pt_regs_user_frames) >= 64:
            return
        # debug: log near-miss frames whose cpsr slot looks like USR mode
        try:
            ti = sp & ~0x1FFF
            for probe in (ti + 0x1FB0, ti + 0x1FB8, ti + 0x1FA8):
                cpsr = self._read32_phys(probe + 0x40)
                pc = self._read32_phys(probe + 0x3C)
                lr = self._read32_phys(probe + 0x38)
                if (cpsr & 0x1F) == 0x10:
                    self.pt_regs_user_frames.append({
                        'tag': 'probe:' + tag, 'base': f'0x{probe:08x}',
                        'pc_slot': f'0x{pc:08x}', 'lr_slot': f'0x{lr:08x}',
                        'cpsr_slot': f'0x{cpsr:08x}'})
        except Exception:
            pass
        base = self._find_pt_regs_base(sp)
        if base is None:
            return
        words = [self._read32_phys(base + off) for off in range(0, 0x44, 4)]
        self.pt_regs_user_frames.append({
            'tag': tag, 'sp': f'0x{sp:08x}', 'base': f'0x{base:08x}',
            'r0': f'0x{words[0]:08x}', 'r1': f'0x{words[1]:08x}',
            'sp_slot': f'0x{words[0x34 // 4]:08x}',
            'lr_slot': f'0x{words[0x38 // 4]:08x}',
            'pc_slot': f'0x{words[0x3C // 4]:08x}',
            'cpsr_slot': f'0x{words[0x40 // 4]:08x}'})

    def _read_pt_regs_frame(self, sp: int, bias: int = 0) -> list[int]:
        return [self._read32_phys((sp - bias + off) & 0xFFFFFFFF)
                for off in range(0, 0x48, 4)]

    def _record_frame(self, tag: str, sp: int, bias: int = 0) -> None:
        if len(self.pt_regs_frames) >= 24:
            return
        try:
            words = self._read_pt_regs_frame(sp, bias)
            self.pt_regs_frames.append({
                'tag': tag, 'sp': f'0x{sp:08x}',
                'sp_slot': f'0x{words[0x34 // 4]:08x}',
                'lr_slot': f'0x{words[0x38 // 4]:08x}',
                'pc_slot': f'0x{words[0x3C // 4]:08x}',
                'cpsr_slot': f'0x{words[0x40 // 4]:08x}',
                'words': [f'0x{w:08x}' for w in words]})
        except Exception:
            pass

    def _resolve_user_pgd(self) -> int | None:
        """Resolve current->mm->pgd (E0453): thread_info = SP & ~0x1FFF,
        task = [ti+0xC], mm = [task+0xCC], pgd = [mm+8]."""
        try:
            sp = self.uc.reg_read(UC_ARM_REG_SP)
            ti = sp & ~0x1FFF
            task = self._read32_kernel(ti + 0xC)
            if not task:
                return None
            mm = self._read32_kernel(task + 0xCC)
            if not mm:
                mm = self._read32_kernel(task + 0xD0)  # active_mm fallback
            if not mm:
                return None
            pgd_va = self._read32_kernel(mm + 8)
            pgd_pa = self._kernel_phys(pgd_va) & 0xFFFFC000
            if 0x1000 <= pgd_pa < 0x80000000 and pgd_pa != 0x00104000:
                return pgd_pa
        except Exception:
            return None
        return None

    def _refresh_user_ttbr0(self) -> None:
        """Prefer the live CP15 TTBR0, fall back to current->mm->pgd."""
        try:
            value = self._read_cp15_ttbr0() & 0xFFFFC000
            if value and value != 0x00104000:
                self._cached_ttbr0 = value
                return
            if self._last_switch_mm_pgd and self._last_switch_mm_pgd != 0x00104000:
                self._cached_ttbr0 = self._last_switch_mm_pgd
                return
        except Exception:
            pass
        fallback = self._resolve_user_pgd()
        if fallback:
            self._cached_ttbr0 = fallback
        elif len(self.mm_resolve_trace) < 8:
            try:
                sp = self.uc.reg_read(UC_ARM_REG_SP)
                ti = sp & ~0x1FFF
                task = self._read32_kernel(ti + 0xC)
                mm = self._read32_kernel(task + 0xCC) if task else 0
                pgd_va = self._read32_kernel(mm + 8) if mm else 0
                self.mm_resolve_trace.append({
                    'sp': f'0x{sp:08x}', 'ti': f'0x{ti:08x}',
                    'task': f'0x{task:08x}', 'mm': f'0x{mm:08x}',
                    'pgd_va': f'0x{pgd_va:08x}',
                    'ttbr0_read': f'0x{value:08x}',
                    'last_switch': (f'0x{self._last_switch_mm_pgd:08x}'
                                    if self._last_switch_mm_pgd else None)})
            except Exception:
                pass

    @staticmethod
    def scan_ttbr0_write_sites(code: bytes, base: int) -> list[tuple[int, int]]:
        """Find mcr p15,0,Rt,c2,c0,0 (TTBR0 write) sites: (addr, Rt)."""
        sites = []
        for offset in range(0, len(code) - 3, 4):
            word = struct.unpack_from('<I', code, offset)[0]
            if (word & 0xFFFF0FFF) == 0xEE020F10:
                sites.append((base + offset, (word >> 12) & 0xF))
        # E0456: Thumb-2 32-bit encoding 0xEE02 / 0x0F10 (Rt in hw2[15:12])
        for offset in range(0, len(code) - 3, 2):
            hw1 = struct.unpack_from('<H', code, offset)[0]
            if hw1 != 0xEE02:
                continue
            hw2 = struct.unpack_from('<H', code, offset + 2)[0]
            if (hw2 & 0x0FFF) == 0x0F10:
                site = base + offset
                rt = (hw2 >> 12) & 0xF
                if (site, rt) not in sites:
                    sites.append((site, rt))
        sites.sort()
        return sites

    def _install_ttbr0_write_hooks(self) -> None:
        """Hook every TTBR0 write site and cache the pgd physical value (E0455)."""
        regs = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
                UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC]
        try:
            code = self.kernel_raw
            base = self.kernel_load
            if base < 0x60000000:
                # the raw image is loaded at a physical address; scan/hook it at
                # its kernel virtual address (E0455)
                base += 0x60000000
            sites = self.scan_ttbr0_write_sites(code, base)
        except Exception:
            return
        self.ttbr0_write_sites = [site for site, _ in sites][:16]
        for site, rt in sites[:16]:
            def hook(uc, hook_address, size, user, pc=site, rt=rt):
                try:
                    value = uc.reg_read(regs[rt]) & ~0x3FFF
                    self.ttbr0_write_hits += 1
                    if len(self.switch_mm_values) < 8:
                        self.switch_mm_values.append(value)
                    if 0x00100000 <= value < 0x80000000 and value != 0x00104000:
                        changed = value != self._cached_ttbr0
                        self._cached_ttbr0 = value
                        self._last_switch_mm_pgd = value
                        if changed and self.virtual_tlb:
                            self._flush_cputlb()
                except Exception:
                    pass
            try:
                self.uc.hook_add(UC_HOOK_CODE, hook, begin=site, end=site)
            except Exception:
                pass

    def _install_switch_mm_hook(self) -> None:
        """Hook cpu_v7_switch_mm(r0 = pgd phys) to track TTBR0 (E0453)."""
        try:
            address = self._symbol_address('cpu_v7_switch_mm')
        except Exception:
            address = None
        if address is None:
            # E0454: the decoded kallsyms used by the runner does not expose
            # cpu_v7_switch_mm; use the verified image address instead.
            address = 0x6011A26C
        self.switch_mm_address = address
        try:
            def switch_mm_hook(uc, hook_address, size, user, pc=address):
                try:
                    pgd = uc.reg_read(UC_ARM_REG_R0) & 0xFFFFC000
                    self.switch_mm_hits += 1
                    if len(self.switch_mm_values) < 8:
                        self.switch_mm_values.append(pgd)
                    if 0x1000 <= pgd < 0x80000000:
                        if pgd != self._cached_ttbr0:
                            self._pending_cputlb_flush = True
                            self._cached_ttbr0 = pgd
                            if self.virtual_tlb:
                                self._flush_cputlb()
                        self._last_switch_mm_pgd = pgd
                except Exception:
                    pass
            self.uc.hook_add(UC_HOOK_CODE, switch_mm_hook,
                            begin=address, end=address)
            self.switch_mm_hooked = True
        except Exception:
            self.switch_mm_hooked = False

    def _read_cp15_ttbr1(self) -> int:
        """Read the active TTBR1 without changing architectural CPU state."""
        value = self.uc.cpr_read(15, 0, 2, 0, 1, self._cp15_security(), False)
        self._cached_ttbr1 = value & 0x7FFFFFFF & ~0x3FFF
        return value

    def _pa_mapped(self, pa: int) -> bool:
        for begin, end in self._mapped:
            if begin <= pa < end:
                return True
        return False

    def _walk_page_tables_phys_with_perms(
            self, vaddr: int, privileged: bool | None = None) -> tuple[int, int] | None:
        """Walk live TTBR0 -> L1 -> L2 and decode ARMv7 R/W/X permissions (E0505)."""
        ttbr0 = self._cached_ttbr0
        if not ttbr0:
            return None
        if privileged is None:
            privileged = (self.uc.reg_read(UC_ARM_REG_CPSR) & 0x1F) != 0x10
        try:
            l1 = struct.unpack('<I', self.uc.mem_read(
                (ttbr0 & 0xFFFFC000) + ((vaddr >> 20) & 0xFFF) * 4, 4))[0]
            if l1 & 2:
                pa = (l1 & 0xFFF00000) | (vaddr & 0x000FFFFF)
                ap2 = (l1 >> 15) & 1
                ap10 = (l1 >> 10) & 3
                xn = (l1 >> 4) & 1
                can_read = (ap10 != 0) if privileged else (ap10 in (2, 3))
                if not can_read:
                    return None
                can_write = (ap2 == 0) and (ap10 != 0 if privileged else ap10 == 3)
                perms = 1 | (2 if can_write else 0) | (4 if xn == 0 else 0)
                return pa, perms
            if not (l1 & 1):
                return None
            l2pa = l1 & 0xFFFFFC00
            l2 = struct.unpack('<I', self.uc.mem_read(
                l2pa + ((vaddr >> 12) & 0xFF) * 4, 4))[0]
            if l2 & 2:
                pa = (l2 & 0xFFFFF000) | (vaddr & 0xFFF)
                ap2 = (l2 >> 9) & 1
                ap10 = (l2 >> 4) & 3
                xn = l2 & 1
                can_read = (ap10 != 0) if privileged else (ap10 in (2, 3))
                if not can_read:
                    return None
                can_write = (ap2 == 0) and (ap10 != 0 if privileged else ap10 == 3)
                perms = 1 | (2 if can_write else 0) | (4 if xn == 0 else 0)
                return pa, perms
            return None
        except Exception:
            return None

    def _walk_page_tables_phys(self, vaddr: int) -> int | None:
        """Walk live TTBR0 -> L1 -> L2 using physical mem_read only (no guest exec)."""
        res = self._walk_page_tables_phys_with_perms(vaddr, privileged=True)
        return res[0] if res is not None else None

    def _translate_virtual(self, vaddr: int) -> int | None:
        """Map a VA to a PA for the UC_TLB_VIRTUAL fill hook (E0448)."""
        if vaddr >= 0xFFFF0000:
            pa = self._walk_kernel_pt(vaddr)
            if pa is not None:
                return pa
            # high vectors and the identity-mapped MMIO/SRAM window
            return vaddr
        if vaddr >= 0x60000000:
            if vaddr < 0x80000000:
                return vaddr - 0x60000000
            # vmalloc/ioremap: use the kernel page tables (E0448); the plain
            # identity guess is only a last resort for stubbed MMIO pages.
            pa = self._walk_kernel_pt(vaddr)
            if pa is not None:
                return pa
            if vaddr >= 0xF0000000:
                # known CXD90045 hardware MMIO/SRAM window: identity-mapped stubs
                return vaddr
            # unmapped vmalloc/fixmap must surface as a real fault, not a bogus PA
            return None
        if not self._mmu_enabled:
            # pre-MMU early boot: the kernel runs from physical addresses
            return vaddr
        return self._walk_page_tables_phys(vaddr)

    def _capture_fast_syscall_frame(self, uc) -> None:
        """E0503: snapshot the pt_regs at ret_fast_syscall entry (base = SP+8)."""
        self._fast_frame = None
        try:
            sp = uc.reg_read(UC_ARM_REG_SP)
            words = [self._read32_phys(sp + 4 * i)
                     for i in range(20)]
            base = 2  # SP + 8
            f_sp = words[base + 0x34 // 4]
            f_lr = words[base + 0x38 // 4]
            f_pc = words[base + 0x3C // 4]
            f_cpsr = words[base + 0x40 // 4]
            if ((f_cpsr & 0x1F) == 0x10 and
                    0x1000 <= (f_pc or 0) < 0x60000000):
                frame_words = list(words[base:base + 17])
                frame_words[0] = uc.reg_read(UC_ARM_REG_R0)
                self._fast_frame = {'words': frame_words,
                                    'sp': f_sp, 'lr': f_lr, 'pc': f_pc,
                                    'cpsr': f_cpsr, 'svc_sp': sp}
        except Exception:
            self._fast_frame = None

    def _restore_fast_syscall_user_return(self, uc) -> bool:
        """E0503: enforce USR|Thumb + odd PC at ret_fast_syscall `movs pc, lr`."""
        try:
            spsr = uc.reg_read(UC_ARM_REG_SPSR)
            lr = uc.reg_read(UC_ARM_REG_LR)
            sp_now = uc.reg_read(UC_ARM_REG_SP)
        except Exception:
            self._fast_frame = None
            return False
        frame = self._fast_frame
        self._fast_frame = None
        if (spsr & 0x1F) not in (0, 0x10):
            return False
        if frame is not None and 'svc_sp' in frame:
            if (sp_now & ~0x1FFF) != (frame['svc_sp'] & ~0x1FFF):
                frame = None
        if frame is not None:
            cpsr = frame['cpsr']
            target = frame['pc'] or frame['lr'] or lr
            user_sp = frame['sp']
        else:
            if (spsr & 0x1F) != 0x10 or not (0x1000 <= (lr or 0) < 0x60000000):
                return False
            cpsr = spsr
            target = lr
            user_sp = None
        if (spsr & 0x1F) != 0x10 and (cpsr & 0x1F) != 0x10:
            return False
        user_cpsr = (cpsr & ~0x3F) | 0x10 | (0x20 if (cpsr & 0x20) else 0)
        try:
            uc.reg_write(UC_ARM_REG_CPSR, user_cpsr)
            if frame is not None:
                for i in range(13):
                    uc.reg_write(UC_ARM_REG_R0 + i, frame['words'][i])
                if user_sp is not None:
                    uc.reg_write(UC_ARM_REG_SP, user_sp)
                uc.reg_write(UC_ARM_REG_LR, frame['lr'])
                self._restore_user_sp = False
            uc.reg_write(UC_ARM_REG_SPSR, user_cpsr)
            uc.reg_write(UC_ARM_REG_PC, self.thumb_pc(target & 0xFFFFFFFF,
                                                     user_cpsr))
        except Exception:
            return False
        return True

    def _apply_pending_tlb_work(self) -> None:
        """E0502: run cache/flush work in the outer run loop, never in a hook
        (nested emu_start from a callback breaks the machine, see E0456/E0501)."""
        pages = self._pending_evict_pages
        need_flush = self._pending_cputlb_flush
        if not pages and not need_flush:
            return
        remove = getattr(self.uc, 'ctl_remove_cache', None)
        if remove is not None:
            for page in pages:
                try:
                    remove(page, page + 0x1000)
                    self._page_cache_drops += 1
                except Exception:
                    pass
        if need_flush:
            try:
                self._flush_cputlb()
            except Exception:
                pass
            try:
                self._flush_tlb()
            except Exception:
                pass
        self._pending_evict_pages = set()
        self._pending_cputlb_flush = False

    def _clear_mid_tb_itstate(self) -> None:
        """Clear transient CPSR.IT bits materialized mid-TB by Unicorn's
        non-exiting cpu_restore_state() during data TLB fills and MMIO hooks.

        QEMU's ARM translator compiles IT conditions into each TB and only
        writes env->condexec_bits at TB exit when dc->condexec_mask != 0,
        assuming env->condexec_bits stays 0 throughout TB execution.  Unicorn
        invokes cpu_restore_state(..., will_exit=false) before data TLB-fill
        and memory-hook callbacks, leaving stale ITSTATE if an IT block
        subsequently finishes before TB exit.
        """
        cpsr = self.uc.reg_read(UC_ARM_REG_CPSR)
        if cpsr & 0x0600FC00:
            self.uc.reg_write(UC_ARM_REG_CPSR, cpsr & ~0x0600FC00)
            self.mid_tb_itstate_clears += 1

    def _on_tlb_fill(self, uc, vaddr, access, entry, user_data=None) -> bool:
        self.tlb_fills += 1
        try:
            if access == 18 and vaddr >= 0x60000000:  # UC_MEM_FETCH
                self._mmu_enabled = True
            perms = 7  # UC_PROT_ALL
            if self._mmu_enabled and vaddr < 0x60000000:
                res = self._walk_page_tables_phys_with_perms(vaddr)
                if res is not None:
                    target, perms = res
                else:
                    target = None
            else:
                target = self._translate_virtual(vaddr)
            if 0x0402F000 <= vaddr < 0x04030000 and len(self.e0502_fill) < 24:
                self.e0502_fill.append({
                    'vaddr': f'0x{vaddr:08x}', 'access': access,
                    'target': (f'0x{target:08x}' if target is not None else None),
                    'mmu': self._mmu_enabled,
                    'ttbr0': (f'0x{self._cached_ttbr0:08x}'
                              if self._cached_ttbr0 else None)})
            if target is None:
                self.tlb_fill_misses += 1
                if len(self.tlb_fill_samples) < 64:
                    sample = {'vaddr': f'0x{vaddr:08x}', 'access': access,
                              'ttbr0': (f'0x{self._cached_ttbr0:08x}'
                                        if self._cached_ttbr0 else None),
                              'ttbr1': (f'0x{self._cached_ttbr1:08x}'
                                        if self._cached_ttbr1 else None)}
                    sym = self._symbol_address('swapper_pg_dir')
                    sample['swapper_pg_dir'] = f'0x{sym:08x}' if sym else None
                    probes = []
                    idx = (vaddr >> 20) & 0xFFF
                    bases = []
                    if self._cached_ttbr1:
                        bases.append(self._cached_ttbr1 & ~0x3FFF)
                    if sym:
                        try:
                            bases.append(self._kernel_phys(sym) & 0xFFFFC000)
                        except Exception:
                            pass
                    for base in bases[:2]:
                        for index in (idx, idx & 0x3FF):
                            try:
                                word = struct.unpack('<I', self.uc.mem_read(
                                    (base + index * 4) & 0xFFFFFFFF, 4))[0]
                                probes.append({'base': f'0x{base:08x}',
                                               'index': index,
                                               'l1': f'0x{word:08x}'})
                            except Exception as error:
                                probes.append({'base': f'0x{base:08x}',
                                               'index': index,
                                               'error': str(error)[:40]})
                    sample['probes'] = probes[:8]
                    self.tlb_fill_samples.append(sample)
                self._last_tlb_fault = (vaddr, access)
                return False
            entry.paddr = target
            entry.perms = perms
            need = 4 if access == 18 else (2 if access == 17 else 1)
            if not (perms & need):
                self._last_tlb_fault = (vaddr, access)
            elif (access == 16 and (perms & 1)) or (access == 17 and (perms & 2)):
                self._clear_mid_tb_itstate()
            return True
        except Exception:
            self._last_tlb_fault = (vaddr, access)
            return False

    def _install_virtual_tlb(self) -> None:
        """Enable Unicorn's virtual TLB and the live page-table fill hook (E0448)."""
        try:
            from unicorn import UC_HOOK_TLB_FILL, UC_TLB_VIRTUAL
            self.uc.ctl_set_tlb_mode(UC_TLB_VIRTUAL)
            self.uc.hook_add(UC_HOOK_TLB_FILL, self._on_tlb_fill)
            self.virtual_tlb = True
        except Exception as error:
            self.virtual_tlb = False
            self.virtual_tlb_error = f'{type(error).__name__}: {error}'

    def _walk_user_va(self, pc: int) -> dict | None:
        """Resolve a user VA through TTBR0 -> L1 -> L2 and return its page info."""
        ttbr0 = self._read_cp15_ttbr0() & 0xFFFFC000
        info = {'pc': f'0x{pc:08x}', 'ttbr0': f'0x{ttbr0:08x}'}
        try:
            l1 = struct.unpack('<I', self.uc.mem_read(
                ttbr0 + ((pc >> 20) & 0xFFF) * 4, 4))[0]
            info['l1'] = f'0x{l1:08x}'
            pa = None
            if l1 & 2:
                pa = (l1 & 0xFFF00000) | (pc & 0x000FFFFF)
            elif l1 & 1:
                l2pa = l1 & 0xFFFFFC00
                l2 = struct.unpack('<I', self.uc.mem_read(
                    l2pa + ((pc >> 12) & 0xFF) * 4, 4))[0]
                info['l2'] = f'0x{l2:08x}'
                if l2 & 2:
                    pa = (l2 & 0xFFFFF000) | (pc & 0xFFF)
            if pa is not None:
                info['pa'] = f'0x{pa:08x}'
            return info
        except Exception as error:
            info['error'] = str(error)
            return info

    def _capture_live_fault_page(self, pc: int) -> dict | None:
        """Optional capped diagnostic dump (bytes/regs) for a user fault PC."""
        info = self._walk_user_va(pc) or {'pc': f'0x{pc:08x}'}
        dump = dict(info)
        dump['cpsr'] = f'0x{self.uc.reg_read(UC_ARM_REG_CPSR):08x}'
        for name, reg in (('r0', UC_ARM_REG_R0), ('r1', UC_ARM_REG_R1),
                          ('r2', UC_ARM_REG_R2), ('lr', UC_ARM_REG_LR),
                          ('sp', UC_ARM_REG_SP)):
            dump[name] = f'0x{self.uc.reg_read(reg):08x}'
        if len(self.live_fault_dumps) < 8 and 'pa' in dump:
            try:
                raw = bytes(self.uc.mem_read(int(dump['pa'], 16) - 8, 24))
                dump['bytes'] = raw.hex()
                dump['halfword_at_pc'] = f'0x{int.from_bytes(raw[8:10], "little"):04x}'
                dump['halfword_before'] = f'0x{int.from_bytes(raw[6:8], "little"):04x}'
            except Exception as error:
                dump['error'] = str(error)
            self.live_fault_dumps.append(dump)
        return dump

    def _unused_capture(self, pc: int) -> dict | None:
        ttbr0 = self._read_cp15_ttbr0() & 0xFFFFC000
        dump = {'pc': f'0x{pc:08x}', 'cpsr': f'0x{self.uc.reg_read(UC_ARM_REG_CPSR):08x}',
                'ttbr0': f'0x{ttbr0:08x}', 'r0': f'0x{self.uc.reg_read(UC_ARM_REG_R0):08x}',
                'r1': f'0x{self.uc.reg_read(UC_ARM_REG_R1):08x}',
                'r2': f'0x{self.uc.reg_read(UC_ARM_REG_R2):08x}',
                'lr': f'0x{self.uc.reg_read(UC_ARM_REG_LR):08x}',
                'sp': f'0x{self.uc.reg_read(UC_ARM_REG_SP):08x}'}
        try:
            l1 = struct.unpack('<I', self.uc.mem_read(
                ttbr0 + ((pc >> 20) & 0xFFF) * 4, 4))[0]
            dump['l1'] = f'0x{l1:08x}'
            pa = None
            if l1 & 2:
                pa = (l1 & 0xFFF00000) | (pc & 0x000FFFFF)
            elif l1 & 1:
                l2pa = l1 & 0xFFFFFC00
                l2 = struct.unpack('<I', self.uc.mem_read(
                    l2pa + ((pc >> 12) & 0xFF) * 4, 4))[0]
                dump['l2'] = f'0x{l2:08x}'
                if l2 & 2:
                    pa = (l2 & 0xFFFFF000) | (pc & 0xFFF)
            if pa is not None:
                dump['pa'] = f'0x{pa:08x}'
                raw = bytes(self.uc.mem_read(pa - 8, 24))
                dump['bytes'] = raw.hex()
                dump['halfword_at_pc'] = f'0x{int.from_bytes(raw[8:10], "little"):04x}'
                dump['halfword_before'] = f'0x{int.from_bytes(raw[6:8], "little"):04x}'
        except Exception as error:
            dump['error'] = str(error)
        self.live_fault_dumps.append(dump)
        return dump

    def _on_unmapped_read(self, uc, access, address, size, value, user_data=None) -> bool:
        self._last_unmapped = address
        self._last_unmapped_write = False
        return False

    def _on_unmapped_write(self, uc, access, address, size, value, user_data=None) -> bool:
        self._last_unmapped = address
        self._last_unmapped_write = True
        return False

    def _note_fault(self, pc: int, address: int, cpsr: int) -> bool:
        """Repeated-fault circuit breaker (E0441).

        Returns True when the exact same (pc, faulting VA, mode) fault has been
        delivered `max_same_fault_repeats` times in a row, so diagnostic runs
        stop quickly instead of burning the whole instruction budget.
        """
        key = (pc & ~1, address, cpsr & 0x1F)
        if key == self._fault_guard_key:
            self._fault_guard_count += 1
        else:
            self._fault_guard_key = key
            self._fault_guard_count = 1
        return self._fault_guard_count >= self.max_same_fault_repeats

    def _kernel_phys(self, va: int) -> int:
        """Translate a kernel linear-map VA to its physical address (E0437).

        Unicorn's mem_read/mem_write operate on the physical address space, but
        kernel stacks/pt_regs live at 0x60000000+ (PAGE_OFFSET).  All kernel-VA
        accesses from the emulator must translate first.
        """
        if (self.PAGE_OFFSET and self.PAGE_OFFSET <= va
                < self.PAGE_OFFSET + 0x80000000):
            return va - self.PAGE_OFFSET
        return va

    def _emulate_banked_store(self, pc: int) -> None:
        """Emulate `stmdb r0, {sp, lr}^` using the saved USR bank values.

        Unicorn shares R13/R14 between modes, so the kernel's usr_entry banked
        store would otherwise write the SVC stack/LR into pt_regs.
        """
        base = self.uc.reg_read(UC_ARM_REG_R0)
        user_sp = self.mode_sp.get('usr')
        user_lr = self.mode_lr.get('usr')
        if user_sp is None:
            user_sp = self.user_sp
        pa = self._kernel_phys(base)
        try:
            self.uc.mem_write(pa - 8, struct.pack('<I', (user_sp or 0) & 0xFFFFFFFF))
            self.uc.mem_write(pa - 4, struct.pack('<I', (user_lr or 0) & 0xFFFFFFFF))
        except Exception:
            pass
        self.uc.reg_write(UC_ARM_REG_PC, pc + 4)

    def _deliver_user_exception(self, pc: int, cpsr: int) -> bool:
        """Emulate vector_stub + usr_entry for a user fault (E0430).

        The real ARMv7 vector code uses the banked SP_abt for a 12-byte scratch
        frame before switching to SVC; Unicorn shares R13 between modes, so we
        write that scratch frame ourselves, switch to SVC mode with the saved
        kernel SVC stack pointer and jump straight to the kernel's __dabt_usr /
        __pabt_usr handler with r0 pointing at the scratch frame.
        """
        if self._pending_exception == 'pabt':
            handler = self._symbol_address('__pabt_usr')
        elif self._pending_exception == 'und':
            handler = self._symbol_address('__und_usr')
        else:
            handler = self._symbol_address('__dabt_usr')
        if handler is None:
            return False
        scratch_pa = self.VECTOR_SCRATCH_PA
        scratch_va = scratch_pa + self.PAGE_OFFSET
        r0 = self.uc.reg_read(UC_ARM_REG_R0)
        saved_pc = (((pc & ~1) + (2 if (cpsr & 0x20) else 4))
                    if self._pending_exception == 'und' else pc)
        self.uc.mem_write(scratch_pa, struct.pack(
            '<3I', r0 & 0xFFFFFFFF, saved_pc & 0xFFFFFFFF, cpsr & 0xFFFFFFFF))
        self.user_sp = self.uc.reg_read(UC_ARM_REG_SP)
        self.mode_sp['usr'] = self.user_sp
        self.mode_lr['usr'] = self.uc.reg_read(UC_ARM_REG_LR)
        self._restore_user_sp = True
        self.uc.reg_write(UC_ARM_REG_CPSR, 0x13 | 0xC0)  # SVC, IRQ/FIQ masked
        # E0440: Unicorn banks SP, so after the mode switch R13 already holds
        # the live SVC bank value (the kernel stack it returned to user on).
        # Do NOT overwrite it with a stale capture; validate thread_info->task.
        live_sp = self.uc.reg_read(UC_ARM_REG_SP)
        ti = live_sp & ~0x1FFF
        task = 0
        try:
            raw = bytes(self.uc.mem_read(self._kernel_phys(ti + 0xC), 4))
            task = struct.unpack('<I', raw)[0]
        except Exception:
            task = 0
        if task == 0 and self.mode_sp.get('svc') is not None:
            # fall back to the last value captured at ret_to_user
            self.uc.reg_write(UC_ARM_REG_SP, self.mode_sp['svc'])
            live_sp = self.mode_sp['svc']
            ti = live_sp & ~0x1FFF
            try:
                raw = bytes(self.uc.mem_read(self._kernel_phys(ti + 0xC), 4))
                task = struct.unpack('<I', raw)[0]
            except Exception:
                task = 0
        if len(self.stack_delivery_trace) < 32:
            self.stack_delivery_trace.append({
                'pc': f'0x{pc:08x}', 'sp': f'0x{live_sp:08x}',
                'thread_info': f'0x{ti:08x}', 'task': f'0x{task:08x}'})
        self._last_thread_info = ti
        self._last_thread_task = task
        self.uc.reg_write(UC_ARM_REG_R0, scratch_va)
        self.uc.reg_write(UC_ARM_REG_LR, pc)
        self.uc.reg_write(UC_ARM_REG_PC, handler)
        self._flush_tlb()
        return True

    def _deliver_undef_exception(self, pc: int, cpsr: int) -> bool:
        """Deliver an ARMv7 Undefined Instruction exception to __und_usr/__und_svc.

        Unlike vector_dabt/vector_pabt (which subtract 8/4 in vector_stub),
        vector_und saves raw LR_und = PC + 2 (Thumb) or PC + 4 (ARM) into the
        12-byte vector scratch frame so __und_usr / __und_svc can inspect the
        faulting instruction, dispatch lazy VFP state via call_fpe/do_vfp
        (including Thumb-2 av-cam.bin running in SVC mode), or deliver SIGILL.
        """
        mode = cpsr & 0x1F
        if not self.data_abort_delivery or mode not in (0x10, 0x13):
            return False
        sym = '__und_usr' if mode == 0x10 else '__und_svc'
        if self._symbol_address(sym) is None:
            return False
        undef_key = (pc & ~1, mode, self._cached_ttbr0 if mode == 0x10 else 0)
        if self._undef_last_key == undef_key:
            self._undef_same_pc += 1
            if self._undef_same_pc > 8:
                return False
        else:
            self._undef_last_key = undef_key
            self._undef_same_pc = 1
        self._pending_evict_pages.add(pc & ~0xFFF)
        self._pending_cputlb_flush = True
        self._pending_exception = 'und'
        delivered = (self._deliver_user_exception(pc & ~1, cpsr)
                     if mode == 0x10
                     else self._deliver_svc_exception(pc & ~1, cpsr, 'und'))
        if not delivered:
            return False
        self.undef_exceptions_delivered += 1
        if len(self.undef_exception_trace) < 64:
            self.undef_exception_trace.append({
                'pc': f'0x{pc & ~1:08x}',
                'cpsr': f'0x{cpsr:08x}',
                'thumb': bool(cpsr & 0x20),
                'svc': mode == 0x13,
            })
        return True

    # ------------------------------------------------- data abort delivery
    ABORT_HELPER_PA = 0x02001000

    def _set_cp15_fault(self, address: int, fsr: int) -> bool:
        """Populate the active DFAR/DFSR bank without executing guest helpers."""
        security = self._cp15_security()
        self.uc.cpr_write(15, 0, 6, 0, 0, security, False, address & 0xFFFFFFFF)
        self.uc.cpr_write(15, 0, 5, 0, 0, security, False, fsr & 0xFFFFFFFF)
        return True

    def _deliver_prefetch_abort(self, pc: int, cpsr: int, address: int) -> bool:
        """Deliver an ARMv7 Prefetch Abort to vector 0xFFFF000C (E0429)."""
        if not self.data_abort_delivery:
            return False
        self.last_ifar = address & 0xFFFFFFFF
        self.last_ifsr = 0x5
        self._pte_dirty = True
        self._last_fault_page = address & ~0xFFF
        if ((cpsr & 0x1F) == 0x13
                and self._symbol_address('__pabt_svc') is not None):
            if not self._set_cp15_prefetch_fault(address, 0x5):
                return False
            self._pending_exception = 'pabt'
            if self._deliver_svc_exception(pc, cpsr, 'pabt'):
                self.prefetch_aborts_delivered += 1
                if len(self.prefetch_abort_trace) < 64:
                    self.prefetch_abort_trace.append({
                        'pc': f'0x{pc:08x}', 'address': f'0x{address:08x}',
                        'ifsr': '0x00000005', 'user': False, 'svc': True})
                return True
        if (cpsr & 0x1F) == 0x10 and self._symbol_address('__pabt_usr') is not None:
            if not self._set_cp15_prefetch_fault(address, 0x5):
                return False
            self._pending_exception = 'pabt'
            if self._deliver_user_exception(pc, cpsr):
                self.prefetch_aborts_delivered += 1
                if len(self.prefetch_abort_trace) < 64:
                    self.prefetch_abort_trace.append({
                        'pc': f'0x{pc:08x}', 'address': f'0x{address:08x}',
                        'ifsr': '0x00000005', 'user': True})
                return True
        if self.prefetch_aborts_delivered >= self.data_abort_limit:
            return False
        self.last_ifar = address & 0xFFFFFFFF
        self.last_ifsr = 0x5
        if not self._set_cp15_prefetch_fault(address, 0x5):
            return False
        current_mode = cpsr & 0x1F
        current_sp = self.uc.reg_read(UC_ARM_REG_SP)
        if current_mode == 0x10:
            self.user_sp = current_sp
            if self.mode_sp.get('svc') is not None:
                self.uc.reg_write(UC_ARM_REG_SP, self.mode_sp['svc'])
            self._restore_user_sp = True
        self.uc.reg_write(UC_ARM_REG_CPSR, 0x17 | 0xC0)
        self.uc.reg_write(UC_ARM_REG_SPSR, cpsr)
        self.uc.reg_write(UC_ARM_REG_LR, pc + 4)
        self.uc.reg_write(UC_ARM_REG_PC, 0xFFFF000C)
        self.prefetch_aborts_delivered += 1
        if len(self.prefetch_abort_trace) < 64:
            self.prefetch_abort_trace.append({
                'pc': f'0x{pc:08x}', 'address': f'0x{address:08x}',
                'ifsr': '0x00000005', 'user': current_mode == 0x10})
        return True

    def _set_cp15_prefetch_fault(self, address: int, ifsr: int) -> bool:
        """Populate the active IFAR/IFSR bank without executing guest helpers."""
        security = self._cp15_security()
        self.uc.cpr_write(15, 0, 6, 0, 2, security, False, address & 0xFFFFFFFF)
        self.uc.cpr_write(15, 0, 5, 0, 1, security, False, ifsr & 0xFFFFFFFF)
        return True

    def _deliver_data_abort(self, pc: int, cpsr: int, address: int,
                            write: bool) -> bool:
        """Deliver an ARMv7 Data Abort to 0xFFFF0010 with DFAR/DFSR populated."""
        if not self.data_abort_delivery:
            return False
        # E0445: the kernel reads DFAR/DFSR through the v7_early_abort MRC hooks,
        # so the saved fault state MUST be updated before the user-mode delivery
        # (previously it was only set on the kernel-mode path -> stale DFAR).
        fsr = self.data_abort_fsr | (0x800 if write else 0)
        self.last_dfar = address & 0xFFFFFFFF
        self.last_dfsr = fsr & 0xFFFFFFFF
        if (cpsr & 0x1F) == 0x10 and self._symbol_address('__dabt_usr') is not None:
            if not self._set_cp15_fault(address, 0x5 | (0x800 if write else 0)):
                return False
            self._pte_dirty = True
            self._last_fault_page = address & ~0xFFF
            self._pending_evict_pages.add(address & ~0xFFF)
            self._pending_evict_pages.add(pc & ~0xFFF)
            self._pending_cputlb_flush = True
            self._pending_exception = 'dabt'
            if self._deliver_user_exception(pc, cpsr):
                return True
        if self.data_aborts_delivered >= self.data_abort_limit:
            return False
        abort_key = (pc, address & ~0xFFF)
        if self._abort_last_pc == abort_key:
            self._abort_same_pc += 1
            if self._abort_same_pc > 8:
                return False
        else:
            self._abort_last_pc = abort_key
            self._abort_same_pc = 1
        fsr = self.data_abort_fsr | (0x800 if write else 0)
        self.last_dfar = address & 0xFFFFFFFF
        self.last_dfsr = fsr & 0xFFFFFFFF
        self._pte_dirty = True
        self._last_fault_page = address & ~0xFFF
        if ((cpsr & 0x1F) == 0x13
                and self._symbol_address('__dabt_svc') is not None):
            # Live SVC-mode fault: emulate the vector stub into __dabt_svc and
            # keep the current kernel SP/LR (do_DataAbort still runs the kernel
            # exception-table lookup for genuine invalid probes).
            if not self._set_cp15_fault(address, fsr):
                return False
            self._pending_cputlb_flush = True
            self._pending_exception = 'dabt'
            delivered = self._deliver_svc_exception(pc, cpsr, 'dabt')
            if delivered:
                self.data_aborts_delivered += 1
                if len(self.data_abort_trace) < 64:
                    self.data_abort_trace.append({
                        'pc': f'0x{pc:08x}', 'address': f'0x{address:08x}',
                        'fsr': f'0x{fsr:08x}', 'write': bool(write),
                        'user': False, 'svc': True})
            return delivered
        if not self._set_cp15_fault(address, fsr):
            return False
        # ABT mode with I/F masked; banked SPSR_abt/LR_abt after the CPSR switch.
        self.uc.reg_write(UC_ARM_REG_CPSR, 0x17 | 0xC0)
        self.uc.reg_write(UC_ARM_REG_SPSR, cpsr)
        self.uc.reg_write(UC_ARM_REG_LR, pc + 8)
        self.uc.reg_write(UC_ARM_REG_PC, 0xFFFF0010)
        self.data_aborts_delivered += 1
        if len(self.data_abort_trace) < 64:
            self.data_abort_trace.append({
                'pc': f'0x{pc:08x}', 'address': f'0x{address:08x}',
                'fsr': f'0x{fsr:08x}', 'write': write})
        return True

    # --------------------------------------------------- __ex_table fixups
    def _mmio_write_candidates(self) -> list[dict]:
        candidates = []
        for address, stream in self.mmio_write_bytes.items():
            if len(stream) < 8:
                continue
            printable = sum(1 for byte in stream if 32 <= byte < 127 or byte in (9, 10, 13))
            ratio = printable / len(stream)
            if ratio < 0.6:
                continue
            text = ''.join(chr(byte) if 32 <= byte < 127 else '.'
                           for byte in stream[:200])
            candidates.append({'address': f'0x{address:08x}', 'bytes': len(stream),
                               'printable_ratio': round(ratio, 2), 'text': text})
        candidates.sort(key=lambda item: item['bytes'], reverse=True)
        return candidates[:8]

    @staticmethod
    def _ex_table_memory_op(word: int) -> bool:
        """ARM load/store or exclusive, the instructions __ex_table records."""
        if (word & 0x0C000000) == 0x04000000:
            return True
        if (word & 0x0FF00FFF) == 0x01900F9F:  # LDREX
            return True
        if (word & 0x0FF00FF0) == 0x01800F90:  # STREX
            return True
        return False

    def _build_ex_table(self) -> None:
        """Locate the kernel __ex_table (sorted insn -> fixup pairs).

        A whole-image scan of every word pair that looks like a kernel pointer
        also matches kallsyms and the abort stubs. One of those collisions maps
        ``__dabt_invalid`` onto itself, so a real data abort spins until the
        instruction budget is gone. The real table is the long, strictly
        increasing run whose faulting sites are loads and stores.
        """
        self.ex_table = {}
        self.ex_table_entries = 0
        if not self.ex_table_fixup:
            return
        raw = self.kernel_raw
        text_start = self.kernel_load + self.PAGE_OFFSET
        text_end = text_start + len(raw)
        best: tuple[int, int, int] | None = None
        for align in (0, 4):
            run_len = 0
            run_mem = 0
            prev = None
            run_start = align

            def consider() -> None:
                nonlocal best
                if run_len < 64 or run_mem / run_len < 0.90:
                    return
                if best is None or run_len > best[0]:
                    best = (run_len, run_mem, run_start)

            for offset in range(align, len(raw) - 7, 8):
                insn, fixup = struct.unpack_from('<II', raw, offset)
                ok = (
                    text_start <= insn < text_end
                    and text_start <= fixup < text_end
                    and insn != fixup
                    and (prev is None or insn > prev)
                )
                if ok:
                    if run_len == 0:
                        run_start = offset
                        run_mem = 0
                    run_len += 1
                    prev = insn
                    site = insn - text_start
                    if 0 <= site <= len(raw) - 4:
                        word = struct.unpack_from('<I', raw, site)[0]
                        if self._ex_table_memory_op(word):
                            run_mem += 1
                else:
                    consider()
                    run_len = 0
                    run_mem = 0
                    prev = None
            consider()
        if best is None:
            return
        length, _mem, start = best
        for index in range(length):
            insn, fixup = struct.unpack_from('<II', raw, start + index * 8)
            self.ex_table[insn] = fixup
        self.ex_table_entries = length

    def _try_ex_table_fixup(self, pc: int) -> int | None:
        if not self.ex_table_fixup or not self.ex_table:
            return None
        fixup = self.ex_table.get(pc)
        if fixup is None or fixup == pc:
            return None
        if len(self.ex_table_trace) < 64:
            self.ex_table_trace.append({
                'pc': f'0x{pc:08x}', 'fixup': f'0x{fixup:08x}'})
        self.ex_table_fixups += 1
        self.uc.reg_write(UC_ARM_REG_PC, fixup)
        return fixup

    def _fault_state(self) -> dict:
        """Register + decoded-instruction snapshot at the current PC."""
        from capstone import CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, Cs
        from capstone.arm import ARM_OP_MEM

        import unicorn.arm_const as arm_const

        def read_register(index):
            if index == 13:
                return self.uc.reg_read(UC_ARM_REG_SP)
            if index == 14:
                return self.uc.reg_read(UC_ARM_REG_LR)
            if index == 15:
                return self.uc.reg_read(UC_ARM_REG_PC)
            return self.uc.reg_read(getattr(arm_const, f'UC_ARM_REG_R{index}'))

        def register_index(name):
            aliases = {'sb': 9, 'sl': 10, 'fp': 11, 'ip': 12,
                       'sp': 13, 'lr': 14, 'pc': 15}
            name = name.lower()
            if name in aliases:
                return aliases[name]
            if name.startswith('r') and name[1:].isdigit():
                return int(name[1:])
            return None

        state = {
            'pc': f'0x{self.uc.reg_read(UC_ARM_REG_PC):08x}',
            'cpsr': f'0x{self.uc.reg_read(UC_ARM_REG_CPSR):08x}',
            'r0': f'0x{self.uc.reg_read(UC_ARM_REG_R0):08x}',
            'r1': f'0x{self.uc.reg_read(UC_ARM_REG_R1):08x}',
            'r2': f'0x{self.uc.reg_read(UC_ARM_REG_R2):08x}',
            'r3': f'0x{self.uc.reg_read(UC_ARM_REG_R3):08x}',
            'r4': f'0x{self.uc.reg_read(UC_ARM_REG_R4):08x}',
            'r5': f'0x{self.uc.reg_read(UC_ARM_REG_R5):08x}',
            'sp': f'0x{self.uc.reg_read(UC_ARM_REG_SP):08x}',
            'lr': f'0x{self.uc.reg_read(UC_ARM_REG_LR):08x}'}
        pc = self.uc.reg_read(UC_ARM_REG_PC)
        cpsr = self.uc.reg_read(UC_ARM_REG_CPSR)
        try:
            raw = self._guest_read(pc, 4)
            md = Cs(CS_ARCH_ARM, CS_MODE_THUMB if cpsr & 0x20 else CS_MODE_ARM)
            md.detail = True
            instruction = next(md.disasm(raw, pc), None)
            if instruction is not None:
                state['mnemonic'] = instruction.mnemonic
                state['size'] = instruction.size
                memory = next((op for op in instruction.operands
                               if op.type == ARM_OP_MEM), None)
                if memory is not None and memory.mem.index == 0:
                    base_index = register_index(
                        instruction.reg_name(memory.mem.base))
                    if base_index is not None:
                        base_value = read_register(base_index)
                        state['base'] = f'0x{base_value:08x}'
                        state['effective_address'] = (
                            f'0x{(base_value + memory.mem.disp) & 0xFFFFFFFF:08x}')
                if 'effective_address' not in state:
                    pc_pa = (self._translate_virtual(pc)
                             if self._mmu_enabled else self._kernel_phys(pc))
                    decoded = self._decode_fault_address(pc, pc_pa)
                    if decoded is not None:
                        state['effective_address'] = f'0x{decoded[0]:08x}'
        except Exception:
            pass
        return state

    def run(self, limit: int | None = None) -> dict:
        limit = limit or self.instruction_limit
        stop_reason = None
        stop_error = None
        executed = 0
        try:
            if self.use_code_hook:
                self.uc.emu_start(self.kernel_load, 0, count=limit)
                executed = self._instructions
            else:
                # Slice-based run: no per-instruction Python callback, so a
                # multi-hundred-million-instruction bring-up stays practical.  The
                # PC after each slice is the sampling record.
                remaining = limit
                while remaining > 0:
                    if (self.virtual_tlb and self._cached_ttbr1 is None
                            and (self.uc.reg_read(UC_ARM_REG_PC) >= 0x60000000)):
                        try:
                            self._read_cp15_ttbr1()
                        except Exception:
                            pass
                    self._try_inject_irq()
                    slice_size = min(self.slice_size, remaining)
                    if self._frame_clock_sliced():
                        slice_size = min(slice_size, self._cxd_bus.us_until_event())
                    pc_now = self.uc.reg_read(UC_ARM_REG_PC)
                    thumb = self.uc.reg_read(UC_ARM_REG_CPSR) & 0x20
                    self._last_unmapped = None
                    self._last_tlb_fault = None
                    try:
                        self._apply_pending_tlb_work()
                        self.uc.emu_start(pc_now | (1 if thumb else 0), 0,
                                          count=slice_size)
                        if getattr(self, '_intr_stopped', False):
                            stop_reason = 'repeated_fault'
                            break
                        if getattr(self, '_exclusive_resume', False):
                            self._exclusive_resume = False
                            self.exclusive_emulated += 1
                            executed += 1
                            remaining -= 1
                            continue
                        if getattr(self, '_direct_map_vtlb', False) and not self.virtual_tlb:
                            self._direct_map_vtlb = False
                            self._install_virtual_tlb()
                            self._mmu_enabled = True
                            continue
                        if self._user_refresh_requested:
                            self._user_refresh_requested = False
                            try:
                                self._refresh_user_ttbr0()
                                self._read_cp15_ttbr1()
                            except Exception:
                                pass
                        if self._vtlb_requested and not self.virtual_tlb:
                            self._install_virtual_tlb()
                            self._vtlb_requested = False
                            self._mmu_enabled = True
                            if (getattr(self, 'enable_svc_dispatch', False)
                                    and not self._svc_hook_installed):
                                try:
                                    self.uc.hook_add(UC_HOOK_INTR,
                                                     self._on_interrupt)
                                    self._svc_hook_installed = True
                                except Exception:
                                    pass
                            # Prime the page-table caches before any user fetch.
                            if self._activation_pgd:
                                self._cached_ttbr0 = self._activation_pgd
                            try:
                                self.uc.reg_write(UC_ARM_REG_CPSR, 0xD3)
                                self._refresh_user_ttbr0()
                                self._read_cp15_ttbr1()
                            except Exception:
                                pass
                            if self._activation_pgd:
                                self._cached_ttbr0 = self._activation_pgd
                            try:
                                self.uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
                            except Exception:
                                pass
                            remove = getattr(self.uc, 'ctl_remove_cache', None)
                            if remove is not None:
                                try:
                                    remove(0, 0x100000000 - 0x1000)
                                except Exception:
                                    pass
                        _resume = self._pending_user_resume
                        if _resume is not None:
                            self._pending_user_resume = None
                            _rpc, _rcpsr = _resume
                            self._apply_pending_tlb_work()
                            self._set_pc_and_cpsr(_rpc, _rcpsr)
                            _uframe = getattr(self, '_pending_user_frame', None)
                            self._pending_user_frame = None
                            if _uframe is not None and (_rcpsr & 0x1F) == 0x10:
                                for _ri in range(13):
                                    self.uc.reg_write(UC_ARM_REG_R0 + _ri,
                                                      _uframe['words'][_ri])
                                self.uc.reg_write(UC_ARM_REG_SP, _uframe['sp'])
                                self.uc.reg_write(UC_ARM_REG_LR, _uframe['lr'])
                                self._restore_user_sp = False
                            try:
                                self.uc.ctl_flush_tb()
                            except Exception:
                                pass
                            executed += 1
                            remaining = max(0, remaining - 1)
                            continue
                    except Exception as error:
                        message = str(error)
                        if ('UNMAPPED' in message or 'PROT' in message
                                or 'UC_ERR_EXCEPTION' in message
                                or 'FETCH' in message or 'INSN' in message):
                            pc = self.uc.reg_read(UC_ARM_REG_PC)
                            cpsr = self.uc.reg_read(UC_ARM_REG_CPSR)
                            info = self._fault_state()
                            effective = info.get('effective_address')
                            mnemonic = info.get('mnemonic', '')
                            fault_va = int(effective, 16) if effective else None
                            # E0442: use the exact unmapped access captured by the
                            # READ/WRITE_UNMAPPED hooks; only FETCH faults (or a
                            # fault whose address equals PC) are prefetch aborts.
                            upper = message.upper()
                            is_fetch = 'FETCH' in upper or 'INSN' in upper
                            captured = self._last_unmapped is not None
                            if (not captured and self.unmapped
                                    and self.unmapped[-1].get('pc') == pc):
                                # generic UC_HOOK_MEM_UNMAPPED fired for this PC
                                entry = self.unmapped[-1]
                                self._last_unmapped = entry.get('address')
                                self._last_unmapped_write = (
                                    entry.get('access') == 1)
                                captured = True
                            is_mem = (captured or 'READ' in upper
                                      or 'WRITE' in upper)
                            access_addr = fault_va
                            if captured:
                                access_addr = self._last_unmapped
                            write = (self._last_unmapped_write
                                     or mnemonic.startswith(('str', 'stm', 'push', 'vst')))
                            user_mode_cpsr = (cpsr & 0x1F) == 0x10
                            if not user_mode_cpsr and self._last_tlb_fault is not None:
                                _tlb_va, _tlb_acc = self._last_tlb_fault
                                if _tlb_acc == 18:
                                    is_fetch = True
                                    access_addr = pc
                                elif _tlb_acc in (16, 17):
                                    is_mem = True
                                    write = (_tlb_acc == 17)
                                    if (access_addr is None
                                            or (access_addr & ~0xFFF) != (_tlb_va & ~0xFFF)):
                                        access_addr = _tlb_va
                            # Prefetch only for instruction fetch or an unknown
                            # user-mode fault with no captured memory access; a
                            # captured READ/WRITE_UNMAPPED is always a Data Abort.
                            prefetch = is_fetch or (not is_mem and user_mode_cpsr)
                            if len(self.fault_classification_trace) < 64:
                                self.fault_classification_trace.append({
                                    'pc': f'0x{pc:08x}',
                                    'cpsr': f'0x{cpsr:08x}',
                                    'address': (f'0x{access_addr:08x}'
                                                if access_addr is not None else None),
                                    'kind': 'prefetch' if prefetch else 'data',
                                    'write': bool(write),
                                    'msg': message[:40]})
                            user_mode = (cpsr & 0x1F) == 0x10
                            if ('INSN' in upper and (cpsr & 0x1F) == 0x13
                                    and self._translate_virtual(pc & ~1) is not None):
                                if self._deliver_undef_exception(pc, cpsr):
                                    executed += 1
                                    remaining = max(0, remaining - 1)
                                    continue
                                raise
                            if self._should_recover_insn_invalid(pc, message):
                                if self._deliver_undef_exception(pc, cpsr):
                                    executed += 1
                                    remaining = max(0, remaining - 1)
                                    continue
                                if ((cpsr & 0x1F) == 0x10 and (cpsr & 0x20) == 0
                                        and (self.uc.reg_read(UC_ARM_REG_FPEXC) & 0x40000000) == 0):
                                    try:
                                        self.uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
                                        _page = pc & ~0xFFF
                                        remove = getattr(self.uc, 'ctl_remove_cache', None)
                                        if remove is not None:
                                            remove(_page, _page + 0x1000)
                                        self.uc.ctl_flush_tb()
                                        self._set_pc_and_cpsr(pc, cpsr)
                                        executed += 1
                                        remaining = max(0, remaining - 1)
                                        continue
                                    except Exception:
                                        pass
                                if len(self.insn_invalid_unsupported) < 16:
                                    try:
                                        _raw = self._read_user_bytes(pc & ~1, 4)
                                        _hex = _raw.hex() if _raw else None
                                    except Exception:
                                        _hex = None
                                    _mn = None
                                    try:
                                        from capstone import (CS_ARCH_ARM,
                                                              CS_MODE_ARM,
                                                              CS_MODE_THUMB, Cs)
                                        _md = Cs(CS_ARCH_ARM, CS_MODE_THUMB
                                                 if cpsr & 0x20 else CS_MODE_ARM)
                                        _raw = self._read_user_bytes(pc & ~1, 4)
                                        if _raw:
                                            _ins = list(_md.disasm(_raw, pc & ~1))
                                            if _ins:
                                                _mn = f'{_ins[0].mnemonic} {_ins[0].op_str}'
                                    except Exception:
                                        _mn = None
                                    self.insn_invalid_unsupported.append({
                                        'pc': f'0x{pc:08x}', 'bytes': _hex,
                                        'mnemonic': _mn})
                                raise
                            in_kuser = (0xFFFF0000 <= pc < 0xFFFF1000)
                            if (not is_mem and not is_fetch and user_mode
                                    and (0x1000 <= pc < 0x60000000 or in_kuser)):
                                # E0443: distinguish a real CPU exception (SWI/UNDEF)
                                # from an instruction-fetch page fault by checking
                                # whether the faulting page is mapped in the guest
                                # page tables.  Mapped -> dispatch as supervisor call;
                                # unmapped -> fall through to the prefetch abort path.
                                # E0444: decode is unconditional for a mapped page;
                                # the capped dump is only for diagnostics.
                                if in_kuser:
                                    page_pa = self._walk_kernel_pt(pc)
                                else:
                                    info = self._walk_user_va(pc)
                                    self._capture_live_fault_page(pc)
                                    page_pa = None
                                    if info is not None and 'pa' in info:
                                        page_pa = int(info['pa'], 16)
                                is_svc = False
                                if page_pa is not None:
                                    try:
                                        raw = bytes(self.uc.mem_read(page_pa, 2))
                                        is_svc = (int.from_bytes(raw, 'little')
                                                  == 0xDF00)
                                    except Exception:
                                        is_svc = False
                                if page_pa is not None and not is_svc:
                                    # register-indirect load/store whose guest
                                    # translation fault has no captured READ/WRITE
                                    # address; decode base+offset and deliver a real
                                    # Data Abort so the kernel demand-pages it.
                                    decoded = self._decode_fault_address(pc, page_pa)
                                    if decoded is not None:
                                        target, is_write = decoded
                                        if len(self.fault_classification_trace) < 256:
                                            self.fault_classification_trace.append({
                                                'pc': f'0x{pc:08x}',
                                                'cpsr': f'0x{cpsr:08x}',
                                                'address': f'0x{target:08x}',
                                                'kind': 'data-decoded',
                                                'write': bool(is_write)})
                                        _walk = (
                                            self._walk_page_tables_phys_with_perms(target)
                                            if user_mode and 0x1000 <= target < 0x60000000
                                            else None
                                        )
                                        _need_perm = 2 if is_write else 1
                                        if _walk is not None and (_walk[1] & _need_perm) == _need_perm:
                                            self._flush_cputlb()
                                            self._flush_tlb()
                                            self._set_pc_and_cpsr(pc, cpsr)
                                            page = target & ~0xFFF
                                            pc_page = pc & ~0xFFF
                                            remove = getattr(self.uc, 'ctl_remove_cache', None)
                                            if remove is not None:
                                                try:
                                                    remove(page, page + 0x1000)
                                                    if pc_page != page:
                                                        remove(pc_page, pc_page + 0x1000)
                                                    self._page_cache_drops += 1
                                                except Exception:
                                                    pass
                                            try:
                                                self.uc.emu_start(
                                                    pc | (1 if (cpsr & 0x20) else 0),
                                                    0, count=1)
                                                executed += 1
                                                remaining = max(0, remaining - 1)
                                                continue
                                            except Exception:
                                                pass
                                            executed += 1
                                            remaining = max(0, remaining - 1)
                                            if self._note_fault(pc, target, cpsr):
                                                stop_reason = 'repeated_fault'
                                                break
                                            continue
                                        if self._deliver_data_abort(
                                                pc, cpsr, target, is_write):
                                            page = target & ~0xFFF
                                            try:
                                                if len(self.pte_rewalks) < 64:
                                                    self.pte_rewalks.append(
                                                        self._pte_report(page))
                                            except Exception:
                                                pass
                                            # E0445: ctl_remove_cache only drops code
                                            # TBs; force a true SoftMMU flush.
                                            self._flush_cputlb()
                                            remove = getattr(self.uc, 'ctl_remove_cache', None)
                                            if remove is not None:
                                                try:
                                                    remove(page, page + 0x1000)
                                                    self._page_cache_drops += 1
                                                except Exception:
                                                    pass
                                            executed += 1
                                            remaining = max(0, remaining - 1)
                                            if self._note_fault(pc, target, cpsr):
                                                stop_reason = 'repeated_fault'
                                                break
                                            continue
                                if page_pa is not None and is_svc:
                                    self._on_interrupt(self.uc, 2)
                                    executed += 1
                                    remaining = max(0, remaining - 1)
                                    if self._note_fault(pc, pc, cpsr):
                                        stop_reason = 'repeated_fault'
                                        break
                                    continue
                            if prefetch:
                                if captured and is_fetch and self._last_unmapped is not None:
                                    address = self._last_unmapped
                                elif (self._last_tlb_fault is not None
                                      and self._last_tlb_fault[1] == 18
                                      and (self._last_tlb_fault[0] & ~0xFFF) != (pc & ~0xFFF)):
                                    address = self._last_tlb_fault[0]
                                else:
                                    address = pc
                                if 0x1000 <= (pc & ~1) < 0x60000000:
                                    self._pending_evict_pages.add(pc & ~0xFFF)
                                    self._pending_cputlb_flush = True
                                if self._deliver_prefetch_abort(pc, cpsr, address):
                                    executed += 1
                                    remaining = max(0, remaining - 1)
                                    if self._note_fault(pc, address, cpsr):
                                        stop_reason = 'repeated_fault'
                                        break
                                    continue
                            # Demand-paged user VAs (stack/BSS/args) must be reported
                            # to the kernel's do_DataAbort so handle_mm_fault can
                            # allocate the anonymous page; only genuine invalid
                            # accesses (e.g. the futex probe at VA 0) fall back to the
                            # __ex_table fixup.  E0428 fix.
                            user_fault = (self.data_abort_delivery
                                          and access_addr is not None
                                          and 0x1000 <= access_addr < 0x60000000
                                          and (user_mode
                                               or (pc >= 0x60000000
                                                   and self._in_demand_page_helper(pc))))
                            if user_fault:
                                if self._deliver_data_abort(pc, cpsr, access_addr, write):
                                    executed += 1
                                    remaining = max(0, remaining - 1)
                                    if self._note_fault(pc, access_addr, cpsr):
                                        stop_reason = 'repeated_fault'
                                        break
                                    continue
                            if self._try_ex_table_fixup(pc) is not None:
                                executed += 1
                                remaining = max(0, remaining - 1)
                                continue
                            if (not user_fault and access_addr is not None
                                    and 0x1000 <= access_addr < 0xF0000000):
                                # Non-user-mapped fault (e.g. MODULES_VADDR / vmalloc
                                # lazy PGD sync): let do_DataAbort process it.
                                if self._deliver_data_abort(pc, cpsr, access_addr, write):
                                    executed += 1
                                    remaining = max(0, remaining - 1)
                                    if self._note_fault(pc, access_addr, cpsr):
                                        stop_reason = 'repeated_fault'
                                        break
                                    continue
                        if (self.exclusive_fallback
                                and 'UC_ERR_EXCEPTION' in message):
                            pc = self.uc.reg_read(UC_ARM_REG_PC)
                            cpsr = self.uc.reg_read(UC_ARM_REG_CPSR)
                            if self._emulate_exclusive(pc, cpsr):
                                self.exclusive_emulated += 1
                                if len(self.exclusive_trace) < 64:
                                    self.exclusive_trace.append(
                                        {'pc': f'0x{pc:08x}',
                                         'count': self.exclusive_emulated})
                                executed += 1
                                remaining = max(0, remaining - 1)
                                continue
                        raise
                    executed += slice_size
                    remaining -= slice_size
                    every = self.progress_every
                    if every and executed // every != (executed - slice_size) // every:
                        print(f'progress {executed} pc '
                              f'{self.uc.reg_read(UC_ARM_REG_PC):#x}', flush=True)
                    self._fault_guard_count = 0
                    self._abort_same_pc = 0
                    self._undef_same_pc = 0
                    self._advance_timer(slice_size)
                    pc = self.uc.reg_read(UC_ARM_REG_PC)
                    self.pc_samples[pc] += 1
                    if self.unmapped and self.unmapped_mmio_policy != 'observe-zero':
                        break
            if stop_reason is None and (
                    not self.unmapped or self.unmapped_mmio_policy == 'observe-zero'):
                stop_reason = 'instruction_limit'
        except Exception as error:  # single stop path, report it verbatim
            stop_error = f'{type(error).__name__}: {error}'
            try:
                self.exception_state = self._fault_state()
            except Exception:
                self.exception_state = {}
            if not self.unmapped:
                stop_reason = 'unicorn_error'
        if stop_reason is None and executed >= limit:
            stop_reason = 'instruction_limit'
        if self.unmapped and stop_reason is None:
            stop_reason = ('instruction_limit'
                           if self.unmapped_mmio_policy == 'observe-zero'
                           else 'unmapped_access')
        self._instructions = executed
        pc = self.uc.reg_read(UC_ARM_REG_PC)
        return self.report(pc, stop_reason, stop_error)

    def report(self, pc: int, stop_reason: str, stop_error: str | None) -> dict:
        hotspots = [
            {'pc': address, 'symbol': self.nearest_symbol(address), 'samples': count}
            for address, count in self.pc_samples.most_common(12)]
        mmio_reads = [{'address': address, 'count': count}
                      for address, count in self.mmio_counts.most_common(48)]
        serial = bytes(self.serial)
        return {
            'machine_id': self.machine_id,
            'entry_cpsr': f'0x{self.entry_cpsr:08x}',
            'entry_scr': self.entry_scr,
            'kernel_load': self.kernel_load,
            'initrd_load': self.initrd_load,
            'boot_params': self.boot_params,
            'kernel_bytes': len(self.kernel_raw),
            'initrd_bytes': len(self.initrd),
            'atags_bytes': len(self.atags),
            'instruction_limit': self.instruction_limit,
            'instructions_hooked': self._instructions,
            'stop_reason': stop_reason,
            'stop_error': stop_error,
            'stop_pc': pc,
            'stop_symbol': self.nearest_symbol(pc),
            'user_mode_entries': list(self.user_mode_entries),
            'prefetch_aborts_delivered': self.prefetch_aborts_delivered,
            'undef_exceptions_delivered': self.undef_exceptions_delivered,
            'undef_exception_trace': list(self.undef_exception_trace),
            'do_page_fault_frames': list(self.do_page_fault_frames),
            'tlb_flushes': self._tlb_flushes,
            'svc_stack_refreshes': self._svc_stack_refreshes,
            'last_thread_info': f'0x{self._last_thread_info:08x}',
            'last_thread_task': f'0x{self._last_thread_task:08x}',
            'stack_delivery_trace': list(self.stack_delivery_trace),
            'fault_guard_key': (list(self._fault_guard_key)
                                if self._fault_guard_key else None),
            'fault_guard_count': self._fault_guard_count,
            'fault_classification_trace': list(self.fault_classification_trace),
            'live_fault_dumps': list(self.live_fault_dumps),
            'pte_rewalks': list(self.pte_rewalks),
            'virtual_tlb': self.virtual_tlb,
            'virtual_tlb_on_userspace': self.virtual_tlb_on_userspace,
            'switch_mm_hooked': self.switch_mm_hooked,
            'switch_mm_hits': self.switch_mm_hits,
            'ttbr0_write_sites': [f'0x{s:08x}' for s in self.ttbr0_write_sites],
            'ttbr0_write_hits': self.ttbr0_write_hits,
            'pt_regs_frames': list(self.pt_regs_frames),
            'pt_regs_user_frames': list(self.pt_regs_user_frames),
            'ret_tail_hits': self.ret_tail_hits,
            'ret_tail_trace': list(self.ret_tail_trace),
            'user_restores': self.user_restores,
            'insn_invalid_recoveries': self.insn_invalid_recoveries,
            'insn_invalid_unsupported': list(self.insn_invalid_unsupported),
            'e0475_trace': list(self.e0475_trace),
            'e0483_trace': list(self.e0483_trace),
            'e0483_entry_stack': dict(self.e0483_entry_stack),
            'e0492_frames': list(self.e0492_frames),
            'e0492_msr': list(self.e0492_msr),
            'pt_frame_gen': self.pt_frame_gen,
            'pt_frame_consumed': self.pt_frame_consumed,
            'e0493_trace': list(self.e0493_trace),
            'e0495_trace': list(self.e0495_trace),
            'e0496_kernel': list(self.e0496_kernel),
            'e0497_trace': list(self.e0497_trace),
            'e0499_trace': list(self.e0499_trace),
            'e0500_frames': list(self.e0500_frames),
            'e0502_fill': list(self.e0502_fill),
            'e0504_trace': list(self.e0504_trace),
            'user_entry_trace': list(self.user_entry_trace),
            'ret_user_returns': list(self.ret_user_returns),
            'activation_pgd': (f'0x{self._activation_pgd:08x}'
                               if self._activation_pgd else None),
            'switch_mm_values': [f'0x{v:08x}' for v in self.switch_mm_values],
            'mm_resolve_trace': list(self.mm_resolve_trace),
            'virtual_tlb_error': self.virtual_tlb_error,
            'tlb_fills': self.tlb_fills,
            'tlb_fill_misses': self.tlb_fill_misses,
            'tlb_fill_samples': list(self.tlb_fill_samples),
            'cputlb_flushes': self.cputlb_flushes,
            'mm_fault_returns': list(self.mm_fault_returns),
            'page_cache_drops': self._page_cache_drops,
            'mid_tb_itstate_clears': self.mid_tb_itstate_clears,
            'svc_hits': self.svc_hits,
            'svc_trace': list(self.svc_trace),
            'kuser_hits': self.kuser_hits,
            'kuser_entries': list(self.kuser_entries),
            'pabt_mrc_hits': self.pabt_mrc_hits,
            'dabt_mrc_hits': self.dabt_mrc_hits,
            'vfp_d16_mvfr0': self.vfp_d16_mvfr0,
            'vfp_mvfr0_d16_hits': self.vfp_mvfr0_d16_hits,
            'vfp_mvfr0_sites': list(self.vfp_mvfr0_sites),
            'qemu_peripherals': self.qemu_peripherals,
            'frame_clock': None if self._cxd_bus is None else {
                'sysv_frames': self._cxd_bus.vph.frame,
                'vsyncs': self._cxd_bus.vph.vsyncs,
                'evsel_status': f'0x{self._cxd_bus.evsel.sta:08x}',
                'edge_pending': sorted(self._edge_pending),
            },
            'oracle_mmio_hits': {f'0x{addr:08x}': count
                                 for addr, count in self.oracle_mmio_hits.items()},
            'last_ifar': f'0x{self.last_ifar:08x}',
            'last_dfar': f'0x{self.last_dfar:08x}',
            'banked_store_hits': self.banked_store_hits,
            'banked_restore_hits': self.banked_restore_hits,
            'prefetch_abort_trace': list(self.prefetch_abort_trace),
            'mode_lr': {key: (hex(value) if value is not None else None)
                        for key, value in self.mode_lr.items()},
            'mode_sp': {key: (hex(value) if value is not None else None)
                        for key, value in self.mode_sp.items()},
            'trace_reg_values': {hex(pc): values
                                 for pc, values in self.trace_reg_values.items()},
            'serial_bytes': len(serial),
            'serial_text': serial.decode('latin1', errors='replace'),
            'mmio_reads': mmio_reads,
            'mmio_write_count': sum(self.mmio_writes.values()),
            'stub_hits': {f'0x{address:08x}': count
                          for address, count in self.stub_hits.items()},
            'boot_patch_hits': {f'0x{pc:08x}': count
                                for pc, count in self.patch_hits.items()},
            'hotspots': hotspots,
            'timer_model': {
                'enabled': self.timer_irq_model,
                'mask_async_abort_on_irq': self.mask_async_abort_on_irq,
                'timer_irq': self.timer_irq,
                'timer_counter': self._timer_counter,
                'timer_counter_reads': self._timer_counter_reads,
                'ticks_per_instruction': self.timer_ticks_per_instruction,
                'ticks_per_jiffy': self.timer_ticks_per_jiffy,
                'jiffies_injected': self._jiffies_injected,
                'pending': self._timer_irq_pending,
                'gic_enabled': self._gic_enabled,
                'gic_iar_reads': self._gic_iar_reads,
                'gic_iar_pcs': self._gic_iar_pcs,
                'injection_pcs': self._injection_pcs,
                'gic_eoir_writes': self._gic_eoir_writes,
                'gic_active_irq': self._gic_active_irq,
                'gic_registers': {f'{cpu}:0x{offset:02x}': value
                                  for (cpu, offset), value in
                                  sorted(self._gic_regs.items())},
            },
            'unmapped_mmio_mapped': [f'0x{page:08x}'
                                     for page in self._unmapped_mmio_mapped],
            'unmapped_addresses': [{'address': f'0x{address:08x}', 'count': count}
                                   for address, count in
                                   self.unmapped_counts.most_common(256)],
            'mmio_write_candidates': self._mmio_write_candidates(),
            'printk_trace': self.printk_trace[:200],
            'trace_point_hits': self.trace_point_hits[:64],
            'ex_table_entries': getattr(self, 'ex_table_entries', 0),
            'ex_table_fixups': self.ex_table_fixups,
            'ex_table_trace': self.ex_table_trace[:16],
            'data_aborts_delivered': self.data_aborts_delivered,
            'direct_map_completions': self.direct_map_completions,
            'data_abort_trace': self.data_abort_trace[:32],
            'exclusive_patched': self.exclusive_patched,
            'exclusive_emulated': self.exclusive_emulated,
            'exclusive_trace': self.exclusive_trace[:16],
            'exception_state': getattr(self, 'exception_state', None),
            'unmapped': [
                {key: (f'0x{value:08x}' if key in ('pc', 'address') else value)
                 for key, value in entry.items()} for entry in self.unmapped[:64]],
        }


def write_report(report: dict, path) -> None:
    from pathlib import Path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report, indent=2), encoding='utf-8')
