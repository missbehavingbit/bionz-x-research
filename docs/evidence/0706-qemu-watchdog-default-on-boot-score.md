# E0706 — Boot score for the CXD4115 hardware watchdog enabled by default at `0xF2402000`

Date: 2026-11-26.

This record covers one emulator change: the existing CXD4115 hardware watchdog model (machine
option `watchdog`, E0636) is now enabled by default, so it serves the stock kernel's watchdog
setup and kick writes at `0xF2402000` instead of the device-wide catch-all. The scores below
were produced by this project's boot scorer (oracle version 3) on the scored build, configured
with `--disable-sdl`. This record does not claim any physical camera result.

## Observation

- Before the change, the baseline boot listed 47 distinct unmodelled address pairs. Three of
  them are the one-channel watchdog block at `0xF2402000`: writes at `+0x0` (CTRL), `+0x4`
  (CLR) and `+0x8` (CMP). Every access fell through to the device-wide catch-all, which drops
  writes, so an expiry could never happen.
- The model already existed, but behind the opt-in machine option `watchdog` (E0636), which
  was off by default. That record's register contract is the one this change relies on: the
  kernel's `cxd900x0_wdt_setup`/`start`/`stop` and `watchdog_touch`/`tick` drive one CXD4115
  channel with CTRL `0x80000012`, CMP `1001 * timeout_ms`, CTRL `0x80001012` (START), kick CLR
  `0x10`, stop CTRL `0` plus CLR `0x11`.

## Change

The change is in `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` and `qemu-a6400/run.sh`:
- **Default on:** the static `sony_a6400_watchdog` flag is initialised to `true`, and the
  machine property description says `default on`. No new device model is added; the existing
  E0636 one-channel CXD4115 channel at `0xF2402000` is the one that runs, and no IRQ is wired.
- **On expiry:** the channel reports `cxd4115-wdt: chN expired (ctrl=… cmp=…)` and calls
  `watchdog_perform_action()`, so QEMU `-action watchdog=` selects the action. A missed kick
  now resets the machine by default; `-action watchdog=` can select pause, shutdown, poweroff,
  debug, none or inject-nmi instead.
- **Turning it off:** in `run.sh` the option now defaults on, and `WATCHDOG=0` passes
  `watchdog=off`, which restores the write-dropped behaviour.
- **Tests:** `tests/test_qemu_watchdog.py` replays the kernel's exact register sequence; its
  old default-machine drop test is retargeted to `watchdog=off` with its asserts intact, and a
  default-machine expiry test is added. A two-check static test (no QEMU boot) asserts that the
  model defaults to `true` and that `run.sh` passes `watchdog=off` for `WATCHDOG=0` without
  forcing `watchdog=on` anywhere.

## Result

Each column is one score file as recorded. No values are averaged. A dash marks a value not
present in these score files.

| Measure | Before (baseline) | After (rerun) | After (promotion) |
| --- | --- | --- | --- |
| Identity check | ok | ok | ok |
| Final sequencer step | 42 of 43, `0x897F` | same | same |
| Mismatch count | 0 | 0 | 0 |
| Distinct unmodelled pairs | 47 | **44** | **44** |
| Unmodelled reads | 31 | 31 | 31 |
| Accesses to unmodelled space | 1,220 | 474 | 478 |
| `t_reach` | 52.49 / 52.09 s | 51.72 / 51.55 s | 51.61 / 51.75 s |
| Contended host | — | no | no |
| Peak load (1 min, `busy_max`) | — | 1.44 / 1.58 | 1.47 / 2.07 |

- **Scorer verdict:** the comparison was accepted as coverage. The change removed three
  distinct pairs and added none: writes at `+0x0`, `+0x4` and `+0x8` at `0xF2402000` leave the
  unmodelled list, and the final machine state is the same. Coverage only, not new behaviour.
- **Recount:** an independent red-team re-count found the same 47 -> 44 change with 0 added,
  and confirmed that only those three watchdog write offsets leave. It found no
  `cxd4115-wdt` line in either scored run, one boot banner each, and identical final state. It
  confirmed that every other unhandled offset in the block still logs for both directions, and
  that the model uses QEMU timers and `watchdog_perform_action` only — no guest-RAM write, no
  guest-PC branch and no sourceless constant reply.
- **Promotion:** the promotion was measured twice on a quiet host and reproduced the same
  sequencer state and the same distinct count, 44.
- **Timing:** the scorer samples every 5 s, so the `t_reach` differences are one sampling step
  and not a speed claim. Both scores are marked quiet.
- **Expiries:** zero expiries were observed in the scored runs. The guest armed the watchdog
  and kicked it about every 6.8 ms in the scoring profile (`-icount shift=0,sleep=on`); the
  default-off log ends with 808 armed/kick writes, the last at its final line. If the guest had
  stopped kicking while armed, a reset would follow within about 0.5 s (CMP `0x7a314` at
  1 MHz); it never fired.

Identity: the instruction digest, the boot manifest digest and the submicro camera page digest
are the same in the rerun and the promotion. The QEMU binary digest is `eb54ee51…` in both, as
expected for one scored build.

## Inputs

- Camera-captured boot inputs: the boot manifest and the submicro camera page. Neither is in
  the repository.
- The camera-captured `IDT_CAM` preload. Not in the repository. Its contents are not described
  here.
- Boot options of the scored runs: `EVSEL_LATE_US=500`, `CAMPIG_ENABLE_READBACK=1`,
  `SGC1_EVENT_A=1`, `GATE_FORCE_PROBE=1`, `LIVEVIEW_SOURCE=bars`, `ENTRY_FORCE_PROBE=1`,
  `WORK_DMA=1`, speed profile `default`, `-icount shift=0,sleep=on`. `GATE_FORCE_PROBE=1` and
  `ENTRY_FORCE_PROBE=1` are declared probes that the scorer allows. `WATCHDOG` is not set in
  the scored runs, so the default applies. Every option is identical before and after, so the
  difference is this change alone.

## Build and commands

- QEMU v11.1.3 with this repository's `qemu-a6400` overlay, built in a fresh build directory,
  configured with `--disable-sdl`.
- Test: `python3 -m unittest tests.test_qemu_watchdog -v` (four cases) and the static
  default-on check (two cases); all six pass.
- Each boot was scored by the boot scorer, oracle version 3, with two runs per score.

## Limits

- Emulator only. The camera-captured inputs are not in the repository, so these counts cannot
  be reproduced without them. The record claims no physical camera result.
- The change adds coverage only. It does not move the firmware past its sequencer step, which
  stays at 42 of 43 with 0 mismatches.
- The clock rate, the reset scope and the reset-on-match bit are inferences from E0636, not
  checked on hardware.
- Zero expiries are established **only** for the scoring profile `-icount shift=0,sleep=on`
  (the oracle runs and one lead boot; the guest armed the watchdog and kicked it about every
  6.8 ms there) and for one `sleep=off` boot with the model on that completed. The other
  speed-profile boots are not valid evidence: one `sleep=off` boot with the model off ended
  early, and all `shift=auto` and multi-threaded TCG boots, on and off, ended in the guest
  exception monitor. Behaviour under those profiles is not established.
- `WATCHDOG=0` (`watchdog=off`) restores the write-dropped behaviour; the `0xF2402000` writes
  then fall back to the unmodelled catch-all and no expiry can occur.
