"""Checked fixtures and optional hash-pinned original local USB route replays."""
import unittest

from tools.research.probe_usb_local_setconfig import (
    FixtureHeap, INPUTS, LocalSetconfigReplay, MESSAGE, RECEIVER, RouteBoundary,
    SETCONFIG_CATEGORY, SETCONFIG_MESSAGE_ID, SETCONFIG_PARAM_ID, SENDER, WRONG_LANES,
)


class FixtureMemory:
    def __init__(self):
        self.writes = []

    def mem_write(self, address, data):
        self.writes.append((address, bytes(data)))


class LocalUsbFixtureTests(unittest.TestCase):
    def test_free_rejects_foreign_interior_and_double_free(self):
        memory = FixtureMemory()
        heap = FixtureHeap(memory, base=0x1000, size=32)
        pointer = heap.allocate(16)
        for invalid in (pointer + 4, pointer + 16, 0x2000):
            with self.subTest(pointer=invalid):
                with self.assertRaisesRegex(RouteBoundary, 'foreign_or_double_free'):
                    heap.free(invalid)
                self.assertEqual(heap.live, {pointer: 16})
        heap.free(pointer)
        with self.assertRaisesRegex(RouteBoundary, 'foreign_or_double_free'):
            heap.free(pointer)
        self.assertEqual(heap.live, {})
        self.assertEqual([event['operation'] for event in heap.events], ['allocate', 'free'])

    def test_heap_exhaustion_does_not_write_or_discard_owned_storage(self):
        memory = FixtureMemory()
        heap = FixtureHeap(memory, base=0x1000, size=16)
        first = heap.allocate(1)
        second = heap.allocate(8)
        self.assertEqual((first, second), (0x1000, 0x1008))
        before = (dict(heap.live), list(memory.writes), heap.next)
        with self.assertRaisesRegex(RouteBoundary, 'fixture_heap_exhausted'):
            heap.allocate(1)
        self.assertEqual((heap.live, memory.writes, heap.next), before)

    def test_replay_rejects_invalid_budget_and_unknown_imports(self):
        for budget in (True, 0, -1, 1.5, None):
            with self.subTest(budget=budget):
                with self.assertRaises(ValueError):
                    LocalSetconfigReplay(instruction_budget=budget)
        replay = LocalSetconfigReplay()
        with self.assertRaises(RouteBoundary) as raised:
            replay._service('ioctl', [3, 0x80000005, 0x2000, 0])
        self.assertEqual(raised.exception.details['reason'], 'unsupported_import')
        self.assertEqual(raised.exception.details['symbol'], 'ioctl')


@unittest.skipUnless(all(path.is_file() for _, path, _, _ in INPUTS),
                     'preserved a6400 ELF inputs are not present')
class OriginalLocalUsbRouteTests(unittest.TestCase):
    def test_original_sync_post_wake_dispatch_and_teardown(self):
        report = LocalSetconfigReplay().execute()
        self.assertEqual(report['status'], 'original_local_setconfig_delivered', report)
        trace = report['trace']
        stages = [
            'event_listener', 'send_event', 'osal_snd_sync_msg',
            '__osal_snd_sync_msg', 'local_callback', 'preprocess', 'post',
            'osal_snd_msg', 'process', 'event_map', 'dispatch', 'fsm_execute_main',
            'get_int32', 'broadcast',
        ]
        position = 0
        for entry in trace:
            if position < len(stages) and entry['function'] == stages[position]:
                position += 1
        self.assertEqual(position, len(stages))
        self.assertTrue(all(entry.get('returned') == 0 for entry in trace
                            if entry['function'] in stages[:8]))
        for stage in stages[:8]:
            self.assertEqual(next(entry['tid'] for entry in trace
                                  if entry['function'] == stage), SENDER)
        process = next(entry for entry in trace if entry['function'] == 'process')
        self.assertEqual(process['tid'], RECEIVER)
        self.assertNotEqual(process['objmsg']['pointer'], MESSAGE)
        self.assertEqual((process['objmsg']['category'], process['objmsg']['message_id']),
                         (SETCONFIG_CATEGORY, SETCONFIG_MESSAGE_ID))
        dispatch = next(entry for entry in trace if entry['function'] == 'dispatch')
        self.assertEqual(dispatch['args'][1:3], [0x13, process['objmsg']['pointer']])
        self.assertEqual(report['fsm_handle_event_return'], 0xFFFFFFFF)
        self.assertFalse(report['fsm_event_handled'])
        self.assertEqual((report['fsm_state_before'], report['fsm_state_after']), (0, 0))
        param = next(entry for entry in trace if entry['function'] == 'get_int32')
        self.assertEqual((param['args'][1], param['returned']), (SETCONFIG_PARAM_ID, 0))
        futex = [entry for entry in report['services'] if entry['name'] == 'syscall']
        self.assertEqual([entry['cmd'] for entry in futex],
                         ['FUTEX_WAIT', 'FUTEX_WAKE', 'FUTEX_WAIT'])
        self.assertEqual(futex[1]['woken'], 1)
        self.assertEqual(futex[0]['uaddr'], futex[1]['uaddr'])
        frees = [entry for entry in trace if entry['function'] == 'osal_free_msg']
        self.assertEqual([entry['tid'] for entry in frees], [SENDER, RECEIVER])
        self.assertNotEqual(frees[0]['args'][1], frees[1]['args'][1])
        self.assertEqual(report['queue_allocation_count'], 0)
        self.assertEqual(report['fblock_remaining_before'], report['fblock_remaining_after'])
        self.assertEqual(report['heap_live'], {})
        self.assertEqual(report['mutexes_live'], {})
        self.assertFalse(report['camera_access'])

    def test_no_registration_is_an_original_error_without_dispatch(self):
        replay = LocalSetconfigReplay(register=False)
        report = replay.execute()
        self.assertEqual(report['status'], 'original_local_setconfig_no_registration', report)
        self.assertEqual(report['send_return'], 0xFFFFFC04)
        self.assertEqual(replay.threads.threads[RECEIVER].state, 'blocked_futex')
        functions = [entry['function'] for entry in report['trace']]
        self.assertIn('__osal_snd_sync_msg', functions)
        self.assertNotIn('local_callback', functions)
        self.assertNotIn('process', functions)
        self.assertEqual(report['heap_live'], {})
        self.assertEqual(report['queue_allocation_count'], 0)

    def test_instruction_exhaustion_is_never_delivery(self):
        report = LocalSetconfigReplay(instruction_budget=8).execute()
        self.assertEqual(report['status'], 'boundary', report)
        self.assertEqual(report['boundary']['reason'], 'execution_limit')
        self.assertLessEqual(report['instructions'], 8)

    def test_adjustment_and_input_callbacks_are_refused(self):
        replay = LocalSetconfigReplay()
        replay._load()
        for symbol in WRONG_LANES:
            with self.subTest(symbol=symbol):
                with self.assertRaises(RouteBoundary) as raised:
                    replay.call(symbol, MESSAGE, 0)
                self.assertEqual(raised.exception.details['reason'], 'wrong_usb_delivery_lane')
                self.assertEqual(raised.exception.details['symbol'], symbol)


if __name__ == '__main__':
    unittest.main()
