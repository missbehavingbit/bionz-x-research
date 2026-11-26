# E0670 - a6400A Ghidra read consumers and fixed-partition candidate

Date: 2026-11-15. **Entirely offline. No camera connection requested.**
No USB enumeration, mode change, camera request, installer, preload hook,
filesystem write on the camera or physical-memory scan was performed.
**No firmware or raw partition capture was obtained.**

## 1. Decision

Continue offline before reconnecting the camera. Ghidra now corroborates the
earlier missing-handler conclusion and identifies a more concrete stock path:
the `0601/32` adjustment callback can prepare a read-only mapping of **only
`/dev/nflasha5`**. This is a fixed-node candidate, not an arbitrary `nflasha*`
reader or a body-qualified host acquisition procedure.

The important distinctions are:

- FF01/FF03 have no factory in the examined stock Senser tables. The generic
  fallback creates `NoAction(0x80)`, consistent with the earlier live result.
- `0601/FE RAM_READ` accesses only a **256-byte local scratch buffer**.
  It does not interpret its offset as an arbitrary CPU or physical address.
- Other admitted RAM_READ commands route through subsystem IPC, not that
  local scratch path. Their consumers are still separate contracts.
- Jiritsu file read is driven by **`stat.st_size`**, uses DMM and calls `fsync`.
  A successful zero-size source yields no file bytes; it is not a raw dump.
- `0601/32` has no exact table row, but **level 3 permits a block fallback**.
  An absent exact adjustment command is therefore not universally unreachable.
  This does not add a missing top-level Senser FF01/FF03 factory.
- Original fixed-node callback controls verify O_RDONLY, capacity conversion,
  mapping parameters and **explicit** release on constructed resources.
  They do not prove host streaming, cancellation or complete Senser exit.

The next connection gate is a bounded host transfer and interruption/recovery
contract for the candidate, plus validation of its live admission and identity.
Do not install the older a6400 daemon or modify startup/preload configuration
to bypass this boundary.

## 2. Inputs, tools and coordinates

Preserved official a6400A 1.01 package, published 2026-06-30. These are package
identities, not a live installed-byte attestation:

| Input | SHA-256 |
| --- | --- |
| `nflasha15_unpacked/lib/libsencore.so` | `08804d8752c0bdfbad6e3f212cc1feaf2145e71e866a64c3f94bc4c2eca36089` |
| `nflasha15_unpacked/lib/libJiritsu.so` | `c3621c7ec30771604b1aaae25e51699340333486e1aa281a1cc5f4a543a99dff` |
| `nflasha15_unpacked/lib/libInfraExtCmdSvc.so` | `6d48838df8c5f52b05d287cb487a6ecc26c3f86cdf2efa33ff877a25bd5d906a` |

The connected WSL REA **6.9.0** / Ghidra **12.1.4** backend performs actual
decompilation and instruction queries. Unlike
[E0669](0669-a6400a-offline-senser-dispatch-and-native-read-gates.md),
this checkpoint does not substitute Capstone for an unavailable Ghidra engine.
Capstone and Unicorn independently examine/execute the original bytes.

For these analyzed ELF imports, Ghidra reports image base **`0x10000`**.
Tables and instructions below use **linked ELF coordinates** unless explicitly
marked Ghidra. A Thumb entry adds bit 0; these are not camera runtime addresses.
The original-instruction harness uses its own explicit bias `0x01000000`.

The new source is
[a6400a_reader_controls.py](../../tools/research/a6400a_reader_controls.py).
It reuses the bounded window/library helpers and extracted adjustment setup
from [a6400a_service_contracts.py](../../tools/research/a6400a_service_contracts.py).
The earlier 164-case report is replayed unchanged in scope after that extraction.
Inputs are hash-checked before original-code execution.

## 3. Top-level dispatch and the local RAM_READ consumer

