"""Offline original-code framing, serializer and fixed-node release controls."""
from __future__ import annotations

import argparse
from collections.abc import Iterator
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
from threading import Event

from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2

from project_paths import PROJECT_ROOT
from tools.research.a6400a_fixed_reader import (
    CAPACITY, COMMAND, FRAME_BYTES, HEADER, TRANSFER_BYTES, FixedPartitionReader,
    FixedReadError,
)
from tools.research.a6400a_reader_controls import CLASSIFIER, nflasha5_window
from tools.research.a6400a_service_contracts import (
    DATA, HASHES, IMAGE, PACKAGE, RETURN, SYSTEM, Library, REPLY, SERIALIZE, ThumbWindow,
)

PAYLOAD = 0x03000000
PACKET_SERIALIZE = "_Z9SerializeP10PacketDataj"
SEND = "_Z12SenserIfSendPhj"
MINIMUM = "_ZSt3minIjERKT_S2_S2_"
MAXIMUM = "_ZSt3maxIjERKT_S2_S2_"
DESTRUCTOR = "_ZN29SubsystemCommunicationCommandD1Ev"


def _logging(window: ThumbWindow) -> None:
    for name in ("_Z10cpp_logrechPKc", "_Z14cppdump_logrechPKcPhj"):
        window.fixture(name, lambda: window.result(0))
        veneer = window.plt_address(name) - 4
        if bytes(window.cpu.mem_read(veneer, 4)) == b"\x78\x47\xC0\x46":
            window.allowed.append((veneer, veneer + 4))


def _memory(window: ThumbWindow, address: int, length: int) -> None:
    table = window.library.elf.get_section_by_name(".rel.dyn")
    symbols = window.library.elf.get_section(table["sh_link"])
    vtable = window.library.symbols["_ZTV10MemoryData"]
    start = vtable["st_value"]
    for row in table.iter_relocations():
        if start <= row["r_offset"] < start + vtable["st_size"]:
            symbol = symbols.get_symbol(row["r_info_sym"])
            if row["r_info_type"] != 2 or not isinstance(symbol["st_shndx"], int):
                raise ValueError("Unexpected original MemoryData vtable relocation")
            window.write32(IMAGE + row["r_offset"], IMAGE + symbol["st_value"])
            if symbol["st_info"]["type"] == "STT_FUNC":
                window.allow(symbol.name)
    window.globals("_Z15SenserIfReceivePhj", "_Z15SenserIfRcvPhysPhj",
                   "_Z15SenserIfSndPhysPhj", SEND)
    window.cpu.mem_write(address, struct.pack(
        "<6I", IMAGE + start + 8, IMAGE + window.library.function(SEND)[0] + 1,
        PAYLOAD, PAYLOAD + length, PAYLOAD, 0))
    window.original(MINIMUM)
    _logging(window)


def response_frames(core: Library, length: int) -> dict:
    if not 0 < length <= CAPACITY:
        raise ValueError("Original framing control exceeds the fixed-node capacity profile")
    window = ThumbWindow(core, REPLY)
    packet, data = DATA + 0x1000, DATA + 0x2000
    _memory(window, data, length)
    window.original(MAXIMUM)
    window.cpu.mem_write(packet, HEADER.pack(0, 0x40, 1, 0, 0, 0, 0))
    headers: list[bytes] = []
    frames: list[int] = []

    def header() -> None:
        sender, source, count, _ = window.arguments()
        if (sender, source, count) != (IMAGE + core.function(SEND)[0] + 1, packet, HEADER.size):
            raise ValueError("Original response did not serialize its exact header")
        headers.append(bytes(window.cpu.mem_read(source, count)))
        window.result(0)

    def payload() -> None:
        source, count, _, _ = window.arguments()
        if source != data or count != min(length - sum(frames), FRAME_BYTES):
            raise ValueError("Original response exceeded its remaining-byte frame")
        frames.append(count)
        window.write32(data + 16, window.read32(data + 16) + count)
        window.result(1)

    window.fixture(SERIALIZE, header)
    window.fixture(PACKET_SERIALIZE, payload)
    for address in (0xEC10, 0xEC30):
        window.fixtures[IMAGE + address] = ("declared send mutex fixture; no threads", lambda: window.result(0))
    result = window.run({UC_ARM_REG_R0: packet, UC_ARM_REG_R1: data, UC_ARM_REG_R2: 0})
    expected = [HEADER.pack(length - offset, 0x40, 1, 3, 0, 0, 0)
                for offset in range(0, length, FRAME_BYTES)]
    if headers != expected or sum(frames) != length:
        raise RuntimeError("Original header progression differs from exact remaining-byte framing")
    return {"bytes": length, "header_hex": [raw.hex() for raw in headers],
            "frame_bytes": frames, "original_execution": result,
            "scope": "Original SendResponse/MemoryData methods; header/data serialization and mutex fixtures, not USB"}


