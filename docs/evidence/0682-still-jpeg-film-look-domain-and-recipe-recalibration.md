# E0682 — Still-JPEG domain for Film looks, C¹ filmic curve, white-point-preserving look matrix, and real-stock 33³ presets

- **Date:** 2026-11-22
- **Status:** Offline verified (Rust workspace tests, Python Film Sim Studio tests, `bionz-cli preset-eval`, `bionz-cli render` A/B, `bionz-cli pack`); no camera access performed.
- **Related:** [E0680](0680-still-high-iso-nr-crispening-and-paired-jpg.md), [E0679](0679-still-dcv-psf-layout-calibration-and-mod.md), [E0600 (Still RAW)](0600-av-cam-still-raw-develop-and-imx284-reg-writers.md).

## 1. Inputs & identities

- `E:\DCIM\100MSDCF\_DSC1738.ARW` — 25,082,624 B, SHA-256 `4c0658e7ff2e9ab01d3a02e8185d7ae83f711ecfcd6284574013a6a66bc70788` (ILCE-6400, 6000×4000, ISO 640).
- Camera JPEG of the same shot: `C:\Users\Administrator\Pictures\Sony a6400\18.11\_DSC1738.JPG` — SHA-256 `0bbb9613990419584df01dd2c20caf13ecb040232d22924c8512bd710abe3a59` (6000×4000).
- Stock still table `dcv_psf_table_still.bin` (57,848 B, SHA-256 `b3b82d87…624a17`, as E0679) and `artifacts/qemu-a6400/inputs-moddev-1/av-cam.bin` (16,803,048 B, SHA-256 `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`).
- Seven shipped presets `mods/film_sims/presets/*.bzlook` (Gold 200 after recompile: see §4).
- Tools: Rust 1.99.1, `bionz-cli` (`--release --no-default-features`, CPU path), Python 3.14 + numpy 2.4.3 for the LUT fitter. Host-only; no USB, SD or camera calls.

## 2. Root cause confirmed

`GammaPath::StillJpeg` treated side A (the look) as a **Movie EC** look: it enabled `EC` (record-31 logarithmic knot positions on 0..8191) and applied `table.a0` (which is `M_look · STOCK_A0`) *after* `cam_to_srgb`. Three errors compounded:

1. A uniform 96-knot curve fed through the record-31 knot positions maps 288/8191 (3.5 % input) to knot 48 (>50 % output): highlights and midtones blow out.
2. `cam_to_srgb` followed by `M_look · STOCK_A0` applies the stock green-boost matrix twice.
3. Side B used the calibrated still chain (`STILL_STANDARD_FC`, identity matrix, full range), so A and B were not comparable at all.

Measured on `_DSC1738` with the *same* preset tables and front end, Gold 200 only differing in the gamma path:

| Path (side A, Gold 200) | ΔE2000 mean | p95 |
| --- | --- | --- |
| Legacy `Movie EC` (`--gamma-path movie-ec`: EC + `M_look·STOCK_A0` on sRGB) | **17.09** | **33.31** |
| Still JPEG (`--gamma-path still-jpeg`: calibrated sRGB + relative matrix + composed FC) | **8.26** | **21.57** |

The legacy number reproduces the plan's reported 15.64/28.03 (the small difference is the recompiled preset and the ISO-640 NR model).

## 3. Fix

- `bionz_preset::apply_still_jpeg_look` / `still_jpeg_params_from_compiled`:
  - `base_matrix = cam_to_srgb` (unless the preset embeds one) — sensor RGB is calibrated once.
  - `color_matrix = M_sim · M_stock⁻¹` in Q10, diagonal-corrected to row sum **1024** (`relative_q10_matrix`). The shipped presets' `a0` is `M_look · STOCK_A0`, so the relative matrix is exactly `M_look`.
  - `fc` = the preset's relative transfer (`fc` over `stock_fc`) **composed onto `STILL_STANDARD_FC`** (`compose_relative_fc`), so both sides use the calibrated still tone domain.
  - `bypass[6] = true` (no EC), `bypass[7] = false`, `legal_range = false`.
- `bionz_preset::still_jpeg_stock_params` is the matching side B (identity matrix, `STILL_STANDARD_FC`).
- `bionz-app` uses these for side A in `GammaPath::StillJpeg`, and `has_custom_ec` now also treats FC/matrix/LUT differences as a custom look.
- `bionz-cli render --gamma-path still-jpeg | movie-ec | still-fc` selects the chain; `preset-eval` always evaluates the still path.

