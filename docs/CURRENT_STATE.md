# Current State

For the full documentation index see [docs/INDEX.md](INDEX.md). For the verified test count and latest evidence snapshot, see [README.md](../README.md) and [UVC/PTP Status](UVC_PTP_STATUS.md).

## Active Track Summary (as of 2026-11-20 / E0677)

| Track | Current Frontier | Latest Evidence | Next Step |
| --- | --- | --- | --- |
| **L2 Full-System Kernel Boot** (`UnicornMachine`) | Both `nflasha1` updater (`E0653`) and captured `nflasha3` main firmware (`E0656`) boot natively through `DMP_2D_Initialize : 0` and `/bin/ash`. With `--qemu-peripherals`, the QEMU SYSV/VPH/evsel frame clock (`E0676`) and the oracle-backed SIO channel-3 start/done path, GPIO port-1 input `0x32`, and ADC done/`0x3FF` results (`E0677`) are on the same machine | [E0677](evidence/0677-unicorn-sio-gpio-oracle.md), [E0676](evidence/0676-unicorn-sysv-vph-frame-clock.md), [E0656](evidence/0656-unicorn-main-firmware-boot.md) | Replay captured `nflasha3` and check whether `av-cam` passes the `ObjMic` wait; CPUs 1–3 are still offline, and no Unicorn GUI frame is claimed |
| **L3 User-Space (`im.elf`)** (`InterpreterProbe`) | Unmodified `im.elf` runs to `1.50B` instructions (`120` threads, `0` heap ownership errors, no `0x4C->0x50` padding) after fixing Unicorn 2.1.4 Thumb-2 stale-`ITSTATE` bugs; `/dev/dmpgles2` 128 KiB init (`sugilite_open` 4-byte zero + `rm_rqueue_init` 128 KiB `memset`), `62` `/dev/ldec` LZ77 DMA jobs, `24` Sugilite GPU jobs + `24` IRQ 76 completions, and double-buffered `640×480` RGBA4444 `viewRootPowerSw.uxc` OSD A/B presentation verified (`THREAD_INFRA1` complete) | [E0657](evidence/0657-unicorn-l3-allocator-gles-init-and-sugilite-gpu.md), [E0530](evidence/0530-gles-tgid-completion-and-shared-memory-boundary.md), [E0526](evidence/0526-stella-allocator-size-isolation-and-padding-trial.md), [l3-userspace-and-ipc.md](architecture/l3-userspace-and-ipc.md) | Qualify external `av-cam.bin` / sub-micro `UPM` (`0x920572`/`0x920021`) and `ObjPower` (`0x0059000E`) producers to advance `DefRootView` (`CheckerBootState`/`CheckerBootType`) past transparent `viewRootPowerSw.uxc` |
| **Track B Physical UVC/PTP** (`054C:0322`) | Run B25 (`E0468`) proved `S1` (camera arms EP0 OUT status in ≤16.4 ms; host's single Status OUT was NAK'd in `NRDY→arm` window). `E0476` status pre-arm fix built (`dcc2f64f…`), audited (`117/8/0`), Unicorn-verified (`8/8 PASS`) | [E0476](evidence/0476-status-prearm-fix-build-audit-unicorn.md), [UVC_PTP_STATUS.md](UVC_PTP_STATUS.md) | Awaiting explicit user GO for single supervised Run B26 |
| **QEMU `sony-a6400` machine** (`qemu-a6400/`) | Captured four-core boot. Opt-in synthetic luma reaches the stock LCD and releases the reconstructed packaged boot's step-32 guard. A separate opt-in lens rail reply releases captured step 26. S1 wakes the stock rear OSD after Auto Power Save. Settings survive a fresh process after an explicit native save; the original file service mounts virtual SD. Diagnostic USB policies enumerate the original bus-power/HID function, but stock view-driven USB startup is unqualified | [E0591](evidence/0591-qemu-a6400-first-live-view-frame.md), [E0598](evidence/0598-qemu-token-driven-ep0-configuration.md), [E0650](evidence/0650-qemu-virtual-media-gates.md), [E0651](evidence/0651-qemu-a6400-stock-finder-monitor-rear-lcd.md), [E0652](evidence/0652-qemu-a6400-late-black-screen-scanout-capture.md), [qemu-a6400/STATUS.md](../qemu-a6400/STATUS.md) | Verify remaining application controls, automatic shutdown save, stock JPEG playback, and still capture. A synthetic chart does not qualify the sensor or recording; genuine UVC and non-EP0 data/chain contracts are separate |
| **Multi-Device Platform** (`CXD90045`) | 13 bodies / 9 kernels mapped (`E0477–E0489`); `TargetProfile` registry has `ilce_6400`, `ilce_6400a`, `zv_e10`, `ilce_6100`, `ilce_6600`, `ilce_7c`, `ilce_7m3`, `ilce_7rm4a`; read-only physical identities: `ILCE-6400A` 1.01 (`E0509`) and `ILCE-7RM4A` 1.13 (Mass Storage `054C:0D9D`, native `GetModelInfo`, `E0646`) | [PLATFORM_MODEL.md](PLATFORM_MODEL.md), [ADDING_A_BIONZX_CAMERA.md](ADDING_A_BIONZX_CAMERA.md) | Wire `TargetProfile` directly into `UnicornMachine` and `InterpreterProbe` for universal target selection |

