# E0704 — Boot score for ZERO-only rows on the `0xF0220000` FUSB block

Date: 2026-11-25 (measurements 2026-11-24).

This record covers one emulator change: a per-offset rule window for the FUSB block `0xF0220000`..`0xF023FFFF`
(loop lead L0114). The scores below were produced by this project's boot scorer (oracle version 3) on the scored
build, configured with `--disable-sdl`. This record does not claim any physical camera result.

## Observation

- Before the change, the baseline boot listed 127 distinct unmodelled address pairs. Eight of them are in the
  `0xF0220000` block, over five distinct offsets: `+0x0`, `+0x4`, `+0x11008`, `+0x11080` and `+0x11084`. Every
  access fell through to the device-wide catch-all, which returns 0 for every read.
- The block is the kernel UDIF resource `add` at physical `0xF0220000`, size `0x20000`, priority -600. It is the
  block the firmware reaches through its probe at `0x602AD52C` and handler `0x602B6778`.
- An earlier count listed nine pairs. The recount is eight pairs over the five offsets: `+0x0`, `+0x4` and
  `+0x11008` are read and written, and `+0x11080` and `+0x11084` are written only.
- Per offset, the baseline shows two patterns:
  - **Read-modify-write**, at `+0x0`, `+0x4` and `+0x11008`: the guest reads the offset and writes it.
  - **Write-only**, at `+0x11080` and `+0x11084`.
- A first read-back version of this window was measured and refuted: returning the guest's own last write made the
  final state non-deterministic. This record scores the ZERO-only version.

## Change

The model is in `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c`:
- **The window:** a 128 KiB per-offset rule window `cxd90045.f022` at `0xF0220000`, priority -600, above the
  `cxd90045.unmodelled` catch-all at -1000. The DWC3 block at `0xF020C000`, size `0x4000`, does not overlap it.
- **The rules:** five rules, one per distinct offset, all ZERO. Writes are stored but never read back. Reads
  return 0, the value the catch-all returned before.
- **Unruled offsets** inside the window still log both ways as `cxd90045.f022.unmodelled` with the `0xF0000000`
  base, under the window's name. An unruled access is not silenced, so the window is not a blanket zero.

The ZERO rules return exactly what the catch-all returned, so for the five offsets the guest sees the same reads
as before. There are no RF rows in this change; an RF row would store and read back the guest's own last write.

The test is `qemu-a6400/tests/test_qemu_f022.py` (12 checks): the static table has exactly the five offsets and no
RF row, each ruled offset reads 0 after a write, the ruled offsets produce no unmodelled log line, and an unruled
offset inside the window (`0xF0225000`) still logs both a read and a write.

## Result

Each column is one score file as recorded. No values are averaged. A dash marks a value not present in these
score files.

| Measure | Before (baseline L0111) | After (rerun) | After (promotion) |
| --- | --- | --- | --- |
| Identity check | ok | ok | ok |
| Final sequencer step | 42 of 43, `0x897F` | same | same |
| Mismatch count | 0 | 0 | 0 |
| Distinct unmodelled pairs | 127 | **119** | **119** |
| Unmodelled reads | — | 56 | 56 |
| Accesses to unmodelled space | — | 5,386,628 | 5,380,060 |
| `t_reach` | — | 52.90 / 51.56 s | 51.90 / 51.38 s |
| Contended host | — | yes | no |
| Peak load (1 min, `busy_max`) | — | 3.87 / 3.61 | 2.14 / 2.22 |

- **Scorer verdict:** the comparison of the rerun against its baseline reported `moved: true`, kind `coverage`,
  with nothing worse. Exactly eight distinct pairs left the unmodelled list and none was added anywhere.
- **Recount:** an independent red-team re-count found the same 127 -> 119 change with 0 added. It parsed the five
  rules as all ZERO, confirmed that the catch-all returns 0 for every size as the ZERO rows do, confirmed that the
  ruled offsets are silent in the unmodelled log, that unruled offsets still log both directions, and that the
  window does not overlap DWC3. It upheld the change with a comment correction only.
- **Promotion:** the promotion was measured twice on a quiet host and reproduced the same sequencer state and the
  same distinct count, 119.
- **Timing:** the scorer samples every 5 s, so the `t_reach` differences are one sampling step and not a speed
  claim. The rerun is marked contended; the promotion ran on a quiet host.

Identity: the instruction digest, the boot manifest digest and the submicro camera page digest are the same in
the rerun and the promotion. The QEMU binary digest is `9601231d…` in both, as expected for one scored build.

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
- Test: `python3 qemu-a6400/tests/test_qemu_f022.py` with `A6400_QEMU_DIR` pointing at the scored build.
- Each boot was scored by the boot scorer, oracle version 3, with two runs per score.

## Limits

- Emulator only. The camera-captured inputs are not in the repository, so these counts cannot be reproduced
  without them. The record claims no physical camera result.
- The change adds coverage. It does not add new behaviour, decode the FUSB registers, or move the firmware past
  its sequencer step, which stays at 42 of 43 with 0 mismatches.
- ZERO rules give the value the catch-all gave, not a captured hardware value. They answer 0 as the stub did and
  do not store and read back a write. A guest that depended on a non-zero read at those offsets would see 0.
- The read-back model was rejected because it made the final state non-deterministic. No captured value for these
  offsets is claimed here.
- Unruled offsets in the 128 KiB window stay unmodelled and still log.
