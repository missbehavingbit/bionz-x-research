#!/usr/bin/env python3
"""Build a ready-formatted memory-card image for the emulator (no root, no mtools needed).

    python3 tools/make_sdcard.py out/sdcard.img [--size 4G] [--image a.jpg ...] [--force]

The card is laid out the way the SD Association's formatter (and a camera) lays out an SDHC card:
MBR with one FAT32 (LBA, type 0x0C) partition starting at sector 8192 (4 MiB), 32 KiB clusters,
reserved sectors chosen so the data area starts on a 4 MiB boundary. The pictures go to
DCIM/100MSDCF/DSC00001.JPG, DSC00002.JPG, ... (DCF names), plus the empty marker file
PRIVATE/SONY/SONYCARD.IND that the camera's own format creates. Without --image the generated test
picture tools/a7c_test_image.jpg is used. The file is sparse: only the written parts use disk space.
Size 4G..32G (SDHC). Use your own camera's JPEGs with --image for the best compatibility.
"""

import argparse
import os
import struct
import sys
import time
from pathlib import Path

SECTOR = 512
PART_START = 8192            # 4 MiB, SD-formatter alignment
SPC = 64                     # sectors per cluster (32 KiB)
ALIGN = 8192                 # data area on a 4 MiB boundary
EOC = 0x0FFFFFFF


