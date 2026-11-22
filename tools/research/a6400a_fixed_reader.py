"""Bounded 0336 fixed-nflasha5 protocol logic; no USB discovery or live CLI."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
import hashlib
import math
import struct
from threading import Event
import time
from typing import Protocol

HEADER = struct.Struct("<IHHBBBB")
COMMAND = struct.pack("<HH", 0x601, 0x32)
CAPACITY = 62_914_560
FRAME_BYTES = 0x100000
TRANSFER_BYTES = 0x8000


class BulkTransport(Protocol):
    """An already-admitted 0336 transport honoring each supplied timeout."""

    def read(self, length: int, *, timeout_ms: int) -> bytes: ...

    def write(self, data: bytes, *, timeout_ms: int) -> int: ...


class ByteSink(Protocol):
    def write(self, data: bytes) -> int: ...


@dataclass
class ReadReceipt:
    expected_bytes: int
    status: str = "not_started"
    request_attempted: bool = False
    request_sent: bool = False
    payload_bytes: int = 0
    output_bytes: int = 0
    frames: int = 0
    io_calls: int = 0
    zero_length_packets: int = 0
    wire_complete: bool = False
    cleanup_verified: bool = False
    output_integrity_unknown: bool = False
    payload_sha256: str = ""
    output_sha256: str = ""
    error: str | None = None
    output_error: str | None = None


class FixedReadError(RuntimeError):
    def __init__(self, receipt: ReadReceipt):
        self.receipt = asdict(receipt)
        super().__init__(receipt.error)


class FixedPartitionReader:
    """One read, one fixed command, one deadline; cancellation drains valid data.

    USB admission and ownership belong to the caller. A complete stream does
    not attest firmware cleanup, USB STOP or a safely recovered camera.
    """

    def __init__(self, transport: BulkTransport, *, expected_bytes: int = CAPACITY,
                 sequence: int = 1, seconds: float = 120,
                 clock: Callable[[], float] = time.monotonic):
        if (type(expected_bytes) is not int or not 0 < expected_bytes <= CAPACITY
                or expected_bytes % 512):
            raise ValueError("Fixed-node capacity must be positive, sector-aligned and at most 60 MiB")
        if type(sequence) is not int or not 1 <= sequence <= 0xFFFF:
            raise ValueError("A nonzero unsigned 16-bit sequence is required")
        if not math.isfinite(seconds) or not 0 < seconds <= 120:
            raise ValueError("The complete transfer must have a finite deadline of at most 120 seconds")
        self.transport = transport
        self.sequence = sequence
        self.seconds = seconds
        self.clock = clock
        self.receipt = ReadReceipt(expected_bytes)
        self.deadline = 0.0
        self.payload_hash = hashlib.sha256()
        self.output_hash = hashlib.sha256()
        self.max_calls = 128 + 64 * math.ceil(expected_bytes / TRANSFER_BYTES)
        self.used = False

    def _snapshot(self) -> ReadReceipt:
        self.receipt.payload_sha256 = self.payload_hash.hexdigest()
        self.receipt.output_sha256 = self.output_hash.hexdigest()
        return self.receipt

    def _fail(self, status: str, message: str) -> None:
        self.receipt.status = status
        self.receipt.error = message
        raise FixedReadError(self._snapshot())

    def _timeout(self) -> int:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            self._fail("deadline", "Transfer/drain deadline reached; firmware cleanup is unverified")
        if self.receipt.io_calls >= self.max_calls:
            self._fail("io_limit", "Transfer/drain I/O limit reached; firmware cleanup is unverified")
        self.receipt.io_calls += 1
        return max(1, min(2000, math.ceil(remaining * 1000)))

    def _read(self, length: int) -> bytes:
        raw = self.transport.read(length, timeout_ms=self._timeout())
        if self.clock() >= self.deadline:
            self._fail("deadline", "Transfer/drain deadline reached after I/O; firmware cleanup is unverified")
        if not isinstance(raw, bytes) or len(raw) > length:
            self._fail("invalid_transport", "Transport returned non-bytes or an oversized transfer")
        return raw

    def _exact(self, length: int) -> bytes:
        result = bytearray()
        while len(result) < length:
            raw = self._read(length - len(result))
            if not raw:
                self._fail("short_transfer", "Unexpected zero-length packet inside header or payload")
            result.extend(raw)
        return bytes(result)

    def read(self, output: ByteSink, *, cancel: Event | None = None) -> ReadReceipt:
        if self.used:
            raise ValueError("A fixed reader cannot be reused or retried")
        self.used = True
        self.deadline = self.clock() + self.seconds
        cancelled = cancel is not None and cancel.is_set()
        if cancelled:
            self._fail("cancelled_before_request", "Read cancelled before sending any request")
        try:
            self.receipt.status = "reading"
            request = HEADER.pack(len(COMMAND), 0x40, self.sequence, 0, 0, 0, 0) + COMMAND
            timeout = self._timeout()
            self.receipt.request_attempted = True
            sent = self.transport.write(request, timeout_ms=timeout)
            if type(sent) is not int or sent != len(request):
                self._fail("short_request", "Request was not fully acknowledged; no retry or STOP sent")
            self.receipt.request_sent = True
            remaining = self.receipt.expected_bytes
            while remaining:
                raw = self._exact(HEADER.size)
                size, function, sequence, version, micon, offset, status = HEADER.unpack(raw)
                if (size != remaining or function != 0x40 or sequence != self.sequence
                        or (version, micon, offset, status) != (3, 0, 0, 0)):
                    self._fail(
                        "invalid_header",
                        "Fixed-node reply mismatch: " + repr(
                            (size, function, sequence, version, micon, offset, status)),
                    )
                self.receipt.frames += 1
                frame_remaining = min(remaining, FRAME_BYTES)
                while frame_remaining:
                    amount = min(frame_remaining, TRANSFER_BYTES)
                    pending = amount
                    while pending:
                        data = self._read(pending)
                        if not data:
                            self._fail("short_transfer", "Unexpected zero-length packet inside payload")
                        self.payload_hash.update(data)
                        self.receipt.payload_bytes += len(data)
                        cancelled = cancelled or (cancel is not None and cancel.is_set())
                        if not cancelled and self.receipt.output_error is None:
                            try:
                                written = output.write(data)
                            except (OSError, ValueError) as error:
                                self.receipt.output_error = type(error).__name__ + ": " + str(error)
                                self.receipt.output_integrity_unknown = True
                            else:
                                if type(written) is not int or not 0 <= written <= len(data):
                                    self.receipt.output_error = "Host write returned an invalid byte count"
                                    self.receipt.output_integrity_unknown = True
                                else:
                                    self.receipt.output_bytes += written
                                    self.output_hash.update(data[:written])
                                    if written != len(data):
                                        self.receipt.output_error = "Short host write; partial output retained"
                        pending -= len(data)
                    if amount % 512 == 0:
                        if self._read(512):
                            self._fail("invalid_terminator", "Expected an empty USB terminator, not data to discard")
                        self.receipt.zero_length_packets += 1
                    remaining -= amount
                    frame_remaining -= amount
            self.receipt.wire_complete = True
        except OSError as error:
            self._fail("transport_failed", type(error).__name__ + ": " + str(error))
        cancelled = cancelled or (cancel is not None and cancel.is_set())
        if self.receipt.output_error is not None:
            self._fail("output_failed_drained", self.receipt.output_error)
        if cancelled:
            self._fail("cancelled_drained", "Cancellation drained the stream; firmware cleanup is still unverified")
        self.receipt.status = "stream_complete_cleanup_unverified"
        return self._snapshot()
