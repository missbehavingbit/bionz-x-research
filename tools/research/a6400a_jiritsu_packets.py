"""Reproduce packaged A6400A Jiritsu packet contracts entirely offline.

Only hash-pinned ARM instructions and constructed memory/stream fixtures run.
There is no USB backend, authentication, filesystem service or OSAL response.
"""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version
import io
import json
from pathlib import Path
import platform
import struct
import sys

from elftools.elf.elffile import ELFFile
from unicorn import (
    UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_MEM_INVALID, UC_HOOK_MEM_READ,
    UC_MODE_THUMB, Uc, UcError,
)
from unicorn.arm_const import (
    UC_ARM_REG_CPSR, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0,
    UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP,
    UC_CPU_ARM_CORTEX_A9,
)

from project_paths import PROJECT_ROOT
from tools.research.a6400a_dmm import (
    DMM_ADDRESS, DMM_COMPLETION, DMM_HANDLE, DMM_REQUEST, DMM_SHA256,
    DMM_UNIT_TYPE, MAX_DMM_BYTES, DmmBufferFixture, kernel_lifetime_controls,
)
from tools.research.inventory import digest
from tools.research.startup_trace import analyze, plt_symbols, read_va

UPDATER_SHA256 = "fd6ae50755a9202a8b506b6b77aa27ab41ae717e96f05a0d4e2ad699b40d9ad1"
LIBRARY_HASHES = {
    "libJiritsu.so": "c3621c7ec30771604b1aaae25e51699340333486e1aa281a1cc5f4a543a99dff",
    "libsencore.so": "08804d8752c0bdfbad6e3f212cc1feaf2145e71e866a64c3f94bc4c2eca36089",
}
LIBRARY_DIR = (PROJECT_ROOT / "firmware" / "extracted_a6400a_v101"
               / "firmware.tar_unpacked" / "0700_part_image" / "dev"
               / "nflasha15_unpacked" / "lib")
HEADER = struct.Struct("<IHHBBBB")
OSAL_HEADER = struct.Struct("<BBHIHHBBHIIHH")
QUEUE = 0x016F0480
INLINE_SIZE = 0x124
BASE, DATA, STACK, RETURN = 0x01000000, 0x03000000, 0x03200000, 0x03400000
REGISTERS = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
TRANSACTION = "_ZN29SubsystemCommunicationCommand16TransactionStartEPK12SenserPacketPKvS4_"
SERIALIZE = "_Z9SerializePFiPhjEPvj"
SEND = "_Z12SenserIfSendPhj"
PHYSICAL_SEND = "_Z15SenserIfSndPhysPhj"
RECEIVE = "_Z15SenserIfReceivePhj"
LOG_FIXTURES = frozenset({
    "_Z10cpp_logrechPKc", "_Z10cpp_logrechPKcj", "_Z10cpp_logrechPKcjj",
    "_Z14cppdump_logrechPKcPhj", "_Z18jiritsu_cpp_logrechPKc",
})
RESPONSE_FUNCTIONS = (
    "_Z12SendResponseP12SenserPacketP10PacketDataj", SERIALIZE,
    "_Z9SerializeP10PacketDataj", "_ZSt3maxIjERKT_S2_S2_", "_ZSt3minIjERKT_S2_S2_",
    "_ZNK10MemoryData9IsHealthyEv", "_ZNK10MemoryData7GetSizeEv",
    "_ZNK10MemoryData5BeginEv", "_ZNK10MemoryData13GetBufferSizeEv",
    "_ZN10MemoryData9GetBufferEv", "_ZNK10MemoryData5GetIoEv",
    "_ZN10MemoryData7AdvanceEj",
)


def _uint(value: int, bits: int, name: str) -> None:
    if type(value) is not int or not 0 <= value < 1 << bits:
        raise ValueError(f"{name} must be an unsigned {bits}-bit integer")


def request_packet(command: int, payload: bytes, *, sequence: int = 1) -> bytes:
    """Construct the bounded, single-packet fixture profile, not a live request."""
    _uint(command, 16, "command")
    _uint(sequence, 16, "sequence")
    if command not in range(1, 0x13):
        raise ValueError("Unknown packaged Jiritsu command")
    if not isinstance(payload, bytes) or len(payload) > INLINE_SIZE:
        raise ValueError("Payload must be bytes within the 292-byte inline fixture profile")
    body = struct.pack("<HH", 0x1001, 0x4000 | command) + payload
    return HEADER.pack(len(body), 0x40, sequence, 0, 0, 0, 0) + body


def unpack_request(packet: bytes) -> tuple[int, int, bytes]:
    if len(packet) < HEADER.size + 4:
        raise ValueError("Truncated Senser request")
    size, function, sequence, version, micon, offset, response = HEADER.unpack_from(packet)
    if size != len(packet) - HEADER.size:
        raise ValueError("Senser body length mismatch")
    if (function, version, micon, offset, response) != (0x40, 0, 0, 0, 0):
        raise ValueError("Request is outside the declared Senser fixture profile")
    category, command = struct.unpack_from("<HH", packet, HEADER.size)
    payload = packet[HEADER.size + 4:]
    if category != 0x1001 or request_packet(command & 0xFF, payload, sequence=sequence) != packet:
        raise ValueError("Request is outside the packaged Jiritsu fixture profile")
    return command & 0xFF, sequence, payload


