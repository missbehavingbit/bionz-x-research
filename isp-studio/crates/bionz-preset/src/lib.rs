//! Universal preset container and the legacy A6LT v1 packer.

use std::fs::File;
use std::io::{Cursor, Read, Write};
use std::path::Path;

use bionz_core::{pack_esram_words, OraclePreset, Profile};
use serde::Deserialize;
use thiserror::Error;

pub const A6LT_MAGIC: &[u8; 4] = b"A6LT";
pub const BZLK_MAGIC: &[u8; 4] = b"BZLK";

/// Pure-Rust `SMOD v2` emitter: turns compiled tables into a `.mod` for `<SD>:\mods\` without a
/// compiler, WSL or Python on the user's machine.
pub mod smod_v2;
pub use smod_v2::{emit_live_smod_v2, emit_smod_v2, live_template_layout, mod_filename, template_layout};
/// Stock tables extracted from the hash-verified ILCE-6400 2.00 `av-cam.bin`
/// (`a6400-lut-converter/tools/emit_stock_rs.py`).
pub mod stock_a6400;
const FLAG_PERM_VERIFIED: u32 = 1;
const FLAG_SYNTHETIC_STOCK: u32 = 1;

#[derive(Debug, Error)]
pub enum PresetError {
    #[error("{0}")]
    Message(String),
    #[error(transparent)]
    Io(#[from] std::io::Error),
}

pub type Result<T> = std::result::Result<T, PresetError>;

fn err(msg: impl Into<String>) -> PresetError {
    PresetError::Message(msg.into())
}

#[derive(Clone, Debug)]
pub struct CompiledTables {
    pub target: String,
    pub avcam_sha256: [u8; 32],
    pub flags: u32,
    pub lut_level: u16,
    pub ec: Vec<u32>,
    pub fc: Vec<u16>,
    pub a0: Vec<i16>,
    pub a1: Vec<i16>,
    pub b2: Vec<i16>,
    pub refadr: Vec<u8>,
    pub stock_ec: Vec<u32>,
    pub stock_fc: Vec<u16>,
    pub stock_a0: Vec<i16>,
    pub stock_a1: Vec<i16>,
    pub stock_b2: Vec<i16>,
    /// BZLK v2 extension: a Color Mode base matrix (stage 6, for example Movie entry B8) and its stock.
    pub base_matrix: Option<Vec<i16>>,
    pub stock_base_matrix: Option<Vec<i16>>,
    /// BZLK v2 extension: subdivision exponents of the EC record the `ec` table belongs to.
    pub ec_exponents: Option<[u8; 9]>,
}

/// Marker of the optional v2 extension block written after the stock tables.
const BZX2_MAGIC: &[u8; 4] = b"BZX2";
const EXT_BASE: u32 = 1;
const EXT_STOCK_BASE: u32 = 2;
const EXT_EC_EXP: u32 = 4;

impl CompiledTables {
    /// True when the stock tables were not taken from a hash-identified `av-cam.bin`
    /// (synthetic flag set, or no av-cam digest recorded).
    pub fn has_synthetic_stock(&self) -> bool {
        self.flags & FLAG_SYNTHETIC_STOCK != 0 || self.avcam_sha256 == [0u8; 32]
    }
}

#[derive(Clone, Debug, Deserialize)]
pub struct LookMeta {
    pub name: String,
    #[serde(default)]
    pub author: String,
    #[serde(default = "one")]
    pub version: u32,
    #[serde(default)]
    pub uuid: String,
    #[serde(default)]
    pub intent_kind: String,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub notes: String,
    #[serde(default)]
    pub created_with: String,
    #[serde(default)]
    pub cube_sha256: String,
}

fn one() -> u32 {
    1
}

#[derive(Clone, Debug)]
pub struct Bzlook {
    pub meta: LookMeta,
    pub recipe_toml: String,
    pub compiled: Vec<(String, CompiledTables)>,
    /// Optional Movie variant (`compiled/<target>_movie.bin`). Missing on older presets; the app
    /// then falls back to the Photo table in the Movie EC chain.
    pub compiled_movie: Vec<(String, CompiledTables)>,
    pub report_json: Vec<(String, String)>,
    pub thumb_png: Vec<u8>,
    /// Spatial tables stored under `spatial/<name>`, for example `dcv_psf_table_movie_bayer.bin`.
    /// They are parsed and validated by `bionz_core::spatial` when used.
    pub spatial: Vec<(String, Vec<u8>)>,
}

pub fn pack_a6lt(preset: &OraclePreset, profile: &Profile) -> Result<Vec<u8>> {
    if preset.gamma.len() != profile.gamma_size {
        return Err(err("gamma length does not match the profile"));
    }
    if preset.delta.len() != profile.n_nodes() * 3 {
        return Err(err("delta length does not match the profile"));
    }
    for value in &preset.delta {
        if !(-64..64).contains(value) {
            return Err(err("delta entries must be signed 7-bit integers"));
        }
    }
    let mut payload = Vec::new();
    for value in &preset.gamma {
        payload.extend(value.to_le_bytes());
    }
    for value in &preset.matrix {
        payload.extend(value.to_le_bytes());
    }
    payload.extend([0, 0]);
    for word in pack_esram_words(&preset.delta, &profile.permutation) {
        payload.extend(word.to_le_bytes());
    }
    let [ny, ncb, ncr] = profile.grid();
    let flags = if profile.permutation_verified { FLAG_PERM_VERIFIED } else { 0 };
    let crc = crc32fast::hash(&payload);
    let mut out = Vec::with_capacity(32 + payload.len());
    out.extend(A6LT_MAGIC);
    out.extend(1u16.to_le_bytes());
    out.extend(32u16.to_le_bytes());
    out.extend(flags.to_le_bytes());
    out.extend((profile.gamma_size as u16).to_le_bytes());
    out.extend((profile.gamma_max_val as u16).to_le_bytes());
    out.extend((profile.matrix_q as u16).to_le_bytes());
    out.push(ny as u8);
    out.push(ncb as u8);
    out.push(ncr as u8);
    out.push(0);
    out.extend((payload.len() as u32).to_le_bytes());
    out.extend(crc.to_le_bytes());
    out.extend(0u16.to_le_bytes());
    out.extend(payload);
    Ok(out)
}

pub fn unpack_a6lt(buf: &[u8], profile: &Profile) -> Result<OraclePreset> {
    if buf.len() < 32 || &buf[0..4] != A6LT_MAGIC {
        return Err(err("not an A6LT v1 preset"));
    }
    let ver = u16::from_le_bytes([buf[4], buf[5]]);
    let hsz = u16::from_le_bytes([buf[6], buf[7]]) as usize;
    let flags = u32::from_le_bytes(buf[8..12].try_into().unwrap());
    if ver != 1 || hsz != 32 || buf.len() < hsz {
        return Err(err("invalid A6LT header"));
    }
    if profile.permutation_verified && flags & FLAG_PERM_VERIFIED == 0 {
        return Err(err("legacy unverified ordering: supply the profile used to write this preset"));
    }
    let plen = u32::from_le_bytes(buf[22..26].try_into().unwrap()) as usize;
    let crc = u32::from_le_bytes(buf[26..30].try_into().unwrap());
    if buf.len() != hsz + plen {
        return Err(err("invalid preset size"));
    }
    let payload = &buf[hsz..hsz + plen];
    if crc32fast::hash(payload) != crc {
        return Err(err("preset truncated or CRC mismatch"));
    }
    let gs = profile.gamma_size;
    let gamma = (0..gs)
        .map(|i| u16::from_le_bytes([payload[i * 2], payload[i * 2 + 1]]))
        .collect();
    let o = gs * 2;
    let mut matrix = [0i16; 9];
    for i in 0..9 {
        matrix[i] = i16::from_le_bytes([payload[o + i * 2], payload[o + i * 2 + 1]]);
    }
    let words_off = o + 20;
    let mut natural = vec![0u32; profile.n_nodes()];
    let words: Vec<u32> = (0..profile.n_nodes())
        .map(|i| {
            let p = words_off + i * 4;
            u32::from_le_bytes(payload[p..p + 4].try_into().unwrap())
        })
        .collect();
    for (slot, &node) in profile.permutation.iter().enumerate() {
        natural[node] = words[slot];
    }
    let delta = natural
        .into_iter()
        .flat_map(|w| {
            [sx7((w >> 16) as u8), sx7((w >> 8) as u8), sx7(w as u8)]
        })
        .collect();
    Ok(OraclePreset { gamma, matrix, delta })
}

fn sx7(v: u8) -> i8 {
    let v = v & 0x7f;
    (if v & 0x40 != 0 { v as i16 - 128 } else { v as i16 }) as i8
}

pub fn encode_compiled(table: &CompiledTables) -> Vec<u8> {
    let mut key = [0u8; 32];
    let bytes = table.target.as_bytes();
    key[..bytes.len().min(32)].copy_from_slice(&bytes[..bytes.len().min(32)]);
    let mut payload = Vec::new();
    payload.extend(BZLK_MAGIC);
    payload.extend(1u16.to_le_bytes());
    payload.extend(key);
    payload.extend(table.avcam_sha256);
    payload.extend(table.flags.to_le_bytes());
    payload.extend(table.lut_level.to_le_bytes());
    push_u32s(&mut payload, &table.ec);
    push_u16s(&mut payload, &table.fc);
    push_i16s(&mut payload, &table.a0);
    push_i16s(&mut payload, &table.a1);
    push_i16s(&mut payload, &table.b2);
    payload.extend(&table.refadr);
    push_u32s(&mut payload, &table.stock_ec);
    push_u16s(&mut payload, &table.stock_fc);
    push_i16s(&mut payload, &table.stock_a0);
    push_i16s(&mut payload, &table.stock_a1);
    push_i16s(&mut payload, &table.stock_b2);
    let mut mask = 0u32;
    let mut ext = Vec::new();
    if let Some(m) = &table.base_matrix {
        mask |= EXT_BASE;
        push_i16s(&mut ext, &m[..9]);
    }
    if let Some(m) = &table.stock_base_matrix {
        mask |= EXT_STOCK_BASE;
        push_i16s(&mut ext, &m[..9]);
    }
    if let Some(e) = &table.ec_exponents {
        mask |= EXT_EC_EXP;
        ext.extend_from_slice(e);
    }
    if mask != 0 {
        payload[4..6].copy_from_slice(&2u16.to_le_bytes());
        payload.extend(BZX2_MAGIC);
        payload.extend(mask.to_le_bytes());
        payload.extend(ext);
    }
    let crc = crc32fast::hash(&payload);
    payload.extend(crc.to_le_bytes());
    payload
}

pub fn decode_compiled(buf: &[u8]) -> Result<CompiledTables> {
    if buf.len() < 8 || &buf[0..4] != BZLK_MAGIC {
        return Err(err("not a BZLK table"));
    }
    let crc_pos = buf.len() - 4;
    let crc = u32::from_le_bytes(buf[crc_pos..].try_into().unwrap());
    if crc32fast::hash(&buf[..crc_pos]) != crc {
        return Err(err("BZLK CRC mismatch"));
    }
    let mut c = Cursor::new(&buf[..crc_pos]);
    let mut magic = [0u8; 4];
    c.read_exact(&mut magic)?;
    let _ver = read_u16(&mut c)?;
    let mut key = [0u8; 32];
    c.read_exact(&mut key)?;
    let mut sha = [0u8; 32];
    c.read_exact(&mut sha)?;
    let flags = read_u32(&mut c)?;
    let lut_level = read_u16(&mut c)?;
    let ec = read_u32s(&mut c, 96)?;
    let fc = read_u16s(&mut c, 96)?;
    let a0 = read_i16s(&mut c, 9)?;
    let a1 = read_i16s(&mut c, 9)?;
    let b2 = read_i16s(&mut c, 9)?;
    let mut refadr = vec![0u8; 1215];
    c.read_exact(&mut refadr)?;
    let stock_ec = read_u32s(&mut c, 96)?;
    let stock_fc = read_u16s(&mut c, 96)?;
    let stock_a0 = read_i16s(&mut c, 9)?;
    let stock_a1 = read_i16s(&mut c, 9)?;
    let stock_b2 = read_i16s(&mut c, 9)?;
    let target = String::from_utf8_lossy(&key).trim_end_matches('\0').to_string();
    let (mut base_matrix, mut stock_base_matrix, mut ec_exponents) = (None, None, None);
    let mut magic = [0u8; 4];
    if c.read_exact(&mut magic).is_ok() {
        if &magic != BZX2_MAGIC {
            return Err(err("unknown data after the BZLK stock tables"));
        }
        let mask = read_u32(&mut c)?;
        if mask & EXT_BASE != 0 {
            base_matrix = Some(read_i16s(&mut c, 9)?);
        }
        if mask & EXT_STOCK_BASE != 0 {
            stock_base_matrix = Some(read_i16s(&mut c, 9)?);
        }
        if mask & EXT_EC_EXP != 0 {
            let mut e = [0u8; 9];
            c.read_exact(&mut e)?;
            if bionz_core::ec_knot_positions(&e).is_none() {
                return Err(err("EC exponents do not give 96 knots"));
            }
            ec_exponents = Some(e);
        }
    }
    let _ = FLAG_SYNTHETIC_STOCK;
    Ok(CompiledTables {
        base_matrix,
        stock_base_matrix,
        ec_exponents,
        target,
        avcam_sha256: sha,
        flags,
        lut_level,
        ec,
        fc,
        a0,
        a1,
        b2,
        refadr,
        stock_ec,
        stock_fc,
        stock_a0,
        stock_a1,
        stock_b2,
    })
}

fn push_u32s(out: &mut Vec<u8>, values: &[u32]) {
    for v in values {
        out.extend(v.to_le_bytes());
    }
}
fn push_u16s(out: &mut Vec<u8>, values: &[u16]) {
    for v in values {
        out.extend(v.to_le_bytes());
    }
}
fn push_i16s(out: &mut Vec<u8>, values: &[i16]) {
    for v in values {
        out.extend(v.to_le_bytes());
    }
}
fn read_u16(c: &mut Cursor<&[u8]>) -> Result<u16> {
    let mut b = [0u8; 2];
    c.read_exact(&mut b)?;
    Ok(u16::from_le_bytes(b))
}
fn read_u32(c: &mut Cursor<&[u8]>) -> Result<u32> {
    let mut b = [0u8; 4];
    c.read_exact(&mut b)?;
    Ok(u32::from_le_bytes(b))
}
fn read_u32s(c: &mut Cursor<&[u8]>, n: usize) -> Result<Vec<u32>> {
    (0..n).map(|_| read_u32(c)).collect()
}
fn read_u16s(c: &mut Cursor<&[u8]>, n: usize) -> Result<Vec<u16>> {
    (0..n).map(|_| read_u16(c)).collect()
}
fn read_i16s(c: &mut Cursor<&[u8]>, n: usize) -> Result<Vec<i16>> {
    (0..n)
        .map(|_| {
            let mut b = [0u8; 2];
            c.read_exact(&mut b)?;
            Ok(i16::from_le_bytes(b))
        })
        .collect()
}

pub fn load_bzlook(path: &Path) -> Result<Bzlook> {
    let data = std::fs::read(path)?;
    load_bzlook_bytes(&data)
}

/// Parse a `.bzlook` container held in memory. The portable app embeds its presets with
/// `include_bytes!` and has no file beside the executable to open.
pub fn load_bzlook_bytes(data: &[u8]) -> Result<Bzlook> {
    let mut zip = zip::ZipArchive::new(Cursor::new(data)).map_err(|e| err(e.to_string()))?;
    let mut meta_text = String::new();
    zip.by_name("look.toml").map_err(|e| err(e.to_string()))?.read_to_string(&mut meta_text)?;
    let meta: LookMeta = toml::from_str(&meta_text).map_err(|e| err(e.to_string()))?;
    let mut recipe_toml = String::new();
    if zip.by_name("intent/recipe.toml").is_ok() {
        zip.by_name("intent/recipe.toml").unwrap().read_to_string(&mut recipe_toml)?;
    }
    let mut compiled = Vec::new();
    let mut compiled_movie = Vec::new();
    let mut report_json = Vec::new();
    let names: Vec<String> = zip.file_names().map(|s| s.to_string()).collect();
    for name in names {
        if name.starts_with("compiled/") && name.ends_with(".bin") {
            let mut bytes = Vec::new();
            zip.by_name(&name).unwrap().read_to_end(&mut bytes)?;
            let key = name.trim_start_matches("compiled/").trim_end_matches(".bin").to_string();
            if let Some(target) = key.strip_suffix("_movie") {
                compiled_movie.push((target.to_string(), decode_compiled(&bytes)?));
            } else {
                compiled.push((key, decode_compiled(&bytes)?));
            }
        } else if name.starts_with("compiled/") && name.ends_with(".json") {
            let mut text = String::new();
            zip.by_name(&name).unwrap().read_to_string(&mut text)?;
            let key = name.trim_start_matches("compiled/").trim_end_matches(".json").to_string();
            report_json.push((key, text));
        }
    }
    let mut spatial = Vec::new();
    let names: Vec<String> = zip.file_names().map(|s| s.to_string()).collect();
    for name in names {
        if let Some(file_name) = name.strip_prefix("spatial/") {
            if file_name.is_empty() || file_name.contains('/') {
                continue;
            }
            let mut bytes = Vec::new();
            zip.by_name(&name).unwrap().read_to_end(&mut bytes)?;
            spatial.push((file_name.to_string(), bytes));
        }
    }
    spatial.sort_by(|a, b| a.0.cmp(&b.0));
    let mut thumb_png = Vec::new();
    if let Ok(mut file) = zip.by_name("thumb.png") {
        file.read_to_end(&mut thumb_png)?;
    }
    Ok(Bzlook { meta, recipe_toml, compiled, compiled_movie, report_json, thumb_png, spatial })
}

pub fn store_bzlook(path: &Path, look: &Bzlook) -> Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let file = File::create(path)?;
    let mut zip = zip::ZipWriter::new(file);
    let opts = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
    zip.start_file("look.toml", opts).map_err(|e| err(e.to_string()))?;
    zip.write_all(toml_from_meta(&look.meta).as_bytes())?;
    if !look.recipe_toml.is_empty() {
        zip.start_file("intent/recipe.toml", opts).map_err(|e| err(e.to_string()))?;
        zip.write_all(look.recipe_toml.as_bytes())?;
    }
    for (key, table) in &look.compiled {
        zip.start_file(format!("compiled/{key}.bin"), opts).map_err(|e| err(e.to_string()))?;
        zip.write_all(&encode_compiled(table))?;
        if let Some((_, report)) = look.report_json.iter().find(|(k, _)| k == key) {
            zip.start_file(format!("compiled/{key}.json"), opts).map_err(|e| err(e.to_string()))?;
            zip.write_all(report.as_bytes())?;
        }
    }
    for (key, table) in &look.compiled_movie {
        zip.start_file(format!("compiled/{key}_movie.bin"), opts).map_err(|e| err(e.to_string()))?;
        zip.write_all(&encode_compiled(table))?;
        if let Some((_, report)) = look.report_json.iter().find(|(k, _)| k == &format!("{key}_movie")) {
            zip.start_file(format!("compiled/{key}_movie.json"), opts).map_err(|e| err(e.to_string()))?;
            zip.write_all(report.as_bytes())?;
        }
    }
    for (name, bytes) in &look.spatial {
        if name.is_empty() || name.contains('/') || name.contains('\\') {
            return Err(err(format!("invalid spatial table name {name:?}")));
        }
        zip.start_file(format!("spatial/{name}"), opts).map_err(|e| err(e.to_string()))?;
        zip.write_all(bytes)?;
    }
    if !look.thumb_png.is_empty() {
        zip.start_file("thumb.png", opts).map_err(|e| err(e.to_string()))?;
        zip.write_all(&look.thumb_png)?;
    }
    zip.finish().map_err(|e| err(e.to_string()))?;
    Ok(())
}

