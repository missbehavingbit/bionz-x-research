# E0615 — Contributor boot score for the 0xF350 read-back window

Date: 2026-11-19. Recorded from the comment posted on merged pull request
[#37](https://github.com/ivica00/bionz-x-research/pull/37) after merge
(`a75684f7cbadeab9cf0633605a19860394bef7a6`, 2026-11-18T19:04:16Z). The
comment itself is 2026-11-18T22:19:06Z, author `alexscemo`. This workspace
did not re-run the boot scorer and did not access a physical camera.

## Result

The contributor reports that mapping `cxd90014-regfile` over
`0xF3500000–0xF350FFFF` (64 KiB, priority -500, name `cxd90045.f35-cfg`)
keeps the same final state and `[VPH]` prefix, and drops unmodelled pairs
from 2855 to 2416. The claim is coverage. The posted time increase is
attributed to host drift: a control run of the unchanged baseline in the
same session measured 37.3 s.

| Run | Final state | `[VPH]` prefix | Unmodelled pairs | `t_reach` (s) |
| --- | --- | --- | --- | --- |
| Before (#35 stack) | 26/43, step `0x8004`, module bit 2 `0x0323` / `0x0320` | `1cb47093...8243` | 2855 | 32.11 / 32.03 |
| With #37 | same | same | **2416** | 37.31 / 37.21 |

Reported `qemu-system-arm` sha256 with the change:
`75cffe5e5857cf7180b2d9b28b837bb8c8c9fc742c6d6a1bc15b7f5c808577c8`.
The build named in the comment is QEMU 10.1.0, before the v11 move.

## Why the contributor treats read-back as safe for this window

- 477 accesses (95 reads, 382 writes, 70 read offsets), the same count with
  and without the model.
- With the model, 49 reads return non-zero and each equals the guest's own
  last write (0 mismatches). The other 46 read never-written words and
  return 0.
- At most 2 consecutive reads of one offset (`0xF3501008`). The ordered
  `(kind, offset, write value)` stream for the window is identical with and
  without the model (477/477), so no read value changed a firmware decision
  in that log.
- Removed pairs are exactly the 439 pairs of this window. 0 pairs were added
  elsewhere.
- Same serial milestones: 2079 lines and 1836 eMMC sequence lines on both
  sides. The only differences named are one kernel timestamp and one log
  address.
- The mapping does not overlap the clock block at `0xF3400000–0xF340FFFF`
  or the SA1 block at `0xF35C0000`.

## Validation reported on the PR branch

The unit suite showed the same 113 environment errors with and without the
change. `python -m tools.research.ci_check` on the PR branch at comment
time: 5/5 PASS.

## Limitations

- The block is not identified in the docs. Words written with 1 and read
  back (for example `+0x10..+0x1C = 0x100`) could be self-clearing triggers
  on hardware. The firmware does not poll them in the posted log, and the
  boot takes the same path when those reads return 0. That semantics is
  unverified.
- Emulator only. Inputs named below are not in this repository. This record
  does not repeat the measurement and claims no physical camera result.

## How the contributor measured it

Same scorer and acceptance rule as
[E0614](0614-f280-clock-readback-contributor-boot-score.md):
`qemu-a6400/run.sh`, `-icount shift=0,sleep=on`,
`EVSEL_LATE_US=500 CAMPIG_ENABLE_READBACK=1 SGC1_EVENT_A=1`, no
`LIVEVIEW_SOURCE`. Boot manifest sha256
`c575bd8e6b04e1da92f175098e43ef90b9023616632d25d7b42e57de62a1613c`,
sub-micro page
`14fba4e607b0efdb76ced74e0463355a279146c96bbba0abec89776b8972fb54`.
Host: AMD Ryzen 7 5800X (8C/16T), WSL2, Linux 6.18.
