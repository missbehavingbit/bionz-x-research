"""Opt-in ILCE-6400A 1.01 Jiritsu worker; ordinary files carry bounded RAM chunks."""
from __future__ import annotations

from array import array
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import signal
import struct
import subprocess
from threading import Event
import time
from typing import TYPE_CHECKING
import uuid

from project_paths import PROJECT_ROOT
from tools.research import a6400a_senser_acquire as senser
from tools.research.a6400a_fixed_acquire import admit_receipts, load_receipt
from tools.research.a6400a_fixed_reader import BulkTransport, HEADER, TRANSFER_BYTES
from tools.research.a6400a_jiritsu import (
    CHUNK_BYTES, JiritsuStatusError, RamChunkWorker, chunk_cleanup_command,
    ephemeral_chunk_worker_script, readable_storage,
)
from tools.research.a6400a_ram_probe import (
    CONFIG as RAM_PROBE_CONFIG, EXECUTABLE as RAM_PROBE_EXECUTABLE, REPORT_BYTES,
    SOURCE as RAM_PROBE_SOURCE, RamWorkspaceProbe, probe_config, probe_script, qualify_executable,
)

if TYPE_CHECKING:
    from usb.core import Device

VERSION = b"38117_03.2026041003"
LIBRARY_SHA = "c3621c7ec30771604b1aaae25e51699340333486e1aa281a1cc5f4a543a99dff"
SOURCE = PROJECT_ROOT / "tools" / "native_camera" / "jiritsu_ram_loader.S"
ATTEMPTS = PROJECT_ROOT / "artifacts" / "camera-sessions" / "a6400a-jiritsu-worker"
WORKSPACE_FILES = ("/tmp/jiritsu/run.sh", "/tmp/jiritsu/libJit.so", "/tmp/jiritsu/out.txt")
INVENTORY_COMMAND = (
    "printf '__UPTIME__\\n'; busybox cat /proc/uptime && "
    "printf '__PARTITIONS__\\n' && busybox cat /proc/partitions && "
    "printf '__SIZES__\\n' && "
    "for f in /sys/class/block/nflasha*/size; do "
    '[ -f "$f" ] || break; n=${f%/size}; n=${n##*/}; '
    'printf "%s " "$n"; busybox cat "$f" || break; done'
)


class WorkerUsbTransport:
    def __init__(self, device: Device, driver):
        if (device.idVendor, device.idProduct) != (0x054C, 0x0336):
            raise ValueError("Jiritsu worker requires the admitted 054C:0336 device")
        interfaces = list(device.get_active_configuration())
        if (len(interfaces) != 1 or interfaces[0].bInterfaceNumber != 0
                or interfaces[0].bAlternateSetting != 0
                or interfaces[0].bInterfaceClass != 0xFF
                or driver.backend.dev is not device):
            raise ValueError("Unexpected service interface or authentication handle")
        endpoints = list(interfaces[0])
        inputs = [ep for ep in endpoints if ep.bmAttributes == 2 and ep.bEndpointAddress & 0x80]
        outputs = [ep for ep in endpoints if ep.bmAttributes == 2 and not ep.bEndpointAddress & 0x80]
        if (not inputs or not outputs or not driver.epIn or not driver.epOut
                or driver.epIn[0] != inputs[0].bEndpointAddress
                or driver.epOut[0] != outputs[0].bEndpointAddress
                or inputs[0].wMaxPacketSize != 512 or outputs[0].wMaxPacketSize != 512):
            raise ValueError("Service primary endpoints differ from the qualified 512-byte profile")
        self.device = device
        self.ep_in, self.ep_out = inputs[0].bEndpointAddress, outputs[0].bEndpointAddress

    def read(self, length: int, *, timeout_ms: int) -> bytes:
        if not 0 < length <= TRANSFER_BYTES or not 1 <= timeout_ms <= 2000:
            raise ValueError("Jiritsu USB read exceeds its operation bounds")
        raw = self.device.read(self.ep_in, length, timeout=timeout_ms)
        if isinstance(raw, array) and raw.typecode == "B":
            return raw.tobytes()
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        raise OSError("Jiritsu USB read returned a non-byte buffer")

    def write(self, data: bytes, *, timeout_ms: int) -> int:
        if not 0 < len(data) < TRANSFER_BYTES or not 1 <= timeout_ms <= 2000:
            raise ValueError("Jiritsu USB write exceeds its single-transfer profile")
        count = self.device.write(self.ep_out, data, timeout=timeout_ms)
        if type(count) is not int:
            raise OSError("Jiritsu USB write returned no acknowledged byte count")
        return count


