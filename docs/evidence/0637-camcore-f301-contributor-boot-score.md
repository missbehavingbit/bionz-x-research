# E0637 — Contributor boot score for the CamCore 0xF3010000 register model

Date: 2026-11-19. Recorded from the description of merged pull request
[#42](https://github.com/ivica00/bionz-x-research/pull/42)
(`0419ce44e7177239f97f544965b7ef8bba0579b1`, 2026-11-19T06:52:09Z), author
`alexscemo`. This workspace did not re-run the boot scorer and did not
access a physical camera.

## Result

The merged machine maps `cxd90045-camcore` over the 64 KiB block at
`0xF3010000`, priority -500, name `cxd90045.camcore-f301`. It is on by
default. When the opt-in live-view pipeline is enabled, CamPig at
`0xF3000000` (128 KiB, priority -400) still covers this window. The posted
score did not set `LIVEVIEW_SOURCE`.

The contributor reports the same final mode-sequencer state and a drop in
distinct unmodelled addresses from 2,322 to 1,310. Time to that state does
not improve. No `[VPH]` digest was published with this pull request.

| Measure | Before | After |
| --- | --- | --- |
| Final state | step 26 of 43, value `0x8004`; module bit 2 request `0x0323` / report `0x0320` | same |
| Distinct unmodelled addresses | 2,322 | **1,310** |
| of which reads | 291 | 263 |
| Accesses to unmodelled space | 982,160 | 598,402 |
| `t_reach` (two runs) | 37.04 s / 37.03 s | 37.25 s / 37.02 s |

Build: QEMU v11.1.3 with this repository's `qemu-a6400` overlay. Scorer
version 3. Boot options on both sides: `EVSEL_LATE_US=500
CAMPIG_ENABLE_READBACK=1 SGC1_EVENT_A=1`, `-icount shift=0,sleep=on`. Host
busy during measurement: no.

## Register rules, as posted

The baseline log cited by the change (`loop/runs/L0016/run1/qemu-d.log`)
has 1,012 distinct `(kind, offset)` pairs and 219,810 accesses in this
block, but only 28 read offsets. The other 984 offsets are write-only.
The device stores every write. The 28 reads are named rules in
`qemu-a6400/qemu-overlay/hw/misc/cxd90045_camcore.c`:

- 16 read-modify-write words read back the last write. Each posted PC is
  an `ldr` / `bfi` / `str` on the same word: `0x4B00`, `0x4B04`, `0x5A04`,
  `0x5A14`, `0x5A18`, `0x5A1C`, `0x5A28`, `0x5A2C`, `0x5A30`, `0x5A48`,
  `0x5A4C`, `0x5AC8`, `0x5ACC`, `0x5AD0`, `0x5AD4`, `0x5E00`.
- `0x5020` reads back the guest's own write `0x01030001`. The posted nibble
  getters are `0x63CD0A1C` and `0x63CD0A28`.
- 11 never-written status words stay 0, which is the previous catch-all
  result: `0x5600`, `0x5604`, `0x5B80`, `0x5B84`, `0x5B8C`, `0x5B90`,
  `0x5B94`, `0x5B98`, `0x5B9C`, `0x5BA0`, `0x5BA4`. The posted getter masks
  are in the device table. Returning 0 does not invent a statistics value.
- An unlisted read returns 0 and is logged with `LOG_UNIMP`. The `trace`
  property logs every access and defaults off.

The posted access-stream comparison against the stack without this commit:
0 structural divergences over 162,346 accesses. Write values differ only
where read-back keeps the guest's own earlier field bits (7,178 writes on
14 words) and after `0x5020` returns the guest's `0x01030001` (3,472 writes
on 14 offsets). The independent review upheld that split and said 16/16
read-modify-write PCs are `ldr`/`bfi`/`str`. The block is the av-cam window
that [E0564](0564-qemu-ydet-window-configuration-reaches-camcore.md) reaches.

## Validation reported on the PR branch

`python3 -m unittest discover -s tests -q`: 1323 tests, 120 errors, 88
skipped. The same 120 ids fail on upstream main `24068f1` in that
environment. `python3 -m tools.research.render_research_status --check`
passed. Firmware-gated tests skip without the captured inputs and are not
claimed. GitHub CI on the pull request was green before merge, including
CI Summary.

## Limitations

- Emulator only. The captured inputs required to reproduce the counts are
  not in the repository. This record does not repeat the measurement and
  claims no physical camera result.
- The firmware still stops at step 26. Coverage changed; boot progress did
  not.
- The model is not opt-in. A self-clearing or counting status word among
  the 16 read-back registers would be stored as plain memory. The posted
  logs are the evidence that those reads are the guest's own last write.
- The 7,178 differing writes are described as 14 words, while the rule
  table names 16 read-modify-write words. Both figures are the contributor's.
  This record does not resolve that difference from a new log.
