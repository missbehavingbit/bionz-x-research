# E0570 — ILCE-7RM4A (a7R IVA) gains a sourced reference profile

Date: 2026-11-17. The packaged-image measurements were read on 2026-11-14. Offline packaged firmware only: no camera, no USB, no updater executed.
This record registers the `ilce_7rm4a` target profile that `docs/ADDING_A_BIONZX_CAMERA.md`
describes, using only the a7R IVA's own packaged updater. It is contributed by an a7R IVA
owner who reports the body running the current release; no value here is taken from that
camera — every claim is read out of the packaged 1.13 image named below.

## 0. What changes, in one paragraph

`ilce_7rm4a` stops being only a row in the E0477 platform map and becomes a registered
`STATUS_REFERENCE` profile in `src/a6400/targets/`. It gains seven kernel symbol addresses
decoded from its own `vmlinux.bin`, a `CXD90045` identity from the package's own `config.yaml`,
its full 18-pair stock USB id set, and its 88-entry device-node table (which has **no**
`/dev/usb/video` or `/dev/usb/audio` row). Nothing is copied from the a6400 or any other body:
E0480 already showed this body's gadget-core symbols sit at their own addresses, a fixed
distance from their neighbours, and this record reproduces and extends that decode.

## 1. Inputs (each hash is the file that was read)

| input | SHA-256 |
| --- | --- |
| `Update_ILCE7RM4AV113.exe` (updater executable, read-only) | `545fdf6cf5009a4d077e8626313974c9ba5ee3741039c3d976c15d85b1246aa1` |
| `system/vmlinux.bin` (main kernel) | `8d02896458cd8927d3122d72548852d274c21962f5130faba71361ad10f44905` |
| `config.yaml` (fwtool unpack) | `b511141a6266ceb0ef477e24c8bc8abd1462fe956217dcd33adbb9a65052589f` |
| `rootfs/kmod/devno.ko` | `74096566ca4132ef3e947dc122427b6f3044ae5ab2d1657f5c6eb12945b1efa5` |
| `nflasha3/av-cam.bin` | `44e0c17bde9477fca8e2b3b6c2b0c93f309491a78134a4ec230dceeecb9c49fa` |

The updater `.exe` hash matches the a7R IVA row of E0477, and the `system/vmlinux.bin` hash
matches the `8d02896458cd8927…` value E0477 recorded for this body. The package was unpacked
with the project's documented procedure: `tools.research.sony_updater_payload` reports the
container shape, and the PackMan `FirmwareData_ILCE7RM4AV113.dat` member was unpacked with
`fwtool.py unpack` into a fresh output directory. Nothing was decrypted by hand, executed, or
written back.

Build identity, from the banner inside `system/vmlinux.bin`:

```
Linux version 3.0.27_nl-rt106+ (jp10239@dic-build-scm-u20-019)
(gcc version 4.5.1 (20220614 (Sony CE Linux 8.2.7.6)) ) #1 SMP PREEMPT RT Sat Jan 31 03:20:50 JST 2026
```

vermagic `3.0.27_nl-rt106+ SMP preempt mod_unload ARMv7`, the same string the a6400 and the
other BIONZ X bodies carry. The build machine `jp10239@dic-build-scm-u20-019` differs from the
`…u20-009` that stamps the a6100A and a7 III images of E0482, so this is a distinct build, not
a relabelled one.

## 2. Kernel symbols resolved offline

Decoded from this body's own `system/vmlinux.bin` with
`tools/research/decode_kernel_kallsyms.py` (24,514 symbols total):

| symbol | address | cross-check |
| --- | --- | --- |
| `usb_gadgetcore_ioctl` | `0x602BC860` | matches E0480 |
| `usb_gadgetcore_register_driver` | `0x602BC62C` | matches E0480 |
| `usb_gadgetcore_unregister_driver` | `0x602BC580` | not listed in E0480 (newly resolved) |
| `usb_gcw_start` | `0x602C1814` | matches E0480 |
| `usb_gcw_stop` | `0x602C1850` | matches E0480 |
| `setup_req` | `0x602BD4C0` | matches E0480 |
| `make_config_desc` | `0x602BFC70` | matches E0480 |

