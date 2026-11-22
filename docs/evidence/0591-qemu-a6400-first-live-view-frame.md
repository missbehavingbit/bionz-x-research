# E0591 — First firmware-driven live-view frame in QEMU

Date: 2026-11-18. Offline ILCE-6400 firmware 2.00, unmodified captured inputs
and a fresh qcow2 overlay per boot. No physical camera, SD card, or USB access.

## Result

Opt-in `LIVEVIEW_SOURCE=pgm:PATH` produced a new 640×480 rear-LCD image from
the original firmware's live-view path. All **583,680** active bytes of the
1,024×570 guest ch51 luminance plane matched the asymmetric PGM. All
**304,073** LCD pixels outside the guest's nontransparent OSD matched the
chart at the first qualifying snapshot. The matched phase-only control
programmed ch51 but had zero video bytes and black at all 304,073 equivalent
LCD pixels. A second snapshot repeated the result with a different OSD mask.
No firmware patch, ready flag, OSD edit, or direct host write to the panel
buffer was used.

| At 133 s host time | Phase-only control | Asymmetric PGM |
| --- | --- | --- |
| VPH panel enable `0xF3440144` | `0x01000233` | `0x01000233` |
| Ch51 luminance, SHA-256 (without row padding) | `548a2ea469e7e61369d18824c9676adde0bf0199e1f22148c69e09089b2e3ff0` | `9c0c75aff9e26d5852e24e5f6e3e969e3d873cf8e07a5c08e6067137fee67706` |
| VPH 0 PPM, SHA-256 | `fe2817156703d57dc2d1b147607d8df07eefa878df2971af947e5b011223c309` | `58f4f2c6302ec64ebe56b0d415c9a4b2eafe699409cdc6089aedbbf91995ccf1` |
| First Ydet word `0xF3015700` | zero | `0x000C6F8E`, also observed on the guest's native load |

At 110 s both LCD captures were still the identical opaque NTSC notice
(`b2705bc0…`) even though the chart was already in ch51. A normal rear-center
press dismissed the notice in both runs. The observer stops the CPUs before
capturing the actual ch51/ch53 descriptors and planes and a VPH head 0
screendump. It uses ch53's captured alpha, not a crop or screenshot-difference
heuristic, to identify pixels belonging to the Y8 layer.

Stock rear-LCD selection needs no `viewUnified2.so` patch. The firmware reads
Backup units `0x00F90427`/`0x00F90428` for GPIO port 0 and pin 2. At
`0x6361AC28` it polls `P0.2` and posts `KEY_OTHER_EYE_CONTACT` (`0x708B`);
low means eye contact. The published machine idles this input high with
`REAR_CONTROLS=1` (as in both replays), leaving the unselected baseline
unchanged. This is not a proximity-sensor or EVF power model.

## Original handoff

Addresses below are for the captured ORIL (`dd8f8d0c…`, VA base `0x635C6000`,
PA `0x035C6000`). The Thumb sites were matched by original byte anchors and
checked in the captured instruction listing. Do not reuse the older packaged
coordinates from [E0561](0561-qemu-a6400-module3-waits-on-ydet-statistics.md).

| Original step | Phase-only | PGM |
| --- | ---: | ---: |
| Sgc1 IRQ 35 handler `0x63769DA4` → callback `0x6376A908` | 386 | 377 |
| One-shot front-end write at `0x6376A972` | 0 | 0 |
| CamPig IRQ 36 handler `0x63CD521C`; acknowledgments at `0xF3000048` | 0 | 319 |
| EE promotion `0x63CCDF52` and completion `0x63CCD930` | 0 each | 319 each |
| Pending job invoked from completion at `0x63CCD946` | 0 | 312 |
| Ydet producer `0x63BFDF48` → getter `0x63CD996A` → publish call `0x63BFE030` | 0 each | 312 each |
| Original ARM copy reads from `0xF3015700`–`0xF301579C` | 0 | 12,480 (312 × 40) |
| Consumer's fresh-record store `0x63BFD64A` writes 1; counter increment at `0x63BD5B32` | 0 each | 312 each |