class LibraryImage:
    def __init__(self, path: Path, expected_sha256: str):
        self.path = path
        self.raw = path.read_bytes()
        if hashlib.sha256(self.raw).hexdigest() != expected_sha256:
            raise ValueError(f"Library input identity mismatch: {path.name}")
        self.elf = ELFFile(io.BytesIO(self.raw))
        if (self.elf.elfclass, self.elf.little_endian,
                self.elf["e_machine"], self.elf["e_type"]) != (32, True, "EM_ARM", "ET_DYN"):
            raise ValueError("Expected an ELF32 little-endian ARM shared library")
        table = self.elf.get_section_by_name(".dynsym")
        if table is None:
            raise ValueError("Missing original dynamic symbol table")
        self.values = {s.name: s["st_value"] for s in table.iter_symbols()
                       if isinstance(s["st_shndx"], int)}
        self.functions = {s.name: (s["st_value"], s["st_size"]) for s in table.iter_symbols()
                          if s["st_info"]["type"] == "STT_FUNC"
                          and isinstance(s["st_shndx"], int) and s["st_size"]}
        self.plt = plt_symbols(self.elf)

    def read(self, address: int, size: int) -> bytes:
        data = read_va(self.elf, self.raw, address, size)
        if data is None:
            raise ValueError(f"Original range is not file-backed: {address:#x}+{size:#x}")
        return data


class OfflineReplay:
    """Closed original-code windows; all other PCs/imports fail closed."""

    def __init__(self, image: LibraryImage, functions=(), windows=()):
        self.image = image
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        self.uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A9)
        pages = {}
        for segment in image.elf.iter_segments():
            if segment["p_type"] != "PT_LOAD":
                continue
            start = BASE + segment["p_vaddr"]
            flags = segment["p_flags"]
            permissions = ((flags & 4) >> 2) | (flags & 2) | ((flags & 1) << 2)
            for page in range(start & ~4095, (start + segment["p_memsz"] + 4095) & ~4095, 4096):
                pages[page] = pages.get(page, 0) | permissions
        for page, permissions in sorted(pages.items()):
            self.uc.mem_map(page, 4096, permissions)
        for segment in image.elf.iter_segments():
            if segment["p_type"] == "PT_LOAD":
                self.uc.mem_write(BASE + segment["p_vaddr"], segment.data())
        self.unresolved_data = {}
        relocations = image.elf.get_section_by_name(".rel.dyn")
        if relocations is not None:
            symbols = image.elf.get_section(relocations["sh_link"])
            for relocation in relocations.iter_relocations():
                address = BASE + relocation["r_offset"]
                kind = relocation["r_info_type"]
                symbol = symbols.get_symbol(relocation["r_info_sym"])
                if kind == 23:  # R_ARM_RELATIVE uses the original in-place addend.
                    value = BASE + self.word(address)
                elif kind in (2, 21):
                    if not isinstance(symbol["st_shndx"], int):
                        self.unresolved_data[address] = symbol.name
                        continue
                    value = BASE + symbol["st_value"]
                    if kind == 2:
                        value += self.word(address)
                else:
                    raise ValueError(f"Unsupported original data relocation: {kind}")
                self.put_word(address, value)
        self.uc.mem_map(DATA, 0x20000, 3)
        self.uc.mem_map(STACK, 0x10000, 3)
        self.uc.mem_map(RETURN, 4096, 5)
        self.allowed_names = set(functions)
        self.allowed = [(BASE + (image.functions[n][0] & ~1),
                         BASE + (image.functions[n][0] & ~1) + image.functions[n][1])
                        for n in functions]
        self.allowed.extend((BASE + start, BASE + end) for start, end in windows)
        self.boundaries = {}
        self.entries = {}
        self.stream = None
        self.stream_offset = 0
        self.sink = None
        self.sink_chunk = 0
        self.lock_fixtures = False
        self.uc.hook_add(UC_HOOK_CODE, self._code)
        self.uc.hook_add(UC_HOOK_MEM_INVALID, self._invalid_memory)
        for address, name in self.unresolved_data.items():
            self.uc.hook_add(UC_HOOK_MEM_READ, self._unresolved_read, name,
                             begin=address, end=address + 3)

    def word(self, address: int) -> int:
        return struct.unpack("<I", bytes(self.uc.mem_read(address, 4)))[0]

    def put_word(self, address: int, value: int) -> None:
        self.uc.mem_write(address, struct.pack("<I", value))

    def _return(self, value=None) -> None:
        if value is not None:
            self.uc.reg_write(UC_ARM_REG_R0, value)
        self.uc.reg_write(UC_ARM_REG_PC, self.uc.reg_read(UC_ARM_REG_LR))

    def _stop(self, reason: str) -> None:
        self.stop = reason
        self.uc.emu_stop()

    def _invalid_memory(self, cpu, access, address, size, value, user):
        self.memory_fault = {"access": access, "address": address, "size": size,
                             "pc": cpu.reg_read(UC_ARM_REG_PC),
                             "args": [cpu.reg_read(r) for r in REGISTERS]}
        return False

    def _unresolved_read(self, cpu, access, address, size, value, name):
        self._stop(f"unsupported_data_symbol:{name}@{address:#x}")

    def _service(self, name: str) -> bool:
        args = [self.uc.reg_read(r) for r in REGISTERS]
        if name in self.boundaries:
            self._stop(self.boundaries[name])
            return True
        if name in ("memcpy", "memset") and args[2] > 4096:
            self._stop(f"unsupported_memory_helper_size:{name}:{args[2]}")
            return True
        if name in LOG_FIXTURES:
            self.services.append({"fixture": "record_only_log", "symbol": name, "args": args})
            self._return()
        elif name == "memcpy":
            self.uc.mem_write(args[0], bytes(self.uc.mem_read(args[1], args[2])))
            self.services.append({"fixture": name, "args": args[:3]})
            self._return(args[0])
        elif name == "memset":
            self.uc.mem_write(args[0], bytes([args[1] & 255]) * args[2])
            self.services.append({"fixture": name, "args": args[:3]})
            self._return(args[0])
        elif name == RECEIVE and self.stream is not None:
            chunk = self.stream[self.stream_offset:self.stream_offset + args[1]]
            if len(chunk) != args[1]:
                self._stop("unsupported_receive_beyond_constructed_stream")
                return True
            self.uc.mem_write(args[0], chunk)
            self.stream_offset += len(chunk)
            self.services.append({"fixture": "constructed_receive_stream", "bytes": len(chunk)})
            self._return(len(chunk))
        elif name == SEND and self.sink is not None:
            size = min(args[1], self.sink_chunk) if self.sink_chunk else args[1]
            self.sink.extend(self.uc.mem_read(args[0], size))
            self.services.append({"fixture": "record_only_send_sink", "bytes": size})
            self._return(size)
        else:
            return False
        return True

    def _code(self, cpu, address, size, user):
        self.fetches += 1
        if address in self.entries:
            self._stop(self.entries[address])
            return
        if self.lock_fixtures and address in (BASE + 0xEC10, BASE + 0xEC30):
            self.services.append({"fixture": "record_only_lock" if address == BASE + 0xEC10
                                  else "record_only_unlock", "channel": cpu.reg_read(UC_ARM_REG_R0)})
            self._return()
            return
        relative = address - BASE
        if relative in self.image.plt:
            name = self.image.plt[relative]
            if self._service(name):
                return
            target = self.image.functions.get(name)
            if name in self.allowed_names or (
                    target and BASE + (target[0] & ~1) in self.entries):
                cpu.reg_write(UC_ARM_REG_PC, BASE + self.image.functions[name][0])
                return
            self._stop(f"unsupported_import:{name}")
            return
        for name in (SEND, RECEIVE, PHYSICAL_SEND):
            if relative == (self.image.values.get(name, -1) & ~1) and self._service(name):
                return
        if (relative + 4 in self.image.plt
                and self.image.read(relative, 4) == b"\x78\x47\xc0\x46"):
            self.original_instructions += 1
            return
        if not any(start <= address < end for start, end in self.allowed):
            self._stop(f"unsupported_pc:{address:#x}")
            return
        self.original_instructions += 1

    def call(self, entry: int, args: tuple[int, ...], *, stop: str = "returned") -> dict:
        self.stop = None
        self.memory_fault = None
        self.services = []
        self.fetches = self.original_instructions = 0
        self.uc.reg_write(UC_ARM_REG_CPSR, 0x30)
        self.uc.reg_write(UC_ARM_REG_SP, STACK + 0xFF00)
        self.uc.reg_write(UC_ARM_REG_LR, RETURN | 1)
        for index, register in enumerate(REGISTERS):
            self.uc.reg_write(register, args[index] if index < len(args) else 0)
        try:
            self.uc.emu_start(BASE + entry, RETURN, timeout=2_000_000, count=100_000)
        except UcError as error:
            raise RuntimeError(f"Original execution fault at PC={self.uc.reg_read(UC_ARM_REG_PC):#x}: "
                               f"{error}; memory={self.memory_fault}; "
                               f"args={[self.uc.reg_read(r) for r in REGISTERS]}") from error
        pc = self.uc.reg_read(UC_ARM_REG_PC)
        if self.stop is None:
            self.stop = "returned" if pc == RETURN else "instruction_or_time_limit"
        result = {"stop": self.stop, "pc": pc, "args_or_return": [
            self.uc.reg_read(r) for r in REGISTERS],
            "original_instructions": self.original_instructions,
            "hook_fetches": self.fetches, "callee_fixtures": self.services}
        if self.stop != stop:
            raise RuntimeError(f"Unexpected original-code boundary: {result}")
        return result


