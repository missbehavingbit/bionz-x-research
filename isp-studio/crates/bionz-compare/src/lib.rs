//! Real-versus-emulated comparison. Alignment is phase correlation on
//! power-of-two luma, then a one-pixel SAD check. Other sizes use the SAD window.
//! A synthetic pair is not a camera observation and cannot promote a stage badge.

use std::path::Path;

use serde::Serialize;
use sha2::{Digest, Sha256};

#[derive(Clone, Debug, Serialize)]
pub struct Receipt {
    pub kind: String,
    pub inputs: Vec<InputId>,
    pub tool: String,
    pub command: String,
    pub policy: String,
    pub observation: String,
    pub assumptions: Vec<String>,
    pub failures: Vec<String>,
    pub repeatability: String,
    pub next_boundary: String,
    pub shift: [i32; 2],
    pub mean_de2000: f64,
    pub p95_de2000: f64,
    pub promotes_badge: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct InputId {
    pub path: String,
    pub sha256: String,
}

pub fn file_sha256(path: &Path) -> Option<String> {
    let bytes = std::fs::read(path).ok()?;
    Some(hex(&Sha256::digest(bytes)))
}

pub fn delta_e2000(lab1: [f64; 3], lab2: [f64; 3]) -> f64 {
    let (l1, a1, b1) = (lab1[0], lab1[1], lab1[2]);
    let (l2, a2, b2) = (lab2[0], lab2[1], lab2[2]);
    let c = (a1.hypot(b1) + a2.hypot(b2)) / 2.0;
    let g = 0.5 * (1.0 - (c.powi(7) / (c.powi(7) + 25f64.powi(7))).sqrt());
    let (a1, a2) = ((1.0 + g) * a1, (1.0 + g) * a2);
    let (c1, c2) = (a1.hypot(b1), a2.hypot(b2));
    let h1 = b1.atan2(a1).to_degrees().rem_euclid(360.0);
    let h2 = b2.atan2(a2).to_degrees().rem_euclid(360.0);
    let mut dh = h2 - h1;
    let neutral = c1 * c2 == 0.0;
    let short = dh.abs() <= 180.0;
    if neutral {
        dh = 0.0;
    } else if !short {
        dh = (dh + 180.0).rem_euclid(360.0) - 180.0;
    }
    let dl = l2 - l1;
    let dc = c2 - c1;
    let d_h = 2.0 * (c1 * c2).sqrt() * (dh.to_radians() / 2.0).sin();
    let l = (l1 + l2) / 2.0;
    let c = (c1 + c2) / 2.0;
    let mut h = h1 + h2;
    if !neutral {
        h = if short { h / 2.0 } else { (h / 2.0 + 180.0).rem_euclid(360.0) };
    }
    let t = 1.0 - 0.17 * (h - 30.0).to_radians().cos()
        + 0.24 * (2.0 * h).to_radians().cos()
        + 0.32 * (3.0 * h + 6.0).to_radians().cos()
        - 0.20 * (4.0 * h - 63.0).to_radians().cos();
    let ls = 1.0 + 0.015 * (l - 50.0).powi(2) / (20.0 + (l - 50.0).powi(2)).sqrt();
    let cs = 1.0 + 0.045 * c;
    let hs = 1.0 + 0.015 * c * t;
    let rt = -2.0 * (c.powi(7) / (c.powi(7) + 25f64.powi(7))).sqrt()
        * (60.0 * (-((h - 275.0) / 25.0).powi(2)).exp()).to_radians().sin();
    let (x, y, z) = (dl / ls, dc / cs, d_h / hs);
    (x * x + y * y + z * z + rt * y * z).max(0.0).sqrt()
}

#[derive(Clone, Copy, Debug)]
struct Cpx {
    re: f64,
    im: f64,
}

fn c_add(a: Cpx, b: Cpx) -> Cpx { Cpx { re: a.re + b.re, im: a.im + b.im } }
fn c_sub(a: Cpx, b: Cpx) -> Cpx { Cpx { re: a.re - b.re, im: a.im - b.im } }
fn c_mul(a: Cpx, b: Cpx) -> Cpx { Cpx { re: a.re * b.re - a.im * b.im, im: a.re * b.im + a.im * b.re } }

fn fft(data: &mut [Cpx], inverse: bool) {
    let n = data.len();
    let mut j = 0usize;
    for i in 1..n {
        let mut bit = n >> 1;
        while j & bit != 0 {
            j ^= bit;
            bit >>= 1;
        }
        j ^= bit;
        if i < j {
            data.swap(i, j);
        }
    }
    let mut len = 2usize;
    while len <= n {
        let ang = std::f64::consts::TAU / len as f64 * if inverse { 1.0 } else { -1.0 };
        let wlen = Cpx { re: ang.cos(), im: ang.sin() };
        let mut start = 0usize;
        while start < n {
            let mut w = Cpx { re: 1.0, im: 0.0 };
            for k in 0..len / 2 {
                let u = data[start + k];
                let v = c_mul(data[start + k + len / 2], w);
                data[start + k] = c_add(u, v);
                data[start + k + len / 2] = c_sub(u, v);
                w = c_mul(w, wlen);
            }
            start += len;
        }
        len <<= 1;
    }
    if inverse {
        for value in data.iter_mut() {
            value.re /= n as f64;
            value.im /= n as f64;
        }
    }
}

fn fft2(grid: &mut [Cpx], width: usize, height: usize, inverse: bool) {
    let mut row = vec![Cpx { re: 0.0, im: 0.0 }; width];
    for y in 0..height {
        row.copy_from_slice(&grid[y * width..(y + 1) * width]);
        fft(&mut row, inverse);
        grid[y * width..(y + 1) * width].copy_from_slice(&row);
    }
    let mut col = vec![Cpx { re: 0.0, im: 0.0 }; height];
    for x in 0..width {
        for y in 0..height {
            col[y] = grid[y * width + x];
        }
        fft(&mut col, inverse);
        for y in 0..height {
            grid[y * width + x] = col[y];
        }
    }
}

/// Circular shift of `other` relative to `reference`, from the phase-correlation peak.
/// Both dimensions must be powers of two. `None` means the sizes cannot be transformed.
pub fn phase_shift(reference: &[u8], other: &[u8], width: usize, height: usize) -> Option<[i32; 2]> {
    if width < 2 || height < 2 || width & (width - 1) != 0 || height & (height - 1) != 0 {
        return None;
    }
    if reference.len() != width * height || other.len() != width * height {
        return None;
    }
    let mut freq_ref: Vec<Cpx> = reference.iter().map(|v| Cpx { re: *v as f64, im: 0.0 }).collect();
    let mut freq_other: Vec<Cpx> = other.iter().map(|v| Cpx { re: *v as f64, im: 0.0 }).collect();
    fft2(&mut freq_ref, width, height, false);
    fft2(&mut freq_other, width, height, false);
    let mut cross = Vec::with_capacity(freq_ref.len());
    for (left, right) in freq_ref.iter().zip(&freq_other) {
        let re = left.re * right.re + left.im * right.im;
        let im = left.im * right.re - left.re * right.im;
        let mag = re.hypot(im);
        if mag < 1e-8 {
            cross.push(Cpx { re: 0.0, im: 0.0 });
        } else {
            cross.push(Cpx { re: re / mag, im: im / mag });
        }
    }
    fft2(&mut cross, width, height, true);
    let (index, _) = cross.iter().enumerate().max_by(|(_, a), (_, b)| a.re.total_cmp(&b.re))?;
    let mut x = (index % width) as i32;
    let mut y = (index / width) as i32;
    if x > width as i32 / 2 {
        x -= width as i32;
    }
    if y > height as i32 / 2 {
        y -= height as i32;
    }
    Some([-x, -y])
}

/// Translation from phase correlation, then a one-pixel SAD check around that peak.
/// The linear terms stay identity: this pair has no measured scale or rotation.
pub fn align_translation(reference: &[u8], other: &[u8], width: usize, height: usize) -> [i32; 2] {
    let seed = phase_shift(reference, other, width, height).unwrap_or([0, 0]);
    let mut best = seed;
    let mut best_score = i64::MAX;
    for dy in seed[1] - 1..=seed[1] + 1 {
        for dx in seed[0] - 1..=seed[0] + 1 {
            if dx.abs() >= width as i32 || dy.abs() >= height as i32 {
                continue;
            }
            let mut score = 0i64;
            let margin = 1i32;
            for y in margin..height as i32 - margin {
                for x in margin..width as i32 - margin {
                    let ox = x + dx;
                    let oy = y + dy;
                    if ox < 0 || oy < 0 || ox >= width as i32 || oy >= height as i32 {
                        continue;
                    }
                    let a = reference[(y as usize) * width + x as usize] as i64;
                    let b = other[(oy as usize) * width + ox as usize] as i64;
                    score += (a - b).abs();
                }
            }
            if score < best_score {
                best_score = score;
                best = [dx, dy];
            }
        }
    }
    best
}

pub fn find_shift(reference: &[u8], other: &[u8], width: usize, height: usize, radius: i32) -> [i32; 2] {
    let mut best = [0, 0];
    let mut best_score = i64::MAX;
    for dy in -radius..=radius {
        for dx in -radius..=radius {
            let mut score = 0i64;
            for y in radius..height as i32 - radius {
                for x in radius..width as i32 - radius {
                    let a = reference[(y as usize) * width + x as usize] as i64;
                    let b = other[((y + dy) as usize) * width + (x + dx) as usize] as i64;
                    score += (a - b).abs();
                }
            }
            if score < best_score {
                best_score = score;
                best = [dx, dy];
            }
        }
    }
    best
}

pub fn compare_rgb(reference: &[u8], emulated: &[u8]) -> (f64, f64) {
    let mut values = Vec::new();
    for (a, b) in reference.chunks(3).zip(emulated.chunks(3)) {
        let lab_a = srgb_to_lab(a);
        let lab_b = srgb_to_lab(b);
        values.push(delta_e2000(lab_a, lab_b));
    }
    values.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let mean = values.iter().sum::<f64>() / values.len().max(1) as f64;
    let p95 = values.get((values.len() as f64 * 0.95) as usize).copied().unwrap_or(0.0);
    (mean, p95)
}

fn lab_chroma(lab: [f64; 3]) -> f64 {
    (lab[1] * lab[1] + lab[2] * lab[2]).sqrt()
}

fn lab_hue_deg(lab: [f64; 3]) -> f64 {
    lab[2].atan2(lab[1]).to_degrees().rem_euclid(360.0)
}

/// Preset-quality metrics measured against the camera JPEG at the same size.
#[derive(Clone, Copy, Debug, Default)]
pub struct PresetMetrics {
    /// Pixels the camera renders near-neutral (Lab chroma < 6, L 20..90).
    pub neutral_pixels: usize,
    /// Mean ΔE2000 on those near-neutral pixels. The look should not tint grays.
    pub neutral_de: f64,
    /// Mean chroma difference (emulated - camera) on those pixels.
    pub neutral_chroma_delta: f64,
    /// Pixels in the skin-tone sector of the camera image.
    pub skin_pixels: usize,
    /// Mean circular hue difference in degrees (emulated - camera) on skin tones.
    pub skin_hue_delta_deg: f64,
    /// Mean chroma ratio (emulated / camera) on skin tones.
    pub skin_chroma_ratio: f64,
    /// Share of camera shadows (L < 15) still dark (L < 25) in the emulation.
    pub shadow_retention: f64,
    /// Share of camera highlights (L > 90) still bright (L > 80) in the emulation.
    pub highlight_retention: f64,
}

/// Measure neutral-axis drift, skin-tone stability and tone retention between matched images.
pub fn preset_metrics(reference: &[u8], emulated: &[u8]) -> PresetMetrics {
    let mut out = PresetMetrics::default();
    let (mut neutral_de_sum, mut neutral_chroma_sum) = (0.0, 0.0);
    let (mut skin_hue_x, mut skin_hue_y) = (0.0, 0.0);
    let (mut skin_chroma_cam, mut skin_chroma_emu) = (0.0, 0.0);
    let (mut shadows_total, mut shadows_kept) = (0usize, 0usize);
    let (mut highlights_total, mut highlights_kept) = (0usize, 0usize);
    for (a, b) in reference.chunks(3).zip(emulated.chunks(3)) {
        let lab_a = srgb_to_lab(a);
        let lab_b = srgb_to_lab(b);
        let ca = lab_chroma(lab_a);
        if (20.0..=90.0).contains(&lab_a[0]) && ca < 6.0 {
            out.neutral_pixels += 1;
            neutral_de_sum += delta_e2000(lab_a, lab_b);
            neutral_chroma_sum += lab_chroma(lab_b) - ca;
        }
        if (25.0..=85.0).contains(&lab_a[0]) && (20.0..=70.0).contains(&lab_hue_deg(lab_a)) && (8.0..60.0).contains(&ca) {
            out.skin_pixels += 1;
            let mut delta = lab_hue_deg(lab_b) - lab_hue_deg(lab_a);
            if delta > 180.0 {
                delta -= 360.0;
            } else if delta < -180.0 {
                delta += 360.0;
            }
            let rad = delta.to_radians();
            skin_hue_x += rad.cos();
            skin_hue_y += rad.sin();
            skin_chroma_cam += ca;
            skin_chroma_emu += lab_chroma(lab_b);
        }
        if lab_a[0] < 15.0 {
            shadows_total += 1;
            if lab_b[0] < 25.0 {
                shadows_kept += 1;
            }
        }
        if lab_a[0] > 90.0 {
            highlights_total += 1;
            if lab_b[0] > 80.0 {
                highlights_kept += 1;
            }
        }
    }
    let n = out.neutral_pixels.max(1) as f64;
    out.neutral_de = neutral_de_sum / n;
    out.neutral_chroma_delta = neutral_chroma_sum / n;
    if out.skin_pixels > 0 {
        out.skin_hue_delta_deg = skin_hue_y.atan2(skin_hue_x).to_degrees();
        out.skin_chroma_ratio = skin_chroma_emu / skin_chroma_cam.max(1e-9);
    } else {
        out.skin_chroma_ratio = 1.0;
    }
    out.shadow_retention = shadows_kept as f64 / shadows_total.max(1) as f64;
    out.highlight_retention = highlights_kept as f64 / highlights_total.max(1) as f64;
    out
}

pub fn abs_difference(reference: &[u8], other: &[u8]) -> Vec<u8> {
    reference.iter().zip(other).map(|(left, right)| left.abs_diff(*right)).collect()
}

pub fn delta_e_heatmap(reference: &[u8], other: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(reference.len());
    for (left, right) in reference.chunks(3).zip(other.chunks(3)) {
        let score = delta_e2000(srgb_to_lab(left), srgb_to_lab(right));
        let hot = ((score / 10.0).clamp(0.0, 1.0) * 255.0) as u8;
        let cool = (80.0 * (1.0 - hot as f64 / 255.0)) as u8;
        out.extend([hot, cool, 40]);
    }
    out
}

#[derive(Clone, Debug)]
pub struct ChartHit {
    pub origin: [usize; 2],
    pub patch: usize,
    pub gap: usize,
    pub rgb: [[u8; 3]; 24],
}

fn patch_stats(rgb: &[u8], width: usize, x0: usize, y0: usize, size: usize) -> ([f64; 3], f64) {
    let inset = (size / 4).max(1);
    let mut acc = [0.0; 3];
    let mut count = 0.0;
    let mut luma_acc = 0.0;
    let mut luma_sq = 0.0;
    for y in y0 + inset..y0 + size - inset {
        for x in x0 + inset..x0 + size - inset {
            let i = (y * width + x) * 3;
            let sample = [rgb[i] as f64, rgb[i + 1] as f64, rgb[i + 2] as f64];
            for channel in 0..3 {
                acc[channel] += sample[channel];
            }
            let luma = 0.2126 * sample[0] + 0.7152 * sample[1] + 0.0722 * sample[2];
            luma_acc += luma;
            luma_sq += luma * luma;
            count += 1.0;
        }
    }
    if count == 0.0 {
        return ([0.0; 3], 0.0);
    }
    let mean = [acc[0] / count, acc[1] / count, acc[2] / count];
    let variance = (luma_sq / count - (luma_acc / count).powi(2)).max(0.0);
    (mean, variance.sqrt())
}

/// Search for a flat 6×4 patch grid. A hit is a candidate layout, not proof of a photographed chart.
pub fn detect_colorchecker(rgb: &[u8], width: usize, height: usize) -> Option<ChartHit> {
    const COLS: usize = 6;
    const ROWS: usize = 4;
    const GAP: usize = 2;
    let background = corner_mean(rgb, width, height);
    let mut best_score = 50.0;
    let mut best = None;
    for patch in [6usize, 8, 10] {
        let span_x = COLS * patch + (COLS - 1) * GAP;
        let span_y = ROWS * patch + (ROWS - 1) * GAP;
        if span_x >= width || span_y >= height {
            continue;
        }
        let mut y = 0;
        while y + span_y < height {
            let mut x = 0;
            while x + span_x < width {
                let mut uniform = 0.0;
                let mut contrast = 0.0;
                let mut gutters = 0.0;
                let mut separated = 0.0;
                let mut means = [[0u8; 3]; 24];
                let mut previous = [0.0; 3];
                let mut flat = true;
                for row in 0..ROWS {
                    for col in 0..COLS {
                        let px = x + col * (patch + GAP);
                        let py = y + row * (patch + GAP);
                        let (mean, deviation) = patch_stats(rgb, width, px, py, patch);
                        if deviation > 8.0 {
                            flat = false;
                        }
                        uniform += deviation;
                        if col > 0 {
                            contrast += (mean[0] - previous[0]).abs() + (mean[1] - previous[1]).abs() + (mean[2] - previous[2]).abs();
                            let gap_index = (py + patch / 2) * width + (px - 1);
                            let gap = [rgb[gap_index * 3] as f64, rgb[gap_index * 3 + 1] as f64, rgb[gap_index * 3 + 2] as f64];
                            gutters += (mean[0] - gap[0]).abs() + (mean[1] - gap[1]).abs() + (mean[2] - gap[2]).abs();
                        }
                        if row > 0 {
                            let gap_index = (py - 1) * width + (px + patch / 2);
                            let gap = [rgb[gap_index * 3] as f64, rgb[gap_index * 3 + 1] as f64, rgb[gap_index * 3 + 2] as f64];
                            gutters += (mean[0] - gap[0]).abs() + (mean[1] - gap[1]).abs() + (mean[2] - gap[2]).abs();
                        }
                        let border = (mean[0] - background[0]).abs() + (mean[1] - background[1]).abs() + (mean[2] - background[2]).abs();
                        if border > 30.0 {
                            separated += 1.0;
                        }
                        previous = mean;
                        means[row * COLS + col] = [mean[0] as u8, mean[1] as u8, mean[2] as u8];
                    }
                }
                let score = separated * 1000.0 + contrast + gutters - uniform * 4.0;
                if flat && score > best_score {
                    best_score = score;
                    best = Some(ChartHit { origin: [x, y], patch, gap: GAP, rgb: means });
                }
                x += 2;
            }
            y += 2;
        }
    }
    best
}

pub fn chart_delta_e(reference: &ChartHit, other: &ChartHit) -> (f64, [f64; 24]) {
    let mut scores = [0.0; 24];
    for index in 0..24 {
        scores[index] = delta_e2000(srgb_to_lab(&reference.rgb[index]), srgb_to_lab(&other.rgb[index]));
    }
    (scores.iter().sum::<f64>() / 24.0, scores)
}

fn corner_mean(rgb: &[u8], width: usize, height: usize) -> [f64; 3] {
    let corners = [(0usize, 0usize), (width - 1, 0), (0, height - 1), (width - 1, height - 1)];
    let mut acc = [0.0; 3];
    for (x, y) in corners {
        let i = (y * width + x) * 3;
        acc[0] += rgb[i] as f64;
        acc[1] += rgb[i + 1] as f64;
        acc[2] += rgb[i + 2] as f64;
    }
    [acc[0] / 4.0, acc[1] / 4.0, acc[2] / 4.0]
}

fn srgb_to_lab(rgb: &[u8]) -> [f64; 3] {    let lin = rgb.iter().map(|v| {
        let v = *v as f64 / 255.0;
        if v <= 0.04045 { v / 12.92 } else { ((v + 0.055) / 1.055).powf(2.4) }
    }).collect::<Vec<_>>();
    let x = 0.4124564 * lin[0] + 0.3575761 * lin[1] + 0.1804375 * lin[2];
    let y = 0.2126729 * lin[0] + 0.7151522 * lin[1] + 0.0721750 * lin[2];
    let z = 0.0193339 * lin[0] + 0.1191920 * lin[1] + 0.9503041 * lin[2];
    let f = |t: f64| {
        let d = 6.0 / 29.0;
        if t > d * d * d { t.cbrt() } else { t / (3.0 * d * d) + 4.0 / 29.0 }
    };
    let (fx, fy, fz) = (f(x / 0.95047), f(y), f(z / 1.08883));
    [116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)]
}

