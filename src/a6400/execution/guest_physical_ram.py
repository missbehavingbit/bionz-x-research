"""Coherent physical RAM backing shared by /dev/stream, /dev/mem, and Sugilite DMA.

On CXD90045, user space maps GRM/DMM pools (e.g. ``gl_common`` at PA
``0x202E0000`` and OSD A/B at PA ``0x23A80000`` / ``0x23B16000``) simultaneously
through ``/dev/stream`` and multiple ``/dev/mem`` file descriptors, while the
Sugilite (SMAPH-S30) GPU reads command lists, shaders, vertex/index buffers,
and textures by physical address and writes rendered tiles and PPF-detiled OSD
surfaces back to physical RAM.

Backing physical DRAM ranges with shared ``ctypes`` buffers mapped via
``mu.mem_map_ptr`` keeps all virtual aliases and GPU DMA coherent without
per-instruction memory-copy hooks.
"""

from __future__ import annotations

import ctypes
from typing import Dict, List, Optional, Tuple
import zlib

import unicorn

PAGE_SIZE = 4096
BANK_SHIFT = 26  # 64 MiB per bank; [0x20000000, 0x24000000) is bank 8.
BANK_SIZE = 1 << BANK_SHIFT
BANK_MASK = BANK_SIZE - 1
DRAM_LIMIT = 0x40000000


class GuestPhysicalRam:
    """Sparse 64 MiB physical DRAM banks shared across VA aliases and DMA."""

    def __init__(self) -> None:
        self._banks: Dict[int, ctypes.Array] = {}
        self.mappings: List[Dict[str, int | str]] = []

    def supports_range(self, phys_addr: int, length: int) -> bool:
        if length <= 0 or phys_addr < 0 or phys_addr + length > DRAM_LIMIT:
            return False
        return (phys_addr >> BANK_SHIFT) == ((phys_addr + length - 1) >> BANK_SHIFT)

    def _bank(self, bank_index: int) -> ctypes.Array:
        buf = self._banks.get(bank_index)
        if buf is None:
            buf = ctypes.create_string_buffer(BANK_SIZE)
            self._banks[bank_index] = buf
        return buf

    def read_physical(self, phys_addr: int, size: int) -> Optional[bytes]:
        if size < 0 or phys_addr < 0 or phys_addr + size > DRAM_LIMIT:
            return None
        if size == 0:
            return b''
        out = bytearray()
        cur = phys_addr
        rem = size
        while rem > 0:
            bank_idx = cur >> BANK_SHIFT
            off = cur & BANK_MASK
            chunk = min(rem, BANK_SIZE - off)
            buf = self._banks.get(bank_idx)
            if buf is None:
                out.extend(b'\x00' * chunk)
            else:
                out.extend(buf.raw[off : off + chunk])
            cur += chunk
            rem -= chunk
        return bytes(out)

    def write_physical(self, phys_addr: int, data: bytes) -> bool:
        size = len(data)
        if size == 0:
            return True
        if phys_addr < 0 or phys_addr + size > DRAM_LIMIT:
            return False
        cur = phys_addr
        pos = 0
        while pos < size:
            bank_idx = cur >> BANK_SHIFT
            off = cur & BANK_MASK
            chunk = min(size - pos, BANK_SIZE - off)
            buf = self._bank(bank_idx)
            ctypes.memmove(ctypes.addressof(buf) + off, data[pos : pos + chunk], chunk)
            cur += chunk
            pos += chunk
        return True

    def map_alias(
        self,
        mu: unicorn.Uc,
        virtual_addr: int,
        phys_addr: int,
        length: int,
        prot: int,
        device: str,
        seed_from_existing: bool = False,
    ) -> bool:
        """Bind ``[virtual_addr, virtual_addr + length)`` onto shared physical RAM."""
        rounded = (length + PAGE_SIZE - 1) & -PAGE_SIZE
        if not self.supports_range(phys_addr, rounded):
            return False
        bank_idx = phys_addr >> BANK_SHIFT
        off = phys_addr & BANK_MASK
        already_allocated = bank_idx in self._banks
        buf = self._bank(bank_idx)
        if seed_from_existing and not already_allocated:
            try:
                existing = bytes(mu.mem_read(virtual_addr, rounded))
                if any(existing):
                    ctypes.memmove(ctypes.addressof(buf) + off, existing, rounded)
            except unicorn.UcError:
                pass
        try:
            mu.mem_unmap(virtual_addr, rounded)
        except unicorn.UcError:
            pass
        host_ptr = ctypes.addressof(buf) + off
        mu.mem_map_ptr(virtual_addr, rounded, unicorn.UC_PROT_ALL, host_ptr)
        if prot != unicorn.UC_PROT_ALL:
            mu.mem_protect(virtual_addr, rounded, prot)
        record = {
            'virtual_start': int(virtual_addr),
            'physical_start': int(phys_addr),
            'length': int(rounded),
            'prot': int(prot),
            'device': str(device),
        }
        if not any(
            m['virtual_start'] == record['virtual_start']
            and m['length'] == record['length']
            for m in self.mappings
        ):
            self.mappings.append(record)
        return True

    def to_state(self) -> Dict[str, object]:
        banks_packed: Dict[int, List[Tuple[int, bytes]]] = {}
        for bank_idx, buf in self._banks.items():
            raw = buf.raw
            pages: List[Tuple[int, bytes]] = []
            for offset in range(0, BANK_SIZE, PAGE_SIZE):
                page = raw[offset : offset + PAGE_SIZE]
                if any(page):
                    pages.append((offset, zlib.compress(page, 6)))
            if pages:
                banks_packed[int(bank_idx)] = pages
        return {
            'banks': banks_packed,
            'mappings': [dict(item) for item in self.mappings],
        }

    def from_state(self, mu: unicorn.Uc, state: Dict[str, object]) -> None:
        self._banks.clear()
        self.mappings = [dict(item) for item in (state.get('mappings') or [])]
        for bank_idx_raw, pages in (state.get('banks') or {}).items():
            buf = self._bank(int(bank_idx_raw))
            base_ptr = ctypes.addressof(buf)
            for offset, blob in pages:
                page = zlib.decompress(blob)
                ctypes.memmove(base_ptr + int(offset), page, len(page))
        for m in list(self.mappings):
            va = int(m['virtual_start'])
            pa = int(m['physical_start'])
            length = int(m['length'])
            prot = int(m.get('prot', unicorn.UC_PROT_READ | unicorn.UC_PROT_WRITE))
            if not self.supports_range(pa, length):
                continue
            buf = self._bank(pa >> BANK_SHIFT)
            off = pa & BANK_MASK
            try:
                mu.mem_unmap(va, length)
            except unicorn.UcError:
                pass
            mu.mem_map_ptr(va, length, unicorn.UC_PROT_ALL, ctypes.addressof(buf) + off)
            if prot != unicorn.UC_PROT_ALL:
                mu.mem_protect(va, length, prot)
