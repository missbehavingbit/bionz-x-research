# ZV-E10 libObj.so — Reverse-engineering report (a6400 equivalents)

**File:** `<workspace>\zv-e10-flasha15\lib\libObj.so` — 21,305,528 bytes, ELF32 LSB, `EM_ARM`, `ET_DYN`, entry `0x0010fee0`.
**Identity evidence cross-checked against:** `<workspace>\sony-a6400-research\docs\evidence\0523-mod-loader-ok-routing-diagnostics.md` (ILCE-6400, same Sony BIONZ-X / "CE" framework).
**Tools:** pyelftools 0.33, capstone 5.0.7 (Thumb-2, no M-class), raw byte scans, R_ARM_RELATIVE/GLOB_DAT/ABS32 relocation maps built from `.rel.dyn` (`libObj-relocs.txt`, 79,841 RELATIVE + 49 GLOB_DAT + 22,559 ABS32; 102,449 total entries; `.rel.dyn` @ file `0x0003ade0`, `DT_REL`/`DT_RELSZ` = `0x3ade0`/`0xc8188`).

## 0. File layout and base mapping (read this first)

Two `PT_LOAD` segments; **all values in this report are link-time VAs (load base 0), which coincide with file offsets in segment 1** (`file off == VA`). Segment 2 is shifted by **+0x8000** (`VA = file off + 0x8000`).

| Seg | Type | File range | vaddr range | Flags | Delta |
|---|---|---|---|---|---|
| 1 | PT_LOAD | `0x00000000..0x0135ddd4` | `0x00000000..0x0135ddd4` | R+X (5) | +0x0 |
| 2 | PT_LOAD | `0x0135ddd4..0x0151f328` | `0x01365dd4..0x0161a928` | RW (6) | **+0x8000** |
| 0 | PT_ARM_EXIDX | `0x012e4fe4..0x0135ddd4` | same | R (4) | +0x0 |

Section map (file == VA unless noted):

| Section | off | addr | size | RX? |
|---|---|---|---|---|
| `.gnu.hash` | 0x000000d4 | 0x000000d4 | 0x000036b0 | R |
| `.dynsym` | 0x00003784 | | 0x0000f370 | R |
| `.dynstr` | 0x00012af4 | | 0x0002643d | R |
| `.rel.dyn` | 0x0003ade0 | | 0x000c8188 | R |
| `.rel.plt` | 0x00102f68 | | 0x00005060 | R |
| `.init` | 0x00107fc8 | 0x00107fc8 | 0x0a | R+X |
| `.plt` | 0x00107fd4 | 0x00107fd4 | 0x00007f00 | R+X |
| `.text` | 0x0010fee0 | 0x0010fee0 | **0x00d3b028** | R+X |
| `.fini` | 0x00e4af08 | | 0x06 | R+X |
| `.rodata` | 0x00e4af10 | 0x00e4af10 | 0x00397b01 | R+X |
| `.ARM.extab` | 0x011e2a14 | | 0x001025d0 | R+X |
| `.ARM.exidx` | 0x012e4fe4 | | 0x00078df0 | R |
| `.init_array` | 0x0135ddd4 | `0x01365dd4` | 0x00000440 | RW |
| `.data.rel.ro` | 0x0135e220 | `0x01366220` | 0x00071880 | RW |
| `.dynamic` | 0x013cfaa0 | `0x013d7aa0` | 0x00000188 | RW |
| `.got` | 0x013cfc28 | `0x013d7c28` (= `DT_PLTGOT`) | 0x00009fe8 | RW |
| `.data` | 0x013d9c10 | `0x013e1c10` | 0x00077718 | RW |
| `.bss` | 0x01451328 | `0x01459328` | 0x0003fb54 | RW (NOBITS) |

Note: `.plt` [`0x00107fd4`, `0x00117ed4`) overlaps `.text` [`0x0010fee0`, ...) — the sections overlap in the address space (descriptive sections over the RX segment), not a file error. `.plt` stubs are **ARM-mode**; callers in Thumb reach them via a 4-byte `78 47 c0 46` (`bx pc; mov r8,r8`) veneer at even entry, +4, or via `blx #ARM_addr`.

**Load-base rule:** at runtime, `runtime_VA = base + link_VA`; `.rel.dyn` R_ARM_RELATIVE entries patch slots with `base + addend`; nothing else needs a base (delta-encoded literals are PC-relative deltas computed at link time with PC = insn+4; confirmed experimentally, see §3). The ZV-E10 load base is **unknown** (a6400 used `0x05d18000`).

**Defined dynamic symbols:** 1,955 (e.g., `ViewBase::Action`, `DefRootView::ActionCheckerListBase::*`, `EventManager::*`) — this build is **not** stripped of dynamic symbols; only local (non-exported) code is unnamed. All offsets below were cross-verified call sites + relocations, not just the dynsym table.

---

## Item 1 — `ViewBase::Action` / `ViewBase::Main`

| Function | File offset = VA | Size | Confidence |
|---|---|---|---|
| `_ZN8ViewBase6ActionEi` (ViewBase::Action(int)) | **`0x001393a8`** (sym `0x001393a9`, Thumb) | 0x24 | High (dynsym + disasm) |
| `_ZN8ViewBase4MainEPiS0_` (ViewBase::Main(Event*, void*)) | **`0x0013bc80`** (sym `0x0013bc81`) | 0x98 | High |
| Routing helper called by Main | **`0x0013bb1c`** | ~0x110 | High |
| `ViewBase::Entry` | `0x00139400` | 0x1e | dynsym |
| `ViewBase::Exit` | `0x00139332` | 0x1e | dynsym |
| `ViewBase::Constructor / C2(ViewManager*)` | `0x001395e0` / `0x0013ba34` | 0x30 / 0xe8 | dynsym |

### 1a. ViewBase::Action (`+0x3ebef0` on a6400)

Bytes @ `0x001393a8`:
`b0 b5 04 46 00 af 0d 46 ff f7 f4 ff 23 68 20 46 29 46 d3 f8 a0 30 98 47 20 46 29 46 bd e8 b0 40 ff f7 eb bf`

```
0x001393a8: push     {r4, r5, r7, lr}
0x001393aa: mov      r4, r0
0x001393ac: add      r7, sp, #0
0x001393ae: mov      r5, r1
0x001393b0: bl       #0x13939c        ; empty stub (push {r7,lr}; add r7,sp,#0; pop {r7,pc})
0x001393b4: ldr      r3, [r4]          ; this->vptr
0x001393b6: mov      r0, r4
0x001393b8: mov      r1, r5
0x001393ba: ldr.w    r3, [r3, #0xa0]   ; <<< vtable slot +0xa0  (a6400: +0x94)
0x001393be: blx      r3
0x001393c0: mov      r0, r4
0x001393c2: mov      r1, r5
0x001393c4: pop.w    {r4, r5, r7, lr}
0x001393c8: b.w      #0x1393a2        ; tail-jump to tiny return-thunk stub
```

