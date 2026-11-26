"""Replay the original LIRO selector and copy with an isolated B8 source fixture."""

import argparse
import hashlib
import json
from pathlib import Path
import struct

import unicorn as uc
from unicorn import arm_const as arm
from elftools.elf.elffile import ELFFile

from project_paths import PROJECT_ROOT


IMAGE_BASE = 0x635C6000
SELECTOR = 0x63AE4DF0
COPY_CALL = 0x63AE4EAC
WIDE_PATCH = 0x63AE4EA8
COPY = 0x63DA22E8
STOP = 0x63AE4EB0
TABLE = 0x6444A044
B8 = 0x6444B1D4
GOT_SLOT = 0x645C630C
BACKUP_SOURCE = 0x64561444
PAGE = 4096
OUTPUT, CONTEXT, STATE, SHADOW, STACK = range(0x10000, 0x15000, PAGE)
GPRS = tuple(getattr(arm, f"UC_ARM_REG_R{i}") for i in range(13) if i != 1)


def checked(path, expected):
    data = (PROJECT_ROOT / path).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"Input identity mismatch: {path}")
    return data


def load_trampoline(path):
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        patch = elf.get_section_by_name(".patch")
        fixture = elf.get_section_by_name(".fixture")
        symbols = elf.get_section_by_name(".symtab")
        enabled = symbols.get_symbol_by_name("fixture_enabled")[0]["st_value"]
        matrix = symbols.get_symbol_by_name("fixture_matrix")[0]["st_value"]
        layout = (patch["sh_addr"], patch["sh_size"], fixture["sh_addr"])
        if layout not in ((COPY_CALL, 4, 0x63AE5000), (WIDE_PATCH, 8, 0x5F400000)):
            raise ValueError("Unexpected patch placement")
        start, end = fixture["sh_addr"], fixture["sh_addr"] + fixture["sh_size"]
        if not start <= enabled < matrix <= end - 18 or end > start + PAGE:
            raise ValueError("Unexpected trampoline layout")
        return patch["sh_addr"], patch.data(), fixture.data(), start, enabled, matrix


