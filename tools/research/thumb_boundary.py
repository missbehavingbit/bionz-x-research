"""Compare bounded E0467 firmware traces from Unicorn and QEMU."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import inspect
import json
import os
import re
import shutil
from pathlib import Path

import subprocess
import sys
import tempfile
import time

from project_paths import PROJECT_ROOT

WINDOWS = {'return': (0x6010DF34, 0x6010DF67), 'loader': (0x0402E790, 0x0402E7AB)}
CHECKPOINT_PC = WINDOWS['return'][0]
KERNEL_ENTRY_PC = 0x00108000
# Decompressed nflasha1 Image (E0545). These physical addresses run in SVC
# with the MMU off. A lower address above the image is a user virtual address.
KERNEL_IMAGE_BYTES = 3581952
USER_RETURN_MSR = 0x6010DF50
USER_RETURN_MOVS = 0x6010DF64
MAX_ROWS = 16
USER_LOADER_ROWS = 8
USER_LOADER_SPAN = 64
USER_STACK_BYTES = 224
REGISTERS = tuple(f'r{i}' for i in range(13)) + ('sp', 'lr', 'cpsr')
# Architectural CPSR bits a difference report can name. The low five bits are the mode.
CPSR_BITS = ((31, 'N'), (30, 'Z'), (29, 'C'), (28, 'V'), (27, 'Q'),
             (24, 'J'), (9, 'E'), (8, 'A'), (7, 'I'), (6, 'F'), (5, 'T'))


def identity(contract, target, boot_set):
    return {
        'target': target,
        'boot_set': boot_set,
        'kernel_sha256': hashlib.sha256(contract.raw_kernel).hexdigest(),
        'initrd_sha256': hashlib.sha256(contract.initrd).hexdigest(),
    }


def window_bounds(name, loader_entry=None):
    start, end = WINDOWS[name]
    if name == 'loader' and loader_entry is not None:
        return loader_entry, loader_entry + (end - start)
    return start, end


def observe_without_continue(pc, start, cpsr, user_only=None):
    """The restored snapshot is already stopped on the window's first instruction."""
    if user_only is None:
        user_only = start < 0x60000000
    if pc != start:
        return False
    if user_only and (cpsr & 0x1F) != 0x10:
        return False
    return True


def checkpoint_usable(record, identity, qemu_sha256, cmdline, pc=None):
    expected = CHECKPOINT_PC if pc is None else pc
    return (isinstance(record, dict)
            and record.get('identity') == identity
            and record.get('pc') == expected
            and record.get('qemu_sha256') == qemu_sha256
            and record.get('cmdline') == cmdline
            and record.get('snapshot') in ('vmstate.bin', 'vmstate.bin.gz'))


def parse_stop(value):
    """A probe stop: user-return, user-loader, kernel-entry, or pc:ADDRESS."""
    if value in ('user-return', 'user-loader'):
        return {'kind': 'user-return', 'name': value, 'user_only': True,
                'limit': USER_LOADER_ROWS if value == 'user-loader' else 1, 'mmu_off': False}
    if value == 'kernel-entry':
        return {'kind': 'pc', 'pc': KERNEL_ENTRY_PC, 'end': KERNEL_ENTRY_PC + 4,
                'name': 'kernel-entry', 'user_only': False, 'limit': 1, 'mmu_off': True}
    if value.startswith('pc:'):
        try:
            pc = int(value[3:], 0)
        except ValueError as error:
            raise argparse.ArgumentTypeError('Expected pc:ADDRESS') from error
        if not 0 <= pc <= 0xffffffff or pc & 1:
            raise argparse.ArgumentTypeError('Expected an even ARM address')
        if pc == CHECKPOINT_PC:
            start, end = WINDOWS['return']
            return {'kind': 'pc', 'pc': start, 'end': end, 'name': 'return',
                    'user_only': False, 'limit': MAX_ROWS, 'mmu_off': False}
        physical = KERNEL_ENTRY_PC <= pc < KERNEL_ENTRY_PC + KERNEL_IMAGE_BYTES
        return {'kind': 'pc', 'pc': pc, 'end': pc + 4, 'name': f'pc:0x{pc:08x}',
                'user_only': not physical and pc < 0x60000000,
                'limit': 1, 'mmu_off': physical}
    raise argparse.ArgumentTypeError('Expected user-return, user-loader, kernel-entry, or pc:ADDRESS')


def stop_from_window(window, loader_entry=None):
    """Keep the historical window names working as stops."""
    if window == 'return':
        return parse_stop(f'pc:{CHECKPOINT_PC:#x}')
    start, end = window_bounds('loader', loader_entry)
    return {'kind': 'pc', 'pc': start, 'end': end, 'name': 'loader',
            'user_only': True, 'limit': MAX_ROWS, 'mmu_off': False}


def stage_for_stop(stop):
    """Latest saved PC that has not already passed the stop."""
    pc = stop.get('pc')
    if stop['kind'] == 'pc' and pc < CHECKPOINT_PC:
        return 'kernel-entry'
    return 'return'


def stage_directory(checkpoint, stage):
    """The return snapshot keeps the original flat directory layout."""
    if stage == 'return':
        return checkpoint
    return checkpoint / stage