These are **not** the a6400's values, and not a neighbour's. E0480 measured that on all six of
its shared symbols this body sits exactly `0x4` from the a7 III and `0x1000` from the ILCE-9 —
a constant delta that is invisible without both images and is exactly why a copied address is
wrong by an amount no reader can catch. The seventh symbol here,
`usb_gadgetcore_unregister_driver` at `0x602BC580`, is newly resolved (E0480 listed only six);
it sits `0xAC` below this body's `register_driver`. Even that intra-build spacing is
body-specific — the a6400 places the same two symbols `0xB0` apart (`0x602B8610` − `0x602B8560`)
— which is one more reason an address cannot be transplanted between bodies.

## 3. The device-node table has no UVC row

`devno.ko` carries the `udif_devnodes` object that `src/a6400/execution/zve10_uvc_enablement.py`
parses:

| field | value |
| --- | --- |
| table entries | 88 |
| `/dev/usb/*` nodes | 17 |
| `usb/video` | absent |
| `usb/audio` | absent |

The 17 USB nodes are `adb`, `buspwr`, `buspwric`, `cur`, `kikilog`, `massstorage`, `portmon`,
`sen`, `sicd_core`, `sicd_data`, `sicd_event`, `std`, `swic`, `ter`, `usb_extcmd`, `usbg_bspwr`,
`vspe` — the same 17 E0482 lists for the a6100A/a6600/a7 III. As with those three, the
kernel-side node table does not publish a video or audio node, so a UVC function could not
appear at a `/dev/usb/video` path even if a module were loaded; and E0479 already recorded that
no UVC module package ships in this body's tree. Both halves of that answer say no.

This 88-entry `devno.ko` (`74096566ca4132ef…`) is byte-identical to the a6100A's `devno.ko`
recorded in E0482. That is a measured equality within the 8.2.7.6 build generation, read from
this body's own file; it is not a copied value, and the two bodies' kernels still differ
(E0477).

## 4. USB identity set, from the package's own config

`config.yaml` lists `dat.crypterName: CXD90045`, `fdat.model: 0x91030003`, `fdat.version:
'1.13'`, `fdat.region: 0`, and 18 `normalUsbDescriptors` pairs, all with vendor id `0x054C`:

```
0448 047D 0BEE 0BEF 0BFE 0BFF 0C13 0C14 0CA7 0CA8 0CC9 0CCA 0D0D 0D0E 0D11 0D12 0D9C 0D9D
```

This is the widest stock set in the project so far. It is a superset of the a6400's six
(`0448 047D 0BFE 0BFF 0CA7 0CA8`) and, unlike the a6100A chain, also carries the a7 III's
`0BEE/0BEF` pair plus six pairs of its own (`0C13 0C14 0CC9 0CCA 0D9C 0D9D`). `0x0CA8`, the
mass-storage identity the a6400 records as stock, is present here, but — per E0482's warning —
that is a per-body fact, not a family constant. The updater-mode pair `0x054C:0x03E2` is the
same one every package presents and is not a profile claim.

## 5. av-cam command table

`av-cam.bin` carries **333** command records at offset `0x1096180` in this image, matching the
a7R IVA figure E0477 reports. This is recorded as a cross-check, not a profile field.

## 6. Confidence and limitations

- **Measured:** every hash, address, table entry, pid pair and the command count above, from the
  files named in section 1, with the documented offline tools.
- **Not shown:** no camera was accessed; no USB transaction, updater execution, boot, live
  `/proc`, `/setting` read or physical dump is claimed. A resolved symbol is presence evidence,
  not loadability or callability. The `uipc_queues` are left empty because the a6400's values
  come from a live, per-boot `/proc/osal/uipc` read that has no offline equivalent here.
- **Status:** `ilce_7rm4a` is registered `STATUS_REFERENCE`, not a verified execution or
  installation target. Its kernel is distinct from the a6400's (E0477); do not reuse the a6400
  installer, hooks or checkpoints for it.

## 7. Next experiment

A read-only USB identity capture from the owner's body (host-observed VID/PID per mode, kernel
banner, `/sys/class/udc` state) would move individual fields from offline-sourced to
physically observed, following the a6400A path in E0506–E0509. That is a separately authorized
physical workstream, not part of this offline onboarding.
