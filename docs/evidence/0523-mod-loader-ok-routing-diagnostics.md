# E0523 — Mod Loader OK-key routing diagnostics (tab 7 "Mods")

Date: 2026-11-15. Camera: ILCE-6400 (physical, MSC debugger transport). Scope: user-authorized RAM-only
Mod Loader installs on fresh boots; all other camera access read-only.

## Question

Why pressing OK on the injected `MenuTouchFull` row in MENU tab 7 never reaches the Mod Loader
toggle path.

## Runs (each on a fresh boot, guard `check` then `install`, all 175 `im.elf` threads resumed)

| Run | Third hook | Payload sha256 | Result |
|---|---|---|---|
| A | `0x157f955e` (`+0x21355e`, TBH dispatcher), logs only when `last_menu+0x1f0 == 7` | `20e172f5…` (`artifacts/hardware/mod_loader_diag_157f955e/`) | One tab-7 entry: `act 2`, `v158 0`, `m1f8 -1`. |
| B | `0x15b92044` (`+0x5ac044`, `enter_selected`), logs every call (`ML_DIAG_ENTER`) | `7653feb8…` (`artifacts/hardware/mod_loader_diag_enter/`) | No log file created: `enter_selected` never called. User confirmed the row had the cursor. |
| C | `0x157f955e`, logs every action (cap 120), cached tab, `view+0x14c`; on `act==1` also calls `0x207c4a(view)` (`ML_HOOK_DISPATCH`) | `8acc262d…` (`artifacts/hardware/mod_loader_diag_dispatch/`) | Log receipt `mod_loader-run1.log` (sha256 `3550ecd5…`). |

Run C, user procedure: OK on a normal item in tab 1, back out; then OK on `MenuTouchFull` in tab 7.

- Tab 1: `… act 2, act 3, act 1 (d_sel 0x0b26f798), act 6 (v14c 2), act 3, act 5`.
- Tab 7: idle/redraw actions (`0x1a`, `0x1c`, `0x23`, `0x24`, `0x82`, `0x83`, `0x87`, `0x0c`) and a final
  `act 2`. **No `act 1` was dispatched for the OK press.** 49 entries, below the 120 cap.

## Static support (live `viewUnified2.so`, sha256 `fa534aa2…`, load base `0x155e6000`)

- TBH at `+0x21356a`, table `+0x21356e`: index 1 → `0x213548` → Action1 `0x2127e8`;
  indices 2/3/4/5 → `0x211ba4/0x211b90/0x211b7a/0x211b64` → move handler `0x211b00` with dir 1/0/3/2
  (all gated by `view+0x1b8 == 0`).
- Action1 calls `enter_selected` `0x5ac044` only if `view+0x14c ∈ {1,2}`, the `0xa056f604` widget exists,
  and `0x207c4a(view)` (three chained node `vtable+0x28` calls from `view+0x1a0`) is non-null.
- Move apply `0x211a24` updates selection through node `vtable+0xc0` and reads through `vtable+0x28`/`+0x24`.
  ~~In the fake-node vtable slot 48 is a no-op~~ — **superseded**: the set_root hook copies slots 0–64 from the
  real root vtable, so slots 4–22, 24–31, 33–34, 36–39, 41–64 are the real `CmnViewSettingNode` methods
  (`CautionConfig.so`, live bytes equal to firmware file; slot 48 = `setItemSelectedBySRNum`). Field meaning from
  those methods: `+0x14` layer depth, `+0x18` selected index (1-based), `+0x1c` SR number, `+0x20` index,
  `+0x24` state (2/4/6 = selected). Symbol/slot map: `artifacts/hardware/mod_loader_diag_dispatch/cmnviewsettingnode-syms.txt`.

### Live node comparison (read-only, same boot as Run C)

| Node | depth `+0x14` | sel `+0x18` | sr | idx | state |
|---|---|---|---|---|---|
| real tab 1 / page 1 / item 1 | 2 / 3 / 4 | 1 / 1 / 1 | 1 | 1 | 1 / 2 / 2 |
| real tab 6 / page 6 / leaf item 1 | 2 / 3 / 0 | 6 / 1 / -1 | 1 | 1 | 1 / 2 / 2 |
| fake tab 7 / page 1 / item 1 | 2 / **2** / 0 | 1 / 1 / **0** | 1 | 1 | 2 / 2 / 2 |

