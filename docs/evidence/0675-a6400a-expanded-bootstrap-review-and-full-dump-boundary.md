# E0675 - a6400A expanded bootstrap review and full-dump boundary

Date: **2026-11-16 UTC**. The owner requests all flash partitions and complete
firmware, confirms installed version **1.01 unchanged**, and expands
qualification beyond E0674's no-upload/no-mode-change scope.

**The requested full dump is not complete. No new partition bytes are acquired.**
Host permissions now permit actual camera requests, but neither a general
partition reader nor a qualified temporary execution entry is established.
No driver change, service entry, upload, camera file write, storage write,
updater operation or physical-memory scan is performed.

## 1. Fresh physical observations

The existing bounded native tools establish:

- Exactly one healthy `054C:0F0F` / Microsoft USBSTOR camera, with two
  parent-associated disks, LUNs 0 and 1. No disk is selected by drive letter.
- A fresh native response identifies **ILCE-6400A / `0032858730`**.
  The transaction takes **203.780 ms**, including one response-only BUSY wait;
  the model request is not replayed.
- Exactly one debugger-channel IN returns **SCSI status 2, sense 05/20/00,
  zero bytes**. No debugger request, handshake or launch is sent.
- Final native postflight again identifies the same model through unchanged
  USBSTOR in **203.934 ms**, including one response-only BUSY wait.

The postflight is a responsiveness observation, not live module attestation,
monotonic `/proc/uptime`, `/tmp_bt` inventory or retrospective proof of E0673
Senser cleanup. No owned camera helper was staged. The SCSI inquiry revision
`1.00` is not substituted for the owner's firmware-version confirmation.

Receipts remain under
`artifacts/analysis/e0675-a6400a-full-dump-attempt/`, including
`host-preflight-01/`, `native-channel-01/`, `original-binding-01/` and
`native-postflight-01/`.

## 2. Preserved captures and exact coverage

Both earlier captures are read back and hash-verified without changing them:

| Source | Bytes | SHA-256 |
| --- | --- | --- |
| E0673 raw `/dev/nflasha5` | 62,914,560 | `d131b73f9c4610bdc3f549a65e94d70c11cc92d3071b878d65ef46a8fe13b3b6` |
| E0674 exported MSC LUN 1 | 2,097,152 | `824fb2144bca8fb3dfabffeb9e132408604bae37296b209df5e590be07db2027` |

These are **existing captures**, not newly acquired bytes. LUN 1 remains
`msc-lun1.bin`; matching geometry does not attest `/dev/nflasha23` backing.
See [E0673](0673-a6400a-first-actual-nflasha5-capture.md) and
[E0674](0674-a6400a-readonly-msc-lun-capture-and-flash-boundary.md) for the
original acquisition paths and physical limits.

The unchanged package partition configuration has SHA-256
`fc23dc2edb8e378e7985fb5da4d09a0e4c5f2445d679a730fd1d544dde831f78`.
Its **16 nonzero configured extents total 1,352,597,504 bytes**. Only one
extent, nflasha5, has an attributed raw capture. The other configured nonzero
nodes are `1/2/3/4/6/7/10/11/12/13/15/16/17/18/23`.

`coverage.json` preserves every configured row, including zero-size slots.
Configured capacity is not a new live size observation. Boot areas and a
whole-device alias are not live-inventoried or acquired on this body. No
updater image, missing extent or unknown boot region is renamed, padded or
concatenated into a supposedly complete physical backup.

## 3. Offline inputs and analysis identity

All new deep analysis uses the preserved official a6400A 1.01 package, not the
original a6400's files or runtime addresses. Input hashes are checked before
analysis and again afterward:

| Library | SHA-256 |
| --- | --- |
| `libJiritsu.so` | `c3621c7ec30771604b1aaae25e51699340333486e1aa281a1cc5f4a543a99dff` |
| `libObj.so` | `bf8dd162cdd50ffb0cf0cf9e66eacfd75aa3149e5f15176352231f6721666887` |

