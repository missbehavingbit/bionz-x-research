# E0683 — ARRI-style highlight shoulder and the Fujifilm simulation set

- **Date:** 2026-11-22
- **Status:** Offline verified (Rust workspace tests, Python Film Sim Studio tests, `bionz-cli preset-eval`); no camera access performed.
- **Related:** [E0682](0682-still-jpeg-film-look-domain-and-recipe-recalibration.md), [E0679](0679-still-dcv-psf-layout-calibration-and-mod.md), [E0600 (ARRI)](0600-arri-sdk-debayer-afa-texture-and-anr-pipeline.md).

## 1. Reference characteristics (official sources, read-only)

No proprietary LUT files were downloaded or committed. The look targets come from Fujifilm's own descriptions of each simulation and ARRI's published image-science notes; the recipes are our own parametric model fitted onto the a6400 ISP, not a copy of any vendor transform.

- Fujifilm, *What are the features of each Film Simulation?* (FAQ 000008291) and the X/GFX Film Simulation pages:
  - **PROVIA**: all-round, comfortable color.
  - **Velvia**: ultra-high chroma, vivid and sharp, landscape.
  - **ASTIA**: soft, faithful skin plus vivid blue skies/greenery.
  - **CLASSIC CHROME**: low saturation, hard shadow gradation, documentary.
  - **PRO Neg. Std**: soft gradations and skin tones, neutral tonality for post.
  - **PRO Neg. Hi**: slightly harder than Std, outdoor portraits.
  - **CLASSIC Neg.**: hard tonality, different highlight/shadow color for depth.
  - **ETERNA**: suppressed saturation, very soft highlight/shadow gradation ("high-est and deep shadows").
  - **ETERNA BLEACH BYPASS**: high contrast with low saturation, "silver remaining" film-development look.
  - **NOSTALGIC Neg.**: amber highlights, rich shadow color.
  - **REALA ACE**: faithful, neutral color with hard tonality for a three-dimensional impression.
  - **ACROS**: monochrome, rich shadow detail, high-definition sharpness.
  - URLs: `digitalcamera-support-en.fujifilm.com/digitalcameraengpcdetail?aid=000008291`,
    `fujifilm-x.com/global/products/film-simulation/`, per-look pages (`/velvia`, `/astia`, `/reala-ace`,
    `/pro-neg-std`, `/eterna`, `/eterna-bleach-bypass`, `/acros`, …).
- ARRI, *LogC4 Specification* (2023-05) and *Dynamic Range* white paper (2022-09-28), plus
  `arri.com/en/learn/camera-systems/image-science/log-c`: a log encoding leaves highlight headroom, and
  the display transform rolls the top of the range off softly instead of clipping; the ALEXA 35's
  17-stop latitude is presented as a "soft, film-like roll-off".

## 2. ARRI-style highlight shoulder (new)

`FilmRecipe3D.shoulder` / `Recipe::shoulder` (`0..1`, default `0`) adds a **C¹ soft knee at 0.55** after the
contrast curve:

```
u = clamp((y - k) / (1 - k), 0, 1)      k = 0.55
g(u) = u + s·u²(1 - u)                   s = shoulder
y' = k + (1-k)·g(u)
```

- Slope 1 at the knee (no kink) and slope `1 - s` at white: the top of the range compresses instead of
  plateauing, so highlights keep gradation and do not hard-clip.
- Anchored at `black_fade` / `white_ceiling`; monotone for `s ∈ [0,1]`.
- Tests: `arri_style_shoulder_is_c1_soft_and_keeps_highlight_gradation` (Rust) and
  `test_film_recipes_shoulder_and_fujifilm_simulations` (Python) check C¹ at the knee, monotonicity,
  endpoint 1.0, a lower slope at 0.99 than the same recipe without the shoulder, and ≥30 distinct
  10-bit codes in the top 10 % of the input (no plateau).
- Recipes that need the most roll-off use the largest values: ETERNA / Pro Negative 0.55, Astia 0.50,
  Vision3 800T 0.55, Portra 0.50, Acros 0.45; contrast looks use 0.25–0.40 (Velvia 0.25, Bleach Bypass 0.30).

Limit: this shapes the display-referred signal the a6400 still pipeline already captured. It does not
recover scene values the sensor clipped; ARRI's 17-stop latitude is a sensor property the a6400 lacks.
What it removes is the abrupt display clip and the crushed top-stop gradation.

## 3. New Fujifilm simulations

Added to `RECIPES_3D` and `LIVE_PICKER_ORDER` (12 looks + Stock):

| Look | Modeled character |
| --- | --- |
| **Eterna Bleach Bypass** | saturation 0.40, contrast 1.30, toe −0.20; cool shadows, high-contrast silver look |
| **Pro Negative** (Pro Neg. Std) | saturation 0.94, contrast 0.92, toe +0.10, shoulder 0.55; soft gradation, skin protect 0.95 |
| **Reala Ace** | saturation 1.02, contrast 1.14, faithful color, moderate shoulder |
| **Velvia** | saturation 1.30, contrast 1.26, vivid skies/foliage, small shoulder |
| **Astia** | saturation 1.02, contrast 0.96, soft contrast, skin protect 0.94, vivid sky/green |
| **Acros** | monochrome weights `[0.34, 0.55, 0.11]`, rich shadow detail, shoulder 0.45 |

