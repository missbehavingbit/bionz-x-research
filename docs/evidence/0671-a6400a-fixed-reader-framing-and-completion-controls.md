# E0671 - a6400A fixed-reader framing and completion controls

Date: 2026-11-15. **Entirely offline; keep the camera disconnected.**
No USB enumeration, request, mode/driver change, camera write, installer,
preload hook or physical-memory operation occurred. **No raw partition bytes
were captured.** This follows
[E0670](0670-a6400a-ghidra-reader-consumers-and-fixed-partition-candidate.md).

## 1. Decision and demonstrated scope

A bounded host protocol reader now replays the original fixed-node response
framing through exactly **62,914,560 constructed bytes (60 MiB)**, excluding
mapping padding. Cancellation and output failures drain a valid bounded
stream but remain errors, never successful acquisitions.

Original response extraction arms completion state **`0x10` before streaming**.
The original transaction destructor, packet builder and registered callback
can then unmap, unlock and close the owned fixed-nflasha5 resources under
declared synchronous OSAL/libc fixtures. This advances beyond E0670's direct
callback release, but **does not execute a real pthread forced unwind or
attest an acknowledged USB STOP**.

A significant remaining failure is now explicit: if completion-message
delivery fails, the original destructor frees its OSAL message while leaving
the mapping and descriptor active. An unarmed state also does not release
them. Do not equate host byte completion, a cancellation request, a freed
message or normal USB enumeration with released camera resources.

No live transport adapter or acquisition action is enabled. The existing
FF01-based acquisition client and its guards remain unchanged; they cannot
use this new fixed-node protocol. This is not a general `nflasha*` reader,
root debugger or successful firmware dump.

## 2. Preserved inputs and tools

All inputs come from the already-preserved official ILCE-6400A 1.01 package:

| Input | SHA-256 |
| --- | --- |
| `nflasha15_unpacked/lib/libsencore.so` | `08804d8752c0bdfbad6e3f212cc1feaf2145e71e866a64c3f94bc4c2eca36089` |
| `nflasha15_unpacked/lib/libInfraUsbCmn.so` | `1db8f013b8aa159ab647f4388c2322ec7b8a4f0fe3150b6cac287ca42b490d9f` |
| `nflasha15_unpacked/kmod/usbg_sen.ko` | `d58e96a1cd4c22d5411d3049f67830d4e96bc97c0b0f343e64b3120a9e0904ad` |

Connected Linux REA **6.9.0** / Ghidra **12.1.4** performs actual decompilation
of these targets. Original core instructions run separately in bounded
Unicorn controls with declared Cortex-A9 CPU, memory, object and I/O fixtures.
Those fixtures are not a live installed-byte or silicon attestation.

For these shared libraries, Ghidra analysis addresses equal linked ELF
addresses plus `0x10000`; the Unicorn bias is separately `0x01000000`.
The kernel module's `.text` coordinates are section-relative. None of these
coordinates is presented as a camera runtime PC.

Implementation:

- [a6400a_fixed_reader.py](../../tools/research/a6400a_fixed_reader.py):
  one-shot, fixed-command host parser with explicit byte/count/deadline bounds.
- [a6400a_transfer_controls.py](../../tools/research/a6400a_transfer_controls.py):
  original response framing, serialization and completion/release controls;
  its CLI only consumes private offline inputs and constructed USB events.
- [a6400a_reader_controls.py](../../tools/research/a6400a_reader_controls.py):
  the existing fixed-node fixture setup is extracted for reuse without
  changing the earlier lifecycle API or its failed-map residue.
- [test_a6400a_fixed_reader.py](../../tests/test_a6400a_fixed_reader.py):
  constructed protocol edge cases and optional hash-checked original-code cases.

## 3. Exact request and response framing

The only transmitted request admitted by the constructed transport is a
16-byte packet:

```text
size=4, pFunc=0x40, sequence=1, version=0,
micon=0, offset=0, response=0, block=0x0601, command=0x0032
```

The reader has no arbitrary pathname, partition selector, memory address,
write command, authentication or USB discovery interface. Its sole node is
the stock callback's hardcoded `/dev/nflasha5`. The expected capacity must be
positive, sector-aligned and no greater than the configured **60 MiB** cap.
That configured size is not a live capacity observation.

Original `SendResponse`, linked **`0xEFA4`**, writes response version **3**.
With a separate mapped `MemoryData` source it emits:

1. A 12-byte header with the **remaining total bytes**, pFunc `0x40`, matching
   sequence and unchanged success response `0`.
2. At most **`0x100000` bytes per frame**.
3. Another remaining-byte header until all bytes are consumed.

At exactly 60 MiB, controls observe **60 headers**: first size `62,914,560`,
last size `1,048,576`; payload total is exactly `62,914,560`, not the
callback's `62,918,656`-byte mapping size.

Boundary controls also cover 512 bytes, 1 MiB minus 512, exactly 1 MiB and
1 MiB plus 512. The host rejects wrong capacity/progression, function,
sequence, version, micon, offset or response before accepting that frame.
It does not retain the old FF01 parser's version-zero expectation.