## Conclusions

- Observation: OK in tab 7 is filtered **before** the ViewSettingMenu action dispatcher; neither
  `0x21355e` act 1 nor `0x5ac044` runs. Hooking either point cannot toggle mods as-is.
- ~~Inference: fake page depth 2 makes native code treat row focus as tab-bar focus~~ — **falsified by Run D**.
  The upstream key→action mapping has not been traced.
- Run A's earlier reading "`act 2` = tab-bar navigation" is refined: acts 2–5 are directional moves.

## Run D — depth fix (falsified)

Fresh boot, `artifacts/hardware/mod_loader_diag_dispatch_depth3/` (payload `bd883a8b…`, install `.req`
`c03d0ac9…`), `INSTALLED hooks=15b91654,15b91a80,157f955e`, 175 threads resumed. Live read after the run:
fake tab 7 depth 2 / page depth 3 / item depth 0, item `sel -1`, item state 2 (fix in effect).
User pressed OK on `MenuTouchFull` in tab 7. Log receipt `mod_loader-run1.log` (sha256 `e42d7c77…`):
tab 7 again ends at `act 2`; **no `act 1`**. The layer-depth / leaf-`sel` mismatch is not the cause.

## Upstream trace (static, partial)

- `ViewBase::Action(int)` (libObj `+0x3ebef0`, live base `0x05d18000`) only forwards to vtable `+0x94`
  (ProductAction → `+0x100` = `0x21355e`). It is called by the generic `View` engine; `ViewBase::Main`
  (`+0x3edd4c`) and helper `+0x3edc04` route through `DefRootView::ActionCheckerListBase`
  (`IsReadyExec`/`IsUpdateEvent`) and `View::pushEvent`. `transformByTriggerTable` only maps raw event IDs
  through the view's trigger-table function; it carries no per-row condition.
- `ViewSettingMenu` vtable (file `0x8da270`): `+0x100` action dispatcher `0x21355e`, `+0x104` = `0x213418`,
  a 15-way switch on `[r0+4]` whose targets clear/set view flags (`+0x1b6`, `+0x290`, `+0x36f`) and call
  `SetHighlightPos` / `0x207c4a` — consistent with per-state entry handlers, **not** guards.
- `ViewBaseProduct::ProductMain` (`0x2f2df4`) handles key events `0xf03..0xf0e` itself (e.g. →
  `UpdateHighlightPos(dir)`), before any state-machine action.
- Not yet found: the table/condition that maps the OK event to action 1 in the current view state.
  Proposed next step: log the `ViewSettingMenu` instance pointer, then diff read-only snapshots of the view
  object with the cursor on a real tab-1 row versus the `MenuTouchFull` row to find the differing state field.

## Run E — in-camera post-action view dumps

Host-side snapshots are invalid: any debugger USB exchange closes the camera menu (user observation), so
reads after navigation hit a closed/freed view. Run E (fresh boot, `artifacts/hardware/mod_loader_diag_dispatch_dump/`,
payload `cc1d217a…`, install `.req` `1c6564da…`) calls the original dispatcher as a subroutine and, for acts 1–5,
writes `view[0..0x300) + menu[0x1d0..0x210)` to `/tmp_bt/mods/vNN.bin` while the menu is open. One menu
session, same view instance `0x1a2acef0`. Receipts: `mod_loader-run1.log` (`b1d3edd2…`), `v0e.bin` (`1a97e71f…`),
`v0f.bin` (`a751d3ee…`), `v2e.bin` (`2539d6ef…`).

- Widget navigation helpers (`0x5ab240` up / `0x5ab274` down) show `menu+0x1d8 = 1, row = -1` is tab-bar
  focus and `0x1d8 = 0, row = 0` is list row 0. Act 2 = down, act 3 = up (dirs 1/0).
- Tab 7 after act 2: `0x1d8 = 0`, `row = 0` (native list focus on `MenuTouchFull`, consistent with the cursor).
- **`view+0x18c` = 3 in tab 1, 0 in tab 7.** Writer: act `0x1a` handler `0x212f68` stores `0x206f28(view)`,
  which reads int property 19 of the current tab node (`root->getSubItemBySRNum(view+0x150)`) and maps
  1→1, 3→3, 0/2/4→0, other→3. The Mods tab forwards int properties to `orig_tab5` (real 6th tab, My Menu),
  so tab 7 is classified like My Menu (kind 0), whose handlers use `PAS_MyMenuListTab`.

