# ZV-E10 `viewUnified2.so` — reverse-engineering report

**Target:** `<workspace>\zv-e10-flasha15\lib\viewUnified2.so` (14,284,732 bytes)
**Framework:** Sony BIONZ-X / CE Linux, ZV-E10 firmware 2.03, ET_DYN, `EM_ARM`, ARM **Thumb-2** (all code seen is Thumb; the library also contains ARM stubs in its PLT). Not PIE-relocated (file bytes are the pre-relocation addends; `ET_DYN` + `.rel.dyn` `R_ARM_RELATIVE` addends are stored in the slots — the file has **no** `TEXTREL`, and all code-side absolute references are PC-relative).

Reference point (from the ILCE-6400 background material, used only as orientation — **not** as evidence about ZV-E10 bytes): the ILCE-6400 offsets (vtable `0x8da270`, TBH `0x21355e`, enter_selected `0x5ac044`, ViewBase::Action `0x3ebef0`, Main `0x3edd4c`, ActionCheckerListBase `~0x3edc04`, move apply `0x211a24`) live in ILCE-6400's **`libObj.so`** (15,923,100 bytes), not in its 285,696-byte viewUnified2.so. **None of those file offsets is valid for the ZV-E10 library** (verified by disassembly — see §5).

---

## 0. File map and offset arithmetic (read this first)

Section layout (`sh_addr == sh_offset` up to `.ARM.exidx`; everything from `.init_array` on differs by **0x8000**):

| section | file off | vaddr | size | flags |
|---|---|---|---|---|
| `.gnu.hash` | 0xd4 | 0xd4 | 0x4d68 | A |
| `.dynsym` | 0x4e3c | 0x4e3c | 0x10550 | A |
| `.dynstr` | 0x1538c | 0x1538c | 0x2fe9f | A |
| `.rel.dyn` | 0x47318 | 0x47318 | 0x1393a8 | A |
| `.rel.plt` | 0x1806c0 | 0x1806c0 | 0x5608 | A |
| `.init` | 0x185cc8 | 0x185cc8 | 0xa | WA/X |
| `.plt` | 0x185cd4 | 0x185cd4 | 0x91f0 | X |
| `.text` | 0x18eec8 | 0x18eec8 | 0x74af6c | X |
| `.rodata` | 0x8d9e40 | 0x8d9e40 | 0x1e8745 | A |
| `.ARM.extab` | 0xac2588 | 0xac2588 | 0x706c0 | A |
| `.ARM.exidx` | 0xb32c48 | 0xb32c48 | 0x45ea8 | A |
| `.init_array` | 0xb78af0 | 0xb80af0 | 0x1ba0 | WA |
| `.data.rel.ro` | 0xb7a698 | 0xb82698 | 0x97c20 | WA |
| `.got` | 0xc123b0 | 0xc1a3b0 | 0xd758 | WA |
| `.data` | 0xc1fb08 | 0xc27b08 | 0x17f724 | WA |
| `.bss` | 0xd9f22c | 0xda7230 | 0xa88e0 | WA |

Segments: `seg1 PT_LOAD` file `[0, 0xb78af0)` → vaddr `[0, 0xb78af0)` (vaddr == file); `seg2 PT_LOAD` file `[0xb78af0, 0xda7e2c)` → vaddr `[0xb80af0, …)` (**vaddr == file + 0x8000**). Gap vaddr `[0xb78af0, 0xb80af0)` unmapped.

**Offset → “loaded base + X” mapping (no runtime addresses are claimed anywhere in this report):**

- All `.text`/`.plt`/`.rodata` offsets below the second segment boundary: `file == vaddr`, so `loaded = base + file`.
- All `.init_array`/`.data.rel.ro`/`.got`/`.data` items: `vaddr = file + 0x8000`, so the slot’s stored addend + `base` is the loaded address of the **target**, and `loaded_slot = base + vaddr = base + file + 0x8000`.
- `.rel.dyn` relocation `r_offset` values are **vaddrs** (use `file = r_offset` for r_offset < 0xb78af0, else `file = r_offset − 0x8000`).
- `.rel.dyn` `R_ARM_RELATIVE` (type 23) 160373 total relocations (types: 23 ×76065, 2 `R_ARM_ABS32` ×83782, 21 `R_ARM_GLOB_DAT` ×526). For SHT_REL, the **addend is stored in the slot itself** (verified). Slot of kind ABS32+symbol (e.g. imported framework methods) = an *undefined* framework import (`SHN_UNDEF`).

Verification method for every vtable/typeinfo word below: dump `.data.rel.ro` file bytes AND cross-check with the `.rel.dyn` relocation list (all relocs dumped to `relocs.txt` during the analysis).

---

## 1. ViewSettingMenu vtable — **LOCATED (high confidence)**

The `ViewSettingMenu` class **is defined in this library** (its RTTI, vtable, address point and a secondary fragment are all present). Its base `_ZTI13ViewBaseForMR` (`ViewBaseForMR`) is **imported** (`SHN_UNDEF`, reloc type ABS32 at the typeinfo’s base-type word), as expected — the class derives single, public, non-virtual from an imported base.

**Primary vtable `_ZTV15ViewSettingMenu`**

- _ZTV symbol address (offset-to-top slot): vaddr **`0xbaaba8`** → file **`0xba2ba8`** (`.data.rel.ro`).
  - slot 0 `0xbaaba8`: offset-to-top = `0x00000000` (no relocation)
  - slot +4 `0xbaabac`: RTTI → `rel+0xbaada4` (`.rel.dyn` entry `r_offset=0xbaabac type=23 addend=0xbaada4`)
  - **address point** (value stored in object `vptr`s) = `0xbaabb0` → file **`0xba2bb0`** (first virtual slot).
- `_ZTV+0x100` (i.e. byte offset 0x100 from _ZTV start; slot index 62; file `0xba2ca8`): bytes `17 dd 2e 00` = `rel+0x2edd17` → Thunk@`0x2edd16` → `b.w 0x18dc78` (PLT) → GOT slot `0xc1c958` → JUMP_SLOT → `_ZN17CheckFocusAreaInh10startCacheEv` (local, `0x597380`, two cache bytes := 1).
- `_ZTV+0x104` (file `0xba2cac`): bytes `db dc 2e 00` = `rel+0x2edcdb` → Thunk@`0x2edcda` → `b.w 0x18dc78` → same `startCacheEv` target.

Note: the `_ZTV+0x100/+0x104` slots in this build are **cache-lifecycle shims**, not the Action dispatcher (see §2). The ILCE-6400 `+0x100 = Action dispatcher` layout does **not** carry over.

**Typeinfo `_ZTI15ViewSettingMenu`** at file/vaddr **`0xba2da4`** (vaddr `0xbaada4`, `.data.rel.ro`)

