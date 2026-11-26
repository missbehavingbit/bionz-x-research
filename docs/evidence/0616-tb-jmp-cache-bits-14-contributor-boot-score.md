# E0616 — Contributor boot score for TB_JMP_CACHE_BITS 14

Date: 2026-11-19. Recorded from the comment posted on merged pull request
[#38](https://github.com/ivica00/bionz-x-research/pull/38) after merge
(`3ac63b67b8ec8e89e777c7de39b279d9b9851423`, 2026-11-18T19:05:59Z). The
comment itself is 2026-11-18T22:19:07Z, author `alexscemo`. This workspace
did not re-run the boot scorer or the host profile, and did not access a
physical camera.

## Result

The merged change raises `TB_JMP_CACHE_BITS` from 12 to 14 in the v11 patch.
The posted timing was measured on the QEMU 10.1.0 build; the same one-line
hunk was then carried into the v11 patch. Final state, `[VPH]` prefix and
unmodelled pair count stay the same. Time to the final state drops about 14%.

| Run | Final state | `[VPH]` prefix | Unmodelled pairs | `t_reach` (s) |
| --- | --- | --- | --- | --- |
| Bits 12 | 26/43, step `0x8004`, module bit 2 `0x0323` / `0x0320` | `1cb47093...8243` | 3065 | 37.30 / 37.06 |
| Bits 14 | same | same | 3065 | **32.07 / 32.04** |

A separate re-measurement before acceptance: 32.58 / 32.03 s.
Reported `qemu-system-arm` sha256:
`b2d4eefd39fbc761aaaaf39ed850e02ca9b0d490ba8394bce6e8ca6a34dd8b89`.

A host profile of the 12-bit build (`perf record --call-graph dwarf`, 22 s
window) had `helper_lookup_tb_ptr` at 6.19% and `qht_lookup_custom` at
5.49% as the two top symbols. 91% of the samples inside `qht_lookup_custom`
came through `helper_lookup_tb_ptr`, and 8% through `cpu_exec_loop`. The
stated reason is that a 4× larger per-vCPU jump cache keeps more hot
translation blocks out of the qht path.

## Why the contributor says guest behaviour cannot change

Source review against the QEMU 10.1.0 tree, as posted:

- A jump-cache hit is re-validated on the full key (pc, cs_base, flags,
  cflags) in `accel/tcg/cpu-exec.c` (lines 239–244), the same key the qht
  path compares (157–165). The store uses the same hash (960–963).
- `TB_JMP_PAGE_BITS` becomes 7 (128-slot page blocks). Page-flush paths
  clear exactly `TB_JMP_PAGE_SIZE` slots (`cputlb.c` 151–164 and 554–555).
  The range flush scales with `TB_JMP_CACHE_SIZE` (744–760). Invalidation
  uses the same hash (`tb-maint.c` 879–897). No stale-entry path was found.
  arm-softmmu's minimum `TARGET_PAGE_BITS` (10) keeps every shift
  non-negative.
- icount consumption is per translation block (`tb->icount`), independent
  of how the block was looked up.
- Cost: 16 bytes × 16384 entries = 256 KiB per vCPU (about 1 MiB for 4
  vCPUs, up from 256 KiB), allocated once with `g_new0`, no migration state.

## Validation and limitations

`python -m tools.research.ci_check` on the PR branch at comment time:
5/5 PASS.

- Whether the removed misses were conflict misses or key mismatches was not
  counted separately. The gain is measured, not explained per miss.
- The v11 tree was not re-measured with and without this hunk alone. The
  comment places it inside the v11 baseline (42.75 s before the SD fix in
  #39, 37.0 s after). See
  [E0617](0617-v11-sd-insert-check-contributor-boot-score.md).
- Emulator speed only. No physical camera check. This record does not
  repeat the profile or the boot score.

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
