use std::path::{Path, PathBuf};
use std::time::Instant;

use bionz_core::{integer_pipeline, IntegerParams, RgbI};
use bionz_preset::{emit_film_sim_data, emit_live_header, load_bzlook};
use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "bionz-cli")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Write film_sim_data.h or film_sim_live_data.h from .bzlook files.
    Pack {
        #[arg(long, default_value = "a6400")]
        target: String,
        /// high-level mode; only Photo tables can be packed for Film Sims today.
        #[arg(long, default_value = "photo")]
        mode: String,
        #[arg(short, long)]
        output: PathBuf,
        /// Allow tables whose stock is synthetic. The mod restores the stock arrays when it turns off,
        /// so use this only for host tests, never for a header that will run on a camera.
        #[arg(long)]
        allow_synthetic_stock: bool,
        presets: Vec<PathBuf>,
    },
    /// Write an SMOD v2 `.mod` for ILCE-6400 / ILCE-6100 straight from a .bzlook preset, with no
    /// compiler involved: the ARM template is embedded in `bionz-preset`.
    Smod {
        /// .bzlook preset container.
        preset: PathBuf,
        /// Camera body whose physical addresses the payload gets.
        #[arg(long, default_value = "a6400")]
        target: String,
        /// Output `.mod` path (default: `<LookName>.mod` next to the preset).
        #[arg(short, long)]
        output: Option<PathBuf>,
    },
    /// Write a Live Picker `FilmSimulations.mod` from a folder of `.bzlook` presets: every exportable
    /// look goes into one mod that switches them live in Live View.
    LiveMod {
        /// Folder holding the `.bzlook` presets (sorted by file name).
        presets: PathBuf,
        #[arg(long, default_value = "a6400")]
        target: String,
        #[arg(short, long)]
        output: PathBuf,
    },
    /// Time the integer pipeline. `--raw` is recorded and not required for the synthetic ramp.
    Bench {
        #[arg(long)]
        raw: Option<PathBuf>,
        #[arg(long, default_value_t = 256)]
        width: u32,
    },
    /// Compare two 8-bit images and write a receipt. The receipt does not promote a badge.
    Compare {
        reference: PathBuf,
        emulated: PathBuf,
        #[arg(short, long)]
        output: PathBuf,
    },
    /// Run the CPU and GPU integer chains and print the mismatch count.
    Parity,
    /// Print a dcv_psf table's layout and health, and optionally write a copy rescaled by --gain at
    /// the correct offsets (zero-DC preserving).
    Dcv {
        input: PathBuf,
        #[arg(long)]
        gain: Option<f32>,
        #[arg(short, long)]
        output: Option<PathBuf>,
    },
    /// Score the demosaics: the 2x2 super-pixel image of a RAW is full-colour ground truth; it is
    /// re-mosaicked (RGGB), demosaicked, and compared (PSNR, chroma error) on the interior.
    DemosaicEval {
        input: PathBuf,
    },
    /// Evaluate still-path emulation on ARW+JPG pairs (registration, dE2000, MTF/overshoot, HF energy,
    /// halo width, flat-area noise, false colour).
    StillEval {
        /// ARW files (matching .JPG with same stem in the same directory is loaded automatically).
        inputs: Vec<PathBuf>,
        /// Optional dcv_psf_table_still.bin (or movie table).
        #[arg(long)]
        dcv: Option<PathBuf>,
        #[arg(long, default_value_t = 1.0)]
        dcv_gain: f32,
        #[arg(long, default_value_t = 14)]
        dcv_q: u32,
        /// Still DCV entry index (0..5; corresponds to aperture/axis entry).
        #[arg(long, default_value_t = 0)]
        dcv_entry: usize,
        /// Keep raw DC in the DCV kernel (default: force DC-free).
        #[arg(long)]
        dc_raw: bool,
        /// Optional captured ILCE-6400 2.00 av-cam.bin for real stock FC/EC tables.
        #[arg(long)]
        avcam: Option<PathBuf>,
        /// Gamma path: fc, ec, ec-fc, fc-ec, srgb.
        #[arg(long, default_value = "fc")]
        gamma_path: String,
        /// Detail-crispening soft threshold on the still DCV high-pass delta (linear units).
        /// 0 disables it (default), matching the earlier baselines.
        #[arg(long, default_value_t = 0.0)]
        crispening: f32,
        /// Edge-preserving 3x3 luma noise reduction strength before the still DCV (0..1).
        /// 0 disables it (default), matching the earlier baselines.
        #[arg(long, default_value_t = 0.0)]
        luma_nr: f32,
        /// Map each RAW's EXIF ISO to the modeled NR/crispening preset (overrides the two values).
        #[arg(long)]
        iso_model: bool,
        /// Stock matrix: a0, a1, b2, identity.
        #[arg(long, default_value = "a0")]
        matrix: String,
        /// Apply the RAW file's sensor-RGB -> linear-sRGB matrix as base_matrix.
        #[arg(long)]
        raw_matrix: bool,
        /// Optional JSON file containing a 96-entry 10-bit FC LUT (0..1023).
        #[arg(long)]
        fc_json: Option<PathBuf>,
        #[arg(long, default_value_t = 0.0)]
        ev: f32,
        /// Use legal 16-235 range instead of full 0-255.
        #[arg(long)]
        legal_range: bool,
        /// Optional JSON report path.
        #[arg(short, long)]
        output: Option<PathBuf>,
        /// Optional directory to save 400x300 center/detail PNG crops (cam vs emu).
        #[arg(long)]
        crop_dir: Option<PathBuf>,
        /// Suffix for emulated crop filenames (default: emu).
        #[arg(long, default_value = "emu")]
        crop_tag: String,
    },
    /// Design an improved natural low-halo dcv_psf_table_still.bin from the stock table.
    StillDesign {
        /// Stock dcv_psf_table_still.bin.
        stock: PathBuf,
        /// Strength multiplier (default 1.0).
        #[arg(long, default_value_t = 1.0)]
        strength: f32,
        /// Output dcv_psf_table_still.bin path.
        #[arg(short, long)]
        output: PathBuf,
    },
    /// Fit a 96-point 10-bit FC tone curve (0..1023) from matched ARW+JPG pairs.
    StillFitTone {
        /// ARW files (matching .JPG with same stem is loaded automatically).
        inputs: Vec<PathBuf>,
        /// Exposure offset (EV) before the tone curve.
        #[arg(long, default_value_t = 0.5)]
        ev: f32,
        /// Output JSON path for the 96-element FC LUT.
        #[arg(short, long)]
        output: PathBuf,
    },
    /// Develop a RAW or display image through a preset's compiled tables and write a PNG.
    Render {        input: PathBuf,
        #[arg(long)]
        preset: Option<PathBuf>,
        #[arg(long, default_value = "a6400")]
        target: String,
        /// Use the preset's stock tables (the camera without the look).
        #[arg(long)]
        stock: bool,
        #[arg(long, default_value_t = 0.0)]
        ev: f32,
        /// Long side of the output; 0 keeps the developed size.
        #[arg(long, default_value_t = 1600)]
        max_side: usize,
        /// RAW demosaic: directional, bilinear or superpixel.
        #[arg(long, default_value = "superpixel")]
        demosaic: String,
        /// Gamma path: still-jpeg (default), movie-ec, still-fc.
        #[arg(long, default_value = "still-jpeg")]
        gamma_path: String,
        /// High-level mode: photo (Still JPEG + Photo variant) or movie (Movie EC + legal range + Movie variant).
        #[arg(long)]
        mode: Option<String>,
        /// Use legal 16-235 range (movie mode sets this automatically).
        #[arg(long)]
        legal_range: bool,
        /// Apply the RAW EXIF ISO high-ISO NR/crispening model in the front end.
        #[arg(long)]
        iso_model: bool,
        /// A dcv_psf table: movie_bayer layout runs on the CFA, movie_4k / movie layout after demosaic.
        #[arg(long)]
        dcv: Vec<PathBuf>,
        /// Width the 9x9 DCV runs at (the image is area-resized first). 3840 models 4K movie.
        #[arg(long, default_value_t = 3840)]
        plane_width: usize,
        /// Multiplier on the DCV high-pass (1.0 = as stored).
        #[arg(long, default_value_t = 1.0)]
        dcv_gain: f32,
        /// DCV fixed-point scale (coefficients / 2^q).
        #[arg(long, default_value_t = 14)]
        dcv_q: u32,
        /// Keep each kernel's DC as stored (default: DC-free, see bionz_core::spatial).
        #[arg(long)]
        dc_raw: bool,
        /// Crop x,y,w,h of the developed image (applied before max_side).
        #[arg(long)]
        crop: Option<String>,
        #[arg(short, long)]
        output: PathBuf,
    },
    /// Evaluate every `.bzlook` preset on real ARW inputs against the camera JPEG and write
    /// side-by-side PNGs plus a JSON report (neutral drift, skin stability, tone retention, rails).
    PresetEval {
        /// ARW files. A same-stem `.JPG` next to each ARW is used when present.
        inputs: Vec<PathBuf>,
        /// Folder with .bzlook presets.
        #[arg(long, default_value = "mods/film_sims/presets")]
        presets: PathBuf,
        #[arg(long, default_value = "a6400")]
        target: String,
        /// Camera JPEG for a single input when it is not next to the ARW.
        #[arg(long)]
        camera: Option<PathBuf>,
        /// Still dcv_psf table. Defaults to the stock still table beside this checkout when present.
        #[arg(long)]
        dcv: Option<PathBuf>,
        #[arg(long, default_value_t = 13)]
        dcv_q: u32,
        #[arg(long, default_value_t = 1.0)]
        dcv_gain: f32,
        /// Apply the RAW EXIF ISO high-ISO NR/crispening model.
        #[arg(long, default_value_t = true)]
        iso_model: bool,
        #[arg(long, default_value_t = 0.0)]
        ev: f32,
        /// Long side of the saved comparison PNGs.
        #[arg(long, default_value_t = 1200)]
        png_side: usize,
        /// Output folder for the JSON report and PNGs.
        #[arg(short, long)]
        output: PathBuf,
    },
}