```
file 0xba2da4: 08 00 00 00  — vtable ptr: R_ARM_ABS32 +8 → _ZTVN10__cxxabiv120__si_class_type_infoE (imported, sym 301)
file 0xba2da8: 4f f1 8e 00  — rel+0x008ef14f → "15ViewSettingMenu\0" (.rodata, file 0x8ef14f)
file 0xba2dac: 00 00 00 00  — R_ARM_ABS32 → _ZTI13ViewBaseForMR (imported)
```

Itanium name string `.rodata` **`0x8ef14f`** = `"15ViewSettingMenu\0"` (preceded by `00 01 31` and followed by `00 00 00 00`). `_ZTI13ViewBaseForMR` typeinfo does **not** exist here (imported).

**Secondary / construction vtable fragment** at vaddr `0xbaad54` → file **`0xba2d54`**: offset-to-top = `0xffffffd8` (−40), RTTI → `rel+0xbaada4` (same ViewSettingMenu typeinfo), first override `rel+0x2edc89`; last slot `rel+0x2eec8d` at `0xbaada0`; ends immediately before the typeinfo object (`0xbaada4`). This is the `ViewSettingMenu`-as-base-subobject fragment used when a derived class (in some other library or elsewhere in this image) embeds `ViewSettingMenu` at offset +40. No relocation sites point at it in this file (construction vtables are consumed by derived-class code that loads them from GOT/literals; derived class not identified).

**Exported helper reference**

- `.got` slot `0xc1cf38` (file `0xc14f38`): bytes `a8 ab ba 00` = `0xbaaba8` = pointer to `_ZTV15ViewSettingMenu` (`.rel.dyn` entry `r_offset=0xc1cf38 type=23 addend=0xbaaba8`).
- Exported factory symbols both local-defined: `ViewSettingMenuToInstance` (sym 3450, `0x2f36f4`), `ViewGlobalMenuToInstance` (sym 3345, `0x2e4c0e`) — not needed for these items.

**How it maps to loaded base:** `_ZTV = base + 0xbaaba8`, address point `base + 0xbaabb0`, typeinfo `base + 0xbaada4`, fragment `base + 0xbaad54`, GOT slot `base + 0xc1cf38` (all `vaddr`s; `file = vaddr − 0x8000`). Name string `base + 0x8ef14f`.

---

## 2. TBH dispatcher (Action dispatcher) — **LOCATED, as the 148-case `Action` TBH at 0x30412a (high structural confidence; exact virtual-slot name unverifiable, medium confidence)**

**There is no small (≈7-case) TBH Action dispatcher anywhere in this file.** The framework `ViewBase::Action(int)` itself is **imported** (`SHN_UNDEF`, sym 1078 `_ZN8ViewBase6ActionEi`); `ViewBaseProduct::ProductActionEi` is also imported (sym 1078-family, ABS32 in our vtable). `ViewSettingMenu` does **not** override the Action virtual — idx5 (`0xbaabc4`) is an ABS32 relocation to the imported symbol, as is idx40 (`0xbaac50`, `_ZN15ViewBaseProduct13ProductActionEi`). The small-TBH population survey found 308 TBHs / ~1114 TBBs in `.text` — all are unrelated key handlers / event LUTs / per-screen switches (e.g. `0x599874` is a key-code dispatcher comparing `0x6a`, `0x1b`, `0xd`…; `0x49005c` calls `viewManagerIf::openView` + `WrapperSettingUtil::getExposureMode`).

**The ZV-E10 equivalent of the ILCE-6400 "Action dispatcher" is the 148-case TBH at vtable slot idx75:**

- Slot: bytes `2b 41 30 00` at file `0xba2cdc` (vaddr `0xbaacdc`, `.data.rel.ro`) = `rel+0x30412b` → code at **file `0x30412a`** (vaddr `0x30412a`).
  - idx75 from address point (`0xbaabb0 + 4*75 = 0xbaacdc`); equivalently `_ZTV+0x134` (`0xbaaba8+0x134`); parent should note the ILCE-6400 `+0x100` slot numbering does not transfer.
- Function prologue: `0x30412a: push {r4, r5, r7, lr}; mov r4, r0; add r7, sp, #0; cmp r1, #0x93; bhi.w #0x30478c; tbh [pc, r1, lsl #1]`
  - **TBH bytes at file `0x304136`: `df e8 11 f0`** (`TBH [pc, r1, lsl #1]`); **jump table at file `0x30413a`** (right after the instruction), 148 halfword entries starting `94 00 98 00 9c 00 a0 00 a4 00 a8 00 …` (case targets = table_base + 2×entry); **default (bhi) target `0x30478c`** = `pop {r4, r5, r7, pc}` → out-of-range action IDs are ignored and r0 is returned unchanged. `r1` = action ID (valid range 0..147), `r0` = `this`.
  - Each case body is `pop.w {r4, r5, r7, lr}; b.w #handler` (8-byte tail-call thunks), e.g. case 1 body at `0x30426a` = `bd e8 b0 40 ff f7 51 bf` → `b.w #0x304114`.
- Case mapping (decoded via capstone + custom table decoder, `table_base = 0x30413a`):
  - **case 0** → `0x304262` → `b.w 0x301750`
  - **case 1** → `0x30426a` → `b.w **0x304114**`  ← the “Action1 / enter selected” case
  - **case 2** → `0x304272` → `b.w **0x300204**` (dir **1**)
  - **case 3** → `0x30427a` → `b.w **0x3001f0**` (dir **0**)
  - **case 4** → `0x304282` → `b.w **0x3001b0**` (dir **3**)
  - **case 5** → `0x30428a` → `b.w **0x300170**` (dir **2**)
  - **case 6** → `0x304292` → `b.w `0x2f43f4`` (mode 2→1 transition path)
  - **case 7** → `0x30429a` → `b.w `0x2f4420`` (writes 0x20 / 0x22 via `0x190c70` helper)
- The movement case bodies write `r1` = direction exactly as ILCE-6400 reports:
  - `0x300204`: `ldrb.w r3, [r0, #0x1d0]; cbnz r3, #0x300218; movs r1, #1; …; b.w #0x30010c`
  - `0x3001f0`: `ldrb.w r1, [r0, #0x1d0]; cbnz r1, #0x300202; …; b.w #0x30010c` (r1 == 0 here)
  - `0x3001b0`: `…; movs r1, #3; bl #0x30010c`
  - `0x300170`: `…; movs r1, #2; bl #0x30010c`
  - (cases 4/5 additionally test `ViewBase::GetEventId()` (PLT→`0x18bb1c`) against `0x43`/`0x44` to gate a stateful skip path and tail-branch to `0x18bed4`/`0x5930e4`; cases 4/5 branch to the shared move function **with r1 = direction 3 / 2**.)

