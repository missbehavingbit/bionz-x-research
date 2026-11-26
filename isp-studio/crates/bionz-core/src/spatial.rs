//! Spatial front end: `/usr/sabin/dcv_psf_table_*` decoding, Bayer-domain DCV, a directional
//! demosaic and the post-demosaic 9x9 DCV. This is the CPU reference; `bionz-gpu` mirrors it.
//!
//! Table layout (decoded on the host from the stock a6400 files, 2026-11-22):
//! * Bytes 0x00..0x30: ASCII sensor, lens and date tags (for example `IMX094`, `SAL50_CenGch_1`).
//! * u16 at 0x30: total size. u16 pairs at 0x40: zone grid (9, 7), footprint, then per-group
//!   kernel shapes. u16 at 0x54/0x58/0x5c: group strides; u32 at 0x78..: group byte offsets.
//! * Data from byte 144 (0x90). Earlier tools started at 0xEC and so misread the first 46 values.
//! * `movie_4k` / `movie`: 63 zones (9 columns x 7 rows) x 84 halfwords: a 9x9 kernel and 3 pad.
//! * `movie_bayer`: 3 groups (G, R, B) x 63 zones x 48 halfwords. G: a 5x5 kernel on its own phase
//!   (rows of 10: 5 same-phase taps, 4 taps of a 4x4 kernel on the other green phase, 1 pad; row 4
//!   holds only the 5x5 row). R and B: a 5x5 kernel, row-major, then zeros.
//! * Every stock kernel is symmetric and sums to about zero (+36, +4): an additive high-pass,
//!   `out = in + conv(in, K) / 2^q`.
//!
//! Model assumptions (badge A): the fixed-point scale `q` (default 14), nearest-zone or bilinear
//! zone selection, that group 0 is green and groups 1/2 are red/blue, and where the 9x9 kernel runs.
//!
//! DC behaviour: the Arrivederci v4/v5 4K table leaves zone (0,0) with a kernel sum of +1965. Read
//! as an additive Q14 high-pass that predicts a step of about +3 8-bit codes at the zone boundary in
//! S-Log3 footage. Frame 1 s of C0029 (v4 ON, 3840x2160, 2026-11-19) shows +0.04 / -0.05 codes, so
//! the hardware does not appear to pass a kernel's DC. `coefficients(.., dc_free)` models that by
//! moving each kernel's sum out of its centre tap; the app turns it on by default.

pub const ZONE_COLS: usize = 9;
pub const ZONE_ROWS: usize = 7;
pub const ZONES: usize = ZONE_COLS * ZONE_ROWS;
const DATA_START: usize = 144;

pub const STILL_ZONE_COLS: usize = 5;
pub const STILL_ZONE_ROWS: usize = 4;
pub const STILL_ZONES: usize = STILL_ZONE_COLS * STILL_ZONE_ROWS;
pub const STILL_DATA_START: usize = 248;
pub const STILL_KERNEL_W: usize = 11;
pub const STILL_KERNEL_H: usize = 7;
pub const STILL_KERNEL_TAPS: usize = STILL_KERNEL_W * STILL_KERNEL_H;
pub const STILL_ZONE_STRIDE_HALFWORDS: usize = 80;
pub const STILL_CENTRE_TAP: usize = 3 * STILL_KERNEL_W + 5;

#[derive(Clone, Debug, PartialEq)]
pub enum DcvLayout {
    /// Per zone: [group][41] = G: 25 same-phase + 16 cross-phase; R, B: 25 same-phase + 16 zeros.
    Bayer { zones: Vec<[[i16; 41]; 3]> },
    /// Per zone: 81 taps of a 9x9 kernel, row-major.
    Plane9 { zones: Vec<[i16; 81]> },
    /// Still table: `entries` along the aperture/iris axis (`axis_values`), each holding 20 zones
    /// (5x4) x 3 channel groups of 11x7 row-major kernels (77 taps).
    Still {
        axis_values: Vec<u32>,
        entries: Vec<[[[i16; STILL_KERNEL_TAPS]; 3]; STILL_ZONES]>,
    },
}

#[derive(Clone, Debug, PartialEq)]
pub struct DcvTable {
    pub sensor: String,
    pub lens: String,
    pub layout: DcvLayout,
}

fn u16_at(b: &[u8], off: usize) -> u16 {
    u16::from_le_bytes([b[off], b[off + 1]])
}

fn u32_at(b: &[u8], off: usize) -> u32 {
    u32::from_le_bytes([b[off], b[off + 1], b[off + 2], b[off + 3]])
}

fn i16_at(b: &[u8], off: usize) -> i16 {
    i16::from_le_bytes([b[off], b[off + 1]])
}

fn ascii_fields(b: &[u8]) -> Vec<String> {
    b[..0x30].split(|c| *c == 0).filter(|s| !s.is_empty()).map(|s| String::from_utf8_lossy(s).into_owned()).collect()
}

impl DcvTable {
    /// Parse a `dcv_psf_table_movie_bayer`, `movie_4k`, `movie`, or `still` table.
    pub fn parse(bytes: &[u8]) -> Result<Self, String> {
        if bytes.len() < DATA_START + 2 || u32_at(bytes, 0x30) as usize != bytes.len() {
            return Err("not a dcv_psf table (size field does not match)".into());
        }
        let grid = (u16_at(bytes, 0x40), u16_at(bytes, 0x42));
        let fields = ascii_fields(bytes);
        let sensor = fields.first().cloned().unwrap_or_default();
        let lens = fields.get(1).cloned().unwrap_or_default();
        if grid == (STILL_ZONE_COLS as u16, STILL_ZONE_ROWS as u16) {
            return Self::parse_still(bytes, sensor, lens);
        }
        if grid != (ZONE_COLS as u16, ZONE_ROWS as u16) {
            return Err(format!("unsupported zone grid {grid:?}"));
        }
        let layout = match bytes.len() {
            n if n == DATA_START + ZONES * 84 * 2 => {
                let mut zones = Vec::with_capacity(ZONES);
                for z in 0..ZONES {
                    let base = DATA_START + z * 168;
                    let mut k = [0i16; 81];
                    for (i, tap) in k.iter_mut().enumerate() {
                        *tap = i16_at(bytes, base + i * 2);
                    }
                    if (81..84).any(|i| i16_at(bytes, base + i * 2) != 0) {
                        return Err(format!("zone {z}: nonzero padding"));
                    }
                    zones.push(k);
                }
                DcvLayout::Plane9 { zones }
            }
            n if n == DATA_START + 3 * ZONES * 48 * 2 => {
                let mut zones = vec![[[0i16; 41]; 3]; ZONES];
                for g in 0..3 {
                    for (z, zone) in zones.iter_mut().enumerate() {
                        let base = DATA_START + (g * ZONES + z) * 96;
                        let e: Vec<i16> = (0..48).map(|i| i16_at(bytes, base + i * 2)).collect();
                        let k = &mut zone[g];
                        if g == 0 {
                            for r in 0..4 {
                                for c in 0..5 {
                                    k[r * 5 + c] = e[r * 10 + c];
                                }
                                for c in 0..4 {
                                    k[25 + r * 4 + c] = e[r * 10 + 5 + c];
                                }
                            }
                            k[20..25].copy_from_slice(&e[40..45]);
                        } else {
                            k[..25].copy_from_slice(&e[..25]);
                        }
                    }
                }
                DcvLayout::Bayer { zones }
            }
            n => return Err(format!("unsupported dcv_psf table size {n}")),
        };
        Ok(Self { sensor, lens, layout })
    }

