//! Pure-Rust `SMOD v2` packer for Film Simulation mods.
//!
//! Two ARM Thumb payloads are compiled once, during development, and committed under `assets/`:
//!
//! * `film_sim_template.blob` (`mods/film_sims/template/build_template_wsl.sh`) - the single-look
//!   lifecycle. Every target address and table lives in one `struct sim_payload`, so
//!   [`emit_smod_v2`] clones it, writes the selected preset and returns a `.mod`.
//! * `film_sim_live_template.mod` (`mods/film_sims/live_picker/build_live_template_wsl.sh`) - the
//!   Live Picker skeleton: `event_push` hook, 48-byte bss and the RLC1 relocation table are already
//!   packed. [`emit_live_smod_v2`] clones it and rewrites only the name, the `struct live_payload`
//!   (up to [`LIVE_MAX_LOOKS`] looks) and the code checksum.
//!
//! Both are written to `<SD>:\mods\` and loaded by Mod Loader v2. No compiler, WSL or Python is
//! involved at run time.

use std::collections::BTreeMap;
use std::sync::OnceLock;

use serde::Deserialize;

use crate::{err, known_target, CompiledTables, Result};

pub const SMOD_MAGIC: u32 = 0x444F_4D53; // "SMOD"
pub const SMOD_VERSION: u32 = 2;
/// Command item: pressing the menu entry runs the mod and closes the menu (no ON/OFF switch value).
pub const FLAG_ACTION: u32 = 16;
/// The file carries an RLC1 relocation table after the code (Mod Loader v2 fixes the literals).
pub const FLAG_RELOC: u32 = 4;
/// `SMOD v2` header: magic, version, flags, 32-byte name, then six words.
pub const HEADER_LEN: usize = 68;
/// `SMOD v2` hook entry size.
pub const HOOK_LEN: usize = 40;
/// `SMOD v2` rejects names longer than 31 ASCII bytes.
const MAX_NAME: usize = 31;
/// Looks the Live Picker payload can carry (slot 0 is "Stock (Off)").
pub const LIVE_MAX_LOOKS: usize = 15;
/// Mod Loader v2 menu entry name for the Live Picker.
pub const LIVE_MOD_NAME: &str = "Film Simulations";
/// Mod Loader v2 file name for the Live Picker.
pub const LIVE_MOD_FILENAME: &str = "FilmSimulations.mod";
/// Fixed log path of the Live Picker (`film_sim_live.c` keeps the same one).
const LIVE_LOG_PATH: &str = "/tmp_bt/mods/film_sim_live.log";

const TEMPLATE_BIN: &[u8] = include_bytes!("../assets/film_sim_template.blob");
const TEMPLATE_JSON: &str = include_str!("../assets/film_sim_template.json");
const LIVE_TEMPLATE_MOD: &[u8] = include_bytes!("../assets/film_sim_live_template.mod");
const LIVE_TEMPLATE_JSON: &str = include_str!("../assets/film_sim_live_template.json");

/// FNV-1a 32-bit, identical to `tools/research/pack_mod.py` and the loader's checksum.
pub fn fnv1a_32(data: &[u8]) -> u32 {
    let mut hash = 0x811C_9DC5u32;
    for byte in data {
        hash = (hash ^ *byte as u32).wrapping_mul(0x0100_0193);
    }
    hash
}

#[derive(Deserialize)]
struct TemplateRecord {
    schema: String,
    code_len: usize,
    code_fnv1a_32: String,
    on_off: String,
    off_off: String,
    payload: TemplatePayloadRecord,
}

#[derive(Deserialize)]
struct TemplatePayloadRecord {
    #[allow(dead_code)]
    magic: String,
    offset: usize,
    size: usize,
    fields: BTreeMap<String, usize>,
}

/// Layout of the embedded template, validated against the blob it describes.
#[derive(Clone, Debug)]
pub struct TemplateLayout {
    pub code_len: usize,
    pub code_fnv1a_32: u32,
    pub on_off: u32,
    pub off_off: u32,
    pub payload_offset: usize,
    pub payload_size: usize,
    fields: BTreeMap<String, usize>,
}

impl TemplateLayout {
    /// Offset of a payload field inside the payload, checked to lie inside it.
    pub fn field(&self, name: &str) -> Result<usize> {
        let offset = *self.fields.get(name).ok_or_else(|| err(format!("template payload has no {name} field")))?;
        if offset >= self.payload_size {
            return Err(err(format!("template payload field {name} at {offset} is outside {} bytes", self.payload_size)));
        }
        Ok(offset)
    }

    /// Absolute offset of a payload field inside the code blob.
    pub fn code_field(&self, name: &str) -> Result<usize> {
        Ok(self.payload_offset + self.field(name)?)
    }
}

fn parse_hex(text: &str) -> Result<u32> {
    u32::from_str_radix(text.trim_start_matches("0x"), 16).map_err(|e| err(format!("not a hex word: {text:?} ({e})")))
}

