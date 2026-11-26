# E0598 — Token-driven EP0 reaches original UDC configuration

Date: 2026-11-18. Offline QEMU only; no physical USB, camera, SD or flash access.

## Result and scope

An explicitly selected `cxd90045-dwc3.strict-ep0=on` policy completes six
standard control transfers through the original captured kernel and gadget:

1. GET_DESCRIPTOR(DEVICE,18), including the OUT status ZLP.
2. SET_ADDRESS(1), including the IN status ZLP.
3. GET_DESCRIPTOR(CONFIGURATION,9), including status OUT.
4. GET_DESCRIPTOR(CONFIGURATION,34), including status OUT.
5. SET_CONFIGURATION(1), including status IN.
6. GET_CONFIGURATION, returning `01`, including status OUT.

Two fresh runs of the final binary observe original UDC state **configured**
before disconnect, DCFG address field **1**, original configuration readback
**1**, and exact complete host-token ledgers. Native qtest passes **33**
ownership/status/error-boundary checks. E0597's **44** independent profile
checks still pass, and a fresh legacy-mode firmware run returns the unchanged
DEVICE packet.

This qualifies a **bounded virtual control-transfer enumeration sequence**.
The selected original function is bus-power/HID, **not UVC**. No HID report,
UVC control/alternate-setting exchange, video payload or completed teardown
is claimed. Host address filtering, physical packetization/timing, reset/
stall recovery and unsupported controller commands remain unqualified.

## Why the earlier model was insufficient

The historical model completes IN TRBs immediately when the guest arms them,
even without a host request, and its OUT parser rejects zero-byte data. That
path remains the default to preserve earlier replay baselines. New behavior
requires the independent strict-ep0 option; no earlier default is silently
promoted to a faithful USB transaction model.

The new path:

- Retires a bounded single TRB only on the corresponding explicit host
  request: `SETUP <8-byte hex>`, `IN 1`, `OUT 0 <hex>` or `OUT 0 -` (ZLP).
- Returns `NAK <ep>` when unarmed. It emits the configured DATA/STATUS
  XFERNOTREADY event once per pending request; the host must retry. No
  token is retained and automatically consumed after a later arm.
- Preserves HWO until the requested transfer actually completes. Status
  OUT accepts zero bytes and retains the unused receive-buffer length;
  status IN emits a real empty `IN 1 ` response only on a host request.
- Uses original endpoint event-enable bits, DALEPENA, Run/Stop and stall
  state. Data, SETUP, two-stage status and three-stage status TRB types are
  checked separately.
- Reports explicit ERROR boundaries for malformed/oversized input lines,
  wrong direction/stage, unsupported TRB types/chains/length flags, transfers
  beyond the 1024-byte bound, unavailable rings and invalid DMA. It does not
  truncate a longer request to 1024 and mark the whole request successful.
- Limits DMA to declared sony-a6400 RAM windows, with range/overflow and
  MemTxResult checks. Catch-all MMIO zero replies cannot become fabricated
  DMA payloads. No arbitrary memory or host-path mapping is introduced.

Tokens represent bounded single-TRB transfer requests, not a verified
physical packet/burst/timing engine. Only the observed short EP0 exchanges
are qualified here; broader transfer sizes and endpoint classes are not.
The older DEPCMD/register model is not qualified in its entirety by this work.

## Original instruction support and host implementation

Consulted RELATED_REPOSITORIES, REA/dependency workflows, E0468/E0476's
status/NRDY observations and identity-matched E0597 inputs. No upstream
implementation was copied. Targeted raw-kernel ARM decoding and actual
original-driver execution qualify this step; a new ELF/REA call-chain result
is not claimed for the raw kernel.

Captured kernel SHA-256:
`d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618`.
Its own kallsyms locate:

| Original function | ARM VA | Contract used |
| --- | --- | --- |
| dwc3_ep0_start_trans | 0x602B2424 | original TRB and STARTTRANSFER production |
| __dwc3_ep0_do_control_status | 0x602B253C | 0x30/0x40 control type selection; IN/OUT length rules |
| __dwc3_ep0_do_control_data | 0x602B25C8 | original data-stage TRB production |
| dwc3_ep0_interrupt | 0x602B2C68 | actual complete/not-ready event handling and subsequent arms |

Status type 3 versus 4 follows whether SETUP declares a data phase. The
original OUT status path can arm a nonzero receive capacity; receiving a
ZLP leaves that capacity as residual instead of demanding a zero-sized TRB.
Native fixtures and original-driver replays independently cover these rules.
`kernel-identity.json` and `kernel-instruction-candidates.txt` under
`artifacts/tests/qemu-ep0-e0598-2/` retain source identity, symbol bounds and
decoded candidates. Inline table words are not asserted to be executed code.

`qemu_ep0_host.Ep0Host` sends bounded tokens and retries NAKs with the same
request. It rejects STALL, ERROR, EOF, malformed acknowledgements, wrong
endpoint NAKs, invalid descriptor lengths and mismatching configuration
readback. It supplies no device reply. The full CONFIGURATION bytes and
configuration value are read from original firmware, not constructed by
the host. Diagnostic configure/start input remains the unchanged E0596 mod;
this is not a stock application USB decision.

## Inputs, tools and exact replay policy

Final DWC3 overlay SHA-256:
`7e19837ec87f38fd56097ab22f7b18632708f8ae33e2850adfb828c2f2e01adf`.
Isolated QEMU SHA-256:
`056b7c35cc38af52dda1ac16109689fcf9c4db6ec7a7e648810574917213cb67`.
Build: `artifacts/qemu-a6400/e0598-isolated-build-2/`.
Original upstream sources/objects are untouched; exact compile/relink
arguments and build log are retained. The default hardware profile,
captured hardware profile and captured start-gate combinations pass 44 reads.

