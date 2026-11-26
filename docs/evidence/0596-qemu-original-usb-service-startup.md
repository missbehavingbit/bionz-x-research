# E0596 — Original USB service commands inside the captured QEMU process

Date: 2026-11-18. Offline QEMU only. No camera/USB/physical SD access.

Follow-up: [E0597](0597-qemu-original-gadget-start-and-device-descriptor.md)
identifies this run's CUsbSvc state 7 as the E305 error path (-EBUSY),
then separately observes successful service state 3, created=1 and the first
DEVICE descriptor with an explicit physical start-gate snapshot policy.

## Result and fidelity scope

A QEMU-only diagnostic mod now sends the E0534 bus-power configure/start
commands from the firmware's existing UI thread through the original
constructors, allocator, MsgSender and USB receiver/service threads.
Configure returns 0 and the real UsbControl FSM transitions from 1 to 5;
start returns 0. The original kernel logs `usb_otgcore: transfer to Gadget`.
This passes the E0594 separate-open boundary without closing the owner's
descriptor, replacing ioctls, writing FSM state or fabricating hardware events.

These are explicit diagnostic input commands, not a stock application decision
or a complete camera boot. Bus-power function mask 4 is not a UVC function.
The observed event ring, DCTL.RUN and descriptor replies remain absent.
No UVC enumeration, video transfer, successful STOP or full teardown is claimed.

## Identity problem discovered before any command was sent

The original package inputs used in E0534 are not byte-identical to the
captured filesystems booted by QEMU. The initial mod refuses before GetInstance
or any configure/start command; refined diagnostics identify `file_usb`.
The existing guest guard independently reads these file identities:

| File | Packaged size | Captured size | Captured FNV-1a |
| --- | --- | --- | --- |
| libUsbControl.so | 94220 | 94176 | a2ae986b |
| libMWF.so | 238152 | 238108 | 2b7c22d1 |
| libc-2.11.2.so | 984276 | 984292 | 7f95ef74 |

The 44-byte reductions are not established by truncating the packaged files:
their prefix checksums do not match. Captured files were extracted offline
using debugfs 1.47.2, read-only (no `-w`), from the already saved ext2 images:

- nflasha15 SHA-256 `70d2613fcd24b13cfc4d75e98f435171fdfc50a6bc64d408e2df42ffb8197064`.
- nflasha7 SHA-256 `f65cb8467e55f057309defc25466185ab0d847e21f2217d27068ebd32e482bd5`.

New explicit `--captured-libs` profile pins all three SHA-256 values; the
packaged default is retained and still refuses this guest:

| File | Captured SHA-256 |
| --- | --- |
| libUsbControl.so | b5cea9d2c92a0b0810353900871fa2806ca3064aa5d8cf3ca7db5ddb6f3940e2 |
| libMWF.so | 0903f8f9257e71b1c6bfd76bc74863c686d5a6d13d2b982cccae63b4cd43a7b2 |
| libc-2.11.2.so | 2b294f4c256aebb2746cacddefc39c33c448a0d7f18c2813daa54109c445b910 |

Complete `.text` sections in libUsbControl and libMWF are identical between
profiles (SHA-256 `12df5493aab3971abef37dc0f9c9f0c453aea831655bbfce96472aeeb14b6bf4`
and `15ecbaae1bf7e2668782e48bb281a938dca45ec7fe60e350d5e849b337d93ad1`).
The relevant symbol offsets and original E0534 message implementation agree.
Libc `.text` differs: captured malloc/free are Thumb ELF VAs `0x5B491` /
`0x5B99D`, versus packaged `0x5B3C9` / `0x5B8D5`. The builder resolves the
captured definitions, never reuses the packaged allocator addresses.
Receipts: `artifacts/tests/qemu-usb-captured-libs-e0596/`.

## Runtime stimulus and guards

Source: `qemu-a6400/tools/usb_service_stimulus.c`. The mod has no hooks or
off handler and is not published as a usable streaming mod. It requires an
explicit `/tmp_bt/qemu-usb-service.allow` marker and the QEMU run's serial
console token. These are operator guards, not hardware detection. Never run
this diagnostic on the camera. Loader `Active` / `auto_on` only means its
void lifecycle entry ran; the separate diagnostic log determines progress.

Before the first original function call, it checks whole-file length/FNV
and the first 16 loaded bytes of every selected function. Input generation
requires pinned SHA-256. FNV is a runtime accidental-drift guard, not a
cryptographic equivalence proof. The existing loader relocates each literal
using actual process maps. Raw code is PIC with no unresolved imports.
Embedded expected instruction bytes are named data excluded from relocation;
the packer's first refusal exposed an instruction word resembling a pointer.
No packer restriction was weakened.

