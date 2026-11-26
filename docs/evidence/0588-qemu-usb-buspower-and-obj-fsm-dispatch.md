# E0588 — Unlocked QMP VBUS drives libBusPower FSM while UsbControl stays in STOP

Date 2026-11-18. Offline QEMU follow-up to
[E0587](0587-qemu-usb-cid-high-unlocks-vbus-broadcast.md).

## Inputs and tool identities

- **QEMU binary:** Isolated scratch `qemu-system-arm` SHA-256
  `c0aed410afced9ca22e6b08d3743ead046f5cc0d7feda7d8e7fc4d0e74ad1167`
  (`build.sha256`, `usb-cid-high=on` + `usb-vbus-test=on`).
- **TCG instrumentation plugin:** `artifacts/qemu-a6400/e0588-usb-fsm-state/vbus-trace.so`
  SHA-256 `4cad828d0a06564ab0f7d8e15148f8032f303738c7d7e4a818206787a7117ab6`.
- **Guest binary:** `libUsbControl.so` (94,220 B, SHA-256
  `70cd5742fde9eea78c60905e4015531b4c3ab2668d2cb85c934531f451f1211f`),
  mapped in `im.elf` at `0x048de000–0x048f4000` (`r-xp`).
- **Boot set and policy:** Linux-local `inputs-moddev-1` copy, disposable
  eMMC overlay, `ULOGIO=`, `USB_VBUS_TEST=1`, `USB_CID_HIGH=1`,
  `EVSEL_LATE_US=500`, and `-icount shift=0,sleep=on -net none -no-reboot`.
  Offline QEMU test only; no physical camera/USB access and no guest firmware
  or USB data modification.

## Commands

```powershell
wsl.exe --exec python3 artifacts/qemu-a6400/e0588-usb-fsm-state/prepare.py
wsl.exe --exec python3 artifacts/qemu-a6400/e0588-usb-fsm-state/build.py
wsl.exe --exec env PYTHONPATH=. python3 -u artifacts/qemu-a6400/e0588-usb-fsm-state/replay-vbus.py
wsl.exe --exec env PYTHONPATH=. python3 artifacts/qemu-a6400/e0588-usb-fsm-state/correlate.py
```

All four commands exited `0` (`vbus-report.json`: `exit=0`, `owned_exit=0`).

## Observations

1. **Boot state of `UsbControl` (`0x08E005E8`):**
   During boot, `UsbControl` received `0x0A2000` (`+0x11270` -> `DispatchEvent`
   `+0x10678` -> `MsgId2EventId` `+0x104AC`) and later entered
   `UsbControl::actionStopEntry` (`+0x11A18`, `lr=libUsbControl.so+0xABF1`).
2. **Downstream `UFW::Fsm::DispatchEvent` (`+0x0AFD2`) targets on QMP attach/detach:**
   Resolving `lr` against `/proc/$p/maps` (`r-xp`) and reading `[fsm+0x10]`
   (`current_state`) and `UFW::Fsm::handleEvent` (`+0x0AC5A`) return values
   identifies both downstream FSMs invoked after `BroadCastEvt`:

| Transition | Broadcast `MsgId` | `libBusPower.so+0x3B03` (`0x08E00D2C`) | `libObj.so+0x839C1B` (`0x08E016C8`) | `UsbControl::DispatchEvent` (`+0x10678`) |
| --- | --- | --- | --- | --- |
| QMP Attach | `0x0A400A` | `r1=1`, `state 0 -> 2` (`r0=0`) | `r1=2`, `state 0`, `r0=0xFFFFFFFF` (ignored) | *(not called)* |
| QMP Detach | `0x0A400B` | `r1=0`, `state 2 -> 0` (`r0=0`) | `r1=2`, `state 0`, `r0=0xFFFFFFFF` (ignored) | *(not called)* |

3. **Why gadget start did not trigger:**
   `libBusPower.so` reacted cleanly to both `0x0A400A` and `0x0A400B`, whereas
   `libObj.so+0x839C1B` (`0x08E016C8`, `state=0`) ignored `r1=2` and no
   application/server module (`libInfraMscServer.so`, `libInfraMtpServer.so`,
   `libInfraRemote.so`, `libObj.so`) sent `0x0A2000` (`StartGadget` command)
   to `UsbControl`.

## Next boundary

**E0589:** Trace `libObj.so+0x839C1B` (`0x08E016C8`, `state=0`, `r1=2`) and the
USB connection / mass-storage / PC-Remote mode controller in `libObj.so` /
`libInfraMscServer.so` / `libInfraRemote.so` to determine what guest UI/mode
state is required for `0x0A400A` (`MSGID_VBUS_ON_EVT`) to trigger
`0x0A2000` (`StartGadget`) into `UsbControl`.

## Receipts

- `artifacts/qemu-a6400/e0588-usb-fsm-state/prepare.py`, `fsm-trace.c`,
  `build.py`, `build-commands.txt`, `build.sha256`, `replay-vbus.py`
- Capture & verification: `vbus-console.log`, `vbus-report.json`,
  `correlate.py`, `correlation.json` (all checks exited `0`).
