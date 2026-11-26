# E0646 — ILCE-7RM4A physical USB identity and native GetModelInfo on firmware 1.13

Date: 2026-11-19. Physical, read-only: the owner's own a7R IVA on a Windows host over USB.
This is the camera half that [E0570](0570-ilce-7rm4a-offline-sourced-profile.md) section 7 and
[E0645](0645-ilce-7rm4a-getmodelinfo-extcmd-chain-identical.md) section 5 left open. No flash write,
no mode switch issued by the host, no driver change, no sector I/O, no SD write, no `/dev/mem` or
`/dev/stream` access, and no SCSI command outside the probe's three-entry allowlist.

## 0. What changes, in one paragraph

The a7R IVA's host-visible USB identity is now observed rather than packaged: **Mass Storage is
`054C:0D9D` (`REV_0200`, USBSTOR)** and **PC Remote is `054C:0D9F` (host driver libusbK)**. With the
body on firmware 1.13 — the image E0570/E0645 analysed — the unchanged read-only probe sent one SCSI
INQUIRY (`Sony` / `DSC` / `1.00`) and then the Sony `GetModelInfo` request `7a 01`. The first response
read returned Sony BUSY (sense `9/0x81/0x81`), the second returned a clean 8192-byte reply carrying the
model string **`ILCE-7RM4A`** and the opaque five-byte product code **`0032853030`**, 0.205 s after the
request. That closes all three camera questions E0645 section 4 listed: the Mass Storage gadget running on
1.13 does route a host CDB `0x7A` to the extcmd handler (that it is the packaged `usbg_storage.ko`
`b541a2dd…` E0645 hashed is inferred from the version, not read live); the live identity is the expected
one; and the body answers after one BUSY, like the a6400A's first-transaction behaviour class (E0508,
E0509). `ilce_7rm4a`'s `usb_pid` in the probe moves from `None` to the observed `0x0D9D`.

## 1. Firmware version, and a correction to E0570

At the start of the session the owner read **1.12** from the camera menu, not 1.13. E0570 states that
the owner's body was "reported running the current release"; that statement was wrong for this body
at the time and is superseded here. E0570's measurements are unaffected: they are read from the
packaged 1.13 image and never claimed to come from the camera.

Because E0645's GetModelInfo argument covers only 1.13 binaries, no Sony vendor command was sent on
1.12. The owner chose to update the body to 1.13 with Sony's official updater, which the owner ran
themselves (no agent involvement in the flash). Before running it, the owner computed the updater's
SHA-256 as `545fdf6cf5009a4d077e8626313974c9ba5ee3741039c3d976c15d85b1246aa1` (369,269,800 bytes),
which equals the E0570 input hash, so the flashed image is the one E0570 and E0645 analysed. After
the update the owner read **1.13** from the camera menu. Every physical result below except the 1.12
PC Remote row is on 1.13.

The displayed version is owner-reported. The SCSI INQUIRY revision `1.00` is not a firmware version
(E0509 records the same value for the a6400A), so nothing on the wire independently confirms 1.13.

## 2. Host and tool identities

| item | value |
| --- | --- |
| host | Windows 11 Pro 10.0.26300, Python 3.14.10 |
| repository base | `upstream/main` `d7e6ea9` (the merge of E0645) |
| probe | `tools/research/a6400a_native_readonly.py` at `d7e6ea9`, SHA-256 `cdf82a7c48a88d9bb9700c9005ec9ae2d3b63e013417d5de61574b07e38c6cd4`, unmodified; `ilce_7rm4a` admitted with `--pid 0x0D9D` |
| host lock | `tools/camera_debugger.py` at `d7e6ea9`, SHA-256 `c033292e04e1dfe97e57bf9ec664a3bb083fc5b1d196cd96acb11d7cb0c0c951` |
| USB-mode selection | set by the owner in the camera menu between connections; the host issued no mode switch |

## 3. Host-observed USB identity per mode

Read from the Windows PnP tree only (`Get-PnpDevice -PresentOnly` filtered on `VID_054C`, plus
`DEVPKEY_Device_HardwareIds`/`CompatibleIds` and `Win32_DiskDrive`). These are reads of host state;
no handle to the device is opened and no USB transfer is initiated by them. Instance serials were
seen by the host and are deliberately not recorded.

| mode | firmware | VID:PID | hardware id / class | host service | in packaged 18 pairs (E0570)? |
| --- | --- | --- | --- | --- | --- |
| PC Remote | 1.12 | `054C:0D9F` | "Sony Remote Control Camera" | libusbK | **no** |
| PC Remote | 1.13 | `054C:0D9F` | "Sony Remote Control Camera" | libusbK | **no** |
| Mass Storage | 1.13 | `054C:0D9D` | `USB\VID_054C&PID_0D9D&REV_0200`; class `08/06/50` (MSC, SCSI transparent, bulk-only) | USBSTOR | yes (`0D9C/0D9D`) |

