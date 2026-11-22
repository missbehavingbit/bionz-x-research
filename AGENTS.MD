# Project instructions

## 1. Objective and working scope

Build an evidence-backed, multi-device Sony BIONZ X (`CXD90045`) environment in which original or modified firmware produces application behavior through verified CPU, OS, and hardware contracts. Host-written navigation, rendering, or camera decisions are not firmware compatibility. Follow the user's current scope; continue authorized work without routine confirmation. Do not spawn subagents unless explicitly requested or required by another applicable instruction.

## 2. Standing authorizations and hardware safety boundaries

- **Read-only camera observations (authorized 2026-11-10):** `tools/camera_debugger.py exec`, native read-only probes, `/proc`, `/sys`, bounded single-page (4 KiB) `/dev/mem` reads, and process-memory reads are always allowed. No writes, mode switches, SD changes, or teardown without explicit scope. Keep complete receipts, remove only owned helpers, and verify camera responsiveness (`/proc/uptime` monotonic, debugger responsive, `/tmp_bt` clean).
- **SAFETY NOTE (`E0454`, 2026-11-10):** Bulk `/dev/mem` physical scans (e.g., 16 MiB chunks) and page-table/kernel-memory sweeps dropped USB and **rebooted the camera**. They are **excluded** from standing read-only authorization; never run them without explicit per-incident user go-ahead.
- **SAFETY NOTE (`E0647`, 2026-11-19):** Reading PA `0x000F8000` through `/dev/mem` (single page) gave an external abort; Sony's exception monitor turns any such abort (even in user space) into a warm reboot. The kernel does not guard `/dev/mem`. Every camera `/dev/mem` access must pass `tools/devmem_policy.py`: only verified pages are standing-allowed; device-map/SRAM pages need per-address go-ahead; the first MiB and unmapped holes are forbidden unless the reader catches SIGBUS (`ro_page` v2, verified on the camera) and the address has a go-ahead; DRAM and `read()` on `/dev/mem` are forbidden.
- **SAFETY NOTE (`E0297`, `E0298`):** Do **not** read `/dev/stream` for OSD capture (rebooted camera). Use safe `im.elf` ptrace (`E0298`: OSD A/B `640×480` RGBA4444).
- **Device & storage boundaries (`E0219`, `E0222`):**
  - Physical SD `E:\` is read-only for emulator inputs. Authorized diagnostic camera writes stay under `/tmp_bt` (except the explicitly authorized small SD diagnostic journal `E0219`, which remains unwritten and requires verified service ownership, non-overwrite creation, bounded write, flush/readback, and restart-survival evidence).
  - `A:/dev/mmca` is SD (`E0222`); `B:/dev/nflasha23` is internal flash and **excluded**. Never guess from drive letters, concurrently write-mount SD, or issue raw storage writes.
  - Ordinary tests must never access USB, invoke service-mode backup, install firmware, replace drivers, or modify the camera. A host monitoring window is not completion, successful STOP, restored hooks, drained DMA, or unloaded-module proof.

## 3. Current technical checkpoint

See [docs/INDEX.md](docs/INDEX.md) and [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md) for the full map. Active workstreams:
- **L2 Kernel Boot (`UnicornMachine`):** `E0653`, `E0656`, `E0676`, `E0677` — `nflasha1` completes post-`uname` child/COW roundtrips and D16 `AT_HWCAP` parity; captured `nflasha3` main firmware boots through stock `/sbin/init`, 9 mounts, 39 `.ko` modules, `LIRO` `entry_main`, `im.elf` `DMP_2D_Initialize : 0`, and `/bin/ash`. `--qemu-peripherals` includes the QEMU SYSV/VPH frame clock, evsel `ext-vd`, SIO channel-3 start/done, GPIO port-1 input `0x32`, and ADC done with released-key results `0x3FF`. Single-core `cpu 1..3 offline`; no Unicorn GUI frame; the `ObjMic` replay is still open.
- **L3 User-Space (`InterpreterProbe`):** `E0526`, `E0530`, `E0534` — 88+ threads and historical allocator abort; isolated constructor fits `0x4C`, so the prior pool-overrun explanation is unproven. Strict GLES stops on unknown shared contents; original USB startup reaches FSM 7 but stops at OTG SET_MODE.
- **Track B Physical UVC/PTP (`054C:0322`):** `E0468`, `E0471`, `E0476` — Run B25 `S1` root-caused EP0 Status-OUT NAK window; `E0476` status pre-arm module (`dcc2f64f…`, `117/8/0`, Unicorn `8/8 PASS`) awaits explicit user GO for Run B26.
- **Multi-Device Platform (`CXD90045`):** `E0477–E0489`, `E0506–E0509` — 13 bodies / 9 kernels mapped + `ILCE-6400A` 1.01 reference qualification and USBSTOR native `GetModelInfo` (`0032858730`, 2.727 ms); `TargetProfile` registry in `src/a6400/targets/`.

## 4. Fidelity and evidence invariants

- Separate observations, inference, assumptions, fixtures, and physical verification. Bound execution; stop with PC, arguments, and reason at unsupported imports, syscalls, devices, memory, or metadata. Faults and limits are not success.
- **English-only repository policy:** Exclusively English (among human spoken languages) must be used for all repository content, including documentation, evidence records, code comments, docstrings, UI strings, and commit messages. Non-English human text is not permitted anywhere in the repository.
- No silent zero/success replies, automatic mappings, fabricated queues/UI, or weakened guards to advance execution. Add contracts via explicit opt-in policies; retain default no-response and earlier regression baselines.
- Guest access uses a declared namespace and allowlisted hash-checked inputs, never arbitrary host paths/syscalls. Unsupplied runtime mounts are unknown, not verified absent.
- Original code owns application logic/rendering in fidelity experiments. A bounded instruction probe is not a booted kernel, timing model, complete machine, or proof of deployment safety.
- Preserve firmware, backups, and captures; modify separate outputs or overlays. Never silently refresh hashes. Keep upstream checkouts and `venv/` in place; do not change their Git state incidentally.
- Material evidence records need input/tool identities, exact command/policy, original instruction support, serviced operations, observation/stop, assumptions, failures, repeatability, and next boundary. Preserve failed receipts and mark superseded conclusions. Update `README.md`/roadmap when status changes; never publish proprietary inputs or secrets.

## 5. Implementation, verification, and research tooling

- Before deriving a new contract or repeating research, consult [docs/RELATED_REPOSITORIES.md](docs/RELATED_REPOSITORIES.md) for relevant upstream work. Pin useful findings to exact commits and identity-matched evidence; upstream claims are leads, not verified local contracts or authorization to run tools.
- Use small components and shared path/memory rules (`project_paths.py`). Prefer `rg`, constructed fixtures, and focused ABI/state/error/provenance/ARM checks. Generated fixtures belong under `artifacts/tests/`.
- Pass the repository root URI explicitly as `workspaceRoot` to Pylance MCP tools that accept it. Discover the URI with `pylanceWorkspaceRoots` if needed; see [Pylance MCP troubleshooting](CONTRIBUTING.MD#pylance-mcp).
- Launch first-party scripts as modules from the repository root (`python -m tools.research.<tool>`, `python -m legacy.<prototype>`).

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -q
.\venv\Scripts\python.exe -m tools.research.probe_interpreter
```

