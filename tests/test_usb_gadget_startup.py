"""Original-process USB startup guards; no camera or physical-media access."""
import hashlib
import json
import os
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest import mock

import unicorn
from unicorn import arm_const as arm

from project_paths import PROJECT_ROOT
from src.a6400.execution.guest_filesystem import UnsupportedFilesystem
from src.a6400.execution.guest_fusb_startup import CapturedFusbStartup, KERNEL_SHA256, check_dwc3_context
from src.a6400.execution.guest_threads import GuestThreads
from tests.test_interpreter_probe import make_elf
from tools.research.emulator_session import build_boot_session
from tools.research.experiment_ui_boot import build_parser
from tools.research.probe_usb_gadget_startup import (
    BOOT_OPTIONS, ProcessUsbStartup, RETURN, RouteBoundary, checked_checkpoint,
    loaded_module, registry_nodes,
)


def observer():
    replay = ProcessUsbStartup.__new__(ProcessUsbStartup)
    replay.mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_ARM)
    replay.mu.mem_map(0x10000, 4096)
    replay.mu.mem_map(0x20000000, 0x10000)
    replay.mu.mem_map(RETURN, 4096)
    replay.mu.reg_write(arm.UC_ARM_REG_CPSR, 0x30)
    replay.mu.reg_write(arm.UC_ARM_REG_SP, 0x20008000)
    replay.probe = SimpleNamespace(
        mu=replay.mu, threads=GuestThreads(), instructions=0, report={})
    replay.budget, replay.instructions = 100, 0
    replay.control, replay.stop_on_receive = None, False
    replay.phase = 'test'
    replay.pending, replay.live, replay.released, replay.functions = {}, {}, set(), {}
    replay.symbols = {}
    replay.report = {'trace': [], 'checks': [], 'heap_events': [],
                     'port_file_transactions': []}
    return replay


