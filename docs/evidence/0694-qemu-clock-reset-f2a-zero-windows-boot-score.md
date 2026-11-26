# E0694 — Boot score for ZERO-only windows on four clock/reset-group pages at 0xF2A00000-0xF2A32000

Date: 2026-11-24.

This record covers an emulator change that maps four 4 KiB pages of the clock and reset
group at `0xF2A00000`, `0xF2A10000`, `0xF2A20000` and `0xF2A32000`, each with per-offset
ZERO-only rules. The scores below were produced by this project's boot scorer (oracle
version 3) on the scored build, configured with `--disable-sdl`. This record does not
claim any physical camera result.

## Observation

- Before the change, the baseline boot listed 104 distinct unmodelled address pairs.
  Twenty-one of them fall in `0xF2A0xxxx` to `0xF2A3xxxx`, the range of the four new
  pages. Each of the 21 pairs fell through to the device-wide catch-all, which returns 0
  for every read.
- Every one of the 21 pairs is covered by a new rule. None is left unruled in that range.
- The existing read-back model on the sibling pages at `0xF2A01000`, `0xF2A11000`,
  `0xF2A12000`, `0xF2A21000`, `0xF2A22000` and `0xF2A30000` is separate and unchanged.
  The new pages do not overlap it.

## Change

The rules are in `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c`, in four tables
(`cxd90045_f2a0_rules` to `cxd90045_f2a3_rules`), with a comment block above the first
table. The test is `qemu-a6400/tests/test_qemu_f2a0.py`. Each window is 0x1000 bytes,
created at priority -600 and on by default. The create call is not gated by any
environment option. The read-back table of the sibling pages is not extended.

| Page | Offset | Rule |
| --- | --- | --- |
| `0xF2A00000` | `+0x008`, `+0x024`, `+0x028` | read returns 0; writes stored |
| `0xF2A00000` | `+0x0A4` | write-only in the baseline; writes stored |
| `0xF2A10000` | `+0x004`, `+0x008`, `+0x024`, `+0x028` | read returns 0; writes stored |
| `0xF2A20000` | `+0x024`, `+0x028` | read returns 0; writes stored |
| `0xF2A32000` | `+0x030`, `+0x1A0` | write-only in the baseline; writes stored |

Every rule is `ZERO`: a read returns 0, and a write is stored but never read back. The
guest therefore sees the same values as before. Offsets without a rule still log as
unmodelled on both the read and write paths, so the windows are not a blanket zero.
This is coverage, not new behaviour.

The static analysis that led to these offsets names only `0xF2A00000` and `0xF2A10000`
as the group's offset-0 pages. `0xF2A20000` and `0xF2A32000` come from the baseline
unmodelled list only. Offsets `+0x004`, `+0x008`, `+0x024` and `+0x028` are read-modify-write
in the firmware. They are ruled `ZERO` rather than read-back, because their read-back
values have not been captured. The comment block in the source says the same.

## Result

Each column is one score file or one run as recorded. No values are averaged.

| Measure | Before (baseline) | After (rerun) | After (promotion) |
| --- | --- | --- | --- |
| Identity check | ok | ok | ok |
| Final sequencer step | 42 of 43, value `0x897F` | same | same |
| Mismatch count | 0 | 0 | 0 |
| Distinct unmodelled address pairs | 104 | **83** | **83** |
| Unmodelled reads | 53 | 44 | 44 |
| Accesses to unmodelled space | 5,425,492 | 5,262,400 | 5,394,998 |
| `t_reach` (two runs) | 46.6 s / 51.49 s | 52.0 s / 55.28 s | 51.89 s / 51.41 s |
| Contended host | no | yes | no |
| Peak QEMU processes | 1 | 1 | 1 |
| Peak load (1 min, per run) | 1.51 / 1.75 | 1.99 / 6.25 | 2.32 / 1.87 |

The scorer's comparison of the rerun against the baseline reported `moved: true`, kind
`coverage`, `regressed: false`, with nothing worse. The scorer did not judge speed for the
rerun, because one of its runs shared the machine with other load. The promotion was
measured twice on a quiet host and reproduced the same sequencer state and the same
distinct count.

The distinct count fell by 21, which matches the 21 covered pairs. Unmodelled reads fell
from 53 to 44.

The access count fell by about 163,000 in the rerun and by about 30,500 in the promotion.
The score files give totals only, not a breakdown by address pair, so this record does
not explain the fall. One reading, not checked here, is that accesses to the 21 retired
pairs no longer count as unmodelled.

Identity: the instruction digest, the boot manifest and submicro camera page digests,
and the scorer's visual check digest (`vph`, sha256 `1cb47093…`) are the same in the
baseline and in both scores. The QEMU binary digest changed, as expected for a code
change: `d839ba24…` before and `be400e22…` after.

The final sequencer step and its value did not change, so boot progress did not change.

Build checks: the focused test file `qemu-a6400/tests/test_qemu_f2a0.py` ran 4 tests with
result OK. The unit test suite in this environment reports 1656 tests, with
`errors=142` and `skipped=153`. The change adds no new failing test id compared with main.
The dynamic-link check reported no missing libraries.

## Inputs

- Camera-captured boot inputs: the boot manifest and the submicro camera page. Neither is
  in the repository.
- The camera-captured `IDT_CAM` preload (`idt_cam.bin`). Not in the repository.
- Boot options: `EVSEL_LATE_US=500`, `CAMPIG_ENABLE_READBACK=1`, `SGC1_EVENT_A=1`,
  `GATE_FORCE_PROBE=1`, `LIVEVIEW_SOURCE=bars`, `ENTRY_FORCE_PROBE=1`, `WORK_DMA=1`,
  speed profile `default`, and `-icount shift=0,sleep=on`.
- `GATE_FORCE_PROBE=1` and `ENTRY_FORCE_PROBE=1` are declared probes that the scorer allows.
  They, the `IDT_CAM` preload and every other option are identical before and after, so the
  difference between the columns is this change alone.

## Build and commands

- QEMU v11.1.3 with this repository's `qemu-a6400` overlay, copied from the pristine tree
  into a fresh build directory.
- `setup-qemu.sh` applied the overlay. Configure: `--target-list=arm-softmmu --disable-sdl
  --disable-werror`. Build: `ninja -j2 qemu-system-arm qemu-img qemu-io`.
- Focused test: `python3 -m unittest qemu-a6400/tests/test_qemu_f2a0.py -q`.
- Each boot was scored by the boot scorer, oracle version 3, with two runs per score.

## Limits

- Emulator only. The camera-captured inputs are not in the repository, so these counts
  cannot be reproduced without them. The record claims no physical camera result.
- The change adds coverage for four pages and twelve offsets. It does not move the firmware
  past its sequencer step, which stays at 42 of 43.
- Two of the four pages are not supported by the static analysis as offset-0 pages of the
  group. They are included because the baseline lists accesses there.
- The rules return the value the catch-all returned (0). The read-modify-write offsets are
  ruled `ZERO` without captured read-back values, so a guest that depends on a non-zero
  read-back at those offsets would see 0 here.
- Write-only rows also answer a read with 0 instead of logging it. Reads at `0xF2A000A4`,
  `0xF2A32030` and `0xF2A321A0` were not in the baseline and now return 0 without a log
  entry.
- A self-clearing or counting status word in these pages would be stored as plain memory
  and not modelled. No such word is claimed to be absent.
- The access-count fall is not explained by the score files, and this record does not
  resolve it.
