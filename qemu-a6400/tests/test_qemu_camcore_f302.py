#!/usr/bin/env python3
"""L0090 qtest: the 0xF3020800..0xF3020BFF CamCore rule window.

Positive control: a ruled offset (a write-only Z0 word and the RMW +0xB80
RF word) has no catch-all log line, returns 0 for Z0 and the last write for RF.
Negative control: an unruled word inside the mapped window that the certified
boot never touches still logs as unmodelled with the catch-all offset base.

Run: A6400_QEMU_DIR=loop/builds/L0090 python3 qemu-a6400/tests/test_qemu_camcore_f302.py
"""
import hashlib
import os
import select
import subprocess
import sys
import time
from pathlib import Path

QEMU = Path(os.environ["A6400_QEMU_DIR"]) / "build/qemu-system-arm"
OUT = Path(os.environ.get("L0090_OUT", "loop/scratch/L0090"))
BASE = 0xF3020800
ZERO_OFF = BASE + 0x08          # block-relative 0x808, ruled Z0
RF_OFF = BASE + 0x380           # block-relative 0xB80, ruled RF
UNRULED = BASE + 0x3F0          # block-relative 0xBF0, never touched by boot


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
    # Positive: ruled Z0 word stores but reads 0 and does not log.
    q.wr(ZERO_OFF, 0x12345678)
    check("P.z0_reads_0", q.rd(ZERO_OFF), 0)
    # Positive: ruled RF word reads back the last write and does not log.
    q.wr(RF_OFF, 0xDEADBEEF)
    check("P.rf_reads_back", q.rd(RF_OFF), 0xDEADBEEF)
    q.wr(RF_OFF, 0x00010200)
    check("P.rf_reads_last", q.rd(RF_OFF), 0x00010200)
    # Negative: unruled word inside the mapped window still logs.
    q.wr(UNRULED, 0xCAFEBABE)
    _ = q.rd(UNRULED)
    return 0


_, log = run(proof, "qtest-f302.log")

print("qemu=" + str(QEMU))
print("qemu_sha256=" + sha256(QEMU))

pos = [l for l in log.splitlines() if "0x3020808" in l or "0x3020b80" in l]
neg_w = [l for l in log.splitlines()
         if "unimplemented device write" in l and "offset 0x3020bf0" in l]
neg_r = [l for l in log.splitlines()
         if "unimplemented device read" in l and "offset 0x3020bf0" in l]
print("ruled_log_lines=%d" % len(pos))
for line in pos:
    print("  " + line)
print("unruled_write_log_lines=%d" % len(neg_w))
for line in neg_w:
    print("  " + line)
print("unruled_read_log_lines=%d" % len(neg_r))
for line in neg_r:
    print("  " + line)

checks.append(("N.ruled_z0_not_logged",
               len([l for l in pos if "0x3020808" in l]), 0,
               not [l for l in pos if "0x3020808" in l]))
checks.append(("N.ruled_rf_not_logged",
               len([l for l in pos if "0x3020b80" in l]), 0,
               not [l for l in pos if "0x3020b80" in l]))
checks.append(("N.unruled_write_logged", len(neg_w), 1, len(neg_w) == 1))
checks.append(("N.unruled_read_logged", len(neg_r), 1, len(neg_r) == 1))

bad = [c for c in checks if not c[3]]
print("checks=%d failed=%d" % (len(checks), len(bad)))
sys.exit(1 if bad else 0)
