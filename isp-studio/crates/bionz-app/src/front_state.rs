//! Spatial front-end settings and per-side table selection. Side A uses the preset's spatial tables
//! (falling back to stock for any table it does not carry); side B always uses stock.

use std::hash::{Hash, Hasher};

use bionz_core::spatial::{DcvTable, DemosaicMode, FrontOptions};
use bionz_raw::{LinearImage, RawFrame, SpatialTables};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum SpatialPath {
    Off,
    /// HD movie: `dcv_psf_table_movie_bayer.bin` on the CFA before demosaic.
    HdBayer,
    /// 4K movie: area resize to 3840 wide, then `dcv_psf_table_movie_4k.bin` (9x9).
    Uhd4k,
    /// HD movie without the Bayer flag: resize to 1920 wide, then `dcv_psf_table_movie.bin` (9x9).
    HdPlane,
    /// Still photo: `dcv_psf_table_still.bin` (11x7, 5x4 zones) at full resolution.
    Still,
}

impl SpatialPath {
    pub const ALL: [SpatialPath; 5] = [SpatialPath::Off, SpatialPath::Still, SpatialPath::HdBayer, SpatialPath::Uhd4k, SpatialPath::HdPlane];

    pub fn label(self) -> &'static str {
        match self {
            SpatialPath::Off => "Off (no DCV)",
            SpatialPath::Still => "Still photo: 11x7 DCV (full res)",
            SpatialPath::HdBayer => "HD movie: Bayer DCV",
            SpatialPath::Uhd4k => "4K movie: 9x9 DCV at 3840",
            SpatialPath::HdPlane => "HD movie: 9x9 DCV at 1920",
        }
    }

    fn table(self) -> Option<(&'static str, Option<usize>)> {
        match self {
            SpatialPath::Off => None,
            SpatialPath::Still => Some(("dcv_psf_table_still.bin", None)),
            SpatialPath::HdBayer => Some(("dcv_psf_table_movie_bayer.bin", None)),
            SpatialPath::Uhd4k => Some(("dcv_psf_table_movie_4k.bin", Some(3840))),
            SpatialPath::HdPlane => Some(("dcv_psf_table_movie.bin", Some(1920))),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum DemosaicChoice {
    Directional,
    Bilinear,
    SuperPixel,
}

impl DemosaicChoice {
    pub const ALL: [DemosaicChoice; 3] = [DemosaicChoice::Directional, DemosaicChoice::Bilinear, DemosaicChoice::SuperPixel];

    pub fn label(self) -> &'static str {
        match self {
            DemosaicChoice::Directional => "Directional (ADA-style, full res)",
            DemosaicChoice::Bilinear => "Bilinear (full res)",
            DemosaicChoice::SuperPixel => "2x2 super-pixel (half res, fast)",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SpatialSettings {
    pub path: SpatialPath,
    pub demosaic: DemosaicChoice,
    pub q: u32,
    pub dc_free: bool,
    pub zone_bilinear: bool,
    pub gain: f32,
    /// Model the camera's high-ISO still noise reduction and detail crispening from the RAW ISO.
    pub high_iso_model: bool,
}

impl Default for SpatialSettings {
    fn default() -> Self {
        Self { path: SpatialPath::Off, demosaic: DemosaicChoice::Directional, q: 14, dc_free: true, zone_bilinear: false, gain: 1.0, high_iso_model: true }
    }
}

impl SpatialSettings {
    pub fn front_options(&self) -> FrontOptions {
        FrontOptions {
            demosaic: if self.demosaic == DemosaicChoice::Bilinear { DemosaicMode::Bilinear } else { DemosaicMode::Directional },
            dcv_q: self.q,
            zone_bilinear: self.zone_bilinear,
            dcv_gain: self.gain,
            ..Default::default()
        }
    }

    pub fn table_name(&self) -> Option<&'static str> {
        self.path.table().map(|(n, _)| n)
    }
}

/// Coefficients for one side, plus what the UI should say about them.
#[derive(Clone, Default)]
pub struct SideTables {
    pub bayer: Option<Vec<f32>>,
    pub plane9: Option<(Vec<f32>, usize)>,
    pub still: Option<Vec<f32>>,
    pub label: String,
    pub warning: Option<String>,
    pub fingerprint: String,
}

/// Build the coefficients for `settings` from `bytes` (the chosen table) or report why not.
pub fn side_tables(settings: &SpatialSettings, bytes: Option<&[u8]>, origin: &str) -> SideTables {
    let Some((name, width)) = settings.path.table() else {
        return SideTables { label: "no DCV".into(), ..Default::default() };
    };
    let Some(bytes) = bytes else {
        return SideTables { label: format!("{name} not available"), warning: Some(format!("{name}: no table (load the stock firmware tables or a preset that carries it)")), ..Default::default() };
    };
    match DcvTable::parse(bytes) {
        Ok(table) => {
            let (asym, dc) = table.health();
            let (lo, hi) = table.zone_centre_range();
            let mut warning = None;
            if asym > 0 || dc > 0 || (lo > 0 && hi > lo.saturating_mul(2)) {
                warning = Some(format!(
                    "{origin} {name}: {asym} asymmetric kernels, {dc} with a DC offset, zone centre taps {lo}..{hi}. \
                     This is what a table built with the wrong offsets looks like."
                ));
            }
            let coef = table.coefficients(settings.q, settings.dc_free);
            let fingerprint = crate::spatial_src::sha256_hex(bytes);
            let label = format!("{origin} {name} ({}, centre {lo}..{hi})", table.lens);
            match (&table.layout, width) {
                (bionz_core::spatial::DcvLayout::Still { .. }, _) => SideTables { still: Some(coef), label, warning, fingerprint, ..Default::default() },
                (_, None) => SideTables { bayer: Some(coef), label, warning, fingerprint, ..Default::default() },
                (_, Some(w)) => SideTables { plane9: Some((coef, w)), label, warning, fingerprint, ..Default::default() },
            }
        }
        Err(e) => SideTables { label: format!("{name}: {e}"), warning: Some(format!("{origin} {name}: {e}")), ..Default::default() },
    }
}

pub fn develop(frame: &RawFrame, settings: &SpatialSettings, t: &SideTables) -> Result<LinearImage, String> {
    let tables = SpatialTables { bayer: t.bayer.as_deref(), plane9: t.plane9.as_ref().map(|(c, w)| (c.as_slice(), *w)), still: t.still.as_deref() };
    let mut opt = settings.front_options();
    if settings.high_iso_model {
        opt = opt.with_iso_preset(frame.iso);
    }
    bionz_raw::develop_frame(frame, &opt, settings.demosaic == DemosaicChoice::SuperPixel, &tables).map_err(|e| e.to_string())
}

/// Identifies one developed result: settings plus the exact tables of both sides.
pub fn front_key(settings: &SpatialSettings, a: &SideTables, b: &SideTables) -> u64 {
    let mut h = std::collections::hash_map::DefaultHasher::new();
    settings.path.hash(&mut h);
    settings.demosaic.hash(&mut h);
    settings.q.hash(&mut h);
    settings.dc_free.hash(&mut h);
    settings.zone_bilinear.hash(&mut h);
    settings.gain.to_bits().hash(&mut h);
    settings.high_iso_model.hash(&mut h);
    a.fingerprint.hash(&mut h);
    b.fingerprint.hash(&mut h);
    h.finish()
}