fn load_layout() -> std::result::Result<TemplateLayout, String> {
    let record: TemplateRecord = serde_json::from_str(TEMPLATE_JSON).map_err(|e| format!("template record: {e}"))?;
    if record.schema != "bionz-film-sim-template/1" {
        return Err(format!("unknown template record schema {:?}", record.schema));
    }
    if record.code_len != TEMPLATE_BIN.len() {
        return Err(format!("template record says {} code bytes, blob has {}", record.code_len, TEMPLATE_BIN.len()));
    }
    let checksum = parse_hex(&record.code_fnv1a_32).map_err(|e| e.to_string())?;
    if fnv1a_32(TEMPLATE_BIN) != checksum {
        return Err("template blob does not match its recorded FNV-1a checksum".into());
    }
    let payload = &record.payload;
    if payload.offset + payload.size > TEMPLATE_BIN.len() {
        return Err("template payload runs past the code blob".into());
    }
    let magic = parse_hex(&payload.magic).map_err(|e| e.to_string())?;
    let at = payload.offset;
    if TEMPLATE_BIN[at..at + 4] != magic.to_le_bytes() {
        return Err("SIM_PAYLOAD_MAGIC is not at the recorded payload offset".into());
    }
    let on_off = parse_hex(&record.on_off).map_err(|e| e.to_string())?;
    let off_off = parse_hex(&record.off_off).map_err(|e| e.to_string())?;
    for (label, entry) in [("on_off", on_off), ("off_off", off_off)] {
        if entry & 1 == 0 || entry as usize >= TEMPLATE_BIN.len() {
            return Err(format!("template {label} {entry:#x} is not a Thumb entry inside the code"));
        }
    }
    let layout = TemplateLayout {
        code_len: record.code_len,
        code_fnv1a_32: checksum,
        on_off,
        off_off,
        payload_offset: payload.offset,
        payload_size: payload.size,
        fields: payload.fields.clone(),
    };
    // Every field the packer writes must be declared, and the writable arrays must be the sizes the
    // compiled tables have.
    for (name, size) in [
        ("ec_stock", 96 * 4),
        ("ec_sim", 96 * 4),
        ("fc_stock", 96 * 2),
        ("fc_sim", 96 * 2),
        ("a0_stock", 9 * 2),
        ("a0_sim", 9 * 2),
        ("a1_stock", 9 * 2),
        ("a1_sim", 9 * 2),
        ("b2_stock", 9 * 2),
        ("b2_sim", 9 * 2),
        ("refadr_sim", 1215),
    ] {
        let start = *layout.fields.get(name).ok_or_else(|| format!("template payload has no {name} field"))?;
        let end = layout.fields.values().filter(|v| **v > start).min().copied().unwrap_or(layout.payload_size);
        if end - start < size {
            return Err(format!("template payload field {name} is {} bytes, need {size}", end - start));
        }
    }
    for name in ["ec_gamma_pa", "fc_gamma_pa", "color_a0_pa", "color_a1_pa", "color_b2_pa", "cc_tuning_pa", "refadr0_pa", "mod_tag", "mod_log_path"] {
        if !layout.fields.contains_key(name) {
            return Err(format!("template payload has no {name} field"));
        }
    }
    Ok(layout)
}

/// The embedded template layout, validated once per process.
pub fn template_layout() -> Result<&'static TemplateLayout> {
    static LAYOUT: OnceLock<std::result::Result<TemplateLayout, String>> = OnceLock::new();
    match LAYOUT.get_or_init(load_layout) {
        Ok(layout) => Ok(layout),
        Err(message) => Err(err(message.clone())),
    }
}

/// The raw template blob (the code the packer clones).
pub fn template_code() -> &'static [u8] {
    TEMPLATE_BIN
}

fn put_u32(bytes: &mut [u8], at: usize, value: u32) {
    bytes[at..at + 4].copy_from_slice(&value.to_le_bytes());
}

fn put_bytes(bytes: &mut [u8], at: usize, width: usize, value: &[u8]) -> Result<()> {
    if value.len() > width {
        return Err(err(format!("{} bytes do not fit a {width}-byte field", value.len())));
    }
    bytes[at..at + width].fill(0);
    bytes[at..at + value.len()].copy_from_slice(value);
    Ok(())
}

fn check_table(name: &str, len: usize, want: usize) -> Result<()> {
    if len != want {
        return Err(err(format!("{name} has {len} entries, the SMOD v2 payload needs {want}")));
    }
    Ok(())
}

