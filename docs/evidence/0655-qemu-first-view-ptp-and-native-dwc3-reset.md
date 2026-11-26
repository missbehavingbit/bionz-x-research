# E0655 - Original FirstView USB checks, bulk OUT, and the DWC3 reset entry

Date: 2026-11-19. Offline ILCE-6400 follow-up to
[E0654](0654-usb-stock-attach-and-fusb-start-guard.md).
No physical USB, camera, media, module load, or streaming.

## What was and was not verified

| Gate | Result |
| --- | --- |
| Stock cable before FirstView | Original jack handler sees attach. The first-view readiness helpers return true, and the native USB status accessor returns 1. No observed stock gadget start, DWC3 RUN, or DEVICE response. |
| Virtual bulk OUT and PTP verification | Native qtest passes strict bulk OUT, and host tests require a Sony `054C:0CAA` still-image interface plus paired `GetDeviceInfo` data and `0x2001` response. A constructed transfer is not firmware data. The real Sony transfer was not reached. |
| Unicorn original gadget start | E305 follows the actual bound DWC3 callback into core reset and reaches the original PL310 cache sync store. No cache sync or GCTL write is supplied; E305 is unanswered. |
| Original-process SETCONFIG and teardown | Not verified. `created=0`; no configuration event or teardown acknowledgement is fabricated. |

## QEMU: earlier cable timing does not clear the stock startup boundary

The [host probe](../../tools/research/qemu_usb_host_probe.py) waits for
the identity-checked boot-time `SetJackStatus(1,0)` before asserting VBUS.
This tests an edge after original jack initialization but before the
first-view checks, rather than a cable pulse before the IRQ was enabled
or after the view had settled. The prepared boot manifest, kernel,
captured libraries, powered sub-micro replay and read-only eMMC base
retain E0654's hashes. No application decision or configuration was
written by the host.

The final early replay observes the actual `SetJackStatus(1,1)` before
the first-view checks. `viewUnified2.so+0x2AC03A` returns 1.
The helper's native `model/ROOT` parameter `0x102` value at
`+0x2B2704` is 2 and its return at `+0x2B2712` is 1. The native
USB status accessor at `+0x2B278E` returns value 1 twice. This **rules
out a missed early cable edge or a failing sampled readiness check**
as the explanation for that run. It does not show the view has
completed all initialization. Neither `ActionOpenUSB` nor
`UsbIfWrapper::StartGadget` is observed, UDC is `not attached` at
`UNKNOWN` speed, and the host gets only `NAK 0`. The paired sensor-bars
control reaches the same boundary. The bars are a sensor input fixture,
not USB output.

The native `NeutrOff` writer from E0654 has two direct routes in the
captured original firmware: the `TBH` at `libObj+0x4E61B8` selects
`0x4E65CC` for selector `0x60` and `0x4E671C` for selector `0x87`.
The former tail-calls the clear routine; the latter clears then calls
the readiness wrapper. These are dispatcher selectors, not proven
hardware events or instructions to inject messages. In
`usb-stock-13a3-playback-05`, an ordinary rear Play press selects
`0x87`, executes the zero store at `+0x4AA194`, and makes the original
readiness helper request `true` for the same object. The display says
`No memory card. Cannot play.` This is not a successful media playback
test. Subsequent VBUS still yields no original release helper, no
`ActionOpenUSB`, no DWC3 RUN and only `NAK 0`. Therefore FALSE readiness
is a real blocker in the shooting baseline, but clearing it in the
playback path is not by itself sufficient to open USB.

The repeat `usb-stock-13a3-playback-dispatch-07` filters the original
I-InputMain stack word at `[r7+0xDC]` to physical key `0x07000001` before
counting hits. It again observes the Play-driven `0x87` path, a zero
store and the same object's ready request. Afterward both USB edges
reach original `libObj+0x8622C6` with `r6=0` and reason `r3=0x11`;
the callback queue send and original inhibit release have no qualified
hits. Normal half-press after Play in `usb-stock-13a3-resume-shooting-06`
also yields no USB start; that run does not show a stable resumed
shooting view. These runs separate restored `ModelCamera` readiness
from the still-inhibited USB input subscription.

QEMU results are under `artifacts/qemu-a6400/`: early control
`usb-stock-13a3-first-view-01`; bulk-enabled stock attempts
`usb-stock-13a3-first-view-ptp-02`, `usb-stock-13a3-first-view-bars-03`
and `usb-stock-13a3-first-view-final-04`. Final stock replay quits its
owned QEMU with code 0; the evidence probe exits 2 because no Sony
transaction completed. The passive first-view plugin hash is
`874aab0cb7ffb26b633293018c7f796d314e93ae04a455e000d24085bb321a4f`.
The post-Play USB filter plugin is
`be0de707e1d6bbb7f1da0cf535e0b7fa21e569cfcef312d4e99cbd12eaf183d3`.

## Bounded bulk OUT and the stock PTP success gate

