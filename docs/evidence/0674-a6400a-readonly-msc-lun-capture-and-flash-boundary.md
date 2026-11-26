# E0674 - a6400A read-only MSC LUN capture and remaining flash boundary

Date: **2026-11-15 UTC**. The owner requested the remaining `nflasha*`
partitions and firmware, excluding the already acquired `nflasha5`.
The owner selected **existing read-only routes only, with no uploads or mode
changes**, then approved the narrowly scoped Administrator read.

**Result: one genuine 2 MiB exported Mass Storage LUN image is saved.**
Its geometry agrees with the configured `nflasha23` PMBP partition, but
**the live backing node is not attested**. This is deliberately named
`msc-lun1.bin`, not `nflasha23.bin`. **The remaining raw partitions and full
firmware backup were not obtained.** No `nflasha5` request was sent.

## 1. Actual captured bytes

| Field | Observation |
| --- | --- |
| Native camera identity | `ILCE-6400A`, product-code bytes `0032858730` |
| Version provenance | Owner-confirmed 1.01 in E0673; no new firmware-version query |
| Current transport | Healthy `054C:0F0F`, Microsoft `USBSTOR`, no rebinding |
| Source | Camera-parent-verified MSC LUN 1; actual SCSI address `0/0/1` |
| Capture | `artifacts/analysis/e0674-a6400a-remaining-partitions/msc-lun1-live-01/msc-lun1.bin` |
| Bytes | **2,097,152 / 2 MiB** |
| SHA-256 | `824fb2144bca8fb3dfabffeb9e132408604bae37296b209df5e590be07db2027` |
| Capacity | READ CAPACITY: last LBA **4095**, block size **512** |
| Data requests | **64** READ(10) requests, **32 KiB** each, all successful |
| Transfer duration | **0.134333 seconds**, within the 30-second cooperative budget |
| Capture process | `22:12:33.383164Z` to `22:12:36.204803Z`, exit **0** |
| Integrity | Streaming SHA-256, closed-file readback and independent PowerShell hash agree |
| Attribution | Candidate `/dev/nflasha23`; **not live-node attestation** |

The image contains 23,178 zero bytes, 1,635,833 `FF` bytes and 438,141 other
bytes. These are actual captured bytes, not padding or an all-erased fixture.
Local structure shows MBR and sector-1 signatures `55AA`, partition type 1,
start sector 1, length 4095 sectors and a 512-byte BPB sector size.
The volume identifier and private file contents are not copied into the report.
These checks do not prove an atomic snapshot, firmware authenticity or which
camera device node produced the exported sectors.

The image, source snapshots and complete receipts remain private under
`artifacts`. Never commit or publish the captured contents.

## 2. Bounded normal-mode reader

[a6400a_msc_lun_readonly.py](../../tools/research/a6400a_msc_lun_readonly.py)
is a separate opt-in client. Its default invocation records host metadata
without opening a camera handle. The old inquiry/model-info and Senser
allowlists remain unchanged.

The live path:

1. Associate both disks with the actual healthy Sony USB parent; never select
   by a drive letter or guessed physical disk number.
2. Verify the native model and product code on LUN 0, **without reading its
   storage sectors**. Native identity completes in **2.518 ms**.
3. Recheck the association before opening LUN 1.
4. Verify the opened storage object's device number and obtain its SCSI
   address through `IOCTL_SCSI_GET_ADDRESS`; reject any LUN other than 1.
5. Permit only literal READ CAPACITY and fixed READ(10) commands covering the
   2 MiB profile. Reject OUT transfers, write commands, changed flags,
   unaligned/out-of-range LBAs, unknown capacity and unexpected lengths.
6. Retain a partial file on failure; never retry a data command, pad missing
   bytes, overwrite an existing capture, reset USB or switch modes.
7. Flush/fsync and independently verify the closed file before finalizing it.
8. Close owned handles and require unchanged healthy USBSTOR metadata.

All 65 storage commands are IN transfers with SCSI status 0 and exact
acknowledged lengths. SPTD has a five-second command timeout; the capture
checks its 30-second budget between commands and after streaming. This is
not hard asynchronous cancellation, a timing model or a DMA teardown test.

Windows requires a read/write-access host handle for this SPTD interface.
That handle permission is not a storage write: the new transport refuses
every OUT request. The separate identity getter sends its already-reviewed
request packet, not sector data. No camera file write, helper, installer,
preload, service START/STOP, driver replacement or raw SD read is performed.

## 3. REA/Ghidra and original-instruction support

Connected WSL REA **6.9.0** / Ghidra **12.1.4** examines original 1.01 package
inputs. This is static support, not attestation of the currently loaded
camera modules.

| Input | SHA-256 |
| --- | --- |
| `libInfraMscServer.so` | `98d43efb9056a0175597671c192f7db78c426c5a486a86162c0ede92e7ce0591` |
| `libInfraFsys.so` | `31cedd4f69f501f5c5381028579586ccefabd4cd3901aa7bda3205df9e59ebcd` |
| `usbg_storage.ko` | `3c79156a3f3e9fc1a1bff733d33647a1121a5688aed87271647a14d8b9c346b2` |
| `partinf.conf` | `fc23dc2edb8e378e7985fb5da4d09a0e4c5f2445d679a730fd1d544dde831f78` |
| `mount.conf` | `0ef2d463b13cb23995f83080e6189da9e1287566d0645f5b41d5c8da14fd8acd` |

Key observations:

- `MassStorageServer::ConfigureFuncCmd`, `DriveListMaking` and
  `GetLunFromMediaIdlist` use configured media IDs and runtime media records,
  not a host-selectable arbitrary flash pathname:
  `ev_8dd624b75bdac4c73d916bc243ce6c0ab2a6ee62a50931a1d4a91d076df255c6`.
