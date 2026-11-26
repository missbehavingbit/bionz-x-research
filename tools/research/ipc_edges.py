#!/usr/bin/env python3
"""Enumerate the camera's IPC call sites and the queue id each one passes.

The application layer talks over OSAL messages, and a queue id is what links a producer to a
consumer. This module produces the table: every call to ``osal_snd_msg``, ``osal_rcv_msg_tmo`` or
an ``ioctl``, the function it happens in, and the constant argument when there is one.

The discipline that matters: **an argument is only reported when it is actually a constant.**
``constant_arguments`` walks back from the call site looking for an immediate written into the
register. A register copied from another register, loaded from memory or computed has no static
value, and guessing one would put a wrong queue id in the table — worse than an empty cell,
because an empty cell is visibly unknown.

Offline only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# Sibling tools are imported as modules, so the directory must be importable even when this file
# is run with ``-I`` (which drops the script directory from sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parent))

#: The imports whose call sites are worth tabulating.
_IPC_CALLEES = ("osal_snd_msg", "osal_rcv_msg_tmo", "osal_snd_sync_msg", "ioctl")


def _capstone():
    import capstone

    return capstone.Cs(capstone.CS_ARCH_ARM,
                       capstone.CS_MODE_THUMB | capstone.CS_MODE_LITTLE_ENDIAN)


def _decode_to(md, data: bytes, base: int, start: int, site: int):
    """Instructions from ``start`` that end exactly at ``site``, or ``None`` if the stream
    decoded from ``start`` does not land on ``site`` (it began inside another instruction)."""
    out = []
    for ins in md.disasm(data[start - base:site - base], start):
        out.append(ins)
    if not out or out[-1].address + out[-1].size != site:
        return None
    return out


def constant_arguments(data: bytes, base: int, site: int, register: int,
                       window: int = 0x40) -> int | None:
    """The constant held by ``register`` at ``site``, or ``None`` when it is not knowable.

    The value is tracked forward through a straight-line window that ends exactly at the call:

    * ``mov``/``movs``/``movw`` with an immediate sets it; a following ``movt`` sets the top half,
      so ``movw r0,#0x8192; movt r0,#0xca`` is ``0xca8192`` (the earlier version kept ``0xca``).
    * Any other instruction that writes the register makes it unknown, as does a call
      (``bl``/``blx`` clobber r0-r3) or an instruction inside an IT block.
    * The window must decode onto the call site from some start; if no start does, the bytes
      before it are not one instruction stream and nothing is reported.

    Straight-line only: a branch into the window from elsewhere is not seen, which is why a
    reported constant is a lead to confirm by disassembly, not a verified runtime value.
    """
    import capstone
    from capstone import arm_const

    md = _capstone()
    md.detail = True
    instructions = None
    for start in range(max(base, site - window), site, 2):
        instructions = _decode_to(md, data, base, start, site)
        if instructions is not None:
            break
    if not instructions:
        return None
    target_reg = getattr(arm_const, f"ARM_REG_R{register}")
    value: int | None = None
    it_remaining = 0
    for ins in instructions:
        if ins.mnemonic.startswith("it"):
            it_remaining = len(ins.mnemonic) - 1     # it, itt, ite, ittt, ...
            continue
        conditional = it_remaining > 0
        if it_remaining:
            it_remaining -= 1
        if ins.mnemonic in ("bl", "blx") or (ins.mnemonic in ("blx", "bx") and ins.op_count(capstone.CS_OP_REG)):
            if register <= 3:
                value = None
            continue
        try:
            _, written = ins.regs_access()
        except capstone.CsError:
            written = []
        if target_reg not in written:
            continue
        operands = ins.operands
        immediate = (len(operands) == 2 and operands[1].type == arm_const.ARM_OP_IMM)
        if conditional:
            value = None
        elif ins.mnemonic in ("mov", "movs", "mov.w", "movs.w", "movw") and immediate:
            value = operands[1].imm & 0xFFFFFFFF
        elif ins.mnemonic == "movt" and immediate and value is not None:
            value = (value & 0xFFFF) | ((operands[1].imm & 0xFFFF) << 16)
        elif (ins.mnemonic in ("ldr", "ldr.w") and len(operands) == 2
              and operands[1].type == arm_const.ARM_OP_MEM
              and operands[1].mem.base == arm_const.ARM_REG_PC
              and operands[1].mem.index == 0):
            # A literal-pool load: the word at Align(PC, 4) + disp, where PC is this
            # instruction's address + 4. A later `add rX, pc` (an address, not a value) or any
            # other write still resets it to unknown.
            literal = ((ins.address + 4) & ~3) + operands[1].mem.disp
            offset = literal - base
            value = (int.from_bytes(data[offset:offset + 4], "little")
                     if 0 <= offset <= len(data) - 4 else None)
        else:
            value = None
    return value


def _literal_loads(data: bytes, base: int) -> dict[int, tuple[int, int]]:
    """``address -> (register, immediate)`` for Thumb PC-relative loads.

    Used because the compiler materialises a queue id either with ``movw``/``movt`` or by loading
    a literal from the pool, and both forms are common in this code.
    """
    out: dict[int, tuple[int, int]] = {}
    for pos in range(0, len(data) - 4, 2):
        first = int.from_bytes(data[pos:pos + 2], "little")
        if (first >> 11) == 0b01001:          # 16-bit ldr rX,[pc,#imm8]
            register = (first >> 8) & 0x7
            immediate = (first & 0xFF) * 4
            out[base + pos] = (register, immediate)
            continue
        second = int.from_bytes(data[pos + 2:pos + 4], "little")
        if (first & 0xFF7F) == 0xF85F:        # 32-bit ldr.w rX,[pc,#imm12]
            register = (second >> 12) & 0xF
            immediate = second & 0xFFF
            out[base + pos] = (register, immediate)
    return out


def ipc_edges(path: Path) -> list[dict[str, Any]]:
    """One entry per IPC call site found in ``path``.

    Each entry: ``{"caller", "callee", "site", "queue_or_cmd"}``. ``caller`` is the function whose
    declared extent contains the site when the symbol table answers, else ``sub_XXXXXXXX``.
    """
    from callgraph import (_plt_symbol_names, direct_calls, executable_ranges,
                           largest_code_section, make_enclosing)
    from elf_symbols import functions, read_symbols

    path = Path(path)
    funcs = functions(read_symbols(path))
    names_by_address = {s.address: s.name for s in funcs if s.address}
    plt_names = _plt_symbol_names(path)
    code_base, code = largest_code_section(path)
    enclosing = make_enclosing(funcs)

    edges: list[dict[str, Any]] = []
    for caller, callee in direct_calls(code, code_base, executable_ranges(path)):
        resolved = plt_names.get(callee) or names_by_address.get(callee) or ""
        if resolved not in _IPC_CALLEES:
            continue
        # The first argument of an OSAL call is the queue id in r0; for ioctl the command sits
        # in r1 after the file descriptor.
        register = 1 if resolved == "ioctl" else 0
        value = constant_arguments(code, code_base, caller, register)
        edges.append({
            "caller": enclosing(caller),
            "callee": resolved,
            "site": caller,
            "queue_or_cmd": value,
        })
    return edges


def edges_to_tsv(edges: list[dict[str, Any]]) -> str:
    """The table, with an unknown id written as an empty field rather than a zero."""
    lines = ["caller\tcallee\tsite\tqueue_or_cmd"]
    for edge in edges:
        value = edge["queue_or_cmd"]
        rendered = "" if value is None else f"0x{value:x}"
        lines.append(f"{edge['caller']}\t{edge['callee']}\t0x{edge['site']:x}\t{rendered}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("image", type=Path)
    parser.add_argument("--tsv", action="store_true", help="write the table instead of a summary")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args(argv)

    edges = ipc_edges(args.image)
    if args.tsv:
        sys.stdout.write(edges_to_tsv(edges))
        return 0
    with_constant = sum(1 for e in edges if e["queue_or_cmd"] is not None)
    print(f"edges: {len(edges)}  with a constant id: {with_constant}  "
          f"unknown: {len(edges) - with_constant}", file=sys.stderr)
    for edge in edges[: args.limit]:
        value = "-" if edge["queue_or_cmd"] is None else f"0x{edge['queue_or_cmd']:x}"
        print(f"0x{edge['site']:x} {edge['caller']} -> {edge['callee']} {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())