fn toml_from_meta(meta: &LookMeta) -> String {
    format!(
        "name = {:?}\nauthor = {:?}\nversion = {}\nuuid = {:?}\nintent_kind = {:?}\ntags = [{}]\nnotes = {:?}\ncreated_with = {:?}\ncube_sha256 = {:?}\n",
        meta.name,
        meta.author,
        meta.version,
        meta.uuid,
        meta.intent_kind,
        meta.tags.iter().map(|t| format!("{t:?}")).collect::<Vec<_>>().join(", "),
        meta.notes,
        meta.created_with,
        meta.cube_sha256,
    )
}

#[derive(Clone, Debug)]
pub struct TargetAddrs {
    pub key: &'static str,
    pub label: &'static str,
    pub ec_gamma_pa: u32,
    pub fc_gamma_pa: u32,
    pub color_a0_pa: u32,
    pub color_a1_pa: u32,
    pub color_b2_pa: u32,
    pub cc_tuning_pa: u32,
    pub refadr0_pa: u32,
}

pub fn known_target(key: &str) -> Option<TargetAddrs> {
    match key {
        "a6400" => Some(TargetAddrs {
            key: "a6400",
            label: "ILCE-6400 Ver.2.00",
            ec_gamma_pa: 0x03E55330,
            fc_gamma_pa: 0x03E8F154,
            color_a0_pa: 0x0444A244,
            color_a1_pa: 0x0444A256,
            color_b2_pa: 0x0444B168,
            cc_tuning_pa: 0x0444A19E,
            refadr0_pa: 0x044ACD27,
        }),
        "a6100" => Some(TargetAddrs {
            key: "a6100",
            label: "ILCE-6100 Ver.1.00",
            ec_gamma_pa: 0x03E553B0,
            fc_gamma_pa: 0x03E8F1D4,
            color_a0_pa: 0x0444A254,
            color_a1_pa: 0x0444A266,
            color_b2_pa: 0x0444B178,
            cc_tuning_pa: 0x0444A1AE,
            refadr0_pa: 0x044ACD37,
        }),
        _ => None,
    }
}

