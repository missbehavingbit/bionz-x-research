# E0584 — UsbControl PostMsg handoff solved and ProcessMsg SetJackStatus suppression gate identified

Date 2026-11-18. Offline QEMU follow-up to
[E0583](0583-qemu-usb-local-sync-send.md).

## Inputs and tool identities

- **QEMU binary:** Isolated scratch `qemu-system-arm` SHA-256
  `8bcef2b519915f88c92a768d929007c80fb8d31d03a8c87590ab0c67c0fe9fd1`
  (same `sony-a6400` machine build as E0580–E0583; `build.sha256`).
- **TCG instrumentation plugin:** `artifacts/qemu-a6400/e0584-usb-postmsg/vbus-trace.so`
  SHA-256 `0cb5f6fb1c07c81f85060f3ea0afdd4d7da5bd70c3b8108febf006a4f0a03075`.
- **Guest binaries (verified `ILCE-6400` 2.00 dump):**
  - `libUsbControl.so` (94,220 B, SHA-256
    `70cd5742fde9eea78c60905e4015531b4c3ab2668d2cb85c934531f451f1211f`),
    mapped in `im.elf` at `0x04223000–0x04239000` (`r-xp`).
  - `libosal_uipc.so` (26,692 B, SHA-256
    `b6ded35add9582413b401cf4db78ec2c9a201b632e78c2731d6bbb379249b3eb`),
    mapped in `im.elf` at `0x0400a000–0x04010000` (`r-xp`).
  - `usb_portMonitor` kernel module live at `0x5F0A7000`.
- **Boot set and policy:** Linux-local `inputs-moddev-1` copy, disposable
  eMMC overlay, `ULOGIO=`, `USB_VBUS_TEST=1`, `EVSEL_LATE_US=500`, and
  `-icount shift=0,sleep=on -net none -no-reboot`. Offline QEMU test only;
  no physical camera/USB access and no guest firmware or USB data modification.

## Commands

```powershell
wsl.exe --exec python3 artifacts/qemu-a6400/e0584-usb-postmsg/prepare.py
wsl.exe --exec python3 artifacts/qemu-a6400/e0584-usb-postmsg/build.py
wsl.exe --exec env PYTHONPATH=. python3 -u artifacts/qemu-a6400/e0584-usb-postmsg/replay-vbus.py
wsl.exe --exec env PYTHONPATH=. python3 artifacts/qemu-a6400/e0584-usb-postmsg/correlate.py
```

All four commands exited `0` (`vbus-report.json`: `exit=0`, `owned_exit=0`;
QMP sequence: attach `true`, duplicate attach `true` with zero new IRQ 230 reads,
detach `false`).

## Original instruction support and serviced operations

Disassembly receipts (`postmsg-rcvmsg.txt`, `processmsg-main.txt`,
`jack-status.txt`) and live TCG trace sites in `libUsbControl.so` cover:

- `UFW::MsgReceiver::PostMsg` (`+0xA918`): unwraps `MWF::ObjMsg*` via
  `UFW::MsgUtil::GetCmdMsg` (`+0xA926`), heap-allocates an 8-byte `MWF::ObjMsg`
  (`+0xA92E`), copy-constructs it (`+0xA936`), allocates an 8-byte OSAL message
  wrapper (`+0xA942`), stores the copy pointer into `[wrapper]` (`+0xA968`), and
  posts it via `osal_snd_msg` on queue `0x00CA8192` (`+0xA96A`).
- `UFW::MsgReceiver::RcvMsg` (`+0xA9A8`): receives from `0x00CA8192`
  (`+0xA9C0`), extracts `[wrapper]` into `*out_msg` (`+0xAAA2..+0xAAA4`), and
  frees the OSAL wrapper (`+0xAAB2`).
- `UsbControl::Main` (`+0x112C4`): calls `RcvMsg` (`+0x112D8`) and dispatches
  `(msg_id, obj_msg)` to `UsbControl::ProcessMsg` (`+0x112E2`).
- `UsbControl::ProcessMsg` (`+0x10ED4`): handles `0x0A4000` (`+0x10F84..+0x110C6`),
  calling `SetJackStatus` (`+0x1103E`), branching on `r0 == 0` at
  `+0x11042..+0x11044` (`beq.w 0x1128c`) before `sendJackStatusToInput`
  (`+0x1104E`), `SetCateIdMsgId(0x6103, 0x0A400A/0x0A400B)` (`+0x11076`), and
  `BroadCastEvt` (`+0x110B0`). Case `0x0A6000` (`+0x110CC..+0x110F0`) extracts
  parameters `0x1003`/`0x1002` and calls `SetJackStatus(this, 2, param_1002)`.