So the whole Action dispatch `(this, actionID)` — **not** just movement — was folded by Sony into one TBH in this build; the movement indices 1..5 kept the exact ILCE-6400 semantics/order (1 = enter/execute, 2..5 = dirs 1, 0, 3, 2).

**Evidence trail:** vtable slot `0xba2cdc` → `rel+0x30412b` (`.data.rel.ro` raw bytes `2b 41 30 00`) → disassembly of `0x30412a..0x30413a` (`push {r4,r5,r7,lr}; mov r4,r0; cmp r1,#0x93; bhi.w #0x30478c; tbh [pc,r1,lsl#1]`; TBH bytes `df e8 11 f0`) → 148-entry table (`94 00`… step 4) → per-case `pop.w {r4,r5,r7,lr}; b.w` → handler targets (`0x301750`, `0x304114`, `0x300204`, `0x3001f0`, `0x3001b0`, `0x300170`, `0x2f43f4`, `0x2f4420`, …). Tables/cases verified by decoding every entry.

**Load-base mapping:** TBH `base + 0x304136`, function entry `base + 0x30412a`, table `base + 0x30413a`, handler cluster `base + 0x30010c..0x301750`, default branch `base + 0x30478c`, case-1 target `base + 0x304114`. All are `.text`, so `file == vaddr == base-relative offset`.

---

## 3. Action1 (execute-selected) — **LOCATED (high confidence)**

**Case 1 path:**

`0x304136 (TBH)` → case 1 stub `0x30426a` (`bd e8 b0 40` = `pop.w {r4, r5, r7, lr}`; `ff f7 51 bf` = `b.w #0x304114`) → **`0x304114`**:

```
00304114: push {r3, r4, r7, lr}
00304116: mov  r4, r0
00304118: bl   #0x303260        ← step 1: resolve & execute the currently-selected item
0030411e: mov  r0, r4
00304120: bl   #0x2f5564        ← step 2: second half (resolve highlight/mode)
00304124: str.w r0, [r4, #0x174]  ← store result into this+0x174 (same member the PAS handlers read)
00304128: pop  {r3, r4, r7, pc}
```

**Step 1 (`0x303260`, the "execute current item" worker):**
```
ldr.w r3, [r0, #0x164]          ← this+0x164 = current mode/state
subs r2, r3, #1; cmp r2, #1; bhi → mode-3/4 path at 0x30329c
  mode 1/2: ldr r1,[pc,#0xa8]; blx #0x186db0      → PLT → ViewBase::getWidget(this, ID)  (GOT 0xc1a7cc-ish; sym 219)
            blx #0x18da6c                         → PLT → Check_PAS_MenuListTab(Widget*) (GOT 0xc1c8b8; sym 4052)
            cmp r0, #0; beq …; bl #0x2f5f22       ← materializes the PAS_MenuListNode params
            cmp r0, #0; beq …; blx #0x1884e8      → PLT → PAS_MenuListTab::openLevel2Popup(CmnViewSettingNode*) (sym 3640, local, 0x86292c)
            cbnz r0 → movs r3,#2 else movs r3,#1 (mode transitions); b …
  mode 3/4: blx #0x186db0 (getWidget); blx #0x186cd0 → PLT → Check_PAS_MenuListCategorizedItem (sym 3915); executes the categorized path
```
**Step 2 (`0x2f5564`, the "resolve selection result" worker):**
```
ldr.w r3, [r0, #0x164]; cmp r3,#2 / cmp r3,#4 → picks the widget-check helper:
   mode 4 → blx #0x186cd0 (Check_PAS_MenuListCategorizedItem) + tail-branch to 0x189174 → PAS_MenuListCategorizedItem::getHighlightItemPos() (LOCAL, 0x86cbd9)
   mode 2 → blx #0x18da6c (Check_PAS_MenuListTab)             + tail-branch to 0x187e2c → PAS_MenuListTab::getHighlightItemPos()          (LOCAL, 0x860b36)
   else → return −1
```
`0x2f5564` is also inlined-identical at `0x2f55ac..` (same body). `getHighlightItemPos` (`0x860b36`): `ldrb.w r3, [r0,#0x1d8]; cbnz r3 → return −1; else return [r0+0x1fc]` — the stored highlight index of the PAS menu list.

`0x303260` mode==1/2 path tails into `blx #0x1884e8` → **`PAS_MenuListTab::openLevel2Popup(CmnViewSettingNode*)` at file `0x86292c`** (local, exported): opens the selected item’s level-2 menu = **the ZV-E10 equivalent of `enter_selected`**. The result is stored back into `this+0x174` (`0x304114`: `str.w r0, [r4, #0x174]`).

So, mapping ILCE-6400 → ZV-E10:

| ILCE-6400 (framework lib) | ZV-E10 (`viewUnified2.so`) |
|---|---|
| Action1 @ `0x2127e8` (case 1 of TBH) | **`0x304114`** (case 1 of the TBH at `0x304136`; case body `0x30426a`) |
| `enter_selected` @ `0x5ac044` | **`0x303260`** (worker: resolves and executes the highlighted item; for mode 1/2 calls **`PAS_MenuListTab::openLevel2Popup` at `0x86292c`**) + **`0x2f5564`** (resolves highlight index, stores to `this+0x174`) |

**Load-base mapping:** case-1 body `base + 0x30426a`, Action1 `base + 0x304114`, workers `base + 0x303260` and `base + 0x2f5564`, `openLevel2Popup` `base + 0x86292c`, `PAS_MenuListTab::getHighlightItemPos` `base + 0x860b36`, `.text` (file == vaddr).

---

## 4. move handler (indices 2–5) and move apply — **LOCATED (high confidence)**

**Move handler bodies** (case targets of the same TBH):

| action idx | case body (file) | dispatch | direction (r1) |
|---|---|---|---|
| 2 | `0x300204` | `ldrb.w r3, [r0, #0x1d0]; cbnz r3, #0x300218; movs r1, #1; b.w #0x30010c` | **1** |
| 3 | `0x3001f0` | `ldrb.w r1, [r0, #0x1d0]; cbnz r1, #0x300202; b.w #0x30010c` | **0** |
| 4 | `0x3001b0` | …(GetEventId check `0x43`)…`movs r1, #3; bl #0x30010c` (if byte `[this+0x4a4]==0`, tail `b.w #0x18bed4` instead) | **3** |
| 5 | `0x300170` | …(GetEventId check `0x44`)…`movs r1, #2; bl #0x30010c` (same conditional tail call) | **2** |

— exactly the ILCE-6400 dir order **1, 0, 3, 2**.

