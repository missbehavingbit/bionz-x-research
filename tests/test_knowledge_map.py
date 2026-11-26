"""Knowledge-map sources validate against docs/evidence and generated pages are current.

Offline and standard-library only (runs in the guardrail job).
"""
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from tools.research import knowledge_map as km

ROOT = Path(__file__).resolve().parents[1]


class RepositoryMapTest(unittest.TestCase):
    def test_sources_valid_and_outputs_current(self):
        stale = km.stale_outputs(ROOT)
        self.assertEqual([], [p.relative_to(ROOT).as_posix() for p in stale],
                         "run: python -m tools.research.knowledge_map build")

    def test_query_reports_retraction(self):
        text = km.query("E0379", ROOT)
        self.assertIn("RETRACTED", text)
        self.assertIn("superseded by", text)

    def test_every_evidence_file_belongs_to_an_area(self):
        areas = km.load_areas(ROOT / km.SOURCES_DIR_REL)
        self.assertEqual([], km.unassigned_records(ROOT, areas))

    def test_generated_page_links_resolve(self):
        from tools.research.ci_check import iter_relative_markdown_links

        broken = []
        for page in (ROOT / km.MAP_DIR_REL).glob("*.md"):
            for link in iter_relative_markdown_links(page.read_text(encoding="utf-8")):
                target = link.split("#", 1)[0]
                if target and not (page.parent / target).resolve().exists():
                    broken.append(f"{page.name}: {link}")
        self.assertEqual([], broken)

    def test_query_by_address(self):
        self.assertIn("__dwc3_ep0_do_control_status", km.query("0x602b253c", ROOT))


class EvidenceResolutionTest(unittest.TestCase):
    CATALOG = {
        "E0386": ["0386-retraction-probe.md", "0386-runb16-depcmd.md"],
        "E0468": ["0468-runb25.md"],
    }

    def test_unique(self):
        self.assertEqual("0468-runb25.md", km.resolve_evidence("E0468", self.CATALOG))

    def test_ambiguous_requires_qualifier(self):
        with self.assertRaisesRegex(LookupError, "ambiguous"):
            km.resolve_evidence("E0386", self.CATALOG)
        self.assertEqual("0386-runb16-depcmd.md",
                         km.resolve_evidence("E0386:runb16", self.CATALOG))

    def test_missing_and_malformed(self):
        with self.assertRaises(LookupError):
            km.resolve_evidence("E9999", self.CATALOG)
        with self.assertRaises(LookupError):
            km.resolve_evidence("0468", self.CATALOG)


class ValidationTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kmap-"))
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / "docs" / "evidence").mkdir(parents=True)
        (self.root / "docs" / "map" / "sources").mkdir(parents=True)
        for name in ("0001-first.md", "0002-second.md"):
            (self.root / "docs" / "evidence" / name).write_text("# x\n", encoding="utf-8")

    def write_source(self, body: str) -> None:
        head = textwrap.dedent("""\
            [meta]
            area = "demo"
            title = "Demo"
            scope = "demo scope"
            evidence_span = "E0001-E0002"
            reviewed = "2026-11-22"
            records = ["E0001-E0002"]

            [[entity]]
            id = "fn.a"
            kind = "function"
            name = "a"
            summary = "does a"
            """)
        (self.root / "docs" / "map" / "sources" / "demo.toml").write_text(
            head + textwrap.dedent(body), encoding="utf-8")

    def problems(self) -> list[str]:
        try:
            km.generate(self.root)
        except km.MapError as exc:
            return exc.problems
        return []

    def test_valid_source_generates(self):
        self.write_source("""
            [[claim]]
            id = "C1"
            entities = ["fn.a"]
            status = "verified-offline"
            text = "a returns 0"
            evidence = ["E0002"]

            [[claim]]
            id = "R1"
            entities = ["fn.a"]
            status = "retracted"
            text = "a returns 1"
            evidence = ["E0001"]
            superseded_by = "C1"
            """)
        outputs = km.generate(self.root)
        page = outputs[self.root / "docs" / "map" / "demo.md"]
        self.assertIn("Do not rely on", page)
        self.assertIn("~~a returns 1~~", page)
        self.assertIn("../evidence/0002-second.md", page)

    def test_unknown_evidence_and_entity(self):
        self.write_source("""
            [[claim]]
            id = "C1"
            entities = ["fn.a", "fn.missing"]
            status = "verified-offline"
            text = "x"
            evidence = ["E0003"]
            """)
        joined = "\n".join(self.problems())
        self.assertIn("unknown entity 'fn.missing'", joined)
        self.assertIn("E0003", joined)

    def test_supersession_rules(self):
        self.write_source("""
            [[claim]]
            id = "C1"
            entities = ["fn.a"]
            status = "inference"
            text = "x"
            evidence = ["E0001"]
            superseded_by = "C2"

            [[claim]]
            id = "C2"
            entities = ["fn.a"]
            status = "refuted"
            text = "y"
            evidence = ["E0002"]
            """)
        joined = "\n".join(self.problems())
        self.assertIn("C1 is inference but has superseded_by", joined)
        self.assertIn("itself inactive", joined)

    def test_unassigned_evidence_file_is_reported(self):
        (self.root / "docs" / "evidence" / "0003-third.md").write_text("# E0003 - Third\n", encoding="utf-8")
        self.write_source("""
            [[claim]]
            id = "C1"
            entities = ["fn.a"]
            status = "verified-offline"
            text = "x"
            evidence = ["E0001"]
            """)
        self.assertIn("evidence file 0003-third.md belongs to no map area", "\n".join(self.problems()))

    def test_record_index_is_extracted_from_files(self):
        (self.root / "docs" / "evidence" / "0002-second.md").write_text(
            "# E0002 - Second title\nDate: 2026-11-01\nThis corrects the E0001 reading.\n", encoding="utf-8")
        self.write_source("""
            [[claim]]
            id = "C1"
            entities = ["fn.a"]
            status = "verified-offline"
            text = "x"
            evidence = ["E0001"]
            """)
        page = km.generate(self.root)[self.root / "docs" / "map" / "demo.md"]
        self.assertIn("## Record index (2 files)", page)
        self.assertIn("| [E0002](../evidence/0002-second.md) | 2026-11-01 | Second title |  | This corrects the E0001 reading. |", page)

    def test_deep_reviewed_must_be_inside_records(self):
        source = (self.root / "docs" / "map" / "sources" / "demo.toml")
        self.write_source("""
            [[claim]]
            id = "C1"
            entities = ["fn.a"]
            status = "verified-offline"
            text = "x"
            evidence = ["E0001"]
            """)
        text = source.read_text(encoding="utf-8").replace(
            'records = ["E0001-E0002"]', 'records = ["E0001"]\ndeep_reviewed = ["E0002"]')
        source.write_text(text, encoding="utf-8")
        (self.root / "docs" / "map" / "sources" / "other.toml").write_text(
            '[meta]\narea = "other"\ntitle = "Other"\nscope = "s"\nevidence_span = "E0002"\n'
            'reviewed = "2026-11-22"\nrecords = ["E0002"]\n', encoding="utf-8")
        self.assertIn("deep_reviewed names 0002-second.md, which is not in this area's records",
                      "\n".join(self.problems()))

    def test_todo_lists_records_waiting_for_review(self):
        self.write_source("""
            [[claim]]
            id = "C1"
            entities = ["fn.a"]
            status = "verified-offline"
            text = "x"
            evidence = ["E0001"]
            """)
        source = self.root / "docs" / "map" / "sources" / "demo.toml"
        source.write_text(source.read_text(encoding="utf-8").replace(
            'records = ["E0001-E0002"]', 'records = ["E0001-E0002"]\ndeep_reviewed = ["E0001"]'), encoding="utf-8")
        report = km.todo("demo", self.root)
        self.assertIn("[demo] 1/2 deep-reviewed; 1 waiting", report)
        self.assertIn("E0002", report)
        self.assertNotIn("  E0001", report)

    def test_entity_without_claim_and_bad_status(self):
        self.write_source("""
            [[claim]]
            id = "C1"
            entities = ["fn.a"]
            status = "probably"
            text = "x"
            evidence = ["E0001"]

            [[entity]]
            id = "fn.b"
            kind = "function"
            name = "b"
            summary = "does b"
            """)
        joined = "\n".join(self.problems())
        self.assertIn("unknown status 'probably'", joined)
        self.assertIn("entity fn.b has no claim", joined)


if __name__ == "__main__":
    unittest.main()
