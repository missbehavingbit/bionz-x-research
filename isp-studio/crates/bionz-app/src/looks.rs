//! Preset library: discovery, import (.bzlook, .cube, .a6lt), recipe compile, save, Film Sims
//! packing, and portable camera `.mod` export. Nothing here talks to a camera.

use std::path::{Path, PathBuf};

use bionz_core::{IntegerParams, OraclePreset, Profile};
use bionz_fit::{decompose, recipe_cube, Recipe};
use bionz_preset::{
    apply_fitted_gamma, apply_movie_gamma, compose_stock_matrix, emit_film_sim_data, emit_live_header, emit_live_smod_v2,
    emit_smod_v2, integer_params_from_compiled, load_bzlook, load_bzlook_bytes, pack_refadr, parse_cube,
    stock_params_from_compiled, store_bzlook, unpack_a6lt, Bzlook, CompiledTables, LookMeta,
};

use crate::bundled_presets;

/// Cube size used when a recipe is fitted. 17 points keep slider edits under a second.
pub const RECIPE_CUBE_SIZE: usize = 17;

pub struct Entry {
    pub name: String,
    pub path: Option<PathBuf>,
    pub look: Bzlook,
    pub recipe: Option<Recipe>,
    pub unsaved: bool,
    pub report: String,
}

impl Entry {
    pub fn from_look(look: Bzlook, path: Option<PathBuf>) -> Self {
        let recipe = if look.recipe_toml.trim().is_empty() { None } else { toml::from_str::<Recipe>(&look.recipe_toml).ok() };
        let report = look.report_json.first().map(|(k, r)| format!("{k}: {}", summarize_report(r))).unwrap_or_default();
        Self { name: look.meta.name.clone(), path, look, recipe, unsaved: false, report }
    }

    pub fn table(&self, target: &str) -> Option<&CompiledTables> {
        self.look.compiled.iter().find(|(k, _)| k == target).map(|(_, t)| t)
    }

    /// Movie variant with the documented fallback to the Photo table.
    pub fn movie_table(&self, target: &str) -> Option<&CompiledTables> {
        self.look.movie_table_or_photo(target).map(|(t, _)| t)
    }

    pub fn targets(&self) -> Vec<String> {
        self.look.compiled.iter().map(|(k, _)| k.clone()).collect()
    }

    /// Mode-aware parameters: `movie = true` prefers `compiled/<target>_movie.bin` and falls back
    /// to the Photo table when the preset has no Movie variant (older presets).
    pub fn params_mode(&self, target: &str, stock: bool, movie: bool) -> Option<IntegerParams> {
        let table = if movie { self.look.movie_table_or_photo(target).map(|(t, _)| t) } else { self.table(target) }?;
        Some(if stock { stock_params_from_compiled(table) } else { integer_params_from_compiled(table) })
    }

    /// True when the preset carries a Movie variant for this target.
    pub fn has_movie_table(&self, target: &str) -> bool {
        self.look.table_for(target, true).is_some()
    }
}

fn summarize_report(json: &str) -> String {
    let Ok(v) = serde_json::from_str::<serde_json::Value>(json) else {
        return String::new();
    };
    match (v.get("mean_de76").and_then(|x| x.as_f64()), v.get("max_de76").and_then(|x| x.as_f64())) {
        (Some(mean), Some(max)) => format!("fit mean dE76 {mean:.2}, max {max:.2}"),
        _ => String::new(),
    }
}

fn has_bzlook(dir: &Path) -> bool {
    std::fs::read_dir(dir)
        .map(|it| it.flatten().any(|e| e.path().extension().is_some_and(|x| x.eq_ignore_ascii_case("bzlook"))))
        .unwrap_or(false)
}

/// `BIONZ_PRESETS`, then `presets/` or `mods/film_sims/presets/` next to the executable or the
/// working directory or any of their parents.
pub fn find_preset_dir() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("BIONZ_PRESETS") {
        let p = PathBuf::from(p);
        if p.is_dir() {
            return Some(p);
        }
    }
    let mut starts = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            starts.push(dir.to_path_buf());
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        starts.push(cwd);
    }
    for start in starts {
        for dir in start.ancestors() {
            for candidate in [dir.join("presets"), dir.join("mods").join("film_sims").join("presets")] {
                if candidate.is_dir() && has_bzlook(&candidate) {
                    return Some(candidate);
                }
            }
        }
    }
    None
}