**Common move handler / move apply** — **`0x30010c`** (called by all four):
```
0030010c: push {r4, r5, r6, r7, lr}; sub sp, #0x24
00300110: mov  r4, r1                    ← r4 := direction
…
00300114: ldr  r1, [pc, #0x54]          ← literal = some WidgetID (menu-list sub-widget)
00300118: blx  #0x186db0                → PLT → ViewBase::getWidget(this, ID) (sym 219)
0030011c: blx  #0x18da6c                → PLT → Check_PAS_MenuListTab(Widget*) (sym 4052)
00300120: mov  r1, r0; cbz r0, #0x300162  ← bail if widget invalid
00300124: ldr  r3, [r0]                  ← widget's vptr
0030012a: mov  r2, r4                    ← r2 := direction (argument 2)
0030012c: ldr.w r3, [r3, #0x1b4]; blx r3  ← VIRTUAL CALL: [widget->vptr + 0x1b4](…, direction)
00300132: ldrb r3, [sp, #8+0xc]; cbz → return
          …build a stack ParamList (ldmdb/stm.w)…
0030014c: bl   #0x30002c                 ← post-move param registration
0030015a: b.w  #0x2f5998                 ← refresh pass
```
**Post-move refresh (`0x2f5998`):** `getWidget(this, lit); Check_PAS_MenuListTab(r0); cbz → return; ldr.w r3, [r0]; mov r1, r4(dir); ldr.w r3, [r3, #0x1b0]; blx r3` → calls the PAS widget’s second direction-taking virtual at **slot +0x1b0**.

**The PAS_MenuListTab widget vtable (local, exported):**

- `_ZTV15PAS_MenuListTab` **address point** vaddr `0xc09e28` → file **`0xc01e28`** (`_ZTV` base vaddr `0xc09e20`, offset-to-top `0`, RTTI at +4 → rel `0xc09fe8`). 112 slots (size `0x1c0`, typeinfo at vaddr `0xc09fe8` → file **`0xc01fe8`**; name `"15PAS_MenuListTab"` at file/vaddr **`0xa8b2a0`** `.rodata`).
- Slot **idx108 (+0x1b0, file `0xc01fd8`)** = `f9 29 86 00` = `rel+0x8629f9` → **`0x8629f8`**: `add.w r3, r0, #0x18400; add.w r3, r3, #0x2a8; …; cbz r2 → skip; strb r2, [r3]; bl #0x861bbc; bl refresh-helpers` — post-move “clear dirty flag + refresh” routine.
- Slot **idx109 (+0x1b4, file `0xc01fdc`)** = `07 1e 86 00` = `rel+0x861e07` → **`0x861e06`**: `push {…}; mov r4, r1; mov r5, r0; mov r6, r2 (direction); bl #0x86022c; ldrb.w r3, [r4, #0x200]; cbnz r3 → …; …; cmp r6, #3; bhi → 0x861e94; tbb [pc, r6]` — **the actual per-direction cursor mover**; table at `0x861e44` (`1e 23 02 10`): idx0→`0x861a08`, idx1→`0x861a3c`, idx2→`0x861e48` (→ `0x861a6e`/`0x861aa2` depending on `[this+0x1d8]`), idx3→`0x861e64` (→ `0x861ac2`/`0x861ae2`); the four functions implement clamp/wraparound cursor movement with dedup (`subs r1,#1; cmp r1,#0; bgt …`). This is the **ZV-E10 “move apply” at the lowest level** (the ILCE-6400 `0x211a24` analog).

Additionally the menu’s list-move primitive is implemented in **`PAS_MenuListPopup::moveHlt(WGT_MoveDirection)` at file `0x88db10`** (local, exported): `bl #0x88c72c (validity) → bl #0x88cf2a (read dirty byte) → bl #0x88d05e (animated move, fp/vector math) → … → bl #0x88cfdc; bl #0x88d5c4`. It is what `PAS_MenuListTab::setPopupPrev (file 0x861b4e)`, `PAS_MenuListTab::setPopupNext (file 0x861b78)` call with `r1 = 0 / 1` (call chain: `setPopupPrev → 0x186d6c (moveHlt) → 0x187b2c (getHltPos) → 0x18a934 (convHltPosToItem)`). These are the exported “move” entry points used from the Action path when the selected item must move (the equivalents of ILCE-6400’s highlight-move applied to the list).

**Method table (file offsets, all `.text`, file == vaddr):**

| role | file offset |
|---|---|
| case bodies 2..5 (dir 1/0/3/2) | `0x300204` / `0x3001f0` / `0x3001b0` / `0x300170` |
| move apply (called by cases) | **`0x30010c`** |
| post-move refresh tail | **`0x2f5998`** |
| PAS_MenuListTab vtable idx109 (`+0x1b4`) = direction TBB | **`0x861e06`** (jump table `0x861e44`) |
| PAS_MenuListTab vtable idx108 (`+0x1b0`) = dirty-flag refresh | `0x8629f8` |
| PAS_MenuListPopup::moveHlt (list move primitive) | `0x88db10` |
| PAS_MenuListTab::setPopupPrev / setPopupNext (exported) | `0x861b4e` / `0x861b78` |
| PAS_MenuListTab::getHighlightItemPos (exported) | `0x860b36` |

**Load-base mapping:** `base + 0x30010c` (move apply), `base + 0x861e06`, `base + 0x88db10`, `base + 0xc01e28` (PAS vtable address point), `base + 0xc09fe8` (typeinfo), `base + 0xa8b2a0` (name). `.text`/rodata `file == vaddr`; vtable/typeinfo `file = vaddr − 0x8000`.

---

## 5. ViewBase::Action / ViewBase::Main (+0x3ebxxx region) — **NOT PRESENT IN THIS FILE (high confidence)**

**Evidence:**