class DmmReplay(OfflineReplay):
    """Two exact OSAL exchanges, not a general DMM/OSAL implementation."""

    def __init__(self, image: LibraryImage, buffer: DmmBufferFixture, *, statuses=(0, 0)):
        if statuses not in ((0, 0), (-1, 0), (0, -1)):
            raise ValueError("Unsupported constructed DMM completion-status control")
        super().__init__(image, (*RESPONSE_FUNCTIONS, "_ZN10MemoryData7ReleaseEv"),
                         ((0x11F28, 0x11F32), (0x11FA4, 0x11FBA),
                          (0x120D0, 0x12240), (0x12B50, 0x13090)))
        self.buffer = buffer
        self.statuses = statuses
        self.exchange_index = 0
        self.stage = "dynamic_alloc"
        self.exchanges = []
        self.qid = 0x003D0901
        self.message, self.reply = DATA + 0x8000, DATA + 0x8100

    def _service(self, name: str) -> bool:
        args = [self.uc.reg_read(r) for r in REGISTERS]
        if name == PHYSICAL_SEND and self.sink is not None:
            try:
                data = self.buffer.read(args[0], args[1])
            except ValueError as error:
                self._stop(f"unsupported_dmm_fixture_read:{error}")
                return True
            size = min(len(data), self.sink_chunk) if self.sink_chunk else len(data)
            self.sink.extend(data[:size])
            self.services.append({"fixture": "record_only_dmm_sink", "address": args[0],
                                  "requested": args[1], "bytes": size})
            self._return(size)
            return True
        if name not in ("osal_dynamic_id", "osal_valloc_msg_wait", "osal_snd_msg",
                        "osal_rcv_msg_tmo", "osal_free_msg"):
            return super()._service(name)
        if self.exchange_index >= 2:
            self._stop(f"unsupported_dmm_exchange:{name}")
            return True
        operation = (2, 4)[self.exchange_index]
        expected_name = {
            "dynamic_alloc": "osal_dynamic_id", "allocate": "osal_valloc_msg_wait",
            "send": "osal_snd_msg", "receive": "osal_rcv_msg_tmo",
            "free": "osal_free_msg", "dynamic_free": "osal_dynamic_id",
        }[self.stage]
        if name != expected_name:
            self._stop(f"unsupported_dmm_exchange_order:{self.stage}:{name}")
            return True
        if self.stage in ("dynamic_alloc", "allocate", "receive"):
            pointer = args[2] if self.stage == "dynamic_alloc" else args[1]
            if not STACK <= pointer <= STACK + 0x10000 - 4 or pointer % 4:
                self._stop(f"unsupported_dmm_output_pointer:{pointer:#x}")
                return True
        if self.stage == "dynamic_alloc" and args[:2] == [0x2A, 0x3D]:
            self.put_word(args[2], self.qid)
            self.stage = "allocate"
        elif self.stage == "allocate" and (args[0], args[2], args[3]) == (0x61003C, 24, 1):
            self.uc.mem_write(self.message - 4, b"\xA5" * 28)
            self.put_word(args[1], self.message)
            self.stage = "send"
        elif self.stage == "send" and args[:2] == [0x61003C, self.message]:
            expected = DMM_REQUEST.pack(
                operation, self.buffer.metadata.handle,
                self.buffer.metadata.unit_type if operation == 2 else 0,
                0, 0, 0, 0, 1 if operation == 2 else 2)
            actual = bytes(self.uc.mem_read(self.message, 24))
            if actual != expected or self.word(self.message - 4) != self.qid:
                self._stop("unsupported_dmm_request_or_reply_queue")
                return True
            completion = bytearray(self.buffer.completion if operation == 2 else
                                   DMM_COMPLETION.pack(0x8004, 0, self.buffer.metadata.handle,
                                                       0, 0, 0, 0, 0, 0))
            struct.pack_into("<h", completion, 2, self.statuses[self.exchange_index])
            self.uc.mem_write(self.reply, bytes(completion))
            self.exchanges.append({"operation": operation, "request_hex": actual.hex(),
                                   "completion_hex": completion.hex(), "reply_queue": self.qid})
            self.stage = "receive"
        elif self.stage == "receive" and (args[0], args[2]) == (self.qid, 0xFFFFFFFF):
            self.put_word(args[1], self.reply)
            if self.statuses[self.exchange_index] == 0:
                if operation == 2:
                    self.buffer.acquire()
                else:
                    self.buffer.release_consumer()
            self.stage = "free"
        elif self.stage == "free" and args[:2] == [self.qid, self.reply]:
            self.uc.mem_write(self.reply, b"\xA5" * 24)
            self.stage = "dynamic_free"
        elif self.stage == "dynamic_free" and args[:3] == [0x4A, 0, self.qid]:
            self.exchange_index += 1
            self.stage = "dynamic_alloc"
        else:
            self._stop(f"unsupported_dmm_fixture_arguments:{name}:{args}")
            return True
        self.services.append({"fixture": "constructed_dmm_osal_exchange", "symbol": name,
                              "operation": operation, "args": args})
        self._return(0)
        return True

    def require_released_metadata(self) -> None:
        if (self.exchange_index != 2 or self.stage != "dynamic_alloc"
                or self.buffer.consumer != "released" or any(self.statuses)):
            raise ValueError("DMM metadata exchange did not release a successful consumer")


