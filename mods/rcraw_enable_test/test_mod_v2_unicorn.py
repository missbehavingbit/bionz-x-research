"""Offline Unicorn test of the SMOD v2 RCRAW_Enable_Test build (no camera).

Usage: python mods/rcraw_enable_test/test_mod_v2_unicorn.py BUILD_DIR
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import unicorn as uc
from unicorn import arm_const as A

BUILD_DIR = Path(sys.argv[1])
mod = (BUILD_DIR / "RCRAW_Enable_Test.mod").read_bytes()
magic, ver, flags, name, code_size, fnv, bss, on_off, off_off, hooks = struct.unpack_from("<III32s6I", mod)
assert magic == 0x444F4D53 and ver == 2 and name.rstrip(b"\0") == b"RCRAW_Enable_Test" and hooks == 0
code = mod[68 : 68 + code_size]

IS_A6400A = "--target=ilce_6400a" in sys.argv or (
    "--target" in sys.argv and sys.argv[sys.argv.index("--target") + 1] == "ilce_6400a"
) or b"\xc0\x2e\x3f\x04" in code
RCRAW_PGOFF = 0x043F2 if IS_A6400A else 0x043D1
EN_OFF = 0xB04 if IS_A6400A else 0x810
RD_OFF = 0xEC0 if IS_A6400A else 0xBCC
D5_OFF = 0x828 if IS_A6400A else 0x534
RCRAW_PAGE = 0x30000000
AIF0_PAGE = 0x30100000
Q7C8_PAGE = 0x30200000
STOP = 0x31000000
BASE = 0x40AB3000


def make_machine() -> tuple[uc.Uc, list[str]]:
    m = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
    map_sz = ((len(code) + 0x2000) + 0xFFF) & ~0xFFF
    m.mem_map(BASE, map_sz)
    m.mem_write(BASE, code)

    for page in (RCRAW_PAGE, AIF0_PAGE, Q7C8_PAGE, STOP):
        m.mem_map(page, 0x1000)
    m.mem_map(0x20000000, 0x10000)

    # Guard neighboring words around RCRAW_EN and RDRAW_EN
    m.mem_write(RCRAW_PAGE + EN_OFF - 4, struct.pack("<I", 0xCAFEBABE))
    m.mem_write(RCRAW_PAGE + EN_OFF, struct.pack("<I", 0x00000000))
    m.mem_write(RCRAW_PAGE + EN_OFF + 4, struct.pack("<I", 0x11223344))
    m.mem_write(RCRAW_PAGE + D5_OFF, struct.pack("<I", 0x00000100))
    m.mem_write(RCRAW_PAGE + RD_OFF, struct.pack("<I", 0x00000000))

    m.mem_write(AIF0_PAGE + 0x400, struct.pack("<II", 1, 0x80000000))
    m.mem_write(AIF0_PAGE + 0x410, struct.pack("<I", 0x12345000))
    m.mem_write(AIF0_PAGE + 0x480, struct.pack("<II", 0, 0))
    m.mem_write(AIF0_PAGE + 0x490, struct.pack("<I", 0x18000000))
    m.mem_write(AIF0_PAGE + 0x4A4, struct.pack("<I", 0x01800000))
    m.mem_write(AIF0_PAGE + 0x4AC, struct.pack("<I", 0x0007))
    m.mem_write(Q7C8_PAGE + 0x7C8, struct.pack("<I", 0x00000400))

    logs: list[str] = []

    def intr(mu, _n, _):
        nr = mu.reg_read(A.UC_ARM_REG_R7)
        a = [
            mu.reg_read(r)
            for r in (
                A.UC_ARM_REG_R0,
                A.UC_ARM_REG_R1,
                A.UC_ARM_REG_R2,
                A.UC_ARM_REG_R3,
                A.UC_ARM_REG_R4,
                A.UC_ARM_REG_R5,
            )
        ]
        ret = 0
        if nr == 5:  # open
            path = bytes(mu.mem_read(a[0], 80)).split(b"\0")[0].decode(errors="replace")
            if path == "/dev/mem":
                ret = 3
            elif path == "/tmp_bt/mods/rcraw_enable_test.log":
                ret = 4
            else:
                raise AssertionError(f"unexpected open {path}")
        elif nr == 192:  # mmap2
            pgoff = a[5]
            if pgoff == RCRAW_PGOFF:
                ret = RCRAW_PAGE
            elif pgoff == 0xF3002:
                assert (a[2] & 2) == 0, "AIF0 page must be mapped read-only"
                ret = AIF0_PAGE
            elif pgoff == 0xF3031:
                assert (a[2] & 2) == 0, "0xF3031 page must be mapped read-only"
                ret = Q7C8_PAGE
            else:
                raise AssertionError(f"unexpected mmap2 pgoff={pgoff:#x}")
        elif nr == 4:  # write
            assert a[0] == 4
            logs.append(bytes(mu.mem_read(a[1], a[2])).decode())
            ret = a[2]
        elif nr in (6, 91):
            ret = 0
        else:
            raise AssertionError(f"unexpected syscall {nr}")
        mu.reg_write(A.UC_ARM_REG_R0, ret & 0xFFFFFFFF)

    m.hook_add(uc.UC_HOOK_INTR, intr)
    return m, logs


def invoke(m: uc.Uc, entry_off: int) -> None:
    m.reg_write(A.UC_ARM_REG_SP, 0x20010000)
    m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
    m.emu_start((BASE + entry_off) | 1, STOP, count=20000)
    assert m.reg_read(A.UC_ARM_REG_R0) == 0


m, logs = make_machine()

# 1. ON on stock -> writes 1
invoke(m, on_off)
assert struct.unpack("<I", m.mem_read(RCRAW_PAGE + EN_OFF, 4))[0] == 1
assert struct.unpack("<I", m.mem_read(RCRAW_PAGE + EN_OFF - 4, 4))[0] == 0xCAFEBABE
assert struct.unpack("<I", m.mem_read(RCRAW_PAGE + EN_OFF + 4, 4))[0] == 0x11223344
assert "rcraw_en on=1 sw=w val=00000001 rdraw=00000000 d534=00000100 w1=1,1,12345000 w2=0,0,18000000,01800000,0007 q7c8=00000400" in logs[-1], logs[-1]

# 2. ON again -> already (a)
invoke(m, on_off)
assert "rcraw_en on=1 sw=a val=00000001" in logs[-1], logs[-1]

# 3. OFF -> restores 0
invoke(m, off_off)
assert struct.unpack("<I", m.mem_read(RCRAW_PAGE + EN_OFF, 4))[0] == 0
assert "rcraw_en on=0 sw=w val=00000000" in logs[-1], logs[-1]

# 4. OFF again -> already (a)
invoke(m, off_off)
assert "rcraw_en on=0 sw=a val=00000000" in logs[-1], logs[-1]

# 5. Foreign value -> untouched on ON and OFF (sw=x)
m.mem_write(RCRAW_PAGE + EN_OFF, struct.pack("<I", 0xDEADBEEF))
invoke(m, on_off)
assert struct.unpack("<I", m.mem_read(RCRAW_PAGE + EN_OFF, 4))[0] == 0xDEADBEEF
assert "rcraw_en on=1 sw=x val=deadbeef" in logs[-1], logs[-1]

invoke(m, off_off)
assert struct.unpack("<I", m.mem_read(RCRAW_PAGE + EN_OFF, 4))[0] == 0xDEADBEEF
assert "rcraw_en on=0 sw=x val=deadbeef" in logs[-1], logs[-1]

print(f"UNICORN RCRAW_ENABLE_TEST PASS ({len(logs)} runs): {logs[0].strip()}")