    fn parse_still(bytes: &[u8], sensor: String, lens: String) -> Result<Self, String> {
        if bytes.len() < STILL_DATA_START + STILL_ZONES * STILL_ZONE_STRIDE_HALFWORDS * 2 {
            return Err("still dcv_psf table is too short".into());
        }
        let n0 = u32_at(bytes, 0x60) as usize;
        let n1 = u32_at(bytes, 0x64) as usize;
        let n2 = u32_at(bytes, 0x68) as usize;
        if n0 == 0 || n1 == 0 || n2 == 0 || n0 * n1 * n2 > 64 {
            return Err(format!("unsupported still axis counts ({n0}, {n1}, {n2})"));
        }
        let n_entries = n0 * n1 * n2;
        let strides = (u32_at(bytes, 0x54), u32_at(bytes, 0x58), u32_at(bytes, 0x5c));
        if strides != (1600, 1600, 1600) {
            return Err(format!("unsupported still block strides {strides:?}"));
        }
        let axis_values: Vec<u32> = (0..n1).map(|i| u32_at(bytes, 0x70 + i * 4)).collect();
        let offsets_start = 0x70 + (n1 + n0 * n1) * 4;
        if offsets_start + n_entries * 3 * 4 > STILL_DATA_START {
            return Err("still dcv_psf offset table exceeds header".into());
        }
        let mut entries = Vec::with_capacity(n_entries);
        for e in 0..n_entries {
            let mut block = [[[0i16; STILL_KERNEL_TAPS]; 3]; STILL_ZONES];
            for ch in 0..3 {
                let off = u32_at(bytes, offsets_start + (e * 3 + ch) * 4) as usize;
                if off < STILL_DATA_START || off + STILL_ZONES * STILL_ZONE_STRIDE_HALFWORDS * 2 > bytes.len() {
                    return Err(format!("still entry {e} ch {ch}: offset {off} out of range"));
                }
                for z in 0..STILL_ZONES {
                    let zbase = off + z * STILL_ZONE_STRIDE_HALFWORDS * 2;
                    for i in 0..STILL_KERNEL_TAPS {
                        block[z][ch][i] = i16_at(bytes, zbase + i * 2);
                    }
                    if (STILL_KERNEL_TAPS..STILL_ZONE_STRIDE_HALFWORDS).any(|i| i16_at(bytes, zbase + i * 2) != 0) {
                        return Err(format!("still entry {e} ch {ch} zone {z}: nonzero padding"));
                    }
                }
            }
            entries.push(block);
        }
        Ok(Self { sensor, lens, layout: DcvLayout::Still { axis_values, entries } })
    }

    pub fn kind(&self) -> &'static str {
        match self.layout {
            DcvLayout::Bayer { .. } => "Bayer (movie_bayer)",
            DcvLayout::Plane9 { .. } => "9x9 plane (movie_4k / movie)",
            DcvLayout::Still { .. } => "11x7 plane (still, 5x4 zones)",
        }
    }

    /// Kernels whose taps are not mirror-symmetric, and zones whose taps do not sum to near zero.
    /// Stock tables report none; a table built with the wrong offsets reports many.
    pub fn health(&self) -> (usize, usize) {
        let mut asym = 0;
        let mut dc = 0;
        let mut check_rect = |k: &[i16], w: usize, h: usize| {
            let at = |r: usize, c: usize| k[r * w + c];
            let symmetric = (0..h).all(|r| (0..w).all(|c| at(r, c) == at(h - 1 - r, w - 1 - c) && at(r, c) == at(r, w - 1 - c)));
            if !symmetric {
                asym += 1;
            }
        };
        match &self.layout {
            DcvLayout::Plane9 { zones } => {
                for z in zones {
                    check_rect(z, 9, 9);
                    if z.iter().map(|v| *v as i32).sum::<i32>().abs() > 256 {
                        dc += 1;
                    }
                }
            }
            DcvLayout::Bayer { zones } => {
                for z in zones {
                    for (g, k) in z.iter().enumerate() {
                        check_rect(&k[..25], 5, 5);
                        if g == 0 {
                            check_rect(&k[25..], 4, 4);
                        }
                        if k.iter().map(|v| *v as i32).sum::<i32>().abs() > 256 {
                            dc += 1;
                        }
                    }
                }
            }
            DcvLayout::Still { entries, .. } => {
                for entry in entries {
                    for zone in entry {
                        for k in zone {
                            check_rect(k, STILL_KERNEL_W, STILL_KERNEL_H);
                            if k.iter().map(|v| *v as i32).sum::<i32>().abs() > 256 {
                                dc += 1;
                            }
                        }
                    }
                }
            }
        }
        (asym, dc)
    }

    /// Kernel coefficients as f32 (divided by 2^q), flattened for the GPU:
    /// Bayer: [zone][group][41]; Plane9: [zone][81]; Still: [zone][channel][77] (entry 0).
    /// `dc_free` subtracts each kernel's sum from its centre tap, so the stage cannot shift a flat
    /// level (assumption, supported by the C0029 null result in the module notes).
    pub fn coefficients(&self, q: u32, dc_free: bool) -> Vec<f32> {
        self.coefficients_at(0, q, dc_free)
    }

    pub fn coefficients_at(&self, entry_idx: usize, q: u32, dc_free: bool) -> Vec<f32> {
        let s = 1.0 / (1u32 << q) as f32;
        let fix = |k: &[i16], centre: usize| -> Vec<f32> {
            let mut v: Vec<f32> = k.iter().map(|t| *t as f32 * s).collect();
            if dc_free {
                let sum: f32 = v.iter().sum();
                v[centre] -= sum;
            }
            v
        };
        match &self.layout {
            DcvLayout::Bayer { zones } => zones.iter().flat_map(|z| z.iter().flat_map(|k| fix(k, 12))).collect(),
            DcvLayout::Plane9 { zones } => zones.iter().flat_map(|z| fix(z, 40)).collect(),
            DcvLayout::Still { entries, .. } => {
                let idx = entry_idx.min(entries.len().saturating_sub(1));
                entries[idx].iter().flat_map(|z| z.iter().flat_map(|k| fix(k, STILL_CENTRE_TAP))).collect()
            }
        }
    }

    /// Smallest and largest centre tap over the zones (the green group for Bayer). A healthy table
    /// has one value; a table where some zones were left unscaled shows a large ratio.
    pub fn zone_centre_range(&self) -> (i16, i16) {
        let centres: Vec<i16> = match &self.layout {
            DcvLayout::Bayer { zones } => zones.iter().map(|z| z[0][12]).collect(),
            DcvLayout::Plane9 { zones } => zones.iter().map(|z| z[40]).collect(),
            DcvLayout::Still { entries, .. } => entries.first().map(|e| e.iter().map(|z| z[0][STILL_CENTRE_TAP]).collect()).unwrap_or_default(),
        };
        (*centres.iter().min().unwrap_or(&0), *centres.iter().max().unwrap_or(&0))
    }
}

