"""Offline verification of the portable BIONZ Look Lab `SMOD v2` `.mod` export.

The portable app emits `.mod` files in pure Rust (`bionz-preset::smod_v2`) from a committed ARM
Thumb template. This suite checks the result end to end without a camera:

1. The CLI's `smod` subcommand exports a `.mod` from each shipped `.bzlook`.
2. `src.a6400.mod_manager.smod.parse_smod` - the same strict parser the Mod Manager uses - accepts
   every file, and the header/checksum are the loader's SMOD v2 shape.
3. The payload carries the preset's own tables and the body's published physical addresses, read
   back independently from the `.bzlook` (a zip + BZLK decode, not the Rust packer).
4. Unicorn runs the emitted lifecycle code the way Mod Loader v2 does (code at an arbitrary base,
   `/dev/mem` `mmap2` serviced from emulated single pages) and `sim_life(1)`/`sim_life(0)` must swap
   and restore EC, FC, A0, A1, B2, RefAdr[0] and the CC tuning word, leave every other byte alone,
   and report `w` for each table in the mod log.

Run from the repository root:
    .\\venv\\Scripts\\python.exe -m unittest tests.test_look_lab_smod_v2 -v
"""
from __future__ import annotations

import io
import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.a6400.mod_manager.smod import SMOD_MAGIC, SMOD_V2_FLAG_ACTION, parse_smod

ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "isp-studio"
PRESETS = ROOT / "mods" / "film_sims" / "presets"

try:
    import unicorn as uc
    from unicorn import arm_const as A
except ModuleNotFoundError:  # the pure-format checks still run without Unicorn
    uc = None
    A = None

# Published per-body physical addresses (bionz-preset::known_target). Restated here on purpose so a
# wrong table cannot agree with itself.
KNOWN_TARGETS = {
    "a6400": {
        "ec_gamma_pa": 0x03E55330,
        "fc_gamma_pa": 0x03E8F154,
        "color_a0_pa": 0x0444A244,
        "color_a1_pa": 0x0444A256,
        "color_b2_pa": 0x0444B168,
        "cc_tuning_pa": 0x0444A19E,
        "refadr0_pa": 0x044ACD27,
    },
    "a6100": {
        "ec_gamma_pa": 0x03E553B0,
        "fc_gamma_pa": 0x03E8F1D4,
        "color_a0_pa": 0x0444A254,
        "color_a1_pa": 0x0444A266,
        "color_b2_pa": 0x0444B178,
        "cc_tuning_pa": 0x0444A1AE,
        "refadr0_pa": 0x044ACD37,
    },
}
PRESET_NAMES = {
    "classic-chrome": "Classic Chrome",
    "gold-200": "Gold 200",
    "eterna-bleach-bypass": "Eterna Bleach Bypass",
}
SHIPPED = sorted(p.stem for p in PRESETS.glob("*.bzlook"))

PAGE = 0x1000
MOD_BASE = 0x40AB3000
STACK = 0x20000000
EMULATED_MEM = 0x30000000
STOP = 0x31000000


def fnv1a_32(data: bytes) -> int:
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


class _Cursor:
    def __init__(self, data: bytes):
        self.data, self.offset = data, 0

    def take(self, n: int) -> bytes:
        value = self.data[self.offset:self.offset + n]
        if len(value) != n:
            raise AssertionError("BZLK table truncated")
        self.offset += n
        return value

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def u32s(self, n: int) -> list[int]:
        return list(struct.unpack(f"<{n}I", self.take(4 * n)))

    def u16s(self, n: int) -> list[int]:
        return list(struct.unpack(f"<{n}H", self.take(2 * n)))

    def i16s(self, n: int) -> list[int]:
        return list(struct.unpack(f"<{n}h", self.take(2 * n)))


