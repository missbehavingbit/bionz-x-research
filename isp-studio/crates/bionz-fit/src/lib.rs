//! Port of `lut_core.decompose`.
//! Film recipes use `source = None`. ARRI Rec.709 and wide-gamut use the
//! S-Log3 / S-Gamut3.Cine sample set. Wide gamut also scales chroma near the rail.

use bionz_core::{apply_ycc, matrix_codes, round_code, ycc_to_rgb, OraclePreset, Profile};use bionz_preset::{sample_cube, Cube};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Recipe {
    pub name: String,
    #[serde(default)]
    pub shadow_toe: f64,
    #[serde(default = "one")]
    pub mid_contrast: f64,
    #[serde(default = "default_cross")]
    pub crossover: f64,
    #[serde(default)]
    pub highlight_rolloff: f64,
    #[serde(default)]
    pub shoulder: f64,
    #[serde(default)]
    pub black_fade: f64,
    #[serde(default = "one")]
    pub white_ceiling: f64,
    #[serde(default = "one")]
    pub saturation: f64,
    #[serde(default)]
    pub warmth: f64,
    #[serde(default)]
    pub red_amber: f64,
    #[serde(default)]
    pub green_olive: f64,
    #[serde(default)]
    pub blue_cyan: f64,
    #[serde(default)]
    pub highlight_desat: f64,
    #[serde(default)]
    pub sky_luma: f64,
    #[serde(default)]
    pub sky_sat: f64,
    #[serde(default)]
    pub foliage_luma: f64,
    #[serde(default)]
    pub foliage_sat: f64,
    #[serde(default)]
    pub red_luma: f64,
    #[serde(default)]
    pub red_highlight_hue: f64,
    #[serde(default = "default_skin")]
    pub skin_protect: f64,
    #[serde(default)]
    pub shadow_warmth: f64,
    #[serde(default)]
    pub shadow_tint: f64,
    #[serde(default)]
    pub highlight_warmth: f64,
    #[serde(default)]
    pub highlight_tint: f64,
    #[serde(default)]
    pub monochrome: bool,
    #[serde(default = "default_bw")]
    pub bw_weights: [f64; 3],
}

fn one() -> f64 { 1.0 }
fn default_cross() -> f64 { 0.28 }
fn default_skin() -> f64 { 0.65 }
fn default_bw() -> [f64; 3] { [0.30, 0.59, 0.11] }

#[derive(Clone, Debug)]
pub struct FitReport {
    pub clamped_nodes: i32,
    pub at_rail_nodes: i32,
    pub mean_de76: f64,
    pub p95_de76: f64,
    pub max_de76: f64,
}

pub fn evaluate_recipe(rgb: &[f64], recipe: &Recipe) -> Vec<f64> {
    if recipe.name == "Stock (Reset)" {
        return rgb.iter().map(|v| v.clamp(0.0, 1.0)).collect();
    }
    let mut out = Vec::with_capacity(rgb.len());
    for px in rgb.chunks(3) {
        let toned = [
            filmic(px[0], recipe),
            filmic(px[1], recipe),
            filmic(px[2], recipe),
        ];
        let luma = 0.2126 * toned[0] + 0.7152 * toned[1] + 0.0722 * toned[2];
        let cb = toned[2] - luma;
        let cr = toned[0] - luma;
        let chroma = cb.hypot(cr);
        let sat_mask = (chroma / 0.12).clamp(0.0, 1.0);
        let hue = cb.atan2(cr).to_degrees().rem_euclid(360.0);
        let skin = recipe.skin_protect
            * hue_weight(hue, 350.0, 38.0)
            * (chroma / 0.06).clamp(0.0, 1.0)
            * (1.0 - ((chroma - 0.28) / 0.15).clamp(0.0, 1.0));
        let w_sky = hue_weight(hue, 105.0, 48.0).max(hue_weight(hue, 155.0, 42.0)) * sat_mask;
        let w_foliage = hue_weight(hue, 225.0, 52.0) * sat_mask * (1.0 - skin);
        let w_red = hue_weight(hue, 5.0, 40.0) * sat_mask;
        if recipe.monochrome {
            let mono = recipe.bw_weights[0] * toned[0] + recipe.bw_weights[1] * toned[1] + recipe.bw_weights[2] * toned[2];
            let dy = (recipe.sky_luma * w_sky + recipe.foliage_luma * w_foliage + recipe.red_luma * w_red) * (chroma / 0.10).clamp(0.0, 1.0);
            let y = (mono + dy).clamp(0.0, 1.0);
            out.extend([y, y, y]);
            continue;
        }
        let mat = look_matrix(recipe);
        let mut color = [0.0; 3];
        for row in 0..3 {
            color[row] = (toned[0] * mat[row][0] + toned[1] * mat[row][1] + toned[2] * mat[row][2]).clamp(0.0, 1.0);
        }
        let y_out = 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2];
        if recipe.highlight_desat > 0.0 {
            let hi = recipe.highlight_desat * ((y_out - 0.62) / 0.35).clamp(0.0, 1.0);
            for c in 0..3 {
                color[c] = y_out + (1.0 - hi) * (color[c] - y_out);
            }
        }
        // Chromatic warmth: luminance-tapered midtone tint (see `_look_matrix` in the Python recipe).
        if recipe.warmth.abs() > 1e-9 {
            let ym = y_out.clamp(0.0, 1.0);
            let mid = 4.0 * ym * (1.0 - ym);
            let w = recipe.warmth * mid * (1.0 - 0.35 * skin);
            color[0] = (color[0] + 0.5 * w).clamp(0.0, 1.0);
            color[1] = (color[1] - 0.098 * w).clamp(0.0, 1.0);
            color[2] = (color[2] - 0.5 * w).clamp(0.0, 1.0);
        }
        let dy = (recipe.sky_luma * w_sky + recipe.foliage_luma * w_foliage + recipe.red_luma * w_red)
            * (0.25 + 0.75 * (std::f64::consts::PI * y_out.clamp(0.0, 1.0)).sin());
        let sat_adj = 1.0 + (recipe.sky_sat * w_sky + recipe.foliage_sat * w_foliage) * (1.0 - 0.7 * skin);
        let y_adj = (y_out + dy).clamp(0.0, 1.0);
        for c in 0..3 {
            color[c] = y_adj + sat_adj * (color[c] - y_out);
        }
        if recipe.red_highlight_hue.abs() > 1e-6 {
            let hi_w = ((y_out - 0.35) / 0.50).clamp(0.0, 1.0) * w_red * (1.0 - 0.55 * skin);
            let twist = recipe.red_highlight_hue * hi_w;
            color[1] = (color[1] + twist * 0.22 * (color[0] - color[1])).clamp(0.0, 1.0);
            color[2] = (color[2] - twist * 0.12 * (color[0] - color[2])).clamp(0.0, 1.0);
        }
        let w_shad = (1.0 - y_adj / 0.45).clamp(0.0, 1.0).powf(1.4);
        let w_hi = ((y_adj - 0.48) / 0.48).clamp(0.0, 1.0).powf(1.3);
        let d_r = recipe.shadow_warmth * w_shad + recipe.highlight_warmth * w_hi + 0.5 * (recipe.shadow_tint * w_shad + recipe.highlight_tint * w_hi);
        let d_g = -0.6 * (recipe.shadow_tint * w_shad + recipe.highlight_tint * w_hi);
        let d_b = -(recipe.shadow_warmth * w_shad + recipe.highlight_warmth * w_hi) + 0.5 * (recipe.shadow_tint * w_shad + recipe.highlight_tint * w_hi);
        let anchor = (y_adj / 0.06).min((1.0 - y_adj) / 0.04).clamp(0.0, 1.0);
        color[0] = (color[0] + d_r * anchor).clamp(0.0, 1.0);
        color[1] = (color[1] + d_g * anchor).clamp(0.0, 1.0);
        color[2] = (color[2] + d_b * anchor).clamp(0.0, 1.0);
        out.extend(color);
    }
    out
}

