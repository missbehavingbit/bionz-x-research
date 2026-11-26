# E0562 — The Thumb loader prefix matches and its VFP capability word differs

Date: 2026-11-17. Offline ILCE-6400 `nflasha1` updater experiment.
Inputs and Unicorn CPU options are those of
[E0560](0560-unicorn-nonsecure-af-exception-return.md). The decompressed
kernel SHA-256 is `ae0d4448a803850b082a80f3b9fc8dcc733d692c9d1125bf838258b7c84ff925`
and the initrd SHA-256 is
`e6315bf0c84300b763b33dc3a9a5e9ac2b4fdb18090ecd80e136f86afa4f1845`.

## Reproduction

The new `user-loader` stop follows the observed user Thumb entry for
at most eight instructions in the first 64 bytes. It captures 224
initial stack bytes from SP before the first instruction. Reads
translate each page and require the first guest RAM bank.

Use the E0560 Unicorn command with `--thumb-stop user-loader` and new
outputs under `artifacts/analysis/thumb-start-unicorn-02`. Use the E0560
QEMU setup with `--stop user-loader --seconds 90` and output directory
`artifacts/analysis/thumb-start-qemu-04`. The QEMU receipt identifies
the single-core Sony binary, staged GDB, capture tool, debugger stepping
policy and exact launch command. The Unicorn receipt records the runner,
Unicorn version, full arguments and stop.

```powershell
$run = 'artifacts/analysis/thumb-start-unicorn-02'
.\venv\Scripts\python.exe -m tools.research.thumb_boundary "$run/boot.json" --stop user-loader --qemu-report artifacts/analysis/thumb-start-qemu-04/qemu.json --output "$run/compare-loader"
```

### QEMU instruction fetch

At the first USR Thumb PC the stack is mapped, but the short-descriptor
walk for the instruction finds no mapped instruction page. One GDB step
lands at the high prefetch-abort vector `0xFFFF000C`, CPSR `0x197`.
A hardware breakpoint observes the guest kernel returning to the same
Thumb entry with the same registers and a valid mapping. QMP retrieves
the bytes while GDB holds the VM at that address. This accounts for why
earlier post-capture physical reads could retrieve bytes even when the
virtual read failed at initial landing. Unicorn's code hook observes the
instruction after it becomes executable.

The first QEMU approach using debugger physical-memory mode did not get
a loader translation. The next run sampled the mapped stack via QMP
and stopped on the missing instruction mapping. A 256-byte Unicorn
stack read crossed into an unmapped page. These failed attempts remain
in `thumb-start-qemu-01`, `thumb-start-qemu-02` and
`thumb-start-unicorn-01`. The bounded 224-byte sample fits the page.

## Loader prefix and stack

The four instruction samples at relative offsets `0`, `4`, `8` and
`0xA` match in bytes and translated physical addresses
`0x0042E790`, `0x0042E794`, `0x0042E798` and `0x0042E79A`.
The following call leaves the 64-byte window. QEMU's landed PC is
entry plus `0x3318`. The absolute entry PC and stack addresses differ.
The raw register comparison reports SP and r1 at the first instruction.
At the last sample r0 also carries SP, after the original `mov r0, sp`.
Both entries have `r1 - sp = 0xC8`.

Both stack captures contain `argc=1`, the `/bin/ash` argv entry,
`HOME=/`, the beginning of `TERM=linux`, and the same auxiliary-vector
tag order through `AT_NULL`. The first raw byte difference, at SP+5,
is in the relocated argv pointer. SP-relative locations match for
argv, envp, platform, random data and exec filename. The sample
includes the 16 random bytes, which differ across runs. It ends before
the end of the final strings.

The second Unicorn run, `thumb-start-unicorn-03`, reproduces the complete
windows, stop and UART bytes. Both reach BusyBox and stop at the
configured instruction limit at `0x6011A23C`. QEMU runs `03` and `04`
reproduce the relative instruction samples, physical fetch addresses,
prefetch-abort state and capability word across different virtual entries.
The prior E0560 comparison still matches all 16 return samples.
The offline suite ran 1,978 cases, with 18 skips and zero failures
(`artifacts/analysis/thumb-start-tests/full.log`).

The first non-address scalar difference in the structured stack is
`AT_HWCAP` at SP+`0x1C`. Unicorn reports `0x0008A0D7` and QEMU
`0x0000E0D7`. The XOR is `0x00084000`, bit 19 in Unicorn and bit
14 in QEMU. These are `VFPD32` and `VFPv3D16`, respectively, in the [Linux ARM HWCAP definitions](https://github.com/torvalds/linux/blob/v6.12/arch/arm/include/uapi/asm/hwcap.h#L19-L25).
The original kernel reads MVFR0 at `0x6041FFB0`, tests its low
nibble against 1, then sets `0x4000` at `0x6041FFC0` on equality
or `0x80000` at `0x6041FFC4` otherwise. The existing Unicorn boot
runner patches the later D16 assertion branch at `0x6041FFD8`.
The capability word remains guest-generated.

The next boundary is the loader callee at entry plus `0x3318` and its
use of `AT_HWCAP`. Measure the effect of the D16/D32 difference before
changing CPU capability policy.
