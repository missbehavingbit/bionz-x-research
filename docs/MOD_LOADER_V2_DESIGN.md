# Mod Loader v2 — design

The current SD build uses the jointly installed debugger as its startup host. It
works through a separate camera-side worker and requires no USB connection during
boot. See the offline status below.

Status: proposal (2026-11-16). Nothing in this document has been built or tested yet.

Mod Loader v1 works, but each new mod forced users through fragile steps. This design removes
those steps. Each item names the observed root cause (E0505 and the loader sources) and the
change that removes it. "Failure-free" is the goal, not a guarantee: every step keeps a guard
that refuses rather than corrupting state, plus a receipt.

## Problems in v1 and their causes

| User-visible problem | Root cause (observed) |
| --- | --- |
| "Do not open the menu before installing" | The fake Mods-tab nodes keep a vtable whose entries point into the loader code (`ml_get_state`, `st->vtable[...]`). After the menu builds nodes, removing that code would leave dangling function pointers, so the guard refuses to unload (`-24`). The SD boot package also installs the v1 loader on every boot. |
| "Find a free code cave" for every mod | SMOD v1 mods are linked at a fixed `code_addr` in `im.elf` RX padding. Caves are rare and must be proven unused, one by one. The loader itself is capped at 3120 bytes by its cave. |
| New mod files are not shown | `ml_build_tree` scans only while `st->scanned == 0`, so it scans once per install. |
| Mods on the SD card are ignored | The boot reader stages a fixed file list (`MenuTouchFull.mod` only), and the camera does not mount the card at `/mnt/mmca1`. |
| Data mods cannot restore on OFF | SMOD v1 OFF only reverts hook bytes. Partly fixed by the lifecycle flag (bit 1). |
| "Shuffle Color Modes" for Arrivederci color | The camera applies the matrix only on a recompute event (for example a Color Mode change). The mod cannot trigger one. |
| Color stays after power-off | The matrix table is backed up by Sony at power-off. |

## Design

### 1. Permanent shim plus replaceable core

- **Shim** (stays in the existing 3120-byte cave, never removed): the three view hooks and a
  small dispatcher that calls through function pointers kept in the mmap'd state block. It
  also holds the fake-node vtable stubs, which forward through the same pointer table.
- **Core** (any size): loaded into memory the shim allocates with `mmap` (RWX) from a core
  file. Menu building, scanning, mod loading and lifecycle handling all live here.
- **Upgrade in place:** a new core is loaded at a new address, verified, and the pointer table
  is switched in one store while threads are frozen. The old core stays mapped (never
  unmapped), so retained menu nodes can never call freed code. No restart or "menu closed"
  rule is needed, and the `-24` case disappears.
- The core file's hash is pinned in the shim, so only an approved core can run.

### 2. SMOD v2: relocatable mods (no caves)

- Mods are built position-independent (`-fPIC`, PC-relative data). The core allocates RWX
  memory for each mod with `mmap`, copies the code, verifies the CRC, and flushes caches.
- Header: name, version, flags, code size, optional state size, entry offsets
  (`init`, `on`, `off`), and hook records given as *target VA, original bytes, entry offset*.
  The core generates each trampoline itself (absolute `ldr pc` to base+offset).
- Lifecycle is standard: `on()` after hooks are applied, `off()` before they are reverted.
- SMOD v1 files remain loadable (fixed address, existing rules) for backward compatibility.
  New mods use v2.

### 3. Live mod list

- Rescan `/tmp_bt/mods` (and any mounted SD `mods/`) every time the Mods tab opens.
- Entries are keyed by file name plus CRC. An enabled mod keeps its state across rescans. A
  changed or removed file of an enabled mod is shown as `ERR` and is never reloaded silently.

### 4. SD card as the single install medium

- The boot reader stages **every** `*.mod` in the card's `mods/` folder (bounded count and
  size) plus the shim requests and core file from `boot-mod-loader/`, instead of a fixed list.
- `boot.sh` keeps pinned hashes for the guard, shim requests and core. Mods are not pinned;
  the core validates their headers and CRC, and the user chooses them by putting them on the card.