`offline-inputs.json` records the selected inputs. The existing
[REA batch launcher](../../tools/research/rea_wsl_batch.py) runs the configured
normal-user WSL REA **6.9.0** / Ghidra **12.1.4** environment. Thirty focused
pseudocode/instruction queries complete across the successful batches and
the separately retained large-library CLI result. These are static results,
not observed camera execution.

Ghidra coordinates below use its reported **0x10000 image bias**. For example,
Ghidra `0x197430` corresponds to linked ELF `0x187430`, not a camera runtime
address. Original Thumb bytes and selected instruction reports provide
separate support; no missing runtime service is answered with fixture success
in a physical experiment.

## 4. Why the stock Jiritsu loader is not a qualified RAM-only bootstrap

The upload and loader consumers are more specific than their command names:

- Legacy `adj_cmd_set_file`, Ghidra **0x15628**, first invokes
  `sys_mount("/dev/nflasha6", "/tmp/jiritsu")`. The upload path opens the
  selected file with **0x241**, writes, fsyncs and closes it. Only afterward
  does the literal `/tmp/jiritsu/libJit.so` case call `dlopen`.
- `adj_cmd_set_file2`, **0x15a68**, also has an internal mount/write path.
  Its separate `/mnt`-prefixed path is not a general automatic loader.
  Its memory-cache mode requires an existing script runtime, not a supplied
  root execution context.
- The identity-matched E0670 `jit_thread` decompilation is reused: initialization
  mounts nflasha6, may restore/delete recovery files, copies the library into
  a nested tmpfs, and then loads it. A later RAM copy does not make the
  preceding internal-flash operations nonpersistent.
- The concrete parent-directory helper **0x1443c** uses pathname/stat/directory
  operations, not a shell command. The MD5 helper **0x13dc0** still uses stat
  size and an O_RDONLY mapping; it is not a block-capacity reader.
- The remaining direct shell calls are also inspected. The tar helper builds
  an archive operation and calls `sync`; the clock helper **0x14608** formats
  numeric date fields. Neither is promoted to a qualified arbitrary command
  endpoint or safe acquisition bootstrap.

The earlier `sys_mount` flags without `MS_RDONLY` remain documented in
[E0670](0670-a6400a-ghidra-reader-consumers-and-fixed-partition-candidate.md).
Actual mount state, existing files, rollback and interruption recovery remain
unobserved. No library is uploaded to internal flash as a compatibility test,
and the original installer/preload guards are not changed.

Selected Evidence:

- Legacy upload/constructor: `ev_23494ffc872017211e6c6024cc06215028c406615d8b0a6f4ad390a20b7b8f95`.
- File2 upload/constructor: `ev_3069435d885c006e068a6dfe5b41e0b5911ed4a23438c3524299484894918e2b`.
- Parent-directory helper: `ev_30878b63230e9ff15a026c68b0b8dad8c76d22928d32f4ebf4310862fb56c7dc`.
- Clock shell formatting: `ev_6868b0ee7e7223a034d3f5e75731a9c1939f7c49bf258e1cee348b8782ca46da`.

## 5. MPR's read-named path writes camera-accessible storage

The original handler at Ghidra **0x197430** selects fixed `/DATA` or
`/DATA.IMG` paths. It does not accept a host-selected `/dev/nflasha*` pathname.
The worker and its actual callees establish the effect:

1. **0x195e5c** obtains media geometry and loops over raw-media chunks.
2. **0x19b368** calls **`mcmn_drv_read`** for the selected media handle.
3. **0x19c120** constructs **`%c:%s`** and calls **`Fsys_Open`**; its selected
   output mode is 6.
4. **0x19c204** calls **`Fsys_Write`** with the chunk and requested count.
5. The other worker's helper **0x19c238** uses `Fsys_FindFirst`, `Fsys_Read`,
   `Fsys_Write`, directory creation and attribute changes to copy a tree.

Consequently this is not a read-only host streaming API. Its output drive is
runtime-selected and unattested here; neither SD nor internal flash is chosen
by guessing a drive letter. No such request is sent. Its admitted source-media
set is also not a demonstrated enumeration of every internal flash partition.

Selected Evidence:

