#!/usr/bin/env python3
"""Summarise unmodelled MMIO traffic from QEMU's "-d unimp" log, streamed.

    mkfifo out/unimp.fifo
    run.sh -d unimp -D out/unimp.fifo &   # (tools/hwscan.sh does this)
    tools/unimp_stats.py out/unimp.fifo out/unimp.txt

Writes a per-register table (reads, writes, last value written, first
sequence number) sorted by address, then the top hot registers -- a register
read hundreds of thousands of times is almost always a poll loop waiting
for hardware the machine does not model yet.
"""
import collections
import re
import sys

BASES = {'a7c.io': 0xF0000000}
PAT = re.compile(r'(\S+): unimplemented device (read|write)\s+\(size (\d+), '
                 r'offset 0x([0-9a-f]+)(?:, value 0x([0-9a-f]+))?')


def main():
    src, out = sys.argv[1], sys.argv[2]
    reads = collections.Counter()
    writes = collections.Counter()
    last = {}
    first = {}
    seq = 0
    with open(src, errors='replace') as f:
        for line in f:
            m = PAT.search(line)
            if not m:
                continue
            base = BASES.get(m.group(1), 0)
            a = base + int(m.group(4), 16)
            seq += 1
            first.setdefault(a, seq)
            if m.group(2) == 'read':
                reads[a] += 1
            else:
                writes[a] += 1
                last[a] = m.group(5)
    with open(out, 'w') as o:
        o.write('%d accesses\n\nHOT (most reads):\n' % seq)
        for a, n in reads.most_common(15):
            o.write('  %08x  reads %9d  writes %6d  first #%d\n' % (a, n, writes[a], first[a]))
        o.write('\nALL (by address): addr reads writes last_write first#\n')
        for a in sorted(set(reads) | set(writes)):
            o.write('  %08x %9d %7d %10s %9d\n' % (a, reads[a], writes[a], last.get(a, '-'), first[a]))
    print('%s: %d accesses, %d registers' % (out, seq, len(first)))


if __name__ == '__main__':
    main()