fn slug(name: &str) -> String {
    let mut out = String::new();
    let mut sep = false;
    for ch in name.chars() {
        if ch.is_ascii_alphanumeric() {
            out.push(ch.to_ascii_lowercase());
            sep = false;
        } else if !sep && !out.is_empty() {
            out.push('_');
            sep = true;
        }
    }
    let out = out.trim_matches('_').to_string();
    if out.is_empty() { "custom_sim".into() } else { out }
}

fn fmt_dec(values: &[impl std::fmt::Display]) -> String {
    values.iter().map(|v| v.to_string()).collect::<Vec<_>>().join(",")
}

fn fmt_hex_u32(values: &[u32]) -> String {
    values.iter().map(|v| format!("{v:#x}")).collect::<Vec<_>>().join(",")
}

fn fmt_bytes(values: &[u8]) -> String {
    values.iter().map(|v| format!("0x{v:02x}")).collect::<Vec<_>>().join(",")
}

pub fn emit_film_sim_data(name: &str, target: &str, table: &CompiledTables) -> Result<String> {
    let prof = known_target(target).ok_or_else(|| err(format!("unknown target {target}")))?;
    let tag = slug(name);
    Ok(format!(
        "/* Auto-generated by mods/film_sims/studio_core.py for {name} ({label}) */\n#include <stdint.h>\n\n#define EC_GAMMA_PA 0x{ec:08x}u\n#define FC_GAMMA_PA 0x{fcpa:08x}u\n#define COLOR_A0_PA 0x{a0pa:08x}u\n#define COLOR_A1_PA 0x{a1pa:08x}u\n#define COLOR_B2_PA 0x{b2pa:08x}u\n#define MOD_TAG \"{tag}\"\n#define MOD_LOG_PATH \"/tmp_bt/mods/{tag}.log\"\n\nstatic const uint32_t EC_GAMMA_STOCK[96] = {{{stock_ec}}};\nstatic const uint32_t EC_GAMMA_SIM[96] = {{{ecsim}}};\nstatic const uint16_t FC_GAMMA_STOCK[96] = {{{stock_fc}}};\nstatic const uint16_t FC_GAMMA_SIM[96] = {{{fcsim}}};\nstatic const int16_t COLOR_A0_STOCK[9] = {{{stock_a0}}};\nstatic const int16_t COLOR_A0_SIM[9] = {{{a0sim}}};\nstatic const int16_t COLOR_A1_STOCK[9] = {{{stock_a1}}};\nstatic const int16_t COLOR_A1_SIM[9] = {{{a1sim}}};\nstatic const int16_t COLOR_B2_STOCK[9] = {{{stock_b2}}};\nstatic const int16_t COLOR_B2_SIM[9] = {{{b2sim}}};\n#define REFADR_ENABLED 1\n#define CC_TUNING_PA 0x{cc:08x}u\n#define REFADR0_PA 0x{refpa:08x}u\nstatic const uint8_t REFADR_SIM[1215] = {{{refadr}}};\n",
        label = prof.label,
        ec = prof.ec_gamma_pa,
        fcpa = prof.fc_gamma_pa,
        a0pa = prof.color_a0_pa,
        a1pa = prof.color_a1_pa,
        b2pa = prof.color_b2_pa,
        stock_ec = fmt_hex_u32(&table.stock_ec),
        ecsim = fmt_hex_u32(&table.ec),
        stock_fc = fmt_dec(&table.stock_fc),
        fcsim = fmt_dec(&table.fc),
        stock_a0 = fmt_dec(&table.stock_a0),
        a0sim = fmt_dec(&table.a0),
        stock_a1 = fmt_dec(&table.stock_a1),
        a1sim = fmt_dec(&table.a1),
        stock_b2 = fmt_dec(&table.stock_b2),
        b2sim = fmt_dec(&table.b2),
        cc = prof.cc_tuning_pa,
        refpa = prof.refadr0_pa,
        refadr = fmt_bytes(&table.refadr),
    ))
}

