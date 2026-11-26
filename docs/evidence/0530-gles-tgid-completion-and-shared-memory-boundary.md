# E0530 - Original GLES TGID completion and the software-shared memory boundary

Date: 2026-11-17. Offline only; no camera, USB, physical SD or firmware writes.

## Result and claim boundary

**The same-TGID software completion contract is corrected and tested. A
firmware-produced display frame is still blocked, not demonstrated.**

Original driver/kernel execution verifies ownership, completion reset, signaling
and the fast path. Original caller execution also refutes the supposed
"event-present" interpretation of bit 24. A new opt-in policy preserves those
rules without the historical engine-success assumptions.

Two original-process replays stop identically at instruction **146,135,735**:
`libObj.so+0x72BF96`, runtime PC `0x51DBBF96`, reads four uninitialized bytes at
the start of the software-shared mapping, VA `0x12810000`. OSD A/B are unmapped,
there are zero modeled engine completions, and no display PNG is produced.
Seeding a zero, BAR identity or assumed completion would not resolve this stop.

## Identities and analysis

The target is the preserved ILCE-6400 `extracted_a6400` baseline. Its release
label is not independently requalified here; the following exact input
identities, not a marketing version or another body's addresses, bound this
record:

| Input | SHA-256 |
| --- | --- |
| `grm_gles.ko` | `a8d1dbe8c66be365d32371b13f35a0e7d598c415c0ff17f2c8f544084ccc6882` |
| `libObj.so` | `60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1` |
| `vmlinux.bin` | `12eeec829407595e28265f7d0db3b88c56f63a9f0fdd725c9c53c66a49643707` |
| Packaged WBI `nflasha5` | `19a702b030306a32f4287131da314e5c89d66163997f0f9a23b0967981b0001b` |

The module is ARM ELF32 ET_REL; `.text` begins at file offset `0x34`.
Module offsets below are `.text`-relative, not Ghidra addresses. REA's synthetic
Ghidra text base is `0x10000`; the captured module base is `0x5F400000`.
Original module instructions, excluding relocated words, match the captured
image. Kernel helper instructions are checked against the preserved kernel.
The user caller window is Thumb; its linked addresses have no load bias in
the isolated branch experiment. The process replay resolves `libObj.so` at
`0x51690000`.

Analysis used existing REA 6.9.0 / Ghidra 12.2, with completed manifests and
unchanged source hashes under:

- `artifacts/tools/rea/gpu-completion-20261117-01/`: `sugilite_ioctl` and
  `sugilite_interrupt`, including original ioctl instructions.
- `artifacts/tools/rea/gpu-completion-20261117-02/`: `sugilite_dev_init` and
  `sugilite_mmap_dev`, both with original instructions.

The execution reports record Python, Unicorn and helper source hashes.
Cortex-A9 remains the existing isolated CPU research control, not a qualified
camera CPU/timing model.

