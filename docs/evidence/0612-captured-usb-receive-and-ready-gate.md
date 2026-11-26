# E0612 — Captured USB receive path and a passing readiness gate

Date: 2026-11-19. Offline ILCE-6400 QEMU only. Follow-up to
[E0611](0611-qemu-usb-input-chain-identity-boundary.md) and Phase 1 of the
[attach-chain plan](../plans/qemu-usb-attach-chain-plan.md).

## Observed boundary

Identity-matched probes establish that the USB message reaches **Linux
InfraInput**, rather than disappearing before it. The captured original
`I-InputRecv` thread reads queue `0x007A0020` directly. Both attach and detach
arrive intact and the original physical-key lookup returns index `0x19D`.
This index is not an application event ID or proof of callback delivery.

The sampled `model/ROOT` parameter `0x102` is already `2`, and the original
readiness checker returns `1` at its observed return site before cable attach.
Nevertheless `InputService::createEvent(0x15040010, ...)`, `CheckerUSBConnect`,
`ActionOpenUSB` and `StartGadget` remain unobserved at the qualified probe sites.
Thus a blanket explanation that ROOT readiness prevents the chain is not
supported. A further trace passes receive-side filtering and observes both
USB states in `I-InputMain`. The next boundary is **I-InputMain's subscription
filtering and callback dispatch**, after both original queue transfers.

There is no new hardware reply, firmware patch, synthetic input message,
gadget activation, stock USB screen or completed USB attach chain in this record.

## Identity and original code

E0611 pins the prepared kernel, av-cam, Backup.bin and saved filesystem hashes.
This work preserves those inputs and pins the captured libraries:

| Library | SHA-256 |
| --- | --- |
| libObj.so | `e88b7e0fc61b8c98ac06c1979e6ebdd3fa8a2a49384b3fd5ffc8d06da4174ef1` |
| viewUnified2.so | `fa534aa2160fc3e9c93119a85bdd9f1988b5f9f64b62c43fad178fabe4fa823d` |
| libUsbControl.so | `b5cea9d2c92a0b0810353900871fa2806ca3064aa5d8cf3ca7db5ddb6f3940e2` |

The generators fail on mismatched captured hashes. Final discovery probes
verify 32 original bytes at the executing PC before incrementing their
bounded counters, then resolve hits against retained executable process maps.
This prevents common short prologues at unrelated page offsets from consuming
the log cap. Each observation retains executable/plugin/source/boot-manifest
hashes. `instruction-windows.json` preserves bounded objdump commands, bytes
identities and local packaged/captured comparisons; no global address delta
is assumed for differing libraries.

Constructor decoding uses actual ELF PT_LOAD translation and R_ARM_RELATIVE
GOT entries, not file offsets as writable-segment VAs. The saved constructor
receipt identifies `I-InputMain` at `libObj+0x861ED0`, `I-InputRecv` at
`+0x862800`, `I-InputRepeat` at `+0x862BF8`, and `I-InputILog` at `+0x863EEC`.
These are linked coordinates, not runtime process addresses.

Original instructions establish the receive path:

1. `+0x86282A..+0x862834` loads literal `+0x862ABC = 0x007A0020` and calls
   original `osal_rcv_msg_tmo(..., -1)`.
2. `+0x862890` observes the original received pointer, with four physical
   message words retained as `physical16`.
3. `+0x862A26` calls `+0x860D18`; `+0x862A2A` observes the original key-table
   lookup return. For USB it is `0x19D`, not a negative lookup failure.
4. Later original code checks object byte `+0x44`, manages per-key state and
   can forward to queue `0x007A804C`, read by `I-InputMain`.

This corrects the plan's earlier unverified requirement for an
`av-cam -> Linux InfraInput` return hop. ORIL contains related queue literals,
but those literals do not establish ownership of this observed receive.

## Bounded replay observations

Policies: `REAR_CONTROLS=1`, `USB_VBUS_TEST=1`, `USB_CID_HIGH=1`,
`EVSEL_LATE_US=500`, `ULOGIO=''`, `LIVEVIEW_SOURCE=none` or `bars`,
`-icount shift=0,sleep=on -net none -no-reboot`. Each replay verifies prepared
inputs and uses a new disposable overlay. A normal QMP `ret` key dismisses
the NTSC notice. QMP then applies true, duplicate true, false VBUS inputs.
The observer captures the actual LCD before and after toggles and bounds
the run to 240 seconds; each cited paired replay quits its owned QEMU with 0.

