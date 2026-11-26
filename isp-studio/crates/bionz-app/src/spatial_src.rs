//! Spatial table sources: the stock `/usr/sabin` tables from a local firmware extraction (hash
//! checked), and the Arrivederci v5 import. Nothing here reads the camera, and no table data is
//! shipped with the app.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use bionz_core::spatial::DcvTable;
use bionz_preset::{Bzlook, CompiledTables, LookMeta};
use sha2::{Digest, Sha256};

use crate::looks::Entry;

/// SHA-256 of the stock ILCE-6400 tables (firmware extraction recorded in E0602 / E0679).
pub const STOCK_TABLES: [(&str, &str); 8] = [
    ("dcv_psf_table_still.bin", "b3b82d875733c17f9c4193a1208a996421d16eeb97208a92939e9a0c21624a17"),
    ("dcv_psf_table_movie_bayer.bin", "8fdc685767baecbf42049fbf990fbe0b65773ea05f47ece1a9f936b4c04d7cb4"),
    ("dcv_psf_table_movie_4k.bin", "4e0d8847b9f721d281c9d92080b1c49662eb85867b53f38d6a7d5e9a69d470a0"),
    ("dcv_psf_table_movie.bin", "1e7f6bb2842bc828143473ae4f9e39e3ceaa407bb1e003215d237014b82d2004"),
    ("spica_musashi_mov_4k_y_tap_step1.bin", "34dee92693ab235f66f8d31bea3797465496cfddfefe0ff5180ab3bbe57a4e3d"),
    ("spica_musashi_mov_4k_y_tap_step2.bin", "3960cf3d7ae9e93f5f2ed0fc3f764e2fd86f32746a2a8daf1388e2b787d1f314"),
    ("spica_musashi_mov_4k_c_tap_step1.bin", "fbeb326800d37e05718df5f5c49c5984f89a4ae01b4614013cf1cd157851af41"),
    ("spica_musashi_mov_4k_c_tap_step2.bin", "3ecd20fcf2cdca8abeafb73f30e0c79b3751bbb0887ac7261b9f83fd623faeb2"),
];

/// SHA-256 of the released Arrivederci v5 SMOD v2 (`mlv2-layout-build-4`).
pub const ARRIVEDERCI_V5_SHA256: &str = "46c41981e7b37231a6cadb0d033f514c5b05a17ecb38c85f68d909d64a0b64b7";
/// av-cam.bin identified in E0505, whose record 31 and entry B8 the v5 stock arrays come from.
pub const AVCAM_E0505_SHA256: &str = "90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e";
const RECORD31_EXPONENTS: [u8; 9] = [3, 3, 4, 4, 3, 3, 3, 4, 3];
const SABIN_REL: &str = "firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/sabin";
const ARRI_V4_SABIN_REL: &str = "artifacts/analysis/arri-v4-sabin";

const ARRI_MOVIE_TABLES: [(&str, &str); 2] = [
    ("dcv_psf_table_movie_bayer.bin", "1d8ccc2cf4084964607635ee9f234c8b0a9a8cfef76ee1dadfcc7d7a1b00683c"),
    ("dcv_psf_table_movie_4k.bin", "8f2cadbc75fcb48c242fc3426be7768e3ca4427cbf93fae6b298a967b2151dd4"),
];

/// Load the Arrivederci v5 Movie DCV tables (`dcv_psf_table_movie_bayer.bin` and
/// `dcv_psf_table_movie_4k.bin`) from `artifacts/analysis/arri-v4-sabin` when present and hash-valid.
pub fn load_arri_movie_tables() -> Vec<(String, Vec<u8>)> {
    let Some(dir) = search_roots().into_iter().map(|r| r.join(ARRI_V4_SABIN_REL)).find(|d| d.is_dir()) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for (name, want) in ARRI_MOVIE_TABLES {
        if let Ok(bytes) = std::fs::read(dir.join(name)) {
            if sha256_hex(&bytes) == want {
                out.push((name.to_string(), bytes));
            }
        }
    }
    out
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes).iter().map(|b| format!("{b:02x}")).collect()
}