def read_bzlk(blob: bytes) -> dict:
    """Decode the BZLK table container (v1/v2) exactly as `bionz-preset::decode_compiled` does."""
    c = _Cursor(blob)
    assert c.take(4) == b"BZLK", "not a BZLK table"
    c.u16()  # container version; the v2 fields are read by position, not by this number
    c.take(32)  # target key
    sha = c.take(32)
    flags = c.u32()
    lut_level = c.u16()
    table = {
        "ec": c.u32s(96),
        "fc": c.u16s(96),
        "a0": c.i16s(9),
        "a1": c.i16s(9),
        "b2": c.i16s(9),
        "refadr": c.take(1215),
        "stock_ec": c.u32s(96),
        "stock_fc": c.u16s(96),
        "stock_a0": c.i16s(9),
        "stock_a1": c.i16s(9),
        "stock_b2": c.i16s(9),
    }
    return {"table": table, "avcam_sha256": sha, "flags": flags, "lut_level": lut_level}


def preset_table(stem: str, target: str) -> dict:
    """The `.bzlook`'s compiled table for one body, decoded here rather than by the Rust code."""
    with zipfile.ZipFile(io.BytesIO((PRESETS / f"{stem}.bzlook").read_bytes())) as zf:
        return read_bzlk(zf.read(f"compiled/{target}.bin"))


def pack_u32(values) -> bytes:
    return struct.pack(f"<{len(values)}I", *values)


def pack_u16(values) -> bytes:
    return struct.pack(f"<{len(values)}H", *values)


def pack_i16(values) -> bytes:
    return struct.pack(f"<{len(values)}h", *values)


# --------------------------------------------------------------------------------------------------
# Rust CLI


def cargo() -> str | None:
    for name in ("cargo", "cargo.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def ensure_cli() -> Path | None:
    """Build `bionz-cli` (no GPU feature) so the exported `.mod` uses the committed template."""
    exe = STUDIO / "target" / "debug" / ("bionz-cli.exe" if os.name == "nt" else "bionz-cli")
    tool = cargo()
    if tool is None:
        return exe if exe.is_file() else None
    # Keep the linker's temporary files inside the checkout: some sandboxes allow writes only under
    # the workspace and the system temp folder then fails with LNK1104.
    tmp = STUDIO / "target" / "test-tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TMP": str(tmp), "TEMP": str(tmp), "TMPDIR": str(tmp)}
    built = subprocess.run(
        [tool, "build", "--quiet", "-p", "bionz-cli", "--no-default-features"],
        cwd=STUDIO, capture_output=True, text=True, timeout=3600, env=env,
    )
    if built.returncode != 0:
        raise unittest.SkipTest(f"cargo build -p bionz-cli failed: {built.stderr.strip()[-2000:]}")
    return exe if exe.is_file() else None