/// Scale every kernel of a movie dcv_psf table by `gain` at its real offsets, keeping the header,
/// the zone/group structure and the padding. Each kernel's rounding error is folded into its
/// centre tap so the kernel sums exactly as the stock kernel did scaled (flat fields stay flat).
/// This is what the Arrivederci v4/v5 builder intended; it started at 0xEC with 48-value blocks.
pub fn rescale_table(bytes: &[u8], gain: f32) -> Result<Vec<u8>, String> {
    let table = DcvTable::parse(bytes)?;
    let mut out = bytes.to_vec();
    let mut put = |off: usize, v: i32| out[off..off + 2].copy_from_slice(&(v.clamp(-32768, 32767) as i16).to_le_bytes());
    let scale = |taps: &[i16], center: usize| -> Vec<i32> {
        let target = (taps.iter().map(|v| *v as f32).sum::<f32>() * gain).round() as i32;
        let mut s: Vec<i32> = taps.iter().map(|v| (*v as f32 * gain).round() as i32).collect();
        let err = s.iter().sum::<i32>() - target;
        s[center] -= err;
        s
    };
    match &table.layout {
        DcvLayout::Plane9 { zones } => {
            for (z, k) in zones.iter().enumerate() {
                for (i, v) in scale(k, 40).into_iter().enumerate() {
                    put(DATA_START + z * 168 + i * 2, v);
                }
            }
        }
        DcvLayout::Bayer { zones } => {
            for (z, zone) in zones.iter().enumerate() {
                for (g, k) in zone.iter().enumerate() {
                    let base = DATA_START + (g * ZONES + z) * 96;
                    let s = scale(k, 12);
                    if g == 0 {
                        for r in 0..4 {
                            for c in 0..5 {
                                put(base + (r * 10 + c) * 2, s[r * 5 + c]);
                            }
                            for c in 0..4 {
                                put(base + (r * 10 + 5 + c) * 2, s[25 + r * 4 + c]);
                            }
                        }
                        for c in 0..5 {
                            put(base + (40 + c) * 2, s[20 + c]);
                        }
                    } else {
                        for i in 0..25 {
                            put(base + i * 2, s[i]);
                        }
                    }
                }
            }
        }
        DcvLayout::Still { axis_values, entries } => {
            let n0 = u32_at(bytes, 0x60) as usize;
            let n1 = axis_values.len();
            let offsets_start = 0x70 + (n1 + n0 * n1) * 4;
            for (e, entry) in entries.iter().enumerate() {
                for ch in 0..3 {
                    let off = u32_at(bytes, offsets_start + (e * 3 + ch) * 4) as usize;
                    for (z, zone) in entry.iter().enumerate() {
                        let zbase = off + z * STILL_ZONE_STRIDE_HALFWORDS * 2;
                        for (i, v) in scale(&zone[ch], STILL_CENTRE_TAP).into_iter().enumerate() {
                            put(zbase + i * 2, v);
                        }
                    }
                }
            }
        }
    }
    Ok(out)
}

/// Encode a `DcvLayout::Still` table back to its binary format, preserving `header` (the 248-byte
/// header from a stock file) or building a minimal 248-byte header if `header` is `None`.
pub fn encode_still_table(
    sensor: &str,
    lens: &str,
    axis_values: &[u32],
    entries: &[[[[i16; STILL_KERNEL_TAPS]; 3]; STILL_ZONES]],
    header: Option<&[u8]>,
) -> Result<Vec<u8>, String> {
    let n1 = axis_values.len();
    if n1 == 0 || n1 != entries.len() || n1 > 6 {
        return Err(format!("still encoder supports 1..=6 axis entries, got {n1} / {}", entries.len()));
    }
    let total = STILL_DATA_START + n1 * 3 * STILL_ZONES * STILL_ZONE_STRIDE_HALFWORDS * 2;
    let mut out = vec![0u8; total];
    if let Some(h) = header {
        if h.len() < STILL_DATA_START {
            return Err("still header template is shorter than 248 bytes".into());
        }
        out[..STILL_DATA_START].copy_from_slice(&h[..STILL_DATA_START]);
    } else {
        let put_str = |dst: &mut [u8], s: &str| {
            let b = s.as_bytes();
            let n = b.len().min(dst.len().saturating_sub(1));
            dst[..n].copy_from_slice(&b[..n]);
        };
        put_str(&mut out[0x00..0x10], sensor);
        put_str(&mut out[0x10..0x20], lens);
        out[0x3c..0x3e].copy_from_slice(&5u16.to_le_bytes());
        out[0x3e..0x40].copy_from_slice(&5u16.to_le_bytes());
        out[0x40..0x42].copy_from_slice(&(STILL_ZONE_COLS as u16).to_le_bytes());
        out[0x42..0x44].copy_from_slice(&(STILL_ZONE_ROWS as u16).to_le_bytes());
        for (i, &(kw, kh)) in [(21u16, 13u16), (11, 11), (11, 7), (7, 7)].iter().enumerate() {
            out[0x44 + i * 4..0x46 + i * 4].copy_from_slice(&kw.to_le_bytes());
            out[0x46 + i * 4..0x48 + i * 4].copy_from_slice(&kh.to_le_bytes());
        }
        for i in 0..3 {
            out[0x54 + i * 4..0x58 + i * 4].copy_from_slice(&1600u32.to_le_bytes());
        }
        out[0x60..0x64].copy_from_slice(&1u32.to_le_bytes());
        out[0x64..0x68].copy_from_slice(&(n1 as u32).to_le_bytes());
        out[0x68..0x6c].copy_from_slice(&1u32.to_le_bytes());
        out[0x6c..0x70].copy_from_slice(&0x200000u32.to_le_bytes());
    }
    out[0x30..0x34].copy_from_slice(&(total as u32).to_le_bytes());
    out[0x60..0x64].copy_from_slice(&1u32.to_le_bytes());
    out[0x64..0x68].copy_from_slice(&(n1 as u32).to_le_bytes());
    out[0x68..0x6c].copy_from_slice(&1u32.to_le_bytes());
    for (i, &v) in axis_values.iter().enumerate() {
        out[0x70 + i * 4..0x74 + i * 4].copy_from_slice(&v.to_le_bytes());
    }
    let offsets_start = 0x70 + (n1 + n1) * 4;
    for (e, entry) in entries.iter().enumerate() {
        for ch in 0..3 {
            let off = STILL_DATA_START + (e * 3 + ch) * STILL_ZONES * STILL_ZONE_STRIDE_HALFWORDS * 2;
            let slot = offsets_start + (e * 3 + ch) * 4;
            out[slot..slot + 4].copy_from_slice(&(off as u32).to_le_bytes());
            for (z, zone) in entry.iter().enumerate() {
                let zbase = off + z * STILL_ZONE_STRIDE_HALFWORDS * 2;
                for (i, &tap) in zone[ch].iter().enumerate() {
                    out[zbase + i * 2..zbase + i * 2 + 2].copy_from_slice(&tap.to_le_bytes());
                }
            }
        }
    }
    Ok(out)
}

