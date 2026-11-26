#!/usr/bin/env python3
"""Add one file to the root directory (or an existing subdirectory) of a FAT12/FAT16 partition image.

Used by run.sh to place inputs on the guest /setting partition (nflasha2)
inside a per-run copy, so the original eMMC image is never modified.

Refuses to overwrite: exits non-zero if the 8.3 name already exists, the
root directory is full, or there are not enough free clusters.

usage: fat_add_file.py [--if-absent] PARTITION_IMAGE SOURCE_FILE NAME.EXT
       fat_add_file.py [--if-absent] PARTITION_IMAGE SOURCE_FILE DIR/NAME   (DIR must already exist in
                       the root; the file goes into a free slot of DIR's existing clusters, e.g.
                       mode/dmode)

--if-absent accepts one existing entry only when its recorded size equals the
source (an input that is already staged, e.g. /setting/ulogio.bin in a prepared
capture); a different size is still refused, and nothing is overwritten.
"""

import struct
import sys


def fat_entry(fat: bytearray, fat12: bool, n: int) -> int:
    if fat12:
        off = n + n // 2
        v = fat[off] | (fat[off + 1] << 8)
        return (v >> 4) if n & 1 else (v & 0xFFF)
    return struct.unpack_from('<H', fat, 2 * n)[0]


def set_fat_entry(fat: bytearray, fat12: bool, n: int, value: int) -> None:
    if fat12:
        off = n + n // 2
        v = fat[off] | (fat[off + 1] << 8)
        if n & 1:
            v = (v & 0x000F) | (value << 4)
        else:
            v = (v & 0xF000) | value
        fat[off] = v & 0xFF
        fat[off + 1] = (v >> 8) & 0xFF
    else:
        struct.pack_into('<H', fat, 2 * n, value)


def short_name(name: str) -> tuple[bytes, int]:
    base, _, ext = name.partition('.')
    if not (1 <= len(base) <= 8 and len(ext) <= 3):
        raise SystemExit(f'not an 8.3 name: {name}')
    raw = (base.upper().ljust(8) + ext.upper().ljust(3)).encode('ascii')
    # NT case flags: 0x08 lowercase base, 0x10 lowercase extension
    flags = (0x08 if base.islower() else 0) | (0x10 if ext.islower() else 0)
    return raw, flags


def main() -> int:
    argv = sys.argv[1:]
    if_absent = bool(argv) and argv[0] == '--if-absent'
    if if_absent:
        argv = argv[1:]
    image_path, source_path, name = argv[0:3]
    img = bytearray(open(image_path, 'rb').read())
    data = open(source_path, 'rb').read()
    bps, spc, reserved, nfats, root_entries, total16, _media, fat_sectors = \
        struct.unpack_from('<HBHBHHBH', img, 11)
    total = total16 or struct.unpack_from('<I', img, 32)[0]
    root_sectors = (root_entries * 32 + bps - 1) // bps
    data_start = (reserved + nfats * fat_sectors + root_sectors) * bps
    clusters = (total * bps - data_start) // (spc * bps)
    fat12 = clusters < 4085
    cluster_bytes = spc * bps
    fat_off = reserved * bps
    fat = bytearray(img[fat_off:fat_off + fat_sectors * bps])

    dirname, _, leaf = name.rpartition('/')
    raw_name, case_flags = short_name(leaf)
    root_off = fat_off + nfats * fat_sectors * bps

    def find_slot(offsets):
        """First free 32-byte slot among the given directory entry offsets; refuse duplicates.

        Returns 'present' when --if-absent found an entry whose size matches the source.
        """
        slot = None
        for off in offsets:
            entry = img[off:off + 32]
            if entry[0] == 0 or entry[0] == 0xE5:
                if slot is None:
                    slot = off
                if entry[0] == 0:
                    break
                continue
            if entry[11] != 0x0F and entry[:11] == raw_name:
                if if_absent and struct.unpack_from('<I', entry, 28)[0] == len(data):
                    print(f'{name} already present ({len(data)} B); leaving it unchanged')
                    return 'present'
                raise SystemExit(f'{name} already exists; refusing to overwrite')
        return slot

    root_offsets = [root_off + 32 * i for i in range(root_entries)]
    if dirname:
        if '/' in dirname:
            raise SystemExit('only one directory level is supported')
        raw_dir, _ = short_name(dirname)
        dir_cluster = None
        for off in root_offsets:
            entry = img[off:off + 32]
            if entry[0] == 0:
                break
            if entry[0] != 0xE5 and entry[11] != 0x0F and entry[11] & 0x10 and entry[:11] == raw_dir:
                dir_cluster = struct.unpack_from('<H', entry, 26)[0]
                break
        if dir_cluster is None:
            raise SystemExit(f'directory {dirname} not found in the root')
        offsets, c, eoc_min = [], dir_cluster, (0xFF8 if fat12 else 0xFFF8)
        while 2 <= c < eoc_min and len(offsets) < 65536:
            base = data_start + (c - 2) * cluster_bytes
            offsets += [base + 32 * i for i in range(cluster_bytes // 32)]
            c = fat_entry(fat, fat12, c)
        slot_off = find_slot(offsets)
        if slot_off == 'present':
            return 0
        if slot_off is None:
            raise SystemExit(f'directory {dirname} has no free slot in its clusters')
        where = f'{dirname}/ slot {offsets.index(slot_off)}'
    else:
        slot_off = find_slot(root_offsets)
        if slot_off == 'present':
            return 0
        if slot_off is None:
            raise SystemExit('root directory full')
        where = f'root slot {(slot_off - root_off) // 32}'

    need = max(1, (len(data) + cluster_bytes - 1) // cluster_bytes)
    free = [n for n in range(2, clusters + 2) if fat_entry(fat, fat12, n) == 0][:need]
    if len(free) < need:
        raise SystemExit('not enough free clusters')
    eoc = 0xFFF if fat12 else 0xFFFF
    for a, b in zip(free, free[1:] + [eoc]):
        set_fat_entry(fat, fat12, a, b)
    for k, c in enumerate(free):
        chunk = data[k * cluster_bytes:(k + 1) * cluster_bytes]
        off = data_start + (c - 2) * cluster_bytes
        img[off:off + cluster_bytes] = chunk.ljust(cluster_bytes, b'\0')
    for f in range(nfats):
        img[fat_off + f * fat_sectors * bps:fat_off + (f + 1) * fat_sectors * bps] = fat

    entry = bytearray(32)
    entry[0:11] = raw_name
    entry[11] = 0x20                      # archive
    entry[12] = case_flags
    struct.pack_into('<H', entry, 26, free[0])
    struct.pack_into('<I', entry, 28, len(data))
    img[slot_off:slot_off + 32] = entry
    open(image_path, 'wb').write(img)
    print(f'added {name} ({len(data)} B) at cluster {free[0]} '
          f'({"FAT12" if fat12 else "FAT16"}, {where})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
