# E0653 - Unicorn updater child/COW continuation and the D16 capability word

Date: 2026-11-19. Offline ILCE-6400 `nflasha1` updater only. This
follows the separate boundaries in [E0533](0533-unicorn-native-thumb-userspace-continuation.md)
and [E0562](0562-thumb-loader-stack-vfp-capability.md). No camera, physical
media, USB, or firmware writes. No main-firmware application boot or GUI
is established by this path.

## Child/COW exception

The pre-fix run printed the original `uname -a` result but did not
return to the prompt. It reported `UC_ERR_EXCEPTION` at `0x60116E9C`
(`do_page_fault+0x108`). The original instructions are `bl handle_mm_fault`
at `0x60116E98` and `tst r0, #0x33` at `0x60116E9C`. At the latter PC
the kernel had returned `r0=0`, with fault VA `r4=0x00058CEC` and
saved fault status `r8=0x805` (write). The host hook used the unrelated
previous instruction-fault address, `last_ifar=0x04162978`, to request TB
removal. The reported `tst` was not the cause.

Unicorn 2.1.4's
[`ctl_remove_cache` implementation](https://github.com/unicorn-engine/unicorn/blob/8028ec436f2d9376525352dd38ed9ed6b9f6be10/qemu/accel/tcg/translate-all.c#L1117-L1137)
fetch-translates the supplied virtual address. A native reduction at
the same return PC and with an unmapped prior IFAR fails with the same
exception on the old machine. The hook now only samples the return value
and live fault VA; it does not fetch another page. The dirty-PTE
user-return path still flushes SoftMMU entries and translated code
separately, without a redundant in-hook address-range invalidation.
No guest page was mapped or guest fault result synthesized by this fix.

Both corrected runs return from the same sampled write fault and then
complete the ordered UART sequence: `uname -a` prints Linux `3.0.27_nl-rt106`,
`/bin/busybox true` returns `CHILD_EXIT:0`, the subshell prints `child`
while the parent still prints `parent`, `pwd` returns `/`, and `COW_DONE`
is followed by a fresh prompt. They each visit the original `sys_clone`
three times and `sys_execve` twice, and both reach `sys_wait4`. The samples
do not establish the physical copy or reuse of every COW page.

Both stop at the 550M runner bound in `cpu_v7_do_idle+0x8` (`0x6011A23C`),
with no stop error, unmapped access, or manual Thumb recovery. The 10,109
UART bytes match across the two runs (SHA-256
`5597133d356f5bff1236757e891d807adafb9e824b1d65cb39f91c038f23a7b1`).

## Independent userspace capability measurement

QEMU observes the native Cortex-A5 `vmrs r1, mvfr0` result in `r1`
at `0x6041FFB4`, immediately after the instruction. A separate QEMU
capture measures the original Thumb loader's initial auxv. The matched
Unicorn runs vary only the CPU capability option (besides output paths);
the CPSR/SCR controls are off in both. The loader's first four instruction
samples match QEMU by bytes and relative offsets.

| Replay | MVFR0 at the kernel probe | `AT_HWCAP` (tag 16) at loader entry |
| --- | --- | --- |
| Unicorn default, with historical VFP assertion branch patch | `0x11110222` | `0x0008A0D7` |
| Unicorn `--vfp-d16-mvfr0`, no VFP assertion branch patch | `0x10110221` | `0x0000E0D7` |
| QEMU `sony-a6400`, one Cortex-A5 | `0x10110221` | `0x0000E0D7` |

The original kernel masks MVFR0 to its low nibble, then sets bit 14
(`VFPv3D16`) for `1` or bit 19 (`VFPD32`) otherwise. All other auxv HWCAP
bits match. The opt-in hook now requires one unambiguous ARM VMRS probe
in `__boottime_vfp_init`; it runs once in the matched replay. The guest
computes `elf_hwcap` and the auxv; neither is overwritten. This reconciles
the **advertised** capability word. Unicorn still executes on Cortex-A9,
and VMRS outside the hooked boot site is unchanged. This is not a
hardware D16 model, proof of VFP context-switch equivalence, or a claim
that early libc chose a different code path. Child continuation works
with either capability word, so the D16 option is not a COW fix.

## Inputs, receipts, and replay

The partition and capture-manifest pins are those of E0533. The original
decompressed kernel and initrd are respectively
`ae0d4448a803850b082a80f3b9fc8dcc733d692c9d1125bf838258b7c84ff925`
and `e6315bf0c84300b763b33dc3a9a5e9ac2b4fdb18090ecd80e136f86afa4f1845`.
Python 3.15.2, Unicorn 2.1.4 and Capstone 5.0.7 were used on Windows.
The single-core QEMU binary was
`feb056aad4e6aaa0d668398cc64a257d85184ddca0ec4a2fe35e373db55dfbff`;
the staged GDB 17.1 was
`450401555504d3cd80e67a1a4b993e7e5b830f5d7a129153a29b01459b102039`.

Reverify 0.11.0 verified the `bl`/`tst` bytes at raw offset `0xEE98`
and 44 bytes of the MVFR0/HWCAP branch at `0x317FB0`. These offsets
subtract kernel VA base `0x60108000`, independently checked with GNU
ARM disassembly. The retained ledger preserves E0533's refuted return
offset separately. Byte verdicts do not establish CPU behavior.

Reproduce the two Unicorn runs from the repository root with fresh outputs:

```powershell
$uart = (@('uname -a', '/bin/busybox true', 'echo CHILD_EXIT:$?',
           'x=parent; (x=child; echo "$x"); echo "$x"', 'pwd', 'echo COW_DONE') -join "`n") + "`n"
$out = 'artifacts\analysis\unicorn-l2-20261119\NEW-REPLAY'
$cpu = @()  # Repeat with @('--vfp-d16-mvfr0') and a new output directory.
.\.venv\Scripts\python.exe -u -m tools.research.run_nflasha1_boot `
  --limit 550000000 --no-exclusive-fallback --sony-override --svc-dispatch `
  --cmdline-extra 'init=/bin/ash console=ttyAM0,115200n8 amba2.console=1' `
  --trace-user-mode --trace-thumb-boundary --thumb-stop user-loader `
  --trace-reg 0x0000C438:r0,r1,sp,lr,cpsr --trace-reg 0x60110E58:r0,r1,r2,r3,sp `
  --trace-reg 0x60110EC8:r0,r1,r2,sp --trace-reg 0x60116E9C:r0,r4,r5,r6,r7,r8,sp,cpsr `
  --trace-reg 0x60138238:r0,r1,r2,sp --trace-reg 0x6041FFB4:r1,cpsr `
  --trace-reg 0x6041FFD8:r3,cpsr --serial-input $uart @cpu `
  --output "$out\boot.json" --serial-output "$out\serial.log"
```

Declared Sony handoff, board fixup RAM patch, timer/GIC, SVC, and MMIO
bring-up policies remain. For QEMU use the
[`thumb_boundary` runner](../QEMU_UNICORN_BLOCKER_PLAN.md) with the same
`--cmdline-extra`, `--seconds 90`, and separate stops `user-loader` and
`pc:0x6041ffb4`. The QEMU receipts record the exact commands and clean
quit status, with no disk or network.

Local receipts are under `artifacts/analysis/unicorn-l2-20261119/`:
`baseline/`, `cow-default/`, `cow-d16/`, `qemu-loader/`, `qemu-mvfr0/`,
`kernel-byte-ledger.json`, and `verification.json` (input, tool and
report identities). `cow-regression-before.log` preserves the failing
native fixture. `focused.log` records 125 passing tests across the virtual
TLB, abort, machine, timer/GIC and Thumb-boundary selectors. `full-suite.log`
records 2,150 discovered, 2,146 run, 104 skip records and no failures or
errors, using the [offline media-denial recipe](../TOOLCHAIN.md).