- **Semantics match the a6400 build exactly**: `Action(int)` forwards to **vptr + 0xa0** (i.e., `this->vtable[40]`). On a6400 the slot was `+0x94` (`this->vtable[37]`). **The a6400 and ZV-E10 ViewBase vtables are 3 virtual slots apart** (ZV-E10 has 3 additional virtuals before `ProductAction`/the Action slot — e.g., inserted earlier in the interface).
- `bl #0x13939c` targets a 6-byte empty stub (`push {r7,lr}; add r7,sp,#0; pop {r7,pc}`); likewise the final `b.w #0x1393a2` hits another such stub — these are linker/codegen artifacts, not real work.
- a6400 had a separate "helper" at `+0x3edc04` next to `Main` — the ZV-E10 analog of that routing helper is **`0x0013bb1c`** (called by Main, see 1b).
- a6400 evidence (`0523` doc): `Action` forwards to `ViewSettingMenu`'s `+0x100` dispatcher = a6400 `ViewSettingMenu vtable +0x100`. On ZV-E10 the equivalent (`ViewSettingMenu`-style dispatcher at `vtable+0x100`) cannot be cross-checked: no `ViewSettingMenu`/`CmnViewSettingNode` RTTI exists in this module (see Item 5). The `ViewSettingMenu` class most likely lives in `viewUnified2.so` (same as a6400).

### 1b. ViewBase::Main (`+0x3edd4c` on a6400)

Bytes @ `0x0013bc80`:
`2d e9 f0 43 85 b0 04 af 04 46 0e 46 15 46 cd f7 62 ed d4 f8 14 90 80 46`

```
0x0013bc8e: blx  #0x00109754        ; PLT -> _ZNK4View7getViewEv      (View::getView() const)
0x0013bc92: ldr.w sb, [r4, #0x14]   ; this->field_0x14  (the Event* stored in the view)
0x0013bc96: mov  r0, sb; blx #0x00108790   ; PLT -> _ZNK5Event5getIdEv  (Event::getId())
0x0013bc9e: ldr  r3, [pc, #0x74]    ; literal 0x11012001 (event-id magic)
0x0013bca0: cmp  r0, r3
0x0013bca2: bne  #0x0013bcbc
0x0013bca4: mov  r0, sb; movs r1, #0x21; movs r2, #1; blx #0x0010d2b8  ; Event::getParameter(ev, 0x21, 1)
0x0013bcae: cbz  r0, #0x0013bcbc
0x0013bcb0: bl   #0x00111158        ; (unnamed, no dynsym)
0x0013bcb4: cmp  r8, r0; bne  #0x0013bcbc
0x0013bcb8: bl   #0x00144c8c        ; (unnamed)
0x0013bcbc: mov  r0, r4; mov r1, r6; mov r2, r5; bl #0x0013bb1c  ; **the routing helper**
0x0013bcc6: cmp  r0, #1             ; == 1 ?
0x0013bcca: bne  #0x0013bd00
0x0013bccc: ldr  r3, [r4]           ; this->vptr
0x0013bcd4: ldr.w r3, [r3, #0x9c]   ; <<< vtable slot +0x9c (compare: Action uses +0xa0)
0x0013bcd8: blx  r3
0x0013bcdc: blx  #0x00109754        ; View::getView()
0x0013bce4: bl   #0x001390aa        ; (unnamed)
...
0x0013bcf0: movs r2, #0; mov r3, r0; mov r0, r8; blx #0x0010d330 ; PLT -> _Z17blogEventSequence15BlogViewOrModeli21BlogEventSequenceTypeiiiP5Event
0x0013bd10: b.w  #0x00139378        ; tail call into layoutChange()-family
; literal pool @0x0013bd14: 0x11012001
```

**Routing helper `0x0013bb1c`** (the a6400 `+0x3edc04` analog, called by Main):

```
0x0013bb26: ldr  r5, [pc, #0x150]   ; delta   -> r5 = .got base (0x013d7c28)
0x0013bb28: ldr  r6, [r0, #0x14]    ; the Event*
0x0013bb2a: add  r5, pc
0x0013bb2e: mov  r0, r6; blx #0x00108790        ; Event::getId()
0x0013bb32: ldr  r3, [pc, #0x130]   ; 0x1100400b (event magic, path A)
0x0013bb34: cmp  r0, r3; beq #0x0013bb74
0x0013bb38: ldr  r3, [pc, #0x12c]   ; 0x11014001 (path B)
0x0013bb3a: cmp  r0, r3; bne #0x0013bb7c
...
0x0013bba0: and  r3, r0, #0xff000000; cmp.w r3, #0x12000000 ; masked compare (path C)
0x0013bba8: beq  #0x0013bbb6
0x0013bbaa: ldr  r3, [pc, #0xc4]    ; 0xff00f000 (mask)
0x0013bbac: ldr  r2, [pc, #0xc4]    ; 0x17008000 (magic)
0x0013bbb2: cmp  r3, r2; bne #0x0013bc36
0x0013bbb6: bl   #0x0012a4f6        ; init local DefRootView checker list on stack
...
0x0013bbbc: ldr  r3, [r4]; ldr.w r3, [r3, #0x48]   ; <<< vtable slot +0x48
0x0013bbc4: blx  r3
...
0x0013bbf2: ... inline event-post: _Znwj(0x10) -> _ZN5EventC1Emhh -> push (see Item 4)
```

Literal pool of the helper (`0x0013bc5c..0x0013bc7f`): `0x11012007`, `0x1100400b`, `0x11014001`, `0xff00f000`, `0x17008000`, `0x0129c0fa` (→ `.got` base), `0x000086f8` (→ `.got+0x86f8` = slot `0x013de320`, `R_ARM_RELATIVE` addend `0x00b360b1` = Thumb func `0x00b360b0`).

---

## Item 2 — `DefRootView::ActionCheckerListBase` chain (IsReadyExec / IsUpdateEvent)

All offsets = file offset = VA (segment 1). From `.dynsym` (exported, Thumb) and verified disassembly:

