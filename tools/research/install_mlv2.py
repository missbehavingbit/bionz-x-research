"""Install Mod Loader v2 (M1, RAM-only) on a running camera over the debugger.

    .\\venv\\Scripts\\python.exe -m tools.research.install_mlv2 --build artifacts/tests/mlv2-build-4 \\
        [--mod artifacts/tests/arrivederci-v2-build-1/Arrivederci.mod] [--check-only]

Works at any time after boot: with stock view hooks it runs "install"; with a v1 loader resident
(SD boot package or lifecycle build) it runs "upgrade", whether or not the menu was opened.
Order: stage core.bin atomically (temp file + mv) -> stage mods -> guard check -> guard
install/upgrade -> remove guard and request -> read back hooks. All camera writes stay in RAM and
under /tmp_bt; a restart (or battery pull) returns the camera to its previous loader.
Writes a JSON receipt under the build directory.
"""
import argparse
import hashlib
import json
import struct
import time
from pathlib import Path

from tools.camera_debugger import OP_EXEC, connect

HOOKS = {"set_root": (0x15B91654, 16), "refresh": (0x15B91A80, 16), "open_item": (0x15B92044, 12)}
GUARD, REQ = "/tmp_bt/mlv2_guard", "/tmp_bt/mlv2.req"


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", required=True, type=Path)
    ap.add_argument("--mod", action="append", type=Path, default=[])
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()
    info = json.loads((a.build / "build.json").read_text())
    core, req, guard = ((a.build / n).read_bytes() for n in ("core.bin", "mlv2.req", "mlv2_guard_arm"))
    if sha(core) != info["core"]["sha256"] or sha(req) != info["request"]["sha256"]:
        raise SystemExit("build.json does not match core.bin / mlv2.req")
    entries = struct.unpack_from("<3I", req, 16)
    R = {"build": str(a.build), "core_sha256": sha(core), "request_sha256": sha(req),
         "guard_sha256": sha(guard), "mods": {}, "steps": []}

    def ex(d, c):
        r = d.request(OP_EXEC, c.encode())
        out = r["payload"].decode(errors="replace")
        R["steps"].append({"exec": c, "status": r["status"], "flags": r["flags"], "out": out})
        return out

    def hooks(d):
        return {k: d.memory(149, addr, n).hex() for k, (addr, n) in HOOKS.items()}

    with connect("extcmd") as d:
        R["uptime_before"] = ex(d, "cat /proc/uptime").strip()
        R["comm"] = ex(d, "cat /proc/149/comm").strip()
        R["hooks_before"] = hooks(d)
        if R["comm"] != "im.elf":
            R["stopped"] = "PID 149 is not im.elf; nothing changed"
        else:
            ex(d, "mkdir -p /tmp_bt/mlv2 /tmp_bt/mods")
            R["steps"].append({"push": "guard", **d.push(GUARD, guard)})
            R["steps"].append({"push": "request", **d.push(REQ, req)})
            ex(d, f"chmod 700 {GUARD}")
            out = ex(d, f"{GUARD} check {REQ}; echo GUARD_EXIT=$?")
            state = next((l for l in out.splitlines() if l.startswith("STATE ")), "")
            R["state"] = state
            op = "install" if "hooks=stock" in state else "upgrade" if "hooks=v1" in state else None
            if "hooks=v2" in state and "shim=shim" in state:
                op = "already"
            if a.check_only or op is None:
                R["stopped"] = "check only" if a.check_only else "hook state not stock/v1/v2; nothing changed"
            else:
                # Core first (atomic rename), so the shim never sees a half-written core.bin.
                R["steps"].append({"push": "core", **d.push("/tmp_bt/mlv2/core.bin.tmp", core)})
                ex(d, "mv -f /tmp_bt/mlv2/core.bin.tmp /tmp_bt/mlv2/core.bin")
                for m in a.mod:
                    data = m.read_bytes()
                    dst = f"/tmp_bt/mods/{m.name}"
                    R["steps"].append({"push": m.name, **d.push(dst + ".tmp", data)})
                    ex(d, f"mv -f {dst}.tmp {dst}")
                    R["mods"][m.name] = sha(data)
                if op != "already":
                    out = ex(d, f"{GUARD} {op} {REQ}; echo GUARD_EXIT=$?")
                    R["guard_result"] = next((l for l in out.splitlines() if l.startswith("MLV2_RESULT")), "")
                    R["installed"] = "GUARD_EXIT=0" in out and ("INSTALLED" in out or "UPGRADED" in out)
                else:
                    R["installed"] = True
            ex(d, f"rm -f {GUARD} {REQ}")
        R["hooks_after"] = hooks(d)
        expect = [b"\xdf\xf8\x00\xf0" + struct.pack("<I", e) for e in entries]
        R["hooks_point_to_v2"] = all(bytes.fromhex(R["hooks_after"][k])[:8] == e for k, e in zip(HOOKS, expect))
        R["final"] = ex(d, "cat /proc/uptime; ls -A /tmp_bt /tmp_bt/mlv2 /tmp_bt/mods; tail -5 /tmp_bt/mods/mod_loader.log 2>/dev/null")
        R["ping"] = d.ping()
    out = a.build / ("install-receipt-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
    out.write_text(json.dumps(R, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in R.items() if k != "steps"}, indent=1))
    print("receipt:", out)


if __name__ == "__main__":
    main()
