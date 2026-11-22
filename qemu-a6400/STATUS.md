# QEMU `sony-a6400`: findings and next steps

**Declared stimulus:** opt-in, default-off `gate-force-probe=on` (`GATE_FORCE_PROBE=1`) one-shot patches the exact av-cam step-26 producer cbz at PA `0x03874fd0` to a NOP (`cond. PROBE gate-force`), not a fix.
**Declared stimulus:** opt-in, default-off `entry-force-probe=on` (`ENTRY_FORCE_PROBE=1`) makes the first av-cam entry take the r0 = 1 path while the camera-control state is 240 (`cond. PROBE entry-force`), not a fix.

## PL011 UART3 at 0xF2003000 (2026-11-21 / L0098)

The stock kernel's udif table (VA `0x604DB3D4`, name pointer `0x6044A054` =
`"uart3"`) gives base `0xF2003000` (`0x604DB4C8`), end `0xF2003FFF`
(`0x604DB4CC`) and IRQ `120` (`0x78` at `+0x128`, `0x604DB4FC`). The camera's
`/proc/interrupts` agrees: GIC 120 is `cxd900x0-uart` (25,253 hits), while
IRQ 150 is `liro-150` with 0 hits (QEMU's certified boot still registers
UART0/`ttyAM0` on IRQ 150). The `uart_devs[3]` AMBA probe read the PrimeCell ID block at
`0xF2003FE0..0xF2003FFC` through the `cxd90045.unmodelled` catch-all (eight
32-bit reads, offsets `0x2003FE0..0x2003FFC`) and got 0, so the baseline
serial console says `cxd900x0_init: amba_device_register(uart_devs[3])
failed.` (120 s baseline, line 78).

The board now maps a second stock PL011 at `0xF2003000` (size `0x1000`, GIC
SPI 88 = IRQ 120, `serial_hd(1)` = NULL because only one `-serial` is
passed); UART0/console is unchanged. No wrapper is needed: QEMU's `pl011`
already returns the kernel's expected IDs, PID `0x00141011` and CID
`0xB105F00D`. qtest (`tests/test_qemu_uart3.py`, qemu sha256
`5708b5f9…f43d9`) reads both devices' IDs, checks UART0's idle flag register
(`0x90`), and uses an unowned page (`0xF2004000`) as the catch-all positive
control; 7/7 checks pass.

A 120 s boot with the certified env (qemu sha256 `5708b5f9…f43d9`, model
sha256 `e668e8d6…3d9`) now logs `uart3: ttyAM1 at MMIO 0xf2003000 (irq = 120)
is a AMBA/PL011` and no `amba_device_register` failure. Against a same-commit
baseline (qemu sha256 `204b4d08…734de7`), exactly the eight ID reads retire
(87 -> 79 distinct catch-all read offsets; 627 writes unchanged; no new
offset), and the final sequencer state is identical (index 42, step
`0x897F`, status/mirror `0x0102`, top/b5_top/b5_opd 1, 43 steps). Console and
all other devices are untouched.

## Opt-in bus error in the reserved first MiB (2026-11-19 / E0647)

What the option does:
- **Turning it on:** use `-M sony-a6400,lowmem-abort=on` (`LOWMEM_ABORT=1`).
- **Effect:** CPU accesses to PA `0x000F8000-0x000F8FFF` get a decode error, the same AXI
  DECERR the camera returned (FSR `0x008`, ExT = 0). Each access is logged under
  `-d guest_errors`.
- **Rest of the machine:** all other unassigned addresses still read 0, through a
  lowest-priority whole-space unimplemented device.

The default board backs the whole first MiB with RAM, which is why a `/dev/mem` read that
warm-rebooted the camera passed in QEMU. With the option, the stock firmware shows the camera's
exception record for the same read (`external abort on non-linefetch`, exception monitor).
See [E0647](../docs/evidence/0647-ilce6400-devmem-reboot-root-cause-and-policy.md).

## LIRO `ram` section is av-cam data (2026-11-19 / E0644)

The work DMA copy is av-cam's own initialised RW data (1,878 references from av-cam code into
the run region), not another core's firmware; this supersedes the E0642 inference. No code that
uses it runs before the current stall, so set `WORK_DMA=1` for runs meant to get further. The guard
gains `exec_range=START:END`. See
[E0644](../docs/evidence/0644-a6400-liro-ram-section-is-avcam-data.md).

## 0xF30A fill engine and LIRO `ram` section (2026-11-19 / E0642)

LIRO `entry_pre` zeroes its bss (`0x0403A000` + `0x497A26`) with the `0xF30A` memory-fill
engine and copies its `ram` section with the work DMA (E0644: av-cam RW data). The
bss is already zero in the preloaded `av-cam.bin`, so the unmodelled fill is harmless for a cold
boot. No main-CPU access reaches the `ram` run region in 120 s; its consumer is not found. The
guard's `trace_ram=1` adds RAM tracing. See
[E0642](../docs/evidence/0642-a6400-f30a-fill-engine-and-liro-ram-section.md).

## Opt-in work DMA copy model (2026-11-19 / E0641)

`-M sony-a6400,work-dma=on` (`WORK_DMA=1`) performs the av-cam work DMA copies at
`0xF0102000` (channel `+0x1C0`, config `0x1C491000 | words`, 0 = 4096); any other mode is
refused and logged under `-d unimp`. A stock boot copies 52 chunks (847,460 B, an embedded ARM
program from `av-cam.bin`) to `0x045ACF00` with the same console output as without the model.
See [E0641](../docs/evidence/0641-qemu-work-dma-copy-model-opt-in.md).

## Work DMA contract and missing copies (2026-11-19 / E0640)

av-cam `0x635DC8B8` enables the work DMA at `0xF0102000` (`+0x030`), copies 52 × 16 KiB
through the `+0x1C0` channel (start `+0x1D0`, busy `+0x01C` bit 6) and disables it. The
handover guard now has a `[C]` contract for it, and a stock boot passes every DMA contract.
The engine is unmodelled, so av-cam sees immediate completion but **no data is copied**
(`0x044D3380…` → `0x045ACF00…`). `trace=START:END` on the guard logs MMIO PC/LR. See
[E0640](../docs/evidence/0640-a6400-work-dma-0xf0102000-contract.md).

## Handover guard DMA quiescence (2026-11-19 / E0639)

`dma=tools/handover_dma_contracts.txt` adds G10: DWC3 must be stopped and halted, PL330 and
the VPH/sensor AIF channels need an idle status read after their last write, and engines
without a known stop contract (work DMA `0xF0102000`) count as unverified. A bounded stock
boot programs only DWC3 (soft reset, never started) and the work DMA. See
[E0639](../docs/evidence/0639-qemu-handover-guard-dma-quiescence.md).

## Handover guard for cache/MMU teardown (2026-11-19 / E0638)

`tools/handover_guard.c` (TCG plugin, `entry=0xADDR[,exit=1]`) reports, when the entry is
first executed, whether SCTLR.M/C/I are off, a full DCCISW pass followed C-off, ICIALLU, DSB
and ISB ordering holds, IRQ/FIQ are masked and the other cores are parked in WFx. The
mainline-order fixture `tools/handover_payload.S` passes; nine variants that each drop one
step fail only that item, although all of them still reach the entry in TCG. Outer L2, DMA
and GIC are not checked. See
[E0638](../docs/evidence/0638-qemu-handover-guard-cache-mmu-teardown.md).

