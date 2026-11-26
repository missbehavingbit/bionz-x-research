//! Dehancer-derived post-gamma Halation -> Film Grain stages for a6400 Photo Mode.
//!
//! Operates strictly on gamma-encoded `[0..255]` (`[0..1]`) sRGB/display frames in
//! **Halation -> Film Grain** order using precomputed `u8[256]` 1D LUTs, downsampled
//! separable box blurs, and a deterministic seamless `256x256` grain tile. No floats,
//! `pow()`, `exp()`, or 3D procedural noise run inside the per-pixel loops.

use std::sync::OnceLock;

/// Preset profiles matching `artifacts/Halation film grain and film simulation/halation/index_film.json`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum HalationPreset {
    Mm8,
    Mm8NoRemjet,
    Mm16,
    Mm16NoRemjet,
    Mm35,
    Mm35NoRemjet,
    Mm65,
}

impl HalationPreset {
    pub const ALL: [HalationPreset; 7] = [
        HalationPreset::Mm8,
        HalationPreset::Mm8NoRemjet,
        HalationPreset::Mm16,
        HalationPreset::Mm16NoRemjet,
        HalationPreset::Mm35,
        HalationPreset::Mm35NoRemjet,
        HalationPreset::Mm65,
    ];

    pub fn label(self) -> &'static str {
        match self {
            HalationPreset::Mm8 => "8 mm / Super 8",
            HalationPreset::Mm8NoRemjet => "8 mm / Super 8 No Remjet",
            HalationPreset::Mm16 => "16 mm / Super 16",
            HalationPreset::Mm16NoRemjet => "16 mm / Super 16 No Remjet",
            HalationPreset::Mm35 => "35 mm / Super 35",
            HalationPreset::Mm35NoRemjet => "35 mm / Super 35 No Remjet",
            HalationPreset::Mm65 => "65 mm / 70 mm",
        }
    }

    pub fn to_params(self, enabled: bool) -> HalationParams {
        let (low, high, local_diff, global_diff, power, hue, blue_comp, opacity) = match self {
            HalationPreset::Mm8 => (15.0, 80.0, 25.0, 45.0, 70.0, 50.0, 100.0, 80.0),
            HalationPreset::Mm8NoRemjet => (0.0, 80.0, 45.0, 75.0, 80.0, 50.0, 100.0, 80.0),
            HalationPreset::Mm16 => (15.0, 80.0, 20.0, 35.0, 70.0, 50.0, 0.0, 80.0),
            HalationPreset::Mm16NoRemjet => (0.0, 80.0, 35.0, 55.0, 80.0, 50.0, 0.0, 80.0),
            HalationPreset::Mm35 => (15.0, 80.0, 15.0, 25.0, 70.0, 50.0, 100.0, 54.0),
            HalationPreset::Mm35NoRemjet => (0.0, 80.0, 25.0, 35.0, 80.0, 50.0, 100.0, 80.0),
            HalationPreset::Mm65 => (15.0, 80.0, 10.0, 20.0, 70.0, 50.0, 100.0, 70.0),
        };
        let scattering = if matches!(self, HalationPreset::Mm35) {
            0.60
        } else {
            (power / 100.0) * 0.85
        };
        HalationParams {
            enabled,
            low: low / 100.0,
            high: high / 100.0,
            local_diffusion: local_diff / 100.0,
            global_diffusion: global_diff / 100.0,
            scattering,
            scattering_amp: 1.0 + (hue - 50.0) / 100.0,
            defringe: blue_comp / 100.0,
            impact: opacity / 100.0,
            mask_mode: false,
        }
    }
}

/// Parameters for the post-gamma Halation stage.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct HalationParams {
    pub enabled: bool,
    pub low: f32,
    pub high: f32,
    pub local_diffusion: f32,
    pub global_diffusion: f32,
    pub scattering: f32,
    pub scattering_amp: f32,
    pub defringe: f32,
    pub impact: f32,
    pub mask_mode: bool,
}

impl Default for HalationParams {
    fn default() -> Self {
        HalationPreset::Mm35.to_params(false)
    }
}

/// Preset profiles matching `artifacts/Halation film grain and film simulation/grain/index_film.json`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum GrainPreset {
    Mm8Iso50,
    Mm8Iso250,
    Mm8Iso500,
    Mm16Iso50,
    Mm16Iso250,
    Mm16Iso500,
    Mm35Iso50,
    Mm35Iso250,
    Mm35Iso500,
    Mm65Iso50,
}

impl GrainPreset {
    pub const ALL: [GrainPreset; 10] = [
        GrainPreset::Mm8Iso50,
        GrainPreset::Mm8Iso250,
        GrainPreset::Mm8Iso500,
        GrainPreset::Mm16Iso50,
        GrainPreset::Mm16Iso250,
        GrainPreset::Mm16Iso500,
        GrainPreset::Mm35Iso50,
        GrainPreset::Mm35Iso250,
        GrainPreset::Mm35Iso500,
        GrainPreset::Mm65Iso50,
    ];