Ghidra `Parse` at linked `0x127A0` walks `pFunc`, calls a matching factory and
constructs `NoAction(0x80)` when there is no match or command object.
`TransactionStart` is at linked `0x13370`, Ghidra `0x23370`.
Its file-backed admission table agrees with E0669.

Resolved helper linked **`0x11F44`** returns false for `0601/1A`, `0601/FE`
and `0601/FF`, diverting those commands from subsystem IPC to local handling.
The FE path at `0x135F4` checks:

```text
offset < 256
length <= 256
offset + length <= 256
```

Its source is linked BSS **`0x5B048 + offset`**, not a caller-supplied pointer.
The response payload begins at **`0x5B158`**.
The original response preparation selects `SenserIfSend` with that result
buffer and the requested count; it stops before response-object construction.

Controls verify exact bytes and unchanged scratch source for lengths 0/1/4/256
and end offsets. `255+1` succeeds; `255+2`, offset 256 even with length 0,
length 257, and `0xFFFFFFFF` offset are rejected with status `0x81`.
Rejected cases do not become fabricated payloads.

The original `0304/FE` path instead prepares queue **`0x00570254`** at levels
2/3 and stops before `osal_valloc_msg_wait`. No subsystem memory, OSAL response
or physical address is supplied.

## 4. A real but fixed `/dev/nflasha5` candidate

### Admission is not just exact-row lookup

The stock `0601` block contains `SSBI_MK_MODE` (`31`) and other commands but no
exact `32` record. After an exact lookup miss, original `TransactionStart`
allows a matching-block fallback when adjustment level is **3** and the
requested transaction class is eligible.

Original controls for `0601/32` demonstrate:

| Level fixture | Result |
| --- | --- |
| 1 | Rejected transaction type `FF` |
| 2 | Rejected transaction type `FF` |
| 3 | Admitted type 0 using block's first entry, index 755 |

This is a stock path, not a patched table, changed guard or authentication
bypass. Its live level availability has not been measured.

### Original callback behavior

`sens_adjCmd_init` registers the `0x003D025F` callback at linked **`0x150EC`**.
Its `0601/32` branch begins at **`0x15952`**:

1. Refuse when its global descriptor is already active: status `A0`.
2. Obtain the size of the hardcoded `/dev/nflasha5`.
3. Open that same hardcoded node with **`0x1000`**:
   `O_RDONLY | O_LARGEFILE`, not a write descriptor.
4. Request `flock(..., 6)` (exclusive, nonblocking).
5. Map **PROT_READ=1 / MAP_SHARED=1**, offset 0.
6. Prepare mode 8 / status 1 / transfer type 2 with the mapping and exact
   capacity as the response.
7. An explicit callback mode `10` unmaps, unlocks and closes its owned descriptor.

`DeviceIo::size`, linked **`0x177F4`**, distinguishes regular and block files.
For a block file it performs **BLKGETSIZE `0x1260`** and shifts sectors left 9.
The original instruction control converts **122,880 fixture sectors** into
**62,914,560 bytes**, matching the configured WBI partition capacity in E0507.
This is not a live capacity observation.

The callback rounds its mapping to `(capacity / page_size + 1) * page_size`:
for the aligned fixture capacity that is **62,918,656 bytes**. The declared
response remains 62,914,560 bytes. A future host must not mistake mapping
padding for valid device data or silently trust an unbounded returned size.

### What the controls do and do not prove

Original callback and packet-status instructions run on constructed state.
`access`/capacity, descriptor, flock, mapping, division, logging and release
callees have explicitly recorded results; no real open, mmap or storage I/O
occurs. Success produces the expected mapping response; explicit mode `10`
releases it and resets descriptor/mapping state.

Additional cases:

- Busy status `A0`: no operations touch another fixture session's descriptor.
- Open failure `88`: no lock or mapping is attempted.
- Lock failure `A0`: the owned descriptor is closed.
- Mapping failure `88`: unlock and close occur, **but the mapping pointer stays
  `FFFFFFFF` and the mapping-size field remains populated**. The harness
  preserves this residue instead of normalizing it to success.

