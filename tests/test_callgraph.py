"""Contract tests for building a direct-call graph over a packaged library.

The camera's libraries are unstripped, so a direct call can be resolved to a name. That turns
`libObj.so` from 20 MB of bytes into a map of who calls what — the thing needed to follow a
capability instead of guessing at it.

Only direct calls are followed. An indirect call through a register is counted as unresolved
rather than invented: a guessed edge would make a path look traced when it is not.

Offline only.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "research"))

from callgraph import build_callgraph, callers_of, direct_calls  # noqa: E402


class DirectCallTests(unittest.TestCase):
    def test_a_real_thumb_blx_is_recorded_as_an_edge(self) -> None:
        # Bytes taken from the a6400 libupdatertalk.so: `blx #0x59c` at 0x5da. This is a real
        # ARM Thumb-2 BLX, including its H flag and word-aligned PC, rather than a guessed
        # encoding; an earlier attempt at a hand-built encoding tested nothing.
        data = bytes.fromhex("fff7e0ef")
        edges = direct_calls(data, 0x5da)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0], (0x5da, 0x59C))

    def test_a_non_branch_instruction_produces_no_edge(self) -> None:
        data = bytes.fromhex("00bf")         # nop
        self.assertEqual(direct_calls(data, 0x1000), [])

    @staticmethod
    def _encode_bl(address: int, target: int) -> bytes:
        offset = target - (address + 4)
        sign = (offset >> 24) & 1
        i1, i2 = (offset >> 23) & 1, (offset >> 22) & 1
        j1, j2 = (1 - i1) ^ sign, (1 - i2) ^ sign
        first = 0xF000 | (sign << 10) | ((offset >> 12) & 0x3FF)
        second = 0xD000 | (j1 << 13) | (j2 << 11) | ((offset >> 1) & 0x7FF)
        return first.to_bytes(2, "little") + second.to_bytes(2, "little")

    def test_a_bl_at_a_halfword_address_is_not_word_aligned(self) -> None:
        # Only blx aligns its PC. Aligning for bl as well put 156,131 of 555,069 libObj.so
        # targets two bytes low. Checked against capstone, which decodes the same bytes.
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
        data = bytes.fromhex("00bf") + self._encode_bl(0x1002, 0x2002)
        decoded = list(md.disasm(data[2:], 0x1002))[0]
        self.assertEqual(decoded.mnemonic, "bl")
        self.assertEqual(int(decoded.op_str.lstrip("#"), 16), 0x2002)
        # A bl stays in Thumb state, so its target is reported with bit 0 set, as an ELF
        # symbol for a Thumb function is.
        self.assertEqual(direct_calls(data, 0x1000), [(0x1002, 0x2003)])

    def test_the_second_half_of_an_instruction_is_not_a_call(self) -> None:
        # A 32-bit instruction whose second halfword looks like the start of a bl must not
        # produce an edge: the stream is walked by instruction length, not by every offset.
        data = bytes.fromhex("4ff00000") + self._encode_bl(0x1004, 0x1100)   # mov.w r0,#0 ; bl
        self.assertEqual(direct_calls(data, 0x1000), [(0x1004, 0x1101)])
        self.assertEqual(direct_calls(bytes(1) + data, 0x0fff), [],
                         "an odd start cannot hold a Thumb instruction")

    def test_a_target_outside_every_code_range_is_dropped(self) -> None:
        data = self._encode_bl(0x1000, 0x2000)
        self.assertEqual(direct_calls(data, 0x1000, [(0x1000, 0x1004)]), [])
        self.assertEqual(direct_calls(data, 0x1000, [(0x1000, 0x3000)]), [(0x1000, 0x2001)])


class GraphTests(unittest.TestCase):
    def test_callers_of_a_known_name_are_listed(self) -> None:
        graph = {"nodes": {"a": 0x1000, "b": 0x2000}, "edges": [("a", "b")], "unresolved": 0}
        self.assertEqual(callers_of(graph, "b"), ["a"])

    def test_callers_of_an_unvisited_name_is_empty(self) -> None:
        graph = {"nodes": {"a": 0x1000}, "edges": [], "unresolved": 0}
        self.assertEqual(callers_of(graph, "zzz"), [])

    def test_an_address_without_a_symbol_becomes_a_sub_name(self) -> None:
        # Exercises the naming rule without needing a real ELF: an unresolved callee address is
        # named "sub_XXXXXXXX" so paths stay printable.
        from callgraph import _name_for
        self.assertEqual(_name_for(0x1234, {}), "sub_00001234")
        self.assertEqual(_name_for(0x1234, {0x1234: "known"}), "known")


class RealLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        import os
        raw = os.environ.get("A6400_UNPACKED_ROOT")
        if not raw:
            self.skipTest("A6400_UNPACKED_ROOT not set; no unpacked body available")
        self.hits = sorted(Path(raw).glob(
            "*/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/lib/libObj.so"))
        if not self.hits:
            self.skipTest("no libObj.so under A6400_UNPACKED_ROOT")
        self.lib = self.hits[0]

    def test_graph_of_a_real_library_resolves_most_edges(self) -> None:
        graph = build_callgraph(self.lib)
        # libObj.so defines 1,852 code symbols inside .text; the other 1,444 of its 3,297
        # FUNC symbols are imports with address 0, which are reported separately.
        self.assertGreater(len(graph["nodes"]), 1800)
        self.assertGreater(len(graph["imports"]), 1000)
        self.assertGreater(len(graph["edges"]), 10000)
        # These libraries carry `.dynsym` only, so local functions are unnamed and most edges
        # land on `sub_XXXXXXXX`. That is a property of the shipped files, not a defect: what
        # matters is that the named subset exists and is usable.
        self.assertGreater(graph["named_edges"], 0,
                           "some edges must resolve to real symbol names")
        # Defined functions are exported entry points, so calls *to* them from inside the
        # library are rare; a large count would mean callee matching has gone loose again.
        self.assertGreater(graph["named_local_callees"], 0)
        self.assertLess(graph["named_local_callees"], 1000)

    def test_every_plt_relocation_names_exactly_one_entry(self) -> None:
        """PLT entries are 12 or 16 bytes (a Thumb `bx pc; nop` prefix), not a fixed 12.

        The fixed stride mislabelled every import after the first prefixed entry, so E0483's
        "IPC" call sites were calls to other imports (E0663).
        """
        from elftools.elf.elffile import ELFFile

        from callgraph import _plt_symbol_names
        names = _plt_symbol_names(self.lib)
        arm_entries = {address: name for address, name in names.items() if not address & 1}
        with open(self.lib, "rb") as handle:
            count = ELFFile(handle).get_section_by_name(".rel.plt").num_relocations()
        self.assertEqual(len(arm_entries), count)
        self.assertGreater(len(names) - len(arm_entries), 0, "no Thumb-prefixed entries seen")

    def test_a_named_caller_must_own_the_address_it_is_given(self) -> None:
        """The rule that was wrong once: a caller name requires size-bounded containment.

        These libraries export ~1,853 functions covering under 1% of .text, so bisecting to the
        nearest symbol start names 99.1% of call sites wrongly. This asserts the honest figure.
        """
        graph = build_callgraph(self.lib)
        named = set(graph["nodes"].values())
        named_callers = sum(1 for caller, _ in graph["edges"] if caller in named)
        total = len(graph["edges"])
        self.assertLess(named_callers / total, 0.05,
                        "more call sites carry a name than the symbol sizes can justify")

    def test_callers_of_a_real_import_are_found_on_more_than_one_body(self) -> None:
        # Not every body's libObj.so calls osal_snd_msg directly (the a6100a does not), so this
        # checks across the available bodies instead of assuming the first one by name is
        # representative — the assumption that made an earlier version of this test fail.
        found = {}
        for lib in self.hits:
            graph = build_callgraph(lib)
            if callers_of(graph, "osal_snd_msg"):
                found[lib.parts[-9]] = len(callers_of(graph, "osal_snd_msg"))
        self.assertTrue(found, "no body's libObj.so calls osal_snd_msg, so the IPC work is moot")


if __name__ == "__main__":
    unittest.main()