class JiritsuFiles:
    """Unencrypted legacy GET_FILE framing verified by the preserved live captures."""

    def __init__(self, transport: BulkTransport, ram_dir: str, events: list, *,
                 probe: bool = False, private_tmpfs: bool = False, partition_dump: bool = False):
        prefix = "/tmp/jiritsu" if private_tmpfs else "/tmp_network"
        if (private_tmpfs and not probe
                or partition_dump and not (probe and private_tmpfs)
                or re.fullmatch(re.escape(prefix) + r"/a6400a-[0-9a-f]{32}", ram_dir) is None):
            raise ValueError("Live workspace must be a unique declared RAM directory")
        self.transport, self.ram_dir, self.events = transport, ram_dir, events
        self.sequence = 0
        self.in_sync = True
        self.probe = probe
        self.partition_dump = partition_dump

    @property
    def probe_ack(self) -> str:
        return "/tmp/jiritsu/" + self.ram_dir.rsplit("/", 1)[1] + ".ack"

    @property
    def probe_metadata(self) -> str:
        return "/tmp/jiritsu/" + self.ram_dir.rsplit("/", 1)[1] + ".meta"

    def exchange(self, code: int, body: bytes, limit: int) -> bytes:
        if not self.in_sync:
            raise RuntimeError("Jiritsu channel is unusable after an unresolved transfer")
        if (code not in (2, 7, 8, 9, 15, 16, 18) or not 0 <= limit <= CHUNK_BYTES
                or len(body) + HEADER.size + 4 >= TRANSFER_BYTES):
            raise ValueError("Jiritsu operation exceeds the bounded worker profile")
        if self.sequence >= 0xFFFF:
            raise ValueError("Jiritsu sequence space exhausted; no wrap or replay")
        self.sequence += 1
        command = struct.pack("<HH", 0x1001, code) + body
        packet = HEADER.pack(len(command), 0x40, self.sequence, 0, 0, 0, 0) + command
        event = {"operation": "jiritsu", "command": code, "sequence": self.sequence,
                 "request_bytes": len(packet), "wire_complete": False, "limit": limit}
        self.events.append(event)
        self.in_sync = False
        deadline = time.monotonic() + 30
        calls = 0
        budget = limit + 4 + HEADER.size

        def read(length: int) -> bytes:
            nonlocal calls, budget
            remaining = deadline - time.monotonic()
            if remaining <= 0 or calls >= 128 + 64 * math.ceil((limit + 4) / TRANSFER_BYTES):
                raise TimeoutError("Jiritsu transfer deadline/I/O bound reached; no retry")
            calls += 1
            data = self.transport.read(length, timeout_ms=min(2000, max(1, math.ceil(remaining * 1000))))
            if not isinstance(data, bytes) or len(data) > length or len(data) > budget:
                raise OSError("Jiritsu transfer returned invalid or excessive bytes")
            budget -= len(data)
            if time.monotonic() >= deadline:
                raise TimeoutError("Jiritsu response arrived after its deadline")
            return data

        def exact(length: int) -> bytes:
            result = bytearray()
            while len(result) < length:
                data = read(min(length - len(result), TRANSFER_BYTES))
                if not data:
                    raise OSError("Unexpected empty Jiritsu data/header transfer")
                result.extend(data)
            return bytes(result)

        count = self.transport.write(packet, timeout_ms=2000)
        event["acknowledged_bytes"] = count
        if type(count) is not int or count != len(packet):
            raise OSError("Jiritsu request was not completely acknowledged; no retry")
        header = HEADER.unpack(exact(HEADER.size))
        size, function, sequence, version, micon, offset, response = header
        event["header"] = list(header)
        if (function, sequence, version, micon, offset) != (0x40, self.sequence, 3, 0, 0):
            raise ValueError("Jiritsu reply identity differs from the live legacy profile")
        if size == 0:
            self.in_sync = True
            event["wire_complete"] = True
            raise RuntimeError(f"Jiritsu envelope refused the request: {response:#x}")
        if response or not 4 <= size <= limit + 4:
            raise ValueError("Jiritsu reply status/extent is outside the declared operation")
        payload = bytearray()
        while len(payload) < size:
            amount = min(TRANSFER_BYTES, size - len(payload))
            payload.extend(exact(amount))
            if amount % 512 == 0 and read(512):
                raise ValueError("Jiritsu transfer terminator contains unexpected bytes")
        self.in_sync = True
        status = struct.unpack_from("<I", payload)[0]
        event.update(wire_complete=True, status=status, received_bytes=len(payload),
                     io_calls=calls, sha256=hashlib.sha256(payload).hexdigest())
        if status:
            raise JiritsuStatusError(status)
        return bytes(payload[4:])

    def read(self, path: str, limit: int) -> bytes:
        allowed = {"/version.txt", "/usr/lib/libJiritsu.so", "/tmp/jmode", "/tmp/jbusy",
                   *WORKSPACE_FILES}
        if self.probe:
            allowed.update((RAM_PROBE_EXECUTABLE, RAM_PROBE_CONFIG, self.probe_ack, self.probe_metadata))
        if path not in allowed and path not in {
            self.ram_dir + "/" + name for name in ("ready", "input", "output", "chunk")
        }:
            raise ValueError("File read is outside the declared Jiritsu worker namespace")
        return self.exchange(8, b"\x01\x00\x00/.." + path.encode("ascii") + b"\0", limit)

    def write(self, path: str, data: bytes, *, verify: bool = True) -> None:
        allowed = {WORKSPACE_FILES[0], self.ram_dir + "/input", self.ram_dir + "/go"}
        if self.probe:
            allowed.update((RAM_PROBE_EXECUTABLE, RAM_PROBE_CONFIG, self.probe_ack))
        if path not in allowed:
            raise ValueError("Upload is outside the owned worker control files")
        if path == RAM_PROBE_CONFIG:
            nonce = self.ram_dir.rsplit("-", 1)[1]
            expected = probe_config(nonce, private_tmpfs=self.ram_dir.startswith("/tmp/jiritsu/"),
                                    partition_dump=self.partition_dump)
            if data != expected:
                raise ValueError("Native configuration differs from its nonce/mount/reader policy")
        if path == self.probe_ack:
            nonce = self.ram_dir.rsplit("-", 1)[1].encode("ascii")
            if re.fullmatch(nonce + rb" (?:4k|512k|cleanup) [0-9]{1,10} [0-9]{1,10}\n", data) is None:
                raise ValueError("RAM probe acknowledgment is outside its fixed phase protocol")
        if not data or len(data) > 16000:
            raise ValueError("Worker control upload is empty or exceeds 16000 bytes")
        target = "/tmp/jiritsu/../.." + path if path.startswith("/tmp_network/") else path
        self.exchange(15, b"\0" + target.encode("ascii") + b"\0", 0)
        self.exchange(16, b"\x00\xff\x00" + data, 0)
        if verify and self.read(path, len(data)) != data:
            raise ValueError("Worker control-file readback differs from the upload")

    def require_absent(self, path: str) -> None:
        try:
            self.read(path, 32700)
        except JiritsuStatusError as error:
            if error.status != 0x10000006:
                raise
        else:
            raise FileExistsError("Existing Jiritsu file/marker; no overwrite permitted: " + path)

    def remove_owned(self, path: str, expected: bytes) -> None:
        allowed = set(WORKSPACE_FILES)
        if self.probe:
            allowed.update((RAM_PROBE_EXECUTABLE, RAM_PROBE_CONFIG, self.probe_ack, self.probe_metadata))
        if path not in allowed or self.read(path, len(expected)) != expected:
            raise ValueError("File no longer matches the owned script/loader; refusing deletion")
        if path == WORKSPACE_FILES[1]:
            self.exchange(9, b"\x00\x02\x00", 0)
        else:
            self.exchange(15, b"\0" + path.encode("ascii") + b"\0", 0)
            self.exchange(18, b"\x00\xff", 0)
        self.require_absent(path)

