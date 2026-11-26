//! Stages 1-5. Display files use the `image` crate. Camera RAW uses `rawler`
//! and returns the integer CFA plus black, white, and white-balance tags.
//! Hash checks refuse a local table whose digest does not match.

use std::path::Path;

use sha2::{Digest, Sha256};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum RawError {
    #[error("{0}")]
    Message(String),
}

#[derive(Clone, Debug)]
pub struct CfaMeta {
    pub width: usize,
    pub height: usize,
    pub black: [i32; 4],
    pub white: i32,
    pub wb: [f32; 4],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Demosaic {
    Bilinear,
    EdgeDirected,
    SuperPixel2x2,
}

pub fn sha256_file(path: &Path) -> Result<String, RawError> {
    let bytes = std::fs::read(path).map_err(|e| RawError::Message(e.to_string()))?;
    Ok(hex_encode(&Sha256::digest(bytes)))
}

pub fn require_sha256(path: &Path, expected: &str) -> Result<(), RawError> {
    let got = sha256_file(path)?;
    if got != expected.to_ascii_lowercase() {
        return Err(RawError::Message(format!("hash mismatch for {}: {got}", path.display())));
    }
    Ok(())
}

pub fn decode_camera_raw(path: &Path) -> Result<(CfaMeta, Vec<u16>), RawError> {
    let image = rawler::decode_file(path).map_err(|e| RawError::Message(e.to_string()))?;
    let samples = match image.data {
        rawler::RawImageData::Integer(values) => values,
        rawler::RawImageData::Float(_) => {
            return Err(RawError::Message("float RAW is not a CFA stage".into()));
        }
    };
    if image.cpp != 1 {
        return Err(RawError::Message(format!("expected a single-channel CFA, found {} channels", image.cpp)));
    }
    if samples.len() != image.width.saturating_mul(image.height) {
        return Err(RawError::Message("CFA sample count does not match the image size".into()));
    }
    let white = image.whitelevel.0.first().copied().unwrap_or(65535) as i32;
    let black_level = image.blacklevel.levels.first().map(|level| level.as_f32() as i32).unwrap_or(0);
    let mut wb = image.wb_coeffs;
    if wb.iter().all(|value| *value == 0.0) {
        wb = [1.0; 4];
    }
    let meta = CfaMeta {
        width: image.width,
        height: image.height,
        black: [black_level; 4],
        white,
        wb,
    };
    Ok((meta, samples))
}

/// Where a working image came from. A display image is not sensor data.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SourceKind {
    CameraRaw,
    DisplayImage,
}

/// Scene-linear RGB in [0, 1] relative to the sensor white level (RAW) or to display white (JPEG/PNG).
#[derive(Clone, Debug)]
pub struct LinearImage {
    pub width: usize,
    pub height: usize,
    pub rgb: Vec<f32>,
    pub kind: SourceKind,
    pub note: String,
}

const RAW_EXTENSIONS: [&str; 12] = ["arw", "srf", "sr2", "dng", "nef", "cr2", "cr3", "raf", "orf", "rw2", "pef", "nrw"];

pub fn is_camera_raw(path: &Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .map(|e| RAW_EXTENSIONS.contains(&e.to_ascii_lowercase().as_str()))
        .unwrap_or(false)
}

/// Load a RAW or display image as linear RGB, box-downscaled so the long side is at most `max_side`
/// (0 keeps the decoded size).
///
/// RAW path (stages 1-4): rawler unpack, the file's black and white levels, as-shot white balance
/// normalized to green, the file's crop, a 2x2 super-pixel demosaic (half resolution), then the
/// orientation tag. No host color matrix is applied: the chain receives camera-native RGB, as the
/// stage-6 Color Mode matrix would. Display path: sRGB decode to linear (synthetic input, badge S).
pub fn load_linear(path: &Path, max_side: usize) -> Result<LinearImage, RawError> {
    let image = if is_camera_raw(path) { load_raw_linear(path)? } else { load_display_linear(path)? };
    Ok(downscale(image, max_side))
}

/// A cropped, black-subtracted, white-balanced CFA in [0, 1] relative to sensor white.
#[derive(Clone, Debug)]
pub struct RawFrame {
    pub width: usize,
    pub height: usize,
    pub cfa: Vec<f32>,
    /// Colours (0 R, 1 G, 2 B) of (0,0), (1,0), (0,1), (1,1) after the crop.
    pub pattern: [u8; 4],
    pub orientation: rawler::Orientation,
    /// Q10 sensor-RGB -> linear-sRGB 3x3 matrix derived from the RAW camera matrix (rows normalized to 1024).
    pub cam_to_srgb: Option<[i32; 9]>,
    /// ISO speed from the RAW EXIF metadata (`ISOSpeedRatings`/`ISOSpeed`), when present.
    pub iso: Option<u32>,
    pub note: String,
}

