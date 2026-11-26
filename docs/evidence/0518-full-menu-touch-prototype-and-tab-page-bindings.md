# E0518 — Full Main-Menu Touch Prototype (Tabs, Page Dots, Rows, Tap-to-Enter)

- **Date:** 2026-11-14
- **Scope:** Offline static verification, freestanding Thumb-2 payload build, and guarded RAM-only installer/remover for full `PAS_MenuListTab` touch control on FW 2.00 (`im.elf`). No camera writes or live probes executed.
- **Inputs / Artifacts:**
  - `artifacts/hardware/live-libs/viewUnified2.so` (`PAS_MenuListTab` + `ViewSettingMenu` disassembly)
  - `tools/native_camera/menu_touch_full.c`
  - `tools/native_camera/native_menu_touch_full_guard.c`
  - `tools/native_camera/menu_touch_trace_trampoline.S` (existing trampoline -> `0x06309d55`)
  - `tools/research/build_menu_touch_full.py`
  - `artifacts/hardware/menu_touch_full.elf`, `artifacts/hardware/menu-touch-full-payload.bin`, `artifacts/hardware/menu-touch-full-{check,install,remove}.req`, `artifacts/hardware/menu-touch-full-build.json`, `artifacts/hardware/native_menu_touch_full_guard`
  - `tests/test_menu_touch_full.py`

## 1. Static Verification of Tab, Page, and Enter Contracts (`viewUnified2.so`)

Disassembly of `viewUnified2.so` (live relocation base `0x155e6000`):

1. **Tab Boxes (`0x005abcd8..0x005abda6`):**
   - Loops `r5 = 0 .. tabs - 1` (`5aa458(menu)` -> `menu + 0x1e4`).
   - Updates `GEN_Icon` tab boxes at `menu + 0x0b9c + r5 * 0x220` (`vtable + 0x104` image `0x1f/0x1e/0x1d` for active/inactive/focus tab box) and tab icons at `menu + 0x1a7c + r5 * 0x220`.
2. **Page Dots (`0x005aaef0..0x005aaf98`):**
   - Loops `r8 = 0 .. 14` across the 15 `GEN_Icon` page indicators at `menu + 0x166b4 + r8 * 0x220`, showing `r8 < page_count` (`menu + 0x1e8`) and positioning at `x = 320 + 3 - 9 * page_count + 18 * r8`.
3. **Tree Binding & Refresh (`PAS_MenuListTab::binding` `0x005ab430` -> live `0x15b91431`, `refresh` `0x005ac110` -> live `0x15b92111`):**
   - `binding(menu)` walks `root = *(menu + 0x186ac)` -> `tab_node` -> `page_node` using vtable `+0x10` (`getCount`), `+0x28` (`getSelectedChild`), `+0x30` (`getSelectedIndex`) and writes counts/indices into `menu + 0x1e4..0x1fc`.
   - `refresh(menu, 0)` redraws tabs, page dots, and the 6 item rows (`menu + 0x9350 + r * 0x1118`) from the updated tree selection.
4. **Enter Action (`ViewSettingMenu::Action1` `0x002127e8` -> `PAS_MenuListTab::enterSelectedItem` `0x005ac044` -> live `0x15b92045`):**
   - Invokes `enter_selected(menu, leaf)` where `leaf` is `indexed_child(page_node, r + 1, &leaf)`. Opens `PAS_MenuListPopup` (`menu + 0xfb20`) when `leaf->getCount() > 0` or executes the item callback when `0`.

## 2. Freestanding Payload & Guard Design

- **Hook target:** `0x06309d48` (`libObj.so` mouse release consumer; original 12-byte prologue `2de9f04788b04ff014080122`).
- **Owned RX padding:** `0x0700d708` (`1096` / `2296` bytes used; `entry = 0x700d76d`, Thumb-2).
- **Owned RW padding:** `0x07143920` (`624` / `1760` bytes used; `MTF1` state + `16`-event trace ring).
- **Runtime guards before any UI call:**
  - `system == 0x956f0`, `mouse < 4`, valid framebuffer coordinates `(x, y) != (32767, 32767)`.
  - `lookup(0xaadca2c7, 0xdfdc8ddd, 0xa056f604, &err)` matches `PAS_MenuListTab` (`vtable == 0x15f1f1d0`, `self == menu`), `main_state(menu)` (`0x15b90535`), `byte(menu, 0x21) == 2`, `!popup_open(menu + 0xfb20)` (`0x15b95b61`), `!byte(menu, 0x1d8)`, `inherited_mode(menu) <= 2` (`0x631e815`).
  - Verified 3-level `CmnViewSettingNode` tree (`vtable == 0x16c183e8`), bounded `1 <= tabs <= 6`, `1 <= pages <= 15`, `1 <= rows <= 6`, and `*(menu + 0x1f8) == row_sel - 1`.
- **Hit-test order (`Widget::isHit` `0x6305081`, `{ (float)x, (float)y }`):**
  1. Tab boxes (`menu + 0x0b9c + i * 0x220`) -> `set_selected_index(root, i + 1)` + `binding` + `refresh`.
  2. Page dots (`menu + 0x166b4 + p * 0x220`) -> `set_selected_index(tab_node, p + 1)` + `binding` + `refresh`.
  3. Item rows (`menu + 0x9350 + r * 0x1118`) -> if `r + 1 != row_sel`: select row (`set_selected_index(page_node, r + 1)` + `binding` + `refresh`); if `r + 1 == row_sel`: `enter_selected(menu, leaf)`.
- **Atomic guard (`tools/native_camera/native_menu_touch_full_guard.c`):**
  - Reads `/tmp_bt/menu_touch_full.req`, checks `MTFR` header + FNV-1a checksum (`0xaabdef36`), `/proc/149/comm == im.elf`, `/proc/149/maps`, thread PCs clear, zeroed RX/RW regions before install, readback verification, and full rollback/restore (`op=2`).

## 3. Offline Build & Test Results

- `wsl.exe arm-linux-gnueabi-gcc ... -Os -Wall -Wextra -Werror -o artifacts/hardware/menu_touch_full.elf ...`: exit `0`, `0` warnings, `code_bytes = 1096` (`<= 2296`), `sha256 = 0cfb6ff6a617a832ba29c021163263f9129d06c1243031dbf924d14edecb6c53`.
- `wsl.exe arm-linux-gnueabi-gcc -O2 -static -Wall -Wextra -Werror -o artifacts/hardware/native_menu_touch_full_guard ...`: exit `0`, `0` warnings.
- `python -m tools.research.build_menu_touch_full`: exit `0`, generated `.bin`, `-{check,install,remove}.req`, and build JSON.
- `python -m unittest discover -s tests -p "*touch*" -q`: `Ran 31 tests in 0.033s — OK`.

## 4. Next Boundary

- Ready for live RAM-only camera verification on user go-ahead: push `native_menu_touch_full_guard` + `menu-touch-full-{check,install,remove}.req` to `/tmp_bt/`, run `op=0` (read-only preflight check) then `op=1` (install), test physical screen taps on tabs, page dots, rows, and second-tap Enter, and verify `op=2` clean removal.