fn main() -> anyhow::Result<()> {
    match Cli::parse().command {
        Command::Pack { target, mode, output, allow_synthetic_stock, presets } => {
            if mode != "photo" {
                anyhow::bail!(
                    "pack --mode {mode}: only Photo tables can be packed for Film Sims. Movie variants write EC record 31 and the B8 base matrix; build those with mods/arri_isp/arrivederci."
                );
            }
            let mut looks = Vec::new();
            for path in &presets {
                let look = load_bzlook(path)?;
                if !look.spatial.is_empty() {
                    anyhow::bail!("{} carries /usr/sabin tables; build it with mods/arri_isp instead of Film Sims", path.display());
                }
                let table = look.compiled.iter().find(|(k, _)| k == &target).ok_or_else(|| anyhow::anyhow!("no {target} table in {}", path.display()))?.1.clone();
                if table.base_matrix.is_some() {
                    anyhow::bail!("{} carries a Color Mode base matrix; Film Sims headers target the A0/A1/B2 matrices. Use the mod that owns it.", path.display());
                }
                if table.has_synthetic_stock() && !allow_synthetic_stock {
                    anyhow::bail!(
                        "{}: the {target} stock tables are synthetic; the mod would restore them on the camera (imaging hazard H01). \
                         Use studio_core.py with the real av-cam.bin, or pass --allow-synthetic-stock for host-only tests.",
                        path.display()
                    );
                }
                looks.push((look.meta.name, table));
            }
            let text = if looks.len() == 1 {
                emit_film_sim_data(&looks[0].0, &target, &looks[0].1)?
            } else {
                emit_live_header(&target, &looks)?
            };
            if let Some(parent) = output.parent() {
                std::fs::create_dir_all(parent)?;
            }
            std::fs::write(&output, text)?;
            println!("wrote {}", output.display());
        }
        Command::Smod { preset, target, output } => {
            let look = load_bzlook(&preset)?;
            let table = look
                .compiled
                .iter()
                .find(|(k, _)| k == &target)
                .map(|(_, t)| t)
                .ok_or_else(|| {
                    anyhow::anyhow!(
                        "no {target} table in {} (it has: {})",
                        preset.display(),
                        look.compiled.iter().map(|(k, _)| k.as_str()).collect::<Vec<_>>().join(", ")
                    )
                })?;
            let blob = bionz_preset::emit_smod_v2(&look.meta.name, &target, table)?;
            let out = output.unwrap_or_else(|| preset.with_file_name(bionz_preset::mod_filename(&look.meta.name)));
            if let Some(parent) = out.parent() {
                if !parent.as_os_str().is_empty() {
                    std::fs::create_dir_all(parent)?;
                }
            }
            std::fs::write(&out, &blob)?;
            let info = serde_json::json!({
                "preset": preset.display().to_string(),
                "name": look.meta.name,
                "target": target,
                "output": out.display().to_string(),
                "bytes": blob.len(),
                "sha256": bionz_compare::file_sha256(&out).unwrap_or_default(),
            });
            println!("{}", serde_json::to_string_pretty(&info)?);
        }
        Command::LiveMod { presets, target, output } => {
            anyhow::ensure!(presets.is_dir(), "{} is not a folder", presets.display());
            let mut paths: Vec<PathBuf> = std::fs::read_dir(&presets)?
                .flatten()
                .map(|e| e.path())
                .filter(|p| p.extension().is_some_and(|x| x.eq_ignore_ascii_case("bzlook")))
                .collect();
            paths.sort();
            anyhow::ensure!(!paths.is_empty(), "no .bzlook presets in {}", presets.display());
            let mut looks = Vec::new();
            let mut skipped = Vec::new();
            for path in &paths {
                let look = load_bzlook(path)?;
                let Some((_, table)) = look.compiled.iter().find(|(k, _)| k == &target) else {
                    skipped.push(format!("{} (no {target} table)", look.meta.name));
                    continue;
                };
                if !look.spatial.is_empty() {
                    skipped.push(format!("{} (spatial tables)", look.meta.name));
                    continue;
                }
                if let Some(reason) = bionz_preset::smod_v2::live_picker_skip_reason(&look.meta.name, table) {
                    skipped.push(format!("{} ({reason})", look.meta.name));
                    continue;
                }
                if looks.len() >= bionz_preset::smod_v2::LIVE_MAX_LOOKS {
                    skipped.push(format!("{} (the picker carries at most {} looks)", look.meta.name, bionz_preset::smod_v2::LIVE_MAX_LOOKS));
                    continue;
                }
                looks.push((look.meta.name.clone(), table.clone()));
            }
            anyhow::ensure!(!looks.is_empty(), "no look in {} can go into the {target} Live Picker: {}", presets.display(), skipped.join(", "));
            let blob = bionz_preset::emit_live_smod_v2(&target, &looks)?;
            if let Some(parent) = output.parent() {
                if !parent.as_os_str().is_empty() {
                    std::fs::create_dir_all(parent)?;
                }
            }
            std::fs::write(&output, &blob)?;
            let info = serde_json::json!({
                "kind": "live",
                "target": target,
                "output": output.display().to_string(),
                "bytes": blob.len(),
                "looks": looks.iter().map(|(name, _)| name.clone()).collect::<Vec<_>>(),
                "skipped": skipped,
                "sha256": bionz_compare::file_sha256(&output).unwrap_or_default(),
            });
            println!("{}", serde_json::to_string_pretty(&info)?);
        }
        Command::Bench { raw, width } => {
            if let Some(path) = &raw {
                let (meta, samples) = bionz_raw::decode_camera_raw(path)?;
                let side = 32.min(meta.width).min(meta.height) & !1;
                let mut cropped = meta.clone();
                cropped.width = side.max(2);
                cropped.height = side.max(2);
                let mut window = Vec::with_capacity(cropped.width * cropped.height);
                for y in 0..cropped.height {
                    for x in 0..cropped.width {
                        window.push(samples[y * meta.width + x]);
                    }
                }
                let started = Instant::now();
                let taps = bionz_raw::preview_from_cfa(&window, &cropped, bionz_raw::Demosaic::Bilinear, &IntegerParams::default())?;
                println!(
                    "raw {}x{} window {}x{} taps {} in {:.2} ms {}",
                    meta.width, meta.height, cropped.width, cropped.height, taps.len(),
                    started.elapsed().as_secs_f64() * 1000.0, path.display()
                );
            }
            let height = width;
            let pixels: Vec<RgbI> = (0..width * height)
                .map(|i| RgbI { r: (i % 4096) as i32, g: ((i / 3) % 4096) as i32, b: ((i / 7) % 4096) as i32 })
                .collect();
            let params = IntegerParams::default();
            let start = Instant::now();
            let _ = integer_pipeline(&pixels, width as i32, &params);
            println!("integer_pipeline {}x{} in {:.2} ms", width, height, start.elapsed().as_secs_f64() * 1000.0);
            #[cfg(feature = "gpu")]
            {
                let (taps, times) = bionz_gpu::run_gpu_times(&pixels, width as i32, &params)?;
                println!(
                    "gpu host-wait develop {:.2} ms finish {:.2} ms on {}x{} taps {}. The plan targets are 40 ms cold and 4 ms for a later-stage edit.",
                    times.develop_ms, times.finish_ms, width, height, taps.len()
                );
            }
        }
        Command::Compare { reference, emulated, output } => {
            let (w0, h0, a) = bionz_raw::load_display(&reference)?;
            let (w1, h1, b) = bionz_raw::load_display(&emulated)?;
            if (w0, h0) != (w1, h1) {
                anyhow::bail!("image sizes differ");
            }
            let (mean, p95) = bionz_compare::compare_rgb(&a, &b);
            let luma = |rgb: &[u8]| -> Vec<u8> {
                rgb.chunks(3).map(|p| ((p[0] as u32 * 2126 + p[1] as u32 * 7152 + p[2] as u32 * 722) / 10000) as u8).collect()
            };
            let luma_a = luma(&a);
            let luma_b = luma(&b);
            let shift = if bionz_compare::phase_shift(&luma_a, &luma_b, w0 as usize, h0 as usize).is_some() {
                bionz_compare::align_translation(&luma_a, &luma_b, w0 as usize, h0 as usize)
            } else {
                bionz_compare::find_shift(&luma_a, &luma_b, w0 as usize, h0 as usize, 2)
            };
            let receipt = bionz_compare::receipt_for_shift(&reference, &emulated, shift, mean, p95, true);
            std::fs::write(&output, serde_json::to_string_pretty(&receipt)?)?;
            println!("mean {mean:.4} p95 {p95:.4}");
        }
        Command::Render { input, preset, target, stock, ev, max_side, demosaic, gamma_path, mode, legal_range, iso_model, dcv, plane_width, dcv_q, dcv_gain, dc_raw, crop, output } => {
            use bionz_core::spatial::{DcvLayout, DcvTable, DemosaicMode, FrontOptions};
            let mode = mode.as_deref();
            if let Some(m) = mode {
                anyhow::ensure!(m == "photo" || m == "movie", "--mode must be photo or movie");
            }
            let gamma_path = match mode {
                Some("photo") => "still-jpeg".to_string(),
                Some("movie") => "movie-ec".to_string(),
                _ => gamma_path,
            };
            let legal = legal_range || mode == Some("movie");
            let started = Instant::now();
            let mut cam_to_srgb = None;
            let mut image = if bionz_raw::is_camera_raw(&input) {
                let frame = bionz_raw::decode_raw_frame(&input)?;
                cam_to_srgb = frame.cam_to_srgb;
                let mut opt = FrontOptions {
                    demosaic: if demosaic == "bilinear" { DemosaicMode::Bilinear } else { DemosaicMode::Directional },
                    dcv_q,
                    dcv_gain,
                    ..Default::default()
                };
                if iso_model {
                    opt = opt.with_iso_preset(frame.iso);
                }
                let mut bayer = None;
                let mut plane = None;
                let mut still_coef = None;
                for path in &dcv {
                    let table = DcvTable::parse(&std::fs::read(path)?).map_err(anyhow::Error::msg)?;
                    let (asym, dc) = table.health();
                    println!("{}: {} {} {}, asymmetric kernels {asym}, DC-offset kernels {dc}", path.display(), table.kind(), table.sensor, table.lens);
                    match table.layout {
                        DcvLayout::Bayer { .. } => bayer = Some(table.coefficients(dcv_q, !dc_raw)),
                        DcvLayout::Plane9 { .. } => plane = Some(table.coefficients(dcv_q, !dc_raw)),
                        DcvLayout::Still { .. } => still_coef = Some(table.coefficients(dcv_q, !dc_raw)),
                    }
                }
                let tables = bionz_raw::SpatialTables { bayer: bayer.as_deref(), plane9: plane.as_deref().map(|c| (c, plane_width)), still: still_coef.as_deref() };
                bionz_raw::develop_frame(&frame, &opt, demosaic == "superpixel", &tables)?
            } else {
                bionz_raw::load_linear(&input, 0)?
            };
            if let Some(spec) = &crop {
                let v: Vec<usize> = spec.split(',').map(|s| s.trim().parse()).collect::<Result<_, _>>()?;
                anyhow::ensure!(v.len() == 4, "--crop needs x,y,w,h");
                let (x0, y0, cw, ch) = (v[0].min(image.width - 1), v[1].min(image.height - 1), v[2], v[3]);
                let (cw, ch) = (cw.min(image.width - x0), ch.min(image.height - y0));
                let mut rgb = Vec::with_capacity(cw * ch * 3);
                for y in y0..y0 + ch {
                    rgb.extend_from_slice(&image.rgb[(y * image.width + x0) * 3..(y * image.width + x0 + cw) * 3]);
                }
                image = bionz_raw::LinearImage { width: cw, height: ch, rgb, ..image };
            }
            let image = bionz_raw::downscale(image, max_side);
            let decoded_ms = started.elapsed().as_secs_f64() * 1000.0;
            let table = match &preset {
                Some(path) => {
                    let look = load_bzlook(path)?;
                    let found = if gamma_path == "movie-ec" {
                        look.movie_table_or_photo(&target).map(|(t, _)| t)
                    } else {
                        look.table_for(&target, false)
                    };
                    Some(found.cloned().ok_or_else(|| anyhow::anyhow!("no {target} table"))?)
                }
                None => None,
            };
            let params = match (gamma_path.as_str(), stock) {
                ("still-jpeg", true) => bionz_preset::still_jpeg_stock_params(cam_to_srgb),
                ("still-jpeg", false) => match &table {
                    Some(t) => bionz_preset::still_jpeg_params_from_compiled(t, cam_to_srgb),
                    None => bionz_preset::still_jpeg_stock_params(cam_to_srgb),
                },
                ("movie-ec", false) => match &table {
                    Some(t) => {
                        let mut p = bionz_preset::integer_params_from_compiled(t);
                        bionz_preset::apply_movie_look(&mut p, t, cam_to_srgb, legal);
                        p
                    }
                    None => {
                        let mut p = IntegerParams::default();
                        if let Some(m) = cam_to_srgb {
                            p.base_matrix = m;
                        }
                        p.bypass[6] = false;
                        p.bypass[7] = true;
                        p.legal_range = legal;
                        p
                    }
                },
                ("movie-ec", true) => {
                    let mut p = match &table {
                        Some(t) => bionz_preset::stock_params_from_compiled(t),
                        None => IntegerParams::default(),
                    };
                    p.bypass[6] = false;
                    p.bypass[7] = true;
                    p.legal_range = legal;
                    p
                }
                ("still-fc", _) => {
                    let mut p = match (&table, stock) {
                        (Some(t), true) => bionz_preset::stock_params_from_compiled(t),
                        (Some(t), false) => bionz_preset::integer_params_from_compiled(t),
                        (None, _) => IntegerParams::default(),
                    };
                    let identity = [1024, 0, 0, 0, 1024, 0, 0, 0, 1024];
                    if p.base_matrix == identity {
                        if let Some(m) = cam_to_srgb {
                            p.base_matrix = m;
                        }
                    }
                    p.bypass[6] = true;
                    p.legal_range = legal;
                    p
                }
                (other, _) => anyhow::bail!("unknown --gamma-path {other}"),
            };
            let pixels = bionz_raw::quantize(&image, ev, params.gamma_max);
            let t = Instant::now();
            #[cfg(feature = "gpu")]
            let (taps, backend) = (bionz_gpu::run_gpu_banded(&pixels, image.width as i32, &params)?, bionz_gpu::adapter_info()?);
            #[cfg(not(feature = "gpu"))]
            let (taps, backend) = (integer_pipeline(&pixels, image.width as i32, &params), "CPU".to_string());
            let gpu_ms = t.elapsed().as_secs_f64() * 1000.0;
            let rgb: Vec<u8> = taps
                .iter()
                .flat_map(|tap| bionz_core::encoded_to_rgb8(tap.encoded_y, tap.encoded_cb, tap.encoded_cr, params.legal_range))
                .collect();
            image::save_buffer(&output, &rgb, image.width as u32, image.height as u32, image::ColorType::Rgb8)?;
            println!(
                "{}x{} decode {:.0} ms, chain {:.1} ms on {}; {}",
                image.width, image.height, decoded_ms, gpu_ms, backend, image.note
            );
        }
        Command::PresetEval { inputs, presets, target, camera, dcv, dcv_q, dcv_gain, iso_model, ev, png_side, output } => {
            use bionz_core::spatial::{DcvLayout, DcvTable, DemosaicMode, FrontOptions};
            anyhow::ensure!(!inputs.is_empty(), "pass at least one ARW");
            anyhow::ensure!(presets.is_dir(), "preset folder {} does not exist", presets.display());
            std::fs::create_dir_all(&output)?;
            // Stock still table: --dcv or the checkout's firmware extraction.
            let dcv_path = dcv.or_else(find_stock_still_table);
            let still_coef = match &dcv_path {
                Some(path) => {
                    let table = DcvTable::parse(&std::fs::read(path)?).map_err(anyhow::Error::msg)?;
                    let (asym, dc) = table.health();
                    println!("still table {}: {} ({asym} asymmetric, {dc} DC-offset kernels)", path.display(), table.kind());
                    match table.layout {
                        DcvLayout::Still { .. } => Some(table.coefficients(dcv_q, true)),
                        _ => anyhow::bail!("{} is not a still dcv_psf table", path.display()),
                    }
                }
                None => {
                    println!("no still dcv_psf table found; evaluating without the DCV stage");
                    None
                }
            };
            let mut preset_paths: Vec<PathBuf> = std::fs::read_dir(&presets)?
                .flatten()
                .map(|e| e.path())
                .filter(|p| p.extension().is_some_and(|x| x.eq_ignore_ascii_case("bzlook")))
                .collect();
            preset_paths.sort();
            anyhow::ensure!(!preset_paths.is_empty(), "no .bzlook files in {}", presets.display());
            let mut report = Vec::new();
            for arw in &inputs {
                let jpg = match &camera {
                    Some(path) if inputs.len() == 1 => path.clone(),
                    _ => find_camera_jpeg(arw).ok_or_else(|| {
                        anyhow::anyhow!("no camera JPEG for {} (pass --camera <file.JPG>)", arw.display())
                    })?,
                };
                println!("camera JPEG {}", jpg.display());
                let frame = bionz_raw::decode_raw_frame(arw)?;
                let mut opt = FrontOptions { demosaic: DemosaicMode::Directional, dcv_q, dcv_gain, ..Default::default() };
                if iso_model {
                    opt = opt.with_iso_preset(frame.iso);
                }
                let tables = bionz_raw::SpatialTables { bayer: None, plane9: None, still: still_coef.as_deref() };
                let linear = bionz_raw::develop_frame(&frame, &opt, false, &tables)?;
                let (w, h) = (linear.width, linear.height);
                let camera_rgb = {
                    let (cw, ch, raw) = bionz_raw::load_display(&jpg)?;
                    if (cw as usize, ch as usize) == (w, h) {
                        raw
                    } else {
                        bionz_raw::resize_rgb8(&raw, cw as usize, ch as usize, w, h)
                    }
                };
                // Register the camera JPEG to the developed frame once per shot.
                let patch_luma = |rgb: &[u8]| -> Vec<u8> {
                    let (pw, ph) = (512usize.min(w), 512usize.min(h));
                    let (x0, y0) = ((w - pw) / 2, (h - ph) / 2);
                    let mut out = Vec::with_capacity(pw * ph);
                    for py in y0..y0 + ph {
                        for px in x0..x0 + pw {
                            let i = (py * w + px) * 3;
                            out.push(((rgb[i] as u32 * 2126 + rgb[i + 1] as u32 * 7152 + rgb[i + 2] as u32 * 722) / 10000) as u8);
                        }
                    }
                    out
                };
                let stock_pixels = bionz_raw::quantize(&linear, ev, IntegerParams::default().gamma_max);
                let stock_params = bionz_preset::still_jpeg_stock_params(frame.cam_to_srgb);
                let stock_rgb = render_taps(&stock_pixels, w, &stock_params);
                let shift = bionz_compare::phase_shift(&patch_luma(&camera_rgb), &patch_luma(&stock_rgb), 512.min(w), 512.min(h)).unwrap_or([0, 0]);
                // Compare at quarter resolution for speed; metrics use the same pixels on both sides.
                let (sw, sh) = ((w / 4).max(1), (h / 4).max(1));
                let cam_s = bionz_raw::resize_rgb8(&camera_rgb, w, h, sw, sh);
                let base = |rgb: &[u8]| bionz_raw::resize_rgb8(rgb, w, h, sw, sh);
                let stock_s = base(&stock_rgb);
                let (stock_de, stock_p95) = bionz_compare::compare_rgb(&cam_s, &stock_s);
                let stock_metrics = bionz_compare::preset_metrics(&cam_s, &stock_s);
                let stem = arw.file_stem().unwrap_or_default().to_string_lossy().to_string();
                println!(
                    "{stem}: {w}x{h} iso={} shift={shift:?} stock dE={stock_de:.2}/{stock_p95:.2} neutral={:.2} skin_c={:.2}",
                    frame.iso.map(|v| v.to_string()).unwrap_or_else(|| "?".into()),
                    stock_metrics.neutral_de, stock_metrics.skin_chroma_ratio
                );
                for path in &preset_paths {
                    let look = load_bzlook(path)?;
                    let Some((_, table)) = look.compiled.iter().find(|(k, _)| k == &target) else {
                        println!("  {}: no {target} table; skipped", look.meta.name);
                        continue;
                    };
                    if table.base_matrix.is_some() || !look.spatial.is_empty() {
                        println!("  {}: Movie/spatial look; skipped in Photo preset-eval", look.meta.name);
                        continue;
                    }
                    let params = bionz_preset::still_jpeg_params_from_compiled(table, frame.cam_to_srgb);
                    let emu = render_taps(&bionz_raw::quantize(&linear, ev, params.gamma_max), w, &params);
                    let emu_s = base(&emu);
                    let (de_mean, de_p95) = bionz_compare::compare_rgb(&cam_s, &emu_s);
                    let metrics = bionz_compare::preset_metrics(&cam_s, &emu_s);
                    let rail = table.refadr.iter().map(|v| (*v as i8).unsigned_abs()).max().unwrap_or(0);
                    let rail_nodes = table.refadr.iter().filter(|v| (**v as i8).unsigned_abs() >= 63).count();
                    let png = output.join(format!("{stem}-{}.png", slug(&look.meta.name)));
                    save_pair_png(&png, &camera_rgb, &emu, w, h, png_side)?;
                    println!(
                        "  {:14} dE={de_mean:5.2}/{de_p95:5.2} neutral={:.2} (n={}) skin_hue={:+.1}deg skin_c={:.2} shadow={:.2} highlight={:.2} rail={rail}@{}",
                        look.meta.name, metrics.neutral_de, metrics.neutral_pixels, metrics.skin_hue_delta_deg,
                        metrics.skin_chroma_ratio, metrics.shadow_retention, metrics.highlight_retention, rail_nodes
                    );
                    report.push(serde_json::json!({
                        "input": arw.display().to_string(),
                        "arw_sha256": bionz_compare::file_sha256(arw).unwrap_or_default(),
                        "jpg": jpg.display().to_string(),
                        "jpg_sha256": bionz_compare::file_sha256(&jpg).unwrap_or_default(),
                        "preset": look.meta.name,
                        "preset_target": target,
                        "iso": frame.iso,
                        "shift": shift,
                        "width": w,
                        "height": h,
                        "de2000_mean": de_mean,
                        "de2000_p95": de_p95,
                        "stock_de2000_mean": stock_de,
                        "stock_de2000_p95": stock_p95,
                        "stock_neutral_de": stock_metrics.neutral_de,
                        "stock_skin_chroma_ratio": stock_metrics.skin_chroma_ratio,
                        "neutral_de": metrics.neutral_de,
                        "neutral_chroma_delta": metrics.neutral_chroma_delta,
                        "neutral_pixels": metrics.neutral_pixels,
                        "skin_hue_delta_deg": metrics.skin_hue_delta_deg,
                        "skin_chroma_ratio": metrics.skin_chroma_ratio,
                        "skin_pixels": metrics.skin_pixels,
                        "shadow_retention": metrics.shadow_retention,
                        "highlight_retention": metrics.highlight_retention,
                        "lut_rail_abs": rail,
                        "lut_rail_nodes": rail_nodes,
                        "comparison_png": png.display().to_string(),
                    }));
                }
            }
            let path = output.join("preset-eval.json");
            std::fs::write(&path, serde_json::to_string_pretty(&report)?)?;
            println!("wrote {}", path.display());
        }
        Command::Dcv { input, gain, output } => {
            let bytes = std::fs::read(&input)?;
            let table = bionz_core::spatial::DcvTable::parse(&bytes).map_err(anyhow::Error::msg)?;
            let (asym, dc) = table.health();
            let (lo, hi) = table.zone_centre_range();
            println!("{}: {} sensor {} lens {}; asymmetric kernels {asym}, DC-offset kernels {dc}, zone centre taps {lo}..{hi}", input.display(), table.kind(), table.sensor, table.lens);
            if let (Some(gain), Some(out)) = (gain, output) {
                let fixed = bionz_core::spatial::rescale_table(&bytes, gain).map_err(anyhow::Error::msg)?;
                std::fs::write(&out, &fixed)?;
                let (a, d) = bionz_core::spatial::DcvTable::parse(&fixed).map_err(anyhow::Error::msg)?.health();
                println!("wrote {} (gain {gain}); asymmetric {a}, DC-offset {d}", out.display());
            }
        }
        Command::DemosaicEval { input } => {
            use bionz_core::spatial::{demosaic, DemosaicMode, FrontOptions};
            let truth = if input.as_os_str() == "synthetic" {
                // Noise-free target: a grey zone plate on the left, slanted colour edges on the right.
                let (w, h) = (1024usize, 768usize);
                let mut rgb = vec![0.0f32; w * h * 3];
                for y in 0..h {
                    for x in 0..w {
                        let px = if x < w / 2 {
                            let (dx, dy) = (x as f32 - w as f32 / 4.0, y as f32 - h as f32 / 2.0);
                            let v = 0.5 + 0.4 * ((dx * dx + dy * dy) * 0.0016).cos();
                            [v, v, v]
                        } else {
                            let t = (x as f32 - 0.27 * y as f32) / 37.0;
                            let band = (t.floor() as i32).rem_euclid(4);
                            let s = (y as f32 * 0.21 - x as f32 * 0.05).sin() * 0.5 + 0.5;
                            match band {
                                0 => [0.8, 0.15, 0.1],
                                1 => [0.1, 0.6 * s + 0.2, 0.15],
                                2 => [0.15, 0.2, 0.85],
                                _ => [0.9, 0.85, 0.2 * s],
                            }
                        };
                        rgb[(y * w + x) * 3..(y * w + x) * 3 + 3].copy_from_slice(&px);
                    }
                }
                bionz_raw::LinearImage { width: w, height: h, rgb, kind: bionz_raw::SourceKind::DisplayImage, note: String::new() }
            } else {
                let frame = bionz_raw::decode_raw_frame(&input)?;
                bionz_raw::develop_frame(&frame, &FrontOptions::default(), true, &bionz_raw::SpatialTables { bayer: None, plane9: None, still: None })?
            };
            let (w, h) = (truth.width & !1, truth.height & !1);
            // Gamma 1/2.2 so the score weighs shadows like a viewer does.
            let g = |v: f32| v.clamp(0.0, 1.0).powf(1.0 / 2.2);
            let pattern = [0u8, 1, 1, 2];
            let mut cfa = vec![0.0f32; w * h];
            for y in 0..h {
                for x in 0..w {
                    let c = bionz_core::spatial::cfa_color(pattern, x, y);
                    cfa[y * w + x] = truth.rgb[(y * truth.width + x) * 3 + c];
                }
            }
            for (name, mode) in [("bilinear", DemosaicMode::Bilinear), ("directional", DemosaicMode::Directional)] {
                #[cfg(feature = "gpu")]
                let opt = FrontOptions { demosaic: mode, ..Default::default() };
                let t = Instant::now();
                #[cfg(feature = "gpu")]
                let out = if std::env::var("BIONZ_EVAL_CPU").is_ok() { demosaic(&cfa, w, h, pattern, mode) } else { bionz_gpu::run_front_bayer(&cfa, w, h, pattern, None, &opt).unwrap_or_else(|_| demosaic(&cfa, w, h, pattern, mode)) };
                #[cfg(not(feature = "gpu"))]
                let out = demosaic(&cfa, w, h, pattern, mode);
                let ms = t.elapsed().as_secs_f64() * 1000.0;
                let (mut se, mut ce, mut n) = (0f64, 0f64, 0f64);
                for y in 8..h - 8 {
                    for x in 8..w - 8 {
                        let a: Vec<f32> = (0..3).map(|c| g(truth.rgb[(y * truth.width + x) * 3 + c])).collect();
                        let b: Vec<f32> = (0..3).map(|c| g(out[(y * w + x) * 3 + c])).collect();
                        for c in 0..3 {
                            se += ((a[c] - b[c]) as f64).powi(2);
                        }
                        // Chroma error: difference of (R-G, B-G) between truth and result.
                        ce += (((a[0] - a[1]) - (b[0] - b[1])).abs() + ((a[2] - a[1]) - (b[2] - b[1])).abs()) as f64;
                        n += 1.0;
                    }
                }
                let psnr = 10.0 * (1.0 / (se / (3.0 * n))).log10();
                println!("{name:12} PSNR {psnr:.2} dB, mean chroma error {:.5}, {w}x{h} in {ms:.0} ms", ce / n);
            }
        }
        Command::Parity => {
            #[cfg(feature = "gpu")]
            {
                let params = IntegerParams::default();
                let pixels = [RgbI { r: 0, g: 0, b: 0 }, RgbI { r: 1000, g: 2000, b: 3000 }];
                let cpu = integer_pipeline(&pixels, 2, &params);
                let gpu = bionz_gpu::run_gpu(&pixels, 2, &params)?;
                let mismatches = cpu.iter().zip(&gpu).filter(|(a, b)| a != b).count();
                println!("mismatches {mismatches}");
                if mismatches != 0 {
                    anyhow::bail!("CPU and GPU differ");
                }
            }
            #[cfg(not(feature = "gpu"))]
            {
                println!("built without gpu feature; CPU-only mode");
            }
        }
        Command::StillEval { inputs, dcv, dcv_gain, dcv_q, dcv_entry, dc_raw, avcam, gamma_path, crispening, luma_nr, iso_model, matrix, raw_matrix, fc_json, ev, legal_range, output, crop_dir, crop_tag } => {
            use bionz_core::spatial::{DcvLayout, DcvTable, DemosaicMode, FrontOptions};
            let base_opt = FrontOptions {
                demosaic: DemosaicMode::Directional,
                dcv_q,
                dcv_gain,
                crispening_threshold: crispening,
                luma_nr_strength: luma_nr,
                ..Default::default()
            };
            let mut bayer_coef = None;
            let mut still_coef = None;
            if let Some(dcv_path) = &dcv {
                let table = DcvTable::parse(&std::fs::read(dcv_path)?).map_err(anyhow::Error::msg)?;
                match table.layout {
                    DcvLayout::Bayer { .. } => bayer_coef = Some(table.coefficients(dcv_q, !dc_raw)),
                    DcvLayout::Still { .. } => still_coef = Some(table.coefficients_at(dcv_entry, dcv_q, !dc_raw)),
                    DcvLayout::Plane9 { .. } => {}
                }
            }
            let mut params = IntegerParams::default();
            params.legal_range = legal_range;
            let use_srgb = gamma_path == "srgb";
            match gamma_path.as_str() {
                "fc" | "srgb" => { params.bypass[6] = true; params.bypass[7] = false; }
                "ec" => { params.bypass[6] = false; params.bypass[7] = true; }
                "ec-fc" => { params.bypass[6] = false; params.bypass[7] = false; params.fc_before_ec = false; }
                "fc-ec" => { params.bypass[6] = false; params.bypass[7] = false; params.fc_before_ec = true; }
                other => anyhow::bail!("unknown --gamma-path {other}"),
            }
            let a0 = [1241, -205, -12, 62, 1116, -154, 30, -251, 1245];
            let a1 = [1161, -205, 68, 43, 1134, -153, 16, -236, 1243];
            let b2 = [1024, -88, 88, 51, 1024, -51, 48, -289, 1264];
            let id = [1024, 0, 0, 0, 1024, 0, 0, 0, 1024];
            params.color_matrix = match matrix.as_str() {
                "a0" => a0,
                "a1" => a1,
                "b2" => b2,
                "identity" => id,
                other => anyhow::bail!("unknown --matrix {other}"),
            };
            if let Some(av_path) = &avcam {
                let av = std::fs::read(av_path)?;
                let fc_off = 0x03E8F154 - 0x035C6000;
                let ec_off = 0x03E55330 - 0x035C6000;
                anyhow::ensure!(av.len() >= fc_off + 96 * 2 && av.len() >= ec_off + 96 * 4, "av-cam.bin too small");
                for i in 0..96 {
                    params.fc[i] = u16::from_le_bytes([av[fc_off + i * 2], av[fc_off + i * 2 + 1]]) as i32;
                    let w = u32::from_le_bytes([av[ec_off + i * 4], av[ec_off + i * 4 + 1], av[ec_off + i * 4 + 2], av[ec_off + i * 4 + 3]]);
                    params.ec[i] = bionz_core::mode0_10bit((w & 0xffff) as i32) * 4095 / 1023;
                }
            }
            if use_srgb {
                for i in 0..96 {
                    let x = i as f64 / 95.0;
                    let s = if x <= 0.0031308 { x * 12.92 } else { 1.055 * x.powf(1.0 / 2.4) - 0.055 };
                    params.fc[i] = (s * 1023.0).round() as i32;
                }
            }
            if let Some(fc_path) = &fc_json {
                let lut: Vec<i32> = serde_json::from_slice(&std::fs::read(fc_path)?)?;
                anyhow::ensure!(lut.len() == 96, "fc_json must have 96 entries");
                params.fc.copy_from_slice(&lut);
                params.bypass[6] = true;
                params.bypass[7] = false;
            }
            if let Some(dir) = &crop_dir {
                std::fs::create_dir_all(dir)?;
            }
            let mut results = Vec::new();
            for arw_path in &inputs {
                let jpg_path = ["JPG", "jpg", "JPEG", "jpeg"]
                    .iter()
                    .map(|ext| arw_path.with_extension(ext))
                    .find(|p| p.is_file())
                    .ok_or_else(|| anyhow::anyhow!("no matching JPEG for {}", arw_path.display()))?;
                let frame = bionz_raw::decode_raw_frame(arw_path)?;
                let opt = if iso_model { base_opt.with_iso_preset(frame.iso) } else { base_opt };
                let mut pair_params = params.clone();
                if raw_matrix {
                    if let Some(m) = frame.cam_to_srgb {
                        pair_params.base_matrix = m;
                    }
                }
                let tables = bionz_raw::SpatialTables { bayer: bayer_coef.as_deref(), plane9: None, still: still_coef.as_deref() };
                let linear = bionz_raw::develop_frame(&frame, &opt, false, &tables)?;
                let pixels = bionz_raw::quantize(&linear, ev, pair_params.gamma_max);
                #[cfg(feature = "gpu")]
                let taps = bionz_gpu::run_gpu_banded(&pixels, linear.width as i32, &pair_params)?;
                #[cfg(not(feature = "gpu"))]
                let taps = integer_pipeline(&pixels, linear.width as i32, &pair_params);
                let emu_rgb: Vec<u8> = taps
                    .iter()
                    .flat_map(|t| bionz_core::encoded_to_rgb8(t.encoded_y, t.encoded_cb, t.encoded_cr, pair_params.legal_range))
                    .collect();
                let (cw, ch, cam_rgb_raw) = bionz_raw::load_display(&jpg_path)?;
                let (w, h) = (linear.width, linear.height);
                let cam_rgb = if (cw as usize, ch as usize) == (w, h) {
                    cam_rgb_raw
                } else {
                    bionz_raw::resize_rgb8(&cam_rgb_raw, cw as usize, ch as usize, w, h)
                };
                let (pw, ph) = (512usize.min(w), 512usize.min(h));
                let (x0, y0) = ((w - pw) / 2, (h - ph) / 2);
                let patch_luma = |rgb: &[u8]| -> Vec<u8> {
                    let mut out = Vec::with_capacity(pw * ph);
                    for py in y0..y0 + ph {
                        for px in x0..x0 + pw {
                            let i = (py * w + px) * 3;
                            out.push(((rgb[i] as u32 * 2126 + rgb[i + 1] as u32 * 7152 + rgb[i + 2] as u32 * 722) / 10000) as u8);
                        }
                    }
                    out
                };
                let shift = bionz_compare::phase_shift(&patch_luma(&cam_rgb), &patch_luma(&emu_rgb), pw, ph).unwrap_or([0, 0]);
                let (sw, sh) = (w / 4, h / 4);
                let cam_s = bionz_raw::resize_rgb8(&cam_rgb, w, h, sw, sh);
                let emu_s = bionz_raw::resize_rgb8(&emu_rgb, w, h, sw, sh);
                let (de_mean, de_p95) = bionz_compare::compare_rgb(&cam_s, &emu_s);
                let emu_matched = bionz_compare::color_match_rgb(&cam_s, &emu_s);
                let (de_m_mean, de_m_p95) = bionz_compare::compare_rgb(&cam_s, &emu_matched);
                let m_cam = bionz_compare::measure_spatial(&cam_rgb, w, h);
                let m_emu = bionz_compare::measure_spatial(&emu_rgb, w, h);
                let hf_ratio = m_emu.hf_energy / m_cam.hf_energy.max(1e-9);
                println!(
                    "{}: {}x{} iso={} crisp={:.4} luma_nr={:.2} shift={:?} dE={:.2}/{:.2} dE_matched={:.2}/{:.2} HF_ratio={:.3} (cam {:.2} emu {:.2}) over={:.1}%/{:.1}% under={:.1}%/{:.1}% halo={:.2}/{:.2} noise={:.2}/{:.2} fc={:.1}/{:.1}",
                    arw_path.file_stem().unwrap_or_default().to_string_lossy(),
                    w, h, frame.iso.map(|v| v.to_string()).unwrap_or_else(|| "?".into()),
                    opt.crispening_threshold, opt.luma_nr_strength,
                    shift, de_mean, de_p95, de_m_mean, de_m_p95, hf_ratio, m_cam.hf_energy, m_emu.hf_energy,
                    m_cam.overshoot_pct, m_emu.overshoot_pct, m_cam.undershoot_pct, m_emu.undershoot_pct,
                    m_cam.halo_width, m_emu.halo_width, m_cam.flat_noise, m_emu.flat_noise, m_cam.false_color, m_emu.false_color
                );
                if let Some(dir) = &crop_dir {
                    let stem = arw_path.file_stem().unwrap_or_default().to_string_lossy();
                    let (cx0, cy0, cw_c, ch_c) = ((w / 2).saturating_sub(200), (h / 2).saturating_sub(150), 400.min(w), 300.min(h));
                    let save_crop = |rgb: &[u8], suffix: &str| -> anyhow::Result<()> {
                        let mut buf = Vec::with_capacity(cw_c * ch_c * 3);
                        for py in cy0..cy0 + ch_c {
                            buf.extend_from_slice(&rgb[(py * w + cx0) * 3..(py * w + cx0 + cw_c) * 3]);
                        }
                        image::save_buffer(dir.join(format!("{stem}_{suffix}.png")), &buf, cw_c as u32, ch_c as u32, image::ColorType::Rgb8)?;
                        Ok(())
                    };
                    save_crop(&cam_rgb, "cam")?;
                    save_crop(&emu_rgb, &crop_tag)?;
                }
                results.push(serde_json::json!({
                    "stem": arw_path.file_stem().unwrap_or_default().to_string_lossy(),
                    "arw_sha256": bionz_compare::file_sha256(arw_path).unwrap_or_default(),
                    "jpg_sha256": bionz_compare::file_sha256(&jpg_path).unwrap_or_default(),
                    "width": w,
                    "height": h,
                    "shift": shift,
                    "iso": frame.iso,
                    "crispening": opt.crispening_threshold,
                    "luma_nr": opt.luma_nr_strength,
                    "de2000_mean": de_mean,
                    "de2000_p95": de_p95,
                    "de2000_matched_mean": de_m_mean,
                    "de2000_matched_p95": de_m_p95,
                    "hf_ratio": hf_ratio,
                    "cam": m_cam,
                    "emu": m_emu,
                }));
            }
            let n = results.len().max(1) as f64;
            let avg = |f: fn(&serde_json::Value) -> f64| results.iter().map(f).sum::<f64>() / n;
            println!(
                "AGGREGATE ({} pairs): dE_mean={:.2} dE_p95={:.2} dE_matched={:.2}/{:.2} HF_ratio={:.3} emu_over={:.2}% emu_under={:.2}% emu_halo={:.2} cam_over={:.2}% cam_under={:.2}% cam_halo={:.2} emu_noise={:.2} cam_noise={:.2}",
                results.len(),
                avg(|r| r["de2000_mean"].as_f64().unwrap_or(0.0)),
                avg(|r| r["de2000_p95"].as_f64().unwrap_or(0.0)),
                avg(|r| r["de2000_matched_mean"].as_f64().unwrap_or(0.0)),
                avg(|r| r["de2000_matched_p95"].as_f64().unwrap_or(0.0)),
                avg(|r| r["hf_ratio"].as_f64().unwrap_or(0.0)),
                avg(|r| r["emu"]["overshoot_pct"].as_f64().unwrap_or(0.0)),
                avg(|r| r["emu"]["undershoot_pct"].as_f64().unwrap_or(0.0)),
                avg(|r| r["emu"]["halo_width"].as_f64().unwrap_or(0.0)),
                avg(|r| r["cam"]["overshoot_pct"].as_f64().unwrap_or(0.0)),
                avg(|r| r["cam"]["undershoot_pct"].as_f64().unwrap_or(0.0)),
                avg(|r| r["cam"]["halo_width"].as_f64().unwrap_or(0.0)),
                avg(|r| r["emu"]["flat_noise"].as_f64().unwrap_or(0.0)),
                avg(|r| r["cam"]["flat_noise"].as_f64().unwrap_or(0.0)),
            );
            let high: Vec<&serde_json::Value> = results.iter().filter(|r| r["iso"].as_u64().unwrap_or(0) >= 1600).collect();
            if !high.is_empty() {
                let n = high.len() as f64;
                let avg_high = |f: fn(&serde_json::Value) -> f64| high.iter().map(|r| f(r)).sum::<f64>() / n;
                println!(
                    "HIGH ISO ({} pairs, ISO>=1600): HF_ratio={:.3} emu_noise={:.2} cam_noise={:.2} dE_matched={:.2}",
                    high.len(),
                    avg_high(|r| r["hf_ratio"].as_f64().unwrap_or(0.0)),
                    avg_high(|r| r["emu"]["flat_noise"].as_f64().unwrap_or(0.0)),
                    avg_high(|r| r["cam"]["flat_noise"].as_f64().unwrap_or(0.0)),
                    avg_high(|r| r["de2000_matched_mean"].as_f64().unwrap_or(0.0)),
                );
            }
            if let Some(out_path) = &output {
                if let Some(p) = out_path.parent() {
                    std::fs::create_dir_all(p)?;
                }
                std::fs::write(out_path, serde_json::to_string_pretty(&results)?)?;
                println!("wrote {}", out_path.display());
            }
        }
        Command::StillDesign { stock, strength, output } => {
            let stock_bytes = std::fs::read(&stock)?;
            let designed = bionz_core::spatial::design_natural_still_table(&stock_bytes, strength).map_err(anyhow::Error::msg)?;
            let table = bionz_core::spatial::DcvTable::parse(&designed).map_err(anyhow::Error::msg)?;
            let h = table.health();
            anyhow::ensure!(h.0 == 0 && h.1 == 0, "designed table failed health check: {h:?}");
            if let Some(p) = output.parent() {
                std::fs::create_dir_all(p)?;
            }
            std::fs::write(&output, &designed)?;
            println!("wrote {} ({} bytes, health={:?})", output.display(), designed.len(), h);
        }
        Command::StillFitTone { inputs, ev, output } => {
            let mut bins: Vec<Vec<i32>> = vec![Vec::new(); 96];
            for arw_path in &inputs {
                let jpg_path = ["JPG", "jpg", "JPEG", "jpeg"]
                    .iter()
                    .map(|ext| arw_path.with_extension(ext))
                    .find(|p| p.is_file())
                    .ok_or_else(|| anyhow::anyhow!("no matching JPEG for {}", arw_path.display()))?;
                let frame = bionz_raw::decode_raw_frame(arw_path)?;
                let m = frame.cam_to_srgb.unwrap_or([1024, 0, 0, 0, 1024, 0, 0, 0, 1024]);
                let opt = bionz_core::spatial::FrontOptions::default();
                let tables = bionz_raw::SpatialTables { bayer: None, plane9: None, still: None };
                let linear = bionz_raw::develop_frame(&frame, &opt, true, &tables)?;
                let pixels = bionz_raw::quantize(&linear, ev, 4095);
                let (cw, ch, cam_rgb) = bionz_raw::load_display(&jpg_path)?;
                let (sw, sh) = (linear.width / 4, linear.height / 4);
                let cam_s = bionz_raw::resize_rgb8(&cam_rgb, cw as usize, ch as usize, sw, sh);
                let step_x = (linear.width / sw).max(1);
                let step_y = (linear.height / sh).max(1);
                for py in 0..sh {
                    for px in 0..sw {
                        let sx = (px * step_x).min(linear.width - 1);
                        let sy = (py * step_y).min(linear.height - 1);
                        let p = pixels[sy * linear.width + sx];
                        let r = ((m[0] * p.r + m[1] * p.g + m[2] * p.b + 512) >> 10).clamp(0, 4095);
                        let g = ((m[3] * p.r + m[4] * p.g + m[5] * p.b + 512) >> 10).clamp(0, 4095);
                        let b = ((m[6] * p.r + m[7] * p.g + m[8] * p.b + 512) >> 10).clamp(0, 4095);
                        let o = (py * sw + px) * 3;
                        for (val, tgt) in [(r, cam_s[o]), (g, cam_s[o + 1]), (b, cam_s[o + 2])] {
                            let idx = ((val * 95 + 2047) / 4095).clamp(0, 95) as usize;
                            bins[idx].push((tgt as i32 * 1023 + 127) / 255);
                        }
                    }
                }
            }
            let mut lut = vec![-1i32; 96];
            for (i, b) in bins.iter_mut().enumerate() {
                if !b.is_empty() {
                    b.sort_unstable();
                    lut[i] = b[b.len() / 2];
                }
            }
            if lut[0] < 0 { lut[0] = 0; }
            if lut[95] < 0 { lut[95] = 1023; }
            let mut last = 0usize;
            for i in 1..96 {
                if lut[i] >= 0 {
                    for j in last + 1..i {
                        let t = (j - last) as f64 / (i - last) as f64;
                        lut[j] = (lut[last] as f64 + t * (lut[i] - lut[last]) as f64).round() as i32;
                    }
                    last = i;
                }
            }
            for i in 1..96 {
                if lut[i] < lut[i - 1] {
                    lut[i] = lut[i - 1];
                }
            }
            if let Some(p) = output.parent() {
                std::fs::create_dir_all(p)?;
            }
            std::fs::write(&output, serde_json::to_string_pretty(&lut)?)?;
            println!("wrote {} (lut[0]={}, lut[12]={}, lut[48]={}, lut[95]={})", output.display(), lut[0], lut[12], lut[48], lut[95]);
        }
    }
    Ok(())
}