1. `ViewBase::Action(int)` = `_ZN8ViewBase6ActionEi`, sym 1078, `SHN_UNDEF`, `value=0x0`. `ViewBase::Main(char**, char**)` = `_ZN8ViewBase4MainEPiS0_`, sym 840, UNDEF. Also UNDEF: `Entry` (697), `Exit` (989), `Destructor` (1736), `Constructor` (328), `Completed` (99), `onLoad` (1224), `onUnload` (500), `resume` (1568), `suspend` (1723), `IsModelPrepare` (867), `timerSchedule` (1433), etc.
2. The ViewSettingMenu vtable’s Action/Main slots (`idx5 0xbaabc4`; `idx2 0xbaabb8`) are ABS32 relocations to those imported symbols — **this class does not override them**.
3. The **ILCE-6400 file offsets do not transfer**: in this file, `0x3ebef0` contains `add.w r2, r7, #0x6700; …; bl #0x332cb4` (stack-built jump-table initializer, PLT calls to `0x32f5c4`/`0x332cb4`); `0x3edd4c` contains `mcrr2 p5, #0, pc, r2, c7` (misaligned Thumb it’s `b840` = `lsls r0, r7`; `07f5b842` = `add.w r2, r7, #0x5c00`; `bl #0x332cb4` — analogous table builder, different code); `0x3edc04` contains similar table-builder code calling `0x32f5c4/0x32f5d2/0x332cb4`. **None of them is ViewBase::Action / ViewBase::Main / ActionCheckerListBase.** (ILCE-6400 offsets referred to libObj.so.)
4. The Sony **DefRootView** action framework used everywhere in this library is entirely imported:
   `ActionCheckerListBase::Initialize` (sym 77, ×2 in `.rel.plt`), `ActionCheckerListBase::SetBlogParam` (169), `ActionCheckerListBase::ExecEv` (327), `ActionCheckerListBase::IsUpdateEvent` (441), `ActionCheckerListBase::C2Ev` (608), `ActionCheckerListBase::addCheckerEiPNS_11CheckerBaseE` (991, ×2), `ActionCheckerListBase::addActionEiPNS_10ActionBaseE` (1088, ×2), `ActionCheckerListBase::execActionEi` (1313), `ActionCheckerListBase::execCheckEi` (1333), `ActionCheckerListBase::IsReadyExecEv` (1511), `ActionEventMapBase::DispatchEventEm` (663), `ActionEventMapBase::addActionEi...` (821, ×2), `ActionEventMapBase::execActionEi` (1610), `ActionEventMapBase::Initialize` (1299, ×2), `ActionEventMapBase::SetBlogParam` (1272), `ActionEventMapBase::C2Ev` (1059), `ActionEventMapBase::D2Ev` (286), `CheckerBase::addUpdateEvent` (483), `CheckerBase::C2Ev` (798, ×2), `CheckerBase::D2Ev` (1757), `ActionBase::C2Ev` (319, ×2).
   Meaning: view-side “registered actions” handled by the imported framework call back into view code through the Action vtable at slot idx5 (or the `ViewBaseProduct` action path), which lands in the framework library — **the dispatcher logic (`Exec` → `execAction` → TBH) lives in the framework library, not here.**
5. No local (`st_shndx == 10`) symbol or string with `ViewBase6Action`, `ViewBase4Main`, `DefRootView` names exists (checked `.dynsym` local definitions; only imported `SHN_UNDEF` entries above exist).

**What this means for the port blueprint:** In the ZV-E10 firmware, `ViewBase::Action`/`ViewBase::Main` (and the ILCE-6400 `+0x3ebxxx` Action checker list logic) live in the framework library that provides `_ZN8ViewBase6ActionEi` — i.e. **ZV-E10’s `libObj.so`** (21,305,528 bytes) or another co-packaged framework `.so` — **not in `viewUnified2.so`**. They cannot be located inside this file. The closest in-file anchors for the framework call path are the exported/local PAS entry points listed in §3/§4 and the `.rel.plt` imports listed above.

If the parent has (or can dump) ZV-E10’s `libObj.so`, the equivalents will be found by the same RTTI/reloc technique; do **not** assume the ILCE-6400 offsets.

**Load-base mapping:** n/a (absent).

---

## 6. CmnViewSettingNode vtable slots — **NOT PRESENT IN THIS FILE (high confidence)**

**Evidence:**

1. `CmnViewSettingNode` symbols in `.dynsym`: exactly **3**, all `SHN_UNDEF` imports: `_ZN18CmnViewSettingNodeD1Ev` (sym 383), `_ZN18CmnViewSettingNode15initSettingNodeEPPS_i` (sym 1217), `_ZN18CmnViewSettingNodeC1EPPS_iPK24CmnViewSettingProperties` (sym 1324). No local definitions (`st_shndx == 10`), no local hidden duplicates.
2. **No** `CmnViewSettingNode` (mangled or unmangled) byte string exists in `.rodata` (`.rodata` file range `0x8d9e40..0xac2585`): 0 hits for `b"CmnViewSettingNode"`, `b"18CmnViewSettingNode"`, or `b"setItemSelectedBySRNum"`. The only `SRNum` hit is the debug/trace string `"currentSRNum: %d,line:%d in %s"` at file `0xa41b3b`, which belongs to the **`CmnSettingNodeUtil`** (local, exported — e.g. `_ZN18CmnSettingNodeUtil19initSettingNodeDataEP18CmnViewSettingNode` sym ~1841, `…initBeltWidget` `0x6a410d`, `…10setGreyout` `0x6a2e9f`) / **`AfOrientImpl`** debug table (`"12AfOrientImpl"` follows the string). Different class; not the node vtable.
3. **No RTTI** for `CmnViewSettingNode`: the Itanium name `"18CmnViewSettingNode"` does not appear in `.rodata`; the string `18CmnViewSettingNode` occurs only inside `.dynstr` as prefixes/suffixes of symbol names (e.g. `_ZN18CmnViewSettingNodeC1EP…`) and inside the `"…18CmnSettingNodeUtil"` debug string.
4. Therefore the CmnViewSettingNode vtable (and its slot 48) is **not in this library** — it is constructed in the defining library (imported here). All in-file users reference the node only via borrowed symbols (`PAS_*::openLevel2Popup(CmnViewSettingNode*)` etc.), or via `CmnSettingNodeUtil` helpers which themselves are imported/`PLT`-called into PAS code.
5. The `SRNum`-related flow *does* exist here only as debug text (`"currentSRNum: %d,line:%d in %s"` at `0xa41b3b`); the `setItemSelectedBySRNum` implementation (and hence vtable slot 48 semantics) is not recoverable from this file. The vtable layout (slots 0–64, `CautionConfig.so`-identical live bytes per earlier work) must be pulled from the defining library (`CautionConfig.so` — out of scope here) or from ZV-E10’s `libObj.so`.

**Load-base mapping:** n/a (absent).

Local helper classes that *are* present and adjacent: `CmnSettingNodeUtil` (local exported, `0x6a2e29`–`0x6a410d` range; wraps node calls), `ViewSettingMenuToInstance` (`0x2f36f4`).

---

## 7. Summary table