def cpsr_bit_names(left, right):
    """Name the architectural CPSR bits that differ, plus the mode field."""
    mask = (left ^ right) & 0xffffffff
    names = [name for bit, name in CPSR_BITS if mask & (1 << bit)]
    if (left & 0x1f) != (right & 0x1f):
        names.append('mode')
    return names


def walk_short_descriptor(vaddr, ttbr0, read_word):
    """Walk ARMv7 short-descriptor TTBR0 the same way as UnicornMachine."""
    if ttbr0 is None:
        return None
    l1_address = (ttbr0 & 0xFFFFC000) + ((vaddr >> 20) & 0xFFF) * 4
    l1 = read_word(l1_address)
    if l1 is None:
        return None
    if l1 & 2:
        return (l1 & 0xFFF00000) | (vaddr & 0x000FFFFF)
    if not (l1 & 1):
        return None
    l2 = read_word((l1 & 0xFFFFFC00) + ((vaddr >> 12) & 0xFF) * 4)
    if l2 is None or not (l2 & 2):
        return None
    return (l2 & 0xFFFFF000) | (vaddr & 0xFFF)


def physical_address(vaddr, ttbr0, read_word, mmu_off=False):
    """Where to read an instruction when a virtual read is unavailable."""
    if mmu_off:
        return vaddr
    if 0x60000000 <= vaddr < 0x80000000:
        return vaddr - 0x60000000
    return walk_short_descriptor(vaddr, ttbr0, read_word)


def read_user_sample(address, size, translate, read):
    """Read a bounded user virtual range via the first guest RAM bank."""
    if not 0x1000 <= address < address + size <= 0x60000000:
        raise ValueError('sample outside guest userspace')
    data = bytearray()
    while len(data) < size:
        virtual = address + len(data)
        length = min(size - len(data), 0x1000 - (virtual & 0xfff))
        physical = translate(virtual)
        if physical is None or not 0 <= physical < physical + length <= 0x40000000:
            raise ValueError(f'no first-bank RAM translation for {virtual:#x}')
        piece = read(physical, length)
        if len(piece) != length:
            raise ValueError(f'short physical read at {physical:#x}')
        data.extend(piece)
    return bytes(data)


def parse_xp_word(text):
    match = re.search(r'0x([0-9a-fA-F]{1,8})\b', text or '')
    if match is None or len(match.group(1)) > 8:
        return None
    return int(match.group(1), 16)


def parse_xp_bytes(text):
    values = [int(item, 16) for item in re.findall(r'0x([0-9a-fA-F]{2})\b', text or '')]
    return bytes(values) if values else None


def arm_user_address(value):
    try:
        address = int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError('Expected an ARM user-space address') from error
    if not 0x1000 <= address <= 0x5fffff00 or address & 1:
        raise argparse.ArgumentTypeError('Expected an even ARM user-space address')
    return address


def capture_unicorn(machine, loader_entry=None, stops=None):
    from unicorn import UC_HOOK_CODE, UcError, __version__
    from unicorn import arm_const

    registers = {name: getattr(arm_const, f'UC_ARM_REG_{name.upper()}')
                 for name in (*REGISTERS, 'spsr')}
    windows = {name: [] for name in WINDOWS}
    bounds = {name: window_bounds(name, loader_entry) for name in WINDOWS}

    def record(uc, pc, size, rows, start, user_only):
        if len(rows) >= MAX_ROWS or (not rows and user_only and pc != start and start is not None):
            return
        state = {name: uc.reg_read(registers[name]) for name in REGISTERS}
        mode = state['cpsr'] & 0x1f
        if user_only and mode != 0x10:
            return
        if mode not in (0x10, 0x1f):
            state['spsr'] = uc.reg_read(registers['spsr'])
        pa = machine._translate_virtual(pc)
        row = dict(pc=pc, offset=0 if start is None else pc - start, state=state, bytes=None,
                   page_walk_address=pa, decoded_size=size)
        if pa is not None:
            try:
                row['bytes'] = bytes(uc.mem_read(pa, 4)).hex()
            except UcError as error:
                row['read_error'] = str(error)
        rows.append(row)

    for name, (start, end) in bounds.items():
        rows = windows[name]
        user_only = start < 0x60000000

        def observe(uc, pc, size, _, rows=rows, start=start, user_only=user_only):
            if len(rows) >= MAX_ROWS or (not rows and pc != start):
                return
            record(uc, pc, size, rows, start, user_only)

        # Earlier machine hooks may service or redirect individual instructions.
        machine.uc.hook_add(UC_HOOK_CODE, observe, begin=start, end=end)
    for stop in stops or []:
        name = stop['name']
        if name in windows:
            continue
        rows = windows[name] = []
        if stop['kind'] == 'user-return':
            bounds[name] = [name]
            seen = {'done': False}

            def user_return(uc, pc, size, _, rows=rows, seen=seen, name=name,
                            limit=stop['limit']):
                if seen['done']:
                    return
                cpsr = uc.reg_read(registers['cpsr'])
                if (cpsr & 0x1f) != 0x10:
                    return
                if name == 'user-return':
                    seen['done'] = True
                    record(uc, pc, size, rows, None, True)
                    return
                entry = rows[0]['pc'] if rows else pc
                if not (cpsr & 0x20 and entry <= pc < entry + USER_LOADER_SPAN):
                    seen['done'] = True
                    return
                record(uc, pc, size, rows, entry, True)
                if len(rows) == 1:
                    sp = rows[0]['state']['sp']
                    try:
                        rows[0]['stack_bytes'] = read_user_sample(
                            sp, USER_STACK_BYTES, machine._translate_virtual, uc.mem_read).hex()
                    except (UcError, ValueError) as error:
                        rows[0]['stack_error'] = str(error)
                seen['done'] = len(rows) >= limit

            machine.uc.hook_add(UC_HOOK_CODE, user_return, begin=0x1000, end=0x5fffffff)
            continue
        bounds[name] = [stop['pc'], stop['end']]
        start = stop['pc']

        def one_pc(uc, pc, size, _, rows=rows, start=start, user_only=stop['user_only']):
            if rows:
                return
            record(uc, pc, size, rows, start, user_only)

        machine.uc.hook_add(UC_HOOK_CODE, one_pc, begin=start, end=start + 3)
    return {'unicorn_version': __version__, 'bounds': bounds, 'rows': windows}


