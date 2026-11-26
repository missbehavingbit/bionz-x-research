# E0583 — Original USB VBUS events take the local synchronous send path

Date 2026-11-18. Offline QEMU follow-up to
[E0582](0582-qemu-usb-port-monitor-queue.md).

## Inputs

The E0582 kernel, boot manifest, `libUsbControl.so` and QEMU binary
identities apply. The boot uses the verified Linux-local
`inputs-moddev-1` copy, a disposable eMMC overlay, `ULOGIO=`,
`USB_VBUS_TEST=1`, `EVSEL_LATE_US=500` and
`-icount shift=0,sleep=on -net none -no-reboot`.
Each replay starts a shell, records the relevant guest library maps,
then issues QMP attach, duplicate attach and detach.

## Original allocation and send

In the first replay `libUsbControl.so` was mapped at `0x0472C000`.
The guest allocated an 8-byte wrapper against queue `0x00CA8192`
at `+0xA6FA` and got status zero at `+0xA6FE` for both transitions.
The original `osal_snd_sync_msg` call at `+0xA71C` and
`SendEvt` each returned zero, sampled at `+0xA720` and
`+0xA72A` respectively. The object pointers passed from
`EventListner` to the sender matched for each transition.

The receive-side replay recorded the original constructor using
category `0x6103` and message ID `0x0A4000` for both transitions.
The wrapper at `+0xA71C` contained the constructor's object pointer.
An ordinary `RcvMsg` on the same queue returned after each send, but
the objects extracted at `+0xAAA2` differed from the sent objects:

| Transition | Sent object | Object extracted by the ordinary receiver |
| --- | --- | --- |
| Attach | `0x08F61C20` | `0x08F61C30` |
| Detach | `0x08F70628` | `0x08F70580` |

This rules out a direct pointer match in the sampled ordinary receiver.
The contents and provenance of the different receive-side objects
remain unqualified.

## Original local OSAL path

`libosal_uipc.so::__osal_snd_sync_msg` tests bit 15 at `+0x3DB0`.
The local branch selects a queue object and an entry. It subtracts 12
from the payload pointer and calls the entry's function pointer at
`+0x3E7E` with the resulting header in `r0`. The instructions at
`+0x3E9A–0x3EAA` clear the synchronous flag and return zero through
`r8` on this callback path. The caller's status alone cannot prove
which original handler ran or what it returned.

The local-path replay matched the attach wrapper at `SendEvt+0x34`
to the original call at `libosal_uipc.so+0x3E7E`. A fourth replay
filtered those calls by the wrapper passed on queue `0x00CA8192`.
Both transitions passed the same wrapper pointer and bytes from
`libUsbControl.so+0xA71C` to `libosal_uipc.so+0x3180`.
The indirect target there was `0x0519D61D` with
`libUsbControl.so` mapped at `0x0518B000`. It resolves to the
original Thumb entry `UsbControl::funcOsalSyncCb` at `+0x1261C`.
The attach wrapper held object `0x000767C8`; detach held
`0x0018AF70`. Both were delivered through that synchronous callback.

The callback tail-calls `UsbControl::preProcess` at `+0x123B4`.
The disassembly identifies the `0x0A4000` case as a call to
`UFW::MsgReceiver::PostMsg` at `+0x12470`. That call and any
copy or ownership handoff linking the synchronous object to the
ordinary receiver remain the next runtime boundary.

## Receipts

The `e0583-usb-send/`, `e0583-usb-receive/`, `e0583-usb-local/`,
and `e0583-usb-callback/` directories under `artifacts/qemu-a6400/` contain
the bounded trace sources, disassembly, build commands, console and
QMP receipts. The receive replay includes `correlate.py` and its
`correlation.json` showing the sent object and the absence of a direct
pointer match at the instrumented receive sites. The original
instructions and mapped addresses were observed without modifying
the guest firmware, its decisions or USB data.
All four QEMU runs exited 0 via QMP. The third replay's generic
callback sample budget did not isolate detach. The fourth used
the recorded send wrapper to filter unrelated local callbacks.
