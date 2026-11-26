# E0526 - Stella allocation-size isolation and an opt-in padding trial

Date: 2026-11-17.

## Result and scope

**The cited stella helpers do not contain a chunk-to-usable-size calculation.**
Original `libObj.so+0x79E8C2` / `+0x79E902` dispatch allocation requests through
an allocator vtable. The selected `HeapAllocator` method forwards the request
unchanged to `malloc`. Original libc already subtracts four bytes from an
ordinary in-use chunk's size when reporting its usable bytes.

The original log-block constructor fits its `0x4C` request in this isolated
replay. The explicit `0x4C -> 0x50` modified-firmware candidate increases the
allocation, but does not eliminate the chunk/usable distinction:

| Policy | Request | Normalized chunk | Ordinary usable bytes | Constructor's highest write |
| --- | --- | --- | --- | --- |
| Original | `0x4C` | `0x50` | `0x4C` | byte at offset `0x48` |
| Opt-in request padding | `0x50` | `0x58` | `0x54` | byte at offset `0x48` |

Both policies passed eleven bounded checks, twice, with identical repeated
instruction traces and observations. Each run recorded 255 instruction-hook
observations across its fragments. Neither run reproduced the historical
Appframework corruption, executed a complete allocation/free lifecycle, or
proved that its abort was fixed.

This qualifies the earlier **confirmed pool-boundary bug / not an emulator
bug** interpretation in the L3 reference. The archived abort and corrupting
stores remain historical observations; their ownership/root-cause explanation
is not proved by these helper addresses. No subtract-four pointer patch is
enabled, and the padding trial is not promoted to a verified correction.

## Input and tool identities

ILCE-6400 V2.00 package inputs, read-only:

| Input | SHA-256 |
| --- | --- |
| `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/lib/libObj.so` | `60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1` |
| `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha7_unpacked/lib/libc-2.11.2.so` | `bceb5cfb7769b54bf9c9d178b8a5f4579531c0f60e673cbcae872a8c7a3508fb` |

The new [isolation tool](../../tools/research/stella_allocator_probe.py) records
its own SHA-256, the shared decoding/input-check/mapping helper SHA-256 values,
the exact argument vector, and original instruction windows in every report.
Observed environment: Python 3.14.6, Unicorn 2.1.4, Capstone 5.0.7,
pyelftools 0.33, Windows AMD64. No dependencies were installed or upgraded.
The provisional Cortex-A9 CPU selection matches `InterpreterProbe`; it is not
a newly verified physical CPU identity.

[Related repositories](../RELATED_REPOSITORIES.md),
[REA workflow](../REA_WORKFLOW.md), and
[dependency workflow](../DEPENDENCY_WORKFLOW.md) were consulted. No upstream
allocator implementation or cross-model contract was imported. The retained
`artifacts/analysis/a6400-v200-allocator-windows-20261112/windows.json` matched
the current `libObj.so` hash; fresh instruction decoding and execution supplied
the observations here.

## Original instruction contracts

All addresses below are ELF-relative virtual addresses, not camera addresses.

1. `libObj+0x79FB3E..+0x79FB44` dispatches the singleton's vtable slot `+0x30`
   (`0x134C310 + 0x30 -> +0x64402D`), which executes `adds r0, #8; pop {r7, pc}`
   and returns the embedded `stella::HeapAllocator` at `singleton + 8` (constructed
   by `+0x63D874 -> +0x6440DC -> +0x7ACBC8` with vtable `0x1353F20`, slot zero
   `+0x7ACBBB`), storing it at `[r4, #0x40]`. `libObj+0x79E8C2` and `+0x79E902`
   then receive `{r0=request, r1=allocator}`, load vtable slot zero (`+0x7ACBBB`),
   rearrange the arguments to `{r0=allocator, r1=request}`, and call `+0x7ACBBA`.
   That method moves `r1` to `r0` and tail-branches to the `malloc` import veneer
   at `+0xFEEA4`. Both wrappers preserve `0x4C` and `0x10B0` requests in the
   replay. Execution stops at the import; **no malloc reply is supplied**.
2. The original log-block caller at `+0x79FE14` executes the request-setting
   instruction at `+0x79FE16` and calls the second wrapper. The opt-in trial
   changes only `4c20 -> 5020` (`movs r0,#0x4C -> #0x50`) in the emulator copy.