fn search_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    for start in [std::env::current_exe().ok().and_then(|p| p.parent().map(Path::to_path_buf)), std::env::current_dir().ok()].into_iter().flatten() {
        roots.extend(start.ancestors().map(Path::to_path_buf));
    }
    roots
}

/// `BIONZ_SABIN`, else the firmware extraction found above the executable or working directory.
pub fn find_stock_sabin() -> Option<PathBuf> {
    if let Ok(dir) = std::env::var("BIONZ_SABIN") {
        let dir = PathBuf::from(dir);
        if dir.is_dir() {
            return Some(dir);
        }
    }
    search_roots().into_iter().map(|r| r.join(SABIN_REL)).find(|d| d.join(STOCK_TABLES[0].0).is_file())
}

/// Stock tables that exist and match their recorded hash. Mismatches are reported, not used.
pub fn load_stock_tables(dir: &Path) -> (Vec<(String, Vec<u8>)>, Vec<String>) {
    let mut ok = Vec::new();
    let mut problems = Vec::new();
    for (name, want) in STOCK_TABLES {
        match std::fs::read(dir.join(name)) {
            Ok(bytes) if sha256_hex(&bytes) == want => ok.push((name.to_string(), bytes)),
            Ok(_) => problems.push(format!("{name}: hash does not match the recorded stock table")),
            Err(_) => problems.push(format!("{name}: missing")),
        }
    }
    (ok, problems)
}

/// `NAME[N] = { ... }` arrays of a C header, as integers.
fn c_arrays(text: &str) -> HashMap<String, Vec<i64>> {
    let mut out = HashMap::new();
    let mut rest = text;
    while let Some(open) = rest.find("] = {") {
        let head = &rest[..open];
        let name_start = head.rfind(|c: char| !(c.is_ascii_alphanumeric() || c == '_' || c == '[')).map(|i| i + 1).unwrap_or(0);
        let name = head[name_start..].split('[').next().unwrap_or("").to_string();
        let body_start = open + 5;
        let Some(close) = rest[body_start..].find('}') else { break };
        let values: Vec<i64> = rest[body_start..body_start + close]
            .split(',')
            .filter_map(|v| {
                let v = v.trim();
                if v.is_empty() {
                    None
                } else if let Some(hex) = v.strip_prefix("0x") {
                    i64::from_str_radix(hex, 16).ok()
                } else {
                    v.parse().ok()
                }
            })
            .collect();
        out.insert(name, values);
        rest = &rest[body_start + close..];
    }
    out
}

/// The build header for an Arrivederci `.mod`: next to it, or in a build folder whose `.mod` has the
/// same hash.
fn header_for_mod(mod_path: &Path) -> Result<(PathBuf, String), String> {
    let mod_sha = sha256_hex(&std::fs::read(mod_path).map_err(|e| e.to_string())?);
    if let Some(dir) = mod_path.parent() {
        let beside = dir.join("arrivederci_data.h");
        if beside.is_file() {
            return Ok((beside, mod_sha));
        }
    }
    for root in search_roots() {
        let tests = root.join("artifacts").join("tests");
        let Ok(dirs) = std::fs::read_dir(&tests) else { continue };
        for dir in dirs.flatten().map(|d| d.path()).filter(|d| d.file_name().is_some_and(|n| n.to_string_lossy().starts_with("arrivederci"))) {
            let (m, h) = (dir.join("Arrivederci.mod"), dir.join("arrivederci_data.h"));
            if h.is_file() && std::fs::read(&m).map(|b| sha256_hex(&b) == mod_sha).unwrap_or(false) {
                return Ok((h, mod_sha));
            }
        }
    }
    Err(format!("no arrivederci_data.h found for this .mod (SHA-256 {}…); import the build header directly", &mod_sha[..12]))
}

