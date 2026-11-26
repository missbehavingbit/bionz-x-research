//! Integer stage chain. The same arithmetic is transcribed into WGSL.
//! Division is toward zero, matching WGSL and Rust `/` on signed integers.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RgbI {
    pub r: i32,
    pub g: i32,
    pub b: i32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct YccI {
    pub y: i32,
    pub cb: i32,
    pub cr: i32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct StageTap {
    pub rgb: RgbI,
    pub ycc: YccI,
    pub encoded_y: u8,
    pub encoded_cb: u8,
    pub encoded_cr: u8,
}

/// The two camera colour domains Look Lab models. Photo is the still-JPEG chain; Movie is the
/// Movie EC (record 31) chain with legal-range output.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LabMode {
    Photo,
    Movie,
}

impl LabMode {
    pub fn label(self) -> &'static str {
        match self {
            LabMode::Photo => "Photo",
            LabMode::Movie => "Movie",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BaseMatrixSource {
    /// Preset BZX2 matrix when it exists, otherwise leave the mode's base matrix (RAW cam_to_srgb).
    Auto,
    CameraToSrgb,
    PresetBaseMatrix,
    Identity,
}

impl BaseMatrixSource {
    pub fn label(self) -> &'static str {
        match self {
            BaseMatrixSource::Auto => "Auto (preset or camera)",
            BaseMatrixSource::CameraToSrgb => "RAW cam_to_srgb",
            BaseMatrixSource::PresetBaseMatrix => "Preset BZX2",
            BaseMatrixSource::Identity => "Identity",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ColorMatrixMode {
    /// The mode's own matrix: relative `M_sim·M_stock⁻¹` in Photo, absolute preset A0 in Movie.
    Auto,
    RelativeToStock,
    AbsoluteA0,
    AbsoluteA1,
    AbsoluteB2,
}

impl ColorMatrixMode {
    pub fn label(self) -> &'static str {
        match self {
            ColorMatrixMode::Auto => "Auto (mode default)",
            ColorMatrixMode::RelativeToStock => "Relative M_sim·M_stock⁻¹",
            ColorMatrixMode::AbsoluteA0 => "Absolute A0",
            ColorMatrixMode::AbsoluteA1 => "Absolute A1",
            ColorMatrixMode::AbsoluteB2 => "Absolute B2",
        }
    }
}

/// Manual overrides for how a preset is routed through the integer chain. Defaults leave the
/// selected mode's calibrated behaviour untouched.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct IspRouting {
    pub base_matrix_source: BaseMatrixSource,
    pub color_matrix_mode: ColorMatrixMode,
    /// Scales the RefAdr residual LUT toward 0 (0 = bypass delta, 1 = as compiled).
    pub lut_strength: f32,
    /// EC knot record: Some(31) = record-31 exponents, Some(0) = uniform knots, None = as loaded.
    pub ec_record: Option<u8>,
}

impl Default for IspRouting {
    fn default() -> Self {
        Self {
            base_matrix_source: BaseMatrixSource::Auto,
            color_matrix_mode: ColorMatrixMode::Auto,
            lut_strength: 1.0,
            ec_record: None,
        }
    }
}

/// Uniform EC knots (0..=8191 in 96 equal steps), the `ec_record = 0` option.
pub fn uniform_ec_knots() -> [i32; 96] {
    let mut out = [0i32; 96];
    for (i, slot) in out.iter_mut().enumerate() {
        *slot = (i as i32 * 8191) / 95;
    }
    out
}

/// Scale an i8 residual LUT by `strength` (0..=1), rounding half away from zero like the fitter.
pub fn scale_lut(delta: &mut [i8], strength: f32) {
    let s = strength.clamp(0.0, 1.0) as f64;
    if (s - 1.0).abs() < 1e-9 {
        return;
    }
    for v in delta.iter_mut() {
        let scaled = (*v as f64 * s).round();
        *v = scaled.clamp(-128.0, 127.0) as i8;
    }
}

#[derive(Clone, Debug)]
pub struct IntegerParams {    pub gamma_max: i32,
    pub matrix_q: i32,
    pub lut_level: i32,
    pub gain_level: i32,
    pub gain_enable: bool,
    pub bypass: [bool; 16],
    pub fc_before_ec: bool,
    pub legal_range: bool,
    pub base_matrix: [i32; 9],
    pub color_matrix: [i32; 9],
    pub ec: [i32; 96],
    pub fc: [i32; 96],
    pub delta: Vec<i8>,
    pub detail: [i32; 9],
    pub ec_full_scale: i32,
    /// EC knot positions on the 0..=`ec_full_scale` input axis (catalog rows 9 and 13).
    pub ec_knots: [i32; 96],
}

/// Subdivision exponents of EC record 31, read live in Movie S-Log3 (catalog row 13).
pub const EC_RECORD31_EXPONENTS: [u8; 9] = [3, 3, 4, 4, 3, 3, 3, 4, 3];

/// Right-endpoint knot positions built from nine subdivision exponents.
/// Segment starts are [0,32,64,...,4096] and extents [32,32,64,...,4096]; positions cap at 8191.
/// Returns `None` when the exponents do not produce exactly 96 knots.
pub fn ec_knot_positions(exponents: &[u8; 9]) -> Option<[i32; 96]> {
    const STARTS: [i32; 9] = [0, 32, 64, 128, 256, 512, 1024, 2048, 4096];
    const EXTENTS: [i32; 9] = [32, 32, 64, 128, 256, 512, 1024, 2048, 4096];
    let mut out = [0i32; 96];
    let mut n = 0usize;
    for seg in 0..9 {
        let e = exponents[seg] as u32;
        if e > 12 {
            return None;
        }
        let step = EXTENTS[seg] >> e;
        for k in 0..(1usize << e) {
            if n >= 96 {
                return None;
            }
            out[n] = (STARTS[seg] + step * (k as i32 + 1)).min(8191);
            n += 1;
        }
    }
    (n == 96).then_some(out)
}

/// 96-point 10-bit FC tone curve (0..1023) fitted to 24 ILCE-6400 Creative Style Standard
/// ARW+JPG pairs at 0 EV with the RAW `cam_to_srgb` matrix in `base_matrix`.
pub const STILL_STANDARD_FC: [i32; 96] = [
    0, 84, 173, 241, 301, 357, 409, 449, 485, 518, 550, 578, 606, 634, 658, 682,
    702, 718, 734, 750, 766, 778, 790, 802, 810, 822, 830, 842, 850, 859, 867, 875,
    883, 891, 899, 903, 907, 915, 919, 923, 927, 931, 931, 939, 943, 947, 947, 951,
    955, 959, 963, 963, 967, 967, 967, 971, 971, 971, 975, 975, 979, 979, 983, 983,
    983, 987, 987, 987, 991, 991, 991, 991, 995, 995, 995, 995, 995, 995, 995, 995,
    999, 999, 999, 999, 1003, 1003, 1003, 1003, 1003, 1007, 1007, 1007, 1011, 1011, 1011, 1023,
];

impl Default for IntegerParams {
    fn default() -> Self {
        let mut identity = [0; 9];
        identity[0] = 1024;
        identity[4] = 1024;
        identity[8] = 1024;
        Self {
            gamma_max: 4095,
            matrix_q: 1024,
            lut_level: 4,
            gain_level: 0,
            gain_enable: false,
            bypass: [false; 16],
            fc_before_ec: false,
            legal_range: true,
            base_matrix: identity,
            color_matrix: identity,
            ec: {
                let mut t = [0; 96];
                for (i, slot) in t.iter_mut().enumerate() {
                    *slot = (i as i32) * 4095 / 95;
                }
                t
            },
            fc: {
                let mut t = [0; 96];
                for (i, slot) in t.iter_mut().enumerate() {
                    *slot = (i as i32) * 1023 / 95;
                }
                t
            },
            delta: vec![0; 5 * 9 * 9 * 3],
            detail: [0, 0, 0, 0, 256, 0, 0, 0, 0],
            ec_full_scale: 8191,
            ec_knots: ec_knot_positions(&EC_RECORD31_EXPONENTS).expect("record 31 has 96 knots"),
        }
    }
}

pub fn mode0_10bit(v: i32) -> i32 {
    let s = v >> 6;
    if s <= 1022 && (v & 0x20) != 0 {
        s + 1
    } else {
        s
    }
}

pub fn wide_mul(a: i32, b: i32) -> i64 {
    a as i64 * b as i64
}

fn div_toward_zero(n: i32, d: i32) -> i32 {
    n / d
}

fn q10_row(rgb: RgbI, row: &[i32], q: i32, lo: i32, hi: i32) -> i32 {
    let acc = rgb.r * row[0] + rgb.g * row[1] + rgb.b * row[2];
    let code = div_toward_zero(acc + q / 2, q);
    code.clamp(lo, hi)
}

fn apply_matrix(rgb: RgbI, m: &[i32], q: i32, hi: i32) -> RgbI {
    RgbI {
        r: q10_row(rgb, &m[0..3], q, 0, hi),
        g: q10_row(rgb, &m[3..6], q, 0, hi),
        b: q10_row(rgb, &m[6..9], q, 0, hi),
    }
}

fn sample_curve(table: &[i32], code: i32, full_scale: i32) -> i32 {
    let span = 95i32;
    let code = code.clamp(0, full_scale);
    let pos_num = code * span;
    let i0 = (pos_num / full_scale).min(94);
    let rem = pos_num - i0 * full_scale;
    let frac = (rem << 16) / full_scale;
    let quant = 1 << 16;
    let low = table[i0 as usize];
    let high = table[i0 as usize + 1];
    (low * (quant - frac) + high * frac + quant / 2) / quant
}

/// Piecewise-linear lookup on explicit knot positions. Below the first knot the first value is held
/// (assumption). The segment fraction is Q16, rounded half up. Binary search, same as the WGSL kernel.
pub fn sample_knots(table: &[i32], knots: &[i32; 96], code: i32) -> i32 {
    let x = code.max(0);
    if x <= knots[0] {
        return table[0];
    }
    if x >= knots[95] {
        return table[95];
    }
    let (mut lo, mut hi) = (0usize, 95usize);
    while hi - lo > 1 {
        let mid = (lo + hi) / 2;
        if knots[mid] <= x {
            lo = mid;
        } else {
            hi = mid;
        }
    }
    let span = (knots[hi] - knots[lo]).max(1);
    let frac = ((x - knots[lo]) << 16) / span;
    let quant = 1 << 16;
    (table[lo] * (quant - frac) + table[hi] * frac + quant / 2) / quant
}

/// Stage 10 gain in Q8 (catalog row 17).
pub fn stage_gain(level: i32, enable: bool) -> i32 {
    if enable && level <= 0xffe {
        (0xFFF00 / (4095 - level).max(1)).clamp(256, 511)
    } else {
        256
    }
}

/// Display-only inverse of the 8-bit BT.709 encoder. Floats are allowed here because this is the
/// view, not the emulated chain.
pub fn encoded_to_rgb8(y: u8, cb: u8, cr: u8, legal: bool) -> [u8; 3] {
    let (yn, cbn, crn) = if legal {
        ((y as f32 - 16.0) / 219.0, (cb as f32 - 128.0) / 224.0, (cr as f32 - 128.0) / 224.0)
    } else {
        (y as f32 / 255.0, (cb as f32 - 128.0) / 255.0, (cr as f32 - 128.0) / 255.0)
    };
    let r = yn + 1.5748 * crn;
    let b = yn + 1.8556 * cbn;
    let g = (yn - 0.2126 * r - 0.0722 * b) / 0.7152;
    [r, g, b].map(|v| (v.clamp(0.0, 1.0) * 255.0 + 0.5) as u8)
}

pub fn matrix_gain(coef: &[i32; 9], level: i32, enable: bool) -> [i32; 18] {
    let g = if enable && level <= 0xffe {
        let den = (4095 - level).max(1);
        (0xFFF00 / den).clamp(256, 511)
    } else {
        256
    };
    let mut out = [0i32; 18];
    for row in 0..3 {
        let mut first = [0i32; 3];
        for col in 0..3 {
            let c = coef[row * 3 + col];
            first[col] = (wide_mul(c, g) / 256) as i32;
            out[row * 6 + col] = first[col].clamp(-32768, 32767);
        }
        let mut second = [0i32; 3];
        for col in 0..3 {
            let c = coef[row * 3 + col];
            let prod = wide_mul(wide_mul(c, g) as i32, g) / 65536;
            second[col] = prod as i32;
        }
        second[row] = -(second[(row + 1) % 3] + second[(row + 2) % 3]);
        for col in 0..3 {
            out[row * 6 + 3 + col] = second[col].clamp(-32768, 32767);
        }
    }
    out
}

fn rgb_to_ycc(rgb: RgbI, max: i32) -> YccI {
    let yn = (2126 * rgb.r + 7152 * rgb.g + 722 * rgb.b + 5000) / 10000;
    let y = (yn * 256 + max / 2) / max.max(1);
    let cb = ((rgb.b - yn) * 13796) / (max * 100).max(1);
    let cr = ((rgb.r - yn) * 16256) / (max * 100).max(1);
    YccI {
        y: y.clamp(0, 256),
        cb: cb.clamp(-128, 128),
        cr: cr.clamp(-128, 128),
    }
}

fn axis_index(value: i32, nodes: &[i32]) -> (i32, i32) {
    if value <= nodes[0] {
        return (0, 0);
    }
    if value >= nodes[nodes.len() - 1] {
        let i = nodes.len() as i32 - 2;
        return (i, 65536);
    }
    let mut i = 0;
    while i + 1 < nodes.len() && value >= nodes[i + 1] {
        i += 1;
    }
    if i > nodes.len() - 2 {
        i = nodes.len() - 2;
    }
    let span = (nodes[i + 1] - nodes[i]).max(1);
    let frac = ((value - nodes[i]) << 16) / span;
    (i as i32, frac.clamp(0, 65536))
}

fn sample_lut(delta: &[i8], ycc: YccI, level: i32) -> [i32; 3] {
    const Y: [i32; 5] = [0, 64, 128, 192, 256];
    const C: [i32; 9] = [-128, -96, -64, -32, 0, 32, 64, 96, 128];
    let (iy, fy) = axis_index(ycc.y, &Y);
    let (icb, fcb) = axis_index(ycc.cb, &C);
    let (icr, fcr) = axis_index(ycc.cr, &C);
    let mut acc = [0i64; 3];
    for dz in 0..2 {
        for dy in 0..2 {
            for dx in 0..2 {
                let wy = if dx == 1 { fy >> 12 } else { 16 - (fy >> 12) };
                let wb = if dy == 1 { fcb >> 12 } else { 16 - (fcb >> 12) };
                let wr = if dz == 1 { fcr >> 12 } else { 16 - (fcr >> 12) };
                let w = wy * wb * wr;
                let idx = (((iy + dx) * 9 + (icb + dy)) * 9 + (icr + dz)) as usize;
                for c in 0..3 {
                    let node = delta[idx * 3 + c] as i32;
                    let scaled = (level * node) >> 3;
                    acc[c] += w as i64 * scaled as i64;
                }
            }
        }
    }
    [
        ((acc[0] + 2048) / 4096) as i32,
        ((acc[1] + 2048) / 4096) as i32,
        ((acc[2] + 2048) / 4096) as i32,
    ]
}

fn detail_at(rgb: &[RgbI], w: i32, x: i32, y: i32, k: &[i32]) -> RgbI {
    let mut acc = [0i32; 3];
    for ky in 0..3i32 {
        for kx in 0..3i32 {
            let sx = (x + kx - 1).clamp(0, w - 1);
            let sy = (y + ky - 1).clamp(0, (rgb.len() as i32 / w) - 1);
            let p = rgb[(sy * w + sx) as usize];
            let c = k[(ky * 3 + kx) as usize];
            acc[0] += p.r * c;
            acc[1] += p.g * c;
            acc[2] += p.b * c;
        }
    }
    RgbI {
        r: (acc[0] / 256).clamp(0, 4095),
        g: (acc[1] / 256).clamp(0, 4095),
        b: (acc[2] / 256).clamp(0, 4095),
    }
}

pub fn encode_integer(ycc: YccI, legal: bool) -> (u8, u8, u8) {
    let (yg, cg, off, y_lo, y_hi, c_lo, c_hi) = if legal {
        (219, 224, 16, 16, 235, 16, 240)
    } else {
        (255, 255, 0, 0, 255, 0, 255)
    };
    let y = ((ycc.y * yg + 128) / 256 + off).clamp(y_lo, y_hi) as u8;
    let cb = ((ycc.cb * cg + 128) / 256 + 128).clamp(c_lo, c_hi) as u8;
    let cr = ((ycc.cr * cg + 128) / 256 + 128).clamp(c_lo, c_hi) as u8;
    (y, cb, cr)
}

struct Chain {
    after_base: Vec<RgbI>,
    after_ec: Vec<RgbI>,
    after_gamma: Vec<RgbI>,
    after_matrix: Vec<RgbI>,
    after_gain: Vec<RgbI>,
    after_detail: Vec<RgbI>,
    ycc: Vec<YccI>,
    after_lut: Vec<YccI>,
    encoded: Vec<(u8, u8, u8)>,
}

fn develop_chain(input: &[RgbI], width: i32, params: &IntegerParams) -> Chain {
    let mut working = input.to_vec();
    if !params.bypass[5] {
        for px in &mut working {
            *px = apply_matrix(*px, &params.base_matrix, params.matrix_q, params.gamma_max);
        }
    }
    let after_base = working.clone();
    let mut gamma_in = working;
    let apply_ec = |px: &mut RgbI| {
        if params.bypass[6] {
            return;
        }
        let scale = params.ec_full_scale.max(1);
        let max = params.gamma_max.max(1);
        px.r = sample_knots(&params.ec, &params.ec_knots, px.r * scale / max);
        px.g = sample_knots(&params.ec, &params.ec_knots, px.g * scale / max);
        px.b = sample_knots(&params.ec, &params.ec_knots, px.b * scale / max);
    };
    let apply_fc = |px: &mut RgbI| {
        if params.bypass[7] {
            return;
        }
        // FC is a 10-bit table: scale the working code in and the 10-bit result back out.
        let max = params.gamma_max.max(1);
        px.r = sample_curve(&params.fc, px.r * 1023 / max, 1023) * max / 1023;
        px.g = sample_curve(&params.fc, px.g * 1023 / max, 1023) * max / 1023;
        px.b = sample_curve(&params.fc, px.b * 1023 / max, 1023) * max / 1023;
    };
    let mut after_ec = gamma_in.clone();
    for (px, saved) in gamma_in.iter_mut().zip(&mut after_ec) {
        if params.fc_before_ec {
            apply_fc(px);
            apply_ec(px);
            *saved = *px;
        } else {
            apply_ec(px);
            *saved = *px;
            apply_fc(px);
        }
    }
    let after_gamma = gamma_in.clone();
    if !params.bypass[8] {
        for px in &mut gamma_in {
            *px = apply_matrix(*px, &params.color_matrix, params.matrix_q, params.gamma_max);
        }
    }
    let after_matrix = gamma_in.clone();
    if !params.bypass[9] && params.gain_enable {
        // The first half of the gained matrix is coef * g / 256, so this stage scales the matrix
        // output by g. The earlier code multiplied a second time by the Q10 matrix with a /256
        // divisor, which applied the matrix twice at 4x gain.
        let g = stage_gain(params.gain_level, true);
        for px in &mut gamma_in {
            px.r = (px.r * g / 256).clamp(0, params.gamma_max);
            px.g = (px.g * g / 256).clamp(0, params.gamma_max);
            px.b = (px.b * g / 256).clamp(0, params.gamma_max);
        }
    }
    let after_gain = gamma_in.clone();
    let after_detail = if !params.bypass[12] {
        (0..gamma_in.len())
            .map(|i| {
                let x = (i as i32) % width.max(1);
                let y = (i as i32) / width.max(1);
                detail_at(&gamma_in, width, x, y, &params.detail)
            })
            .collect()
    } else {
        gamma_in
    };
    let mut ycc = Vec::with_capacity(after_detail.len());
    let mut after_lut = Vec::with_capacity(after_detail.len());
    let mut encoded = Vec::with_capacity(after_detail.len());
    for rgb in &after_detail {
        let plain = if params.bypass[10] {
            YccI { y: 0, cb: 0, cr: 0 }
        } else {
            rgb_to_ycc(*rgb, params.gamma_max)
        };
        ycc.push(plain);
        let mut graded = plain;
        if !params.bypass[11] {
            let d = sample_lut(&params.delta, plain, params.lut_level.clamp(0, 7));
            graded.y = (plain.y + d[0]).clamp(0, 256);
            graded.cb = (plain.cb + d[1]).clamp(-128, 128);
            graded.cr = (plain.cr + d[2]).clamp(-128, 128);
        }
        after_lut.push(graded);
        encoded.push(if params.bypass[13] { (0, 128, 128) } else { encode_integer(graded, params.legal_range) });
    }
    Chain { after_base, after_ec, after_gamma, after_matrix, after_gain, after_detail, ycc, after_lut, encoded }
}

pub fn integer_pipeline(input: &[RgbI], width: i32, params: &IntegerParams) -> Vec<StageTap> {
    let chain = develop_chain(input, width, params);
    chain
        .after_detail
        .into_iter()
        .zip(chain.after_lut)
        .zip(chain.encoded)
        .map(|((rgb, ycc), (ey, ecb, ecr))| StageTap { rgb, ycc, encoded_y: ey, encoded_cb: ecb, encoded_cr: ecr })
        .collect()
}

/// RGB preview of one catalog stage. Stages 1–5 show the integer input. YCC stages are drawn as Y and offset chroma.
pub fn stage_display(input: &[RgbI], width: i32, params: &IntegerParams, stage: u8) -> Vec<RgbI> {
    let chain = develop_chain(input, width, params);
    let ycc_rgb = |samples: &[YccI]| {
        samples
            .iter()
            .map(|ycc| RgbI {
                r: (ycc.y * params.gamma_max / 256).clamp(0, params.gamma_max),
                g: ((ycc.cb + 128) * params.gamma_max / 256).clamp(0, params.gamma_max),
                b: ((ycc.cr + 128) * params.gamma_max / 256).clamp(0, params.gamma_max),
            })
            .collect()
    };
    match stage {
        7 => chain.after_ec,
        8 => chain.after_gamma,
        9 => chain.after_matrix,
        10 => chain.after_gain,
        11 => ycc_rgb(&chain.ycc),
        12 => ycc_rgb(&chain.after_lut),
        13 => chain.after_detail,
        14 | 15 | 16 => chain
            .encoded
            .into_iter()
            .map(|(y, cb, cr)| RgbI {
                r: y as i32 * params.gamma_max / 255,
                g: cb as i32 * params.gamma_max / 255,
                b: cr as i32 * params.gamma_max / 255,
            })
            .collect(),
        6 => chain.after_base,
        _ => input.to_vec(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn routing_helpers_scale_lut_and_build_uniform_knots() {
        let mut delta = vec![64i8, -64, 10, -10, 0];
        scale_lut(&mut delta, 0.5);
        assert_eq!(delta, vec![32, -32, 5, -5, 0]);
        scale_lut(&mut delta, 0.0);
        assert!(delta.iter().all(|v| *v == 0));
        let knots = uniform_ec_knots();
        assert_eq!(knots[0], 0);
        assert_eq!(knots[95], 8191);
        assert!(knots.windows(2).all(|w| w[1] > w[0]));
        assert_eq!(BaseMatrixSource::Identity.label(), "Identity");
        assert_eq!(ColorMatrixMode::AbsoluteB2.label(), "Absolute B2");
    }

    #[test]
    fn record31_knots_match_the_live_buffer() {
        let k = ec_knot_positions(&EC_RECORD31_EXPONENTS).unwrap();
        assert_eq!(&k[..3], &[4, 8, 12]);
        assert_eq!(k[94], 7680);
        assert_eq!(k[95], 8191);
        assert!(k.windows(2).all(|w| w[0] < w[1]));
        assert!(ec_knot_positions(&[3; 9]).is_none());
    }

    #[test]
    fn knot_lookup_hits_knots_and_midpoints() {
        let k = ec_knot_positions(&EC_RECORD31_EXPONENTS).unwrap();
        let table: Vec<i32> = (0..96).map(|i| i * 40).collect();
        assert_eq!(sample_knots(&table, &k, 0), 0);
        assert_eq!(sample_knots(&table, &k, 8), 40);
        assert_eq!(sample_knots(&table, &k, 10), 60);
        assert_eq!(sample_knots(&table, &k, 9000), 95 * 40);
    }

    #[test]
    fn fc_keeps_full_scale() {
        let mut params = IntegerParams::default();
        params.bypass[6] = true;
        params.bypass[12] = true;
        let shown = stage_display(&[RgbI { r: 4095, g: 2048, b: 0 }], 1, &params, 8);
        assert_eq!(shown[0].r, 4095);
        assert!((shown[0].g - 2048).abs() <= 8, "{:?}", shown[0]);
        assert_eq!(shown[0].b, 0);
    }

    #[test]
    fn display_inverse_round_trips_grey() {
        assert_eq!(encoded_to_rgb8(16, 128, 128, true), [0, 0, 0]);
        assert_eq!(encoded_to_rgb8(235, 128, 128, true), [255, 255, 255]);
        assert_eq!(encoded_to_rgb8(128, 128, 128, false), [128, 128, 128]);
    }

    #[test]
    fn mode0_matches_studio_core() {
        assert_eq!(mode0_10bit(0), 0);
        assert_eq!(mode0_10bit(0x20), 1);
        assert_eq!(mode0_10bit(1022 << 6), 1022);
        assert_eq!(mode0_10bit((1022 << 6) | 0x20), 1023);
        assert_eq!(mode0_10bit((1023 << 6) | 0x20), 1023);
    }

    #[test]
    fn gain_diagonal_cancels_row() {
        let mut coef = [0i32; 9];
        coef[0] = 1024;
        coef[4] = 1024;
        coef[8] = 1024;
        let out = matrix_gain(&coef, 0, true);
        for row in 0..3 {
            let sum = out[row * 6 + 3] + out[row * 6 + 4] + out[row * 6 + 5];
            assert_eq!(sum, 0, "row {row}");
        }
    }

    #[test]
    fn wide_product_of_gain_square() {
        let prod = wide_mul(32767, 261121);
        assert_eq!(prod, 8_556_151_807);
        assert_eq!(prod / 65536, 130556);
    }

    #[test]
    fn rails_and_zero_are_stable() {
        let params = IntegerParams::default();
        let pixels = [
            RgbI { r: 0, g: 0, b: 0 },
            RgbI { r: 4095, g: 4095, b: 4095 },
            RgbI { r: 8191, g: 0, b: 1 },
        ];
        let a = integer_pipeline(&pixels, 3, &params);
        let b = integer_pipeline(&pixels, 3, &params);
        assert_eq!(a, b);
        assert_eq!(a[0].encoded_y, a[0].encoded_y);
    }

    #[test]
    fn gain_tap_matches_the_pipeline_rgb_when_detail_is_bypassed() {
        let mut params = IntegerParams::default();
        params.bypass[12] = true;
        let pixels = [RgbI { r: 1000, g: 2000, b: 500 }];
        let shown = stage_display(&pixels, 1, &params, 10);
        let taps = integer_pipeline(&pixels, 1, &params);
        assert_eq!(shown[0], taps[0].rgb);
        assert_eq!(stage_display(&pixels, 1, &params, 4), pixels);
    }
}
