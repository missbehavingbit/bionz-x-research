"""Knowledge map: subject-indexed summaries of evidence records (stdlib only).

Hand-curated sources live in ``docs/map/sources/<area>.toml``. Each source
declares entities (functions, addresses, devices, modules, problems), claims
about them with an explicit status and evidence references, the current
frontier, hazards, and known documentation drift. This tool validates the
sources against ``docs/evidence/`` and renders:

* ``docs/map/<area>.md``  - one readable page per area;
* ``docs/map/index.json`` - a lookup index (entity, address, evidence ID).

Commands (run from the repository root)::

    python -m tools.research.knowledge_map build      # write generated files
    python -m tools.research.knowledge_map check      # validate, fail on drift
    python -m tools.research.knowledge_map query 0x602b253c
    python -m tools.research.knowledge_map query E0379

The map summarises; evidence records stay authoritative. Retracted and
refuted claims are kept and rendered so they are not re-derived.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from project_paths import PROJECT_ROOT

MAP_DIR_REL = Path("docs") / "map"
SOURCES_DIR_REL = MAP_DIR_REL / "sources"
EVIDENCE_DIR_REL = Path("docs") / "evidence"
INDEX_NAME = "index.json"
SCHEMA_VERSION = 1

ENTITY_KINDS = frozenset({
    "problem", "device", "function", "field", "module", "source",
    "usb-identity", "host", "process", "address", "tool",
})
ACTIVE_STATUSES = ("verified-physical", "verified-offline", "inference", "open")
INACTIVE_STATUSES = ("refuted", "retracted")
CLAIM_STATUSES = frozenset(ACTIVE_STATUSES + INACTIVE_STATUSES)
FRONTIER_STATES = frozenset({
    "blocked-on-user-go", "open", "in-progress", "not-started", "done",
})
STATUS_LABEL = {
    "verified-physical": "PHYSICAL",
    "verified-offline": "OFFLINE",
    "inference": "INFERENCE",
    "open": "OPEN",
    "refuted": "REFUTED",
    "retracted": "RETRACTED",
}

EVIDENCE_FILE_RE = re.compile(r"^(\d{4})[a-z0-9]*-.+\.md$")
EVIDENCE_REF_RE = re.compile(r"^E(\d{4})(?::([a-z0-9-]+))?$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class MapError(ValueError):
    """Raised when sources are invalid; carries every problem found."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Area:
    name: str
    source: Path
    data: dict


# ----------------------------------------------------------------- evidence