Ghidra's original transaction destructor sends a mode-10 completion only under
its expected state conditions. That observation and an explicit callback
release do not prove every interrupted/failed USB transfer reaches that state,
or that asynchronous teardown is complete. **Host transport and cancellation
remain unqualified.** The callback cannot select other `nflasha` nodes.

## 5. Jiritsu is not a stat-independent raw reader

Ghidra resolves `adj_cmd_get_file` at linked **`0x53D0`** and its stat shim at
**`0x9024`** (`__xstat` version 3). The original branch:

- Mode 1 prefixes the supplied name with `/mnt` and takes its size from stat.
- Mode 0 first calls `sys_mount("/dev/nflasha6", "/tmp/jiritsu")`.
- `sys_mount` uses vfat flags **`0xC0ED0490`**, without `MS_RDONLY`.
  The read-named command is not universally mount-free or read-only.
- It allocates/selects DMM, opens with flags 0, requests exactly `st_size`
  bytes, calls **`fsync`**, closes and tests exact transfer length.
- It places an embedded result status before any bytes; the enclosing
  function's return 0 is not a successful-file certificate.

Original orchestration controls demonstrate:

| Constructed case | Embedded result / output |
| --- | --- |
| Size 12, full read | Status 0; 4 status bytes + 12 fixture bytes |
| Size 0, full read | Status 0; **4 status bytes only**, read count 0 |
| Size 12, read returns 11 | `1000000A`; status-only response; fsync/close still occur |
| Open failure | `10000008`; no data read |
| Internal mode, mount failure | `10000004`; stops before stat/read |

Stat values are declared fixtures. No live block-device size or `/proc` result
is inferred. A `st_size=0` path cannot be promoted to a complete dump because
its embedded status is zero.

The separate `get_file2`/path-setting and script-loader decompilation is retained.
The loader uses internal mounting, tmpfs copying, `dlopen` and script/runtime
state; it is not qualified here as a RAM-only helper bootstrap. None of those
operations are invoked on the camera.

## 6. Other pFunc paths and native extcmd registry

ProductInfo, AdjustControl, TestMode and authentication-completion decompilation
corroborate E0669: terminal flags affect ProductInfo's `00F1` path, but do not
register FF01/FF03. TestMode communicates through `/var/fifo/sen_host_req` and
`sen_host_res`; the bounded supplied-library/bin marker scan did not identify
an additional consumer of those FIFO names. This is not a proof of runtime
absence or permission to create a consumer.

`libInfraExtCmdSvc.so` Ghidra reconstruction confirms a dynamic registration
protocol/list: feature/plugin creation requires IPC/registration state;
transport descriptors are inserted by ID and duplicate IDs are refused.
It is not a hidden fixed table of arbitrary file-read commands.
The original preparation controls for features **1/2/4/6/7**, and E0669's
network cleanup/write caution, remain in force. No native "Get" family is
promoted to raw-flash access by name alone.

## 7. Own-image L3 progress

The new [a6400a_userspace_prefix.py](../../tools/research/a6400a_userspace_prefix.py)
uses fixed hashes for the 1.01 ext2 root image, `im.elf`, its raw-ext2-verified
interpreter symlink and the own main kernel. It never uses the original
a6400 filesystem inventory.

| Explicit policy | Instructions | Stop |
| --- | --- | --- |
| `stop` | 1,183 | First `brk(0)`; zero syscall replies |
| `brk` | 2,240 | `uname`; one guest-only heap-query reply |
| `brk-uname` | **3,109** | `access("/etc/ld.so.preload", R_OK)`; two replies |

The loader consumes only the **compiled kernel release** from guarded uname.
Runtime-dependent uname fields remain unknown. The preload access is a stop,
not an injected absence or permission to install/activate a preload hook.
No guest filesystem service, IPC success, kuser state or device response is
added. This is still a bounded loader prefix, not a booted kernel/application.
The separate original-a6400 L2 shell result is not reassigned to this body.

