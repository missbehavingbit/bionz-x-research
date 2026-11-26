"""Offline Unicorn test of Mod Loader v2 (real shim + core ARM code, emulated syscalls and menu).

Usage: python tools/native_camera/mlv2/test_mlv2_unicorn.py BUILD_DIR [ARRIVEDERCI_V2_MOD [MTF_V2_BUILD_DIR]]
Scenarios: fresh install, rescan, SMOD v2 load + hook fixup + lifecycle, SMOD v1 compatibility,
core upgrade in place, bad core rejection, hook mismatch, v1 takeover with an enabled v1 mod,
SD disable, v1 -> v2 MenuTouchFull replacement, and (with a reload-capable core) the reload
request: replace, refusals before OFF, failed OFF, new-file scan, MenuTouchFull fresh state,
and (with a layout-aware build) a moved process layout: shim relocated as the guard does it, view
and libtestcmd bases moved, relocatable MenuTouchFull moved by /proc/self/maps.
"""
import struct
import sys
from pathlib import Path

import unicorn as uc
from unicorn import arm_const as A

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
from tools.research.pack_mod import extract_elf_text, pack_smod, fnv1a_32  # noqa: E402

B = Path(sys.argv[1])
shim_addr, shim_code, shim_syms = extract_elf_text((B / "shim.elf").read_bytes())
IS_A6400A = shim_addr == 0x05D76800
SHIM = 0x05D76800 if IS_A6400A else 0x05662800
SET_TEXT = 0x130958DC if IS_A6400A else 0x157356D0
SET_MODE = 0x1309BC9C if IS_A6400A else 0x1573BA68
MENU_REFRESH = 0x134F6360 if IS_A6400A else 0x15B92110
VIEW_PAGE_BASE = 0x13090000 if IS_A6400A else 0x15700000
ANCHOR = 0x13ABFC40 if IS_A6400A else 0x1615EC40
MENU, NODES, STUBS, TARGETS, V1CODE = 0x30000000, 0x31000000, 0x32000000, 0x33000000, 0x34000000
MMAP0, STACK, STOP = 0x40000000, 0x50000000, 0x5F000000
ORIG12 = bytes.fromhex("2de9f04788b04ff014080122")

core_bin = (B / "core.bin").read_bytes()
testmod = (B / "TestMod.mod").read_bytes()


def v1_mod(name, hook_target, lifecycle=True):
    code = b"\x70\x47\x00\xbf"                      # bx lr; nop
    hook = (hook_target, ORIG12, b"\x00\xbf" * 6)
    return pack_smod(name, V1CODE, code, [hook], flags=2 if lifecycle else 0)


class Camera:
    shim_at, shim_image, view_bias = SHIM, None, 0

    def __init__(self, files, v1_state=None):
        self.files = dict(files)
        self.fds, self.next_fd, self.mm_next = {}, 3, MMAP0
        self.texts, self.log, self.calls = [], [], []
        m = self.m = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
        m.mem_map(self.shim_at & ~0xfff, 0x2000, uc.UC_PROT_READ | uc.UC_PROT_EXEC)   # RX like the real cave
        m.mem_write(self.shim_at, self.shim_image or shim_code)
        vb = self.view_bias
        for base, size in ((VIEW_PAGE_BASE + vb, 0x100000), ((MENU_REFRESH + vb) & ~0xfff, 0x2000),
                           ((ANCHOR + vb) & ~0xfff, 0x1000), (MENU, 0x20000), (NODES, 0x10000),
                           (STUBS, 0x1000), (TARGETS, 0x1000), (V1CODE, 0x1000), (STACK - 0x10000, 0x10000), (STOP, 0x1000)):
            m.mem_map(base, size)
        after_rf = (shim_syms["ml_after_refresh"] - SHIM + self.shim_at) | 1
        m.mem_write(MENU_REFRESH + vb, bytes.fromhex("10b50446014a904710bd00bf") + struct.pack("<I", after_rf))
        m.mem_write(TARGETS + 0x100, ORIG12)
        m.mem_write(TARGETS + 0x200, ORIG12)
        # Firmware root node: 6 tabs; vtable entries are stubs returning 0; tab5 props answer 1.
        vt = NODES + 0x1000
        m.mem_write(vt, struct.pack("<72I", *[(STUBS + 4 * i) | 1 for i in range(72)]))
        kids = NODES + 0x2000
        tabs = [NODES + 0x3000 + 0x40 * i for i in range(6)]
        for t in tabs:
            m.mem_write(t, struct.pack("<10I", vt, 0, 0, 0, 0, 2, 1, 0, 0, 0))
        m.mem_write(kids, struct.pack("<6I", *tabs))
        self.root = NODES
        m.mem_write(NODES, struct.pack("<10I", vt, kids, 6, 0, 0, 1, 1, 0, 0, 0))
        self.tabs = tabs
        m.mem_write(MENU + 0x1F0, struct.pack("<I", 7))       # Mods tab current
        m.mem_write(MENU + 0x1F4, struct.pack("<I", 1))
        if v1_state:
            st = self.mmap(8192)
            m.mem_write(st, v1_state)
            m.mem_write(ANCHOR + self.view_bias, struct.pack("<2I", 0x444F4D53, st))
        m.hook_add(uc.UC_HOOK_INTR, self.syscall)
        m.hook_add(uc.UC_HOOK_CODE, self.code)

    def mmap(self, n):
        a = self.mm_next
        n = (n + 0xFFF) & ~0xFFF
        self.m.mem_map(a, n)
        self.mm_next += n + 0x1000
        return a

    def ret(self, val=0):
        self.m.reg_write(A.UC_ARM_REG_R0, val & 0xFFFFFFFF)
        self.m.reg_write(A.UC_ARM_REG_PC, self.m.reg_read(A.UC_ARM_REG_LR))

    def code(self, m, pc, size, _):
        r = [m.reg_read(x) for x in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2)]
        if pc == STOP:
            m.emu_stop()
        elif pc == SET_TEXT + self.view_bias:
            self.texts.append((r[0] - MENU, bytes(m.mem_read(r[1], 40)).split(b"\0")[0].decode()))
            self.ret()
        elif pc == SET_MODE + self.view_bias:
            self.ret()
        elif pc == MENU_REFRESH + self.view_bias:
            self.texts.clear()
        elif STUBS <= pc < STUBS + 0x1000:
            i = (pc - STUBS) // 4
            if i in (23, 32):                               # tab5 int/resid props
                m.mem_write(r[2], struct.pack("<I", 7))
                self.ret(1)
            else:
                self.ret(0)

    def syscall(self, m, intno, _):
        n = m.reg_read(A.UC_ARM_REG_R7)
        a = [m.reg_read(x) for x in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3,
                                     A.UC_ARM_REG_R4, A.UC_ARM_REG_R5)]
        self.calls.append(n)
        res = 0
        if n == 5:
            path = bytes(m.mem_read(a[0], 80)).split(b"\0")[0].decode()
            if path.endswith("mod_loader.log"):
                res = 99
            elif path == "/tmp_bt/mlv2/sd-stopped" and a[1] == 0x441:
                self.files[path] = b""
                res = self.next_fd; self.next_fd += 1; self.fds[res] = ["file", b"", 0]
            elif a[1] in (0x241, 0x441):                     # write-only create (truncate / append)
                if a[1] == 0x241 or path not in self.files:
                    self.files[path] = b""
                res = self.next_fd; self.next_fd += 1; self.fds[res] = ["wfile", path, 0]
            elif a[1] & 0o40000:
                entries = {}
                prefix = path + "/"
                for p in self.files:
                    if p.startswith(prefix):
                        rem = p[len(prefix):]
                        if "/" in rem:
                            entries[rem.split("/", 1)[0]] = 4
                        else:
                            entries.setdefault(rem, 8)
                if entries or path == "/tmp_bt/mods":
                    res = self.next_fd; self.next_fd += 1; self.fds[res] = ["dir", sorted(entries.items()), False]
                else:
                    res = -2
            elif path in self.files:
                res = self.next_fd; self.next_fd += 1; self.fds[res] = ["file", self.files[path], 0]
            else:
                res = -2
        elif n == 3:
            f = self.fds[a[0]]
            data = f[1][f[2]:f[2] + a[2]]
            m.mem_write(a[1], data)
            f[2] += len(data)
            res = len(data)
        elif n == 19:
            self.fds[a[0]][2] = a[1]; res = a[1]
        elif n == 6:
            self.fds.pop(a[0], None)
        elif n == 4:
            data = bytes(m.mem_read(a[1], a[2]))
            if a[0] == 99:
                self.log.append(data.decode().strip())
            elif self.fds.get(a[0], [None])[0] == "wfile":
                self.files[self.fds[a[0]][1]] += data
            res = a[2]
        elif n == 217:
            f = self.fds[a[0]]
            if f[2]:
                res = 0
            else:
                buf = b""
                for name, dtype in f[1]:
                    nm = name.encode() + b"\0"
                    rl = (19 + len(nm) + 7) & ~7
                    buf += struct.pack("<QqHB", 1, 0, rl, dtype) + nm.ljust(rl - 19, b"\0")
                m.mem_write(a[1], buf); f[2] = True; res = len(buf)
        elif n == 10:                                       # unlink
            path = bytes(m.mem_read(a[0], 80)).split(b"\0")[0].decode()
            res = 0 if self.files.pop(path, None) is not None else -2
        elif n == 192:
            res = self.mmap(a[1])
        elif n == 125:
            m.mem_protect(a[0], a[1], uc.UC_PROT_ALL); res = 0
        elif n == 242:
            m.mem_write(a[2], struct.pack("<I", 1))
        elif n in (241, 0x0F0002):
            res = 0
        else:
            raise AssertionError(f"unexpected syscall {n}")
        m.reg_write(A.UC_ARM_REG_R0, res & 0xFFFFFFFF)

    def call(self, sym, *args):
        m = self.m
        for reg, v in zip((A.UC_ARM_REG_R0, A.UC_ARM_REG_R1), args):
            m.reg_write(reg, v)
        m.reg_write(A.UC_ARM_REG_SP, STACK - 0x100)
        m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
        m.emu_start((shim_syms[sym] - SHIM + self.shim_at) | 1, STOP, count=5_000_000)
        return m.reg_read(A.UC_ARM_REG_R0)

    def u32(self, a):
        return struct.unpack("<I", self.m.mem_read(a, 4))[0]

    def open_menu(self):
        self.texts.clear()
        self.call("ml_before_set_root", MENU, self.root)
        self.call("ml_after_refresh", MENU)
        return [t for off, t in self.texts if off != 0x5470]

    def items(self, page_idx=0):
        kids = self.u32(self.root + 4)
        tab = self.u32(kids + 24)
        pages = self.u32(tab + 4)
        page = self.u32(pages + 4 * page_idx)
        items = self.u32(page + 4)
        return [self.u32(items + 4 * i) for i in range(self.u32(page + 8))]

    def press(self, index, page_idx=0):
        self.call("ml_before_enter", MENU, self.items(page_idx)[index])


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok:", msg)


