"""Bounded, opt-in Senser acquisition for the owner-confirmed ILCE-6400A.

Driver installation is external. No updater, shell, file write or memory scan
is sent. A service read attempt always attempts the explicit Senser exit.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import io
import json
import math
from pathlib import Path
import re
import struct
import subprocess
import sys
import time

from project_paths import PROJECT_ROOT
from tools.camera_debugger import host_lock

NORMAL_PID = 0x0F0F
SERVICE_PIDS = (0x02A9, 0x0336)
START = (1, 0x37FF, 0xD7AA)
STOP = (1, 0xC800, 0x2855)
PMCA_COMMIT = "a82f5baaa8e9c3d9f28f94699e860fb2e48cc8e0"
PACKET = struct.Struct("<IHHBBBB")
AUTH = struct.Struct(">HH512s")
PARTITION = re.compile(r"nflasha(?:[0-9]+|B[01])\Z")
MAX_PARTITION_BYTES = 512 * 1024 * 1024

DISCOVER = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$devices=@(Get-CimInstance Win32_PnPEntity |
  Where-Object { $_.PNPDeviceID -like 'USB\VID_054C*' -and $_.Present -ne $false })
if ($devices.Count -ne 1) { throw 'Expected exactly one connected Sony USB device.' }
$device=$devices[0]
$pair=[regex]::Match($device.PNPDeviceID,'VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})')
if (-not $pair.Success) { throw 'Sony USB identity could not be parsed.' }
$location=Get-PnpDeviceProperty -InstanceId $device.PNPDeviceID -KeyName DEVPKEY_Device_LocationPaths
$signed=@(Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.DeviceID -eq $device.PNPDeviceID })
[ordered]@{
  vid=[Convert]::ToInt32($pair.Groups[1].Value,16)
  pid=[Convert]::ToInt32($pair.Groups[2].Value,16)
  service=$device.Service; status=$device.Status
  error=[int]$device.ConfigManagerErrorCode
  locations=@($location.Data); serial_retained=$false
  drivers=@($signed | Select-Object InfName,DriverProviderName,DriverVersion,IsSigned)
} | ConvertTo-Json -Depth 5 -Compress
"""


def discover() -> dict:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", DISCOVER],
        capture_output=True, timeout=45, check=False,
    )
    if result.returncode:
        raise RuntimeError("Sony USB discovery failed; no device opened")
    return json.loads(result.stdout.decode("utf-8-sig"))


def admit_device(metadata: dict, baseline: dict, action: str) -> None:
    expected = (NORMAL_PID,) if action == "enter" else SERVICE_PIDS
    if (metadata.get("vid") != 0x054C or metadata.get("pid") not in expected
            or metadata.get("status") != "OK" or metadata.get("error") != 0
            or metadata.get("service", "").lower() not in ("winusb", "libusb0", "libusbk")):
        raise ValueError("Camera mode/driver/status admission failed")
    locations = metadata.get("locations")
    if not locations or locations != baseline.get("locations"):
        raise ValueError("Camera physical USB location changed")
    if (baseline.get("vid"), baseline.get("pid"), baseline.get("service")) != (
            0x054C, NORMAL_PID, "USBSTOR"):
        raise ValueError("Baseline is not the original Sony Mass Storage binding")


