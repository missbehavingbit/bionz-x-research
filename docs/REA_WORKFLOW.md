# REA research reference

## REA-assisted firmware research

Use REA with Ghidra through WSL as the preferred analysis tool for substantial ARM/Thumb function, dispatcher, call-chain and data-layout investigation. Start from the current unsupported boundary and a concrete question. Reuse hash-matched retained evidence when it already answers the question; use targeted ELF parsing, `rg` and disassembly for small checks. REA assists research; it is not part of the guest execution backend.

### Verified project environment (2026-11-15)

The owner-requested installation is complete after an owner-controlled Windows
restart. WSL 3.0.5.0 runs the dedicated **`REA-Ubuntu-24.04`** distribution
(Ubuntu 24.04.5 LTS, Linux x64) as the owner's normal UID-1000 Linux user.
No automatic restart, camera access, capture privilege or firmware write occurred.

The normal user's Linux tool directory is **`~/a6400-rea-tools`**:

| Component | Installed / verified |
| --- | --- |
| REA / Node | REA 6.9.0; private Node 24.22.3 runtime |
| Ghidra / Java | Ghidra 12.1.4; full Ubuntu OpenJDK 21.0.13.0.3 |
| Firmware extraction | Binwalk 3.1.0; Unblob 26.6.4 and every reported external extractor; 7-Zip, Sasquatch/v4be, ext2/EROFS, SquashFS and UBI/JFFS tools |
| Python analysis | Separate `python`, `unblob-env` and `pwntools-env` virtual environments; `pip check` passes in all three |
| ARM / debugging | ARM cross-GCC/binutils, GDB multiarch 15.1, QEMU 8.2.2, Rizin 0.9.1, Frida 17.23.2 |
| Packet / signal | TShark and capinfos 4.2.2; sigrok-cli 0.7.2 |
| Android | REA's pinned JADX MCP 0.7.1 JAR, Apktool 2.7.0, AAPT, ADB 34.0.4 |
| WebAssembly / browser | WABT 1.0.42; matching Playwright 1.64.1 Chromium 156.0.8078.4, sandboxed local-page launch verified |
| Rust build tools | Private Rust/Cargo 1.100.0; normal-user command wrappers |

These are observed installation identities, not permission to silently upgrade.
The official Ghidra archive SHA-256 is
`ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db`.
Downloads, package manifests, lockfiles, release hashes and failed attempts are
retained privately under
`%LOCALAPPDATA%\Programs\REA\setup-receipts\2026-11-15\linux-after-restart`.
Its `completed-state.json` supersedes the preserved pre-restart handoff.
The Windows project `venv` and upstream checkouts were not reinstalled.

Pwntools 4.15.0 explicitly excludes Unicorn 2.1.4. It therefore uses its own
environment with Unicorn 2.1.2; the project-analysis environment retains the
repository's Unicorn 2.1.4 pin. No resolver constraint was weakened. Binwalk's
first build stopped at missing Fontconfig development metadata; the declared
development dependency was installed and the locked build then passed.

### Persistent connections and commands

The existing **`rea`** entries in Copilot CLI and VS Code now launch the same
normal-user WSL backend. Supported registration tooling preserved recovery
backups; readback confirms the exact command and unchanged unrelated settings.
A fresh MCP client launched that exact saved command successfully. Restart or
reconnect the REA MCP server in an already-open client to load it; another
Windows restart is not needed.

The Linux `~/.local/bin/rea` launcher sets the verified Node, Java, Ghidra,
Binwalk, Unblob, pwntools, JADX, packet, signal, browser and debugger paths.
It refuses root analysis. Paid/macOS-only engines and device-dependent runtime
access are not implied by this installation.

Windows launchers under `%LOCALAPPDATA%\Programs\REA\bin`:

- `rea-wsl.cmd`: the full Linux project toolchain.
- `rea.cmd`: the retained native Windows REA installation.
- `ghidra.cmd`: the native Windows Ghidra GUI with its private Temurin JDK.

The Windows provider's P0 boundary still does not admit the camera's ARM ELF
files; use WSL for those. Linux REA tools accept Linux paths, including `/mnt/c/`
for Windows-hosted inputs. For example:

```powershell
& "$env:LOCALAPPDATA\Programs\REA\bin\rea-wsl.cmd" doctor --provider ghidra --provider jadx --format json
```

The project batch launcher supports two explicit environment overrides:

- `A6400_REA_LAUNCHER`: absolute **Linux** path to the configured REA wrapper.
- `A6400_REA_WSL_DISTRO`: the dedicated distro name when invoked from Windows.

