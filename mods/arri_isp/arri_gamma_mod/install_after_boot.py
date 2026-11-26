"""Host tool: after a fresh boot (menu not yet opened), swap in the lifecycle Mod Loader and
stage Arrivederci.mod. RAM-only; a restart returns to the SD package loader.

Run from the repository root:  python mods/arri_isp/arri_gamma_mod/install_after_boot.py
Steps: wait for the SD boot install receipt; require the loader anchor to be zero; remove the
sd-standalone-v1 loader; check + install the lifecycle loader; on failure reinstall the
original; stage the mod; remove the guard and request. Writes a JSON receipt.
"""
import hashlib, json, sys, time
from pathlib import Path
import comtypes.client
comtypes.client.gen_dir = None
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.camera_debugger import connect, OP_EXEC

root = Path(__file__).resolve().parents[3]
SD = root / "artifacts/publish/mod-loader-sd/artifacts/hardware/sd-standalone-v1"
LC = root / "artifacts/tests/arri-mod-loader-size-2026-11-16/lifecycle-reqs"
# Default payload: the combined Arrivederci mod (gamma + color). The two separate test mods
# share code caves with it, so they are no longer staged together with it.
MOD = root / "mods/arri_isp/arrivederci/Arrivederci.mod"
OUT = root / "artifacts/tests/arrivederci-2026-11-16" / ("install-after-boot-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
EXPECT = {"guard": "e72040bc", "mod": "6b3d383a", "lc_check": "bd5d6b98", "lc_install": "9544f214"}

def h(b): return hashlib.sha256(b).hexdigest()
guard = (SD / "native_mod_loader_guard_arm").read_bytes()
old = {k: (SD / f"mod-loader-{k}.req").read_bytes() for k in ("check", "install", "remove")}
new = {k: (LC / f"mod-loader-{k}.req").read_bytes() for k in ("check", "install")}
mod = MOD.read_bytes()
assert h(guard).startswith(EXPECT["guard"]) and h(mod).startswith(EXPECT["mod"])
assert h(new["check"]).startswith(EXPECT["lc_check"]) and h(new["install"]).startswith(EXPECT["lc_install"])
R = {"steps": []}

def ex(d, c):
    r = d.request(OP_EXEC, c.encode()); out = r["payload"].decode(errors="replace")
    R["steps"].append({"exec": c, "status": r["status"], "flags": r["flags"], "out": out}); return out

def run_guard(d, req, label):
    R["steps"].append({"push": label, **d.push("/tmp_bt/mod_loader.req", req)})
    return ex(d, "/tmp_bt/native_mod_loader_guard; echo GUARD_EXIT=$?")

with connect("extcmd") as d:
    for _ in range(60):
        rc = ex(d, "cat /tmp_bt/mod_loader_card_boot.once/receipt 2>/dev/null")
        if "SD_BOOT_INSTALLED" in rc or "SD_BOOT_SKIPPED" in rc: break
        time.sleep(2)
    R["sd_receipt_done"] = "SD_BOOT_INSTALLED" in rc
    R["anchor_zero"] = not any(d.memory(149, 0x1615ec40, 8))
    if not (R["sd_receipt_done"] and R["anchor_zero"]):
        R["stopped"] = "SD loader not installed yet or menu already used (anchor nonzero); nothing changed"
    else:
        R["steps"].append({"push": "guard", **d.push("/tmp_bt/native_mod_loader_guard", guard)})
        ex(d, "chmod 700 /tmp_bt/native_mod_loader_guard")
        out = run_guard(d, old["remove"], "sd-standalone remove")
        R["removed"] = "REMOVED" in out and "GUARD_EXIT=0" in out
        if R["removed"]:
            out = run_guard(d, new["check"], "lifecycle check")
            R["checked"] = "CHECK_OK" in out and "THEME_OK" in out and "GUARD_EXIT=0" in out
            if R["checked"]:
                out = run_guard(d, new["install"], "lifecycle install")
                R["installed"] = "INSTALLED" in out and "GUARD_EXIT=0" in out
            if not R.get("installed"):
                run_guard(d, old["check"], "rollback check")
                out = run_guard(d, old["install"], "rollback install")
                R["rollback_installed"] = "INSTALLED" in out
        if R.get("installed"):
            R["steps"].append({"push": "Arrivederci.mod", **d.push("/tmp_bt/mods/Arrivederci.mod", mod)})
        ex(d, "rm -f /tmp_bt/mod_loader.req /tmp_bt/native_mod_loader_guard")
    R["open_item_hook"] = d.memory(149, 0x15b92044, 12).hex()
    # Post-boot check: matrix table entry B8 and selector index (refill-at-boot evidence)
    import struct as _s
    R["b8_after_boot"] = list(_s.unpack("<9h", d.read("/dev/mem", 0x0444b1d4, 18)))
    R["matrix_index_after_boot"] = d.read("/dev/mem", 0x643cc460 - 0x60000000, 1)[0]
    R["record31_after_boot_word0"] = hex(_s.unpack("<I", d.read("/dev/mem", 0x03e5363c, 4))[0])
    R["final"] = ex(d, "cat /proc/uptime; ls -A /tmp_bt /tmp_bt/mods")
    R["ping"] = d.ping()
OUT.write_text(json.dumps(R, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: v for k, v in R.items() if k != "steps"}, indent=1))
print("receipt:", OUT)