## Opt-in hardware watchdog (2026-11-19 / E0636)

`-M sony-a6400,watchdog=on` (`WATCHDOG=1`) maps a one-channel CXD4115 watchdog at
`0xF2402000`, matching the stock kernel's `cxd900x0_wdt_setup/start/stop` and
`watchdog_touch` writes (CMP `1001 * wdt.timeout` = `0x7A314`). Expiry is logged and follows
`-action watchdog=` (default reset). Default machine unchanged. qtest 14/14; paired 150 s
replays with `-action watchdog=none`: 0 expiries, same boot stage. Reset scope and clock rate
are inferred, not hardware-checked. See
[E0636](../docs/evidence/0636-qemu-cxd4115-hardware-watchdog-opt-in.md).

## CamCore block at 0xF3010000 (2026-11-19 / E0637)

`cxd90045-camcore` stores writes in the 64 KiB block and answers the 28
reads observed in the contributor's baseline log: 16 read-modify-write
words and `0x5020` return the guest's last write, and 11 never-written
status words stay 0. Unlisted reads return 0 and are logged. The posted
boot stays at step 26 while unmodelled addresses fall from 2,322 to 1,310.
The model is on by default; opt-in live view still overlays it at priority
-400. Not re-run here and not checked on a physical camera. See
[E0637](../docs/evidence/0637-camcore-f301-contributor-boot-score.md).

## CamCore 0xF3020800 window (2026-11-21 / L0090)

The certified baseline 120 s boot logs 90 distinct catch-all pairs at
`0xF3020800..0xF3020BFF`, the 1 KiB after the 0x800-byte AIF model at
`0xF3020000`: 89 write-only offsets and one read-modify-write word `+0xB80`.
`cxd90045-camcore` now mounts a 0x400-byte sub-window there (priority -600) with
one block-relative rule per offset: Z0 for the write-only offsets, RF for
`+0xB80` (all 74 reads are followed by a write; readers `0x63784AB4/0x63784A74`).
An offset without a rule inside the window still logs as unmodelled. The
post-change boot has 0 catch-all pairs in the window and the same final
sequencer state (`index 42`, `step 0x897F`, `status/mirror 0x0102`,
`top/b5_top/b5_opd 1`, 43 steps); qtest 7/7 (a ruled write is silent, the
unruled `+0xBF0` still logs).

## Contributor boot scores for merged emulator patches (2026-11-19 / E0614–E0618)

Pull requests #35, #37 and #40 map plain `cxd90014-regfile` read-back over
`0xF2800000` (32 KiB), `0xF3500000` (64 KiB) and `0xF3880000` (64 KiB).
#38 raises `TB_JMP_CACHE_BITS` from 12 to 14 in the v11 patch. #39 checks
SD/eMMC insertion once per block in the v11 data path. The posted scores
keep step 26 (`0x0323`/`0x0320`) and the same `[VPH]` prefix. They were not
re-run in this workspace and were not checked on a physical camera. See
[E0614](../docs/evidence/0614-f280-clock-readback-contributor-boot-score.md)
through
[E0618](../docs/evidence/0618-f388-readback-contributor-boot-score.md).

## Native USB release waits for ModelCamera (2026-11-19 / E0619)

Original still-recording release paths execute but their group-1 readiness
check receives false from `ModelCamera` (`0x0B`), with successful transport.
`ModelStillRec` (`0x0F`) is ready. Paired none/bars runs match the native load
of model byte `+0x64`; a separate trace confirms native writes `1,0,1,0`.
The final clear returns to `libObj+0x4AA0A4`. Its original state dependency
is next; no hardware response or ready-state bypass was added. See
[E0619](../docs/evidence/0619-native-usb-release-model-camera-readiness.md).

## Stock USB input is inhibited by original startup policy (2026-11-19 / E0613)

Captured-code traces pass physical USB messages through I-InputRecv and
I-InputMain, then observe native suppression reason `0x11` for the live
`0x15040010` subscription. Startup code itself writes the physical-key inhibit
byte `0 -> 1`; its native clear helper is not reached in paired bounded runs.
The next boundary is original initialization/release. The P display and
opt-in luma do not establish that initialization completed. No hardware model
or firmware input changed; 56 receipt checks pass. See
[E0613](../docs/evidence/0613-original-usb-key-inhibit-owner-and-release-boundary.md).

## Original UDC reaches configured through host-driven EP0 (2026-11-18 / E0598)

Opt-in `-global cxd90045-dwc3.strict-ep0=on` retires EP0 TRBs only on explicit
host requests, supports status ZLPs and NRDY/NAK retries without queuing a
failed token, and refuses unsupported length/chain/DMA inputs. Two final
original-firmware runs complete DEVICE, address 1, CONFIGURATION header/body,
SET_CONFIGURATION(1) and GET_CONFIGURATION=1; UDC is configured. The original
function is bus-power/HID, not UVC. All 33 native EP0 checks and 44 profile
checks pass; the legacy DEVICE replay is unchanged. Packetization/timing,
error recovery, non-EP0 endpoints and video are unqualified. See
[E0598](../docs/evidence/0598-qemu-token-driven-ep0-configuration.md).

## First original USB DEVICE descriptor (2026-11-18 / E0597)

Passive original-code tracing roots E0596's failure in the missing bit-0
read at 0xF0200024. Two single-word physical MSC reads return 1. Optional
`-global cxd90045-dwc3.captured-start-gate=on` supplies that read-only
connected-state snapshot, independently of captured-hwparams; defaults stay
unchanged. Original E305 and create now return 0, service reaches state 3,
created=1, and the driver arms its event ring and RUN. GET_DESCRIPTOR returns
the original 18-byte bus-power DEVICE descriptor (054C:096F).
Native qtest: 44/44; bounded original guard cases: 8/8.
This is not full enumeration or UVC; control status/address/configuration,
snapshot reset/disconnect semantics and data transfers remain unqualified.
See [E0597](../docs/evidence/0597-qemu-original-gadget-start-and-device-descriptor.md).

## Original USB service command path (2026-11-18 / E0596)

An offline diagnostic mod with an explicit captured-library profile sends
original configure/start messages through firmware constructors, allocator
and MsgSender. Configure/start sends return 0, UsbControl reaches FSM 7 with
masks 4/4, and the kernel logs transfer to Gadget. No descriptor is closed
or duplicated and no ioctl/FSM/event reply is fabricated. The sampled
bus-power registry entry still has created=0; DWC3 ring/RUN and IN replies
remain absent. Data-function activation and UVC are unqualified. The
packaged profile correctly refuses the captured libraries, including a
different libc allocator layout. See
[E0596](../docs/evidence/0596-qemu-original-usb-service-startup.md).

## Physical DWC3 hardware-parameter profile (2026-11-18 / E0595)

