"""Offline Unicorn test of the SMOD v2 MenuTouchFull build (no camera).

Usage: python mods/menu_touch_full/test_mtf_v2_unicorn.py BUILD_DIR

Loads the packed mod the way Mod Loader v2 does (code at an mmap base, zeroed bss after it,
hook templates with the base fixup) at two different bases, then drives the patched firmware
prologues: forwarding with the state cleared, lifecycle ON/OFF, the press capture and the
release guards. Firmware callees are replaced by host stubs; menu handling beyond the early
guards is not exercised here (E0520/E0522 cover it on the camera with the v1 build).
"""
import struct
import sys
from pathlib import Path

import unicorn as uc
from unicorn import arm_const as A

B = Path(sys.argv[1])
mod = (B / "MenuTouchFull.mod").read_bytes()
syms = {}
for line in (B / "mtf.sym").read_text().splitlines():
    parts = line.split()
    if len(parts) == 3:
        syms[parts[2]] = int(parts[0], 16)

SMOD = 0x444F4D53
FW = 0x06309000
HOOK_REL, HOOK_PRS = 0x06309D48, 0x06309E54
ORIG_REL = bytes.fromhex("2de9f04788b04ff014080122")
ORIG_PRS = bytes.fromhex("2de9f04788b04ff014090122")
GET_POS = 0x06309018
GET_SYSTEM = 0x06308D30
PHYSICAL_SYSTEM = 0x956F0
STACK, STOP, MSG = 0x50000000, 0x5F000000, 0x52000000
STATE_BYTES = 632


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok:", msg)


magic, ver, flags, name, code_size, fnv, bss, on_off, off_off, hook_count = struct.unpack_from("<III32s6I", mod)
check(magic == SMOD and ver == 2 and name.rstrip(b"\0") == b"Menu Touch Full", "SMOD v2 header, name 'Menu Touch Full'")
check(bss == STATE_BYTES and syms["__smod_bss"] == code_size, f"bss {bss} B starts at code end ({code_size:#x})")
check(on_off == off_off == (syms["mt_life"] | 1), "on/off lifecycle entries point at mt_life")
hooks = []
for i in range(hook_count):
    t, size, fix = struct.unpack_from("<IBB", mod, 68 + 40 * i)
    orig = mod[68 + 40 * i + 8:68 + 40 * i + 8 + size]
    tmpl = mod[68 + 40 * i + 24:68 + 40 * i + 24 + size]
    hooks.append((t, orig, tmpl, fix))
check([(h[0], h[1]) for h in hooks] == [(HOOK_REL, ORIG_REL), (HOOK_PRS, ORIG_PRS)],
      "hook targets and original bytes equal the verified v1 prologues")
code = mod[68 + 40 * hook_count:68 + 40 * hook_count + code_size]
tail = mod[68 + 40 * hook_count + code_size:]
if flags & 4:   # SMOD_V2_FLAG_RELOC: relocation table after the code (camera layout: bias 0)
    check(tail[:4] == b"RLC1" and len(code) == code_size, f"code then a {len(tail)} B relocation table")
else:
    check(len(code) == code_size and not tail, "file ends with the code")


