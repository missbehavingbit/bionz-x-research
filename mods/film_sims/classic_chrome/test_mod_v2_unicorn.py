"""Offline Unicorn test of the SMOD v2 Classic Chrome mod for ILCE-6100 (no camera).

Usage: python3 mods/film_sims/classic_chrome/test_mod_v2_unicorn.py BUILD_DIR

Loads ClassicChrome.mod the way Mod Loader v2 does (relocatable code at an arbitrary base)
and calls its lifecycle entry against emulated /dev/mem pages. Checks: ON/OFF from stock
and Classic Chrome, repeated ON/OFF idempotence, foreign content left untouched, and
every byte around the four targets (including the 9 EC exponent words) unchanged.
"""
import re
import struct
import sys
from pathlib import Path

import unicorn as uc
from unicorn import arm_const as A

B = Path(sys.argv[1])
mod = (B / "ClassicChrome.mod").read_bytes()
magic, ver, flags, name, code_size, fnv, bss, on_off, off_off, hooks = struct.unpack_from("<III32s6I", mod)
assert magic == 0x444F4D53 and ver == 2 and name.rstrip(b"\0") == b"Classic Chrome" and hooks == 0
assert flags == 16, f"expected SMOD_V2_FLAG_ACTION (16), got {flags}"
code = mod[68:68 + code_size]
hdr = (B / "classic_chrome_data.h").read_text()


def arr(n):
    return [int(v, 0) for v in re.search(rf"{n}\[\d+\] = \{{([^}}]*)\}}", hdr).group(1).split(",")]


ec_stock, ec_chrome = (struct.pack("<96I", *arr(n)) for n in ("EC_GAMMA_STOCK", "EC_GAMMA_CHROME"))
fc_stock, fc_chrome = (struct.pack("<96H", *arr(n)) for n in ("FC_GAMMA_STOCK", "FC_GAMMA_CHROME"))
a0_stock, a0_chrome = (struct.pack("<9h", *arr(n)) for n in ("COLOR_A0_STOCK", "COLOR_A0_CHROME"))
a1_stock, a1_chrome = (struct.pack("<9h", *arr(n)) for n in ("COLOR_A1_STOCK", "COLOR_A1_CHROME"))

def _define_u32(name, default):
    m = re.search(rf"#define\s+{name}\s+(0x[0-9a-fA-F]+|\d+)u?", hdr)
    return int(m.group(1), 0) if m else default


EC_PA = _define_u32("EC_GAMMA_PA", 0x03E553B0)
FC_PA = _define_u32("FC_GAMMA_PA", 0x03E8F1D4)
A0_PA = _define_u32("COLOR_A0_PA", 0x0444A254)
A1_PA = _define_u32("COLOR_A1_PA", 0x0444A266)
EC_PAGE, FC_PAGE, C_PAGE, STOP, BASE = 0x30000000, 0x30100000, 0x30200000, 0x31000000, 0x40AB3000
EC_OFF, FC_OFF, A0_OFF, A1_OFF = EC_PA & 0xFFF, FC_PA & 0xFFF, A0_PA & 0xFFF, A1_PA & 0xFFF