pub fn emit_live_header(target: &str, looks: &[(String, CompiledTables)]) -> Result<String> {
    let prof = known_target(target).ok_or_else(|| err(format!("unknown target {target}")))?;
    if looks.is_empty() {
        return Err(err("live header needs at least one preset"));
    }
    let stock = &looks[0].1;
    let n = looks.len() + 1;
    let mut names = vec!["\"Stock (Off)\"".to_string()];
    for (name, _) in looks {
        let short: String = name.chars().take(15).collect();
        names.push(format!("\"{short}\""));
    }
    let idxs = (0..n).map(|i| if i == 0 { "-1".into() } else { (i - 1).to_string() }).collect::<Vec<_>>().join(",");
    let mut ec = Vec::new();
    let mut fc = Vec::new();
    let mut a0 = Vec::new();
    let mut a1 = Vec::new();
    let mut b2 = Vec::new();
    let mut luts = Vec::new();
    for (_, table) in looks {
        ec.push(format!("{{{}}}", fmt_hex_u32(&table.ec)));
        fc.push(format!("{{{}}}", fmt_dec(&table.fc)));
        a0.push(format!("{{{}}}", fmt_dec(&table.a0)));
        a1.push(format!("{{{}}}", fmt_dec(&table.a1)));
        b2.push(format!("{{{}}}", fmt_dec(&table.b2)));
        luts.push(format!("{{{}}}", fmt_bytes(&table.refadr)));
    }
    Ok(format!(
        "/* Auto-generated by mods/film_sims/studio_core.py for FilmSimLive.mod ({label}) */\n#include <stdint.h>\n\n#define EC_GAMMA_PA 0x{ecpa:08x}u\n#define FC_GAMMA_PA 0x{fcpa:08x}u\n#define COLOR_A0_PA 0x{a0pa:08x}u\n#define COLOR_A1_PA 0x{a1pa:08x}u\n#define COLOR_B2_PA 0x{b2pa:08x}u\n#define CC_TUNING_PA 0x{cc:08x}u\n#define REFADR0_PA 0x{refpa:08x}u\n#define LIVE_N_PRESETS {n}u\n\nconst char LIVE_NAMES[{n}][16] = {{{names}}};\nconst int8_t LIVE_LUT_IDX[{n}] = {{{idxs}}};\n\nconst uint32_t EC_GAMMA_STOCK[96] = {{{stock_ec}}};\nconst uint16_t FC_GAMMA_STOCK[96] = {{{stock_fc}}};\nconst int16_t COLOR_A0_STOCK[9] = {{{stock_a0}}};\nconst int16_t COLOR_A1_STOCK[9] = {{{stock_a1}}};\nconst int16_t COLOR_B2_STOCK[9] = {{{stock_b2}}};\n\nconst uint32_t LIVE_EC[{pn}][96] = {{{ec}}};\nconst uint16_t LIVE_FC[{pn}][96] = {{{fc}}};\nconst int16_t LIVE_A0[{pn}][9] = {{{a0}}};\nconst int16_t LIVE_A1[{pn}][9] = {{{a1}}};\nconst int16_t LIVE_B2[{pn}][9] = {{{b2}}};\nconst uint8_t LIVE_REFADR[{pn}][1215] = {{{luts}}};\n",
        label = prof.label,
        ecpa = prof.ec_gamma_pa,
        fcpa = prof.fc_gamma_pa,
        a0pa = prof.color_a0_pa,
        a1pa = prof.color_a1_pa,
        b2pa = prof.color_b2_pa,
        cc = prof.cc_tuning_pa,
        refpa = prof.refadr0_pa,
        names = names.join(","),
        stock_ec = fmt_hex_u32(&stock.stock_ec),
        stock_fc = fmt_dec(&stock.stock_fc),
        stock_a0 = fmt_dec(&stock.stock_a0),
        stock_a1 = fmt_dec(&stock.stock_a1),
        stock_b2 = fmt_dec(&stock.stock_b2),
        pn = looks.len(),
        ec = ec.join(","),
        fc = fc.join(","),
        a0 = a0.join(","),
        a1 = a1.join(","),
        b2 = b2.join(","),
        luts = luts.join(","),
    ))
}

#[derive(Clone, Debug)]
pub struct Cube {
    pub size: usize,
    pub domain_min: [f64; 3],
    pub domain_max: [f64; 3],
    pub table: Vec<f64>,
    pub title: String,
}

pub fn parse_cube(text: &str) -> Result<Cube> {
    let mut size = None;
    let mut dmin = [0.0; 3];
    let mut dmax = [1.0; 3];
    let mut title = String::new();
    let mut rows = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let parts: Vec<&str> = line.split_whitespace().collect();
        let key = parts[0].to_ascii_uppercase();
        match key.as_str() {
            "TITLE" => title = line[5..].trim().trim_matches('"').to_string(),
            "LUT_3D_SIZE" => size = Some(parts[1].parse::<usize>().map_err(|e| err(e.to_string()))?),
            "LUT_1D_SIZE" => return Err(err("1D .cube LUTs are not supported")),
            "LUT_3D_INPUT_RANGE" => {
                let low: f64 = parts[1].parse().map_err(|e: std::num::ParseFloatError| err(e.to_string()))?;
                let high: f64 = parts[2].parse().map_err(|e: std::num::ParseFloatError| err(e.to_string()))?;
                dmin = [low; 3];
                dmax = [high; 3];
            }
            "DOMAIN_MIN" => dmin = parse3(&parts[1..])?,
            "DOMAIN_MAX" => dmax = parse3(&parts[1..])?,
            other if other.starts_with("LUT_") || other.starts_with("DOMAIN_") => {}
            _ => {
                let r: f64 = parts[0].parse().map_err(|e: std::num::ParseFloatError| err(e.to_string()))?;
                let g: f64 = parts[1].parse().map_err(|e: std::num::ParseFloatError| err(e.to_string()))?;
                let b: f64 = parts[2].parse().map_err(|e: std::num::ParseFloatError| err(e.to_string()))?;
                rows.extend([r, g, b]);
            }
        }
    }
    let size = size.ok_or_else(|| err("LUT_3D_SIZE must be at least 2"))?;
    if size < 2 || rows.len() != size * size * size * 3 {
        return Err(err(format!("expected {} rows", size.pow(3))));
    }
    let mut table = vec![0.0; rows.len()];
    for b in 0..size {
        for g in 0..size {
            for r in 0..size {
                let src = ((b * size + g) * size + r) * 3;
                let dst = ((r * size + g) * size + b) * 3;
                table[dst..dst + 3].copy_from_slice(&rows[src..src + 3]);
            }
        }
    }
    Ok(Cube { size, domain_min: dmin, domain_max: dmax, table, title })
}

