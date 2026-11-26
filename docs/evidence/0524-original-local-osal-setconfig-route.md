# E0524 - Original process-local OSAL SETCONFIG route into the USB FSM

Date: 2026-11-17. Offline original-instruction replay with explicitly constructed
process, thread, heap and pthread-mutex fixtures. No camera access, device
writes, USB enumeration, firmware installation or mode change.

## Result and scope

The original **local synchronous send followed by an asynchronous post** now
has a reproducible, opt-in execution probe:

```text
UsbIfWrapper::EventListner
  -> UFW::MsgSender::SendEvt
  -> osal_snd_sync_msg(0x00CA8192)
  -> original local sync dispatcher
  -> UsbControl::funcOsalSyncCb -> preProcess
  -> UFW::MsgReceiver::PostMsg -> osal_snd_msg
  -> original WAIT_CB handoff + FUTEX_WAKE_PRIVATE
  -> UsbControl::Main -> RcvMsg -> ProcessMsg
  -> MsgId2EventId(0x0A4001) -> Fsm::DispatchEvent(0x13)
  -> original FSM table execution
  -> typed GetInt32(0x1006) == 0
  -> original 0x0A4002 notification construction/broadcast attempt
  -> original message destruction -> next blocking receive
```

The notification-target list is an **empty fixture**: notification construction
and the original broadcast call execute, but delivery to a notification consumer
is not claimed. The original FSM is explicitly initialized to startup state 0
and remains in state 0. **Event delivery is not a USB mode transition.**
Original `Fsm::handleEvent` returns **`0xFFFFFFFF` (-1)** for event `0x13`
in this startup state; no action is selected. The receipt exposes that result
as `fsm_handle_event_return` and `fsm_event_handled=false`, rather than treating
transport completion as acceptance of the configuration.

This closes the missing first-stage execution proof relative to E0335/E0337,
which started at a modelled asynchronous post. It does not replace those
checkpoint observations or qualify a full-process or physical-producer replay.

The adjustment packet ABI was already decoded in
[E0333](0333-sync-callback-packet-format.md) /
[E0334](0334-sync-callback-lane-is-usb-adjustment.md). Neither kernel queue
`0x00CA0288` nor `0x00CA01A4` carries this SETCONFIG message. The new probe
explicitly refuses execution of their adjustment/input callback entries.

## Input and tool identities

Inputs are read-only files below the preserved ILCE-6400 package extraction.
The historical live-library captures and E0673 checkpoints are not present in
this workspace, so this is not described as a resume of that process.

| Input | SHA-256 |
| --- | --- |
| `nflasha15_unpacked/lib/libosal_uipc.so` | `b6ded35add9582413b401cf4db78ec2c9a201b632e78c2731d6bbb379249b3eb` |
| `nflasha15_unpacked/lib/libMWF.so` | `a79dc0d558e6454e31e57ea3b026364be924d0e78c1b6101015125a249f7e274` |
| `nflasha15_unpacked/lib/libUsbControl.so` | `70cd5742fde9eea78c60905e4015531b4c3ab2668d2cb85c934531f451f1211f` |
| `nflasha7_unpacked/lib/libstdc++.so.6.0.14` | `aee778e0f0d6a4dc6031d3e50940ff831a4222d9efa438fcdd28599e07e75918` |

Runner:
[`probe_usb_local_setconfig.py`](../../tools/research/probe_usb_local_setconfig.py).
Python 3.14.6, Unicorn 2.1.4, pyelftools 0.33; selected execution CPU is
`UC_CPU_ARM_CORTEX_A9`, not a claim of a Cortex-A5 timing model. Each JSON receipt
records the input identities/load biases, runner SHA-256, tool versions, serviced
imports, original-function arguments/returns, checks, stop PC and registers.

The explicit isolated link scope supplies only these four ELF providers.
Original C++ allocation/deallocation and list helpers execute; their libc heap
imports use one checked allocation fixture. No firmware bytes are patched.
Unresolved imports, accessed unsupplied data/TLS relocations, unexpected control
flow and execution limits stop explicitly rather than returning success.