fn filmic(x: f64, recipe: &Recipe) -> f64 {
    let x = x.clamp(0.0, 1.0);
    let xc = recipe.crossover.clamp(0.08, 0.80);
    let m0 = (1.0 + 1.30 * recipe.shadow_toe).clamp(0.35, 2.0);
    let m1 = recipe.mid_contrast.clamp(0.50, 1.70);
    let m2 = (1.0 - 0.85 * recipe.highlight_rolloff).clamp(0.08, 1.0);
    let yc = (xc * 0.5 * (m0 + m1)).clamp(0.02, 0.95);
    let span = 1.0 - xc;
    let hermite = |t: f64, y1: f64, s1: f64, y2: f64, s2: f64, h: f64| {
        let t2 = t * t;
        let t3 = t2 * t;
        let h00 = 2.0 * t3 - 3.0 * t2 + 1.0;
        let h10 = t3 - 2.0 * t2 + t;
        let h01 = -2.0 * t3 + 3.0 * t2;
        let h11 = t3 - t2;
        h00 * y1 + h10 * h * s1 + h01 * y2 + h11 * h * s2
    };
    let y = if x <= xc {
        let t = (x / xc.max(1e-9)).clamp(0.0, 1.0);
        hermite(t, 0.0, m0, yc, m1, xc)
    } else {
        let t = ((x - xc) / span.max(1e-9)).clamp(0.0, 1.0);
        hermite(t, yc, m1, 1.0, m2, span)
    };
    // ARRI-style highlight knee: identity below 0.55, C1 cubic with slope 1 at the knee and
    // slope 1 - shoulder at white. Keeps highlight gradation without clipping.
    let y = {
        let s = recipe.shoulder.clamp(0.0, 1.0);
        if s <= 1e-9 {
            y
        } else {
            let k = 0.55;
            if y <= k {
                y
            } else {
                let u = ((y - k) / (1.0 - k)).clamp(0.0, 1.0);
                k + (1.0 - k) * (u + s * u * u * (1.0 - u))
            }
        }
    };
    (recipe.black_fade + (recipe.white_ceiling - recipe.black_fade) * y.clamp(0.0, 1.0)).clamp(0.0, 1.0)
}

fn hue_weight(hue: f64, center: f64, half_width: f64) -> f64 {
    let diff = ((hue - center + 180.0).rem_euclid(360.0) - 180.0).abs();
    let u = (diff / half_width).clamp(0.0, 1.0);
    0.5 * (1.0 + (std::f64::consts::PI * u).cos())
}

fn look_matrix(recipe: &Recipe) -> [[f64; 3]; 3] {
    let sat = recipe.saturation;
    let (lr, lg, lb) = (0.2126, 0.7152, 0.0722);
    let sat_m = [
        [lr + (1.0 - lr) * sat, lg * (1.0 - sat), lb * (1.0 - sat)],
        [lr * (1.0 - sat), lg + (1.0 - lg) * sat, lb * (1.0 - sat)],
        [lr * (1.0 - sat), lg * (1.0 - sat), lb + (1.0 - lb) * sat],
    ];
    let ra = recipe.red_amber.clamp(-0.35, 0.35);
    let go = recipe.green_olive.clamp(-0.35, 0.35);
    let bc = recipe.blue_cyan.clamp(-0.35, 0.35);
    let hue = [
        [1.0 - ra, ra, 0.0],
        [go * 0.5, 1.0 - go, go * 0.5],
        [0.0, bc, 1.0 - bc],
    ];
    let mut m = [[0.0; 3]; 3];
    for r in 0..3 {
        for c in 0..3 {
            m[r][c] = hue[r][0] * sat_m[0][c] + hue[r][1] * sat_m[1][c] + hue[r][2] * sat_m[2][c];
        }
    }
    m
}

pub fn recipe_cube(recipe: &Recipe, size: usize) -> Cube {
    let mut table = Vec::with_capacity(size * size * size * 3);
    let axis: Vec<f64> = (0..size).map(|i| i as f64 / (size - 1) as f64).collect();
    let mut rgb = Vec::with_capacity(table.capacity());
    for r in 0..size {
        for g in 0..size {
            for b in 0..size {
                rgb.extend([axis[r], axis[g], axis[b]]);
            }
        }
    }
    table = evaluate_recipe(&rgb, recipe);
    Cube { size, domain_min: [0.0; 3], domain_max: [1.0; 3], table, title: recipe.name.clone() }
}

const TO_SGAMUT3CINE: [[f64; 3]; 3] = [
    [0.6456794776099917, 0.2591145469997148, 0.09520597539029337],
    [0.08752999151186004, 0.7596995625540705, 0.1527704459340693],
    [0.03695741988752367, 0.12928090476633827, 0.8337616753461382],
];
const SONY_TO_AWG3: [[f64; 3]; 3] = [
    [0.9744346385608513, 0.02380231623862594, 0.00176304520052296],
    [-0.08922581454940891, 1.0712572754652228, 0.01796853908418612],
    [-0.03535419779143754, 0.03822609644151848, 0.9971281013499192],
];
const AWG_TO_REC709: [[f64; 3]; 3] = [
    [1.6175234363068056, -0.5372866221882938, -0.08023681411851233],
    [-0.07057274089781584, 1.3346130623303276, -0.26404032143251177],
    [-0.02110172804279351, -0.2269538752182664, 1.2480556032610597],
];
const SHOULDER_CODE: [f64; 24] = [
    0.0148, 0.0148, 0.0149, 0.0149, 0.0151, 0.0155, 0.0164, 0.0182, 0.0216, 0.0285, 0.0452, 0.0813,
    0.1374, 0.2228, 0.3457, 0.4832, 0.6190, 0.7531, 0.8705, 0.9406, 0.9778, 0.9940, 0.9993, 1.0000,
];

fn mat_vec(m: [[f64; 3]; 3], v: [f64; 3]) -> [f64; 3] {
    [
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    ]
}

fn slog3_encode(x: f64) -> f64 {
    let hi = (420.0 + ((x + 0.01).max(1e-12) / 0.19).log10() * 261.5) / 1023.0;
    let lo = (x * (171.2102946929 - 95.0) / 0.01125 + 95.0) / 1023.0;
    if x >= 0.01125 { hi } else { lo }
}

fn slog3_decode(y: f64) -> f64 {
    let hi = 10f64.powf((y * 1023.0 - 420.0) / 261.5) * 0.19 - 0.01;
    let lo = (y * 1023.0 - 95.0) * 0.01125 / (171.2102946929 - 95.0);
    if y >= 171.2102946929 / 1023.0 { hi } else { lo }
}

fn logc3_encode(linear: f64) -> f64 {
    const CUT: f64 = 0.010591;
    const A: f64 = 5.555556;
    const B: f64 = 0.052272;
    const C: f64 = 0.247190;
    const D: f64 = 0.385537;
    const E: f64 = 5.367655;
    const F: f64 = 0.092809;
    if linear > CUT { C * (A * linear + B).max(1e-12).log10() + D } else { E * linear + F }
}