| Symbol | VA (=file off) | Size | Role |
|---|---|---|---|
| `_ZN11DefRootView21ActionCheckerListBase11IsReadyExecEv` | **`0x0012a260`** (sym `+1`) | 0x96 | IsReadyExec() |
| `_ZN11DefRootView21ActionCheckerListBase13IsUpdateEventEm` | **`0x0012a65c`** | 0x24 | IsUpdateEvent(unsigned long) |
| `_ZN11DefRootView21ActionCheckerListBase4ExecEv` | `0x00129fac` | 0x0c | Exec() — `ldr r3,[r3,#0xc]; blx r3` (dispatch to a vtable slot `+0xc`) |
| `_ZN11DefRootView21ActionCheckerListBase10execActionEi` | `0x0012b8bd` | 0x56 | execAction(int) |
| `_ZN11DefRootView21ActionCheckerListBase9execCheckEi` | `0x0012b41d` | 0x5e | |
| `_ZN11DefRootView21ActionCheckerListBase10addCheckerEiPNS_11CheckerBaseE` | `0x0012b47b` | 0x70 | |
| `_ZN11DefRootView21ActionCheckerListBase9addActionEiPNS_10ActionBaseE` | `0x0012b913` | 0x22 | |
| `_ZN11DefRootView21ActionCheckerListBase10InitializeEPNS_18ViewFunctionImplIfE` | `0x00129f8f` | 0x1c | |
| `_ZN11DefRootView21ActionCheckerListBase12SetBlogParamEiiiii` | `0x00129fdf` | 0x3a | |
| `_ZN11DefRootView11CheckerBase14addUpdateEventEm` | `0x0016a0ce` | 0x1e | CheckerBase |
| `_ZN11DefRootView11CheckerBaseC2EPNS_18ViewFunctionImplIfE` | `0x0016a010` | 0x3c | |
| `_ZN11DefRootView18ActionEventMapBase10execActionEi` | `0x00135739` | 0x60 | ActionEventMapBase |
| `_ZN11DefRootView18ActionEventMapBase10DispatchEventEm` | `0x001355e4` | 0x0c | |
| `_ZN11DefRootView10ActionBaseC2EPNS_18ViewFunctionImplIfE` | `0x001537ac` | 0x1c | |

`IsUpdateEvent` disasm:

```
0x0012a65c: push {r7, lr}; sub sp, #8; add r7, sp, #0
0x0012a662: add.w r3, r7, #8
0x0012a666: adds r0, #0x1c            ; this+0x1c (checker list node ptr field)
0x0012a668: str  r1, [r3, #-0x4]!     ; sp-4 <- arg (event id?)
0x0012a66c: mov  r1, r3; bl #0x0012a630   ; -> list-find/iterate helper
0x0012a672: cmp  r0, #0; movne r0, #1
...
```

`0x0012a630` (called by IsUpdateEvent) is the list-walk/predicate combinator (calls `0x0012a5d6`, `0x0012a1ea`, `0x0012a0d2`, `eor r0,r0,#1`).

`IsReadyExec` disasm (head):

```
0x0012a260: push.w {r4..r8, sb, lr}; sub sp, #0x14
0x0012a26a: add.w r8, r4, #4          ; this+4
0x0012a26e: ldr  r6, [r0, #0x54]      ; this+0x54 (current iterator)
0x0012a272: bl   #0x0012a018          ; (list begin helper, `str r2,[r0]`)
0x0012a276: mov  r0, r8; bl #0x0012a256   ; (list end helper)
0x0012a27c: str  r0, [r7, #4]
... compares iterators, iterates [this+0x54], calls vtable slot `+0x48/+0xc` via `blx r3` ...
0x0012a2a4: ldr  r1, [r4, #0x4c]      ; this+0x4c
0x0012a2a6: mov  r3, sb; blx #0x0010f05c  ; (iteration body)
0x0012a2b4: ldr  r3, [r4]; ldr r3, [r3, #8]; blx r3   ; vtable slot +0x8 call
```

**Routing into the chain from views** (as in a6400): `ViewBase::Main` (`0x0013bc80`) → `bl #0x0013bb1c` (routing helper) → on the masked-event path: `bl #0x0012a4f6` (builds a **local** `ActionCheckerListBase` on the stack via `0x0012a516` / `0x0012a504` / `0x0012a4e8`), then `vtable +0x48` call, and (path `0x0013bbf2`) inline event posting.

**Important negative finding:** there is **no contiguous vtable for `ActionCheckerListBase` in this module** — no `R_ARM_RELATIVE` relocation anywhere in the file points at `0x0012a261` (IsReadyExec), `0x0012a65d` (IsUpdateEvent), `0x00129fab` (ExecEv) or `0x0012b8bd` (execAction). `grep`-style raw search for those VAs finds only `.gnu.hash`/`.dynsym` byte coincidences. Same for `ViewBase::Action/Main/Entry/Exit` — **no vtable slot in libObj.so references them**. Consistent with these classes being abstract/weak-emitted (vtables emitted in the instantiating TU, e.g. `viewUnified2.so` for `ViewSettingMenu`, as on a6400) — and with "no RTTI/vtable for `CmnViewSettingNode` here either" (Item 5). Verify before relying on any concrete vtable layout in this file.

**RTTI-ish objects present** (Sony custom typeinfo; kind byte `0x08`, not Itanium `__cxxabiv1`), useful for class identification:

| Name | VA (=file off for seg 1; file = VA−0x8000 for seg 2) |
|---|---|
| `_ZTIN11DefRootView21ActionCheckerListBaseE` | VA `0x01389990` (file `0x01381990`); points to name string `0x00e4c043` = `N11DefRootView21ActionCheckerListBaseE` and `0x00e4c06a` = `21ContentListManagerKey`; bytes: `08 00 00 00 43 c0 e4 00 08 00 00 00 6a c0 e4 00 6c f1 38 01` |
| `_ZTIN11DefRootView11CheckerBaseE` | VA `0x0138ec08` (file `0x01386c08`); reloc at `0x0138ec18` → `CheckerBase::D2Ev` `0x00169fc5` |
| `_ZTIN11DefRootView18ActionEventMapBaseE` | VA `0x0138aa90` (file `0x01382a90`); name `0x00e4ca9d` |
| `_ZTI8ViewBase` | VA `0x0138add8` (file `0x01382dd8`); name `0x00e4cb65`=`8ViewBase`; bytes `08 00 00 00 65 cb e4 00 00 00 00 00 02 00 00 00 00 00 00 00 02 00 00 00 00 8e 38 01 02 28 00 00` |
| `_ZTI4View` | VA `0x013c2178` (file `0x013ba178`); `{8, reloc→0x01033ea5 ("4View"), reloc→0x0138c648}` |
| `_ZTIN11DefRootView21ActionCheckerListBaseE`-adjacent vtable-like clusters | FUNC clusters at `0x013898c8..0x01389904` (16 ptrs, 0x00128xxx range), `0x01389918..0x01389928`, `0x01389948..0x01389968`, `0x01389978..0x0138997c` (`0x0012aa45` = `ActionCheckerListBase::D2Ev`, `0x0012aa9d`) |

`_ZTIN11DefRootView21ActionCheckerListBaseE` itself has **no incoming references** — weak RTTI never used in this module.

**Chain entry points, summary:** `DefRootView::ActionCheckerListBase` methods exist and are exported (dynsym). The "View activation chain" into them is: `ViewBase::Main` → routing helper `0x0013bb1c` (event-id dispatch) → [local checker list init `0x0012a4f6`] → vtable slot `+0x48` / inlined event posts; `IsReadyExec`/`IsUpdateEvent` are reached from the local list object via its (external) vtable — **not** through a vtable physically present in this file.

---

## Item 3 — Event-manager global pointer and deref chain

**The global: `g_pEvtMgr`** — exported object (`STB_GLOBAL`, `STT_OBJECT`, size 4), `.bss` @ VA **`0x014920e0`** (seg 2, NOBITS → no file bytes). Export index: sym 2396 in `.dynsym`.

**Access mechanisms (both verified in the file):**

