# E0511 — Offline `ViewSettingMenu`, `CmnViewSettingNode`, and `.uxc` Menu Architecture

Date: 2026-11-14. **Strictly offline.** No camera access, no USB, no writes to firmware or hardware.
Scope: Static analysis of `nflasha15` menu libraries and compiled `.uxc` layout/resource files to map how the Sony ILCE-6400 menu is constructed, rendered, and where customization boundaries sit.

## 0. Short Answer

The ILCE-6400 menu system is split across four cooperating layers:

1. **Menu Data & Logic Model (`CautionConfig.so` + static node trees in `viewUnified2.so`) [C]:**
   `CautionConfig.so` defines **14,332** `CmnViewSettingNode` symbols representing tabs, pages, items, and custom-button/Fn options. Each node is constructed via `CmnViewSettingNode::CmnViewSettingNode(CmnViewSettingNode** children, int count, CmnViewSettingProperties const* props)` and exposes virtual property/selection methods (`vtable+0x28` `getSelectedItem`, `vtable+0x5c` `getProperty(prop_id, &out)` where `14` = `ProcId` and `15`/`16` = `ProcValId`, `vtable+0x8c` `isItemSelectable`).
2. **View Controller & Mode Selection (`viewUnified2.so`) [C]:**
   `ViewSettingMenuToInstance` (`0x00205574`) allocates `0x3d0` (976) bytes and constructs `ViewSettingMenu` (`0x00204c90`, inheriting `ViewBaseForMR`). Its setup routine (`0x00210940+`) registers layout ID `0xaadca2c7` via `ViewBase::setLayoutManagementInformation` (`0x00210976`) and selects the active root `CmnViewSettingNode*` tree (`controller+0x1a0` at `0x0021098c`) via `0x00208648`, which reads `BackupManager::Bkup_Read` and dispatches across a **75-case `tbh` jump table** (camera/dial modes).
3. **Compiled Binary Layouts & Resources (`share/app/*.uxc`) [C]:**
   `nflasha15_unpacked/share/app/` contains **228** `.uxc` files sharing the `uxc\x07` binary header (`u16@8` type, `u16@10` view/file ID, `u16@12` offset-table entry count, `u16@16..` relative offset table). Widget resource IDs used directly by `ViewSettingMenu` Action1 (`0x002127e8`) appear verbatim inside the `.uxc` files: `0xa056f604` (`PAS_MenuListTab`) in both `LayoutMaster_Menu.uxc` (`0xd93`) and `viewSettingMenu.uxc` (`0x1f37`), and `0xdabd8bcb` (`PAS_MenuListCategorizedItem`) in `LayoutMaster_Menu.uxc` (`0xc7f`).
4. **Layout & Resource Engine (`libObj.so`) [C]:**
   `libObj.so` exports `ux::wgtlay::WidgetLayouter`, `ux::wgtlay::LayoutConverter`, and `ux::resmgr::ResourceManager` (reading from `/usr/share/app/`), plus `CmnViewMenuData` (`initMenuData`, `isItemGreyout`, `setGreyOut`, `getSubItem`, `getIconResID`, `getText`).

## 1. Verified Inputs & Hashes

| File (under `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/`) | Size (bytes) | SHA-256 | Role |
| --- | ---: | --- | --- |
| `lib/viewUnified2.so` | 11,530,552 | `1e2867b6bff2d4fd4d3b93bacf8c7da0b9a86f266b4ba1763230e33badb6e7f2` | `ViewSettingMenu`, `PAS_Menu*` widgets, static `CmnViewSettingNode` trees [C] |
| `lib/CautionConfig.so` | 12,070,800 | `bfd1bd7bad0ab3478b894da5dbb51c15464b1bedc4201240ccb61cd743150ef7` | `CmnViewSettingNode` hierarchy (`14,332` `.dynsym` entries) [C] |
| `lib/libObj.so` | 20,860,436 | `60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1` | `ux::wgtlay::WidgetLayouter`, `ux::resmgr::ResourceManager`, `CmnViewMenuData` (E0483) [C] |
| `share/app/viewSettingMenu.uxc` | 13,940 | `af3ca5e6ad68c45ca73f340ac52f9a0120cd1f8bc4da53491759e9667c01e554` | Main `ViewSettingMenu` layout (`id=0x004c`, root `0x7e098000`) [C] |
| `share/app/LayoutMaster_Menu.uxc` | 3,512 | `2979f6a373bd63757ce587fef1e2e7c35406874cd76539d6712b426cb9efe484` | Shared master menu widget templates (`id=0x008f`, root `0x7e11e000`) [C] |

## 2. Static Code Findings (`viewUnified2.so` & `libObj.so`)

### 2.1 `ViewSettingMenu` Construction, Layout Binding, and Mode-Dependent Tree Selection

- `ViewSettingMenuToInstance` (`0x00205574`, Thumb): allocates `0x3d0` bytes (`operator new`) and calls constructor `0x00204c90` (`ViewBaseForMR::ViewBaseForMR(ViewManager*)` base). [C]
- Setup routine (`0x00210940+`):
  - `0x00210976`: calls `ViewBase::setLayoutManagementInformation(0xaadca2c7, factory_fn)`. [C]
  - `0x00210986`: calls `0x00208648` and stores the returned root `CmnViewSettingNode*` at `controller + 0x1a0` (`0x0021098c`). [C]
  - `0x00208648`: calls `BackupManager::Bkup_Read` into a stack byte, subtracts `1`, range-checks `<= 74` (`0x4a`), and dispatches via `tbh [pc, r3, lsl #1]` (`0x00208668`) to choose the mode-specific menu tree. [C]
