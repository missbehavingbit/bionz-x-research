# E0695 — Boot score for one write-only row in the CamCore 0xF3120000 table

Date: 2026-11-24.

This record covers an emulator change that adds one per-offset rule to the existing
CamCore register table for the `0xF3120000` block. The scores below were produced by this
project's boot scorer (oracle version 3) on the scored build, configured with
`--disable-sdl`. This record does not claim any physical camera result.

## Observation

- Before the change, the baseline boot listed 83 distinct unmodelled address pairs (the
  before-baseline score file). One of them is write-only in that list, with no matching
  read: `write 0x3123000` (physical `0xF3123000`).
- The pair had no existing rule. The access fell through to the device-wide catch-all,
  which returns 0 for every read.
- The camcore block at `0xF3120000` is 64 KiB and is registered at priority -600. The
  SGC1 CamPig window at `0xF3120000` (priority -400) is only 0x2000 bytes long, so it does
  not answer `0xF3123000`. The SIF page at `0xF3122000` ends at `0xF3122FFF`. The
  static check found no mapping above priority -600 that covers `0xF3123000`.
- The baseline has no `read 0x3123000` entry. The access is write-only in the baseline.
  Its use by the firmware was not traced, so no contract for this register is claimed.

## Change

The rule is in `qemu-a6400/qemu-overlay/hw/misc/cxd90045_camcore.c`, in the existing table
`camcore_rules_f312`. The offset is relative to `0xF3120000`, so the row is `+0x3000`
(physical `0xF3123000`). The rule is `CAM_ZERO` with a full 32-bit mask. Its read returns
0, and a write is stored as before. The table keeps its sort order, and no other row is
changed. The test is `qemu-a6400/tests/test_qemu_f312_l0119.py`, which checks the row
statically.

The rule returns the value the catch-all returned (0), so the guest sees the same reads as
before. This is coverage, not new behaviour.

## Result

Each column is one score file or one run as recorded. No values are averaged.

| Measure | Before (baseline) | After (rerun) | After (promotion) |
| --- | --- | --- | --- |
| Identity check | ok | ok | ok |
| Final sequencer step | 42 of 43, value `0x897F` | same | same |
| Mismatch count | 0 | 0 | 0 |
| Distinct unmodelled address pairs | 83 | **82** | **82** |
| Unmodelled reads | 44 | 44 | 44 |
| Accesses to unmodelled space | 5,394,998 | 5,378,767 | 5,352,659 |
| `t_reach` | 51.89 s (one value recorded) | 64.29 s / 51.46 s | 51.61 s / 51.68 s |
| Contended host | no | yes | no |
| Peak QEMU processes | 1 | 1 | 1 |
| Peak load (1 min, `busy_max`) | 2.32 | 7.36 | 1.74 |

The scorer's comparison of the rerun against the baseline reported `moved: true`, kind
`coverage`, `regressed: false`, with nothing worse. The scorer did not judge speed for the
rerun, because a run shared the machine with other load. The promotion was measured twice
on a quiet host and reproduced the same sequencer state and the same distinct count.

The distinct count fell by 1, which matches the one covered pair. Unmodelled reads did not
change at 44. The access count fell by about 16,000 in the rerun and by about 42,000 in the
promotion. The score files give totals only, not a breakdown by address pair, so this
record does not explain the fall.

Identity: the instruction digest, the boot manifest digest and the submicro camera page
digest are the same in the baseline and in both scores. The QEMU binary digest changed, as
expected for a code change: `be400e22…` before and `1df761e5…` after, identical in the
rerun and the promotion.

The final sequencer step and its value did not change, so boot progress did not change.

Build checks: the focused test file `qemu-a6400/tests/test_qemu_f312_l0119.py` ran 3 tests
with result OK. The unit test suite in this environment reports 1656 tests, with
`errors=142` and `skipped=153`. The same 142 test ids fail on main in this environment
(compared id by id, 0 new), mostly because of optional dependencies.

## Inputs

- Camera-captured boot inputs: the boot manifest and the submicro camera page. Neither is
  in the repository.
- The camera-captured `IDT_CAM` preload (`idt_cam.bin`). Not in the repository. Its
  contents are not described here.
- Boot options: `EVSEL_LATE_US=500`, `CAMPIG_ENABLE_READBACK=1`, `SGC1_EVENT_A=1`,
  `GATE_FORCE_PROBE=1`, `LIVEVIEW_SOURCE=bars`, `ENTRY_FORCE_PROBE=1`, `WORK_DMA=1`,
  speed profile `default`, and `-icount shift=0,sleep=on`.
- `GATE_FORCE_PROBE=1` and `ENTRY_FORCE_PROBE=1` are declared probes that the scorer allows.
  They, the `IDT_CAM` preload and every other option are identical before and after, so the
  difference between the columns is this change alone.

## Build and commands

- QEMU v11.1.3 with this repository's `qemu-a6400` overlay, built in a fresh build
  directory.
- Configure with `--disable-sdl`. Build: `ninja -j4 qemu-system-arm qemu-img qemu-io`.
- Focused test: `python3 -m unittest qemu-a6400/tests/test_qemu_f312_l0119.py -q`.
- Each boot was scored by the boot scorer, oracle version 3, with two runs per score.

## Limits

- Emulator only. The camera-captured inputs are not in the repository, so these counts
  cannot be reproduced without them. The record claims no physical camera result.
- The change adds coverage for one offset. It does not move the firmware past its
  sequencer step, which stays at 42 of 43.
- The row is marked write-only in the baseline. Its read value of 0 is not sourced from a
  captured read, and the firmware's use of this register is not decoded. A guest that
  depends on a non-zero read-back at this offset would see 0 here.
- The access-count fall is not explained by the score files, and this record does not
  resolve it.