3. The libc `_int_malloc` normalization fragment `+0x592BA..+0x592D0` computes
   `max(0x10, (request + 11) & ~7)` through its original instructions, including
   the conditional Thumb instructions. The fixture supplies the read of
   `max_fast` and starts after the overflow guard. Its requests are
   `0, 1, 0x10, 0x24, 0x4C, 0x50, 0x10B0`; this is not execution of bin lookup,
   allocation, failure handling or arbitrary huge requests.
4. Original libc `malloc_usable_size` at `+0x5CA2C` is executed on constructed
   chunk headers. It returns `chunk_size - 4` for an ordinary chunk whose
   following header marks it in use, zero when that in-use test fails,
   `chunk_size - 8` for an mmap chunk, and zero for NULL. These zero values
   come from original instructions, not host fallback responses.
5. Original log-block construction at `libObj+0x7A750A`, including helpers
   `+0x7A74A0` / `+0x7A7494`, runs on a separately constructed buffer. Its
   writes end at offset `0x49` exclusive; the last is a one-byte store at
   `+0x48` (matching three `0x18`-byte sub-objects at `+0x00`, `+0x18`, `+0x30`
   plus the one-byte flag at `+0x48`, aligned to `0x4C`). Every store fits the
   requested extent under both policies. The allocation's own size word and its
   neighbour's size word stay unchanged.
6. Original `PthreadMutex::create` at `libObj+0x7AF46C` calls `+0x7B1ADC`
   (`getGlobalAllocator`, reading `.bss` `0x13F70A4`; setter `+0x7B1ACC` has no
   callers in `.text`). With `.bss` `0x13F70A4 = 0`, `+0x7AF476` (`cbz r0, #0x7af48c`)
   takes the fallback branch (`+0x7AF48C..+0x7AF498`), calling `+0x7AF3EC(0x24)`
   and stopping at the `_ZnwjRKSt9nothrow_t` (`operator new(0x24, std::nothrow)`)
   import veneer `+0xFEB40` with `r0 = 0x24` and `LR = +0x7AF493`, immediately
   preceding `+0x7AF498: bl #0x7af440` (`LR = +0x7AF49D`, the exact return address
   in the archived corruptor stack `libObj+0x7AF49D / 0x7B5D8F / 0x7B5DDD`, whereas
   the custom-allocator branch at `+0x7AF486` pushes `LR = +0x7AF48B`).

An ordinary glibc allocation can borrow the following chunk's `prev_size`
word while the previous chunk is in use. For the archived coordinates,
chunk `0x13E6B8`, user `0x13E6C0`, and size `0x50` imply an exclusive usable
end of `0x13E70C`, not `0x13E708`. A byte at user offset `0x48` is therefore
not, by itself, an overflow into the following chunk's size field.
`0x13E710` is also the next chunk's ordinary user address (`0x13E708 + 8`), and
the `+0x7AF49D` return address proves that `PthreadMutex::create` requested its
`0x24` bytes via `operator new(0x24, std::nothrow)` rather than a `stella` pool.
Its later ownership conflict needs the actual allocation/free trace; adjacent
addresses alone do not prove that stella advanced a pool by the wrong amount.

## Policies, guards and implementation

The fragment runner admits only declared original code windows, supplied data,
owned payload/stack writes, and explicit return/import/normalization stops.
Unknown reads, writes, code, unmapped access, instruction limits and faults
produce distinct stops. Each fragment is limited to 256 instructions and one
second. It supplies no OS, device, syscall, import or queue responses.

Allocator/vtable objects, the normalization input, chunk headers, load bases,
mapping rows and constructor buffers are explicit fixtures. They are not a
captured heap, successful `malloc` results or a full process. The isolated
`libObj` load bias is `0x30000000`, deliberately different from the archived
process's bias.

The existing boot experiment's `--guest-patch-logalloc-0x50` flag remains
disabled by default. It now shares the isolation tool's patch helper:

- Require the exact source SHA-256 in the loaded module's filesystem cache.
- Resolve the load bias from one guest path's mmap entries and ELF `PT_LOAD`
  segments; do not use the historical `0x5482FE16` address.
- Require file-backed executable code, an executable mapping, and matching
  original source and loaded bytes before writing.
- Write/read back only the selected halfword; record the source hash,
  guest path, bias, offset, old/new bytes and modified-firmware classification.
- Reject mismatched/repeated patches; keep original input bytes unchanged and
  `camera_fidelity_validated=false`.

The generic boot policies, scheduler, allocator implementation and
`UnicornMachine` are unchanged. No camera, USB, mode, flash, SD, firmware
installation or driver operation was performed for this experiment.

## Commands, receipts and repeatability

From the repository root in PowerShell:

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_stella_allocator_probe tests.test_session_usb_wiring tests.test_interpreter_probe.UsbControlDispatchCliTests -v