fn parse3(parts: &[&str]) -> Result<[f64; 3]> {
    Ok([
        parts[0].parse().map_err(|e: std::num::ParseFloatError| err(e.to_string()))?,
        parts[1].parse().map_err(|e: std::num::ParseFloatError| err(e.to_string()))?,
        parts[2].parse().map_err(|e: std::num::ParseFloatError| err(e.to_string()))?,
    ])
}

pub fn cube_to_text(cube: &Cube) -> String {
    let mut out = format!(
        "TITLE \"{}\"\nLUT_3D_SIZE {}\nDOMAIN_MIN {} {} {}\nDOMAIN_MAX {} {} {}\n",
        cube.title, cube.size, cube.domain_min[0], cube.domain_min[1], cube.domain_min[2], cube.domain_max[0], cube.domain_max[1], cube.domain_max[2]
    );
    let n = cube.size;
    for b in 0..n {
        for g in 0..n {
            for r in 0..n {
                let i = ((r * n + g) * n + b) * 3;
                out.push_str(&format!("{:.6} {:.6} {:.6}\n", cube.table[i], cube.table[i + 1], cube.table[i + 2]));
            }
        }
    }
    out
}

pub fn sample_cube(cube: &Cube, raw: &[f64]) -> Vec<f64> {
    let mut unit = Vec::with_capacity(raw.len());
    for px in raw.chunks(3) {
        for c in 0..3 {
            let span = cube.domain_max[c] - cube.domain_min[c];
            unit.push((px[c] - cube.domain_min[c]) / span * (cube.size - 1) as f64);
        }
    }
    bionz_core::trilinear(&cube.table, [cube.size; 3], 3, &unit)
}

/// Planes are dY, dCb, dCr in natural node order. This is the Film Sims RefAdr layout.
pub fn pack_refadr(delta: &[i8]) -> Vec<u8> {
    let nodes = delta.len() / 3;
    let mut out = Vec::with_capacity(nodes * 3);
    for plane in 0..3 {
        for node in 0..nodes {
            out.push(delta[node * 3 + plane] as u8);
        }
    }
    out
}

/// `fit @ stock`, then the diagonal restores the stock row sum. Python `round` is half-to-even.
pub fn compose_stock_matrix(stock: &[i16], fit: &[i16], matrix_q: i32) -> [i16; 9] {
    let q = matrix_q as f64;
    let mut out = [0i16; 9];
    for row in 0..3 {
        let mut values = [0i64; 3];
        for col in 0..3 {
            let mut acc = 0.0;
            for k in 0..3 {
                acc += fit[row * 3 + k] as f64 / q * stock[k * 3 + col] as f64;
            }
            values[col] = bionz_core::np_rint(acc) as i64;
        }
        let target: i64 = stock[row * 3..row * 3 + 3].iter().map(|v| *v as i64).sum();
        let sum: i64 = values.iter().sum();
        values[row] -= sum - target;
        for col in 0..3 {
            out[row * 3 + col] = values[col] as i16;
        }
    }
    out
}

fn sample_gamma(x: f64, gamma: &[u16], gamma_max: f64) -> f64 {
    let n = gamma.len();
    let pos = x.clamp(0.0, 1.0) * (n - 1) as f64;
    let i = (pos.floor() as usize).min(n - 2);
    let t = pos - i as f64;
    let a = gamma[i] as f64 / gamma_max;
    let b = gamma[i + 1] as f64 / gamma_max;
    a * (1.0 - t) + b * t
}

fn linear_to_srgb(x: f64) -> f64 {
    let x = x.clamp(0.0, 1.0);
    if x <= 0.003_130_8 {
        12.92 * x
    } else {
        1.055 * x.powf(1.0 / 2.4) - 0.055
    }
}

/// Evaluate a fitted 96-knot display-referred curve directly at each Movie EC knot position
/// `linear_to_srgb(knot / 8191.0)` so highlights above `stock_ec`'s plateau keep smooth gradation.
pub fn apply_movie_gamma(gamma: &[u16], gamma_max: i32, exponents: &[u8; 9]) -> Vec<u32> {
    let max = gamma_max as f64;
    let Some(knots) = bionz_core::ec_knot_positions(exponents) else {
        return Vec::new();
    };
    let mut ec = Vec::with_capacity(96);
    let mut last = 0i64;
    for k in knots {
        let s = linear_to_srgb(k as f64 / 8191.0);
        let mapped = bionz_core::np_rint(sample_gamma(s, gamma, max) * 1023.0).clamp(0.0, 1023.0) as i64;
        last = last.max(mapped);
        ec.push((last as u32) << 6);
    }
    ec
}

/// Map stock EC (`10-bit << 6`) and FC (10-bit) through a fitted 96-knot curve, then force them monotonic.
pub fn apply_fitted_gamma(stock_ec: &[u32], stock_fc: &[u16], gamma: &[u16], gamma_max: i32) -> (Vec<u32>, Vec<u16>) {
    let max = gamma_max as f64;
    let mut fc = Vec::with_capacity(stock_fc.len());
    let mut last = 0i64;
    for value in stock_fc {
        let mapped = bionz_core::np_rint(sample_gamma(*value as f64 / 1023.0, gamma, max) * 1023.0).clamp(0.0, 1023.0) as i64;
        last = last.max(mapped);
        fc.push(last as u16);
    }
    let mut ec = Vec::with_capacity(stock_ec.len());
    last = 0;
    for value in stock_ec {
        let code = (value >> 6) as f64 / 1023.0;
        let mapped = bionz_core::np_rint(sample_gamma(code, gamma, max) * 1023.0).clamp(0.0, 1023.0) as i64;
        last = last.max(mapped);
        ec.push((last as u32) << 6);
    }
    (ec, fc)
}

#[cfg(test)]
fn to_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push(HEX[(b >> 4) as usize] as char);
        out.push(HEX[(b & 0xf) as usize] as char);
    }
    out
}

/// The same chain with the target's stock tables and an empty residual LUT: the camera without the look.
pub fn stock_params_from_compiled(table: &CompiledTables) -> bionz_core::IntegerParams {
    let mut stock = table.clone();
    stock.ec = table.stock_ec.clone();
    stock.fc = table.stock_fc.clone();
    stock.a0 = table.stock_a0.clone();
    stock.a1 = table.stock_a1.clone();
    stock.b2 = table.stock_b2.clone();
    stock.refadr = vec![0; table.refadr.len()];
    stock.base_matrix = table.stock_base_matrix.clone();
    integer_params_from_compiled(&stock)
}

const Q10_IDENTITY: [i32; 9] = [1024, 0, 0, 0, 1024, 0, 0, 0, 1024];

fn invert3(m: [[f64; 3]; 3]) -> Option<[[f64; 3]; 3]> {
    let det = m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
    if det.abs() < 1e-9 {
        return None;
    }
    let inv = [
        [
            (m[1][1] * m[2][2] - m[1][2] * m[2][1]) / det,
            (m[0][2] * m[2][1] - m[0][1] * m[2][2]) / det,
            (m[0][1] * m[1][2] - m[0][2] * m[1][1]) / det,
        ],
        [
            (m[1][2] * m[2][0] - m[1][0] * m[2][2]) / det,
            (m[0][0] * m[2][2] - m[0][2] * m[2][0]) / det,
            (m[0][2] * m[1][0] - m[0][0] * m[1][2]) / det,
        ],
        [
            (m[1][0] * m[2][1] - m[1][1] * m[2][0]) / det,
            (m[0][1] * m[2][0] - m[0][0] * m[2][1]) / det,
            (m[0][0] * m[1][1] - m[0][1] * m[1][0]) / det,
        ],
    ];
    Some(inv)
}

