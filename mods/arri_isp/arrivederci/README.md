# Arrivederci — ARRI Classic look mod

**Status: usable as part of modset v2 (2026-11-17; Mod Loader v2, fitted Classic matrix, E0556).** Earlier status: functional (verified on the camera, 2026-11-16). With PP Gamma S-Log3 and Color
Mode S-Gamut3.Cine:

| Mode | Tone curve | Color matrix |
| --- | --- | --- |
| Movie | Yes | Yes (selector index 23 → entry B8). Toggled from the Mods tab under Mod Loader v2, it applies when you leave the menu; no Color Mode round trip ([E0555](../../../docs/evidence/0555-arrivederci-menu-exit-recompute-and-cc8-refill.md)) |
| Photo | Yes | No: photo mode keeps index 0 (entry A0) for every Color Mode, so A0 is not patched |

A copy is on the SD card as `E:\mods\Arrivederci.mod` (hash verified), with a status note
`E:\mods\Arrivederci.txt`. The current SD boot package does not load it automatically (see
Requirements in the note); install it with the lifecycle loader.

`Arrivederci.mod` (1404 bytes, SHA-256 `6b3d383a4dc59c1b017f0ae5b6350c8434f1ae96bf13c418971af1f1cd6bcf36`)
combines the two tested ARRI layers in one Mods-tab entry for the ILCE-6400 firmware image
identified in [E0505](../../../docs/evidence/0505-av-cam-cc-3dlut-and-ec-gamma-pipeline.md):

| Layer | Target (LIRO RAM) | Effect | Survives a restart? |
| --- | --- | --- | --- |
| Tone curve | EC record 31 (S-Log3), 96 words at PA `0x03e5363c` | ARRI Classic Rec.709 tone instead of S-Log3 | **No.** Reloaded from the read-only image at boot. |
| Color matrix | Matrix table entry B8 (S-Gamut3.Cine, index 23), 9 int16 at PA `0x0444b1d4` | ARRI Rec.709-primary matrix | **Yes, until turned OFF.** Sony's backup saves the table at power-off. |

ON writes the ARRI data only where Sony's stock data is present; OFF restores stock only where
the ARRI data is present. Anything else is left untouched. Because both checks compare content,
OFF also works in a later boot. Each toggle appends `arrivederci on=N gamma=C color=C` to
`/tmp_bt/mods/arrivederci.log` (`w` written, `a` already in place, `x` unknown content left
alone, `v` verify failed).

## Mod Loader v2 (2026-11-17)

The SMOD v2 build (`build_v2_wsl.sh`, `Arrivederci.mod` 1424 B, SHA-256 `90dc6290…eaf3`) was
staged in `/tmp_bt/mods` beside a RAM-resident v2 core. ON and OFF from the Mods tab, followed by
leaving the menu, switched tone and color in both directions without a Color Mode change. B8,
both CC live copies and record 31 read back as expected (E0555). The color matrix is still
backup-backed, so turn Arrivederci **OFF before power-off**. The SD package does not ship it.

## Fitted Classic matrix (2026-11-17)

`fit_matrix.py` refits B8 against the official ARRI LogC3 Classic LUT. It models the camera order: matrix, then the tested record-31 curve, then 8 bits. `build_v3_wsl.sh` builds it, and ON/OFF also recognize the previous matrix (`color=l`). Fitted Q10 matrix `[[1698,-670,-4],[-105,1408,-279],[90,-478,1412]]`. In the model, skin ΔE00 goes from 1.20 to 0.58 and saturated highlights from 11.5 to 4.7. Build `artifacts/tests/arrivederci-v3-build-1` (`Arrivederci.mod` 1516 B, SHA-256 `c09693b8…a88a`) ran on the camera; see [E0556](../../../docs/evidence/0556-arrivederci-classic-fitted-matrix.md).

## Color matrix persistence (reversible, by design)

The matrix table is part of Sony's persistent backup data (`/setting/Backup.bin` / `Backup.bak`,
file offset 207840). The camera saves the RAM table at power-off and restores it at boot. With
Arrivederci ON at power-off, the ARRI matrix is therefore saved and comes back in the next boot,
while the tone curve returns to stock.

- **To return fully to stock:** with the lifecycle loader and Arrivederci loaded, toggle ON then
  OFF (ON reports `color=a`, OFF reports `color=w`), do a Color Mode round trip, then power off
  normally with the switch. The camera then saves stock into both backup files. This recovery
  was verified on 2026-11-16.
- A battery pull does **not** revert the matrix. It keeps whatever the last backup save held.
- The stored values are a valid, row-normalized Q10 matrix, and no tool writes flash directly.

## Historical test setup

The lifecycle loader invoked the mod while the camera was set to S-Log3 /
S-Gamut3.Cine. Gamma took effect immediately. The Color Mode round trip triggered
recomputation of the matrix. Subsequent testing showed that the B8 replacement
was copied to backup and could survive a restart. The EC gamma replacement
was volatile. See E0505 for the backup and restore receipts.

## Rebuild and test

```bash
wsl.exe --exec bash mods/arri_isp/arrivederci/build_wsl.sh artifacts/tests/arrivederci-new
python mods/arri_isp/arrivederci/test_mod_unicorn.py artifacts/tests/arrivederci-new/Arrivederci.mod
```

Not included yet: the 5×9×9 YCbCr 3D LUT layer (no Movie target located). Encoder range and
scale are unmeasured. A fully restart-reversible color layer would need a non-backed matrix
source at recompute time; see E0505, "GOT-redirect feasibility".
