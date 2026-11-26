# E0687 — Photo/Movie fixes: bypass precedence, Movie-domain fit, locked Gamma path

- **Date:** 2026-11-22
- **Status:** Offline verified (Rust workspace tests, Python Film Sim Studio tests, regenerated presets); no camera access performed.
- **Related:** [E0686](0686-photo-movie-modes-and-dual-variant-presets.md), [E0683](0683-arri-shoulder-and-fujifilm-simulations.md).

## 1. User stage bypasses now always win

In `bionz-app::LabApp::effective` the `self.bypass` OR-loop ran **before** the mode/GammaPath branch, so
`apply_movie_look` / `apply_still_jpeg_look` / the gamma defaults overwrote `bypass[6]` and `bypass[7]`
and the per-stage checkboxes silently did nothing for EC/FC.

The loop is now `apply_user_bypass(&mut params, &self.bypass)` and runs **after** the mode branch and
before `apply_routing`. Test `user_stage_bypass_wins_after_mode_defaults` builds a Movie chain
(`bypass[6] = false, bypass[7] = true`) and a Photo-style chain, applies the user's EC/FC bypass and
asserts it survives (`bypass[6] = true` / `bypass[7] = true` respectively).

## 2. Movie variant has its own domain fit

`compile_recipe` fitted the cube once with `decompose(&cube, Profile::a6400())` and reused the Photo
`gamma`/`matrix`/`delta` in `compiled_movie`. The chain looks the Movie EC up at record-31 knot
positions while the fitter models a uniform 96-point lookup, so the tone was bent.

Added:

- `bionz-fit::decompose_movie(cube, profile, stock_ec)`: resamples the target cube through the inverse
  of the stock Movie EC (record-31 knots → knot input positions) before the existing uniform fit. The
  fit is then composed onto `stock_ec` with `apply_fitted_gamma`, exactly as the chain applies it.
- `mods/film_sims/lut_bridge.py::movie_domain_cube` performs the same transform for the Python
  compiler, and `export_golden.py` now fits every Movie variant on that domain-transformed cube
  (`movie_stock` = record-31 EC + identity matrices).
- `looks::compile_recipe` computes the Movie preset separately and passes it to `compose_look`;
  `report_json` gets `"mode": "photo"`/`"mode": "movie"` entries.

Measured on the Gold 200 neutral ramp in the record-31 EC domain (mean absolute 10-bit error):

| Fit used for the Movie table | Neutral error |
| --- | --- |
| Photo `decompose` gamma composed onto Movie EC | 2.010 codes |
| `decompose_movie` | **0.465 codes** |

Test: `movie_domain_fit_reproduces_the_neutral_ramp_better_than_a_photo_fit`, plus
`test_movie_domain_cube_matches_record31_knots` in the Python suite (knot positions match
`[4, 8, 12, …, 7680, 8191]`, linear stock maps value→knot input, non-linear stock warps the cube).
`recipe_compile_changes_the_tables_and_keeps_stock` now asserts the recompiled Movie `ec` and
`refadr` differ from the Photo fit and stay a separate fit of the same recipe.

## 3. Gamma path is locked in Movie mode

The `Gamma path` ComboBox was editable in Movie mode while `effective()` ignored it. It is now wrapped
in `add_enabled_ui(self.mode == LabMode::Photo, …)` with the disabled tooltip
“Movie mode locks EC record-31. Use ISP stages (advanced) or ISP routing below for overrides.”
Photo mode keeps the full selector.

## 4. Verification

```
cargo test --manifest-path isp-studio/Cargo.toml --workspace -q
python -m unittest tests.test_a6100_film_sim_studio -v
python a6400-lut-converter/tools/export_golden.py
```

- Rust: 19 `bionz-app`, 23 `bionz-core`, 9 `bionz-fit`, 11 `bionz-gpu`, 10 `bionz-preset`, 2 `bionz-raw`,
  2 `bionz-compare`, 1 `bionz-target` — all pass (new bypass and Movie-domain tests included).
- Python: the Movie-domain knot test and the full 3D LUT bridge test pass; 2 environment skips remain.
- All 12 presets regenerated: Photo tables unchanged in behaviour, Movie tables now use the
  Movie-domain fit (a6400 and a6100; provenance and stock arrays unchanged).

## 5. Limits

- The Movie-domain transform models the chain's record-31 lookup; the camera B8 base matrix is still not
  baked into the recipe (`base_matrix = None`, render-time `cam_to_srgb`).
- Visual Photo/Movie A/B in Look Lab remains a user step; no camera writes were made.