pub fn load_library(dir: &Path) -> (Vec<Entry>, Vec<String>) {
    let mut entries = Vec::new();
    let mut errors = Vec::new();
    let mut paths: Vec<PathBuf> = std::fs::read_dir(dir)
        .map(|it| it.flatten().map(|e| e.path()).filter(|p| p.extension().is_some_and(|x| x.eq_ignore_ascii_case("bzlook"))).collect())
        .unwrap_or_default();
    paths.sort();
    for path in paths {
        match load_bzlook(&path) {
            Ok(look) => entries.push(Entry::from_look(look, Some(path))),
            Err(err) => errors.push(format!("{}: {err}", path.display())),
        }
    }
    (entries, errors)
}

/// The presets compiled into the executable (see [`crate::bundled_presets`]).
pub fn load_bundled_library() -> (Vec<Entry>, Vec<String>) {
    let mut entries = Vec::new();
    let mut errors = Vec::new();
    for (file, bytes) in bundled_presets::BUNDLED {
        match load_bzlook_bytes(bytes) {
            Ok(look) => entries.push(Entry::from_look(look, None)),
            Err(err) => errors.push(format!("bundled {file}: {err}")),
        }
    }
    (entries, errors)
}

/// Disk presets first, then every bundled preset whose name is not already in the library. The
/// portable executable therefore starts with all twelve looks when nothing is on disk beside it, and
/// a user's updated copy of a preset still wins.
pub fn load_library_with_bundled(dir: Option<&Path>) -> (Vec<Entry>, Vec<String>) {
    let (mut entries, mut errors) = dir.map(load_library).unwrap_or_default();
    let (bundled, bundled_errors) = load_bundled_library();
    errors.extend(bundled_errors);
    for entry in bundled {
        if !entries.iter().any(|e| e.name == entry.name) {
            entries.push(entry);
        }
    }
    (entries, errors)
}

/// Which bodies an export writes.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum ModTargetSelection {
    /// `a6400` and `a6100` side by side (`<dir>/a6400/mods/…` and `<dir>/a6100/mods/…`).
    Both,
    A6400,
    A6100,
}

impl ModTargetSelection {
    pub fn label(self) -> &'static str {
        match self {
            ModTargetSelection::Both => "a6400 + a6100",
            ModTargetSelection::A6400 => "a6400 only",
            ModTargetSelection::A6100 => "a6100 only",
        }
    }

    pub fn targets(self) -> &'static [&'static str] {
        match self {
            ModTargetSelection::Both => &["a6400", "a6100"],
            ModTargetSelection::A6400 => &["a6400"],
            ModTargetSelection::A6100 => &["a6100"],
        }
    }
}

/// Write `SMOD v2` `.mod` files for the selected look without any external tool.
///
/// Mod Loader v2 loads every `*.mod` under `<SD>:\mods\`. With both bodies selected the files go to
/// `<dir>/a6400/mods/<Look>.mod` and `<dir>/a6100/mods/<Look>.mod`, so the two SD cards can be
/// prepared from one folder. With one body the file goes straight to a folder that is already named
/// `mods`, or otherwise into `<dir>/mods/`, so pointing at a card root (`E:\`) or at `E:\mods\`
/// both produce `E:\mods\<Look>.mod`.
pub fn export_mods(dir: &Path, selected: &Entry, targets: ModTargetSelection) -> Result<Vec<PathBuf>, String> {
    if !selected.look.spatial.is_empty() {
        return Err(format!(
            "{} writes /usr/sabin spatial tables; a Film Sims .mod only swaps EC/FC, the A0/A1/B2 matrices and \
             RefAdr[0]. Build it with mods/arri_isp/arrivederci instead.",
            selected.name
        ));
    }
    let file = bionz_preset::mod_filename(&selected.name);
    let both = targets == ModTargetSelection::Both;
    let mut written = Vec::new();
    for target in targets.targets() {
        let table = selected.table(target).ok_or_else(|| format!("{} has no {target} table (it has: {})", selected.name, selected.targets().join(", ")))?;
        let blob = emit_smod_v2(&selected.name, target, table).map_err(|e| format!("{target}: {e}"))?;
        let folder = mod_folder(dir, target, both);
        std::fs::create_dir_all(&folder).map_err(|e| format!("{}: {e}", folder.display()))?;
        let path = folder.join(&file);
        std::fs::write(&path, &blob).map_err(|e| format!("{}: {e}", path.display()))?;
        written.push(path);
    }
    Ok(written)
}

/// [`export_mods`] plus the two messages the UI shows: a dialog detail block listing every file, and
/// a one-line summary for the status bar. Both the Export dialog and the headless entry point use
/// this, so they cannot drift apart.
pub fn export_mods_report(dir: &Path, selected: &Entry, targets: ModTargetSelection) -> Result<(String, String), String> {
    let files = export_mods(dir, selected, targets)?;
    let summary = format!(
        "Wrote {} .mod file(s) for {}. Copy them to <SD>:\\mods\\ if they are not there yet, then enable the look in \
         MENU -> Mods. Nothing was sent to a camera.",
        files.len(),
        targets.label()
    );
    let detail = files.iter().map(|f| f.display().to_string()).collect::<Vec<_>>().join("\n");
    Ok((summary, detail))
}