- Static initializer (`0x00214000+`): constructs `CmnViewSettingNode` instances in `viewUnified2.so` via `_ZN18CmnViewSettingNodeC1EPPS_iPK24CmnViewSettingProperties@plt` (`0x00154024`) and registers destructors with `__aeabi_atexit`. [C]

### 2.2 Action Dispatch & Widget IDs (`0x002127e8`)

- `ViewSettingMenu` Action1 handler (`0x002127e8`) switches on `controller + 0x14c` (menu state):
  - **States 1 or 2 (main tabbed list):** calls `ViewBase::getWidget(0xa056f604)`, casts to `PAS_MenuListTab` (`0x005ab424`), fetches the 3-level selected node via `0x00207c4a` (three chained `vtable+0x28` calls from `controller+0x1a0`), and invokes `0x005ac044`. [C]
  - **States 3 or 4 (categorized item list):** calls `ViewBase::getWidget(0xdabd8bcb)`, casts to `PAS_MenuListCategorizedItem` (`0x00562b18`), fetches the 2-level selected node from `controller+0x1a4` (`0x00207f28`) or `0x0020c020`, and invokes `0x00562f26`. [C]

### 2.3 Node & Table Helpers

- `CmnSettingNodeUtil::getProcIdForItem` (`0x002fe9ea`) calls `node->vtable[0x5c / 4](14, &out)`; `getProcValIdForItem` (`0x002fea3a`) queries property `16` (`0x10`) and falls back to property `15` (`0x0f`) if unset or if `ProcId == 27`. [C]
- `CmnMenuTableUtil` (`0x003eac3c`, `0x003eacb4`) wraps `CmnViewMenuData` (`libObj.so`) for static table-driven sub-menus (`CmnViewMenuItemStruct*`, `isItemGreyout`, `setGreyOut`, `getIconResID`, `getText`). [C]

## 3. Compiled `.uxc` Resource & Layout Format (`share/app/*.uxc`)

All inspected `.uxc` files begin with magic `75 78 63 07` (`"uxc\x07"`), `u32@4 = 0`, `u16@8` type tag (`0x0008` for layout/view files, `0x000f` for `string_english.uxc`), and `u16@10` resource/view ID:

- `viewBaseMenu.uxc` (84 B): `id=0x008d`, root `0x7e11a000`. [C]
- `viewGlobalMenu.uxc` (3,316 B): `id=0x008e`, root `0x7e11c000`. [C]
- `LayoutMaster_Menu.uxc` (3,512 B): `id=0x008f`, `15` (`0x000f`) offset entries, root `0x7e11e000`, includes `640x480` (`0x0280 x 0x01e0`) display bounds at `0x84..0x8c`, `PAS_MenuListTab` ID `0xa056f604` at `0xd93`, and `PAS_MenuListCategorizedItem` ID `0xdabd8bcb` at `0xc7f`. [C]
- `viewSettingMenu.uxc` (13,940 B): `id=0x004c`, root `0x7e098000`, references `PAS_MenuListTab` ID `0xa056f604` at `0x1f37`. [C]
- `viewFnMenu.uxc` (6,868 B): `id=0x003c`, root `0x7e078000`. [C]
- `master_camera.uxc` (101,720 B): `id=0x004e`. [C]
- `string_english.uxc` (375,160 B): `type=0x000f`, `id=0x0027`, stores UTF-8 null-terminated strings indexed by binary offset tables. [C]

`libObj.so` contains the base directory string `"/usr/share/app/"` (`0x01002e0d`) used by `ux::resmgr::ResourceManager` and `ux::wgtlay::WidgetLayouter`. [C]

## 4. Tooling Boundary & Customization Implications

- **REA/Ghidra batch timeout [C]:** `tools.research.rea_wsl_batch` on `viewUnified2.so` (`11.53 MB`) hit Ghidra's `330000 ms` `REA_GHIDRA_STARTUP_TIMEOUT_MS` ceiling during initial auto-analysis (`artifacts/tools/rea/menu-research-20261114-b`). Disassembly and ELF/symbol verification in this record were therefore performed directly via `arm-linux-gnueabi-objdump` (WSL) and `pyelftools`.
- **Customization surfaces identified [I]:**
  1. **Menu structure & items (add/remove/reorder tabs, pages, items):** governed by the static `CmnViewSettingNode` trees in `viewUnified2.so` (`0x00214000+`), the 75-case mode selector (`0x00208648`), and `CmnViewSettingNode` subclasses in `CautionConfig.so` (rather than `.uxc` layout files alone).
  2. **Visual layout, geometry, strings, and icons:** governed by `LayoutMaster_Menu.uxc`, `viewSettingMenu.uxc`, `string_<lang>.uxc`, and `image_*.uxc` under `/usr/share/app/`, loaded by `ux::wgtlay::WidgetLayouter` and `ux::resmgr::ResourceManager` in `libObj.so`.
  3. **Input/touch policy & widget hooks:** governed by `PAS_MenuListTab` / `PAS_MenuListCategorizedItem` widget methods in `viewUnified2.so` (e.g., `0x005ab424`, `0x005ac044`, `0x005ac110`).
- **Next offline research step [I]:** Decode the `CmnViewSettingProperties` struct layout passed to `CmnViewSettingNode::CmnViewSettingNode` at `0x00214140+` (linking each node to its `ProcId`, `ProcValId`, string ID, and icon ID) and map the `LayoutConverter` property tags in `.uxc` records.
