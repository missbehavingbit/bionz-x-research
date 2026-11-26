# E0589 — QEMU USB Mode Gate, `DefRootView::ActionOpenUSB`, and `StartGadget` (`0x0A2000`)

**Date:** 2026-11-18  
**Goal:** Identify which firmware component consumes `0x0A400A` (`MSGID_VBUS_ON_EVT`) or `sendJackStatusToInput(1, 1)` (`0x007A0020`) and gates `0x0A2000` (`StartGadget`) into `UsbControl`, and verify its runtime state in QEMU (`sony-a6400`).

---

## 1. Inputs & Tool Identities

- **Kernel (`nflasha3-Image`):** SHA-256 `12eeec829407595e28265f7d0db3b88c56f63a9f0fdd725c9c53c66a49643707`
- **Firmware libraries (`nflasha15_unpacked/lib/`):**
  - `libUsbControl.so` (94,220 B, SHA-256 `70cd5742fde9eea78c60905e4015531b4c3ab2668d2cb85c934531f451f1211f`)
  - `libBusPower.so` (`BusPower`)
  - `libInfraMscServer.so` (`MassStorageServer`), `libInfraMtpServer.so` (`MtpServer`), `libInfraRemote.so` (`InfraRemoteUsb`)
  - `libObj.so` (`CEC` + `libObj` core), `viewUnified2.so` (`DefRootView`)
- **QEMU overlay:** `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` with `USB_VBUS_TEST=1` and `USB_CID_HIGH=1` (`usb-vbus-test=on`, `usb-cid-high=on`), preserving the `E0580` default (`usb-cid-high=off`).
- **Artifacts:** `artifacts/qemu-a6400/e0589-usb-mode-gate/` (`prepare.py`, `build.py`, `fsm-trace.c`, `vbus-trace.so`, `replay-vbus.py`, `correlate.py`, `correlation.json`, `vbus-report.json`, `vbus-console.log`).

---

## 2. Static Analysis Observations

1. **`0x000A400A` (`MSGID_VBUS_ON_EVT`) consumers across `nflasha15_unpacked/lib/`:**
   - Only six libraries contain the aligned `0x000A400A` literal: `libUsbControl.so` (sender), `libBusPower.so`, `libInfraMscServer.so`, `libInfraMtpServer.so`, `libInfraRemote.so`, and `libObj.so` (`0x83AA20`).
   - Disassembly of `libObj.so+0x839BD4..0x83AA50` (`CEC_init@@Base` region) shows `0x83A9C4` receives `0x0A6000..0x0A6005` (`JACK_STATUS` range) and explicitly returns immediately on `0x0A400A`/`0x0A400B` (`0x83A9D8..0x83AA1C`); `libObj.so+0x839C1B` is the HDMI-CEC FSM observed in `E0588`.
2. **`BusPower` (`libBusPower.so`) does not send `0x0A2000` (`StartGadget`):**
   - `BusPower::UsbCtrl_StartBusPowerFunction` (`+0x5DC0`) calls `UsbCtrl_StartGadgetBusPower` (`+0x59F8`), which sends `0x00083001` (category `0x6103`), not `0x0A2000`.
3. **USB gadget mode dispatch lives in `viewUnified2.so` (`DefRootView`):**
   - `DefRootView::ActionOpenUSB` (`+0x2ADFB8`, vtable `+0x8E3F88`) calls `+0x2ADF68`, which checks `WrapperSettingUtil::isCtrlWithSmartPhoneOn()` (returns `2` if enabled) and otherwise calls `BackupManager::Bkup_Read(0x0107001F, &val)` to select `view/USBAUTO` (`1`), `view/USBMASSSTORAGE` (`2`), `view/USBMTP` (`3`), or `view/USBREMOTEPC` (`4`).
   - `DefRootView::CheckerUSBAndClockReady` (`+0x2AC00A` / `+0x2AC044`) and `CheckerUSBAndClockReady4FirstView` (`+0x2B271A`) query `model/ROOT` (`0x6696F8`, param `0x102` / `0x10B`) and `InputService::getInputEventStatus` (`0x15040010`, param `0x17005008`).
   - `UsbControl::sendJackStatusToInput(1, 1)` (`libUsbControl.so+0x12B24`) allocates a 16-byte message on `0x007A0020` with `keyLocal2PhysCode(1)` and sends it via `osal_snd_msg`.

