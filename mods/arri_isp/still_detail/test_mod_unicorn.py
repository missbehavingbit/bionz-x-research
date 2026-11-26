"""Offline Unicorn test of the SMOD v2 StillDetail build (no camera).

Usage: python mods/arri_isp/still_detail/test_mod_unicorn.py BUILD_DIR

Loads StillDetail.mod the way Mod Loader v2 does (relocatable code at an arbitrary base)
and verifies:
  1. ON when /tmp_bt/mods/sabin/dcv_psf_table_still.bin is absent: creates dirs, writes all
     57,848 bytes matching BUILD_DIR/dcv_psf_table_still.bin, and bind-mounts over
     /usr/sabin/dcv_psf_table_still.bin.
  2. ON when the sabin file already exists: skips rewriting and bind-mounts.
  3. OFF: calls umount2("/usr/sabin/dcv_psf_table_still.bin", MNT_DETACH=2).
"""
import struct
import sys
from pathlib import Path

import unicorn as uc
from unicorn import arm_const as A

B = Path(sys.argv[1])
mod = (B / "StillDetail.mod").read_bytes()
magic, ver, flags, name, code_size, fnv, bss, on_off, off_off, hooks = struct.unpack_from("<III32s6I", mod)
assert magic == 0x444F4D53 and ver == 2 and name.rstrip(b"\0") == b"StillDetail" and hooks == 0
code = mod[68:68 + code_size]
expected_table = (B / "dcv_psf_table_still.bin").read_bytes()
assert len(expected_table) == 57848
STOP, BASE = 0x31000000, 0x40ab3000


def run(on, sabin_present=False):
    m = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
    map_sz = ((len(code) + bss + 0x2000) + 0xfff) & ~0xfff
    m.mem_map(BASE, map_sz)
    m.mem_write(BASE, code)
    m.mem_map(0x20000000, 0x10000)
    m.mem_map(STOP, 0x1000)
    log, mounts, umounts, files, open_fds = [], [], [], {}, {}
    next_fd = [10]

    def intr(mu, n, _):
        nr = mu.reg_read(A.UC_ARM_REG_R7)
        a = [mu.reg_read(r) for r in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2,
                                      A.UC_ARM_REG_R3, A.UC_ARM_REG_R4, A.UC_ARM_REG_R5)]
        ret = 0
        if nr == 5:
            path = bytes(mu.mem_read(a[0], 80)).split(b"\0")[0].decode(errors="replace")
            if path.startswith("/tmp_bt/mods/sabin/"):
                if (a[1] & 3) == 0:
                    ret = 5 if sabin_present else -2
                else:
                    fd = next_fd[0]
                    next_fd[0] += 1
                    open_fds[fd] = path
                    files[path] = bytearray()
                    ret = fd
            else:
                assert path == "/tmp_bt/mods/still_detail.log", f"unexpected open {path}"
                ret = 4
        elif nr == 4:
            data = bytes(mu.mem_read(a[1], a[2]))
            if a[0] == 4:
                log.append(data.decode())
            elif a[0] in open_fds:
                files[open_fds[a[0]]].extend(data)
            ret = len(data)
        elif nr == 39:
            ret = 0
        elif nr == 21:
            assert a[3] == 4096
            mounts.append((bytes(mu.mem_read(a[0], 80)).split(b"\0")[0].decode(),
                           bytes(mu.mem_read(a[1], 80)).split(b"\0")[0].decode()))
        elif nr == 52:
            assert a[1] == 2
            umounts.append(bytes(mu.mem_read(a[0], 80)).split(b"\0")[0].decode())
        elif nr != 6:
            raise AssertionError(f"unexpected syscall {nr}")
        mu.reg_write(A.UC_ARM_REG_R0, ret & 0xffffffff)

    m.hook_add(uc.UC_HOOK_INTR, intr)
    m.reg_write(A.UC_ARM_REG_SP, 0x2000fff0)
    m.reg_write(A.UC_ARM_REG_R0, 1 if on else 0)
    m.reg_write(A.UC_ARM_REG_LR, STOP)
    entry = BASE + (on_off if on else off_off) + 1
    m.emu_start(entry, STOP, count=200000)
    return m.reg_read(A.UC_ARM_REG_R0), log, mounts, umounts, files


rc, log, mounts, umounts, files = run(True, sabin_present=False)
assert rc == 0 and mounts == [("/tmp_bt/mods/sabin/dcv_psf_table_still.bin", "/usr/sabin/dcv_psf_table_still.bin")]
assert bytes(files["/tmp_bt/mods/sabin/dcv_psf_table_still.bin"]) == expected_table
assert "still_detail ON prep=0 mount=0" in "".join(log)

rc, log, mounts, umounts, files = run(True, sabin_present=True)
assert rc == 0 and mounts == [("/tmp_bt/mods/sabin/dcv_psf_table_still.bin", "/usr/sabin/dcv_psf_table_still.bin")]
assert not files

rc, log, mounts, umounts, files = run(False)
assert rc == 0 and umounts == ["/usr/sabin/dcv_psf_table_still.bin"]
assert "still_detail OFF umount=0" in "".join(log)
print("StillDetail Unicorn verification: 3/3 PASS")
