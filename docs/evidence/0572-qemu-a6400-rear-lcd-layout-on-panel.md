# E0572 — Firmware rear-LCD layout rendered on QEMU panel output

Date: 2026-11-17. Offline firmware replay following E0571.

## Result

An opt-in diagnostic changed the return value of the firmware's display
controller from device 2 to device 1 on a disposable eMMC overlay.
QEMU head 0 then showed a different firmware-rendered 640 × 480 OSD.
The same run executed the original
`LG_viewstlrec::LayoutST_NORMAL_WITH_INFO_LR` methods at `0x16d618`
and `0x16d624`. QEMU captured the output directly. The YUV image in the
center remains parked at E0569.

## Inputs and controls

The E0571 input identity still applies:
`viewUnified2.so` SHA-256
`1e2867b6bff2d4fd4d3b93bacf8c7da0b9a86f266b4ba1763230e33badb6e7f2`.
The baseline QEMU binary was
`2720703c2b200a1dbaf9ee7c7e4a99e3e6a8c026b21e7517bcd7b836354486a7`.
The replay uses the E0561 legacy layout, a fresh qcow2 overlay,
`EVSEL_LATE_US=500` (experimental), `-icount shift=0,sleep=on`,
and the launcher defaults for ulogio and submicro input.

From the project root in WSL:

```bash
bash artifacts/qemu-a6400/rear-lcd-selector/replay.sh NEW_BASELINE
bash artifacts/qemu-a6400/rear-lcd-selector/replay.sh NEW_PATTERN monitor-pattern
bash artifacts/qemu-a6400/rear-lcd-selector/replay.sh NEW_PANEL monitor-output
```

The trace signatures use 12 original bytes from the hash-checked ELF, and
their runtime bases are checked against inode 14273 in guest process maps.
The syscall trace records pathname arguments at entry. Both the stock
and edited firmware request `image_EVF.uxc` and
`image_DSC_Only_169.uxc` during boot, so asset names alone do not
identify the active layout.

| Run | Observation |
| --- | --- |
| Baseline and trace-02 | `0x40aa5c` returned device 2 in all 16 recorded samples. No hit at either of the two monitored LCD methods. Head 0 SHA-256 `e26b3bee28b653c49d4319df22a7d837993ac44c576aac31a73b9a24c2de7ee0`. |
| monitor-pattern-01 | Changed `0x40a2e4` from `movs r0,#2` to `movs r0,#1` on the overlay. The lookup at `0x40a324` selected the alternate key, but device 2 still reached `0x40aa5c`. Both head digests matched the baseline. |
| monitor-output-01 | Changed the controller return at `0x40a374` (`mov r0,r4` → `movs r0,#1`, bytes `20 46` → `01 20`, disk offset `0x360eab74`). Device 1 reached `0x40aa5c`. The LCD methods executed, and head 0 SHA-256 changed to `a0f47a7ec2bdc9543feff65af45d4b341bbc2aa6bc56573e80889efb5bd8e0c7`. Head 1 was unchanged. |

Original class identity: the name pointer at file offset `0x8d6c14`
identifies `LayoutST_NORMAL_WITH_INFO_LR`; its typeinfo virtual address
`0x8dec10` is referenced from the vtable at `0x8deb70`. The address point
at `0x8deb78` contains the odd Thumb pointers `0x16d619` and
`0x16d625`. The constructor at `0x171e70` loads that vtable through GOT
slot `0x94d31c`, adds 8 and stores the result into the object. In
`monitor-output-01` both methods ran with object `0x0aea5458` and a
load bias of `0x117fc000`, matching `/proc/144/maps`.
The independent `monitor-output-02` replay captured both methods on
object `0x0b09aa60`, with vtable pointer `0x143cdb78`. Subtracting the
verified load bias `0x13aef000` gives the expected `0x8deb78`.
That run exited 0 and produced head 0 SHA-256
`c5268a00bc491bb93881a7096ef321c8451671c43dbaaee26b3ca922b2471265`
with a visible `NO CARD` indicator.

The first passive trace had stale register bytes in its shared read
buffer. `trace-01` is retained and its register values were discarded.
`trace-02` clears the buffer before each register read. Probe exits for
the direct baseline, corrected trace, and both overlay trials were 0.

The selected candidate keys for `CmnPanelCommonPattern` are
`0x150701B0` and `0x15070060`. At `0x2bf860` the guest calls
`InputService::getInputEventStatus` with the key under `0x17005003`
and reads the result from `0x17005008`. The next boundary is the
firmware input that naturally returns the LCD device. Stock boot,
physical LCD timing, and the native panel raster remain unverified.

Receipts are under `artifacts/qemu-a6400/rear-lcd-*/`.
`rear-lcd-monitor-output-01/head0.png` and
`rear-lcd-monitor-output-02/head0.png` are lossless RGB conversions of
their QEMU PPM captures, without resizing.