class NativeRamChunkWorker(RamChunkWorker):
    """Reuse durable capture handling with a qualified, non-shell native worker."""

    def __init__(self, probe: RamWorkspaceProbe):
        rounds = probe.report.get("ram_roundtrips", [])
        if (not probe.ready or probe.failed or probe.binding is None
                or probe.binding.kind != "private-tmpfs"
                or [row.get("bytes") for row in rounds] != [4096] * 3 + [CHUNK_BYTES] * 3
                or any(row.get("host_readback_verified") is not True for row in rounds)):
            raise ValueError("Native reader requires private RAM and six verified roundtrips")
        super().__init__(probe.files, probe.ram_dir, probe.nonce, clock=probe.clock, sleep=probe.sleep)
        self.probe = probe
        self.probe.native_command("remove")
        self.probe.report["ram_proof_slot_retired"] = True
        self.ready = True

    def wait_ready(self) -> bytes:
        if not self.probe.ready or self.probe.failed:
            raise RuntimeError("Native reader qualification is no longer valid")
        return self.files.read("/tmp/jiritsu/out.txt", 65536)

    def command(self, command: str) -> bytes:
        if command != INVENTORY_COMMAND:
            raise ValueError("Native reader does not accept shell commands")
        return self.probe.native_command("inventory")

    def read_chunk(self, partition: str, total_bytes: int, chunk: dict) -> str:
        self.probe.report["raw_partition_read_attempted"] = True
        if self.probe.report.get("raw_partition_reads") is not True:
            self.probe.report["raw_partition_reads"] = None
        body = self.probe.native_command("chunk", partition, chunk["offset"], chunk["count"], total_bytes)
        self.probe.report["raw_partition_reads"] = True
        match = re.search(
            rb"^__A6400A_CHUNK__\n([0-9a-f]{64}  "
            + re.escape((self.ram_dir + "/chunk").encode("ascii")) + rb")\n$", body, re.M,
        )
        if match is None:
            raise ValueError("Native chunk has no exact source-bound checksum")
        return match[1].decode("ascii")

    def remove_chunk(self, chunk: dict) -> None:
        self.probe.native_command("remove")


def parse_inventory(data: bytes) -> tuple[float, dict[str, int]]:
    try:
        before, partitions, sizes = re.split(rb"__PARTITIONS__\n|__SIZES__\n", data)
    except ValueError as error:
        raise ValueError("Worker inventory is incomplete or has repeated sections") from error
    if not before.startswith(b"__UPTIME__\n"):
        raise ValueError("Worker inventory has no uptime")
    uptime = senser.uptime(before.removeprefix(b"__UPTIME__\n"))
    names = senser.partition_names(partitions)
    proc_sizes = {row[3]: int(row[2]) * 1024
                  for line in partitions.decode("ascii").splitlines()
                  if len(row := line.split()) == 4 and row[3] in names}
    capacities = {}
    for line in sizes.decode("ascii").splitlines():
        fields = line.split()
        if len(fields) != 2 or not fields[1].isdigit():
            raise ValueError("Malformed sysfs block-size inventory")
        name, sectors = fields
        if name in capacities:
            raise ValueError("Duplicate sysfs block-size inventory")
        capacities[name] = int(sectors) * 512
    result = {}
    for name in names:
        if name not in capacities or proc_sizes[name] != capacities[name]:
            raise ValueError("Live /proc and sysfs geometry disagree: " + name)
        if name == "nflasha23":
            continue
        readable_storage("/dev/" + name)
        size = capacities[name]
        if size and (size % 4096 or size > senser.MAX_PARTITION_BYTES):
            raise ValueError("Partition capacity exceeds the aligned 512-MiB profile: " + name)
        if size:
            result["/dev/" + name] = size
    if not result:
        raise ValueError("No allowed nonzero live partition capacities")
    return uptime, result


