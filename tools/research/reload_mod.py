"""Reload one SMOD v2 mod on a camera running a reload-capable Mod Loader v2 core (RAM only).

    .\\venv\\Scripts\\python.exe -m tools.research.reload_mod --mod artifacts/tests/NEW_BUILD/MenuTouchFull.mod \\
        [--core artifacts/tests/mlv2-reload-build-1/core.bin] [--timeout 120]

Steps (all camera writes stay under /tmp_bt):
  1. optional core upgrade: core.bin.tmp is pushed, read back and renamed over core.bin;
  2. the mod goes to /tmp_bt/mods/.NAME.part (not scanned: no .mod suffix), is read back and
     renamed over /tmp_bt/mods/NAME;
  3. the reload request (file name only) is published with the same temp + rename pattern;
  4. the core consumes it on the Sony UI thread at the next menu refresh or root setup and
     writes /tmp_bt/mlv2/reload.result. Open or move in the Sony menu while the tool waits;
  5. on reload_ok the hooks and resident code are read back from im.elf and compared with the file.
The core loads and hash-checks the new copy and checks its hook targets before it switches the old
copy OFF, and stops if OFF does not restore the original bytes. Writes a JSON receipt next to --mod.
Run it only with an explicit go-ahead for that camera session.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import time
from pathlib import Path

SMOD_MAGIC = 0x444F4D53
NO_FIXUP = 0xFF
REQ, RESULT = "/tmp_bt/mlv2/reload", "/tmp_bt/mlv2/reload.result"
CORE_MARKER = REQ.encode()
IM_PID = 149


def parse_smod_v2(data: bytes) -> dict:
    """Header fields and hook records of an SMOD v2 file (same checks as the core's parser)."""
    if len(data) < 68:
        raise ValueError("file shorter than the SMOD header")
    magic, version, flags = struct.unpack_from("<3I", data, 0)
    name = data[12:44].split(b"\0")[0].decode("ascii", "replace")
    code_size, fnv, bss, on_off, off_off, hook_count = struct.unpack_from("<6I", data, 44)
    if magic != SMOD_MAGIC or version != 2:
        raise ValueError("not an SMOD v2 file")
    if not code_size or code_size & 3 or hook_count > 8 or code_size + bss > 1024 * 1024:
        raise ValueError("SMOD v2 header outside the loader limits")
    hooks = []
    for i in range(hook_count):
        off = 68 + 40 * i
        target, size, fixup = struct.unpack_from("<IBB", data, off)
        if not 1 <= size <= 16 or (fixup != NO_FIXUP and fixup + 4 > size):
            raise ValueError(f"hook {i} outside the loader limits")
        hooks.append({"target": target, "size": size, "fixup": fixup,
                      "orig": data[off + 8:off + 8 + size], "tmpl": data[off + 24:off + 24 + size]})
    code_off = 68 + 40 * hook_count
    code = data[code_off:code_off + code_size]
    if len(code) != code_size or fnv1a_32(code) != fnv:
        raise ValueError("code bytes do not match the header hash")
    return {"name": name, "flags": flags, "code_size": code_size, "fnv1a_32": fnv, "bss_size": bss,
            "on_off": on_off, "off_off": off_off, "hooks": hooks, "code": code}


def fnv1a_32(data: bytes) -> int:
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def hook_bytes(hook: dict, base: int) -> bytes:
    """Bytes the core writes at a hook target for a copy loaded at base."""
    out = bytearray(hook["tmpl"])
    if hook["fixup"] != NO_FIXUP:
        f = hook["fixup"]
        struct.pack_into("<I", out, f, (struct.unpack_from("<I", out, f)[0] + base) & 0xFFFFFFFF)
    return bytes(out)


def parse_result(text: str) -> tuple[str, int] | None:
    parts = text.strip().split()
    if len(parts) != 2 or not parts[0].startswith("reload_"):
        return None
    return parts[0], int(parts[1], 16)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mod", required=True, type=Path)
    ap.add_argument("--core", type=Path, help="reload-capable core.bin to stage first (optional)")
    ap.add_argument("--timeout", type=float, default=120.0)
    a = ap.parse_args()
    data = a.mod.read_bytes()
    smod = parse_smod_v2(data)
    name = a.mod.name
    if not name.endswith(".mod") or "/" in name or len(name) > 47:
        raise SystemExit("mod file name must be NAME.mod, at most 47 characters")
    core = a.core.read_bytes() if a.core else None
    if core is not None and CORE_MARKER not in core:
        raise SystemExit("--core does not contain the reload request path; it is not reload-capable")

    from tools.camera_debugger import OP_EXEC, connect

    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    R = {"mod": str(a.mod), "mod_sha256": sha(data), "name": smod["name"], "fnv1a_32": f"{smod['fnv1a_32']:#010x}",
         "core_sha256": sha(core) if core else None, "steps": []}

    def ex(d, c):
        r = d.request(OP_EXEC, c.encode())
        out = r["payload"].decode(errors="replace")
        R["steps"].append({"exec": c, "status": r["status"], "flags": r["flags"], "out": out})
        return out

    with connect("extcmd") as d:
        R["uptime_before"] = ex(d, "cat /proc/uptime").strip()
        R["comm"] = ex(d, f"cat /proc/{IM_PID}/comm").strip()
        R["hooks_before"] = {f"{h['target']:#x}": d.memory(IM_PID, h["target"], h["size"]).hex() for h in smod["hooks"]}
        if R["comm"] != "im.elf":
            R["stopped"] = f"PID {IM_PID} is not im.elf; nothing changed"
        elif ex(d, "test -f /tmp_bt/mlv2/core.bin && echo CORE_PRESENT").strip() != "CORE_PRESENT":
            R["stopped"] = "no Mod Loader v2 core staged; install the loader first"
        else:
            if core is not None:
                R["steps"].append({"push": "core", **d.push("/tmp_bt/mlv2/core.bin.tmp", core)})
                ex(d, "mv -f /tmp_bt/mlv2/core.bin.tmp /tmp_bt/mlv2/core.bin")
            part, dst = f"/tmp_bt/mods/.{name}.part", f"/tmp_bt/mods/{name}"
            R["steps"].append({"push": name, **d.push(part, data)})
            ex(d, f"mv -f {part} {dst}")
            ex(d, f"rm -f {RESULT}")
            R["steps"].append({"push": "request", **d.push(REQ + ".part", name.encode() + b"\n")})
            ex(d, f"mv -f {REQ}.part {REQ}")
            print("Request published. Open the Sony menu or move within it to let the core act.")
            deadline, result = time.monotonic() + a.timeout, None
            while time.monotonic() < deadline:
                out = ex(d, f"cat {RESULT} 2>/dev/null; test -f {REQ} && echo PENDING")
                result = parse_result(out.split("\n")[0]) if out.strip() else None
                if result:
                    break
                time.sleep(1.0)
            R["result"] = f"{result[0]} {result[1]:08x}" if result else None
            if result is None:
                R["stopped"] = "no result before the timeout; the request may still be pending"
            elif result[0] == "reload_ok":
                base = result[1]
                R["hooks_after"] = {}
                ok = True
                for h in smod["hooks"]:
                    live = d.memory(IM_PID, h["target"], h["size"])
                    R["hooks_after"][f"{h['target']:#x}"] = live.hex()
                    ok &= live == hook_bytes(h, base)
                code = b"".join(d.memory(IM_PID, base + o, min(4096, smod["code_size"] - o))
                                for o in range(0, smod["code_size"], 4096))
                R["hooks_point_to_new_copy"] = ok
                R["resident_code_matches_file"] = code == smod["code"]
        R["final"] = ex(d, "cat /proc/uptime; ls -A /tmp_bt /tmp_bt/mlv2 /tmp_bt/mods; "
                           "tail -8 /tmp_bt/mods/mod_loader.log 2>/dev/null")
        R["ping"] = d.ping()
    out = a.mod.parent / ("reload-receipt-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
    out.write_text(json.dumps(R, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in R.items() if k != "steps"}, indent=1))
    print("receipt:", out)


if __name__ == "__main__":
    main()