/// Import Arrivederci from its build header or its `.mod`. Every layer becomes part of the look:
/// EC record 31 (tone), entry B8 (stage-6 base matrix), RefAdr (residual LUT) and the `/usr/sabin`
/// replacement tables (spatial). The stock arrays in the header are the camera's own values.
pub fn import_arrivederci(path: &Path) -> Result<Entry, String> {
    let is_mod = path.extension().is_some_and(|e| e.eq_ignore_ascii_case("mod"));
    let (header, mod_sha) = if is_mod { header_for_mod(path).map(|(h, s)| (h, Some(s)))? } else { (path.to_path_buf(), None) };
    let text = std::fs::read_to_string(&header).map_err(|e| format!("{}: {e}", header.display()))?;
    let arrays = c_arrays(&text);
    let get = |name: &str, len: usize| -> Result<&Vec<i64>, String> {
        let v = arrays.get(name).ok_or_else(|| format!("{name} is missing from {}", header.display()))?;
        if v.len() != len {
            return Err(format!("{name} has {} values, expected {len}", v.len()));
        }
        Ok(v)
    };
    let u32s = |v: &Vec<i64>| v.iter().map(|x| *x as u32).collect::<Vec<u32>>();
    let i16s = |v: &Vec<i64>| v.iter().map(|x| *x as i16).collect::<Vec<i16>>();
    let bytes = |v: &Vec<i64>| v.iter().map(|x| *x as u8).collect::<Vec<u8>>();
    let ec = u32s(get("GAMMA_ARRI", 96)?);
    let stock_ec = u32s(get("GAMMA_STOCK", 96)?);
    let base = i16s(get("COLOR_ARRI", 9)?);
    let stock_base = i16s(get("COLOR_STOCK", 9)?);
    let refadr = match arrays.get("REFADR_ARRI") {
        Some(v) if v.len() == 1215 => bytes(v),
        Some(v) => return Err(format!("REFADR_ARRI has {} values, expected 1215", v.len())),
        None => vec![0; 1215],
    };
    let mut spatial = Vec::new();
    for (array, file, len) in [
        ("SABIN_DCV_BAYER", "dcv_psf_table_movie_bayer.bin", 18288),
        ("SABIN_DCV_4K", "dcv_psf_table_movie_4k.bin", 10728),
        ("SABIN_Y_TAP_1", "spica_musashi_mov_4k_y_tap_step1.bin", 48000),
        ("SABIN_Y_TAP_2", "spica_musashi_mov_4k_y_tap_step2.bin", 48000),
    ] {
        if arrays.contains_key(array) {
            spatial.push((file.to_string(), bytes(get(array, len)?)));
        }
    }
    if arrays.contains_key("SABIN_DCV_BAYER") {
        // The mod bind-mounts a zero-filled file over both chroma tap tables.
        spatial.push(("spica_musashi_mov_4k_c_tap_step1.bin".into(), vec![0; 40000]));
        spatial.push(("spica_musashi_mov_4k_c_tap_step2.bin".into(), vec![0; 40000]));
    }
    for (name, data) in &spatial {
        if name.starts_with("dcv_psf") {
            DcvTable::parse(data).map_err(|e| format!("{name}: {e}"))?;
        }
    }
    let identity: Vec<i16> = vec![1024, 0, 0, 0, 1024, 0, 0, 0, 1024];
    let ramp: Vec<u16> = (0..96).map(|i| (i * 1023 / 95) as u16).collect();
    let mut avcam = [0u8; 32];
    for (i, b) in avcam.iter_mut().enumerate() {
        *b = u8::from_str_radix(&AVCAM_E0505_SHA256[i * 2..i * 2 + 2], 16).unwrap();
    }
    let table = CompiledTables {
        target: "a6400".into(),
        avcam_sha256: avcam,
        flags: 0,
        lut_level: 4,
        ec,
        fc: ramp.clone(),
        // In Movie S-Log3 the CC matrix stage holds a Q10 identity (catalog row 17).
        a0: identity.clone(),
        a1: identity.clone(),
        b2: identity.clone(),
        refadr,
        stock_ec,
        stock_fc: ramp,
        stock_a0: identity.clone(),
        stock_a1: identity.clone(),
        stock_b2: identity,
        base_matrix: Some(base),
        stock_base_matrix: Some(stock_base),
        ec_exponents: Some(RECORD31_EXPONENTS),
    };
    let header_sha = sha256_hex(text.as_bytes());
    let version = match mod_sha.as_deref() {
        Some(ARRIVEDERCI_V5_SHA256) => "v5 (release hash verified)",
        Some(_) => "unverified build",
        None => "from build header",
    };
    let look = Bzlook {
        meta: LookMeta {
            name: if mod_sha.as_deref() == Some(ARRIVEDERCI_V5_SHA256) { "Arrivederci v5".into() } else { "Arrivederci".into() },
            author: String::new(),
            version: 1,
            uuid: String::new(),
            intent_kind: "arrivederci".into(),
            tags: vec!["movie".into(), "s-log3".into()],
            notes: format!(
                "Imported {version}. Header SHA-256 {header_sha}{}. Layers: EC record 31, Color Mode entry B8 (stage 6), RefAdr, /usr/sabin tables. \
                 Targets Movie S-Log3 / S-Gamut3.Cine on av-cam {AVCAM_E0505_SHA256}.",
                mod_sha.map(|s| format!(", mod SHA-256 {s}")).unwrap_or_default()
            ),
            created_with: "BIONZ Look Lab".into(),
            cube_sha256: String::new(),
        },
        recipe_toml: String::new(),
        compiled: vec![("a6400".into(), table)],
        compiled_movie: Vec::new(),
        report_json: Vec::new(),
        thumb_png: Vec::new(),
        spatial,
    };
    let mut entry = Entry::from_look(look, None);
    entry.unsaved = true;
    entry.report = format!("Arrivederci {version}: tone, B8 matrix, 3D LUT and {} spatial tables", entry.look.spatial.len());
    Ok(entry)
}

