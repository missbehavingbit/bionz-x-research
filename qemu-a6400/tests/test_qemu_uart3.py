#!/usr/bin/env python3
"""L0098 qtest: UART3 is a real PL011 at 0xF2003000.

Positive control: the eight PrimeCell ID registers at +0xFE0..+0xFFC read
the kernel's expected AMBA values (PID 0x00141011, CID 0xB105F00D) and are
served by the PL011 model, so the cxd90045.unmodelled catch-all logs nothing
for them.  UART0 still exposes the same IDs and an idle PL011 flag register,
so the console device is unchanged.

Negative control: the next page (0xF2004000), which no device owns, still
logs as `cxd90045.unmodelled`, certifying that the ID-read log is a meter
and that a silent 0xF2003FE0 means "mapped", not "log disabled".

Run from the repository root:
  A6400_QEMU_DIR=loop/builds/L0098 python3 qemu-a6400/tests/test_qemu_uart3.py
"""
import hashlib
import os
import select
import subprocess
import sys
import time
from pathlib import Path

QEMU = Path(os.environ["A6400_QEMU_DIR"]) / "build/qemu-system-arm"
OUT = Path(os.environ.get("L0098_OUT", "loop/scratch/L0098"))
UART0 = 0xF2000000
UART3 = 0xF2003000
ID_BASE = 0xFE0
UNMAPPED = 0xF2004000


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


def primecell_ids(q, base):
    """Assemble the 32-bit PID and CID from the eight byte-valued registers."""
    pid = cid = 0
    for i in range(4):
        pid |= (q.rd(base + ID_BASE + 4 * i) & 0xFF) << (8 * i)
        cid |= (q.rd(base + ID_BASE + 0x10 + 4 * i) & 0xFF) << (8 * i)
    return pid, cid


checks = []


def check(name, got, want):
    ok = got == want
    checks.append((name, got, want, ok))
    print("%s %s: got %#x want %#x" % ("PASS" if ok else "FAIL", name, got, want))


def proof(q):
    uart3_pid, uart3_cid = primecell_ids(q, UART3)
    check("P.uart3_pid", uart3_pid, 0x00141011)
    check("P.uart3_cid", uart3_cid, 0xB105F00D)

    uart0_pid, uart0_cid = primecell_ids(q, UART0)
    check("C.uart0_pid_unchanged", uart0_pid, 0x00141011)
    check("C.uart0_cid_unchanged", uart0_cid, 0xB105F00D)
    # Idle PL011 flag register: TXFE (0x80) | RXFE (0x10).
    check("C.uart0_flags_idle", q.rd(UART0 + 0x18) & 0xFF, 0x90)

    # Negative control: an unowned page still reaches the catch-all meter.
    _ = q.rd(UNMAPPED)
    return 0


log = None
try:
    q = QTest("sony-a6400", OUT / "qtest-uart3.log")
    try:
        proof(q)
    finally:
        log = q.close()
except RuntimeError as exc:
    print("FAIL qtest: %s" % exc)
    sys.exit(1)

print("qemu=" + str(QEMU))
print("qemu_sha256=" + sha256(QEMU))

id_offsets = ["0x%x" % (0x2003FE0 + 4 * i) for i in range(8)]
uart3_noise = [l for l in log.splitlines()
               if "unimplemented device" in l
               and any(("offset " + o) in l for o in id_offsets)]
neg = [l for l in log.splitlines()
       if "unimplemented device read" in l and "offset 0x2004000" in l]
print("uart3_id_unmodelled_lines=%d" % len(uart3_noise))
for line in uart3_noise:
    print("  " + line)
print("unowned_read_log_lines=%d" % len(neg))
for line in neg:
    print("  " + line)

checks.append(("N.uart3_ids_not_unmodelled", len(uart3_noise), 0,
               not uart3_noise))
checks.append(("N.unowned_read_logged", len(neg), 1, len(neg) == 1))

bad = [c for c in checks if not c[3]]
print("checks=%d failed=%d" % (len(checks), len(bad)))
sys.exit(1 if bad else 0)
