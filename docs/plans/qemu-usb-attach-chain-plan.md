# Plan: Emulating the Full USB Attach / Mode-Switch Chain in QEMU (`sony-a6400`)

**Date:** 2026-11-19  
**Goal:** Emulate the complete firmware USB attach/detach chain (`usb-vbus` -> FUSB/ORIL -> `InfraInput` -> `InputService` -> `DefRootView::ActionOpenUSB` -> `libInfraMscServer.so` / `libInfraRemote.so` -> `cxd90045-dwc3`) in `sony-a6400` QEMU so that plugging in the virtual USB cable triggers the real firmware USB Mass Storage / PC Remote flow, allowing faithful offline reproduction and resolution of the real-camera UVC + USB-plug-in conflict.

---

## Current Baseline & Exact Stopping Boundary

1. **Already working in QEMU (`E0580`–`E0588`):**
   - QMP `usb-vbus=true` (`USB_VBUS_TEST=1`, `USB_CID_HIGH=1` in `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c`) updates `0xF2920010` (`0x2 -> 0x3`) and pulses `IRQ 230`.
   - Original kernel handlers (`fusb_interrupt_usb_vbus` -> `fusb_notify_vbus` -> `event_vbus_exec` -> `usb_portMonitor` -> `/dev/usb/event`) deliver the event to `im.elf` (`libUsbControl.so`).
   - `UsbControl::SetJackStatus(1, 1)` returns `1`, sends the 16-byte message `0x07000001` (`status=1`) via `osal_snd_msg` on queue `0x007A0020` (`UsbControl::sendJackStatusToInput`), and broadcasts `0x0A400A` (`MSGID_VBUS_ON_EVT`), transitioning `libBusPower.so` FSM `0 -> 2`.
2. **Historical boundary hypothesis (`E0589`; routing and offset assumptions superseded by E0612/E0613 below):**
   - The `0x07000001` message on `0x007A0020` routes through OSAL UIPC toward `av-cam.bin` (ORIL, which shares `0x007A0020` with `input.ko` and contains both `0x007A0020` and `0x07000001`), returning into `libObj.so` (`InfraInput` table at `+0x13E25CC` maps `0x07000001 -> 0x0293` -> `snd_InfraInputEvent_to_InputServant` `+0x3CF6DC` -> `InputService` key `0x15040010`).
   - In `viewUnified2.so` (`DefRootView`), `CheckerUSBConnect` (`+0x2ACBB0`, helper `+0x2ACB28`) and `CheckerUSBAndClockReady4FirstView` (`+0x2B271A`) query `InputService::getInputEventStatus(0x15040010)` (expecting `1` or `2`), while `CheckerUSBAndClockReady` (`+0x2AC00A`, helper `+0x2ABF6C`) checks `model/ROOT` param `0x102` and view state (`[vtable+0x50] in {2, 3}`).
   - Only after those gates pass does `DefRootView::ActionOpenUSB` (`+0x2ADFB8`) check `CmnForUseMultipleViewStatus::getDirectRecStatus()` (`+0x4114E4`), `WrapperSettingUtil::isCtrlWithSmartPhoneOn()`, and `BackupManager::Bkup_Read(0x0107001F)`, open `view/USBAUTO` (`1`), `view/USBMASSSTORAGE` (`2`), `view/USBMTP` (`3`), or `view/USBREMOTEPC` (`4`), demand-load `libInfraMscServer.so` / `libInfraMtpServer.so` / `libInfraRemote.so`, and send `0x0A2000` (`StartGadget`) to `UsbControl`.
   - In `E0589`, `CheckerUSBConnect`, `ActionOpenUSB`, `ActionConnectUsbAtPreStable` (`+0x2A524C`), and `UsbIfWrapper::StartGadget` (`+0x12C78`) remained at **0 hits**.

*(Checked `docs/RELATED_REPOSITORIES.md`; all contracts will be derived from locally verified `ILCE-6400` binaries and captured inputs.)*

---

## Phased Execution Plan

### Execution checkpoint — 2026-11-19 (E0611)

Phase 1 has started; it is not complete. See [E0611](../evidence/0611-qemu-usb-input-chain-identity-boundary.md).
Two offline observations (`LIVEVIEW_SOURCE=none` and `bars`) reached the original
P shooting display after a rear-center key, then observed VBUS attach, duplicate
attach without a new edge, and detach. Captured BK4 key `0x0107001F` is `04`.

