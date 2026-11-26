"""Pack compiled ARM Thumb ELF mods into SMOD v1 (.mod) files for the SD /mods loader."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SMOD_MAGIC = 0x444F4D53  # "SMOD"
SMOD_VERSION = 1
SMOD_HEADER_FMT = "<III32sIIIIII"
SMOD_HEADER_SIZE = struct.calcsize(SMOD_HEADER_FMT)  # 68 bytes
SMOD_HOOK_FMT = "<II16s16s"
SMOD_HOOK_SIZE = struct.calcsize(SMOD_HOOK_FMT)  # 40 bytes
SMOD_MAX_HOOKS = 8
SMOD_FLAG_STATE_INIT = 1  # state_size bytes of initial state follow the code
MTF2_STATE_HEADER = struct.pack("<6I", 0x4D544632, 2, 2, 0, 0, 16)  # E0520 guard: magic, ver, enabled, count, inflight, cap
SMOD_MAX_PATCH = 16


def fnv1a_32(data: bytes) -> int:
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def make_thumb_trampoline(entry_addr: int) -> bytes:
    """Build 12-byte ARM Thumb absolute jump at a 4-byte-aligned address."""
    return b"\xdf\xf8\x04\xf0\x00\xbf\x00\xbf" + struct.pack("<I", entry_addr)


def extract_elf_text(elf_bytes: bytes, expected_code_addr: int | None = None) -> tuple[int, bytes, dict[str, int]]:
    hdr = struct.unpack_from("<16sHHIIIIIHHHHHH", elf_bytes)
    if hdr[0][:7] != b"\x7fELF\x01\x01\x01" or hdr[2] != 40 or hdr[-3] != 40:
        raise ValueError("Invalid ARM32 little-endian ELF header")

    sections = [struct.unpack_from("<10I", elf_bytes, hdr[6] + i * 40) for i in range(hdr[-2])]
    ns = sections[hdr[-1]]
    names = elf_bytes[ns[4] : ns[4] + ns[5]]

    text = None
    syms: dict[str, int] = {}
    for sec in sections:
        name = names[sec[0] : names.index(0, sec[0])].decode(errors="replace")
        if name == ".text":
            text = sec
        elif (sec[2] & 2) and sec[5] and name not in (".text", ".ARM.exidx", ".ARM.attributes"):
            raise ValueError(f"Unmapped allocatable ELF section: {name}")
        elif sec[1] == 2:
            strtab = sections[sec[6]]
            strings = elf_bytes[strtab[4] : strtab[4] + strtab[5]]
            for off in range(0, sec[5], 16):
                st_name, st_value, *_ = struct.unpack_from("<IIIBBH", elf_bytes, sec[4] + off)
                if st_name:
                    sym = strings[st_name : strings.index(0, st_name)].decode(errors="replace")
                    syms[sym] = st_value

    if text is None or (text[5] % 4) != 0:
        raise ValueError("Missing or unaligned .text section")
    if expected_code_addr is not None and text[3] != expected_code_addr:
        raise ValueError(f".text address {hex(text[3])} != expected {hex(expected_code_addr)}")

    code = elf_bytes[text[4] : text[4] + text[5]]
    return text[3], code, syms


def pack_smod(
    name: str,
    code_addr: int,
    code: bytes,
    hooks: list[tuple[int, bytes, bytes]],
    state_addr: int = 0,
    state_size: int = 0,
    flags: int = 0,
    state_init: bytes | None = None,
) -> bytes:
    """Pack an SMOD blob. If state_init is given it must be exactly state_size bytes; it is
    appended after the code and SMOD_FLAG_STATE_INIT is set so the loader copies it into the
    state block instead of zero-filling it."""
    if state_init is not None:
        if not state_addr or len(state_init) != state_size:
            raise ValueError("state_init must be exactly state_size bytes with a state_addr")
        flags |= SMOD_FLAG_STATE_INIT
    if len(hooks) > SMOD_MAX_HOOKS:
        raise ValueError(f"Too many hooks: {len(hooks)} > {SMOD_MAX_HOOKS}")
    if (len(code) % 4) != 0 or not code:
        raise ValueError("Code must be non-empty and 4-byte aligned")

    name_bytes = name.encode("ascii")[:31]
    checksum = fnv1a_32(code)
    header = struct.pack(
        SMOD_HEADER_FMT,
        SMOD_MAGIC,
        SMOD_VERSION,
        flags,
        name_bytes,
        code_addr,
        len(code),
        state_addr,
        state_size,
        len(hooks),
        checksum,
    )

    hook_blobs = bytearray()
    for target_addr, orig_bytes, hook_bytes in hooks:
        if len(orig_bytes) != len(hook_bytes) or not (1 <= len(orig_bytes) <= SMOD_MAX_PATCH):
            raise ValueError(f"Invalid patch length at {hex(target_addr)}")
        hook_blobs.extend(
            struct.pack(
                SMOD_HOOK_FMT,
                target_addr,
                len(orig_bytes),
                orig_bytes.ljust(SMOD_MAX_PATCH, b"\x00"),
                hook_bytes.ljust(SMOD_MAX_PATCH, b"\x00"),
            )
        )

    return header + bytes(hook_blobs) + code + (state_init or b"")


# MTF2 (E0520) rejects menus whose root has more than 6 tabs (`tabs - 1 > 5`). Mod Loader injects a 7th
# tab, so the SMOD build raises the limit to 7 by patching that single immediate. Context bytes:
# add.w r2, r8, #-1 ; cmp r2, #5 ; bhi.w <return>
MTF2_MAX_TABS_OFF = 0x0700DC60 - 0x0700D708
MTF2_MAX_TABS_ORIG = bytes.fromhex("08f1ff32052a3ff611af")
MTF2_MAX_TABS_PATCH = bytes.fromhex("08f1ff32062a3ff611af")


def patch_mtf2_max_tabs(code: bytes) -> bytes:
    end = MTF2_MAX_TABS_OFF + len(MTF2_MAX_TABS_ORIG)
    if code[MTF2_MAX_TABS_OFF:end] != MTF2_MAX_TABS_ORIG:
        raise ValueError("MTF2 tab-limit context bytes do not match the E0520 payload")
    return code[:MTF2_MAX_TABS_OFF] + MTF2_MAX_TABS_PATCH + code[end:]


def build_menu_touch_full_mod(
    elf_path: Path | None = None,
    out_path: Path | None = None,
    patch_max_tabs: bool = True,
) -> dict:
    """patch_max_tabs: apply the binary 6->7 tab patch (E0520 payload only). Source builds compiled with
    -DMT_MAX_TABS=7u pass False."""
    elf_path = elf_path or (ROOT / "artifacts/hardware/menu_touch_full.elf")
    out_path = out_path or (ROOT / "artifacts/hardware/mods/MenuTouchFull.mod")

    code_addr, code, syms = extract_elf_text(elf_path.read_bytes(), expected_code_addr=0x0700D708)
    if patch_max_tabs:
        code = patch_mtf2_max_tabs(code)
    rel_entry = syms.get("mt_full_release", 0)
    prs_entry = syms.get("mt_full_press", 0)
    if not (rel_entry & 1) or not (prs_entry & 1):
        raise ValueError(f"Invalid Thumb entrypoints: rel={hex(rel_entry)}, prs={hex(prs_entry)}")

    orig_rel = bytes([0x2D, 0xE9, 0xF0, 0x47, 0x88, 0xB0, 0x4F, 0xF0, 0x14, 0x08, 0x01, 0x22])
    orig_prs = bytes([0x2D, 0xE9, 0xF0, 0x47, 0x88, 0xB0, 0x4F, 0xF0, 0x14, 0x09, 0x01, 0x22])
    hooks = [
        (0x06309D48, orig_rel, make_thumb_trampoline(rel_entry)),
        (0x06309E54, orig_prs, make_thumb_trampoline(prs_entry)),
    ]
    blob = pack_smod(
        name="Menu Touch Full",
        code_addr=code_addr,
        code=code,
        hooks=hooks,
        state_addr=0x07143920,
        state_size=632,
        state_init=MTF2_STATE_HEADER.ljust(632, bytes(1)),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(blob)
    try:
        out_rel = str(out_path.relative_to(ROOT))
    except ValueError:
        out_rel = str(out_path)
    return {
        "name": "Menu Touch Full",
        "output": out_rel,
        "size": len(blob),
        "code_addr": hex(code_addr),
        "code_bytes": len(code),
        "hooks": len(hooks),
        "sha256": hashlib.sha256(blob).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Pack menu_touch_full.elf into SMOD .mod format.")
    parser.add_argument("--elf", type=Path, default=ROOT / "artifacts/hardware/menu_touch_full.elf")
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts/hardware/mods/MenuTouchFull.mod")
    parser.add_argument("--no-tab-patch", action="store_true", help="ELF already built with MT_MAX_TABS=7u")
    args = parser.parse_args()
    print(json.dumps(build_menu_touch_full_mod(args.elf, args.out, not args.no_tab_patch), indent=2))


if __name__ == "__main__":
    main()