/// Designs a natural, low-halo still `dcv_psf_table_still.bin` inspired by ARRI ADA-7/AFA
/// texture enhancement: a zero-DC Difference-of-Gaussians kernel (`sigma1 = 0.60`, `sigma2 = 1.55`)
/// that boosts mid-band micro-contrast without the stock table's `r=2` positive ringing ring,
/// uniform across all 20 zones and preserving the stock 248-byte header and axis entries.
pub fn design_natural_still_table(stock_bytes: &[u8], strength: f32) -> Result<Vec<u8>, String> {
    let table = DcvTable::parse(stock_bytes)?;
    let (axis_values, orig_entries) = match &table.layout {
        DcvLayout::Still { axis_values, entries } => (axis_values.clone(), entries),
        _ => return Err("design_natural_still_table requires a Still DCV table".into()),
    };
    let s1 = 0.45f64;
    let s2 = 0.90f64;
    let mut g1 = [0.0f64; STILL_KERNEL_TAPS];
    let mut g2 = [0.0f64; STILL_KERNEL_TAPS];
    for r in 0..STILL_KERNEL_H {
        let dy = r as f64 - 3.0;
        for c in 0..STILL_KERNEL_W {
            let dx = c as f64 - 5.0;
            let d2 = dx * dx + dy * dy;
            if dx.abs() <= 2.0 && dy.abs() <= 2.0 {
                g1[r * STILL_KERNEL_W + c] = (-d2 / (2.0 * s1 * s1)).exp();
                g2[r * STILL_KERNEL_W + c] = (-d2 / (2.0 * s2 * s2)).exp();
            }
        }
    }
    let sum1: f64 = g1.iter().sum();
    let sum2: f64 = g2.iter().sum();
    let mut base = [0.0f64; STILL_KERNEL_TAPS];
    for i in 0..STILL_KERNEL_TAPS {
        base[i] = g1[i] / sum1 - g2[i] / sum2;
    }
    // Scale so that at strength=1.0 the centre tap is ~4200 (crisp detail with compact halo).
    let scale = (4200.0 * strength as f64) / base[STILL_CENTRE_TAP].max(1e-9);
    let mut new_entries = Vec::with_capacity(orig_entries.len());
    for entry in orig_entries {
        let stock_c = entry[0][0][STILL_CENTRE_TAP].abs().max(1) as f64;
        let ratio = (stock_c / 5624.0).clamp(0.45, 1.45);
        let mut k = [0i16; STILL_KERNEL_TAPS];
        let mut sum = 0i32;
        for i in 0..STILL_KERNEL_TAPS {
            let v = (base[i] * scale * ratio).round() as i32;
            k[i] = v.clamp(-32768, 32767) as i16;
            sum += k[i] as i32;
        }
        k[STILL_CENTRE_TAP] = (k[STILL_CENTRE_TAP] as i32 - sum).clamp(-32768, 32767) as i16;
        let zone = [k, k, k];
        new_entries.push([zone; STILL_ZONES]);
    }
    encode_still_table(&table.sensor, &table.lens, &axis_values, &new_entries, Some(&stock_bytes[..STILL_DATA_START]))
}