fn exif_iso(exif: &rawler::exif::Exif) -> Option<u32> {
    exif.iso_speed_ratings.map(u32::from).or(exif.iso_speed).or(exif.recommended_exposure_index)
}

fn cam_to_srgb_q10(image: &rawler::RawImage) -> Option<[i32; 9]> {
    let cam_to_xyz = image.cam_to_xyz_normalized();
    const XYZ_TO_SRGB: [[f32; 3]; 3] = [
        [3.2404542, -1.5371385, -0.4985314],
        [-0.9692660, 1.8760108, 0.0415560],
        [0.0556434, -0.2040259, 1.0572252],
    ];
    let mut m = [0.0f32; 9];
    for r in 0..3 {
        let mut row_sum = 0.0f32;
        for c in 0..3 {
            let v = (0..3).map(|k| XYZ_TO_SRGB[r][k] * cam_to_xyz[k][c]).sum::<f32>();
            m[r * 3 + c] = v;
            row_sum += v;
        }
        if !row_sum.is_finite() || row_sum.abs() < 1e-4 {
            return None;
        }
        for c in 0..3 {
            m[r * 3 + c] /= row_sum;
        }
    }
    let mut q10 = [0i32; 9];
    for r in 0..3 {
        let mut s = 0i32;
        for c in 0..3 {
            q10[r * 3 + c] = (m[r * 3 + c] * 1024.0).round() as i32;
            s += q10[r * 3 + c];
        }
        q10[r * 3 + r] += 1024 - s;
    }
    Some(q10)
}

pub fn decode_raw_frame(path: &Path) -> Result<RawFrame, RawError> {
    use rawler::decoders::RawDecodeParams;
    let source = rawler::rawsource::RawSource::new(path).map_err(|e| RawError::Message(format!("{}: {e}", path.display())))?;
    let decoder = rawler::get_decoder(&source).map_err(|e| RawError::Message(format!("{}: {e}", path.display())))?;
    let iso = decoder.raw_metadata(&source, &RawDecodeParams::default()).ok().and_then(|m| exif_iso(&m.exif));
    let image = decoder.raw_image(&source, &RawDecodeParams::default(), false).map_err(|e| RawError::Message(format!("{}: {e}", path.display())))?;
    let samples = match &image.data {
        rawler::RawImageData::Integer(values) => values,
        rawler::RawImageData::Float(_) => return Err(RawError::Message("float RAW data is not a CFA".into())),
    };
    if image.cpp != 1 {
        return Err(RawError::Message(format!("expected a single-channel CFA, found {} channels", image.cpp)));
    }
    let cfa = match &image.photometric {
        rawler::rawimage::RawPhotometricInterpretation::Cfa(config) => config.cfa.clone(),
        _ => return Err(RawError::Message("the RAW file is not a Bayer CFA".into())),
    };
    let full_w = image.width;
    let (cx, cy, cw, ch) = match image.crop_area {
        Some(rect) => (rect.p.x, rect.p.y, rect.d.w, rect.d.h),
        None => (0, 0, full_w, image.height),
    };
    let (cw, ch) = (cw & !1, ch & !1);
    let white = image.whitelevel.0.first().copied().unwrap_or(65535) as f32;
    let black: Vec<f32> = image.blacklevel.levels.iter().map(|l| l.as_f32()).collect();
    let mut wb = image.wb_coeffs;
    if !wb[3].is_finite() || wb[3] <= 0.0 {
        wb[3] = wb[1];
    }
    if !wb.iter().all(|v| v.is_finite() && *v > 0.0) {
        wb = [1.0; 4];
    }
    let green = wb[1].max(1e-6);
    let gains = [wb[0] / green, 1.0, wb[2] / green];
    let mut out = vec![0.0f32; cw * ch];
    for y in 0..ch {
        let row = cy + y;
        for x in 0..cw {
            let col = cx + x;
            let color = cfa.color_at(row, col).min(2);
            let b = match black.len() {
                0 => 0.0,
                1 => black[0],
                n => black[((row % 2) * 2 + (col % 2)) % n],
            };
            out[y * cw + x] = ((samples[row * full_w + col] as f32 - b) / (white - b).max(1.0)).max(0.0) * gains[color];
        }
    }
    let pattern = [
        cfa.color_at(cy, cx).min(2) as u8,
        cfa.color_at(cy, cx + 1).min(2) as u8,
        cfa.color_at(cy + 1, cx).min(2) as u8,
        cfa.color_at(cy + 1, cx + 1).min(2) as u8,
    ];
    let cam_to_srgb = cam_to_srgb_q10(&image);
    let note = format!(
        "{} {}x{} CFA, black {:.0}, white {:.0}, WB R {:.3} B {:.3}{}",
        image.camera.model, cw, ch, black.first().copied().unwrap_or(0.0), white, gains[0], gains[2],
        iso.map(|v| format!(", ISO {v}")).unwrap_or_default()
    );
    Ok(RawFrame { width: cw, height: ch, cfa: out, pattern, orientation: image.orientation, cam_to_srgb, iso, note })
}

