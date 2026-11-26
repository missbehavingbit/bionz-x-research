"""Offline a6400A read consumers: scratch-buffer and file-size contracts, not camera access."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct

from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP

from project_paths import PROJECT_ROOT
from tools.research.a6400a_service_contracts import (
    DATA, HASHES, IMAGE, PACKAGE, STACK, SYSTEM, Library, ThumbWindow,
    adjustment_gate, adjustment_window,
)

JIRITSU_SHA = "c3621c7ec30771604b1aaae25e51699340333486e1aa281a1cc5f4a543a99dff"
CLASSIFIER = (0x11F44, 0x11F9A)
SCRATCH, RESPONSE = IMAGE + 0x5B048, IMAGE + 0x5B148
EXECUTE = "_ZN29SubsystemCommunicationCommand9DoExecuteEP12SenserPacket"
MEMORY_CTOR = "_ZN10MemoryDataC1EPFiPhjEPvjPFvS3_E"


def cstring(window: ThumbWindow, address: int, maximum: int = 4096) -> bytes:
    result = bytearray()
    for offset in range(maximum):
        byte = window.cpu.mem_read(address + offset, 1)[0]
        if not byte:
            return bytes(result)
        result.append(byte)
    raise ValueError("Fixture string lacks a terminator inside its declared bound")


def byte_fixtures(window: ThumbWindow) -> None:
    def memset() -> None:
        destination, value, count, _ = window.arguments()
        if (count > 8192 or not (DATA <= destination <= destination + count <= DATA + 0x10000
                                or STACK <= destination <= destination + count <= STACK + 0x10000)):
            raise ValueError("Byte-fill fixture exceeds declared data/stack bounds")
        window.cpu.mem_write(destination, bytes([value & 255]) * count)
        window.result(destination)

    def memcpy() -> None:
        destination, source, count, _ = window.arguments()
        if count > 8192:
            raise ValueError("Byte-copy fixture exceeds declared transfer bound")
        for address, permissions in ((source, 1), (destination, 2)):
            if not any(start <= address <= address + count <= end + 1 and flags & permissions
                       for start, end, flags in window.cpu.mem_regions()):
                raise ValueError("Byte-copy fixture crosses an undeclared or protected mapping")
        value = bytes(window.cpu.mem_read(source, count)) if count else b""
        window.cpu.mem_write(destination, value)
        window.result(destination)

    window.fixture("memset", memset)
    window.fixture("memcpy", memcpy)


def scratch_read(core: Library, offset: int, length: int, *, level: int = 1) -> dict:
    if not 0 <= offset <= 0xFFFFFFFF or not 0 <= length <= 0xFFFFFFFF:
        raise ValueError("Scratch read fixture requires unsigned ARM32 arguments")
    window, reads = adjustment_window(core, 0x601, 0xFE, level, struct.pack("<II", offset, length))
    window.allowed.append((IMAGE + CLASSIFIER[0], IMAGE + CLASSIFIER[1]))
    byte_fixtures(window)
    window.fixture("_Z10cpp_logrechPKcjjj", lambda: window.result(0))
    source = bytes((index * 37 + 11) & 255 for index in range(256))
    window.cpu.mem_write(SCRATCH, source)
    window.cpu.mem_write(RESPONSE, bytes(0x140))
    transaction = window.run(_transaction_arguments())
    if sum(reads) != 8:
        raise RuntimeError("Original scratch request did not consume its exact payload")
    status = window.cpu.mem_read(DATA + 0x159, 1)[0]
    accepted = offset < 256 and length <= 256 and offset + length <= 256
    if (transaction["arguments"][0], status) != ((0, 1) if accepted else (0x81, 0x81)):
        raise RuntimeError("Original scratch result differs from the measured byte-window contract")
    count = window.read32(RESPONSE + 12)
    payload = bytes(window.cpu.mem_read(RESPONSE + 16, count)) if accepted else b""
    if accepted and (count != length or payload != source[offset:offset + length]):
        raise RuntimeError("Original scratch copy does not match the declared source bytes")
    if bytes(window.cpu.mem_read(SCRATCH, 256)) != source:
        raise RuntimeError("A scratch read changed its declared source")
    result = {"offset": offset, "length": length, "level_fixture": level,
              "status": status, "accepted": accepted, "payload_bytes": len(payload),
              "payload_sha256": hashlib.sha256(payload).hexdigest(),
              "scratch_source_unchanged": True, "transaction": transaction}
    if accepted:
        window.allow(EXECUTE)
        sender = window.globals("_Z12SenserIfSendPhj")["_Z12SenserIfSendPhj"]

        def allocate() -> None:
            if window.arguments()[0] != 24:
                raise ValueError("Unexpected original response-object allocation")
            window.result(DATA + 0x6000)

        window.fixture("_Znwj", allocate)
        response = window.run(
            {UC_ARM_REG_R0: DATA, UC_ARM_REG_R1: DATA + 0x4000},
            start=IMAGE + core.function(EXECUTE)[0], stop_before=window.plt_address(MEMORY_CTOR),
        )
        if window.arguments() != [DATA + 0x6000, sender, RESPONSE + 16, length]:
            raise RuntimeError("Original response does not use the bounded scratch-result buffer")
        result["response_object_boundary"] = response
    result["scope"] = "Original transaction and response preparation; explicit scratch/byte-copy/allocation fixtures; no IPC, device or USB"
    return result


def _transaction_arguments() -> dict[int, int]:
    return {UC_ARM_REG_R0: DATA, UC_ARM_REG_R1: DATA + 0x4000,
            UC_ARM_REG_R2: 0, UC_ARM_REG_R3: DATA + 0x5000}


def routed_read(core: Library, level: int) -> dict:
    window, reads = adjustment_window(core, 0x304, 0xFE, level, struct.pack("<II", 0, 4))
    window.allowed.append((IMAGE + CLASSIFIER[0], IMAGE + CLASSIFIER[1]))
    result = window.run(_transaction_arguments(), stop_before=window.plt_address("osal_valloc_msg_wait"))
    if sum(reads) != 8 or window.arguments()[0] != 0x570254:
        raise RuntimeError("Original subsystem RAM_READ route differs from its declared queue")
    result.update(level_fixture=level, queue=window.arguments()[0],
                  scope="Before OSAL allocation/IPC; no subsystem reply or memory address is supplied")
    return result


def block_capacity(core: Library, sectors: int, *, ioctl_result: int = 0) -> dict:
    if not 0 <= sectors <= 0xFFFFFFFF:
        raise ValueError("Block-capacity fixture requires unsigned ARM32 sectors")
    window = ThumbWindow(core, "_ZNK8DeviceIo4sizeEv")
    operations = []

    def stat() -> None:
        _, destination, _, _ = window.arguments()
        window.cpu.mem_write(destination, bytes(96))
        window.write32(destination + 16, 0x6000)
        window.result(0)

    def ioctl() -> None:
        _, request, destination, _ = window.arguments()
        if request != 0x1260 or not STACK <= destination < STACK + 0x10000 - 8:
            raise ValueError("Original capacity query differs from BLKGETSIZE")
        window.write32(destination, sectors)
        operations.append({"ioctl": request, "sectors_fixture": sectors,
                           "result_fixture": ioctl_result, "real_operation": False})
        window.result(ioctl_result)

    window.fixture("_ZNK8DeviceIo5fstatER4stat", stat)
    window.fixture("_ZNK8DeviceIo5ioctlEiPKv", ioctl)
    result = window.run({UC_ARM_REG_R0: DATA})
    expected = (sectors << 9) & 0xFFFFFFFF if ioctl_result == 0 else 0
    if window.arguments()[0] != expected:
        raise RuntimeError("Original capacity calculation differs from sector-to-byte conversion")
    return {"sectors_fixture": sectors, "bytes": expected, "operations": operations,
            "original_execution": result,
            "scope": "Original DeviceIo::size with constructed fstat/BLKGETSIZE results; no real ioctl"}


def nflasha5_window(core: Library, *, busy: bool = False,
                   open_result: int = 7, lock_result: int = 0,
                   map_result: int = DATA + 0x8000) -> tuple[ThumbWindow, list[dict]]:
    capacity = 62_914_560
    fd = IMAGE + 0x5AE8C
    window = ThumbWindow(core, EXECUTE)
    window.allowed.extend((IMAGE + start, IMAGE + end) for start, end in
                          ((0x150EC, 0x15ADE), (0x13F08, 0x13F26), (0x13F26, 0x13F40)))
    byte_fixtures(window)
    window.write32(fd, 42 if busy else 0xFFFFFFFF)
    operations = []

    def log() -> None:
        window.result(0)

    window.fixture("_Z10cpp_logrechPKcjj", log)
    window.fixtures[IMAGE + 0x13E20] = ("diagnostic logging fixture; no file output", log)
    window.fixtures[IMAGE + 0x13D7A] = ("diagnostic logging fixture; no file output", log)
    window.fixture("__errno_location", lambda: window.result(DATA + 0x7000))

    def size_query() -> None:
        command, directory, source, _ = window.arguments()
        if command != 6 or cstring(window, directory) != b"/dev" or cstring(
                window, source) != b"/dev/nflasha5":
            raise ValueError("Original fixed-partition size request changed")
        operations.append({"operation": "capacity_fixture", "path": "/dev/nflasha5",
                           "bytes": capacity, "real_operation": False})
        window.result(capacity)

    window.fixtures[IMAGE + 0x14110] = ("declared block-capacity result; no filesystem", size_query)

    def open_device() -> None:
        source, flags, _, _ = window.arguments()
        if cstring(window, source) != b"/dev/nflasha5" or flags != 0x1000:
            raise ValueError("Original fixed-partition open is not O_RDONLY|O_LARGEFILE")
        operations.append({"operation": "open_fixture", "flags": flags, "real_operation": False})
        window.result(open_result)

    def lock() -> None:
        descriptor, flags, _, _ = window.arguments()
        if descriptor != open_result or flags not in (6, 12):
            raise ValueError("Unexpected original nonblocking lock/unlock request")
        operations.append({"operation": "flock_fixture", "flags": flags, "real_operation": False})
        window.result(lock_result if flags == 6 else 0)

    def divide() -> None:
        dividend, divisor, _, _ = window.arguments()
        if divisor != 4096:
            raise ValueError("Unexpected original page-size division")
        window.result(dividend // divisor)

    def mmap() -> None:
        address, size, protection, flags = window.arguments()
        stack = window.cpu.reg_read(UC_ARM_REG_SP)
        descriptor, offset = struct.unpack("<II", bytes(window.cpu.mem_read(stack, 8)))
        if (address, size, protection, flags, descriptor, offset) != (
                0, capacity + 4096, 1, 1, open_result, 0):
            raise ValueError("Original fixed-partition mapping differs from the read-only profile")
        operations.append({"operation": "mmap_fixture", "bytes": size, "protection": protection,
                           "flags": flags, "real_operation": False})
        window.result(map_result)

    def munmap() -> None:
        address, size, _, _ = window.arguments()
        if (address, size) != (map_result, capacity + 4096):
            raise ValueError("Original release does not match the owned mapping")
        operations.append({"operation": "munmap_fixture", "real_operation": False})
        window.result(0)

    def close() -> None:
        if window.arguments()[0] != open_result:
            raise ValueError("Original release does not close its own descriptor")
        operations.append({"operation": "close_fixture", "real_operation": False})
        window.result(0)

    window.fixture("open", open_device)
    window.fixture("flock", lock)
    window.fixture("getpagesize", lambda: window.result(4096))
    window.fixture("__aeabi_uidiv", divide)
    window.fixture("mmap", mmap)
    window.fixture("munmap", munmap)
    window.fixture("close", close)
    return window, operations


def nflasha5_lifecycle(core: Library, *, busy: bool = False,
                      open_result: int = 7, lock_result: int = 0,
                      map_result: int = DATA + 0x8000) -> dict:
    capacity = 62_914_560
    fd, mapping_size, mapping = (IMAGE + value for value in (0x5AE8C, 0x5B294, 0x5B298))
    window, operations = nflasha5_window(core, busy=busy, open_result=open_result,
                                       lock_result=lock_result, map_result=map_result)
    window.cpu.mem_write(DATA, b"\x01" + bytes(0x13F))
    window.cpu.mem_write(DATA + 24, struct.pack("<HH", 0x601, 0x32))
    start = window.run({UC_ARM_REG_R0: DATA}, start=IMAGE + 0x150EC)
    response_mode, status, _, transfer, _, _, _, address, length = struct.unpack(
        "<BBHBBBBII", bytes(window.cpu.mem_read(DATA, 16)))
    success = not busy and open_result >= 0 and lock_result >= 0 and map_result >= 0
    release = None
    if success:
        if (response_mode, status, transfer, address, length) != (8, 1, 2, map_result, capacity):
            raise RuntimeError("Original fixed-partition callback produced an unexpected response")
        window.cpu.mem_write(DATA, b"\x10")
        release = window.run({UC_ARM_REG_R0: DATA}, start=IMAGE + 0x150EC)
        if (window.read32(fd), window.read32(mapping_size), window.read32(mapping)) != (0xFFFFFFFF, 0, 0):
            raise RuntimeError("Original explicit release did not reset its owned resources")
    elif status != (0xA0 if busy or lock_result < 0 else 0x88):
        raise RuntimeError("Original fixed-partition failure status differs from the measured profile")
    if busy and operations:
        raise RuntimeError("Busy callback unexpectedly touched another session's resources")
    return {"busy_fixture": busy, "open_result_fixture": open_result,
            "lock_result_fixture": lock_result, "map_result_fixture": map_result,
            "response_mode": response_mode, "status": status, "response_bytes": length,
            "descriptor_after": window.read32(fd), "mapping_pointer_after": window.read32(mapping),
            "mapping_size_after": window.read32(mapping_size),
            "operations": operations, "callback_execution": start, "release_execution": release,
            "scope": "Original fixed-node callback and explicit release; capacity/libc fixtures, no real open, mmap, IPC, transfer or storage access"}


def jiritsu_read(library: Library, *, mode: int = 1, size: int = 12,
                 read_result: int | None = None, mount_result: int = -1,
                 open_result: int = 7) -> dict:
    if mode not in (0, 1) or not 0 <= size <= 4096 or (
            read_result is not None and not 0 <= read_result <= size):
        raise ValueError("File-read fixture exceeds the declared mode/4-KiB profile")
    window = ThumbWindow(library, "adj_cmd_get_file")
    byte_fixtures(window)
    request, manager, output, total, state = DATA, DATA + 0x1000, DATA + 0x2000, DATA + 0x4000, DATA + 0x5000
    path = b"/fixture.bin"
    window.cpu.mem_write(request, bytes(0x140))
    window.write32(request + 0x14, 3 + len(path) + 1)
    window.cpu.mem_write(request + 0x1C, bytes([mode, 0, 0]) + path + b"\0")
    window.write32(manager, state)
    window.write32(state + 4, output)
    window.allowed.append((IMAGE + 0x39D8, IMAGE + 0x39E2))
    operations: list[dict] = []
    payload = bytes((index * 17 + 3) & 255 for index in range(size))

    def string_length() -> None:
        window.result(len(cstring(window, window.arguments()[0])))

    def concatenate() -> None:
        destination, source, limit, _ = window.arguments()
        value = cstring(window, destination) + cstring(window, source)[:limit]
        if len(value) >= 4097:
            raise ValueError("Path fixture exceeds original local buffer")
        window.cpu.mem_write(destination, value + b"\0")
        window.result(destination)

    def mount() -> None:
        operations.append({"operation": "mount_boundary_fixture", "result": mount_result,
                           "real_operation": False})
        window.result(mount_result)

    def stat() -> None:
        source, destination, _, _ = window.arguments()
        operations.append({"operation": "stat_fixture", "path": cstring(window, source).decode(),
                           "st_size": size, "real_operation": False})
        window.cpu.mem_write(destination, bytes(88))
        window.write32(destination + 44, size)
        window.result(0)

    def classify() -> None:
        operations.append({"operation": "DMM_unit_type_fixture", "requested_bytes": window.arguments()[0]})
        window.result(0x42)

    def allocate() -> None:
        if window.arguments()[:2] != [manager, 0x42]:
            raise ValueError("Unexpected original DMM allocation arguments")
        window.result(0)

    def open_file() -> None:
        name, flags, _, _ = window.arguments()
        if cstring(window, name) != b"/mnt/fixture.bin" or flags != 0:
            raise ValueError("Original file request changed pathname or O_RDONLY admission")
        operations.append({"operation": "open_fixture", "flags": flags, "result": open_result,
                           "real_operation": False})
        window.result(open_result)

    def read_file() -> None:
        descriptor, destination, count, _ = window.arguments()
        if (descriptor, destination, count) != (open_result, output + 4, size):
            raise ValueError("Original read does not use the stat size and declared DMM output")
        count = size if read_result is None else read_result
        if count:
            window.cpu.mem_write(destination, payload[:count])
        operations.append({"operation": "read_fixture", "requested": size, "returned": count,
                           "real_operation": False})
        window.result(count)

    window.fixture("strlen", string_length)
    window.fixture("strncat", concatenate)
    window.fixture("sys_mount", mount)
    window.fixtures[IMAGE + 0x9024] = ("declared stat ABI; no filesystem", stat)
    window.fixture("_ZN6DmmMgr13CheckUnitTypeEj", classify)
    window.fixture("_ZN6DmmMgr9dmm_allocEj", allocate)
    window.fixture("open", open_file)
    window.fixture("read", read_file)
    for name in ("fsync", "close"):
        def operation(name=name) -> None:
            operations.append({"operation": name + "_fixture", "real_operation": False})
            window.result(0)
        window.fixture(name, operation)
    for name in ("_Z18jiritsu_cpp_logrechPKc", "_Z18jiritsu_cpp_logrechPKcj",
                 "_Z22jiritsu_cppdump_logrechPKcPhj"):
        window.fixture(name, lambda: window.result(0))
    result = window.run({UC_ARM_REG_R0: request, UC_ARM_REG_R1: manager, UC_ARM_REG_R2: total})
    status = window.read32(output)
    transferred = window.read32(total)
    expected_status = (0x10000004 if mode == 0 else
                       0x10000008 if open_result < 0 else
                       0x1000000A if read_result is not None and read_result != size else 0)
    if status != expected_status or transferred != (
            4 + size if expected_status == 0 else 4):
        raise RuntimeError("Original file result differs from the exact-read/error contract")
    return {"mode": mode, "stat_size_fixture": size, "read_result_fixture": read_result,
            "status": status, "response_bytes_including_status": transferred,
            "operations": operations, "original_execution": result,
            "scope": "Original file-read orchestration and DMM data getter; constructed stat/DMM/libc results; no actual file or mount"}


def qualify(core: Library, jiritsu: Library) -> dict:
    scratch = [scratch_read(core, offset, length) for offset, length in
               ((0, 4), (0, 256), (255, 1), (255, 0), (256, 0), (255, 2),
                (0, 257), (0xFFFFFFFF, 4))]
    files = [
        jiritsu_read(jiritsu, size=12),
        jiritsu_read(jiritsu, size=0),
        jiritsu_read(jiritsu, size=12, read_result=11),
        jiritsu_read(jiritsu, size=12, open_result=-1),
        jiritsu_read(jiritsu, mode=0),
    ]
    return {"status": "offline_consumers_verified_no_physical_reader",
            "scratch_controls": scratch, "subsystem_routes": [routed_read(core, level) for level in (2, 3)],
            "jiritsu_file_controls": files,
            "fixed_partition_admission": [adjustment_gate(core, 0x601, 0x32, level, b"")
                                           for level in (1, 2, 3)],
            "block_capacity_controls": [block_capacity(core, 122880),
                                        block_capacity(core, 122880, ioctl_result=-1)],
            "fixed_partition_lifecycle": [
                nflasha5_lifecycle(core), nflasha5_lifecycle(core, busy=True),
                nflasha5_lifecycle(core, open_result=-1),
                nflasha5_lifecycle(core, lock_result=-1),
                nflasha5_lifecycle(core, map_result=-1),
            ],
            "limitations": [
                "Package bytes and explicit runtime fixtures do not attest the live installed system.",
                "The local RAM_READ source is a 256-byte BSS scratch buffer, not arbitrary address space.",
                "Subsystem RAM_READ stops before allocation/IPC; no raw-flash namespace is established.",
                "File reads use a supplied stat size and do not implement block-capacity discovery or chunked acquisition.",
                "A zero st_size fixture produces zero file bytes; no physical device stat was measured.",
                "The read-named path still calls fsync; no live filesystem side-effect guarantee is inferred.",
                "DMM allocation, module/queue registration, transfer cleanup and Senser exit remain separate live gates.",
                "The fixed-node callback is restricted to nflasha5; its direct fixture invocation is not a complete host-to-camera read.",
                "A successful explicit callback release does not prove asynchronous cancellation or whole-session teardown.",
            ]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    if not output.is_relative_to(PROJECT_ROOT / "artifacts"):
        parser.error("Offline receipts must remain under artifacts")
    output.mkdir(parents=True, exist_ok=False)
    report = {"started_utc": datetime.now(timezone.utc).isoformat(), "camera_operations": 0,
              "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "inputs": {}, "status": "in_progress"}
    try:
        libraries = {}
        for name, digest in (("libsencore.so", HASHES["libsencore.so"]), ("libJiritsu.so", JIRITSU_SHA)):
            raw = (PACKAGE / SYSTEM / name).read_bytes()
            libraries[name] = Library(raw, digest)
            report["inputs"][name] = {"sha256": digest, "bytes": len(raw)}
        report.update(qualify(libraries["libsencore.so"], libraries["libJiritsu.so"]))
    except (OSError, ValueError, RuntimeError) as error:
        report.update(status="blocked", error_type=type(error).__name__, error=str(error))
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "camera_operations", "error")
                      if key in report}, indent=2))
    return 0 if report["status"] == "offline_consumers_verified_no_physical_reader" else 2


if __name__ == "__main__":
    raise SystemExit(main())
