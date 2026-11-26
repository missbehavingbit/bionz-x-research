"""Declared, single-extent DMM fixtures; never a camera or kernel service."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import struct

DMM_SHA256 = "4560112c05abeb5588950c9adac5a0bb5fc4cbb13cd44bdf8ad46a904ce14f01"
DMM_REQUEST = struct.Struct("<HHIIIIHH")
DMM_COMPLETION = struct.Struct("<HhHHHhIII")
DMM_HANDLE, DMM_UNIT_TYPE = 0x5678, 0x02030405
DMM_ADDRESS = 0x04000020
MAX_DMM_BYTES = 4096


@dataclass(frozen=True)
class DmmMetadata:
    handle: int
    unit_type: int
    address: int
    capacity: int

    @classmethod
    def decode(cls, completion: bytes, *, handle: int, unit_type: int,
               raw_size: int) -> DmmMetadata:
        if not isinstance(completion, bytes) or len(completion) != DMM_COMPLETION.size:
            raise ValueError("DMM fixture requires an exact 24-byte completion")
        operation, status, actual_handle, token, count, detail, actual_type, address, capacity = (
            DMM_COMPLETION.unpack(completion))
        if operation != 0x8002 or status or detail or token:
            raise ValueError("DMM fixture requires a successful operation-2 completion with token zero")
        if (actual_handle, actual_type) != (handle, unit_type) or not actual_type:
            raise ValueError("DMM fixture handle/unit identity mismatch")
        if count != 1:
            raise ValueError("DMM helper copies only one extent; other counts are unsupported")
        if not address or not 0 < capacity <= MAX_DMM_BYTES or address + capacity > 1 << 32:
            raise ValueError("DMM extent exceeds the bounded ARM32 fixture range")
        if type(raw_size) is not int or not 0 <= raw_size <= capacity:
            raise ValueError("DMM raw length exceeds the declared extent")
        if address + raw_size >= 1 << 32:
            raise ValueError("DMM raw pointer end is not representable in MemoryData")
        return cls(actual_handle, actual_type, address, capacity)


class DmmBufferFixture:
    """An externally owned byte fixture, not an allocator or automatic mapping."""

    def __init__(self, completion: bytes, payload: bytes, *, raw_size: int):
        self.metadata = DmmMetadata.decode(
            completion, handle=DMM_HANDLE, unit_type=DMM_UNIT_TYPE, raw_size=raw_size)
        if not isinstance(payload, bytes) or len(payload) != self.metadata.capacity:
            raise ValueError("DMM fixture bytes must exactly cover the declared capacity")
        self.completion = completion
        self.payload = payload
        self.raw_size = raw_size
        self.owner_alive = True
        self.consumer = "not_acquired"
        self.events = [{"event": "declared_owner", "shares": 1, "accesses": 1}]

    def bind_reply(self, header: bytes) -> None:
        if not isinstance(header, bytes) or len(header) != 16:
            raise ValueError("DMM fixture requires an exact 16-byte raw reply header")
        phase, response, _, transfer, reserved, handle, unit_type, size = struct.unpack(
            "<BBHBBHII", header)
        if ((phase, response, transfer, reserved) != (8, 1, 1, 0)
                or (handle, unit_type, size) != (
                    self.metadata.handle, self.metadata.unit_type, self.raw_size)):
            raise ValueError("Raw reply does not match the admitted DMM fixture")
        if not self.owner_alive or self.consumer != "not_acquired":
            raise ValueError("DMM fixture is retired or already consumed")

    def acquire(self) -> None:
        if not self.owner_alive or self.consumer != "not_acquired":
            raise ValueError("DMM fixture cannot acquire a stale or duplicate consumer")
        self.consumer = "acquired"
        self.events.append({"event": "fixture_share_and_open", "shares": 2, "accesses": 2})

    def release_consumer(self) -> None:
        if not self.owner_alive or self.consumer != "acquired":
            raise ValueError("DMM fixture consumer release is unbalanced")
        self.consumer = "released"
        self.events.append({"event": "fixture_close_and_free", "shares": 1, "accesses": 1})

    def read(self, address: int, size: int) -> bytes:
        if not self.owner_alive or self.consumer != "released":
            raise ValueError("DMM fixture read outside the owner/consumer lifetime")
        if type(address) is not int or type(size) is not int:
            raise ValueError("DMM fixture read requires integer address and size")
        offset = address - self.metadata.address
        if offset < 0 or size < 0 or offset + size > self.raw_size:
            raise ValueError("DMM fixture read exceeds the declared raw reply range")
        return self.payload[offset:offset + size]

    def retire(self) -> None:
        if not self.owner_alive or self.consumer != "released":
            raise ValueError("DMM fixture owner retirement is unbalanced")
        self.owner_alive = False
        self.events.append({"event": "fixture_retired_not_a_kernel_free"})


def kernel_lifetime_controls(path: Path) -> dict:
    """Execute only selected original counter operations over a constructed row."""
    from unicorn import UC_HOOK_MEM_WRITE
    from unicorn import arm_const as arm

    from tools.research.dmm_config_trace import (
        BSS, CODE, RETURN, SCRATCH, STACK, DmmConfigProbe,
    )
    from tools.research.mount_evidence import symbol

    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != DMM_SHA256:
        raise ValueError("A6400A DMM input identity mismatch")

    class LifetimeProbe(DmmConfigProbe):
        def stop(self, reason, **details):
            details.setdefault("arguments", [
                self.mu.reg_read(getattr(arm, f"UC_ARM_REG_R{i}")) for i in range(4)])
            super().stop(reason, **details)

        def _code(self, mu, address, size, data):
            if address == self.extent_free:
                self.stop("before_extent_free", arguments=[
                    mu.reg_read(getattr(arm, f"UC_ARM_REG_R{i}")) for i in range(4)])
                return
            if address != RETURN and not any(a <= address < b for a, b in self.allowed):
                self.stop("unsupported_original_pc", address=address)
                return
            super()._code(mu, address, size, data)

        def _read(self, mu, access, address, size, value, data):
            super()._read(mu, access, address, size, value, data)
            for begin, end, allowed in self.declared_reads:
                if address < end and begin < address + size:
                    if not any(a <= address and address + size <= b for a, b in allowed):
                        self.stop("undeclared_dmm_state_read", address=address, size=size)
                        return

        def _write(self, mu, access, address, size, value, data):
            if not any(a <= address and address + size <= b for a, b in self.writable):
                self.stop("unsupported_original_write", address=address, size=size)

    probe = LifetimeProbe(raw, b"")
    names = ("dmm_unit_ctrl_MakeHandle", "dmm_unit_ctrl_Share", "dmm_unit_ctrl_Open",
             "dmm_unit_ctrl_Close", "dmm_unit_ctrl_Free")
    probe.allowed = [(CODE + symbol(probe.elf, n)["st_value"],
                      CODE + symbol(probe.elf, n)["st_value"] + symbol(probe.elf, n)["st_size"])
                     for n in names]
    probe.allowed.append((CODE + 0x85F0, CODE + 0x8658))
    probe.extent_free = CODE + symbol(probe.elf, "dmm_ext_ctrl_FreeExtent")["st_value"]
    row = BSS + 0x962A0 + 0xB18 + 0x278 * 0x150
    probe.writable = [(row + 8, row + 0xA), (row + 0xC, row + 0xE),
                      (SCRATCH + 0x100, SCRATCH + 0x108),
                      (SCRATCH + 0x200, SCRATCH + 0x202), (STACK, STACK + 0x4000)]
    probe.declared_reads = [
        (BSS, BSS + probe.elf.get_section_by_name(".bss")["sh_size"], [(row, row + 20)]),
        (SCRATCH, SCRATCH + 4096, [(SCRATCH + 0x100, SCRATCH + 0x108),
                                  (SCRATCH + 0x200, SCRATCH + 0x202),
                                  (SCRATCH + 0x300, SCRATCH + 0x314)]),
    ]
    probe.mu.mem_write(row, struct.pack("<I4xBBBBHHI",
                                       DMM_UNIT_TYPE, 1, 1, 0, 0x15, 2, 1, SCRATCH + 0x300))
    probe.mu.mem_write(SCRATCH + 0x300, struct.pack(
        "<5I", 0, DMM_ADDRESS & ~4095, DMM_ADDRESS & 4095, 0x23, 0))
    probe.mu.hook_add(UC_HOOK_MEM_WRITE, probe._write)
    calls = []

    def counters():
        prefix = bytes(probe.mu.mem_read(row, 20))
        return {"shares": prefix[8], "accesses": prefix[9],
                "state": struct.unpack_from("<H", prefix, 0xC)[0]}

    def call(name, args, expected, state, *, stop="isolated_function_return"):
        before = counters()
        result = probe.call(name, args, budget=10_000)
        if (result["status"] != stop or counters() != state
                or (expected is not None and result["return_value"] != expected)):
            raise AssertionError(f"Original DMM lifetime control changed: {result}; {counters()}")
        calls.append({"before": before, "after": counters(), "execution": result})

    owned = {"shares": 1, "accesses": 1, "state": 2}
    call(names[0], (0x278, 0x15), DMM_HANDLE, owned)
    call(names[1], (DMM_UNIT_TYPE + 1, DMM_HANDLE), 0xFFFFFFFD, owned)
    call(names[1], (DMM_UNIT_TYPE, DMM_HANDLE ^ 0x400), 0xFFFFFDFB, owned)
    call(names[1], (DMM_UNIT_TYPE, DMM_HANDLE), 0, {**owned, "shares": 2})
    call(names[2], (DMM_HANDLE, 1, SCRATCH + 0x100, SCRATCH + 0x200), 0,
         {"shares": 2, "accesses": 2, "state": 2})
    extent = bytes(probe.mu.mem_read(SCRATCH + 0x100, 8))
    count = struct.unpack("<H", probe.mu.mem_read(SCRATCH + 0x200, 2))[0]
    if count != 1 or struct.unpack("<II", extent) != (DMM_ADDRESS, 0x23):
        raise AssertionError("Original DMM extent metadata differs from the declared fixture")
    call(names[3], (DMM_HANDLE,), 0, {**owned, "shares": 2})
    call(names[4], (DMM_HANDLE,), 0, owned)
    call(names[3], (DMM_HANDLE,), 0, {"shares": 1, "accesses": 0, "state": 1})
    call(names[4], (DMM_HANDLE,), None, {"shares": 1, "accesses": 0, "state": 0},
         stop="before_extent_free")
    return {
        "sha256": DMM_SHA256, "scope": "original counters over a constructed unit and extent",
        "placements": probe.placements, "unit_row": row, "extent_hex": extent.hex(),
        "declared_extent_words": [0, DMM_ADDRESS & ~4095, DMM_ADDRESS & 4095, 0x23, 0],
        "calls": calls,
        "limitations": [
            "No DMM initialization, allocation, IPC, physical memory or camera state is supplied.",
            "Share and access counters are separate; operation 4 can close access and free a share.",
            "Final owner release stops before the original extent-free routine; teardown is not completed.",
        ],
    }