---

## 3. QEMU Runtime Verification (`E0589`)

**Command & Policy:**
```bash
D=artifacts/qemu-a6400/e0589-usb-mode-gate
python3 $D/prepare.py
gcc -shared -fPIC -O2 -I$HOME/sony-a6400-qemu/include/qemu $(pkg-config --cflags glib-2.0) $D/fsm-trace.c -o $D/vbus-trace.so $(pkg-config --libs glib-2.0)
python3 $D/build.py
PYTHONPATH=. python3 $D/replay-vbus.py   # USB_VBUS_TEST=1 USB_CID_HIGH=1, 20s settle after __USB_STATE_END__
python3 $D/correlate.py
```

**Observed Results (`correlation.json`, `vbus-report.json`):**
- **Loaded libraries in `im.elf` (`/proc/$p/maps`):** `libUsbControl.so` (`0x049C5000-0x049DB000`), `libBusPower.so` (`0x080E8000-0x080F0000`), and `viewUnified2.so` (`0x13FAA000-0x14856000`) are loaded. `libInfraMscServer.so`, `libInfraMtpServer.so`, and `libInfraRemote.so` are **not loaded** at boot.
- **Boot / `FirstView` window (before `usb-vbus=true` at line 4016):**
  - `libUsbControl.so+0x10678` (`UsbControl::DispatchEvent`) ran **1** time during boot (`r6=0x000A2000`, `r3=0x000A2002` → `STOP` entry).
  - `viewUnified2.so+0x2AC00A` (`CheckerUSBAndClockReady`), `+0x2AC044`, and `+0x2B271A` (`CheckerUSBAndClockReady4FirstView`) each ran **1** time (`L3382–L3384`, `lr=libObj.so+0x418D45`), before `usb-vbus=true`.
- **VBUS attach/detach (`value=00000003` at `L4016`, `value=00000002` at `L4127`):**
  - `libUsbControl.so+0x12980` (`SetJackStatus`) ran **7** times total (boot + attach/detach), and `libBusPower.so` transitioned `0 → 2 → 0` (`DispatchEvent`), while `libBusPower.so+0x59F8` / `+0x5F7C` ran **0** times.
  - `viewUnified2.so+0x2ACBB0` (`CheckerUSBConnect`), `+0x2ADFB8` (`ActionOpenUSB`), and `+0x2A524C` (`ActionConnectUsbAtPreStable`) ran **0** times.
  - `libUsbControl.so+0x12C78` (`UsbIfWrapper::StartGadget`) ran **0** times.

---

## 4. Conclusions, Assumptions, and Next Boundary

- **Verified:** `MassStorageServer`, `MtpServer`, and `InfraRemote` are not loaded at boot; they are demand-loaded by `viewUnified2.so` (`DefRootView::ActionOpenUSB` → `view/USBAUTO`, `view/USBMASSSTORAGE`, `view/USBMTP`, or `view/USBREMOTEPC`). Because `DefRootView` does not advance past its boot/clock-readiness gate (`CheckerUSBAndClockReady` / `CheckerUSBAndClockReady4FirstView`, while Live-View remains blocked at step 32), `sendJackStatusToInput(1, 1)` on `0x007A0020` never triggers `ActionOpenUSB`, so `0x0A2000` (`StartGadget`) is never sent to `UsbControl`.
- **Inferred:** Unblocking `DefRootView` to a ready shooting/playback state (Phase 4 Live-View step 32 / initial clock setup) — or testing `cxd90045-dwc3` gadget bring-up via an explicit `0x0A2000` (`StartGadget`) / `view/USBREMOTEPC` trigger in Phase 2 — is the next boundary before `UsbIfWrapper::StartGadget` (`+0x12C78`) drives `0xF0200000`.
- **Repeatability:** `correlate.py` passed (`exit 0`, exact counts verified).