def with_fnv_variant(core, pad=4):
    """Same core code plus trailing zero bytes: a different, valid core image."""
    code = core[20:] + b"\0" * pad
    return struct.pack("<5I", 0x52434C4D, 2, len(code), fnv1a_32(code), 1) + code


print("Scenario 1: fresh install, no mods, then rescan picks up a new file")
cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin})
texts = cam.open_menu()
check(cam.u32(cam.root + 8) == 7, "root now has 7 children (Mods tab added)")
check(texts == ["No mods found", ""], f"empty list shown ({texts})")
check(cam.u32(ANCHOR) == 0x444F4D53 and cam.u32(ANCHOR + 4) != 0, "fresh install created the tab-artwork anchor block")
cam.files["/tmp_bt/mods/TestMod.mod"] = testmod
texts = cam.open_menu()
check(texts == ["Test Mod", "OFF"], f"rescan shows the new mod without restart ({texts})")

print("Scenario 2: SMOD v2 ON/OFF with relocation, hook fixup and lifecycle")
cam.press(0)
_, tcode, tsyms = extract_elf_text((B / "test_mod.elf").read_bytes(), expected_code_addr=0)
hook = bytes(cam.m.mem_read(TARGETS + 0x100, 12))
entry = struct.unpack("<I", hook[8:12])[0]
base = entry - tsyms["t_hook"]
check(hook[:8] == bytes.fromhex("dff804f000bf00bf") and base >= MMAP0 and base % 0x1000 == 0,
      f"hook jumps to t_hook at load base {base:#x} + {tsyms['t_hook']:#x}")
check(cam.u32(base + tsyms["t_counter"]) == 1, "on() ran once (counter = 1)")
texts = cam.open_menu()
check(texts == ["Test Mod", "ON"], f"state survives a rescan ({texts})")
cam.press(0)
check(bytes(cam.m.mem_read(TARGETS + 0x100, 12)) == ORIG12, "OFF restored the original hook bytes")
check(cam.u32(base + tsyms["t_counter"]) == 0x101, "off() ran (counter = 0x101)")
cam.press(0)
check(cam.u32(base + tsyms["t_counter"]) == 1 and struct.unpack("<I", bytes(cam.m.mem_read(TARGETS + 0x108, 4)))[0] == entry,
      "second ON reloads into the same mapping (counter reset by reload, on() ran)")

print("Scenario 3: core upgrade in place keeps the enabled mod")
cam.files["/tmp_bt/mlv2/core.bin"] = with_fnv_variant(core_bin)
texts = cam.open_menu()
check(sum(l.startswith("core_load") for l in cam.log) == 2, "second core loaded")
check(texts == ["Test Mod", "ON"], f"enabled mod kept across the upgrade ({texts})")
cam.press(0)
check(bytes(cam.m.mem_read(TARGETS + 0x100, 12)) == ORIG12, "new core can switch the mod OFF")

