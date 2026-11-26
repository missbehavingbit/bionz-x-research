# E0532 - A6400A DMM metadata, bounds and borrowed reply lifetime

Date: 2026-11-17. **Offline original instructions over explicitly constructed
memory and OSAL completions. USB remains disabled.** Continues
[E0525](0525-a6400a-jiritsu-senser-offline-packets.md).

## Result and scope

The optional `--dmm-fixtures` policy in
[`a6400a_jiritsu_packets`](../../tools/research/a6400a_jiritsu_packets.py)
now runs the original raw-reply metadata exchange and serializer against one
declared extent. It produces an eighth, **47-byte synthetic packet** through a
record-only memory sink. Original decoder/serializer controls also cover raw
lengths **0, 1 and 4096**, producing exactly **12, 13 and 4108 bytes**.

The policy is not a DMM allocator, physical mapping, OSAL implementation or
live file-transfer service. The physical-address fixture is deliberately
**unmapped** in Unicorn. The sink reads only its explicitly supplied host bytes;
the original physical sender body does not execute. No USB backend, camera,
physical volume, mount, file operation or authentication is used.

Without the flag, the original seven packets, fixture report and instruction
report remain byte-identical to E0525. Raw decoding still stops at
`before_dmm_metadata`, replay PC `0x010120D0`. The target remains `reference`.

## 1. Inputs, tools and coordinates

| Input | Bytes | SHA-256 |
| --- | ---: | --- |
| Preserved A6400A 1.01 updater | 324,175,032 | `fd6ae50755a9202a8b506b6b77aa27ab41ae717e96f05a0d4e2ad699b40d9ad1` |
| `libJiritsu.so` | 162,568 | `c3621c7ec30771604b1aaae25e51699340333486e1aa281a1cc5f4a543a99dff` |
| `libsencore.so` | 341,036 | `08804d8752c0bdfbad6e3f212cc1feaf2145e71e866a64c3f94bc4c2eca36089` |
| A6400A main-package `kmod/dmm.ko` | 84,896 | `4560112c05abeb5588950c9adac5a0bb5fc4cbb13cd44bdf8ad46a904ce14f01` |

All inputs remain read-only and are rehashed after successful replay. The updater
is hashed, not executed. The module is an additional opt-in input, not a transfer
of the original a6400 DMM contract to a new body.

Replay: Python **3.15.2**, Unicorn **2.1.4**, Capstone **5.0.9**, pyelftools
**0.33**. Static reconstruction: qualified REA **6.9.0**, Ghidra **12.2**.
Manifests retain exact expanded commands, source/tool hashes and the Cortex-A9
CPU fixture. That CPU selection is not a physical CPU-identification result.

Library addresses below are ELF linked coordinates; Thumb entries have bit 0
set. Ghidra translates these libraries by `+0x10000`; replay uses `+0x01000000`.
For the ARM ET_REL module, offsets are `.text`-relative, Ghidra's `.text` starts
at `0x1003C`, and the selected original instructions execute at
`0x01000000 + offset`. Its file `.text` offset is `0x34`. These are constructed
mappings, not camera runtime addresses.

The related-repository catalog was consulted. No cross-body address, upstream
runtime claim or physical result supplies a missing contract here.

## 2. Original metadata exchange

The original helper at `libsencore+0x120D1`:

1. Clears exactly **24 output bytes**.
2. Requests a dynamic reply queue with `osal_dynamic_id(0x2A, 0x3D, out)`.
3. Allocates a **24-byte** message on queue `0x0061003C`, flag `1`, in the
   declared local-LSI-zero fixture.
4. Writes the reply queue to `message-4` through original helper `0x11F29`.
5. Encodes operation **2** or **4**, sends, receives with timeout `0xFFFFFFFF`,
   copies **24 bytes**, checks signed status at `completion+2`, frees the
   completion, then releases the dynamic queue with operation `0x4A`.

Only these two exact exchanges are supplied. Unexpected order, arguments,
output pointers, request bytes or further OSAL calls stop explicitly. Freed
completion bytes are poisoned; the decoder still returns the copied address.
The constructed reply queue `0x003D0901` is not an observed camera queue.