def compare(unicorn, qemu, window):
    """Report the earliest difference in the observed prefix, with raw values."""
    for key in ('target', 'boot_set', 'kernel_sha256', 'initrd_sha256'):
        left = unicorn.get('identity', {}).get(key)
        right = qemu.get('identity', {}).get(key)
        if not left or left != right:
            return dict(status='incomparable', field=key, unicorn=left, qemu=right)
    u_bounds = unicorn['windows'].get('bounds', {}).get(window)
    q_bounds = qemu['windows'].get('bounds', {}).get(window)
    if u_bounds != q_bounds and window != 'user-return':
        return dict(status='incomparable', reason='different window bounds',
                    unicorn=u_bounds, qemu=q_bounds)
    left = unicorn['windows']['rows'].get(window, [])
    right = qemu['windows']['rows'].get(window, [])
    result = compare_rows(left, right, window, unicorn['stop'], qemu['stop'])
    if window == 'user-loader' and left and right:
        result['loader'] = compare_loader_samples(left, right)
    return result


def compare_loader_samples(left, right):
    """Keep raw CPU differences and separately compare the observed byte ranges."""
    matching = 0
    for u, q in zip(left, right):
        if not u.get('bytes') or u['offset'] != q['offset'] or u['bytes'] != q.get('bytes'):
            break
        matching += 1
    entries = {}
    stacks = []
    for name, rows in (('unicorn', left), ('qemu', right)):
        entry = rows[0]
        state = entry['state']
        data = bytes.fromhex(entry.get('stack_bytes', ''))
        stacks.append(data)
        entries[name] = dict(pc=entry['pc'], sp=state['sp'],
                             r1_from_sp=state['r1'] - state['sp'],
                             rows=len(rows), stack_bytes=len(data),
                             stack_error=entry.get('stack_error'))
    shared = min(map(len, stacks))
    first = next((offset for offset in range(shared)
                  if stacks[0][offset] != stacks[1][offset]), None)
    return dict(matching_instruction_samples=matching, entries=entries,
                compared_stack_bytes=shared, first_stack_difference=first)


def compare_rows(left, right, window, unicorn_stop, qemu_stop):
    for index, (u, q) in enumerate(zip(left, right)):
        if index and u['offset'] != q['offset']:
            return difference(index, 'instruction_path', u, q)
        if u['offset'] != q['offset'] or not u['bytes'] or not q['bytes']:
            return dict(status='incomparable', index=index, reason='missing aligned instruction observation')
        if u['bytes'] != q['bytes']:
            if index == 0 and window != 'user-return':
                return dict(status='incomparable', index=index, reason='entry instruction bytes differ',
                            unicorn=u, qemu=q)
            return annotate_cpsr(difference(index, 'instruction_bytes', u, q), u, q)
        fields = {key: [value, q['state'][key]] for key, value in u['state'].items()
                  if key in q['state'] and value != q['state'][key]}
        if fields:
            return annotate_cpsr(difference(index, 'cpu_state', u, q) | {'registers': fields}, u, q)
    if not left or not right:
        return dict(status='incomparable', reason='entry not observed',
                    missing=[name for name, rows in (('unicorn', left), ('qemu', right)) if not rows])
    shared = min(len(left), len(right))
    result = dict(status='matching_prefix', rows=shared,
                  observed_rows={'unicorn': len(left), 'qemu': len(right)},
                  first_unpaired={'unicorn': left[shared:shared + 1], 'qemu': right[shared:shared + 1]},
                  stops={'unicorn': unicorn_stop, 'qemu': qemu_stop})
    if shared and left[0].get('pc') != right[0].get('pc'):
        result['addresses'] = {'unicorn': left[0].get('pc'), 'qemu': right[0].get('pc')}
    return result


def difference(index, category, unicorn, qemu):
    return dict(status='difference', index=index, category=category, unicorn=unicorn, qemu=qemu)


def annotate_cpsr(result, unicorn, qemu):
    registers = result.get('registers', {})
    if 'cpsr' in registers:
        result['cpsr_bits'] = cpsr_bit_names(registers['cpsr'][0], registers['cpsr'][1])
    if unicorn.get('pc') != qemu.get('pc'):
        result['addresses'] = {'unicorn': unicorn.get('pc'), 'qemu': qemu.get('pc')}
    return result


