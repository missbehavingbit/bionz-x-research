"""Pack and validate the v2 dual-hook full-menu touch payload and .req files."""
import hashlib
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE_ADDR = 0x0700D708
CODE_CAP = 2296
STATE_ADDR = 0x07143920
STATE_SIZE = 632
HOOK_REL = 0x06309D48
HOOK_PRS = 0x06309E54
REQ_MAGIC = 0x4D544652  # "MTFR"
PROTO_VER = 2


def fnv1a_32(data: bytes) -> int:
    h = 2166136261
    for b in data:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def find_elf_symbols(raw: bytes, sections: list, names: bytes) -> dict:
    symtab = None
    strtab = None
    for sec in sections:
        name = names[sec[0] : names.index(0, sec[0])].decode()
        if name == ".symtab":
            symtab = sec
        elif name == ".strtab":
            strtab = sec
    if symtab is None or strtab is None:
        raise ValueError("Missing .symtab/.strtab in ELF")
    sdata = raw[strtab[4] : strtab[4] + strtab[5]]
    out = {}
    for off in range(symtab[4], symtab[4] + symtab[5], 16):
        st_name, st_value, _sz, _info, _oth, _shndx = struct.unpack_from("<IIIBBH", raw, off)
        if st_name:
            sname = sdata[st_name : sdata.index(0, st_name)].decode()
            out[sname] = st_value
    return out


def pack_req(op: int, code: bytes, rel_entry: int, prs_entry: int, checksum: int) -> bytes:
    header = struct.pack(
        "<10I",
        REQ_MAGIC,
        PROTO_VER,
        op,
        len(code),
        rel_entry,
        prs_entry,
        checksum,
        STATE_SIZE,
        HOOK_REL,
        HOOK_PRS,
    )
    return header + code


def build_artifacts() -> dict:
    elf_path = ROOT / "artifacts/hardware/menu_touch_full.elf"
    raw = elf_path.read_bytes()
    hdr = struct.unpack_from("<16sHHIIIIIHHHHHH", raw)
    if hdr[0][:7] != b"\x7fELF\x01\x01\x01" or hdr[2] != 40 or hdr[-3] != 40:
        raise ValueError("Wrong ARM ELF header")

    sections = [struct.unpack_from("<10I", raw, hdr[6] + i * 40) for i in range(hdr[-2])]
    ns = sections[hdr[-1]]
    names = raw[ns[4] : ns[4] + ns[5]]
    text = None
    for sec in sections:
        name = names[sec[0] : names.index(0, sec[0])].decode()
        if name == ".text":
            text = sec
        if (sec[2] & 2) and sec[5] and name not in (".text", ".ARM.exidx", ".ARM.attributes"):
            raise ValueError(f"Unmapped runtime section: {name}")

    if text is None or text[3] != CODE_ADDR or text[5] > CODE_CAP or (text[5] % 4):
        raise ValueError(f"Payload invalid or exceeds RX padding ({text[5] if text else None} > {CODE_CAP})")

    syms = find_elf_symbols(raw, sections, names)
    rel_entry = syms.get("mt_full_release", 0)
    prs_entry = syms.get("mt_full_press", 0)
    if not (rel_entry & 1) or not (prs_entry & 1):
        raise ValueError(f"Invalid Thumb entrypoints: rel={hex(rel_entry)}, prs={hex(prs_entry)}")

    code = raw[text[4] : text[4] + text[5]]
    checksum = fnv1a_32(code)

    out_dir = ROOT / "artifacts/hardware"
    (out_dir / "menu-touch-full-payload.bin").write_bytes(code)
    for op, name in ((0, "check"), (1, "install"), (2, "remove")):
        (out_dir / f"menu-touch-full-{name}.req").write_bytes(
            pack_req(op, code, rel_entry, prs_entry, checksum)
        )

    manifest = {
        "version": PROTO_VER,
        "rel_entry": hex(rel_entry),
        "prs_entry": hex(prs_entry),
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
        ],
        "deployed": False,
    }
    (out_dir / "menu-touch-full-build.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    print(json.dumps(build_artifacts(), indent=2))
