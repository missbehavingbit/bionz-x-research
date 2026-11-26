"""Offline verification of the Live Picker `FilmSimulations.mod` (SMOD v2, event_push hook).

The portable app emits this mod in pure Rust (`bionz-preset::smod_v2::emit_live_smod_v2`) from a
committed ARM skeleton that already carries the `event_push` hook, the 48-byte bss and the RLC1
relocation table. This suite checks it without a camera:

1. `bionz-cli live-mod` packs every exportable `.bzlook` into one `.mod`.
2. `src.a6400.mod_manager.smod.parse_smod` (the Mod Manager's strict parser) accepts it: version 2,
   FLAG_ACTION | FLAG_RELOC, one hook, 48 bytes of bss, a lifecycle entry, a valid RLC1 table.
3. Unicorn installs the hook the way Mod Loader v2 does (trampoline at the firmware target plus the
   `base + entry` fixup), runs `live_life(1)`, then drives `event_push` with key events:
   - Left/Right (and Up/Down) cycle Stock + the twelve looks live in ISP RAM;
   - the stock slot restores the stock tables and bypasses the CC tuning word;
   - ENTER locks the choice, clears the banner and passes the release through;
   - a foreign event and every disarmed key go to the original `event_push` untouched;
   - MENU disarms after the first (closing) event and draws/clears the OSD banner on OSD_A and OSD_B.

Run from the repository root:
    .\\venv\\Scripts\\python.exe -m unittest tests.test_look_lab_live_smod_v2 -v
"""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.a6400.mod_manager.smod import SMOD_MAGIC, parse_smod

from tests.test_look_lab_smod_v2 import KNOWN_TARGETS, PRESETS, ROOT, SHIPPED, ensure_cli, pack_i16, pack_u16, pack_u32, preset_table

try:
    import unicorn as uc
    from unicorn import arm_const as A
except ModuleNotFoundError:
    uc = None
    A = None

PAGE = 0x1000
MOD_BASE = 0x40B30000
STACK = 0x20000000
STOP = 0x32000000
EMULATED = 0x31000000
OSD_A = 0x0BFD0000
OSD_B = 0x0CFDD000
OSD_BYTES = 0x96000
EV = 0x2000F000
HOOK_PAGE = 0x0655A000
LIVE_MAGIC = 0x46534C31
# struct live_state word indices (magic, status[16], armed, skip_first_menu, swallow_enter_rel, idx, ...)
BSS_MAGIC, BSS_ARMED, BSS_SKIP_MENU, BSS_SWALLOW, BSS_IDX = 0, 5, 6, 7, 8
FLAG_ACTION = 16
FLAG_RELOC = 4

# The original event_push resumes at libObj+0x842535 (0x0655a535) after the trampoline rebuilt its
# frame. Return a marker instead of the real function: pop the rebuilt frame and return to
# live_event_push's caller, exactly as the original epilogue would.
PASSTHROUGH_MARK = 0x5A
ORIGINAL_TAIL = bytes.fromhex("5a20" "04b0" "bde8f081")  # movs r0,#0x5a; add sp,#16; pop {r4-r8,pc}

# The camera exposes OSD A and OSD B as /dev/stream mappings whose *offset* column is the physical
# address (E0298): the mod scans /proc/self/maps for "23a80000"/"23b16000" and keeps the mapping base.
MAPS = (b"0bfd0000-0c066000 rw-s 23a80000  /dev/stream\n"
        b"0cfdd000-0d073000 rw-s 23b16000  /dev/stream\n")

# Key ids from film_sim_live.c.
KEY_ENTER_PRESS, KEY_ENTER_REL = 0x15010052, 0x15010051
KEY_UP_PRESS, KEY_UP_REL = 0x15010062, 0x15010061
KEY_DOWN_PRESS, KEY_DOWN_REL = 0x15010072, 0x15010071
KEY_RIGHT_PRESS, KEY_RIGHT_REL = 0x15010082, 0x15010081
KEY_LEFT_PRESS, KEY_LEFT_REL = 0x15010092, 0x15010091
KEY_MENU = 0x15010102
FOREIGN = 0x15010199