class ExportFixture:
    """Exports `.mod` files through the CLI into a temporary folder, once per session."""

    def __init__(self) -> None:
        self.workdir = Path(tempfile.mkdtemp(prefix="look-lab-smod-"))
        self.cli = ensure_cli()
        self.cache: dict[tuple[str, str], bytes] = {}

    def close(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def mod(self, stem: str, target: str) -> bytes:
        key = (stem, target)
        if key in self.cache:
            return self.cache[key]
        if self.cli is None:
            raise unittest.SkipTest("bionz-cli is not built and cargo is not available")
        out = self.workdir / f"{stem}-{target}.mod"
        done = subprocess.run(
            [str(self.cli), "smod", str(PRESETS / f"{stem}.bzlook"), "--target", target, "--output", str(out)],
            cwd=ROOT, capture_output=True, text=True, timeout=600,
        )
        if done.returncode != 0:
            raise AssertionError(f"bionz-cli smod {stem} {target} failed: {done.stderr.strip()}")
        info = json.loads(done.stdout)
        assert info["target"] == target and info["bytes"] == out.stat().st_size, info
        blob = out.read_bytes()
        code_len = struct.unpack_from("<I", blob, 44)[0]
        if code_len != TEMPLATE["code_len"]:
            raise AssertionError(
                f"the built bionz-cli embeds a stale template ({code_len} code bytes, the committed record says "
                f"{TEMPLATE['code_len']}); rebuild it with `cargo build -p bionz-cli --no-default-features`"
            )
        self.cache[key] = blob
        return blob


FIXTURE: ExportFixture | None = None


def setUpModule() -> None:
    global FIXTURE
    FIXTURE = ExportFixture()


def tearDownModule() -> None:
    if FIXTURE is not None:
        FIXTURE.close()


def fixture() -> ExportFixture:
    assert FIXTURE is not None
    return FIXTURE


def payload_field(mod: bytes, name: str, record: dict) -> int:
    return 68 + record["payload"]["offset"] + record["payload"]["fields"][name]


def payload_bytes(mod: bytes, name: str, width: int, record: dict) -> bytes:
    at = payload_field(mod, name, record)
    return mod[at:at + width]


# --------------------------------------------------------------------------------------------------
# Unicorn: run the emitted lifecycle the way Mod Loader v2 does


class Machine:
    """A Thumb code page at an arbitrary base plus single-page `/dev/mem` mappings by physical page."""

    def __init__(self, code: bytes, entry: int):
        self.uc = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
        size = (len(code) + 0x2000 + PAGE - 1) & ~(PAGE - 1)
        self.uc.mem_map(MOD_BASE, size)
        self.uc.mem_write(MOD_BASE, code)
        self.uc.mem_map(STACK, 0x10000)
        self.uc.mem_map(STOP, PAGE)
        self.entry = entry
        self.pages: dict[int, int] = {}
        self.next_host = EMULATED_MEM
        self.log: list[str] = []
        self.log_path: str | None = None
        self.uc.hook_add(uc.UC_HOOK_INTR, self._intr)

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

    def snapshot(self) -> dict[int, bytes]:
        return {pa: bytes(self.uc.mem_read(host, PAGE)) for pa, host in self.pages.items()}

    def _intr(self, mu, _intno, _user) -> None:
        nr = mu.reg_read(A.UC_ARM_REG_R7)
        a = [mu.reg_read(r) for r in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2,
                                      A.UC_ARM_REG_R3, A.UC_ARM_REG_R4, A.UC_ARM_REG_R5)]
        ret = 0
        if nr == 5:  # open
            path = bytes(mu.mem_read(a[0], 96)).split(b"\0")[0].decode()
            if path == "/dev/mem":
                ret = 3
            elif path.startswith("/tmp_bt/mods/") and path.endswith(".log"):
                self.log_path = path
                ret = 4
            else:
                raise AssertionError(f"unexpected open({path!r})")
        elif nr == 192:  # mmap2(addr=0, len=4096, prot=3, flags=1, fd, pgoff)
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

    def run(self, on: int) -> None:
        self.uc.reg_write(A.UC_ARM_REG_R0, on)
        self.uc.reg_write(A.UC_ARM_REG_SP, STACK + 0xF000)
        self.uc.reg_write(A.UC_ARM_REG_LR, STOP | 1)
        self.uc.emu_start((MOD_BASE + (self.entry & ~1)) | 1, STOP, count=4_000_000)


def splice(pages: dict[int, bytes], pa: int, data: bytes) -> None:
    while data:
        off = pa & 0xFFF
        take = min(len(data), PAGE - off)
        page = bytearray(pages[pa >> 12])
        page[off:off + take] = data[:take]
        pages[pa >> 12] = bytes(page)
        pa += take
        data = data[take:]


def seeded(mod: bytes, addrs: dict, table: dict) -> Machine:
    """A machine with the body's stock tables in emulated `/dev/mem` pages."""
    record = TEMPLATE
    entry = int(record["on_off"], 16)
    code = mod[68:68 + struct.unpack_from("<I", mod, 44)[0]]
    machine = Machine(code, entry)
    machine.poke(addrs["ec_gamma_pa"], pack_u32(table["stock_ec"]))
    machine.poke(addrs["fc_gamma_pa"], pack_u16(table["stock_fc"]))
    machine.poke(addrs["color_a0_pa"], pack_i16(table["stock_a0"]))
    machine.poke(addrs["color_a1_pa"], pack_i16(table["stock_a1"]))
    machine.poke(addrs["color_b2_pa"], pack_i16(table["stock_b2"]))
    machine.poke(addrs["cc_tuning_pa"], b"\xff\xff")
    machine.poke(addrs["refadr0_pa"], b"\x00" * 1215)
    return machine


