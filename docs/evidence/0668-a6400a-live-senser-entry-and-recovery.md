# E0668 - a6400A live Senser entry, file-read refusal and recovery; no partition capture

Date: 2026-11-15 UTC; first entry/recovery on 2026-11-14 UTC. The owner explicitly approved
necessary acquisition operations. **No firmware image, executable payload,
shell command, camera file write, raw-sector write or physical-memory scan
was issued. No partition data was captured.**

Offline follow-up: [E0669](0669-a6400a-offline-senser-dispatch-and-native-read-gates.md)
reproduces the missing-handler/response-prefix decision in original instructions,
distinguishes real adjustment gates and maps native command declarations.
It performs no additional camera request.**

[E0670](0670-a6400a-ghidra-reader-consumers-and-fixed-partition-candidate.md)
adds actual Ghidra and downstream original-instruction controls. It identifies
a fixed-node candidate but does not authorize or perform reconnection or a dump.**

## Result

**Drivers and authentication are now working, but the tested file request is
refused. No raw partitions have been dumped.** The owner staged WinUSB for
the previously observed `054C:0336` before repeating entry. Two new entry/probe
runs reach a healthy service device at the saved port and complete all original
authentication rounds.

The first live `/proc/version` request stopped at a non-matching envelope.
One diagnostic repeat retained the complete, non-credential reply header:

| Field | Live value |
| --- | --- |
| Payload size | 0 |
| Function / sequence | `0xFF01` / 1 |
| Version / micon / offset | 3 / 0 / 0 |
| Response status | `0x80` |

The tool's version guard remains unchanged. Independently, pinned PMCA
`_sendFileControlPacket` requires response 1 and would reject this response 128.
No version data, uptime, partition inventory, raw device bytes or shell command
was obtained. The first reply's fields were not retained; they are not
retroactively reconstructed from the second request.

Both attempts send an explicit STOP under the declared PIPE policy. Those
controls remain **unacknowledged**, not proven successful commands. Subsequent
host-only observations find healthy `054C:0F0F`/WinUSB at the original port.
This establishes normal USB re-enumeration, not a root-shell exit or complete
device cleanup proof. Final original-driver restoration is recorded separately.

### First entry and owner recovery (historical)

**Live Senser entry works on this a6400A.** After the camera-only Windows
binding changed from USBSTOR to WinUSB, the original authentication sequence
completed all three SHA-256 challenge rounds and final acceptance. The camera
re-enumerated as `054C:0336` at the original USB port.

The separate service-mode device had Windows error 28/no driver. Before that
driver could be installed, the owner reported a black display and `054C:0994`.
Host-only observations confirmed a healthy HID/charging identity at the same
port. No command was sent to `0994`, and its driver was not replaced.

The owner performed one ordinary restart and restored the normal camera's
Microsoft USB Mass Storage binding. Final observations verify:

- `054C:0F0F`, USBSTOR, OK/error 0, original physical USB location.
- Original `usbstor.inf`, Microsoft, signed, version `10.0.26100.9444`.
- Owner reports LCD/menu working and the display showing USB Mode Mass Storage.
- A bounded native inquiry/model query succeeds: ILCE-6400A / `0032858730`.
- No service reader was reached, no `/proc`/live module bytes were read, and no
  native debugger or temporary camera helper was installed.

**Authentication success is not a dump or a proven Senser exit.** Software STOP
and service read access remain unverified. Recovery here used owner actions,
not an inferred successful STOP.

## Inputs and implementation

- Existing Sony-PMCA-RE checkout remained clean at commit
  `a82f5baaa8e9c3d9f28f94699e860fb2e48cc8e0`.
- The missing Windows USB runtime was restored in the existing project venv:
  `libusb-package==1.0.30.1`; DLL SHA-256
  `698752cd7f4634c36a7e5dd18006ef27b839fd851db571b3991c750351d8bf55`.
- Zadig 2.9 was obtained from its official release and verified as signed by
  Akeo Consulting; SHA-256
  `4ecaa95df3da3621486a043aef8b3050b8bafe7c901402871e816229ef82039b`.
  Its elevated window could not be controlled by the medium-integrity UI
  engine; the owner performed the exact camera-only driver changes.
- [a6400a_senser_acquire.py](../../tools/research/a6400a_senser_acquire.py) adds
  opt-in entry/read/leave actions, original-driver/location admission, bounded
  transport/output guards, credential-redacted operation receipts and explicit
  cleanup. It sends no updater, shell, file-write or memory-dump request.
