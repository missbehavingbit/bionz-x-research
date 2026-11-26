# E0586 — FUSB bit 1 (0xF2920010) drives CID kevent subtype 0 and latches jack 2

Date 2026-11-18. Offline QEMU follow-up to
[E0585](0585-qemu-usb-jack2-suppression.md).

## Inputs and tool identities

- **QEMU binary:** Isolated scratch `qemu-system-arm` SHA-256
  `8bcef2b519915f88c92a768d929007c80fb8d31d03a8c87590ab0c67c0fe9fd1`
  (`build.sha256`).
- **TCG instrumentation plugin:** `artifacts/qemu-a6400/e0586-usb-otg-kevent/vbus-trace.so`
  SHA-256 `4b1bdda5a4a1d4ef62fc3b8c71e33bd9c7ca520dd853ab5d933488a09815613d`.
- **Guest binaries (verified `ILCE-6400` 2.00 dump):**
  - `nflasha3-Image` (kernel SHA-256
    `12eeec829407595e28265f7d0db3b88c56f63a9f0fdd725c9c53c66a49643707`).
  - `libInfraUsbCmn.so` (85,876 B, SHA-256
    `5338bafbb545ebc223fac15c9253a41ac9fe49c01fe32c73af40c3fb50eec0a6`),
    mapped in `im.elf` at `0x040af000–0x040c2000` (`r-xp`).
  - `libUsbControl.so` (94,220 B, SHA-256
    `70cd5742fde9eea78c60905e4015531b4c3ab2668d2cb85c934531f451f1211f`),
    mapped in `im.elf` at `0x045d2000–0x045e8000` (`r-xp`).
- **Boot set and policy:** Linux-local `inputs-moddev-1` copy, disposable
  eMMC overlay, `ULOGIO=`, `USB_VBUS_TEST=1`, `EVSEL_LATE_US=500`, and
  `-icount shift=0,sleep=on -net none -no-reboot`. Offline QEMU test only;
  no physical camera/USB access and no guest firmware or USB data modification.

## Commands

```powershell
wsl.exe --exec python3 artifacts/qemu-a6400/e0586-usb-otg-kevent/prepare.py
wsl.exe --exec python3 artifacts/qemu-a6400/e0586-usb-otg-kevent/build.py
wsl.exe --exec env PYTHONPATH=. python3 -u artifacts/qemu-a6400/e0586-usb-otg-kevent/replay-vbus.py
wsl.exe --exec env PYTHONPATH=. python3 artifacts/qemu-a6400/e0586-usb-otg-kevent/correlate.py
```

All four commands exited `0` (`vbus-report.json`: `exit=0`, `owned_exit=0`).

## Original instruction support and serviced operations

- **Kernel FUSB CID/VBUS probe (`nflasha3-Image`):**
  - `0x602ADB74`: reads `0xF2920010`; `0x602ADB8C` executes
    `ubfx r1, r1, #1, #1` (bit 1) and calls `0x602AD2CC` (`fusb_notify_cid`).
  - `0x602ADB80`: reads `0xF2920010`; `0x602ADB98` executes
    `and r1, r5, #1` (bit 0) and calls `0x602AD230` (`fusb_notify_vbus`).
  - `0x602AD330..0x602AD33C`: also samples bit 1 of `0xF2920010` via
    `ubfx r1, r1, #1, #1` and calls `0x602AD2CC` (`fusb_notify_cid`).
- **Userspace OTG kevent dispatch (`libInfraUsbCmn.so` -> `libUsbControl.so`):**
  - `CUsbSvc::proc_kevent_otg` (`+0xB0DC`): `r1 == 0` (subtype `0`, CID)
    routes through `+0xB368` (`CUsbSvc::proc_notify(0, ...)`) ->
    `proc_svc_event(0, ...)` (`+0xCDF4` -> `+0xCE36`) -> `UsbIfWrapper`
    callback (`libUsbControl.so+0x12E54`, `r0 = 9`), which builds
    `0x0A6000` (`param_1002` at `+0x12EA4`).
  - `CUsbSvc::proc_kevent_otg` (`+0xB0DC`): `r1 == 1` (subtype `1`, VBUS)
    routes through `+0xB36E` (`CUsbSvc::proc_notify(1, ...)`) ->
    `proc_svc_event(1, ...)` (`+0xCE4A`) -> `UsbIfWrapper` callback
    (`r0 = 0`, ignored by `+0x12E54`), while `0x00E942CD` separately drives
    `0x0A4000` (`jack_id == 1`).

## Observations

1. **Startup `0xF2920010 == 0` forces active-low CID = 0 (`jack 2 = 1`):**
   `SonyUsbVbus` (`qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c`) currently
   returns `vbus->present` (`0` or `1`), leaving bit 1 at `0`. Both FUSB probe
   reads (`0x602ADB74` and `0x602ADB80`) read `0x00000000`.
2. **Boot CID kevent chain (`subtype 0` -> `event 9` -> `0x0A6000`):**
   `CUsbSvc::proc_kevent_otg` received three `subtype 0` (`r1=0`, `port=0`)
   events during boot:

| # | `kevent` payload | `byte0` | `UsbIfWrapper` (`+0x12E54`) | `param_1002` (`+0x12EA4`) | Effect on `[UsbControl+0xCC]` |
| --- | --- | --- | --- | --- | --- |
| 1 | `023e986000000000` | `2` | `r0=9`, `wrap_arg=00000000…` | `0` | `0` |
| 2 | `013e986000000000` | `1` | `r0=9`, `wrap_arg=01000000…` | `1` | `1` |
| 3 | `01ffffff00000000` | `1` | `r0=9`, `wrap_arg=01000000…` | `1` | `1` |

3. **QMP VBUS transitions remain `subtype 1`:** Both QMP attach (`byte0=1`)
   and detach (`byte0=2`) arrived at `CUsbSvc::proc_kevent_otg` with
   `subtype 1` (`r1=1`), passing `r0=0` to `UsbIfWrapper+0x12E54`, while
   `0x0A4000` on `SetJackStatus(1, ...)` stayed gated (`r0=0` at `+0x11042`)
   because `[UsbControl+0xCC] == 1`.

## Next boundary

**E0587:** Add an opt-in QEMU experiment setting bit 1 (`0x2`) at
`0xF2920010` (active-low CID floating / B-device idle) while keeping the
existing `E0580` `usb-vbus-test` qtest baseline unchanged, and verify that
`[UsbControl+0xCC]` stays `0`, `SetJackStatus(1, 1)` succeeds on QMP attach,
and `UsbControl::ProcessMsg` broadcasts `0x0A400A` (`MSGID_VBUS_ON_EVT`) into
the `UsbControl` FSM.

## Receipts

- `artifacts/qemu-a6400/e0586-usb-otg-kevent/prepare.py`, `fsm-trace.c`,
  `build.py`, `build-commands.txt`, `build.sha256`, `replay-vbus.py`
- Capture & verification: `vbus-console.log`, `vbus-report.json`,
  `correlate.py`, `correlation.json` (all checks exited `0`).
