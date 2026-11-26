# E0705 — Boot score for the DWC3 gadget model enabled by default at `0xF020C000`

Date: 2026-11-26.

This record covers one emulator change: the existing CXD90045 DWC3 gadget model (machine
option `dwc3-gadget`, E0590) is now enabled by default, so it serves the guest's accesses at
`0xF020C000` and IRQ 229 instead of the device-wide catch-all. The scores below were produced
by this project's boot scorer (oracle version 3) on the scored build, configured with
`--disable-sdl`. This record does not claim any physical camera result.

## Observation

- Before the change, the baseline boot listed 53 distinct unmodelled address pairs. Six of
  them are in the `0xF020C000` DWC3 block, over four distinct offsets: `+0x110` (GCTL) and
  `+0x704` (DCTL) are read and written; `+0x200` (GUSB2PHYCFG0) and `+0x2C0`
  (GUSB3PIPECTL0) are written only. Every access fell through to the device-wide catch-all,
  which returns 0 for every read.
- The model already existed, but behind the opt-in machine option `dwc3-gadget` (E0590),
  which was off by default. The block at `0xF020C000`, size `0x4000`, did not overlap any
  other modelled window in the scored boot.

## Change

The change is in `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` and `qemu-a6400/run.sh`:
- **Default on:** the static `sony_a6400_dwc3_gadget` flag is initialised to `true`, and the
  machine property description says `default on`. No new device model is added; the existing
  E0590 model at `0xF020C000` and IRQ 229 is the one that runs.
- **Served offsets:** the model stores and returns GCTL at `+0x110`, GUSB2PHYCFG0 at
  `+0x200`, GUSB3PIPECTL0 at `+0x2C0` and DCTL at `+0x704`. Writes are kept in the model's
  own state and reads return that stored state.
- **The rest of the block:** an unhandled offset still logs as `cxd90045-dwc3: unimp` for
  both directions; the checked boot produces none.
- **Turning it off:** in `run.sh` the option now defaults on, and `DWC3_GADGET=0` passes
  `dwc3-gadget=off`, which restores the catch-all behaviour.

The test is `qemu-a6400/tests/test_qemu_l0154_dwc3_default.py` (two static checks, no QEMU
boot): the model's default declaration is `true`, and `run.sh` has the `DWC3_GADGET=0`
branch that passes `dwc3-gadget=off` and does not force `dwc3-gadget=on` anywhere.

## Result

Each column is one score file as recorded. No values are averaged. A dash marks a value not
present in these score files.

| Measure | Before (baseline) | After (rerun) | After (promotion) |
| --- | --- | --- | --- |
| Identity check | ok | ok | ok |
| Final sequencer step | 42 of 43, `0x897F` | same | same |
| Mismatch count | 0 | 0 | 0 |
| Distinct unmodelled pairs | 53 | **47** | **47** |
| Unmodelled reads | — | 31 | 31 |
| Accesses to unmodelled space | — | 1,201 | 1,220 |
| `t_reach` | — | 52.29 / 52.11 s | 52.49 / 52.09 s |
| Contended host | — | no | no |
| Peak load (1 min, `busy_max`) | — | 1.62 / 1.60 | 1.44 / 1.58 |

- **Scorer verdict:** the comparison of the rerun against its baseline reported `moved: true`,
  kind `coverage`, `regressed: false`, with nothing worse. Exactly six distinct pairs left the
  unmodelled list and none was added anywhere.
- **Recount:** an independent red-team re-count found the same 53 -> 47 change with 0 added.
  It confirmed that front-end and back-end unimplemented logging cover every unhandled offset,
  that the checked boot has no `cxd90045-dwc3` unimp line, that the four offsets are stored
  state, that DCTL bit 30 (controller reset) resets the model, that the USB serial messages
  are identical, and that IRQ 229 does not fire. It upheld the change with a comment
  correction only: the retired set is six pairs, four writes and two reads, not the four
  write pairs first listed.
- **Promotion:** the promotion was measured twice on a quiet host and reproduced the same
  sequencer state and the same distinct count, 47.
- **Timing:** the scorer samples every 5 s, so the `t_reach` differences are one sampling step
  and not a speed claim. Both scores are marked quiet.

Identity: the instruction digest, the boot manifest digest and the submicro camera page digest
are the same in the rerun and the promotion. The QEMU binary digest is `73de505f…` in both, as
expected for one scored build.

## Inputs

- Camera-captured boot inputs: the boot manifest and the submicro camera page. Neither is in
  the repository.
- The camera-captured `IDT_CAM` preload. Not in the repository. Its contents are not described
  here.
- Boot options of the scored runs: `EVSEL_LATE_US=500`, `CAMPIG_ENABLE_READBACK=1`,
  `SGC1_EVENT_A=1`, `GATE_FORCE_PROBE=1`, `LIVEVIEW_SOURCE=bars`, `ENTRY_FORCE_PROBE=1`,
  `WORK_DMA=1`, speed profile `default`, `-icount shift=0,sleep=on`. `GATE_FORCE_PROBE=1` and
  `ENTRY_FORCE_PROBE=1` are declared probes that the scorer allows. `DWC3_GADGET` is not set
  in the scored runs, so the default applies. Every option is identical before and after, so
  the difference is this change alone.

## Build and commands

- QEMU v11.1.3 with this repository's `qemu-a6400` overlay, built in a fresh build directory,
  configured with `--disable-sdl`.
- Test: `python3 -m unittest qemu-a6400/tests/test_qemu_l0154_dwc3_default.py -q`.
- Each boot was scored by the boot scorer, oracle version 3, with two runs per score.

## Limits

- Emulator only. The camera-captured inputs are not in the repository, so these counts cannot
  be reproduced without them. The record claims no physical camera result.
- The change adds coverage only. It does not move the firmware past its sequencer step, which
  stays at 42 of 43 with 0 mismatches, and it does not add new behaviour.
- The four registers are stored state with no verified hardware behaviour. Reads return the
  model's own last stored value, not a captured bus value; DCTL bit 30 resets the model.
  Whether the real controller answers these offsets the same way is not shown here.
- `DWC3_GADGET=0` in `run.sh` turns the model off and restores the catch-all. Unknown offsets
  in the block stay unmodelled and still log in either case.