    pub fn label(self) -> &'static str {
        match self {
            GrainPreset::Mm8Iso50 => "8 mm ISO 50",
            GrainPreset::Mm8Iso250 => "8 mm ISO 250",
            GrainPreset::Mm8Iso500 => "8 mm ISO 500",
            GrainPreset::Mm16Iso50 => "16 mm ISO 50",
            GrainPreset::Mm16Iso250 => "16 mm ISO 250",
            GrainPreset::Mm16Iso500 => "16 mm ISO 500",
            GrainPreset::Mm35Iso50 => "35 mm ISO 50",
            GrainPreset::Mm35Iso250 => "35 mm ISO 250",
            GrainPreset::Mm35Iso500 => "35 mm ISO 500",
            GrainPreset::Mm65Iso50 => "65 mm ISO 50",
        }
    }

    pub fn to_params(self, enabled: bool, seed: u32) -> GrainParams {
        let (scale, amount, shadows, midtones, highlights, color) = match self {
            GrainPreset::Mm8Iso50 => (40.0, 25.0, 30.0, 50.0, 50.0, 50.0),
            GrainPreset::Mm8Iso250 => (48.0, 50.0, 25.0, 45.0, 65.0, 65.0),
            GrainPreset::Mm8Iso500 => (48.0, 70.0, 30.0, 45.0, 65.0, 90.0),
            GrainPreset::Mm16Iso50 => (25.0, 25.0, 30.0, 55.0, 65.0, 65.0),
            GrainPreset::Mm16Iso250 => (25.0, 45.0, 30.0, 55.0, 65.0, 65.0),
            GrainPreset::Mm16Iso500 => (25.0, 70.0, 30.0, 45.0, 65.0, 75.0),
            GrainPreset::Mm35Iso50 => (8.0, 30.0, 30.0, 55.0, 65.0, 65.0),
            GrainPreset::Mm35Iso250 => (1.0, 20.0, 30.0, 55.0, 65.0, 65.0),
            GrainPreset::Mm35Iso500 => (16.0, 40.0, 35.0, 55.0, 65.0, 70.0),
            GrainPreset::Mm65Iso50 => (1.0, 10.0, 30.0, 45.0, 55.0, 65.0),
        };
        GrainParams {
            enabled,
            amount: amount / 100.0,
            scale,
            shadows: shadows / 100.0,
            midtones: midtones / 100.0,
            highlights: highlights / 100.0,
            color: color / 100.0,
            seed,
        }
    }
}

/// Parameters for the post-gamma Film Grain stage.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct GrainParams {
    pub enabled: bool,
    pub amount: f32,
    pub scale: f32,
    pub shadows: f32,
    pub midtones: f32,
    pub highlights: f32,
    pub color: f32,
    pub seed: u32,
}

impl Default for GrainParams {
    fn default() -> Self {
        GrainPreset::Mm35Iso250.to_params(false, 1)
    }
}

/// Combined post-gamma Halation -> Film Grain configuration.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct FilmPostParams {
    pub halation: HalationParams,
    pub grain: GrainParams,
}

impl FilmPostParams {
    pub fn is_active(&self) -> bool {
        (self.halation.enabled && (self.halation.impact > 0.0 || self.halation.mask_mode))
            || (self.grain.enabled && self.grain.amount > 0.0)
    }
}

#[inline(always)]
fn mix_u8(a: u8, b: u8, t: u8) -> u8 {
    let t256 = (t as i32) + ((t as i32) >> 7);
    ((a as i32 * (256 - t256) + b as i32 * t256 + 128) >> 8) as u8
}

#[inline(always)]
fn mul_u8(a: u8, b: u8) -> u8 {
    let b256 = (b as u32) + ((b as u32) >> 7);
    ((a as u32 * b256 + 128) >> 8) as u8
}

#[inline(always)]
fn blend_overlay_u8(base: u8, blend: u8) -> u8 {
    if base <= 127 {
        let v = 2 * (base as u32) * (blend as u32);
        ((v * 257 + 32768) >> 16).min(255) as u8
    } else {
        let inv = 2 * (255 - base as u32) * (255 - blend as u32);
        255 - (((inv * 257 + 32768) >> 16).min(255) as u8)
    }
}

fn to_u8(v: f32) -> u8 {
    (v.clamp(0.0, 1.0) * 255.0).round() as u8
}

