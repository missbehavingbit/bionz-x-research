# E0692 — Boot score for three write-only rows in the CamCore 0xF2910000 table

Date: 2026-11-24.

This record covers an emulator change that adds three per-offset rules to the existing
CamCore register table for the `0xF2910000` block. The scores below were produced by this
project's boot scorer (oracle version 3) on the scored build, configured with
`--disable-sdl`. This record does not claim any physical camera result.

## Observation

- Before the change, the baseline boot listed 107 distinct unmodelled address pairs
  (the before-baseline score file). Three of them are write-only in that list, with no
  matching read: `write 0x2914048`, `write 0x2915044` and `write 0x2915114`.
- The three pairs had no existing rule. Each access fell through to the device-wide
  catch-all, which returns 0 for every read.
- An independent check of the baseline list upheld that each of the three pairs has
  exactly one entry and that none is a read.
- The neighbouring mode-read register at `0xF2915010` is a separate 4-byte subregion that
  takes precedence over the table. The new rows do not touch it.

## Change

The rules are in `qemu-a6400/qemu-overlay/hw/misc/cxd90045_camcore.c`, in the table
`camcore_rules_f291`. The offsets are relative to `0xF2910000`, and the table is served by
the already-mapped windows at `0xF2914000` and `0xF2915000`. The test is
`qemu-a6400/tests/test_qemu_f291_l0117.py`.

| Offset | Physical address | Rule |
| --- | --- | --- |
| `+0x4048` | `0xF2914048` | `CAM_ZERO`, read returns 0 |
| `+0x5044` | `0xF2915044` | `CAM_ZERO`, read returns 0 |
| `+0x5114` | `0xF2915114` | `CAM_ZERO`, read returns 0 |

The table stays sorted and unique, and the existing `0x5010` row is unchanged. Each new
rule returns the value the catch-all returned before (0), so the guest sees the same reads
as before. This is coverage, not new behaviour.

## Result

Each column is one score file or one run as recorded. No values are averaged.

| Measure | Before (baseline) | After (rerun) | After (promotion) |
| --- | --- | --- | --- |
| Identity check | ok | ok | ok |
| Final sequencer step | 42 of 43, value `0x897F` | same | same |
| Mismatch count | 0 | 0 | 0 |
| Distinct unmodelled address pairs | 107 | **104** | **104** |
| Unmodelled reads | 53 | 53 | 53 |
| Accesses to unmodelled space | 5,425,442 | 5,477,223 | 5,425,492 |
| `t_reach` (two runs) | 51.65 s / 52.02 s | 52.05 s / 51.65 s | 46.6 s / 51.49 s |
| Contended host | no | yes | no |
| Peak QEMU processes | 1 | 1 | 1 |
| Peak load (1 min, `busy_max`) | 2.66 | 3.39 | 1.51 |

The scorer's comparison of the rerun against the baseline reported `moved: true`, kind
`coverage`, `regressed: false`. The promotion was measured twice and reproduced the same
sequencer state and the same distinct count.

The distinct count fell by 3, which matches the three covered pairs. Unmodelled reads did
not change.

Identity: the instruction digest and the boot manifest and submicro camera page digests are
the same in the baseline and in both scores. The scorer's visual check digest (`vph`,
sha256 `1cb47093…`) is also the same. The QEMU binary digest changed, as expected for a
code change: `24aaac8a…` before and `d839ba24…` after.

The two after-score files report different access counts (5,477,223 and 5,425,492) for the
same build and inputs. The rerun is the one recorded as contended. The score files do not
say which run each access figure comes from, and this record does not resolve the
difference. The promotion figure is 50 above the baseline; the rerun figure is 51,781
above.

The final sequencer step and its value did not change, so boot progress did not change.

Build checks: the focused test file `qemu-a6400/tests/test_qemu_f291_l0117.py` ran 3 tests
with result OK. The unit test suite in this environment reports 1656 tests, with
`errors=142` and `skipped=153`. The same 142 test ids fail on main in this environment
(compared id by id), mostly because of optional dependencies. The dynamic-link check reported no missing libraries.

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

- QEMU v11.1.3 with this repository's `qemu-a6400` overlay, copied from the pristine tree
  into a fresh build directory.
- Configure: `--target-list=arm-softmmu --disable-sdl --disable-werror`. Build:
  `ninja -j4 qemu-system-arm qemu-img qemu-io`.
- Each boot was scored by the boot scorer, oracle version 3, with two runs per score.

## Limits

- Emulator only. The camera-captured inputs are not in the repository, so these counts
  cannot be reproduced without them. The record claims no physical camera result.
- The change adds coverage for three offsets in one table. It does not move the firmware
  past its sequencer step, which stays at 42 of 43.
- The rules return the value the catch-all returned (0). A self-clearing or counting status
  word at these offsets would be stored as plain memory and not modelled. No such word is
  claimed to be absent.
- The access-count difference between the two after-scores is not explained by the score
  files, and this record does not resolve it.
