# E0640 — Work DMA engine at 0xF0102000: original start/stop/busy contract

Date: 2026-11-19. Offline only: QEMU MMIO trace of the stock boot plus static Thumb
disassembly of `av-cam.bin`. No camera access.

## Why

E0639 left the work DMA engine at `0xF0102000` as the only DMA engine in the bounded
stock boot whose quiescence could not be judged (observe-only contract, 213 stores).

## Method

- `handover_guard.c` (SHA-256 `b946990807a5b5da90d59716f7e1122b1e2d9ff1a6c37643afdcc4a9e632ea02`)
  gains `trace=START:END[,trace_limit=N]`. It logs PC, LR, direction, guest-physical
  address and value of each IO access in the range. With `trace=` set, the memory callback
  asks for register access so LR can be read.
- Stock boot, default machine, `-icount shift=0,sleep=on`, outer
  `timeout --preserve-status 60` (QEMU exit 0):
  `trace=0xF0102000:0xF0103000,trace_limit=1000`. 478 accesses.
  Run `-1` had PC only; run `-2` adds LR. Receipts are in
  `artifacts/qemu-a6400/e0640-work-dma-trace-{1,2}/`.
- Static disassembly (capstone 5.0.7, Thumb) of `av-cam.bin`, SHA-256
  `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`, base `0x635C6000`.

## Result [C]

All accesses go through two generic av-cam helpers:

- `0x635DC708` stores `r1` to `[r0]`, then reads the register back into a global (a
  posted-write flush; every programming write is followed by a read of the same word).
- `0x63C01D50` is a plain `ldr r0, [r0]`.

LR places every call in one routine, `0x635DC8B8`–`0x635DC93E`:

| av-cam PC | Access | Meaning |
| --- | --- | --- |
| `0x635DC8B8` | `+0x030 = 1` | engine enable |
| `0x635DC8C0` / `0x635DC8C8` | `+0x008 = 0x40`, `+0x00C = 0x40` | channel bit 6 (mask/clear, role inferred) |
| `0x635DC8CE` | `+0x1C8 = 0` | channel word |
| loop `0x635DC8D8` | `+0x1C0` source, `+0x1C4` destination, `+0x1CC = 0x1C491000 \| words` | chunks of `0x1000` words (16 KiB); the last chunk uses the remainder |
| `0x635DC922` | `+0x1D0 = 1` | channel start |
| `0x635DC92A`–`0x635DC934` | read `+0x01C`, `tst #0x40`, loop while set | wait for channel busy bit 6 to clear |
| `0x635DC93A` | `+0x030 = 0` | engine disable after the last chunk |

Observed run: 52 chunks, source `0x044D3380…`, destination `0x045ACF00…`, both advancing
by `0x4000`. The routine sits between writes to the `0xF30A0000` block (`+0x12BC`,
`+0x12C4`, `+0x1280 = 0x21`) and a poll of `0xF30A1284`. That context is consistent with
E0618's note that `0xF30A1280` selects a path. The engine's purpose beyond this copy is
not established.

## Contract change

`handover_dma_contracts.txt` (`f163b568…5ff7`) replaces `observe work-dma` with:

- `run work-dma-en 0xF0102030 0x1` [C]
- `region work-dma-ch6 0xF01021C0 0xF0102200 0xF010201C 0x40 0x0` [C]
- `observe` for the other channel blocks `0xF0102040–0xF01021BF` and for
  `0xF0102200–0xF0102FFF`. These are not used by this routine; treating them as further
  channels at a 0x40 stride is an inference.

## Verification

- Fixture `handover_payload.S` (`9d71d765…6e30e6`) replays the routine for one 4-word
  copy (`WDMA`). `WDMA_NO_POLL` skips the busy poll and `WDMA_KEEP_ENABLED` skips the
  disable. `tests/test_qemu_handover_guard.py` (`91f624dd…0ddb`):
  - `WDMA` passes ("stopped, idle confirmed").
  - `WDMA_NO_POLL` fails as "stop NOT confirmed".
  - `WDMA_KEEP_ENABLED` fails as "work-dma-en: STILL RUNNING".
  - `WORK_DMA` (a store to an unused channel block) fails as UNVERIFIED.
  - All earlier cases still hold: 4 tests OK.
- Stock boot with the new contracts, 120 s, QEMU exit 0
  (`artifacts/qemu-a6400/e0640-stock-contracts-1/`): `work-dma-en` stopped (2 stores,
  last 0), `work-dma-ch6` stopped and idle confirmed (209 stores, last busy read 0),
  `dwc3-run` never started. Every contracted engine programmed in this boot is quiescent
  by its contract.

## Fidelity gap found

QEMU has no model at `0xF0102000`. The busy read returns 0 from the unimplemented
catch-all, so av-cam believes all 52 copies (about 832 KiB) completed, but **no data is
copied**. The destination `0x045ACF00…` keeps whatever was there. Any later behaviour that
depends on that buffer is not original-firmware behaviour. A copy model would need the
`+0x1CC` field semantics beyond the word count (`0x1C491000` upper bits are unknown) and
the completion timing.

## Limits

- The idle verdict is only as good as the emulated status: in QEMU the busy bit is a
  constant 0, so the guard confirms the guest *checked*, not that hardware was idle.
- Roles of `+0x008`, `+0x00C`, `+0x1C8` and the `+0x1CC` upper bits are not established.
- One routine in one bounded boot; other av-cam users of this engine (later boot stages)
  may use other channels.

## Next boundary

Model the `+0x1C0` channel as an opt-in copy engine once the `+0x1CC` fields are decoded,
or capture its effect with a read-only camera observation. Then repeat the DMA inventory
at later boot stages.