pub fn receipt_for_shift(reference: &Path, other: &Path, shift: [i32; 2], mean: f64, p95: f64, synthetic: bool) -> Receipt {
    Receipt {
        kind: if synthetic { "synthetic-self-check".into() } else { "camera-pair".into() },
        inputs: vec![
            InputId { path: reference.display().to_string(), sha256: file_sha256(reference).unwrap_or_default() },
            InputId { path: other.display().to_string(), sha256: file_sha256(other).unwrap_or_default() },
        ],
        tool: "bionz-compare".into(),
        command: "compare".into(),
        policy: "files only; no camera or USB access".into(),
        observation: format!("shift {shift:?}, mean dE2000 {mean:.4}, p95 {p95:.4}"),
        assumptions: vec!["sRGB display code values".into(), "translation-only alignment before an optional manual four-point refine".into()],
        failures: vec![],
        repeatability: "deterministic on the same bytes".into(),
        next_boundary: if synthetic {
            "A matched ARW and camera JPEG are required before any stage badge changes.".into()
        } else {
            "Write an evidence record before promoting an assumption-labelled stage.".into()
        },
        shift,
        mean_de2000: mean,
        p95_de2000: p95,
        promotes_badge: false,
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct SpatialMetrics {
    pub hf_energy: f64,
    pub overshoot_pct: f64,
    pub undershoot_pct: f64,
    pub halo_width: f64,
    pub flat_noise: f64,
    pub false_color: f64,
}

fn luma_f64(rgb: &[u8], i: usize) -> f64 {
    0.2126 * rgb[i * 3] as f64 + 0.7152 * rgb[i * 3 + 1] as f64 + 0.0722 * rgb[i * 3 + 2] as f64
}

/// Measure spatial quality metrics on an 8-bit RGB image:
/// - `hf_energy`: RMS of 3x3 high-pass luma residual
/// - `overshoot_pct` / `undershoot_pct` / `halo_width`: 1D horizontal/vertical step-edge profile stats
/// - `flat_noise`: mean luma std in the flattest 20% of 16x16 blocks
/// - `false_color`: mean |R-G|+|B-G| excess on strong edge pixels relative to smooth chroma
pub fn measure_spatial(rgb: &[u8], w: usize, h: usize) -> SpatialMetrics {
    let n = w * h;
    let mut y = vec![0.0f64; n];
    for i in 0..n {
        y[i] = luma_f64(rgb, i);
    }
    // 1. High-frequency energy: RMS of (y - 3x3 box(y)).
    let mut hf_sq = 0.0;
    let mut hf_n = 0usize;
    for py in 1..h.saturating_sub(1) {
        for px in 1..w.saturating_sub(1) {
            let mut sum = 0.0;
            for dy in -1isize..=1 {
                for dx in -1isize..=1 {
                    sum += y[((py as isize + dy) as usize) * w + (px as isize + dx) as usize];
                }
            }
            let d = y[py * w + px] - sum / 9.0;
            hf_sq += d * d;
            hf_n += 1;
        }
    }
    let hf_energy = (hf_sq / hf_n.max(1) as f64).sqrt();

    // 2. Flat-area noise: 16x16 blocks, take lowest 20% by gradient energy, average luma std.
    let mut blocks = Vec::new();
    let bw = w / 16;
    let bh = h / 16;
    for by in 0..bh {
        for bx in 0..bw {
            let mut sum = 0.0;
            let mut sq = 0.0;
            let mut grad = 0.0;
            for dy in 0..16 {
                let row = (by * 16 + dy) * w + bx * 16;
                for dx in 0..16 {
                    let v = y[row + dx];
                    sum += v;
                    sq += v * v;
                    if dx + 1 < 16 {
                        grad += (y[row + dx + 1] - v).abs();
                    }
                }
            }
            let mean = sum / 256.0;
            let std = (sq / 256.0 - mean * mean).max(0.0).sqrt();
            blocks.push((grad, std));
        }
    }
    blocks.sort_by(|a, b| a.0.total_cmp(&b.0));
    let keep = (blocks.len() / 5).max(1);
    let flat_noise = if blocks.is_empty() { 0.0 } else { blocks[..keep].iter().map(|b| b.1).sum::<f64>() / keep as f64 };

    // 3. Edge overshoot / undershoot / halo width & false color on strong horizontal transitions.
    let mut over_sum = 0.0;
    let mut under_sum = 0.0;
    let mut halo_sum = 0.0;
    let mut edge_n = 0usize;
    let mut fc_sum = 0.0;
    for py in (2..h.saturating_sub(2)).step_by(2) {
        for px in 6..w.saturating_sub(6) {
            let g = y[py * w + px + 1] - y[py * w + px - 1];
            if g.abs() < 40.0 {
                continue;
            }
            // Monotonicity check around edge center to avoid textured regions.
            let lo = (y[py * w + px - 5] + y[py * w + px - 4]) * 0.5;
            let hi = (y[py * w + px + 4] + y[py * w + px + 5]) * 0.5;
            let step = (hi - lo).abs();
            if step < 35.0 {
                continue;
            }
            let (low, high) = if lo < hi { (lo, hi) } else { (hi, lo) };
            let mut pmin = f64::INFINITY;
            let mut pmax = f64::NEG_INFINITY;
            let mut width = 0usize;
            for dx in -4isize..=4 {
                let v = y[py * w + (px as isize + dx) as usize];
                pmin = pmin.min(v);
                pmax = pmax.max(v);
                if v > high + 0.02 * step || v < low - 0.02 * step {
                    width += 1;
                }
            }
            over_sum += ((pmax - high) / step).max(0.0) * 100.0;
            under_sum += ((low - pmin) / step).max(0.0) * 100.0;
            halo_sum += width as f64;
            let i = py * w + px;
            let (r, gch, b) = (rgb[i * 3] as f64, rgb[i * 3 + 1] as f64, rgb[i * 3 + 2] as f64);
            fc_sum += (r - gch).abs() + (b - gch).abs();
            edge_n += 1;
        }
    }
    let denom = edge_n.max(1) as f64;
    SpatialMetrics {
        hf_energy,
        overshoot_pct: over_sum / denom,
        undershoot_pct: under_sum / denom,
        halo_width: halo_sum / denom,
        flat_noise,
        false_color: fc_sum / denom,
    }
}

pub fn hex(bytes: impl AsRef<[u8]>) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    bytes.as_ref().iter().map(|b| format!("{b:02x}")).collect::<String>().chars().for_each(|_| {});
    let bytes = bytes.as_ref();
    let mut out = String::new();
    for b in bytes {
        out.push(HEX[(b >> 4) as usize] as char);
        out.push(HEX[(b & 0xf) as usize] as char);
    }
    let _ = HEX;
    out
}

/// Fits a 3x4 affine colour transform in linearized sRGB from `test` to `reference`
/// and returns the colour-matched RGB8 buffer of `test`.
pub fn color_match_rgb(reference: &[u8], test: &[u8]) -> Vec<u8> {
    let n = (reference.len().min(test.len())) / 3;
    if n == 0 {
        return test.to_vec();
    }
    let to_lin = |v: u8| -> f64 {
        let x = v as f64 / 255.0;
        if x <= 0.04045 { x / 12.92 } else { ((x + 0.055) / 1.055).powf(2.4) }
    };
    let to_srgb = |v: f64| -> u8 {
        let x = v.clamp(0.0, 1.0);
        let s = if x <= 0.0031308 { x * 12.92 } else { 1.055 * x.powf(1.0 / 2.4) - 0.055 };
        (s * 255.0).round().clamp(0.0, 255.0) as u8
    };
    let mut xtx = [[0.0f64; 4]; 4];
    let mut xty = [[0.0f64; 3]; 4];
    let step = (n / 8192).max(1);
    for idx in (0..n).step_by(step) {
        let o = idx * 3;
        let x = [to_lin(test[o]), to_lin(test[o + 1]), to_lin(test[o + 2]), 1.0];
        let y = [to_lin(reference[o]), to_lin(reference[o + 1]), to_lin(reference[o + 2])];
        for r in 0..4 {
            for c in 0..4 {
                xtx[r][c] += x[r] * x[c];
            }
            for c in 0..3 {
                xty[r][c] += x[r] * y[c];
            }
        }
    }
    for i in 0..4 {
        xtx[i][i] += 1e-6;
    }
    // Gauss-Jordan elimination on 4x4 augmented with 3 RHS columns.
    let mut aug = [[0.0f64; 7]; 4];
    for r in 0..4 {
        aug[r][..4].copy_from_slice(&xtx[r]);
        aug[r][4..7].copy_from_slice(&xty[r]);
    }
    for col in 0..4 {
        let piv = (col..4).max_by(|&a, &b| aug[a][col].abs().total_cmp(&aug[b][col].abs())).unwrap_or(col);
        aug.swap(col, piv);
        let d = aug[col][col];
        if d.abs() < 1e-12 {
            return test.to_vec();
        }
        for j in col..7 {
            aug[col][j] /= d;
        }
        for r in 0..4 {
            if r == col { continue; }
            let f = aug[r][col];
            for j in col..7 {
                aug[r][j] -= f * aug[col][j];
            }
        }
    }
    let mut out = Vec::with_capacity(n * 3);
    for idx in 0..n {
        let o = idx * 3;
        let x = [to_lin(test[o]), to_lin(test[o + 1]), to_lin(test[o + 2]), 1.0];
        for c in 0..3 {
            let v = aug[0][4 + c] * x[0] + aug[1][4 + c] * x[1] + aug[2][4 + c] * x[2] + aug[3][4 + c] * x[3];
            out.push(to_srgb(v));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_delta_e_and_shift() {
        let zero = delta_e2000([50.0, 0.0, 0.0], [50.0, 0.0, 0.0]);
        assert!(zero.abs() < 1e-9);        let sample = delta_e2000([50.0, 2.6772, -79.7751], [50.0, 0.0, -82.7485]);
        assert!((sample - 2.0425).abs() < 0.02, "{sample}");
        let mut reference = vec![0u8; 16 * 16];
        let mut other = vec![0u8; 16 * 16];
        for y in 0..16 {
            for x in 0..16 {
                reference[y * 16 + x] = ((x * 13 + y * 7) % 256) as u8;
            }
        }
        for y in 0..16 {
            for x in 0..16 {
                let sx = (x as i32 - 2).clamp(0, 15) as usize;
                let sy = (y as i32 + 1).clamp(0, 15) as usize;
                other[y * 16 + x] = reference[sy * 16 + sx];
            }
        }
        assert_eq!(find_shift(&reference, &other, 16, 16, 3), [2, -1]);
        let mut rolled = vec![0u8; 16 * 16];
        for y in 0..16 {
            for x in 0..16 {
                let sx = (x + 16 - 3) % 16;
                let sy = (y + 16 - 5) % 16;
                rolled[y * 16 + x] = reference[sy * 16 + sx];
            }
        }
        assert_eq!(phase_shift(&reference, &rolled, 16, 16), Some([3, 5]));
        assert_eq!(align_translation(&reference, &rolled, 16, 16), [3, 5]);
        let left = [10u8, 20, 30];
        let right = [200u8, 20, 30];
        assert!(abs_difference(&left, &left).iter().all(|v| *v == 0));
        assert_eq!(abs_difference(&left, &right), vec![190, 0, 0]);
        assert_eq!(delta_e_heatmap(&left, &left)[0], 0);
        assert!(delta_e_heatmap(&left, &right)[0] > 0);
        let mut chart = vec![30u8; 80 * 60 * 3];
        for row in 0..4 {
            for col in 0..6 {
                let color = [(col * 40 + row * 15) as u8, (255 - col * 30) as u8, (row * 60) as u8];
                for y in 0..8 {
                    for x in 0..8 {
                        let px = 10 + col * 10 + x;
                        let py = 8 + row * 10 + y;
                        let i = (py * 80 + px) * 3;
                        chart[i..i + 3].copy_from_slice(&color);
                    }
                }
            }
        }
        let found = detect_colorchecker(&chart, 80, 60).unwrap();
        assert_eq!(found.origin, [10, 8]);
        assert_eq!(found.patch, 8);
        let (mean, _) = chart_delta_e(&found, &found);
        assert!(mean < 1e-9);
        let dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../artifacts/tests/bionz-compare");
        std::fs::create_dir_all(&dir).unwrap();
        let left = dir.join("left.gray");
        let right = dir.join("right.gray");
        std::fs::write(&left, &reference).unwrap();
        std::fs::write(&right, &other).unwrap();
        let receipt = receipt_for_shift(&left, &right, [2, -1], 0.0, 0.0, true);
        assert!(!receipt.promotes_badge);
        std::fs::write(dir.join("synthetic-receipt.json"), serde_json::to_string_pretty(&receipt).unwrap()).unwrap();
    }

    #[test]
    fn preset_metrics_are_exact_for_identical_images_and_flag_a_tint() {
        let mut image = Vec::new();
        for y in 0..24 {
            for x in 0..24 {
                let v = (x * 10).min(255) as u8;
                let px = if y < 4 {
                    [v / 8, v / 8, v / 8] // shadows
                } else if y > 19 {
                    [240, 240, 240] // highlights
                } else {
                    [v, v, v] // neutral ramp
                };
                image.extend(px);
            }
        }
        let same = preset_metrics(&image, &image);
        assert_eq!(same.neutral_de, 0.0);
        assert!(same.neutral_pixels > 0);
        assert_eq!(same.shadow_retention, 1.0);
        assert_eq!(same.highlight_retention, 1.0);
        // A blue cast on the neutral ramp must show up as neutral-axis drift.
        let mut tinted = Vec::with_capacity(image.len());
        for (i, v) in image.iter().enumerate() {
            tinted.push(if i % 3 == 0 { (*v).saturating_sub(6) } else { *v });
        }
        let drift = preset_metrics(&image, &tinted);
        assert!(drift.neutral_de > same.neutral_de);
        assert!(drift.neutral_chroma_delta > 0.0);
    }
}
