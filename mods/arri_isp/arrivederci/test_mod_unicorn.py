"""Offline Unicorn test of the packed Arrivederci SMOD lifecycle entry (no camera)."""
import json
import struct
import sys
from pathlib import Path

import unicorn as uc
from unicorn import arm_const as A

root = Path(__file__).resolve().parents[3]
mod = Path(sys.argv[1]).read_bytes()
magic, ver, flags, name, code_addr, code_size, *_ = struct.unpack_from("<III32sIIIIII", mod)
assert magic == 0x444F4D53 and flags == 2 and name.rstrip(b"\0") == b"Arrivederci"
code = mod[68:68 + code_size]
g = root / "artifacts/analysis/arri-gamma-test-block-2026-11-16"
g_stock = (g / "record31-stock.bin").read_bytes()
g_arri = (g / "record31-arri-classic-rec709.bin").read_bytes()
c = json.loads((root / "artifacts/analysis/arri-movie-color-2026-11-16/arri-matrix-candidates.json").read_text())
c_stock = struct.pack("<9h", *sum(c["B8_stock"], []))
c_arri = struct.pack("<9h", *sum(c["candidates"]["rec709"]["q10"], []))
GPAGE, CPAGE, STOP = 0x30000000, 0x30100000, 0x31000000
GOFF, COFF = 0x63c, 0x1d4


def run(on, grec, centry):
    m = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
    m.mem_map(code_addr & ~0xfff, 0x2000)
    m.mem_write(code_addr, code)
    for page, off, data in ((GPAGE, GOFF, grec), (CPAGE, COFF, centry)):
        m.mem_map(page, 0x1000)
        m.mem_write(page, b"\x5a" * 0x1000)
        m.mem_write(page + off, data)
    m.mem_map(0x20000000, 0x10000)
    m.mem_map(STOP, 0x1000)
    log = []

    def intr(mu, n, _):
        nr = mu.reg_read(A.UC_ARM_REG_R7)
        a = [mu.reg_read(r) for r in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2,
                                      A.UC_ARM_REG_R3, A.UC_ARM_REG_R4, A.UC_ARM_REG_R5)]
        ret = 0
        if nr == 5:
            path = bytes(mu.mem_read(a[0], 40)).split(b"\0")[0].decode(errors="replace")
            ret = {"/dev/mem": 3, "/tmp_bt/mods/arrivederci.log": 4}[path]
        elif nr == 192:
            assert a[1:5] == [4096, 3, 1, 3]
            ret = {0x03e5363c >> 12: GPAGE, 0x0444b1d4 >> 12: CPAGE}[a[5]]
        elif nr == 4:
            log.append(bytes(mu.mem_read(a[1], a[2])).decode())
        elif nr not in (6, 91):
            raise AssertionError(f"unexpected syscall {nr}")
        mu.reg_write(A.UC_ARM_REG_R0, ret)

    m.hook_add(uc.UC_HOOK_INTR, intr)
    m.reg_write(A.UC_ARM_REG_R0, on)
    m.reg_write(A.UC_ARM_REG_SP, 0x2000f000)
    m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
    m.emu_start(code_addr | 1, STOP, count=400000)
    gp, cp = bytes(m.mem_read(GPAGE, 0x1000)), bytes(m.mem_read(CPAGE, 0x1000))
    assert gp[:GOFF] == b"\x5a" * GOFF and gp[GOFF + 420:] == b"\x5a" * (0x1000 - GOFF - 420)
    assert cp[:COFF] == b"\x5a" * COFF and cp[COFF + 18:] == b"\x5a" * (0x1000 - COFF - 18)
    return gp[GOFF:GOFF + 420], cp[COFF:COFF + 18], "".join(log).strip()


g, cc, l = run(1, g_stock, c_stock)
assert g == g_arri and cc == c_arri and l == "arrivederci on=1 gamma=w color=w", l
print("ON from stock:", l)
g, cc, l = run(0, g_arri, c_arri)
assert g == g_stock and cc == c_stock and l == "arrivederci on=0 gamma=w color=w", l
print("OFF from ARRI:", l)
odd = bytearray(c_stock); odd[0] ^= 1
g, cc, l = run(1, g_arri, bytes(odd))
assert g == g_arri and cc == bytes(odd) and l == "arrivederci on=1 gamma=a color=x", l
print("ON mixed (gamma already, color foreign):", l)
print("PASS: both layers, neighbouring bytes untouched, exponent words preserved:", g[384:] == g_stock[384:])