/// Camera JPEG for an ARW: same-stem file next to it, else `<stem>` under
/// `%USERPROFILE%\Pictures\Sony a6400\*\` (the date folders this project's owners use).
fn find_camera_jpeg(arw: &Path) -> Option<PathBuf> {
    for ext in ["JPG", "jpg", "JPEG", "jpeg"] {
        let path = arw.with_extension(ext);
        if path.is_file() {
            return Some(path);
        }
    }
    let stem = arw.file_stem()?;
    let home = std::env::var_os("USERPROFILE").map(PathBuf::from)?;
    let base = home.join("Pictures").join("Sony a6400");
    let mut found: Vec<PathBuf> = std::fs::read_dir(base)
        .ok()?
        .flatten()
        .filter(|e| e.path().is_dir())
        .flat_map(|dir| {
            ["JPG", "jpg", "JPEG", "jpeg"]
                .iter()
                .map(move |ext| dir.path().join(stem).with_extension(ext))
        })
        .filter(|p| p.is_file())
        .collect();
    found.sort();
    found.into_iter().next()
}

/// Stock still dcv_psf table beside this checkout, when present.
fn find_stock_still_table() -> Option<PathBuf> {    let candidates = [
        "firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/sabin/dcv_psf_table_still.bin",
        "artifacts/qemu-a6400/sabin/dcv_psf_table_still.bin",
    ];
    let mut roots = vec![std::env::current_dir().unwrap_or_default()];
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            roots.push(dir.to_path_buf());
        }
    }
    for root in roots {
        for ancestor in root.ancestors() {
            for rel in candidates {
                let path = ancestor.join(rel);
                if path.is_file() {
                    return Some(path);
                }
            }
        }
    }
    None
}