In Mass Storage the device exposes **three LUNs** (0, 1, 2), each a `USBSTOR\DISK&VEN_SONY&PROD_DSC&REV_1.00`
disk with model `Sony DSC USB Device`, all parent-verified to the `054C:0D9D` device by the probe's
discovery. What each LUN backs is not determined here.

Observations and boundaries on this table:

- `0x0D9F` is host-observed but absent from the packaged `normalUsbDescriptors` set; where that
  descriptor comes from is not identified. It did not change between 1.12 and 1.13.
- The libusbK binding for PC Remote is a pre-existing host-side driver assignment on this PC, not
  part of this session and not a property of the camera.
- MTP and UVC were not observed. Stock descriptor membership does not identify a mode.

## 4. Probe runs (exact commands and receipts)

All runs were launched from the repository root. The receipts live under the git-ignored `artifacts/`
tree and are cited by hash; none is committed.

| # | command (`python -m tools.research.a6400a_native_readonly --target ilce_7rm4a --pid 0x0D9D …`) | launched by | result | receipt SHA-256 |
| --- | --- | --- | --- | --- |
| 1 | `--inquiry --output-dir artifacts/e0646-ilce7rm4a-inquiry` | agent, non-elevated shell | `blocked`: discovery and LUN 0 admission passed (disk index 2), then `CreateFileW` on `\\.\PhysicalDrive2` failed with WinError 5; `operations: []`, no CDB sent | `7d5690b4886616614e79ab70aa86ab76bf5a150fe96ff4bb0ef1ca0a16c46d23` |
| 2 | same as 1 (output directory reused) | owner, PowerShell opened as administrator | refused by the tool's non-overwrite rule at `mkdir`, before the host lock, discovery or any handle (so elevation was not exercised); no receipt written, run 1's receipt verified intact | — |
| 3 | `--inquiry --output-dir artifacts/e0646-ilce7rm4a-inquiry-admin` | owner, same shell | `passed` | `4b9d4fc5eb3d120bb9da267996c0342b95600b042e47b6bc32a87437517542a7` |
| 4 | `--inquiry --model-info --output-dir artifacts/e0646-ilce7rm4a-model-info` | owner, same shell | `passed` | `337d1c066b7f7fecc805e0d299a23e38ad73c2ee99c83fc0a2e2ca93e571e128` |

That runs 3 and 4 had administrator rights is inferred from the `CreateFileW` open succeeding where
run 1's failed with WinError 5; the token itself was not inspected. The receipts are kept in the
owner's working copy under `artifacts/` (git-ignored by repository policy) and are reproduced in the
tables below; their hashes pin them.

Every receipt records `mode_switches`, `updater_initialization`, `driver_changes`, `sector_io`,
`camera_file_writes` and `serials_retained` as `false`.

### 4.1 INQUIRY (runs 3 and 4)

| CDB | direction | bytes | SCSI status | sense | result |
| --- | --- | --- | --- | --- | --- |
| `12 00 00 00 24 00` | in | 36/36 | 0 | `0/0/0` | vendor `Sony`, product `DSC`, revision `1.00` |

The admitted disk model `Sony DSC USB Device` matches `CameraIdentity.disk_model`.

### 4.2 GetModelInfo transaction (run 4)

| step | CDB | direction | bytes | SCSI status | sense | meaning |
| --- | --- | --- | --- | --- | --- | --- |
| request | `7a01` + 10 zero bytes | out | 8192/8192 | 0 | `0/0/0` | request descriptor `size=0, command=1, direction=0` accepted; ack 0.001191 s |
| response read 1 | same | in | 0/8192 | 2 | `9/0x81/0x81` | Sony BUSY, no partial data; request **not** resubmitted |
| response read 2 | same | in | 8192/8192 | 0 | `0/0/0` | reply header `command=1, direction=1`, payload 8176 bytes |

Transaction start `2026-11-19T19:30:59.011447Z`, end `19:30:59.216900Z`, elapsed **0.205468 s** of the
8 s budget, 2 of the 40 permitted response reads. The 0.2 s inter-read delay is the probe's own BUSY
back-off, so the elapsed time is dominated by the probe, not measured camera latency.

Parsed reply:

| field | value |
| --- | --- |
| model string | `ILCE-7RM4A` (matches `expected_model`) |
| product code (wire, opaque) | `0032853030` |
| serial / property list | parsed past, not retained |

