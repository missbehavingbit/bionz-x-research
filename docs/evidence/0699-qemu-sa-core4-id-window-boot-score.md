# E0699 — Boot score for an ID-only window on SA core 4 at `0xF35D00F0`

Date: 2026-11-25.

This record covers an emulator change that answers the four ID words of a fifth SA DSP core
at `0xF35D0000`. The value comes from a constant that the firmware itself compares against.
It was not captured from the camera bus. The scores below were produced by this project's
boot scorer (oracle version 3) on the scored build, configured with `--disable-sdl`. This
record does not claim any physical camera result.

## Observation

- Before the change, the baseline boot listed 57 distinct unmodelled address pairs. Four of
  them are reads with no matching write: `read 0x35d00f0`, `read 0x35d00f4`,
  `read 0x35d00f8` and `read 0x35d00fc` (physical `0xF35D00F0`..`0xF35D00FC`). Each is read
  once per boot. The device-wide catch-all answered them with 0.
- The board already models four SA cores (`sa_cores` in `sony_a6400.c`): `0xF34A0000`,
  `0xF34B0000`, `0xF34C0000` and `0xF35C0000`. Each returns a 16-byte ID at `+0xF0`..`+0xFC`.
  Their IDs come from a block of ID strings in `av-cam.bin`.
- Static analysis of `av-cam.bin` (SHA-256 `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`)
  shows the following:
  - **Five bases.** av-cam keeps the SA core bases in a five-entry table that appears 13
    times, with index 4 = `0xF35D0000` and indices 0..3 = the four modelled cores.
  - **The ID check.** An ID routine at `0x63A7ED50` reads the four ID words of every core,
    0 to 4, through an accessor at `0x63A7BAF0`. The accessor reads base + `0xF0` + 4 × n.
    The routine stores core 4's words in a buffer and then compares each core's 16 bytes,
    byte by byte, with a constant string.
  - **The expected value.** For core 4 the constant is the 16 bytes `SA1_V1.0SA5_V1.0`, the
    fifth entry of the ID string block. It is byte-identical to SA1's string.
  - **On a mismatch,** the routine sets one sticky status byte (0 when all five match). The
    routine's direct caller continues either way. Later use of that byte was not traced.
  - **Only one reader.** No other av-cam code reads `0xF35D00F0`..`0xF35D00FC`. Fourteen
    other code sites use the `0xF35D0000` base, at other offsets (`+0x0`, `+0x4`, `+0x8`,
    `+0xC`, `+0x1C`, `+0x30`, `+0x80`..`+0x88`). The baseline boot touches none of them.
- The literal pool and compare operands were resolved independently twice, by the analysis
  and by a separate review.

## Change

The model is in `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c`:
- **The window:** a 16-byte read-only region `cxd90045.sa4-id` at `0xF35D00F0`, priority
  -600 (above the -1000 catch-all). Reads return `SA1_V1.0SA5_V1.0` little-endian, with
  correct bytes for 1-, 2- and 4-byte reads.
- **Writes** are ignored and logged under `-d unimp`, as `cxd90045.sa4-id: write …`. The
  baseline has no write there.
- **What stays unmodelled:** the rest of core 4 (`0xF35D0000` page, IRQs, other registers).
  The firmware gives no evidence for it, and no full SA core is instantiated. The sa_cores
  table still has four entries.

The test is `qemu-a6400/tests/test_qemu_l0137_sa4_id.py`. It checks the ID bytes, the base,
the size, the priority, the region name, and that no fifth SA core was added. It reads only
the overlay source.

## Result

Each column is one score file as recorded. No values are averaged.

| Measure | Before (baseline) | After (rerun) | After (promotion) |
| --- | --- | --- | --- |
| Identity check | ok | ok | ok |
| Final sequencer step | 42 of 43, value `0x897F` | same | same |
| Mismatch count | 0 | 0 | 0 |
| Distinct unmodelled address pairs | 57 | **53** | **53** |
| Unmodelled reads | 37 | 33 | 33 |
| Accesses to unmodelled space | 1,228 | 1,230 | 1,222 |
| `t_reach` | 52.07 s / 52.31 s | 52.12 s / 51.51 s | 52.67 s / 46.49 s |
| Contended host | no | no | no |
| Peak load (1 min, `busy_max`) | 1.54 | 1.81 | 1.41 |

The scorer's comparison of the rerun against the baseline reported `moved: true`, kind
`coverage`, `regressed: false`, with nothing worse. Exactly the four reads above left the
unmodelled list, and no entry was added. Both runs of both scores ended at the same
sequencer step with 0 mismatches. The promotion was measured twice on a quiet host and
reproduced the same state and the same distinct count.

Identity: the instruction digest, the boot manifest digest and the submicro camera page
digest are the same in the baseline and in both scores. The QEMU binary digest changed, as
expected for a code change: `ef287356…` before and `d6d560c3…` after, identical in the rerun
and the promotion.

Build checks: the focused test ran with result OK. The unit test suite in this environment
reports 1665 tests with `errors=143` and `skipped=153`. The same 143 test ids fail on main
in this environment (compared id by id, 0 new), mostly because of optional dependencies.

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
- Configure with `--disable-sdl`. Build: `ninja -j3 qemu-system-arm qemu-img qemu-io`.
- Focused test: `python3 -m unittest qemu-a6400/tests/test_qemu_l0137_sa4_id.py -q`.
- Static analysis: capstone 5.0.6 in Thumb mode over `av-cam.bin`, with VA = `0x635C6000` +
  file offset.
- Each boot was scored by the boot scorer, oracle version 3, with two runs per score.

## Limits

- Emulator only. The camera-captured inputs are not in the repository, so these counts
  cannot be reproduced without them. The record claims no physical camera result.
- The ID is the value the firmware expects, not a captured bus read. Whether the real
  chip has a fifth SA core that answers this ID is not shown here. A read of the four words
  at `0xF35D00F0`..`0xF35D00FC` on a running camera would settle it.
- The sticky status byte the routine sets on a mismatch changes from 1 to 0 with this
  model. Its later use by the firmware was not traced. The final sequencer state and
  mismatch count did not change.
- The change adds coverage for four reads. It does not move the firmware past its
  sequencer step, which stays at 42 of 43.