- The new service package is `oem40.inf`, hash-matched to its driver-store
  INF/catalog; its catalog and Microsoft `WinUSB.sys` signatures are valid.
  The later normal-mode binding is `oem79.inf`, also hash-matched and signature
  verified. WMI reports `IsSigned=false` for the latter; that observation is
  retained, not changed to true. Admission instead uses the actual bound
  package's valid catalog and the signed Microsoft driver binary.
- The initially collected service-package receipt used PowerShell's automatic
  `$Matches` variable for a path, which a regex overwrote. The bad path field
  is preserved and explicitly superseded by
  `service-driver/staged-windows-package-verified.json`.
- Earlier access prototypes were recovered into a new ignored output directory,
  not over the worktree. Their Windows CRLF bytes match the historical receipt
  hashes exactly; Git LF blobs and conversion are recorded separately.

## Failed attempts and corrected prerequisite

The first strict entry attempt stopped on a vendor-control PIPE/STALL before
authentication. One cleanup-only diagnostic retained the actual USB error:
errno 32, libusb backend `-9`. No unsupported identity was used.

The reviewed PMCA implementation permits start/stop control stalls. The new
`--permit-control-pipe` policy permits only that exact error, records the
control as **unacknowledged**, and still requires real authentication and
re-enumeration. Its first challenge write stalled; no reply or entry was
credited.

The wrapper also needed PMCA's original Mass Storage readiness/inquiry
prerequisite. The owned transport was recovered using the standard MSC class
reset and clear-halt operations, followed by real Sony DSC inquiry. The corrected
entry then completed:

| Authentication operation | Observed result |
| --- | --- |
| Challenge command 1, three rounds | 8 (SHA-256 challenge) |
| Response command 3, three rounds | 4 (accepted) |
| Final command 5 | 6, accepted result byte 1 |
| Post-entry host observation | `054C:0336`, same USB port, no driver/error 28 |

Raw authentication material and keys were not retained. These are live
observations, unlike the earlier package-only controls in
[E0667](0667-a6400a-debugger-abi-and-live-channel-boundary.md).

## Receipts and validation

Private receipts remain under
`artifacts/analysis/e0512-authorized-nflasha-acquisition/`: original binding,
normal native preflight, runtime/source recovery identities, three entry
attempts, control/transport diagnostics, restored binding, recovery query and
test results. Failed attempts are preserved.

Core successful entry command:

```powershell
.\venv\Scripts\python.exe -m tools.research.a6400a_senser_acquire --action enter `
  --permit-control-pipe `
  --baseline .\artifacts\analysis\e0512-authorized-nflasha-acquisition\original-binding\receipt.json `
  --output-dir .\artifacts\analysis\e0512-authorized-nflasha-acquisition\senser-enter-03
```

Result: exit 0 / `service_entered`, not read or deployment qualification.

Focused transport/recovery controls: **42 cases, no failures/errors**.
Pylance reports no errors in the new tool. The guarded full suite is **not
green**: **1,752 run, 1 failure, 4 errors, 151 skips**, matching the earlier
missing historical symbol/descriptor inputs. A legacy raw-SD open was denied
before access; its synthetic fallback is not physical verification.
No ordinary test was permitted to contact the camera.

After adding the one-file probe, this focused command passes **58 cases**:

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_a6400a_senser_acquire tests.test_a6400a_native_readonly tests.test_msc_extcmd_trace tests.test_camera_debugger_lock tests.test_research_status_sync -q
```

Controls cover the exact 4,096-byte success / 4,097-byte rejection boundary,
service refusal, invalid version data, sole-file access and failed exit
observation. The aligned-transfer fixture includes Senser's additional
512-byte transfer; an initially missing fixture reply was corrected without
changing the transport. Pylance/syntax checks report no tool errors.

The final guarded full suite runs **1,756 cases, 1 failure, 4 errors, 151 skips**.
The four errors are the missing E0322 overlay manifest; the failure remains the
original-target `__pipe` symbol lookup. Raw SD access is again denied before
access. New identities, exact guard recipe and complete result/log are retained
in `probe-validation-final/`; earlier results and physical receipts are not
overwritten.

### New live and alternate-route checks

The back-to-back commands use the same baseline and explicit PIPE policy:
`--action enter` followed immediately by `--action probe`. Their new, exclusive
outputs are `senser-enter-04` / `senser-file-probe-01` and `senser-enter-05` /
`senser-file-probe-02`. Each process retains its authentication results and
failure/cleanup state. A postflight source snapshot before subsequent edits is
stored in `header-probe-postflight-identities.json`; future receipts record the
source-file digest and interpreter version at startup.

