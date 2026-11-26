# E0617 — Contributor boot score for the v11 per-block SD insertion check

Date: 2026-11-19. Recorded from the comment on pull request
[#39](https://github.com/ivica00/bionz-x-research/pull/39), posted
2026-11-18T22:19:09Z by `alexscemo`. The pull request merged as
`80e66eac880d78c970368763ea59378a0db330ce` (2026-11-19T02:45:40Z). The
pull-request description was cut off after "Red team upheld (old/new patch
trees"; the comment is the full posted evidence. This workspace did not
re-run the boot scorer or `perf`, and did not access a physical camera.

## Result

QEMU 11.1.3 `sd_read_data()` / `sd_write_data()` called `blk_is_inserted()`
on every byte. The merged patch checks insertion when `data_offset == 0`,
at transfer start and at each block boundary. The posted runs keep the same
final state, `[VPH]` prefix and unmodelled pair count. Time to the final
state drops about 13%.

| Run | Final state | `[VPH]` prefix | Unmodelled pairs | `t_reach` (s) |
| --- | --- | --- | --- | --- |
| v11 before | 26/43, step `0x8004`, module bit 2 `0x0323` / `0x0320` | `1cb47093...8243` | 2322 | 42.75 / 42.17 |
| v11 with #39 | same | same | 2322 | **37.04 / 37.03** |

A first pair of runs gave 37.12 / 37.02 s. Reported `qemu-system-arm`
sha256 with the change:
`f9668d0ea714b006cc3b1927a0f4a918e0808bdca547a221d4501cb3ba771051`.

## Cause, as profiled by the contributor

Sum of the insertion-check path (`blk_is_inserted`, `blk_co_is_inserted*`,
`bdrv_co_is_inserted`, `qemu_get_current_aio_context`,
`qemu_aio_coroutine_enter`, `coroutine_trampoline`, `bdrv_poll_co` and the
coroutine/aio helpers), from `perf record` on 45 s boots:

| Build | Share of `qemu-system-arm` CPU |
| --- | --- |
| v10.1.0 (already had the `data_offset == 0` guard) | 0.00% |
| v11.1.3 before | **24.09%** (`bdrv_co_is_inserted` 3.26%, `qemu_get_current_aio_context` 3.18%, `qemu_aio_coroutine_enter` 2.82%, `coroutine_trampoline` 2.14%, `bdrv_poll_co` 1.54%) |
| v11.1.3 with #39 | 0.08% (`bdrv_co_is_inserted` only, once per block) |

Call chain in the v11 source: `sd_read_data` / `sd_write_data` →
`blk_is_inserted` → generated co_wrapper in `block/block-gen.c`
(`qemu_coroutine_create` + `bdrv_poll_co`) → `blk_co_is_inserted` →
`bdrv_co_is_inserted`. CMD18 and CMD25 set `length = 1`, and
`sdbus_read_data` / `sdbus_write_data` loop until the 512-byte block is
done, so before the change the check ran once per byte. In v11,
`blk_is_inserted()` is a `co_wrapper` that creates a coroutine and polls
(`bdrv_co_is_inserted`, `qemu_get_current_aio_context`,
`qemu_aio_coroutine_enter`). On the v10 build the same guard (called L0004
in the comment) was already present and that path was 0.00%.

## Why the contributor treats the guard as equivalent

- Applying the old and the new patch to clean v11.1.3 trees and diffing
  gives exactly two changed lines, `hw/sd/sd.c` 2675 and 2823:
  `if (!sd->blk || !blk_is_inserted(sd->blk))` becomes
  `if (!sd->blk || (sd->data_offset == 0 && !blk_is_inserted(sd->blk)))`.
- `data_offset` is 0 at every transfer start (`sd_cmd_to_receivingdata`,
  `sd_cmd_to_sendingdata`, CMD18, CMD25, invalid-transition reset) and at
  every block boundary in CMD18/CMD25, so a removed card is still caught on
  the first byte of the next block. CMD12 leaves `data_offset` unchanged
  and only returns to transfer state.
- On this machine the eMMC is created from `drive_get(IF_SD, 0, 0)` and
  attached at realize. Nothing in the posted run ejects it.
- `Brought up 4 CPUs` on both sides. The 10 `[VPH]` lines are identical.
  eMMC sequence lines 1843 versus 1842 (one early
  `execute_tuning ... non-HS200` warning fewer, described as a
  timeout-cutoff effect).

## Validation reported on the PR branch

- `python -m tools.research.ci_check`: 5/5 PASS (py_compile 762 files,
  binary/size guard 1886 files, markdown links 577, status SVG sync, 86
  guardrail tests).
- `python3 -m unittest discover -s tests -q`: 1312 tests, 120 errors, 88
  skipped. The same 120 test ids fail on upstream main `3ac63b6` in that
  environment. Firmware-gated tests skip and are not claimed.

## Limitations

Emulator speed only. No guest-visible change was intended and none was
observed in the posted logs. The guard is the one v10 already had. A card
removed in the middle of a 512-byte block is not re-checked until the next
block boundary. This record does not repeat the measurement and claims no
physical camera result. Captured mode step 26 remains open.

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
