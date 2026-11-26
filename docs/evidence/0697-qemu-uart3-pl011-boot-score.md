# E0697 — Boot score for the UART3 PL011 model at 0xF2003000

Date: 2026-11-21. Recorded from the description of merged pull request
[#63](https://github.com/ivica00/bionz-x-research/pull/63)
(merge commit `0795881`, 2026-11-21T19:27:18+01:00), PR commit `1b962f9`, author
`alexscemo`. This workspace did not re-run the boot scorer and did not access a
physical camera. The scores below are the ones the pull request reports, checked
against the boot scorer's saved score files (oracle version 3) for the accepted run
and the promotion run.

## Result

The merged machine instantiates a second stock ARM PL011 at `0xF2003000` (size
`0x1000`, GIC SPI 88, which is IRQ 120). The board passes no second `-serial`, so its
chardev is null. UART0 and the console are unchanged. The change is on by default; it
adds no environment option.

The kernel's device table names this block `uart3` with IRQ 120. The `/proc/interrupts`
listing in the pull request agrees. Before the change, the AMBA probe of `uart_devs[3]`
read the eight PrimeCell ID words at `0xF2003FE0..0xF2003FFC` through the unmodelled
catch-all, got 0, and the serial log reported `amba_device_register(uart_devs[3])
failed`. After the change those eight reads are served by the PL011 (PID `0x00141011`,
CID `0xB105F00D`), and the log reports `uart3: ttyAM1 at MMIO 0xf2003000 (irq = 120) is
a AMBA/PL011`.

| Measure | Before | After |
| --- | --- | --- |
| Final mode-sequencer state | step 42 of 43, value `0x897F` | step 42 of 43, value `0x897F` (0 mismatches) |
| Distinct unmodelled device addresses | 738 | **730** |
| of which reads | 94 | 86 |
| Accesses to unmodelled space | 7,442,362 (pull request only) | 7,626,479 |
| `t_reach` (two runs) | 42.17 s / 42.10 s (pull request only) | 42.65 s / 42.09 s |
| Host busy during measurement | no | no |

The distinct-address drop is exactly the eight ID pairs. The pull request's accept
commit describes it as "expected 738 -> 730 with the index-42 state unchanged", and the
scorer's compare step recorded the change as a coverage move with no regression. The
accepted boot had 0 mismatches and the same final state as the baseline.

The scorer's first run of this change reported 7,748,496 accesses to unmodelled space
and 92.1 s wall time for its full run. The promotion run that accepted the change
reported 7,626,479 accesses, which matches the pull request's "after" figure. The table
uses the promotion run.

Build: QEMU v11.1.3 with this repository's `qemu-a6400` overlay (qemu sha256
`5708b5f9…`). Scorer version 3.
Boot options on both sides: `EVSEL_LATE_US=500 CAMPIG_ENABLE_READBACK=1 SGC1_EVENT_A=1
GATE_FORCE_PROBE=1 LIVEVIEW_SOURCE=bars ENTRY_FORCE_PROBE=1 WORK_DMA=1`, with the
captured IDT sidecar, and `-icount shift=0,sleep=on`.

The change touches `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` (12 lines added),
adds the qtest `qemu-a6400/tests/test_qemu_uart3.py` (152 lines), and adds a section to
`qemu-a6400/STATUS.md`. The qtest reads both devices' ID registers, checks UART0's idle
flag register (`0x90`), and uses an unowned page (`0xF2004000`) as a negative control.
The pull request reports 7 of 7 checks passing.

## Verification

- `python3 -m unittest discover -s tests -q` (Linux/WSL, pull request branch): 1537
  tests run, `FAILED (errors=136, skipped=123)`. The pull request reports that the 136
  failing ids also fail on upstream `main` in that environment, mostly because of
  optional dependencies.
- `python3 -m tools.research.render_research_status --check`: passed.
- Firmware-gated tests skip without local captured inputs. Skipped checks are not
  claimed as validated.

## Limitations

- Emulator only. The captured inputs needed to reproduce the counts are not in the
  repository. This record does not repeat the measurement and claims no physical
  camera result.
- The firmware still stops at the same sequencer step (42 of 43). Coverage changed;
  boot progress did not.
- The "before" access count and wall times come from the pull request description only;
  no saved baseline score file was checked for them.
- The model is not opt-in. It is the stock PL011 with its stock ID registers, so no
  wrapper or constant return is involved.