## Original instruction support

Offsets below are ELF virtual addresses with the Thumb bit cleared, not live
process addresses:

| Library | Offset | Original responsibility |
| --- | --- | --- |
| `libUsbControl` | `0x12D28` / `0xA6E8` | `EventListner` / `SendEvt`: allocate an 8-byte OSAL envelope, write the source and command, synchronously send, then free the sender envelope |
| `libosal_uipc` | `0x3AE0` | Register the real local callback record and queue through original allocation/table creation |
| `libosal_uipc` | `0x3EC8` / `0x3DB0` | Local synchronous send: bit-15 routing, queue lock, sync marker, activity/TLS bookkeeping, dispatcher call and completion |
| `libosal_uipc` | `0x3168` | Unlock, call `callback(head+0xC, user_data)`, then relock; the host never invokes that callback |
| `libUsbControl` | `0x1261C` / `0x123B4` | Local callback thunk / `preProcess`: recognize the SETCONFIG message and call `PostMsg` |
| `libUsbControl` | `0xA918` | Copy the ObjMsg using its original copy constructor, allocate a separate envelope, copy the source and call `osal_snd_msg` |
| `libosal_uipc` | `0x3C34` / `0x3848` / `0x32B0` / `0x5100` | Original asynchronous send, waiter unlink, output-slot delivery and futex wake |
| `libUsbControl` | `0x112C4` / `0xA9A8` | Original `Main` receive loop and `RcvMsg`; the receiver envelope is freed before processing |
| `libUsbControl` | `0x10ED4` / `0x104AC` / `0xAFD2` | `ProcessMsg`, original message-to-event lookup and FSM dispatch; Main subsequently destroys/deletes the message |

Targeted REA/Ghidra 12.1.4 results and instruction queries are retained under:

- `artifacts/tools/rea/usb-osal-route-20261117-control/`
- `artifacts/tools/rea/usb-osal-route-20261117-osal-exact/`

Ghidra selected synthetic image base `0x10000`; for example its OSAL entry
`0x13AE0` corresponds to ELF VA `0x3AE0`. Capstone instruction checks used ELF
coordinates. The earlier batch
`artifacts/tools/rea/usb-osal-route-20261117-osal/` is preserved as **failed**:
the registration name matched both a PLT thunk and its definition. The separate
successful batch uses exact entry addresses; the failed batch is not reused as
completed evidence. The related-repository catalog was consulted; no upstream
runtime contract or camera authorization was imported.

## Execution observations and ownership

Final receipts:
`artifacts/execution/usb-osal-route-20261117-final/{setconfig,no-registration}.json`.

Positive case:

- Original constructors build `{category=0x6103, message_id=0x0A4001,
  param 0x1006=int32 0}`. No in-image ParamMap, fabricated OSAL message header,
  host queue-link writes or free-suppression hook is used.
- Original registration creates the queue object and callback record. Sender
  thread 1000 executes the local callback synchronously; activity returns to 0,
  TLS records the sender, the queue unlocks and the send returns 0.
- Original `PostMsg` copies the command object and posts a **different**
  8-byte envelope. Original dispatcher code writes the blocked receiver's
  output slot, unlinks its WAIT_CB record and stores its futex word.
- The syscall fixture receives `FUTEX_WAKE_PRIVATE`, wakes exactly one
  receiver and returns the actual wake count. Original receiver thread 1001
  resumes its saved context; the host does not call `ProcessMsg` or the FSM.
- Original lookup returns event 19, original FSM execution runs, the typed
  parameter read returns 0, and the original notification has message ID
  `0x0A4002`. Main destroys/deletes the copy and blocks for another message.
- Both original OSAL envelopes are released, queue allocation count is 0 and
  fblock free bytes return to baseline. Seven checked heap allocations have
  seven matching frees; no live heap allocation or pthread mutex remains.
- Stop: next legitimate receiver futex wait, after **16,948 instruction-hook
  observations**, including fixture-import/return hooks, below the 100,000
  instruction budget. FSM state is **0 -> 0**.

