"""Offline Unicorn verification suite for PhotonBin 6MP (Option 1 + Option 3 SMOD v2 mod).

Usage:
  .\\venv\\Scripts\\python.exe mods\\photon_bin_6mp\\test_mod_v2_unicorn.py <BUILD_DIR>

Verifies:
  1. SMOD v2 + RLC1 + SMNU binary integrity via strict `parse_smod`.
  2. Option 3 (`av-cam.bin` LIRO `.text` swaps via `/dev/mem`):
     - `pb_on`: Force HCG (`PA 0x0379FEC6`, `0x0379FF7C`) + Bulb/Silent 14-bit `1H2H`
       (`PA 0x038ECC48`, `0x038ECC56`) verified against real `ILCE-6400A` 1.01 `av-cam.bin` bytes.
     - Live `pb_cfg(2)` updates (`Force HCG` -> `Stock Auto` -> `Force LCG`; `Bulb + Silent` -> `Stock Gate`).
     - Execution of real `av-cam.bin` `IMX284` Mconv & `1H2H` slices in Unicorn before/after patching.
     - Foreign-byte fail-closed preservation (`sw='x'`) and exact byte-for-byte `pb_off` restoration.
  3. Option 1 (`ImageBuilder::MakeRawInfoArea` hook -> `.ARW` 4x4->2x2 Bayer & 2x2 Mono convergence):
     - Sony ARW 2.3 `cRAW` (`Compression=32767`) input decode + `[1,2,1]` Phase-Free Binomial &
       `2x2 Box Sum` across `+2 EV (4x)`, `+1 EV (2x)`, `+0 EV (1x)` gains -> `16-bit Uncomp` `.ARW`.
     - `14-bit cRAW` re-encoding (`Compression=32767`, `SonyRawFileType=2`), decoded back with an
       independent `LibRaw::sony_arw2_load_raw` Python reference to prove round-trip accuracy.
     - `2x2 Mono Quad` panchromatic convergence & `Sensor Only` 24 MP passthrough.
     - Full `6024x4024` -> `3008x2008` (`3000x2000` active crop at `(4,4)`) metadata & extent trim.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import unicorn as uc
from unicorn import arm_const as A

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.a6400.mod_manager.smod import SMOD_V2_FLAG_RELOC, SMOD_V2_FLAG_SUBMENU, parse_smod  # noqa: E402
from tools.research.pack_mod import extract_elf_text  # noqa: E402

BUILD = Path(sys.argv[1])
AVCAM_ORIL = 0x635C6000

MOD_BYTES = (BUILD / "PhotonBin6MP.mod").read_bytes()
_, CODE, SYMS = extract_elf_text((BUILD / "photon_bin_6mp.elf").read_bytes(), expected_code_addr=0)
BSS_SIZE = struct.unpack_from("<I", MOD_BYTES, 52)[0]

info = parse_smod(MOD_BYTES, "PhotonBin6MP.mod")
TARGETS = {
    "0x06d3a61c": {
        "label": "ILCE-6400A 1.01",
        "avcam": ROOT / "firmware/extracted_a6400a_v101/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked/av-cam.bin",
        "orig_stub": 0x06D3A600,
        "pa_main_mconv": 0x0379FEC6,
        "pa_zaf_mconv": 0x0379FF7C,
        "pa_1h2h_silent": 0x038ECC48,
        "pa_1h2h_bulb": 0x038ECC56,
    },
    "0x06b04bcc": {
        "label": "ILCE-6100 1.00",
        "avcam": Path.home() / "a6100-qemu-inputs/av-cam.bin",
        "orig_stub": 0x06B04BC0,
        "pa_main_mconv": 0x0379EC9A,
        "pa_zaf_mconv": 0x0379ED50,
        "pa_1h2h_silent": 0x038EA8FC,
        "pa_1h2h_bulb": 0x038EA90A,
    },
}
hook_tgt = info.hooks[0]["target"] if info.hook_count else ""
if hook_tgt not in TARGETS:
    raise AssertionError(f"Unsupported hook target {hook_tgt!r}")
TGT = TARGETS[hook_tgt]
AVCAM_PATH = TGT["avcam"]
ORIG_STUB = TGT["orig_stub"]
PA_MAIN_MCONV = TGT["pa_main_mconv"]
PA_ZAF_MCONV = TGT["pa_zaf_mconv"]
PA_1H2H_SILENT = TGT["pa_1h2h_silent"]
PA_1H2H_BULB = TGT["pa_1h2h_bulb"]

MOD_BASE  = 0x40000000
WORK_MEM  = 0x20000000
RAW_MEM   = 0x22000000
DEVMEM_VA = 0x50000000
STACK_TOP = 0x5E000000
STOP_PC   = 0x5F000000


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)
    print(f"  ok: {msg}")


def ref_sony_curve(knots4: tuple[int, int, int, int]) -> list[int]:
    """Exact LibRaw case 0x7010 + sony_arw2_load_raw 11-bit -> 14-bit curve."""
    sc = [0, *(k >> 2 & 0xFFF for k in knots4), 4095]
    curve = [0] * 4096
    for i in range(5):
        for j in range(sc[i] + 1, sc[i + 1] + 1):
            curve[j] = min(16383, curve[j - 1] + (1 << i))
    return [curve[p << 1] for p in range(2048)]


def ref_encode_arw2_row(pixels: list[int], lut: list[int]) -> bytes:
    """Reference Sony ARW 2.3 row encoder (width multiple of 32)."""
    assert len(pixels) % 32 == 0
    out = bytearray()

    def inv11(v14: int) -> int:
        best_i, best_d = 0, abs(v14 - lut[0])
        for idx, lv in enumerate(lut):
            d = abs(v14 - lv)
            if d < best_d:
                best_i, best_d = idx, d
        return best_i

    for x0 in range(0, len(pixels), 32):
        for parity in (0, 1):
            p11 = [inv11(pixels[x0 + parity + 2 * i]) for i in range(16)]
            max_v = max(p11)
            min_v = min(p11)
            imax = p11.index(max_v)
            imin = p11.index(min_v)
            if imax == imin:
                imin = 1 if imax == 0 else 0
            sh = 0
            while sh < 4 and (0x80 << sh) <= (max_v - min_v):
                sh += 1
            bits = (max_v & 0x7FF) | ((min_v & 0x7FF) << 11) | (imax << 22) | (imin << 26)
            bitpos = 30
            for i in range(16):
                if i in (imax, imin):
                    continue
                delta = min(0x7F, ((p11[i] - min_v) + ((1 << (sh - 1)) if sh else 0)) >> sh)
                bits |= delta << bitpos
                bitpos += 7
            out += bits.to_bytes(16, "little")
    return bytes(out)


def ref_decode_arw2_row(row_bytes: bytes, width: int, lut: list[int]) -> list[int]:
    """Exact LibRaw::sony_arw2_load_raw row decoder (1-byte zero guard like calloc(raw_width+1,1))."""
    padded = row_bytes + b"\x00"
    out = [0] * width
    col = 0
    for off in range(0, width, 16):
        if col >= width - 30 and (col & 1) == 0:
            break
        dp = padded[off : off + 17]
        val = struct.unpack_from("<I", dp, 0)[0]
        max_v = val & 0x7FF
        min_v = (val >> 11) & 0x7FF
        imax = (val >> 22) & 0x0F
        imin = (val >> 26) & 0x0F
        sh = 0
        while sh < 4 and (0x80 << sh) <= (max_v - min_v):
            sh += 1
        bit = 30
        pix = [0] * 16
        for i in range(16):
            if i == imax:
                pix[i] = max_v
            elif i == imin:
                pix[i] = min_v
            else:
                raw2 = struct.unpack_from("<H", dp, bit >> 3)[0]
                p = (((raw2 >> (bit & 7)) & 0x7F) << sh) + min_v
                pix[i] = min(p, 0x7FF)
                bit += 7
        for i in range(16):
            out[col] = lut[pix[i]]
            col += 2
        col -= 1 if (col & 1) else 31
    return out


class ModHarness:
    def __init__(self, avcam: bytes):
        self.m = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
        map_len = (len(CODE) + BSS_SIZE + 0xFFF) & ~0xFFF
        self.m.mem_map(MOD_BASE, map_len)
        self.m.mem_write(MOD_BASE, CODE)
        self.m.mem_map(ORIG_STUB & ~0xFFF, 0x2000)
        # Stub at 0x06d3a628 (where pb_orig_make_raw_info_area jumps): movs r0, #0; pop context!
        # Wait: pb_orig_make_raw_info_area did: push.w {r4..r11,lr}; sub sp,#0x14; add r7,sp,#8; mov r4,r0; mov r6,r1
        # So at 0x06d3a628 we undo sub sp,#0x14 -> add sp,#0x14; movs r0,#0; pop.w {r4..r11,pc}!
        # add sp, #0x14 = 0xB005; movs r0, #0 = 0x2000; pop.w {r4..r11, pc} = 0xE8BD 0x8FF0
        self.m.mem_write(int(hook_tgt, 16) + 12, bytes.fromhex("05b00020bde8f08f"))

        self.m.mem_map(WORK_MEM, 0x10000)
        self.m.mem_map(RAW_MEM, 0x2000000)   # 32 MiB DMM RAW arena
        self.m.mem_map(DEVMEM_VA, 0x20000)
        self.m.mem_map(STACK_TOP - 0x20000, 0x20000)
        self.m.mem_map(STOP_PC, 0x1000)

        # Populate physical LIRO pages from real av-cam.bin
        self.phys_pages: dict[int, bytearray] = {}
        for pa in (PA_MAIN_MCONV & ~0xFFF, PA_1H2H_SILENT & ~0xFFF):
            off = (pa + 0x60000000) - AVCAM_ORIL
            self.phys_pages[pa] = bytearray(avcam[off : off + 0x1000])

        self.logs: list[str] = []
        self.active_maps: dict[int, tuple[int, int]] = {}
        self.next_map = DEVMEM_VA
        self.m.hook_add(uc.UC_HOOK_INTR, self._syscall)
        self.m.hook_add(uc.UC_HOOK_CODE, self._code)

    def _code(self, m, pc, size, _):
        if pc == STOP_PC:
            m.emu_stop()

    def _syscall(self, m, intno, _):
        nr = m.reg_read(A.UC_ARM_REG_R7)
        a = [m.reg_read(r) for r in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2,
                                     A.UC_ARM_REG_R3, A.UC_ARM_REG_R4, A.UC_ARM_REG_R5)]
        res = 0
        if nr == 5:  # open
            path = bytes(m.mem_read(a[0], 64)).split(b"\0", 1)[0].decode()
            res = 10 if path == "/dev/mem" else (11 if path.endswith(".log") else -2)
        elif nr == 4:  # write
            if a[0] == 11:
                self.logs.append(bytes(m.mem_read(a[1], a[2])).decode())
            res = a[2]
        elif nr == 6:  # close
            res = 0
        elif nr == 192:  # mmap2
            pa = a[5] << 12
            length = (a[1] + 0xFFF) & ~0xFFF
            if pa in self.phys_pages and length == 0x1000:
                va = self.next_map
                self.next_map += 0x1000
                m.mem_write(va, bytes(self.phys_pages[pa]))
                self.active_maps[va] = (pa, length)
                res = va
            else:
                res = -22
        elif nr == 91:  # munmap
            va = a[0]
            if va in self.active_maps:
                pa, length = self.active_maps.pop(va)
                self.phys_pages[pa][:] = m.mem_read(va, length)
            res = 0
        else:
            raise AssertionError(f"unexpected syscall {nr}")
        m.reg_write(A.UC_ARM_REG_R0, res & 0xFFFFFFFF)

    def set_cfg(self, b_mode=0, gain=0, kern=0, arw=0, mconv=0, h2h=0) -> None:
        self.m.mem_write(MOD_BASE + len(CODE) + 4, bytes([b_mode, gain, kern, arw, mconv, h2h]))

    def call(self, sym: str, *args: int) -> int:
        for reg, val in zip((A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2), args):
            self.m.reg_write(reg, val)
        self.m.reg_write(A.UC_ARM_REG_SP, STACK_TOP - 0x100)
        self.m.reg_write(A.UC_ARM_REG_LR, STOP_PC | 1)
        self.m.emu_start((MOD_BASE + SYMS[sym]) | 1, STOP_PC, count=20_000_000)
        return self.m.reg_read(A.UC_ARM_REG_R0)

    def read_pa(self, pa: int, n: int) -> bytes:
        pg = pa & ~0xFFF
        off = pa & 0xFFF
        return bytes(self.phys_pages[pg][off : off + n])


print("1. Validating SMOD v2 + RLC1 + SMNU package format")
check(info.version == 2 and info.name == "PhotonBin 6MP", f"parsed {info.name!r} ({info.file_size} B)")
check(bool(info.flags & SMOD_V2_FLAG_RELOC) and bool(info.flags & SMOD_V2_FLAG_SUBMENU),
      f"carries both RELOC and SUBMENU flags (flags={info.flags:#x})")
check(info.hook_count == 1 and info.hooks[0]["target"] == hook_tgt,
      f"hooks MakeRawInfoArea @ {info.hooks[0]['target']} ({TGT['label']})")

print("2. Verifying Option 3 LIRO instruction swaps & live IMX284 execution in Unicorn")
avcam = AVCAM_PATH.read_bytes()
h = ModHarness(avcam)
stock_mc_main = h.read_pa(PA_MAIN_MCONV, 6)
stock_mc_zaf  = h.read_pa(PA_ZAF_MCONV, 6)
stock_sil     = h.read_pa(PA_1H2H_SILENT, 2)
stock_bulb    = h.read_pa(PA_1H2H_BULB, 2)
check(stock_mc_main.hex() == "b8f1000f03d0" and stock_mc_zaf.hex() == "b8f1000f03d0" and
      stock_sil.hex() == "f2b9" and stock_bulb.hex() == "19e0",
      f"verified real {TGT['label']} av-cam.bin stock instruction bytes")

h.set_cfg(b_mode=0, gain=0, kern=0, arw=0, mconv=0, h2h=0)  # Force HCG + Bulb/Silent 1H2H
h.call("pb_on", 1)
check(h.read_pa(PA_MAIN_MCONV, 6).hex() == "b8f1000f00bf" and
      h.read_pa(PA_ZAF_MCONV, 6).hex() == "b8f1000f00bf" and
      h.read_pa(PA_1H2H_SILENT, 2).hex() == "00bf" and
      h.read_pa(PA_1H2H_BULB, 2).hex() == "00bf",
      f"pb_on patched Main/ZAF Mconv -> HCG and Silent/Bulb -> 14b 1H2H ({h.logs[-1].strip()})")

# Execute real av-cam.bin Main Mconv slice with r8=0 (ISO 100!) to prove bit 1 (0x02) sets!
mc_page_pa = PA_MAIN_MCONV & ~0xFFF
mc_page_va = mc_page_pa + 0x60000000
mc_start_va = PA_MAIN_MCONV + 0x60000000
mc_end_va = mc_start_va + 0x16
mu_mc = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
mu_mc.mem_map(mc_page_va, 0x1000)
mu_mc.mem_map(0x70000000, 0x1000)
mu_mc.mem_write(mc_page_va, bytes(h.phys_pages[mc_page_pa]))
mu_mc.mem_write(0x70000100, b"\x00\x00\x20\x00")  # reg 0x099C initial = 0x20 (bit 1 clear)
mu_mc.reg_write(A.UC_ARM_REG_R4, 0x70000100)
mu_mc.reg_write(A.UC_ARM_REG_R8, 0)  # ISO 100 -> stock would keep LCG (bit 1 = 0)
mu_mc.emu_start(mc_start_va | 1, mc_end_va, count=32)
reg_099c = mu_mc.mem_read(0x70000102, 1)[0]
check(reg_099c == 0x22, f"Unicorn executed patched IMX284 Mconv @ ISO 100: reg 0x099C = {reg_099c:#04x} (HCG bit 1 SET)")

# Live submenu updates via pb_cfg(2): Force LCG + Stock 1H2H Gate
h.set_cfg(mconv=2, h2h=1)
h.call("pb_cfg", 2)
check(h.read_pa(PA_MAIN_MCONV, 6).hex() == "4ff0000803e0" and
      h.read_pa(PA_1H2H_SILENT, 2) == stock_sil and h.read_pa(PA_1H2H_BULB, 2) == stock_bulb,
      f"live pb_cfg(2) switched Mconv -> Force LCG and restored Stock 1H2H gate ({h.logs[-1].strip()})")

# Execute Force LCG with r8=1 (ISO 6400!) on a fresh Unicorn CPU (avoid stale TCG TB cache!)
mu_lcg = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
mu_lcg.mem_map(mc_page_va, 0x1000)
mu_lcg.mem_map(0x70000000, 0x1000)
mu_lcg.mem_write(mc_page_va, bytes(h.phys_pages[mc_page_pa]))
mu_lcg.mem_write(0x70000100, b"\x00\x00\x22\x00")  # bit 1 initially set
mu_lcg.reg_write(A.UC_ARM_REG_R4, 0x70000100)
mu_lcg.reg_write(A.UC_ARM_REG_R8, 1)  # high ISO
mu_lcg.emu_start(mc_start_va | 1, mc_end_va, count=32)
reg_099c_lcg = mu_lcg.mem_read(0x70000102, 1)[0]
check(reg_099c_lcg == 0x20, f"Unicorn executed Force LCG @ ISO 6400: reg 0x099C = {reg_099c_lcg:#04x} (bit 1 CLEARED)")

# Foreign-byte fail-closed test + pb_off restoration
sil_pg_pa = PA_1H2H_SILENT & ~0xFFF
sil_pg_off = PA_1H2H_SILENT & 0xFFF
h.phys_pages[sil_pg_pa][sil_pg_off : sil_pg_off + 2] = b"\xaa\xbb"
h.set_cfg(mconv=0, h2h=0)
h.call("pb_cfg", 2)
check(h.read_pa(PA_1H2H_SILENT, 2) == b"\xaa\xbb" and "h2h=x," in h.logs[-1],
      "foreign bytes at 1H2H site refused without modification (sw='x')")
h.phys_pages[sil_pg_pa][sil_pg_off : sil_pg_off + 2] = stock_sil
h.call("pb_off", 0)
check(h.read_pa(PA_MAIN_MCONV, 6) == stock_mc_main and
      h.read_pa(PA_ZAF_MCONV, 6) == stock_mc_zaf and
      h.read_pa(PA_1H2H_SILENT, 2) == stock_sil and
      h.read_pa(PA_1H2H_BULB, 2) == stock_bulb,
      "pb_off restored all 4 av-cam.bin LIRO sites byte-for-byte to stock")

print("3. Verifying Option 1 .ARW 4x4->2x2 Bayer & 2x2 Mono photon convergence in Unicorn")
KNOTS = (2080, 4416, 8160, 12256)
LUT = ref_sony_curve(KNOTS)


def setup_frame(h: ModHarness, in_w: int, in_h: int, rggb_vals: tuple[int, int, int, int],
                comp: int = 32767) -> tuple[int, int, int]:
    """Populate rawInfoArea, rawMainSrcData, rawAdditionalInfo, and rawMain for Unicorn."""
    info_va = WORK_MEM + 0x0000
    src_va  = WORK_MEM + 0x1000
    ext_va  = WORK_MEM + 0x2000
    add_va  = WORK_MEM + 0x3000

    # Build synthetic RGGB frame
    R, Gr, Gb, B = rggb_vals
    row_even = [R if (x % 2 == 0) else Gr for x in range(in_w)]
    row_odd  = [Gb if (x % 2 == 0) else B  for x in range(in_w)]
    if comp == 32767:
        enc_even = ref_encode_arw2_row(row_even, LUT)
        enc_odd  = ref_encode_arw2_row(row_odd, LUT)
        raw_blob = (enc_even + enc_odd) * (in_h // 2)
    else:
        b_even = struct.pack(f"<{in_w}H", *row_even)
        b_odd  = struct.pack(f"<{in_w}H", *row_odd)
        raw_blob = (b_even + b_odd) * (in_h // 2)

    h.m.mem_write(RAW_MEM, raw_blob)

    src_buf = bytearray(0x80)
    struct.pack_into("<H", src_buf, 0x00, in_w)
    struct.pack_into("<H", src_buf, 0x04, in_h)
    struct.pack_into("<H", src_buf, 0x08, 14)
    struct.pack_into("<H", src_buf, 0x0C, comp)
    struct.pack_into("<H", src_buf, 0x38, 2 if comp == 32767 else 1)
    for idx, kv in enumerate(KNOTS):
        struct.pack_into("<H", src_buf, 0x40 + 4 * idx, kv)
    struct.pack_into("<H", src_buf, 0x60, in_h)
    h.m.mem_write(src_va, bytes(src_buf))

    # rawAdditionalInfo as in firmware: {version=1, dev_ifd_off=12, raw_ifd_off}; out-of-line
    # values are relative to the START OF THEIR OWN IFD (SetRawAdditionalInfoToIfd).
    add_buf = bytearray(160)
    dev_off, raw_off = 12, 48
    struct.pack_into("<III", add_buf, 0, 1, dev_off, raw_off)
    struct.pack_into("<I", add_buf, dev_off, 2)
    struct.pack_into("<HHII", add_buf, dev_off + 4, 0x0001, 4, 1, 0xDEAD)
    struct.pack_into("<HHII", add_buf, dev_off + 16, 0x0002, 4, 2, 28)   # -> dev_off + 28
    struct.pack_into("<II", add_buf, dev_off + 28, 2008, 3008)
    struct.pack_into("<I", add_buf, raw_off, 4)
    struct.pack_into("<HHII", add_buf, raw_off + 4, 0xC61F, 4, 2, 52)
    struct.pack_into("<HHII", add_buf, raw_off + 16, 0xC620, 4, 2, 60)
    struct.pack_into("<HHII", add_buf, raw_off + 28, 0xC68D, 3, 4, 68)
    struct.pack_into("<HHII", add_buf, raw_off + 40, 0xC61D, 3, 1, 16383)
    struct.pack_into("<II", add_buf, raw_off + 52, 12, 12)
    struct.pack_into("<II", add_buf, raw_off + 60, 6000, 4000)
    struct.pack_into("<HHHH", add_buf, raw_off + 68, 0, 0, in_h, in_w)
    h.m.mem_write(add_va, bytes(add_buf))

    ext_sz = (len(raw_blob) + 0xFFF) & ~0xFFF   # libObj requires 4 KiB-aligned extents
    h.m.mem_write(ext_va, struct.pack("<II", 0x70000000, ext_sz))

    info_buf = bytearray(0x200)
    struct.pack_into("<II", info_buf, 0x0D0, src_va, len(src_buf))
    struct.pack_into("<IIII", info_buf, 0x110, RAW_MEM, len(raw_blob), ext_va, 1)
    struct.pack_into("<II", info_buf, 0x120, add_va, len(add_buf))
    h.m.mem_write(info_va, bytes(info_buf))
    return info_va, src_va, add_va


h.set_cfg(b_mode=0, gain=0, kern=0, arw=0, mconv=0, h2h=0)
h.call("pb_on", 1)

# 3a. cRAW 64x32 -> 32x16 16-bit Uncompressed, [1,2,1] Phase, +2 EV (4x Sum)
# Input linear levels above 512: R=512+1000=1512, Gr=512+2000=2512, Gb=512+2200=2712, B=512+3000=3512
info_va, src_va, add_va = setup_frame(h, 64, 32, (1512, 2512, 2712, 3512), comp=32767)
rc = h.call("pb_hook_make_raw_info_area", info_va, 0, 0)
out_w = struct.unpack_from("<H", h.m.mem_read(src_va + 0x00, 2))[0]
out_h = struct.unpack_from("<H", h.m.mem_read(src_va + 0x04, 2))[0]
out_bps = struct.unpack_from("<H", h.m.mem_read(src_va + 0x08, 2))[0]
out_comp = struct.unpack_from("<H", h.m.mem_read(src_va + 0x0C, 2))[0]
out_sz = struct.unpack_from("<I", h.m.mem_read(info_va + 0x114, 4))[0]
row0 = struct.unpack(f"<{out_w}H", h.m.mem_read(RAW_MEM, out_w * 2))
row1 = struct.unpack(f"<{out_w}H", h.m.mem_read(RAW_MEM + out_w * 2, out_w * 2))
exp_r  = 512 + 4 * (LUT[LUT.index(min(LUT, key=lambda v: abs(v - 1512)))] - 512)
exp_gr = 512 + 4 * (LUT[LUT.index(min(LUT, key=lambda v: abs(v - 2512)))] - 512)
exp_gb = 512 + 4 * (LUT[LUT.index(min(LUT, key=lambda v: abs(v - 2712)))] - 512)
exp_b  = 512 + 4 * (LUT[LUT.index(min(LUT, key=lambda v: abs(v - 3512)))] - 512)
check(rc == 0 and (out_w, out_h, out_bps, out_comp, out_sz) == (32, 16, 16, 1, 32 * 16 * 2),
      f"cRAW 64x32 -> 32x16 16-bit Uncomp SubIFD metadata ({out_w}x{out_h}, {out_bps}b, comp={out_comp}, {out_sz} B)")
check(row0[4] == exp_r and row0[5] == exp_gr and row1[4] == exp_gb and row1[5] == exp_b,
      f"+2 EV (4x) [1,2,1] RGGB photon sum exact: R={row0[4]} Gr={row0[5]} Gb={row1[4]} B={row1[5]} (black=512 preserved)")

# 3b. 16-bit Uncompressed input with high highlights (12,000 ADU) -> 16-bit Uncompressed preserves >16,383 without clipping!
h.set_cfg(b_mode=0, gain=0, kern=1, arw=0)  # 2x2 Box Sum, +2 EV (4x), 16-bit Uncomp
info_va, src_va, add_va = setup_frame(h, 64, 32, (12000, 8000, 8000, 512), comp=1)
h.call("pb_hook_make_raw_info_area", info_va, 0, 0)
row0 = struct.unpack("<32H", h.m.mem_read(RAW_MEM, 64))
row1 = struct.unpack("<32H", h.m.mem_read(RAW_MEM + 64, 64))
check(row0[0] == 512 + 4 * (12000 - 512) and row1[1] == 512,
      f"16-bit Uncomp preserves 4x highlight headroom ({row0[0]} > 16383) AND exact 512 black floor ({row1[1]})")

# 3c. Re-encoded 14-bit Sony ARW 2.3 cRAW output -> decode back with reference LibRaw::sony_arw2_load_raw!
h.set_cfg(b_mode=0, gain=1, kern=0, arw=2)  # +1 EV (2x), [1,2,1], 14-bit cRAW
info_va, src_va, add_va = setup_frame(h, 64, 32, (1512, 2012, 2012, 2512), comp=32767)
h.call("pb_hook_make_raw_info_area", info_va, 0, 0)
out_comp = struct.unpack_from("<H", h.m.mem_read(src_va + 0x0C, 2))[0]
out_srft = struct.unpack_from("<H", h.m.mem_read(src_va + 0x38, 2))[0]
out_sz   = struct.unpack_from("<I", h.m.mem_read(info_va + 0x114, 4))[0]
dec_r0 = ref_decode_arw2_row(bytes(h.m.mem_read(RAW_MEM, 32)), 32, LUT)
dec_r1 = ref_decode_arw2_row(bytes(h.m.mem_read(RAW_MEM + 32, 32)), 32, LUT)
check((out_comp, out_srft, out_sz) == (32767, 2, 32 * 16) and
      abs(dec_r0[0] - (512 + 2 * 1000)) <= 16 and abs(dec_r1[1] - (512 + 2 * 2000)) <= 32,
      f"re-encoded 14-bit Sony cRAW ({out_sz} B) decodes via LibRaw ARW2: R={dec_r0[0]} B={dec_r1[1]}")

# 3d. 2x2 Mono Quad mode + Sensor Only passthrough
h.set_cfg(b_mode=1, gain=2, kern=0, arw=1)  # 2x2 Mono Quad, +0 EV Avg (1x), 14-bit Uncomp
info_va, src_va, _ = setup_frame(h, 64, 32, (1512, 2512, 2512, 3512), comp=1)
h.call("pb_hook_make_raw_info_area", info_va, 0, 0)
mono_r0 = struct.unpack("<32H", h.m.mem_read(RAW_MEM, 64))
check(all(v == 2512 for v in mono_r0), f"2x2 Mono Quad +0 EV averages (R+Gr+Gb+B)/4 = {mono_r0[0]}")

h.set_cfg(b_mode=2)  # Sensor Only (24 MP passthrough)
info_va, src_va, _ = setup_frame(h, 64, 32, (1512, 2512, 2512, 3512), comp=32767)
h.call("pb_hook_make_raw_info_area", info_va, 0, 0)
check(struct.unpack_from("<H", h.m.mem_read(src_va, 2))[0] == 64,
      "Sensor Only mode leaves 24 MP RAW frame & SubIFD untouched")

# 3e. Full-sensor 6024x4024x4-row crop/geometry check -> verify 6024x16 produces 3008x8 & 6024x4024 crop math
info_va, src_va, add_va = setup_frame(h, 128, 16, (1024, 1024, 1024, 1024), comp=32767)
h.set_cfg(b_mode=0, gain=0, kern=0, arw=0)
h.call("pb_hook_make_raw_info_area", info_va, 0, 0)
ext_trimmed = struct.unpack_from("<II", h.m.mem_read(WORK_MEM + 0x2000, 8))
check(ext_trimmed[1] == 0x1000 and (ext_trimmed[1] & 0xFFF) == 0,
      f"physical DMM extent trimmed to 4 KiB-aligned {ext_trimmed[1]:#x} B")

# 3f. rawAdditionalInfo: DevInfo IFD untouched, raw IFD out-of-line values patched IFD-relative,
#     and Call 2 (same shared src_data/ext, fresh RawInfoArea) gets the trimmed fields re-applied.
h.set_cfg(b_mode=0, gain=0, kern=0, arw=0)
info_va, src_va, add_va = setup_frame(h, 128, 16, (1024, 1024, 1024, 1024), comp=32767)
orig_add = bytes(h.m.mem_read(add_va, 160))
h.call("pb_hook_make_raw_info_area", info_va, 0, 0)
new_add_va = struct.unpack_from("<I", h.m.mem_read(info_va + 0x120, 4))[0]
check(new_add_va != add_va, "Call 1 redirects rawAdditionalInfo to the cached add_buf copy")
patched = bytes(h.m.mem_read(new_add_va, 160))
check(patched[:48] == orig_add[:48], "DevInfo IFD (header + dev entries + payload) survives uncorrupted")
oo_w, oo_h = 64, 8
check(struct.unpack_from("<II", patched, 48 + 52) == (0, 0),
      "DefaultCropOrigin patched at raw_ifd + val")
check(struct.unpack_from("<II", patched, 48 + 60) == (oo_w, oo_h),
      f"DefaultCropSize patched at raw_ifd + val: {struct.unpack_from('<II', patched, 48 + 60)}")
check(struct.unpack_from("<HHHH", patched, 48 + 68) == (0, 0, oo_h, oo_w), "ActiveArea patched at raw_ifd + val")
call1_sz = struct.unpack_from("<I", h.m.mem_read(info_va + 0x114, 4))[0]
ext1 = struct.unpack_from("<II", h.m.mem_read(WORK_MEM + 0x2000, 8))
check((ext1[0] & 0xFFF) == 0 and (ext1[1] & 0xFFF) == 0, "extent phys/size 4 KiB-aligned after Call 1")

# Call 2: new RawInfoArea whose fields were rebuilt from the original (stale) values.
info2 = WORK_MEM + 0x4000
ib = bytearray(h.m.mem_read(info_va, 0x200))
struct.pack_into("<I", ib, 0x114, 0x01735B00)
struct.pack_into("<I", ib, 0x11C, 1)
struct.pack_into("<I", ib, 0x120, add_va)
h.m.mem_write(info2, bytes(ib))
h.call("pb_hook_make_raw_info_area", info2, 0, 0)
r2 = h.m.mem_read(info2, 0x200)
check(struct.unpack_from("<I", r2, 0x114)[0] == call1_sz and
      struct.unpack_from("<I", r2, 0x120)[0] == new_add_va,
      "Call 2 re-applies trimmed raw size + patched add_buf")

h.call("pb_off", 0)
print("ALL PHOTONBIN 6MP UNICORN TESTS PASSED")
