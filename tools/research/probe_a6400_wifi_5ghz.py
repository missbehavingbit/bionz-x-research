"""Offline CLM/NVRAM verification and live 5 GHz Wi-Fi probe for ILCE-6400."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
FW_ROOT = ROOT / "firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked"
BCM_BIN = FW_ROOT / "wlan/bcm/bcm4339.bin"
NVRAM_HIGH_DIR = FW_ROOT / "share/network/nvram/high"


def parse_clm_summary(bcm_bytes: bytes) -> dict:
    idx = bcm_bytes.find(b"CLM DATA")
    if idx < 0:
        raise ValueError("CLM DATA header not found in bcm4339.bin")
    clm = bcm_bytes[idx:idx + 0x2000]
    xr_revs = []
    pos = 0
    while True:
        j = clm.find(b"XR", pos)
        if j < 0:
            break
        xr_revs.append(clm[j + 2])
        pos = j + 2
    countries = [cc.decode("ascii") for cc in (b"US", b"EU", b"JP", b"XV") if clm.find(cc) >= 0]
    return {
        "clm_offset": idx,
        "sha256": hashlib.sha256(bcm_bytes).hexdigest(),
        "size": len(bcm_bytes),
        "xr_revisions": xr_revs,
        "countries_present": countries,
    }


def parse_nvram_profile(path: Path) -> dict:
    text = path.read_text(encoding="latin1")
    kv = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        kv[k.strip()] = v.strip()
    return {
        "file": path.name,
        "ccode": kv.get("ccode"),
        "regrev": int(kv["regrev"]) if "regrev" in kv else None,
        "aa2g": kv.get("aa2g"),
        "aa5g": kv.get("aa5g"),
        "maxp5ga0": kv.get("maxp5ga0"),
        "has_vht80": "mcsbw805ghpo" in kv and kv.get("mcsbw805ghpo") != "0x00000000",
    }


def inspect_offline() -> dict:
    clm = parse_clm_summary(BCM_BIN.read_bytes())
    profiles = [parse_nvram_profile(p) for p in sorted(NVRAM_HIGH_DIR.glob("bcm4339_H_*.txt"))]
    return {
        "clm": clm,
        "high_profiles_count": len(profiles),
        "profiles": profiles,
        "recommended_eu_cec": next((p for p in profiles if p["regrev"] == 88), None),
        "recommended_cn": next((p for p in profiles if p["regrev"] == 76), None),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--camera", action="store_true", help="read-only Wi-Fi interface/module check via camera_debugger")
    args = p.parse_args()
    result = {"offline": inspect_offline()}
    if args.camera:
        sys.path.insert(0, str(ROOT))
        from tools.camera_debugger import connect, OP_EXEC
        with connect() as dbg:
            out = dbg.request(
                OP_EXEC,
                b"lsmod | grep -E 'bcmdhd|cfg80211|wlan'; ifconfig -a | grep -A3 -E 'wlan|p2p' || true",
            )
            result["camera_status"] = out["status"]
            result["camera_output"] = out["payload"].decode("latin1", "replace")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
