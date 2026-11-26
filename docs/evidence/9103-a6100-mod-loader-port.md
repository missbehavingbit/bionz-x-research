# E9103 — Porting Mod Loader v2 to the ILCE-6100: first milestone

- **Date:** 2026-11-21
- **Scope:** offline. Static comparison of the a6100 V1.00 `viewUnified2.so` with the
  ILCE-6400 2.00 build the loader sources target. No camera. No mod executed yet.
- **Code:** [tools/native_camera/mlv2/a6100_layout.py](../../tools/native_camera/mlv2/a6100_layout.py),
  tests [tests/test_mlv2_a6100_layout.py](../../tests/test_mlv2_a6100_layout.py).

## Question

Can the existing Mod Loader v2 (shim + core + SMOD v2 mods) be retargeted to the a6100
emulator build, and can the existing mods (`MenuTouchFull`, `Arrivederci`) run on it?

## Result: the three view hooks are byte-identical, shifted by +0x15b8

The loader's shim is reached through three trampolines that replay the first bytes of
three `viewUnified2.so` functions (`mlv2_trampoline.S`). Those functions are the same code
in the a6100 build:

| Hook | a6400 offset | a6100 offset | delta | replayed bytes |
| --- | --- | --- | --- | --- |
| `set_root` | `0x5ab654` | **`0x5acc0c`** | +0x15b8 | `044600af51b100f5c23303f52b731960` |
| `refresh` | `0x5aba80` | **`0x5ad038`** | +0x15b8 | `2de9f04f9bb006af044607f12000` |
| `open_item` | `0x5ac044` | **`0x5ad5fc`** | +0x15b8 | `00f5c23303f52c73` |

Each site is located by a unique signature (the 8-byte `open_item` replay alone occurs
twice in the image, so a 10-byte signal is used) and the bytes at the recorded offset must
equal the replay the trampoline performs. A different image, a moved site or a duplicated
site is refused rather than guessed.

## Result: the shift is not uniform across the build

200 sampled 32-byte windows from the a6100 image were searched in the a6400 image: 129
matched, with the most common deltas `+0x15a8` (12), `+0x14a8` (7), `+0x9a8` (5),
`+0x1490` (4), `+0xf20` (3). The two builds are close but not a single-offset pair, so
every *other* address the loader and the mods use must be mapped per site, not by adding a
constant.

Consequence for the existing mods:

- `MenuTouchFull` v2 hooks two menu functions at fixed addresses; those targets need their
  own a6100 offsets before it can be loaded.
- `Arrivederci` v3 writes fixed physical addresses inside `av-cam`'s LIRO RAM (EC record 31
  at `0x03e5363c`, matrix entry B8 at `0x0444b1d4`) for the a6400 build. The a6100 has the
  same stages but different tables; those must be located per build before the mod means
  anything.
- The loader's own pieces beyond the trampolines — the 3120-byte code cave (a6400 uses a
  `libtestcmd.so` cave), the fake-node vtable and the core's calls into Sony menu
  constructors — also need a6100 addresses.

So "test the existing mods on the a6100" is a porting task per mod, not a configuration
flag. What is now established is that the loader's hook layer itself is portable with
known, verified offsets.

## M2 — shim window and guard preimages (done)

The shim needs 2048 bytes of executable, zero, unused memory. The a6400 loader uses
`libtestcmd.so` at `base + 0x2800`; the **same window works on the a6100**:

| Item | Value |
| --- | --- |
| file | `libtestcmd.so`, SHA-256 `66b30d86…55e222a`, 10,080 B (`0x2760`) |
| shim window | `base + 0x2800`, 2048 B, ends exactly at the page boundary `0x3000` |
| why it is safe | the file ends at `0x2760`, so `0x2800` is beyond EOF; it sits in the last page of the `R E` segment (zero fill) and past the used range of the `RW` segment (`0x203c + 0x1fc`) |
| writability | the shim calls `ml_make_rwx` on its slot, as on the a6400 |

Guard preimages this build needs: `viewUnified2.so` `7bb0f245…`, `libObj.so`
`a9e2c016…`, `im.elf` `ab7eed01…`, `libtestcmd.so` `66b30d86…`, plus the three hook
byte sequences above. All four hashes and the hook bytes are now in the layout module and
checked by tests.

## M3 — loader constants (resolved by symbol)

The shim calls two Sony functions through PLT stubs. They are resolvable by **symbol
name**, not by offset arithmetic: the `.plt` is not a constant shift of the a6400 one
(index arithmetic was off by ~0x32c; a 12-byte signature matched hundreds of sites
because it is the generic Thumb-to-ARM veneer plus PIC constant loads).

