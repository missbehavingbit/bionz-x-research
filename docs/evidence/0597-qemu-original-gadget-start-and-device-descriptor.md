# E0597 — Original gadget start and first DEVICE descriptor in QEMU

Date: 2026-11-18. Two bounded physical read-only observations in normal MSC;
all configure/start and host-transfer experiments are offline QEMU only.

## Result

The E0596 startup failure is now identified and a separate, explicit
physical-snapshot policy passes that boundary:

- Original `SET_MODE(2)` returns 0, but `0xE305` returns kernel `-EBUSY`.
  `CUsbSvc::createGadget` then returns 14 and sets service state **7**, an
  error state on this path. UsbControl's separate FSM state 7 does not
  establish gadget success. This qualifies E0596's unresolved created=0.
- `fusb_start_gadget` passes mode=2, CID=1 and speed=1, then reads bit 0 of
  **physical `0xF0200024`**. The unimplemented QEMU word returns 0; original
  instructions branch to `-EBUSY`. Moving virtual VBUS before start does
  not fix it: get-mode already returns 2 and the same word still blocks it.
- Two fixed-word physical reads return **`0x00000001`**. Opt-in
  `-global cxd90045-dwc3.captured-start-gate=on` supplies only this read-only
  connected-MSC snapshot. The historical default stays unimplemented/zero.
- With that policy, original create returns 0, CUsbSvc reaches **state 3**,
  the original bus-power registry has **created=1**, and original gadgetcore
  callback/descriptor ioctls succeed. The driver arms a 4096-byte event ring,
  sets DCTL.RUN, and services IRQ 229. Virtual GET_DESCRIPTOR receives:
  `IN 1 12010002000000404c056f09000101020301` — the firmware's original
  18-byte bus-power DEVICE descriptor, VID/PID **054C:096F**.

This is a bounded EP0 descriptor observation, **not complete USB enumeration
or UVC streaming**. The host has not proved Status OUT, SET_ADDRESS,
SET_CONFIGURATION, UVC control/alternate-setting exchanges or data streaming.
The model's remaining immediate-IN/truncation behaviors remain unqualified.
No physical mode change, gadget-start command, module load or hook was run.

## Original-code analysis and identity qualification

Consulted RELATED_REPOSITORIES, REA_WORKFLOW, DEPENDENCY_WORKFLOW and retained
E0534/E0594–E0596 before deriving the next contract. No upstream contract
was copied. The current REA profile could not start because its declared
Node executable is absent in this environment; failed receipt:
`artifacts/tools/rea/e0597-gadget-create-1/`. The installed legacy profile was
then explicitly selected, with REA 6.7.0 / Ghidra 12.1.4. Three queries
completed in `e0597-gadget-create-legacy-1/`, source unchanged:

- `CUsbSvc::createGadget(CUSB_MES*)`, Thumb ELF VA `0xBBC5`.
- `CUsbGadget::createGadget(...)`, Thumb ELF VA `0x88B9`.
- `CUsbGadgetBusPower::on_create(...)`, Thumb ELF VA `0xF9E9`.

Packaged InfraUsbCmn SHA-256 is
`5338bafbb545ebc223fac15c9253a41ac9fe49c01fe32c73af40c3fb50eec0a6`.
The captured copy, extracted read-only with debugfs from the already saved
nflasha15, is `733acef066e3e3691f7258e7416b638a7960d99b70f27e85ddc4daad2b5ecef2`.
Complete `.text`, `.data` and `.rodata` bytes agree between these inputs;
comparison and extraction receipts are under
`artifacts/tests/qemu-usb-infra-e0597/`. The `.text` SHA-256 is
`87dfc4a5d022c1aa4f257d216f1791c78468d957caee5734d11eb0604c6b0bc5`.
REA pseudocode was checked against original instructions and native traces.

The actual captured QEMU kernel SHA-256 is
`d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618`,
as recorded by its unchanged boot manifest. It is **not** the packaged
`12eeec82...` kernel used in earlier static leads; identical file lengths
and several symbol addresses do not make them identity-equivalent. The
first trace generator rejected that mismatch. The final generator pins the
captured digest and decodes its own 24,600 kallsyms. The older static kernel
identity is not silently reused or promoted into captured-kernel proof.

