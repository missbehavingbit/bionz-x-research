# E0533 - Native Unicorn ARM-to-Thumb continuation reaches BusyBox and ash

Date: 2026-11-17. **Strictly offline, Unicorn only.** No QEMU process,
camera, USB, physical SD, firmware installation or driver operation.
Labels: **[C]** checked bytes, constructed regressions or captured execution;
**[I]** interpretation bounded by those observations.

## 1. Result

Two fresh original `nflasha1` kernel/initrd replays execute the dynamic linker,
enter BusyBox at `0x0000C438` (`CPSR=0x60000030`, USR/Thumb), and complete
`/bin/ash` builtin command roundtrips on `ttyAM0` [C]:

```text
BusyBox v1.13.4 (...) built-in shell (ash)
/ # echo UNICORN_ASH_OK
UNICORN_ASH_OK
/ # pwd
/
/ #
```

| Observation | `final-01` | `final-02` |
| --- | --- | --- |
| Instruction budget/accounting | 800,000,000 | 800,000,000 |
| Stop | `instruction_limit`, `cpu_v7_do_idle+0x8`, `0x6011A23C` | Same |
| Stop error | None | None |
| UART bytes | 9,806 | 9,806, identical SHA-256 |
| SVC hits / user restores | 215 / 373 | 215 / 373 |
| Manual Thumb recoveries / unsupported decode receipts | 0 / 0 | 0 / 0 |
| Unmapped accesses | 0 | 0 |
| Explicit read-stub hits | `0xF800277C: 1` | Same |

All report fields agree except the backend's TLB-fill counter (570,504 versus
550,056). Whole-report byte determinism or equivalent timing is not claimed.

These are bounded shell observations, not completed-camera or timing claims.
The [historical E0510 shell](0510-nflasha1-busybox-ash-ttyam0-shell.md)
already existed; the active E0467 checkpoint was stale. This record fixes the
remaining re-entrant continuation mechanism rather than claiming the QEMU
shell as Unicorn evidence.

## 2. Root cause and correction

The original linker entry starts with two four-byte Thumb-2 literal loads and
a two-byte `mov r0, sp`. A PC sequence `...790 -> ...794 -> ...798` alone
does not establish ARM decoding [C].

The host diagnostics could themselves interrupt that entry [C]:

```text
entry code hook -> stack/literal read -> _walk_user_va
  -> _read_cp15_ttbr0 -> nested emu_start of an ARM MRC helper
```

The constructed reduction at the historical `0x0402E790` address records
execution of helper `0x62001200` instead of progressing through the original
entry. The stopped-state preservation control also proves that all three old
TTBR/TLB helpers overwrite `LR_svc=0x60123454` with the saved user
`LR_usr=0x0402E79F`. They restore LR before switching back from SVC to USR,
so the write lands in the wrong register bank. Both secure and non-secure
controls fail before the change [C].

[The machine](../../src/a6400/execution/unicorn_machine.py) now:

- Reads TTBR0/TTBR1 using Unicorn's CP-register API, selecting the existing
  security bank from `SCR.NS`; it does not change SCR or boot CPSR.
- Populates DFAR/DFSR and IFAR/IFSR through the same native API, without
  executing guest MCR helpers or moving the guest PC.
- Uses `UC_CTL_TLB_FLUSH` with `UC_CTL_IO_WRITE` for SoftMMU invalidation.
  Translated-code invalidation remains a separate `ctl_flush_tb` operation.
- Removes the fixed-address manual linker prologue from pending user returns.
  Native Unicorn executes the original Thumb instructions.
- Removes forced-Thumb/manual-instruction recovery on invalid user
  instructions. A resident invalid instruction is recorded and stops in its
  original ARM/Thumb state. The earlier ARM VFP-enable retry is preserved.

Native API failures propagate rather than returning an invented zero or
silently leaving a partially restored CPU [C]. No new guest mapping,
application decision or firmware instruction patch was added.

The five focused additions in
[test_virtual_tlb.py](../../tests/emulator/test_virtual_tlb.py) cover banked
register/PC/CPSR preservation, maintenance inside a Thumb code hook, an actual
changed-PTE re-walk, kernel-to-user continuation without warm-up, and an ARM
`udf` that must stop without being reinterpreted as Thumb.

