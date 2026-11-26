# E0524 - Reproducible captured QEMU boot inputs and qualified LIRO waits

Date: 2026-11-16. Offline only: no camera/USB operation, driver change or firmware
deployment. Original dumps remain read-only. Labels: [O] observation,
[S] original static instruction support, [I] inference, [A] model assumption.

## 1. Result

- Two independent input preparations have identical manifests and output hashes
  [O]. Both complete user-partition sets and both hardware boot areas come from
  the declared capture, not a mixture with update-package files.
- The native QEMU eMMC layout requires 4 MiB boot0, 4 MiB boot1, then the SDM user
  area [S/O]. The original `CMD1 0x40000080` also requires sector addressing below
  2 GiB. Fixing those contracts restores captured filesystem mounts without a
  firmware patch or a 4 GiB capacity workaround [O].
- The original kernel brings up four CPUs, loads the captured software, runs
  LIRO `entry_pre` / `entry_main`, initializes DMP/stella and answers shell
  commands [O]. This is not a complete camera boot or a verified GUI frame.
- A stopped snapshot identifies a real Task-43 event wait, and native controller
  traces distinguish a missing input assertion from a merely registered or
  disabled IRQ [O/S]. No completion, semaphore, flag or IRQ was injected.

## 2. Input and tool identities

The ignored `qemu-a6400/inputs.local.json` declares this workstation's external
capture directory and pins the original manifest/checksum-list hashes. The
capture and generated images can contain private settings; never publish them.
The whole dump-folder pattern is also git-ignored if copied into the worktree.

| Input / tool | SHA-256 |
| --- | --- |
| Capture `manifest.json` | `735640d8cbe8dc24dc683519a5b262dd47a4b27efc0345b00b6a21af5c2bc015` |
| Capture `SHA256SUMS.txt` | `95f0831ead174891df4eac363a92a60f3604005d629093b3f2900f1431375472` |
| Main kernel, extracted from captured `nflasha3` | `d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618` |
| Captured main initrd | `db95861f6c56418a216c4805f1f76f2cf2a0e74fcbe0d3350b87cf6296254673` |
| Captured av-cam (16,803,048 B) | `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209` |
| Captured `/usr` partition | `70d2613fcd24b13cfc4d75e98f435171fdfc50a6bc64d408e2df42ffb8197064` |
| Captured `Backup.bin` | `13345a908433d7130adb382ee01a951ac8e691d091defd666d4d8a8af081232d` |
| Prepared 2 GiB eMMC backing image | `18e37ec3b3e56d26d31c0b0a4fd91ff10d0d1cb8a23308e49c35abd5cd3cc94b` |
| QEMU 10.1.0, corrected native binary | `feb056aad4e6aaa0d668398cc64a257d85184ddca0ec4a2fe35e373db55dfbff` |
| `qemu-img` | `adf99e047e0058710b750af2c6a90945e1efbafb337b28ab85882cfff76487ec` |
| Persisted QEMU patch | `14521e33ecdbd3cc9424b87b14c1ffaf78677f2dd325f60c870a692d25836dfc` |

QEMU upstream is `f8b2f64e2336a28bf0d50b6ef8a7d8c013e9bcf3` (`v10.1.0`).
REA is 6.7.0; Ghidra is 12.1.4 with full OpenJDK 21.0.13.0.3. Per-run observer,
source and decoder identities are retained in the private receipts and
`artifacts/qemu-a6400/e0524-replay-audit.json`.

The unused `manifest-final.json` differs from its existing checksum-list entry.
Neither file was rewritten. Used inputs are independently checked against the
unchanged primary per-device manifest and pinned checksum list [O]. This audit
does not certify unrelated capture archives.

## 3. Reproducible preparation and storage fixes

From the repository root in PowerShell:

```powershell
.\venv\Scripts\python.exe -m tools.research.qemu_boot_inputs prepare --report-dir artifacts\qemu-a6400\inputs-emmc-v2-20261116-124021-a --allow-unacquired-zero-fill
.\venv\Scripts\python.exe -m tools.research.qemu_boot_inputs prepare --report-dir artifacts\qemu-a6400\inputs-emmc-v2-20261116-124021-b --allow-unacquired-zero-fill
.\venv\Scripts\python.exe -m tools.research.qemu_boot_inputs verify --report-dir artifacts\qemu-a6400\inputs-emmc-v2-20261116-124021-a --sources
```

These are retained run names; use new directories for another run. Preparation
refuses overwrites, partial images, changed hashes and escaping source paths.
All eight prepared outputs match between the two runs [O]. The audit rechecks
both sets, original source hashes, archived RAM-read hashes, task/IRQ decodings
and the native controller trace.

The captured user capacity is not a complete whole-disk acquisition. Schema 2
records hardware boot areas, the 8 MiB user-area translation, capacity rounding
and unacquired zero-filled backing ranges. Such bytes are explicit emulator
construction, not observed camera content [A]. Writable source partitions were
not acquired atomically.