### Request: `<HHIIIIHH>`, 24 bytes

| Offset | Operation 2 | Operation 4 |
| --- | --- | --- |
| `+0x00` | `2`: share an existing unit | `4`: release a share |
| `+0x02` | Unit handle | Unit handle |
| `+0x04` | Unit type from the raw reply | Zero |
| `+0x08..+0x13` | Zero in this helper | Zero in this helper |
| `+0x14` | Halfword zero | Halfword zero |
| `+0x16` | Mode `1`: open access too | Mode `2`: close access too |

The module's relocated handler tables associate operation 2 with
`dmm_ctrl_AllocUnitShareHdlr` and operation 4 with `dmm_ctrl_FreeUnitHdlr`.
Operation 4 is **not merely Close**, and is not unconditional backing-store
destruction: its handler first closes access when mode is 2, then frees a share.

### Admitted operation-2 completion: `<HhHHHhIII>`, 24 bytes

| Offset | Field | Fixture admission |
| --- | --- | --- |
| `+0x00` | Reply operation | `0x8002` |
| `+0x02` | Signed public result | Zero |
| `+0x04` | Unit handle | Exact expected `0x5678` |
| `+0x06` | Echoed token | Declared zero |
| `+0x08` | Extent count | Exactly one |
| `+0x0A` | Signed detailed result | Zero |
| `+0x0C` | Unit type | Exact expected `0x02030405` |
| `+0x10` | Extent address | Declared `0x04000020` |
| `+0x14` | Extent capacity | Exact supplied byte count, at most 4096 |

The kernel can describe multiple extents, but this library helper copies space
for only one. The phase-8 raw reply consumer returns the first copied address;
it does not establish a safe multi-extent walk or validate the requested raw
length against capacity. **The single-extent and bounds checks are host fixture
admission, not newly discovered firmware validation.**

## 3. Bounds and ownership

[`a6400a_dmm`](../../tools/research/a6400a_dmm.py) rejects malformed completion
sizes, nonzero results/tokens, stale identities, zero/multiple extents, zero
addresses/capacities, capacities above 4096 and missing fixture bytes.
Raw headers must match phase 8, response 1, transfer type 1, reserved zero,
handle, unit type and admitted length. Raw length may be zero but cannot exceed
the extent. Extent arithmetic cannot exceed ARM32, and the exclusive raw
`MemoryData` end must itself fit in 32 bits; wrapping to zero is rejected.

Every physical-sink request is checked before short-write clipping. Reads before
consumer release, beyond raw length, into capacity-only tail bytes, or after
fixture retirement are refused. The primary fixture supplies eight extra `A5`
capacity bytes; none appears in the serialized packet. Partial sends accept at
most five bytes per call. The 4096-byte control uses a 512-byte sink limit.

### Original counter controls, not an allocated kernel unit

The existing module loader is reused with closed original-PC, declared-read and
mutation ranges. There are no kernel import responses. A constructed
`0x150`-byte-stride unit row uses:

- unit type at `+0`, share count byte at `+8`, open-access count byte at `+9`;
- declared gate zero at `+0xA`, generation at `+0xB`, state halfword at `+0xC`;
- extent count halfword at `+0xE`, extent pointers from `+0x10`.

Original `MakeHandle(0x278, 0x15)` returns `0x5678`. Wrong unit type returns
`-3`; a generation-mismatched handle returns `-0x205`, with counters unchanged.
An explicitly supplied five-word extent record has base `0x04000000`, offset
`0x20`, capacity `0x23` and flags zero. Original `Open` returns
`(0x04000020, 0x23)`, extent count one.

| Original operation | Shares | Open accesses | State |
| --- | ---: | ---: | ---: |
| Declared owner | 1 | 1 | 2 |
| Consumer `Share` | 2 | 1 | 2 |
| Consumer `Open(mode=1)` | 2 | 2 | 2 |
| Consumer `Close` | 2 | 1 | 2 |
| Consumer `Free` | 1 | 1 | 2 |
| Later owner `Close` | 1 | 0 | 1 |
| Owner `Free`, stopped before extent release | 1 | 0 | 0 |

