"""Read-only filesystem evidence and bounded known-pattern RAM/USB qualification."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
from threading import Event

from project_paths import PROJECT_ROOT
from tools.research.a6400a_jiritsu import CHUNK_BYTES, JiritsuWorkerSession, readable_storage, verify_chunk

SOURCE = PROJECT_ROOT / "tools" / "native_camera" / "ram_workspace_probe.c"
EXECUTABLE = "/tmp/jiritsu/ram_probe.elf"
CONFIG = "/tmp/jiritsu/probe.cfg"
HEADER = struct.Struct("<16sIIIIIii")
RECORD = struct.Struct("<64siiQIIIII")
REPORT_BYTES = HEADER.size + 3 * RECORD.size + 4
PATHS = ("/tmp_network", "/tmp_bt", "/tmp")
RAMFS_MAGIC, TMPFS_MAGIC, EXT2_MAGIC = 0x858458F6, 0x01021994, 0xEF53


@dataclass(frozen=True)
class RamBinding:
    path: str
    device: int
    magic: int
    kind: str
    evidence: str


def decode_native(data: bytes, *, private_path: str | None = None) -> dict:
    if len(data) != REPORT_BYTES or struct.unpack_from("<I", data, len(data) - 4)[0] != 0x52414D21:
        raise ValueError("Native RAM evidence has no exact committed frame")
    tag, pid, uid, euid, count, operation, operation_result, rollback_result = HEADER.unpack_from(data)
    if (tag != b"A6400A_RAM_V2".ljust(16, b"\0") or count != 3 or not pid or uid or euid
            or operation not in (0, 1, 3)):
        raise ValueError("Native RAM evidence identity/privilege mismatch")
    if private_path is not None and re.fullmatch(r"/tmp/jiritsu/a6400a-[0-9a-f]{32}", private_path) is None:
        raise ValueError("Private RAM evidence path is outside its nonce namespace")
    paths = (private_path or PATHS[0], *PATHS[1:])
    records = {}
    for index, expected in enumerate(paths):
        path, stat_rc, fs_rc, device, magic, size, blocks, available, flags = RECORD.unpack_from(
            data, HEADER.size + index * RECORD.size)
        if path.rstrip(b"\0").decode("ascii") != expected:
            raise ValueError("Native RAM evidence contains an undeclared path")
        records[expected] = {
            "stat_result": stat_rc, "statfs_result": fs_rc, "device": device,
            "magic": magic, "block_size": size, "blocks": blocks, "available": available,
            "flags": flags,
        }
    return {"pid": pid, "uid": uid, "euid": euid, "operation": operation,
            "operation_result": operation_result, "rollback_result": rollback_result, "records": records}


def prove_network_ram(native: dict, diagnostic: bytes, *, private_path: str | None = None) -> RamBinding:
    path = private_path or "/tmp_network"
    record = native["records"][path]
    if record["stat_result"] or record["statfs_result"]:
        raise ValueError("Directory stat/statfs failed; RAM backing is unknown")
    if re.search(rb"^Uid:\s+0\s+0\s+0\s+0\s*$", diagnostic, re.M) is None:
        raise ValueError("The diagnostic shell has not proved its root UIDs")
    device, magic = record["device"], record["magic"]
    if not 0 <= device <= 0xFFFFFFFF or not 512 <= record["block_size"] <= 65536:
        raise ValueError("Native device/block-size encoding is outside the ARM32 probe")
    if private_path is not None:
        if (native["operation"] != 1 or native["operation_result"] != 0 or magic != TMPFS_MAGIC
                or record["flags"] & 15 != 14
                or record["blocks"] * record["block_size"] != 2 * 1024 * 1024
                or record["available"] * record["block_size"] < CHUNK_BYTES):
            raise ValueError("Private tmpfs creation, actual type, flags, capacity or free-space proof failed")
        return RamBinding(path, device, magic, "private-tmpfs",
                          "exclusive native mkdir/mount; actual tmpfs magic; 2-MiB capacity; nodev/nosuid/noexec flags")
    if native["operation"] != 0:
        raise ValueError("Existing-directory proof cannot consume a private mount operation")
    if magic in (RAMFS_MAGIC, TMPFS_MAGIC):
        kind = "ramfs" if magic == RAMFS_MAGIC else "tmpfs"
        return RamBinding("/tmp_network", device, magic, kind, "successful native statfs filesystem magic")
    if magic != EXT2_MAGIC or not 256 <= device <= 271:
        raise ValueError("Active filesystem is not a proved memory filesystem or supported RAM disk")
    minor = device - 256
    name = f"ram{minor}"
    text = diagnostic.decode("ascii")
    attributes = {}
    sizes = {}
    partitions = {}
    drivers = []
    mounts = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 4 and fields[:2] == ["U", name]:
            if fields[2] in attributes:
                raise ValueError("Duplicate RAM-device uevent field")
            attributes[fields[2]] = fields[3]
        elif len(fields) == 3 and fields[:2] == ["S", name]:
            if name in sizes or not fields[2].isdigit():
                raise ValueError("Invalid/duplicate RAM-device sector count")
            sizes[name] = int(fields[2]) * 512
        elif len(fields) == 5 and fields[0] == "P" and fields[4] == name:
            if name in partitions or not all(value.isdigit() for value in fields[1:4]):
                raise ValueError("Invalid/duplicate RAM-device partition row")
            partitions[name] = tuple(map(int, fields[1:4]))
        elif len(fields) == 7 and fields[0] == "MI":
            _, mount_id, parent_id, dev, point, fs, source = fields
            if dev == f"1:{minor}" and (point == "/" or "/tmp_network" == point
                                       or "/tmp_network".startswith(point + "/")):
                mounts.append((point, fs, source))
        elif len(fields) == 3 and fields[:2] == ["B", "1"]:
            drivers.append(fields[2])
    if drivers != ["ramdisk"]:
        raise ValueError("Block major 1 is not independently registered as the RAM-disk driver")
    if attributes and attributes != {"MAJOR": "1", "MINOR": str(minor), "DEVNAME": name}:
        raise ValueError("The active ext2 device conflicts with its RAM-disk sysfs identity")
    if name not in partitions:
        raise ValueError("RAM-disk geometry was not observed in /proc/partitions")
    major, observed_minor, kib = partitions[name]
    if ((major, observed_minor) != (1, minor) or kib <= 0
            or (name in sizes and kib * 1024 != sizes[name])):
        raise ValueError("RAM-disk /proc and sysfs geometry disagree")
    if not mounts:
        raise ValueError("No matching active-device mountinfo evidence")
    selected = max(mounts, key=lambda mount: len(mount[0]))
    if selected[1] != "ext2" or any(
            len(point) == len(selected[0]) and fs != "ext2" for point, fs, _ in mounts):
        raise ValueError("Native ext2 device and mountinfo filesystem disagree")
    return RamBinding("/tmp_network", device, magic, "ext2-on-ramdisk",
                      f"native st_dev=1:{minor}; registered ramdisk driver; /proc {name} and mountinfo; "
                      + ("sysfs corroborated" if attributes and name in sizes else "sysfs identity/capacity not fully observed"))


def prove_swap_inactive(diagnostic: bytes) -> dict[str, int]:
    states = re.findall(rb"^SWAP_STATE Header=([01]) Active=([0-9]+) Valid=([01])$", diagnostic, re.M)
    if not states:
        raise ValueError("RAM-only data staging has no observed swap-state evidence")
    header, active, valid = map(int, states[-1])
    if header != 1 or valid != 1 or active:
        raise ValueError("RAM-only data staging requires a valid table with no active swap")
    return {"header": header, "active": active, "valid": valid}


def probe_config(nonce: str, *, private_tmpfs: bool = False, partition_dump: bool = False) -> bytes:
    if re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise ValueError("Worker nonce must be 32 lowercase hexadecimal characters")
    if partition_dump and not private_tmpfs:
        raise ValueError("Native partition reading requires an explicit private tmpfs")
    return f"{nonce} {int(bool(private_tmpfs))}{' dump' if partition_dump else ''}\n".encode("ascii")


def parse_meminfo_snapshots(diagnostic: bytes) -> dict[str, dict[str, int]]:
    snapshots: dict[str, dict[str, int]] = {}
    for line in diagnostic.splitlines():
        if not line.startswith(b"MEMINFO "):
            continue
        parts = line.decode("ascii", errors="replace").split()
        if len(parts) < 2:
            continue
        values: dict[str, int] = {}
        for item in parts[2:]:
            if "=" in item:
                key, raw_val = item.split("=", 1)
                if raw_val.isdigit():
                    values[key] = int(raw_val)
        if values:
            snapshots[parts[1]] = values
    return snapshots


def probe_script(ram_dir: str, nonce: str, *, private_tmpfs: bool = False) -> bytes:
    parent = "/tmp/jiritsu" if private_tmpfs else "/tmp_network"
    if ram_dir != parent + "/a6400a-" + nonce or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise ValueError("RAM probe needs its declared nonce directory")
    ack = "/tmp/jiritsu/a6400a-" + nonce + ".ack"
    metadata = "/tmp/jiritsu/a6400a-" + nonce + ".meta"
    native_action = f"--mount-private {nonce}" if private_tmpfs else ""
    native_check = f"--check-private {nonce}" if private_tmpfs else "--check-network"
    script = f"""#!/bin/sh