**Required correction before interpreting function hit counts:** the prepared
kernel is `d7a9ae09...`, not E0589's listed `12eeec82...`. Captured `libObj.so`
and `viewUnified2.so` have different `.text` hashes from the packaged libraries
used to prepare the historical probes. Captured `snd_InfraInputEvent_to_InputServant`,
`callback_direct`, `createEvent`, and `getInputEventStatus` are respectively
`+0x3CF664`, `+0x3D2C8E`, `+0x3CF8D2`, and `+0x3D5644`.
The initial probe counts remain discovery receipts, not evidence that ORIL
drops an event or that a named view gate blocks it. Rebuild identity-matched
probes and derive the captured view gates before selecting Phase 2 branch A/B/C.

### Execution correction — E0612

[E0612](../evidence/0612-captured-usb-receive-and-ready-gate.md) establishes
that the captured Linux `I-InputRecv` thread directly receives `0x007A0020`.
Both USB states reach it, physical lookup returns index `0x19D`, its suspend
byte is clear, and both states reach `I-InputMain` on `0x007A804C`. The sampled
ROOT `0x102` is already `2`, with ready-checker return `1`. A mandatory ORIL
return hop and an unready ROOT cannot be assumed as the missing contract.
The captured view list contains physical `0x07000001 -> 0x15040010`, but
application USB callback delivery is not established. Next trace the actual
I-InputMain subscription/filter/callback state before selecting Phase 2.

### Phase 1 — Passive Trace of `0x007A0020 -> I-InputRecv -> I-InputMain -> InputService (0x15040010)`
**Objective:** Locate the original message/filter/callback boundary without firmware patches or guessed hardware replies. **Gate identified in E0613:** original startup code inhibits physical USB key `0x07000001`; live subscription matches but I-InputMain suppresses delivery with reason `0x11`. The native release path is not reached in the bounded runs. See [E0613](../evidence/0613-original-usb-key-inhibit-owner-and-release-boundary.md).

Current continuation: follow the original initialization paths calling
`view+0x41AD18`, which clears the inhibition through InputService. The startup
owner queries alias `@M001`, parameter `0x100`, observes `1`, and natively
sets inhibition `0 -> 1`. Do not conflate this with ROOT `0x102=2`, and do not
clear either flag or call the helper externally. Original still-recording
initialization must reach the release path through qualified contracts.

1. **Static inspection (read-only):**
   - Use the captured libObj receive/main and subscription instructions from E0612/E0613. Investigate ORIL only when an original dependency establishes its involvement; shared queue literals alone do not prove a return hop.
   - Inspect `inputs-moddev-1/Backup.bin` for key `0x0107001F` (USB Connection mode: Auto / MSC / MTP / PC Remote) and `isCtrlWithSmartPhoneOn()`.
2. **Passive QEMU TCG trace (new script modeled on `artifacts/qemu-a6400/e0589-usb-mode-gate/`):**
   - Boot with `REAR_CONTROLS=1`, `USB_VBUS_TEST=1`, `USB_CID_HIGH=1` (and a paired run with `LIVEVIEW_SOURCE=bars` per `E0591`), press `center` to dismiss the NTSC notice and obtain the original `P` display, then toggle QMP `usb-vbus=true` and `usb-vbus=false`. The display alone does not prove application initialization completed.
   - Passively trace:
     - Captured `libObj.so` I-InputRecv (`+0x862800`) and I-InputMain (`+0x861ED0`), subscription transform (`+0x861018`) and suppression branch (`+0x8622C8`),
     - `libObj.so`: `snd_InfraInputEvent_to_InputServant` (`+0x3CF664`), `InputService::callback_direct` (`+0x3D2C8E`), `InputService::createEvent` (`+0x3CF8D2`), `InputService::getInputEventStatus` (`+0x3D5644`) for `0x15040010`,
     - `viewUnified2.so`: `CheckerUSBAndClockReady` (`+0x2AC00A`), `CheckerUSBConnect` (`+0x2ACBB0`), `ActionConnectUsbAtPreStable` (`+0x2A524C`), `ActionOpenUSB` (`+0x2ADFB8`), and `model/ROOT` `0x102` return value.
3. **Phase 1 verification:** E0611 preserves discovery/identity failures; E0612 qualifies direct Linux delivery; E0613 qualifies the original inhibit owner and live suppression. E0619 reaches the enclosing release paths, identifies valid ModelCamera not-ready replies and observes native readiness writes; the caller's original state dependency remains open. Keep each run under a new output directory.

---