1. **GOT slot + double deref** (the pattern used by all in-file helpers):
   - GOT slot VA **`0x013ded14`** (file off `0x013d6d14`), byte offset `0x70ec` from `DT_PLTGOT` = `.got` base `0x013d7c28`.
   - Relocation at `0x013ded14`: **`R_ARM_GLOB_DAT` (type 21), sym `g_pEvtMgr`** → at load time the slot becomes `&g_pEvtMgr`.
   - Computed with pc-relative **delta literals** (base-independent; delta `0x0128ab96`-style values + `pc` = `.got` base exactly — verified: `0x0014d092 + 0x0128ab96 = 0x013d7c28` ✓, and `0x0012c636 + 0x012ab5f2 = 0x013d7c28` ✓).
2. **ABS32 (type 2) literal directly in .text** at **`0x0041ba60`** (file = VA, RX segment, reloc type 2 sym 2396) — a direct `&g_pEvtMgr` literal, patched at load. Used by the function at **`0x0041ba3c`** (see below).

**Code idiom A — "post event to manager" helpers (two byte-identical clones, LTO/inline duplicates):**

`0x0014d07c` (clone `0x0014cff8`), bytes `2d e9 f0 43 85 b0 04 af 06 46 1e 4a 00 af 1e 4d 0b 46`:

```
0x0014d07c: push.w {r4,r5,r6,r7,r8,lr}
0x0014d080: mov  r4, r2
0x0014d082: ldr  r2, [pc, #0x70]   ; 0x11012004 (event-id magic; cmp/beq path)
0x0014d086: ldr  r5, [pc, #0x70]   ; delta -> r5 = .got base (0x013d7c28)
0x0014d088: mov  r6, r0
0x0014d08a: cmp  r1, r2            ; arg1 == 0x11012004 ? special path (cbz/bl ParamListD1, delete)
0x0014d08e: add  r5, pc
0x0014d092: ldr.w r1, [r6, #0x14c] ; view->field_0x14c   (<=== the a6400 "view+0x14c" field!)
0x0014d098: mov.w r0, #0x13000000  ; event-id class base
0x0014d09c: bl   #0x00117788       ; pack id: if ((r1 & 0x1fe0 | 0x...))==0: id = 0x13000000 + (arg<<12) + r2
0x0014d0a0: mov  r8, r0
0x0014d0a2: movs r0, #0x10         ; 16 bytes
0x0014d0a4: blx  #0x00108e0c       ; PLT -> _Znwj(16)   (operator new)
0x0014d0a8: mov  r1, r8            ; id
0x0014d0aa: movs r2, #4            ; h1 = 4
0x0014d0ac: movs r3, #0            ; h2 = 0
0x0014d0b0: blx  #0x001082b8       ; PLT -> _ZN5EventC1Emhh  (Event ctor, see Item 4)
0x0014d0b4: mov  r0, r6            ; this = ev
0x0014d0b6: mov  r1, r4            ; ParamList* (2nd arg of the helper)
0x0014d0b8: blx  #0x0010b308       ; PLT -> _ZN5Event12setParamListEP9ParamList
0x0014d0bc: ldr  r3, [pc, #0x3c]   ; 0x000070ec
0x0014d0be: mov  r1, r6            ; ev
0x0014d0c0: movs r2, #1
0x0014d0c2: ldr  r3, [r5, r3]      ; r3 = [.got + 0x70ec]  = &g_pEvtMgr   (GLOB_DAT)
0x0014d0c4: ldr  r0, [r3]          ; r0 = g_pEvtMgr         (**load global; load [global]**)
0x0014d0c6: pop.w {r4,r5,r6,r7,r8,lr}
0x0014d0ca: b.w  #0x0010c2a8       ; PLT -> EventManager::push(mgr, ev, 1)
; literal pool @0x0014d0f8: 0x0128ab96 (delta to .got), 0x000070ec
```