/// Spatial tables for `develop_frame`: Bayer DCV coefficients, a 9x9 DCV with the frame width it
/// runs at (the image is area-resized to that width first, as a model of the 6K to 4K path), or a
/// still 11x7 DCV applied at full resolution.
pub struct SpatialTables<'a> {
    pub bayer: Option<&'a [f32]>,
    pub plane9: Option<(&'a [f32], usize)>,
    pub still: Option<&'a [f32]>,
}

/// Stages 1-5 on a decoded frame: optional Bayer DCV, demosaic, optional 4K-scale 9x9 DCV, orientation.
/// `super_pixel` takes the fast 2x2 path (half resolution, no Bayer DCV).
pub fn develop_frame(
    frame: &RawFrame,
    opt: &bionz_core::spatial::FrontOptions,
    super_pixel: bool,
    tables: &SpatialTables<'_>,
) -> Result<LinearImage, RawError> {
    use bionz_core::spatial;
    let (mut w, mut h) = (frame.width, frame.height);
    let mut rgb = if super_pixel {
        let (sw, sh) = (w / 2, h / 2);
        let mut rgb = vec![0.0f32; sw * sh * 3];
        for y in 0..sh {
            for x in 0..sw {
                let mut acc = [0.0f32; 3];
                let mut n = [0u32; 3];
                for (dx, dy) in [(0, 0), (1, 0), (0, 1), (1, 1)] {
                    let c = spatial::cfa_color(frame.pattern, dx, dy);
                    acc[c] += frame.cfa[(y * 2 + dy) * w + x * 2 + dx];
                    n[c] += 1;
                }
                for c in 0..3 {
                    rgb[(y * sw + x) * 3 + c] = acc[c] / n[c].max(1) as f32;
                }
            }
        }
        (w, h) = (sw, sh);
        rgb
    } else {
        #[cfg(feature = "gpu")]
        let gpu_res = bionz_gpu::run_front_bayer(&frame.cfa, w, h, frame.pattern, tables.bayer, opt).ok();
        #[cfg(not(feature = "gpu"))]
        let gpu_res: Option<Vec<f32>> = None;
        match gpu_res {
            Some(rgb) => rgb,
            None => {
                let cfa = match tables.bayer {
                    Some(coef) => spatial::dcv_bayer(&frame.cfa, w, h, frame.pattern, coef, opt),
                    None => frame.cfa.clone(),
                };
                spatial::demosaic(&cfa, w, h, frame.pattern, opt.demosaic)
            }
        }
    };
    if let Some((coef, target_w)) = tables.plane9 {
        if target_w > 0 && target_w < w {
            let target_h = (h * target_w + w / 2) / w;
            rgb = spatial::resize_area(&rgb, w, h, target_w, target_h);
            (w, h) = (target_w, target_h);
        }
        #[cfg(feature = "gpu")]
        let p9 = bionz_gpu::run_front_plane9(&rgb, w, h, coef, opt).ok();
        #[cfg(not(feature = "gpu"))]
        let p9: Option<Vec<f32>> = None;
        rgb = p9.unwrap_or_else(|| spatial::dcv_plane9(&rgb, w, h, coef, opt));
    }
    if let Some(coef) = tables.still {
        // The luma noise-reduction model runs on the CPU in both builds (it is a light 3x3
        // bilateral); the DCV stage itself is dispatched to the GPU when available.
        if opt.luma_nr_strength > 0.0 {
            rgb = spatial::luma_nr(&rgb, w, h, opt.luma_nr_strength);
        }
        #[cfg(feature = "gpu")]
        let st = bionz_gpu::run_front_still(&rgb, w, h, coef, opt).ok();
        #[cfg(not(feature = "gpu"))]
        let st: Option<Vec<f32>> = None;
        rgb = st.unwrap_or_else(|| spatial::dcv_still(&rgb, w, h, coef, opt));
    }
    let (w, h, rgb) = orient(w, h, rgb, frame.orientation);
    let mut note = frame.note.clone();
    note.push_str(if super_pixel { ", super-pixel demosaic" } else { match opt.demosaic {
        spatial::DemosaicMode::Directional => ", directional demosaic",
        spatial::DemosaicMode::Bilinear => ", bilinear demosaic",
    } });
    if tables.bayer.is_some() && !super_pixel {
        note.push_str(", Bayer DCV");
    }
    if tables.plane9.is_some() {
        note.push_str(", 9x9 DCV");
    }
    if tables.still.is_some() {
        note.push_str(", still 11x7 DCV");
    }
    if opt.luma_nr_strength > 0.0 || opt.crispening_threshold > 0.0 {
        note.push_str(&format!(
            ", modeled high-ISO NR (crispening {:.4}, luma NR {:.2})",
            opt.crispening_threshold, opt.luma_nr_strength
        ));
    }
    Ok(LinearImage { width: w, height: h, rgb, kind: SourceKind::CameraRaw, note })
}

