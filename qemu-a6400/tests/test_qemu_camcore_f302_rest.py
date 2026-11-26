#!/usr/bin/env python3
"""L0093 qtest: the 0xF3020C00..0xF3020FFF CamCore rule window.

Positive control: ruled write-only (Z0) words in the new window have no
catch-all log line and read 0.  The L0090 window at 0xF3020800..0xF3020BFF is
still selected for its own sub-window (Z0 +0x808, RF +0xB80), which also
guards the two-table selector.  Negative control: an unruled word inside the
new mapped window that the certified boot never touches still logs both ways
with the catch-all offset base.

Run: A6400_QEMU_DIR=loop/builds/L0093 python3 qemu-a6400/tests/test_qemu_camcore_f302_rest.py
"""
import hashlib
import os
import select
import subprocess
import sys
import time
from pathlib import Path

QEMU = Path(os.environ["A6400_QEMU_DIR"]) / "build/qemu-system-arm"
OUT = Path(os.environ.get("L0093_OUT", "loop/scratch/L0093"))
BASE = 0xF3020C00
ZERO_OFF = BASE + 0x08          # block-relative 0xC08, ruled Z0
ZERO_OFF2 = BASE + 0x170        # block-relative 0xD70, ruled Z0
UNRULED = BASE + 0x104          # block-relative 0xD04, never touched by boot
OLD_ZERO = 0xF3020808           # L0090 window: ruled Z0
OLD_RF = 0xF3020B80             # L0090 window: ruled RF


class QTest:
    def __init__(self, machine, log_path):
        self.log_path = log_path
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


def run(body, log_name):
    q = QTest("sony-a6400", OUT / log_name)
    try:
        result = body(q)
    finally:
        log = q.close()
    return result, log


checks = []


def check(name, got, want):
    ok = got == want
    checks.append((name, got, want, ok))
    print("%s %s: got %#x want %#x" % ("PASS" if ok else "FAIL", name, got, want))


def proof(q):
    # Positive: two ruled Z0 words in the new window store but read 0.
    q.wr(ZERO_OFF, 0x12345678)
    check("P.rest_z0_reads_0", q.rd(ZERO_OFF), 0)
    q.wr(ZERO_OFF2, 0xA5A5A5A5)
    check("P.rest_z0b_reads_0", q.rd(ZERO_OFF2), 0)
    # Positive: the L0090 sub-window still uses its own table.
    q.wr(OLD_ZERO, 0x11111111)
    check("P.old_z0_reads_0", q.rd(OLD_ZERO), 0)
    q.wr(OLD_RF, 0xDEADBEEF)
    check("P.old_rf_reads_back", q.rd(OLD_RF), 0xDEADBEEF)
    # Negative: unruled word inside the new mapped window still logs.
    q.wr(UNRULED, 0xCAFEBABE)
    _ = q.rd(UNRULED)
    return 0


_, log = run(proof, "qtest-f302-rest.log")

print("qemu=" + str(QEMU))
print("qemu_sha256=" + sha256(QEMU))

ruled_lines = [l for l in log.splitlines()
               if "0x3020c08" in l.lower() or "0x3020d70" in l.lower()
               or "0x3020808" in l.lower() or "0x3020b80" in l.lower()]
neg_w = [l for l in log.splitlines()
         if "unimplemented device write" in l and "offset 0x3020d04" in l.lower()]
neg_r = [l for l in log.splitlines()
         if "unimplemented device read" in l and "offset 0x3020d04" in l.lower()]
print("ruled_log_lines=%d" % len(ruled_lines))
for line in ruled_lines:
    print("  " + line)
print("unruled_write_log_lines=%d" % len(neg_w))
for line in neg_w:
    print("  " + line)
print("unruled_read_log_lines=%d" % len(neg_r))
for line in neg_r:
    print("  " + line)

checks.append(("N.ruled_rest_z0_not_logged",
               len([l for l in ruled_lines if "0x3020c08" in l.lower()]), 0,
               not [l for l in ruled_lines if "0x3020c08" in l.lower()]))
checks.append(("N.ruled_rest_z0b_not_logged",
               len([l for l in ruled_lines if "0x3020d70" in l.lower()]), 0,
               not [l for l in ruled_lines if "0x3020d70" in l.lower()]))
checks.append(("N.old_z0_not_logged",
               len([l for l in ruled_lines if "0x3020808" in l.lower()]), 0,
               not [l for l in ruled_lines if "0x3020808" in l.lower()]))
checks.append(("N.old_rf_not_logged",
               len([l for l in ruled_lines if "0x3020b80" in l.lower()]), 0,
               not [l for l in ruled_lines if "0x3020b80" in l.lower()]))
checks.append(("N.unruled_write_logged", len(neg_w), 1, len(neg_w) == 1))
checks.append(("N.unruled_read_logged", len(neg_r), 1, len(neg_r) == 1))

bad = [c for c in checks if not c[3]]
print("checks=%d failed=%d" % (len(checks), len(bad)))
sys.exit(1 if bad else 0)
