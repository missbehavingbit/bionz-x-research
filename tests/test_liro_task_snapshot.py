"""Constructed, read-only LIRO task/stack decoding and identity refusal checks."""
import struct
import unittest

from tools.research.liro_task_snapshot import (
    KERNEL_SHA256, USR_SHA256, decode_gic_trace, snapshot_interrupts, snapshot_tasks, task_table_address, unwind_frames,
)


class LiroTaskSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.table = 0x5F001780
        self.control = 0x68002000
        self.task = 0x68004000
        self.stack = 0x68006000
        self.memory = {
            self.table: bytearray(2040), self.control: bytearray(160),
            self.task: bytearray(12), self.stack: bytearray(8192),
        }
        struct.pack_into('<2I', self.memory[self.table], 64 * 8, 0, self.control)
        control = self.memory[self.control]
        for offset, value in ((8, 0x10), (0x14, self.task), (0x18, 65), (0x4C, 0x635C8001)):
            struct.pack_into('<I', control, offset, value)
        struct.pack_into('<3I', self.memory[self.task], 0, 2, 0, self.stack)
        stack = self.memory[self.stack]
        struct.pack_into('<3I', stack, 0x38, self.stack + 0x100, self.stack + 0xE0, 0x6011A33C)
        struct.pack_into('<3I', stack, 0x100 - 12, self.stack + 0x130, self.stack + 0x104, 0x635C9001)
        struct.pack_into('<3I', stack, 0x130 - 12, 0, self.stack + 0x134, 0x6010EC28)

    def read(self, address, size):
        for base, data in self.memory.items():
            if base <= address and address + size <= base + len(data):
                return bytes(data[address - base:address - base + size])
        raise AssertionError(f'Unexpected memory read: {address:#x}+{size:#x}')

    def test_dormant_snapshot_keeps_original_entry_and_bounded_caller_frames(self):
        result = snapshot_tasks(self.read, self.table, (65,),
                                kernel_sha256=KERNEL_SHA256, usr_sha256=USR_SHA256)[0]
        self.assertTrue(result['dormant'])
        self.assertIn('does not distinguish', result['dormant_limit'])
        self.assertEqual(result['entry'], 0x635C8001)
        self.assertEqual(len(result['control_block_words']), 40)
        self.assertEqual(result['control_block_words'][0x18 // 4], 65)
        self.assertEqual([frame['pc'] for frame in result['stack_observation']['frames']],
                         [0x6011A33C, 0x635C9001, 0x6010EC28])
        self.assertEqual(result['stack_observation']['stop'], 'frame_pointer_outside_confirmed_stack')
        struct.pack_into('<I', self.memory[self.task], 0, 0)
        running = snapshot_tasks(self.read, self.table, (65,),
                                 kernel_sha256=KERNEL_SHA256, usr_sha256=USR_SHA256)[0]
        self.assertEqual(running['stack_observation'], 'not_unwound_runnable_or_running_task')

    def test_foreign_profile_task_or_ambiguous_runtime_symbol_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported kernel/usr'):
            snapshot_tasks(self.read, self.table, (65,), kernel_sha256='0' * 64, usr_sha256=USR_SHA256)
        struct.pack_into('<I', self.memory[self.control], 0x18, 64)
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            snapshot_tasks(self.read, self.table, (65,), kernel_sha256=KERNEL_SHA256, usr_sha256=USR_SHA256)
        line = f'__LIRO_BSS__:0x{self.table - 0x780:08x}\n'
        self.assertEqual(task_table_address(line), self.table)
        with self.assertRaisesRegex(ValueError, 'one current-run'):
            task_table_address(line + line)

    def test_invalid_frame_pointer_never_follows_unobserved_memory(self):
        data = bytes(self.memory[self.stack])
        result = unwind_frames(data, self.stack, 0xF2403000, self.stack + 0xE0, 0x6011A33C)
        self.assertEqual(len(result['frames']), 1)
        self.assertEqual(result['stop'], 'frame_pointer_outside_confirmed_stack')

    def test_irq_records_preserve_handler_identity_without_inventing_completion(self):
        inhs = self.table - 0x780 + 0x140
        self.memory[inhs] = bytearray(1024)
        record = 0x6800A000
        self.memory[record] = bytearray(24)
        struct.pack_into('<I', self.memory[inhs], 101 * 4, record)
        struct.pack_into('<3I', self.memory[record], 0, 101, 0x5F001000, 0x63D00001)
        self.memory[record][12] = 1
        result = snapshot_interrupts(self.read, self.table, (101,),
                                     kernel_sha256=KERNEL_SHA256, usr_sha256=USR_SHA256)[0]
        self.assertEqual((result['handler'], result['requested_cpu'], result['thumb']), (0x63D00001, 1, True))
        self.assertTrue(result['handler_record'].startswith('65000000'))
        trace = ('gic_enable_irq irq 101 enabled\n'
                 'gic_disable_irq irq 101 disabled\n'
                 'gic_enable_irq irq 101 enabled\n'
                 'gic_set_irq irq 101 level 1 cpumask 0xf target 0x2\n')
        self.assertEqual(decode_gic_trace(trace, (101,))[101],
                         {'enabled': True, 'enable_transitions': 3, 'input_assertions': 1})
        with self.assertRaisesRegex(ValueError, 'not observed'):
            decode_gic_trace(trace, (102,))
        struct.pack_into('<I', self.memory[record], 0, 102)
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            snapshot_interrupts(self.read, self.table, (101,),
                                kernel_sha256=KERNEL_SHA256, usr_sha256=USR_SHA256)


if __name__ == '__main__':
    unittest.main()
