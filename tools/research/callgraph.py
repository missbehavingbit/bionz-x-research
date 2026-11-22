#!/usr/bin/env python3
"""Build a direct-call graph over a packaged ARM library.

The camera's libraries ship unstripped, so a ``bl`` can be resolved to a symbol name instead of
an anonymous address. That is what turns 20 MB of ``libObj.so`` into something a person can
follow: who calls ``osal_snd_msg``, what reaches ``ObjDisplay_Start``, where a button event
enters.

Two deliberate limits:

* **Direct calls only.** An indirect call through a register cannot be resolved statically, so it
  is counted in ``unresolved`` rather than guessed. A guessed edge would make a traced path look
  complete when it is not.
* **Names are never invented.** An address with no symbol becomes ``sub_XXXXXXXX``, which is
  honest about being unknown.

Offline only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


def _capstone():
    import capstone

    return capstone.Cs(capstone.CS_ARCH_ARM,
                       capstone.CS_MODE_THUMB | capstone.CS_MODE_LITTLE_ENDIAN)


def _is_32bit(first: int) -> bool:
    """Whether a Thumb halfword starts a 32-bit instruction (ARMv7-M/A encoding rule)."""
    return (first >> 11) in (0b11101, 0b11110, 0b11111)


def _thumb_bl_target(data: bytes, pos: int, base: int) -> int | None:
    """Target of a 32-bit Thumb ``bl``/``blx`` at ``pos``, or ``None`` if not one.

    The return value is in *symbol space*: a ``bl`` stays in Thumb state, so its target carries
    bit 0 set exactly as the ELF symbol for a Thumb function does; a ``blx`` switches to ARM
    state, so its target is word-aligned and even. Matching symbol tables therefore needs no
    further adjustment.

    Two encoding facts that earlier versions of this function got wrong, both measured on the
    a6400 ``libObj.so`` against capstone:

    * Bit 12 of the second halfword distinguishes the two forms (1 = ``bl``, 0 = ``blx``).
    * Only ``blx`` aligns the PC to a word before adding the offset. Aligning for ``bl`` too put
      156,131 of 555,069 targets two bytes low.
    """
    if pos & 1 or pos + 4 > len(data):
        return None
    first = int.from_bytes(data[pos:pos + 2], "little")
    second = int.from_bytes(data[pos + 2:pos + 4], "little")
    # first halfword 11110 S imm10; second halfword 11 J1 X J2 imm11, X=1 bl, X=0 blx
    if (first & 0xF800) != 0xF000 or (second & 0xC000) != 0xC000:
        return None
    is_bl = bool(second & 0x1000)
    if not is_bl and second & 1:
        return None                      # blx with H=1 is UNDEFINED
    sign = (first >> 10) & 1
    j1 = (second >> 13) & 1
    j2 = (second >> 11) & 1
    imm10 = first & 0x3FF
    imm11 = second & 0x7FF
    i1 = 1 - (j1 ^ sign)
    i2 = 1 - (j2 ^ sign)
    offset = (sign << 24) | (i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm11 << 1)
    if sign:
        offset -= 1 << 25
    pc = base + pos + 4
    if is_bl:
        return ((pc + offset) & 0xFFFFFFFF) | 1
    return ((pc & ~3) + offset) & 0xFFFFFFFF


def direct_calls(data: bytes, base: int,
                 code_ranges: list[tuple[int, int]] | None = None) -> list[tuple[int, int]]:
    """``(caller address, callee address)`` for every direct call in ``data``.

    The section is walked as an instruction stream: halfword-aligned, advancing by each
    instruction's own length. Scanning every byte offset instead (the earlier method) produced
    12,767 "calls" at odd addresses, which Thumb cannot encode, and 4,214 more that began in the
    middle of another 32-bit instruction. Each candidate is still confirmed by capstone.

    ``code_ranges`` (``[(start, end), ...]`` of executable memory) drops a target that lands
    outside any code; such a "call" is data that happens to decode as a branch. Without it, every
    decoded target is kept.
    """
    md = _capstone()
    edges: list[tuple[int, int]] = []
    pos = 0
    limit = len(data) - 1
    while pos < limit:
        first = int.from_bytes(data[pos:pos + 2], "little")
        if not _is_32bit(first):
            pos += 2
            continue
        target = _thumb_bl_target(data, pos, base)
        if target is not None:
            decoded = list(md.disasm(data[pos:pos + 4], base + pos))[:1]
            confirmed = bool(decoded) and decoded[0].mnemonic in ("bl", "blx") \
                and decoded[0].size == 4
            inside = code_ranges is None or any(lo <= (target & ~1) < hi
                                                for lo, hi in code_ranges)
            if confirmed and inside:
                edges.append((base + pos, target))
        pos += 4
    return edges


def _name_for(address: int, names: dict[int, str]) -> str:
    """The symbol at ``address``, or a name that admits it is unknown."""
    return names.get(address, f"sub_{address:08X}")


def make_enclosing(funcs) -> "callable":
    """A function mapping an instruction address to the symbol whose extent contains it.

    A symbol covers only its declared size, and a Thumb symbol's value carries bit 0, so the
    extent is ``[value & ~1, (value & ~1) + size)``. These libraries export ~1,853 functions
    whose sizes total 126,608 bytes of a 13,849,456-byte .text: bisecting to the nearest
    preceding start attributed 99.1% of call sites to a function that does not contain them.
    Anything outside every extent stays an anonymous ``sub_``.
    """
    import bisect

    spans = sorted(((s.address & ~1), s.size, s.name) for s in funcs if s.address and s.size)
    starts = [start for start, _, _ in spans]

    def enclosing(address: int) -> str:
        index = bisect.bisect_right(starts, address)
        if index:
            start, size, name = spans[index - 1]
            if start <= address < start + size:
                return name
        return f"sub_{address:08X}"

    return enclosing


def executable_ranges(path: Path) -> list[tuple[int, int]]:
    """``[(start, end), ...]`` of every allocated executable section (``.text``, ``.plt``, ...)."""
    from elftools.elf.elffile import ELFFile

    with open(path, "rb") as handle:
        elf = ELFFile(handle)
        return [(s["sh_addr"], s["sh_addr"] + s["sh_size"]) for s in elf.iter_sections()
                if s["sh_flags"] & 0x4 and s["sh_flags"] & 0x2 and s["sh_size"]]


def largest_code_section(path: Path) -> tuple[int, bytes]:
    """``(address, bytes)`` of the largest executable PROGBITS section."""
    from elftools.elf.elffile import ELFFile

    try:
        with open(path, "rb") as handle:
            elf = ELFFile(handle)
            best = None
            for section in elf.iter_sections():
                if section["sh_type"] != "SHT_PROGBITS" or not section["sh_flags"] & 0x4:
                    continue
                if best is None or section["sh_size"] > best[1]:
                    best = (section["sh_addr"], section["sh_size"], section.data())
    except Exception as exc:
        raise ValueError(f"{path}: cannot read executable sections ({exc})") from exc
    if best is None:
        raise ValueError(f"{path}: no executable section")
    return best[0], best[2]


def build_callgraph(path: Path) -> dict[str, Any]:
    """Load an ELF and return ``{nodes, edges, unresolved, ...}``.

    ``nodes`` maps a symbol address (Thumb bit included) to its name; ``edges`` is a list of
    ``(caller name, callee name)``; ``unresolved`` counts calls whose target is not a known
    symbol or PLT stub. ``named_edges`` counts calls whose callee resolves (a defined function or
    an import through the PLT); ``named_local_callees`` the subset that lands on a function
    defined in this library. Every earlier published figure counted only imports, because Thumb
    symbol values are odd and the targets they were compared with were even.
    """
    from elf_symbols import functions, read_symbols

    path = Path(path)
    funcs = functions(read_symbols(path))
    # Defined code only: an import carries address 0, so including it would map every import to
    # the single address 0. Imports appear as edge targets by name, through the PLT.
    names = {sym.address: sym.name for sym in funcs if sym.address}
    imports = {sym.name for sym in funcs if not sym.address}
    code_base, code = largest_code_section(path)
    plt_names = _plt_symbol_names(path)
    enclosing = make_enclosing(funcs)

    pairs = direct_calls(code, code_base, executable_ranges(path))
    edges: list[tuple[str, str]] = []
    unresolved = named_edges = local = 0
    for caller, callee in pairs:
        if callee in plt_names:
            callee_name = plt_names[callee]
            named_edges += 1
        elif callee in names:
            callee_name = names[callee]
            named_edges += 1
            local += 1
        else:
            callee_name = f"sub_{callee & ~1:08X}"
            unresolved += 1
        edges.append((enclosing(caller), callee_name))

    total = len(edges)
    return {"nodes": dict(names), "edges": edges, "unresolved": unresolved,
            "imports": sorted(imports), "named_edges": named_edges,
            "named_local_callees": local,
            "named_fraction": named_edges / total if total else 0.0}


def _arm_imm(instruction: int) -> int:
    """The rotated 8-bit immediate of an ARM data-processing instruction."""
    rotate = ((instruction >> 8) & 0xF) * 2
    value = instruction & 0xFF
    return ((value >> rotate) | (value << (32 - rotate))) & 0xFFFFFFFF if rotate else value


def _plt_symbol_names(path: Path) -> dict[int, str]:
    """Map each PLT entry address to the imported symbol it jumps through.

    Each ARM entry is ``add ip, pc, #a``; ``add ip, ip, #b``; ``ldr pc, [ip, #c]!``, and the GOT
    slot it loads is computed from those three immediates and matched to the ``.rel.plt``
    relocation with that ``r_offset``. Entries reached from Thumb code carry a 4-byte
    ``bx pc; nop`` prefix, so entries are 12 *or* 16 bytes long: the fixed 12-byte stride used
    before mislabelled every import after the first prefixed entry. The prefix address is mapped
    too, with bit 0 set, because a Thumb ``bl`` lands there. An entry that does not decode is
    skipped rather than guessed.
    """
    from elftools.elf.elffile import ELFFile

    try:
        with open(path, "rb") as handle:
            elf = ELFFile(handle)
            plt = elf.get_section_by_name(".plt")
            rel = elf.get_section_by_name(".rel.plt")
            if plt is None or rel is None:
                return {}
            symtab = elf.get_section(rel["sh_link"])
            by_slot: dict[int, str] = {}
            for relocation in rel.iter_relocations():
                symbol = symtab.get_symbol(relocation["r_info_sym"])
                by_slot[relocation["r_offset"]] = (
                    symbol.name or f"import_{relocation['r_info_sym']}")
            data = plt.data()
            start = plt["sh_addr"]
    except Exception:
        return {}

    names: dict[int, str] = {}
    pos = 20                                   # fixed PLT0 header
    while pos + 12 <= len(data):
        prefix = None
        if data[pos:pos + 4] == bytes.fromhex("7847c046"):     # bx pc; nop (Thumb)
            prefix = start + pos
            pos += 4
            if pos + 12 > len(data):
                break
        words = [int.from_bytes(data[pos + 4 * i:pos + 4 * i + 4], "little") for i in range(3)]
        if ((words[0] & 0x0FFFF000) != 0x028FC000 or (words[1] & 0x0FFFF000) != 0x028CC000
                or (words[2] & 0x0F7FF000) != 0x053CF000):
            pos += 4                           # not an entry here: resynchronise on the next word
            continue
        entry = start + pos
        offset = words[2] & 0xFFF
        if not words[2] & (1 << 23):
            offset = -offset
        slot = (entry + 8 + _arm_imm(words[0]) + _arm_imm(words[1]) + offset) & 0xFFFFFFFF
        name = by_slot.get(slot)
        if name is not None:
            names[entry] = name
            if prefix is not None:
                names[prefix | 1] = name
        pos += 12
    return names


def callers_of(graph: dict[str, Any], name: str) -> list[str]:
    """Every node with an edge to ``name``, sorted and deduplicated."""
    return sorted({caller for caller, callee in graph["edges"] if callee == name})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("image", type=Path)
    parser.add_argument("--callers", help="print only the callers of this symbol")
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args(argv)

    graph = build_callgraph(args.image)
    if args.callers:
        for name in callers_of(graph, args.callers)[: args.limit]:
            print(name)
        return 0
    print(f"nodes: {len(graph['nodes'])}  edges: {len(graph['edges'])}  "
          f"unresolved targets: {graph['unresolved']}", file=sys.stderr)
    for caller, callee in graph["edges"][: args.limit]:
        print(f"{caller} -> {callee}")
    return 0


if __name__ == "__main__":
    sys.exit(main())