class ProcessUsbStartupGuardTests(unittest.TestCase):
    def test_captured_otg_kernel_requires_exact_identity(self):
        with self.assertRaisesRegex(ValueError, 'Captured FUSB kernel identity mismatch'):
            CapturedFusbStartup(bytes(4_085_888))

    def test_native_reset_context_refuses_other_role_or_mmio_alias(self):
        context = dict(kernel_sha256=KERNEL_SHA256, kernel_bias=0x60000000,
                       fusb=0x689E9600, fusb_state=2, cid=1, vbus=1,
                       binding=0x604E4E30, callback=0x602B1B9C, dwc3=0x689E5810,
                       registers=0xB0880100, outer_sync=0x6011A5D0, l2x0_registers=0xF8002000,
                       gctl=0)
        check_dwc3_context(context)
        for field, value in (('cid', 0), ('gctl', 0x800), ('registers', 0xA0010020),
                             ('l2x0_registers', 0xF8004000), ('dwc3', 0xF020C100)):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'bound reset profile'):
                check_dwc3_context({**context, field: value})

    def test_checkpoint_identity_is_pinned_before_restore(self):
        root = PROJECT_ROOT / 'artifacts' / 'tests' / 'usb-gadget-startup'
        root.mkdir(parents=True, exist_ok=True)
        path = root / 'identity.ckpt'
        path.write_bytes(b'constructed checkpoint identity fixture')
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(checked_checkpoint(path, expected), path.resolve())
        path.write_bytes(b'changed checkpoint')
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            checked_checkpoint(path, expected)
        with self.assertRaisesRegex(ValueError, 'locally generated artifact'):
            checked_checkpoint(PROJECT_ROOT / 'README.md', expected)

    def test_loaded_module_must_be_unambiguous_and_hash_matched(self):
        probe = SimpleNamespace(mmap=SimpleNamespace(mappings=[]))
        with self.assertRaisesRegex(RouteBoundary, 'mapping_ambiguous'):
            loaded_module(probe, 'libUsbControl.so', '0' * 64)
        probe.mmap.mappings = [{
            'path': '/usr/lib/libUsbControl.so', 'address': 0x10000,
            'offset': 0, 'length': 4096,
        }]
        probe.filesystem = SimpleNamespace(cache={
            '/usr/lib/libUsbControl.so': b'changed ELF bytes'})
        with self.assertRaisesRegex(RouteBoundary, 'identity_mismatch'):
            loaded_module(probe, 'libUsbControl.so', '0' * 64)
        root = PROJECT_ROOT / 'artifacts' / 'tests' / 'usb-gadget-startup'
        root.mkdir(parents=True, exist_ok=True)
        path = root / 'libUsbControl.so'
        image = make_elf(path, bytes.fromhex('7047'))
        probe.filesystem.cache['/usr/lib/libUsbControl.so'] = image['data']
        probe.mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_ARM)
        probe.mu.mem_map(0x10000, 0x2000)
        probe.mu.mem_write(0x10000, image['data'])
        probe.mu.mem_write(0x10100, b'\0\0')
        with self.assertRaisesRegex(RouteBoundary, 'executable_bytes_changed'):
            loaded_module(probe, 'libUsbControl.so', image['sha256'])

    def test_registry_traversal_retains_native_created_state_and_refuses_cycles(self):
        nodes = {}
        for at, following, created in ((0x1000, 0x2000, 0), (0x2000, 0, 1)):
            raw = bytearray(0x48)
            struct.pack_into('<I', raw, 4, at + 0x80)
            raw[8] = created
            struct.pack_into('<I', raw, 0x34, 16)
            struct.pack_into('<I', raw, 0x44, following)
            nodes[at] = raw
        read = lambda address, size: nodes[address][:size]
        result = registry_nodes(read, 0x1000)
        self.assertEqual([entry['object'] for entry in result], [0x1080, 0x2080])
        self.assertEqual([entry['created'] for entry in result], [0, 1])
        struct.pack_into('<I', nodes[0x2000], 0x44, 0x1000)
        with self.assertRaisesRegex(RouteBoundary, 'registry_cycle'):
            registry_nodes(read, 0x1000)
        with self.assertRaisesRegex(RouteBoundary, 'registry_short_read'):
            registry_nodes(lambda address, size: b'\0' * 8, 0x1000)

    def test_returning_abi_call_preserves_existing_cpu_stack_and_tls(self):
        replay = observer()
        replay.mu.mem_write(0x10000, bytes.fromhex('01307047'))
        replay.symbols['increment'] = 0x10001
        replay.mu.reg_write(arm.UC_ARM_REG_R0, 0x1234)
        replay.mu.reg_write(arm.UC_ARM_REG_C13_C0_3, 0x20000100)
        replay.mu.hook_add(unicorn.UC_HOOK_CODE, replay._code)
        self.assertEqual(replay.call('increment', 7), 8)
        self.assertEqual(replay.mu.reg_read(arm.UC_ARM_REG_R0), 0x1234)
        self.assertEqual(replay.mu.reg_read(arm.UC_ARM_REG_SP), 0x20008000)
        self.assertEqual(replay.mu.reg_read(arm.UC_ARM_REG_C13_C0_3), 0x20000100)
        self.assertEqual(replay.probe.threads.current_tid, 1000)
        self.assertEqual(replay.mu.reg_read(arm.UC_ARM_REG_CPSR) & 0x20, 0x20)

    def test_free_is_not_reported_complete_until_original_return(self):
        replay = observer()
        replay.functions[0x10000] = 'free'
        replay.live[0x20000100] = 32
        replay.mu.reg_write(arm.UC_ARM_REG_R0, 0x20000100)
        replay.mu.reg_write(arm.UC_ARM_REG_LR, 0x10081)
        replay._code(replay.mu, 0x10000, 2, None)
        self.assertEqual(replay.live, {0x20000100: 32})
        self.assertEqual(replay.report['heap_events'], [])
        replay._code(replay.mu, 0x10080, 2, None)
        self.assertEqual(replay.live, {})
        self.assertEqual(replay.report['heap_events'][0]['operation'], 'free')
        with self.assertRaisesRegex(RouteBoundary, 'double_free'):
            replay._code(replay.mu, 0x10000, 2, None)

    def test_zero_remaining_budget_never_starts_unbounded_execution(self):
        replay = observer()
        replay.instructions = replay.budget
        with mock.patch.object(replay.mu, 'emu_start') as start:
            with self.assertRaisesRegex(RouteBoundary, 'instruction_limit'):
                replay._run(0x10001)
            start.assert_not_called()
        for invalid in (True, 0, -1, 0.5):
            with self.subTest(budget=invalid):
                with self.assertRaises(ValueError):
                    ProcessUsbStartup(None, instruction_budget=invalid)

    def test_selected_port_fixture_is_one_scoped_read_only_file(self):
        replay = observer()
        fallback = mock.Mock(return_value={'returned': -2})
        filesystem = SimpleNamespace(
            request=fallback, fds={}, _path=lambda pointer: '/var/usbport',
            after_write=mock.Mock())
        replay.probe.filesystem = filesystem
        replay.state = lambda: {'selected_port': 0}
        replay.install_selected_port_file()
        with self.assertRaisesRegex(UnsupportedFilesystem, 'contract_mismatch'):
            filesystem.request(5, [0x10000, 2, 0])
        opened = filesystem.request(5, [0x10000, 0x1000, 0])
        fd = opened['returned']
        with self.assertRaisesRegex(UnsupportedFilesystem, 'operation_unsupported'):
            filesystem.request(3, [fd, 0x20000000, 2])
        read = filesystem.request(3, [fd, 0x20000000, 1])
        self.assertEqual(read['returned'], 1)
        self.assertEqual(bytes(replay.mu.mem_read(0x20000000, 1)), b'0')
        self.assertEqual(filesystem.request(6, [fd, 0, 0])['returned'], 0)
        self.assertEqual(filesystem.fds, {})
        self.assertEqual(filesystem.request(3, [123, 0, 0]), {'returned': -2})
        fallback.assert_called_once()