The framing control executes original `SendResponse` and `MemoryData` methods,
but supplies explicit header/data serialization and mutex fixtures. The
separate serializer control below covers original byte-wise progression;
these are not represented as one complete host-to-camera execution.

## 4. Serialization and the USB boundary

Original `Serialize`, linked **`0xED70`**, retries positive short I/O results
by advancing the pointer and reducing the outstanding count. Original
`SenserIfSend`, linked **`0x10AC0`**, caps each underlying write at
**`0x8000`** bytes. Original `MemoryData` methods and packet serialization
advance a 65,536-byte declared source exactly under short-write fixtures.

Zero, negative (`-1`, `-4`, `-6`) and larger-than-outstanding-count (`65,537`)
fixtures reach a declared boundary **before `pthread_testcancel`** without
advancing `MemoryData`. No infinite yield loop is treated as success, and no
actual thread cancellation is supplied.

The lower call chain reaches `libInfraUsbCmn`'s `CUsbGadgetSen::send`,
linked **`0xE434`**, issuing ioctl **`0x400CE800`**, then `usbg_sen.ko`'s
`fops_ioctl`. The send branch sets request byte **`+0x1A`, bit 1** for the
final queued request and clears it for intermediate requests. Original ARM
instructions at `.text+0xD74..0xD94` corroborate this conditional update.
That is consistent with the Linux gadget zero-packet flag and the reviewed
upstream host's terminator reads; **controller behavior is not executed**.

The constructed host transport explicitly supplies empty USB terminators
after sector-aligned data transfers. The new reader requires an empty
terminator rather than silently discarding 512 bytes. Unexpected empty
payload/header transfers, nonempty terminators and oversized transfers fail.
This fixture contract is not a live packet capture or a controller/DMA proof.

## 5. Original completion and ownership controls

Original response extraction, linked **`0x12B50`**, accepts the callback's
mode-8/status-1/type-2 mapped response, returns its pointer and stores command
completion state **`0x10`**. This happens before `MemoryData` streaming.

The new control executes, in order:

1. The original fixed-node callback acquiring declared read-only resources.
2. Original response extraction, observing state `0x10`.
3. The original transaction destructor, linked **`0x12A70`**.
4. Its original mode-10 OSAL packet builder, linked **`0x12040`**.
5. A declared synchronous OSAL dispatch into the **original registered
   callback**, then the original unmap/unlock/close branch.

Libc calls, OSAL delivery/free and base destructors are explicit fixtures.
No actual device is opened or mapped; no queue or thread is invented in a
guest compatibility experiment.

| Constructed destructor state / delivery | Observation |
| --- | --- |
| Original extracted `0x10`, delivery succeeds | Callback unmaps, unlocks, closes; descriptor `FFFFFFFF`, mapping pointer/size zero |
| State `0` | Message freed; no mapped-resource completion; declared descriptor/mapping remain active |
| State `8` | Completion and message free skipped; declared descriptor/mapping remain active |
| State `0x10`, OSAL delivery fails | Message freed, but no release callback; descriptor/mapping remain active |

States `0`/`8` are negative fixture controls, not proof that cancellation can
occur at every such point. `SenserThread` registers cleanup before command
execution and enables cancellation in serializer error loops, but actual
pthread/C++ forced unwinding, concurrent module shutdown and USB STOP remain
outside these bounded controls.

## 6. Host interruption and deadline policy

The reader uses one total deadline, at most **120 seconds**, per-I/O timeouts
at most **2,000 ms**, a finite fragmentation budget, and no request retry.
It cannot be reused after either success or failure.

- Cancellation before the request sends nothing.
- Cancellation during a valid transfer stops output and drains the declared
  stream within the original deadline; it raises `cancelled_drained`.
- Short or failed host writes likewise drain, preserve confirmed output
  accounting/hashes and raise `output_failed_drained`. A write exception
  explicitly marks output integrity unknown.
- Bad headers, transport failures, unexpected packet boundaries, deadline or
  I/O-limit exhaustion stop with partial accounting and **unverified cleanup**.
  No invented cancellation packet, STOP or recovery success is returned.
- Even a final terminator received after the deadline fails. A focused
  counterexample initially returned wire completion at time 2 with deadline
  1; the post-I/O deadline check and its regression test close that defect.

A successful result is named `stream_complete_cleanup_unverified`.
Every result/error keeps `cleanup_verified=false`. Constructed 60 MiB replay
is not a physical dump and cannot be promoted to one by its hash or filename.

## 7. Reproduction, validation and retained failures

Run from the repository root with the existing environment, choosing new
output directories:

```powershell
.\venv\Scripts\python.exe -m tools.research.a6400a_transfer_controls --output-dir .\artifacts\analysis\e0671-a6400a-fixed-reader-framing\controls-final
.\venv\Scripts\python.exe -m tools.research.a6400a_reader_controls --output-dir .\artifacts\analysis\e0671-a6400a-fixed-reader-framing\earlier-reader-regression
.\venv\Scripts\python.exe -m tools.research.a6400a_service_contracts --output-dir .\artifacts\analysis\e0671-a6400a-fixed-reader-framing\earlier-service-regression
.\venv\Scripts\python.exe -m unittest tests.test_a6400a_fixed_reader tests.test_a6400a_service_contracts -q
```

