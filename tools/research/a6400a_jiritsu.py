"""ILCE-6400A Jiritsu helpers that do not open a camera by themselves.

The local nflasha5 warm-boot image proves the service names and temporary
paths. It does not prove the numeric adjust-command IDs used by another body.
Packet builders therefore refuse an unverified command map before any bytes
that would be sent to a camera are returned.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import struct
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Protocol

from tools.research.wbi_memory import decode_section, parse_sections

WBI_NEEDLES = (
    b"ILCE-6400A",
    b"Linux version 3.0.27_nl-rt106+",
    b"libJiritsu.so",
    b"libsencore.so",
    b"libusbcmd.so",
    b"usb_extcmd",
    b"/tmp/jiritsu/libJit.so",
    b"/tmp/jiritsu/ver.txt",
    b"JIRITSU_GET_FILE",
    b"JIRITSU_SET_FILE",
    b"JIRITSU_DEBUG",
    b"JIRITSU_START",
)
RAM_PREFIXES = ("/tmp/", "/tmp_bt/", "/tmp_network/")
EXCLUDED_STORAGE = {"/dev/nflasha23"}
STORAGE_PATH = re.compile(r"/dev/nflasha(?:B[01]|[0-9]+)")
CHUNK_BYTES = 512 * 1024
A6400A_DUMP_PARTITIONS: dict[str, int] = {
    "/dev/nflashaB0": 4_194_304,
    "/dev/nflashaB1": 4_194_304,
    "/dev/nflasha1": 8_323_072,
    "/dev/nflasha3": 50_331_648,
    "/dev/nflasha4": 12_582_912,
    "/dev/nflasha5": 62_914_560,
    "/dev/nflasha7": 8_388_608,
    "/dev/nflasha15": 314_572_800,
}


class JiritsuUnverified(RuntimeError):
    """Raised when a camera packet needs a command ID this body has not proven."""


@dataclass(frozen=True)
class JiritsuCommandMap:
    """Numeric adjust commands. ``verified`` is reserved for an A6400A proof."""

    status: str
    block: int
    get_file: int
    set_path: int
    set_bytes: int
    source: str

    def require_verified(self) -> None:
        if self.status != "verified":
            raise JiritsuUnverified(
                "This command map cannot be sent to a camera "
                f"(status {self.status}: {self.source})."
            )


# Public ZV-E10 layout. Kept for a future diff against A6400A libJiritsu.so.
# status is deliberately not "verified".
ZV_E10_PUBLIC_COMMANDS = JiritsuCommandMap(
    status="unverified",
    block=0x1001,
    get_file=0x4008,
    set_path=15,
    set_bytes=16,
    source="ZV-E10 public notes, not an ILCE-6400A disassembly",
)

# Halfwords selected by the ILCE-6400A 1.01 dispatcher. status stays short of
# "verified" because no Senser packet was built or sent from this map.
A6400A_V101_LIBRARY_COMMANDS = JiritsuCommandMap(
    status="library",
    block=0x1001,
    get_file=8,
    set_path=15,
    set_bytes=16,
    source=(
        "ILCE-6400A 1.01 nflasha15 libJiritsu.so SHA-256 c3621c7ec3077160"
        "4b1aaae25e51699340333486e1aa281a1cc5f4a543a99dff, dispatcher 0x7015"
    ),
)


def jiritsu_key() -> bytes:
    """AES-128 key named by the public Jiritsu file reader."""
    material = b"jiritsu team"
    return hashlib.md5(material + b"\0" * (16 - len(material))).digest()


def jiritsu_crypt(data: bytes) -> bytes:
    """AES-128-CTR with the documented 00..0f counter. Encrypt and decrypt match."""
    from Cryptodome.Cipher import AES

    return AES.new(jiritsu_key(), AES.MODE_CTR, nonce=b"", initial_value=bytes(range(16))).decrypt(data)


def get_file_payload(path: str, commands: JiritsuCommandMap) -> bytes:
    """Build a GET_FILE body. Refuses an unverified command map first."""
    commands.require_verified()
    if not path.startswith("/") or path.startswith("/..") or "\0" in path:
        raise ValueError("Jiritsu read path must be absolute")
    transport = ("/.." + path).encode("ascii")
    return bytes((1, 0, 1)) + transport


def ephemeral_runner(daemon_path: str) -> bytes:
    """Shell text that starts the debugger from RAM and does not touch flash."""
    if not daemon_path.startswith(RAM_PREFIXES) or ".." in daemon_path or "\n" in daemon_path:
        raise ValueError("Daemon path must stay on a RAM prefix")
    script = (
        "#!/bin/sh\n"
        "export A6400_EPHEMERAL_RAM=1\n"
        f"exec {daemon_path}\n"
    )
    if b"/setting" in script.encode() or b"ld.so.preload" in script.encode():
        raise ValueError("Ephemeral runner must not name internal-flash paths")
    return script.encode()


def _validated_ram_dir(ram_dir: str) -> str:
    """Validate a RAM directory path without trailing slash, traversal, or newlines."""
    if (
        not ram_dir.startswith(RAM_PREFIXES)
        or re.fullmatch(r"/[A-Za-z0-9_./-]+", ram_dir) is None
        or any(part in ("", ".", "..") for part in ram_dir.split("/")[1:])
    ):
        raise ValueError("Chunk directory must be a RAM path without a trailing slash")
    return ram_dir


def readable_storage(path: str) -> str:
    """Allow a raw nflasha partition read and reject the excluded region."""
    if path in EXCLUDED_STORAGE or STORAGE_PATH.fullmatch(path) is None:
        raise ValueError(f"Storage path is not allowed: {path}")
    return path


def chunk_cleanup_command(ram_dir: str) -> str:
    """Remove any staged chunk file in RAM so tmpfs holds at most one chunk."""
    _validated_ram_dir(ram_dir)
    return f"busybox rm -f {ram_dir}/chunk"


def chunk_read_command(path: str, ram_dir: str, offset: int, count: int) -> str:
    """One read-only dd plus sha256sum into a single reused RAM chunk file."""
    readable_storage(path)
    _validated_ram_dir(ram_dir)
    if offset < 0 or count <= 0 or offset % 4096 or count % 4096 or count > CHUNK_BYTES:
        raise ValueError("Chunk range must be a positive 4096-byte multiple up to 512 KiB")
    destination = ram_dir + "/chunk"
    skip = offset // 4096
    blocks = count // 4096
    return (
        f"busybox rm -f {destination} "
        f"&& if dd_status=$(busybox dd if={path} of={destination} "
        f"bs=4096 skip={skip} count={blocks} 2>&1); "
        f"then busybox sha256sum {destination}; "
        'else printf "%s\\n" "$dd_status"; false; fi'
    )


def plan_partition_chunk_dump(
    path: str,
    total_bytes: int | None = None,
    ram_dir: str = "/tmp_network/a6400a-dump",
    chunk_bytes: int = CHUNK_BYTES,
) -> dict:
    """Plan a bounded single-slot RAM-chunk read sequence for an allowlisted partition."""
    readable_storage(path)
    cleanup_cmd = chunk_cleanup_command(ram_dir)
    if total_bytes is None:
        if path not in A6400A_DUMP_PARTITIONS:
            raise ValueError(f"Partition capacity is not configured for {path}")
        total_bytes = A6400A_DUMP_PARTITIONS[path]
    if total_bytes <= 0 or total_bytes % 4096:
        raise ValueError("Partition total_bytes must be a positive 4096-byte multiple")
    if chunk_bytes <= 0 or chunk_bytes > CHUNK_BYTES or chunk_bytes % 4096:
        raise ValueError("chunk_bytes must be a positive 4096-byte multiple up to 512 KiB")

    ram_chunk_path = ram_dir + "/chunk"
    chunks = []
    for index, offset in enumerate(range(0, total_bytes, chunk_bytes)):
        count = min(chunk_bytes, total_bytes - offset)
        chunks.append(
            {
                "index": index,
                "offset": offset,
                "count": count,
                "ram_chunk_path": ram_chunk_path,
                "camera_command": chunk_read_command(path, ram_dir, offset, count),
                "cleanup_command": cleanup_cmd,
            }
        )

    return {
        "partition": path,
        "total_bytes": total_bytes,
        "chunk_bytes": chunk_bytes,
        "chunk_count": len(chunks),
        "max_ram_bytes": min(total_bytes, chunk_bytes),
        "ram_dir": ram_dir,
        "ram_chunk_path": ram_chunk_path,
        "dlopen_constraint": (
            "adj_cmd_set_file2 at 0x5d08 dlopens /tmp/jiritsu/libJit.so synchronously "
            "on im.elf; never stage full partitions in tmpfs or re-upload libJit.so "
            "in the same power cycle"
        ),
        "chunks": chunks,
    }


def ephemeral_chunk_worker_script(
    ram_dir: str = "/tmp_network/a6400a-dump", *, nonce: str | None = None,
) -> bytes:
    """Keep control/chunk files in RAM and identify the owned startup log."""
    _validated_ram_dir(ram_dir)
    nonce = uuid.uuid4().hex if nonce is None else nonce
    if re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise ValueError("Worker nonce must be 32 lowercase hexadecimal characters")
    parent = ram_dir.rsplit("/", 1)[0]
    script = (
        "#!/bin/sh\n"
        "exec >'/tmp/jiritsu/out.txt' 2>&1\n"
        f"printf '__A6400A_WORKER_LOG__:{nonce}\\n' || exit 30\n"
        "init_failure() {\n"
        ' printf "__A6400A_INIT_ERROR__:%s\\n" "$1"\n'
        " while :; do busybox sleep 1 || exit 41; done\n"
        "}\n"
        f"ram_parent='{parent}'\n"
        'physical_parent=$(CDPATH= cd -P "$ram_parent" && pwd -P) || { printf "Cannot resolve RAM parent\\n"; init_failure 30; }\n'
        '[ "$physical_parent" = "$ram_parent" ] || { printf "RAM parent resolves elsewhere: %s\\n" "$physical_parent"; init_failure 30; }\n'
        "mount_length=0\n"
        "ram_fs=\n"
        "mount_conflict=0\n"
        "while read -r mount_source mount_path mount_fs mount_rest; do\n"
        ' case "$mount_path" in /) matched=1;; *)\n'
        '  case "$ram_parent" in "$mount_path"|"$mount_path"/*) matched=1;; *) matched=0;; esac;; esac\n'
        ' if [ "$matched" -eq 1 ]; then\n'
        '  printf "MountCandidate: %s %s\\n" "$mount_path" "$mount_fs"\n'
        '  if [ "${#mount_path}" -gt "$mount_length" ]; then\n'
        '   mount_length=${#mount_path}; ram_fs=$mount_fs; mount_conflict=0\n'
        '  elif [ "${#mount_path}" -eq "$mount_length" ] && [ "$ram_fs" != "$mount_fs" ]; then\n'
        "   mount_conflict=1\n"
        "  fi\n"
        " fi\n"
        "done </proc/mounts\n"
        '[ "$mount_conflict" -eq 0 ] || { printf "Ambiguous parent mounts refused\\n"; init_failure 31; }\n'
        'case "$ram_fs" in tmpfs|ramfs|rootfs) ;; *) printf "RAM filesystem refused: %s\\n" "$ram_fs"; init_failure 31;; esac\n'
        f"busybox mkdir '{ram_dir}' || init_failure 32\n"
        "poll_delay=1\n"
        'if busybox sleep 0.1; then poll_delay=0.1; else printf "Fractional sleep unavailable; using one second\\n"; fi\n'
        f"printf '__A6400A_WORKER__:{nonce}\\n' >'{ram_dir}/ready.tmp' || init_failure 33\n"
        f'printf "RamFilesystem: %s\\n" "$ram_fs" >>\'{ram_dir}/ready.tmp\' || init_failure 34\n'
        f'printf "PollDelay: %s\\n" "$poll_delay" >>\'{ram_dir}/ready.tmp\' || init_failure 34\n'
        f"busybox cat /proc/$$/status >>'{ram_dir}/ready.tmp' || init_failure 34\n"
        f"busybox mv '{ram_dir}/ready.tmp' '{ram_dir}/ready' || init_failure 35\n"
        "while :; do\n"
        f" if [ -f '{ram_dir}/input' ] && [ -f '{ram_dir}/go' ]; then\n"
        f"  busybox mv '{ram_dir}/input' '{ram_dir}/current' || init_failure 36\n"
        f"  busybox rm '{ram_dir}/go' || init_failure 37\n"
        f"  . '{ram_dir}/current' >'{ram_dir}/output.tmp' 2>&1\n"
        "  rc=$?\n"
        f"  printf '\\n__A6400A_RC__:%s\\n' \"$rc\" >>'{ram_dir}/output.tmp'\n"
        f"  busybox mv '{ram_dir}/output.tmp' '{ram_dir}/output' || init_failure 38\n"
        f"  busybox rm '{ram_dir}/current' || init_failure 39\n"
        " else\n"
        '  busybox sleep "$poll_delay" || init_failure 40\n'
        " fi\n"
        "done\n"
    )
    encoded = script.encode("ascii")
    if b"/setting" in encoded or b"ld.so.preload" in encoded or b"/dev/nflasha23" in encoded:
        raise ValueError("Ephemeral worker must not reference flash or excluded storage")
    return encoded


class JiritsuStatusError(RuntimeError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Jiritsu returned status {status:#010x}")


class ChunkFiles(Protocol):
    def read(self, path: str, limit: int) -> bytes: ...

    def write(self, path: str, data: bytes, *, verify: bool = True) -> None: ...


class JiritsuWorkerSession:
    """Bound polling of an already-loaded, nonce-identified file worker."""

    def __init__(
        self, files: ChunkFiles, ram_dir: str, nonce: str, *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.files = files
        self.ram_dir = _validated_ram_dir(ram_dir)
        if re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
            raise ValueError("Worker nonce must be 32 lowercase hexadecimal characters")
        self.nonce = nonce
        self.clock, self.sleep = clock, sleep
        self.ready = False
        self.failed = False
        self.initialization_failed = False
        self.number = 0
        self.capture_receipt: dict = {}

    def _poll(self, path: str, accept: Callable[[bytes], bool], seconds: float, *,
              startup_error: bool = False, interval: float = 0.05) -> bytes:
        if (not math.isfinite(seconds) or not 0 < seconds <= 30
                or not math.isfinite(interval) or not 0 < interval <= 2.0):
            raise ValueError("Worker deadline and poll interval must stay within bounded positive ranges")
        deadline = self.clock() + seconds
        for attempt in range(math.ceil(seconds / interval) + 1):
            if self.clock() >= deadline:
                break
            read_data: bytes | None = None
            try:
                read_data = self.files.read(path, 65536)
            except JiritsuStatusError as error:
                if error.status not in (0x10000006, 0x10000008, 0x1000000A):
                    self.failed = True
                    raise
            else:
                if self.clock() >= deadline:
                    break
                if accept(read_data):
                    return read_data
            if startup_error and (path == "/tmp/jiritsu/out.txt" or attempt % 4 == 3):
                log: bytes | None = read_data if path == "/tmp/jiritsu/out.txt" else None
                if log is None and path != "/tmp/jiritsu/out.txt":
                    try:
                        log = self.files.read("/tmp/jiritsu/out.txt", 65536)
                    except JiritsuStatusError as error:
                        if error.status not in (0x10000006, 0x10000008, 0x1000000A):
                            self.failed = True
                            raise
                if log is not None:
                    failure = re.search(rb"^__A6400A_INIT_ERROR__:(\d+)$", log, re.M)
                    if log.startswith(f"__A6400A_WORKER_LOG__:{self.nonce}\n".encode("ascii")) and failure:
                        self.initialization_failed = True
                        raise RuntimeError(
                            f"Worker initialization refused ({int(failure[1])}): "
                            + log.decode("utf-8", errors="backslashreplace")
                        )
            self.sleep(interval)
        self.failed = True
        raise TimeoutError("Worker response deadline reached; no command or loader replay")

class RamChunkWorker(JiritsuWorkerSession):
    """Control a RAM chunk worker through ordinary USB file transfers."""

    def wait_ready(self) -> bytes:
        prefix = f"__A6400A_WORKER__:{self.nonce}\n".encode("ascii")
        data = self._poll(self.ram_dir + "/ready", lambda raw: raw.startswith(prefix), 15, startup_error=True)
        if re.search(rb"^Uid:\s+0\s+0\s+0\s+0\s*$", data, re.M) is None:
            self.failed = True
            raise ValueError("Worker did not report all four root UIDs")
        self.ready = True
        return data

    def command(self, command: str) -> bytes:
        if not self.ready or self.failed:
            raise RuntimeError("Worker is not ready or a previous operation is unresolved")
        if (not command.isascii() or not command or len(command) > 8192
                or re.search(r"\b(?:exit|exec|kill|trap|return)\b", command)):
            raise ValueError("Command cannot replace or terminate the persistent worker")
        self.number += 1
        marker = f"__A6400A_CMD_{self.nonce}_{self.number}__\n".encode("ascii")
        request = b"printf '%s\\n' '" + marker.rstrip(b"\n") + b"'\n" + command.encode("ascii") + b"\n"
        self.files.write(self.ram_dir + "/input", request)
        # The worker consumes this commit file, so a readback would race it.
        self.files.write(self.ram_dir + "/go", b"go\n", verify=False)
        data = self._poll(
            self.ram_dir + "/output",
            lambda raw: raw.startswith(marker) and re.search(rb"\n__A6400A_RC__:\d+\n$", raw) is not None,
            30,
        )
        end = re.search(rb"\n__A6400A_RC__:(\d+)\n$", data)
        if end is None:
            raise ValueError("Worker completion has no exit status")
        output = data[len(marker):end.start()]
        if int(end[1]) != 0:
            raise RuntimeError(
                f"Worker command failed with exit {int(end[1])}: "
                + output.decode("utf-8", errors="backslashreplace")
            )
        return output

    def read_chunk(self, partition: str, total_bytes: int, chunk: dict) -> str:
        return self.command(
            "free_kib=; while read -r key value units; do "
            'if [ "$key" = MemFree: ]; then free_kib=$value; break; fi; '
            'done </proc/meminfo; if [ -n "$free_kib" ] && [ "$free_kib" -ge 8192 ]; then '
            + chunk["camera_command"]
            + '; else printf "Insufficient or unknown MemFree: %s KiB\\n" "$free_kib"; false; fi'
        ).decode("ascii")

    def remove_chunk(self, chunk: dict) -> None:
        self.command(chunk["cleanup_command"])

    def capture(self, partition: str, total_bytes: int, output: Path, *,
                cancel: Event | None = None) -> dict:
        plan = plan_partition_chunk_dump(partition, total_bytes, self.ram_dir)
        if shutil.disk_usage(output).free < total_bytes + CHUNK_BYTES + 1024 * 1024:
            raise OSError("Insufficient host disk space; no chunk requested")
        name = partition.rsplit("/", 1)[1]
        partial, final = output / (name + ".bin.partial"), output / (name + ".bin")
        if final.exists():
            raise FileExistsError("Refusing to overwrite an existing capture")
        receipt = self.capture_receipt = {
            "partition": partition, "expected_bytes": total_bytes, "verified_bytes": 0,
            "partial_file": partial.name, "complete": False, "chunks": [],
        }
        sha = hashlib.sha256()
        with partial.open("xb") as stream:
            try:
                for chunk in plan["chunks"]:
                    if cancel is not None and cancel.is_set():
                        raise InterruptedError("Cancelled between completed RAM chunks")
                    camera_hash = self.read_chunk(partition, total_bytes, chunk)
                    data = self.files.read(plan["ram_chunk_path"], chunk["count"])
                    try:
                        chunk_hash = verify_chunk(data, camera_hash, chunk["count"])
                    except ValueError:
                        failed = output / f"chunk-{chunk['index']}-unverified.bin"
                        with failed.open("xb") as failed_stream:
                            failed_stream.write(data)
                            failed_stream.flush()
                            os.fsync(failed_stream.fileno())
                        receipt["unverified_chunk_file"] = failed.name
                        raise
                    if stream.write(data) != len(data):
                        raise OSError("Short host capture write; partial file retained")
                    stream.flush()
                    os.fsync(stream.fileno())
                    sha.update(data)
                    receipt["verified_bytes"] += len(data)
                    receipt["chunks"].append({
                        "offset": chunk["offset"], "bytes": len(data), "sha256": chunk_hash,
                    })
                    self.remove_chunk(chunk)
            finally:
                stream.flush()
                os.fsync(stream.fileno())
        with partial.open("rb") as stream:
            saved_sha = hashlib.file_digest(stream, "sha256").hexdigest()
        if partial.stat().st_size != total_bytes or saved_sha != sha.hexdigest():
            raise ValueError("Saved capture length/hash differs from the verified chunks")
        os.link(partial, final)
        partial.unlink()
        receipt.update(complete=True, file=final.name, sha256=saved_sha, host_readback_verified=True)
        return receipt


def verify_chunk(data: bytes, camera_line: str, expected_count: int) -> str:
    """Require the camera sha256sum line to match the host bytes."""
    if len(data) != expected_count:
        raise ValueError("Chunk length does not match the requested count")
    parts = camera_line.split()
    if len(parts) < 2 or len(parts[0]) != 64:
        raise ValueError("Camera checksum line is not a sha256sum result")
    digest = hashlib.sha256(data).hexdigest()
    if parts[0] != digest:
        raise ValueError("Camera and host chunk hashes differ")
    return digest


def audit_wbi(image: Path) -> dict:
    """Count required strings in a WBI1 image. Does not write section files."""
    raw = image.read_bytes()
    header, sections = parse_sections(raw)
    counts = {needle.decode("ascii"): 0 for needle in WBI_NEEDLES}
    for section in sections:
        data, _blocks, _used = decode_section(raw, section)
        for needle in WBI_NEEDLES:
            counts[needle.decode("ascii")] += data.count(needle)
    missing = [name for name, count in counts.items() if count <= 0]
    return {
        "path": str(image),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "magic": header["magic"],
        "sections": header["sections"],
        "expanded_size": header["expanded_size"],
        "string_counts": counts,
        "missing": missing,
        "command_ids": "not recovered from this warm-boot image",
        "filesystem": "WBI1 warm-boot memory, not a mountable root filesystem",
    }


def parse_adjust_status(response: bytes) -> tuple[int, bytes]:
    """Split a little-endian status word from an adjust response body."""
    if len(response) < 4:
        raise ValueError("Adjust response is shorter than its status word")
    status = struct.unpack_from("<I", response)[0]
    return status, response[4:]