/// Bayer colour at (x, y): 0 = R, 1 = G, 2 = B. `pattern` lists the colours of (0,0), (1,0), (0,1), (1,1).
pub fn cfa_color(pattern: [u8; 4], x: usize, y: usize) -> usize {
    pattern[(y & 1) * 2 + (x & 1)] as usize
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DemosaicMode {
    /// ARRI-ADA-style: 5x5 directional gradients, Hamilton-Adams estimates with shadow damping,
    /// continuous direction weights, then colour-difference interpolation of R and B.
    Directional,
    Bilinear,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct FrontOptions {
    pub demosaic: DemosaicMode,
    /// Fixed-point scale of the DCV kernels: coefficients are divided by 2^q (assumption).
    pub dcv_q: u32,
    /// Blend kernels bilinearly between zone centres instead of using the nearest zone.
    pub zone_bilinear: bool,
    /// Multiplier on the DCV high-pass (1.0 = as stored).
    pub dcv_gain: f32,
    /// Soft-threshold on the still DCV high-pass delta, in the same linear units as the CFA/RGB
    /// data. Deltas below `t/2` are dropped, deltas above `1.5t` pass unchanged, and the transition
    /// uses a smoothstep. This models `PP_DetailCrispening`, which keeps flat-area sensor noise from
    /// being amplified. `0.0` disables it, so earlier baselines are unchanged.
    pub crispening_threshold: f32,
    /// Edge-preserving 3x3 luma bilateral applied before the still DCV, `0..1`. Weighted by the
    /// luma difference, so edges survive while flat-area noise is reduced. Models `CAMNRSTL`/`KKNR`/
    /// `LFNR`. `0.0` disables it, so earlier baselines are unchanged.
    pub luma_nr_strength: f32,
}

impl Default for FrontOptions {
    fn default() -> Self {
        Self {
            demosaic: DemosaicMode::Directional,
            dcv_q: 14,
            zone_bilinear: false,
            dcv_gain: 1.0,
            crispening_threshold: 0.0,
            luma_nr_strength: 0.0,
        }
    }
}

impl FrontOptions {
    /// Modeled high-ISO still noise reduction and detail crispening for a RAW ISO (badge A).
    ///
    /// The camera runs `CAMNRSTL`, `KKNR`/`LFNR` and `PP_DetailCrispening` before/around the still
    /// DCV. Their parameters are not decoded, so this maps ISO to a monotonic preset: no change at
    /// ISO 400 and below (the lens/AA filter already limit detail), then growing soft-threshold and
    /// bilateral strength. `iso` is the RAW EXIF ISO (`RawFrame::iso`) when available.
    pub fn with_iso_preset(self, iso: Option<u32>) -> Self {
        let Some(iso) = iso else { return self };
        let (crispening_threshold, luma_nr_strength) = match iso {
            0..=100 => (0.0, 0.0),
            101..=400 => (0.0008, 0.08),
            401..=800 => (0.0015, 0.15),
            801..=1600 => (0.0040, 0.35),
            1601..=3200 => (0.0080, 0.68),
            3201..=6400 => (0.0140, 0.95),
            _ => (0.0200, 1.00),
        };
        Self { crispening_threshold, luma_nr_strength, ..self }
    }
}

/// Soft-threshold used by [`dcv_still`] for detail crispening: identity above `1.5t`, suppressed
/// below `t/2`, smoothstep in between. `t <= 0` returns `x` unchanged.
pub fn crispening_soft_threshold(x: f32, t: f32) -> f32 {
    if t <= 0.0 {
        return x;
    }
    let a = x.abs();
    let (lo, hi) = (t * 0.5, t * 1.5);
    if a <= lo {
        return 0.0;
    }
    if a >= hi {
        return x;
    }
    let u = (a - lo) / (hi - lo);
    x * u * u * (3.0 - 2.0 * u)
}

/// Edge-preserving 3x3 bilateral on interleaved linear RGB, weighted by luma differences and
/// blended by `strength` (`0.0` returns a copy). Used before the still DCV as a lightweight model
/// of the camera's luma noise reduction. Luma uses the Rec.709 weights on the linear values.
pub fn luma_nr(rgb: &[f32], w: usize, h: usize, strength: f32) -> Vec<f32> {
    let strength = strength.clamp(0.0, 1.0);
    if strength <= 0.0 || w < 3 || h < 3 {
        return rgb.to_vec();
    }
    // Luma difference where the bilateral weight halves: about 1.5 8-bit codes of linear noise.
    const SIGMA: f32 = 0.004;
    let inv = 1.0 / (2.0 * SIGMA * SIGMA);
    let luma = |p: &[f32], x: usize, y: usize| -> f32 {
        let i = (y * w + x) * 3;
        0.2126 * p[i] + 0.7152 * p[i + 1] + 0.0722 * p[i + 2]
    };
    let mut out = rgb.to_vec();
    for y in 0..h {
        for x in 0..w {
            let l0 = luma(rgb, x, y);
            let mut acc = [0.0f32; 3];
            let mut wsum = 0.0f32;
            for dy in -1i32..=1 {
                let sy = (y as i32 + dy).clamp(0, h as i32 - 1) as usize;
                for dx in -1i32..=1 {
                    let sx = (x as i32 + dx).clamp(0, w as i32 - 1) as usize;
                    let dl = luma(rgb, sx, sy) - l0;
                    let wt = (-dl * dl * inv).exp();
                    let i = (sy * w + sx) * 3;
                    acc[0] += wt * rgb[i];
                    acc[1] += wt * rgb[i + 1];
                    acc[2] += wt * rgb[i + 2];
                    wsum += wt;
                }
            }
            let d = (y * w + x) * 3;
            let inv_sum = 1.0 / wsum.max(1e-9);
            for c in 0..3 {
                let filtered = acc[c] * inv_sum;
                out[d + c] = rgb[d + c] * (1.0 - strength) + filtered * strength;
            }
        }
    }
    out
}

/// Shadow threshold of the directional demosaic: 1024 on a 16-bit scale (E0600).
pub const SHADOW_KNEE: f32 = 1024.0 / 65535.0;

/// Zone weights for pixel (x, y) of a full frame (fw x fh): up to four (zone, weight) pairs.
pub fn zone_weights(x: usize, y: usize, fw: usize, fh: usize, bilinear: bool) -> [(usize, f32); 4] {
    zone_weights_grid(x, y, fw, fh, ZONE_COLS, ZONE_ROWS, bilinear)
}

pub fn zone_weights_grid(x: usize, y: usize, fw: usize, fh: usize, cols: usize, rows: usize, bilinear: bool) -> [(usize, f32); 4] {
    if !bilinear {
        let zx = (x * cols / fw.max(1)).min(cols - 1);
        let zy = (y * rows / fh.max(1)).min(rows - 1);
        return [(zy * cols + zx, 1.0), (0, 0.0), (0, 0.0), (0, 0.0)];
    }
    let fx = ((x as f32 + 0.5) / fw as f32 * cols as f32 - 0.5).clamp(0.0, (cols - 1) as f32);
    let fy = ((y as f32 + 0.5) / fh as f32 * rows as f32 - 0.5).clamp(0.0, (rows - 1) as f32);
    let (x0, y0) = (fx.floor() as usize, fy.floor() as usize);
    let (x1, y1) = ((x0 + 1).min(cols - 1), (y0 + 1).min(rows - 1));
    let (tx, ty) = (fx - x0 as f32, fy - y0 as f32);
    [
        (y0 * cols + x0, (1.0 - tx) * (1.0 - ty)),
        (y0 * cols + x1, tx * (1.0 - ty)),
        (y1 * cols + x0, (1.0 - tx) * ty),
        (y1 * cols + x1, tx * ty),
    ]
}

fn at(img: &[f32], w: usize, h: usize, x: isize, y: isize) -> f32 {
    img[(y.clamp(0, h as isize - 1) as usize) * w + x.clamp(0, w as isize - 1) as usize]
}

/// Bayer-domain DCV on a CFA plane (CPU reference). `coef` is `DcvTable::coefficients`.
pub fn dcv_bayer(cfa: &[f32], w: usize, h: usize, pattern: [u8; 4], coef: &[f32], opt: &FrontOptions) -> Vec<f32> {
    let mut out = vec![0.0f32; w * h];
    for y in 0..h {
        for x in 0..w {
            let c = cfa_color(pattern, x, y);
            let g = match c { 1 => 0, 0 => 1, _ => 2 };
            let mut hp = 0.0f32;
            for (zone, zw) in zone_weights(x, y, w, h, opt.zone_bilinear) {
                if zw == 0.0 {
                    continue;
                }
                let k = &coef[(zone * 3 + g) * 41..(zone * 3 + g) * 41 + 41];
                let mut acc = 0.0f32;
                for j in 0..5 {
                    for i in 0..5 {
                        acc += k[j * 5 + i] * at(cfa, w, h, x as isize + 2 * i as isize - 4, y as isize + 2 * j as isize - 4);
                    }
                }
                if g == 0 {
                    for j in 0..4 {
                        for i in 0..4 {
                            acc += k[25 + j * 4 + i] * at(cfa, w, h, x as isize + 2 * i as isize - 3, y as isize + 2 * j as isize - 3);
                        }
                    }
                }
                hp += zw * acc;
            }
            let v = cfa[y * w + x];
            out[y * w + x] = (v + opt.dcv_gain * hp).max(0.0);
        }
    }
    out
}

/// Demosaic a CFA plane to interleaved linear RGB (CPU reference). Directional mode runs four passes:
/// 1. green at R/B sites (`green_directional`);
/// 2. the opposite colour at R/B sites from the better diagonal, in colour difference;
/// 3. R and B at green sites from the better of horizontal/vertical, in colour difference;
/// 4. optional false-colour suppression: 3x3 median of R-G and B-G on the interpolated channels.
pub fn demosaic(cfa: &[f32], w: usize, h: usize, pattern: [u8; 4], mode: DemosaicMode) -> Vec<f32> {
    demosaic_with(cfa, w, h, pattern, mode, mode == DemosaicMode::Directional)
}

pub fn demosaic_with(cfa: &[f32], w: usize, h: usize, pattern: [u8; 4], mode: DemosaicMode, chroma_median: bool) -> Vec<f32> {
    let p = |x: isize, y: isize| at(cfa, w, h, x, y);
    let col = |x: isize, y: isize| cfa_color(pattern, x.rem_euclid(2) as usize, y.rem_euclid(2) as usize);
    let (wi, hi) = (w as isize, h as isize);
    // Pass 1: green everywhere.
    let mut green = vec![0.0f32; w * h];
    for y in 0..hi {
        for x in 0..wi {
            green[y as usize * w + x as usize] = if col(x, y) == 1 {
                p(x, y)
            } else {
                match mode {
                    DemosaicMode::Bilinear => 0.25 * (p(x - 1, y) + p(x + 1, y) + p(x, y - 1) + p(x, y + 1)),
                    DemosaicMode::Directional => green_directional(&p, x, y),
                }
            };
        }
    }
    let mut rgb = chroma_passes(cfa, &green, w, h, pattern, mode);
    // Pass 4: false-colour suppression on interpolated channels only; native samples stay.
    if chroma_median {
        let ix = |x: isize, y: isize| ((y.clamp(0, hi - 1) * wi + x.clamp(0, wi - 1)) * 3) as usize;
        let src = rgb.clone();
        for y in 0..hi {
            for x in 0..wi {
                let c = col(x, y);
                let i = ix(x, y);
                for t in [0usize, 2] {
                    if c == t {
                        continue;
                    }
                    let mut v = [0.0f32; 9];
                    let mut n = 0;
                    for dy in -1..=1 {
                        for dx in -1..=1 {
                            let j = ix(x + dx, y + dy);
                            v[n] = src[j + t] - src[j + 1];
                            n += 1;
                        }
                    }
                    rgb[i + t] = src[i + 1] + median9(v);
                }
            }
        }
    }
    for v in &mut rgb {
        *v = v.max(0.0);
    }
    rgb
}

/// Passes 2 and 3: native samples, green, the opposite colour at R/B sites (diagonal), then R and B
/// at green sites (horizontal/vertical), all in colour difference against `green`.
fn chroma_passes(cfa: &[f32], green: &[f32], w: usize, h: usize, pattern: [u8; 4], mode: DemosaicMode) -> Vec<f32> {
    let p = |x: isize, y: isize| at(cfa, w, h, x, y);
    let col = |x: isize, y: isize| cfa_color(pattern, x.rem_euclid(2) as usize, y.rem_euclid(2) as usize);
    let (wi, hi) = (w as isize, h as isize);
    let g = |x: isize, y: isize| at(green, w, h, x, y);
    let ix = |x: isize, y: isize| ((y.clamp(0, hi - 1) * wi + x.clamp(0, wi - 1)) * 3) as usize;
    let mut rgb = vec![0.0f32; w * h * 3];
    for y in 0..hi {
        for x in 0..wi {
            let c = col(x, y);
            let i = ix(x, y);
            rgb[i + 1] = g(x, y);
            if c == 1 {
                continue;
            }
            rgb[i + c] = p(x, y);
            let other = 2 - c;
            let d = |xx: isize, yy: isize| p(xx, yy) - g(xx, yy);
            rgb[i + other] = match mode {
                DemosaicMode::Bilinear => g(x, y) + 0.25 * (d(x - 1, y - 1) + d(x + 1, y - 1) + d(x - 1, y + 1) + d(x + 1, y + 1)),
                DemosaicMode::Directional => {
                    let ne = 0.5 * (d(x + 1, y - 1) + d(x - 1, y + 1));
                    let nw = 0.5 * (d(x - 1, y - 1) + d(x + 1, y + 1));
                    let g_ne = (d(x + 1, y - 1) - d(x - 1, y + 1)).abs() + (g(x + 1, y - 1) - g(x - 1, y + 1)).abs()
                        + (d(x + 2, y - 2) - d(x, y)).abs().min((d(x - 2, y + 2) - d(x, y)).abs()) * 0.5;
                    let g_nw = (d(x - 1, y - 1) - d(x + 1, y + 1)).abs() + (g(x - 1, y - 1) - g(x + 1, y + 1)).abs()
                        + (d(x - 2, y - 2) - d(x, y)).abs().min((d(x + 2, y + 2) - d(x, y)).abs()) * 0.5;
                    let (a, b) = (1.0 / (g_ne * g_ne + 1e-6), 1.0 / (g_nw * g_nw + 1e-6));
                    g(x, y) + (a * ne + b * nw) / (a + b)
                }
            };
        }
    }
    for y in 0..hi {
        for x in 0..wi {
            if col(x, y) != 1 {
                continue;
            }
            let i = ix(x, y);
            let gv = g(x, y);
            for t in [0usize, 2] {
                let d = |xx: isize, yy: isize| rgb[ix(xx, yy) + t] - g(xx, yy);
                rgb[i + t] = match mode {
                    DemosaicMode::Bilinear => {
                        if col(x - 1, y) == t {
                            gv + 0.5 * (d(x - 1, y) + d(x + 1, y))
                        } else {
                            gv + 0.5 * (d(x, y - 1) + d(x, y + 1))
                        }
                    }
                    DemosaicMode::Directional => {
                        let hval = 0.5 * (d(x - 1, y) + d(x + 1, y));
                        let vval = 0.5 * (d(x, y - 1) + d(x, y + 1));
                        let gh = (d(x - 1, y) - d(x + 1, y)).abs() + (g(x - 1, y) - g(x + 1, y)).abs() + 0.5 * (g(x - 2, y) + g(x + 2, y) - 2.0 * gv).abs();
                        let gvv = (d(x, y - 1) - d(x, y + 1)).abs() + (g(x, y - 1) - g(x, y + 1)).abs() + 0.5 * (g(x, y - 2) + g(x, y + 2) - 2.0 * gv).abs();
                        let (a, b) = (1.0 / (gh * gh + 1e-6), 1.0 / (gvv * gvv + 1e-6));
                        gv + (a * hval + b * vval) / (a + b)
                    }
                };
            }
        }
    }
    rgb
}

/// Median of nine values (sorting network order is irrelevant on the CPU; the GPU uses the same
/// partial selection, so results match bit for bit up to float evaluation order).
pub fn median9(mut v: [f32; 9]) -> f32 {
    v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    v[4]
}

/// Green at a red or blue site: 5x5 Hamilton-Adams gradients (green difference plus half the colour
/// Laplacian) with a double-weighted centre line, Hamilton-Adams
/// estimates whose Laplacian term is damped in the shadows, blended by the gradient ratio.
fn green_directional(p: &impl Fn(isize, isize) -> f32, x: isize, y: isize) -> f32 {
    let c0 = p(x, y);
    let mut gh = 0.0f32;
    let mut gv = 0.0f32;
    for k in -2..=2isize {
        let wgt = if k == 0 { 2.0 } else { 1.0 };
        let lh = 0.5 * (2.0 * p(x, y + k) - p(x - 2, y + k) - p(x + 2, y + k)).abs();
        let lv = 0.5 * (2.0 * p(x + k, y) - p(x + k, y - 2) - p(x + k, y + 2)).abs();
        gh += wgt * ((p(x - 1, y + k) - p(x + 1, y + k)).abs() + lh);
        gv += wgt * ((p(x + k, y - 1) - p(x + k, y + 1)).abs() + lv);
    }
    let avg_h = 0.5 * (p(x - 1, y) + p(x + 1, y));
    let avg_v = 0.5 * (p(x, y - 1) + p(x, y + 1));
    let sh = (avg_h / SHADOW_KNEE).min(1.0);
    let sv = (avg_v / SHADOW_KNEE).min(1.0);
    let est_h = avg_h + sh * 0.25 * (2.0 * c0 - p(x - 2, y) - p(x + 2, y));
    let est_v = avg_v + sv * 0.25 * (2.0 * c0 - p(x, y - 2) - p(x, y + 2));
    let eps = 1e-6f32;
    let wh = 1.0 / (gh * gh + eps);
    let wv = 1.0 / (gv * gv + eps);
    let lo = avg_h.min(avg_v).min(p(x - 1, y).min(p(x + 1, y)).min(p(x, y - 1).min(p(x, y + 1))));
    let hi = avg_h.max(avg_v).max(p(x - 1, y).max(p(x + 1, y)).max(p(x, y - 1).max(p(x, y + 1))));
    // Keep the estimate inside the local green range plus the Laplacian headroom, to avoid zippering.
    let g = (wh * est_h + wv * est_v) / (wh + wv);
    let slack = 0.5 * (hi - lo);
    g.clamp(lo - slack, hi + slack)
}

/// Post-demosaic 9x9 DCV on interleaved RGB (CPU reference), applied to each channel alike.
pub fn dcv_plane9(rgb: &[f32], w: usize, h: usize, coef: &[f32], opt: &FrontOptions) -> Vec<f32> {
    let mut out = vec![0.0f32; rgb.len()];
    for y in 0..h {
        for x in 0..w {
            let mut hp = [0.0f32; 3];
            for (zone, zw) in zone_weights(x, y, w, h, opt.zone_bilinear) {
                if zw == 0.0 {
                    continue;
                }
                let k = &coef[zone * 81..zone * 81 + 81];
                for j in 0..9isize {
                    for i in 0..9isize {
                        let sx = (x as isize + i - 4).clamp(0, w as isize - 1) as usize;
                        let sy = (y as isize + j - 4).clamp(0, h as isize - 1) as usize;
                        let t = k[(j * 9 + i) as usize] * zw;
                        let s = (sy * w + sx) * 3;
                        hp[0] += t * rgb[s];
                        hp[1] += t * rgb[s + 1];
                        hp[2] += t * rgb[s + 2];
                    }
                }
            }
            let d = (y * w + x) * 3;
            for c in 0..3 {
                out[d + c] = (rgb[d + c] + opt.dcv_gain * hp[c]).max(0.0);
            }
        }
    }
    out
}

/// Post-demosaic still 11x7 DCV on interleaved RGB (CPU reference).
/// `coef` is `DcvTable::coefficients_at` of a `Still` table: `[20 zones][3 channels][77 taps]`.
pub fn dcv_still(rgb: &[f32], w: usize, h: usize, coef: &[f32], opt: &FrontOptions) -> Vec<f32> {
    let mut out = vec![0.0f32; rgb.len()];
    for y in 0..h {
        for x in 0..w {
            let mut hp = [0.0f32; 3];
            for (zone, zw) in zone_weights_grid(x, y, w, h, STILL_ZONE_COLS, STILL_ZONE_ROWS, opt.zone_bilinear) {
                if zw == 0.0 {
                    continue;
                }
                for ch in 0..3 {
                    let base = (zone * 3 + ch) * STILL_KERNEL_TAPS;
                    let k = &coef[base..base + STILL_KERNEL_TAPS];
                    let mut acc = 0.0f32;
                    for j in 0..STILL_KERNEL_H as isize {
                        let sy = (y as isize + j - 3).clamp(0, h as isize - 1) as usize;
                        for i in 0..STILL_KERNEL_W as isize {
                            let sx = (x as isize + i - 5).clamp(0, w as isize - 1) as usize;
                            acc += k[(j as usize) * STILL_KERNEL_W + (i as usize)] * rgb[(sy * w + sx) * 3 + ch];
                        }
                    }
                    hp[ch] += zw * acc;
                }
            }
            let d = (y * w + x) * 3;
            for c in 0..3 {
                let delta = opt.dcv_gain * hp[c];
                out[d + c] = (rgb[d + c] + crispening_soft_threshold(delta, opt.crispening_threshold)).max(0.0);
            }
        }
    }
    out
}

/// Area-weighted resize of interleaved RGB (used for the 6K to 4K scale before the 9x9 DCV; the
/// camera's `spica` polyphase filter is not decoded, so this is a model).
pub fn resize_area(rgb: &[f32], w: usize, h: usize, dw: usize, dh: usize) -> Vec<f32> {
    let sx = w as f32 / dw as f32;
    let sy = h as f32 / dh as f32;
    let mut out = vec![0.0f32; dw * dh * 3];
    for y in 0..dh {
        let y0 = y as f32 * sy;
        let y1 = y0 + sy;
        for x in 0..dw {
            let x0 = x as f32 * sx;
            let x1 = x0 + sx;
            let mut acc = [0.0f32; 3];
            let mut wsum = 0.0f32;
            let mut yy = y0.floor() as usize;
            while (yy as f32) < y1 && yy < h {
                let wy = ((yy + 1) as f32).min(y1) - (yy as f32).max(y0);
                let mut xx = x0.floor() as usize;
                while (xx as f32) < x1 && xx < w {
                    let wx = ((xx + 1) as f32).min(x1) - (xx as f32).max(x0);
                    let wgt = wx * wy;
                    let s = (yy * w + xx) * 3;
                    acc[0] += wgt * rgb[s];
                    acc[1] += wgt * rgb[s + 1];
                    acc[2] += wgt * rgb[s + 2];
                    wsum += wgt;
                    xx += 1;
                }
                yy += 1;
            }
            let d = (y * dw + x) * 3;
            for c in 0..3 {
                out[d + c] = acc[c] / wsum.max(1e-9);
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bayer_table(g: [i16; 41], rb: [i16; 41]) -> Vec<u8> {
        let mut b = vec![0u8; DATA_START + 3 * ZONES * 96];
        let n = b.len() as u16;
        b[0x30..0x32].copy_from_slice(&n.to_le_bytes());
        b[0x40..0x42].copy_from_slice(&9u16.to_le_bytes());
        b[0x42..0x44].copy_from_slice(&7u16.to_le_bytes());
        for grp in 0..3 {
            for z in 0..ZONES {
                let base = DATA_START + (grp * ZONES + z) * 96;
                let mut e = [0i16; 48];
                if grp == 0 {
                    for r in 0..4 {
                        e[r * 10..r * 10 + 5].copy_from_slice(&g[r * 5..r * 5 + 5]);
                        e[r * 10 + 5..r * 10 + 9].copy_from_slice(&g[25 + r * 4..25 + r * 4 + 4]);
                    }
                    e[40..45].copy_from_slice(&g[20..25]);
                } else {
                    e[..25].copy_from_slice(&rb[..25]);
                }
                for (i, v) in e.iter().enumerate() {
                    b[base + i * 2..base + i * 2 + 2].copy_from_slice(&v.to_le_bytes());
                }
            }
        }
        b
    }

    #[test]
    fn bayer_table_round_trips_and_is_healthy() {
        let mut g = [0i16; 41];
        g[12] = 7812;
        g[7] = -504;
        g[11] = -504;
        g[13] = -504;
        g[17] = -504;
        g[25 + 5] = -1520;
        g[25 + 6] = -1520;
        g[25 + 9] = -1520;
        g[25 + 10] = -1520;
        let mut rb = [0i16; 41];
        rb[12] = 5572;
        rb[7] = -1393;
        rb[11] = -1393;
        rb[13] = -1393;
        rb[17] = -1393;
        let t = DcvTable::parse(&bayer_table(g, rb)).unwrap();
        match &t.layout {
            DcvLayout::Bayer { zones } => {
                assert_eq!(zones[0][0], g);
                assert_eq!(zones[62][1], rb);
            }
            _ => panic!("expected Bayer"),
        }
        assert_eq!(t.health().0, 0);
    }

    #[test]
    fn rescale_keeps_layout_symmetry_and_scales_every_zone() {
        let mut g = [0i16; 41];
        g[12] = 7812;
        for i in [7, 11, 13, 17] {
            g[i] = -504;
        }
        for i in [30, 31, 34, 35] {
            g[i] = -1520;
        }
        let mut rb = [0i16; 41];
        rb[12] = 5572;
        for i in [7, 11, 13, 17] {
            rb[i] = -1393;
        }
        let src = bayer_table(g, rb);
        let out = rescale_table(&src, 0.075).unwrap();
        assert_eq!(&out[..DATA_START], &src[..DATA_START]);
        let t = DcvTable::parse(&out).unwrap();
        assert_eq!(t.health(), (0, 0));
        let DcvLayout::Bayer { zones } = &t.layout else { panic!() };
        for zone in zones {
            assert_eq!(zone[0][7], -38);
            let want: i32 = (g.iter().map(|v| *v as f32).sum::<f32>() * 0.075).round() as i32;
            assert_eq!(zone[0].iter().map(|v| *v as i32).sum::<i32>(), want);
        }
    }

    #[test]
    fn flat_field_is_unchanged_by_a_zero_sum_kernel() {
        let mut k = [0i16; 81];
        k[40] = 4096;
        for i in [31, 39, 41, 49] {
            k[i] = -1024;
        }
        let coef: Vec<f32> = (0..ZONES).flat_map(|_| k.iter().map(|v| *v as f32 / 16384.0)).collect();
        let rgb = vec![0.3f32; 20 * 14 * 3];
        let out = dcv_plane9(&rgb, 20, 14, &coef, &FrontOptions::default());
        assert!(out.iter().all(|v| (v - 0.3).abs() < 1e-6));
    }

    #[test]
    fn directional_demosaic_keeps_a_flat_grey_and_resolves_a_vertical_edge() {
        let (w, h) = (16, 12);
        let pattern = [0, 1, 1, 2];
        let flat = vec![0.4f32; w * h];
        let rgb = demosaic(&flat, w, h, pattern, DemosaicMode::Directional);
        assert!(rgb.iter().all(|v| (v - 0.4).abs() < 1e-5));
        let edge: Vec<f32> = (0..w * h).map(|i| if i % w < 8 { 0.1 } else { 0.8 }).collect();
        let rgb = demosaic(&edge, w, h, pattern, DemosaicMode::Directional);
        for y in 2..h - 2 {
            assert!((rgb[(y * w + 3) * 3 + 1] - 0.1).abs() < 1e-4);
            assert!((rgb[(y * w + 12) * 3 + 1] - 0.8).abs() < 1e-4);
        }
    }

    #[test]
    fn area_resize_preserves_mean() {
        let rgb: Vec<f32> = (0..30 * 20 * 3).map(|i| (i % 7) as f32 / 7.0).collect();
        let out = resize_area(&rgb, 30, 20, 19, 13);
        let mean = |v: &[f32]| v.iter().sum::<f32>() / v.len() as f32;
        assert!((mean(&rgb) - mean(&out)).abs() < 0.01);
    }

    #[test]
    fn still_table_round_trips_and_rescales() {
        let mut k = [0i16; STILL_KERNEL_TAPS];
        k[STILL_CENTRE_TAP] = 5624;
        for idx in [STILL_CENTRE_TAP - 1, STILL_CENTRE_TAP + 1, STILL_CENTRE_TAP - STILL_KERNEL_W, STILL_CENTRE_TAP + STILL_KERNEL_W] {
            k[idx] = -1406;
        }
        let entries = vec![[[k; 3]; STILL_ZONES]; 6];
        let axis = vec![4608, 5369, 5632, 5867, 6144, 6379];
        let bytes = encode_still_table("IMX210", "TEST", &axis, &entries, None).unwrap();
        assert_eq!(bytes.len(), 57848);
        let parsed = DcvTable::parse(&bytes).unwrap();
        assert_eq!(parsed.health(), (0, 0));
        assert_eq!(parsed.zone_centre_range(), (5624, 5624));
        let scaled = rescale_table(&bytes, 0.25).unwrap();
        let reparsed = DcvTable::parse(&scaled).unwrap();
        assert_eq!(reparsed.health(), (0, 0));
        assert_eq!(reparsed.zone_centre_range(), (1408, 1408));
        let coef = reparsed.coefficients(14, true);
        let rgb = vec![0.4f32; 24 * 18 * 3];
        let out = dcv_still(&rgb, 24, 18, &coef, &FrontOptions::default());
        assert!(out.iter().all(|v| (v - 0.4).abs() < 1e-6));
    }

    #[test]
    fn stock_still_table_parses_when_present() {
        let path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/sabin/dcv_psf_table_still.bin");
        if !path.is_file() {
            return;
        }
        let bytes = std::fs::read(&path).unwrap();
        let table = DcvTable::parse(&bytes).unwrap();
        assert_eq!(table.sensor, "IMX210");
        assert_eq!(table.lens, "SAL50 CenGch_0");
        assert_eq!(table.health(), (0, 0));
        let DcvLayout::Still { axis_values, entries } = &table.layout else { panic!("expected Still") };
        assert_eq!(axis_values, &[4608, 5369, 5632, 5867, 6144, 6379]);
        assert_eq!(entries.len(), 6);
        let reencoded = encode_still_table(&table.sensor, &table.lens, axis_values, entries, Some(&bytes)).unwrap();
        assert_eq!(reencoded, bytes);
    }

    #[test]
    fn crispening_soft_threshold_is_identity_above_the_band_and_zero_below_it() {
        assert_eq!(crispening_soft_threshold(0.5, 0.0), 0.5);
        assert_eq!(crispening_soft_threshold(0.0004, 0.002), 0.0);
        assert_eq!(crispening_soft_threshold(-0.0004, 0.002), 0.0);
        assert_eq!(crispening_soft_threshold(0.004, 0.002), 0.004);
        assert!(crispening_soft_threshold(0.0015, 0.002) > 0.0);
        assert!(crispening_soft_threshold(0.0015, 0.002) < 0.0015);
        // Odd symmetry and continuity at the edges of the band.
        let (lo, hi) = (0.001, 0.003);
        assert!((crispening_soft_threshold(lo, 0.002)).abs() < 1e-9);
        assert!((crispening_soft_threshold(hi, 0.002) - hi).abs() < 1e-9);
    }

    #[test]
    fn luma_nr_keeps_edges_and_smooths_flat_noise() {
        let (w, h) = (24, 16);
        let mut rgb = vec![0.2f32; w * h * 3];
        // A hard step on the right half.
        for y in 0..h {
            for x in 12..w {
                let i = (y * w + x) * 3;
                rgb[i..i + 3].fill(0.6);
            }
        }
        let flat_before = rgb[(5 * w + 3) * 3];
        let edge_before = rgb[(5 * w + 12) * 3];
        let out = luma_nr(&rgb, w, h, 1.0);
        assert!((out[(5 * w + 3) * 3] - flat_before).abs() < 1e-5);
        assert!((out[(5 * w + 12) * 3] - edge_before).abs() < 1e-5);
        // Adding noise to a flat patch and denoising brings the local variance down.
        let mut noisy = vec![0.2f32; w * h * 3];
        for (i, v) in noisy.iter_mut().enumerate() {
            *v += ((i * 2654435761usize % 101) as f32 / 100.0 - 0.5) * 0.02;
        }
        let smooth = luma_nr(&noisy, w, h, 0.8);
        let var = |v: &[f32]| {
            let mean = v.iter().sum::<f32>() / v.len() as f32;
            v.iter().map(|x| (x - mean) * (x - mean)).sum::<f32>() / v.len() as f32
        };
        assert!(var(&smooth) < var(&noisy));
        assert_eq!(luma_nr(&noisy, w, h, 0.0), noisy);
    }

    #[test]
    fn iso_preset_is_off_low_and_grows_with_iso() {
        let base = FrontOptions::default();
        let off = base.with_iso_preset(Some(100));
        assert_eq!((off.crispening_threshold, off.luma_nr_strength), (0.0, 0.0));
        let low = base.with_iso_preset(Some(200));
        assert!(low.luma_nr_strength > 0.0 && low.crispening_threshold > 0.0);
        assert!(low.luma_nr_strength < 0.1);
        let high = base.with_iso_preset(Some(12800));
        let higher = base.with_iso_preset(Some(51200));
        assert!(high.luma_nr_strength > 0.0 && high.crispening_threshold > 0.0);
        assert!(higher.luma_nr_strength >= high.luma_nr_strength);
        assert_eq!(base.with_iso_preset(None), base);
    }
}
