"""Offline Unicorn test of the SMOD v2 SilentShutterUnlock build (no camera).

Usage: python mods/silent_shutter_unlock/test_mod_v2_unicorn.py BUILD_DIR
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import unicorn as uc
from unicorn import arm_const as A

BUILD_DIR = Path(sys.argv[1])
mod = (BUILD_DIR / "SilentShutterUnlock.mod").read_bytes()

magic, ver, flags, name, code_size, fnv, bss, on_off, off_off, hooks = struct.unpack_from(
    "<III32s6I", mod
)
assert magic == 0x444F4D53, f"bad magic {magic:#x}"
assert ver == 2, f"bad ver {ver}"
assert name.rstrip(b"\0") == b"Silent Unlock", name
assert bss == 80, f"unexpected bss {bss}"
assert hooks == 1, f"expected 1 hook, got {hooks}"
assert (flags & 4) != 0, "expected FLAG_RELOC (4)"

hook_off = 68
target_va, orig_len, fix_off, orig_bytes, tmpl_bytes = struct.unpack_from(
    "<IBB2x16s16s", mod, hook_off
)
assert target_va == 0x069888A0, f"unexpected target_va {target_va:#x}"
assert orig_len == 12, f"unexpected orig_len {orig_len}"
assert orig_bytes[:orig_len] == bytes.fromhex("2de9f04104464cf2b60000af"), orig_bytes.hex()
hook_entry_off = struct.unpack_from("<I", tmpl_bytes, fix_off)[0]
assert (hook_entry_off & 1) == 1, f"hook_entry_off must be Thumb: {hook_entry_off:#x}"

code_off = hook_off + hooks * 40
code = mod[code_off : code_off + code_size]

# Verify RLC1 trailer relocates 0x069888ad (libObj.so) and 0x056918b8 (libSysDef.so)
rlc_off = code_off + code_size
rlc_magic, rlc_libs, rlc_count = struct.unpack_from("<IHH", mod, rlc_off)
assert rlc_magic == 0x31434C52, f"expected RLC1 trailer, got {rlc_magic:#x}"
assert rlc_libs == 2 and rlc_count == 2, f"expected 2 libs / 2 words, got {rlc_libs}/{rlc_count}"
libs = {}
for i in range(rlc_libs):
    lib_lo, lib_hi, lib_name = struct.unpack_from("<II24s", mod, rlc_off + 8 + i * 32)
    libs[i] = (lib_lo, lib_hi, lib_name.rstrip(b"\0").decode())
assert {v[2] for v in libs.values()} == {"libObj.so", "libSysDef.so"}, libs

reloc_vals = {}
rel_base = rlc_off + 8 + rlc_libs * 32
for i in range(rlc_count):
    rel_entry = struct.unpack_from("<I", mod, rel_base + i * 4)[0]
    lib_idx = rel_entry >> 24
    word_off = rel_entry & 0xFFFFFF
    val = struct.unpack_from("<I", code, word_off)[0]
    reloc_vals[libs[lib_idx][2]] = val
assert reloc_vals == {"libObj.so": 0x069888AD, "libSysDef.so": 0x056918B8}, reloc_vals

BASE = 0x40AB3000
LIBOBJ_PAGE = 0x06988000
SYSDEF_RW_BASE = 0x05691000
SYSDEF_RW_SIZE = 0x160000
INH_REF_TBL_VA = 0x056918B8
INH_TBL_FILE_OFF = 0x188B8
INH_NUM_FEAT = 2190
INH_WORDS_PER_FEAT = 160
INH_TBL_BYTES_LEN = INH_NUM_FEAT * INH_WORDS_PER_FEAT * 4
SILENT_FACTORS = [0x0A83, 0x0DE2, 0x114F, 0x1296, 0x13BB]
FEAT_SILENT_SHUTTER = 0x0832
SILENT_MODE_FACTORS = (
    list(range(0x094B, 0x0963))
    + list(range(0x0967, 0x0969))
    + list(range(0x0B1F, 0x0B4C))
    + [0x0B8B, 0x0B8D]
    + list(range(0x0CDB, 0x0CE2))
)
assert len(SILENT_MODE_FACTORS) == 80

ORIG_INH_TBL = (
    Path(__file__).resolve().parents[2]
    / "artifacts"
    / "analysis"
    / "cam-usr"
    / "lib"
    / "libSysDef.so"
).read_bytes()[INH_TBL_FILE_OFF : INH_TBL_FILE_OFF + INH_TBL_BYTES_LEN]
assert len(ORIG_INH_TBL) == INH_TBL_BYTES_LEN

UNLOCK_RANGES = [
    (0x00B6, 0x00B6),
    (0x00B8, 0x00C4),
    (0x036D, 0x0376),
    (0x0379, 0x0379),
    (0x037B, 0x0387),
    (0x03E8, 0x03E9),
    (0x041B, 0x041F),
    (0x0509, 0x050A),
    (0x07A2, 0x07B7),
]
UNLOCK_FEATS = [f for lo, hi in UNLOCK_RANGES for f in range(lo, hi + 1)]
assert len(UNLOCK_FEATS) == 69
SENTINEL_UNTOUCHED = [0x0118, 0x0146, 0x051B]
MODE_SENTINEL_UNTOUCHED = [0x113C, 0x0B88]

RESUME_ADDR = 0x069888AC
OUT_BUF_ADDR = 0x20008000
SELF_PTR = 0x12345678
STOP = 0x31000000

# Mock original body at 0x069888AC (after the 12-byte prologue has executed):
# By then: r4 == self, r0 == 0xc0b6, r1..r3 == args 2..4, [sp,#24] == dst_val, [sp,#28] == out_buf
# Stub writes:
#   out_buf[0] = r4 (self)
#   out_buf[1] = r0 (0xc0b6)
#   out_buf[2] = r1 (src_param)
#   out_buf[3] = r3 (dst_param)
#   out_buf[4] = [sp, #24] (dst_val)
#   r0 = 0x3a
#   pop {r4, r5, r6, r7, r8, pc}
MOCK_ORIG_CODE = (
    # ldr r5, [sp, #28]  (out_buf)  -> f8dd 501c
    bytes.fromhex("ddf81c50")
    # str r4, [r5, #0]              -> 602c
    + bytes.fromhex("2c60")
    # str r0, [r5, #4]              -> 6068
    + bytes.fromhex("6860")
    # str r1, [r5, #8]              -> 60a9
    + bytes.fromhex("a960")
    # str r3, [r5, #12]             -> 60eb
    + bytes.fromhex("eb60")
    # ldr r6, [sp, #24] (dst_val)   -> ddf8 1860
    + bytes.fromhex("ddf81860")
    # str r6, [r5, #16]             -> 612e
    + bytes.fromhex("2e61")
    # movs r0, #0x3a                -> 203a
    + bytes.fromhex("3a20")
    # ldmia.w sp!, {r4-r8, pc}      -> e8bd 81f0
    + bytes.fromhex("bde8f081")
)


def make_machine() -> tuple[uc.Uc, list[str]]:
    m = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
    map_sz = ((len(code) + bss + 0x2000) + 0xFFF) & ~0xFFF
    m.mem_map(BASE, map_sz)
    m.mem_write(BASE, code)
    m.mem_write(BASE + len(code), b"\x00" * bss)

    m.mem_map(LIBOBJ_PAGE, 0x1000)
    patched_tmpl = bytearray(tmpl_bytes[:orig_len])
    struct.pack_into("<I", patched_tmpl, fix_off, BASE + hook_entry_off)
    m.mem_write(target_va, bytes(patched_tmpl))
    m.mem_write(RESUME_ADDR, MOCK_ORIG_CODE)

    m.mem_map(0x20000000, 0x10000)
    m.mem_map(SYSDEF_RW_BASE, SYSDEF_RW_SIZE)
    m.mem_write(INH_REF_TBL_VA, ORIG_INH_TBL)
    m.mem_map(STOP, 0x1000)

    logs: list[str] = []

    def intr(mu, _n, _):
        nr = mu.reg_read(A.UC_ARM_REG_R7)
        a = [mu.reg_read(r) for r in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2)]
        ret = 0
        if nr == 5:  # open
            path = bytes(mu.mem_read(a[0], 80)).split(b"\0")[0].decode(errors="replace")
            assert path == "/tmp_bt/mods/silent_shutter_unlock.log", f"unexpected open {path}"
            ret = 4
        elif nr == 4:  # write
            assert a[0] == 4
            logs.append(bytes(mu.mem_read(a[1], a[2])).decode())
            ret = a[2]
        elif nr == 6:  # close
            ret = 0
        else:
            raise AssertionError(f"unexpected syscall {nr}")
        mu.reg_write(A.UC_ARM_REG_R0, ret & 0xFFFFFFFF)

    m.hook_add(uc.UC_HOOK_INTR, intr)
    return m, logs


def call_life(m: uc.Uc, entry_off: int) -> None:
    m.reg_write(A.UC_ARM_REG_SP, 0x20010000)
    m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
    m.emu_start((BASE + entry_off) | 1, STOP, count=20000)
    assert m.reg_read(A.UC_ARM_REG_R0) == 0


def call_hook(
    m: uc.Uc, src_param: int, src_val: int, dst_param: int, dst_val: int
) -> tuple[int, list[int]]:
    m.mem_write(OUT_BUF_ADDR, struct.pack("<5I", 0, 0, 0, 0, 0))
    sp = 0x2000F000
    m.mem_write(sp, struct.pack("<II", dst_val, OUT_BUF_ADDR))
    m.reg_write(A.UC_ARM_REG_SP, sp)
    m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
    m.reg_write(A.UC_ARM_REG_R0, SELF_PTR)
    m.reg_write(A.UC_ARM_REG_R1, src_param)
    m.reg_write(A.UC_ARM_REG_R2, src_val)
    m.reg_write(A.UC_ARM_REG_R3, dst_param)
    m.emu_start(target_va | 1, STOP, count=20000)
    ret = m.reg_read(A.UC_ARM_REG_R0)
    out = list(struct.unpack("<5I", m.mem_read(OUT_BUF_ADDR, 20)))
    return ret, out


def read_inh_word(m: uc.Uc, feat: int, fac: int) -> int:
    addr = INH_REF_TBL_VA + (feat * INH_WORDS_PER_FEAT + (fac >> 5)) * 4
    return struct.unpack("<I", m.mem_read(addr, 4))[0]


def orig_inh_word(feat: int, fac: int) -> int:
    return struct.unpack_from("<I", ORIG_INH_TBL, (feat * INH_WORDS_PER_FEAT + (fac >> 5)) * 4)[0]


m, logs = make_machine()

# 0. Before ON: verify target bits are set in stock DefInh::sm_refTbl
exp_cleared = 0
for feat in UNLOCK_FEATS:
    for fac in SILENT_FACTORS:
        mask = 1 << (fac & 31)
        assert (read_inh_word(m, feat, fac) & mask) != 0, f"expected {fac:#x} set for {feat:#x}"
        exp_cleared += 1
for feat in SENTINEL_UNTOUCHED:
    for fac in SILENT_FACTORS:
        mask = 1 << (fac & 31)
        assert (read_inh_word(m, feat, fac) & mask) != 0, f"expected sentinel {fac:#x} set for {feat:#x}"
for fac in SILENT_MODE_FACTORS:
    mask = 1 << (fac & 31)
    assert (read_inh_word(m, FEAT_SILENT_SHUTTER, fac) & mask) != 0, f"expected mode {fac:#x} set for 0x832"
    exp_cleared += 1
for fac in MODE_SENTINEL_UNTOUCHED:
    mask = 1 << (fac & 31)
    assert (read_inh_word(m, FEAT_SILENT_SHUTTER, fac) & mask) != 0, f"expected mode sentinel {fac:#x} set for 0x832"
assert exp_cleared == 69 * 5 + 80, exp_cleared

# 1. While OFF, even unlocked pairs fall through to original handler
ret, out = call_hook(m, 0x9983, 0x451, 0x9A53, 0x100)
assert ret == 0x3A and out == [SELF_PTR, 0xC0B6, 0x9983, 0x9A53, 0x100], (hex(ret), out)

# 2. Toggle ON
call_life(m, on_off)
assert f"silent_unlock on=1 bypass=0x00000000 total=0x00000000 inh=0x{exp_cleared:08x}" in logs[-1], logs[-1]
inh_cleared = struct.unpack("<I", m.mem_read(BASE + len(code) + 16, 4))[0]
assert inh_cleared == exp_cleared, f"expected {exp_cleared} cleared bits, got {inh_cleared}"

# Check DefInh::sm_refTbl after ON: all 5 silent factors cleared on 69 features, 71 mode factors cleared on 0x832,
# while sentinel non-target features and sentinel non-mode factors remain untouched.
for feat in UNLOCK_FEATS:
    for fac in SILENT_FACTORS:
        got = read_inh_word(m, feat, fac)
        assert (got & (1 << (fac & 31))) == 0, f"feat {feat:#x} factor {fac:#x} still set"
for feat in SENTINEL_UNTOUCHED:
    for fac in SILENT_FACTORS:
        got = read_inh_word(m, feat, fac)
        exp = orig_inh_word(feat, fac)
        assert got == exp, f"sentinel {feat:#x} factor {fac:#x} modified: {got:#x} != {exp:#x}"
for fac in SILENT_MODE_FACTORS:
    got = read_inh_word(m, FEAT_SILENT_SHUTTER, fac)
    assert (got & (1 << (fac & 31))) == 0, f"0x832 mode factor {fac:#x} still set"
for fac in MODE_SENTINEL_UNTOUCHED:
    got = read_inh_word(m, FEAT_SILENT_SHUTTER, fac)
    exp = orig_inh_word(FEAT_SILENT_SHUTTER, fac)
    if fac == 0x0B88:
        # 0x0b8b (bit 11) and 0x0b8d (bit 13) share word 92 with 0x0b88 (bit 8)
        assert (got & (1 << (fac & 31))) != 0 and (got | (1 << 11) | (1 << 13)) == exp, (
            f"0x832 mode sentinel {fac:#x} modified: {got:#x} vs {exp:#x}"
        )
    else:
        assert got == exp, f"0x832 mode sentinel {fac:#x} modified: {got:#x} != {exp:#x}"

# 3. Unlocked pairs (both directions) return 0 and leave out_buf untouched
unlocked_pairs = [
    (0x9983, 0x9A53),  # Silent -> Picture Profile
    (0x9983, 0x9685),  # Silent -> PP Gamma Type
    (0x9983, 0x9754),  # Silent -> Picture Effect
    (0x9983, 0x9A19),  # Silent -> Picture Effect Gamma
    (0x9983, 0x95CE),  # Silent -> HDR Level
    (0x9983, 0x9818),  # Silent -> Auto HDR
    (0x9983, 0x95CB),  # Silent -> DRO Level
    (0x9983, 0x951E),  # Silent -> ISO (MFNR)
    (0x9983, 0x9811),  # Silent -> Multi Frame NR
    (0x9983, 0x9989),  # Silent -> Multi Shot NR Level
    (0x95C8, 0x9983),  # Exposure Mode -> Silent (P/A/S/M lock)
    (0x9B7F, 0x9983),  # Camera Recording Mode -> Silent
    (0xC00D, 0x9983),  # ExcMgr Master Shoot Mode -> Silent
    (0xC0F1, 0xA013),  # ExcMgr Mode Dial -> EXC_SILENT_SHUTTER_MODE
    (0x9A53, 0x9983),  # Picture Profile -> Silent
    (0xA013, 0x9A53),  # EXC_SILENT_SHUTTER_MODE -> PP
]
for sp, dp in unlocked_pairs:
    ret, out = call_hook(m, sp, 0x451, dp, 0x222)
    assert ret == 0 and out == [0, 0, 0, 0, 0], f"pair ({sp:#x}, {dp:#x}) failed: {ret:#x} {out}"

# 4. Stock-preserved pairs fall through to mock original with all 6 args intact
stock_pairs = [
    (0x9983, 0x956F),  # Flash (Strobe Mode)
    (0x9983, 0x9F62),  # Wireless Flash
    (0x9983, 0x95A4),  # Long Exposure NR
    (0x9983, 0x9B50),  # Flickerless
    (0x95C8, 0x9754),  # Unrelated pair
]
for sp, dp in stock_pairs:
    ret, out = call_hook(m, sp, 0x451, dp, 0x777)
    assert ret == 0x3A and out == [SELF_PTR, 0xC0B6, sp, dp, 0x777], (
        f"stock pair ({sp:#x}, {dp:#x}) failed: {ret:#x} {out}"
    )

# 5. Toggle OFF and verify log records exact bypass count (13) and total count (18), plus inh_cleared reset
call_life(m, off_off)
assert (
    f"silent_unlock on=0 bypass=0x{len(unlocked_pairs):08x} total=0x{len(unlocked_pairs)+len(stock_pairs):08x} inh=0x00000000"
    in logs[-1]
), logs[-1]

# 6. Verify OFF restores fall-through and byte-for-byte original DefInh::sm_refTbl
cur_tbl = bytes(m.mem_read(INH_REF_TBL_VA, INH_TBL_BYTES_LEN))
assert cur_tbl == ORIG_INH_TBL, "DefInh::sm_refTbl was not restored byte-for-byte on OFF"
ret, out = call_hook(m, 0x9983, 0x451, 0x9A53, 0x333)
assert ret == 0x3A and out == [SELF_PTR, 0xC0B6, 0x9983, 0x9A53, 0x333], (hex(ret), out)

print(
    f"UNICORN SILENT_SHUTTER_UNLOCK PASS: {len(unlocked_pairs)} unlocked, "
    f"{len(stock_pairs)} stock-preserved, inh_cleared={inh_cleared}, logs={len(logs)}"
)