| Observation | none | bars |
| --- | --- | --- |
| FUSB attach/detach handler edges | 2 | 2 |
| Duplicate attach produces another edge | no | no |
| ROOT 0x102 at two sampled stores | 2, 2 | 2, 2 |
| Ready-checker return at `view+0x2AC03A` | 1 | 1 |
| USB physical message received on attach/detach | yes / yes | yes / yes |
| USB lookup return at `libObj+0x862A2A` | 0x19D / 0x19D | 0x19D / 0x19D |
| USB application event created at qualified createEvent site | 0 | 0 |

The exact-signature send probes retain the original `osal_snd_msg` queue and
payloads: attach `01000007000000000100000000000000`, detach
`01000007000000000000000000000000`, queue `0x007A0020`, return `0`.
Only the first 16 bytes of send-probe `event24` blobs are the allocated
physical message; the extra eight bytes are not interpreted as message data.
The subsequent receiver probes independently observe those exact 16-byte
messages. Four unrelated `snd_InfraInputEvent_to_InputServant` events contain
`0x150700F0`/`0x150702C0`; they are not substituted for USB event `0x15040010`.

The rear display remains the original P shooting view, with the bars chart
under the OSD in that policy. It does not enter a stock USB mode screen.
Readiness was sampled before attach, not continuously proved for every later
application decision. No complete call-stack pairing or real-time timing
model is claimed.

The subsequent `e0612-usb-filter/` observation further narrows the boundary:

- At `+0x862A9A`, original `r3=0` after loading object byte `+0x44`; the
  receive-side suspend gate is clear for both USB edges.
- Original receive-to-main forwarding at `+0x862BDC` returns `1`, its
  helper's success result, at `+0x862BE0` for both edges.
- `I-InputMain` copies the original six words at `+0x861F8C`: attach
  `{07000001, 0, 1, 0, 19D, 2}`, detach `{07000001, 0, 0, 0, 19D, 2}`.
  The final two words are the original key-table index and flags, not host additions.
- No USB createEvent or ActionOpenUSB is observed in this run either.

The captured view's original key-list data at linked VA `+0x95AAE4`, entry 59
of 212, contains `{0x07000001, 0x15040010, 0x7FFFFFFE}`. The runtime
SetEventKeyList call passes that list for handle 3. Registration-order
analysis identifies `view+0x2BD034` as a callback candidate; its retained
original instructions tail-call the InputService direct callbacks. This is
a static/dynamic lead, not proof that this subscription is enabled or invoked
for USB. Inspect the actual I-InputMain subscription state before blaming
hardware or bypassing a callback.

## Receipts, failures and validation

Artifacts under `artifacts/qemu-a6400/` are separate, retained generations:

- `e0612-captured-usb-chain/`: initial captured-code probes, bounded original
  instruction windows, constructor pointer receipt, Ghidra receipts and audit.
- `e0612-usb-mailbox/`: original send payload/return and callback-registration probes.
- `e0612-usb-exact/` and `bars/`: stronger 32-byte signature validation and
  original send/ready observations.
- `e0612-usb-recv/` and `bars/`: direct queue receive and physical-key lookup.
- `e0612-usb-filter/`: additional receive-to-main filtering observation.

Earlier short-signature generations remain discovery records. The none
receive replay's serial map listing omitted a parseable libUsbControl map;
its zero selected UsbControl counts are **unqualified**, not missing execution.
The independently qualified exact-signature pair and bars receive replay
supply the sender observations. LibObj/view mappings in the receive pair are retained.

The configured Ghidra 12.2 path was absent in this WSL installation. No tools
were installed or global settings changed. The explicit legacy profile
(Ghidra 12.1.4, JDK 21) supports separate bounded headless receipts with no
global analysis. The first legacy query failed because required query labels
were absent; its failed result is preserved even though headless exited 0.
Subsequent labeled, constructor and task batches report completed and preserve
unchanged input hashes. These are the bounded headless profile, not a completed
default REA import. Pseudocode with bad-instruction warnings (notably the
exploratory synchronous callback window) remains unqualified; it does not
authorize runtime contracts.

`audit.py` passes **46 receipt checks** across the exact-signature and receive
pairs and the filtering observation: owned exits, tool identities, edges,
ROOT/checker values, original send/receive payloads, lookup returns, main-thread
delivery and no exhausted user log caps in the paired traces. Scripts
pass Python syntax checks. The full emulator/unit suite was not rerun for
artifact-only research; no production emulator code changed.

## Next boundary

Follow original I-InputMain subscription filtering and callback delivery. Do not add
a ready flag, fabricate `0x15040010`, or emulate an ORIL register on the basis
of the superseded routing hypothesis. A missing hardware contract has not
been established, so Phase 2 implementation is not justified by this evidence.