/// Rebuild the preset's dcv_psf tables from stock at the correct offsets. The gain per table is the
/// median zone centre tap of the preset's table divided by the stock centre tap, which recovers the
/// intended attenuation when only some zones were damaged (Arrivederci v4/v5: 0.075).
pub fn repair_entry(entry: &Entry, stock: &[(String, Vec<u8>)]) -> Result<(Entry, String), String> {
    let mut look = entry.look.clone();
    let mut notes = Vec::new();
    for (name, bytes) in look.spatial.iter_mut() {
        if !name.starts_with("dcv_psf") {
            continue;
        }
        let Some((_, stock_bytes)) = stock.iter().find(|(n, _)| n == name) else {
            notes.push(format!("{name}: no stock table to rebuild from"));
            continue;
        };
        let centres = |t: &DcvTable| -> Vec<i16> {
            match &t.layout {
                bionz_core::spatial::DcvLayout::Bayer { zones } => zones.iter().map(|z| z[0][12]).collect(),
                bionz_core::spatial::DcvLayout::Plane9 { zones } => zones.iter().map(|z| z[40]).collect(),
                bionz_core::spatial::DcvLayout::Still { entries, .. } => entries.first().map(|e| e.iter().map(|z| z[0][bionz_core::spatial::STILL_CENTRE_TAP]).collect()).unwrap_or_default(),
            }
        };
        let preset = DcvTable::parse(bytes)?;
        let stock_table = DcvTable::parse(stock_bytes)?;
        let mut c = centres(&preset);
        c.sort_unstable();
        let median = c[c.len() / 2] as f32;
        let stock_centre = centres(&stock_table)[c.len() / 2] as f32;
        if stock_centre == 0.0 {
            notes.push(format!("{name}: stock centre tap is zero"));
            continue;
        }
        let gain = median / stock_centre;
        *bytes = bionz_core::spatial::rescale_table(stock_bytes, gain)?;
        notes.push(format!("{name}: rebuilt from stock at gain {gain:.4}"));
    }
    if notes.is_empty() {
        return Err("this preset carries no dcv_psf tables".into());
    }
    look.meta.name = format!("{} (repaired DCV)", entry.name);
    look.meta.notes = format!("{} Repaired: {}.", look.meta.notes, notes.join("; "));
    let mut fixed = Entry::from_look(look, None);
    fixed.recipe = entry.recipe.clone();
    fixed.unsaved = true;
    fixed.report = notes.join("; ");
    Ok((fixed, notes.join("; ")))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn repo() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..")
    }

    #[test]
    fn c_array_parser_reads_hex_and_signed_values() {
        let a = c_arrays("static const int16_t A[3] = {1,-2,0x10};\nstatic const uint8_t B_2[2] = {0xff,7};");
        assert_eq!(a["A"], vec![1, -2, 16]);
        assert_eq!(a["B_2"], vec![255, 7]);
    }

    #[test]
    fn v5_import_carries_every_layer_and_flags_the_damaged_tables() {
        let mod_path = repo().join("mods/arri_isp/arrivederci/Arrivederci.mod");
        if !mod_path.is_file() || !mod_path.with_file_name("arrivederci_data.h").is_file() {
            return;
        }
        let entry = import_arrivederci(&mod_path).unwrap();
        assert_eq!(entry.name, "Arrivederci v5");
        let t = entry.table("a6400").unwrap();
        assert!(!t.has_synthetic_stock());
        assert_eq!(t.base_matrix.as_deref(), Some(&[1698, -670, -4, -105, 1408, -279, 90, -478, 1412][..]));
        assert_eq!(t.stock_ec[0], 0x1500);
        assert_eq!(entry.look.spatial.len(), 6);
        let dcv4k = &entry.look.spatial.iter().find(|(n, _)| n == "dcv_psf_table_movie_4k.bin").unwrap().1;
        let (asym, dc) = DcvTable::parse(dcv4k).unwrap().health();
        assert!(asym > 0 && dc > 0, "v5 4K table should show the offset damage ({asym}, {dc})");
        let params = entry.params_mode("a6400", false, false).unwrap();
        assert_eq!(params.base_matrix[0], 1698);
        assert_eq!(entry.params_mode("a6400", true, false).unwrap().base_matrix[0], 1181);
    }

    #[test]
    fn repair_rebuilds_v5_tables_at_the_intended_gain() {
        let mod_path = repo().join("mods/arri_isp/arrivederci/Arrivederci.mod");
        let Some(dir) = find_stock_sabin() else { return };
        if !mod_path.is_file() {
            return;
        }
        let (stock, _) = load_stock_tables(&dir);
        let v5 = import_arrivederci(&mod_path).unwrap();
        let (fixed, report) = repair_entry(&v5, &stock).unwrap();
        assert!(report.contains("gain 0.07"), "{report}");
        for (name, bytes) in fixed.look.spatial.iter().filter(|(n, _)| n.starts_with("dcv")) {
            let t = DcvTable::parse(bytes).unwrap();
            assert_eq!(t.health(), (0, 0), "{name}");
            let (lo, hi) = t.zone_centre_range();
            assert_eq!(lo, hi, "{name}: every zone scaled alike");
        }
        assert_eq!(fixed.table("a6400").unwrap().base_matrix, v5.table("a6400").unwrap().base_matrix);
    }

    #[test]
    fn stock_tables_are_healthy_when_present() {
        let Some(dir) = find_stock_sabin() else { return };
        let (tables, problems) = load_stock_tables(&dir);
        assert!(problems.is_empty(), "{problems:?}");
        for (name, bytes) in tables.iter().filter(|(n, _)| n.starts_with("dcv")) {
            assert_eq!(DcvTable::parse(bytes).unwrap().health(), (0, 0), "{name}");
        }
    }
}