~~Fix: `node_get_int_prop` returns 3 for prop 19 on the Mods tab~~ — **disproven by Run F/G, reverted**. Built
`artifacts/hardware/mod_loader_diag_dispatch_kind3/` (payload `2534b989…`) and
`artifacts/hardware/mod_loader_toggle_kind3/` (payload `a50bcdd0…`, 2996 B). Not yet installed.

## Run F — toggle build with prop 19 = 3

Fresh boot, `artifacts/hardware/mod_loader_toggle_kind3/` (payload `a50bcdd0…`, install `.req` `a1bc35a6…`),
`INSTALLED hooks=15b91654,15b91a80,15b92044`. OK on `MenuTouchFull`: no visible change, no `mod_loader.log`
(`enter_selected` not called).

## Run G — dump build with prop 19 = 3, scripted keys

Fresh boot, `artifacts/hardware/mod_loader_diag_dispatch_kind3/` (payload `2534b989…`, install `.req` `99392d03…`).
User sequence in one menu session: tab bar → tab 1 → down → up → left to tab 7 → down → OK → exit.
Receipts: `mod_loader-run1.log` (`9b3476e7…`), `v2a.bin` tab 1 after down (`aee1b702…`), `v2b.bin` tab 1 after up
(`5e141262…`), `v37.bin` tab 7 after down (`6c65641f…`).

- After OK in tab 7: **no `act 1`** (log ends at `act 2`, seq `0x37`).
- `view+0x18c`: tab 1 = **0**, tab 7 = 3. The Run E value 3 for tab 1 was the constructor default (`0x2109c0`)
  before any `act 0x1a`; the "My Menu kind" inference is wrong. Prop-19 override reverted in source.
- Tab 1 row vs tab 7 row, same view instance: identical focus state (`menu+0x1d8 = 0`, `row 0`, `view+0x10 = 2`).
  Remaining differences: tab/page/item counts and `menu+0x1fc` (tab 1 = 0, tab 7 = -2 in both bar and row
  focus). `menu+0x1fc` is written by popup helpers (`0x5ab34c`/`0x5ab376`, from `PAS_MenuListPopup` at
  `menu+0xfb20`) and by `0x5aa1e0`/`0x5ab0d6`/`0x5ab560`; its consumer for OK is not identified.

## Run H — ProductMain event log; OK gate located

Fresh boot, `artifacts/hardware/mod_loader_diag_pmain/` (`ML_HOOK_PMAIN`, hook `0x158d8df4` =
`ViewBaseProduct::ProductMain`, confirmed as `ViewSettingMenu` vtable `+0x90` via relocation `0x8e2300`;
payload `50e04287…`, guard `9fd2fccf…`). Logs `ViewBase::GetEventId` (via viewUnified2 PLT `0x157397b8`) only
for objects with vptr `0x15ec8270`. Receipt `mod_loader-run1.log` (`29850cab…`).

- Tab 1: down `0x01`, OK `0x05`, back `0x06`, up `0x02`, left `0x04`. Tab 7: down `0x01`, **OK `0x05` arrives**.
- `ProductMain` → view `vt+0xf4` = `0x20e114`: generated state table (switch on `[this+4]` state 0–14, then
  event; guards `0x20de0a(this, cond)`; outputs `*r1` next state, `*r2` action). State 1 / event 5:
  cond 0 → st 12 act 43; cond 1 → st 13; cond 3 → act 10; **cond 4 → act 1**; cond 10 → st 3; cond 26 → st 5 …
- Cond 4 (`0x20c95c`): `sel = 0x207c4a(view)`; `v = -1; sel->getIntProperty(12, &v)`; `sel->vt[0xa0](0) == 0`;
  `0x2061b0(view)`; **requires `v == 0`** and `getCountSelectable(sel) > 1`, then widget/greyout checks.
  Cond 3 is the `prop 12 == 4` path. Mods items never wrote prop 12 (`v` stayed -1) and had 0 children →
  cond 4 false → no act 1. This explains Runs A–G.

Fix (source, default build): Mods items return prop 12 = 0 and have two shared dummy option children
(`opt_nodes`), depth 4, `sel 1`; redundant `enter_leaf` log removed for size. Built
`artifacts/hardware/mod_loader_toggle_opt/` (payload `180b1a67…`, 3100/3120 B, install `.req` `6297340f…`).
Not yet installed.

