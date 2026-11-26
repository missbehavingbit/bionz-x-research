# E0691 — Boot score for the clkrst3 0xF2900000 write-only register rows

Date: 2026-11-24.

This record covers an emulator change that maps the 4 KiB clock and reset bank at
`0xF2900000` (the kernel names it `clkrst3`) with nine per-offset rules. The scores
below were produced by this project's boot scorer (oracle version 3) on the scored
build, configured with `--disable-sdl`. This record does not claim any physical camera
result.

## Observation

- Before the change, the bank was answered by the device-wide catch-all, which returns
  0 for every read. The `cxd90045.unmodelled` window covers `0xF0000000` for `0x08000000`
  bytes.
- The baseline boot listed 13 distinct unmodelled `0x29000xx` address pairs. Twelve
  are covered by the new rules. The thirteenth is the read at `+0x030`, which is
  deliberately left unmodelled because its read feeds a branch in the guest, so a
  constant would be a guess. It still logs as unmodelled.
- No other mapping in the emulator overlaps `0xF2900000` to `0xF2900FFF`. The
  neighbouring windows (the `0xF2800000` register file, the LDEC page at `0xF2903000`
  and the `0xF0000000` window) are separate.

## Change

The rules are in `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c`, and the test is
`qemu-a6400/tests/test_qemu_f290.py`. The bank is created at priority -600, the same
priority as the other per-offset windows. The window is on by default, and the create
call is not gated by any environment option.

| Offset | Rule |
| --- | --- |
| `+0x004`, `+0x008` | read returns 0; writes stored |
| `+0x028`, `+0x034`, `+0x038` | write-only in the baseline; writes stored |
| `+0x044`, `+0x0A4`, `+0x0A8` | write-only in the baseline; writes stored |
| `+0x0C4` | read returns 0; writes stored |

Every rule is `ZERO`: a read returns 0, and a write is stored but never read back. The
guest therefore sees the same values as before. Offsets without a rule still log as
unmodelled on both the read and write paths, so the window is not a blanket zero.
This is coverage, not new behaviour.

## Result

Each column is one score file or one run as recorded. No values are averaged.

| Measure | Before (baseline) | After (rerun) | After (promotion) |
| --- | --- | --- | --- |
| Identity check | ok | ok | ok |
| Final sequencer step | 42 of 43, value `0x897F` | same | same |
| Mismatch count | 0 | 0 | 0 |
| Distinct unmodelled address pairs | 119 | **107** | **107** |
| Unmodelled reads | 56 | 53 | 53 |
| Accesses to unmodelled space | 5,380,060 | 5,425,601 | 5,425,442 |
| `t_reach` (two runs) | 51.9 s / 51.38 s | 52.5 s / 46.77 s | 51.65 s / 52.02 s |
| Contended host | no | no | no |
| Peak QEMU processes | 1 | 1 | 1 |
| Peak load (1 min) | 2.14 (one run) | 2.25 to 2.65 | 2.66 to 2.81 |

The scorer's comparison of the rerun against the baseline reported `moved: true`,
kind `coverage`, `regressed: false`, nothing worse. The promotion re-measured the change
twice on a quiet host and reproduced the same state and count.

Identity: the instruction digest is the same in the baseline and in both scores. The
boot manifest and submicro camera page digests are also the same. The QEMU binary
digest changed, as expected for a code change: `9601231d…` before and `24aaac8a…` after.

The distinct count fell by 12, which matches the twelve covered pairs. The access
count rose by about 45,400 to 45,500 (about 0.8 percent). The score files do not explain
the rise. One candidate, not checked here, is that an 8-byte access to an offset without
a rule is now logged as two 4-byte accesses. Unmodelled reads fell from 56 to 53.

The final sequencer step and its value did not change, so boot progress did not change.

Build checks: the focused test file `qemu-a6400/tests/test_qemu_f290.py` ran 5 tests with
result OK. The unit test suite in this environment reports 1656 tests, with
`errors=142` and `skipped=153`. The same 142 test ids fail on main in this environment,
mostly because of optional dependencies. The dynamic-link check reported no missing
libraries.

## Inputs

- Camera-captured boot inputs: the boot manifest and the submicro camera page. Neither is
  in the repository.
- The camera-captured `IDT_CAM` preload (`idt_cam.bin`). Not in the repository.
- Boot options: `EVSEL_LATE_US=500`, `CAMPIG_ENABLE_READBACK=1`, `SGC1_EVENT_A=1`,
  `GATE_FORCE_PROBE=1`, `LIVEVIEW_SOURCE=bars`, `ENTRY_FORCE_PROBE=1`, `WORK_DMA=1`,
  speed profile `default`, and `-icount shift=0,sleep=on`.
- `GATE_FORCE_PROBE=1` and `ENTRY_FORCE_PROBE=1` are declared probes that the scorer allows;
  they, the `IDT_CAM` preload and every other option are identical before and after, so the
  difference between the columns is this change alone.

## Build and commands

- QEMU v11.1.3 with this repository's `qemu-a6400` overlay, copied from the pristine tree
  into a fresh build directory.
- `setup-qemu.sh` applied the overlay. Configure: `--target-list=arm-softmmu --disable-sdl
  --disable-werror`. Build: `ninja -j2 qemu-system-arm qemu-img qemu-io`.
- Each boot was scored by the boot scorer, oracle version 3, with two runs per score.

## Limits

- Emulator only. The camera-captured inputs are not in the repository, so these counts
  cannot be reproduced without them. The record claims no physical camera result.
- The change adds coverage for one bank. It does not move the firmware past its
  sequencer step, which stays at 42 of 43.
- The `+0x030` read remains unmodelled. Until its branch is understood, that access
  keeps logging.
- The rules return the value the catch-all returned (0). A self-clearing or counting
  status word in this bank would be stored as plain memory and not modelled. No such
  word is claimed to be absent.
- The access-count rise is not explained by the score files, and this record does not
  resolve it.
