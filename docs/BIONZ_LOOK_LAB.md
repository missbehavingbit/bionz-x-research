# BIONZ Look Lab

Host studio for the evidence-backed BIONZ color chain. It lives in [`isp-studio/`](../isp-studio/) and does not talk to a camera, USB, or `/dev/mem`.

A universal preset is a `.bzlook` zip. [`isp-studio/docs/PRESET_FORMAT.md`](../isp-studio/docs/PRESET_FORMAT.md) is the layout. The twelve Film Sims recipes are packed under `mods/film_sims/presets/`. Their compiled tables use the hash-verified `av-cam.bin` stock and carry both an `a6400` and an `a6100` table plus a Movie variant for each.

## Portable release

`scripts/build_look_lab_release.ps1` produces `dist/BIONZ-Look-Lab-portable/bionz-look-lab.exe` (and a zip). The executable is self-contained: the twelve presets and the ARM `SMOD v2` template are compiled in, so it runs from an empty folder with no Python, GCC, WSL or preset files. It is built with the statically linked MSVC CRT (`isp-studio/.cargo/config.toml`, `+crt-static`) and `windows_subsystem = "windows"`, so it needs no Visual C++ redistributable and opens no console window.

Two scripts check the promise without a camera:

- `scripts/verify_look_lab_export.ps1` copies the exe into `C:\Temp\LookLabTest` (nothing else in it), runs `bionz-look-lab.exe --export-mod <out> --look "Classic Chrome" --bodies both`, and asserts `preset_dir = null` (all twelve looks came from the bundle), that `out\a6400\mods\<Look>.mod` and `out\a6100\mods\<Look>.mod` exist, that each starts with `SMOD` version 2 / `FLAG_ACTION` and matches its FNV-1a 32, that the two bodies differ in the address payload, and that a single body written into a folder already named `mods` does not create a nested `mods` folder.
- `scripts/smoke_look_lab_portable.ps1` starts the same copy the way a double click does (`cmd /c start`) and checks that the process is still alive with a main window after 8 s.

`--export-mod` is the Export dialog's write path driven from the command line: it loads the library with `looks::load_library_with_bundled`, picks the look by name, and calls `looks::export_mods_report`, the same function the dialog's **Export .mod** button runs. It prints a JSON receipt (`presets_loaded`, `presets_built_in`, `preset_dir`, `files` with size and SHA-256). Without a recognised flag the process starts the GUI as usual.

## Using the app

```powershell
cd isp-studio
cargo run --release -p bionz-app
```

You can also pass files: `bionz-look-lab.exe <photo.ARW> [<camera.JPG>]`.