def qualify_loader(path: Path) -> dict:
    from elftools.elf.elffile import ELFFile
    from unicorn import UC_ARCH_ARM, UC_HOOK_INTR, UC_MODE_THUMB, Uc, UcError
    from unicorn.arm_const import (
        UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
        UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R7, UC_ARM_REG_SP,
    )

    raw = path.read_bytes()
    elf = ELFFile(io.BytesIO(raw))
    if (elf.elfclass, elf.little_endian, elf["e_machine"], elf["e_type"]) != (
            32, True, "EM_ARM", "ET_DYN"):
        raise ValueError("Loader must be an ARM32 little-endian shared library")
    symbols = elf.get_section_by_name(".dynsym")
    text = elf.get_section_by_name(".text")
    init = elf.get_section_by_name(".init_array")
    dynamic = elf.get_section_by_name(".dynamic")
    if symbols is None or text is None or init is None or init.data_size != 4 or dynamic is None:
        raise ValueError("Loader constructor/section metadata is missing")
    if any(symbol.name and symbol["st_shndx"] == "SHN_UNDEF" for symbol in symbols.iter_symbols()):
        raise ValueError("Freestanding loader has an unresolved import")
    if any(tag.entry.d_tag == "DT_NEEDED" for tag in dynamic.iter_tags()):
        raise ValueError("Freestanding loader has a library dependency")
    entries = [symbol["st_value"] for symbol in symbols.iter_symbols() if symbol.name == "launch"]
    if len(entries) != 1 or not entries[0] & 1:
        raise ValueError("Loader has no single Thumb launch entry")
    base, stack, returned = 0x01000000, 0x02000000, 0x03000000
    controls = []
    for fork_result, native_present in ((123, True), (-11, True), (0, True), (0, False)):
        cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        start = base + text["sh_addr"]
        cpu.mem_map(start & ~4095, ((start + text.data_size + 4095) & ~4095) - (start & ~4095), 7)
        cpu.mem_write(start, text.data())
        cpu.mem_map(stack, 4096, 3)
        cpu.mem_map(returned, 4096, 5)
        cpu.reg_write(UC_ARM_REG_SP, stack + 4096)
        cpu.reg_write(UC_ARM_REG_LR, returned | 1)
        preserved = {UC_ARM_REG_R4: 0x44444444, UC_ARM_REG_R5: 0x55555555,
                     UC_ARM_REG_R7: 0x77777777}
        for register, value in preserved.items():
            cpu.reg_write(register, value)
        calls = []

        def syscall(machine, number, user):
            operation = machine.reg_read(UC_ARM_REG_R7)
            argument = machine.reg_read(UC_ARM_REG_R0)
            calls.append((operation, argument))
            if operation == 190 and len(calls) == 1:
                machine.reg_write(UC_ARM_REG_R0, fork_result & 0xFFFFFFFF)
            elif fork_result == 0 and operation == 6 and 3 <= argument <= 255:
                machine.reg_write(UC_ARM_REG_R0, 0)
            elif fork_result == 0 and operation == 15:
                if (bytes(machine.mem_read(argument, 27)) != b"/tmp/jiritsu/ram_probe.elf\0"
                        or machine.reg_read(UC_ARM_REG_R1) != 0o700):
                    raise ValueError("Loader chmod selected an undeclared executable/mode")
                machine.reg_write(UC_ARM_REG_R0, 0 if native_present else 0xFFFFFFFE)
            elif fork_result == 0 and operation == 11:
                executable = b"/tmp/jiritsu/ram_probe.elf\0" if native_present else b"/bin/sh\0"
                selected = bytes(machine.mem_read(argument, len(executable)))
                argv = machine.reg_read(UC_ARM_REG_R1)
                script_pointer = struct.unpack("<I", machine.mem_read(argv + 4, 4))[0]
                expected_arg = b"--daemon\0" if native_present else b"/tmp/jiritsu/run.sh\0"
                if (selected != executable
                        or bytes(machine.mem_read(script_pointer, len(expected_arg))) != expected_arg
                        or machine.reg_read(UC_ARM_REG_SP) % 8):
                    raise ValueError("Loader child selected an unexpected executable/script")
                machine.emu_stop()
            else:
                raise ValueError("Loader reached an undeclared syscall")

        cpu.hook_add(UC_HOOK_INTR, syscall)
        try:
            cpu.emu_start(base + entries[0], returned, count=10000)
        except UcError as error:
            raise ValueError("Loader ABI fixture stopped on unsupported execution") from error
        if fork_result:
            if (cpu.reg_read(UC_ARM_REG_PC) != returned
                    or cpu.reg_read(UC_ARM_REG_SP) != stack + 4096
                    or any(cpu.reg_read(register) != value for register, value in preserved.items())
                    or len(calls) != 1):
                raise ValueError("Loader parent did not promptly preserve the constructor ABI")
        elif (len(calls) != 256 or calls[-1][0] != 11
              or [argument for operation, argument in calls if operation == 6] != list(range(3, 256))):
            raise ValueError("Loader child did not close inherited descriptors and reach execve")
        controls.append({"vfork_result": fork_result, "native_present": native_present, "syscalls": len(calls),
                         "execution": "constructed syscalls only; no host or camera execution"})
    return {"constructor_controls": controls, "sha256": hashlib.sha256(raw).hexdigest()}