Historical 2026-10-18 and 2026-11-06 checkpoints are preserved in [docs/archives/](archives/).

The earlier no-peripheral captured-profile [E0527](evidence/0527-qemu-task43-initialization-flag-wait.md)
decodes Task 43's first display wait as flag 10, mask 3, pattern zero, not the
later `0x3373` loop. Its unasserted IRQs and dormant Task 65 remain bounded
observations; they do not replace the later QEMU device-model and rear-panel
results above.

## CXD90045 MISCREG/eFuse & A6400A Jiritsu Chunk Planner (2026-11-19 / E0643)

[E0643](evidence/0643-cxd90045-miscreg-efuse-and-a6400a-jiritsu-chunk-planner.md)
confirms neither `DGU5` (`nflashaB0`) nor the `ILCE-6400` (`8.2.6.0`) /
`ILCE-6400A` (`8.2.7.6`) kernels verify kernel signatures, verifies live
`ILCE-6400` `MISCREG`/`eFuse` MMIO (`0xF2915000=0x00000500`,
`0xF2915010=0x0c01002b`) via a bounded single-page `/dev/mem` read, documents
synchronous `dlopen("/tmp/jiritsu/libJit.so", RTLD_LAZY)` at `0x5D08` in
`ILCE-6400A` 1.01 `libJiritsu.so`, and adds `--mode plan-chunk-dump` to
`tools/research/a6400a_jiritsu_bootstrap.py` for single-slot `<= 512 KiB` RAM
partition dumps (allowing `/dev/nflashaB0`/`B1` while rejecting `/dev/nflasha23`).

## CamCore register model (2026-11-19 / E0637)

Merged pull request #42 maps `cxd90045-camcore` over `0xF3010000` (64 KiB,
priority -500) in the default machine. The posted score keeps step 26 and
lowers distinct unmodelled addresses from 2,322 to 1,310. It was not re-run
here. The opt-in live-view CamPig window still covers this block when that
pipeline is enabled. See
[E0637](evidence/0637-camcore-f301-contributor-boot-score.md).

## Contributor emulator scores (2026-11-19 / E0614–E0618)

