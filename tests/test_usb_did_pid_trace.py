"""Offline checks for tools.research.usb_did_pid_trace on constructed fixtures.

No Sony bytes are embedded: the BK4 preset and the DID table are built here, so
the tests exercise the lookup contract, not any body's real values.
"""
import hashlib
import struct
import unittest

from project_paths import PROJECT_ROOT
from src.a6400.execution.backup_service import BK4PresetState
from tools.research.usb_did_pid_trace import (
    NO_UNIT, DidRecord, decode_did_records, elf_did_records, is_bcd, overwrite_decision,
    resolve_unit, trace,
)


def bk4(units):
    """Build a checksummed BK4 image. ``units`` maps (subsystem, prop) -> bytes."""
    subsystems = max(s for s, _ in units) + 1
    per = {s: max(p for t, p in units if t == s) + 1 for s in {s for s, _ in units}}
    properties = sum(per.values())
    data_offset = 0x100 + 6 * subsystems + 6 * properties
    raw = bytearray(data_offset)
    raw[12:16] = b"BK4\0"
    payload = bytearray()
    base = 0
    for s in range(subsystems):
        count = per.get(s, 0)
        struct.pack_into("<HI", raw, 0x100 + 6 * s, count, base)
        for p in range(count):
            value = units.get((s, p))
            entry = 0x100 + 6 * subsystems + 6 * (base + p)
            if value is None:
                struct.pack_into("<HI", raw, entry, 0, NO_UNIT)
                continue
            # flag byte = size, offset = absolute payload position (original encoding)
            struct.pack_into("<HI", raw, entry, 0, (len(value) << 24) | (data_offset + len(payload)))
            payload += value
        base += count
    raw += payload
    struct.pack_into("<4I", raw, 0x10, subsystems, properties, data_offset, len(payload))
    checksum = (sum(raw[:0x20]) + sum(raw[0x24:])) & 0xFFFFFFFF
    struct.pack_into("<I", raw, 0x20, checksum)
    return BK4PresetState(bytes(raw))


class FakeMemory:
    def __init__(self, words):
        self.words = words

    def read(self, va, count):
        return tuple(self.words[va + 4 * i] for i in range(count))


def record(did, pid_unit, aux_unit):
    return [did, 0, 0, 0, 0, 0, pid_unit, aux_unit]


class DecodeTests(unittest.TestCase):
    def memory(self, table, records):
        words = {}
        for i, va in enumerate(table):
            words[0x1000 + 4 * i] = va
        for va, rec in records.items():
            for i, w in enumerate(rec):
                words[va + 4 * i] = w
        return FakeMemory(words)

    def test_null_entries_are_skipped_and_units_are_read_from_words_6_and_7(self):
        mem = self.memory([0x2000, 0, 0x3000], {0x2000: record(0, 0x00010002, 0x00010000),
                                               0x3000: record(2, NO_UNIT, NO_UNIT)})
        got = decode_did_records(mem.read, 0x1000, 3)
        self.assertEqual(got, [DidRecord(0, 0x2000, 0x00010002, 0x00010000),
                               DidRecord(2, 0x3000, NO_UNIT, NO_UNIT)])

    def test_record_that_does_not_name_its_own_did_fails_closed(self):
        mem = self.memory([0x2000], {0x2000: record(7, 1, 1)})
        with self.assertRaisesRegex(ValueError, "layout not recognised"):
            decode_did_records(mem.read, 0x1000, 1)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.state = bk4({(1, 0): b"\x00\x01", (1, 2): b"\x34\x12", (1, 3): b"DSC\0"})

    def test_two_byte_unit_resolves_to_a_pid_value(self):
        row = resolve_unit(self.state, 0x00010002)
        self.assertEqual((row["status"], row["value"]), ("resolved", "0x1234"))

    def test_no_override_missing_and_non_16_bit_units_are_reported_not_guessed(self):
        self.assertEqual(resolve_unit(self.state, NO_UNIT)["status"], "no_override")
        self.assertEqual(resolve_unit(self.state, 0x00010001)["status"], "not_in_preset")
        self.assertEqual(resolve_unit(self.state, 0x00050000)["status"], "not_in_preset")
        row = resolve_unit(self.state, 0x00010003)
        self.assertEqual(row["status"], "not_a_16_bit_unit")
        self.assertNotIn("value", row)

    def test_trace_pairs_each_did_with_its_pid_and_aux_units(self):
        rows = trace([DidRecord(0x3B, 0x2000, 0x00010002, 0x00010000)], self.state)
        self.assertEqual(rows[0]["did"], "0x3b")
        self.assertEqual(rows[0]["pid"]["value"], "0x1234")
        self.assertEqual(rows[0]["aux"]["value"], "0x0100")
        self.assertTrue(rows[0]["would_overwrite"])