## 8. Reproduction, receipts and next boundary

```powershell
.\venv\Scripts\python.exe -m tools.research.a6400a_reader_controls --output-dir .\artifacts\analysis\e0670-a6400a-reader-consumers\controls-final
.\venv\Scripts\python.exe -m tools.research.a6400a_userspace_prefix --policy brk-uname --output-dir .\artifacts\analysis\e0670-a6400a-reader-consumers\l3-prefix-final
.\venv\Scripts\python.exe -m tools.research.a6400a_service_contracts --output-dir .\artifacts\analysis\e0670-a6400a-reader-consumers\earlier-contract-regression
.\venv\Scripts\python.exe -m unittest tests.test_a6400a_service_contracts tests.test_msc_extcmd_trace tests.test_usb_debug_abi_trace -q
```

All three replays exit **0**, reaching their declared stops; the listed
focused command passes **27 tests**. A first callback window stopped before
its final four-byte Thumb pop because its exclusive bound was two bytes short.
The bound was corrected to contain that original instruction, not weakened to
permit unknown execution. Read controls check exact scratch boundaries,
zero-size/short file responses, block size, resource ownership, visible failure
residue and the level-3 adjustment fallback. No unsupported API returns success.

The final reader report contains **25 bounded original-code cases**, in
addition to replaying the earlier 164-case service report. Final combined
service/acquisition/ABI/launcher/layout/target/dashboard validation passes
**126 tests**. Pylance reports no diagnostics or syntax errors in the new
tools/tests.

The physically guarded full suite runs **1,777 tests, 1 failure, 4 errors,
151 skips**. Failure/error IDs match the immediately preceding REA setup
baseline (1,766 tests): the missing E0322 overlay manifest and original-target
`__pipe` symbol expectation are unchanged, with 11 additional passing tests.
A legacy raw-SD open is denied before access; its synthetic fallback is not
physical verification. Actual USB, camera clients, raw/non-repository drives,
native device APIs and real WSL subprocesses are denied during ordinary tests.
The exact harness, source digests and complete log are in `full-validation/`.
No missing input or guard is weakened to obtain green.

Private receipts and complete REA bundles are under
`artifacts/analysis/e0670-a6400a-reader-consumers/`. Query ambiguity between
PLT thunks and real definitions is retained; corrected queries use observed
entries, not guessed runtime addresses. Key evidence:

- Senser `TransactionStart`: `ev_49e27c0228f1e8cecf4ca562bf7c1b918844fe74f9c66d0179a21d65d7615bc3`.
- Fixed-node callback decompilation: `ev_1c7192305695dfb6970b1e4ebe0cb04e5c35384b3ad947c470b9a99565b2dac5`.
- Block-size original instructions: `ev_4adab0b67ac90925fcad8e9613107d27d194400d61a8b26ae26d946cc4045fe8`.
- Jiritsu paths/read methods: `ev_ca72abcbfbdfc68a7f5a2082d575a7ad0251d74644869a99e4b2edbe52e9bc71`.
- Native registry: `ev_d7323244166f1729941ada1a44bf3946a8288e92e81801b157fa8c570fda9572`.

All owned Ghidra engines are closed after saving their evidence. Failed
selectors and earlier receipts are preserved. No firmware identity or installer
guard was changed.

**Keep the camera disconnected.** Next, validate a strictly bounded host
reader for the fixed node, exact full/partial-transfer framing and a proven
normal/cancel/error release sequence. Only then consider a supervised live
admission/read test; even success would qualify only the measured node, not
all firmware or every `nflasha*` partition.

[E0671](0671-a6400a-fixed-reader-framing-and-completion-controls.md) follows
this boundary with exact remaining-byte framing, bounded constructed host
replays and original extraction/destructor/callback release controls. It still
does not enable a live adapter or qualify actual forced unwinding/USB exit.
