"""Offline Unicorn test of the SMOD v2 OpenGate_5400_Test build (no camera).

Usage: python mods/opengate_5400_test/test_mod_v2_unicorn.py BUILD_DIR
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import unicorn as uc
from unicorn import arm_const as A

BUILD_DIR = Path(sys.argv[1])
mod = (BUILD_DIR / "OpenGate_5400_Test.mod").read_bytes()
magic, ver, flags, name, code_size, fnv, bss, on_off, off_off, hooks = struct.unpack_from("<III32s6I", mod)
assert magic == 0x444F4D53 and ver == 2 and name.rstrip(b"\0") == b"OpenGate_5400_Test" and hooks == 0
code = mod[68 : 68 + code_size]

AVCAM_E25_PAGE = 0x30000000
AVCAM_DF1_PAGE = 0x30100000
AVCAM_543_PAGE = 0x30200000
AVCAM_570_PAGE = 0x30300000
AVCAM_E36_PAGE = 0x30500000
AVCAM_E37_PAGE = 0x30600000
AIF0_PAGE = 0x30900000
LIBMPR_7C7_PAGE = 0x0790B000
LIBMPR_7C9_PAGE = 0x0790D000
LIBMPR_8EB_PAGE = 0x07A2F000
LIBMPR_A2F_PAGE = 0x07B73000
LIBMPR_A59_PAGE = 0x07B9D000
LIBMPR_A5A_PAGE = 0x07B9E000
STOP = 0x31000000
BASE = 0x40AB3000

U16_3840_2160 = 0x08700F00
U16_3240_2160 = 0x08700CA8

IS_A6400A = b"\xf4\x7d\xe2\x03" in code
if IS_A6400A:
    M16_OFF, M17_OFF, TN_OFF, TP_OFF = 0xDE0, 0xE2C, 0x33C, 0x504
    D0_OFF, D1_OFF, D2_OFF, V0_OFF, V1_OFF, V2_OFF = 0x5E4, 0x68C, 0x734, 0xB3C, 0x678, 0x7A8
    AVCAM_E28_PAGE = 0x30700000
    PATCHES = [
        (AVCAM_E25_PAGE, M16_OFF + 0x14, 4, 304),
        (AVCAM_E25_PAGE, M16_OFF + 0x1C, 6000, 5400),
        (AVCAM_E25_PAGE, M16_OFF + 0x34, 2, 200),
        (AVCAM_E25_PAGE, M16_OFF + 0x3C, 3376, 3600),
        (AVCAM_E25_PAGE, M17_OFF + 0x14, 4, 304),
        (AVCAM_E25_PAGE, M17_OFF + 0x1C, 6000, 5400),
        (AVCAM_E25_PAGE, M17_OFF + 0x34, 2, 200),
        (AVCAM_E25_PAGE, M17_OFF + 0x3C, 3376, 3600),
        (AVCAM_E28_PAGE, TN_OFF + 0x00, 862, 810),
        (AVCAM_E28_PAGE, TN_OFF + 0x04, 1740, 1852),
        (AVCAM_E28_PAGE, TN_OFF + 0x0C, 862, 810),
        (AVCAM_E28_PAGE, TN_OFF + 0x10, 1740, 1852),
        (AVCAM_E28_PAGE, TP_OFF + 0x00, 862, 810),
        (AVCAM_E28_PAGE, TP_OFF + 0x04, 1740, 1852),
        (AVCAM_E28_PAGE, TP_OFF + 0x0C, 862, 810),
        (AVCAM_E28_PAGE, TP_OFF + 0x10, 1740, 1852),
        (AVCAM_DF1_PAGE, D0_OFF + 0x00, 3840, 3240),
        (AVCAM_DF1_PAGE, D0_OFF + 0x08, 3840, 3264),
        (AVCAM_DF1_PAGE, D1_OFF + 0x00, 3840, 3240),
        (AVCAM_DF1_PAGE, D1_OFF + 0x08, 3840, 3264),
        (AVCAM_DF1_PAGE, D2_OFF + 0x00, 3840, 3240),
        (AVCAM_DF1_PAGE, D2_OFF + 0x08, 3840, 3264),
        (AVCAM_DF1_PAGE, V0_OFF + 0x00, 3840, 3240),
        (AVCAM_DF1_PAGE, V0_OFF + 0x0C, 3840, 3240),
        (AVCAM_543_PAGE, V1_OFF + 0x00, 3840, 3240),
        (AVCAM_543_PAGE, V1_OFF + 0x08, 3840, 3240),
        (AVCAM_570_PAGE, V2_OFF + 0x00, 3840, 3240),
    ]
    VENC_PATCHES = [
        (AVCAM_E36_PAGE, 0x59C), (AVCAM_E36_PAGE, 0x5FC), (AVCAM_E36_PAGE, 0x77C), (AVCAM_E36_PAGE, 0x7E0),
        (AVCAM_E36_PAGE, 0x844), (AVCAM_E36_PAGE, 0x8A8), (AVCAM_E36_PAGE, 0x90C), (AVCAM_E36_PAGE, 0x970),
        (AVCAM_E37_PAGE, 0x5F0), (AVCAM_E37_PAGE, 0x654), (AVCAM_E37_PAGE, 0x6B8), (AVCAM_E37_PAGE, 0x71C),
        (AVCAM_E37_PAGE, 0x780), (AVCAM_E37_PAGE, 0x7E4),
    ]
    PGOFF_MAP = {
        0x03E27: AVCAM_E25_PAGE, 0x03E28: AVCAM_E28_PAGE, 0x03DF4: AVCAM_DF1_PAGE,
        0x04548: AVCAM_543_PAGE, 0x04574: AVCAM_570_PAGE, 0x03E39: AVCAM_E36_PAGE,
        0x03E3A: AVCAM_E37_PAGE, 0xF3002: AIF0_PAGE,
    }
else:
    AVCAM_E28_PAGE = None
    PATCHES = [
        (AVCAM_E25_PAGE, 0x430 + 0x14, 4, 304),
        (AVCAM_E25_PAGE, 0x430 + 0x1C, 6000, 5400),
        (AVCAM_E25_PAGE, 0x430 + 0x34, 2, 200),
        (AVCAM_E25_PAGE, 0x430 + 0x3C, 3376, 3600),
        (AVCAM_E25_PAGE, 0x47C + 0x14, 4, 304),
        (AVCAM_E25_PAGE, 0x47C + 0x1C, 6000, 5400),
        (AVCAM_E25_PAGE, 0x47C + 0x34, 2, 200),
        (AVCAM_E25_PAGE, 0x47C + 0x3C, 3376, 3600),
        (AVCAM_E25_PAGE, 0x98C + 0x00, 862, 810),
        (AVCAM_E25_PAGE, 0x98C + 0x04, 1740, 1852),
        (AVCAM_E25_PAGE, 0x98C + 0x0C, 862, 810),
        (AVCAM_E25_PAGE, 0x98C + 0x10, 1740, 1852),
        (AVCAM_E25_PAGE, 0xB54 + 0x00, 862, 810),
        (AVCAM_E25_PAGE, 0xB54 + 0x04, 1740, 1852),
        (AVCAM_E25_PAGE, 0xB54 + 0x0C, 862, 810),
        (AVCAM_E25_PAGE, 0xB54 + 0x10, 1740, 1852),
        (AVCAM_DF1_PAGE, 0x9FC + 0x00, 3840, 3240),
        (AVCAM_DF1_PAGE, 0x9FC + 0x08, 3840, 3264),
        (AVCAM_DF1_PAGE, 0xAA4 + 0x00, 3840, 3240),
        (AVCAM_DF1_PAGE, 0xAA4 + 0x08, 3840, 3264),
        (AVCAM_DF1_PAGE, 0xB4C + 0x00, 3840, 3240),
        (AVCAM_DF1_PAGE, 0xB4C + 0x08, 3840, 3264),
        (AVCAM_DF1_PAGE, 0xEC0 + 0x00, 3840, 3240),
        (AVCAM_DF1_PAGE, 0xEC0 + 0x0C, 3840, 3240),
        (AVCAM_543_PAGE, 0x88C + 0x00, 3840, 3240),
        (AVCAM_543_PAGE, 0x88C + 0x08, 3840, 3240),
        (AVCAM_570_PAGE, 0x198 + 0x00, 3840, 3240),
    ]
    VENC_PATCHES = [
        (AVCAM_E36_PAGE, 0x9F4), (AVCAM_E36_PAGE, 0xA54), (AVCAM_E36_PAGE, 0xBD4), (AVCAM_E36_PAGE, 0xC38),
        (AVCAM_E36_PAGE, 0xC9C), (AVCAM_E36_PAGE, 0xD00), (AVCAM_E36_PAGE, 0xD64), (AVCAM_E36_PAGE, 0xDC8),
        (AVCAM_E37_PAGE, 0xA48), (AVCAM_E37_PAGE, 0xAAC), (AVCAM_E37_PAGE, 0xB10), (AVCAM_E37_PAGE, 0xB74),
        (AVCAM_E37_PAGE, 0xBD8), (AVCAM_E37_PAGE, 0xC3C),
    ]
    PGOFF_MAP = {
        0x03E25: AVCAM_E25_PAGE, 0x03DF1: AVCAM_DF1_PAGE, 0x04543: AVCAM_543_PAGE,
        0x04570: AVCAM_570_PAGE, 0x03E36: AVCAM_E36_PAGE, 0x03E37: AVCAM_E37_PAGE,
        0xF3002: AIF0_PAGE,
    }

LIBMPR_U32_VAS = [0x07B73114, 0x07B73124, 0x07B73134, 0x07B73144, 0x07B73154, 0x07B9DE78]
LIBMPR_U16_VAS = [0x0790BFD0, 0x0790D868, 0x07A2FC28, 0x07B9E0C4]


def make_machine() -> tuple[uc.Uc, list[str]]:
    m = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
    map_sz = ((len(code) + 0x2000) + 0xFFF) & ~0xFFF
    m.mem_map(BASE, map_sz)
    m.mem_write(BASE, code)

    pages = [
        AVCAM_E25_PAGE, AVCAM_DF1_PAGE, AVCAM_543_PAGE, AVCAM_570_PAGE,
        AVCAM_E36_PAGE, AVCAM_E37_PAGE, AIF0_PAGE,
        LIBMPR_7C7_PAGE, LIBMPR_7C9_PAGE, LIBMPR_8EB_PAGE,
        LIBMPR_A2F_PAGE, LIBMPR_A59_PAGE, LIBMPR_A5A_PAGE, STOP,
    ]
    if AVCAM_E28_PAGE is not None:
        pages.append(AVCAM_E28_PAGE)
    for page in pages:
        m.mem_map(page, 0x1000)
    m.mem_map(0x20000000, 0x10000)

    for page, off, stock, _ in PATCHES:
        m.mem_write(page + off, struct.pack("<I", stock))
    for page, off in VENC_PATCHES:
        m.mem_write(page + off, struct.pack("<I", U16_3840_2160))
    for va in LIBMPR_U32_VAS:
        m.mem_write(va, struct.pack("<I", 3840))
    for va in LIBMPR_U16_VAS:
        m.mem_write(va, struct.pack("<I", U16_3840_2160))
    m.mem_write(AVCAM_E25_PAGE + 0x42C, struct.pack("<I", 0xCAFEBABE))
    m.mem_write(AIF0_PAGE + 0x424, struct.pack("<I", 0x00000F60))
    m.mem_write(AIF0_PAGE + 0x4A4, struct.pack("<I", 0x00000148))

    logs: list[str] = []

    def intr(mu, _n, _):
        nr = mu.reg_read(A.UC_ARM_REG_R7)
        a = [
            mu.reg_read(r)
            for r in (
                A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2,
                A.UC_ARM_REG_R3, A.UC_ARM_REG_R4, A.UC_ARM_REG_R5,
            )
        ]
        ret = 0
        if nr == 5:
            path = bytes(mu.mem_read(a[0], 80)).split(b"\0")[0].decode(errors="replace")
            if path == "/dev/mem":
                ret = 3
            elif path == "/tmp_bt/mods/opengate_5400_test.log":
                ret = 4
            else:
                raise AssertionError(f"unexpected open {path}")
        elif nr == 192:
            ret = PGOFF_MAP[a[5]]
        elif nr == 125:
            assert a[1] == 4096
            ret = 0
        elif nr == 4 and a[0] == 4:
            logs.append(bytes(mu.mem_read(a[1], a[2])).decode(errors="replace"))
            ret = a[2]
        elif nr in (6, 91):
            ret = 0
        else:
            raise AssertionError(f"unexpected syscall nr={nr}")
        mu.reg_write(A.UC_ARM_REG_R0, ret)

    m.hook_add(uc.UC_HOOK_INTR, intr)
    return m, logs


def call(m: uc.Uc, off: int) -> None:
    m.reg_write(A.UC_ARM_REG_SP, 0x2000E000)
    m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
    m.emu_start((BASE + off) | 1, STOP, timeout=2_000_000, count=200_000)


m, logs = make_machine()
call(m, on_off)
for page, off, _, patched in PATCHES:
    assert struct.unpack("<I", m.mem_read(page + off, 4))[0] == patched
for page, off in VENC_PATCHES:
    assert struct.unpack("<I", m.mem_read(page + off, 4))[0] == U16_3240_2160
for va in LIBMPR_U32_VAS:
    assert struct.unpack("<I", m.mem_read(va, 4))[0] == 3840
for va in LIBMPR_U16_VAS:
    assert struct.unpack("<I", m.mem_read(va, 4))[0] == U16_3840_2160

call(m, on_off)
call(m, off_off)
for page, off, stock, _ in PATCHES:
    assert struct.unpack("<I", m.mem_read(page + off, 4))[0] == stock
for page, off in VENC_PATCHES:
    assert struct.unpack("<I", m.mem_read(page + off, 4))[0] == U16_3840_2160
for va in LIBMPR_U32_VAS:
    assert struct.unpack("<I", m.mem_read(va, 4))[0] == 3840
for va in LIBMPR_U16_VAS:
    assert struct.unpack("<I", m.mem_read(va, 4))[0] == U16_3840_2160

print("".join(logs), end="")
print(f"UNICORN OPENGATE_5400_TEST PASS ({len(code)} B)")