def build_loader(output: Path) -> int:
    from tools.research.rea_wsl_batch import wsl_path

    output = output.resolve()
    if not output.is_relative_to(PROJECT_ROOT / "artifacts"):
        raise ValueError("Loader builds must remain under artifacts")
    output.mkdir(parents=True, exist_ok=False)
    binary = output / "libJit.so"
    command = [
        "wsl.exe", "--exec", "arm-linux-gnueabi-gcc", "-march=armv7-a", "-mthumb",
        "-nostdlib", "-shared", "-fPIC",
        "-Wl,-Bsymbolic,-z,defs,-z,noexecstack,--build-id=none,-z,max-page-size=4096,--hash-style=both",
        "-o", wsl_path(binary), wsl_path(SOURCE),
    ]
    report = {"status": "in_progress", "source_sha256": senser.host_digest(SOURCE),
              "command": command, "camera_operations": 0}
    try:
        result = subprocess.run(command, capture_output=True, timeout=120, check=False)
        (output / "stdout.log").write_bytes(result.stdout)
        (output / "stderr.log").write_bytes(result.stderr)
        report["exit_code"] = result.returncode
        if result.returncode:
            raise RuntimeError("ARM loader compilation failed; see retained stderr.log")
        if not 0 < binary.stat().st_size < 32700:
            raise ValueError("Loader exceeds the bounded upload profile")
        report.update(qualify_loader(binary), status="built_not_deployed", file=binary.name,
                      bytes=binary.stat().st_size)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
    finally:
        (output / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "built_not_deployed" else 2


def acquire(args) -> int:
    if (args.mode not in ("check", "jiritsu-dump", "cleanup-worker", "qualify-ram")
            or not args.owner_authorized_jiritsu_workspace or not args.confirm_firmware_101):
        raise ValueError("An explicit owner-approved ILCE-6400A 1.01 worker policy is required")
    if args.mode == "jiritsu-dump":
        requested = args.partitions or [args.partition]
        if requested != ["all-allowed"]:
            if not requested or len(requested) != len(set(requested)):
                raise ValueError("Requested partitions must be nonempty and unique")
            for path in requested:
                readable_storage(path)
    if args.mode == "qualify-ram" and (args.partitions or args.partition != "/dev/nflashaB0"):
        raise ValueError("RAM qualification cannot request firmware partitions")
    native_reader = getattr(args, "native_reader", False)
    if native_reader and (args.mode != "jiritsu-dump" or not getattr(args, "private_tmpfs", False)
                          or args.ram_probe_manifest is None):
        raise ValueError("Native partition reading requires private RAM and its native build manifest")
    if getattr(args, "private_tmpfs", False) and args.mode != "qualify-ram" and not native_reader:
        raise ValueError("A private RAM mount requires explicit qualification or the native reader")
    with senser.host_lock():
        return _acquire_locked(args)


def _acquire_locked(args) -> int:
    output = args.output_dir.resolve()
    if not output.is_relative_to(PROJECT_ROOT / "artifacts"):
        raise ValueError("Jiritsu capture output must remain under artifacts")
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "status": "in_progress", "started_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Owner-approved Jiritsu workspace writes and single-slot RAM chunk capture",
        "events": [], "captures": [], "camera_file_writes": False, "loader_upload_attempted": False,
        "driver_changes": False, "raw_storage_writes": False, "updater_operations": False,
        "excluded_storage": ["/dev/nflasha23"], "full_firmware_captured": False,
        "cleanup_verified": False, "source_sha256": senser.host_digest(Path(__file__)),
        "worker_source_sha256": senser.host_digest(Path(__file__).with_name("a6400a_jiritsu.py")),
    }
    modules = device = driver = files = worker = None
    old_signal = None
    signal_installed = False
    cancel = Event()
    protocol_error = RuntimeError
    payload = script = probe_elf = b""
    owned: dict = {}
    try:
        baseline_record, report["baseline_sha256"] = load_receipt(args.baseline)
        identity_record, report["identity_receipt_sha256"] = load_receipt(args.identity_receipt)
        baseline, report["identity"] = admit_receipts(baseline_record, identity_record)
        if args.mode != "check":
            manifest, report["loader_manifest_sha256"] = load_receipt(args.loader_manifest)
            loader = args.loader_manifest.resolve().parent / "libJit.so"
            payload = loader.read_bytes()
            if (manifest.get("status") != "built_not_deployed"
                    or manifest.get("source_sha256") != senser.host_digest(SOURCE)
                    or manifest.get("sha256") != hashlib.sha256(payload).hexdigest()
                    or not 0 < len(payload) < 32700):
                raise ValueError("No matching authored, bounded loader build")
            report["loader_qualification"] = qualify_loader(loader)
        if args.mode == "cleanup-worker":
            owned, report["owned_receipt_sha256"] = load_receipt(args.owned_receipt)
            script = args.owned_receipt.resolve().parent.joinpath("worker.sh").read_bytes()
            if (owned.get("loader_upload_attempted") is not True and owned.get("camera_file_writes") is not True
                    or owned.get("identity", {}).get("model") != "ILCE-6400A"
                    or owned.get("loader_qualification", {}).get("sha256") != hashlib.sha256(payload).hexdigest()
                    or owned.get("worker_script_sha256") != hashlib.sha256(script).hexdigest()
                    or not 0 < len(script) <= 16000
                    or re.fullmatch(r"/(?:tmp_network|tmp/jiritsu)/a6400a-[0-9a-f]{32}", owned.get("worker_ram_dir", "")) is None):
                raise ValueError("Cleanup needs an exact preserved uploaded script/loader identity")
        partition_dump = (args.mode == "jiritsu-dump" and getattr(args, "native_reader", False)
                          or owned.get("ram_partition_dump") is True)
        probe_enabled = args.mode == "qualify-ram" or partition_dump or owned.get("ram_probe") is True
        private_tmpfs = ((args.mode == "qualify-ram" or partition_dump) and args.private_tmpfs
                         or owned.get("ram_private_tmpfs") is True)
        if probe_enabled:
            if args.ram_probe_manifest is None:
                raise ValueError("RAM probe requires its native executable manifest")
            probe_build, report["ram_probe_manifest_sha256"] = load_receipt(args.ram_probe_manifest)
            probe_path = args.ram_probe_manifest.resolve().parent / "ram_probe.elf"
            probe_elf = probe_path.read_bytes()
            if (probe_build.get("status") != "built_not_deployed"
                    or probe_build.get("sha256") != hashlib.sha256(probe_elf).hexdigest()
                    or (args.mode != "cleanup-worker" and probe_build.get("source_sha256") != senser.host_digest(RAM_PROBE_SOURCE))
                    or (owned and owned.get("ram_probe_elf_sha256") != hashlib.sha256(probe_elf).hexdigest())
                    or not 0 < len(probe_elf) <= 16000):
                raise ValueError("No matching authored, bounded native RAM probe")
            report["ram_probe_native_qualification"] = qualify_executable(probe_path)
            report["ram_probe_elf_sha256"] = hashlib.sha256(probe_elf).hexdigest()
            report["ram_probe_host_sha256"] = senser.host_digest(Path(__file__).with_name("a6400a_ram_probe.py"))
            report["ram_probe"] = True
            report["ram_private_tmpfs"] = private_tmpfs
            report["ram_partition_dump"] = partition_dump
            if private_tmpfs:
                report["private_tmpfs_cap_bytes"] = 2 * 1024 * 1024
            report["raw_partition_reads"] = False
            report["scope"] = ("Qualified private RAM and bounded native partition reads"
                               if partition_dump else
                               "Read-only filesystem evidence and known-pattern RAM/USB qualification; no firmware partition reads")
        metadata = report["metadata_before"] = senser.discover()
        senser.admit_device(metadata, baseline, "read")
        if metadata["pid"] != 0x0336:
            raise ValueError("Enter the reviewed 054C:0336 service session first")
        modules = senser.load_pmca()
        protocol_error = modules["generic"].GenericUsbException
        device, raw_driver = senser.connect(metadata, modules)
        transport = WorkerUsbTransport(device, raw_driver)
        driver = senser.BoundedDriver(raw_driver, report["events"])
        report["start_control_attempted"] = True
        senser.control_request(driver, senser.START, protocol_error, modules["core"].USBError,
                               args.permit_control_pipe, report)
        driver.begin("auth")
        senser.authenticate(driver, modules["constants"], modules["crypto"])
        report["authentication_accepted"] = True
        nonce = uuid.uuid4().hex if not owned else owned["worker_ram_dir"].rsplit("-", 1)[1]
        ram_dir = ("/tmp/jiritsu" if private_tmpfs else "/tmp_network") + "/a6400a-" + nonce
        files = JiritsuFiles(transport, ram_dir, report["events"], probe=probe_enabled,
                            private_tmpfs=private_tmpfs, partition_dump=partition_dump)
        if files.read("/version.txt", 64) != VERSION:
            raise ValueError("Live camera system version does not match the 1.01 package")
        library = files.read("/usr/lib/libJiritsu.so", 162568)
        if hashlib.sha256(library).hexdigest() != LIBRARY_SHA:
            raise ValueError("Actual Jiritsu library does not match the analyzed 6400A image")
        report["live_library_sha256"] = LIBRARY_SHA
        report["system_version"] = VERSION.decode("ascii")
        if args.mode == "check":
            report["status"] = "small_file_and_live_library_verified"
        elif args.mode == "cleanup-worker":
            files.exchange(2, b"\x00\x00", 0)
            report.update(
                status="owned_worker_cleanup_requested", cleanup_only=True,
                camera_file_writes=True,
                owned_loader_cleanup_required=owned.get("loader_upload_attempted") is True,
                worker_ram_dir=ram_dir, power_removal_required=True,
            )
        else:
            ATTEMPTS.mkdir(parents=True, exist_ok=True)
            marker = ATTEMPTS / (report["baseline_sha256"] + ".json")
            with marker.open("x", encoding="utf-8") as stream:
                json.dump({"output": str(output), "worker_nonce": nonce,
                           "loader_sha256": hashlib.sha256(payload).hexdigest(),
                           "recovery_required": True}, stream, indent=2)
            report["one_shot_marker"] = str(marker.relative_to(PROJECT_ROOT))
            for path in ("/tmp/jmode", "/tmp/jbusy"):
                files.require_absent(path)
            report["camera_filesystem_mutations_possible"] = True
            files.exchange(2, b"\x00\x00", 0)
            extra = (RAM_PROBE_EXECUTABLE, RAM_PROBE_CONFIG, files.probe_ack, files.probe_metadata) if probe_enabled else ()
            for path in (*WORKSPACE_FILES, *extra):
                files.require_absent(path)
            report["absence_limit"] = "Stock stat-failure status, not atomic O_EXCL or an errno attestation"
            script = (probe_script(ram_dir, nonce, private_tmpfs=private_tmpfs) if probe_enabled
                      else ephemeral_chunk_worker_script(ram_dir, nonce=nonce))
            (output / "worker.sh").write_bytes(script)
            report["worker_ram_dir"] = ram_dir
            report["worker_script_sha256"] = hashlib.sha256(script).hexdigest()
            report["camera_file_writes"] = True
            files.write(WORKSPACE_FILES[0], script)
            if probe_enabled:
                config = probe_config(nonce, private_tmpfs=private_tmpfs, partition_dump=partition_dump)
                report["ram_probe_config_upload_attempted"] = True
                report["probe_config_hex"] = config.hex()
                files.write(RAM_PROBE_CONFIG, config)
                report["ram_probe_upload_attempted"] = True
                files.write(RAM_PROBE_EXECUTABLE, probe_elf)
            report["loader_upload_attempted"] = True
            report["owned_loader_cleanup_required"] = True
            report["power_removal_required"] = True
            files.exchange(7, b"\x00\x02\x00" + payload, 0)
            if files.read(WORKSPACE_FILES[1], len(payload)) != payload:
                raise ValueError("Loaded library readback differs from the authored payload")
            old_signal = signal.signal(signal.SIGINT, lambda *_: cancel.set())
            signal_installed = True
            if probe_enabled:
                worker = RamWorkspaceProbe(files, ram_dir, nonce, report, private_tmpfs=private_tmpfs)
                fixture_dir = PROJECT_ROOT / "artifacts" / "tests" / ("ram-probe-" + nonce)
                report["ram_probe_result"] = worker.run(output, fixture_dir, cancel=cancel)
                report["status"] = "ram_backing_and_repeated_transfers_verified_power_removal_required"
                print("Verified RAM backing and six exact known-pattern USB file reads.", flush=True)
                if partition_dump:
                    worker = NativeRamChunkWorker(worker)
                    _capture_requested_partitions(worker, args, output, report, cancel)
            else:
                worker = RamChunkWorker(files, ram_dir, nonce)
                _capture_requested_partitions(worker, args, output, report, cancel)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, protocol_error,
            KeyboardInterrupt) as error:
        report.update(status="blocked", error_type=type(error).__name__, error=str(error))
        if worker is not None and worker.initialization_failed:
            report["status"] = "worker_initialization_refused"
    finally:
        _finish_session(args, output, report, modules, device, driver, files, worker,
                        script, payload, probe_elf, owned, nonce if files is not None else "",
                        baseline if driver is not None else {}, protocol_error,
                        old_signal, signal_installed, cancel)
    print(json.dumps({key: report.get(key) for key in (
        "status", "error", "owned_loader_files_removed", "recovery_required",
    )}), flush=True)
    normal = report.get("metadata_after", {})
    return 0 if (report["status"] == "small_file_and_live_library_verified"
                 and report.get("exit_control_acknowledged") and report.get("host_handle_released")
                 and normal.get("pid") == senser.NORMAL_PID and normal.get("status") == "OK"
                 and normal.get("error") == 0) else 2


