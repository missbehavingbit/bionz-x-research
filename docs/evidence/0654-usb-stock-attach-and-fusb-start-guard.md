# E0654 - Stock USB attach and the original FUSB start boundary

Date: 2026-11-19. Offline ILCE-6400 only. No physical camera, SD, USB
device, module load, or streaming operation.

## Result

- **QEMU:** The original powered guest receives late virtual VBUS edges,
  but does not start a stock gadget. DWC3 RUN remains clear, the UDC is
  `not attached` at `UNKNOWN` speed, and the host receives `NAK 0` rather
  than a DEVICE descriptor. No Sony data transfer was verified.
- **Unicorn:** An opt-in original ARM callback replay advances the original
  `im.elf` process past `SET_MODE(2)`. With the connected FUSB inputs from
  E0597 it stops at the actual `dwc3_gadget_otg_probe` call. Original
  creation, SETCONFIG production/delivery and complete teardown are
  unverified. The default policies still stop at the old boundary.

The original VBUS local synchronous send from
[E0583](0583-qemu-usb-local-sync-send.md) is not evidence that the view
started a gadget. [E0598](0598-qemu-token-driven-ep0-configuration.md)
enumerates the explicitly started **HID** function, not stock MSC or PTP.
[E0650](0650-qemu-virtual-media-gates.md) reads a sector from a separate
non-Sony fixture. Neither meets the stock-transfer gate.

## QEMU: powered stock decisions and the readiness branch

The [host probe](../../tools/research/qemu_usb_host_probe.py) adds optional
early and late cable schedules. The final late schedule waits for a
nonzero original OSD DMA base and enable bit 1, captures the rear LCD,
sends an ordinary rear-center key and samples the original OSD again.
It reads guest process maps and UDC state and requests only the standard
DEVICE descriptor over the virtual host link. No mods, USB ioctl helper,
backup changes or readiness writes are supplied. The `strict-ep0`,
`captured-hwparams` and read-only `captured-start-gate` policies are
explicitly selected; none starts the guest gadget. Each run uses a fresh
disposable eMMC overlay.

Boot input manifest SHA-256:
`bf12cfa60d64560d6db6327e42e81fd5d81b96c883c90879228ed67364cdde6d`.
Kernel: `d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618`.
Powered A/C/B replay:
`3385ec340e931aa7b7dbb954da99e5b6239174ba90441eb2c905f01bcf5bbda9`.
Existing isolated QEMU 11.1.3:
`823fbee11e7aff4fba51a17ab9d733694dd6f21e7bbaa30180b3e3f8a56d62b7`.
The base eMMC SHA-256 remained
`18e37ec3b3e56d26d31c0b0a4fd91ff10d0d1cb8a23308e49c35abd5cd3cc94b`.

The observer uses 16 matching loaded bytes at each ARM/Thumb site, then
reads registers. The [site generator](../../tools/research/build_usb_attach_trace.py)
pins the three captured library identities from
[E0619](0619-native-usb-release-model-camera-readiness.md). The libraries
were recovered with `debugfs -R 'dump /lib/NAME NAME'` from the saved
`nflasha15` image (`70d2613fcd24b13cfc4d75e98f435171fdfc50a6bc64d408e2df42ffb8197064`,
unchanged before/after). ARM attributes confirm ARMv7-A / Thumb-2; offsets
below are ELF VAs, not runtime PCs or Ghidra image-base coordinates.

The native observations refine E0619's last `ModelCamera` not-ready store:

- The original backup call at `libObj+0x4AA01A` uses key `0x00030143`,
  succeeds, and the branch at `+0x4AA098` observes word `0` in the paired
  frame. The checksum-validated saved BK4 contains `00` for that key. The
  captured USB Connection value `0x0107001F = 04` is a different property.
- At `+0x4AA094` the original branch sees `this+0x61E4 = 1`, then at
  `+0x4AA0A0` calls the original readiness setter with `0` for that same
  object. This nonzero byte takes the false branch without testing the
  backup word.