Two read-only MSC camera samples agree on GSNPSID and GHWPARAMS0–8.
All nine hardware-parameter words differ from the historical QEMU model.
Opt in with `-global cxd90045-dwc3.captured-hwparams=on`, or
`qemu_usb_host_probe --captured-hwparams`. Native qtest passes 20 reads
across the unchanged default and physical profile. This qualifies register
identity only; advertised capabilities and USB transfers remain unqualified.
See [E0595](../docs/evidence/0595-dwc3-physical-hardware-parameter-profile.md).

## OTG separate-open diagnostic boundary (2026-11-18 / E0594)

The opt-in `qemu_usb_host_probe --otg-mode-probe` stages an offline ARM
helper on a disposable overlay. Its O_RDWR open of `/dev/usb/otgcore`
returns EBUSY before SET_MODE(2); it cannot independently start the gadget
in the running stock guest. No event ring, RUN or USB descriptor reply is
observed. Continue through the original USB service's ownership contracts.
See [E0594](../docs/evidence/0594-qemu-otg-exclusive-open-boundary.md).

## Virtual USB host on the DWC3 model: no gadget start (2026-11-18 / E0593)

- `python3 -m tools.research.qemu_usb_host_probe` boots with `dwc3-gadget`, `usb-vbus-test` and
  `usb-cid-high`, then binds a host socket with `-global cxd90045-dwc3.chardev=...`. It sends VBUS,
  `CONN` and GET_DESCRIPTOR, and reads the model registers over QMP.
- The original driver only performs the soft reset (DCTL `0x40000000`, GCTL `0x800` then `0`). The
  event ring and DCTL.RUN stay 0, `dwc3.0` stays `not attached`, IRQ 229 stays 0, and no `IN` reply
  arrives. `dwc3_trace.c` now builds for plugin API 7.
- Next boundary: start a gadget through original code (stock `StartGadget`, E0589, or the
  `libInfraUsbCmn` createGadget ioctls, E0534), then repeat the probe. See
  [E0593](../docs/evidence/0593-qemu-dwc3-virtual-host-and-gadget-start-gap.md).

## Mods-tab command items for a "USB Streaming" entry (2026-11-18 / E0592)

- New `SMOD_V2_FLAG_ACTION` (`pack_mod_v2 --action`) makes a Mods row a command item
  with no ON/OFF value. Pressing it switches the mod ON and queues the MENU key, so the menu closes
  to the shooting screen. A second press switches it OFF.
- With the launcher QEMU build and captured inputs: `mod_on 041da000`, `action_close 041da000`,
  then the menu closed. The row read `Active` on reopening, and a second press logged `mod_off`.
  Artifacts: `artifacts/qemu-a6400/e0592-action-item/`. No UVC function, gadget, or
  notice is involved. The USB boundary is still E0589. See
  [E0592](../docs/evidence/0592-mod-loader-command-items-usb-streaming-entry.md).

## `DefRootView::ActionOpenUSB` gates USB server `dlopen` and `StartGadget` (`0x0A2000`) (2026-11-18 / E0589)

- Static analysis proves `libObj.so+0x83A9C4` (`CEC`) ignores `0x0A400A`/`0x0A400B`,
  `libBusPower.so+0x59F8` sends `0x083001` rather than `0x0A2000`, and
  `viewUnified2.so` (`DefRootView::ActionOpenUSB` `+0x2ADFB8` reading
  `BackupManager::Bkup_Read(0x0107001F)`) selects `view/USBAUTO`,
  `view/USBMASSSTORAGE`, `view/USBMTP`, or `view/USBREMOTEPC`.
- QEMU `im.elf` `/proc/$p/maps` confirms `libInfraMscServer.so`,
  `libInfraMtpServer.so`, and `libInfraRemote.so` are not loaded at boot.
  `DefRootView::CheckerUSBAndClockReady` (`+0x2AC00A`, `+0x2AC044`) and
  `CheckerUSBAndClockReady4FirstView` (`+0x2B271A`) run once at `FirstView`
  before `usb-vbus=true`, and `CheckerUSBConnect` (`+0x2ACBB0`),
  `ActionOpenUSB` (`+0x2ADFB8`), and `UsbIfWrapper::StartGadget` (`+0x12C78`)
  stay at 0 hits on QMP attach.
- Artifacts: `artifacts/qemu-a6400/e0589-usb-mode-gate/` (`vbus-trace.so`,
  `vbus-console.log`, `vbus-report.json`, `correlate.py`, `correlation.json`).
  Next boundary: unblock `DefRootView` past `FirstView`/Live-View step 32
  (Phase 4) or drive `0x0A2000` / `cxd90045-dwc3` gadget model (Phase 2). See
  [E0589](../docs/evidence/0589-qemu-usb-mode-gate-and-defrootview.md).

## `libBusPower.so` FSM transitions on unlocked VBUS while `UsbControl` stays in STOP (2026-11-18 / E0588)

- Resolving `lr` and `[fsm+0x10]` at `UFW::Fsm::DispatchEvent` (`+0x0AFD2`)
  proves `0x0A400A` / `0x0A400B` drives `libBusPower.so+0x3B03` (`0x08E00D2C`)
  across `state 0 → 2 → 0` (`handleEvent` returning `0`) on QMP attach and
  detach.
- Meanwhile `libObj.so+0x839C1B` (`0x08E016C8`, `state=0`) ignores `r1=2`
  (`handleEvent` returning `0xFFFFFFFF`), `UsbControl` (`0x08E005E8`) remains
  in `actionStopEntry` (`+0x11A18`), and no `0x0A2000` (`StartGadget`) command
  arrives at `UsbControl::DispatchEvent` (`+0x10678`).
- Artifacts: `artifacts/qemu-a6400/e0588-usb-fsm-state/` (`vbus-trace.so`,
  `vbus-console.log`, `vbus-report.json`, `correlate.py`, `correlation.json`).
  Next boundary: `libObj.so+0x839C1B` and the USB connection mode gate
  (`E0589`). See
  [E0588](../docs/evidence/0588-qemu-usb-buspower-and-obj-fsm-dispatch.md).

## Opt-in `usb-cid-high` unlocks VBUS broadcast and FSM dispatch (2026-11-18 / E0587)

- Added opt-in `usb-cid-high` (`USB_CID_HIGH=1`, default `false`) to
  `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` and `qemu-a6400/run.sh`,
  setting bit 1 (`0x2`) at `0xF2920010` while keeping the `E0580`
  `usb-vbus-test` baseline intact.
- Both boot `0x0A6000` calls passed `param_1002 = 0`, keeping
  `[UsbControl+0xCC] == 0`. Both QMP attach (`0x00000003`) and QMP detach
  (`0x00000002`) passed `SetJackStatus(1, ...) == 1`, updated `[+0xD0]`
  (`0 → 1 → 0`), posted `sendJackStatusToInput(1, ...)` on `0x007A0020`,
  broadcast `0x0A400A` / `0x0A400B` (`r0=0`), and reached
  `UFW::Fsm::DispatchEvent` (`0x0B900A24`, `r1=1` / `r1=0`).
- Artifacts: `artifacts/qemu-a6400/e0587-usb-vbus-unlock/` (`vbus-trace.so`,
  `vbus-console.log`, `vbus-report.json`, `correlate.py`, `correlation.json`).
  Next boundary: why `0x0B900A24` did not enter `actionGVbusON` (`+0x11F46`) /
  `actionGStartGadget` (`+0x120B0`) (`E0588`). See
  [E0587](../docs/evidence/0587-qemu-usb-cid-high-unlocks-vbus-broadcast.md).

