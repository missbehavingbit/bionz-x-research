# E0522 — Mods tab appearance: tab icon source and `uxd` image container layout

Date: 2026-11-15. Camera: ILCE-6400. Initial research used read-only process-memory reads of
`im.elf` (PID 149) and offline analysis. Later sections record the guarded RAM-only installs
and physical display tests. Goal: give the injected "Mods" tab its own look ("MODS", a colour
not used by other tabs).

## 1. Where the tab look comes from (live + static)

- `PAS_MenuListTab` tab-header loop (`viewUnified2.so` `0x5abcfa`–`0x5abda6`, live sha256 `fa534aa2…`): per tab *i*,
  icon widget `menu+0xb9c+i*0x220` ← `vt+0x104(resid)` with `resid = tab_node->getResidProperty(29 or 30)`;
  second widget `menu+0x1a7c+i*0x220` ← resid from `0x5aa2a4(menu, i)` = tab *i* resid property 2
  (default `0x0205c6bd`).
- Live property tables of the six real tabs (read from `CmnViewSettingProperties`, E0512 layout):

| Tab | prop 19 | prop 29 | prop 30 | prop 31 | prop 2 |
|---|---|---|---|---|---|
| 1 | 0 | `0x020cc109` | `0x020cc115` | `0x020cc12b` | — |
| 2 | 0 | `0x020cc107` | `0x020cc105` | `0x020cc10f` | — |
| 3 | 3 | `0x020cc106` | `0x020cc101` | `0x020cc11d` | — |
| 4 | 1 | `0x020cc12a` | `0x020cc131` | `0x020cc116` | — |
| 5 | 2 | `0x020cc11b` | `0x020cc0fd` | `0x020cc0fc` | — |
| 6 (My Menu) | 4 | `0x020cc117` | `0x020cc12f` | `0x020cc100` | — |

  No tab has prop 2, so the second widget is the same default for all tabs: **the tab colour is baked into the
  icon images**. The Mods tab forwards props 2/29/30/31 to tab 6, hence the grey star.

## 2. `uxd\x07` image container (offline; tool `tools/research/uxd_image_container.py`)

Inputs: `share/app/image_DSLR_Only_169.uxc` (sha256 `ebe8caa9…`), `image_cmn_169.uxc` (`1a8f14c7…`),
`image_DSC_Only_169.uxc`, `image_EVF.uxc` (firmware `extracted_a6400`).

- Header 16 B (`uxd\x07`, six u16), opaque u16 index words, then a record table.
- Record: `{u32 res_id, u32 format, u16 width, u16 height, u32 size, u32 offset}` (20 B); format bit
  `0x08000000` → 40-byte record with 20 extension bytes before size/offset.
- UI resource `0x02XXXXXX` ↔ record `0x1aXXXXXX` (same low 24 bits).
- ~~Data base = record table start + 4; `size` exceeds the true block by ~17–20 bytes~~ — **superseded (§9)**:
  data = **record address + 4 + offset**; blocks are contiguous (0–3 bytes alignment padding) and sizes are exact.
  The first block still starts at the end of the record table (DSLR 676 records, cmn 6550, DSC 2207, EVF 1746).
- Formats: `0x80000000` raw 16 bpp (`size == w*h*2`, `image_cmn_o_direct_169.uxc` 640×480), `0xe0000000` main
  compressed (and `0xe8000000` with extension), `0x60000000` second compressed format.
- All 18 tab icons are 91×66 `0xe0000000` records in `image_DSLR_Only_169.uxc`; this container holds **no other
  91×66 image** (no spare tab icon of another colour exists there).

## 3. Codec status (not decoded) — NOTE: §3–§6 used blocks extracted with the superseded data base, which is
correct only for the first record of each container; byte-level codec observations and the memory search for
compressed icon bytes in §6 must be redone with §9's layout.

- Not a standard stream (zlib/raw deflate rejected; LZSS flag-byte families with 1/2-byte literals, 4 token
  layouts, min-length 1–3, both bit orders: no consistent fit, even allowing padded strides).
- `MTX_RA_DECOMP_Create` (`libObj +0x5a84e6`) requires a `"RAC\x03"` header → different codec.
- Exported `libObj` symbols expose only `GRM_bitmapCreate`/`BMP_Create` and libra `ImageData` accessors; the decoder
  is internal (stripped). Heuristic scans of all `libObj` functions for nibble/byte decoder shapes found nothing;
  alcor `0x79dcc8` only validates the `uxc`/`uxd` magic (`0x79dbd0` / `0x79dc2c`).