print("Scenario 4: bad core is rejected, previous core stays active")
bad = bytearray(with_fnv_variant(core_bin, pad=8)); bad[-1] ^= 0xFF   # new header hash, corrupt code
cam.files["/tmp_bt/mlv2/core.bin"] = bytes(bad)
texts = cam.open_menu()
check(any(l.startswith("core_bad") for l in cam.log) and texts == ["Test Mod", "OFF"], "corrupt core ignored, menu still works")

print("Scenario 5: SMOD v1 compatibility, hook mismatch")
cam.files["/tmp_bt/mods/OldStyle.mod"] = v1_mod("Old Style", TARGETS + 0x200)
texts = cam.open_menu()
check(texts == ["Old Style", "OFF", "Test Mod", "OFF"], f"v1 and v2 listed, sorted ({texts})")
cam.press(0)
check(bytes(cam.m.mem_read(TARGETS + 0x200, 12)) == b"\x00\xbf" * 6, "v1 hook applied at its fixed target")
cam.press(0)
check(bytes(cam.m.mem_read(TARGETS + 0x200, 12)) == ORIG12, "v1 hook reverted")
cam.m.mem_write(TARGETS + 0x100, b"\xff" * 12)
cam.press(1)
check(bytes(cam.m.mem_read(TARGETS + 0x100, 12)) == b"\xff" * 12 and cam.open_menu()[3] == "ERR",
      "foreign bytes at the hook target: no write, shown as ERR")

print("Scenario 6: takeover of a running v1 loader with an enabled v1 mod")
v1 = bytearray(8192)
struct.pack_into("<3I", v1, 0, 0x444F4D53, 1, 1)
mod_off = 0x418
name = b"Legacy On".ljust(32, b"\0")
path = b"/tmp_bt/mods/Legacy.mod".ljust(64, b"\0")
hook = struct.pack("<II16s16s", TARGETS + 0x200, 12, ORIG12, b"\x00\xbf" * 6)
v1[mod_off:mod_off + 440] = name + path + bytes([1, 1, 0, 2]) + struct.pack("<5I", V1CODE, 4, 0, 0, fnv1a_32(bytes.fromhex("704700bf"))) + hook + bytes(280)  # as a real v1 loader stores it
cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/Legacy.mod": v1_mod("Legacy On", TARGETS + 0x200)},
             v1_state=bytes(v1))
cam.m.mem_write(TARGETS + 0x200, b"\x00\xbf" * 6)        # v1 had applied the hook
cam.m.mem_write(V1CODE, bytes.fromhex("704700bf"))          # and its code is resident
texts = cam.open_menu()
check(texts == ["Legacy On", "ON"], f"enabled v1 mod imported as ON ({texts})")
cam.press(0)
check(bytes(cam.m.mem_read(TARGETS + 0x200, 12)) == ORIG12, "v2 switches the imported v1 mod OFF (hook reverted)")

if len(sys.argv) > 2:
    print("Scenario 7: Arrivederci v2 is accepted and listed")
    cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/Arrivederci.mod": Path(sys.argv[2]).read_bytes()})
    check(cam.open_menu() == ["Arrivederci", "OFF"], "Arrivederci v2 parsed and listed")

print("Scenario 8: SD disable restores the mod hook and stock menu root")
cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mlv2/sd-managed": b"",
              "/tmp_bt/mods/TestMod.mod": testmod, "/mnt/sd/mods/Legacy.mod": v1_mod("Unstaged", TARGETS + 0x200)})
stock_children = cam.u32(cam.root + 4)
check(cam.open_menu() == ["Test Mod", "OFF"], "managed mode lists only RAM-staged mods")
item = cam.items()[0]
cam.press(0)
cam.files["/tmp_bt/mlv2/sd-disabled"] = b""
check(cam.call("ml_before_enter", MENU, item) == 0, "stale Mods entry is consumed without reactivation")
check(bytes(cam.m.mem_read(TARGETS + 0x100, 12)) == ORIG12, "SD disable restores the original hook")
cam.open_menu()
check(cam.u32(cam.root + 8) == 6 and cam.u32(cam.root + 4) == stock_children,
      "the next root setup restores the original six-tab tree")
check("/tmp_bt/mlv2/sd-stopped" in cam.files, "OFF and root restoration publish the stop receipt")
del cam.files["/tmp_bt/mlv2/sd-stopped"], cam.files["/tmp_bt/mlv2/sd-disabled"]
check(cam.open_menu() == ["Test Mod", "OFF"], "revalidated card restores Mods with the mod OFF")
cam.press(0)
check(bytes(cam.m.mem_read(TARGETS + 0x100, 12)) != ORIG12, "the mod can be enabled again in the same session")

print("Scenario 9: SD disable preserves foreign bytes at a mod hook")
cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/TestMod.mod": testmod})
cam.open_menu()
cam.press(0)
foreign = b"\xaa" * 12
cam.m.mem_write(TARGETS + 0x100, foreign)
cam.files["/tmp_bt/mlv2/sd-disabled"] = b""
cam.open_menu()
check(bytes(cam.m.mem_read(TARGETS + 0x100, 12)) == foreign, "foreign hook bytes are preserved")
check(any(line.startswith("mod_off_failed") for line in cam.log), "incomplete rollback is reported")
check("/tmp_bt/mlv2/sd-stopped" not in cam.files, "failed rollback blocks reactivation")


RELOAD_REQ, RELOAD_RES = "/tmp_bt/mlv2/reload", "/tmp_bt/mlv2/reload.result"


def mod_variant(data, *, counter=None, target=None, corrupt=False, syms=None):
    """TestMod copy: new initial t_counter (valid new build), other hook target, or corrupt code."""
    b = bytearray(data)
    hooks = struct.unpack_from("<I", b, 64)[0]
    code_off, code_size = 68 + 40 * hooks, struct.unpack_from("<I", b, 44)[0]
    if counter is not None:
        struct.pack_into("<I", b, code_off + syms["t_counter"], counter)
    if target is not None:
        struct.pack_into("<I", b, 68, target)
    if corrupt:
        b[code_off + code_size - 1] ^= 0xFF
    else:
        struct.pack_into("<I", b, 48, fnv1a_32(bytes(b[code_off:code_off + code_size])))
    return bytes(b)


def reload(cam, name, via="root"):
    cam.files[RELOAD_REQ] = name.encode() + b"\n"
    if via == "root":
        texts = cam.open_menu()
    else:
        cam.texts.clear()
        cam.call("ml_after_refresh", MENU)
        texts = [t for off, t in cam.texts if off != 0x5470]
    return cam.files.get(RELOAD_RES, b"").decode().strip(), texts