fn logc3_decode(code: f64) -> f64 {
    const CUT: f64 = 0.010591;
    const A: f64 = 5.555556;
    const B: f64 = 0.052272;
    const C: f64 = 0.247190;
    const D: f64 = 0.385537;
    const E: f64 = 5.367655;
    const F: f64 = 0.092809;
    if code > E * CUT + F { (10f64.powf((code - D) / C) - B) / A } else { (code - F) / E }
}

fn lin709_to_slog3(rgb: [f64; 3]) -> [f64; 3] {
    let g = mat_vec(TO_SGAMUT3CINE, rgb);
    [slog3_encode(g[0]).clamp(0.0, 1.0), slog3_encode(g[1]).clamp(0.0, 1.0), slog3_encode(g[2]).clamp(0.0, 1.0)]
}

fn slog3_to_lin709(code: [f64; 3]) -> [f64; 3] {
    let lin = [slog3_decode(code[0]), slog3_decode(code[1]), slog3_decode(code[2])];
    let inv = invert3(TO_SGAMUT3CINE);
    [
        inv[0][0] * lin[0] + inv[0][1] * lin[1] + inv[0][2] * lin[2],
        inv[1][0] * lin[0] + inv[1][1] * lin[1] + inv[1][2] * lin[2],
        inv[2][0] * lin[0] + inv[2][1] * lin[1] + inv[2][2] * lin[2],
    ]
}

fn plausible(code: [f64; 3]) -> bool {
    slog3_to_lin709(code).iter().all(|v| *v >= -0.02 && *v <= 16.0)
}

fn shoulder(linear: f64) -> f64 {
    if linear <= 0.0 {
        return 0.0;
    }
    let scene = geomspace(1e-5, 64.0, 24);
    let xp: Vec<f64> = scene.iter().map(|v| v.log10()).collect();
    interp(linear.max(1e-5).log10(), &xp, &SHOULDER_CODE).clamp(SHOULDER_CODE[0], 1.0)
}

fn classic_rec709_pixel(logc: [f64; 3]) -> [f64; 3] {
    let awg = [logc3_decode(logc[0].clamp(0.0, 1.0)), logc3_decode(logc[1].clamp(0.0, 1.0)), logc3_decode(logc[2].clamp(0.0, 1.0))];
    let mut rec = mat_vec(AWG_TO_REC709, awg);
    for v in &mut rec {
        *v = shoulder(v.max(0.0)).clamp(0.0, 1.0);
    }
    let peak = rec[0].max(rec[1]).max(rec[2]);
    let weight = ((peak - 0.45) / (1.0 - 0.45)).clamp(0.0, 1.0).powi(2);
    let scale = 1.0 - 0.65 * weight;
    let mut out = [0.0; 3];
    for c in 0..3 {
        out[c] = (peak - (peak - rec[c]) * scale).clamp(0.0, 1.0);
    }
    out
}

pub fn sample_arri_wide(sony_codes: &[f64]) -> Vec<f64> {
    let mut out = Vec::with_capacity(sony_codes.len());
    for px in sony_codes.chunks(3) {
        let linear = [slog3_decode(px[0]), slog3_decode(px[1]), slog3_decode(px[2])];
        let awg = mat_vec(SONY_TO_AWG3, linear);
        out.extend([
            logc3_encode(awg[0]).clamp(0.0, 1.0),
            logc3_encode(awg[1]).clamp(0.0, 1.0),
            logc3_encode(awg[2]).clamp(0.0, 1.0),
        ]);
    }
    out
}

pub fn sample_arri_rec709(sony_codes: &[f64]) -> Vec<f64> {
    let mut out = Vec::with_capacity(sony_codes.len());
    for px in sony_codes.chunks(3) {
        let linear = [slog3_decode(px[0]), slog3_decode(px[1]), slog3_decode(px[2])];
        let awg = mat_vec(SONY_TO_AWG3, linear);
        let logc = [logc3_encode(awg[0]).clamp(0.0, 1.0), logc3_encode(awg[1]).clamp(0.0, 1.0), logc3_encode(awg[2]).clamp(0.0, 1.0)];
        out.extend(classic_rec709_pixel(logc));
    }
    out
}

fn source_samples() -> (Vec<f64>, Vec<f64>) {
    let axis = linspace(13);
    let mut samples = Vec::new();
    let mut weight = Vec::new();
    for z in &axis {
        for y in &axis {
            for x in &axis {
                let px = [*x, *y, *z];
                if plausible(px) {
                    samples.extend(px);
                    weight.push(1.0);
                }
            }
        }
    }
    let scene = geomspace(0.003, 1.0, 9);
    for z in &scene {
        for y in &scene {
            for x in &scene {
                samples.extend(lin709_to_slog3([*x, *y, *z]));
                weight.push(1.0);
            }
        }
    }
    let hues = [0.01, 0.12, 0.45, 1.0];
    let scales = [1.6, 3.2, 6.4, 12.8];
    for z in hues {
        for y in hues {
            for x in hues {
                for scale in scales {
                    samples.extend(lin709_to_slog3([x * scale, y * scale, z * scale]));
                    weight.push(1.0);
                }
            }
        }
    }
    let skin = [[0.22, 0.11, 0.075], [0.48, 0.29, 0.21], [0.09, 0.04, 0.025]];
    for base in skin {
        for scale in geomspace(0.2, 5.0, 11) {
            samples.extend(lin709_to_slog3([base[0] * scale, base[1] * scale, base[2] * scale]));
            weight.push(6.0);
        }
    }
    for g in geomspace(0.00001, 16.0, 129) {
        let encoded = lin709_to_slog3([g, g, g]);
        samples.extend([encoded[0], encoded[0], encoded[0]]);
        weight.push(8.0);
    }
    (samples, weight)
}

fn geomspace(start: f64, end: f64, n: usize) -> Vec<f64> {
    let a = start.ln();
    let b = end.ln();
    (0..n).map(|i| (a + (b - a) * i as f64 / (n - 1) as f64).exp()).collect()
}

fn invert3(m: [[f64; 3]; 3]) -> [[f64; 3]; 3] {
    let det = m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
    let inv_det = 1.0 / det;
    [
        [(m[1][1] * m[2][2] - m[1][2] * m[2][1]) * inv_det, (m[0][2] * m[2][1] - m[0][1] * m[2][2]) * inv_det, (m[0][1] * m[1][2] - m[0][2] * m[1][1]) * inv_det],
        [(m[1][2] * m[2][0] - m[1][0] * m[2][2]) * inv_det, (m[0][0] * m[2][2] - m[0][2] * m[2][0]) * inv_det, (m[0][2] * m[1][0] - m[0][0] * m[1][2]) * inv_det],
        [(m[1][0] * m[2][1] - m[1][1] * m[2][0]) * inv_det, (m[0][1] * m[2][0] - m[0][0] * m[2][1]) * inv_det, (m[0][0] * m[1][1] - m[0][1] * m[1][0]) * inv_det],
    ]
}

fn linspace(n: usize) -> Vec<f64> {
    (0..n).map(|i| i as f64 / (n - 1) as f64).collect()
}

fn gamma_table(values: &[f64], profile: &Profile) -> Vec<u16> {
    let mut acc: f64 = 0.0;
    values
        .iter()
        .map(|v| {
            acc = acc.max(v.clamp(0.0, 1.0));
            round_code(acc * profile.gamma_max_val as f64) as u16
        })
        .collect()
}