/// Where a `.mod` goes for one body: `<dir>/<body>/mods` when both bodies are exported, otherwise
/// `<dir>` if it is already named `mods`, else `<dir>/mods`.
fn mod_folder(dir: &Path, target: &str, both: bool) -> PathBuf {
    if both {
        dir.join(target).join("mods")
    } else if dir.file_name().is_some_and(|n| n.eq_ignore_ascii_case("mods")) {
        dir.to_path_buf()
    } else {
        dir.join("mods")
    }
}

/// Every library look the Live Picker can carry for `target`, in library order, plus the names it had
/// to skip and why.
pub fn live_candidates<'a>(all: &'a [Entry], target: &str) -> (Vec<(&'a str, &'a CompiledTables)>, Vec<String>) {
    let mut looks = Vec::new();
    let mut skipped = Vec::new();
    for entry in all {
        let Some(table) = entry.table(target) else { continue };
        if !entry.look.spatial.is_empty() {
            skipped.push(format!("{} (spatial tables)", entry.name));
        } else if let Some(reason) = bionz_preset::smod_v2::live_picker_skip_reason(&entry.name, table) {
            skipped.push(format!("{} ({reason})", entry.name));
        } else if looks.len() >= bionz_preset::smod_v2::LIVE_MAX_LOOKS {
            skipped.push(format!("{} (the picker carries at most {} looks)", entry.name, bionz_preset::smod_v2::LIVE_MAX_LOOKS));
        } else {
            looks.push((entry.name.as_str(), table));
        }
    }
    (looks, skipped)
}

/// Write the Live Picker `FilmSimulations.mod` (up to 15 looks, switched live in Live View) for the
/// selected bodies. Same destination rules as [`export_mods`].
pub fn export_live_mods(dir: &Path, all: &[Entry], targets: ModTargetSelection) -> Result<Vec<PathBuf>, String> {
    let both = targets == ModTargetSelection::Both;
    let mut written = Vec::new();
    for target in targets.targets() {
        let (looks, skipped) = live_candidates(all, target);
        if looks.is_empty() {
            return Err(format!(
                "no look in the library can go into the {target} Live Picker{}",
                if skipped.is_empty() { String::new() } else { format!(": {}", skipped.join(", ")) }
            ));
        }
        let owned: Vec<(String, CompiledTables)> = looks.iter().map(|(name, table)| ((*name).to_string(), (*table).clone())).collect();
        let blob = emit_live_smod_v2(target, &owned).map_err(|e| format!("{target}: {e}"))?;
        let folder = mod_folder(dir, target, both);
        std::fs::create_dir_all(&folder).map_err(|e| format!("{}: {e}", folder.display()))?;
        let path = folder.join(bionz_preset::smod_v2::LIVE_MOD_FILENAME);
        std::fs::write(&path, &blob).map_err(|e| format!("{}: {e}", path.display()))?;
        written.push(path);
    }
    Ok(written)
}

/// [`export_live_mods`] plus the dialog detail and the status-bar summary.
pub fn export_live_mods_report(dir: &Path, all: &[Entry], targets: ModTargetSelection) -> Result<(String, String), String> {
    let files = export_live_mods(dir, all, targets)?;
    let (looks, skipped) = live_candidates(all, targets.targets()[0]);
    let mut summary = format!(
        "Wrote {} Live Picker .mod file(s) with {} look(s) for {}. Press MENU -> Mods -> Film Simulations, then Left/Right \
         switches looks and ENTER locks one. Nothing was sent to a camera.",
        files.len(),
        looks.len(),
        targets.label()
    );
    if !skipped.is_empty() {
        summary.push_str(&format!(" Skipped: {}.", skipped.join(", ")));
    }
    let detail = files.iter().map(|f| f.display().to_string()).collect::<Vec<_>>().join("\n");
    Ok((summary, detail))
}