class BoundedDriver:
    def __init__(self, driver, events: list):
        self.driver = driver
        self.events = events
        self.phase = "control"
        self.path = None
        self.remaining = 8192
        self.deadline = time.monotonic() + 30

    def begin(self, phase: str, limit: int = 8192, path: str | None = None):
        self.phase, self.path = phase, path
        self.remaining = limit
        self.deadline = time.monotonic() + 120

    def check(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("Senser operation deadline reached; no retry")

    def getId(self):
        return self.driver.getId()

    def vendorRequestOut(self, request, value, index, data=b""):
        self.check()
        if (request, value, index) not in (START, STOP) or data:
            raise ValueError("Vendor control request outside start/stop allowlist")
        self.events.append({"operation": "start" if (request, value, index) == START else "stop"})
        return self.driver.vendorRequestOut(request, value, index, data)

    def read(self, length: int, ep=0, timeout=None):
        self.check()
        if ep != 0 or not 0 < length <= 0x8000 or length > self.remaining:
            raise ValueError("USB read outside declared operation budget")
        raw = self.driver.read(length, ep=ep, timeout=timeout)
        if not raw or len(raw) > length:
            raise RuntimeError("Empty or oversized USB read; no retry")
        self.remaining -= len(raw)
        self.events.append({"operation": "read", "phase": self.phase, "bytes": len(raw)})
        return raw

    def write(self, data: bytes, ep=0):
        self.check()
        if ep != 0:
            raise ValueError("Terminal writes are not permitted")
        if self.phase == "auth":
            if len(data) != 516 or (~struct.unpack_from(">H", data)[0] & 0xFFFF) not in (1, 3, 5):
                raise ValueError("Authentication packet outside declared protocol")
        elif self.phase == "file":
            if len(data) < PACKET.size + 4:
                raise ValueError("Short file-control request")
            size, function, sequence, version, micon, offset, response = PACKET.unpack_from(data)
            command, name_size = struct.unpack_from("<HH", data, PACKET.size)
            body = data[PACKET.size:]
            name = body[4:].rstrip(b"\0").decode("latin1")
            if (function != 0xFF01 or command != 2 or name != self.path
                    or size != len(body) or name_size != len(body) - 4
                    or not sequence or any((version, micon, offset, response))):
                raise ValueError("Only the declared Senser file read is permitted")
        else:
            raise ValueError("No bulk write is permitted in this phase")
        self.events.append({"operation": "write", "phase": self.phase,
                            "bytes": len(data), "credential_bytes_retained": False})
        return self.driver.write(data, ep=ep)


class BoundedSink:
    def __init__(self, limit: int, stream=None):
        self.limit = limit
        self.stream = stream if stream is not None else io.BytesIO()
        self.size = 0
        self.sha = hashlib.sha256()

    def write(self, data: bytes):
        if self.size + len(data) > self.limit:
            raise ValueError("Senser output exceeds declared size; partial data preserved")
        self.stream.write(data)
        self.sha.update(data)
        self.size += len(data)


def authenticate(driver: BoundedDriver, constants, crypto) -> None:
    def exchange(command: int, payload: bytes = b""):
        driver.write(AUTH.pack(~command & 0xFFFF, 0, payload))
        raw = driver.read(AUTH.size)
        if len(raw) != AUTH.size:
            raise RuntimeError("Short Senser authentication response")
        encoded, salt, data = AUTH.unpack(raw)
        result = (~encoded & 0xFFFF) - salt
        driver.events.append({"operation": "authentication_result", "command": command,
                              "result": result, "credential_bytes_retained": False})
        return result, data

    for index in range(3):
        result, challenge = exchange(1)
        if result not in (2, 8):
            raise RuntimeError("Unexpected Senser authentication challenge status")
        keys = constants.senserKeysSha1 if result == 2 else constants.senserKeysSha256
        material = challenge + keys[index] if driver.getId()[1] == 0x0336 else challenge[:4]
        digest = crypto.sha1_faulty(material) if result == 2 else hashlib.sha256(material).digest()
        result, _ = exchange(3, b"\x01" + digest)
        if result != 4:
            raise RuntimeError("Senser authentication round was not accepted")
    result, data = exchange(5)
    if result != 6 or data[0] != 1:
        raise RuntimeError("Senser authentication did not complete")


def control_request(driver: BoundedDriver, request: tuple[int, int, int],
                    generic_error, usb_error, permit_pipe: bool, report: dict) -> None:
    try:
        driver.vendorRequestOut(*request)
    except generic_error as error:
        cause = error.__context__
        if not permit_pipe or not isinstance(cause, usb_error) or cause.backend_error_code != -9:
            raise
        report.setdefault("unacknowledged_controls", []).append({
            "operation": "start" if request == START else "stop",
            "error": str(cause), "backend_error_code": cause.backend_error_code,
            "policy": "explicit original-PMCA control-pipe allowance; not control success",
        })


class ReadOnlySenser:
    def __init__(self, driver: BoundedDriver):
        self.driver = driver
        self.sequence = 1

    def read_exact(self, size: int) -> bytes:
        raw = bytearray()
        while len(raw) < size:
            raw.extend(self.driver.read(size - len(raw)))
        return bytes(raw)

    def readFile(self, path: str, sink: BoundedSink) -> None:
        name = path.encode("latin1")
        name += bytes(4 - len(name) % 4)
        body = struct.pack("<HH", 2, len(name)) + name
        self.driver.write(PACKET.pack(len(body), 0xFF01, self.sequence, 0, 0, 0, 0) + body)
        minimum = 0 if self.driver.getId()[1] == 0x0336 else 512
        while True:
            raw = self.driver.read(max(PACKET.size, minimum))
            if len(raw) < PACKET.size:
                raise RuntimeError("Short Senser reply header")
            size, function, sequence, version, micon, offset, status = PACKET.unpack_from(raw)
            header = {"size": size, "function": function, "sequence": sequence,
                      "version": version, "micon": micon, "offset": offset, "status": status}
            self.driver.events.append({"operation": "file_reply_header", "header": header})
            if function != 0xFF01 or sequence != self.sequence or any((version, micon, offset)):
                raise RuntimeError("Senser reply does not match the declared file read: " + str(header))
            length = min(size, 0x100000)
            initial = raw[PACKET.size:]
            if len(initial) > length:
                raise RuntimeError("Senser reply contains undeclared payload padding")
            sink.write(initial)
            done = len(initial)
            chunk = 0x8000 - minimum
            while done < length:
                amount = min(length - done, chunk)
                sink.write(self.read_exact(amount))
                done += amount
                if not amount & 511:
                    self.driver.read(512)
                chunk = 0x8000
            if status != 1:
                raise RuntimeError(f"Senser file read refused with status {status}")
            if size <= 0x100000:
                break
        self.sequence += 1


def partition_names(raw: bytes) -> list[str]:
    names = []
    for line in raw.decode("ascii").splitlines():
        fields = line.split()
        if len(fields) == 4 and PARTITION.fullmatch(fields[3]):
            if not all(value.isdigit() for value in fields[:3]) or fields[3] in names:
                raise ValueError("Malformed or duplicate partition inventory")
            names.append(fields[3])
    if not names:
        raise ValueError("No nflasha partition nodes observed")
    return names


def uptime(raw: bytes) -> float:
    value = float(raw.decode("ascii").split()[0])
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid camera uptime")
    return value


def read_small(camera, driver: BoundedDriver, path: str, limit=16384) -> bytes:
    sink = BoundedSink(limit)
    driver.begin("file", limit * 2 + 4096, path)
    camera.readFile(path, sink)
    return sink.stream.getvalue()


def load_pmca():
    checkout = PROJECT_ROOT / "Sony-PMCA-RE"
    result = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                            capture_output=True, check=True, timeout=15)
    if result.stdout.decode("ascii").strip() != PMCA_COMMIT:
        raise ValueError("PMCA revision differs from reviewed input")
    sys.path.insert(0, str(checkout))
    package = importlib.import_module("libusb_package")
    backend = package.get_libusb1_backend()
    if backend is None:
        raise RuntimeError("Bundled Windows libusb backend unavailable")
    return {"core": importlib.import_module("usb.core"),
            "util": importlib.import_module("usb.util"),
            "generic": importlib.import_module("pmca.usb.driver.generic"),
            "base": importlib.import_module("pmca.usb"),
            "library": importlib.import_module("pmca.usb.driver.generic.libusb"),
            "constants": importlib.import_module("pmca.usb.constants"),
            "crypto": importlib.import_module("pmca.usb.crypto"), "backend": backend}