fn matrix_from(first: &[[f64; 2]], profile: &Profile) -> [i16; 9] {
    let q = profile.matrix_q as f64;
    let mut out = [0i16; 9];
    for r in 0..3 {
        let a = round_code(first[r][0] * q) as i32;
        let b = round_code(first[r][1] * q) as i32;
        let c = profile.matrix_q as i32 - a - b;
        out[r * 3] = a as i16;
        out[r * 3 + 1] = b as i16;
        out[r * 3 + 2] = c as i16;
    }
    out
}

fn solve2(ata: [[f64; 2]; 2], atb: [f64; 2]) -> Option<[f64; 2]> {
    let det = ata[0][0] * ata[1][1] - ata[0][1] * ata[1][0];
    if det.abs() < 1e-12 {
        return None;
    }
    Some([
        (atb[0] * ata[1][1] - ata[0][1] * atb[1]) / det,
        (ata[0][0] * atb[1] - atb[0] * ata[1][0]) / det,
    ])
}

pub fn decompose(cube: &Cube, profile: &Profile) -> (OraclePreset, FitReport) {
    decompose_with(profile, &|u| sample_cube(cube, u), false, 0.0)
}

fn linear_to_srgb(x: f64) -> f64 {
    let x = x.clamp(0.0, 1.0);
    if x <= 0.003_130_8 {
        12.92 * x
    } else {
        1.055 * x.powf(1.0 / 2.4) - 0.055
    }
}

/// Movie variant fit: the 1D gamma is fitted in stock-EC value space, so the target cube (which
/// is display-referred sRGB) is sampled at `linear_to_srgb(x)` where `x` is the linear scene input
/// that the record-31 EC knot maps to each stock value.
pub fn decompose_movie(cube: &Cube, profile: &Profile, stock_ec: &[u32]) -> (OraclePreset, FitReport) {
    let knots = bionz_core::ec_knot_positions(&bionz_core::EC_RECORD31_EXPONENTS).expect("record 31 gives 96 knots");
    let n = stock_ec.len().min(96);
    if n < 2 {
        return decompose(cube, profile);
    }
    let mut value: Vec<f64> = stock_ec[..n].iter().map(|w| (((*w >> 6) as f64) / 1023.0).clamp(0.0, 1.0)).collect();
    for i in 1..n {
        if value[i] < value[i - 1] {
            value[i] = value[i - 1];
        }
    }
    let input: Vec<f64> = (0..n).map(|i| knots[i] as f64 / 8191.0).collect();
    let to_input = |u: f64| -> f64 {
        if u <= value[0] {
            return input[0];
        }
        if u >= value[n - 1] {
            return input[n - 1];
        }
        let mut i = 0usize;
        while i + 2 < n && value[i + 1] <= u {
            i += 1;
        }
        let mut j = i + 1;
        while j < n && value[j] == value[i] {
            j += 1;
        }
        if j >= n {
            return input[n - 1];
        }
        let t = ((u - value[i]) / (value[j] - value[i])).clamp(0.0, 1.0);
        input[i] + t * (input[j] - input[i])
    };
    let sample = move |u: &[f64]| {
        let mapped: Vec<f64> = u.iter().map(|q| linear_to_srgb(to_input(*q))).collect();
        sample_cube(cube, &mapped)
    };
    decompose_with(profile, &sample, false, 0.0)
}

pub fn decompose_with(
    profile: &Profile,
    sample: &dyn Fn(&[f64]) -> Vec<f64>,
    source_slog3: bool,
    edge_compression: f64,
) -> (OraclePreset, FitReport) {
    let (samples, weight) = if source_slog3 {
        source_samples()
    } else {
        let axis = linspace(13);
        let mut samples = Vec::new();
        for z in &axis {
            for y in &axis {
                for x in &axis {
                    samples.extend([*x, *y, *z]);
                }
            }
        }
        let n_grid = samples.len() / 3;
        for g in linspace(129) {
            samples.extend([g, g, g]);
        }
        let mut weight = vec![1.0; samples.len() / 3];
        for w in weight.iter_mut().skip(n_grid) {
            *w = 8.0;
        }
        (samples, weight)
    };
    let target = sample(&samples);
    let target_ycc = bionz_core::rgb_to_ycc(&target.iter().map(|v| v.clamp(0.0, 1.0)).collect::<Vec<_>>(), profile);
    let x = linspace(profile.gamma_size);
    let neutral_rgb: Vec<f64> = x.iter().flat_map(|v| [*v, *v, *v]).collect();
    let neutral_s = sample(&neutral_rgb);
    let mut neutral = Vec::new();
    for px in neutral_s.chunks(3) {
        neutral.push(((px[0] + px[1] + px[2]) / 3.0).clamp(0.0, 1.0));
    }
    let gamma0 = gamma_table(&neutral, profile);
    let preset0 = OraclePreset { gamma: gamma0.clone(), matrix: [0; 9], delta: vec![] };
    let g_codes = bionz_core::gamma_codes(&preset0, profile, &samples);
    let mut a_rows = Vec::new();
    let mut b_cols = [Vec::new(), Vec::new(), Vec::new()];
    for (i, px) in g_codes.chunks(3).enumerate() {
        let scale = profile.gamma_max_val as f64;
        let g0 = px[0] as f64 / scale;
        let g1 = px[1] as f64 / scale;
        let g2 = px[2] as f64 / scale;
        let unclipped = target[i * 3] > 0.005 && target[i * 3] < 0.995 && target[i * 3 + 1] > 0.005 && target[i * 3 + 1] < 0.995 && target[i * 3 + 2] > 0.005 && target[i * 3 + 2] < 0.995;
        let w = (weight[i] * if unclipped { 1.0f64 } else { 0.0 }).sqrt();
        a_rows.push([w * (g0 - g2), w * (g1 - g2)]);
        for c in 0..3 {
            b_cols[c].push(w * (target[i * 3 + c].clamp(0.0, 1.0) - g2));
        }
    }
    let mut coeff = [[0.0; 2]; 3];
    let mut rank_ok = true;
    for c in 0..3 {
        let mut ata = [[0.0; 2]; 2];
        let mut atb = [0.0; 2];
        for (row, &bi) in a_rows.iter().zip(&b_cols[c]) {
            for i in 0..2 {
                atb[i] += row[i] * bi;
                for j in 0..2 {
                    ata[i][j] += row[i] * row[j];
                }
            }
        }
        if let Some(sol) = solve2(ata, atb) {
            coeff[c] = sol;
        } else {
            rank_ok = false;
        }
    }
    if !rank_ok {
        coeff = [[1.0, 0.0], [0.0, 1.0], [0.0, 0.0]];
    }
    for row in &mut coeff {
        for v in row.iter_mut() {
            *v = v.clamp(-4.0, 4.0);
        }
    }
    let mut params = [0.0; 9];
    params[3] = coeff[0][0];
    params[4] = coeff[0][1];
    params[5] = coeff[1][0];
    params[6] = coeff[1][1];
    params[7] = coeff[2][0];
    params[8] = coeff[2][1];

    let candidate = |parameters: &[f64; 9]| -> (f64, Vec<u16>, [i16; 9]) {
        let mut curve = neutral.clone();
        let knots = [0.0, 0.25, 0.5, 0.75, 1.0];
        let vals = [0.0, parameters[0], parameters[1], parameters[2], 0.0];
        for (i, &xv) in x.iter().enumerate() {
            curve[i] += interp(xv, &knots, &vals);
        }
        let gamma = gamma_table(&curve, profile);
        let first = [
            [parameters[3], parameters[4]],
            [parameters[5], parameters[6]],
            [parameters[7], parameters[8]],
        ];
        let matrix = matrix_from(&first, profile);
        let preset = OraclePreset { gamma: gamma.clone(), matrix, delta: vec![] };
        let codes = matrix_codes(&preset, profile, &samples);
        let mid: Vec<f64> = codes.iter().map(|c| *c as f64 / profile.gamma_max_val as f64).collect();
        let mid_ycc = bionz_core::rgb_to_ycc(&mid, profile);
        let lo = profile.delta_range[0] as f64;
        let hi = profile.delta_range[1] as f64;
        let mut max_abs: f64 = 0.0;
        let mut excess: f64 = 0.0;
        let mut sse: f64 = 0.0;
        let mut count: f64 = 0.0;
        for i in 0..weight.len() {
            let sw = (weight[i] as f64).sqrt();
            for c in 0..3 {
                let residual = target_ycc[i * 3 + c] - mid_ycc[i * 3 + c];
                let err = residual * sw;
                max_abs = max_abs.max(err.abs());
                excess = excess.max((residual - hi).max(lo - residual).max(0.0));
                sse += err * err;
                count += 1.0;
            }
        }
        let cost = max_abs + 3.0 * excess + 0.15 * (sse / count).sqrt();
        (cost, gamma, matrix)
    };

    let mut best = candidate(&params);
    let scales = [0.45, 0.45, 0.45, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0];
    let mut directions = Vec::new();
    for i in 0..9 {
        let mut d = [0.0; 9];
        d[i] = 1.0;
        directions.push(d);
    }
    let mut coupled = Vec::new();
    for block in 0..3 {
        for col in 0..6 {
            let mut d = [0.0; 9];
            d[block] = 1.0;
            d[3 + col] = 1.0;
            coupled.push(d);
        }
    }
    directions.extend(coupled.iter().copied());
    for mut d in coupled {
        for value in d.iter_mut().skip(3) {
            *value = -*value;
        }
        directions.push(d);
    }
    for step in [0.040, 0.025, 0.012, 0.006, 0.003, 0.0015] {
        for direction in &directions {
            let mut change = [0.0; 9];
            for i in 0..9 {
                change[i] = step * direction[i] * scales[i];
            }
            let mut plus = params;
            let mut minus = params;
            for i in 0..9 {
                plus[i] += change[i];
                minus[i] -= change[i];
            }
            let a = candidate(&plus);
            let b = candidate(&minus);
            let (trial, sign_params) = if a.0 <= b.0 { (a, plus) } else { (b, minus) };
            if trial.0 < best.0 - 1e-9 {
                best = trial;
                params = sign_params;
            }
        }
    }
    let gamma = best.1;
    let matrix = best.2;
    let nodes = lut_nodes(profile);
    let preset_base = OraclePreset { gamma: gamma.clone(), matrix, delta: vec![0; profile.n_nodes() * 3] };
    let signal = signal_for_nodes(&nodes, &preset_base, profile);
    let reached = apply_ycc(&preset_base, profile, &signal);
    let target_nodes = sample(&signal);
    let target_nodes_ycc = bionz_core::rgb_to_ycc(
        &target_nodes.iter().map(|v| v.clamp(0.0, 1.0)).collect::<Vec<_>>(),
        profile,
    );
    let node_rgb = ycc_to_rgb(&nodes, profile);
    let mut seed = vec![0.0; profile.n_nodes() * 3];
    for i in 0..profile.n_nodes() {
        let px = &node_rgb[i * 3..i * 3 + 3];
        let oog = px.iter().any(|v| *v < -1e-6 || *v > 1.0 + 1e-6);
        if !oog {
            for c in 0..3 {
                seed[i * 3 + c] = target_nodes_ycc[i * 3 + c] - reached[i * 3 + c];
            }
        }
    }
    let delta = fit_nodes(&samples, &target_ycc, &weight, &seed, &preset_base, profile, edge_compression);
    let preset = OraclePreset { gamma, matrix, delta: delta.clone() };
    let report = score_with(sample, &preset, profile);
    (preset, report)
}

