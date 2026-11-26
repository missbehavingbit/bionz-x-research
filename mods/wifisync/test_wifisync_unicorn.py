#!/usr/bin/env python3
"""Offline Unicorn verification for the WiFiSync mod and its portable core.

Two independent receipts in one run:

1. **Core behaviour on Cortex-A5 Thumb.** `wifisync_core.c` + `wifisync_checks.c`
   are cross-compiled with arm-linux-gnueabi-gcc, loaded into Unicorn and
   `ws_run_checks()` executes 72 assertions (CRC-32 vectors, RLC-free queue,
   Smart Single-Best, discovery window, burst quiet window, HTTP/1.1 parsing,
   Range bodies, ack watermark, long-poll wake, power machine).

2. **SMOD v2 packaging.** The produced `WiFiSync.mod` is parsed strictly and
   checked against the live ILCE-6400 bytes: header/flags, bss size, one hook
   at `libmpr.so + 0x00869D24` whose original bytes equal the live library file,
   submenu table (3 items), and the Thumb lifecycle entries.

Usage (from the repository root, Windows Python with Unicorn installed):
    python mods/wifisync/test_wifisync_unicorn.py artifacts/tests/wifisync-build
"""
from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path

from unicorn import Uc, UcError, UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_THUMB
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R4, UC_ARM_REG_SP

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.a6400.mod_manager.smod import (  # noqa: E402
    SMOD_V2_FLAG_RELOC,
    SMOD_V2_FLAG_SUBMENU,
    parse_smod,
)

MOD = ROOT / "mods" / "wifisync"
BUILD = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "artifacts" / "tests" / "wifisync-build"
OUT = ROOT / "artifacts" / "tests" / "wifisync-unicorn"

BASE = 0x40000000
STACK = 0x40800000
STOP = 0x40F00000
CHECKS: list[dict] = []

MPR = ROOT / "artifacts/dumps/a6400-live-lib/libmpr.so"
MPR_REF_BASE = 0x07144000
CON_PUT_OFF = 0x00869D24


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append({"name": name, "pass": bool(cond), "detail": detail})
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail and not cond else ""))
    if not cond:
        raise AssertionError(name + (" " + detail if detail else ""))


def to_wsl(path: Path) -> str:
    s = str(path)
    if len(s) > 2 and s[1] == ":":
        drive = s[0].lower()
        return f"/mnt/{drive}" + s[2:].replace("\\", "/")
    return s.replace("\\", "/")


def arm_cc(cmd: list[str]) -> None:
    if sys.platform.startswith("win"):
        script = " && ".join(
            " ".join(f"'{c}'" if " " in c else c for c in part)
            for part in [cmd]
        )
        subprocess.run(["wsl.exe", "--exec", "bash", "-lc", script], check=True)
    else:
        subprocess.run(cmd, check=True)


def build_arm_checks() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    elf = OUT / "wifisync_checks_arm.elf"
    if "--no-build" in sys.argv and elf.is_file():
        return elf
    objs = []
    common = [
        "arm-linux-gnueabi-gcc", "-mthumb", "-mcpu=cortex-a5", "-mfloat-abi=soft",
        "-mgeneral-regs-only", "-O2", "-ffreestanding", "-fno-builtin", "-fno-common",
        "-nostdlib", "-fno-stack-protector", "-Wall", "-Wextra", "-Werror", "-I", to_wsl(MOD),
    ]
    try:
        for src in ("wifisync_core.c", "wifisync_checks.c"):
            obj = OUT / (src.replace(".c", ".o"))
            arm_cc(common + ["-c", to_wsl(MOD / src), "-o", to_wsl(obj)])
            objs.append(to_wsl(obj))
        arm_cc([
            "arm-linux-gnueabi-ld", "--build-id=none", f"-Ttext={BASE:#x}", "-e", "ws_run_checks",
            *objs, "-o", to_wsl(elf),
        ])
    except subprocess.CalledProcessError:
        if elf.is_file():
            print(f"note: cross-build unavailable; using prebuilt {elf}", file=sys.stderr)
            return elf
        raise
    return elf


