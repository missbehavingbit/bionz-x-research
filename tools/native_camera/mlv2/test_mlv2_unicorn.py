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
SHIM = 0x05662800
SET_TEXT, SET_MODE = 0x157356D0, 0x1573BA68
ANCHOR = 0x1615EC40
MENU, NODES, STUBS, TARGETS, V1CODE = 0x30000000, 0x31000000, 0x32000000, 0x33000000, 0x34000000
MMAP0, STACK, STOP = 0x40000000, 0x50000000, 0x5F000000
ORIG12 = bytes.fromhex("2de9f04788b04ff014080122")

_, shim_code, shim_syms = extract_elf_text((B / "shim.elf").read_bytes(), expected_code_addr=SHIM)
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
        for base, size in ((0x15700000 + vb, 0x100000), ((ANCHOR + vb) & ~0xfff, 0x1000), (MENU, 0x20000), (NODES, 0x10000),
                           (STUBS, 0x1000), (TARGETS, 0x1000), (V1CODE, 0x1000), (STACK - 0x10000, 0x10000), (STOP, 0x1000)):
            m.mem_map(base, size)
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
                names = sorted({p.rsplit("/", 1)[1] for p in self.files if p.rsplit("/", 1)[0] == path})
                if names or path == "/tmp_bt/mods":
                    res = self.next_fd; self.next_fd += 1; self.fds[res] = ["dir", names, False]
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
                for name in f[1]:
                    nm = name.encode() + b"\0"
                    rl = (19 + len(nm) + 7) & ~7
                    buf += struct.pack("<QqHB", 1, 0, rl, 8) + nm.ljust(rl - 19, b"\0")
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

    def items(self):
        kids = self.u32(self.root + 4)
        tab = self.u32(kids + 24)
        pages = self.u32(tab + 4)
        page0 = self.u32(pages)
        items = self.u32(page0 + 4)
        return [self.u32(items + 4 * i) for i in range(self.u32(page0 + 8))]

    def press(self, index):
        self.call("ml_before_enter", MENU, self.items()[index])


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
    print("Scenario 10: imported v1 MenuTouchFull is replaced by the SMOD v2 build")
    mtf_dir = Path(sys.argv[3])
    mtf_v2 = (mtf_dir / "MenuTouchFull.mod").read_bytes()
    mtf_v1 = (root / "mods/menu_touch_full/MenuTouchFull.mod").read_bytes()
    MTF_HOOKS = ((0x06309D48, bytes.fromhex("2de9f04788b04ff014080122")),
                 (0x06309E54, bytes.fromhex("2de9f04788b04ff014090122")))
    v1_hooks = [mtf_v1[68 + 40 * i:108 + 40 * i] for i in range(2)]
    v1 = bytearray(8192)
    struct.pack_into("<3I", v1, 0, 0x444F4D53, 1, 1)
    hdr = struct.unpack_from("<6I", mtf_v1, 44)           # code_addr, code_size, state_addr, state_size, hooks, fnv
    entry_v1 = (b"Menu Touch Full".ljust(32, b"\0") + b"/tmp_bt/mods/MenuTouchFull.mod".ljust(64, b"\0") +
                bytes([1, 2, 0, 1]) + struct.pack("<5I", hdr[0], hdr[1], hdr[2], hdr[3], hdr[5]) +
                b"".join(v1_hooks) + bytes(440 - 120 - 80))
    v1[0x418:0x418 + 440] = entry_v1                       # enabled, 2 hooks, SMOD flags 1 (state init)
    cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/MenuTouchFull.mod": mtf_v1}, v1_state=bytes(v1))
    cam.m.mem_map(0x06309000, 0x1000)
    for (target, _), rec in zip(MTF_HOOKS, v1_hooks):
        cam.m.mem_write(target, rec[24:24 + 12])           # the v1 loader had applied both hooks
    check(cam.open_menu() == ["Menu Touch Full", "ON"], "enabled v1 MenuTouchFull imported as ON")
    cam.files["/tmp_bt/mods/MenuTouchFull.mod"] = mtf_v2
    check(cam.open_menu() == ["Menu Touch Full", "ON*"], "replacing the file marks the running v1 copy as changed (ON*)")
    cam.press(0)
    check(all(bytes(cam.m.mem_read(t, 12)) == o for t, o in MTF_HOOKS), "OFF reverts both v1 hooks")
    check(cam.open_menu() == ["Menu Touch Full", "OFF"], "next rescan lists the v2 file, OFF")
    cam.press(0)
    word = struct.unpack("<I", bytes(cam.m.mem_read(0x06309D48 + 8, 4)))[0]
    msyms = {p[2]: int(p[0], 16) for p in (ln.split() for ln in (mtf_dir / "mtf.sym").read_text().splitlines())
             if len(p) == 3}
    base = word - (msyms["mt_full_release"] | 1)
    code_size = struct.unpack_from("<I", mtf_v2, 44)[0]
    check(base >= MMAP0 and base % 0x1000 == 0 and
          bytes(cam.m.mem_read(0x06309D48, 8)) == bytes.fromhex("dff804f000bf00bf"),
          f"v2 ON: release hook points into the core-allocated mapping at {base:#x}")
    check(struct.unpack("<I", bytes(cam.m.mem_read(0x06309E54 + 8, 4)))[0] == base + (msyms["mt_full_press"] | 1),
          "v2 ON: press hook fixed up to the same base")
    st = struct.unpack("<6I", bytes(cam.m.mem_read(base + code_size, 24)))
    check(st[0] == 0x4D544632 and st[1] == 2 and st[2] == 2 and st[5] == 16, "on() published the MTF2 state in the bss")
    cam.press(0)
    check(all(bytes(cam.m.mem_read(t, 12)) == o for t, o in MTF_HOOKS), "v2 OFF restores both prologues")
    st = struct.unpack("<3I", bytes(cam.m.mem_read(base + code_size, 12)))
    check(st[0] == 0 and st[2] == 0, "off() cleared magic and enabled before the hooks were reverted")


