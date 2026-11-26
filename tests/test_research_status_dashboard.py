"""Structural and epistemic checks for the knowledge-map coverage dashboard SVG.

The dashboard is generated, not hand-drawn: these tests parse the committed SVG and
verify that it is well-formed, that no text overlaps or leaves the canvas, that every
number comes from ``tools.research.knowledge_map.compute_coverage``, and that the only
percentages shown are inventory shares stated next to their fraction, never an overall
firmware-compatibility figure.

Standard library only (``xml.etree`` + the generator's own geometry estimator), so the
same checks run in CI.
"""
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from tools.research.a6400_status_data import DAILY_TEST_HISTORY, STATUS_SNAPSHOT
from tools.research.knowledge_map import (
    AreaSummary,
    FrontierItem,
    InventoryCoverage,
    MapCoverage,
    compute_coverage,
)
from tools.research.render_research_status import (
    LEGEND,
    WIDTH,
    estimate_text_width,
    format_share,
    render_svg,
    render_test_growth_svg,
    text_box,
)

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "docs" / "images" / "research-status.svg"
GROWTH_SVG = ROOT / "docs" / "images" / "test-growth.svg"
SVG_NS = "{http://www.w3.org/2000/svg}"
COVERAGE = compute_coverage(ROOT)


def _parse(svg_text=None):
    if svg_text is None:
        svg_text = SVG.read_text(encoding="utf-8")
    return ET.fromstring(svg_text)


def _text_nodes(root):
    nodes = []
    for element in root.iter(f"{SVG_NS}text"):
        family_attr = element.get("font-family", "")
        nodes.append({
            "text": "".join(element.itertext()),
            "x": float(element.get("x")),
            "y": float(element.get("y")),
            "size": float(element.get("font-size")),
            "weight": int(element.get("font-weight", "400")),
            "anchor": element.get("text-anchor", "start"),
            "family": "mono" if "monospace" in family_attr else "sans",
        })
    return nodes


def _texts(svg_text=None):
    return [node["text"] for node in _text_nodes(_parse(svg_text))]


class TestWellFormed(unittest.TestCase):
    def test_parses_as_xml(self):
        for svg_path in (SVG, GROWTH_SVG):
            root = _parse(svg_path.read_text(encoding="utf-8"))
            self.assertEqual(root.tag, f"{SVG_NS}svg", str(svg_path))

    def test_canvas_is_920_wide_and_consistent(self):
        for svg_path in (SVG, GROWTH_SVG):
            root = _parse(svg_path.read_text(encoding="utf-8"))
            width = int(root.get("width"))
            height = int(root.get("height"))
            self.assertEqual(width, WIDTH, str(svg_path))
            self.assertEqual(root.get("viewBox"), f"0 0 {width} {height}", str(svg_path))
            self.assertGreater(height, 500, str(svg_path))

    def test_has_accessible_title_and_description(self):
        root = _parse()
        title = root.find(f"{SVG_NS}title")
        desc = root.find(f"{SVG_NS}desc")
        self.assertIn("Knowledge Map Coverage", title.text)
        self.assertIn(STATUS_SNAPSHOT.latest.evidence_id, title.text)
        self.assertIn("not a firmware compatibility percentage", desc.text.lower())

        growth_root = _parse(GROWTH_SVG.read_text(encoding="utf-8"))
        self.assertIn(f"{STATUS_SNAPSHOT.suite.test_count:,}", growth_root.find(f"{SVG_NS}title").text)
        self.assertIn("not a firmware compatibility percentage",
                      growth_root.find(f"{SVG_NS}desc").text.lower())

    def test_committed_files_are_the_generated_documents(self):
        self.assertEqual(SVG.read_text(encoding="utf-8"), render_svg(COVERAGE))
        self.assertEqual(GROWTH_SVG.read_text(encoding="utf-8"), render_test_growth_svg(DAILY_TEST_HISTORY))


class TestTextLayout(unittest.TestCase):
    def test_no_text_overlaps(self):
        for svg_path in (SVG, GROWTH_SVG):
            nodes = _text_nodes(_parse(svg_path.read_text(encoding="utf-8")))
            self.assertGreater(len(nodes), 30, f"{svg_path} should be text-dense")
            boxes = [(text_box(n["x"], n["y"], n["text"], n["size"], n["family"], n["weight"], n["anchor"]),
                      n["text"]) for n in nodes]
            collisions = []
            for index, (box, text) in enumerate(boxes):
                for other_box, other_text in boxes[index + 1:]:
                    if (box[0] < other_box[2] and box[2] > other_box[0]
                            and box[1] < other_box[3] and box[3] > other_box[1]):
                        collisions.append(f"{text!r} <-> {other_text!r}")
            self.assertEqual(collisions, [], f"{svg_path} overlapping text: " + "; ".join(collisions[:5]))

    def test_all_text_stays_inside_the_canvas(self):
        for svg_path in (SVG, GROWTH_SVG):
            root = _parse(svg_path.read_text(encoding="utf-8"))
            width = int(root.get("width"))
            height = int(root.get("height"))
            for node in _text_nodes(root):
                left, top, right, bottom = text_box(node["x"], node["y"], node["text"], node["size"],
                                                    node["family"], node["weight"], node["anchor"])
                self.assertGreaterEqual(left, 0.0, f"{svg_path}: {node['text']}")
                self.assertLessEqual(right, width, f"{svg_path}: {node['text']}")
                self.assertGreaterEqual(top, 0.0, f"{svg_path}: {node['text']}")
                self.assertLessEqual(bottom, height, f"{svg_path}: {node['text']}")

    def test_width_estimator_is_conservative(self):
        self.assertGreater(estimate_text_width("MMMM", 10), estimate_text_width("iiii", 10) * 0.9)