# Executed in GDB's Python interpreter. Memory reads are guest virtual reads.
GDB_CAPTURE = """
import gdb
import json

gdb.execute('set pagination off')
gdb.execute('set confirm off')
gdb.execute('set remotetimeout 3')
gdb.execute('set auto-load off')
gdb.execute('set architecture arm')
gdb.execute('target remote ' + socket_path)
mask = gdb.execute('maintenance packet qqemu.sstep', to_string=True)
print('STEP_POLICY ' + json.dumps(mask), flush=True)
pc_now = int(gdb.parse_and_eval('$pc')) & 0xfffffffe
cpsr_now = int(gdb.parse_and_eval('$cpsr')) & 0xffffffff
already = pc_now == start and not (user_only and (cpsr_now & 0x1f) != 0x10)
if already:
    print('BOUNDARY_RESUME ' + json.dumps(dict(pc=pc_now)), flush=True)
else:
    bp = gdb.Breakpoint('*%#x' % start, type=gdb.BP_HARDWARE_BREAKPOINT)
    if user_only:
        bp.condition = '($cpsr & 0x1f) == 0x10'
    gdb.execute('continue')
    bp.delete()
# Unicorn's code hook records executed PCs inside the window and skips a
# call's body. Software breakpoints bring QEMU back to the next in-window
# instruction; a permanent departure ends the sample.
watched = []
address = start
while address < end:
    # Break only on instruction boundaries. A halfword software breakpoint
    # would overwrite the upper half of a 32-bit instruction.
    watched.append(gdb.Breakpoint('*%#x' % address))
    if not user_only:
        address += 4
        continue
    half = bytes(gdb.selected_inferior().read_memory(address, 2))
    value = half[0] | (half[1] << 8)
    address += 4 if value >= 0xE800 else 2
recorded = 0
while recorded < limit:
    pc = int(gdb.parse_and_eval('$pc')) & 0xfffffffe
    if pc < start or pc >= end:
        print('BOUNDARY_STOP ' + json.dumps(dict(reason='window_exit', pc=pc)), flush=True)
        break
    state = {name: int(gdb.parse_and_eval('$' + name)) & 0xffffffff for name in registers}
    if user_only and (state['cpsr'] & 0x1f) != 0x10:
        gdb.execute('stepi', to_string=True)
        continue
    if (state['cpsr'] & 0x1f) not in (0x10, 0x1f):
        spsr = gdb.parse_and_eval('$spsr')
        if spsr.type.code != gdb.TYPE_CODE_VOID:
            state['spsr'] = int(spsr) & 0xffffffff
    row = dict(pc=pc, offset=pc - start, state=state,
               thread=gdb.selected_thread().ptid, bytes=None)
    try:
        row['bytes'] = bytes(gdb.selected_inferior().read_memory(pc, 4)).hex()
    except gdb.MemoryError as error:
        row['read_error'] = str(error)
    print('BOUNDARY_ROW ' + json.dumps(row), flush=True)
    recorded += 1
    if recorded >= limit:
        print('BOUNDARY_STOP ' + json.dumps(dict(reason='sample_limit')), flush=True)
        break
    gdb.execute('stepi', to_string=True)
    landed = int(gdb.parse_and_eval('$pc')) & 0xfffffffe
    if landed < start or landed >= end:
        gdb.execute('continue')
else:
    print('BOUNDARY_STOP ' + json.dumps(dict(reason='sample_limit')), flush=True)
for item in watched:
    item.delete()
"""


# Stop once, before the requested window sample, so a later probe restores
# this CPU instead of booting the updater again.
GDB_REACH = """
import gdb
gdb.execute('set pagination off')
gdb.execute('set confirm off')
gdb.execute('set architecture arm')
gdb.execute('target remote ' + socket_path)
gdb.execute('break *%#x' % checkpoint_pc)
gdb.execute('continue')
pc = int(gdb.parse_and_eval('$pc')) & 0xfffffffe
print('CHECKPOINT_PC ' + str(pc), flush=True)
gdb.execute('disconnect')
"""