def replay(image, stock, alternate, index, enabled, trampoline=None):
    machine = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
    code_pages = {address & -PAGE for address in (SELECTOR, COPY)}
    data_pages = {address & -PAGE for address in (TABLE, B8, GOT_SLOT, BACKUP_SOURCE)}
    for page in sorted(code_pages | data_pages):
        permissions = uc.UC_PROT_READ | (uc.UC_PROT_EXEC if page in code_pages else 0)
        machine.mem_map(page, PAGE, permissions)
        offset = page - IMAGE_BASE
        machine.mem_write(page, image[offset:offset + PAGE])

    machine.mem_map(OUTPUT, 5 * PAGE, uc.UC_PROT_READ | uc.UC_PROT_WRITE)
    machine.mem_write(OUTPUT, b"\xa5" * 20)
    machine.mem_write(CONTEXT, struct.pack("<I", STATE))
    machine.mem_write(B8, stock)
    machine.mem_write(SHADOW, alternate)
    machine.mem_protect(SHADOW, PAGE, uc.UC_PROT_READ)
    fixture_start = fixture_end = 0
    patch_site = COPY_CALL
    matrix_source = SHADOW
    if trampoline is not None:
        patch_site, patch, code, fixture_start, enabled_address, matrix_source = trampoline
        fixture_end = enabled_address
        machine.mem_map(fixture_start, PAGE, uc.UC_PROT_READ | uc.UC_PROT_EXEC)
        machine.mem_write(fixture_start, code)
        machine.mem_write(enabled_address, bytes([enabled]))
        machine.mem_write(matrix_source, alternate)
        machine.mem_write(patch_site, patch)
    machine.reg_write(arm.UC_ARM_REG_R0, OUTPUT)
    machine.reg_write(arm.UC_ARM_REG_R1, index)
    machine.reg_write(arm.UC_ARM_REG_R2, CONTEXT)
    machine.reg_write(arm.UC_ARM_REG_SP, STACK + PAGE)

    selected = []
    accesses = []
    stopped = False
    call_state = None

    def preserved_state(cpu):
        return ([cpu.reg_read(reg) for reg in GPRS], cpu.reg_read(arm.UC_ARM_REG_SP),
                cpu.reg_read(arm.UC_ARM_REG_APSR) & 0xF8000000)

    def instruction(cpu, pc, _size, _data):
        nonlocal stopped, call_state
        if pc == STOP:
            stopped = True
            cpu.emu_stop()
        elif pc == patch_site:
            wide = patch_site == WIDE_PATCH
            original = cpu.reg_read(arm.UC_ARM_REG_R9 if wide else arm.UC_ARM_REG_R1)
            expected = TABLE + (0x200 + 18 * index if index < 15 else 0x1100 + 18 * (index - 15))
            if original != expected:
                raise AssertionError(f"Unexpected selector result: {original:#x}")
            effective = matrix_source if enabled and index == 23 and original == B8 else original
            selected.append((original, effective))
            call_state = preserved_state(cpu)
            if wide:
                call_state[0][0] = cpu.reg_read(arm.UC_ARM_REG_R8)
            if trampoline is None:
                cpu.reg_write(arm.UC_ARM_REG_R1, effective)
        elif pc == COPY:
            if (cpu.reg_read(arm.UC_ARM_REG_R1) != selected[0][1]
                    or preserved_state(cpu) != call_state
                    or cpu.reg_read(arm.UC_ARM_REG_LR) != (STOP | 1)):
                raise AssertionError("The copy ABI changed unexpectedly")
        elif not (SELECTOR <= pc < STOP or COPY < pc <= 0x63DA231A or fixture_start <= pc < fixture_end):
            raise AssertionError(f"Unsupported PC {pc:#x}")

    def copy_access(cpu, kind, addr, size, _value, _data):
        if COPY <= cpu.reg_read(arm.UC_ARM_REG_PC) <= 0x63DA231A and kind == uc.UC_MEM_READ:
            accesses.append((addr, size))

    machine.hook_add(uc.UC_HOOK_CODE, instruction)
    machine.hook_add(uc.UC_HOOK_MEM_READ, copy_access)
    machine.emu_start(SELECTOR | 1, STOP + 2, count=120)
    if not stopped or len(selected) != 1:
        raise AssertionError("Selector did not return from the original copy")
    original, effective = selected[0]
    reads = [(addr, size) for addr, size in accesses if not STACK <= addr < STACK + PAGE]
    if reads != [(effective + i, 2) for i in range(0, 18, 2)]:
        raise AssertionError(f"Unexpected matrix access footprint: {reads}")
    actual = bytes(machine.mem_read(OUTPUT, 20))
    expected = bytes(machine.mem_read(effective, 18)) + b"\xa5\xa5"
    if actual != expected or bytes(machine.mem_read(B8, 18)) != stock:
        raise AssertionError("Copy output or stock matrix changed unexpectedly")
    if struct.unpack("<I", machine.mem_read(BACKUP_SOURCE, 4))[0] != TABLE:
        raise AssertionError("Unexpected backup descriptor source")
    return {
        "index": index, "override_fixture": enabled, "original_source": hex(original),
        "effective_source": hex(effective), "output_hex": actual[:18].hex(),
        "matrix_reads": [[hex(addr), size] for addr, size in reads],
        "stop_pc": hex(STOP),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--trampoline", type=Path, help="Linked offline fixture ELF")
    args = parser.parse_args()
    report = (PROJECT_ROOT / args.report_dir).resolve()
    if not report.is_relative_to(PROJECT_ROOT / "artifacts"):
        parser.error("Report must stay under artifacts")
    image = checked("artifacts/hardware/av-cam-camera-original.bin",
                    "dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209")
    source = "artifacts/analysis/arri-movie-color-2026-11-16/"
    stock = checked(source + "b8-expected20.bin",
                    "54b5552b2e36dd5d436acaac99effd4596ddbcce3e567e3592d83284af8c7a6a")[:18]
    alternate = checked(source + "b8-rec709-20.bin",
                        "a1544b766886b9431cb3ac97d89394072521b48a471f798820ea75b6276dd6ba")[:18]
    trampoline = load_trampoline(args.trampoline) if args.trampoline else None
    report.mkdir(exist_ok=False)
    scope = "Linked Thumb trampoline at synthetic placement" if trampoline else "Host register substitution"
    result = {"scope": f"Original selector and copy; {scope}",
              "fixture": "Zeroed mode flags at context +0xb8 and +3; backup descriptor readback only",
              "runs": []}
    try:
        for index, enabled in ((23, False), (23, True), (23, False), (0, True), (22, True)):
            result["runs"].append(replay(image, stock, alternate, index, enabled, trampoline))
    except Exception as exc:
        result["error"] = str(exc)
        raise
    finally:
        (report / "receipt.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"{len(result['runs'])} original-instruction cases passed")


if __name__ == "__main__":
    main()
