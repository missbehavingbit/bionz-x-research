# E0667 - a6400A debugger ABI and live channel boundary

Date: 2026-11-14. **Offline original-instruction controls plus one explicitly
approved, elevated, read-only normal Mass Storage observation.** No service or
updater mode, debugger request payload, helper upload/launch, driver replacement,
camera filesystem access, SD operation or flash write.

## 0. Verdict

**The packaged debugger ABI passes the bounded offline gates; the physical
ILCE-6400A is not yet qualified for debugger installation or partition dumps.**

- Original ARM dispatch selects the daemon's Create, Destroy, receive and send
  ioctl branches. Each control stops before the user-access check.
- Original Create field initialization accepts the declared 12-byte
  `{opcode=0x7a, feature=0x23, receive=4096, send=4096}` fixture. Execution stops
  before mutex initialization; allocation and registration are not supplied.
- Twelve original readiness cases exercise feature `0x23`, missing/present
  nodes, both directions and wrong keys. No readiness writes occur.
- Original `list_delete_node` unlinks a constructed debugger node while
  preserving another node for feature 1. Context and mutex calls are explicit
  fixtures, not real kernel locking or a complete Destroy/STOP proof.
- The approved live probe returns ILCE-6400A / opaque product code
  `0032858730`, then exactly one feature-`0x23` IN returns **05/20/00 and zero
  bytes**. No usable debugger channel is observed.
- The installation registry still excludes `ilce_6400a`. The original target's
  strict driver/library guard still rejects these different packaged inputs.

No `A6400_USB_DEBUG_V1` handshake, root permission, live module checksum,
`/proc` inventory, atomic snapshot or physical partition dump is claimed.

## 1. Inputs and tools

Inputs are preserved from the Sony 1.01 package identified in
[E0506](0506-a6400a-offline-qualification-and-debugger-boundary.md), under
`firmware/extracted_a6400a_v101/`. The model query does not re-read the live
firmware version or attest that the camera's installed files equal this package.

| Component | Bytes | SHA-256 |
| --- | --- | --- |
| `usb_extcmd.ko` | 14,796 | `d47d05ec891d18c8582baf19e413967c5d3fd5c36438d76f6d2e8a7f112428e3` |
| `usbg_storage.ko` | 67,036 | `3c79156a3f3e9fc1a1bff733d33647a1121a5688aed87271647a14d8b9c346b2` |
| `libusbcmd.so` | 15,996 | `23b94deb094d31f160cfe1d46330f2425e94d4801dfccb66720c4cf4b81cfb7e` |
| `libInfraDevInfoSender.so` | 13,184 | `db4a0c97ccbd5fad6acec2354dbfeea12c50b2e93e7d346d02e280d7f70d896e` |

The existing project venv reports Python 3.14.4, Unicorn 2.1.4, Capstone 5.0.9
and pyelftools 0.33. No dependency or toolchain installation was performed.

| Tool | SHA-256 |
| --- | --- |
| [msc_extcmd_trace.py](../../tools/research/msc_extcmd_trace.py) | `c232244ee4faa6899e4b472c73abde3102d112f2c8395f874017fdff128e12f8` |
| [a6400a_native_readonly.py](../../tools/research/a6400a_native_readonly.py) | `0b0a2a1145288a5f24af77de802aa78c9f9cce6ba16a82627d814b4bc5ca8939` |
| [a6400a_debugger_presence.py](../../tools/research/a6400a_debugger_presence.py) | `ae1c48aeee31ba77be5ba033fa4d643fb8d5d108d10ac7d95b352d26ea283ba0` |

`input-tool-identities-final.json` retains the remaining tool/test/C-source
identities and rechecks all four original input hashes. The initial identity
receipt is preserved separately; hashes were not silently refreshed.

## 2. REA availability and evidence

The connected Windows MCP server reports REA 6.9.0. No deep provider is
configured: Ghidra reports `not_configured`. Portable inspection is available
and was used without starting Ghidra, changing registration or modifying inputs.

- Kernel-module metadata:
  `ev_2f0c999bbfdd65caf924f8235af5e5ca1ff06cf6ffbe530a0c77b9591fa79552`.
  This identifies the ELF32 little-endian ARM relocatable module and its
  declared vermagic, not a loaded kernel/module.
