//! f64 / integer path that follows `lut_core.model` and `lut_core.pack`.

use crate::Profile;

#[derive(Clone, Debug)]
pub struct OraclePreset {
    pub gamma: Vec<u16>,
    pub matrix: [i16; 9],
    pub delta: Vec<i8>,
}

pub fn round_code(x: f64) -> f64 {
    (x + 0.5).floor()
}

/// NumPy `rint`: half to even, matching the gamma Q16 fraction.
pub fn np_rint(x: f64) -> f64 {
    if !x.is_finite() {
        return x;
    }
    let floor = x.floor();
    let frac = x - floor;
    if (frac - 0.5).abs() <= 1e-12 {
        if (floor as i64).rem_euclid(2) == 0 {
            floor
        } else {
            floor + 1.0
        }
    } else if frac > 0.5 {
        floor + 1.0
    } else {
        floor
    }
}

pub fn gamma_codes(preset: &OraclePreset, profile: &Profile, u: &[f64]) -> Vec<i64> {
    let span = (profile.gamma_size - 1) as f64;
    let quant = 1i64 << 16;
    let mut out = Vec::with_capacity(u.len());
    for &value in u {
        let clipped = value.clamp(0.0, 1.0);
        let pos = clipped * span;
        let mut i0 = pos.floor() as usize;
        if i0 > profile.gamma_size - 2 {
            i0 = profile.gamma_size - 2;
        }
        let frac = np_rint((pos - i0 as f64) * quant as f64) as i64;
        let low = preset.gamma[i0] as i64;
        let high = preset.gamma[i0 + 1] as i64;
        out.push((low * (quant - frac) + high * frac + quant / 2) / quant);
    }
    out
}

pub fn matrix_codes(preset: &OraclePreset, profile: &Profile, rgb: &[f64]) -> Vec<i64> {
    let gamma = gamma_codes(preset, profile, rgb);
    let q = profile.matrix_q;
    let mut out = Vec::with_capacity(gamma.len());
    for pixel in gamma.chunks(3) {
        for row in 0..3 {
            let acc = pixel[0] * preset.matrix[row * 3] as i64
                + pixel[1] * preset.matrix[row * 3 + 1] as i64
                + pixel[2] * preset.matrix[row * 3 + 2] as i64;
            let code = (acc + q / 2).div_euclid(q);
            out.push(code.clamp(0, profile.gamma_max_val));
        }
    }
    out
}

fn np_interp(x: f64, xp: &[f64], fp: &[f64]) -> f64 {
    if x <= xp[0] {
        return fp[0];
    }
    if x >= xp[xp.len() - 1] {
        return *fp.last().unwrap();
    }
    let mut i = 0;
    while i + 1 < xp.len() && !(x < xp[i + 1]) {
        i += 1;
    }
    let t = (x - xp[i]) / (xp[i + 1] - xp[i]);
    fp[i] * (1.0 - t) + fp[i + 1] * t
}

pub fn rgb_to_ycc(rgb: &[f64], profile: &Profile) -> Vec<f64> {
    let kr = profile.kr();
    let kb = profile.kb();
    let kg = 1.0 - kr - kb;
    let s = profile.y_scale;
    let mut out = Vec::with_capacity(rgb.len());
    for px in rgb.chunks(3) {
        let yn = kr * px[0] + kg * px[1] + kb * px[2];
        let cb = (px[2] - yn) / (2.0 * (1.0 - kb));
        let cr = (px[0] - yn) / (2.0 * (1.0 - kr));
        out.extend([yn * s, cb * s, cr * s]);
    }
    out
}

pub fn ycc_to_rgb(ycc: &[f64], profile: &Profile) -> Vec<f64> {
    let kr = profile.kr();
    let kb = profile.kb();
    let kg = 1.0 - kr - kb;
    let s = profile.y_scale;
    let mut out = Vec::with_capacity(ycc.len());
    for px in ycc.chunks(3) {
        let yn = px[0] / s;
        let cb = px[1] / s;
        let cr = px[2] / s;
        let b = yn + 2.0 * (1.0 - kb) * cb;
        let r = yn + 2.0 * (1.0 - kr) * cr;
        let g = (yn - kr * r - kb * b) / kg;
        out.extend([r, g, b]);
    }
    out
}

