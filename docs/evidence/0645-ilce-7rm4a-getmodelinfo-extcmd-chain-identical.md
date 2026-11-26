# E0645 — ILCE-7RM4A GetModelInfo extcmd chain is byte-identical to the a6400A

Date: 2026-11-19. Offline packaged firmware only: no camera, no USB, no updater executed.
This record answers one question raised while planning a read-only identity capture for the
a7R IVA ([E0570](0570-ilce-7rm4a-offline-sourced-profile.md)): is the Sony `GetModelInfo`
vendor command the a6400A probe uses (SCSI opcode `0x7A`, subcommand 1) the right command for
the a7R IVA too? It is decidable from the firmware alone, and the answer is yes.

## 0. What changes, in one paragraph

Three of the four binaries in the a6400A's `GetModelInfo` external-command chain are
**byte-identical** on the a7R IVA, including the ones that define the command itself. Because
[E0509](0509-a6400a-msc-reply-lifetime-and-verified-native-identity.md) already proved, on the
a6400A, that `UsbCmd_ExtCmdSvcWrap` registers opcode `0x7A` and that `usb_extcmd.ko` holds the
inquiry-state/BUSY decision, that layer transfers to the a7R IVA: the wire command `7a 01 …` and
its reply format are the correct `GetModelInfo` request for this body. The fourth binary, the
mass-storage gadget `usbg_storage.ko`, differs, so whether a host CDB reaches that handler over
Mass Storage is a camera question (section 4), not something this record claims. The read-only
probe `tools/research/a6400a_native_readonly.py` is generalized so the a7R IVA (and any future
body) can be admitted without copying the a6400A's identity, while the a6400A path is unchanged.

## 1. Inputs

The a6400A chain and its four component SHA-256 are already recorded in
`tools/research/msc_extcmd_trace.py` (`DRIVER_SHA`, `STORAGE_SHA`, `USBCMD_SHA`, `DEVINFO_SHA`)
and E0509. The a7R IVA files come from its own packaged 1.13 updater
(`545fdf6c…`, E0570), unpacked with `fwtool.py`, under the normal runtime rootfs
(`…/parts/usr/`):

| chain component | role | a7R IVA SHA-256 | vs a6400A |
| --- | --- | --- | --- |
| `usb_extcmd.ko` | kernel module that dispatches the SCSI external command | `d47d05ec891d18c8582baf19e413967c5d3fd5c36438d76f6d2e8a7f112428e3` | **identical** |
| `libusbcmd.so` | sets opcode `0x7A` (`UsbCmd_ExtCmdSvcWrap::transInit`, `usbcmd_t::PackParamDriver`) and packs the request/reply | `23b94deb094d31f160cfe1d46330f2425e94d4801dfccb66720c4cf4b81cfb7e` | **identical** |
| `libInfraDevInfoSender.so` | sends the model-info response (`13ReqTranserCmp`) | `db4a0c97ccbd5fad6acec2354dbfeea12c50b2e93e7d346d02e280d7f70d896e` | **identical** |
| `usbg_storage.ko` | mass-storage gadget: SCSI CDB classification and external-command routing (`storage_cmd_cmdPacket`, `extCmd_exec`/`extCmd_std`, `unknownCmd_std`) | `b541a2ddc57da209c1b286f784c4218a43f02b1710f54709f484efde0a31669c` | **differs** |

The three identical SHAs equal the a6400A constants in `msc_extcmd_trace.py` exactly. The one
that differs, `usbg_storage.ko`, is not a neutral byte pipe: its exported symbols show it
classifies the incoming SCSI CDB and routes external commands. That is why the match below is
stated as command-definition evidence, not end-to-end acceptance (section 4).

## 2. What is confirmed, and what is not

The parts that **define** `GetModelInfo` are byte-identical on both bodies:

- **Opcode registration and request/reply packing** — `libusbcmd.so`, which still carries the
  exact symbols E0509 named:
  `_ZN20UsbCmd_ExtCmdSvcWrap9transInitEP24UsbCmd_ExtCmdSvcWrapUnitP16__extcmd_svc_msg`,
  `_ZN8usbcmd_t15PackParamDriverEPvP19usbcmd_data_param_t`, and the `UsbCmd_ExtCmdSvcWrap`
  constructors/destructor.