/// Import a preset file. `.cube` and `.a6lt` are fitted or composed onto the stock tables of `base`.
pub fn import_file(path: &Path, base: Option<&Bzlook>) -> Result<Entry, String> {
    let ext = path.extension().and_then(|e| e.to_str()).unwrap_or_default().to_ascii_lowercase();
    let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("Imported").to_string();
    match ext.as_str() {
        "bzlook" => load_bzlook(path).map(|look| Entry::from_look(look, Some(path.to_path_buf()))).map_err(|e| e.to_string()),
        "cube" => {
            let base = base.ok_or("load a .bzlook preset first: a .cube is fitted onto its stock tables")?;
            let text = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
            let cube = parse_cube(&text).map_err(|e| e.to_string())?;
            let (preset, report) = decompose(&cube, &Profile::a6400());
            let mut look = compose_look(&stem, &preset, base, "cube")?;
            look.meta.cube_sha256 = bionz_compare::file_sha256(path).unwrap_or_default();
            let mut entry = Entry::from_look(look, None);
            entry.report = format!("fit mean dE76 {:.2}, max {:.2}, rail nodes {}", report.mean_de76, report.max_de76, report.at_rail_nodes);
            entry.unsaved = true;
            Ok(entry)
        }
        "a6lt" => {
            let base = base.ok_or("load a .bzlook preset first: an .a6lt is composed onto its stock tables")?;
            let bytes = std::fs::read(path).map_err(|e| e.to_string())?;
            let preset = unpack_a6lt(&bytes, &Profile::a6400()).map_err(|e| e.to_string())?;
            let mut entry = Entry::from_look(compose_look(&stem, &preset, base, "a6lt")?, None);
            entry.unsaved = true;
            Ok(entry)
        }
        _ => Err(format!("{} is not a .bzlook, .cube or .a6lt preset", path.display())),
    }
}

/// Fit a recipe and compose it onto every target table of `base`.
pub fn compile_recipe(recipe: &Recipe, base: &Bzlook) -> Result<(Bzlook, String), String> {
    let cube = recipe_cube(recipe, RECIPE_CUBE_SIZE);
    let (preset, report) = decompose(&cube, &Profile::a6400());
    let mut look = compose_look(&recipe.name, &preset, base, "recipe")?;
    look.meta = LookMeta { intent_kind: "recipe".into(), ..base.meta.clone() };
    look.meta.name = recipe.name.clone();
    look.recipe_toml = toml::to_string(recipe).map_err(|e| e.to_string())?;
    let summary = format!(
        "fit mean dE76 {:.2}, p95 {:.2}, max {:.2}, rail nodes {}",
        report.mean_de76, report.p95_de76, report.max_de76, report.at_rail_nodes
    );
    let mut reports: Vec<(String, String)> = look
        .compiled
        .iter()
        .map(|(k, _)| {
            (k.clone(), serde_json::json!({ "mean_de76": report.mean_de76, "p95_de76": report.p95_de76, "max_de76": report.max_de76,
                "at_rail_nodes": report.at_rail_nodes, "cube_size": RECIPE_CUBE_SIZE, "mode": "photo", "tool": "BIONZ Look Lab" }).to_string())
        })
        .collect();
    for (k, _) in &look.compiled_movie {
        reports.push((
            format!("{k}_movie"),
            serde_json::json!({ "mean_de76": report.mean_de76, "p95_de76": report.p95_de76, "max_de76": report.max_de76,
                "at_rail_nodes": report.at_rail_nodes, "cube_size": RECIPE_CUBE_SIZE, "mode": "movie", "tool": "BIONZ Look Lab" }).to_string(),
        ));
    }
    look.report_json = reports;
    Ok((look, summary))
}

fn compose_look(name: &str, preset: &OraclePreset, base: &Bzlook, kind: &str) -> Result<Bzlook, String> {
    if base.compiled.is_empty() {
        return Err("the base preset has no compiled target tables".into());
    }
    let compose_photo = |stock: &CompiledTables| {
        let (ec, fc) = apply_fitted_gamma(&stock.stock_ec, &stock.stock_fc, &preset.gamma, 4095);
        CompiledTables {
            ec,
            fc,
            a0: compose_stock_matrix(&stock.stock_a0, &preset.matrix, 1024).to_vec(),
            a1: compose_stock_matrix(&stock.stock_a1, &preset.matrix, 1024).to_vec(),
            b2: compose_stock_matrix(&stock.stock_b2, &preset.matrix, 1024).to_vec(),
            refadr: pack_refadr(&preset.delta),
            ..stock.clone()
        }
    };
    let compiled: Vec<(String, CompiledTables)> =
        base.compiled.iter().map(|(key, stock)| (key.clone(), compose_photo(stock))).collect();
    // Movie variant: evaluate the fitted sRGB tone directly at linear_to_srgb(knot / 8191)
    // so highlights above stock_ec's 803 plateau keep smooth gradation.
    let compiled_movie: Vec<(String, CompiledTables)> = base
        .compiled_movie
        .iter()
        .map(|(key, stock)| {
            let exps = stock.ec_exponents.unwrap_or(bionz_core::EC_RECORD31_EXPONENTS);
            let ec = apply_movie_gamma(&preset.gamma, 4095, &exps);
            let table = CompiledTables {
                ec,
                fc: stock.stock_fc.clone(),
                a0: compose_stock_matrix(&stock.stock_a0, &preset.matrix, 1024).to_vec(),
                a1: compose_stock_matrix(&stock.stock_a1, &preset.matrix, 1024).to_vec(),
                b2: compose_stock_matrix(&stock.stock_b2, &preset.matrix, 1024).to_vec(),
                refadr: pack_refadr(&preset.delta),
                ec_exponents: Some(exps),
                ..stock.clone()
            };
            (key.clone(), table)
        })
        .collect();
    Ok(Bzlook {
        meta: LookMeta {
            name: name.into(),
            author: String::new(),
            version: 1,
            uuid: String::new(),
            intent_kind: kind.into(),
            tags: Vec::new(),
            notes: String::new(),
            created_with: "BIONZ Look Lab".into(),
            cube_sha256: String::new(),
        },
        recipe_toml: String::new(),
        compiled,
        compiled_movie,
        report_json: Vec::new(),
        thumb_png: Vec::new(),
        spatial: base.spatial.clone(),
    })
}