## Next boundary

Fix applied in source: fake page depth 3, fake leaf `sel = -1`. Built
`artifacts/hardware/mod_loader_diag_dispatch_depth3/` (payload `bd883a8b…`) and
`artifacts/hardware/mod_loader_toggle_depth3/` (payload `a3d9f5b3…`, 2984 B). Run D: no effect.
Next: trace the upstream OK→action mapping (ViewBase::Action `0x06103ef0` / ProductAction slot+0x94) to find
the condition that suppresses OK in tab 7.

## Source changes

Default build: fake page `layer_depth` (was `unk14`) 2 → 3, fake leaf `selected_idx` 0 → -1.
Opt-in: `ML_DIAG_ENTER` and `ML_HOOK_DISPATCH` in `tools/native_camera/mod_loader.c`,
`mod_loader_trampoline.S`, `native_mod_loader_guard.c`; build scripts
`scratch/build_mod_loader_diag_enter_wsl.sh`, `scratch/build_mod_loader_diag_dispatch_wsl.sh`.
`build_mod_loader.py` still writes `open_item 0x15b92044` into manifests; the dispatch manifest was
corrected by hand. Note: on-disk default sources (0x15b92044 toggle) do not correspond to the Run A artifacts.

## Run I — option-item toggle: OK works, touch inactive

Fresh boot, `artifacts/hardware/mod_loader_toggle_opt/` (payload `180b1a67…`, install `.req` `6297340f…`),
`INSTALLED hooks=15b91654,15b91a80,15b92044`. User: OK on `MenuTouchFull` toggles ON/OFF; menu touch does not work.
Log receipt `mod_loader-run1.log` (`6c0af3c0…`): `enter_idx 0` / `mod_on` / `mod_off` / `mod_on 0700d708`.

- Live (read-only): MTF2 hooks present at `0x06309d48`/`0x06309e54` (`dff804f0 00bf00bf 01d90007` / `…45d80007`),
  code at `0x0700d708` equals the `.mod` payload (fnv `0xab132df6`, sha256 `9552c276…`, same as E0520).
- **State block `0x07143920` is all zero.** MTF2 `mt_full_press`/`mt_full_release` pass through unless
  `magic == "MTF2"`, `version == 2` and `enabled`; in E0520 its native guard wrote `{MTF2, 2, 2, 0, 0, 16}` first.
  Mod Loader zero-filled the state, so the mod was hooked but inert.

Fix: SMOD `flags` bit 0 (`SMOD_FLAG_STATE_INIT`) = `state_size` bytes of initial state follow the code; the loader
reads them into the state block instead of zero-filling (`ModEntry.pad` carries the flag; `skip_invalid` log
dropped for size). `pack_mod.build_menu_touch_full_mod` now appends the E0520 header padded to 632 B.
Artifacts: loader `artifacts/hardware/mod_loader_toggle_stateinit/` (payload `18ad68a0…`, 3112/3120 B, install
`.req` `28386230…`); mod `artifacts/hardware/mods/MenuTouchFull.mod` (`9584fdfd…`, MTF2 code bytes unchanged);
previous mod preserved as `MenuTouchFull-v1-zero-state.mod` (`b4b67176…`). Not yet installed.

Process note: an intermediate scripted edit truncated the untracked `tests/test_mod_loader.py`; it was restored
from the session copy and extended (`test_pack_smod_state_init`); `tests.test_mod_loader` 4/4 OK.

## Run J — state-init loader: state correct, MTF2 still never runs

Fresh boot, `artifacts/hardware/mod_loader_toggle_stateinit/` (payload `18ad68a0…`, install `.req` `28386230…`),
mod `MenuTouchFull.mod` (`9584fdfd…`). Log: `enter_idx 0` / `mod_on` / `mod_off` / `mod_on`. User: touch does not work.
Live read: state `0x07143920` = `MTF2, 2, 2, 0, 0, 16` (init applied); hooks present; but `count`, `press_valid`,
`last_err`, `inflight` all 0 → MTF2 press/release logic never executed after the in-process hook write.