CHECKPOINT = os.environ.get('A6400_USB_PROCESS_CHECKPOINT')
CHECKPOINT_SHA256 = os.environ.get('A6400_USB_PROCESS_CHECKPOINT_SHA256')
CAPTURED_KERNEL = os.environ.get('A6400_USB_CAPTURED_KERNEL')
QEMU_CONTEXT = os.environ.get('A6400_USB_QEMU_CONTEXT')
QEMU_CONTEXT_SHA256 = os.environ.get('A6400_USB_QEMU_CONTEXT_SHA256')


@unittest.skipUnless(CHECKPOINT and CHECKPOINT_SHA256,
                     'explicit trusted original-process checkpoint and SHA-256 are not supplied')
class OriginalProcessUsbStartupTests(unittest.TestCase):
    def replay(self, *, function=4, budget=500000, captured_fusb=None):
        path = checked_checkpoint(Path(CHECKPOINT), CHECKPOINT_SHA256)
        args = build_parser().parse_args([*BOOT_OPTIONS, '--load-checkpoint', str(path)])
        probe = build_boot_session(args).probe
        if captured_fusb is not None:
            probe.usb_otgcore.captured_fusb_startup = captured_fusb
        replay = ProcessUsbStartup(probe, instruction_budget=budget)
        return replay.execute(exercise_startup=True, function=function)

    def test_original_heap_fsm_and_gadget_registration_reach_guarded_mode_ioctl(self):
        report = self.replay()
        self.assertEqual(report['status'], 'boundary', report.get('boundary'))
        boundary = report['boundary']['original_boundary']
        self.assertEqual(boundary['reason'], 'usb_otgcore_set_mode_reply_not_derivable')
        self.assertIn('configure_message_heap_balanced', report['checks'])
        self.assertEqual([entry['fsm'] for entry in report['states']], [1, 5])
        state = report['final_state']
        self.assertEqual((state['fsm'], state['configured'], state['started']), (7, 4, 4))
        self.assertEqual((state['service_state'], state['device_id']), (6, 55))
        self.assertEqual(len(state['gadgets']), 1)
        self.assertEqual(state['gadgets'][0]['function_id'], 16)
        self.assertEqual(state['gadgets'][0]['created'], 0)
        self.assertIn(state['service'], state['enabled_contexts'])
        native_start = next(entry for entry in report['trace']
                            if entry['function'] == 'start_gadget')
        self.assertNotIn('returned', native_start)
        self.assertNotIn('setconfig_action', [entry['function'] for entry in report['trace']])

    def test_mass_storage_missing_user_initialization_is_not_success(self):
        report = self.replay(function=1)
        self.assertEqual(report['status'], 'boundary', report.get('boundary'))
        self.assertEqual(report['boundary']['check'], 'original_gadget_start_succeeded')
        native_start = next(entry for entry in report['trace']
                            if entry['function'] == 'start_gadget')
        self.assertEqual(native_start['returned'], 0xFFFFFFFB)
        self.assertEqual(report['final_state']['gadgets'], [])

    @unittest.skipUnless(CAPTURED_KERNEL, 'captured FUSB kernel not supplied')
    def test_original_fusb_set_mode_uses_the_right_selector(self):
        fusb = CapturedFusbStartup(Path(CAPTURED_KERNEL).read_bytes())
        self.assertEqual(fusb.set_mode(1)['speed_selector'], 1)
        self.assertEqual(fusb.set_mode(2)['speed_selector'], 0)

    @unittest.skipUnless(CAPTURED_KERNEL, 'captured FUSB kernel not supplied')
    def test_connected_guard_reaches_bound_dwc3_without_acknowledging_start(self):
        report = self.replay(captured_fusb=CapturedFusbStartup(Path(CAPTURED_KERNEL).read_bytes()))
        self.assertEqual(report['status'], 'boundary')
        boundary = report['boundary']['original_boundary']
        self.assertEqual(boundary['reason'], 'usb_otgcore_dwc3_gadget_probe_unmodelled')
        self.assertEqual((boundary['kernel_pc'], boundary['callback_pc']), (0x602ACB04, 0x602B1B9C))
        self.assertNotIn('returned', boundary)
        self.assertEqual(report['final_state']['gadgets'][0]['created'], 0)
        self.assertFalse(report['setconfig_delivered'])

    @unittest.skipUnless(CAPTURED_KERNEL, 'captured FUSB kernel not supplied')
    def test_gate_clear_returns_native_ebusy_without_reaching_dwc3(self):
        fusb = CapturedFusbStartup(Path(CAPTURED_KERNEL).read_bytes(), gate_clear_control=True)
        fusb.set_mode(2)
        observation = fusb.start_guard()
        self.assertEqual(observation['returned'], -16)
        self.assertNotIn('callback_pc', observation)

    @unittest.skipUnless(CAPTURED_KERNEL and QEMU_CONTEXT and QEMU_CONTEXT_SHA256,
                         'explicit native QEMU DWC3 context and digest are not supplied')
    def test_original_dwc3_probe_reaches_cache_sync_but_does_not_acknowledge_e305(self):
        path = checked_checkpoint(Path(QEMU_CONTEXT), QEMU_CONTEXT_SHA256)
        context = json.loads(path.read_bytes())['native_dwc3_context']
        fusb = CapturedFusbStartup(Path(CAPTURED_KERNEL).read_bytes(), qemu_context=context)
        report = self.replay(captured_fusb=fusb)
        boundary = report['boundary']['original_boundary']
        self.assertEqual(boundary['reason'], 'usb_otgcore_outer_cache_sync_unmodelled')
        self.assertEqual(boundary['l2x0_sync_address'], 0xF8002730)
        self.assertEqual((boundary['old_gctl'], boundary['prepared_gctl']), (0, 0x800))
        self.assertFalse(boundary['gctl_write_completed'])
        self.assertNotIn('returned', boundary)
        self.assertEqual(report['final_state']['gadgets'][0]['created'], 0)


if __name__ == '__main__':
    unittest.main()