- `UsbControl::SetJackStatus` (`+0x12980`) and
  `UsbControl::sendJackStatusToInput` (`+0x12B24`, posting 16-byte messages to
  queue `0x007A0020` at `+0x12B82`).

## Observations

### 1. Complete PostMsg-to-ProcessMsg pointer handoff (solving E0583)

`PostMsg` explains the E0583 pointer difference: it intentionally copy-constructs
`SendEvt`'s original `MWF::ObjMsg` into a newly heap-allocated `MWF::ObjMsg`
and sends that copy in a new 8-byte wrapper on queue `0x00CA8192`. Both post-boot
QMP VBUS transitions completed the full handoff end-to-end into
`UsbControl::ProcessMsg`:

| Stage | Offset | QMP Attach (`value=00000001`) | QMP Detach (`value=00000000`) |
| --- | --- | --- | --- |
| `EventListner` alloc (`0x6103`, `0x0A4000`) | `+0x13076` | `r0=0x0007DB18` | `r0=0x00269060` |
| `SendEvt` `osal_snd_sync_msg` (`0x00CA8192`) | `+0x0A71C` | `r1=0x045AA48C`, `wrapper=18db0700…` | `r1=0x045AEE8C`, `wrapper=60902600…` |
| `funcOsalSyncCb` | `+0x1261C` | `r0=0x045AA48C`, `wrapper=18db0700…` | `r0=0x045AEE8C`, `wrapper=60902600…` |
| `preProcess` -> `PostMsg` | `+0x12470` | `r1=0x045AA48C`, `r6=0x0007DB18` | `r1=0x045AEE8C`, `r6=0x00269060` |
| `PostMsg` `ObjMsg` copy-ctor | `+0x0A936` | `r1=0x0007DB18` -> `r0=0x0007EEF8` | `r1=0x00269060` -> `r0=0x00269050` |
| `PostMsg` `osal_snd_msg` (`0x00CA8192`) | `+0x0A96A` / `+0x0A96E` | `r1=0x045AA4AC`, `wrapper=f8ee0700…`, ret `0` | `r1=0x045AEEAC`, `wrapper=50902600…`, ret `0` |
| `RcvMsg` extract & store | `+0x0AAA2` / `+0x0AAAA` | `r5=0x045AA4AC`, `r3=0x0007EEF8` | `r5=0x045AEEAC`, `r3=0x00269050` |
| `UsbControl::Main` -> `ProcessMsg` | `+0x112E2` | `r0=0x08D01B68`, `r2=0x0007EEF8` | `r0=0x08D01B68`, `r2=0x00269050` |
| `ProcessMsg` `0x0A4000` (`0x1002`, `0x1003`) | `+0x10F84` / `+0x10F9A` | `r6=1`, `r0=0` (`r8=0x0007EEF8`) | `r6=0`, `r0=0` (`r8=0x00269050`) |
| `sendJackStatusToInput(4, 0)` from `+0x10FD6` | `+0x12B82` | *(not called)* | `r0=0x007A0020`, `0400000700000000…` |
| `ProcessMsg` -> `SetJackStatus` | `+0x1103E` | `(0x08D01B68, 1, 1)` | `(0x08D01B68, 1, 0)` |
| Post-check broadcast (`+0x1104E`, `+0x11076`, `+0x110B0`) | — | *(not reached)* | *(not reached)* |

### 2. Early boot vs. post-boot QMP behavior

During pre-QMP boot (before `__USB_STATE__`), three `0x0A4000` messages passed
through `ProcessMsg` cleanly and completed broadcast (`+0x110B4 r0=0`):

1. `0x00038D38` and `0x00038EC0` (two jack-1 detach messages with `r6=0, r0=0`
   at `+0x10F9A`) were dequeued at `+0x112E2` *before* the three boot
   `0x0A6000` messages (`0x00039030`, `0x0003A2A8`, `0x0003A358`). Both jack-1
   calls executed `SetJackStatus(0x08D01B68, 1, 0)` (`+0x1103E`), returned
   non-zero, proceeded to `sendJackStatusToInput(1, 0)` (`+0x1104E` ->
   `+0x12B82 r0=0x007A0020 input_msg=01000007000000000000000000000000`), called
   `SetCateIdMsgId(0x6103, 0x0A400B)` (`+0x11076`), and called `BroadCastEvt`
   (`+0x110B0`, returning `0` at `+0x110B4`).
