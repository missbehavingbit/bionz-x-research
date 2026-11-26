#!/usr/bin/env python3
"""End-to-end ARM Thumb-2 Unicorn verification for FilmPost.mod (SMOD v2 + SMNU + RLC1)."""
from __future__ import annotations

import argparse
import io
import math
from pathlib import Path
import struct
import sys

from PIL import Image, ImageCms
from unicorn import (
    UC_ARCH_ARM,
    UC_HOOK_CODE,
    UC_HOOK_INTR,
    UC_MODE_THUMB,
    Uc,
)
from unicorn.arm_const import (
    UC_ARM_REG_C13_C0_3,
    UC_ARM_REG_CPSR,
    UC_ARM_REG_LR,
    UC_ARM_REG_PC,
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R2,
    UC_ARM_REG_R3,
    UC_ARM_REG_R4,
    UC_ARM_REG_R5,
    UC_ARM_REG_R7,
    UC_ARM_REG_SP,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.a6400.mod_manager.smod import (
    SMOD_HEADER_SIZE,
    SMOD_HOOK_SIZE,
    SMOD_RELOC_MAGIC,
    SMOD_SUBMENU_MAGIC,
    SMOD_V2_FLAG_RELOC,
    SMOD_V2_FLAG_SUBMENU,
    parse_smod,
)

_V2_HDR = struct.Struct("<III32s6I")
_V2_HOOK = struct.Struct("<IBB2x16s16s")
_RELOC_HDR = struct.Struct("<IHH")
_RELOC_LIB = struct.Struct("<II24s")
_SUBMENU_HDR = struct.Struct("<IBBHI")
_SUBMENU_ITEM = struct.Struct("<BB2x24s108s")

TARGETS = {
    "ilce_6400": {
        "hook_va": 0x079D0A1A,
        "resume_va": 0x079D0A27,
        "libmpr_range": (0x07144000, 0x07E2C000),
    },
    "ilce_6400a": {
        "hook_va": 0x07E3FBE6,
        "resume_va": 0x07E3FBF3,
        "libmpr_range": (0x075CE000, 0x082B6000),
    },
}

EXPECTED_ORIG_HEX = "b0b582b000af044608463a60"
MOD_BASE = 0x20000000
RETURN_SENTINEL = 0xDEAD0000
STACK_BASE = 0x40000000
STACK_SIZE = 0x00040000
ASLR_SLIDE = 0x01234000
ORIG_RET_SENTINEL = 0x12345678


def parse_mod_sections(raw: bytes) -> dict:
    magic, version, flags, _name, code_size, _fnv, bss_size, on_off, off_off, hook_count = (
        _V2_HDR.unpack_from(raw, 0)
    )
    assert magic == 0x444F4D53 and version == 2
    hooks_end = SMOD_HEADER_SIZE + hook_count * SMOD_HOOK_SIZE
    code_end = hooks_end + code_size
    code = bytearray(raw[hooks_end:code_end])

    hooks = []
    for i in range(hook_count):
        target, patch_size, fixup, orig_bytes, tmpl_bytes = _V2_HOOK.unpack_from(
            raw, SMOD_HEADER_SIZE + i * SMOD_HOOK_SIZE
        )
        hooks.append(
            {
                "target": target,
                "patch_size": patch_size,
                "fixup": fixup,
                "orig_hex": orig_bytes[:patch_size].hex(),
                "tmpl": tmpl_bytes[:patch_size],
            }
        )

    trail = code_end
    reloc_libs = []
    reloc_offs = []
    if flags & SMOD_V2_FLAG_RELOC:
        rmagic, nlib, nrel = _RELOC_HDR.unpack_from(raw, trail)
        assert rmagic == SMOD_RELOC_MAGIC
        pos = trail + _RELOC_HDR.size
        for _ in range(nlib):
            old_base, old_end, raw_lib = _RELOC_LIB.unpack_from(raw, pos)
            lib_name = raw_lib.split(b"\x00", 1)[0].decode("ascii")
            reloc_libs.append((old_base, old_end, lib_name))
            pos += _RELOC_LIB.size
        reloc_offs = list(struct.unpack_from(f"<{nrel}I", raw, pos))
        trail = pos + nrel * 4 + 4

    submenu = None
    if flags & SMOD_V2_FLAG_SUBMENU:
        smagic, item_count, bss_cfg_off, _, cfg_call_off = _SUBMENU_HDR.unpack_from(raw, trail)
        assert smagic == SMOD_SUBMENU_MAGIC
        pos = trail + _SUBMENU_HDR.size
        items = []
        for _ in range(item_count):
            opt_count, default_val, raw_lbl, raw_opts = _SUBMENU_ITEM.unpack_from(raw, pos)
            lbl = raw_lbl.split(b"\x00", 1)[0].decode("ascii")
            opts = [s for s in raw_opts.split(b"\x00")[:opt_count]]
            items.append((lbl, default_val, [o.decode("ascii") for o in opts]))
            pos += _SUBMENU_ITEM.size
        submenu = {
            "bss_cfg_off": bss_cfg_off,
            "cfg_call_off": cfg_call_off,
            "items": items,
        }

    return {
        "flags": flags,
        "code": code,
        "code_size": code_size,
        "bss_size": bss_size,
        "on_off": on_off,
        "off_off": off_off,
        "hooks": hooks,
        "reloc_libs": reloc_libs,
        "reloc_offs": reloc_offs,
        "submenu": submenu,
    }


class ModHarness:
    def __init__(self, mod_path: Path, target_cfg: dict) -> None:
        raw = mod_path.read_bytes()
        info = parse_smod(raw, filename="FilmPost.mod")
        assert info.version == 2
        assert info.name == "Film Post"
        assert info.has_reloc
        assert info.flags & SMOD_V2_FLAG_SUBMENU
        assert info.hook_count == 2
        assert info.hooks[0]["target"] == f"{target_cfg['hook_va']:#010x}"
        assert info.hooks[0]["orig_hex"] == EXPECTED_ORIG_HEX

        parsed = parse_mod_sections(raw)
        assert len(parsed["reloc_offs"]) == 2, f"Expected 2 relocs, got {parsed['reloc_offs']}"
        assert parsed["submenu"] is not None
        items = parsed["submenu"]["items"]
        assert len(items) == 4
        assert [it[0] for it in items] == ["Halation", "Halation Mix", "Film Grain", "JPEG Quality"]
        assert [it[1] for it in items] == [0, 0, 0, 0]

        # Apply RLC1 ASLR slide to mod code
        for rel_off in parsed["reloc_offs"]:
            val = struct.unpack_from("<I", parsed["code"], rel_off)[0]
            lo, hi = target_cfg["libmpr_range"]
            assert lo <= val < hi, f"Unexpected reloc word {val:#x}"
            struct.pack_into("<I", parsed["code"], rel_off, val + ASLR_SLIDE)

        hook0 = parsed["hooks"][0]
        self.hook_entry_off = struct.unpack_from("<I", hook0["tmpl"], hook0["fixup"])[0]
        assert self.hook_entry_off & 1

        self.parsed = parsed
        self.target_cfg = target_cfg
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)

        total_mod = ((parsed["code_size"] + parsed["bss_size"] + 4095) // 4096) * 4096
        self.uc.mem_map(MOD_BASE, total_mod)
        self.uc.mem_write(MOD_BASE, bytes(parsed["code"]))
        self.bss_addr = MOD_BASE + parsed["code_size"]
        # Initialize submenu defaults in BSS just like Mod Loader v2
        cfg_off = parsed["submenu"]["bss_cfg_off"]
        for idx, (_lbl, default_idx, _opts) in enumerate(items):
            self.uc.mem_write(self.bss_addr + cfg_off + idx, bytes([default_idx]))

        # Map stack and return sentinel page
        self.uc.mem_map(STACK_BASE, STACK_SIZE)
        self.uc.mem_map(RETURN_SENTINEL, 4096)
        self.uc.mem_write(RETURN_SENTINEL, b"\x00\xbf" * 8)

        # Install slid original WriteStill resume stub
        self.resume_addr = (target_cfg["resume_va"] & ~1) + ASLR_SLIDE
        resume_page = self.resume_addr & ~4095
        self.uc.mem_map(resume_page, 8192)
        if self.resume_addr % 4 == 2:
            # ldr r0, [pc, #4]; add sp, #8; pop {r4, r5, r7, pc}; .word ORIG_RET_SENTINEL
            stub = struct.pack("<3HI", 0x4801, 0xB002, 0xBDB0, ORIG_RET_SENTINEL)
        else:
            stub = struct.pack("<4HI", 0x4801, 0xB002, 0xBDB0, 0xBF00, ORIG_RET_SENTINEL)
        self.uc.mem_write(self.resume_addr, stub)

        # Enable VFP/NEON access bit cleanly just in case
        self.uc.reg_write(UC_ARM_REG_C13_C0_3, 0)

        self.next_mmap = 0x30000000
        self.active_mmaps: dict[int, int] = {}
        self.cur_mmap_bytes = 0
        self.peak_mmap_bytes = 0
        self.logs: list[str] = []
        self.cur_log = bytearray()
        self.maps_eof = False
        self.orig_calls: list[dict[str, int]] = []

        self.uc.hook_add(UC_HOOK_INTR, self._on_intr)
        self.uc.hook_add(UC_HOOK_CODE, self._on_resume_stub, begin=self.resume_addr, end=self.resume_addr + 2)

    def _is_mapped(self, addr: int, length: int) -> bool:
        if length <= 0:
            return False
        end = addr + length - 1
        regions = list(self.uc.mem_regions())
        page = addr & ~4095
        while page <= (end & ~4095):
            if not any(lo <= page <= hi for lo, hi, _perm in regions):
                return False
            page += 4096
        return True

    def _on_resume_stub(self, uc: Uc, addr: int, size: int, user_data: object) -> None:
        if addr != self.resume_addr:
            return
        r7 = uc.reg_read(UC_ARM_REG_R7)
        saved_r2 = struct.unpack("<I", uc.mem_read(r7, 4))[0]
        self.orig_calls.append(
            {
                "r4_this": uc.reg_read(UC_ARM_REG_R4),
                "r0_param": uc.reg_read(UC_ARM_REG_R0),
                "r2_still": uc.reg_read(UC_ARM_REG_R2),
                "stack_r2_still": saved_r2,
                "r3_flags": uc.reg_read(UC_ARM_REG_R3),
            }
        )

    def _on_intr(self, uc: Uc, intno: int, user_data: object) -> None:
        if intno != 2:  # SWI / SVC
            raise RuntimeError(f"Unexpected CPU interrupt {intno} at PC={uc.reg_read(UC_ARM_REG_PC):#x}")
        nr = uc.reg_read(UC_ARM_REG_R7)
        if nr == 192:  # mmap2
            length = uc.reg_read(UC_ARM_REG_R1)
            aligned = ((length + 4095) // 4096) * 4096
            addr = self.next_mmap
            self.next_mmap += aligned + 0x10000  # guard gap
            uc.mem_map(addr, aligned)
            self.active_mmaps[addr] = aligned
            self.cur_mmap_bytes += aligned
            if self.cur_mmap_bytes > self.peak_mmap_bytes:
                self.peak_mmap_bytes = self.cur_mmap_bytes
            uc.reg_write(UC_ARM_REG_R0, addr)
        elif nr == 91:  # munmap
            addr = uc.reg_read(UC_ARM_REG_R0)
            length = uc.reg_read(UC_ARM_REG_R1)
            aligned = ((length + 4095) // 4096) * 4096
            assert addr in self.active_mmaps, f"munmap of unmapped {addr:#x}"
            assert self.active_mmaps[addr] == aligned, (
                f"munmap size mismatch at {addr:#x}: got {aligned}, had {self.active_mmaps[addr]}"
            )
            del self.active_mmaps[addr]
            self.cur_mmap_bytes -= aligned
            uc.mem_unmap(addr, aligned)
            uc.reg_write(UC_ARM_REG_R0, 0)
        elif nr == 219:  # mincore
            addr = uc.reg_read(UC_ARM_REG_R0)
            length = uc.reg_read(UC_ARM_REG_R1)
            vec_ptr = uc.reg_read(UC_ARM_REG_R2)
            if (addr & 4095) != 0:
                uc.reg_write(UC_ARM_REG_R0, (-22) & 0xFFFFFFFF)  # -EINVAL
            elif not self._is_mapped(addr, max(1, length)):
                uc.reg_write(UC_ARM_REG_R0, (-12) & 0xFFFFFFFF)  # -ENOMEM
            else:
                n_pages = (max(1, length) + 4095) // 4096
                uc.mem_write(vec_ptr, b"\x01" * n_pages)
                uc.reg_write(UC_ARM_REG_R0, 0)
        elif nr == 5:  # open
            path_ptr = uc.reg_read(UC_ARM_REG_R0)
            path_raw = bytes(uc.mem_read(path_ptr, 32)).split(b"\x00", 1)[0]
            if b"/proc/self/maps" in path_raw:
                self.maps_eof = False
                uc.reg_write(UC_ARM_REG_R0, 4)
            else:
                self.cur_log.clear()
                uc.reg_write(UC_ARM_REG_R0, 3)
        elif nr == 3:  # read
            fd = uc.reg_read(UC_ARM_REG_R0)
            buf = uc.reg_read(UC_ARM_REG_R1)
            cnt = uc.reg_read(UC_ARM_REG_R2)
            if fd == 4 and not self.maps_eof:
                line = b"50000000-50040000 rw-p 00000000 00:00 0 [unicorn-test]\n"
                chunk = line[:cnt]
                uc.mem_write(buf, chunk)
                self.maps_eof = True
                uc.reg_write(UC_ARM_REG_R0, len(chunk))
            else:
                uc.reg_write(UC_ARM_REG_R0, 0)
        elif nr == 4:  # write
            fd = uc.reg_read(UC_ARM_REG_R0)
            buf = uc.reg_read(UC_ARM_REG_R1)
            cnt = uc.reg_read(UC_ARM_REG_R2)
            if fd == 3:
                self.cur_log.extend(bytes(uc.mem_read(buf, cnt)))
            uc.reg_write(UC_ARM_REG_R0, cnt)
        elif nr == 6:  # close
            fd = uc.reg_read(UC_ARM_REG_R0)
            if fd == 3 and self.cur_log:
                self.logs.append(self.cur_log.decode("ascii", errors="replace").strip())
                self.cur_log.clear()
            uc.reg_write(UC_ARM_REG_R0, 0)
        elif nr == 263:  # clock_gettime
            ts_ptr = uc.reg_read(UC_ARM_REG_R1)
            tick = getattr(self, "_clk_tick", 0) + 1
            self._clk_tick = tick
            uc.mem_write(ts_ptr, struct.pack("<2I", 100 + tick, 0))
            uc.reg_write(UC_ARM_REG_R0, 0)
        elif nr == 120:  # clone -> -ENOSYS in single-core Unicorn harness
            uc.reg_write(UC_ARM_REG_R0, (-38) & 0xFFFFFFFF)
        elif nr == 240:  # futex
            uc.reg_write(UC_ARM_REG_R0, 0)
        else:
            raise RuntimeError(f"Unsupported syscall nr={nr}")

    def call_thumb(self, entry_off: int, r0: int = 0, r1: int = 0, r2: int = 0, r3: int = 0) -> int:
        sp = STACK_BASE + STACK_SIZE - 0x100
        self.uc.reg_write(UC_ARM_REG_SP, sp)
        self.uc.reg_write(UC_ARM_REG_LR, RETURN_SENTINEL | 1)
        self.uc.reg_write(UC_ARM_REG_R0, r0)
        self.uc.reg_write(UC_ARM_REG_R1, r1)
        self.uc.reg_write(UC_ARM_REG_R2, r2)
        self.uc.reg_write(UC_ARM_REG_R3, r3)
        cpsr = self.uc.reg_read(UC_ARM_REG_CPSR)
        self.uc.reg_write(UC_ARM_REG_CPSR, cpsr | 0x20)
        self.uc.emu_start((MOD_BASE + entry_off) | 1, RETURN_SENTINEL, count=80_000_000)
        return self.uc.reg_read(UC_ARM_REG_R0)

    def set_cfg(self, hal: int, hal_mix: int, grain: int, qual: int) -> None:
        cfg_off = self.parsed["submenu"]["bss_cfg_off"]
        self.uc.mem_write(self.bss_addr + cfg_off, bytes([hal, hal_mix, grain, qual]))
        cfg_call_off = self.parsed["submenu"]["cfg_call_off"]
        if cfg_call_off:
            self.call_thumb(cfg_call_off, 2)


def make_test_image(w: int = 160, h: int = 160) -> Image.Image:
    im = Image.new("RGB", (w, h), (16, 16, 20))
    px = im.load()
    # Deterministic natural-looking scene texture outside clean halo & mid-gray measurement zones
    # so the baseline JPEG bitrate reflects a real camera photo rather than solid flat rectangles.
    hval = 0x9E3779B9
    for y in range(h):
        for x in range(w):
            in_halo_zone = (20 <= y < 58) and (30 <= x < 68)
            in_gray_zone = (64 <= y < 94) and (8 <= x < w - 8)
            if not in_halo_zone and not in_gray_zone:
                hval = ((hval * 1664525) + 1013904223) & 0xFFFFFFFF
                d = ((hval >> 16) & 31) - 16
                r = max(0, min(255, 24 + ((x * 7 + y * 5) % 48) + d))
                g = max(0, min(255, 26 + ((x * 5 + y * 9) % 44) + d))
                b = max(0, min(255, 30 + ((x * 11 + y * 3) % 52) + d))
                px[x, y] = (r, g, b)
    # Bright white highlight block x=36..60, y=24..48 (to trigger red halation halo)
    for y in range(24, 48):
        for x in range(36, 60):
            px[x, y] = (255, 255, 255)
    # Uniform mid-gray strip y=64..92 (to measure film grain variance cleanly)
    for y in range(64, 92):
        for x in range(8, w - 8):
            px[x, y] = (128, 128, 128)
    return im


def encode_test_jpeg(im: Image.Image, subsampling: int, quality: int = 95) -> bytes:
    buf = io.BytesIO()
    icc = ImageCms.createProfile("sRGB")
    icc_bytes = ImageCms.ImageCmsProfile(icc).tobytes()
    im.save(
        buf,
        format="JPEG",
        quality=quality,
        subsampling=subsampling,
        icc_profile=icc_bytes,
    )
    raw = buf.getvalue()
    # Inject a COM marker right after SOI so we also verify APPn+COM verbatim copy
    com_payload = b"SONY-FILM-POST-TEST"
    com_seg = b"\xff\xfe" + struct.pack(">H", len(com_payload) + 2) + com_payload
    assert raw[:2] == b"\xff\xd8"
    return b"\xff\xd8" + com_seg + raw[2:]


def region_stats(im: Image.Image) -> dict:
    px = im.load()
    # Sample inner halo ring right below white block (x=40..56, y=48..50, within 1..3 px of y=47 edge)
    halo_r, halo_g, halo_b = [], [], []
    for y in range(48, 50):
        for x in range(40, 56):
            r, g, b = px[x, y]
            halo_r.append(r)
            halo_g.append(g)
            halo_b.append(b)
    # Sample mid-gray strip interior (x=16..144, y=68..88)
    mid_vals = []
    for y in range(68, 88):
        for x in range(16, 144):
            r, g, b = px[x, y]
            mid_vals.append(0.299 * r + 0.587 * g + 0.114 * b)
    mean_mid = sum(mid_vals) / len(mid_vals)
    std_mid = math.sqrt(sum((v - mean_mid) ** 2 for v in mid_vals) / len(mid_vals))
    return {
        "halo_r": sum(halo_r) / len(halo_r),
        "halo_g": sum(halo_g) / len(halo_g),
        "halo_b": sum(halo_b) / len(halo_b),
        "mid_mean": mean_mid,
        "mid_std": std_mid,
    }


def setup_still_info(
    uc: Uc,
    base_addr: int,
    main_jpg: bytes,
    thumb_jpg: bytes | None = None,
    slot_off: int = 0x90,
) -> tuple[int, int]:
    total_size = 0x40000
    try:
        uc.mem_map(base_addr, total_size)
    except Exception:
        pass
    uc.mem_write(base_addr, b"\x00" * total_size)

    still_info = base_addr
    jpg_area = still_info + 0x08

    main_buf_addr = base_addr + 0x1000
    uc.mem_write(main_buf_addr, main_jpg)
    uc.mem_write(jpg_area + slot_off, struct.pack("<2I", main_buf_addr, len(main_jpg)))

    if thumb_jpg is not None:
        thumb_buf_addr = base_addr + 0x20000
        uc.mem_write(thumb_buf_addr, thumb_jpg)
        uc.mem_write(jpg_area + 0x80, struct.pack("<2I", thumb_buf_addr, len(thumb_jpg)))

    return still_info, main_buf_addr


def run_suite(build_dir: Path, target: str) -> None:
    mod_path = build_dir / "FilmPost.mod"
    cfg = TARGETS[target]
    h = ModHarness(mod_path, cfg)

    src_im = make_test_image(160, 160)
    thumb_im = src_im.resize((64, 64))
    thumb_jpg = encode_test_jpeg(thumb_im, subsampling=1, quality=85)

    # Activate mod
    h.call_thumb(h.parsed["on_off"], 1)

    for label, sub in [("4:2:2", 1), ("4:2:0", 2)]:
        jpg_bytes = encode_test_jpeg(src_im, subsampling=sub, quality=95)
        still_addr, buf_addr = setup_still_info(
            h.uc, 0x50000000, jpg_bytes, thumb_jpg=thumb_jpg, slot_off=0x90
        )
        orig_calls_before = len(h.orig_calls)
        ret = h.call_thumb(
            h.hook_entry_off,
            r0=0x11110000,
            r1=0x22220000,
            r2=still_addr,
            r3=0x000000A5,
        )
        assert ret == ORIG_RET_SENTINEL, f"{label}: unexpected return {ret:#x}"
        assert len(h.orig_calls) == orig_calls_before + 1
        call = h.orig_calls[-1]
        assert call == {
            "r4_this": 0x11110000,
            "r0_param": 0x22220000,
            "r2_still": still_addr,
            "stack_r2_still": still_addr,
            "r3_flags": 0x000000A5,
        }
        assert not h.active_mmaps, f"{label}: mmap leak {h.active_mmaps}"

        new_vaddr, new_size = struct.unpack("<2I", h.uc.mem_read(still_addr + 0x08 + 0x90, 8))
        assert new_vaddr == buf_addr
        assert 1024 < new_size <= len(jpg_bytes), f"{label}: size {new_size} vs {len(jpg_bytes)}"
        out_jpg = bytes(h.uc.mem_read(buf_addr, new_size))
        assert b"SONY-FILM-POST-TEST" in out_jpg, f"{label}: COM marker dropped"

        # Smaller thumbnail slot +0x80 must remain untouched
        t_vaddr, t_size = struct.unpack("<2I", h.uc.mem_read(still_addr + 0x08 + 0x80, 8))
        assert t_size == len(thumb_jpg)
        assert bytes(h.uc.mem_read(t_vaddr, t_size)) == thumb_jpg

        dec_im = Image.open(io.BytesIO(out_jpg)).convert("RGB")
        assert dec_im.size == (160, 160)
        assert "icc_profile" in Image.open(io.BytesIO(out_jpg)).info, f"{label}: APP2 ICC lost"

        base_stats = region_stats(Image.open(io.BytesIO(jpg_bytes)).convert("RGB"))
        post_stats = region_stats(dec_im)
        assert post_stats["halo_r"] > base_stats["halo_r"] + 3.0, (
            f"{label}: expected red halation boost, got {post_stats} vs {base_stats}"
        )
        assert post_stats["halo_r"] > post_stats["halo_g"] + 3.0 and post_stats["halo_r"] > post_stats["halo_b"] + 3.0, (
            f"{label}: halo not red-dominant: {post_stats}"
        )
        assert post_stats["mid_std"] > max(base_stats["mid_std"] + 0.5, 1.0), (
            f"{label}: expected film grain variance, got {post_stats} vs {base_stats}"
        )
        print(
            f"[{target} {label}] PASS: orig={len(jpg_bytes)} -> out={new_size}, "
            f"halo=({post_stats['halo_r']:.1f},{post_stats['halo_g']:.1f},{post_stats['halo_b']:.1f}), "
            f"mid_std={post_stats['mid_std']:.2f}"
        )

    # Submenu check 1: Halation=Off (4), Grain=35mm ISO 500 (1)
    h.set_cfg(4, 0, 1, 0)
    jpg_422 = encode_test_jpeg(src_im, subsampling=1, quality=95)
    still_addr, buf_addr = setup_still_info(h.uc, 0x50000000, jpg_422)
    assert h.call_thumb(h.hook_entry_off, 1, 2, still_addr, 3) == ORIG_RET_SENTINEL
    assert not h.active_mmaps
    _, new_size = struct.unpack("<2I", h.uc.mem_read(still_addr + 0x08 + 0x90, 8))
    dec_g = Image.open(io.BytesIO(bytes(h.uc.mem_read(buf_addr, new_size)))).convert("RGB")
    sg = region_stats(dec_g)
    base_s = region_stats(Image.open(io.BytesIO(jpg_422)).convert("RGB"))
    assert sg["halo_r"] <= sg["halo_b"] + 1.0 and abs(sg["halo_r"] - base_s["halo_r"]) < 5.0, (
        f"Halation=Off unexpectedly boosted red channel: {sg} vs {base_s}"
    )
    assert sg["mid_std"] > 1.2, f"Grain=35mm ISO 500 expected visible variance: {sg}"
    print(f"[{target} grain-only] PASS: mid_std={sg['mid_std']:.2f}")

    # Submenu check 2: Halation=16mm (2), Grain=Off (4)
    h.set_cfg(2, 0, 4, 0)
    still_addr, buf_addr = setup_still_info(h.uc, 0x50000000, jpg_422)
    assert h.call_thumb(h.hook_entry_off, 1, 2, still_addr, 3) == ORIG_RET_SENTINEL
    assert not h.active_mmaps
    _, new_size = struct.unpack("<2I", h.uc.mem_read(still_addr + 0x08 + 0x90, 8))
    dec_h = Image.open(io.BytesIO(bytes(h.uc.mem_read(buf_addr, new_size)))).convert("RGB")
    sh = region_stats(dec_h)
    assert sh["halo_r"] > base_s["halo_r"] + 3.0 and sh["halo_r"] > sh["halo_b"] + 3.0
    print(f"[{target} halation-only] PASS: halo_r={sh['halo_r']:.1f}")

    # Submenu check 3: All-Off (Halation=Off, Grain=Off) -> bit-identical passthrough
    h.set_cfg(4, 0, 4, 0)
    still_addr, buf_addr = setup_still_info(h.uc, 0x50000000, jpg_422)
    assert h.call_thumb(h.hook_entry_off, 1, 2, still_addr, 3) == ORIG_RET_SENTINEL
    assert not h.active_mmaps
    _, new_size = struct.unpack("<2I", h.uc.mem_read(still_addr + 0x08 + 0x90, 8))
    assert new_size == len(jpg_422)
    assert bytes(h.uc.mem_read(buf_addr, new_size)) == jpg_422

    # Headerless JPEG check: Sony Slot 1 streams starting at FF DB (without FF D8 SOI)
    h.set_cfg(0, 0, 0, 0)
    dqt_pos = jpg_422.index(b"\xff\xdb")
    hdrless = jpg_422[dqt_pos:]
    still_addr, buf_addr = setup_still_info(h.uc, 0x50000000, hdrless, slot_off=0x90)
    assert h.call_thumb(h.hook_entry_off, 1, 2, still_addr, 3) == ORIG_RET_SENTINEL
    assert not h.active_mmaps
    _, hdrless_sz = struct.unpack("<2I", h.uc.mem_read(still_addr + 0x08 + 0x90, 8))
    assert 1024 < hdrless_sz <= len(hdrless), f"headerless: {hdrless_sz} vs {len(hdrless)}"
    out_hdrless = bytes(h.uc.mem_read(buf_addr, hdrless_sz))
    assert out_hdrless[:2] == b"\xff\xdb", f"expected FF DB output start, got {out_hdrless[:4].hex()}"
    dec_hdrless = Image.open(io.BytesIO(b"\xff\xd8" + out_hdrless)).convert("RGB")
    shl = region_stats(dec_hdrless)
    assert shl["halo_r"] > base_s["halo_r"] + 3.0 and shl["mid_std"] > 1.0
    print(f"[{target} headerless] PASS: orig={len(hdrless)} -> out={hdrless_sz}, halo_r={shl['halo_r']:.1f}")

    # Fail-closed check 1: non-JPEG bytes in slot & null still_info
    h.set_cfg(0, 0, 0, 0)
    non_jpg = b"NOT_A_JPEG" + b"\x00" * 1024
    still_addr, buf_addr = setup_still_info(h.uc, 0x50000000, non_jpg)
    assert h.call_thumb(h.hook_entry_off, 1, 2, still_addr, 3) == ORIG_RET_SENTINEL
    assert not h.active_mmaps
    assert bytes(h.uc.mem_read(buf_addr, len(non_jpg))) == non_jpg
    assert h.call_thumb(h.hook_entry_off, 1, 2, 0, 3) == ORIG_RET_SENTINEL
    assert not h.active_mmaps

    # Fail-closed check 2: ultra-tiny flat JPEG where grain re-encode cannot fit <= orig_size skips cleanly
    tiny_flat = encode_test_jpeg(Image.new("RGB", (64, 64), (128, 128, 128)), subsampling=1, quality=85)
    still_addr, buf_addr = setup_still_info(h.uc, 0x50000000, tiny_flat)
    assert h.call_thumb(h.hook_entry_off, 1, 2, still_addr, 3) == ORIG_RET_SENTINEL
    assert not h.active_mmaps
    _, tight_sz = struct.unpack("<2I", h.uc.mem_read(still_addr + 0x08 + 0x90, 8))
    assert tight_sz == len(tiny_flat)
    assert bytes(h.uc.mem_read(buf_addr, tight_sz)) == tiny_flat

    # Fail-closed check 3: unmapped vaddr in slot[0] fails cleanly via SYS_mincore without crashing
    still_addr, _ = setup_still_info(h.uc, 0x50000000, jpg_422)
    h.uc.mem_write(still_addr + 0x08 + 0x90, struct.pack("<2I", 0x66660000, 65536))
    assert h.call_thumb(h.hook_entry_off, 1, 2, still_addr, 3) == ORIG_RET_SENTINEL
    assert not h.active_mmaps

    # Diag check 1: qual_idx == 3 (Diag: Probe Only) leaves JPEG untouched and emits fp_prb log with 0 mmaps
    h.set_cfg(0, 0, 0, 3)
    still_addr, buf_addr = setup_still_info(h.uc, 0x50000000, jpg_422)
    assert h.call_thumb(h.hook_entry_off, 1, 2, still_addr, 3) == ORIG_RET_SENTINEL
    assert not h.active_mmaps
    assert h.logs[-1].startswith("fp_prb "), f"Unexpected probe log: {h.logs[-1]}"
    _, prb_sz = struct.unpack("<2I", h.uc.mem_read(still_addr + 0x08 + 0x90, 8))
    assert prb_sz == len(jpg_422)
    assert bytes(h.uc.mem_read(buf_addr, prb_sz)) == jpg_422

    # Diag check 2: qual_idx == 4 (Diag: No Write) runs pipeline, logs fp_dry, frees mmap, and leaves orig_buf untouched
    h.set_cfg(0, 0, 0, 4)
    still_addr, buf_addr = setup_still_info(h.uc, 0x50000000, jpg_422)
    assert h.call_thumb(h.hook_entry_off, 1, 2, still_addr, 3) == ORIG_RET_SENTINEL
    assert not h.active_mmaps
    assert h.logs[-1].startswith("fp_dry #"), f"Unexpected dry log: {h.logs[-1]}"
    _, dry_sz = struct.unpack("<2I", h.uc.mem_read(still_addr + 0x08 + 0x90, 8))
    assert dry_sz == len(jpg_422)
    assert bytes(h.uc.mem_read(buf_addr, dry_sz)) == jpg_422

    # fp_off passthrough
    h.call_thumb(h.parsed["off_off"], 0)
    still_addr, buf_addr = setup_still_info(h.uc, 0x50000000, jpg_422)
    assert h.call_thumb(h.hook_entry_off, 1, 2, still_addr, 3) == ORIG_RET_SENTINEL
    assert not h.active_mmaps
    _, new_size = struct.unpack("<2I", h.uc.mem_read(still_addr + 0x08 + 0x90, 8))
    assert new_size == len(jpg_422)
    assert bytes(h.uc.mem_read(buf_addr, new_size)) == jpg_422

    print(
        f"[{target}] ALL UNICORN CHECKS PASSED "
        f"(peak_mmap={h.peak_mmap_bytes} B, {len(h.logs)} log records emitted)"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("build_dir", type=Path)
    ap.add_argument("--target", choices=sorted(TARGETS), default="ilce_6400")
    args = ap.parse_args()
    run_suite(args.build_dir, args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
