# ZV-E10 Touch-Mod Port Blueprint (2026-11-16, offline)

**Goal:** port the a6400 (`ILCE-6400`) `MenuTouchFull`/`MTF2` full-menu touch mod to the **ZV-E10**.
**Status:** static RE complete for all port-checklist items; **no camera has been touched; no firmware modified.**
**Sources:** ZV-E10 2.03 official firmware package (`D:\Downloads\Update_ZVE10V203.exe` → decrypted with fwtool) + full on-device flash dump (`zv-e10-nflasha`, read-only pull via service shell) — see `FINDINGS-ZVE10.md`.
**Working files:** `<workspace>\zv-e10-analysis\viewUnified2_RE-report.md`, `<workspace>\zv-e10-libObj-RE-report.md`, Ghidra project `F:\ghidra\zv_e10_proj` (both `viewUnified2.so` and `libObj.so` imported and analyzed; MCP server config at `F:\ghidra\zv_e10_proj\.mcp.json` / `start_mcp.bat`).

## 0. Framework identity — the mod is portable *in principle*

Same Sony BIONZ-X / CE Linux 8 / ARM Thumb-2 framework, same three libraries, same menu class tree. `PAS_MenuListTab` struct layout is **identical** (verified: `menu+0x18400+0x2ac` = `menu+0x186ac` root-tree pointer — byte-identical semantics to the a6400). All addresses below are **file offsets** (`.text`: file == vaddr; `.data.rel.ro`/`.got`/`.data`/`.bss`: vaddr = file + 0x8000; second ELF segment). Runtime addresses = base + file offset. Load base unknown until boot.

## 1. Verified ZV-E10 ↔ a6400 address map

