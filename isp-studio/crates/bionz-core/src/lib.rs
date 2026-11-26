//! CPU reference model for the BIONZ color chain.
//!
//! `oracle` matches `lut_core.model` in f64 and integer codes.
//! `integer` is the bit-exact stage chain shared with the WGSL kernels.
//! Floats are not used in `integer`. Assumption-labelled stages stay switchable.

mod integer;
mod oracle;
mod profile;
pub mod spatial;

pub use integer::{
    ec_knot_positions, encode_integer, encoded_to_rgb8, integer_pipeline, matrix_gain, mode0_10bit, sample_knots,
    scale_lut, stage_display, stage_gain, uniform_ec_knots, wide_mul, BaseMatrixSource, ColorMatrixMode, IntegerParams,
    IspRouting, LabMode, RgbI, StageTap, YccI, EC_RECORD31_EXPONENTS, STILL_STANDARD_FC,
};
pub use oracle::{
    apply_ycc, encode_8bit, gamma_codes, matrix_codes, np_rint, pack_esram_words, rgb_to_ycc,
    round_code, trilinear, ycc_to_rgb, OraclePreset,
};
pub use profile::Profile;

pub const STAGE_COUNT: usize = 16;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Badge {
    Verified,
    Modeled,
    Assumed,
    SyntheticInput,
    NotApplicable,
}

#[derive(Clone, Copy, Debug)]
pub struct StageInfo {
    pub index: u8,
    pub name: &'static str,
    pub badge: Badge,
    pub note: &'static str,
}

pub fn stage_catalog() -> &'static [StageInfo] {
    &STAGES
}

const STAGES: [StageInfo; 16] = [
    StageInfo { index: 1, name: "RAW unpack", badge: Badge::Verified, note: "Container format only. Sensor physics are not claimed." },
    StageInfo { index: 2, name: "Optical black and white level", badge: Badge::Modeled, note: "Per-channel levels come from the file tags." },
    StageInfo { index: 3, name: "White balance gains", badge: Badge::Modeled, note: "As-shot or user gains on the CFA." },
    StageInfo { index: 4, name: "Demosaic", badge: Badge::Assumed, note: "The camera algorithm is unknown. Bilinear, edge-directed, and 2x2 super-pixel are selectable models." },
    StageInfo { index: 5, name: "6K to 4K resample", badge: Badge::Modeled, note: "Hash-checked spica taps can be loaded. The convolution is not applied." },
    StageInfo { index: 6, name: "Camera to working RGB", badge: Badge::Modeled, note: "The Color Mode Q10 table is verified. The input domain is modeled." },
    StageInfo { index: 7, name: "EC gamma", badge: Badge::Assumed, note: "Knot format and live values are verified. The input scale is an assumption." },
    StageInfo { index: 8, name: "FC gamma", badge: Badge::Modeled, note: "96 halfwords. Order relative to EC is switchable because it is open." },
    StageInfo { index: 9, name: "Matrix A0/A1/B2", badge: Badge::Modeled, note: "Q10 (sum + 512) >> 10, then clamp." },
    StageInfo { index: 10, name: "Matrix-stage gain", badge: Badge::Assumed, note: "The replayed math is verified. Movie applicability is an assumption." },
    StageInfo { index: 11, name: "RGB to YCbCr", badge: Badge::Modeled, note: "BT.709 with Y in [0, 256] and chroma in [-128, 128]." },
    StageInfo { index: 12, name: "Residual 5x9x9 LUT", badge: Badge::Modeled, note: "Layout and eSRAM permutation are verified. Interpolation is modeled." },
    StageInfo { index: 13, name: "Detail", badge: Badge::Modeled, note: "Optional 3x3 from a local hash-checked kernel. Identity when bypassed." },
    StageInfo { index: 14, name: "Encoder", badge: Badge::Assumed, note: "8-bit legal or full. Chroma siting and the filter are assumptions until measured." },
    StageInfo { index: 15, name: "Codec", badge: Badge::Assumed, note: "Optional ffmpeg re-encode. Not part of the bit-exact chain." },
    StageInfo { index: 16, name: "View transform", badge: Badge::NotApplicable, note: "Preview only. Never written into a packed preset." },
];

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StageSwitch {
    pub bypass: [bool; 16],
    pub fc_before_ec: bool,
    pub ec_input_full_scale: i32,
    pub demosaic: Demosaic,
}

impl Default for StageSwitch {
    fn default() -> Self {
        Self {
            bypass: [false; 16],
            fc_before_ec: false,
            ec_input_full_scale: 8191,
            demosaic: Demosaic::Bilinear,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Demosaic {
    Bilinear,
    EdgeDirected,
    SuperPixel2x2,
}
