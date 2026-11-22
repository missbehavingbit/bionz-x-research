"""Verify original GLES 128 KiB shared-memory init, IRQ 76, and unpadded allocator contracts."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import struct
from pathlib import Path

import unicorn
from elftools.elf.elffile import ELFFile
from unicorn import arm_const as arm

from project_paths import PROJECT_ROOT
from src.a6400.execution.guest_gles import (
    GLES_LOCK_SEMAPHORE,
    GLES_UNLOCK_SEMAPHORE,
    GlesBinding,
    GuestGles,
)
from src.a6400.execution.guest_mmap import GuestMmap
from src.a6400.execution.interpreter_probe import install_libc_it_latch_repairs
from src.a6400.execution.kernel_task_identity import KernelTaskIdentityTable
from src.a6400.execution.sugilite_gpu import ST_P3D, ST_PPF, ST_PSC0, SugiliteGpuEngine
from tools.research.rea_wsl_batch import checked_input

MODULE = 'firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/kmod/grm_gles.ko'
MODULE_SHA256 = 'a8d1dbe8c66be365d32371b13f35a0e7d598c415c0ff17f2c8f544084ccc6882'
LIBOBJ = 'firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/lib/libObj.so'
LIBOBJ_SHA256 = '60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1'
LIBC = 'firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha7_unpacked/lib/libc-2.11.2.so'
LIBC_SHA256 = 'bceb5cfb7769b54bf9c9d178b8a5f4579531c0f60e673cbcae872a8c7a3508fb'


def _map_elf_segments(mu: unicorn.Uc, elf: ELFFile, raw: bytes, base: int) -> None:
    spans: list[tuple[int, int]] = []
    for seg in elf.iter_segments():
        if seg['p_type'] != 'PT_LOAD':
            continue
        vaddr = int(seg['p_vaddr'])
        memsz = int(seg['p_memsz'])
        p0 = (base + vaddr) & ~0xFFF
        p1 = (base + vaddr + memsz + 0xFFF) & ~0xFFF
        if spans and p0 <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], p1))
        else:
            spans.append((p0, p1))
    for p0, p1 in spans:
        mu.mem_map(p0, p1 - p0)
    for seg in elf.iter_segments():
        if seg['p_type'] != 'PT_LOAD':
            continue
        vaddr = int(seg['p_vaddr'])
        filesz = int(seg['p_filesz'])
        off = int(seg['p_offset'])
        if filesz:
            mu.mem_write(base + vaddr, raw[off:off + filesz])


def probe_kernel_sugilite_open_and_irq(ko_raw: bytes) -> dict:
    """Execute original grm_gles.ko sugilite_open and sugilite_interrupt in ARM mode."""
    elf = ELFFile(io.BytesIO(ko_raw))
    text_sec = elf.get_section_by_name('.text')
    symtab = elf.get_section_by_name('.symtab')
    rel_sec = elf.get_section_by_name('.rel.text')
    text = bytearray(text_sec.data())

    code_base = 0x80000000
    stub_base = 0x80020000
    data_base = 0x80040000
    shared_va = 0x80080000
    bar_va = 0x80100000
    sp_top = 0x80200000
    ret_sentinel = 0x8003F000

    # Replace CP15 ATS1CPR / PAR translation sequence (0x4ac..0x4cc) with r0 = shared_va PA
    # so user-mode Unicorn executes sugilite_open without privileged CP15 fault; record exact
    # patched range as host kernel-translation stand-in.
    cp15_orig = bytes(text[0x4AC:0x4D0])
    # movw r0, #0x0000; movt r0, #0x2000; nop * 7
    cp15_repl = (
        struct.pack('<II', 0xE3000000, 0xE3420000)
        + struct.pack('<I', 0xE1A00000) * 7
    )
    text[0x4AC:0x4D0] = cp15_repl

    stubs: dict[str, int] = {}
    stub_calls: list[dict] = []
    next_stub = stub_base
    next_kalloc = data_base + 0x8000

    for rel in rel_sec.iter_relocations():
        off = int(rel['r_offset'])
        rtype = int(rel['r_info_type'])
        sym = symtab.get_symbol(rel['r_info_sym'])
        shndx = sym['st_shndx']
        if rtype in (28, 29):  # R_ARM_CALL / R_ARM_JUMP24
            if shndx == 1:
                target = code_base + int(sym['st_value'])
            else:
                name = sym.name or f'sec_{shndx}_{sym["st_value"]}'
                if name not in stubs:
                    stubs[name] = next_stub
                    next_stub += 0x10
                target = stubs[name]
            disp = ((target - (code_base + off + 8)) >> 2) & 0xFFFFFF
            insn = struct.unpack_from('<I', text, off)[0]
            struct.pack_into('<I', text, off, (insn & 0xFF000000) | disp)
        elif rtype == 2:  # R_ARM_ABS32
            addend = struct.unpack_from('<I', text, off)[0]
            if shndx != 'SHN_UNDEF':
                target = data_base + (int(shndx) * 0x400) + addend
            else:
                target = data_base + 0x7000
            struct.pack_into('<I', text, off, target & 0xFFFFFFFF)

    mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_ARM)
    mu.ctl_set_cpu_model(arm.UC_CPU_ARM_CORTEX_A9)
    mu.mem_map(code_base, 0x40000)
    mu.mem_map(data_base, 0x20000)
    mu.mem_map(shared_va, 0x20000)
    mu.mem_map(bar_va, 0x4000)
    mu.mem_map(sp_top - 0x4000, 0x4000)
    mu.mem_write(code_base, bytes(text))
    for addr in stubs.values():
        mu.mem_write(addr, struct.pack('<I', 0xE12FFF1E))  # bx lr
    mu.mem_write(ret_sentinel, struct.pack('<I', 0xEAFFFFFE))

    # Populate thread_info at sp & ~0x1fff -> +0xc task_struct -> +0x108 tgid (1000)
    tinfo = (sp_top - 0x100) & ~0x1FFF
    task_va = data_base + 0x6000
    mu.mem_write(tinfo + 0x0C, struct.pack('<I', task_va))
    mu.mem_write(task_va + 0x108, struct.pack('<I', 1000))
    # Initialize list_head in module .bss/.data referenced at 0x608 (r5 + 0, r5 + 4)
    for sec_idx in range(1, 20):
        lh = data_base + sec_idx * 0x400
        mu.mem_write(lh, struct.pack('<II', lh, lh))
        mu.mem_write(lh + 4, struct.pack('<II', lh + 4, lh + 4))

    inode_va = data_base + 0x100
    filp1_va = data_base + 0x400
    filp2_va = data_base + 0x600
    dev_va = data_base + 0x1000
    mu.mem_write(inode_va + 0x190, struct.pack('<I', dev_va))
    mu.mem_write(dev_va + 0x40, struct.pack('<II', 0, 0xDEADBEEF))  # open_count=0, seq=deadbeef
    mu.mem_write(dev_va + 0xAC, struct.pack('<I', shared_va))
    mu.mem_write(dev_va + 0xF8, struct.pack('<I', bar_va))
    mu.mem_write(shared_va, b'\xA5' * 0x20000)

    engine = SugiliteGpuEngine()
    engine.status = ST_P3D | ST_PPF | ST_PSC0

    addr_to_stub = {v: k for k, v in stubs.items()}

    def _on_stub(uc: unicorn.Uc, address: int, size: int, _ud) -> None:
        nonlocal next_kalloc
        if address == ret_sentinel:
            uc.emu_stop()
            return
        name = addr_to_stub.get(address)
        if name is None:
            return
        r0 = uc.reg_read(arm.UC_ARM_REG_R0)
        r1 = uc.reg_read(arm.UC_ARM_REG_R1)
        r2 = uc.reg_read(arm.UC_ARM_REG_R2)
        stub_calls.append({'stub': name, 'r0': r0, 'r1': r1, 'r2': r2})
        if name == '__memzero':
            uc.mem_write(r0, b'\x00' * r1)
        elif name == 'memset':
            uc.mem_write(r0, bytes([r1 & 0xFF]) * r2)
        elif name == 'kmem_cache_alloc':
            ptr = next_kalloc
            next_kalloc += 0x80
            uc.mem_write(ptr, b'\x00' * 0x80)
            uc.reg_write(arm.UC_ARM_REG_R0, ptr)
        elif name in ('__bug_noarg',):
            uc.emu_stop()

    def _on_bar(uc: unicorn.Uc, access: int, address: int, size: int, value: int, _ud) -> None:
        off = address - bar_va
        if access == unicorn.UC_MEM_WRITE:
            engine.write_mmio(off, value)
            uc.mem_write(bar_va + 0x34, struct.pack('<I', engine.read_mmio(0x34)))

    h1 = mu.hook_add(unicorn.UC_HOOK_CODE, _on_stub)
    h2 = mu.hook_add(unicorn.UC_HOOK_MEM_WRITE, _on_bar, begin=bar_va, end=bar_va + 0x3FFF)

    def _call(fn_off: int, r0: int, r1: int) -> int:
        mu.reg_write(arm.UC_ARM_REG_SP, sp_top - 0x100)
        mu.reg_write(arm.UC_ARM_REG_LR, ret_sentinel)
        mu.reg_write(arm.UC_ARM_REG_R0, r0)
        mu.reg_write(arm.UC_ARM_REG_R1, r1)
        mu.emu_start(code_base + fn_off, ret_sentinel, count=4096)
        return mu.reg_read(arm.UC_ARM_REG_R0)

    ret_open1 = _call(0x43C, inode_va, filp1_va)
    shared_after_open1 = bytes(mu.mem_read(shared_va, 0x20000))
    zero_prefix = shared_after_open1[:8]
    untouched_count = sum(1 for b in shared_after_open1[4:] if b == 0xA5)
    open_count_1, seq_1 = struct.unpack('<II', bytes(mu.mem_read(dev_va + 0x40, 8)))
    sem_counts = [
        struct.unpack('<I', bytes(mu.mem_read(dev_va + off + 8, 4)))[0]
        for off in (0xB0, 0xC4, 0xD8)
    ]

    # Simulate user-space rm_rqueue_init writing initialized flag 1 at +0, then second open
    mu.mem_write(shared_va, struct.pack('<I', 1))
    stub_calls_before_2 = len(stub_calls)
    ret_open2 = _call(0x43C, inode_va, filp2_va)
    word0_after_open2 = struct.unpack('<I', bytes(mu.mem_read(shared_va, 4)))[0]
    open_count_2 = struct.unpack('<I', bytes(mu.mem_read(dev_va + 0x40, 4)))[0]
    open2_memzero_calls = [c for c in stub_calls[stub_calls_before_2:] if c['stub'] == '__memzero']

    # Execute original sugilite_interrupt(76, dev_va) with BAR+0x34 = 0xC4000000
    mu.mem_write(bar_va + 0x34, struct.pack('<I', engine.read_mmio(0x34)))
    mu.mem_write(dev_va + 0xA8, struct.pack('<I', 0))
    stub_calls_before_irq = len(stub_calls)
    ret_irq = _call(0x658, 76, dev_va)
    dev_a8_after_irq = struct.unpack('<I', bytes(mu.mem_read(dev_va + 0xA8, 4)))[0]
    irq_wake_calls = [c for c in stub_calls[stub_calls_before_irq:] if c['stub'] == '__wake_up']
    mu.hook_del(h1)
    mu.hook_del(h2)

    return {
        'cp15_ats1cpr_standin': {
            'offset_range': '0x4ac..0x4cf',
            'original_hex': cp15_orig.hex(),
            'reason': 'user-mode Unicorn cannot execute privileged CP15 ATS1CPR/PAR DMA address translation',
        },
        'first_open': {
            'ret': ret_open1,
            'open_count': open_count_1,
            'event_seq_dev_44': seq_1,
            'first_8_bytes_hex': zero_prefix.hex(),
            'zeroed_header_bytes': 4,
            'untouched_0xa5_bytes_after_offset_4': untouched_count,
            'semaphore_counts': sem_counts,
            'memzero_calls': [c for c in stub_calls[:stub_calls_before_2] if c['stub'] == '__memzero'],
        },
        'second_open': {
            'ret': ret_open2,
            'open_count': open_count_2,
            'shared_word0_preserved': word0_after_open2,
            'memzero_calls': open2_memzero_calls,
        },
        'sugilite_interrupt': {
            'ret_irq_handled': ret_irq,
            'status_seeded': ST_P3D | ST_PPF | ST_PSC0,
            'status_after_ack': engine.read_mmio(0x34),
            'dev_a8_accumulated': dev_a8_after_irq,
            'wake_up_calls': len(irq_wake_calls),
        },
    }


def probe_userspace_rm_rqueue_init(libobj_raw: bytes, libc_raw: bytes) -> dict:
    """Execute original libObj.so rm_rqueue_init against GuestGles(kernel-sugilite)."""
    obj_elf = ELFFile(io.BytesIO(libobj_raw))
    libc_elf = ELFFile(io.BytesIO(libc_raw))

    mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB)
    mu.ctl_set_cpu_model(arm.UC_CPU_ARM_CORTEX_A9)
    # Enable VFP/NEON CP10/CP11 in case user-space touches d-registers
    cpacr = mu.reg_read(arm.UC_ARM_REG_C1_C0_2)
    mu.reg_write(arm.UC_ARM_REG_C1_C0_2, cpacr | (0xF << 20))
    mu.reg_write(arm.UC_ARM_REG_FPEXC, 0x40000000)

    obj_base = 0x54000000
    libc_base = 0x50200000
    stub_page = 0x09000000
    stack_base = 0x5E000000
    state_page = 0x12000000

    _map_elf_segments(mu, obj_elf, libobj_raw, obj_base)
    _map_elf_segments(mu, libc_elf, libc_raw, libc_base)
    mu.mem_map(stub_page, 0x2000)
    mu.mem_map(stack_base, 0x20000)
    mu.mem_map(state_page, 0x4000)

    mmap_mgr = GuestMmap(mu, None, base_address=0x20000000, ceiling_address=0x40000000)
    ktable = KernelTaskIdentityTable(address_limit=0x5F000000)
    ktable.register(1000, 1000)
    gles = GuestGles(GlesBinding(table=ktable), completion_policy='kernel-sugilite')

    # Resolve libObj GOT/PLT symbols against libc-2.11.2.so + GuestGles syscall stubs
    StubIoctl = stub_page + 0x100
    StubMmap = stub_page + 0x110
    StubErrno = stub_page + 0x120
    StubGetpid = stub_page + 0x130
    StubDefault = stub_page + 0x140
    RetStop = stub_page + 0x200
    for addr in (StubIoctl, StubMmap, StubErrno, StubGetpid, StubDefault):
        mu.mem_write(addr, b'\x70\x47')  # Thumb bx lr
    mu.mem_write(RetStop, b'\xfe\xe7')

    libc_syms = {}
    cdyn = libc_elf.get_section_by_name('.dynsym')
    for s in cdyn.iter_symbols():
        if s.name and s['st_value'] and s['st_shndx'] != 'SHN_UNDEF':
            libc_syms[s.name] = libc_base + int(s['st_value'])

    special = {
        'ioctl': StubIoctl | 1,
        'mmap': StubMmap | 1,
        'mmap64': StubMmap | 1,
        '__errno_location': StubErrno | 1,
        'getpid': StubGetpid | 1,
    }
    odynsym = obj_elf.get_section_by_name('.dynsym')
    for sec_name in ('.rel.dyn', '.rel.plt'):
        sec = obj_elf.get_section_by_name(sec_name)
        if sec is None:
            continue
        for rel in sec.iter_relocations():
            rt = rel['r_info_type']
            roff = int(rel['r_offset'])
            if rt == 23:  # R_ARM_RELATIVE
                cur = struct.unpack('<I', bytes(mu.mem_read(obj_base + roff, 4)))[0]
                mu.mem_write(obj_base + roff, struct.pack('<I', (obj_base + cur) & 0xFFFFFFFF))
            elif rt in (21, 22):  # GLOB_DAT / JUMP_SLOT
                sym = odynsym.get_symbol(rel['r_info_sym'])
                if sym.name in special:
                    target = special[sym.name]
                elif sym['st_value'] and sym['st_shndx'] != 'SHN_UNDEF':
                    target = obj_base + int(sym['st_value'])
                elif sym.name in libc_syms:
                    target = libc_syms[sym.name]
                else:
                    target = StubDefault | 1
                mu.mem_write(obj_base + roff, struct.pack('<I', target & 0xFFFFFFFF))

    shared_guard_faults: list[dict] = []
    extra_hooks: list[int] = []
    fd_to_owner: dict[int, int] = {}

    def _guard_shared(uc: unicorn.Uc, access: int, address: int, size: int, value: int, _ud) -> None:
        if access == unicorn.UC_MEM_WRITE:
            for a in range(address, address + size):
                gles.shared_unknown.pop(a, None)
        elif any(a in gles.shared_unknown for a in range(address, address + size)):
            shared_guard_faults.append({'pc': uc.reg_read(arm.UC_ARM_REG_PC), 'address': address, 'size': size})
            uc.emu_stop()

    hooks_installed = False

    def _on_code(uc: unicorn.Uc, address: int, size: int, _ud) -> None:
        nonlocal hooks_installed
        if address == RetStop:
            uc.emu_stop()
            return
        if address == StubErrno:
            uc.reg_write(arm.UC_ARM_REG_R0, state_page + 0x3000)
            return
        if address == StubGetpid:
            uc.reg_write(arm.UC_ARM_REG_R0, 1000)
            return
        if address == StubDefault:
            uc.reg_write(arm.UC_ARM_REG_R0, 0)
            return
        if address == StubIoctl:
            fd = uc.reg_read(arm.UC_ARM_REG_R0)
            cmd = uc.reg_read(arm.UC_ARM_REG_R1)
            arg = uc.reg_read(arm.UC_ARM_REG_R2)
            ret = gles.ioctl(
                fd_to_owner[fd], cmd, arg,
                lambda a, n: bytes(uc.mem_read(a, n)),
                lambda a, d: uc.mem_write(a, bytes(d)),
                tid=1000,
            )
            uc.reg_write(arm.UC_ARM_REG_R0, (ret or 0) & 0xFFFFFFFF)
            return
        if address == StubMmap:
            addr_req = uc.reg_read(arm.UC_ARM_REG_R0)
            length = uc.reg_read(arm.UC_ARM_REG_R1)
            prot = uc.reg_read(arm.UC_ARM_REG_R2)
            flags = uc.reg_read(arm.UC_ARM_REG_R3)
            sp = uc.reg_read(arm.UC_ARM_REG_SP)
            fd, off_bytes = struct.unpack('<II', bytes(uc.mem_read(sp, 8)))
            ev = gles.mmap2(mmap_mgr, addr_req, length, prot, flags, off_bytes >> 12, owner_id=fd_to_owner[fd])
            uc.reg_write(arm.UC_ARM_REG_R0, ev['returned'] & 0xFFFFFFFF)
            if not hooks_installed and gles.shared_mapping is not None:
                sv = gles.shared_mapping['virtual']
                sl = gles.shared_mapping['length']
                extra_hooks.append(
                    uc.hook_add(
                        unicorn.UC_HOOK_MEM_READ | unicorn.UC_HOOK_MEM_WRITE,
                        _guard_shared,
                        begin=sv,
                        end=sv + sl - 1,
                    )
                )
                hooks_installed = True

    h_code = mu.hook_add(unicorn.UC_HOOK_CODE, _on_code, begin=stub_page, end=stub_page + 0xFFF)

    # Populate g_rm_memset_fn (+0x1382c34, referenced via GOT +0x13704e4 at +0x72c158/+0x72c1b2)
    # with original libc-2.11.2.so memset (+0x5fa10).
    mu.mem_write(obj_base + 0x1382C34, struct.pack('<I', libc_syms['memset']))
    ipc_boundary = obj_base + 0x72C0E8  # declared SysV shmget(0x4101) boundary after 128 KiB init
    memset_calls = 0

    def _count_memset(uc: unicorn.Uc, address: int, size: int, _ud) -> None:
        nonlocal memset_calls
        memset_calls += 1

    extra_hooks.append(
        mu.hook_add(unicorn.UC_HOOK_CODE, _count_memset,
                    begin=libc_syms['memset'], end=libc_syms['memset'])
    )

    # First client open (open_count 0 -> 1): publish fd=10 at libObj g_dmpgles_fd (+0x1382514)
    owner1 = gles.open(0x2, 1000, 1000, [(stack_base, 0x20000)])
    fd_to_owner[10] = owner1['owner_id']
    mu.mem_write(obj_base + 0x1382514, struct.pack('<I', 10))
    mu.reg_write(arm.UC_ARM_REG_SP, stack_base + 0x18000)
    mu.reg_write(arm.UC_ARM_REG_LR, RetStop | 1)
    mu.emu_start((obj_base + 0x72BF80) | 1, ipc_boundary, count=500000)
    pc_stop_1 = mu.reg_read(arm.UC_ARM_REG_PC)
    memset_calls_1 = memset_calls
    # Release semaphore 1 via original rm_unlock_sem(1) (+0x72bc20) before second client open
    mu.reg_write(arm.UC_ARM_REG_SP, stack_base + 0x18000)
    mu.reg_write(arm.UC_ARM_REG_LR, RetStop | 1)
    mu.reg_write(arm.UC_ARM_REG_R0, 1)
    mu.emu_start((obj_base + 0x72BC20) | 1, RetStop, count=10000)
    sv = gles.shared_mapping['virtual'] if gles.shared_mapping else None
    hdr_words_1 = list(struct.unpack('<8I', bytes(mu.mem_read(sv, 32)))) if sv else []
    unknown_after_init1 = len(gles.shared_unknown)

    # Second client open (open_count 1 -> 2): publish fd=11 at libObj g_dmpgles_fd (+0x1382514)
    owner2 = gles.open(0x2, 1000, 1000, [(stack_base, 0x20000)])
    fd_to_owner[11] = owner2['owner_id']
    mu.mem_write(obj_base + 0x1382514, struct.pack('<I', 11))
    mu.reg_write(arm.UC_ARM_REG_SP, stack_base + 0x18000)
    mu.reg_write(arm.UC_ARM_REG_LR, RetStop | 1)
    mu.emu_start((obj_base + 0x72BF80) | 1, ipc_boundary, count=500000)
    pc_stop_2 = mu.reg_read(arm.UC_ARM_REG_PC)
    memset_calls_2 = memset_calls - memset_calls_1
    mu.reg_write(arm.UC_ARM_REG_SP, stack_base + 0x18000)
    mu.reg_write(arm.UC_ARM_REG_LR, RetStop | 1)
    mu.reg_write(arm.UC_ARM_REG_R0, 1)
    mu.emu_start((obj_base + 0x72BC20) | 1, RetStop, count=10000)
    hdr_words_2 = list(struct.unpack('<8I', bytes(mu.mem_read(sv, 32)))) if sv else []

    mu.hook_del(h_code)
    for eh in extra_hooks:
        mu.hook_del(eh)

    return {
        'first_open_zeroed_4b': gles.shared_header_zeroed,
        'unknown_bytes_on_first_mmap': 0x20000 - 4,
        'unknown_bytes_after_guest_rm_rqueue_init': unknown_after_init1,
        'shared_guard_faults': shared_guard_faults,
        'first_stop_pc_offset': pc_stop_1 - obj_base,
        'second_stop_pc_offset': pc_stop_2 - obj_base,
        'first_init_memset_calls': memset_calls_1,
        'second_init_memset_calls': memset_calls_2,
        'shared_aliases': list(gles.shared_mapping.get('aliases', [])) if gles.shared_mapping else [],
        'shared_header_words_after_first_init': [hex(w) for w in hdr_words_1],
        'shared_header_words_after_second_init': [hex(w) for w in hdr_words_2],
        'gles_Lock_Unlock_transactions': [
            {'op': t.get('operation'), 'cmd': hex(t.get('cmd', 0)), 'sem': t.get('sem_index')}
            for t in gles.transactions
            if t.get('cmd') in (GLES_LOCK_SEMAPHORE, GLES_UNLOCK_SEMAPHORE) or t.get('operation') == 'mmap2'
        ],
    }


def probe_libc_allocator_4000_ops(libc_raw: bytes, *, with_repairs: bool) -> dict:
    """Run 4,000 deterministic original glibc-2.11.2 malloc/free ops without size padding."""
    elf = ELFFile(io.BytesIO(libc_raw))
    mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB)
    mu.ctl_set_cpu_model(arm.UC_CPU_ARM_CORTEX_A9)
    cpacr = mu.reg_read(arm.UC_ARM_REG_C1_C0_2)
    mu.reg_write(arm.UC_ARM_REG_C1_C0_2, cpacr | (0xF << 20))
    mu.reg_write(arm.UC_ARM_REG_FPEXC, 0x40000000)

    base = 0x50200000
    brk_start = 0x01000000
    brk_cur = brk_start
    brk_mapped = brk_start
    mmap_cur = 0x20000000
    stack_base = 0x5E000000
    tls_base = 0x5E100000
    ret_stop = 0x09000000

    _map_elf_segments(mu, elf, libc_raw, base)
    mu.mem_map(stack_base, 0x20000)
    mu.mem_map(tls_base, 0x4000)
    mu.mem_map(ret_stop, 0x1000)
    mu.mem_map(0xFFFF0000, 0x1000)
    ret_zero = ret_stop + 0x10
    mu.mem_write(ret_stop, b'\xfe\xe7')
    mu.mem_write(ret_zero, b'\x00\x20\x70\x47')  # movs r0, #0; bx lr

    # Populate kuser helpers at 0xffff0fa0 (dmb), 0xffff0fc0 (cmpxchg), 0xffff0fe0 (get_tls)
    mu.mem_write(0xFFFF0FA0, struct.pack('<II', 0xF57FF05F, 0xE12FFF1E))
    mu.mem_write(
        0xFFFF0FC0,
        struct.pack('<IIIIII', 0xE5923000, 0xE0533000, 0x05821000, 0xE2730000, 0xE12FFF1E, 0),
    )
    mu.mem_write(0xFFFF0FE0, struct.pack('<II', 0xE59F0000, 0xE12FFF1E) + struct.pack('<I', tls_base + 0x400))
    mu.reg_write(arm.UC_ARM_REG_C13_C0_3, tls_base + 0x400)

    # Populate synthetic _rtld_global_ro (+0x10 = _dl_pagesize = 4096) and _rtld_global for ptmalloc_init
    rtld_ro = tls_base + 0x1000
    rtld_rw = tls_base + 0x2000
    mu.mem_write(rtld_ro + 0x10, struct.pack('<I', 0x1000))
    mu.mem_write(rtld_rw + 0x7D0, struct.pack('<II', ret_zero | 1, ret_zero | 1))

    reldyn = elf.get_section_by_name('.rel.dyn')
    relplt = elf.get_section_by_name('.rel.plt')
    dynsym = elf.get_section_by_name('.dynsym')
    for sec in (reldyn, relplt):
        if sec is None:
            continue
        for rel in sec.iter_relocations():
            rt = rel['r_info_type']
            roff = int(rel['r_offset'])
            if rt == 23:
                cur = struct.unpack('<I', bytes(mu.mem_read(base + roff, 4)))[0]
                mu.mem_write(base + roff, struct.pack('<I', (base + cur) & 0xFFFFFFFF))
            elif rt == 2:
                addend = struct.unpack('<I', bytes(mu.mem_read(base + roff, 4)))[0]
                sym = dynsym.get_symbol(rel['r_info_sym'])
                if sym['st_value']:
                    mu.mem_write(base + roff, struct.pack('<I', (base + int(sym['st_value']) + addend) & 0xFFFFFFFF))
            elif rt in (21, 22):
                sym = dynsym.get_symbol(rel['r_info_sym'])
                if sym.name == '_rtld_global_ro':
                    mu.mem_write(base + roff, struct.pack('<I', rtld_ro))
                elif sym.name == '_rtld_global':
                    mu.mem_write(base + roff, struct.pack('<I', rtld_rw))
                elif sym['st_value']:
                    mu.mem_write(base + roff, struct.pack('<I', (base + int(sym['st_value'])) & 0xFFFFFFFF))
                elif rt == 22:
                    mu.mem_write(base + roff, struct.pack('<I', ret_zero | 1))
                else:
                    mu.mem_write(base + roff, struct.pack('<I', rtld_rw + 0x800))

    report: dict = {}
    if with_repairs:
        # Also arm the top-split literal load check + 5 _int_free + _int_malloc repairs
        install_libc_it_latch_repairs(mu, base, report, include_malloc_add=True)
        lit = struct.unpack('<I', bytes(mu.mem_read(base + 0x5A094, 4)))[0]

        def _top_split(uc: unicorn.Uc, address: int, size: int, _ud) -> None:
            cpsr = uc.reg_read(arm.UC_ARM_REG_CPSR)
            it_mask = (3 << 25) | (0x3F << 10)
            if cpsr & it_mask:
                uc.reg_write(arm.UC_ARM_REG_CPSR, cpsr & ~it_mask)
            if uc.reg_read(arm.UC_ARM_REG_R2) != lit:
                uc.reg_write(arm.UC_ARM_REG_R2, lit)
                report['top_split_repairs'] = report.get('top_split_repairs', 0) + 1

        mu.hook_add(unicorn.UC_HOOK_CODE, _top_split, begin=base + 0x59C00, end=base + 0x59C00)

    def _on_intr(uc: unicorn.Uc, intno: int, _ud) -> None:
        nonlocal brk_cur, brk_mapped, mmap_cur
        if intno != 2:
            uc.emu_stop()
            return
        nr = uc.reg_read(arm.UC_ARM_REG_R7)
        r0 = uc.reg_read(arm.UC_ARM_REG_R0)
        r1 = uc.reg_read(arm.UC_ARM_REG_R1)
        if nr == 45:  # brk
            if r0 >= brk_start:
                need = (r0 + 0xFFF) & ~0xFFF
                while brk_mapped < need:
                    uc.mem_map(brk_mapped, 0x10000)
                    brk_mapped += 0x10000
                brk_cur = r0
            uc.reg_write(arm.UC_ARM_REG_R0, brk_cur)
        elif nr == 192:  # mmap2
            sz = (r1 + 0xFFF) & ~0xFFF
            uc.mem_map(mmap_cur, sz)
            uc.reg_write(arm.UC_ARM_REG_R0, mmap_cur)
            mmap_cur += sz + 0x1000
        elif nr == 91:  # munmap
            uc.reg_write(arm.UC_ARM_REG_R0, 0)
        elif nr == 220:  # madvise
            uc.reg_write(arm.UC_ARM_REG_R0, 0)
        else:
            report['unexpected_syscall'] = nr
            uc.emu_stop()

    fault: dict | None = None

    def _on_invalid(uc: unicorn.Uc, access: int, address: int, size: int, value: int, _ud) -> bool:
        nonlocal fault
        fault = {
            'pc': uc.reg_read(arm.UC_ARM_REG_PC),
            'offset_in_libc': uc.reg_read(arm.UC_ARM_REG_PC) - base,
            'lr_offset_in_libc': hex(uc.reg_read(arm.UC_ARM_REG_LR) - base),
            'cpsr': uc.reg_read(arm.UC_ARM_REG_CPSR),
            'address': address,
            'access': access,
        }
        return False

    def _on_stop(uc: unicorn.Uc, address: int, size: int, _ud) -> None:
        uc.emu_stop()

    def _on_block(uc: unicorn.Uc, address: int, size: int, _ud) -> None:
        pass

    h_blk = mu.hook_add(unicorn.UC_HOOK_BLOCK, _on_block)
    h_intr = mu.hook_add(unicorn.UC_HOOK_INTR, _on_intr)
    h_inv = mu.hook_add(unicorn.UC_HOOK_MEM_INVALID, _on_invalid)
    h_stp = mu.hook_add(unicorn.UC_HOOK_CODE, _on_stop, begin=ret_stop, end=ret_stop + 4)

    rng = random.Random(526)
    sizes = (4, 0x24, 0x48, 0x4C, 0x168, 0x200, 0x10B0, 0x50, 0x10, 0x2B8)
    live: list[tuple[int, int, int]] = []
    malloc_va = (base + 0x5B3C8) | 1
    free_va = (base + 0x5B8D4) | 1

    completed = 0
    for step in range(4000):
        if live and (len(live) >= 32 or rng.randrange(3) == 0):
            idx = rng.randrange(len(live))
            ptr, sz, tag = live.pop(idx)
            payload = bytes(mu.mem_read(ptr, sz))
            if payload != bytes([tag]) * sz:
                return {
                    'with_repairs': with_repairs,
                    'status': 'payload_corruption',
                    'step': step,
                    'size': sz,
                }
            mu.reg_write(arm.UC_ARM_REG_SP, stack_base + 0x18000)
            mu.reg_write(arm.UC_ARM_REG_LR, ret_stop | 1)
            mu.reg_write(arm.UC_ARM_REG_R0, ptr)
            try:
                mu.emu_start(free_va, ret_stop, count=100000)
            except unicorn.UcError as exc:
                return {
                    'with_repairs': with_repairs,
                    'status': 'fault_in_free',
                    'step': step,
                    'free_size': sz,
                    'error': str(exc),
                    'fault': fault,
                }
        else:
            sz = rng.choice(sizes)
            tag = (step & 0x7F) | 0x80
            mu.reg_write(arm.UC_ARM_REG_SP, stack_base + 0x18000)
            mu.reg_write(arm.UC_ARM_REG_LR, ret_stop | 1)
            mu.reg_write(arm.UC_ARM_REG_R0, sz)
            try:
                mu.emu_start(malloc_va, ret_stop, count=100000)
            except unicorn.UcError as exc:
                return {
                    'with_repairs': with_repairs,
                    'status': 'fault_in_malloc',
                    'step': step,
                    'request_size': sz,
                    'error': str(exc),
                    'fault': fault,
                }
            ptr = mu.reg_read(arm.UC_ARM_REG_R0)
            if ptr == 0:
                return {'with_repairs': with_repairs, 'status': 'null_malloc', 'step': step, 'request_size': sz}
            mu.mem_write(ptr, bytes([tag]) * sz)
            live.append((ptr, sz, tag))
        completed += 1

    return {
        'with_repairs': with_repairs,
        'status': 'completed',
        'operations_completed': completed,
        'live_allocations_remaining': len(live),
        'libc_it_latch_clears': report.get('libc_it_latch_clears', 0),
        'libc_malloc_largebin_repairs': report.get('libc_malloc_largebin_repairs', 0),
        'top_split_repairs': report.get('top_split_repairs', 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--output',
        type=Path,
        default=PROJECT_ROOT / 'artifacts/analysis/unicorn-l3-gpu-20261119/gles-and-allocator-isolation.json',
    )
    args = parser.parse_args()

    ko_raw = checked_input(PROJECT_ROOT, MODULE, MODULE_SHA256)
    libobj_raw = checked_input(PROJECT_ROOT, LIBOBJ, LIBOBJ_SHA256)
    libc_raw = checked_input(PROJECT_ROOT, LIBC, LIBC_SHA256)

    import os, sys
    print('[1/4] kernel sugilite_open + sugilite_interrupt...', file=sys.stderr, flush=True)
    kernel_res = probe_kernel_sugilite_open_and_irq(ko_raw)
    print('[2/4] userspace rm_rqueue_init...', file=sys.stderr, flush=True)
    rqueue_res = probe_userspace_rm_rqueue_init(libobj_raw, libc_raw)
    print('[3/4] libc 4000-op allocator (unrepaired)...', file=sys.stderr, flush=True)
    alloc_unrepaired = probe_libc_allocator_4000_ops(libc_raw, with_repairs=False)
    print('[4/4] libc 4000-op allocator (repaired)...', file=sys.stderr, flush=True)
    alloc_repaired = probe_libc_allocator_4000_ops(libc_raw, with_repairs=True)

    result = {
        'inputs': {
            MODULE: hashlib.sha256(ko_raw).hexdigest(),
            LIBOBJ: hashlib.sha256(libobj_raw).hexdigest(),
            LIBC: hashlib.sha256(libc_raw).hexdigest(),
        },
        'kernel_sugilite_open_and_irq': kernel_res,
        'userspace_rm_rqueue_init': rqueue_res,
        'libc_allocator_4000_ops_unrepaired': alloc_unrepaired,
        'libc_allocator_4000_ops_repaired': alloc_repaired,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2), flush=True)
    os._exit(0)


if __name__ == '__main__':
    main()
