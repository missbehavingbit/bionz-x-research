# E0587 — Opt-in usb-cid-high unlocks SetJackStatus(1, 1) and 0x0A400A/B FSM broadcast

Date 2026-11-18. Offline QEMU follow-up to
[E0586](0586-qemu-usb-cid-bit1-and-otg-kevent.md).

## Inputs and tool identities

- **QEMU binary:** Isolated scratch `qemu-system-arm` SHA-256
  `c0aed410afced9ca22e6b08d3743ead046f5cc0d7feda7d8e7fc4d0e74ad1167`
  (`qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` with opt-in `usb-cid-high=on`
  and `qemu-a6400/run.sh` `USB_CID_HIGH=1`).
- **TCG instrumentation plugin:** `artifacts/qemu-a6400/e0587-usb-vbus-unlock/vbus-trace.so`
  SHA-256 `fe50f88389d07c6d2f734c85514c642e0bad549da643c0aeb1deec85975c9f5f`.
- **Guest binary:** `libUsbControl.so` (94,220 B, SHA-256
  `70cd5742fde9eea78c60905e4015531b4c3ab2668d2cb85c934531f451f1211f`),
  mapped in `im.elf` at `0x055e1000–0x055f7000` (`r-xp`).
- **Boot set and policy:** Linux-local `inputs-moddev-1` copy, disposable
  eMMC overlay, `ULOGIO=`, `USB_VBUS_TEST=1`, `USB_CID_HIGH=1`,
  `EVSEL_LATE_US=500`, and `-icount shift=0,sleep=on -net none -no-reboot`.
  `usb-cid-high` defaults to `false`, preserving the `E0580` `usb-vbus-test`
  baseline. Offline QEMU test only; no physical camera/USB access and no guest
  firmware or USB data modification.

## Commands

```powershell
wsl.exe --exec python3 artifacts/qemu-a6400/e0587-usb-vbus-unlock/prepare.py
wsl.exe --exec python3 artifacts/qemu-a6400/e0587-usb-vbus-unlock/build.py
wsl.exe --exec env PYTHONPATH=. python3 -u artifacts/qemu-a6400/e0587-usb-vbus-unlock/replay-vbus.py
wsl.exe --exec env PYTHONPATH=. python3 artifacts/qemu-a6400/e0587-usb-vbus-unlock/correlate.py
```

All four commands exited `0` (`vbus-report.json`: `exit=0`, `owned_exit=0`).

## Observations

1. **Startup `0xF2920010` bit 1 = 1 keeps `[UsbControl+0xCC] == 0`:**
   With `USB_CID_HIGH=1`, both FUSB startup probe reads (`0x602ADB74` and
   `0x602ADB80`) read `0x00000002`, and both boot `0x0A6000` calls at `+0x110EE`
   passed `param_1002 = 0`, keeping `[0x00033140 + 0xCC] == 0` (`+0x110F4`).
2. **Unlocked QMP attach and detach through `ProcessMsg` and `UFW::Fsm::DispatchEvent`:**
   On QMP attach (`0xF2920010 = 0x00000003`) and QMP detach
   (`0xF2920010 = 0x00000002`), `UsbControl::SetJackStatus(0x00033140, 1, ...)`
   returned `1` (`+0x129CE` / `+0x11042`), updated `[0x00033140 + 0xD0]`,
   notified `Input` on queue `0x007A0020`, broadcast `0x0A400A` / `0x0A400B`
   (`r0=0` at `+0x110B4`), and reached `UFW::Fsm::DispatchEvent` (`+0x0AFD2`):

| QMP transition | `0xF2920010` | `[+0xD0]` before -> after | `SetJackStatus` ret | `0x007A0020` `input_msg` | `BroadCastEvt` (`+0x11076`) | `UFW::Fsm::DispatchEvent` (`+0x0AFD2`) |
| --- | --- | --- | --- | --- | --- | --- |
| Attach | `0x00000003` | `0` -> `1` | `1` | `010000070000000001000000…` | `0x6103`, `0x0A400A` (`r0=0`) | `fsm=0x0B900A24`, `r1=1`, `msg=0x0B923110` |
| Detach | `0x00000002` | `1` -> `0` | `1` | `010000070000000000000000…` | `0x6103`, `0x0A400B` (`r0=0`) | `fsm=0x0B900A24`, `r1=0`, `msg=0x0B9DC350` |

   Neither `UsbControl::actionGVbusON` (`+0x11F46`), `actionWUsbRsrcEntry`
   (`+0x119D8`), nor `actionGStartGadget` (`+0x120B0`) executed during the
   observation window.

## Next boundary

**E0588:** Inspect `UFW::Fsm::DispatchEvent` (`+0x0AFD2`) and the active state
of `0x0B900A24` (and `UsbControl`'s `0x0A400A`/`0x0A400B` consumer) to
determine why `event_id = 1` (`MSGID_VBUS_ON_EVT`) on `0x0B900A24` did not
enter `UsbControl::actionGVbusON` / `actionWUsbRsrcEntry` / `StartGadget` (e.g.,
whether `UsbControl`'s FSM starts in `STATE_ID_STOP` awaiting a UI/USB-mode
command before `STATE_ID_G_W_VBUS`).

## Receipts

- `artifacts/qemu-a6400/e0587-usb-vbus-unlock/prepare.py`, `fsm-trace.c`,
  `build.py`, `build-commands.txt`, `build.sha256`, `replay-vbus.py`
- Capture & verification: `vbus-console.log`, `vbus-report.json`,
  `correlate.py`, `correlation.json` (all checks exited `0`).