.\venv\Scripts\python.exe -m tools.research.stella_allocator_probe --report-dir artifacts\analysis\stella-allocator-20261117-stock-01
.\venv\Scripts\python.exe -m tools.research.stella_allocator_probe --guest-patch-logalloc-0x50 --report-dir artifacts\analysis\stella-allocator-20261117-padding-01
.\venv\Scripts\python.exe -m tools.research.stella_allocator_probe --report-dir artifacts\analysis\stella-allocator-20261117-stock-02
.\venv\Scripts\python.exe -m tools.research.stella_allocator_probe --guest-patch-logalloc-0x50 --report-dir artifacts\analysis\stella-allocator-20261117-padding-02
```

Results: **13/13 focused tests passed**; all four replays exited zero with
`isolation_complete`, eleven passing checks and `source_unchanged=true`.
Stock reports have no patch; each padding report has exactly one patch.
Repeat comparisons included all checks, arithmetic, allocation traces,
normalization, usable-size cases, constructor reads/writes and decoded
instruction records, not just the final status.

Focused tests use a constructed ELF under `artifacts/tests/stella-allocator/`,
not proprietary firmware. They cover nonhistorical load biases, source and
loaded preimage/identity/mapping rejection before writing, repeat-patch
rejection, unknown reads/code, writes outside a payload, unmapped memory and
instruction-limit classification.

### Failed attempts and regression limits

The preferred targeted REA/Ghidra attempt was:

```powershell
.\venv\Scripts\python.exe -u -m tools.research.rea_wsl_batch --target firmware\extracted_a6400\firmware.tar_unpacked\0700_part_image\dev\nflasha15_unpacked\lib\libObj.so --procedure 0x007acbba --procedure 0x0079fccc --procedure 0x007af3e8 --instructions 0x007acbba --report-dir artifacts\tools\rea\stella-allocator-20261117-01 --reuse-evidence
```

It **failed**, not completed analysis: Ghidra exceeded its 330,000 ms import
deadline before returning any query evidence. The failed manifest, diagnostics,
source-after hash and successful cleanup-close receipt are retained in that new
directory. The source hash remained unchanged. The small, explicitly bounded
instruction windows above were then decoded directly and tested in Unicorn;
no failed REA output was reused as successful decompilation.

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -q
```

The direct full-suite run **failed**: 1,902 tests run, 20
`UC_ERR_NOMEM` errors in kernel fixtures, 126 skipped. A diagnostic rerun using
the same discovery with `gc.collect()` after each test also **failed**:
1,910 run, 18 kernel `UC_ERR_NOMEM` errors, 126 skipped. These are not new
passing-suite counts.

A second diagnostic executed each discovered `emulator.*` case serially as
`python -m unittest tests.emulator.<module>.<class>.<method> -q` in its own
fresh process, then ran the remaining discovered inventory together:
all **169** individual emulator cases completed without a failing exit.
The remaining group still had one kernel allocation `UC_ERR_NOMEM` and two
shared-status synchronization failures while E0524/E0525 documentation changed.
Its combined receipt records 1,917 tests run, 128 skipped. A fresh standalone
rerun of the remaining kernel case passed:

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_multi_device_architecture.TestMultiDeviceArchitecture.test_unicorn_machine_from_target_uses_profile_boot_layout_and_kallsyms -q
```

The focused `test_flush_cputlb_helper_runs` case also passed in a fresh process.
The full run, GC diagnostic and process-isolated diagnostic receipts are retained
under `artifacts/analysis/stella-allocator-20261117-validation-01/`.
Other concurrent work added tests during these runs, so collected counts differ;
none replaces the historical **1,903 / 2026-11-15 passing baseline**.
No kernel allocation guard, assertion or test was weakened to obtain a pass.

## Remaining boundary and next experiment

The historical `boot-e0460-engine22.ckpt` and `boot-e0542-b3M.ckpt` were not
present locally; the preserved execution directory contained only the earlier
import smoke receipt. No new full-process continuation is claimed.

Recover or regenerate an identity-matched pre-corruption state and follow the
original allocation/free ownership of `0x13E708` / user `0x13E710`, including
the selected allocator instance, bin unlink and thread/Thumb state. Establish
the first ownership divergence before choosing a firmware correction. Keep
the padding trial opt-in, preserve the original baseline, and do not subtract
four from pointers returned by allocation dispatchers. `viewUnified2`, OSD,
a corrected Appframework abort and physical modified-firmware behavior remain
unverified.