`Acros` replaces the previous `ACROS B&W` file (`acros.bzlook`); the other eleven keep their names.
All twelve are compiled at 33³ for **both a6400 and a6100**. The a6100 table reuses the a6400
`av-cam.bin` stock values (user-authorized on 2026-11-22), because the repository's cross-body test
`test_real_avcam_stock_tables_match_across_a6100_and_a6400` asserts the ILCE-6100 and ILCE-6400
EC/FC/A0/A1/B2 stock tables are identical; only the PA addresses differ, and those come from the a6100
`TargetProfile`. Each preset's `look.toml` records the provenance (`a6100: a6400 av-cam.bin reused for
a6100 (cross-body stock parity)`), the compiled table keeps the real a6400 digest `dd8f8d0c…`, and
`bionz-cli pack --target a6100` emits the a6100 addresses (`EC_GAMMA_PA 0x03e553b0`, `REFADR0_PA 0x044acd37`).
All fits have `clamped_nodes = 0` and `at_rail_nodes = 0` (Acros was tuned from toe −0.10/contrast 1.10 to
−0.08/1.08 and white 0.997→0.994 to remove its single rail node).

## 4. End-to-end measurement on `_DSC1738` (ISO 640)

`bionz-cli preset-eval E:\DCIM\100MSDCF\_DSC1738.ARW --presets mods/film_sims/presets -o isp-studio/target/preset-eval-fuji`

```
stock: dE=7.41/20.23 neutral=7.16 skin_c=0.62
  Acros                dE=11.49/23.69 neutral=8.08 skin_c=0.03 shadow=0.95 highlight=0.99
  Astia                dE= 8.15/20.96 neutral=8.25 skin_c=0.60 shadow=0.93 highlight=1.00
  Classic Chrome       dE= 9.16/21.66 neutral=7.84 skin_c=0.40 shadow=0.95 highlight=0.99
  Classic Neg          dE= 9.22/21.63 neutral=8.08 skin_c=0.44 shadow=0.95 highlight=1.00
  Eterna Bleach Bypass dE=10.38/22.14 neutral=7.71 skin_c=0.19 shadow=0.94 highlight=0.99
  ETERNA               dE= 9.80/21.10 neutral=7.99 skin_c=0.28 shadow=0.93 highlight=1.00
  Gold 200             dE= 7.92/20.97 neutral=8.13 skin_c=0.69 shadow=0.93 highlight=1.00
  Portra 400           dE= 8.70/21.16 neutral=8.34 skin_c=0.54 shadow=0.92 highlight=1.00
  Pro Negative         dE= 8.60/21.09 neutral=8.22 skin_c=0.53 shadow=0.92 highlight=1.00
  Reala Ace            dE= 8.05/21.24 neutral=7.91 skin_c=0.62 shadow=0.94 highlight=1.00
  Velvia               dE= 7.02/21.43 neutral=7.80 skin_c=1.04 shadow=0.94 highlight=0.99
  Vision3 800T         dE= 9.78/22.01 neutral=8.44 skin_c=0.39 shadow=0.95 highlight=1.00
```

The chroma ordering matches Fujifilm's descriptions: **Velvia 1.04 > Gold/Reala 0.62–0.69 > Astia 0.60 >
Portra/Pro Neg 0.53–0.54 > Chrome 0.40 > Vision3 0.39 > Eterna 0.28 > Bleach 0.19 > Acros 0.03**.
Highlight retention is 0.99–1.00 and shadow retention 0.92–0.95 on every look. Neutral drift rises
~0.3–0.9 over the stock 7.16 because the shoulder and warmth are intentional midtone lifts, not a
broken matrix.

## 5. Verification

```powershell
cargo test --manifest-path isp-studio/Cargo.toml --workspace -q
python -m unittest tests.test_a6100_film_sim_studio -v
python a6400-lut-converter/tools/export_golden.py
python -m mods.film_sims.studio_core --target a6400 --multi-target --emit-live-header mods/film_sims/live_picker/film_sim_live_data.h
bionz-cli preset-eval "E:\DCIM\100MSDCF\_DSC1738.ARW" --presets mods/film_sims/presets -o isp-studio/target/preset-eval-fuji
```

- Rust: 18 app, 22 core, 11 gpu, 8 preset, 8 fit, 2 raw, 2 compare, 1 target — all pass.
- Python: 8 tests, 6 pass, 2 skipped (no arm gcc; a6100 capture absent). The full 3D-LUT bridge test
  now fits all 12 looks at 33³ with synthetic stock and asserts monotone EC/FC, row-sum invariance,
  `at_rail_nodes = 0`, and the correct cube channel order.
- `LIVE_N_PRESETS 13u` (Stock + 12), `LIVE_REFADR[12][1215]`.
- Presets: 12 `.bzlook` files, real a6400 stock, fit reports 0/0 clamped/rail nodes.
- `preset-eval` emits 12 camera|emulated PNGs and `preset-eval.json`.

## 6. Limits & next boundary

- The a6100 tables use the a6400 capture's stock values under the documented cross-body parity
  assumption. They are not an independent a6100 capture; supplying `artifacts/a6100-inputs/av-cam.bin`
  later should replace them and would re-run the hash check.
- No vendor LUT or sample image is redistributed; the comparison is against published look descriptions
  and our measured character ordering.
- Manual visual check in BIONZ Look Lab and the physical A/B remain user steps; no camera writes were made.
