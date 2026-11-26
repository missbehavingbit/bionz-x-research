"""Pack the ModsTabLook SMOD (E0522) from mods_tab_look.elf.

Build the ELF first (WSL):
  arm-linux-gnueabi-gcc -mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin -nostdlib
    -nostartfiles -fno-pic -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror
    -T tools/native_camera/mods_tab_look.ld tools/native_camera/mods_tab_look_trampoline.S
    tools/native_camera/mods_tab_look.c -o artifacts/hardware/mods_tab_look/mods_tab_look.elf
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from tools.research.pack_mod import extract_elf_text, fnv1a_32, pack_smod

ROOT = Path(__file__).resolve().parents[2]
CODE_ADDR = 0x11434398          # /usr/lib/libptp.so RX zero padding (live, E0522)
CODE_END = 0x11435000
HOOK_ADDR = 0x064B54B6          # libObj+0x79d4b6 alcor index lookup (live base 0x05d18000)
HOOK_ORIG = bytes.fromhex("f8b500af0d460446fff7e2ff")  # push {r3-r7,lr}; add r7,sp,#0; mov r5,r1; mov r4,r0; bl


def hook_bytes(entry: int) -> bytes:
    """HOOK_ADDR is 2 mod 4: nop; ldr.w pc, [pc, #0] (4-aligned); .word entry; nop."""
    if not entry & 1 or HOOK_ADDR % 4 != 2:
        raise ValueError("unexpected entry/hook alignment")
    return bytes.fromhex("00bf") + bytes.fromhex("dff800f0") + struct.pack("<I", entry) + bytes.fromhex("00bf")


def build(elf: Path, out: Path) -> dict:
    code_addr, code, syms = extract_elf_text(elf.read_bytes(), expected_code_addr=CODE_ADDR)
    if code_addr + len(code) > CODE_END:
        raise ValueError(f"code exceeds libptp padding ({len(code)} > {CODE_END - CODE_ADDR})")
    entry = syms.get("mtl_hook", 0)
    blob = pack_smod("Mods Tab Look", code_addr, code, [(HOOK_ADDR, HOOK_ORIG, hook_bytes(entry))])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    return {"output": str(out.relative_to(ROOT)) if out.is_relative_to(ROOT) else str(out),
            "code_addr": hex(code_addr), "code_bytes": len(code), "capacity": CODE_END - CODE_ADDR,
            "entry": hex(entry), "fnv1a_32": hex(fnv1a_32(code)), "sha256": hashlib.sha256(blob).hexdigest()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--elf", type=Path, default=ROOT / "artifacts/hardware/mods_tab_look/mods_tab_look.elf")
    ap.add_argument("--out", type=Path, default=ROOT / "artifacts/hardware/mods/ModsTabLook.mod")
    a = ap.parse_args()
    print(json.dumps(build(a.elf, a.out), indent=2))


if __name__ == "__main__":
    main()
