#!/usr/bin/env python3
"""Continuously sample Sony's klog ring through the QEMU HMP monitor.

    tools/klogtap.py MONITOR_SOCK OUT_TXT [seconds] [interval]

Keeps one monitor connection, pmemsaves the ring (klog.addr=0x035A5000,
0x20000 bytes) every `interval` seconds and appends every new, complete,
timestamped line to OUT_TXT (deduplicated, noise filtered).  At the end the
file is rewritten sorted by kernel timestamp.
"""
import os
import re
import socket
import sys
import tempfile
import time

KLOG_ADDR, KLOG_SIZE = 0x035A5000, 0x20000
NOISE = ('Software Tuning is forbiden', 'hwtimer', 'caught up', 'expire(')
LINE = re.compile(r'\[\s*(\d+\.\d{6})\] ([\x20-\x7e]*)')


class Monitor:
    def __init__(self, path):
        for _ in range(100):
            try:
                self.s = socket.socket(socket.AF_UNIX)
                self.s.connect(path)
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise SystemExit('cannot connect to ' + path)
        self.s.settimeout(10)
        self.wait()

    def wait(self):
        buf = b''
        while not buf.rstrip().endswith(b'(qemu)'):
            chunk = self.s.recv(65536)
            if not chunk:
                raise EOFError
            buf += chunk
        return buf

    def cmd(self, c):
        self.s.sendall(c.encode() + b'\r\n')
        return self.wait()


def main():
    sock, out = sys.argv[1], sys.argv[2]
    secs = float(sys.argv[3]) if len(sys.argv) > 3 else 120
    step = float(sys.argv[4]) if len(sys.argv) > 4 else 0.25
    mon = Monitor(sock)
    tmp = os.path.join(tempfile.mkdtemp(), 'klog.bin')
    seen = {}
    end = time.time() + secs
    with open(out, 'w') as f:
        while time.time() < end:
            try:
                mon.cmd('pmemsave 0x%x 0x%x "%s"' % (KLOG_ADDR, KLOG_SIZE, tmp))
            except (EOFError, OSError):
                break
            txt = re.sub(rb'[^\x20-\x7e]', b'\n', open(tmp, 'rb').read()).decode()
            for m in LINE.finditer(txt):
                ts, msg = float(m.group(1)), m.group(2)
                if any(n in msg for n in NOISE) or (ts, msg) in seen:
                    continue
                seen[(ts, msg)] = 1
                f.write('[%12.6f] %s\n' % (ts, msg))
            f.flush()
            time.sleep(step)
    lines = sorted(seen)
    # drop lines that are a truncated prefix of another line with the same timestamp
    keep = [l for i, l in enumerate(lines)
            if not (i + 1 < len(lines) and lines[i + 1][0] == l[0]
                    and lines[i + 1][1].startswith(l[1]))]
    with open(out, 'w') as f:
        for ts, msg in keep:
            f.write('[%12.6f] %s\n' % (ts, msg))
    print('%s: %d lines' % (out, len(keep)))


if __name__ == '__main__':
    main()