def run(on, ec_in, fc_in, a0_in, a1_in):
    m = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
    map_sz = ((len(code) + 0x2000) + 0xFFF) & ~0xFFF
    m.mem_map(BASE, map_sz)
    m.mem_write(BASE, code)
    for page in (EC_PAGE, FC_PAGE, C_PAGE):
        m.mem_map(page, 0x1000)
        m.mem_write(page, b"\x5a" * 0x1000)
    m.mem_write(EC_PAGE + EC_OFF, ec_in)
    m.mem_write(FC_PAGE + FC_OFF, fc_in)
    m.mem_write(C_PAGE + A0_OFF, a0_in)
    m.mem_write(C_PAGE + A1_OFF, a1_in)
    m.mem_map(0x20000000, 0x10000)
    m.mem_map(STOP, 0x1000)
    log = []

    def intr(mu, _n, _):
        nr = mu.reg_read(A.UC_ARM_REG_R7)
        a = [mu.reg_read(r) for r in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2,
                                      A.UC_ARM_REG_R3, A.UC_ARM_REG_R4, A.UC_ARM_REG_R5)]
        ret = 0
        if nr == 5:
            path = bytes(mu.mem_read(a[0], 80)).split(b"\0")[0].decode(errors="replace")
            ret = {"/dev/mem": 3, "/tmp_bt/mods/classic_chrome.log": 4}[path]
        elif nr == 192:
            assert a[1:5] == [4096, 3, 1, 3]
            ret = {EC_PA >> 12: EC_PAGE, FC_PA >> 12: FC_PAGE, A0_PA >> 12: C_PAGE}[a[5]]
        elif nr == 4:
            data = bytes(mu.mem_read(a[1], a[2]))
            if a[0] == 4:
                log.append(data.decode())
            ret = len(data)
        elif nr not in (6, 91):
            raise AssertionError(f"unexpected syscall {nr}")
        mu.reg_write(A.UC_ARM_REG_R0, ret)

    m.hook_add(uc.UC_HOOK_INTR, intr)
    m.reg_write(A.UC_ARM_REG_R0, on)
    m.reg_write(A.UC_ARM_REG_SP, 0x2000F000)
    m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
    m.emu_start((BASE + (on_off & ~1)) | 1, STOP, count=2000000)
    ec_p = bytes(m.mem_read(EC_PAGE, 0x1000))
    fc_p = bytes(m.mem_read(FC_PAGE, 0x1000))
    c_p = bytes(m.mem_read(C_PAGE, 0x1000))
    assert ec_p[:EC_OFF] == b"\x5a" * EC_OFF and ec_p[EC_OFF + 384:] == b"\x5a" * (0x1000 - EC_OFF - 384)
    assert fc_p[:FC_OFF] == b"\x5a" * FC_OFF and fc_p[FC_OFF + 192:] == b"\x5a" * (0x1000 - FC_OFF - 192)
    assert c_p[:A0_OFF] == b"\x5a" * A0_OFF and c_p[A1_OFF + 18:] == b"\x5a" * (0x1000 - A1_OFF - 18)
    return (
        ec_p[EC_OFF:EC_OFF + 384],
        fc_p[FC_OFF:FC_OFF + 192],
        c_p[A0_OFF:A0_OFF + 18],
        c_p[A1_OFF:A1_OFF + 18],
        "".join(log).strip(),
    )


def case(label, on, ec_in, fc_in, a0_in, a1_in, want_ec, want_fc, want_a0, want_a1, want_log):
    got = run(on, ec_in, fc_in, a0_in, a1_in)
    assert got == (want_ec, want_fc, want_a0, want_a1, want_log), (label, got[-1], want_log)
    print(f"PASS {label}: {got[-1]}")


case("ON from stock", 1, ec_stock, fc_stock, a0_stock, a1_stock,
     ec_chrome, fc_chrome, a0_chrome, a1_chrome,
     "classic_chrome on=1 ec=w fc=w a0=w a1=w")
case("ON when already active", 1, ec_chrome, fc_chrome, a0_chrome, a1_chrome,
     ec_chrome, fc_chrome, a0_chrome, a1_chrome,
     "classic_chrome on=1 ec=a fc=a a0=a a1=a")
case("OFF from Classic Chrome", 0, ec_chrome, fc_chrome, a0_chrome, a1_chrome,
     ec_stock, fc_stock, a0_stock, a1_stock,
     "classic_chrome on=0 ec=w fc=w a0=w a1=w")
case("OFF when already stock", 0, ec_stock, fc_stock, a0_stock, a1_stock,
     ec_stock, fc_stock, a0_stock, a1_stock,
     "classic_chrome on=0 ec=a fc=a a0=a a1=a")
bad_ec, bad_a1 = b"\x11" * 384, b"\x22" * 18
case("ON with foreign EC and A1 leaves foreign untouched", 1, bad_ec, fc_stock, a0_stock, bad_a1,
     bad_ec, fc_chrome, a0_chrome, bad_a1,
     "classic_chrome on=1 ec=x fc=w a0=w a1=x")
print("ALL 5 CLASSIC CHROME UNICORN CASES PASSED")
