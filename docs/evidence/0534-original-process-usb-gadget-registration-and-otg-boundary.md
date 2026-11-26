# E0534 - Original-process USB gadget registration and the OTG startup boundary

Date: 2026-11-18; investigation began 2026-11-17. Offline only. No camera,
physical USB, SD, flash, firmware installation or mode-switch operation.

## Result: prerequisite replay, not completed configuration

The original process now supplies its own loaded libraries, libc allocator,
pthread state, TLS, queues, objects and receiver/service threads for a bounded
USB startup replay. Original messages and FSM actions take `UsbControl` through
states **1 -> 5 -> 6 -> 7**, without writing the FSM state. Original bus-power
gadget registration allocates and links a real process-owned record/object.

**The requested end-to-end configuration path is still incomplete.**
`CUsbSvc::createGadget` stops at the guarded `/dev/usb/otgcore`
`SET_MODE` ioctl **`0x4001E30C`, mode 2**, before gadget creation completes or
gadgetcore's configuration callbacks are registered. No `SETCONFIG` event is
fabricated, produced or delivered by this new replay. The report states
`configuration_path_completed=false`, `gadget_event_produced=false` and
`setconfig_delivered=false`; exit 2 is the preserved boundary, not success.

[E0524](0524-original-local-osal-setconfig-route.md) remains the isolated
SETCONFIG transport/teardown proof with a checked fixture heap. This record
does not promote that fixture result into a complete original-process producer
or a working USB mode.

Runner: [probe_usb_gadget_startup.py](../../tools/research/probe_usb_gadget_startup.py).
Existing default device/reply policies are unchanged.

## Original state and instruction support

The original `G_START` table, ELF VA `0x1E110`, contains event 19 at
`0x1E130`: next state -1, no guard, action
`UsbControl::actionGSetConfig` at Thumb `0x11F53`. The action's original
six bytes at file/ELF offset `0x11F52` are `80b500af80bd`: it returns without
changing state. Event acceptance is therefore not itself a mode transition.

The eight relocated tables are preserved in
`artifacts/execution/usb-gadget-startup-573298cc-final02/fsm-tables.json`.
They establish the relevant path:

| Before | Original input/action | After |
| --- | --- | --- |
| 0, waiting for resource | `onUsbRsrcAcquired` produces `0x0A2000`, mapped to event 0 | 1, STOP |
| 1 | `0x083003`, param `0x1005`, maps to event 11; `actionGConfigureFunc` records the function mask | 5 |
| 5 | `0x083001`, same function, maps to event 12; `actionGStartGadget` queues the start and compares masks | 6 |
| 6 | Original self-message `0x0A7001`, event 24 | 7 |
| 7 | Entry invokes original `UsbIfWrapper::StartGadget` and `doSyncStartStop` | Native create remains pending at OTG ioctl |

These are ELF virtual addresses with the Thumb bit cleared where appropriate:

| Library | Address | Responsibility |
| --- | --- | --- |
| `libUsbControl` | `0x11750`, `0x1045C`, `0x10174` | Original constructor, local callback registration and Init/Main thread entry, executed during startup |
| `libUsbControl` | `0x11DDC`, `0x120B0`, `0x11F58` | Configure, start and gadget-state entry actions |
| `libUsbControl` | `0x13268`, `0x13340` | Original USB start API and dynamic completion-queue lifetime |
| `libInfraUsbCmn` | `0x9250` | `registerGadget`: requires service state 5; allocates a `0x48`-byte record, creates the class object, links its original list |
| `libInfraUsbCmn` | `0x91D2`, `0xFB58` | Select/construct the original bus-power gadget for function ID 16 |
| `libInfraUsbCmn` | `0xAAE0`, `0x9ED8`, `0xBBC4` | Service start, queued service request and original `createGadget` |
| `libInfraUsbCmn` | `0xF0A0`, `0xF3D8` | MSC registration requires user initialization; `usbgmscif_init` is its original initializer |
| `libInfraMscServer` | `0x6CE6` | Original MSC initializer call in `jobStartFuncCmd`, after its physical-buffer allocations |

The MSC function-1 negative control reaches `StartGadget=-5` with no gadget
record: the required `usbgmscif_init` state is absent. Its original higher-level
caller needs two shared and two per-LUN `0x190800` physical-buffer allocations,
plus its actual drive list and callbacks. No fake media, DMA buffers or context
is supplied to repair that control.

The function-4 bus-power branch instead uses already initialized original
user state. This is a separate, explicitly selected original gadget, not a
claim that MSC was fixed.

## Process, inputs and runtime ownership

`--cold-start` runs the original interpreter and `im.elf` with
`debug s g r u l i a lazy-global`. It stops at **131,889,674** estimated
block-hook instructions on the existing unsupplied stream bytes at physical
`0x20030000`. No read response is supplied there. The USB thread and service
already exist. The earlier independently generated checkpoint reached the
same boundary at 131,889,676 after a separate namespace stop/resume.