BANNER_TOP_LEFT = (42 * 640 + 120) * 2
BANNER_RECT_BYTES = (26 * 640) * 2

# The live skeleton's layout: where the payload and its name table sit inside the code.
LIVE_RECORD = json.loads((ROOT / "isp-studio/crates/bionz-preset/assets/film_sim_live_template.json").read_text())


def slot_names(mod: bytes) -> list[str]:
    """The look names the picker shows, in slot order (slot 0, "Stock (Off)", excluded)."""
    payload = LIVE_RECORD["payload"]
    base = LIVE_RECORD["code_offset"] + payload["offset"]
    looks = struct.unpack_from("<I", mod, base + payload["fields"]["n_looks"])[0]
    names = []
    for slot in range(1, looks + 1):
        start = base + payload["fields"]["names"] + slot * 16
        names.append(mod[start:start + 16].split(b"\0")[0].decode())
    return names


def preset_name(stem: str) -> str:
    import zipfile
    with zipfile.ZipFile(PRESETS / f"{stem}.bzlook") as zf:
        for line in zf.read("look.toml").decode().splitlines():
            if line.startswith("name ="):
                return line.split("=", 1)[1].strip().strip('"')
    raise AssertionError(f"{stem}: look.toml has no name")


def slot_tables(mod: bytes, target: str) -> list[dict]:
    """Each slot's compiled table, resolved through the names the mod itself carries.

    The CLI sorts presets by file name and the app by its curated library order, so the expected
    order comes from the file rather than from an assumption shared with the packer.
    """
    by_name = {preset_name(stem): preset_table(stem, target)["table"] for stem in SHIPPED}
    tables = []
    for name in slot_names(mod):
        matches = [table for full, table in by_name.items() if full.startswith(name)]
        assert len(matches) == 1, f"slot name {name!r} matches {len(matches)} presets"
        tables.append(matches[0])
    return tables