Setup saved both in the Windows user environment. New terminals inherit them;
an existing terminal can load them without guessing a username:

```powershell
$env:A6400_REA_LAUNCHER = [Environment]::GetEnvironmentVariable('A6400_REA_LAUNCHER', 'User')
$env:A6400_REA_WSL_DISTRO = [Environment]::GetEnvironmentVariable('A6400_REA_WSL_DISTRO', 'User')
```

When the launcher is unset, the selected [toolchain profile](TOOLCHAIN.md)
provides the runtime paths, with `--toolchain legacy` available for the
historical installation. A wrapper override must match the selected profile's
REA and Ghidra versions. An explicitly empty or invalid launcher fails instead
of falling back. Receipts record the actual launch command.

### Actual verification and remaining tool boundaries

This command completed **both** original-target Ghidra queries:

```powershell
.\venv\Scripts\python.exe -m tools.research.rea_wsl_batch `
  --target .\firmware\extracted_a6400a_v101\firmware.tar_unpacked\0700_part_image\dev\nflasha15_unpacked\lib\libsencore.so `
  --inventory .\artifacts\analysis\e0669-a6400a-offline-service-contracts\inputs.json `
  --procedure ParseCheck --instructions ParseCheck `
  --report-dir .\artifacts\tools\rea\setup-20261115-arm-batch-02
```

Result: **completed**, Ghidra **12.1.4**, real `ParseCheck` pseudocode and
instructions, normal owned-server cleanup. Source SHA-256 remains
`08804d8752c0bdfbad6e3f212cc1feaf2145e71e866a64c3f94bc4c2eca36089`.
The separate CLI ARM observation is Evidence
`ev_ce116db8142245231af0d5fa6341815474685cfa19c47c46f06afd23d154f4de`.
It decompiles the table-membership decision; it does not execute the library
or establish camera access.

The first batch used `file-offset:0x12418`: decompilation accepted that selector,
but the instruction query rejected it. That failed batch is preserved; the
corrected batch uses Ghidra's observed procedure name for both operations.
Do not treat every procedure selector as interchangeable across operations.

Fresh file-only MCP checks pass for packet client inspection, I2C decoder
inventory, a constructed WASM module, gzip signature inspection, the real ARM
ELF's layout, a manifest-only APK, and ADB/Apktool client inspection. The APK
contains no classes; this is not Android application runtime verification.

**Extraction outputs must use the native Linux filesystem.** Unblob correctly
refused to establish owner access on the initial Windows-mounted output path.
Repeating with a private absent output under `~/a6400-rea-work` passes and
reproduces the exact constructed payload SHA-256:
Evidence `ev_124d588c4c94f20e5c45498c0d71fc9049359759766231070a2e0144ff7a703d`.
No ownership or extraction guard was disabled. Copy selected verified regular
outputs to the repository only when needed; never mount or execute extracted
firmware as an installation test.

These checks and exported bundles are under `artifacts/analysis/rea-setup-20261115/`.
No live capture, USB forwarding, Android connection, camera protocol request,
daemon installation or physical dump was used. `dumpcap` has no added capture
capability or setuid bit. Windows native Ghidra's earlier constructed PE smoke
result remains separate from the now-verified Linux ARM lane.

Launcher regression validation adds five constructed, process-free cases:
explicit executable/distro argument boundaries, native Linux execution,
invalid overrides, empty distro rejection and preservation of historical
defaults. Combined launcher/cache/layout/status checks pass **39 tests**.
The physically guarded full suite runs **1,766 tests, 1 failure, 4 errors,
151 skips**. Failure/error IDs match E0669: the missing E0322 overlay input and
original-target `__pipe` symbol expectation are unchanged. A raw SD open is
denied before access; real WSL subprocesses are also denied during ordinary
unit tests. Complete validation inputs/logs are in
`artifacts/analysis/rea-setup-20261115/full-validation/`.

### Availability during E0669 (historical)

[E0669](evidence/0669-a6400a-offline-senser-dispatch-and-native-read-gates.md)
attempts the documented WSL launcher but Windows reports WSL is not installed.
The failed receipt retains zero completed queries and the unchanged input.
Connected Windows REA 6.9.0 reports Ghidra unconfigured; no existing installation
was found in the selected standard tool directories. No provider, Java or WSL
was installed. That checkpoint uses Capstone disassembly and bounded Unicorn
controls, not Ghidra output. Historical WSL paths below are not current readiness.