## Next boundary

Obtain decoded pixels for at least one known compressed image (input/output pair), either by locating the decoder
via Ghidra (`tools.research.rea_wsl_batch`) from `ux::resmgr::ResourceManager::request` → libra `0x7a1d48`, or by an
in-camera dump of a decoded tab-icon bitmap while the menu is open (Mod Loader hook, like E0521 Run E). Then derive
the codec, render all tab icons, and encode a "MODS" icon in an unused colour.

Tests: `tests.test_uxd_image_container` (constructed fixtures, no firmware data) 4/4 OK.

## 4. FAILED: in-camera OSD-strip dump froze the camera (2026-11-15)

Build `artifacts/hardware/mod_loader_diag_osd/` (`-DML_DIAG_OSD`, payload `78e4d4dd…`, install `.req` `b594ff80…`):
the refresh hook writes OSD A rows 0–69 (`write(fd, 0x0bfd0000, 89600)` — VA of `im.elf`'s `/dev/stream` mapping,
PA `0x23a80000`) to `/tmp_bt/mods/osdN.bin` on the 3rd/6th/9th refresh; mod toggling stubbed. Install on a camera with
no prior install this boot: `CHECK_OK code=0412f3d0..0412fccc bytes=2300`, `INSTALLED hooks=15b91654,15b91a80,15b92044`,
175 threads resumed; guard and `.req` then removed from `/tmp_bt` (1 MiB tmpfs); camera answered afterwards (uptime
1210 s, live hook bytes `dff800f0 f1fb1204 2de9f043`).

User report: the camera then **froze on the MSC/USB debugger (PlayMemories) screen**; recovered by battery pull.
After recovery: uptime 101 s, `im.elf` PID 149, `/tmp_bt` clean. `dump-ring` contains only the current boot
(`boots=1`, crc `0x2acd6534`) — the frozen boot's kernel log was not preserved (cold power cycle). Receipts:
`artifacts/hardware/mod_loader_diag_osd/freeze/{ring,dmesg}.json`.

Cause: **not established**. Leading inference: the kernel `write()` copying directly from the `/dev/stream` OSD
mapping (device/uncached `rw-s` VMA) inside `im.elf`'s UI thread. E0297 already linked foreign access to
`/dev/stream` pages with a reboot; E0298's safe capture used ptrace reads instead. Other differences from earlier
working runs (no `.mod` present → "No mods" path; guard removed after install) are considered less likely.

Rule adopted: no in-process kernel copies from `/dev/stream` mappings. Prefer the proven host-side ptrace OSD capture
(E0298, `tools.research.capture_camera_osd_ptrace`) with no camera modification.

## 5. Host-side ptrace OSD capture while the menu was open (race lost)

`scratch/capture_osd_tabstrip.py` (read-only ptrace, E0298 path, rows 0–69 of OSD A/B). Baseline on the USB screen:
A = B = `9474fc8f…` (PlayMemories header, decodes correctly as RGBA4444). User opened MENU (stock 6 tabs, nothing
installed) and the capture ran right after: OSD A `3ed271dc…` / `7e42dca1…` shows a dark framed dialog, not the
tab bar; OSD B unchanged; extra `/dev/stream` planes `0x0dba8000`/`0x0de97000` (0x81000 each) look like video data,
`0x0aeb8000` mostly zero. The USB exchange replaces the menu frame before the first read completes. Camera
responsive throughout (uptime 264 → 282 s). No decoded tab-icon pixels obtained.

## 6. Read-only scan of graphics memory for decoded icons (negative)

Read-only ptrace reads of `im.elf` `/dev/stream` mappings after the stock menu had been shown (camera responsive
throughout, uptime 383 → 720 s): `mpMemoryHeap` 4 MiB (`22ed3074…`), `mpTextHeap` 2 MiB (`f9d3ecf7…`),
`mpMemoryHeap2` 1.6 MiB (`0bfcfc9d…`), `gl_common` 32 MiB (`52e70d13…`), stored under `artifacts/hardware/grm_heaps/`.