def connect(metadata: dict, modules: dict):
    core = modules["core"]
    devices = list(core.find(find_all=True, idVendor=0x054C, backend=modules["backend"]))
    if len(devices) != 1 or devices[0].idProduct != metadata["pid"]:
        raise ValueError("USB device enumeration differs from admitted camera")
    device = devices[0]
    interfaces = list(device.get_active_configuration())
    if len(interfaces) != 1 or interfaces[0].bInterfaceNumber != 0:
        raise ValueError("Unexpected camera USB interface layout")
    expected_class = 8 if metadata["pid"] == NORMAL_PID else 0xFF
    if interfaces[0].bInterfaceClass != expected_class:
        raise ValueError("Unexpected camera USB interface class")
    directions = {endpoint.bEndpointAddress & 0x80 for endpoint in interfaces[0]
                  if endpoint.bmAttributes & 3 == 2}
    if directions != {0, 0x80}:
        raise ValueError("Camera bulk endpoints are incomplete")
    cls = modules["generic"].MscBbbDriver if expected_class == 8 else modules["generic"].GenericUsbDriver
    return device, cls(modules["library"].UsbBackend(device))


def host_digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def wait_mode(expected: tuple[int, ...], locations: list, seconds=15) -> dict:
    end = time.monotonic() + seconds
    last_error = None
    while time.monotonic() < end:
        try:
            metadata = discover()
        except RuntimeError as error:
            last_error = str(error)
        else:
            if metadata["pid"] in expected and metadata["locations"] == locations:
                return metadata
        time.sleep(0.5)
    raise TimeoutError("Expected camera mode did not re-enumerate: " + str(last_error))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("inspect", "enter", "probe", "read", "leave"), default="inspect")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--permit-control-pipe", action="store_true",
                        help="Allow only the original PMCA start/stop STALL behavior; real auth/mode proof remains required")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    out = args.output_dir.resolve()
    if not out.is_relative_to(PROJECT_ROOT / "artifacts"):
        parser.error("Receipts must remain under artifacts")
    out.mkdir(parents=True, exist_ok=False)
    report = {"started_utc": datetime.now(timezone.utc).isoformat(), "action": args.action,
              "status": "in_progress", "events": [], "captures": [], "camera_file_writes": False,
              "shell_commands": False, "memory_scans": False, "updater_operations": False,
              "source_file_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "python_version": sys.version}
    modules = None
    device = None
    driver = None
    protocol_error = RuntimeError
    try:
        with host_lock():
            report["metadata_before"] = metadata = discover()
            if args.action == "inspect":
                if (metadata["pid"], metadata["service"], metadata["status"], metadata["error"]) != (
                        NORMAL_PID, "USBSTOR", "OK", 0):
                    raise ValueError("Original healthy Mass Storage baseline not observed")
                report["status"] = "baseline_observed"
            else:
                if args.baseline is None:
                    raise ValueError("A preserved original-driver baseline is required")
                baseline = json.loads(args.baseline.read_text(encoding="utf-8"))["metadata_before"]
                admit_device(metadata, baseline, args.action)
                modules = load_pmca()
                protocol_error = modules["generic"].GenericUsbException
                device, raw_driver = connect(metadata, modules)
                driver = BoundedDriver(raw_driver, report["events"])
                if args.action == "enter":
                    info = modules["base"].MscDevice(raw_driver).getDeviceInfo()
                    if info.manufacturer != "Sony" or info.model != "DSC":
                        raise ValueError("Generic Mass Storage inquiry does not identify the camera")
                    report["msc_preflight"] = {"vendor": info.manufacturer, "model": info.model}
                if args.action != "leave":
                    control_request(driver, START, protocol_error, modules["core"].USBError,
                                    args.permit_control_pipe, report)
                    driver.begin("auth")
                    authenticate(driver, modules["constants"], modules["crypto"])
                    if args.action in ("probe", "read"):
                        camera = ReadOnlySenser(driver)
                        version = read_small(camera, driver, "/proc/version", 4096)
                        if not version.startswith(b"Linux version "):
                            raise ValueError("No valid camera /proc/version response")
                        (out / "proc-version.txt").write_bytes(version)
                        report["file_probe"] = {"path": "/proc/version", "bytes": len(version),
                                                "sha256": hashlib.sha256(version).hexdigest()}
                    if args.action == "read":
                        before_uptime = uptime(read_small(camera, driver, "/proc/uptime", 4096))
                        inventory = read_small(camera, driver, "/proc/partitions")
                        (out / "proc-partitions.txt").write_bytes(inventory)
                        names = partition_names(inventory)
                        for name in names:
                            sectors = int(read_small(camera, driver, f"/sys/class/block/{name}/size", 4096))
                            length = sectors * 512
                            if not 0 < length <= MAX_PARTITION_BYTES:
                                raise ValueError("Partition size outside bounded acquisition profile")
                            partial = out / (name + ".bin.partial")
                            with partial.open("xb") as stream:
                                sink = BoundedSink(length, stream)
                                driver.begin("file", length + length // 16 + 8192, "/dev/" + name)
                                camera.readFile("/dev/" + name, sink)
                                stream.flush()
                            if sink.size != length:
                                raise ValueError("Incomplete partition capture: " + name)
                            digest = host_digest(partial)
                            if digest != sink.sha.hexdigest():
                                raise ValueError("Host capture hash differs from streaming hash")
                            final = out / (name + ".bin")
                            partial.rename(final)
                            current = uptime(read_small(camera, driver, "/proc/uptime", 4096))
                            if current < before_uptime:
                                raise ValueError("Camera uptime regressed during acquisition")
                            before_uptime = current
                            report["captures"].append({"device": "/dev/" + name, "bytes": length,
                                                       "sha256": digest, "host_hash_verified": True})
                if args.action != "enter":
                    driver.begin("control")
                    control_request(driver, STOP, protocol_error, modules["core"].USBError,
                                    args.permit_control_pipe, report)
                    report["exit_control_sent"] = True
                modules["util"].dispose_resources(device)
                device = None
                expected = SERVICE_PIDS if args.action == "enter" else (NORMAL_PID,)
                report["metadata_after"] = wait_mode(expected, baseline["locations"])
                report["status"] = "service_entered" if args.action == "enter" else (
                    "file_read_verified" if args.action == "probe" else (
                        "captures_complete" if report["captures"] else "normal_mode_restored"))
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError,
            protocol_error) as error:
        report.update(status="blocked", error_type=type(error).__name__, error=str(error))
        if error.__context__ is not None:
            report["underlying_error"] = type(error.__context__).__name__ + ": " + str(error.__context__)
    finally:
        cleanup_required = args.action in ("probe", "read") or (
            args.action == "enter" and report["status"] == "blocked")
        if driver is not None and cleanup_required and not report.get("exit_control_sent"):
            try:
                driver.begin("control")
                control_request(driver, STOP, protocol_error, modules["core"].USBError,
                                args.permit_control_pipe, report)
                report["exit_control_sent"] = True
            except (OSError, ValueError, RuntimeError, protocol_error) as error:
                report["exit_error"] = type(error).__name__ + ": " + str(error)
        if device is not None and modules is not None:
            modules["util"].dispose_resources(device)
        if cleanup_required:
            try:
                if "metadata_after" in report:
                    report["metadata_final"] = report["metadata_after"]
                elif driver is not None and report.get("exit_control_sent"):
                    report["metadata_final"] = wait_mode((NORMAL_PID,), report["metadata_before"]["locations"])
                else:
                    report["metadata_final"] = discover()
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                report["metadata_final_error"] = str(error)
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        (out / "receipt.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] in ("baseline_observed", "service_entered", "file_read_verified",
                                    "captures_complete", "normal_mode_restored") else 2


if __name__ == "__main__":
    raise SystemExit(main())
