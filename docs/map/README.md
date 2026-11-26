# Knowledge map

The evidence log (`docs/evidence/`, 700+ records) is ordered by time. Most questions are
about a subject: "what do we know about `0x602b253c`?", "is the E0379 conclusion still
valid?", "what must not be done before the next camera run?". The knowledge map answers
those questions by subject, with a status and evidence links on every claim.

The map summarises. Evidence records stay authoritative. Follow the links before acting
on a claim.

## Areas

Every file under `docs/evidence/` belongs to at least one area; `check` fails on any
unassigned file. Each page ends with a record index extracted from the files themselves.

| Area | Page |
| --- | --- |
| L3 user space: original im.elf under InterpreterProbe | [l3-userspace.md](l3-userspace.md) |
| L2 full-system kernel boot (UnicornMachine) | [l2-kernel-boot.md](l2-kernel-boot.md) |
| QEMU sony-a6400 machine | [qemu-a6400.md](qemu-a6400.md) |
| USB composite UVC/PTP on the physical camera (before EP0 runs) | [usb-composite.md](usb-composite.md) |
| USB / DWC3 EP0 control path (Track B UVC/PTP) | [usb-ep0.md](usb-ep0.md) |
| USB stack inside the emulators | [usb-emulated.md](usb-emulated.md) |
| Native USB debugger, monitor and diagnostic storage | [native-debugger.md](native-debugger.md) |
| Live hardware observations and peripheral contracts | [hardware-live.md](hardware-live.md) |
| Storage, SD card and backup service | [storage-sd-backup.md](storage-sd-backup.md) |
| Menu model and touch UI | [menu-touch-ui.md](menu-touch-ui.md) |
| Mod Loader and firmware mods | [mods.md](mods.md) |
| Imaging pipeline: color, RAW, RCRAW and recording paths | [imaging-pipeline.md](imaging-pipeline.md) |
| Wi-Fi and remote control | [wifi-and-remote.md](wifi-and-remote.md) |
| CXD90045 multi-device platform | [platform-multidevice.md](platform-multidevice.md) |
| ILCE-6400A qualification and firmware acquisition | [a6400a-acquisition.md](a6400a-acquisition.md) |

## Use

```powershell
.\venv\Scripts\python.exe -m tools.research.knowledge_map query 0x602b253c
.\venv\Scripts\python.exe -m tools.research.knowledge_map query E0379
.\venv\Scripts\python.exe -m tools.research.knowledge_map query ep0state
```

`query` matches entity ids, names, aliases, and addresses, claim text, and E-IDs. An E-ID
query also shows whether the claims that cite that record are still current. Read the
area page for the whole picture. `index.json` is the same data for tools.

## Layout

- `sources/<area>.toml`: the hand-curated source of truth.
- `<area>.md` and `index.json`: generated. Do not edit them by hand.

Each source has these tables:

| Table | Holds |
| --- | --- |
| `meta` | area, title, scope, target, evidence span, review date, what is not covered, and `records` (ranges such as `E0001-E0055`, single records such as `E0386:runb16`, path globs such as `cross_model/*`, and `-` exclusions) |
| `entity` | a subject: `problem`, `device`, `function`, `field`, `module`, `source`, `usb-identity`, `host`, `process`, `address`, `tool` |
| `claim` | one statement about one or more entities, with `status` and `evidence` |
| `frontier` | the open edge of the work: state, candidate, next step, hypotheses, what to read in the result |
| `hazard` | something that crashed, rebooted, or wasted a run, and the rule that prevents it |
| `drift` | a document whose stated status no longer matches the evidence |

Claim statuses:

| Status | Meaning |
| --- | --- |
| `verified-physical` | observed on the camera or host during a recorded run |
| `verified-offline` | disassembly, Unicorn execution, or static proof |
| `inference` | argued from evidence, not observed directly |
| `open` | stated hypothesis under test |
| `refuted` | tested and shown false |
| `retracted` | withdrawn after a later record corrected it |

## Coverage and the status dashboard

[`inventories.toml`](inventories.toml) lists firmware inventories: kernel functions and
DWC3 functions from a captured `/proc/kallsyms`, loaded modules, firmware ELF files,
`im.elf` static plugins, device nodes, and flash partitions. An entity joins an inventory
through `inventory = ["kernel.text:<symbol>", ...]`. Each named item is graded by the
strongest current claim about the entity: `verified` (a verified claim), `boundary` (an
open frontier item), or `decoded` (inference only).

`docs/images/research-status.svg`, shown in the README "Live Status Telemetry" section, is
rendered from this coverage by `python -m tools.research.render_research_status --write`.
It shows how much of each inventory the map describes. It is not a firmware compatibility
figure, and unmapped items are only not yet summarised.

The kallsyms and ELF sources are local and not tracked in Git. Their totals are recorded in
`inventories.toml` and re-verified, together with the source SHA-256 and every symbol that
an entity names, whenever the source file is present.

## Deep curation

Most claims outside the pilot were written from titles, lead paragraphs and consolidated
documents. [CURATION_PLAN.md](CURATION_PLAN.md) is the long-term plan for contributors to read
every record in full and verify it claim by claim. Progress is tracked per area in
`meta.deep_reviewed` and shown by `python -m tools.research.knowledge_map todo`.

## Maintenance rules

1. When a new evidence record changes what is known, update the area source in the same
   change, then run `build`.
2. Do not edit a claim in place when it turns out to be wrong. Add the new claim, set the
   old one to `retracted` or `refuted`, and point `superseded_by` at the new one.
3. Cite E-IDs. Where two records share a number, qualify the reference with a filename
   fragment, such as `E0386:retraction` or `E0386:runb16`.
4. `python -m tools.research.knowledge_map check` (also run by
   `tests/test_knowledge_map.py` in the guardrail job) fails on unknown or ambiguous
   evidence, dangling entity links, broken supersession chains, entities with no claim,
   and generated files that are out of date.