class Loaded:
    def __init__(self, base, system):
        self.base = base
        self.calls = []
        self.pos = (100, 200)
        self.system = system
        m = self.m = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
        m.mem_map(base, (code_size + bss + 0x1FFF) & ~0xFFF)
        m.mem_write(base, code)
        m.mem_write(base + code_size + bss, b"\xa5" * 64)          # sentinel after the bss
        m.mem_map(GET_SYSTEM & ~0xFFF, 0x1000)
        m.mem_map(FW, 0x1000)
        # Firmware continuation after each replayed prologue: check the replayed registers, then
        # add sp,#0x20; movs r0,#0x5a (rel) / #0x6b (prs); pop.w {r4-r10,pc}
        for target, orig in ((HOOK_REL, ORIG_REL), (HOOK_PRS, ORIG_PRS)):
            m.mem_write(target, orig)
            ret = 0x5A if target == HOOK_REL else 0x6B
            m.mem_write(target + 12, bytes([0x08, 0xB0, ret, 0x20, 0xBD, 0xE8, 0xF0, 0x87]))
        for target, orig, tmpl, fix in hooks:          # what the core writes at ON
            patch = bytearray(tmpl)
            if fix != 0xFF:
                struct.pack_into("<I", patch, fix, struct.unpack_from("<I", patch, fix)[0] + base)
            m.mem_write(target, bytes(patch))
        m.mem_map(STACK - 0x10000, 0x10000)
        m.mem_map(STOP, 0x1000)
        m.mem_map(MSG, 0x1000)
        m.hook_add(uc.UC_HOOK_CODE, self.code)
        m.hook_add(uc.UC_HOOK_INTR, self.intr)
        m.hook_add(uc.UC_HOOK_MEM_UNMAPPED, self.unmapped)

    def unmapped(self, m, access, addr, size, value, _):
        raise AssertionError(f"unmapped access {addr:#x} at pc {m.reg_read(A.UC_ARM_REG_PC):#x}")

    def intr(self, m, intno, _):
        n = m.reg_read(A.UC_ARM_REG_R7)
        if n != 224:
            raise AssertionError(f"unexpected syscall {n}")
        m.reg_write(A.UC_ARM_REG_R0, 777)

    def code(self, m, pc, size, _):
        if pc == STOP:
            m.emu_stop()
        elif pc in (HOOK_REL + 12, HOOK_PRS + 12):
            want_r8 = pc == HOOK_REL + 12
            r8, r9, r2 = (m.reg_read(r) for r in (A.UC_ARM_REG_R8, A.UC_ARM_REG_R9, A.UC_ARM_REG_R2))
            ok = r2 == 1 and ((r8 == 0x14) if want_r8 else (r9 == 0x14))
            self.calls.append(("orig_rel" if want_r8 else "orig_prs", ok))
        elif pc == GET_SYSTEM:
            m.reg_write(A.UC_ARM_REG_R0, self.system)
            m.reg_write(A.UC_ARM_REG_PC, m.reg_read(A.UC_ARM_REG_LR))
        elif pc == GET_POS:
            r0, r1, r2, r3 = (m.reg_read(r) for r in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3))
            self.calls.append(("get_pos", r0, r1))
            m.mem_write(r2, struct.pack("<h", self.pos[0]))
            m.mem_write(r3, struct.pack("<h", self.pos[1]))
            m.reg_write(A.UC_ARM_REG_PC, m.reg_read(A.UC_ARM_REG_LR))
        elif FW <= pc < FW + 0x1000 and not (HOOK_REL <= pc < HOOK_REL + 20 or HOOK_PRS <= pc < HOOK_PRS + 20):
            raise AssertionError(f"unexpected firmware call {pc:#x}")
        elif pc >= 0x06000000 and not (FW <= pc < FW + 0x1000) and not (self.base <= pc < self.base + code_size):
            raise AssertionError(f"unexpected call outside the stubs {pc:#x}")

    def run(self, entry, *args):
        m = self.m
        for reg, v in zip((A.UC_ARM_REG_R0, A.UC_ARM_REG_R1), args):
            m.reg_write(reg, v)
        sp = STACK - 0x100
        m.reg_write(A.UC_ARM_REG_SP, sp)
        m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
        for r in (A.UC_ARM_REG_R4, A.UC_ARM_REG_R10):
            m.reg_write(r, 0x1234)
        m.emu_start(entry | 1, STOP, count=200000)
        assert m.reg_read(A.UC_ARM_REG_SP) == sp, "stack balanced"
        assert m.reg_read(A.UC_ARM_REG_R4) == 0x1234 and m.reg_read(A.UC_ARM_REG_R10) == 0x1234, "callee-saved kept"
        return m.reg_read(A.UC_ARM_REG_R0)

    def state(self):
        raw = bytes(self.m.mem_read(self.base + code_size, STATE_BYTES))
        f = struct.unpack_from("<14I", raw)
        keys = ("magic", "version", "enabled", "count", "inflight", "capacity", "last_action", "last_target",
                "last_x", "last_y", "last_err", "press_x", "press_y", "press_valid")
        return dict(zip(keys, f)), raw

    def message(self, mouse):
        self.m.mem_write(MSG, bytes(0x14) + bytes([mouse]) + bytes(11))
        return MSG


