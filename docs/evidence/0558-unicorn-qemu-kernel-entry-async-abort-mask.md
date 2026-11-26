# E0558 — Unicorn can replay the QEMU kernel-entry async-abort mask

Date: 2026-11-17. Offline ILCE-6400 `nflasha1` updater experiment.

The opt-in `--mask-async-abort-at-entry` initializes Unicorn's SVC CPSR to
`0x000001D3`. The default entry value is `0x000000D3`. E0548 identifies the
first original instruction at physical `0x00108000` as `msr CPSR_c, #0xD3`
(`d3f021e3`). Its byte mask leaves CPSR.A, bit 8, intact. The focused ARM
test executes this instruction with both option values and checks the result.
The option models entry state and has no asynchronous-abort delivery model.

## Inputs and replay

- Decompressed kernel SHA-256: `ae0d4448a803850b082a80f3b9fc8dcc733d692c9d1125bf838258b7c84ff925`.
- Initrd SHA-256: `e6315bf0c84300b763b33dc3a9a5e9ac2b4fdb18090ecd80e136f86afa4f1845`.
- Python 3.14.6 and Unicorn 2.1.4. The boot report carries the runner hash
  and full argument list. The compared QEMU receipts carry their own binary,
  GDB and capture-tool hashes and commands.

```powershell
$run = 'artifacts/analysis/entry-async-abort-01'
.\venv\Scripts\python.exe -m tools.research.run_nflasha1_boot --limit 800000000 --no-exclusive-fallback --sony-override --svc-dispatch --trace-user-mode --trace-thumb-boundary --thumb-stop kernel-entry --thumb-stop user-return --mask-async-abort-at-entry --cmdline-extra 'init=/bin/ash console=ttyAM0,115200n8 amba2.console=1' --output "$run/boot.json" --serial-output "$run/serial.log"
.\venv\Scripts\python.exe -m tools.research.thumb_boundary "$run/boot.json" --stop kernel-entry --qemu-report artifacts/analysis/thumb-qemu-kernel-entry-01/qemu.json --output "$run/compare-entry"
.\venv\Scripts\python.exe -m tools.research.thumb_boundary "$run/boot.json" --window return --qemu-report artifacts/analysis/thumb-qemu-return-03/qemu.json --output "$run/compare-return"
.\venv\Scripts\python.exe -m tools.research.thumb_boundary "$run/boot.json" --stop user-return --qemu-report artifacts/analysis/thumb-qemu-user-return-04/qemu.json --output "$run/compare-user-return"
```

The comparisons reuse archived QEMU samples from the matching firmware
inputs. Unicorn runs with the established timer, MMIO, and SVC policies
recorded in its boot report. No physical device was accessed.

| Stop | Observation |
| --- | --- |
| `0x00108000` kernel entry | CPSR.A now matches. CPSR.Z/C and the QEMU boot-stub values in `r3`–`r6` are the remaining differences. |
| `0x6010DF34` first `ret_to_user` sample | CPSR is `0x40000053` in Unicorn and `0x40000153` in QEMU. A is the sole differing comparable bit here. |
| First return to Thumb user mode | Both have CPSR `0x30`, bytes `dff868a0`, and physical address `0x0042E790`. Virtual PC, `r1`, and SP differ. |

Unicorn reached the BusyBox `/ # ` prompt on `ttyAM0` with 292 user
restores and 9,727 serial bytes. It reached its configured limit at
`cpu_v7_do_idle+0x8` (`0x6011A23C`) with no stop error.
Repeating the command with output directory `entry-async-abort-02`
produced identical trace windows, stop, and serial bytes.

The offline suite ran 1,974 cases with 18 skips and zero failures after
synchronizing the evidence index and generated status cards. The earlier
receipt contained two status-sync failures from the E0556/E0557 mismatch.
Both logs are under `artifacts/analysis/entry-async-abort-tests/`.

## Next boundary

The first recorded IRQ injection interrupts `0x60361578` with CPSR
`0x80000153`. The second interrupts `0x6015EE8C` with `0x80000053`.
These are pre-injection samples from `timer_model.injection_pcs`. The
first injection's synthetic IRQ entry writes a CPSR with A clear.
Trace that IRQ and its return to locate why A stays clear afterwards.
The CPU model difference also remains.
The original MMU enable path from physical `0x00108044` branches to
`__enable_mmu` and reaches `__mmap_switched`. The first read of the carried
`r9` on that path is `str sb, [r4]` at virtual `0x6041D08C`.
