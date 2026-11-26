# Native USB debugger (installed; normal MSC verified)

## Per-body qualification

The E0667–E0675 links in this section refer to the
[local a6400A evidence series](CURRENT_STATE.md#local-ilce-6400a-firmware-dump-research).

[E0675](evidence/0675-a6400a-expanded-bootstrap-review-and-full-dump-boundary.md)
re-verifies native identity and postflight in unchanged USBSTOR after the owner
expands acquisition qualification. Feature 0x23 still returns 05/20/00 without
data. Ghidra traces internal-flash writes before the examined Jiritsu library
load and Fsys writes in MPR's read-named path; neither is a qualified RAM-only
bootstrap or general host reader. Existing captures pass hash checks, but no
new partition bytes or helper are acquired/staged. Installation guards remain
unchanged. Full firmware acquisition is still technically blocked.

[E0674](evidence/0674-a6400a-readonly-msc-lun-capture-and-flash-boundary.md)
adds a separate opt-in, size-guarded reader for the a6400A's existing 2 MiB
MSC LUN 1 through unchanged USBSTOR. Actual bytes and independent hashes
agree; `nflasha23` backing remains unattested. No SD-sector read, upload,
mode change or storage write is available. Fresh native postflight responds,
but feature 0x23 IN still returns 05/20/00 with zero bytes. This is not a
debugger, a general raw-partition reader or a complete firmware backup.
The old inquiry/model-info and Senser allowlists are unchanged.

[E0673](evidence/0673-a6400a-first-actual-nflasha5-capture.md) obtains one
actual a6400A raw partition: nflasha5, 60 MiB, with matching streaming and
independent host hashes. The stock fixed callback is not the custom root
debugger and cannot select other nodes. STOP is unacknowledged, normal
WinUSB enumeration is not cleanup proof, and owner recovery/postflight remain
explicit. The other partitions and persistent debugger are not qualified.

[E0672](evidence/0672-a6400a-guarded-fixed-acquisition-and-supervised-gate.md)
adds a separate opt-in fixed-nflasha5 client, not the native root daemon. It
requires fresh original-driver/native-model receipts, owner-confirmed 1.01,
the same admitted 0336 handle and actual write counts. Exact-capacity
constructed disk/hash and interruption tests precede its supervised first
use. Unknown transfers retain partial files and defer unqualified STOP;
unacknowledged exit still requires recovery. No installer guard changes.

[E0671](evidence/0671-a6400a-fixed-reader-framing-and-completion-controls.md)
adds bounded offline fixed-node transfer logic and exact 60 MiB constructed
replay. Original completion extraction/destruction reaches the release
callback under declared fixtures; delivery failure leaves the mapping active.
Cancellation drains valid input but never claims camera cleanup. The guarded
live 0336 adapter and real exit/recovery remain unqualified. Keep the camera
disconnected; the existing FF01 acquisition client is not this new reader.

[E0670](evidence/0670-a6400a-ghidra-reader-consumers-and-fixed-partition-candidate.md)
uses the connected Ghidra backend and original instructions to distinguish
scratch/IPC/file read consumers. A stock fixed nflasha5 callback is a candidate,
not a debugger or a general partition reader. It still needs bounded host
streaming and normal/cancel/error recovery qualification before the next
camera connection. No original installer or preload hook is enabled.

[E0669](evidence/0669-a6400a-offline-senser-dispatch-and-native-read-gates.md)
explains the a6400A file-read refusal entirely offline: no FF01/FF03 factory is registered
under any stock authentication table, and the reply builder itself generates
version 3. TERM_MODE is excluded; other adjustment read names are not yet safe
reader contracts. Native features 1/2/4/6/7 have original registration controls,
but the network handler can delete configuration/write backup before getters.
Do not treat all native "Get" requests as read-only. No installer guard changes.

[E0668](evidence/0668-a6400a-live-senser-entry-and-recovery.md) verifies live
a6400A Senser authentication and `054C:0336` enumeration under owner-authorized
driver/mode changes. The initially missing service driver is now pre-staged
and binds correctly in two repeated runs. The bounded file request returns
status `0x80` with zero payload; no reader or installation is qualified.
An earlier owner restart restored USBSTOR; later STOP attempts are
unacknowledged but normal USB re-enumeration is observed.
This does not qualify the persistent native debugger or weaken its identity checks.

The installed/verified statements below refer to the original **ILCE-6400**,
not every BIONZ X body. For **ILCE-6400A 1.01**,
[E0506](evidence/0506-a6400a-offline-qualification-and-debugger-boundary.md)
verifies packaged API packing offline and observes host Mass Storage `054C:0F0F`,
but does **not** qualify installation or native debugger access. Its
`usb_extcmd.ko` is not identity-equivalent to the original input, and USB kernel
addresses moved. Do not run the original installer or bypass its identity checks.
Bootstrap/service-mode entry and startup writes need a separately reviewed,
explicitly authorized body-specific procedure before any live partition dump.

[E0508](evidence/0508-a6400a-native-readonly-transport-and-model-info-busy.md)
adds a separate, tightly allowlisted Windows USBSTOR inquiry/model-info probe,
not an installer. Inquiry works in the owner's Administrator session; the model
query remains BUSY after the bounded read-only completion window. Do not retry
that request, increase polling, run generic PMCA `infoCommand` (which also
initializes the updater), or infer debugger access from SCSI acceptance.
The camera is still in unchanged Mass Storage; no native debugger is qualified.

[E0509](evidence/0509-a6400a-msc-reply-lifetime-and-verified-native-identity.md)
supersedes that normal-query boundary: a fresh single-process transaction
returns ILCE-6400A in 2.727 ms through unchanged USBSTOR. Original packaged code
uses a finite reply lifetime, so the client refuses delayed continuation and
counts request time in one bounded transaction budget. This is **not** debugger
or partition access. Do not interpret the successful model query as permission
for service mode, uploads, startup changes or driver replacement.

[E0552](evidence/0552-a6400a-nflasha5-wbi-jiritsu-names.md) reads the local
`nflasha5` dump as a WBI1 snapshot. The expanded strings name `libJiritsu.so`,
`JIRITSU_GET_FILE` and `/tmp/jiritsu/libJit.so`. That is why a stock
FileControl installer cannot stage the debugger, and why a whole-file Jiritsu
read can return small firmware files without becoming a raw-flash reader.
`python -m tools.research.a6400a_jiritsu_bootstrap --mode audit-wbi` recounts
those strings. [E0554](evidence/0554-a6400a-v101-jiritsu-command-dispatch.md)
then reads the official 1.01 `libJiritsu.so`: its dispatcher selects
`GET_FILE` with halfword 8, not the public ZV-E10 value `0x4008`.
Without the explicit experimental owner policy, camera modes still return
before USB. `A6400_EPHEMERAL_RAM=1` keeps a
future daemon log on `/tmp_bt` or `/tmp` and waits longer for
`/dev/usb/usb_extcmd`; it has not been started on a camera.
[E0643](evidence/0643-cxd90045-miscreg-efuse-and-a6400a-jiritsu-chunk-planner.md)
confirms synchronous `dlopen("/tmp/jiritsu/libJit.so", RTLD_LAZY)` at `0x5D08`
in `adj_cmd_set_file2` and adds the offline `--mode plan-chunk-dump` planner
(`<= 512 KiB` single reused RAM chunk slot, allowing `/dev/nflashaB0`/`B1`
while rejecting `/dev/nflasha23`) to avoid `tmpfs` OOM and watchdog resets.

The [E0643 supervised follow-up](evidence/0643-cxd90045-miscreg-efuse-and-a6400a-jiritsu-chunk-planner.md#5-supervised-ilce-6400a-temporary-worker-follow-up)
adds a guarded ordinary-file worker/client, not a USB serial dump. The
controller stages/pulls/hashes/removes one data file at a time, at most
512 KiB, and retains partial output. `build-loader` is offline; experimental
`check`/`jiritsu-dump` require fresh native identity/original-binding receipts,
firmware confirmation and `--owner-authorized-jiritsu-workspace`. Dumping
also requires a locally built, ABI-checked loader manifest. Normal entry and
driver changes are separate supervised actions, not automatic side effects.

Three actual attempts obtained no partition bytes. Nonce-tagged shell output
identifies overlapping `rootfs`/`ext2` parent mounts, not a qualified RAM
directory; the last ready exchange timed out. Owned files were subsequently
hash-checked/deleted with `cleanup-worker --owned-receipt ...`, without
uploading another worker. USBSTOR and post-battery native responsiveness were
verified. The target stays `reference`; no usable root debugger, safe RAM
backing, successful STOP or complete dump is claimed. Resolve mount backing
or review a private bounded RAM mount before another physical attempt.

The [safe RAM qualification follow-up](evidence/0643-cxd90045-miscreg-efuse-and-a6400a-jiritsu-chunk-planner.md#6-safe-ram-workspace-qualification)
uses `--mode qualify-ram --private-tmpfs` as a separate owner-authorized mode.
Native stat/statfs and mountinfo show that `/tmp_network` and `/tmp_bt` are on
`nflasha7`/ext2, not a proved RAM buffer. The new exclusive private tmpfs is
physically verified at exactly 2 MiB with nodev/nosuid/noexec, and its unmount
and owned-directory removal are acknowledged. Its native binding recheck
passes, but free memory remains below the unchanged 8-MiB floor; no pattern
or partition data transfer is claimed. Compact diagnostics and a five-check
wait do not bypass that guard.

Future failed-run recovery uses the bounded `ownership.json` with
`--owned-receipt`, retaining the complete wire receipt separately. Private
qualification still requires fresh identity/original-binding receipts and
the loader/native-probe manifests. It does not enable private mounts for
`jiritsu-dump`, automatically replace drivers or qualify a general debugger.
Future data tests also require observed inactive swap; tmpfs type alone does
not prove exclusive RAM residency. That final safeguard was tested offline,
not observed on the camera during this qualification.

The [2026-11-21 native-reader path](evidence/0643-cxd90045-miscreg-efuse-and-a6400a-jiritsu-chunk-planner.md#8-read-only-cache-reclamation-atomic-ipc-and-all-17-allowed-partitions)
additionally admits `--mode jiritsu-dump --native-reader --private-tmpfs`
under the existing explicit owner, firmware, fresh-receipt and build policies.
It requires six verified known-pattern transfers, retires their RAM slot,
reclaims unmapped boot/asset page cache via read-only `POSIX_FADV_DONTNEED`
(`MemFree` `~24.7 MiB` above the unchanged 8-MiB floor), publishes responses
via atomic `rename(output.tmp, output)`, and admits only typed
inventory/chunk/removal operations. Default entry and arbitrary-shell guards
are unchanged. Live raw reads complete and SHA-256 verify all **17 allowed
nonzero `nflasha*` partitions (`1,354,694,656` bytes)**, matching E0673's
`nflasha5` bit-for-bit. Inactive swap, owned cleanup, original USBSTOR and
post-battery native responsiveness are observed. This does not qualify a
general debugger, factory-pristine whole-eMMC backup or raw `nflasha23` access;
original unacknowledged STOP receipts remain unchanged.

[E0667](evidence/0667-a6400a-debugger-abi-and-live-channel-boundary.md)
adds opt-in original debugger ioctl/registration/list-deletion fixtures. An
approved normal-mode, elevated observation verifies ILCE-6400A, but one feature
`0x23` IN returns **05/20/00 with zero bytes**. No usable debugger channel is
observed; installation and partition access remain unqualified.

The separate [channel observer](../tools/research/a6400a_debugger_presence.py)
defaults to host metadata only. `--check-channel` verifies normal model identity
and permits exactly one channel IN, not a debugger request, shell or launch.
An observation exits 2 because it cannot certify debugger readiness. Neither
this observer nor the ABI fixtures authorize running the original installer.
The current daemon/launcher sources write `/setting` logs; an unchanged payload
is not a nonpersistent RAM-only bootstrap.

## Protocol v2 (E0401): warm log readout, recover, and the PC Remote boundary

The installed daemon now speaks protocol v2 (same 4096-byte frame, feature
`0x23`, opcode `0x7a`) and adds four operations while keeping the v1 surface:

* `usb-state` — UDC/module/node/klog/debugger receipt.
* `dmesg` — non-destructive kernel printk readout over USB (`SYSLOG_ACTION_READ_ALL`);
  no SD card needed.
* `dump-ring` — the camera kernel's warm-reboot klog ring at the
  `/proc/cmdline`-verified `klog.addr` (`0x035a5000`, `0x20000` bytes), read
  through `/dev/mem` O_RDONLY, validated against `/proc/iomem`, CRC32-checked,
  and split into per-boot segments. A warm reboot keeps the previous boot's log
  and appends the next boot's log (decoded from the preserved `vmlinux.bin`).
* `recover` — process-context removal of allowlisted authored modules with a
  receipt; `recover --to-msc` additionally asks a PTP-mode camera to reconnect
  to Mass Storage (`SonyReqReconnect 0x9282`), where that operation exists.

**Measured boundary:** this firmware's stock PC Remote mode (`054C:0CAA`) exposes
only PTP `0x1001-0x100A/0x101B/0x9201/0x9202/0x9205/0x9207/0x9209` — no
`SonyDiExtCmd 0x9280/0x9281` and no `0x9282`. The debugger therefore answers in
Mass Storage and survives a full MSC→PC Remote→MSC round trip with the same
pid, but is **not reachable while PC Remote is active**. Recovery from PC Remote
is currently the camera's own menu switch (zero battery pulls), or a future
composite-gadget interface. An "MTP" USB setting was requested but the camera
re-enumerated as Mass Storage before each recorded session, so MTP-mode
reachability and a live in-band MTP→MSC switch are **not** claimed; the
`0x9282` request is sent on the same open MTP session when one is present. See
[E0401](evidence/0401-usb-debugger-v2-ring-recover-and-mode-boundary.md).

`tools/research/upgrade_usb_debugger.py` stages a new daemon over USB
(`/tmp_bt` → `/setting`) with a preserved rollback copy and never needs service
mode; the daemon retries lock/registration so the old instance can be stopped
without leaving the camera without a debugger.

Research update, 2026-11-06: see E0106–E0109 for complete active-menu capture,
original lookup/geometry/hit/selection controls and the remaining activation
resource-manager boundary. The one-shot main-thread helper reads TLS/registers
and detaches; it does not establish the originating UI worker. Operational
workflow: [live camera plus emulator](LIVE_CAMERA_EMULATOR_WORKFLOW.md).
Historical statements below that the debugger was not yet installed refer to
pre-E0092 preparation, not its current installed state.

Latest menu investigation: E0097 captured the actual PAS_MenuListTab tree and
touch coordinates during disconnected MENU interaction. The root inherits an
empty mouse handler; only GEN_Button among 21 sampled widget types has a
specialized handler. Touch menu remains incomplete: the original application
selection/activation contract still needs verification. Targeted observers
were built and verified running, logs preserved, then stopped. See
`docs/evidence/0097-real-menu-widget-tree-and-mouse-handlers.md`. Use host `python`
for the client in this environment; the workspace venv lacks Crypto.

Hardware status: installed with read-back verification, running as PID 1793/uid=euid=0 over normal USB external commands. Shell status/capabilities, command exit codes, five-second timeout, output limits, process/thread/file/memory reads and verified small binary uploads have passed camera tests. Multiple sessions worked without service-mode entry. See E0092 and the manifests under artifacts/hardware. A separate future power-cycle startup test, MTP mode, and disconnected-interface recovery remain unverified. The historical preparation/approval text below records earlier stages; it does not override this current status.

Current status: the user explicitly approved this package after the initial automatic-review rejection. Bootstrap then stopped on original-file comparisons, which were resolved as build metadata differences with code/data/relocations verified unchanged. No payload/startup write has occurred; the camera stopped enumerating before the next attempt. Resume the approved installer after physical USB reconnect. See E0091 for exact captured originals and identity checks; no repeat deployment approval is needed.

This debugger registers a userspace command handler in Sony's existing `/dev/usb/usb_extcmd` driver. Normal requests use the Sony MSC/MTP external-command transport, feature 0x23. The client never enters Senser or updater mode. MSC operation is the first hardware test target; MTP support is implemented but unverified.

The previous Antigravity bridge in tools/usb_session.py used Senser. Its historical task-2546 log explicitly records switching modes and a working root shell. The new channel has not yet been deployed or tested on camera; current camera identity succeeds over native USB, but both the PMCA TEST probe and the local bridge did not provide a usable normal-mode debugger.

## Intended capabilities

* `info`: server PID, uid/euid/gid, protocol and limits.
* `ps` / `threads PID`: process or thread names.
* `read /absolute/path`: chunked file/pseudo-file reads with explicit limits.
* `maps PID` / `snapshot PID DIRECTORY`: retain process comm/status/maps locally.
* `mem PID ADDRESS SIZE`: bounded read through ptrace, with attach/wait/read/detach. This temporarily stops the selected thread; it does not stop all threads or produce an atomic whole-process snapshot.
* `exec COMMAND`: root shell command, merged stdout/stderr, real exit status, five-second deadline and explicit output truncation. These commands can modify the camera when the caller requests it.
* `push LOCAL REMOTE`: bounded small binary upload through supported shell printf, with full read-back verification, without service mode.
* `stop`: unregister this USB feature and exit the debugger process.

No network listener is created. No kernel patch, global security setting change, flash partition rewrite, or camera UI replacement is part of this debugger. Effective permissions must be measured on hardware; no privilege beyond root is claimed.

## Build and validation

Run `venv\Scripts\python.exe -m tools.research.build_native_usb_debugger` from the project root. It uses the installed WSL ARM compiler and original Sony libc, with explicit time/file ABI settings, linker undefined-symbol rejection and warnings as errors. It does not install tools or deploy to the camera. A manifest under artifacts/analysis retains source/binary hashes.

Six protocol tests cover response identity, malformed frames, chunk offsets, EOF versus limits, errno versus shell exit status, memory bounds and path validation. The full project suite passed 553 tests before final C-only cleanup. The ARM sources compile successfully; this does not validate their hardware behavior. A host C dispatch harness exists at tools/research/test_usb_debug_dispatch.c, but was not run because this WSL installation has no native gcc/cc (only the ARM cross-compiler). Timeout/truncation and native ioctl lifecycle still require runtime tests. Do not describe these C paths as exercised.

## Deployment and approval boundary

Deployment was blocked by automatic approval review: general debugger improvement authorization did not explicitly cover a persistent root USB server with arbitrary shell commands, file access and process-memory reads. Obtain the user's explicit approval for this package before running `venv\Scripts\python.exe tools/deploy_native_usb_debugger.py`. Do not work around that rejection.

The one-time installer uses Senser to read/verify the analyzed camera driver/API, upload and read back two small payloads, back up `/etc/ld.so.preload` locally, and append `/setting/usb_debug_launcher.so` while preserving existing entries. No new USB server is currently installed. After installation, subsequent debugger requests are intended to avoid service mode; native registration must first be verified.

The launcher starts the daemon once per boot, using `/tmp_bt` for its one-shot marker. The daemon holds an advisory process lock and unregisters its feature on a clean stop. `/setting/disable_usb_debug` suppresses future launches; it does not stop an existing process. `stop` does not remove preload or its launch marker. Remove only this launcher's preload entry to uninstall startup behavior, preserving other entries. Keep the installer's before/after files and report actual read-back results.

## First hardware checks after approval

Use the client with the existing USB mode and verify `info`, `exec 'id'`, `/proc/self/status` effective capabilities, `ps`, process maps and a small known code window. Test `exec 'printf ok; exit 7'` and bounded timeout/output behavior. Retain failures as failures; never auto-fallback to service/updater mode. Confirm the complete original ioctl create/transfer/destroy lifecycle before relying on repeated sessions.

The debugger is an investigation tool. The physical touch-menu mod is still not functional; further native touch-to-widget event tracing remains necessary.

## Current USB lifecycle guidance

The native daemon is a background process, so routine debugger calls no longer require entering and stopping Senser. The client serializes this workspace's sessions. It does not automatically reset USB, switch camera modes, or power-cycle a port. Simultaneous Mass Storage/Senser operation and software recovery after the gadget disappears have not been tested. Keep those device-state transitions distinct from the background process lifecycle.
