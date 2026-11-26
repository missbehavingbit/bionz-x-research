# E0690 — Boot score for the CamCore 0xF3000000 and 0xF3120000 register models

Date: 2026-11-19. Recorded from the description of merged pull request
[#46](https://github.com/ivica00/bionz-x-research/pull/46)
(merge commit `40552a81299a7b1a00a87b888c7db6c2e62c0845`, 2026-11-19T18:47:14+01:00),
head commit `bbd918f`, author `alexscemo`. This workspace did not re-run the boot
scorer and did not access a physical camera. The scores below are the ones the pull
request reports, produced by this project's boot scorer (oracle version 3) on the
pull request branch.

## Result

The merged machine maps `cxd90045-camcore` over two register blocks with per-word
rule tables, at priority -600 so the existing models keep winning: the `0xF3000050`
CamPig enable read-back, the SGC1 words at `0xF3120000` (priority -500) and the opt-in
live-view CamPig (priority -400). The change is on by default; it adds no environment
option.

The rules cover:

- `0xF3000000` block: 23 read-back words and 34 never-written status words that read 0.
- `0xF3120000` block: 5 read-back words and 8 zero words.

A word reads back only when every read of it is followed by a write to the same
register. The offsets `+0x2008`, `+0x2280`, `+0x2400` and `+0x2480` are status reads
and stay 0. The `0xF3010000` rules (see E0637) are unchanged.

The contributor reports the same final mode-sequencer state and a drop in distinct
unmodelled device addresses from 1,310 to 421. Time to that state does not change in
a meaningful way. No `[VPH]` digest is part of this record.

| Measure | Before | After |
| --- | --- | --- |
| Final state | step 26 of 43, value `0x8004`; module bit 2 request `0x0323` / report `0x0320` | same |
| Distinct unmodelled device addresses | 1,310 | **421** |
| of which reads | 263 | 193 |
| Accesses to unmodelled space | 598,402 | 60,053 |
| `t_reach` (two runs) | 37.25 s / 37.02 s | 37.56 s / 37.01 s |
| Host busy during measurement | no | no |

Access-stream comparison against the same boot without this commit: no new unmodelled
address pair; every read-back read equals the guest's own last write (0 of 17,598 and
0 of 1,879 reads differ); every changed write is the base value plus the read-back
bits. The pull request reports that an independent review of the run logs upheld
this split.

Build: QEMU v11.1.3 with this repository's `qemu-a6400` overlay. Scorer version 3.
Boot options on both sides: `EVSEL_LATE_US=500 CAMPIG_ENABLE_READBACK=1
SGC1_EVENT_A=1`, `-icount shift=0,sleep=on`.

The change touches `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` (43 lines added) and
`qemu-a6400/qemu-overlay/hw/misc/cxd90045_camcore.c` (185 lines changed). Register
rules come from the av-cam code that reads each register (70 of 70 reading program
counters checked).

## Verification

- `python3 -m unittest discover -s tests -q` (Linux/WSL, pull request branch): 1338
  tests run, `FAILED (errors=120, skipped=103)`. The pull request reports that the 120
  failing ids also fail on upstream `main` in that environment, mostly because of
  optional dependencies.
- `python3 -m tools.research.render_research_status --check`: passed.
- Firmware-gated tests skip without local captured inputs. Skipped checks are not
  claimed as validated.

## Limitations

- Emulator only. The captured inputs needed to reproduce the counts are not in the
  repository. This record does not repeat the measurement and claims no physical
  camera result.
- The firmware still stops at the same sequencer step (26). Coverage changed; boot
  progress did not.
- The model is not opt-in. A self-clearing or counting status word among the read-back
  registers would be stored as plain memory. The posted logs are the evidence that
  those reads return the guest's own last write.
- The pull request's own safety checklist leaves "new behaviour is opt-in" unchecked,
  consistent with the default-on behaviour recorded here.
