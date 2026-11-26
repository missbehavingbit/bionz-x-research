"""Offline Unicorn test of the packed ARRI gamma SMOD lifecycle entry (no camera)."""
import struct, sys
from pathlib import Path
import unicorn as uc
from unicorn import arm_const as A
root = Path(__file__).resolve().parents[3]
mod = Path(sys.argv[1]).read_bytes()
hdr = struct.unpack_from("<III32sIIIIII", mod)
magic, ver, flags, name, code_addr, code_size, st_a, st_s, hooks, fnv = hdr
assert magic == 0x444F4D53 and flags == 2 and hooks == 0
code = mod[68:68 + code_size]
d = root / "artifacts/analysis/arri-gamma-test-block-2026-11-16"
stock = (d / "record31-stock.bin").read_bytes(); arri = (d / "record31-arri-classic-rec709.bin").read_bytes()
PAGE, STOP = 0x30000000, 0x31000000
def run(on, page_init):
    m = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
    base = code_addr & ~0xfff
    m.mem_map(base, 0x2000); m.mem_write(code_addr, code)
    m.mem_map(PAGE, 0x1000); m.mem_write(PAGE, page_init)
    m.mem_map(0x20000000, 0x10000); m.mem_map(STOP, 0x1000)
    log, calls = [], []
    def intr(mu, n, _):
        nr = mu.reg_read(A.UC_ARM_REG_R7); a = [mu.reg_read(r) for r in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3, A.UC_ARM_REG_R4, A.UC_ARM_REG_R5)]
        path = lambda p: bytes(mu.mem_read(p, 40)).split(b"\0")[0].decode()
        calls.append(nr); ret = 0
        if nr == 5: ret = {"/dev/mem": 3, "/tmp_bt/mods/arri_gamma.log": 4}[path(a[0])]
        elif nr == 192:
            assert a[1] == 4096 and a[2] == 3 and a[3] == 1 and a[4] == 3 and a[5] == 0x03e5363c >> 12; ret = PAGE
        elif nr == 4: log.append(bytes(mu.mem_read(a[1], a[2])).decode())
        elif nr in (6, 91): ret = 0
        else: raise AssertionError(f"unexpected syscall {nr}")
        mu.reg_write(A.UC_ARM_REG_R0, ret)
    m.hook_add(uc.UC_HOOK_INTR, intr)
    m.reg_write(A.UC_ARM_REG_R0, on); m.reg_write(A.UC_ARM_REG_SP, 0x2000f000); m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
    m.emu_start(code_addr | 1, STOP, count=200000)
    return bytes(m.mem_read(PAGE + 0x63c, 420)), "".join(log).strip()
pg = lambda rec: bytes(0x63c) + rec + bytes(0x1000 - 0x63c - 420)
r, l = run(1, pg(stock)); assert r == arri and l.endswith("result=w"), l; print("ON from stock:", l)
r, l = run(1, pg(arri)); assert r == arri and l.endswith("result=a"), l; print("ON again:", l)
r, l = run(0, pg(arri)); assert r == stock and l.endswith("result=w"), l; print("OFF from ARRI:", l)
odd = bytearray(stock); odd[0] ^= 1
r, l = run(1, pg(bytes(odd))); assert r == bytes(odd) and l.endswith("result=x"), l; print("ON on foreign content:", l)
r, l = run(0, pg(stock)); assert r == stock and l.endswith("result=a"), l; print("OFF when stock:", l)
print("PASS: exponent words untouched in all cases:", r[384:] == stock[384:])
