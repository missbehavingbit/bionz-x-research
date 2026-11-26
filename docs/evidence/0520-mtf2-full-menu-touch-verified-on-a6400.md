# E0520 — MTF2 Full-Menu Touch Mod Verified on Live Sony A6400

- **Date:** 2026-11-15
- **Scope:** RAM-only deployment (`op=1`) and live physical verification of the `MTF2` (`tools/native_camera/menu_touch_full.c`) dual-hook touch mod on `ILCE-6400` (`im.elf` PID `149`). No flash writes or permanent firmware modifications were performed.

## 1. Input Identities, Build & Verification

- **Source:** [tools/native_camera/menu_touch_full.c](../../tools/native_camera/menu_touch_full.c), [tools/native_camera/menu_touch_full_trampoline.S](../../tools/native_camera/menu_touch_full_trampoline.S), [tools/research/build_menu_touch_full.py](../../tools/research/build_menu_touch_full.py)
- **Guard binary:** [tools/native_camera/native_menu_touch_full_guard.c](../../tools/native_camera/native_menu_touch_full_guard.c) (`/tmp_bt/native_menu_touch_full_guard`)
- **Compiled payload (`menu_touch_full.elf`):**
  - `code_address`: `0x0700d708`, `code_bytes`: `2128 / 2296` (`0` warnings with `-Wall -Wextra -Werror`)
  - `rel_entry`: `0x700d901` (`mt_full_release`), `prs_entry`: `0x700d845` (`mt_full_press`)
  - `fnv1a_32`: `0xab132df6`, `sha256`: `9552c2767a3c366224f5e5dc16d78d21ff466cabc35dea9145c8b5ae27e94804`
- **Unit tests (`C:\Python314\python.exe -m unittest discover -s tests -p "*touch*" -q`):** `Ran 31 tests in 0.030s — OK`.

## 2. Root Causes Resolved Since v2 Prototype (`E0519`)

1. **Thread-safe async event delivery (`push_async_event`):**
   - `Event::Event(ev, id, ev_type, dest)` (`0x05E14E20` in `libObj-live.so`) requires `ev_type = 4u` and `dest = 1u` (`[ev + 9] == 1`) so `EventManager::push` (`0x05E18D5D`) enqueues into `mgr->queues[1]` and invokes `mgr->notify()`, preventing UI-thread lockups and freezes.
2. **Main-menu row entry (`ROW_ENT`):**
   - `0x150100b2u` mapped to `KEY_DOWN` (`r8 = 1`, Action `2`) in `ViewSettingMenu` State 1 trigger table (`viewUnified2.so` `0x204221`). Replaced with `0x15010052u` (`KEY_ENTER`, `r8 = 5`, Action `1` -> `0x213548` open popup).
3. **Popup option selection + live apply without closing (`POP_SEL`):**
   - `set_popup_row(popup, r)` (`0x15b96f11`) updates the visual highlight (`[popup + 0x648d] = r`) but does not write the 0-based option index `top + r` to `[menu + 0x1fc]`.
   - `MTF2` now writes `*(volatile uint32_t *)(menu + 0x1fcu) = top + r;` and invokes `ViewSettingMenu`'s apply handler `0x157f8ca5u` (`viewUnified2.so` `0x212ca4`), which commits the setting to `BackupManager`/model and refreshes the main menu row while keeping the popup open.
4. **Popup outside tap (`POP_CAN`):**
   - Tapping outside the visible popup widget (`popup + 0x64c`, bounded `80..560 x 32..456`, excluding scrollbar/navigation icons) dispatches `0x15010102u` (`KEY_MENU`), closing only the popup.

## 3. Live Camera Receipt & Verified Behavior

- **Install (`op=1`):** `CHECK op=1 rel=0700d901 prs=0700d845 code_bytes=2128 owned_zero_padding=1 pc_clear=1` -> `INSTALLED hooks=06309d48,06309e54`, `FREEZE threads=175 attached=175 resumed=175 result=0 elapsed_ms=1370`, `END result=0 op=1 errno=0`.
- **Health:** `/proc/uptime` monotonic (`2362.53 8578.59`), `im.elf` PID `149` active.
- **Physical user verification:**
  - Tab switching, page indicator switching, and horizontal swipe paging work cleanly.
  - 1st tap on main-menu row selects row; 2nd tap opens `PAS_MenuListPopup`.
  - Tapping a popup option immediately selects and applies the setting without closing the popup.
  - Vertical swipe inside popup scrolls options.
  - Tapping outside the popup closes only the popup.
  - Camera remained completely stable with zero freezes or touch loss.

## 4. Saved Mod Artifact

- Preserved verified source snapshot under [mods/menu_touch_full/](../../mods/menu_touch_full/).
