# E0696 — Opt-in entry-force-probe for the av-cam entry dispatch (declared stimulus)

Date: 2026-11-20. Recorded from the description of merged pull request
[#58](https://github.com/ivica00/bionz-x-research/pull/58) (merge commit `a281437`,
2026-11-20T23:24:42+01:00), author `alexscemo`. This workspace did not re-run QEMU or the
boot scorer and did not access a physical camera. The pull request reports no oracle score
for this change: it is a declared stimulus, not a fix, and it was not scored as progress.

## Result

The merge adds a machine property, `entry-force-probe`, and a wrapper option
`ENTRY_FORCE_PROBE=1` that sets it. The property is false unless set, so the change is off by
default. The pull request body also says "No new environment opt-in: the change is active in
the default machine". That sentence contradicts the code and the same body's "default-off"
statement; this record follows the code.

When enabled, a 100 us virtual-time timer reads the camera-control state S at guest physical
address `0x0431B594`. While S is 240 and the four bytes at virtual address `0x6386CDC0` still
hold the original `add.w r3,r0,#-1`, the board rewrites them to `movs r0,#1; movs r3,#0`, so
the first av-cam entry takes the `r0 = 1` path, and it invalidates the translated block. When
S leaves 240, the original bytes are restored and the block is invalidated again.

The declared condition is `cond. PROBE entry-force`. The result is a stimulus result: a run
with the probe on shows the state that the forced write produces. It is not evidence of
firmware behaviour on the unmodified path, and it is not a fix.

## Observed run states, as reported

These are the values the pull request and its internal red-team review report. They are not
re-measured here.

- Force runs: 2 of 2 reach mode-sequencer index 42 (`0x2A`), with S = 0 at the end.
- Control runs with the same build and no probe: 2 of 2 stay at index 26, with S = `0x13`.
- Follow-up: 10 boots, all 10 with the expected outcome: 6 entry-only boots and 2 boots with
  the gate-force probe also on reach index 42 with one apply per boot, and 2 control boots
  without the probe stay at index 26. The pull request's "10/10 boots ... reach index 42"
  counts the two control boots among the ten; 8 boots ran with the probe.

The red-team review upheld the claim with these corrections:

- One-shot is not enforced. The forced bytes persist to the next 100 us tick, and every later
  window with S = 240 applies the patch again.
- First-entry attribution is inferred from the timing, not logged.
- The 100 us poll is a race. Two of two, and ten of ten, are not a measure of reliability.

## Build and commands

- Build: QEMU v11.1.3 built from the pristine source with this repository's `qemu-a6400`
  overlay. The patch is applied on upstream main `67ce031`.
- Force runs: `ENTRY_FORCE_PROBE=1` with `GATE_FORCE_PROBE=0`. Control runs: neither option.
  Two follow-up runs also set `GATE_FORCE_PROBE=1`.
- Boot options beyond these, and the captured inputs used, are not recorded in the sources.
- Unit tests, as reported: `python3 -m unittest discover -s tests -q` ran 1393 tests with
  127 errors and 115 skipped. The pull request states that the 127 failing ids also fail on
  upstream main `67ce031` in that environment.
- `python3 -m tools.research.render_research_status --check` passed, as reported.

## Limitations

- Emulator only. The change was not verified on a physical camera.
- The captured inputs needed to reproduce the run states are not in the repository, and the
  pull request does not include them.
- The forced state changes the final boot state. Review the behaviour change, not only the
  diff.
- The first-entry attribution and the one-shot behaviour are not logged directly; see the
  corrections above.