- Original `usbg_storage::read_std`, Ghidra **`0x179A4`**, decodes READ(10)
  opcode `28`, its big-endian LBA/count, direction and media-capacity bounds.
  The instructions select pre-read only at **64 KiB or above**; this client
  uses 32 KiB requests:
  `ev_5be756d03b2dba4a781d0ae3d9a2404f11956f37116487d2ef6b89b2c437a664`,
  `ev_b4defdb5b08c51dc48de6736557f2af18c766e6a52b5857727cb09362c5a2c32`.
- Original `storage_read` delegates to `bdio_read`. The separately named
  `extCmd_readMedia` delegates an ioctl to an existing backend; its name
  does not provide a new arbitrary-device reader:
  `ev_0b8a13cd259105a257a753627b916cc54e3ee5216b817a221c230cc51614d150`,
  `ev_6ed0ea5a91412d6be6e1e4a2a6f408a5181a09606bcca605ebaafc0fed9d057f`.
- The configured `/PMBP` extent is `/dev/nflasha23`, 2 MiB. The library's
  pathname string and the matching size are **inference inputs**, not proof
  of the live mounted drive or absence of virtualization. Ghidra's exact
  pathname-xref query returns no actionable references; that does not prove
  the string is unused. No live Fsys record, `/proc` or `/sys` is obtained.
- Matching E0670/E0673 Senser evidence is reused: the proven fixed raw reader
  selects only `nflasha5`; stock FF01/FF03 and stat-sized Jiritsu reads are
  not promoted to general raw-flash access.

`original-msc-instructions.json` separately preserves identity-checked ARM
bytes, decoded instructions and relocations for six selected functions.
Offsets there are section-relative, not runtime addresses. No original
kernel is executed and no unknown syscall/device response is supplied.

The complete **27-record** REA bundle is saved as
`artifacts/analysis/e0674-a6400a-remaining-partitions/rea-stock-read-routes.json`.
The owned Ghidra engine is closed and retained investigation state is cleared.

## 4. Failures, postflight and scope limits

The first non-elevated native-channel attempt fails with **WinError 5 before
any camera I/O**. That failed receipt is retained. The owner-approved UAC
process then performs the bounded LUN capture, with capture-source hashes
matched to the validated versions and copied to separate source snapshots.

After capture, a fresh native postflight response again identifies
`ILCE-6400A` / `0032858730` in **204.424 ms**, including one response-only
BUSY wait. The request is not replayed. One feature-`23` IN returns
**05/20/00, zero bytes**; no usable existing debugger channel is observed.
That observer correctly exits **2**, not root-debugger success.

The camera remains in healthy USBSTOR Mass Storage. No owned helper was
staged on the camera or remains to remove. `/proc/uptime`, `/tmp_bt`, the
live partition list and old E0673 Senser-resource release are **unverified**;
fresh USB identity does not retroactively acknowledge that older STOP.

One inline configuration-verification command initially fails on an unused,
nonexistent Python import. The corrected hash-only check succeeds without
changing any source or input; both outcomes are recorded in
`configuration-validation.json`.

## 5. Remaining acquisition boundary

The package configures nonzero `nflasha1/2/3/4/6/7/10/11/12/13/15/16/17/18`
extents in addition to `5` and `23`. None of those raw nodes is captured here.
Boot areas and any whole-device alias remain unknown on this body.
Configured zero-size slots are not declared physically absent or filled with
invented empty dumps.

- `nflasha5`: deliberately skipped; preserve the E0673 capture unchanged.
- `nflasha23`: a matching-size exported LUN image is saved, but live backing
  attribution remains an explicit separate gate.
- Other partitions and full firmware: **blocked within the selected existing
  read-only, no-upload, no-mode-change scope**.

A general reader still requires a verified existing route or a separately
reviewed and explicitly authorized nonpersistent helper. Do not weaken the
old installer, infer access from package extraction or rename smaller updater
payloads as full physical partition captures.

## 6. Reproduction and validation

The actual owner-approved elevated command, from the repository root:

```powershell
.\venv\Scripts\python.exe -m tools.research.a6400a_msc_lun_readonly `
  --read-lun1 --output-dir .\artifacts\analysis\e0674-a6400a-remaining-partitions\msc-lun1-live-01
```

Result: **exit 0**, complete LUN image and receipt, **not complete firmware**.
The existing output cannot be reused. No capture is silently replayed.
Exact elevated launch arguments, PIDs, command exits and input/source hashes
are retained in the adjacent launch receipts.

Validation uses `.\venv\Scripts\python.exe -` with preserved PowerShell
here-string harnesses. Their exact source, tool hashes and selectors are
recorded in `focused-validation-final/validation-inputs.json` and
`full-validation/validation-inputs.json`:

- Focused LUN/identity selectors: **33 passed**, zero physical access.
- Pylance syntax checks: both new Python files pass.
- Status generator `--write` / `--check`: pass. The synchronized E0674
  README/dashboard checks pass **39 cases**:
  `.\venv\Scripts\python.exe -m unittest tests.test_research_status_sync tests.test_research_status_dashboard -q`.
- Guarded full suite: **1,824 run, 1 failure, 4 errors, 151 skips**.
  The failure is the existing `__pipe` symbol expectation; all four errors
  require the missing E0322 overlay manifest. No guard or missing input is
  weakened to make the run green. A legacy raw `E:` open is denied before
  device access.

Tests cover the actual 2 MiB output, chunk ordering/hash readback, refusal of
unknown geometry/writes/unaligned reads, partial retention, non-overwrite,
default no-I/O, correct same-handle SCSI address and wrong-LUN cleanup.