The original driver sends rootfs sector `0x52002`; the earlier model interpreted
it as byte address `0x52002`. Native command/address tracing now shows the correct
byte address `0x0A400400` [O]. Constructed qtests demonstrate that sector mode
failed before the fix while byte mode passed; both pass afterward. No host-made
filesystem response is substituted for the original kernel path.

## 4. Qualified runtime waits

The observer waits for an original serial marker, paces commands at 100
characters/second, sends one command at a time and requires actual guest exit
statuses. It does not retry commands. All CPUs are stopped for bounded RAM
observations. LIRO offsets are qualified by the kernel/usr hashes and this
capture's `liro.ko` hash
`ca5fa9ecc990c2ceda755887563dcd2291fb49f56a4260d895818e7f6782f535`.

Local `tsks` was not available through the attempted `/proc/kallsyms` query.
The successful binding uses this run's `/sys/module/liro/sections/.bss` plus
the original ELF `tsks` offset `0x780`, not an address reused from another boot.
Original `ref_tsk`, `set_tsk_state`, `tsk_thread_helper`, `get_wchan` and
`unwind_frame` instructions establish control-block and frame offsets [S].
Unobserved bytes, foreign identities and frame-pointer escapes stop explicitly.

| Task | Observation | Supported interpretation |
| --- | --- | --- |
| 1 | Linux semaphore sleep; original caller `0x6361BB48`, saved return `0x6361BB4D`; `r0=0x00BF04EE`, timeout -1 | This is the second wait on that semaphore in `0x6361BAD8`, after an earlier wait on `0x00BF04EF`. Its lifecycle purpose and status as a global boot blocker remain unproven. |
| 43 | State `0x4`, wait kind 8, object 10; original entry `0x63D8B805` waits with mask `0x3373`, mode 1, timeout -1 | A real ITRON event-flag wait, not an invented missing message. |
| 65 | State `0x10`, no queued activation; helper sleep; entry veneer `0x63D4BA5F` reaches `0x638FCD4C` | The target has a finite initialization body. Dormancy alone does not distinguish an unstarted, returned or otherwise ended task; a sub-micro deadlock is not demonstrated. |

The actual registered IRQ 101/102 callbacks are `0x63D8929F` and
`0x63D8935D`, requesting CPU 1 [O]. Original instructions contain conditional
paths setting flag-10 bits 1/2 [S]. Their predicates must also be respected;
raising an IRQ is not automatically a valid completion.

The final passive GIC trace records one enable transition for each IRQ and zero
input assertions across the bounded run [O]. Thus these lines are enabled, not
merely registered, but their modeled producers never assert them. Task-state
and original-frame observations also reproduce across independent boots.

Example bounded probe inside WSL, from the repository root:

```bash
python3 -u -m tools.research.qemu_boot_probe --inputs "<Linux path to prepared inputs>" --report-dir artifacts/qemu-a6400/NEW-PROBE --seconds 120 --wait-for "Created HeapAllocator:" --guest-command "cat /proc/interrupts" --guest-command "cat /proc/udm/upm_bootmode" --liro-task 1 --liro-task 43 --liro-task 65 --liro-irq 101 --liro-irq 102 --read-physical 0x20021000:4096
```

Main receipts:

- `artifacts/qemu-a6400/raw-boot-sector-mode-20261116-125613/`
- `artifacts/qemu-a6400/liro-sysfs-snapshot-20261116-133512/`
- `artifacts/qemu-a6400/liro-display-wait-20261116-143537/`
- `artifacts/qemu-a6400/liro-passive-gic-20261116-145308/`
- `artifacts/qemu-a6400/e0524-replay-audit.json`
- `artifacts/tools/rea/qemu-liro-task-state-20261116/`,
  `qemu-liro-task-layout-20261116/` and `qemu-liro-suspend-contract-20261116/`
- `artifacts/analysis/e0524_avcam_targeted/targeted-phase2.json`

## 5. Failures retained and superseded

- A contribution-preflight constructed fixture exposed output-directory creation
  before capture/output overlap validation. The guard now runs before any
  directory or receipt is written; the failing-before regression is retained in
  `artifacts/tests/e0524-pr-preparation-20261116-1555/`. No original capture was
  used by this regression.
- Windows CRLF scripts/patches and a setup script that silently skipped patch
  conflicts prevented the first build. LF attributes, an exact upstream revision
  guard and verified reverse-patch detection now fail closed.
- Schema-1 generated disks omitted the two native eMMC boot areas. Their
  preserved outputs are reproducible but **invalid for this machine**; schema 2
  supersedes them. The original source hashes were not refreshed.
- Concurrent console reads on cloud-backed DrvFS failed with `ENODATA`. Console
  capture now stays on Linux storage, followed by hash-checked archival.
- Burst serial input truncated a command. Paced, acknowledged input replaces it.
  The earlier missing-path result is not used as firmware evidence.
- The guest has no per-task `/proc/<pid>/stack` file. Saved-frame observation uses
  the original, version-checked kernel ABI instead.