For comparison only, the a6400A's code is `0032858730` (E0509). The two differ in their fourth byte;
the format of this five-byte field is not decoded and no meaning is attached to the difference. It is
a different namespace from the updater's `fdat.model` (`0x91030003` for this body, E0570).

## 5. What this proves for E0645's open questions

| E0645 §4 question | answer | basis |
| --- | --- | --- |
| (a) Does the differing `usbg_storage.ko` route host CDB `0x7A` to the extcmd handler over Mass Storage? | **Yes** for the gadget running on 1.13 | the request was accepted and a well-formed `command=1, direction=1` reply came back on the same CDB; identity of the running module with the packaged `b541a2dd…` file is inferred, not measured |
| (b) Live model string and product code | `ILCE-7RM4A`, `0032853030` | run 4 |
| (c) Prompt answer or BUSY first? | **one BUSY, then the reply** | run 4, response read 1 |

This is one transaction on one body on one boot. Repeatability across power cycles, and whether the
first BUSY is systematic, are not established; the a6400A's own first-BUSY cause also remains unproven
(E0509).

## 6. Superseded

- E0570 §0 and its profile boundary, "the owner's body … reported running the current release": wrong
  for this body when this session began (it ran 1.12). E0570's offline measurements stand.
- E0645 §3 and §4 "Not yet observed: the a7R IVA Mass Storage USB PID" and the `usb_pid=None` entry:
  superseded by the observed `0x0D9D`.
- E0645 §4 "Not shown (needs a camera)" items (a)–(c): answered by section 5 above, for firmware 1.13
  and one transaction. E0645's offline hash comparison stands unchanged.

## 7. Camera state

No anomaly was observed during or after the runs: the device was still present as `054C:0D9D`
(`REV_0200`) in a passive PnP hardware-id re-read after run 4. A later passive read at `19:37:15Z` showed no Sony
device because the owner had unplugged the cable, as the owner confirmed. No camera-side responsiveness
check (`/proc/uptime`, debugger) exists on this body, so "no anomaly" is the owner's and the host's
observation, not a camera-internal measurement.

## 8. Repository changes

- `tools/research/a6400a_native_readonly.py`: `IDENTITIES["ilce_7rm4a"].usb_pid` `None` → `0x0D9D`.
  The SCSI allowlist and every bound (8 s budget, 40 reads, no resubmit, no sector I/O, no mode switch)
  are unchanged; the unobserved-PID refusal path stays tested on a fixture identity.
- `src/a6400/targets/bodies/ilce_7rm4a.py`: `product_code="0032853030"`; observed-tier PIDs
  `mass_storage` `0x0D9D` and `pc_remote` `0x0D9F`; `bcd_device=0x0200`; boundaries corrected for the
  1.12/1.13 history. The profile stays `STATUS_REFERENCE`.
- Tests: `tests/test_a6400a_native_readonly.py` (observed PID, PC Remote PID never admitted, one BUSY
  then reply on constructed fixtures with no live bytes) and `tests/test_target_profiles.py`
  (observed tiers, `0x0D9F` not claimed as stock, sources cite this record).
- Status map: `docs/CURRENT_STATE.md`, `README.md`, the evidence index and the rendered status SVG.
- Validation: full offline suite in a fresh `venv` with the pinned `requirements.txt` on Windows,
  Python 3.14.10: **2046 run, OK, 209 skipped** (firmware/hardware-gated); no camera access by tests.

## 9. Confidence and limitations

- **Measured (host/wire):** the VID/PID/REV/class/service rows, the LUN count, every CDB, status,
  sense, byte count and timing in section 4, the parsed model string and product code, and the
  receipt hashes.
- **Owner-reported:** the displayed firmware versions 1.12 and 1.13, the updater hash and size, the
  USB mode selected in the menu for each connection, and that the cable was unplugged by hand.
- **Not shown:** live kernel, module or rootfs bytes (no `/proc`, `/sys`, memory or partition read);
  that the running `usbg_storage.ko` is the packaged `b541a2dd…` file; the meaning of the product code;
  what LUNs 1 and 2 back; the MTP and UVC PIDs; repeatability of the BUSY-then-reply pattern.
- **Status:** `ilce_7rm4a` remains a reference profile, not an execution or installation target. No
  root, debugger or dump access is claimed.

## 10. Next experiment

A repeat of run 4 after a cold power cycle would show whether the first BUSY is systematic. An MTP
observation would complete the per-mode PID table. Both are read-only and fit the same tool and
bounds; neither is part of this record.
