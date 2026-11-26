"""Timer/GIC IRQ model checks for the CXD90045 Unicorn machine (E0417)."""
import struct
import unittest

from src.a6400.execution.arm_atags import build_atags
from src.a6400.execution.unicorn_machine import (
    GIC_CPU,
    GIC_DIST,
    GIC_ICCICR,
    TIMER_BASE,
    TIMER_TICKS_PER_JIFFY,
    UnicornMachine,
)

MACHINE_ID = 0x0DA9
STORAGE = 0x00100000


def gic_program():
    """Reads GIC IAR, GICD_TYPER and the free-running timer counter into RAM."""
    image = bytearray(0x100)
    words = [
        0xE321F0D3,  # 0x00 msr cpsr_c,#0xd3 (kernel head word)
        0xE59F0054,  # 0x04 ldr r0,[pc,#0x54] -> 0x60 GICC
        0xE3A01001,  # 0x08 mov r1,#1
        0xE5801000,  # 0x0c str r1,[r0]          ICCICR = 1
        0xE59F004C,  # 0x10 ldr r0,[pc,#0x4c] -> 0x64 GICD
        0xE5801000,  # 0x14 str r1,[r0]          GICD_CTLR = 1
        0xE59F0048,  # 0x18 ldr r0,[pc,#0x48] -> 0x68 GICC
        0xE590200C,  # 0x1c ldr r2,[r0,#0x0c]    IAR read
        0xE59F3044,  # 0x20 ldr r3,[pc,#0x44] -> 0x6c STORAGE
        0xE5832000,  # 0x24 str r2,[r3]
        0xE59F0040,  # 0x28 ldr r0,[pc,#0x40] -> 0x70 GICD
        0xE5902004,  # 0x2c ldr r2,[r0,#4]       GICD_TYPER read
        0xE59F303C,  # 0x30 ldr r3,[pc,#0x3c] -> 0x74 STORAGE+4
        0xE5832000,  # 0x34 str r2,[r3]
        0xE59F0038,  # 0x38 ldr r0,[pc,#0x38] -> 0x78 TIMER_BASE
        0xE590210C,  # 0x3c ldr r2,[r0,#0x10c]  counter read
        0xE59F3034,  # 0x40 ldr r3,[pc,#0x34] -> 0x7c STORAGE+8
        0xE5832000,  # 0x44 str r2,[r3]
        0xEAFFFFFE,  # 0x48 b .
    ]
    for index, word in enumerate(words):
        struct.pack_into('<I', image, index * 4, word)
    struct.pack_into('<8I', image, 0x60, GIC_CPU, GIC_DIST, GIC_CPU, STORAGE,
                     GIC_DIST, STORAGE + 4, TIMER_BASE, STORAGE + 8)
    return bytes(image)