Only the existing opt-in `strict-ep0` plus `strict-bulk` policies activate
the new OUT behavior. The controller resolves an enabled physical
endpoint from the original endpoint configuration's logical address; it
requires a bulk pipe and an owned, single normal last TRB. In the captured
kernel's kallsyms, `dwc3_prepare_one_trb`
(`0x602AEBC0`) writes the TRB and `dwc3_gadget_giveback` (`0x602AFFB8`)
retires ownership. The model updates only the admitted payload and TRB,
preserves the residual length, and completes on the physical endpoint.
It refuses chains, out-of-range DMA, unsupported packet lengths and an
ambiguous endpoint. NAK is retried and does not queue a future delivery.

The separate host read requires an original configured UDC, mapped
`libInfraRemote.so`, the stock PC Remote device and still-image
descriptors from [E0258](0258-offline-descriptor-diff-pc-remote-vs-composite.md), a bulk command,
validated Sony ILCE-6400 `GetDeviceInfo` data, and the success response
for the same transaction. The HID product or a data reply without a
valid response cannot pass. It never requests liveview or streaming.

Isolated QEMU build and native tests (WSL, repository root):

```bash
python3 -B -m tools.research.qemu_dwc3_profile_check \
  --qemu-dir "$QUALIFIED_QEMU_WITH_DWC3_OBJECT" \
  --report-dir artifacts/qemu-a6400/NEW-BULK
python3 -B -m tools.research.qemu_ep0_contract_check \
  --qemu-dir artifacts/qemu-a6400/NEW-BULK --strict-bulk-out \
  --report-dir artifacts/tests/NEW-TOKENS
```

Binary `44ddcc59abe9742d6ed24559256a20773b01f564b2d13f2744f24e38a8dc2c93`;
build receipt `usb-13a3-strict-bulk-out-build-02`. An earlier relink
refused a pre-DWC3 build with no matching object; no upstream checkout
was modified. **44** native identity, **51** native token, and **33**
legacy EP0 contract checks pass. The latter uses strict EP0 with the
bulk feature off. Reproduce the stock attempt with E0654's launcher and
site generator, this binary, and `--stock-attach before-first-view
--stock-ptp-info --capture-dwc3-context --seconds 280`. The target is
`0x1001` only after original USB startup, not the diagnostic HID mode.

## Unicorn: real bound DWC3 entry, no guessed cache completion

The original stock QEMU kernel was paused briefly to read bounded fields
from the live FUSB binding, not to change state. Verified physical
addresses are kernel VA minus `0x60000000`; the raw `Image` file bias is
separately `0x60108000`. The stock boot had already bound
`fusb+0xBC -> 0x604E4E30`, with callback `0x602B1B9C` and a native DWC3
object. The selected context receipt is `usb-stock-13a3-first-view-bars-03/report.json`,
SHA-256 `ea79ed76940adc6cc1972dbf06dd2d42d322bff2e9a1f61ebaf81d2cb3832893`.
It supplies object `0x689E2810`, DWC3 virtual register base `0xB0880100`,
GCTL `0`, outer sync callback `0x6011A5D0` and PL310 base `0xF8002000`.

In the opt-in original process replay, E305's native FUSB `BLX`
executes and enters `dwc3_gadget_otg_probe`. The actual ARM at
`0x602AE108..0x602AE110` reads GCTL and prepares `0x00000800`.
It calls original `l2x0_cache_sync` before writing GCTL. At
`0x6011A5E8` the original instructions would store `0` to
`0xF8002730`. The bounded replay stops **before** this store, with
no GCTL write, no passed reset, and no fabricated DMA coherence.
Reverify 0.11.0 verified the pinned byte windows; the source of the
pointer values is the native QEMU capture, not those byte claims.

Reproduce with E0654's Unicorn command, adding
`--otg-qemu-context <trusted-stock-report> --otg-qemu-context-sha256 <digest>`.
`usb-gadget-13a3-dwc3-prefix-01` and `-02` retain the same stop, fd 11
and userspace SVC `0x5029B078`. Neither the context-only replay nor
the opt-in E0597 prefix is persisted as a successful E305 completion.

Next boundary in QEMU is the original physical-key inhibit release,
not the observed FirstView checks or a missing USB packet encoder.
In Unicorn, qualify cache synchronization,
controller initialization, gadgetcore commit, then original event delivery
and ownership closure. None of the three upstream issues is closed by this
record.

## Validation

The original-process, default-refusal and USB host suites ran **119 tests**
with all trusted input variables set; no skips or failures. Receipt:
`artifacts/tests/usb-13a3-first-view-focused.log`. The earlier full-suite
receipts retain a status-card synchronization failure during editing and a
Windows `UC_ERR_NOMEM` allocating the full ILCE-6400 machine while the
shared host also ran QEMU. The status tests and isolated allocation test
passed afterward. The final full discovery, run separately from the VM
experiments using the physical-volume-denied E0534 recipe, ran **2,163
tests, 120 skipped, zero failures or errors**. Receipt:
`artifacts/tests/usb-13a3-first-view-full-serial.log`.
