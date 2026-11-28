"""ILCE-7C (a7C) Mod Loader v2 layout and binary identity checks.

Resolves and verifies all ILCE-7C firmware offsets against the extracted
viewUnified2.so, libObj.so, libtestcmd.so, and im.elf binaries.
"""
from __future__ import annotations

import hashlib

VIEWUNIFIED2_SHA256 = '1e524d4e97e6f0a1fc45e45dbbabec2f5d1a84d59d27e2d22e76f5fad73e2abe'
LIBOBJ_SHA256 = 'ac07a5dfb140381bf95813a76794d83437726231a740928e590be8d4ea5dfdbd'
IM_ELF_SHA256 = 'fca372d437333271f5d21c880768c02a99509bcc2629e3ba00122fb7a9f716af'
TESTCMD_SHA256 = 'ebd2afaab57d26a2e3d93637768a8bb2e0fd2467f4ccbcf27707b53a64545ad5'

PLT_STUB_PREFIX = bytes.fromhex('7847c046')

CAVE = {
    'file': 'libtestcmd.so',
    'file_size': 10080,
    'rx_filesz': 0x203c,
    'rx_page_end': 0x3000,
    'shim_offset': 0x2800,
    'shim_cap': 2048,
}

CONSTANTS = {
    'SET_TEXT': {
        'a7c': 0x181c44,
        'a6400': 0x14f6d0,
        'symbol': '_ZN9GEN_Label13setTextDirectEPKc',
    },
    'SET_MODE': {
        'a7c': 0x188b3c,
        'a6400': 0x155a68,
        'symbol': '_ZN2ux6wgtsys6Widget7setModeENS1_10WidgetModeE',
    },
}

LIBOBJ_EVENTS = {
    'event_new': {
        'a7c': 0x001084d0,
        'a6400': 0x000fd974,
        'symbol': '_Znwj',
        'kind': 'plt',
    },
    'event_ctor': {
        'a7c': 0x00898b11,
        'a6400': 0x000fce20,
        'symbol': '_ZN5EventC1Emhh',
        'kind': 'thumb',
        'signal': bytes.fromhex('0446416000af0272'),
    },
    'event_push': {
        'a7c': 0x0089a0bd,
        'a6400': 0x00100d5d,
        'symbol': '_ZN12EventManager4pushEP5Eventb',
        'kind': 'thumb',
        'signal': bytes.fromhex('e9f04184b0044602af08460d461646ff'),
    },
    'event_mgr': {
        'a7c': 0x01488ea0,
        'a6400': 0x01371984,
        'symbol': 'g_pEvtMgr',
        'kind': 'bss_direct',
    },
}

GOT_RELOC = 0x013d6050

LIBOBJ_SEGMENT = {
    'rw_p_vaddr': 0x0135d4e4,
    'rw_p_offset': 0x013554e4,
    'gap': 0x8000,
}

HOOKS = {
    'set_root': {
        'a7c': 0x7fa2e0,
        'a6400': 0x5ab654,
        'original': bytes.fromhex('044600af51b100f5c23303f52b731960'),
        'replay': bytes.fromhex('044600af51b100f5c23303f52b731960'),
        'signal': bytes.fromhex('044600af51b100f5c23303f52b731960'),
    },
    'refresh': {
        'a7c': 0x7fa718,
        'a6400': 0x5aba80,
        'original': bytes.fromhex('2de9f04f9bb006af044607f120004ff0'),
        'replay': bytes.fromhex('2de9f04f9bb006af044607f12000'),
        'signal': bytes.fromhex('2de9f04f9bb006af044607f12000'),
    },
    'open_item': {
        'a7c': 0x7facdc,
        'a6400': 0x5ac044,
        'original': bytes.fromhex('00f5c23303f52c732de9f043'),
        'replay': bytes.fromhex('00f5c23303f52c73'),
        'signal': bytes.fromhex('00f5c23303f52c732de9'),
    },
}

