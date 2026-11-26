"""Replay the original process-local USB SETCONFIG route without camera access.

The stimulus enters UsbIfWrapper::EventListner, not an adjustment callback.
Original OSAL code owns registration, allocation, synchronous callback dispatch,
asynchronous posting, receiver wakeup and envelope release. UsbControl::Main
owns ProcessMsg, FSM dispatch and destruction of the received ObjMsg.

This is an isolated original-instruction experiment, not a booted application.
Heap and pthread-mutex services are checked fixtures; gadget registration,
physical enumeration and a USB mode transition are not supplied or claimed.
"""
from __future__ import annotations

import argparse
from collections import Counter
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
from src.a6400.execution.guest_osal_alloc import (
    FBLOCK_CONTROL_OFFSET,
    QUEUE_ID_USB_CONTROL_LOCAL,
    lookup_local_queue,
)
from src.a6400.execution.guest_threads import GuestThread, GuestThreads
from src.a6400.execution.mwf_objmsg import (
    SETCONFIG_CATEGORY,
    SETCONFIG_MESSAGE_ID,
    SETCONFIG_PARAM_ID,
)
from src.a6400.images.process_elf import inspect_image


PARTITIONS = (
    PROJECT_ROOT / 'firmware' / 'extracted_a6400' / 'firmware.tar_unpacked'
    / '0700_part_image' / 'dev'
)
INPUTS = (
    ('libosal_uipc.so', PARTITIONS / 'nflasha15_unpacked' / 'lib' / 'libosal_uipc.so',
     0x10000000, 'b6ded35add9582413b401cf4db78ec2c9a201b632e78c2731d6bbb379249b3eb'),
    ('libMWF.so', PARTITIONS / 'nflasha15_unpacked' / 'lib' / 'libMWF.so',
     0x11000000, 'a79dc0d558e6454e31e57ea3b026364be924d0e78c1b6101015125a249f7e274'),
    ('libUsbControl.so', PARTITIONS / 'nflasha15_unpacked' / 'lib' / 'libUsbControl.so',
     0x12000000, '70cd5742fde9eea78c60905e4015531b4c3ab2668d2cb85c934531f451f1211f'),
    ('libstdc++.so.6', PARTITIONS / 'nflasha7_unpacked' / 'lib' / 'libstdc++.so.6.0.14',
     0x14000000, 'aee778e0f0d6a4dc6031d3e50940ff831a4222d9efa438fcdd28599e07e75918'),
)
OSAL_BASE = INPUTS[0][2]
IMPORTS = 0x13000000
RETURN = IMPORTS + 0xF000
FIXTURE = 0x20000000
CONTROL = FIXTURE + 0x1000
WRAPPER = FIXTURE + 0x2000
MESSAGE = FIXTURE + 0x3000
PARAM = MESSAGE + 0x20
FBLOCK = 0x22000000
FBLOCK_SIZE = 0x10000
SENDER_STACK = 0x23000000
RECEIVER_STACK = 0x23010000
STACK_SIZE = 0x10000
SENDER, RECEIVER = 1000, 1001
ARG_REGISTERS = (
    arm.UC_ARM_REG_R0, arm.UC_ARM_REG_R1,
    arm.UC_ARM_REG_R2, arm.UC_ARM_REG_R3,
)
SYMBOLS = {
    'sender_ctor': '_ZN3UFW9MsgSenderC1Etjj',
    'receiver_ctor': '_ZN3UFW11MsgReceiverC1Etj',
    'sem_ctor': '_ZN3UFW3SemC1EtPKc',
    'list_ctor': '_ZNSt4listIjSaIjEEC1Ev',
    'fsm_ctor': '_ZN3UFW3FsmC1EPKcPPKNS_18FsmEventDispatcherEPvt',
    'fsm_init': '_ZN3UFW3Fsm4InitEi',
    'fsm_table': '_ZN10UsbControl10s_stateTblE',
    'objmsg_ctor': '_ZN3MWF6ObjMsgC1Ejj',
    'objmsg_dtor': '_ZN3MWF6ObjMsgD1Ev',
    'param_ctor': '_ZN3MWF17ParamStd_TemplateIiLNS_5Param4TypeE7EEC1Ei',
    'param_dtor': '_ZN3MWF17ParamStd_TemplateIiLNS_5Param4TypeE7EED1Ev',
    'add_param': '_ZN3MWF6ObjMsg8AddParamEjRKNS_5ParamE',
    'event_listener': '_ZN12UsbIfWrapper12EventListnerEPN3MWF6ObjMsgE',
    'send_event': '_ZN3UFW9MsgSender7SendEvtEPN3MWF6ObjMsgE',
    'local_callback': '_ZN10UsbControl14funcOsalSyncCbEPvS0_',
    'preprocess': '_ZN10UsbControl10preProcessEPv',
    'post': '_ZN3UFW11MsgReceiver7PostMsgEPv',
    'receive': '_ZN3UFW11MsgReceiver6RcvMsgEPjPPN3MWF6ObjMsgE',
    'main': '_ZN10UsbControl4MainEv',
    'process': '_ZN10UsbControl10ProcessMsgEjPN3MWF6ObjMsgE',
    'event_map': '_ZN10UsbControl13MsgId2EventIdEj',
    'dispatch': '_ZN3UFW3Fsm13DispatchEventEiPv',
    'fsm_execute_main': '_ZN3UFW3Fsm11executeMainEi',
    'fsm_handle_event': '_ZN3UFW3Fsm11handleEventEPKNS_18FsmEventDispatcherEiPiPPFvPS0_iPvE',
    'get_int32': '_ZN3UFW7MsgUtil8GetInt32EPN3MWF6ObjMsgEj',
    'broadcast': '_ZN3UFW9MsgSender12BroadCastEvtEPN3MWF6ObjMsgERSt4listIjSaIjEE',
}
OBSERVED = (
    'event_listener', 'send_event', 'osal_snd_sync_msg', '__osal_snd_sync_msg',
    'local_callback', 'preprocess', 'post', 'osal_snd_msg', 'receive', 'process',
    'event_map', 'dispatch', 'fsm_execute_main', 'fsm_handle_event',
    'get_int32', 'broadcast', 'osal_free_msg',
)
WRONG_LANES = (
    '_ZN3UFW7AdjBase14funcOsalSyncCbEPvS1_',
    '_ZN10UsbControl14funcInputGetCbEPvS0_',
)


