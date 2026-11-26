"""Passive, identity-checked allocation ownership diagnostics for InterpreterProbe."""
import hashlib
import io
import struct
from collections import Counter

import unicorn
from elftools.elf.elffile import ELFFile
from unicorn import arm_const as arm

from src.a6400.execution.interpreter_probe import module_mapping_entries, resumed_module_base
from tools.research.stella_allocator_probe import LIBC_SHA256, LIBOBJ_SHA256
from tools.research.startup_trace import read_va


PUBLIC_ALLOCATORS = ('malloc', 'free', 'calloc', 'realloc', 'memalign',
                     'valloc', 'pvalloc', 'posix_memalign')
REGISTERS = ('r0', 'r1', 'r2', 'r3', 'r4', 'r5', 'r6', 'r7', 'r8',
             'r9', 'r10', 'r11', 'r12', 'sp', 'lr', 'pc', 'cpsr')


class HeapOwnershipTrace:
    def __init__(self, probe, limit=200000):
        if limit <= 0:
            raise ValueError('Heap trace event limit must be positive')
        self.probe, self.mu, self.limit = probe, probe.mu, limit
        self.pending = {}
        self.live = {}
        self.retired = {}
        self.return_hooks = set()
        self.watched_pages = {}
        self.bound = {}
        self.counts = Counter()
        self.report = {
            'observe_only': True, 'status': 'observing', 'event_limit': limit,
            'start_instruction': probe.instructions,
            'ownership_before_start': 'not reconstructed',
            'modules': {}, 'events': [], 'counts': self.counts,
            'live': self.live,
        }
        probe.report['heap_ownership_trace'] = self.report

    def install(self):
        original = self.probe._arm_relocated_libc_repair

        def mapped(event):
            original(event)
            self.bind_loaded()

        self.probe._arm_relocated_libc_repair = mapped
        self.bind_loaded()
        return self

    def _stop(self, reason, **details):
        if self.report['status'] != 'observing':
            return
        boundary = {'reason': reason, 'instruction': self.probe.instructions,
                    'tid': self.probe.threads.current_tid,
                    'registers': self._registers(),
                    'pending': {tid: list(stack) for tid, stack in self.pending.items()
                                if stack}, **details}
        self.report.update(status='boundary', first_divergence=boundary)
        self.probe.report.update(status='heap_ownership_boundary', boundary=boundary)
        self.mu.emu_stop()

    def _registers(self):
        return {name: self.mu.reg_read(getattr(arm, 'UC_ARM_REG_' + name.upper()))
                for name in REGISTERS}

    def _record(self, operation, **details):
        if self.report['status'] != 'observing':
            return None
        if len(self.report['events']) >= self.limit:
            self._stop('ownership_event_limit')
            return None
        event = {'id': len(self.report['events']), 'op': operation,
                 'instruction': self.probe.instructions,
                 'tid': self.probe.threads.current_tid, **details}
        self.report['events'].append(event)
        self.counts[operation] += 1
        return event

    def bind_loaded(self):
        for module, expected in (('libc-2.11.2.so', LIBC_SHA256),
                                 ('libObj.so', LIBOBJ_SHA256)):
            if module in self.bound:
                continue
            entries = module_mapping_entries(module, self.probe.mmap.mappings)
            if not entries:
                continue
            paths = {entry['path'] for entry in entries}
            if len(paths) != 1:
                self._stop('ambiguous_allocator_module', module=module)
                return
            path = next(iter(paths))
            raw = self.probe.filesystem.cache.get(path)
            if raw is None or hashlib.sha256(raw).hexdigest() != expected:
                self._stop('allocator_source_identity_mismatch', module=module, path=path)
                return
            elf = ELFFile(io.BytesIO(raw))
            segments = [segment for segment in elf.iter_segments()
                        if segment['p_type'] == 'PT_LOAD']
            resolution = resumed_module_base(
                module, entries,
                [{'file_offset': segment['p_offset'], 'vaddr': segment['p_vaddr']}
                 for segment in segments])
            base = resolution['base']
            points = {}
            if module == 'libc-2.11.2.so':
                symbols = elf.get_section_by_name('.dynsym')
                points = {symbol.name: symbol['st_value'] & ~1
                          for symbol in symbols.iter_symbols()
                          if symbol.name in PUBLIC_ALLOCATORS}
                if set(points) != set(PUBLIC_ALLOCATORS):
                    self._stop('allocator_symbols_incomplete', symbols=sorted(points))
                    return
                points.update(_int_malloc=0x59298, _int_free=0x588FC,
                              allocator_assert=0x57AE0)
            else:
                points = {'stella_allocate': 0x79E8C2,
                          'stella_allocate_throw': 0x79E902,
                          'heap_allocate': 0x7ACBBA,
                          'mutex_create': 0x7AF46C,
                          'mutex_construct': 0x7AF440,
                          'log_construct': 0x7A750A,
                          'stella_abort': 0x64142A}
            for name, offset in points.items():
                source = read_va(elf, raw, offset, 8)
                if source is None or bytes(self.mu.mem_read(base + offset, 8)) != source:
                    self._stop('allocator_loaded_preimage_mismatch',
                               module=module, offset=offset)
                    return
            self.bound[module] = base
            self.report['modules'][module] = {
                'path': path, 'sha256': expected, 'base': base,
                'points': {name: base + offset for name, offset in points.items()},
            }
            for name, offset in points.items():
                if module == 'libc-2.11.2.so' and name != 'allocator_assert':
                    callback = self._entry_hook(name)
                else:
                    callback = self._point_hook(name)
                self.mu.hook_add(unicorn.UC_HOOK_CODE, callback,
                                 begin=base + offset, end=base + offset)

    def _point_hook(self, name):
        def point(uc, address, size, data):
            event = self._record(name, registers=self._registers())
            if name in ('allocator_assert', 'stella_abort'):
                sp = uc.reg_read(arm.UC_ARM_REG_SP)
                try:
                    stack = bytes(uc.mem_read(sp, 256)).hex()
                except unicorn.UcError as error:
                    self._stop('abort_stack_unreadable', error=str(error), event=event)
                    return
                self._stop('original_' + name, event=event, stack_hex=stack)
        return point

    def _entry_hook(self, name):
        def enter(uc, address, size, data):
            if self.report['status'] != 'observing':
                return
            try:
                self.enter(name)
            except unicorn.UcError as error:
                self._stop('allocator_entry_read_fault', function=name, error=str(error))
        return enter

    def _header(self, pointer):
        previous, raw_size = struct.unpack('<II', self.mu.mem_read(pointer - 8, 8))
        size = raw_size & ~7
        return {'pointer': pointer, 'chunk': pointer - 8,
                'previous_size': previous, 'raw_size': raw_size,
                'size': size, 'usable': size - (8 if raw_size & 2 else 4)}

    def enter(self, name):
        registers = self._registers()
        tid = self.probe.threads.current_tid
        event = self._record(name + '_enter', registers=registers)
        if event is None:
            return
        frame = {'function': name, 'call': event['id'], 'registers': registers}
        stack = self.pending.setdefault(tid, [])
        frame['nested_public'] = any(row['function'] in PUBLIC_ALLOCATORS for row in stack)
        if name == '_int_malloc':
            frame.update(arena=registers['r0'], requested=registers['r1'])
        elif name == '_int_free':
            event['header'] = self._header(registers['r1'] + 8)
        elif name in ('free', 'realloc') and registers['r0']:
            event['header'] = self._header(registers['r0'])
            if name == 'free':
                pointer = registers['r0']
                owner = self.live.pop(pointer, None)
                event['previous_owner'] = owner
                event['unobserved_owner'] = owner is None
                if pointer in self.retired:
                    self._stop('repeated_free_without_observed_reallocation',
                               call=event, previous_free=self.retired[pointer])
                    return
                self.retired[pointer] = {**event['header'], 'free_call': event['id'],
                                         'previous_owner': owner}
                self._watch(self.retired[pointer])
        stack.append(frame)
        address = registers['lr'] & ~1
        if address not in self.return_hooks:
            if len(self.return_hooks) >= 4096:
                self._stop('allocator_return_hook_limit')
                return
            self.return_hooks.add(address)
            self.mu.hook_add(unicorn.UC_HOOK_CODE, self._return_hook,
                             begin=address, end=address)

    def _return_hook(self, uc, address, size, data):
        if self.report['status'] != 'observing':
            return
        tid = self.probe.threads.current_tid
        stack = self.pending.get(tid, [])
        if not stack:
            return
        while stack and self.report['status'] == 'observing':
            frame = stack[-1]
            entry = frame['registers']
            if address != entry['lr'] & ~1 or uc.reg_read(arm.UC_ARM_REG_SP) != entry['sp']:
                return
            if bool(uc.reg_read(arm.UC_ARM_REG_CPSR) & 0x20) != bool(entry['lr'] & 1):
                self._stop('allocator_return_mode_mismatch', frame=frame)
                return
            stack.pop()
            try:
                self.returned(frame)
            except unicorn.UcError as error:
                self._stop('allocator_return_read_fault', frame=frame, error=str(error))

    def returned(self, frame):
        name, entry = frame['function'], frame['registers']
        result = self.mu.reg_read(arm.UC_ARM_REG_R0)
        event = self._record(name + '_return', call=frame['call'], result=result,
                             nested_public=frame['nested_public'],
                             registers=self._registers())
        if event is None or name in ('free', '_int_free'):
            return
        pointer = result
        if name == 'posix_memalign':
            pointer = struct.unpack('<I', self.mu.mem_read(entry['r0'], 4))[0] if result == 0 else 0
        if not pointer:
            return
        requested = (frame['requested'] if name == '_int_malloc' else
                     entry['r0'] * entry['r1'] if name == 'calloc' else
                     entry['r1'] if name in ('realloc', 'memalign') else
                     entry['r2'] if name == 'posix_memalign' else entry['r0'])
        header = self._header(pointer)
        event.update(requested=requested, header=header)
        if pointer & 7 or header['size'] < 0x10 or header['size'] & 7 or requested > header['usable']:
            self._stop('invalid_allocation_result', allocation=event)
            return
        if name == '_int_malloc':
            event['arena'] = frame['arena']
            membership = self._membership(frame['arena'], header['chunk'])
            event['free_membership'] = membership
            if membership:
                self._stop('allocation_returned_while_still_free', allocation=event,
                           entry_call=self.report['events'][frame['call']])
            return
        if frame['nested_public']:
            return
        if name == 'realloc':
            self.live.pop(entry['r0'], None)
        for owner in self.live.values():
            if pointer < owner['pointer'] + owner['usable'] and owner['pointer'] < pointer + header['usable']:
                self._stop('overlapping_live_allocations', allocation=event, previous_owner=owner)
                return
        for old_pointer, old in list(self.retired.items()):
            if header['chunk'] < old['chunk'] + old['size'] and old['chunk'] < header['chunk'] + header['size']:
                del self.retired[old_pointer]
        self.live[pointer] = {**header, 'allocation_call': frame['call'],
                              'allocation_return': event['id'], 'requested': requested,
                              'tid': self.probe.threads.current_tid, 'caller': entry['lr']}

    def _membership(self, arena, chunk):
        raw = bytes(self.mu.mem_read(arena, 0x438))
        roots = [('fastbin', index, struct.unpack_from('<I', raw, 8 + index * 4)[0], None)
                 for index in range(10)]
        roots += [('bin', index, struct.unpack_from('<I', raw, 0x38 + (index - 1) * 8)[0],
                   arena + 0x30 + (index - 1) * 8) for index in range(1, 128)]
        visited_count = 0
        for kind, index, current, sentinel in roots:
            seen = set()
            while current and current != sentinel:
                if current == chunk:
                    return {'kind': kind, 'index': index, 'arena': arena,
                            'chunk': chunk, 'visited': visited_count}
                if current in seen or visited_count >= 4096:
                    self._stop('free_list_traversal_boundary', kind=kind, index=index,
                               arena=arena, address=current)
                    return None
                seen.add(current)
                visited_count += 1
                current = struct.unpack('<I', self.mu.mem_read(current + 8, 4))[0]
        return None

    def _watch(self, header):
        begin, end = header['pointer'], header['pointer'] + header['usable']
        if not 0 < header['usable'] <= 0x1000000:
            self._stop('freed_extent_not_supported', header=header)
            return
        for page in range(begin & ~4095, (end + 4095) & ~4095, 4096):
            if page not in self.watched_pages:
                self.watched_pages[page] = set()
                self.mu.hook_add(unicorn.UC_HOOK_MEM_WRITE, self._write,
                                 begin=max(0, page - 15), end=page + 4095)
            self.watched_pages[page].add(begin)

    def _write(self, uc, access, address, size, value, data):
        if self.report['status'] != 'observing':
            return
        pc = uc.reg_read(arm.UC_ARM_REG_PC)
        libc = self.bound.get('libc-2.11.2.so')
        if libc is not None and libc + 0x57B00 <= pc < libc + 0x5D000:
            return
        tid = self.probe.threads.current_tid
        if any(frame['function'] in PUBLIC_ALLOCATORS for frame in self.pending.get(tid, [])):
            return
        candidates = set()
        for page in range(address & ~4095, (address + size + 4095) & ~4095, 4096):
            candidates.update(self.watched_pages.get(page, ()))
        for pointer in candidates:
            old = self.retired.get(pointer)
            if old and address < pointer + old['usable'] and pointer < address + size:
                event = self._record('freed_payload_write', address=address, size=size,
                                     value=value, registers=self._registers(), previous_free=old)
                self._stop('write_after_observed_free', write=event)
                return
