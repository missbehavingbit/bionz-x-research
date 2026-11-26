# E0593 — QEMU virtual USB host on the DWC3 model: the guest never starts the gadget

Date: 2026-11-18. Offline QEMU `sony-a6400` with the camera-captured inputs. No physical camera,
SD card, or USB access.

## Purpose

This step makes UVC testable in QEMU before any camera run. The repository already had an opt-in
`cxd90045-dwc3` gadget model (`dwc3-gadget=on`, PR #33) with a line-based host chardev
(`CONN`, `DISC`, `SETUP <hex>`, `OUT <ep> <hex>`; replies `IN <ep> <hex>`). Neither QEMU build used
by the panel contained it, and no record shows it running against the guest kernel. This record
connects a virtual host to it and observes the original kernel.

## Tools and identities

| Item | Value |
| --- | --- |
| QEMU | `~/sony-a6400-qemu-11.1.3`, current `qemu-a6400/qemu-overlay` copied in (the setup-script step), `ninja qemu-system-arm`; SHA-256 `18000b29a3c42f4abfc413d5fc44bff400c30467663d89a6ba86ae6e4c87996b` |
| Inputs | `~/a6400-inputs/inputs-moddev-1` (E0524 captured disk), manifest SHA-256 `5aa4c878cd8214c5b503388f44d3d4f4512482d7ef37050beaadbe8b24906147` |
| New probe | `tools/research/qemu_usb_host_probe.py`, SHA-256 `0fe9e7ff2264fb0592c39b2836fbf279a504fce3273cdf1fd649413cf8ccc543` (run 4) |
| Trace plugin | `qemu-a6400/tools/dwc3_trace.c` (E0579), ported to plugin API 7 under `#if`; `artifacts/tests/dwc3-trace-11.1.3/dwc3_trace.so`, SHA-256 `6e3d798306142d7a1f38c3ea9ea19fe88c5859b937e67b57c745ac5e7ed5e80f` |

```bash
python3 -m tools.research.qemu_usb_host_probe --qemu-dir $HOME/sony-a6400-qemu-11.1.3 \
  --inputs $HOME/a6400-inputs/inputs-moddev-1 --plugin artifacts/tests/dwc3-trace-11.1.3/dwc3_trace.so \
  --report-dir artifacts/qemu-a6400/e0593-dwc3-host-4
```

The probe runs `run.sh` with `DWC3_GADGET=1 USB_VBUS_TEST=1 USB_CID_HIGH=1`, a
`-chardev socket` bound with `-global cxd90045-dwc3.chardev=usbhost`, and QMP. After the firmware
marker and shell it records guest state, sets `usb-vbus=true`, sends `CONN`, sends one
GET_DESCRIPTOR(device, 18) SETUP, collects replies for 10 s, sends `DISC`, and clears VBUS. It reads
nine model registers through `human-monitor-command xp` before VBUS, after VBUS, and after
`CONN`. Nothing is answered on the guest's behalf.

## Observations (run 4; runs 1–2 agree, run 3 failed to start on a path with a space and was removed)

| Point | Result |
| --- | --- |
| Guest UDC | `/sys/class/udc/dwc3.0` exists; `state=not attached`, `current_speed=UNKNOWN` before, during, and after the attach |
| IRQs | 229 `dwc3`: 0 on every CPU throughout. 230 `fusb_usb_vbus`: 0 → 1 (attach) → 2 (detach). 231: 0 |
| Model registers (all three reads) | GCTL, GEVNTADRLO0, GEVNTSIZ0, GEVNTCOUNT0, DCFG, DCTL, DEVTEN, DALEPENA = 0; DSTS = `0x00400000` (halted) |
| Host replies | none (`in_replies: []`) |
| QEMU exit | QMP `quit`, exit 0 |

The trace plugin captured 28 driver-range accesses, all during boot except the VBUS handler:

- FUSB/OTG setup at `0x602ad748..0x602ad8d4` (as in E0579), including `0xF020C2C0 = 0x11080002`
  then `0x00020000`, and `0xF020C200 = 0x40`.
- `dwc3_probe` `0x602ae464` writes DCTL `0x40000000` (core soft reset). The model clears it, and
  the read at `0x602ae46c` returns 0.
- The reset routine writes GCTL `0x800`, reads it back, writes 0 (`0x602ae130..0x602ae168`). Then
  `0x602ae4c0`/`0x602ae524` read GCTL and write 0.
- After that the driver makes no DWC3 access: no GSNPSID or GHWPARAMS read, no event-buffer
  address/size, no DEVTEN, no DCTL.RUN. On attach the only access is the FUSB VBUS read
  `0x602ad2ac` (`value=3`).

## Conclusion

- The DWC3 model and the original driver agree on the reset handshake. The model answers the
  soft-reset bits as the driver expects (DCTL self-clear, GCTL read-back), with no
  "failed to initialize core".
- On this kernel the event ring and Run/Stop are not set at probe time. They come later, when a
  gadget is started through `usb_gadgetcore`. In this boot the start never happens: the stock
  `UsbControl` never sends `StartGadget` (`0x0A2000`, E0588/E0589). The OTG `SET_MODE` path that
  would precede it was isolated in E0534. Without Run/Stop the model correctly ignores `CONN`, and
  a SETUP has no TRB to land in.
- So the next QEMU boundary for UVC is starting a gadget, not the DWC3 transfer model. It is still
  unknown whether the model's EP0/TRB handling satisfies the original driver once started.
  Nothing here is UVC enumeration.

## Next boundary

Start a gadget in QEMU through original code. Either release the stock `StartGadget`
(`DefRootView` past FirstView, E0589), or drive the original `libInfraUsbCmn` createGadget sequence
(OTG `SET_MODE` mode 2 `0x4001E30C`, then gadgetcore) from a bounded guest helper that only uses
those original ioctls. Then repeat this probe: event-ring programming, DCTL.RUN, the `CONN` reset
events, and whether GET_DESCRIPTOR returns an `IN 1` line. Only after that does loading the Track B
UVC module in the guest become meaningful.
