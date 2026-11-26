#!/usr/bin/env python3
"""L0114 qtest: the 0xF0220000 FUSB per-offset rule window, ZERO-only rows.

F-0106 measured 5 distinct offsets / 9 unmodelled (read|write, offset) pairs
in the kernel UDIF block 'add' (PA 0xF0220000, size 0x20000): +0x0, +0x4,
+0x11008, +0x11080 and +0x11084.  L0114 rules all five ZERO (read returns 0,
writes accepted and dropped), the catch-all-equivalent value.  The RF rows
of L0112 were non-deterministic (F-0112) and are not in the table any more.

Static check:
  cxd90045_f022_rules in sony_a6400.c has exactly the five offsets, every
  row is CXD90045_F292_ZERO, and no CXD90045_F292_RF row remains.

Positive controls:
  Z0   each ruled offset reads 0 even after a write: 0xF0220000,
       0xF0220004, 0xF0231008, 0xF0231080, 0xF0231084.

Negative control:
  An unruled offset inside the window (0xF0225000) still logs both a read and
  a write as `cxd90045.f022.unmodelled`, so a silent block means "ruled", not
  "log disabled".

Run from the repository root:
  A6400_QEMU_DIR=loop/builds/L0114 python3 qemu-a6400/tests/test_qemu_f022.py
"""
import hashlib
import os
import re
import select
import subprocess
import sys
import time
from pathlib import Path

QEMU = Path(os.environ["A6400_QEMU_DIR"]) / "build/qemu-system-arm"
OUT = Path(os.environ.get("L0112_OUT", "loop/scratch/L0112"))

BASE = 0xF0220000
RULED_OFF = [0x00000, 0x00004, 0x11008, 0x11080, 0x11084]
UNRULED = ("unruled_5000", BASE + 0x5000)
SOURCE = Path("qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c")


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
    print("%s %s: got %r want %r" % ("PASS" if ok else "FAIL", name, got, want))


def static_table():
    text = SOURCE.read_text(errors="replace")
    start = text.index("static const Cxd90045F292Rule cxd90045_f022_rules[]")
    body = text[start:text.index("};", start)]
    rows = re.findall(r"\{\s*(0x[0-9a-fA-F]+),\s*(CXD90045_F292_\w+),", body)
    return [(int(off, 16), kind) for off, kind in rows]


def static_check():
    rows = static_table()
    offs = sorted(off for off, _ in rows)
    check("S.rows_are_the_five_offsets", offs, sorted(RULED_OFF))
    check("S.no_RF_row", sum(k == "CXD90045_F292_RF" for _, k in rows), 0)
    check("S.all_rows_ZERO", sum(k == "CXD90045_F292_ZERO" for _, k in rows),
          len(RULED_OFF))


def proof(q):
    for i, off in enumerate(RULED_OFF):
        addr = BASE + off
        q.wr(addr, 0x5A5A0000 + i)
        check("Z0.%05x_reads_zero" % off, q.rd(addr), 0)
    name, addr = UNRULED
    q.wr(addr, 0xCAFEF00D)
    check("N.%s_read" % name, q.rd(addr), 0)
    return 0


static_check()

try:
    q = QTest("sony-a6400", OUT / "qtest-f022.log")
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
for off in RULED_OFF:
    ruled_noise += unmodelled("read", BASE + off) + unmodelled("write", BASE + off)
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
