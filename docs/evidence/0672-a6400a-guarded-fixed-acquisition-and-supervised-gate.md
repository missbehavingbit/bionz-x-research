# E0672 - a6400A guarded fixed acquisition and supervised connection gate

Date: 2026-11-15. **Offline implementation and qualification.**
No camera discovery, USB request, driver/mode change or physical dump is
performed by the qualification described here. This advances
[E0671](0671-a6400a-fixed-reader-framing-and-completion-controls.md)
from an offline protocol reader to a separately opt-in live acquisition client.
**It supports only the stock fixed `/dev/nflasha5` callback, not all partitions.**

## 1. Decision

The guarded client is implemented. Its first hardware use must be a
**supervised qualification**, beginning with fresh normal-mode identity and
original-driver receipts. Firmware **1.01 is explicitly owner-confirmed**;
the normal model response is measured, not a live installed-library hash.
The host must not claim raw captures until real bytes are received and
verified on disk.

The cancellation policy avoids the unsupported forced-abort assumption:

- Cancellation or host output failure drains a valid transfer within the
  original bounded deadline before one explicit STOP attempt.
- An incomplete, malformed or unknown transfer is not followed by an
  invented abort/STOP. Its partial file and recovery journal remain intact.
  The owner must perform ordinary power-cycle recovery.
- A PIPE/STALL on STOP remains unacknowledged, even when normal USB
  enumeration subsequently appears.
- Every outcome retains `cleanup_verified=false`. Even a complete file and
  acknowledged exit are not a physical descriptor/mapping-close attestation.

The previously observed owner restart and signed USBSTOR restoration in
[E0668](0668-a6400a-live-senser-entry-and-recovery.md) provide the recovery
precedent. They do not prove a future STOP will succeed.

## 2. Implementation

New client:
[a6400a_fixed_acquire.py](../../tools/research/a6400a_fixed_acquire.py).
The older FF01 client and its default guards are unchanged.

### Input and device admission

- Require explicit `--confirm-firmware-101`.
- Require original-driver inspect and successful native model receipts from
  the last **30 minutes**; reject future/naive timestamps.
- Bound each receipt to **64 KiB**, a regular file under repository
  `artifacts`, and hash the exact bytes parsed.
- Require the native parent-verified camera LUN, model `ILCE-6400A`,
  product-code bytes `0032858730`, and no updater initialization.
- Retain the original physical USB location and healthy driver/status checks.
- Accept only service **`054C:0336`**, vendor interface 0 / alternate 0,
  high-speed **512-byte** primary bulk endpoints.
- Require the same PyUSB device object used by the authenticated driver's
  backend. Select the driver's first descriptor-ordered bulk endpoints,
  never guessed endpoint addresses.

The original SDK's `GenericUsbDriver.write` and `UsbBackend.write` drop the
actual write count. The new non-owning adapter therefore calls the already
admitted PyUSB handle directly, with explicit timeout, retaining its **actual
acknowledged count**. `None`, short or invalid counts are not converted to a
successful request.

The adapter admits only one literal 16-byte `0601/32` request, bounds read
sizes/counts/bytes/time, and accepts actual empty USB buffers for the protocol
reader's terminator validation. No reset, clear-halt, kernel-driver detach,
terminal, updater, arbitrary command or file-write API is exposed.

### File and session ownership

- Keep the shared camera lock through authentication, streaming, STOP,
  handle disposal and normal-mode observation.
- Check host free space before the data request.
- Create an exclusive `nflasha5.bin.partial`, flush/fsync it, then read back
  its size/hash against the streaming payload/output hashes.
- Promote with an exclusive hard link and remove only the owned partial
  name. An existing final capture is never overwritten.
- Preserve a pre-request recovery journal and a one-shot marker keyed to the
  original baseline hash. Reusing the baseline is refused before discovery.
  Do not remove markers to bypass recovery.
- Install a cooperative SIGINT handler for the CLI and restore it after
  bounded exit handling. SIGINT stops output and drains; it is not a
  firmware cancellation acknowledgment.

The pure [reader](../../tools/research/a6400a_fixed_reader.py) now saves and
hashes each validated payload fragment immediately. A reproduced timeout
after 100 valid bytes previously retained zero bytes while assembling a
larger chunk. The new regression requires those **100 bytes** and their
partial accounting to survive. No bytes from an unvalidated header are
promoted to partition data.

## 3. Qualification evidence and limits

[Constructed acquisition tests](../../tests/test_a6400a_fixed_acquire.py)
exercise the actual PyUSB method signatures through mocked device handles,
not another permissive transport shim. PyUSB's installed `Device.write`
returns a count and `USBError` derives from `OSError`; both are checked
without opening a device.

The exact **62,914,560-byte / 60 MiB** profile runs through the complete
adapter, client, disk flush, readback hash and exit-report path:

- 60 remaining-byte response headers.
- 1,920 data chunks and 1,920 empty terminators.
- 3,900 bounded primary IN calls and one literal request OUT.
- Exactly 62,914,560 bytes saved; no mapping-padding bytes.

Constructed controls also cover handle/location/endpoint mismatch, write
count loss/shortness, malformed buffers, deadline expiry, stale or wrong
identity, SIGINT, disk-space refusal, partial-fragment timeout, retained
partial files, one-shot replay prevention and unacknowledged exit.

