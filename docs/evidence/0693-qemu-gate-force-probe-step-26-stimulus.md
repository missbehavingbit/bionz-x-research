# E0693 — Opt-in gate-force-probe for the step-26 gate (declared stimulus)

Date: 2026-11-20. Recorded from the description of merged pull request
[#51](https://github.com/ivica00/bionz-x-research/pull/51)
(merge commit `2db9d597e07e014a6005991285340738f6e40d6d`, 2026-11-20T16:17:01+01:00),
author `alexscemo`. This workspace did not re-run QEMU or the boot scorer and did not
access a physical camera. The pull request reports no oracle score for this change: it
is a declared stimulus and was not scored as progress.

## Result

The merge adds a machine property, `gate-force-probe`, and a wrapper option
`GATE_FORCE_PROBE=1` that sets it. The property is false unless set, so the change is off
by default. The pull request body also says the change is "active in the default
machine"; that sentence contradicts its own code and its "off-by-default" statement, and
this record follows the code. When enabled, the board checks guest physical
address `0x03874FD0` every 100 ms of virtual time. Only when the six bytes at that address
match the expected av-cam step-26 producer sequence does it replace one 16-bit Thumb
conditional branch with a 16-bit NOP, once. The producer then always stores 1.

The pull request states that this is not a model or a fix. It is the same force as an
earlier debugger-based probe, applied without a debugger. The declared condition is
`cond. PROBE gate-force`.

The result is a stimulus result. A run with this probe on shows the state that the forced
write produces. It is not evidence of firmware behaviour on the unmodified path.

## Observed run states, as reported

These are the values the pull request reports for its lead check. They are not
re-measured here.

| Run | Probe | Mode-sequencer index | Request / report words |
| --- | --- | --- | --- |
| ON, 2 of 2 runs | on, patched once | 32 | `0x032E` / `0x0326` |
| OFF, baseline identity | off | 26 | `0x0323` / `0x0320` |

The independent review re-checked the claim from the run logs. It upheld the claim with
one correction: the forced write is value-equivalent to the earlier debugger force (it
stores 1), but it is not timing-equivalent. The patch lands at about 1 s of virtual time
and stays in place, while the earlier probe held a 3 s window at index 26. The reviewer
also noted that the 16-bit write uses host byte order, which is correct on x86 hosts; a
portable little-endian conversion would be more robust.

The pull request does not report a before/after coverage table for this change. Its
"before" measures (final state, distinct unmodelled device addresses, read and access
counts, wall time, host load) are all recorded there as "not recorded". No score
file exists for this change.

## Build and commands

- Build: QEMU (version not recorded) with this repository's `qemu-a6400` overlay.
- Boot options on both sides: `none`, `-icount shift=0,sleep=on`.
- Unit tests, as reported: `python3 -m unittest discover -s tests -q` reports 1343 tests,
  120 errors and 103 skipped. The same 120 ids also fail on upstream main in that
  environment, mostly from missing optional dependencies. No new failures are reported.
- `python3 -m tools.research.render_research_status --check` passed, as reported.
- Firmware-gated tests skip without the captured inputs and are not claimed as validated.

## Limitations

- Emulator only. The change was not verified on a physical camera.
- The captured inputs needed to reproduce the run states are not in the repository, and
  the pull request does not include them.
- The forced state changes the final boot state. Review the behaviour change, not only
  the diff.
- The probe is a declared stimulus. Its results are not firmware behaviour and do not
  advance the boot-progress record by themselves. The scorer must be run with the
  declared condition allowed before any score for it is recorded.