/// `ILCE-6400`/`ILCE-6100` `.mod` bytes for one preset and one body.
///
/// The template's `sim_life` refuses to run from the payload of another preset: it only swaps a table
/// whose current content equals either the stock or the simulated values it carries, so a stale
/// payload cannot be applied twice or restore a foreign table.
///
/// Refuses presets whose stock tables are synthetic (the mod writes `*_STOCK` back when it turns off,
/// and the color matrices persist across power cycles) and Movie/Arrivederci presets that carry a
/// Color Mode base matrix the Film Sims payload cannot express.
pub fn emit_smod_v2(name: &str, target: &str, table: &CompiledTables) -> Result<Vec<u8>> {
    let layout = template_layout()?;
    let profile = known_target(target).ok_or_else(|| err(format!("unknown target {target}: expected a6400 or a6100")))?;
    if table.has_synthetic_stock() {
        return Err(err(format!(
            "{name}: the {target} stock tables are synthetic (no av-cam.bin hash). A .mod restores them on the \
             camera when it turns off; compile the preset against the real stock tables first."
        )));
    }
    if table.base_matrix.is_some() {
        return Err(err(format!(
            "{name} writes a Color Mode base matrix (EC record 31 / B8). The Film Sims payload targets the A0/A1/B2 \
             matrices; build that look with mods/arri_isp/arrivederci instead."
        )));
    }
    check_table("ec", table.ec.len(), 96)?;
    check_table("fc", table.fc.len(), 96)?;
    check_table("a0", table.a0.len(), 9)?;
    check_table("a1", table.a1.len(), 9)?;
    check_table("b2", table.b2.len(), 9)?;
    check_table("refadr", table.refadr.len(), 1215)?;
    if name.trim().is_empty() {
        return Err(err("the preset needs a name for the Mod Loader menu entry"));
    }
    if !name.is_ascii() {
        return Err(err(format!("{name}: the .mod name must be ASCII for the camera menu")));
    }
    let tag = crate::slug(name);
    if tag.len() > 31 {
        return Err(err(format!("{name}: the log tag {tag:?} exceeds 31 bytes")));
    }
    let log_path = format!("/tmp_bt/mods/{tag}.log");
    if log_path.len() > 63 {
        return Err(err(format!("{name}: the log path {log_path:?} exceeds 63 bytes")));
    }

    let mut code = TEMPLATE_BIN.to_vec();
    let at = |field: &str| layout.code_field(field);
    put_u32(&mut code, at("ec_gamma_pa")?, profile.ec_gamma_pa);
    put_u32(&mut code, at("fc_gamma_pa")?, profile.fc_gamma_pa);
    put_u32(&mut code, at("color_a0_pa")?, profile.color_a0_pa);
    put_u32(&mut code, at("color_a1_pa")?, profile.color_a1_pa);
    put_u32(&mut code, at("color_b2_pa")?, profile.color_b2_pa);
    put_u32(&mut code, at("cc_tuning_pa")?, profile.cc_tuning_pa);
    put_u32(&mut code, at("refadr0_pa")?, profile.refadr0_pa);
    put_bytes(&mut code, at("mod_tag")?, 32, tag.as_bytes())?;
    put_bytes(&mut code, at("mod_log_path")?, 64, log_path.as_bytes())?;
    write_u32s(&mut code, at("ec_stock")?, &table.stock_ec);
    write_u32s(&mut code, at("ec_sim")?, &table.ec);
    write_u16s(&mut code, at("fc_stock")?, &table.stock_fc);
    write_u16s(&mut code, at("fc_sim")?, &table.fc);
    write_i16s(&mut code, at("a0_stock")?, &table.stock_a0);
    write_i16s(&mut code, at("a0_sim")?, &table.a0);
    write_i16s(&mut code, at("a1_stock")?, &table.stock_a1);
    write_i16s(&mut code, at("a1_sim")?, &table.a1);
    write_i16s(&mut code, at("b2_stock")?, &table.stock_b2);
    write_i16s(&mut code, at("b2_sim")?, &table.b2);
    put_bytes(&mut code, at("refadr_sim")?, 1215, &table.refadr)?;

    let mut menu_name = [0u8; 32];
    let ascii = &name.as_bytes()[..name.len().min(MAX_NAME)];
    menu_name[..ascii.len()].copy_from_slice(ascii);

    let mut out = Vec::with_capacity(HEADER_LEN + code.len());
    out.extend(SMOD_MAGIC.to_le_bytes());
    out.extend(SMOD_VERSION.to_le_bytes());
    out.extend(FLAG_ACTION.to_le_bytes());
    out.extend(menu_name);
    out.extend((code.len() as u32).to_le_bytes());
    out.extend(fnv1a_32(&code).to_le_bytes());
    out.extend(0u32.to_le_bytes()); // bss_size: the template uses no writable state
    out.extend(layout.on_off.to_le_bytes());
    out.extend(layout.off_off.to_le_bytes());
    out.extend(0u32.to_le_bytes()); // no hooks: the loader calls the lifecycle entries
    out.extend(code);
    Ok(out)
}

fn write_u32s(bytes: &mut [u8], at: usize, values: &[u32]) {
    for (index, value) in values.iter().enumerate() {
        put_u32(bytes, at + index * 4, *value);
    }
}

fn write_u16s(bytes: &mut [u8], at: usize, values: &[u16]) {
    for (index, value) in values.iter().enumerate() {
        bytes[at + index * 2..at + index * 2 + 2].copy_from_slice(&value.to_le_bytes());
    }
}

fn write_i16s(bytes: &mut [u8], at: usize, values: &[i16]) {
    for (index, value) in values.iter().enumerate() {
        bytes[at + index * 2..at + index * 2 + 2].copy_from_slice(&value.to_le_bytes());
    }
}

// --------------------------------------------------------------------------------------------------
// Live Picker (`FilmSimulations.mod`)

#[derive(Deserialize)]
struct LiveTemplateRecord {
    schema: String,
    mod_bytes: usize,
    name: String,
    smod_flags: u32,
    hook_count: u32,
    bss_size: u32,
    on_off: String,
    off_off: String,
    code_offset: usize,
    code_len: usize,
    code_fnv1a_32: String,
    payload: TemplatePayloadRecord,
}

/// Layout of the committed Live Picker skeleton, validated against the bytes it describes.
#[derive(Clone, Debug)]
pub struct LiveTemplateLayout {
    pub mod_bytes: usize,
    pub name: String,
    pub smod_flags: u32,
    pub hook_count: u32,
    pub bss_size: u32,
    pub on_off: u32,
    pub off_off: u32,
    pub code_offset: usize,
    pub code_len: usize,
    pub code_fnv1a_32: u32,
    pub payload_offset: usize,
    pub payload_size: usize,
    fields: BTreeMap<String, usize>,
}

impl LiveTemplateLayout {
    /// Absolute offset of a payload field inside the skeleton.
    pub fn code_field(&self, name: &str) -> Result<usize> {
        let offset = *self.fields.get(name).ok_or_else(|| err(format!("live payload has no {name} field")))?;
        if offset >= self.payload_size {
            return Err(err(format!("live payload field {name} at {offset} is outside {} bytes", self.payload_size)));
        }
        Ok(self.code_offset + self.payload_offset + offset)
    }
}