The byte-identical recovered service-control source
(`c389df932d82a64cb3e91f2b7d7fbbee97d813c96ec24f1ae5bc4aabee3c5f1f`)
is replayed under `offline-service-recheck-01`. Only its host root context is
adapted to its recovered location; the source and firmware inputs are unchanged.
Original instruction controls pass, with FileControl/MemoryDump absent from
all three packaged service tables and the adjustment-terminal fallback refused.
This is consistent with the live failure, not a live installed-byte attestation.

Portable REA export inspection of the same `libsencore.so` hash retains
638 declared exports under Evidence
`ev_ee08d3da1e3be1ec391305eec970dd783c8a8cca4e0d4540510736b76c3f316e`.
No deep engine or camera-side instrumentation is started.

The stock updater-shell alternative is checked without camera I/O:
121 headers cover six families, none `CXD90045`; neither `ILCE-6400A` nor the
suffix-stripped `ILCE-6400` has a candidate. The pinned selector can strip
trailing letters, so an explicit target check is necessary. No updater
initialization, payload upload, guard override or firmware flashing is attempted.
[Upstream documentation](https://github.com/ma1co/Sony-PMCA-RE#firmware-updater)
also excludes its custom-updater route on CXD90045/CXD90057 because their
firmware is cryptographically signed. This does not prove every possible
future execution route impossible.

## Prepared guards and next boundary

### Final recovery and validation

The owner restores Microsoft USB Mass Storage for only `054C:0F0F`.
`final-usbstor-baseline/receipt.json` verifies USBSTOR / `usbstor.inf`,
Microsoft signed version `10.0.26100.9444`, OK/error 0, and the original port.
The owner reports a working LCD/menu and normal Mass Storage display.

The specifically approved elevated response check runs:

```powershell
.\venv\Scripts\python.exe -m tools.research.a6400a_native_readonly --inquiry --model-info --output-dir .\artifacts\analysis\e0512-authorized-nflasha-acquisition\final-native-recovery-check
```

Result: **exit 0 / passed**, Sony DSC inquiry and ILCE-6400A /
`0032858730` model response. The model transaction takes **0.002586 s**.
This is a model response, not firmware or partition data. The owned elevated
process exits normally; its launch/exit receipt is retained. No camera helper
was uploaded, so no camera helper removal is claimed.

Final related validation passes **126 cases** across acquisition, native
observer, original transport controls, host locking, target profiles,
repository layout and status synchronization/dashboard tests. The final
physically guarded full suite remains **1,756 run, 1 failure, 4 errors,
151 skips**, with the same missing historical inputs and a denied raw-SD open.
Complete inputs/guard recipe/result are in `final-validation/`.
The historical E0510 passing record stays dated 2026-11-14 even though this
checkpoint is dated 2026-11-15; status validation now permits that chronology.

The complete portable REA bundle is saved as
`rea-service-export-evidence.json` before clearing its two retained records.
No deep engine was opened or left running. The final outcome receipt is
`final-outcome-after-live-file-refusal.json`: acquisition blocked, camera
recovered, **zero raw partition captures**.

`/proc/uptime`, debugger responsiveness and `/tmp_bt` contents remain
unmeasured. Normal-driver restoration and a successful model query do not
establish root access, an acknowledged Senser STOP or a usable firmware reader.

The observed `054C:0336` service driver is now prepared and physically binds
during both new entries. The earlier unbound-device handoff is not repeated.
Keep normal-driver rollback and failure handling explicit.

The new `--action probe` reads only `/proc/version`, bounded to 4,096 bytes,
requires its Linux version prefix, preserves its length/hash, and attempts
STOP plus normal re-enumeration. It never reads an inventory or partition.
Refused/invalid replies and an unobserved exit remain blocked, not success.
The hardware probe refuses the request as recorded above. Failed owned sessions
now wait for bounded normal re-enumeration instead of relying on one immediate
discovery result. A successful observation is not relabeled as root access.

For host driver preparation, Zadig's documented **Device → Load Preset Device**
or **Create New Device** can stage the observed `VID 054C / PID 0336` before it
is connected. A private preset is saved under the receipt directory's
`service-driver/ILCE-6400A-Senser.cfg`; no interface-number suffix is specified.
Select WinUSB and verify the exact ID before installation. This is a Windows
driver change, not camera entry. Do not change a receiver, hub, `0994` charger
or the recovered `0F0F` binding during this preparation.

The tested standard file request is refused; identical requests are not retried.
The next boundary is a body-qualified execution or alternative reader contract,
not more driver installation or another body's updater image.
Capture success must be based on observed nodes, lengths, complete bytes and
hashes, not a mode label or shell message.