def _capture_requested_partitions(worker: RamChunkWorker, args, output: Path,
                                 report: dict, cancel: Event) -> None:
    (output / "worker-ready.txt").write_bytes(worker.wait_ready())
    inventory = worker.command(INVENTORY_COMMAND)
    (output / "inventory-before.txt").write_bytes(inventory)
    before_uptime, capacities = parse_inventory(inventory)
    report["live_capacities"] = capacities
    requested = args.partitions or [args.partition]
    if requested == ["all-allowed"]:
        preferred = ["/dev/nflashaB0", "/dev/nflashaB1", "/dev/nflasha1",
                     "/dev/nflasha7", "/dev/nflasha3", "/dev/nflasha15"]
        requested = [path for path in preferred if path in capacities]
        requested += sorted(set(capacities) - set(requested))
    if len(requested) != len(set(requested)):
        raise ValueError("Duplicate requested partitions")
    for path in requested:
        readable_storage(path)
        if path not in capacities:
            raise ValueError("Requested partition has no verified live capacity: " + path)
    for path in requested:
        print(f"Capturing {path}: {capacities[path]} bytes through {CHUNK_BYTES}-byte RAM chunks", flush=True)
        report["captures"].append(worker.capture(path, capacities[path], output, cancel=cancel))
        print(f"Verified {path}: {report['captures'][-1]['sha256']}", flush=True)
    after = worker.command(INVENTORY_COMMAND)
    (output / "inventory-after.txt").write_bytes(after)
    after_uptime, after_capacities = parse_inventory(after)
    if after_uptime < before_uptime or after_capacities != capacities:
        raise ValueError("Camera uptime regressed or live storage geometry changed")
    report["uptime_monotonic"] = True
    report["status"] = "requested_partitions_captured_power_removal_required"