if b"/tmp_bt/mlv2/reload" in core_bin:
    print("Scenario 11: reload request replaces an enabled SMOD v2 mod in one step")
    _, _, tsyms = extract_elf_text((B / "test_mod.elf").read_bytes(), expected_code_addr=0)
    cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/TestMod.mod": testmod})
    cam.open_menu()
    cam.press(0)

    def entry_base(target=TARGETS + 0x100):
        return struct.unpack("<I", bytes(cam.m.mem_read(target + 8, 4)))[0] - tsyms["t_hook"]

    old = entry_base()
    cam.files["/tmp_bt/mods/TestMod.mod"] = mod_variant(testmod, counter=0x10, syms=tsyms)
    res, texts = reload(cam, "TestMod.mod")
    new = entry_base()
    check(res.startswith("reload_ok") and int(res.split()[1], 16) == new, f"result file reports the new base ({res})")
    check(RELOAD_REQ not in cam.files, "request consumed")
    check(new != old and new % 0x1000 == 0, f"new copy in a fresh mapping ({old:#x} -> {new:#x}); old one left mapped")
    check(cam.u32(old + tsyms["t_counter"]) == 0x101, "old copy: off() ran before its hook was reverted")
    check(cam.u32(new + tsyms["t_counter"]) == 0x11, "new copy: new code loaded, on() ran once")
    check(texts == ["Test Mod", "ON"], f"menu shows ON, not ON* ({texts})")

    good = cam.files["/tmp_bt/mods/TestMod.mod"]
    hook_now = bytes(cam.m.mem_read(TARGETS + 0x100, 12))
    cam.files["/tmp_bt/mods/TestMod.mod"] = mod_variant(testmod, counter=0x20, corrupt=True, syms=tsyms)
    res, _ = reload(cam, "TestMod.mod")
    check(res.startswith("reload_err_load") and bytes(cam.m.mem_read(TARGETS + 0x100, 12)) == hook_now and
          cam.u32(new + tsyms["t_counter"]) == 0x11, f"corrupt code: refused before OFF, old copy untouched ({res})")
    cam.m.mem_write(TARGETS + 0x200, b"\xaa" * 12)
    cam.files["/tmp_bt/mods/TestMod.mod"] = mod_variant(good, target=TARGETS + 0x200, syms=tsyms)
    res, _ = reload(cam, "TestMod.mod")
    check(res.startswith("reload_err_target") and bytes(cam.m.mem_read(TARGETS + 0x100, 12)) == hook_now,
          f"new target holds foreign bytes: refused before OFF ({res})")
    cam.m.mem_write(TARGETS + 0x200, ORIG12)
    res, texts = reload(cam, "TestMod.mod", via="refresh")
    check(res.startswith("reload_ok") and bytes(cam.m.mem_read(TARGETS + 0x100, 12)) == ORIG12 and
          entry_base(TARGETS + 0x200) == int(res.split()[1], 16) and texts == ["Test Mod", "ON"],
          f"reload from the refresh hook moves the hook to a new target and restores the old one ({res})")
    cam.files[RELOAD_REQ] = b"../TestMod.mod\n"
    cam.open_menu()
    check(cam.files[RELOAD_RES].startswith(b"reload_err_request") and RELOAD_REQ not in cam.files,
          "a request with a directory is rejected and consumed")
    cam.m.mem_write(TARGETS + 0x200, b"\xbb" * 12)
    res, texts = reload(cam, "TestMod.mod")
    check(res.startswith("reload_err_off") and bytes(cam.m.mem_read(TARGETS + 0x200, 12)) == b"\xbb" * 12 and
          texts[1] == "ERR", f"old hook overwritten by someone else: OFF fails, replacement stops, ERR ({res})")

    cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/TestMod.mod": testmod})
    cam.open_menu()
    cam.files["/tmp_bt/mods/Second.mod"] = mod_variant(testmod, target=TARGETS + 0x200, syms=tsyms)
    res, texts = reload(cam, "Second.mod")
    check(res == "reload_scan 00000000" and texts == ["Test Mod", "OFF", "Test Mod", "OFF"] and
          bytes(cam.m.mem_read(TARGETS + 0x200, 12)) == ORIG12, f"new file: listed by the same scan, left OFF ({res})")
    res, _ = reload(cam, "TestMod.mod")
    check(res == "reload_scan 00000001", f"disabled mod: nothing to replace ({res})")

if len(sys.argv) > 3:
    mtf_dir = Path(sys.argv[3])
    mtf_v2 = (mtf_dir / "MenuTouchFull.mod").read_bytes()
    msyms = {p[2]: int(p[0], 16) for p in (ln.split() for ln in (mtf_dir / "mtf.sym").read_text().splitlines())
             if len(p) == 3}
    code_size = struct.unpack_from("<I", mtf_v2, 44)[0]
    if IS_A6400A:
        MTF_REL, MTF_PRS = 0x0659AE98, 0x0659AFA4
    else:
        MTF_REL, MTF_PRS = 0x06309D48, 0x06309E54
    MTF_HOOKS = ((MTF_REL, bytes.fromhex("2de9f04788b04ff014080122")),
                 (MTF_PRS, bytes.fromhex("2de9f04788b04ff014090122")))
    if not IS_A6400A:
        print("Scenario 10: imported v1 MenuTouchFull is replaced by the SMOD v2 build")
        mtf_v1 = (root / "mods/menu_touch_full/MenuTouchFull.mod").read_bytes()
        v1_hooks = [mtf_v1[68 + 40 * i:108 + 40 * i] for i in range(2)]
        v1 = bytearray(8192)
        struct.pack_into("<3I", v1, 0, 0x444F4D53, 1, 1)
        hdr = struct.unpack_from("<6I", mtf_v1, 44)           # code_addr, code_size, state_addr, state_size, hooks, fnv
        entry_v1 = (b"Menu Touch Full".ljust(32, b"\0") + b"/tmp_bt/mods/MenuTouchFull.mod".ljust(64, b"\0") +
                    bytes([1, 2, 0, 1]) + struct.pack("<5I", hdr[0], hdr[1], hdr[2], hdr[3], hdr[5]) +
                    b"".join(v1_hooks) + bytes(440 - 120 - 80))
        v1[0x418:0x418 + 440] = entry_v1                       # enabled, 2 hooks, SMOD flags 1 (state init)
        cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/MenuTouchFull.mod": mtf_v1,
                      "/proc/self/maps": (
                          b"05d18000-0700e000 r-xp 00000000 fc:0f 22409      /usr/lib/libObj.so\n"
                          b"155e6000-15e92000 r-xp 00000000 fc:0f 22532      /usr/lib/viewUnified2.so\n"
                          b"1615f000-16bfe000 r-xp 00000000 fc:0f 22443      /usr/lib/CautionConfig.so\n"
                      )}, v1_state=bytes(v1))
        cam.m.mem_map(MTF_REL & ~0xfff, 0x1000)
        cam.m.mem_map(0x064B5000, 0x1000)
        for (target, _), rec in zip(MTF_HOOKS, v1_hooks):
            cam.m.mem_write(target, rec[24:24 + 12])           # the v1 loader had applied both hooks
        check(cam.open_menu() == ["Menu Touch Full", "ON"], "enabled v1 MenuTouchFull imported as ON")
        cam.files["/tmp_bt/mods/MenuTouchFull.mod"] = mtf_v2
        check(cam.open_menu() == ["Menu Touch Full", "ON*"], "replacing the file marks the running v1 copy as changed (ON*)")
        cam.press(0)
        check(all(bytes(cam.m.mem_read(t, 12)) == o for t, o in MTF_HOOKS), "OFF reverts both v1 hooks")
        check(cam.open_menu() == ["Menu Touch Full", "OFF"], "next rescan lists the v2 file, OFF")
        cam.press(0)
        word = struct.unpack("<I", bytes(cam.m.mem_read(MTF_REL + 8, 4)))[0]
        base = word - (msyms["mt_full_release"] | 1)
        check(base >= MMAP0 and base % 0x1000 == 0 and
              bytes(cam.m.mem_read(MTF_REL, 8)) == bytes.fromhex("dff804f000bf00bf"),
              f"v2 ON: release hook points into the core-allocated mapping at {base:#x}")
        check(struct.unpack("<I", bytes(cam.m.mem_read(MTF_PRS + 8, 4)))[0] == base + (msyms["mt_full_press"] | 1),
              "v2 ON: press hook fixed up to the same base")
        st = struct.unpack("<6I", bytes(cam.m.mem_read(base + code_size, 24)))
        check(st[0] == 0x4D544632 and st[1] == 2 and st[2] == 2 and st[5] == 16, "on() published the MTF2 state in the bss")
        cam.press(0)
        check(all(bytes(cam.m.mem_read(t, 12)) == o for t, o in MTF_HOOKS), "v2 OFF restores both prologues")
        st = struct.unpack("<3I", bytes(cam.m.mem_read(base + code_size, 12)))
        check(st[0] == 0 and st[2] == 0, "off() cleared magic and enabled before the hooks were reverted")


