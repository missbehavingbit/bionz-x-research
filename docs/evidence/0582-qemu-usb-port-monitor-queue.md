# E0582 — Original USB port monitor sends attach and detach to UsbControl

Date 2026-11-18. Offline QEMU follow-up to
[E0581](0581-qemu-vbus-port-and-userspace-delivery.md).

## Inputs and observation

The main kernel, captured boot manifest and `usb_portMonitor.ko` use
the E0581 identities. The isolated QEMU binary SHA-256 is
`8bcef2b519915f88c92a768d929007c80fb8d31d03a8c87590ab0c67c0fe9fd1`.
The matching original `libUsbControl.so` SHA-256 is
`70cd5742fde9eea78c60905e4015531b4c3ab2668d2cb85c934531f451f1211f`.

The guest used the verified Linux-local `inputs-moddev-1` boot set and
a disposable overlay, with `ULOGIO=`, `USB_VBUS_TEST=1`,
`EVSEL_LATE_US=500` and `-icount shift=0,sleep=on -net none -no-reboot`.
After stella and a serial shell, the observer recorded the port-monitor
module base and USB library mappings, then sent QMP attach, duplicate
attach and detach. The original port-monitor module was at `0x5F0A7000`.

## Original state transition

| Cable transition | Diagnostic at `.text+0x1384` | Message words at `.text+0xE60` | Queue |
| --- | --- | --- | --- |
| Attach | Operation 3, port 0, state 4 | `{1, 0, 0}` | `0x00E942CD` |
| Detach | Operation 2, port 0, state 6 | `{0, 0, 0}` | `0x00E942CD` |

The state handler called the original writer at `.text+0xE60`.
Relocations at `+0xE8C` and `+0xEFC` identify
`osal_valloc_msg_wait` and `osal_snd_msg`. The original stores at
`+0xEE8–0xEF0` serialize the three words into a 12-byte message. The
queue literal is at `+0xFA0`. Both post-send samples at `+0xF00`
returned zero. The two state numbers and the payload are observations
from a QEMU replay. Their wider enumeration remains unqualified.

In the original `libUsbControl.so`, `UsbIfWrapper::doInitialize`
registers the callback at `+0x131A4` for queue `0x00E942CD`. The
callback loads message words 0 and 1, calls the routine at
`+0x13054`, and returns the message through `osal_free_msg`.
The routine at `+0x13054` builds `MWF::ObjMsg` category
`0x6103`, ID `0x0A4000`. [E0323](0323-usbcontrol-msgid-eventid-map.md)
maps this ID to FSM event 2.

The receiver replay directly observed the 12 message bytes at
`0x0462F1A4` within the guest's `0x0461C000` executable mapping. The
callback received `010000000000000000000000` after attach and
`000000000000000000000000` after detach. At `0x0462F1AC` the first two
words were in `r0/r1` as `1/0` and `0/0`, and both reached the
`+0x13054` event-building routine. QEMU exited 0 by QMP in the
queue and receiver replays.

A further replay mapped `libUsbControl.so` at `0x0527F000`. Both
cable changes reached the original `EventListner` call at `+0x13144`
and `UFW::MsgSender::SendEvt` at `+0xA6E8` with matching object
pointers within each transition. The original `SendEvt` allocates
through the queue ID in its sender object at `+0x10` and calls
`osal_snd_sync_msg` at `+0xA71C`. The trace established entry into
`SendEvt`; it did not sample that call or its return. Generic
`UFW::Fsm::DispatchEvent` entries with event 2 were also observed,
but their messages were not correlated to these two send requests.
The monitored gadget-start entries were absent at their qualified
addresses in the bounded replay. QEMU exited 0 via QMP.

## Receipts

The directories `e0582-port-dispatch/`, `e0582-port-queue/`,
`e0582-port-receiver/`, and `e0582-usb-fsm/` under
`artifacts/qemu-a6400/` contain original
instruction slices and relocations, the bounded trace sources, build
commands and hashes, and replay consoles. The first replay timed out
waiting for the full `/proc` snapshot. A retry filtered the module and
library rows in the guest and used a 35 s snapshot deadline. The initial
receiver trace excluded the randomized `libUsbControl.so` base
`0x079AD000`; the final trace raised the search ceiling and used the
observed executable mapping to exclude coincidental instruction matches.

These offline observers supplied no guest memory contents or device
transactions. Next, observe the allocation and synchronous-send
results inside `SendEvt`, correlate the original object to the
receiving USB Control dispatch, and follow its state decision.