fn load_raw_linear(path: &Path) -> Result<LinearImage, RawError> {
    let image = rawler::decode_file(path).map_err(|e| RawError::Message(format!("{}: {e}", path.display())))?;
    let samples = match &image.data {
        rawler::RawImageData::Integer(values) => values,
        rawler::RawImageData::Float(_) => return Err(RawError::Message("float RAW data is not a CFA".into())),
    };
    if image.cpp != 1 {
        return Err(RawError::Message(format!("expected a single-channel CFA, found {} channels", image.cpp)));
    }
    let cfa = match &image.photometric {
        rawler::rawimage::RawPhotometricInterpretation::Cfa(config) => config.cfa.clone(),
        _ => return Err(RawError::Message("the RAW file is not a Bayer CFA".into())),
    };
    let (full_w, full_h) = (image.width, image.height);
    let (cx, cy, cw, ch) = match image.crop_area {
        Some(rect) => (rect.p.x, rect.p.y, rect.d.w, rect.d.h),
        None => (0, 0, full_w, full_h),
    };
    let white = image.whitelevel.0.first().copied().unwrap_or(65535) as f32;
    let black: Vec<f32> = image.blacklevel.levels.iter().map(|l| l.as_f32()).collect();
    let black_at = |row: usize, col: usize| -> f32 {
        match black.len() {
            0 => 0.0,
            1 => black[0],
            n => black[((row % 2) * 2 + (col % 2)) % n],
        }
    };
    let mut wb = image.wb_coeffs;
    if !wb[3].is_finite() || wb[3] <= 0.0 {
        wb[3] = wb[1];
    }
    if !wb.iter().all(|v| v.is_finite() && *v > 0.0) {
        wb = [1.0; 4];
    }
    let green = wb[1].max(1e-6);
    let gains = [wb[0] / green, 1.0, wb[2] / green];
    let w = cw / 2;
    let h = ch / 2;
    let mut rgb = vec![0.0f32; w * h * 3];
    for by in 0..h {
        for bx in 0..w {
            let mut acc = [0.0f32; 3];
            let mut count = [0u32; 3];
            for dy in 0..2 {
                for dx in 0..2 {
                    let row = cy + by * 2 + dy;
                    let col = cx + bx * 2 + dx;
                    if row >= full_h || col >= full_w {
                        continue;
                    }
                    let color = cfa.color_at(row, col).min(2);
                    let b = black_at(row, col);
                    let v = ((samples[row * full_w + col] as f32 - b) / (white - b).max(1.0)).max(0.0);
                    acc[color] += v;
                    count[color] += 1;
                }
            }
            let i = (by * w + bx) * 3;
            for c in 0..3 {
                rgb[i + c] = if count[c] > 0 { acc[c] / count[c] as f32 * gains[c] } else { 0.0 };
            }
        }
    }
    let (w, h, rgb) = orient(w, h, rgb, image.orientation);
    let note = format!(
        "{} {}x{} CFA, black {:.0}, white {:.0}, WB R {:.3} B {:.3}, super-pixel demosaic",
        image.camera.model, cw, ch, black.first().copied().unwrap_or(0.0), white, gains[0], gains[2]
    );
    Ok(LinearImage { width: w, height: h, rgb, kind: SourceKind::CameraRaw, note })
}