## 4. Tone curve and look matrix (Python + Rust, bit-matched)

- `_filmic_tone_vec` / `filmic` is now two cubic Hermite segments with a **shared slope at `crossover`** (C¹): `m0 = clamp(1 + 1.3·shadow_toe, .35, 2)`, `m1 = clamp(mid_contrast, .5, 1.7)`, `m2 = clamp(1 − .85·highlight_rolloff, .08, 1)`; the anchor height is the mean slope. Monotone by Fritsch–Carlson bounds; endpoints stay at `black_fade` / `white_ceiling`. The old power curve had derivative 0 at `x = crossover` for `mid_contrast > 1`.
- `_look_matrix` / `look_matrix` is `S` (saturation) times a row-stochastic hue rotation built from `red_amber`, `green_olive`, `blue_cyan`; **rows sum to 1**, so neutral gray maps to neutral gray. `warmth` no longer enters the matrix (the old row-normalization cancelled it). It is a luminance-tapered midtone tint (`mid = 4y(1−y)`, `ΔR = +0.5w`, `ΔG = −0.098w`, `ΔB = −0.5w`, scaled by `skin_protect`) that is zero at the black and white anchors and roughly luma-preserving.
- The seven `RECIPES_3D` entries use the calibrated values from the plan (Gold 200: `black_fade 0.006`, `mid_contrast 1.10`, `shadow_toe −0.06`, `rolloff 0.36`, `warmth 0.045`, `red_amber 0.14`, `skin_protect 0.88`, …; Portra 400, Classic Chrome/Neg, Vision3 800T, ETERNA, ACROS B&W likewise). Rust `Recipe`/`evaluate_recipe` is the same arithmetic.
- Python and Rust probes/fits agree: `recipe_probes_match_python`, `recipe_fits_match_python_rails`, `filmic_curve_is_c1_monotone_and_anchored`, `look_matrix_preserves_neutral_gray_and_rows_sum_to_one` pass.

## 5. Presets recompiled with real camera stock

`a6400-lut-converter/tools/export_golden.py` now:

- keeps deterministic synthetic stock for the golden vectors (`stages.json`, `recipes.json`, `recipe_fits.json`), and
- fits the seven recipes at **33³** against the hash-verified `av-cam.bin` stock and writes `flags = 0`, `avcam_sha256 = dd8f8d0c…`, the fitted EC/FC/A0/A1/B2 + 1215-byte RefAdr0, a rendered 96×64 `thumb.png`, and `compiled/a6400.json` (fit report). `look.toml` records the real-stock compilation.
- Gold 200 fit: mean ΔE76 **0.83**, p95 **1.91**, max **3.76**, `clamped_nodes = 0`, `at_rail_nodes = 0`.
- `mods/film_sims/live_picker/film_sim_live_data.h` regenerated with `python -m mods.film_sims.studio_core --target a6400 --multi-target --emit-live-header …`.
- `bionz-cli pack --target a6400` now succeeds for the shipped presets; `looks::tests::every_shipped_preset_packs_with_real_stock` writes both single and live headers for all seven, and `pack_refuses_synthetic_stock` still rejects a table with the synthetic flag.

The verified stock tables are also embedded as Rust constants for provenance: `a6400-lut-converter/tools/emit_stock_rs.py` reads `av-cam.bin`, checks `dd8f8d0c…`, and emits `isp-studio/crates/bionz-preset/src/stock_a6400.rs` (`EC_REC28_V1`, `FC_TBL0`, `STOCK_A0/A1/B2`, digest). `bionz-preset::tests::shipped_presets_carry_the_verified_avcam_stock` proves every shipped preset's four stock arrays and `avcam_sha256` equal those constants.

**Blocked:** the a6100 target is not compiled because `artifacts/a6100-inputs/av-cam.bin` is absent (the only hash-verified capture on this host is the a6400 one). `export_golden.py` reports `a6100: real av-cam.bin unavailable … skipped` and compiles a6400 only; fabricating a6100 stock would violate the synthetic-stock guard. Providing that capture enables both targets without code changes.

## 6. End-to-end evaluation on the real shot