Comments on merged pull requests #35, #37, #38, #39 and #40 are recorded as
[E0614](evidence/0614-f280-clock-readback-contributor-boot-score.md) through
[E0618](evidence/0618-f388-readback-contributor-boot-score.md). They are the
contributor's quiet-host boot scores, not a local re-run. Reported final
state stays step 26 of 43, step `0x8004`, module bit 2 `0x0323`/`0x0320`.
Read-back windows `0xF2800000`, `0xF3500000` and `0xF3880000` reduce
unmodelled pairs. `TB_JMP_CACHE_BITS` 14 and the v11 per-block SD insertion
check are speed claims on that same state. No physical camera check is
claimed. The later [opt-in lens rail reply](evidence/0591-qemu-a6400-first-live-view-frame.md#captured-step-26-sub-micro-lens-rail-acknowledgement)
is a different replay, not part of those scores.

## USB delivery update (2026-11-18)

2026-11-19 [E0655](evidence/0655-qemu-first-view-ptp-and-native-dwc3-reset.md)
shows that early native cable delivery passes the sampled FirstView checks.
A stock Play press separately clears the observed `NeutrOn` flag and restores
`ModelCamera` readiness, but original I-InputMain still suppresses USB with
reason `0x11`. Native bulk OUT fixtures and a Sony PTP data/response validator
are separate from stock USB proof.
The Unicorn E305 replay enters the live-bound DWC3 reset and stops before
the PL310 sync write; complete startup, SETCONFIG, and teardown remain open.

2026-11-19 [E0654](evidence/0654-usb-stock-attach-and-fusb-start-guard.md)
observes the original `ModelCamera` `NeutrOn` store followed by a false
readiness result. With the stock `P` display, VBUS reaches the original jack
handler but neither starts the DWC3 gadget nor returns a DEVICE descriptor.
In Unicorn, the opt-in original FUSB `SET_MODE` leaf runs, and E305 stops
at the bound `dwc3_gadget_otg_probe`. No Sony data transfer, process-owned
SETCONFIG lifecycle, or streaming is established.

2026-11-19 [E0619](evidence/0619-native-usb-release-model-camera-readiness.md)
reaches the enclosing still-recording USB-release paths. Binding group 1
receives valid not-ready replies from `ModelCamera` (`0x0B`), while
`ModelStillRec` (`0x0F`) is ready. Paired none/bars traces match the exact
native byte load; a separate writer trace records original stores `1,0,1,0`.
The last clear returns to `libObj+0x4AA0A4`. Next is that caller's original
state dependency; no missing hardware response or connection to the separate
step-26 wait is established. No production emulator code changed.

2026-11-19 [E0613](evidence/0613-original-usb-key-inhibit-owner-and-release-boundary.md)
identifies the original application-delivery gate. Startup code queries alias
`@M001` parameter `0x100=1` and natively inhibits USB physical key `0x07000001`
(`0 -> 1`). I-InputMain matches the live `0x15040010` subscription but suppresses
it with reason `0x11`. The original clear helper and six discovered direct
call sites are unobserved in paired bounded replays. Next is authentic
still-recording initialization/release, not an injected USB event or ready flag.
No hardware model changed; 56 receipt checks pass.

2026-11-19 [E0612](evidence/0612-captured-usb-receive-and-ready-gate.md)
uses captured-code probes: original USB attach/detach messages reach Linux
I-InputRecv on `0x007A0020`, resolve key index `0x19D`, pass its suspend check,
and reach I-InputMain on `0x007A804C`. Sampled ROOT `0x102=2` and ready-checker
return `1` qualify a ready observation, not a stock USB flow. No application
USB event/ActionOpenUSB is observed. Next is I-InputMain subscription filtering
and callback delivery; neither a mandatory ORIL return hop nor missing ROOT
readiness is established as the blocker. No hardware model changed.

2026-11-19 [E0611](evidence/0611-qemu-usb-input-chain-identity-boundary.md)
starts the USB attach-chain plan. Two offline boots reach the original P
display after center and observe attach/duplicate-attach/detach edges, with
and without the bars source. Captured BK4 USB Connection is `04`. Saved
filesystem qualification finds different captured libObj/viewUnified2 code
from the packaged probe inputs; historical zero hit counts do not locate
the gate. Resolve captured entries and ORIL delivery before implementing a
missing hardware contract. Phase 1 remains incomplete.

[E0598](evidence/0598-qemu-token-driven-ep0-configuration.md) adds optional
host-driven EP0 ownership, status ZLPs and NRDY/NAK retries. Two final runs
of original firmware complete DEVICE/address/configuration exchanges and
GET_CONFIGURATION=1, with UDC configured. The original bus-power/HID function
is not UVC; non-EP0 data, chains, packetization and video remain unqualified.
The legacy descriptor replay and 44 profile checks remain unchanged; 33
native EP0 boundary checks pass.

[E0597](evidence/0597-qemu-original-gadget-start-and-device-descriptor.md)
traces E0596's CUsbSvc state 7 to E305's native -EBUSY, caused by the
unimplemented F0200024 bit-0 guard. Two physical read-only MSC samples
qualify an opt-in connected-state snapshot. Original create then returns 0,
service reaches state 3, created=1, and the driver arms its ring and RUN;
GET_DESCRIPTOR returns the original bus-power DEVICE descriptor 054C:096F.
At that checkpoint control status/address/configuration were unqualified;
E0598 adds the bounded virtual sequence, while UVC remains unqualified.
The actual captured kernel is pinned separately from earlier packaged static
leads; matching addresses do not establish matching kernel bytes.

[E0596](evidence/0596-qemu-original-usb-service-startup.md) exercises original
USB configure/start commands inside QEMU's captured process, through an
explicit identity-checked diagnostic mod. UsbControl reaches FSM 7 with
configured/started masks 4/4 and the kernel logs transfer to Gadget. The
sampled bus-power registry created byte is 0; DWC3 has no event ring/RUN or
descriptor reply. The E0594 separate-open boundary is passed through the
original service; actual class/gadgetcore activation remains unqualified.

[E0595](evidence/0595-dwc3-physical-hardware-parameter-profile.md) adds a
physical-camera-backed, opt-in QEMU DWC3 GHWPARAMS profile. Two MSC read-only
samples agree; 20 native qtest reads preserve the historical default and
verify the new values. This qualifies identity registers, not the encoded
capabilities or transfers. The E0594 exclusive-open/original-service startup
boundary remains open.

[E0534](evidence/0534-original-process-usb-gadget-registration-and-otg-boundary.md)
regenerates original `im.elf` state and uses its libc heap, pthread state and
queues for configure/start commands. Original actions reach FSM
`1 -> 5 -> 6 -> 7` and create/link a bus-power gadget registration record.
`created=0`: original `createGadget` stops at unqualified OTG `SET_MODE`
`0x4001E30C`, mode 2. No hardware reply or SETCONFIG event is supplied.
Configure-stage heap ownership balances; the pending start retains its owners.
The boundary checkpoint retries the unanswered ioctl rather than skipping it.
Next for this separate Unicorn path: native OTG/class/gadgetcore startup contracts, then original event
production, delivery and full teardown. End-to-end configuration remains open;
[E0524](evidence/0524-original-local-osal-setconfig-route.md) is still the separate
isolated SETCONFIG delivery proof.

## A6400A service-packet update (2026-11-17)

[E0532](evidence/0532-a6400a-dmm-metadata-bounds-and-lifetime.md) extends
[E0525](evidence/0525-a6400a-jiritsu-senser-offline-packets.md) with an explicit
`--dmm-fixtures` policy: original 24-byte metadata exchanges, separate share/open
counter controls, bounded borrowed-buffer lifetime and eight repeatable packets
to a memory-only sink. Raw lengths 0, 1 and 4096 serialize exactly; failed
metadata/release completions cannot authorize sending. The physical fixture
address stays unmapped. Default replay retains all seven earlier packets and
stops before DMM metadata. Authentic allocation, multi-extent replies and full
owner teardown remain unsupported. No authentication, filesystem service, USB
command or physical compatibility is claimed; the profile remains `reference`.

## GPU completion update (2026-11-17)

[E0530](evidence/0530-gles-tgid-completion-and-shared-memory-boundary.md) verifies
the original same-TGID completion/reset and fast-flag rules, with selective
modeled cross-FD wake and no user-stack context guessing. The 128 KiB
`dmpgles2` mapping is software-shared memory, not the 16 KiB GPU BAR; bit 24
selects caller recovery, not event presence. Historical assumed engine
completions are not qualified GPU work.

Opt-in `--gles-device kernel-bound-tgid` preserves unknown shared bytes and
unsupported MMIO as explicit stops. Two original-process replays stop at
146,135,735 instructions, `libObj+0x72BF96`, on the first shared-memory read.
OSD A/B remain unmapped; no firmware-produced display frame is claimed.
Next: establish real shared backing/initialization, then functional GPU
operations, IRQ and guest-driven presentation without assumed success.

## Local ILCE-6400A firmware-dump research

The links below refer to the local a6400A E0667–E0675 evidence series
(2026-11-14 to 2026-11-16), not the upstream menu records with those IDs.
Each entry records the state at the time, including limits later superseded.

**E0643 native reader — all 17 allowed partitions (2026-11-21):** the
[complete allowed-partition follow-up](evidence/0643-cxd90045-miscreg-efuse-and-a6400a-jiritsu-chunk-planner.md#8-read-only-cache-reclamation-atomic-ipc-and-all-17-allowed-partitions)
uses read-only `POSIX_FADV_DONTNEED` on packaged boot/asset files (`MemFree`
`8,500 -> 24,660 KiB`, ending at `25,432 KiB`), `POSIX_FADV_RANDOM` on raw
sources, and atomic `rename(output.tmp, output)` to capture and SHA-256 verify
all **17 allowed nonzero `nflasha*` partitions (`1,354,694,656` bytes:
`nflashaB0`, `B1`, `1..7`, `10..13`, `15..18`)** without lowering the 8,192-KiB
guard (`nflasha5` matches E0673 bit-for-bit). Private `tmpfs` unmount/rmdir,
owned-file cleanup, `USBSTOR` restoration, owner battery-removal recovery, and
native postflight pass. Excluded `nflasha23` and whole-device `nflasha` remain
unacquired, and mounted partitions (including `nflasha6`) are non-atomic live
snapshots rather than a factory-pristine full backup.

**E0643 safe RAM qualification:** the
[diagnostic/private-probe follow-up](evidence/0643-cxd90045-miscreg-efuse-and-a6400a-jiritsu-chunk-planner.md#6-safe-ram-workspace-qualification)
identifies `/tmp_network` and `/tmp_bt` on nflasha7/ext2, then physically proves
an exclusive 2-MiB private tmpfs with nodev/nosuid/noexec. Native binding checks,
unmount and owned-directory removal pass. Data tests stop before creation:
free memory remains below the unchanged 8-MiB floor even after a bounded wait.
Owned helper cleanup, USBSTOR restoration and final post-battery native
responsiveness pass. Next: qualify memory headroom/transfer footprint, not
lower the guard or claim 512-KiB transfers. No new partition bytes acquired.

**E0643 supervised worker follow-up:** the
[bounded temporary-worker implementation](evidence/0643-cxd90045-miscreg-efuse-and-a6400a-jiritsu-chunk-planner.md#5-supervised-ilce-6400a-temporary-worker-follow-up)
uses USB file pulls of one reusable 512-KiB RAM slot. Three startup attempts
obtain no raw partition bytes. Actual nonce-tagged output identifies
overlapping rootfs/ext2 mounts; their active backing is not qualified. The
last ready exchange times out. Hash-matched helper/log files were removed in
a cleanup-only session, followed by verified USBSTOR restoration and native
responsiveness after owner battery removal. Next: resolve mount backing or
review a bounded private RAM mount, not weaken guards or repeat the loader.

**E0675 expanded-scope review:** [Bootstrap review and full-dump boundary](evidence/0675-a6400a-expanded-bootstrap-review-and-full-dump-boundary.md)
confirms current native identity/postflight and re-verifies existing captures,
but obtains no additional partition bytes. The owner now permits broader
qualification; host permissions and E0674's former scope are not the blocker.
Ghidra confirms internal-flash effects before the examined Jiritsu library load
and actual Fsys writes in MPR's read-named path. No speculative loader, driver
change or mode entry is attempted. A general reader or verified temporary
bootstrap, live inventory and complete firmware capture remain unqualified.

**E0674 read-only normal-mode capture:** [MSC LUN capture and flash boundary](evidence/0674-a6400a-readonly-msc-lun-capture-and-flash-boundary.md)
saves 2,097,152 genuine LUN 1 bytes through unchanged USBSTOR, SHA-256
`824fb2144bca8fb3dfabffeb9e132408604bae37296b209df5e590be07db2027`,
with independent host agreement. The matching-size `nflasha23` backing is
unattested; retain the neutral `msc-lun1.bin` name. Native postflight responds,
but the one debugger-channel IN returns 05/20/00 and no data. nflasha5 is
skipped. Other raw partitions/full firmware remain blocked under the owner's
existing-read-only/no-upload/no-mode-change scope. No old Senser cleanup or
live `/proc` state is inferred.

**E0673 physical capture:** [First actual nflasha5 dump](evidence/0673-a6400a-first-actual-nflasha5-capture.md)
records 62,914,560 real camera bytes, SHA-256
`d131b73f9c4610bdc3f549a65e94d70c11cc92d3071b878d65ef46a8fe13b3b6`,
with streaming and independent host readback agreement. One initial handoff
stops on unexpected 0994 before sending a data request; immediate entry/read
after owner restart succeeds. STOP remains unacknowledged and recovery is
not inferred from normal WinUSB enumeration or the screen message. Remaining
partitions still need a genuine general reader or reviewed temporary helper.

**E0672 guarded acquisition update:** [Live-client qualification and supervised gate](evidence/0672-a6400a-guarded-fixed-acquisition-and-supervised-gate.md)
adds a separately opt-in 0336 adapter/client without weakening the old FF01
guards. It retains actual write counts, exact-capacity disk/hash verification,
partial bytes, one-shot ownership and bounded cooperative draining. An
incomplete transfer defers unqualified STOP and requires owner recovery.
Next is a supervised fresh normal-mode identity check and fixed-nflasha5
attempt, not a claim that every partition or actual cleanup is qualified.

**E0671 offline update:** [Fixed-reader framing and completion controls](evidence/0671-a6400a-fixed-reader-framing-and-completion-controls.md)
adds a bounded fixed-command parser and exact 60 MiB constructed replay of
original framing, with short-I/O, cancellation/drain and deadline controls.
Original mapped-response extraction arms mode-10 completion before streaming;
the destructor reaches the actual release callback under declared OSAL/libc
fixtures. Failed completion delivery leaves the mapping/descriptor active.
No live adapter, forced pthread unwind or acknowledged USB STOP is qualified.
Keep the camera disconnected; no raw capture or general partition reader exists.

**E0670 offline update:** [Ghidra reader consumers](evidence/0670-a6400a-ghidra-reader-consumers-and-fixed-partition-candidate.md)
identifies local RAM_READ as a 256-byte scratch read, distinguishes Jiritsu's
stat-size contract, and finds a level-3 fallback to a fixed `/dev/nflasha5`
read-only callback. Capacity and explicit cleanup pass constructed original-code
controls, but mapping failure retains stale fields and host/cancel teardown is
unqualified. Keep the camera disconnected. The own-image loader reaches guarded
preload-file access after 3,109 instructions; no preload is supplied.

**E0669 offline update:** [Senser dispatch and native read gates](evidence/0669-a6400a-offline-senser-dispatch-and-native-read-gates.md)
reproduces the generic missing-handler `0x80` decision and server-generated
version 3. FF01/FF03 are absent from all stock levels; TERM_MODE is excluded,
but selected RAM_READ requests pass admission before unqualified routing.
Native feature 7 can perform configuration deletion/backup writes before a
getter. No camera access or installer is used. The own 1.01 loader stops at
`brk(0)` after 1,183 original instructions with zero syscall replies.
Profile selection alone does not retarget the older L2/L3 CLI image sources.

**E0668 update:** [Live Senser entry and recovery](evidence/0668-a6400a-live-senser-entry-and-recovery.md)
now verifies both WinUSB bindings and repeated live authentication at the
original port. The bounded file request returns `0xFF01` / sequence 1 /
version 3 / status `0x80`, with zero payload. No inventory or dump is accepted.
Host observations confirm normal `054C:0F0F` after unacknowledged STOP controls;
the earlier owner restart/USBSTOR recovery remains historical.
Original package controls corroborate absent file/memory APIs and terminal
refusal. No stock CXD90045/ILCE-6400A updater-shell image is available.
The remaining gate is a body-qualified execution or alternative reader route.

[E0667](evidence/0667-a6400a-debugger-abi-and-live-channel-boundary.md) passes
bounded original debugger ioctl, registration-field and list-deletion fixtures.
The approved normal Mass Storage observer verifies ILCE-6400A but feature `0x23`
returns `05/20/00` with no data. The camera remains a reference target with no
qualified root debugger or physical dump access. Next: review a body-specific
nonpersistent entry and payload/live-module/cleanup contracts, not the original
installer. The current-host full suite is not green; no missing input or guard
was weakened. REA 6.9.0 is connected for portable inspection; Ghidra is unconfigured.