- Stride/coherence search for 91-wide bitmaps (182/184/192/364/368/384-byte rows): `mpMemoryHeap`
  `0x1a0000`–`0x290000` has regular 64/128/384-byte-stride data, but renders as tables/glyph rows, not icons;
  `gl_common` has only 13 dense 64 KiB blocks (strides 64/128/768), no icon-like region.
- The compressed tab-icon blocks (`0x1a0cc117`, `…12f`, `…100`, `…109`, `…115`, `…12b`; probes at 0/64/512) occur in
  none of the four mappings.

Inference (unverified): decoded tab icons are not retained in these mappings after the menu closes; the codec may be
consumed by the GPU/blitter directly, so the only decoded pixels would be the rendered OSD frame, which host-side
capture cannot reach (USB closes the menu, §5).

## 7. Ghidra search for the decoder (negative) and current inference

Headless Ghidra 12.1.4 (WSL) imported and analysed live `libObj.so` (`firmware/live-8.2.6.0/lib/libObj.so`, sha256
`e88b7e0f…`, 831 s; project outside the repo). Scripts and outputs: `artifacts/tools/ghidra-libobj-e0522/`
(`run_import.sh`, `CalleeDecompile.java`, `RangeDecompile.java`, `run_all.sh`; full `.text` decompiled in 7 chunks,
49,879 functions, `all-*.c`). Ghidra image base `0x10000` (`FUN_007b1d48` = ELF `0x7a1d48`).

- Callee walk (depth 6, 250 functions) from `ResourceManager::request`/`createImage`/libra `0x7a1d48`: record views
  and format-word bit accessors (`0x7a2626` bit 31, `0x7a264a` bit 27 = 40-byte-record flag, `0x7a2656` bit 26,
  `0x7a2662` bits 8–14), no pixel decoder.
- Scoring all 49,879 functions for loop + nibble/bit unpacking + back-reference copy: top hits are the MTX `RAC`
  codec (`0x59b000`–`0x5a9000`), the text engine (`Ts*`, `0x57xxxx`) and GL ES driver internals (`0x74xxxx`–`0x79xxxx`,
  texture slots; formats ETC1 `0x8d64`, ETC2 `0x9274/0x9278`, ASTC `0x93b0+`, no vendor format) — none is the `uxd`
  codec. `grm_gles.ko`/`grm_ma.ko`, `libmintx.so`, `libclema.so` expose nothing decoder-like.
- `/proc/149/maps`: image containers are mapped read-only from `/usr/share/app/` (`image_DSLR_Only_169.uxc` at
  `0x17797000`, `image_cmn_169.uxc` at `0x17dec000`, `image_EVF.uxc`, `image_DSC_Only_169.uxc`).

Inference (unverified): `0xe0000000` images are decompressed by graphics hardware from the mapped containers (DMA-style
read-ahead in `size`, no software decoder, no decoded copies in memory). Candidate codec-free route: serve a custom image
in the raw `0x80000000` 16 bpp format (supported by `image_cmn_o_direct_169.uxc`) by intercepting the resource-record
lookup for a private resource ID — requires locating that lookup and confirming raw images render in a tab widget.

## 8. Raw-image route: test, lookup chain and ModsTabLook mod

- **Raw images render in tab widgets (camera test).** Loader build `mod_loader_test_rawicon` (`ML_TEST_RAWICON`, payload
  `66721c54…`, install `.req` `aedf7f4e…`, `INSTALLED hooks=15b91654,15b91a80,15b92044`) made the Mods tab report the
  existing raw image `0x02104000` (`image_cmn_o_direct_169.uxc`, 640×480, format `0x80000000`). User photo: the Mods tab
  shows blue sky with detail in the lower right — the top-left 91×66 crop of that photo. The `o_direct` container is
  mapped on demand (`0x27f00000` after first use), so its address is not stable.
- Raw pixel layout: RGBA4444 little-endian (R bits 15–12, A bits 3–0); the raw photo decodes correctly this way and the GL
  colour converter `libObj 0x74f630` case 4 uses the same nibble order.
