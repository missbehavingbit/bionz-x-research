"""Pack Arrivederci as an SMOD v1 lifecycle mod (flag bit 1, no hooks)."""
import hashlib
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
from tools.research.pack_mod import extract_elf_text, pack_smod  # noqa: E402

elf, out = Path(sys.argv[1]), Path(sys.argv[2])
addr, code, syms = extract_elf_text(elf.read_bytes(), expected_code_addr=0x05662760)
if syms.get("arri_life", 0) != (addr | 1) or len(code) > 0x05663000 - addr:
    raise SystemExit("arri_life must be the Thumb entry at the code start and fit the cave")
blob = pack_smod(name="Arrivederci", code_addr=addr, code=code, hooks=[], flags=2)
(out / "Arrivederci.mod").write_bytes(blob)
info = {"name": "Arrivederci", "file": "Arrivederci.mod", "code_addr": hex(addr), "code_bytes": len(code),
        "cave_bytes": 0x05663000 - addr, "flags": 2, "hooks": 0, "size": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest()}
(out / "build.json").write_text(json.dumps(info, indent=2) + "\n")
print(json.dumps(info, indent=2))