/// `M_sim * M_stock^-1` in Q10, rounded the way Python's `round` does, with each row adjusted on
/// the diagonal so it sums to `1024`. This is the look's *relative* matrix: applied after
/// `cam_to_srgb` it must not apply the stock A0 green boost a second time.
pub fn relative_q10_matrix(sim: &[i16], stock: &[i16]) -> [i32; 9] {
    if sim.len() < 9 || stock.len() < 9 {
        return Q10_IDENTITY;
    }
    let mut ms = [[0.0f64; 3]; 3];
    let mut mk = [[0.0f64; 3]; 3];
    for r in 0..3 {
        for c in 0..3 {
            ms[r][c] = sim[r * 3 + c] as f64 / 1024.0;
            mk[r][c] = stock[r * 3 + c] as f64 / 1024.0;
        }
    }
    let Some(inv) = invert3(mk) else { return Q10_IDENTITY };
    let mut out = [0i32; 9];
    for r in 0..3 {
        for c in 0..3 {
            let acc = ms[r][0] * inv[0][c] + ms[r][1] * inv[1][c] + ms[r][2] * inv[2][c];
            out[r * 3 + c] = bionz_core::np_rint(acc * 1024.0) as i32;
        }
        let sum: i32 = out[r * 3..r * 3 + 3].iter().sum();
        out[r * 3 + r] -= sum - 1024;
    }
    out
}

/// Compose the preset's FC transfer (defined by `sim` over `stock`'s output axis) onto `base`.
/// `out[i] = interp(sim, k)` where `k` is the index whose `stock[k]` equals `base[i]`, i.e. the
/// relative tone delta the film look applies in the stock curve's own output space. Monotone by
/// construction; `sim == stock` returns `base` unchanged.
pub fn compose_relative_fc(base: &[i32; 96], sim: &[u16], stock: &[u16]) -> [i32; 96] {
    if sim.len() < 96 || stock.len() < 96 {
        return *base;
    }
    let simf: Vec<f64> = sim[..96].iter().map(|v| *v as f64).collect();
    let mut out = [0i32; 96];
    for (i, b) in base.iter().enumerate() {
        let x = (*b as f64).clamp(0.0, 1023.0);
        let mut idx = if x <= stock[0] as f64 {
            0.0
        } else {
            95.0
        };
        if x > stock[0] as f64 && x < stock[95] as f64 {
            for k in 0..95 {
                let (a, c) = (stock[k] as f64, stock[k + 1] as f64);
                if x >= a && x <= c {
                    idx = if c > a { k as f64 + (x - a) / (c - a) } else { (k + 1) as f64 };
                    break;
                }
            }
        }
        let k = idx.floor().clamp(0.0, 94.0) as usize;
        let t = (idx - k as f64).clamp(0.0, 1.0);
        let y = simf[k] * (1.0 - t) + simf[k + 1] * t;
        out[i] = bionz_core::np_rint(y).clamp(0.0, 1023.0) as i32;
    }
    for i in 1..96 {
        out[i] = out[i].max(out[i - 1]);
    }
    out
}

/// Fill the still-JPEG chain into `params`: `cam_to_srgb` as `base_matrix` (unless the preset
/// embeds one), the preset's relative Q10 look matrix, and its relative tone composed onto the
/// calibrated still Standard FC. EC is bypassed and FC runs; output is full range.
pub fn apply_still_jpeg_look(params: &mut bionz_core::IntegerParams, table: &CompiledTables, cam_to_srgb: Option<[i32; 9]>) {
    if let Some(m) = &table.base_matrix {
        for (slot, v) in params.base_matrix.iter_mut().zip(m) {
            *slot = *v as i32;
        }
    } else if let Some(m) = cam_to_srgb {
        params.base_matrix = m;
    }
    params.color_matrix = relative_q10_matrix(&table.a0, &table.stock_a0);
    params.fc = compose_relative_fc(&bionz_core::STILL_STANDARD_FC, &table.fc, &table.stock_fc);
    params.bypass[6] = true;
    params.bypass[7] = false;
    params.legal_range = false;
}

/// A full `IntegerParams` for the still-JPEG look chain (see [`apply_still_jpeg_look`]).
pub fn still_jpeg_params_from_compiled(table: &CompiledTables, cam_to_srgb: Option<[i32; 9]>) -> bionz_core::IntegerParams {
    let mut params = integer_params_from_compiled(table);
    apply_still_jpeg_look(&mut params, table, cam_to_srgb);
    params
}

/// The same still-JPEG chain with the calibrated stock curve and an identity matrix: side B.
pub fn still_jpeg_stock_params(cam_to_srgb: Option<[i32; 9]>) -> bionz_core::IntegerParams {
    let mut params = bionz_core::IntegerParams::default();
    if let Some(m) = cam_to_srgb {
        params.base_matrix = m;
    }
    params.color_matrix = Q10_IDENTITY;
    params.fc = bionz_core::STILL_STANDARD_FC;
    params.bypass[6] = true;
    params.bypass[7] = false;
    params.legal_range = false;
    params
}

/// Fill the Movie EC chain: `base_matrix` from the preset BZX2 (or `cam_to_srgb`), the preset's
/// absolute A0 matrix, EC record-31 knots and the residual LUT. FC is bypassed; the encoder runs
/// legal or full range as requested. Mirrors [`apply_still_jpeg_look`] for the Movie domain.
pub fn apply_movie_look(
    params: &mut bionz_core::IntegerParams,
    table: &CompiledTables,
    cam_to_srgb: Option<[i32; 9]>,
    legal: bool,
) {
    if let Some(m) = &table.base_matrix {
        for (slot, v) in params.base_matrix.iter_mut().zip(m) {
            *slot = *v as i32;
        }
    } else if let Some(m) = cam_to_srgb {
        params.base_matrix = m;
    }
    for index in 0..9 {
        if let Some(value) = table.a0.get(index) {
            params.color_matrix[index] = *value as i32;
        }
    }
    if let Some(knots) = table.ec_exponents.as_ref().and_then(bionz_core::ec_knot_positions) {
        params.ec_knots = knots;
    }
    params.bypass[6] = false;
    params.bypass[7] = true;
    params.legal_range = legal;
}

/// Apply the advanced routing overrides on top of a mode-built chain.
///
/// `Auto` keeps whatever the mode set (preset BZX2 or `cam_to_srgb` for the base; the mode's own
/// matrix for the colour stage). `lut_strength` scales the RefAdr delta toward zero without touching
/// the stock tables. `ec_record = Some(31)` selects record-31 knots, `Some(0)` uniform knots.
pub fn apply_routing(
    params: &mut bionz_core::IntegerParams,
    table: &CompiledTables,
    routing: &bionz_core::IspRouting,
    cam_to_srgb: Option<[i32; 9]>,
) {
    use bionz_core::BaseMatrixSource;
    match routing.base_matrix_source {
        BaseMatrixSource::Auto => {
            if let Some(m) = &table.base_matrix {
                for (slot, v) in params.base_matrix.iter_mut().zip(m) {
                    *slot = *v as i32;
                }
            }
        }
        BaseMatrixSource::CameraToSrgb => {
            if let Some(m) = cam_to_srgb {
                params.base_matrix = m;
            }
        }
        BaseMatrixSource::PresetBaseMatrix => {
            params.base_matrix = match &table.base_matrix {
                Some(m) if m.len() >= 9 => {
                    let mut out = Q10_IDENTITY;
                    for i in 0..9 {
                        out[i] = m[i] as i32;
                    }
                    out
                }
                _ => Q10_IDENTITY,
            };
        }
        BaseMatrixSource::Identity => params.base_matrix = Q10_IDENTITY,
    }
    use bionz_core::ColorMatrixMode;
    let absolute = |m: &[i16]| -> [i32; 9] {
        let mut out = Q10_IDENTITY;
        for i in 0..9 {
            if let Some(v) = m.get(i) {
                out[i] = *v as i32;
            }
        }
        out
    };
    params.color_matrix = match routing.color_matrix_mode {
        ColorMatrixMode::Auto => params.color_matrix,
        ColorMatrixMode::RelativeToStock => relative_q10_matrix(&table.a0, &table.stock_a0),
        ColorMatrixMode::AbsoluteA0 => absolute(&table.a0),
        ColorMatrixMode::AbsoluteA1 => absolute(&table.a1),
        ColorMatrixMode::AbsoluteB2 => absolute(&table.b2),
    };
    bionz_core::scale_lut(&mut params.delta, routing.lut_strength);
    match routing.ec_record {
        Some(31) => {
            if let Some(knots) = bionz_core::ec_knot_positions(&bionz_core::EC_RECORD31_EXPONENTS) {
                params.ec_knots = knots;
            }
        }
        Some(0) => params.ec_knots = bionz_core::uniform_ec_knots(),
        _ => {}
    }
}