| # | Item | Status | File offset (this build) | vaddr / load-map | Confidence |
|---|---|---|---|---|---|
| 1 | ViewSettingMenu vtable | **Located** | `_ZTV` at **`0xba2ba8`** (addr pt `0xba2bb0`); RTTI `0xba2da4`; name `0x8ef14f`; fragment `0xba2d54`; GOT `0xc14f38` | `_ZTV base+0xbaaba8`, addr pt `base+0xbaabb0`, RTTI `base+0xbaada4`, name `base+0x8ef14f`, GOT slot `base+0xc1cf38` | **High** |
| 2 | TBH (Action) dispatcher | **Located** — 148-case TBH (all actions; idx1..5 = enter/dirs 1,0,3,2) | entry **`0x30412a`**, TBH instr `0x304136` (`df e8 11 f0`), table `0x30413a`, default `0x30478c`; slot idx75 (`_ZTV+0x134`) file `0xba2cdc` | `base+0x30412a` (`.text`) | **High (structural) / Medium (slot name)** |
| | ↳ (note: no small TBH exists; framework Action itself is imported) | — | — | — | High |
| 3 | Action1 (execute-selected) | **Located** | case1 stub `0x30426a` → **`0x304114`** → workers **`0x303260`** + **`0x2f5564`**; `PAS_MenuListTab::openLevel2Popup` **`0x86292c`** | `base+0x304114` (`.text`) | **High** |
| 4 | move handler (idx 2–5) + move apply | **Located** | case bodies `0x300204`(dir1) / `0x3001f0`(dir0) / `0x3001b0`(dir3) / `0x300170`(dir2); **move apply `0x30010c`**; refresh `0x2f5998`; PAS_MenuListTab dir-TBB `0x861e06` (+`+0x1b4`), `0x8629f8` (+`+0x1b0`); `PAS_MenuListPopup::moveHlt` `0x88db10`; `PAS_MenuListTab::setPopupPrev/Next` `0x861b4e`/`0x861b78`; PAS vtable `0xc01e20` (addr pt `0xc01e28`) | `base+0x30010c`, `base+0x861e06`, `base+0x88db10`, `base+0xc01e28` | **High** |
| 5 | ViewBase::Action / Main (+0x3ebxxx region) | **Not present** — imported (`SHN_UNDEF`); ILCE-6400 offsets verified as unrelated code | — | — | **High** |
| 6 | CmnViewSettingNode vtable (slot 48) | **Not present** — imported; only 3 UNDEF symbols; no RTTI/strings | — | — | **High** |

Global load-base arithmetic: `.text/.plt/.rodata/.init` — `file == vaddr == base-relative offset`; `.init_array/.data.rel.ro/.got/.data/.bss` — `vaddr = file + 0x8000` (`loaded = base + file + 0x8000` for the slot; relocations store *target* = `base + addend` in the slot). `.rel.dyn` `r_offset`s are vaddrs. No runtime addresses are given for any item.

---

## 8. Exact commands / method used

- ELF parse & section/segment dump: `python + pyelftools 0.33` (`ELFFile`, `iter_sections`, `iter_segments`, `.dynsym`, `.rel.dyn`, `.rel.plt`). Result file map: §0.
- All relocations dumped: `relocs.txt` (`r_offset type sym addend` per line) via `.rel.dyn` iteration (160 373 entries).
- vtable/typeinfo hunt: searched `.rel.dyn` for `addend == <typeinfo vaddr>` references (e.g. `0xbaada4` → found `0xbaabac` = ViewSettingMenu vtable RTTI slot and `0xbaad58` = fragment RTTI). Confirmed by matching constructor-vtable-pointer patterns (`offset-to-top` word `0x00000000`/`0xffffffd8`, then RTTI ptr, then virtuals). Cross-checked every slot against the relocation table (kind: `rel+0x…` = local target Thumb bit set; `ABS32 sym` = imported `SHN_UNDEF`; `ABS32 sym` with `shndx=10` + local `rel+…` = locally defined exported call through PLT).
- TBH survey: `data.find(b"\xdf\xe8")` in `.text` (`0x18eec8..0x8d9e34`) → 1422 raw hits; classified by second halfword (`0xf0NN` → TBB, `0xf1NN` → TBH) → 1114 TBB / 308 TBH. Filtered to same-register preceding `cmp rN, #imm (imm ≤ 10)` (capstone scan of the preceding 24 bytes, register cross-check against the TBH index register) → 87 small TBHs; **none** of them is the menu Action dispatcher (they are key handlers / event mappers / mode switches, e.g. `0x599874` key-code dispatcher, `0x49005c` exposure-mode dispatcher, `0x2f2c90` trigger-table/event→command LUT mapped through `View::requestTriggerTable`, `0x30412a` 148-case TBH). The `0x30412a` 148-case TBH was chosen because (a) it is an actual override in the `ViewSettingMenu` vtable (idx75), (b) case 1 = execute-selected (§3), (c) cases 2..5 pass dirs **1/0/3/2** into a shared move-apply virtual call, matching the ILCE-6400 dispatcher’s indices 1..5 exactly.
- Disassembly: capstone 5.0.7, `CS_ARCH_ARM | CS_MODE_THUMB` (ARM mode used for PLT stub bodies).
- PLT→symbol resolution: decoded each stub (`add ip, pc, #0xa00000; add ip, ip, #imm; ldr pc, [ip, #off]`), computed the GOT slot, and matched it to `.rel.plt` `R_ARM_JUMP_SLOT` entries → symbol. GOT slot base formula verified on `0x188580` (→ `CheckFocusAreaInh::stopCacheEv`) and `0x18dc78` (→ `startCacheEv`). (Initial mis-resolution was caused by two hex-arithmetic slips and by capstone’s `add ip, ip, #144, #20` rotate notation; corrected before any result was accepted.)
- Strings/RTTI: raw `bytes.find` over the file and per-section; `.rodata` string hits listed with exact file offsets.