fn orient(w: usize, h: usize, rgb: Vec<f32>, orientation: rawler::Orientation) -> (usize, usize, Vec<f32>) {
    use rawler::Orientation::*;
    let map: Option<(usize, usize, Box<dyn Fn(usize, usize) -> (usize, usize)>)> = match orientation {
        Rotate180 => Some((w, h, Box::new(move |x, y| (w - 1 - x, h - 1 - y)))),
        Rotate90 => Some((h, w, Box::new(move |x, y| (y, h - 1 - x)))),
        Rotate270 => Some((h, w, Box::new(move |x, y| (w - 1 - y, x)))),
        _ => None,
    };
    let Some((ow, oh, source)) = map else {
        return (w, h, rgb);
    };
    let mut out = vec![0.0f32; ow * oh * 3];
    for y in 0..oh {
        for x in 0..ow {
            let (sx, sy) = source(x, y);
            let s = (sy * w + sx) * 3;
            let d = (y * ow + x) * 3;
            out[d..d + 3].copy_from_slice(&rgb[s..s + 3]);
        }
    }
    (ow, oh, out)
}

fn srgb_to_linear(v: u8) -> f32 {
    let c = v as f32 / 255.0;
    if c <= 0.04045 { c / 12.92 } else { ((c + 0.055) / 1.055).powf(2.4) }
}

fn load_display_linear(path: &Path) -> Result<LinearImage, RawError> {
    let (w, h, bytes) = load_display(path)?;
    let lut: Vec<f32> = (0..=255u8).map(srgb_to_linear).collect();
    let rgb = bytes.iter().map(|b| lut[*b as usize]).collect();
    Ok(LinearImage {
        width: w as usize,
        height: h as usize,
        rgb,
        kind: SourceKind::DisplayImage,
        note: "display image decoded from sRGB to linear; not sensor data".into(),
    })
}

/// Exposure gain in stops, then quantize linear [0, 1] to the integer chain input (0..=gamma_max).
/// Sensor white maps to gamma_max and so to the top EC knot (8191); that input scale is the open
/// stage-7 assumption.
pub fn quantize(image: &LinearImage, ev: f32, gamma_max: i32) -> Vec<bionz_core::RgbI> {
    let gain = 2f32.powf(ev) * gamma_max as f32;
    image
        .rgb
        .chunks_exact(3)
        .map(|p| bionz_core::RgbI {
            r: (p[0] * gain + 0.5).clamp(0.0, gamma_max as f32) as i32,
            g: (p[1] * gain + 0.5).clamp(0.0, gamma_max as f32) as i32,
            b: (p[2] * gain + 0.5).clamp(0.0, gamma_max as f32) as i32,
        })
        .collect()
}

/// Integer box downscale so the long side is at most `max_side`.
pub fn downscale(image: LinearImage, max_side: usize) -> LinearImage {
    let long = image.width.max(image.height);
    if max_side == 0 || long <= max_side {
        return image;
    }
    let f = long.div_ceil(max_side);
    let (w, h) = (image.width / f, image.height / f);
    let mut rgb = vec![0.0f32; w * h * 3];
    let norm = 1.0 / (f * f) as f32;
    for y in 0..h {
        for x in 0..w {
            let mut acc = [0.0f32; 3];
            for dy in 0..f {
                let row = (y * f + dy) * image.width;
                for dx in 0..f {
                    let s = (row + x * f + dx) * 3;
                    acc[0] += image.rgb[s];
                    acc[1] += image.rgb[s + 1];
                    acc[2] += image.rgb[s + 2];
                }
            }
            let d = (y * w + x) * 3;
            rgb[d] = acc[0] * norm;
            rgb[d + 1] = acc[1] * norm;
            rgb[d + 2] = acc[2] * norm;
        }
    }
    LinearImage { width: w, height: h, rgb, ..image }
}

/// Resize 8-bit RGB to an exact size with box averaging (used to put a camera JPEG on the emulated grid).
pub fn resize_rgb8(src: &[u8], sw: usize, sh: usize, dw: usize, dh: usize) -> Vec<u8> {
    let mut out = vec![0u8; dw * dh * 3];
    for y in 0..dh {
        let y0 = y * sh / dh;
        let y1 = ((y + 1) * sh / dh).max(y0 + 1).min(sh);
        for x in 0..dw {
            let x0 = x * sw / dw;
            let x1 = ((x + 1) * sw / dw).max(x0 + 1).min(sw);
            let mut acc = [0u32; 3];
            for sy in y0..y1 {
                for sx in x0..x1 {
                    let s = (sy * sw + sx) * 3;
                    acc[0] += src[s] as u32;
                    acc[1] += src[s + 1] as u32;
                    acc[2] += src[s + 2] as u32;
                }
            }
            let n = ((y1 - y0) * (x1 - x0)) as u32;
            let d = (y * dw + x) * 3;
            for c in 0..3 {
                out[d + c] = ((acc[c] + n / 2) / n) as u8;
            }
        }
    }
    out
}

