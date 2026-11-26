# PhotonBin 6MP (`PhotonBin6MP.mod`)

Relocatable **`SMOD v2`** low-light photography mod for **Sony `ILCE-6400A` (FW `1.01`)** and **Sony `ILCE-6100` (FW `1.00`)** combining:

1. **Option 1 — Pre-Demosaic `4×4 → 2×2` Bayer & `2×2` Monochrome Photon Convergence into `.ARW` (`ImageBuilder::MakeRawInfoArea` hook in `libObj.so`):**
   - Decodes 14-bit `6024×4024` RAW captures from Sony `ARW 2.3` `cRAW` (`Compression = 32767`, using each frame's `Tag 0x7010` `SonyToneCurve` knots) or 16-bit Uncompressed RAW.
   - Subtracts optical black (`512` ADU) so black pedestal never multiplies, converges 4 photosites into a **6.0 MP (`3008×2008` total, `3000×2000` active at `(4,4)`)** output frame, and writes out a self-consistent `.ARW` with updated `SubIFD` tags (`ImageWidth`, `ImageLength`, `BitsPerSample`, `Compression`, `SonyRawFileType`, `RowsPerStrip`, `StripByteCounts`), `rawAdditionalInfo` (`DefaultCropOrigin`, `DefaultCropSize`, `WhiteLevel`), and trimmed physical DMM extents.
2. **Option 3 — Hardware `IMX284` Analog Readout Overrides (`av-cam.bin` LIRO `.text` via `/dev/mem`):**
   - **`FD Conv Gain` (`Mconv`, P2S reg `0x099C`):** Overrides Main (`PA 0x0379FEC6`) and ZAF (`PA 0x0379FF7C`) floating-diffusion conversion gain (`Force HCG` `+9.805 dB` / `3.092×` at all ISOs, `Stock Auto` ISO 400 crossover, or `Force LCG`).
   - **`14b 1H2H Read` (`ADnum = 2`):** Overrides `CaptureUtilityImager` Silent Shutter (`PA 0x038ECC48`) and Bulb (`PA 0x038ECC56`) gates so 14-bit dual-ADC readout remains active instead of dropping to 12-bit `1H1H`.

See **[E0680](../../docs/evidence/0680-photonbin-6mp-and-mlv2-nested-submenus.md)** for full disassembly, SNR math, and `ILCE-6400A` `1.01` QEMU menu screenshots, and **[E9104](../../docs/evidence/9104-a6100-photonbin-6mp-port.md)** for the `ILCE-6100` `1.00` port and physical camera verification.

---

## Nested Menu Options (`MENU -> Mods -> PhotonBin 6MP`)

Requires **Mod Loader v2 `core.bin` `CTX_VERSION >= 4`** (`SMOD_V2_FLAG_SUBMENU` / `SMNU`; `v3` renders it as a plain toggle). When updating `MLV2.TAR` on an SD-boot camera, also update the expected SHA-256 in `/setting/mlv2_sd_boot.sh`. Pressing `Center` on `PhotonBin 6MP` (`OFF >` / `ON >`) opens its 2-page nested submenu:

| Page | Row | Setting | Options (`*` = Default) | Behavior |
| :---: | :---: | :--- | :--- | :--- |
| `1/2` | `0` | `< Back` | — | Returns to the top-level `Mods` tab |
| `1/2` | `1` | `Mod Status` | `OFF` / `ON` | Enables/disables the mod and restores all stock bytes on `OFF` |
| `1/2` | `2` | `Binning Mode` | `4x4->2x2 Bayer`* \| `2x2 Mono Quad` \| `Sensor Only` | 6 MP RGGB Bayer convergence, 6 MP panchromatic `(R+Gr+Gb+B)` quad sum, or 24 MP unbinned passthrough |
| `1/2` | `3` | `Photon Gain` | `+2 EV Sum (4x)`* \| `+1 EV Mid (2x)` \| `+0 EV Avg (1x)` | Sensitivity vs. highlight headroom scaling above `512` black level |
| `1/2` | `4` | `Spatial Kernel` | `[1,2,1] Phase`* \| `2x2 Box Sum` | `[1,2,1]/[1,3,3,1]` uniform `2.0`-px Cartesian lattice filter (eliminates `1:3:1:3` Bayer moire) or direct `2×2` quad sum |
| `1/2` | `5` | `ARW Output` | `16-bit Uncomp`* \| `14-bit Uncomp` \| `14-bit cRAW` | `12.08 MB` lossless 16-bit (`0..63,996`), `12.08 MB` 14-bit (`0..16,383`), or `6.04 MB` re-encoded Sony `ARW 2.3` `cRAW` |
| `2/2` | `0` | `FD Conv Gain` | `Force HCG`* \| `Stock Auto` \| `Force LCG` | Live LIRO `IMX284` `Mconv` control (updates immediately via `pb_cfg` while `ON`) |
| `2/2` | `1` | `14b 1H2H Read` | `Bulb + Silent`* \| `Stock Gate` | Live LIRO 14-bit `1H2H` gate control (updates immediately via `pb_cfg` while `ON`) |

---

## Build & Verify

```powershell
# Build in WSL (from the repository root)
wsl.exe -- bash mods/photon_bin_6mp/build_wsl.sh artifacts/mods/ilce_6400a/photon_bin_6mp --target ilce_6400a
wsl.exe -- bash mods/photon_bin_6mp/build_wsl.sh artifacts/mods/ilce_6100/photon_bin_6mp --target ilce_6100

# Run offline Unicorn verification suite (Option 3 + Option 1 + ARW2 round-trip)
.\venv\Scripts\python.exe mods\photon_bin_6mp\test_mod_v2_unicorn.py artifacts\mods\ilce_6400a\photon_bin_6mp
.\venv\Scripts\python.exe mods\photon_bin_6mp\test_mod_v2_unicorn.py artifacts\mods\ilce_6100\photon_bin_6mp
```