fn load_live_layout() -> std::result::Result<LiveTemplateLayout, String> {
    let record: LiveTemplateRecord = serde_json::from_str(LIVE_TEMPLATE_JSON).map_err(|e| format!("live template record: {e}"))?;
    if record.schema != "bionz-live-picker-template/1" {
        return Err(format!("unknown live template schema {:?}", record.schema));
    }
    if record.mod_bytes != LIVE_TEMPLATE_MOD.len() {
        return Err(format!("live skeleton record says {} bytes, asset has {}", record.mod_bytes, LIVE_TEMPLATE_MOD.len()));
    }
    if record.smod_flags & FLAG_ACTION == 0 || record.smod_flags & FLAG_RELOC == 0 {
        return Err("the live skeleton must carry FLAG_ACTION and FLAG_RELOC".into());
    }
    if record.hook_count != 1 {
        return Err("the live skeleton must carry exactly one hook".into());
    }
    let code_end = record.code_offset + record.code_len;
    if code_end > LIVE_TEMPLATE_MOD.len() {
        return Err("the live skeleton's code runs past the file".into());
    }
    let code = &LIVE_TEMPLATE_MOD[record.code_offset..code_end];
    let checksum = parse_hex(&record.code_fnv1a_32).map_err(|e| e.to_string())?;
    if fnv1a_32(code) != checksum {
        return Err("the live skeleton's code does not match its recorded FNV-1a checksum".into());
    }
    // The relocation table sits after the code and must stay untouched by the packer.
    let trailer = &LIVE_TEMPLATE_MOD[code_end..];
    if trailer.len() < 12 {
        return Err("the live skeleton has no RLC1 relocation table".into());
    }
    let reloc_magic = u32::from_le_bytes(trailer[0..4].try_into().unwrap());
    if reloc_magic != 0x3143_4C52 {
        return Err("the live skeleton's relocation table magic is not RLC1".into());
    }
    let payload = &record.payload;
    if payload.offset + payload.size > record.code_len {
        return Err("the live payload runs past the code".into());
    }
    let magic = parse_hex(&payload.magic).map_err(|e| e.to_string())?;
    let at = record.code_offset + payload.offset;
    if LIVE_TEMPLATE_MOD[at..at + 4] != magic.to_le_bytes() {
        return Err("LIVE_PAYLOAD_MAGIC is not at the recorded offset".into());
    }
    let on_off = parse_hex(&record.on_off).map_err(|e| e.to_string())?;
    let off_off = parse_hex(&record.off_off).map_err(|e| e.to_string())?;
    if on_off & 1 == 0 || on_off as usize >= record.code_len {
        return Err(format!("live lifecycle entry {on_off:#x} is not a Thumb offset inside the code"));
    }
    let layout = LiveTemplateLayout {
        mod_bytes: record.mod_bytes,
        name: record.name,
        smod_flags: record.smod_flags,
        hook_count: record.hook_count,
        bss_size: record.bss_size,
        on_off,
        off_off,
        code_offset: record.code_offset,
        code_len: record.code_len,
        code_fnv1a_32: checksum,
        payload_offset: payload.offset,
        payload_size: payload.size,
        fields: payload.fields.clone(),
    };
    for name in [
        "ec_gamma_pa", "fc_gamma_pa", "color_a0_pa", "color_a1_pa", "color_b2_pa", "cc_tuning_pa", "refadr0_pa",
        "n_presets", "n_looks", "log_path", "names", "lut_idx",
        "ec_stock", "fc_stock", "a0_stock", "a1_stock", "b2_stock",
        "ec", "fc", "a0", "a1", "b2", "refadr",
    ] {
        if !layout.fields.contains_key(name) {
            return Err(format!("live payload has no {name} field"));
        }
    }
    Ok(layout)
}

/// The embedded Live Picker skeleton layout, validated once per process.
pub fn live_template_layout() -> Result<&'static LiveTemplateLayout> {
    static LAYOUT: OnceLock<std::result::Result<LiveTemplateLayout, String>> = OnceLock::new();
    match LAYOUT.get_or_init(load_live_layout) {
        Ok(layout) => Ok(layout),
        Err(message) => Err(err(message.clone())),
    }
}

/// The committed Live Picker skeleton (the bytes the packer clones).
pub fn live_template_mod() -> &'static [u8] {
    LIVE_TEMPLATE_MOD
}

/// Why a look cannot go into the Live Picker, or `None` when it can.
///
/// Shared by the app's library filter and `bionz-cli live-mod` so a folder export skips the same looks
/// in both places instead of failing the whole file.
pub fn live_picker_skip_reason(name: &str, table: &CompiledTables) -> Option<&'static str> {
    if table.base_matrix.is_some() {
        Some("Color Mode base matrix")
    } else if table.has_synthetic_stock() {
        Some("synthetic stock")
    } else if name.trim().is_empty() || !name.is_ascii() || name.len() > MAX_NAME {
        Some("name is not menu-safe ASCII")
    } else {
        None
    }
}