IRQ 35 alone was not a frame-completion signal. The Sgc1 callback checks
`[obj+0x20]` at `0x6376A908` and branches past the one-shot writes for
continuous mode. On `CXD90045`, `Sgc1::Stop` (`0x6376A766` → `0x6376A814`)
latches line position via `+0x214`, clears `+0x200` bit 0, and polls
`(+0x210 & 1) == 0`. The model clears the `Iif` input-frame run bit at
`0xF3120410` (`0x63CD482C`) at raster end so `StaCamCor` (`0x6391382E`) does not
abort with `CamCore Start Error 0x208`. Ordering `evsel` source 23 (`CAMOUT_V`,
`0x639303D0`, ch4) at `+250 µs` between source 29 (`Frame Start`, ch6, `0 µs`)
and source 30 (`HS Root`, ch5, `+500 µs`) likewise avoids `Lost CAMOUT_V` frame
stamps. The opt-in model uses Sgc1 raster end to clock only guest-armed CamPig
paths, publishes the window-derived Ydet words before asserting IRQ 36, and
lets the native EE callbacks publish and consume the records. On camera AIF
groups, `irq[0]` masks channel 1 (`0x00F0`) and `irq[1]` masks channel 3
(`0xF000`). The guest's paired RC0 AIF ch0/ch3 launch runs the emulated Y8
transfer to the guest-programmed DRAM destination (`RAM0` PA `< 0x40000000`),
alternating `0x14E90000` and `0x14FBC000` and raising IRQ 41 (`0x63A1F32C` →
`0x63A1F478` `VphUpdateYuvCall`). VPH ch51 scans the selected buffer (`0x400`
active bytes, `0x80` pad per line); ch53 is the independent OSD.

This proves the frame and statistics handoff, **not** release of the older
packaged step-32 wait (`0x032E`/`0x0326`). Both captured boots remained at
step **26** (`0x0323` requested, `0x0320` reported). The captured `0x0323`
case at `0x63BC2A74` needs input byte `+3 == 1`, supplied from object byte
`+0xD46` at `0x63BC2602`. Neither run requested or accepted `0x032E`.
The 500 µs event phase, nominal 36 MHz raster clock, and statistical scale
are model assumptions. The source and resize path are synthetic luma, not
validated RAW/color ISP, autofocus, or still/movie recording.