exec >'/tmp/jiritsu/out.txt' 2>&1
printf '__A6400A_WORKER_LOG__:{nonce}\\n'
phase=metadata
owned_ram=0
private_mode={int(private_tmpfs)}
fail() {{ printf '__A6400A_INIT_ERROR__:%s\\n' "$1"; phase=error; }}
check_swap() {{
 swap_header=0; active_swaps=0; swap_valid=1
 if [ -r /proc/swaps ]; then
  while read -r swap_name swap_type swap_size swap_used swap_priority swap_rest; do
   if [ "$swap_header" -eq 0 ]; then
    if [ "$swap_name" = Filename ] && [ "$swap_type" = Type ] && [ "$swap_size" = Size ] && [ "$swap_used" = Used ] && [ "$swap_priority" = Priority ] && [ -z "$swap_rest" ]; then
     swap_header=1
    else swap_valid=0; break; fi
   else
    active_swaps=$((active_swaps+1))
    [ "$active_swaps" -le 16 ] || {{ swap_valid=0; break; }}
   fi
  done </proc/swaps
 else swap_valid=0; fi
 printf 'SWAP_STATE Header=%s Active=%s Valid=%s\\n' "$swap_header" "$active_swaps" "$swap_valid"
 [ "$swap_header" -eq 1 ] && [ "$swap_valid" -eq 1 ] && [ "$active_swaps" -eq 0 ]
}}
while IFS= read -r line; do
 case "$line" in Uid:*|Pid:*|PPid:*|SigIgn:*|SigCgt:*|CapEff:*) printf '%.256s\\n' "$line";; esac
done </proc/$$/status
if read -r up idle </proc/uptime; then printf 'UPTIME START %.32s\\n' "$up"; else fail 41; fi
check_swap || printf 'Swap state is active or unqualified; data staging forbidden\\n'
physical_parent=$(CDPATH= cd -P '{parent}' && pwd -P)
if [ "$physical_parent" != '{parent}' ]; then fail 30; fi
n=0
while read -r id parent device root point rest; do
 case "$point" in /|/tmp|/tmp_bt|/tmp_network|/tmp/jiritsu)
  n=$((n+1)); [ "$n" -le 16 ] || {{ fail 31; break; }}
  tail=${{rest#*" - "}}
  set -f; set -- $tail
  printf 'MI %.12s %.12s %.20s %.64s %.32s %.128s\\n' "$id" "$parent" "$device" "$point" "$1" "$2"
 ;; esac
done </proc/self/mountinfo
if [ "$private_mode" -eq 0 ]; then
while read -r major minor kib name; do
 case "$name" in ram[0-9]*) printf 'P %.12s %.12s %.20s %.16s\\n' "$major" "$minor" "$kib" "$name";; esac
done </proc/partitions
block_section=0
while read -r number name rest; do
 if [ "$number" = Block ] && [ "$name" = devices: ]; then block_section=1
 elif [ "$number" = Character ]; then block_section=0
 elif [ "$block_section" -eq 1 ] && [ "$number" = 1 ]; then printf 'B 1 %.32s\\n' "$name"; fi
done </proc/devices
if IFS= read -r cmdline </proc/cmdline; then printf 'CMD %.2048s\\n' "$cmdline"; fi
for n in 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
 p=/sys/class/block/ram$n
 if [ ! -r "$p/uevent" ]; then p=/sys/block/ram$n; fi
 if [ -r "$p/uevent" ]; then
  while IFS='=' read -r key value; do
   case "$key" in MAJOR|MINOR|DEVNAME) printf 'U ram%s %s %.64s\\n' "$n" "$key" "$value";; esac
  done <"$p/uevent"
 fi
 if [ -r "$p/size" ]; then read -r sectors <"$p/size"; printf 'S ram%s %.20s\\n' "$n" "$sectors"; fi
done
fi
if [ "$phase" != error ]; then
 if busybox chmod 700 '{EXECUTABLE}'; then
  '{EXECUTABLE}' {native_action} >'{metadata}'
  rc=$?
  if [ "$rc" -ne 0 ]; then fail 42
  else
   if [ "$private_mode" -eq 1 ]; then owned_ram=1; fi
   printf '__A6400A_PROBE_META__:{nonce}\\n'
  fi
 else fail 42; fi