All three final replays exit **0** at their declared boundaries. New receipts
contain **15 original-code controls** (5 framing, 6 serializer and 4 completion)
and **7 constructed host replays**, including cancellation and output failure.
The earlier **25 reader controls and 164-case service baseline** also replay
successfully after fixture extraction.

The final combined regression command passes **144 tests**:

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_a6400a_fixed_reader tests.test_a6400a_service_contracts tests.test_a6400a_senser_acquire tests.test_msc_extcmd_trace tests.test_usb_debug_abi_trace tests.test_rea_wsl_batch tests.test_repository_layout tests.test_target_profiles tests.test_research_status_sync tests.test_research_status_dashboard -q
```

Pylance diagnostics and syntax checks are clean for the new reader, controls,
tests and shared reader helper. The physically guarded full suite runs
**1,795 tests: 1 failure, 4 errors, 151 skips**, exiting **1**. Failure/error
IDs exactly match E0670's 1,777-case run, with **18 additional passing tests**:
the existing original-target `__pipe` expectation and missing
`e0322-uvc-overlay-plan.json` remain unchanged.

The full-suite harness denies USB discovery, camera-client/real-WSL
subprocesses, native device APIs and raw/non-repository drives. It denies the
legacy raw `E:` open **before access**; that test's synthetic fallback is not
physical verification. The exact harness, validated source hashes and complete
failure log are preserved in `full-validation/`. No input or guard is weakened
to obtain green. Dashboard/link/layout checks remain separate from this
non-green full-suite result.

The first new CLI receipt is preserved as blocked: the send callback has no
PLT import, so the control must allow its actual local entry, not fabricate a
PLT alias. Initial focused failures also exposed a real Thumb-to-ARM import
veneer before diagnostic logging; only its verified original `bx pc`/nop
bytes are admitted. An initial oversized-I/O fixture was larger than the
32-KiB inner clamp but not the outer outstanding count; it is corrected to
test the actual original comparison, not a strengthened firmware guard.

A dependency-plan attempt stops before analysis because its default
historical `elf-inventory.json` is absent. No old-body inventory is substituted
or guard weakened. Connected Ghidra directly analyzes the explicitly selected,
identity-matched module. Failed/ambiguous procedure selectors are retained;
corrected queries use observed entries and linked/Ghidra translation.

Complete REA bundles, input identities, original-code replays and validation
receipts are retained privately under
`artifacts/analysis/e0671-a6400a-fixed-reader-framing/`. These are offline
receipts, not captured camera firmware. All owned Ghidra engines are closed;
the complete 23-record final bundle is saved before investigation reset.

Key REA evidence:

- Response framing and `MemoryData`: `ev_510432a8fb21c6c95d2a5af778e9cc590fcd764a22211495e0180668e0ecb931`.
- Framing instructions: `ev_b55c9c7e59df36d84b2cbbc25fe18c47f4c2e847d9cc507014ecb38b1725304c`.
- Serializer/thread cleanup: `ev_28281e8b05a25e07f128007871ac6aa9ba6943ef8b8068e1efee6e0785b147b8`.
- Serializer instructions: `ev_b3ee0d4c895f4c70a8c508fa9118780856d79854d6dda104f7ce9a29789b5482`.
- Original completion-state store: `ev_172d93ab110507c8cf71e574b75143e8ec386e916ba900760f09f027049b0304`.
- Shared-library send ioctl: `ev_0b62adfadf8165d9b8a60aca51d18f26bfac4a5db8b835b409d24c4db1ab914d`.
- Kernel ioctl/send completion: `ev_ead2a3a124b9b5a44814c360a80d447409985417ebccd996bf64e81c1c6b0158`.

## 8. Next connection gate

**Keep the camera disconnected.** Before a live fixed-node test:

1. Adapt the guarded, already-authenticated `054C:0336` session to this
   reader without weakening the legacy empty-read/write-command guards.
   The reviewed upstream wrapper drops write byte counts; never turn that
   `None` into a fabricated fully acknowledged request.
2. Qualify request write accounting, empty-terminator handling and the real
   cancellation/STOP handoff against the original transport/runtime contracts.
3. Preserve a clear failure/recovery receipt when completion delivery or exit
   is unacknowledged. Mapping failure residue from E0670 remains relevant.
4. Only then perform a supervised model/firmware/admission/read qualification.
   Even success qualifies **nflasha5 only**, not the remaining partitions.

[E0672](0672-a6400a-guarded-fixed-acquisition-and-supervised-gate.md) implements
the separate guarded adapter/client and a supervised sequence. It preserves
real write counts and partial fragments, drains cooperative cancellation and
requires owner recovery instead of an unqualified abort on unknown transfers.
