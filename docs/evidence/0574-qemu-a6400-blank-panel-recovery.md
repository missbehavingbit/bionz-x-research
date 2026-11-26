# E0574 — Distinguish blank QEMU output from a disconnected panel

Date: 2026-11-17. Offline follow-up to E0573.

The long-running panel at port 8641 returned a 640 × 480 RGB frame with
every pixel black before and after MENU, DISP, and MENU again. The four
PNG digests matched. This qualifies the reported empty display as actual
QEMU output. The run had shown the original firmware menu earlier.
Console 0 had previously selected both OSD buffers. The old server did
not expose a second monitor, so the OSD registers and buffers at the
late failure were unavailable.

An independent replay used the E0573 binary, `REAR_CONTROLS=1`,
`REAR_LCD_DIAGNOSTIC=1`, `EVSEL_LATE_US=500`, and
`-icount shift=0,sleep=on`. The bounded observer paused QEMU around each
snapshot, read `0xf3440144` and AIF ch53 at `0xf3441300`, took a VPH
head 0 screendump, and used QMP `pmemsave` on the two known RGBA4444
buffers at `0x23a80000` and `0x23b16000`.

| Host seconds | Surface | Ch53 base | Pixels with nonzero alpha in A / B |
| --- | --- | --- | --- |
| 48, 60, 72, 90 | All black | 0 | 0 / 0 |
| 120 | Visible | `0x23a80000` | 687 / 0 |

SYSV count increased from `0x10` to `0x40` over these samples. These
pre-first-frame blanks came from an unprogrammed output while the guest
was still booting. The bounded replay did not reproduce the transition
from a visible menu back to a persistent blank. The cause of that
transition remains open. A separate shell-driven replay reached
`SetContentTarget` after 56.5 s and its original screen contained 3808
nonblack pixels. Its first HMP buffer dump commands failed because the
filename was unquoted. The QMP observer supplied the successful reads.

The HTTP service now reports `X-Camera-LCD: blank` or `visible` from the
RGB bytes. The page distinguishes a valid blank frame from a failed
request and clears a stale image when the request fails. The service
was restarted on port 8641. The live browser displayed the original
Sony menu after MENU and reported `Live QEMU LCD`. No display model,
camera or SD card state was changed by this correction.

Receipts: `artifacts/qemu-a6400/panel-blank/`, including the live
HTTP headers, frames, original-run black PNGs, bounded replay logs
and `observe.py`. Next, retain the ch53 descriptor and both buffers
through a post-menu blank so the next change can target the condition
that caused it.