The unchanged captured manifest is
`5aa4c878cd8214c5b503388f44d3d4f4512482d7ef37050beaadbe8b24906147`.
Unchanged stimulus mod SHA-256:
`16c0e4eabd8b92754a614fab99b52a116109095ad9f0fdb35d54ad6731258255`.
Unchanged passive E0597 startup plugin SHA-256:
`efcef87c449cd1f6f2277c99677512606ea9998473056cf4d0715598330935f7`.
Loader/guard/request hashes remain in every report. Current validation-tool
identities are in `validation-source-identities.json` alongside the native
contract receipt.

```bash
python3 -m tools.research.qemu_dwc3_profile_check \
  --qemu-dir /home/administrator/sony-a6400-qemu-11.1.3 \
  --report-dir artifacts/qemu-a6400/NEW_BUILD
python3 -m tools.research.qemu_ep0_contract_check \
  --qemu-dir artifacts/qemu-a6400/NEW_BUILD \
  --report-dir artifacts/tests/NEW_CONTRACT_CHECK
python3 -m tools.research.qemu_usb_service_replay \
  --qemu-dir artifacts/qemu-a6400/NEW_BUILD \
  --inputs /home/administrator/a6400-inputs/inputs-moddev-1 \
  --loader artifacts/tests/mlv2-action-build-1 \
  --mod artifacts/tests/qemu-usb-stimulus-e0596-captured-2/UsbServiceProbe.mod \
  --plugin artifacts/tests/qemu-usb-startup-trace-e0597-5/trace.so \
  --captured-start-gate --vbus-before-start --strict-ep0 \
  --report-dir artifacts/qemu-a6400/NEW_RUN
```

Then, with the existing project Python:
`python -m tools.research.audit_qemu_usb_startup artifacts/qemu-a6400/NEW_RUN --expect-enumerated`.
Omitting strict-ep0 preserves E0597's one-descriptor observation mode.

## Observations and failures

Final original-firmware receipts:

| Run | Observation |
| --- | --- |
| e0598-control-sequence-3 | Six controls complete; UDC configured; 64.7 s; passive original create/descriptor commit checks pass |
| e0598-control-sequence-4 | Same complete sequence and exact descriptor bytes; UDC configured; 69.5 s |
| e0598-legacy-descriptor-regression-2 | Strict mode off: original DEVICE packet unchanged; 69.8 s; ten E0597-style receipt checks pass |

Both final enumeration receipts pass all **61** auditor checks, including
every TX/RX token pair, all six status phases, native DCFG address 1,
original UDC configured and GET_CONFIGURATION=1. QMP quit and owned process
exit are 0. Read-only model observations after configuration include:
GCTL=0x2000, event-ring size=0x1000, DCFG=0x00400009,
DCTL=0x8CF00A00, DEVTEN=0x1E1F and DALEPENA=0xB.
Run 4 records 18 DWC3 interrupts. Guest-allocated ring addresses and ASLR
library bases are runtime values, not host fixtures.

DEVICE bytes remain `12010002000000404c056f09000101020301`.
The original complete 34-byte CONFIGURATION is
`0902220001010080fa09040000010300000009211101000122130007058103200001`:
one interface, class 3 (HID), interrupt-IN endpoint 0x81, configuration 1.
Neither these original descriptors nor a configured UDC imply UVC support.

Preserved earlier/failed trials:

- `e0598-control-sequence-1` first completes the sequence on the preliminary
  build, before the final explicit RAM-window guard was added. It is not
  used as proof of the final DMA rejection policy.
- Concurrent `e0598-control-sequence-2` and
  `e0598-legacy-descriptor-regression-1` stop before the menu checkpoint at
  139.4 / 139.6 seconds, without executing USB stimulus. Cause is unresolved;
  parallelism is not asserted to be the proven cause. Their original reports
  and console/serial logs remain. Subsequent runs are individual fresh boots.
- The first enumeration-auditor attempt raises IndexError because the older
  parser assumed every IN reply contained nonempty data. It is corrected to
  retain and validate actual empty status replies. Raw guest/host captures
  were not rewritten or treated as failures of the firmware transfer.

## Validation and next boundary

Native qtest passes 33 checks covering token-gated HWO, no unsolicited IN,
NAK retries without queued delivery, one pending NRDY, zero OUT/IN status,
residual length, malformed/overflow input, wrong direction, unsupported
LINK/chain/long transfers and MMIO payload refusal. These are constructed
fixtures, separate from original-firmware replay.
Seven host-protocol tests cover complete phase ordering, retry, bad ACK/NAK,
empty descriptor rejection, configuration mismatch, STALL and EOF.

The combined focused status/host checks pass 50 tests. Full offline discovery
passes **2120 tests, 29 skipped**, in 63.258 seconds with physical media
explicitly denied and per-case garbage collection. The retained E0596
`run_full_suite.py` is the runner; receipt:
`artifacts/tests/qemu-ep0-e0598-2/full-suite.txt`. Python compilation,
scoped whitespace and generated status checks pass. Status SVG/PNG outputs
and current summaries were refreshed; historical raw evidence is retained.

Next: exercise a hash-qualified real UVC function/module inside the guest.
Non-EP0 data endpoints, transfer chains, packetization and video buffers need
their own contracts. The physical B26 authorization boundary is unchanged;
no physical UVC test or camera modification is part of this record.