- **Resource lookup chain (Ghidra + disassembly):** `0x7a90c4` splits a resource ID into block `>>13 & 0x7ff` and index
  `& 0x1fff`, finds the database, then `0x79d534(db, k)` = table base (`db + (db[0]&0x7c) + ((db[1] >> 11)&0x7ffc)`,
  helpers `0x79d492`/`0x79d476`/`0x79d4a0`) + `0x79d4b6(db, k)` (u16 index ×4, `0xffff` → −1; encodings 0/1/3). `db` is
  the container + 8: DSLR `db[0]=0x08`, block `0x66` at `db+2`, `db[1]=0x002ee2ee` (750 entries). Offline: all 676 DSLR
  records satisfy `res_id = 0x1a000000 | block<<13 | k`; 74 index slots are `0xffff` (first: `0x020cc012`).
- **ModsTabLook SMOD** (`tools/native_camera/mods_tab_look{.c,_trampoline.S,.ld}`, packer
  `tools/research/build_mods_tab_look.py`): code in `/usr/lib/libptp.so` RX zero padding (`0x11434398`, live padding
  verified zero from `0x11434395`), 1624/3176 B, hook `0x064b54b6` (live bytes `f8b500af0d460446fff7e2ff` verified;
  `nop; ldr.w pc` for the 2-mod-4 site). For `(block 0x66, k 0x12)` it mmaps 16 KiB once, unpacks the RLE icon
  (`mods/mods_tab_look/make_icon.py`: "MODS" white on transparent, teal band; 25 colours, 637 runs, 1328 B) and returns
  a record `{0x1a0cc012, 0x80000000, 91, 66, 12012, off}`; all other lookups run the original code.
  `artifacts/hardware/mods/ModsTabLook.mod` sha256 `8a4d1893…`.
- Loader: Mods tab props 29/30/31 → `0x020cc012` only while the hook opcode is present at `0x064b54b8`
  (`mod_loader_tablook`, payload `90b730d9…`, 3088/3120 B, install `.req` `cd9e4262…`). Not yet installed.


## 9. ModsTabLook crash (warm reboot) and corrected data-offset rule

Fresh boot; loader `mod_loader_tablook` (install `.req` `cd9e4262…`, `INSTALLED hooks=15b91654,15b91a80,15b92044`) with
`MenuTouchFull.mod` and `ModsTabLook.mod` v1 (`8a4d1893…`, kept as `ModsTabLook-v1-crash-table-offset.mod`). Toggling
"Mods Tab Look" ON **rebooted the camera**. The warm reboot preserved the kernel log ring (`dump-ring`, `boots=2`,
crc `0x9d6d7f74`; raw `artifacts/hardware/mods_tab_look/crash1/ring.bin` sha256 recorded alongside): exception monitor,
`program: im.elf(uxengine_res1)`, page translation fault at `0x00398a34`, `pc 0x043212e8` = libc `memcpy`,
`r0 0x176eb180 r1 0x00398a34 r2 0x2e6c`, `lr 0x064c491b` (libObj `0x7ac91a`, memory-stream `read`).

Root cause (static, libObj): `0x5c21a4` sets the record view base to **record + 4** (ID word bit 31 clear) and
`0x7a26bc` points at `{size, offset}` inside the record; `0x7a5424` (type 6) builds the stream as
`base + offset, size`. v1 computed `offset` relative to the table base, so the resource thread read a wrong address.
Offline check with the corrected rule: consecutive blocks in DSLR/cmn are contiguous up to 0–3 bytes of 4-byte padding
and sizes are exact. Parser fixed (`block(blob, rec)` = `rec + 4 + offset`), tests updated (4/4).

Fix: ModsTabLook v2 writes `offset = 28` (pixels 32 bytes after the record), no per-call recomputation;
`artifacts/hardware/mods/ModsTabLook.mod` sha256 `4b4e6130…` (code 1616 B). Not yet installed.

## 10. ModsTabLook v2 — works, image sheared (open)

Fresh boot; loader `mod_loader_tablook` (`INSTALLED hooks=15b91654,15b91a80,15b92044`) + `MenuTouchFull.mod` +
`ModsTabLook.mod` v2 (`4b4e6130…`). User toggled "Mods Tab Look" ON: no crash, the Mods tab icon changes to our raw
image (teal band visible at the bottom), but the "MODS" glyphs are horizontally smeared/sheared. Inference
(unverified): the renderer assumes a padded row stride for raw images (e.g. width rounded up to 8/16/32 px or a
different bytes-per-row), so 91-px rows are misread. Next: test padded strides (e.g. 96 px) with `w=91` vs `w=96`.