fn lut_index(ycc: &[f64], profile: &Profile) -> Vec<f64> {
    let y_idx: Vec<f64> = (0..profile.y_axis.len()).map(|i| i as f64).collect();
    let c_idx: Vec<f64> = (0..profile.c_axis.len()).map(|i| i as f64).collect();
    let mut out = Vec::with_capacity(ycc.len());
    for px in ycc.chunks(3) {
        out.push(np_interp(px[0], &profile.y_axis, &y_idx));
        out.push(np_interp(px[1], &profile.c_axis, &c_idx));
        out.push(np_interp(px[2], &profile.c_axis, &c_idx));
    }
    out
}

pub fn trilinear(table: &[f64], dims: [usize; 3], channels: usize, fi: &[f64]) -> Vec<f64> {
    let mut out = vec![0.0; fi.len() / 3 * channels];
    for (p, coord) in fi.chunks(3).enumerate() {
        let mut base = [0usize; 3];
        let mut frac = [0.0; 3];
        for axis in 0..3 {
            let clipped = coord[axis].clamp(0.0, (dims[axis] - 1) as f64);
            let mut i0 = clipped.floor() as usize;
            if i0 > dims[axis] - 2 {
                i0 = dims[axis] - 2;
            }
            base[axis] = i0;
            frac[axis] = clipped - i0 as f64;
        }
        for dx in 0..2 {
            for dy in 0..2 {
                for dz in 0..2 {
                    let wx = if dx == 1 { frac[0] } else { 1.0 - frac[0] };
                    let wy = if dy == 1 { frac[1] } else { 1.0 - frac[1] };
                    let wz = if dz == 1 { frac[2] } else { 1.0 - frac[2] };
                    let idx = ((base[0] + dx) * dims[1] + (base[1] + dy)) * dims[2] + (base[2] + dz);
                    let w = wx * wy * wz;
                    for c in 0..channels {
                        out[p * channels + c] += w * table[idx * channels + c];
                    }
                }
            }
        }
    }
    out
}

pub fn apply_ycc(preset: &OraclePreset, profile: &Profile, u: &[f64]) -> Vec<f64> {
    let codes = matrix_codes(preset, profile, u);
    let mid: Vec<f64> = codes.iter().map(|c| *c as f64 / profile.gamma_max_val as f64).collect();
    let ycc = rgb_to_ycc(&mid, profile);
    let lo = profile.delta_range[0] as f64;
    let hi = profile.delta_range[1] as f64;
    let table: Vec<f64> = preset.delta.iter().map(|d| (*d as f64).clamp(lo, hi)).collect();
    let fi = lut_index(&ycc, profile);
    let delta = trilinear(&table, profile.grid(), 3, &fi);
    ycc.iter().zip(delta).map(|(a, b)| a + b).collect()
}

pub fn encode_8bit(ycc: &[f64], width: usize, height: usize, profile: &Profile, legal: bool, chroma_420: bool) -> (Vec<u8>, Vec<u8>) {
    let (y_gain, c_gain, offset, y_lo, y_hi, c_lo, c_hi) = if legal {
        (219.0, 224.0, 16.0, 16.0, 235.0, 16.0, 240.0)
    } else {
        (255.0, 255.0, 0.0, 0.0, 255.0, 0.0, 255.0)
    };
    let scale = profile.y_scale;
    let mut y_plane = Vec::with_capacity(width * height);
    let mut chroma = vec![[0.0; 2]; width * height];
    for y in 0..height {
        for x in 0..width {
            let i = (y * width + x) * 3;
            let code = round_code(ycc[i] * y_gain / scale + offset).clamp(y_lo, y_hi);
            y_plane.push(code as u8);
            chroma[y * width + x] = [ycc[i + 1], ycc[i + 2]];
        }
    }
    let (cw, ch, samples) = if chroma_420 {
        let pad_h = height % 2;
        let pad_w = width % 2;
        let ph = height + pad_h;
        let pw = width + pad_w;
        let mut padded = vec![[0.0; 2]; pw * ph];
        for y in 0..ph {
            for x in 0..pw {
                let sy = y.min(height - 1);
                let sx = x.min(width - 1);
                padded[y * pw + x] = chroma[sy * width + sx];
            }
        }
        let bh = ph / 2;
        let bw = pw / 2;
        let mut out = vec![[0.0; 2]; bw * bh];
        for by in 0..bh {
            for bx in 0..bw {
                let mut acc = [0.0; 2];
                for dy in 0..2 {
                    for dx in 0..2 {
                        let s = padded[(by * 2 + dy) * pw + bx * 2 + dx];
                        acc[0] += s[0];
                        acc[1] += s[1];
                    }
                }
                out[by * bw + bx] = [acc[0] / 4.0, acc[1] / 4.0];
            }
        }
        (bw, bh, out)
    } else {
        (width, height, chroma)
    };
    let mut c_plane = Vec::with_capacity(cw * ch * 2);
    for sample in samples {
        for value in sample {
            let code = round_code(value * c_gain / scale + 128.0).clamp(c_lo, c_hi);
            c_plane.push(code as u8);
        }
    }
    let _ = (cw, ch);
    (y_plane, c_plane)
}