def expected_pages(before: dict[int, bytes], addrs: dict, on: bool, table: dict) -> dict[int, bytes]:
    pages = dict(before)
    splice(pages, addrs["ec_gamma_pa"], pack_u32(table["ec"] if on else table["stock_ec"]))
    splice(pages, addrs["fc_gamma_pa"], pack_u16(table["fc"] if on else table["stock_fc"]))
    for name in ("a0", "a1", "b2"):
        splice(pages, addrs[f"color_{name}_pa"], pack_i16(table[name] if on else table[f"stock_{name}"]))
    splice(pages, addrs["cc_tuning_pa"], b"\x00\x00" if on else b"\xff\xff")
    splice(pages, addrs["refadr0_pa"], table["refadr"] if on else b"\x00" * 1215)
    return pages


_TEMPLATE: dict | None = None


def template_record() -> dict:
    global _TEMPLATE
    if _TEMPLATE is None:
        _TEMPLATE = json.loads((STUDIO / "crates" / "bionz-preset" / "assets" / "film_sim_template.json").read_text())
    return _TEMPLATE


TEMPLATE = template_record()


# --------------------------------------------------------------------------------------------------
# Tests


class TestLookLabSmodV2(unittest.TestCase):
    def mod(self, stem: str, target: str) -> bytes:
        return fixture().mod(stem, target)

    def test_parser_accepts_every_shipped_preset_for_both_bodies(self):
        self.assertEqual(len(SHIPPED), 12, f"expected the twelve shipped presets, found {SHIPPED}")
        for stem in SHIPPED:
            for target in KNOWN_TARGETS:
                mod = self.mod(stem, target)
                info = parse_smod(mod, f"{stem}.mod")
                self.assertEqual(info.version, 2)
                self.assertTrue(info.name and info.name.isascii(), info.name)
                self.assertEqual(info.flags, SMOD_V2_FLAG_ACTION)
                self.assertFalse(info.has_reloc)
                self.assertFalse(info.auto_on)
                self.assertTrue(info.lifecycle)
                self.assertEqual(info.hook_count, 0)
                self.assertEqual(info.bss_size, 0)
                self.assertEqual(info.code_size, len(mod) - 68)
                self.assertEqual(info.fnv1a_32, fnv1a_32(mod[68:]))

    def test_payload_carries_the_preset_tables_and_the_body_addresses(self):
        for stem in ("classic-chrome", "gold-200", "eterna-bleach-bypass"):
            for target, addrs in KNOWN_TARGETS.items():
                mod = self.mod(stem, target)
                decoded = preset_table(stem, target)
                table = decoded["table"]
                for field, address in addrs.items():
                    got = struct.unpack("<I", payload_bytes(mod, field, 4, TEMPLATE))[0]
                    self.assertEqual(got, address, f"{stem} {target} {field}")
                self.assertEqual(payload_bytes(mod, "ec_stock", 384, TEMPLATE), pack_u32(table["stock_ec"]))
                self.assertEqual(payload_bytes(mod, "ec_sim", 384, TEMPLATE), pack_u32(table["ec"]))
                self.assertEqual(payload_bytes(mod, "fc_stock", 192, TEMPLATE), pack_u16(table["stock_fc"]))
                self.assertEqual(payload_bytes(mod, "fc_sim", 192, TEMPLATE), pack_u16(table["fc"]))
                for name in ("a0", "a1", "b2"):
                    self.assertEqual(payload_bytes(mod, f"{name}_stock", 18, TEMPLATE), pack_i16(table[f"stock_{name}"]))
                    self.assertEqual(payload_bytes(mod, f"{name}_sim", 18, TEMPLATE), pack_i16(table[name]))
                self.assertEqual(payload_bytes(mod, "refadr_sim", 1215, TEMPLATE), table["refadr"])
                self.assertNotEqual(decoded["avcam_sha256"], b"\0" * 32, f"{stem}: stock is not from av-cam.bin")
                tag = payload_bytes(mod, "mod_tag", 32, TEMPLATE).split(b"\0")[0].decode()
                log = payload_bytes(mod, "mod_log_path", 64, TEMPLATE).split(b"\0")[0].decode()
                self.assertEqual(log, f"/tmp_bt/mods/{tag}.log")

    def test_exported_mods_differ_between_bodies(self):
        for stem in ("classic-chrome", "gold-200"):
            a6400, a6100 = self.mod(stem, "a6400"), self.mod(stem, "a6100")
            self.assertEqual(len(a6400), len(a6100))
            self.assertNotEqual(a6400, a6100, "the two bodies must not share a payload")

    def test_template_is_not_the_exported_code(self):
        record = TEMPLATE
        blob = (STUDIO / "crates" / "bionz-preset" / "assets" / "film_sim_template.blob").read_bytes()
        template_fn = int(record["code_fnv1a_32"], 16)
        self.assertEqual(len(blob), record["code_len"])
        self.assertEqual(fnv1a_32(blob), template_fn)
        shipped = self.mod("classic-chrome", "a6400")
        self.assertNotEqual(fnv1a_32(shipped[68:]), template_fn, "the payload was not written")
        self.assertEqual(struct.unpack_from("<I", shipped, 0)[0], SMOD_MAGIC)


