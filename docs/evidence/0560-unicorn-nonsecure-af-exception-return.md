# E0560 — Non-secure A/F writes reproduce the updater exception return

Date: 2026-11-17. Offline ILCE-6400 `nflasha1` updater experiment.
Inputs are identical to [E0559](0559-unicorn-qemu-irq-async-abort-mask.md).
The kernel's SHA-256 is
`ae0d4448a803850b082a80f3b9fc8dcc733d692c9d1125bf838258b7c84ff925`.
The initrd's SHA-256 is
`e6315bf0c84300b763b33dc3a9a5e9ac2b4fdb18090ecd80e136f86afa4f1845`.

## Original ARM path

On the first `ret_to_user` visit, `msr spsr_fsxc, r1` at `0x6010DF50`
sets SPSR to `0x93`. The E0559 Unicorn trace shows this value through
`movs pc, lr` at `0x6010DF64`, with LR `0x6010EB84` and live CPSR
`0x600001D3`. Returning to the original `kernel_thread_helper` should
restore the saved SVC CPSR, with A and F clear. The instruction at that
landing is `msr cpsr_c, r7` (`07f021e1`), and QEMU observes
`r7=0x13` with pre-instruction CPSR `0x93`.

## Opt-in policy

`--allow-nonsecure-af-writes` changes the Unicorn Cortex-A9 model's reset
SCR from `0x01` to `0x31`, setting AW and FW with NS still set.
The constructor requires the observed CPU model and initial SCR and checks
the write by readback. Default runs use the backend reset state. The
option lets the guest perform the A/F transitions through its original
instructions. The QEMU reference disables EL3, and Unicorn retains its
EL3 model under this policy.

The reduced fixture executes the original ARM exception-return form
`subs pc, lr, #4`. With interrupted CPSR and SPSR_irq `0x80000013`,
the default SCR leaves CPSR `0x80000153` after the return. With AW and
FW set, the same instruction restores `0x80000013`. This follows the
non-secure mask permissions described in
[Unicorn 2.1.4's CPSR implementation](https://github.com/unicorn-engine/unicorn/blob/8028ec4/qemu/target/arm/helper.c#L7526-L7576).

## Replay and comparison

Use the E0559 Unicorn command with the additional
`--allow-nonsecure-af-writes` flag. Output directories are
`artifacts/analysis/af-write-01` and `af-write-02`. The separate landing
replay uses `--limit 80000000 --thumb-stop pc:0x6010eb84` in
`af-write-landing-01`. Boot reports retain the full argv, Unicorn version,
runner hash and stop.

The fresh `thumb_boundary` QEMU captures are
`af-write-qemu-return-01/qemu.json` and `af-write-qemu-landing-01/qemu.json`,
under `artifacts/analysis/`. They use the single-core patched
`sony-a6400` binary SHA-256
`2720703c2b200a1dbaf9ee7c7e4a99e3e6a8c026b21e7517bcd7b836354486a7`
from the same local reference as E0559, plus staged GDB 17.1. The receipts
record the command, input and tool hashes, stepping policy and stop.

```powershell
$run = 'artifacts/analysis/af-write-01'
.\venv\Scripts\python.exe -m tools.research.thumb_boundary "$run/boot.json" --window return --qemu-report artifacts/analysis/af-write-qemu-return-01/qemu.json --output "$run/compare-return"
```

All 16 comparable rows match in instruction path, bytes and shared
registers. At the second visit, both engines now report CPSR
`0x40000013`. GDB did not supply SPSR, so that register is excluded
from the comparison. The two Unicorn boots produced identical trace
windows, IRQ samples, stop and serial bytes. Both reached the BusyBox
`/ # ` prompt and stopped at their instruction limit in
`cpu_v7_do_idle+0x8` (`0x6011A23C`), with 292 user restores and
9,727 captured UART bytes. The 80-million-instruction landing run hit
its limit at `0x6017D7FC` before the sample. That result remains in
`af-write-landing-01`. The 800-million-instruction retry
`af-write-landing-02` captures `0x6010EB84`. The paired record
`compare-landing` matches the original instruction bytes and every
shared register, including restored CPSR `0x93`.
The offline suite ran 1,976 cases with 18 skips and zero failures
(`artifacts/analysis/af-write-tests/full.log`).

## Next boundary

The fresh `af-write-qemu-user-return-01` capture and the corresponding
`af-write-01/compare-user-return` receipt agree on CPSR `0x30`, bytes
`dff868a0`, physical address `0x0042E790`, and `r1 - sp = 0xC8`.
The entry PC is `0x0400C790` in Unicorn and `0x04040790` in QEMU.
SP is `0x5E8A0F10` and `0x5EACDF10`, respectively. The QEMU virtual
byte read failed and the page-walk fallback supplied the bytes.
Compare the initial stack contents and the next loader instructions
at offsets from each engine's observed Thumb entry.