fi
while :; do
 if [ -f '{ack}' ] && read -r an op dev magic extra <'{ack}'; then
  if [ "$an" = '{nonce}' ] && [ -z "$extra" ]; then
   case "$op" in
    cleanup)
     if [ "$phase" != clean ]; then
      if [ "$owned_ram" -eq 1 ]; then
       if busybox rm -f '{ram_dir}/chunk'; then
        if [ "$private_mode" -eq 1 ]; then
         if '{EXECUTABLE}' --unmount-private '{nonce}' >'{metadata}'; then owned_ram=0; else fail 49; fi
        elif busybox rmdir '{ram_dir}'; then owned_ram=0; else fail 49; fi
       else fail 49; fi
      fi
      if [ "$owned_ram" -eq 0 ]; then
       phase=clean
       if read -r up idle </proc/uptime; then printf 'UPTIME CLEAN %.32s\\n' "$up"; else fail 41; fi
       printf '__A6400A_PROBE_CLEAN__:{nonce}\\n'
      fi
     fi
    ;;
    4k|512k)
     if {{ [ "$op" = 4k ] && [ "$phase" = metadata ]; }} || {{ [ "$op" = 512k ] && [ "$phase" = 4k ]; }}; then
      phase=working
      memory_checks=0
      while :; do
       free_kib=
       while read -r key value units; do
        if [ "$key" = MemFree: ]; then free_kib=$value; break; fi
       done </proc/meminfo
       memory_checks=$((memory_checks+1))
       if [ -n "$free_kib" ] && [ "$free_kib" -ge 8192 ]; then break; fi
       printf 'MEMORY_WAIT %s Attempt=%s MemFreeKiB=%s\\n' "$op" "$memory_checks" "$free_kib"
       [ "$memory_checks" -lt 5 ] || break
       busybox sleep 1 || {{ fail 50; break; }}
      done
      native_rc=0
      '{EXECUTABLE}' {native_check} "$dev" "$magic" || native_rc=$?
      printf 'GATE %s MemFreeKiB=%s NativeCheckExit=%s Device=%s Magic=%s\\n' "$op" "$free_kib" "$native_rc" "$dev" "$magic"
      if [ -n "$free_kib" ] && [ "$free_kib" -ge 8192 ] && [ "$native_rc" -eq 0 ] && check_swap; then
       if [ "$owned_ram" -eq 0 ]; then
        if busybox mkdir '{ram_dir}'; then owned_ram=1; else fail 43; fi
       fi
       if [ "$phase" = working ]; then
        blocks=1; [ "$op" != 512k ] || blocks=128
        i=0; good=1
        while [ "$i" -lt "$blocks" ]; do
         if ! printf '%4096s' A6400A_RAM; then good=0; break; fi
         i=$((i+1))
        done >'{ram_dir}/chunk'
        if [ "$good" -eq 1 ]; then
         hash=$(busybox sha256sum '{ram_dir}/chunk')
         if [ "$?" -eq 0 ]; then
          phase=$op
          if read -r up idle </proc/uptime; then printf 'UPTIME %s %.32s\\n' "$op" "$up"; else fail 41; fi
          printf '__A6400A_PROBE_DATA__:%s:{nonce}\\n%s\\n' "$op" "$hash"
         else fail 46; fi
        else fail 45; fi
       fi
      else printf 'RAM binding or free-memory recheck failed\\n'; fail 44; fi
     fi
    ;;
   esac
  fi
 fi
 busybox sleep 1 || exit 50
