#!/usr/bin/env python3
"""L0102 qtest: the 0xF292C000 per-offset rule window.

F-0101 measured 11 distinct (read|write, offset) pairs / 41 accesses on this
page: ten write-only offsets (PC 0x5F519BEC) and the +0x160 poll (PC
0x5F519B34, never written).  There is no read-follows-write on the page, so
every offset is Z0: a write is stored silently and a read returns 0, which is
the certified catch-all baseline for these offsets.

Positive controls:
  Z0   each of the 11 observed offsets reads 0 even after a write:
       0xF292C004/008/104/108/114/118/124/128/154/158/160.
  RF   the shared cxd90045_f292 rule block still reads back the last write at
       an existing RF offset (0xF2920040); the C page has no RF offset, so
       this is a code-path control, not a C-page claim.

Negative control:
  An unruled offset inside the mapped page (0xF292C200) still logs both a
  read and a write as `cxd90045.f292-c.unmodelled`, so a silent page means
  "ruled", not "log disabled".

Run from the repository root:
  A6400_QEMU_DIR=loop/builds/L0102 python3 qemu-a6400/tests/test_qemu_f292c.py
"""
import hashlib
import os
import select
import subprocess
import sys
import time
from pathlib import Path

QEMU = Path(os.environ["A6400_QEMU_DIR"]) / "build/qemu-system-arm"
OUT = Path(os.environ.get("L0102_OUT", "loop/scratch/L0102"))

CPAGE = 0xF292C000
CPAGE_RULES = [
    ("0x004", CPAGE + 0x004),
    ("0x008", CPAGE + 0x008),
    ("0x104", CPAGE + 0x104),
    ("0x108", CPAGE + 0x108),
    ("0x114", CPAGE + 0x114),
    ("0x118", CPAGE + 0x118),
    ("0x124", CPAGE + 0x124),
    ("0x128", CPAGE + 0x128),
    ("0x154", CPAGE + 0x154),
    ("0x158", CPAGE + 0x158),
    ("0x160", CPAGE + 0x160),
]
UNRULED = ("c_0x200", CPAGE + 0x200)
OTG_RF = ("otg_0x040_rf_control", 0xF2920040)


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
    for i, (name, addr) in enumerate(CPAGE_RULES):
        q.wr(addr, 0xA5A50000 + i)
        check("Z0.c_" + name, q.rd(addr), 0)

    name, addr = OTG_RF
    q.wr(addr, 0x5EED0040)
    check("RF.code_path_" + name, q.rd(addr), 0x5EED0040)

    name, addr = UNRULED
    q.wr(addr, 0xCAFEF00D)
    check("N.unruled_read_" + name, q.rd(addr), 0)
    return 0


log = None
try:
    q = QTest("sony-a6400", OUT / "qtest-f292c.log")
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
for _, addr in CPAGE_RULES:
    ruled_noise += unmodelled("read", addr) + unmodelled("write", addr)
print("ruled_unmodelled_lines=%d" % len(ruled_noise))
for line in ruled_noise:
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