## 11. ModsTabLook v3 — 96-pixel record and rows

Static inputs: live-build `libObj.so` SHA-256
`e88b7e0fc61b8c98ac06c1979e6ebdd3fa8a2a49384b3fd5ffc8d06da4174ef1`
and its Ghidra 12.1.4 export from §7. Addresses below are ELF-relative.

- The type-6 stream in `0x7a5424` carries the record length as-is. The memory branch at
  `0x7a550a`–`0x7a5514` passes the computed data pointer and the size word to `0x7ac9e8`.
  That path does not calculate pitch.
- `BMP_Create` (`0x67a7c4`) passes logical dimensions through `0x679148` and calls `0x6791fc`.
  Its instructions at `0x67923e`–`0x679266` enforce a minimum alignment of 8 pixels and compute
  aligned width and height through `0x6799d0` / `0x6799ec`. At `0x679288`–`0x679296` the
  allocation size multiplies those dimensions by the pixel byte count. The separate GRM path
  at `0x70cca0` also distinguishes logical width from backing width using a 16-pixel round-up.
  These are bitmap backing rules. They do not uniquely identify which upload path consumed the
  odd-width tab image in v2.
- `91 * 2 = 182` bytes per serialized row failed in §10. Rounding 91 up to either 8 or 16
  pixels yields 96, or 192 bytes per RGBA4444 row. V3 declares width 96 and serializes exactly
  66 rows of that width, with original artwork in columns 0–90 and five transparent columns.
  The loader's tab rectangle is 91×66. The record retains format `0x80000000` and offset 28,
  with size 12,672 bytes.

Build and receipts: `artifacts/hardware/mods_tab_look/v3-96px/`. Ran
`venv/Scripts/python.exe mods/mods_tab_look/make_icon.py`, the WSL ARM GCC 15.2.0 command documented
in `tools.research.build_mods_tab_look` with the ELF output in that receipt directory, then
`venv/Scripts/python.exe -m tools.research.build_mods_tab_look --elf <receipt-dir>/mods_tab_look.elf --out <receipt-dir>/ModsTabLook.mod`.
Code size 1,608 B, SMOD SHA-256 `1f38b73f9490d33c0ce612c507dca8bf9ccc4dc19892cc902d3b5eb519679414`.
The host check decoded the 639 RLE runs to the raw bytes, checked every transparent row tail,
and verified the encoded bytes embedded in the ARM ELF. Focused SMOD and UXD tests passed (7/7).
An offline Unicorn run executed the compiled Thumb lookup with a fixture for `mmap2`. It confirmed
the foreign-index bypass, cached lookup reuse, exact record fields, and byte-for-byte pixel output
(`thumb-record.txt`).
The full suite ran 1,910 tests with 15 skips and five failures in research-status SVG/README
synchronization (the generated status counts 527 evidence receipts and the checked-in SVG counts 525).

Physical procedure: user freshly rebooted into MSC. The debugger saw uptime 46.10 s and only the
standard Bluetooth entries in `/tmp_bt`. The first preflight tried `df`, which this shell lacked,
and exited before any writes. Repeated preflight at uptime 142.29 s confirmed the clean staging
area. Created `/tmp_bt/mods`, then used `tools.camera_debugger push` for the v3 SMOD, the
`mod_loader_tablook/native_mod_loader_guard_arm` and `mod-loader-check.req`. All three matched
byte-for-byte on readback. After `chmod 700`, the guard returned
`CHECK_OK code=0412f3d0..0412ffe0 bytes=3088` and `MOD_LOADER_RESULT=0 changed=0`.
Only then pushed and readback-verified `mod-loader-install.req` (SHA-256
`cd9e426247488b89a161ec0063971624e163fb24f8cb0c202ea5f1e251feed5c`) and ran the same guard.
It returned `INSTALLED hooks=15b91654,15b91a80,15b92044` and `MOD_LOADER_RESULT=0 changed=1`.
Both guard runs resumed 175 threads. Removed the owned guard and request. At uptime 182.38 s,
the camera answered and its only staged test file was `/tmp_bt/mods/ModsTabLook.mod`.
The icon is the sole mod staged for this run. No OSD or `/dev/stream` copies were used.

