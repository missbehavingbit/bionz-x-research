"""Parse Sony `uxd\\x07` image resource containers (share/app/image_*.uxc) — container level only.

Proven layout (E0522, offline):
* 16-byte header: magic b"uxd\\x07", u16 fields at +4..+14.
* A table of records `{u32 res_id (0x1aXXXXXX), u32 format, u16 width, u16 height, u32 size, u32 offset}` (20 B).
  Format bit 0x08000000 marks a 40-byte record: 20 extension bytes (likely stretch metadata) sit between
  height and size/offset.
  Image resource IDs used by the UI as 0x02XXXXXX map to record IDs 0x1aXXXXXX (same low 24 bits).
* Data offsets are relative to **each record's address + 4** (libObj `0x5c21a4`: view base = record + 4;
  `0x7a5424`: stream = base + offset, length = size). Blocks are then contiguous (0-3 bytes of 4-byte
  alignment padding), sizes are exact, and the first block starts at the end of the record table.
* Formats seen: 0x80000000 = raw 16 bpp (size == w*h*2), 0xe0000000 = main compressed format,
  0x60000000 = second compressed format. The compressed pixel codecs are NOT decoded here.
"""

from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"uxd\x07"
RECORD = struct.Struct("<IIHHII")
KNOWN_FORMATS = (0x80000000, 0xE0000000, 0x60000000)


EXT_FLAG = 0x08000000


@dataclass(frozen=True)
class ImageRecord:
    res_id: int
    fmt: int
    width: int
    height: int
    size: int
    offset: int
    record_pos: int
    ext: bytes = b""

    @property
    def ui_res_id(self) -> int:
        return 0x02000000 | (self.res_id & 0x00FFFFFF)


def _record_len(blob: bytes, pos: int) -> int:
    """Length of the record at pos (20 or 40), or 0 if pos does not hold a plausible record."""
    if pos + RECORD.size > len(blob):
        return 0
    rid, fmt, w, h, _sz, _off = RECORD.unpack_from(blob, pos)
    if (rid >> 24) != 0x1A or fmt == 0 or (fmt & 0x07FFFFFF) or not (0 < w < 4096 and 0 < h < 4096):
        return 0
    n = 40 if fmt & EXT_FLAG else 20
    return n if pos + n <= len(blob) else 0


def _is_record(blob: bytes, pos: int) -> bool:
    return _record_len(blob, pos) != 0


def find_record_table(blob: bytes, scan_limit: int = 4_000_000) -> tuple[int, int]:
    """Return (start, end) of the contiguous 20-byte record table."""
    if blob[:4] != MAGIC:
        raise ValueError("not a uxd container")
    limit = min(len(blob) - RECORD.size, scan_limit)
    for pos in range(16, limit, 4):
        n = _record_len(blob, pos)
        if n and _record_len(blob, pos + n):
            end = pos
            while (n := _record_len(blob, end)):
                end += n
            return pos, end
    raise ValueError("record table not found")


def parse(blob: bytes) -> tuple[int, list[ImageRecord]]:
    """Return (record_table_start, records)."""
    start, end = find_record_table(blob)
    records = []
    pos = start
    while pos < end:
        n = _record_len(blob, pos)
        rid, fmt, w, h = struct.unpack_from("<IIHH", blob, pos)
        size, off = struct.unpack_from("<II", blob, pos + n - 8)
        records.append(ImageRecord(rid, fmt, w, h, size, off, pos, blob[pos + 12:pos + n - 8]))
        pos += n
    return start, records


def block(blob: bytes, rec: ImageRecord) -> bytes:
    """Raw (possibly compressed) data block: `size` bytes at record address + 4 + offset."""
    begin = rec.record_pos + 4 + rec.offset
    if begin < 0 or begin + rec.size > len(blob):
        raise ValueError(f"block for {rec.res_id:#x} is outside the container")
    return blob[begin:begin + rec.size]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("container", type=Path)
    ap.add_argument("--res-id", type=lambda s: int(s, 0), action="append", default=[],
                    help="UI (0x02..) or record (0x1a..) resource ID to show")
    ap.add_argument("--size", help="filter WIDTHxHEIGHT")
    args = ap.parse_args()
    blob = args.container.read_bytes()
    table, recs = parse(blob)
    want = {0x1A000000 | (r & 0xFFFFFF) for r in args.res_id}
    out = []
    for r in recs:
        if want and r.res_id not in want:
            continue
        if args.size and f"{r.width}x{r.height}" != args.size:
            continue
        out.append({"res_id": hex(r.res_id), "ui_res_id": hex(r.ui_res_id), "format": hex(r.fmt),
                    "size": f"{r.width}x{r.height}", "bytes": r.size, "data": hex(r.record_pos + 4 + r.offset)})
    print(json.dumps({"record_table": hex(table), "records": len(recs), "selected": out}, indent=1))


if __name__ == "__main__":
    main()
