# E0581 — Virtual USB cable events reach the original userspace USB service

Date 2026-11-18. Offline ILCE-6400 QEMU replay of [E0580](0580-qemu-usb-vbus-interrupt.md).

## Inputs and policy

The QEMU binary SHA-256 was
`8bcef2b519915f88c92a768d929007c80fb8d31d03a8c87590ab0c67c0fe9fd1`.
The original kernel and boot manifest have the E0580 identities. The matching
`usb_portMonitor.ko` and `libInfraUsbCmn.so` have SHA-256
`8c681fe93dca82bc7757848a12fb40b3040ba8026f92cc47ebf34990bb415478` and
`5338bafbb545ebc223fac15c9253a41ac9fe49c01fe32c73af40c3fb50eec0a6`,
respectively.

All runs use the Linux-local verified `inputs-moddev-1` and a disposable
eMMC overlay with `ULOGIO=`, `USB_VBUS_TEST=1`, and
`-icount shift=0,sleep=on -net none -no-reboot`. The consumer and userspace
traces also use `EVSEL_LATE_US=500`. They wait for stella and a shell,
read `/proc/modules` and `im.elf` maps from the guest, then issue QMP
attach, duplicate attach and detach. The duplicate observation lasted 12 s
and the post-detach observation lasted 8 s. Each run exited 0 via QMP.

## Original kernel and userspace behavior

- `event_vbus_exec` saw one valid port with index 0. It wrote port
  state 1 on attach and 2 on detach at `0x602B6300`, then invoked the
  same registered callback with events 7 and 8 respectively at
  `0x602B635C`. The callback address `0x5F0A8EF8` resolves using the
  observed module base `0x5F0A7000` to `usb_portMonitor_notify+0`
  (module `.text+0x1EF8`). That routine's original relocations show its
  OSAL message allocation and send calls.
- Both transitions called `usb_event_add_queue` at `0x602B5C14` and led
  to new visits to the original `usb_event_read` at `0x602B5D7C` for
  the same open file. The callback pointer `0x0492F0DD` in the final
  userspace trace resolves to Thumb `libInfraUsbCmn.so+0xB0DC` from
  the guest's `0x04924000` executable mapping. The earlier replay
  resolved a different absolute pointer to the same library offset.
- The original `CUsbSvc::proc_kevent_otg` at that offset executed
  with subtype 1 and an 8-byte payload for each transition. The first
  byte was 1 on attach and 2 on detach, with port 0 at payload `+4`.
  The two original backup calls returned 1, the saved port matched 0,
  and the forced-senser check returned 0 at `+0xB1A4`. Both went
  through the ordinary notification path at `+0xB344` and
  `+0xB36E`. The forced-senser request site `+0xB1D4` was absent.
- Before attach, the stock service had issued `SET_PORT_DESC`
  (`0x4024E301`), `SET_PARAM` (`0x4010E313`) and `START`
  (`0xE304`). Neither attach nor detach led to another
  `usb_otgcore_ioctl` visit or to the monitored FUSB gadget start,
  gadget-core start or DWC3 pull-up entries in the observation window.
- A fourth replay traced the port-monitor worker. On attach it consumed
  event 7 for port 0 and invoked the one-byte backup helper with key
  `0x0051000F` at module `.text+0x1B30`. The helper's original
  relocations are to `Backup_get_datasize` and `Backup_read`.
  The worker then entered `.text+0x17FC` with arguments `(3, 0, 2)`.
  On detach it consumed event 8 and entered the same routine with
  `(2, 0, 0)`. Neither transition reached either line-state query inside
  `usb_portMonitor_PrepareValues` (`.text+0x99C` and `+0xA40`).
  These measurements locate the port-monitor dispatch. They do not
  establish which later consumer chooses the USB gadget mode.

Register values above were read from original instructions. The userspace
observer matches instruction bytes and page offsets to locate candidates
across ASLR. It can report coincidentally matching instructions, so only
addresses inside the observed executable mapping at the expected
library-relative offsets were used. No guest application decision,
memory content or USB transaction was supplied by the observers.

## Receipts and boundary

The `e0581-vbus-port/`, `e0581-vbus-consumer/`, `e0581-vbus-userspace/`,
and `e0581-vbus-classifier/` directories under `artifacts/qemu-a6400/`
retain the trace sources, disassembly, isolated build commands, hashes,
replay recipes, console and QMP results. Run
`build.py` and `replay-vbus.py` in the same WSL session from the repository
root, with `PYTHONPATH=.` and the matching `vbus-trace.so` built
against QEMU 10.1. `e0581-vbus-userspace/prepare.py` generates the trace
source. The first consumer-trace source failed to compile because its
old scalar counter increment survived conversion to a per-site counter.
The failed source and corrected build are retained.

The next boundary is the port-monitor dispatch at module `.text+0x17FC`
and its consumers, alongside the ordinary userspace notification.
USB enumeration and streaming remain outside this evidence.