Physical result: the user's photo shows sharp white “MODS” text and a straight teal band while the
mod is ON (`v3-96px/camera-photo.png`). After reconnection, read-only debugger checks found the
live hook at `0x064b54b6` and read the record through `mtl_buf` at `0x114344a8`. The record was
at `0x0bd98000` in a private anonymous mapping. Its fields matched the v3 specification, and all
12,672 pixel bytes matched the generated raw image byte-for-byte. Uptime advanced from 413.27 s to
413.87 s. This demonstrates a working 192-byte row pitch with a width of 96; the minimum pitch
for an odd declared width remains unverified. The user reported the selected Mods tab stayed
the same brightness. V3 gave all three tab states the same resource ID.

## 12. ModsTabLook v4 — stock inactive, active and tab-bar focus routing

The retained `viewUnified2.so` has SHA-256
`fa534aa2160fc3e9c93119a85bdd9f1988b5f9f64b62c43fad178fabe4fa823d`.
Its original header loop compares each index to the current tab at `0x5abd10`–`0x5abd14`.
Other tabs request property 29. The current tab requests 30 when `menu+0x1d8` is zero,
or 31 with tab-bar focus. The getter at vtable +0x80 obtains the resource ID and the
original widget setter at vtable +0x104 updates the icon.

The new loader supplies `0x020cc012`, `0x020cc019` and `0x020cc01a` for those three properties
only when the ModsTabLook hook is live and the `MTL4` capability word is present. Older hook
payloads receive the previous single ID. With no hook the Mods tab delegates to the My Menu
resource getter. All three chosen indices are `0xffff` in the original DSLR table; adjacent
`0x13` and `0x14` are occupied. The ModsTabLook hook lazily builds one 96×66 record per state.
The artwork retains the white wordmark, sizes and positions it closer to the stock glyphs,
and uses a full-width teal gradient with distinct brightness levels. Resource selection
and rendering run through the camera's original menu code.

Build receipts: `artifacts/hardware/mods_tab_look/v4-states/`. Repeated the v3 generator and
ARM build commands for the new output directory and ran
`wsl.exe --exec bash scratch/build_mod_loader_variant_wsl.sh artifacts/hardware/mods_tab_look/v4-states/loader`.
The shared run stream and three palettes occupy 1,486 B. V4 SMOD code and data occupy 1,824 B.
The loader fits its 3,120 B slot exactly. The matching install request SHA-256 is
`eb033bfab2c202ddfee6143c3b36ee12fecfbed19752aad46076275277d7cb91`.
The v4 SMOD SHA-256 is `0fa7abe1ebda55646a18e93eeee640cb9c35efe5229a6298e87b26b849a0ac41`.
The bounded Thumb replay checked all three records and pixels against the generator, the
lookup fallback for an occupied index, cached reuse and loader routing with the mod
disabled, v3 enabled and v4 enabled (`thumb-check.txt`).

V4 physical install repeated the §11 sequence with the v4 SMOD and matching loader in a
fresh MSC boot. Uptime was 51.84 s with no prior staging files. The SMOD, guard, check
request and install request all matched on readback. The check returned
`CHECK_OK code=0412f3d0..04130000 bytes=3120`, result 0, changed 0. Install returned the same
three hook addresses as v3, result 0, changed 1. Both operations resumed 175 threads.
The debugger responded at uptime 92.49 s after removal of the guard and request. Only
the v4 SMOD remains staged. The host suite again reported the same five status-SVG/README
failures, 15 skips and no runtime errors across 1,910 tests.

The user confirmed the three-state artwork worked on the camera. After reconnecting without a
reboot, the debugger responded at uptime 283.72 s and all three cache pointers at
`0x114344dc` were nonzero (`0x0d99f000`, `0x0c066000`, `0x0bd98000`), showing that the
hook had built all three images. The user then requested that the artwork become the
Mod Loader default and that MenuTouchFull be included in the combined test.

## 13. Default artwork in the Mod Loader and MenuTouchFull coexistence

The standard WSL build now compiles the menu loader and artwork together and emits version 2
check/install/remove requests. The artwork retains its own 1-page RX slot in `libptp.so` and
the same 12-byte resource hook. Its three mutable pointers are reserved at the end of the
loader's existing anonymous state mapping, leaving the executable slot read-only. The
firmware still chooses the three resources from menu properties 29–31. The normal build
skips standalone Mods Tab Look payloads targeting the now-owned artwork slot.