## Supported original instructions and runtime trace

`build_usb_startup_trace` emits exact 16-byte fingerprints and original
instruction receipts from the captured InfraUsbCmn and kernel. The API-7
passive plugin checks candidate PCs plus all 16 loaded bytes, reads ARM
registers, and logs at most 32 matching samples/site/CPU. It writes no guest
memory, registers, queues or events. Runtime matching precedes the sample
budget, so unrelated candidate prologues cannot exhaust a real site's quota.
The final 94 sites include both ioctl epilogues and the short FUSB start body.
Inline jump-table words no longer stop scanning before the true ARM epilogue;
only an executed PC with matching loaded bytes qualifies a runtime site.

Key original boundaries:

| Site | Original operation / observation |
| --- | --- |
| Infra +0xBBFA | first SET_MODE returned 0 |
| Infra +0xBC0C | E305 libc return -1 in controls, 0 with snapshot |
| Infra +0xBF3C / +0xBF2C | error 14 path / actual create return |
| Kernel 0x602B817C | ioctl epilogue: -16 on blocked E305 |
| Kernel 0x602ACAAC–0x602ACAD0 | mode=2, CID=1, speed=1 pass |
| Kernel 0x602ACAD8 | pointer read from context is 0xF0200000 |
| Kernel 0x602ACADC–0x602ACAE4 | load +0x24, test bit 0, branch to -EBUSY when clear |
| Kernel 0x602B8844 | original gadgetcore registration 0x401CE201 and descriptor commit 0x400CE203 |

The startup plugin compilation initially exposed API-7 differences: init
callbacks take user data and read_register returns bool plus byte-array
length. Those are corrected explicitly; full register observations are
required by the receipt auditor. Initial failed builds/runs are preserved.
Preparation failures without native output files are explicitly transcribed
as such in `qemu-usb-startup-trace-e0597-1/attempt-notes.md`; they are not
represented as raw receipts or successful observations.

## Physical observation and safety receipts

The 656-byte `usb_gadget_gate_readonly.S` helper opens `/dev/mem` O_RDONLY,
maps exactly one 4096-byte page at `0xF0200000` PROT_READ/MAP_SHARED and
executes exactly one MMIO load at +0x24. Its only other stores are private
BSS/stdout, not MMIO. ARM instructions were inspected before upload.
Helper SHA-256:
`54caeda7fdee7ada2370919bb09232cd503a6bc640da3be97da2706b140f7618`.

`capture_dwc3_identity --gadget-gate --helper ...` uses an absent, hash-named
`/tmp_bt` file, verifies upload readback, executes the bounded helper, removes
only its owned file and verifies uptime/debugger/temporary-directory state.
It does not write a hardware register, SD, flash or persistent camera file.
Receipts: `artifacts/hardware/e0597-gadget-gate-{1,2}/`. Both four-byte results
are `01000000`, SHA-256
`67abdd721024f0ff4e0b3f4c2fc13bc5bad42d0b7851d456d88d203d15aaa450`.
Debugger PID 700 and info remain unchanged. Run 2 uptime advances
4949.67 -> 4950.03 seconds. Both runs clean up their helper and preserve
unrelated `/tmp_bt` contents; normal MSC remains configured.

Only a connected-MSC value is established. Register meaning beyond the
original bit-0 guard, reset value, disconnected state, timing, writes and
transitions are unknown. The opt-in model deliberately exposes a **snapshot**,
not a complete PHY/wrapper implementation. Unsupported writes are rejected;
no adjacent register contents or generic success response is added.

## QEMU implementation, commands and observations

The DWC3 model adds a separately mapped read-only four-byte snapshot window
only when `captured-start-gate` is enabled. It is independent of E0595's
captured-hwparams option; both defaults preserve earlier baselines.
The isolated relink writes only new artifact objects/binaries, leaving
upstream sources and objects unchanged.