2. After the three boot `0x0A6000` messages were dequeued and processed at
   `+0x112E2`, a third boot `0x0A4000` message (`0x08D04C00`, `r6=0, r0=1` at
   `+0x10F9A`) called `SetJackStatus(0x08D01B68, 3, 0)` and completed
   `sendJackStatusToInput(3, 0)` (`input_msg=03000007…`) and `BroadCastEvt`
   (`0x0A400B`, `r0=0`).
3. After boot (post-`__USB_STATE__`), both QMP attach (`SetJackStatus(1, 1)`)
   and QMP detach (`SetJackStatus(1, 0)`) exited immediately after `+0x1103E`
   without hitting `+0x1104E`, `+0x11076`, or `+0x110B0`. In addition, QMP
   detach hit `+0x10FD6` -> `+0x12B82` (`sendJackStatusToInput(4, 0)` on
   `0x007A0020` with `input_msg=04000007000000000000000000000000`), which the
   instructions at `+0x10FD0..+0x10FD4` (`ldr.w r3, [r5, #0xd0]; cbnz r3, 0x11016`)
   only execute when `[UsbControl+0xd0] == 0`.

### 3. Static disassembly proof of the suppression gate

In `jack-status.txt` (`+0x12980..+0x129D6`):

- For `jack_id == 1` (`+0x129A4..+0x129B0`), `SetJackStatus` reads
  `[r4, #0xcc]`: if `[r4, #0xcc] == 1`, it branches to `+0x129C6` (`movs r4, #0`)
  and returns `0` without writing `[r4, #0xd0]`; otherwise it stores `status`
  into `[r4, #0xd0]` and returns `1`.
- For `jack_id == 2` (`+0x129B2..+0x129B6`), `SetJackStatus` writes `status`
  into `[r4, #0xcc]` (and returns `0`). In `processmsg-main.txt`
  (`+0x110CC..+0x110F0`), `ProcessMsg` case `0x0A6000` reads parameters `0x1003`
  and `0x1002` and calls `SetJackStatus(this, 2, param_1002)`.
- For `jack_id == 3` (`+0x129B8`), `SetJackStatus` writes `[r4, #0xd4]` and
  always returns `1`, bypassing the `[r4, #0xcc]` check.

**Inference:** `[UsbControl+0xcc]` was not `1` when the first two boot
`0x0A4000` jack-1 messages ran, was set to `1` by one of the three subsequent
boot `0x0A6000` messages, and thereafter caused `SetJackStatus(1, 1_or_0)` on
both QMP attach and QMP detach to return `0`, take `11042: cmp r0, #0; 11044:
beq.w 0x1128c`, leave `[UsbControl+0xd0] == 0`, and suppress downstream USB
broadcast (`0x0A400A`/`0x0A400B`). Direct runtime sampling inside `0x0A6000`
(`+0x110CC..+0x110F0`) and `SetJackStatus` (`+0x12980`) is still needed to
verify `[this+0xcc]` and the `0x0A6000` sender/payload directly.

## Next boundary

**E0585:** Instrument `ProcessMsg` case `0x0A6000` (`+0x110CC..+0x110F0`),
`SetJackStatus` (`+0x12980..+0x129C6`), and the caller of `0x0A6000` to observe
the `0x1002`/`0x1003` parameters and `[UsbControl+0xcc]` directly, and determine
which emulated hardware or guest policy sets jack 2 (`[this+0xcc] = 1`) and
gates VBUS jack 1 transitions.

## Receipts

- `artifacts/qemu-a6400/e0584-usb-postmsg/prepare.py`, `fsm-trace.c`,
  `build.py`, `build-commands.txt`, `build.sha256`, `replay-vbus.py`
- Disassembly: `postmsg-rcvmsg.txt`, `processmsg-main.txt`, `jack-status.txt`
- Capture & verification: `vbus-console.log`, `vbus-report.json`,
  `correlate.py`, `correlation.json` (all checks exited `0`).