**Code idiom B — plain event-push helper** `0x0012c62c` (called via tail `b.w` at `0x0012c642`, i.e. Sony's "post to manager" trampoline used elsewhere in the framework):

```
0x0012c62c: ldr  r3, [pc, #0x18]   ; 0x012ab5f2 (delta)
0x0012c62e: mov  r1, r0             ; ev
0x0012c630: ldr  r2, [pc, #0x18]   ; 0x000070ec
0x0012c632: add  r3, pc             ; r3 = .got base (0x0012c636+0x012ab5f2 = 0x013d7c28)
0x0012c634: push {r7, lr}; add r7, sp, #0
0x0012c638: ldr  r3, [r3, r2]       ; r3 = [.got + 0x70ec] = &g_pEvtMgr
0x0012c63a: movs r2, #1
0x0012c63c: ldr  r0, [r3]           ; r0 = g_pEvtMgr
0x0012c63e: pop.w {r7, lr}
0x0012c642: b.w  #0x0010c2a8        ; EventManager::push(mgr, ev, 1)
```

**Code idiom C — the direct ABS32 global (a6400 `rd32(g_pEvtMgr)` analog), event-id lookup** `0x0041ba3c`:

```
0x0041ba3c: ldr  r3, [pc, #0x20]   ; 0x0041ba60 : 0x00000000 + ABS32 reloc(type 2) -> &g_pEvtMgr
0x0041ba3e: movs r2, #1
0x0014d040/push...
0x0041ba44: ldr  r0, [r3]          ; r0 = g_pEvtMgr
0x0014d046/ldr r1, [pc, #0x1c]     ; 0x16020001 (event-id magic)
0x0041ba48: bl   #0x0018b6         ; unnamed EventManager method right after
                                   ;   _ZN12EventManager5countEj (0x0012a18c4: cmp r2,#3; bhi;
                                   ;   r6 = [vptr + r2*4]  = vtable slot #1; iterates handlers;
                                   ;   calls Event::getId() per entry)
0x0041ba4c: cbnz r0, #0x0041ba5e   ; found -> return
0x0041ba4e: movs r1, #2; ldr r0, [pc,#0x10]  ; r0 = 0x16020001
0x0041ba52: bl   #0x0012c650       ; creates event (16-byte) of id 0x16020001, param=2
0x0041ba5a: b.w  #0x0012c62c       ; tail -> idiom B
```

**Inside `EventManager::push` itself** the "load mgr → dispatch through vtable" pattern repeats: `sw` `r8` = result of `Event::getId`-style classification (`bl #0x001680` → cmp r0,#1 / cmp r0,#3), then `ldr r2,[r4]` (vptr), `ldr.w r0,[r2, r8, lsl #2]`, `blx r3` — i.e. **vtable slot `r8`** (1 or 3) called on the manager. This is exactly the a6400 `"load global; load [mgr]; call [vtable+X]"` shape — already inside the member, no separate veneer needed.

**Does any code WRITE `g_pEvtMgr`?** No. Relocations referencing sym 2396: only `0x0041ba60` (ABS32, read) and `0x013ded14` (GLOB_DAT, read). The manager is created/assigned in **another module** (Sony app core; `libObj.so` only exports the pointer for others to fill). Also note two "sibling" manager globals in the same `.bss` block: `timerManager` @ VA `0x014920e4` (its own GOT slot `0x013dbeb8`) and **`pViewManager`** @ VA `0x0149221c` (GOT slot `0x013dc7b4`) — analogous pattern, equally uninitialized in this module.

**`EventManager::push` PLT stub mapping** (done precisely; see `pltmap.py`):
`0x0010c2a8` = `12 c6 8f e2 cc ca 8c e2 a8 fe bc e5` (ARM) → GOT `0x013d915c` → **`_ZN12EventManager4pushEP5Eventb`** (`.rel.plt` JUMP_SLOT @ VA `0x013d915c`).

---

## Item 4 — Global operator new / event ctor / event push

| Function | File offset = VA | Size | Confidence |
|---|---|---|---|
| global `operator new(unsigned int)` → `_Znwj` | **imported**, PLT stub **`0x00108e0c`** → GOT `0x013d80a8` (JUMP_SLOT @ `.rel.plt` r_offset `0x013d80a8`, sym idx 204) | — | High |
| `_ZN5EventC1Emhh` — **`Event::Event(unsigned long, unsigned char, unsigned char)`** | **`0x0013a01`→`0x0008a01e8`** | 0x36 | High |
| `Event::Event(Event const&)` (copy) | `0x0008a021f` | 0x20 | High |
| `Event::setParamList(ParamList*)` | `0x0008a0269` | 0x22 | dynsym |
| `_ZN12EventManager4pushEP5Eventb` — **EventManager::push(Event*, bool)** | **`0x0008a1794`** | 0xb4 | High |
| `_ZN12EventManager5countEj` | `0x0008a1849` | 0x22 | dynsym |
| PLT stub for push (used by all helpers) | `0x0013a2a8`→**`0x0010c2a8`** | ARM stub | High |

**Global operator new:** `_Znwj` is **imported** (undefined dynsym, SHN_UNDEF); this module defines **no** local `operator new`/`operator delete` symbols (verified: zero defined non-undefined `_Znwj/_ZdaPv/_Znaj` in `.dynsym`; only unrelated `InputService*_new*` names matched). Every event allocation goes through `bl/blx #0x00108e0c` (11,921 immediate `blx` sites in `.text`). PLT stub bytes @ `0x00108e0c`: `12 c6 8f e2 cf ca 8c e2 a0 f2 bc e5` (ARM) → `add ip,pc,#0x1200000; add ip,ip,#0xcf000; ldr pc,[ip,#0x2a0]!` → GOT `0x013d80a8` = `_Znwj`.
(Note: a6400 doc's "global operator new at libObj ~+0x16e974" — no equivalent local function exists here; the a6400 symbol was likely also an import/veneer. No local candidate found.)

**Event ctor** `0x008a01e8`, bytes `b0 b5 04 46 41 60 00 af 02 72 43 72 14 20 68 f4 0a e6 00 21 05 46 6d f4 3c e3 e5 60 04 20 68 f4 02 e6 01 23 03 60 20 60 20 46`:

```
0x008a01e8: push {r4, r5, r7, lr}
0x008a01ea: mov  r4, r0              ; this (Event*)
0x008a01ec: str  r1, [r0, #4]        ; this->id   = id      (u32 @ +0x4)
0x008a01f0: strb r2, [r0, #8]        ; this->b_8  = type    (u8  @ +0x8)
0x008a01f2: strb r3, [r0, #9]        ; this->b_9  = dest?   (u8  @ +0x9)
0x008a01f4: movs r0, #0x14           ; 20 bytes
0x0013d878/bl 0x0010d878             ; PLT -> ParamList::ParamList(0)   (ParamList*)
0x008a01fe: blx #0x0010d878
0x008a0202: str  r5, [r4, #0xc]      ; this->params = ParamList(0)     (@ +0xc)
0x008a0204: movs r0, #4              ; 4 bytes
0x00108e0c/bl 0x00108e0c             ; PLT -> _Znwj(4)
0x008a020a: movs r3, #1
0x008a020c: str  r3, [r0]            ; *cell = 1  (refcount = 1)
0x008a020e: str  r0, [r4]            ; this->refcount_cell = cell      (@ +0x0)
0x008a0212: pop  {r4, r5, r7, pc}
```

So `Event` layout: `{ refcnt_cell* @0x0 (points to a 4-byte cell, init 1), id @0x4, type @0x8, dest @0x9, ParamList* @0xc (created empty) }` — sizeof(Event) = 16 = `0x10`, matching the `_Znwj(0x10)` in every event-posting helper.

**Event push** `EventManager::push(Event* ev, bool flag)` @ `0x008a1794`, bytes `2d e9 f0 41 84 b0 04 46 02 af 08 46 0d 46 16 46`:

```
0x008a1794: push.w {r4-r8, lr}; sub sp, #0x10
0x008a179a: mov  r4, r0            ; this (manager)
0x008a179e: mov  r0, r1; mov r5, r1; mov r6, r2
0x008a17a4: bl   #0x008a1680       ; classify event -> r8 (1 / 3 / other)
0x008a17a8: cmp  r0, #1; beq #0x008a17b2
0x008a17ae: cmp  r0, #3; bne #0x008a1820
0x0013b2b2: (r8==1 or 3): mov r0, r4; bl #0x008a16ea   ; lock
0x008a17b8: ldr  r2, [r4]; ldr r3, [r4, #8]
0x0013b7be: ldr.w r0, [r2, r8, lsl #2]; blx r3          ; handler[r8](ev)   (vtable slot r8)
0x008a17c4: cmp.w r8, #1; bne #0x008a17d4
0x0013b7ca: [r8==1]: ldr r0, [r2, #8]; blx r3           ; extra slot-2 call
0x008a17d4: ldr.w r0, [r3, r8, lsl #2]; bl #0x00089fed6 ; notify
0x008a17e0: bl   #0x00089feca
0x0013b7ea: movs r1, #0x64         ; 100
0x008a17ee: str  r0, [r7, #4]; bl #0x0013b320?? -> bl #0x00e49edc
0x0013b7fc: ldr  r3, [pc, #0x48]; add r3, pc            ; string ("CONTENT_TYPE\n"-family)
0x0013b81a: bl   #0x00139938 -> bl #0x00089f938
0x008a1812: mov  r0, r4; bl #0x008a16f8                ; unlock
0x008a1818: cbz  r6, #0x008a1838
0x008a181a: ldr  r3, [r4, #4]; blx r3                  ; if (flag) [this+4](ev)   (extra notifier)
0x008a1838: movs r0, #0; add.w r7, r7, #8; mov sp, r7; pop.w {r4-r8, pc}
```

**The "Sony eventing" composite used in this build** (the a6400 "event ctor(ev, event_id, type, 1) + evmgr push" pattern), concretely — from helper `0x0014d07c` (Item 3, idiom A):
`_Znwj(0x10)` → `Event::Event(id=0x13000000|(field<<12)|bits, type=4, dest=0)` → `Event::setParamList(ev, paramlist)` → `EventManager::push(g_pEvtMgr, ev, 1)`.

Byte dumps for the chain: ctor `0x008a01e8..0x008a0212` above; push stub `0x0010c2a8: 12 c6 8f e2 cc ca 8c e2 a8 fe bc e5`; push entry `0x008a1794: 2d e9 f0 41 84 b0`; helper `0x0014d09c: ca f7 b6 fb` (`bl #0x117788`).

---

## Item 5 — `CmnViewSettingNode` vtable: **NOT in libObj.so — home is `CautionConfig.so`** (verified cross-module)

**libObj.so contains no trace of the class** (exhaustive checks, all zero hits):

- Strings (whole 21 MB): `CmnViewSettingNode`, `ViewSettingNode`, `17CmnViewSettingNode`, `CmnViewSetting`, `setItemSelectedBySRNum`, `ItemSelected`, `getLayerDepth`, `LayerDepth`, `SRNum`, `getCountSelectable`, `Selectable`, `SettingNode` — **absent**.
- `.dynstr` symbols: only `CmnViewMenuData*` (menu item-struct helpers); no `CmnViewSettingNode` / node classes with `SRNum`/`intProperty` patterns. (`grep` in dynsym file: 46 `CmnView` hits, all `CmnViewMenuData`.)
- RTTI-ish names in `.rodata`: `17CmnViewMenuDataIF` (`0x00e4db6b`), `15CmnViewMenuData` (`0x00e4db81`), `gfxeng::Node` — no `CmnViewSettingNode` record.
- Only `getSubItem`-family hit: `_ZN15CmnViewMenuData10getSubItemEPK21CmnViewMenuItemStructiPPS0_` @ `0x0015b0be` — unrelated.
- Vtables defined in libObj.so (FUNC clusters at `0x013898c8..0x0138997f`, RTTI `_ZTI8ViewBase` @ VA `0x0138add8`, `_ZTI4View` @ VA `0x013c2178`, `_ZTIN11DefRootView*`) — **none of these are `CmnViewSettingNode`**; no relocation in libObj.so targets `CmnViewSettingNode*` symbols (they are referenced only as **undefined** symbols — i.e., imported from another module).

**Where it actually lives (all libraries in `<workspace>\zv-e10-flasha15\lib\` scanned):**

- **`CautionConfig.so`** — defines **23,476** `CmnViewSettingNode*` symbols, including the base class. ET_DYN, entry `0x00b85b24`; seg1 R+X VA==file off; **seg2 delta +0x8000** (`VA = file + 0x8000`).
  - `_ZTV18CmnViewSettingNode` @ VA **`0x00fab960`** → **file offset `0x00f33960`**, size `0x10c` (67 words: slot0=0, slot1=`_ZTI…`, slots 2..66 = methods).
  - `_ZTI18CmnViewSettingNode` @ VA `0x00fab954` (file `0x00f33954`), size 8; `_ZTS18CmnViewSettingNode` @ VA `0x00dd74dc`.
  - `_ZN18CmnViewSettingNodeC1EPPS_iPK24CmnViewSettingProperties` @ `0x00b8eb21` (+0x24); `D1Ev` @ `0x00b8ea3d`; `15initSettingNodeEPPS_i` @ `0x00b8dffb`; `4initEPPS_ii` @ `0x00b8e83d` (+0x120).
- **`viewUnified2.so`** — defines only the PAS_* menu-tab classes (e.g. `PAS_MenuListTab::openLevel2Popup` @ `0x0086292d`, `PAS_DialSelect`…); its `.dynstr` lists **UNDEFINED** `_ZN18CmnViewSettingNodeC1EPPS_iPK24CmnViewSettingProperties`, `…D1Ev`, `…15initSettingNode…`, `g_pEvtMgr`, `_ZN12EventManager4pushEP5Eventb` — all imported from elsewhere (CautionConfig.so / libObj.so).
- No other library in the dump defines `CmnViewSettingNode` (scan included `scenario/*.so`, `tool/*.so`; many were unparseable/`Magic number does not match` — Sony-packed — but none of the parseable ones define the base class).

**Slot map (file `0x00f33960`, ABS32 relocations with symbol references — this file uses `R_ARM_ABS32`, not RELATIVE):**

| slot | off | symbol |
|---|---|---|
| 1 | +0x04 | `_ZTI18CmnViewSettingNode` |
| 2 | +0x08 | `_ZN18CmnViewSettingNodeD1Ev` |
| 3 | +0x0c | `_ZN18CmnViewSettingNodeD0Ev` |
| 4 | +0x10 | `…15initSettingNodeEPPS_i` |
| 6 | +0x18 | `…18getCountSelectableERi` ← getCountSelectable |
| 7 | +0x1c | `…11getCountAllERi` |
| 10 | +0x28 | `…17getSubItemByIndexEiPPS_` |
| 11 | +0x2c | `…17getSubItemBySRNumEiPPS_` |
| 12 | +0x30 | `…15getSelectedItemEPPS_` |
| 13 | +0x34 | `…20getIndexSelectedItemERi` |
| 24 | +0x60 | `…13getLayerDepthERi` ← **getLayerDepth** |
| 25 | +0x64 | `…14getIntPropertyEiRi` |
| 37 | +0x94 | `…16isItemSelectableEv` |
| 39 | +0x9c | `…14isItemSelectedEv` |
| 41 | +0xa4 | `…21isItemSelectedBySRNumEi` |
| 48 | +0xc0 | `…15setItemSelectedEv` |
| 49 | +0xc4 | `…22setItemSelectedByIndexEi` |
| 50 | +0xc8 | `…22setItemSelectedBySRNumEi` ← **setItemSelectedBySRNum** |
| 58 | +0xe8 | `…17updateSettingNodeEv` |
| 59 | +0xec | `…5cloneEv` |
| 64 | +0x100 | `…4initEPPS_ii` |
| 65 | +0x104 | `…19setHeadItemSelectedEv` |
| 66 | +0x108 | `…11updateSRNumEv` |

**vs a6400 (`0523` doc):** a6400 slot 48 = `setItemSelectedBySRNum`; **ZV-E10 slot 50 (+0xc8) = `setItemSelectedBySRNum`** (2-slot shift: ZV-E10 inserted `isItemSelected`/`isItemSelectedByIndex`/`isItemSelectedBySRNum` as slots 39–41). getLayerDepth = slot 24 (+0x60). Field layout (+0x14 depth, +0x18 sel, +0x1c SR, +0x20 idx, +0x24 state) is presumed identical (same class, same build system) but was not re-verified field-by-field in this task.

---

## Item 6 — RX zero-padding regions (for future loader placement)

RX segment = `PT_LOAD` seg 1, file `0x00000000..0x0135ddd4` (vaddr identical). Zero-run scan (exact bytes, runs ≥ 64 B):

| Region | Result |
|---|---|
| `.text` `0x0010fee0..0x00e4af08` | **0 runs ≥ 64 B** |
| `.ARM.extab` `0x011e2a14..0x012e4fe4` | **0 runs ≥ 64 B** |
| `.ARM.exidx` `0x012e4fe4..0x0135ddd4` | **0 runs ≥ 64 B** |
| `.rodata` `0x00e4af10..0x011e2a14` | **153 runs ≥ 64 B** (all listed below) |
| `.plt`/`.init` | included in `.text` scan (0 large) |

**Largest RX zero regions (all in `.rodata`):**

| File offset = VA | Size | Bytes |
|---|---|---|
| **`0x011964ee`** | **0x3f7e = 16,254** | previous non-zero byte `0x18` @ `0x011964ed` (last word `f3 18 00 00`); next non-zero `0x41` @ `0x0119a46c` (index-table `41 09 00 00…` = `0x00000941…`). RX at runtime (`PT_LOAD` flags 5). **Not 4K/16K aligned** (`0x…4ee`); loader would need to carve a sub-range, e.g. `0x01196500..0x0119a000` ≈ 15,104 B |
| `0x011956e6` | 0x0d96 = 3,478 | `27 14 00 00…` pattern before |
| `0x010b5511` | 0x06db = 1,755 | |
| `0x0110ede6` | 0x06da = 1,754 | |
| `0x01112b3a` | 0x06da = 1,754 | |
| `0x010b6241` | 0x05ff = 1,535 | |
| `0x0119cc9a` | 0x052a = 1,322 | |
| `0x0119d6ea` | 0x047e = 1,150 | |
| `0x010325f8` | 0x0401 = 1,025 | |
| `0x01032a07` | 0x0401 = 1,025 | stride `0x40f` — triple-strided zero table |
| `0x01032e16` | 0x0401 = 1,025 | |
| `0x0119b456` | 0x0337 = 823 | |
| `0x01193f4a` | 0x02e6 = 742 | |
| `0x0119c816` | 0x02e6 = 742 | |
| `0x01194536` | 0x02de = 734 | |
| `0x01194b1a` | 0x02de = 734 | |
| `0x0119a986` | 0x02c6 = 710 | |
| `0x00fb8581` | 0x0203 = 515 | |
| `0x00fad61b` | 0x0201 = 513 | |
| `0x010b3ee1` | 0x01fb = 507 | |
| `0x0119bb5e` | 0x01e6 = 486 | |
| `0x0110d7ce` / `0x01111522` | 0x01e2 = 482 | (two occurrences, same |size) |
| `0x00f05b8d` | 0x01db = 475 | |
| `0x0119525e` | 0x01be = 446 | |

`.rodata` ends at `0x011e2a14`; the biggest runs sit in the `0x0119xxxx` band, interleaved with index tables of 16/32-bit values (e.g. at `0x0119a470`: `42 09 00 00 43 09 00 00 4b 09 …` = `0x00000942…`). These are almost certainly **table payload zeros / padding inside constant data** (e.g., lookup tables or bitmap atlases), not guaranteed-dead space: **they must not be assumed free for loader placement without verifying no table dereferences them**. Treat `0x011964ee (16,254 B)` as a *candidate* only.

Segment 2 (RW, **not** executable) is excluded by definition; `.bss` is 0x3fb54 bytes of NOBITS zeros.

**Honest bottom line for Item 6:** this build has **no large zero padding inside `.text` at all** (dense code); the largest RX zero region is 16 KB in `.rodata` and is of unknown provenance; the a6400-style "large NOP gaps in executable code" do not exist here. If the loader needs guaranteed-safe space, it must come from `.bss`/`.data` (RW) or from a new segment — **not** from `.text`.

---

## Load-base / offset mapping notes

1. Segment 1: `VA == file offset` for **all** items above (`.text`, `.rodata`, `.plt`, `.init`).
2. Segment 2 (`NX`… actually RW): `VA = file off + 0x8000` — applies to `_ZTI*`/vtable-like clusters, `.got`, `.data`, `.bss` addresses (e.g. file `0x01381990` = VA `0x01389990`).
3. `DT_PLTGOT` = VA `0x013d7c28`; GOT slot `0x013ded14` = byte offset `0x70ec`; `DT_REL` = `0x0003ade0`, `DT_RELSZ` = `0x000c8188`; `DT_JMPREL` = `0x00102f68`, `DT_PLTRELSZ` = `0x5060`.
4. Relocations: `R_ARM_RELATIVE` (type 23) covers all code/data pointers; `R_ARM_GLOB_DAT` (type 21, 49 entries) covers exported objects like `g_pEvtMgr`, `timerManager`, `pViewManager`, `ViewBase`'s `_ZTV*`; `R_ARM_ABS32` (type 2, 22,559 entries) for absolute data literals (e.g., `0x0041ba60` → `g_pEvtMgr`); `R_ARM_TARGET1`(21)-named "GLOB_DAT" confusion aside — type 21 is used for both GLOB_DAT targets (`21` = R_ARM_GLOB_DAT on ARM).
5. Runtime address of any link-time VA: `base + VA`. All "file offset = VA" statements in Items 1–4 are valid only with this rule. Load base unknown.
6. Literal pools: verified two independent absolute-to-`.got`-base deltas (e.g. `0x0128ab96`, `0x012ab5f2`, `0x0129c0fa` all + their `pc` = exactly `0x013d7c28`) and one event-id constant family `0x1100xxxx` / `0x12000000` / `0x13000000` / `0x16020001` / `0x17008000` (Sony event-id class space). The Sony loader does **not** need to patch these deltas.
7. `vtable+0xa0` (Action) vs a6400 `+0x94`: **ZV-E10 ViewBase has 3 extra virtual slots**; same relationship seen elsewhere is *not* assumed anywhere else — all other slots quoted in this report were measured, not inferred.

---

## Cross-check table vs a6400 (ILCE-6400) background notes

| a6400 (file offset) | ZV-E10 | Status |
|---|---|---|
| ViewBase::Action `+0x3ebef0` → vptr `+0x94` | **`0x001393a8`** → vptr **`+0xa0`** | found; +3 slots |
| ViewBase::Main `+0x3edd4c` | **`0x0013bc80`** | found |
| Main helper `+0x3edc04` | **`0x0013bb1c`** (event-id dispatch: `0x1100400b`/`0x11014001`/`0x12xxxxxx`/`0x17xxxxxx`) | found |
| `DefRootView::ActionCheckerListBase` (IsReadyExec/IsUpdateEvent) | **IsReadyExec `0x0012a260`, IsUpdateEvent `0x0012a65c`** (dynsym) | found; vtable not emitted in module |
| Event-manager global (`rd32 0x07089984`-ish, `mgr = *(mgr+0)`) | **`g_pEvtMgr`** @ VA `0x014920e0` (.bss, 4 B, exported) | found; +GOT slot `0x013ded14` (`0x70ec` from `.got` base `0x013d7c28`), GLOB_DAT; ABS32 literal `0x0041ba60`; double deref `⟹ mgr = [g_pEvtMgr]` |
| Global operator new `+0x16e974` | **imported `_Znwj`**, PLT `0x00108e0c` → GOT `0x013d80a8` | found (no local def) |
| Event ctor `~+0x14e20`-ish | **`Event::Event(m,h,h)` `0x008a01e8`** (refcnt=1 @+0x0, id @+0x4, type @+0x8, ParamList* @+0xc, sizeof 0x10) | found |
| Event push `~+0x18d5d` | **`EventManager::push` `0x008a1794`** (PLT `0x0010c2a8`) | found |
| `CmnViewSettingNode` (via CautionConfig.so there) | not in libObj.so; **home `CautionConfig.so`** (`_ZTV18CmnViewSettingNode` VA `0x00fab960` / file `0x00f33960`, slot 50 = `setItemSelectedBySRNum`, slot 24 = `getLayerDepth`) | located (Item 5 addendum) |
| RX zero padding | none in `.text`; largest `0x011964ee` 16,254 B (`.rodata`) | found |

---

## Addendum — cross-module findings (parent-agent brief)

### A. `WidgetSystem` singleton chain (sys_getInstance equivalent; a6400 `…0x956f0`)

`_ZN2ux6wgtsys12WidgetSystem15sys_getInstanceEv` @ **`0x00616fe4`** (Thumb, exported, size 0x18 — libObj.so):

```
0x00616fe4: ldr      r3, [pc, #0xc]   ; 0x00dc0c3c  (delta)
0x00616fe6: ldr      r2, [pc, #0x10]  ; 0x00007608  (index)
0x00616fe8: add      r3, pc           ; r3 = .got base   (0x00616fec + 0x00dc0c3c = 0x013d7c28 ✓)
0x00616fee: ldr      r3, [r3, r2]     ; r3 = [.got + 0x7608]  → slot VA 0x013df230
0x00616ff0: ldr      r0, [r3]         ; r0 = *slot            (**load global; load [global]**)
0x00616ff2: pop      {r7, pc}
```

- GOT slot VA **`0x013df230`** (file off `0x013d7230`); relocation: **R_ARM_RELATIVE, addend `0x0145cb1c`** → points into `.bss`.
- **`.bss` slot @ VA `0x0145cb1c`** (unexported, 4 B, in seg2, NOBITS) — holds the WidgetSystem singleton instance pointer (same GOT→`&global`→`*global` idiom as `g_pEvtMgr`).
- Exported via libObj.so dynsym (`WidgetSystem::sys_getInstance` @ `0x00616fe4|1`); **viewUnified2.so imports it** (undefined there) along with `_ZN5EventC1Emhh`, `EventManager::push`, `g_pEvtMgr`, `Event::setParamList`, `_Znwj` — the libObj ↔ viewUnified2 framework seam is confirmed by import/export symmetry, not just by proximity.
- Same pattern siblings in libObj `.bss`: `g_pEvtMgr` @ `0x014920e0`, `timerManager` @ `0x014920e4`, `pViewManager` @ `0x0149221c`.

### B. Event-framework symbol placement (op_new / ev_ctor / evmgr_push equivalents)

Sony's names here are the stock Itanium/libstdc++ ones: **`op_new` = `_Znwj`** (imported, PLT `0x00108e0c`, GOT slot `0x013d80a8`, JUMP_SLOT @ `.rel.plt` r_offset `0x013d80a8`, sym idx 204); **`ev_ctor` = `_ZN5EventC1Emhh`** @ `0x008a01e8` (exported) / `Event::Event(const&)` @ `0x008a021f`; **`evmgr_push` = `EventManager::push` (`_ZN12EventManager4pushEP5Eventb`)** @ `0x008a1794` (exported) with its PLT stub `0x0010c2a8` (GOT slot `0x013d915c`, `.rel.plt` JUMP_SLOT @ `0x013d915c`). libObj.so additionally exports `g_pEvtMgr`, `EventManager::count` (`0x008a1849`), `Event::setParamList`, `Event::addParameter`, `Event::setDest`, `Event::getId/getDest`, `TimerEvent`, `View`, `LkmBase`, `UtilityBase`, `LayoutableWidgetBase` etc. **No `op_new`/`ev_ctor`-style strings exist**; the strings `EventManager`/`g_pEvtMgr` appear only as symbol names in `.dynstr` (and in viewUnified2.so's `.dynstr`, as imports).

### C. Zero-padding for loader placement (final, precise)

`0x011964ee..0x0119a46c` (16,254 B, VA==file off, `PT_LOAD` R+X, `.rodata`) is inside a mapping the ZV-E10 process owns, but it is **not** loader-exclusive and **not** aligned; before it ends a 32-bit index table (`0x00000941, 0x00000942…`) at `0x0119a46c`, and the preceding 4 bytes are `f3 18 00 00`. Practical home: sub-range `0x01196500..0x0119a000` (~15,104 B) or a fresh segment via the loader's own RX region. *Do not write into it until its consumers are verified* (it may back hot tables; the Sony loader/live-RAM would detect corruption of static data only if something reads it). runner-ups: `0x011956e6` (3,478 B), `0x010b5511` (1,755 B), `0x0110ede6`/`0x01112b3a` (1,754 B each), `0x010b6241` (1,535 B), all `.rodata`.

No such padding exists in `.text`/`.ARM.extab`/`.ARM.exidx` (0 runs ≥ 64 B).

---

## Honest gaps / not located

1. **`CmnViewSettingNode` / menu-node vtable** — entirely absent from libObj.so; home is `CautionConfig.so` (see Item 5). Its slot table and base RTTI were located there, not in libObj.so.
2. **`ViewBase` concrete vtable, `ActionCheckerListBase` vtable, `ViewSettingMenu` vtable (`+0x100` dispatcher)** — not emitted in libObj.so; live in other modules (per the a6400 `viewUnified2.so` evidence). Therefore the exact ZV-E10 slot for the "ProductAction/dispatcher" cannot be measured here; the `+0xa0` Action slot *is* measured (it is consumed by `ViewBase::Action` itself).
3. **Writer/initializer of `g_pEvtMgr`** — not in this module (no stores found; only load-side xrefs). Manager instance is created in another module; libObj only exports the pointer.
4. **The 16 KB `.rodata` zero run** at `0x011964ee` — cannot prove dead space vs in-use zeros of a table.
5. **`.bss` VA `0x01459328`** — all addresses after `.data` map only with the +0x8000 delta; no file bytes exist; runtime contents unknown.
6. **link base unknown** — everything here is link-time VA; ZV-E10 could relocate it anywhere. Do not hardcode any absolute VA.
7. The unnamed EventManager method at `0x008a18b6` (called with `(mgr, 0x16020001, 1)`) — internal, not exported; behavior (event-id search across manager's handler array, `cmp r2,#3; bhi`, `r6 = [vptr + r2*4]`, `Event::getId()` compare) was identified but not fully reverse-engineered.
8. `0x00111158`, `0x00144c8c`, `0x001390aa`, `0x0012a4f6`, `0x0012a4e8` (helpers called by Main / its helper) — internal, unnamed; characterized only by call context.

*Analysis performed on the file read-only; nothing outside `<workspace>\zv-e10-flasha15\` was modified.*