```bash
python3 -m tools.research.qemu_dwc3_profile_check \
  --qemu-dir /home/administrator/sony-a6400-qemu-11.1.3 \
  --report-dir artifacts/qemu-a6400/NEW_BUILD
```

Final build receipt: `artifacts/qemu-a6400/e0597-isolated-build-2/`.
Overlay SHA-256 `580e713b4bd56484a50eef144f94992e481438cd1c1620a80090e6bf046d5550`;
QEMU SHA-256 `5c0f0fe831cb0e39d970d62981bf6a187c0c5b8051d86bed21c81b3c0c14ba2a`.
Native qtest passes 44 checks across all four combinations of the independent
identity and start-gate policies, including unchanged default gate=0.

The E0596 mod is unchanged, SHA-256
`16c0e4eabd8b92754a614fab99b52a116109095ad9f0fdb35d54ad6731258255`.
It sends explicit bus-power commands through original constructors, allocator
and threads. Input manifest stays
`5aa4c878cd8214c5b503388f44d3d4f4512482d7ef37050beaadbe8b24906147`.

```bash
python3 -m tools.research.qemu_usb_service_replay \
  --qemu-dir artifacts/qemu-a6400/e0597-isolated-build-2 \
  --inputs /home/administrator/a6400-inputs/inputs-moddev-1 \
  --loader artifacts/tests/mlv2-action-build-1 \
  --mod artifacts/tests/qemu-usb-stimulus-e0596-captured-2/UsbServiceProbe.mod \
  --plugin artifacts/tests/qemu-usb-startup-trace-e0597-5/trace.so \
  --vbus-before-start --captured-start-gate \
  --report-dir artifacts/qemu-a6400/NEW_RUN
```

Baseline controls: `e0597-traced-service-{2,3}`, `e0597-vbus-before-service-1`
and `e0597-gadget-guard-1`. All retain native E305 failure and no IN packet.
With the policy: `e0597-captured-gate-service-1` observes service=3, created=1,
GCTL=0x2000, guest-allocated ring=0x08A32000, ring size=0x1000,
DCTL=0x80000000 before connect, DEVTEN=0x1E1F and DALEPENA=3.
After host reset/GET_DESCRIPTOR: DCFG=0x00400001, DCTL=0x8CF00000,
IRQ 229 count=4, UDC state=`default`, and the original descriptor above.
QMP quit and owned exit are 0; elapsed time 69.9 seconds. Device state
`default` is not `configured`; DISC transmission is not a successful STOP
or complete guest teardown proof.

A second fresh run, `e0597-captured-gate-service-2`, independently returns
the exact same DEVICE bytes, with service=3, created=1, ring/RUN active and
QMP/owned exit 0 in 66.0 seconds. Both successful receipts pass all ten
auditor checks. The VBUS-before-start control without the snapshot also
passes its eight expected-blocked checks, preserving EBUSY rather than
manufacturing a successful mode transition.

## Validation and next boundary

Eight bounded original-ARM guard cases pass: wrong mode, CID, speed, clear
bit 0 / bit 1 only, bit 0 set / all bits set, and an explicit unsupported
callback stop. No-callback cases use declared fixtures and only qualify the
original guards; no callback success is fabricated. Real callback/driver
execution is separately observed in QEMU.

`audit_qemu_usb_startup` passes eight blocked-control receipt checks and
ten successful-start/descriptor checks. It requires original call/return
observations, service state, created byte, guest-armed ring/RUN and exact
DEVICE packet; it explicitly records enumeration/UVC as unverified.

The 43 focused status/host-probe checks pass. Full offline discovery passes
2113 tests, 29 skipped, in 57.978 seconds with PhysicalVolumeSource explicitly
denied and per-case garbage collection. Receipt:
`artifacts/tests/usb-gadget-gate-e0597/full-suite.txt`; runner is the retained
E0596 `run_full_suite.py`. Source compilation, scoped whitespace checks and
generated status synchronization pass. Status SVG/PNG outputs were refreshed.

Next: qualify host control-transfer status, SET_ADDRESS and configuration
before treating the USB host bridge as enumerated. Then exercise an actual
UVC function; the bus-power descriptor is not the USB Streaming Test mod.
