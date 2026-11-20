#!/usr/bin/env python3
"""Read a Sony warm-boot image (WBI, partition nflasha5): the RAM snapshot the
camera resumes from.  Self-contained (format as documented by fwtool.py's
sony/wbi.py and the kernel's lib/lz77/lz77_inflate.c).

    tools/wbi.py nflasha5.raw list
    tools/wbi.py nflasha5.raw read PA LEN OUT      # bytes of one PA range
    tools/wbi.py nflasha5.raw dump OUTDIR          # every section -> OUTDIR/<pa>.bin

Layout: header {magic "WBI1", nsect, flag (bit0 compressed), resume vector,
version 0x20060224, sector size, data size, ...}; data starts at the first
non-empty sector after the header; section table (32 bytes each:
addr, size, checksum, flag, osize, virt, pad, metachecksum) follows the data.
Section data is a sequence of LZ77 blocks (type 0x0F = stored, 0xF0 = coded).
"""
import os
import struct
import sys

LENGTHS = list(range(3, 17)) + [32, 64]


def inflate_block(d, p):
    """Decode one LZ77 block at d[p:]; return (bytes, new position)."""
    t = d[p]
    p += 1
    if t == 0x0F:
        n = d[p + 1] | d[p + 2] << 8
        p += 3
        return d[p:p + n], p + n
    if t != 0xF0:
        raise ValueError('bad LZ77 block type 0x%02x at 0x%x' % (t, p - 1))
    out = bytearray()
    while True:
        flags = d[p]
        p += 1
        if flags == 0:
            out += d[p:p + 8]
            p += 8
            continue
        for i in range(8):
            if (flags >> i) & 1:
                a, b = d[p], d[p + 1]
                p += 2
                dist = (a & 0xF) << 8 | b
                if dist == 0:
                    return bytes(out), p
                n = LENGTHS[a >> 4]
                start = len(out) - dist
                if dist >= n:
                    out += out[start:start + n]
                else:
                    for k in range(n):
                        out.append(out[start + k])
            else:
                out.append(d[p])
                p += 1


class Wbi:
    def __init__(self, path):
        self.d = open(path, 'rb').read()
        d = self.d
        (magic, self.nsect, self.flag, self.resume, ver, self.ssize,
         self.dsize) = struct.unpack_from('<4s6I', d, 0)
        if magic != b'WBI1' or ver != 0x20060224 or not self.flag & 1:
            raise SystemExit('not a compressed WBI1 image')
        hdr = self.ssize
        while d[hdr:hdr + self.ssize].strip(b'\0') == b'' or \
                d[hdr:hdr + self.ssize].strip(b'\xff') == b'':
            hdr += self.ssize
        self.base = hdr
        self.sections = []
        off = 0
        for i in range(self.nsect):
            addr, size, _, flag, osize, virt = struct.unpack_from(
                '<6I', d, hdr + self.dsize + 32 * i)
            self.sections.append((addr, virt, osize, hdr + off, size))
            off += size

    def section(self, i):
        addr, virt, osize, foff, size = self.sections[i]
        d = self.d
        p, out = foff, bytearray()
        while len(out) < osize:
            blk, p = inflate_block(d, p)
            out += blk
        return bytes(out[:osize])

    def read(self, pa, n):
        for i, (addr, virt, osize, foff, size) in enumerate(self.sections):
            if addr <= pa and pa + n <= addr + osize:
                return self.section(i)[pa - addr:pa - addr + n]
        raise SystemExit('PA 0x%08x+0x%x not in the snapshot' % (pa, n))


def main():
    w = Wbi(sys.argv[1])
    cmd = sys.argv[2] if len(sys.argv) > 2 else 'list'
    if cmd == 'list':
        print('resume vector 0x%08x, %d sections' % (w.resume, w.nsect))
        for addr, virt, osize, foff, size in w.sections:
            print('  PA %08x  VA %08x  %8x bytes (%x compressed)' % (addr, virt, osize, size))
    elif cmd == 'read':
        open(sys.argv[5], 'wb').write(w.read(int(sys.argv[3], 0), int(sys.argv[4], 0)))
    elif cmd == 'dump':
        os.makedirs(sys.argv[3], exist_ok=True)
        for i, (addr, *_r) in enumerate(w.sections):
            open(os.path.join(sys.argv[3], '%08x.bin' % addr), 'wb').write(w.section(i))
            print('%08x' % addr, flush=True)


if __name__ == '__main__':
    main()