/// `ILCE-6400`/`ILCE-6100` `FilmSimulations.mod` bytes carrying `looks` (1..=15).
///
/// The looks keep their library order: slot 0 of the picker is always "Stock (Off)", slots 1.. are
/// `looks`. Every look needs a table for the same body and the same stock tables, because the picker
/// writes one stock set when it returns to Off.
pub fn emit_live_smod_v2(target: &str, looks: &[(String, CompiledTables)]) -> Result<Vec<u8>> {
    let layout = live_template_layout()?;
    let profile = known_target(target).ok_or_else(|| err(format!("unknown target {target}: expected a6400 or a6100")))?;
    if looks.is_empty() {
        return Err(err("the Live Picker needs at least one look"));
    }
    if looks.len() > LIVE_MAX_LOOKS {
        return Err(err(format!("the Live Picker carries at most {LIVE_MAX_LOOKS} looks, got {}", looks.len())));
    }
    for (name, table) in looks {
        if table.has_synthetic_stock() {
            return Err(err(format!(
                "{name}: the {target} stock tables are synthetic (no av-cam.bin hash). The picker writes them back when it \
                 returns to Off; compile the preset against the real stock tables first."
            )));
        }
        if table.base_matrix.is_some() {
            return Err(err(format!(
                "{name} writes a Color Mode base matrix (EC record 31 / B8). The Live Picker payload targets the A0/A1/B2 \
                 matrices; build that look with mods/arri_isp/arrivederci instead."
            )));
        }
        if !name.is_ascii() {
            return Err(err(format!("{name}: the picker shows ASCII names only")));
        }
        check_table("ec", table.ec.len(), 96)?;
        check_table("fc", table.fc.len(), 96)?;
        check_table("a0", table.a0.len(), 9)?;
        check_table("a1", table.a1.len(), 9)?;
        check_table("b2", table.b2.len(), 9)?;
        check_table("refadr", table.refadr.len(), 1215)?;
        if name.trim().is_empty() {
            return Err(err("every look needs a name for the picker banner"));
        }
    }
    let stock = &looks[0].1;
    for (name, table) in looks.iter().skip(1) {
        if table.stock_ec != stock.stock_ec || table.stock_fc != stock.stock_fc
            || table.stock_a0 != stock.stock_a0 || table.stock_a1 != stock.stock_a1
            || table.stock_b2 != stock.stock_b2
        {
            return Err(err(format!(
                "{name}: its stock tables differ from {}'s. The picker stores one stock set; export presets compiled \
                 against the same av-cam.bin.",
                looks[0].0
            )));
        }
    }
    if LIVE_LOG_PATH.len() > 63 {
        return Err(err("the Live Picker log path exceeds 63 bytes"));
    }

    let mut out = LIVE_TEMPLATE_MOD.to_vec();
    let mut menu_name = [0u8; 32];
    let ascii = &layout.name.as_bytes()[..layout.name.len().min(MAX_NAME)];
    menu_name[..ascii.len()].copy_from_slice(ascii);
    out[12..44].copy_from_slice(&menu_name);

    let at = |field: &str| layout.code_field(field);
    put_u32(&mut out, at("ec_gamma_pa")?, profile.ec_gamma_pa);
    put_u32(&mut out, at("fc_gamma_pa")?, profile.fc_gamma_pa);
    put_u32(&mut out, at("color_a0_pa")?, profile.color_a0_pa);
    put_u32(&mut out, at("color_a1_pa")?, profile.color_a1_pa);
    put_u32(&mut out, at("color_b2_pa")?, profile.color_b2_pa);
    put_u32(&mut out, at("cc_tuning_pa")?, profile.cc_tuning_pa);
    put_u32(&mut out, at("refadr0_pa")?, profile.refadr0_pa);
    put_u32(&mut out, at("n_presets")?, looks.len() as u32 + 1);
    put_u32(&mut out, at("n_looks")?, looks.len() as u32);
    put_bytes(&mut out, at("log_path")?, 64, LIVE_LOG_PATH.as_bytes())?;

    // Slot 0 is "Stock (Off)"; slots 1.. are the looks in library order.
    let names = at("names")?;
    for slot in 0..=looks.len() {
        let text = if slot == 0 { "Stock (Off)" } else { looks[slot - 1].0.as_str() };
        let text: String = text.chars().take(15).collect();
        put_bytes(&mut out, names + slot * 16, 16, text.as_bytes())?;
    }
    let lut_idx = at("lut_idx")?;
    out[lut_idx..lut_idx + 16].fill(0);
    out[lut_idx] = 0xFF; // -1: Stock, no RefAdr
    for slot in 1..=looks.len() {
        out[lut_idx + slot] = (slot - 1) as u8;
    }

    // One stock set, then each look's tables.
    let ec_stock = at("ec_stock")?;
    let fc_stock = at("fc_stock")?;
    let a0_stock = at("a0_stock")?;
    let a1_stock = at("a1_stock")?;
    let b2_stock = at("b2_stock")?;
    write_u32s(&mut out, ec_stock, &stock.stock_ec);
    write_u16s(&mut out, fc_stock, &stock.stock_fc);
    write_i16s(&mut out, a0_stock, &stock.stock_a0);
    write_i16s(&mut out, a1_stock, &stock.stock_a1);
    write_i16s(&mut out, b2_stock, &stock.stock_b2);
    let ec = at("ec")?;
    let fc = at("fc")?;
    let a0 = at("a0")?;
    let a1 = at("a1")?;
    let b2 = at("b2")?;
    let refadr = at("refadr")?;
    for (slot, (_, table)) in looks.iter().enumerate() {
        write_u32s(&mut out, ec + slot * 96 * 4, &table.ec);
        write_u16s(&mut out, fc + slot * 96 * 2, &table.fc);
        write_i16s(&mut out, a0 + slot * 9 * 2, &table.a0);
        write_i16s(&mut out, a1 + slot * 9 * 2, &table.a1);
        write_i16s(&mut out, b2 + slot * 9 * 2, &table.b2);
        put_bytes(&mut out, refadr + slot * 1215, 1215, &table.refadr)?;
    }

    let checksum = fnv1a_32(&out[layout.code_offset..layout.code_offset + layout.code_len]);
    out[48..52].copy_from_slice(&checksum.to_le_bytes());
    Ok(out)
}

