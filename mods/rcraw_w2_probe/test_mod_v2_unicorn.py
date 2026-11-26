"""Offline Unicorn test of the SMOD v2 RCRAW_W2_Probe build (no camera).

Usage: python mods/rcraw_w2_probe/test_mod_v2_unicorn.py BUILD_DIR
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import unicorn as uc
from unicorn import arm_const as A

BUILD_DIR = Path(sys.argv[1])
mod = (BUILD_DIR / "RCRAW_W2_Probe.mod").read_bytes()
magic, ver, flags, name, code_size, fnv, bss, on_off, off_off, hooks = struct.unpack_from("<III32s6I", mod)
assert magic == 0x444F4D53 and ver == 2 and name.rstrip(b"\0") == b"RCRAW_W2_Probe" and hooks == 0
code = mod[68 : 68 + code_size]

IS_A6400A = "--target=ilce_6400a" in sys.argv or (
    "--target" in sys.argv and sys.argv[sys.argv.index("--target") + 1] == "ilce_6400a"
) or b"\x44\xf2\x29\x45" in code or b"\xbc\x95\x42\x04" in code
RAW_PGOFF = 0x04429 if IS_A6400A else 0x04408
RAW_OFF = 0x5BC if IS_A6400A else 0x0EC
AIF0_PAGE = 0x30000000
AIF1_PAGE = 0x30100000
RAW_EXT_PAGE = 0x30200000
STOP = 0x31000000
BASE = 0x40AB3000


def run_probe(on: int) -> str:
    m = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
    map_sz = ((len(code) + 0x2000) + 0xFFF) & ~0xFFF
    m.mem_map(BASE, map_sz)
    m.mem_write(BASE, code)

    for page in (AIF0_PAGE, AIF1_PAGE, RAW_EXT_PAGE, STOP):
        m.mem_map(page, 0x1000)
    m.mem_map(0x20000000, 0x10000)

    # Seed AIF0 W1 (+0x400), AIF0 W2 (+0x480), AIF1 W2 (+0x480), and SET_RAW_EXTERNAL_OUTPUT (+0x0EC/+0x0F4)
    m.mem_write(AIF0_PAGE + 0x400, struct.pack("<II", 1, 0x80000000))
    m.mem_write(AIF0_PAGE + 0x410, struct.pack("<I", 0x12345000))
    m.mem_write(AIF0_PAGE + 0x424, struct.pack("<I", 0x00400000))
    m.mem_write(AIF0_PAGE + 0x42C, struct.pack("<I", 0x0003))

    m.mem_write(AIF0_PAGE + 0x480, struct.pack("<II", 1, 0x80000000))
    m.mem_write(AIF0_PAGE + 0x490, struct.pack("<I", 0x18000000))
    m.mem_write(AIF0_PAGE + 0x4A4, struct.pack("<I", 0x01800000))
    m.mem_write(AIF0_PAGE + 0x4AC, struct.pack("<I", 0x0007))

    m.mem_write(AIF1_PAGE + 0x480, struct.pack("<II", 0, 0))
    m.mem_write(AIF1_PAGE + 0x490, struct.pack("<I", 0x19000000))
    m.mem_write(AIF1_PAGE + 0x4A4, struct.pack("<I", 0x00200000))
    m.mem_write(AIF1_PAGE + 0x4AC, struct.pack("<I", 0x0001))

    m.mem_write(RAW_EXT_PAGE + RAW_OFF, b"\x01")
    m.mem_write(RAW_EXT_PAGE + RAW_OFF + 8, struct.pack("<I", 0x00BF0274))

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
                assert (a[1] & 3) == 0, f"/dev/mem must be O_RDONLY, got flags={a[1]:#x}"
                ret = 3
            elif path == "/tmp_bt/mods/rcraw_w2_probe.log":
                ret = 4
            else:
                raise AssertionError(f"unexpected open {path}")
        elif nr == 192:  # mmap2
            assert a[1:5] == [4096, 1, 1, 3], f"unexpected mmap2 args {a}"
            ret = {0xF3002: AIF0_PAGE, 0xF3102: AIF1_PAGE, RAW_PGOFF: RAW_EXT_PAGE}[a[5]]
        elif nr == 4 and a[0] == 4:  # write
            logs.append(bytes(mu.mem_read(a[1], a[2])).decode())
            ret = a[2]
        elif nr in (6, 91):  # close, munmap
            ret = 0
        else:
            raise AssertionError(f"unexpected syscall nr={nr} args={a}")
        mu.reg_write(A.UC_ARM_REG_R0, ret & 0xFFFFFFFF)

    m.hook_add(uc.UC_HOOK_INTR, intr)
    m.reg_write(A.UC_ARM_REG_SP, 0x2000F000)
    m.reg_write(A.UC_ARM_REG_R0, on)
    m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
    m.emu_start((BASE + (on_off if on else off_off)) | 1, STOP, timeout=500000)
    assert m.reg_read(A.UC_ARM_REG_R0) == 0
    return "".join(logs)


log_on = run_probe(1)
log_off = run_probe(0)
print("ON :", log_on.strip())
print("OFF:", log_off.strip())
assert log_on == "rcraw_w2 on=1 w1=1,1,12345000,00400000,0003 w2=1,1,18000000,01800000,0007 w2b=0,0,19000000,00200000,0001 ext=01,00bf0274\n"
assert log_off == "rcraw_w2 on=0 w1=1,1,12345000,00400000,0003 w2=1,1,18000000,01800000,0007 w2b=0,0,19000000,00200000,0001 ext=01,00bf0274\n"
print("UNICORN RCRAW_W2_PROBE PASS")