- Fixed-path handler: `ev_6bb5bdcd6793c1192efd71ce5267983f9257fcfb96a8459408716e8bae086316`.
- Raw-media worker: `ev_d78a4b8f2611985be4029e5e0fcd1469fcb8ea382f95de7da11c087bcd4325cd`.
- Media read: `ev_af2e14ae8575b6784d140fd59124cd4c3cae1e350acd0e303b638075c898db07`.
- File write: `ev_4e07cd6ca4bcf528dabc8c039014986340be161a74718917596a7943d81ebb2f`.
- Recursive copy: `ev_ce1faef7e92e5b6a62c57af35e1cb1a00c435c03b2de78b7d06c00b1b4b82643`.

## 6. Preserved failures and recovery of the analysis workflow

- The first libObj import exceeds Ghidra's **330,000 ms** startup deadline,
  with zero completed queries. Its diagnostics and unchanged input hash remain.
- A host-only retry fails during Python argument quoting before REA starts.
- A subsequent CLI query uses a **1,200,000 ms** provider budget and a private
  owned Program checkpoint. It completes after about 828 seconds and produces
  valid identity-matched Evidence. The PowerShell launcher fails to retain its
  child exit code; the null value is **not** converted to exit-0 evidence.
- Later standard batch queries restore the verified checkpoint and complete
  with exit **0**, unchanged source and normal owned-engine closure.
- Selectors **0x197600** and **0x19a8e4** are not recognized procedures in this
  Ghidra Program. Rejected fallback selectors are retained, not treated as
  evidence of absent code or reassigned coordinates. Observed procedure names
  are used for the successful worker queries.
- One context-inspection wrapper assumes a `subject` field, but that tool
  returns a composed `result`. Its complete context response is saved before
  the wrapper fails; its engine closes in `finally`. No result is fabricated.

All owned analysis engines are closed. The private Program checkpoint location
is retained in `mpr-read-consumers-03/launch.json`; query Evidence and failed
receipts are preserved under the Windows artifact directory.

## 7. Commands, validation and remaining requirement

Executed from the repository root:

```powershell
.\venv\Scripts\python.exe -m tools.research.a6400a_native_readonly --output-dir .\artifacts\analysis\e0675-a6400a-full-dump-attempt\host-preflight-01
.\venv\Scripts\python.exe -m tools.research.a6400a_debugger_presence --check-channel --output-dir .\artifacts\analysis\e0675-a6400a-full-dump-attempt\native-channel-01
.\venv\Scripts\python.exe -m tools.research.a6400a_senser_acquire --action inspect --output-dir .\artifacts\analysis\e0675-a6400a-full-dump-attempt\original-binding-01
.\venv\Scripts\python.exe -m tools.research.a6400a_reader_controls --output-dir .\artifacts\analysis\e0675-a6400a-full-dump-attempt\reader-controls-baseline-01
.\venv\Scripts\python.exe -m tools.research.a6400a_service_contracts --output-dir .\artifacts\analysis\e0675-a6400a-full-dump-attempt\service-controls-baseline-01
.\venv\Scripts\python.exe -m tools.research.a6400a_native_readonly --inquiry --model-info --output-dir .\artifacts\analysis\e0675-a6400a-full-dump-attempt\native-postflight-01
```

The metadata and final identity commands exit **0**. Channel observation exits
**2**, correctly reporting no verified debugger. Both offline original-code
replays exit **0**; the reader replay retains its **25 bounded controls**.
No ordinary test is used to contact hardware. The full suite is not rerun for
this evidence/status-only update; historical suite failures are not relabeled.

Status regeneration with `render_research_status --write` and `--check`
passes. The offline command
`.\venv\Scripts\python.exe -m unittest tests.test_research_status_sync tests.test_research_status_dashboard -q`
passes **39 cases**. Pylance reports no syntax errors in the updated status-data
module; all four relative links in this evidence record resolve, and
`git diff --check` reports no whitespace errors.

The acquisition requirement remains **blocked on a technical contract, not
host elevation or the owner's earlier scope choice**. Completing it needs an
actual general raw reader or a body-specific temporary execution entry with
verified transfer, bounded resources, cleanup and recovery. No such contract
is created by granting broader permission, changing the driver, naming a
command "read", or installing the old body's payload.