Original GetInstance supplies the live object. State must be 1. Original
malloc allocates 8-byte ObjMsg and 12-byte integer parameter; original
constructors build category `0x6103`, command `0x83003`, parameter `0x1005=4`.
Original SendEvt(control+0x28, message) is called; original destructors/free
release the caller's objects. A bounded 100 x 10 ms guest sleep waits for
state 5. The same path sends command `0x83001`; state/masks and service
registry fields are read only. No queue, state, gadget record or return is
injected. Existing firmware threads perform asynchronous service work.

This reuses identity-matched E0534 instruction evidence after consulting
RELATED_REPOSITORIES and the REA/dependency workflow, not an upstream claim.
The mod does not repair missing inputs or invent callback results.

## Reproduction and receipts

```powershell
.\venv\Scripts\python.exe -m tools.research.build_qemu_usb_stimulus --captured-libs artifacts/tests/qemu-usb-captured-libs-e0596 --output artifacts/tests/NEW_BUILD
```

```bash
bash qemu-a6400/tools/build_usb_service_stimulus.sh artifacts/tests/NEW_BUILD
python3 -m tools.research.qemu_usb_service_replay \
  --qemu-dir artifacts/qemu-a6400/e0595-isolated-build-1 \
  --inputs /home/administrator/a6400-inputs/inputs-moddev-1 \
  --loader artifacts/tests/mlv2-action-build-1 \
  --mod artifacts/tests/NEW_BUILD/UsbServiceProbe.mod \
  --report-dir artifacts/qemu-a6400/NEW_RUN
```

QEMU SHA-256 `2ed2d3d9e7325316ba4251e57f30e1579a891f7085a9322c0c8cfbe2bef5600e`;
input manifest `5aa4c878cd8214c5b503388f44d3d4f4512482d7ef37050beaadbe8b24906147`.
Captured DWC3 hardware-parameter profile is enabled. The existing E0592
guard and loader run inside disposable guest overlays; upstream QEMU
sources/build objects and all physical inputs are unchanged.

- `e0596-service-{1,2,3}`: preserved identity-refusal runs, including file
  diagnostics and maps, no commands sent.
- `e0596-service-captured-1`: original command acceptance above; immediate
  state-after-send is still 5 (asynchronous, not a failed start). QMP/owned
  exit 0, 62.8 seconds. Event ring and RUN stay zero, IRQ 229 stays zero,
  host receives no IN packet.
- `e0596-service-captured-2`: the bounded wait observes UsbControl FSM 7,
  configured/started masks 4/4, interface `0x0002F2D8`, CUsbSvc `0x0002F3A0`,
  service state 7 and registry `0x002F9940`, function ID 16. The sampled
  registry `created` byte remains 0. This is a non-atomic read immediately
  after the FSM wait; it is not a proved create failure or a successful
  activation. QMP/owned exit 0, 64.0 seconds; no ring/RUN/IN reply.
  Final mod SHA-256 `16c0e4eabd8b92754a614fab99b52a116109095ad9f0fdb35d54ad6731258255`.
- Builds `qemu-usb-stimulus-e0596-*` preserve generated symbol/identity
  reports, packer records and failed attempts. The first generation selected
  the wrong provider for SendEvt and stopped; its actual definition is in
  libUsbControl. Undefined symbol candidates are now rejected explicitly.

## Validation and next boundary

Four bounded ARM guard scenarios pass for the captured build: no allow
marker, mismatched file, mismatched loaded instruction, and matching input.
The matching scenario stops before executing original GetInstance. These
tests qualify rejection/entry guards, not USB startup; QEMU supplies the
separate original-process observations. The helper has no successful teardown
claim: each experiment terminates its owned QEMU process at the boundary.

All 43 focused status/host-probe tests pass. Full discovery passes 2113 tests,
29 skipped, in 60.579 seconds, with PhysicalVolumeSource explicitly denied
and per-case garbage collection. The exact runner and full-suite.txt receipt
are under `artifacts/tests/qemu-usb-stimulus-e0596-captured-2/`.
Source compilation, undefined-import/absolute-relocation checks, generated
status synchronization and scoped whitespace checks also pass. Status SVG
and PNG artifacts were refreshed without inflating hardware compatibility.

Next: distinguish accepted start command, pending/created gadget registration
and actual UDC activation; trace the original gadgetcore/class operations
before implementing further DWC3 responses. Bus-power creation alone would
not establish a descriptor-bearing data function.
