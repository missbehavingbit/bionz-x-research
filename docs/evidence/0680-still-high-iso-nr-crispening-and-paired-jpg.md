# E0680 — Still high-ISO NR / detail-crispening model, calibrated `cam_to_srgb` for Film presets, and paired-JPEG compare

- **Date:** 2026-11-22
- **Status:** Offline verified (`bionz-core`, `bionz-raw --no-default-features`, `bionz-gpu`, `bionz-app`, `bionz-cli still-eval`); no camera access performed.
- **Related:** [E0679](0679-still-dcv-psf-layout-calibration-and-mod.md), [E0678](0678-dcv-psf-table-layout-and-arrivederci-v5-offset-defect.md), [E0600 (Still RAW)](0600-av-cam-still-raw-develop-and-imx284-reg-writers.md), [E0601](0601-av-cam-sigitp-bayer-detail-and-pqd-stage-map.md).

## 1. Inputs & identities

- Calibration and held-out ARW+JPG pairs from `C:\Users\Administrator\Pictures\Sony a6400\`, recorded with per-file SHA-256 in `isp-studio/target/still-calib/pairs.json` (24 calibration + 10 held-out; same identities as E0679).
- Stock still table `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/sabin/dcv_psf_table_still.bin` — 57,848 bytes, SHA-256 `b3b82d875733c17f9c4193a1208a996421d16eeb97208a92939e9a0c21624a17`.
- Fitted still tone curve `isp-studio/target/still-calib/fc_still_standard_ev0.json` (96 entries, equals `bionz_core::STILL_STANDARD_FC`).
- Shipped Film Simulation presets `mods/film_sims/presets/*.bzlook` (7 files, ZIP containers; `compiled/a6400.bin` is a 2,555-byte BZLK table = base header + CRC, i.e. **no BZX2 extension and no embedded `base_matrix`**).
- **Tools:** Rust 1.99.1; `bionz-core`/`bionz-raw`/`bionz-gpu`/`bionz-app` unit tests; `bionz-cli` built `--release --no-default-features` (CPU path). Host-only; no USB, SD or camera calls.

## 2. What the camera does that the model did not (badge O/A)

`PP_DetailCrispening`, `CAMNRSTL`, `KKNR` and `LFNR` are catalogued in `av-cam.bin` (E0679 §3) but their parameters are not decoded. At ISO 4000–16000 the emulated still preview therefore amplified sensor noise in flat areas (`flat_noise` ≈ 1.5–2× the camera JPEG) and kept a `HF_ratio` of 1.8–2.3× the camera JPEG on clean aligned scenes.

This record adds an **opt-in** model (badge A — a monotonic stand-in, not a decode of the camera's own parameters):

1. **Edge-preserving luma NR, before the still DCV.** 3×3 bilateral on interleaved linear RGB, weight `exp(-Δluma² / (2σ²))` with `σ = 0.004` (≈1.5 8-bit codes), blended by `strength ∈ 0..1`. Edges survive because the weight collapses across a luma step.
2. **Detail crispening on the still DCV high-pass.** The additive delta `d = gain · conv(rgb, K)/2^q` is soft-thresholded: `|d| ≤ t/2 → 0`, `|d| ≥ 3t/2 → d`, smoothstep between. This is the shape `PP_DetailCrispening` implies (suppress small amplitudes, keep real detail), scaled to this model's linear units.
3. **ISO mapping.** `RawFrame::iso` is now read from the RAW EXIF (`ISOSpeedRatings`/`ISOSpeed`/`RecommendedExposureIndex`) via the same `rawler` decoder used for the frame. `FrontOptions::with_iso_preset(iso)` selects:

| ISO range | `crispening_threshold` | `luma_nr_strength` |
| --- | --- | --- |
| 0–100 | 0.0 | 0.0 |
| 101–400 | 0.0008 | 0.08 |
| 401–800 | 0.0015 | 0.15 |
| 801–1600 | 0.0040 | 0.35 |
| 1601–3200 | 0.0080 | 0.68 |
| 3201–6400 | 0.0140 | 0.95 |
| ≥ 6401 | 0.0200 | 1.00 |

`FrontOptions::default()` keeps both values at `0.0`, so every earlier regression baseline is byte-identical unless the model is enabled.

The GPU still kernel (`bionz-gpu/src/front.wgsl`) mirrors the crispening soft threshold; the luma bilateral runs once on the CPU before the DCV dispatch in both builds. A GPU/CPU parity test (`still_crispening_matches_cpu_reference`) checks the soft threshold to `2e-5`.

## 3. Calibrated `base_matrix` for `custom_ec` presets

In `GammaPath::StillJpeg` the `Stock` branch already applied `cam_to_srgb` as `base_matrix`; the `custom_ec` branch left the identity, so preset `EC` + `color_matrix` + `CC` ran on **raw sensor RGB**. The same held for `GammaPath::MovieEc`. Both now fill an identity `base_matrix` with `RawFrame::cam_to_srgb`, while a matrix embedded in a preset (`CompiledTables::base_matrix`) is preserved. Audit of the 7 shipped presets: none embeds one (`compiled/a6400.bin` is exactly 2,555 bytes; no `BZX2` chunk), so the fill is safe and the presets now receive camera-calibrated RGB.

## 4. Switches in BIONZ Look Lab

- **`Model high-ISO NR & crispening`** in *Debayer & detail* (on by default; still path). It reads the RAW ISO, shows the selected preset values on hover, and re-develops when toggled. When the RAW has no EXIF ISO the status line says so and nothing is applied.
- **`Use paired .JPG` / `Simulated Stock (.jpg)`** toolbar toggle. Opening an `.ARW` looks for a same-stem `.JPG`/`.jpg` next to it and remembers it **without** switching the comparison. The toggle swaps compare side B between the real camera JPEG and the emulated stock baseline. `✕ camera` still unloads the JPEG. Dropping or opening a JPEG with the same stem still loads it directly.

## 5. Verification commands and results

### 5.1 Tests

```powershell
cargo test --manifest-path isp-studio/Cargo.toml -p bionz-core -p bionz-preset -p bionz-fit -p bionz-compare -p bionz-target -q
cargo test --manifest-path isp-studio/Cargo.toml -p bionz-raw --no-default-features -q
cargo test --manifest-path isp-studio/Cargo.toml -p bionz-gpu -q
cargo test --manifest-path isp-studio/Cargo.toml -p bionz-app -q
```

- `bionz-core`: 22 passed (new `crispening_soft_threshold_*`, `luma_nr_keeps_edges_and_smooths_flat_noise`, `iso_preset_is_off_low_and_grows_with_iso`).
- `bionz-raw --no-default-features`: 2 passed.
- `bionz-gpu`: 11 passed (new `still_crispening_matches_cpu_reference`).
- `bionz-app`: 17 passed (new `camera_base_matrix_fills_identity_and_preserves_an_embedded_matrix` and `paired_jpeg_finds_a_same_stem_file_and_reports_none_otherwise`).
- `bionz-preset`/`bionz-fit`/`bionz-compare`/`bionz-target`: all passed.

### 5.2 Calibration baseline (24 pairs, model off)

```powershell
bionz-cli still-eval <24 calib ARWs> --dcv <dcv_psf_table_still.bin> --dcv-q 13 --dcv-entry 0 \
  --gamma-path fc --matrix identity --raw-matrix --ev 0.0 --fc-json <fc_still_standard_ev0.json> \
  --output isp-studio/target/still-calib/calib_nr_baseline.json
```

```
AGGREGATE (24 pairs): dE_mean=6.42 dE_p95=15.78 dE_matched=6.12/14.38 HF_ratio=1.713 emu_over=23.15% emu_under=23.85% emu_halo=2.96 cam_over=19.28% cam_under=17.01% cam_halo=2.81 emu_noise=5.50 cam_noise=3.80
HIGH ISO (10 pairs, ISO>=1600): HF_ratio=2.300 emu_noise=8.96 cam_noise=5.92 dE_matched=5.73
```

This reproduces E0679's unmatched `dE2000 = 6.42 / 15.78` exactly.

### 5.3 Calibration with `--iso-model` (24 pairs)

```powershell
bionz-cli still-eval <24 calib ARWs> ... --fc-json <fc_still_standard_ev0.json> --iso-model \
  --output isp-studio/target/still-calib/calib_nr_v3.json
```

```
AGGREGATE (24 pairs): dE_mean=6.29 dE_p95=15.59 dE_matched=6.00/14.27 HF_ratio=1.240 emu_over=21.22% emu_under=20.34% emu_halo=2.67 cam_over=19.28% cam_under=17.01% cam_halo=2.81 emu_noise=3.66 cam_noise=3.80
HIGH ISO (10 pairs, ISO>=1600): HF_ratio=1.394 emu_noise=5.44 cam_noise=5.92 dE_matched=5.46
```

| Metric (24 calib pairs) | Model off | Model on (`--iso-model`) | Camera |
| --- | --- | --- | --- |
| `HF_ratio` (all) | 1.713 | **1.240** | 1.0 |
| `flat_noise` (all) | 5.50 | **3.66** | 3.80 |
| `overshoot_pct` | 23.15% | 21.22% | 19.28% |
| `halo_width` | 2.96 | 2.67 | 2.81 |
| `dE2000` unmatched | 6.42 / 15.78 | 6.29 / 15.59 | — |
| `dE2000` matched | 6.12 / 14.38 | 6.00 / 14.27 | — |

High-ISO subset (ISO ≥ 1600, 10 pairs): `HF_ratio` 2.300 → **1.394**, `flat_noise` 8.96 → **5.44** (camera 5.92, i.e. 0.92×), `dE_matched` 5.73 → 5.46.

### 5.4 Held-out pairs (model on)

```powershell
bionz-cli still-eval <10 held-out ARWs> ... --iso-model --output isp-studio/target/still-calib/heldout_nr_v3.json
```

```
AGGREGATE (10 pairs): dE_mean=5.75 dE_p95=13.72 dE_matched=5.09/12.48 HF_ratio=1.281 emu_over=14.60% emu_under=14.41% emu_halo=2.31 cam_over=14.13% cam_under=13.14% cam_halo=2.49 emu_noise=2.90 cam_noise=2.80
```

E0679's held-out baseline was `dE2000 5.82 / 13.82`, matched `5.15 / 12.52`, `emu_halo 2.53` vs `2.49`; the model improves all three and does not over-smooth (`emu_over 14.60%` vs camera `14.13%`).

### 5.5 Sweep receipts

`isp-studio/target/still-calib/sweep_hi_*.json` holds the high-ISO (10-pair) sweeps used to fit the table: constant `0.010/0.70` → `HF_ratio 1.582`, `noise 5.86`; constant `0.014/0.85` → `1.415`, `5.49`; constant `0.020/1.00` → `1.280`, `5.21`. At the top of the ISO range the emulated `HF_ratio` plateaus around 1.4–1.8 (ISO 6400–16000): the remaining gap is sensor-noise shaping and the decoded-vs-camera detail stage, not the soft threshold.

## 6. Limits and next boundary

- The ISO table is a monotonic **model**, not the camera's `CAMNRSTL`/`KKNR`/`LFNR` parameters; the badge stays A. No camera writes and no firmware were changed.
- `flat_noise` now sits slightly below the camera on ISO 4000–12800 (0.7–0.9×) while ISO 16000 remains above (1.1×); a real per-ISO fit needs the decoded NR parameters or paired captures under a fixed chart.
- Physical A/B (2–3 `.ARW`+`.JPG` pairs with a preset loaded, PP ON for `.bzlook` color/gamma) remains a **manual step for user GO**; this record is offline only.
- Next: run `bionz-cli still-eval --iso-model` over the full 253 matched pairs once the extra pairs are recorded, and compare `.bzlook` presets with the filled `base_matrix` against their camera JPEGs.