def fat_datetime(t):
    lt = time.localtime(t)
    date = ((lt.tm_year - 1980) << 9) | (lt.tm_mon << 5) | lt.tm_mday
    tim = (lt.tm_hour << 11) | (lt.tm_min << 5) | (lt.tm_sec // 2)
    return date, tim


def dirent(name83, attr, cluster, size, t):
    date, tim = fat_datetime(t)
    assert len(name83) == 11
    return struct.pack("<11sBBBHHHHHHHI", name83, attr, 0, 0, tim, date, date,
                       cluster >> 16, tim, date, cluster & 0xFFFF, size)


def name83(name):
    base, _, ext = name.upper().partition(".")
    return (base.ljust(8)[:8] + ext.ljust(3)[:3]).encode("ascii")


def parse_size(s):
    s = s.strip().upper()
    mult = {"G": 1 << 30, "M": 1 << 20}.get(s[-1], 1)
    return int(float(s[:-1]) * mult) if s[-1] in "GM" else int(s)


def build(path, size, images, label="NO NAME    ", sony_marker=True):
    total = size // SECTOR
    part_sectors = total - PART_START
    # FAT size / reserved sectors so that (reserved + 2*fatsz) % ALIGN == 0
    fatsz = 1
    for _ in range(20):
        reserved = 32
        while (reserved + 2 * fatsz) % ALIGN:
            reserved += 1
        clusters = (part_sectors - reserved - 2 * fatsz) // SPC
        need = ((clusters + 2) * 4 + SECTOR - 1) // SECTOR
        if need <= fatsz:
            break
        fatsz = need
    data_start = reserved + 2 * fatsz            # relative to the partition
    if clusters < 65525:
        sys.exit("card too small for FAT32 (use --size 4G or more)")

    now = time.time()
    fat = {0: 0x0FFFFFF8, 1: EOC}
    clusters_data = {}
    next_cl = [2]

    def alloc(nbytes):
        n = max(1, (nbytes + SPC * SECTOR - 1) // (SPC * SECTOR))
        first = next_cl[0]
        for i in range(n):
            fat[first + i] = first + i + 1 if i < n - 1 else EOC
        next_cl[0] += n
        return first

    root = alloc(SPC * SECTOR)
    dcim = alloc(SPC * SECTOR)
    sub = alloc(SPC * SECTOR)
    if sony_marker:
        priv = alloc(SPC * SECTOR)
        sony = alloc(SPC * SECTOR)
    files = []
    for i, img in enumerate(images, 1):
        data = Path(img).read_bytes()
        name = "DSC%05d.JPG" % i
        cl = alloc(len(data))
        clusters_data[cl] = data
        files.append((name, cl, len(data)))

    root_dir = dirent(name83("DCIM"), 0x10, dcim, 0, now)
    if sony_marker:
        # PRIVATE/SONY/SONYCARD.IND: the empty marker file libInfraFsys Fsys_Format
        # (.text+0x1E61A) creates on every card the camera formats
        root_dir += dirent(name83("PRIVATE"), 0x10, priv, 0, now)
        clusters_data[priv] = dirent(b".          ", 0x10, priv, 0, now) + \
            dirent(b"..         ", 0x10, 0, 0, now) + dirent(name83("SONY"), 0x10, sony, 0, now)
        clusters_data[sony] = dirent(b".          ", 0x10, sony, 0, now) + \
            dirent(b"..         ", 0x10, priv, 0, now) + \
            dirent(name83("SONYCARD.IND"), 0x20, 0, 0, now)
    dcim_dir = dirent(b".          ", 0x10, dcim, 0, now) + dirent(b"..         ", 0x10, 0, 0, now) + \
        dirent(name83("100MSDCF"), 0x10, sub, 0, now)
    sub_dir = dirent(b".          ", 0x10, sub, 0, now) + dirent(b"..         ", 0x10, dcim, 0, now)
    for name, cl, ln in files:
        sub_dir += dirent(name83(name), 0x20, cl, ln, now)
    clusters_data[root] = root_dir
    clusters_data[dcim] = dcim_dir
    clusters_data[sub] = sub_dir

    used = next_cl[0] - 2
    free = clusters - used

    # --- MBR
    mbr = bytearray(SECTOR)
    def chs(lba):
        c, r = divmod(lba, 255 * 63)
        h, s = divmod(r, 63)
        c = min(c, 1023)
        return bytes([h, ((c >> 2) & 0xC0) | (s + 1), c & 0xFF])
    mbr[446:462] = bytes([0x00]) + chs(PART_START) + bytes([0x0C]) + chs(total - 1) + \
        struct.pack("<II", PART_START, part_sectors)
    mbr[510:512] = b"\x55\xaa"

    # --- boot sector
    vbr = bytearray(SECTOR)
    vbr[0:3] = b"\xEB\x58\x90"
    vbr[3:11] = b"MSDOS5.0"
    struct.pack_into("<HBHBHHBHHHII", vbr, 11, SECTOR, SPC, reserved, 2, 0, 0, 0xF8, 0, 63, 255,
                     PART_START, part_sectors)
    struct.pack_into("<IHHIHH", vbr, 36, fatsz, 0, 0, root, 1, 6)
    struct.pack_into("<BBBI11s8s", vbr, 64, 0x80, 0, 0x29, int(now) & 0xFFFFFFFF,
                     label.encode("ascii")[:11].ljust(11), b"FAT32   ")
    vbr[510:512] = b"\x55\xaa"
    fsinfo = bytearray(SECTOR)
    struct.pack_into("<I", fsinfo, 0, 0x41615252)
    struct.pack_into("<III", fsinfo, 484, 0x61417272, free, next_cl[0])
    struct.pack_into("<I", fsinfo, 508, 0xAA550000)

    # --- FAT (only the used part is non-zero)
    nfat = max(fat) + 1
    fat_bytes = bytearray(4 * nfat)
    for k, v in fat.items():
        struct.pack_into("<I", fat_bytes, 4 * k, v)

    with open(path, "wb") as f:
        f.truncate(size)
        def put(sector, data):
            f.seek(sector * SECTOR)
            f.write(data)
        put(0, mbr)
        p = PART_START
        put(p, vbr)
        put(p + 1, fsinfo)
        put(p + 6, vbr)
        put(p + 7, fsinfo)
        put(p + reserved, fat_bytes)
        put(p + reserved + fatsz, fat_bytes)
        for cl, data in clusters_data.items():
            put(p + data_start + (cl - 2) * SPC, data)
    return clusters, files


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("output")
    ap.add_argument("--size", default="4G", help="card size, 4G..32G (default 4G)")
    ap.add_argument("--image", action="append", default=[],
                    help="JPEG to put on the card (repeatable; default: tools/a7c_test_image.jpg)")
    ap.add_argument("--force", action="store_true", help="replace an existing card image")
    ap.add_argument("--no-sony-marker", action="store_true",
                    help="leave out PRIVATE/SONY/SONYCARD.IND (the file a Sony camera's format creates)")
    a = ap.parse_args()
    size = parse_size(a.size)
    if not (4 << 30) <= size <= (32 << 30):
        sys.exit("size must be 4G..32G")
    if os.path.exists(a.output) and not a.force:
        sys.exit(f"{a.output} exists (use --force to replace it)")
    images = a.image or [str(here / "a7c_test_image.jpg")]
    for img in images:
        if Path(img).read_bytes()[:2] != b"\xff\xd8":
            sys.exit(f"{img} is not a JPEG")
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    clusters, files = build(a.output, size, images, sony_marker=not a.no_sony_marker)
    print(f"{a.output}: {size >> 30} GB FAT32 card, {clusters} clusters of 32 KiB")
    for name, _, ln in files:
        print(f"  DCIM/100MSDCF/{name}  {ln} bytes")


if __name__ == "__main__":
    main()
