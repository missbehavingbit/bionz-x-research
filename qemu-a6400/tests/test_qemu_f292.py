#!/usr/bin/env python3
"""L0100 qtest: the 0xF2920000 OTG/A/E per-offset rule windows.

Positive controls, one per declared class (F-0099 contract):
  RF   the offset reads back the last word written:
       0xF2920040, 0xF2920050, 0xF2920100 (kernel OTG) and 0xF292A010 (av-cam A).
  Z0   the offset reads 0 even after a write (status never written, or
       write-only config): 0xF2920010, 0xF292A044, 0xF292A070, 0xF292A074,
       0xF292A080, 0xF292E008, 0xF292E018.
The ruled offsets log no `unmodelled` line.

Negative controls:
  An unruled offset inside each mapped window (0xF2920044, 0xF292A100,
  0xF292E100) still logs both a read and a write as `cxd90045.*.unmodelled`,
  so a silent mapped window means "ruled", not "log disabled".
  The out-of-scope 0xF292C000 page is not mapped by this model and still
  reaches the board catch-all.

Run from the repository root:
  A6400_QEMU_DIR=loop/builds/L0100 python3 qemu-a6400/tests/test_qemu_f292.py
"""
import hashlib
import os
import select
import subprocess
import sys
import time
from pathlib import Path

QEMU = Path(os.environ["A6400_QEMU_DIR"]) / "build/qemu-system-arm"
OUT = Path(os.environ.get("L0100_OUT", "loop/scratch/L0100"))

OTG = 0xF2920000
AVCAM_A = 0xF292A000
AVCAM_E = 0xF292E000
OUT_OF_SCOPE = 0xF292C000


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
RF = [
    ("otg_0x0040", OTG + 0x0040),
    ("otg_0x0050", OTG + 0x0050),
    ("otg_0x0100", OTG + 0x0100),
    ("avcam_a_0x010", AVCAM_A + 0x010),
]
Z0 = [
    ("otg_0x0010", OTG + 0x0010, 0xFFFFFFFF),
    ("avcam_a_0x044", AVCAM_A + 0x044, 0x00000004),
    ("avcam_a_0x070", AVCAM_A + 0x070, 0x5A5A5A5A),
    ("avcam_a_0x074", AVCAM_A + 0x074, 0x00000001),
    ("avcam_a_0x080", AVCAM_A + 0x080, 0x00000002),
    ("avcam_e_0x008", AVCAM_E + 0x008, 0x00000001),
    ("avcam_e_0x018", AVCAM_E + 0x018, 0x00100000),
]
UNRULED = [
    ("otg", OTG + 0x0044),
    ("avcam_a", AVCAM_A + 0x100),
    ("avcam_e", AVCAM_E + 0x100),
]


def check(name, got, want):
    ok = got == want
    checks.append((name, got, want, ok))
    print("%s %s: got %#x want %#x" % ("PASS" if ok else "FAIL", name, got, want))


def proof(q):
    for i, (name, addr) in enumerate(RF):
        val = 0x10000000 + i
        q.wr(addr, val)
        check("RF." + name, q.rd(addr), val)

    for name, addr, val in Z0:
        q.wr(addr, val)
        check("Z0." + name, q.rd(addr), 0)

    for name, addr in UNRULED:
        q.wr(addr, 0xCAFEF00D)
        check("N.unruled_read_" + name, q.rd(addr), 0)

    # Out-of-scope C page: not part of the OTG/A/E windows. L0102 models it
    # with its own window (test_qemu_f292c.py), so these accesses must not
    # reach the board catch-all either.
    q.wr(OUT_OF_SCOPE + 0x004, 0x1)
    q.rd(OUT_OF_SCOPE + 0x004)
    q.rd(OUT_OF_SCOPE + 0x160)
    return 0


log = None
try:
    q = QTest("sony-a6400", OUT / "qtest-f292.log")
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


ruled_addrs = [a for _, a in RF] + [a for _, a, _ in Z0]
ruled_noise = []
for addr in ruled_addrs:
    ruled_noise += unmodelled("read", addr) + unmodelled("write", addr)
print("ruled_unmodelled_lines=%d" % len(ruled_noise))
for line in ruled_noise:
    print("  " + line)

unruled_reads = unruled_writes = 0
for name, addr in UNRULED:
    r = unmodelled("read", addr)
    w = unmodelled("write", addr)
    unruled_reads += len(r)
    unruled_writes += len(w)
    print("%s unruled read_lines=%d write_lines=%d" % (name, len(r), len(w)))
    for line in r + w:
        print("  " + line)

c_lines = (unmodelled("read", OUT_OF_SCOPE + 0x004) +
           unmodelled("write", OUT_OF_SCOPE + 0x004) +
           unmodelled("read", OUT_OF_SCOPE + 0x160))
print("out_of_scope_catch_all_lines=%d" % len(c_lines))
for line in c_lines:
    print("  " + line)

checks.append(("N.ruled_offsets_silent", len(ruled_noise), 0,
               not ruled_noise))
checks.append(("N.unruled_reads_logged", unruled_reads, len(UNRULED),
               unruled_reads == len(UNRULED)))
checks.append(("N.unruled_writes_logged", unruled_writes, len(UNRULED),
               unruled_writes == len(UNRULED)))
checks.append(("N.out_of_scope_catch_all", len(c_lines), 0, len(c_lines) == 0))

bad = [c for c in checks if not c[3]]
print("checks=%d failed=%d" % (len(checks), len(bad)))
sys.exit(1 if bad else 0)
