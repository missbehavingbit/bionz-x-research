"""Offline Unicorn test of the packed ARRI color SMOD lifecycle entry (no camera)."""
import json, struct, sys
from pathlib import Path
import unicorn as uc
from unicorn import arm_const as A
root = Path(__file__).resolve().parents[3]
mod = Path(sys.argv[1]).read_bytes()
magic, ver, flags, name, code_addr, code_size, *_ = struct.unpack_from("<III32sIIIIII", mod)
assert magic == 0x444F4D53 and flags == 2
code = mod[68:68 + code_size]
c = json.loads((root / "artifacts/analysis/arri-movie-color-2026-11-16/arri-matrix-candidates.json").read_text())
stock = struct.pack("<9h", *sum(c["B8_stock"], [])); new = struct.pack("<9h", *sum(c["candidates"]["rec709"]["q10"], []))
PAGE, STOP, OFF = 0x30000000, 0x31000000, 0x1d4
def run(on, entry):
    m = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
    m.mem_map(code_addr & ~0xfff, 0x2000); m.mem_write(code_addr, code)
    m.mem_map(PAGE, 0x1000); m.mem_write(PAGE, b"\xa5" * 0x1000); m.mem_write(PAGE + OFF, entry)
    m.mem_map(0x20000000, 0x10000); m.mem_map(STOP, 0x1000)
    log = []
    def intr(mu, n, _):
        nr = mu.reg_read(A.UC_ARM_REG_R7); a = [mu.reg_read(r) for r in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3, A.UC_ARM_REG_R4, A.UC_ARM_REG_R5)]
        path = lambda p: bytes(mu.mem_read(p, 40)).split(b"\0")[0].decode()
        ret = 0
        if nr == 5: ret = {"/dev/mem": 3, "/tmp_bt/mods/arri_color.log": 4}[path(a[0])]
        elif nr == 192: assert a[1:6] == [4096, 3, 1, 3, 0x0444b1d4 >> 12]; ret = PAGE
        elif nr == 4: log.append(bytes(mu.mem_read(a[1], a[2])).decode())
        elif nr not in (6, 91): raise AssertionError(nr)
        mu.reg_write(A.UC_ARM_REG_R0, ret)
    m.hook_add(uc.UC_HOOK_INTR, intr)
    m.reg_write(A.UC_ARM_REG_R0, on); m.reg_write(A.UC_ARM_REG_SP, 0x2000f000); m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
    m.emu_start(code_addr | 1, STOP, count=100000)
    pg = bytes(m.mem_read(PAGE, 0x1000))
    assert pg[:OFF] == b"\xa5" * OFF and pg[OFF + 18:] == b"\xa5" * (0x1000 - OFF - 18), "neighbours touched"
    return pg[OFF:OFF + 18], "".join(log).strip()
r, l = run(1, stock); assert r == new and l.endswith("w"), l; print("ON from stock:", l)
r, l = run(1, new); assert r == new and l.endswith("a"), l; print("ON again:", l)
r, l = run(0, new); assert r == stock and l.endswith("w"), l; print("OFF from ARRI:", l)
odd = bytearray(stock); odd[0] ^= 1
r, l = run(1, bytes(odd)); assert r == bytes(odd) and l.endswith("x"), l; print("ON on foreign:", l)
print("PASS: neighbouring bytes untouched in every case")
