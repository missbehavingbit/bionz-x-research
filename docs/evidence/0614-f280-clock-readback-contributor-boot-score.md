# E0614 — Contributor boot score for the 0xF280 clock read-back window

Date: 2026-11-19. Recorded from the comment posted on merged pull request
[#35](https://github.com/ivica00/bionz-x-research/pull/35) after merge
(`a4e464637756d9976a77ff42a7486a60d82cee59`, 2026-11-18T18:45:10Z). The
comment itself is 2026-11-18T22:19:04Z, author `alexscemo`. This workspace
did not re-run the boot scorer and did not access a physical camera.

## Result

The contributor reports that mapping `cxd90014-regfile` over
`0xF2800000–0xF2807FFF` (32 KiB, priority -500, name in the merged machine
`cxd90045.clkgen-f28`) keeps the same final av-cam mode-sequencer state and
the same published `[VPH]` prefix, and drops the distinct unmodelled pair
count from 3065 to 2855.

| Run | Final state | `[VPH]` prefix | Unmodelled pairs | `t_reach` (s) |
| --- | --- | --- | --- | --- |
| Before (TB_JMP_CACHE_BITS=14 plus the SD guard) | 26/43, step `0x8004`, module bit 2 request `0x0323` / report `0x0320` | `1cb47093...8243` | 3065 | 32.58 |
| With #35 | same | same | **2855** | 32.11 / 32.03 |

Reported `qemu-system-arm` sha256 with the change:
`15728ed6d3c29a2d1c7f4e8a99b3c24a09e4a74cac3da537689fc649b625147b`.
The build named in the comment is QEMU 10.1.0, before the v11 move.
The full `[VPH]` digest was published only as that prefix.

## Why the contributor treats read-back as safe for this window

Independent review of the access logs, as posted:

- 110 distinct offsets and 210 `(kind, offset)` pairs. Access counts were
  43,937 before and 41,425 after. The pair set was identical in both runs.
- 29,384 reads with the model. Every read returned the guest's own last
  write to that offset (0 mismatches). 219 reads hit a word before its first
  write and returned 0, as before.
- Longest run of consecutive reads of one offset: 4. No poll was claimed.
- The four clock dividers `0xF28040C4`, `0xF28041A8`, `0xF28050B8` and
  `0xF280518C` show the read-modify-write documented in
  `docs/architecture/cxd90045-sugilite-cross-gen-parity.md`
  (`(val & ~0xF) | 6`): read 0, write `0x6`.
- The only write-stream difference called out is `0xF28040DC` /
  `0xF28040E0`, where the guest's own `0x76` field (written eight times)
  survives a later pointer write. That is the expected effect of read-back.
- One `cxd90014-regfile` at `0xF2800000`, size `0x8000`, priority -500.
  The comment states no other overlay device covers the window, so the
  mapping only replaces the -1000 catch-all.

## Validation reported on the PR branch

`python3 -m unittest discover -s tests -q`: 1242 tests, the same 113
environment errors with and without the change. `python -m tools.research.ci_check`
on the PR branch at comment time: 5/5 PASS. Those counts belong to the
contributor's environment.

## Limitations

- The "clock generator" label is documented for 4 of the 110 offsets. The
  rest of the window is unidentified. A real status or self-clearing
  register would be modelled as plain storage.
- Emulator only. The captured boot manifest and sub-micro page named below
  are not in this repository. Nothing in the comment was checked on a
  physical camera, and this record does not repeat the measurement.

## How the contributor measured it

Deterministic local boot scorer on `qemu-a6400/run.sh`, default
`-icount shift=0,sleep=on`, environment
`EVSEL_LATE_US=500 CAMPIG_ENABLE_READBACK=1 SGC1_EVENT_A=1`, no
`LIVEVIEW_SOURCE`. The scorer is not part of the pull request. It records
the av-cam mode table (43 steps), host-wall `t_reach`, distinct unmodelled
`(read|write, offset)` pairs from `-d unimp`, and the 640×480 `[VPH]` frame
hash. Input identity: boot manifest sha256
`c575bd8e6b04e1da92f175098e43ef90b9023616632d25d7b42e57de62a1613c`,
sub-micro page
`14fba4e607b0efdb76ced74e0463355a279146c96bbba0abec89776b8972fb54`.

Acceptance rule stated with the comment: two quiet-host runs (no other
QEMU, one-minute load below half the CPUs) must reach the same final state
and frame hash, and either the slower run must be at least 10% faster or
the unmodelled pair count must drop. A second review then tries to refute
the claim from the logs and the source. Host: AMD Ryzen 7 5800X (8C/16T),
WSL2, Linux 6.18.
