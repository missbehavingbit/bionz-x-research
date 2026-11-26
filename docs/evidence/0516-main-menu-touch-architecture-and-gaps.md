# E0516 — Main-Menu Touch Architecture, `PAS_MenuListTab` Vtable, and Full-Touch Requirements

- **Date:** 2026-11-14
- **Scope:** Offline static analysis of `viewUnified2.so` (`PAS_MenuListTab` vtable and `ViewSettingMenu` navigation) and `libObj.so` (`WidgetSystem` mouse release consumer `0x005f1dc0`), building on `E0128–E0131` and `docs/MAIN_MENU_TOUCH_PROTOTYPE.md`.

## 1. Input Identities & Artifacts

- `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/lib/viewUnified2.so` (`sha256: 1e2867b6bff2d4fd4d3b93bacf8c7da0b9a86f266b4ba1763230e33badb6e7f2`)
- `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/lib/libObj.so` (`sha256: 60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1`)
- Generated analysis outputs:
  - `artifacts/analysis/e0516_libobj_mouse.txt` (`libObj.so` `0x005f1c00..0x005f2350`)
  - `artifacts/analysis/e0516_viewsettingmenu_keys.txt` (`viewUnified2.so` `0x00207c00..0x00207f00`)
  - `artifacts/analysis/e0516_viewsettingmenu_nav.txt` (`viewUnified2.so` `0x00207484..0x00207c00`)

## 2. Why `PAS_MenuListTab` Does Not Receive Native Touch Events

- **[C] `PAS_MenuListTab` RTTI and Vtable (`viewUnified2.so`):**
  - RTTI string `"15PAS_MenuListTab"` at `0x00802a8c`, typeinfo at `0x00939388`, primary vtable at `0x009391d0`.
  - Vtable slots `+0x068` (`WidgetBase::onMouseEvent`), `+0x06c` (`WidgetBase::onMouseEventHook`), and `+0x070` (`WidgetBase::onKeyEvent`) are **not overridden** by `PAS_MenuListTab` (they remain base `WidgetBase` entries).
  - Vtable slot `+0x108` is `LayoutableWidgetBase::setDefWidgetMode(WidgetMode)`, leaving the menu in passive mode (`mode == 2`, `E0131`).
  - Vtable slots `+0x1ac` (`0x005ac111`, refresh) and `+0x1b0` (`0x005ab573`) are `PAS_MenuListTab`-local.
- **[C] `libObj.so` Mouse Release Routing (`0x005f1dc0`, live `0x06309d48`):**
  - `WidgetSystem::postMouseRelease` (`0x005f2248`) queues release events; the consumer at `0x005f1dc0` clears the button bit in `[r8 + 0x4b0]` and loads the captured target `[r8 + 0x4b8]` or press target `[r8 + 0x4b4]` into `r6`.
  - When the menu is passive (`mode == 2`), press handling never assigns `[r8 + 0x4b4]`, so `r6 == 0` and `0x005f1e06` (`cmp r6, #0; beq 0x005f1eba`) exits before dispatching `MouseEvent` (`type=6`).
  - Intercepting `0x005f1dc0` (as validated in `E0128` via `menu_touch_trace.c`) receives every physical touch release on `Appframework` TID `380` even when `r6 == 0`.

## 3. `libObj.so` & `viewUnified2.so` Symbol Offsets for Touch Integration

- **[C] `libObj.so` `.dynsym` (Thumb `+1`):**
  - `0x005ed0f9`: `ux::wgtsys::Widget::isHit(ux::core::Vector2 const&)` (live `0x06305080`)
  - `0x005ed545`: `ux::wgtsys::Widget::setMode(ux::wgtsys::Widget::WidgetMode)` (live `0x063054cc`)
  - `0x005f1091`: `ux::wgtsys::WidgetSystem::getMousePosInFramebuffer(unsigned char, short&, short&)` (live `0x06309018`)
  - `0x005f2249`: `ux::wgtsys::WidgetSystem::postMouseRelease(unsigned char, unsigned char, ux::core::MsecTime)`
- **[C] `PAS_MenuListTab` internal fields (`viewUnified2.so`):**
  - `0x001e4..0x001f8`: tab/selection indices (`0x001ec` = tab count returned by `0x005aa46c`).
  - `0x186ac`: `CmnViewSettingNode*` root model pointer (`binding` at `0x005ab430`).
  - `0x088b0` (stride `0x0220`, `5` slots, `0x005abc9a: cmp r5, #5`) and `0x05400..0x09200` / `0x0fb20` (`PAS_MenuListPopup`): child widget arrays.

## 4. Concrete Requirements for Full Main-Menu Touch Controls

1. **Row Selection (already prototyped in `E0128–E0131`):**
   - Hook `0x005f1dc0` (live `0x06309d48`) on TID `380`, guard via `menu_touch_native` (active menu `state == 2`, `PAS_MenuListPopup` closed, ancestor `WidgetMode` valid, real lifetime `epoch`), hit-test the 6 row widgets via `Widget::isHit`, update `setSelectedIndex`, call `binding(0x005ab430)` + `refresh(0x005ac110, 0)`.
   - **Remaining item from `E0131`:** drive a real non-zero `epoch` from menu lifecycle transitions (open/close/page-change) rather than a fixed test constant.
2. **Row Activation (Tap-to-Enter):**
   - Dispatch `ViewSettingMenu` Enter / `Action1` (`0x002127e8`) when tapping an already-highlighted row (or single-tap-to-enter).
3. **Tab & Page Touch Navigation:**
   - Map the 6 top tab-bar widget rects and page-bar/arrow widget rects inside `PAS_MenuListTab` (`viewSettingMenu.uxc`), hit-test them with `Widget::isHit`, and update the selected `Tab` / `Page` node + `binding` + `refresh`.
4. **Sub-menu / `PAS_MenuListPopup` Touch (Phase 2):**
   - Extend hit-testing to `PAS_MenuListPopup` (`menu + 0x0fb20`) when `state == 3` / popup flag `1`.
5. **Touch-Friendly Layout Modernization:**
   - Prune/consolidate `Mode 67` (`0x00b0fcec`) pages in RAM so high-use settings sit on early pages with clean, readable labels.
