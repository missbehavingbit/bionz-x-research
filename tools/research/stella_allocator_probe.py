"""Isolate original stella allocation and glibc size contracts without booting im.elf."""
import argparse
import hashlib
import io
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import capstone
import unicorn
from elftools.elf.elffile import ELFFile
from unicorn import arm_const as arm

from project_paths import PROJECT_ROOT
from src.a6400.execution.guest_memory import accessible
from src.a6400.execution.interpreter_probe import module_mapping_entries, resumed_module_base
from tools.research.rea_wsl_batch import checked_input
from tools.research.startup_trace import decode_block, read_va


LIBOBJ = 'firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/lib/libObj.so'
LIBOBJ_SHA256 = '60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1'
LIBC = 'firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha7_unpacked/lib/libc-2.11.2.so'
LIBC_SHA256 = 'bceb5cfb7769b54bf9c9d178b8a5f4579531c0f60e673cbcae872a8c7a3508fb'
LOGALLOC_VA = 0x79FE16
LOGALLOC_EXPECTED = bytes.fromhex('4c20')
LOGALLOC_REPLACEMENT = bytes.fromhex('5020')
STACK = 0x08000000
RETURN = 0x09000000
STATE = 0x10000000
CHUNK = 0x20000000
USER = CHUNK + 8
OBJ_BASE = 0x30000000
LIBC_BASE = 0x50000000


def _checked_elf(raw, expected):
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Allocator input SHA-256 mismatch')
    elf = ELFFile(io.BytesIO(raw))
    if (elf.elfclass, elf.little_endian, elf['e_machine'], elf['e_type']) != (
            32, True, 'EM_ARM', 'ET_DYN'):
        raise ValueError('Expected an ELF32 little-endian ARM shared library')
    return elf


def _read_code(elf, raw, address, size):
    data = read_va(elf, raw, address, size)
    if data is None or len(data) != size:
        raise ValueError(f'Original code is not file-backed: {address:#x}+{size:#x}')
    return data


def apply_logalloc_patch(probe):
    """Apply the existing request-padding trial only to an identity-matched loaded copy."""
    entries = module_mapping_entries('libObj.so', probe.mmap.mappings)
    paths = {entry['path'] for entry in entries}
    if len(paths) != 1:
        raise ValueError('Log allocation patch requires one mapped libObj.so path')
    path = next(iter(paths))
    if path not in probe.filesystem.cache:
        raise ValueError('Log allocation patch requires the loaded libObj.so source cache')
    raw = bytes(probe.filesystem.cache[path])
    elf = _checked_elf(raw, LIBOBJ_SHA256)
    segments = [segment for segment in elf.iter_segments()
                if segment['p_type'] == 'PT_LOAD']
    if not any(segment['p_flags'] & 1
               and segment['p_vaddr'] <= LOGALLOC_VA
               and LOGALLOC_VA + 2 <= segment['p_vaddr'] + segment['p_filesz']
               for segment in segments):
        raise ValueError('Log allocation patch is outside file-backed executable code')
    if _read_code(elf, raw, LOGALLOC_VA, 2) != LOGALLOC_EXPECTED:
        raise ValueError('Log allocation patch source preimage mismatch')
    resolution = resumed_module_base(
        'libObj.so', entries,
        [{'file_offset': segment['p_offset'], 'vaddr': segment['p_vaddr']}
         for segment in segments])
    address = resolution['base'] + LOGALLOC_VA
    if not any(entry['address'] <= address
               and address + 2 <= entry['address'] + entry['length']
               and entry['prot'] & unicorn.UC_PROT_EXEC for entry in entries):
        raise ValueError('Log allocation patch lacks an executable file mapping')
    if not accessible(probe.mu, address, 2, unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC):
        raise ValueError('Log allocation patch is not mapped readable/executable')
    if bytes(probe.mu.mem_read(address, 2)) != LOGALLOC_EXPECTED:
        raise ValueError(f'Log allocation patch loaded preimage mismatch at {address:#x}')
    probe.mu.mem_write(address, LOGALLOC_REPLACEMENT)
    if bytes(probe.mu.mem_read(address, 2)) != LOGALLOC_REPLACEMENT:
        raise RuntimeError('Log allocation patch readback mismatch')
    entry = {
        'label': 'stella_logalloc_chunksize_0x50_overlay',
        'va': address, 'module_base': resolution['base'],
        'module_offset': LOGALLOC_VA, 'guest_path': path,
        'source_sha256': LIBOBJ_SHA256,
        'expected_hex': LOGALLOC_EXPECTED.hex(),
        'replacement_hex': LOGALLOC_REPLACEMENT.hex(),
        'source': 'libObj.so movs r0,#0x4C -> #0x50; request-padding trial',
        'classification': 'modified firmware; not a verified allocator correction',
    }
    probe.report['guest_patch'] = entry
    probe.report.setdefault('guest_patches', []).append(entry)
    probe.report['camera_fidelity_validated'] = False
    return entry