pub fn load_display(path: &Path) -> Result<(u32, u32, Vec<u8>), RawError> {
    let image = image::open(path).map_err(|e| RawError::Message(e.to_string()))?.to_rgb8();
    Ok((image.width(), image.height(), image.into_raw()))
}

pub fn optical_black(cfa: &[u16], meta: &CfaMeta) -> Vec<f32> {
    cfa.iter().enumerate().map(|(i, sample)| {
        let ch = bayer_channel(i % meta.width, i / meta.width);
        let black = meta.black[ch];
        let span = (meta.white - black).max(1) as f32;
        ((*sample as i32 - black).max(0) as f32) / span
    }).collect()
}

pub fn apply_wb(linear: &[f32], meta: &CfaMeta) -> Vec<f32> {
    linear.iter().enumerate().map(|(i, v)| {
        let ch = bayer_channel(i % meta.width, i / meta.width);
        v * meta.wb[ch]
    }).collect()
}

pub fn demosaic(cfa: &[f32], meta: &CfaMeta, mode: Demosaic) -> Vec<f32> {
    match mode {
        Demosaic::SuperPixel2x2 => super_pixel(cfa, meta),
        Demosaic::Bilinear | Demosaic::EdgeDirected => bilinear(cfa, meta, mode == Demosaic::EdgeDirected),
    }
}

fn bayer_channel(x: usize, y: usize) -> usize {
    match (y & 1, x & 1) {
        (0, 0) => 0,
        (0, 1) => 1,
        (1, 0) => 3,
        _ => 2,
    }
}

fn at(cfa: &[f32], w: usize, h: usize, x: isize, y: isize) -> f32 {
    let x = x.clamp(0, w as isize - 1) as usize;
    let y = y.clamp(0, h as isize - 1) as usize;
    cfa[y * w + x]
}

fn bilinear(cfa: &[f32], meta: &CfaMeta, edge: bool) -> Vec<f32> {
    let (w, h) = (meta.width, meta.height);
    let mut rgb = vec![0.0; w * h * 3];
    for y in 0..h {
        for x in 0..w {
            let (r, g, b) = match bayer_channel(x, y) {
                0 => (at(cfa, w, h, x as isize, y as isize), green_cross(cfa, w, h, x, y, edge), blue_diag(cfa, w, h, x, y)),
                2 => (blue_diag(cfa, w, h, x, y), green_cross(cfa, w, h, x, y, edge), at(cfa, w, h, x as isize, y as isize)),
                _ => {
                    let horiz = at(cfa, w, h, x as isize - 1, y as isize) + at(cfa, w, h, x as isize + 1, y as isize);
                    let vert = at(cfa, w, h, x as isize, y as isize - 1) + at(cfa, w, h, x as isize, y as isize + 1);
                    let (red, blue) = if bayer_channel(x, y) == 1 {
                        (horiz * 0.5, vert * 0.5)
                    } else {
                        (vert * 0.5, horiz * 0.5)
                    };
                    (red, at(cfa, w, h, x as isize, y as isize), blue)
                }
            };
            let i = (y * w + x) * 3;
            rgb[i] = r;
            rgb[i + 1] = g;
            rgb[i + 2] = b;
        }
    }
    rgb
}

fn green_cross(cfa: &[f32], w: usize, h: usize, x: usize, y: usize, edge: bool) -> f32 {
    let samples = [
        at(cfa, w, h, x as isize - 1, y as isize),
        at(cfa, w, h, x as isize + 1, y as isize),
        at(cfa, w, h, x as isize, y as isize - 1),
        at(cfa, w, h, x as isize, y as isize + 1),
    ];
    if edge {
        let gh = (samples[0] - samples[1]).abs();
        let gv = (samples[2] - samples[3]).abs();
        if gh + 0.02 < gv {
            return 0.5 * (samples[0] + samples[1]);
        }
        if gv + 0.02 < gh {
            return 0.5 * (samples[2] + samples[3]);
        }
    }
    0.25 * samples.iter().sum::<f32>()
}

fn blue_diag(cfa: &[f32], w: usize, h: usize, x: usize, y: usize) -> f32 {
    0.25 * (at(cfa, w, h, x as isize - 1, y as isize - 1)
        + at(cfa, w, h, x as isize + 1, y as isize - 1)
        + at(cfa, w, h, x as isize - 1, y as isize + 1)
        + at(cfa, w, h, x as isize + 1, y as isize + 1))
}

