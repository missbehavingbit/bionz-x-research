"""Pack the ARRI color ELF as an SMOD v1 lifecycle mod (flag bit 1, no hooks)."""
import hashlib, json, sys
from pathlib import Path
root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
from tools.research.pack_mod import extract_elf_text, pack_smod
elf, preset, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
addr, code, syms = extract_elf_text(elf.read_bytes(), expected_code_addr=0x0414f7f0)
if syms.get("arri_life", 0) != (addr | 1):
    raise SystemExit("arri_life must be the Thumb entry at the code start")
if len(code) > 0x04150000 - addr:
    raise SystemExit("code exceeds cave")
name = {"rec709": "ARRI Classic Color", "awg3": "ARRI AWG3 Color"}[preset]
fname = {"rec709": "ARRIColor.mod", "awg3": "ARRIColorAWG3.mod"}[preset]
blob = pack_smod(name=name, code_addr=addr, code=code, hooks=[], flags=2)
(out / fname).write_bytes(blob)
info = {"name": name, "file": fname, "code_addr": hex(addr), "code_bytes": len(code), "cave_bytes": 0x04150000 - addr,
        "flags": 2, "size": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
(out / "build.json").write_text(json.dumps(info, indent=2) + "\n"); print(json.dumps(info, indent=2))
