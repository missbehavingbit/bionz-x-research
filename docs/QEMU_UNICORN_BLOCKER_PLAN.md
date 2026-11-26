# QEMU-assisted diagnosis for Unicorn

## Goal

Use the same captured firmware inputs in both engines and report the earliest
observable difference at a named execution boundary. Keep the Unicorn run and
its stop intact. Use the QEMU result to decide which contract to investigate.

## First boundary

Use the ILCE-6400 updater (`nflasha1`) for the ARM exception return at
`0x6010DF34`–`0x6010DF64` and the Thumb loader entry at `0x0402E790`.
E0467 recorded an invalid-instruction failure near `0x0402E798`. E0510
subsequently recorded an interactive updater shell in Unicorn. Use the current
updater as a positive control and preserve E0467 as historical evidence.

1. Capture up to 16 instruction observations per window in Unicorn alongside
   its normal stop and error. Record the raw kernel and initrd hashes, PC,
   instruction bytes, ARM registers, CPSR, available SPSR, and the Unicorn
   page-walk result. Existing Unicorn hooks can service or redirect instructions.
2. Boot the matching captured updater inputs on the patched QEMU
   `sony-a6400` machine under WSL/Linux. Break at the selected address through a
   local GDB socket, collect pre-instruction observations, record GDB's IRQ/timer
   stepping policy and selected thread, and shut down through QMP. This QEMU
   experiment uses one core, a Sony `kemco` handoff, and no disk or network.
   Record the resulting timeout or exception if QEMU cannot reach the boundary.
3. Reject mismatched input hashes or mismatched entry bytes. Report the first
   observed difference in instruction path, bytes, or comparable CPU registers.
   For a matching prefix, show both stops and any first unpaired row. A state
   difference is an observation and may reflect differences in the boot path.
4. Repeat the experiment with the same arguments in new output directories.
   Preserve raw receipts and report whether the same boundary was reached.
   If a firmware fix is needed, reduce the difference to a focused ARM or
   platform test, change Unicorn, and replay the original firmware. Changes to
   timing, device responses, and OS behavior require independent support from
   original instructions and applicable captures.

## Commands

Run the existing Unicorn updater command with `--trace-thumb-boundary` and
specify new `--output` and `--serial-output` files under `artifacts`. The E0510
positive-control invocation is:

```powershell
./venv/Scripts/python.exe -m tools.research.run_nflasha1_boot --limit 800000000 --no-exclusive-fallback --sony-override --svc-dispatch --trace-user-mode --trace-thumb-boundary --cmdline-extra 'init=/bin/ash console=ttyAM0,115200n8 amba2.console=1' --output artifacts/analysis/thumb-unicorn-01/boot.json --serial-output artifacts/analysis/thumb-unicorn-01/serial.log
```

From the repository root in WSL/Linux, with an ARM-capable GDB that supports
Python and the locally patched QEMU build:

```bash
python3 -m tools.research.thumb_boundary artifacts/analysis/thumb-unicorn-01/boot.json \
  --qemu ~/sony-a6400-qemu/build/qemu-system-arm --gdb /usr/bin/gdb-multiarch \
  --cmdline-extra 'init=/bin/ash console=ttyAM0,115200n8 amba2.console=1' \
  --window loader --seconds 45 --output artifacts/analysis/thumb-compare-01
```

To replay the async-abort mask observed at QEMU kernel entry in E0548, add
`--mask-async-abort-at-entry` to the Unicorn command. The report records
`entry_cpsr` as `0x000001d3` with this option and `0x000000d3` by default.
This option initializes CPSR.A once at handoff. Asynchronous abort delivery
is outside the scope of this experiment.

Add `--mask-async-abort-on-irq` to replay the A bit of QEMU's AArch32 IRQ
entry separately. E0559 uses both options and compares the return window
with the archived QEMU sample.

E0560 also enables `--allow-nonsecure-af-writes` on the Unicorn Cortex-A9.
This sets SCR.AW/FW so the guest can restore the saved CPSR.A/F values.
With all three options the 16-row return window matches the fresh QEMU
reference for the ILCE-6400 updater.

Use `--thumb-stop user-loader` in Unicorn and `--stop user-loader` in
`thumb_boundary` for E0562. This records a short instruction prefix from the
observed Thumb entry, with 224 bytes of initial stack. Relative instruction
offsets and stack observations accompany the raw register difference.

Name the question with `--stop` instead of adding a window:

- `--stop pc:0x6010DF34` is the return window. `--window return` remains that stop.
- `--stop user-return` restores the return snapshot and continues to the `movs pc, lr` whose SPSR value is USR. The landed PC does not have to be known in advance.
- `--stop kernel-entry` is physical `0x00108000`. It is before the return snapshot, so the first probe cold-boots once and writes a second stage.

```bash
python3 -m tools.research.thumb_boundary \
  --qemu ~/sony-a6400-qemu/build/qemu-system-arm --gdb /usr/bin/gdb-multiarch \
  --cmdline-extra 'init=/bin/ash console=ttyAM0,115200n8 amba2.console=1' \
  --stop user-return --checkpoint artifacts/analysis/thumb-checkpoint-02 \
  --seconds 180 --output artifacts/analysis/thumb-qemu-user-return-04
```

`--checkpoint` keeps the stopped VM under `artifacts`. The return stage is the flat `checkpoint.json` and `vmstate.bin.gz` at `0x6010DF34`. The kernel-entry stage is `kernel-entry/` beside it. The migration stream is gzipped after it is written and inflated only while a probe is restoring it. A probe restores the latest stage whose PC is still before the stop, then discards the live VM. The snapshot is the VM as GDB left it, in the debug runstate. The Sony QEMU patch allows `inmigrate` to return to that state. A snapshot from another QEMU binary is rejected.

When GDB cannot read the instruction, the probe reads `$TTBR0_EL1`, walks the short-descriptor tables, and reads the physical address with QMP. A CPSR difference names the architectural bits (`A` is bit 8, `F` is bit 6). On the Unicorn runner, repeat `--thumb-stop user-return` or `--thumb-stop kernel-entry` with `--trace-thumb-boundary` so one boot records those stops too.

A `pc:` stop inside the decompressed nflasha1 image, 3,581,952 bytes from `0x00108000`, is SVC with the MMU off. The kernel-entry snapshot can reach it. A lower address above that image stays a user virtual stop. E0548 used that to show the GIC-stub registers dying before the shared setup at `0x0035DA20`. At `0x0035DAC8` the remaining registers are `r9` and CPSR.A.

`--window loader` remains the historical loader window. Omit the Unicorn report to capture QEMU independently. Use `--qemu-report path/to/qemu.json` in place of the binary arguments to compare an archived capture. If the loader maps at another address, use `--thumb-loader-entry ADDRESS` on the Unicorn runner. The comparison command reuses that address from the Unicorn report. For an independent QEMU capture, specify it with `--loader-entry ADDRESS`. Exit code 2 indicates that comparison was unavailable or capture produced no observations. A comparison exit code of 0 means the report was produced. Inspect its status for the difference or matching prefix.

## Later boundaries

Extend the same workflow to one MMIO transaction or IRQ at a time. Align by
device address, guest instruction and operation, then compare value, side
effects, and interrupt order. Keep proposed Unicorn contracts opt-in and retain
unsupported-operation stops until the contract has evidence. All work is offline
and uses hash-checked captures.