class _Fragment:
    """Small instruction/data allowlist with no import or syscall responses."""

    def __init__(self, code, supplied=(), writable=()):
        self.mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_ARM)
        self.mu.ctl_set_cpu_model(arm.UC_CPU_ARM_CORTEX_A9)
        self.code = [(address, bytes(data)) for address, data in code]
        self.known = set()
        self.pages = set()
        self.writable = [(STACK, STACK + 0x2000), *writable]
        self.report = {'status': 'running', 'trace': [], 'reads': [], 'writes': [],
                       'code_inputs': [
                           {'address': address, 'length': len(data),
                            'sha256': hashlib.sha256(data).hexdigest()}
                           for address, data in self.code]}
        for address, data in [*self.code, *supplied, (RETURN, bytes.fromhex('fee7'))]:
            self._map(address, len(data))
            self.mu.mem_write(address, bytes(data))
            self.known.update(range(address, address + len(data)))
        self._map(STACK, 0x2000)
        self.mu.hook_add(unicorn.UC_HOOK_CODE, self._instruction)
        self.mu.hook_add(unicorn.UC_HOOK_MEM_READ | unicorn.UC_HOOK_MEM_WRITE,
                         self._memory)
        self.mu.hook_add(unicorn.UC_HOOK_MEM_INVALID, self._invalid)

    def _map(self, address, size):
        for page in range(address & ~4095, (address + size + 4095) & ~4095, 4096):
            if page not in self.pages:
                self.mu.mem_map(page, 4096)
                self.pages.add(page)

    def _stop(self, status, **details):
        if self.report['status'] == 'running':
            self.report.update(status=status, boundary={
                'pc': self.mu.reg_read(arm.UC_ARM_REG_PC), **details})
        self.mu.emu_stop()

    def _instruction(self, mu, address, size, _):
        if address in self.stops:
            self._stop(self.stops[address])
        elif not any(start <= address and address + size <= start + len(data)
                     for start, data in self.code):
            self._stop('unsupported_code', address=address, size=size)
        else:
            self.report['trace'].append(address)

    def _memory(self, mu, access, address, size, value, _):
        event = {'pc': mu.reg_read(arm.UC_ARM_REG_PC),
                 'address': address, 'size': size}
        if access == unicorn.UC_MEM_WRITE:
            if not any(lo <= address and address + size <= hi for lo, hi in self.writable):
                self._stop('unsupported_write', **event)
                return
            self.known.update(range(address, address + size))
            self.report['writes'].append({**event, 'value': value})
        elif not all(at in self.known for at in range(address, address + size)):
            self._stop('unsupplied_read', **event)
        else:
            self.report['reads'].append(event)

    def _invalid(self, mu, access, address, size, value, _):
        self._stop('unmapped_memory', access=access, address=address, size=size)
        return False

    def run(self, start, registers, stops=None, limit=256):
        self.stops = {RETURN: 'returned', **(stops or {})}
        self.mu.reg_write(arm.UC_ARM_REG_SP, STACK + 0x2000)
        self.mu.reg_write(arm.UC_ARM_REG_LR, RETURN | 1)
        for name, value in registers.items():
            self.mu.reg_write(getattr(arm, 'UC_ARM_REG_' + name.upper()), value)
        try:
            self.mu.emu_start(start | 1, 0, count=limit, timeout=1000000)
        except unicorn.UcError as error:
            if self.report['status'] == 'running':
                self._stop('execution_fault', error=str(error))
            else:
                self.report['unicorn_error'] = str(error)
        if self.report['status'] == 'running':
            self.report['status'] = (
                'timeout' if self.mu.query(unicorn.UC_QUERY_TIMEOUT) else 'instruction_limit')
        self.report['registers'] = {
            name: self.mu.reg_read(getattr(arm, 'UC_ARM_REG_' + name.upper()))
            for name in ('r0', 'r1', 'r2', 'r5', 'r7', 'r11', 'sp', 'lr', 'pc', 'cpsr')}
        return self.report