fn interp(x: f64, xp: &[f64], fp: &[f64]) -> f64 {
    if x <= xp[0] {
        return fp[0];
    }
    if x >= *xp.last().unwrap() {
        return *fp.last().unwrap();
    }
    let mut i = 0;
    while i + 1 < xp.len() && x >= xp[i + 1] {
        i += 1;
    }
    let t = (x - xp[i]) / (xp[i + 1] - xp[i]);
    fp[i] * (1.0 - t) + fp[i + 1] * t
}

fn lut_nodes(profile: &Profile) -> Vec<f64> {
    let mut nodes = Vec::new();
    for y in &profile.y_axis {
        for cb in &profile.c_axis {
            for cr in &profile.c_axis {
                nodes.extend([*y, *cb, *cr]);
            }
        }
    }
    nodes
}

fn signal_for_nodes(nodes: &[f64], preset: &OraclePreset, profile: &Profile) -> Vec<f64> {
    let rgb = ycc_to_rgb(nodes, profile);
    let inv = invert_q10(&preset.matrix, profile.matrix_q as f64);
    let x = linspace(profile.gamma_size);
    let g: Vec<f64> = preset.gamma.iter().enumerate().map(|(i, v)| *v as f64 / profile.gamma_max_val as f64 + i as f64 * 1e-10).collect();
    let mut signal = Vec::new();
    for px in rgb.chunks(3) {
        let clipped = [px[0].clamp(0.0, 1.0), px[1].clamp(0.0, 1.0), px[2].clamp(0.0, 1.0)];
        for c in 0..3 {
            let v = clipped[0] * inv[c][0] + clipped[1] * inv[c][1] + clipped[2] * inv[c][2];
            signal.push(interp(v, &g, &x));
        }
    }
    signal
}

fn invert_q10(matrix: &[i16; 9], q: f64) -> [[f64; 3]; 3] {
    let m = [
        [matrix[0] as f64 / q, matrix[1] as f64 / q, matrix[2] as f64 / q],
        [matrix[3] as f64 / q, matrix[4] as f64 / q, matrix[5] as f64 / q],
        [matrix[6] as f64 / q, matrix[7] as f64 / q, matrix[8] as f64 / q],
    ];
    let det = m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
    let inv_det = if det.abs() < 1e-12 { 0.0 } else { 1.0 / det };
    [
        [(m[1][1] * m[2][2] - m[1][2] * m[2][1]) * inv_det, (m[0][2] * m[2][1] - m[0][1] * m[2][2]) * inv_det, (m[0][1] * m[1][2] - m[0][2] * m[1][1]) * inv_det],
        [(m[1][2] * m[2][0] - m[1][0] * m[2][2]) * inv_det, (m[0][0] * m[2][2] - m[0][2] * m[2][0]) * inv_det, (m[0][2] * m[1][0] - m[0][0] * m[1][2]) * inv_det],
        [(m[1][0] * m[2][1] - m[1][1] * m[2][0]) * inv_det, (m[0][1] * m[2][0] - m[0][0] * m[2][1]) * inv_det, (m[0][0] * m[1][1] - m[0][1] * m[1][0]) * inv_det],
    ]
}

