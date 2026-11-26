# E0519 — Live v1 Touch Validation and v2 Swipe + Popup Prototype

- **Date:** 2026-11-14
- **Scope:** Live v1 RAM test observations + clean removal (`MTF1`), followed by offline mapping, build, and unit verification of the v2 dual-hook prototype (`MTF2`) adding horizontal swipe paging and full `PAS_MenuListPopup` touch support.

## 1. Live v1 (`MTF1`) RAM Test Receipt

- Installed v1 (`0x06309d48` -> `0x0700d76d`, `1104 B`, `enabled=2`) and verified camera stability (`uptime 542.26s` -> `665.27s`).
- Read back `0x07143920` (`624 B`): `count = 15`, `inflight = 0`, `last_err = 0`.
  - Events `1..10` and `14..15`: `MT_ACT_TAB` switching across tabs `1..6`.
  - Events `11..12`: `MT_ACT_ROW_SELECT` on rows `3` and `4`.
  - Event `13`: `MT_ACT_ROW_ENTER` on row `4` (opened `PAS_MenuListPopup`).
- Cleanly removed (`op=2`): `REMOVED hook=06309d48 code_restored=1 state_restored=1`, `FREEZE threads=175 attached=175 resumed=175 result=0 elapsed_ms=1060`, `/tmp_bt` cleaned.

## 2. v2 (`MTF2`) Static Mapping & Implementation

1. **Dual-Hook Press + Release (`libObj.so`):**
   - `0x06309e54` (`0x005f1ecc`, `WidgetSystem::press`, 12-byte prologue `2de9f04788b04ff014090122`, no PC-relative instructions) -> `mt_full_press` (`0x0700d789`) -> trampoline to `0x06309e61`. Records `(press_x, press_y, press_valid=1)`.
   - `0x06309d48` (`0x005f1dc0`, `WidgetSystem::release`) -> `mt_full_release` (`0x0700d845`) -> trampoline to `0x06309d55`.
2. **Horizontal Swipe Paging (`PAS_MenuListTab`):**
   - On release when `|dx| >= 48` and `|dx| > 2 * |dy|`: swipes left (`dx < 0`) to next page (or next tab) and right (`dx > 0`) to previous page (or previous tab), then calls `binding(menu)` + `refresh(menu, 0)`.
3. **`PAS_MenuListPopup` (`menu + 0xfb20`, `viewUnified2.so` `0x005af6dc..0x005b1020`):**
   - Popup node: `*(popup + 0x6490)` (`CmnViewSettingNode *`, vtable `0x16c183e8`).
   - Scroll top index: `*(uint8_t *)(popup + 0x648c)`.
   - Visible row widgets (`0..5`): `popup + 0x1070 + r * 0x398`.
   - Background panel widget: `popup + 0x2600`.
   - Actions: vertical swipe (`|dy| >= 36 && |dy| > |dx|`) calls `popup_scroll` (`0x15b96f45`, `dir = dy < 0 ? 1 : 0`); tapping a visible row sets `set_selected_index(pnode, top + r + 1)` and invokes `enter_selected(menu, leaf)` (`0x15b92045`) to apply and close; tapping outside `popup + 0x2600` calls `cancel_popup(menu)` (`0x15b9184d`).

## 3. Offline Build & Test Verification

- `menu_touch_full.elf`: `1844 / 2296` bytes `.text`, `0` `.data`/`.bss`, `0` warnings (`-Wall -Wextra -Werror`), `sha256 = 8f18986eaad27a9fc97a8da34b46cc880e83b1f0d74cbc16326bf9e1d54261a3`.
- `native_menu_touch_full_guard`: static stripped ARM binary (`0` warnings).
- `python -m tools.research.build_menu_touch_full`: generated `MTFR` v2 `.req` files (`check`, `install`, `remove`) and manifest (`state_bytes = 632`).
- `python -m unittest discover -s tests -p "*touch*" -q`: `Ran 31 tests in 0.033s — OK`.

## 4. Next Boundary

- Ready for live RAM-only `op=0` check + `op=1` install + physical test on user go-ahead.