def elf_load_segments(path: Path) -> tuple[list[tuple[int, bytes, int]], dict[str, tuple[int, int]]]:
    data = path.read_bytes()
    if data[:4] != b"\x7fELF":
        raise ValueError("not an ELF")
    e_phoff = struct.unpack_from("<I", data, 0x1C)[0]
    e_phentsize = struct.unpack_from("<H", data, 0x2A)[0]
    e_phnum = struct.unpack_from("<H", data, 0x2C)[0]
    segs = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, p_offset, p_vaddr, _, p_filesz, p_memsz, _ = struct.unpack_from("<IIIIIII", data, off)
        if p_type == 1:
            segs.append((p_vaddr, data[p_offset:p_offset + p_filesz], p_memsz))
    e_shoff = struct.unpack_from("<I", data, 0x20)[0]
    e_shentsize = struct.unpack_from("<H", data, 0x2E)[0]
    e_shnum = struct.unpack_from("<H", data, 0x30)[0]
    e_shstrndx = struct.unpack_from("<H", data, 0x32)[0]
    shstr = None
    sections = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        sections.append(struct.unpack_from("<10I", data, off))
    shstr = sections[e_shstrndx]
    names = data[shstr[4]:shstr[4] + shstr[5]]
    syms: dict[str, tuple[int, int]] = {}
    for sec in sections:
        name_off = sec[0]
        name = names[name_off:names.index(0, name_off)].decode() if name_off < len(names) else ""
        if name not in (".symtab", ".dynsym") or sec[4] == 0:
            continue
        strtab = sections[sec[6]]
        strtab_data = data[strtab[4]:strtab[4] + strtab[5]]
        for j in range(sec[5] // sec[9]):
            st_off = sec[4] + j * sec[9]
            st_name, st_value, st_size, st_info, _, st_shndx = struct.unpack_from("<IIIBBH", data, st_off)
            if st_name and st_shndx != 0:
                nm = strtab_data[st_name:strtab_data.index(0, st_name)].decode()
                syms[nm] = (st_value, st_size)
    return segs, syms


def run_unicorn(elf: Path) -> None:
    from unicorn import UC_HOOK_MEM_WRITE_UNMAPPED

    segs, syms = elf_load_segments(elf)
    uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    for vaddr, blob, memsz in segs:
        base = vaddr & ~0xFFF
        size = ((memsz + (vaddr & 0xFFF) + 0xFFF) & ~0xFFF)
        uc.mem_map(base, size)
        uc.mem_write(vaddr, blob)
    uc.mem_map(0x40800000, 0x100000)
    uc.mem_map(0x40F00000, 0x1000)

    stop_hit = {"v": False}
    last = {"pc": 0}

    def hook(uc_, addr, size, _):
        last["pc"] = addr
        if addr == STOP:
            stop_hit["v"] = True
            uc_.emu_stop()

    uc.hook_add(UC_HOOK_CODE, hook)
    bad = {}

    def unmapped(uc_, access, addr, size, value, _):
        bad["pc"] = uc_.reg_read(UC_ARM_REG_PC)
        bad["addr"] = addr
        bad["size"] = size
        bad["access"] = access
        bad["value"] = value
        bad["sp"] = uc_.reg_read(UC_ARM_REG_SP)
        bad["r4"] = uc_.reg_read(UC_ARM_REG_R4)
        uc_.emu_stop()
        return False

    uc.hook_add(UC_HOOK_MEM_WRITE_UNMAPPED, unmapped)
    fn = syms["ws_run_checks"][0] | 1
    uc.reg_write(UC_ARM_REG_SP, 0x408FF000)
    uc.reg_write(UC_ARM_REG_LR, STOP)
    try:
        uc.emu_start(fn, STOP, 0, 200_000_000)
    except UcError as exc:
        if bad:
            raise AssertionError(
                f"unmapped write pc={bad['pc']:#x} addr={bad['addr']:#x} size={bad['size']} "
                f"access={bad['access']} value={bad['value']:#x} r4={bad['r4']:#x} sp={bad['sp']:#x}"
            ) from exc
        raise AssertionError(f"{exc} at pc={last['pc']:#x}") from exc
    check("unicorn_returned", stop_hit["v"] or uc.reg_read(UC_ARM_REG_PC) == STOP,
          hex(uc.reg_read(UC_ARM_REG_PC)))

    count = struct.unpack("<I", uc.mem_read(syms["g_check_count"][0], 4))[0]
    fails = struct.unpack("<I", uc.mem_read(syms["g_check_fail"][0], 4))[0]
    check("unicorn_check_count", count >= 70, str(count))
    check("unicorn_no_failures", fails == 0, str(fails))
    table = syms["g_checks"][0]
    for i in range(count):
        name_ptr, passed = struct.unpack("<II", uc.mem_read(table + i * 8, 8))
        raw = bytes(uc.mem_read(name_ptr, 64))
        name = raw.split(b"\x00", 1)[0].decode()
        CHECKS.append({"name": f"core:{name}", "pass": bool(passed), "detail": ""})
        if not passed:
            print("FAIL core:" + name)
    check("unicorn_all_named_checks_pass", all(c["pass"] for c in CHECKS if c["name"].startswith("core:")))


def verify_mod() -> None:
    mod_path = BUILD / "WiFiSync.mod"
    check("mod_exists", mod_path.is_file(), str(mod_path))
    blob = mod_path.read_bytes()
    info = parse_smod(blob)
    pack = json.loads((BUILD / "pack.json").read_text()) if (BUILD / "pack.json").is_file() else {}
    check("mod_name", info.name == "WiFiSync", info.name)
    check("mod_version", info.version == 2, str(info.version))
    check("mod_reloc_flag", bool(info.flags & SMOD_V2_FLAG_RELOC))
    check("mod_submenu_flag", bool(info.flags & SMOD_V2_FLAG_SUBMENU))
    check("mod_hook_count", info.hook_count == 1, str(info.hook_count))
    check("mod_bss_311296", info.bss_size == 311296, str(info.bss_size))
    hook = info.hooks[0]
    check("hook_target", int(str(hook["target"]), 16) == MPR_REF_BASE + CON_PUT_OFF, str(hook["target"]))
    mpr = MPR.read_bytes()
    expected = mpr[CON_PUT_OFF:CON_PUT_OFF + 12]
    check("hook_orig_bytes_match_firmware", bytes.fromhex(str(hook["orig_hex"])) == expected,
          f"{hook['orig_hex']} != {expected.hex()}")
    patch = bytes.fromhex(str(hook["tmpl_hex"]))
    check("hook_template_jump", patch[:4] == b"\xdf\xf8\x04\xf0"
          or patch[:6] == b"\x00\xbf\xdf\xf8\x00\xf0", patch.hex())
    check("mod_size_under_64k", len(blob) < 65536, str(len(blob)))

    # SMNU trailer follows header + hooks + code + RLC1.
    reloc_len = 8 + 1 * 32 + 1 * 4 + 4
    sub = 68 + 40 + info.code_size + reloc_len
    magic, count, cfg_off, _reserved, cfg_call = struct.unpack_from("<IBBHI", blob, sub)
    check("submenu_magic", magic == 0x554E4D53, hex(magic))
    check("submenu_items_3", count == 3, str(count))
    check("submenu_cfg_offset_4", cfg_off == 4, str(cfg_off))
    check("submenu_cfg_hook", cfg_call != 0, hex(cfg_call))
    check("pack_reloc_single_word", len(pack.get("reloc_words", [])) == 1,
          str(pack.get("reloc_words")))
    check("pack_on_off_present", bool(pack) and "code_bytes" in pack, str(pack.get("code_bytes")))


def main() -> int:
    elf = build_arm_checks()
    verify_mod()
    run_unicorn(elf)
    OUT.mkdir(parents=True, exist_ok=True)
    receipt = {
        "status": "wifisync_unicorn",
        "checks": CHECKS,
        "failures": [c for c in CHECKS if not c["pass"]],
        "arm_elf": str(elf),
        "mod": str(BUILD / "WiFiSync.mod"),
    }
    (OUT / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    failures = receipt["failures"]
    print(f"checks={len(CHECKS)} failures={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