if len(sys.argv) > 3 and b"/tmp_bt/mlv2/reload" in core_bin:
    print("Scenario 12: MenuTouchFull reload starts the new copy with fresh gesture state")
    extra_files = {}
    if IS_A6400A:
        extra_files["/proc/self/maps"] = (
            b"05fc2000-0729e000 r-xp 00000000 fc:0f 2111       /usr/lib/libObj.so\n"
            b"12f46000-137f3000 r-xp 00000000 fc:0f 2240       /usr/lib/viewUnified2.so\n"
            b"13ac0000-1455f000 r-xp 00000000 fc:0f 2148       /usr/lib/CautionConfig.so\n"
        )
    else:
        extra_files["/proc/self/maps"] = (
            b"05d18000-0700e000 r-xp 00000000 fc:0f 22409      /usr/lib/libObj.so\n"
            b"155e6000-15e92000 r-xp 00000000 fc:0f 22532      /usr/lib/viewUnified2.so\n"
            b"1615f000-16bfe000 r-xp 00000000 fc:0f 22443      /usr/lib/CautionConfig.so\n"
        )
    cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/MenuTouchFull.mod": mtf_v2, **extra_files})
    cam.m.mem_map(MTF_REL & ~0xfff, 0x1000)
    cam.m.mem_map((0x067A862E if IS_A6400A else 0x064B54B6) & ~0xfff, 0x1000)
    for target, orig in MTF_HOOKS:
        cam.m.mem_write(target, orig)
    cam.open_menu()
    cam.press(0)

    def mtf_base():
        return struct.unpack("<I", bytes(cam.m.mem_read(MTF_REL + 8, 4)))[0] - (msyms["mt_full_release"] | 1)

    base1 = mtf_base()
    st1 = base1 + code_size
    cam.m.mem_write(st1 + 24, b"\x5a" * 32)                 # gesture data recorded by the old copy
    res, texts = reload(cam, "MenuTouchFull.mod")
    base2 = mtf_base()
    check(res.startswith("reload_ok") and base2 != base1 and texts == ["Menu Touch Full", "ON"],
          f"same file reloaded into a new mapping {base1:#x} -> {base2:#x} ({res})")
    check(struct.unpack("<I", bytes(cam.m.mem_read(MTF_PRS + 8, 4)))[0] == base2 + (msyms["mt_full_press"] | 1),
          "both hooks point at the new copy")
    st_old = struct.unpack("<3I", bytes(cam.m.mem_read(st1, 12)))
    check(st_old[0] == 0 and st_old[2] == 0 and bytes(cam.m.mem_read(st1 + 24, 32)) == b"\x5a" * 32,
          "old copy: off() cleared magic and enabled; its gesture data stays in its own bss")
    st_new = bytes(cam.m.mem_read(base2 + code_size, 632))
    check(struct.unpack_from("<6I", st_new)[:3] == (0x4D544632, 2, 2) and not any(st_new[24:]),
          "new copy: published with an empty gesture state and event log")
    cam.press(0)
    check(all(bytes(cam.m.mem_read(t, 12)) == o for t, o in MTF_HOOKS), "OFF after a reload restores both prologues")