def serialize_payload(core: Library, *, results: tuple[int, ...] = (127, 385),
                      failure: int | None = None) -> dict:
    length = 65536
    window = ThumbWindow(core, PACKET_SERIALIZE)
    data = DATA + 0x2000
    _memory(window, data, length)
    window.original(SERIALIZE)
    window.allow(SEND)
    window.write32(IMAGE + 0x5AEC4, DATA + 0x7000)
    writes: list[dict] = []
    completed = 0

    def send() -> None:
        nonlocal completed
        _, source, count, _ = window.arguments()
        if source != PAYLOAD + completed or not 0 < count <= TRANSFER_BYTES:
            raise ValueError("Original send pointer/32-KiB cap differs from the declared source")
        result = failure if failure is not None else (
            results[len(writes)] if len(writes) < len(results) else count)
        writes.append({"offset": source - PAYLOAD, "requested": count,
                       "returned_fixture": result, "real_operation": False})
        if 0 < result <= count:
            completed += result
        window.result(result)

    def cancellation_enabled() -> None:
        if window.arguments()[:2] != [0, 0]:
            raise ValueError("Unexpected original serializer cancellation policy")
        window.result(0)

    window.fixture("_ZN15usb_send_func_tclEPhj", send)
    window.fixture("pthread_setcancelstate", cancellation_enabled)
    window.fixtures[IMAGE + 0xEC60] = ("declared error lock handoff; no thread cancellation", lambda: window.result(0))
    result = window.run(
        {UC_ARM_REG_R0: data, UC_ARM_REG_R1: length},
        stop_before=window.plt_address("pthread_testcancel") if failure is not None else None,
    )
    advanced = window.read32(data + 16) - PAYLOAD
    if failure is None and (completed, advanced, window.arguments()[0]) != (length, length, 1):
        raise RuntimeError("Original serializer did not retry short writes and advance exactly")
    if failure is not None and (completed or advanced):
        raise RuntimeError("An invalid original I/O result advanced the source")
    return {"bytes": length, "returned_fixture": failure, "writes": writes,
            "bytes_sent": completed, "bytes_advanced": advanced,
            "cancellation_boundary": failure is not None, "original_execution": result,
            "scope": "Original PacketData/MemoryData/Serialize/SenserIfSend; declared USB writes, no real USB or pthread unwind"}