if len(sys.argv) > 3 and b"/tmp_bt/mlv2/reload" in core_bin:
    print("Scenario 12: MenuTouchFull reload starts the new copy with fresh gesture state")
    cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/MenuTouchFull.mod": mtf_v2})
    cam.m.mem_map(0x06309000, 0x1000)
    for target, orig in MTF_HOOKS:
        cam.m.mem_write(target, orig)
    cam.open_menu()
    cam.press(0)

    def mtf_base():
        return struct.unpack("<I", bytes(cam.m.mem_read(0x06309D48 + 8, 4)))[0] - (msyms["mt_full_release"] | 1)

    base1 = mtf_base()
    st1 = base1 + code_size
    cam.m.mem_write(st1 + 24, b"\x5a" * 32)                 # gesture data recorded by the old copy
    res, texts = reload(cam, "MenuTouchFull.mod")
    base2 = mtf_base()
    check(res.startswith("reload_ok") and base2 != base1 and texts == ["Menu Touch Full", "ON"],
          f"same file reloaded into a new mapping {base1:#x} -> {base2:#x} ({res})")
    check(struct.unpack("<I", bytes(cam.m.mem_read(0x06309E54 + 8, 4)))[0] == base2 + (msyms["mt_full_press"] | 1),
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
    maps = (f"{0x05660000 + TB:08x}-{0x05663000 + TB:08x} r-xp 00000000 fc:0f 22470      /usr/lib/libtestcmd.so\n"
            f"{0x05d18000 + OB:08x}-{0x0700e000 + OB:08x} r-xp 00000000 fc:0f 22409      /usr/lib/libObj.so\n"
            f"{0x07016000 + OB:08x}-{0x07106000 + OB:08x} rw-p 012f6000 fc:0f 22409      /usr/lib/libObj.so\n"
            f"{0x155e6000 + VB:08x}-{0x15e92000 + VB:08x} r-xp 00000000 fc:0f 22532      /usr/lib/viewUnified2.so\n"
            f"{0x1615f000 + CB:08x}-{0x16bfe000 + CB:08x} r-xp 00000000 fc:0f 22443      /usr/lib/CautionConfig.so\n").encode()

    class Moved(Camera):
        shim_at, shim_image, view_bias = SHIM + TB, bytes(shim), VB

    cam = Moved({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/MenuTouchFull.mod": reloc_mod,
                 "/proc/self/maps": maps})
    cam.m.mem_map((0x06309000 + OB) & ~0xfff, 0x1000)
    cam.m.mem_map((0x064B54B6 + OB) & ~0xfff, 0x1000)     # libObj alcor lookup: no ModsTabLook hook
    for target, orig in MTF_HOOKS:
        cam.m.mem_write(target + OB, orig)
    texts = cam.open_menu()
    check(texts == ["Menu Touch Full", "OFF"], f"moved shim and core build the Mods tab, SET_TEXT at +{VB:#x} ({texts})")
    check(any(l.startswith("core_view_bias") for l in cam.log), "core reports the viewUnified2.so difference")
    check(cam.u32(ANCHOR + VB) == 0x444F4D53, "tab-artwork anchor created at the moved viewUnified2.so address")
    check("tab_icon_tab6 " + f"{OB & 0xFFFFFFFF:08x}" in cam.log,
          "no ModsTabLook hook at the moved libObj.so lookup: the tab uses the sixth tab's icon")
    cam.press(0)
    word = struct.unpack("<I", bytes(cam.m.mem_read(0x06309D48 + OB + 8, 4)))[0]
    base = word - (msyms["mt_full_release"] | 1)
    check(base >= MMAP0 and bytes(cam.m.mem_read(0x06309D48 + OB, 8)) == bytes.fromhex("dff804f000bf00bf"),
          f"release hook applied at the moved libObj.so target {0x06309D48 + OB:#x}")
    code_size = struct.unpack_from("<I", reloc_mod, 44)[0]
    code_file = reloc_mod[68 + 80:68 + 80 + code_size]
    live = bytes(cam.m.mem_read(base, code_size))
    moved_words = {o: (struct.unpack_from("<I", live, o)[0] - struct.unpack_from("<I", code_file, o)[0]) & 0xFFFFFFFF
                   for o in range(0, code_size, 4) if live[o:o + 4] != code_file[o:o + 4]}
    # Find known references by value; compiler changes can move literal pools.
    want = {0x15B91431: VB, 0x0713D000: OB, 0x16C183E8: CB, 0x06309D55: OB}
    literals = {o: word for o, (word,) in enumerate(struct.iter_unpack("<I", code_file))
                if word in want or word == 0x06308D31}
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
    MTL_HOOK, MTL_ORIG = 0x064B54B6, bytes.fromhex("f8b500af0d460446fff7e2ff")
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
    words = [struct.unpack("<I", bytes(cam.m.mem_read((base & ~0xFFF) + o, 4)))[0] for o in (0x154, 0x158)]
    check(words == [0x064B5487 + OB, 0x064B54C3 + OB], "trampoline return addresses moved with libObj.so")
    cam.press(0)
    check(bytes(cam.m.mem_read(MTL_HOOK + OB, 12)) == MTL_ORIG, "user OFF restores the original lookup")
    check(cam.open_menu() == ["Mods Tab Look", "OFF"], "the next scan does not switch it ON again")

if (B / "ActionTest.mod").exists():
    print("Scenario 15: command item (SMOD_V2_FLAG_ACTION) starts the mod and closes the menu")
    action_mod = (B / "ActionTest.mod").read_bytes()
    _, _, tsyms = extract_elf_text((B / "test_mod.elf").read_bytes(), expected_code_addr=0)
    check(struct.unpack_from("<I", action_mod, 8)[0] & 0x10 == 0x10, "file carries the action flag")
    OP_NEW, EV_CTOR, EV_PUSH, EV_SLOT = 0x05E15974, 0x05E14E20, 0x05E18D5C, 0x07089984
    ref_maps = b"".join(row + b"\n" for row in (
        b"05d18000-0700e000 r-xp 00000000 fc:0f 22409      /usr/lib/libObj.so",
        b"07016000-07106000 rw-p 012f6000 fc:0f 22409      /usr/lib/libObj.so"))

    class EventCamera(Camera):
        def __init__(self, files):
            super().__init__(files)
            self.events = []
            self.m.mem_map(0x05E14000, 0x6000)
            self.m.mem_map(0x064B5000, 0x1000)                 # ModsTabLook check reads here
            self.m.mem_map(0x07089000, 0x1000)
            self.m.mem_write(EV_SLOT, struct.pack("<I", 0x07089A00))
            self.m.mem_write(0x07089A00, struct.pack("<I", 0x0708A5A5))

        def code(self, m, pc, size, _):
            r = [m.reg_read(x) for x in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3)]
            if pc == OP_NEW:
                self.ret(0x07089B00 if r[0] == 16 else 0)
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
    check(cam.events == [("ctor", 0x07089B00, 0x15010102, 4, 1), ("push", 0x0708A5A5, 0x07089B00, 1)],
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

print("ALL SCENARIOS PASSED")