## FUSB bit 1 (CID) and OTG kevent subtype 0 root cause (2026-11-18 / E0586)

- Kernel disassembly (`nflasha3-Image` `0x602ADB74..0x602ADB9C`,
  `0x602AD2CC..0x602AD33C`) proves `0xF2920010` encodes VBUS in bit 0
  (`& 1`) and active-low OTG CID/ID in bit 1 (`ubfx r1, r1, #1, #1`).
  Because `SonyUsbVbus` returns `0` at startup (bit 1 clear), `fusb_notify_cid`
  sees `0` (Host/CID asserted).
- In `libInfraUsbCmn.so`, `CUsbSvc::proc_kevent_otg` (`+0xB0DC`) routes
  `subtype 0` (CID) through `proc_notify(0)` / `proc_svc_event(0)` to
  `UsbIfWrapper+0x12E54` (`r0=9`), emitting boot payloads `byte0 = 2, 1, 1`
  (`param_1002 = 0, 1, 1`) and latching `[UsbControl+0xCC] = 1`.
- Artifacts: `artifacts/qemu-a6400/e0586-usb-otg-kevent/` (`vbus-trace.so`,
  `vbus-console.log`, `vbus-report.json`, `correlate.py`, `correlation.json`).
  Next boundary: opt-in bit-1 CID high (`0x2` at `0xF2920010`) to unlock
  `SetJackStatus(1, 1)` and `0x0A400A` FSM broadcast (`E0587`). See
  [E0586](../docs/evidence/0586-qemu-usb-cid-bit1-and-otg-kevent.md).

## USB Control jack-2 (event 9 / 0x0A6000) suppression gate (2026-11-18 / E0585)

- Direct guest memory reads (`+0xC0..+0xDF` at `+0x110EE`, `+0x110F4`,
  `+0x12980`, `+0x1103E`, `+0x11042`) confirm all three boot `0x0A6000`
  messages come from `UsbIfWrapper` callback `+0x12E54` (`r0=9`,
  `proc_svc_event` case `1` / `CUsbSvc::proc_kevent_otg` case `1`), driving
  `SetJackStatus(this, 2, 0)` then `SetJackStatus(this, 2, 1)` twice and
  latching `[UsbControl+0xCC] = 1`.
- Both post-boot QMP VBUS attach and detach read `[0x09200AB8 + 0xCC] == 1`
  and `[+0xD0] == 0` at `SetJackStatus(this, 1, 1_or_0)` (`+0x12980`), return
  `0` (`+0x129CE` / `+0x11042`), and exit before downstream USB broadcast.
- Artifacts: `artifacts/qemu-a6400/e0585-usb-jack2/` (`vbus-trace.so`,
  `vbus-console.log`, `vbus-report.json`, `correlate.py`, `correlation.json`).
  Next boundary: `usb_portMonitor` / `/dev/usb/event` OTG kevent type-1
  (`ID`/`CID`) producer (`E0586`). See
  [E0585](../docs/evidence/0585-qemu-usb-jack2-suppression.md).

## USB Control PostMsg handoff and SetJackStatus gate (2026-11-18 / E0584)

- `UFW::MsgReceiver::PostMsg` (`+0xA936`/`+0xA96A`) allocates and
  copy-constructs a fresh `MWF::ObjMsg` (`0x0007DB18` → `0x0007EEF8` attach;
  `0x00269060` → `0x00269050` detach), solving the `E0583` `RcvMsg` pointer
  difference and delivering both transitions through `UsbControl::Main`
  (`+0x112E2`) into `UsbControl::ProcessMsg` (`+0x10F84`).
- Unlike early-boot `0x0A4000` jack-1 detach messages processed before three
  boot `0x0A6000` messages, both post-boot QMP attach and detach exit early
  after `SetJackStatus(this, 1, 1_or_0)` (`+0x1103E` -> `beq.w 0x1128c`).
  Disassembly of `SetJackStatus` (`+0x12980`) and case `0x0A6000`
  (`+0x110CC..+0x110F0`) shows `jack_id == 1` returns `0` whenever `[this + 0xCC]`
  (jack 2, written by `0x0A6000`) equals `1`.
- Artifacts: `artifacts/qemu-a6400/e0584-usb-postmsg/` (`vbus-trace.so`,
  `vbus-console.log`, `vbus-report.json`, `correlate.py`, `correlation.json`,
  `postmsg-rcvmsg.txt`, `processmsg-main.txt`, `jack-status.txt`). See
  [E0584](../docs/evidence/0584-qemu-usb-postmsg-and-processmsg.md).

## USB Control local synchronous send (2026-11-18 / E0583)

Both original VBUS messages allocate and send successfully on the local
`0x00CA8192` queue. Both wrappers reach the original
`UsbControl::funcOsalSyncCb`. The next observable handoff is
`preProcess` to `MsgReceiver::PostMsg`. See
[E0583](../docs/evidence/0583-qemu-usb-local-sync-send.md).

## USB Control port-monitor queue (2026-11-18 / E0582)

Original port-monitor dispatch sends attach and detach through
`0x00E942CD`. The stock `libUsbControl.so` callback receives both
12-byte messages and enters `SendEvt` for each. The synchronous-send
result and receiving state decision remain to be traced. See
[E0582](../docs/evidence/0582-qemu-usb-port-monitor-queue.md).

## USB userspace event delivery (2026-11-18 / E0581)

Virtual attach and detach now have an observed path through port 0,
`usb_portMonitor`, `/dev/usb/event`, and the original
`CUsbSvc::proc_kevent_otg`. Both take the ordinary notification path
without a new OTG ioctl or gadget start in the observation window.
See [E0581](../docs/evidence/0581-qemu-vbus-port-and-userspace-delivery.md).

## Virtual USB cable (2026-11-18 / E0580)

`USB_VBUS_TEST=1` lets QMP control cable presence. The original FUSB
handler took IRQ 230 on connect and disconnect and read `1` then `0`
at `0xF2920010`. The duplicate connect generated no additional visit.
Both transitions also reached `event_vbus_exec`. Trace that handler's
port decision and callbacks through gadget start before implementing
EP0 transactions. See [E0580](../docs/evidence/0580-qemu-usb-vbus-interrupt.md).

## USB and sensor observation (2026-11-18 / E0579)

The original DWC3 driver binds at `0xF0200000` and exposes the Sony USB
device nodes. The passive USB observer captured 15 original MMIO accesses
during startup, including reset polls answered by unimplemented MMIO.
IRQ 229–231 remained idle in the baseline shell snapshot. The virtual
cable status word, interrupt behavior, and gadget transfers remain open.

Opt-in `P2S_TRACE=1` records transfer staging, modeled completion and
reset. Native qtest verified the trace and the default writer behavior.
The captured firmware boot emitted no P2S transfer before its bounded
stop. See [E0579](../docs/evidence/0579-qemu-usb-and-sensor-observation.md).

## Viewfinder console (2026-11-16 / E0544)