def _normalize_request(elf, raw, requested):
    if not 0 <= requested <= 0xFFFFFFDE:
        raise ValueError('Request is outside the already-overflow-checked prefix contract')
    start, end = 0x592BA, 0x592D0
    fragment = _Fragment(
        [(LIBC_BASE + start, _read_code(elf, raw, start, end + 2 - start))],
        [(STATE, struct.pack('<I', 0x40))])
    report = fragment.run(
        LIBC_BASE + start, {'r2': requested, 'r12': STATE - 0x490},
        {LIBC_BASE + end: 'normalization_complete'})
    report.update(requested=requested, chunk_size=report['registers']['r11'])
    return report


def _usable_size(elf, raw, chunk_size, *, flags=1, next_inuse=True, null=False):
    code = _read_code(elf, raw, 0x5CA2C, 38)
    fragment = _Fragment(
        [(LIBC_BASE + 0x5CA2C, code)],
        [(CHUNK, struct.pack('<II', 0, chunk_size | flags)),
         (CHUNK + chunk_size + 4, struct.pack('<I', 0x20 | int(next_inuse)))])
    report = fragment.run(LIBC_BASE + 0x5CA2C, {'r0': 0 if null else USER})
    report.update(chunk_size=chunk_size, flags=flags, next_inuse=next_inuse,
                  null=null, usable_size=report['registers']['r0'])
    return report