@unittest.skipIf(uc is None, "unicorn is not installed; the ARM Thumb lifecycle check needs it")
class TestLookLabSmodV2Unicorn(unittest.TestCase):
    def check_roundtrip(self, stem: str, target: str, foreign: bool = False) -> None:
        addrs = KNOWN_TARGETS[target]
        table = preset_table(stem, target)["table"]
        machine = seeded(fixture().mod(stem, target), addrs, table)
        if foreign:
            # A table that is neither stock nor simulated must not be overwritten.
            machine.poke(addrs["color_a1_pa"], pack_i16([0x1111] * 9))
            machine.poke(addrs["ec_gamma_pa"], pack_u32([0x22222222] * 96))
        before = machine.snapshot()

        machine.run(1)
        want = expected_pages(before, addrs, True, table)
        if foreign:
            splice(want, addrs["color_a1_pa"], pack_i16([0x1111] * 9))
            splice(want, addrs["ec_gamma_pa"], pack_u32([0x22222222] * 96))
        self.assertEqual(machine.snapshot(), want, f"{stem} {target}: ON did not apply the tables")
        self.assertEqual(machine.uc.reg_read(A.UC_ARM_REG_PC), STOP)

        machine.run(1)
        self.assertEqual(machine.snapshot(), want, f"{stem} {target}: a repeated ON is not idempotent")

        machine.run(0)
        want_off = expected_pages(before, addrs, False, table)
        if foreign:
            splice(want_off, addrs["color_a1_pa"], pack_i16([0x1111] * 9))
            splice(want_off, addrs["ec_gamma_pa"], pack_u32([0x22222222] * 96))
        self.assertEqual(machine.snapshot(), want_off, f"{stem} {target}: OFF did not restore the stock tables")

        self.assertTrue(machine.log_path, "the mod never opened its log")
        self.assertTrue(machine.log_path.startswith("/tmp_bt/mods/"), machine.log_path)
        lines = "".join(machine.log).strip().splitlines()
        self.assertEqual(len(lines), 3, lines)
        tag = machine.log_path.rsplit("/", 1)[-1][:-4]
        if foreign:
            # Foreign EC and A1 content matches neither stock nor the look, so it is left alone and
            # reported as a mismatch; every other table still swaps and restores.
            want = [
                f"{tag} on=1 ec=x fc=w a0=w a1=x b2=w lut3d=w",
                f"{tag} on=1 ec=x fc=a a0=a a1=x b2=a lut3d=w",
                f"{tag} on=0 ec=x fc=w a0=w a1=x b2=w lut3d=w",
            ]
        else:
            want = [
                f"{tag} on=1 ec=w fc=w a0=w a1=w b2=w lut3d=w",
                f"{tag} on=1 ec=a fc=a a0=a a1=a b2=a lut3d=w",
                f"{tag} on=0 ec=w fc=w a0=w a1=w b2=w lut3d=w",
            ]
        self.assertEqual(lines, want)

    def test_classic_chrome_on_both_bodies(self):
        for target in ("a6400", "a6100"):
            self.check_roundtrip("classic-chrome", target)

    def test_every_shipped_preset_swaps_and_restores(self):
        for stem in SHIPPED:
            self.check_roundtrip(stem, "a6400")

    def test_foreign_tables_are_left_alone(self):
        self.check_roundtrip("gold-200", "a6100", foreign=True)


if __name__ == "__main__":
    unittest.main()