Upstream API implementation checked at Unicorn commit
`8028ec436f2d9376525352dd38ed9ed6b9f6be10` (tag `2.1.4`):
[CP-register access](https://github.com/unicorn-engine/unicorn/blob/8028ec436f2d9376525352dd38ed9ed6b9f6be10/qemu/target/arm/unicorn_arm.c)
and [separate TB/TLB controls](https://github.com/unicorn-engine/unicorn/blob/8028ec436f2d9376525352dd38ed9ed6b9f6be10/uc.c).
The local native regressions, not those upstream sources, establish the
reported behavior.

## 3. Identities and address spaces

| Input/tool | SHA-256 or identity |
| --- | --- |
| Captured `nflasha1` partition | `b264e1e61bde1808dbe4b14df834933040af6a48f90e89bb08338ca07515451f` |
| Preserved capture manifest | `735640d8cbe8dc24dc683519a5b262dd47a4b27efc0345b00b6a21af5c2bc015` |
| Decompressed updater kernel | `ae0d4448a803850b082a80f3b9fc8dcc733d692c9d1125bf838258b7c84ff925` |
| Updater initrd | `e6315bf0c84300b763b33dc3a9a5e9ac2b4fdb18090ecd80e136f86afa4f1845` |
| Original `ld-2.11.2.so`, 101,736 bytes | `a3e07ef429c671139038d16a41ca919e3b96c59b8e197809a7f04ad0648f8a18` |
| Changed machine source | `79a077e3c5ae2eff7d25b43c147d6b1f35440e8204e4f924b93cbad7f79bbafd` |
| Unchanged boot runner | `d4a8499eaeb468b59f6cc3a9c2bcede69c3e8e3cebdced5b0291546eee604b2a` |
| Windows Unicorn native library | `010d85dce84b8f6ffaec3d5420f1b5f7768f859eb831be574c7da23e656ee8f1` |
| Python / Unicorn / Capstone | 3.15.2 / 2.1.4 / 5.0.7 |
| Exact UART bytes, both final runs | `de26941dbbfc55062027e8cf56389084fd30e5bfcbf82e78b8ba182a67badb73` |

The binary ledger was restored separately for the exact kernel and linker,
then checked using the workspace Reverify 0.11.0 adapter. Only explicitly
selected hash-checked copies were staged. Byte claims are not CPU claims.

- Kernel virtual `0x6010DF64` minus image virtual base `0x60108000`
  gives raw offset `0x5F64`: `0e f0 b0 e1`, **VERIFIED**.
- ELF entry is `0x791`; the first load segment has file/linked base zero.
  File offset `0x790` contains `df f8 68 a0 df f8 68 40 68 46`,
  **VERIFIED**.
- An initial decimal transcription used 24356 (`0x5F24`) instead of 24420
  (`0x5F64`). The wrong-offset expectation was explicitly **REFUTED** and
  remains in the retained kernel ledger. It is not instruction evidence.

In the final firmware runs, the linker load bias is `0x04031000`, not the
historical `0x0402E000`. Native hook PCs are
`0x04031790 -> 0x04031794 -> 0x04031798 -> 0x0403179A -> 0x04034AA8`.
The original bytes and the constructed regression independently cover the
historical failure coordinate; the implementation no longer depends on that
fixed loader address.

## 4. Execution policy and reproduction

Existing bring-up aids are retained: the reconstructed Sony `kemco` handoff,
labelled in-RAM VFP/fixup patches, Cortex-A9 model, timer/GIC and SVC bridges,
deferred virtual TLB, kuser support, and `observe-zero` MMIO policy. This is
not an unpatched hardware-model qualification. Guest kernel/syscalls, linker,
BusyBox and ash own their application behavior; the host supplies UART input
and captures UART output.

From the repository root, using a fresh output directory for each replay:

```powershell
$out = 'artifacts\analysis\unicorn-thumb-20261117\final-01'
New-Item -ItemType Directory -Path $out -ErrorAction Stop | Out-Null
.\.venv\Scripts\python.exe -u -m tools.research.run_nflasha1_boot `
  --limit 800000000 --no-exclusive-fallback --sony-override --svc-dispatch `
  --cmdline-extra 'init=/bin/ash console=ttyAM0,115200n8 amba2.console=1' `
  --trace-user-mode --serial-input "echo UNICORN_ASH_OK`npwd`n" `
  --trace-reg 0x0000C438:r0,r1,sp,lr,cpsr `
  --trace-reg 0x0402E798:r0,sp,cpsr `
  --output "$out\boot.json" --serial-output "$out\serial.log"
```

Repeat with `final-02`. Do not overwrite the retained directories.
The local receipt verifier was executed as:

```powershell
.\.venv\Scripts\python.exe -m artifacts.tests.verify_unicorn_thumb_20261117
```

It checks input/source identities, native entry PCs, the BusyBox entry probe,
exact ordered command responses and a returning prompt, stop/error fields,
unsupported/manual counts and repeatability. A runner exit code alone is not
accepted as success.

Receipts live under `artifacts/analysis/unicorn-thumb-20261117/`:

- `verification.json` indexes input/tool/source and per-run hashes.
- `final-01/` and `final-02/` retain reports, console logs and serial exports.
- `serial-uart.bin` in each final directory preserves the exact 9,806 UART
  bytes extracted from report JSON. The unchanged Windows runner's text-mode
  `serial.log` export inserts 148 extra CR bytes; the verifier checks that
  exact export transformation and preserves both identities.
- `byte-ledger/` preserves the verified and refuted claims.
- `regressions-before-with-helper-page.log` retains seven failing assertions;
  `focused-final.log` records **100 run, OK, 2 skipped**.

The focused and full checks use ordinary unittest selectors with prompt
garbage collection between cases, avoiding retention of multiple 2 GiB
machine fixtures. Full discovery additionally denies physical-volume
construction:

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

The first full run collected 1,997 with 115 skips and seven status/dashboard
failures, not runtime failures: concurrent evidence/diagram refresh changed
the inventory during that process, and its imported daily-history assertion
still expected 29 rather than 30 points. The failed log is preserved as
`full-suite.log`; it is not reported as passing.

After synchronizing the shared status artifacts, the same full-discovery
recipe passed: **2,000 run, OK, 94 skipped**, zero failures/errors
(`full-suite-refreshed.log`). This is the current checkout/local-input run,
not the earlier E0532 suite count. The focused run used the same `Result`
class with:

```python
suite = unittest.defaultTestLoader.loadTestsFromNames([
    'tests.emulator.test_virtual_tlb',
    'tests.emulator.test_data_abort',
    'tests.emulator.test_unicorn_machine',
    'tests.emulator.test_timer_gic_model',
])
```

`.\.venv\Scripts\python.exe -m tools.research.ci_check --summary` also passed
**5/5 guardrails**, including 86 guardrail cases (`ci-check.log`).

The required status refresh exposed a chart boundary at exactly 2,000:
the terminal count collided with the phase header. The renderer now derives
its Y-axis from the latest count with headroom and 400-case ticks, retaining
the strict collision guard. Counts 1,999, 2,000, 2,001 and 2,400 all pass
(`growth-thresholds.log`). The preserved Python 3.14 environment also passes
all 57 virtual-TLB cases (`legacy-python-virtual-tlb.log`).

The final complete-checkout discovery passed **2,009 run, OK, 96 skipped**,
zero failures/errors (`full-suite-complete.log`), using the same physical
guard/collection recipe at verbosity 0. Its appended `VALIDATION_SUMMARY`
records the result structurally. A preceding refresh's one stale README image
alt count is preserved in `full-suite-final.log`; it was corrected without
changing the test. A simultaneous CI compilation also encountered a Windows
bytecode-cache file lock (`ci-check-complete.log`), not a source compile error.
Rerunning with an isolated bytecode-cache prefix passed **5/5 guardrails**
and all 86 guardrail cases (`ci-check-isolated-cache.log`):

```powershell
$env:PYTHONPYCACHEPREFIX = Join-Path (Get-Location) 'artifacts\analysis\unicorn-thumb-20261117\ci-pycache'
.\.venv\Scripts\python.exe -m tools.research.ci_check --summary
```

The owned temporary cache was removed afterwards; no existing cache,
environment or upstream checkout was removed.

## 5. Remaining boundaries

- Baseline and native-API-only runs with `uname -a` reach ash and print the
  Linux identity, then stop with `UC_ERR_EXCEPTION` at `do_page_fault+0x108`
  (`0x60116E9C`) during subsequent child/COW activity. These failed receipts
  remain under `baseline/` and `native-api/`. The final builtin-only replays
  do not qualify that separate path or reproduce E0510's broader applet claim.
- The 800M stop is a bound, not camera startup completion. No main-firmware
  service, Appframework, GUI frame, SMP/timing equivalence or physical safety
  is established.
- No hash, firmware input, upstream checkout or other worktree was modified.