The cold profile is deliberately experimental: preset-backed backup,
kernel-e0303 IPC, observed-running kikilog, original module validation,
deterministic canary entropy, explicit empty `/tmp` and `/proc`, observed
minimal `/setting`, existing stream/GLES/device contracts, CPU workarounds and
10,000-instruction preemption. Exact options are in every receipt and in
`BOOT_OPTIONS`. This is not a hardware-equivalent boot.

Bounded ABI calls use an existing thread's TLS and unused stack space, preserving
its CPU context on return. Original `Main` and the existing cooperative
scheduler own command reception and service work. Preemption is disabled during
these bounded calls. No imported `malloc`, `free`, mutex, dispatcher or callback
is replaced; unknown syscalls/devices still stop.

All seven source hashes and loaded executable PT_LOAD bytes are checked:

| Input | SHA-256 | Load bias |
| --- | --- | --- |
| `libc-2.11.2.so` | `bceb5cfb7769b54bf9c9d178b8a5f4579531c0f60e673cbcae872a8c7a3508fb` | `0x50200000` |
| `libUsbControl.so` | `70cd5742fde9eea78c60905e4015531b4c3ab2668d2cb85c934531f451f1211f` | `0x53A40000` |
| `libMWF.so` | `a79dc0d558e6454e31e57ea3b026364be924d0e78c1b6101015125a249f7e274` | `0x51260000` |
| `libosal_uipc.so` | `b6ded35add9582413b401cf4db78ec2c9a201b632e78c2731d6bbb379249b3eb` | `0x50030000` |
| `libInfraUsbCmn.so` | `5338bafbb545ebc223fac15c9253a41ac9fe49c01fe32c73af40c3fb50eec0a6` | `0x538F0000` |
| `libstdc++.so.6.0.14` | `aee778e0f0d6a4dc6031d3e50940ff831a4222d9efa438fcdd28599e07e75918` | `0x500A0000` |
| `libpthread-2.11.2.so` | `938387093567f5721bdeafa5bf68275d3a8705c8c6536745358e229af27fcab8` | `0x50080000` |

Additional static input `libInfraMscServer.so`:
`59e746ab655564d3a7d10778ceca49309787b811971cf96ff47e4fe6618cc165`.
The original `im.elf` is
`866729eb0bc1e6f5aba04d8bf8e45491985afe89a078ccb106a1ce2476865225`;
kernel symbol analysis uses
`12eeec829407595e28265f7d0db3b88c56f63a9f0fdd725c9c53c66a49643707`.
Python 3.15.2, Unicorn 2.1.4 and pyelftools 0.33 are recorded with each runner
hash. Cortex-A9 remains a provisional execution CPU, not camera timing.

Final repeated prerequisite replays observe **125,877** instruction-hook
visits each. Original libc returns 29 allocations and completes 20 frees.
Configure-stage ownership balances exactly. Nine allocations remain live at the
stopped start operation, including the registered gadget and pending command/
service objects; they are not reported as cleaned up or freed.

The final state is:

```text
UsbControl 0x54801D28: FSM=7, configured=4, started=4
usbif 0x000309C0 -> CUsbSvc 0x00030CF0
CUsbSvc: state=6, DID=55, selected_port=0
registry record 0x54805440 -> gadget 0x54805490, FID=16, created=0
enabled event context: 0x00030CF0 (original service registration)
createGadget thread 1024: SET_MODE(mode=2) unanswered
```

`created=0` is important: record/object registration is established, but the
gadget is not activated. No kernel event can be claimed merely from this list.

The replay's `/var/usbport` is an explicit one-byte, memory-only port-0 input
fixture, scoped to the original `O_RDONLY|O_SYNC` open, one-byte read and close.
It is not a captured camera file, does not touch the host filesystem and cannot
acknowledge a driver operation. Invalid flags, reads and operations stop.

## Persistence, negative controls and research receipts

The pending OTG status was absent from `checkpoint_resume_state`'s retry list.
It is now recognized specifically so this unanswered ioctl is preserved as an
EABI SVC retry, not skipped with its unserviced fd value left in `r0`. ARM and
Thumb regressions cover it; the final real-process boundary checkpoint was
restored and stopped on the same ioctl again without an acknowledgement.

Retained execution directories:

- `artifacts/execution/usb-gadget-startup-573298cc-cold-final03/`:
  fresh original-process report, bootstrap checkpoint and initial frontier.
- `artifacts/execution/usb-gadget-startup-573298cc-final04/` and
  `usb-gadget-startup-573298cc-final05/`:
  final-source replays with all seven library/executable preimage checks.
  `final05/repeatability.json` confirms identical source/input identities,
  traces, ownership, state and stop.
- Earlier `usb-gadget-startup-573298cc-final01/` and `final02/`:
  repeated trace, heap ownership, state, pending calls and boundary checkpoints.
  `final02/repeatability.json` compares exact trace/heap/state/boundary fields;
  `final02/retry-verification.json` verifies the pending ioctl is retried.
