# Verified research toolchain

## Scope and versions

Qualified on 2026-11-17. This upgrade covers offline research, emulation and build
tools, not the Android/Flutter applications, camera firmware, drivers or system
packages. Stable releases were selected; QEMU 11.2.0-rc0 and Node prereleases were
excluded.

| Component | Current research environment | Preserved baseline |
| --- | --- | --- |
| REA | 6.9.0 | 6.7.0 |
| Ghidra | 12.2 | 12.1.4 |
| Java for Ghidra | Temurin JDK 27.0.1.0.2+1 | OpenJDK 21 |
| Node for REA | 26.13.1 LTS | WSL Node 22.22.1 |
| npm for the REA runtime | 12.2.2 | WSL npm 9.2.0 |
| Patched QEMU | 11.1.3 | 10.1.0 |
| Research Python | 3.15.2, Windows and WSL | Existing Windows 3.14.6 environment |
| pip / uv | 26.3 / 0.13.9 | Existing environment unchanged |
| Meson / Ninja / CMake packages | 1.12.1 / 1.13.2 / 4.4.5 | Distro tools unchanged |

[tools/toolchain.json](../tools/toolchain.json) records the current and legacy
REA profiles, archive URLs/checksums and exact QEMU source commit.
[requirements-tools.txt](../requirements-tools.txt) pins the optional research
environment. The four minimal offline dependencies in
[requirements.txt](../requirements.txt) were already current and remain
unchanged. Python 3.11 is the minimum because Pillow 13 requires it.