def command_table(image: LibraryImage) -> list[dict]:
    result = []
    for index in range(0x60B):
        address = 0x19214 + index * 0x48
        row = image.read(address, 0x48)
        queue = struct.unpack_from("<I", row, 0x44)[0]
        if queue != QUEUE:
            continue
        category, command = struct.unpack_from("<HH", row)
        result.append({"index": index, "linked_address": address, "category": category,
                       "command": command, "queue": queue,
                       "name": row[4:0x44].split(b"\0", 1)[0].decode("ascii")})
    if [(r["category"], r["command"]) for r in result] != [
            (0x1001, 0x4000 | command) for command in range(1, 0x13)]:
        raise ValueError("Packaged Jiritsu command table differs from the analyzed profile")
    return result


def request_control(image: LibraryImage, packet: bytes, *, level: int = 3) -> dict:
    command, sequence, payload = unpack_request(packet)
    run = OfflineReplay(image, (TRANSACTION, SERIALIZE),
                        ((0x11F44, 0x11FA4), (0x12040, 0x120A6)))
    run.stream = payload
    run.uc.mem_write(DATA + 0x1000, packet[:HEADER.size])
    run.uc.mem_write(DATA + 0x1100, packet[HEADER.size:HEADER.size + 4])
    run.uc.mem_write(BASE + image.values["adjCodeLevel"], bytes([level]))
    run.boundaries["osal_valloc_msg_wait"] = "before_osal_allocation"
    transaction = run.call(image.functions[TRANSACTION][0],
                           (DATA, DATA + 0x1000, 0, DATA + 0x1100),
                           stop="returned" if level == 1 else "before_osal_allocation")
    if level == 1:
        if transaction["args_or_return"][0] != 0x80:
            raise AssertionError("Original privilege-level rejection changed")
        return {"declared_adj_code_level": level, "transaction": transaction}
    if transaction["args_or_return"] != [QUEUE, DATA + 0x150, 0x140, 1]:
        raise AssertionError("Original OSAL allocation arguments changed")
    run.put_word(DATA + 0x154, DATA + 0x2000)
    run.uc.mem_write(DATA + 0x158, b"\x01")
    encoder = run.call(0x12041, (DATA + 0xC,))
    actual = bytes(run.uc.mem_read(DATA + 0x2000, 0x140))
    expected = OSAL_HEADER.pack(1, 1, sequence, 4 + len(payload), 0x40, 0,
                                0, 0, 0, 0, 0, 0x1001, command)
    expected += payload + bytes(INLINE_SIZE - len(payload))
    if actual != expected:
        raise AssertionError("Original OSAL encoder differs from the constructed packet contract")
    return {"declared_adj_code_level": level, "transaction": transaction,
            "encoder": encoder, "osal_hex": actual.hex()}