The [pinned a5000 CamPig](https://github.com/Oein/a5000-emulator/blob/6ad36bd7b0411d55a5920447ff51e3de01f8cb9b/qemu-overlay/hw/misc/cxd90014_campig.c)
and [AIF](https://github.com/Oein/a5000-emulator/blob/6ad36bd7b0411d55a5920447ff51e3de01f8cb9b/qemu-overlay/hw/dma/cxd90014_aif.c)
models provided reference contracts. The IRQ timing, register use, and
buffer handoff above were checked against this ILCE-6400 firmware.

## Replay and tests

| Input / tool | SHA-256 |
| --- | --- |
| Captured ORIL (`av-cam.bin`) | `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209` |
| E0524 boot manifest | `bf12cfa60d64560d6db6327e42e81fd5d81b96c883c90879228ed67364cdde6d` |
| [E0651](0651-qemu-a6400-stock-finder-monitor-rear-lcd.md) powered WBI sub-micro fixture (explicit replay assumption) | `3385ec340e931aa7b7dbb954da99e5b6239174ba90441eb2c905f01bcf5bbda9` |
| 8×5 binary PGM chart | `01ebbeff1124611ee8e0e439226aaf840dd92b9ae9f24d0655b44d84dba5cbe` |
| QEMU 11.1.3 frozen causal-v2 binary | `d3a61aa0d8af83cf96d9c3d8fa0b5f585a83f61733b0808f6e9986fe8941bf64` |
| Frozen causal-v2 QEMU source patch | `e1b4d607d6deb865eebc7a8a5af028c6f26a42e28bfbd956342550e76cfc1bad` |
| Passive TCG observer | `3fba5c198d4cfdf1e071227571cccf8882f02c659113e76450a7d3291f28fb05` |

The 384-byte A/C/B sub-micro replay was recovered from the captured `nflasha5`
WBI page at `0x03443000`, using the slots from
[E0527](0527-qemu-a6400-real-submicro-frames-power-on-holds.md). The powered
fixture changes Slot B TLV `0x03` from `00 40 00 02` (hibernate) to
`00 00 00 03` (powered) and updates its checksum. This is a declared device
input assumption, not a modification to guest firmware.

For the launcher command see [Experimental live-view input](../../qemu-a6400/README.md#experimental-live-view-input).
Detailed local receipts and observer sources (not included in this repository)
are under `artifacts/qemu-a6400/first-liveview-validation/`. The retained
replay command, with `QEMU` the frozen build directory, `QEMU_SRC` the matching
source tree, and `INPUTS` the verified E0524 boot input directory:

```bash
p=artifacts/qemu-a6400/first-liveview-validation
micro=artifacts/qemu-a6400/stock-rear-lcd-20261118/wbi-powered-submicro-384.bin
work=$(mktemp -d)
cc -O2 -Wall -Wextra -Werror -Wno-unused-parameter -fPIC -shared \
  $(pkg-config --cflags glib-2.0) -I"$QEMU_SRC/include/plugins" \
  "$p/observe.c" -o "$work/observe.so"
for source in control chart; do
  python3 -B "$p/replay.py" --qemu "$QEMU" --inputs "$INPUTS" \
    --submicro "$micro" --source "$source" --plugin "$work/observe.so" \
    --out "$p/NEW-$source" --at 90 110 133 155
done
python3 -B "$p/verify.py" --matched "$p/NEW-control" "$p/NEW-chart"
rm -- "$work/observe.so"
rmdir -- "$work"
```

The matched runs use `EVSEL_LATE_US=500`, the prior CamPig readback/Sgc1
event-A experiments for the phase-only control, and `-icount shift=0,sleep=on`.
Both ended by QMP quit with exit 0. An independent built-in
`LIVEVIEW_SOURCE=bars` replay (`bars-03` on the same `causal-v2` binary
`d3a61aa0…` and captured inputs, rear-center press at 120 s host, snapshot at
150 s host, QMP quit exit 0; receipts in
`artifacts/qemu-a6400/causal-liveview-lYastL/`) likewise matched **583,680 /
583,680** active `ch51` luminance bytes (raw `ch51` SHA-256
`090cd48f67faf8f137883bb508461ca79ba93fb3aaa11e5f2a7db10b98e088b3`) and VPH
head 0 PPM SHA-256
`4581a30e6ec11103bc1348dd0915262d1a2ac01295a3ba521dbcd19cb1f83174`
(**302,501 / 307,200** panel pixels matching before masking the live OSD, bar centers
`[235, 210, 170, 145, 106, 81, 41, 16]`, sequencer still at step 26); the
retained `bars-02` negative receipt captured before dismissing the NTSC banner
had full `ch51` bars occluded by the opaque modal. The independent native
device regressions (`A6400_QEMU_DIR="$QEMU" python3 -B -m unittest tests.test_qemu_liveview tests.test_qemu_emmc -v`)
passed all 12 cases, and the guarded Windows offline suite passed (`2,103`
tests run, `108` skipped, `0` failures). An earlier DrvFS console run did not
reach firmware scanout within its host budget; the retry used Linux-native
serial logging. The first causal build failed at QEMU realization because the
GPIO input and output shared a name. The corrected build uses distinct names
and was used for the matched and `bars-03` replays.

### Publication build on upstream `main`

Rebuilt QEMU 11.1.3 from the pinned source with the published overlay (binary
SHA-256 `fcebc98c5a5d0fa3d382a18bcb2ae4ad60bdff7634e1a3edcdafcd4248ab1edf`,
patch `1210b9b7debabe9dfa94c581687dd1974563919e8dc858b9b8113772bb35eb18`).
The initial build exposed the incoming DWC3 overlay's outdated `CharBackend`
type. The build passed after changing it to QEMU 11.1.3's `CharFrontend`;
USB diagnostics were disabled in both replays.

The same matched command reproduced the phase-only control and PGM result.
At 110 s, both packed-luma and LCD PPM hashes match the table above. The
PGM matched all 583,680 active ch51 bytes and all 304,073 LCD pixels outside
the OSD, while the control was black at those locations. The observer counted
538 native CamPig completions, 537 Ydet publications and fresh-record
increments, and 21,480 original result reads in the PGM run; the control
had zero in each category, despite delivering IRQ 35. Both runs quit with
exit 0 and still reported step 26.

The publication build passed all 11 native `test_qemu_liveview` and
`test_qemu_emmc` cases. Guarded Windows discovery on the isolated checkout
ran 2,021 tests with 177 skips and no failures or errors.

### Separate sequencer replays (2026-11-19)

The captured replay still stops at step **26**, request/report `0x0323/0x0320`.
The original `0x63BC2A74` guard ran 173 times without accepting the request.
The sampled `0x63BA7448` load copied byte `0x64319046` into the object at
`0x6440A278+0xD46`; all sampled values were zero. The other direct writer
at `0x63B929F8` did not run. A second passive replay found the ARM copy at
`0x63AC79A4` writing the first word of the selected `0x4E8`-byte buffer
and `0x63854B52` selecting slot 0. Both statically resolved callers of
`0x63854B44` pass `0x6431A854`, so byte `0x6431A856` is the upstream
field. The native producer at `0x63874FD4` writes it from three original
predicates and a separate global byte. A further replay observed the
body lens-detect port/pin `1/4`, zero at `0x6431B57C`, state `0x13`
at `0x6431B594` on the `0x6386C29C` path, and zero at `0x644A8C3E`.
`0x13` does not satisfy the original `0`, `0xF2`, `0xF4` checks on that
path. The boot reached the store of byte `0x6431A856` 226 times; sampled
stores were zero. A write watch within original ORIL found
`0xF0 -> 0x03 -> 0x13` at `0x6431B594`, written by `0x6386BDA8`,
`0x6386D3B2` and `0x6386D274` respectively. The last writer is the
literal `0x13` store in the original lens state machine, followed by
`0x6386ED6A`. The unresolved boundary is which original input and
completion should move that state forward, not missing Ydet data.

The captured ORIL with only the packaged preset substituted is a
diagnostic contrast, not a fix. Its sampled lens state was zero and
the native producer stored 1 at `0x6431A856`. However, the sequencer
never started (`step_count=0`) and the rear LCD stayed black in that
replay. Replacing the captured settings is not a proven way to boot
the captured firmware.

The reconstructed **packaged** replay uses the E0561 ORIL (`90d515cf…`)
and the original `CX62600_UC2` preset (`241c8521…`) both on disk and in
the bootloader preload. It retains the captured kernel, initrd, system
files and eMMC layout. This is not a byte-identical copy of the historical
disk. The E0651 powered WBI fixture, 500 us event delay, CamPig enable
readback and Sgc1 event-A policy were held fixed. The same QEMU 11.1.3
binary (`0a9b08341486dd3d9248a1cc9e6cf99e0b2d3bd52c25f8fd029ef963cb657ae7`)
and a fresh overlay were used on each run:

| Packaged ORIL with original preset | Phase-only control | `LIVEVIEW_SOURCE=bars` |
| --- | --- | --- |
| Original `0x032E` guard at `0x63BB3914` | 102 reads, sampled byte 0 | five reads: `0, 0, 0, 0, 1` |
| Native Ydet producer at `0x63BEE670` | 0 hits | 117 hits |
| Final sequencer state | step 32, `0x032E/0x0326` | step 42 of 43 |

Both runs exited 0 through QMP. The negative control stayed at step 32
across another 102 SYSV ticks. The packaged ORIL on the **captured**
preset and filesystem stayed at step 26 with either source and never
executed the `0x032E` guard. The original state transition with bars
therefore qualifies the older **step-32 predicate** in the reconstructed
boot; it does not release the stock captured settings' step-26 gate.
No sequencer variables, guest code, or Ready flags were changed.

Private receipts and bounded, instruction-anchored observers:
`artifacts/qemu-a6400/bringup-step26-01/`, `bringup-ready-source-01/`,
`bringup-captured-ready-predicate-01/`, `bringup-lens-state-owner-01/`,
`bringup-captured-package-preset-01/`,
`bringup-package-oril-{control,bars}-01/`,
`bringup-package-preset-control-02/`, `bringup-package-preset-bars-01/`
and `first-liveview-validation/{bringup_observe.py,observe_steps.c,
observe_ready_source.c,prepare_package_replay.py}`.

### Captured step 26: sub-micro lens rail acknowledgement

The captured lens state machine has a path that clears state `0x13`:
at `0x6386E200` it checks the rail status through `0x6386BA5C`, and when
that returns zero its original continuation can store state zero at
`0x6386ECCA`. When this profile uses sub-micro power control, firmware
puts the rail request in TX slot B TLV `0x0A` data byte 1 bit 0
(`0x6386ACDA`) and reads the reported state from RX TLV `0x03` data
byte 3 bit 0 (`0x6386AC28`/`0x6386AC2E`). The powered replay fixture
fixed this status bit high, including when firmware requested off.

`SUBMICRO_LENS_POWER=1` (`submicro-lens-power=on`) lets only that status
bit follow the request and recomputes the CRC. Instant settling at the
SPI exchange is a model assumption; no attached lens, UART reply, Ready
store, guest patch or completion event is supplied. The qtest checks
both levels, missing/short TLVs, and the original reply's other bytes.

`bringup-lens-rail-{on,off}-01` use captured ORIL and settings, the same
powered fixture and bars source, QEMU
`0af1091c82470adb5b5cf952e798f9c67d685871de7402faacfe0f3067635534`,
fresh overlays and `-icount shift=0,sleep=on -net none -no-reboot`. Both
take a first snapshot at 112 wall seconds and a second after 135 more
display ticks; both quit via QMP with exit 0.

| Captured ORIL/settings with bars | Frozen rail reply | Request-following rail reply |
| --- | --- | --- |
| Last Ydet register sample | `0x0051525C` | `0x0051525C` |
| Native producer at `0x63874FD4` | Sampled zero | Sampled `0, 0, 1`, then `1` |
| Original `0x0323` guard and accept store | 136 reads, sampled zero; no accept stores | Six reads of `1`; six stores of `0x0323` |
| Final sequencer step | 26 (`0x0323` / `0x0320`) | 42 |

This releases the captured step-26 gate; the earlier packaged
step-32 Ydet comparison remains independent. Neither result alone
demonstrates still playback, JPEG capture, or physical lens behavior.
