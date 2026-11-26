# E0515 — Complete Menu Tree, Mode 67 (`ILCE-6400`), Runtime Limits, and Modification Contracts

- **Date:** 2026-11-14
- **Scope:** Strictly offline static analysis of `viewUnified2.so`, `CautionConfig.so`, `libObj.so`, `viewSettingMenu.uxc`, and `string_english.uxc` from `ILCE-6400` (`0700_part_image/dev/nflasha15_unpacked`). No camera connection, USB traffic, or firmware modification was performed.

## 1. Input Identities & Tools

- `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/lib/viewUnified2.so` (`11,530,552 B`, `sha256: 1e2867b6bff2d4fd4d3b93bacf8c7da0b9a86f266b4ba1763230e33badb6e7f2`)
- `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/lib/CautionConfig.so` (`12,070,800 B`, `sha256: bfd1bd7bad0ab3478b894da5dbb51c15464b1bedc4201240ccb61cd743150ef7`)
- `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/lib/libObj.so` (`20,860,436 B`, `sha256: 60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1`)
- `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/share/app/viewSettingMenu.uxc` (`13,940 B`, `sha256: af3ca5e6ad68c45ca73f340ac52f9a0120cd1f8bc4da53491759e9667c01e554`)
- `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/share/app/string_english.uxc` (`375,160 B`, `sha256: b62c58521eca7b0443c5e570aaf8554ae5b05d94f46940dce7d9bc01a94b2921`)
- Tools: `arm-linux-gnueabi-objdump` (via WSL) and `.venv\Scripts\python.exe` (`pyelftools`).
- Generated analysis outputs:
  - `artifacts/analysis/e0515_mode_selector.txt`
  - `artifacts/analysis/e0515_all_init.txt`
  - `artifacts/analysis/e0515_full_menu_tree.json`
  - `artifacts/analysis/e0515_pas_menulisttab.txt`
  - `artifacts/analysis/e0515_uxc_check.txt`

## 2. Mode Selector (`0x00208648`) and Full Static Node Reconstruction

- **[C] Backup key & jump table (`viewUnified2.so` `0x00208648..0x00208840`):**
  - Calls `BackupManager::Bkup_Read(0x01070316, &mode_byte)` (`0x00208654` loads literal `0x01070316` from `0x0020882c`).
  - Subtracts `1` (`0x00208660`), bounds-checks `<= 74` (`0x4a`), and dispatches via `tbh [pc, r3, lsl #1]` at `0x00208668` across **75** mode indices (`1..75`), resolving to **49** unique targets (`NULL` for mode `32`, and `48` distinct `CmnViewSettingNode*` root objects).
- **[C] Complete static node reconstruction (`0x00213b8c..0x00228b50`):**
  - Disassembly of `0x00213b8c..0x00235000` contains **`1,748`** calls to `CmnViewSettingNodeC1(CmnViewSettingNode**, int, CmnViewSettingProperties const*)` (`0x00154024@plt`), ending at `0x00228b2a`.
  - Forward Thumb-2 register-tracking over `artifacts/analysis/e0515_all_init.txt` reconstructed **`1,748 / 1,748`** calls (`0` unmatched), including all `48` non-NULL mode roots and their Tab -> Page -> `cmnViewSettingNodeRoot*` hierarchies in `artifacts/analysis/e0515_full_menu_tree.json`.
  - Some nodes pass local `CmnViewSettingProperties` constructed in `viewUnified2.so` via `CmnViewSettingPropertiesC1(const _settingProperty*, int)` (`0x0014fa0c@plt`), while leaf `cmnViewSettingNodeRoot*` items resolve to `CautionConfig.so` symbols.

## 3. Identification of `ILCE-6400` Menu Root: Mode `67` (`0x00b0fcec`)

- **[C] Feature classification across all `48` non-NULL mode trees:**
  - Only two mode trees combine `ApplicationList = False` (no PlayMemories Camera Apps), `MyMenu = True`, `Bluetooth = True`, and `IntervalRECSettings = True`:
    - **Mode `67` (`0x00b0fcec`)**: `6` tabs, `188` leaf item references. Includes `cmnViewSettingNodeRootSelfPortraitShooting`, `cmnViewSettingNodeRootSilentShutter`, `cmnViewSettingNodeRootPictureProfile`, `cmnViewSettingNodeRootSLogDispAssist`, and `cmnViewSettingNodeRootBluetoothRemoteCtrl`, and omits `DoubleSlotSettings`, `WiredLANSetting`, and `APSCSuper35mm`.
    - **Mode `75` (`0x00b0e564`)**: `6` tabs, `186` leaf item references. Includes `DoubleSlotSettings`, `WiredLANSetting`, `APSCSuper35mm`, and `SteadyShotSettings` (full-frame dual-slot profile).