- Complete library export inventory:
  `ev_ffb6c0cbf8bd8ce41b123967a07217785aa9361d842d66e2a2bf9cf2e043c065`.
  The inventory contains 76 exports; declarations do not prove callability.
- Summary and selected export views:
  `ev_7b513218f8e3a4aa944f51abc7a5fe813c088656517d475fbefa6d103470e76a`
  and `ev_0b722ceedcf7b5aabb5002589a354b296798106f068f936df2fdb6ee1819c4a8`.

The canonical records are retained locally in `rea-evidence-initial.json`.
ARM instruction observations and runtime fixtures below come from the existing
Capstone/Unicorn tools, not REA decompilation or camera execution.

## 3. Offline ABI boundaries

The opt-in `--debugger-abi` leaves the earlier model-info controls enabled and
does not broaden any physical transport admission:

| Operation | ioctl | Original `.text` stop offset |
| --- | --- | --- |
| Create | `0xc00ceb00` | `0xcbc`, before user-access checking |
| Destroy | `0xc002eb01` | `0xe94`, before user-access checking |
| Receive | `0xc014eb02` | `0xf5c`, before user-access checking |
| Send | `0xc014eb03` | `0x1200`, before user-access checking |

Create's field-writing window is `.text+0xdac..0xe14`; the stop precedes
`__rt_mutex_init`. A second fixture preserves the full feature `0x1234` and
distinct 512/2048-byte receive/send values, rather than checking only defaults.
The earlier public-to-driver packing replay also passes: 24 input bytes become
20 driver bytes with reserved bytes untouched.

These are closed instruction windows with declared memory, frame, allocation
results and callee fixtures. They do not perform allocation, production locks,
waitqueues, registration, data transfer, cancellation or complete cleanup.

The fresh single-target dependency inventory produces 22 bounded static
functions, no graph truncation, and 44 unfulfilled REA queries. Generated WSL
commands were not executed. The initial planner failed because the historical
default inventory was missing; its failure is retained, and the corrected run
uses a separate hash-checked inventory.

Identity-matched earlier local list-lifecycle receipts remain at
`artifacts/analysis/e0503-a6400a-usb-list-lifecycle/replay-02/report.json`.
Their populated `list_finalize` fixture stops at `.text+0x31c` reading poison
address `0x100` while the mutex remains held. Original instructions corroborate
that path. This is not a physical crash observation. **Do not unload or replace
the stock module** as a debugger bootstrap or cleanup operation.

## 4. Approved physical observation

The first normal-mode attempt failed at Windows handle creation with
`WinError 5`; its receipt has **zero camera operations**. The owner then approved
UAC elevation for only the bounded normal-mode observer.

The elevated process completed with exit **2**, deliberately not a debugger
readiness certificate. Between `18:46:50.808533Z` and `18:46:52.119682Z`:

1. Parent-verified camera LUN 0 remains `054C:0F0F`, USBSTOR, OK/error 0.
2. One 36-byte SCSI inquiry succeeds.
3. One model request is acknowledged; the first response is BUSY with no data.
4. The next response succeeds within the existing eight-second transaction
   budget: ILCE-6400A / `0032858730`, elapsed **0.204600 s**.
5. One 4,096-byte IN for CDB `7a2300000000000000000000` returns SCSI status 2,
   sense **`05/20/00`**, transfer **0 bytes**. No channel polling follows.

Only the stock model request has an OUT payload; **no debugger packet is sent**.
The observer does not call updater initialization, Senser, a shell, file
read/write, storage-sector commands or a helper launcher. Instance serials and
model property-list/serial data are not retained.

The package's original readiness logic associates this sense with a missing
matching node. The live result establishes no usable channel in this state,
not that a daemon can never exist or that the live driver equals the package.
Earlier same-identity local observations show the same channel sense; they are
historical receipts, not new execution or a live module checksum.