### Phase 2 — Emulate the Missing Hardware Contract Unblocking `DefRootView::ActionOpenUSB`
**Objective:** Satisfy the original firmware's real hardware/peripheral dependency in `sony_a6400.c` (strictly opt-in, no firmware patches, no fabricated UI events) so `usb-vbus=true` organically triggers `DefRootView::ActionOpenUSB`.

1. **Implement only what Phase 1 proves is missing:**
   - **Branch A (if ORIL filters `0x07000001` on a peripheral/sub-micro/power/RTC register):** Add opt-in emulation of that specific register/WBI/RTC/GPIO response in `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` (verified from ORIL disassembly and, if needed, a single bounded 4 KiB read-only check on the physical camera).
   - **Branch B (current E0619 ModelCamera readiness boundary):** The original still-recording release paths execute but group 1 is blocked by valid `ModelCamera` (`0x0B`) readiness false; `ModelStillRec` (`0x0F`) is ready. Native setter `libObj+0x3FE020` writes `1,0,1,0`; the final clear returns to `+0x4AA0A4`. Trace the original caller's byte `this+0x61E4` and stack `[r7+4]` branch inputs and their owner/state chain before deriving a hardware contract or linking the independent E0591 step-26 wait. See [E0619](../evidence/0619-native-usb-release-model-camera-readiness.md). Only implement an opt-in hardware response supported by original instructions and state/side-effect evidence. Do not preset model readiness, ROOT readiness, alias `@M001`, init-done state, physical-key inhibition or application events.
   - **Branch C (if `Backup.bin` `0x0107001F` or `isCtrlWithSmartPhoneOn()` selects the mode):** Document the captured value in `inputs-moddev-1/Backup.bin` and test both **Mass Storage** and **PC Remote** via the camera's own Setup -> USB Connection menu (no manual `Backup.bin` patching).
2. **Phase 2 verification:**
   - On QMP `usb-vbus=true` (with **no** mods loaded):
     - `DefRootView` passes `CheckerUSBConnect` -> `ActionOpenUSB` (`+0x2ADFB8`),
     - the LCD (`/lcd.png`) displays the original Sony *"USB Mode Mass Storage / Connecting..."* or *"PC Remote"* screen,
     - `libInfraMscServer.so` or `libInfraRemote.so` is demand-loaded in `/proc/<im.elf>/maps`,
     - `0x0A2000` (`StartGadget`) reaches `UsbControl` and enters `UsbIfWrapper::StartGadget` (`+0x12C78`).

---

### Phase 3 — Dynamic `0xF0200024` Start-Gate, VBUS Disconnect, and Bulk OUT in `cxd90045_dwc3.c`
**Objective:** Ensure stock `usbg_storage.ko` (MSC) and `usbg_stillimage.ko` (PC Remote/PTP), once launched by the firmware in Phase 2, complete full DWC3 lifecycle on both attach and detach.

1. **Link `0xF0200024` start-gate to live `usb-vbus` (`sony_a6400.c`, `cxd90045_dwc3.c`):**
   - Preserve `E0597`/`E0598` `captured-start-gate=on` baseline; add opt-in `vbus-linked-start-gate=on` (default `off`) so bit 0 of `0xF0200024` tracks live `usb-vbus` (`0` when unplugged -> `-EBUSY`, `1` when plugged -> gadget start succeeds).
   - Deliver DWC3 `DEVT_DISCONNECT` when `usb-vbus` drops `true -> false` while the gadget is active, and clean `DEVT_USBRESET` + `DEVT_CONNECTDONE` on `false -> true` without stale EP0 state.
2. **Add `strict_bulk_out` (and Interrupt IN if needed) in `cxd90045_dwc3.c`:**
   - Extend `strict-bulk=on` (currently Bulk IN only) with `strict_bulk_out(s, ep_idx, data, len)` for `OUT <ep> <hex>`: resolve physical EP via `strict_resolve_ep`, verify active TRB and RAM bounds, DMA into guest buffer, update TRB status/remaining length, and emit `DEPEVT_XFERCOMPLETE` with `phys_ep` (or `NAK` + `DEPEVT_XFERNOTREADY` when no TRB is queued).
3. **Phase 3 verification:**
   - Existing UVC regression (`tools.research.qemu_uvc_smoke --strict-bulk`) and `tools.research.qemu_dwc3_profile_check` (`44/44`) remain 100% green.
   - New virtual-host test for stock **MSC (`054C:096F`)** and/or **PC Remote (PTP)**:
     - `usb-vbus=true` without any mod -> firmware enters USB screen -> host enumerates `054C:096F` and completes at least one SCSI `INQUIRY` / `TEST UNIT READY` (CBW on Bulk OUT, data + CSW on Bulk IN) or PTP `GetDeviceInfo`.
     - `usb-vbus=false` -> firmware cleanly exits the USB screen back to shooting view (`P` mode).

