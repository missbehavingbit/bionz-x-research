#!/usr/bin/env python3
"""Extract im.elf's debug log ("ulog": "[SSSSS.UUUUUU] < pid:: x> msg") and
av-cam's bracketed log lines from a RAM dump of the av-cam/Backup carve-out.

    tools/ulog.py out/avram.bin [base=0x02E68000]

Make the dump with the QEMU monitor:
    pmemsave 0x02E68000 0x1A5E000 "out/avram.bin"
"""
import re
import sys

ULOG = re.compile(rb'\[(\d{5}\.\d{6})\] < *(\w+):: *(\w)> ([\x20-\x7e]{1,300})')


def main():
    d = open(sys.argv[1], 'rb').read()
    lines = {}
    for m in ULOG.finditer(d):
        lines[(m.group(1), m.group(4))] = '[%s] <%s:%s> %s' % (
            m.group(1).decode(), m.group(2).decode(), m.group(3).decode(),
            m.group(4).decode().rstrip())
    for k in sorted(lines):
        print(lines[k])


if __name__ == '__main__':
    main()