fn fit_nodes(samples: &[f64], target_ycc: &[f64], weight: &[f64], seed: &[f64], preset: &OraclePreset, profile: &Profile, edge_compression: f64) -> Vec<i8> {
    let codes = matrix_codes(preset, profile, samples);
    let mid: Vec<f64> = codes.iter().map(|c| *c as f64 / profile.gamma_max_val as f64).collect();
    let mid_ycc = bionz_core::rgb_to_ycc(&mid, profile);
    let lo = profile.delta_range[0] as f64;
    let hi = profile.delta_range[1] as f64;
    let n = profile.n_nodes();
    let ns = weight.len();
    let (blend, indices) = blend_matrix(&mid_ycc, profile);
    let mut degrees = vec![0.0; n];
    for s in 0..ns {
        for k in 0..8 {
            degrees[indices[s * 8 + k]] += blend[s * 8 + k] * weight[s];
        }
    }
    let seed = project(seed, lo, hi);
    let mut fitted = seed.clone();
    let mut raw = fitted.clone();
    for _ in 0..180 {
        let mut error = vec![0.0; ns * 3];
        for s in 0..ns {
            for c in 0..3 {
                let mut predicted = 0.0;
                for k in 0..8 {
                    predicted += blend[s * 8 + k] * fitted[indices[s * 8 + k] * 3 + c];
                }
                error[s * 3 + c] = target_ycc[s * 3 + c] - mid_ycc[s * 3 + c] - predicted;
            }
        }
        let mut correction = vec![0.0; n * 3];
        for s in 0..ns {
            for k in 0..8 {
                let node = indices[s * 8 + k];
                let gate = blend[s * 8 + k] * weight[s];
                for c in 0..3 {
                    correction[node * 3 + c] += gate * error[s * 3 + c];
                }
            }
        }
        for node in 0..n {
            let denom = degrees[node] + 0.25;
            for c in 0..3 {
                let i = node * 3 + c;
                raw[i] = fitted[i] + 0.8 * (correction[i] + 0.25 * (seed[i] - fitted[i])) / denom;
            }
        }
        fitted = project(&raw, lo, hi);
        if edge_compression > 0.0 {
            let nc = profile.c_axis.len();
            for node in 0..n {
                let cb = profile.c_axis[(node / nc) % nc].abs();
                let cr = profile.c_axis[node % nc].abs();
                let edge = cb.max(cr);
                let scale = 1.0 - edge_compression * ((edge - 64.0) / 64.0).clamp(0.0, 1.0);
                fitted[node * 3 + 1] *= scale;
                fitted[node * 3 + 2] *= scale;
            }
        }
    }
    inset_rail(&fitted, lo, hi)
        .iter()
        .map(|v| round_code(*v) as i8)
        .collect()
}

fn blend_matrix(mid_ycc: &[f64], profile: &Profile) -> (Vec<f64>, Vec<usize>) {
    let ny = profile.y_axis.len();
    let nc = profile.c_axis.len();
    let y_fp: Vec<f64> = (0..ny).map(|i| i as f64).collect();
    let c_fp: Vec<f64> = (0..nc).map(|i| i as f64).collect();
    let mut blend = Vec::new();
    let mut indices = Vec::new();
    for px in mid_ycc.chunks(3) {
        let fi = [
            interp(px[0], &profile.y_axis, &y_fp),
            interp(px[1], &profile.c_axis, &c_fp),
            interp(px[2], &profile.c_axis, &c_fp),
        ];
        let limit = [ny - 2, nc - 2, nc - 2];
        let base = [
            (fi[0] as usize).min(limit[0]),
            (fi[1] as usize).min(limit[1]),
            (fi[2] as usize).min(limit[2]),
        ];
        let frac = [fi[0] - base[0] as f64, fi[1] - base[1] as f64, fi[2] - base[2] as f64];
        for oy in 0..2 {
            for oc in 0..2 {
                for or_ in 0..2 {
                    let offsets = [oy, oc, or_];
                    let mut w = 1.0;
                    for axis in 0..3 {
                        w *= if offsets[axis] == 1 { frac[axis] } else { 1.0 - frac[axis] };
                    }
                    let index = (base[0] + oy) * nc * nc + (base[1] + oc) * nc + (base[2] + or_);
                    blend.push(w);
                    indices.push(index);
                }
            }
        }
    }
    (blend, indices)
}

fn inset_rail(fitted: &[f64], lo: f64, hi: f64) -> Vec<f64> {
    let mut out = Vec::with_capacity(fitted.len());
    for px in fitted.chunks(3) {
        let peak = px[1].abs().max(px[2].abs());
        let scale = if peak >= 62.0 { 61.0 / peak.max(1e-6) } else { 1.0 };
        let luma = if px[0] >= hi {
            hi - 1.0
        } else if px[0] <= lo {
            lo + 1.0
        } else {
            px[0]
        };
        out.extend([luma, px[1] * scale, px[2] * scale]);
    }
    out
}

fn project(raw: &[f64], lo: f64, hi: f64) -> Vec<f64> {
    let mut out = Vec::with_capacity(raw.len());
    for px in raw.chunks(3) {
        let mut demand: f64 = 1.0;
        for c in 1..3 {
            let d = if px[c] >= 0.0 { px[c] / hi } else { px[c] / lo };
            demand = demand.max(d);
        }
        let scale = demand.max(1.0);
        out.push(px[0].clamp(lo, hi));
        out.push(px[1] / scale);
        out.push(px[2] / scale);
    }
    out
}

#[cfg(test)]
fn score(cube: &Cube, preset: &OraclePreset, profile: &Profile) -> FitReport {
    score_with(&|u| sample_cube(cube, u), preset, profile)
}

fn score_with(sample: &dyn Fn(&[f64]) -> Vec<f64>, preset: &OraclePreset, profile: &Profile) -> FitReport {
    let axis = linspace(9);
    let mut u = Vec::new();
    for b in &axis {
        for g in &axis {
            for r in &axis {
                u.extend([*r, *g, *b]);
            }
        }
    }
    let reference = sample(&u);
    let ycc = apply_ycc(preset, profile, &u);
    let rgb = ycc_to_rgb(&ycc, profile);
    let mut de = Vec::new();
    for (a, b) in reference.chunks(3).zip(rgb.chunks(3)) {
        let la = lab(a);
        let lb = lab(&[b[0].clamp(0.0, 1.0), b[1].clamp(0.0, 1.0), b[2].clamp(0.0, 1.0)]);
        let d = (la[0] - lb[0]).hypot(la[1] - lb[1]).hypot(0.0) ;
        let dist = ((la[0] - lb[0]).powi(2) + (la[1] - lb[1]).powi(2) + (la[2] - lb[2]).powi(2)).sqrt();
        let _ = d;
        de.push(dist);
    }
    de.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let mean = de.iter().sum::<f64>() / de.len() as f64;
    let p95 = de[(de.len() as f64 * 0.95) as usize];
    let rails = preset.delta.iter().filter(|d| **d <= profile.delta_range[0] as i8 || **d >= profile.delta_range[1] as i8).count();
    FitReport {
        clamped_nodes: 0,
        at_rail_nodes: (rails / 3) as i32,
        mean_de76: mean,
        p95_de76: p95,
        max_de76: *de.last().unwrap_or(&0.0),
    }
}

