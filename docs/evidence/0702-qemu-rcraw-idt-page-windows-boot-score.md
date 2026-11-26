# E0702 — Boot score for a per-offset window on the `0xF3030000` RCRAW/IDT page

Date: 2026-11-25 (measurements 2026-11-21).

This record covers one emulator change: a per-offset rule window for the IDT/RCRAW hardware compressor page
`0xF3030000`..`0xF303FFFF` (loop lead L0105). The scores below were produced by this project's boot scorer
(oracle version 3) on the scored build, configured with `--disable-sdl`. This record does not claim any physical
camera result.

## Observation

- Before the change, the baseline boot listed 704 distinct unmodelled address pairs. 547 of them are in the
  `0xF3030000` page, over 534 distinct offsets: 15 offsets are read and 532 are written, and 13 are both read and
  written. Every access fell through to the device-wide catch-all, which returns 0 for every read.
- The brief for the lead counted 112 pairs in this page. That was the oracle's `unmodelled_top` list, which is
  capped at 200 entries; the full `-d unimp` log of the certified 120 s boot is the ground truth used here.
- Per offset, the baseline shows one of three patterns:
  - **Read-back** (RF): the guest reads back a value it wrote itself, at 12 offsets.
  - **Write-only.**
  - **Read with no writer**, at two read-only status words. A read returns 0 from the catch-all.
- A first-hit watchpoint pass named every reader (15 of 15 read offsets) and a matching pass named the writers
  (13 of 13 written offsets), with the mode sequencer still at its step. The 512-word bulk block
  `0xF3031000`..`0xF30317FC` is written by one writer; the scattered config words and the self-test words have their
  own writers.

## Change

The model is in `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c`:
- **The window:** a 64 KiB per-offset rule window `cxd90045.f303` at `0xF3030000`, priority -600, above the
  `cxd90045.unmodelled` catch-all at -1000. The CAMPIG windows end below it.
- **The rules:** 534 rules, one per distinct offset. 12 are RF, where the guest reads back its own write. 522 are
  ZERO: writes are stored, and reads return 0, the value the catch-all returned.
- **Unruled offsets** inside the window still log both ways as `cxd90045.f303.unmodelled`, under the window's
  name. An unruled access is not silenced, so the window is not a blanket zero.

The ZERO rules return exactly what the catch-all returned, so for those offsets the guest sees the same reads as
before. The RF rules return the guest's own last write.

The test is `qemu-a6400/tests/test_qemu_rcraw.py` (41 checks): all 12 RF offsets read back the exact word written,
25 sampled ZERO offsets read 0 after a write, the ruled offsets produce no unmodelled log line, and an unruled
offset inside the window (`0xF3031800`) still logs both a read and a write.

## Result

Each column is one score file as recorded. No values are averaged. A dash marks a value not present in these
two score files.

| Measure | Before (baseline L0102) | After (rerun) | After (promotion) |
| --- | --- | --- | --- |
| Identity check | ok | ok | ok |
| Final sequencer step | 42 of 43, `0x897F` | same | same |
| Mismatch count | 0 | 0 | 0 |
| Distinct unmodelled pairs | 704 | **157** | **157** |
| Unmodelled reads | — | 63 | 63 |
| Accesses to unmodelled space | — | 7,610,805 | 7,446,398 |
| `t_reach` | — | 43.16 / 42.12 s | 42.12 / 42.10 s |
| Contended host | — | no | no |
| Peak load (1 min, `busy_max`) | — | 3.26 | 1.21 |

- **Scorer verdict:** the comparison of the rerun against its baseline reported `moved: true`, kind `coverage`,
  with nothing worse. Exactly 547 distinct pairs left the unmodelled list and none was added.
- **Recount:** an independent recount found the same 704 -> 157 change with 0 added. It parsed the 534 rules as
  12 RF and 522 ZERO, matched the ruled set against the baseline offsets, confirmed that all 12 RF offsets are
  written before they are read, and confirmed that an unruled offset still logs both directions.
- **Promotion:** the promotion was measured twice on a quiet host and reproduced the same sequencer state and the
  same distinct count, 157.
- **Timing:** the scorer samples every 5 s, so the `t_reach` differences are one sampling step and not a speed claim.

Identity: the instruction digest, the boot manifest digest and the submicro camera page digest are the same in
the rerun and the promotion. The QEMU binary digest is `dea94742…` in both, as expected for one scored build.

## Inputs

- Camera-captured boot inputs: the boot manifest and the submicro camera page. Neither is in the repository.
- The camera-captured `IDT_CAM` preload. Not in the repository. Its contents are not described here.
- Boot options of the scored runs: `EVSEL_LATE_US=500`, `CAMPIG_ENABLE_READBACK=1`, `SGC1_EVENT_A=1`,
  `GATE_FORCE_PROBE=1`, `LIVEVIEW_SOURCE=bars`, `ENTRY_FORCE_PROBE=1`, `WORK_DMA=1`, speed profile `default`,
  `-icount shift=0,sleep=on`. `GATE_FORCE_PROBE=1` and `ENTRY_FORCE_PROBE=1` are declared probes that the scorer
  allows. Every option is identical before and after, so the difference is this change alone.

## Build and commands

- QEMU v11.1.3 with this repository's `qemu-a6400` overlay, built in a fresh build directory, configured with
  `--disable-sdl`.
- Test: `python3 qemu-a6400/tests/test_qemu_rcraw.py` with `A6400_QEMU_DIR` pointing at the scored build.
- Each boot was scored by the boot scorer, oracle version 3, with two runs per score.

## Limits

- Emulator only. The camera-captured inputs are not in the repository, so these counts cannot be reproduced
  without them. The record claims no physical camera result.
- The change adds coverage. It does not add new behaviour, decode the compressor, or move the firmware past its
  sequencer step, which stays at 42 of 43 with 0 mismatches.
- ZERO rules give the value the catch-all gave, not a captured hardware value. The firmware's use of the
  write-only registers is not decoded. A guest that depended on a non-zero read at those offsets would see 0.
- RF rules store and read back the guest's own last write. They are not a bus capture of a hardware register.
- The two read-only status words have no writer in the boot. Their real values are uncaptured, and the model
  returns 0, as the catch-all did.