class RouteBoundary(RuntimeError):
    def __init__(self, reason, **details):
        super().__init__(reason)
        self.details = {'reason': reason, **details}


class FixtureHeap:
    """Bounded allocation fixture shared by malloc/free and original C++ new/delete."""

    def __init__(self, memory, base=0x21000000, size=0x100000):
        self.memory = memory
        self.base, self.size, self.next = base, size, base
        self.live = {}
        self.events = []

    def allocate(self, size):
        if not isinstance(size, int) or size <= 0:
            raise RouteBoundary('fixture_heap_invalid_size', size=size)
        address = (self.next + 7) & ~7
        if size > self.base + self.size - address:
            raise RouteBoundary('fixture_heap_exhausted', size=size)
        self.next = address + size
        self.live[address] = size
        self.memory.mem_write(address, b'\xA5' * size)
        self.events.append({'operation': 'allocate', 'address': address, 'size': size})
        return address

    def free(self, address):
        if address == 0:
            return
        if address not in self.live:
            raise RouteBoundary('fixture_heap_foreign_or_double_free', address=address)
        size = self.live.pop(address)
        self.events.append({'operation': 'free', 'address': address, 'size': size})


class LocalSetconfigReplay:
    def __init__(self, *, register=True, instruction_budget=100000):
        if not isinstance(register, bool):
            raise ValueError('register must be a boolean')
        if (isinstance(instruction_budget, bool) or
                not isinstance(instruction_budget, int) or instruction_budget <= 0):
            raise ValueError('instruction_budget must be positive')
        self.register = register
        self.budget = instruction_budget
        self.mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_ARM)
        self.mu.ctl_set_cpu_model(arm.UC_CPU_ARM_CORTEX_A9)
        self.modules = []
        self.exports = {}
        self.imports = {}
        self.unsupplied_slots = set()
        self.executable_ranges = []
        self.observed_addresses = {}
        self.wrong_lane_addresses = {}
        self.pending_returns = {}
        self.mutexes = {}
        self.instructions = 0
        self.stop = None
        self.heap = FixtureHeap(self.mu)
        self.threads = GuestThreads(SENDER)
        self.threads.threads[RECEIVER] = GuestThread(
            RECEIVER, 'UsbControl', state='runnable', tls_val=FIXTURE + 0x800)
        self.threads.threads[SENDER].tls_val = FIXTURE + 0xC00
        self.report = {
            'status': 'running', 'camera_access': False,
            'scope': 'isolated original process-local SETCONFIG route',
            'queue': QUEUE_ID_USB_CONTROL_LOCAL, 'inputs': [],
            'trace': [], 'services': [], 'checks': [],
            'tool_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'tools': {'python': sys.version, 'unicorn': unicorn.__version__,
                      'pyelftools': elftools.__version__,
                      'cpu_model': 'UC_CPU_ARM_CORTEX_A9'},
            'limitations': [
                'Constructed process, two thread contexts and explicitly linked ELF providers; '
                'neither the dynamic loader nor full im.elf startup executes.',
                'Heap and pthread mutex imports use checked fixtures, not guest glibc/stella.',
                'UsbControl subobjects and FSM state 0 are initialized by original constructors; '
                'the notification-target list is empty.',
                'The explicit stimulus starts at EventListner, after the physical gadget-event '
                'producer. No gadget context is fabricated or enabled.',
                'Dispatch and notification construction do not prove a USB mode transition, '
                'hardware enumeration, UVC/PTP operation or deployment safety.',
            ],
        }

    def read(self, address, size):
        return bytes(self.mu.mem_read(address, size))

    def word(self, address):
        return struct.unpack('<I', self.read(address, 4))[0]

    def put(self, address, value):
        self.mu.mem_write(address, struct.pack('<I', value & 0xFFFFFFFF))

    def symbol(self, name):
        return self.exports[SYMBOLS.get(name, name)]

    def check(self, name, condition):
        if not condition:
            raise RouteBoundary('route_check_failed', check=name)
        self.report['checks'].append(name)

    def _load(self):
        for name, path, base, expected in INPUTS:
            image = inspect_image(path, expected)
            elf = ELFFile(io.BytesIO(image['data']))
            self.modules.append((base, elf))
            self.report['inputs'].append({
                'name': name, 'path': path.relative_to(PROJECT_ROOT).as_posix(),
                'sha256': image['sha256'], 'base': base,
            })
            for segment in image['segments']:
                start = (base + segment['p_vaddr']) & ~0xFFF
                end = (base + segment['p_vaddr'] + segment['p_memsz'] + 0xFFF) & ~0xFFF
                flags = segment['p_flags']
                permissions = (
                    (unicorn.UC_PROT_READ if flags & 4 else 0) |
                    (unicorn.UC_PROT_WRITE if flags & 2 else 0) |
                    (unicorn.UC_PROT_EXEC if flags & 1 else 0))
                self.mu.mem_map(start, end - start, permissions)
                self.mu.mem_write(base + segment['p_vaddr'],
                                  image['data'][segment['p_offset']:
                                                segment['p_offset'] + segment['p_filesz']])
                if flags & 1:
                    self.executable_ranges.append((
                        base + segment['p_vaddr'],
                        base + segment['p_vaddr'] + segment['p_filesz']))
            for symbol in elf.get_section_by_name('.dynsym').iter_symbols():
                if symbol.name and symbol['st_shndx'] != 'SHN_UNDEF':
                    self.exports.setdefault(symbol.name, base + symbol['st_value'])
        self.mu.mem_map(IMPORTS, 0x10000, unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC)
        for address, size in (
            (FIXTURE, 0x10000), (self.heap.base, self.heap.size),
            (FBLOCK, FBLOCK_SIZE), (SENDER_STACK, STACK_SIZE),
            (RECEIVER_STACK, STACK_SIZE), (0xFFFF0000, 0x1000),
        ):
            self.mu.mem_map(address, size, unicorn.UC_PROT_READ | unicorn.UC_PROT_WRITE)
        self._relocate()
        self.observed_addresses = {
            self.symbol(name) & ~1: name for name in OBSERVED
        }
        self.wrong_lane_addresses = {
            self.symbol(name) & ~1: name for name in WRONG_LANES
        }
        self.mu.hook_add(unicorn.UC_HOOK_CODE, self._code)
        self.mu.hook_add(unicorn.UC_HOOK_MEM_READ, self._read_guard)

    def _relocate(self):
        counts = Counter()
        for base, elf in self.modules:
            for section in elf.iter_sections():
                if section['sh_type'] != 'SHT_REL':
                    continue
                symbols = elf.get_section(section['sh_link'])
                for relocation in section.iter_relocations():
                    kind = relocation['r_info_type']
                    counts[kind] += 1
                    slot = base + relocation['r_offset']
                    old = self.word(slot)
                    if kind == 23:
                        self.put(slot, base + old)
                        continue
                    if kind in (17, 18):
                        self.unsupplied_slots.add(slot)
                        continue
                    if kind not in (2, 21, 22):
                        raise RouteBoundary('unsupported_relocation', kind=kind, slot=slot)
                    symbol = symbols.get_symbol(relocation['r_info_sym'])
                    name = symbol.name
                    if name in self.exports:
                        value = self.exports[name]
                    else:
                        existing = next((address for address, value in self.imports.items()
                                         if value == name), None)
                        value = existing if existing is not None else IMPORTS + 4 * len(self.imports)
                        if value >= RETURN:
                            raise RouteBoundary('import_stub_limit')
                        self.imports[value] = name
                        if symbol['st_info']['type'] in ('STT_OBJECT', 'STT_TLS'):
                            self.unsupplied_slots.add(slot)
                    self.put(slot, value + (old if kind == 2 else 0))
        self.report['relocations'] = dict(counts)

    def _read_guard(self, uc, access, address, size, value, _):
        for slot in range(address & ~3, address + size, 4):
            if slot in self.unsupplied_slots:
                raise RouteBoundary('unsupplied_relocation_read', address=address, slot=slot)

    def _return(self, value=0):
        self.mu.reg_write(arm.UC_ARM_REG_R0, value & 0xFFFFFFFF)
        self.mu.reg_write(arm.UC_ARM_REG_PC, self.mu.reg_read(arm.UC_ARM_REG_LR))

    def _service(self, name, args):
        a, b, c, d = args
        entry = {'name': name, 'tid': self.threads.current_tid, 'args': args}
        self.report['services'].append(entry)
        if name == 'malloc':
            result = self.heap.allocate(a)
        elif name == 'free':
            self.heap.free(a)
            result = 0
        elif name in ('memcpy', 'memmove'):
            if c > self.heap.size:
                raise RouteBoundary('fixture_copy_limit', length=c)
            self.mu.mem_write(a, self.read(b, c))
            result = a
        elif name == 'memset':
            if c > self.heap.size:
                raise RouteBoundary('fixture_memset_limit', length=c)
            self.mu.mem_write(a, bytes([b & 0xFF]) * c)
            result = a
        elif name == 'pthread_mutex_init':
            if b or a in self.mutexes:
                raise RouteBoundary('fixture_mutex_init_unsupported', address=a, attributes=b)
            self.mutexes[a] = None
            result = 0
        elif name == 'pthread_mutex_lock':
            if a not in self.mutexes or self.mutexes[a] is not None:
                raise RouteBoundary('fixture_mutex_not_available', address=a)
            self.mutexes[a] = self.threads.current_tid
            result = 0
        elif name in ('pthread_mutex_unlock', 'pthread_mutex_destroy'):
            expected = self.threads.current_tid if name.endswith('unlock') else None
            if a not in self.mutexes or self.mutexes[a] != expected:
                raise RouteBoundary('fixture_mutex_ownership', address=a, operation=name)
            if name.endswith('destroy'):
                del self.mutexes[a]
            else:
                self.mutexes[a] = None
            result = 0
        elif name == 'syscall' and a == 240 and c in (0x80, 0x81):
            if c == 0x80:
                self._return(0)
                self.threads.save_context(self.mu)
                result, _, details = self.threads.handle_futex_wait(b, d, self.read)
                entry.update(details)
                if result is not None:
                    raise RouteBoundary('unexpected_futex_wait_mismatch', **details)
                self.stop = 'blocked_futex'
                self.mu.emu_stop()
                return
            result, details = self.threads.handle_futex_wake(b, d)
            entry.update(details)
        else:
            raise RouteBoundary('unsupported_import', symbol=name, arguments=args)
        entry['returned'] = result
        self._return(result)

    def _code(self, uc, address, size, _):
        self.instructions += 1
        args = [uc.reg_read(register) for register in ARG_REGISTERS]
        key = (self.threads.current_tid, address)
        pending = self.pending_returns.get(key)
        stack = uc.reg_read(arm.UC_ARM_REG_SP)
        while pending and pending[-1]['stack'] == stack:
            pending.pop()['entry']['returned'] = args[0]
        if address == RETURN:
            self.stop = 'return'
            uc.emu_stop()
            return
        if address in self.wrong_lane_addresses:
            raise RouteBoundary('wrong_usb_delivery_lane',
                                symbol=self.wrong_lane_addresses[address])
        if address in self.imports:
            self._service(self.imports[address], args)
            return
        if not any(start <= address and address + size <= end
                   for start, end in self.executable_ranges):
            raise RouteBoundary('unsupported_control_flow', address=address, arguments=args)
        name = self.observed_addresses.get(address)
        if name:
            if len(self.report['trace']) >= 256:
                raise RouteBoundary('route_trace_limit')
            entry = {'function': name, 'tid': self.threads.current_tid,
                     'pc': address, 'args': args}
            self.report['trace'].append(entry)
            return_key = (self.threads.current_tid, uc.reg_read(arm.UC_ARM_REG_LR) & ~1)
            self.pending_returns.setdefault(return_key, []).append({
                'entry': entry, 'stack': stack})
            if name in ('process', 'broadcast'):
                pointer = args[2] if name == 'process' else args[1]
                body = self.word(pointer)
                entry['objmsg'] = {
                    'pointer': pointer, 'body': body,
                    'category': self.word(body + 4),
                    'message_id': self.word(body + 8),
                }

    def _emulate(self, entry):
        remaining = self.budget - self.instructions
        if remaining <= 0:
            raise RouteBoundary('instruction_limit', limit=self.budget)
        self.stop = None
        self.mu.emu_start(entry, 0xFFFFFFFF, count=remaining, timeout=10000000)
        if self.stop is None:
            raise RouteBoundary('execution_limit', limit=self.budget)
        return self.stop

    def call(self, function, *args, tid=SENDER, block=False):
        self.threads.current_tid = tid
        self.threads.threads[tid].state = 'running'
        self.mu.reg_write(arm.UC_ARM_REG_CPSR, 0x10)
        self.mu.reg_write(arm.UC_ARM_REG_C13_C0_3, self.threads.threads[tid].tls_val)
        stack = (SENDER_STACK if tid == SENDER else RECEIVER_STACK) + STACK_SIZE - 0x100
        self.mu.reg_write(arm.UC_ARM_REG_SP, stack)
        self.mu.reg_write(arm.UC_ARM_REG_LR, RETURN)
        for register, value in zip(ARG_REGISTERS, args):
            self.mu.reg_write(register, value)
        for index, value in enumerate(args[4:]):
            self.put(stack + index * 4, value)
        stopped = self._emulate(self.symbol(function))
        self.check('call_' + function, stopped == ('blocked_futex' if block else 'return'))
        return self.mu.reg_read(arm.UC_ARM_REG_R0)

    def _setup(self):
        self.call('fblock_init', FBLOCK, FBLOCK_SIZE)
        self.mu.mem_write(FIXTURE, b'USB route fixture\x00')
        self.call('list_ctor', CONTROL + 4)
        self.call('sem_ctor', CONTROL + 0xC, 0xCA, FIXTURE)
        self.call('sender_ctor', CONTROL + 0x58, 0xCA, QUEUE_ID_USB_CONTROL_LOCAL, 0)
        self.call('receiver_ctor', CONTROL + 0x70, 0xCA, QUEUE_ID_USB_CONTROL_LOCAL)
        self.call('fsm_ctor', CONTROL + 0xF4, FIXTURE, self.symbol('fsm_table'), CONTROL, 0xCA)
        self.call('fsm_init', CONTROL + 0xF4, 0)
        self.call('sender_ctor', WRAPPER + 8, 0xCA,
                  QUEUE_ID_USB_CONTROL_LOCAL, QUEUE_ID_USB_CONTROL_LOCAL)
        if self.register:
            result = self.call('osal_reg_sync_msg_queue_cb',
                               QUEUE_ID_USB_CONTROL_LOCAL, CONTROL + 0x84,
                               self.symbol('local_callback'), CONTROL)
            self.check('original_sync_registration', result == 0)
        self.call('main', CONTROL, tid=RECEIVER, block=True)

    def execute(self):
        try:
            self._load()
            self._setup()
            queue = lookup_local_queue(
                self.read, OSAL_BASE, QUEUE_ID_USB_CONTROL_LOCAL)['object']
            self.report['queue_object'] = queue
            registration = self.word(queue + 0x18)
            self.report['registration'] = registration
            self.report['fsm_state_before'] = self.word(CONTROL + 0x104)
            baseline_remaining = self.word(OSAL_BASE + FBLOCK_CONTROL_OFFSET + 0x14)
            baseline_heap = dict(self.heap.live)
            self.call('objmsg_ctor', MESSAGE, SETCONFIG_CATEGORY, SETCONFIG_MESSAGE_ID)
            self.call('param_ctor', PARAM, 0)
            self.call('add_param', MESSAGE, SETCONFIG_PARAM_ID, PARAM)
            self.call('param_dtor', PARAM)
            result = self.call('event_listener', WRAPPER, MESSAGE)
            self.report['send_return'] = result
            self.call('objmsg_dtor', MESSAGE)
            if self.register:
                self.check('original_sync_send_completed', result == 0)
                self.check('original_sync_bookkeeping_restored',
                           self.read(registration + 0xC, 2) == b'\x00\x01' and
                           self.word(registration + 0x10) ==
                           self.threads.threads[SENDER].tls_val and self.word(queue) == 0)
                self.check('receiver_woken_by_original_post',
                           self.threads.threads[RECEIVER].state == 'runnable')
                self.threads.restore_context(self.mu, RECEIVER)
                self.check('receiver_returns_to_wait',
                           self._emulate(self.mu.reg_read(arm.UC_ARM_REG_PC) |
                                         (self.mu.reg_read(arm.UC_ARM_REG_CPSR) & 0x20) >> 5)
                           == 'blocked_futex')
                trace = self.report['trace']
                self.check('original_process_receives_setconfig', any(
                    entry['function'] == 'process' and
                    entry['objmsg']['category'] == SETCONFIG_CATEGORY and
                    entry['objmsg']['message_id'] == SETCONFIG_MESSAGE_ID
                    for entry in trace))
                self.check('original_fsm_event_0x13', any(
                    entry['function'] == 'dispatch' and entry['args'][1] == 0x13
                    for entry in trace))
                self.check('original_fsm_executes_event_0x13', any(
                    entry['function'] == 'fsm_execute_main' and entry['args'][1] == 0x13
                    for entry in trace))
                handling = next((entry for entry in trace
                                 if entry['function'] == 'fsm_handle_event' and
                                 entry['args'][2] == 0x13), None)
                self.check('original_fsm_table_result_observed',
                           handling is not None and 'returned' in handling)
                self.report['fsm_handle_event_return'] = handling['returned']
                self.report['fsm_event_handled'] = handling['returned'] != 0xFFFFFFFF
                self.check('original_message_id_maps_to_event_0x13', any(
                    entry['function'] == 'event_map' and
                    entry['args'][1] == SETCONFIG_MESSAGE_ID and entry.get('returned') == 0x13
                    for entry in trace))
                self.check('original_typed_param_0x1006', any(
                    entry['function'] == 'get_int32' and
                    entry['args'][1] == SETCONFIG_PARAM_ID and entry.get('returned') == 0
                    for entry in trace))
                self.check('original_0x0a4002_notification', any(
                    entry['function'] == 'broadcast' and
                    entry['objmsg']['category'] == SETCONFIG_CATEGORY and
                    entry['objmsg']['message_id'] == 0x0A4002
                    for entry in trace))
                status = 'original_local_setconfig_delivered'
            else:
                self.check('missing_registration_is_not_success', result == 0xFFFFFC04)
                self.check('missing_registration_does_not_wake_receiver',
                           self.threads.threads[RECEIVER].state == 'blocked_futex')
                self.check('missing_registration_does_not_dispatch', not any(
                    entry['function'] in ('local_callback', 'preprocess', 'post', 'process', 'dispatch')
                    for entry in self.report['trace']))
                status = 'original_local_setconfig_no_registration'
            self.check('original_envelopes_released',
                       struct.unpack('<H', self.read(queue + 6, 2))[0] == 0)
            self.check('original_fblock_bytes_restored',
                       self.word(OSAL_BASE + FBLOCK_CONTROL_OFFSET + 0x14) == baseline_remaining)
            self.check('message_heap_ownership_balanced', self.heap.live == baseline_heap)
            self.check('message_mutex_lifetime_balanced', not self.mutexes)
            self.report.update(status=status, boundary=None,
                               fsm_state_after=self.word(CONTROL + 0x104),
                               queue_allocation_count=struct.unpack(
                                   '<H', self.read(queue + 6, 2))[0],
                               fblock_remaining_before=baseline_remaining,
                               fblock_remaining_after=self.word(
                                   OSAL_BASE + FBLOCK_CONTROL_OFFSET + 0x14))
        except RouteBoundary as error:
            self.report.update(status='boundary', boundary=error.details)
        except (ValueError, KeyError, OSError, unicorn.UcError) as error:
            self.report.update(status='failed', boundary={
                'reason': type(error).__name__, 'error': str(error)})
        self.report.update(instructions=self.instructions, heap_events=self.heap.events,
                           heap_live=self.heap.live, mutexes_live=self.mutexes,
                           pc=self.mu.reg_read(arm.UC_ARM_REG_PC),
                           arguments=[self.mu.reg_read(reg) for reg in ARG_REGISTERS])
        return self.report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-dir', required=True, type=Path)
    parser.add_argument('--instructions', type=int, default=100000)
    args = parser.parse_args(argv)
    output = args.report_dir.resolve()
    if not output.is_relative_to((PROJECT_ROOT / 'artifacts').resolve()):
        parser.error('Report directory must remain under artifacts')
    if args.instructions <= 0:
        parser.error('--instructions must be positive')
    output.mkdir(parents=True, exist_ok=False)
    reports = []
    for name, registered in (('setconfig', True), ('no-registration', False)):
        report = LocalSetconfigReplay(
            register=registered, instruction_budget=args.instructions).execute()
        reports.append(report)
        (output / (name + '.json')).write_text(
            json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(f"{name}: {report['status']} ({report['instructions']} instructions)")
        if report['boundary']:
            print(json.dumps(report['boundary']))
    expected = ('original_local_setconfig_delivered', 'original_local_setconfig_no_registration')
    return 0 if tuple(report['status'] for report in reports) == expected else 2


if __name__ == '__main__':
    raise SystemExit(main())
