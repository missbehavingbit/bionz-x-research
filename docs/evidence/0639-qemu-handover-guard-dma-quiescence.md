# E0639 — QEMU handover guard: DMA quiescence contracts (Custom OS prerequisite 2, DMA half)

Date: 2026-11-19. Offline only: QEMU TCG plugin, constructed bare-metal fixtures,
bounded stock-firmware boots. No camera access.

## Why

Prerequisite 2 (local roadmap "Custom OS prerequisites") asks to identify the active
DMA engines and to flag unquiesced DMA before a jump to a new kernel. A DMA engine left
running keeps writing into RAM that the new kernel already owns. TCG would not show that
as a failure at the jump. E0638 covered the secondary-core half (G9).

## Change

`qemu-a6400/tools/handover_guard.c` (SHA-256
`dd2791676a2911c37379bd9e4b049d41dbeebbf1ca035f46ca3de46502ddc63c`; supersedes the
E0638 file hash `d1ad7498…`) gains `dma=FILE` and item **G10**. With a contract file
it adds a memory callback per instruction, keeps only IO (MMIO) accesses
(`qemu_plugin_hwaddr_is_io`), and records guest-physical store and load values.

| Kind | Meaning at the handover entry |
| --- | --- |
| `run NAME ADDR MASK [STATUS SMASK IDLE]` | FAIL if the last store to ADDR left MASK bits set. If the bits were set and later cleared, a load of STATUS with `(v & SMASK) == IDLE` must follow the clearing store. Stores that never set MASK count as "never started" |
| `region NAME START END STATUS SMASK IDLE` | any store in `[START, END)` needs a later idle load of STATUS. This is the conservative form for engines whose start register is not known, so a start through an unexpected register cannot pass |
| `observe NAME START END` | no stop contract known: any store is UNVERIFIED, which fails while `strict_unknown=1` (default) |

Without `dma=`, the report says `G10 not configured` and keeps "DMA quiescence" in its
not-checked list. When the entry is never reached, the exit report prints the contract
state marked "informational, not a handover verdict".

### Contracts (`qemu-a6400/tools/handover_dma_contracts.txt`, `a37303a1…55eb0`, 39 lines)

| Engine | Contract | Source |
| --- | --- | --- |
| USB DWC3 | `run` DCTL `0xF020C704` bit 31, then DSTS `0xF020C70C` bit 22 (DEVCTRLHLT) = 1 | [C] Synopsys DWC3 / Linux `dwc3_gadget_run_stop`; base from E0590/E0597 |
| PL330 `0xF0030000` | `region` + DBGSTATUS `+0xD00` bit 0 = 0 and CSR0–3 `+0x100+8n` [3:0] = 0 | [C] ARM PL330 TRM (only when `pl330-dma=on`) |
| VPH layers | `run` `0xF3440144` bits 0–1, `0xF3440184` bit 0 | [I] a5000 RE, `cxd90014_vph.c` header |
| VPH AIF ch51–58 | `region` per 0x80 block at `0xF3441200`, `+0x00` bit 0 = 0 | [I] a5000 RE (start/busy cleared at frame end) |
| Sensor/ISP AIF (24 channels) | `region` per channel, `+0x00` bit 0 = 0 | [I] E0622 av-cam table `0x63E23764` |
| Work DMA `0xF0102000` | `observe` | [?] E0525 access map only |

## Results

Same QEMU binary as E0638 (`7a8caef8…68b70f`).

Fixtures (`handover_payload.S`, `83319592…423da`), each with
`-plugin handover_guard.so,entry=0x600000,exit=1,dma=contracts.txt`:

| Variant | Machine | Exit | G10 |
| --- | --- | --- | --- |
| GOOD | default | 0 | ok, dwc3 never programmed |
| DWC3 (RUN, then stop and poll DEVCTRLHLT) | `dwc3-gadget=on` | 0 | ok, "stopped, idle confirmed" (DSTS `0x00400000`) |
| DWC3 + DWC3_KEEP_RUNNING | `dwc3-gadget=on` | 3 | BAD "STILL RUNNING" (DCTL `0x80000000`) |
| DWC3 + DWC3_NO_POLL | `dwc3-gadget=on` | 3 | BAD "stop NOT confirmed by an idle status read" |
| DWC3_SOFTRESET (DCTL `0x40000000` only) | `dwc3-gadget=on` | 0 | ok, "never started" |
| WORK_DMA | default | 3 | BAD "UNVERIFIED (no stop contract)" |

`tests/test_qemu_handover_guard.py` (`e3fd2792…3d3db`) asserts these cases, the
unconfigured report, and the ten E0638 cache/MMU variants against the new plugin version:
4 tests OK. All QEMU qtests in WSL: 18 OK.

### Stock firmware inventory [C, bounded]

`run.sh`, `-icount shift=0,sleep=on`, guard with an unreachable entry:

| Run | Result |
| --- | --- |
| `e0639-dma-quiesce-2/firmware-dwc3` (`DWC3_GADGET=1`, `TIMEOUT=120`) | report present; only two engines programmed |
| `firmware-default-repro120` (default machine, outer `timeout --preserve-status` 120 s, QEMU exit 0) | same two engines |

The two engines:

- `dwc3-run`: one store, DCTL = `0x40000000` (CSFTRST). RUN is never set → "never started".
  This holds even on the default machine, where `0xF020C000` falls into the unimplemented
  catch-all.
- `work-dma`: 213 stores, no stop contract → UNVERIFIED.

No VPH or sensor AIF channel is programmed in this bounded boot. The console reports
`[VPH] YUV layer still off`, consistent with E0525's power-off factor 7 boundary.
So for the current emulated boot stage, the work DMA engine is the only engine whose
quiescence cannot be judged. Its register contract is the next unknown.

## Failures preserved

- `artifacts/qemu-a6400/e0639-dma-quiesce-1/`: first collection, before the `run`
  semantics fix. It reported the stock DWC3 soft reset as "stop NOT confirmed", a false
  BAD, because any store was treated as a stop. Fixed with `ever_active`/`stop_seq`.
- In both `-1` and `-2`, `firmware-default-guard.log` stops after the 2 init lines with
  no exit report (QEMU killed by `run.sh`'s internal `timeout 120`, exit 124 masks the
  real status). A direct reproduction (same mode, 120 s, status preserved) exited 0 and
  printed the full report. Cause not established; a guard verdict must not rely on the
  exit report. The handover verdict itself is printed at the entry, not at exit.
- `e0639-dma-quiesce-1` and the first repro attempt also hit WSL `/tmp` being cleared
  between invocations (plugin missing); rebuilt and rerun.

## Limits

- `[I]` contracts are inferred from a5000 work and av-cam tables, not hardware-checked.
- The guard sees only CPU-issued MMIO; it does not observe DMA bus traffic. An engine
  started by firmware outside QEMU (bootloader-left state) is invisible until the guest
  touches it.
- Engines not in the file are not checked, and the report says so.

## Next boundary

Identify the work DMA engine at `0xF0102000` (start/stop/status words) from av-cam or
kernel code, so it can move from `observe` to `run`/`region`. Then extend the inventory
to later boot stages (live view, recording) once those are reachable in QEMU.
