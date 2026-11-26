# E0703 — Boot score for a per-offset window on the `0xF3040000` AIF page

Date: 2026-11-25 (measurements 2026-11-21).

This record covers one emulator change: a per-offset rule window for the AIF page `0xF3040000`..`0xF304FFFF`
(loop lead L0108). The scores below were produced by this project's boot scorer (oracle version 3) on the scored
build, configured with `--disable-sdl`. This record does not claim any physical camera result.

## Observation

- Before the change, the baseline boot listed 157 distinct unmodelled address pairs. 17 of them are in the
  `0xF3040000` page, over 14 distinct offsets and 67 accesses: 4 offsets are read and 13 are written, and 3 are
  both read and written. Every access fell through to the device-wide catch-all, which returns 0 for every read.
- The AIF group at `0xF3041000` (2 channels, `0x800` bytes) belongs to the live-view pipeline and is not one of the
  17 pairs.
- A first-hit watchpoint pass named every reader and writer. Readers are `+0x004` (`0x637884F2`, never written),
  `+0x108` (`0x637883A6`), `+0x110` (`0x637883C6`) and `+0x120` (`0x637883F0`, the repeated poll). Writers are
  `+0x100`, `+0x108`, `+0x110`, `+0x11C`, `+0x120`, `+0x124`, the address words `+0x128`..`+0x138`, `+0x13C` and
  `+0x008`.
- Per offset, the baseline shows one of three patterns:
  - **Read-back** (RF): the guest reads back a value it wrote itself, at the three offsets `+0x108`, `+0x110` and
    `+0x120`.
  - **Write-only**, at ten offsets.
  - **Read with no writer**, at the read-only status word `+0x004`. A read returns 0 from the catch-all.

## Change

The model is in `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c`:
- **The window:** a 64 KiB per-offset rule window `cxd90045.f304` at `0xF3040000`, priority -600, above the
  `cxd90045.unmodelled` catch-all at -1000 and below the live-view AIF group at `0xF3041000` (-300), which keeps
  its `0x800`-byte register block.
- **The rules:** 14 rules, one per distinct offset. 3 are RF, where the guest reads back its own write. 11 are
  ZERO: writes are stored, and reads return 0, the value the catch-all returned.
- **Unruled offsets** inside the window still log both ways as `cxd90045.f304.unmodelled` with the `0xF0000000`
  base, under the window's name. An unruled access is not silenced, so the window is not a blanket zero.

The ZERO rules return exactly what the catch-all returned, so for those offsets the guest sees the same reads as
before. The RF rules return the guest's own last write.

The test is `qemu-a6400/tests/test_qemu_f304.py` (18 checks): all 3 RF offsets read back the exact word written,
all 11 ZERO offsets read 0 after a write, the ruled offsets produce no unmodelled log line, and an unruled offset
inside the window (`0xF3041800`) still logs both a read and a write.

## Result

Each column is one score file as recorded. No values are averaged. A dash marks a value not present in these
score files.

| Measure | Before (baseline L0105) | After (rerun) | After (promotion) |
| --- | --- | --- | --- |
| Identity check | ok | ok | ok |
| Final sequencer step | 42 of 43, `0x897F` | same | same |
| Mismatch count | 0 | 0 | 0 |
| Distinct unmodelled pairs | 157 | **140** | **140** |
| Unmodelled reads | — | 59 | 59 |
| Accesses to unmodelled space | — | 6,285,664 | 6,550,343 |
| `t_reach` | — | 42.21 / 42.11 s | 42.11 / 42.08 s |
| Contended host | — | no | no |
| Peak load (1 min, `busy_max`) | — | 2.39 | 1.30 |

- **Scorer verdict:** the comparison of the rerun against its baseline reported `moved: true`, kind `coverage`,
  with nothing worse. Exactly 17 distinct pairs left the unmodelled list and none was added anywhere.
- **Recount:** an independent recount found the same 157 -> 140 change with 0 added. It parsed the 14 rules as
  3 RF and 11 ZERO, matched the ruled set against the baseline offsets, confirmed that all RF offsets are written
  before they are read, and confirmed that an unruled offset still logs both directions.
- **Promotion:** the promotion was measured twice on a quiet host and reproduced the same sequencer state and the
  same distinct count, 140.
- **Timing:** the scorer samples every 5 s, so the `t_reach` differences are one sampling step and not a speed claim.

Identity: the instruction digest, the boot manifest digest and the submicro camera page digest are the same in
the rerun and the promotion. The QEMU binary digest is `e16e40ae…` in both, as expected for one scored build.

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
- Test: `python3 qemu-a6400/tests/test_qemu_f304.py` with `A6400_QEMU_DIR` pointing at the scored build.
- Each boot was scored by the boot scorer, oracle version 3, with two runs per score.

## Limits

- Emulator only. The camera-captured inputs are not in the repository, so these counts cannot be reproduced
  without them. The record claims no physical camera result.
- The change adds coverage. It does not add new behaviour, decode the AIF registers, or move the firmware past its
  sequencer step, which stays at 42 of 43 with 0 mismatches.
- ZERO rules give the value the catch-all gave, not a captured hardware value. The firmware's use of the
  write-only registers is not decoded. A guest that depended on a non-zero read at those offsets would see 0.
- RF rules store and read back the guest's own last write. They are not a bus capture of a hardware register.
- The read-only status word `+0x004` has no writer in the boot. Its real value is uncaptured, and the model
  returns 0, as the catch-all did.