pub fn save(entry: &mut Entry, path: &Path) -> Result<(), String> {
    entry.look.meta.name = entry.name.clone();
    if let Some(recipe) = &mut entry.recipe {
        recipe.name = entry.name.clone();
        entry.look.recipe_toml = toml::to_string(recipe).map_err(|e| e.to_string())?;
    }
    store_bzlook(path, &entry.look).map_err(|e| e.to_string())?;
    entry.path = Some(path.to_path_buf());
    entry.unsaved = false;
    Ok(())
}

pub fn slug(name: &str) -> String {
    let s: String = name.chars().map(|c| if c.is_ascii_alphanumeric() { c.to_ascii_lowercase() } else { '-' }).collect();
    s.split('-').filter(|p| !p.is_empty()).collect::<Vec<_>>().join("-")
}

/// Write `film_sim_data_<target>.h` for the selected preset and `film_sim_live_data_<target>.h` for
/// every library preset that has that target. Only targets the presets were compiled for are written.
///
/// Refuses tables whose stock is synthetic: the mod writes the `*_STOCK` arrays back when it turns
/// off, and `COLOR_A0_PA` lies in the backup-persisted color matrix table (imaging hazard H01), so
/// synthetic stock would be restored onto the camera and could persist across power cycles.
pub fn pack(dir: &Path, selected: &Entry, all: &[Entry]) -> Result<Vec<PathBuf>, String> {
    if selected.look.compiled.iter().any(|(_, t)| t.base_matrix.is_some()) || !selected.look.spatial.is_empty() {
        return Err(format!(
            "{} writes EC record 31, the Color Mode B8 matrix or /usr/sabin tables; Film Sims headers target a \
             different EC table and the A0/A1/B2 matrices. Build it with mods/arri_isp/arrivederci instead.",
            selected.name
        ));
    }
    if let Some((key, _)) = selected.look.compiled.iter().find(|(_, t)| t.has_synthetic_stock()) {
        return Err(format!(
            "{}: the {key} stock tables are synthetic (no av-cam.bin hash). Packed headers would restore wrong \
             values on the camera. Build the mod with mods/film_sims/studio_core.py, which reads the real stock \
             tables from av-cam.bin.",
            selected.name
        ));
    }
    std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let mut written = Vec::new();
    for (key, table) in &selected.look.compiled {
        let path = dir.join(format!("film_sim_data_{key}.h"));
        let text = emit_film_sim_data(&selected.name, key, table).map_err(|e| format!("{key}: {e}"))?;
        std::fs::write(&path, text).map_err(|e| e.to_string())?;
        written.push(path);
        let looks: Vec<(String, CompiledTables)> = all
            .iter()
            .filter(|e| e.look.spatial.is_empty())
            .filter_map(|e| e.table(key).filter(|t| !t.has_synthetic_stock() && t.base_matrix.is_none()).map(|t| (e.name.clone(), t.clone())))
            .collect();
        if looks.len() > 1 {
            let path = dir.join(format!("film_sim_live_data_{key}.h"));
            let text = emit_live_header(key, &looks).map_err(|e| format!("{key} live: {e}"))?;
            std::fs::write(&path, text).map_err(|e| e.to_string())?;
            written.push(path);
        }
    }
    Ok(written)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn presets() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../mods/film_sims/presets")
    }

    #[test]
    fn library_loads_every_shipped_preset_with_its_recipe() {
        let (entries, errors) = load_library(&presets());
        assert!(errors.is_empty(), "{errors:?}");
        assert!(entries.len() >= 7);
        assert!(entries.iter().all(|e| e.recipe.is_some() && e.table("a6400").is_some()));
        assert!(entries.iter().all(|e| e.has_movie_table("a6400")), "every recipe ships a Movie variant");
    }

    #[test]
    fn recipe_compile_changes_the_tables_and_keeps_stock() {
        let (entries, _) = load_library(&presets());
        let chrome = entries.iter().find(|e| e.name == "Classic Chrome").unwrap();
        let mut recipe = chrome.recipe.clone().unwrap();
        recipe.saturation = 0.2;
        let (look, summary) = compile_recipe(&recipe, &chrome.look).unwrap();
        assert!(summary.contains("dE76"));
        let new = &look.compiled[0].1;
        let old = chrome.table("a6400").unwrap();
        assert_eq!(new.stock_ec, old.stock_ec);
        assert_ne!(new.refadr, old.refadr);
        assert_eq!(look.compiled_movie.len(), look.compiled.len(), "Photo and Movie variants are compiled together");
        let movie_old = chrome.movie_table("a6400").unwrap();
        let movie_new = &look.compiled_movie[0].1;
        assert_eq!(movie_new.stock_ec, movie_old.stock_ec);
        assert_eq!(movie_new.stock_fc, movie_old.stock_fc);
        assert_ne!(movie_new.refadr, movie_old.refadr);
        assert_ne!(movie_new.ec, new.ec, "Movie EC is fitted in the record-31 domain, not the Photo EC");
        assert_ne!(movie_new.fc, new.fc, "Movie FC keeps stock_fc rather than the Photo FC curve");
        assert_eq!(movie_new.ec_exponents, Some(bionz_core::EC_RECORD31_EXPONENTS));
        let reread = Entry::from_look(look, None);
        assert!((reread.recipe.unwrap().saturation - 0.2).abs() < 1e-9);
    }

    #[test]
    fn a6lt_import_composes_onto_stock() {
        let (entries, _) = load_library(&presets());
        let a6lt = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../a6400-lut-converter/exported/arri-logc3-awg3.a6lt");
        let entry = import_file(&a6lt, Some(&entries[0].look)).unwrap();
        assert_eq!(entry.name, "arri-logc3-awg3");
        assert!(entry.table("a6400").is_some());
        assert!(import_file(&a6lt, None).is_err());
    }

    #[test]
    fn pack_refuses_synthetic_stock() {
        let (mut entries, _) = load_library(&presets());
        // The shipped presets are now compiled against real av-cam stock; make one synthetic again.
        for (_, t) in &mut entries[0].look.compiled {
            t.flags |= 1;
            t.avcam_sha256 = [0u8; 32];
        }
        assert!(entries[0].table("a6400").unwrap().has_synthetic_stock());
        let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/look-lab-pack-test");
        let err = pack(&dir, &entries[0], &entries).unwrap_err();
        assert!(err.contains("synthetic"), "{err}");
    }

    #[test]
    fn every_shipped_preset_packs_with_real_stock() {
        let (entries, _) = load_library(&presets());
        assert_eq!(entries.len(), 12);
        for entry in &entries {
            let table = entry.table("a6400").expect("a6400 table");
            assert!(!table.has_synthetic_stock(), "{} still has synthetic stock", entry.name);
            assert_ne!(table.avcam_sha256, [0u8; 32]);
        }
        let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/look-lab-pack-real");
        let _ = std::fs::remove_dir_all(&dir);
        for entry in &entries {
            let files = pack(&dir, entry, &entries).unwrap_or_else(|e| panic!("{}: {e}", entry.name));
            for target in ["a6400", "a6100"] {
                assert!(files.iter().any(|p| p.to_string_lossy().contains(&format!("film_sim_data_{target}.h"))));
                assert!(files.iter().any(|p| p.to_string_lossy().contains(&format!("film_sim_live_data_{target}.h"))));
            }
        }
    }

    #[test]
    fn pack_writes_single_and_live_headers_for_real_stock() {
        let (mut entries, _) = load_library(&presets());
        for e in &mut entries {
            for (_, t) in &mut e.look.compiled {
                t.flags = 0;
                t.avcam_sha256 = [0xdd; 32];
            }
        }
        let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/look-lab-pack-test");
        let files = pack(&dir, &entries[0], &entries).unwrap();
        assert_eq!(files.len(), 4, "single + live header per target");
        let joined: Vec<String> = files.iter().map(|p| std::fs::read_to_string(p).unwrap()).collect();
        assert!(joined.iter().any(|t| t.contains("EC_GAMMA_PA 0x03e55330u")));
        assert!(joined.iter().any(|t| t.contains("EC_GAMMA_PA 0x03e553b0u")));
    }

    #[test]
    fn slug_is_file_safe() {
        assert_eq!(slug("ACROS B&W"), "acros-b-w");
    }

    #[test]
    fn bundled_presets_load_with_no_files_on_disk() {
        let (entries, errors) = load_bundled_library();
        assert!(errors.is_empty(), "{errors:?}");
        assert_eq!(entries.len(), 12);
        assert_eq!(entries.len(), bundled_presets::COUNT);
        assert!(entries.iter().all(|e| e.path.is_none() && e.recipe.is_some()));
        for entry in &entries {
            for target in ["a6400", "a6100"] {
                let table = entry.table(target).unwrap_or_else(|| panic!("{} has no {target} table", entry.name));
                assert!(!table.has_synthetic_stock(), "{} {target} stock is synthetic", entry.name);
                assert!(entry.has_movie_table(target), "{} has no {target} Movie variant", entry.name);
            }
        }
        // A missing folder is not an error: the portable executable still gets all twelve looks.
        let (merged, errors) = load_library_with_bundled(Some(Path::new("no-such-preset-folder")));
        assert!(errors.is_empty(), "{errors:?}");
        assert_eq!(merged.len(), 12);
    }

    #[test]
    fn disk_presets_win_over_bundled_duplicates() {
        let (mut disk, _) = load_library(&presets());
        disk.truncate(3);
        let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/look-lab-merge-disk");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        for entry in &disk {
            let path = dir.join(entry.path.as_ref().unwrap().file_name().unwrap());
            std::fs::copy(entry.path.as_ref().unwrap(), &path).unwrap();
        }
        let (merged, errors) = load_library_with_bundled(Some(&dir));
        assert!(errors.is_empty(), "{errors:?}");
        assert_eq!(merged.len(), 12, "three disk presets plus the nine other bundled ones");
        assert_eq!(merged.iter().filter(|e| e.name == "Classic Chrome").count(), 1);
        assert!(merged.iter().filter(|e| e.name == "Classic Chrome").all(|e| e.path.is_some()), "the disk copy wins");
    }

    #[test]
    fn export_writes_a_mod_for_both_targets() {
        let (entries, _) = load_library(&presets());
        let chrome = entries.iter().find(|e| e.name == "Classic Chrome").unwrap();
        let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/look-lab-mod-export");
        let _ = std::fs::remove_dir_all(&dir);
        let files = export_mods(&dir, chrome, ModTargetSelection::Both).unwrap();
        assert_eq!(files.len(), 2);
        let a6400 = dir.join("a6400").join("mods").join("ClassicChrome.mod");
        let a6100 = dir.join("a6100").join("mods").join("ClassicChrome.mod");
        assert_eq!(files, vec![a6400.clone(), a6100.clone()]);
        let read = |path: &Path| {
            let blob = std::fs::read(path).unwrap();
            assert_eq!(&blob[0..4], b"SMOD");
            assert_eq!(u32::from_le_bytes(blob[4..8].try_into().unwrap()), 2);
            assert_eq!(u32::from_le_bytes(blob[8..12].try_into().unwrap()), 16, "FLAG_ACTION");
            assert_eq!(&blob[12..26], b"Classic Chrome");
            let code_len = u32::from_le_bytes(blob[44..48].try_into().unwrap()) as usize;
            assert_eq!(blob.len(), 68 + code_len);
            let checksum = u32::from_le_bytes(blob[48..52].try_into().unwrap());
            assert_eq!(bionz_preset::smod_v2::fnv1a_32(&blob[68..]), checksum);
            blob
        };
        let blob6400 = read(&a6400);
        let blob6100 = read(&a6100);
        let payload = bionz_preset::template_layout().unwrap();
        let ec_at = |blob: &Vec<u8>| {
            let at = 68 + payload.code_field("ec_gamma_pa").unwrap();
            u32::from_le_bytes(blob[at..at + 4].try_into().unwrap())
        };
        assert_eq!(ec_at(&blob6400), bionz_preset::known_target("a6400").unwrap().ec_gamma_pa);
        assert_eq!(ec_at(&blob6100), bionz_preset::known_target("a6100").unwrap().ec_gamma_pa);
        assert_ne!(blob6400, blob6100, "the two bodies differ in their physical addresses");
    }

    #[test]
    fn single_target_export_respects_a_mods_folder() {
        let (entries, _) = load_library(&presets());
        let chrome = entries.iter().find(|e| e.name == "Classic Chrome").unwrap();
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/look-lab-mod-single");
        let _ = std::fs::remove_dir_all(&root);
        // Card root: the file lands in <root>/mods/.
        let files = export_mods(&root, chrome, ModTargetSelection::A6100).unwrap();
        assert_eq!(files, vec![root.join("mods").join("ClassicChrome.mod")]);
        // A folder already named mods (as an SD card opened at <SD>:\mods) gets the file directly.
        let mods = root.join("mods");
        let files = export_mods(&mods, chrome, ModTargetSelection::A6400).unwrap();
        assert_eq!(files, vec![mods.join("ClassicChrome.mod")]);
        assert!(mods.join("ClassicChrome.mod").is_file());
        assert!(!mods.join("mods").exists(), "no nested mods folder inside a mods folder");
    }

    #[test]
    fn live_picker_export_writes_filmsimulations_for_both_bodies() {
        let (entries, _) = load_library(&presets());
        let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/look-lab-live-export");
        let _ = std::fs::remove_dir_all(&dir);
        let files = export_live_mods(&dir, &entries, ModTargetSelection::Both).unwrap();
        let a6400 = dir.join("a6400").join("mods").join("FilmSimulations.mod");
        let a6100 = dir.join("a6100").join("mods").join("FilmSimulations.mod");
        assert_eq!(files, vec![a6400.clone(), a6100.clone()]);
        for path in [&a6400, &a6100] {
            let blob = std::fs::read(path).unwrap();
            assert_eq!(&blob[..4], b"SMOD");
            assert_eq!(u32::from_le_bytes(blob[4..8].try_into().unwrap()), 2);
            let flags = u32::from_le_bytes(blob[8..12].try_into().unwrap());
            assert_eq!(flags, 20, "FLAG_ACTION | FLAG_RELOC");
            assert_eq!(&blob[12..28], b"Film Simulations");
            assert_eq!(u32::from_le_bytes(blob[52..56].try_into().unwrap()), 48, "bss");
            assert_eq!(u32::from_le_bytes(blob[64..68].try_into().unwrap()), 1, "one hook");
        }
        assert_ne!(std::fs::read(&a6400).unwrap(), std::fs::read(&a6100).unwrap(), "each body has its own addresses");
        // The single-look export of one preset is a different file with a different name.
        let chrome = entries.iter().find(|e| e.name == "Classic Chrome").unwrap();
        let single = export_mods(&dir, chrome, ModTargetSelection::A6400).unwrap();
        assert_eq!(single, vec![dir.join("mods").join("ClassicChrome.mod")]);
        assert!(a6400.is_file() && single[0].is_file());
    }

    #[test]
    fn live_picker_skips_looks_it_cannot_carry_and_says_so() {
        let (mut entries, _) = load_library(&presets());
        entries[0].look.spatial.push(("dcv_psf_table_movie_bayer.bin".into(), vec![0u8; 16]));
        let (looks, skipped) = live_candidates(&entries, "a6400");
        assert_eq!(looks.len(), 11);
        assert_eq!(looks[0].0, entries[1].name);
        assert!(skipped.iter().any(|s| s.contains(&entries[0].name) && s.contains("spatial")), "{skipped:?}");
        let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/look-lab-live-skip");
        let _ = std::fs::remove_dir_all(&dir);
        let (summary, detail) = export_live_mods_report(&dir, &entries, ModTargetSelection::A6100).unwrap();
        assert!(summary.contains("11 look(s)"), "{summary}");
        assert!(summary.contains("Skipped:"), "{summary}");
        assert!(detail.ends_with("FilmSimulations.mod"), "{detail}");
        // No look at all for a body that is not in the library is an error, not an empty mod.
        let mut only_6400: Vec<Entry> = Vec::new();
        for entry in &entries {
            if entry.table("a6400").is_some() && entry.table("a6100").is_none() {
                only_6400.push(Entry::from_look(entry.look.clone(), None));
            }
        }
        if !only_6400.is_empty() {
            assert!(export_live_mods(&dir, &only_6400, ModTargetSelection::A6100).unwrap_err().contains("no look"));
        }
    }

    #[test]
    fn export_refuses_spatial_presets_and_missing_targets() {        let (entries, _) = load_library(&presets());
        let mut look = entries[0].look.clone();
        look.spatial.push(("dcv_psf_table_movie_bayer.bin".into(), vec![0u8; 16]));
        let spatial = Entry::from_look(look, None);
        let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/look-lab-mod-refusal");
        let err = export_mods(&dir, &spatial, ModTargetSelection::Both).unwrap_err();
        assert!(err.contains("arrivederci"), "{err}");
        let mut one = entries[0].look.clone();
        one.compiled.retain(|(k, _)| k == "a6400");
        one.compiled_movie.clear();
        let only_6400 = Entry::from_look(one, None);
        assert!(export_mods(&dir, &only_6400, ModTargetSelection::Both).unwrap_err().contains("a6100"));
    }
}


