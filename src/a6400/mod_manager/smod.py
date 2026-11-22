"""Strict SMOD v1 / v2 binary parser and integrity validator."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import struct

SMOD_MAGIC = 0x444F4D53  # "SMOD"
SMOD_HEADER_SIZE = 68
SMOD_HOOK_SIZE = 40
SMOD_MAX_HOOKS = 8
SMOD_MAX_PATCH = 16
SMOD_V2_NO_FIXUP = 0xFF
SMOD_MAX_TOTAL_BYTES = 1024 * 1024
MAX_MOD_FILENAME_LEN = 47

SMOD_V1_FLAG_STATE_INIT = 1
SMOD_V1_FLAG_LIFECYCLE = 2
SMOD_V2_FLAG_RELOC = 4
SMOD_V2_FLAG_AUTO_ON = 8

SMOD_RELOC_MAGIC = 0x31434C52  # "RLC1"
SMOD_RELOC_MAX_LIBS = 8
SMOD_RELOC_MAX_RELOCS = 1024

_V1_HDR = struct.Struct("<III32s6I")
_V1_HOOK = struct.Struct("<II16s16s")
_V2_HDR = struct.Struct("<III32s6I")
_V2_HOOK = struct.Struct("<IBB2x16s16s")
_RELOC_HDR = struct.Struct("<IHH")
_RELOC_LIB = struct.Struct("<II24s")

_SAFE_MOD_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.mod$")


class SmodError(ValueError):
    """Raised when an SMOD binary or filename violates loader invariants."""


def fnv1a_32(data: bytes) -> int:
    """Compute the 32-bit FNV-1a checksum used by Mod Loader v1/v2."""
    h = 0x811C9DC5
    for byte in data:
        h = ((h ^ byte) * 0x01000193) & 0xFFFFFFFF
    return h


def validate_mod_filename(filename: str) -> str:
    """Validate a `.mod` filename against Mod Loader v2 naming and path bounds."""
    if not isinstance(filename, str) or not filename:
        raise SmodError("Mod filename must be a non-empty string")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise SmodError(f"Mod filename must not contain path separators: {filename!r}")
    if len(filename) > MAX_MOD_FILENAME_LEN:
        raise SmodError(
            f"Mod filename {filename!r} exceeds {MAX_MOD_FILENAME_LEN} characters"
        )
    if not _SAFE_MOD_FILENAME_RE.match(filename):
        raise SmodError(
            f"Mod filename {filename!r} must be ASCII alphanumeric/._- and end with .mod"
        )
    return filename


@dataclass(frozen=True)
class SmodInfo:
    """Validated metadata extracted from an SMOD v1 or v2 binary."""

    version: int
    name: str
    flags: int
    code_size: int
    bss_size: int
    hook_count: int
    fnv1a_32: int
    sha256: str
    file_size: int
    has_reloc: bool
    auto_on: bool
    lifecycle: bool
    state_init: bool
    code_addr: int
    hooks: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "name": self.name,
            "flags": self.flags,
            "code_size": self.code_size,
            "bss_size": self.bss_size,
            "hook_count": self.hook_count,
            "fnv1a_32": f"{self.fnv1a_32:#010x}",
            "sha256": self.sha256,
            "file_size": self.file_size,
            "has_reloc": self.has_reloc,
            "auto_on": self.auto_on,
            "lifecycle": self.lifecycle,
            "state_init": self.state_init,
            "code_addr": f"{self.code_addr:#010x}" if self.code_addr else None,
            "hooks": [dict(h) for h in self.hooks],
        }


def _decode_cstr(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def _parse_v1(data: bytes) -> SmodInfo:
    magic, version, flags, raw_name, code_addr, code_size, state_addr, state_size, hook_count, expected_fnv = (
        _V1_HDR.unpack_from(data, 0)
    )
    del magic, version, state_addr
    if not code_size or (code_size & 3) or hook_count > SMOD_MAX_HOOKS:
        raise SmodError("SMOD v1 header outside loader limits")
    if code_size + state_size > SMOD_MAX_TOTAL_BYTES:
        raise SmodError("SMOD v1 payload exceeds 1 MiB loader bound")
    hooks_end = SMOD_HEADER_SIZE + hook_count * SMOD_HOOK_SIZE
    code_end = hooks_end + code_size
    if len(data) < code_end:
        raise SmodError("SMOD v1 file truncated before end of code")

    hooks: list[dict[str, object]] = []
    for index in range(hook_count):
        off = SMOD_HEADER_SIZE + index * SMOD_HOOK_SIZE
        target, patch_size, orig_bytes, hook_bytes = _V1_HOOK.unpack_from(data, off)
        if not 1 <= patch_size <= SMOD_MAX_PATCH:
            raise SmodError(f"SMOD v1 hook {index} patch size {patch_size} out of bounds")
        hooks.append(
            {
                "target": f"{target:#010x}",
                "size": patch_size,
                "orig_hex": orig_bytes[:patch_size].hex(),
                "patch_hex": hook_bytes[:patch_size].hex(),
            }
        )

    code = data[hooks_end:code_end]
    if fnv1a_32(code) != expected_fnv:
        raise SmodError("SMOD v1 code bytes fail FNV-1a checksum")
    if (flags & SMOD_V1_FLAG_STATE_INIT) and len(data) < code_end + state_size:
        raise SmodError("SMOD v1 file truncated before end of initial state")

    return SmodInfo(
        version=1,
        name=_decode_cstr(raw_name) or "Unnamed Mod",
        flags=flags,
        code_size=code_size,
        bss_size=state_size,
        hook_count=hook_count,
        fnv1a_32=expected_fnv,
        sha256=hashlib.sha256(data).hexdigest(),
        file_size=len(data),
        has_reloc=False,
        auto_on=False,
        lifecycle=bool(flags & SMOD_V1_FLAG_LIFECYCLE),
        state_init=bool(flags & SMOD_V1_FLAG_STATE_INIT),
        code_addr=code_addr,
        hooks=tuple(hooks),
    )


def _validate_reloc_table(data: bytes, offset: int) -> None:
    if len(data) < offset + _RELOC_HDR.size:
        raise SmodError("SMOD v2 relocation table header truncated")
    magic, nlib, nrel = _RELOC_HDR.unpack_from(data, offset)
    if magic != SMOD_RELOC_MAGIC:
        raise SmodError("Invalid SMOD v2 relocation table magic")
    if nlib > SMOD_RELOC_MAX_LIBS or nrel > SMOD_RELOC_MAX_RELOCS:
        raise SmodError("SMOD v2 relocation table exceeds loader limits")
    table_len = _RELOC_HDR.size + nlib * _RELOC_LIB.size + nrel * 4
    if len(data) < offset + table_len + 4:
        raise SmodError("SMOD v2 relocation table truncated")
    table_bytes = data[offset : offset + table_len]
    (expected_fnv,) = struct.unpack_from("<I", data, offset + table_len)
    if fnv1a_32(table_bytes) != expected_fnv:
        raise SmodError("SMOD v2 relocation table fails FNV-1a checksum")


def _parse_v2(data: bytes) -> SmodInfo:
    magic, version, flags, raw_name, code_size, expected_fnv, bss_size, on_off, off_off, hook_count = (
        _V2_HDR.unpack_from(data, 0)
    )
    del magic, version
    if not code_size or (code_size & 3) or hook_count > SMOD_MAX_HOOKS:
        raise SmodError("SMOD v2 header outside loader limits")
    if code_size + bss_size > SMOD_MAX_TOTAL_BYTES:
        raise SmodError("SMOD v2 payload exceeds 1 MiB loader bound")
    for entry_off in (on_off, off_off):
        if entry_off and ((entry_off & 1) == 0 or entry_off >= code_size):
            raise SmodError("SMOD v2 lifecycle entry offset outside valid Thumb code range")

    hooks_end = SMOD_HEADER_SIZE + hook_count * SMOD_HOOK_SIZE
    code_end = hooks_end + code_size
    if len(data) < code_end:
        raise SmodError("SMOD v2 file truncated before end of code")

    hooks: list[dict[str, object]] = []
    for index in range(hook_count):
        off = SMOD_HEADER_SIZE + index * SMOD_HOOK_SIZE
        target, patch_size, fixup, orig_bytes, tmpl_bytes = _V2_HOOK.unpack_from(data, off)
        if not 1 <= patch_size <= SMOD_MAX_PATCH:
            raise SmodError(f"SMOD v2 hook {index} patch size {patch_size} out of bounds")
        if fixup != SMOD_V2_NO_FIXUP and fixup + 4 > patch_size:
            raise SmodError(f"SMOD v2 hook {index} fixup offset out of bounds")
        hooks.append(
            {
                "target": f"{target:#010x}",
                "size": patch_size,
                "fixup": fixup,
                "orig_hex": orig_bytes[:patch_size].hex(),
                "tmpl_hex": tmpl_bytes[:patch_size].hex(),
            }
        )

    code = data[hooks_end:code_end]
    if fnv1a_32(code) != expected_fnv:
        raise SmodError("SMOD v2 code bytes fail FNV-1a checksum")

    has_reloc = bool(flags & SMOD_V2_FLAG_RELOC)
    if has_reloc:
        _validate_reloc_table(data, code_end)

    return SmodInfo(
        version=2,
        name=_decode_cstr(raw_name) or "Unnamed Mod",
        flags=flags,
        code_size=code_size,
        bss_size=bss_size,
        hook_count=hook_count,
        fnv1a_32=expected_fnv,
        sha256=hashlib.sha256(data).hexdigest(),
        file_size=len(data),
        has_reloc=has_reloc,
        auto_on=bool(flags & SMOD_V2_FLAG_AUTO_ON),
        lifecycle=bool(on_off or off_off),
        state_init=False,
        code_addr=0,
        hooks=tuple(hooks),
    )


def parse_smod(data: bytes, filename: str | None = None) -> SmodInfo:
    """Validate and parse an SMOD v1 or v2 binary."""
    if filename is not None:
        validate_mod_filename(filename)
    if len(data) < SMOD_HEADER_SIZE:
        raise SmodError("File shorter than 68-byte SMOD header")
    if len(data) > SMOD_MAX_TOTAL_BYTES:
        raise SmodError("SMOD file exceeds 1 MiB loader bound")
    magic, version = struct.unpack_from("<II", data, 0)
    if magic != SMOD_MAGIC:
        raise SmodError(f"Invalid SMOD magic {magic:#010x}")
    if version == 1:
        return _parse_v1(data)
    if version == 2:
        return _parse_v2(data)
    raise SmodError(f"Unsupported SMOD version {version}")
