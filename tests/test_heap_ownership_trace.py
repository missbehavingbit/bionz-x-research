"""Constructed execution controls for passive allocation ownership diagnostics."""
import struct
import unittest
from types import SimpleNamespace

import unicorn
from unicorn import arm_const as arm

from tools.research.heap_ownership_trace import HeapOwnershipTrace


class HeapOwnershipTraceTests(unittest.TestCase):
    def setUp(self):
        self.mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_ARM)
        self.mu.mem_map(0x1000, 0x1000)
        self.mu.mem_map(0x10000, 0x10000)
        self.mu.mem_map(0x20000, 0x1000)
        self.mu.reg_write(arm.UC_ARM_REG_CPSR, 0x30)
        self.mu.reg_write(arm.UC_ARM_REG_SP, 0x1F000)
        self.probe = SimpleNamespace(
            mu=self.mu, instructions=0, report={'status': 'running'},
            threads=SimpleNamespace(current_tid=1000),
            mmap=SimpleNamespace(mappings=[]),
            filesystem=SimpleNamespace(cache={}),
            _arm_relocated_libc_repair=lambda event: None)
        self.trace = HeapOwnershipTrace(self.probe)
        self.mu.mem_write(0x11000, struct.pack('<II', 0, 0x51))
        self.pointer = 0x11008
        self.mu.mem_write(0x1080, bytes.fromhex('00bffee7'))

    def tearDown(self):
        self.trace = None
        self.probe = None
        self.mu = None

    def _function(self, address, name, code):
        self.mu.mem_write(address, bytes.fromhex(code))
        self.mu.hook_add(unicorn.UC_HOOK_CODE, self.trace._entry_hook(name),
                         begin=address, end=address)

    def _run(self, address, *arguments):
        self.mu.reg_write(arm.UC_ARM_REG_LR, 0x1081)
        for index, value in enumerate(arguments):
            self.mu.reg_write(getattr(arm, 'UC_ARM_REG_R' + str(index)), value)
        self.mu.emu_start(address | 1, 0x1082, count=20)

    def test_tail_called_malloc_completes_both_frames_before_free(self):
        # realloc(NULL, size) tail-calls malloc, which returns a constructed chunk.
        self._function(0x1000, 'realloc', '004607e0')
        self._function(0x1014, 'malloc', '10467047')
        self._function(0x1040, 'free', '7047')
        self._run(0x1000, 0, 0x4C, self.pointer)
        self.assertEqual(self.trace.report['status'], 'observing')
        self.assertEqual(self.trace.pending[1000], [])
        self.assertEqual(self.trace.live[self.pointer]['requested'], 0x4C)
        self._run(0x1040, self.pointer)
        self.assertEqual(self.trace.report['status'], 'observing')
        self.assertNotIn(self.pointer, self.trace.live)
        self.assertIn(self.pointer, self.trace.retired)

    def test_reuse_releases_retired_ownership_and_duplicate_live_return_stops(self):
        self._function(0x1000, 'malloc', '08467047')
        self._function(0x1040, 'free', '7047')
        self._run(0x1000, 0x4C, self.pointer)
        self._run(0x1040, self.pointer)
        self._run(0x1000, 0x4C, self.pointer)
        self.assertNotIn(self.pointer, self.trace.retired)
        self._run(0x1000, 0x4C, self.pointer)
        self.assertEqual(self.probe.report['boundary']['reason'],
                         'overlapping_live_allocations')

    def test_first_write_to_freed_payload_stops_but_borrowed_prev_size_does_not(self):
        self._function(0x1000, 'malloc', '08467047')
        self._function(0x1040, 'free', '7047')
        self._run(0x1000, 0x4C, self.pointer)
        self._run(0x1040, self.pointer)
        # str r0,[r1]; bx lr.
        self.mu.mem_write(0x1060, bytes.fromhex('08607047'))
        self._run(0x1060, 1, self.pointer - 8)
        self.assertEqual(self.trace.report['status'], 'observing')
        self._run(0x1060, 1, self.pointer)
        self.assertEqual(self.probe.report['boundary']['reason'],
                         'write_after_observed_free')
        self.assertEqual(self.probe.report['boundary']['write']['address'], self.pointer)

    def test_internal_return_still_in_free_bin_stops_before_caller(self):
        arena = 0x20000
        sentinel = arena + 0x30
        self.mu.mem_write(arena + 0x38, struct.pack('<II', self.pointer - 8, self.pointer - 8))
        self.mu.mem_write(self.pointer, struct.pack('<II', sentinel, sentinel))
        self._function(0x1000, '_int_malloc', '10467047')
        self._run(0x1000, arena, 0x4C, self.pointer)
        self.assertEqual(self.probe.report['boundary']['reason'],
                         'allocation_returned_while_still_free')
        self.assertEqual(self.probe.report['boundary']['allocation']['free_membership']['index'], 1)

    def test_return_is_thread_stack_and_mode_specific(self):
        self._function(0x1000, 'malloc', '08467047')
        self.mu.reg_write(arm.UC_ARM_REG_R0, 0x4C)
        self.mu.reg_write(arm.UC_ARM_REG_LR, 0x1081)
        self.trace.enter('malloc')
        self.probe.threads.current_tid = 1001
        self.trace._return_hook(self.mu, 0x1080, 2, None)
        self.assertEqual(len(self.trace.pending[1000]), 1)
        self.assertEqual(self.trace.live, {})
        self.probe.threads.current_tid = 1000
        self.mu.reg_write(arm.UC_ARM_REG_CPSR, 0x10)
        self.trace._return_hook(self.mu, 0x1080, 4, None)
        self.assertEqual(self.probe.report['boundary']['reason'],
                         'allocator_return_mode_mismatch')

    def test_event_limit_and_source_mismatch_are_explicit_boundaries(self):
        self.trace.limit = 1
        self.trace._record('first')
        self.trace._record('second')
        self.assertEqual(self.probe.report['boundary']['reason'], 'ownership_event_limit')
        self.probe.report = {'status': 'running'}
        self.probe.mmap.mappings = [
            {'path': '/lib/libc-2.11.2.so', 'address': 0x10000000,
             'offset': 0, 'length': 4096, 'prot': 5}]
        trace = HeapOwnershipTrace(self.probe).install()
        self.assertEqual(trace.report['first_divergence']['reason'],
                         'allocator_source_identity_mismatch')


if __name__ == '__main__':
    unittest.main()
