"""Emit the position-independent Film Simulation SMOD v2 template and its layout record.

The ARM build script (`build_template_wsl.sh`) compiles `film_sim_template.c`, links it at 0 with
`tools/native_camera/mlv2/mod_v2.ld`, and also compiles a tiny ARM probe that records the
`struct sim_payload` field offsets for the target ABI. This module turns those artifacts into:

* `isp-studio/crates/bionz-preset/assets/film_sim_template.blob` - the raw `.text` blob.
* `isp-studio/crates/bionz-preset/assets/film_sim_template.json` - code length, FNV-1a checksum,
  `sim_life` offset and the payload offsets the Rust packer writes through.

It refuses to emit an asset whose checksum, symbol or magic would not survive a clone-and-patch.
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

PAYLOAD_MAGIC = 0x4B4C5A42
# Must match the order of the `offsetof` probe in build_template_wsl.sh.
OFFSET_FIELDS = (
    "size",
    "magic",
    "ec_gamma_pa",
    "fc_gamma_pa",
    "color_a0_pa",
    "color_a1_pa",
    "color_b2_pa",
    "cc_tuning_pa",
    "refadr0_pa",
    "mod_tag",
    "mod_log_path",
    "ec_stock",
    "ec_sim",
    "fc_stock",
    "fc_sim",
    "a0_stock",
    "a0_sim",
    "a1_stock",
    "a1_sim",
    "b2_stock",
    "b2_sim",
    "refadr_sim",
    "pad",
)
# Fixed sizes of the arrays the packer writes; a layout change must fail here, not on a camera.
EXPECTED_ARRAY_SIZES = {"ec_stock": 96 * 4, "ec_sim": 96 * 4, "fc_stock": 96 * 2, "fc_sim": 96 * 2,
                        "a0_stock": 9 * 2, "a0_sim": 9 * 2, "a1_stock": 9 * 2, "a1_sim": 9 * 2,
                        "b2_stock": 9 * 2, "b2_sim": 9 * 2, "refadr_sim": 1215}


def read_offsets(path: Path) -> dict[str, int]:
    raw = path.read_bytes()
    if len(raw) != 4 * len(OFFSET_FIELDS):
        raise SystemExit(f"{path}: expected {4 * len(OFFSET_FIELDS)} bytes, got {len(raw)}")
    values = struct.unpack("<%dI" % len(OFFSET_FIELDS), raw)
    return dict(zip(OFFSET_FIELDS, values))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--elf", type=Path, required=True)
    ap.add_argument("--offsets", type=Path, required=True, help="raw little-endian offsetof probe output")
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--compiler", required=True)
    ap.add_argument("--flags", required=True)
    args = ap.parse_args()

    code_addr, code, syms = extract_elf_text(args.elf.read_bytes(), expected_code_addr=0)
    if code_addr != 0:
        raise SystemExit(f".text is linked at {code_addr:#x}, not 0")
    if not code or len(code) % 4:
        raise SystemExit("template .text is empty or not 4-byte aligned")

    on_off = syms.get("sim_life", 0)
    if not on_off & 1 or on_off >= len(code):
        raise SystemExit(f"sim_life must be a Thumb entry inside the code (got {on_off:#x})")

    fields = read_offsets(args.offsets)
    size = fields["size"]
    if fields["magic"] != 0:
        raise SystemExit("payload magic must be the first field")
    if size <= 0 or size % 4:
        raise SystemExit(f"payload size {size} must be a positive multiple of 4")
    for name, expected in EXPECTED_ARRAY_SIZES.items():
        start = fields[name]
        after = next(v for k, v in fields.items() if k != "size" and v > start) if name != "refadr_sim" else fields["pad"]
        if after - start < expected:
            raise SystemExit(f"payload field {name} is {after - start} bytes, need {expected}")
    if fields["pad"] != size - 1:
        raise SystemExit(f"pad offset {fields['pad']} is not the last byte of a {size}-byte payload")

    magic = struct.pack("<I", PAYLOAD_MAGIC)
    hits = [i for i in range(0, len(code) - 3, 4) if code[i:i + 4] == magic]
    if len(hits) != 1:
        raise SystemExit(f"expected exactly one SIM_PAYLOAD_MAGIC word in .text, found {len(hits)}")
    payload_off = hits[0]
    if payload_off + size > len(code):
        raise SystemExit(f"payload at {payload_off:#x}+{size} runs past the {len(code)}-byte code")
    if syms.get("sim_payload") != payload_off:
        raise SystemExit(f"sim_payload symbol {syms.get('sim_payload')} != magic offset {payload_off}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "film_sim_template.blob").write_bytes(code)
    record = {
        "schema": "bionz-film-sim-template/1",
        "source": args.source.as_posix(),
        "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "compiler": args.compiler,
        "flags": args.flags,
        "code_len": len(code),
        "code_fnv1a_32": f"{fnv1a_32(code):#010x}",
        "code_sha256": hashlib.sha256(code).hexdigest(),
        "on_off": f"{on_off:#x}",
        "off_off": f"{on_off:#x}",
        "payload": {
            "magic": f"{PAYLOAD_MAGIC:#010x}",
            "offset": payload_off,
            "size": size,
            "fields": {name: value for name, value in fields.items() if name != "size"},
        },
    }
    (args.out_dir / "film_sim_template.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"code_len": len(code), "payload_offset": payload_off, "payload_size": size,
                      "on_off": record["on_off"], "fnv1a_32": record["code_fnv1a_32"]}))


if __name__ == "__main__":
    main()