This section continues the captured-input boot recorded in E0524. It does not
replace that qualification. The hash-checked input set, CMD1 sector negotiation,
Task 43 flag-10 wait, unasserted IRQ 101/102 observation, and dormant Task 65
remain as stated below.

Later records E0525–E0541, on that same machine:

- E0525 puts a ZV-E10 `ulogio.bin` stand-in into the per-run overlay. Appframework
  creates its objects and opens view 2. Without a sub-micro model the power lever
  reads OFF (factor 7).
- E0526–E0527 add SIO, GPIO, ADC, and a replay of one read-only 4 KiB sub-micro
  page. With the timing-event block, the factor-7 power-off stops.
- E0528–E0529: `ObjMic` was waiting on ADF `SetPath(0xA0)`. The ported VPH and
  SYSV frame clock let boot reach `modelCamera ACTIVE`, `EEStart`, and `PIN_OPEN`.
- E0530–E0536: Sugilite P3D/PPF draws the shooting-screen OSD. Console 0 is the
  viewfinder readout, so the a5000 640→853 rear-LCD stretch stays off. The centre
  stays black because VPH `0x144` bit0 stays clear. Leo PLL and MIPI D-PHY init
  complete. The imager never writes SA GO and never kicks P2S.
- E0537–E0541: the eye sensor is not the a5000 hinge pin. The finder predicates
  and the two backup bytes do not switch the image set. Forcing `isOutputEvf` to
  return 0 changes 765 focus-mark pixels and leaves the viewfinder skin in place.
- E0542: `viewUnified2.so` registers `LayoutST_NORMAL_*` beside each
  `LayoutST_NORMAL_EVF_*`. `isOutputEvf` at `0x60f5c4` only changes a coordinate.
- E0543: imager `PowerOn` (`0x6379093C`) is only a function-table slot. The `VD_Pre`
  function at `0x6391E614` returns before CamCore start unless virtual slot
  `+0x28` is non-zero and bit 1 of the flag byte is set. `en144` stays
  `0x3000232`.
- E0544: the mode sequencer index is 12 of 43, on wait `0x8347`. Module 1 is the
  only masked mismatch (`0x0105` requested, `0x0100` reported). The imager word
  `0x0A10` matches on both sides.
- E0549: index 0 is module id 1. The frame publisher copies its report from
  `*(0x6434CA54 + 8) + 0x690`. At `SetContentTarget` that halfword is `0x0100`,
  while indexes 1 and 2 already show `0x0285` and `0x0320`. The selector flag
  at `0x643334E8 + 0x8E` is clear, so the other source is not used.
- E0550: the path selector at `0x643305E4` has already accepted request `0x3003`.
  Its result word at `0x643305D0` is `2`. The mode halfword at
  `0x64351898 + 0x690` is still `0x0100` in that same sample.
- E0551: `0x63988EAA` writes `*(0x645A21E4+0x3EB0)+0x690`. At the stall that
  object is `0x6435CC38` and the halfword is `0x0000`. The copy runs only
  when `*(0x645A21E4+0x623C)+0x1F` is 5; that byte is 0. The sequencer
  index at `0x64330614` is 12. Module 1's halfword is still `0x0100`.
- E0553: a write watchpoint on `0x64351F28` caught `0x63922304` seeding `0x0100`
  and `0x639267BC` storing the value returned by `0x63D3BD56`. That function
  returns the old halfword unless the request is `0x0112`. At the stall the
  request at `0x64350F04` is `0x0105`, and the byte at `0x64354174` that lets
  the store run is 0. The index stays 12. No device model changed.

`tools/a6400_probe.py` waits for a `/proc/snsc_boot_time` checkpoint instead of a
fixed sleep. A `/proc/snsc_boot_time` read can block for more than 30 seconds and
then answer (E0530). That delay is not a hung shell.

- E0557: the third writer of `0x64351F28` is `0x63923A1A` in `VD_Sequence`
  (`0x63923874`). Only the HS root `0x639242D8` posts `VD_Sequence`, and only
  in HS slot 0. The HS root is on timing-event source 30 (ch5). The frame
  start `0x63924A38`, which resets the slot, is on source 29 (ch6). The evsel
  model fires both in one pulse, so the root always reads slot 3 and
  `VD_Sequence` never runs. The byte at `0x64354174` is set by `int_HS` once
  per frame; the 0 in E0553 was a mid-frame sample. With the opt-in
  `EVSEL_LATE_US=500` (evsel `late-src=30 late-us=500`), `VD_Sequence` runs
  every frame and module 1 reports `0x0118`. The sequencer passes step 12 and
  waits at step 32, `WAIT{3,4,5,6,10,12}`, on module 3: `0x032E` requested,
  `0x0326` reported. VPH ch51 (YUV) is programmed: `lb=0x400`, 570 lines,
  two alternating bases. The 500 µs phase is an assumption.

- E0561: module 3 accepts `0x32E` only after a counter passes 6. The counter
  advances only on frames with a new, valid PQD Ydet (luminance statistics)
  record. The producer `0x63BEE670` (`PQD_YdetRegReadManager.cpp`) never
  runs, so the consumer reads a zero record every frame.

- [E0563](../docs/evidence/0563-qemu-ydet-adapter-and-opd-comparison.md):
  `0x63D3DD22` calls the Ydet adapter with a handle and four bytes of input
  metadata. The a6400 shares the a5000 OPD base and bit-1 start instruction,
  with seven firmware IRQ records and different IRQ values. The tested OPD
  start sites and Ydet producer stayed idle at step 32. A passive replay
  counted 509 single-window descriptor lookups and 556 consumer visits.

- [E0564](../docs/evidence/0564-qemu-ydet-window-configuration-reaches-camcore.md):
  the window descriptor is valid and writes `0xF3015848–0xF3015854`.
  The getter pair belongs to the third argument of the window setup;
  its first base is `0xF3000000`, yielding candidate result address
  `0xF3015700`. The result publisher still receives no calls.
  A predicate in the SuperSlow manager checks `0x640D39BC` and returns zero.

- [E0565](../docs/evidence/0565-qemu-superslow-field-and-pqd-came-dispatch.md):
  `0x640D39BC` holds a SuperSlow resource pointer. Its constructor clears
  it, and normal `VD_Pre` continues after the resource check. The normal
  result path is the CamCore job callback at `0x639023AC`, which submits
  `EC::PQD_CamE` and reaches the Ydet producer via `0x63D3DD22`. A passive
  trace saw 206 submissions with this callback forwarded to the EE
  controller at `0x63CBCA06`, and no callback execution.

- [E0566](../docs/evidence/0566-qemu-ee-callback-stays-pending.md):
  EE accepts the submissions and writes `0x639023AD` to its pending slot
  at `+0x84`. The observed boot never ran the promotion function
  `0x63CBC586` or the completion load at `0x63CBBF6E`. The registered
  event owner is `CamPigInterruption23` at base `0xF3000000`. Startup
  programs paths 2 and 3, but the unmodelled enable register at `+0x50`
  reads zero between the two writes.

- [E0567](../docs/evidence/0567-qemu-campig-enable-register-readback.md):
  the opt-in word at `0xF3000050` lets original firmware retain both enable
  bits and write `0x0C000000`. Paired replays still wait at step 32 with
  zero activation and result-publication hits.

