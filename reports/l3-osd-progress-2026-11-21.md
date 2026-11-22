# L3 progress report - 2026-11-21

## In one paragraph

The emulator no longer dies at the camera app's first screen allocation. It now
uses the camera's real screen memory: it opens the captured OSD A buffer
(640x480, 614,400 bytes at physical 0x23A80000), allocates it, maps it, writes a
full black frame, and loads the UI module viewUnified2.so. The app then waits in
a 20 ms loop, so no visible UI is drawn yet.

## What was wrong before

- The app asked the graphics service to open DMM unit 0x4092e.
- The emulator only had evidence for unit 0x280 (gl_common), so it refused with -9.
- The app treated that as fatal and aborted (tgkill/SIGABRT) at 200,429,509
  instructions.
- A separate resume bug also fabricated a many-minute wait for a network select.

## What was found

- The camera's preserved memory image contains its own DMM unit table.
- It has two records for 0x4092e, indices 28 and 32.
- Their extents are exactly OSD A and OSD B: 0x23A80000 and 0x23B16000,
  0x96000 bytes each, 640x480, extent id 0x62.
- Handles follow the captured rule (0x400 | index): 0x041C and 0x0420.
- Calibration: gl_common unit 0x280 -> 0x202E0000, 32 MiB, handle 0x0417,
  matching earlier evidence.

## What changed

- New opt-in graphics mode `captured-e0044-dmm`. Defaults still refuse unit
  0x4092e.
- Checkpoint resume: the restored graphics policy is now recorded, and an
  explicit conflicting request fails closed instead of being silently ignored.
- Checkpoint restore no longer re-arms a poll timer from a select writefds
  pointer.
- Graphics transaction labels now name the OSD mapping instead of claiming
  gl_common.
- Tests: 2,152 run, only the existing tkinter import error, 129 skipped.

## Proof from the run

- Checkpoint built under the new mode at 141,980,014 instructions.
- Open of unit 0x4092e at 195,754,532 -> (0, 0x23A80000, 0x96000, 0x041C).
- Allocation of 0x96000 from handle 0x041C at 195,763,557 -> 0x23A80000.
- /dev/stream mapped: virtual 0x1BCD0000 -> physical 0x23A80000, length 0x96000.
- Full surface written once: 153,600 four-byte writes = 614,400 bytes, TID 1087.
- viewUnified2.so present in the process mappings.
- Run ends at the 300,000,000-instruction budget with TID 1087 runnable.

## What is still missing

- The written frame is all zero: a black clear, not a drawn screen.
- The app idles in a fixed 20 ms wait after the clear.
- The missing piece is the trigger that makes the UI draw: input, a display
  completion, or a service reply. The graphics service is no longer the blocker.

## Evidence and commits

- Evidence 9001 to 9006 under ../docs/evidence/
- Commits on gemini/l3-heap-overrun: b932732, f8f79bd, 7729b2f, f2b7021,
  6e84aea, c993076.

## Limits

- The DMM table comes from a preserved memory image, not a live camera.
- Two captured 0x4092e records exist; the mode uses the first (OSD A)
  explicitly.
- No camera, USB or SD access; no push.

## Relation to the mainline policy

The run used an opt-in `captured-e0044-dmm` policy in the investigating
checkout. The mainline tree implements the same `0x4092e` -> OSD A/B mapping as
`captured-wbi-dmm`; this report is independent confirmation of that mapping and
of the 20 ms wait / black-frame boundary, not a new mode.