- **Opcode/feature match and the BUSY/ready decision** — `usb_extcmd.ko`, whose
  `get_inquiry_state` and status-ioctl logic E0509 decoded.
- **The reply sender** — `libInfraDevInfoSender.so` (`DevInfoSender`).

So the request descriptor the probe sends (`MODEL_CDB = 7a 01` + an 8192-byte `MODEL_REQUEST`
whose header is `size=0, command=1, direction=0`), the 8-bit opcode, the 16-bit feature, the
10-second reply lifetime and the BUSY/ready semantics are all the same code on the a7R IVA.
**The vendor command and its format are therefore confirmed correct for this body**, and E0509's
static proof of that layer transfers without re-reverse-engineering.

What is **not** proven offline is that a host-issued CDB `0x7A` actually reaches that identical
handler over Mass Storage, because the one binary that differs — `usbg_storage.ko` — is exactly
the mass-storage gadget that classifies the incoming SCSI CDB and decides whether it is an
external command (`storage_cmd_cmdPacket`, `extCmd_exec`/`extCmd_std`, `unknownCmd_std`). Its
difference from the a6400A's copy cannot be diffed here (the a6400A package is not in-repo), so
whether it routes `0x7A` to the extcmd path identically is a camera question, grouped with the
live-reply gap in section 4.

This is a measured equality on this body's own files, not a copied value: the a7R IVA kernel and
command table still differ from the a6400A's (E0477), and only these specific extcmd binaries
happen to be shared across the build generation.

## 3. Probe generalized (no invented identity)

`tools/research/a6400a_native_readonly.py` now carries a `CameraIdentity` record and an
`IDENTITIES` registry, selected with `--target` (default `ilce_6400a`, unchanged):

- `ilce_6400a` — `usb_pid=0x0F0F`, `expected_model="ILCE-6400A"` (the fully observed body, E0509).
- `ilce_7rm4a` — `usb_pid=None`, `expected_model="ILCE-7RM4A"`.

The a7R IVA's Mass Storage PID is a host-observed runtime fact that has **not** been captured
yet, so it is left `None`; the probe refuses (`--pid` required) rather than guessing one from
the 18 packaged descriptor pairs (E0570). The Windows discovery filter, the LUN/driver
admission and the model-info model string are all taken from the selected identity. The SCSI
allowlist (`INQUIRY`, the `GetModelInfo` request, the `GetModelInfo` response) and every bound —
8-second budget, 40-attempt cap, no resubmit, no sector I/O, no mode switch — are unchanged.

## 4. Confidence and limitations

- **Measured:** the four SHA-256 above and the `libusbcmd.so` symbol set, from the files named
  in section 1 with standard hashing and `pyelftools`.
- **Not shown (needs a camera):** (a) that the differing `usbg_storage.ko` classifies and routes
  a host CDB `0x7A` to the identical extcmd handler the same way the a6400A does — the command is
  defined identically, but its SCSI-side delivery is in the one binary that changed; (b) the live
  response content — the a7R IVA's own model string and its opaque five-byte product code; and
  (c) whether the body answers `GetModelInfo` in Mass Storage promptly or returns BUSY first, as
  the a6400A did (E0508 BUSY, then E0509 one clean transaction). A byte-identical command
  definition does not by itself prove runtime reachability.
- **Not yet observed:** the a7R IVA Mass Storage USB PID, hence `usb_pid=None`.
- **Build-local:** the *initrd/updater* ramdisk carries a different `libusbcmd.so`
  (`b3de7fa5…`); that is the updater environment, not the operating Mass Storage mode, which uses
  the identical rootfs library above.

## 5. Next experiment

A read-only host identity capture from a real a7R IVA (host-observed VID/PID per USB mode, then
`a6400a_native_readonly.py --target ilce_7rm4a --pid 0x<observed> --inquiry [--model-info]`),
recorded as its own evidence record, would supply the Mass Storage PID and confirm the live
`GetModelInfo` reply. That is a separately authorized physical workstream; this record is
offline only.
