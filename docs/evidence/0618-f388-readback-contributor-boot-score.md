# E0618 — Contributor boot score for the 0xF388 read-back window

Date: 2026-11-19. Recorded from the comment on pull request
[#40](https://github.com/ivica00/bionz-x-research/pull/40), posted
2026-11-18T22:19:11Z by `alexscemo`
([comment](https://github.com/ivica00/bionz-x-research/pull/40#issuecomment-6993282264)).
The branch was updated onto main after #39
(`50594ffd50ef0e2964693d749a5bc87eeded82ea`) and merged as
`2ce3db0c57cd98fb92d9b558000db032a5298a92` (2026-11-19T02:47:35Z). This
workspace did not re-run the boot scorer and did not access a physical
camera.

## Result

The change maps `cxd90014-regfile` over the unidentified 64 KiB block at
`0xF3880000` (priority -500, name `cxd90045.f388-cfg`). The contributor
reports the same final state and `[VPH]` prefix, and a drop in unmodelled
pairs from 2416 to 2322. Those 94 removed pairs are exactly this window.
Zero pairs were added. The same window is part of the v11 baseline used
for #39, which also reports 2322 pairs
([E0617](0617-v11-sd-insert-check-contributor-boot-score.md)).

| Run | Final state | `[VPH]` prefix | Unmodelled pairs | `t_reach` (s) |
| --- | --- | --- | --- | --- |
| Before (#37 stack) | 26/43, step `0x8004`, module bit 2 `0x0323` / `0x0320` | `1cb47093...8243` | 2416 | 37.31 / 37.21 |
| With #40 | same | same | **2322** | 37.07 / 37.05 |

Two quiet runs each, on the QEMU 10.1.0 build at measurement time. The
window mapping is unchanged on v11. Reported `qemu-system-arm` sha256 with
the change:
`3b4e55542a34a70ead5b702eb03d4c0f8c59529d689e92b0f7b0e06ed652847e`.

## Why the contributor treats read-back as safe for this window

Two independent reviews of the access logs, as posted:

- 236 window accesses, 94 `(kind, offset)` pairs. The ordered
  `(kind, offset)` sequence is identical with and without the model
  (0 divergences).
- 106 reads, 0 mismatches against the guest's own last write. Eleven words
  are read before any write and return 0, as before: `0xF3880010`,
  `0xF3880014`, `0xF3880018`, `0xF388001C`, `0xF3881008`, `0xF3881200`,
  `0xF3881204`, `0xF3881208`, `0xF3881380`, `0xF3881384`, `0xF3881388`.
- No poll: at most 2 consecutive reads of one word (`0xF3881008`). Nine
  repeated reads with no write in between return the unchanged value. The
  same sequence also reaches the same state when every read returns 0.
- Exactly four words are read-modify-written, and each new write is the
  read value OR the old write, so the guest keeps its own earlier bits:
  `0xF3881008` (`0xF` → `0xF00F`), `0xF3881200` (`0x1` → `0x10803`),
  `0xF3881380` (`0x1` → `0x3`), `0xF3882004` (`0x0` → `0xC0000E`).
  No other offset's write changes. A first review refuted an earlier
  wording that claimed only one such word. The claim and the code comment
  were corrected to name all four, and the second review upheld that.
- After each differing read, the next access is identical. Outside the
  window, the deterministic content of the other blocks is unchanged.
- Blocks left unmodelled because read-back would be wrong there:
  `0xF30A0000` (a read of `+0x1280` = `0x21` selects a path),
  `0xF0100000` (work-DMA control/status loops), `0xF2920000` (21-read poll
  at `+0xC160`), and `0xF0300000` (write-verify, overlaps mapped eMMC0).

The machine comment gives one concrete read-modify-write example:
`+0x1200` becomes `0x10802 | 0x1 = 0x10803` once the earlier bits read back.
That matches the `0x1 → 0x10803` row above.

## Validation reported on the PR branch

- `python -m tools.research.ci_check`: 5/5 PASS.
- `python3 -m unittest discover -s tests -q`: no new failing test ids
  against upstream main `3ac63b6` in that environment. The remaining errors
  are missing optional dependencies. The pull-request body reports 1312
  tests, 120 errors and 88 skipped, the same 120 ids as that main.

## Limitations

- The block is unidentified. A self-clearing status or trigger word would
  be modelled as plain storage. The logs show no bit that disappears and is
  then polled. A datasheet would still be required to exclude that.
- eMMC `execute_tuning` warning counts differ slightly (1827/1827 before
  versus 1834/1831 after). Final state and frame are identical, and those
  counts also vary between unchanged runs.
- Emulator only. Inputs named below are not in this repository. This record
  does not repeat the measurement and claims no physical camera result.
  Captured mode step 26 (`0x0323` / `0x0320`) remains the open sequencer
  boundary.

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