| Port item | a6400 (FW 2.00) | **ZV-E10 (FW 2.03)** | Confidence |
|---|---|---|---|
| PAS_MenuListTab vtable `_ZTV15PAS_MenuListTab` | `0x15f1f1d0` | **`0xc01e20`** (addr pt `0xc01e28`); RTTI @ `0xc01fe8` → `15PAS_MenuListTab` @ `0xa8b2a0` | High (reconciled; my earlier `0xc19e20` was the same object at Ghidra image base 0x10000) |
| ViewSettingMenu vtable | `0x15EC8270` | **`0xbaabb0`** (addr pt; `_ZTV` `0xbaaba8`, RTTI `0xbaada4`, name `0x8ef14f`) | High |
| ViewBase vtable `_ZTV8ViewBase` | libObj vtable | **`0x1382cf0`** (addr pt; `_ZTV` `0x1382ce8`, RTTI → `_ZTI8ViewBase`, name `8ViewBase`) | High |
| Action dispatcher (enter + move) | TBH `0x21355e` (7-case) | **TBH `0x304136`** (`df e8 11 f0`), entry `0x30412a`, table `0x30413a`, 148 cases, idx75 (`_ZTV+0x134`); called via `ProductActionEi` @ `0x59c76e` → `[this+0x12c]` → default `0x30478c`; **case 1 = execute-selected; cases 2–5 = dirs 1/0/3/2** | High |
| Action1 (execute selected) | `0x2127e8` | **`0x304114`** (case body `0x30426a`) → workers `0x303260` + `0x2f5564` | High |
| `enter_selected` analog | `0x5ac044` | **`PAS_MenuListTab::openLevel2Popup @ 0x86292c`** (called from `0x303260`) | High |
| move handler bodies (dir 1/0/3/2) | `0x211b00` | **`0x300204` / `0x3001f0` / `0x3001b0` / `0x300170`** | High |
| move apply | `0x211a24` | **`0x30010c`** → virtual `[vptr+0x1b4]` (`0x861e06` = idx109; dir TBB, table `0x861e44`) then `0x2f5998` (post-move refresh) | High |
| PAS idx108 (`+0x1b0`, idx108 = 0x1b0/4) | `0x??` | **`0x8629f8`** (dirty-flag clear + refresh) | High |
| PAS list-move primitive | — | **`PAS_MenuListPopup::moveHlt @ 0x88db10`**; `setPopupPrev/Next` `0x861b4e`/`0x861b78` | High |
| getHighlightItemPos | — | **`0x860b36`** | High |
| ViewBase::Action(int) | libObj `+0x3ebef0` | **libObj `0x001393a8`** → `[vptr+0xa0]` (a6400 +0x94; ZV-E10 has 3 extra ViewBase virtuals) | High |
| ViewBase::Main | libObj `+0x3edd4c` | **libObj `0x0013bc80`** (routes `0x0013bb1c`, idx+0x9c) | High |
| DefRootView::ActionCheckerListBase | libObj | `IsReadyExecEv` **`0x0012a260`**, `IsUpdateEvent` **`0x0012a65c`**, `ExecEv` **`0x00129fac`**, `execActionEi` **`0x0012b8bc`**, `execCheckEi` **`0x0012b41c`**, `addChecker` **`0x0012b47b`**, `addActionEi` **`0x0012b912`**, `DispatchEventEm` **`0x001355e4`**, `Initialize` **`0x00129f8e`** (all exported) | High |
| EventManager singleton holder | `0x07089984` | **`g_pEvtMgr`, .bss VA `0x014920e0`** (STT_OBJECT, exported; GOT slot `0x013ded14`, `DT_PLTGOT` `0x013d7c28`; deref helpers `0x0014cff8`/`0x0014d07c`/`0x0012c62c`; `EventManager::push` **`0x008a1794`**; PLT `0x0010c2a8`) | High |
| Event ctor | `0x05E14E20` | **`Event::Event(unsigned long, unsigned char, unsigned char) @ 0x008a01e8`** (+ copy ctor `0x008a021f`; `setParamList` `0x008a0269`) | High |
| operator new | `0x05E15974` | **imported `_Znwj`** (PLT `0x00108e0c`, GOT `0x013d80a8`) — not locally defined | High |
| WidgetSystem singleton | `0x956f0` | **.bss VA `0x0145cb1c`** (unexported; GOT slot `0x013df230` via `sys_getInstance` **`0x00616fe4`**) | High |
| CmnViewSettingNode vtable | CautionConfig | **VA `0x00fab968`** (file `0xfa3968`), RTTI `_ZTI18CmnViewSettingNode` @ `0xfab954`, typeinfo name `0xdd74dc` | High |
| CmnViewSettingNode methods (idx with a6400 mapping) | slot 24/48 | slot 24 `getLayerDepth`, idx46 `setItemSelectedEv`, idx47 `setItemSelectedByIndex`, **idx48 `setItemSelectedBySRNum`** (file `0xb8e60c`), idx49 `setItemSelectedByDiffSRNum`, idx56 `updateSettingNode`, idx64 `updateSRNumEv` (+0x100) | High |
| Widget hit-test / binding / refresh / popup_* / main_state / cancel_popup / confirm_popup / apply_popup | libObj/viewUnified2 | `isHit`≈`Widget::isHit` (TBD), refresh = PAS vtable idx108 `0x8629f8` + `0x2f5998`; popup primitives in viewUnified2.so (exports `PAS_MenuListPopup::moveHlt` etc.) | Partial |
| **Hook candidates (dual-hook design)** | `HOOK_REL`/`HOOK_PRS` WidgetSystem::release/press | **`postMouseRelease @ 0x00628488`** (implementation; `0x0011a128` = PLT stub) and **`postMousePress @ 0x00628420`** (implementation; `0x001189b4` = PLT stub) — same shape as the a6400's hook targets, full prologues, `WidgetSystem` symbols exported in libObj.so | High |
| **RX zero padding (loader home)** | libObj RX pad | **libObj.so: ~45 KB** across 15+ runs ≥ 256 B; largest **16,254 B @ `0x011964ee`**; **viewUnified2.so: ~30 KB**, largest **12,000 B @ `0x00a6ea30`**; **CautionConfig.so: 0 B**. All inside R+X PT_LOADs, same string/parameter-table-padding pattern as the a6400's injection home | High |
| `/proc/<pid>/maps` line names | `im.elf`, `/libObj.so` | Same expected process layout (`im.elf`, `/libObj.so`, `/viewUnified2.so`, `/CautionConfig.so`) — verify live | Medium |
| `/tmp_bt/menu_touch_full.req` transport | a6400 | ZV-E10 `/tmp_bt` existence unverified | Unknown |
| PID `/proc/<pid>/comm` = `im.elf` | 149 | ZV-E10 process name likely `im.elf` too (same app framework) — verify live | High (medium) |
| Hook prologue 12 bytes | `2de9…` | **Will differ** — must be re-derived from the ZV-E10's `WidgetSystem::release`/`press` once found | Unknown |