| Constant | Symbol | a6400 | a6100 |
| --- | --- | --- | --- |
| `SET_TEXT` | `GEN_Label::setTextDirect(char const*)` | `0x14f6d0` | **`0x150030`** |
| `SET_MODE` | `ux::wgtsys::Widget::setMode(WidgetMode)` | `0x155a68` | **`0x1563c8`** |

The five `libObj`-local addresses the core uses for the enter/confirm event
(`REF_OP_NEW`, `REF_EV_CTOR`, `REF_EVMGR_PUSH`, `REF_EVMGR_SLOT`) turned out to be four
named symbols; see §M3b. The fifth, the alcor index hook at `+0x79d4b6` (`MTL_HOOK_ADDR`,
the `ModsTabLook` tab-artwork hook), is a genuine instruction site and is still
unresolved. It is optional for v2: `core.c` tests for the hook and falls back to the
sixth tab's icon when it is absent, so a missing port costs the tab icon, not the loader.


## Toolchain (now installed, root-free)

`scripts/setup_arm_toolchain.sh` installs the cross compiler without root by downloading the
Ubuntu `*arm-linux-gnueabi` packages and extracting them under `artifacts/toolchain/`, with
wrapper scripts that supply the sysroot, the gcc-cross directory and the host library path
the extracted compiler cannot know. Re-running it is a no-op.

Verified end to end on this machine:

```
bash scripts/setup_arm_toolchain.sh            # 33 packages, ~51 MB, no root
export PATH="$PWD/artifacts/toolchain/arm-linux-gnueabi/bin:$PATH"
bash tools/native_camera/mlv2/build_wsl.sh /tmp/mlv2-repro
```

The a6400 loader builds reproducibly with it:

| Output | Bytes | SHA-256 |
| --- | ---: | --- |
| `shim.bin` | 1008 | `bdd1ae95fd7fbfd0a95190d41250055808f2675b03ee28f1e8d026998c28a2a6` |
| `core.bin` | 8136 (8116 code) | `cd8971b344b66e3560936177d4047be5bf96b0e4f6572cf2682daba40cc9f8ca` |
| `mlv2_guard_arm` | 692564 | (built) |
| `mlv2.req` | 1136 | `02dd9b7d2319674d8d4874c067b73dde0ecaf6614830215361c1ae69f91e2e10` |

One step still fails: the SD-boot companion `.so` links against
`tools/sony_sysroot/lib/libc.so`, which is not present in this checkout. That file is only
needed for the SD-card boot package, not for the RAM install through the guard.

## What this milestone adds

- A build table with the three hook offsets and their replayed bytes, the shim window and
  the two PLT constants.
- A verifier that fails closed on: wrong `viewUnified2.so`/`libtestcmd.so` SHA-256, wrong
  bytes at a site, a site that occurs more than once, a moved site, a missing PLT stub
  prefix, or a cave that no longer fits the page.
- Nine tests covering those cases and asserting the a6400 offsets are not silently reused.

## M5 path: the emulated guest has a root shell

The a6100 run's serial console is a root shell, not just kernel log. A second QEMU instance
started with `SERIAL=unix:/tmp/a6100-serial.sock,server=on,wait=off` answered:

```
/ # echo MARK-$$
MARK-141
/ # ls /tmp_bt
ble_local_keys  bsa_s_pid  bt-avk-fifo
```

So the emulator has an injection path after all: stage the loader files through
`SETTING_FILES` (they land in `/setting` on the disposable overlay), copy them to
`/tmp_bt`, and run the guard from the shell. No host-side memory poking is needed.

## What the guard needs, and what is still a6400-specific

The guard is better factored than expected: it finds the `im.elf` process, takes each
library base from `/proc/<pid>/maps` by leaf name, and computes `view_bias`/`testcmd_bias`
against its reference bases. It is already built with `-DMLV2_VIEW_BASE`/`-DMLV2_SHIM_ADDR`
overrides, so a per-build port is a matter of constants:

| Constant | Where | a6400 | a6100 |
| --- | --- | --- | --- |
| three hook VAs | `mlv2_guard.c`, `mlv2_trampoline.S` | `+0x5ab654`, `+0x5aba80`, `+0x5ac044` | `+0x5acc0c`, `+0x5ad038`, `+0x5ad5fc` (**resolved**) |
| `SET_TEXT`, `SET_MODE` | `mlv2.h` | `+0x14f6d0`, `+0x155a68` | `+0x150030`, `+0x1563c8` (**resolved**) |
| tab rectangle slots | `mlv2_guard.c` | `+0x802a64`, `+0x802ae4` (`0x15de8a64`, `0x15de8ae4`) | `+0x803984`, `+0x803a04` (**resolved**, §M3c) |
| v1 anchor | `mlv2.h` (`+0xb78c40` in viewUnified2 `.bss`) | `+0xb78c40` | `+0xb7a658` (**resolved**, §M3c) |
| event references | `core.c` | five opaque addresses | four symbols (**resolved**, §M3b) |