Primary releases:
[REA 6.9.0](https://github.com/morluto/rea/releases/tag/rea-agents-6.9.0),
[Ghidra 12.2](https://github.com/NationalSecurityAgency/ghidra/releases/tag/Ghidra_12.2_build),
[QEMU downloads](https://www.qemu.org/download/),
[Node 26.13.1 checksums](https://nodejs.org/dist/v26.13.1/SHASUMS256.txt),
and [Python 3.15.2](https://www.python.org/downloads/release/python-3152/).
The QEMU base is `v11.1.3`, commit
`005c61c0ef6f6ae615283440b4bb7be51b2c6ddd`, not a floating upstream branch.

## Using the installed environments

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

Activate `.venv` or select its interpreter in VS Code when the latest research
Python is wanted. The existing `venv/` was neither replaced nor upgraded.
Installing the research packages in another environment uses
`python -m pip install -r requirements-tools.txt`.

The WSL research environment is
`/home/administrator/a6400-rea-tools/build-tools-3.15.2/`.
Versioned REA, Node, Ghidra and JDK installations live beside it. User-local
`rea`, `ghidra`, `node`, `npm`, `npx` and QEMU launchers are under
`/home/administrator/.local/bin`; a new WSL login shell selects them. Explicit
launches do not depend on a shell refreshing its PATH:

```powershell
wsl.exe --exec /home/administrator/.local/bin/rea --version
wsl.exe --exec /home/administrator/.local/bin/rea doctor --provider ghidra --json
wsl.exe --exec /home/administrator/.local/bin/ghidra
```

The Windows Start menu also has **Ghidra 12.2 (WSL)**. Its 12.1.4 shortcut and
the root-owned `/usr/local/bin` legacy launchers remain available. No system
Java, Python, Node or global MCP registration was replaced. An already-running
session or an isolated worktree can therefore retain its previous tools.

The batch launcher defaults to the `current` profile. `A6400_REA_TOOLS_ROOT`
can relocate its relative installation paths to another absolute Linux root.
Every fresh session checks the REA server version; query evidence must identify
the selected Ghidra version and the original target hash.

## Workspace MCP integrations

Installed and fixture-qualified on 2026-11-17, separately from the environments
above. These are optional host analysis tools, not emulator execution backends.
The source revisions and source-archive SHA-256 values are pinned in
[`workspace_mcp`](../tools/toolchain.json).

| Integration | Installed source | Workspace skill | Enabled tools |
| --- | --- | --- | --- |
| [PyGhidra-MCP](https://github.com/clearbluejar/pyghidra-mcp/tree/f367da41568e6ef5d967c63b56bf640b1a46c3c2) | 0.3.0, `f367da41568e` | [pyghidra-analysis](../.github/skills/pyghidra-analysis/SKILL.md) | 12 |
| [Ghidra MCP](https://github.com/bethington/ghidra-mcp/tree/5f6c416ffe973e834644609aff1a2f7f1b93668e) | 7.0.0 with pinned post-release changes, `5f6c416ffe97` | [ghidra-dataflow](../.github/skills/ghidra-dataflow/SKILL.md) | 16 |
| [Reverify](https://github.com/2akouwu/reverify/tree/f32ea84ccd6bb2aff90eca711ccfdbf04e6b8923) | 0.11.0 with `full` extras, `f32ea84ccd6b` | [reverify-claims](../.github/skills/reverify-claims/SKILL.md) | 6 |

The skills are repository-authored usage guides; these upstream snapshots did
not contain standalone `SKILL.md` packages. No user-wide skills, agent hooks,
global MCP configuration, system packages or existing Ghidra extensions changed.

### Availability and storage

The native Copilot configuration is [workspace-root `.mcp.json`](../.mcp.json),
not the deprecated editor-only `servers` format. Start a new Copilot session
in this workspace, or reload the workspace, and approve the servers when the
client prompts. The skill commands are `/pyghidra-analysis`,
`/ghidra-dataflow` and `/reverify-claims`. Command/protocol checks below do not
claim that an already-running editor session has reloaded its customizations.

The `ghidra` and `pyghidra` entries launch `wsl.exe` in `REA-Ubuntu-24.04` from
the repository root. `workspace_mcp.ghidra_install_root` selects
`~/a6400-mcp-tools/20261121` for their private Ghidra 12.2, Temurin JDK 27,
Python 3.12 virtual environments and workspace data. This installation is
independent of the working REA/Ghidra backend. On another host, provision a
matching installation and adjust the distro and install root; the launcher
also accepts an explicit `--install-root` override. Provision the model in
advance; it is not installed by the launcher.

The original 2026-11-17 qualification ran in `Ubuntu-26.04`. All three
environments in that run used distro Python 3.14.4 under
`/home/administrator/a6400-mcp-tools/20261117/envs/{pyghidra,ghidra,reverify}/`.
The Reverify entry still expects that separate installation.
Pinned source archives, sources, build output, package inventories and receipts
live in that same private installation root. Existing Windows and WSL research
environments remain unchanged. The installation used the already-qualified uv
binary, three isolated `uv venv`/`uv pip install` operations, and:

```bash
bash ./gradlew --no-daemon --console=plain --max-workers=2 buildExtension
```

That build ran in the pinned Ghidra MCP source with the qualified Ghidra 12.2
and JDK 27 paths. It succeeded with upstream deprecation warnings. The published
7.0.0 extension ZIP targets Ghidra 12.1.4 and was deliberately not installed.
The headless JAR is used directly; the REA Ghidra installation is not modified.
The bridge's MCP `initialize` response reports SDK version 1.30.0; its installed
distribution and Java backend identify version 7.0.0. Retain all identities.

The installation's `workspace/` directory holds staged inputs, projects and
the Reverify ledger. PyGhidra's persistent project is
`workspace/pyghidra/sony-research.gpr`; use returned, hash-suffixed program
identifiers and confirm metadata hashes. Ghidra MCP projects must also stay
under `workspace/`; pass that directory as `searchDir` to `list_projects`
instead of searching the server's private home. Open the actual `.gpr` file
rather than the extensionless path returned by project creation. Do not share
a live project between providers or delete its locks to work around concurrent
ownership.

### Isolation and retained limits

The [launcher](../tools/research/mcp_workspace.py) uses a private home/cache,
disables telemetry and debugger/script integration, and preserves the existing
REA policy. Ghidra MCP uses an authenticated ephemeral loopback port and a
private Unix socket. Its token exists only in child-process environments;
instance auto-discovery is disabled, program selectors are required, and its
filesystem endpoints are restricted to the installation's `workspace/` root.
The launcher waits for a valid TCP port and the actual analysis schema before
starting the bridge, and stops its owned backend when the stdio client exits.

Reverify's adapter permits only staged inputs, byte/structural claims and ledger
reads/recording. It rejects instruction, CPU-emulation, source-execution and
equivalence claims, arbitrary input paths and ledger deletion. Its upstream
`full_fidelity` flag indicates installed dependencies, not verified Thumb,
camera or OS fidelity. CLI installation does not qualify its fallback engines.

PyGhidra's local CPU embedding model is `all-MiniLM-L6-v2`, 384 dimensions.
Its 83,178,821-byte archive was checked against SHA-256
`913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3`;
the URL and identity are pinned in the manifest. Only public model weights
were downloaded; no firmware or queries were uploaded to an inference service.
Ghidra analysis and code/string indexing have separate readiness states.
Wait for the relevant state and check actual results, not just queue acceptance.
The environment settings are not a filesystem or network sandbox.

Fixture qualification does not resolve the historical large-library cold-import
timeout, qualify a processor dialect for real firmware, or establish new camera
contracts. The tiny fixtures lack CPU attributes; both loaders selected
`ARM:LE:32:v8`, which is recorded as an assumption, not a Cortex-A5 qualification.

### Validation and receipts

Local receipts, including failed attempts, are retained under the installation's
`receipts/` directory and copied to ignored
`artifacts/tools/mcp-install/20261117/wsl-receipts/`. The completed search-capable
run is `qualification-20261117-163213/report.json`.

| Check | Result |
| --- | --- |
| `uv pip check --python <each-isolated-environment>/bin/python` | PASS, all three environments |
| `.\.venv\Scripts\python.exe -m unittest tests.test_mcp_workspace tests.test_rea_toolchain -v` | PASS, 13 checks: nine MCP policies/protocol/readiness cases and four preserved REA profile cases |
| `.\.venv\Scripts\python.exe -m artifacts.tests.mcp_workspace_windows` | PASS: exact configured Windows commands, initialization, tool discovery, all 34 selected tool names, one real call per server and clean exit |
| WSL bridge Python: `python -m artifacts.tests.mcp_workspace_qualify` | PASS: both original fixture instruction sequences and SHA-256 identities, ARM/Thumb decompilation in both providers, data-flow chains, literal/semantic search, restart persistence and explicit rejected requests |
| Final full unittest discovery with physical-volume construction explicitly denied | PASS, 1,935 run, 133 skipped; no failures or errors |
| `wsl.exe --exec /home/administrator/.local/bin/rea doctor --provider ghidra --json` | PASS, 9/9 scoped checks; missing unrelated providers/REA global skill remain informational |
| Skill metadata, relative documentation links and discovery names | PASS, three skills |
| `.\.venv\Scripts\python.exe -m tools.research.ci_check --summary` | PASS, 5/5 guardrails including 86 guardrail tests |

The full-suite isolation invocation, from the repository root, was:

```python
import unittest
from unittest import mock

suite = unittest.defaultTestLoader.discover('tests')
with mock.patch('tools.research.sdhci_bridge.PhysicalVolumeSource',
                side_effect=PermissionError('Physical media disabled for offline validation')):
    result = unittest.TextTestRunner(verbosity=0).run(suite)
assert result.wasSuccessful()
```

This prevents the pre-existing SD report test's default physical-volume open;
it changes neither production code nor test assertions. An earlier unguarded
run passed 1,932 cases before the three protocol/readiness regression tests
were added; its unavailable-drive fallback receipt is preserved separately.
MCP qualification used only constructed files under `artifacts/tests/`, not
camera access or proprietary firmware.

Preserved failures include a fixture-helper import attempted from the deliberately
minimal MCP environment, premature queries before asynchronous imports completed,
an extensionless project path, the backend publishing `tcp_port=-1` before TCP
readiness, and an interrupted embedding download. The fixture helper was rerun
with the existing complete research Python; the launcher race and null-error
classification were fixed and tested; the model was verified and search rerun.
No failed analysis or incomplete index is promoted to successful evidence.

To disable these additions, stop the three workspace MCP servers and remove only
their entries from `.mcp.json` and the three corresponding skill directories.
Keep the isolated installation, projects and receipts unless their removal is
explicitly intended. No qualified REA/QEMU rollback or global configuration
change is needed.

### Pre-push validation follow-up (2026-11-17)

**The earlier 133-skip result is not a complete pre-push qualification.**
The downloaded a6400 dumps are present and match the pinned identities; many
tests previously checked an incomplete legacy directory instead.

[The shared capture resolver](../project_paths.py) now reads the existing
`qemu-a6400/inputs.local.json`, verifies its manifest/checksum pins, and translates
its Windows drive path when used in WSL. Boot-set loading, EXBL defaults and
the corresponding test gates use that same location. Unconfigured clones retain
the legacy directory; invalid configured inputs fail explicitly. No original
dump, size/hash pin or firmware assertion changed.

The probe builder now uses the installed default WSL distribution instead of
requiring a distro named `Ubuntu`. Its host-check report also supports the
external temporary output directory used by the report tests. These changes
unblocked the actual compiled host fixture and its report-contract tests.

Two missing offline fixtures were restored without camera access:

- The decompressed updater kernel was generated from the verified updater
  boot-set member: 3,581,952 bytes, SHA-256
  `ae0d4448a803850b082a80f3b9fc8dcc733d692c9d1125bf838258b7c84ff925`.
- The tree C fixture was rebuilt with the documented flags, linker map and
  unchanged source. Its object and linked ELF reproduce the historical hashes
  `54f3b119fad3540b807b5e260064de274125e1d5c11c62d07f6b628fd517f474`
  and `21dd583f3f474148bd423874c23f3e3d210471e2d0c1021f5e14c246cda17df1`.
  This is not a rebuilt or audited deployable kernel module.

The missing ARM compiler was restored privately under
`/home/administrator/a6400-rea-tools/arm-gcc-15.2.0-prepush/`, not installed as
a system package. Distro packages were checked against their indexed SHA-256
values. The compiler identifies as GCC 15.2.0; an explicit
`-B/usr/arm-linux-gnueabi/bin/` selects the existing ARM binutils instead of the
host assembler. The unsuccessful initial assembler selection is preserved in
the build receipt.

Receipts and the machine-readable **blocked** push gate are under
`artifacts/tools/prepush-validation/`; `coverage-gate.json` lists every
unvalidated individual test and its prerequisite. Results:

| Check | Result |
| --- | --- |
| Windows: `python -m unittest tests.test_nflasha_boot_set tests.test_exbl_image tests.test_machine_contract tests.test_kernel_backing tests.emulator.test_exbl_b0_boot tests.test_probe_build tests.test_probes_report -v` | PASS, 58 checks, no skips |
| Linux: `python -m unittest tests.test_menu_touch_policy tests.test_qemu_emmc tests.test_probes_report -v` | PASS, 50 checks, no skips |
| Windows unpacked-library opt-in selector documented in CONTRIBUTING | PASS, 77 run, one skip: no available body carries command `0x413b` |
| Windows: `python -m unittest tests.test_tree_deepcopy_model tests.emulator.test_data_abort -v` | PASS, 56 run, one skip for the unavailable deployable-module audit receipt |
| Linux full discovery, unpacked-root opt-in and explicit physical-volume denial | 2,004 collected; 1,956 individual tests passed, zero failures/errors; 45 skip records cover 48 unvalidated tests |
| Windows full discovery with the same input policy | 2,004 collected; 1,911 individual tests passed, 10 status-sync failures, zero errors and 80 skip records |
| Fresh Windows `python -m unittest tests.test_research_status_dashboard tests.test_research_status_sync -q` | PASS, 39 checks; the prior process held E0532 while concurrent documentation updates produced E0533 |
| Final `python -m tools.research.ci_check --summary` | FAIL, 3/5 checks passed; status SVG synchronization and eight guardrail assertions failed during further concurrent updates |
| Latest targeted status diagnostic, same 39-test selector | FAIL, one assertion: README reports both 1,965 and 2,000 tests while its snapshot expects only 2,000 |

The complete runs used `.venv` Python 3.15.2 on Windows and the existing WSL
research Python. Their report files preserve the full selected test IDs,
actually passed IDs, skip reasons, failure traces, interpreter identity,
selected input roots and path-helper hashes. The physical-volume constructor
was explicitly denied as in the earlier isolated full-suite invocation.
The fresh 39-test rerun does not relabel the failed Windows full run as passing.
Subsequent documentation changes also mean that fresh pass is not the final
worktree's status qualification. Those unrelated in-progress files were not
overwritten to force a green result.

The remaining 48 individual tests need:

| Unvalidated cases | Prerequisite |
| ---: | --- |
| 18 | E0351 compiled fixtures and the matching ZV-E10 reference |
| 15 | E0154/E0322 descriptor and overlay manifests |
| 5 | Built touch/mod-loader payloads and native guard |
| 3 | Audited carrier/helper/tree module outputs |
| 2 | E0667 bridge and original kernel fixture |
| 2 | ILCE-7C 2.00 reference pulls |
| 1 | Captured `libUsbControl` live-library evidence |
| 1 | Pinned prebuilt USB debugger binaries |
| 1 | An unpacked body containing command `0x413b` |

The user confirmed that an archived artifact directory is not available.
Reproducible outputs may be rebuilt from their actual prerequisites, but missing
historical receipts and other-camera inputs must not be fabricated or replaced
with a6400 data. **No push was performed; complete qualification remains blocked
on this coverage and a stable final worktree.**

## QEMU build and preserved storage behavior

Run the setup script from the repository root in WSL:

```bash
bash qemu-a6400/setup-qemu.sh
export PATH="$HOME/a6400-rea-tools/build-tools-3.15.2/bin:$PATH"
cd "$HOME/sony-a6400-qemu-11.1.3/build"
../configure --python="$HOME/a6400-rea-tools/build-tools-3.15.2/bin/python" \
  --target-list=arm-softmmu --enable-sdl --disable-werror --disable-docs
ninja -j2 qemu-system-arm qemu-img qemu-io
```

The versioned checkout preserves `$HOME/sony-a6400-qemu`. Setup verifies the
pinned commit before copying anything and distinguishes an already-applied
patch from a conflict. It never silently skips a conflicting patch.

The port retains the previous CPU register model, timers, storage offsets and
CMD1 sector/byte behavior. Source adaptations cover moved core headers,
`DEFINE_MACHINE_ARM`, physical-memory access and the loader error API.

An upstream geometry check initially rejected the unchanged 2 GiB backend after
subtracting its two 4 MiB boot partitions. The Sony board now explicitly enables
the default-off `x-sony-legacy-backend` compatibility property. Its bounded
exception requires a power-of-two whole backend, no RPMB partition and a
512 KiB-aligned, CSD-representable user area. Other eMMC devices retain the
upstream default checks. No input was resized or silently repadded: the
2 GiB fixture still reports 2040 MiB of user data, as on the old build.
Invalid whole-backend and alignment cases remain rejected.

[Native tests](../tests/test_qemu_emmc.py) run only with a local Linux Sony QEMU
build and use constructed sparse images with paused CPUs. They never access
USB, physical storage or the camera. `A6400_QEMU_DIR` selects another build,
including the preserved 10.1.0 control.

## Qualification results

Receipts, package inventories, preserved failures and input/tool hashes are in
the local, ignored `artifacts/toolchain-upgrade/20261117/` directory.
Successful REA query evidence remains under `artifacts/tools/rea/`.

| Check | Result |
| --- | --- |
| Original Windows suite, before changes | 1914 collected; PASS, 126 skipped |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests -q` | 1926 collected; PASS, 133 skipped |
| `.\venv\Scripts\python.exe -m unittest discover -s tests -q` after changes | 1926 collected; PASS, 133 skipped |
| WSL research Python: `python -m unittest discover -s tests -q` | 1926 collected; PASS, 98 skipped |
| `.\.venv\Scripts\python.exe -m tools.research.ci_check --summary` | PASS, 5/5 guardrails including 86 guardrail cases |
| WSL research Python: `python -m probes.build_probe --host-check --output-dir <new-output>` | PASS; help/smoke exit 0, report valid, fixture removed |
| `python -m unittest tests.test_qemu_emmc -v`, each QEMU version | 7/7 PASS on both builds |
| Current REA scoped Ghidra doctor | 9/9 checks PASS |
| Original ARM, Thumb and C++ shared-library queries | PASS; 677 instruction listings exactly match the old provider |
| Same-version REA cache replay | 2/2 hits; no REA/Ghidra process started |
| Original files-policy interpreter replay, both Python versions | Identical complete reports; expected boundary exit 2 |
| Captured firmware boot, each QEMU version | Four cores, original av-cam entry, four successful serial commands, clean QMP exit |
| Pristine QEMU patch / reverse checks and wrong-version guard | PASS; old checkout rejected before copying |
| Preserved QEMU binaries/source patch, old Python packages and prepared boot inputs | Unchanged |

The seven additional Windows skips are the new Linux-native QEMU checks, not
lost coverage of the original suite. The five other new cases check toolchain
selection, server identity and cross-version cache separation.

The interpreter replay used
`python -m tools.research.probe_interpreter --syscalls files --instructions 3000000 --timeout-us 15000000 --report <new-report>`.
Its `unsupported_filesystem` stop is not a successful application boot.

Both QEMU controls used the retained captured-boot worktree at
`c29f0324e14c002fddd2fa4c8962add4ce9256b9`, with identical replay-helper SHA-256
`79ae8163d3bdcb1c09780be3e3097646e2d8c3dbdaafd4b65c2604472f6433cc`
and input-manifest SHA-256
`bf12cfa60d64560d6db6327e42e81fd5d81b96c883c90879228ed67364cdde6d`.
The 90-second probes used `ULOGIO=`, `SUBMICRO_PAGE=`, the appropriate
`A6400_QEMU_DIR`, and read `/proc/version`, the online CPU set,
`/proc/interrupts` and `/proc/udm` through the emulated serial shell.
That worktree supplies `tools.research.qemu_boot_probe`; its other changes were
not imported into this branch. Existing peripheral assumptions remain; these
checks do not establish GUI rendering, complete device emulation or hardware
deployment safety.

### Retained limits and installation failures

- Cold, full `libObj.so` analysis exceeded the unchanged 330-second startup
  deadline on both Ghidra versions. Neither failed batch is usable evidence.
  No timeout, analyzer coverage or firmware guard was weakened to claim success.
- Windows direct package downloads encountered a TLS handshake failure, and the
  configured package mirror did not yet contain that day's uv release. Downloads
  were obtained through working, certificate-verified WSL TLS and installed from
  a recorded local wheelhouse. No certificate checks or global package settings
  were disabled. PyYAML 6.0.3 uses its explicit pure-Python build on Windows.
- System package upgrades and a new ARM cross-compiler were not attempted.
  The existing distro compiler built QEMU; optional cross-compiler tests remain
  skipped where that compiler is absent.
- CI now selects Python 3.15.2 with `actions/checkout@v7.0.1` and
  `actions/setup-python@v7.1.0`. Hosted Actions execution is not a local test
  result; the next workflow run must qualify the hosted environment.

## Rollback without modifying evidence

Use the preserved Python directly:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -q
.\venv\Scripts\python.exe -m tools.research.rea_wsl_batch --toolchain legacy --reuse-evidence --procedure Data_SearchId --report-dir artifacts/tools/rea/NEW_LEGACY_REPORT
```

`--toolchain legacy` selects REA 6.7.0, Ghidra 12.1.4 and JDK 21. Change only
`default_rea_profile` in the toolchain manifest to restore that default for
future batches. Historical evidence is preserved and is never relabelled:
12.1.4 and 12.2 cache entries remain separate.

Set `A6400_QEMU_DIR=/home/administrator/sony-a6400-qemu` to use the old QEMU
binary with the existing boot workflow. Do not run the new setup script against
that old checkout: it deliberately refuses to overwrite a different release.

## Upstream rebase integration (2026-11-18)

The local CPU/USB/GPU/toolchain work is integrated with
`ivica00/bionz-x-research` commit
`e9c93f1c48de623fece8083960e8e2be37e78924`. The newer README structure, eight
profiles, Mod Loader v2, rear-panel controls and QEMU peripheral models remain;
the parallel original-instruction evidence records are retained separately.
Existing records and their dated suite totals were not rewritten.

The 11.1.3 patch now builds every incoming peripheral source and preserves
the incoming debug-state checkpoint transition. Header, input and display
adapters use the actual 11.1.3 APIs: Linux keycodes are converted to QKeyCode,
and completed synchronous display refreshes return their completion state.
The old register-only Sugilite stub is not restored over the executing
P3D/PPF implementation. No new firmware/device reply is supplied by these
host API adaptations.

Native validation used a separate local checkout,
`/home/administrator/sony-a6400-qemu-11.1.3-rebase-20261118`, at the pinned
QEMU commit above. The existing default native checkout, previous Python
environment and other research worktrees were not modified. Rebuild the
selected checkout before expecting a previously installed binary to contain
the merged models.

Local receipts, including failed builds and the first failed suite, remain in
`artifacts/validation/origin-rebase-20261118/`.
`native-build-identities.json` matches all 15 native overlay files to the
repository and records the patch and binary identities. The resulting
`qemu-system-arm` SHA-256 is
`f88c1a77c80f636eef82cf20cd9aeb566614bd03f844c10b1a60a71feb5508f4`.
The probe selects its repository patch reference from the running QMP version,
not a hardcoded 10.1.0 filename. These references are explicitly distinguished
from the separately recorded native compilation inputs.

| Exact command/check | Result |
| --- | --- |
| `A6400_QEMU_DIR=/home/administrator/sony-a6400-qemu-11.1.3-rebase-20261118 bash qemu-a6400/setup-qemu.sh`, followed by the configure command above and `ninja -j2 qemu-system-arm qemu-img qemu-io` | PASS; `qemu-build-04.log`, with failed API-port attempts 01-03 retained |
| WSL research Python: `python -m unittest tests.test_qemu_emmc -v`, with that same `A6400_QEMU_DIR` | PASS, 8/8; eMMC sector/byte addressing, 2 GiB geometry/refusals, rear-key ADC/wheel state and both synchronous display callbacks |
| `.\.venv\Scripts\python.exe -m unittest tests.emulator.test_timer_gic_model tests.test_qemu_boot_inputs tests.test_qemu_emmc -v` | PASS, 32 run, 8 Windows-native skips; `focused-integration-02.log` |
| Full offline discovery with unpacked-root opt-in, physical-volume denial and per-case collection, below | PASS, 2,095 discovered / 2,091 runner cases, 2,004 individual passes, 88 skip records, zero failures/errors; `full-suite-windows-02.json` and `.log` |
| Original updater replay, below | PASS; 800M bound, native BusyBox Thumb entry, ash echo/pwd roundtrip, no unmapped access or manual Thumb recovery |
| Captured main-firmware QEMU replay, below | PASS; four CPUs, four successful serial commands, QMP stopped state and clean exit 0 |

The full suite's class-level skips explain the difference between discovered
cases, runner cases and skip records. Neither skipped coverage nor the earlier
blocked pre-push gate is relabelled as validated. The suite receipt retains
individual pass/skip identities and verifies unchanged source hashes across
the run. E0578 remains the latest research record; this integrated suite is
not additional Open Gate or physical-camera acceptance.

The first full run had one `UC_ERR_NOMEM` while the incoming IRQ-mask fixture
constructed several full-size machines inside one test. Its constructed
timer/GIC fixture now declares 8 MiB RAM consistently in both the model and
ATAGS; all interrupt, saved-SPSR and exception-return assertions remain.
Production RAM banks and real-firmware inputs are unchanged. That failed
2,090-case run also overlapped the native API-port edits and is not reported
as a stable passing snapshot.

Full offline validation policy, from the repository root in PowerShell:

```powershell
$env:A6400_UNPACKED_ROOT = (Resolve-Path .\firmware).Path
@'
import gc, sys, unittest
from unittest import mock
class Result(unittest.TextTestResult):
    def stopTest(self, test):
        super().stopTest(test)
        gc.collect()
suite = unittest.defaultTestLoader.discover('tests')
with mock.patch('tools.research.sdhci_bridge.PhysicalVolumeSource',
                side_effect=PermissionError('Physical media disabled for offline validation')):
    result = unittest.TextTestRunner(verbosity=1, resultclass=Result).run(suite)
sys.exit(not result.wasSuccessful())
'@ | .\.venv\Scripts\python.exe -
```

Original updater command, using a fresh output directory for every replay:

```powershell
$out = 'artifacts\validation\origin-rebase-20261118\NEW-UNICORN-RUN'
.\.venv\Scripts\python.exe -u -m tools.research.run_nflasha1_boot `
  --limit 800000000 --no-exclusive-fallback --sony-override --svc-dispatch `
  --cmdline-extra 'init=/bin/ash console=ttyAM0,115200n8 amba2.console=1' `
  --trace-user-mode --trace-thumb-boundary `
  --serial-input "echo REBASE_ASH_OK`npwd`n" `
  --trace-reg 0x0000C438:r0,r1,sp,lr,cpsr `
  --output "$out\boot.json" --serial-output "$out\serial.log"
```

`--trace-thumb-boundary` also keeps the auxiliary kernel log inside the new
output directory rather than replacing an older runner-default log.
`unicorn-default-01/` retains the actual report and UART bytes.
The original partition/kernel/initrd pins remain those of E0533. The merged
machine SHA-256 is
`b119175e5c0c020754ad38d9e74bd9c48e909e3be559cb8a830ac81a359a2479`.
This is a builtin-only continuation check, not qualification of the separate
post-`uname` child/COW boundary.

The 120-second native replay used the already verified captured inputs:

```bash
A6400_QEMU_DIR=/home/administrator/sony-a6400-qemu-11.1.3-rebase-20261118 \
ULOGIO= SUBMICRO_PAGE= python -u -m tools.research.qemu_boot_probe \
  --inputs artifacts/qemu-a6400/inputs-pr-preflight-20261116-1555 \
  --report-dir artifacts/validation/origin-rebase-20261118/NEW-QEMU-RUN \
  --seconds 120 --guest-command "dmesg -n 1" \
  --guest-command "cat /sys/devices/system/cpu/online" \
  --guest-command "cat /proc/uptime" --guest-command "echo REBASE_QEMU_OK"
```

`qemu-captured-boot-01/` and `firmware-replay-verification.json` retain its
actual input/binary identities, the `0-3` CPU response, guest marker, stop and
clean shutdown; all 14 combined replay checks pass. Optional ulogio/sub-micro
fixtures were unavailable and explicitly disabled, not substituted. The native
unit PPMs are constructed-model checks, not firmware frames. No new QEMU GUI
or sensor frame, timing equivalence, hardware compatibility, USB command or
physical-camera result is claimed by this integration validation.