Thus releasing the consumer does not remove the declared owner's share/access.
This tests the original counter operations separately from the constructed OSAL
exchange; it does not turn that exchange into a running kernel service.

The original Jiritsu `cmd_high_capacity_get_file` phase-`0x10` continuation
reaches `DmmMgr::dmm_free`, stopping **before entry**, replay PC `0x01007F3C`.
The separate module control stops **before `dmm_ext_ctrl_FreeExtent`**, replay
PC `0x0100B228`. State zero at that boundary is not completed teardown.

The original `MemoryData::Release` does nothing to a borrowed buffer whose
release callback is null, matching `SubsystemCommunicationCommand::DoExecute`.
It does not own the DMM unit. Fixture retirement invalidates only the harness's
owned bytes and is explicitly labelled **not a kernel free**.

### Error controls

- Failed metadata completion (`status=-1`): original decoder returns null.
- Failed operation-4 completion: original decoder still returns `0x04000020`.
  The caller ignores that helper's result on this branch.
- Both controls are recorded with `serialized=false`; the explicit host gate
  requires both exchanges to complete successfully and the consumer to be
  released. A non-null pointer alone is not completion.

No firmware instruction or error branch is patched.

## 4. Reproduction and receipts

From the repository root, using fresh output directories:

```powershell
.\.venv\Scripts\python.exe -m tools.research.a6400a_jiritsu_packets `
  --dmm-fixtures --report-dir artifacts\tests\a6400a-dmm-NEW

.\.venv\Scripts\python.exe -m tools.research.a6400a_jiritsu_packets `
  --report-dir artifacts\tests\a6400a-dmm-baseline-NEW

.\.venv\Scripts\python.exe -m unittest `
  tests.test_a6400a_jiritsu_packets tests.test_msc_extcmd_trace -v
```

Final repeated receipts:
`artifacts\tests\a6400a-dmm-20261117-07` and `-08`.
All eight packet binaries and both reports compare byte-for-byte:

| Output | Bytes | SHA-256 |
| --- | ---: | --- |
| `fixtures.json` | 113,025 | `cdf9d4e6cc98e3b4764143c295938087d15cf00919f0d868a3bdc0f7bd68a9ff` |
| `original-instructions.json` | 691,790 | `f424dc3689b7340f7e402d03d3d1ec3c579ac1dee07b00bcf9097954d8a1d224` |
| `synthetic-dmm-reply.bin` | 47 | `f98409044de66437cac9b31409df06b1e36410fc63958a703dd62f394fb34299` |

The primary raw decode executes 243 original instructions; serialization
executes 440. Library calls retain the 100,000-instruction/two-second bounds.
Each module counter call is limited to 10,000 instructions and the reused
loader's 30-second timeout, with explicit return/boundary checks.

The default receipt `artifacts\tests\a6400a-dmm-baseline-20261117-02` matches
E0525's `a6400a-jiritsu-20261117-06` fixture report, instruction report and all
seven packets exactly.

Focused checks: **22 run, PASS**. Final full shared-worktree offline suite:
**2000 run, 94 skipped, zero failures/errors**, Python 3.15.2. The full-suite
receipt is `artifacts\tests\a6400a-dmm-validation-20261117-03`; replay/test source
hashes are recorded and unchanged across validation. The earlier passing
`a6400a-dmm-validation-20261117-01` receipt collected 1965 with 131 skipped.
This includes other worktree changes; the count is not attributed solely to
this change. Physical-volume construction was explicitly denied, using:

```powershell
@'
import gc
import unittest
from unittest import mock
class CollectedResult(unittest.TextTestResult):
    def stopTest(self, test):
        super().stopTest(test)
        gc.collect()
suite = unittest.defaultTestLoader.discover('tests')
with mock.patch('tools.research.sdhci_bridge.PhysicalVolumeSource',
                side_effect=PermissionError('Physical media disabled for offline validation')):
    result = unittest.TextTestRunner(verbosity=0, resultclass=CollectedResult).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