fn super_pixel(cfa: &[f32], meta: &CfaMeta) -> Vec<f32> {
    let w = meta.width / 2;
    let h = meta.height / 2;
    let mut rgb = vec![0.0; w * h * 3];
    for y in 0..h {
        for x in 0..w {
            let i = (y * w + x) * 3;
            rgb[i] = cfa[(y * 2) * meta.width + x * 2];
            rgb[i + 1] = 0.5 * (cfa[(y * 2) * meta.width + x * 2 + 1] + cfa[(y * 2 + 1) * meta.width + x * 2]);
            rgb[i + 2] = cfa[(y * 2 + 1) * meta.width + x * 2 + 1];
        }
    }
    rgb
}

/// Stages 1–4, then the integer chain from stage 6. Demosaic stays an assumption.
pub fn preview_from_cfa(
    cfa: &[u16],
    meta: &CfaMeta,
    mode: Demosaic,
    params: &bionz_core::IntegerParams,
) -> Result<Vec<bionz_core::StageTap>, RawError> {
    if meta.width < 2 || meta.height < 2 || cfa.len() != meta.width * meta.height {
        return Err(RawError::Message("CFA size does not match the metadata".into()));
    }
    let balanced = apply_wb(&optical_black(cfa, meta), meta);
    let rgb = demosaic(&balanced, meta, mode);
    let width = if mode == Demosaic::SuperPixel2x2 { meta.width / 2 } else { meta.width };
    let height = if mode == Demosaic::SuperPixel2x2 { meta.height / 2 } else { meta.height };
    if rgb.len() != width * height * 3 {
        return Err(RawError::Message("demosaic size does not match the CFA".into()));
    }
    let pixels: Vec<bionz_core::RgbI> = rgb.chunks(3).map(|px| bionz_core::RgbI {
        r: (px[0].clamp(0.0, 1.0) * params.gamma_max as f32) as i32,
        g: (px[1].clamp(0.0, 1.0) * params.gamma_max as f32) as i32,
        b: (px[2].clamp(0.0, 1.0) * params.gamma_max as f32) as i32,
    }).collect();
    Ok(bionz_core::integer_pipeline(&pixels, width as i32, params))
}

pub fn extract_q10_matrix(image: &[u8], file_offset: usize) -> Result<[i16; 9], RawError> {
    if image.len() < file_offset + 18 {
        return Err(RawError::Message("color-mode table offset is outside the image".into()));
    }
    let mut out = [0i16; 9];
    for i in 0..9 {
        let p = file_offset + i * 2;
        out[i] = i16::from_le_bytes([image[p], image[p + 1]]);
    }
    Ok(out)
}

/// Center 9 halfwords of a `dcv_psf` table, at the offset used by the E0606 extractor.
/// The file must match `expected_sha256`. This does not apply the kernel to an image.
pub fn load_dcv_center(path: &Path, expected_sha256: &str) -> Result<[i16; 9], RawError> {
    require_sha256(path, expected_sha256)?;
    let bytes = std::fs::read(path).map_err(|err| RawError::Message(err.to_string()))?;
    if bytes.len() < 0x11C + 10 {
        return Err(RawError::Message("dcv table is shorter than the center tap".into()));
    }
    let mut taps = [0i16; 9];
    for (i, tap) in taps.iter_mut().enumerate() {
        let offset = 0x114 + i * 2;
        *tap = i16::from_le_bytes([bytes[offset], bytes[offset + 1]]);
    }
    Ok(taps)
}

/// `spica` Y/C taps are little-endian `i16` in the observed range `0..=32767`.
/// A value outside that range, or a hash mismatch, is refused.
pub fn load_spica_taps(path: &Path, expected_sha256: &str) -> Result<Vec<i16>, RawError> {
    require_sha256(path, expected_sha256)?;
    let bytes = std::fs::read(path).map_err(|err| RawError::Message(err.to_string()))?;
    if bytes.len() < 2 || bytes.len() % 2 != 0 {
        return Err(RawError::Message("spica table length is not a whole number of taps".into()));
    }
    let mut taps = Vec::with_capacity(bytes.len() / 2);
    for pair in bytes.chunks_exact(2) {
        let tap = i16::from_le_bytes([pair[0], pair[1]]);
        if !(0..=32767).contains(&tap) {
            return Err(RawError::Message("spica tap is outside the observed 0..=32767 range".into()));
        }
        taps.push(tap);
    }
    Ok(taps)
}

fn hex_encode(bytes: impl AsRef<[u8]>) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let bytes = bytes.as_ref();
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push(HEX[(b >> 4) as usize] as char);
        out.push(HEX[(b & 0xf) as usize] as char);
    }
    out
}