- [E0568](../docs/evidence/0568-qemu-campig-dispatch-order-and-input-sync.md):
  firmware starts input sync at `0xF3120210` and registers IRQ 36 event A.
  The IRQ 37 table orders completion before promotion for coalesced status.
  No frame callback ran. The 36 MHz input clock is inferred from the
  programmed geometry and nominal frame period.

- [E0569](../docs/evidence/0569-qemu-sgc1-event-a.md):
  the opt-in input timer delivered 231 events to the original IRQ 35
  handler and callback. The earlier IRQ 36 trial instead invoked the
  `0xF3000000` handler. The callback's front-end write branch remained idle.

**Earlier packaged live-image checkpoint:** with `EVSEL_LATE_US=500`,
`CAMPIG_ENABLE_READBACK=1`, and `SGC1_EVENT_A=1`, the sequencer
waited at step 32 (`0x032E` requested, `0x0326` reported). The IRQ 35
callback's one-shot write, CamPig IRQ 36 completion, and the Ydet producer
all stayed idle.

**First firmware-driven live-view frame:** [E0591](../docs/evidence/0591-qemu-a6400-first-live-view-frame.md)
uses captured firmware and opt-in `LIVEVIEW_SOURCE`. Sgc1 raster end drives
guest-armed CamPig completion; original firmware publishes and consumes fresh
Ydet records. Guest-launched RC0 DMA fills ch51 and the rear LCD shows the
exact test chart outside the OSD. A matched phase-only boot had black video.
The [separate packaged replay](../docs/evidence/0591-qemu-a6400-first-live-view-frame.md#separate-sequencer-replays-2026-11-19)
holds step 32 (`0x032E`/`0x0326`) with the phase-only control. With bars,
the original guard reads 1 and reaches step 42. The reconstructed replay uses
the packaged ORIL and preset rather than the historical disk image. With
the frozen sub-micro reply, captured settings wait at step 26
(`0x0323`/`0x0320`) on a separate original lens-state predicate. With the opt-in
[lens rail reply](../docs/evidence/0591-qemu-a6400-first-live-view-frame.md#captured-step-26-sub-micro-lens-rail-acknowledgement),
the captured replay's original guard accepts `0x0323` and advances to
step 42. The matched frozen-reply control stays at 26 with fresh Ydet.
This models the supply status only, not lens communication or autofocus.

**Rear-monitor milestone:** [E0572](../docs/evidence/0572-qemu-a6400-rear-lcd-layout-on-panel.md)
captures the original `LayoutST_NORMAL_WITH_INFO_LR` on QEMU console 0
after an opt-in overlay selects device 1 in the firmware display controller.
In [E0591](../docs/evidence/0591-qemu-a6400-first-live-view-frame.md),
`REAR_CONTROLS=1` idles the active-low eye-contact input `P0.2` high; stock
firmware selects the rear LCD without that overlay.

[E0573](../docs/evidence/0573-qemu-a6400-rear-panel-controls.md) adds an
interactive panel around the VPH output. The opt-in keys drive ADC, GPIO,
sub-micro and TPU paths. The original firmware opens MENU and responds to
directional and center input, including a normal center press to dismiss
the NTSC notice in the live-view replay.

The [E0573 follow-up](../docs/evidence/0573-qemu-a6400-rear-panel-controls.md#2026-11-18-follow-up)
removes the panel's virtual-time hold queue from wheel turns and selector
toggles. The [stock wheel follow-up](../docs/evidence/0573-qemu-a6400-rear-panel-controls.md#follow-up-stock-wheel-detents-2026-11-19)
verifies opposite selection moves with four native TPU phase counts per
detent. AF/MF--AEL switch and button input reach the driver; an application
action in both positions remains unverified.

[E0574](../docs/evidence/0574-qemu-a6400-blank-panel-recovery.md) confirmed
the panel was receiving real black frames in a long-running instance.
A fresh instance recovered the original menu. In a separate replay,
boot-time blanks had zero ch53 base and both OSD buffers were empty.

[E0651](../docs/evidence/0651-qemu-a6400-stock-finder-monitor-rear-lcd.md)
identifies GPIO `P0.2` (`Backup` `0x00F90427/28`, polled by `av-cam`
`0x6361ac28` -> `KEY_OTHER_EYE_CONTACT = 0x708B`) as the active-low EVF eye
sensor. With `P0.2` idled high by `REAR_CONTROLS=1`, unmodified firmware
(`REAR_LCD_DIAGNOSTIC=0`) returns `1` from `0x40a374`, passes device `1` to
`0x40aa5c`, sets manager device `0` at `0x40fde4`, and renders the rear-LCD
layouts (`LayoutST_NORMAL_WITH_INFO_LR`, `LayoutST_NORMAL_INFO_OFF_LR`,
`LayoutST_NORMAL_LEVEL_LR`) via stock `FINDER/MONITOR` (`Auto` and
`Monitor(Manual)`). `rear_panel.py` now defaults to stock selection.

[E0652](../docs/evidence/0652-qemu-a6400-late-black-screen-scanout-capture.md)
captures VPH/SYSV registers and both OSD buffers across the late black-screen
failure: when `Power Save Start Time` expires with `Bluetooth Rmt Ctrl` `Off`,
the guest logs `EV OFF add_off_factor: 0x8`, runs `EV SET_PANEL_OUT`, clears
`0xf3440144` bits 0/1 to `0x3000230`, zero-fills active `ch53` buffer A
(`0x23a80000`) while leaving backbuffer B (`0x23b16000`) stale, deactivates
`modelCamera`, and blocks in `Mt F00_010_libObj.so`. QEMU's VPH/SYSV scanout
continues running; the guest stopped drawing.

The [S1 follow-up](../docs/evidence/0652-qemu-a6400-late-black-screen-scanout-capture.md#follow-up-body-shutter-input-and-save-boundary)
returns the stock shooting OSD after Auto Power Save with half-press on
`P0.1`. MENU did not wake the display, and the OSD stayed visible after S1
release. Captured step 26 was unchanged. The stock menu setting changed
in the UI, and the [explicit native commit](../docs/evidence/0652-qemu-a6400-late-black-screen-scanout-capture.md#follow-up-settings-survive-an-explicit-native-commit-2026-11-19)
wrote `Backup.bin`; a fresh process restored the identical `Off` menu
from those persisted bytes. Automatic power-off commit and
application-level AF/MF--AEL behavior remain unverified.

[E0575](../docs/evidence/0575-qemu-mod-loader-v2-compatibility-boundary.md)
qualifies the Mod Loader v2 target mismatch. QEMU runs `im.elf` as PID 144
in the observed boot, with Sony library bases different from the current
physical guard. Its unchanged ARM check returns `-5 identity/maps` in
the guest. See [the mod development workflow](MOD_DEVELOPMENT.md).

## Virtual removable media (2026-11-18 / E0650)

[E0650](../docs/evidence/0650-qemu-virtual-media-gates.md) adds an opt-in
MMC0 slot with QMP insertion/ejection. The native SD test verifies active-low
`MODEREAD` bit 24 and sector persistence after process restart without changing
internal eMMC. With live sub-micro slot A detect and the correct absent
Memory Stick bit, the stock driver now registers `mmca10p1` and the file
service mounts `/dev/mmca`. Initial replays read sectors covering the
seeded JPEG but queued the second `ModelBrowser` initialization behind
recorder teardown. The initial mounted shutter trial issued no SD writes.
Separately, a checked opt-in `IDT_CAM` preload supplies the original
auxiliary image at PA `0x20152000`. It resolves the captured final
step-42 mismatch (`0x0102` wanted, `0x0118` reported): original
`IDT_TOP` initializes, `IDT_OPD` is armed, module 1 reports `0x0102`,
and the native sequencer clears the stage. The internal `0x0302`
notification is sent and dequeued. The opt-in `DDRB_CONTROL=1` model
services the subsequent native UPM idle-to-active transition: DMM
returns the allocation-rule reply, the native EE acknowledgement opens
the camera's deferred queue, and the second stock `ModelBrowser`
initialization completes in a mounted run. The firmware prepares an
image database on the disposable SD. Separately, `DFS_CONTROL=1`
completes the observed clock switch at `0xF2916000`, allowing native
UPM and DMM rule 0 to return. The stock Folder View now finds the seed
and reaches the still decoder filter. `ZMA1_STARTUP=1` recognizes the
original boot program and supplies its startup interrupt. With
`ZMA1_JPEG_FILL=1`, the separate native DGI fill callback completes and
its wait returns success. The firmware still does not display the
seeded JPEG; the stock LCD says **Unable to display**. Recorder teardown
can retain a pin `0xB` reference. Opt-in `SHUTTER_EDGE_IRQ=1` latches the
two body GPIO pulses in the virtual GIC. With that policy, two mounted JPEG
self-timer runs reach the original `STILL_CAPTURE_START_CMD` before release;
no capture completion, encoding or new photo is verified.
The virtual OHCI Mass Storage
test proves enumeration and basic reads for a non-Sony fixture; the Sony
DWC3 gadget remains a separate prerequisite for USB-dependent mods.
See [scenario instructions](README.md#offline-sd-and-usb-scenarios).

The DFS build passes 50 focused checks. QEMU `ba71c8f8e5eb...` passes
all six native live-view/DDR/DFS/SA tests; the full WSL suite reports
2,187 tests with 78 skips and no failures. After the body shutter edge
policy, `1858a1bb7de7...` passes 52 focused checks and full discovery runs
2,188 tests (78 skipped, no failures).
An earlier WSL run had one Unicorn virtual-TLB assertion failure; the
isolated test and final suite passed without a change to that test.
The captured eMMC, ORIL, and original virtual SD fixture re-hash unchanged.

## Captured-input qualification (2026-11-16 / E0524)

The reproducible input builder now uses all 18 captured images: two 4 MiB
hardware boot areas and the 16 nonempty SDM user partitions. It retains the
captured kernel, initrd, av-cam, libraries and settings together. Two independent
preparations produce identical output hashes; the original captures remain
unchanged. The native eMMC user area begins at backing offset 8 MiB.

The 2 GiB model also needed a real protocol correction: honor the original
`CMD1 0x40000080` sector-mode request. Before the fix, rootfs sector `0x52002`
was read as a byte offset; afterward it reads `0x0A400400`. Both sector-addressed
and byte-addressed constructed-disk regressions pass, without inflating capacity.

Original firmware reaches four CPUs, mounts its captured filesystems, runs LIRO
`entry_pre` / `entry_main`, initializes DMP/stella and answers shell commands.
This is not a complete camera boot or a verified GUI frame.

Stopped, hash-qualified task/IRQ observations refine the older next-step list:

- Task 65 is dormant with no queued activation. Its observed entry target has a
  finite initialization body; the helper sleep alone is not a demonstrated
  sub-micro deadlock.
- Task 43 waits on ITRON flag 10, mask `0x3373`, mode 1, timeout -1.
  Original registered IRQ 101/102 handlers contain paths setting bits 1/2.
  Native traces show both IRQs enabled, with zero input assertions during the
  bounded run.
- Task 1 waits on OSAL semaphore `0x00BF04EE` at av-cam `0x6361BB48`.
  Its lifecycle purpose and status as a global blocker remain unproven.

**Next:** recover the actual register/status/timing producer contract for the
enabled IRQ 101/102 paths. Do not inject a periodic interrupt or signal a flag
merely to advance the firmware. These are qualified observations of separate
waits, not proof of one global deadlock.

Commands, identities, failed receipts and limitations:
[E0524](../docs/evidence/0524-reproducible-captured-qemu-boot-and-liro-waits.md).

## Earlier no-peripheral initialization wait (2026-11-17 / E0527)

[E0527](../docs/evidence/0527-qemu-task43-initialization-flag-wait.md) identifies
**Task 43** inside its first display-initialization wait at `0x63A67DBE`:
`twai_flg(10, 3, TWF_ORW, result, -1)`. The flag object is `0x645248DC`,
with pattern zero in two independent stopped snapshots. The actual saved
caller is `0x63A67DC3`; the later task-main-loop mask `0x3373` is not this wait.
IRQs 101/102 are enabled but have no input assertions in either native trace.
Their original handlers conditionally set bits 1/2.

Task 65 is dormant with no queued activation, not a demonstrated sub-micro
deadlock. Task 1 separately waits on semaphore `0x00BF04EE`, after checkpoint
line `0x143`, step 2. Neither observation proves a sole global blocker.

These addresses are qualified for captured av-cam `dd8f8d0c...` and captured
LIRO, not the packaged image used in the historical snapshot below.
The new offline decoder reconstructs actual saved arguments and checks the
linked object; no firmware, completion flag or device model was changed.

At this snapshot the next boundary was the display/VPH register and timing
producer for IRQs 101/102, including the original handler guards. These runs
did not include the later peripheral models; their lack of application
startup does not supersede the viewfinder/rear-panel results above.

## Historical E0521/E0522 snapshot

Snapshot: 2026-11-15, after E0521 and E0522. No physical-camera run is described here.
Labels: [C] confirmed by a run receipt or disassembly, [I] inference, [A] assumption.

The 2026-11-17 [toolchain qualification](../docs/TOOLCHAIN.md) reproduces the
four-core captured-firmware boot on QEMU 11.1.3. The historical findings and
hardware limits below are unchanged.

## Where the boot stands

`./run.sh` (WSL2) boots the main `nflasha3` firmware through these stages:

1. Kernel at `0x00108000` with the kemco block at `0x00103000`; 4 Cortex-A5 cores online [C].
2. `init` mounts every eMMC partition through a fresh qcow2 overlay (`/`, `/usr`, `/setting`, `/system`, `/log`, …) [C].
3. 40 kernel modules load, including `backup`, `dmm`, `upm`, `mcmn_drv`, `liro`, `dma330` and `hdmi` [C].
4. av-cam (LIRO µITRON) runs `entry_pre` / `entry_main`, and its tasks bind to CPUs 0–3 [C].
5. `stella`, `DMP_2D_Initialize : 0`, the `im.elf` monitor buffer, and a BusyBox `ash` on `ttyAM0` [C].

A shell prompt is not a working camera application. No `liro-*` interrupt has fired yet [C].

## Findings

The bootloader hand-off is not executed; it is reproduced. Each item below was recovered from the
a6400 binaries, then cross-checked against `Oein/a5000-emulator` `docs/BOOT.md`.

| # | Finding | Evidence | How it is modelled |
| --- | --- | --- | --- |
| 1 | The kernel runs at TEXT_OFFSET `0x00108000` and reads `"kemco"+cmdline` from `r2 = 0x00103000`, not ATAGS/DTB | `boot/loadaddr.txt`; `cmdline override at 60103005` [C] | `sony_a6400_load_boot_contract()` |
| 2 | `backup.ko Backup_load=0` expects `Backup.bin` to be preloaded at the physical address in its header word 0 (`0x02E80000`). Without it, `-EIO` and ~20 dependent modules fail | `backupfile_init` @`0x4614`, `Data_check` @`0x3e04` [C] | `-device loader` in `run.sh` |
| 3 | The main boot needs `/setting/kemco.txt`. `data/nflasha3-kemco.bin` is the updater cmdline, whose `memrsv` leaves av-cam's RAM unreserved | E0521 §0.3 [C] | `run.sh` reads it from `setting.tar` (console token swapped) |
| 4 | `liro.ko adrs=035c6000` expects `av-cam.bin` preloaded there (`LIRO` magic at +4) | `Magic unmatched` before the fix, `end entry_main` after [C] | `-device loader` in `run.sh` |
| 5 | Secondary cores: `cxd900x0_smp_init` writes the physical `cxd900x0_secondary_entry` to SRAM `0xFE005010 + 4*(cpu-1)` and issues `SEV`, then the standard pen-release handshake follows | `0x6011ed40..0x6011ee98`, `boot_secondary` @`0x603c08ac` [C] | Secondary WFE stub at `0x1200` |
| 6 | The kernel never writes `GICD_ITARGETSR`. Its `irq_select_affinity` only reads the target byte back | `gic_dist_init` @`0x604951c8`, `0x6011feb0` [C] | CPU0 stub at `0x1000` presets the targets |
| 7 | Per-CPU timers: clockevents ch0/2/3/4 → IRQ 153/155/156/157; hwtimer0..3 = ch5..8 → IRQ 158..161 | tables `0x604d8d80`, `0x603c9354` [C]; hwtimerN = CPU N [I] | Target bytes; `cxd4115_timer.c` |
| 8 | The timer block follows the CXD4115 protocol (clocksource ch1 `0x1031`, periodic `0x1102`, one-shot `0x112`) | kernel timer code [C] | `cxd4115_timer.c` adapted from a5000 (GPL-2.0) |
| 9 | Without `-icount`, ~15 µs hwtimer deadlines expire before they are armed (3,254 warnings per run) | E0522 §0.6 [C] | `run.sh` default `-icount shift=0,sleep=on` [A] |
| 10 | The inputs come from two firmware versions: the kernel is a 2021 build, while rootfs and `/usr` are 2019 (`38111_03.2019052701`) | E0521 §1 [C] | Not fixed; modules load because vermagic matches |

## Known gaps and noise (not blocking the shell)

- All SPIs other than the timers target CPU0 [A]. No CXD90045 GICD dump exists yet.
- `osal_ulogio.ko` fails: `/setting/ulogio.bin` is absent [C].
- `sata_drv.ko` and `msen_drv.ko` are not present in `/usr/kmod` [C].
- Compressed `dnflasha*` mounts fail `ENXIO` (`AL_EMMC_BLK_UZ … Information table is invalid`). init falls back to the uncompressed partitions [C].
- The eMMC driver prints `execute_tuning … forbiden for non-HS200 mode` on every access (~900 lines per run) [C].
- `/system` (`nflasha3`) on `emmc.img` is an empty FAT. av-cam and the other boot-set files are only supplied as preloads [C].
- The watchdog at `0xF2402000` is unmodelled by default, although the main cmdline sets `wdt.mode=1 wdt.timeout=500` [C]. Opt-in `watchdog=on` / `WATCHDOG=1` models it (E0636).
- `nflasha1: bogus number of reserved sectors`: the updater partition on `emmc.img` is not FAT [C].

## Next possible steps (suggested order)

For each step, check `Oein/a5000-emulator` first for the CXD90014 equivalent.

1. **Find what av-cam waits on.** The captured-profile investigation is now
   qualified by [E0527](../docs/evidence/0527-qemu-task43-initialization-flag-wait.md)
   above; do not transfer its addresses to this packaged input set.
   The original lead was: no `liro-*` IRQ has fired. The a5000 analogue is
   `docs/RE_AVCAM_IRQ_BOOT.md`: the boot checkpoint `prof` words in RAM, plus the "Liro Start"
   task blocked before sub-micro traffic. Locate the a6400 av-cam checkpoint variables and read them
   from the QEMU monitor (`xp`) to find the blocking task and wait object.
2. **Sub-micro link.** On the a5000, this is the SIO 128-byte exchange triggered by a GPIO strobe,
   with frames replayed from a real snapshot (`docs/RE_SUBMICRO_LINK.md`, `a5k_submicro_*`). Recover
   the CXD90045 SIO base/IRQs and the frame format from av-cam.
3. **Bootloader hand-off page.** The a5000 preloads `0x80021000` (boot reason at +0x4C, read by
   `upm.ko`). The a6400 cmdline has `wbimem=0x1000@0x20021000` and `pm.resume=0x20021054`. Check what
   `upm.ko` and av-cam read there [I: likely the same role].
4. **ADC / keys and front-end units.** ADC channels must read as "no key pressed". Front-end and
   sensor-interface IRQs are needed once live view runs (a5000 §1.1 IRQ table).
5. **Watchdog.** Done as opt-in `watchdog=on` (E0636). Remaining: hardware reset scope, PMIC
   keepalive, and the `wdt_intr` GPIO pre-expiry path.
6. **Display.** Sugilite/`dmp_smaph` scan-out to a QEMU console. The a5000 has `dmp_smaph.c`,
   `cxd90014_vph.c` and `a5k_fbview.c`. The OSD format is known from E0298 (640×480 RGBA4444).
7. **Optional real-camera verification (read-only, standing authorization):** one 4 KiB read of
   the GIC distributor page `0xF8001000` would replace the CPU0 target default. It would also confirm
   inference 7.
8. **Housekeeping.** Build `emmc.img` reproducibly from the firmware partition images, with a
   script and hashes. Pick one firmware release for the kernel, rootfs and `/usr`. Silence or rate-limit
   the eMMC tuning warning once its cause is understood. Add `ulogio.bin` if a real copy exists.

## Reproduce

```bash
bash qemu-a6400/setup-qemu.sh
ninja -C "$HOME/sony-a6400-qemu-11.1.3/build" qemu-system-arm qemu-img qemu-io
TIMEOUT=110 bash qemu-a6400/run.sh
```

Records: [E0521](../docs/evidence/0521-qemu-a6400-main-firmware-boots-to-avcam.md),
[E0522](../docs/evidence/0522-qemu-a6400-smp-four-cores-and-cxd4115-timers.md).
Receipts: `artifacts/qemu-a6400/e0521/`, `artifacts/qemu-a6400/e0522/`.