# The restored return snapshot is still in SVC. SPSR is not readable here;
# r1 at the MSR is the value written into SPSR. Continue until that value is
# USR, step through movs pc, lr, and record the landed instruction.
GDB_USER_RETURN = """
import gdb
import json

gdb.execute('set pagination off')
gdb.execute('set confirm off')
gdb.execute('set remotetimeout 3')
gdb.execute('set auto-load off')
gdb.execute('set architecture arm')
gdb.execute('target remote ' + socket_path)
mask = gdb.execute('maintenance packet qqemu.sstep', to_string=True)
print('STEP_POLICY ' + json.dumps(mask), flush=True)
msr = gdb.Breakpoint('*%#x' % msr_pc, type=gdb.BP_HARDWARE_BREAKPOINT)
msr.condition = '($r1 & 0x1f) == 0x10'
gdb.execute('continue')
msr.delete()
movs = gdb.Breakpoint('*%#x' % movs_pc, type=gdb.BP_HARDWARE_BREAKPOINT)
gdb.execute('continue')
movs.delete()
ttbr0 = None
found = []
try:
    for reg in gdb.selected_frame().architecture().registers():
        if 'ttbr0' not in reg.name.lower():
            continue
        value = gdb.parse_and_eval('$' + reg.name)
        found.append(dict(name=reg.name, text=str(value), type=str(value.type)))
except gdb.error as error:
    print('TTBR0_ERROR ' + json.dumps(str(error)), flush=True)
print('TTBR0_REGS ' + json.dumps(found), flush=True)
for item in found:
    token = item['text'].strip().split()[0].rstrip(',')
    if token in ('void', '<unavailable>'):
        continue
    ttbr0 = int(token, 16 if token.lower().startswith('0x') else 10) & 0xffffffff
    break
gdb.execute('stepi', to_string=True)
if not loader_probe:
    pc = int(gdb.parse_and_eval('$pc')) & 0xfffffffe
    state = {name: int(gdb.parse_and_eval('$' + name)) & 0xffffffff for name in registers}
    row = dict(pc=pc, offset=0, state=state, thread=gdb.selected_thread().ptid,
               bytes=None, ttbr0=ttbr0)
    try:
        row['bytes'] = bytes(gdb.selected_inferior().read_memory(pc, 4)).hex()
    except gdb.MemoryError as error:
        row['read_error'] = str(error)
    print('BOUNDARY_ROW ' + json.dumps(row), flush=True)
    print('BOUNDARY_STOP ' + json.dumps(dict(reason='user_return', pc=pc, ttbr0=ttbr0)), flush=True)
else:
    import sys
    import time
    sys.path.insert(0, project_root)
    from tools.research.qemu_boot_probe import Qmp

    def read_ram(address, size):
        if not 0 <= address < address + size <= 0x40000000:
            raise ValueError('physical sample outside first guest RAM bank')
        text = monitor.request('human-monitor-command',
                               {'command-line': 'xp /%dbx %#x' % (size, address)})
        data = parse_xp_bytes(text)
        if data is None or len(data) != size:
            raise ValueError('short physical memory response')
        return data

    def translate(address):
        return walk_short_descriptor(address, ttbr0,
                                     lambda pa: int.from_bytes(read_ram(pa, 4), 'little'))

    def read_state():
        pc = int(gdb.parse_and_eval('$pc')) & 0xfffffffe
        state = {name: int(gdb.parse_and_eval('$' + name)) & 0xffffffff for name in registers}
        return pc, state

    entry = int(gdb.parse_and_eval('$pc')) & 0xfffffffe
    reason = 'sample_limit'
    monitor = Qmp(monitor_path, time.monotonic() + 15)
    try:
        for index in range(limit):
            pc, state = read_state()
            if state['cpsr'] & 0x3f != 0x30 or not entry <= pc < entry + loader_span:
                reason = 'left_thumb_window'
                break
            row = dict(pc=pc, offset=pc-entry, state=state, thread=gdb.selected_thread().ptid,
                       bytes=None, ttbr0=ttbr0)
            if index == 0:
                try:
                    row['stack_bytes'] = read_user_sample(
                        state['sp'], stack_bytes, translate, read_ram).hex()
                except (ValueError, OSError, RuntimeError) as error:
                    row['stack_error'] = str(error)
            try:
                row['page_walk_address'] = translate(pc)
                if index == 0 and row['page_walk_address'] is None:
                    gdb.execute('stepi', to_string=True)
                    next_pc, next_state = read_state()
                    row['initial_fetch'] = dict(pc=next_pc, cpsr=next_state['cpsr'])
                    if next_pc != 0xffff000c or next_state['cpsr'] & 0x1f != 0x17:
                        raise ValueError('unexpected stop resolving initial instruction fetch')
                    retry = gdb.Breakpoint('*%#x' % entry, type=gdb.BP_HARDWARE_BREAKPOINT)
                    retry.condition = '($cpsr & 0x3f) == 0x30'
                    try:
                        gdb.execute('continue', to_string=True)
                    finally:
                        retry.delete()
                    retry_pc, retry_state = read_state()
                    if retry_pc != entry or retry_state != state:
                        raise ValueError('prefetch abort did not return to the same entry state')
                    row['page_walk_address'] = translate(pc)
                row['bytes'] = read_user_sample(pc, 4, translate, read_ram).hex()
            except (ValueError, OSError, RuntimeError) as error:
                row['read_error'] = str(error)
                reason = 'loader_read_error'
            print('BOUNDARY_ROW ' + json.dumps(row), flush=True)
            if not row['bytes'] or index + 1 == limit:
                break
            gdb.execute('stepi', to_string=True)
    finally:
        monitor.close()
    print('BOUNDARY_STOP ' + json.dumps(dict(reason=reason, pc=pc, ttbr0=ttbr0)), flush=True)
"""


def wait_migration(qmp, deadline):
    while True:
        status = qmp.request('query-migrate')
        if status.get('status') in ('completed', 'failed', 'cancelled'):
            return status
        if time.monotonic() >= deadline:
            raise TimeoutError('migration did not finish')
        time.sleep(0.05)


def migration_link(snapshot):
    """dd argv cannot contain spaces, so the stream goes through a /tmp symlink."""
    link = Path('/tmp') / f'thumbckpt-{os.getpid()}'
    if link.is_symlink() or link.exists():
        link.unlink()
    if not snapshot.exists():
        snapshot.touch()
    link.symlink_to(snapshot)
    return link


def compress_snapshot(raw_path, stage_dir):
    """Keep the migration stream gzipped. The raw file exists only while dd runs."""
    gzip_path = stage_dir / 'vmstate.bin.gz'
    with raw_path.open('rb') as source, gzip.open(gzip_path, 'wb', compresslevel=6) as destination:
        shutil.copyfileobj(source, destination)
    raw_path.unlink(missing_ok=True)
    return gzip_path


