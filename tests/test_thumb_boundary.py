"""Regression checks for the offline differential trace report."""
import unittest

from tools.research.thumb_boundary import (CHECKPOINT_PC, GDB_CAPTURE,
                                           GDB_USER_RETURN, KERNEL_ENTRY_PC,
                                           checkpoint_usable, compare,
                                           cpsr_bit_names, observe_without_continue,
                                           parse_stop, parse_xp_bytes, parse_xp_word,
                                           physical_address, read_user_sample, stage_for_stop,
                                           walk_short_descriptor)


def receipt(rows, kernel='kernel-a'):
    return dict(identity=dict(target='ilce_6400', boot_set='nflasha1',
                              kernel_sha256=kernel, initrd_sha256='initrd'),
                windows={'rows': {'loader': rows}}, stop={'reason': 'test'})


def sample(offset=0, data='00bf00bf', cpsr=0x30):
    return dict(offset=offset, bytes=data, state={'cpsr': cpsr})


class ThumbBoundaryTest(unittest.TestCase):
    def test_user_loader_preserves_cpu_difference_and_summarizes_bytes(self):
        def loader_report(pc, sp, stack):
            rows = [
                dict(sample(), pc=pc, stack_bytes=stack,
                     state={'cpsr': 0x30, 'sp': sp, 'r1': sp + 0xc8}),
                dict(sample(offset=2), pc=pc + 2),
            ]
            report = receipt([])
            report['windows']['rows']['user-loader'] = rows
            return report

        result = compare(loader_report(0x4000, 0x8000, '010002'),
                         loader_report(0x5000, 0x9000, '010003'), 'user-loader')
        self.assertEqual(result['category'], 'cpu_state')
        self.assertEqual(result['loader']['matching_instruction_samples'], 2)
        self.assertEqual(result['loader']['compared_stack_bytes'], 3)
        self.assertEqual(result['loader']['first_stack_difference'], 2)
        self.assertEqual(result['loader']['entries']['qemu']['r1_from_sp'], 0xc8)

    def test_user_sample_translates_each_page_and_rejects_non_ram(self):
        mapping = {0x1ffe: 0x4ffe, 0x2000: 0x8000}
        reads = {(0x4ffe, 2): b'ab', (0x8000, 2): b'cd'}
        read = lambda physical, length: reads[physical, length]
        self.assertEqual(read_user_sample(0x1ffe, 4, mapping.get, read), b'abcd')
        mapping[0x2000] = 0xf0000000
        with self.assertRaisesRegex(ValueError, 'no first-bank RAM translation'):
            read_user_sample(0x1ffe, 4, mapping.get, read)

    def test_incompatible_kernel_precedes_state_comparison(self):
        result = compare(receipt([sample()]), receipt([sample()], 'kernel-b'), 'loader')
        self.assertEqual(result['field'], 'kernel_sha256')

    def test_entry_bytes_must_match(self):
        result = compare(receipt([sample()]), receipt([sample(data='abcdabcd')]), 'loader')
        self.assertEqual(result['status'], 'incomparable')

    def test_first_cpu_difference_precedes_later_path_difference(self):
        unicorn = receipt([sample(), sample(2, cpsr=0x10), sample(4)])
        qemu = receipt([sample(), sample(2), sample(6)])
        result = compare(unicorn, qemu, 'loader')
        self.assertEqual(result['index'], 1)
        self.assertEqual(result['registers'], {'cpsr': [0x10, 0x30]})

    def test_shared_prefix_reports_unpaired_row_and_stop(self):
        result = compare(receipt([sample()]), receipt([sample(), sample(2)]), 'loader')
        self.assertEqual(result['status'], 'matching_prefix')
        self.assertEqual(result['first_unpaired']['qemu'], [sample(2)])

    def test_absent_window_names_engine(self):
        result = compare(receipt([sample()]), receipt([]), 'loader')
        self.assertEqual(result['missing'], ['qemu'])

    def test_gdb_python_program_compiles(self):
        self.assertIn('Software breakpoints bring QEMU back', GDB_CAPTURE)
        self.assertIn('BOUNDARY_RESUME', GDB_CAPTURE)
        compile(GDB_CAPTURE, '<gdb-capture>', 'exec')

    def test_restored_stop_skips_the_boot_continue(self):
        self.assertTrue(observe_without_continue(CHECKPOINT_PC, CHECKPOINT_PC, 0x153))
        self.assertFalse(observe_without_continue(0x1000, CHECKPOINT_PC, 0x153))
        self.assertFalse(observe_without_continue(0x0400C790, 0x0400C790, 0x153))

    def test_checkpoint_requires_the_same_binary_and_cmdline(self):
        identity = dict(target='ilce_6400', boot_set='nflasha1',
                        kernel_sha256='k', initrd_sha256='i')
        record = dict(identity=identity, pc=CHECKPOINT_PC, qemu_sha256='q',
                      cmdline='kemco', snapshot='vmstate.bin')
        self.assertTrue(checkpoint_usable(record, identity, 'q', 'kemco'))
        self.assertFalse(checkpoint_usable(record, identity, 'other', 'kemco'))
        self.assertTrue(checkpoint_usable(dict(record, pc=KERNEL_ENTRY_PC), identity, 'q', 'kemco',
                                          KERNEL_ENTRY_PC))
        self.assertFalse(checkpoint_usable(record, identity, 'q', 'kemco', KERNEL_ENTRY_PC))

    def test_stop_selects_the_latest_stage_that_can_reach_it(self):
        self.assertEqual(stage_for_stop(parse_stop('user-return')), 'return')
        self.assertEqual(stage_for_stop(parse_stop(f'pc:{CHECKPOINT_PC:#x}')), 'return')
        self.assertEqual(stage_for_stop(parse_stop('kernel-entry')), 'kernel-entry')
        self.assertEqual(parse_stop('kernel-entry')['pc'], KERNEL_ENTRY_PC)
        self.assertEqual(parse_stop('pc:0x6010DF34')['name'], 'return')

    def test_physical_kernel_stop_stays_in_svc(self):
        head = parse_stop('pc:0x00108010')
        self.assertFalse(head['user_only'])
        self.assertTrue(head['mmu_off'])
        self.assertEqual(stage_for_stop(head), 'kernel-entry')
        user = parse_stop('pc:0x0400C790')
        self.assertTrue(user['user_only'])
        self.assertFalse(user['mmu_off'])

    def test_kernel_entry_stop_is_already_the_sample(self):
        self.assertTrue(observe_without_continue(KERNEL_ENTRY_PC, KERNEL_ENTRY_PC, 0x1D3,
                                                 user_only=False))

    def test_cpsr_difference_names_the_abort_mask(self):
        self.assertEqual(cpsr_bit_names(0x40000053, 0x40000153), ['A'])
        self.assertEqual(cpsr_bit_names(0x10, 0x30), ['T'])
        result = compare(receipt([sample(cpsr=0x40000053)]),
                         receipt([sample(cpsr=0x40000153)]), 'loader')
        self.assertEqual(result['cpsr_bits'], ['A'])

    def test_user_return_compares_bytes_when_the_address_slides(self):
        unicorn = dict(identity=dict(target='ilce_6400', boot_set='nflasha1',
                                     kernel_sha256='kernel-a', initrd_sha256='initrd'),
                       windows={'bounds': {'user-return': ['user-return']},
                                'rows': {'user-return': [dict(sample(), pc=0x0400C790)]}},
                       stop={'reason': 'test'})
        qemu = dict(identity=unicorn['identity'],
                    windows={'bounds': {'user-return': ['other']},
                             'rows': {'user-return': [dict(sample(data='aabbccdd'), pc=0x0404A790)]}},
                    stop={'reason': 'test'})
        result = compare(unicorn, qemu, 'user-return')
        self.assertEqual(result['status'], 'difference')
        self.assertEqual(result['category'], 'instruction_bytes')
        self.assertEqual(result['addresses']['qemu'], 0x0404A790)

    def test_short_descriptor_walk_matches_the_unicorn_rules(self):
        words = {0x4100: 0x00008001, 0x8030: 0x12345002}

        def read_word(address):
            return words.get(address)

        self.assertEqual(walk_short_descriptor(0x0400C790, 0x4000, read_word), 0x12345790)
        self.assertEqual(walk_short_descriptor(0x0400C790, 0x4000, lambda address: 0x11100002),
                         0x1110C790)
        self.assertIsNone(walk_short_descriptor(0x0400C790, 0x4000, lambda address: 0))
        self.assertEqual(physical_address(KERNEL_ENTRY_PC, None, read_word, mmu_off=True),
                         KERNEL_ENTRY_PC)
        self.assertEqual(physical_address(0x6010DF34, None, read_word), 0x0010DF34)
        self.assertEqual(parse_xp_word('0000000000004100: 0x00008001\n'), 0x8001)
        self.assertEqual(parse_xp_bytes('0000000012345790: 0xdf 0xf8 0x68 0xa0\n').hex(),
                         'dff868a0')

    def test_user_return_gdb_program_compiles(self):
        compile(GDB_USER_RETURN, '<gdb-user-return>', 'exec')
        self.assertIn('msr_pc', GDB_USER_RETURN)