- `+0x4E5248` calls `+0x4AB8C4`; comparison with original instructions
  identifies the `NeutrOn` store at `+0x4AB912`. It writes `1` to exactly
  `this+0x61E4`. The original caller then invokes the readiness helper
  at `+0x4E524E`. In the final replay `this = 0x0031EA20`; the store has
  `r3 = this+0x6100` and `r2 = 1`, the branch reads the resulting `1`,
  and the setter receives the same `this` and `false`. `+0x4AA194` is a
  native zero-store candidate for the same field; it is not observed.
  The event that should take that path is not yet qualified.

Local run receipts under `artifacts/qemu-a6400/`:

| Run | Observed boundary |
|---|---|
| `usb-stock-13a3-after-01` | Late attach: IRQ 230 advances on cable insertion; no DWC3 interrupt, no MSC/MTP/Remote server in sampled maps, RUN clear, `NAK 0`; QMP quit 0 |
| `usb-stock-13a3-early-01` | Cable-present readback before the firmware marker; no handler IRQ count at the attached sample; duplicate true is not a new edge. No gadget, `NAK 0`; QMP quit 0 |
| `usb-stock-13a3-traced-01` | Late attach/detach uses original `SetJackStatus`; IRQ 230 advances `0 -> 1 -> 2`; no gadget, `NAK 0`; QMP quit 0 |
| `usb-stock-13a3-traced-ready-02` | Passive readiness branch observations, but the probe incorrectly waited for the luma-enable bit instead of the OSD-enable bit. Timed out before cable insertion; not an attach result |
| `usb-stock-13a3-final-stock-03` | Corrected OSD gate; native rear LCD shows `P` before insert. Original `SetJackStatus(1,1)` and `(1,0)`, IRQ 230 `0 -> 1 -> 2`, IRQ 229 stays 0, RUN clear, `NAK 0`. No qualified `ActionOpenUSB` or `StartGadget` hit. Owned QMP exit 0 in 233.9 s |

Final passive plugin SHA-256:
`ebcdb3108ca426a429d3131e3cae94b7a40586874db4ffae3d0b7f9b4d4354e0`.

Reproduce the final experiment from the repository root with the prepared
inputs and a fresh output directory:

```powershell
.\.venv\Scripts\python.exe -m tools.research.build_usb_attach_trace `
  --kernel <prepared-Image> --libraries <checked-library-directory> `
  --report-dir artifacts\qemu-a6400\NEW-TRACE
```

In WSL, with the qualified QEMU 11.1.3 headers:

```bash
cc -O2 -Wall -Wextra -Wno-unused-parameter -fPIC -shared \
  $(pkg-config --cflags --libs glib-2.0) \
  -I"$QEMU_SOURCE/include/plugins" -Iartifacts/qemu-a6400/NEW-TRACE \
  qemu-a6400/tools/usb_startup_trace.c -o artifacts/qemu-a6400/NEW-TRACE/trace.so
python3 -B -m tools.research.qemu_usb_host_probe \
  --qemu-dir "$ISOLATED_QEMU" --inputs "$PREPARED_INPUTS" \
  --powered-submicro "$POWERED_REPLAY" --stock-attach after-notice \
  --captured-hwparams --plugin artifacts/qemu-a6400/NEW-TRACE/trace.so \
  --seconds 260 --report-dir artifacts/qemu-a6400/NEW-ATTACH
```

The probe exits 2 on a stock boundary, even when the VM quits cleanly.
Next QEMU gate: observe what clears the native `NeutrOn` state and allows
the release helper, then require original `ActionOpenUSB`, demand-loaded
server, class-qualified enumeration, and a real PTP `GetDeviceInfo`
data and response or a Sony MSC BOT read with valid status. Do not send
streaming requests to make up for the missing transaction.

## Unicorn: SET_MODE and E305

With the E0534 bootstrap SHA-256
`a749522fab13fb0430a556d0284b356031efc0714f4906fb43c8f6eb40037208`,
diagnostic function 4 still registers an original process-owned gadget
and reaches FSM 7. The original captured ARM kernel (same SHA-256 as the
QEMU kernel above, VA bias `0x60108000`) establishes:

| Native site | Observation |
|---|---|
| `0x602AD970`, `0x602B646C` | FUSB init installs ops `0x604E4CF4`; the OTG core bind publishes the owning subobject at FUSB context `+8` |
| `0x602B7C24..0x602B7C78` | Mode 1 supplies selector 1; mode 2 supplies selector 0. Ops `+0x3C` is `fusb_set_speed` (`0x602AC96C`), not a gadget-start function |
| `0x602AC988` | Original store: `[r0+0x88]`, i.e. FUSB context `+0x90`; distinct from CID `+0x84` and VBUS `+0x88` |
| `0x602AD280` | Original VBUS writer; the E0597 start guard's read of FUSB `+0x88` is not link speed |
| `0x602B2184`, `0x602ACD6C` | DWC3 binds its gadget object to FUSB context `+0xBC`; bound start callback is `0x602B1B9C` |

Reverify 0.11.0 verified the table-slot and instruction bytes at the
translated file offsets; adjacent table bytes are not the published
table base. Its SHA-keyed ledger retains both observations. PyGhidra's
shared project was locked and was not changed. The behavioral evidence
is the bounded original ARM replay, not the byte verdicts.

`--otg-connected-kernel` runs the two original FUSB bodies on a separate
Unicorn CPU with bounded scratch state; it does not boot the kernel,
execute the full ioctl dispatcher or supply a DWC3 object. The E0597
connected inputs (FUSB idle `2`, CID `1`, VBUS `1`, `0xF0200024 = 1`) are
declared fixtures, not a new capture of the Unicorn checkpoint.

| Run in `artifacts/execution/` | Result |
|---|---|
| `usb-gadget-13a3-baseline-01` | Existing verified policy stops at `SET_MODE`, created=0 |
| `usb-gadget-13a3-connected-01`, `-02` | Actual SET_MODE callback stores selector 0 and returns 0. Native E305 guard reaches `BLX` at `0x602ACB04`, target `dwc3_gadget_otg_probe` at `0x602B1B9C`. No E305 acknowledgement. Userspace SVC `0x5029B078`, fd 11, arg `0x59CD6D8F`, TID 1024. Service state 6, created=0, nine live allocations |
| `usb-gadget-13a3-clear-01`, `-02` | Clear-bit negative control returns native `-EBUSY` (`-16`, return instruction `0x602ACB2C` in final receipt). Original create returns 14, service enters error state 7, created=0. Start completion remains pending at the instruction bound; not a teardown pass |

The new provider is not persisted in the boundary checkpoint. The
checkpoint retains the unanswered E305 SVC retry, not a successful return.
Reproduce with:

```powershell
.\.venv\Scripts\python.exe -m tools.research.probe_usb_gadget_startup `
  --checkpoint <trusted-e0534-bootstrap> --checkpoint-sha256 <pinned-digest> `
  --exercise-startup --function 4 --otg-connected-kernel <captured-Image-under-artifacts> `
  --report-dir artifacts\execution\NEW-START
```

Add `--otg-gate-clear-control` for the negative case. Both stop with exit
2. Next: qualify the original bound DWC3 object, its initialization and
gadgetcore commit. Only after a real start can the process-owned event
reader, notification, local sync-to-async SETCONFIG and balanced cleanup
be verified together. [E0524](0524-original-local-osal-setconfig-route.md)
still proves only the isolated local route, not this lifecycle.

The [upstream #4](https://github.com/ivica00/bionz-x-research/issues/4)
`flags=1` lanes are separate from that process-local delivery. The
[offline overlay](https://github.com/ivica00/bionz-x-research/issues/2)
and [video DMA](https://github.com/ivica00/bionz-x-research/issues/5)
issues are not resolved by these USB startup experiments.

## Validation

With explicit trusted checkpoint path/hash and `A6400_USB_CAPTURED_KERNEL`
set, the focused suite passed **113 tests** (no skips). It covers the
original callback, negative EBUSY, default refusal, original process replay,
isolated SETCONFIG, checkpoint retries, OSD gating and host EP0 helpers.
Full offline discovery during the change ran **2,156 tests, 120 skipped,
zero failures/errors** using E0534's physical-volume-denied, per-case-GC
runner with `venv`. The final OSD-bit fix was checked by the focused suite
and the native replay. Logs: `artifacts/tests/usb-13a3-{full-01,focused-final}.log`.