def evidence_catalog(evidence_dir: Path) -> dict[str, list[str]]:
    """Map ``E0386`` to every evidence filename that carries that number."""
    catalog: dict[str, list[str]] = {}
    for path in sorted(evidence_dir.glob("*.md")):
        match = EVIDENCE_FILE_RE.match(path.name)
        if match:
            catalog.setdefault(f"E{match.group(1)}", []).append(path.name)
    for path in sorted(evidence_dir.glob("*/**/*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(evidence_dir).as_posix()
        catalog[f"doc:{rel}"] = [rel]
    return catalog


def evidence_label(ref: str) -> str:
    """Short link label: the E-ID, or the file stem of a ``doc:`` record."""
    if ref.startswith("doc:"):
        return Path(ref[4:]).stem
    return ref.split(":", 1)[0]


def resolve_evidence(ref: str, catalog: dict[str, list[str]]) -> str:
    """Resolve ``E0468``, ``E0386:retraction`` or ``doc:<path>`` to exactly one file."""
    if ref.startswith("doc:"):
        if ref not in catalog:
            raise LookupError(f"evidence {ref!r} has no record in docs/evidence")
        return catalog[ref][0]
    match = EVIDENCE_REF_RE.match(ref)
    if not match:
        raise LookupError(f"malformed evidence reference {ref!r}")
    eid = f"E{match.group(1)}"
    qualifier = match.group(2)
    names = catalog.get(eid, [])
    if qualifier:
        names = [n for n in names if qualifier in n[4:]]
    if not names:
        raise LookupError(f"evidence {ref!r} has no record in docs/evidence")
    if len(names) > 1:
        raise LookupError(
            f"evidence {ref!r} is ambiguous ({', '.join(names)}); add a ':fragment' qualifier")
    return names[0]


# ------------------------------------------------------------------ records
#
# Every file under docs/evidence/ (except the directory README) must belong to at
# least one area through that area's `records` list. Markdown files are records;
# other files (TSV/JSON/PNG receipts) are attachments. The record index on each area
# page is extracted from the files themselves, so it never paraphrases or drops them.

RECORD_RANGE_RE = re.compile(r"^E(\d{4})-E(\d{4})$")
DATE_RE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
CORRECTION_RE = re.compile(r"\b(retract\w*|supersed\w*|withdrawn|correct(?:ion|ed|s)\b|refuted)", re.IGNORECASE)
RECORD_ID_RE = re.compile(r"^(\d{4})")
LOCAL_LINK_RE = re.compile(r"\]\((?!https?:|/|\.\./|#|mailto:)([^)\s]+)\)")
# UTF-8 punctuation that some records store double-encoded (shown only in extracted titles).
MOJIBAKE = {"â€”": "—", "â€“": "–", "â†’": "→"}


def evidence_files(evidence_dir: Path) -> list[str]:
    """Every evidence file as a path relative to docs/evidence (README excluded)."""
    return sorted(
        path.relative_to(evidence_dir).as_posix()
        for path in evidence_dir.rglob("*")
        if path.is_file() and path.relative_to(evidence_dir).as_posix() != "README.md"
        and "__pycache__" not in path.parts
    )


def _file_number(rel: str) -> int | None:
    if "/" in rel:
        return None
    match = RECORD_ID_RE.match(rel)
    return int(match.group(1)) if match else None


def expand_records(entries, files: list[str], catalog: dict[str, list[str]]) -> tuple[set[str], list[str]]:
    """Expand ``records`` entries to files; return (files, problems).

    Entry forms: ``E0001-E0055`` (every file numbered in the range), ``E0386:runb16`` or
    ``E0468`` (one record, via resolve_evidence), ``path/glob*`` (files under
    docs/evidence), and any of those prefixed with ``-`` to exclude.
    """
    import fnmatch

    chosen: set[str] = set()
    problems: list[str] = []
    for raw in _as_list(entries):
        entry = str(raw)
        exclude = entry.startswith("-")
        body = entry[1:] if exclude else entry
        span = RECORD_RANGE_RE.match(body)
        if span:
            low, high = int(span.group(1)), int(span.group(2))
            found = {f for f in files if (n := _file_number(f)) is not None and low <= n <= high
                     and f.endswith(".md")}
        elif body.startswith("E"):
            try:
                found = {resolve_evidence(body, catalog)}
            except LookupError as exc:
                problems.append(f"records entry {entry!r}: {exc}")
                continue
        else:
            found = {f for f in files if fnmatch.fnmatchcase(f, body)}
        if not found:
            problems.append(f"records entry {entry!r} matches no evidence file")
        if exclude:
            chosen -= found
        else:
            chosen |= found
    return chosen, problems


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


@dataclass(frozen=True)
class RecordInfo:
    file: str
    label: str
    title: str
    date: str
    corrections: tuple[str, ...]


def record_info(evidence_dir: Path, rel: str) -> RecordInfo:
    """Title, date and correction notes extracted verbatim from one evidence file."""
    number = _file_number(rel)
    label = f"E{number:04d}" if number is not None else Path(rel).name
    if not rel.endswith(".md"):
        return RecordInfo(rel, label, "attachment", "", ())
    lines = _read_text(evidence_dir / rel).splitlines()
    title = next((line.lstrip("#").strip() for line in lines if line.startswith("#")), rel)
    for broken, fixed in MOJIBAKE.items():
        title = title.replace(broken, fixed)
    title = re.sub(r"^(?:E|Evidence(?: Record)?\s*)?0*\d{1,4}[a-z0-9]*\s*[:\-—–�]+\s*", "", title)
    date = next((m.group(1) for line in lines[:15] if (m := DATE_RE.search(line))), "")
    if not date:
        found = DATE_RE.search(rel)
        date = found.group(1) if found else ""
    notes = []
    for line in lines[1:]:
        if CORRECTION_RE.search(line):
            note = _one_line(line.strip("#*->` "))
            # Links in a record are relative to docs/evidence/; rebase same-directory ones.
            base = Path(rel).parent.as_posix()
            prefix = "../evidence/" + ("" if base == "." else base + "/")
            notes.append(LOCAL_LINK_RE.sub(lambda m: f"]({prefix}{m.group(1)})", note))
            if len(notes) == 2:
                break
    return RecordInfo(rel, label, _one_line(title), date, tuple(notes))


def unassigned_records(root: Path, areas: list["Area"]) -> list[str]:
    evidence_dir = root / EVIDENCE_DIR_REL
    files = evidence_files(evidence_dir)
    catalog = evidence_catalog(evidence_dir)
    assigned: set[str] = set()
    for area in areas:
        chosen, _ = expand_records(area.data.get("meta", {}).get("records"), files, catalog)
        assigned |= chosen
    return [f for f in files if f not in assigned]


# ------------------------------------------------------------------ loading

def load_areas(sources_dir: Path) -> list[Area]:
    areas = []
    for path in sorted(sources_dir.glob("*.toml")):
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        areas.append(Area(name=path.stem, source=path, data=data))
    return areas


def _as_list(value) -> list:
    return list(value) if isinstance(value, list) else []


def validate_area(area: Area, catalog: dict[str, list[str]], root: Path) -> list[str]:
    problems: list[str] = []
    where = area.source.name
    data = area.data
    meta = data.get("meta", {})
    for key in ("area", "title", "scope", "evidence_span", "reviewed"):
        if not isinstance(meta.get(key), str) or not meta.get(key).strip():
            problems.append(f"{where}: meta.{key} is required")
    if meta.get("area") and meta.get("area") != area.name:
        problems.append(f"{where}: meta.area {meta.get('area')!r} must equal the file stem")

    entities = {e.get("id"): e for e in _as_list(data.get("entity"))}
    claims = {c.get("id"): c for c in _as_list(data.get("claim"))}
    seen: set[str] = set()
    for table in ("entity", "claim", "frontier", "hazard"):
        for item in _as_list(data.get(table)):
            item_id = item.get("id")
            if not isinstance(item_id, str) or not ID_RE.match(item_id):
                problems.append(f"{where}: {table} has invalid id {item_id!r}")
                continue
            if item_id in seen:
                problems.append(f"{where}: duplicate id {item_id!r}")
            seen.add(item_id)

    def check_evidence(owner: str, refs, required: bool = True) -> None:
        refs = _as_list(refs)
        if required and not refs:
            problems.append(f"{where}: {owner} cites no evidence")
        for ref in refs:
            try:
                resolve_evidence(ref, catalog)
            except LookupError as exc:
                problems.append(f"{where}: {owner}: {exc}")

    def check_entities(owner: str, refs) -> None:
        for ref in _as_list(refs):
            if ref not in entities:
                problems.append(f"{where}: {owner} references unknown entity {ref!r}")

    for eid, entity in entities.items():
        if entity.get("kind") not in ENTITY_KINDS:
            problems.append(f"{where}: entity {eid} has unknown kind {entity.get('kind')!r}")
        for key in ("name", "summary"):
            if not isinstance(entity.get(key), str) or not entity.get(key).strip():
                problems.append(f"{where}: entity {eid} needs {key}")
        check_entities(f"entity {eid}.related", entity.get("related"))

    for cid, claim in claims.items():
        status = claim.get("status")
        if status not in CLAIM_STATUSES:
            problems.append(f"{where}: claim {cid} has unknown status {status!r}")
        if not isinstance(claim.get("text"), str) or not claim.get("text").strip():
            problems.append(f"{where}: claim {cid} needs text")
        if not _as_list(claim.get("entities")):
            problems.append(f"{where}: claim {cid} names no entity")
        check_entities(f"claim {cid}", claim.get("entities"))
        check_evidence(f"claim {cid}", claim.get("evidence"))
        successor = claim.get("superseded_by")
        if successor:
            if status in ACTIVE_STATUSES:
                problems.append(
                    f"{where}: claim {cid} is {status} but has superseded_by; mark it retracted or refuted")
            target = claims.get(successor)
            if target is None:
                problems.append(f"{where}: claim {cid} superseded_by unknown claim {successor!r}")
            elif target.get("status") not in ACTIVE_STATUSES:
                problems.append(
                    f"{where}: claim {cid} superseded_by {successor}, which is itself inactive")

    for entity_id in entities:
        if not any(entity_id in _as_list(c.get("entities")) for c in claims.values()) \
                and not any(entity_id in _as_list(f.get("entities"))
                            for f in _as_list(data.get("frontier"))):
            problems.append(f"{where}: entity {entity_id} has no claim or frontier item")

    for item in _as_list(data.get("frontier")):
        fid = item.get("id")
        if item.get("state") not in FRONTIER_STATES:
            problems.append(f"{where}: frontier {fid} has unknown state {item.get('state')!r}")
        for key in ("title", "next"):
            if not isinstance(item.get(key), str) or not item.get(key).strip():
                problems.append(f"{where}: frontier {fid} needs {key}")
        check_entities(f"frontier {fid}", item.get("entities"))
        check_evidence(f"frontier {fid}", item.get("evidence"))

    for item in _as_list(data.get("hazard")):
        hid = item.get("id")
        for key in ("title", "effect", "rule"):
            if not isinstance(item.get(key), str) or not item.get(key).strip():
                problems.append(f"{where}: hazard {hid} needs {key}")
        check_evidence(f"hazard {hid}", item.get("evidence"))

    for item in _as_list(data.get("drift")):
        doc = item.get("doc", "")
        if not doc or not (root / doc).is_file():
            problems.append(f"{where}: drift entry names missing document {doc!r}")

    evidence_dir = root / EVIDENCE_DIR_REL
    files = evidence_files(evidence_dir)
    chosen, record_problems = expand_records(data.get("meta", {}).get("records"), files, catalog)
    problems.extend(f"{where}: {problem}" for problem in record_problems)
    deep, deep_problems = expand_records(data.get("meta", {}).get("deep_reviewed"), files, catalog)
    problems.extend(f"{where}: deep_reviewed {problem}" for problem in deep_problems)
    problems.extend(f"{where}: deep_reviewed names {rel}, which is not in this area's records"
                    for rel in sorted(deep - chosen))
    return problems


# ---------------------------------------------------------------- rendering

def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _cell(text: str) -> str:
    return _one_line(text).replace("|", r"\|")


def _evidence_links(refs, catalog: dict[str, list[str]]) -> str:
    links = []
    for ref in _as_list(refs):
        name = resolve_evidence(ref, catalog)
        label = evidence_label(ref)
        links.append(f"[{label}](../evidence/{name})")
    return ", ".join(links)


def _anchor(item_id: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", item_id.lower())


def render_area(area: Area, catalog: dict[str, list[str]], records: list[RecordInfo] | None = None,
                deep: set[str] | None = None) -> str:
    data = area.data
    meta = data["meta"]
    entities = _as_list(data.get("entity"))
    claims = _as_list(data.get("claim"))
    claim_by_id = {c["id"]: c for c in claims}
    out: list[str] = []
    add = out.append

    add(f"# Knowledge map: {meta['title']}")
    add("")
    add(f"<!-- Generated by `python -m tools.research.knowledge_map build` from "
        f"`{SOURCES_DIR_REL.as_posix()}/{area.source.name}`. Do not edit by hand. -->")
    add("")
    add(f"- **Scope:** {_one_line(meta['scope'])}")
    if meta.get("body"):
        add(f"- **Target:** {_one_line(meta['body'])}")
    add(f"- **Evidence span:** {meta['evidence_span']}; last reviewed {meta['reviewed']}.")
    if records:
        md_records = [info for info in records if info.file.endswith(".md")]
        done = sum(1 for info in md_records if info.file in (deep or set()))
        add(f"- **Deep review:** {done} of {len(md_records)} records read in full and checked claim by "
            "claim ([curation plan](CURATION_PLAN.md)); the rest are curated from titles, lead "
            "paragraphs and consolidated documents.")
    if meta.get("not_covered"):
        add(f"- **Not covered here:** {_one_line(meta['not_covered'])}")
    add("- **Status legend:** PHYSICAL = observed on the camera/host; OFFLINE = disassembly, "
        "Unicorn or static proof; INFERENCE = argued from evidence, not observed directly; "
        "RETRACTED/REFUTED = do not rely on, kept to prevent re-derivation.")
    add("")
    add("The evidence records are authoritative; follow the links before acting on a claim.")
    add("")

    frontier = _as_list(data.get("frontier"))
    if frontier:
        add("## Frontier")
        add("")
        for item in frontier:
            add(f"### <a id=\"{_anchor(item['id'])}\"></a>{item['id']} - {item['title']}")
            add("")
            add(f"- **State:** `{item['state']}`")
            if item.get("candidate"):
                add(f"- **Candidate:** {_one_line(item['candidate'])}")
            if item.get("verified"):
                add(f"- **Verified so far:** {_one_line(item['verified'])}")
            if item.get("preflight"):
                add(f"- **Preflight:** {_one_line(item['preflight'])}")
            add(f"- **Next:** {_one_line(item['next'])}")
            if item.get("read_in_result"):
                add(f"- **Read in the result:** {_one_line(item['read_in_result'])}")
            for hypothesis in _as_list(item.get("hypotheses")):
                add(f"- {_one_line(hypothesis)}")
            add(f"- **Evidence:** {_evidence_links(item.get('evidence'), catalog)}")
            add("")

    hazards = _as_list(data.get("hazard"))
    if hazards:
        add("## Hazards (read before any camera run)")
        add("")
        add("| ID | Hazard | Effect | Rule | Evidence |")
        add("| --- | --- | --- | --- | --- |")
        for item in hazards:
            add(f"| <a id=\"{_anchor(item['id'])}\"></a>{item['id']} | {_cell(item['title'])} | "
                f"{_cell(item['effect'])} | {_cell(item['rule'])} | "
                f"{_evidence_links(item.get('evidence'), catalog)} |")
        add("")

    addressed = [e for e in entities if e.get("address")]
    if addressed:
        add("## Address lookup")
        add("")
        add("| Address | Entity |")
        add("| --- | --- |")
        for entity in sorted(addressed, key=lambda e: str(e["address"])):
            add(f"| `{entity['address']}` | [{entity['name']}](#{_anchor(entity['id'])}) |")
        add("")

    add("## Entities")
    add("")
    for entity in entities:
        eid = entity["id"]
        add(f"### <a id=\"{_anchor(eid)}\"></a>{entity['name']}")
        add("")
        facts = [f"`{eid}`", entity["kind"]]
        if entity.get("address"):
            facts.append(f"address `{entity['address']}`")
        if entity.get("inventory"):
            facts.append("inventory " + ", ".join(f"`{ref}`" for ref in entity["inventory"]))
        add(" | ".join(facts))
        add("")
        add(_one_line(entity["summary"]))
        add("")
        if entity.get("aliases"):
            add("Aliases: " + ", ".join(f"`{a}`" for a in entity["aliases"]))
            add("")
        mine = [c for c in claims if eid in _as_list(c.get("entities"))]
        active = [c for c in mine if c.get("status") in ACTIVE_STATUSES]
        inactive = [c for c in mine if c.get("status") in INACTIVE_STATUSES]
        if active:
            add("Current knowledge:")
            add("")
            for claim in active:
                line = (f"- **{claim['id']}** `{STATUS_LABEL[claim['status']]}` "
                        f"{_one_line(claim['text'])} ({_evidence_links(claim['evidence'], catalog)})")
                if claim.get("note"):
                    line += f" *Note: {_one_line(claim['note'])}*"
                add(line)
            add("")
        if inactive:
            add("Do not rely on:")
            add("")
            for claim in inactive:
                line = (f"- ~~{_one_line(claim['text'])}~~ **{claim['id']}** "
                        f"`{STATUS_LABEL[claim['status']]}` ({_evidence_links(claim['evidence'], catalog)})")
                successor = claim.get("superseded_by")
                if successor:
                    line += f"; see {successor}: {_one_line(claim_by_id[successor]['text'])}"
                if claim.get("note"):
                    line += f" *Note: {_one_line(claim['note'])}*"
                add(line)
            add("")
        related = [r for r in _as_list(entity.get("related"))]
        if related:
            names = {e["id"]: e["name"] for e in entities}
            add("Related: " + ", ".join(f"[{names[r]}](#{_anchor(r)})" for r in related))
            add("")

    if records:
        add(f"## Record index ({len(records)} files)")
        add("")
        add("Extracted from the files themselves: title, first date, and up to two lines that "
            "mention a correction, retraction or supersession. Every file under `docs/evidence/` "
            "belongs to at least one area.")
        add("")
        add("| Record | Date | Title | Deep | Correction notes |")
        add("| --- | --- | --- | --- | --- |")
        for info in records:
            notes = " / ".join(_cell(note)[:220] for note in info.corrections)
            mark = "yes" if info.file in (deep or set()) else ""
            add(f"| [{info.label}](../evidence/{info.file}) | {info.date} | {_cell(info.title)} | {mark} | {notes} |")
        add("")

    drift = _as_list(data.get("drift"))
    if drift:
        add("## Documentation drift")
        add("")
        add("| Document | Section | Says | Current |")
        add("| --- | --- | --- | --- |")
        for item in drift:
            doc_link = f"[{item['doc']}](../../{item['doc']})"
            add(f"| {doc_link} | {_cell(item.get('section', ''))} | "
                f"{_cell(item.get('observed', ''))} | {_cell(item.get('current', ''))} |")
        add("")
    return "\n".join(out).rstrip() + "\n"


def build_index(areas: list[Area], catalog: dict[str, list[str]],
                records_by_area: dict[str, list[str]] | None = None) -> dict:
    index: dict = {"schema": SCHEMA_VERSION, "areas": {}, "entities": {},
                   "addresses": {}, "evidence": {}}
    for area in areas:
        data = area.data
        meta = data["meta"]
        index["areas"][area.name] = {
            "title": meta["title"],
            "doc": f"{MAP_DIR_REL.as_posix()}/{area.name}.md",
            "reviewed": meta["reviewed"],
            "evidence_span": meta["evidence_span"],
        }
        claims = _as_list(data.get("claim"))
        for entity in _as_list(data.get("entity")):
            eid = entity["id"]
            mine = [c for c in claims if eid in _as_list(c.get("entities"))]
            index["entities"][eid] = {
                "area": area.name,
                "name": entity["name"],
                "kind": entity["kind"],
                "address": entity.get("address"),
                "aliases": _as_list(entity.get("aliases")),
                "anchor": f"{area.name}.md#{_anchor(eid)}",
                "active_claims": [c["id"] for c in mine if c.get("status") in ACTIVE_STATUSES],
                "inactive_claims": [c["id"] for c in mine if c.get("status") in INACTIVE_STATUSES],
            }
            if entity.get("address"):
                index["addresses"][str(entity["address"])] = eid
        for table, kind in (("claim", "claim"), ("frontier", "frontier"), ("hazard", "hazard")):
            for item in _as_list(data.get(table)):
                for ref in _as_list(item.get("evidence")):
                    eid = evidence_label(ref)
                    entry = {"area": area.name, "kind": kind, "id": item["id"],
                             "file": resolve_evidence(ref, catalog)}
                    if kind == "claim":
                        entry["status"] = item.get("status")
                    index["evidence"].setdefault(eid, []).append(entry)
    index["addresses"] = dict(sorted(index["addresses"].items()))
    index["evidence"] = dict(sorted(index["evidence"].items()))
    if records_by_area is not None:
        owners: dict[str, list[str]] = {}
        for area_name, files in records_by_area.items():
            for rel in files:
                owners.setdefault(rel, []).append(area_name)
        index["records"] = {rel: sorted(names) for rel, names in sorted(owners.items())}
    return index


def area_deep_reviewed(root: Path, areas: list[Area]) -> dict[str, set[str]]:
    """Records each area marks as deep-reviewed (see docs/map/CURATION_PLAN.md)."""
    evidence_dir = root / EVIDENCE_DIR_REL
    files = evidence_files(evidence_dir)
    catalog = evidence_catalog(evidence_dir)
    return {area.name: expand_records(area.data.get("meta", {}).get("deep_reviewed"), files, catalog)[0]
            for area in areas}


def area_records(root: Path, areas: list[Area]) -> dict[str, list[str]]:
    """Sorted evidence files per area, from each area's ``records`` list."""
    evidence_dir = root / EVIDENCE_DIR_REL
    files = evidence_files(evidence_dir)
    catalog = evidence_catalog(evidence_dir)
    return {area.name: sorted(expand_records(area.data.get("meta", {}).get("records"), files, catalog)[0]) for area in areas}


def generate(root: Path = PROJECT_ROOT) -> dict[Path, str]:
    """Validate every source and return {absolute output path: text}."""
    evidence_dir = root / EVIDENCE_DIR_REL
    catalog = evidence_catalog(evidence_dir)
    areas = load_areas(root / SOURCES_DIR_REL)
    problems: list[str] = []
    for area in areas:
        problems.extend(validate_area(area, catalog, root))
    problems.extend(coverage_problems(root, areas, load_inventories(root)))
    problems.extend(f"evidence file {rel} belongs to no map area (add it to a `records` list)"
                    for rel in unassigned_records(root, areas))
    if problems:
        raise MapError(problems)
    per_area = area_records(root, areas)
    deep_by_area = area_deep_reviewed(root, areas)
    outputs = {
        root / MAP_DIR_REL / f"{area.name}.md": render_area(
            area, catalog, [record_info(evidence_dir, rel) for rel in per_area[area.name]],
            deep_by_area[area.name])
        for area in areas
    }
    outputs[root / MAP_DIR_REL / INDEX_NAME] = (
        json.dumps(build_index(areas, catalog, per_area), indent=2, sort_keys=False) + "\n")
    return outputs


def stale_outputs(root: Path = PROJECT_ROOT) -> list[Path]:
    stale = []
    for path, text in generate(root).items():
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != text:
            stale.append(path)
    return stale


# -------------------------------------------------------------------- query

def query(term: str, root: Path = PROJECT_ROOT) -> str:
    """Return a plain-text answer for an entity, alias, address, word, or E-ID."""
    catalog = evidence_catalog(root / EVIDENCE_DIR_REL)
    areas = load_areas(root / SOURCES_DIR_REL)
    needle = term.strip().lower()
    lines: list[str] = []
    eid_match = re.fullmatch(r"e(\d{4})", needle)
    for area in areas:
        data = area.data
        claims = _as_list(data.get("claim"))
        claim_by_id = {c["id"]: c for c in claims}
        for entity in _as_list(data.get("entity")):
            haystack = [entity["id"], entity["name"], str(entity.get("address", ""))]
            haystack += _as_list(entity.get("aliases"))
            if not any(needle in str(h).lower() for h in haystack):
                continue
            lines.append(f"[{area.name}] {entity['name']} ({entity['id']}, {entity['kind']}"
                         + (f", {entity['address']}" if entity.get("address") else "") + ")")
            lines.append(f"  {_one_line(entity['summary'])}")
            for claim in claims:
                if entity["id"] in _as_list(claim.get("entities")):
                    lines.append(_claim_line(claim, claim_by_id))
            lines.append("")
        for table in ("claim", "frontier", "hazard"):
            for item in _as_list(data.get(table)):
                refs = [evidence_label(r).lower() for r in _as_list(item.get("evidence"))]
                text_fields = " ".join(str(item.get(k, "")) for k in
                                       ("text", "title", "effect", "rule", "next", "note"))
                hit = (eid_match and needle in refs) or (not eid_match and needle in text_fields.lower())
                if not hit:
                    continue
                if table == "claim":
                    lines.append(f"[{area.name}] " + _claim_line(item, claim_by_id).lstrip())
                else:
                    lines.append(f"[{area.name}] {table} {item['id']}: {_one_line(item['title'])}")
                    detail = item.get("next") or item.get("rule")
                    if detail:
                        lines.append(f"  {_one_line(detail)}")
    if eid_match:
        files = catalog.get(f"E{eid_match.group(1)}", [])
        lines.append("Evidence files: " + (", ".join(files) if files else "none"))
    if not lines:
        return f"No map entry for {term!r}. Areas mapped: {', '.join(a.name for a in areas)}.\n"
    return "\n".join(lines).rstrip() + "\n"


def _claim_line(claim: dict, claim_by_id: dict) -> str:
    label = STATUS_LABEL.get(claim.get("status"), "?")
    refs = ", ".join(_as_list(claim.get("evidence")))
    line = f"  {claim['id']} {label}: {_one_line(claim['text'])} [{refs}]"
    successor = claim.get("superseded_by")
    if successor and successor in claim_by_id:
        line += f" -> superseded by {successor}"
    return line


# ----------------------------------------------------------------- coverage

INVENTORY_KINDS = frozenset({"kallsyms", "json", "status"})
DEPTH_VERIFIED = "verified"
DEPTH_DECODED = "decoded"
DEPTH_BOUNDARY = "boundary"
DEPTH_ORDER = (DEPTH_VERIFIED, DEPTH_BOUNDARY, DEPTH_DECODED)


@dataclass(frozen=True)
class InventoryCoverage:
    id: str
    title: str
    unit: str
    total: int
    verified: int
    decoded: int
    boundary: int
    source: str

    @property
    def mapped(self) -> int:
        return self.verified + self.decoded + self.boundary


@dataclass(frozen=True)
class FrontierItem:
    area: str
    id: str
    title: str
    state: str


@dataclass(frozen=True)
class AreaSummary:
    name: str
    title: str
    entities: int
    active_claims: int
    inactive_claims: int
    hazards: int
    evidence_records: int
    evidence_span: str
    reviewed: str
    records: int = 0
    deep_reviewed: int = 0


@dataclass(frozen=True)
class MapCoverage:
    areas: tuple[AreaSummary, ...]
    inventories: tuple[InventoryCoverage, ...]
    frontier: tuple[FrontierItem, ...]
    evidence_cited: int
    evidence_total: int
    evidence_indexed: int = 0
    evidence_deep: int = 0

    @property
    def entities(self) -> int:
        return sum(a.entities for a in self.areas)

    @property
    def active_claims(self) -> int:
        return sum(a.active_claims for a in self.areas)

    @property
    def inactive_claims(self) -> int:
        return sum(a.inactive_claims for a in self.areas)

    @property
    def hazards(self) -> int:
        return sum(a.hazards for a in self.areas)


def load_inventories(root: Path) -> list[dict]:
    path = root / MAP_DIR_REL / "inventories.toml"
    if not path.is_file():
        return []
    with path.open("rb") as handle:
        return list(tomllib.load(handle).get("inventory", []))


def _kallsyms_members(path: Path, scope: str, match: str) -> set[str]:
    members: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[1] not in ("t", "T"):
            continue
        if scope == "vmlinux" and len(fields) == 3:
            if match in fields[2]:
                members.add(fields[2])
        elif scope == "modules" and len(fields) == 4:
            members.add(fields[3].strip("[]"))
    return members


def _status_total(category: str) -> int | None:
    from tools.research.a6400_status_data import status_categories

    found = status_categories().get(category)
    return None if found is None else found.total


def inventory_totals(root: Path, inventories: list[dict]) -> tuple[dict[str, int], dict[str, set[str] | None], list[str]]:
    """Return totals, locally known member sets (None = source absent), and problems."""
    import hashlib

    totals: dict[str, int] = {}
    members: dict[str, set[str] | None] = {}
    problems: list[str] = []
    catalog = evidence_catalog(root / EVIDENCE_DIR_REL)
    for item in inventories:
        iid = item.get("id")
        kind = item.get("kind")
        where = f"inventories.toml: {iid}"
        if not isinstance(iid, str) or not ID_RE.match(iid):
            problems.append(f"inventories.toml: invalid id {iid!r}")
            continue
        if kind not in INVENTORY_KINDS:
            problems.append(f"{where}: unknown kind {kind!r}")
            continue
        for key in ("title", "unit"):
            if not isinstance(item.get(key), str) or not item.get(key):
                problems.append(f"{where}: needs {key}")
        if item.get("evidence"):
            try:
                resolve_evidence(item["evidence"], catalog)
            except LookupError as exc:
                problems.append(f"{where}: {exc}")
        members[iid] = None
        if kind == "status":
            total = _status_total(item.get("category", ""))
            if total is None:
                problems.append(f"{where}: unknown status category {item.get('category')!r}")
                continue
            totals[iid] = total
            continue
        total = item.get("total")
        if not isinstance(total, int) or total <= 0:
            problems.append(f"{where}: needs a positive integer total")
            continue
        totals[iid] = total
        source = root / item.get("source", "")
        if not item.get("source") or not source.is_file():
            continue  # untracked source: keep the recorded total
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != item.get("source_sha256"):
            problems.append(f"{where}: {item['source']} SHA-256 is {digest}, not the recorded value")
            continue
        if kind == "kallsyms":
            found = _kallsyms_members(source, item.get("scope", ""), item.get("match", ""))
            members[iid] = found
            if len(found) != total:
                problems.append(f"{where}: source counts {len(found)}, recorded total is {total}")
        else:
            document = json.loads(source.read_text(encoding="utf-8"))
            if item.get("list"):
                entries = document.get(item["list"], [])
                field = item.get("member_field", "path")
                # Members are matched by path suffix, e.g. "nflasha15_unpacked/bin/im.elf".
                members[iid] = {str(entry.get(field, "")) for entry in entries}
                value = len(entries)
            else:
                value = document.get(item.get("key", ""))
            if value != total:
                problems.append(f"{where}: {item['source']} counts {value!r}, recorded total is {total}")
    return totals, members, problems


def _entity_depth(entity_id: str, data: dict) -> str:
    statuses = {c.get("status") for c in _as_list(data.get("claim"))
                if entity_id in _as_list(c.get("entities"))}
    if statuses & {"verified-physical", "verified-offline"}:
        return DEPTH_VERIFIED
    if any(entity_id in _as_list(f.get("entities")) and f.get("state") != "done"
           for f in _as_list(data.get("frontier"))):
        return DEPTH_BOUNDARY
    return DEPTH_DECODED


def coverage_problems(root: Path, areas: list[Area], inventories: list[dict]) -> list[str]:
    _totals, members, problems = inventory_totals(root, inventories)
    known = {item.get("id") for item in inventories}
    for area in areas:
        for entity in _as_list(area.data.get("entity")):
            for ref in _as_list(entity.get("inventory")):
                iid, _, member = str(ref).partition(":")
                owner = f"{area.source.name}: entity {entity.get('id')}"
                if iid not in known or not member:
                    problems.append(f"{owner}: invalid inventory reference {ref!r}")
                elif members.get(iid) is not None and member not in members[iid] and not any(
                        full.endswith("/" + member) for full in members[iid]):
                    problems.append(f"{owner}: {member!r} is not a member of inventory {iid}")
    return problems


def compute_coverage(root: Path = PROJECT_ROOT) -> MapCoverage:
    """Join mapped entities with firmware inventories (raises MapError on bad input)."""
    catalog = evidence_catalog(root / EVIDENCE_DIR_REL)
    areas = load_areas(root / SOURCES_DIR_REL)
    inventories = load_inventories(root)
    problems = coverage_problems(root, areas, inventories)
    for area in areas:
        problems.extend(validate_area(area, catalog, root))
    if problems:
        raise MapError(problems)
    totals, _members, _ = inventory_totals(root, inventories)

    per_area = area_records(root, areas)
    deep_by_area = area_deep_reviewed(root, areas)
    best: dict[str, dict[str, str]] = {item["id"]: {} for item in inventories}
    rank = {depth: index for index, depth in enumerate(DEPTH_ORDER)}
    summaries = []
    frontier = []
    cited: set[str] = set()
    for area in areas:
        data = area.data
        claims = _as_list(data.get("claim"))
        area_files: set[str] = set()
        for table in ("claim", "frontier", "hazard"):
            for item in _as_list(data.get(table)):
                for ref in _as_list(item.get("evidence")):
                    area_files.add(resolve_evidence(ref, catalog))
        cited |= area_files
        for entity in _as_list(data.get("entity")):
            depth = _entity_depth(entity["id"], data)
            for ref in _as_list(entity.get("inventory")):
                iid, _, member = str(ref).partition(":")
                current = best[iid].get(member)
                if current is None or rank[depth] < rank[current]:
                    best[iid][member] = depth
        summaries.append(AreaSummary(
            name=area.name,
            title=data["meta"]["title"],
            entities=len(_as_list(data.get("entity"))),
            active_claims=sum(1 for c in claims if c.get("status") in ACTIVE_STATUSES),
            inactive_claims=sum(1 for c in claims if c.get("status") in INACTIVE_STATUSES),
            hazards=len(_as_list(data.get("hazard"))),
            evidence_records=sum(1 for name in area_files if name.endswith(".md")),
            evidence_span=data["meta"]["evidence_span"],
            reviewed=data["meta"]["reviewed"],
            records=sum(1 for rel in per_area[area.name] if rel.endswith(".md")),
            deep_reviewed=sum(1 for rel in deep_by_area[area.name] if rel.endswith(".md")),
        ))
        frontier.extend(FrontierItem(area.name, f["id"], f["title"], f["state"])
                        for f in _as_list(data.get("frontier")))

    rows = []
    for item in inventories:
        depths = list(best[item["id"]].values())
        source = item.get("evidence") or f"status:{item.get('category')}"
        rows.append(InventoryCoverage(
            id=item["id"], title=item["title"], unit=item["unit"], total=totals[item["id"]],
            verified=depths.count(DEPTH_VERIFIED), decoded=depths.count(DEPTH_DECODED),
            boundary=depths.count(DEPTH_BOUNDARY), source=source,
        ))
    evidence_total = len({name for names in catalog.values() for name in names if name.endswith(".md")})
    indexed = {rel for files in per_area.values() for rel in files if rel.endswith(".md")}
    cited = {name for name in cited if name.endswith(".md")}
    deep_all = {rel for files in deep_by_area.values() for rel in files if rel.endswith(".md")}
    return MapCoverage(tuple(summaries), tuple(rows), tuple(frontier), len(cited), evidence_total,
                       len(indexed), len(deep_all))


def todo(area_name: str | None = None, root: Path = PROJECT_ROOT) -> str:
    """Records not yet deep-reviewed, per area, with sizes (oldest first)."""
    evidence_dir = root / EVIDENCE_DIR_REL
    areas = load_areas(root / SOURCES_DIR_REL)
    names = [area.name for area in areas]
    if area_name and area_name not in names:
        return f"Unknown area {area_name!r}. Areas: {', '.join(names)}." + "\n"
    per_area = area_records(root, areas)
    deep = area_deep_reviewed(root, areas)
    lines = []
    for area in areas:
        if area_name and area.name != area_name:
            continue
        records = [rel for rel in per_area[area.name] if rel.endswith(".md")]
        waiting = [rel for rel in records if rel not in deep[area.name]]
        kib = sum((evidence_dir / rel).stat().st_size for rel in waiting) / 1024
        lines.append(f"[{area.name}] {len(records) - len(waiting)}/{len(records)} deep-reviewed; "
                     f"{len(waiting)} waiting ({kib:.0f} KiB)")
        for rel in waiting:
            info = record_info(evidence_dir, rel)
            size = (evidence_dir / rel).stat().st_size / 1024
            lines.append(f"  {info.label:8} {size:6.1f} KiB  {info.title[:90]}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="validate sources and write docs/map outputs")
    sub.add_parser("check", help="validate sources and fail if outputs are stale")
    q = sub.add_parser("query", help="look up an entity, address, word, or E-ID")
    q.add_argument("term")
    t = sub.add_parser("todo", help="list records still waiting for a deep review")
    t.add_argument("area", nargs="?", help="limit to one area")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # titles may hold characters a legacy console lacks
    try:
        if args.command == "build":
            for path, text in generate().items():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                print(path.relative_to(PROJECT_ROOT).as_posix())
            return 0
        if args.command == "check":
            stale = stale_outputs()
            if stale:
                for path in stale:
                    print(f"stale: {path.relative_to(PROJECT_ROOT).as_posix()}", file=sys.stderr)
                print("run: python -m tools.research.knowledge_map build", file=sys.stderr)
                return 1
            print("knowledge map OK")
            return 0
        if args.command == "todo":
            sys.stdout.write(todo(args.area))
            return 0
        sys.stdout.write(query(args.term))
        return 0
    except MapError as exc:
        for problem in exc.problems:
            print(f"error: {problem}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