- Installing a mod then means copying a file to `E:\mods\` and powering on the camera.

### 5. Arrivederci without Color Mode shuffling

- Research item: find the `im.elf` call the menu uses to send PP settings to LIRO, and have
  the mod re-send the current Color Mode after writing the matrix. That makes the camera
  recompute immediately.
- Research item: a loader hook on the camera's power-off path that calls every enabled mod's
  `off()` before Sony's backup save. Backed-up tables such as the color matrix would then never
  persist unintentionally.
- Photo-mode color: trace the develop-time matrix selection (separate from live view).

## Milestones

| Milestone | Content | Camera writes |
| --- | --- | --- |
| M1 | Shim plus core split, upgrade in place, live rescan, SMOD v2 loader with relocatable mods; Arrivederci and MenuTouchFull rebuilt as v2 | RAM only (guard via debugger) |
| M2 | Boot package: generic SD staging, pinned shim and core hashes | **One-time update of `/setting` files (internal flash). Needs your explicit approval.** |
| M3 | Arrivederci: automatic recompute; off-before-backup hook; photo-mode color research | RAM only |

Each milestone is built and tested offline first (Unicorn tests for the shim, core and mod
lifecycles, plus the existing loader tests), then tested on the camera with receipts.

## M1 status (2026-11-16, offline only; not yet run on the camera)

| Part | Source | Offline result |
| --- | --- | --- |
| Shim (912 B in the libtestcmd cave, 2208 B capacity) | `tools/native_camera/mlv2/shim.c`, `mlv2_trampoline.S` | Unicorn harness: 7/7 scenarios pass |
| PIC core (`/tmp_bt/mlv2/core.bin`, 5092 B, FNV `0xff62088e`) | `tools/native_camera/mlv2/core.c` | Same harness: load, rescan, toggle, upgrade in place, corrupt-core refusal, v1 import |
| SMOD v2 packer | `tools/research/pack_mod_v2.py` | Arrivederci v2 build (`artifacts/tests/arrivederci-v2-build-1/Arrivederci.mod`, 1424 B) |
| Guard (`mlv2_guard check/install/upgrade`) | `tools/native_camera/mlv2/mlv2_guard.c` | Host ptrace test against a stand-in process: 9/9 cases pass (`artifacts/tests/mlv2-guard-host-2/guard-host-test.txt`) |
| Host install helper | `tools/research/install_mlv2.py` | CLI only; not run against the camera |

Build: `artifacts/tests/mlv2-build-4` (`build_wsl.sh`; `build.json` lists every SHA-256; request
`mlv2.req` 972 B, SHA-256 `c982320a…59458`).

Guard behavior:

- `check` reports the hook state (`stock`, `v1` with the matching v1 build, `v2`, `unknown`) and
  the cave state; it writes nothing.
- `install` (stock hooks) and `upgrade` (sd-standalone v1 or lifecycle v1 hooks) write the shim,
  the tab rectangles and the three hook branches with every im.elf thread stopped; each write is
  read back and every earlier step rolls back on a failure.
- The v1 code, v1 state and v1 anchor stay in place on `upgrade`, so menu nodes that v1 already
  built stay valid. This removes the "do not open the menu first" rule.
- It refuses a non-zero cave, unknown hooks, or a thread paused inside a hook window or the cave
  (`-17`, retry). The busy-thread path is implemented but not exercised by the host test.
- A second `install` on an installed camera is a no-op.

Not done in M1: MenuTouchFull is not rebuilt as SMOD v2. The v2 core imports it from the v1
state when it is enabled, which the harness covers.

The full unit suite ran 1911 tests with 7 failures, all in the status-dashboard sync tests. The
cause is evidence records E0524–E0540 that are newer than the documented snapshot (E0523); those
failures are unrelated to the loader.

## M1 camera test (2026-11-16, RAM only)

Host-observed. Receipts are in `artifacts/tests/mlv2-build-4/`: `install-receipt-20261116-212141.json`
(check only), `install-receipt-20261116-212233.json` (upgrade), and `menu-test-readback-20261116-212707.json`.

1. **Check:** at uptime 85 s, the SD boot package had installed the sd-standalone v1 loader. The
   guard reported `STATE hooks=v1 v1_build=0 shim=zero`. The menu state was not a precondition.
2. **Upgrade:** `MLV2_RESULT=0 changed=1`. The three hooks read back as branches to
   `0x05662a19/0x05662a69/0x05662a79`. `core.bin` was staged by an atomic rename, and
   `Arrivederci.mod` (SMOD v2, SHA-256 `90dc6290…eaf3`) was added beside the v1 `MenuTouchFull.mod`.
   The guard and request were removed, uptime kept increasing, and the debugger answered.
3. **User menu test:** both Arrivederci and MenuTouchFull appeared in the Mods tab and both
   worked. The user switched Arrivederci on and then off.
4. **Readback:**
   - The loader log shows `core_init`, `core_load ff62088e` (the shim loaded the core from
     `/tmp_bt` on the first menu open), and `mod_on`/`mod_off` with the v2 mod mapped at `0x040da000`.
   - The Arrivederci log shows `on=1 gamma=w color=w` and then `on=0 gamma=w color=w`.
   - After OFF, record 31 word 0 is `0x1500` and entry B8 is `[1181,-188,31,51,1024,-51,118,-356,1263]`.
     Both match the stock values. The matrix index is 23 and the shim slot is set.

Result: on the camera, M1 removes two v1 rules. Installing after boot no longer depends on whether
the menu was opened, and SMOD v2 mods load without a code cave.

Still open:

- Color still needs a Color Mode round trip (M3).
- MenuTouchFull is still served through the v1 import.
- Persistence across restarts needs M2 (a `/setting` write that needs approval).

## Handoff for the next agent (2026-11-16)

Mod Loader v2 M1 is implemented and passed its camera test, but it is uncommitted.

- **Shim:** `tools/native_camera/mlv2/shim.c` sits permanently in the libtestcmd cave at
  `0x05662760`.
- **Core:** the shim loads the position-independent core (`core.c`, header `MLCR`, see
  `mlv2.h`) from `/tmp_bt/mlv2/core.bin` on each menu open. A core with a new FNV is loaded in
  place. The core rescans the mod folders, loads SMOD v1 and SMOD v2 mods (v2 mods are
  relocatable and packed by `tools/research/pack_mod_v2.py`), and imports the mods a resident v1
  loader had enabled.
- **Guard:** `mlv2_guard.c` installs onto stock hooks or takes over from either known v1 build
  under a ptrace thread freeze, with readback and rollback. `tools/research/install_mlv2.py`
  drives it over the debugger.
- **Build and tests:** `build_wsl.sh` builds everything into a new directory (the current build
  is `artifacts/tests/mlv2-build-4`). The offline tests are `test_mlv2_unicorn.py` (venv, 7/7)
  and `test_mlv2_guard_host.py` (WSL, 9/9).
- **Next:**
  - M3: Arrivederci auto-recompute instead of a Color Mode round trip, plus an off-before-backup
    hook, because table `0x6444a044` is saved to `/setting` at power-off.
  - Rebuild MenuTouchFull as SMOD v2.
  - M2: a boot package. It writes `/setting` internal flash, so it needs explicit user approval.
- **Rule for camera tests:** Arrivederci must be OFF before power-off.

## MenuTouchFull as SMOD v2 (offline, 2026-11-17)

`mods/menu_touch_full/build_mtf_v2_wsl.sh` builds MenuTouchFull position-independently
(`-DMT_SMOD_V2`). `mod_v2.ld` now exports `__smod_bss` at the end of the code; a mod reaches
its `--bss` area through that hidden symbol. The Arrivederci v2 rebuild stays byte-identical
(`90dc6290…eaf3`). Build `artifacts/tests/mtf-v2-build-1` (SHA-256 `5ac09180…4c48`, 2764 B)
passed the mod-level Unicorn test at two load bases and scenario 10 of the core harness
(`mlv2-sd-build-3` core, `unicorn-core.txt`). Scenario 10 imports an enabled v1 copy, replaces
the file, switches v1 OFF and v2 ON/OFF.

Camera run (2026-11-17, RAM and `/tmp_bt` only, user-approved). The guard check reported
`hooks=v1 v1_build=0 shim=zero`. `install_mlv2 --build mlv2-sd-build-3 --mod` the v2 file
returned `MLV2_RESULT=0 changed=1` (receipt `mlv2-sd-build-3/install-receipt-20261117-100053.json`).
Both touch prologues were still stock, so no v1 copy was active. After the user's menu test
(reported "works"), the readback (`mtf-v2-build-1/camera-readback-20261117.json`) shows the
loader log `core_init`, `core_load 529057cd`, `mod_on 04075000`. Both hooks jump to the mapping
(`0x040751f5`, `0x04075115`). The 2616 resident code bytes equal the file. The MTF2 state is
published (enabled 2, inflight 0, last_err 0) with seven recorded actions, all tab taps
(tabs 6 to 1, then Mods). Page, row, swipe and popup actions and v2 OFF were not exercised in this
run, and the mod was left ON. Uptime advanced and the debugger replied. A restart returns
the SD package's v1 loader. Next: exercise row, popup and swipe actions and OFF, then switch the SD
package to the v2 file.

## Modset v2: usable (2026-11-17)

Status: **usable** with a RAM install after each boot. The user marked this modset as usable after the camera runs recorded here and in E0555/E0556.

| Part | File | SHA-256 |
| --- | --- | --- |
| Mod Loader v2 (shim, core, guard) | `artifacts/tests/mlv2-sd-build-3` | core `552220a4…8b3e`, request `fac109bb…d49e`, guard `1d84f253…c9d` |
| MenuTouchFull (SMOD v2) | `artifacts/tests/mtf-v2-build-1/MenuTouchFull.mod` | `5ac09180…4c48` |
| Arrivederci, fitted Classic matrix (SMOD v2) | `artifacts/tests/arrivederci-v3-build-1/Arrivederci.mod` | `c09693b8…a88a` |

Install: `python -m tools.research.install_mlv2 --build artifacts/tests/mlv2-sd-build-3 --mod …/MenuTouchFull.mod --mod …/Arrivederci.mod`. Known limits: the MenuTouchFull v2 camera run confirmed tab taps only. Arrivederci color is Movie-only and backup-backed, so turn it OFF before power-off. The card's auto-boot still installs the v1 loader. Booting this set from the card needs the M2 `/setting` update, which has not been done.

SD copy: `E:\modset-v2\` was created on 2026-11-17 with the files above, `build.json`, a `README.txt` and `SHA256SUMS`. All seven entries verified with `sha256sum -c` after the copy. The card was in a host card reader (removable FAT32, no Sony USB device present), so the camera had no access. Nothing existing was overwritten. `boot-mod-loader\` and `mods\MenuTouchFull.mod` were re-hashed unchanged. Staging copy: `artifacts/tests/modset-v2-sd-1/`.

## Arrivederci on the v2 loader without a Color Mode round trip (2026-11-17)

See [E0555](evidence/0555-arrivederci-menu-exit-recompute-and-cc8-refill.md). With the v2 loader
resident, Arrivederci v2 was staged and toggled ON and then OFF from the Mods tab. Each time the
user left the menu without changing Color Mode, the CC matrix recompute had already run:
B8, both live copies (+0x12c, +0x686) and record 31 matched the expected state in both
directions. This resolves the M3 item "Color Mode shuffling" for Mods-tab use; the exact
trigger call is not isolated. A guarded write to CC+8 (the per-frame matrix stage source)
was overwritten within about one frame, so it is not a usable color target. The
off-before-backup handoff remains open, and Arrivederci stays out of `MLV2.TAR`.

## Process-layout relocation and QEMU (2026-11-17)

See [E0577](evidence/0577-qemu-real-mods-relocation-and-hot-reload.md). Addresses stay those of the
ILCE-6400 reference layout. The guard finds `im.elf` by name, takes the viewUnified2/libtestcmd bases from
its maps and relocates hooks, tables, entries and the shim. The ML2Q v2 request carries 15 shim relocations
found by a two-link diff. The core reads the view difference and the shim slot from `ShimState`. SMOD v2 mods
packed with `--reloc-layout` carry a library relocation table that the core applies from `/proc/self/maps`.
At zero bias every step equals the earlier behavior. Build `artifacts/tests/mlv2-layout-build-2` and
`mtf-v2-reloc-build-1` ran in the QEMU Sony menu; they have not run on the camera.

SMOD v2 flag 8 (`SMOD_V2_FLAG_AUTO_ON`): the core switches the mod ON the first time it lists the
file. ModsTabLook ships this way for QEMU (`mods/mods_tab_look/build_mtl_v2_wsl.sh`). When the
ModsTabLook hook is absent, the core gives the Mods tab the sixth tab's icon.

## Command items (2026-11-18)

`SMOD_V2_FLAG_ACTION` (16) turns a Mods row into a command. The row shows no ON/OFF value:
it is empty while idle and reads `Active` while running. A press on an idle item runs the
normal ON path, and only after a successful ON queues the MENU key through libObj
(MenuTouchFull's E0520 calls, moved by the live libObj base). The menu then closes to the
shooting screen. A press on a running item switches it OFF and keeps the menu open. Auto-on is
ignored for these items. Unicorn scenario 15 and a QEMU run pass
([E0592](evidence/0592-mod-loader-command-items-usb-streaming-entry.md)). Not run on the camera.

## Reload request (offline, 2026-11-17)

See [E0576](evidence/0576-mod-loader-v2-reload-request.md). Build `artifacts/tests/mlv2-reload-build-1` changes
only the core (`1a105461…a4a1`). Shim, guard and request are identical to `mlv2-sd-build-3`, so an installed v2
loader picks it up as a core upgrade. A file name in `/tmp_bt/mlv2/reload` makes the core replace that enabled
SMOD v2 mod on the UI thread. It loads and hash-checks the new copy into a fresh mapping and checks the hook
targets. Then it switches the old copy OFF with readback and stops if that fails. Finally it applies the new
hooks and runs `on()`. The result goes to `/tmp_bt/mlv2/reload.result`.
`python -m tools.research.reload_mod --mod NEW.mod [--core …/core.bin]` stages the file and the request and
reads the hooks back. It has not been run on a camera.

## v2 SD boot and deactivation (offline build)

Build from the repository root:

```powershell
wsl.exe --exec bash tools/native_camera/mlv2/build_wsl.sh artifacts/tests/NEW_MLV2_SD_BUILD
```

The build produces `companion/mlv2_sd_boot_hook.so`, `companion/mlv2_sd_boot.sh`
and `companion/mlv2_card_read.elf` for a future joint installation, plus
`card/MLV2.TAR` for the root of a card with the E0522 FAT32 layout. The companion
is designed to replace the v1 SD companion entry in the joint package. It expects
the debugger executable at `/setting/a6400_usb_debugd.elf` and both worker files
in `/setting`. No installer ran. A deployment would put the companion files and
startup configuration on internal storage. The worker itself uses `/tmp_bt` for
writes, opens the qualified SD block device read-only, and issues no USB requests.

The worker verifies the archive against its build-pinned SHA-256 before extracting
the guard, request, core and the known MenuTouchFull build and artwork asset. It
requires stock hooks on PID 149. Its read-only presence check polls for the archive
or a disable marker. Removal, deletion, a disable marker or a reader error requests
deactivation. The next menu hook entry stops new mod activations and
invokes OFF. The next root setup restores the original child pointer and child
count. The shim and its mappings stay resident to preserve retained references.
After successful OFF and root restoration, the core publishes a stop acknowledgment.
The watcher then verifies the reinserted archive with the pinned SHA-256 and permits
the next menu root setup to show Mods again, with the mods OFF. No reboot or memory
reinstallation is required in the tested sequence. A rapid return waits for the
acknowledgment, and a corrupt archive leaves Mods disabled. Steady-state presence
checks the FAT entry. Resumption after deactivation additionally checks all bytes.

The core checks that owned mod hooks were restored. On conflict or failure it
leaves foreign bytes intact, records `mod_off_failed` and keeps the enabled state
pending retry. These checks do not establish camera-thread quiescence or successful
execution of every mod-specific OFF callback. The MenuTouchFull rollback and
warm-resume removal sequence still need physical validation.

Arrivederci is excluded from `MLV2.TAR`. Its earlier developer-installed build
can still modify the backup-backed matrix. Neither the SD watcher nor a menu hook
is synchronized with the LIRO backup. E0505 identifies six backup readers/writers
of the static table and documents persistence on a power cycle. The next boundary
is to verify a synchronous, pre-snapshot handoff or a color source isolated from
backup before adding Arrivederci to this package. A stock SD boot, no-card boot,
warm resume and before/after backup comparison remain hardware verification work.

Offline receipts for `artifacts/tests/mlv2-sd-build-2`: `build.json` records the
shim, core and request identities. `card/MLV2.TAR` has SHA-256
`8e862b1c24ff756501aa671f0c2d17028b9dfcb565003cfdeedf10419c6c8e04`.
`python -m tools.native_camera.mlv2.test_mlv2_unicorn BUILD ARRIVEDERCI_FIXTURE`
passed nine cases, including managed listing, deactivation and foreign-hook
refusal. `python3 -m tools.native_camera.mlv2.test_mlv2_guard_host BUILD NEW_REPORT`
passed nine cases (`artifacts/tests/mlv2-sd-guard-host-1`). The host reader harness
passed six synthetic FAT32 checks, including deletion and disable marker
(`artifacts/tests/mlv2-sd-reader-1`). The synthetic reader bypasses the physical
block-device identity gate. The retained LIRO disassembly in the build directory
reconfirms the table-descriptor fetch at `0x638ffe40` and write call at
`0x638ffe52`, using the hash-checked `av-cam-camera-original.bin` and E0505's
`disasm.py`. It does not establish a Linux-side pre-snapshot handshake.

Hot reinsertion receipts are under `artifacts/tests/mlv2-sd-build-3`. The archive
SHA-256 is `ee036ebdc3fcf72361d0f33a5355edcbb849b00195ff167e65df5f02ff845513`.
Nine Unicorn scenarios pass, including stop acknowledgment, same-session
reactivation, and refusal after an incomplete hook restoration. The fixture in
`reinsertion-fixture/` covers absence, a rapid return before acknowledgment,
corrupt return, and verified reactivation with a simulated card reader.

Arrivederci recompute research in the same build directory used an identity-checked
`libObj.so` (`60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1`).
Thumb literal-reference scanning and `arm-linux-gnueabi-objdump -D -j .text -M
force-thumb` identify the `SetPictureProfileForLiro` converter candidate at
`0xa93c08`. It accepts MWF message IDs `0x75ae` and `0x75e6`; the Thumb function
pointer appears at linked GOT address `0x136f84c`. The accepted branch at
`0xa94534` initializes an output buffer and starts reading typed parameters
from the original MWF message. This converter alone does not submit a command.
Neither the producer's current
profile context, the same-value recompute behavior, nor its ordering relative to
Sony's backup snapshot has been verified. No guessed firmware call was added.

### Arrivederci trigger and backup boundary

New offline disassembly receipts are under
`artifacts/analysis/arrivederci-trigger-backup-1/`. The same `libObj.so` image
writes packet bytes `+0 = 0x05`, `+2 = 0x66`, `+3 = 0x40` at
`0xa94534`–`0xa9454e`, writes the caller's third argument at `+4..+7`, then
looks up MWF parameter `0x9673`. Byte `+1` is not defined by this window.
This is a correlation signature for a future observation of the existing
Color Mode round trip. It is insufficient to construct or replay a packet.
The preserved CC refresh replay independently reaches `0x63da2274` and
conditionally `0x63da109e` using constructed state. Its predicate, gate and
downstream firmware calls are fixture boundaries, so it does not establish
that setting the current profile again publishes a matrix to the ISP.

The SHA-256-checked `av-cam-camera-original.bin` (`dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`)
was examined with E0505's `disasm.py` at `638ffdf0:638ffe34`,
`635dd710:635dd730` and `638ff8cc:638ff954`. The write call at `0x638ffe52`
hands Sony's descriptor list to stub `0x635dd710`, which loads a callback
from a runtime table (`[GOT slot 0x645c80fc]`, then `+0x14`, then `+0x2b0`).
The preserved image resolves the first two loads to `0x635c6000` and
`0x635c60b4` and contains zero at the resulting callback slot. This is an
unresolved runtime target. The call site alone does not prove where the data
is copied or that all backup writers share this path.

Continue by matching the observed profile-change message and completion to
the CC refresh and publication path. For backup, qualify the live callback
and the descriptor consumers before considering a synchronous restore, or
qualify a matrix source that the backup path cannot read. No camera, SD or
flash was accessed, and the Arrivederci runtime behavior has not changed.

### Arrivederci live backup target (read-only)

Receipts: `artifacts/analysis/arrivederci-live-backup-read-1/`. The existing
extcmd debugger read 200 known instruction bytes at `0x63af9f38` matching the
preserved LIRO image. Three four-byte `/dev/mem` reads then resolved the live
chain `0x645c80fc` → `0x635c6000`, `0x635c6014` → `0x635c60b4`,
`0x635c6364` → `0x5f05e940`. Filtered `/proc/kallsyms` identifies that final
address as `Backup_write_multi [backup]`, at `.text+0x1940` from the live
`/sys/module/backup/sections/.text` base `0x5f05d000`.

Both original ID lists (`0x64561184` and `0x64560ed4`) start with
`0x30000`, `0x30001`, `0x30002`, `0x30003`. Three 16-byte live reads confirmed
the first entries of both lists and of the source-pointer list at `0x64561440`.
The second source is the original CC adjustment structure `0x6444a044`, so the
matrix is covered by unit `0x30001` on both observed branches. No backup was
triggered. Reading `/usr/kmod/backup.ko` twice with a 73861-byte limit yielded
73796 bytes, SHA-256 `d8f09a1bab645df76ab4d920773dfd7df6854cd0c9194ec1693bbe5cbcc6d4c7`.
This differs from the extracted reference `63c69e991a8da77e301a7090c8ba6e4dcced43921e88415e3591cf0e96dbb2c6`.
The camera module stays in local artifacts. `arm-linux-gnueabi-objdump -dr
--disassemble=Backup_write_multi` and `-dr --start-address=0x5ec
--stop-address=0x880` on that exact copy establish the write path.

The multi-write loop loads each source pointer at `.text+0x1a0c` and calls
the per-unit writer at `+0x1a18`. The LIRO source address takes the kernel-pointer
branch at `+0x7d4`. `+0x838` copies into backup RAM with `memcpy`, before releasing
the per-unit write lock at `+0x848`. Thus a restore synchronized only with the
later flash write can already be too late. This is one verified copy path,
with alternate writers and synchronization still open. Camera uptime increased,
the debugger replied, and `/tmp_bt` was unchanged. No helper was staged and no
camera memory, SD, flash, backup or mode was modified. The earlier PP snapshots
contain state pages rather than the original message and completion, so they
do not qualify a same-value recompute request.

### Arrivederci single-unit writes and isolated matrix candidate

The next read-only extcmd session (`related-live-callbacks.json`) rechecked
the live API table pointer and read 96 bytes at LIRO VA `0x635c6320`
(physical `0x035c6320`, one page). Filtered `/proc/kallsyms` resolved:
`+0x26c` to `Backup_nread_multi`, `+0x298` to `Backup_read`, `+0x2a8` to
`Backup_write`, `+0x2b0` to `Backup_write_multi`, `+0x284` to
`Backup_write_setting_attr`, and `+0x2c8` to `Backup_reg_cb`. Uptime advanced
from 3866.98 to 3867.60 seconds, `/tmp_bt` was unchanged and ping returned
`PONG`. No operation was triggered on the backup subsystem.

Identity-checked LIRO `disasm.py` and `arm-linux-gnueabi-objdump -dr` on
the exact camera module identify a second writer using the same source list.
The LIRO path at `0x639004ea` looks up the incoming unit ID, loads the selected
source at `0x639004fc`, and calls the `Backup_write` veneer at `0x63900500`.
`Backup_write` at `.text+0x167c`, `Backup_write_vsize` at `+0x1694`, and
`Backup_write_multi` at `+0x1940` all call the common writer at `+0x5ec`.
This makes a change specific to the multi-write wrapper insufficient to
isolate the matrix from every identified path. This is static route evidence,
not a capture of either path executing during shutdown. Alternate asynchronous
consumers and live ordering remain open.

The narrower isolation candidate is LIRO matrix selection at `0x63ae4df0`.
The stock selector uses the common table base and an offset derived from
the requested matrix index; the index-23 route computes `0x1190` at
`0x63ae4e20`–`0x63ae4e2c` before adding the table address. A substitution
specific to this source selection could use a separate 18-byte matrix rather
than a copy of the whole adjustment structure and would leave the backup source
unchanged. Before considering any physical patch, replay the original copy and
its downstream operations to verify the byte contract and qualify executable
scratch and synchronized installation. A same-value profile refresh remains
separate. New disassembly receipts are in `arrivederci-live-backup-read-1/`.

### Arrivederci isolated matrix replay

Run `python -m tools.research.arrivederci_matrix_source_replay --report-dir
artifacts/analysis/arrivederci-matrix-source-replay-1` from the repository root
with a fresh report directory. The tool verifies the retained LIRO image and
both E0505 20-byte fixtures by SHA-256 and uses only their first 18 bytes as
matrix input. It maps the original selector and copy code read/execute, and
the stock table, descriptor, and alternate matrix read-only during execution.

Five bounded Unicorn runs reached the instruction following the original
copy at `0x63ae4eb0`. The original code selected stock B8 at `0x6444b1d4`.
An explicit host register fixture at call site `0x63ae4eac` substituted a separate
buffer only for index 23 and the exact original B8 pointer. The original copy at
`0x63da22e8` performed nine two-byte reads and copied exactly 18 bytes. The tests
covered independent B8 OFF/ON/OFF runs and indices 0 and 22 while the fixture
was enabled. The other indices kept their original source. Stock B8 bytes and
backup descriptor `0x64561444` remained unchanged in every run. The original
mode gates used constructed zero-valued state for this bounded branch.

This qualifies the byte contract for source isolation. The replacement buffer
is a fixture VA, with no physical placement or runtime patch implied. The run
stops before downstream ISP publication and does not execute the backup writer.
Next, qualify scratch and an instruction-level trampoline with the original
register/flag behavior. Independently identify a callable profile refresh and
prove it reaches live publication for both ON and OFF. No camera access or
runtime mod change occurred in this step.

### Arrivederci Thumb trampoline replay

`tools/research/arrivederci_matrix_source_fixture.S` is an offline fixture.
It replaces the original four-byte BL at `0x63ae4eac` in the emulator and
accepts the alternate source only when the index is 23, the original source
is `0x6444b1d4`, and the fixture byte enables it. It tail-calls original
Thumb code at `0x63da22e8`. The replay compares `r0` and `r2`–`r12`, SP,
LR at the original copy entry, and APSR NZCVQ against the pre-branch state
and the original return address. Original copy reads, output width, stock
B8 bytes and backup descriptor are checked as before.

Assemble with `arm-linux-gnueabi-as -mcpu=cortex-a5` and link with
`arm-linux-gnueabi-ld --build-id=none --section-start=.patch=0x63ae4eac
--section-start=.fixture=0x63ae5000 -e matrix_source_fixture`. The linked ELF
SHA-256 is `fc07016bbe08c1b39301089fc03d7af58adc96b39b0e8053eae716558778016d`.
An initial link to an untyped even-valued external copy address produced an ARM
interworking veneer in the disassembly and was rejected. The accepted fixture
uses an explicit Thumb destination. The linked replay with `--trampoline`
passed five cases (`arrivederci-matrix-trampoline-replay-1`); the baseline
replay without the option also passed five (`arrivederci-matrix-source-replay-2`).

The fixture address `0x63ae5000` contains original code in the retained image.
It is a synthetic placement and cannot be used as camera scratch. The current
result establishes a working instruction sequence only. A deployable build
still needs proven executable placement, safe patch publication and an
immediate refresh that reaches the ISP. No camera access occurred.

### Arrivederci distant trampoline placement replay

`arrivederci_matrix_source_fixture.S` also supports an offline `wide` variant.
Assemble with `arm-linux-gnueabi-as -mcpu=cortex-a5 --defsym=wide=1` and link
with `arm-linux-gnueabi-ld --build-id=none --section-start=.patch=0x63ae4ea8
--section-start=.fixture=0x5f400000 -e matrix_source_fixture`. The resulting
ELF (`artifacts/analysis/arrivederci-matrix-far-build-1/fixture.elf`, SHA-256
`38388fff0516cb78d05c9758e938fc65a3b95e393d3ee409bb67f3477c301a3c`) replaces
two MOVs plus the original BL with an eight-byte PC load and Thumb pointer.
The trampoline replays both MOVs, reconstructs LR and tail-calls the original
copy. The assembly uses a synthetic address in the kernel module VA range to
test a distance beyond the direct Thumb BL range. It does not claim ownership
of that address on the camera.

The `--trampoline` replay passed five cases at the distant fixture address
(`arrivederci-matrix-far-replay-1`) and five at the earlier near address
(`arrivederci-matrix-near-replay-2`). The checks confirm the chosen matrix,
original copy width, registers, flags, LR and unchanged backup source. The
retained `liro.ko` (SHA-256 `72cb5700d34ecac6f8a9475f05268e55c94bf972183884bdb0e6d95edc40d9f7`)
calls `__alloc_res` and links its allocation into an owned allocation list in
`liro_malloc`. This window provides no executable-placement contract. The
distant fixture removes the near-address dependency; installation would still
need an owned executable allocation, original-byte checks, a proven safe
eight-byte patch transaction and cross-core cache synchronization. E0201
documents why an instruction-cache flush alone does not establish quiescence.
No module was loaded and no camera memory was accessed in this step.

### Arrivederci kernel placement and publication constraints

`artifacts/analysis/arrivederci-kernel-placement-1/` contains disassembly from
the hash-checked extracted `vmlinux.bin` (`12eeec829407595e28265f7d0db3b88c56f63a9f0fdd725c9c53c66a49643707`),
using E0407b1 `analyze.py` and the retained E0401 symbols. The first request
for `v7_coherent_kern_range` by name failed because the table names the alias
`v7_coherent_user_range` at the same address. The address-range repeat succeeded.

- `module_alloc` at `0x60111e38` requests `__vmalloc_node_range` with bounds
  `0x5f000000` to `0x60000000`. `vmalloc_exec` at `0x601a57dc` and
  `module_alloc` load the same protection word at `0x604f77ec`. This supports
  executable module text as an owned candidate for the distant trampoline.
  Neither allocator appears in the retained export-symbol list under those
  names. A supported module `.text` allocation is the candidate, not a raw
  call to an unexported allocator or a fixed virtual address.
- `stop_machine` at `0x60173074` pins the online CPU set and calls
  `__stop_machine`, which reaches `stop_cpus`. `stop_machine_cpu_stop`
  disables local IRQs in state 2, executes the chosen callback in state 3,
  and participates in the state acknowledgment before release. The
  CPU-stopper thread is schedulable work. This path does not by itself check
  saved return PCs of tasks that were interrupted or preempted in the eight
  bytes being replaced. `stop_machine` is GPL-only per E0201.
- `v7_coherent_kern_range` at `0x60119de4` does data-cache cleaning, DSB,
  instruction-cache line invalidation, branch-predictor invalidation, DSB
  and ISB. This is stronger evidence than the isolated `v7_flush_icache_all`
  body, but still requires qualification of the remote CPU synchronization
  path and the LIRO mapping used for the write.

The next installation decision is between proving a pre-start publication
point before the selector can run and proving saved-PC retirement on a live
update. A stopped callback plus a cache flush is insufficient for the wide
patch. No module code was built or loaded for this step and no physical
camera operation occurred. Immediate ON/OFF recompute remains unresolved.

### Arrivederci LIRO pre-start and WBI boundaries

Receipts are under `artifacts/analysis/arrivederci-liro-prestart-1/`. The
hash-checked E0407b1 kernel disassembler was run on `sys_init_module` and
explicit tail range `0x601648a0:0x60165034` (the named query alone caps the
window at 4 KiB). At `0x60164eb0` a blocking notifier receives event `1`
and the loaded module pointer. `0x60164ec0` invokes the module initializer
after that notification. The notifier return is not used to decide whether
to run the initializer. E0032 locates the original `im.elf l` module-load
operation. The retained symbol table lists `register_module_notifier` as an
export. A notifier present before the LIRO load is a candidate cold-init
observation point; ordering of a companion installed by the debugger is
not established.

The camera's existing debugger read `/usr/kmod/liro.ko` (65212 bytes, SHA-256
`ca5fa9ecc990c2ceda755887563dcd2291fb49f56a4260d895818e7f6782f535`, matching
E0524 and differing from the extracted module). WSL `arm-linux-gnueabi-objdump
-dr --disassemble=liro_init` on this exact file confirms creation of
`tsk_entry` at `.init.text+0xa4` and its wake at `+0xc4`. The extracted
module's `tsk_entry` also has a later indirect image entry at `+0x440`.

A bounded filtered `dmesg` read on the active camera shows `liro resume (WBI)`
and `entry_resume:635c6078(1)` at about 0.85 s. The exact camera module's
`liro_resume` contains a WBI branch that can copy two saved regions back into
the image. The larger copy at `.text+0x1d8` uses a start and size derived from
module state by `get_cp_start` and `get_cp_size`; the active region bounds
are unverified. This resume callback is distinct from the cold-init notifier.
Do not assume the notifier runs on WBI resume or that an installed hook and
its backing allocation both survive it. No reboot was requested or performed.
The debugger replied, uptime advanced from 5719.11 to 5719.49, and `/tmp_bt`
was unchanged. The copied proprietary module remains in local artifacts.

Next: establish whether a jointly installed component can register before
LIRO cold-init, resolve the bounded WBI region metadata, and model resume
with both retained and restored memory before publishing any patch. The
camera read did not install a module, modify RAM or write flash or SD.

### Arrivederci WBI copy bounds on the camera

`arm-linux-gnueabi-objdump -dr --disassemble=liro_suspend` on the retained
camera module confirms the first nine words of the LIRO API table are
copied into module state (`.text+0x3a4`–`+0x3c8`). `get_cp_start` at
`+0x30` adds metadata words 3 and 4. `get_cp_size` at `+0x54` subtracts
that start from word 5. For the saved-memory branch, suspend copies the API
subrange and the derived larger region; resume restores them at `+0x1c8`
and `+0x1d8`, respectively.

`wbi-live-copy-bounds-2.json` records three bounded extcmd `/dev/mem` reads:
4 bytes at physical `0x035c6014`, 36 at `0x035c60b4`, and 8 at `0x03ae4ea8`.
The last bytes match the original hook-window preimage. The first attempt
(`wbi-live-copy-bounds.json`) stopped because the decoded end is beyond the
retained image file. The repeat decodes the metadata without reading either
region. It gives half-open destination ranges `0x635c60d8..0x635c641c`
(836 bytes) and `0x6443ed59..0x6469cd00` (2482087 bytes). Neither overlaps
the selector patch at `0x63ae4ea8`. The larger range includes stock B8
at `0x6444b1d4`. This establishes the scope of these two copy operations,
not whether the patched instruction or a new module allocation would persist
through all WBI paths. In particular, leaving a branch beyond these ranges
does not establish that its executable destination survives.

Uptime increased from 5952.25 to 5952.35 seconds, the debugger replied,
and `/tmp_bt` was unchanged. No suspend or resume was initiated. No writes,
helper staging, page-table walks or region sweeps were performed. The next
qualification is paired survival of the call-site and owned executable module
mapping, or re-arming before LIRO resumes with a verified lifetime under both
retained and restored memory.