pub fn natural_words(delta: &[i8]) -> Vec<u32> {
    delta
        .chunks(3)
        .map(|d| {
            let y = (d[0] as u8 as u32) & 0x7f;
            let cb = (d[1] as u8 as u32) & 0x7f;
            let cr = (d[2] as u8 as u32) & 0x7f;
            (y << 16) | (cb << 8) | cr
        })
        .collect()
}

pub fn pack_esram_words(delta: &[i8], permutation: &[usize]) -> Vec<u32> {
    let natural = natural_words(delta);
    permutation.iter().map(|&node| natural[node]).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn load() -> (Profile, OraclePreset, serde_json::Value) {
        let profile = Profile::a6400();
        let stages: serde_json::Value =
            serde_json::from_str(include_str!("../../../../artifacts/tests/bionz-golden/stages.json")).unwrap();
        let gamma: Vec<u16> = stages["gamma_u16"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap() as u16).collect();
        let matrix_v: Vec<i16> = stages["matrix_i16"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap() as i16).collect();
        let mut matrix = [0i16; 9];
        matrix.copy_from_slice(&matrix_v);
        let delta: Vec<i8> = stages["delta_i8"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap() as i8).collect();
        (profile, OraclePreset { gamma, matrix, delta }, stages)
    }

    fn f64s(v: &serde_json::Value) -> Vec<f64> {
        v.as_array().unwrap().iter().map(|n| n.as_f64().unwrap()).collect()
    }

    fn i64s(v: &serde_json::Value) -> Vec<i64> {
        v.as_array().unwrap().iter().map(|n| n.as_i64().unwrap()).collect()
    }

    #[test]
    fn gamma_and_matrix_match_numpy() {
        let (profile, preset, stages) = load();
        let u = f64s(&stages["u"]);
        assert_eq!(gamma_codes(&preset, &profile, &u), i64s(&stages["gamma_codes_i64"]));
        assert_eq!(matrix_codes(&preset, &profile, &u), i64s(&stages["matrix_codes_i64"]));
    }

    #[test]
    fn ycc_matches_numpy_bits() {
        let (profile, preset, stages) = load();
        let u = f64s(&stages["u"]);
        let got = apply_ycc(&preset, &profile, &u);
        let expect: Vec<u64> = stages["apply_ycc_bits"]
            .as_array()
            .unwrap()
            .iter()
            .map(|n| n.as_u64().unwrap())
            .collect();
        assert_eq!(got.len(), expect.len());
        for (a, b) in got.iter().zip(&expect) {
            assert_eq!(a.to_bits(), *b, "{a} vs bits {b}");
        }
    }

    #[test]
    fn encode_and_esram_match() {
        let (profile, preset, stages) = load();
        let image = f64s(&stages["encode_image_f64"]);
        let (y, c) = encode_8bit(&image, 3, 3, &profile, true, true);
        let (yf, cf) = encode_8bit(&image, 3, 3, &profile, false, true);
        assert_eq!(y, i64s(&stages["encode_legal_y_u8"]).into_iter().map(|v| v as u8).collect::<Vec<_>>());
        assert_eq!(c, i64s(&stages["encode_legal_c_u8"]).into_iter().map(|v| v as u8).collect::<Vec<_>>());
        assert_eq!(yf, i64s(&stages["encode_full_y_u8"]).into_iter().map(|v| v as u8).collect::<Vec<_>>());
        assert_eq!(cf, i64s(&stages["encode_full_c_u8"]).into_iter().map(|v| v as u8).collect::<Vec<_>>());
        let words = pack_esram_words(&preset.delta, &profile.permutation);
        let expect = decode_hex(stages["pack_esram_hex"].as_str().unwrap());
        let got: Vec<u8> = words.iter().flat_map(|w| w.to_le_bytes()).collect();
        assert_eq!(got, expect);
    }
}

#[cfg(test)]
fn decode_hex(s: &str) -> Vec<u8> {
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect()
}
