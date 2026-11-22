"""Mod Loader v2 build layout for the ILCE-6100 (a6100) V1.00 `viewUnified2.so`.

The loader's three view hooks are byte-identical between the a6100 build and the
ILCE-6400 2.00 build the v2 sources were written for; only the offsets differ.
This module records the a6100 offsets, checks them against the shipped image,
and refuses loudly if the image is not the build it was derived from.

It is a layout table, not a loader: it lets the trampolines and the guard target
this build, and it is the place a future a6100 core/guard reads instead of the
a6400 constants.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

# SHA-256 of the modules the loader touches in the ILCE-6100 V1.00 root filesystem.
VIEWUNIFIED2_SHA256 = '7bb0f245c8ccdff8a7d4a6f473357b50cbdeed12d081bde805edffd339c28bce'
LIBOBJ_SHA256 = 'a9e2c016a214fc45ac9d7caacaedb3ac2d1bd5afa273121a4163ac7896f90e1b'
IM_ELF_SHA256 = 'ab7eed01e7ac204f555298bdfd13c0ffa89b34d1f1cd399bd74ad734ede8d93e'
TESTCMD_SHA256 = '66b30d8637f96dedb714c832084aa86681de71a7ce554a78232a9b92655e222a'

# The shim needs 2048 bytes of executable, zero, unused memory. The a6100
# libtestcmd.so ends at 0x2760, so base + 0x2800 is still inside the last page
# of its R E segment (zero fill after filesz) and beyond the RW segment's used
# range (0x203c + 0x1fc). Same layout the a6400 loader uses.
CAVE = dict(file='libtestcmd.so', offset=0x2800, size=2048,
            file_size=0x2760, sha256=TESTCMD_SHA256,
            note='beyond EOF, inside the R E segment last page; mprotect via ml_make_rwx')

# PLT stubs the shim calls. Resolved by symbol name (gdb labels the .plt), not
# by offset arithmetic: the a6100 .plt is not a constant shift of the a6400 one.
CONSTANTS = {
    'SET_TEXT': dict(a6100=0x150030, a6400=0x14f6d0,
                     symbol='_ZN9GEN_Label13setTextDirectEPKc'),
    'SET_MODE': dict(a6100=0x1563c8, a6400=0x155a68,
                     symbol='_ZN2ux6wgtsys6Widget7setModeENS1_10WidgetModeE'),
}

PLT_STUB_PREFIX = bytes.fromhex('7847c046')  # bx pc; nop (Thumb -> ARM veneer)

# The five libObj references the a6400 core used to post a MENU key are symbols,
# not opaque offsets: three are functions the DSO defines itself and one is the
# EventManager singleton pointer.  They are resolved by name from the dynamic
# symbol table, so a build whose .plt or .text moves cannot silently mismatch.
# `event_new` stays a PLT stub because operator new is imported from libstdc++.
LIBOBJ_EVENTS = {
    'event_new': dict(kind='plt', a6100=0xfd974, a6400=0xfd974,
                      symbol='_Znwj',
                      signal='12c68fe26dca8ce224fdbce512c68fe2'),
    'event_ctor': dict(kind='func', a6100=0x840f7d, a6400=0x840fed,
                       symbol='_ZN5EventC1Emhh',
                       signal='b50446416000af027243721420bcf4f4'),
    'event_push': dict(kind='func', a6100=0x842529, a6400=0x842599,
                       symbol='_ZN12EventManager4pushEP5Eventb',
                       signal='e9f04184b0044602af08460d461646ff'),
    'event_mgr': dict(kind='object', a6100=0x1425c70, a6400=0x1424d70,
                      symbol='g_pEvtMgr', signal=None),
}

# `g_pEvtMgr` lives in .bss, so it has no file bytes to match.  Its GOT entry is
# still verifiable: the .rel.dyn record that binds it is (r_offset, r_info) with
# r_info = (symbol index 2361 << 8) | R_ARM_GLOB_DAT, unique in the image.
GOT_RELOC = dict(got=0x1371f44, r_info=0x93915, file=0xcc1c8)

# libObj's RW LOAD.  The kernel maps the segment at the page containing
# p_offset, so the runtime address of an ELF virtual address is
#   rw_lo + (elf_va - (rw_pgoff + gap)),  gap = p_vaddr - p_offset.
# `gap` is 0x8000 in both builds.  Checked against the a6400 constant the
# shipped loader uses: libobj_runtime(0x1371984, 0x07016000, 0x012f6000)
# returns 0x07089984.
LIBOBJ_SEGMENT = dict(p_vaddr=0x12fe5bc, p_offset=0x12f65bc, gap=0x8000)

# site -> a6100/a6400 offsets, the bytes the trampoline replays (`original`,
# `replay` bytes) and a longer unique locator (`signal`). The replayed bytes are
# identical in both builds; the signal is what `verify()` searches for, because
# an 8-byte replay is ambiguous in a 11 MB image.
HOOKS = {
    'set_root': dict(a6100=0x5acc0c, a6400=0x5ab654, replay=16,
                     original='044600af51b100f5c23303f52b731960',
                     signal='044600af51b100f5c23303f52b731960'),
    'refresh': dict(a6100=0x5ad038, a6400=0x5aba80, replay=14,
                    original='2de9f04f9bb006af044607f12000',
                    signal='2de9f04f9bb006af044607f12000'),
    'open_item': dict(a6100=0x5ad5fc, a6400=0x5ac044, replay=8,
                      original='00f5c23303f52c73',
                      signal='00f5c23303f52c732de9'),
}

# The two 7th-tab rectangle tables in viewUnified2.so .rodata (`15PAS_MenuListTab`).
# In the a6400 FW 2.00 build the guard reference base is 0x155e6000 and the
# two slot-6 addresses are 0x15de8a64 (+0x802a64) and 0x15de8ae4 (+0x802ae4).
# Each slot-6 is preceded by the 48-byte `orig_rect_prefix` (tabs 0..5 x/y/w/h)
# and holds 8 zero bytes at rest.  In the a6100 image that 48-byte prefix occurs
# only at these two tables (+0x803954 and +0x8039d4).
RECT_PREFIX = bytes.fromhex(
    '110000005b004200670000005b004200bd0000005b004200'
    '130100005b004200690100005b004200bf0100005b004200'
)
RECT_PATCH = bytes.fromhex('150200005b004200')
RECT_TABLES = {
    'rect_tbl1_slot6': dict(a6100=0x803984, a6400=0x802a64, prefix_off=0x803954),
    'rect_tbl2_slot6': dict(a6100=0x803a04, a6400=0x802ae4, prefix_off=0x8039d4),
}

# `MLV2_V1_ANCHOR`: 8-byte {magic, state} slot at the exact end of viewUnified2.so
# .bss (within the zero-filled tail of its last 4 KiB page).
# a6400: .bss 0xb06ba8 + 0x72098 = 0xb78c40 (page ends at 0xb79000).
# a6100: .bss 0xb07d80 + 0x728d8 = 0xb7a658 (page ends at 0xb7b000).
V1_ANCHOR = dict(a6100=0xb7a658, a6400=0xb78c40, size=8, page_end=0xb7b000)


class LayoutMismatch(ValueError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify(image: bytes, *, expect_sha256: str | None = VIEWUNIFIED2_SHA256) -> dict:
    """Return the resolved hook table, or raise if the image is not this build.

    The offsets are checked two ways: the recorded offset must carry the
    recorded original bytes, and those bytes must occur exactly once in the
    image (a moved or duplicated site is a mismatch, not a guess).
    """
    if expect_sha256 is not None:
        actual = sha256(image)
        if actual != expect_sha256:
            raise LayoutMismatch(f'viewUnified2 SHA-256 {actual} is not the '
                                 f'a6100 build {expect_sha256}')
    out = {}
    for name, row in HOOKS.items():
        original = bytes.fromhex(row['original'])
        signal = bytes.fromhex(row['signal'])
        at = image[row['a6100']:row['a6100'] + row['replay']]
        if at != original:
            raise LayoutMismatch(f'{name}: bytes at +{row["a6100"]:#x} are '
                                 f'{at.hex()}, expected {original.hex()}')
        hits = []
        pos = 0
        while True:
            i = image.find(signal, pos)
            if i < 0:
                break
            hits.append(i)
            pos = i + 1
        if hits != [row['a6100']]:
            raise LayoutMismatch(f'{name}: site occurs at '
                                 f'{[hex(h) for h in hits]}, expected once at '
                                 f'+{row["a6100"]:#x}')
        out[name] = dict(offset=row['a6100'], original=original, replay=row['replay'],
                         signal=signal, trap_after=row['replay'])
    for name, row in CONSTANTS.items():
        stub = image[row['a6100']:row['a6100'] + 4]
        if stub != PLT_STUB_PREFIX:
            raise LayoutMismatch(f'{name}: +{row["a6100"]:#x} is {stub.hex()}, '
                                 f'expected a PLT stub {PLT_STUB_PREFIX.hex()}')
        out[name] = dict(offset=row['a6100'], symbol=row['symbol'], kind='plt')
    prefix_hits = []
    pos = 0
    while True:
        i = image.find(RECT_PREFIX, pos)
        if i < 0:
            break
        prefix_hits.append(i)
        pos = i + 1
    expected_prefixes = [RECT_TABLES['rect_tbl1_slot6']['prefix_off'],
                         RECT_TABLES['rect_tbl2_slot6']['prefix_off']]
    if prefix_hits != expected_prefixes:
        raise LayoutMismatch(f'rect_prefix: occurs at {[hex(h) for h in prefix_hits]}, '
                             f'expected {[hex(h) for h in expected_prefixes]}')
    for name, row in RECT_TABLES.items():
        slot = image[row['a6100']:row['a6100'] + 8]
        if slot != b'\x00' * 8:
            raise LayoutMismatch(f'{name}: +{row["a6100"]:#x} is {slot.hex()}, '
                                 f'expected 8 zero bytes')
        out[name] = dict(offset=row['a6100'], prefix_off=row['prefix_off'],
                         patch=RECT_PATCH)
    out['v1_anchor'] = dict(offset=V1_ANCHOR['a6100'], size=V1_ANCHOR['size'],
                            page_end=V1_ANCHOR['page_end'])
    return out


def libobj_runtime(elf_va: int, rw_lo: int, rw_pgoff: int) -> int:
    """Runtime address of a libObj ELF virtual address, from its RW maps line.

    `rw_lo`/`rw_pgoff` come from `/proc/<pid>/maps`; the page-rounded file
    offset means the mapping starts `gap` bytes into the segment, not at
    p_vaddr.  Works for the file-backed part and for the anonymous .bss
    mapping that follows it, because both continue the same linear layout.
    """
    return rw_lo + (elf_va - (rw_pgoff + LIBOBJ_SEGMENT['gap']))


def verify_libobj(image: bytes) -> dict:
    """Resolve the four event references, or raise if the image is not this build."""
    actual = sha256(image)
    if actual != LIBOBJ_SHA256:
        raise LayoutMismatch(f'libObj SHA-256 {actual} is not the a6100 build '
                             f'{LIBOBJ_SHA256}')
    out = {}
    for name, row in LIBOBJ_EVENTS.items():
        if row['signal'] is None:
            out[name] = dict(offset=row['a6100'], symbol=row['symbol'],
                             kind=row['kind'])
            continue
        signal = bytes.fromhex(row['signal'])
        at = image[row['a6100']:row['a6100'] + len(signal)]
        if at != signal:
            raise LayoutMismatch(f'{name}: bytes at +{row["a6100"]:#x} are '
                                 f'{at.hex()}, expected {signal.hex()}')
        if image.count(signal) != 1:
            raise LayoutMismatch(f'{name}: signal occurs {image.count(signal)} '
                                 f'times, expected once')
        out[name] = dict(offset=row['a6100'], symbol=row['symbol'],
                         kind=row['kind'], signal=signal)
    record = struct.pack('<II', GOT_RELOC['got'], GOT_RELOC['r_info'])
    if image.count(record) != 1 or \
            image[GOT_RELOC['file']:GOT_RELOC['file'] + 8] != record:
        raise LayoutMismatch('event_mgr: g_pEvtMgr GOT relocation is not at '
                             f'+{GOT_RELOC["got"]:#x}')
    return out


def verify_cave(libtestcmd: bytes) -> dict:
    """The shim window must be past EOF and leave >= CAVE['size'] bytes to page end."""
    actual = hashlib.sha256(libtestcmd).hexdigest()
    if actual != CAVE['sha256']:
        raise LayoutMismatch(f'libtestcmd.so SHA-256 {actual} is not the a6100 build')
    if len(libtestcmd) != CAVE['file_size']:
        raise LayoutMismatch(f'libtestcmd.so is {len(libtestcmd)} bytes, expected '
                             f'{CAVE["file_size"]}')
    start, size = CAVE['offset'], CAVE['size']
    if start < len(libtestcmd):
        raise LayoutMismatch(f'cave offset {start:#x} is inside the file')
    page_end = (start + 0x1000 - 1) & ~0xfff
    if page_end - start < size:
        raise LayoutMismatch(f'cave at {start:#x} has only {page_end - start} bytes '
                             f'to the page end, needs {size}')
    return dict(start=start, size=size, page_end=page_end,
                zero_from=len(libtestcmd))


def from_file(path: Path) -> dict:
    return verify(Path(path).read_bytes())