layout_aware = (B / "mlv2.req").exists() and struct.unpack_from("<I", (B / "mlv2.req").read_bytes(), 4)[0] == 2
if len(sys.argv) > 4 and layout_aware:
    print("Scenario 13: moved process layout (QEMU-like) with the relocatable MenuTouchFull")
    req = (B / "mlv2.req").read_bytes()
    h = struct.unpack_from("<9I", req)
    off = 36 + 12 * h[8]
    shim = bytearray(req[off:off + h[3]])
    nrel = struct.unpack_from("<I", req, off + h[3])[0]
    rels = struct.unpack_from(f"<{nrel}I", req, off + h[3] + 4)
    VB, TB, OB, CB = 0x00800000, 0x00100000, -0x00200000, 0x00300000   # view, libtestcmd, libObj, CautionConfig
    for r in rels:                                                    # what the guard does
        o = r & 0x3FFFFFFF
        struct.pack_into("<I", shim, o, (struct.unpack_from("<I", shim, o)[0] + (VB if r >> 30 else TB)) & 0xFFFFFFFF)
    check(shim == bytearray(shim_code) or nrel > 0, f"request carries {nrel} shim relocations")
    reloc_mod = Path(sys.argv[4]).read_bytes()
    if IS_A6400A:
        maps = (f"{0x05d74000 + TB:08x}-{0x05d77000 + TB:08x} r-xp 00000000 fc:0f 2173       /usr/lib/libtestcmd.so\n"
                f"{0x05fc2000 + OB:08x}-{0x0729e000 + OB:08x} r-xp 00000000 fc:0f 2111       /usr/lib/libObj.so\n"
                f"{0x072a6000 + OB:08x}-{0x07390000 + OB:08x} rw-p 012dc000 fc:0f 2111       /usr/lib/libObj.so\n"
                f"{0x12f46000 + VB:08x}-{0x137f3000 + VB:08x} r-xp 00000000 fc:0f 2240       /usr/lib/viewUnified2.so\n"
                f"{0x13ac0000 + CB:08x}-{0x1455f000 + CB:08x} r-xp 00000000 fc:0f 2148       /usr/lib/CautionConfig.so\n").encode()
        mtl_lookup = 0x067A862E
        want = {0x134F5681: VB, 0x073C7000: OB, 0x14579428: CB, 0x0659AEA5: OB}
        singleton_acc = 0x06599E7D
    else:
        maps = (f"{0x05660000 + TB:08x}-{0x05663000 + TB:08x} r-xp 00000000 fc:0f 22470      /usr/lib/libtestcmd.so\n"
                f"{0x05d18000 + OB:08x}-{0x0700e000 + OB:08x} r-xp 00000000 fc:0f 22409      /usr/lib/libObj.so\n"
                f"{0x07016000 + OB:08x}-{0x07106000 + OB:08x} rw-p 012f6000 fc:0f 22409      /usr/lib/libObj.so\n"
                f"{0x155e6000 + VB:08x}-{0x15e92000 + VB:08x} r-xp 00000000 fc:0f 22532      /usr/lib/viewUnified2.so\n"
                f"{0x1615f000 + CB:08x}-{0x16bfe000 + CB:08x} r-xp 00000000 fc:0f 22443      /usr/lib/CautionConfig.so\n").encode()
        mtl_lookup = 0x064B54B6
        want = {0x15B91431: VB, 0x0713D000: OB, 0x16C183E8: CB, 0x06309D55: OB}
        singleton_acc = 0x06308D31

    class Moved(Camera):
        shim_at, shim_image, view_bias = SHIM + TB, bytes(shim), VB

    cam = Moved({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/MenuTouchFull.mod": reloc_mod,
                 "/proc/self/maps": maps})
    cam.m.mem_map((MTF_REL + OB) & ~0xfff, 0x1000)
    cam.m.mem_map((mtl_lookup + OB) & ~0xfff, 0x1000)     # libObj alcor lookup: no ModsTabLook hook
    for target, orig in MTF_HOOKS:
        cam.m.mem_write(target + OB, orig)
    texts = cam.open_menu()
    check(texts == ["Menu Touch Full", "OFF"], f"moved shim and core build the Mods tab, SET_TEXT at +{VB:#x} ({texts})")
    check(any(l.startswith("core_view_bias") for l in cam.log), "core reports the viewUnified2.so difference")
    check(cam.u32(ANCHOR + VB) == 0x444F4D53, "tab-artwork anchor created at the moved viewUnified2.so address")
    check("tab_icon_tab6 " + f"{OB & 0xFFFFFFFF:08x}" in cam.log,
          "no ModsTabLook hook at the moved libObj.so lookup: the tab uses the sixth tab's icon")
    cam.press(0)
    word = struct.unpack("<I", bytes(cam.m.mem_read(MTF_REL + OB + 8, 4)))[0]
    base = word - (msyms["mt_full_release"] | 1)
    check(base >= MMAP0 and bytes(cam.m.mem_read(MTF_REL + OB, 8)) == bytes.fromhex("dff804f000bf00bf"),
          f"release hook applied at the moved libObj.so target {MTF_REL + OB:#x}")
    code_size = struct.unpack_from("<I", reloc_mod, 44)[0]
    code_file = reloc_mod[68 + 80:68 + 80 + code_size]
    live = bytes(cam.m.mem_read(base, code_size))
    moved_words = {o: (struct.unpack_from("<I", live, o)[0] - struct.unpack_from("<I", code_file, o)[0]) & 0xFFFFFFFF
                   for o in range(0, code_size, 4) if live[o:o + 4] != code_file[o:o + 4]}
    # Find known references by value; compiler changes can move literal pools.
    literals = {o: word for o, (word,) in enumerate(struct.iter_unpack("<I", code_file))
                if word in want or word == singleton_acc}
    check(want.keys() <= set(literals.values())
          and all(moved_words.get(o * 4) == (want.get(value, OB) & 0xFFFFFFFF)
                  for o, value in literals.items()),
          f"known references (including the singleton accessor when present) moved by their library")
    check(set(moved_words.values()) <= {VB, OB & 0xFFFFFFFF, CB}, "no word moved by anything else")
    cam.press(0)
    check(all(bytes(cam.m.mem_read(t + OB, 12)) == o for t, o in MTF_HOOKS), "OFF restores both moved prologues")
    del cam.files["/proc/self/maps"]
    cam2 = Moved({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/MenuTouchFull.mod": reloc_mod})
    check(cam2.open_menu() == ["Menu Touch Full", "ERR"], "without /proc/self/maps the relocatable mod is refused (ERR)")

if len(sys.argv) > 5 and layout_aware:
    print("Scenario 14: auto-on ModsTabLook in a moved layout; a user OFF sticks")
    mtl = Path(sys.argv[5]).read_bytes()
    MTL_HOOK = 0x067A862E if IS_A6400A else 0x064B54B6
    MTL_RET = [0x067A85FF + OB, 0x067A863B + OB] if IS_A6400A else [0x064B5487 + OB, 0x064B54C3 + OB]
    MTL_ORIG = bytes.fromhex("f8b500af0d460446fff7e2ff")
    check(struct.unpack_from("<I", mtl, 8)[0] & 0xC == 0xC, "ModsTabLook file carries the relocation and auto-on flags")
    cam = Moved({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/ModsTabLook.mod": mtl, "/proc/self/maps": maps})
    cam.m.mem_map((MTL_HOOK + OB) & ~0xfff, 0x1000)
    cam.m.mem_write(MTL_HOOK + OB, MTL_ORIG)
    texts = cam.open_menu()
    live = bytes(cam.m.mem_read(MTL_HOOK + OB, 12))
    check(texts == ["Mods Tab Look", "ON"] and any(l.startswith("auto_on") for l in cam.log),
          f"first listing switches it ON ({texts})")
    check(struct.unpack_from("<I", live, 2)[0] == 0xF000F8DF, "hook at the moved alcor lookup: the core's tab-icon check sees it")
    base = struct.unpack_from("<I", live, 6)[0] & ~1
    mtl_code = mtl[68 + 40:68 + 40 + struct.unpack_from("<I", mtl, 44)[0]]
    live_mtl = bytes(cam.m.mem_read(base & ~0xFFF, len(mtl_code)))
    moved_mtl = [struct.unpack_from("<I", live_mtl, o)[0]
                 for o in range(0, len(mtl_code), 4) if live_mtl[o:o + 4] != mtl_code[o:o + 4]]
    check(moved_mtl == MTL_RET, "trampoline return addresses moved with libObj.so")
    cam.press(0)
    check(bytes(cam.m.mem_read(MTL_HOOK + OB, 12)) == MTL_ORIG, "user OFF restores the original lookup")
    check(cam.open_menu() == ["Mods Tab Look", "OFF"], "the next scan does not switch it ON again")

if (B / "ActionTest.mod").exists():
    print("Scenario 15: command item (SMOD_V2_FLAG_ACTION) starts the mod and closes the menu")
    action_mod = (B / "ActionTest.mod").read_bytes()
    _, _, tsyms = extract_elf_text((B / "test_mod.elf").read_bytes(), expected_code_addr=0)
    check(struct.unpack_from("<I", action_mod, 8)[0] & 0x10 == 0x10, "file carries the action flag")
    if IS_A6400A:
        OP_NEW, EV_CTOR, EV_PUSH, EV_SLOT = 0x060BD48C, 0x060BC938, 0x067F8C48, 0x07319874
        ref_maps = b"".join(row + b"\n" for row in (
            b"05fc2000-0729e000 r-xp 00000000 fc:0f 2111       /usr/lib/libObj.so",
            b"072a6000-07390000 rw-p 012dc000 fc:0f 2111       /usr/lib/libObj.so"))
        ev_pages = ((0x060BC000, 0x3000), (0x067F8000, 0x2000), (0x067A8000, 0x1000), (0x07319000, 0x2000))
        ev_ptr_slot, ev_mgr_obj, ev_alloc = 0x07319A00, 0x0731A5A5, 0x07319B00
    else:
        OP_NEW, EV_CTOR, EV_PUSH, EV_SLOT = 0x05E15974, 0x05E14E20, 0x05E18D5C, 0x07089984
        ref_maps = b"".join(row + b"\n" for row in (
            b"05d18000-0700e000 r-xp 00000000 fc:0f 22409      /usr/lib/libObj.so",
            b"07016000-07106000 rw-p 012f6000 fc:0f 22409      /usr/lib/libObj.so"))
        ev_pages = ((0x05E14000, 0x6000), (0x064B5000, 0x1000), (0x07089000, 0x2000))
        ev_ptr_slot, ev_mgr_obj, ev_alloc = 0x07089A00, 0x0708A5A5, 0x07089B00

    class EventCamera(Camera):
        def __init__(self, files):
            super().__init__(files)
            self.events = []
            for pg, sz in ev_pages:
                self.m.mem_map(pg, sz)
            self.m.mem_write(EV_SLOT, struct.pack("<I", ev_ptr_slot))
            self.m.mem_write(ev_ptr_slot, struct.pack("<I", ev_mgr_obj))

        def code(self, m, pc, size, _):
            r = [m.reg_read(x) for x in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3)]
            if pc == OP_NEW:
                self.ret(ev_alloc if r[0] == 16 else 0)
            elif pc == EV_CTOR:
                self.events.append(("ctor", r[0], r[1], r[2] & 0xFF, r[3] & 0xFF)); self.ret(r[0])
            elif pc == EV_PUSH:
                self.events.append(("push", r[0], r[1], r[2])); self.ret(0)
            else:
                super().code(m, pc, size, _)

    def row_text(cam):
        return [t for off, t in cam.texts if off == 0x9350 + 0xB60]

    cam = EventCamera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/UsbStreaming.mod": action_mod,
                       "/proc/self/maps": ref_maps})
    texts = cam.open_menu()
    check(texts == ["USB Streaming Test", ""], f"listed without an ON/OFF value ({texts})")
    cam.texts.clear()
    cam.press(0)
    base = None
    for line in cam.log:
        if line.startswith("action_close "):
            base = int(line.split()[1], 16)
    check(base is not None and cam.u32(base + tsyms["t_counter"]) == 1, "press switched the mod ON (on() ran once)")
    check(cam.events == [("ctor", ev_alloc, 0x15010102, 4, 1), ("push", ev_mgr_obj, ev_alloc, 1)],
          f"MENU key event queued to the live event manager ({cam.events})")
    check(row_text(cam) == ["Active"], f"row shows Active ({row_text(cam)})")
    check(cam.open_menu() == ["USB Streaming Test", "Active"], "running state survives a rescan")
    cam.events.clear(); cam.texts.clear()
    cam.press(0)
    check(cam.u32(base + tsyms["t_counter"]) == 0x101 and not cam.events and row_text(cam) == [""],
          "second press stops it (off() ran), menu stays open, value cleared")

    cam = EventCamera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/UsbStreaming.mod": action_mod})
    cam.open_menu()
    cam.press(0)
    check(any(l.startswith("action_close_unavailable") for l in cam.log) and not cam.events,
          "without a known libObj.so base no event is posted (reported, menu stays open)")
    check(cam.open_menu() == ["USB Streaming Test", "Active"], "the mod itself still started")

if (B / "SubmenuTest.mod").exists():
    print("Scenario 16: nested submenu (SMOD_V2_FLAG_SUBMENU) navigation, multi-page options, and live cfg callback")
    sub_mod = (B / "SubmenuTest.mod").read_bytes()
    _, tcode, tsyms = extract_elf_text((B / "test_mod.elf").read_bytes(), expected_code_addr=0)
    check(struct.unpack_from("<I", sub_mod, 8)[0] & 0x20 == 0x20, "file carries SMOD_V2_FLAG_SUBMENU")
    cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/SubmenuTest.mod": sub_mod,
                  "/tmp_bt/mods/TestMod.mod": testmod})
    texts = cam.open_menu()
    check(texts == ["Submenu Test", "OFF >", "Test Mod", "OFF"],
          f"submenu mod shows 'OFF >' chevron alongside regular mod ({texts})")
    cam.press(0)  # Enter Submenu Test nested submenu
    title = [t for off, t in cam.texts if off == 0x5470]
    rows_p1 = [t for off, t in cam.texts if off != 0x5470]
    check(title == ["Submenu Test"], f"header title switched to mod name ({title})")
    check(rows_p1 == [
        "< Back", "",
        "Mod Status", "OFF",
        "Binning Mode", "2x2 Bayer",
        "Photon Gain", "+2 EV (4x)",
        "Spatial Kernel", "Box Sum",
        "ARW Output", "16-bit Uncomp",
    ], f"submenu Page 1 renders < Back, Mod Status, and 4 default options ({rows_p1})")
    kids = cam.u32(cam.root + 4)
    tab_node = cam.u32(kids + 24)
    check(cam.u32(tab_node + 8) == 2 and len(cam.items(0)) == 6 and len(cam.items(1)) == 1,
          "submenu builds 2 pages (6 rows on Page 1, 1 row on Page 2)")

    # Customize Binning Mode (row 2 -> 2x2 Mono) while mod is still OFF, then check Page 2
    cam.texts.clear()
    cam.press(2, page_idx=0)
    check([t for _, t in cam.texts] == ["2x2 Mono"], "cycling option while OFF updates value text")
    cam.m.mem_write(MENU + 0x1F4, struct.pack("<I", 2))
    cam.texts.clear()
    cam.call("ml_after_refresh", MENU)
    check([t for off, t in cam.texts if off != 0x5470] == ["FD Conv Gain", "Force HCG"],
          "submenu Page 2 renders remaining option row")

    # Return to Page 1, turn Mod Status ON (row 1), verify bss_cfg_off populated BEFORE on()
    cam.m.mem_write(MENU + 0x1F4, struct.pack("<I", 1))
    cam.press(1, page_idx=0)
    hook = bytes(cam.m.mem_read(TARGETS + 0x100, 12))
    base = struct.unpack("<I", hook[8:12])[0] - tsyms["t_hook"]
    cfg_bytes = list(cam.m.mem_read(base + len(tcode) + 4, 5))
    check(cfg_bytes == [1, 0, 1, 0, 0] and cam.u32(base + tsyms["t_counter"]) == 1,
          f"pre-ON submenu selection synced to __smod_bss+4 ({cfg_bytes}) and on() ran once")

    # Cycle Photon Gain (row 3) while ON -> updates bss live and invokes t_cfg(2) (+0x1000)
    cam.texts.clear()
    cam.press(3, page_idx=0)
    cfg_bytes = list(cam.m.mem_read(base + len(tcode) + 4, 5))
    check(cfg_bytes == [1, 1, 1, 0, 0] and cam.u32(base + tsyms["t_counter"]) == 0x1001,
          f"live option cycle updated __smod_bss+4 ({cfg_bytes}) and called t_cfg(2)")

    # Press < Back (row 0) -> returns to top-level Mods with 'ON >'
    cam.press(0, page_idx=0)
    check([t for off, t in cam.texts if off == 0x5470] == ["Mods"] and
          [t for off, t in cam.texts if off != 0x5470] == ["Submenu Test", "ON >", "Test Mod", "OFF"],
          "pressing < Back returns to Mods tab showing 'ON >'")

    # Re-enter submenu, turn Mod Status OFF, close & reopen MENU -> custom selections persist!
    cam.press(0)
    cam.press(1, page_idx=0)
    check(bytes(cam.m.mem_read(TARGETS + 0x100, 12)) == ORIG12, "turning OFF inside submenu reverts hook")
    check(cam.open_menu() == ["Submenu Test", "OFF >", "Test Mod", "OFF"],
          "reopening MENU resets to top-level Mods tab")
    cam.press(0)
    rows_after = [t for off, t in cam.texts if off != 0x5470]
    check(rows_after[5] == "2x2 Mono" and rows_after[7] == "+1 EV (2x)",
          f"custom submenu selections survive OFF + menu rescan ({rows_after})")

