# E0644 — The LIRO `ram` section is av-cam's own RW data, not a sub-core image

Date: 2026-11-19. Offline only: static analysis of `av-cam.bin` (SHA-256 `90d515cf…125e`,
base `0x635C6000`) and bounded stock-firmware QEMU execution traces. No camera access.

## Question

E0642 inferred [I] that the block copied by the work DMA (LIRO `ram` section,
`0x644D3380` + `0xCEE64`, run address `0x645ACF00`) is firmware for another core. That
inference rested on its leading ARM vector table, whose literals point to `0x0100xxxx`.
This record tests it. **The E0642 sub-core inference is superseded.**

## Results

### 1. av-cam code points into the run region 1,878 times [C]

A word scan of `av-cam.bin` finds 1,880 words with values in `[0x645ACF00, 0x6467BD64)`.
Two of them are inside the `ram` image itself; the other 1,878 are elsewhere in av-cam,
mostly in literal pools next to code (`0x635CA740 → 0x6460FDD3`,
`0x635CD984 → 0x64633224`, `0x635F2F88/0x635F4400/0x635F5A0C → 0x646060E2`, …).

Disassembling the targets at their load addresses shows data, not function prologues:
`movs r0, r0` runs, `str`/`stm` patterns and table words. So the odd values are byte
offsets into structures, not Thumb entry points.

Inside the `ram` image, 95,849 words point back into the av-cam image, 2 into the run
region and 635 into `0x0100xxxx`.

**Reading:** the `ram` section is av-cam's initialised RW data. It is copied from its load
address to its run address by `entry_pre`. The suspend stub re-runs the bss clear and the
copy (E0642), which fits a warm boot restoring pristine RW state [I]. The leading vector
table and the `0x0100xxxx` words are part of that data image; their role is not
established.

### 2. Execution and access traces [C]

`handover_guard.c` (SHA-256
`b840f2f400199376d409e2282e04a7ccbd4eb0c4303ca55f249f2789b64c789e`) gains
`exec_range=START:END[,exec_limit=N]`. It logs translated blocks whose first PC is in a
virtual range, with LR, and prints the total block count and last PC at exit.

Stock boot, `-icount shift=0,sleep=on`, outer `timeout --preserve-status` (all QEMU
exit 0):

| Run | Range | Result |
| --- | --- | --- |
| `e0644-control-1` (40 s, `WORK_DMA=1`) | `0x635DC810` (hardware init) | 1 block, `lr=0x635C604C`: called from the `entry_pre` stub, confirming E0642 §2 dynamically |
| `e0644-workdma-1` (120 s, `WORK_DMA=1`) | run region `0x645ACF00–0x6467BDFF` | 0 blocks |
| `e0644-baseline-1` (120 s) | same | 0 blocks |
| `e0644-avcam-text-1` (120 s, `WORK_DMA=1`) | av-cam text `0x635C6000–0x6403A000` | 499,175 blocks; first `0x635C6044` (`entry_pre`, `lr=0x5F4EF444` in `liro.ko`); last `0x63C01E5C`, an empty `bx lr` hook on CPU0 |

Together with E0642 §4 (0 CPU loads/stores in the run region over 120 s; a positive
control works): av-cam executes, but none of its code that reads its `ram` data runs
before the known stall (E0525 power-off factor 7, E0527 initialisation wait).

## Consequence for emulation

The consumer of the copy is av-cam itself, later in boot. Without `work-dma=on`, that
data would read as zeros once those paths run, and av-cam would act on the wrong data
rather than fault. So the E0641 copy model matters for any boot that gets past the current
stall. Keeping it opt-in follows the project rule. A run that is meant to reach later
stages should set `WORK_DMA=1`.

## Superseded

E0642 §3's inference "linked for a core that sees its memory at `0x0100xxxx`" and its
next-boundary item "find what starts the `0x0100xxxx` core" are superseded by this
record. E0642's observations (fill engine, paths, traces) stand.

## Limits

- Bounded boot (≤ 120 s) only. The role of the vector-table head of the data image is
  open.
- The literal scan can include coincidental values; the conclusion rests on the
  structure-like targets, the in-image back-pointers and the copy/re-copy pattern, not on
  the count alone.

## Next boundary

None for a sub-core. The useful follow-up is getting av-cam past its current stall
(E0525/E0527) with `WORK_DMA=1`, then rerunning `trace_ram` on the run region to see the
first real consumer.