/// Picture Profile 10 and 12 select EC records 28 and 31. Other values are not a verified selection.
pub fn ec_record_for_picture_profile(picture_profile: u8) -> Option<u8> {
    match picture_profile {
        10 => Some(28),
        12 => Some(31),
        _ => None,
    }
}

pub fn decode_note() -> &'static str {
    "ARW and DNG CFA decoding uses rawler and returns integer samples plus the file's black, white, and white-balance tags. This crate does not read the camera. A video frame is extracted only by an external ffmpeg binary supplied by the caller."
}

/// Extract one PNG frame with an external ffmpeg. A missing binary is an error, not an empty success.
pub fn extract_video_frame(ffmpeg: &Path, video: &Path, output_png: &Path) -> Result<(), RawError> {
    if !ffmpeg.is_file() {
        return Err(RawError::Message(format!("ffmpeg is not available at {}", ffmpeg.display())));
    }
    let output = std::process::Command::new(ffmpeg)
        .args(["-y", "-i"])
        .arg(video)
        .args(["-frames:v", "1"])
        .arg(output_png)
        .output()
        .map_err(|err| RawError::Message(err.to_string()))?;
    if !output.status.success() {
        return Err(RawError::Message("ffmpeg did not write a frame".into()));
    }
    if !output_png.is_file() {
        return Err(RawError::Message("ffmpeg reported success without an output frame".into()));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn black_wb_and_super_pixel() {
        let meta = CfaMeta { width: 2, height: 2, black: [10, 10, 10, 10], white: 110, wb: [1.0, 2.0, 1.0, 1.5] };
        let cfa = [10u16, 60, 40, 110];
        let linear = optical_black(&cfa, &meta);
        assert_eq!(linear[0], 0.0);
        let balanced = apply_wb(&linear, &meta);
        let rgb = demosaic(&balanced, &meta, Demosaic::SuperPixel2x2);
        assert_eq!(rgb.len(), 3);
        assert!(rgb[1] > rgb[0]);
        let taps = preview_from_cfa(&cfa, &meta, Demosaic::SuperPixel2x2, &bionz_core::IntegerParams::default()).unwrap();
        assert_eq!(taps.len(), 1);
        assert!(taps[0].rgb.b > taps[0].rgb.r);
    }

    #[test]
    fn hash_and_matrix_extract() {
        let path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/bionz-raw-hash.txt");
        std::fs::write(&path, b"table").unwrap();
        let digest = sha256_file(&path).unwrap();
        require_sha256(&path, &digest).unwrap();
        let mut image = vec![0u8; 20];
        image[0] = 0xff;
        image[1] = 0x03;
        let matrix = extract_q10_matrix(&image, 0).unwrap();
        assert_eq!(matrix[0], 1023);
        assert_eq!(ec_record_for_picture_profile(10), Some(28));
        assert_eq!(ec_record_for_picture_profile(12), Some(31));
        assert_eq!(ec_record_for_picture_profile(1), None);
        let missing = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/not-a-raw.txt");
        std::fs::write(&missing, b"not a camera raw").unwrap();
        assert!(decode_camera_raw(&missing).is_err());
        let dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target");
        let dcv = dir.join("synthetic-dcv.gray");
        let mut body = vec![0u8; 0x130];
        let center = [28i16, 0, 0, -504, 7812, -504, 0, 0, 28];
        for (i, tap) in center.iter().enumerate() {
            body[0x114 + i * 2..0x114 + i * 2 + 2].copy_from_slice(&tap.to_le_bytes());
        }
        std::fs::write(&dcv, &body).unwrap();
        let digest = sha256_file(&dcv).unwrap();
        assert_eq!(load_dcv_center(&dcv, &digest).unwrap(), center);
        assert!(load_dcv_center(&dcv, "00").is_err());
        let spica = dir.join("synthetic-spica.gray");
        std::fs::write(&spica, 32767i16.to_le_bytes()).unwrap();
        let spica_sha = sha256_file(&spica).unwrap();
        assert_eq!(load_spica_taps(&spica, &spica_sha).unwrap(), vec![32767]);
        std::fs::write(&spica, (-1i16).to_le_bytes()).unwrap();
        let bad_sha = sha256_file(&spica).unwrap();
        assert!(load_spica_taps(&spica, &bad_sha).is_err());
        let missing_ffmpeg = dir.join("missing-ffmpeg.gray");
        let err = extract_video_frame(&missing_ffmpeg, &spica, &dir.join("frame.png")).unwrap_err();
        assert!(err.to_string().contains("ffmpeg is not available"));
    }
}