/// Run the integer chain on the CPU (deterministic, independent of GPU adapters).
fn render_taps(pixels: &[RgbI], width: usize, params: &IntegerParams) -> Vec<u8> {
    integer_pipeline(pixels, width as i32, params)
        .iter()
        .flat_map(|tap| bionz_core::encoded_to_rgb8(tap.encoded_y, tap.encoded_cb, tap.encoded_cr, params.legal_range))
        .collect()
}

/// Camera JPEG on the left, emulated preset on the right, separated by a thin accent line.
fn save_pair_png(path: &std::path::Path, camera: &[u8], emulated: &[u8], w: usize, h: usize, max_side: usize) -> anyhow::Result<()> {
    let side = (max_side / 2).max(1);
    let scale = ((w.max(h) as f64) / side as f64).max(1.0);
    let (dw, dh) = (((w as f64) / scale).round().max(1.0) as usize, ((h as f64) / scale).round().max(1.0) as usize);
    let left = bionz_raw::resize_rgb8(camera, w, h, dw, dh);
    let right = bionz_raw::resize_rgb8(emulated, w, h, dw, dh);
    let mut out = Vec::with_capacity(dw * 2 * dh * 3);
    for row in 0..dh {
        out.extend_from_slice(&left[row * dw * 3..(row + 1) * dw * 3]);
        out.extend_from_slice(&[232, 160, 60]);
        out.extend_from_slice(&right[row * dw * 3..(row + 1) * dw * 3]);
    }
    image::save_buffer(path, &out, (dw * 2 + 1) as u32, dh as u32, image::ColorType::Rgb8)?;
    Ok(())
}

fn slug(name: &str) -> String {
    let s: String = name.chars().map(|c| if c.is_ascii_alphanumeric() { c.to_ascii_lowercase() } else { '-' }).collect();
    s.split('-').filter(|p| !p.is_empty()).collect::<Vec<_>>().join("-")
}