'@ | .\.venv\Scripts\python.exe -
```

`python -m tools.research.ci_check --summary`: **PASS, 5/5 guardrails**, including
86 guardrail cases. `python -m tools.research.render_research_status --check`:
**PASS** for the E0532 snapshot. The shared L2 workstream reserves E0533 and
owns the subsequent global snapshot refresh; no L2 implementation or row was
changed by this work.

### Static and byte evidence

The exact target inventory is
`artifacts\analysis\a6400a-dmm-20261117\inventory.json`.
Successful hash-matched REA batches under `artifacts\tools\rea`:

| Batch | Exact procedures; additional instruction queries |
| --- | --- |
| `a6400a-dmm-senser-20261117-01` | `0x220d0`, `0x23090`, `0x2262c`; instructions `0x220d0` |
| `a6400a-dmm-jiritsu-20261117-01` | `0x17e3c`, `0x1800c`, `0x16e8c`, `0x18244`; instructions `0x16e8c` |
| `a6400a-dmm-kernel-20261117-01` | `dmm_ctrl_AllocUnitShareHdlr`, `dmm_ctrl_OpenUnitHdlr`, `dmm_ctrl_CloseUnitHdlr`, `dmm_unit_ctrl_Open`, `dmm_unit_ctrl_Close`, `dmm_ctrl_InternalCtrlHdlr`; instructions `dmm_unit_ctrl_Open`, `dmm_unit_ctrl_Close` |
| `a6400a-dmm-kernel-20261117-02` | `dmm_unit_ctrl_Share`, `dmm_unit_ctrl_Free`, `0x156ec`; instructions `dmm_unit_ctrl_Share`, `0x156ec` |
| `a6400a-dmm-kernel-20261117-03` | `dmm_ctrl_FreeUnitHdlr`; instructions `dmm_ctrl_FreeUnitHdlr` |

Each used `python -m tools.research.rea_wsl_batch --target <exact input above>
--inventory <inventory> --procedure <each listed procedure> --instructions
<each listed instruction query> --report-dir <fresh batch> --reuse-evidence`.
All manifests report `completed`, matching Ghidra 12.2 and unchanged sources.
Original instructions, relocations and bounded execution qualify the pseudocode.

Reverify 0.11.0 inspected only a selected, hash-checked copy under the workspace
MCP inputs root. The exact-hash ledger was restored first: no prior facts or
refutations existed. Three instruction-byte sites at file offsets `0x120E2`,
`0x12154`, `0x1216A` were verified; a proposed 32-byte helper clear was refuted
by the actual `18 22` bytes at `0x120E2`. Each lies in the first PT_LOAD, where
file offset equals linked VA. This is byte evidence, not semantic proof.
The copied ledger is
`artifacts\analysis\a6400a-dmm-20261117\reverify-ledger.json`,
SHA-256 `a770245a1cee20ceaf0963c95e130ebb294b1040dd0e7322326b5f9931905813`.
The mixed verdict is retained; it is not reported as an all-verified batch.

### Preserved failures and remaining boundary

PyGhidra MCP initialization reported a lock on its existing `sony-research`
project. Ghidra MCP initialization reported loopback connection refusal.
Neither project, lock, backend nor tool installation was changed. The documented
REA provider analyzed the same exact input bytes instead.

Preliminary replay `-01` completed before strict declared-state read guards.
Replay `-02` correctly failed at an undeclared extent-flags read at descriptor
`+0x10`. The final fixture supplies that fifth word explicitly as zero;
the guards remain enabled. `-03`/`-04` passed the guarded primary fixture;
`-05`/`-06` additionally reproduce the exact raw-length boundary controls.
`-07`/`-08` retain argument-register diagnostics at every module stop and
explicit PC/arguments when the physical-sink range guard rejects a request.
Full-suite validation `a6400a-dmm-validation-20261117-02` collected 1999 cases,
with zero errors and one coupled dashboard failure: the expected history length
was still 29 after adding the thirtieth dated point. Its expectation was updated
without changing the history/source consistency checks.
Failed receipts are preserved, not reclassified.

Next: recover authentic allocation/runtime ownership and the real transport's
mapping, cache and teardown effects before generalizing beyond these fixtures.
Multi-extent replies, cross-LSI operation, actual physical sending, full owner
teardown and complete service transactions remain unsupported. No firmware
boot, camera acceptance or deployment-safety result follows from this replay.
