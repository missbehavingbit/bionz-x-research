# E0641 — Opt-in work DMA copy model at 0xF0102000

Date: 2026-11-19. Offline only: QEMU model, qtest, paired bounded stock-firmware boots.
No camera access.

## Why

E0640 found that av-cam `0x635DC8B8`–`0x635DC93E` copies 52 chunks through the work
DMA engine. With the engine unmodelled, the busy read returns 0 and **no data moves**,
so guest memory after that routine differs from the original firmware's.

## What is copied [C/I]

- The source `0x044D3380` is inside the preloaded `av-cam.bin` (SHA-256 `90d515cf…125e`,
  PA `0x035C6000`–`0x045ACE98`), at file offset `0xF0D380`. It starts with an ARM
  exception vector table (`e59ff018` ×5, `e1a00000`, `e51ff120`, …) [C], so it is an
  embedded ARM program image.
- The destination `0x045ACF00` is `0x68` bytes past the end of the image. The total is
  51 × 16 KiB + `0xB99` words = `0xCEE64` bytes (847,460).
- The routine sits between writes to the `0xF30A0000` block and a poll of `0xF30A1284`.
  This looks like loading firmware for another on-chip core [I]; that core is not
  modelled and its identity is not established.

## Model

`qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` (SHA-256
`08f7d2c1f201700fc0fedd665aa48b5462daddbed9e8b0246cfe3c2aa75a4de5`): machine property
`work-dma` (default **off**; `run.sh` `WORK_DMA=1`). It maps a 4 KiB region at
`0xF0102000` (priority 0, above the unimplemented catch-all).

- Registers read back what was written; `+0x01C` bit 6 (busy) always reads 0, because the
  copy is synchronous. Completion timing is not modelled.
- A start (`+0x1D0` bit 0) copies `count` 32-bit words from `+0x1C0` to `+0x1C4` through
  the system address space, **only** if all of these hold: the engine is enabled
  (`+0x030` bit 0), the config upper bits equal `0x1C491000`, and both addresses are
  word-aligned. `count` = `+0x1CC` bits [11:0], with 0 = 4096 [I: av-cam writes
  `0x1C491000` for each 16 KiB chunk and `0x1C491000 | rest` for the last; both branches
  agree only with this reading].
- Anything else is refused: another mode, a start while disabled, or a start at another
  `+0x40` block offset `+0x10`. The refusal is logged under `-d unimp` and nothing is
  copied, so the guest never sees fabricated data for an unknown mode.
- Copies are logged under `-d unimp` (first chunk and partial chunks).
- Plain word copy is an assumption [I] supported by the source content; no transform has
  been ruled out on hardware.

## Verification

`tests/test_qemu_work_dma.py` (`e07623e6…6c44`), WSL qtest:

- A full chunk (config `0x1C491000`) copies exactly 4096 words, and the word after it
  stays 0.
- A 3-word remainder continues correctly.
- A different mode, a disabled engine and a start on another block copy nothing.
- The default machine keeps the region unmodelled.

All QEMU qtests: 21 OK. Binary `qemu-system-arm` SHA-256 `b5dbec91…2bb4ef`.

Paired stock boots, 150 s, `-icount shift=0,sleep=on -d unimp,plugin`, handover guard
with DMA contracts, outer `timeout --preserve-status` (both QEMU exit 0):

| Run | Copies | Console lines | Unique console lines | Distinct unmodelled pairs | G10 work DMA |
| --- | --- | --- | --- | --- | --- |
| `e0641-baseline-1` | none (unmodelled) | 1,153 | — | 96 | en stopped, ch6 idle confirmed |
| `e0641-workdma-1` (`WORK_DMA=1`) | 52, `0x044D3380→0x045ACF00` … `0x0459F380→0x04678F00`, total 847,460 B, 0 refused | 1,149 | identical set to baseline | 88 | same |

The two runs print the same set of unique console lines; the line-count difference is
repeated tuning warnings. In this bounded boot nothing observable consumes the copied
program, which fits it being code for a core QEMU does not run. The model makes the
memory state match the original routine's intent; it does not change the boot stage.

Receipts: `artifacts/qemu-a6400/e0641-{baseline,workdma}-1/` (console, stderr,
`qemu.log.gz`, filtered `work-dma.log`, `SHA256SUMS`).

## Limits

- `+0x1CC` upper bits, `+0x008`/`+0x00C`/`+0x1C8` roles, interrupt wiring and timing are
  unknown; only the one observed configuration runs.
- Not checked on hardware. A read-only check would compare one 4 KiB page at the
  destination and at the source on a running camera; that needs the camera connected
  and is within the standing read-only authorization, but was not done here.

## Next boundary

Identify the consumer of `0x045ACF00` (the `0xF30A0000` block and the core it starts),
which decides whether that code ever needs to run in emulation.