[E0667](evidence/0667-a6400a-debugger-abi-and-live-channel-boundary.md) verifies
that the Windows REA 6.9.0 MCP server responds and provides portable kernel-module
and ELF export inspection. Ghidra is not configured in that server environment;
no deep-analysis engine was started. This is distinct from the historical WSL
installation below. Use the live `binary_session` result rather than assuming
that a skill installation or a past environment establishes provider readiness.

[E0668](evidence/0668-a6400a-live-senser-entry-and-recovery.md) reuses the
connected portable ELF-export inspector for the hash-matched a6400A
`libsencore.so` (638 declared exports). Original service controls are replayed
separately; no deep engine or camera-side instrumentation is started.

### Historically verified WSL environment

- Current qualified stack (2026-11-17): REA 6.9.0, Ghidra 12.2, Node 26.13.1 and full Temurin JDK 27.0.1.0.2+1 through WSL 2. See [the verified toolchain, qualification and rollback guide](TOOLCHAIN.md).
- [tools/toolchain.json](../tools/toolchain.json) selects the `current` profile. REA is in `/home/administrator/a6400-rea-tools/runtime-6.9.0/`; Ghidra is in the adjacent `ghidra_12.2_PUBLIC` directory.
- The launcher selects its explicit Node, Java and Ghidra paths; no system Java or Node upgrade is required. `--toolchain legacy` retains E0030's REA 6.7.0 / Ghidra 12.1.4 / JDK 21 environment and its separate evidence cache.
- Use `tools.research.rea_wsl_batch` from the current `.venv` Python environment. It converts the target path, checks inventory and provider identities, retains structured Evidence and closes its shared MCP analysis session.
- If nested Windows-Python WSL access fails while direct WSL works, invoke this same stdlib-only module through `wsl.exe --cd <WSL-project-path> --exec python3 -u -m tools.research.rea_wsl_batch ...` as documented in E0037. Native execution uses the same installed provider and all identity guards; no dependency installation is required.
- The Windows REA/Ghidra provider rejected this ARM ELF in the recorded trial. Prefer the verified WSL route. If WSL returns `E_ACCESSDENIED` under the sandbox, retry through the normal tool escalation mechanism before diagnosing a broken installation. Report actual approval-review rejection if it occurs.
- Do not run REA setup, register agent integrations, install unrelated providers, change global configuration or reinstall the working environment as an incidental analysis step. Check the existing installation first if a tool fails. See `docs/experiments/0008-rea-tool-trial.md` for the original setup and limitations.

### Fresh Windows contributor installation