def completion_release(core: Library, *, state: int | None = None,
                       delivery_result: int = 0) -> dict:
    if state not in (None, 0, 8, 16):
        raise ValueError("Cleanup-state fixture outside the measured profile")
    window, operations = nflasha5_window(core, map_result=PAYLOAD)
    _logging(window)
    message = DATA + 0x4000
    request = bytearray(0x140)
    request[0] = 1
    struct.pack_into("<HH", request, 24, 0x601, 0x32)
    window.cpu.mem_write(message, bytes(request))
    acquired = window.run({UC_ARM_REG_R0: message}, start=IMAGE + 0x150EC)
    window.cpu.mem_write(DATA + 12, bytes(request))
    window.write32(DATA + 0x14C, 0x3D025F)
    window.write32(DATA + 0x150, 7)
    window.write32(DATA + 0x154, message)
    window.allowed.extend((IMAGE + start, IMAGE + end) for start, end in (
        CLASSIFIER, (0x11FA4, 0x11FBA), (0x12B50, 0x13090),
        (0x12040, 0x120B0), (0x120B0, 0x120D0),
    ))
    window.allow(DESTRUCTOR)
    window.globals("_ZTV29SubsystemCommunicationCommand")
    extraction = window.run(
        {UC_ARM_REG_R0: DATA + 12, UC_ARM_REG_R1: DATA + 0x5000},
        start=IMAGE + 0x12B50,
    )
    extracted_state = window.cpu.mem_read(DATA + 0x158, 1)[0]
    if window.arguments()[0] != PAYLOAD or extracted_state != 16:
        raise RuntimeError("Original mapped-response extraction did not arm mode-10 completion")
    if state is not None:
        window.cpu.mem_write(DATA + 0x158, bytes([state]))
    deliveries: list[dict] = []
    freed: list[int] = []
    resume = [0]

    def delivered() -> None:
        window.cpu.reg_write(UC_ARM_REG_LR, resume[0])
        window.result(0)

    def deliver() -> None:
        if window.arguments()[:2] != [0x3D025F, 7]:
            raise ValueError("Original destructor changed its owned OSAL queue/message")
        mode = window.cpu.mem_read(message, 1)[0]
        if mode != 16 or window.read32(message + 16) != PAYLOAD:
            raise ValueError("Original completion packet differs from the owned mapping")
        deliveries.append({"mode": mode, "queue": 0x3D025F, "result_fixture": delivery_result,
                           "real_operation": False})
        if delivery_result:
            window.result(delivery_result)
        else:
            resume[0] = window.cpu.reg_read(UC_ARM_REG_LR)
            window.cpu.reg_write(UC_ARM_REG_R0, message)
            window.cpu.reg_write(UC_ARM_REG_LR, RETURN + 0x41)
            window.cpu.reg_write(UC_ARM_REG_PC, IMAGE + 0x150ED)

    def free_message() -> None:
        if window.arguments()[:2] != [0x3D025F, 7]:
            raise ValueError("Original destructor freed an unowned fixture message")
        freed.append(7)
        window.result(0)

    window.fixtures[RETURN + 0x40] = ("declared synchronous OSAL callback return", delivered)
    window.fixture("osal_snd_sync_msg", deliver)
    window.fixture("osal_free_msg", free_message)
    for name in ("_ZN23InconcurrentCommandTypeI29SubsystemCommunicationCommandED2Ev",
                 "_ZN7CommandD2Ev"):
        window.fixture(name, lambda: window.result(0))
    destruction = window.run({UC_ARM_REG_R0: DATA},
                             start=IMAGE + core.function(DESTRUCTOR)[0])
    fields = [window.read32(IMAGE + offset) for offset in (0x5AE8C, 0x5B294, 0x5B298)]
    released = fields == [0xFFFFFFFF, 0, 0]
    if released != (state in (None, 16) and delivery_result == 0):
        raise RuntimeError("Original release result differs from the explicit cleanup-state/delivery fixture")
    return {"extracted_state": extracted_state, "destructor_state_fixture": state,
            "delivery_result_fixture": delivery_result, "mapping_released": released,
            "resource_fields_after": fields, "deliveries": deliveries, "freed_messages": freed,
            "operations": operations, "acquisition_execution": acquired,
            "extraction_execution": extraction, "destruction_execution": destruction,
            "scope": "Original callback/extraction/destructor/release; synchronous OSAL and libc/base-destructor fixtures, not actual pthread unwinding or USB STOP"}