Artifacts (in `<workspace>\zv-e10-analysis\`): `relocs.txt` (all .rel.dyn entries), `tbh_hits.txt` (all `df e8` hits with context), `vtable_dump.txt` (primary-vtable slot table), `action_framework_stubs.txt`.

## 9. Honest gaps / caveats

- The exact C++ *name* of the idx75 virtual (`_ZTV+0x134`, `0x30412a`) cannot be printed — local symbols are stripped; it is an override of some `ViewBaseProduct`-derived action virtual, not one of the named imported ones. Structural evidence (action-id switch, joint case 1..5) strongly identifies it as the view’s action handler.
- Whether the imported `ViewBase::Action(int)` (or the imported `DefRootView` framework) ultimately *calls* the 0x30412a dispatcher, or calls `ViewSettingMenu`’s version via the imported framework’s `ActionCheckerListBase` registration generated at view construction — cannot be proven from this file alone. In both cases the handler entry is 0x30412a.
- Case 4/5 have extra `GetEventId` gating (`0x43`/`0x44`, i.e. 67/68) and a conditional branch to `0x18bed4`/`0x5930e4` instead of the shared move apply — semantics not resolved (likely “move only if key event, else default outside-menu behavior”), documented but not interpreted.
- `ViewGlobalMenu`/`ViewSettingMenuToInstance` construction vtable chain (fragment at `0xba2d54`, −40) — the derived class that hosts `ViewSettingMenu` at offset +40 was not identified in scope.
- Items 5 and 6 are genuinely **not in this file**; both would be co-packaged in ZV-E10’s `libObj.so` or `CautionConfig.so`. Any claimed offsets for them within `viewUnified2.so` would be fabricated.
- The `hash`/`gnu.hash` symbol table confirms all reported exported names are really exported (`shndx=10`), so the PAS entry points are callable from the framework library via normal PLT resolution.

---


---

## 10. ADDENDUM - items 5 and 6 located in libObj.so / CautionConfig.so; full Action chain; PAS reconciliation

### 10a. Full Action dispatch chain in the ZV-E10 build (supersedes the section 2 caveat)

ViewBase::Action(int) does NOT call the TBH directly. The ZV-E10 framework (like ILCE-6400's) wraps the dispatch in a cache bracket:

    ViewBase::Action(int)                 [libObj.so file 0x1393a8]
      push {r4,r5,r7,lr}; mov r4,r0; mov r5,r1
      bl   #0x13939c                       (framework helper, not decoded)
      ldr  r3, [r4]; mov r0, r4; mov r1, r5
      ldr.w r3, [r3, #0xa0]                vtable slot +0xa0 (slot 40, from address point)
      blx  r3

  -> slot +0xa0 in the ViewSettingMenu vtable (idx40, file 0xba2c50, ABS32 -> sym 3189) = ViewBaseProduct::ProductActionEi - defined LOCAL+exported in viewUnified2.so at file 0x59c76e (sym 3189, value 0x59c76f). Its body:

    ldr  r3, [r0]; push {r4,r5,r7,lr}; mov r4,r0; add r7,sp,#0
    ldr.w r3, [r3, #0x100]; blx r3          `[slot +0x100](this)`          -> slot 64 = idx64 -> 0x2edd0b
    mov  r5, r1                             (r1 = action ID)
    ldr.w r3, [r3, #0x12c]; blx r3          `[slot +0x12c](this, action)`  -> slot 75 = idx75 -> 0x30412b
    ldr.w r3, [r3, #0x104]; blx r3          `[slot +0x104](this)`          -> slot 65 = idx65 -> 0x2edccf
    pop  {r4,r5,r7,pc}

  i.e. startCache (slot +0x100/idx64 -> thunk 0x2edd0a -> PLT 0x18dc78 -> CheckFocusAreaInh::startCacheEv, local 0x597380) -> dispatch (slot +0x12c/idx75 -> 0x30412a = the 148-case TBH) -> stopCache (slot +0x104/idx65 -> thunk 0x2edcce -> PLT 0x188580 -> CheckFocusAreaInh::stopCacheEv, local 0x59739c). ProductAction(...) itself never touches the action ID except forwarding it in r1 to the dispatcher.

So the ILCE-6400 "vtable+0x100 = Action dispatcher" maps in ZV-E10 to: +0x100 = startCacheEv, +0x104 = stopCacheEv, +0x12c = the Action dispatcher (148-case TBH @ 0x30412a, vtable slot idx75, byte offset +0x12c from address point / _ZTV+0x134) - the Sony framework inserted two cache-lifecycle shims around the handler and moved the handler deeper into the vtable. The ILCE-6400 slot numbering does not transfer.

### 10b. ViewBase::Action / ViewBase::Main - LOCATED in ZV-E10 libObj.so (high confidence)

<workspace>\zv-e10-flasha15\lib\libObj.so (21,305,528 B). Same segment layout as viewUnified2.so (seg1 file==vaddr==0; seg2 file=vaddr-0x8000; same .data.rel.ro rule).

| Symbol (dynsym) | sym | file/vaddr | role |
|---|---|---|---|
| _ZN8ViewBase6ActionEi | 1956 | **file/vaddr 0x1393a8** (0x1393a9) | ViewBase::Action(int) - exported; forwards to vtable slot +0xa0 |
| _ZN8ViewBase4MainEPiS0_ | 2745 | **file/vaddr 0x13bc80** (0x13bc81) | ViewBase::Main(char**, char**) - exported; calls bl #0x13bb1c (framework impl), forwards to slot +0x9c (slot 39) near the end |
| _ZN11DefRootView21ActionCheckerListBase4ExecEv | 3477 | **0x129faa** | Exec() - vtable slot +0xc dispatch |
| ActionCheckerListBase10execActionEi | 2292 | **0x12b8bc** | per-action executor (iterates the action map at [r0+0x34], calls bl #0x1537c8) |
| ActionCheckerListBase9execCheckEi | 2420 | **0x12b41c** | per-check executor |
| ActionCheckerListBase11IsReadyExecEv | 2320 | **0x12a260** | readiness check |
| ActionCheckerListBase10InitializeEPNS_18ViewFunctionImplIfE | 2846 | **0x129f8e** | framework init |
| ActionCheckerListBase9addActionEiPNS_10ActionBaseE | 3528 | **0x12b912** | action registration |
| ActionEventMapBase13DispatchEventEm | 2084 | **0x1355e4** | event -> action map dispatch |
| _ZTIN11DefRootView21ActionCheckerListBaseE | 2721 | vaddr 0x1389990 (.data.rel.ro) | typeinfo |
| _ZTIN11DefRootView18ActionEventMapBaseE | 3630 | vaddr 0x138aa90 | typeinfo |
| _ZTIN11DefRootView11CheckerBaseE | 3278 | vaddr 0x138ec08 | typeinfo |

**ViewBase vtable _ZTV8ViewBase (primary)** - NOT a dynamic symbol (local); located via RTTI reference:

- vtable at vaddr **0x138ace8** -> file **0x1382ce8** (.data.rel.ro). offset-to-top = 0 (+0x00), RTTI at +0x04 -> ABS32 -> **_ZTI8ViewBase** (sym 2956, vaddr **0x138add8** -> file **0x1382dd8**, name "8ViewBase" at file/vaddr 0xe4cb65, base: ABS32 _ZTI4View + rel+0x1388e00; __vmi_class_type_info, 2 bases, arity 2, flags 0x2802).
- **Address point** = vaddr **0x138acf0** -> file **0x1382cf0** (slot 0 = _ZN8ViewBase5EntryEv ... idx5 = _ZN8ViewBase6ActionEi at +0x14, idx39 = +0x9c = __cxa_pure_virtual, idx40 = +0xa0 = __cxa_pure_virtual, idx52 = _ZN8ViewBase24UserGetBootFailCautionIdEv at +0xd0; 53 slots idx0..idx52, vtable ends at 0x138adc4 where the -40 fragment begins; full slot table verified against relocs).
- A secondary/construction vtable fragment at **0x138adc4** (offset-to-top 0xffffffd8 = -40, RTTI -> _ZTI8ViewBase; first slots rel+0x13b9c9, rel+0x13b9e5) - ViewBase at offset +40 in a derived class (derived class not identified).
- Vtable-group neighbors: two locally-defined __si_class_type_info typeinfos at 0x138acc0/0x138accc and an abstract construction fragment at 0x138acd8 (RTTI -> rel+0x138accc, one local virtual rel+0x138e71).

These are the ILCE-6400 0x3ebef0 / 0x3edd4c / ~0x3edc04 equivalent positions (ILCE-6400 offsets do not apply to ZV-E10).

**Confidence: High** - local exported symbols + disassembly + RTTI/reloc cross-validation.

### 10c. CmnViewSettingNode vtable - LOCATED in ZV-E10 CautionConfig.so (high confidence)

<workspace>\zv-e10-flasha15\lib\CautionConfig.so (17,694,160 B). Segment layout same pattern (seg1 file==vaddr; seg2 file=vaddr-0x8000; .data.rel.ro at vaddr 0xf97580 / file 0xf8f580; .rodata at vaddr==file 0xdb1d18).

- _ZTS18CmnViewSettingNode (typeinfo name string) sym 27296, shndx=12 (.rodata), vaddr==file **0xdd74dc** = "18CmnViewSettingNode".
- _ZTI18CmnViewSettingNode sym 72138, shndx=18 (.data.rel.ro), vaddr **0xfab954** -> file **0xfa3954**: {ABS32 -> _ZTVN10__cxxabiv117__class_type_infoE +8 (no base), ABS32 -> _ZTS18CmnViewSettingNode}.
- _ZTV18CmnViewSettingNode sym 77555, shndx=18, vaddr **0xfab960** -> file **0xfa3960**: +0x00 offset-to-top = 0; +0x04 RTTI -> ABS32 _ZTI18CmnViewSettingNode; **address point** = vaddr **0xfab968** -> file **0xfa3968**; **65 virtual slots (idx0..idx64, +0x00..+0x100)**.
- Slot map (from reloc table, byte offset from address point): idx0 +0x00 D1Ev, idx2 +0x08 initSettingNodeEPPS_i, ... idx46 +0xb8 setItemSelectedEv, idx47 +0xbc setItemSelectedByIndexEi, **idx48 +0xc0 _ZN18CmnViewSettingNode22setItemSelectedBySRNumEi**, idx49 +0xc4 setItemSelectedByDiffSRNumEi, idx64 +0x100 updateSRNumEv. Ends at 0xfaba6c (next word not a relocation).
- **Slot 48 confirmed exactly**: vtable relocation r_offset=0xfaba28 type=R_ARM_ABS32 sym=62014 (_ZN18CmnViewSettingNode22setItemSelectedBySRNumEi); sym 62014 defined locally, value 0xb8e60d -> code at **file 0xb8e60c** (.text, file==vaddr). Disassembly: push {r7,lr}; sub sp,#8; adds r2,r7,#4; ldr r3,[r0]; ldr r3,[r3,#0x38]; blx r3; cbz r0 -> return; ldr r0,[r7,#4]; ldr r3,[r0]; ldr.w r3,[r3,#0xb8]; blx r3 - resolves the target sub-node via vtable slot +0x38 (getIdBySRNum-ish) then calls the sub-node's vtable slot +0xb8 (idx46 = setItemSelectedEv) - i.e. select the item identified by its SR number. Matches the a6400 "slot 48 = setItemSelectedBySRNum" exactly.
- CmnViewSettingNode has **no base class** (__class_type_info), 65 slots, +0x100 = updateSRNumEv - same shape as the a6400 layout ported before.

**Confidence: High** - dynsym-defined symbols + relocations + Itanium layout all agree.

### 10d. PAS_MenuListTab vtable reconciliation (viewUnified2.so)

**Same object** _ZTV15PAS_MenuListTab, not a second vtable: the Ghidra project loads viewUnified2.so with **image base 0x10000**, so every Ghidra address = ELF vaddr + 0x10000, and file == vaddr only in .text/.rodata/.plt (seg1); seg2 (.data.rel.ro) has vaddr = file + 0x8000. Mappings:

- Ghidra 0x00c19e20 = my vaddr 0xc09e20 = **file 0xc01e20** (_ZTV; offset-to-top 0, RTTI at +0x04 -> rel 0xc09fe8) - offset-to-top/RTTI bytes verified in file (00 00 00 00 e8 9f c0 00).
- Ghidra 0x00c19fe8 = my vaddr 0xc09fe8 = **file 0xc01fe8** = _ZTI15PAS_MenuListTab (name "15PAS_MenuListTab" at file/vaddr 0xa8b2a0).
- Ghidra 0x00a9b2a0 = my file/vaddr 0xa8b2a0 (.rodata).
- Ghidra 0x00870951 = my slot idx0 = rel+0x860951 (+0x10000).
- Ghidra 0x00870b36 = my **file/vaddr 0x860b36** (PAS_MenuListTab::getHighlightItemPos, exported).
- Ghidra's "+0x08 0x00e6056c" = idx2 ABS32 -> _ZNK2ux6wgtsys10WidgetBase10isWidget3DEv (imported); Ghidra resolved the ABS32 against the concurrently-loaded libObj.so program (its image base 0x0) - i.e. 0x00e6056c is libObj's address of that symbol, **not** bytes in viewUnified2.so (raw bytes there are 00 00 00 00).
- Ghidra's "magic keys at 0x2ecb60/0x2ed120/0x2ed14c" are **file offsets** (verified in file: c7 a2 dc aa = 0xAADCA2C7, dd 8d dc df = 0xDFDC8DDD, 04 f6 56 a0 = 0xA056F604) - their search_bytes reports raw file offsets, while get_code/xrefs report Ghidra addresses; inconsistent denomination in the messages, same bytes.

My report's "file 0xc01e20 / addr point file 0xc01e28" is the same complete-object vtable; the Ghidra view is the same bytes at base 0x10000. No second vtable, no fragment.

### 10e. Updated summary rows

| # | Item | ZV-E10 location | Confidence |
|---|---|---|---|
| 5 | ViewBase::Action/Main | libObj.so 0x1393a8 / 0x13bc80; ViewBase vtable _ZTV8ViewBase at file 0x1382ce8 (addr pt file 0x1382cf0) | High |
| 6 | CmnViewSettingNode vtable (slot 48) | CautionConfig.so _ZTV18CmnViewSettingNode at file 0xfa3960 (addr pt file 0xfa3968); slot 48 at 0xfa3a28 -> _ZN18CmnViewSettingNode22setItemSelectedBySRNumEi (code file 0xb8e60c) | High |
