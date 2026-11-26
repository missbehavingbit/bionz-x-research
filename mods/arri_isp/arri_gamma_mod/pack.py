"""Pack the ARRI gamma ELF as an SMOD v1 lifecycle mod (flag bit 1, no hooks)."""
import hashlib, json, sys
from pathlib import Path
root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
from tools.research.pack_mod import extract_elf_text, pack_smod
elf, preset, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
addr, code, syms = extract_elf_text(elf.read_bytes(), expected_code_addr=0x05662760)
entry = syms.get("arri_life", 0)
if entry != (addr | 1):
    raise SystemExit(f"arri_life must be the Thumb entry at the code start, got {entry:#x}")
if len(code) > 0x05663000 - addr:
    raise SystemExit("code exceeds cave")
name = {"classic": "ARRI Classic Gamma", "logc3": "ARRI LogC3 Gamma"}[preset]
blob = pack_smod(name=name, code_addr=addr, code=code, hooks=[], flags=2)
fname = {"classic": "ARRIClassic.mod", "logc3": "ARRILogC3.mod"}[preset]
(out / fname).write_bytes(blob)
info = {"name": name, "file": fname, "code_addr": hex(addr), "code_bytes": len(code), "cave_bytes": 0x05663000 - addr,
        "flags": 2, "hooks": 0, "size": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
(out / "build.json").write_text(json.dumps(info, indent=2) + "\n")
print(json.dumps(info, indent=2))