def inflate_snapshot(gzip_path, raw_path):
    with gzip.open(gzip_path, 'rb') as source, raw_path.open('wb') as destination:
        shutil.copyfileobj(source, destination)
    return raw_path


def read_gdb_rows(path, limit):
    rows = []
    notes = {}
    if not path.exists():
        return rows, notes
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        if line.startswith('BOUNDARY_ROW ') and len(rows) < limit:
            rows.append(json.loads(line.split(' ', 1)[1]))
        elif line.startswith('STEP_POLICY '):
            notes['step_policy'] = json.loads(line.split(' ', 1)[1])
        elif line.startswith('BOUNDARY_STOP '):
            notes['stop'] = json.loads(line.split(' ', 1)[1])
        elif line.startswith('TTBR0_ERROR '):
            notes['ttbr0_error'] = json.loads(line.split(' ', 1)[1])
    return rows, notes


def fill_instruction_bytes(rows, qmp, mmu_off):
    """Read instruction bytes from physical RAM when GDB could not."""
    def read_word(address):
        text = qmp.request('human-monitor-command',
                           {'command-line': f'xp /1wx 0x{address:x}'})
        return parse_xp_word(text)

    for row in rows:
        if row.get('bytes'):
            continue
        address = physical_address(row['pc'], row.get('ttbr0'), read_word, mmu_off)
        row['page_walk_address'] = address
        if address is None:
            continue
        text = qmp.request('human-monitor-command',
                           {'command-line': f'xp /4bx 0x{address:x}'})
        data = parse_xp_bytes(text)
        if data is not None and len(data) >= 4:
            row['bytes'] = data[:4].hex()
        else:
            row['physical_read'] = text


def qemu_command(qemu, kernel, initrd, cmdline, debug, monitor, incoming):
    command = [
        str(qemu), '-M', 'sony-a6400', '-smp', '1', '-m', '1G',
        '-display', 'none', '-serial', 'none', '-monitor', 'none', '-net', 'none',
        '-no-reboot', '-icount', 'shift=0,sleep=on',
        '-kernel', str(kernel), '-initrd', str(initrd), '-append', cmdline,
        '-chardev', f'socket,id=thumb_gdb,path={debug},server=on,wait=off',
        '-gdb', 'chardev:thumb_gdb',
        '-qmp', f'unix:{monitor},server=on,wait=off',
    ]
    if incoming:
        # -S during -incoming defer asks for a runstate change QEMU rejects.
        command[1:1] = ['-incoming', 'defer']
    else:
        command[1:1] = ['-S']
    return command