/// The `.mod` file name Mod Loader v2 accepts: `CamelCaseLook.mod` (ASCII, at most 47 characters).
pub fn mod_filename(name: &str) -> String {
    let mut out = String::new();
    let mut start = true;
    for ch in name.chars() {
        if ch.is_ascii_alphanumeric() {
            if start {
                out.push(ch.to_ascii_uppercase());
                start = false;
            } else {
                out.push(ch);
            }
        } else {
            start = true;
        }
    }
    if out.is_empty() {
        out.push_str("CustomSim");
    }
    format!("{out}.mod")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::load_bzlook;
    use std::path::PathBuf;
    fn preset(name: &str) -> crate::Bzlook {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../mods/film_sims/presets");
        load_bzlook(&root.join(format!("{name}.bzlook"))).unwrap()
    }

    fn table_of(name: &str, target: &str) -> CompiledTables {
        preset(name).compiled.iter().find(|(k, _)| k == target).unwrap().1.clone()
    }

    fn header(blob: &[u8]) -> (u32, u32, u32, [u8; 32], u32, u32, u32, u32, u32, u32) {
        let name: [u8; 32] = blob[12..44].try_into().unwrap();
        (
            u32::from_le_bytes(blob[0..4].try_into().unwrap()),
            u32::from_le_bytes(blob[4..8].try_into().unwrap()),
            u32::from_le_bytes(blob[8..12].try_into().unwrap()),
            name,
            u32::from_le_bytes(blob[44..48].try_into().unwrap()),
            u32::from_le_bytes(blob[48..52].try_into().unwrap()),
            u32::from_le_bytes(blob[52..56].try_into().unwrap()),
            u32::from_le_bytes(blob[56..60].try_into().unwrap()),
            u32::from_le_bytes(blob[60..64].try_into().unwrap()),
            u32::from_le_bytes(blob[64..68].try_into().unwrap()),
        )
    }

    fn payload_u32(blob: &[u8], field: &str) -> u32 {
        let layout = template_layout().unwrap();
        let at = HEADER_LEN + layout.code_field(field).unwrap();
        u32::from_le_bytes(blob[at..at + 4].try_into().unwrap())
    }

    #[test]
    fn template_layout_matches_the_blob() {
        let layout = template_layout().unwrap();
        assert_eq!(layout.code_len, template_code().len());
        assert_eq!(fnv1a_32(template_code()), layout.code_fnv1a_32);
        assert_eq!(layout.on_off & 1, 1, "sim_life must be a Thumb entry");
        assert_eq!(layout.payload_size, 2604, "payload is the documented SimPayload");
        let magic = &template_code()[layout.payload_offset..layout.payload_offset + 4];
        assert_eq!(magic, &0x4B4C_5A42u32.to_le_bytes());
    }

    #[test]
    fn emitted_mod_has_a_valid_smod_v2_header_and_code_checksum() {
        let table = table_of("classic-chrome", "a6400");
        let blob = emit_smod_v2("Classic Chrome", "a6400", &table).unwrap();
        let (magic, version, flags, name, code_len, checksum, bss, on_off, off_off, hooks) = header(&blob);
        assert_eq!(magic, SMOD_MAGIC);
        assert_eq!(version, 2);
        assert_eq!(flags, FLAG_ACTION);
        assert_eq!(&name[..14], b"Classic Chrome");
        assert_eq!(code_len as usize, template_code().len());
        assert_eq!(bss, 0);
        assert_eq!(hooks, 0);
        assert_eq!(off_off, on_off);
        assert_eq!(on_off & 1, 1);
        assert!(on_off < code_len);
        let code = &blob[HEADER_LEN..];
        assert_eq!(code.len(), code_len as usize);
        assert_eq!(fnv1a_32(code), checksum);
        assert_eq!(blob.len(), HEADER_LEN + code_len as usize);
    }

    #[test]
    fn both_targets_carry_their_own_physical_addresses() {
        let table = table_of("classic-chrome", "a6400");
        for target in ["a6400", "a6100"] {
            let blob = emit_smod_v2("Classic Chrome", target, &table).unwrap();
            let profile = known_target(target).unwrap();
            assert_eq!(payload_u32(&blob, "ec_gamma_pa"), profile.ec_gamma_pa, "{target} EC");
            assert_eq!(payload_u32(&blob, "fc_gamma_pa"), profile.fc_gamma_pa, "{target} FC");
            assert_eq!(payload_u32(&blob, "color_a0_pa"), profile.color_a0_pa, "{target} A0");
            assert_eq!(payload_u32(&blob, "color_a1_pa"), profile.color_a1_pa, "{target} A1");
            assert_eq!(payload_u32(&blob, "color_b2_pa"), profile.color_b2_pa, "{target} B2");
            assert_eq!(payload_u32(&blob, "cc_tuning_pa"), profile.cc_tuning_pa, "{target} CC");
            assert_eq!(payload_u32(&blob, "refadr0_pa"), profile.refadr0_pa, "{target} RefAdr");
        }
        assert_ne!(known_target("a6400").unwrap().ec_gamma_pa, known_target("a6100").unwrap().ec_gamma_pa);
    }

    #[test]
    fn tables_and_tag_land_in_the_payload() {
        let table = table_of("gold-200", "a6400");
        let blob = emit_smod_v2("Gold 200", "a6400", &table).unwrap();
        let layout = template_layout().unwrap();
        let code = &blob[HEADER_LEN..];
        let read = |field: &str| layout.code_field(field).unwrap();
        let ec_stock = read("ec_stock");
        for (index, value) in table.stock_ec.iter().enumerate() {
            let at = ec_stock + index * 4;
            assert_eq!(u32::from_le_bytes(code[at..at + 4].try_into().unwrap()), *value);
        }
        let ec_sim = read("ec_sim");
        for (index, value) in table.ec.iter().enumerate() {
            let at = ec_sim + index * 4;
            assert_eq!(u32::from_le_bytes(code[at..at + 4].try_into().unwrap()), *value);
        }
        let a0 = read("a0_sim");
        for (index, value) in table.a0.iter().enumerate() {
            let at = a0 + index * 2;
            assert_eq!(i16::from_le_bytes(code[at..at + 2].try_into().unwrap()), *value);
        }
        let refadr = read("refadr_sim");
        assert_eq!(&code[refadr..refadr + 1215], &table.refadr[..]);
        let tag = read("mod_tag");
        assert_eq!(&code[tag..tag + 5], b"gold_");
        let log = read("mod_log_path");
        assert_eq!(&code[log..log + 23], b"/tmp_bt/mods/gold_200.l");
        assert!(fnv1a_32(template_code()) != fnv1a_32(&blob[HEADER_LEN..]), "patched code must not keep the template checksum");
    }

    #[test]
    fn synthetic_stock_is_refused() {
        let mut table = table_of("classic-chrome", "a6400");
        table.flags |= 1;
        table.avcam_sha256 = [0u8; 32];
        assert!(table.has_synthetic_stock());
        let message = emit_smod_v2("Classic Chrome", "a6400", &table).unwrap_err().to_string();
        assert!(message.contains("synthetic"), "{message}");
    }

    #[test]
    fn base_matrix_and_unknown_targets_are_refused() {
        let mut table = table_of("classic-chrome", "a6400");
        table.base_matrix = Some([1024, 0, 0, 0, 1024, 0, 0, 0, 1024].to_vec());
        assert!(emit_smod_v2("Classic Chrome", "a6400", &table).unwrap_err().to_string().contains("base matrix"));
        table.base_matrix = None;
        assert!(emit_smod_v2("Classic Chrome", "a7s", &table).is_err());
        assert!(emit_smod_v2("", "a6400", &table).is_err());
        assert!(emit_smod_v2("Clássico", "a6400", &table).is_err(), "non-ASCII names are refused");
    }

    #[test]
    fn every_shipped_preset_emits_a_mod_for_both_bodies() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../mods/film_sims/presets");
        let mut count = 0;
        for entry in std::fs::read_dir(&root).unwrap().flatten() {
            let path = entry.path();
            if !path.extension().is_some_and(|e| e.eq_ignore_ascii_case("bzlook")) {
                continue;
            }
            let look = load_bzlook(&path).unwrap();
            for (target, table) in &look.compiled {
                let blob = emit_smod_v2(&look.meta.name, target, table).unwrap_or_else(|e| panic!("{}: {e}", look.meta.name));
                let (magic, version, ..) = header(&blob);
                assert_eq!((magic, version), (SMOD_MAGIC, 2));
                assert_eq!(payload_u32(&blob, "ec_gamma_pa"), known_target(target).unwrap().ec_gamma_pa);
                count += 1;
            }
        }
        assert_eq!(count, 24, "12 presets x 2 bodies");
    }

    #[test]
    fn mod_filename_is_loader_safe() {
        assert_eq!(mod_filename("Classic Chrome"), "ClassicChrome.mod");
        assert_eq!(mod_filename("ACROS B&W"), "ACROSBW.mod");
        assert_eq!(mod_filename("Eterna Bleach Bypass"), "EternaBleachBypass.mod");
        assert_eq!(mod_filename("Portra 400"), "Portra400.mod");
        assert_eq!(mod_filename("***"), "CustomSim.mod");
        for name in ["Classic Chrome", "Eterna Bleach Bypass", "Vision3 800T"] {
            let file = mod_filename(name);
            assert!(file.len() <= 47 && file.ends_with(".mod"));
            assert!(file.chars().next().unwrap().is_ascii_alphanumeric());
            assert!(file.chars().all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '_' || c == '-'));
        }
    }

    // ----------------------------------------------------------------------------------------------
    // Live Picker

    fn shipped_looks(target: &str) -> Vec<(String, CompiledTables)> {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../mods/film_sims/presets");
        let mut paths: Vec<PathBuf> = std::fs::read_dir(&root)
            .unwrap()
            .flatten()
            .map(|e| e.path())
            .filter(|p| p.extension().is_some_and(|e| e.eq_ignore_ascii_case("bzlook")))
            .collect();
        paths.sort();
        paths
            .iter()
            .map(|path| {
                let look = load_bzlook(path).unwrap();
                let table = look.compiled.iter().find(|(k, _)| k == target).expect("table").1.clone();
                (look.meta.name.clone(), table)
            })
            .collect()
    }

    fn live_u32(blob: &[u8], field: &str) -> u32 {
        let layout = live_template_layout().unwrap();
        let at = layout.code_field(field).unwrap();
        u32::from_le_bytes(blob[at..at + 4].try_into().unwrap())
    }

    fn live_bytes<'a>(blob: &'a [u8], field: &str, width: usize) -> &'a [u8] {
        let layout = live_template_layout().unwrap();
        let at = layout.code_field(field).unwrap();
        &blob[at..at + width]
    }

    #[test]
    fn live_template_keeps_its_hook_bss_and_relocation_table() {
        let layout = live_template_layout().unwrap();
        let blob = live_template_mod();
        assert_eq!(layout.mod_bytes, blob.len());
        assert_eq!(layout.hook_count, 1);
        assert_eq!(layout.bss_size, 48);
        assert_eq!(layout.smod_flags, FLAG_ACTION | FLAG_RELOC);
        assert_eq!(layout.off_off, 0, "the picker only runs on ON");
        assert_eq!(layout.on_off & 1, 1);
        let code = &blob[layout.code_offset..layout.code_offset + layout.code_len];
        assert_eq!(fnv1a_32(code), layout.code_fnv1a_32);
        // RLC1: the trampoline's resume address in libObj, and nothing else.
        let trailer = &blob[layout.code_offset + layout.code_len..];
        assert_eq!(&trailer[0..4], b"RLC1");
        let (nlib, nrel) = (u16::from_le_bytes(trailer[4..6].try_into().unwrap()), u16::from_le_bytes(trailer[6..8].try_into().unwrap()));
        assert_eq!((nlib, nrel), (1, 1));
        let table_len = 8 + nlib as usize * 32 + nrel as usize * 4;
        let expected = u32::from_le_bytes(trailer[table_len..table_len + 4].try_into().unwrap());
        assert_eq!(fnv1a_32(&trailer[..table_len]), expected);
        assert_eq!(trailer.len(), table_len + 4, "no submenu table in the Live Picker");
    }

    #[test]
    fn live_picker_mod_carries_every_look_for_both_bodies() {
        for target in ["a6400", "a6100"] {
            let looks = shipped_looks(target);
            assert_eq!(looks.len(), 12);
            let blob = emit_live_smod_v2(target, &looks).unwrap();
            let layout = live_template_layout().unwrap();
            let (magic, version, flags, name, code_len, checksum, bss, on_off, off_off, hooks) = header(&blob);
            assert_eq!((magic, version), (SMOD_MAGIC, 2));
            assert_eq!(flags, FLAG_ACTION | FLAG_RELOC);
            assert_eq!(&name[..16], b"Film Simulations");
            assert_eq!(code_len as usize, layout.code_len);
            assert_eq!(bss, 48);
            assert_eq!(hooks, 1);
            assert_eq!((on_off, off_off), (layout.on_off, 0));
            assert_eq!(blob.len(), layout.mod_bytes);
            assert_eq!(fnv1a_32(&blob[layout.code_offset..layout.code_offset + layout.code_len]), checksum);

            let profile = known_target(target).unwrap();
            assert_eq!(live_u32(&blob, "ec_gamma_pa"), profile.ec_gamma_pa);
            assert_eq!(live_u32(&blob, "fc_gamma_pa"), profile.fc_gamma_pa);
            assert_eq!(live_u32(&blob, "color_a0_pa"), profile.color_a0_pa);
            assert_eq!(live_u32(&blob, "color_a1_pa"), profile.color_a1_pa);
            assert_eq!(live_u32(&blob, "color_b2_pa"), profile.color_b2_pa);
            assert_eq!(live_u32(&blob, "cc_tuning_pa"), profile.cc_tuning_pa);
            assert_eq!(live_u32(&blob, "refadr0_pa"), profile.refadr0_pa);
            assert_eq!(live_u32(&blob, "n_presets"), 13);
            assert_eq!(live_u32(&blob, "n_looks"), 12);

            let names = live_bytes(&blob, "names", 16 * 16);
            assert_eq!(&names[..16], b"Stock (Off)\0\0\0\0\0");
            for (index, (look, _)) in looks.iter().enumerate() {
                let slot = &names[(index + 1) * 16..(index + 2) * 16];
                let text = std::str::from_utf8(&slot[..15]).unwrap().trim_end_matches('\0');
                assert_eq!(text, &look.chars().take(15).collect::<String>()[..], "slot {} name", index + 1);
            }
            let lut = live_bytes(&blob, "lut_idx", 16);
            assert_eq!(lut[0], 0xFF, "Stock has no RefAdr");
            for index in 1..=12usize {
                assert_eq!(lut[index], (index - 1) as u8);
            }
            let stock = &looks[0].1;
            assert_eq!(live_bytes(&blob, "ec_stock", 384), pack_u32_bytes(&stock.stock_ec).as_slice());
            let ec_at = live_template_layout().unwrap().code_field("ec").unwrap();
            for (index, (name, table)) in looks.iter().enumerate() {
                assert_eq!(&blob[ec_at + index * 384..ec_at + index * 384 + 384], pack_u32_bytes(&table.ec).as_slice(), "{name} EC");
            }
            let refadr_at = live_template_layout().unwrap().code_field("refadr").unwrap();
            for (index, (name, table)) in looks.iter().enumerate() {
                assert_eq!(&blob[refadr_at + index * 1215..refadr_at + index * 1215 + 1215], &table.refadr[..], "{name} RefAdr");
            }
            assert!(fnv1a_32(live_template_mod()) != checksum, "the payload was written");
        }
    }

    fn pack_u32_bytes(values: &[u32]) -> Vec<u8> {
        values.iter().flat_map(|v| v.to_le_bytes()).collect()
    }

    #[test]
    fn live_picker_refuses_what_it_cannot_carry() {
        let looks = shipped_looks("a6400");
        assert!(emit_live_smod_v2("a6400", &[]).unwrap_err().to_string().contains("at least one look"));
        let too_many: Vec<(String, CompiledTables)> = (0..16).map(|i| (format!("Look {i}"), looks[0].1.clone())).collect();
        assert!(emit_live_smod_v2("a6400", &too_many).unwrap_err().to_string().contains("at most 15"));
        assert!(emit_live_smod_v2("a7s", &looks).is_err());

        let mut synthetic = looks.clone();
        synthetic[0].1.flags |= 1;
        synthetic[0].1.avcam_sha256 = [0u8; 32];
        assert!(emit_live_smod_v2("a6400", &synthetic).unwrap_err().to_string().contains("synthetic"));

        let mut movie = looks.clone();
        movie[0].1.base_matrix = Some(vec![1024, 0, 0, 0, 1024, 0, 0, 0, 1024]);
        assert!(emit_live_smod_v2("a6400", &movie).unwrap_err().to_string().contains("base matrix"));

        let mut mixed = looks.clone();
        mixed[1].1.stock_a0 = vec![1; 9];
        assert!(emit_live_smod_v2("a6400", &mixed).unwrap_err().to_string().contains("stock tables differ"));

        // Fifteen looks are accepted (the payload has exactly fifteen slots).
        let fifteen: Vec<(String, CompiledTables)> = (0..15).map(|i| (format!("Look {i}"), looks[0].1.clone())).collect();
        let blob = emit_live_smod_v2("a6400", &fifteen).unwrap();
        assert_eq!(live_u32(&blob, "n_presets"), 16);
        assert_eq!(live_u32(&blob, "n_looks"), 15);
    }
}
