import gc
import struct
import unittest
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_CPSR, UC_ARM_REG_LR, UC_ARM_REG_PC,
                              UC_ARM_REG_R0, UC_ARM_REG_R2, UC_ARM_REG_R4,
                              UC_ARM_REG_R8, UC_ARM_REG_R10, UC_ARM_REG_SPSR,
                              UC_ARM_REG_SP)

from src.a6400.execution.machine_contract import build_atags
from src.a6400.execution.unicorn_machine import UnicornMachine
from tests.emulator.test_data_abort import tiny_kernel


def make_machine(**kwargs):
    machine = UnicornMachine(tiny_kernel(), b'\x00' * 64,
                             build_atags('console=ttyAM0', [(0, 0x40000000)]),
                             machine_id=0x0DA9, instruction_limit=100,
                             exclusive_patch=False, virtual_tlb_enabled=True,
                             **kwargs)
    return machine


class VirtualTlbTest(unittest.TestCase):
    def tearDown(self):
        gc.collect()

    def test_virtual_tlb_is_enabled(self):
        machine = make_machine()
        self.assertTrue(machine.virtual_tlb, machine.virtual_tlb_error)

    def test_pre_mmu_identity(self):
        machine = make_machine()
        machine._mmu_enabled = False
        self.assertEqual(machine._translate_virtual(0x00108A4C), 0x00108A4C)

    def test_kernel_lowmem_linear(self):
        machine = make_machine()
        machine._mmu_enabled = True
        self.assertEqual(machine._translate_virtual(0x60108A4C), 0x00108A4C)

    def test_high_vectors_identity(self):
        machine = make_machine()
        machine._mmu_enabled = True
        self.assertEqual(machine._translate_virtual(0xFFFF0018), 0xFFFF0018)

    def test_user_va_page_table_walk(self):
        machine = make_machine()
        machine._mmu_enabled = True
        ttbr0 = 0x10800000
        l2page = 0x10901000
        machine.uc.mem_write(ttbr0 + 0x040 * 4, struct.pack('<I', l2page | 1))
        machine.uc.mem_write(l2page + 0x31 * 4,
                             struct.pack('<I', 0x00431000 | 0x7F))
        machine._cached_ttbr0 = ttbr0
        self.assertEqual(machine._translate_virtual(0x04031000), 0x00431000)

    def test_user_va_unmapped_returns_none(self):
        machine = make_machine()
        machine._mmu_enabled = True
        machine._cached_ttbr0 = 0x10800000  # empty L1 -> no entry
        self.assertIsNone(machine._translate_virtual(0x04031000))

    def test_user_va_without_ttbr0_returns_none(self):
        machine = make_machine()
        machine._mmu_enabled = True
        machine._cached_ttbr0 = None
        self.assertIsNone(machine._translate_virtual(0x04031000))


    def test_ttbr1_section_translation(self):
        machine = make_machine()
        machine._mmu_enabled = True
        base = 0x10800000
        machine._cached_ttbr1 = base
        vaddr = 0xDF700000
        idx = (vaddr >> 20) & 0xFFF
        machine.uc.mem_write(base + idx * 4, struct.pack('<I', 0x00100000 | 2))
        self.assertEqual(machine._translate_virtual(vaddr), 0x00100000)

    def test_ttbr1_l2_small_page_translation(self):
        machine = make_machine()
        machine._mmu_enabled = True
        base = 0x10800000
        l2page = 0x10900000
        machine._cached_ttbr1 = base
        vaddr = 0xDF7FE123
        idx = (vaddr >> 20) & 0xFFF
        machine.uc.mem_write(base + idx * 4, struct.pack('<I', l2page | 1))
        l2idx = (vaddr >> 12) & 0xFF
        machine.uc.mem_write(l2page + l2idx * 4,
                             struct.pack('<I', 0x00401000 | 2))
        self.assertEqual(machine._translate_virtual(vaddr), 0x00401123)

    def test_ttbr1_unmapped_vmalloc_returns_none(self):
        machine = make_machine()
        machine._mmu_enabled = True
        machine._cached_ttbr1 = 0x10800000
        self.assertIsNone(machine._translate_virtual(0xDF7FE000))

    def test_mmio_window_identity(self):
        machine = make_machine()
        machine._mmu_enabled = True
        self.assertEqual(machine._translate_virtual(0xF2000000), 0xF2000000)


    def test_deferred_virtual_tlb_waits_for_userspace(self):
        machine = make_machine(virtual_tlb_on_userspace=True)
        self.assertFalse(machine.virtual_tlb)
        machine._install_virtual_tlb()
        self.assertTrue(machine.virtual_tlb)

    def test_deferred_flag_reported(self):
        machine = make_machine(virtual_tlb_on_userspace=True)
        machine._install_virtual_tlb()
        self.assertTrue(machine.virtual_tlb_on_userspace)
        self.assertTrue(machine._translate_virtual(0x60100000) is not None)


    def test_resolve_user_pgd_handles_empty_chain(self):
        machine = make_machine()
        machine.uc.reg_write(__import__('unicorn').arm_const.UC_ARM_REG_SP, 0)
        self.assertIsNone(machine._resolve_user_pgd())

    def test_refresh_user_ttbr0_keeps_kernel_pgd_without_fallback(self):
        machine = make_machine()
        machine._cached_ttbr0 = 0x00104000
        machine._refresh_user_ttbr0()
        # tiny machine TTBR0 reads 0 and no current->mm fallback exists
        self.assertEqual(machine._cached_ttbr0, 0)

    def test_page_table_maintenance_preserves_thumb_and_banked_registers(self):
        machine = make_machine(ram_banks=((0, 0x800000), (0x02001000, 0x1000)),
                               mmio_ranges=(),
                               sram=(0xFE000000, 0x1000))
        uc = machine.uc
        uc.cpr_write(15, 0, 2, 0, 0, 0, False, 0x10000)
        uc.cpr_write(15, 0, 2, 0, 1, 0, False, 0x14000)
        uc.cpr_write(15, 0, 2, 0, 0, 1, False, 0x18000)
        uc.cpr_write(15, 0, 2, 0, 1, 1, False, 0x1C000)
        for secure in (False, True):
            uc.cpr_write(15, 0, 1, 1, 0, 1, False, 0 if secure else 1)
            for name, args, expected in (
                    ('_read_cp15_ttbr0', (), 0x18000 if secure else 0x10000),
                    ('_read_cp15_ttbr1', (), 0x1C000 if secure else 0x14000),
                    ('_flush_cputlb', (), None),
                    ('_set_cp15_fault', (0x12345000, 0x805), True),
                    ('_set_cp15_prefetch_fault', (0x0402E790, 0x5), True)):
                with self.subTest(secure=secure, operation=name):
                    uc.reg_write(UC_ARM_REG_CPSR, 0xD3)
                    uc.reg_write(UC_ARM_REG_SP, 0x60006000)
                    uc.reg_write(UC_ARM_REG_LR, 0x60123454)
                    uc.reg_write(UC_ARM_REG_SPSR, 0x60000030)
                    machine._set_pc_and_cpsr(0x0402E790, 0xA0000030)
                    uc.reg_write(UC_ARM_REG_SP, 0x5E864F10)
                    uc.reg_write(UC_ARM_REG_LR, 0x0402E79F)
                    uc.reg_write(UC_ARM_REG_R0, 0x11223344)
                    self.assertEqual(getattr(machine, name)(*args), expected)
                    self.assertEqual(uc.reg_read(UC_ARM_REG_PC), 0x0402E790)
                    self.assertEqual(uc.reg_read(UC_ARM_REG_CPSR), 0xA0000030)
                    self.assertEqual(uc.reg_read(UC_ARM_REG_SP), 0x5E864F10)
                    self.assertEqual(uc.reg_read(UC_ARM_REG_LR), 0x0402E79F)
                    self.assertEqual(uc.reg_read(UC_ARM_REG_R0), 0x11223344)
                    uc.reg_write(UC_ARM_REG_CPSR, 0xD3)
                    self.assertEqual(uc.reg_read(UC_ARM_REG_SP), 0x60006000)
                    self.assertEqual(uc.reg_read(UC_ARM_REG_LR), 0x60123454)
                    self.assertEqual(uc.reg_read(UC_ARM_REG_SPSR), 0x60000030)

    def test_thumb_entry_continues_after_page_table_maintenance_hook(self):
        machine = make_machine(ram_banks=((0, 0x800000), (0x02001000, 0x1000)),
                               mmio_ranges=(),
                               sram=(0xFE000000, 0x1000))
        uc = machine.uc
        machine._mmu_enabled = True
        machine._cached_ttbr0 = 0x10000
        uc.cpr_write(15, 0, 2, 0, 0, 0, False, 0x10000)
        uc.mem_write(0x10100, struct.pack('<I', 0x20001))
        uc.mem_write(0x200B8, struct.pack('<I', 0x0042E03E))
        uc.mem_write(0x0042E790, bytes.fromhex('dff868a0dff868406846fee7'))
        uc.mem_write(0x0042E7FC, struct.pack('<2I', 0x11223344, 0x55667788))
        machine._set_pc_and_cpsr(0x0402E790, 0x30)
        uc.reg_write(UC_ARM_REG_SP, 0x5E864F10)
        hits = []

        def maintain_tables(uc, address, size, user_data):
            hits.append(address)
            machine._read_cp15_ttbr0()
            machine._read_cp15_ttbr1()
            machine._flush_cputlb()

        uc.hook_add(UC_HOOK_CODE, maintain_tables,
                    begin=0x0402E794, end=0x0402E794)
        uc.emu_start(0x0402E791, 0, count=4)
        self.assertEqual(hits, [0x0402E794])
        self.assertEqual(uc.reg_read(UC_ARM_REG_R10), 0x11223344)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R4), 0x55667788)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R0), 0x5E864F10)
        self.assertEqual(uc.reg_read(UC_ARM_REG_PC), 0x0402E79A)
        self.assertEqual(uc.reg_read(UC_ARM_REG_CPSR), 0x30)

    def test_softmmu_flush_rewalks_changed_user_data_mapping(self):
        machine = make_machine(ram_banks=((0, 0x800000),), mmio_ranges=(),
                               sram=(0xFE000000, 0x1000))
        uc = machine.uc
        machine._mmu_enabled = True
        machine._cached_ttbr0 = 0x10000
        uc.cpr_write(15, 0, 2, 0, 0, 0, False, 0x10000)
        uc.mem_write(0x10100, struct.pack('<I', 0x20001))
        uc.mem_write(0x200B8, struct.pack('<2I', 0x0042E03E, 0x0050003E))
        uc.mem_write(0x0042E100, bytes.fromhex('1068'))  # ldr r0, [r2]
        uc.mem_write(0x00500000, struct.pack('<I', 0x11223344))
        uc.mem_write(0x00501000, struct.pack('<I', 0x55667788))
        machine._set_pc_and_cpsr(0x0402E100, 0x30)
        uc.reg_write(UC_ARM_REG_R2, 0x0402F000)
        uc.emu_start(0x0402E101, 0, count=1)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R0), 0x11223344)
        uc.mem_write(0x200BC, struct.pack('<I', 0x0050103E))
        machine._flush_cputlb()
        uc.emu_start(0x0402E101, 0, count=1)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R0), 0x55667788)
        self.assertEqual(uc.reg_read(UC_ARM_REG_CPSR), 0x30)

    def test_mm_fault_return_does_not_fetch_stale_ifar(self):
        machine = make_machine(ram_banks=((0, 0x800000),), mmio_ranges=(),
                               sram=(0xFE000000, 0x1000))
        uc = machine.uc
        machine._mmu_enabled = True
        machine._cached_ttbr0 = 0x10000
        machine.last_ifar = 0x04162978  # The previous executable is not mapped here.
        pc = 0x60116E9C
        uc.mem_write(machine._kernel_phys(pc), bytes.fromhex('330010e30090a0e1'))
        uc.reg_write(UC_ARM_REG_R0, 0)  # Successful handle_mm_fault return.
        uc.reg_write(UC_ARM_REG_R4, 0x00058CEC)
        uc.emu_start(pc, pc + 8, count=2)
        self.assertEqual(uc.reg_read(UC_ARM_REG_PC), pc + 8)
        self.assertEqual(machine.tlb_fill_misses, 0)
        self.assertEqual(machine.mm_fault_returns[-1]['addr'], '0x00058cec')

    def test_user_return_flush_does_not_fetch_the_fault_page(self):
        pc = 0x6010DF34
        machine = make_machine(ram_banks=((0, 0x800000),), mmio_ranges=(),
                               sram=(0xFE000000, 0x1000), symbols={'ret_to_user': pc})
        uc = machine.uc
        machine._mmu_enabled = True
        machine._pte_dirty = True
        machine._last_fault_page = 0x0402F000
        machine._cached_ttbr0 = 0x10000
        uc.cpr_write(15, 0, 2, 0, 0, 0, False, 0x10000)
        uc.reg_write(UC_ARM_REG_SP, 0x60030000)
        uc.mem_write(machine._kernel_phys(pc), bytes.fromhex('0100a0e3'))  # mov r0, #1
        uc.emu_start(pc, pc + 4)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R0), 1)
        self.assertEqual(machine.cputlb_flushes, 1)
        self.assertEqual(machine.tlb_fill_misses, 0)

    def test_thumb_user_return_executes_original_entry_without_warmup(self):
        from unittest.mock import patch

        program = struct.pack('<3I', 0xE321F0D3, 0xE51FF004, 0x6010DF64)
        machine = UnicornMachine(
            program, b'\0' * 64, b'\0' * 64, machine_id=0xDA9,
            ram_banks=((0, 0x800000),), mmio_ranges=(),
            sram=(0xFE000000, 0x1000), virtual_tlb_enabled=True,
            timer_irq_model=False, data_abort_delivery=False,
            exclusive_fallback=False, instruction_limit=50, sample_interval=10)
        uc = machine.uc
        uc.mem_write(0x0010DF64, struct.pack('<I', 0xE1B0F00E))
        uc.cpr_write(15, 0, 2, 0, 0, 0, False, 0x10000)
        machine._cached_ttbr0 = 0x10000
        uc.mem_write(0x10100, struct.pack('<I', 0x20001))
        uc.mem_write(0x200B8, struct.pack('<I', 0x0042E03E))
        uc.mem_write(0x0042E790, bytes.fromhex('dff868a0dff868406846fee7'))
        uc.mem_write(0x0042E7FC, struct.pack('<2I', 0x11223344, 0x55667788))
        words = [0] * 18
        words[13:17] = [0x5E864F10, 0x0402E79F, 0x0402E790, 0x30]
        machine.pt_frame_last = {
            'gen': 1, 'words': words, 'sp': words[13], 'lr': words[14],
            'pc': words[15], 'cpsr': words[16],
        }
        with patch.object(machine, '_emulate_thumb_prologue',
                          wraps=machine._emulate_thumb_prologue) as warmup:
            report = machine.run()
        warmup.assert_not_called()
        self.assertEqual(report['stop_reason'], 'instruction_limit')
        self.assertIsNone(report['stop_error'])
        self.assertEqual(report['insn_invalid_recoveries'], 0)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R10), 0x11223344)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R4), 0x55667788)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R0), 0x5E864F10)
        self.assertEqual(uc.reg_read(UC_ARM_REG_PC), 0x0402E79A)
        self.assertEqual(uc.reg_read(UC_ARM_REG_CPSR), 0x30)
        self.assertTrue(any(row['pc'] == '0x0402e798' and row['thumb']
                            for row in report['user_entry_trace']))

    def test_invalid_arm_instruction_stops_without_forcing_thumb(self):
        from unicorn.arm_const import UC_ARM_REG_FPEXC

        machine = make_machine(ram_banks=((0, 0x800000),), mmio_ranges=(),
                               sram=(0xFE000000, 0x1000),
                               data_abort_delivery=False)
        uc = machine.uc
        machine._mmu_enabled = True
        machine._cached_ttbr0 = 0x10000
        uc.cpr_write(15, 0, 2, 0, 0, 0, False, 0x10000)
        uc.mem_write(0x10100, struct.pack('<I', 0x20001))
        uc.mem_write(0x200B8, struct.pack('<I', 0x0042E03E))
        uc.mem_write(0x0042E100, struct.pack('<I', 0xE7F000F0))  # udf #0
        uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
        machine._set_pc_and_cpsr(0x0402E100, 0x10)
        report = machine.run()
        self.assertEqual(report['stop_reason'], 'unicorn_error')
        self.assertIn('UC_ERR_INSN_INVALID', report['stop_error'])
        self.assertEqual(report['stop_pc'], 0x0402E100)
        self.assertEqual(uc.reg_read(UC_ARM_REG_CPSR), 0x10)
        self.assertEqual(report['insn_invalid_recoveries'], 0)
        self.assertEqual(report['insn_invalid_unsupported'], [{
            'pc': '0x0402e100', 'bytes': 'f000f0e7', 'mnemonic': 'udf #0',
        }])


    def test_switch_mm_hook_uses_verified_image_address(self):
        machine = make_machine()
        machine._install_switch_mm_hook()
        self.assertTrue(machine.switch_mm_hooked)
        self.assertEqual(machine.switch_mm_address, 0x6011A26C)


    def test_ttbr0_opcode_scanner(self):
        import struct
        code = struct.pack('<2I', 0xEE023F10, 0xEAFFFFFE)
        sites = UnicornMachine.scan_ttbr0_write_sites(code, 0x60100000)
        self.assertEqual(sites, [(0x60100000, 3)])

    def test_ttbr0_opcode_scanner_ignores_other_mcr(self):
        import struct
        code = struct.pack('<2I', 0xEE010F10, 0xEE021F30)
        self.assertEqual(UnicornMachine.scan_ttbr0_write_sites(code, 0), [])


    def test_ttbr0_scanner_finds_thumb2_encoding(self):
        import struct
        code = struct.pack('<2H', 0xEE02, 0x3F10)
        sites = UnicornMachine.scan_ttbr0_write_sites(code, 0x60100000)
        self.assertEqual(sites, [(0x60100000, 3)])

    def test_ttbr0_scanner_thumb2_ignores_other_second_halfword(self):
        import struct
        code = struct.pack('<2H', 0xEE02, 0x1F30)
        self.assertEqual(UnicornMachine.scan_ttbr0_write_sites(code, 0), [])


    def test_activation_pgd_default_and_read32_phys(self):
        machine = make_machine()
        self.assertIsNone(machine._activation_pgd)
        # tiny machine: PA 0 is mapped and zero-filled
        self.assertEqual(machine._read32_phys(0), 0)


    def test_pt_regs_base_finder_accepts_user_frame(self):
        import struct
        machine = make_machine()
        ti = 0x0100E000
        base = ti + 0x1FB0
        # pc slot (+0x3C) = user address, cpsr slot (+0x40) = USR mode
        machine.uc.mem_write(base + 0x3C, struct.pack('<I', 0x0402E790))
        machine.uc.mem_write(base + 0x40, struct.pack('<I', 0x00000070))
        self.assertEqual(machine._find_pt_regs_base(ti + 0x1FB0), base)

    def test_pt_regs_base_finder_rejects_zero_pc(self):
        import struct
        machine = make_machine()
        ti = 0x0100E000
        base = ti + 0x1FB0
        machine.uc.mem_write(base + 0x3C, struct.pack('<I', 0))
        machine.uc.mem_write(base + 0x40, struct.pack('<I', 0x00000070))
        self.assertIsNone(machine._find_pt_regs_base(ti + 0x1FB0))


    def test_thumb_pc_keeps_thumb_bit_for_even_target(self):
        self.assertEqual(UnicornMachine.thumb_pc(0x0402E790, 0x70), 0x0402E791)

    def test_thumb_pc_clears_lsb_in_arm_mode(self):
        self.assertEqual(UnicornMachine.thumb_pc(0x0402E791, 0x10), 0x0402E790)


    def test_pending_user_return_defaults_and_restore_counter(self):
        machine = make_machine()
        self.assertIsNone(machine.pending_user_spsr)
        self.assertIsNone(machine.pending_user_lr)
        self.assertEqual(machine.user_restores, 0)
        # thumb_pc is used by the restore for the final PC
        self.assertEqual(machine.thumb_pc(0x0402E790, 0x30), 0x0402E791)


    def test_set_pc_and_cpsr_thumb_lsb(self):
        machine = make_machine()
        machine._set_pc_and_cpsr(0x0402E790, 0x00000030)
        self.assertTrue(machine.uc.reg_read(UC_ARM_REG_CPSR) & 0x20)
        # Unicorn keeps PC even in the register while T is carried in CPSR
        self.assertIn(machine.uc.reg_read(UC_ARM_REG_PC), (0x0402E790, 0x0402E791))

    def test_thumb_ldr_w_and_mov_executes_under_odd_begin(self):
        machine = make_machine()
        # ldr.w sl,[pc,#0x68] ; ldr.w r4,[pc,#0x68] ; mov r0,sp (Thumb-2)
        code = bytes.fromhex('dff868a0dff868406846')
        machine.uc.mem_write(0x0402E790, code)
        machine.uc.reg_write(UC_ARM_REG_CPSR, 0x00000030)
        machine.uc.reg_write(UC_ARM_REG_PC, 0x0402E791)
        machine.uc.emu_start(0x0402E791, 0, count=3)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_PC), 0x0402E79A)


    def test_pending_user_resume_defaults_to_none(self):
        machine = make_machine()
        self.assertIsNone(machine._pending_user_resume)
        # the run loop resumes at the odd LSB produced by thumb_pc
        self.assertEqual(machine.thumb_pc(0x0402E790, 0x30), 0x0402E791)


    def test_tb_remove_cache_api_available(self):
        machine = make_machine()
        # E0467: eviction API used when a cached ARM TB must be dropped
        self.assertTrue(hasattr(machine.uc, 'ctl_remove_cache'))
        machine.uc.ctl_remove_cache(0x0402E000, 0x0402F000)


    def test_thumb_prologue_interpreter_ldr_mov_bl(self):
        machine = make_machine()
        base = 0x0402E790
        code = bytes.fromhex('dff868a0dff86840684603f085f9')
        machine._read_user_bytes = lambda va, size: code[va - base:va - base + size]
        machine._read_user32 = lambda va: {
            0x0402E7FC: 0x11111111,
            0x0402E800: 0x22222222,
        }.get(va)
        res = machine._emulate_thumb_prologue(0x0402E791, 0x30)
        self.assertIsNotNone(res)
        pc, cpsr, trace = res
        self.assertEqual(pc, 0x0403_1AA8)
        self.assertEqual(cpsr, 0x30)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_LR), 0x0402E79F)
        self.assertEqual([t[0] for t in trace], ['ldr', 'ldr', 'mov', 'bl'])


    def test_general_stepper_push(self):
        machine = make_machine()
        mem = {}
        code = bytes.fromhex('2de9f04f')  # push.w {r4-r11,lr}
        machine._read_user_bytes = lambda va, size: code[va - 0x1000:va - 0x1000 + size]
        machine._read_user32 = lambda va: mem.get(va)
        machine._write_user32 = lambda va, val: mem.__setitem__(va, val & 0xFFFFFFFF) or True
        machine.uc.reg_write(UC_ARM_REG_SP, 0x2000)
        machine.uc.reg_write(UC_ARM_REG_R4, 0xAA)
        machine.uc.reg_write(UC_ARM_REG_LR, 0xBB)
        res = machine._step_user_thumb_insns(0x1000, 0x30, max_steps=1)
        self.assertIsNotNone(res)
        pc, cpsr, trace = res
        self.assertEqual([t[0] for t in trace], ['push'])
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_SP), 0x2000 - 9 * 4)
        self.assertIn(0xAA, mem.values())

    def test_general_stepper_ldr_mov_bl(self):
        machine = make_machine()
        base = 0x0402E790
        code = bytes.fromhex('dff868a0dff86840684603f085f9')
        machine._read_user_bytes = lambda va, size: code[va - base:va - base + size]
        machine._read_user32 = lambda va: {
            0x0402E7FC: 0x11111111,
            0x0402E800: 0x22222222,
        }.get(va)
        res = machine._step_user_thumb_insns(0x0402E791, 0x30, max_steps=4)
        self.assertIsNotNone(res)
        pc, cpsr, trace = res
        self.assertEqual(pc, 0x04031AA8)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_LR), 0x0402E79F)
        self.assertEqual([t[0] for t in trace], ['ldr', 'ldr', 'mov', 'bl'])


    def test_general_stepper_callee_block(self):
        machine = make_machine()
        base = 0x04031AA8
        # push.w {r4-r11,lr} ; sub.w sp,sp,#0x1f4 ; ldr.w r3,[pc,#0x7c]
        code = bytes.fromhex('2de9f04fadf51f7ddff87c34')
        machine._read_user_bytes = lambda va, size: code[va - base:va - base + size]
        machine._read_user32 = lambda va: 0x12345678
        machine._write_user32 = lambda va, val: True
        machine.uc.reg_write(UC_ARM_REG_SP, 0x8000)
        res = machine._step_user_thumb_insns(0x04031AA8, 0x30, max_steps=3)
        self.assertIsNotNone(res)
        pc, cpsr, trace = res
        self.assertEqual([t[0] for t in trace], ['push', 'alu', 'ldr'])
        self.assertEqual(pc, 0x04031AB4)


    def test_should_recover_insn_invalid_ignores_cpsr_mode(self):
        machine = make_machine()

        class _Resident:
            def __call__(self, pc):
                return True
        machine._user_pa = _Resident()
        # userspace VA, INSN message -> recover regardless of the reported CPSR
        self.assertTrue(machine._should_recover_insn_invalid(
            0x04031AAC, 'Invalid instruction (UC_ERR_INSN_INVALID)'))
        # kernel VA / other message -> no recovery
        self.assertFalse(machine._should_recover_insn_invalid(
            0x6010DF64, 'Invalid instruction (UC_ERR_INSN_INVALID)'))
        self.assertFalse(machine._should_recover_insn_invalid(
            0x04031AAC, 'Unhandled CPU exception (UC_ERR_EXCEPTION)'))


    def test_stepper_keeps_partial_progress_on_unmapped_literal(self):
        machine = make_machine()
        base = 0x04031AAC
        # sub.w sp,sp,#0x27c ; ldr.w r3,[pc,#0x47c] (literal page not resident)
        code = bytes.fromhex('adf51f7ddff87c34')
        machine._read_user_bytes = lambda va, size: code[va - base:va - base + size]
        machine._read_user32 = lambda va: None
        machine._write_user32 = lambda va, val: True
        res = machine._step_user_thumb_insns(0x04031AAC, 0x30, max_steps=2)
        self.assertIsNotNone(res)
        pc, cpsr, trace = res
        self.assertEqual([t[0] for t in trace], ['alu'])
        self.assertEqual(pc, 0x04031AB0)


    def test_take_user_frame_consumes_generation_once(self):
        machine = make_machine()
        machine.pt_frame_last = {
            'gen': 7, 'words': [0] * 18, 'sp': 0x5E864F10,
            'lr': 0x0402E790, 'pc': 0x0402E790, 'cpsr': 0x30}
        first = machine._take_user_frame()
        self.assertIsNotNone(first)
        self.assertEqual(first['gen'], 7)
        self.assertEqual(machine.pt_frame_consumed, 7)
        # a second hit without a new frame must not replay it
        self.assertIsNone(machine._take_user_frame())
        machine.pt_frame_last = dict(machine.pt_frame_last, gen=8)
        third = machine._take_user_frame()
        self.assertEqual(third['gen'], 8)
        self.assertEqual(machine.pt_frame_consumed, 8)


    def test_stepper_mov_high_register_r8(self):
        machine = make_machine()
        base = 0x04031A00
        # mov r8, r0 (Thumb 16-bit high-register MOV, 0x4680 -> bytes 80 46)
        code = bytes.fromhex('8046')
        machine._read_user_bytes = lambda va, size: code[va - base:va - base + size]
        machine.uc.reg_write(UC_ARM_REG_R0, 0x5E864F10)
        res = machine._step_user_thumb_insns(0x04031A00, 0x30, max_steps=1)
        self.assertIsNotNone(res)
        self.assertEqual(res[0], 0x04031A02)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_R8), 0x5E864F10)

    def test_user_frame_keeps_r8_slot_for_restore(self):
        machine = make_machine()
        words = [0] * 18
        words[8] = 0x5E864F10   # pt_regs + 0x20 == r8
        words[0x34 // 4] = 0x5E864F10
        words[0x3C // 4] = 0x0402E790
        words[0x40 // 4] = 0x30
        machine.pt_frame_last = {'gen': 1, 'words': words,
                                 'sp': words[13], 'lr': 0,
                                 'pc': words[15], 'cpsr': words[16]}
        frame = machine._take_user_frame()
        self.assertIsNotNone(frame)
        self.assertEqual(frame['words'][8], 0x5E864F10)
        self.assertEqual(frame['words'][0x20 // 4], 0x5E864F10)
        self.assertEqual(frame['words'][0x34 // 4], 0x5E864F10)


    def test_recovery_syncs_user_bank_from_frame(self):
        machine = make_machine()
        words = [0] * 18
        words[0] = 0x5E864F10
        words[8] = 0x5E864F10
        words[0x34 // 4] = 0x5E864F10
        words[0x3C // 4] = 0x04031AA8
        words[0x40 // 4] = 0x30
        machine.pt_frame_last = {'gen': 5, 'words': words, 'sp': 0x5E864F10,
                                 'lr': 0, 'pc': 0x04031AA8, 'cpsr': 0x30}
        machine.uc.reg_write(UC_ARM_REG_CPSR, 0x13 | 0xC0)
        machine.uc.reg_write(UC_ARM_REG_SP, 0x60472990)  # kernel stack
        synced = machine._sync_user_bank_for_recovery(0x04031AAC, 0x30)
        self.assertTrue(synced)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_SP), 0x5E864EEC)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_R0), 0x5E864F10)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_R8), 0x5E864F10)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_CPSR) & 0x1F, 0x10)


    def test_svc_return_address_uses_true_instruction(self):
        machine = make_machine()
        # SVC at 0x1000 (Thumb 0xDF00); Unicorn reports PC after it (0x1002)
        machine.uc.mem_write(0x1000, b'\x00\xdf')
        self.assertEqual(machine._svc_return_address(0x1002, True), 0x1002)
        self.assertEqual(machine._svc_return_address(0x1000, True), 0x1002)
        # ARM SVC (0xEF000000) at 0x2000, reported after it (0x2004)
        machine.uc.mem_write(0x2000, b'\x00\x00\x00\xef')
        self.assertEqual(machine._svc_return_address(0x2004, False), 0x2004)


    def test_swi_captures_user_bank_before_switch(self):
        machine = make_machine()
        machine._symbol_address = lambda name: 0x6010DFA0
        machine.uc.mem_write(0x1000, b'\x00\xdf')
        machine.uc.reg_write(UC_ARM_REG_CPSR, 0x30)   # USR + Thumb
        machine.uc.reg_write(UC_ARM_REG_SP, 0x5E864F10)
        machine.uc.reg_write(UC_ARM_REG_LR, 0x0403E98D)
        machine.uc.mem_write(0x1000, b'\x00\xdf')
        machine.uc.reg_write(UC_ARM_REG_PC, 0x1001)  # odd PC keeps Thumb
        machine._on_interrupt(machine.uc, 2)
        self.assertEqual(machine.svc_hits, 1)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_SPSR), 0x30)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_CPSR) & 0x1F, 0x13)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_PC), 0x6010DFA0)
        self.assertEqual(machine._pending_swi_usr_sp, 0x5E864F10)
        self.assertEqual(machine._pending_swi_usr_lr, 0x0403E98D)

    def test_vector_stmdb_is_single_shot_into_ptregs(self):
        machine = make_machine()
        machine._pending_swi_usr_sp = 0x5E864C04
        machine._pending_swi_usr_lr = 0x0403E98D
        machine.uc.reg_write(UC_ARM_REG_CPSR, 0x93)
        machine.uc.reg_write(UC_ARM_REG_R8, 0x6000303C)
        self.assertTrue(machine._emulate_vector_stmdb(machine.uc))
        self.assertFalse(machine._emulate_vector_stmdb(machine.uc))
        sp_word = int.from_bytes(bytes(machine.uc.mem_read(0x3034, 4)), 'little')
        lr_word = int.from_bytes(bytes(machine.uc.mem_read(0x3038, 4)), 'little')
        self.assertEqual(sp_word, 0x5E864C04)
        self.assertEqual(lr_word, 0x0403E98D)


    def test_fast_syscall_return_enforces_usr_thumb_odd_pc(self):
        machine = make_machine()
        words = [0] * 20
        words[0] = 0x00055000          # r0 = new brk value
        words[13] = 0x5E864C04         # SP_usr
        words[14] = 0x0403E98D         # LR_usr
        words[15] = 0x0403EB16         # ARM_pc (even, Thumb code)
        words[16] = 0x600000F0         # ARM_cpsr USR|Thumb
        machine._fast_frame = {'words': words[:17], 'sp': 0x5E864C04,
                               'lr': 0x0403E98D, 'pc': 0x0403EB16,
                               'cpsr': 0x600000F0}
        machine.uc.reg_write(UC_ARM_REG_CPSR, 0x93)
        machine.uc.reg_write(UC_ARM_REG_LR, 0x0403EB16)
        self.assertTrue(machine._restore_fast_syscall_user_return(machine.uc))
        cpsr = machine.uc.reg_read(UC_ARM_REG_CPSR)
        self.assertEqual(cpsr & 0x1F, 0x10)     # USR
        self.assertTrue(cpsr & 0x20)            # Thumb
        # Unicorn stores the architectural (even) PC; CPSR.T carries Thumb.
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_PC) & ~1, 0x0403EB16)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_SP), 0x5E864C04)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_R0), 0x00055000)

    def test_decode_fault_address_thumb2_pc_relative_ldr(self):
        machine = make_machine()
        # 0x0402F666: dff8ec4b = ldr.w r4, [pc, #0xbec] -> ((0x0402F666 + 4) & ~3) + 0xbec = 0x04030254
        pa = 0x0042F666
        machine.uc.mem_write(pa, bytes.fromhex('dff8ec4b'))
        machine._set_pc_and_cpsr(0x0402F666, 0x600000F0)
        decoded = machine._decode_fault_address(0x0402F666, pa)
        self.assertEqual(decoded, (0x04030254, False))

    def test_capture_fast_syscall_frame_preserves_live_r0(self):
        machine = make_machine()
        sp = 0x60003000
        # At 0x6010DEC0 (ret_fast_syscall), pt_regs starts at sp + 8, where ARM_r0 is 0,
        # while the live syscall return value (0x00055000) is in R0.
        pt_words = [0] * 18
        pt_words[13] = 0x5E864C04
        pt_words[14] = 0x0403E98D
        pt_words[15] = 0x0403EB16
        pt_words[16] = 0x600000F0
        import struct
        machine.uc.mem_write(0x3008, struct.pack('<18I', *pt_words))
        machine.uc.reg_write(UC_ARM_REG_CPSR, 0x93)
        machine.uc.reg_write(UC_ARM_REG_SP, sp)
        machine.uc.reg_write(UC_ARM_REG_R0, 0x00055000)
        machine._capture_fast_syscall_frame(machine.uc)
        self.assertIsNotNone(machine._fast_frame)
        self.assertEqual(machine._fast_frame['words'][0], 0x00055000)

    def test_on_tlb_fill_respects_armv7_ap2_and_xn(self):
        import struct
        from types import SimpleNamespace

        machine = make_machine(virtual_tlb_on_userspace=True)
        machine._mmu_enabled = True
        machine._cached_ttbr0 = 0x10000
        # L1 entry at 0x10000 + (0x041F1000 >> 20)*4 -> L2 table at 0x20000 (type=1)
        l1_idx = (0x041F1000 >> 20) & 0xFFF
        machine.uc.mem_write(0x10000 + l1_idx * 4, struct.pack('<I', 0x20001))

        cases = [
            # empty_zero_page: AP[2]=1, AP[1:0]=3, XN=1 -> READ only (1)
            (0xBF7FEE7F, 0xBF7FE000, 1),
            # RWX: AP[2]=0, AP[1:0]=3, XN=0 -> ALL (7)
            (0x0042F03E, 0x0042F000, 7),
            # RX: AP[2]=1, AP[1:0]=3, XN=0 -> READ|EXEC (5)
            (0x0042F23E, 0x0042F000, 5),
            # RW NX: AP[2]=0, AP[1:0]=3, XN=1 -> READ|WRITE (3)
            (0x0042F03F, 0x0042F000, 3),
        ]
        l2_idx = (0x041F1000 >> 12) & 0xFF
        for l2_val, expected_pa, expected_perms in cases:
            machine.uc.mem_write(0x20000 + l2_idx * 4, struct.pack('<I', l2_val))
            entry = SimpleNamespace(paddr=0, perms=0)
            ok = machine._on_tlb_fill(machine.uc, 0x041F1000, 16, entry)
            self.assertTrue(ok)
            self.assertEqual(entry.paddr, expected_pa)
            self.assertEqual(entry.perms, expected_perms)

    def test_ret_tail_hook_allows_kuser_target_and_clears_stale_user_sp(self):
        import struct
        machine = make_machine(trace_user_mode=True)
        machine.uc.mem_map(0x6010D000, 0x1000)
        # nop at 0x6010DF64 (mov r0, r0)
        machine.uc.mem_write(0x6010DF64, struct.pack('<I', 0xE1A00000))
        machine.user_sp = 0x5E8CB81C
        machine._restore_user_sp = True
        words = [0] * 17
        words[13] = 0x5E8CB830
        words[14] = 0x0403E98D
        words[15] = 0xFFFF0FC0
        words[16] = 0x00000010
        machine.pt_frame_last = {
            'gen': 7,
            'words': words,
            'sp': 0x5E8CB830,
            'lr': 0x0403E98D,
            'pc': 0xFFFF0FC0,
            'cpsr': 0x00000010,
        }
        machine.uc.reg_write(UC_ARM_REG_CPSR, 0x93)
        machine.uc.emu_start(0x6010DF64, 0x6010DF68, count=1)
        self.assertEqual(machine.user_restores, 1)
        self.assertFalse(machine._restore_user_sp)
        self.assertIsNotNone(machine._pending_user_frame)
        self.assertEqual(machine._pending_user_frame['sp'], 0x5E8CB830)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_SP), 0x5E8CB830)

    def test_decode_fault_address_kuser_strexeq_write(self):
        import struct
        machine = make_machine()
        pa = 0x00000FCC
        # strexeq r3, r1, [r2] (0x01823F91)
        machine.uc.mem_write(pa, struct.pack('<I', 0x01823F91))
        machine._set_pc_and_cpsr(0xFFFF0FCC, 0x00000010)
        machine.uc.reg_write(UC_ARM_REG_R2, 0x00123450)
        self.assertEqual(machine._decode_fault_address(0xFFFF0FCC, pa),
                         (0x00123450, True))

    def test_sync_uart_irq_asserts_and_clears_irq_150(self):
        from src.a6400.execution.unicorn_machine import (
            GIC_ICCICR, PL011_IMSC, UART_IRQ)
        machine = make_machine()
        machine._gic_regs[('cpu', GIC_ICCICR)] = 1
        machine._gic_regs[('dist', 0x000)] = 1
        machine._uart_regs[PL011_IMSC] = 0x10  # RXIM
        machine.serial_input = bytearray()
        machine._sync_uart_irq()
        self.assertNotIn(UART_IRQ, machine._soft_pending_irqs)
        machine.serial_input.extend(b'a')
        machine._sync_uart_irq()
        self.assertIn(UART_IRQ, machine._soft_pending_irqs)
        machine._uart_regs[PL011_IMSC] = 0
        machine._sync_uart_irq()
        self.assertNotIn(UART_IRQ, machine._soft_pending_irqs)

    def test_switch_mm_hook_sets_pending_cputlb_flush_on_pgd_change(self):
        import struct
        machine = make_machine(virtual_tlb_on_userspace=True)
        addr = machine.switch_mm_address
        page = addr & ~0xFFF
        machine.uc.mem_map(page, 0x1000)
        machine.uc.mem_write(addr, struct.pack('<I', 0xE1A00000))
        machine._cached_ttbr0 = 0x1DC1C000
        machine._pending_cputlb_flush = False
        machine.uc.reg_write(UC_ARM_REG_R0, 0x1DC18000)
        machine.uc.emu_start(addr, addr + 4, count=1)
        self.assertTrue(machine._pending_cputlb_flush)

    def test_updater_boot_contract_overrides_amba2_console(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from src.a6400.execution.machine_contract import UpdaterBootContract

        boot_set = SimpleNamespace(
            load_addresses=lambda: {
                'kernel_load': 0x00108000,
                'boot_params': 0x00000100,
            },
            raw_kernel_image=lambda: b'\x00' * 64,
            kernel_file=lambda: SimpleNamespace(sha256='0' * 64),
            read=lambda path: (
                b'kemco amba2.console=0 init=/linuxrc mem=128M@0x00000000*\n'
                if path == 'boot/kemco.txt' else b''
            ),
        )
        with patch('src.a6400.execution.machine_contract.recover_machine_descriptor',
                   return_value=SimpleNamespace(machine_id=0x0DA9)):
            contract = UpdaterBootContract(
                boot_set, cmdline_extra="init=/bin/ash amba2.console=1"
            )
        self.assertIn("amba2.console=1", contract.cmdline)
        self.assertNotIn("amba2.console=0", contract.cmdline)
        self.assertIn("amba2.console=1", contract.sony_cmdline)
        self.assertNotIn("amba2.console=0", contract.sony_cmdline)

    def test_data_tlb_fill_inside_thumb_it_block_does_not_leak_itstate(self):
        from unicorn import UcError
        from unicorn.arm_const import (UC_ARM_REG_R1, UC_ARM_REG_R3,
                                       UC_ARM_REG_R7, UC_ARM_REG_R9)

        machine = make_machine(ram_banks=((0, 0x800000),), mmio_ranges=(),
                               sram=(0xFE000000, 0x1000))
        uc = machine.uc
        machine._mmu_enabled = True
        machine._cached_ttbr0 = 0x10000
        uc.cpr_write(15, 0, 2, 0, 0, 0, False, 0x10000)
        uc.mem_write(0x10010, struct.pack('<2I', 0x20001, 0x21001))
        # Map 0x404000..0x405FFF -> PA 0x404000..0x405FFF (RX) and 0x500000..0x503FFF (RW)
        uc.mem_write(0x20000 + 4 * 4, struct.pack('<2I', 0x40423E, 0x40523E))
        for i in range(4):
            uc.mem_write(0x21000 + i * 4, struct.pack('<I', (0x500000 + i * 0x1000) | 0x3F))

        entry, target, stop = 0x404E3A, 0x405346, 0x40535C
        uc.mem_write(entry, bytes.fromhex('12f0040f1fbf2368d9f80410cb18c4f8b431002800f07a82'))
        uc.mem_write(target, bytes.fromhex('d7f840e0d9f81000def81c1088423ff67dadbef82c30d9f800e0'))
        for address, word in ((0x500000, 0x1000), (0x501004, 0x9000),
                              (0x502040, 0x503000), (0x50301C, 0x9000), (0x50302C, 5)):
            uc.mem_write(address, struct.pack('<I', word))
        machine._set_pc_and_cpsr(entry, 0x30)
        uc.reg_write(UC_ARM_REG_R2, 5)
        uc.reg_write(UC_ARM_REG_R4, 0x500000)
        uc.reg_write(UC_ARM_REG_R7, 0x502000)
        uc.reg_write(UC_ARM_REG_R9, 0x501000)
        uc.reg_write(UC_ARM_REG_LR, 0)
        uc.emu_start(entry | 1, stop, count=32)
        self.assertEqual(uc.reg_read(UC_ARM_REG_PC), stop)
        self.assertEqual(uc.reg_read(UC_ARM_REG_LR), 0x503000)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R3), 5)
        self.assertEqual(uc.reg_read(UC_ARM_REG_CPSR) & 0x0600FC00, 0)
        self.assertGreaterEqual(machine.mid_tb_itstate_clears, 1)

        # Page-crossing IT block: UC_MEM_FETCH on next page must preserve active ITSTATE.
        uc.mem_write(0x404FFA, bytes.fromhex('052a0cbf2368632300bf'))
        uc.mem_write(0x500000, struct.pack('<I', 0x12345678))
        machine._flush_cputlb()
        machine._flush_tlb()
        machine._set_pc_and_cpsr(0x404FFA, 0x30)
        uc.reg_write(UC_ARM_REG_R2, 5)
        uc.reg_write(UC_ARM_REG_R4, 0x500000)
        uc.emu_start(0x404FFB, 0x405004, count=10)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R3), 0x12345678)

        # Unmapped slot 2 after mapped slot 1 must still report architectural CPSR.IT on fault.
        uc.mem_write(0x404100, bytes.fromhex('052a04bf2368d9f80010'))
        machine._flush_cputlb()
        machine._flush_tlb()
        machine._set_pc_and_cpsr(0x404100, 0x30)
        uc.reg_write(UC_ARM_REG_R2, 5)
        uc.reg_write(UC_ARM_REG_R4, 0x500000)
        uc.reg_write(UC_ARM_REG_R9, 0x600000)
        with self.assertRaises(UcError):
            uc.emu_start(0x404101, 0x404120, count=10)
        self.assertEqual(uc.reg_read(UC_ARM_REG_PC), 0x404106)
        self.assertNotEqual(uc.reg_read(UC_ARM_REG_CPSR) & 0x0600FC00, 0)

    def test_privileged_module_pte_permissions_and_svc_stm_abort(self):
        from types import SimpleNamespace
        from unicorn.arm_const import UC_ARM_REG_R3

        dabt_svc = 0x6010D980
        machine = make_machine(
            ram_banks=((0, 0x800000), (0x02002000, 0x1000)), mmio_ranges=(), sram=(0xFE000000, 0x1000),
            symbols={'__dabt_svc': dabt_svc}, timer_irq_model=False,
            exclusive_fallback=False, sample_interval=5)
        uc = machine.uc
        machine._mmu_enabled = True
        machine._cached_ttbr0 = 0x10000
        uc.cpr_write(15, 0, 2, 0, 0, 0, False, 0x10000)

        # Map 0x5F000000 with AP[2]=0, AP[1:0]=1, XN=0 (PAGE_KERNEL_EXEC: 0x1E)
        l1_idx = (0x5F000000 >> 20) & 0xFFF
        uc.mem_write(0x10000 + l1_idx * 4, struct.pack('<I', 0x20001))
        uc.mem_write(0x20000, struct.pack('<I', 0x0050001E))

        # Privileged SVC gets RWX (7); USR gets None (no access -> fault)
        uc.reg_write(UC_ARM_REG_CPSR, 0x13)
        entry = SimpleNamespace(paddr=0, perms=0)
        self.assertTrue(machine._on_tlb_fill(uc, 0x5F000000, 17, entry))
        self.assertEqual((entry.paddr, entry.perms), (0x00500000, 7))
        uc.reg_write(UC_ARM_REG_CPSR, 0x10)
        self.assertFalse(machine._on_tlb_fill(uc, 0x5F000000, 16, entry))

        # Unmapped 0x5F001000 touched by SVC `stmge r0!, {r2, r3}` delivers __dabt_svc
        # which installs the L2 PTE and returns via svc_exit (+0x44) to complete the store.
        pc = 0x60259304
        uc.mem_write(machine._kernel_phys(pc), struct.pack('<2I', 0xA8A0000C, 0xEAFFFFFE))
        # Tiny __dabt_svc stub: install PTE at 0x20004, jump to +0x44, return to saved PC
        stub = [
            0xE59F6044,  # +0x00: ldr r6, [pc, #0x44] -> 0x60020004
            0xE59F7044,  # +0x04: ldr r7, [pc, #0x44] -> 0x0050101E
            0xE5867000,  # +0x08: str r7, [r6]
            0xE5904004,  # +0x0C: ldr r4, [r0, #4] (saved fault PC)
            0xE5905008,  # +0x10: ldr r5, [r0, #8] (saved CPSR)
            0xE5900000,  # +0x14: ldr r0, [r0]    (saved R0)
            0xEA000009,  # +0x18: b +0x44
        ] + [0xE1A00000] * 10 + [
            0xE16FF005,  # +0x44: msr SPSR_fsxc, r5 (triggers svc_abort_exit_flush)
            0xE1B0F004,  # +0x48: movs pc, r4
            0x60020004,  # +0x4C: literal L2 slot (kernel VA)
            0x0050101E,  # +0x50: literal PAGE_KERNEL_EXEC PTE
        ]
        uc.mem_write(machine._kernel_phys(dabt_svc), struct.pack(f'<{len(stub)}I', *stub))
        uc.reg_write(UC_ARM_REG_CPSR, 0x20000013)
        uc.reg_write(UC_ARM_REG_SP, 0x60030000)
        uc.reg_write(UC_ARM_REG_R0, 0x5F001000)
        uc.reg_write(UC_ARM_REG_R2, 0xAABBCCDD)
        uc.reg_write(UC_ARM_REG_R3, 0x11223344)
        uc.reg_write(UC_ARM_REG_PC, pc)
        report = machine.run(limit=20)
        self.assertEqual(report['data_abort_trace'][-1]['address'], '0x5f001000')
        self.assertTrue(report['data_abort_trace'][-1]['write'])
        self.assertEqual(struct.unpack('<2I', uc.mem_read(0x00501000, 8)),
                         (0xAABBCCDD, 0x11223344))

    def test_kthread_return_executes_ldmdb_after_consumed_user_frame(self):
        from unicorn.arm_const import UC_ARM_REG_R5, UC_ARM_REG_R7

        machine = make_machine(
            ram_banks=((0, 0x800000),), mmio_ranges=(), sram=(0xFE000000, 0x1000),
            allow_nonsecure_af_writes=True)
        uc = machine.uc
        machine._mmu_enabled = True
        # Leave a previously consumed user pt_frame_last in place.
        machine.pt_frame_gen = 3
        machine.pt_frame_consumed = 3
        machine.pt_frame_last = {
            'gen': 3, 'words': [0] * 18, 'sp': 0x5EAAF000,
            'lr': 0x04070001, 'pc': 0x00008C10, 'cpsr': 0x60000030,
        }
        # Exact fast_ret_to_user (0x6010DF48..0x6010DF68) + kernel_thread_helper
        fast_ret = [
            0xE59D1040,  # 0x6010DF48: ldr r1, [sp, #64]
            0xE5BDE03C,  # 0x6010DF4C: ldr lr, [sp, #60]!
            0xE16FF001,  # 0x6010DF50: msr SPSR_fsxc, r1
            0xF57FF01F,  # 0x6010DF54: clrex
            0xE95D7FFF,  # 0x6010DF58: ldmdb sp, {r0-r12, sp, lr}^
            0xE1A00000,  # 0x6010DF5C: nop
            0xE28DD00C,  # 0x6010DF60: add sp, sp, #12
            0xE1B0F00E,  # 0x6010DF64: movs pc, lr
        ]
        uc.mem_write(machine._kernel_phys(0x6010DF48), struct.pack('<8I', *fast_ret))
        helper = [
            0xE121F007,  # 0x6010EB84: msr CPSR_c, r7
            0xE1A00004,  # 0x6010EB88: mov r0, r4
            0xE1A0E006,  # 0x6010EB8C: mov lr, r6
            0xE1A0F005,  # 0x6010EB90: mov pc, r5
        ]
        uc.mem_write(machine._kernel_phys(0x6010EB84), struct.pack('<4I', *helper))
        target_fn = 0x60200000
        uc.mem_write(machine._kernel_phys(target_fn), struct.pack('<I', 0xEAFFFFFE))

        # Build kthread pt_regs at 0x6004FFB0 (top of stack 0x6004FFF8)
        sp = 0x6004FFB0
        pt_regs = [0] * 18
        pt_regs[4] = 0xCAFE1234   # kthread arg -> r0
        pt_regs[5] = target_fn    # kthread fn  -> pc
        pt_regs[6] = 0x60138110   # do_exit     -> lr
        pt_regs[7] = 0x00000013   # SVC, IRQ on -> cpsr
        pt_regs[15] = 0x6010EB84  # kernel_thread_helper
        pt_regs[16] = 0x00000093  # SPSR for fast_ret_to_user
        uc.mem_write(machine._kernel_phys(sp), struct.pack('<18I', *pt_regs))

        uc.reg_write(UC_ARM_REG_CPSR, 0x93)
        uc.reg_write(UC_ARM_REG_SP, sp)
        uc.reg_write(UC_ARM_REG_R4, 0)
        uc.reg_write(UC_ARM_REG_R5, 0)
        uc.reg_write(UC_ARM_REG_R7, 0)
        uc.emu_start(0x6010DF48, target_fn, count=20)
        self.assertEqual(uc.reg_read(UC_ARM_REG_PC), target_fn)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R0), 0xCAFE1234)
        self.assertEqual(uc.reg_read(UC_ARM_REG_LR), 0x60138110)
        self.assertEqual(uc.reg_read(UC_ARM_REG_SP), 0x6004FFF8)
        self.assertEqual(uc.reg_read(UC_ARM_REG_CPSR) & 0xFF, 0x13)

    def test_svc_fast_syscall_clears_stale_user_fast_frame(self):
        machine = make_machine()
        uc = machine.uc
        sp = 0x60003000
        # Seed a stale user _fast_frame, then run _capture_fast_syscall_frame on an SVC frame.
        machine._fast_frame = {
            'words': [0] * 17, 'sp': 0x5EAACF00, 'lr': 0x04159E7B,
            'pc': 0x04182304, 'cpsr': 0x60000010,
        }
        svc_pt_words = [0] * 18
        svc_pt_words[15] = 0x5F047100  # kernel module caller PC
        svc_pt_words[16] = 0x60000013  # SVC CPSR
        uc.mem_write(0x3008, struct.pack('<18I', *svc_pt_words))
        uc.reg_write(UC_ARM_REG_CPSR, 0x93)
        uc.reg_write(UC_ARM_REG_SPSR, 0x60000013)
        uc.reg_write(UC_ARM_REG_SP, sp)
        uc.reg_write(UC_ARM_REG_LR, 0x5F047100)
        machine._capture_fast_syscall_frame(uc)
        self.assertIsNone(machine._fast_frame)
        self.assertFalse(machine._restore_fast_syscall_user_return(uc))
        self.assertEqual(uc.reg_read(UC_ARM_REG_CPSR) & 0x1F, 0x13)

    def test_switch_mm_flushes_virtual_tlb_immediately_mid_slice(self):
        from unicorn.arm_const import UC_ARM_REG_R1, UC_ARM_REG_R3

        switch_mm = 0x6011A36C
        machine = make_machine(
            ram_banks=((0, 0x800000),), mmio_ranges=(), sram=(0xFE000000, 0x1000),
            symbols={'cpu_v7_switch_mm': switch_mm})
        uc = machine.uc
        machine._mmu_enabled = True
        pgd_a, pgd_b = 0x10000, 0x14000
        l2_a, l2_b = 0x20000, 0x21000
        uc.mem_write(pgd_a, struct.pack('<I', l2_a | 1))
        uc.mem_write(pgd_b, struct.pack('<I', l2_b | 1))
        uc.mem_write(l2_a + 8 * 4, struct.pack('<I', 0x0040803F))
        uc.mem_write(l2_b + 8 * 4, struct.pack('<I', 0x0050803F))
        uc.mem_write(0x00408000, struct.pack('<I', 0x11111111))
        uc.mem_write(0x00508000, struct.pack('<I', 0x22222222))

        # Single continuous run: read [0x8000] under pgd_a into r1, call switch_mm(pgd_b),
        # then immediately read [0x8000] into r3 without leaving emu_start.
        uc.mem_write(machine._kernel_phys(switch_mm), struct.pack('<I', 0xE12FFF1E))  # bx lr
        code_va = 0x60200000
        seq = [
            0xE3A02902,  # mov r2, #0x8000
            0xE5921000,  # ldr r1, [r2] -> 0x11111111
            0xE3A00905,  # mov r0, #0x14000 (pgd_b)
            0xEBFC68DA,  # bl 0x6011A36C (switch_mm)
            0xE3A02902,  # mov r2, #0x8000
            0xE5923000,  # ldr r3, [r2] -> must be 0x22222222, not stale 0x11111111
        ]
        # Compute exact BL offset from code_va + 0x0C to switch_mm
        rel = (switch_mm - (code_va + 0x0C + 8)) // 4
        seq[3] = 0xEB000000 | (rel & 0x00FFFFFF)
        uc.mem_write(machine._kernel_phys(code_va), struct.pack('<6I', *seq))

        machine._cached_ttbr0 = pgd_a
        uc.reg_write(UC_ARM_REG_CPSR, 0x13)
        uc.emu_start(code_va, code_va + 24, count=16)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R1), 0x11111111)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R3), 0x22222222)

    def test_unmapped_user_prefetch_abort_ignores_aliasing_physical_ram(self):
        from unicorn.arm_const import UC_ARM_REG_R5

        pabt_usr = 0x6010DE20
        machine = make_machine(
            ram_banks=((0, 0x05000000),), mmio_ranges=(), sram=(0xFE000000, 0x1000),
            symbols={'__pabt_usr': pabt_usr}, timer_irq_model=False,
            exclusive_fallback=False, sample_interval=5)
        uc = machine.uc
        machine._mmu_enabled = True
        machine._cached_ttbr0 = 0x10000
        uc.cpr_write(15, 0, 2, 0, 0, 0, False, 0x10000)

        # Place `ldrb r3, [r5, #0x36]` (0x7DA9 -> r5+0x16, or 0xF8953036) at PA == user VA
        # while leaving user VA 0x0404DD4C unmapped in TTBR0.
        user_pc = 0x0404DD4C
        uc.mem_write(user_pc, bytes.fromhex('95f83630'))
        uc.mem_write(machine._kernel_phys(pabt_usr), struct.pack('<I', 0xEAFFFFFE))
        uc.reg_write(UC_ARM_REG_CPSR, 0x13)
        uc.reg_write(UC_ARM_REG_SP, 0x60030000)
        machine._set_pc_and_cpsr(user_pc, 0x20000030)
        uc.reg_write(UC_ARM_REG_R5, 0x04068554)
        report = machine.run(limit=5)
        self.assertEqual(report['last_ifar'], '0x0404dd4c')
        self.assertEqual(report['prefetch_abort_trace'][-1]['address'], '0x0404dd4c')

    def test_fast_work_pending_and_ret_from_fork_do_not_trigger_return_hooks(self):
        machine = make_machine(ram_banks=((0, 0x800000),), mmio_ranges=(),
                               sram=(0xFE000000, 0x1000))
        uc = machine.uc
        machine._mmu_enabled = True
        # Put `mov r0, #7` at 0x6010DEFC (fast_work_pending) and `mov r0, #9` at 0x6010DF68 (ret_from_fork)
        uc.mem_write(machine._kernel_phys(0x6010DEFC), struct.pack('<I', 0xE3A00007))
        uc.mem_write(machine._kernel_phys(0x6010DF68), struct.pack('<I', 0xE3A00009))
        machine._fast_frame = {
            'words': [0xDEAD] * 17, 'sp': 0x5E001000, 'lr': 0x040884DB,
            'pc': 0x04094C5C, 'cpsr': 0x60000010, 'svc_sp': 0x60030FA8,
        }
        machine.pt_frame_gen = 1
        machine.pt_frame_consumed = 0
        machine.pt_frame_last = {
            'gen': 1, 'words': [0xBEEF] * 18, 'sp': 0x5E001000,
            'lr': 0x040884DB, 'pc': 0x04094C5C, 'cpsr': 0x60000030,
        }
        uc.reg_write(UC_ARM_REG_CPSR, 0x93)
        uc.reg_write(UC_ARM_REG_SPSR, 0x60000010)
        uc.reg_write(UC_ARM_REG_SP, 0x60030FA8)
        uc.emu_start(0x6010DEFC, 0x6010DF00, count=1)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R0), 7)
        self.assertEqual(uc.reg_read(UC_ARM_REG_CPSR) & 0x1F, 0x13)
        uc.emu_start(0x6010DF68, 0x6010DF6C, count=1)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R0), 9)
        self.assertEqual(uc.reg_read(UC_ARM_REG_CPSR) & 0x1F, 0x13)

    def test_no_work_pending_replaces_preempted_user_frame_from_other_task(self):
        ret_to_user = 0x6010DF34
        machine = make_machine(
            ram_banks=((0, 0x800000),), mmio_ranges=(), sram=(0xFE000000, 0x1000),
            symbols={'ret_to_user': ret_to_user})
        uc = machine.uc
        machine._mmu_enabled = True
        # Task A entered ret_to_user (0x6010DF34) on stack 0x60030FB0, then rescheduled.
        machine.pt_frame_gen = 5
        machine.pt_frame_consumed = 4
        machine.pt_frame_last = {
            'gen': 5, 'words': [0xFFFFFFFE] + [0] * 17, 'sp': 0x5EFC23B4,
            'lr': 0x040884DB, 'pc': 0x04094C5C, 'cpsr': 0x60000010,
            'svc_sp': 0x60030FB0,
        }
        # Task B returns via ret_to_user_from_irq -> no_work_pending (0x6010DF48) on stack 0x60050FB0.
        fast_ret = [
            0xE59D1040,  # 0x6010DF48: ldr r1, [sp, #64]
            0xE5BDE03C,  # 0x6010DF4C: ldr lr, [sp, #60]!
            0xE16FF001,  # 0x6010DF50: msr SPSR_fsxc, r1
            0xF57FF01F,  # 0x6010DF54: clrex
            0xE95D7FFF,  # 0x6010DF58: ldmdb sp, {r0-r12, sp, lr}^
            0xE1A00000,  # 0x6010DF5C: nop
            0xE28DD00C,  # 0x6010DF60: add sp, sp, #12
            0xE1B0F00E,  # 0x6010DF64: movs pc, lr
        ]
        uc.mem_write(machine._kernel_phys(0x6010DF48), struct.pack('<8I', *fast_ret))
        sp_b = 0x60050FB0
        pt_b = [0x1234] + [0] * 17
        pt_b[13] = 0x5EE01000
        pt_b[14] = 0x04094C61
        pt_b[15] = 0x04094C5C
        pt_b[16] = 0x60000030  # Thumb USR!
        uc.mem_write(machine._kernel_phys(sp_b), struct.pack('<18I', *pt_b))
        uc.reg_write(UC_ARM_REG_CPSR, 0x93)
        uc.reg_write(UC_ARM_REG_SP, sp_b)
        uc.emu_start(0x6010DF48, 0x6010DF68, count=10)
        self.assertEqual(machine._pending_user_resume, (0x04094C5D, 0x60000030))
        self.assertEqual(uc.reg_read(UC_ARM_REG_R0), 0x1234)

    def test_run_consumes_pending_user_resume_without_leaking_across_slices(self):
        ret_to_user = 0x6010DF34
        machine = make_machine(
            ram_banks=((0, 0x800000),), mmio_ranges=(), sram=(0xFE000000, 0x1000),
            symbols={'ret_to_user': ret_to_user})
        uc = machine.uc
        machine.slice_size = 8
        machine._mmu_enabled = False
        # Kernel snippet at 0x8000 branches straight to _ret_tail_hook (0x6010DF64) in physical/mapped RAM;
        # use identity low RAM 0x2000 for user resume target and 0x1000 for kernel start.
        uc.mem_write(0x2000, struct.pack('<2I', 0xE2800001, 0xEAFFFFFE))  # add r0, r0, #1; b .
        calls = 0
        orig_emu_start = uc.emu_start
        def fake_emu_start(begin, until, timeout=0, count=0):
            nonlocal calls
            calls += 1
            if calls == 1:
                machine._pending_user_resume = (0x2000, 0x60000010)
                machine._pending_user_frame = {
                    'words': [41] + [0] * 17, 'sp': 0x7000, 'lr': 0x2004,
                    'pc': 0x2000, 'cpsr': 0x60000010,
                }
                return
            return orig_emu_start(begin, until, timeout=timeout, count=count)
        uc.emu_start = fake_emu_start
        try:
            machine.run(limit=9)
        finally:
            uc.emu_start = orig_emu_start
        self.assertIsNone(machine._pending_user_resume)
        self.assertIsNone(machine._pending_user_frame)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R0), 42)
        self.assertEqual(uc.reg_read(UC_ARM_REG_SP), 0x7000)

    def test_deliver_undef_exception_passes_raw_lr_und_to_und_usr(self):
        und_usr = 0x6010DD20
        machine = make_machine(
            ram_banks=((0, 0x3000000),), mmio_ranges=(), sram=(0xFE000000, 0x1000),
            data_abort_delivery=True, symbols={'__und_usr': und_usr})
        uc = machine.uc
        machine._mmu_enabled = True
        machine._cached_ttbr0 = 0x10000
        # SVC stack with valid thread_info->task so _deliver_user_exception keeps live SVC SP.
        uc.reg_write(UC_ARM_REG_CPSR, 0xD3)
        uc.reg_write(UC_ARM_REG_SP, 0x60031FB0)
        uc.mem_write(machine._kernel_phys(0x6003000C), struct.pack('<I', 0x60900000))
        # Switch to Thumb USR (0x60000030) and deliver undef at 0x07DAD7A0.
        uc.reg_write(UC_ARM_REG_CPSR, 0x60000030)
        uc.reg_write(UC_ARM_REG_R0, 0xCAFEBABE)
        uc.reg_write(UC_ARM_REG_SP, 0x5EBFC518)
        uc.reg_write(UC_ARM_REG_LR, 0x07DAD643)
        self.assertTrue(machine._deliver_undef_exception(0x07DAD7A0, 0x60000030))
        self.assertEqual(uc.reg_read(UC_ARM_REG_PC), und_usr)
        self.assertEqual(uc.reg_read(UC_ARM_REG_CPSR) & 0x1F, 0x13)
        scratch = struct.unpack('<3I', uc.mem_read(machine.VECTOR_SCRATCH_PA, 12))
        self.assertEqual(scratch, (0xCAFEBABE, 0x07DAD7A2, 0x60000030))
        # ARM USR (0x60000010) must pass PC + 4 in scratch[1].
        uc.reg_write(UC_ARM_REG_CPSR, 0x60000010)
        uc.reg_write(UC_ARM_REG_R0, 0x11223344)
        self.assertTrue(machine._deliver_undef_exception(0x04094C5C, 0x60000010))
        scratch_arm = struct.unpack('<3I', uc.mem_read(machine.VECTOR_SCRATCH_PA, 12))
        self.assertEqual(scratch_arm, (0x11223344, 0x04094C60, 0x60000010))
        self.assertEqual(machine.undef_exceptions_delivered, 2)

    def test_deliver_undef_exception_supports_thumb_svc_und_svc(self):
        und_svc = 0x6010DAE0
        machine = make_machine(
            ram_banks=((0, 0x4000000),), mmio_ranges=(), sram=(0xFE000000, 0x1000),
            data_abort_delivery=True, symbols={'__und_svc': und_svc})
        uc = machine.uc
        machine._mmu_enabled = True
        uc.reg_write(UC_ARM_REG_CPSR, 0x60000033)  # Thumb-2 SVC (av-cam.bin)
        uc.reg_write(UC_ARM_REG_SP, 0x60935ED0)
        uc.reg_write(UC_ARM_REG_LR, 0x6379D543)
        uc.reg_write(UC_ARM_REG_R0, 0x64501234)
        self.assertTrue(machine._deliver_undef_exception(0x63CEFF54, 0x60000033))
        self.assertEqual(uc.reg_read(UC_ARM_REG_PC), und_svc)
        self.assertEqual(uc.reg_read(UC_ARM_REG_CPSR) & 0x3F, 0x13)
        self.assertEqual(uc.reg_read(UC_ARM_REG_SP), 0x60935ED0)
        self.assertEqual(uc.reg_read(UC_ARM_REG_LR), 0x6379D543)
        scratch = struct.unpack('<3I', uc.mem_read(machine.VECTOR_SCRATCH_PA + 0x10, 12))
        self.assertEqual(scratch, (0x64501234, 0x63CEFF56, 0x60000033))


if __name__ == '__main__':
    unittest.main()