def dispatch_control(image: LibraryImage, command: int, *, phase: int = 1) -> dict:
    run = OfflineReplay(image, windows=((0x7014, 0x71D8),))
    for name, (entry, _) in image.functions.items():
        if name.startswith("cmd_"):
            run.entries[BASE + (entry & ~1)] = f"before_handler:{name}"
    run.uc.mem_write(DATA, bytes([phase, 1]))
    run.uc.mem_write(DATA + 0x1A, struct.pack("<H", command))
    expected = {
        1: "cmd_mode", 2: "cmd_mount", 3: "cmd_format", 4: "cmd_start", 5: "cmd_debug",
        9: "cmd_file_delete", 14: "cmd_factory", 18: "cmd_file_delete2",
    }.get(command, "cmd_high_capacity_send_file" if command in (6, 7, 12, 15, 16)
          else "cmd_high_capacity_get_file")
    return {"command": command, "phase": phase,
            **run.call(0x7015, (DATA,), stop=f"before_handler:{expected}")}


def payload_dispatch_controls(image: LibraryImage) -> list[dict]:
    result = []
    for command, wrapper, handler in (
            (0xF, "cmd_high_capacity_send_file", "adj_cmd_set_file_path"),
            (0x10, "cmd_high_capacity_send_file", "adj_cmd_set_file2"),
            (0x11, "cmd_high_capacity_get_file", "adj_cmd_get_file2")):
        run = OfflineReplay(image, (wrapper,))
        run.entries[BASE + (image.functions[handler][0] & ~1)] = f"before_handler:{handler}"
        run.uc.mem_write(DATA, b"\x04\x01")
        run.uc.mem_write(DATA + 0x1A, struct.pack("<H", command))
        run.put_word(BASE + 0x2F580, DATA + 0x1000)
        result.append({"command": command, "phase": 4,
                       **run.call(image.functions[wrapper][0], (DATA,),
                                  stop=f"before_handler:{handler}")})
    return result


def builder_controls(image: LibraryImage) -> list[dict]:
    getters = ("_ZN6DmmMgr13GetUnitHandleEv", "_ZN6DmmMgr11GetUnitTypeEv")
    result = []
    for name, mode, transfer, length in (
            ("packet_set_error", 0, 0, 4),
            ("packet_set_process_end", 8, 0, 4),
            ("packet_set_process_req_raw_data", 2, 1, 0x23),
            ("packet_set_process_res_raw_data", 8, 1, 0x23),
            ("packet_set_fin_del_data", 0x20, 0, 4)):
        run = OfflineReplay(image, (name, *getters))
        run.uc.mem_write(DATA, b"\xA5" * 32)
        run.uc.mem_write(DATA + 2, struct.pack("<H", 0x1234))
        run.put_word(BASE + 0x2F580, DATA + 0x1000)
        run.uc.mem_write(DATA + 0x1008, struct.pack("<H", 0x5678))
        run.put_word(DATA + 0x1010, 0x02030405)
        run.put_word(BASE + image.values["g_raw_res_size"], length)
        response = 0x81 if mode == 0 else 1
        execution = run.call(image.functions[name][0], (DATA, response, length))
        actual = bytes(run.uc.mem_read(DATA, 16))
        expected = struct.pack("<BBHBBHII", mode, response, 0x1234, transfer, 0,
                               0x5678 if transfer else 0,
                               0x02030405 if transfer else 0, length)
        if actual != expected or bytes(run.uc.mem_read(DATA + 16, 16)) != b"\xA5" * 16:
            raise AssertionError(f"Original response builder contract changed: {name}")
        result.append({"name": name, "osal_header_hex": actual.hex(), "execution": execution})
    return result


def continuation_controls(image: LibraryImage, initial: bytes, reply: bytes) -> list[dict]:
    result = []
    for phase in (4, 0x10):
        run = OfflineReplay(image, windows=((0x12040, 0x120A6),))
        run.uc.mem_write(DATA, initial)
        run.put_word(DATA + 0x148, DATA + 0x2000)
        run.uc.mem_write(DATA + 0x14C, bytes([phase]))
        run.uc.mem_write(DATA + 0x2000, reply)
        execution = run.call(0x12041, (DATA,))
        actual = bytes(run.uc.mem_read(DATA + 0x2000, 0x140))
        expected = bytearray(initial)
        expected[0] = phase
        expected[0xC] = reply[4]
        expected[0xE:0x10] = reply[6:8]
        expected[0x10:0x18] = reply[8:16]
        if actual != expected:
            raise AssertionError("Original continuation metadata copy changed")
        result.append({"phase": phase, "osal_hex": actual.hex(), "execution": execution})
    return result


def _serialize_response(run: OfflineReplay, payload: bytes | None, *,
                        response: int, sink_chunk: int, start: int = DATA + 0x4000,
                        io_symbol: str = SEND) -> dict:
    image = run.image
    run.sink = bytearray()
    run.sink_chunk = sink_chunk
    run.lock_fixtures = True
    header = HEADER.pack(0, 0x40, 0x1234, 0, 0, 0, response)
    run.uc.mem_write(DATA, header)
    if payload is not None:
        if io_symbol == SEND:
            run.uc.mem_write(start, payload)
        vtable = BASE + image.values["_ZTV10MemoryData"] + 8
        # Explicit MemoryData fixture; fields follow its original constructor.
        run.uc.mem_write(DATA + 0x1000, struct.pack(
            "<6I", vtable, BASE + image.values[io_symbol], start, start + len(payload), start, 0))
    execution = run.call(image.functions[RESPONSE_FUNCTIONS[0]][0],
                         (DATA, DATA + 0x1000 if payload is not None else 0, 0))
    body = payload if payload is not None else b""
    expected = HEADER.pack(len(body), 0x40, 0x1234, 3, 0, 0, response) + body
    if bytes(run.sink) != expected:
        raise AssertionError("Original Senser response serialization changed")
    return {"packet_hex": bytes(run.sink).hex(), "execution": execution,
            "sink_max_chunk": sink_chunk, "payload_origin": "constructed fixture"}