Before deriving contracts, the related-repository catalog was consulted.
The comparison source was
[`Oein/a5000-emulator` at `6ad36bd7b0411d55a5920447ff51e3de01f8cb9b`](https://github.com/Oein/a5000-emulator/tree/6ad36bd7b0411d55a5920447ff51e3de01f8cb9b),
particularly `docs/RE_DISPLAY_GPU.md` and `docs/RE_GPU_P3D_CMD.md`. Its earlier
body's shaders, registers and displayed frame are not transferred as a6400
proof. No upstream source was copied or executed.

## Corrected contracts

### 1. Ownership is TGID, not user stack contents

At module `+0xF4C..+0xF60`, the driver aligns **kernel SP** to `thread_info`,
loads `[thread_info+0xC]` as the current task, then `[task+0x108]` as TGID.
The lookup at `+0` compares it with `[owner+8]`.
`KernelTaskObservation` independently identifies task PID/TGID at
`+0x104/+0x108`.

There is no kernel read of `[user_argument+0xC]`. The historical
`_ctx_identity` routine read unrelated user stack data, treated TGID-shaped
data as a context pointer, and added a process-wide completion fallback.
It is retained only for old-policy reproduction.

### 2. A parked wait must not restart its entry/reset operation

Original new-owner initialization executes `complete_all(owner+0x14)`.
The wait ioctl `0xC004820D` then:

- Consumes `[owner+0x10]` if set, clears that fast flag, and writes output 1.
- Otherwise resets completion `done` to zero, releases the driver lock, and
  enters `wait_for_completion_interruptible_timeout(owner+0x14, 10000000)`.

Signal ioctl `0xC004820E` reads one u32 key. Zero selects current TGID;
nonzero selects that TGID's owner. If completion is already done, the signal
sets the fast flag; otherwise it executes `complete_all`. Successful signals
leave the input key unchanged. An absent nonzero owner writes `0x1FFFFFFF`
and returns `-EFAULT`; it is not successful completion.

The new policy tracks an in-flight wait separately from a new ioctl entry.
Retrying a parked syscall therefore cannot clear the signal it received.
Existing waiters are woken across descriptors sharing the TGID, while an
IRQ waiter or unrelated descriptor waiter is not woken by a software signal.
Foreign-owner task liveness and scheduler timeouts remain explicit unsupported
contracts, not guessed successful responses.

### 3. IRQ waits and software completions are different

The original IRQ handler accumulates upper status bits in `dev+0xA8` and wakes
`dev+0x4C`. It does not complete `owner+0x14`.
Ioctl `0xC0048203` masks its input with `0xC4000000`; matched events clear
their mask bits and return the mask. The strict policy blocks with no event,
rejects an invalid mask with `-EFAULT`, and stops rather than inventing the
unmatched-event retry/recovery path.

Original Thumb execution at `libObj+0x72B88C` establishes:

| Input status fixture | Original successor |
| --- | --- |
| `0` | Resubmit at `+0x72B7C6` |
| `0x04000000`, `0x40000000`, `0x80000000` | Normal continuation at `+0x72B89C` |
| Historical assumed `0x45000000` | Recovery at `+0x72D6E0` |

Thus `0x01000000` is a recovery discriminator here, **not required event
presence**. The earlier interpretation of E0314's engine11/engine15 progress
is superseded; those receipts do not demonstrate genuine GPU completion.

### 4. The 128 KiB mapping is not the GPU BAR

Original `sugilite_mmap_dev` maps:

- Length `0x20000`: the software-shared `dev+0xAC` vmalloc area, page by page.
- The BAR path: physical `0xF1000000`, length `0x4000`.

`sugilite_dev_init` allocates the shared area with `vmalloc(0x20000)`, not a
verified zero initializer. The new policy guards its bytes until guest CPU
writes establish them. It does not seed the shared area with GPU identity
`0x000B0000`, and it rejects unsupported alias/shape requests.

The old `+0xE8/+0xF4/+0x16C` descriptor-triggered "engine" model was acting on
**software shared memory**, not a hardware command submission. Its default
and historical opt-in results remain reproducible, but are unqualified.
`kernel-bound-tgid` rejects that model and its seeded descriptor block.
Actual GPU MMIO accesses other than the observed identity read stop until
their effects are modeled and verified.

## Original-instruction and process replays

Run from the repository root, using new output directories:

```powershell
.\.venv\Scripts\python.exe -m tools.research.gles_completion_probe --report-dir artifacts\analysis\NEW_GLES_COMPLETION
```

Final receipts `artifacts/analysis/gpu-completion-20261117-04/` and `-05/`
pass **8/8 checks**, with identical traces: 3,520 original module/kernel
instructions and 20 original caller instructions. Original open constructs a
new TGID-97 owner using the captured allocator, locks and task state; no
allocation, lock, ioctl or GPU return is supplied by the host.

The driver sequence is: open; wait/reset to the kernel-wait import boundary;
key-0 signal; explicit-TGID signal; fast wait returning 1; another wait/reset
to the kernel-wait import boundary. Completion `(fast, done)` observations are
`(0,0)`, `(0,0x7FFFFFFF)`, `(1,0x7FFFFFFF)`, `(0,0x7FFFFFFF)`, `(0,0)`.
The stopped first wait is not scheduler-registered or given a fabricated
return. Cross-thread scheduler integration is separately tested with explicit
task/descriptor fixtures, not presented as an executed Linux scheduler wake.

Process replay uses the locally regenerated, unused pre-GLES checkpoint
`artifacts/execution/heap-ownership-20261117/cold-01.ckpt`, SHA-256
`1169ac4a83d91bc1d6b6aa36a6a206eb09c1b48c37ae74d9b7783355a78172d2`,
at 69,827,675 instructions. It has no GLES owner, transaction, mapping or
engine result to retrofit. Used legacy GPU checkpoints cannot change policy.
The source checkpoint remains unchanged.

The final repeated command was:

```powershell
.\.venv\Scripts\python.exe -u -m tools.research.experiment_ui_boot `
  --load-checkpoint artifacts\execution\heap-ownership-20261117\cold-01.ckpt `
  --instructions 160000000 --timeout-us 90000000 `
  --argument debug --argument s --argument g --argument r --argument u `
  --argument l --argument i --argument a --argument lazy-global --stimulus none `
  --fast-hooks --urandom-open --urandom-entropy deterministic `
  --tmp-overlay experimental-empty --proc-overlay experimental-empty `
  --setting-overlay observed-e0301 --stream-device image-driver `
  --regular-inode-rdev --readonly-image-times --backup-service preset-backed `
  --graphics-service authentic-grm --grm-pools captured-e0044 --media-service authentic-sd `
  --null-page read-zero --madvise-dontneed --gles-device kernel-bound-tgid `
  --gpio-device driver-bound --kmod-service verified-image `
  --kikilog-device observed-running-e0301 --usb-buspower-device driver-bound `
  --usb-event-device driver-bound --usb-otgcore-device driver-bound `
  --usb-reply-policy verified-connect --hdmi-device driver-bound `
  --cec-device driver-bound --mmca-char-device driver-bound `
  --uipc-pool-options kernel-e0301 --tty-device log-sink --nanosleep-policy block `
  --stall-syscalls 0 --syscall-record-limit 5000 `
  --display-contract camera-e0074-e0044 --quiet-boot `
  --report artifacts\execution\NEW_GLES_PROCESS\report.json `
  --save-checkpoint artifacts\execution\NEW_GLES_PROCESS\stop.ckpt
```

Create the new report directory first. Here `--instructions` is an absolute
instruction ceiling on resume, not an additional instruction allowance.
The inherited process model, deterministic entropy, filesystem overlays,
transport replies and clone-derived kernel task records are experimental
inputs, not a booted kernel. `authentic-sd` selects the existing hash-checked
packaged-WBI stream window at PA `0x20030000`; it does not read a physical SD
card. No queue stimulus or assumed engine completion was supplied.

Receipts `artifacts/execution/gpu-tgid-20261117-04/` and `-05/` have identical
process reports (SHA-256
`f6efe9998f28dc26515a5b2e80190d0bf2abbffa113612dd8b38e42e1d98f0af`).
The shared area still has 131,072 unverified bytes at the stop.
`artifacts/analysis/gpu-completion-20261117-05/repeatability.json` records
all nine repeatability/provenance checks passing.

## Validation and preserved failures

- Focused command:
  `.\.venv\Scripts\python.exe -m unittest tests.test_guest_gles tests.test_guest_threads tests.test_interpreter_probe tests.test_gles_engine_multi_command tests.test_wbi_gles_execute tests.test_emulator_gui -q`
  - **PASS: 168 run, one skipped.**
- Full `unittest.defaultTestLoader.discover('tests')`, with the documented
  `PhysicalVolumeSource` constructor denied and per-case garbage collection:
  **PASS: 2,000 run, 94 skipped, zero failures/errors**.
  Complete output and summary:
  `artifacts/analysis/gpu-completion-validation-20261117/full-suite.log` and
  `full-suite-summary.json`. This is the integrated dirty-worktree snapshot,
  not 2,000 new GPU tests.
- `.\.venv\Scripts\python.exe -m tools.research.ci_check --summary`:
  **PASS: 5/5 guardrails, including 86 guardrail tests**. Adding this evidence
  record initially made the generated evidence count/cache URL stale.
  `python -m tools.research.render_research_status --write` synchronized the
  generated SVGs; the shared status data and README content apart from SVG
  URLs were verified unchanged. Final receipt: `ci-guardrails.log` alongside
  the full-suite summary.
- `python -m tools.research.emulator_gui --checkpoint <final-stop.ckpt>
  --headless --out <new-directory>`: **expected exit 2**, explicit
  "No verified display surface", no output directory or fallback PNG.
  The CLI previously discarded `main()`'s error return and exited 0; its
  entry point now propagates the failure, with a subprocess regression test.
- Original completion report `-01/` retains the first failed harness output
  guard; `-02/` verifies the corrected guard. Neither failed state was promoted
  to a framebuffer or wakeup claim.
- Process `-01/` retains a too-small absolute instruction ceiling; `-02/`
  stops on unsupplied stream bytes; `-03/` uses the packaged WBI window but
  preemption exposes an unrelated unsupported media reply before graphics.
  The cooperative baseline in `-04/` and `-05/` reaches the graphics boundary.
- Shared PyGhidra access failed on its existing project lock, and the separate
  Ghidra MCP launcher failed with connection refused. No locks, instances or
  shared projects were reset; completed REA batches above supply the analysis.
- Reverify 0.11.0 was used only for staged, hash-checked file bytes.
  Its retained ledger has three verified byte claims and two refuted
  file-offset transcription errors. Corrected offsets use `.text+0x34`;
  refutations remain in
  `artifacts/analysis/gpu-completion-20261117-03/reverify-ledger.json`.
  This is not a CPU, scheduler or hardware proof from Reverify.

## Next boundary

Establish the actual initial backing and ownership of the shared vmalloc
area, or execute its real initialization chain with fully declared inputs.
Do not silently zero it or import captured pointers into a different address
space. Then qualify actual MMIO/DMA work, IRQ acknowledge and guest wakeup;
only successful functional rendering may authorize a completion. P3D parsing,
an IRQ bit, a loop exit or a fixture pixel copy is not a rendered camera screen.
OSD A/B remain the only presentation candidates in this lane.