## 2. Architecture call (what the port must mirror)

The a6400 mod = **dual-hook** (WidgetSystem::press/release) **+** **freestanding Thumb-2 payload** **+** **owned zero RX padding in libObj.so** **+** **guarded installer** (freeze all tids via ptrace, verify PCs, write, verify, rollback on failure) **+** **state block in owned zero RW padding**.

ZV-E10 deltas, all verified against the firmware:

1. **The whole Action dispatch is one 148-case TBH at slot idx75**, reached as `ViewBase::Action → [vptr+0xa0] → viewUnified2.so ProductActionEi (idx40) → [this+0x12c]` (i.e. `_ZTV+0x134`). There is **no small TBH**; movement = cases 2–5 with dirs 1/0/3/2 into shared move apply `0x30010c`.
2. **Framework Action/DefRootView machinery lives in libObj.so** (not viewUnified2.so); viewUnified2.so only imports it via ABS32/PLT.
3. **CmnViewSettingNode lives in CautionConfig.so** (its vtable, slot 48 `setItemSelectedBySRNum`, idx56 `updateSettingNode` etc.) — viewUnified2.so/CautionConfig seam confirmed; the PAS_* layer imports it.
4. **Event seam is libObj↔viewUnified2** (`Event::Event(ulong,u8,u8)`, `EventManager::push`, `g_pEvtMgr`) — import/export symmetry verified.
5. **Executable RX zero padding exists** across `libObj.so` (~45 KB, largest 16,254 B @ `0x011964ee`) and `viewUnified2.so` (~30 KB) — same `.rodata` table-padding pattern the a6400 exploited. `CautionConfig.so` has none. The injection home is viable; correctness cannot be verified without live testing (none of the zeros can be proven dead offline).
6. **Struct offsets match** (`menu+0x186ac` confirmed); tab/page/row hit-test regions should be verified against a live menu session, since they depend on initialized geometry (stride `0x1118` rows etc. may match, may not).

## 3. Honest effort estimate

See `FINDINGS-ZVE10.md` §10 / the spec in the same directory. In short:
- **Best case** (same Sony layout family, minor shifts — exactly what we observed): the static RE is done; remaining is recompilation of the payload with the ZV-E10 addresses + verification.
- **Realistic:** weeks, because the installer must be re-derived against the ZV-E10's live `im.elf`, and every hook candidate (`WidgetSystem::release`/`press`, prologue bytes) must be re-derived from the ZV-E10's own libraries — the two hook functions have not been located yet in the ZV-E10 build and the prologue bytes differ. Also the "Mods tab" UX design (7th tab) must be rethought for the ZV-E10's vlog-focused menu.
- The **live phase** (body-specific debugger deployment, dozens of crash cycles) is out of scope for this blueprint and requires explicit per-incident user authorization plus a separately reviewed ZV-E10 procedure; the ILCE-6400 debugger does NOT transfer (different kernel, different USB descriptor surface per `E0506/E0508/E0509`).

## 4. Blueprint to action (if the user wants to proceed)

1. Port `menu_touch_full.c` + trampoline against ZV-E10 symbols; rebuild with `-DMT_SYSTEM_ADDR=…` etc. (all the `0x15b…`/`0x630…`/`0x956f0` constants replaced per §1).
2. Provide the payload a state block in a loader-owned RW segment (Sony library `.bss` is not ours to borrow without proven dead regions).
3. Installer (`native_menu_touch_full_guard`-style) rewritten against the ZV-E10's live map (`im.elf`, pid discovery, `/proc/<pid>/maps`-derived libObj/viewUnified2/CautionConfig bases + `.so` identity checks).
4. **Before any live step: explicit user GO per incident**, full receipts, `op=0` check-only first, then install, then removal proof — same discipline as the a6400 sessions.

## 5. Artifacts

- `<workspace>\zv-e10-analysis\viewUnified2_RE-report.md` (294 lines, full evidence)
- `<workspace>\zv-e10-flasha15\zv-e10-libObj-re-report.md` (41 KB, full evidence)
- `F:\ghidra\zv_e10_proj\` — Ghidra project (libObj.so import analyzed in background; MCP server config at `F:\ghidra\zv_e10_proj\.mcp.json`; start script `start_mcp.bat`)
- `FINDINGS-ZVE10.md` — dump/extraction + boot + emulator record