1. **Open photo / RAW** (Ctrl+O), or drop a file on the window. A Sony ARW is decoded from the CFA: the file's black and white levels, as-shot white balance, the file crop, a 2×2 super-pixel demosaic and the orientation tag. No host color matrix is applied. JPEG, PNG and TIFF files are display images (badge S), so they start at −3 EV. **Try the test chart** loads a built-in ColorChecker-style chart.
2. **Pick a preset** in the left list. The list starts with the twelve presets compiled into the executable, then adds every `.bzlook` found in `BIONZ_PRESETS`, or in `presets/` or `mods/film_sims/presets/` next to the executable or the working directory. A disk preset with the same name replaces the built-in copy. **Import preset** adds a `.bzlook`, a `.cube` (fitted onto the ISP tables) or a legacy `.a6lt`.
3. **Tune** the look with the Tone, Color, Hue sectors and Split toning sliders. Releasing a slider refits the tables in the background (17³ cube). A dot (•) marks unsaved changes. **Save preset as…** (Ctrl+S) writes a `.bzlook`.
4. **Compare** with the Emulated, Split (drag the divider), Side by side, Difference, ΔE map and B views. B is the same image through the preset's stock tables. The label reads "Stock (synthetic)" for the shipped presets. To compare against the real camera, shoot RAW+JPEG with the preset active and use **Add camera JPEG**. Dropping a JPEG with the same file name as the open ARW also works. When a same-stem `.JPG` already sits next to the ARW, **Use paired .JPG** switches side B to it and **Simulated Stock (.jpg)** switches back, so the camera JPEG never replaces the stock baseline without a click. The bottom bar shows ΔE mean and p95 for the current A and B.
5. **Export Camera Mod (.mod)** writes finished `SMOD v2` files for `<SD>:\mods\`, in two kinds. **Single look**: one `.mod` per look, switched in MENU -> Mods. **Live picker**: one `FilmSimulations.mod` holding every exportable look, which opens in Live View and switches looks with Left/Right (or Up/Down); ENTER locks the choice, MENU leaves it armed. Pick `a6400 + a6100` to get `<folder>\a6400\mods\…` and `<folder>\a6100\mods\…`, or one body to write `<folder>\mods\…` (or `<folder>\…` when the folder is already named `mods`, so `<SD>:\` and `<SD>:\mods\` both work). Both are generated inside the app by `bionz-preset::smod_v2` from committed ARM Thumb payloads: no Python, WSL or compiler is needed, and the body's physical addresses and the preset tables are written into a fixed payload, with an FNV-1a 32 checksum over the code. They refuse tables with synthetic stock (the mod restores `*_STOCK` when it returns to stock, and `COLOR_A0_PA` sits in the backup-persisted matrix table, imaging hazard H01) and presets that carry a Color Mode base matrix or `/usr/sabin` tables (those belong to `mods/arri_isp/arrivederci`). The older **Advanced: write C headers** button still writes `film_sim_data_<target>.h` and `film_sim_live_data_<target>.h` for the `mods/film_sims` build.

The **Input** section sets exposure, the gamma path and the encoder range. Exposure is the gain before the chain: RAW sensor white maps to the top EC knot at 0 EV. The gamma path is one of: Movie EC only (default), Still FC only, or either experimental order. **ISP stages (advanced)** bypasses stages 6–14 and shows the selected stage's output on a small copy of the image. The view also shows the EC/FC curves against stock, the A0 matrix and a dY slice of the 5×9×9 LUT.

GPU: the chain runs on one persistent wgpu device. The backend order is DX12, then Metal, then Vulkan. Set `BIONZ_GPU_BACKEND=vulkan|dx12|gl` to choose one, and `BIONZ_SOFTWARE_GPU=1` to force the software adapter. Vulkan on the AMD RX 9070 XT test machine crashed the parity tests with an access violation, so it is last in the order. A 1500×1000 preview takes about 60 ms on that GPU, including readback and texture upload.

## What the host checks

| Crate | Check |
| --- | --- |
| `bionz-core` | Gamma, matrix, `apply_ycc` bits, 8-bit encode, and eSRAM words against the Python oracle. The integer chain is the GPU model. |
| `bionz-preset` | A6LT round trip and `film_sim_data.h` / live header text against the golden headers. `smod_v2` emits `SMOD v2` `.mod` files from the committed ARM Thumb payloads: header shape, FNV-1a 32 over the code, the body's seven physical addresses, every table, the refusal of synthetic stock and of base-matrix presets, and a `.mod` for all twelve presets on both bodies; for the Live Picker, one hook, 48 bytes of bss, `FLAG_ACTION|FLAG_RELOC`, one relocated word, fifteen slots and the per-look tables. |
| `bionz-gpu` | The post-gain RGB is cached, then detail, the residual LUT, and the encoder run from that cache. Changing only the LUT level leaves the cache identical and changes the encoded taps. The same chain matches the CPU on the rail pixels and on the compiled Classic Chrome a6400 tables. A LUT-level edit can rerun detail, the residual LUT, and the encoder from the stored post-gain cache. `bionz-cli bench` prints the host wait for those two submits. |
| `bionz-raw` | `rawler` decodes an ARW or DNG into an integer CFA. Black, white balance, and a selectable demosaic then feed the integer chain. A `dcv` center slice and `spica` taps load only after a SHA-256 match. A video frame is extracted only by an external ffmpeg binary. |
| `bionz-fit` | Recipe probes match `evaluate_recipe_3d` within 1e-6. The cube fitter matches the golden tiny cube exactly. On the seven Film Sims recipes and both ARRI targets, its ΔE76 is less than or equal to the Python preset scored on the same samples, and the rail count matches. |
| `bionz-target` | a6400 and a6100 Film Sims addresses from `studio_core`. ILCE-6400A addresses stay unknown. |
| `bionz-compare` | CIEDE2000, an absolute difference, a ΔE heatmap, a 6×4 patch-grid search, phase correlation on power-of-two luma, and a one-pixel SAD check. A detected grid is a layout candidate. Receipts set `promotes_badge` to false. |
| `bionz-cli` | `pack` (refuses synthetic stock unless `--allow-synthetic-stock`), `smod` (one look, one `SMOD v2` `.mod` for `a6400` or `a6100`, no compiler), `live-mod` (a folder of `.bzlook` presets into one Live Picker `FilmSimulations.mod`), `bench`, `compare`, `parity`, and `render` (RAW or image through a preset, or through its stock tables with `--stock`, to PNG). |
| `bionz-py` | `bionz_isp.fit_cube` returns gamma, matrix, and delta. `simulate` runs that fit through `apply_ycc`. `compile_preset` and `pack_live` emit Film Sims headers from `.bzlook` files. `lut_bridge` uses those tables when the module is installed and keeps the Python fitter otherwise. `studio_core.build_mod` calls `bionz-cli pack` when `BIONZ_CLI` and `BIONZ_BZLOOK` are set. |
| `bionz-app` | Library discovery (twelve built-in presets, then disk presets, deduplicated by name), import, background recipe refit, save, the `.mod` export dialog, the viewer, scopes and stage taps described in "Using the app" above. Tests: the chart develops identically on the GPU and the CPU; look and stock differ; recipe refit keeps the stock and changes RefAdr; A6LT import composes onto stock; pack refuses synthetic stock and writes both headers for real stock; the twelve built-in presets load with no file on disk; `export_mods` writes both `<body>/mods/` trees and respects a folder already named `mods`; the Export dialog body renders in a bare `egui::Context` and its write path produces the files it reports; `--export-mod` (headless, same code as the dialog) writes both bodies and fails on an unknown look name. |

`lut_core` stays the numeric oracle. The Tk GUI in `a6400-lut-converter/lut_gui` is deprecated.

## Badges

`bionz_core::stage_catalog` is the badge list. Stages 1 and the container format are verified as a file format. Stages 2, 3, 5, 6, 8, 9, 11, 12, and 13 are models. Stages 4, 7, 10, 14, and 15 are assumptions, including the EC input scale and whether matrix gain applies to Movie. Stage 16 is preview only and is never packed. A green `cargo test` does not move an assumption to a verified badge. Claim C12 on the imaging-pipeline map records that limit and cites E0505 for the CC, gamma, matrix, and LUT arithmetic being modeled.

## Commands

```powershell
C:\Python314\python.exe a6400-lut-converter\tools\export_golden.py
cd isp-studio
cargo test --workspace
.\target\debug\bionz-cli.exe pack --target a6400 --allow-synthetic-stock -o target\host-only\film_sim_data.h ..\mods\film_sims\presets\classic-chrome.bzlook
.\target\debug\bionz-cli.exe smod ..\mods\film_sims\presets\classic-chrome.bzlook --target a6100 -o ..\artifacts\tests\ClassicChrome.mod
.\target\release\bionz-cli.exe render E:\DCIM\100MSDCF\_DSC1479.ARW --preset ..\mods\film_sims\presets\classic-chrome.bzlook -o target\renders\chrome.png
```

## Camera `.mod` export (2026-11-23)

`bionz-preset::smod_v2` is a pure-Rust `SMOD v2` packer. The Film Simulation lifecycle code is compiled once, during development, by `mods/film_sims/template/build_template_wsl.sh` into `isp-studio/crates/bionz-preset/assets/film_sim_template.blob` plus `film_sim_template.json` (code length, FNV-1a 32, `sim_life` offset, and every `struct sim_payload` field offset taken from an ARM `offsetof` probe). Every physical address and every table lives inside that one payload, so one template serves both bodies; the packer clones it, writes `TargetAddrs`, `mod_tag`, `/tmp_bt/mods/<slug>.log`, the stock/simulated EC, FC, A0, A1 and B2 tables and the 1215-byte `RefAdr[0]`, then emits the 68-byte header (`SMOD`, version 2, `FLAG_ACTION`, name, code length, FNV-1a 32, `bss` 0, `on/off = sim_life|1`, no hooks) and the code.

The payload must be read at run time: the template is `const`, so `swap()`/`write_refadr()` take `const volatile` pointers. Without the qualifier GCC constant-folds the zero-initialized payload and every physical address becomes 0 — the Unicorn test catches exactly that.

Verification (`tests/test_look_lab_smod_v2.py`) exports a `.mod` through `bionz-cli smod` for every shipped preset and both bodies, validates it with `src.a6400.mod_manager.smod.parse_smod`, decodes each `.bzlook`'s BZLK table independently to compare the payload byte for byte, and runs the lifecycle in Unicorn the way Mod Loader v2 does (code at an arbitrary base, `/dev/mem` `mmap2` serviced per physical page): `sim_life(1)` must swap EC/FC/A0/A1/B2, `RefAdr[0]` and the CC tuning word, a repeated `sim_life(1)` must be idempotent, `sim_life(0)` must restore stock, and a foreign table must be left untouched and reported as `x` in the mod log.

### Live Picker (`FilmSimulations.mod`)

`emit_live_smod_v2` packs up to fifteen looks into one mod. The skeleton in `assets/film_sim_live_template.mod` is built once by `mods/film_sims/live_picker/build_live_template_wsl.sh` from `film_sim_live_template.c`: it already carries the `event_push` hook at `0x0655a528` (`libObj`), 48 bytes of bss for `struct live_state`, `FLAG_ACTION | FLAG_RELOC` and the one-word RLC1 table that moves the trampoline's resume address. The packer rewrites only the name, the `struct live_payload` (addresses, `n_presets`/`n_looks`, names, `lut_idx`, the stock tables and every look's EC/FC/A0/A1/B2/RefAdr) and the code checksum, so the hook and relocation stay exactly as verified.

Two details that matter: the payload and the 5x7 banner font are separate named sections passed as `--data-symbol`, because the relocation scanner otherwise read glyph bytes as `libmintx` addresses (three false relocations); and the payload is read through `const volatile` pointers so the template's zero initializer is never folded. The emitter refuses more than fifteen looks, synthetic stock, base matrices, non-ASCII names, and looks whose stock tables differ from the first one's (the picker stores one stock set). Both exporters share `live_picker_skip_reason`, so a folder export skips the same looks in the app and in `bionz-cli live-mod`.

`tests/test_look_lab_live_smod_v2.py` installs the hook the way the loader does (trampoline at the firmware target plus the `base + entry` fixup), runs `live_life(1)`, and drives `event_push` with the real key ids: Right/Down and Left/Up step through Stock and every look (each step must write that look's tables into the emulated `/dev/mem` pages), slot 0 restores stock and bypasses the CC tuning word, ENTER locks and clears the banner on both OSD surfaces, the paired release is swallowed once, disarmed keys and foreign events reach the original `event_push`, MENU disarms after its closing event, and the 640x480 RGBA4444 banner is drawn and cleared at `0x0bfd0000`/`0x0cfdd000` (the bases the mod finds by scanning `/proc/self/maps` for the `0x23a80000`/`0x23b16000` offsets, E0298).


```powershell
# regenerate the ARM payloads after editing the templates
wsl.exe -d Ubuntu -- bash mods/film_sims/template/build_template_wsl.sh
wsl.exe -d Ubuntu -- bash mods/film_sims/live_picker/build_live_template_wsl.sh
# offline end-to-end checks (exports + strict parser + Unicorn)
.\venv\Scripts\python.exe -m unittest tests.test_look_lab_smod_v2 tests.test_look_lab_live_smod_v2 -v
# portable Windows release
.\scripts\build_look_lab_release.ps1
# portable proof: the shipped exe, from an empty folder
.\scripts\verify_look_lab_export.ps1
.\scripts\smoke_look_lab_portable.ps1
# the same exports from the command line, anywhere
.\dist\BIONZ-Look-Lab-portable\bionz-look-lab.exe --export-mod C:\Temp\mods-out --look "Gold 200" --bodies a6100
.\dist\BIONZ-Look-Lab-portable\bionz-look-lab.exe --export-mod C:\Temp\mods-out --kind live --bodies both
.\isp-studio\target\debug\bionz-cli.exe live-mod .\mods\film_sims\presets --target a6100 -o C:\Temp\FilmSimulations.mod
```

## Debayer, DCV and Arrivederci (2026-11-22)

A RAW is now decoded to its full CFA and developed on the GPU: Bayer DCV, then demosaic, then the optional 9×9 DCV. The developed image is kept per side: A uses the preset's spatial tables, B uses the stock tables. The **Debayer & detail** panel chooses:

- **Demosaic** (badge A; Sony's algorithm is not decoded):
  - **Directional** follows the ARRI ADA-5/7 description in [E0600](evidence/0600-arri-sdk-debayer-afa-texture-and-anr-pipeline.md):
    - 5×5 Hamilton-Adams gradients with a double-weighted centre line;
    - shadow-damped Laplacian estimates and an anti-zipper clamp;
    - opposite colour from the better diagonal and R/B at green sites from the better axis, both in colour difference;
    - a 3×3 median on R−G and B−G for interpolated channels.
  - No ARRI table or code is used.
  - Also **Bilinear** and **2×2 super-pixel** (half resolution).
- **DCV path:**
  - HD Bayer (`dcv_psf_table_movie_bayer.bin` on the CFA);
  - 4K (area resize to 3840 wide, then `movie_4k` 9×9);
  - HD 9×9 (`movie` at 1920).
- **Model assumptions:** fixed-point `q` (14), DC-free kernels (on, see E0678), nearest or blended zones, strength.

`bionz-cli demosaic-eval` scores the demosaics. It re-mosaics the 2×2 super-pixel image (full-colour ground truth) or a clean synthetic target and compares the result:

| Target | Bilinear | Directional |
| --- | --- | --- |
| Synthetic zone plate + colour edges | 27.27 dB, chroma error 0.0446 | 29.44 dB, 0.0228 |
| `_DSC1482.ARW` | 34.78 dB, 0.0331 | 35.17 dB, 0.0318 |
| `_DSC1479.ARW` | 35.30 dB, 0.0321 | 35.53 dB, 0.0312 |

Real files are noise-limited, so their gains are smaller. On the RX 9070 XT, 6 MP takes about 35 ms and the full 24 MP CFA with readback about 0.4 s.

The `dcv_psf` layout, decoded in [E0678](evidence/0678-dcv-psf-table-layout-and-arrivederci-v5-offset-defect.md):

- The data starts at byte 144.
- The 4K and HD tables hold 9×7 zones of 9×9 kernels.
- The Bayer table holds 3 groups: green is a 5×5 kernel plus a 4×4 quincunx kernel; red and blue are 5×5.

The parser refuses the still table, whose layout is not decoded.

**Import Arrivederci:** use **Import preset** with `Arrivederci.mod` or `arrivederci_data.h`. A `.mod` is matched by SHA-256 to its build header under `artifacts/tests/arrivederci-*`. The look carries every layer:

- EC record 31 (tone, record-31 knots);
- Color Mode entry B8 as the stage-6 base matrix;
- RefAdr;
- the `/usr/sabin` replacements.

Its stock arrays are the camera's own values, so B is real stock and not synthetic. The view switches to the 4K path.

The panel flags v5's tables: 47 asymmetric 4K kernels, and zone centre taps from 428 up to 7684. **Repair preset DCV tables** rebuilds them from stock at the correct offsets, with the gain recovered from the median zone, and adds it as a new preset. Film Sims packing refuses these looks. Build them with `mods/arri_isp/arrivederci`.

```powershell
.\target\release\bionz-cli.exe dcv <table.bin> [--gain 0.075 -o fixed.bin]
.\target\release\bionz-cli.exe demosaic-eval synthetic
.\target\release\bionz-cli.exe render <file.ARW> --demosaic directional --dcv <dcv_psf_table_movie_4k.bin> --max-side 0 --crop 2400,700,600,450 -o crop.png
```

Stock tables are loaded from `BIONZ_SABIN` or the firmware extraction, and checked against their SHA-256. No Sony or ARRI table data ships with the tool. Imported looks embed the mod's tables, so they stay local files.

## Chain corrections (2026-11-22)

These defects in the first app build are fixed in both `bionz-core` and `stages.wgsl`. CPU and GPU parity tests cover them.

- The EC lookup treated the 96 knots as uniform on 0–8191. It now uses the record-31 knot positions built from the subdivision exponents `[3,3,4,4,3,3,3,4,3]` (4, 8, 12, … 7680, 8191; catalog rows 9 and 13).
- FC is a 10-bit table, but it was fed the 12-bit EC output and clipped everything above about 25 %. It now scales in and out.
- Stage 10 applied the matrix a second time with a /256 divisor (4× gain). It now scales the stage-9 output by `g/256`.
- The viewport showed the post-detail RGB tap, so it skipped the residual LUT and the encoder. It now shows the decoded 8-bit encoder output.
- The GPU path requested the fallback (software) adapter and created a new device on every call. Uniform-buffer contents also leaked between calls on that adapter. It now uses one hardware device and a storage buffer for the parameters.
- The preview was a 48×48 sample grid. It now runs at 1600 px on the long side, with banded dispatch for full-resolution export.

Python `unittest` for this repository and `pytest` for `a6400-lut-converter/tests` stay on the existing Python path. The new module is not required for those tests.

## Still path & calibration (2026-11-22)

`DcvTable` now decodes and round-trip encodes `dcv_psf_table_still.bin` (`57,848` bytes: 6 axis entries × 3 channels × 20 zones (`5×4`) × `11×7` taps, 80 halfwords/zone, data from byte `248`), with GPU/CPU parity (`dcv_still` in `front.wgsl` / `spatial.rs`) and `bionz-app` UI support (`SpatialPath::Still`, "Still path" preset button, and spatial calibration metrics beside `ΔE`). See [E0679](evidence/0679-still-dcv-psf-layout-calibration-and-mod.md).

```powershell
.\target\release\bionz-cli.exe still-eval <arw...> --dcv <dcv_psf_table_still.bin> --avcam <av-cam.bin> --gamma-path srgb --ev 0.5 --dcv-q 13
.\target\release\bionz-cli.exe still-eval <arw...> --dcv <dcv_psf_table_still.bin> --dcv-q 13 --gamma-path fc --matrix identity --raw-matrix --ev 0.0 --fc-json fc_still_standard_ev0.json --iso-model
.\target\release\bionz-cli.exe still-design <stock_still.bin> --strength 1.0 -o <natural_still.bin>
```

### High-ISO noise reduction & crispening (2026-11-22)

`CAMNRSTL`/`KKNR`/`LFNR` and `PP_DetailCrispening` are catalogued but not decoded, so their effect is an opt-in model (badge A): a 3×3 edge-preserving luma bilateral before the still DCV, plus a soft threshold on the DCV high-pass delta. `RawFrame::iso` comes from the RAW EXIF and `FrontOptions::with_iso_preset` maps ISO to `(crispening_threshold, luma_nr_strength)`; the defaults are `0.0`, so earlier baselines do not change. In the app, **Model high-ISO NR & crispening** in *Debayer & detail* is on by default on the still path and shows the selected values on hover. In the CLI use `--iso-model`, or set `--crispening`/`--luma-nr` explicitly for sweeps. On the 24 calibration pairs `HF_ratio` drops 1.713 → 1.240 and `flat_noise` 5.50 → 3.66 (camera 3.80); high ISO (≥ 1600) goes 2.300 → 1.394 and 8.96 → 5.44 (camera 5.92). See [E0680](evidence/0680-still-high-iso-nr-crispening-and-paired-jpg.md).

Custom-EC still presets and Movie EC presets also receive the RAW `cam_to_srgb` as `base_matrix` when the preset does not embed one, so `.bzlook` looks now run on camera-calibrated RGB instead of raw sensor RGB. The seven shipped `mods/film_sims` presets embed no matrix (their `compiled/a6400.bin` is a 2,555-byte BZLK table).

### Still-JPEG look domain (2026-11-22)

Side A of the still path no longer runs the Movie EC chain. `GammaPath::StillJpeg` now gives the look the same physical domain as the stock side: `cam_to_srgb` once as `base_matrix`, the preset's **relative** look matrix (`M_sim · M_stock⁻¹`, row sums 1024) instead of `M_look · STOCK_A0`, and the preset's relative FC transfer composed onto `STILL_STANDARD_FC`; EC is bypassed and the range is full. On `_DSC1738` (ISO 640) Gold 200 moved from `ΔE2000 17.09 / 33.31` (legacy EC + double matrix) to `8.26 / 21.57`; the stock side is `7.41 / 20.23`. See [E0682](evidence/0682-still-jpeg-film-look-domain-and-recipe-recalibration.md).

```powershell
.\target\release\bionz-cli.exe preset-eval "E:\DCIM\100MSDCF\_DSC1738.ARW" --presets ../../../mods/film_sims/presets -o ../target/preset-eval
.\target\release\bionz-cli.exe render <arw> --preset <look.bzlook> --gamma-path still-jpeg --dcv <dcv_psf_table_still.bin> --max-side 0 -o look.png
```

`preset-eval` renders every `.bzlook` against the camera JPEG (same folder or `Pictures\Sony a6400\*\` by stem, or `--camera`), writes camera|emulated PNGs, and reports ΔE2000, neutral-axis drift, skin hue/chroma, shadow/highlight retention and LUT rail margins. `render --gamma-path` accepts `still-jpeg` (default), `movie-ec` and `still-fc`.

The shipped presets are now compiled at 33³ against the hash-verified `av-cam.bin` stock for both `a6400` and `a6100` (including Movie variants), with rendered thumbnails, and pack for Film Sims without the synthetic-stock refusal. The recipe curve is C¹ (two Hermite segments sharing the crossover slope) and the look matrix is row-stochastic, with `warmth` applied as a luminance-tapered midtone tint.

### Highlight shoulder and the Fujifilm set (2026-11-22)

Every recipe has an opt-in **ARRI-style shoulder** (`shoulder`, 0..1): a C¹ knee at 0.55 whose slope falls from 1 to `1 − shoulder` at white. Highlights compress smoothly instead of plateauing, so the top of the tone curve keeps gradation (≥30 distinct 10-bit codes in the top 10 %). ETERNA, Pro Negative, Astia and Vision3 use the softest shoulders (0.50–0.55); Velvia and Bleach Bypass use the hardest (0.25–0.30). The a6400 still pipeline cannot recover values the sensor clipped; the shoulder removes the display clip and the crushed top stop, matching the film-like roll-off ARRI describes.

The library now ships **12 looks** (Stock + 12 in `film_sim_live_data.h`): Classic Chrome, Classic Neg, Portra 400, Gold 200, Vision3 800T, ETERNA, **Eterna Bleach Bypass**, **Pro Negative**, **Reala Ace**, **Velvia**, **Astia**, **Acros**. The six new ones follow Fujifilm's published descriptions (Bleach Bypass: high contrast / low saturation; Pro Neg Std: soft gradation and skin; Reala Ace: faithful neutral color with hard tonality; Velvia: ultra-high chroma; Astia: soft contrast with vivid sky/green; Acros: monochrome with rich shadows). `preset-eval` on `_DSC1738` shows the expected skin-chroma ordering (Velvia 1.04 → … → Bleach 0.19 → Acros 0.03). See [E0683](evidence/0683-arri-shoulder-and-fujifilm-simulations.md).

### Photo & Movie modes (2026-11-22)

The controls panel starts with a **📷 Photo | 🎬 Movie** switch. Photo sets the Directional demosaic, Still 11×7 DCV (q=13), ISO NR model, Still-JPEG chain and full range, and selects `compiled/<target>.bin`. Movie sets the Movie DCV path (last used, default 4K), Movie EC record-31, legal 16–235 and selects `compiled/<target>_movie.bin`; presets compiled before the Movie variant fall back to the Photo table and the status line says so. Both variants share one `intent/recipe.toml`, so a slider edit recompiles Photo *and* Movie (see [E0686](evidence/0686-photo-movie-modes-and-dual-variant-presets.md)).

**ISP routing** in the advanced section overrides individual joins: base matrix (Auto / RAW `cam_to_srgb` / preset BZX2 / Identity), look matrix (Auto / relative `M_sim·M_stock⁻¹` / absolute A0 / A1 / B2), `RefAdr` strength 0–100 % and EC knots (as loaded / record 31 / uniform). Side B is never affected by these overrides; it stays the mode's stock. Per-stage **Bypass** checkboxes are applied after the mode defaults, so ticking EC or FC always takes effect. In Movie mode the `Gamma path` selector is locked (EC record-31) with a tooltip pointing to the advanced overrides.

Movie variants are fitted in their own domain: `decompose_movie` resamples the recipe cube through the record-31 EC before fitting, and `export_golden.py` uses the same `movie_domain_cube` transform. On the Gold 200 neutral ramp the Movie-domain error is 0.465 codes versus 2.010 when reusing the Photo fit (see [E0687](evidence/0687-photo-movie-fixes.md)).

`bionz-cli render --mode photo|movie` mirrors the switch, and `pack --mode movie` is refused: Film Sims headers write still/live-view addresses, while Movie uses EC record 31 / B8 and belongs to the Arrivederci mod path.