`bionz-cli preset-eval "E:\DCIM\100MSDCF\_DSC1738.ARW" --presets mods/film_sims/presets -o isp-studio/target/preset-eval` (the ARW has no sibling JPG; `--camera` selects one, otherwise `%USERPROFILE%\Pictures\Sony a6400\*\` is searched by stem; the tool prints the file it used).

```
camera JPEG C:\Users\Administrator\Pictures\Sony a6400\18.11\_DSC1738.JPG
_DSC1738: 6000x4000 iso=640 shift=[-3, -4] stock dE=7.41/20.23 neutral=7.16 skin_c=0.62
  ACROS B&W      dE=11.49/23.41 neutral=7.69 skin_c=0.01 shadow=0.95 highlight=0.99 rail=63@6
  Classic Chrome dE= 9.17/21.60 neutral=7.51 skin_c=0.40 shadow=0.95 highlight=0.99 rail=33@0
  Classic Neg    dE= 9.10/21.41 neutral=7.72 skin_c=0.43 shadow=0.95 highlight=0.99 rail=26@0
  ETERNA         dE= 9.67/20.90 neutral=7.56 skin_c=0.28 shadow=0.94 highlight=0.99 rail=18@0
  Gold 200       dE= 7.81/20.78 neutral=7.77 skin_c=0.68 shadow=0.93 highlight=0.99 rail=20@0
  Portra 400     dE= 8.44/20.87 neutral=7.74 skin_c=0.54 shadow=0.92 highlight=0.99 rail=22@0
  Vision3 800T   dE= 9.62/21.76 neutral=7.79 skin_c=0.38 shadow=0.95 highlight=0.99 rail=23@0
```

- The stock side is `7.41 / 20.23`; the looks now sit within `+0.4 … +4.1` ΔE of stock, which is the intended look, not a broken chain (the legacy chain was `17.09 / 33.31` for Gold).
- Neutral-axis drift of the looks is `+0.35 … +0.63` over the stock `7.16`; tone retention stays `0.92–0.95` (shadows) and `0.99` (highlights); no LUT rail saturation (`rail_nodes = 0` except ACROS's 6 at the ±63 clip, which the fitter reports as `at_rail_nodes = 0` because the rail is applied to the RefAdr planes, not the fit).
- Side-by-side PNGs (`camera | emulated`) are written per preset; the JSON holds file SHA-256s, shift, dE, neutral/skin/tone metrics and rail margins.

## 7. Verification commands

```powershell
cargo test --manifest-path isp-studio/Cargo.toml --workspace -q
python -m unittest tests.test_a6100_film_sim_studio -v
python a6400-lut-converter/tools/export_golden.py
bionz-cli preset-eval "E:\DCIM\100MSDCF\_DSC1738.ARW" --presets mods/film_sims/presets -o isp-studio/target/preset-eval
bionz-cli render "E:\DCIM\100MSDCF\_DSC1738.ARW" --preset mods/film_sims/presets/gold-200.bzlook --gamma-path still-jpeg --demosaic directional --dcv <still.bin> --max-side 0 -o gold.png
bionz-cli compare <camera.JPG> gold.png -o gold.json
```

- Rust: 18 `bionz-app`, 22 `bionz-core`, 11 `bionz-gpu`, 7 `bionz-preset`, 7 `bionz-fit`, 2 `bionz-compare`, 2 `bionz-raw`, 1 `bionz-target` — all pass.
- Python: 7 tests, 5 pass, 2 skipped (no arm gcc; a6100 capture absent). `test_full_3d_lut_bridge_presets_and_cube_import` passes with numpy.
- `bionz-cli pack --target a6400` writes `film_sim_data.h` (9,344 B) from Gold 200 without the synthetic-stock refusal.

## 8. Limits & next boundary

- No camera was touched; the mod loader path and physical A/B remain manual/user-GO.
- `preset-eval` metrics are host heuristics (Lab chroma < 6 for "neutral", Lab hue 20–70° for skin). They are comparative, not camera measurements.
- The a6100 tables and the live header for a6100 remain blocked on `artifacts/a6100-inputs/av-cam.bin`.
- Next: open `_DSC1738.ARW` in BIONZ Look Lab and compare Gold 200 in Split/Side-by-side (manual plan step), then push the packed headers through the mod build on hardware when authorized.
