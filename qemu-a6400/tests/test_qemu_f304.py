#!/usr/bin/env python3
"""L0108 qtest: the 0xF3040000 per-offset rule window.

F-0108 measured 17 distinct catch-all (read|write, offset) pairs on the
0xF3040000 page in a certified 120 s boot: 13 write offsets and 4 read
offsets, 3 of which are written too.  The rule block stores every write.
The three read-follows-write offsets are RF (read back the last write); the
read-only status word 0x004 and the ten write-only offsets are ZERO (read 0),
which is the certified catch-all reply.

Positive controls:
  RF   each RF offset reads back the exact word written:
       0xF3040108/0110/0120.
  Z0   each ZERO offset reads 0 even after a write, including the read-only
       word 0xF3040004 and the bulk address words 0xF3040128..0xF3040138.

Negative control:
  An unruled offset inside the mapped window (0xF3041800, just past the AIF
  group at 0xF3041000..0xF30417FF) still logs both a read and a write as
  `cxd90045.f304.unmodelled`, so a silent page means "ruled", not "log
  disabled".

Run from the repository root:
  A6400_QEMU_DIR=loop/builds/L0108 python3 qemu-a6400/tests/test_qemu_f304.py
"""
import hashlib
import os
import select
import subprocess
import sys
import time
from pathlib import Path

QEMU = Path(os.environ["A6400_QEMU_DIR"]) / "build/qemu-system-arm"
OUT = Path(os.environ.get("L0108_OUT", "loop/scratch/L0108"))

PAGE = 0xF3040000
RF_OFF = [0x108, 0x110, 0x120]
Z0_OFF = [0x004, 0x008, 0x100, 0x11C, 0x124, 0x128,
          0x12C, 0x130, 0x134, 0x138, 0x13C]
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
    q = QTest("sony-a6400", OUT / "qtest-f304.log")
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