fn smoothstep(edge0: f32, edge1: f32, x: f32) -> f32 {
    if edge1 <= edge0 {
        return if x >= edge1 { 1.0 } else { 0.0 };
    }
    let t = ((x - edge0) / (edge1 - edge0)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

struct HalationLuts {
    mask: [u8; 256],
    contrast: [u8; 256],
    alpha_pow: [u8; 256],
    red_impact: [u8; 256],
    scat_r: [u8; 256],
    scat_g: [u8; 256],
    scat_b: [u8; 256],
}

fn build_halation_luts(p: &HalationParams) -> HalationLuts {
    let low = p.low.clamp(0.0, 1.0);
    let high = p.high.clamp(0.0, 1.0);
    let scattering = p.scattering.clamp(0.0, 1.0);
    let amp = p.scattering_amp.clamp(0.5, 3.0);
    let exp_alpha = ((1.87 - scattering / 1.8) / 1.2).max(0.05);
    let exp_scat_r = (0.20 * amp).max(0.02);

    let mut luts = HalationLuts {
        mask: [0; 256],
        contrast: [0; 256],
        alpha_pow: [0; 256],
        red_impact: [0; 256],
        scat_r: [0; 256],
        scat_g: [0; 256],
        scat_b: [0; 256],
    };
    for i in 0..256 {
        let x = i as f32 / 255.0;
        luts.mask[i] = to_u8(1.0 - smoothstep(low, high, x));
        luts.contrast[i] = to_u8(smoothstep(low, 1.0, x));
        luts.alpha_pow[i] = to_u8(x.powf(exp_alpha));
        luts.red_impact[i] = to_u8(x.powf(2.4));
        luts.scat_r[i] = to_u8(x.powf(exp_scat_r));
        luts.scat_g[i] = to_u8(x.powf(0.95));
        luts.scat_b[i] = to_u8(x.powf(1.10));
    }
    luts
}

/// 2-pass separable horizontal + vertical sliding-window box blur on tightly packed RGB bytes.
fn box_blur_rgb(src: &[u8], dst: &mut [u8], tmp: &mut [u8], w: usize, h: usize, radius: usize) {
    if radius == 0 || w == 0 || h == 0 {
        dst.copy_from_slice(src);
        return;
    }
    let win = (2 * radius + 1) as i32;
    let half = win / 2;
    let r_i = radius as isize;
    let max_x = (w - 1) as isize;
    let max_y = (h - 1) as isize;

    // Horizontal pass: src -> tmp
    for y in 0..h {
        let row = y * w * 3;
        let mut sr = 0i32;
        let mut sg = 0i32;
        let mut sb = 0i32;
        for dx in -r_i..=r_i {
            let cx = dx.clamp(0, max_x) as usize;
            let idx = row + cx * 3;
            sr += src[idx] as i32;
            sg += src[idx + 1] as i32;
            sb += src[idx + 2] as i32;
        }
        for x in 0..w {
            let o = row + x * 3;
            tmp[o] = ((sr + half) / win) as u8;
            tmp[o + 1] = ((sg + half) / win) as u8;
            tmp[o + 2] = ((sb + half) / win) as u8;

            let left = (x as isize - r_i).clamp(0, max_x) as usize;
            let right = (x as isize + r_i + 1).clamp(0, max_x) as usize;
            let li = row + left * 3;
            let ri = row + right * 3;
            sr += src[ri] as i32 - src[li] as i32;
            sg += src[ri + 1] as i32 - src[li + 1] as i32;
            sb += src[ri + 2] as i32 - src[li + 2] as i32;
        }
    }

    // Vertical pass: tmp -> dst
    let stride = w * 3;
    for x in 0..w {
        let col = x * 3;
        let mut sr = 0i32;
        let mut sg = 0i32;
        let mut sb = 0i32;
        for dy in -r_i..=r_i {
            let cy = dy.clamp(0, max_y) as usize;
            let idx = cy * stride + col;
            sr += tmp[idx] as i32;
            sg += tmp[idx + 1] as i32;
            sb += tmp[idx + 2] as i32;
        }
        for y in 0..h {
            let o = y * stride + col;
            dst[o] = ((sr + half) / win) as u8;
            dst[o + 1] = ((sg + half) / win) as u8;
            dst[o + 2] = ((sb + half) / win) as u8;

            let top = (y as isize - r_i).clamp(0, max_y) as usize;
            let bot = (y as isize + r_i + 1).clamp(0, max_y) as usize;
            let ti = top * stride + col;
            let bi = bot * stride + col;
            sr += tmp[bi] as i32 - tmp[ti] as i32;
            sg += tmp[bi + 1] as i32 - tmp[ti + 1] as i32;
            sb += tmp[bi + 2] as i32 - tmp[ti + 2] as i32;
        }
    }
}

#[derive(Clone, Copy)]
struct InterpCoord {
    i0: usize,
    i1: usize,
    w: u32, // 0..=256
}

fn build_upsample_coords(full_len: usize, small_len: usize) -> Vec<InterpCoord> {
    let mut out = Vec::with_capacity(full_len);
    if small_len <= 1 {
        out.resize(full_len, InterpCoord { i0: 0, i1: 0, w: 0 });
        return out;
    }
    let max_i = (small_len - 1) as i64;
    let fl = full_len as i64;
    let sl = small_len as i64;
    for i in 0..full_len {
        let pos_q8 = (((2 * i as i64 + 1) * sl * 128) / fl) - 128;
        let clamped = pos_q8.clamp(0, max_i * 256);
        let i0 = (clamped >> 8) as usize;
        let i1 = (i0 + 1).min(small_len - 1);
        let w = (clamped & 255) as u32;
        out.push(InterpCoord { i0, i1, w });
    }
    out
}

/// Apply Dehancer-adapted Halation in-place on an RGBA8 image buffer.
pub fn apply_halation_rgba(rgba: &mut [u8], width: usize, height: usize, params: &HalationParams) {
    if !params.enabled || width == 0 || height == 0 || rgba.len() < width * height * 4 {
        return;
    }
    if params.impact <= 0.0 && !params.mask_mode {
        return;
    }

    let luts = build_halation_luts(params);
    let max_dim = width.max(height);
    let ds = max_dim.div_ceil(750).max(1);
    let dw = width.div_ceil(ds).max(1);
    let dh = height.div_ceil(ds).max(1);

    let mut ds_contrast = vec![0u8; dw * dh * 3];
    if ds == 1 {
        for i in 0..(width * height) {
            let p = i * 4;
            let d = i * 3;
            ds_contrast[d] = luts.contrast[rgba[p] as usize];
            ds_contrast[d + 1] = luts.contrast[rgba[p + 1] as usize];
            ds_contrast[d + 2] = luts.contrast[rgba[p + 2] as usize];
        }
    } else {
        for dy in 0..dh {
            let y0 = dy * ds;
            let y1 = (y0 + ds).min(height);
            for dx in 0..dw {
                let x0 = dx * ds;
                let x1 = (x0 + ds).min(width);
                let count = ((y1 - y0) * (x1 - x0)) as u32;
                let half = count / 2;
                let mut sr = 0u32;
                let mut sg = 0u32;
                let mut sb = 0u32;
                for y in y0..y1 {
                    let row = y * width * 4;
                    for x in x0..x1 {
                        let p = row + x * 4;
                        sr += luts.contrast[rgba[p] as usize] as u32;
                        sg += luts.contrast[rgba[p + 1] as usize] as u32;
                        sb += luts.contrast[rgba[p + 2] as usize] as u32;
                    }
                }
                let d = (dy * dw + dx) * 3;
                ds_contrast[d] = ((sr + half) / count) as u8;
                ds_contrast[d + 1] = ((sg + half) / count) as u8;
                ds_contrast[d + 2] = ((sb + half) / count) as u8;
            }
        }
    }

    let dw_max = dw.max(dh) as f32;
    let local_diff = params.local_diffusion.clamp(0.0, 1.0);
    let global_diff = params.global_diffusion.clamp(0.0, 1.0);
    let r_local = if local_diff > 0.0 {
        (local_diff * 24.0 * (dw_max / 1080.0)).round().max(1.0) as usize
    } else {
        0
    };
    let r_global = if global_diff > 0.0 {
        ((global_diff * 72.0 * (dw_max / 1080.0)).round() as usize).max(r_local + 1)
    } else {
        r_local
    };

    let mut resampled = vec![0u8; dw * dh * 3];
    let mut halation = vec![0u8; dw * dh * 3];
    let mut scratch = vec![0u8; dw * dh * 3];
    box_blur_rgb(&ds_contrast, &mut resampled, &mut scratch, dw, dh, r_local);
    box_blur_rgb(&ds_contrast, &mut halation, &mut scratch, dw, dh, r_global);

    let x_coords = build_upsample_coords(width, dw);
    let y_coords = build_upsample_coords(height, dh);
    let scattering_u8 = to_u8(params.scattering);
    let defringe_u8 = to_u8(params.defringe);
    let impact_u8 = to_u8(params.impact);
    let dw3 = dw * 3;

    for (y, yc) in y_coords.iter().enumerate() {
        let wy = yc.w;
        let iwy = 256 - wy;
        let row0 = yc.i0 * dw3;
        let row1 = yc.i1 * dw3;
        let dst_row = y * width * 4;

        for (x, xc) in x_coords.iter().enumerate() {
            let wx = xc.w;
            let iwx = 256 - wx;
            let w00 = iwx * iwy;
            let w10 = wx * iwy;
            let w01 = iwx * wy;
            let w11 = wx * wy;

            let p00 = row0 + xc.i0 * 3;
            let p10 = row0 + xc.i1 * 3;
            let p01 = row1 + xc.i0 * 3;
            let p11 = row1 + xc.i1 * 3;

            let ir_r = ((resampled[p00] as u32 * w00 + resampled[p10] as u32 * w10 + resampled[p01] as u32 * w01 + resampled[p11] as u32 * w11 + 32768) >> 16) as u8;
            let ir_g = ((resampled[p00 + 1] as u32 * w00 + resampled[p10 + 1] as u32 * w10 + resampled[p01 + 1] as u32 * w01 + resampled[p11 + 1] as u32 * w11 + 32768) >> 16) as u8;
            let ir_b = ((resampled[p00 + 2] as u32 * w00 + resampled[p10 + 2] as u32 * w10 + resampled[p01 + 2] as u32 * w01 + resampled[p11 + 2] as u32 * w11 + 32768) >> 16) as u8;

            let h_r = ((halation[p00] as u32 * w00 + halation[p10] as u32 * w10 + halation[p01] as u32 * w01 + halation[p11] as u32 * w11 + 32768) >> 16) as u8;
            let h_g = ((halation[p00 + 1] as u32 * w00 + halation[p10 + 1] as u32 * w10 + halation[p01 + 1] as u32 * w01 + halation[p11 + 1] as u32 * w11 + 32768) >> 16) as u8;
            let h_b = ((halation[p00 + 2] as u32 * w00 + halation[p10 + 2] as u32 * w10 + halation[p01 + 2] as u32 * w01 + halation[p11 + 2] as u32 * w11 + 32768) >> 16) as u8;

            let dst_idx = dst_row + x * 4;
            let in_r = rgba[dst_idx];
            let in_g = rgba[dst_idx + 1];
            let in_b = rgba[dst_idx + 2];

            let c_r = ir_r.max(h_r).max(in_r);
            let c_g = ir_g.max(h_g).max(in_g);
            let c_b = ir_b.max(h_b).max(in_b);

            let mut a_r = c_r - in_r;
            let mut a_g = c_g - in_g;
            let a_b = c_b - in_b;

            if (a_r | a_g | a_b) == 0 {
                if params.mask_mode {
                    rgba[dst_idx] = 0;
                    rgba[dst_idx + 1] = 0;
                    rgba[dst_idx + 2] = 0;
                }
                continue;
            }

            a_r = (((a_r as u32 * 307 + 128) >> 8).min(255) as u8).max(a_g);
            a_g = (((a_r as u32 * 228 + 128) >> 8) as u8).min(a_g);

            let ap_r = luts.alpha_pow[a_r as usize];
            let ap_g = luts.alpha_pow[a_g as usize];
            let ap_b = luts.alpha_pow[a_b as usize];

            let m_r = luts.mask[in_r as usize] as u32;
            let m_g = luts.mask[in_g as usize] as u32;
            let m_b = luts.mask[in_b as usize] as u32;
            let m_lum = ((77 * m_r + 150 * m_g + 29 * m_b + 128) >> 8) as u8;

            let mut res_r = mix_u8(in_r, in_r.saturating_add(ap_r), m_lum);
            let mut res_g = mix_u8(in_g, in_g.saturating_add(ap_g), m_lum);
            let mut res_b = mix_u8(in_b, in_b.saturating_add(ap_b), m_lum);

            let y_scat = ((162 * in_r as u32 + 47 * in_g as u32 + 47 * in_b as u32 + 128) >> 8) as u8;
            let scat_mask = mul_u8(y_scat, scattering_u8);
            let red_impact = luts.red_impact[res_r as usize];

            let sr = mix_u8(res_r, luts.scat_r[res_r as usize], scat_mask);
            let sg = mix_u8(res_g, luts.scat_g[res_g as usize], scat_mask);
            let sb = mix_u8(res_b, luts.scat_b[res_b as usize], scat_mask);

            res_r = mix_u8(res_r, sr, red_impact);
            res_g = mix_u8(res_g, sg, red_impact);
            res_b = mix_u8(res_b, sb, red_impact);

            let fin_r = res_r.saturating_sub(in_r);
            let mut fin_g = res_g.saturating_sub(in_g);
            let fin_b = res_b.saturating_sub(in_b);
            let damp_g = ((187 * fin_r.min(fin_g) as u32 + 128) >> 8) as u8;
            fin_g = mix_u8(damp_g, fin_g, fin_r);

            res_r = in_r.saturating_add(fin_r);
            res_g = in_g.saturating_add(fin_g);
            res_b = in_b.saturating_add(fin_b);

            if defringe_u8 > 0 {
                let df = mul_u8(defringe_u8, fin_r);
                res_b = mix_u8(res_b, 0, df);
            }

            res_r = mix_u8(in_r, res_r, impact_u8);
            res_g = mix_u8(in_g, res_g, impact_u8);
            res_b = mix_u8(in_b, res_b, impact_u8);

            if params.mask_mode {
                res_r = res_r.saturating_sub(in_r);
                res_g = res_g.saturating_sub(in_g);
                res_b = res_b.saturating_sub(in_b);
            }

            rgba[dst_idx] = res_r;
            rgba[dst_idx + 1] = res_g;
            rgba[dst_idx + 2] = res_b;
        }
    }
}

#[inline(always)]
fn hash2d(x: u32, y: u32) -> u32 {
    let mut h = x.wrapping_mul(0x9E37_79B1) ^ y.wrapping_mul(0x85EB_CA77) ^ 0xC2B2_AE3D;
    h ^= h >> 16;
    h = h.wrapping_mul(0x7FEB_352D);
    h ^= h >> 15;
    h = h.wrapping_mul(0x846C_A68B);
    h ^= h >> 16;
    h
}

/// Deterministic `256x256` seamless clustered film-grain tile centered around 128 (`[24..232]`).
pub fn grain_tile_256() -> &'static [u8; 65536] {
    static TILE: OnceLock<[u8; 65536]> = OnceLock::new();
    TILE.get_or_init(|| {
        let mut raw = vec![0i32; 65536];
        for y in 0..256usize {
            let cy0 = (y >> 1) as u32;
            let cy1 = ((y >> 1) + 1) as u32 & 127;
            let fy = (y & 1) as i32;
            for x in 0..256usize {
                let cx0 = (x >> 1) as u32;
                let cx1 = ((x >> 1) + 1) as u32 & 127;
                let fx = (x & 1) as i32;

                let fine = ((hash2d(x as u32, y as u32) >> 24) as i32) - 128;

                let c00 = ((hash2d(cx0, cy0) >> 24) as i32) - 128;
                let c10 = ((hash2d(cx1, cy0) >> 24) as i32) - 128;
                let c01 = ((hash2d(cx0, cy1) >> 24) as i32) - 128;
                let c11 = ((hash2d(cx1, cy1) >> 24) as i32) - 128;
                let c0 = c00 * (2 - fx) + c10 * fx;
                let c1 = c01 * (2 - fx) + c11 * fx;
                let coarse = (c0 * (2 - fy) + c1 * fy + 2) >> 2;

                raw[y * 256 + x] = (fine * 2 + coarse) / 3;
            }
        }

        // Apply one 3x3 wrap-around box filter (resampler neighborhood) and amplify to [24..232].
        let mut filtered = vec![0i32; 65536];
        let mut sum_all = 0i64;
        for y in 0..256usize {
            for x in 0..256usize {
                let mut s = 0i32;
                for dy in [-1isize, 0, 1] {
                    let yy = ((y as isize + dy) & 255) as usize;
                    for dx in [-1isize, 0, 1] {
                        let xx = ((x as isize + dx) & 255) as usize;
                        s += raw[yy * 256 + xx];
                    }
                }
                // Scale filtered signal by 18 / 9 = 2.0 (restores variance after 3x3 smoothing).
                let val = (s * 11) / 36;
                filtered[y * 256 + x] = val;
                sum_all += val as i64;
            }
        }
        let mean = (sum_all / 65536) as i32;

        let mut tile = [128u8; 65536];
        for (i, &v) in filtered.iter().enumerate() {
            tile[i] = (128 + v - mean).clamp(24, 232) as u8;
        }
        tile
    })
}

struct GrainLuts {
    pow08: [u8; 256],
    shd_w: [u8; 256],
    mid_w: [u8; 256],
    hi_w: [u8; 256],
}

fn build_grain_luts(p: &GrainParams) -> GrainLuts {
    let amount = p.amount.clamp(0.0, 1.0);
    let shd = p.shadows.clamp(0.0, 1.0);
    let mid = p.midtones.clamp(0.0, 1.0);
    let hi = p.highlights.clamp(0.0, 1.0);

    let mut luts = GrainLuts {
        pow08: [0; 256],
        shd_w: [0; 256],
        mid_w: [0; 256],
        hi_w: [0; 256],
    };
    for i in 0..256 {
        let luma = i as f32 / 255.0;
        let g_shd = (-0.5 * (luma / 0.2).powi(2)).exp();
        let g_mid = (-0.5 * ((luma - 0.5) / 0.2).powi(2)).exp();
        let g_hi = (-0.5 * ((luma - 1.0) / 0.2).powi(2)).exp();
        luts.pow08[i] = to_u8(luma.powf(0.8));
        luts.shd_w[i] = to_u8(shd * amount * 2.0 * g_shd);
        luts.mid_w[i] = to_u8(mid * amount * g_mid);
        luts.hi_w[i] = to_u8(hi * amount * 2.0 * g_hi);
    }
    luts
}

#[inline(always)]
fn sample_tile_bilinear(tile: &[u8; 65536], x_q8: u32, y_q8: u32) -> u8 {
    let x0 = ((x_q8 >> 8) & 255) as usize;
    let x1 = (x0 + 1) & 255;
    let fx = x_q8 & 255;
    let ifx = 256 - fx;

    let y0 = ((y_q8 >> 8) & 255) as usize;
    let y1 = (y0 + 1) & 255;
    let fy = y_q8 & 255;
    let ify = 256 - fy;

    let r0 = y0 << 8;
    let r1 = y1 << 8;
    let c00 = tile[r0 + x0] as u32;
    let c10 = tile[r0 + x1] as u32;
    let c01 = tile[r1 + x0] as u32;
    let c11 = tile[r1 + x1] as u32;

    let top = c00 * ifx + c10 * fx;
    let bot = c01 * ifx + c11 * fx;
    ((top * ify + bot * fy + 32768) >> 16) as u8
}

/// Apply Dehancer-adapted Film Grain in-place on an RGBA8 image buffer.
pub fn apply_grain_rgba(rgba: &mut [u8], width: usize, height: usize, params: &GrainParams) {
    if !params.enabled || params.amount <= 0.0 || width == 0 || height == 0 || rgba.len() < width * height * 4 {
        return;
    }

    let tile = grain_tile_256();
    let luts = build_grain_luts(params);
    let color_u8 = to_u8(params.color);

    let eff_scale = 1.0 + (params.scale.clamp(1.0, 48.0) - 1.0) * 0.06;
    let raw_step = 65536.0 * (5200.0 / (width.max(1) as f32)) / eff_scale * 0.45;
    let step_q16 = raw_step.round().clamp(4096.0, (128 * 65536) as f32) as u32;

    let seed_h = hash2d(params.seed, params.seed.wrapping_mul(0x9E37_79B9));
    let ox_q16 = (seed_h & 0x00FF_FFFF) as u32;
    let oy_q16 = ((seed_h >> 8) & 0x00FF_FFFF) as u32;

    const OFF_GX_Q8: u32 = 97 << 8;
    const OFF_GY_Q8: u32 = 173 << 8;
    const OFF_BX_Q8: u32 = 193 << 8;
    const OFF_BY_Q8: u32 = 59 << 8;

    let mut x_q8_lut = Vec::with_capacity(width);
    let mut ax = ox_q16;
    for _ in 0..width {
        x_q8_lut.push(ax >> 8);
        ax = ax.wrapping_add(step_q16);
    }

    let mut ay = oy_q16;
    for y in 0..height {
        let y_q8 = ay >> 8;
        let row = y * width * 4;
        for (x, &x_q8) in x_q8_lut.iter().enumerate() {
            let p = row + x * 4;
            let in_r = rgba[p];
            let in_g = rgba[p + 1];
            let in_b = rgba[p + 2];

            let g_r = sample_tile_bilinear(tile, x_q8, y_q8);
            let (g_g, g_b) = if color_u8 > 0 {
                let raw_g = sample_tile_bilinear(tile, x_q8.wrapping_add(OFF_GX_Q8), y_q8.wrapping_add(OFF_GY_Q8));
                let raw_b = sample_tile_bilinear(tile, x_q8.wrapping_add(OFF_BX_Q8), y_q8.wrapping_add(OFF_BY_Q8));
                (mix_u8(g_r, raw_g, color_u8), mix_u8(g_r, raw_b, color_u8))
            } else {
                (g_r, g_r)
            };

            // Rec.709 luma = (54*R + 183*G + 18*B + 128) >> 8
            let luma = ((54 * in_r as u32 + 183 * in_g as u32 + 18 * in_b as u32 + 128) >> 8).min(255) as usize;
            let ws = luts.shd_w[luma];
            let wm = luts.mid_w[luma];
            let wh = luts.hi_w[luma];

            let mut r = in_r;
            let mut g = in_g;
            let mut b = in_b;

            if ws > 0 {
                r = mix_u8(r, blend_overlay_u8(luts.pow08[r as usize], g_r), ws);
                g = mix_u8(g, blend_overlay_u8(luts.pow08[g as usize], g_g), ws);
                b = mix_u8(b, blend_overlay_u8(luts.pow08[b as usize], g_b), ws);
            }
            if wm > 0 {
                r = mix_u8(r, blend_overlay_u8(r, g_r), wm);
                g = mix_u8(g, blend_overlay_u8(g, g_g), wm);
                b = mix_u8(b, blend_overlay_u8(b, g_b), wm);
            }
            if wh > 0 {
                r = mix_u8(r, blend_overlay_u8(r.saturating_sub(51), g_r), wh);
                g = mix_u8(g, blend_overlay_u8(g.saturating_sub(51), g_g), wh);
                b = mix_u8(b, blend_overlay_u8(b.saturating_sub(51), g_b), wh);
            }

            // 50% normal blend with original input (`blend_normal(inColor, float4(rgb, 0.5))`).
            rgba[p] = ((in_r as u16 + r as u16 + 1) >> 1) as u8;
            rgba[p + 1] = ((in_g as u16 + g as u16 + 1) >> 1) as u8;
            rgba[p + 2] = ((in_b as u16 + b as u16 + 1) >> 1) as u8;
        }
        ay = ay.wrapping_add(step_q16);
    }
}

/// Apply the post-gamma pipeline in strict **Halation -> Film Grain** order.
pub fn apply_film_post(rgba: &mut [u8], width: usize, height: usize, params: &FilmPostParams) {
    if params.halation.enabled {
        apply_halation_rgba(rgba, width, height, &params.halation);
    }
    if params.grain.enabled {
        apply_grain_rgba(rgba, width, height, &params.grain);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn disabled_params_leave_buffer_bit_identical() {
        let mut buf = vec![0u8; 64 * 64 * 4];
        for (i, b) in buf.iter_mut().enumerate() {
            *b = ((i * 37 + 13) & 255) as u8;
        }
        let orig = buf.clone();
        apply_film_post(&mut buf, 64, 64, &FilmPostParams::default());
        assert_eq!(buf, orig);
    }

    #[test]
    fn halation_creates_red_dominant_halo_around_bright_square_and_mask_isolates() {
        let w = 96;
        let h = 96;
        let mut img = vec![0u8; w * h * 4];
        for y in 0..h {
            for x in 0..w {
                let p = (y * w + x) * 4;
                let inside = (36..60).contains(&x) && (36..60).contains(&y);
                let v = if inside { 255 } else { 0 };
                img[p] = v;
                img[p + 1] = v;
                img[p + 2] = v;
                img[p + 3] = 255;
            }
        }

        let mut full = img.clone();
        let params = HalationPreset::Mm35.to_params(true);
        apply_halation_rgba(&mut full, w, h, &params);

        // Pixel adjacent to the white square should exhibit a warm red-orange halo (R > G > B and R > 0).
        let edge_idx = (48 * w + 35) * 4;
        let (r, g, b) = (full[edge_idx], full[edge_idx + 1], full[edge_idx + 2]);
        assert!(r > 10 && r > g && g > b, "expected warm red halo near edge, got ({r}, {g}, {b})");

        // Mask mode should isolate only the halo and keep far black corners at 0.
        let mut mask_params = params;
        mask_params.mask_mode = true;
        let mut masked = img;
        apply_halation_rgba(&mut masked, w, h, &mask_params);
        assert_eq!(&masked[0..3], &[0, 0, 0]);
        assert!(masked[edge_idx] > 10);
        // Inside the clipped white square, mask difference is 0.
        let center_idx = (48 * w + 48) * 4;
        assert_eq!(&masked[center_idx..center_idx + 3], &[0, 0, 0]);
    }

    #[test]
    fn grain_respects_tonal_weights_and_color_channel_coupling() {
        let w = 64;
        let h = 64;
        let mut mid_buf = vec![0u8; w * h * 4];
        for px in mid_buf.chunks_exact_mut(4) {
            px[0] = 128;
            px[1] = 128;
            px[2] = 128;
            px[3] = 255;
        }
        let mut dark_buf = vec![0u8; w * h * 4];
        for px in dark_buf.chunks_exact_mut(4) {
            px[0] = 0;
            px[1] = 0;
            px[2] = 0;
            px[3] = 255;
        }

        let mono = GrainParams {
            enabled: true,
            amount: 0.8,
            scale: 12.0,
            shadows: 0.0,
            midtones: 1.0,
            highlights: 0.0,
            color: 0.0,
            seed: 42,
        };
        apply_grain_rgba(&mut mid_buf, w, h, &mono);
        apply_grain_rgba(&mut dark_buf, w, h, &mono);

        // With color=0 on neutral gray, R == G == B for every pixel and variance > 0 in midtones.
        let mut mid_var = 0u64;
        for px in mid_buf.chunks_exact(4) {
            assert_eq!(px[0], px[1]);
            assert_eq!(px[1], px[2]);
            mid_var += (px[0] as i32 - 128).unsigned_abs() as u64;
        }
        assert!(mid_var > 500, "expected midtone grain variation, got {mid_var}");
        // Pure black with shadows=0 stays unaffected.
        assert!(dark_buf.chunks_exact(4).all(|px| px[0] == 0 && px[1] == 0 && px[2] == 0));

        // With color=1 on neutral gray, channels diverge.
        let mut color_buf = vec![128u8; w * h * 4];
        let colored = GrainParams { color: 1.0, ..mono };
        apply_grain_rgba(&mut color_buf, w, h, &colored);
        let diffs = color_buf
            .chunks_exact(4)
            .filter(|px| px[0] != px[1] || px[1] != px[2])
            .count();
        assert!(diffs > w * h / 2, "expected colored grain chroma differences, got {diffs}");
    }

    #[test]
    fn post_order_applies_grain_after_halation() {
        let w = 96;
        let h = 96;
        let mut base = vec![0u8; w * h * 4];
        for y in 0..h {
            for x in 0..w {
                let p = (y * w + x) * 4;
                let v = if (36..60).contains(&x) && (36..60).contains(&y) { 255 } else { 0 };
                base[p] = v;
                base[p + 1] = v;
                base[p + 2] = v;
                base[p + 3] = 255;
            }
        }
        let params = FilmPostParams {
            halation: HalationPreset::Mm35.to_params(true),
            grain: GrainPreset::Mm35Iso250.to_params(true, 7),
        };
        let mut combined = base.clone();
        apply_film_post(&mut combined, w, h, &params);

        let mut manual = base;
        apply_halation_rgba(&mut manual, w, h, &params.halation);
        apply_grain_rgba(&mut manual, w, h, &params.grain);
        assert_eq!(combined, manual);
    }
}
