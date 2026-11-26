"""Offline original-instruction controls for the a6400A 1.01 USB service boundary."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import struct
from collections.abc import Callable

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE, UC_MODE_THUMB, UC_QUERY_TIMEOUT, Uc, UcError
from unicorn.arm_const import (
    UC_ARM_REG_CPSR, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0,
    UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R5, UC_ARM_REG_R9,
    UC_ARM_REG_SP, UC_CPU_ARM_CORTEX_A9,
)

from project_paths import PROJECT_ROOT
from tools.research.startup_trace import decode_block, plt_symbols, read_va

PACKAGE = PROJECT_ROOT / "firmware" / "extracted_a6400a_v101"
SYSTEM = Path("firmware.tar_unpacked") / "0700_part_image" / "dev" / "nflasha15_unpacked" / "lib"
HASHES = {
    "libsencore.so": "08804d8752c0bdfbad6e3f212cc1feaf2145e71e866a64c3f94bc4c2eca36089",
    "libpro00.so": "3158a653ce16692aec7a941d5ee332035b390f2d516fc6d9576c378d752efa9f",
    "libusbcmd.so": "23b94deb094d31f160cfe1d46330f2425e94d4801dfccb66720c4cf4b81cfb7e",
    "libInfraDevInfoSender.so": "db4a0c97ccbd5fad6acec2354dbfeea12c50b2e93e7d346d02e280d7f70d896e",
    "libInfraKikiLogSender.so": "907cad7cd318475da6f2665ac9367089d89c368e3b50564f425361bcdc045f1c",
    "libInfraExtBackupCommunicator.so": "eb6ae4be0c868e4c0e11014d7c7636dddafc5e32481628dcb5e1b325c8465f08",
    "libInfraLensCommunicator.so": "ba005041abbccc47ed8ee8503a8880540cf6fbd6c2b7ec2a95e1bb902cad0923",
    "libInfraNetworkServiceInfo.so": "bbd1b4c8f459fe8c4769f570abc474b12ff1faa8b36658a3fd57d0debbdc29d4",
}
IMAGE, DATA, STACK, RETURN = 0x01000000, 0x02000000, 0x02100000, 0x02200000
REGISTERS = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
PACKET = struct.Struct("<IHHBBBB")
LEVELS = ("pFunc_level_A", "pFunc_level_B", "pFunc_level_C")
CHECK = "_Z10ParseCheckPK12SenserPacket"
PARSE = "_Z5ParsePK12SenserPacket"
COMPLETE = "_ZN19WorkerThreadManager10onCompleteEi"
REPLY = "_Z12SendResponseP12SenserPacketP10PacketDataj"
NO_ACTION = "_ZN8NoAction9DoExecuteEP12SenserPacket"
NO_ACTION_CTOR = "_ZN8NoActionC1EhP10PacketData"
DISCARD_CTOR = "_ZN8NoAction15DiscardRecvDataC1Ev"
SERIALIZE_HEADER = "_Z9SerializeI15CommandCodeTypeEvPFiPhjERT_"
PRODUCT = "_Z15ProductInfoFuncPK12SenserPacket"
TEST_MODE = "_Z8TestModePK12SenserPacket"
TRANSACTION = "_ZN29SubsystemCommunicationCommand16TransactionStartEPK12SenserPacketPKvS4_"
SERIALIZE = "_Z9SerializePFiPhjEPvj"


class Library:
    def __init__(self, raw: bytes, expected_sha: str):
        if hashlib.sha256(raw).hexdigest() != expected_sha:
            raise ValueError("Original library identity mismatch")
        self.raw = raw
        self.sha256 = expected_sha
        self.elf = ELFFile(io.BytesIO(raw))
        if (self.elf.elfclass, self.elf.little_endian,
                self.elf["e_machine"], self.elf["e_type"]) != (32, True, "EM_ARM", "ET_DYN"):
            raise ValueError("Expected an ELF32 little-endian ARM shared library")
        table = self.elf.get_section_by_name(".dynsym")
        if table is None:
            raise ValueError("Missing original dynamic symbol table")
        self.symbols = {symbol.name: symbol for symbol in table.iter_symbols()
                        if isinstance(symbol["st_shndx"], int)}
        self.plt = plt_symbols(self.elf)

    def function(self, name: str) -> tuple[int, bytes]:
        symbol = self.symbols.get(name)
        if (symbol is None or symbol["st_info"]["type"] != "STT_FUNC"
                or not symbol["st_value"] & 1 or not 0 < symbol["st_size"] <= 16384):
            raise ValueError("Absent or unsupported original Thumb function: " + name)
        address = symbol["st_value"] & ~1
        code = read_va(self.elf, self.raw, address, symbol["st_size"])
        if code is None:
            raise ValueError("Original function is not file-backed: " + name)
        return address, code


class ThumbWindow:
    """Selected functions, explicit local bindings and bounded callee fixtures only."""

    def __init__(self, library: Library, *functions: str):
        self.library = library
        self.cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        self.cpu.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A9)
        self.allowed: list[tuple[int, int]] = []
        self.redirects: dict[int, int] = {}
        self.fixtures: dict[int, tuple[str, Callable[[], None]]] = {}
        self.calls: list[dict] = []
        self.writes: list[dict] = []
        self.instructions = 0
        self.stop: str | None = None
        self.stop_before: int | None = None
        self.limit = 20000
        segments = [segment for segment in library.elf.iter_segments()
                    if segment["p_type"] == "PT_LOAD" and segment["p_memsz"]]
        pages: dict[int, int] = {}
        for segment in segments:
            start = segment["p_vaddr"] & ~4095
            end = (segment["p_vaddr"] + segment["p_memsz"] + 4095) & ~4095
            if not 0 <= start < end <= 0x01000000:
                raise ValueError("Original segment exceeds the declared image namespace")
            flags = segment["p_flags"]
            permissions = (1 if flags & 4 else 0) | (2 if flags & 2 else 0) | (4 if flags & 1 else 0)
            for page in range(start, end, 4096):
                pages[page] = pages.get(page, 0) | permissions
        for page, permissions in sorted(pages.items()):
            self.cpu.mem_map(IMAGE + page, 4096, permissions)
        for segment in segments:
            self.cpu.mem_write(IMAGE + segment["p_vaddr"], segment.data())
        self.cpu.mem_map(DATA, 0x10000, 3)
        self.cpu.mem_map(STACK, 0x10000, 3)
        self.cpu.mem_map(RETURN, 4096, 5)
        for name in functions:
            self.allow(name)
        self.entry = library.function(functions[0])[0]
        self.cpu.hook_add(UC_HOOK_CODE, self._code)
        self.cpu.hook_add(UC_HOOK_MEM_WRITE, self._write)

    def allow(self, name: str) -> None:
        address, code = self.library.function(name)
        self.allowed.append((IMAGE + address, IMAGE + address + len(code)))

    def plt_address(self, name: str) -> int:
        matches = [address for address, symbol in self.library.plt.items() if symbol == name]
        if len(matches) != 1:
            raise ValueError("Absent or ambiguous original PLT entry: " + name)
        return IMAGE + matches[0]

    def original(self, name: str) -> None:
        self.allow(name)
        self.redirects[self.plt_address(name)] = IMAGE + self.library.function(name)[0] + 1

    def fixture(self, name: str, action: Callable[[], None]) -> None:
        self.fixtures[self.plt_address(name)] = (name, action)

    def globals(self, *names: str) -> dict[str, int]:
        section = self.library.elf.get_section_by_name(".rel.dyn")
        if section is None:
            raise ValueError("Missing original local relocations")
        symbols = self.library.elf.get_section(section["sh_link"])
        result = {}
        for name in names:
            rows = [row for row in section.iter_relocations()
                    if row["r_info_type"] == 21
                    and symbols.get_symbol(row["r_info_sym"]).name == name]
            symbol = self.library.symbols.get(name)
            if len(rows) != 1 or symbol is None:
                raise ValueError("Absent or ambiguous local GOT binding: " + name)
            result[name] = IMAGE + symbol["st_value"]
            self.write32(IMAGE + rows[0]["r_offset"], result[name])
        return result

    def write32(self, address: int, value: int) -> None:
        self.cpu.mem_write(address, struct.pack("<I", value))

    def read32(self, address: int) -> int:
        return struct.unpack("<I", bytes(self.cpu.mem_read(address, 4)))[0]

    def result(self, value: int) -> None:
        self.cpu.reg_write(UC_ARM_REG_R0, value & 0xFFFFFFFF)
        self.cpu.reg_write(UC_ARM_REG_PC, self.cpu.reg_read(UC_ARM_REG_LR))

    def arguments(self) -> list[int]:
        return [self.cpu.reg_read(register) for register in REGISTERS]

    def _write(self, cpu, access, address, size, value, user) -> None:
        if not STACK <= address < address + size <= STACK + 0x10000:
            self.writes.append({"address": address, "bytes": size, "value": value})

    def _code(self, cpu, address, size, user) -> None:
        self.instructions += 1
        if address == self.stop_before:
            self.stop = "declared_boundary"
            cpu.emu_stop()
        elif address in self.fixtures:
            name, action = self.fixtures[address]
            self.calls.append({"fixture": name, "arguments": self.arguments(),
                               "real_external_operation": False})
            action()
        elif address in self.redirects:
            cpu.reg_write(UC_ARM_REG_PC, self.redirects[address])
        elif not any(start <= address < address + size <= end for start, end in self.allowed):
            self.stop = "unsupported_pc"
            cpu.emu_stop()

    def run(self, registers: dict[int, int], *, start: int | None = None,
            stop_before: int | None = None) -> dict:
        self.stop_before = stop_before
        self.stop = None
        self.instructions = 0
        self.calls.clear()
        self.writes.clear()
        for register, value in {
            UC_ARM_REG_CPSR: 0x30, UC_ARM_REG_SP: STACK + 0xFFF0,
            UC_ARM_REG_LR: RETURN | 1, **registers,
        }.items():
            self.cpu.reg_write(register, value)
        try:
            self.cpu.emu_start((IMAGE + self.entry if start is None else start) | 1,
                               RETURN, timeout=500_000, count=self.limit)
        except UcError as error:
            raise RuntimeError(
                f"Original control fault at PC {self.cpu.reg_read(UC_ARM_REG_PC):#x}, "
                f"arguments {self.arguments()}: {error}"
            ) from error
        pc = self.cpu.reg_read(UC_ARM_REG_PC)
        completed = (self.stop == "declared_boundary" if stop_before is not None
                     else self.stop is None and pc == RETURN)
        if not completed or self.cpu.query(UC_QUERY_TIMEOUT):
            raise RuntimeError(
                f"Original control did not reach its declared stop: PC {pc:#x}, "
                f"reason {self.stop}, arguments {self.arguments()}"
            )
        return {"stop": self.stop or "bounded_return", "pc": pc,
                "arguments": self.arguments(), "instructions": self.instructions,
                "callee_fixtures": list(self.calls), "non_stack_writes": list(self.writes)}


def service_tables(core: Library) -> dict:
    section = core.elf.get_section_by_name(".rel.dyn")
    if section is None:
        raise ValueError("Missing service-table relocations")
    symbols = core.elf.get_section(section["sh_link"])
    relocations = {row["r_offset"]: row for row in section.iter_relocations()}
    tables = {}
    for name in LEVELS:
        symbol = core.symbols[name]
        size = symbol["st_size"]
        if not size or size % 8:
            raise ValueError("Unsupported service table extent")
        entries = []
        seen = set()
        for offset in range(0, size, 8):
            address = symbol["st_value"] + offset
            raw = read_va(core.elf, core.raw, address, 8)
            if raw is None:
                raise ValueError("Service table is not file-backed")
            function, padding, pointer = struct.unpack("<HHI", raw)
            relocation = relocations.get(address + 4)
            if not function and not pointer and relocation is None:
                if offset != size - 8:
                    raise ValueError("Data follows the service-table sentinel")
                break
            if padding or pointer or function in seen or relocation is None or relocation["r_info_type"] != 2:
                raise ValueError("Unsupported service-table relocation or row")
            target = symbols.get_symbol(relocation["r_info_sym"])
            if not isinstance(target["st_shndx"], int) or target["st_info"]["type"] != "STT_FUNC":
                raise ValueError("Service factory is not a local original function")
            entries.append({"function": function, "factory": target.name,
                            "factory_address": target["st_value"], "pointer_slot": address + 4})
            seen.add(function)
        else:
            raise ValueError("Service table has no sentinel")
        tables[name] = {"address": symbol["st_value"], "entries": entries}
    return tables


def bind_table(window: ThumbWindow, tables: dict, level: str | None) -> None:
    pointer = window.globals("pFunc")["pFunc"]
    window.write32(pointer, 0 if level is None else IMAGE + tables[level]["address"])
    for table in tables.values():
        for row in table["entries"]:
            window.write32(IMAGE + row["pointer_slot"], IMAGE + row["factory_address"])


def dispatch_control(core: Library, tables: dict, level: str | None,
                     function: int, version: int, terminal_disabled: int) -> dict:
    window = ThumbWindow(core, CHECK)
    bind_table(window, tables, level)
    flag = window.globals("terminal_change_function_is_disabled")["terminal_change_function_is_disabled"]
    window.cpu.mem_write(flag, bytes([terminal_disabled]))
    window.cpu.mem_write(DATA, PACKET.pack(20, function, 1, version, 0, 0, 0))
    result = window.run({UC_ARM_REG_R0: DATA})
    result.update(table_fixture=level, function=function, request_version_fixture=version,
                  terminal_disabled_fixture=terminal_disabled, parse_check=window.arguments()[0])
    return result


def level_control(core: Library, level: int) -> dict:
    window = ThumbWindow(core, COMPLETE)
    bindings = window.globals("pFunc", "adjCodeLevel", "terminal_change_function_is_disabled", *LEVELS)
    callback = RETURN + 0x100
    window.cpu.mem_write(callback, b"\x70\x47")
    window.allowed.append((callback, callback + 2))
    window.write32(DATA + 12, callback | 1)
    result = window.run({UC_ARM_REG_R0: DATA, UC_ARM_REG_R1: level})
    pointer = window.read32(bindings["pFunc"])
    selected = [name for name in LEVELS if bindings[name] == pointer]
    if len(selected) != 1:
        raise RuntimeError("Authentication completion selected an unknown table")
    result.update(level_fixture=level, selected_table=selected[0],
                  adj_code_level=window.cpu.mem_read(bindings["adjCodeLevel"], 1)[0],
                  terminal_disabled=window.cpu.mem_read(
                      bindings["terminal_change_function_is_disabled"], 1)[0])
    return result


def parse_refusal(core: Library, tables: dict, level: str, function: int) -> dict:
    window = ThumbWindow(core, PARSE)
    bind_table(window, tables, level)
    heap = iter((DATA + 0x2000, DATA + 0x2100))

    def allocate() -> None:
        expected = 4 if not window.calls[:-1] else 12
        if window.arguments()[0] != expected:
            raise ValueError("Unexpected refusal-object allocation")
        window.result(next(heap))

    window.fixture("_Znwj", allocate)
    # The discarded-input object is an explicit ABI fixture, not a received payload.
    def discard_constructor() -> None:
        if window.arguments()[0] != DATA + 0x2000:
            raise ValueError("Unexpected discarded-input object")
        window.result(DATA + 0x2000)

    window.fixture(DISCARD_CTOR, discard_constructor)
    window.cpu.mem_write(DATA, PACKET.pack(20, function, 1, 0, 0, 0, 0))
    result = window.run({UC_ARM_REG_R0: DATA}, stop_before=window.plt_address(NO_ACTION_CTOR))
    if window.arguments()[:3] != [DATA + 0x2100, 0x80, DATA + 0x2000]:
        raise RuntimeError("Original missing-handler path did not prepare NoAction(0x80)")
    result.update(table_fixture=level, function=function,
                  observation="before original NoAction constructor; no handler or I/O executed")
    return result


def refusal_header(core: Library, function: int, sequence: int) -> dict:
    window = ThumbWindow(core, NO_ACTION, REPLY)
    window.cpu.mem_write(DATA, bytes(8) + b"\x80")
    packet = DATA + 0x1000
    window.cpu.mem_write(packet, PACKET.pack(20, function, sequence, 0, 0, 0, 0))
    execute = window.run({UC_ARM_REG_R0: DATA, UC_ARM_REG_R1: packet})
    response = window.run(
        {UC_ARM_REG_R0: packet, UC_ARM_REG_R1: 0, UC_ARM_REG_R2: 0},
        start=IMAGE + core.function(REPLY)[0], stop_before=IMAGE + 0xEFEA,
    )
    raw = bytes(window.cpu.mem_read(packet, PACKET.size))
    expected = PACKET.pack(0, function, sequence, 3, 0, 0, 0x80)
    if raw != expected:
        raise RuntimeError("Original refusal/header prefix differs from the observed wire shape")
    return {"function": function, "sequence": sequence, "header_hex": raw.hex(),
            "no_action_execution": execute, "reply_prefix_execution": response,
            "scope": "Two original windows; declared NoAction object and null response data; no USB send"}


def factory_control(core: Library, function: str, command: int, disabled: int) -> dict:
    window = ThumbWindow(core, function)
    bindings = window.globals("_Z15SenserIfReceivePhj", "terminal_change_function_is_disabled")
    window.cpu.mem_write(bindings["terminal_change_function_is_disabled"], bytes([disabled]))

    def header_fixture() -> None:
        receiver, destination, _, _ = window.arguments()
        if receiver != bindings["_Z15SenserIfReceivePhj"] or not STACK <= destination < STACK + 0x10000 - 4:
            raise ValueError("Unexpected original command-header ABI")
        window.cpu.mem_write(destination, struct.pack("<HH", 0, command))
        window.result(receiver)

    window.fixture(SERIALIZE_HEADER, header_fixture)
    window.fixture("_Z14cppdump_logrechPKcPhj", lambda: window.result(0))
    if function == PRODUCT and command == 0xF1 and disabled:
        heap = iter((DATA + 0x2000, DATA + 0x2100))
        window.fixture("_Znwj", lambda: window.result(next(heap)))
        window.fixture(DISCARD_CTOR, lambda: window.result(window.arguments()[0]))
        stop = window.plt_address(NO_ACTION_CTOR)
    elif function == PRODUCT:
        stop = window.plt_address("_Z15GetPluginModulePKcth")
    else:
        window.fixture("_Znwj", lambda: window.result(DATA + 0x2000))
        stop = window.plt_address("_ZN24HostCommunicationCommandC1EPK12SenserPacketRK15CommandCodeType")
    result = window.run({UC_ARM_REG_R0: DATA}, stop_before=stop)
    result.update(factory=function, command_fixture=command, terminal_disabled_fixture=disabled)
    return result


def adjustment_catalog(core: Library) -> list[dict]:
    raw = read_va(core.elf, core.raw, 0x19214, 0x60B * 0x48)
    if raw is None:
        raise ValueError("Original adjustment table is not file-backed")
    rows = []
    for index in range(0x60B):
        record = raw[index * 0x48:(index + 1) * 0x48]
        block, code = struct.unpack_from("<HH", record)
        name = record[4:68].split(b"\0", 1)[0].decode("ascii")
        rows.append({"index": index, "block": block, "stored_code": code,
                     "name": name, "route": struct.unpack_from("<I", record, 68)[0]})
    return rows


def adjustment_window(core: Library, block: int, code: int, level: int,
                      payload: bytes) -> tuple[ThumbWindow, list[int]]:
    if level not in (1, 2, 3) or len(payload) > 0x124:
        raise ValueError("Adjustment fixture is outside the declared level/payload profile")
    window = ThumbWindow(core, TRANSACTION)
    window.limit = 60000
    window.original(SERIALIZE)
    bindings = window.globals("_Z15SenserIfReceivePhj", "adjCodeLevel")
    window.cpu.mem_write(bindings["adjCodeLevel"], bytes([level]))
    reads = []

    def receive() -> None:
        consumed = sum(reads)
        destination, count, _, _ = window.arguments()
        if (not 0 < count <= len(payload) - consumed
                or not DATA + 0x28 <= destination
                or destination + count > DATA + 0x14C):
            raise ValueError("Original adjustment receive exceeds the declared payload")
        window.cpu.mem_write(destination, payload[consumed:consumed + count])
        reads.append(count)
        window.result(count)

    receiver = bindings["_Z15SenserIfReceivePhj"] & ~1
    window.fixtures[receiver] = ("constructed received adjustment bytes; no USB", receive)

    def memset() -> None:
        destination, value, count, _ = window.arguments()
        if not DATA <= destination <= destination + count <= DATA + 0x10000:
            raise ValueError("Adjustment memset exceeds its declared object")
        window.cpu.mem_write(destination, bytes([value & 255]) * count)
        window.result(destination)

    window.fixture("memset", memset)
    window.fixture("_Z10cpp_logrechPKcjj", lambda: window.result(0))
    packet, command = DATA + 0x4000, DATA + 0x5000
    window.cpu.mem_write(packet, PACKET.pack(4 + len(payload), 0x40, 1, 0, 0, 0, 0))
    window.cpu.mem_write(command, struct.pack("<HH", block, code))
    return window, reads


def adjustment_gate(core: Library, block: int, code: int, level: int, payload: bytes) -> dict:
    window, reads = adjustment_window(core, block, code, level, payload)
    packet, command = DATA + 0x4000, DATA + 0x5000
    file_precondition = block == 0x603
    boundary = (window.plt_address("_Z15tlog_file_existv") if file_precondition
                else IMAGE + 0x135B6)
    result = window.run(
        {UC_ARM_REG_R0: DATA, UC_ARM_REG_R1: packet, UC_ARM_REG_R2: 0, UC_ARM_REG_R3: command},
        stop_before=boundary,
    )
    if sum(reads) != len(payload):
        raise RuntimeError("Original adjustment gate did not consume the exact payload")
    result.update(block=block, command=code, level_fixture=level, payload_bytes=len(payload),
                  selected_table_index=None if file_precondition else window.cpu.reg_read(UC_ARM_REG_R5),
                  transaction_type=None if file_precondition else window.cpu.reg_read(UC_ARM_REG_R9),
                  gate_reached=not file_precondition,
                  scope=("Before original tlog_file_exist; filesystem/trace state is unsupplied"
                         if file_precondition else
                         "Original TransactionStart and Serialize, before transaction routing or OSAL/device I/O"))
    return result


NATIVE_CLASSES = {
    "libInfraDevInfoSender.so": ("13DevInfoSender", 1),
    "libInfraKikiLogSender.so": ("13KikiLogSender", 2),
    "libInfraExtBackupCommunicator.so": ("21ExtBackupCommunicator", 4),
    "libInfraLensCommunicator.so": ("16LensCommunicator", 6),
    "libInfraNetworkServiceInfo.so": ("18NetworkServiceInfo", 7),
}


def native_registration(library: Library, class_name: str, expected_feature: int) -> dict:
    function = "_ZN" + class_name + "7InitCmdEP16__extcmd_svc_msg"
    window = ThumbWindow(library, function)
    message = DATA + 0x2000

    def allocate() -> None:
        _, destination, size, count = window.arguments()
        if (size, count) != (0x54, 1) or not STACK <= destination <= STACK + 0x10000 - 4:
            raise ValueError("Native registration allocation exceeds its explicit ABI")
        window.write32(destination, message)
        window.result(0)

    window.fixture("osal_valloc_msg_wait", allocate)
    send_name = ("_ZN" + class_name + ("17SndMsgToLowerSyncEPvj"
                 if class_name in ("13DevInfoSender", "13KikiLogSender") else "10SndSyncMsgEPvj"))
    result = window.run({UC_ARM_REG_R0: DATA, UC_ARM_REG_R1: DATA + 0x1000},
                        stop_before=window.plt_address(send_name))
    if window.arguments()[1] != message:
        raise RuntimeError("Original registration does not send its declared message")
    raw = bytes(window.cpu.mem_read(message, 0x54))
    feature = struct.unpack_from("<H", raw, 0x10)[0]
    receive, send = struct.unpack_from("<II", raw, 0x14)
    message_id = struct.unpack_from("<I", raw)[0]
    if (feature, receive, send, message_id) != (expected_feature, 8192, 8192, 0x20000001):
        raise RuntimeError("Original native registration differs from its measured message profile")
    result.update(feature=feature, receive_bytes=receive, send_bytes=send,
                  message_id=message_id,
                  scope="Original message preparation, before registration IPC; not live registration")
    return result


def instruction_record(library: Library, name: str) -> dict:
    address, code = library.function(name)
    return {"name": name, "entry": address | 1, "bytes": len(code),
            "instructions": decode_block(library.elf, library.raw, address | 1,
                                         len(code), library.plt)}


def qualify(libraries: dict[str, Library]) -> dict:
    core = libraries["libsencore.so"]
    tables = service_tables(core)
    checks = [
        dispatch_control(core, tables, table, function, version, disabled)
        for table in (None, *LEVELS)
        for function in (0x10, 0x20, 0x30, 0x40, 0xFF00, 0xFF01, 0xFF02, 0xFF03)
        for version in (0, 3) for disabled in (0, 1)
    ]
    for row in checks:
        table = row["table_fixture"]
        present = table is not None and any(entry["function"] == row["function"]
                                           for entry in tables[table]["entries"])
        if row["parse_check"] != (0 if present else 1):
            raise RuntimeError("Original dispatch differs from table membership")
    product = libraries["libpro00.so"]
    noop = ThumbWindow(product, "command00F1")
    payload = b"\x02" + b"\xA5" * 31
    noop.cpu.mem_write(DATA, payload)
    terminal = noop.run({UC_ARM_REG_R0: DATA})
    if terminal["arguments"][0] != 1 or terminal["non_stack_writes"] or bytes(noop.cpu.mem_read(DATA, 32)) != payload:
        raise RuntimeError("Original terminal export is not the measured no-op")
    adjustments = adjustment_catalog(core)
    gates = [
        adjustment_gate(core, block, command, level, data)
        for block, command, data in
        ((0x601, 0x1F, b"\x02"), (0x601, 0xFE, struct.pack("<II", 0, 4)),
         (0x304, 0xFE, struct.pack("<II", 0, 4)), (0x603, 1, bytes(8)))
        for level in (1, 2, 3)
    ]
    for row in gates:
        if row["block"] == 0x601 and row["command"] == 0x1F and row["transaction_type"] != 0xFF:
            raise RuntimeError("Original TERM_MODE exclusion unexpectedly changed")
    return {
        "status": "offline_controls_passed_no_live_access",
        "tables": tables,
        "authentication_completion": [level_control(core, level) for level in (1, 2, 3)],
        "dispatch_controls": checks,
        "parse_refusal_boundaries": [
            parse_refusal(core, tables, table, function)
            for table in LEVELS for function in (0xFF01, 0xFF03)
        ],
        "refusal_wire_prefixes": [refusal_header(core, function, sequence)
                                  for function in (0xFF01, 0xFF03) for sequence in (1, 0x1234)],
        "product_info_controls": [factory_control(core, PRODUCT, command, disabled)
                                  for command in (0x1F, 0xF1) for disabled in (0, 1)],
        "test_mode_factory": factory_control(core, TEST_MODE, 0x1F, 0),
        "terminal_export_noop": terminal,
        "adjustment_catalog": adjustments,
        "adjustment_gate_controls": gates,
        "native_registrations": {
            name: native_registration(libraries[name], class_name, feature)
            for name, (class_name, feature) in NATIVE_CLASSES.items()
        },
        "instructions": [
            instruction_record(core, name) for name in
            (CHECK, PARSE, NO_ACTION, REPLY, COMPLETE, PRODUCT, TEST_MODE,
             "_ZN24HostCommunicationCommand9DoExecuteEP12SenserPacket",
             TRANSACTION)
        ] + [
            dict(instruction_record(libraries[name], "_ZN" + class_name +
                                    ("14ReqTransferCmpEP16__extcmd_svc_msg"
                                     if class_name == "18NetworkServiceInfo"
                                     else "13ReqTranserCmpEP16__extcmd_svc_msg")),
                 module=name)
            for name, (class_name, _) in NATIVE_CLASSES.items()
        ] + [
            dict(instruction_record(libraries["libInfraKikiLogSender.so"],
                                    "_ZN13KikiLogSender16set_cmd_responseEPhS0_"),
                 module="libInfraKikiLogSender.so"),
            dict(instruction_record(libraries["libusbcmd.so"],
                                    "_ZN20UsbCmd_ExtCmdSvcWrap18RegistTransportCmdEv"),
                 module="libusbcmd.so"),
        ],
        "limitations": [
            "All original inputs are update-package bytes, not live installed-byte attestations.",
            "No Ghidra result is substituted for the unavailable WSL/provider; instruction records use Capstone.",
            "Selected GOT/table pointers, stack/data/BSS and callback results are explicit fixtures.",
            "No discarded-input vector construction, USB worker, OSAL IPC, device open or packet send is executed.",
            "No input code/table bit or authentication guard is patched to enable a handler.",
            "Absent factories under stock tables do not prove every possible future runtime modification impossible.",
            "Native preparation proves protocol declarations, not live registration, read-only effects or a partition reader.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, default=PACKAGE)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    package = args.package_root.resolve()
    output = args.output_dir.resolve()
    if not package.is_relative_to(PROJECT_ROOT / "firmware"):
        parser.error("Original inputs must remain in the declared firmware namespace")
    if not output.is_relative_to(PROJECT_ROOT / "artifacts"):
        parser.error("Receipts must remain under artifacts")
    output.mkdir(parents=True, exist_ok=False)
    report = {"started_utc": datetime.now(timezone.utc).isoformat(),
              "status": "in_progress", "camera_operations": 0, "inputs": {},
              "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        libraries = {}
        for name, expected in HASHES.items():
            path = (package / SYSTEM / name).resolve()
            if not path.is_relative_to(package):
                raise ValueError("Original input escapes the selected package")
            library = Library(path.read_bytes(), expected)
            libraries[name] = library
            report["inputs"][name] = {"path": str(path.relative_to(PROJECT_ROOT)),
                                     "sha256": expected, "bytes": len(library.raw)}
        report.update(qualify(libraries))
    except (OSError, ValueError, RuntimeError, UcError) as error:
        report.update(status="blocked", error_type=type(error).__name__, error=str(error))
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "camera_operations", "error")
                      if key in report}, indent=2))
    return 0 if report["status"] == "offline_controls_passed_no_live_access" else 2


if __name__ == "__main__":
    raise SystemExit(main())