class TestCoverageDerivation(unittest.TestCase):
    def test_every_inventory_row_is_rendered_with_its_fraction(self):
        joined = " ".join(_texts())
        self.assertTrue(COVERAGE.inventories)
        for row in COVERAGE.inventories:
            self.assertIn(row.title, joined)
            self.assertIn(f"{row.mapped:,} / {row.total:,} {row.unit}", joined)
            self.assertIn(f"{row.verified} verified · {row.decoded} decoded · {row.boundary} frontier", joined)

    def test_pills_state_map_and_suite_counts(self):
        joined = " ".join(_texts())
        self.assertIn(f"{len(COVERAGE.areas)} · {COVERAGE.entities} entities", joined)
        self.assertIn(f"{COVERAGE.active_claims} · {COVERAGE.inactive_claims} retracted", joined)
        self.assertIn(f"{COVERAGE.evidence_indexed} indexed · {COVERAGE.evidence_deep} deep", joined)
        self.assertIn(f"{COVERAGE.evidence_deep} / {COVERAGE.evidence_total}", joined)
        self.assertIn(f"{COVERAGE.evidence_indexed} / {COVERAGE.evidence_total}", joined)
        self.assertIn(f"{STATUS_SNAPSHOT.suite.test_count:,} tests", joined)

    def test_areas_and_frontier_are_listed(self):
        joined = " ".join(_texts())
        for area in COVERAGE.areas:
            self.assertIn(f"{area.deep_reviewed}/{area.records} · {area.active_claims}/{area.inactive_claims}", joined)
        for item in COVERAGE.frontier:
            if item.state != "done":
                self.assertIn(f"{item.state} · {item.area}", joined)

    def test_every_evidence_record_is_indexed(self):
        self.assertEqual(COVERAGE.evidence_indexed, COVERAGE.evidence_total)

    def test_usb_pilot_grades_dwc3_functions_as_verified(self):
        rows = {row.id: row for row in COVERAGE.inventories}
        self.assertEqual(rows["kernel.dwc3"].total, 91)
        self.assertGreaterEqual(rows["kernel.dwc3"].verified, 4)
        self.assertLessEqual(rows["kernel.dwc3"].mapped, rows["kernel.text"].mapped)

    def test_renderer_uses_only_the_given_coverage(self):
        fake = MapCoverage(
            areas=(AreaSummary("demo", "Demo area", 2, 3, 1, 4, 5, "E0001-E0002", "2026-11-22"),),
            inventories=(InventoryCoverage("x.items", "Demo items", "items", 200, 10, 5, 1, "E0001"),),
            frontier=(FrontierItem("demo", "F9", "Demo gate", "open"),),
            evidence_cited=5,
            evidence_total=50,
            evidence_indexed=45,
        )
        joined = " ".join(_texts(render_svg(fake)))
        self.assertIn("16 / 200 items · 8.0%", joined)
        self.assertIn("10 verified · 5 decoded · 1 frontier", joined)
        self.assertIn("F9  Demo gate", joined)
        self.assertIn("5 evidence records are not yet indexed", joined)
        self.assertIn("45 / 50 · 90.0%", joined)


class TestEpistemicHonesty(unittest.TestCase):
    SHARE_RE = re.compile(r"^[\d,]+ / [\d,]+ (?:\w+ )?· \d+(?:\.\d+)?%$")

    def test_percentages_only_appear_as_inventory_shares(self):
        for text in _texts():
            if "%" in text and "percentage" not in text:
                self.assertRegex(text, self.SHARE_RE)

    def test_growth_chart_shows_no_percentage(self):
        texts = _texts(GROWTH_SVG.read_text(encoding="utf-8"))
        self.assertFalse([text for text in texts if "%" in text])

    def test_share_format_never_rounds_nonzero_to_zero(self):
        self.assertEqual(format_share(0, 10), "0%")
        self.assertEqual(format_share(6, 14401), "0.04%")
        self.assertEqual(format_share(4, 91), "4.4%")

    def test_footnote_and_legend_are_present(self):
        joined = " ".join(_texts())
        self.assertIn("not an overall firmware compatibility percentage", joined)
        self.assertTrue("evidence records are indexed" in joined or "Unmapped does not mean unknown" in joined)
        for _state, label in LEGEND:
            self.assertIn(label, joined)


if __name__ == "__main__":
    unittest.main()
