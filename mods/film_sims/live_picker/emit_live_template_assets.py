"""Emit the Live Picker SMOD v2 skeleton and its layout record.

The ARM build script (`build_live_template_wsl.sh`) compiles `film_sim_live_template.c`, packs it with
`tools.research.pack_mod_v2` (ACTION flag, 48-byte bss, the `event_push` hook and the RLC1 relocation
table) and compiles a probe that records the `struct live_payload` field offsets for the target ABI.
This module checks the packed skeleton and writes:

* `isp-studio/crates/bionz-preset/assets/film_sim_live_template.mod` - the skeleton, byte for byte.
* `isp-studio/crates/bionz-preset/assets/film_sim_live_template.json` - where the code, the payload and
  each field sit, plus the header fields the Rust packer must keep.

`bionz-preset::smod_v2::emit_live_smod_v2` clones the skeleton and rewrites only the name, the payload
and the code checksum; the hook, the bss and the relocation table must therefore be exactly what is
verified here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.research.pack_mod import extract_elf_text, fnv1a_32  # noqa: E402

PAYLOAD_MAGIC = 0x4C564C31  # "LVL1"
SMOD_MAGIC = 0x444F4D53
SMOD_HEADER_SIZE = 68
SMOD_HOOK_SIZE = 40
FLAG_RELOC = 4
FLAG_ACTION = 16
RELOC_MAGIC = 0x31434C52

# Must match the order of the `offsetof` probe in build_live_template_wsl.sh.
OFFSET_FIELDS = (
    "size", "magic", "ec_gamma_pa", "fc_gamma_pa", "color_a0_pa", "color_a1_pa", "color_b2_pa",
    "cc_tuning_pa", "refadr0_pa", "n_presets", "n_looks", "log_path", "names", "lut_idx",
    "ec_stock", "fc_stock", "a0_stock", "a1_stock", "b2_stock",
    "ec", "fc", "a0", "a1", "b2", "refadr",
)
# (name, element size, elements per look or 1 for the shared stock tables)
ARRAYS = {
    "ec_stock": (4, 96), "fc_stock": (2, 96), "a0_stock": (2, 9), "a1_stock": (2, 9), "b2_stock": (2, 9),
    "ec": (4, 96), "fc": (2, 96), "a0": (2, 9), "a1": (2, 9), "b2": (2, 9), "refadr": (1, 1215),
}


def read_offsets(path: Path) -> dict[str, int]:
    raw = path.read_bytes()
    if len(raw) != 4 * len(OFFSET_FIELDS):
        raise SystemExit(f"{path}: expected {4 * len(OFFSET_FIELDS)} bytes, got {len(raw)}")
    return dict(zip(OFFSET_FIELDS, struct.unpack("<%dI" % len(OFFSET_FIELDS), raw)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mod", type=Path, required=True)
    ap.add_argument("--elf", type=Path, required=True)
    ap.add_argument("--offsets", type=Path, required=True)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--compiler", required=True)
    ap.add_argument("--flags", required=True)
    ap.add_argument("--hook", required=True)
    args = ap.parse_args()

    blob = args.mod.read_bytes()
    magic, version, flags, raw_name, code_len, checksum, bss_size, on_off, off_off, hooks = struct.unpack_from(
        "<III32s6I", blob)
    if magic != SMOD_MAGIC or version != 2:
        raise SystemExit(f"{args.mod}: not an SMOD v2 file")
    if flags & FLAG_ACTION == 0 or flags & FLAG_RELOC == 0:
        raise SystemExit(f"{args.mod}: the live skeleton needs FLAG_ACTION and FLAG_RELOC, flags={flags:#x}")
    if hooks != 1:
        raise SystemExit(f"{args.mod}: expected exactly one hook, got {hooks}")
    if bss_size != 48:
        raise SystemExit(f"{args.mod}: expected 48 bytes of bss, got {bss_size}")
    if not on_off & 1:
        raise SystemExit(f"{args.mod}: lifecycle entry {on_off:#x} is not Thumb")
    code_off = SMOD_HEADER_SIZE + hooks * SMOD_HOOK_SIZE
    code = blob[code_off:code_off + code_len]
    if len(code) != code_len or fnv1a_32(code) != checksum:
        raise SystemExit(f"{args.mod}: code slice or FNV-1a checksum does not match the header")
    trail = blob[code_off + code_len:]
    reloc_magic, nlib, nrel = struct.unpack_from("<IHH", trail)
    if reloc_magic != RELOC_MAGIC or not nlib or not nrel:
        raise SystemExit(f"{args.mod}: no RLC1 relocation table after the code")
    if nlib != 1 or nrel != 1:
        raise SystemExit(
            f"{args.mod}: expected exactly one relocated word (the trampoline's resume address in libObj), "
            f"got {nrel} word(s) across {nlib} library(ies); a data blob is being treated as addresses"
        )
    reloc_len = 8 + nlib * 32 + nrel * 4
    if fnv1a_32(trail[:reloc_len]) != struct.unpack_from("<I", trail, reloc_len)[0]:
        raise SystemExit(f"{args.mod}: relocation table FNV-1a mismatch")
    if len(trail) != reloc_len + 4:
        raise SystemExit(f"{args.mod}: unexpected bytes after the relocation table")

    _, elf_code, syms = extract_elf_text(args.elf.read_bytes(), expected_code_addr=0)
    if elf_code != code:
        raise SystemExit("the ELF .text and the packed code differ; rebuild the skeleton")

    fields = read_offsets(args.offsets)
    size = fields["size"]
    if fields["magic"] != 0 or size <= 0 or size % 4:
        raise SystemExit(f"bad payload layout: magic at {fields['magic']}, size {size}")
    starts = sorted((v, k) for k, v in fields.items() if k != "size")
    for index, (start, name) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else size
        if name in ARRAYS:
            elem, count = ARRAYS[name]
            need = elem * count
        elif name == "names":
            need = 16 * 16
        elif name == "lut_idx":
            need = 16
        elif name == "log_path":
            need = 64
        else:
            need = 4  # a uint32 scalar
        if end - start < need:
            raise SystemExit(f"payload field {name} is {end - start} bytes, need {need}")

    magic_word = struct.pack("<I", PAYLOAD_MAGIC)
    hits = [i for i in range(0, len(code) - 3, 4) if code[i:i + 4] == magic_word]
    if len(hits) != 1:
        raise SystemExit(f"expected exactly one LIVE_PAYLOAD_MAGIC in .text, found {len(hits)}")
    payload_off = hits[0]
    if syms.get("live_payload") != payload_off:
        raise SystemExit(f"live_payload symbol {syms.get('live_payload')} != magic offset {payload_off}")
    if payload_off + size > len(code):
        raise SystemExit(f"payload {payload_off:#x}+{size} runs past the {len(code)}-byte code")

    entry = syms.get("live_event_push", 0)
    if not entry & 1 or entry >= code_len:
        raise SystemExit(f"live_event_push is not a Thumb entry inside the code (got {entry:#x})")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "film_sim_live_template.mod").write_bytes(blob)
    record = {
        "schema": "bionz-live-picker-template/1",
        "source": args.source.as_posix(),
        "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "compiler": args.compiler,
        "flags": args.flags,
        "hook": args.hook,
        "mod_bytes": len(blob),
        "mod_sha256": hashlib.sha256(blob).hexdigest(),
        "name": raw_name.split(b"\0", 1)[0].decode(),
        "smod_flags": flags,
        "hook_count": hooks,
        "bss_size": bss_size,
        "on_off": f"{on_off:#x}",
        "off_off": f"{off_off:#x}",
        "code_offset": code_off,
        "code_len": code_len,
        "code_fnv1a_32": f"{checksum:#010x}",
        "reloc": {"libs": nlib, "words": nrel, "bytes": reloc_len + 4},
        "event_push_offset": f"{entry:#x}",
        "payload": {
            "magic": f"{PAYLOAD_MAGIC:#010x}",
            "offset": payload_off,
            "size": size,
            "fields": {name: value for name, value in fields.items() if name != "size"},
        },
    }
    (args.out_dir / "film_sim_live_template.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"mod_bytes": len(blob), "code_len": code_len, "payload_offset": payload_off,
                      "payload_size": size, "on_off": record["on_off"], "reloc_words": nrel}))


if __name__ == "__main__":
    main()
