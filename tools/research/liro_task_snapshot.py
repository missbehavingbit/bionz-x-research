"""Read-only LIRO task snapshots for the hash-qualified E0524 captured kernel.

Offsets come from original ref_tsk/set_tsk_state/task-helper instructions and
the captured kernel's get_wchan/unwind_frame, not from another camera body.
"""
from collections.abc import Callable
import re
import struct

KERNEL_SHA256 = 'd7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618'
USR_SHA256 = '70d2613fcd24b13cfc4d75e98f435171fdfc50a6bc64d408e2df42ffb8197064'
LIRO_SHA256 = 'ca5fa9ecc990c2ceda755887563dcd2291fb49f56a4260d895818e7f6782f535'
TSKS_BSS_OFFSET = 0x780
INHS_BSS_OFFSET = 0x140


def task_table_address(transcript: str) -> int:
    matches = re.findall(r'^__LIRO_BSS__:(0x[0-9a-fA-F]{8,16})\r?$',
                         transcript, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError('Expected one current-run LIRO .bss section observation')
    address = int(matches[0], 16) + TSKS_BSS_OFFSET
    if not 0x5F000000 <= address < 0x60000000 or address % 4:
        raise ValueError('LIRO task table is outside this kernel module namespace')
    return address


def unwind_frames(stack: bytes, base: int, fp: int, sp: int, pc: int) -> dict:
    if len(stack) != 8192 or base % 8192:
        raise ValueError('Expected one captured 8 KiB kernel thread stack')
    frames = []
    for _ in range(16):
        frames.append(dict(fp=fp, sp=sp, pc=pc))
        if fp % 4 or fp < sp + 12 or fp + 4 >= base + len(stack) or fp - 12 < base:
            return dict(frames=frames, stop='frame_pointer_outside_confirmed_stack')
        next_fp, next_sp, next_pc = struct.unpack_from('<3I', stack, fp - base - 12)
        if next_sp < fp + 4 or next_sp >= base + len(stack):
            return dict(frames=frames, stop='caller_stack_pointer_outside_confirmed_frame')
        fp, sp, pc = next_fp, next_sp, next_pc
    return dict(frames=frames, stop='sixteen_frame_bound')


def _qualified_reader(read, table, kernel_sha256, usr_sha256):
    if kernel_sha256 != KERNEL_SHA256 or usr_sha256 != USR_SHA256:
        raise ValueError('Unsupported kernel/usr identity for the captured LIRO task layout')
    if not 0x5F000000 <= table <= 0x60000000 - 2040 or table % 4:
        raise ValueError('LIRO table is outside the qualified module namespace')

    def checked(address, size):
        if address % 4 or size % 4 or not 0x5F000000 <= address < address + size <= 0xB0000000:
            raise ValueError('LIRO object is outside the declared kernel RAM namespace')
        data = read(address, size)
        if len(data) != size:
            raise ValueError('LIRO memory observation is truncated')
        return data
    return checked


def snapshot_tasks(read: Callable[[int, int], bytes], table: int, task_ids: tuple[int, ...],
                   *, kernel_sha256: str, usr_sha256: str) -> list[dict]:
    checked = _qualified_reader(read, table, kernel_sha256, usr_sha256)
    if not 1 <= len(task_ids) <= 8 or len(set(task_ids)) != len(task_ids):
        raise ValueError('Select one to eight distinct LIRO task IDs')
    if any(type(task_id) is not int or not 1 <= task_id <= 255 for task_id in task_ids):
        raise ValueError('LIRO task IDs must be in 1..255')
    result = []
    for task_id in task_ids:
        lock, control = struct.unpack('<2I', checked(table + (task_id - 1) * 8, 8))
        if not control:
            raise ValueError(f'LIRO task {task_id} has no registered control block')
        words = struct.unpack('<40I', checked(control, 160))
        if words[0x18 // 4] != task_id:
            raise ValueError(f'LIRO task {task_id} control-block identity mismatch')
        record = dict(task_id=task_id, table_lock_word=lock, control_address=control,
                      state_bits=words[2], wait_kind=words[3], wait_id=words[4],
                      linux_task=words[5], priority=words[7], pending_activations=words[8],
                      entry=words[0x4C // 4], entry_argument=words[0x48 // 4],
                      control_block_words=list(words),
                      dormant=bool(words[2] & 0x10),
                      dormant_limit='Dormant alone does not distinguish an unstarted task from a returned entry.')
        linux_task = record['linux_task']
        state, _, stack_base = struct.unpack('<3I', checked(linux_task, 12))
        record['linux_state'] = state
        if state == 0:
            record['stack_observation'] = 'not_unwound_runnable_or_running_task'
        else:
            if stack_base % 8192:
                raise ValueError(f'LIRO task {task_id} has an unaligned kernel stack')
            fp, sp, pc = struct.unpack('<3I', checked(stack_base + 0x38, 12))
            stack = checked(stack_base, 8192)
            record.update(stack_base=stack_base,
                          stack_observation=unwind_frames(stack, stack_base, fp, sp, pc))
        result.append(record)
    return result


def snapshot_interrupts(read: Callable[[int, int], bytes], table: int, irq_ids: tuple[int, ...],
                        *, kernel_sha256: str, usr_sha256: str) -> list[dict]:
    """Observe def_inh's registered records, without raising an interrupt."""
    checked = _qualified_reader(read, table, kernel_sha256, usr_sha256)
    if (not 1 <= len(irq_ids) <= 8 or len(set(irq_ids)) != len(irq_ids)
            or any(type(irq) is not int or not 0 <= irq < 256 for irq in irq_ids)):
        raise ValueError('Select one to eight distinct IRQs in 0..255')
    inhs = table - TSKS_BSS_OFFSET + INHS_BSS_OFFSET
    result = []
    for irq in irq_ids:
        control = struct.unpack('<I', checked(inhs + irq * 4, 4))[0]
        if not control:
            raise ValueError(f'LIRO IRQ {irq} has no registered handler record')
        data = checked(control, 24)
        registered, wrapper, handler = struct.unpack_from('<3I', data)
        if registered != irq or handler == 0:
            raise ValueError(f'LIRO IRQ {irq} handler-record identity mismatch')
        record = dict(irq=irq, control_address=control, wrapper=wrapper,
                      handler=handler, requested_cpu=data[12], thumb=bool(handler & 1),
                      handler_record=data.hex())
        result.append(record)
    return result


def decode_gic_trace(text: str, irq_ids: tuple[int, ...]) -> dict[int, dict]:
    """Derive observed enable transitions and input assertions, never read MMIO."""
    states = {irq: dict(enabled=None, enable_transitions=0, input_assertions=0) for irq in irq_ids}
    for line in text.splitlines():
        event = re.fullmatch(r'gic_(enable|disable)_irq irq (\d+) (?:enabled|disabled)', line)
        if event and int(event[2]) in states:
            row = states[int(event[2])]
            row['enabled'] = event[1] == 'enable'
            row['enable_transitions'] += 1
        event = re.fullmatch(r'gic_set_irq irq (\d+) level ([01]) cpumask 0x[0-9a-f]+ target 0x[0-9a-f]+', line)
        if event and int(event[1]) in states and event[2] == '1':
            states[int(event[1])]['input_assertions'] += 1
    if any(row['enabled'] is None for row in states.values()):
        raise ValueError('Selected IRQ enable state was not observed in the native GIC trace')
    return states