impl Bzlook {
    /// The Photo (`compiled/<target>.bin`) or Movie (`compiled/<target>_movie.bin`) table.
    pub fn table_for(&self, target: &str, movie: bool) -> Option<&CompiledTables> {
        let set = if movie { &self.compiled_movie } else { &self.compiled };
        set.iter().find(|(k, _)| k == target).map(|(_, t)| t)
    }

    /// Movie table with the documented fallback to the Photo table for older presets.
    pub fn movie_table_or_photo(&self, target: &str) -> Option<(&CompiledTables, bool)> {
        self.table_for(target, true).map(|t| (t, true)).or_else(|| self.table_for(target, false).map(|t| (t, false)))
    }
}


pub fn integer_params_from_compiled(table: &CompiledTables) -> bionz_core::IntegerParams {
    let mut params = bionz_core::IntegerParams::default();
    if let Some(m) = &table.base_matrix {
        for (slot, v) in params.base_matrix.iter_mut().zip(m) {
            *slot = *v as i32;
        }
    }
    if let Some(knots) = table.ec_exponents.as_ref().and_then(bionz_core::ec_knot_positions) {
        params.ec_knots = knots;
    }
    if (1..=7).contains(&table.lut_level) {
        params.lut_level = table.lut_level as i32;
    }
    for (index, value) in table.ec.iter().take(96).enumerate() {
        params.ec[index] = ((*value >> 6) as i32).clamp(0, 1023) * 4095 / 1023;
    }
    for (index, value) in table.fc.iter().take(96).enumerate() {
        params.fc[index] = (*value as i32).clamp(0, 1023);
    }
    for index in 0..9 {
        if let Some(value) = table.a0.get(index) {
            params.color_matrix[index] = *value as i32;
        }
    }
    let nodes = 5 * 9 * 9;
    if table.refadr.len() >= nodes * 3 {
        for node in 0..nodes {
            params.delta[node * 3] = table.refadr[node] as i8;
            params.delta[node * 3 + 1] = table.refadr[nodes + node] as i8;
            params.delta[node * 3 + 2] = table.refadr[nodes * 2 + node] as i8;
        }
    }
    params
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn root() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..")
    }

    #[test]
    fn a6lt_matches_golden_blob() {
        let profile = Profile::a6400();
        let stages: serde_json::Value = serde_json::from_str(include_str!("../../../../artifacts/tests/bionz-golden/stages.json")).unwrap();
        let gamma: Vec<u16> = stages["gamma_u16"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap() as u16).collect();
        let matrix_v: Vec<i16> = stages["matrix_i16"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap() as i16).collect();
        let mut matrix = [0i16; 9];
        matrix.copy_from_slice(&matrix_v);
        let delta: Vec<i8> = stages["delta_i8"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap() as i8).collect();
        let preset = OraclePreset { gamma, matrix, delta };
        let blob = pack_a6lt(&preset, &profile).unwrap();
        assert_eq!(to_hex(&blob), stages["pack_preset_hex"].as_str().unwrap());
        let back = unpack_a6lt(&blob, &profile).unwrap();
        assert_eq!(back.gamma, preset.gamma);
        assert_eq!(back.matrix, preset.matrix);
        assert_eq!(back.delta, preset.delta);
        let a6lt = std::fs::read(root().join("a6400-lut-converter/exported/arri-logc3-awg3.a6lt")).unwrap();
        let imported = unpack_a6lt(&a6lt, &profile).unwrap();
        let again = pack_a6lt(&imported, &profile).unwrap();
        assert_eq!(again, a6lt);
    }

    #[test]
    fn headers_match_python() {
        let order = [
            "classic-chrome",
            "classic-neg",
            "portra-400",
            "gold-200",
            "vision3-800t",
            "eterna",
            "eterna-bleach-bypass",
            "pro-negative",
            "reala-ace",
            "velvia",
            "astia",
            "acros",
        ];
        let data_h = root().join("artifacts/tests/bionz-golden/film_sim_data.h");
        let live_h = root().join("artifacts/tests/bionz-golden/film_sim_live_data.h");
        if !data_h.is_file() || !live_h.is_file() {
            return;
        }
        let dir = root().join("mods/film_sims/presets");
        let mut looks = Vec::new();
        for name in order {
            let look = load_bzlook(&dir.join(format!("{name}.bzlook"))).unwrap();
            let table = look.compiled.iter().find(|(k, _)| k == "a6400").unwrap().1.clone();
            if name == "classic-chrome" {
                let header = emit_film_sim_data(&look.meta.name, "a6400", &table).unwrap();
                let expect = std::fs::read_to_string(&data_h).unwrap().replace("\r\n", "\n");
                assert_eq!(header, expect);
            }
            looks.push((look.meta.name, table));
        }
        let live = emit_live_header("a6400", &looks).unwrap();
        let expect = std::fs::read_to_string(&live_h).unwrap().replace("\r\n", "\n");
        assert_eq!(live, expect);
    }

    #[test]
    fn cube_roundtrip_sample() {
        let text = "LUT_3D_SIZE 2\nLUT_3D_INPUT_RANGE 0.2 0.8\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n";
        let cube = parse_cube(text).unwrap();
        let sampled = sample_cube(&cube, &[0.2, 0.5, 0.8]);
        assert!((sampled[0] - 0.0).abs() < 1e-9);
        assert!((sampled[1] - 0.5).abs() < 1e-9);
        assert!((sampled[2] - 1.0).abs() < 1e-9);
        let again = parse_cube(&cube_to_text(&cube)).unwrap();
        assert_eq!(again.table.len(), cube.table.len());
    }

    #[test]
    fn identity_matrix_keeps_stock_row_sums() {
        let stock = [1241i16, -205, -12, 62, 1116, -154, 30, -251, 1245];
        let fit = [1024, 0, 0, 0, 1024, 0, 0, 0, 1024];
        let out = compose_stock_matrix(&stock, &fit, 1024);
        assert_eq!(out, stock);
        let gamma: Vec<u16> = (0..96).map(|i| (i * 4095 / 95) as u16).collect();
        let (ec, fc) = apply_fitted_gamma(&[0, 1023 << 6], &[0, 1023], &gamma, 4095);
        assert!(ec[0] <= ec[1]);
        assert!(fc[0] <= fc[1]);
        let delta = [1i8, 2, 3, 4, 5, 6];
        assert_eq!(pack_refadr(&delta), vec![1, 4, 2, 5, 3, 6]);
        let look = load_bzlook(&root().join("mods/film_sims/presets/classic-chrome.bzlook")).unwrap();
        let table = &look.compiled.iter().find(|(key, _)| key == "a6400").unwrap().1;
        let params = integer_params_from_compiled(table);
        assert_eq!(params.color_matrix[0], table.a0[0] as i32);
        assert!(params.delta.iter().any(|value| *value != 0));
    }

    #[test]
    fn movie_variant_round_trips_and_routing_overrides_the_chain() {
        use bionz_core::{BaseMatrixSource, ColorMatrixMode, IspRouting};
        let path = root().join("mods/film_sims/presets/gold-200.bzlook");
        let look = load_bzlook(&path).unwrap();
        let (movie, is_movie) = look.movie_table_or_photo("a6400").unwrap();
        assert!(is_movie, "shipped presets carry a Movie variant");
        assert_eq!(movie.ec_exponents, Some(bionz_core::EC_RECORD31_EXPONENTS));
        let cam = [1241, -205, -12, 62, 1116, -154, 30, -251, 1245];
        let mut params = integer_params_from_compiled(movie);
        apply_movie_look(&mut params, movie, Some(cam), true);
        assert!(!params.bypass[6]);
        assert!(params.bypass[7]);
        assert!(params.legal_range);
        assert_eq!(params.base_matrix, cam, "Movie base falls back to cam_to_srgb when the preset has no BZX2 base");
        assert_eq!(params.ec_knots, bionz_core::ec_knot_positions(&bionz_core::EC_RECORD31_EXPONENTS).unwrap());
        // Manual routing: identity base, absolute A1, LUT off, uniform knots.
        let routing = IspRouting {
            base_matrix_source: BaseMatrixSource::Identity,
            color_matrix_mode: ColorMatrixMode::AbsoluteA1,
            lut_strength: 0.0,
            ec_record: Some(0),
        };
        apply_routing(&mut params, movie, &routing, Some(cam));
        assert_eq!(params.base_matrix, Q10_IDENTITY);
        for i in 0..9 {
            assert_eq!(params.color_matrix[i], movie.a1[i] as i32);
        }
        assert!(params.delta.iter().all(|v| *v == 0));
        assert_eq!(params.ec_knots, bionz_core::uniform_ec_knots());
        // A preset without a Movie variant falls back to its Photo table.
        let mut no_movie = look.clone();
        no_movie.compiled_movie.clear();
        let (fallback, is_movie) = no_movie.movie_table_or_photo("a6400").unwrap();
        assert!(!is_movie);
        assert_eq!(fallback.a0, look.compiled[0].1.a0);
        // Round-trip preserves both variants.
        let dir = root().join("isp-studio/target/preset-movie-roundtrip");
        std::fs::create_dir_all(&dir).unwrap();
        let out = dir.join("gold-200.bzlook");
        store_bzlook(&out, &look).unwrap();
        let back = load_bzlook(&out).unwrap();
        assert_eq!(back.compiled_movie.len(), look.compiled_movie.len());
        assert_eq!(back.compiled_movie[0].1.ec, look.compiled_movie[0].1.ec);
        assert_eq!(back.compiled_movie[0].1.refadr, look.compiled_movie[0].1.refadr);
        assert_eq!(back.compiled[0].1.fc, look.compiled[0].1.fc);
    }

    #[test]
    fn movie_chain_is_legal_range_and_differs_from_stock() {
        let look = load_bzlook(&root().join("mods/film_sims/presets/gold-200.bzlook")).unwrap();
        let (movie, _) = look.movie_table_or_photo("a6400").unwrap();
        let cam = [1241, -205, -12, 62, 1116, -154, 30, -251, 1245];
        let mut look_params = integer_params_from_compiled(movie);
        apply_movie_look(&mut look_params, movie, Some(cam), true);
        let mut stock_params = stock_params_from_compiled(movie);
        stock_params.bypass[6] = false;
        stock_params.bypass[7] = true;
        stock_params.legal_range = true;
        if stock_params.base_matrix == Q10_IDENTITY {
            stock_params.base_matrix = cam;
        }
        // A neutral ramp through the Movie EC + matrix + LUT.
        let pixels: Vec<bionz_core::RgbI> = (0..256)
            .map(|i| {
                let v = i * 4095 / 255;
                bionz_core::RgbI { r: v, g: v, b: v }
            })
            .collect();
        let look_taps = bionz_core::integer_pipeline(&pixels, 256, &look_params);
        let stock_taps = bionz_core::integer_pipeline(&pixels, 256, &stock_params);
        assert!(look_taps.iter().all(|t| (16..=235).contains(&t.encoded_y)), "Y must stay legal");
        assert!(look_taps.iter().all(|t| (16..=240).contains(&t.encoded_cb) && (16..=240).contains(&t.encoded_cr)));
        assert!(look_taps.iter().zip(&stock_taps).any(|(a, b)| a.encoded_y != b.encoded_y), "the look must change the Movie chain");
    }

    #[test]
    fn shipped_presets_carry_the_verified_avcam_stock() {
        let sha = stock_a6400::avcam_sha256_bytes();
        let order = [
            "classic-chrome",
            "classic-neg",
            "portra-400",
            "gold-200",
            "vision3-800t",
            "eterna",
            "eterna-bleach-bypass",
            "pro-negative",
            "reala-ace",
            "velvia",
            "astia",
            "acros",
        ];
        let dir = root().join("mods/film_sims/presets");
        for name in order {
            let look = load_bzlook(&dir.join(format!("{name}.bzlook"))).unwrap();
            for target in ["a6400", "a6100"] {
                let table = &look.compiled.iter().find(|(k, _)| k == target).unwrap_or_else(|| panic!("{}: no {target}", look.meta.name)).1;
                assert!(!table.has_synthetic_stock(), "{} {target}", look.meta.name);
                // a6100 reuses the a6400 capture because the cross-body stock tables are identical.
                assert_eq!(table.avcam_sha256, sha, "{} {target}", look.meta.name);
                assert_eq!(table.stock_ec, stock_a6400::EC_REC28_V1.to_vec(), "{} {target} stock EC", look.meta.name);
                assert_eq!(table.stock_fc, stock_a6400::FC_TBL0.to_vec(), "{} {target} stock FC", look.meta.name);
                assert_eq!(table.stock_a0, stock_a6400::STOCK_A0.to_vec(), "{} {target} stock A0", look.meta.name);
                assert_eq!(table.stock_a1, stock_a6400::STOCK_A1.to_vec(), "{} {target} stock A1", look.meta.name);
                assert_eq!(table.stock_b2, stock_a6400::STOCK_B2.to_vec(), "{} {target} stock B2", look.meta.name);
            }
        }
    }

    #[test]
    fn relative_matrix_is_identity_for_stock_and_sums_to_1024() {
        let stock = [1241i16, -205, -12, 62, 1116, -154, 30, -251, 1245];
        assert_eq!(relative_q10_matrix(&stock, &stock), Q10_IDENTITY);
        let look = load_bzlook(&root().join("mods/film_sims/presets/gold-200.bzlook")).unwrap();
        let table = &look.compiled.iter().find(|(key, _)| key == "a6400").unwrap().1;
        let rel = relative_q10_matrix(&table.a0, &table.stock_a0);
        for row in 0..3 {
            assert_eq!(rel[row * 3..row * 3 + 3].iter().sum::<i32>(), 1024);
        }
        // Neutral gray stays neutral: rows also keep the column sums near the Q10 identity.
        assert!(rel.iter().all(|v| v.abs() < 4096));
    }

    #[test]
    fn relative_fc_is_identity_when_the_preset_curve_matches_stock() {
        let base = bionz_core::STILL_STANDARD_FC;
        let stock: Vec<u16> = (0..96).map(|i| (i * 1023 / 95) as u16).collect();
        let composed = compose_relative_fc(&base, &stock, &stock);
        assert_eq!(composed, base);
    }

    #[test]
    fn still_jpeg_look_uses_cam_matrix_relative_look_and_stock_fc_domain() {
        let look = load_bzlook(&root().join("mods/film_sims/presets/gold-200.bzlook")).unwrap();
        let table = &look.compiled.iter().find(|(key, _)| key == "a6400").unwrap().1;
        let cam = [1241, -205, -12, 62, 1116, -154, 30, -251, 1245];
        let params = still_jpeg_params_from_compiled(table, Some(cam));
        assert_eq!(params.base_matrix, cam, "sensor RGB must be calibrated once");
        assert_eq!(params.bypass[6], true, "still look bypasses EC");
        assert_eq!(params.bypass[7], false, "still look runs FC");
        assert!(!params.legal_range);
        assert!(params.fc.windows(2).all(|w| w[0] <= w[1]), "FC must be monotone");
        assert!(params.fc.iter().all(|v| (0..=1023).contains(v)));
        // The relative look curve must not inject the log EC domain: both ends stay anchored.
        // The shipped Gold 200 carries black_fade 0.022 (22 codes); Phase 2 lowers it to 0.006.
        assert!(params.fc[0] <= 32, "black {}", params.fc[0]);
        assert!(params.fc[95] >= 980, "white {}", params.fc[95]);
        // A stock-only chain keeps the calibrated curve untouched.
        let stock = still_jpeg_stock_params(Some(cam));
        assert_eq!(stock.base_matrix, cam);
        assert_eq!(stock.color_matrix, Q10_IDENTITY);
        assert_eq!(stock.fc, bionz_core::STILL_STANDARD_FC);
        assert!(stock.bypass[6] && !stock.bypass[7]);
    }
}