---

### Phase 4 — Interactive USB Cable Controls in `rear_panel.py` + `rear_panel.html`
**Objective:** Provide one-click manual USB attach/detach controls and live USB mode/enumeration status at `http://127.0.0.1:8080/`.

1. **Backend (`qemu-a6400/tools/rear_panel.py`):**
   - Add opt-in POST `/usb/attach` and `/usb/detach` endpoints (protected by `Origin` + `X-Camera-Panel: 1`) that set `usb-vbus` via QMP and sync `CONN`/`DISC` on `usbhost.sock`.
   - Extend `GET /uvc` (or add `GET /usb`) with `vbus`, current `vid_pid`, USB class (`MSC` / `PTP` / `UVC`), and stream status.
2. **UI (`qemu-a6400/tools/rear_panel.html`):**
   - Add **"Plug USB" / "Unplug USB"** toggle and compact status badge (`USB: Unplugged` / `MSC 054c:096f` / `UVC 054c:0322 — N frames`) in English.
3. *(Optional iteration speedup)*: Add an opt-in fast staging path for `usbg_video_a6400.ko` (avoiding the ~85s serial `printf` upload) while keeping default `ModDev` behavior intact.

---

### Phase 5 — Reproduce and Fix the Real-Camera `USB Streaming Test` + Delayed USB Plug-In Conflict
**Objective:** Faithfully reproduce the exact real-camera freeze scenario (USB unplugged -> enable mod in MODS tab -> plug USB 2s later) in QEMU, fix it cleanly, and verify full attach/detach/teardown cycles.

1. **Reproduce in QEMU:**
   - Boot with `usb-vbus=false` -> MENU -> MODS -> **USB Streaming Test** -> wait 2s -> `usb-vbus=true`.
   - Capture kernel `dmesg` and `im.elf` logs showing how `DefRootView::ActionOpenUSB` + `libInfraMscServer.so` + `usbg_storage.ko` collide with `usbg_video_a6400.ko` / `UsbStreamingTest.mod`.
2. **Fix in mod / kernel shim (QEMU-verified first):**
   - Coordinate `UsbStreamingTest.mod` and `usbg_video_a6400.ko` so that while **USB Streaming Test** is active, `DefRootView` does not launch MSC/PC Remote on cable plug-in (or `UsbControl` / `usbg_video_a6400.ko` wait for VBUS and claim the gadget cleanly without colliding with `usbg_storage`).
   - Verify full cycle in QEMU: unplugged USB -> enable mod -> plug USB 2s later -> camera stays in LiveView, does not enter MSC or crash, enumerates `054C:0322` and streams 720p MJPEG -> unplug USB / disable mod -> clean return to stock MSC.
3. **Final verification & documentation:**
   - Run focused QEMU checks (`qemu_dwc3_profile_check`, `qemu_uvc_smoke --strict-bulk`, new USB attach/detach test) and Windows `unittest` suite, and record new evidence receipts (`docs/evidence/0611-...`).

---

## Risks & Re-Plan Triggers
- **Captured vs. packaged binary drift (`E0596`, `E0597`):** Always match hashes and `.text` offsets against `inputs-moddev-1` captured images (`nflasha3-Image`, `av-cam.bin`, `nflasha15`).
- **Baseline isolation:** All new QEMU hardware/panel behaviors remain opt-in (`E0580`, `E0587`, `E0591`, `E0597`, `E0598` unchanged).
- **Physical safety:** All 5 phases run offline in QEMU (only a bounded 4 KiB read-only register check if Phase 2 requires an unmapped physical register value).
- **Re-plan triggers:**
  1. Phase 1 reveals `av-cam.bin` (ORIL) never receives `0x007A0020` from Linux, or `model/ROOT 0x102` requires an unmodeled multi-step lens/shutter handshake beyond `E0591` step 26.
  2. `libInfraMscServer.so` requires an unmodeled SDHCI/eMMC LUN ioctl (`mmc_drv` / `mcmn_drv`) before `StartGadget` executes.
  3. `usbg_storage` / `usbg_stillimage` use different DWC3 physical-to-logical endpoint mappings or TRB flags than UVC.