def qemu_capture(args, reference, output):
    from src.a6400.execution.machine_contract import UpdaterBootContract
    from src.a6400.execution.nflasha_boot_set import NflashaBootSet
    from tools.research.inventory import digest
    from tools.research.qemu_boot_probe import Qmp

    if os.name != 'posix':
        raise ValueError('Run QEMU capture inside WSL/Linux')
    expected = reference['identity'] if reference else None
    target, boot_label = 'ilce_6400', 'nflasha1'
    if expected and (expected['target'], expected['boot_set']) != (target, boot_label):
        raise ValueError('The QEMU updater experiment supports ilce_6400/nflasha1')
    boot_set = NflashaBootSet.from_evidence(PROJECT_ROOT, boot_label)
    contract = UpdaterBootContract(boot_set, cmdline_extra=args.cmdline_extra)
    contract.verify()
    actual = identity(contract, target, boot_label)
    report = dict(engine='qemu', identity=actual, windows={'rows': {}},
                  stop={'reason': 'identity_mismatch'})
    if expected and actual != expected:
        return report

    qemu = args.qemu.resolve(strict=True)
    gdb = args.gdb.resolve(strict=True)
    report.update(qemu_sha256=digest(qemu), gdb_sha256=digest(gdb),
                  tool_sha256=digest(Path(__file__)), timeout_seconds=args.seconds,
                  policy='Single-core sony-a6400 updater experiment, no disk or network. '
                         'QEMU kemco handoff and device model may differ from Unicorn policies.')
    stop = args.stop
    name = stop['name']
    if stop['kind'] == 'user-return':
        report['windows']['bounds'] = {name: [name]}
    else:
        report['windows']['bounds'] = {name: [stop['pc'], stop['end']]}
    report['stop_spec'] = name
    with tempfile.TemporaryDirectory(prefix='thumb-', dir='/tmp') as sockets:
        kernel, initrd = Path(sockets) / 'kernel.img', Path(sockets) / 'initrd.img'
        kernel.write_bytes(contract.raw_kernel)
        initrd.write_bytes(contract.initrd)
        debug, monitor = Path(sockets) / 'gdb', Path(sockets) / 'qmp'
        checkpoint = None
        stage_dir = None
        saved = None
        stage = stage_for_stop(stop)
        stage_pc = KERNEL_ENTRY_PC if stage == 'kernel-entry' else CHECKPOINT_PC
        if args.checkpoint:
            checkpoint = args.checkpoint.resolve()
            artifacts = (PROJECT_ROOT / 'artifacts').resolve()
            if not checkpoint.is_relative_to(artifacts):
                raise ValueError('checkpoint must be under artifacts')
            checkpoint.mkdir(parents=True, exist_ok=True)
            stage_dir = stage_directory(checkpoint, stage)
            meta_path = stage_dir / 'checkpoint.json'
            if meta_path.exists():
                saved = json.loads(meta_path.read_text(encoding='utf-8'))
                if not checkpoint_usable(saved, actual, report['qemu_sha256'],
                                         contract.sony_cmdline, stage_pc):
                    raise ValueError('checkpoint does not match this kernel, cmdline, or QEMU binary')
                if not (stage_dir / saved.get('snapshot', '')).is_file():
                    raise ValueError('checkpoint snapshot is missing')
        incoming = saved is not None
        report['checkpoint_stage'] = stage if checkpoint else None
        command = qemu_command(qemu, kernel, initrd, contract.sony_cmdline,
                               debug, monitor, incoming)
        debug_command = [str(gdb), '-nx', '-batch']
        if args.gdb_data_directory:
            debug_command += ['--data-directory', str(args.gdb_data_directory.resolve(strict=True))]
        report.update(command=command, resumed_from_checkpoint=incoming)
        deadline = time.monotonic() + args.seconds

        def write_gdb(path, source, values):
            program = '\n'.join(f'{key} = {value!r}' for key, value in values.items()) + source
            path.write_text(f'python exec({program!r})\n', encoding='utf-8')

        def run_gdb(path, log_path):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('QEMU capture deadline expired')
            with log_path.open('wb') as gdb_log:
                result = subprocess.run(debug_command + ['-x', str(path)], stdin=subprocess.DEVNULL,
                                        stdout=gdb_log, stderr=subprocess.STDOUT,
                                        env=dict(os.environ, DEBUGINFOD_URLS=''), timeout=remaining)
            return result.returncode

        def migrate(uri, qmp_deadline):
            raw = Path('/tmp') / f'thumbckpt-{os.getpid()}.bin'
            link = None
            if incoming and (stage_dir / 'vmstate.bin.gz').is_file():
                inflate_snapshot(stage_dir / 'vmstate.bin.gz', raw)
                stream = raw
            elif incoming:
                link = migration_link(stage_dir / saved['snapshot'])
                stream = link
            else:
                raw.touch()
                stream = raw
            status = None
            try:
                qmp = Qmp(monitor, qmp_deadline)
                try:
                    qmp.request('migrate' if not incoming else 'migrate-incoming',
                                {'uri': f'{uri}{stream} bs=1048576'})
                    status = wait_migration(qmp, qmp_deadline)
                finally:
                    qmp.close()
            finally:
                if link is not None:
                    link.unlink(missing_ok=True)
                elif incoming or status is None or status.get('status') != 'completed':
                    raw.unlink(missing_ok=True)
            if status.get('status') != 'completed':
                raise RuntimeError(f'migration {status.get("status")}: {status.get("error-desc")}')
            if not incoming:
                compress_snapshot(raw, stage_dir)
            return status

        with (output / 'qemu.log').open('xb') as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT)
            try:
                while not debug.exists() or not monitor.exists():
                    if process.poll() is not None:
                        raise RuntimeError(f'QEMU exited during startup ({process.returncode})')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('QEMU debug sockets did not become ready')
                    time.sleep(0.05)
                if incoming:
                    # The snapshot is already stopped. A further stop would ask
                    # for debug -> paused, which this QEMU runstate table rejects.
                    report['migration'] = migrate('exec:dd if=', deadline)
                elif checkpoint:
                    reach = output / 'reach.gdb'
                    write_gdb(reach, GDB_REACH, dict(socket_path=str(debug),
                                                     checkpoint_pc=stage_pc))
                    run_gdb(reach, output / 'reach.log')
                    reached = None
                    for line in (output / 'reach.log').read_text(encoding='utf-8', errors='replace').splitlines():
                        if line.startswith('CHECKPOINT_PC '):
                            reached = int(line.split()[1])
                    if reached != stage_pc:
                        raise RuntimeError(f'checkpoint stop was {reached}, not {stage_pc:#x}')
                    held = Qmp(monitor, deadline)
                    try:
                        held.request('stop')
                    finally:
                        held.close()
                    stage_dir.mkdir(parents=True, exist_ok=True)
                    report['migration'] = migrate('exec:dd of=', deadline)
                    (stage_dir / 'checkpoint.json').write_text(json.dumps(dict(
                        identity=actual, pc=stage_pc, qemu_sha256=report['qemu_sha256'],
                        cmdline=contract.sony_cmdline, snapshot='vmstate.bin.gz',
                        stage=stage), indent=2) + '\n', encoding='utf-8')
                    report['checkpoint_created'] = str(stage_dir)
                script = output / 'capture.gdb'
                if stop['kind'] == 'user-return':
                    source = GDB_USER_RETURN
                    if name == 'user-loader':
                        source = ('\nimport re\n' + inspect.getsource(parse_xp_bytes)
                                  + '\n' + inspect.getsource(walk_short_descriptor)
                                  + '\n' + inspect.getsource(read_user_sample) + source)
                    write_gdb(script, source, dict(
                        socket_path=str(debug), msr_pc=USER_RETURN_MSR,
                        movs_pc=USER_RETURN_MOVS, registers=REGISTERS,
                        loader_probe=name == 'user-loader', limit=stop['limit'],
                        loader_span=USER_LOADER_SPAN, stack_bytes=USER_STACK_BYTES,
                        project_root=str(PROJECT_ROOT), monitor_path=str(monitor)))
                else:
                    write_gdb(script, GDB_CAPTURE, dict(
                        socket_path=str(debug), start=stop['pc'], end=stop['end'],
                        limit=stop['limit'], registers=REGISTERS, user_only=stop['user_only']))
                report['debug_command'] = debug_command + ['-x', str(script)]
                report['stop'] = dict(reason='gdb_exit', returncode=run_gdb(script, output / 'gdb.log'))
                rows, notes = read_gdb_rows(output / 'gdb.log', stop.get('limit', MAX_ROWS))
                report['windows']['rows'][name] = rows
                report.update(notes)
                if (name != 'user-loader' and rows
                        and any(not row.get('bytes') for row in rows) and monitor.exists()):
                    try:
                        held = Qmp(monitor, time.monotonic() + 5)
                        try:
                            try:
                                held.request('stop')
                            except RuntimeError:
                                pass
                            fill_instruction_bytes(rows, held, stop.get('mmu_off', False))
                        finally:
                            held.close()
                    except (OSError, RuntimeError, TimeoutError) as error:
                        report['physical_read_error'] = str(error)
            except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as error:
                report['stop'] = dict(reason=type(error).__name__, detail=str(error))
            finally:
                try:
                    if process.poll() is None and monitor.exists():
                        qmp = Qmp(monitor, time.monotonic() + 5)
                        try:
                            qmp.request('stop')
                            report['final_registers'] = qmp.request(
                                'human-monitor-command', {'command-line': 'info registers -a'})
                            qmp.request('quit')
                            report['qmp_shutdown'] = process.wait(timeout=5) == 0
                        finally:
                            qmp.close()
                except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
                    report['shutdown_error'] = str(error)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    report['qemu_returncode'] = process.returncode

    if name not in report['windows']['rows']:
        rows, notes = read_gdb_rows(output / 'gdb.log', stop.get('limit', MAX_ROWS))
        report['windows']['rows'][name] = rows
        report.update(notes)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('unicorn_report', type=Path, nargs='?')
    parser.add_argument('--window', choices=WINDOWS,
                        help='historical return or loader window; use --stop for a named probe')
    parser.add_argument('--stop', type=parse_stop,
                        help='user-return, user-loader, kernel-entry, or pc:ADDRESS')
    parser.add_argument('--loader-entry', type=arm_user_address, help='override the historical loader VA')
    parser.add_argument('--qemu-report', type=Path, help='compare an existing QEMU receipt')
    parser.add_argument('--qemu', type=Path, help='patched qemu-system-arm binary')
    parser.add_argument('--gdb', type=Path, help='ARM-capable GDB with Python support')
    parser.add_argument('--gdb-data-directory', type=Path, help='data directory for a staged GDB')
    parser.add_argument('--checkpoint', type=Path,
                        help='save or restore a stopped snapshot; kernel-entry is a second stage')
    parser.add_argument('--seconds', type=int, default=45)
    parser.add_argument('--cmdline-extra', default='init=/bin/ash')
    parser.add_argument('--output', required=True, type=Path, help='new directory under artifacts')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 300:
        parser.error('--seconds must be between 1 and 300')
    if args.qemu_report and (args.qemu or args.gdb or args.gdb_data_directory):
        parser.error('choose --qemu-report or --qemu and --gdb')
    if not args.qemu_report and not (args.qemu and args.gdb):
        parser.error('supply --qemu and --gdb, or --qemu-report')
    if args.qemu_report and not args.unicorn_report:
        parser.error('--qemu-report requires a Unicorn report')
    if args.window and args.stop:
        parser.error('choose --stop or --window')
    output = args.output.resolve()
    if not output.is_relative_to((PROJECT_ROOT / 'artifacts').resolve()):
        parser.error('--output must be under artifacts')
    try:
        reference = None
        if args.unicorn_report:
            reference = json.loads(args.unicorn_report.read_text(encoding='utf-8'))['thumb_boundary']
            if args.loader_entry is None and args.stop is None and (args.window or 'loader') == 'loader':
                args.loader_entry = reference['windows']['bounds']['loader'][0]
        if args.stop is None:
            args.stop = stop_from_window(args.window or 'loader', args.loader_entry)
        output.mkdir(parents=True, exist_ok=False)
        if args.qemu_report:
            qemu = json.loads(args.qemu_report.read_text(encoding='utf-8'))
        else:
            qemu = qemu_capture(args, reference, output)
            (output / 'qemu.json').write_text(json.dumps(qemu, indent=2) + '\n', encoding='utf-8')
        window = args.stop['name']
        if reference is None:
            print(json.dumps(qemu['stop'], indent=2))
            rows = qemu['windows']['rows'].get(window, [])
            if window == 'user-loader':
                return 0 if (rows and rows[0].get('stack_bytes')
                             and qemu['stop']['reason'] in ('sample_limit', 'left_thumb_window')) else 2
            return 0 if rows else 2
        report = compare(reference, qemu, window)
    except (KeyError, ValueError, OSError, RuntimeError) as error:
        parser.exit(2, f'{error}\n')
    report.update(window=window, unicorn_stop=reference['stop'], qemu_stop=qemu['stop'])
    formatted = json.dumps(report, indent=2)
    (output / 'comparison.json').write_text(formatted + '\n', encoding='utf-8')
    print(formatted)
    return 2 if report['status'] == 'incomparable' else 0


if __name__ == '__main__':
    sys.exit(main())
