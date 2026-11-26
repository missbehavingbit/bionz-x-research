# Contributing

Contributions should improve a documented Sony BIONZ X / ILCE-6400 contract, fix a reproducible defect, or clarify evidence and its limits. For larger work, open an issue describing the current boundary, proposed experiment and expected observation.

## Work in the main repository

Coordinate research in [the main repository's issue tracker](https://github.com/ivica00/bionz-x-research/issues) before starting substantial work. Use one tracking issue per camera model, with firmware revisions and open questions recorded in that issue. Post partial findings there and open a draft pull request as soon as you have a verified result. Link the evidence and identify the remaining boundary so others can build on your work.

Use branches or working forks to prepare pull requests. Please avoid launching a separate repository or maintaining a long-lived fork for work that fits the shared platform. If you already maintain one, link prominently to the upstream repository in your README, credit the original contributors, describe your changes, and send reusable fixes and findings upstream in focused pull requests. The [MIT License](LICENSE) requires retaining its copyright and permission notices in copies or substantial portions of the software.

Official releases, roadmap decisions, and maintainer support are coordinated in the main repository. For an issue encountered in a fork, include the fork commit and reproduction steps against the current upstream default branch or an official release. Keep fork-specific support in the fork's tracker.

When preparing release notes, credit contributors by camera model or shared feature and link their pull requests or evidence records. Preserve author attribution when bringing in work from an existing fork.

## Multi-device BIONZ X support

The execution platform is built against the Sony a6400 but models the shared BIONZ X (`CXD90045`) contracts, so owners of other bodies (a6100, a6600, a7 III, ZV-E10 or similar) can join without forking the code. Start with [docs/ADDING_A_BIONZX_CAMERA.md](docs/ADDING_A_BIONZX_CAMERA.md): it explains the `TargetProfile` schema in `src/a6400/targets/`, the read-only probe in `probes/`, and how to submit a profile with an evidence receipt. A partial, honest profile is a valid contribution; a complete guess is not.

## UVC/PTP workstreams

For UVC video and PTP mode, start with [docs/CONTRIBUTING_UVC_PTP.md](docs/CONTRIBUTING_UVC_PTP.md). It lists scoped tasks with acceptance criteria, the relevant evidence records and the labels (`uvc`, `ptp`, `good first issue`, `boundary`). Claim a task by commenting on the matching issue.

## Make a focused change

- Read [AGENTS.MD](AGENTS.MD), the relevant evidence and the [research workflow](docs/LIVE_CAMERA_EMULATOR_WORKFLOW.md).
- Preserve original inputs and existing policy baselines. Stop explicitly at unsupported behavior; do not replace unknown services with success values or host-written camera decisions.
- Keep physical observations, original-code execution, fixture results and inferences distinct. Record firmware/input hashes, commands, results, failures and remaining limitations in `docs/evidence/` for material findings.
- Never include proprietary firmware, extracted binaries, backups, raw RAM captures, credentials or generated payloads. Use constructed fixtures where possible.
- **Language policy:** Use exclusively English for all documentation, evidence records, code comments, docstrings, UI strings, pull requests, and commit messages.

## Validate

No camera is needed for ordinary checks. Install the pinned offline dependencies once and run the suite from the repository root:

Use Python 3.11 or newer. The upgraded research environment is `.venv`
(Python 3.15.2); the preserved `venv/` commands below remain valid for the
previous environment. See [toolchain qualification and rollback](docs/TOOLCHAIN.md)
and the optional [research/build dependencies](requirements-tools.txt).

```powershell
# Windows (PowerShell), using the existing environment
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m unittest discover -s tests -q
```

```bash
# Linux / macOS
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -q
```

The repository-structure, target-profile and status-synchronization contracts are standard-library checks that CI runs on every pull request:

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_repository_layout tests.test_target_profiles tests.test_research_status_sync -v
.\venv\Scripts\python.exe -m tools.research.render_research_status --check
```

Run focused checks during implementation and the full suite after substantive code changes. Report actual failures and environment limitations; do not describe historical test counts as a new run. Documentation-only changes need link/content checks. `requirements.txt` pins the offline suite only: physical experiments and local firmware inputs are not reproducible from a fresh clone.

Local captured-boot tests share the declared dump directory in
`qemu-a6400/inputs.local.json`, including its manifest/checksum pins. This local
configuration is ignored by Git; do not commit workstation paths or firmware.
Without that configuration, the legacy `artifacts/dumps/a6400-nflasha-readonly`
location remains supported. A configured capture with bad metadata fails rather
than silently falling back. Per-image size and SHA-256 checks remain unchanged.

Opt in to the unpacked-library integration tests before local pre-push checks:

```powershell
$env:A6400_UNPACKED_ROOT = (Resolve-Path .\firmware).Path
.\.venv\Scripts\python.exe -m unittest tests.test_callgraph tests.test_capability_path tests.test_cmd_search tests.test_cross_model_platform tests.test_elf_symbols tests.test_fb0_absence tests.test_ipc_edges tests.test_update_chain -v
```

In WSL, set `A6400_UNPACKED_ROOT` to the Linux path of that same unpacked-body
directory. Run compiler-dependent and native QEMU checks in Linux, not just
Windows Python. Probe host checks use the installed default WSL distribution
and support a temporary output directory outside the repository.

A suite reported as `OK (skipped=N)` has not validated the skipped checks.
Before pushing, record which checks actually passed on which platform and
classify every remaining prerequisite. Missing historical payloads or
other-model captures are unvalidated coverage, not successful tests; never
replace them with another camera's input or relax hash/assertion guards.

### Pylance MCP

If `pylanceRunCodeSnippet` reports `workspaceRoot is required`, call
`pylanceWorkspaceRoots` and pass the returned repository URI as `workspaceRoot`
on the snippet call. An open VS Code folder is not necessarily a configured MCP
default. `workingDirectory` controls where the snippet runs, not which
workspace Pylance uses.

## Documentation sync

`README.md` and the generated `docs/images/research-status.svg` dashboard are checked against `STATUS_SNAPSHOT` / `TELEMETRY` in `tools/research/a6400_status_data.py`. When a milestone changes the latest evidence record, the evidence count or the recorded suite count, update the data module, regenerate the dashboard with `python -m tools.research.render_research_status --write`, and let `tests/test_research_status_sync.py` + `tests/test_research_status_dashboard.py` prove that the README, the dashboard and the inventory agree. The generator also pins a content-hash query (`?v=...`) on the README image link, so a refreshed dashboard gets a new URL instead of a stale cached render.

## Physical experiments

Device operations require explicit scope and a reviewed plan. Preserve receipts before cleanup; verify restoration rather than inferring it from a host timer. Emulator SD access stays read-only. Do not format, flash, replace drivers or activate experimental UVC as part of routine tests. The current configured-session teardown boundary is unresolved; read E0210–E0218 before proposing another activation. Root [AGENTS.MD](AGENTS.MD) records the owner's existing journal authorization and storage restrictions; it is not blanket permission for contributors to write to a device.

## Pull requests

Explain the problem, resulting behavior, evidence and validation. Link the relevant issue/evidence, state which paths are simulated or physically verified, and list remaining limits. Keep unrelated changes separate. Update status documentation when an evidence boundary changes; preserve superseded findings as history.