done
"""
    encoded = script.encode("ascii")
    if len(encoded) > 16000 or b"/dev/nflasha" in encoded or b"dd if=" in encoded:
        raise ValueError("Probe script exceeds its metadata/known-pattern-only scope")
    return encoded


class RamWorkspaceProbe(JiritsuWorkerSession):
    def __init__(self, files, ram_dir: str, nonce: str, report: dict, *, private_tmpfs: bool = False, **kwargs):
        super().__init__(files, ram_dir, nonce, **kwargs)
        self.report = report
        self.binding: RamBinding | None = None
        self.last_ack = b""
        self.last_uptime: float | None = None
        self.private_tmpfs = private_tmpfs

    def observe_uptime(self, log: bytes, phase: str) -> None:
        found = re.findall(rb"^UPTIME " + phase.encode("ascii") + rb" ([0-9]+(?:\.[0-9]+)?)$", log, re.M)
        if len(found) != 1:
            raise ValueError("RAM probe lacks one exact uptime observation")
        value = float(found[0])
        if not math.isfinite(value) or value <= 0 or (self.last_uptime is not None and value < self.last_uptime):
            raise ValueError("Camera uptime is invalid or regressed during RAM qualification")
        self.last_uptime = value
        self.report.setdefault("ram_probe_uptime", {})[phase] = value

    @property
    def ack_path(self) -> str:
        return "/tmp/jiritsu/a6400a-" + self.nonce + ".ack"

    @property
    def metadata_path(self) -> str:
        return "/tmp/jiritsu/a6400a-" + self.nonce + ".meta"

    def _record(self, marker: bytes, seconds: float = 30, *, startup_error=True, checksum=False) -> bytes:
        def complete(raw: bytes) -> bool:
            if checksum:
                return re.search(re.escape(marker) + rb"[0-9a-f]{64}[ \t]+[^\r\n]+\n", raw) is not None
            return marker in raw
        return self._poll(
            "/tmp/jiritsu/out.txt", complete, seconds, startup_error=startup_error, interval=0.25)

    def _update_meminfo(self, log: bytes) -> None:
        snapshots = parse_meminfo_snapshots(log)
        if snapshots:
            self.report.setdefault("ram_probe_meminfo", {}).update(snapshots)

    def ack(self, phase: str) -> None:
        if phase != "cleanup" and self.binding is None:
            raise ValueError("No data authorization before RAM backing is proved")
        device = self.binding.device if self.binding is not None else 0
        magic = self.binding.magic if self.binding is not None else 0
        self.last_ack = f"{self.nonce} {phase} {device} {magic}\n".encode("ascii")
        self.report["probe_ack_hex"] = self.last_ack.hex()
        self.files.write(self.ack_path, self.last_ack)

    def run(self, output: Path, fixture_dir: Path, *, cancel: Event | None = None) -> dict:
        marker = f"__A6400A_PROBE_META__:{self.nonce}\n".encode("ascii")
        diagnostic = self._record(marker, 15)
        (output / "ram-diagnostics.txt").write_bytes(diagnostic)
        self._update_meminfo(diagnostic)
        self.observe_uptime(diagnostic, "START")
        raw = self.files.read(self.metadata_path, REPORT_BYTES)
        (output / "native-ram-evidence.bin").write_bytes(raw)
        self.report["probe_native_hex"] = raw.hex()
        native = decode_native(raw, private_path=self.ram_dir if self.private_tmpfs else None)
        self.report["native_ram_evidence"] = native
        self.binding = prove_network_ram(native, diagnostic, private_path=self.ram_dir if self.private_tmpfs else None)
        self.report["ram_binding"] = asdict(self.binding)
        self.report["swap_state"] = prove_swap_inactive(diagnostic)
        record = native["records"][self.binding.path]
        if (self.binding.kind == "ext2-on-ramdisk"
                and record["available"] * min(record["block_size"], 512) < 1024 * 1024):
            raise ValueError("RAM backing proved, but conservatively measured free RAM-disk space is below 1 MiB")
        results = []
        fixture_dir.mkdir(parents=True, exist_ok=False)
        for label, size in (("4k", 4096), ("512k", CHUNK_BYTES)):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("RAM probe cancelled between completed transfers")
            self.ack(label)
            marker = f"__A6400A_PROBE_DATA__:{label}:{self.nonce}\n".encode("ascii")
            log = self._record(marker, checksum=True)
            (output / "ram-diagnostics.txt").write_bytes(log)
            self._update_meminfo(log)
            self.observe_uptime(log, label)
            self.report["swap_state"] = prove_swap_inactive(log)
            checksum = log.split(marker, 1)[1].splitlines()[0].decode("ascii")
            pattern = (b" " * (4096 - len(b"A6400A_RAM")) + b"A6400A_RAM") * (size // 4096)
            expected = hashlib.sha256(pattern).hexdigest()
            for attempt in range(3):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("RAM probe cancelled between completed transfers")
                data = self.files.read(self.ram_dir + "/chunk", size)
                try:
                    actual = verify_chunk(data, checksum, size)
                    if data != pattern or actual != expected:
                        raise ValueError("RAM pattern differs from independently constructed host bytes")
                except ValueError:
                    failed = fixture_dir / f"unverified-{label}-read-{attempt + 1}.bin"
                    with failed.open("xb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    self.report["ram_probe_unverified"] = {
                        "file": failed.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                    }
                    raise
                path = fixture_dir / f"{label}-read-{attempt + 1}.bin"
                with path.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                with path.open("rb") as stream:
                    saved = hashlib.file_digest(stream, "sha256").hexdigest()
                if path.stat().st_size != size or saved != expected:
                    raise ValueError("RAM proof saved-file length/hash mismatch")
                results.append({"bytes": size, "sha256": actual, "host_readback_verified": True})
                self.report["ram_roundtrips"] = results
        self.ready = True
        return {"ram_binding": asdict(self.binding), "roundtrips": results}

    def native_command(self, operation: str, *arguments: str | int) -> bytes:
        if not self.ready or self.failed or self.binding is None or self.binding.kind != "private-tmpfs":
            raise ValueError("Native reading requires private RAM and six verified roundtrips")
        if operation in ("inventory", "remove"):
            if arguments:
                raise ValueError("Native metadata/removal operations take no arguments")
        elif operation == "chunk":
            if len(arguments) != 4:
                raise ValueError("Native chunk requires source, offset, count and live capacity")
            path, offset, count, capacity = arguments
            if not isinstance(path, str):
                raise ValueError("Native chunk source must be an allowed storage path")
            readable_storage(path)
            if (any(type(value) is not int for value in (offset, count, capacity))
                    or not isinstance(offset, int) or not isinstance(count, int) or not isinstance(capacity, int)
                    or not 0 < count <= CHUNK_BYTES or not 0 < capacity <= 512 * 1024 * 1024
                    or offset < 0 or offset % 4096 or count % 4096 or capacity % 4096
                    or offset + count > capacity):
                raise ValueError("Native chunk is outside its aligned live-capacity bounds")
        else:
            raise ValueError("Native reader does not accept shell commands")
        if self.number >= 0xFFFE:
            raise ValueError("Native command sequence exhausted; no wrap or replay")
        self.number += 1
        marker = f"__A6400A_CMD_{self.nonce}_{self.number}__\n".encode("ascii")
        fields = (self.nonce, operation, str(self.number), str(self.binding.device),
                  str(self.binding.magic), *(str(value) for value in arguments))
        request = (" ".join(fields) + "\n").encode("ascii")
        event = {"operation": operation, "arguments": list(arguments), "sequence": self.number,
                 "status": "attempted"}
        self.report.setdefault("native_chunk_commands", []).append(event)
        try:
            self.files.write(self.ram_dir + "/input", request)
            self.files.write(self.ram_dir + "/go", b"go\n", verify=False)
            self.sleep(0.095 if operation == "chunk" else 0.015)
            data = self._poll(
                self.ram_dir + "/output",
                lambda raw: raw.startswith(marker) and re.search(rb"\n__A6400A_RC__:\d+\n$", raw) is not None,
                30, startup_error=True, interval=0.04,
            )
        except (OSError, ValueError, RuntimeError):
            if not self.initialization_failed:
                self.failed = True
            event["status"] = "unresolved"
            raise
        end = re.search(rb"\n__A6400A_RC__:(\d+)\n$", data)
        if end is None:
            self.failed = True
            raise ValueError("Native command has no complete exit status")
        body = data[len(marker):end.start()]
        event.update(exit_code=int(end[1]), response_sha256=hashlib.sha256(body).hexdigest(),
                     status="verified" if int(end[1]) == 0 else "refused")
        if int(end[1]):
            event["refusal"] = body.decode("ascii", errors="backslashreplace")
            raise RuntimeError(f"Native {operation} refused ({int(end[1])}): " + event["refusal"])
        return body

    def cleanup(self) -> None:
        self.ack("cleanup")
        marker = f"__A6400A_PROBE_CLEAN__:{self.nonce}\n".encode("ascii")
        log = self._record(marker, 15, startup_error=False)
        self._update_meminfo(log)
        self.observe_uptime(log, "CLEAN")
        if self.private_tmpfs:
            raw = self.files.read(self.metadata_path, REPORT_BYTES)
            native = decode_native(raw, private_path=self.ram_dir)
            self.report["private_tmpfs_cleanup_evidence"] = native
            self.report["probe_native_hex"] = raw.hex()
            if native["operation"] == 1 and native["operation_result"] < 0:
                if native["rollback_result"] not in (-2147483648, 0):
                    raise ValueError("Failed private tmpfs creation left an unremoved mountpoint")
                self.report["private_tmpfs_not_created"] = True
            elif (native["operation"] != 3 or native["operation_result"] != 0
                  or native["rollback_result"] != 0):
                raise ValueError("Private tmpfs unmount and mountpoint removal were not acknowledged")
            else:
                self.report["private_tmpfs_unmounted_and_removed"] = True
        self.report["ram_probe_data_removed"] = True
        self.report["ram_probe_uptime_monotonic"] = len(self.report["ram_probe_uptime"]) >= 2
        self.ready = False


def qualify_executable(path: Path) -> dict:
    from elftools.elf.elffile import ELFFile
    from unicorn import UC_ARCH_ARM, UC_HOOK_INTR, UC_MODE_ARM, Uc, UcError
    from unicorn.arm_const import (
        UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
        UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R7, UC_ARM_REG_SP,
    )

    elf = ELFFile(io.BytesIO(path.read_bytes()))
    if (elf.elfclass, elf.little_endian, elf["e_type"], elf["e_machine"]) != (32, True, "ET_EXEC", "EM_ARM"):
        raise ValueError("RAM probe must be an ARM32 freestanding executable")
    if any(segment["p_type"] in ("PT_INTERP", "PT_DYNAMIC") for segment in elf.iter_segments()):
        raise ValueError("RAM probe must not require a dynamic loader")
    nonce = "d" * 32
    private = "/tmp/jiritsu/a6400a-" + nonce
    cases = [
        ([], {}, 0),
        ([], {"stat_result": -5}, 0),
        (["--check-network", "256", "61267"], {}, 0),
        (["--check-network", "257", "61267"], {}, 1),
        (["--check-network", "256", "61267"], {"stat_result": -5}, 1),
        (["--mount-private", nonce], {}, 0),
        (["--mount-private", nonce], {"mount_result": -19}, 1),
        (["--mount-private", nonce], {"mkdir_result": -17}, 1),
        (["--mount-private", "../elsewhere"], {}, 2),
        (["--check-private", nonce, "10", str(TMPFS_MAGIC)], {}, 0),
        (["--check-private", nonce, "10", str(TMPFS_MAGIC)], {"flags": 6}, 1),
        (["--check-private", nonce, "10", str(TMPFS_MAGIC)], {"blocks": 1024}, 1),
        (["--unmount-private", nonce], {}, 0),
        (["--unmount-private", nonce], {"unmount_result": -16}, 1),
    ]
    controls = []
    for args, policy, expected_exit in cases:
        stat_result = policy.get("stat_result", 0)
        cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        pages = set()
        for segment in elf.iter_segments():
            if segment["p_type"] == "PT_LOAD":
                if (segment["p_memsz"] > 65536 or segment["p_vaddr"] < 0x10000
                        or segment["p_vaddr"] + segment["p_memsz"] > 0x30000):
                    raise ValueError("Native probe image exceeds the declared fixture image window")
                for page in range(segment["p_vaddr"] & ~4095,
                                  (segment["p_vaddr"] + segment["p_memsz"] + 4095) & ~4095, 4096):
                    if page not in pages:
                        cpu.mem_map(page, 4096, 7)
                        pages.add(page)
                cpu.mem_write(segment["p_vaddr"], segment.data())
        stack = 0x02000000
        cpu.mem_map(stack - 65536, 131072, 3)
        strings = [b"/tmp/jiritsu/ram_probe.elf", *[arg.encode("ascii") for arg in args]]
        pointers = []
        cursor = stack + 256
        for value in strings:
            pointers.append(cursor)
            cpu.mem_write(cursor, value + b"\0")
            cursor += len(value) + 1
        cpu.mem_write(stack, struct.pack("<" + "I" * (len(pointers) + 3),
                                        len(pointers), *pointers, 0, 0))
        cpu.reg_write(UC_ARM_REG_SP, stack)
        captured = bytearray()
        exits = []
        operations = []
        mounted = bool(args and args[0] in ("--check-private", "--unmount-private"))
        created = mounted or bool(policy.get("mkdir_result"))

        def string(address: int) -> str:
            result = bytearray()
            for offset in range(96):
                char = bytes(cpu.mem_read(address + offset, 1))
                if char == b"\0":
                    return result.decode("ascii")
                result.extend(char)
            raise ValueError("Native fixture exceeded its string bound")

        def syscall(machine, number, user):
            nonlocal mounted, created
            operation = machine.reg_read(UC_ARM_REG_R7)
            a, b, c = [machine.reg_read(register) for register in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)]
            if operation in (99, 195):
                name = string(a)
                if name not in (*PATHS, private):
                    raise ValueError("Native fixture reached an undeclared path")
                if operation == 195:
                    machine.mem_write(b, struct.pack("<Q", 10 if name == private and mounted else 256))
                    result = -2 if name == private and not created else stat_result
                    machine.reg_write(UC_ARM_REG_R0, result & 0xFFFFFFFF)
                else:
                    values = ([TMPFS_MAGIC, 4096, policy.get("blocks", 512), 512, 512]
                              if name == private and mounted else [EXT2_MAGIC, 1024, 8192, 4096, 4096])
                    values += [0] * 11
                    values[11] = policy.get("flags", 14) if name == private and mounted else 0
                    machine.mem_write(b, struct.pack("<16I", *values))
                    machine.reg_write(UC_ARM_REG_R0, (-2 if name == private and not created else 0) & 0xFFFFFFFF)
            elif operation in (39, 40, 52):
                if string(a) != private:
                    raise ValueError("Private mount fixture selected an undeclared directory")
                operations.append(operation)
                if operation == 39:
                    if b != 0o700 or c:
                        raise ValueError("Private mkdir permissions changed")
                    result = policy.get("mkdir_result", 0)
                    if not result:
                        created = True
                elif operation == 40:
                    if mounted or b or c:
                        raise ValueError("Attempted to remove an active or undeclared mount")
                    result = 0
                    created = False
                else:
                    if not mounted or b or c:
                        raise ValueError("Private unmount must be acknowledged and use no force/lazy flags")
                    result = policy.get("unmount_result", 0)
                    if not result:
                        mounted = False
                machine.reg_write(UC_ARM_REG_R0, result & 0xFFFFFFFF)
            elif operation == 21:
                flags, options = machine.reg_read(UC_ARM_REG_R3), machine.reg_read(UC_ARM_REG_R4)
                if (not created or string(a) != "tmpfs" or string(b) != private
                        or string(c) != "tmpfs" or flags != 14 or string(options) != "size=2097152,mode=0700"):
                    raise ValueError("Private tmpfs fixture lost its exact path/size/safety flags")
                operations.append(operation)
                result = policy.get("mount_result", 0)
                mounted = result == 0
                machine.reg_write(UC_ARM_REG_R0, result & 0xFFFFFFFF)
            elif operation in (20, 24, 49):
                machine.reg_write(UC_ARM_REG_R0, 321 if operation == 20 else 0)
            elif operation == 4 and a == 1 and len(captured) + c <= REPORT_BYTES:
                captured.extend(machine.mem_read(b, c))
                machine.reg_write(UC_ARM_REG_R0, c)
            elif operation == 1:
                exits.append(a)
                machine.emu_stop()
            else:
                raise ValueError(f"Native fixture reached undeclared syscall {operation}")

        cpu.hook_add(UC_HOOK_INTR, syscall)
        try:
            cpu.emu_start(elf["e_entry"], 0, count=50000)
        except UcError as error:
            raise ValueError(f"RAM probe fixture execution failed at {cpu.reg_read(UC_ARM_REG_PC):#x}") from error
        if exits != [expected_exit]:
            raise ValueError("Native RAM probe did not reach its expected bounded exit")
        if not args or args[0] in ("--mount-private", "--unmount-private") and expected_exit != 2:
            decoded = decode_native(bytes(captured), private_path=private if args else None)
            if not args and decoded["records"]["/tmp_network"]["stat_result"] != stat_result:
                raise ValueError("Native probe hid a failed stat syscall")
            if args and args[0] == "--mount-private":
                if policy.get("mkdir_result"):
                    if operations != [39]:
                        raise ValueError("Existing mountpoint authorized a mount or removal")
                elif policy.get("mount_result"):
                    if operations != [39, 21, 40] or decoded["rollback_result"] != 0:
                        raise ValueError("Failed tmpfs mount did not remove only its owned empty directory")
                elif operations != [39, 21] or decoded["operation_result"]:
                    raise ValueError("Private mount success is not acknowledged")
            elif args and (operations[0] != 52 or (expected_exit == 0 and operations != [52, 40])):
                raise ValueError("Native unmount/removal order changed")
        elif captured:
            raise ValueError("Binding-only recheck unexpectedly wrote output")
        controls.append({"arguments": args, "fixtures": policy, "mutation_syscalls": operations, "exit": exits[0]})
    daemon_controls = _qualify_daemon_controls(elf)
    return {"controls": controls, "daemon_controls": daemon_controls,
            "policy": "constructed syscalls only; no actual mkdir, mount, unmount, host or camera execution"}


def _qualify_daemon_controls(elf) -> list[dict]:
    from unicorn import UC_ARCH_ARM, UC_HOOK_INTR, UC_MODE_ARM, Uc, UcError
    from unicorn.arm_const import (
        UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
        UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R7, UC_ARM_REG_SP,
    )

    nonce = "e" * 32
    private = "/tmp/jiritsu/a6400a-" + nonce
    chunk_path = private + "/chunk"
    meta_path = private + ".meta"
    ack_path = private + ".ack"
    pattern_4k = b" " * (4096 - 10) + b"A6400A_RAM"
    expected_4k_sha = hashlib.sha256(pattern_4k).hexdigest()
    results = []
    cases = [(8191, None), (8192, None)]
    cases += [(8192, case) for case in (
        "read", "reclaim-boot-cache", "capacity-change", "excluded", "short-read", "low-memory", "active-swap",
    )]
    for mem_free_kib, native_case in cases:
        cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        pages = set()
        for segment in elf.iter_segments():
            if segment["p_type"] == "PT_LOAD":
                for page in range(segment["p_vaddr"] & ~4095,
                                  (segment["p_vaddr"] + segment["p_memsz"] + 4095) & ~4095, 4096):
                    if page not in pages:
                        cpu.mem_map(page, 4096, 7)
                        pages.add(page)
                cpu.mem_write(segment["p_vaddr"], segment.data())
        stack = 0x02000000
        cpu.mem_map(stack - 65536, 131072, 3)
        p0, p1 = stack + 256, stack + 320
        cpu.mem_write(p0, b"/tmp/jiritsu/ram_probe.elf\0")
        cpu.mem_write(p1, b"--daemon\0")
        cpu.mem_write(stack, struct.pack("<5I", 2, p0, p1, 0, 0))
        cpu.reg_write(UC_ARM_REG_SP, stack)

        fs_files: dict[str, bytearray] = {
            CONFIG: bytearray(probe_config(nonce, private_tmpfs=True, partition_dump=native_case is not None)),
            "/proc/self/status": bytearray(b"Pid:\t777\nPPid:\t156\nUid:\t0\t0\t0\t0\nCapEff:\tffffffffffffffff\n"),
            "/proc/uptime": bytearray(b"100.25 0.00\n"),
            "/proc/swaps": bytearray(b"Filename\t\t\t\tType\t\tSize\tUsed\tPriority\n"),
            "/proc/meminfo": bytearray(f"MemTotal:         128000 kB\nMemFree:           {mem_free_kib} kB\nCached:            24000 kB\n".encode()),
            "/proc/self/mountinfo": bytearray(b"11 1 252:7 / / rw - ext2 /dev/nflasha7 rw\n21 13 252:6 / /tmp/jiritsu rw - vfat /dev/nflasha6 rw\n"),
            "/usr/lib/libJiritsu.so": bytearray(b"lib"),
            "/tmp/jiritsu/libJit.so": bytearray(b"jit"),
            EXECUTABLE: bytearray(b"elf"),
        }
        source_path = "/dev/nflashaB0"
        source = bytes(range(256)) * (CHUNK_BYTES // 256) + bytes(reversed(range(256))) * 16
        if native_case is not None:
            fs_files.update({
                source_path: bytearray(source),
                "/proc/partitions": bytearray(
                    f"major minor  #blocks  name\n252 255 2097152 nflasha\n"
                    f"252 0 {len(source) // 1024} nflashaB0\n252 23 4 nflasha23\n".encode()),
                "/sys/class/block/nflashaB0/size": bytearray(f"{len(source) // 512}\n".encode()),
                "/sys/class/block/nflasha23/size": bytearray(b"8\n"),
            })
        native_requests = [
            ("remove", ()),
            ("inventory", ()),
            ("chunk", (source_path, 0, CHUNK_BYTES, len(source))),
            ("remove", ()),
            ("chunk", (source_path, CHUNK_BYTES, 4096, len(source))),
            ("remove", ()),
        ]
        if native_case == "excluded":
            native_requests = [("remove", ()), ("chunk", ("/dev/nflasha23", 0, 4096, 4096))]
        open_fds: dict[int, tuple[str, int]] = {}
        next_fd = 10
        mounted = False
        created = False
        sleeps = 0
        fadvise_calls = 0
        source_reads = 0
        source_advice = []
        completed_native = []
        native_index = 0
        reclaimable_boot_kib = 0
        staged_empty_go = False

        def cstr(addr: int) -> str:
            out = bytearray()
            for off in range(128):
                ch = bytes(cpu.mem_read(addr + off, 1))
                if ch == b"\0":
                    return out.decode("ascii")
                out.extend(ch)
            raise ValueError("Daemon fixture string exceeded bound")

        def syscall(machine, _num, _user):
            nonlocal next_fd, mounted, created, sleeps, fadvise_calls, source_reads, native_index, reclaimable_boot_kib, staged_empty_go
            op = machine.reg_read(UC_ARM_REG_R7)
            a, b, c = [machine.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)]
            if op == 5:
                path = cstr(a)
                if b & 1:
                    if path not in ("/tmp/jiritsu/out.txt", meta_path, chunk_path, private + "/output.tmp"):
                        raise ValueError(f"Daemon fixture disallowed write open: {path}")
                    if b & 0o200 and path in fs_files:
                        machine.reg_write(UC_ARM_REG_R0, 0xFFFFFFEF)
                        return
                    if b & 0o1000 or path not in fs_files:
                        fs_files[path] = bytearray()
                elif path not in fs_files:
                    machine.reg_write(UC_ARM_REG_R0, (-2) & 0xFFFFFFFF)
                    return
                if path == source_path and b != 0o100000:
                    raise ValueError("Native source was not opened strictly read-only/no-follow")
                if path.startswith("/dev/") and path != source_path:
                    raise ValueError("Native reader opened an excluded or unknown device")
                fd = next_fd
                next_fd += 1
                open_fds[fd] = (path, 0)
                machine.reg_write(UC_ARM_REG_R0, fd)
            elif op == 3:
                path, pos = open_fds[a]
                contents = bytes(fs_files[path])
                if path == "/proc/meminfo" and native_case is not None:
                    charge = len(fs_files.get(chunk_path, b"")) // 1024
                    contents = re.sub(
                        rb"(MemFree:\s*)([0-9]+)",
                        lambda match: match[1] + str(max(0, int(match[2]) - charge)).encode(),
                        contents,
                    )
                chunk = contents[pos:pos + c]
                if path == source_path:
                    source_reads += 1
                    if native_case == "short-read":
                        chunk = b""
                if chunk:
                    machine.mem_write(b, chunk)
                open_fds[a] = (path, pos + len(chunk))
                machine.reg_write(UC_ARM_REG_R0, len(chunk))
            elif op == 4:
                path, pos = open_fds[a]
                data = bytes(machine.mem_read(b, c))
                fs_files[path][pos:pos + c] = data
                open_fds[a] = (path, pos + c)
                machine.reg_write(UC_ARM_REG_R0, c)
            elif op == 6:
                open_fds.pop(a, None)
                machine.reg_write(UC_ARM_REG_R0, 0)
            elif op == 270:
                target_fd_path = open_fds[a][0]
                off_lo, len_lo = machine.reg_read(UC_ARM_REG_R2), machine.reg_read(UC_ARM_REG_R4)
                if target_fd_path == source_path:
                    if b not in (1, 4) or (off_lo, len_lo) != (0, 0):
                        raise ValueError("Unexpected source fadvise advice or range")
                    source_advice.append((b, off_lo, len_lo))
                elif b != 4:
                    raise ValueError("Unexpected fadvise advice")
                if target_fd_path == "/system/av-cam.bin" and b == 4 and reclaimable_boot_kib:
                    nonlocal_mem = bytes(fs_files["/proc/meminfo"])
                    fs_files["/proc/meminfo"] = bytearray(re.sub(
                        rb"(MemFree:\s*)([0-9]+)",
                        lambda match: match[1] + str(int(match[2]) + reclaimable_boot_kib).encode(),
                        nonlocal_mem,
                    ))
                    reclaimable_boot_kib = 0
                fadvise_calls += 1
                machine.reg_write(UC_ARM_REG_R0, 0)
            elif op == 197:
                if open_fds[a][0] != source_path:
                    raise ValueError("Unexpected native fstat64 source")
                buf = bytearray(104)
                struct.pack_into("<I", buf, 16, 0o060600)
                struct.pack_into("<Q", buf, 32, 252 << 8)
                machine.mem_write(b, bytes(buf))
                machine.reg_write(UC_ARM_REG_R0, 0)
            elif op == 54:
                if open_fds[a][0] != source_path or b != 0x80041272:
                    raise ValueError("Unexpected native ioctl")
                capacity = len(source)
                if native_case == "capacity-change" and source_reads:
                    capacity += 4096
                machine.mem_write(c, struct.pack("<Q", capacity))
                machine.reg_write(UC_ARM_REG_R0, 0)
            elif op == 19:
                if open_fds[a][0] != source_path or c != 0 or b > len(source):
                    raise ValueError("Unexpected native source seek")
                open_fds[a] = (source_path, b)
                machine.reg_write(UC_ARM_REG_R0, b)
            elif op == 196:
                path = cstr(a)
                if path not in ("/tmp", "/tmp/jiritsu"):
                    raise ValueError(f"Unexpected lstat64 path: {path}")
                buf = bytearray(104)
                struct.pack_into("<I", buf, 16, 0o040755)
                machine.mem_write(b, bytes(buf))
                machine.reg_write(UC_ARM_REG_R0, 0)
            elif op in (99, 195):
                path = cstr(a)
                if path not in (*PATHS, private):
                    raise ValueError(f"Unexpected stat/statfs path: {path}")
                if op == 195:
                    machine.mem_write(b, struct.pack("<Q", 10 if path == private and mounted else 64519))
                    machine.reg_write(UC_ARM_REG_R0, (-2 if path == private and not created else 0) & 0xFFFFFFFF)
                else:
                    vals = ([TMPFS_MAGIC, 4096, 512, 512, 512] if path == private and mounted else [EXT2_MAGIC, 1024, 4823, 271, 271]) + [0] * 11
                    vals[11] = 14 if path == private and mounted else 0
                    machine.mem_write(b, struct.pack("<16I", *vals))
                    machine.reg_write(UC_ARM_REG_R0, (-2 if path == private and not created else 0) & 0xFFFFFFFF)
            elif op == 39:
                if cstr(a) != private or created:
                    raise ValueError("Unexpected daemon mkdir")
                created = True
                machine.reg_write(UC_ARM_REG_R0, 0)
            elif op == 21:
                flags, opts = machine.reg_read(UC_ARM_REG_R3), machine.reg_read(UC_ARM_REG_R4)
                if not created or cstr(b) != private or flags != 14 or cstr(opts) != "size=2097152,mode=0700":
                    raise ValueError("Unexpected daemon mount")
                mounted = True
                machine.reg_write(UC_ARM_REG_R0, 0)
            elif op == 10:
                path = cstr(a)
                if path not in (chunk_path, private + "/go"):
                    raise ValueError("Unexpected daemon unlink")
                rc = 0 if path in fs_files else -2
                fs_files.pop(path, None)
                machine.reg_write(UC_ARM_REG_R0, rc & 0xFFFFFFFF)
            elif op == 38:
                old_name, new_name = cstr(a), cstr(b)
                if (old_name, new_name) != (private + "/output.tmp", private + "/output") or old_name not in fs_files:
                    raise ValueError("Unexpected daemon rename")
                if any(path == old_name for path, _ in open_fds.values()):
                    raise ValueError("Daemon renamed output.tmp before closing fd")
                staged_out = bytes(fs_files[old_name])
                if not staged_out or not staged_out.endswith((b"\n__A6400A_RC__:0\n", b"\n__A6400A_RC__:65\n")):
                    raise ValueError("Daemon published incomplete output")
                fs_files[new_name] = fs_files.pop(old_name)
                machine.reg_write(UC_ARM_REG_R0, 0)
            elif op == 52:
                if cstr(a) != private or not mounted:
                    raise ValueError("Unexpected daemon umount2")
                mounted = False
                for path in tuple(fs_files):
                    if path.startswith(private + "/"):
                        del fs_files[path]
                machine.reg_write(UC_ARM_REG_R0, 0)
            elif op == 40:
                if cstr(a) != private or mounted or not created:
                    raise ValueError("Unexpected daemon rmdir")
                created = False
                machine.reg_write(UC_ARM_REG_R0, 0)
            elif op in (20, 24, 49):
                machine.reg_write(UC_ARM_REG_R0, 777 if op == 20 else 0)
            elif op == 162:
                sleeps += 1
                log = bytes(fs_files.get("/tmp/jiritsu/out.txt", b""))
                if f"__A6400A_PROBE_CLEAN__:{nonce}\n".encode() in log:
                    machine.emu_stop()
                    return
                if (f"__A6400A_PROBE_DATA__:4k:{nonce}\n".encode() in log
                        or b"__A6400A_INIT_ERROR__:44\n" in log):
                    if native_case is None:
                        fs_files["/proc/uptime"] = bytearray(b"102.50 0.00\n")
                        fs_files[ack_path] = bytearray(f"{nonce} cleanup 10 {TMPFS_MAGIC}\n".encode())
                    elif f"__A6400A_PROBE_DATA__:512k:{nonce}\n".encode() not in log:
                        fs_files["/proc/uptime"] = bytearray(b"102.00 0.00\n")
                        fs_files[ack_path] = bytearray(f"{nonce} 512k 10 {TMPFS_MAGIC}\n".encode())
                    else:
                        response = bytes(fs_files.get(private + "/output", b""))
                        marker = f"__A6400A_CMD_{nonce}_{native_index}__\n".encode()
                        complete = (native_index and response.startswith(marker)
                                    and response.endswith((b"\n__A6400A_RC__:0\n", b"\n__A6400A_RC__:65\n")))
                        if complete and len(completed_native) < native_index:
                            completed_native.append(response)
                            operation, arguments = native_requests[native_index - 1]
                            if operation == "chunk" and response.endswith(b"\n__A6400A_RC__:0\n"):
                                _, offset, count, _ = arguments
                                if bytes(fs_files[chunk_path]) != source[offset:offset + count]:
                                    raise ValueError("Native ARM reader produced incorrect full/tail bytes")
                                expected = hashlib.sha256(source[offset:offset + count]).hexdigest().encode()
                                if expected + b"  " + chunk_path.encode() + b"\n" not in response:
                                    raise ValueError("Native ARM source/RAM SHA-256 disagrees with Python")
                        refused = complete and response.endswith(b"\n__A6400A_RC__:65\n")
                        if refused or len(completed_native) == len(native_requests):
                            fs_files["/proc/uptime"] = bytearray(b"103.00 0.00\n")
                            fs_files[ack_path] = bytearray(f"{nonce} cleanup 10 {TMPFS_MAGIC}\n".encode())
                        elif native_index == 0 or complete:
                            if native_case == "low-memory":
                                fs_files["/proc/meminfo"] = bytearray(b"MemFree: 8191 kB\n")
                            if native_case == "reclaim-boot-cache" and native_index == 2:
                                fs_files["/system/av-cam.bin"] = bytearray(b"r" * 4096)
                                reclaimable_boot_kib = 1024
                                fs_files["/proc/meminfo"] = bytearray(b"MemFree: 7800 kB\n")
                            if native_case == "active-swap":
                                fs_files["/proc/swaps"] += b"/dev/fixture partition 1024 0 -2\n"
                            operation, arguments = native_requests[native_index]
                            if native_case == "read" and native_index == 2 and not staged_empty_go:
                                staged_empty_go = True
                                fs_files[private + "/go"] = bytearray()
                            else:
                                native_index += 1
                                fields = (nonce, operation, str(native_index), "10", str(TMPFS_MAGIC),
                                          *(str(value) for value in arguments))
                                fs_files[private + "/input"] = bytearray((" ".join(fields) + "\n").encode())
                                fs_files[private + "/go"] = bytearray(b"go\n")
                elif f"__A6400A_PROBE_META__:{nonce}\n".encode() in log and ack_path not in fs_files:
                    fs_files["/proc/uptime"] = bytearray(b"101.00 0.00\n")
                    fs_files[ack_path] = bytearray(f"{nonce} 4k 10 {TMPFS_MAGIC}\n".encode())
                if sleeps > 30:
                    raise ValueError("Daemon fixture exceeded bounded sleep count")
                machine.reg_write(UC_ARM_REG_R0, 0)
            else:
                raise ValueError(f"Daemon fixture reached undeclared syscall {op}")

        cpu.hook_add(UC_HOOK_INTR, syscall)
        try:
            cpu.emu_start(elf["e_entry"], 0, count=250000000 if native_case is not None else 2000000)
        except UcError as error:
            raise ValueError(f"RAM daemon fixture failed at {cpu.reg_read(UC_ARM_REG_PC):#x}") from error
        log = bytes(fs_files["/tmp/jiritsu/out.txt"])
        prove_swap_inactive(log)
        clean_meta = decode_native(bytes(fs_files[meta_path]), private_path=private)
        if clean_meta["operation"] != 3 or clean_meta["operation_result"] != 0 or clean_meta["rollback_result"] != 0 or mounted or created:
            raise ValueError("Daemon fixture did not complete verified unmount and rmdir")
        if mem_free_kib < 8192:
            if b"__A6400A_INIT_ERROR__:44\n" not in log or b"__A6400A_PROBE_DATA__:" in log:
                raise ValueError("Daemon fixture permitted data below the 8192-KiB guard")
        else:
            expected_line = f"{expected_4k_sha}  {chunk_path}\n".encode()
            if expected_line not in log:
                raise ValueError("Daemon fixture SHA-256 did not match Python hashlib.sha256")
        if native_case is not None:
            if native_case in ("read", "reclaim-boot-cache"):
                if len(completed_native) != 6 or source_advice != [(1, 0, 0), (4, 0, 0), (1, 0, 0), (4, 0, 0)]:
                    raise ValueError("Native reader did not complete both bounded chunks and removals")
                from tools.research.a6400a_jiritsu_worker import parse_inventory
                inventory = completed_native[1].split(b"\n", 1)[1].rsplit(b"\n__A6400A_RC__:", 1)[0]
                if parse_inventory(inventory)[1] != {source_path: len(source)}:
                    raise ValueError("Native inventory did not preserve the host geometry/exclusion contract")
            elif not completed_native or not completed_native[-1].endswith(b"\n__A6400A_RC__:65\n"):
                raise ValueError("Native reader did not explicitly refuse the constructed source/guard failure")
            if native_case in ("excluded", "low-memory", "active-swap") and source_reads:
                raise ValueError("Native reader touched source bytes despite an exclusion or failed guard")
        results.append({"mem_free_kib_fixture": mem_free_kib, "native_case": native_case,
                        "fadvise_calls": fadvise_calls, "source_reads": source_reads,
                        "source_advice_ranges": source_advice, "completed_native": len(completed_native), "sleeps": sleeps})
    return results


def build_executable(output: Path) -> int:
    from tools.research.rea_wsl_batch import wsl_path

    output = output.resolve()
    if not output.is_relative_to(PROJECT_ROOT / "artifacts"):
        raise ValueError("RAM probe builds must stay under artifacts")
    output.mkdir(parents=True, exist_ok=False)
    binary = output / "ram_probe.elf"
    command = ["wsl.exe", "--exec", "arm-linux-gnueabi-gcc", "-std=gnu11", "-march=armv7-a", "-mthumb",
               "-mthumb-interwork", "-mfloat-abi=soft", "-Os", "-fno-ident", "-Wall", "-Wextra", "-Werror", "-fomit-frame-pointer",
               "-ffreestanding", "-fno-builtin", "-fno-stack-protector", "-fno-pie", "-no-pie",
               "-nostdlib", "-static", "-Wl,-s,-e,_start,-z,defs,-z,noexecstack,--build-id=none,-Ttext-segment=0x10000",
               "-o", wsl_path(binary), wsl_path(SOURCE)]
    report = {"status": "in_progress", "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              "command": command, "camera_operations": 0}
    try:
        result = subprocess.run(command, capture_output=True, timeout=120, check=False)
        (output / "stdout.log").write_bytes(result.stdout)
        (output / "stderr.log").write_bytes(result.stderr)
        if result.returncode:
            raise RuntimeError("RAM probe compilation failed; see retained stderr.log")
        if not 0 < binary.stat().st_size <= 16000:
            raise ValueError("Native RAM probe exceeds the small-upload profile")
        report.update(status="built_not_deployed", sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                      bytes=binary.stat().st_size, qualification=qualify_executable(binary))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
    finally:
        (output / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "built_not_deployed" else 2
