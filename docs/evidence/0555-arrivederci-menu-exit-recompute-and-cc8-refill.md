# E0555 - Arrivederci applies without a Color Mode round trip; CC+8 is refilled every frame

Date: 2026-11-17. ILCE-6400 in MSC with the extcmd debugger (pid 703). Mod Loader v2 was
resident in RAM (installed the same day, see `docs/MOD_LOADER_V2_DESIGN.md`, MenuTouchFull
camera run). The user approved a RAM-only CC+8 test write and asked for Arrivederci to be
staged. Receipts: `artifacts/analysis/arrivederci-live-target-1/`.

## Inputs

| Item | Identity |
| --- | --- |
| Mod Loader v2 core | `mlv2-sd-build-3/core.bin`, SHA-256 `552220a4…8b3e`, FNV `0x529057cd` |
| Arrivederci SMOD v2 | `arrivederci-v2-build-1/Arrivederci.mod`, 1424 B, SHA-256 `90dc6290…eaf3` (M1 build, unchanged) |
| Guarded writer | `arri-gamma-poke-build-2026-11-16-v4/gamma_poke`, SHA-256 `89f6f412…8c4b` |
| Test blocks | `cc8-identity36.bin` `d68309e6…beff`, `cc8-swaprb36.bin` `0e5f7ac3…1843` |

All `/dev/mem` reads were bounded single-page reads at `VA - 0x60000000`.

## 1. Backup list and idle state (read-only, `live-read-1.json`)

- The 169-entry backup source list at `0x64561440` holds no pointer between `0x64359d9c`
  and `0x64438254`. The CC output object `0x643cc384` is therefore not a listed source.
  Sizes were not read, so this is a pointer-level check. It agrees with E0505's earlier
  content check of `Backup.bin`.
- In MSC idle: CC+8 and the matrix stage block `0x6446e152` hold the Q10 identity. Index
  23, B8 and both live copies are stock.

## 2. CC+8 write test (`movie-pre.json`, `cc8-swap-write.json`)

The user set Movie, PP S-Log3 / S-Gamut3.Cine, with live view. Pre-read: the gamma buffer
starts at 84 (record 31). CC+8 and the stage hold the identity. Index 23, B8 and copies stock.

- First attempt: the host computed PA `0x033cc38c` (wrong; the correct PA is `0x043cc38c`).
  The writer refused with `ERR address outside policy`, exit 2, before mapping. No write
  happened. Three 36-byte reads at that wrong PA returned zeros, and they are kept in the
  receipt as a mistake. E0505 also quotes `PA 0x033cc4b0` for copy +0x12c. That value is
  outside the writer's policy window and is likely a transcription error for `0x043cc4b0`.
- Second attempt at PA `0x043cc38c`, expected identity → red/blue swap: `WROTE`,
  `map+0ms new`, `map+20ms other`, `map+200ms expected`, `pread expected`, exit 5. Samples
  at 0.3, 1.0, 3.0 and 8.0 s: CC+8 and the stage block were the identity.
- Conclusion: CC rewrites CC+8 within about one frame. A data write there cannot carry a
  look. In S-Log3 the stage stays the identity, so this stage does not apply the
  S-Gamut3.Cine matrix (consistent with E0505's Movie reading). The helper directory was
  removed (rc 0).

## 3. Arrivederci without a Color Mode change

`Arrivederci.mod` was pushed to `/tmp_bt/mods` (readback verified, atomic rename). Baseline:
B8, copies stock, index 23, record 31 word 0 `0x1500`.

- **ON** (`arrivederci-on-no-roundtrip.json`): the user toggled Arrivederci ON in the Mods
  tab and left the menu without changing Color Mode. The user reported that tone and colors
  changed. Readback: loader log `mod_on 05d15000`, `arrivederci on=1 gamma=w color=w`. B8
  and **both live copies (+0x12c, +0x686)** hold the ARRI matrix `[1884,-828,-32; …]`.
  Record 31 word 0 is `0x300`. CC+8 is still the identity.
- **OFF** (`arrivederci-off-no-roundtrip.json`): toggled OFF and left the menu, again without
  a Color Mode change. The user reported the stock image. Readback: `mod_off 05d15000`,
  `arrivederci on=0 gamma=w color=w`. B8, both copies stock, record 31 `0x1500`.

Observation: in both directions, the CC matrix recompute happened between the Mods-tab
toggle and the readback, with no Color Mode change. Inference: leaving the menu (or a step
of the menu session) triggers the recompute. This matches E0505's unexplained overwrite of
the copies after a menu interaction. The exact trigger call was not isolated. A toggle from
somewhere other than the menu (for example a future SD watcher) is not covered.

## Assumptions, limits and state

- The mode and image changes are user-reported. Readbacks prove the data path, not the
  rendered pixels.
- Backup persistence is unchanged: B8 is still Sony's backed-up table. Arrivederci must be
  OFF before power-off. At the end of this run it is OFF, and the B8 and record 31 RAM reads
  are stock.
- Uptime advanced from 20661.89 s to 21190.68 s across the run, the debugger answered `PONG`
  after every set, and `/tmp_bt` holds only the loader and mod files.
- Camera state left: Mod Loader v2 resident, MenuTouchFull ON, Arrivederci staged and OFF.
  A restart returns the SD package's v1 loader.

## Next boundary

The Color Mode round trip is no longer needed for Mods-tab use. What remains for a
backup-safe color layer is either an off-before-backup handoff on the power-off path or a
matrix source that the backup cannot read (E0505 section on GOT redirect and the selector
trampoline in `MOD_LOADER_V2_DESIGN.md`).
