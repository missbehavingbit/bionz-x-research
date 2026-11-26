# SilentShutterUnlock (`SMOD v2`)

RAM-only **Mod Loader v2** (`SMOD v2`) module for **Sony ILCE-6400 (`2.00` captured layout)** that lifts software mutual-exclusion locks in `libObj.so` **and** clears the menu grey-out inhibition bits in `libSysDef.so` (`DefInh::sm_refTbl`) when **Silent Shooting** is enabled or when **Auto / Superior Auto / SCN / Panorama** inhibits Silent Shooting:

- **Picture Profile** (`PP1`–`PP10`, Gamma, Color Mode, Knee, Detail, Color Depth/Phase, WB Shift)
- **Picture Effect** (main item + effect sub-modes)
- **Auto HDR / DRO** (main item + DRO/HDR levels)
- **Multi Frame NR** (Multi-Shot NR / ISO MFNR selection)
- **P / A / S / M Exposure Mode Lock** (`ExcMgr` exclusion bypass in `libObj.so` for `0x95c8`, `0x95bd`, `0x963f`, `0x9813`, `0x9b7f`, `0xc00d`, `0xc0f1` + clearing the 80 Auto/SCN/Panorama `FactorId` bits on Silent Shooting `FeatureId 0x0832`)

Clears all 5 Silent Shooting `FactorId`s (`0x0a83`, `0x0de2`, `0x114f`, `0x1296`, `0x13bb`) across the 69 verified target `FeatureId`s (`425` total `DefInh` bits cleared, `0x000001a9`) while keeping Flash, BULB, Long Exposure NR, and Flickerless stock (both in `ExcMgr` and in `DefInh::sm_refTbl`). Toggling the mod `OFF` restores all cleared `DefInh::sm_refTbl` bits back to stock values.

**Verification:**
- **Physical `ILCE-6400` (`2.00` captured layout, RAM-only):** Verified live via Mod Loader v2 (`SilentShutterUnlock.mod` `1916` B, SHA-256 `951e7417197bcd609fdd7515e1eb8fb14e23f5d5919c4e974d150be8bc540d0e`). Picture Profile (+ sub-items, main menu & C1), Picture Effect, Auto HDR/DRO, and Multi Frame NR are all un-greyed and functional with Silent Shooting ON; Silent Shooting can be enabled and shot in Auto mode.
- **Offline Unicorn:** `16` unlocked pairs / `5` stock-preserved pairs pass, `425` `DefInh::sm_refTbl` bits cleared on `ON` and restored byte-for-byte on `OFF`.

See [docs/SILENT_SHUTTER_MOD_IDEAS.md](../../docs/SILENT_SHUTTER_MOD_IDEAS.md) for the full analysis of stock limitations and potential follow-up mods.

---

## Installation & RAM-Only Test

1. **Stage onto SD card:** Copy [`SilentShutterUnlock.mod`](SilentShutterUnlock.mod) into `E:\mods\SilentShutterUnlock.mod` on your SD card (or push to `/tmp_bt/mods/SilentShutterUnlock.mod` via the USB Mass Storage debugger).
2. **Load Mod Loader v2 (RAM-only):** Connect the camera in Mass Storage (MSC) mode after a fresh boot and install `Mod Loader v2` using `tools/research/install_mlv2.py` (never writes to flash; power-cycling restores stock firmware).
3. **Toggle in camera menu:** Open `MENU` → tab 7 (`Mods`) → select **`Silent Unlock`** and press the center button to toggle `ON` / `OFF`.
4. **Diagnostics:** Toggling `ON`/`OFF` appends a line (`silent_unlock on=1|0 bypass=0x... total=0x... inh=0x...`) to `/tmp_bt/mods/silent_shutter_unlock.log`.

---

## Rebuild & Offline Verification

```powershell
wsl.exe --exec bash mods/silent_shutter_unlock/build_v2_wsl.sh artifacts/tests/silent-shutter-unlock-v5
C:\Python314\python.exe mods/silent_shutter_unlock/test_mod_v2_unicorn.py artifacts/tests/silent-shutter-unlock-v5
```