- **[I] Conclusion:** Mode `67` (`0x00b0fcec`) is the `ILCE-6400` (`a6400`) main menu tree.
- **[C] Mode `67` (`0x00b0fcec`) Tab & Page structure:**
  - **Tab 1 (`0x00b09c4c`, Camera Settings 1):** `14` pages (`6, 4, 6, 3, 7, 6, 5, 6, 2, 5, 6, 2, 6, 5` items per page; note Page 5 has `7` static items).
  - **Tab 2 (`0x00b134a4`, Camera Settings 2):** `9` pages (`6, 6, 5, 5, 3, 6, 2, 6, 6` items).
  - **Tab 3 (`0x00b09c74`, Network):** `2` pages (`6, 5` items).
  - **Tab 4 (`0x00b09c9c`, Playback):** `3` pages (`6, 6, 6` items).
  - **Tab 5 (`0x00b09cc4`, Setup):** `7` pages (`6, 6, 6, 6, 6, 6, 4` items).
  - **Tab 6 (`0x00b09cec`, My Menu):** `6` pages (Pages 1–5 are `0`-item user-customizable containers `0x00b15758`, `0x00b15780`, `0x00b157a8`, `0x00b19268`, `0x00b157d0`; Page 6 `0x00b157f8` has `5` My Menu management items: `AddItem`, `SortItems`, `DeleteItem`, `DeleteItemsInPage`, `MyMenuSettingReset`).

## 4. Runtime UI Limits in `PAS_MenuListTab` (`0x005ab424..0x005ac300`)

- **[C] Hardcoded widget-slot counts in `PAS_MenuListTab`:**
  - `0x005ab6a4..0x005ab6e2` (`cmp r5, #6`): iterates over **6** `PAS_BtnPopup` row widgets via `Check_PAS_BtnPopup`.
  - `0x005abb10..0x005abc3c` (`cmp r6, #6`): populates **6** visible row slots (`GEN_Label::setText`, `setVariantInfo`, `GEN_Icon::setImage`).
  - `0x005abc4c..0x005abc9c` (`cmp r5, #5`): iterates over **5** widget slots at `r4 + 0x88b0` (stride `0x220`).
- **[C]/[I] Dynamic vs. fixed boundaries:**
  - Tab count, page count per tab, and item list per page are driven dynamically by the `CmnViewSettingNode` tree (`ViewSettingMenu` + `CmnViewSettingNode` child counts).
  - Visible rows per page on screen are hardcoded to **6** in `PAS_MenuListTab` (and `viewSettingMenu.uxc`). Pages with `<= 6` items render directly; pages with `> 6` items (such as Tab 1 Page 5 with `7` static entries) rely on runtime availability filtering/scrolling within the `6`-row viewport.

## 5. `.uxc` Load-Time Integrity Verification (`libObj.so`)

- **[C] No CRC32 or cryptographic checksum in `uxc\x07` validators (`0x0079dc40`, `0x0079dc9c`):**
  - Both functions call header helpers `0x0079d5c4` and `0x0079d5ce` (which log via `stella_print_log` on unexpected header values) and then compare the first `uint32_t` at `[r4, #0]` against `0x07637875` (`"uxc\x07"` little-endian) (`0x0079dc76..0x0079dc82`).
  - No CRC32, MD5, SHA, or signature verification is performed by these `.uxc` container loaders.

## 6. Actionable Blueprint for Modifying the Main Menu

1. **Level 1 — Label / Text Customization (`share/app/string_english.uxc`):**
   - Modify or repack `string_english.uxc` (`uxc\x07` container, `0x1e04xxxx` ResIDs) to rename tabs, items, or guide strings without altering any ELF binary.
2. **Level 2 — Menu Tree Customization (`lib/viewUnified2.so`, Mode `67` `0x00b0fcec`):**
   - Reorder, remove, or swap `cmnViewSettingNodeRoot*` pointers inside existing Page child-pointer arrays, and/or adjust `CmnViewSettingNodeC1` `r2` (`count`) arguments (keeping `<= 6` visible items per page for clean 1-screen display).
   - Reorder or trim Page pointer arrays inside Tabs `1..6` (`0x00b09c4c`, `0x00b134a4`, etc.) or swap Tab pointers inside `0x00b0fcec`.
   - Because existing pointer slots and relocations are reused in-place, no ELF section resizing or new `.rel.dyn` entries are required.
3. **Level 3 — Custom Actions / Runtime Prototyping:**
   - Prototype tree/string changes offline in the emulator (`UnicornMachine` / `InterpreterProbe`) or via an `LD_PRELOAD` / memory-overlay hook on `ViewSettingMenu` (`0x00208648`) before considering any on-device overlay.