class LiveMachine:
    """Thumb code at an arbitrary base, the loader's hook install, emulated `/dev/mem` and OSD."""

    def __init__(self, mod: bytes, addrs: dict):
        magic, version, flags, name, code_len, checksum, bss, on_off, off_off, hooks = struct.unpack_from("<III32s6I", mod)
        assert magic == SMOD_MAGIC and version == 2, "not an SMOD v2 file"
        assert hooks == 1, f"expected one hook, got {hooks}"
        self.mod, self.addrs, self.on_off = mod, addrs, on_off
        self.code_off = 68 + 40 * hooks
        self.code = mod[self.code_off:self.code_off + code_len]
        self.bss = bss
        self.uc = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
        size = (len(self.code) + bss + PAGE - 1) & ~(PAGE - 1)
        self.uc.mem_map(MOD_BASE, size)
        self.uc.mem_write(MOD_BASE, self.code + bytes(bss))
        self.uc.mem_map(STACK, 0x10000)
        self.uc.mem_map(STOP, PAGE)
        for base in (OSD_A, OSD_B):
            self.uc.mem_map(base, OSD_BYTES)
        self.pages: dict[int, int] = {}
        self.next_host = EMULATED
        self.log: list[str] = []
        self.maps_read = 0
        self._install_hook()
        self.uc.hook_add(uc.UC_HOOK_INTR, self._intr)

    def _install_hook(self) -> None:
        """Apply the header's hook record the way Mod Loader v2 does."""
        target, patch, fixup, orig, tmpl = struct.unpack_from("<IBB2x16s16s", self.mod, 68)
        self.hook_target, self.hook_orig = target, orig[:patch]
        self.uc.mem_map(HOOK_PAGE, PAGE)
        self.uc.mem_write(target, tmpl[:patch])
        entry = struct.unpack_from("<I", tmpl, fixup)[0]
        assert entry & 1, "the hook fixup word must be a Thumb entry"
        self.uc.mem_write(target + fixup, struct.pack("<I", MOD_BASE + entry))
        self.uc.mem_write(0x0655A535 & ~1, ORIGINAL_TAIL)

    # -- emulated physical memory --------------------------------------------------------------
    def page(self, pa_page: int) -> int:
        if pa_page not in self.pages:
            self.uc.mem_map(self.next_host, PAGE)
            self.uc.mem_write(self.next_host, b"\x5a" * PAGE)
            self.pages[pa_page] = self.next_host
            self.next_host += PAGE
        return self.pages[pa_page]

    def peek(self, pa: int, n: int) -> bytes:
        out = b""
        while n:
            off = pa & 0xFFF
            take = min(n, PAGE - off)
            out += bytes(self.uc.mem_read(self.page(pa >> 12) + off, take))
            pa += take
            n -= take
        return out

    def poke(self, pa: int, data: bytes) -> None:
        while data:
            off = pa & 0xFFF
            take = min(len(data), PAGE - off)
            self.uc.mem_write(self.page(pa >> 12) + off, data[:take])
            pa += take
            data = data[take:]

    def bss_u32(self, index: int) -> int:
        at = MOD_BASE + len(self.code) + index * 4
        return struct.unpack("<I", bytes(self.uc.mem_read(at, 4)))[0]

    # -- syscalls ------------------------------------------------------------------------------
    def _intr(self, mu, _intno, _user) -> None:
        nr = mu.reg_read(A.UC_ARM_REG_R7)
        a = [mu.reg_read(r) for r in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2,
                                      A.UC_ARM_REG_R3, A.UC_ARM_REG_R4, A.UC_ARM_REG_R5)]
        ret = 0
        if nr == 5:  # open
            path = bytes(mu.mem_read(a[0], 64)).split(b"\0")[0].decode()
            if path == "/dev/mem":
                ret = 3
            elif path == "/proc/self/maps":
                ret = 5
            elif path == "/tmp_bt/mods/film_sim_live.log":
                ret = 4
            else:
                raise AssertionError(f"unexpected open({path!r})")
        elif nr == 3:  # read
            if a[0] == 5:
                chunk = MAPS[self.maps_read:self.maps_read + a[2]]
                self.maps_read += len(chunk)
                if chunk:
                    mu.mem_write(a[1], chunk)
                ret = len(chunk)
            else:
                ret = 0
        elif nr == 192:  # mmap2
            if a[0] != 0 or a[1] != PAGE or a[2] != 3 or a[3] != 1 or a[4] != 3:
                raise AssertionError(f"unexpected mmap2 args {a}")
            ret = self.page(a[5])
        elif nr == 4:  # write
            data = bytes(mu.mem_read(a[1], a[2]))
            if a[0] == 4:
                self.log.append(data.decode())
            ret = len(data)
        elif nr in (6, 91):  # close, munmap
            ret = 0
        else:
            raise AssertionError(f"unexpected syscall {nr}")
        mu.reg_write(A.UC_ARM_REG_R0, ret)

    # -- execution -----------------------------------------------------------------------------
    def run(self, address: int, r0: int = 0, r1: int = 0, r2: int = 0) -> int:
        """Run from an absolute Thumb address (bit 0 set) until the code returns."""
        self.uc.reg_write(A.UC_ARM_REG_R0, r0)
        self.uc.reg_write(A.UC_ARM_REG_R1, r1)
        self.uc.reg_write(A.UC_ARM_REG_R2, r2)
        self.uc.reg_write(A.UC_ARM_REG_SP, STACK + 0xF000)
        self.uc.reg_write(A.UC_ARM_REG_LR, STOP | 1)
        self.uc.emu_start(address | 1, STOP, count=4_000_000)
        return self.uc.reg_read(A.UC_ARM_REG_R0)

    def live_life(self) -> int:
        return self.run(MOD_BASE + self.on_off, r0=1)

    def press(self, key: int) -> int:
        """Call the firmware's event_push (the hook target), as the camera does."""
        self.uc.mem_write(EV, bytes(16))
        self.uc.mem_write(EV + 4, struct.pack("<I", key))
        return self.run(self.hook_target, r0=STACK, r1=EV, r2=0)

    def tables(self):
        addrs = self.addrs
        return (
            self.peek(addrs["ec_gamma_pa"], 384),
            self.peek(addrs["fc_gamma_pa"], 192),
            self.peek(addrs["color_a0_pa"], 18),
            self.peek(addrs["color_a1_pa"], 18),
            self.peek(addrs["color_b2_pa"], 18),
            self.peek(addrs["cc_tuning_pa"], 2),
            self.peek(addrs["refadr0_pa"], 1215),
        )

    def banner(self, base: int) -> bytes:
        return bytes(self.uc.mem_read(base + BANNER_TOP_LEFT, BANNER_RECT_BYTES))