def _allocation_prefix(elf, raw, start, requested, patch_logalloc=False):
    heap_slot0 = struct.unpack('<I', _read_code(elf, raw, 0x1353F20, 4))[0]
    single_slot30 = struct.unpack('<I', _read_code(elf, raw, 0x134C310 + 0x30, 4))[0]
    if (heap_slot0, single_slot30) != (0x7ACBBB, 0x64402D):
        raise ValueError('Original singleton/HeapAllocator vtable slots changed')
    ranges = ((0x79FB3E, 8), (0x64402C, 8), (0x79E8C2, 0x12),
              (0x79E902, 0x12), (0x7ACBBA, 0xE), (0x79FE14, 0x12), (0xFEEA4, 4))
    code = [(OBJ_BASE + address, _read_code(elf, raw, address, size))
            for address, size in ranges]
    manager = bytearray(0x80)
    fragment = _Fragment(
        code,
        [(STATE, struct.pack('<I', OBJ_BASE + 0x134C310)),
         (STATE + 8, struct.pack('<I', OBJ_BASE + 0x1353F20)),
         (OBJ_BASE + 0x134C310 + 0x30, struct.pack('<I', OBJ_BASE + single_slot30)),
         (OBJ_BASE + 0x1353F20, struct.pack('<I', OBJ_BASE + heap_slot0)),
         (STATE + 0x200, manager)],
        [(STATE + 0x200, STATE + 0x280)])
    bind = fragment.run(
        OBJ_BASE + 0x79FB3E,
        {'r0': STATE, 'r3': OBJ_BASE + 0x134C310, 'r4': STATE + 0x200},
        {OBJ_BASE + 0x79FB46: 'allocator_bound'})
    bound_allocator = int.from_bytes(fragment.mu.mem_read(STATE + 0x240, 4), 'little')
    if bind['status'] != 'allocator_bound' or bound_allocator != STATE + 8:
        raise ValueError(f'Original singleton +0x30 allocator binding failed: {bind}')
    patch_report = {}
    if patch_logalloc:
        mappings = [
            {'path': '/usr/lib/libObj.so',
             'address': OBJ_BASE + (segment['p_vaddr'] & ~4095),
             'offset': segment['p_offset'] & ~4095,
             'length': ((segment['p_vaddr'] % 4096 + segment['p_memsz'] + 4095) & ~4095),
             'prot': 5 if segment['p_flags'] & 1 else 3}
            for segment in elf.iter_segments() if segment['p_type'] == 'PT_LOAD']
        fixture = SimpleNamespace(
            mu=fragment.mu, mmap=SimpleNamespace(mappings=mappings),
            filesystem=SimpleNamespace(cache={'/usr/lib/libObj.so': raw}),
            report=patch_report)
        apply_logalloc_patch(fixture)
    fragment.report['status'] = 'running'
    report = fragment.run(
        OBJ_BASE + start,
        {'r0': requested, 'r1': bound_allocator, 'r4': STATE + 0x200, 'r5': 0},
        {OBJ_BASE + 0xFEEA4: 'malloc_import_boundary'})
    report.update(entry_offset=start, input_request=requested,
                  bound_allocator=bound_allocator,
                  malloc_request=report['registers']['r0'],
                  guest_patches=patch_report.get('guest_patches', []))
    return report


def _mutex_allocation_prefix(elf, raw):
    lit_global = _read_code(elf, raw, 0x7B1AE8, 4)
    lit_nothrow = _read_code(elf, raw, 0x7AF400, 8)
    off_got, off_nothrow = struct.unpack('<II', lit_nothrow)
    nothrow_slot = (0x7AF3F0 + 4 + off_got + off_nothrow) & 0xFFFFFFFF
    ranges = ((0x7AF46C, 0x30), (0x7B1ADC, 0xC), (0x7AF3EC, 0x14), (0xFEB40, 4))
    fragment = _Fragment(
        [(OBJ_BASE + address, _read_code(elf, raw, address, size))
         for address, size in ranges],
        [(OBJ_BASE + 0x7B1AE8, lit_global),
         (OBJ_BASE + 0x13F70A4, struct.pack('<I', 0)),
         (OBJ_BASE + 0x7AF400, lit_nothrow),
         (OBJ_BASE + nothrow_slot, struct.pack('<I', OBJ_BASE + 0x1360000))])
    report = fragment.run(
        OBJ_BASE + 0x7AF46C, {},
        {OBJ_BASE + 0xFEB40: 'nothrow_new_import_boundary'})
    report.update(
        operator_new_request=report['registers']['r0'],
        return_offset=(report['registers']['lr'] - OBJ_BASE) & ~1,
        constructor_call_return_offset=0x7AF49D)
    return report


def _constructor(elf, raw, requested, chunk_size):
    ranges = ((0x7A750A, 0x26), (0x7A74A0, 0x1E), (0x7A7494, 0xC))
    fragment = _Fragment(
        [(OBJ_BASE + address, _read_code(elf, raw, address, size))
         for address, size in ranges],
        [(CHUNK, struct.pack('<II', 0, chunk_size | 1)),
         (USER, bytes([0xA5]) * (chunk_size - 4)),
         (CHUNK + chunk_size + 4, struct.pack('<I', 0x21))],
        [(USER, USER + requested)])
    report = fragment.run(OBJ_BASE + 0x7A750A, {'r0': USER})
    payload_writes = [row for row in report['writes']
                      if USER <= row['address'] < USER + requested]
    report.update(
        requested=requested, chunk_size=chunk_size,
        highest_payload_write_end=max(
            (row['address'] + row['size'] - USER for row in payload_writes), default=0),
        own_size_word=int.from_bytes(fragment.mu.mem_read(CHUNK + 4, 4), 'little'),
        next_size_word=int.from_bytes(
            fragment.mu.mem_read(CHUNK + chunk_size + 4, 4), 'little'))
    return report


