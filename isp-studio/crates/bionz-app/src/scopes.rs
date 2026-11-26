//! Viewer helpers: scopes computed once per rendered frame, and the built-in test chart.

use bionz_raw::{LinearImage, SourceKind};
use eframe::egui::{Color32, ColorImage};

/// Histogram (RGB overlay), luma waveform and vectorscope of an 8-bit RGB frame.
pub fn scope_images(pixels: &[Color32], width: usize, height: usize) -> [ColorImage; 3] {
    let total = width * height;
    let step = (total / 250_000).max(1);
    let mut hist = [[0u32; 256]; 3];
    let (ww, wh) = (256usize, 100usize);
    let mut wave = vec![0u32; ww * wh];
    let vs = 120usize;
    let mut vec_density = vec![0u32; vs * vs];
    for i in (0..total).step_by(step) {
        let c32 = pixels[i];
        let p = [c32.r(), c32.g(), c32.b()];
        for c in 0..3 {
            hist[c][p[c] as usize] += 1;
        }
        let (r, g, b) = (p[0] as f32, p[1] as f32, p[2] as f32);
        let y = 0.2126 * r + 0.7152 * g + 0.0722 * b;
        let x = (i % width) * ww / width.max(1);
        let row = wh - 1 - ((y / 255.0) * (wh - 1) as f32) as usize;
        wave[row * ww + x] += 1;
        let cb = (b - y) / 1.8556;
        let cr = (r - y) / 1.5748;
        let vx = ((cb / 255.0 + 0.5) * (vs - 1) as f32).clamp(0.0, (vs - 1) as f32) as usize;
        let vy = ((0.5 - cr / 255.0) * (vs - 1) as f32).clamp(0.0, (vs - 1) as f32) as usize;
        vec_density[vy * vs + vx] += 1;
    }
    let hmax = hist.iter().flat_map(|h| h[2..254].iter()).copied().max().unwrap_or(1).max(1) as f32;
    let hh = 100usize;
    let mut himg = ColorImage::new([256, hh], Color32::from_gray(18));
    for x in 0..256 {
        for c in 0..3 {
            let top = ((hist[c][x] as f32 / hmax).min(1.0) * (hh - 1) as f32) as usize;
            for yy in 0..top {
                let px = &mut himg.pixels[(hh - 1 - yy) * 256 + x];
                let mut v = [px.r(), px.g(), px.b()];
                v[c] = v[c].saturating_add(150);
                *px = Color32::from_rgb(v[0], v[1], v[2]);
            }
        }
    }
    let density = |count: u32, scale: f32| ((count as f32).sqrt() * scale).min(255.0) as u8;
    let wscale = 255.0 / ((total / step) as f32 / ww as f32).sqrt().max(1.0) * 2.5;
    let wimg = ColorImage {
        size: [ww, wh],
        pixels: wave.iter().map(|c| { let v = density(*c, wscale); Color32::from_rgb(v / 2, v, v / 2) }).collect(),
    };
    let vscale = 255.0 / ((total / step) as f32 / 200.0).sqrt().max(1.0);
    let mut vimg = ColorImage {
        size: [vs, vs],
        pixels: vec_density.iter().map(|c| { let v = density(*c, vscale); Color32::from_rgb(v, v, v) }).collect(),
    };
    let mid = vs / 2;
    for i in 0..vs {
        vimg.pixels[mid * vs + i] = Color32::from_gray(60);
        vimg.pixels[i * vs + mid] = Color32::from_gray(60);
    }
    [himg, wimg, vimg]
}

/// Published sRGB approximations of the 24 ColorChecker patches, row by row.
const CHECKER: [[u8; 3]; 24] = [
    [115, 82, 68], [194, 150, 130], [98, 122, 157], [87, 108, 67], [133, 128, 177], [103, 189, 170],
    [214, 126, 44], [80, 91, 166], [193, 90, 99], [94, 60, 108], [157, 188, 64], [224, 163, 46],
    [56, 61, 150], [70, 148, 73], [175, 54, 60], [231, 199, 31], [187, 86, 149], [8, 133, 161],
    [243, 243, 242], [200, 200, 200], [160, 160, 160], [122, 122, 121], [85, 85, 85], [52, 52, 52],
];

/// A 6x4 chart with a grey ramp below it, as a display image (synthetic input, not sensor data).
pub fn test_chart() -> LinearImage {
    let (pw, w, h) = (120usize, 720usize, 540usize);
    let mut rgb8 = vec![0u8; w * h * 3];
    for y in 0..h {
        for x in 0..w {
            let px = if y < 480 {
                let (col, row) = (x / pw, y / pw);
                let (ix, iy) = (x % pw, y % pw);
                if ix < 8 || iy < 8 || ix >= pw - 8 || iy >= pw - 8 { [30, 30, 30] } else { CHECKER[row * 6 + col] }
            } else {
                let v = (x * 255 / (w - 1)) as u8;
                [v, v, v]
            };
            rgb8[(y * w + x) * 3..(y * w + x) * 3 + 3].copy_from_slice(&px);
        }
    }
    let lin = |v: u8| {
        let c = v as f32 / 255.0;
        if c <= 0.04045 { c / 12.92 } else { ((c + 0.055) / 1.055).powf(2.4) }
    };
    LinearImage {
        width: w,
        height: h,
        rgb: rgb8.iter().map(|v| lin(*v)).collect(),
        kind: SourceKind::DisplayImage,
        note: "built-in ColorChecker-style chart (published sRGB approximations); not sensor data".into(),
    }
}
