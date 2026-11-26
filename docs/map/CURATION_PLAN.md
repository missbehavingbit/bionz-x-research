# Knowledge map: long-term deep-curation plan

This plan turns the knowledge map from a complete index into a complete, verified
summary. It is written for contributors: every step can be done by one person in one
pull request, and progress is measured by the tools rather than by hand.

## 1. Where the map stands

| Level | Meaning | Status (2026-11-22) |
| --- | --- | --- |
| Indexed | The file belongs to an area; its title, date and correction lines appear in the area's record index. | 706 / 706 records, 22 attachments |
| Cited | At least one curated claim, frontier item or hazard cites the record. | 706 / 706 |
| Deep-reviewed | Someone read the whole record and checked every claim that cites it, line by line. | usb-ep0 (55 / 55) , l2-kernel-boot (88 / 88) , qemu-a6400 (59 / 59) and usb-emulated (44 / 44) complete; other areas not started (run `todo` for the live count) |

Indexing and citation are complete, so no record is lost. But apart from the three records
above, the curated claims were written from record titles, lead paragraphs and the
consolidated documents (`CURRENT_STATE.md`, `docs/architecture/`, `PLATFORM_MODEL.md`), not
from full reads. Deep review closes that gap. The method is the one the USB/EP0 pilot used:
read the record, extract claims with their status, chase every correction and retraction,
and record hazards and the frontier.

Check progress at any time:

```powershell
.\venv\Scripts\python.exe -m tools.research.knowledge_map todo
.\venv\Scripts\python.exe -m tools.research.knowledge_map todo usb-ep0
```

The dashboard (`docs/images/research-status.svg`) shows the deep-reviewed count per area and
overall.

## 2. Unit of work: one slice per pull request

A **slice** is 8 to 20 consecutive records in one area, at most about 120 KiB of record text.
That is roughly 30k tokens of reading, a size one reviewer can verify. Smaller is fine.

1. **Claim the slice.** Open (or comment on) the area's tracking issue with the area name and
   the record range, for example `usb-ep0 E0354-E0365`. One open slice per area per person.
   Two contributors never edit the same area source in parallel. If you need to, split the
   area into two TOML files first, in a separate small PR.
2. **Branch.** Name it `map/<area>-<first>-<last>`, for example `map/usb-ep0-E0354-E0365`.
3. **Read every record in full**, in numeric order, including its tables and linked receipts
   where a claim depends on them. Do not rely on the title.
4. **Apply the review checklist** (section 3) to each record.
5. **Mark the records** you finished in the area's `meta.deep_reviewed` list. It uses the same
   syntax as `records` (ranges, `E0386:runb16` qualifiers, path globs, `-` exclusions). Only
   list records you actually read in full.
6. **Build and check:**

   ```powershell
   .\venv\Scripts\python.exe -m tools.research.knowledge_map build
   .\venv\Scripts\python.exe -m tools.research.knowledge_map check
   .\venv\Scripts\python.exe -m tools.research.render_research_status --write
   .\venv\Scripts\python.exe -m unittest tests.test_knowledge_map tests.test_research_status_dashboard tests.test_research_status_sync -q
   ```

7. **Open the PR** using the description template in section 6.

## 3. Review checklist (per record)

Work through these for every record in the slice.

- **Claims.** Every statement the record establishes, and still matters, is a claim with
  - the narrowest correct `status`: `verified-physical` only for a recorded camera or host
    run; `verified-offline` for disassembly, Unicorn, QEMU or static proof; `inference` when
    the record argues but does not observe; `open` for a stated hypothesis;
  - the exact numbers, addresses, hashes and units from the record, copied rather than
    paraphrased from memory;
  - every record that supports it in `evidence`.
- **Existing claims.** Each existing claim that cites the record is checked against the text.
  If a claim is wrong, do not edit it in place: add the corrected claim, mark the old one
  `retracted` or `refuted`, and set `superseded_by`.
- **Corrections.** Search the record for retract / supersede / correct / withdrawn / refuted,
  and search later records for its E-ID. Every correction becomes a retracted or refuted claim
  that points to its replacement. Check the "Correction notes" column of the record index for
  leads.
- **Hazards.** Anything that rebooted, crashed, wedged or bricked something, or wasted a
  supervised run, becomes a `hazard` with the rule that prevents it. Safety notes in
  `AGENTS.md` must match the hazards.
- **Entities.** Functions, addresses, devices, modules and problems the record is about become
  or extend entities. Add `inventory` links (`kernel.text:<symbol>`, `kernel.dwc3:<symbol>`,
  `kernel.modules:<name>`, `firmware.elf:<path suffix>`). `check` verifies each symbol and path
  against the local captures.
- **Frontier.** If the record leaves an open next step that is still current, the area's
  `frontier` says so, with its state and what to read in the result.
- **Drift.** If a consolidated document (`README.md`, `CURRENT_STATE.md`, `UVC_PTP_STATUS.md`,
  `PLATFORM_MODEL.md`, `docs/architecture/*.md`, `qemu-a6400/STATUS.md`) contradicts the record,
  add a `drift` entry. Fix the document in a separate PR unless the fix is one line.
- **English only, no secrets, no proprietary payloads.** Claims quote facts, not firmware
  bytes, keys or credentials.