for base, system in ((0x40000000, PHYSICAL_SYSTEM), (0x48123000, 0x28AC8)):
    print(f"Load base {base:#x}")
    L = Loaded(base, system)
    patched = bytes(L.m.mem_read(HOOK_REL, 12))
    check(patched[:8] == bytes.fromhex("dff804f000bf00bf") and
          struct.unpack("<I", patched[8:])[0] == base + (syms["mt_full_release"] | 1),
          "release hook jumps to base + mt_full_release")
    check(struct.unpack("<I", bytes(L.m.mem_read(HOOK_PRS + 8, 4)))[0] == base + (syms["mt_full_press"] | 1),
          "press hook jumps to base + mt_full_press")
    st, raw = L.state()
    check(raw == bytes(STATE_BYTES), "state is zero before ON")
    L.calls.clear()
    check(L.run(HOOK_REL, system, L.message(0)) == 0x5A and L.calls == [("orig_rel", True)],
          "before ON: release forwards to the original with the replayed prologue and returns its result")
    check(L.run(HOOK_PRS, system, L.message(0)) == 0x6B and L.calls[-1] == ("orig_prs", True),
          "before ON: press forwards likewise")
    check(L.state()[1] == bytes(STATE_BYTES), "forwarding does not touch the state")

    L.run(base + on_off, 1)
    st, raw = L.state()
    check(st["magic"] == 0x4D544632 and st["version"] == 2 and st["enabled"] == 2 and st["capacity"] == 16
          and st["inflight"] == 0, "on(): MTF2 state published (version 2, enabled 2, capacity 16)")
    check(bytes(L.m.mem_read(base + code_size + bss, 64)) == b"\xa5" * 64, "nothing written past the bss")

    L.calls.clear()
    check(L.run(HOOK_PRS, system, L.message(1)) == 0x6B, "press with the menu system returns the original result")
    st, _ = L.state()
    check(L.calls == [("orig_prs", True), ("get_pos", system, 1)] and
          (st["press_x"], st["press_y"], st["press_valid"], st["inflight"]) == (100, 200, 1, 0),
          "press: original first, then position captured (100, 200)")

    L.calls.clear()
    foreign = PHYSICAL_SYSTEM if system != PHYSICAL_SYSTEM else 0x28AC8
    check(L.run(HOOK_REL, foreign, L.message(1)) == 0x5A and L.calls == [("orig_rel", True)],
          "release for another system object only forwards")
    check(L.state()[0]["press_valid"] == 1, "foreign release keeps the pending press")

    L.system = 0
    L.calls.clear()
    check(L.run(HOOK_REL, system, L.message(1)) == 0x5A and L.calls == [("orig_rel", True)],
          "unavailable singleton: release only forwards")
    L.system = system
    L.run(HOOK_REL, system, L.message(7))
    check(L.state()[0]["last_err"] == 99 and L.state()[0]["inflight"] == 0, "release with mouse index >= 4 is rejected (last_err 99)")

    L.pos = (32767, 32767)
    L.calls.clear()
    check(L.run(HOOK_REL, system, L.message(2)) == 0x5A, "release with an invalid position returns the original result")
    st, _ = L.state()
    check(L.calls == [("orig_rel", True), ("get_pos", system, 2)] and st["press_valid"] == 0 and st["count"] == 0
          and st["inflight"] == 0, "invalid position: press consumed, no action recorded, no menu lookup")

    L.run(base + off_off, 0)
    st, _ = L.state()
    check(st["magic"] == 0 and st["enabled"] == 0, "off(): magic and enabled cleared")
    L.calls.clear()
    L.pos = (10, 20)
    check(L.run(HOOK_PRS, system, L.message(1)) == 0x6B and L.calls == [("orig_prs", True)],
          "after OFF a thread still entering the hook only forwards")

print("ALL MTF v2 CHECKS PASSED")