The version 2 request appends the artwork size, Thumb entry, FNV-1a and bytes to the loader
request. The guard checks both executable mappings, both stock code ranges, the exact hook
prologues, tab rectangles and an empty loader anchor while the threads are stopped.
It writes and reads back both code ranges before arming the hooks, and on failure attempts to
restore and verify all touched ranges. The artwork branch at the halfword-aligned address is
written through aligned words with adjacent halfwords preserved. Removal is supported before
the loader publishes its menu state. Once the menu can hold references to the loader's
fake nodes, reboot is the supported way to unload the combined build.

Build command:
`wsl.exe --exec bash tools/native_camera/build_mod_loader_wsl.sh artifacts/hardware/mods_tab_look/builtin-touch-final`.
The combined hardware test uses the retained MenuTouchFull v3 SMOD from
`mods/menu_touch_full/MenuTouchFull.mod`, SHA-256
`334a3dd491d04a87e5318694ac652fca65dfe7cd2575e7e6886620bc282b1b80`.
Loader: 3,096 B, SHA-256
`c39c4548b49d26acb435ecde303f03959499969e61d55aa01817ae1063e87efe`.
Built-in artwork: 1,848 B, SHA-256
`902f294fa49b99b0da399a82b9a769eeae62067826f0f5a7726032387618890f`.
Combined install request SHA-256:
`bd4cdd3dcd21e21eb9a0dfc000a977588d41de13363559e618b2a3af8c47b722`.
A second build through the repository entry point reproduced the request exactly.
The Thumb replay kept both code pages RX and verified default property routing, three exact
records and pixels, the anonymous cache and the cold-anchor fallback. The six loader tests
passed. The 1,911-test suite recorded the same five status-SVG/README failures and 15 skips.
The first hardware preflight stopped without writes at uptime 718.10 s because the previous
v4 staging directory was still present. A later read at 1,099.89 s confirmed the camera was
responsive on the same boot and still had that staging directory. The combined request and
MenuTouchFull were not installed on this boot. Physical verification of the combined build
is pending a fresh MSC boot.

### Fresh boot and loader relocation

The user rebooted into MSC. At uptime 50.83 s the staging directory was absent. MenuTouchFull,
the guard, and the check request were uploaded with exact readback. The check stopped with
`PATCH_MISMATCH address=0412f3d0`, result -12, changed 0, and resumed 175 threads.
The bounded read `builtin-touch-final/loader-stock.bin` showed nonzero ELF metadata in that
part of the legacy `libMWF.so` mapping. The legacy location is excluded from the combined build.

The matching extracted `libmprctrl.so` has executable `PT_LOAD` file end at offset `0x393a0`.
In the live RX mapping `0x045ab000–0x045e5000`, that is `0x045e43a0`, with 3,168 bytes to the
page boundary. A 3,096-byte process-memory read there was all zero (SHA-256
`9077a7b633be5045840169c6b10fbec400fe3dcb1ffe071fc12c7a5a915fa592`). Candidate reads and maps
are retained beside the failed guard receipt. The combined build now links the loader at
that address, with the artwork unchanged. Relocated loader SHA-256
`d5744d1b15c69f2b79dbabc2d4f979e1e8d22bd9178518af3fbf345864122186`.

The retry used the same clean boot. The replacement guard and check request were readback-verified.
The guard reported `THEME_OK code=11434398..11434ad0 hook=064b54b6` and
`CHECK_OK code=045e43a0..045e4fb8 bytes=3096` with changed 0. The readback-verified install
request SHA-256 was `80e8a717d1101d260c4ac482950f277c4fda66e295a0c9585c8f9f9eaaa16bdb`.
Install reported the three view hooks installed, result 0 and changed 1. Both operations resumed
175 threads. At uptime 301.24 s, the camera answered with just `MenuTouchFull.mod` staged under
`/tmp_bt/mods`. The guard and request were removed. No standalone artwork mod was deployed.
Receipts are in `artifacts/hardware/mods_tab_look/builtin-touch-live/`. Six loader tests passed
again. The full suite reported the same five status-SVG/README failures and 15 skips across
1,911 tests.

The user confirmed the combined on-camera visual and touch test succeeded. The camera remained
unavailable in MSC/MTP at the post-test debugger check, so the interactive result is a physical
operator report and no independent post-test image or touch-event readback was collected.