- Full raw REA auto-analysis timed out at 330 seconds and remains failed.
  A distinct Ghidra profile uses explicit observed Thumb entries, bounded
  disassembly and no global auto-analysis. Ten queries completed; this is not
  reuse of the failed/default profile. An initial overlapping-context error is
  retained; all contexts are now declared before disassembly.
- Direct QMP GIC debug reads reset the emulator connection. The native SMP GIC
  access path requires a current CPU; that unsafe observer path was removed.
  Passive native enable/disable/input traces provide the controller evidence.
  This affected only an owned emulator process, not a camera.
- The first full Python run exposed four stale generated-status failures and
  a pre-existing report test attempting unavailable physical drive E:. That test
  now mocks the physical source and explicitly verifies synthetic-memory input
  before any further full run.

## 6. Validation and remaining boundary

Initial milestone validation receipts are under `artifacts/tests/e0524-validation/`.

| Exact command | Result |
| --- | --- |
| `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | **OK**, 1904 run, 128 skipped, 38.818 s; `offline-suite-final.log`. This is the runner result, not a discovery-only estimate. |
| `python3 -m unittest tests.test_qemu_emmc -v` inside WSL | **2 passed**, 0.659 s; `native-qemu-and-shell.log`. Constructed disks and paused CPUs; no firmware/device required. |
| `.\venv\Scripts\python.exe -m unittest tests.test_liro_task_snapshot tests.test_qemu_boot_inputs tests.test_sdhci_bridge -q` | **21 passed**. |
| `.\venv\Scripts\python.exe -m unittest tests.test_research_status_sync tests.test_research_status_dashboard tests.test_repository_layout -q` | **53 passed**. |
| `.\venv\Scripts\python.exe -m tools.research.render_research_status --check` | **Passed**, E0524 / 1904 recorded suite cases / 527 receipts. |
| `git diff --check` in the project and owned QEMU checkout | **Passed**. Patch-container blank context prefixes are preserved; the embedded native source is still checked separately. |

The Windows full suite includes the two optional native QEMU cases among its
skips; the separate WSL run executes them. Skips are not additional passing
executions or a compatibility percentage. Idempotent setup verifies the exact
upstream commit and already-applied patch without resetting a checkout.

### Contribution preflight

The contribution is isolated on upstream `main` (`ffa974b`), without the older
local development history. Receipts are under
`artifacts/tests/e0524-pr-preparation-20261116-1555/`.

| Exact command | Result |
| --- | --- |
| `.\venv\Scripts\python.exe -m tools.research.ci_check --summary` | **Passed**, 5/5 checks; 717 Python files compiled, 1639 tracked files checked, 86 guardrail tests. |
| `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | **OK**, 1905 run, 128 skipped, 40.984 s; `offline-suite.log`. Includes the additional capture/output overlap regression. |
| `.\venv\Scripts\python.exe -m unittest tests.test_qemu_boot_inputs tests.test_liro_task_snapshot tests.test_sdhci_bridge -v` | **22 passed**, 2.685 s; `focused-after.log`. The overlap case failed before the fix; `overlap-regression-before.log` is retained. |
| `wsl.exe --distribution Ubuntu-26.04 --user administrator --exec python3 -m unittest tests.test_qemu_emmc -v` | **2 passed**, 0.473 s; `native-emmc.log`. Constructed disks and paused CPUs only. |
| `.\venv\Scripts\python.exe -m tools.research.qemu_boot_inputs prepare --report-dir artifacts\qemu-a6400\inputs-pr-preflight-20261116-1555 --allow-unacquired-zero-fill` | **Passed**; all eight output identities match the earlier schema-2 preparation. |
| `.\venv\Scripts\python.exe -m tools.research.qemu_boot_inputs verify --report-dir artifacts\qemu-a6400\inputs-pr-preflight-20261116-1555 --sources` | **Passed**; prepared outputs and original capture hashes verified unchanged. |
| `wsl.exe --distribution Ubuntu-26.04 --user administrator --exec bash -n qemu-a6400/run.sh` and `wsl.exe --distribution Ubuntu-26.04 --user administrator --exec bash -n qemu-a6400/setup-qemu.sh` | **Both passed**; shell/patch working copies are LF. Neither script was launched. |

The fresh preparation records the revised preparer identity in its own new
manifest. Earlier manifests, inputs and failed receipts were not refreshed.
The eMMC output remains
`18e37ec3b3e56d26d31c0b0a4fd91ff10d0d1cb8a23308e49c35abd5cd3cc94b`.

No whole-camera equivalence, real timing, sensor operation, deployment safety or
GUI frame is established. Existing QEMU peripheral stubs, ignored unmapped MMIO
and default non-timer GIC routing remain explicit assumptions.

**Next:** recover the actual register/status/timing contract of the enabled
IRQ 101/102 producer paths. Do not signal flag 10, release the Task-1 semaphore,
or add a periodic interrupt merely to advance execution. The observations
identify concrete waits; they do not prove one sole global blocker.