def isolate(libobj, libc, *, patch_logalloc=False):
    obj = _checked_elf(libobj, LIBOBJ_SHA256)
    glibc = _checked_elf(libc, LIBC_SHA256)
    wrappers = [_allocation_prefix(obj, libobj, offset, requested)
                for offset in (0x79E8C2, 0x79E902) for requested in (0x4C, 0x10B0)]
    allocation = _allocation_prefix(obj, libobj, 0x79FE14, 0, patch_logalloc)
    normalization = [_normalize_request(glibc, libc, requested)
                     for requested in (0, 1, 0x10, 0x24, 0x4C, 0x50, 0x10B0)]
    normalized = {row['requested']: row['chunk_size'] for row in normalization}
    requested = allocation['malloc_request']
    if allocation['status'] != 'malloc_import_boundary' or requested not in (0x4C, 0x50):
        raise ValueError(f'Original log allocation did not reach its declared import: {allocation}')
    chunk_size = normalized[requested]
    usable = [
        _usable_size(glibc, libc, chunk_size),
        _usable_size(glibc, libc, chunk_size, next_inuse=False),
        _usable_size(glibc, libc, chunk_size, flags=3),
        _usable_size(glibc, libc, chunk_size, null=True),
    ]
    constructor = _constructor(obj, libobj, requested, chunk_size)
    mutex_allocation = _mutex_allocation_prefix(obj, libobj)
    checks = {
        'wrappers_preserve_request': all(
            row['status'] == 'malloc_import_boundary'
            and row['bound_allocator'] == STATE + 8
            and row['malloc_request'] == row['input_request'] for row in wrappers),
        'normalization_completed': all(
            row['status'] == 'normalization_complete' for row in normalization),
        'normalization_matches_glibc_contract': all(
            row['chunk_size'] == max(0x10, (row['requested'] + 11) & ~7)
            for row in normalization),
        'usable_size_returned': all(row['status'] == 'returned' for row in usable),
        'ordinary_usable_size': usable[0]['usable_size'] == chunk_size - 4,
        'not_inuse_returns_zero': usable[1]['usable_size'] == 0,
        'mmapped_usable_size': usable[2]['usable_size'] == chunk_size - 8,
        'null_returns_zero': usable[3]['usable_size'] == 0,
        'constructor_within_request': constructor['status'] == 'returned'
        and constructor['highest_payload_write_end'] <= requested,
        'size_words_preserved': constructor['own_size_word'] == chunk_size | 1
        and constructor['next_size_word'] == 0x21,
        'mutex_null_global_allocator_uses_operator_new': (
            mutex_allocation['status'] == 'nothrow_new_import_boundary'
            and mutex_allocation['operator_new_request'] == 0x24
            and mutex_allocation['return_offset'] == 0x7AF492),
    }
    return {
        'status': 'isolation_complete' if all(checks.values()) else 'isolation_failed',
        'camera_access': False, 'camera_fidelity_validated': False,
        'policy': 'logalloc-request-padding-0x50' if patch_logalloc else 'original',
        'inputs': [{'path': LIBOBJ, 'sha256': LIBOBJ_SHA256},
                   {'path': LIBC, 'sha256': LIBC_SHA256}],
        'checks': checks, 'wrapper_allocations': wrappers,
        'log_allocation': allocation, 'request_normalization': normalization,
        'usable_size_cases': usable, 'log_constructor': constructor,
        'mutex_allocation': mutex_allocation,
        'arithmetic': {
            'requested': requested, 'normalized_chunk_size': chunk_size,
            'ordinary_usable_size': usable[0]['usable_size'],
            'chunk_minus_usable': chunk_size - usable[0]['usable_size'],
            'constructor_required_prefix': constructor['highest_payload_write_end'],
        },
        'verdict': ('Request padding is not a usable-size correction; the four-byte '
                    'chunk/usable difference remains. No overrun reproduced in this prefix.'),
        'original_instructions': {
            'singleton_allocator_accessor': decode_block(obj, libobj, 0x64402D, 8),
            'allocation_wrapper': decode_block(obj, libobj, 0x79E8C3, 0x12),
            'heap_allocator': decode_block(obj, libobj, 0x7ACBBB, 0xE),
            'pthread_mutex_create': decode_block(obj, libobj, 0x7AF46D, 0x30),
            'request_normalization': decode_block(glibc, libc, 0x592BB, 0x16),
            'malloc_usable_size': decode_block(glibc, libc, 0x5CA2D, 38),
        },
        'assumptions': [
            'Original ELF vtable slots (0x134C310+0x30 and 0x1353F20+0x00) bind singleton+8 to HeapAllocator.',
            'Synthetic load bases and mapping rows; only declared original code windows are mapped.',
            'Allocation prefixes stop before malloc/operator new; no allocation or import reply is supplied.',
            'Normalization starts after the original overflow guard; requests are bounded accordingly.',
            'Chunk headers and max_fast are constructed controls, not captured allocator state.',
            'The log constructor runs on a separately constructed buffer, not a malloc result.',
            'Cortex-A9 is the provisional InterpreterProbe CPU choice, not a verified camera CPU.',
        ],
        'remaining_boundary': (
            'Historical im.elf allocation/free ownership and scheduling are not replayed. '
            'No full boot, corrected Appframework abort, viewUnified2 or OSD frame is demonstrated. '
            'The cited wrappers return allocation pointers, not usable-size counts; do not subtract '
            'four from those pointers.'),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-dir', type=Path, required=True,
                        help='new output directory under artifacts')
    parser.add_argument('--guest-patch-logalloc-0x50', action='store_true',
                        help='opt-in identity-checked modified-firmware request-padding trial')
    args = parser.parse_args()
    output = (PROJECT_ROOT / args.report_dir).resolve()
    if not output.is_relative_to((PROJECT_ROOT / 'artifacts').resolve()):
        parser.error('Output must remain under artifacts')
    output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'configuration_error', 'camera_fidelity_validated': False}
    try:
        libobj = checked_input(PROJECT_ROOT, LIBOBJ, LIBOBJ_SHA256)
        libc = checked_input(PROJECT_ROOT, LIBC, LIBC_SHA256)
        report = isolate(libobj, libc, patch_logalloc=args.guest_patch_logalloc_0x50)
        checked_input(PROJECT_ROOT, LIBOBJ, LIBOBJ_SHA256)
        checked_input(PROJECT_ROOT, LIBC, LIBC_SHA256)
        report['source_unchanged'] = True
        return_code = 0 if report['status'] == 'isolation_complete' else 2
    except (OSError, ValueError, unicorn.UcError) as error:
        report.update(status='configuration_error', error=f'{type(error).__name__}: {error}')
        return_code = 2
    report['tools'] = {
        'python': sys.version, 'unicorn': unicorn.__version__,
        'capstone': capstone.__version__,
        'source_sha256': {
            relative: hashlib.sha256((PROJECT_ROOT / relative).read_bytes()).hexdigest()
            for relative in (
                'tools/research/stella_allocator_probe.py',
                'tools/research/startup_trace.py',
                'tools/research/rea_wsl_batch.py',
                'src/a6400/execution/interpreter_probe.py',
                'src/a6400/execution/guest_memory.py')},
    }
    report['command'] = sys.argv
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(output, report['status'], report.get('arithmetic', report.get('error')))
    return return_code


if __name__ == '__main__':
    raise SystemExit(main())
