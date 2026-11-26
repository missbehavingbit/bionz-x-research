# E0595 — Physical DWC3 hardware parameters and an opt-in QEMU profile

Date: 2026-11-18. Physical read-only MMIO in normal MSC, plus offline QEMU.

## Result and scope

The current model's GSNPSID matches the camera, but all nine GHWPARAMS
words differ. Two identical physical samples qualify a separate opt-in
`-global cxd90045-dwc3.captured-hwparams=on` profile. The historical default
is preserved for earlier replays. This corrects advertised register values;
it does not implement or qualify the capabilities encoded by those values,
USB gadget startup, EP0, bulk or isochronous transfers, or UVC streaming.

The E0594 original-service/descriptor-ownership boundary remains open.
No process-owned descriptor was closed or reused, and no camera USB mode
switch, module load, firmware hook, flash or SD write was performed.

## Physical method, identities and receipts

Consulted `RELATED_REPOSITORIES.md`, the live-camera workflow and retained
E0579/E0593/E0594. No upstream device contract was imported. Physical access
used the existing protocol-2 debugger, PID 700, usb_extcmd in normal MSC.
`/sys/class/udc/dwc3.0/state` was `configured`. Kernel version:
`3.0.27_nl-rt106+`, build `Tue Aug 10 13:03:39 JST 2021`. These observations
qualify this connected original ILCE-6400 context, not other camera bodies.

The fixed 724-byte ARM helper `dwc3_identity_readonly.S` opens `/dev/mem`
O_RDONLY, mmap2 maps exactly one page at `0xF020C000` PROT_READ/MAP_SHARED,
and performs ten aligned 32-bit loads at the offsets in the table below.
It writes only a 40-byte stdout result and its own process memory, unmaps,
closes and exits. The helper has no ioctl or MMIO store. Original ARM
instructions were inspected with arm-linux-gnueabi-objdump before use.
Its SHA-256 is `bccf385e81bde5597cb172decdbbd3006946a6341607a52919eee649a7205da6`.

`capture_dwc3_identity` stages only this helper under a unique `/tmp_bt`
name, checks prior absence, verifies upload readback, executes it and removes
only that owned file. Uptime, debugger info and `/tmp_bt` listings are retained
before/after. Existing unrelated `/tmp_bt` entries were preserved. This is
RAM helper staging, not literally zero filesystem writes: run 2's older
`writes=false` metadata refers to the intended read-only probe but is too
broad; the final tool reports `mmio_writes=false` and `ram_helper_staging=true`.

Receipts: `artifacts/hardware/e0595-dwc3-identity-{1,2,3}/`.
Run 1's dd attempt returned status 1 and no bytes; its stderr was redirected,
so the cause is unproven. No value is inferred from that failed read.
Runs 2 and 3 return the same 40 bytes, SHA-256
`e4bb258ae4b2be09f6f75a06375219c78058853c92ca2e71bd2248d122cceb3f`.
Run 3 uptime advances 2463.41 -> 2463.74 seconds; debugger info is unchanged,
all cleanup requests return 0 and the helper is absent from the final listing.

## Register comparison

Offsets are relative to physical `0xF020C000`.

| Register | Offset | Historical QEMU | Physical / captured profile |
| --- | --- | --- | --- |
| GSNPSID | 0x120 | 0x5533290A | 0x5533290A |
| GHWPARAMS0 | 0x140 | 0x20204008 | 0x4020404A |
| GHWPARAMS1 | 0x144 | 0x00241008 | 0x0160C93B |
| GHWPARAMS2 | 0x148 | 0 | 0x00000001 |
| GHWPARAMS3 | 0x14C | 0x10420089 | 0x06A4D085 |
| GHWPARAMS4 | 0x150 | 0 | 0x47822020 |
| GHWPARAMS5 | 0x154 | 0x04020200 | 0x04204108 |
| GHWPARAMS6 | 0x158 | 0x00000400 | 0x0A0A8020 |
| GHWPARAMS7 | 0x15C | 0 | 0x04881472 |
| GHWPARAMS8 | 0x600 | 0 | 0x00000A0A |

## Reproduction and native validation

```bash
arm-linux-gnueabi-gcc -nostdlib -static -Wl,--build-id=none -Wl,-e,_start \
  tools/native_camera/dwc3_identity_readonly.S -o NEW_BUILD/probe.elf
arm-linux-gnueabi-strip NEW_BUILD/probe.elf
```

```powershell
python -m tools.research.capture_dwc3_identity --helper NEW_BUILD/probe.elf --report-dir artifacts/hardware/NEW_CAPTURE
```

An automatic review rejected overwriting the separate upstream checkout's
device source because it could destroy uncommitted work. The replacement
build instead reads `ninja -t compdb -x`, compiles the repository overlay
into a new artifact directory and relinks against existing objects into
that same new directory. It writes neither upstream sources nor upstream
objects. Commands and build log are preserved.

```bash
python3 -m tools.research.qemu_dwc3_profile_check \
  --qemu-dir /home/administrator/sony-a6400-qemu-11.1.3 \
  --report-dir artifacts/qemu-a6400/NEW_BUILD
```

Native QEMU qtest passes all 20 MMIO reads: ten historical-default values
and ten captured-profile values. Constructed qtest inputs are not firmware
execution or hardware transfer verification.
Receipt: `artifacts/qemu-a6400/e0595-isolated-build-1/`.
Overlay SHA-256 `80bd68810c379d0eac958480651038a33d348dbcad07fe97dd7d7a54c0029d65`;
isolated QEMU SHA-256 `2ed2d3d9e7325316ba4251e57f30e1579a891f7085a9322c0c8cfbe2bef5600e`.

The original-firmware replay uses `qemu_usb_host_probe --captured-hwparams`
with that isolated directory, the E0593 plugin and captured-input manifest
SHA-256 `5aa4c878cd8214c5b503388f44d3d4f4512482d7ef37050beaadbe8b24906147`.
Receipt: `artifacts/qemu-a6400/e0595-captured-boot-1/`. Firmware startup and
the shell are observed. Before and after virtual attach, event-ring base/size,
DCTL and DEVTEN remain zero and DSTS remains `0x00400000`; GET_DESCRIPTOR
produces no IN reply. QMP quit and owned exit both return 0 in 59.2 seconds.
Thus the profile preserves this bounded boot and the earlier startup gap.
It does not prove the driver has consumed the new parameter values.

The first full test run recorded 2113 tests, 29 skipped and seven failures,
all in generated status/documentation synchronization after adding E0594.
After synchronizing the latest E0595 status, 43 focused status/host-probe
checks pass. Failed receipts remain under
`artifacts/tests/dwc3-identity-e0595/`; no failing run is labelled success.
The final full run passes 2113 tests, 29 skipped, in 39.780 seconds
(`full-suite-final.txt`). Status SVG and PNG outputs were refreshed.

## Remaining fidelity boundaries

The profile is a bounded identity correction. Current transfer code still
has unqualified behaviors, including immediate IN completion and a 1024-byte
buffer limit. These must not be treated as qualified UVC behavior. The guest
must start its gadget through original service contracts before end-to-end
USB observations can qualify those paths. Unknown features need explicit
stops or separately evidenced implementation, not success responses.