## M3b — the five libObj addresses are four named symbols (done)

They are not opaque code sites. Disassembling the a6400 addresses shows PLT veneers and a
GOT cell, so the whole set resolves from the dynamic symbol table, which both builds
export (2,547 dynamic symbols, no `.symtab` in either):

| `core.c` name | a6400 | a6100 | Symbol |
| --- | --- | --- | --- |
| `REF_OP_NEW` | `0xfd974` | `0xfd974` | `_Znwj` (PLT; imported from libstdc++) |
| `REF_EV_CTOR` | `0xfce20` | `0xfce20` | `_ZN5EventC1Emhh` (PLT; also defined at `0x840f7d`) |
| `REF_EVMGR_PUSH` | `0x100d5d` | `0x100d60` | `_ZN12EventManager4pushEP5Eventb` (also defined at `0x842529`) |
| `REF_EVMGR_SLOT` | `0x07089984` | see below | `g_pEvtMgr` |

The method, and its check: `.rel.plt` binds each PLT entry to a GOT slot; decoding the
three ARM instructions of each veneer (`add ip, pc, #A; add ip, ip, #B; ldr pc, [ip, #C]`)
recovers that slot, and the slot names the symbol. All **2,547 of 2,547** PLT entries
resolve this way in both builds, and the three addresses above are reproduced exactly for
the a6400 — the same numbers the shipped loader uses.

The PLT addresses happen to be identical in both builds because `.rel.plt` and `.plt` have
the same size and order; the veneer *displacements* differ (`24febce5` vs `24fdbce5`), so
matching on bytes would have failed. Matching on the symbol is what makes the port safe.

`a6100_layout.py` now resolves these four by name, with a 16-byte signal per site that must
be unique in the image, and records the a6100 definitions (`Event::Event` `0x840f7d`,
`EventManager::push` `0x842529`) as the preferred call targets.

### The data slot: the a6400 constant reads the wrong cell

`REF_EVMGR_SLOT` is a GOT address, and the address rule is now pinned: the RW LOAD has
`p_vaddr - p_offset = 0x8000`, so a runtime address is
`rw_lo + (elf_va - (rw_pgoff + 0x8000))`. With the a6400 maps line
(`0x07016000`, pgoff `0x012f6000`) this reproduces the shipped constant exactly:

```
libobj_runtime(0x1371984, 0x07016000, 0x012f6000) == 0x07089984
```

But `0x1371984` is an `R_ARM_RELATIVE` GOT entry holding `0x00d5b359`, a `.text` address —
it is **not** `g_pEvtMgr`'s `R_ARM_GLOB_DAT` cell, which is at `0x1371044`. `core.c` reads
that cell and then dereferences it a second time, so `mgr` would be four bytes of code
(`0x8a47f0e9` at `0xd5b359`). The singleton itself is at ELF `0x1424d70`, i.e. runtime
`0x0713cd70` in the anonymous mapping that continues libObj's RW segment.

So the a6100 build must **not** port `REF_EVMGR_SLOT`. It reads `g_pEvtMgr` once at the
address the rule above produces (`0x1425c70` + mapping). That is a one-line change with a
smaller blast radius than the a6400 code, and the a6400 defect is recorded here rather
than copied.

## Arrivederci on the a6100: what the data says (no port claimed)

Reconnaissance for the first real mod, done read-only:

- `Arrivederci.mod` embeds both blocks: the stock EC record at file `0x7d4` (192 B) and the
  ARRI payload at `0x894` (192 B, different).
- The a6100 `av-cam.bin` contains that stock 192-byte block **verbatim** at file
  `0x88d6bc` → VA `0x63e536bc`, **PA `0x03e536bc`** (the a6400 table is at `0x03e5363c`,
  i.e. +0x80 in this build).
- A first `/dev/mem` read from the guest at that PA returned exactly the stock block. A
  later read returned different contents (`0000 0071 …`), so the EC record is rewritten at
  runtime depending on the active gamma/PP. A static address alone is therefore not enough
  for a reliable mod.
- The CC matrix at the a6400 address is **all zeros in the image** (it is computed at run
  time), so it cannot be found by pattern. The relative-delta candidate
  (`0x03e536bc + 0x5F7B98 = 0x0444b254`) read back a descending table
  (`10, 9, 8, 7, 6, 5, …`), not a Q10 matrix. The a6100 matrix address is still unknown.
- A `printf … | dd of=/dev/mem` write of the ARRI block did not take effect (the readback
  was unchanged). Cause not established (busybox `printf` escapes or a strict `/devmem`
  are both possible), so no write is claimed.