def _finish_session(args, output, report, modules, device, driver, files, worker,
                script, payload, probe_elf, owned, nonce, baseline, protocol_error,
                old_signal, signal_installed, cancel) -> None:
    if worker is not None:
        report["last_capture"] = worker.capture_receipt
    if files is not None and files.in_sync and report.get("camera_file_writes"):
        try:
            probe = worker.probe if isinstance(worker, NativeRamChunkWorker) else worker
            if isinstance(probe, RamWorkspaceProbe) and not probe.failed:
                probe.cleanup()
            try:
                startup = files.read(WORKSPACE_FILES[2], 65536)
            except JiritsuStatusError as error:
                if error.status != 0x10000006:
                    raise
                report["startup_log_stat_failure"] = True
            else:
                (output / "worker-startup.log").write_bytes(startup)
                if not startup.startswith(f"__A6400A_WORKER_LOG__:{nonce}\n".encode("ascii")):
                    raise ValueError("Startup log has no owned worker nonce; refusing deletion")
                files.remove_owned(WORKSPACE_FILES[2], startup)
                report["owned_startup_log_removed"] = True
            if isinstance(worker, RamChunkWorker) and not isinstance(worker, NativeRamChunkWorker) and worker.ready and not worker.failed:
                worker.command(chunk_cleanup_command(worker.ram_dir))
            targets = [(WORKSPACE_FILES[0], script)]
            if report.get("ram_probe"):
                config_hex = report.get("probe_config_hex") or owned.get("probe_config_hex")
                if config_hex:
                    targets.insert(0, (RAM_PROBE_CONFIG, bytes.fromhex(config_hex)))
                if report.get("ram_probe_upload_attempted") or owned.get("ram_probe_upload_attempted"):
                    targets.insert(0, (RAM_PROBE_EXECUTABLE, probe_elf))
                ack_hex = report.get("probe_ack_hex") or owned.get("probe_ack_hex")
                if ack_hex:
                    targets.insert(0, (files.probe_ack, bytes.fromhex(ack_hex)))
                native_hex = report.get("probe_native_hex") or owned.get("probe_native_hex")
                if native_hex is not None:
                    targets.insert(0, (files.probe_metadata, bytes.fromhex(native_hex)))
                else:
                    try:
                        native = files.read(files.probe_metadata, REPORT_BYTES)
                    except JiritsuStatusError as error:
                        if error.status != 0x10000006:
                            raise
                        report["native_probe_stat_failure"] = True
                    else:
                        tag = b"A6400A_RAM_V2".ljust(16, b"\0")
                        if native[:16] != tag[:min(len(native), 16)]:
                            report.setdefault("cleanup_unverified_paths", []).append(files.probe_metadata)
                        else:
                            (output / "native-probe-cleanup-observation.bin").write_bytes(native)
                            report["probe_native_hex"] = native.hex()
                            report["native_probe_cleanup_bytes"] = len(native)
                            targets.insert(0, (files.probe_metadata, native))
            if report.get("owned_loader_cleanup_required"):
                targets.insert(0, (WORKSPACE_FILES[1], payload))
            for path, expected in targets:
                try:
                    files.read(path, len(expected))
                except JiritsuStatusError as error:
                    if error.status != 0x10000006:
                        raise
                    report.setdefault("owned_file_stat_failures", []).append(path)
                else:
                    files.remove_owned(path, expected)
            report["owned_loader_files_removed"] = True
            if args.mode == "cleanup-worker":
                report["status"] = "owned_worker_files_removed_power_removal_required"
        except (OSError, ValueError, RuntimeError, protocol_error) as error:
            report["cleanup_error"] = type(error).__name__ + ": " + str(error)
            if report["status"] == "ram_backing_and_repeated_transfers_verified_power_removal_required":
                report["status"] = "ram_probe_cleanup_failed"
    safe_exit = files is None or (files.in_sync and (worker is None or not worker.failed)
                                 and (not isinstance(worker, NativeRamChunkWorker) or not worker.probe.failed))
    if driver is not None and report.get("start_control_attempted") and safe_exit:
        try:
            driver.begin("control")
            senser.control_request(driver, senser.STOP, protocol_error, modules["core"].USBError,
                                   args.permit_control_pipe, report)
            report["exit_control_attempted"] = True
            report["exit_control_acknowledged"] = not any(
                row["operation"] == "stop" for row in report.get("unacknowledged_controls", []))
        except (OSError, ValueError, RuntimeError, protocol_error) as error:
            report["exit_error"] = type(error).__name__ + ": " + str(error)
    elif driver is not None:
        report["exit_deferred"] = "Unresolved transfer/worker; no invented abort; owner recovery required"
    if device is not None and modules is not None:
        try:
            modules["util"].dispose_resources(device)
            report["host_handle_released"] = True
        except (OSError, ValueError, RuntimeError, protocol_error) as error:
            report["host_release_error"] = type(error).__name__ + ": " + str(error)
    if report.get("exit_control_attempted"):
        try:
            report["metadata_after"] = senser.wait_mode((senser.NORMAL_PID,), baseline["locations"])
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            report["postflight_error"] = type(error).__name__ + ": " + str(error)
    if signal_installed:
        signal.signal(signal.SIGINT, old_signal)
    report["cancel_requested"] = cancel.is_set()
    report["recovery_required"] = bool(report.get("start_control_attempted"))
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    with (output / "receipt.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    if report.get("camera_file_writes"):
        write_ownership_receipt(output, report)


def write_ownership_receipt(output: Path, report: dict) -> Path:
    """Keep cleanup metadata bounded without truncating the full wire receipt."""
    fields = (
        "identity", "camera_file_writes", "loader_upload_attempted", "loader_qualification",
        "worker_script_sha256", "worker_ram_dir", "ram_probe", "ram_private_tmpfs",
        "ram_probe_elf_sha256", "ram_probe_upload_attempted", "probe_ack_hex", "probe_native_hex",
        "ram_partition_dump", "ram_probe_config_upload_attempted", "probe_config_hex",
    )
    owned = {field: report[field] for field in fields if field in report}
    owned.update(status=report["status"], source_receipt="receipt.json",
                 source_receipt_sha256=senser.host_digest(output / "receipt.json"))
    raw = json.dumps(owned, indent=2).encode("utf-8")
    if len(raw) > 65536:
        raise ValueError("Cleanup ownership metadata exceeds its 64-KiB admission bound")
    destination = output / "ownership.json"
    with destination.open("xb") as stream:
        stream.write(raw)
    return destination
