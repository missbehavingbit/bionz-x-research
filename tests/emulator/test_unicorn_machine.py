"""Minimal Unicorn machine runner checks (E0416)."""
import struct
import unittest

from unicorn.arm_const import UC_ARM_REG_CPSR, UC_ARM_REG_FPEXC, UC_ARM_REG_R1

from src.a6400.execution.arm_atags import build_atags
from src.a6400.execution.unicorn_machine import MachineError, UnicornMachine

MACHINE_ID = 0x0DA9


def synthetic_kernel_program():
    """A tiny 'kernel': reads PL011 FR, writes 'AB' to DR, then faults on purpose."""
    image = bytearray(0x200)
    words = [
        0xE321F0D3,  # msr cpsr_c, #0xd3  (required ARM kernel head word)
        0xE59F0028,  # ldr r0, [pc, #0x28] -> 0x34 UART0
        0xE5904018,  # ldr r4, [r0, #0x18]  FR read (MMIO hook)
        0xE59F1024,  # ldr r1, [pc, #0x24] -> 0x38 'A'
        0xE5801000,  # str r1, [r0]
        0xE59F2020,  # ldr r2, [pc, #0x20] -> 0x3c 'B'
        0xE5802000,  # str r2, [r0]
        0xE59F301C,  # ldr r3, [pc, #0x1c] -> 0x40 unmapped address
        0xE5831000,  # str r1, [r3]        (faults -> run stops)
        0xEAFFFFFE,  # b .
    ]
    for index, word in enumerate(words):
        struct.pack_into('<I', image, index * 4, word)
    struct.pack_into('<4I', image, 0x34, 0xF2000000, 0x41, 0x42, 0xDEAD0000)
    return bytes(image)


class UnicornMachineTests(unittest.TestCase):
    def make_machine(self, **kwargs):
        return UnicornMachine(
            synthetic_kernel_program(), b'\x00' * 64,
            build_atags('console=ttyAM0', [(0, 0x40000000)]),
            machine_id=MACHINE_ID, instruction_limit=1000, data_abort_delivery=False, **kwargs)

    def test_uart_serial_capture_and_unmapped_stop(self):
        report = self.make_machine().run()
        self.assertEqual(report['serial_text'], 'AB')
        self.assertEqual(report['stop_reason'], 'unmapped_access')
        self.assertEqual(len(report['unmapped']), 1)
        self.assertEqual(report['unmapped'][0]['address'], '0xdead0000')
        self.assertEqual(report['machine_id'], MACHINE_ID)
        # The PL011 FR register is pre-seeded and read once by the program.
        self.assertEqual(report['stub_hits'], {})

    def test_labelled_mmio_read_stub_is_reported(self):
        machine = self.make_machine(mmio_read_stubs={0xF2000018: 0x90})
        report = machine.run()
        self.assertEqual(report['serial_text'], 'AB')
        self.assertIn('0xf2000018', report['stub_hits'])
        self.assertGreater(report['stub_hits']['0xf2000018'], 0)

    def test_kernel_head_word_is_required(self):
        with self.assertRaises(Exception):
            UnicornMachine(b'\x00' * 0x100, b'\x00', b'', machine_id=MACHINE_ID)

    def test_async_abort_mask_is_opt_in_and_survives_kernel_head(self):
        for enabled, expected in ((False, 0xD3), (True, 0x1D3)):
            with self.subTest(enabled=enabled):
                machine = self.make_machine(mask_async_abort_at_entry=enabled)
                self.assertEqual(machine.uc.reg_read(UC_ARM_REG_CPSR), expected)
                machine.uc.emu_start(machine.kernel_load, machine.kernel_load + 4, count=1)
                self.assertEqual(machine.uc.reg_read(UC_ARM_REG_CPSR), expected)

    def test_vfp_d16_mvfr0_emulation_is_opt_in(self):
        kernel = bytearray(0x254)
        struct.pack_into('<I', kernel, 0, 0xE321F0D3)
        probe, other = 0x100, 0x250
        for offset in (probe, other):
            struct.pack_into('<I', kernel, offset, 0xEEF71A10)  # vmrs r1, mvfr0
        base = 0x60108000
        native_mvfr0 = 0x11110222
        d16_mvfr0 = 0x10110221
        for flags, expected in (({}, native_mvfr0), ({'vfp_d16_mvfr0': True}, d16_mvfr0),
                                ({'qemu_cpu_parity': True}, d16_mvfr0)):
            with self.subTest(flags=flags):
                machine = UnicornMachine(
                    bytes(kernel), b'\x00', b'', machine_id=MACHINE_ID,
                    symbols={'__boottime_vfp_init': base + 0xE0},
                    ram_banks=((0, 0x800000),), **flags)
                self.assertEqual(machine.mask_async_abort_on_irq, 'qemu_cpu_parity' in flags)
                uc = machine.uc
                uc.mem_map(base, 0x1000)
                uc.mem_write(base, bytes(kernel))
                uc.reg_write(UC_ARM_REG_FPEXC, 1 << 30)
                for offset, value in ((probe, expected), (other, native_mvfr0)):
                    pc = base + offset
                    uc.emu_start(pc, pc + 4, count=1)
                    self.assertEqual(uc.reg_read(UC_ARM_REG_R1), value)
                self.assertEqual(machine.vfp_mvfr0_d16_hits, int(bool(flags)))

    def test_vfp_d16_override_requires_unambiguous_kernel_probe(self):
        head = struct.pack('<I', 0xE321F0D3)
        vmrs = struct.pack('<I', 0xEEF71A10)
        for body in (b'\0' * 8, vmrs + vmrs):
            with self.subTest(body=body):
                with self.assertRaisesRegex(MachineError, 'one supported VMRS MVFR0 site'):
                    UnicornMachine(
                        head + body, b'\0', b'', machine_id=MACHINE_ID,
                        symbols={'__boottime_vfp_init': 0x60108004},
                        ram_banks=((0, 0x800000),), vfp_d16_mvfr0=True)

    def test_oracle_mmio_trace_replays_sequence_and_holds_last(self):
        import struct
        from unicorn.arm_const import UC_ARM_REG_R0
        # ldr r0, [pc, #0x10] (addr 0xF2916000); ldr r1, [r0]; ldr r2, [r0]; ldr r3, [r0]
        kernel = bytearray(b'\xD3\xF0\x21\xE3' + b'\x00' * 0x100)
        struct.pack_into('<5I', kernel, 0x40,
                         0xE59F0008,  # ldr r0, [pc, #8] -> 0x00108050
                         0xE5901000,  # ldr r1, [r0]
                         0xE5902000,  # ldr r2, [r0]
                         0xE5903000,  # ldr r3, [r0]
                         0xF2916000)  # literal
        machine = UnicornMachine(
            bytes(kernel), b'\x00', b'', machine_id=MACHINE_ID,
            oracle_mmio_trace={0xF2916000: [0x11, 0x22]},
        )
        machine.uc.emu_start(0x00108040, 0x00108050, count=4)
        from unicorn.arm_const import UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_R1), 0x11)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_R2), 0x22)
        self.assertEqual(machine.uc.reg_read(UC_ARM_REG_R3), 0x22)
        self.assertEqual(machine.oracle_mmio_hits[0xF2916000], 3)


if __name__ == '__main__':
    unittest.main()