- Run focused checks during work, the full suite after substantive code changes, and real-firmware replays when their path changes. Preserve reports under new output directories. Boundary exits (including the `files` probe's expected exit 2) are not regressions.
- For substantial ARM/Thumb analysis, prefer REA/Ghidra via WSL and `tools.research.rea_wsl_batch`; reuse only identity-matched evidence. Read [docs/REA_WORKFLOW.md](docs/REA_WORKFLOW.md) and [docs/DEPENDENCY_WORKFLOW.md](docs/DEPENDENCY_WORKFLOW.md) before following call chains.
- References: [CONTRIBUTING.MD](CONTRIBUTING.MD), [docs/LIVE_CAMERA_EMULATOR_WORKFLOW.md](docs/LIVE_CAMERA_EMULATOR_WORKFLOW.md), [docs/ADDING_A_BIONZX_CAMERA.md](docs/ADDING_A_BIONZX_CAMERA.md).
- Complete historical experiment logs are preserved verbatim in [docs/archives/agent-notes-2026-11-13-e0210-to-e0476.md](docs/archives/agent-notes-2026-11-13-e0210-to-e0476.md) and [docs/archives/agent-notes-2026-11-09.md](docs/archives/agent-notes-2026-11-09.md). Keep `AGENTS.md` concise; refresh `docs/images/status_a6400.png` only for substantive research milestones.