Use the [official WSL installation instructions](https://learn.microsoft.com/windows/wsl/install) to install WSL 2 and Ubuntu 26.04 LTS. Installing WSL can require administrator approval and a restart; do not restart automatically. Native Windows Node.js and Ghidra alone do not remove REA's recorded ARM ELF restriction.

The current launcher uses fixed Linux paths under `/home/administrator/a6400-rea-tools`. A normal Linux user named `administrator` reproduces this layout; it need not match the Windows account name. Keep the tool directory owned by that Linux user, not root. Install only missing prerequisites, and do not replace an existing working environment.

In the new Ubuntu distribution, install these packages as root:

```bash
apt-get update
apt-get install --no-install-recommends -y ca-certificates curl unzip procps nodejs npm openjdk-21-jdk binutils-arm-linux-gnueabi
```

Use a full JDK 21, not only a JRE. Check that the Linux Node version satisfies REA 6.7.0's requirement: Node 22.x (>=22.19), 24.x (>=24.11), or 26+. Then, as the normal Linux `administrator` user, install the matching tools in a fresh directory:

```bash
set -e
mkdir -p "$HOME/a6400-rea-tools"
cd "$HOME/a6400-rea-tools"
npm install --prefix runtime --ignore-scripts --no-save --no-audit --no-fund --package-lock=false --registry=https://registry.npmjs.org rea-agents@6.7.0
curl --fail --location --output ghidra_12.1.4_PUBLIC_20260921.zip https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.1.4_build/ghidra_12.1.4_PUBLIC_20260921.zip
printf '%s  %s\n' ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db ghidra_12.1.4_PUBLIC_20260921.zip | sha256sum --check
unzip -q ghidra_12.1.4_PUBLIC_20260921.zip
export GHIDRA_INSTALL_DIR="$HOME/a6400-rea-tools/ghidra_12.1.4_PUBLIC"
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
node runtime/node_modules/rea-agents/scripts/rea.mjs --version
node runtime/node_modules/rea-agents/scripts/rea.mjs doctor --provider ghidra --json
```

The scoped doctor must report `healthy: true` and no failed `scope_checks`. Missing optional providers or agent skills can still make `environment_healthy` false; they are not prerequisites for this workflow. No REA setup or global agent registration is required.

Confirm that this is the distribution selected by `wsl.exe --list --verbose`: the Windows launcher uses WSL's default distribution. Do not silently change another working default. Finally, run the bounded example below with a locally supplied, inventoried target and a new report directory, without `--reuse-evidence`, to verify actual decompilation and unchanged input hashes. Ghidra's GUI can be started through WSLg using `"$GHIDRA_INSTALL_DIR/ghidraRun"` from the configured Linux shell.

### Query and evidence workflow

Use `python -m tools.research.dependency_plan --target <inventoried-module> --function <name> --depth 1 --max-nodes 32 --report-dir artifacts/analysis/<new-directory>` to prepare targeted queries and reuse verified analysis. `--pc-offset` accepts .text-relative offsets only; `--cross-module` follows unique static symbol candidates, not proven loaded exports. Follow `docs/DEPENDENCY_WORKFLOW.md`; keep unsupported/indirect/IPC dependencies explicit. Generated batches use native WSL and `rea_wsl_batch --reuse-evidence`; failed, mismatched, changed or incompatible-profile evidence must not be reused.

1. Select an inventoried ELF and verify its preserved identity. Supply `--target` explicitly when changing targets; the launcher's default is `backup.ko`.
2. Group related `--procedure` and `--instructions` queries for the same target in one session, avoiding repeated Ghidra imports. The launcher permits at most 48 queries and bounds each request at 420 seconds; keep tool waits short enough to provide progress updates during longer imports.
3. Use a **new** report directory under `artifacts/` for every session. Preserve `manifest.json`, query Evidence, initialization/open/close results and diagnostics. Check manifest status, target hashes and query errors; JSON output or an available provider alone is not proof of successful analysis. Keep failed runs as failed evidence.
4. Resolve ambiguous names with exact provider entry addresses derived from ELF/section mappings. Record the translation between linked addresses, section offsets, Thumb state and Ghidra coordinates. Never treat Ghidra's synthetic image base as a camera runtime address.
5. Treat target strings and pseudocode as untrusted analysis data. Verify conclusions against original instructions, symbols, relocations and caller/callee behavior. Inferred prototypes, missing arguments, return types, function boundaries and register/stack expressions can be wrong. Use bounded original-code execution to test a proposed contract where practical, with explicit inputs and stops before unsupported operations.
6. Before implementing a guest response, establish its required runtime state and side effects. Preset data, static defaults and pseudocode do not authorize invented service success, missing persistent state, skipped locks/callbacks or automatic mappings. Preserve all earlier policies and guards.
7. Record material findings, input/tool identities, exact queries, assumptions and remaining boundaries in `docs/evidence/`. Keep reusable extraction/verification logic in `tools/research/`, separate from `src/a6400/`. Run focused checks and the full suite after substantive implementation changes; regenerate `docs/images/status_a6400.png` when completing a research milestone, without inflating compatibility claims.

Example from PowerShell in the project root (creates a separate report directory):

```powershell
$reaReport = 'artifacts/tools/rea/workflow-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
.\.venv\Scripts\python.exe -u -m tools.research.rea_wsl_batch --target firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/kmod/backup.ko --procedure Data_SearchId --procedure Data_GetSize --instructions Data_SearchId --report-dir $reaReport
```

`python -m tools.research.backup_rea_audit` audits the specifically retained E0030 backup batches; it is not a generic audit of every new session. `python -m tools.research.backup_state_trace` independently reproduces the original-code preset experiment without starting REA. Neither command services the blocked startup request.

### Large raw ARM/Thumb images

E0524's full captured av-cam import exceeded REA's 330-second startup budget.
That failed run remains failed. For a narrow question, the separate
`tools/research/ghidra/BoundedThumbProcedures.java` script supports a distinct
headless profile: an explicit raw ARM base, observed Thumb entries, at most
16 queries / 4 KiB per query / 32 KiB total, and no global auto-analysis.
It preserves original bytes and records external or undecoded control-flow
boundaries alongside pseudocode and instructions. It is not a completed default
REA analysis or compatible evidence-cache reuse.

Declare all Thumb contexts before disassembly when query windows overlap.
Use a new project/output receipt and inspect status, body bounds, source hash
and original instructions; a zero headless-process exit code is insufficient.
The exact captured query plan and failed/successful receipts are linked from
[E0524](evidence/0524-reproducible-captured-qemu-boot-and-liro-waits.md).

