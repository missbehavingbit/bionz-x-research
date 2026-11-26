"""Bounded USB startup calls in an identity-checked, original im.elf process.

The checkpoint must be generated locally by the original process runner. Calls
use its existing thread, TLS, stack, dynamic bindings and allocator. No import,
allocator, userspace callback, FSM state or descriptor is replaced here.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import sys

import elftools
from elftools.elf.elffile import ELFFile
import unicorn
from unicorn import arm_const as arm

from project_paths import PROJECT_ROOT
from src.a6400.execution.guest_memory import accessible
from src.a6400.execution.guest_filesystem import UnsupportedFilesystem
from src.a6400.execution.guest_checkpoint import save_checkpoint
from src.a6400.execution.guest_fusb_startup import CapturedFusbStartup, KERNEL_SIZE
from src.a6400.execution.guest_osal_alloc import (
    QUEUE_ID_USB_CONTROL_LOCAL, lookup_local_queue,
)
from src.a6400.execution.interpreter_probe import (
    module_mapping_entries, resumed_module_base,
)
from tools.research.emulator_session import build_boot_session
from tools.research.experiment_ui_boot import build_parser
from tools.research.probe_usb_local_setconfig import INPUTS, RouteBoundary, SYMBOLS
from tools.research.stella_allocator_probe import LIBC_SHA256


RETURN = 0x5E000000
ARG_REGISTERS = (
    arm.UC_ARM_REG_R0, arm.UC_ARM_REG_R1,
    arm.UC_ARM_REG_R2, arm.UC_ARM_REG_R3,
)
BOOT_OPTIONS = (
    '--fast-hooks', '--quiet-boot', '--stimulus', 'none',
    '--backup-service', 'preset-backed', '--graphics-service', 'authentic-grm',
    '--uipc-pool-options', 'kernel-e0303', '--null-page', 'read-zero',
    '--kikilog-device', 'observed-running-e0301', '--kmod-service', 'verified-image',
    '--gpio-device', 'driver-bound', '--usb-event-device', 'driver-bound',
    '--usb-otgcore-device', 'driver-bound', '--usb-buspower-device', 'driver-bound',
    '--usb-reply-policy', 'verified-connect', '--regular-inode-rdev',
    '--urandom-open', '--urandom-entropy', 'deterministic',
    '--readonly-image-times', '--madvise-dontneed',
    '--tmp-overlay', 'experimental-empty', '--proc-overlay', 'experimental-empty',
    '--setting-service', 'observed-dat4', '--setting-overlay', 'observed-e0301',
    '--stream-device', 'image-driver', '--gles-device', 'kernel-bound-decoded',
    '--stream2-device', 'open-only', '--hdmi-device', 'driver-bound',
    '--cec-device', 'driver-bound',
)
IDENTITIES = {
    **{name: digest for name, _, _, digest in INPUTS},
    'libc-2.11.2.so': LIBC_SHA256,
    'libstdc++.so.6.0.14': INPUTS[3][3],
    'libpthread-2.11.2.so': '938387093567f5721bdeafa5bf68275d3a8705c8c6536745358e229af27fcab8',
    'libInfraUsbCmn.so': '5338bafbb545ebc223fac15c9253a41ac9fe49c01fe32c73af40c3fb50eec0a6',
}
USB_SYMBOLS = {
    'control_instance': '_ZN10UsbControl11GetInstanceEv',
    'wrapper_instance': '_ZN12UsbIfWrapper11GetInstanceEv',
    'setconfig_action': '_ZN10UsbControl16actionGSetConfigEPN3UFW3FsmEiPv',
    'configure_action': '_ZN10UsbControl20actionGConfigureFuncEPN3UFW3FsmEiPv',
    'start_action': '_ZN10UsbControl18actionGStartGadgetEPN3UFW3FsmEiPv',
    'gadget_entry': '_ZN10UsbControl23actionGGadgetStartEntryEPN3UFW3FsmEiPv',
    'start_gadget': '_ZN12UsbIfWrapper11StartGadgetEii',
    'register_gadget': '_ZN7CUsbSvc14registerGadgetE8CUSB_FIDP19CUSB_GADGET_REGINFO',
    'create_gadget': '_ZN7CUsbSvc12createGadgetEP8CUSB_MES',
    'gadget_event': '_ZN7CUsbSvc18proc_kevent_gadgetEmhhPv',
    'service_start': '_ZN7CUsbSvc5startE11__USBIF_DIDPvhPFS1_S1_S1_hiE',
    'new_gadget': '_ZN7CUsbSvc10new_gadgetE8CUSB_FID',
    'do_start': '_ZN12UsbIfWrapper7doStartEiii',
    'sync_start_stop': '_ZN12UsbIfWrapper15doSyncStartStopENS_7OP_TYPEEiii',
}
PROCESS_ARGUMENTS = ('debug', 's', 'g', 'r', 'u', 'l', 'i', 'a', 'lazy-global')


def checked_checkpoint(path, expected):
    path = Path(path).resolve()
    if not path.is_relative_to((PROJECT_ROOT / 'artifacts').resolve()):
        raise ValueError('Checkpoint must be a locally generated artifact')
    if len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
        raise ValueError('Checkpoint SHA-256 must be lowercase hexadecimal')
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != expected:
        raise ValueError('Checkpoint SHA-256 mismatch')
    return path


def loaded_module(probe, name, expected):
    entries = module_mapping_entries(name, probe.mmap.mappings)
    paths = {entry['path'] for entry in entries}
    if len(paths) != 1:
        raise RouteBoundary('usb_module_mapping_ambiguous', module=name, paths=sorted(paths))
    path = next(iter(paths))
    raw = probe.filesystem.cache.get(path)
    if raw is None:
        raise RouteBoundary('usb_loaded_module_bytes_missing', module=name, path=path)
    raw = bytes(raw)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise RouteBoundary('usb_loaded_module_identity_mismatch', module=name, path=path)
    elf = ELFFile(io.BytesIO(raw))
    loads = [{'file_offset': segment['p_offset'], 'vaddr': segment['p_vaddr']}
             for segment in elf.iter_segments() if segment['p_type'] == 'PT_LOAD']
    resolution = resumed_module_base(name, entries, loads)
    base = resolution['base']
    for segment in elf.iter_segments():
        if segment['p_type'] == 'PT_LOAD' and segment['p_flags'] & 1:
            original = raw[segment['p_offset']:segment['p_offset'] + segment['p_filesz']]
            if bytes(probe.mu.mem_read(base + segment['p_vaddr'], len(original))) != original:
                raise RouteBoundary('usb_loaded_executable_bytes_changed', module=name,
                                    address=base + segment['p_vaddr'], size=len(original))
    symbols = {symbol.name: base + symbol['st_value']
               for symbol in elf.get_section_by_name('.dynsym').iter_symbols()
               if symbol.name and symbol['st_shndx'] != 'SHN_UNDEF'}
    return {'name': name, 'path': path, 'sha256': expected, 'base': base,
            'symbols': symbols}


def registry_nodes(reader, head):
    nodes, seen = [], set()
    while head:
        if head in seen:
            raise RouteBoundary('usb_gadget_registry_cycle', address=head)
        if len(seen) >= 19:
            raise RouteBoundary('usb_gadget_registry_limit')
        seen.add(head)
        raw = bytes(reader(head, 0x48))
        if len(raw) != 0x48:
            raise RouteBoundary('usb_gadget_registry_short_read', address=head)
        nodes.append({
            'record': head, 'object': struct.unpack_from('<I', raw, 4)[0],
            'created': raw[8], 'function_id': struct.unpack_from('<I', raw, 0x34)[0],
        })
        head = struct.unpack_from('<I', raw, 0x44)[0]
    return nodes


class ProcessUsbStartup:
    def __init__(self, probe, *, instruction_budget=500000):
        if (isinstance(instruction_budget, bool) or
                not isinstance(instruction_budget, int) or instruction_budget <= 0):
            raise ValueError('Instruction budget must be positive')
        self.probe, self.mu = probe, probe.mu
        self.budget = instruction_budget
        self.instructions = 0
        self.stop = None
        self.phase = 'inspect'
        self.control = None
        self.receiver_ready = False
        self.pending = {}
        self.live = {}
        self.released = set()
        self.modules = {}
        self.functions = {}
        self.symbols = {}
        self.report = {
            'status': 'not_started', 'camera_access': False,
            'configuration_path_completed': False,
            'gadget_event_produced': False,
            'setconfig_delivered': False,
            'scope': 'bounded original USB calls within a resumed im.elf process',
            'trace': [], 'heap_events': [], 'states': [], 'checks': [],
            'port_file_transactions': [],
            'scheduling': 'original threads; preemption disabled during bounded ABI calls',
            'tool_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'tools': {'python': sys.version, 'unicorn': unicorn.__version__,
                      'pyelftools': elftools.__version__,
                      'cpu': probe.report.get('cpu')},
            'limitations': [
                'Locally generated process checkpoint, not a physical-camera snapshot.',
                'Original startup retains its declared experimental namespace, canary, '
                'clock, IPC, CPU-workaround and device policies.',
                'Configure/start commands are explicit experiment stimuli; they are not '
                'observations of an original higher-level application decision.',
                'An existing caller thread supplies TLS and unused stack space for bounded '
                'ABI calls. Its saved CPU context is restored after each returning call.',
                'Existing cooperative scheduling resumes runnable receiver/service threads; '
                'any unrelated unsupported operation still stops execution.',
                'No host heap or free suppression, FSM edits, fabricated gadget context '
                'or automatic hardware acknowledgements are supplied.',
                'This runner establishes the startup prerequisite frontier only. It stops '
                'before the unsupported gadget ioctl; it does not inject a SETCONFIG event.',
            ],
        }
        for name in ('libc-2.11.2.so', 'libUsbControl.so', 'libMWF.so',
                     'libosal_uipc.so', 'libInfraUsbCmn.so',
                     'libstdc++.so.6.0.14', 'libpthread-2.11.2.so'):
            module = loaded_module(probe, name, IDENTITIES[name])
            self.modules[name] = module
            for symbol, address in module['symbols'].items():
                self.symbols.setdefault(symbol, address)
        self.report['inputs'] = [{k: v for k, v in module.items() if k != 'symbols'}
                                 for module in self.modules.values()]
        observed = {
            **{name: SYMBOLS[name] for name in (
                'event_listener', 'send_event', 'local_callback', 'preprocess',
                'post', 'receive', 'process', 'dispatch', 'fsm_handle_event',
                'get_int32', 'broadcast')},
            **USB_SYMBOLS,
            'malloc': 'malloc', 'free': 'free',
            'osal_snd_sync_msg': 'osal_snd_sync_msg',
            'osal_snd_msg': 'osal_snd_msg',
        }
        for label, symbol in observed.items():
            self.functions[self.address(symbol) & ~1] = label
        infra = self.modules['libInfraUsbCmn.so']['base']
        for offset, label in (
            (0xD550, 'usbif_start'), (0xD1B0, 'usbif_select_port'),
            (0xD30E, 'usbif_set_speed'), (0xD124, 'usbif_alloc_user'),
            (0xD0F2, 'usbif_commit_user'),
        ):
            self.functions[infra + offset] = label
        if any(start <= RETURN <= end for start, end, _ in self.mu.mem_regions()):
            raise RouteBoundary('usb_call_return_page_collision', address=RETURN)
        self.mu.mem_map(RETURN, 4096, unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC)
        self.mu.mem_write(RETURN, bytes.fromhex('fee7'))
        self.mu.hook_add(unicorn.UC_HOOK_CODE, self._code)
        self.probe.thread_timeslice = 0

    def address(self, name):
        return self.symbols[USB_SYMBOLS.get(name, SYMBOLS.get(name, name))]

    def word(self, address):
        return struct.unpack('<I', self.mu.mem_read(address, 4))[0]

    def check(self, name, condition):
        if not condition:
            raise RouteBoundary('process_usb_check_failed', check=name)
        self.report['checks'].append(name)

    def state(self):
        result = {'phase': self.phase}
        if self.control is not None:
            result.update(control=self.control, fsm=self.word(self.control + 0x104),
                          configured=self.word(self.control + 0x94),
                          started=self.word(self.control + 0x98))
        interface = self.word(self.modules['libUsbControl.so']['base'] + 0x1EAC4)
        if interface:
            service = self.word(interface + 0x3C)
            result.update(interface=interface, service=service,
                          service_state=self.word(service + 0x98),
                          device_id=self.word(service + 0x9C),
                          gadget_registry=self.word(service + 0xC0),
                          gadgets=registry_nodes(self.mu.mem_read, self.word(service + 0xC0)),
                          selected_port=self.word(service + 0x114),
                          enabled_contexts=sorted(self.probe.usb_event.enabled_events))
        return result

    def install_selected_port_file(self):
        filesystem = self.probe.filesystem
        original_request = filesystem.request
        self.report['selected_port_file'] = {
            'path': '/var/usbport', 'data_hex': '30',
            'classification': 'explicit memory-only port-0 input fixture, not a camera file',
        }

        def request(number, args):
            descriptor = filesystem.fds.get(args[0])
            if number == 5 and filesystem._path(args[0]) == '/var/usbport':
                if args[1] != 0x1000 or self.state().get('selected_port') != 0:
                    raise UnsupportedFilesystem('usb_port_fixture_contract_mismatch',
                                                arguments=list(args[:3]))
                fd = next((candidate for candidate in range(3, 259)
                           if candidate not in filesystem.fds), None)
                if fd is None:
                    raise UnsupportedFilesystem('usb_port_fixture_descriptor_limit')
                filesystem.fds[fd] = {
                    'path': '/var/usbport', 'device': 'usb_port_fixture', 'offset': 0}
                result = {'syscall': 'open', 'returned': fd}
            elif descriptor and descriptor.get('device') == 'usb_port_fixture':
                if number == 6:
                    del filesystem.fds[args[0]]
                    result = {'syscall': 'close', 'returned': 0}
                elif number == 3 and args[2] == 1 and descriptor['offset'] == 0:
                    if not accessible(self.mu, args[1], 1, unicorn.UC_PROT_WRITE):
                        raise UnsupportedFilesystem('usb_port_fixture_read_buffer_invalid',
                                                    pointer=args[1])
                    self.mu.mem_write(args[1], b'0')
                    if filesystem.after_write:
                        filesystem.after_write(args[1], 1)
                    descriptor['offset'] = 1
                    result = {'syscall': 'read', 'returned': 1}
                else:
                    raise UnsupportedFilesystem('usb_port_fixture_operation_unsupported',
                                                number=number, arguments=list(args[:3]))
            else:
                return original_request(number, args)
            result.update(path='/var/usbport', arguments=list(args[:3]),
                          reason='explicit_memory_only_selected_port_fixture')
            self.report['port_file_transactions'].append(result)
            return result

        filesystem.request = request

    def _code(self, uc, address, size, _):
        self.instructions += 1
        if self.instructions > self.budget:
            raise RouteBoundary('process_usb_instruction_limit', limit=self.budget)
        tid = self.probe.threads.current_tid
        sp = uc.reg_read(arm.UC_ARM_REG_SP)
        pending = self.pending.get((tid, address), [])
        while pending and pending[-1]['sp'] == sp:
            entry = pending.pop()
            result = uc.reg_read(arm.UC_ARM_REG_R0)
            entry['returned'] = result
            if entry['function'] == 'malloc' and result:
                self.check('original_malloc_returns_aligned_storage', result % 8 == 0)
                self.live[result] = entry['args'][0]
                self.released.discard(result)
                self.report['heap_events'].append({
                    'operation': 'allocate', 'address': result,
                    'size': entry['args'][0], 'phase': entry['phase'], 'tid': tid})
            elif entry['function'] == 'free' and entry['args'][0]:
                pointer = entry['args'][0]
                size = self.live.pop(pointer, None)
                self.released.add(pointer)
                self.report['heap_events'].append({
                    'operation': 'free', 'address': pointer, 'size': size,
                    'ownership': 'observed_allocation' if size is not None else 'preexisting',
                    'phase': entry['phase'], 'tid': tid})
        if address == RETURN:
            self.stop = 'return'
            uc.emu_stop()
            return
        name = self.functions.get(address)
        if not name:
            return
        if len(self.report['trace']) >= 2000:
            raise RouteBoundary('process_usb_trace_limit')
        args = [uc.reg_read(register) for register in ARG_REGISTERS]
        if name == 'receive' and self.stop_on_receive and args[0] == self.control + 0x70:
            queue = lookup_local_queue(
                self.mu.mem_read, self.modules['libosal_uipc.so']['base'],
                QUEUE_ID_USB_CONTROL_LOCAL)['object']
            if self.word(queue + 0xC) == 0:
                self.stop = 'receiver_ready'
                uc.emu_stop()
                return
        entry = {'function': name, 'tid': tid, 'pc': address, 'args': args,
                 'phase': self.phase}
        if self.control is not None and name in ('dispatch', 'fsm_handle_event',
                                                 'setconfig_action', 'gadget_entry'):
            entry['fsm_state'] = self.word(self.control + 0x104)
        self.report['trace'].append(entry)
        key = (tid, uc.reg_read(arm.UC_ARM_REG_LR) & ~1)
        entry['sp'] = sp
        self.pending.setdefault(key, []).append(entry)
        if name == 'free':
            pointer = args[0]
            if pointer in self.released:
                raise RouteBoundary('process_usb_double_free', address=pointer)
            if pointer not in self.live and any(lo < pointer < lo + length
                                                for lo, length in self.live.items()):
                raise RouteBoundary('process_usb_interior_free', address=pointer)

    def _run(self, entry, *, stop_on_receive=False):
        remaining = self.budget - self.instructions
        if remaining <= 0:
            raise RouteBoundary('process_usb_instruction_limit', limit=self.budget)
        self.stop, self.stop_on_receive = None, stop_on_receive
        self.probe.report.pop('boundary', None)
        self.probe.report.update(status='running',
                                 instruction_budget=self.probe.instructions + self.budget)
        self.mu.emu_start(entry, 0xFFFFFFFF, count=remaining, timeout=10000000)
        if self.stop is None:
            if self.instructions >= self.budget:
                raise RouteBoundary('process_usb_instruction_limit', limit=self.budget)
            raise RouteBoundary('process_usb_execution_boundary',
                                status=self.probe.report['status'],
                                original_boundary=self.probe.report.get('boundary'),
                                pc=self.mu.reg_read(arm.UC_ARM_REG_PC))

    def call(self, name, *args):
        context = self.mu.context_save()
        tid = self.probe.threads.current_tid
        sp = (self.mu.reg_read(arm.UC_ARM_REG_SP) - 0x1000) & ~7
        if not accessible(self.mu, sp - 0x4000, 0x5000,
                          unicorn.UC_PROT_READ | unicorn.UC_PROT_WRITE):
            raise RouteBoundary('process_usb_caller_stack_not_available', tid=tid, sp=sp)
        self.mu.reg_write(arm.UC_ARM_REG_SP, sp)
        self.mu.reg_write(arm.UC_ARM_REG_LR, RETURN | 1)
        for register, value in zip(ARG_REGISTERS, args):
            self.mu.reg_write(register, value)
        for index, value in enumerate(args[4:]):
            self.mu.mem_write(sp + index * 4, struct.pack('<I', value))
        self._run(self.address(name))
        result = self.mu.reg_read(arm.UC_ARM_REG_R0)
        self.check('original_call_returned_' + name, self.stop == 'return')
        self.check('original_call_kept_caller_thread', self.probe.threads.current_tid == tid)
        self.mu.context_restore(context)
        return result

    def send_command(self, message_id, function=1):
        caller = self.probe.threads.current_tid
        self.probe.threads.save_context(self.mu)
        message = self.call('malloc', 8)
        parameter = self.call('malloc', 12)
        self.check('command_storage_allocated', bool(message and parameter))
        self.call('objmsg_ctor', message, 0x6103, message_id)
        self.call('param_ctor', parameter, function)
        self.call('add_param', message, 0x1005, parameter)
        self.call('param_dtor', parameter)
        self.call('free', parameter)
        result = self.call('send_event', self.control + 0x28, message)
        self.check('original_command_send_completed', result == 0)
        self.call('objmsg_dtor', message)
        self.call('free', message)
        receiver = next(thread for thread in self.probe.threads.threads.values()
                        if thread.name == 'UsbControl')
        self.check('original_receiver_is_ready_for_delivery',
                   receiver.state == 'runnable' or
                   (self.receiver_ready and receiver.state == 'running'))
        self.probe.threads.restore_context(self.mu, receiver.tid, self.probe.kuser)
        entry = self.mu.reg_read(arm.UC_ARM_REG_PC)
        entry |= int(bool(self.mu.reg_read(arm.UC_ARM_REG_CPSR) & 0x20))
        self._run(entry, stop_on_receive=True)
        self.check('original_receiver_processed_and_released_command',
                   self.stop == 'receiver_ready')
        self.receiver_ready = True
        self.probe.threads.save_context(self.mu)
        self.probe.threads.restore_context(self.mu, caller, self.probe.kuser)

    def execute(self, *, exercise_startup=False, function=1):
        try:
            self.control = self.call('control_instance')
            self.report['states'].append(self.state())
            if exercise_startup:
                self.install_selected_port_file()
                self.check('original_startup_reached_stop_state',
                           self.word(self.control + 0x104) == 1)
                self.phase = 'configure'
                self.send_command(0x83003, function)
                self.report['states'].append(self.state())
                self.check('original_configure_reached_state_5',
                           self.word(self.control + 0x104) == 5)
                self.check('configure_message_heap_balanced', not self.live)
                self.phase = 'start'
                self.send_command(0x83001, function)
                self.report['states'].append(self.state())
                self.check('original_start_reached_state_7',
                           self.word(self.control + 0x104) == 7)
                self.check('original_gadget_start_succeeded', any(
                    entry['function'] == 'start_gadget' and entry.get('returned') == 0
                    for entry in self.report['trace']))
            self.report['status'] = 'original_process_usb_state_observed'
        except RouteBoundary as error:
            self.report.update(status='boundary', boundary=error.details)
        except (ValueError, KeyError, OSError, StopIteration, unicorn.UcError) as error:
            self.report.update(status='failed', boundary={
                'reason': type(error).__name__, 'error': str(error)})
        self.report.update(instructions=self.instructions, heap_live=self.live,
                           incomplete_calls=[
                               {'function': entry['function'], 'tid': entry['tid'],
                                'phase': entry['phase'], 'pc': entry['pc']}
                               for entry in self.report['trace'] if 'returned' not in entry],
                           final_state=self.state(),
                           pc=self.mu.reg_read(arm.UC_ARM_REG_PC),
                           arguments=[self.mu.reg_read(reg) for reg in ARG_REGISTERS])
        return self.report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--checkpoint', type=Path)
    source.add_argument('--cold-start', action='store_true',
                        help='regenerate the original process with declared experimental policies')
    parser.add_argument('--checkpoint-sha256')
    parser.add_argument('--otg-connected-kernel', type=Path,
                        help='opt in to original FUSB callbacks and the E0597 connected guard')
    parser.add_argument('--otg-gate-clear-control', action='store_true',
                        help='use the E0597 clear-bit negative control instead of the snapshot')
    parser.add_argument('--otg-qemu-context', type=Path,
                        help='load a pinned stock QEMU report with native DWC3 prefix fields')
    parser.add_argument('--otg-qemu-context-sha256')
    parser.add_argument('--report-dir', required=True, type=Path)
    parser.add_argument('--instructions', type=int, default=500000)
    parser.add_argument('--function', type=lambda value: int(value, 0),
                        choices=(1, 2, 4, 8, 16), default=1)
    parser.add_argument('--exercise-startup', action='store_true')
    args = parser.parse_args(argv)
    if args.instructions <= 0:
        parser.error('--instructions must be positive')
    if bool(args.checkpoint) != bool(args.checkpoint_sha256):
        parser.error('--checkpoint requires --checkpoint-sha256, and vice versa')
    if args.otg_connected_kernel and not (args.exercise_startup and args.function == 4):
        parser.error('--otg-connected-kernel requires the diagnostic function 4 startup')
    if args.otg_gate_clear_control and not args.otg_connected_kernel:
        parser.error('--otg-gate-clear-control requires --otg-connected-kernel')
    if bool(args.otg_qemu_context) != bool(args.otg_qemu_context_sha256):
        parser.error('--otg-qemu-context requires --otg-qemu-context-sha256 and vice versa')
    if args.otg_qemu_context and (not args.otg_connected_kernel or args.otg_gate_clear_control):
        parser.error('The QEMU context requires the opt-in connected kernel policy')
    output = args.report_dir.resolve()
    if not output.is_relative_to((PROJECT_ROOT / 'artifacts').resolve()):
        parser.error('Report directory must remain under artifacts')
    output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'configuration_error', 'camera_access': False}
    try:
        captured_fusb = None
        if args.otg_connected_kernel:
            kernel = args.otg_connected_kernel.resolve()
            if (not kernel.is_relative_to((PROJECT_ROOT / 'artifacts').resolve()) or
                    kernel.stat().st_size != KERNEL_SIZE):
                raise ValueError('Expected the prepared captured kernel under artifacts')
            qemu_context = None
            if args.otg_qemu_context:
                receipt = checked_checkpoint(args.otg_qemu_context, args.otg_qemu_context_sha256)
                if receipt.stat().st_size > 256 * 1024:
                    raise ValueError('QEMU USB context report exceeds the bounded size')
                capture = json.loads(receipt.read_bytes())
                if (not isinstance(capture, dict) or capture.get('status') != 'boundary' or
                        not capture.get('stock_attach') or 'native_dwc3_context' not in capture):
                    raise ValueError('Expected a bounded stock QEMU report')
                qemu_context = capture['native_dwc3_context']
            captured_fusb = CapturedFusbStartup(kernel.read_bytes(),
                                                gate_clear_control=args.otg_gate_clear_control,
                                                qemu_context=qemu_context)
        options = list(BOOT_OPTIONS)
        if args.checkpoint:
            checkpoint = checked_checkpoint(args.checkpoint, args.checkpoint_sha256)
            options.extend(('--load-checkpoint', str(checkpoint)))
        for argument in PROCESS_ARGUMENTS:
            options.extend(('--argument', argument))
        boot_args = build_parser().parse_args(options)
        from src.a6400.execution import guarded_bytes
        guarded_bytes.PAGE_HOOKS = True
        probe = build_boot_session(boot_args).probe
        if args.cold_start:
            probe.thread_timeslice = 10000
            boot_report = probe.run(180000000, 300000000)
            (output / 'bootstrap.json').write_text(
                json.dumps(boot_report, indent=2) + '\n', encoding='utf-8')
            checkpoint = output / 'bootstrap.ckpt'
            save_checkpoint(probe, checkpoint)
            if (boot_report['status'] != 'unverified_device_read' or
                    boot_report.get('boundary', {}).get('physical_address') != 0x20030000):
                raise RouteBoundary('usb_bootstrap_stopped_before_expected_checkpoint',
                                    status=boot_report['status'],
                                    boundary=boot_report.get('boundary'))
        with checkpoint.open('rb') as stream:
            checkpoint_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        replay = ProcessUsbStartup(probe, instruction_budget=args.instructions)
        if captured_fusb is not None:
            probe.usb_otgcore.captured_fusb_startup = captured_fusb
        try:
            report = replay.execute(exercise_startup=args.exercise_startup, function=args.function)
        finally:
            if captured_fusb is not None:
                del probe.usb_otgcore.captured_fusb_startup
        if captured_fusb is not None:
            report['otg_connected_guard'] = {
                'policy': 'e0597-connected-start-guard', 'kernel_sha256': captured_fusb.sha256,
                'gate_clear_control': args.otg_gate_clear_control,
                'kernel_path': kernel.relative_to(PROJECT_ROOT).as_posix(),
                'transactions': [
                    row for row in probe.usb_otgcore.transactions
                    if row.get('source') in ('captured-kernel-arm-callback',
                                             'e0597-connected-start-guard')],
                'limitation': ('Controller inputs are snapshots and fixtures, not a booted kernel '
                               'in this checkpoint. The DWC3 callback is not completed.'),
            }
            if args.otg_qemu_context:
                report['otg_connected_guard']['qemu_context_sha256'] = args.otg_qemu_context_sha256
        report['checkpoint'] = {
            'path': checkpoint.relative_to(PROJECT_ROOT).as_posix(), 'sha256': checkpoint_sha}
        report['policies'] = list(BOOT_OPTIONS)
        report['process_arguments'] = list(PROCESS_ARGUMENTS)
        if report['status'] == 'boundary':
            save_checkpoint(probe, output / 'boundary.ckpt')
            with (output / 'boundary.ckpt').open('rb') as stream:
                report['boundary_checkpoint_sha256'] = hashlib.file_digest(
                    stream, 'sha256').hexdigest()
    except RouteBoundary as error:
        report.update(status='boundary', boundary=error.details)
    except (ValueError, KeyError, OSError, StopIteration, unicorn.UcError) as error:
        report.update(status='failed', boundary={
            'reason': type(error).__name__, 'error': str(error)})
    finally:
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n',
                                            encoding='utf-8')
    print(report['status'])
    print(json.dumps(report.get('boundary', report.get('final_state')), indent=2))
    return 0 if report['status'] == 'original_process_usb_state_observed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