These are **constructed bytes**, not captured firmware. They do not establish
runtime level-3 admission, live capacity, flash stability during a read,
pthread forced unwinding, delivered OSAL completion, drained DMA or a
restored camera. E0671's original-code completion-delivery failure still
leaves the mapping/descriptor active.

Two failed development receipts are preserved:

- `failed-full-size-fixture.json`: the first large fixture supplied only one
  response frame. The real parser stopped after 1 MiB, retained the partial
  file and deferred STOP. The fixture, not the guard, was corrected to supply
  all 60 remaining-byte frames.
- `failed-partial-fragment-retention.json`: the measured 100-byte retention
  defect and its original reader identity, before the streaming fix.

The existing original-instruction framing/serializer/completion, reader
consumer and service-admission baselines are replayed under the new
`artifacts/analysis/e0672-a6400a-guarded-fixed-acquisition/` directory.
Source/input identities and guarded full-suite receipts remain separate from
physical observations.

Final focused validation passes **185 tests**. The guarded full suite runs
**1,813 tests: 1 failure, 4 errors, 151 skips**, exit **1**. Failure/error IDs
exactly match E0671's 1,795-case baseline, with **18 additional passing
tests**. The unchanged gaps are the original-target `__pipe` expectation and
missing `e0322-uvc-overlay-plan.json`.

The full harness additionally denies unmocked real PyUSB read/write/control,
reset and detach operations while preserving method signatures for fixtures.
USB discovery, native device APIs, camera-client/real-WSL subprocesses and
raw/non-repository drives are also denied. The legacy `E:` open is denied
before access; its synthetic fallback is not physical verification.
Complete harness, source hashes and failure logs are retained in
`full-validation/`; `offline-outcome.json` records the passed supervised
connection gate without claiming a dump. Editor diagnostics are clean.

## 4. Supervised sequence

Do not run a data request merely because the client imports or its tests pass.
Use the following sequence only for the owner-confirmed body/firmware, while
the owner is available for normal restart/recovery.

1. Connect the camera in **Mass Storage**, on the intended USB port, with
   adequate battery/power. Keep the original signed Microsoft USBSTOR
   binding for normal identity checks. Do not select PC Remote.
2. Save fresh receipts, each in a new directory:

   ```powershell
   .\venv\Scripts\python.exe -m tools.research.a6400a_senser_acquire --action inspect --output-dir .\artifacts\analysis\LIVE-ATTEMPT\original-binding
   .\venv\Scripts\python.exe -m tools.research.a6400a_native_readonly --inquiry --model-info --output-dir .\artifacts\analysis\LIVE-ATTEMPT\native-identity
   ```

   The native probe may need an owner-approved elevated process. It sends
   only the existing allowlisted inquiry/model transaction, not generic PMCA
   `infoCommand`, updater initialization or sector I/O.
3. Use only the already-reviewed camera-specific WinUSB binding for
   `054C:0F0F`; the separate `0336` binding was staged in E0668. Preserve host
   driver receipts. Never target `0994` or another device. Then:

   ```powershell
   .\venv\Scripts\python.exe -m tools.research.a6400a_senser_acquire --action enter --permit-control-pipe --baseline .\artifacts\analysis\LIVE-ATTEMPT\original-binding\receipt.json --output-dir .\artifacts\analysis\LIVE-ATTEMPT\enter
   ```

4. Only after service identity/admission, use one new output directory:

   ```powershell
   .\venv\Scripts\python.exe -m tools.research.a6400a_fixed_acquire --baseline .\artifacts\analysis\LIVE-ATTEMPT\original-binding\receipt.json --identity-receipt .\artifacts\analysis\LIVE-ATTEMPT\native-identity\receipt.json --confirm-firmware-101 --permit-control-pipe --output-dir .\artifacts\analysis\LIVE-ATTEMPT\nflasha5-read
   ```

5. Inspect the receipt, not just process exit or file existence. A complete
   file with unacknowledged exit remains
   `fixed_partition_captured_recovery_required`, exit 2. A failed transfer
   remains a partial capture, even if its length looks plausible.
6. If recovery is required, do not replay, force a STOP into an unknown
   transfer, install a daemon or unload a stock module. After the bounded
   client ends, use the owner's ordinary power-off/restart procedure, restore
   original USBSTOR and obtain a fresh native model response. Ask for guidance
   if ordinary controls do not respond; do not silently escalate to flashing
   or forced storage operations.

Normal USB enumeration is not a safe-eject certificate. Verify original
driver, responsiveness and ordinary Windows safe removal before unplugging.
No `/proc/uptime` or `/tmp_bt` observation is invented when the stock file API
remains unavailable.

## 5. Remaining goal

The next physical objective is **one genuine nflasha5 capture**, or a precise
live admission/transfer stop with verified owner recovery. All other
`nflasha*` nodes still require an original, qualified read contract or a
separately reviewed nonpersistent bootstrap. Updater-extracted images are
not substituted for raw captures, and the old installer/preload guards stay
intact.

[E0673](0673-a6400a-first-actual-nflasha5-capture.md) records the first
supervised hardware result: an actual 60 MiB nflasha5 capture with independently
verified host hashes. It preserves the failed handoff and unacknowledged exit;
the other partitions and camera cleanup are not promoted to verified.