def fixture_bytes(offset: int, length: int) -> bytes:
    pattern = bytes(range(256))
    start = offset % len(pattern)
    return (pattern * ((start + length + 255) // 256))[start:start + length]


class FramedTransport:
    """Constructed USB events, including empty packet terminators; never a device."""

    def __init__(self, headers: list[bytes], *, max_fragment: int = TRANSFER_BYTES):
        if not 0 < max_fragment <= TRANSFER_BYTES:
            raise ValueError("Invalid constructed-fragment bound")
        self.headers = headers
        self.max_fragment = max_fragment
        self.pending: bytes | None = None
        self.events = self._events()
        self.requests: list[bytes] = []

    def _events(self) -> Iterator[bytes]:
        offset = 0
        for header in self.headers:
            yield header
            left = min(HEADER.unpack(header)[0], FRAME_BYTES)
            while left:
                count = min(left, TRANSFER_BYTES)
                yield fixture_bytes(offset, count)
                if count % 512 == 0:
                    yield b""
                offset += count
                left -= count

    def write(self, data: bytes, *, timeout_ms: int) -> int:
        expected = HEADER.pack(4, 0x40, 1, 0, 0, 0, 0) + COMMAND
        if self.requests or data != expected or not 0 < timeout_ms <= 2000:
            raise ValueError("Constructed transport refuses anything but one fixed-node request")
        self.requests.append(data)
        return len(data)

    def read(self, length: int, *, timeout_ms: int) -> bytes:
        if not self.requests or not 0 < length <= TRANSFER_BYTES or not 0 < timeout_ms <= 2000:
            raise ValueError("Constructed read exceeds its phase/transfer/deadline bounds")
        if self.pending is None:
            self.pending = next(self.events, None)
            if self.pending is None:
                raise OSError("Constructed input exhausted")
        count = min(length, self.max_fragment)
        raw = self.pending[:count]
        self.pending = self.pending[count:] or None
        return raw


class HashSink:
    def __init__(self):
        self.size = 0
        self.sha = hashlib.sha256()

    def write(self, data: bytes) -> int:
        self.size += len(data)
        self.sha.update(data)
        return len(data)


def qualify(core: Library) -> dict:
    framing = [response_frames(core, length) for length in (
        512, FRAME_BYTES - 512, FRAME_BYTES, FRAME_BYTES + 512, CAPACITY)]
    host = []
    for source in framing:
        sink = HashSink()
        transport = FramedTransport([bytes.fromhex(raw) for raw in source["header_hex"]])
        receipt = FixedPartitionReader(transport, expected_bytes=source["bytes"]).read(sink)
        if sink.size != source["bytes"] or sink.sha.hexdigest() != receipt.payload_sha256:
            raise RuntimeError("Bounded host reader differs from the original-frame fixture")
        host.append(asdict(receipt))
    interrupted = []
    for stop_after_first_write in (True, False):
        cancel = Event()

        class InterruptedSink(HashSink):
            def write(self, data: bytes) -> int:
                if not stop_after_first_write:
                    raise OSError("Declared host output failure")
                written = super().write(data)
                cancel.set()
                return written

        source = framing[3]
        transport = FramedTransport([bytes.fromhex(raw) for raw in source["header_hex"]])
        try:
            FixedPartitionReader(transport, expected_bytes=source["bytes"]).read(
                InterruptedSink(), cancel=cancel)
        except FixedReadError as error:
            expected = "cancelled_drained" if stop_after_first_write else "output_failed_drained"
            if error.receipt["status"] != expected or not error.receipt["wire_complete"]:
                raise RuntimeError("Declared host interruption did not drain its bounded stream") from error
            interrupted.append(error.receipt)
        else:
            raise RuntimeError("A cancelled/failed output was incorrectly reported as a successful read")
    return {"status": "offline_transfer_controls_verified_no_live_adapter",
            "framing_controls": framing, "host_original_frame_replays": host,
            "host_interruption_controls": interrupted,
            "serializer_controls": [serialize_payload(core)] + [
                serialize_payload(core, failure=value) for value in (0, -1, -4, -6, 65537)],
            "completion_controls": [completion_release(core), completion_release(core, state=0),
                                    completion_release(core, state=8),
                                    completion_release(core, delivery_result=-1)],
            "limitations": [
                "Only a fixed 0601/32 request is implemented; no partition selector, USB discovery, authentication or live adapter.",
                "Original response framing uses a declared PacketData serialization fixture; actual serializer/32-KiB capping are separate controls.",
                "Host replays transfer constructed bytes, never camera data or updater-extracted substitutes.",
                "Cancellation/error controls stop at pthread_testcancel, not a fully modeled forced unwind.",
                "Empty USB terminators are an explicit transport fixture; kernel request flags do not attest controller completion or live capture.",
                "OSAL delivery/free, libc resources and base destructors are declared fixtures; delivery failure leaves the mapping owned.",
                "A complete host stream does not prove asynchronous firmware cleanup or an acknowledged USB STOP.",
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
              "reader_sha256": hashlib.sha256(
                  (PROJECT_ROOT / "tools" / "research" / "a6400a_fixed_reader.py").read_bytes()).hexdigest(),
              "inputs": {}, "status": "in_progress"}
    try:
        raw = (PACKAGE / SYSTEM / "libsencore.so").read_bytes()
        core = Library(raw, HASHES["libsencore.so"])
        report["inputs"]["libsencore.so"] = {"bytes": len(raw), "sha256": core.sha256}
        report.update(qualify(core))
    except (OSError, ValueError, RuntimeError) as error:
        report.update(status="blocked", error_type=type(error).__name__, error=str(error))
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "camera_operations", "error")
                      if key in report}, indent=2))
    return 0 if report["status"] == "offline_transfer_controls_verified_no_live_adapter" else 2


if __name__ == "__main__":
    raise SystemExit(main())