## 4. Order of work

Priorities follow the active tracks in `AGENTS.md` §3: active work first, then
safety-critical physical areas, then completed or stable areas. Sizes come from
`knowledge_map todo`.

| Phase | Area | Records | Record text | Suggested slices |
| --- | --- | --- | --- | --- |
| 1 Active tracks | usb-ep0 (done, M1) | 0 left | - | - |
| 1 | l2-kernel-boot (done; E0431-E0677 from short-answer sections) | 0 left | - | - |
| 1 | qemu-a6400 (done; from short-answer sections) | 0 left | - | - |
| 1 | usb-emulated (done; from lead sections) | 0 left | - | - |
| 1 | l3-userspace | 81 | 491 KiB | 5 |
| 2 Physical safety | hardware-live | 15 | 93 KiB | 1 |
| 2 | native-debugger | 32 | 160 KiB | 2 |
| 2 | a6400a-acquisition | 18 | 251 KiB | 3 |
| 2 | usb-composite | 164 | 708 KiB | 8 |
| 3 Stable areas | imaging-pipeline | 35 | 221 KiB | 2 |
| 3 | menu-touch-ui | 55 | 272 KiB | 3 |
| 3 | platform-multidevice | 19 | 139 KiB | 2 |
| 3 | storage-sd-backup | 20 | 133 KiB | 2 |
| 3 | wifi-and-remote | 13 | 128 KiB | 1 |
| 3 | mods | 10 | 112 KiB | 1 |

About 4.1 MiB of records in about 45 slices. As a rough guide, one contributor finishing two
slices a week completes Phase 1 in about ten weeks; three contributors working in parallel on
different areas finish everything in about two to three months. Within an area, go oldest to
newest so corrections are met in the order they were made, except that the record holding the
current frontier should be read first. It anchors what "current" means.

### Milestones

| Milestone | Done when |
| --- | --- |
| M1 Pilot complete (done 2026-11-22) | usb-ep0 55 / 55 deep-reviewed; its frontier and hazards re-verified |
| M2 Active tracks | Phase 1 areas fully deep-reviewed; `AGENTS.md` §3 checkpoint cites map claims |
| M3 Safety | Phase 2 areas fully deep-reviewed; every `AGENTS.md` safety note has a matching hazard |
| M4 Complete | 706 / 706 deep-reviewed; every drift entry resolved or scheduled |
| M5 Steady state | Each new evidence record is mapped and deep-reviewed in the PR that adds it |

## 5. Rules that keep the map trustworthy

- **The evidence record stays authoritative.** The map summarises; when in doubt, link more and
  claim less.
- **Never edit history away.** Wrong claims become `retracted` or `refuted` with
  `superseded_by`; they are never deleted.
- **No silent status upgrades.** Moving `inference` to `verified-*` needs the record that
  observes it.
- **Generated files are never hand-edited.** `docs/map/<area>.md`, `docs/map/index.json` and
  the SVG come from `build` and `render_research_status --write`.
- **New evidence (M5).** A PR that adds `docs/evidence/E####-*.md` must also add the record to
  an area's `records`. `check` fails otherwise. It should also add or retract the claims the
  record changes and list it in `deep_reviewed`.
- **Inventory totals are re-verified locally.** If a source capture changes, update
  `docs/map/inventories.toml` (total and SHA-256) in its own PR, with the reason.

## 6. Pull request template

```markdown
## Map slice: <area> <first>-<last>

- Records read in full: <list or range> (now in `deep_reviewed`)
- Claims added: <ids> · retracted/refuted: <ids, with superseded_by> · changed status: <ids>
- Hazards added/updated: <ids> · frontier changes: <ids>
- Inventory links added: <count> (verified by `check` against local captures: yes/no)
- Drift found: <documents> (fixed here / follow-up issue)
- Checks: `knowledge_map check` OK, `render_research_status --check` OK, focused tests OK
```

### Reviewer checklist

- Pick at least three claims at random and verify them against the cited records.
- Every record in `deep_reviewed` is cited by at least one claim, hazard or frontier item, or
  the PR explains why it carries nothing current.
- Every retraction points to a live replacement claim.
- No claim is stronger than its record (status, numbers, scope).

## 7. Tooling backlog

These make deep curation cheaper. Each is a separate small PR.

1. **Per-claim review stamp.** Optional `reviewed = "YYYY-MM-DD"` on claims, rendered on the page
   and counted by `todo`.
2. **Correction cross-check.** A `check` warning when a record mentions an earlier E-ID next to
   "retract"/"supersede"/"correct" but no retracted claim cites that earlier record.
3. **Member lists for status inventories.** Add named members for `imdb.plugins`, `devno.nodes`
   and `flash.partitions` so entities can link to them (today only the totals are known).
4. **Area split helper.** Split a large area (`usb-composite`) into sub-areas without breaking
   claim IDs.
5. **`todo --next`** to print the next unclaimed slice of at most 120 KiB for an area.

## 8. Ownership

Each area has one curator of record at a time, named in its tracking issue. The curator
reviews slices from other contributors in that area and keeps the area's frontier current.
Ownership changes by comment on the tracking issue. Unowned areas can be claimed by anyone.