if not IS_A6400A:
    print("Scenario 17: ILCE-6400 CTX_VERSION=2 (mlv2-layout-build-4) live upgrade migrates active mods safely")
    _, tcode, tsyms = extract_elf_text((B / "test_mod.elf").read_bytes(), expected_code_addr=0)
    cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/TestMod.mod": testmod})
    stock_kids = cam.u32(cam.root + 4)
    cam.open_menu()
    cam.press(0)  # Enable TestMod under current core first to allocate its code mapping + active hook
    shim_slot = cam.u32(SHIM)
    cur_ctx = cam.u32(shim_slot + 16)
    ctx_dump = bytes(cam.m.mem_read(cur_ctx, 0x4000))
    mod0_off = ctx_dump.index(b"Test Mod" + b"\0" * 24)
    m0 = ctx_dump[mod0_off : mod0_off + 500]
    v2_mod0 = m0[:32] + m0[32:96] + m0[136 : 136 + 364]
    assert len(v2_mod0) == 460
    # Synthesize a live CTX_VERSION=2 context (0x5000 B) carrying the active V2Mod (460 B at +0x448)
    v2_ctx_addr = 0x35000000
    cam.m.mem_map(v2_ctx_addr, 0x5000)
    v2_buf = bytearray(0x46D0)
    struct.pack_into("<5I", v2_buf, 0, 0x58544332, 2, 1, 1, 1)
    # Copy orig_root, orig_tab5, last_menu, vtable[72], root_children[8] (+0x18..+0x164 -> v2 +0x14..+0x160)
    v2_buf[0x14:0x160] = ctx_dump[0x18:0x164]
    v2_buf[0x448 : 0x448 + 460] = v2_mod0
    orig_root = struct.unpack_from("<I", v2_buf, 0x14)[0]
    cam.m.mem_write(orig_root + 4, struct.pack("<I", v2_ctx_addr + 0x140))
    struct.pack_into("<II", v2_buf, 0x46C8, stock_kids, 6)
    cam.m.mem_write(v2_ctx_addr, bytes(v2_buf))
    cam.m.mem_write(shim_slot + 16, struct.pack("<I", v2_ctx_addr))

    cam.files["/tmp_bt/mlv2/core.bin"] = with_fnv_variant(core_bin, pad=16)
    texts = cam.open_menu()
    check(any(l.startswith("ctx_v2_migrated 00000001") for l in cam.log) and texts == ["Test Mod", "ON"],
          f"CTX_VERSION=2 context migrated keeping enabled mod ON ({texts})")
    cam.press(0)
    check(bytes(cam.m.mem_read(TARGETS + 0x100, 12)) == ORIG12,
          "migrated core cleanly turns OFF mod that was enabled under CTX_VERSION=2")