The metadata-only postcheck issues no camera protocol requests and retains the
same healthy USBSTOR association. The model reply demonstrates responsiveness
during the probe. `/proc/uptime`, debugger responsiveness and `/tmp_bt` contents
cannot be measured without qualified camera-side access. No camera helper,
hook or module was created, so no such cleanup is claimed or needed.

## 5. Validation and receipts

All new receipts remain private under
`artifacts/analysis/e0511-6400a-debugger-qualification/`.

| Command / check | Result |
| --- | --- |
| `.\venv\Scripts\python.exe -m unittest tests.test_usb_debug_abi_trace tests.test_usb_debugger_compat tests.test_setup_usb_debugger tests.test_a6400a_native_readonly tests.test_msc_extcmd_trace -q` | **40 run, 3 optional skips, no failures/errors** |
| `.\venv\Scripts\python.exe -m tools.research.msc_extcmd_trace --debugger-abi --output-dir .\artifacts\analysis\e0511-6400a-debugger-qualification\abi-replay-01` | Exit **0**; exact ioctl, feature, registration and list shapes verified; `deployment_qualified=false` |
| `.\venv\Scripts\python.exe -m tools.research.usb_debug_abi_trace --library .\firmware\extracted_a6400a_v101\firmware.tar_unpacked\0700_part_image\dev\nflasha15_unpacked\lib\libusbcmd.so --sha256 23b94deb094d31f160cfe1d46330f2425e94d4801dfccb66720c4cf4b81cfb7e --output .\artifacts\analysis\e0511-6400a-debugger-qualification\packing-replay-01.json` | Exit **0** |
| `.\venv\Scripts\python.exe -m tools.research.a6400a_debugger_presence --check-channel --output-dir .\artifacts\analysis\e0511-6400a-debugger-qualification\native-presence-elevated-01` (approved UAC process) | Exit **2**; completed observation, no usable channel, no debugger verification |
| `.\venv\Scripts\python.exe -m tools.research.a6400a_debugger_presence --output-dir .\artifacts\analysis\e0511-6400a-debugger-qualification\native-postcheck-01` | Exit **0**; metadata only, zero protocol operations |
| `.\venv\Scripts\python.exe -m artifacts.analysis.e0511-6400a-debugger-qualification.offline_suite_02` | Exit **1**; **1,742 run, 1 failure, 4 errors, 151 skips**, not green |
| Pylance diagnostics | No errors in modified Python tools/tests; callback/platform hints are not errors |
| Input/installer guard audit | Four firmware hashes unchanged; original-target compatibility false; a6400A not registered for install |

The first full-suite guard incorrectly rejected the existing host-lock
subprocess's import of the client. That failed run is preserved. The corrected
guard identifies actual physical-client CLI execution, and the host-lock case
passes. Four remaining errors require the missing E0322 descriptor/overlay
manifest; the remaining failure expects original-a6400 libc/HDMI symbols.
Their source locations are unchanged.

Both full runs deny a legacy raw SD open before access. The sampler's subsequent
synthetic-memory `verified` label is not physical verification, and is not
credited here. No SD data was read or written. No test or production guard was
weakened to obtain a green result. The dashboard's **1,787-test E0510 passing
record remains historical**, not this host's new result.

Documentation/dashboard checks and final artifact identities are retained in
`validation-final.json`; the historical passing count is not replaced by a
non-green run.

## 6. Remaining qualification gates

1. Establish a reviewed, body-specific nonpersistent execution entry; none was
   invoked or qualified by the one-shot probe.
2. Qualify the actual payload against the target libc/loader and live module
   bytes, retaining exact identities and existing-file receipts.
3. Verify registration, bounded request/response, root permissions, full
   transfer/Destroy behavior and recovery before trusting partition acquisition.
4. Keep the existing installer blocked until physical qualification is recorded.

The current daemon source opens `/setting/a6400_usb_debugd.log` on startup; the
launcher also writes a `/setting` log and provides persistent startup behavior.
**Launching unchanged sources or release payloads is not a RAM-only,
nonpersistent experiment.** Such a path needs its own reviewed implementation
and verification, not merely a different host filename.

Service-mode entry, uploads, startup changes, driver replacement and camera
storage writes require separate explicit scope. No additional physical probe
is authorized by this record; do not repeat the unchanged channel request.
