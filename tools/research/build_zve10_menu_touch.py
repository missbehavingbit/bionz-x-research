"""Build and pack the ZV-E10 dual-hook full-menu touch payload and .req files.
Uses native zig cc cross-compiler (no WSL required).
"""
import hashlib
import io
import json
import os
import subprocess
import struct
import sys
from pathlib import Path
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "artifacts/hardware/zve10"

CODE_ADDR = 0x011964F0   # 4-byte aligned RX zero-padding in libObj.so (16,252 B)
CODE_CAP = 8192
STATE_ADDR = 0x013F8000  # .data zero-padding in libObj.so (2,923 B)
STATE_SIZE = 656
HOOK_REL = 0x00618000    # dispatchMouseRelease (matches a6400 0x6309d48)
HOOK_PRS = 0x0061810C    # dispatchMousePress (matches a6400 0x6309e54)
REQ_MAGIC = 0x4D544652   # "MTFR"
PROTO_VER = 2


def fnv1a_32(data: bytes) -> int:
    h = 2166136261
    for b in data:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def find_elf_symbols(raw: bytes) -> dict:
    elf = ELFFile(io.BytesIO(raw))
    symtab = elf.get_section_by_name(".symtab")
    if not symtab:
        raise ValueError("Missing .symtab in ELF")
    out = {}
    for s in symtab.iter_symbols():
        if s.name:
            out[s.name] = s["st_value"]
    return out


def pack_req(op: int, code: bytes, rel_offset: int, prs_offset: int, checksum: int) -> bytes:
    header = struct.pack(
        "<10I",
        REQ_MAGIC,
        PROTO_VER,
        op,
        len(code),
        rel_offset | 1,
        prs_offset | 1,
        checksum,
        STATE_SIZE,
        HOOK_REL,
        HOOK_PRS,
    )
    return header + code


def compile_artifacts():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    nc_dir = ROOT / "tools/native_camera/zve10"
    ld_script = nc_dir / "payload.ld"
    tramp_s = nc_dir / "menu_touch_full_trampoline.S"
    menu_c = nc_dir / "menu_touch_full.c"
    guard_c = nc_dir / "native_menu_touch_full_guard.c"

    payload_elf = OUT_DIR / "menu_touch_full.elf"
    guard_bin = OUT_DIR / "native_menu_touch_full_guard"

    # 1. Compile payload ELF (Thumb-2 freestanding)
    cmd_payload = [
        "zig", "cc",
        "-target", "thumb-linux-gnueabi",
        "-mcpu=cortex_a9",
        "-mfloat-abi=softfp",
        "-Os",
        "-g",
        "-ffreestanding",
        "-fno-builtin",
        "-nostdlib",
        "-fno-pic",
        "-fno-stack-protector",
        "-fomit-frame-pointer",
        f"-Wl,-T,{ld_script}",
        "-Wl,--build-id=none",
        "-Wl,-e,mt_full_release",
        str(tramp_s),
        str(menu_c),
        "-o", str(payload_elf),
    ]
    print(f"Compiling payload: {' '.join(cmd_payload)}")
    res = subprocess.run(cmd_payload, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Payload compilation failed: {res.stderr}\n{res.stdout}")

    # 2. Compile guard binary (static ARM musl)
    cmd_guard = [
        "zig", "cc",
        "-target", "arm-linux-musleabi",
        "-mcpu=cortex_a9",
        "-mfloat-abi=softfp",
        "-Os",
        "-static",
        "-s",
        "-Wl,--strip-all",
        "-Wall",
        "-Wextra",
        str(guard_c),
        "-o", str(guard_bin),
    ]
    print(f"Compiling guard: {' '.join(cmd_guard)}")
    res = subprocess.run(cmd_guard, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Guard compilation failed: {res.stderr}\n{res.stdout}")

    return payload_elf, guard_bin


def build_artifacts():
    payload_elf, guard_bin = compile_artifacts()
    raw = payload_elf.read_bytes()
    elf = ELFFile(io.BytesIO(raw))

    if elf["e_machine"] != "EM_ARM":
        raise ValueError("Wrong ARM ELF machine type")

    text_sec = elf.get_section_by_name(".text")
    if text_sec is None:
        raise ValueError("Missing .text section in payload ELF")

    text_addr = text_sec["sh_addr"]
    text_size = text_sec["sh_size"]
    if text_addr != CODE_ADDR:
        raise ValueError(f"Wrong .text address: {hex(text_addr)} != {hex(CODE_ADDR)}")
    if text_size > CODE_CAP or (text_size % 4):
        raise ValueError(f"Invalid text size: {text_size} bytes (cap {CODE_CAP})")

    code = text_sec.data()

    syms = find_elf_symbols(raw)
    rel_sym = syms.get("mt_full_release")
    prs_sym = syms.get("mt_full_press")
    if rel_sym is None or prs_sym is None:
        raise ValueError("Missing mt_full_release / mt_full_press symbols")

    rel_entry = rel_sym | 1
    prs_entry = prs_sym | 1

    rel_offset = (rel_sym & ~1) - CODE_ADDR
    prs_offset = (prs_sym & ~1) - CODE_ADDR

    checksum = fnv1a_32(code)

    (OUT_DIR / "menu-touch-full-payload.bin").write_bytes(code)
    for op, name in ((0, "check"), (1, "install"), (2, "remove")):
        req_data = pack_req(op, code, rel_offset, prs_offset, checksum)
        (OUT_DIR / f"menu-touch-full-{name}.req").write_bytes(req_data)

    manifest = {
        "camera": "Sony ZV-E10",
        "firmware": "2.02 / 2.03",
        "version": PROTO_VER,
        "rel_entry": hex(rel_entry),
        "prs_entry": hex(prs_entry),
        "rel_offset": hex(rel_offset | 1),
        "prs_offset": hex(prs_offset | 1),
        "code_address": hex(CODE_ADDR),
        "code_bytes": len(code),
        "code_capacity": CODE_CAP,
        "state_address": hex(STATE_ADDR),
        "state_bytes": STATE_SIZE,
        "hook_rel": hex(HOOK_REL),
        "hook_prs": hex(HOOK_PRS),
        "fnv1a_32": hex(checksum),
        "sha256": hashlib.sha256(code).hexdigest(),
        "actions": [
            "tab_switch",
            "page_switch",
            "swipe_page",
            "row_select",
            "row_enter",
            "popup_select",
            "popup_scroll",
            "popup_cancel",
            "fn_select",
            "fn_enter",
            "fn_cancel",
        ],
        "deployed": False,
    }
    (OUT_DIR / "menu-touch-full-build.json").write_text(json.dumps(manifest, indent=2))
    print(f"Successfully generated build manifest in {OUT_DIR / 'menu-touch-full-build.json'}")
    return manifest


if __name__ == "__main__":
    manifest = build_artifacts()
    print(json.dumps(manifest, indent=2))
