#!/usr/bin/env python3
"""End-to-end test of the WiFiSync camera-side server against the reference client.

Builds the x86-64 host server from the same `wifisync_core.c` the camera mod
embeds, then runs the real protocol matrix over TCP:

  * discovery + Smart Single-Best (JPG -> JPG, ARW -> ARW, RAW+JPG -> ARW only)
  * full download with size/CRC-32/SHA-256 agreement
  * interrupted transfer resumed with `Range: bytes=<offset>-`
  * POST /ack watermark, duplicate ack, 409 on a wrong size
  * /events long-poll wake on a new photo
  * 15-photo mixed burst, no duplicates, every file bit-identical
  * repeated manifest pass adds nothing

Receipts: artifacts/tests/wifisync-host-matrix/receipt.json (+ server.log).
Run from the repository root:

    wsl.exe --exec bash -lc "cd '/mnt/c/.../a6400 project' && python3 mods/wifisync/test_wifisync_host.py"
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.research.wifisync_client import WifiSyncClient  # noqa: E402

MOD = ROOT / "mods" / "wifisync"
OUT = ROOT / "artifacts" / "tests" / "wifisync-host-matrix"

CHECKS: list[dict] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append({"name": name, "pass": bool(cond), "detail": detail})
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail and not cond else ""))
    if not cond:
        raise AssertionError(name + (" " + detail if detail else ""))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def build_server() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    exe = OUT / "wifisync_host"
    cmd = [
        "gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
        "-I", str(MOD), "-o", str(exe),
        str(MOD / "wifisync_core.c"), str(MOD / "wifisync_host.c"),
    ]
    subprocess.run(cmd, check=True)
    return exe


def crc32_file(path: Path) -> str:
    crc = 0
    with open(path, "rb") as fh:
        while True:
            b = fh.read(256 * 1024)
            if not b:
                break
            crc = zlib.crc32(b, crc)
    return format(crc & 0xFFFFFFFF, "08x")


def make_photo(card: Path, index: int, ext: str, size: int) -> Path:
    path = card / f"DSC0{index:04d}.{ext}"
    data = bytearray(size)
    for i in range(size):
        data[i] = (index * 131 + i * 31 + 7) & 0xFF
    path.write_bytes(data)
    return path


def wait_until(fn, timeout: float = 25.0, interval: float = 0.2):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    return last


def main() -> int:
    server_exe = build_server()
    tmp = Path(tempfile.mkdtemp(prefix="wifisync-host-"))
    card = tmp / "card"
    card.mkdir()
    out = tmp / "out"
    state = tmp / "state.json"
    port = free_port()
    log = open(OUT / "server.log", "w", encoding="utf-8")

    # Initial card: JPG only, ARW only, and RAW+JPG.
    src = {
        1: {"JPG": make_photo(card, 1, "JPG", 6000)},
        2: {"ARW": make_photo(card, 2, "ARW", 150000)},
        3: {"JPG": make_photo(card, 3, "JPG", 5000),
            "ARW": make_photo(card, 3, "ARW", 200000)},
    }

    proc = subprocess.Popen(
        [str(server_exe), "--root", str(card), "--port", str(port), "--duration", "120"],
        stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        ready = wait_until(lambda: _probe(port), timeout=10)
        check("server_ready", bool(ready))

        client = WifiSyncClient("127.0.0.1", port, out, state, timeout=10.0)

        # --- discovery + Smart Single-Best -------------------------------
        def manifest_names():
            m = client.manifest_all(0)
            return [f["name"] for f in m["files"]]

        names = wait_until(lambda: manifest_names() or None, timeout=20) or []
        check("discover_three_stems", sorted(names) ==
              ["DSC00001.JPG", "DSC00002.ARW", "DSC00003.ARW"],
              repr(names))
        check("smart_jpg_only", "DSC00001.JPG" in names)
        check("smart_arw_only", "DSC00002.ARW" in names)
        check("smart_raw_plus_jpg_skips_jpg", "DSC00003.JPG" not in names)

        st = client.status()
        for key in ("camera_id", "fw", "queue_len", "power_state", "ap_chanspec", "uptime_ms"):
            check(f"status_has_{key}", key in st, repr(st))
        check("status_power_active", st["power_state"] == 2)

        # --- full download with independent hash -------------------------
        info = client.download(1, out / "DSC00001.JPG")
        check("download_size", info["size"] == src[1]["JPG"].stat().st_size)
        check("download_crc", info["crc32"] == crc32_file(src[1]["JPG"]))
        check("download_sha256",
              info["sha256"] == hashlib.sha256(src[1]["JPG"].read_bytes()).hexdigest())

        # --- interrupted transfer resumed with Range ---------------------
        seq = next(f["seq_id"] for f in client.manifest_all(0)["files"] if f["name"] == "DSC00003.ARW")
        total = src[3]["ARW"].stat().st_size
        part = out / "DSC00003.ARW.part"
        part.parent.mkdir(parents=True, exist_ok=True)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", f"/file/{seq}")
        r = conn.getresponse()
        first = r.read(64 * 1024)
        conn.close()
        part.write_bytes(first)
        check("resume_partial_written", part.stat().st_size == 64 * 1024)
        info = client.download(seq, out / "DSC00003.ARW")
        check("resume_used_range", info["resumed_from"] == 64 * 1024, str(info["resumed_from"]))
        check("resume_size", info["size"] == total)
        check("resume_sha256",
              info["sha256"] == hashlib.sha256(src[3]["ARW"].read_bytes()).hexdigest())
        check("part_removed", not part.exists())

        # --- ack / duplicate ack / 409 ----------------------------------
        acks = {1: client.ack(1, info["size"] if False else src[1]["JPG"].stat().st_size,
                              crc32_file(src[1]["JPG"]))}
        check("ack_1", acks[1]["acked"] is True)
        dup = client.ack(1, src[1]["JPG"].stat().st_size, crc32_file(src[1]["JPG"]))
        check("ack_idempotent", dup.get("duplicate") is True and dup["ack"] == 1)
        bad = _post_ack(port, seq + 1000, {"size": 1})
        check("ack_unknown_404", bad[0] == 404, str(bad[0]))
        bad = _post_ack(port, seq, {"size": 1})
        check("ack_size_mismatch_409", bad[0] == 409, str(bad[0]))

        # --- /events long-poll wake -------------------------------------
        st = client.status()
        base = int(st["next_index"]) + 1
        woke: dict = {}

        def poll():
            try:
                woke["r"] = client.wait_event(st["queue_head"], timeout=20)
            except Exception as exc:  # noqa: BLE001
                woke["err"] = repr(exc)

        t = threading.Thread(target=poll)
        t.start()
        time.sleep(0.5)
        make_photo(card, base, "ARW", 160000)
        t.join(timeout=25)
        check("events_woke", bool(woke.get("r", {}).get("woke")), repr(woke))

        # --- 15-photo mixed burst ---------------------------------------
        st = client.status()
        base = int(st["next_index"]) + 1
        burst = {}
        for i in range(15):
            idx = base + i
            mode = i % 3
            if mode == 0:
                burst[idx] = {"JPG": make_photo(card, idx, "JPG", 7000 + i)}
            elif mode == 1:
                burst[idx] = {"ARW": make_photo(card, idx, "ARW", 150000 + i * 1000)}
            else:
                burst[idx] = {"JPG": make_photo(card, idx, "JPG", 6000 + i),
                              "ARW": make_photo(card, idx, "ARW", 170000 + i * 1000)}
        expected = {f"DSC0{idx:04d}.{'ARW' if 'ARW' in f else 'JPG'}"
                    for idx, f in burst.items()}

        def burst_ready():
            got = {f["name"] for f in client.manifest_all(0)["files"]}
            return got if expected <= got else None

        got = wait_until(burst_ready, timeout=40) or set()
        if not (expected <= got):
            st2 = client.status()
            m2 = client.manifest_all(0)
            print("DEBUG status", st2)
            print("DEBUG manifest", [f["name"] for f in m2["files"]])
        check("burst_all_discovered", expected <= got, repr(sorted(expected - got)))
        check("burst_no_jpg_duplicates",
              not any(f["name"].endswith(".JPG") for f in client.manifest_all(0)["files"]
                      if f["name"].startswith("DSC0") and int(f["name"][4:8]) in burst and
                      "ARW" in burst[int(f["name"][4:8])]))

        pending_before = [f["name"] for f in client.manifest_all(0)["files"]]
        synced = client.sync_once(ack=True, verbose=False)
        check("burst_sync_count", len(synced) == len(pending_before), str(len(synced)))
        ok_hashes = 0
        for idx, fmts in burst.items():
            ext = "ARW" if "ARW" in fmts else "JPG"
            name = f"DSC0{idx:04d}.{ext}"
            local = out / name
            check(f"burst_present_{name}", local.is_file())
            if local.is_file():
                check(f"burst_sha_{name}",
                      hashlib.sha256(local.read_bytes()).hexdigest() ==
                      hashlib.sha256(fmts[ext].read_bytes()).hexdigest())
                ok_hashes += 1
        check("burst_all_hashes", ok_hashes == len(expected))

        m = client.manifest_all(0)
        check("manifest_empty_after_ack", m["files"] == [], repr(m["files"]))
        st = client.status()
        check("status_queue_drained", st["queue_len"] == 0 and st["ack"] == st["queue_head"])

        # --- repeated scan adds nothing ---------------------------------
        time.sleep(2.0)
        m = client.manifest_all(0)
        check("repeat_scan_no_duplicates", m["files"] == [], repr(m["files"]))

        log.flush()
        _write_receipt(tmp, port, proc, state)
        failures = [c for c in CHECKS if not c["pass"]]
        print(f"checks={len(CHECKS)} failures={len(failures)}")
        return 1 if failures else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def _probe(port: int) -> bool:
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        c.request("GET", "/status")
        r = c.getresponse()
        r.read()
        c.close()
        return r.status == 200
    except OSError:
        return False


def _post_ack(port: int, seq: int, payload: dict) -> tuple[int, bytes]:
    raw = json.dumps(payload).encode()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", f"/ack/{seq}", body=raw,
              headers={"Content-Type": "application/json", "Content-Length": str(len(raw))})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body


def _write_receipt(tmp: Path, port: int, proc: subprocess.Popen, state: Path) -> None:
    receipt = {
        "status": "wifisync_host_matrix",
        "port": port,
        "checks": CHECKS,
        "failures": [c for c in CHECKS if not c["pass"]],
        "state_json": json.loads(state.read_text()) if state.is_file() else None,
        "modcore_sha256": hashlib.sha256((MOD / "wifisync_core.c").read_bytes()).hexdigest(),
        "client_sha256": hashlib.sha256(
            (ROOT / "tools" / "research" / "wifisync_client.py").read_bytes()).hexdigest(),
        "temp_root": str(tmp),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
