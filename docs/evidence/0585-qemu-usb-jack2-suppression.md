# E0585 — UsbIfWrapper event 9 (0x0A6000) sets jack 2 and gates QMP VBUS in ProcessMsg

Date 2026-11-18. Offline QEMU follow-up to
[E0584](0584-qemu-usb-postmsg-and-processmsg.md).

## Inputs and tool identities

- **QEMU binary:** Isolated scratch `qemu-system-arm` SHA-256
  `8bcef2b519915f88c92a768d929007c80fb8d31d03a8c87590ab0c67c0fe9fd1`
  (`build.sha256`).
- **TCG instrumentation plugin:** `artifacts/qemu-a6400/e0585-usb-jack2/vbus-trace.so`
  SHA-256 `94d32d5c70c825718237d3134052961a8690b72d2306e8f50cc95f1dcbe51fa9`.
- **Guest binaries (verified `ILCE-6400` 2.00 dump):**
  - `libUsbControl.so` (94,220 B, SHA-256
    `70cd5742fde9eea78c60905e4015531b4c3ab2668d2cb85c934531f451f1211f`),
    mapped in `im.elf` at `0x079ad000–0x079c3000` (`r-xp`).
  - `libInfraUsbCmn.so` (85,876 B, SHA-256
    `5338bafbb545ebc223fac15c9253a41ac9fe49c01fe32c73af40c3fb50eec0a6`),
    mapped in `im.elf` at `0x04a6b000–0x04a7e000` (`r-xp`).
  - `usb_portMonitor` kernel module live at `0x5F0A7000`.
- **Boot set and policy:** Linux-local `inputs-moddev-1` copy, disposable
  eMMC overlay, `ULOGIO=`, `USB_VBUS_TEST=1`, `EVSEL_LATE_US=500`, and
  `-icount shift=0,sleep=on -net none -no-reboot`. Offline QEMU test only;
  no physical camera/USB access and no guest firmware or USB data modification.

## Commands

```powershell
wsl.exe --exec python3 artifacts/qemu-a6400/e0585-usb-jack2/prepare.py
wsl.exe --exec python3 artifacts/qemu-a6400/e0585-usb-jack2/build.py
wsl.exe --exec env PYTHONPATH=. python3 -u artifacts/qemu-a6400/e0585-usb-jack2/replay-vbus.py
wsl.exe --exec env PYTHONPATH=. python3 artifacts/qemu-a6400/e0585-usb-jack2/correlate.py
```

All four commands exited `0` (`vbus-report.json`: `exit=0`, `owned_exit=0`).

## Original instruction support and serviced operations

- `libUsbControl.so+0x12E54` (`UsbIfWrapper` event callback registered via
  `usbif_initialize` at `+0x131F0`): case `r0 == 9` (`+0x12E8C..+0x12EDE`)
  constructs `MWF::ObjMsg(0x6103, 0x0A6000)` (`+0x12E9E`) with `0x1002` and
  `0x1003` integer parameters and delivers it via `UsbIfWrapper::EventListner`
  (`+0x12FD6`).
- `libInfraUsbCmn.so` static dispatch (`proc_svc_event` `+0xCDF4`, `CUsbSvc::proc_kevent_otg`
  `+0xB0DC`): `CUsbSvc::proc_kevent_otg` case `1` (`+0xB15A..+0xB37E`) calls
  `CUsbSvc::proc_notify(1, ...)` (`+0xB370..+0xB37E`), and `proc_svc_event`
  case `1` (`+0xCE36..+0xCE48`) invokes the registered `UsbIfWrapper` callback
  with `r0 = 9`.
- `UsbControl::ProcessMsg` case `0x0A6000` (`+0x110CC..+0x110F4`) reads
  `param_1003` (`+0x110D2`) and `param_1002` (`+0x110E4`) and calls
  `UsbControl::SetJackStatus(this, 2, param_1002)` (`+0x110EE` -> `+0x12980`).
- `UsbControl::SetJackStatus` (`+0x12980`): `jack_id == 2` (`+0x129B2`) stores
  `status` into `[this+0xCC]` and returns `0`; `jack_id == 1` (`+0x129A4`)
  reads `[this+0xCC]` and returns `0` without updating `[this+0xD0]` when
  `[this+0xCC] == 1`.

## Observations

### 1. Direct observation of `0x0A6000` origin and `[UsbControl+0xCC]` transitions

Every `0x0A6000` message entering `UsbControl::preProcess` (`+0x123D6`) during
boot originated from the `UsbIfWrapper` callback at `+0x12E54` with `r0=9`
(`arg_ptr=0x07F36CF0`, 3 calls total). Separately, `UsbControl::NotifyJackStatus`
(`+0x128CC..+0x12920`) constructed two outbound `0x0A6000` notifications with
`r4=0` (`GetJackStatus(this, 2) == 0`) on queues `0x00C881BB` and `0x012282F6`
that did not re-enter `UsbControl::preProcess`.

Runtime memory reads of `this + 0xC0..0xDF` (`jacks=`) around `SetJackStatus`
directly captured all three boot `0x0A6000` executions on `this = 0x09200AB8`:

| # | `ObjMsg` | `param_1002` (`r2` at `+0x12980`) | `[+0xCC]` before | `[+0xCC]` after (`+0x110F4`) | `SetJackStatus` return |
| --- | --- | --- | --- | --- | --- |
| 1 | `0x00034E08` | `0` | `0` | `0` | `0` |
| 2 | `0x00034EB8` | `1` | `0` | `1` | `0` |
| 3 | `0x00033FE0` | `1` | `1` | `1` | `0` |

### 2. Direct observation of `[UsbControl+0xCC] == 1` gating QMP VBUS

During both post-boot QMP transitions (`attach` and `detach`), the same memory
read at `+0x1103E` / `+0x12980` and `+0x11042` directly confirmed
`[0x09200AB8 + 0xCC] == 1` and `[0x09200AB8 + 0xD0] == 0`, with
`SetJackStatus` returning `r4=0` (`+0x129CE`) and `ProcessMsg` observing
`r0=0` (`+0x11042`) without reaching `+0x1104E`, `+0x11076`, or `+0x110B0`:

| QMP transition | `SetJackStatus` args | `[+0xCC]` before / after | `[+0xD0]` before / after | `r4` (`+0x129CE`) / `r0` (`+0x11042`) |
| --- | --- | --- | --- | --- |
| Attach (`0xF2920010=1`) | `(0x09200AB8, 1, 1)` | `1` / `1` | `0` / `0` | `0` / `0` |
| Detach (`0xF2920010=0`) | `(0x09200AB8, 1, 0)` | `1` / `1` | `0` / `0` | `0` / `0` |

## Next boundary

**E0586:** Trace the kernel `/dev/usb/event` OTG kevent type-1 producer
(`usb_portMonitor` / `fusb` / OTG `ID`/`CID` register) feeding
`CUsbSvc::proc_kevent_otg` case `1` (`libInfraUsbCmn.so+0xB15A..+0xB37E`) to
determine why boot reports `0` then `1` for `jack_id == 2` (`CID`/host-mode jack)
and keeps `[UsbControl+0xCC] = 1` in QEMU.

## Receipts

- `artifacts/qemu-a6400/e0585-usb-jack2/prepare.py`, `fsm-trace.c`,
  `build.py`, `build-commands.txt`, `build.sha256`, `replay-vbus.py`
- Capture & verification: `vbus-console.log`, `vbus-report.json`,
  `correlate.py`, `correlation.json` (all checks exited `0`).
