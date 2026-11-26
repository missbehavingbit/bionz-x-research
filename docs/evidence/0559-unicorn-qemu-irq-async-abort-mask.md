# E0559 — IRQ entry alignment moves the Unicorn return difference to row 13

Date: 2026-11-17. Offline ILCE-6400 `nflasha1` updater experiment.
Inputs and the 800-million-instruction bound are identical to
[E0558](0558-unicorn-qemu-kernel-entry-async-abort-mask.md). Kernel SHA-256
`ae0d4448a803850b082a80f3b9fc8dcc733d692c9d1125bf838258b7c84ff925`,
initrd SHA-256 `e6315bf0c84300b763b33dc3a9a5e9ac2b4fdb18090ecd80e136f86afa4f1845`.

## IRQ entry

Local QEMU commit `f8b2f64e2336a28bf0d50b6ef8a7d8c013e9bcf3`, in
`target/arm/helper.c` at the AArch32 `EXCP_IRQ` case, sets A and I on
ordinary IRQ entry. The Sony machine disables EL3. The current QEMU binary
(`2720703c2b200a1dbaf9ee7c7e4a99e3e6a8c026b21e7517bcd7b836354486a7`)
reached its first observed high IRQ vector at `0xFFFF0018` with
CPSR `0x800001D2` and instruction bytes `780000ea`. GDB did not supply SPSR
for this sample. This capture checks the vector's A bit; IRQ arrival and
the incoming registers are not synchronized between the engines.

Unicorn's new opt-in `--mask-async-abort-on-irq` changes the synthetic IRQ
CPSR from `0xD2` to `0x1D2`, isolating A. The existing FIQ mask and flag
behavior stay in the synthetic model. The original interrupted CPSR is
written to banked SPSR_irq, and PC and LR retain their established values.
The report records both the interrupted and injected CPSR.

## Replays

Use the E0558 Unicorn command with the additional `--mask-async-abort-on-irq`
flag and output directories `artifacts/analysis/irq-async-abort-01` and
`artifacts/analysis/irq-async-abort-02`. Compare the first report with:

```powershell
$run = 'artifacts/analysis/irq-async-abort-01'
.\venv\Scripts\python.exe -m tools.research.thumb_boundary "$run/boot.json" --window return --qemu-report artifacts/analysis/thumb-qemu-return-03/qemu.json --output "$run/compare-return"
```

The fresh QEMU capture used the staged GDB 17.1 and local libraries after
the first attempt found no system `gdb-multiarch`. See
`artifacts/analysis/irq-async-abort-qemu-vector-02/qemu.json` for the binary
and tool identities, full launch command and bounded stop. The
`ret_to_user` comparison uses the archived E0545 return receipt.

Unicorn's first IRQ injects at `0x60361578` with interrupted CPSR
`0x80000153`. The second injects at `0x6015EE8C` with the same CPSR instead
of the `0x80000053` in E0558. The 13 comparable rows from the first
`ret_to_user` visit match. At row 13, the start of the second visit,
Unicorn reports `0x40000153` and QEMU `0x40000013`, differing in A and F.
Unicorn reached the BusyBox prompt and stopped at its instruction limit at
`0x6011A23C` with 292 user restores and 9,727 serial bytes.
The two Unicorn runs produced identical trace windows, IRQ samples, stop,
and serial bytes. The fresh QEMU vector receipt matches their input hashes.
The offline suite ran 1,975 cases with 18 skips and zero failures
(`artifacts/analysis/irq-async-abort-tests/full.log`).

## Exception return boundary

The reduced fixture executes ARM `subs pc, lr, #4` (`0xE25EF004`).
Unicorn 2.1.4's A9 reports SCR `0x1` (NS set, AW clear). The saved SPSR
has the interrupted A value, but the guest return retains the live A
value if the two differ. This is consistent with the conditional A/F
write restrictions in
[Unicorn 2.1.4's CPSR implementation](https://github.com/unicorn-engine/unicorn/blob/8028ec4/qemu/target/arm/helper.c#L7526-L7576).
The initial expectation of unconditional restore failed in both
directions and was reduced to the saved-state check plus the standalone
fixture in `artifacts/analysis/irq-async-abort-tests/`.
In the paired fixture, changing only SCR.AW (`SCR=0x21`) lets the same
instruction restore the saved A value in both directions. The fixture
receipts are `irq-return.log` and `irq-return-aw.log`.

The next boundary is the A/F transition between the two visits to
`ret_to_user`. A comparison must account for the reference's disabled
EL3 and for Unicorn's SCR write restrictions. The IRQ-entry option
does not implement asynchronous abort delivery.