Platform (read-only): `Linux 3.0.27_nl-rt106+ SMP PREEMPT RT`, 4× Cortex-A5 (`CPU part 0xc05`), CPUs `0-3` online.
Inference (unverified): the `cacheflush` syscall (`0xf0002`) issued from the UI thread maintains only that CPU; touch
handling on another core keeps executing cached original prologues at `0x06309d48`/`0x06309e54`. (The E0520 guard
wrote via `/proc/149/mem` under ptrace, a different kernel path.)

Fix: `ml_flush_all(beg, end)` pins the calling thread to each CPU 0–3 in turn (`sched_setaffinity` 241) and issues
`cacheflush` there, then restores the original mask (`sched_getaffinity` 242); used for all code/hook writes in
`ml_toggle_mod`. Toggle error logs merged into `tgl_err <code>` (1 bad hook meta, 2 orig mismatch, 3 open, 4 mprotect
code, 5 read, 6 crc, 7 mprotect hook). Built `artifacts/hardware/mod_loader_toggle_flushall/` (payload `36d25243…`,
3056/3120 B, install `.req` `6de22f1c…`). Not yet installed.

## Run K — per-CPU flush: MTF2 hooks now execute; 6-tab limit found

Fresh boot, `artifacts/hardware/mod_loader_toggle_flushall/` (payload `36d25243…`, install `.req` `6de22f1c…`),
mod `9584fdfd…`. Log `enter_idx 0` / `mod_on`. User: touch still inactive; asked whether the injected 7th tab affects
the other tabs. Live state after touches: `press_x = 438`, `press_y = 298` written by `mt_full_press`, `press_valid`
cleared by `mt_full_release` → **hooks now execute** (supports the per-CPU flush inference); `count = 0`, `last_err = 0`.

Cause: `handle_menu_touch` returns early when `root.child_count - 1 > 5` (`MT_MAX_TABS = 6`, `cmp r2, #5` at
`0x0700dc64`). With Mod Loader's injected tab the root always reports 7, so MTF2 ignored touches in **every** tab.
(Tab 7 itself is still rejected by MTF2's real-vtable checks on tab/page nodes; acceptable, its content is dynamic.)

Fix: `pack_mod.patch_mtf2_max_tabs` changes only that immediate (`2a05` → `2a06`) after verifying 10 context bytes;
FNV recomputed. New `MenuTouchFull.mod` `3f8ac4fb…` (exactly one byte differs from the E0520 code, asserted by test);
previous mod kept as `MenuTouchFull-v2-stateinit-6tabs.mod` (`9584fdfd…`). `tests.test_mod_loader` 5/5 OK.
Not yet installed (the loader caches the mod FNV from its single scan, so a reboot is required).

## Run L — Mod Loader + MTF2 (7-tab patch): menu touch verified

Fresh boot. Loader `artifacts/hardware/mod_loader_toggle_flushall/` (payload `36d25243…`, install `.req` `6de22f1c…`,
`INSTALLED hooks=15b91654,15b91a80,15b92044`, 175 threads resumed); mod `MenuTouchFull.mod` `3f8ac4fb…`.
User toggled `MenuTouchFull` ON via MENU → tab 7 → OK and reported: **touch works** in the ordinary tabs; it does not
work in My Menu (tab 6), in Mods (tab 7), or while the cursor is on the tab bar.

Receipts: `mod_loader-run-touch-ok.log` (`a48bc061…`: `enter_idx 0`, `mod_on 0700d708`), MTF2 state dump
`mtf2-state-touch-ok.bin` (`375e390d…`): `count 29`, `last_err 0`, `inflight 0`; recorded events include
`MT_ACT_TAB` targets 1–6 and `MT_ACT_ROW_SELECT`. Camera responsive (uptime 246 s).

Remaining limits (observations + code reading, not new defects):
- Tab bar focus: MTF2 returns when `menu+0x1d8 != 0` (unchanged E0520 behaviour).
- Mods tab: fake nodes fail MTF2's real `CmnViewSettingNode` vtable checks; intended (dynamic content).
- My Menu: not handled by MTF2 (inference: My Menu uses a different node/list path, cf. `PAS_MyMenuListTab`
  in `ViewSettingMenu` handlers); not verified.

Superseded conclusions in this record: "slot 48 no-op", "page depth", "tab kind / prop 19". Final causes:
(1) item prop 12 / option-count gate in state-table cond 4, (2) zero-filled MTF2 state, (3) single-CPU `cacheflush`
on a 4-core SMP kernel, (4) MTF2 6-tab limit.
