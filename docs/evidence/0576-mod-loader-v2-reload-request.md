# E0576 — Mod Loader v2 reload request (offline)

Date: 2026-11-17. Offline. No camera, SD, USB or QEMU guest access.

## Purpose

This record gives the mod development loop a single reload step:
build → reload → test in the same Sony menu session. The reload replaces
an enabled SMOD v2 mod with the new file in one step.
E0575 showed that the shipped guard does not qualify the QEMU `im.elf`:
the PID, the view hook addresses and the shim cave differ. A live QEMU
install and ON/OFF cycle therefore remain blocked. This record covers
the loader contract only.

## Change

`tools/native_camera/mlv2/core.c` (core only; shim, guard and request
are unchanged) consumes `/tmp_bt/mlv2/reload` on the Sony UI thread.
It runs from `before_set_root` (before the rescan) and from
`after_refresh` (in place). The request holds one file name with no
directory. The core deletes the request first. It then writes one line
to `/tmp_bt/mlv2/reload.result` and the loader log. For an enabled mod
the order is:

1. Parse the new file. Only SMOD v2 is accepted. Load it into a fresh
   RWX mapping and verify the FNV hash.
2. Check each new hook. It must either replace an old hook exactly
   (same target, size and original bytes) or sit on bytes that are
   original now and do not overlap an old hook.
3. Switch the old copy OFF: run `off()`, revert and read back the hooks.
   If OFF fails, or any old target does not hold its original bytes,
   the replacement stops and the old copy shows `ERR`.
4. Re-check that every new target holds its original bytes. Then apply
   the new hooks and run `on()`.

A failure in steps 1–2 leaves the old copy running unchanged. The old
mapping is never unmapped. A thread still inside an old hook therefore
finishes in valid code with its own bss. The new copy starts with zeroed
bss, so no gesture or other state carries over between versions.
A request for a disabled mod, or for a file that is not loaded yet,
reports `reload_scan`. The same root-setup scan then lists a new file
as `OFF`. Each reload leaves one old mapping allocated until restart.

Result codes are `reload_ok <base>`, `reload_scan <0|1>` and
`reload_err_{request,unlink,state,file,kind,load,target,off,on}`.

## Build and checks

```powershell
wsl.exe --exec bash tools/native_camera/mlv2/build_wsl.sh artifacts/tests/mlv2-reload-build-1
.\venv\Scripts\python.exe -m tools.native_camera.mlv2.test_mlv2_unicorn artifacts/tests/mlv2-reload-build-1 artifacts/tests/arrivederci-v3-build-1/Arrivederci.mod artifacts/tests/mtf-v2-build-1
.\venv\Scripts\python.exe -m unittest discover -s tests -q
```

| File | SHA-256 |
| --- | --- |
| `mlv2-reload-build-1/core.bin` (6204 B) | `1a1054615de9689ee3e0077013965536b03ca1b18a07e617de12aab06399a4a1` |
| `shim.bin` (identical to `mlv2-sd-build-3`) | `7f38b9f2f03103d91695c1828a5c07a849306642df729d421023a2bd8d7a79bf` |
| `mlv2_guard_arm` (identical) | `1d84f2535acc0c40eee11ae44446366a9bfb54418420e39b657fc86818402c9d` |
| `mlv2.req` (identical) | `fac109bb7bbd269c5ac552c973438ca6d0cb23269dccfb3c683c3e6cb200d49e` |

The Unicorn harness runs the original shim and core ARM code with emulated
syscalls and a modeled menu. Scenarios 1–10 pass unchanged on the new core.
New scenario 11 uses TestMod builds and covers:

- a replacement into a fresh mapping, where the old `off()` and new `on()`
  each ran once;
- refusal of corrupt code (`reload_err_load 6`) and of a foreign target
  (`reload_err_target`), both before OFF, with the old hook intact;
- a reload from the refresh hook that moves the hook to a new target and
  restores the old target;
- a rejected and consumed request that contains a directory;
- a failed OFF (`reload_err_off`) that stops the replacement and shows `ERR`;
- a new file that is listed `OFF` by the same scan.

New scenario 12 reloads MenuTouchFull v2 (`mtf-v2-build-1`) while gesture data sits
in the old bss. Both hooks moved to the new base. The old state had magic and enabled
cleared and kept its data. The new state was published with an empty gesture state and
event log. OFF after the reload restored both prologues. The `mlv2-sd-build-3` core still
passes scenarios 1–10; the harness skips scenarios 11–12 for cores without the request path.
`mods.menu_touch_full.test_mtf_v2_unicorn` passes. The full suite passed
1982 tests with 18 skipped. Receipts are `unicorn.txt` and `full-suite.txt` in the build directory.

Host side: `tools/research/reload_mod.py` does the following, all under `/tmp_bt`:

- optionally stages a reload-capable `core.bin`;
- pushes the mod to `.NAME.part`, which the scan ignores, and reads it back;
- renames the mod into place and publishes the request with temp + rename;
- waits for the result while the user moves in the Sony menu;
- on `reload_ok`, compares the hooks and the resident code with the file.

`tests/test_reload_mod.py` covers its parser and expected hook bytes. The tool was **not** run.

## Not shown

- Nothing ran on the camera or in QEMU. Executing on the UI thread, picking up a core
  upgrade and reloading in the same menu visit are all modeled, not observed.
- The core does not stop other threads. If a touch handler runs on another thread, a
  gesture in flight finishes in the old copy and its state is not carried over.
  The on-device thread for MenuTouchFull's handlers is not verified.
- The QEMU guest needs a target-aware guard and address profile (E0575) before it can install the
  loader. The physical camera needs an explicit go-ahead to stage this core and run `reload_mod`.

Next: with a go-ahead, stage `mlv2-reload-build-1/core.bin` on the camera that already
runs the v2 loader. Then reload MenuTouchFull and check row, swipe, popup and OFF.
Separately, build the QEMU target profile from E0575.