- `artifacts/execution/usb-gadget-startup-573298cc-final-msc-control/`:
  missing MSC user initialization, original -5, no created registry.
- `usb-gadget-startup-573298cc-boot01` through `boot13`, `start01` through
  `start03`, `buspower01` through `buspower03`, and `cold-final01`:
  earlier limits, missing namespaces, guarded stops, harness errors and the
  slow-hook timeout remain retained. Empty interrupted `cold-final02` is not
  claimed as a run receipt.

Qualified Ghidra 12.2 REA batches:
`artifacts/tools/rea/usb-gadget-startup-573298cc-infra02/` and
`usb-gadget-startup-573298cc-control02/`. Exact selected control entries are
`0x1AB62`, `0x220B0`, `0x21F58`, `0x21F52` in Ghidra's synthetic `+0x10000`
image; firmware/runtime addresses must not reuse that synthetic bias.
The ambiguous-name `control01` batch remains failed. The first direct WSL
invocation's Windows path spelling failed inventory resolution before import.

The separate workspace Ghidra MCP project is `usb-startup-573298cc.gpr`.
Forcing its language override initially imported raw bytes, not ELF; those
results were discarded. Actual ELF imports chose ARM v8, an unqualified loader
guess checked against the original ARMv7-A attributes, Thumb instructions and
bounded original execution. PyGhidra's shared project was locked and was not
altered or unlocked. Related-repository guidance was consulted; no upstream
runtime response was imported. At final session cleanup, the MCP program was
no longer open and `save_program` returned `Program not found`; no different
instance or program was selected to work around that. The completed REA
batches above remain the retained decompilation receipts.

Reverify 0.11.0 verified only the two byte/typed-read claims above against the
exact `libUsbControl` hash. An initial request with wrong parameter names
produced observations, not verification, and remains in its ledger. Static
kernel symbol/operation-table leads do not yet qualify the missing native
driver callback or gadgetcore commit.

## Reproduce and validation

Fresh offline process and prerequisite replay:

```powershell
.\.venv\Scripts\python.exe -m tools.research.probe_usb_gadget_startup `
  --cold-start --exercise-startup --function 4 `
  --report-dir artifacts\execution\usb-gadget-startup-NEW
```

Expected: **exit 2**, `process_usb_execution_boundary` with nested
`usb_otgcore_set_mode_reply_not_derivable`, not a successful configuration.
The output includes bootstrap and boundary checkpoints. Subsequent bounded
runs use `--checkpoint <bootstrap.ckpt> --checkpoint-sha256 <its exact SHA-256>`
instead of `--cold-start`, always with a new report directory. Checkpoints
must be trusted local artifacts; a digest is identity, not a pickle sandbox.
Function 1 is the distinct MSC missing-initialization control.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_usb_gadget_startup tests.test_guest_checkpoint tests.test_usb_local_setconfig tests.test_local_queue_post tests.test_usb_sync_callback tests.test_guest_usb_otgcore tests.test_guest_usb_event tests.test_guest_osal_alloc tests.test_interpreter_probe -q
```

With explicit `A6400_USB_PROCESS_CHECKPOINT` and
`A6400_USB_PROCESS_CHECKPOINT_SHA256` set to the trusted bootstrap:
**305 collected, OK, 1 skipped**. Without these variables the two process
cases skip rather than deserialize an arbitrary file. Constructed tests cover
identity/loaded-code mismatches, bounded linked registries, original-call CPU/
TLS preservation, free-return ownership, instruction exhaustion and the
scoped port-file fixture.

Full offline discovery uses E0533's physical-volume-denied, per-case-GC recipe,
with the same explicit trusted-checkpoint variables:

```powershell
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

Result: **2,009 collected, OK, 94 skipped**, zero failures/errors.
Receipts: `artifacts/execution/usb-gadget-startup-573298cc-validation01/`
`full-suite.log` and `focused-final.log`. The published suite telemetry keeps
E0533's separately dated L2 baseline; its content and native continuation result
are preserved.

The first `ci_check` attempt failed on OneDrive's atomic replacement of an
unrelated existing `.pyc`, not on source compilation. No existing cache was
removed. With `PYTHONPYCACHEPREFIX` set to this session's private
`files\usb-startup-pycache` directory:

```powershell
.\.venv\Scripts\python.exe -m tools.research.ci_check --summary
.\.venv\Scripts\python.exe -m tools.research.render_research_status --check
git --no-pager diff --check
```

Results: **5/5 CI guardrails PASS**, including 86 guardrail cases;
generated status check **PASS**; whitespace check **PASS**.
`ci-check-final.log` retains the isolated-cache result.

## Next boundary

Qualify the registered native OTG operations and their required runtime state,
then execute the original `createGadget` through class initialization,
gadgetcore callback/descriptor registration and its actual commit result.
Only afterward replay original event production, the guarded event reader,
service notification bridge, local synchronous/asynchronous delivery, FSM
acceptance and complete guest-heap teardown. `G_START` alone, a nonzero registry,
or the no-op SETCONFIG action is not a substitute for those prerequisites.
