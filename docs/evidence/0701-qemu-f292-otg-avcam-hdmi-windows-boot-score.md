# E0701 — Boot score for per-offset windows on the `0xF2920000` OTG, av-cam and HDMI pages

Date: 2026-11-25 (measurements 2026-11-21).

This record covers two emulator changes, sent upstream together: per-offset rule windows for the kernel OTG and two
av-cam sub-blocks of the `0xF2920000` block (loop lead L0100), and a ZERO window for the `0xF292C000` HDMI page
(loop lead L0102). The scores below were produced by this project's boot scorer (oracle version 3) on the scored
builds, configured with `--disable-sdl`. This record does not claim any physical camera result.

## Observation

- Before the changes, the baseline boot listed 730 distinct unmodelled address pairs. 26 of them were in
  `0xF2920000`..`0xF292FFFF`, over 22 offsets and 66 accesses. Every one fell through to the device-wide catch-all,
  which returns 0 for every read.
- A static map of the block (kernel Image and `av-cam.bin`) found three owners:
  - **Kernel OTG** (`0x0000`..`0x2FFF`): the kernel's `otg` udif device, base `0xF2920000`, size `0x3000`.
  - **av-cam sub-blocks** at `0xF292A000` and `0xF292E000`: av-cam loads these literal bases.
  - **`0xF292C000`**: no literal in av-cam or the kernel. A runtime watchpoint in a full boot found the reader and
    writer in the `hdmi` kernel module (`hdmi_util_ioread32` / `hdmi_util_iowrite32`).
- Per offset, the baseline shows one of three patterns:
  - **Read-back** (RF): the guest reads back a value it wrote itself.
  - **Write-only.**
  - **Read with no writer**, at `0xF292C160`, read 21 times.

## Change

Both changes are in `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c`, as per-offset windows at priority -600:
- **OTG, av-cam A and av-cam E windows:** 11 rules. Four are RF, where the guest reads back its own write. Seven are
  ZERO: writes are stored, and reads return 0, the value the catch-all returned. They retire 15 pairs.
- **`0xF292C000` page:** one window with ZERO rules for its 10 write-only offsets and for the `+0x160` read. It
  retires 11 pairs.
- **Unruled offsets** in every window still log as unmodelled, under the window's name. An unruled access is not
  silenced.
- **The opt-in E0580 region** at `0xF2920010` keeps priority 0, above the new window.

The ZERO rules return exactly what the catch-all returned, so for those offsets the guest sees the same reads as
before. The RF rules return the guest's own last write.

The tests are qemu-a6400/tests/test_qemu_f292.py (18 checks: RF read-back, ZERO reads, unruled offsets still logged,
and the `0xF292C000` page no longer reaching the catch-all) and qemu-a6400/tests/test_qemu_f292c.py (16 checks).
Both pass against a build of this repository's current overlay.

## Result

Each column is one score file as recorded. No values are averaged.

| Measure | Before (baseline) | OTG/A/E (rerun) | OTG/A/E (promotion) | + C page (rerun) | + C page (promotion) |
| --- | --- | --- | --- | --- | --- |
| Identity check | ok | ok | ok | ok | ok |
| Final sequencer step | 42 of 43, `0x897F` | same | same | same | same |
| Mismatch count | 0 | 0 | 0 | 0 | 0 |
| Distinct unmodelled pairs | 730 | **715** | **715** | **704** | **704** |
| Unmodelled reads | 86 | 79 | 79 | 78 | 78 |
| Accesses to unmodelled space | 7,626,479 | 7,460,306 | 7,506,658 | 7,347,196 | 7,643,422 |
| `t_reach` | 42.65 / 42.09 s | 47.22 / 47.21 s | 43.18 / 42.21 s | 47.28 / 47.31 s | 47.76 / 47.69 s |
| Contended host | no | no | no | no | no |
| Peak load (1 min, `busy_max`) | 1.24 | 2.17 | 1.25 | 2.92 | 1.33 |

- **Scorer verdict:** the comparison of each rerun against its baseline reported `moved: true`, kind `coverage`,
  with nothing worse.
- **Recount:** an independent recount for each change found exactly the targeted pairs removed (15, then 11) and none
  added.
- **Promotion:** each promotion was measured twice on a quiet host and reproduced the same sequencer state and the
  same distinct count.
- **Timing:** the scorer samples every 5 s, so the `t_reach` differences are one sampling step and not a speed claim.

Identity: the instruction digest, boot manifest digest and submicro camera page digest are the same in all five
score files. The QEMU binary digest changed with each code change: `5708b5f9…` (before), `b1bd997d…` (OTG/A/E),
`59eaf180…` (+ C page).

## Inputs

- Camera-captured boot inputs: the boot manifest and the submicro camera page. Neither is in the repository.
- The camera-captured `IDT_CAM` preload. Not in the repository. Its contents are not described here.
- Boot options of the scored runs: `EVSEL_LATE_US=500`, `CAMPIG_ENABLE_READBACK=1`, `SGC1_EVENT_A=1`,
  `GATE_FORCE_PROBE=1`, `LIVEVIEW_SOURCE=bars`, `ENTRY_FORCE_PROBE=1`, `WORK_DMA=1`, speed profile `default`,
  `-icount shift=0,sleep=on`. `GATE_FORCE_PROBE=1` and `ENTRY_FORCE_PROBE=1` are declared probes that the scorer
  allows. Every option is identical across the columns, so the differences are these changes alone.

## Build and commands

- QEMU v11.1.3 with this repository's `qemu-a6400` overlay, built in a fresh build directory, configured with
  `--disable-sdl`; `ninja qemu-system-arm qemu-img qemu-io`.
- Tests: `python3 qemu-a6400/tests/test_qemu_f292.py` and `python3 qemu-a6400/tests/test_qemu_f292c.py`
  (`A6400_QEMU_DIR` pointing at the build).
- Each boot was scored by the boot scorer, oracle version 3, with two runs per score.

## Limits

- Emulator only. The camera-captured inputs are not in the repository, so these counts cannot be reproduced without
  them. No physical camera result is claimed.
- The scores were measured on 2026-11-21, on the stack of that day (730 distinct pairs before). Later changes have
  reduced the count further, but they do not touch these windows.
- ZERO rules give the value the catch-all gave, not a captured hardware value. The firmware's use of the write-only
  registers is not decoded. A guest that depended on a non-zero read at those offsets would see 0.
- The `0xF292C160` read has no writer in the boot. Its real value is uncaptured, and the model returns 0 as before.
- The changes add coverage. They do not move the firmware past its sequencer step, which stays at 42 of 43.