class TimerGicModelTests(unittest.TestCase):
    def make_machine(self, program=None, **kwargs):
        options = dict(machine_id=MACHINE_ID, instruction_limit=40000,
                       sample_interval=1000, timer_irq_model=True,
                       timer_ticks_per_instruction=1,
                       timer_ticks_per_jiffy=1000,
                       ram_banks=((0, 0x800000),))
        options.update(kwargs)
        return UnicornMachine(program or gic_program(), b'\x00' * 64,
                              build_atags('console=ttyAM0', options['ram_banks']),
                              **options)

    def test_gic_reads_are_served_by_the_model(self):
        machine = self.make_machine()
        report = machine.run()
        iar = struct.unpack('<I', machine.uc.mem_read(STORAGE, 4))[0]
        typer = struct.unpack('<I', machine.uc.mem_read(STORAGE + 4, 4))[0]
        counter = struct.unpack('<I', machine.uc.mem_read(STORAGE + 8, 4))[0]
        self.assertEqual(iar, 1023)  # spurious: no pending interrupt yet
        self.assertEqual(typer, 0x1F)  # 1024 interrupt lines reported
        self.assertEqual(counter, 0)  # first slice has not advanced time yet
        self.assertEqual(report['timer_model']['gic_iar_reads'], 1)

    def test_timer_counter_advances_with_executed_instructions(self):
        machine = self.make_machine(instruction_limit=30000, sample_interval=5000)
        report = machine.run()
        self.assertGreaterEqual(report['timer_model']['timer_counter'], 30000)
        self.assertGreater(report['timer_model']['timer_counter_reads'], 0)

    def test_advance_timer_marks_one_pending_jiffy(self):
        machine = self.make_machine()
        machine.timer_ticks_per_jiffy = 100
        machine.timer_irq_start_instructions = 0
        machine._timer_counter = 0
        machine._next_jiffy_tick = 100
        machine._advance_timer(100)
        self.assertEqual(machine._timer_irq_pending, 1)
        self.assertEqual(machine._next_jiffy_tick, 200)
        machine._advance_timer(100)
        self.assertEqual(machine._timer_irq_pending, 1)  # not queued twice
        self.assertEqual(machine._next_jiffy_tick, 300)

    def test_frame_clock_irq_waits_for_gic_enable_then_latches(self):
        from src.a6400.devices.cxd90045_peripherals import ENGINE_FRAME_US, IRQ_SYSV
        machine = self.make_machine(
            qemu_peripherals=True, instruction_limit=40, sample_interval=40,
            timer_irq_start_instructions=0)
        machine._gic_regs[('cpu', GIC_ICCICR)] = 1
        machine._gic_regs[('dist', 0x000)] = 1
        machine._advance_timer(ENGINE_FRAME_US)
        self.assertEqual(machine._cxd_bus.vph.frame, 1)
        self.assertIn(IRQ_SYSV, machine._edge_pending)
        self.assertNotIn(IRQ_SYSV, machine._soft_pending_irqs)
        enable = 1 << (IRQ_SYSV % 32)
        offset = 0x100 + (IRQ_SYSV // 32) * 4
        machine._gic_regs[('dist', offset)] = enable
        also = 1 << (101 % 32)
        machine._gic_regs[('dist', offset)] |= also
        self.assertEqual(machine._gic_regs[('dist', offset)] & enable, enable)
        self.assertEqual(machine._gic_regs[('dist', offset)] & also, also)
        machine._sync_frame_irqs(set())
        self.assertIn(IRQ_SYSV, machine._soft_pending_irqs)
        self.assertNotIn(IRQ_SYSV, machine._edge_pending)
        from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R0
        site = 0x00108080
        machine.uc.mem_write(site, struct.pack('<IIII', 0xE59F0004, 0xE5900000, 0, 0xF3402098))
        machine.uc.reg_write(UC_ARM_REG_PC, site)
        machine.uc.emu_start(site, site + 8, count=2)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_R0), 1)

    def test_gic_isenabler_keeps_both_frame_clock_lines(self):
        image = bytearray(0x80)
        words = [
            0xE321F0D3,
            0xE59F0024,  # ldr r0, [pc, #0x24] -> GICD at 0x30
            0xE59F1024,  # ldr r1, [pc, #0x24] -> bit 98
            0xE580110C,  # str r1, [r0, #0x10c]
            0xE59F1020,  # ldr r1, [pc, #0x20] -> bit 101
            0xE580110C,
            0xE590210C,  # ldr r2, [r0, #0x10c]
            0xE59F3018,  # ldr r3, [pc, #0x18] -> storage at 0x3c
            0xE5832000,
            0xEAFFFFFE,
        ]
        for index, word in enumerate(words):
            struct.pack_into('<I', image, index * 4, word)
        bit98 = 1 << 2
        bit101 = 1 << 5
        struct.pack_into('<4I', image, 0x30, GIC_DIST, bit98, bit101, STORAGE)
        machine = self.make_machine(bytes(image), instruction_limit=20, sample_interval=20)
        machine.run()
        enabled = struct.unpack_from('<I', machine.uc.mem_read(STORAGE, 4))[0]
        self.assertEqual(enabled & (bit98 | bit101), bit98 | bit101)

    def test_irq_injection_enters_arm_irq_vector_with_banked_state(self):
        from unicorn.arm_const import (UC_ARM_REG_CPSR, UC_ARM_REG_LR,
                                       UC_ARM_REG_PC, UC_ARM_REG_SPSR)
        machine = self.make_machine()
        machine._gic_regs[('cpu', GIC_ICCICR)] = 1
        machine._gic_regs[('dist', 0x000)] = 1
        machine.uc.reg_write(UC_ARM_REG_CPSR, 0x13)
        machine.uc.reg_write(UC_ARM_REG_PC, 0x60123456)
        machine._timer_irq_pending = 1
        self.assertTrue(machine._try_inject_irq())
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_CPSR) & 0x1F, 0x12)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_SPSR), 0x13)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_LR), 0x6012345A)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_PC), 0xFFFF0018)
        self.assertEqual(machine._timer_irq_pending, 0)

    def test_irq_async_abort_mask_preserves_banked_spsr(self):
        from unicorn.arm_const import (UC_ARM_REG_CPSR, UC_ARM_REG_PC,
                                       UC_ARM_REG_SPSR)

        for interrupted_cpsr, mask_on_irq, expected_irq_cpsr in (
            (0x80000153, False, 0xD2),
            (0x80000153, True, 0x1D2),
            (0x80000053, True, 0x1D2),
        ):
            with self.subTest(interrupted_cpsr=interrupted_cpsr, mask_on_irq=mask_on_irq):
                machine = self.make_machine(
                    vectors_base=STORAGE, mask_async_abort_on_irq=mask_on_irq)
                vector = STORAGE + 0x18
                machine.uc.mem_write(vector, struct.pack('<I', 0xE25EF004))  # subs pc, lr, #4
                machine.uc.reg_write(UC_ARM_REG_CPSR, interrupted_cpsr)
                machine.uc.reg_write(UC_ARM_REG_PC, machine.kernel_load)
                machine._gic_regs[('cpu', GIC_ICCICR)] = 1
                machine._gic_regs[('dist', 0x000)] = 1
                machine._timer_irq_pending = 1

                self.assertTrue(machine._try_inject_irq())
                self.assertEqual(machine.uc.reg_read(UC_ARM_REG_PC), vector)
                self.assertEqual(machine.uc.reg_read(UC_ARM_REG_CPSR), expected_irq_cpsr)
                self.assertEqual(machine.uc.reg_read(UC_ARM_REG_SPSR), interrupted_cpsr)
                machine.uc.emu_start(vector, machine.kernel_load, count=1)
                self.assertEqual(machine.uc.reg_read(UC_ARM_REG_PC), machine.kernel_load)
                # Unicorn 2.1.4 retains the live A bit on this exception
                # return. Verify the remaining bits independently of that.
                self.assertEqual(machine.uc.reg_read(UC_ARM_REG_CPSR) & ~0x100,
                                 interrupted_cpsr & ~0x100)

    def test_irq_is_not_injected_when_masked_or_gic_disabled(self):
        from unicorn.arm_const import UC_ARM_REG_CPSR
        machine = self.make_machine()
        machine._gic_regs[('cpu', GIC_ICCICR)] = 1
        machine._gic_regs[('dist', 0x000)] = 1
        machine._timer_irq_pending = 1
        machine.uc.reg_write(UC_ARM_REG_CPSR, 0x93)  # IRQ masked
        self.assertFalse(machine._try_inject_irq())
        machine.uc.reg_write(UC_ARM_REG_CPSR, 0x13)
        machine._gic_regs[('cpu', GIC_ICCICR)] = 0
        self.assertFalse(machine._try_inject_irq())

    def test_nonsecure_af_write_policy_restores_saved_masks_on_irq_return(self):
        from unicorn.arm_const import (UC_ARM_REG_CPSR, UC_ARM_REG_PC,
                                       UC_ARM_REG_SPSR)

        for enabled, expected_scr, expected_after_return in (
            (False, 0x01, 0x80000153),
            (True, 0x31, 0x80000013),
        ):
            with self.subTest(enabled=enabled):
                machine = self.make_machine(
                    vectors_base=STORAGE,
                    mask_async_abort_on_irq=True,
                    allow_nonsecure_af_writes=enabled)
                self.assertEqual(machine.uc.cpr_read(15, 0, 1, 1, 0, 3, False),
                                 expected_scr)
                vector = STORAGE + 0x18
                machine.uc.mem_write(vector, struct.pack('<I', 0xE25EF004))  # subs pc, lr, #4
                machine.uc.reg_write(UC_ARM_REG_CPSR, 0x80000013)
                machine.uc.reg_write(UC_ARM_REG_PC, machine.kernel_load)
                machine._gic_regs[('cpu', GIC_ICCICR)] = 1
                machine._gic_regs[('dist', 0x000)] = 1
                machine._timer_irq_pending = 1
                self.assertTrue(machine._try_inject_irq())
                self.assertEqual(machine.uc.reg_read(UC_ARM_REG_SPSR), 0x80000013)
                machine.uc.emu_start(vector, machine.kernel_load, count=1)
                self.assertEqual(machine.uc.reg_read(UC_ARM_REG_PC), machine.kernel_load)
                self.assertEqual(machine.uc.reg_read(UC_ARM_REG_CPSR), expected_after_return)


if __name__ == '__main__':
    unittest.main()