Negative control, with only the local sync registration omitted:

- Original `__osal_snd_sync_msg` returns **`0xFFFFFC04` (-1020)**.
- No local callback, `preProcess`, post, wake, `ProcessMsg` or FSM dispatch
  occurs; the original receiver remains blocked.
- The sender envelope and all three message heap allocations are released.
- Stop after **13,605 instruction-hook observations**; no error is converted
  into a successful delivery.

The original asynchronous post/lifetime explanation is now executable without
the incompatible injected OSAL-backed Param/ParamMap objects of
[E0338](0338-tty-log-sink-teardown-and-usb-event-map.md). This validates ownership
against the **checked heap fixture**, not against guest glibc/stella.

## Reproduce and validation

From the repository root, using a fresh report directory:

```powershell
.\venv\Scripts\python.exe -m tools.research.probe_usb_local_setconfig --report-dir artifacts\execution\usb-osal-route-20261117-final
```

Result: exit 0; `original_local_setconfig_delivered` and
`original_local_setconfig_no_registration`. Earlier independent attempts and
the optional original-input tests reproduce the same instruction counts.
The initial launcher import typo was corrected before the first firmware
execution; no failed firmware receipt was overwritten.

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_usb_local_setconfig tests.test_local_queue_post tests.test_usb_sync_callback tests.test_usb_host_enum tests.test_guest_osal_alloc tests.test_interpreter_probe -q
```

Result: **230 collected, OK, 15 skipped**. Seven new regressions cover the
original ordered two-stage route and ownership, missing registration, bounded
execution, explicit wrong-lane refusal and checked fixture allocation failures.
Original-input cases skip explicitly if the preserved ELF files are absent.
Final focused output is preserved in
`artifacts/execution/usb-osal-route-20261117-verified02/final-usb-checks.log`.

The combined USB, repository, target-profile, documentation and SVG check also
passed: **310 collected, OK, 15 skipped**:

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_usb_local_setconfig tests.test_local_queue_post tests.test_usb_sync_callback tests.test_usb_host_enum tests.test_guest_osal_alloc tests.test_interpreter_probe tests.test_repository_layout tests.test_target_profiles tests.test_research_status_sync tests.test_research_status_dashboard -q
```

Receipt:
`artifacts/execution/usb-osal-route-20261117-verified02/final-checks-attempt01.log`.
Status generation/check and `git diff --check` passed before the shared status
blocks were handed to the E0525 workstream; no overlapping regeneration follows.

A separate CLI control with `--instructions 8` and a fresh report directory
`artifacts/execution/usb-osal-route-20261117-limit` returns **exit 2** with two
explicit `execution_limit` receipts at eight observations each, never delivery.

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -q
```

Result on this Windows environment: **1902 collected, FAILED (17 errors,
126 skipped)**. All errors are `UC_ERR_NOMEM` while the unchanged L2 kernel
fixtures map large RAM banks (`test_data_abort` and one `test_dmp_p3d` case),
before the new USB cases execute. Full output:
`artifacts/execution/usb-osal-route-20261117-verified01/full-suite.log`.
The single existing case
`tests.emulator.test_data_abort.DataAbortDeliveryTests.test_guard_is_keyed_on_decoded_va`
also fails independently with the same allocation error; its receipt is beside
the full-suite log. These unrelated fixtures were not modified.

The dashboard retains its last fully passing **2026-11-15 / E0523** suite
record rather than presenting this failed run as a new passing total. The
passing-suite date is now independent of the latest research date.

## Remaining boundary

This probe starts at an explicit `EventListner` stimulus and uses an initialized
startup-state fixture. A complete im.elf replay still needs its original
runtime heap, notification consumers and a **state-valid gadget event producer**.
E0338's retained live checkpoint has no enabled gadget-class registration;
injecting an OTG event or an adjustment packet cannot substitute for it.
No context, descriptor, hardware acknowledgement, mode transition or frame is
invented to advance that boundary. Existing emulator defaults are unchanged.