RECT_PREFIX = bytes([
    0x11, 0x00, 0x00, 0x00, 0x5b, 0x00, 0x42, 0x00,
    0x67, 0x00, 0x00, 0x00, 0x5b, 0x00, 0x42, 0x00,
    0xbd, 0x00, 0x00, 0x00, 0x5b, 0x00, 0x42, 0x00,
    0x13, 0x01, 0x00, 0x00, 0x5b, 0x00, 0x42, 0x00,
    0x69, 0x01, 0x00, 0x00, 0x5b, 0x00, 0x42, 0x00,
    0xbf, 0x01, 0x00, 0x00, 0x5b, 0x00, 0x42, 0x00,
])

RECT_TABLES = {
    'rect_tbl1_slot6': {'a7c': 0x9ff308, 'prefix_off': 0x9ff2d8, 'a6400': 0x802a04},
    'rect_tbl2_slot6': {'a7c': 0x9ff388, 'prefix_off': 0x9ff358, 'a6400': 0x802a84},
}

V1_ANCHOR = {
    'offset': 0xd9e550,
    'bss_vaddr': 0x00d00068,
    'bss_size': 0x09e4e8,
}


class LayoutMismatch(ValueError):
    """Raised when an A7C binary does not match the expected layout."""


def verify(image: bytes, expect_sha256: str | None = VIEWUNIFIED2_SHA256) -> dict:
    if expect_sha256 is not None:
        got = hashlib.sha256(image).hexdigest()
        if got != expect_sha256:
            raise LayoutMismatch(f'viewUnified2.so SHA-256 {got}, expected {expect_sha256}')
    out = {}
    for name, row in HOOKS.items():
        original, signal = row['original'], row['signal']
        at = image[row['a7c']:row['a7c'] + len(original)]
        if at != original:
            raise LayoutMismatch(
                f'{name}: bytes at +{row["a7c"]:#x} are {at.hex()}, expected {original.hex()}'
            )
        hits = []
        pos = 0
        while True:
            i = image.find(signal, pos)
            if i < 0:
                break
            hits.append(i)
            pos = i + 1
        if hits != [row['a7c']]:
            raise LayoutMismatch(
                f'{name}: site occurs at {[hex(h) for h in hits]}, expected once at +{row["a7c"]:#x}'
            )
        out[name] = {
            'offset': row['a7c'],
            'original': original,
            'replay': row['replay'],
            'signal': signal,
            'trap_after': row['replay'],
        }
    for name, row in CONSTANTS.items():
        stub = image[row['a7c']:row['a7c'] + 4]
        if stub != PLT_STUB_PREFIX:
            raise LayoutMismatch(
                f'{name}: +{row["a7c"]:#x} is {stub.hex()}, expected a PLT stub {PLT_STUB_PREFIX.hex()}'
            )
        out[name] = {'offset': row['a7c'], 'symbol': row['symbol'], 'kind': 'plt'}
    prefix_hits = []
    pos = 0
    while True:
        i = image.find(RECT_PREFIX, pos)
        if i < 0:
            break
        prefix_hits.append(i)
        pos = i + 1
    expected_prefixes = [
        RECT_TABLES['rect_tbl1_slot6']['prefix_off'],
        RECT_TABLES['rect_tbl2_slot6']['prefix_off'],
    ]
    if prefix_hits != expected_prefixes:
        raise LayoutMismatch(
            f'rect_prefix: occurs at {[hex(h) for h in prefix_hits]}, '
            f'expected {[hex(h) for h in expected_prefixes]}'
        )
    for name, row in RECT_TABLES.items():
        slot = image[row['a7c']:row['a7c'] + 8]
        if slot != b'\x00' * 8:
            raise LayoutMismatch(f'{name}: +{row["a7c"]:#x} is {slot.hex()}, expected 8 zero bytes')
        out[name] = {'offset': row['a7c']}
    return out
