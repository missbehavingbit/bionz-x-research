#!/usr/bin/env python3
"""Reference client for the WiFiSync camera-side HTTP server (TCP :8765).

It is the PC-side counterpart of the camera mod: it discovers unacknowledged
photos, resumes interrupted transfers with `Range`, verifies size and CRC-32,
renames `.part` files atomically, POSTs the acknowledgement only after the
bytes are on disk, and records a local dedup database.

The same code backs the offline host-server test matrix
(`mods/wifisync/test_wifisync_host.py`) and a real camera session.

Examples:
    python -m tools.research.wifisync_client --host 192.168.122.1 --out .\\photos --once
    python -m tools.research.wifisync_client --host 192.168.122.1 --watch
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import sys
import time
import zlib
from pathlib import Path

DEFAULT_PORT = 8765
DEFAULT_OUT = Path("A6400Sync")
DEFAULT_STATE = Path(".wifisync-state.json")
CHUNK = 256 * 1024


class WifiSyncError(RuntimeError):
    pass


class WifiSyncClient:
    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        out_dir: Path = DEFAULT_OUT,
        state_path: Path = DEFAULT_STATE,
        timeout: float = 30.0,
    ) -> None:
        self.host = host
        self.port = port
        self.out_dir = Path(out_dir)
        self.state_path = Path(state_path)
        self.timeout = timeout
        self.state = self._load_state()

    # ---------------------------------------------------------------- state
    def _load_state(self) -> dict:
        try:
            if self.state_path.is_file():
                return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        return {"cameras": {}}

    def _save_state(self) -> None:
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _camera_state(self, camera_id: str) -> dict:
        cams = self.state.setdefault("cameras", {})
        return cams.setdefault(camera_id, {"ack": 0, "files": {}})
    
    # ----------------------------------------------------------------- http
    def _conn(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)

    def status(self) -> dict:
        c = self._conn()
        try:
            c.request("GET", "/status")
            r = c.getresponse()
            body = r.read()
            if r.status != 200:
                raise WifiSyncError(f"status HTTP {r.status}")
            return json.loads(body)
        finally:
            c.close()

    def manifest(self, since: int = 0) -> dict:
        c = self._conn()
        try:
            c.request("GET", f"/manifest?since={since}")
            r = c.getresponse()
            body = r.read()
            if r.status != 200:
                raise WifiSyncError(f"manifest HTTP {r.status}")
            return json.loads(body)
        finally:
            c.close()

    def manifest_all(self, since: int = 0) -> dict:
        """Follow the server's bounded pages until the manifest is exhausted."""
        files: list[dict] = []
        cur = int(since)
        head = 0
        for _ in range(64):
            page = self.manifest(cur)
            head = int(page.get("head", head))
            chunk = page.get("files", [])
            if not chunk:
                break
            files.extend(chunk)
            nxt = int(chunk[-1]["seq_id"])
            if nxt <= cur:
                break
            cur = nxt
        return {"since": int(since), "head": head, "files": files}

    def wait_event(self, since: int, timeout: float = 25.0) -> dict:
        old = self.timeout
        # The server holds the request for up to 20 s; give the socket more.
        self.timeout = timeout + 5.0
        try:
            c = self._conn()
            try:
                c.request("GET", f"/events?since={since}")
                r = c.getresponse()
                body = r.read()
                if r.status != 200:
                    raise WifiSyncError(f"events HTTP {r.status}")
                return json.loads(body)
            finally:
                c.close()
        finally:
            self.timeout = old

    def ack(self, seq: int, size: int, crc32: str | None) -> dict:
        payload = {"size": size}
        if crc32:
            payload["crc32"] = crc32
        raw = json.dumps(payload).encode()
        c = self._conn()
        try:
            c.request("POST", f"/ack/{seq}", body=raw,
                      headers={"Content-Type": "application/json",
                               "Content-Length": str(len(raw))})
            r = c.getresponse()
            body = r.read()
            if r.status != 200:
                raise WifiSyncError(f"ack HTTP {r.status}: {body[:120]!r}")
            return json.loads(body)
        finally:
            c.close()

    # --------------------------------------------------------------- files
    def download(self, seq: int, dest: Path, resume: bool = True) -> dict:
        """Download one file to `dest` with Range resume, CRC-32 and size checks.

        Returns {"path", "size", "crc32", "sha256", "resumed_from", "server_crc32"}.
        The `.part` file is kept on failure so the next call resumes.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        offset = 0
        if resume and part.is_file():
            offset = part.stat().st_size

        c = self._conn()
        try:
            headers = {}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            c.request("GET", f"/file/{seq}", headers=headers)
            r = c.getresponse()
            if r.status == 416:
                part.unlink(missing_ok=True)
                raise WifiSyncError("range not satisfiable; restarting")
            if r.status not in (200, 206):
                raise WifiSyncError(f"file HTTP {r.status}")
            server_size = int(r.getheader("X-File-Size") or "0")
            server_crc = (r.getheader("X-CRC32") or "").lower()
            name = r.getheader("X-File-Name") or ""
            content_length = int(r.getheader("Content-Length") or "0")
            if r.status == 200 and offset:
                offset = 0          # server ignored Range: start over
            mode = "ab" if offset else "wb"
            written = 0
            with open(part, mode) as fh:
                while True:
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    written += len(chunk)
                fh.flush()
                os.fsync(fh.fileno())
            if written + offset != content_length + offset:
                raise WifiSyncError("short body")
            total = part.stat().st_size
            if server_size and total != server_size:
                raise WifiSyncError(f"size mismatch: local {total} server {server_size}")
            local_crc = _crc32_file(part)
            if server_crc and local_crc != server_crc:
                part.unlink(missing_ok=True)
                raise WifiSyncError(f"crc mismatch: local {local_crc} server {server_crc}")
            os.replace(part, dest)
            return {
                "path": str(dest),
                "name": name,
                "size": total,
                "crc32": local_crc,
                "sha256": _sha256_file(dest) if _ENABLE_SHA else None,
                "resumed_from": offset,
                "server_crc32": server_crc,
            }
        finally:
            c.close()

    # ---------------------------------------------------------------- sync
    def sync_once(self, ack: bool = True, verbose: bool = True) -> list[dict]:
        st = self.status()
        camera_id = st.get("camera_id", "unknown")
        cam = self._camera_state(camera_id)
        since = int(cam.get("ack", 0))
        manifest = self.manifest_all(since)
        done = []
        for item in manifest.get("files", []):
            seq = int(item["seq_id"])
            name = item["name"]
            if name in cam["files"]:
                continue
            dest = self.out_dir / name
            if verbose:
                print(f"[wifisync] {seq}: {name} ({item['size']} bytes)")
            info = self.download(seq, dest)
            cam["files"][name] = {
                "size": info["size"],
                "crc32": info["crc32"],
                "sha256": info["sha256"],
                "timestamp": time.time(),
            }
            if ack:
                res = self.ack(seq, info["size"], info["crc32"])
                cam["ack"] = max(int(cam.get("ack", 0)), int(res.get("ack", 0)))
            self._save_state()
            done.append(info)
        return done


_ENABLE_SHA = True


def _crc32_file(path: Path) -> str:
    crc = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
    return format(crc & 0xFFFFFFFF, "08x")


def _sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha256_file(path: Path) -> str:
    return _sha256_file(Path(path))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--once", action="store_true", help="one manifest pass, then exit")
    ap.add_argument("--watch", action="store_true", help="long-poll /events and sync")
    ap.add_argument("--no-ack", action="store_true")
    ap.add_argument("--timeout", type=float, default=30.0)
    a = ap.parse_args(argv)
    if not a.once and not a.watch:
        a.once = True

    client = WifiSyncClient(a.host, a.port, a.out, a.state, a.timeout)
    try:
        if a.once:
            done = client.sync_once(ack=not a.no_ack)
            print(json.dumps({"synced": len(done),
                              "files": [d["name"] for d in done]}, indent=2))
            return 0
        while True:
            st = client.status()
            client.sync_once(ack=not a.no_ack)
            try:
                ev = client.wait_event(int(st.get("queue_head", 0)))
                if not ev.get("woke"):
                    time.sleep(0.2)
            except (OSError, socket.timeout, WifiSyncError):
                time.sleep(1.0)
    except KeyboardInterrupt:
        return 0
    except (OSError, socket.timeout, WifiSyncError) as exc:
        print(f"wifisync: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