class ExportFixture:
    def __init__(self) -> None:
        self.workdir = Path(tempfile.mkdtemp(prefix="look-lab-live-"))
        self.cli = ensure_cli()
        self.cache: dict[str, bytes] = {}

    def close(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def mod(self, target: str) -> bytes:
        if target in self.cache:
            return self.cache[target]
        if self.cli is None:
            raise unittest.SkipTest("bionz-cli is not built and cargo is not available")
        out = self.workdir / f"FilmSimulations-{target}.mod"
        done = subprocess.run(
            [str(self.cli), "live-mod", str(PRESETS), "--target", target, "--output", str(out)],
            cwd=ROOT, capture_output=True, text=True, timeout=600,
        )
        if done.returncode != 0:
            raise AssertionError(f"bionz-cli live-mod {target} failed: {done.stderr.strip()}")
        info = json.loads(done.stdout)
        assert info["kind"] == "live" and info["bytes"] == out.stat().st_size, info
        assert len(info["looks"]) == 12 and not info["skipped"], info
        self.cache[target] = out.read_bytes()
        return self.cache[target]


FIXTURE: ExportFixture | None = None


def setUpModule() -> None:
    global FIXTURE
    FIXTURE = ExportFixture()


def tearDownModule() -> None:
    if FIXTURE is not None:
        FIXTURE.close()


def seeded(machine: LiveMachine, stock: dict) -> None:
    addrs = machine.addrs
    machine.poke(addrs["ec_gamma_pa"], pack_u32(stock["stock_ec"]))
    machine.poke(addrs["fc_gamma_pa"], pack_u16(stock["stock_fc"]))
    machine.poke(addrs["color_a0_pa"], pack_i16(stock["stock_a0"]))
    machine.poke(addrs["color_a1_pa"], pack_i16(stock["stock_a1"]))
    machine.poke(addrs["color_b2_pa"], pack_i16(stock["stock_b2"]))
    machine.poke(addrs["cc_tuning_pa"], b"\xff\xff")
    machine.poke(addrs["refadr0_pa"], b"\x00" * 1215)


def expected_look(addrs: dict, table: dict, on: bool):
    return (
        pack_u32(table["ec"] if on else table["stock_ec"]),
        pack_u16(table["fc"] if on else table["stock_fc"]),
        pack_i16(table["a0"] if on else table["stock_a0"]),
        pack_i16(table["a1"] if on else table["stock_a1"]),
        pack_i16(table["b2"] if on else table["stock_b2"]),
        b"\x00\x00" if on else b"\xff\xff",
        table["refadr"] if on else bytes(1215),
    )


class TestLookLabLiveSmodV2(unittest.TestCase):
    def mod(self, target):  # noqa: ANN001
        assert FIXTURE is not None
        return FIXTURE.mod(target)

    def test_parser_accepts_the_live_picker_for_both_bodies(self):
        for target in KNOWN_TARGETS:
            blob = self.mod(target)
            info = parse_smod(blob, "FilmSimulations.mod")
            self.assertEqual(info.version, 2)
            self.assertEqual(info.name, "Film Simulations")
            self.assertEqual(info.flags, FLAG_ACTION | FLAG_RELOC)
            self.assertTrue(info.has_reloc)
            self.assertTrue(info.lifecycle)
            self.assertFalse(info.auto_on)
            self.assertEqual(info.hook_count, 1)
            self.assertEqual(info.bss_size, 48)
            self.assertEqual(info.code_size + 68 + 40 + 48, len(blob), "header + hook + code + RLC1 table")
            self.assertEqual(len(blob) % 4, 0)

    def test_both_bodies_get_their_own_addresses_and_the_same_looks(self):
        a6400, a6100 = self.mod("a6400"), self.mod("a6100")
        self.assertEqual(len(a6400), len(a6100))
        self.assertNotEqual(a6400, a6100)
        self.assertEqual(a6400[12:28], b"Film Simulations")
        self.assertEqual(a6400[68:80], a6100[68:80], "the hook record is layout data, not per-body")


@unittest.skipIf(uc is None, "unicorn is not installed; the ARM live-picker check needs it")
class TestLookLabLiveSmodV2Unicorn(unittest.TestCase):
    def machine(self, target: str) -> LiveMachine:
        assert FIXTURE is not None
        machine = LiveMachine(FIXTURE.mod(target), KNOWN_TARGETS[target])
        seeded(machine, preset_table(SHIPPED[0], target)["table"])
        return machine

    def test_live_view_cycles_looks_and_locks(self):
        target = "a6400"
        addrs = KNOWN_TARGETS[target]
        machine = self.machine(target)
        looks = slot_tables(machine.mod, target)
        self.assertEqual(len(looks), 12)

        # ON: the picker arms in Live View and applies slot 1 = the first look.
        self.assertEqual(machine.live_life(), 0)
        self.assertEqual(machine.bss_u32(BSS_MAGIC), LIVE_MAGIC, "state magic published")
        self.assertEqual(machine.bss_u32(BSS_ARMED), 1, "armed")
        self.assertEqual(machine.bss_u32(BSS_IDX), 1, "idx = slot 1 (first look)")
        self.assertEqual(machine.tables(), expected_look(addrs, looks[0], True))
        self.assertEqual(machine.banner(OSD_A)[:2], b"\x2f\xfc", "banner border drawn on OSD_A")
        self.assertIn(b"\xff\xff", machine.banner(OSD_B), "banner text drawn on OSD_B")
        self.assertIn("live_on", "".join(machine.log))

        # The first MENU event closes MENU and is passed through; the picker stays armed.
        self.assertEqual(machine.press(KEY_MENU), PASSTHROUGH_MARK)
        self.assertEqual(machine.bss_u32(BSS_ARMED), 1, "still armed after the closing MENU event")

        # Right/Down step forward, Left/Up step back, and every step writes the look's tables.
        self.assertEqual(machine.press(KEY_RIGHT_PRESS), 0)
        self.assertEqual(machine.bss_u32(BSS_IDX), 2)
        self.assertEqual(machine.tables(), expected_look(addrs, looks[1], True))
        self.assertEqual(machine.press(KEY_DOWN_PRESS), 0)
        self.assertEqual(machine.bss_u32(BSS_IDX), 3)
        self.assertEqual(machine.tables(), expected_look(addrs, looks[2], True))
        self.assertEqual(machine.press(KEY_LEFT_PRESS), 0)
        self.assertEqual(machine.bss_u32(BSS_IDX), 2)
        self.assertEqual(machine.tables(), expected_look(addrs, looks[1], True))
        self.assertEqual(machine.press(KEY_UP_PRESS), 0)
        self.assertEqual(machine.bss_u32(BSS_IDX), 1)

        # Slot 0 is Stock: the stock tables come back and the CC tuning word is bypassed.
        self.assertEqual(machine.press(KEY_LEFT_PRESS), 0)
        self.assertEqual(machine.bss_u32(BSS_IDX), 0)
        self.assertEqual(machine.tables(), expected_look(addrs, looks[0], False))
        self.assertEqual(machine.press(KEY_RIGHT_PRESS), 0)
        self.assertEqual(machine.bss_u32(BSS_IDX), 1)
        self.assertEqual(machine.tables(), expected_look(addrs, looks[0], True))

        # Key releases are swallowed while armed.
        self.assertEqual(machine.press(KEY_RIGHT_REL), 0)
        self.assertEqual(machine.press(KEY_LEFT_REL), 0)

        # ENTER locks the look: not armed, banner cleared, the release swallowed once.
        self.assertEqual(machine.press(KEY_ENTER_PRESS), 0)
        self.assertEqual(machine.bss_u32(BSS_ARMED), 0, "locked")
        self.assertEqual(machine.bss_u32(BSS_SWALLOW), 1, "the ENTER release is swallowed next")
        self.assertEqual(machine.banner(OSD_A), bytes(len(machine.banner(OSD_A))), "OSD_A banner cleared")
        self.assertEqual(machine.banner(OSD_B), bytes(len(machine.banner(OSD_B))), "OSD_B banner cleared")
        self.assertEqual(machine.press(KEY_ENTER_REL), 0, "the paired release is swallowed")
        self.assertEqual(machine.bss_u32(BSS_SWALLOW), 0)

        # Locked: keys and foreign events reach the original event_push untouched.
        locked = machine.tables()
        self.assertEqual(machine.press(KEY_RIGHT_PRESS), PASSTHROUGH_MARK)
        self.assertEqual(machine.tables(), locked, "a locked picker does not change the ISP")
        self.assertEqual(machine.press(FOREIGN), PASSTHROUGH_MARK)

        # Re-arm keeps the chosen look, then MENU disarms for good.
        self.assertEqual(machine.live_life(), 0)
        self.assertEqual(machine.bss_u32(BSS_IDX), 1, "re-armed on the same look")
        self.assertEqual(machine.press(KEY_MENU), PASSTHROUGH_MARK, "the closing MENU event passes through")
        self.assertEqual(machine.press(KEY_MENU), PASSTHROUGH_MARK)
        self.assertEqual(machine.bss_u32(BSS_ARMED), 0, "MENU disarmed the picker")
        self.assertIn("menu_disarm", "".join(machine.log))
        self.assertEqual(machine.banner(OSD_A), bytes(len(machine.banner(OSD_A))))

    def test_every_slot_writes_its_own_look(self):
        target = "a6100"
        addrs = KNOWN_TARGETS[target]
        machine = self.machine(target)
        looks = slot_tables(machine.mod, target)
        machine.live_life()
        for slot in range(1, len(looks) + 1):
            if slot > 1:
                self.assertEqual(machine.press(KEY_RIGHT_PRESS), 0)
            self.assertEqual(machine.bss_u32(BSS_IDX), slot)
            self.assertEqual(machine.tables(), expected_look(addrs, looks[slot - 1], True), f"slot {slot}")
        # Wrapping: one more Right goes back to Stock, one more to slot 1.
        self.assertEqual(machine.press(KEY_RIGHT_PRESS), 0)
        self.assertEqual(machine.bss_u32(BSS_IDX), 0)
        self.assertEqual(machine.press(KEY_RIGHT_PRESS), 0)
        self.assertEqual(machine.bss_u32(BSS_IDX), 1)


if __name__ == "__main__":
    unittest.main()