class OriginalGateTests(unittest.TestCase):
    """Replays of DescriptorMaintainer isDefaultAddress/readPresetData/chkPresetData/isBCD."""

    @staticmethod
    def row(value):
        return {"status": "resolved", "value": f"{value:#06x}"}

    def test_is_bcd_checks_only_the_low_three_nibbles_like_the_original(self):
        for value in (0x0100, 0x0200, 0x0999, 0xF999):
            self.assertTrue(is_bcd(value), hex(value))
        for value in (0x0D9F, 0x000A, 0x00A0, 0x0A00):
            self.assertFalse(is_bcd(value), hex(value))

    def test_overwrite_needs_both_units_a_nonzero_pid_and_a_bcd_second_value(self):
        self.assertEqual(overwrite_decision(self.row(0x0D9F), self.row(0x0200))[0], True)
        cases = {
            "isDefaultAddress": ({"status": "no_override", "unit": None}, self.row(0x0100)),
            "readPresetData": (self.row(0x0D9F), {"status": "not_a_16_bit_unit"}),
            "first value is zero": (self.row(0), self.row(0x0100)),
            "not BCD": (self.row(0x0D9F), self.row(0x0D9F)),
        }
        for reason, (pid, aux) in cases.items():
            applied, why = overwrite_decision(pid, aux)
            with self.subTest(reason=reason):
                self.assertFalse(applied)
                self.assertIn(reason, why)


EXTRACT = PROJECT_ROOT / "firmware" / "extracted_ilce7rm4a_v113" / "firmware.tar_unpacked"
LOCAL_INPUTS = {
    "lib": (EXTRACT / "0700_part_image/dev/nflasha15_unpacked/lib/libInfraUsbCmn.so",
            "96638679c31ca8809fae2005fb992653629f040acad72280de10b2b1dcf8c246"),
    "cx88900": (EXTRACT / "0110_backup/SYSASTRA-DSLR/MGA/CX88900_UC2.bin",
                "7109822609da4ccddb139b67251373d364bb5b2399b0020f70b694f7971ae736"),
    "cx88901": (EXTRACT / "0110_backup/SYSASTRA-DSLR/MGA/CX88901_AP2.bin",
                "311b99686f728209ad55701cb12ab553accadc848c24cca95d820597de166e03"),
}


class LocalIlce7rm4aV113Tests(unittest.TestCase):
    """E0678 values on the owner's local 1.13 extraction; skipped without identical inputs."""

    @classmethod
    def setUpClass(cls):
        cls.raw = {}
        for name, (path, sha) in LOCAL_INPUTS.items():
            if not path.is_file():
                raise unittest.SkipTest(f"Local ILCE-7RM4A 1.13 extraction not present: {path}")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != sha:
                raise unittest.SkipTest(f"Local input differs from the E0678 identity: {path}")
            cls.raw[name] = data

    def table(self, preset):
        rows = trace(elf_did_records(self.raw["lib"]), BK4PresetState(self.raw[preset]))
        return {r["did"]: r for r in rows}

    def test_pc_remote_and_mass_storage_pids_resolve_and_pass_the_original_gates(self):
        for preset, bcd in (("cx88900", "0x0100"), ("cx88901", "0x0200")):
            rows = self.table(preset)
            with self.subTest(preset=preset):
                self.assertEqual(sorted(rows), ["0x00", "0x0b", "0x0c", "0x0d", "0x37",
                                                "0x38", "0x39", "0x3a", "0x3b"])
                self.assertEqual(rows["0x00"]["pid"]["value"], "0x0d9d")
                self.assertEqual(rows["0x3b"]["pid"]["value"], "0x0d9f")
                self.assertEqual(rows["0x3b"]["pid"]["unit"], "0x00e70035")
                self.assertEqual(rows["0x3b"]["aux"]["value"], bcd)
                self.assertTrue(rows["0x3b"]["would_overwrite"])
                for did in ("0x0d", "0x38", "0x39", "0x3a"):
                    self.assertFalse(rows[did]["would_overwrite"])


if __name__ == "__main__":
    unittest.main()