fn lab(rgb: &[f64]) -> [f64; 3] {
    let lin = rgb.iter().map(|v| {
        let v = v.clamp(0.0, 1.0);
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recipe_probes_match_python() {
        let recipes: serde_json::Value = serde_json::from_str(include_str!("../../../../artifacts/tests/bionz-golden/recipes.json")).unwrap();
        let map = recipes.as_object().unwrap();
        for (_name, body) in map {
            let recipe: Recipe = serde_json::from_value(body["fields"].clone()).unwrap();
            let rgb: Vec<f64> = body["probe_rgb"].as_array().unwrap().iter().map(|v| v.as_f64().unwrap()).collect();
            let expect: Vec<f64> = body["probe_out"].as_array().unwrap().iter().map(|v| v.as_f64().unwrap()).collect();
            let got = evaluate_recipe(&rgb, &recipe);
            for (a, b) in got.iter().zip(&expect) {
                assert!((a - b).abs() < 1e-6, "{} {a} vs {b}", recipe.name);
            }
        }
    }

    #[test]
    fn tiny_cube_matches_python_decompose() {
        use bionz_preset::parse_cube;
        let mut text = String::from("TITLE \"golden-identity\"\nLUT_3D_SIZE 5\n");
        for b in 0..5 {
            for g in 0..5 {
                for r in 0..5 {
                    let rf = r as f64 / 4.0 * 0.9;
                    let gf = g as f64 / 4.0 * 0.95;
                    let bf = b as f64 / 4.0 * 0.85 + 0.02;
                    text.push_str(&format!("{rf:.8} {gf:.8} {bf:.8}\n"));
                }
            }
        }
        let cube = parse_cube(&text).unwrap();
        let (preset, _report) = decompose(&cube, &Profile::a6400());
        let golden: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../artifacts/tests/bionz-golden/decompose_tiny.json"
        ))
        .unwrap();
        let gamma: Vec<u16> = golden["gamma_u16"].as_array().unwrap().iter().map(|v| v.as_u64().unwrap() as u16).collect();
        let matrix: Vec<i16> = golden["matrix_i16"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap() as i16).collect();
        let delta: Vec<i8> = golden["delta_i8"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap() as i8).collect();
        assert_eq!(preset.gamma, gamma);
        assert_eq!(preset.matrix.as_slice(), matrix.as_slice());
        assert_eq!(preset.delta, delta);
    }

    #[test]
    fn recipe_fits_match_python_rails() {
        let recipes: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../artifacts/tests/bionz-golden/recipes.json"
        ))
        .unwrap();
        let fits: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../artifacts/tests/bionz-golden/recipe_fits.json"
        ))
        .unwrap();
        for (name, body) in recipes.as_object().unwrap() {
            let recipe: Recipe = serde_json::from_value(body["fields"].clone()).unwrap();
            let cube = recipe_cube(&recipe, 17);
            let profile = Profile::a6400();
            let (preset, report) = decompose(&cube, &profile);
            let golden = &fits[name];
            let gamma: Vec<u16> = golden["gamma_u16"].as_array().unwrap().iter().map(|v| v.as_u64().unwrap() as u16).collect();
            let matrix: Vec<i16> = golden["matrix_i16"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap() as i16).collect();
            let delta: Vec<i8> = golden["delta_i8"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap() as i8).collect();
            let golden_preset = OraclePreset {
                gamma,
                matrix: matrix.as_slice().try_into().unwrap(),
                delta,
            };
            let golden_score = score(&cube, &golden_preset, &profile);
            let rust_score = score(&cube, &preset, &profile);
            assert!(
                rust_score.mean_de76 <= golden_score.mean_de76 * 1.05 + 1e-6,
                "{name} mean {:.6} vs python {:.6} (Rust port drift over 5%)",
                rust_score.mean_de76,
                golden_score.mean_de76
            );
            assert_eq!(report.at_rail_nodes, 0, "{name} rails");
            let _ = golden_score;
        }
    }

    #[test]
    fn arri_rec709_is_not_worse_than_python() {
        let profile = Profile::a6400();
        let (preset, report) = decompose_with(&profile, &sample_arri_rec709, true, 0.0);
        let golden: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../artifacts/tests/bionz-golden/arri_rec709_fit.json"
        ))
        .unwrap();
        let gamma: Vec<u16> = golden["gamma_u16"].as_array().unwrap().iter().map(|v| v.as_u64().unwrap() as u16).collect();
        let matrix: Vec<i16> = golden["matrix_i16"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap() as i16).collect();
        let delta: Vec<i8> = golden["delta_i8"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap() as i8).collect();
        let golden_preset = OraclePreset { gamma, matrix: matrix.as_slice().try_into().unwrap(), delta };
        let golden_score = score_with(&sample_arri_rec709, &golden_preset, &profile);
        let rust_score = score_with(&sample_arri_rec709, &preset, &profile);
        assert!(
            rust_score.mean_de76 <= golden_score.mean_de76 + 1e-6,
            "ARRI mean {:.6} vs python {:.6}",
            rust_score.mean_de76,
            golden_score.mean_de76
        );
        assert_eq!(report.at_rail_nodes, golden["at_rail_nodes"].as_i64().unwrap() as i32);
    }

    #[test]
    fn movie_domain_fit_reproduces_the_neutral_ramp_better_than_a_photo_fit() {
        use bionz_core::ec_knot_positions;
        let path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../artifacts/analysis/arri-gamma-test-block-2026-11-16/record31-stock.bin");
        if !path.is_file() {
            return;
        }
        let bytes = std::fs::read(&path).unwrap();
        let stock_ec: Vec<u32> = (0..96)
            .map(|i| u32::from_le_bytes([bytes[i * 4], bytes[i * 4 + 1], bytes[i * 4 + 2], bytes[i * 4 + 3]]))
            .collect();
        let knots = ec_knot_positions(&bionz_core::EC_RECORD31_EXPONENTS).unwrap();
        let recipe = Recipe { name: "Gold 200".into(), shadow_toe: -0.06, mid_contrast: 1.10, crossover: 0.27, highlight_rolloff: 0.36, shoulder: 0.35, black_fade: 0.006, white_ceiling: 0.995, saturation: 1.08, warmth: 0.045, red_amber: 0.14, green_olive: 0.05, blue_cyan: 0.08, highlight_desat: 0.2, sky_luma: -0.07, sky_sat: -0.06, foliage_luma: 0.04, foliage_sat: 0.03, red_luma: 0.04, red_highlight_hue: 0.1, skin_protect: 0.88, shadow_warmth: 0.015, shadow_tint: 0.0, highlight_warmth: 0.038, highlight_tint: 0.0, monochrome: false, bw_weights: [0.3, 0.59, 0.11] };
        let cube = recipe_cube(&recipe, 17);
        let sample_gamma = |gamma: &[u16], x: f64| -> f64 {
            let pos = x.clamp(0.0, 1.0) * 95.0;
            let i = (pos.floor() as usize).min(94);
            let t = pos - i as f64;
            (gamma[i] as f64 * (1.0 - t) + gamma[i + 1] as f64 * t) / 4095.0
        };
        let neutral_error = |gamma: &[u16]| -> f64 {
            let composed: Vec<f64> = stock_ec.iter().map(|v| sample_gamma(gamma, ((*v >> 6) as f64) / 1023.0)).collect();
            let mut err = 0.0;
            for (i, out) in composed.iter().enumerate() {
                let s = linear_to_srgb(knots[i] as f64 / 8191.0);
                let target = sample_cube(&cube, &[s, s, s])[0];
                err += (target - out).abs();
            }
            err / 96.0
        };
        let (photo, _) = decompose(&cube, &Profile::a6400());
        let (movie, _) = decompose_movie(&cube, &Profile::a6400(), &stock_ec);
        let photo_error = neutral_error(&photo.gamma);
        let movie_error = neutral_error(&movie.gamma);
        println!("Gold 200 neutral error in the record-31 EC domain: photo fit {photo_error:.5}, movie fit {movie_error:.5}");
        assert!(movie_error < photo_error, "Movie fit {movie_error} should beat Photo fit {photo_error} in the record-31 EC domain");
        // Knot 62 is ~0.18 linear grey (1472 / 8191); composed Movie output must sit near mid-grey, not crushed.
        let mid_out = sample_gamma(&movie.gamma, ((stock_ec[62] >> 6) as f64) / 1023.0);
        assert!(mid_out > 0.35 && mid_out < 0.65, "Movie mid-grey output {mid_out:.3} should not be crushed");
    }

    #[test]
    fn arri_wide_is_not_worse_than_python() {
        let profile = Profile::a6400();
        let (preset, report) = decompose_with(&profile, &sample_arri_wide, true, 0.12);
        let golden: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../artifacts/tests/bionz-golden/arri_wide_fit.json"
        ))
        .unwrap();
        let gamma: Vec<u16> = golden["gamma_u16"].as_array().unwrap().iter().map(|v| v.as_u64().unwrap() as u16).collect();
        let matrix: Vec<i16> = golden["matrix_i16"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap() as i16).collect();
        let delta: Vec<i8> = golden["delta_i8"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap() as i8).collect();
        let golden_preset = OraclePreset { gamma, matrix: matrix.as_slice().try_into().unwrap(), delta };
        let golden_score = score_with(&sample_arri_wide, &golden_preset, &profile);
        let rust_score = score_with(&sample_arri_wide, &preset, &profile);
        assert!(
            rust_score.mean_de76 <= golden_score.mean_de76 + 1e-6,
            "wide mean {:.6} vs python {:.6}",
            rust_score.mean_de76,
            golden_score.mean_de76
        );
        assert_eq!(report.at_rail_nodes, golden["at_rail_nodes"].as_i64().unwrap() as i32);
    }

    #[test]
    fn filmic_curve_is_c1_monotone_and_anchored() {
        let recipes = [
            Recipe { name: "gold".into(), shadow_toe: -0.06, mid_contrast: 1.10, crossover: 0.27, highlight_rolloff: 0.36, shoulder: 0.35, black_fade: 0.006, white_ceiling: 0.995, saturation: 1.08, warmth: 0.045, red_amber: 0.14, green_olive: 0.05, blue_cyan: 0.08, highlight_desat: 0.2, sky_luma: -0.07, sky_sat: -0.06, foliage_luma: 0.04, foliage_sat: 0.03, red_luma: 0.04, red_highlight_hue: 0.1, skin_protect: 0.88, shadow_warmth: 0.015, shadow_tint: 0.0, highlight_warmth: 0.038, highlight_tint: 0.0, monochrome: false, bw_weights: [0.3, 0.59, 0.11] },
            Recipe { name: "eterna".into(), shadow_toe: 0.12, mid_contrast: 0.85, crossover: 0.28, highlight_rolloff: 0.65, shoulder: 0.55, black_fade: 0.014, white_ceiling: 0.965, ..Recipe::default_for_test() },
            Recipe { name: "chrome".into(), shadow_toe: -0.15, mid_contrast: 1.18, crossover: 0.28, highlight_rolloff: 0.44, black_fade: 0.004, white_ceiling: 0.99, ..Recipe::default_for_test() },
        ];
        for r in &recipes {
            let eps = 1e-5;
            let mut prev = filmic(0.0, r);
            for i in 1..=1000 {
                let x = i as f64 / 1000.0;
                let y = filmic(x, r);
                assert!(y >= prev - 1e-9, "{} not monotone at {x}", r.name);
                prev = y;
            }
            assert!((filmic(0.0, r) - r.black_fade).abs() < 1e-9);
            assert!((filmic(1.0, r) - r.white_ceiling).abs() < 1e-9);
            let xc = r.crossover.clamp(0.08, 0.80);
            let left = (filmic(xc, r) - filmic(xc - eps, r)) / eps;
            let right = (filmic(xc + eps, r) - filmic(xc, r)) / eps;
            assert!((left - right).abs() < 2e-3, "{} kink {left} vs {right}", r.name);
        }
    }

    #[test]
    fn arri_style_shoulder_is_c1_soft_and_keeps_highlight_gradation() {
        let r = Recipe { name: "shoulder".into(), shadow_toe: -0.1, mid_contrast: 1.1, crossover: 0.27, highlight_rolloff: 0.3, shoulder: 0.6, ..Recipe::default_for_test() };
        let flat = Recipe { shoulder: 0.0, ..r.clone() };
        let k = 0.55;
        let eps = 1e-5;
        let left = (filmic(k, &r) - filmic(k - eps, &r)) / eps;
        let right = (filmic(k + eps, &r) - filmic(k, &r)) / eps;
        assert!((left - right).abs() < 1e-3, "shoulder kink {left} vs {right}");
        assert!((filmic(1.0, &r) - 1.0).abs() < 1e-9);
        let slope = (filmic(0.99, &r) - filmic(0.99 - eps, &r)) / eps;
        let slope_flat = (filmic(0.99, &flat) - filmic(0.99 - eps, &flat)) / eps;
        assert!(slope < slope_flat, "shoulder must soften the top: {slope} vs {slope_flat}");
        let mut prev = filmic(0.0, &r);
        for i in 1..=1000 {
            let y = filmic(i as f64 / 1000.0, &r);
            assert!(y >= prev - 1e-12);
            prev = y;
        }
        let codes: std::collections::BTreeSet<i32> =
            (900..=1023).map(|i| (filmic(i as f64 / 1023.0, &r) * 1023.0).round() as i32).collect();
        assert!(codes.len() >= 40, "only {} distinct codes in the top 12%", codes.len());
    }

    #[test]
    fn look_matrix_preserves_neutral_gray_and_rows_sum_to_one() {        let recipe = Recipe { name: "warm".into(), warmth: 0.12, red_amber: 0.2, green_olive: 0.1, blue_cyan: 0.15, saturation: 1.1, ..Recipe::default_for_test() };
        let m = look_matrix(&recipe);
        for row in 0..3 {
            assert!((m[row][0] + m[row][1] + m[row][2] - 1.0).abs() < 1e-12);
        }
        let gray = [
            m[0][0] + m[0][1] + m[0][2],
            m[1][0] + m[1][1] + m[1][2],
            m[2][0] + m[2][1] + m[2][2],
        ];
        assert!((gray[0] - 1.0).abs() < 1e-12 && (gray[1] - 1.0).abs() < 1e-12 && (gray[2] - 1.0).abs() < 1e-12);
        // The old row normalisation cancelled this warmth; the new chain keeps it in the midtones.
        let out = evaluate_recipe(&[0.5, 0.5, 0.5], &recipe);
        assert!(out[0] > out[2], "warmth must raise R over B: {out:?}");
        let luma_in = 0.2126 * 0.5 + 0.7152 * 0.5 + 0.0722 * 0.5;
        let luma_out = 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2];
        assert!((luma_in - luma_out).abs() < 0.02, "midtone warmth must keep luma: {luma_in} vs {luma_out}");
    }
}

#[cfg(test)]
impl Recipe {
    fn default_for_test() -> Self {
        Recipe {
            name: String::new(), shadow_toe: 0.0, mid_contrast: 1.0, crossover: 0.28, highlight_rolloff: 0.0, shoulder: 0.0,
            black_fade: 0.0, white_ceiling: 1.0, saturation: 1.0, warmth: 0.0, red_amber: 0.0, green_olive: 0.0,
            blue_cyan: 0.0, highlight_desat: 0.0, sky_luma: 0.0, sky_sat: 0.0, foliage_luma: 0.0, foliage_sat: 0.0,
            red_luma: 0.0, red_highlight_hue: 0.0, skin_protect: 0.65, shadow_warmth: 0.0, shadow_tint: 0.0,
            highlight_warmth: 0.0, highlight_tint: 0.0, monochrome: false, bw_weights: [0.30, 0.59, 0.11],
        }
    }
}
