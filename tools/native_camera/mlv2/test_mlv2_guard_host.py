"""Host end-to-end test of mlv2_guard against a stand-in process (run inside WSL / Linux x86_64).

Usage (from the repository root, inside WSL):
    python3 tools/native_camera/mlv2/test_mlv2_guard_host.py BUILD_DIR NEW_OUTPUT_DIR

BUILD_DIR holds mlv2.req (from build_wsl.sh). The guard is rebuilt for the host with
-DMLV2_HOST_TEST, so the freeze, PC check, POKETEXT writes, readback and state detection run for
real over ptrace against host_test_target. No camera or USB access.
"""
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
build, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir()
req = (build / "mlv2.req").read_bytes()
h = struct.unpack_from("<9I", req)
shim = req[36 + 12 * h[8]:36 + 12 * h[8] + h[3]]
assert len(shim) == h[3]
relocs = ()
if h[1] == 2:
    n = struct.unpack_from("<I", req, 36 + 12 * h[8] + h[3])[0]
    relocs = struct.unpack_from(f"<{n}I", req, 36 + 12 * h[8] + h[3] + 4)
    assert len(req) == 36 + 12 * h[8] + h[3] + 4 + 4 * n


def moved_shim(vb, tb):
    out = bytearray(shim)
    for r in relocs:
        o = r & 0x3FFFFFFF
        struct.pack_into("<I", out, o, (struct.unpack_from("<I", out, o)[0] + (vb if r >> 30 else tb)) & 0xFFFFFFFF)
    return bytes(out)

for src, exe, extra in (("mlv2_guard.c", "guard", ["-DMLV2_HOST_TEST"]),
                        ("host_test_target.c", "target", ["-pthread"])):
    subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", *extra, str(HERE / src), "-o", str(out / exe)],
                   check=True)

HEAD = bytes.fromhex("dff800f0")


def branches(sr, rf, oi):
    return (HEAD + struct.pack("<I", sr) + bytes.fromhex("00bf00bf00bf00bf"),
            HEAD + struct.pack("<I", rf) + bytes.fromhex("00bf00bf00bf4ff0"),
            HEAD + struct.pack("<I", oi) + bytes.fromhex("2de9f043"))


V2 = branches(*h[4:7])
RECT = bytes.fromhex("150200005b004200")
STOCK = (bytes.fromhex("044600af51b100f5c23303f52b731960"), bytes.fromhex("2de9f04f9bb006af044607f120004ff0"),
         bytes.fromhex("00f5c23303f52c732de9f043"))


class Target:
    def __init__(self, mode, vb=0, tb=0):
        self.dir = tempfile.mkdtemp()
        extra = [hex(vb), hex(tb)] if vb or tb else []
        self.p = subprocess.Popen([str(out / "target"), self.dir, mode, *extra], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True)
        self.pid = int(self.p.stdout.readline().split()[1])

    def dump(self):
        self.p.stdin.write("dump\n"); self.p.stdin.flush()
        rows = []
        while (line := self.p.stdout.readline().strip()) != "END":
            rows.append(bytes.fromhex(line.split()[1]))
        return rows

    def close(self):
        self.p.stdin.write("quit\n"); self.p.stdin.flush(); self.p.wait(5)


def guard(t, op):
    r = subprocess.run([str(out / "guard"), op, str(build / "mlv2.req")], env={"MLV2_PID": str(t.pid)},
                       capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout


log, failures = [], []


def case(name, mode, op, expect_rc, expect_text, expect_after):
    t = Target(mode)
    before = t.dump()
    rc, text = guard(t, op)
    after = t.dump()
    alive = t.p.poll() is None
    t.close()
    ok = rc == expect_rc and expect_text in text and alive and expect_after(before, after)
    log.append(f"[{'PASS' if ok else 'FAIL'}] {name}: rc={rc}\n" + "".join("    " + l + "\n" for l in text.splitlines()))
    if not ok:
        failures.append(name)


def installed(_, a):
    cave = shim + bytes(len(a[0]) - len(shim))  # shim, then the rest of the cave stays zero
    return a[0] == cave and tuple(a[1:4]) == V2 and a[4] == a[5] == RECT


def unchanged(b, a):
    return b == a


case("check on stock: reports, writes nothing", "stock", "check", 0, "STATE hooks=stock v1_build=-1 shim=zero", unchanged)
case("install on stock", "stock", "install", 0, "INSTALLED", installed)
case("upgrade from sd-standalone v1", "v1", "upgrade", 0, "UPGRADED", installed)
case("upgrade from lifecycle v1", "v1life", "upgrade", 0, "STATE hooks=v1 v1_build=1", installed)
case("install refuses v1 hooks", "v1", "install", 1, "MLV2_RESULT=-13", unchanged)
case("upgrade refuses stock hooks", "stock", "upgrade", 1, "MLV2_RESULT=-13", unchanged)
case("refuses unknown hooks", "unknown", "upgrade", 1, "STATE hooks=unknown", unchanged)
case("refuses non-zero cave", "cavedirty", "upgrade", 1, "MLV2_RESULT=-12", unchanged)

# Moved layout (QEMU-like): libraries at other bases; the guard relocates every address and the shim.
if relocs:
    VB, TB = 0x00800000, 0x00100000
    t = Target("stock", VB, TB)
    rc, text = guard(t, "install")
    a = t.dump()
    t.close()
    v2m = branches(*(e + TB for e in h[4:7]))
    ms = moved_shim(VB, TB)
    ok = (rc == 0 and f"view_bias={VB:08x} testcmd_bias={TB:08x}" in text and "INSTALLED" in text and
          a[0] == ms + bytes(len(a[0]) - len(ms)) and tuple(a[1:4]) == v2m and a[4] == a[5] == RECT)
    log.append(f"[{'PASS' if ok else 'FAIL'}] install on a moved layout relocates hooks and shim: rc={rc}\n" +
               "".join("    " + l + "\n" for l in text.splitlines()))
    if not ok:
        failures.append("moved layout")

# Idempotence: a second install on an already-installed target is a no-op.
t = Target("stock")
guard(t, "install")
mid = t.dump()
rc, text = guard(t, "install")
end = t.dump()
t.close()
ok = rc == 0 and "ALREADY_INSTALLED" in text and mid == end
log.append(f"[{'PASS' if ok else 'FAIL'}] second install is a no-op: rc={rc}\n")
if not ok:
    failures.append("idempotence")

report = "".join(log) + ("ALL GUARD CASES PASSED\n" if not failures else f"FAILED: {failures}\n")
(out / "guard-host-test.txt").write_text(report)
print(report)
sys.exit(1 if failures else 0)