print("Scenario 18: 1-level subfolder menu, mutual exclusion, and < Back")
_, _, tsyms = extract_elf_text((B / "test_mod.elf").read_bytes(), expected_code_addr=0)
mod_a = bytearray(testmod)
mod_a[12:44] = b"Look Alpha".ljust(32, b"\0")
mod_b = bytearray(mod_variant(testmod, target=TARGETS + 0x200, syms=tsyms))
mod_b[12:44] = b"Look Beta".ljust(32, b"\0")
cam = Camera({
    "/tmp_bt/mlv2/core.bin": core_bin,
    "/tmp_bt/mods/looks/Alpha.mod": bytes(mod_a),
    "/tmp_bt/mods/looks/Beta.mod": bytes(mod_b),
})
texts = cam.open_menu()
check(texts[:2] == ["looks", ">"], f"subfolder listed at top level ({texts})")
cam.press(0)  # Enter 'looks' folder
check([t for off, t in cam.texts if off == 0x5470][-1] == "looks", "header shows folder name")
cam.press(1)  # Enable Look Alpha
check(bytes(cam.m.mem_read(TARGETS + 0x100, 12)) != ORIG12, "Look Alpha enabled inside folder")
cam.press(2)  # Enable Look Beta -> mutually exclusive in folder, disables Look Alpha
check(bytes(cam.m.mem_read(TARGETS + 0x100, 12)) == ORIG12 and bytes(cam.m.mem_read(TARGETS + 0x200, 12)) != ORIG12,
      "enabling Look Beta automatically disabled Look Alpha in the same folder")
cam.press(0)  # < Back
check([t for off, t in cam.texts if off == 0x5470][-1] == "Mods" and
      [t for off, t in cam.texts if off == 0x9350 + 0xB60][-1] == "Look Beta",
      "returning to Mods shows active mod name on the folder row")

print("ALL SCENARIOS PASSED")
