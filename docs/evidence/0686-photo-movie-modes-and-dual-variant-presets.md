# E0686 — Photo/Movie modes, dual-variant `.bzlook` presets, and ISP routing

- **Date:** 2026-11-22
- **Status:** Offline verified (Rust workspace tests, Python Film Sim Studio tests, `bionz-cli` Photo/Movie render paths, `preset-eval` on a real ARW, pack guards). No camera access performed.
- **Related:** [E0683](0683-arri-shoulder-and-fujifilm-simulations.md), [E0682](0682-still-jpeg-film-look-domain-and-recipe-recalibration.md), [E0679](0679-still-dcv-psf-layout-calibration-and-mod.md), [E0505](0505-av-cam-cc-3dlut-and-ec-gamma-pipeline.md) for the av-cam/B8 provenance.

## 1. Two modes, one creative intent

`.bzlook` now holds up to two compiled variants per target:

```
look.toml
intent/recipe.toml
compiled/<target>.bin          Photo: Still-JPEG chain (BZLK v1)
compiled/<target>.json
compiled/<target>_movie.bin    Movie: Movie EC record-31 (BZLK v2, BZX2)
compiled/<target>_movie.json
```

`Bzlook::compiled_movie` is loaded/saved by `load_bzlook`/`store_bzlook`; files without `_movie.bin`
still load and Movie mode falls back to the Photo table (the app says so in the status line). The
format is documented in `isp-studio/docs/PRESET_FORMAT.md`.

`LabMode` (`Photo`, `Movie`) and `IspRouting` live in `bionz-core::integer`:

- `BaseMatrixSource`: `Auto` / `CameraToSrgb` / `PresetBaseMatrix` / `Identity`.
- `ColorMatrixMode`: `Auto` / `RelativeToStock` / `AbsoluteA0` / `AbsoluteA1` / `AbsoluteB2`.
- `lut_strength` (0..1) scales the RefAdr delta toward zero (`scale_lut`), leaving stock untouched.
- `ec_record`: `Some(31)` → record-31 knots, `Some(0)` → uniform knots (`uniform_ec_knots`), `None` → as loaded.

`bionz-preset` adds `apply_movie_look` (base from the preset BZX2 or `cam_to_srgb`, absolute A0,
record-31 EC, FC bypassed, legal range) and `apply_routing` (the overrides above). `apply_still_jpeg_look`
remains the Photo chain.

## 2. Movie variant compilation

`export_golden.py` now also fits every recipe against the Movie stock and writes
`compiled/<target>_movie.bin` and its report:

- Movie stock EC: `artifacts/analysis/arri-gamma-test-block-2026-11-16/record31-stock.bin`
  (hash `bc1d67e3…ceed58`, 105 u32, first 96 used), identity Movie matrices (the B8 base stays a
  camera property; the look matrix is applied at the colour stage).
- The same 33³ recipe cube is fitted again (`fit_cube_to_isp`) onto that stock, so the Movie table has
  its own EC composition and RefAdr; `ec_exponents = [3,3,4,4,3,3,3,4,3]`, `base_matrix = None`
  (render-time fallback to `cam_to_srgb`), digest and stock arrays provenance unchanged (a6400 capture;
  a6100 reuses it under the cross-body parity test).
- `looks::compile_recipe` (slider edits) recompiles Photo and Movie together from the base preset's
  two stock sets.

All 12 presets now carry `a6400`/`a6100` × Photo/Movie tables. Example (Gold 200): Photo 2,555 B,
Movie 2,572 B (BZLK v2 + BZX2 EC exponents).

## 3. UI and CLI

- **BIONZ Look Lab**: a `📷 Photo | 🎬 Movie` switch at the top of the controls panel. Photo sets
  StillJpeg, full range, Still DCV, Directional, q=13, ISO NR model; Movie sets MovieEc, legal range,
  the last Movie spatial path (default Uhd4k), q=14, ISO NR off, and picks the Movie variant (fallback
  to Photo with a status note). The advanced section has **ISP routing**: base matrix source, look
  matrix (relative/A0/A1/B2), RefAdr strength, EC knots. Side B labels “Stock (.jpg)” in Photo and
  “Movie stock” in Movie.
- **`bionz-cli render --mode photo|movie`** overrides `--gamma-path` (`still-jpeg` / `movie-ec`),
  selects the corresponding variant, and sets legal range in Movie (also `--legal-range` for manual
  paths). `--dcv-gain` remains.
- **`bionz-cli pack --mode photo|movie`**: Photo only. Movie is refused with the reason that Film
  Sims headers target still/live-view addresses while Movie uses EC record 31 / B8 (the Arrivederci
  path). The pack loop also refuses any table with a base matrix or `/usr/sabin` spatial data, and the
  live header excludes such looks.
- **`preset-eval`** stays Photo and prints a skip line for Movie/spatial presets.

## 4. Verification

```
cargo test --manifest-path isp-studio/Cargo.toml --workspace -q
python -m unittest tests.test_a6100_film_sim_studio -v
python a6400-lut-converter/tools/export_golden.py
bionz-cli pack --target a6400 --mode movie -o … gold-200.bzlook   → refused with the Movie message
bionz-cli pack --target a6400 --mode photo -o … gold-200.bzlook   → film_sim_data.h written
```

- `bionz-core` 23 tests (new `routing_helpers_scale_lut_and_build_uniform_knots`).
- `bionz-preset` 10 tests (new `movie_variant_round_trips_and_routing_overrides_the_chain`,
  `movie_chain_is_legal_range_and_differs_from_stock`): both variants round-trip, Movie output stays
  in Y 16–235 / C 16–240, routing switches base/A1/LUT/knots, stock fallback works.
- `bionz-app` 18 tests: every shipped preset has a Movie variant; `compile_recipe` updates both
  variants while keeping stock; pack writes a6400/a6100 single+live headers and refuses synthetic
  stock.
- Python Film Sim Studio: all pass (2 environment skips).
- `preset-eval` on `C:\Users\Administrator\Pictures\Sony a6400\_DSC0338.ARW` (the `E:` copy of
  `_DSC1738` was on the removed SD card) with all 12 presets: stock `dE 1.91/4.24`, looks
  `2.85–4.65`; skin-chroma ordering Velvia 1.24 → Gold 0.81 → Reala 0.70 → Astia 0.66 → Portra 0.60 →
  Pro Neg 0.59 → Chrome 0.46 → Vision3 0.43 → Eterna 0.30 → Bleach 0.22 → Acros 0.03; highlights 1.00.
- `bionz-cli render --mode movie --preset gold-200.bzlook --dcv dcv_psf_table_movie_4k.bin
  --plane-width 3840` on the same ARW produced a 1280×853 PNG through the GPU 9×9 DCV path. The Movie
  output is not compared to the camera JPEG (different legal/log domain); the legal-range unit test
  above is the contract.

## 5. Limits & next boundary

- Movie stock is the record-31 capture plus identity matrices; the camera B8 base matrix is not baked
  into the recipe variant (`base_matrix = None`, render-time `cam_to_srgb`). A later pass can set a
  preset B8 via BZX2 (the format and `apply_movie_look` already support it).
- Movie output is not compared against a camera JPEG because it is a legal-range log chain; visual A/B
  between Photo and Movie in Look Lab is the next manual step.
- No camera was touched; packing Photo headers for the mod remains the existing safety path.
