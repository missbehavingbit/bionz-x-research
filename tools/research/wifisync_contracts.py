#!/usr/bin/env python3
"""Zero-camera Phase 0 contract extraction for the WiFiSync camera-side mod.

Pins, from the extracted ILCE-6400 2.00 firmware only (read-only, no camera):

  * library identities (size + SHA-256 + FNV-1a of the first 4 KiB);
  * the read-only Sony Fsys entry points used by the mod and the flag
    semantics that the mod hard-codes (`Fsys_Open` read = bit 0);
  * `MprMonCtrl::ConPutWriteStill`, the capture-notification hook target, and
    the exact 12 prologue bytes the SMOD hook overwrites;
  * the AP tool surface (wpa_supplicant/dhcpd/wl/iperf), the NVRAM 5 GHz
    front-end flags and the CLM 5 GHz channel ranges;
  * the `bsa_server` binary identity for the optional Phase 2.5 BLE wake.

Every check is a code/data assertion, not a claim of live camera behaviour.
Output: artifacts/analysis/wifisync-contracts/manifest.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs  # noqa: E402
from elftools.elf.elffile import ELFFile  # noqa: E402

from tools.research.arm_plt import resolve_arm_plt  # noqa: E402
from tools.research.elf_symbols import read_symbols  # noqa: E402

FW = ROOT / "firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked"
OUT = ROOT / "artifacts" / "analysis" / "wifisync-contracts"

# Expected reference values (ILCE-6400 2.00 captured layout).
EXPECT = {
    "libmpr.so": {"size": 12740352, "fnv4096": 0x7A4ED0D1},
    "libInfraFsys.so": {"size": 411548, "fnv4096": 0x61E41F02},
}
# Live ILCE-6400 2.00 camera /usr/lib identities pinned by WiFiSync.mod.
LIVE_EXPECT = {
    "libmpr.so": {"size": 12665872, "fnv4096": 0x409F7B77},
    "libInfraFsys.so": {"size": 411504, "fnv4096": 0xE1C5B626},
}
SYMBOLS = {
    "libmpr.so": ["_ZNK10MprMonCtrl16ConPutWriteStillEPN5MprIf13Cb_WriteStillE"],
    "libInfraFsys.so": ["Fsys_Open", "Fsys_Read", "Fsys_Close", "Fsys_Stat"],
}
HOOK_PROLOGUE = bytes.fromhex("2de9f0410d4600af04464049")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fnv1a(data: bytes, seed: int = 2166136261) -> int:
    h = seed
    for b in data:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def load_lib(name: str):
    path = FW / "lib" / name
    raw = path.read_bytes()
    elf = ELFFile(BytesIO(raw))
    syms = {s.name: s for s in read_symbols(path)}
    return path, raw, elf, syms


def disasm_thumb(raw: bytes, addr: int, size: int) -> list[str]:
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    return [f"{i.mnemonic} {i.op_str}" for i in md.disasm(raw[addr:addr + size], addr)]


def check_fsys_open_contract(raw: bytes, syms) -> dict:
    sym = syms["Fsys_Open"]
    start = sym.address & ~1
    lines = disasm_thumb(raw, start, sym.size)
    text = "\n".join(lines)
    default_read = bool(re.search(r"tst(?:\.w)?\s+r6,\s*#3", text)) and bool(
        re.search(r"orr(?:eq|s)?\s+r6,\s*r6,\s*#1", text)
    )
    handle_read_attr = bool(re.search(r"movs\s+r0,\s*#3", text)) and "strh" in text
    read_bit_switch = bool(re.search(r"tst(?:\.w)?\s+r6,\s*#1", text))
    create_bit = bool(re.search(r"and(?:s)?\s+r2,\s*lr,\s*#4", text))
    return {
        "symbol_value": f"{sym.address:#010x}",
        "address": f"{start:#010x}",
        "size": sym.size,
        "prologue_hex": raw[start:start + 8].hex(),
        "flags_read_is_bit0": default_read and read_bit_switch,
        "flags_create_is_bit2": create_bit,
        "handle_attr_read_is_3": handle_read_attr,
        "checks": {
            "tst_r6_3_default_read": default_read,
            "read_bit1_switch": read_bit_switch,
            "create_bit4": create_bit,
            "handle_attr_3": handle_read_attr,
        },
    }


def check_fsys_read(raw: bytes, elf, syms) -> dict:
    sym = syms["Fsys_Read"]
    start = sym.address & ~1
    plt = resolve_arm_plt(elf)
    lines = []
    for insn in Cs(CS_ARCH_ARM, CS_MODE_THUMB).disasm(raw[start:start + sym.size], start):
        note = ""
        if insn.mnemonic in ("bl", "blx"):
            m = re.search(r"#0x([0-9a-f]+)", insn.op_str)
            if m:
                target = int(m.group(1), 16) & ~1
                if target in plt:
                    note = f"  ; {plt[target]['name']}"
        lines.append(f"{insn.mnemonic} {insn.op_str}{note}")
    text = "\n".join(lines)
    uses_fread = "PrFILE_fread" in text
    return {
        "symbol_value": f"{sym.address:#010x}",
        "address": f"{start:#010x}",
        "size": sym.size,
        "prologue_hex": raw[start:start + 8].hex(),
        "returns_bytes_from_fread": uses_fread,
        "checks": {
            "prologue_2de9f04f": raw[start:start + 4] == bytes.fromhex("2de9f04f"),
            "calls_prfile_fread": uses_fread,
        },
    }


def check_fsys_stat(raw: bytes, elf, syms) -> dict:
    sym = syms["Fsys_Stat"]
    start = sym.address & ~1
    plt = resolve_arm_plt(elf)
    lines = []
    for insn in Cs(CS_ARCH_ARM, CS_MODE_THUMB).disasm(raw[start:start + sym.size], start):
        note = ""
        if insn.mnemonic in ("bl", "blx"):
            m = re.search(r"#0x([0-9a-f]+)", insn.op_str)
            if m:
                target = int(m.group(1), 16) & ~1
                if target in plt:
                    note = f"  ; {plt[target]['name']}"
        lines.append(f"{insn.mnemonic} {insn.op_str}{note}")
    text = "\n".join(lines)
    calls_fstat = "PrFILE_fstat" in text
    return {
        "symbol_value": f"{sym.address:#010x}",
        "address": f"{start:#010x}",
        "size": sym.size,
        "prologue_hex": raw[start:start + 8].hex(),
        "calls_PrFILE_fstat": calls_fstat,
        "stat_size_at_offset_0": True,   # verified in PFFILE_GetStat below
        "checks": {"calls_prfile_fstat": calls_fstat},
    }


def check_pffile_getstat(raw: bytes, syms) -> dict:
    sym = syms["PFFILE_GetStat"]
    start = sym.address & ~1
    code = raw[start:start + sym.size]
    lines = disasm_thumb(raw, start, sym.size)
    text = "\n".join(lines)
    strd = [l for l in lines if l.startswith("strd") and "[r1]" in l]
    return {
        "symbol_value": f"{sym.address:#010x}",
        "address": f"{start:#010x}",
        "size": sym.size,
        "stores_64bit_size_at_r1": bool(strd),
        "attribute_offset_0x1d": "strb" in text and "#0x1d" in text,
        "checks": {
            "strd_r1_size": bool(strd),
            "attr_0x1d": "strb" in text and "#0x1d" in text,
        },
    }


def check_hook_target(raw: bytes, syms) -> dict:
    name = "_ZNK10MprMonCtrl16ConPutWriteStillEPN5MprIf13Cb_WriteStillE"
    sym = syms[name]
    start = sym.address & ~1
    prologue = raw[start:start + 12]
    return {
        "symbol": name,
        "offset": f"{start:#010x}",
        "reference_address": f"{0x07144000 + start:#010x}",
        "size": sym.size,
        "prologue_hex": prologue.hex(),
        "prologue_matches_hook": prologue == HOOK_PROLOGUE,
        "checks": {"prologue_12_bytes": prologue == HOOK_PROLOGUE},
    }


def check_wlan_surface() -> dict:
    root = FW
    try:
        nvram = sorted((root / "share/network/nvram/high").glob("bcm4339_H_*.txt"))
    except OSError:
        nvram = []
    sample = {}
    if nvram:
        text = nvram[0].read_text(encoding="latin1")
        for key in ("aa5g", "aa2g", "rxchain", "txchain"):
            m = re.search(rf"^{key}=(\S+)", text, re.M)
            if m:
                sample[key] = m.group(1)
    bcm = root / "wlan/bcm/bcm4339.bin"
    clm_offset = None
    clm_api = None
    if bcm.is_file():
        data = bcm.read_bytes()
        idx = data.find(b"CLM DATA")
        if idx >= 0:
            clm_offset = idx
            chunk = data[idx:idx + 0x2000]
            m = re.search(rb"9\.10\.\d+", chunk)
            clm_api = m.group(0).decode() if m else None
    tools = {}
    for rel in ("bin/wpa_supplicant", "sbin/dhcpd", "bin/wl", "bin/iperf", "bin/iw"):
        p = root / rel
        tools[rel] = p.is_file() and p.stat().st_size > 0
    bsa = root / "bin/bsa_server"
    return {
        "nvram_high_profiles": len(nvram),
        "nvram_sample": sample,
        "nvram_5ghz_frontend": sample.get("aa5g") == "1",
        "bcm4339_bin_sha256": sha256_file(bcm) if bcm.is_file() else None,
        "clm_offset": clm_offset,
        "clm_api": clm_api,
        "tools": tools,
        "bsa_server": {
            "present": bsa.is_file(),
            "size": bsa.stat().st_size if bsa.is_file() else 0,
            "sha256": sha256_file(bsa) if bsa.is_file() else None,
        },
        "checks": {
            "all_tools_present": all(tools.values()),
            "nvram_aa5g": sample.get("aa5g") == "1",
            "clm_present": clm_offset is not None,
            "bsa_server_present": bsa.is_file(),
        },
    }


def build_manifest(fw_root: Path = FW) -> dict:
    global FW
    FW = fw_root
    libs = {}
    checks = {}
    for name in ("libmpr.so", "libInfraFsys.so", "libWlanResource.so"):
        path = FW / "lib" / name
        raw = path.read_bytes()
        libs[name] = {
            "path": str(path.relative_to(ROOT)),
            "size": len(raw),
            "sha256": sha256_file(path),
            "fnv1a_4096": f"{fnv1a(raw[:4096]):#010x}",
        }
        if name in EXPECT:
            checks[f"{name}_size"] = len(raw) == EXPECT[name]["size"]
            checks[f"{name}_fnv4096"] = fnv1a(raw[:4096]) == EXPECT[name]["fnv4096"]

    _, mpr_raw, _, mpr_syms = load_lib("libmpr.so")
    _, fsy_raw, fsy_elf, fsy_syms = load_lib("libInfraFsys.so")

    fsys_open = check_fsys_open_contract(fsy_raw, fsy_syms)
    fsys_read = check_fsys_read(fsy_raw, fsy_elf, fsy_syms)
    fsys_stat = check_fsys_stat(fsy_raw, fsy_elf, fsy_syms)
    pffile_stat = check_pffile_getstat(fsy_raw, fsy_syms)
    hook = check_hook_target(mpr_raw, mpr_syms)
    wlan = check_wlan_surface()

    for section in (fsys_open, fsys_read, fsys_stat, pffile_stat, hook):
        checks.update(section["checks"])
    checks.update(wlan["checks"])

    # Cross-check the mod's pinned constants against the manifest.
    mod_src = (ROOT / "mods/wifisync/wifisync_mod.c").read_text(encoding="utf-8")
    checks["mod_pins_mpr_prologue"] = "2d\\xe9\\xf0\\x41\\x0d\\x46\\x00\\xaf" in mod_src
    checks["mod_pins_fsys_open"] = "\\x2d\\xe9\\xf0\\x4f\\x89\\x46" in mod_src
    checks["mod_pins_libmpr_len"] = str(LIVE_EXPECT["libmpr.so"]["size"]) in mod_src
    checks["mod_pins_fsys_len"] = str(LIVE_EXPECT["libInfraFsys.so"]["size"]) in mod_src

    manifest = {
        "status": "wifisync_contracts_offline",
        "firmware_root": str(FW.relative_to(ROOT)),
        "libraries": libs,
        "fsys_open": fsys_open,
        "fsys_read": fsys_read,
        "fsys_stat": fsys_stat,
        "pffile_stat": pffile_stat,
        "hook": hook,
        "wlan": wlan,
        "checks": checks,
        "failed": [k for k, ok in checks.items() if not ok],
        "limitations": [
            "Static/disassembly assertions only; no live camera access.",
            "Fsys thread-safety and actual read results are not exercised here.",
            "5 GHz live channel exposure (wl bands/chanlist) needs Phase 1 on hardware.",
            "BLE wake surface is recorded as a binary identity, not a usable GATT contract.",
        ],
    }
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--firmware-root", type=Path, default=FW)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    manifest = build_manifest(args.firmware_root)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for name, ok in manifest["checks"].items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"checks={len(manifest['checks'])} failed={len(manifest['failed'])}")
    return 1 if manifest["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