Consequence: Arrivederci's a6100 port needs a runtime method, not static guessing — e.g.
`pmemsave` the av-cam region through QMP and locate the matrix by its Q10 row sums
(≈1024 per row), then confirm the EC record's live location the same way.

## M3c & M5 — Tab rectangle slots, `.bss` anchor, and emulator verification (done)

### Tab rectangle slots (`RECT_TBL1_SLOT6`, `RECT_TBL2_SLOT6`) and `V1_ANCHOR`
In `mlv2_guard.c`, the reference `viewUnified2.so` base is `0x155e6000` and the FW 2.00
slot-6 addresses are `0x15de8a64` (`+0x802a64`) and `0x15de8ae4` (`+0x802ae4`) inside
`15PAS_MenuListTab` in `.rodata` (an earlier reconnaissance script had subtracted
`0x155e1000` by mistake and inspected `+0x807a04`). Each slot-6 is preceded by the 48-byte
`orig_rect_prefix` (tabs 0..5 x/y/w/h) and holds 8 zero bytes at rest.

In the a6100 `viewUnified2.so`, `orig_rect_prefix` occurs at only two offsets:
`+0x803954` and `+0x8039d4`, giving unambiguous 8-byte zero slots at **`+0x803984`**
and **`+0x803a04`**.

Similarly, `MLV2_V1_ANCHOR` on the a6400 (`0x1615ec40 - 0x155e6000 = +0xb78c40`) sits at
the exact end of `.bss` (`0xb06ba8 + 0x72098 = 0xb78c40`). The equivalent end-of-`.bss`
offset on the a6100 (`0xb07d80 + 0x728d8`) is **`+0xb7a658`** (within the zero-filled tail
before page boundary `0xb7b000`).

### Build and live QEMU verification (`MLV2_TARGET=a6100`)
Building with `CI_SKIP_SYSROOT=1 MLV2_TARGET=a6100 bash tools/native_camera/mlv2/build_wsl.sh`:

| Output | Bytes | SHA-256 |
| --- | ---: | --- |
| `shim.bin` | 1008 | `1f1533deff61425864534fe06b50c17f0e6d7ae38e1c96b09f5739e722211cf3` |
| `core.bin` | 8016 (7996 code) | `63bf09c5944ea1d2db37b21ca0f5b4b7b73a4a5053aa6b3849e519e75a9667c6` |
| `mlv2.req` | 1136 | `a9d3364e1da358c98e9717dc868c6494df53b3a341b817861fadaab03392117b` |
| `mlv2_guard_arm` | 692524 | `178b5995a733e2e3731e9381d078d390f9d21e9bdc53c0f93090ed78fe91fa6d` |

The default (`a6400`) build remains byte-identical (`bdd1ae95…`, `cd8971b3…`, `02dd9b7d…`).

Booted in QEMU (`rear_panel.py --inputs artifacts/a6100-inputs --loader /tmp/mlv2-a6100-build --mod /tmp/mlv2-a6100-build/ActionTest.mod`):

- Guard check: `TARGET /proc/144 view_bias=fe0bc000 testcmd_bias=ff680000 STATE hooks=stock v1_build=-1 shim=zero MLV2_RESULT=0 changed=0`
- Guard install: `MLV2_RESULT=0 changed=1`
- Loader log across menu open, tab 7 (`Mods 1/1 — USB Streaming Test`) ON, automatic menu close, reopen (`Active`), and OFF:
  ```
  core_init 00000002
  core_view_bias fe0bc000
  tab_icon_tab6 ffc35000
  core_load 4afed1e8
  mod_on 043cc000
  action_close 043cc000
  mod_off 043cc000
  ```
- Also confirmed in the running guest: the 9-int16 B8 color matrix table (`1181, -188, 31, …`) is at `Backup.bin` file offset `0x32c80` (`PA 0x02eb2c80`) and in `av-cam` runtime RAM at **`PA 0x0444b1e4`** (`+0x10` from the a6400's `0x0444b1d4`).

## Remaining work — updated

| Step | Content | State |
| --- | --- | --- |
| M2 | shim window + guard preimages | **done** (§M2) |
| M3 | `SET_TEXT`/`SET_MODE` | **done** (§M3) |
| M3b | five `libObj` event addresses | **done** (§M3b): resolved by symbol; the a6400 slot constant is a defect and is not ported |
| M3c | tab rectangle table + `.bss` anchor | **done** (§M3c): `+0x803984`, `+0x803a04`, anchor `+0xb7a658` |
| M5 | emulator harness: build, install guard, verify 7th `Mods` tab and ON/OFF + `action_close` receipts | **done** (§M5) |
| M4 | physical camera preflight + film-simulation mods | next |

## Boundary

None of this is installable on the camera: the a6100 still has no injection path (E9102).
The emulator is the target, and M5 is the piece that would make a mod actually run there.