def response_control(image: LibraryImage, payload: bytes | None, *,
                     response: int = 1, sink_chunk: int = 0) -> dict:
    return _serialize_response(OfflineReplay(image, RESPONSE_FUNCTIONS), payload,
                               response=response, sink_chunk=sink_chunk)


def reply_decode_control(image: LibraryImage, header: bytes, payload: bytes, *,
                         raw_boundary: bool = False) -> dict:
    run = OfflineReplay(image, windows=((0x11FA4, 0x11FBA), (0x12B50, 0x13090)))
    run.put_word(DATA + 0x154, DATA + 0x2000)
    run.uc.mem_write(DATA + 0x2000, header + payload)
    if raw_boundary:
        run.entries[BASE + 0x120D0] = "before_dmm_metadata"
    execution = run.call(0x12B51, (DATA + 0xC, DATA + 0x1000),
                         stop="before_dmm_metadata" if raw_boundary else "returned")
    state = bytes(run.uc.mem_read(DATA + 0x158, 2))
    result = {"osal_header_hex": header.hex(), "execution": execution,
              "next_phase": state[0], "response": state[1]}
    if raw_boundary:
        if state != b"\x10\x01":
            raise AssertionError("Original raw reply did not stop at the DMM boundary")
    else:
        expected_pointer = DATA + 0x2010 if header[1] == 1 else 0
        if execution["args_or_return"][0] != expected_pointer or state != bytes([0, header[1]]):
            raise AssertionError("Original inline reply decoding changed")
        result["payload_hex"] = (bytes(run.uc.mem_read(expected_pointer, len(payload))).hex()
                                 if expected_pointer else "")
    return result


def dmm_decode_control(image: LibraryImage, buffer: DmmBufferFixture, header: bytes, *,
                       statuses=(0, 0)) -> tuple[DmmReplay, dict]:
    buffer.bind_reply(header)
    run = DmmReplay(image, buffer, statuses=statuses)
    run.put_word(DATA + 0x154, DATA + 0x2000)
    run.uc.mem_write(DATA + 0x2000, header)
    execution = run.call(0x12B51, (DATA + 0xC, DATA + 0x1000))
    return run, {"execution": execution, "exchanges": run.exchanges,
                 "next_phase_and_response_hex": bytes(run.uc.mem_read(DATA + 0x158, 2)).hex()}


def dmm_reply_controls(image: LibraryImage, header: bytes) -> dict:
    raw_size = struct.unpack_from("<I", header, 12)[0]
    payload = b"\0\0\0\0DMM-OFFLINE\n" + bytes(range(raw_size - 16))
    supplied = payload + b"\xA5" * 8
    completion = DMM_COMPLETION.pack(0x8002, 0, DMM_HANDLE, 0, 1, 0,
                                      DMM_UNIT_TYPE, DMM_ADDRESS, len(supplied))
    buffer = DmmBufferFixture(completion, supplied, raw_size=raw_size)
    run, decoded = dmm_decode_control(image, buffer, header)
    if (decoded["execution"]["args_or_return"][0] != DMM_ADDRESS
            or decoded["next_phase_and_response_hex"] != "1001"):
        raise AssertionError("Original raw decoder pointer/continuation changed")
    run.require_released_metadata()
    unmapped = not any(begin <= DMM_ADDRESS <= end for begin, end, _ in run.uc.mem_regions())
    if not unmapped:
        raise AssertionError("DMM fixture address must not be mapped into guest memory")
    serialized = _serialize_response(run, payload, response=1, sink_chunk=5,
                                     start=DMM_ADDRESS, io_symbol=PHYSICAL_SEND)
    borrowed = bytes(run.uc.mem_read(DATA + 0x1000, 24))
    release = run.call(image.functions["_ZN10MemoryData7ReleaseEv"][0], (DATA + 0x1000,))
    if bytes(run.uc.mem_read(DATA + 0x1000, 24)) != borrowed:
        raise AssertionError("Original borrowed MemoryData unexpectedly released its buffer")
    buffer.retire()
    try:
        buffer.read(DMM_ADDRESS, 1)
    except ValueError as error:
        retired_read = str(error)
    else:
        raise AssertionError("Retired DMM fixture remained readable")
    failures = []
    for statuses in ((-1, 0), (0, -1)):
        rejected = DmmBufferFixture(completion, supplied, raw_size=raw_size)
        failed_run, observation = dmm_decode_control(image, rejected, header, statuses=statuses)
        if (observation["execution"]["args_or_return"][0] != (0 if statuses[0] else DMM_ADDRESS)
                or len(failed_run.exchanges) != (1 if statuses[0] else 2)):
            raise AssertionError("Original DMM error-propagation control changed")
        try:
            failed_run.require_released_metadata()
        except ValueError as error:
            failures.append({"statuses": list(statuses), "original_observation": observation,
                             "host_guard": str(error), "serialized": False})
        else:
            raise AssertionError("Failed DMM completion was accepted for serialization")
    return {
        "name": "synthetic-dmm-reply", "kind": "response", **serialized,
        "decode_control": decoded, "borrowed_memory_release": release,
        "lifetime": buffer.events, "retired_read_guard": retired_read, "failure_controls": failures,
        "physical_address_is_unmapped": unmapped,
    }


