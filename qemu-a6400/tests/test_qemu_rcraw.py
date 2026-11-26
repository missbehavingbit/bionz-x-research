#!/usr/bin/env python3
"""L0105 qtest: the 0xF3030000 RCRAW/IDT per-offset rule window.

F-0105 measured 547 distinct catch-all (read|write, offset) pairs on this
64 KiB page in a certified 120 s boot: 532 write offsets and 15 read
offsets, 13 of which are written too.  The rule block stores every write.
Twelve read-follows-write offsets are RF (read back the last write); the two
read-only status words and all write-only offsets are ZERO (read 0), which is
the certified catch-all reply.

Positive controls:
  RF   each RF offset reads back the exact word written:
       0xF3030004/00C/1090/1094/10A8/10AC/10B8/10C0/10C4/10F8/17C0/17C8.
  Z0   each ZERO offset reads 0 even after a write, including the two
       read-only words 0xF3030084/0220 and a bulk zero-fill word
       0xF3031100.

Negative control:
  An unruled offset inside the mapped window (0xF3031800) still logs both a
  read and a write as `cxd90045.f303.unmodelled`, so a silent page means
  "ruled", not "log disabled".

Run from the repository root:
  A6400_QEMU_DIR=loop/builds/L0105 python3 qemu-a6400/tests/test_qemu_rcraw.py
"""
import hashlib
import os
import select
import subprocess
import sys
import time
from pathlib import Path

QEMU = Path(os.environ["A6400_QEMU_DIR"]) / "build/qemu-system-arm"
OUT = Path(os.environ.get("L0105_OUT", "loop/scratch/L0105"))

PAGE = 0xF3030000
RF_OFF = [0x004, 0x00C, 0x1090, 0x1094, 0x10A8, 0x10AC,
          0x10B8, 0x10C0, 0x10C4, 0x10F8, 0x17C0, 0x17C8]
Z0_OFF = [0x000, 0x008, 0x030, 0x084, 0x104, 0x108, 0x110,
          0x144, 0x148, 0x204, 0x208, 0x220, 0x304, 0x308,
          0x404, 0x408, 0x504, 0x508, 0x604, 0x608,
          0x1000, 0x1100, 0x1160, 0x17D0, 0x17FC]
UNRULED = ("unruled_1800", PAGE + 0x1800)


class QTest:
    def __init__(self, machine, log_path):
        self.log_path = log_path
        OUT.mkdir(parents=True, exist_ok=True)
        self.log = open(log_path, "wb")
        self.proc = subprocess.Popen(
            [str(QEMU), "-M", machine, "-accel", "qtest", "-display", "none",
             "-monitor", "none", "-serial", "none", "-net", "none",
             "-d", "unimp", "-qtest", "stdio"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.log, bufsize=0)
        self.pending = b""

    def cmd(self, text):
        self.proc.stdin.write((text + "\n").encode("ascii"))
        deadline = time.monotonic() + 5
        while b"\n" not in self.pending:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([self.proc.stdout], [], [], left)[0]:
                raise RuntimeError("qtest timeout: " + text)
            chunk = os.read(self.proc.stdout.fileno(), 4096)
            if not chunk:
                raise RuntimeError("qtest exited: " + text)
            self.pending += chunk
        line, self.pending = self.pending.split(b"\n", 1)
        if not line.startswith(b"OK"):
            raise RuntimeError(text + ": " + repr(line))
        return line

    def wr(self, addr, value):
        self.cmd("writel %#x %#x" % (addr, value))

    def rd(self, addr):
        return int(self.cmd("readl %#x" % addr).decode().split()[1], 16)

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.log.close()
        return Path(self.log_path).read_text(errors="replace")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


checks = []


def check(name, got, want):
    ok = got == want
    checks.append((name, got, want, ok))
    print("%s %s: got %#x want %#x" % ("PASS" if ok else "FAIL", name, got, want))


def proof(q):
    for i, off in enumerate(RF_OFF):
        addr = PAGE + off
        want = 0xA5A50000 + i
        q.wr(addr, want)
        check("RF.%04x_reads_last_write" % off, q.rd(addr), want)
    for i, off in enumerate(Z0_OFF):
        addr = PAGE + off
        q.wr(addr, 0x5A5A0000 + i)
        check("Z0.%04x_reads_zero" % off, q.rd(addr), 0)
    name, addr = UNRULED
    q.wr(addr, 0xCAFEF00D)
    check("N.%s_read" % name, q.rd(addr), 0)
    return 0


log = None
try:
    q = QTest("sony-a6400", OUT / "qtest-rcraw.log")
    try:
        proof(q)
    finally:
        log = q.close()
except RuntimeError as exc:
    print("FAIL qtest: %s" % exc)
    sys.exit(1)

print("qemu=" + str(QEMU))
print("qemu_sha256=" + sha256(QEMU))


def unmodelled(kind, addr):
    off = "0x%x" % (addr - 0xF0000000)
    return [l for l in log.splitlines()
            if "unimplemented device " + kind in l and "offset " + off in l]


ruled_noise = []
for off in RF_OFF + Z0_OFF:
    ruled_noise += unmodelled("read", PAGE + off) + unmodelled("write", PAGE + off)
print("ruled_unmodelled_lines=%d" % len(ruled_noise))
for line in ruled_noise[:10]:
    print("  " + line)

name, addr = UNRULED
unruled_reads = unmodelled("read", addr)
unruled_writes = unmodelled("write", addr)
print("%s unruled read_lines=%d write_lines=%d"
      % (name, len(unruled_reads), len(unruled_writes)))
for line in unruled_reads + unruled_writes:
    print("  " + line)

checks.append(("N.ruled_offsets_silent", len(ruled_noise), 0,
               not ruled_noise))
checks.append(("N.unruled_read_logged", len(unruled_reads), 1,
               len(unruled_reads) == 1))
checks.append(("N.unruled_write_logged", len(unruled_writes), 1,
               len(unruled_writes) == 1))

bad = [c for c in checks if not c[3]]
print("checks=%d failed=%d" % (len(checks), len(bad)))
sys.exit(1 if bad else 0)
