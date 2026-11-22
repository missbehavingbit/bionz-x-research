"""Package Mod Loader v2: shim payload (+ entry points) and core.bin (MlCoreHeader + code)."""
import hashlib
import json
import struct
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
from tools.research.pack_mod import extract_elf_text, fnv1a_32  # noqa: E402

SHIM_ADDR, SHIM_CAP = 0x05662800, 2048
SHIM_MOVE, VIEW_MOVE = 0x1000, 0x10000      # shim-reloc.elf: shim and view base moved by these
CORE_MAGIC, CORE_VERSION, CORE_MAX = 0x52434C4D, 2, 64 * 1024

out = Path(sys.argv[1])
shim_addr, shim, ssyms = extract_elf_text((out / "shim.elf").read_bytes(), expected_code_addr=SHIM_ADDR)
if len(shim) > SHIM_CAP or shim[:4] != b"\0\0\0\0":
    raise SystemExit("shim too large or slot word not zero")
entries = {k: ssyms[k] for k in ("ml_hook_set_root", "ml_hook_refresh", "ml_hook_open_item")}
if not all(v & 1 and SHIM_ADDR < v < SHIM_ADDR + len(shim) for v in entries.values()):
    raise SystemExit(f"bad shim entries {entries}")
(out / "shim.bin").write_bytes(shim)

# Relocations: every aligned word that differs between the two links must differ by exactly one
# of the two moves; anything else (an instruction that changed) stops the build.
_, shim_b, _ = extract_elf_text((out / "shim-reloc.elf").read_bytes(), expected_code_addr=SHIM_ADDR + SHIM_MOVE)
if len(shim_b) != len(shim):
    raise SystemExit("relocation link changed the shim size")
relocs = []
for off in range(0, len(shim), 4):
    a, b = struct.unpack_from("<I", shim, off)[0], struct.unpack_from("<I", shim_b, off)[0]
    if a == b:
        continue
    delta = (b - a) & 0xFFFFFFFF
    if delta == SHIM_MOVE:
        relocs.append(off)
    elif delta == VIEW_MOVE:
        relocs.append(off | 1 << 30)
    else:
        raise SystemExit(f"shim word {off:#x} differs by {delta:#x}; not a relocatable address")

core_addr, core, csyms = extract_elf_text((out / "core.elf").read_bytes(), expected_code_addr=0)
init = csyms.get("core_init", 0)
if init != 1 or len(core) > CORE_MAX:
    raise SystemExit(f"core_init must be the Thumb entry at offset 0 (got {init:#x}); size {len(core)}")
core_bin = struct.pack("<5I", CORE_MAGIC, CORE_VERSION, len(core), fnv1a_32(core), init) + core
(out / "core.bin").write_bytes(core_bin)

info = {
    "shim": {"addr": hex(shim_addr), "bytes": len(shim), "cap": SHIM_CAP, "fnv1a_32": hex(fnv1a_32(shim)),
             "sha256": hashlib.sha256(shim).hexdigest(), "entries": {k: hex(v) for k, v in entries.items()},
             "relocs": {"testcmd": [hex(r) for r in relocs if not r >> 30],
                        "view": [hex(r & 0x3FFFFFFF) for r in relocs if r >> 30]}},
    "core": {"bytes": len(core), "file_bytes": len(core_bin), "fnv1a_32": hex(fnv1a_32(core)),
             "sha256": hashlib.sha256(core_bin).hexdigest()},
}
(out / "build.json").write_text(json.dumps(info, indent=2) + "\n")
print(json.dumps(info, indent=2))

# Guard request (ML2Q v1): v2 hook entries plus the hook entries of every known v1 build, so an
# "upgrade" recognizes whichever v1 loader is resident. Triples come from the v1 install requests
# (header words 5..7) and are re-checked against those files when they are present.
V1_BUILDS = {
    "sd-standalone-v1": ((0x45E4E7D, 0x45E4ECD, 0x45E4EDD),
                         "artifacts/publish/mod-loader-sd/artifacts/hardware/sd-standalone-v1/mod-loader-install.req"),
    "lifecycle-v1": ((0x45E4EA1, 0x45E4EF1, 0x45E4F01),
                     "artifacts/tests/arri-mod-loader-size-2026-11-16/lifecycle-reqs/mod-loader-install.req"),
}
for name, (triple, rel) in V1_BUILDS.items():
    req = root / rel
    if req.exists() and struct.unpack_from("<3I", req.read_bytes(), 20) != triple:
        raise SystemExit(f"v1 entries for {name} do not match {rel}")
REQ_MAGIC = 0x5132484D
e = [entries[k] for k in ("ml_hook_set_root", "ml_hook_refresh", "ml_hook_open_item")]
# ML2Q v2: v1 fields plus the shim relocation list (the guard relocates for the live layout).
request = struct.pack("<9I", REQ_MAGIC, 2, SHIM_ADDR, len(shim), *e, fnv1a_32(shim), len(V1_BUILDS))
request += b"".join(struct.pack("<3I", *t) for t, _ in V1_BUILDS.values()) + shim
request += struct.pack(f"<{1 + len(relocs)}I", len(relocs), *relocs)
(out / "mlv2.req").write_bytes(request)
info["request"] = {"bytes": len(request), "sha256": hashlib.sha256(request).hexdigest(),
                   "v1_builds": list(V1_BUILDS)}
(out / "build.json").write_text(json.dumps(info, indent=2) + "\n")
print("request", len(request), info["request"]["sha256"])