def dmm_owner_boundary_control(image: LibraryImage) -> dict:
    name = "_ZN6DmmMgr8dmm_freeEv"
    run = OfflineReplay(image, ("cmd_high_capacity_get_file",))
    run.entries[BASE + (image.functions[name][0] & ~1)] = "before_owner_dmm_free"
    run.put_word(BASE + 0x2F580, DATA + 0x1000)
    run.uc.mem_write(DATA, b"\x10\x01")
    return run.call(image.functions["cmd_high_capacity_get_file"][0], (DATA,),
                    stop="before_owner_dmm_free")


def dmm_bounds_controls(image: LibraryImage) -> list[dict]:
    result = []
    supplied = bytes(range(256)) * (MAX_DMM_BYTES // 256)
    completion = DMM_COMPLETION.pack(0x8002, 0, DMM_HANDLE, 0, 1, 0,
                                      DMM_UNIT_TYPE, DMM_ADDRESS, len(supplied))
    for raw_size in (0, 1, MAX_DMM_BYTES):
        buffer = DmmBufferFixture(completion, supplied, raw_size=raw_size)
        header = struct.pack("<BBHBBHII", 8, 1, 0x1234, 1, 0,
                             DMM_HANDLE, DMM_UNIT_TYPE, raw_size)
        run, decoded = dmm_decode_control(image, buffer, header)
        run.require_released_metadata()
        if decoded["execution"]["args_or_return"][0] != DMM_ADDRESS:
            raise AssertionError("Original DMM bounds control did not return its declared address")
        serialized = _serialize_response(run, supplied[:raw_size], response=1, sink_chunk=512,
                                         start=DMM_ADDRESS, io_symbol=PHYSICAL_SEND)
        buffer.retire()
        result.append({"raw_size": raw_size, "capacity": len(supplied),
                       "decode_control": decoded, **serialized})
    return result


def generate(jiritsu: LibraryImage, senser: LibraryImage, *, dmm_fixtures: bool = False) -> dict:
    fixtures = []
    for command, name, payload in (
            (0xF, "set-file-path", b"\x00offline\\fixture.txt"),
            (0x10, "set-file2", b"\x01\xff\x00OFFLINE"),
            (0x11, "get-file2", b"\x01\xff\x00"),
            (0x12, "file-delete2", b"\x01\xff")):
        packet = request_packet(command, payload, sequence=0x1234)
        fixtures.append({"name": name, "kind": "request", "command": command,
                         "packet_hex": packet.hex(), "payload_origin": "constructed fixture",
                         "control": request_control(senser, packet)})
    builders = builder_controls(jiritsu)
    for name, builder, payload in (
            ("parameter-error", builders[1], struct.pack("<I", 0x10000201)),
            ("rejected-command", builders[0], b"")):
        decoded = reply_decode_control(senser, bytes.fromhex(builder["osal_header_hex"]), payload)
        fixtures.append({
            "name": name, "kind": "response", "builder": builder["name"], "decode_control": decoded,
            **response_control(senser, bytes.fromhex(decoded["payload_hex"]) if payload else None,
                               response=decoded["response"]),
        })
    fixtures.append({"name": "synthetic-file-reply", "kind": "response",
                     **response_control(senser, b"\0\0\0\0OFFLINE\n", sink_chunk=5)})
    report = {
        "schema_version": 1, "scope": "offline original-code controls with constructed fixtures",
        "usb_enabled": False, "command_table": command_table(senser), "fixtures": fixtures,
        "dispatch_controls": [dispatch_control(jiritsu, command) for command in range(1, 0x13)],
        "payload_dispatch_controls": payload_dispatch_controls(jiritsu),
        "builder_controls": builders,
        "raw_reply_boundary": reply_decode_control(
            senser, bytes.fromhex(builders[3]["osal_header_hex"]), b"", raw_boundary=True),
        "continuation_controls": continuation_controls(
            senser, bytes.fromhex(fixtures[0]["control"]["osal_hex"]),
            bytes.fromhex(builders[3]["osal_header_hex"])),
        "privilege_rejection_control": request_control(
            senser, bytes.fromhex(fixtures[0]["packet_hex"]), level=1),
        "limitations": [
            "Request version 0, zero micon/offset and privilege level 3 are declared fixtures, not authentication.",
            "Only single-packet requests with at most 292 inline argument bytes are generated.",
            "OSAL allocation/send, DMM allocation/mapping, file access, mounts and USB are not serviced.",
            "Response payloads, MemoryData objects, DMM metadata, log calls and lock calls are explicit fixtures.",
            "Unresolved imported data reads stop; pure memory-helper fixtures are bounded to 4096 bytes.",
            "SenserIfSend is a record-only memory sink; physical transfer and camera acceptance are unverified.",
            "No firmware initialization, complete service transaction or cross-model compatibility is claimed.",
        ],
    }
    if dmm_fixtures:
        raw = dmm_reply_controls(senser, bytes.fromhex(builders[3]["osal_header_hex"]))
        report["fixtures"].append(raw)
        report["schema_version"] = 2
        report["dmm_controls"] = {
            "policy": "explicit single-extent constructed metadata and externally owned bytes",
            "owner_release_boundary": dmm_owner_boundary_control(jiritsu),
            "bounds_controls": dmm_bounds_controls(senser),
            "bounds_are_host_admission_not_firmware_validation": True,
        }
        report["limitations"][2] = (
            "Only two exact constructed DMM OSAL exchanges are supplied; no runtime DMM "
            "allocation, physical mapping, filesystem, mount or USB service is implemented.")
        report["limitations"].append(
            "The physical-send entry is a bounded byte-fixture sink; owner teardown stops before dmm_free.")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", type=Path, default=LIBRARY_DIR)
    parser.add_argument("--updater", type=Path,
                        default=PROJECT_ROOT / "firmware" / "Update_ILCE6400AV101.exe")
    parser.add_argument("--report-dir", type=Path, required=True, help="New directory under artifacts")
    parser.add_argument("--dmm-fixtures", action="store_true",
                        help="Opt in to bounded DMM metadata/lifetime and raw byte-fixture controls")
    args = parser.parse_args(argv)
    if not args.library_dir.resolve().is_relative_to((PROJECT_ROOT / "firmware").resolve()):
        parser.error("Library directory must remain under the project's firmware namespace")
    output = args.report_dir.resolve()
    artifacts = (PROJECT_ROOT / "artifacts").resolve()
    if output == artifacts or not output.is_relative_to(artifacts):
        parser.error("Report directory must be a new directory under artifacts")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"status": "running", "usb_enabled": False, "dmm_fixtures": args.dmm_fixtures, "inputs": [],
                "replay_command": [
                    sys.executable, "-m", "tools.research.a6400a_jiritsu_packets",
                    "--library-dir", str(args.library_dir.resolve()),
                    "--updater", str(args.updater.resolve()), "--report-dir", str(output)],
                "tools": {"python": platform.python_version(),
                          **{name: version(name) for name in ("unicorn", "capstone", "pyelftools")}},
                "cpu_fixture": "Cortex-A9; not a physical CPU identity observation",
                "tool_sha256": digest(Path(__file__)),
                "dmm_helper_sha256": digest(PROJECT_ROOT / "tools" / "research" / "a6400a_dmm.py"),
                "static_helper_sha256": digest(PROJECT_ROOT / "tools" / "research" / "startup_trace.py")}
    if args.dmm_fixtures:
        manifest["replay_command"].append("--dmm-fixtures")
        manifest["dmm_loader_sha256"] = digest(PROJECT_ROOT / "tools" / "research" / "dmm_config_trace.py")
        manifest["module_inspector_sha256"] = digest(PROJECT_ROOT / "tools" / "research" / "storage_trace.py")
    try:
        if digest(args.updater) != UPDATER_SHA256:
            raise ValueError("Updater input identity mismatch")
        manifest["inputs"].append({"path": str(args.updater.resolve()), "sha256": UPDATER_SHA256})
        images = {}
        for name, expected in LIBRARY_HASHES.items():
            images[name] = LibraryImage(args.library_dir / name, expected)
            manifest["inputs"].append({"path": str(images[name].path.resolve()), "sha256": expected})
        if args.dmm_fixtures:
            module = args.library_dir.parent / "kmod" / "dmm.ko"
            if digest(module) != DMM_SHA256:
                raise ValueError("A6400A DMM input identity mismatch")
            manifest["inputs"].append({"path": str(module.resolve()), "sha256": DMM_SHA256})
        report = generate(images["libJiritsu.so"], images["libsencore.so"],
                          dmm_fixtures=args.dmm_fixtures)
        if args.dmm_fixtures:
            report["dmm_controls"]["original_kernel_lifetime"] = kernel_lifetime_controls(module)
        static = [
            analyze(images["libJiritsu.so"].path, [], (
                "jiritsu_init", "cmd_high_capacity_send_file", "cmd_high_capacity_get_file",
                "adj_cmd_set_file_path", "adj_cmd_set_file2", "adj_cmd_get_file2",
                "cmd_file_delete2", "get_access_filename",
                *(row["name"] for row in report["builder_controls"])),
                (("jiritsu_dispatch", 0x7015, 0x1C4),)),
            analyze(images["libsencore.so"].path, [], (
                TRANSACTION, "_Z17AdjustControlFuncPK12SenserPacket",
                "_Z12SendResponseP12SenserPacketP10PacketDataj", SERIALIZE),
                (("osal_request_encoder", 0x12041, 0x70),
                 ("raw_transfer_normalizer", 0x11FA5, 0x16),
                 ("osal_reply_decoder", 0x12B51, 0x540))),
        ]
        if args.dmm_fixtures:
            from tools.research.storage_trace import inspect_module
            static.append({"path": module.relative_to(PROJECT_ROOT).as_posix(), "sha256": DMM_SHA256,
                           "blocks": inspect_module(module.read_bytes(), (
                               "dmm_ctrl_AllocUnitShareHdlr", "dmm_ctrl_FreeUnitHdlr",
                               "dmm_unit_ctrl_MakeHandle", "dmm_unit_ctrl_Share",
                               "dmm_unit_ctrl_Open", "dmm_unit_ctrl_Close", "dmm_unit_ctrl_Free"))})
            static.append(analyze(images["libsencore.so"].path, [], ("_ZN10MemoryData7ReleaseEv",),
                                  (("dmm_metadata_exchange", 0x120D1, 0x170),)))
        for source in manifest["inputs"]:
            if digest(Path(source["path"])) != source["sha256"]:
                raise ValueError("Source changed during offline replay")
        for name, data in (("fixtures.json", report), ("original-instructions.json", static)):
            (output / name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        for fixture in report["fixtures"]:
            (output / (fixture["name"] + ".bin")).write_bytes(bytes.fromhex(fixture["packet_hex"]))
        manifest.update(status="completed_offline_controls", sources_unchanged=True,
                        command_count=len(report["command_table"]), fixture_count=len(report["fixtures"]))
    except (OSError, ValueError, RuntimeError, AssertionError, UcError) as error:
        manifest.update(status="failed", error=f"{type(error).__name__}: {error}")
        print(manifest["error"], file=sys.stderr)
        return 2
    finally:
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{output}: {manifest['command_count']} mapped commands, "
          f"{manifest['fixture_count']} offline packet fixtures; USB disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
