#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
//! BIONZ Look Lab. Develops RAW or display images through compiled BIONZ X preset tables on the GPU
//! and compares the result with the stock camera tables or with a camera JPEG. No USB or camera calls.
//!
//! The UI thread never runs the ISP chain or the fitter. A render worker and a fit worker take the
//! newest request only. While a control moves, the picture is rendered from a small proxy image;
//! when the control is released it is rendered at preview size, and at full size when zoomed in.
//!
//! Release builds set `windows_subsystem = "windows"` above, so the portable `.exe` opens without a
//! console window.

mod bundled_presets;
mod front_state;
mod headless;
mod looks;
mod scopes;
mod spatial_src;
mod worker;

use std::path::{Path, PathBuf};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::Arc;
use std::time::{Duration, Instant};

use bionz_core::{encoded_to_rgb8, integer_pipeline, stage_catalog, stage_display, Badge, BaseMatrixSource, ColorMatrixMode, IntegerParams, IspRouting, LabMode, RgbI};
use bionz_fit::Recipe;
use bionz_preset::CompiledTables;
use bionz_raw::{LinearImage, RawFrame, SourceKind};
use front_state::{DemosaicChoice, SideTables, SpatialPath, SpatialSettings};
use eframe::egui::{self, Color32, RichText, TextureHandle, TextureOptions};
use looks::Entry;
use worker::{FitJob, Fitted, RenderJob, Rendered, Slot, Tier};

/// Long side of the image used while a slider moves (a few milliseconds per render).
const PROXY_SIDE: usize = 640;
/// Long side of the idle preview. Zooming past it renders the full decoded image.
const PREVIEW_SIDE: usize = 1600;
const THUMB_SIDE: usize = 72;
const TAP_SIDE: usize = 220;
const DISPLAY_IMAGE_EV: f32 = -3.0;
/// After the last edit, stay on the proxy this long before rendering the preview.
const SETTLE: Duration = Duration::from_millis(180);
const ACCENT: Color32 = Color32::from_rgb(232, 160, 60);

fn main() -> eframe::Result<()> {
    // `--export-mod <DIR>` runs the Export dialog's write path and prints a JSON receipt, so the
    // portable build can be verified from a folder that holds only the executable. Anything else is
    // a normal GUI launch.
    let args: Vec<std::ffi::OsString> = std::env::args_os().skip(1).collect();
    if let Some(code) = headless::run(&args) {
        std::process::exit(code);
    }
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default().with_inner_size([1440.0, 900.0]).with_min_inner_size([1000.0, 640.0]).with_title("BIONZ Look Lab"),
        ..Default::default()
    };
    eframe::run_native("BIONZ Look Lab", options, Box::new(|cc| Ok(Box::new(LabApp::new(cc)))))
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum GammaPath {
    StillJpeg,
    MovieEc,
    StillFc,
    EcThenFc,
    FcThenEc,
}

impl GammaPath {
    fn label(self) -> &'static str {
        match self {
            GammaPath::StillJpeg => "Still: camera JPEG (sRGB)",
            GammaPath::MovieEc => "Movie: EC gamma only",
            GammaPath::StillFc => "Still: FC gamma only",
            GammaPath::EcThenFc => "EC then FC (experimental)",
            GammaPath::FcThenEc => "FC then EC (experimental)",
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum View {
    Emulated,
    Split,
    SideBySide,
    Difference,
    DeltaE,
    Reference,
}

/// Decoded image at three sizes, shared with the workers without copying.
struct Source {
    path: PathBuf,
    kind: SourceKind,
    note: String,
    full: Arc<LinearImage>,
    preview: Arc<LinearImage>,
    proxy: Arc<LinearImage>,
    tap: LinearImage,
    thumb: LinearImage,
    /// The decoded CFA, kept so the spatial front end can run again with other tables or settings.
    raw: Option<Arc<RawFrame>>,
    /// `front_state::front_key` of the tables and settings these tiers were developed with.
    front_key: u64,
    /// Side B tiers `[proxy, preview, full]` when its spatial tables differ from side A.
    b: Option<[Arc<LinearImage>; 3]>,
}

impl Source {
    fn build(path: PathBuf, full: LinearImage) -> Self {
        let [proxy, preview, full] = tiers(full);
        let tap = bionz_raw::downscale((*proxy).clone(), TAP_SIDE);
        let thumb = bionz_raw::downscale((*proxy).clone(), THUMB_SIDE);
        Self { path, kind: full.kind, note: full.note.clone(), full, preview, proxy, tap, thumb, raw: None, front_key: 0, b: None }
    }

    /// Develop a RAW frame for both sides with the given tables.
    fn develop(path: PathBuf, raw: Arc<RawFrame>, settings: &SpatialSettings, a: &SideTables, b: &SideTables) -> Result<Self, String> {
        let image_a = front_state::develop(&raw, settings, a)?;
        let mut source = Self::build(path, image_a);
        if b.fingerprint != a.fingerprint {
            source.b = Some(tiers(front_state::develop(&raw, settings, b)?));
        }
        source.front_key = front_state::front_key(settings, a, b);
        source.raw = Some(raw);
        Ok(source)
    }

    fn image(&self, tier: Tier) -> Arc<LinearImage> {
        match tier {
            Tier::Proxy => self.proxy.clone(),
            Tier::Preview => self.preview.clone(),
            Tier::Full => self.full.clone(),
        }
    }

    fn image_b(&self, tier: Tier) -> Arc<LinearImage> {
        match &self.b {
            Some(t) => t[tier as usize].clone(),
            None => self.image(tier),
        }
    }
}

/// Decode a RAW (keeping the CFA for later re-development) or a display image.
fn load_source(path: PathBuf, settings: &SpatialSettings, a: &SideTables, b: &SideTables) -> Result<Source, String> {
    if bionz_raw::is_camera_raw(&path) {
        let raw = Arc::new(bionz_raw::decode_raw_frame(&path).map_err(|e| e.to_string())?);
        Source::develop(path, raw, settings, a, b)
    } else {
        bionz_raw::load_linear(&path, 0).map(|full| Source::build(path, full)).map_err(|e| e.to_string())
    }
}

/// `[proxy, preview, full]`, indexed by `Tier as usize`.
fn tiers(full: LinearImage) -> [Arc<LinearImage>; 3] {
    let preview = bionz_raw::downscale(full.clone(), PREVIEW_SIDE);
    let proxy = bionz_raw::downscale(preview.clone(), PROXY_SIDE);
    [Arc::new(proxy), Arc::new(preview), Arc::new(full)]
}

struct Frame {
    generation: u64,
    tier: Tier,
    image: Arc<egui::ColorImage>,
    texture: TextureHandle,
}

#[derive(Default)]
struct SlotState {
    generation: u64,
    sent: Option<(u64, Tier)>,
    frame: Option<Frame>,
}

struct Camera {
    path: PathBuf,
    image: Arc<egui::ColorImage>,
    texture: TextureHandle,
}

enum Job {
    Source(PathBuf, Result<Box<Source>, String>),
    Front(u64, Result<Box<Source>, String>),
    Camera(PathBuf, Result<egui::ColorImage, String>),
    Exported(Result<String, String>),
    Metric(u64, (f64, f64), Option<(bionz_compare::SpatialMetrics, bionz_compare::SpatialMetrics)>),
    Derived(u64, View, egui::ColorImage),
}

enum Action {
    OpenSource,
    OpenCamera,
    ClearCamera,
    TogglePairedJpg,
    UseChart,
    Import,
    SaveAs,
    Pack,
    OpenModExport,
    PickModDir,
    WriteMod,
    Export,
    Select(usize),
    Undo,
    Redo,
    ResetRecipe,
    RepairSpatial,
    ZoomFit,
    ZoomTo(f32),
    ZoomBy(f32),
}

/// Which kind of `.mod` the export dialog writes.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum ModKind {
    /// One look, one mod (`<Look>.mod`): EC/FC/A0/A1/B2 + RefAdr swapped once.
    SingleLook,
    /// Every exportable look in one mod (`FilmSimulations.mod`), switched live in Live View.
    LivePicker,
}

impl ModKind {
    #[cfg(test)]
    fn label(self) -> &'static str {
        match self {
            ModKind::SingleLook => "Single look",
            ModKind::LivePicker => "Live picker",
        }
    }
}

/// State of the "Export Camera Mod (.mod)" dialog.
struct ModExport {
    open: bool,
    targets: looks::ModTargetSelection,
    kind: ModKind,
    dir: Option<PathBuf>,
    status: String,
}

impl Default for ModExport {
    fn default() -> Self {
        Self { open: false, targets: looks::ModTargetSelection::Both, kind: ModKind::SingleLook, dir: None, status: String::new() }
    }
}

/// The body of the "Export Camera Mod (.mod)" window.
///
/// Kept out of `update` so a headless test can render it in a bare `egui::Context` and assert that it
/// requests exactly the actions the window's buttons stand for.
fn mod_export_ui(ui: &mut egui::Ui, state: &mut ModExport, look_name: &str, targets_note: &str, actions: &mut Vec<Action>) {
    ui.label(RichText::new(format!("Look: {look_name}")).strong());
    ui.label(RichText::new(targets_note).weak());
    ui.add_space(6.0);
    ui.label(RichText::new("Mod kind").strong());
    ui.horizontal(|ui| {
        for (value, label) in [(ModKind::SingleLook, "Single look (.mod)"), (ModKind::LivePicker, "Live picker (FilmSimulations.mod)")] {
            if ui.selectable_label(state.kind == value, label).clicked() {
                state.kind = value;
            }
        }
    });
    ui.label(
        RichText::new(match state.kind {
            ModKind::SingleLook => "One look in one .mod. Switching it in MENU -> Mods applies or removes that look.",
            ModKind::LivePicker => {
                "Every exportable look in one .mod. It opens in Live View: Left/Right (or Up/Down) switches, ENTER locks the \
                 chosen look, MENU leaves it armed."
            }
        })
        .small()
        .weak(),
    );
    ui.add_space(6.0);
    ui.label(RichText::new("Camera body").strong());
    ui.horizontal(|ui| {
        for (value, label) in [
            (looks::ModTargetSelection::Both, "a6400 + a6100"),
            (looks::ModTargetSelection::A6400, "ILCE-6400 (a6400)"),
            (looks::ModTargetSelection::A6100, "ILCE-6100 (a6100)"),
        ] {
            if ui.selectable_label(state.targets == value, label).clicked() {
                state.targets = value;
            }
        }
    });
    ui.add_space(6.0);
    ui.label(RichText::new("Destination").strong());
    ui.horizontal(|ui| {
        if ui.button("📁 Choose folder or SD card…").clicked() {
            actions.push(Action::PickModDir);
        }
        match &state.dir {
            Some(path) => ui.label(RichText::new(path.display().to_string()).monospace()),
            None => ui.label(RichText::new("no folder chosen yet").weak()),
        };
    });
    ui.label(
        RichText::new(match (state.kind, state.targets) {
            (_, looks::ModTargetSelection::Both) => "Writes <folder>\\a6400\\mods\\ and <folder>\\a6100\\mods\\.",
            _ => "Writes <folder>\\mods\\, or <folder>\\ when the folder is already named mods.",
        })
        .small()
        .weak(),
    );
    ui.add_space(8.0);
    if ui
        .add_enabled(state.dir.is_some(), egui::Button::new("📦 Export .mod"))
        .on_disabled_hover_text("Choose a destination folder first")
        .clicked()
    {
        actions.push(Action::WriteMod);
    }
    if !state.status.is_empty() {
        ui.add_space(6.0);
        ui.separator();
        ui.label(RichText::new(&state.status).monospace());
    }
    ui.add_space(8.0);
    ui.separator();
    ui.label(RichText::new("How it works").strong());
    ui.label(
        "The .mod contains the same content-guarded single-page swaps as the Film Sims builds: EC Record 28, FC Table 0, \
         the A0/A1/B2 color matrices, RefAdr[0] and the CC tuning word. It is generated here, in Rust, from the preset's \
         compiled tables; no Python, WSL or ARM compiler is needed. The address set and every table are written into a \
         fixed payload structure, and the checksum is FNV-1a 32 over the code.",
    );
    ui.add_space(6.0);
    ui.label(
        "Copy the file into <SD>:\\mods\\ (create the folder if needed), then enable the look in MENU -> Mods. \
         Turning it off writes the stock tables back, so do not delete the .mod while it is active.",
    );
    ui.add_space(6.0);
    if ui
        .button("Advanced: write C headers (Pack for Film Sims)")
        .on_hover_text("film_sim_data_<target>.h / film_sim_live_data_<target>.h for the studio_core.py or film_sim.c build")
        .clicked()
    {
        actions.push(Action::Pack);
    }
}

struct LabApp {
    library: Vec<Entry>,
    preset_dir: Option<PathBuf>,
    selected: usize,
    target: String,
    source: Option<Source>,
    camera: Option<Camera>,
    /// Same-stem camera JPEG found next to the opened RAW (never loaded unless asked).
    paired_camera_path: Option<PathBuf>,
    /// Whether compare side B shows the camera JPEG instead of the emulated stock.
    use_paired_jpg: bool,
    ev: f32,
    gamma_path: GammaPath,
    bypass: [bool; 16],
    legal: bool,
    view: View,
    wipe: f32,
    // Viewer transform: points per full-image pixel and the image's top-left offset in the view.
    fit: bool,
    zoom: f32,
    offset: egui::Vec2,
    view_size: egui::Vec2,
    dragging_divider: bool,
    needed_tier: Tier,
    look: SlotState,
    stock: SlotState,
    analysis_key: u64,
    metric: Option<(f64, f64)>,
    spatial_metric: Option<(bionz_compare::SpatialMetrics, bionz_compare::SpatialMetrics)>,
    metric_key: u64,
    derived: Option<(u64, View, TextureHandle)>,
    derived_pending: Option<(u64, View)>,
    scopes: Option<[TextureHandle; 3]>,
    thumbs: Vec<Option<TextureHandle>>,
    tap_texture: Option<TextureHandle>,
    dirty_tap: bool,
    render_tx: Sender<RenderJob>,
    render_rx: Receiver<Rendered>,
    fit_tx: Sender<FitJob>,
    fit_rx: Receiver<Fitted>,
    tx: Sender<Job>,
    rx: Receiver<Job>,
    busy: Vec<String>,
    fit_generation: u64,
    fit_applied: u64,
    last_edit: Instant,
    control_active: bool,
    natural_still_dcv: bool,
    arri_movie_dcv: bool,
    status: String,
    gpu_info: String,
    render_ms: f64,
    tap: usize,
    lut_y: usize,
    undo: Vec<Recipe>,
    redo: Vec<Recipe>,
    editing: bool,
    show_help: bool,
    dark: bool,
    spatial: SpatialSettings,
    stock_spatial: Vec<(String, Vec<u8>)>,
    stock_spatial_note: String,
    arri_movie_spatial: Vec<(String, Vec<u8>)>,
    front_pending: Option<u64>,
    mode: LabMode,
    routing: IspRouting,
    /// Last Movie spatial path, restored when switching back to Movie.
    last_movie_spatial: SpatialPath,
    mod_export: ModExport,
}

impl LabApp {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        apply_theme(&cc.egui_ctx, true);
        let preset_dir = looks::find_preset_dir();
        let (library, errors) = looks::load_library_with_bundled(preset_dir.as_deref());
        let gpu_info = bionz_gpu::adapter_info().unwrap_or_else(|e| format!("no GPU ({e}); CPU fallback"));
        let status = match (&preset_dir, errors.is_empty()) {
            (Some(dir), true) => format!("Loaded {} presets ({} built in) from {}.", library.len(), bundled_presets::COUNT, dir.display()),
            (Some(_), false) => format!("Some presets failed to load: {}", errors.join("; ")),
            (None, true) => format!(
                "Loaded {} built-in presets: the executable needs no files beside it. Use Import to add .bzlook, .cube or .a6lt.",
                library.len()
            ),
            (None, false) => format!("Some built-in presets failed to load: {}", errors.join("; ")),
        };
        let (tx, rx) = channel();
        let (rendered_tx, render_rx) = channel();
        let (fitted_tx, fit_rx) = channel();
        let render_tx = worker::spawn_renderer(rendered_tx, cc.egui_ctx.clone());
        let fit_tx = worker::spawn_fitter(fitted_tx, cc.egui_ctx.clone());
        let thumbs = library.iter().map(|_| None).collect();
        let (stock_spatial, stock_spatial_note) = match spatial_src::find_stock_sabin() {
            Some(dir) => {
                let (tables, problems) = spatial_src::load_stock_tables(&dir);
                let note = if problems.is_empty() {
                    format!("{} stock tables from {} (SHA-256 verified)", tables.len(), dir.display())
                } else {
                    format!("{} stock tables; {}", tables.len(), problems.join("; "))
                };
                (tables, note)
            }
            None => (Vec::new(), "No stock /usr/sabin tables found (set BIONZ_SABIN to the firmware extraction's sabin folder).".into()),
        };
        let arri_movie_spatial = spatial_src::load_arri_movie_tables();
        let mut app = Self {
            library,
            preset_dir,
            selected: 0,
            target: "a6400".into(),
            source: None,
            camera: None,
            paired_camera_path: None,
            use_paired_jpg: false,
            ev: 0.0,
            gamma_path: GammaPath::StillJpeg,
            bypass: [false; 16],
            legal: false,
            view: View::Split,
            wipe: 0.5,
            fit: true,
            zoom: 1.0,
            offset: egui::Vec2::ZERO,
            view_size: egui::vec2(1.0, 1.0),
            dragging_divider: false,
            needed_tier: Tier::Preview,
            look: SlotState::default(),
            stock: SlotState::default(),
            analysis_key: 0,
            metric: None,
            spatial_metric: None,
            metric_key: u64::MAX,
            derived: None,
            derived_pending: None,
            scopes: None,
            thumbs,
            tap_texture: None,
            dirty_tap: true,
            render_tx,
            render_rx,
            fit_tx,
            fit_rx,
            tx,
            rx,
            busy: Vec::new(),
            fit_generation: 0,
            fit_applied: 0,
            last_edit: Instant::now() - SETTLE,
            control_active: false,
            natural_still_dcv: true,
            arri_movie_dcv: false,
            status,
            gpu_info,
            render_ms: 0.0,
            tap: 11,
            lut_y: 2,
            undo: Vec::new(),
            redo: Vec::new(),
            editing: false,
            show_help: false,
            dark: true,
            spatial: SpatialSettings { path: SpatialPath::Still, q: 13, ..Default::default() },
            stock_spatial,
            stock_spatial_note,
            arri_movie_spatial,
            front_pending: None,
            mode: LabMode::Photo,
            routing: IspRouting::default(),
            last_movie_spatial: SpatialPath::Uhd4k,
            mod_export: ModExport::default(),
        };
        // `bionz-look-lab <image> [<camera.jpg>]`, also used by "Open with" in Explorer.
        // Preset files (.bzlook, .cube, .a6lt, .mod, arrivederci_data.h) are imported first, so the
        // image is developed with the imported preset's spatial tables.
        let (presets, args): (Vec<PathBuf>, Vec<PathBuf>) = std::env::args_os().skip(1).map(PathBuf::from).partition(|p| {
            p.extension().and_then(|e| e.to_str()).is_some_and(|e| ["bzlook", "cube", "a6lt", "mod", "h"].contains(&e.to_ascii_lowercase().as_str()))
        });
        for preset in &presets {
            app.import(preset);
        }
        if let Some(first) = args.first() {
            let (a, b) = app.side_inputs();
            match load_source(first.clone(), &app.spatial, &a, &b) {
                Ok(source) => app.set_source(source),
                Err(err) => app.status = format!("Could not open {}: {err}", first.display()),
            }
        }
        if let Some(camera) = args.get(1) {
            app.spawn_camera(camera.clone());
        }
        app
    }

    fn entry(&self) -> Option<&Entry> {
        self.library.get(self.selected)
    }

    /// Whether compare side B is the real camera JPEG (loaded and toggled on) rather than stock.
    fn camera_active(&self) -> bool {
        self.use_paired_jpg && self.camera.is_some()
    }

    /// Spatial tables for side A (the preset's, stock as fallback) and side B (stock).
    fn side_inputs(&self) -> (SideTables, SideTables) {
        let Some(name) = self.spatial.table_name() else {
            let none = front_state::side_tables(&self.spatial, None, "");
            return (none.clone(), none);
        };
        let stock = self.stock_spatial.iter().find(|(n, _)| n == name).map(|(_, b)| b.as_slice());
        let preset = self.entry().and_then(|e| e.look.spatial.iter().find(|(n, _)| n == name)).map(|(_, b)| b.as_slice());
        let a = match preset {
            Some(bytes) => front_state::side_tables(&self.spatial, Some(bytes), "preset"),
            None if self.spatial.path == SpatialPath::Still && self.natural_still_dcv => {
                if let Some(bytes) = stock.and_then(|b| bionz_core::spatial::design_natural_still_table(b, 1.0).ok()) {
                    front_state::side_tables(&self.spatial, Some(&bytes), "fine-tuned natural")
                } else {
                    front_state::side_tables(&self.spatial, stock, "stock")
                }
            }
            None if self.mode == LabMode::Movie && self.arri_movie_dcv => {
                if let Some(bytes) = self.arri_movie_spatial.iter().find(|(n, _)| n == name).map(|(_, b)| b.as_slice()) {
                    front_state::side_tables(&self.spatial, Some(bytes), "Arrivederci v5")
                } else {
                    front_state::side_tables(&self.spatial, stock, "stock")
                }
            }
            None => front_state::side_tables(&self.spatial, stock, "stock"),
        };
        (a, front_state::side_tables(&self.spatial, stock, "stock"))
    }

    /// Re-run the spatial front end in the background when the tables or settings changed.
    fn maybe_develop(&mut self) {
        let Some(source) = &self.source else { return };
        let Some(raw) = source.raw.clone() else { return };
        let (a, b) = self.side_inputs();
        let key = front_state::front_key(&self.spatial, &a, &b);
        if key == source.front_key || self.front_pending == Some(key) {
            return;
        }
        self.front_pending = Some(key);
        if !self.busy.iter().any(|s| s.starts_with("Developing")) {
            self.busy.push("Developing RAW".into());
        }
        let (path, settings, tx) = (source.path.clone(), self.spatial, self.tx.clone());
        std::thread::spawn(move || {
            let result = Source::develop(path, raw, &settings, &a, &b).map(Box::new);
            let _ = tx.send(Job::Front(key, result));
        });
    }

    /// User switches, the selected mode, and the gamma path, applied to compiled tables.
    fn effective(&self, mut params: IntegerParams, stock: bool, custom_ec: bool, table: Option<&CompiledTables>) -> IntegerParams {
        let id = [1024, 0, 0, 0, 1024, 0, 0, 0, 1024];
        let cam_m = self.source.as_ref().and_then(|s| s.raw.as_ref()).and_then(|r| r.cam_to_srgb);
        if self.mode == LabMode::Movie {
            if !stock && custom_ec {
                if let Some(t) = table {
                    bionz_preset::apply_movie_look(&mut params, t, cam_m, self.legal);
                } else {
                    fill_camera_base_matrix(&mut params, cam_m);
                    params.bypass[6] = false;
                    params.bypass[7] = true;
                    params.legal_range = self.legal;
                }
            } else {
                fill_camera_base_matrix(&mut params, cam_m);
                params.bypass[6] = false;
                params.bypass[7] = true;
                params.legal_range = self.legal;
            }
        } else {
            match self.gamma_path {
                GammaPath::StillJpeg => {
                    if stock || !custom_ec {
                        if let Some(m) = cam_m {
                            params.base_matrix = m;
                            params.color_matrix = id;
                        } else if params.color_matrix == id {
                            params.color_matrix = [1241, -205, -12, 62, 1116, -154, 30, -251, 1245];
                        }
                        params.bypass[6] = true;
                        params.bypass[7] = false;
                        params.legal_range = false;
                        params.fc = bionz_core::STILL_STANDARD_FC;
                    } else if let Some(t) = table {
                        // Same physical still-JPEG domain as side B: camera-calibrated RGB, the
                        // preset's relative look matrix and its relative tone on the calibrated FC.
                        bionz_preset::apply_still_jpeg_look(&mut params, t, cam_m);
                    } else {
                        fill_camera_base_matrix(&mut params, cam_m);
                        params.bypass[6] = false;
                        params.bypass[7] = true;
                        params.legal_range = true;
                    }
                }
                GammaPath::MovieEc => {
                    fill_camera_base_matrix(&mut params, cam_m);
                    params.bypass[7] = true;
                    params.legal_range = self.legal;
                }
                GammaPath::StillFc => {
                    params.bypass[6] = true;
                    params.legal_range = self.legal;
                }
                GammaPath::EcThenFc => {
                    params.fc_before_ec = false;
                    params.legal_range = self.legal;
                }
                GammaPath::FcThenEc => {
                    params.fc_before_ec = true;
                    params.legal_range = self.legal;
                }
            }
        }
        // User stage bypasses always win over the mode/GammaPath defaults.
        apply_user_bypass(&mut params, &self.bypass);
        // Advanced routing applies to the look side only; the stock comparison stays pristine.
        if !stock {
            if let Some(t) = table {
                bionz_preset::apply_routing(&mut params, t, &self.routing, cam_m);
            }
        }
        params
    }

    /// Whether the selected look changes anything beyond the preset's stock tables.
    fn has_custom_ec(&self) -> bool {
        let Some(e) = self.entry() else { return false };
        let movie = self.mode == LabMode::Movie;
        let (Some(look), Some(stock)) = (e.params_mode(&self.target, false, movie), e.params_mode(&self.target, true, movie)) else { return false };
        look.ec != stock.ec || look.fc != stock.fc || look.color_matrix != stock.color_matrix || look.delta != stock.delta
    }

    fn look_params(&self, stock: bool) -> IntegerParams {
        let movie = self.mode == LabMode::Movie;
        let base = self.entry().and_then(|e| e.params_mode(&self.target, stock, movie)).unwrap_or_default();
        let custom = !stock && self.has_custom_ec();
        let table = if stock {
            None
        } else if movie {
            self.entry().and_then(|e| e.movie_table(&self.target))
        } else {
            self.entry().and_then(|e| e.table(&self.target))
        };
        self.effective(base, stock, custom, table)
    }

    /// Apply the plan's Photo or Movie defaults in one click.
    fn set_mode(&mut self, mode: LabMode) {
        if self.mode == mode {
            return;
        }
        self.mode = mode;
        match mode {
            LabMode::Photo => {
                self.gamma_path = GammaPath::StillJpeg;
                self.legal = false;
                self.spatial.path = SpatialPath::Still;
                self.spatial.demosaic = DemosaicChoice::Directional;
                self.spatial.q = 13;
                self.spatial.dc_free = true;
                self.spatial.high_iso_model = true;
                self.status = "Photo mode: Directional demosaic, Still 11x7 DCV (q=13), ISO NR model, Still-JPEG chain (FC, full range), Photo preset variant.".into();
            }
            LabMode::Movie => {
                if matches!(self.spatial.path, SpatialPath::HdBayer | SpatialPath::Uhd4k | SpatialPath::HdPlane) {
                    self.last_movie_spatial = self.spatial.path;
                }
                self.gamma_path = GammaPath::MovieEc;
                self.legal = true;
                self.spatial.path = self.last_movie_spatial;
                self.spatial.demosaic = DemosaicChoice::Directional;
                self.spatial.q = 14;
                self.spatial.dc_free = true;
                self.spatial.high_iso_model = false;
                self.status = self
                    .entry()
                    .map(|e| {
                        if e.has_movie_table(&self.target) {
                            "Movie mode: Movie DCV, Movie EC (record 31), legal range, Movie preset variant.".to_string()
                        } else {
                            "Movie mode: Movie DCV, Movie EC (record 31), legal range. This preset has no Movie variant; using its Photo table as fallback.".to_string()
                        }
                    })
                    .unwrap_or_else(|| "Movie mode: Movie DCV, Movie EC (record 31), legal range.".into());
            }
        }
        self.invalidate_all();
        self.last_edit = Instant::now() - SETTLE;
    }

    fn set_source(&mut self, source: Source) {
        let is_raw = source.kind == SourceKind::CameraRaw;
        if source.kind == SourceKind::DisplayImage {
            self.ev = DISPLAY_IMAGE_EV;
            self.status = format!(
                "Loaded {} ({}x{}). This is a display image, not sensor data: EV is set to {DISPLAY_IMAGE_EV} so diffuse white sits below sensor clip.",
                source.path.display(), source.full.width, source.full.height
            );
        } else {
            self.ev = 0.0;
            self.gamma_path = GammaPath::StillJpeg;
            self.legal = false;
            if self.spatial.path == SpatialPath::Off {
                self.spatial.path = SpatialPath::Still;
                self.spatial.q = 13;
            }
            self.status = format!("Loaded {} ({}x{}). {}.", source.path.display(), source.full.width, source.full.height, source.note);
        }
        // Remember a same-stem camera JPEG but do not force it into the comparison: the toggle in the
        // toolbar decides whether side B shows the real camera JPEG or the emulated stock.
        self.paired_camera_path = if is_raw { paired_jpeg(&source.path) } else { None };
        if let Some(camera) = &self.camera {
            if !same_stem(&camera.path, &source.path) {
                self.camera = None;
            }
        }
        self.use_paired_jpg = self.camera.is_some();
        if let Some(paired) = &self.paired_camera_path {
            self.status.push_str(&format!(" Paired camera JPEG: {} (toggle in the toolbar).", file_name(paired)));
        }
        self.look.frame = None;
        self.stock.frame = None;
        self.fit = true;
        self.source = Some(source);
        self.invalidate_all();
    }

    /// Something that affects both A and B changed (source, exposure, gamma path, range, bypass).
    fn invalidate_all(&mut self) {
        self.look.generation += 1;
        self.stock.generation += 1;
        self.dirty_tap = true;
        self.thumbs.iter_mut().for_each(|t| *t = None);
    }

    /// Only the selected look's tables changed.
    fn invalidate_look(&mut self) {
        self.look.generation += 1;
        self.dirty_tap = true;
        if let Some(t) = self.thumbs.get_mut(self.selected) {
            *t = None;
        }
    }

    fn mark_edit(&mut self) {
        self.last_edit = Instant::now();
    }

    fn interacting(&self) -> bool {
        self.control_active || self.last_edit.elapsed() < SETTLE || self.fit_applied < self.fit_generation
    }

    /// Ask the render worker for whatever the screen needs now. Nothing is rendered here.
    fn schedule(&mut self, ctx: &egui::Context) {
        let Some(source) = &self.source else { return };
        let interacting = self.interacting();
        let desired = if interacting { Tier::Proxy } else { self.needed_tier };
        let mut jobs = Vec::new();
        for slot in [Slot::Look, Slot::Stock] {
            if slot == Slot::Stock && self.camera_active() {
                continue;
            }
            let state = if slot == Slot::Look { &self.look } else { &self.stock };
            let current = state.frame.as_ref().is_some_and(|f| f.generation == state.generation && f.tier >= desired);
            let pending = state.sent.is_some_and(|(g, t)| g == state.generation && t >= desired);
            if current || pending {
                continue;
            }
            jobs.push((slot, state.generation));
        }
        for (slot, generation) in jobs {
            let params = self.look_params(slot == Slot::Stock);
            let image = if slot == Slot::Stock { source.image_b(desired) } else { source.image(desired) };
            let job = RenderJob { slot, generation, tier: desired, image, params, ev: self.ev };
            if self.render_tx.send(job).is_ok() {
                let state = if slot == Slot::Look { &mut self.look } else { &mut self.stock };
                state.sent = Some((generation, desired));
            }
        }
        if interacting {
            ctx.request_repaint_after(SETTLE);
        }
    }

    fn receive(&mut self, ctx: &egui::Context) {
        while let Ok(done) = self.render_rx.try_recv() {
            let state = if done.slot == Slot::Look { &mut self.look } else { &mut self.stock };
            // Keep the newest generation; within one generation keep the larger tier.
            let newer = state.frame.as_ref().is_none_or(|f| (done.generation, done.tier) >= (f.generation, f.tier));
            if done.generation > state.generation || !newer {
                continue;
            }
            let image = Arc::new(egui::ColorImage::from_rgba_unmultiplied([done.width, done.height], &done.rgba));
            let options = if done.tier == Tier::Full { TextureOptions { magnification: egui::TextureFilter::Nearest, ..TextureOptions::LINEAR } } else { TextureOptions::LINEAR };
            let texture = ctx.load_texture(if done.slot == Slot::Look { "look" } else { "stock" }, image.clone(), options);
            if done.slot == Slot::Look {
                self.render_ms = done.ms;
                if !done.gpu {
                    self.gpu_info = "CPU fallback (GPU dispatch failed)".into();
                }
                let s = scopes::scope_images(&image.pixels, done.width, done.height);
                self.scopes = Some(s.map(|img| ctx.load_texture("scope", img, TextureOptions::LINEAR)));
            }
            state.frame = Some(Frame { generation: done.generation, tier: done.tier, image, texture });
            self.analysis_key += 1;
        }
        while let Ok(fitted) = self.fit_rx.try_recv() {
            if fitted.generation <= self.fit_applied {
                continue;
            }
            self.fit_applied = fitted.generation;
            match fitted.result {
                Ok((look, summary)) => {
                    if let Some(entry) = self.library.get_mut(fitted.index) {
                        entry.look.compiled = look.compiled;
                        entry.look.compiled_movie = look.compiled_movie;
                        entry.look.report_json = look.report_json;
                        entry.look.recipe_toml = look.recipe_toml;
                        entry.report = summary.clone();
                        entry.unsaved = true;
                    }
                    if fitted.index == self.selected {
                        self.invalidate_look();
                    } else if let Some(t) = self.thumbs.get_mut(fitted.index) {
                        *t = None;
                    }
                    if self.fit_applied == self.fit_generation {
                        self.status = format!("Look refitted ({summary}). Save as… keeps it.");
                    }
                }
                Err(err) => self.status = format!("Fit failed: {err}"),
            }
        }
    }

    /// The B image as a ColorImage for analysis, or None while it is not ready.
    fn reference_image(&self) -> Option<Arc<egui::ColorImage>> {
        if self.camera_active() {
            return self.camera.as_ref().map(|cam| cam.image.clone());
        }
        self.stock.frame.as_ref().map(|f| f.image.clone())
    }

    /// Metric and difference maps run on short-lived threads, only when the controls are idle.
    fn analyse(&mut self) {
        if self.interacting() {
            return;
        }
        let (Some(a), Some(b)) = (self.look.frame.as_ref().map(|f| f.image.clone()), self.reference_image()) else { return };
        let key = self.analysis_key;
        if self.metric_key != key {
            self.metric_key = key;
            let tx = self.tx.clone();
            let (a, b) = (a.clone(), b.clone());
            let is_cam = self.camera_active();
            std::thread::spawn(move || {
                let (ra, rb) = matched_rgb(&a, &b, PROXY_SIDE * 2);
                let side = a.size[0].max(a.size[1]);
                let f = side.div_ceil(PROXY_SIDE * 2).max(1);
                let (w, h) = (a.size[0] / f, a.size[1] / f);
                let spatial = if is_cam {
                    Some((bionz_compare::measure_spatial(&rb, w, h), bionz_compare::measure_spatial(&ra, w, h)))
                } else {
                    None
                };
                let _ = tx.send(Job::Metric(key, bionz_compare::compare_rgb(&rb, &ra), spatial));
            });
        }
        if matches!(self.view, View::Difference | View::DeltaE)
            && self.derived.as_ref().map(|d| (d.0, d.1)) != Some((key, self.view))
            && self.derived_pending != Some((key, self.view))
        {
            self.derived_pending = Some((key, self.view));
            let view = self.view;
            let tx = self.tx.clone();
            std::thread::spawn(move || {
                let (ra, rb) = matched_rgb(&a, &b, PREVIEW_SIDE);
                let side = a.size[0].max(a.size[1]);
                let f = side.div_ceil(PREVIEW_SIDE).max(1);
                let (w, h) = (a.size[0] / f, a.size[1] / f);
                let rgb = if view == View::Difference {
                    bionz_compare::abs_difference(&rb, &ra).iter().map(|v| v.saturating_mul(4)).collect()
                } else {
                    bionz_compare::delta_e_heatmap(&rb, &ra)
                };
                let _ = tx.send(Job::Derived(key, view, egui::ColorImage::from_rgb([w, h], &rgb)));
            });
        }
    }

    fn refresh_small(&mut self, ctx: &egui::Context) {
        if self.interacting() {
            return;
        }
        if self.dirty_tap {
            self.tap_texture = self.tap_image().map(|img| ctx.load_texture("tap", img, TextureOptions::LINEAR));
            self.dirty_tap = false;
        }
        let Some(source) = &self.source else { return };
        let small = &source.thumb;
        let mut made = Vec::new();
        for i in 0..self.library.len() {
            if self.thumbs.get(i).is_some_and(|t| t.is_none()) {
                let movie = self.mode == LabMode::Movie;
                if let Some(params) = self.library[i].params_mode(&self.target, false, movie) {
                    let custom_ec = self.library[i].params_mode(&self.target, true, movie).is_some_and(|s| {
                        s.ec != params.ec || s.fc != params.fc || s.color_matrix != params.color_matrix || s.delta != params.delta
                    });
                    let table = if movie { self.library[i].movie_table(&self.target) } else { self.library[i].table(&self.target) };
                    let params = self.effective(params, false, custom_ec, table);
                    let pixels = bionz_raw::quantize(small, self.ev, params.gamma_max);
                    let rgb: Vec<u8> = integer_pipeline(&pixels, small.width as i32, &params)
                        .iter()
                        .flat_map(|t| encoded_to_rgb8(t.encoded_y, t.encoded_cb, t.encoded_cr, params.legal_range))
                        .collect();
                    made.push((i, ctx.load_texture(format!("thumb{i}"), egui::ColorImage::from_rgb([small.width, small.height], &rgb), TextureOptions::LINEAR)));
                }
            }
        }
        for (i, t) in made {
            self.thumbs[i] = Some(t);
        }
    }

    /// The selected stage's output on a small copy of the image, made viewable.
    fn tap_image(&self) -> Option<egui::ColorImage> {
        let small = &self.source.as_ref()?.tap;
        let stage = self.tap as u8 + 1;
        let mut params = self.look_params(false);
        let pixels = bionz_raw::quantize(small, self.ev, params.gamma_max);
        let w = small.width as i32;
        let max = params.gamma_max as f32;
        let rgb: Vec<u8> = match stage {
            1..=6 => {
                let shown = if stage == 6 { stage_display(&pixels, w, &params, 6) } else { pixels.clone() };
                shown.iter().flat_map(|p| [p.r, p.g, p.b].map(|v| ((v as f32 / max).clamp(0.0, 1.0).powf(1.0 / 2.2) * 255.0) as u8)).collect()
            }
            11 | 12 => {
                if stage == 11 {
                    params.bypass[11] = true;
                }
                integer_pipeline(&pixels, w, &params)
                    .iter()
                    .flat_map(|t| {
                        let (y, cb, cr) = (t.ycc.y as f32 / 256.0, t.ycc.cb as f32 / 256.0, t.ycc.cr as f32 / 256.0);
                        let r = y + 1.5748 * cr;
                        let b = y + 1.8556 * cb;
                        let g = (y - 0.2126 * r - 0.0722 * b) / 0.7152;
                        [r, g, b].map(|v| (v.clamp(0.0, 1.0) * 255.0) as u8)
                    })
                    .collect()
            }
            14..=16 => integer_pipeline(&pixels, w, &params)
                .iter()
                .flat_map(|t| encoded_to_rgb8(t.encoded_y, t.encoded_cb, t.encoded_cr, params.legal_range))
                .collect(),
            _ => stage_display(&pixels, w, &params, stage)
                .iter()
                .flat_map(|p: &RgbI| [p.r, p.g, p.b].map(|v| (v as f32 / max * 255.0).clamp(0.0, 255.0) as u8))
                .collect(),
        };
        Some(egui::ColorImage::from_rgb([small.width, small.height], &rgb))
    }

    fn spawn_source(&mut self, path: PathBuf) {
        self.busy.push(format!("Decoding {}", file_name(&path)));
        let tx = self.tx.clone();
        let (a, b) = self.side_inputs();
        let settings = self.spatial;
        std::thread::spawn(move || {
            let result = load_source(path.clone(), &settings, &a, &b).map(Box::new);
            let _ = tx.send(Job::Source(path, result));
        });
    }

    fn spawn_camera(&mut self, path: PathBuf) {
        let Some(source) = &self.source else {
            self.status = "Open the RAW first, then the camera JPEG of the same shot.".into();
            return;
        };
        let (dw, dh) = (source.full.width, source.full.height);
        self.busy.push(format!("Loading {}", file_name(&path)));
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let result = bionz_raw::load_display(&path).map_err(|e| e.to_string()).and_then(|(w, h, rgb)| {
                let (w, h) = (w as usize, h as usize);
                let (a, b) = (w as f32 / h as f32, dw as f32 / dh as f32);
                if (a - b).abs() > 0.02 {
                    return Err(format!("aspect {w}x{h} does not match the RAW {dw}x{dh}; use the JPEG shot together with this RAW"));
                }
                Ok(egui::ColorImage::from_rgb([dw, dh], &bionz_raw::resize_rgb8(&rgb, w, h, dw, dh)))
            });
            let _ = tx.send(Job::Camera(path, result));
        });
    }

    /// Queue a refit of the selected recipe. The fit worker keeps only the newest request.
    fn request_fit(&mut self) {
        let Some(entry) = self.library.get(self.selected) else { return };
        let Some(recipe) = entry.recipe.clone() else { return };
        self.fit_generation += 1;
        let job = FitJob { index: self.selected, generation: self.fit_generation, recipe, base: entry.look.clone() };
        let _ = self.fit_tx.send(job);
    }

    fn poll_jobs(&mut self, ctx: &egui::Context) {
        while let Ok(job) = self.rx.try_recv() {
            match job {
                Job::Source(path, result) => {
                    self.busy.retain(|b| !b.starts_with("Decoding"));
                    match result {
                        Ok(source) => self.set_source(*source),
                        Err(err) => self.status = format!("Could not open {}: {err}", path.display()),
                    }
                }
                Job::Front(key, result) => {
                    if self.front_pending == Some(key) {
                        self.front_pending = None;
                        self.busy.retain(|b| !b.starts_with("Developing"));
                    }
                    match result {
                        Ok(fresh) => {
                            let (a, b) = self.side_inputs();
                            let current = front_state::front_key(&self.spatial, &a, &b);
                            if let Some(source) = &mut self.source {
                                if key == current && fresh.path == source.path {
                                    let note = fresh.note.clone();
                                    *source = *fresh;
                                    self.status = format!("Developed: {note}.");
                                    self.invalidate_all();
                                }
                            }
                        }
                        Err(err) => self.status = format!("RAW development failed: {err}"),
                    }
                }
                Job::Camera(path, result) => {
                    self.busy.retain(|b| !b.starts_with("Loading"));
                    match result {
                        Ok(image) => {
                            self.status = format!("Camera reference {} loaded. Compare views now show Emulated vs Camera.", path.display());
                            let image = Arc::new(image);
                            let texture = ctx.load_texture("camera", image.clone(), TextureOptions::LINEAR);
                            self.camera = Some(Camera { path, image, texture });
                            self.use_paired_jpg = true;
                            self.analysis_key += 1;
                        }
                        Err(err) => self.status = format!("Could not use {}: {err}", path.display()),
                    }
                }
                Job::Exported(result) => {
                    self.busy.retain(|b| !b.starts_with("Exporting"));
                    self.status = match result {
                        Ok(msg) => msg,
                        Err(err) => format!("Export failed: {err}"),
                    };
                }
                Job::Metric(key, value, spatial) => {
                    if key == self.analysis_key {
                        self.metric = Some(value);
                        self.spatial_metric = spatial;
                    }
                }
                Job::Derived(key, view, image) => {
                    if key == self.analysis_key {
                        let tex = ctx.load_texture("derived", image, TextureOptions::LINEAR);
                        self.derived = Some((key, view, tex));
                    }
                    self.derived_pending = None;
                }
            }
        }
    }

    fn handle_drop(&mut self, path: PathBuf) {
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or_default().to_ascii_lowercase();
        if matches!(ext.as_str(), "bzlook" | "cube" | "a6lt" | "mod" | "h") {
            self.import(&path);
        } else if matches!(ext.as_str(), "jpg" | "jpeg") && self.source.as_ref().is_some_and(|s| bionz_raw::is_camera_raw(&s.path) && same_stem(&s.path, &path)) {
            self.spawn_camera(path);
        } else {
            self.spawn_source(path);
        }
    }

    fn import(&mut self, path: &Path) {
        let base = self.entry().map(|e| e.look.clone()).or_else(|| self.library.first().map(|e| e.look.clone()));
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or_default().to_ascii_lowercase();
        let arrivederci = ext == "mod" || ext == "h";
        let result = if arrivederci { spatial_src::import_arrivederci(path) } else { looks::import_file(path, base.as_ref()) };
        if arrivederci && result.is_ok() && self.spatial.path == SpatialPath::Off {
            // Its spatial layer was tested in 4K S-Log3 movie (E0610), so show that path.
            self.spatial.path = SpatialPath::Uhd4k;
        }
        match result {
            Ok(entry) => {
                self.status = format!("Imported {} ({}). {}", entry.name, entry.targets().join(", "), entry.report);
                self.library.push(entry);
                self.thumbs.push(None);
                self.select(self.library.len() - 1);
            }
            Err(err) => self.status = format!("Import failed: {err}"),
        }
    }

    fn select(&mut self, index: usize) {
        if index < self.library.len() {
            self.selected = index;
            self.undo.clear();
            self.redo.clear();
            self.invalidate_all();
            let entry = &self.library[index];
            if entry.table(&self.target).is_none() {
                self.status = format!("{} has no {} table (it has: {}).", entry.name, self.target, entry.targets().join(", "));
            }
        }
    }

    fn fit_zoom(&self) -> f32 {
        let Some(source) = &self.source else { return 1.0 };
        let avail = self.view_area();
        (avail.x / source.full.width as f32).min(avail.y / source.full.height as f32)
    }

    /// Size of one image panel: half the view in side-by-side mode.
    fn view_area(&self) -> egui::Vec2 {
        if self.view == View::SideBySide { egui::vec2((self.view_size.x - 8.0) / 2.0, self.view_size.y) } else { self.view_size }
    }

    fn zoom_about(&mut self, new_zoom: f32, anchor: egui::Vec2) {
        let fit = self.fit_zoom();
        let new_zoom = new_zoom.clamp(fit * 0.25, 40.0);
        if self.fit {
            self.apply_fit();
        }
        self.offset = anchor - (anchor - self.offset) * (new_zoom / self.zoom);
        self.zoom = new_zoom;
        self.fit = false;
    }

    fn apply_fit(&mut self) {
        let Some(source) = &self.source else { return };
        self.zoom = self.fit_zoom();
        let size = egui::vec2(source.full.width as f32, source.full.height as f32) * self.zoom;
        self.offset = (self.view_area() - size) / 2.0;
    }

    fn run(&mut self, action: Action, ctx: &egui::Context) {
        match action {
            Action::OpenSource => {
                if let Some(path) = rfd::FileDialog::new()
                    .set_title("Open a photo or camera RAW")
                    .add_filter("Camera RAW and images", &["arw", "dng", "nef", "cr2", "cr3", "raf", "orf", "rw2", "jpg", "jpeg", "png", "tif", "tiff"])
                    .pick_file()
                {
                    self.spawn_source(path);
                }
            }
            Action::OpenCamera => {
                let mut dialog = rfd::FileDialog::new().set_title("Open the camera JPEG of the same shot").add_filter("JPEG", &["jpg", "jpeg"]);
                if let Some(dir) = self.source.as_ref().and_then(|s| s.path.parent()) {
                    dialog = dialog.set_directory(dir);
                }
                if let Some(path) = dialog.pick_file() {
                    self.spawn_camera(path);
                }
            }
            Action::ClearCamera => {
                self.camera = None;
                self.use_paired_jpg = false;
                self.stock.generation += 1;
                self.analysis_key += 1;
                self.status = "Camera reference removed. Compare views show Emulated vs Stock.".into();
            }
            Action::TogglePairedJpg => {
                if self.use_paired_jpg {
                    self.use_paired_jpg = false;
                    self.analysis_key += 1;
                    self.metric_key = u64::MAX;
                    self.invalidate_all();
                    self.status = "Compare side B: simulated stock (.jpg).".into();
                } else if let Some(path) = self.paired_camera_path.clone() {
                    if self.camera.as_ref().is_some_and(|c| c.path == path) {
                        self.use_paired_jpg = true;
                        self.analysis_key += 1;
                        self.metric_key = u64::MAX;
                        self.invalidate_all();
                        self.status = format!("Compare side B: paired camera JPEG {}.", file_name(&path));
                    } else {
                        self.spawn_camera(path);
                    }
                }
            }
            Action::UseChart => self.set_source(Source::build(PathBuf::from("built-in test chart"), scopes::test_chart())),
            Action::Import => {
                if let Some(paths) = rfd::FileDialog::new().set_title("Import presets").add_filter("Presets and mods", &["bzlook", "cube", "a6lt", "mod", "h"]).pick_files() {
                    for path in paths {
                        self.import(&path);
                    }
                }
            }
            Action::SaveAs => {
                let Some(entry) = self.library.get(self.selected) else { return };
                let mut dialog = rfd::FileDialog::new()
                    .set_title("Save preset")
                    .add_filter("BIONZ look", &["bzlook"])
                    .set_file_name(format!("{}.bzlook", looks::slug(&entry.name)));
                if let Some(dir) = entry.path.as_ref().and_then(|p| p.parent()).or(self.preset_dir.as_deref()) {
                    dialog = dialog.set_directory(dir);
                }
                if let Some(path) = dialog.save_file() {
                    let entry = &mut self.library[self.selected];
                    self.status = match looks::save(entry, &path) {
                        Ok(()) => format!("Saved {}.", path.display()),
                        Err(err) => format!("Save failed: {err}"),
                    };
                }
            }
            Action::Pack => {
                let Some(entry) = self.library.get(self.selected) else { return };
                if let Some(dir) = rfd::FileDialog::new().set_title("Folder for Film Sims headers").pick_folder() {
                    self.status = match looks::pack(&dir, entry, &self.library) {
                        Ok(files) => format!(
                            "Wrote {} for target(s) {}. Build the mod with mods/film_sims as usual. Nothing was sent to a camera.",
                            files.iter().map(|f| file_name(f)).collect::<Vec<_>>().join(", "),
                            entry.targets().join(", ")
                        ),
                        Err(err) => format!("Pack failed: {err}"),
                    };
                }
            }
            Action::OpenModExport => {
                if self.entry().is_none() {
                    return;
                }
                self.mod_export.open = true;
                self.mod_export.status.clear();
            }
            Action::PickModDir => {
                if let Some(dir) = rfd::FileDialog::new()
                    .set_title("Destination folder or SD card root for the .mod files")
                    .pick_folder()
                {
                    self.mod_export.dir = Some(dir);
                    self.mod_export.status.clear();
                }
            }
            Action::WriteMod => {
                let Some(entry) = self.library.get(self.selected) else { return };
                let Some(dir) = self.mod_export.dir.clone() else {
                    self.mod_export.status = "Choose a destination folder or SD card first.".into();
                    return;
                };
                let targets = self.mod_export.targets;
                let kind = self.mod_export.kind;
                let result = match kind {
                    ModKind::SingleLook => looks::export_mods_report(&dir, entry, targets),
                    ModKind::LivePicker => looks::export_live_mods_report(&dir, &self.library, targets),
                };
                self.mod_export.status = match result {
                    Ok((summary, detail)) => {
                        self.status = format!(
                            "{} ({})",
                            summary,
                            detail.lines().map(|line| file_name(Path::new(line))).collect::<Vec<_>>().join(", ")
                        );
                        format!("{summary}\n{detail}")
                    }
                    Err(err) => {
                        self.status = format!("Export failed: {err}");
                        format!("Export failed: {err}")
                    }
                };
            }
            Action::Export => {
                let Some(source) = &self.source else { return };
                let default = format!(
                    "{}-{}.png",
                    source.path.file_stem().and_then(|s| s.to_str()).unwrap_or("image"),
                    looks::slug(&self.entry().map(|e| e.name.clone()).unwrap_or_default())
                );
                if let Some(out) = rfd::FileDialog::new().set_title("Export emulated image").add_filter("PNG", &["png"]).set_file_name(default).save_file() {
                    let image = source.full.clone();
                    let params = self.look_params(false);
                    let ev = self.ev;
                    let tx = self.tx.clone();
                    self.busy.push("Exporting".into());
                    std::thread::spawn(move || {
                        let (rgba, _) = worker::develop_rgba(&image, &params, ev);
                        let rgb: Vec<u8> = rgba.chunks_exact(4).flat_map(|p| [p[0], p[1], p[2]]).collect();
                        let result = image::save_buffer(&out, &rgb, image.width as u32, image.height as u32, image::ColorType::Rgb8)
                            .map(|_| format!("Exported {} ({}x{}).", out.display(), image.width, image.height))
                            .map_err(|e| e.to_string());
                        let _ = tx.send(Job::Exported(result));
                    });
                }
            }
            Action::Select(i) => self.select(i),
            Action::Undo | Action::Redo => {
                let forward = matches!(action, Action::Redo);
                let Some(entry) = self.library.get_mut(self.selected) else { return };
                let Some(current) = entry.recipe.clone() else { return };
                let next = if forward { self.redo.pop() } else { self.undo.pop() };
                if let Some(next) = next {
                    if forward { self.undo.push(current) } else { self.redo.push(current) }
                    entry.recipe = Some(next);
                    self.request_fit();
                }
            }
            Action::RepairSpatial => {
                let Some(entry) = self.library.get(self.selected) else { return };
                match spatial_src::repair_entry(entry, &self.stock_spatial) {
                    Ok((fixed, report)) => {
                        self.status = format!("Created {}: {report}. Save it to keep it.", fixed.name);
                        self.library.push(fixed);
                        self.thumbs.push(None);
                        self.select(self.library.len() - 1);
                    }
                    Err(err) => self.status = format!("Repair failed: {err}"),
                }
            }
            Action::ResetRecipe => {
                let Some(entry) = self.library.get_mut(self.selected) else { return };
                let Some(path) = entry.path.clone() else { return };
                if let Ok(look) = bionz_preset::load_bzlook(&path) {
                    *entry = Entry::from_look(look, Some(path));
                    self.undo.clear();
                    self.redo.clear();
                    self.invalidate_look();
                    self.status = "Reverted to the saved preset.".into();
                }
            }
            Action::ZoomFit => self.fit = true,
            Action::ZoomTo(physical) => {
                let ppp = ctx.pixels_per_point();
                let center = self.view_area() / 2.0;
                self.zoom_about(physical / ppp, center);
            }
            Action::ZoomBy(factor) => {
                if self.fit {
                    self.apply_fit();
                }
                let center = self.view_area() / 2.0;
                self.zoom_about(self.zoom * factor, center);
            }
        }
    }

    /// The image viewer: wheel or pinch zooms about the pointer, drag pans, double-click fits.
    fn viewer(&mut self, ui: &mut egui::Ui, b_name: &str) {
        let Some(source) = &self.source else { return };
        let full = egui::vec2(source.full.width as f32, source.full.height as f32);
        let (preview_w, full_w) = (source.preview.width, source.full.width);
        let (view_rect, resp) = ui.allocate_exact_size(ui.available_size(), egui::Sense::click_and_drag());
        self.view_size = view_rect.size();
        if self.fit {
            self.apply_fit();
        }
        let two = self.view == View::SideBySide;
        let panel = self.view_area();
        let panels: Vec<egui::Rect> = if two {
            vec![
                egui::Rect::from_min_size(view_rect.min, panel),
                egui::Rect::from_min_size(view_rect.min + egui::vec2(panel.x + 8.0, 0.0), panel),
            ]
        } else {
            vec![view_rect]
        };
        let ppp = ui.ctx().pixels_per_point();

        // Input.
        if resp.hovered() {
            // Raw wheel delta, consumed here: the smoothed delta would keep arriving for a few frames
            // and scroll the side panel if the pointer moved there.
            let (scroll, pinch, pointer) = ui.input_mut(|i| {
                let s = i.raw_scroll_delta.y;
                i.raw_scroll_delta = egui::Vec2::ZERO;
                i.smooth_scroll_delta = egui::Vec2::ZERO;
                (s, i.zoom_delta(), i.pointer.hover_pos())
            });
            let factor = pinch * (scroll * 0.0018).exp();
            if (factor - 1.0).abs() > 1e-4 {
                if let Some(p) = pointer {
                    let local = panels.iter().find(|r| r.contains(p)).map(|r| p - r.min).unwrap_or(p - view_rect.min);
                    self.zoom_about(self.zoom * factor, local);
                }
            }
        }
        let image_rect_in = |panel_rect: egui::Rect, zoom: f32, offset: egui::Vec2| egui::Rect::from_min_size(panel_rect.min + offset, full * zoom);
        if resp.drag_started() {
            let rect = image_rect_in(panels[0], self.zoom, self.offset);
            let cut = rect.left() + rect.width() * self.wipe;
            self.dragging_divider = self.view == View::Split && resp.interact_pointer_pos().is_some_and(|p| (p.x - cut).abs() < 12.0);
        }
        if resp.dragged() {
            if self.dragging_divider {
                if let Some(p) = resp.interact_pointer_pos() {
                    let rect = image_rect_in(panels[0], self.zoom, self.offset);
                    self.wipe = ((p.x - rect.left()) / rect.width()).clamp(0.0, 1.0);
                }
            } else {
                if self.fit {
                    self.apply_fit();
                }
                self.offset += resp.drag_delta();
                self.fit = false;
            }
        }
        if resp.drag_stopped() {
            self.dragging_divider = false;
        }
        if resp.double_clicked() {
            self.fit = true;
            self.apply_fit();
        }
        // Which render size the current zoom needs.
        let shown_px = full.x * self.zoom * ppp;
        self.needed_tier = if shown_px > preview_w as f32 * 1.1 && full_w > preview_w { Tier::Full } else { Tier::Preview };

        // Paint.
        let painter = ui.painter_at(view_rect);
        painter.rect_filled(view_rect, 0.0, Color32::from_rgb(12, 12, 14));
        let uv = egui::Rect::from_min_max(egui::pos2(0.0, 0.0), egui::pos2(1.0, 1.0));
        let a = self.look.frame.as_ref().map(|f| f.texture.id());
        let b = if self.camera_active() {
            self.camera.as_ref().map(|cam| cam.texture.id())
        } else {
            self.stock.frame.as_ref().map(|f| f.texture.id())
        };
        let label = |p: &egui::Painter, pos: egui::Pos2, text: &str, align: egui::Align2| {
            let galley = p.layout_no_wrap(text.to_string(), egui::FontId::proportional(13.0), Color32::WHITE);
            let r = align.anchor_size(pos, galley.size() + egui::vec2(12.0, 6.0));
            p.rect_filled(r, 4.0, Color32::from_black_alpha(160));
            p.galley(r.min + egui::vec2(6.0, 3.0), galley, Color32::WHITE);
        };
        let rect = image_rect_in(panels[0], self.zoom, self.offset);
        match self.view {
            View::Emulated => {
                if let Some(t) = a { painter.image(t, rect, uv, Color32::WHITE); }
            }
            View::Reference => {
                if let Some(t) = b { painter.image(t, rect, uv, Color32::WHITE); }
                label(&painter, view_rect.left_top() + egui::vec2(8.0, 8.0), b_name, egui::Align2::LEFT_TOP);
            }
            View::Difference | View::DeltaE => match &self.derived {
                Some((_, v, t)) if *v == self.view => { painter.image(t.id(), rect, uv, Color32::WHITE); }
                _ => {
                    if let Some(t) = a { painter.image(t, rect, uv, Color32::from_gray(90)); }
                    label(&painter, view_rect.center(), "Computing… (waits until the controls are idle)", egui::Align2::CENTER_CENTER);
                }
            },
            View::SideBySide => {
                let right = image_rect_in(panels[1], self.zoom, self.offset);
                let pa = ui.painter_at(panels[0]);
                let pb = ui.painter_at(panels[1]);
                if let Some(t) = a { pa.image(t, rect, uv, Color32::WHITE); }
                if let Some(t) = b { pb.image(t, right, uv, Color32::WHITE); }
                label(&pa, panels[0].left_top() + egui::vec2(8.0, 8.0), "Fine-tuned", egui::Align2::LEFT_TOP);
                label(&pb, panels[1].left_top() + egui::vec2(8.0, 8.0), b_name, egui::Align2::LEFT_TOP);
            }
            View::Split => {
                let cut = rect.left() + rect.width() * self.wipe;
                if let Some(t) = b { painter.image(t, rect, uv, Color32::WHITE); }
                if let Some(t) = a {
                    let left = egui::Rect::from_min_max(rect.min, egui::pos2(cut, rect.bottom()));
                    painter.image(t, left, egui::Rect::from_min_max(egui::pos2(0.0, 0.0), egui::pos2(self.wipe, 1.0)), Color32::WHITE);
                }
                let x = cut.clamp(view_rect.left(), view_rect.right());
                painter.line_segment([egui::pos2(x, view_rect.top()), egui::pos2(x, view_rect.bottom())], egui::Stroke::new(2.0_f32, ACCENT));
                painter.circle_filled(egui::pos2(x, view_rect.center().y), 7.0, ACCENT);
                label(&painter, view_rect.left_top() + egui::vec2(8.0, 8.0), "Fine-tuned", egui::Align2::LEFT_TOP);
                label(&painter, view_rect.right_top() + egui::vec2(-8.0, 8.0), b_name, egui::Align2::RIGHT_TOP);
                if resp.hovered() && ui.input(|i| i.pointer.hover_pos()).is_some_and(|p| (p.x - cut).abs() < 12.0) {
                    ui.ctx().set_cursor_icon(egui::CursorIcon::ResizeHorizontal);
                }
            }
        }
        if a.is_none() {
            label(&painter, view_rect.center(), "Rendering…", egui::Align2::CENTER_CENTER);
        }
        let tier = self.look.frame.as_ref().map(|f| f.tier);
        let note = match tier {
            Some(Tier::Proxy) => " · live preview (low resolution)",
            Some(Tier::Full) => " · full resolution",
            _ => "",
        };
        label(
            &painter,
            view_rect.left_bottom() + egui::vec2(8.0, -8.0),
            &format!("{:.0}%{note}", self.zoom * ppp * 100.0),
            egui::Align2::LEFT_BOTTOM,
        );
        if resp.dragged() && !self.dragging_divider {
            ui.ctx().set_cursor_icon(egui::CursorIcon::Grabbing);
        }
    }
}

/// Both images as 8-bit RGB at the same size, at most `max_side` on the long side.
fn matched_rgb(a: &egui::ColorImage, b: &egui::ColorImage, max_side: usize) -> (Vec<u8>, Vec<u8>) {
    let side = a.size[0].max(a.size[1]);
    let f = side.div_ceil(max_side).max(1);
    let (w, h) = (a.size[0] / f, a.size[1] / f);
    let to_rgb = |img: &egui::ColorImage| -> Vec<u8> {
        let rgb: Vec<u8> = img.pixels.iter().flat_map(|p| [p.r(), p.g(), p.b()]).collect();
        bionz_raw::resize_rgb8(&rgb, img.size[0], img.size[1], w, h)
    };
    (to_rgb(a), to_rgb(b))
}

fn same_stem(a: &Path, b: &Path) -> bool {
    a.file_stem().map(|s| s.to_ascii_lowercase()) == b.file_stem().map(|s| s.to_ascii_lowercase())
}

/// User stage bypasses always win over the mode/GammaPath defaults.
fn apply_user_bypass(params: &mut IntegerParams, user: &[bool; 16]) {
    for (i, b) in user.iter().enumerate() {
        params.bypass[i] |= *b;
    }
}

/// Presets may embed their own base matrix; only an identity one is filled with the RAW
/// sensor-to-sRGB matrix, so a preset fitted in linear sRGB / Rec.709 receives calibrated RGB
/// instead of raw sensor RGB.
fn fill_camera_base_matrix(params: &mut IntegerParams, cam_m: Option<[i32; 9]>) {
    const IDENTITY: [i32; 9] = [1024, 0, 0, 0, 1024, 0, 0, 0, 1024];
    if params.base_matrix == IDENTITY {
        if let Some(m) = cam_m {
            params.base_matrix = m;
        }
    }
}

/// A camera JPEG with the RAW's file name next to it (`_DSC1234.ARW` -> `_DSC1234.JPG`).
fn paired_jpeg(raw: &Path) -> Option<PathBuf> {
    ["JPG", "jpg", "JPEG", "jpeg"].iter().map(|ext| raw.with_extension(ext)).find(|p| p.is_file())
}

fn file_name(p: &Path) -> String {
    p.file_name().map(|s| s.to_string_lossy().into_owned()).unwrap_or_else(|| p.display().to_string())
}

fn apply_theme(ctx: &egui::Context, dark: bool) {
    ctx.set_visuals(if dark { egui::Visuals::dark() } else { egui::Visuals::light() });
    ctx.style_mut(|s| {
        s.spacing.item_spacing = egui::vec2(8.0, 6.0);
        s.spacing.button_padding = egui::vec2(10.0, 5.0);
        s.spacing.slider_width = 150.0;
        s.visuals.selection.bg_fill = if dark { Color32::from_rgb(120, 78, 28) } else { Color32::from_rgb(250, 210, 150) };
        s.visuals.selection.stroke.color = if dark { Color32::WHITE } else { Color32::BLACK };
        s.visuals.hyperlink_color = ACCENT;
        if dark {
            s.visuals.panel_fill = Color32::from_rgb(24, 25, 28);
            s.visuals.window_fill = Color32::from_rgb(30, 31, 35);
            s.visuals.extreme_bg_color = Color32::from_rgb(14, 15, 17);
        }
        s.text_styles.insert(egui::TextStyle::Heading, egui::FontId::proportional(17.0));
        s.text_styles.insert(egui::TextStyle::Body, egui::FontId::proportional(13.5));
        s.text_styles.insert(egui::TextStyle::Button, egui::FontId::proportional(13.5));
    });
}

fn badge(ui: &mut egui::Ui, b: Badge) {
    let (text, color, tip) = match b {
        Badge::Verified => ("V", Color32::from_rgb(90, 190, 110), "Verified in firmware or on hardware"),
        Badge::Modeled => ("M", Color32::from_rgb(100, 150, 230), "Modeled from evidence"),
        Badge::Assumed => ("A", Color32::from_rgb(235, 150, 60), "Assumption: not measured on the camera"),
        Badge::SyntheticInput => ("S", Color32::GRAY, "Synthetic input"),
        Badge::NotApplicable => ("-", Color32::GRAY, "View only, never packed"),
    };
    ui.label(RichText::new(text).strong().monospace().color(color)).on_hover_text(tip);
}

fn section(ui: &mut egui::Ui, title: &str, open: bool, add: impl FnOnce(&mut egui::Ui)) {
    egui::CollapsingHeader::new(RichText::new(title).strong()).default_open(open).show(ui, add);
}

#[derive(Default)]
struct ControlEvents {
    changed: bool,
    started: bool,
    finished: bool,
    active: bool,
}

/// Recipe sliders.
fn recipe_controls(ui: &mut egui::Ui, r: &mut Recipe) -> ControlEvents {
    let mut ev = ControlEvents::default();
    let mut slider = |ui: &mut egui::Ui, v: &mut f64, range: std::ops::RangeInclusive<f64>, label: &str, tip: &str| {
        let resp = ui.add(egui::Slider::new(v, range).text(label).fixed_decimals(3));
        let resp = if tip.is_empty() { resp } else { resp.on_hover_text(tip) };
        ev.started |= resp.drag_started() || (resp.changed() && !resp.dragged());
        ev.changed |= resp.changed();
        ev.finished |= resp.drag_stopped() || (resp.changed() && !resp.dragged());
        ev.active |= resp.dragged();
    };
    section(ui, "Tone", true, |ui| {
        slider(ui, &mut r.mid_contrast, 0.6..=1.8, "Contrast", "Midtone slope around the crossover");
        slider(ui, &mut r.shadow_toe, -0.5..=0.5, "Shadows", "Negative crushes, positive lifts");
        slider(ui, &mut r.highlight_rolloff, 0.0..=1.0, "Highlight roll-off", "Soft shoulder");
        slider(ui, &mut r.black_fade, 0.0..=0.08, "Black fade", "Lifted black floor");
        slider(ui, &mut r.white_ceiling, 0.85..=1.0, "White ceiling", "Peak white");
        slider(ui, &mut r.crossover, 0.1..=0.5, "Crossover", "Anchor of the contrast curve");
    });
    section(ui, "Color", true, |ui| {
        slider(ui, &mut r.saturation, 0.0..=1.8, "Saturation", "Global chroma");
        slider(ui, &mut r.warmth, -0.2..=0.2, "Warmth", "Positive warms, negative cools");
        slider(ui, &mut r.highlight_desat, 0.0..=0.8, "Highlight desat.", "Chroma roll-off in highlights");
        slider(ui, &mut r.red_amber, -0.5..=0.5, "Red to amber", "Hue shift of reds");
        slider(ui, &mut r.green_olive, -0.5..=0.5, "Green to olive", "Hue shift of greens");
        slider(ui, &mut r.blue_cyan, -0.5..=0.5, "Blue to cyan", "Hue shift of blues");
    });
    section(ui, "Hue sectors", false, |ui| {
        slider(ui, &mut r.sky_luma, -0.5..=0.5, "Sky luma", "Darken or lift blue/cyan");
        slider(ui, &mut r.sky_sat, -0.5..=0.5, "Sky saturation", "");
        slider(ui, &mut r.foliage_luma, -0.5..=0.5, "Foliage luma", "");
        slider(ui, &mut r.foliage_sat, -0.5..=0.5, "Foliage saturation", "");
        slider(ui, &mut r.red_luma, -0.5..=0.5, "Red luma", "");
        slider(ui, &mut r.red_highlight_hue, -0.5..=0.5, "Bright red hue", "Push bright reds toward copper");
        slider(ui, &mut r.skin_protect, 0.0..=1.0, "Skin protection", "Reduces hue twist on skin");
    });
    section(ui, "Split toning", false, |ui| {
        slider(ui, &mut r.shadow_warmth, -0.2..=0.2, "Shadow warmth", "");
        slider(ui, &mut r.shadow_tint, -0.2..=0.2, "Shadow tint", "Positive magenta, negative green");
        slider(ui, &mut r.highlight_warmth, -0.2..=0.2, "Highlight warmth", "");
        slider(ui, &mut r.highlight_tint, -0.2..=0.2, "Highlight tint", "");
    });
    if ui.checkbox(&mut r.monochrome, "Monochrome").changed() {
        ev.changed = true;
        ev.started = true;
        ev.finished = true;
    }
    ev
}

fn draw_curve(ui: &mut egui::Ui, look: &[i32], stock: &[i32], max: f32) {
    let (rect, _) = ui.allocate_exact_size(egui::vec2(ui.available_width().min(260.0), 110.0), egui::Sense::hover());
    let painter = ui.painter_at(rect);
    painter.rect_filled(rect, 4.0, Color32::from_rgb(16, 16, 18));
    for (values, color) in [(stock, Color32::from_gray(110)), (look, ACCENT)] {
        let pts: Vec<egui::Pos2> = values
            .iter()
            .enumerate()
            .map(|(i, v)| egui::pos2(rect.left() + i as f32 / 95.0 * rect.width(), rect.bottom() - *v as f32 / max * rect.height()))
            .collect();
        painter.add(egui::Shape::line(pts, egui::Stroke::new(1.5_f32, color)));
    }
}

fn draw_matrix(ui: &mut egui::Ui, m: &[i32; 9]) {
    egui::Grid::new(ui.next_auto_id()).spacing([14.0, 2.0]).show(ui, |ui| {
        for row in 0..3 {
            for col in 0..3 {
                ui.label(RichText::new(format!("{:6}", m[row * 3 + col])).monospace());
            }
            ui.end_row();
        }
    });
}

fn draw_lut_slice(ui: &mut egui::Ui, delta: &[i8], y: usize) {
    let (rect, _) = ui.allocate_exact_size(egui::vec2(135.0, 135.0), egui::Sense::hover());
    let painter = ui.painter_at(rect);
    for cb in 0..9 {
        for cr in 0..9 {
            let node = (y * 81 + cb * 9 + cr) * 3;
            let v = delta.get(node).copied().unwrap_or(0);
            let mag = (v.unsigned_abs() as f32 / 64.0 * 215.0) as u8;
            let color = if v >= 0 { Color32::from_rgb(35, 40 + mag, 45) } else { Color32::from_rgb(40 + mag, 35, 40) };
            let cell = egui::Rect::from_min_size(egui::pos2(rect.left() + cr as f32 * 15.0, rect.top() + (8 - cb) as f32 * 15.0), egui::vec2(14.0, 14.0));
            painter.rect_filled(cell, 2.0, color);
        }
    }
}

impl eframe::App for LabApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.poll_jobs(ctx);
        self.receive(ctx);
        let mut actions: Vec<Action> = Vec::new();
        for file in ctx.input(|i| i.raw.dropped_files.clone()) {
            if let Some(path) = file.path {
                self.handle_drop(path);
            }
        }
        let typing = ctx.wants_keyboard_input();
        ctx.input(|i| {
            if i.modifiers.command && i.key_pressed(egui::Key::O) {
                actions.push(if i.modifiers.shift { Action::OpenCamera } else { Action::OpenSource });
            }
            if i.modifiers.command && i.key_pressed(egui::Key::S) {
                actions.push(Action::SaveAs);
            }
            if i.modifiers.command && i.key_pressed(egui::Key::Z) {
                actions.push(if i.modifiers.shift { Action::Redo } else { Action::Undo });
            }
            if i.modifiers.command && i.key_pressed(egui::Key::Y) {
                actions.push(Action::Redo);
            }
            if i.key_pressed(egui::Key::F1) {
                self.show_help = !self.show_help;
            }
            if !typing && !i.modifiers.command {
                if i.key_pressed(egui::Key::F) || i.key_pressed(egui::Key::Num0) {
                    actions.push(Action::ZoomFit);
                }
                if i.key_pressed(egui::Key::Num1) {
                    actions.push(Action::ZoomTo(1.0));
                }
                if i.key_pressed(egui::Key::Num2) {
                    actions.push(Action::ZoomTo(2.0));
                }
                if i.key_pressed(egui::Key::Plus) || i.key_pressed(egui::Key::Equals) {
                    actions.push(Action::ZoomBy(1.25));
                }
                if i.key_pressed(egui::Key::Minus) {
                    actions.push(Action::ZoomBy(0.8));
                }
            }
        });
        // `control_active` still holds last frame's value here; the panels below set it again.
        self.maybe_develop();
        self.schedule(ctx);
        self.analyse();
        self.refresh_small(ctx);
        let control_was_active = self.control_active;
        self.control_active = false;
        if !self.busy.is_empty() {
            ctx.request_repaint_after(Duration::from_millis(50));
        }

        egui::TopBottomPanel::top("toolbar").frame(egui::Frame::side_top_panel(&ctx.style()).inner_margin(egui::Margin::symmetric(10, 8))).show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.label(RichText::new("BIONZ Look Lab").strong().size(16.0).color(ACCENT));
                ui.separator();
                if ui.button("📂 Open photo / RAW").on_hover_text("ARW, DNG or JPEG/PNG (Ctrl+O). Drag and drop also works.").clicked() {
                    actions.push(Action::OpenSource);
                }
                let has_raw = self.source.as_ref().is_some_and(|s| bionz_raw::is_camera_raw(&s.path));
                if ui
                    .add_enabled(has_raw, egui::Button::new("📷 Add camera JPEG"))
                    .on_hover_text("The in-camera JPEG of the same shot, taken with the preset active (Ctrl+Shift+O). Dropping a JPEG with the same file name also works.")
                    .on_disabled_hover_text("Open a RAW first")
                    .clicked()
                {
                    actions.push(Action::OpenCamera);
                }
                if self.camera.is_some() && ui.small_button("✕ camera").clicked() {
                    actions.push(Action::ClearCamera);
                }
                if let Some(paired) = self.paired_camera_path.clone() {
                    let label = if self.use_paired_jpg { "Simulated Stock (.jpg)" } else { "Use paired .JPG" };
                    let hover = format!(
                        "Paired camera JPEG: {}. Switch compare side B between the real camera JPEG and the emulated stock baseline.",
                        paired.display()
                    );
                    if ui.button(label).on_hover_text(hover).clicked() {
                        actions.push(Action::TogglePairedJpg);
                    }
                }
                ui.separator();
                if ui.button("➕ Import preset").on_hover_text(".bzlook, .cube (fitted to the ISP) or legacy .a6lt").clicked() {
                    actions.push(Action::Import);
                }
                if ui.add_enabled(self.entry().is_some(), egui::Button::new("💾 Save preset as…")).on_hover_text("Ctrl+S").clicked() {
                    actions.push(Action::SaveAs);
                }
                if ui.add_enabled(self.entry().is_some(), egui::Button::new("📦 Export Camera Mod (.mod)")).on_hover_text("Writes a ready-to-copy .mod for <SD>:\\mods\\ for a6400 and/or a6100. No compiler needed.").clicked() {
                    actions.push(Action::OpenModExport);
                }
                if ui.add_enabled(self.source.is_some(), egui::Button::new("🖼 Export image")).on_hover_text("Full-resolution PNG of the emulated output").clicked() {
                    actions.push(Action::Export);
                }
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if ui.button("❓ Help").on_hover_text("F1").clicked() {
                        self.show_help = !self.show_help;
                    }
                    if ui.button(if self.dark { "☀" } else { "🌙" }).on_hover_text("Theme").clicked() {
                        self.dark = !self.dark;
                        apply_theme(ctx, self.dark);
                    }
                });
            });
        });

        egui::TopBottomPanel::bottom("status").show(ctx, |ui| {
            ui.horizontal(|ui| {
                let fitting = self.fit_applied < self.fit_generation;
                if !self.busy.is_empty() || fitting {
                    ui.spinner();
                    let mut items = self.busy.clone();
                    if fitting {
                        items.push("Fitting look".into());
                    }
                    ui.label(items.join(", "));
                    ui.separator();
                }
                ui.add(egui::Label::new(&self.status).truncate()).on_hover_text(&self.status);
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    ui.label(RichText::new(format!("{} · {:.0} ms", self.gpu_info, self.render_ms)).weak());
                });
            });
        });

        let synthetic = self.entry().and_then(|e| e.table(&self.target)).is_some_and(|t| t.has_synthetic_stock());
        let b_name = if self.camera_active() {
            "Camera JPEG"
        } else if self.mode == LabMode::Movie {
            "Movie stock"
        } else if self.gamma_path == GammaPath::StillJpeg {
            "Stock (.jpg)"
        } else if synthetic {
            "Stock (synthetic)"
        } else {
            "Stock"
        };

        if self.source.is_some() {
            egui::TopBottomPanel::bottom("scopes").resizable(false).show(ctx, |ui| {
                ui.horizontal(|ui| {
                    if let Some([hist, wave, vec]) = &self.scopes {
                        ui.vertical(|ui| {
                            ui.label(RichText::new("Histogram").small().weak());
                            ui.image((hist.id(), egui::vec2(256.0, 90.0)));
                        });
                        ui.vertical(|ui| {
                            ui.label(RichText::new("Waveform").small().weak());
                            ui.image((wave.id(), egui::vec2(320.0, 90.0)));
                        });
                        ui.vertical(|ui| {
                            ui.label(RichText::new("Vectorscope").small().weak());
                            ui.image((vec.id(), egui::vec2(90.0, 90.0)));
                        });
                    }
                    ui.separator();
                    ui.vertical(|ui| {
                        let b = if self.camera_active() { "camera JPEG" } else if self.mode == LabMode::Movie { "Movie stock" } else if synthetic { "synthetic stock tables" } else { "stock camera tables" };
                        ui.label(RichText::new(format!("Emulated vs {b}")).strong());
                        match self.metric {
                            Some((mean, p95)) => {
                                ui.label(format!("ΔE mean {mean:.2}   p95 {p95:.2}"));
                            }
                            None => {
                                ui.label(RichText::new("ΔE: measured when the controls are idle").weak());
                            }
                        }
                        if let Some((cam, emu)) = &self.spatial_metric {
                            let hf_ratio = emu.hf_energy / cam.hf_energy.max(1e-9);
                            ui.label(
                                RichText::new(format!(
                                    "HF ratio {hf_ratio:.2}x · over {:.1}%/{:.1}% · halo {:.1}/{:.1} · noise {:.1}/{:.1}",
                                    emu.overshoot_pct, cam.overshoot_pct,
                                    emu.halo_width, cam.halo_width,
                                    emu.flat_noise, cam.flat_noise
                                ))
                                .small(),
                            );
                        }
                        if self.camera_active() {
                            ui.label(RichText::new("Synthetic metric until written up as an evidence receipt.").small().weak());
                        } else if self.camera.is_some() {
                            ui.label(RichText::new("Camera JPEG loaded but side B is the simulated stock; use the toolbar toggle.").small().weak());
                        } else {
                            ui.label(RichText::new("Add the camera JPEG to measure against the real camera.").small().weak());
                        }
                    });
                });
            });
        }

        egui::SidePanel::left("library").default_width(230.0).resizable(true).show(ctx, |ui| {
            ui.add_space(4.0);
            ui.heading("Presets");
            if let Some(dir) = &self.preset_dir {
                ui.label(RichText::new(dir.display().to_string()).small().weak()).on_hover_text("Set BIONZ_PRESETS to use another folder");
            }
            ui.add_space(4.0);
            egui::ScrollArea::vertical().show(ui, |ui| {
                for (i, entry) in self.library.iter().enumerate() {
                    let selected = i == self.selected;
                    let frame = egui::Frame::new()
                        .fill(if selected { Color32::from_rgba_unmultiplied(232, 160, 60, 28) } else { Color32::TRANSPARENT })
                        .stroke(if selected { egui::Stroke::new(1.0_f32, ACCENT) } else { egui::Stroke::NONE })
                        .corner_radius(6.0)
                        .inner_margin(egui::Margin::same(6));
                    let resp = frame.show(ui, |ui| {
                        ui.set_width(ui.available_width());
                        ui.horizontal(|ui| {
                            match self.thumbs.get(i).and_then(|t| t.as_ref()) {
                                Some(t) => {
                                    ui.image((t.id(), egui::vec2(54.0, 54.0 * t.size()[1] as f32 / t.size()[0].max(1) as f32)));
                                }
                                None => {
                                    let (r, _) = ui.allocate_exact_size(egui::vec2(54.0, 36.0), egui::Sense::hover());
                                    ui.painter().rect_filled(r, 3.0, Color32::from_gray(45));
                                }
                            }
                            ui.vertical(|ui| {
                                let name = if entry.unsaved { format!("{} •", entry.name) } else { entry.name.clone() };
                                ui.label(RichText::new(name).strong());
                                let kind = match (entry.recipe.is_some(), entry.look.meta.intent_kind.as_str()) {
                                    (true, _) => "recipe".to_string(),
                                    (false, k) if !k.is_empty() => k.to_string(),
                                    _ => "tables".to_string(),
                                };
                                ui.label(RichText::new(format!("{kind} · {}", entry.targets().join(", "))).small().color(Color32::from_gray(150)));
                            });
                        });
                    });
                    if resp.response.interact(egui::Sense::click()).clicked() {
                        actions.push(Action::Select(i));
                    }
                }
            });
        });

        egui::SidePanel::right("controls").default_width(330.0).resizable(true).show(ctx, |ui| {
            egui::ScrollArea::vertical().show(ui, |ui| {
                ui.add_space(4.0);
                ui.heading("Mode");
                ui.horizontal(|ui| {
                    let mut choice = self.mode;
                    ui.selectable_value(&mut choice, LabMode::Photo, "📷 Photo");
                    ui.selectable_value(&mut choice, LabMode::Movie, "🎬 Movie");
                    if choice != self.mode {
                        self.set_mode(choice);
                    }
                });
                ui.label(
                    RichText::new(match self.mode {
                        LabMode::Photo => "Still JPEG: Directional + Still DCV, ISO NR, FC, full range, compiled/<target>.bin.",
                        LabMode::Movie => "Movie EC: Movie DCV, record-31 EC, legal range, compiled/<target>_movie.bin (fallback to Photo).",
                    })
                    .small()
                    .weak(),
                );
                ui.separator();
                ui.heading("Input");
                let resp = ui.add(egui::Slider::new(&mut self.ev, -6.0..=4.0).step_by(0.1).text("Exposure (EV)"))
                    .on_hover_text("Gain before the integer chain. RAW sensor white maps to the top EC knot (8191) at 0 EV.");
                if resp.changed() {
                    self.invalidate_all();
                    self.mark_edit();
                }
                self.control_active |= resp.dragged();
                let before = self.gamma_path;
                ui.add_enabled_ui(self.mode == LabMode::Photo, |ui| {
                    egui::ComboBox::from_label("Gamma path")
                        .selected_text(self.gamma_path.label())
                        .show_ui(ui, |ui| {
                            for p in [GammaPath::StillJpeg, GammaPath::MovieEc, GammaPath::StillFc, GammaPath::EcThenFc, GammaPath::FcThenEc] {
                                ui.selectable_value(&mut self.gamma_path, p, p.label());
                            }
                        })
                        .response
                        .on_hover_text("Which camera gamma stage(s) run. The EC/FC order is open in the evidence; EC record 31 is the verified Movie S-Log3 table.")
                        .on_disabled_hover_text("Movie mode locks EC record-31. Use ISP stages (advanced) or ISP routing below for overrides.");
                });
                if before != self.gamma_path {
                    self.invalidate_all();
                }
                if ui.checkbox(&mut self.legal, "Legal range (16–235)").on_hover_text("Encoder range. Unchecked = full range 0–255.").changed() {
                    self.invalidate_all();
                }
                ui.separator();

                let selected = self.selected;
                let mut events: Option<ControlEvents> = None;
                if let Some(entry) = self.library.get_mut(selected) {
                    ui.horizontal(|ui| {
                        ui.heading("Look");
                        ui.label(RichText::new(&entry.name).color(ACCENT).strong());
                    });
                    if !entry.report.is_empty() {
                        ui.label(RichText::new(&entry.report).small().weak());
                    }
                    ui.horizontal(|ui| {
                        ui.label("Name");
                        if ui.text_edit_singleline(&mut entry.name).changed() {
                            entry.unsaved = true;
                        }
                    });
                    match entry.recipe.as_mut() {
                        Some(recipe) => {
                            let snapshot = recipe.clone();
                            let ev = recipe_controls(ui, recipe);
                            if ev.started && !self.editing {
                                self.undo.push(snapshot);
                                self.redo.clear();
                                self.editing = true;
                            }
                            events = Some(ev);
                        }
                        None => {
                            ui.label(RichText::new("This preset holds compiled tables only (from a .cube, .a6lt or a mod), so there are no recipe sliders. Edit the source and import it again.").weak());
                        }
                    }
                    ui.horizontal(|ui| {
                        if ui.add_enabled(!self.undo.is_empty(), egui::Button::new("Undo")).clicked() {
                            actions.push(Action::Undo);
                        }
                        if ui.add_enabled(!self.redo.is_empty(), egui::Button::new("Redo")).clicked() {
                            actions.push(Action::Redo);
                        }
                        if ui.add_enabled(entry.unsaved && entry.path.is_some(), egui::Button::new("Revert")).clicked() {
                            actions.push(Action::ResetRecipe);
                        }
                    });
                }
                if let Some(ev) = events {
                    self.control_active |= ev.active;
                    if ev.changed {
                        self.mark_edit();
                        self.request_fit();
                    }
                    if ev.finished {
                        self.editing = false;
                    }
                }
                ui.separator();

                let is_raw = self.source.as_ref().is_some_and(|s| s.raw.is_some());
                let (side_a, side_b) = self.side_inputs();
                let has_dcv = self.entry().is_some_and(|e| e.look.spatial.iter().any(|(n, _)| n.starts_with("dcv")));
                section(ui, "Debayer & detail", true, |ui| {
                    if !is_raw {
                        ui.label(RichText::new("Open a camera RAW to use these: a JPEG/PNG has no sensor data to demosaic.").small().weak());
                    }
                    ui.add_enabled_ui(is_raw, |ui| {
                        egui::ComboBox::from_label("Demosaic")
                            .selected_text(self.spatial.demosaic.label())
                            .show_ui(ui, |ui| {
                                for d in DemosaicChoice::ALL {
                                    ui.selectable_value(&mut self.spatial.demosaic, d, d.label());
                                }
                            })
                            .response
                            .on_hover_text("Sony's demosaic is not decoded (badge A). Directional follows ARRI ADA-5/7 as described in E0600: 5x5 directional gradients, shadow-damped Hamilton-Adams estimates, colour-difference R/B, false-colour median.");
                        egui::ComboBox::from_label("DCV path")
                            .selected_text(self.spatial.path.label())
                            .show_ui(ui, |ui| {
                                for p in SpatialPath::ALL {
                                    ui.selectable_value(&mut self.spatial.path, p, p.label());
                                }
                            })
                            .response
                            .on_hover_text("Which /usr/sabin dcv_psf table the camera mode uses. The table layout is decoded; where the hardware applies it is modeled (badge A).");
                        if self.spatial.path == SpatialPath::Still {
                            ui.checkbox(&mut self.natural_still_dcv, "Fine-tuned natural still DCV (DoG)").on_hover_text("When the selected preset has no still DCV table, side A uses the designed low-halo DoG table while side B uses stock.");
                        }
                        let iso = self.source.as_ref().and_then(|s| s.raw.as_ref()).and_then(|r| r.iso);
                        let modeled = self.spatial.front_options().with_iso_preset(iso);
                        let iso_label = iso.map(|v| format!("ISO {v}")).unwrap_or_else(|| "ISO unknown".into());
                        let mut model = self.spatial.high_iso_model;
                        let resp = ui.checkbox(&mut model, "Model high-ISO NR & crispening");
                        if model != self.spatial.high_iso_model {
                            self.spatial.high_iso_model = model;
                            self.invalidate_all();
                        }
                        let resp = if self.spatial.high_iso_model {
                            resp.on_hover_text(format!(
                                "{iso_label}: a 3x3 edge-preserving luma bilateral (strength {:.2}) before the still DCV, and a soft threshold {:.4} on its high-pass delta. Models CAMNRSTL/KKNR/LFNR and PP_DetailCrispening (badge A); their parameters are not decoded.",
                                modeled.luma_nr_strength, modeled.crispening_threshold
                            ))
                        } else {
                            resp.on_hover_text("Off: no modeled noise reduction, matching the earlier regression baselines.")
                        };
                        if iso.is_none() {
                            ui.label(RichText::new("ISO comes from the RAW EXIF; without it no NR preset is applied.").small().weak());
                        }
                        let _ = resp;
                        if self.mode == LabMode::Movie {
                            let has_arri_for_path = self
                                .spatial
                                .table_name()
                                .is_some_and(|name| self.arri_movie_spatial.iter().any(|(n, _)| n == name));
                            let mut arri = self.arri_movie_dcv;
                            let resp = ui
                                .add_enabled(has_arri_for_path, egui::Checkbox::new(&mut arri, "Arrivederci v5 Movie DCV"))
                                .on_hover_text("Side A uses the Arrivederci v5 Movie DCV table (dcv_psf_table_movie_4k / movie_bayer); Side B stays stock.")
                                .on_disabled_hover_text("Select 4K movie or HD movie Bayer DCV (and ensure artifacts/analysis/arri-v4-sabin is present).");
                            if resp.changed() {
                                self.arri_movie_dcv = arri;
                                self.invalidate_all();
                            }
                        }
                        if ui.button("Still path").on_hover_text("Configure Directional demosaic, Still DCV (q=13, DC-free), Still JPEG Standard tone, 0.0 EV, and full 0-255 range.").clicked() {
                            self.spatial.demosaic = DemosaicChoice::Directional;
                            self.spatial.path = SpatialPath::Still;
                            self.spatial.q = 13;
                            self.spatial.dc_free = true;
                            self.gamma_path = GammaPath::StillJpeg;
                            self.ev = 0.0;
                            self.legal = false;
                            self.invalidate_all();
                        }
                        if self.spatial.path != SpatialPath::Off {
                            ui.label(RichText::new(format!("A: {}", side_a.label)).small());
                            ui.label(RichText::new(format!("B: {}", side_b.label)).small());
                            for w in [&side_a.warning, &side_b.warning].into_iter().flatten() {
                                ui.label(RichText::new(w).small().color(Color32::from_rgb(235, 150, 60)));
                            }
                            egui::CollapsingHeader::new(RichText::new("Model assumptions").small()).default_open(false).show(ui, |ui| {
                                ui.add(egui::Slider::new(&mut self.spatial.q, 12..=16).text("Fixed-point q")).on_hover_text("Kernels are divided by 2^q. 14 is assumed.");
                                ui.checkbox(&mut self.spatial.dc_free, "DC-free kernels").on_hover_text("Remove each kernel's sum. C0029 (v4 ON) shows no zone-boundary step, which a DC-passing kernel would cause.");
                                ui.checkbox(&mut self.spatial.zone_bilinear, "Blend between zones").on_hover_text("Off: nearest of the 9x7 zones. On: bilinear between zone centres.");
                                ui.add(egui::Slider::new(&mut self.spatial.gain, 0.0..=2.0).text("DCV strength")).on_hover_text("Multiplier on the high-pass, 1 = as stored.");
                            });
                        }
                        if has_dcv && side_a.warning.is_some() && !self.stock_spatial.is_empty() {
                            if ui.button("Repair preset DCV tables").on_hover_text("Rebuild this preset's dcv_psf tables from stock at the correct offsets, with the gain it was meant to have. Creates a new preset.").clicked() {
                                actions.push(Action::RepairSpatial);
                            }
                        }
                    });
                    ui.label(RichText::new(&self.stock_spatial_note).small().weak());
                });
                ui.separator();

                section(ui, "ISP stages (advanced)", false, |ui| {
                    ui.label(RichText::new("Tick to bypass a stage. Click a name to inspect its output below.").small().weak());
                    ui.horizontal(|ui| {
                        ui.label(RichText::new("V").color(Color32::from_rgb(90, 190, 110)).strong());
                        ui.label(RichText::new("verified").small());
                        ui.label(RichText::new("M").color(Color32::from_rgb(100, 150, 230)).strong());
                        ui.label(RichText::new("modeled").small());
                        ui.label(RichText::new("A").color(Color32::from_rgb(235, 150, 60)).strong());
                        ui.label(RichText::new("assumption").small());
                    });
                    let before = self.bypass;
                    egui::Grid::new("stages").num_columns(3).spacing([6.0, 3.0]).show(ui, |ui| {
                        ui.label(RichText::new("Bypass").small().weak());
                        ui.label(RichText::new("Stage").small().weak());
                        ui.label("");
                        ui.end_row();
                        for stage in stage_catalog() {
                            let idx = stage.index as usize - 1;
                            let runs_in_chain = (6..=14).contains(&stage.index);
                            ui.add_enabled(runs_in_chain, egui::Checkbox::without_text(&mut self.bypass[idx]))
                                .on_disabled_hover_text("Runs before the integer chain (RAW front end) or is view-only");
                            if ui.selectable_label(self.tap == idx, format!("{:>2}  {}", stage.index, stage.name)).on_hover_text(stage.note).clicked() {
                                self.tap = idx;
                                self.dirty_tap = true;
                            }
                            badge(ui, stage.badge);
                            ui.end_row();
                        }
                    });
                    if self.bypass != before {
                        self.invalidate_all();
                    }
                    ui.add_space(6.0);
                    ui.label(RichText::new("ISP routing").strong()).on_hover_text("Manual overrides for how the look table is merged into the integer chain. Stock side B is never affected.");
                    let r = &mut self.routing;
                    let mut changed = false;
                    ui.horizontal(|ui| {
                        ui.label(RichText::new("Base matrix").small());
                        egui::ComboBox::from_id_salt("base_matrix_source").selected_text(r.base_matrix_source.label()).show_ui(ui, |ui| {
                            for v in [BaseMatrixSource::Auto, BaseMatrixSource::CameraToSrgb, BaseMatrixSource::PresetBaseMatrix, BaseMatrixSource::Identity] {
                                changed |= ui.selectable_value(&mut r.base_matrix_source, v, v.label()).changed();
                            }
                        });
                    });
                    ui.horizontal(|ui| {
                        ui.label(RichText::new("Look matrix").small());
                        egui::ComboBox::from_id_salt("color_matrix_mode").selected_text(r.color_matrix_mode.label()).show_ui(ui, |ui| {
                            for v in [ColorMatrixMode::Auto, ColorMatrixMode::RelativeToStock, ColorMatrixMode::AbsoluteA0, ColorMatrixMode::AbsoluteA1, ColorMatrixMode::AbsoluteB2] {
                                changed |= ui.selectable_value(&mut r.color_matrix_mode, v, v.label()).changed();
                            }
                        });
                    });
                    changed |= ui.add(egui::Slider::new(&mut r.lut_strength, 0.0..=1.0).text("RefAdr strength")).changed();
                    ui.horizontal(|ui| {
                        ui.label(RichText::new("EC knots").small());
                        egui::ComboBox::from_id_salt("ec_record").selected_text(match r.ec_record { Some(31) => "Record 31", Some(0) => "Uniform", _ => "As loaded" }).show_ui(ui, |ui| {
                            changed |= ui.selectable_value(&mut r.ec_record, None, "As loaded").changed();
                            changed |= ui.selectable_value(&mut r.ec_record, Some(31), "Record 31").changed();
                            changed |= ui.selectable_value(&mut r.ec_record, Some(0), "Uniform").changed();
                        });
                    });
                    if changed {
                        self.invalidate_look();
                    }
                    if let Some(stage) = stage_catalog().get(self.tap) {
                        ui.add_space(6.0);
                        ui.label(RichText::new(format!("Stage {} output: {}", stage.index, stage.name)).strong());
                        ui.label(RichText::new(stage.note).small().weak());
                        if let Some(t) = &self.tap_texture {
                            let w = ui.available_width().min(300.0);
                            ui.image((t.id(), egui::vec2(w, w * t.size()[1] as f32 / t.size()[0].max(1) as f32)));
                        } else {
                            ui.label(RichText::new("Open an image to see this stage.").weak());
                        }
                        let look = self.look_params(false);
                        let stock = self.look_params(true);
                        match stage.index {
                            7 => {
                                ui.label("EC curve: look (orange) vs stock (grey)");
                                draw_curve(ui, &look.ec, &stock.ec, look.gamma_max as f32);
                            }
                            8 => {
                                ui.label("FC curve: look (orange) vs stock (grey)");
                                draw_curve(ui, &look.fc, &stock.fc, 1023.0);
                            }
                            9 => {
                                ui.label("A0 matrix (Q10), look");
                                draw_matrix(ui, &look.color_matrix);
                                ui.label("stock");
                                draw_matrix(ui, &stock.color_matrix);
                            }
                            6 => {
                                ui.label("Base matrix (identity unless a Color Mode table is loaded)");
                                draw_matrix(ui, &look.base_matrix);
                            }
                            12 => {
                                ui.add(egui::Slider::new(&mut self.lut_y, 0..=4).text("Y node"));
                                ui.label(RichText::new("dY per Cb (up) × Cr (right) node; green +, red −").small().weak());
                                draw_lut_slice(ui, &look.delta, self.lut_y);
                            }
                            _ => {}
                        }
                    }
                });
            });
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            if self.source.is_none() {
                ui.vertical_centered(|ui| {
                    ui.add_space(ui.available_height() * 0.18);
                    ui.label(RichText::new("Start here").size(26.0).strong());
                    ui.add_space(10.0);
                    for (n, text) in [
                        ("1", "Open a Sony ARW (or drop it on this window)."),
                        ("2", "Pick a preset on the left. Tune it with the Look sliders on the right."),
                        ("3", "Compare: Split shows emulated vs stock. Add the in-camera JPEG of the same shot to compare with the real camera."),
                        ("4", "Save the preset (.bzlook) and Export Camera Mod (.mod) to build the camera mod."),
                    ] {
                        ui.label(RichText::new(format!("{n}.  {text}")).size(15.0));
                    }
                    ui.add_space(16.0);
                    ui.horizontal(|ui| {
                        ui.add_space((ui.available_width() - 420.0).max(0.0) / 2.0);
                        if ui.add(egui::Button::new(RichText::new("📂  Open photo / RAW").size(16.0)).min_size(egui::vec2(200.0, 40.0))).clicked() {
                            actions.push(Action::OpenSource);
                        }
                        if ui.add(egui::Button::new(RichText::new("🎨  Try the test chart").size(16.0)).min_size(egui::vec2(200.0, 40.0))).clicked() {
                            actions.push(Action::UseChart);
                        }
                    });
                    ui.add_space(12.0);
                    ui.label(RichText::new("Nothing in this app connects to the camera.").weak());
                });
                return;
            }
            ui.horizontal(|ui| {
                for (v, label, tip) in [
                    (View::Emulated, "Emulated", "Output of the emulated ISP with this preset"),
                    (View::Split, "Split", "Drag the orange divider to move it"),
                    (View::SideBySide, "Side by side", "Both panels zoom and pan together"),
                    (View::Difference, "Difference", "|A - B| x 4"),
                    (View::DeltaE, "ΔE map", "Per-pixel ΔE2000 heat map"),
                    (View::Reference, b_name, "The comparison image"),
                ] {
                    if ui.selectable_label(self.view == v, label).on_hover_text(tip).clicked() {
                        self.view = v;
                        if self.fit {
                            self.apply_fit();
                        }
                    }
                }
                ui.separator();
                if ui.selectable_label(self.fit, "Fit").on_hover_text("F or 0, or double-click the image").clicked() {
                    actions.push(Action::ZoomFit);
                }
                if ui.button("100%").on_hover_text("1 image pixel = 1 screen pixel (key 1)").clicked() {
                    actions.push(Action::ZoomTo(1.0));
                }
                if ui.button("200%").on_hover_text("Key 2").clicked() {
                    actions.push(Action::ZoomTo(2.0));
                }
                if ui.button("−").on_hover_text("Zoom out (-)").clicked() {
                    actions.push(Action::ZoomBy(0.8));
                }
                if ui.button("+").on_hover_text("Zoom in (+)").clicked() {
                    actions.push(Action::ZoomBy(1.25));
                }
                ui.label(RichText::new("Wheel: zoom · drag: pan").small().weak());
            });
            self.viewer(ui, b_name);
        });

        if self.mod_export.open {
            let mut state = std::mem::take(&mut self.mod_export);
            let mut open = state.open;
            let name = self.entry().map(|e| e.name.clone()).unwrap_or_default();
            let targets_note = match self.entry() {
                Some(e) => format!("This preset carries tables for: {}.", e.targets().join(", ")),
                None => String::new(),
            };
            egui::Window::new("Export Camera Mod (.mod)").open(&mut open).default_width(560.0).show(ctx, |ui| {
                mod_export_ui(ui, &mut state, &name, &targets_note, &mut actions);
            });
            state.open = open;
            self.mod_export = state;
        }

        if self.show_help {
            egui::Window::new("How to use BIONZ Look Lab").open(&mut self.show_help).default_width(520.0).show(ctx, |ui| {
                ui.label(RichText::new("Load an image").strong());
                ui.label("Open photo / RAW (Ctrl+O) or drop a file. Sony ARW is decoded from the sensor data (black level, white balance, 2x2 demosaic) and runs through the emulated integer chain: EC gamma > color matrix > YCbCr > 5x9x9 residual LUT > 8-bit encoder. JPEG/PNG files are display images, not sensor data, so they start at -3 EV.");
                ui.add_space(6.0);
                ui.label(RichText::new("Zoom and pan").strong());
                ui.label("Mouse wheel or pinch zooms about the pointer; drag pans; double-click, F or 0 fits; 1 and 2 jump to 100% and 200%. Zooming past the preview size renders the full decoded image in the background.");
                ui.add_space(6.0);
                ui.label(RichText::new("Presets").strong());
                ui.label("The left list shows every .bzlook in the preset folder. Import adds .bzlook, .cube (fitted onto the ISP tables) or .a6lt. Moving a Look slider refits the tables live and shows a low-resolution preview; the full preview follows when you stop. A dot (•) means unsaved; use Save preset as.");
                ui.add_space(6.0);
                ui.label(RichText::new("Compare with the real camera").strong());
                ui.label("Shoot RAW+JPEG with the preset active on the camera. Open the ARW, then Add camera JPEG (or drop the JPEG with the same name). Split, Difference and ΔE map then compare Emulated with the camera, and the bottom bar shows ΔE. Without a JPEG, the comparison is against the stock tables. In-camera JPEGs come from the still path: try Gamma path: Still: FC gamma only. Which gamma table the still path uses is not verified yet.");
                ui.add_space(6.0);
                ui.label(RichText::new("Film Sims").strong());
                ui.label("Export Camera Mod (.mod) writes a finished SMOD v2 file for <SD>:\\mods\\ for the a6400 and/or the a6100. Pick both bodies to get <folder>\\a6400\\mods\\ and <folder>\\a6100\\mods\\, or one body to write straight into the folder you point at. The mod is generated inside the app; no compiler is needed. Templates whose stock tables are synthetic, and Movie/Arrivederci presets with a Color Mode base matrix, are refused: the mod restores the stock tables when it turns off. The C header packer used by mods/film_sims is still available under Advanced in the same dialog.");
                ui.add_space(6.0);
                ui.label(RichText::new("Badges").strong());
                ui.label("V = verified in firmware or on hardware, M = modeled from evidence, A = assumption. A-stages are switchable models, not camera measurements.");
            });
        }

        for action in actions {
            self.run(action, ctx);
        }
        self.control_active |= control_was_active && ctx.input(|i| i.pointer.any_down());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn presets() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../mods/film_sims/presets")
    }

    #[test]
    fn chart_develops_identically_on_gpu_and_cpu() {
        let chart = bionz_raw::downscale(scopes::test_chart(), 160);
        let (entries, _) = looks::load_library(&presets());
        let mut params = entries[0].params_mode("a6400", false, false).unwrap();
        params.bypass[7] = true;
        let pixels = bionz_raw::quantize(&chart, DISPLAY_IMAGE_EV, params.gamma_max);
        let gpu = bionz_gpu::run_gpu_banded(&pixels, chart.width as i32, &params).unwrap();
        assert_eq!(gpu, integer_pipeline(&pixels, chart.width as i32, &params));
    }

    #[test]
    fn look_and_stock_differ_on_the_chart() {
        let chart = bionz_raw::downscale(scopes::test_chart(), 120);
        let (entries, _) = looks::load_library(&presets());
        let chrome = entries.iter().find(|e| e.name == "Classic Chrome").unwrap();
        let run = |stock: bool| {
            let params = chrome.params_mode("a6400", stock, false).unwrap();
            let pixels = bionz_raw::quantize(&chart, DISPLAY_IMAGE_EV, params.gamma_max);
            integer_pipeline(&pixels, chart.width as i32, &params)
        };
        assert_ne!(run(false), run(true));
    }

    #[test]
    fn source_tiers_shrink_and_keep_aspect() {
        let source = Source::build(PathBuf::from("chart"), scopes::test_chart());
        assert!(source.preview.width <= PREVIEW_SIDE && source.proxy.width <= PROXY_SIDE);
        assert!(source.proxy.width < source.full.width);
        let ratio = |i: &LinearImage| i.width as f32 / i.height as f32;
        assert!((ratio(&source.proxy) - ratio(&source.full)).abs() < 0.05);
    }

    #[test]
    fn render_worker_keeps_only_the_newest_request() {
        let ctx = egui::Context::default();
        let (out_tx, out_rx) = channel();
        let tx = worker::spawn_renderer(out_tx, ctx);
        let source = Source::build(PathBuf::from("chart"), scopes::test_chart());
        for generation in 1..=20 {
            tx.send(RenderJob { slot: Slot::Look, generation, tier: Tier::Proxy, image: source.proxy.clone(), params: IntegerParams::default(), ev: -3.0 }).unwrap();
        }
        let mut seen = Vec::new();
        while let Ok(done) = out_rx.recv_timeout(Duration::from_secs(10)) {
            assert_eq!(done.rgba.len(), done.width * done.height * 4);
            seen.push(done.generation);
            if done.generation == 20 {
                break;
            }
        }
        assert_eq!(seen.last(), Some(&20));
        assert!(seen.len() < 20, "stale requests were not dropped: {seen:?}");
    }

    #[test]
    fn live_picker_dialog_writes_filmsimulations_for_the_whole_library() {
        let (entries, _) = looks::load_library(&presets());
        let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/look-lab-dialog-live");
        let _ = std::fs::remove_dir_all(&dir);
        let ctx = egui::Context::default();
        let mut state = ModExport { open: true, targets: looks::ModTargetSelection::Both, kind: ModKind::LivePicker, dir: Some(dir.clone()), status: String::new() };
        let mut actions = Vec::new();
        let _ = ctx.run(egui::RawInput::default(), |ctx| {
            egui::CentralPanel::default().show(ctx, |ui| {
                mod_export_ui(ui, &mut state, "Classic Chrome", "", &mut actions);
            });
        });
        assert!(actions.is_empty(), "rendering must not export anything");
        assert_eq!(state.kind.label(), "Live picker");
        let (summary, detail) = looks::export_live_mods_report(&dir, &entries, state.targets).unwrap();
        state.status = format!("{summary}\n{detail}");
        let a6400 = dir.join("a6400").join("mods").join("FilmSimulations.mod");
        let a6100 = dir.join("a6100").join("mods").join("FilmSimulations.mod");
        assert_eq!(detail.lines().collect::<Vec<_>>(), vec![a6400.display().to_string(), a6100.display().to_string()]);
        assert!(summary.contains("12 look(s)"), "{summary}");
        for path in [&a6400, &a6100] {
            let blob = std::fs::read(path).unwrap();
            assert_eq!(&blob[..4], b"SMOD");
            assert_eq!(u32::from_le_bytes(blob[8..12].try_into().unwrap()), 20, "FLAG_ACTION | FLAG_RELOC");
            assert_eq!(&blob[12..28], b"Film Simulations");
            assert_eq!(u32::from_le_bytes(blob[64..68].try_into().unwrap()), 1, "the event_push hook");
        }
        let mut actions = Vec::new();
        let _ = ctx.run(egui::RawInput::default(), |ctx| {
            egui::CentralPanel::default().show(ctx, |ui| {
                mod_export_ui(ui, &mut state, "Classic Chrome", "", &mut actions);
            });
        });
        assert!(state.status.contains("FilmSimulations.mod"));
        assert!(actions.is_empty());
    }

    #[test]
    fn mod_export_dialog_renders_headless_and_writes_the_files_it_reports() {
        let (entries, _) = looks::load_library(&presets());
        let chrome = entries.iter().find(|e| e.name == "Classic Chrome").unwrap();
        let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/look-lab-dialog-export");
        let _ = std::fs::remove_dir_all(&dir);
        // The dialog body runs in a bare egui context: no window, no GPU, no camera.
        let ctx = egui::Context::default();
        let mut state = ModExport { open: true, targets: looks::ModTargetSelection::Both, kind: ModKind::SingleLook, dir: Some(dir.clone()), status: String::new() };
        let mut actions = Vec::new();
        let _ = ctx.run(egui::RawInput::default(), |ctx| {
            egui::CentralPanel::default().show(ctx, |ui| {
                mod_export_ui(ui, &mut state, &chrome.name, "This preset carries tables for: a6400, a6100.", &mut actions);
            });
        });
        assert!(actions.is_empty(), "rendering the dialog must not export anything by itself");
        assert_eq!(state.targets, looks::ModTargetSelection::Both);

        // The dialog's Export button pushes exactly this action; run its body.
        let (summary, detail) = looks::export_mods_report(&dir, chrome, state.targets).unwrap();
        state.status = format!("{summary}\n{detail}");
        let a6400 = dir.join("a6400").join("mods").join("ClassicChrome.mod");
        let a6100 = dir.join("a6100").join("mods").join("ClassicChrome.mod");
        assert!(a6400.is_file() && a6100.is_file(), "{detail}");
        assert_eq!(detail.lines().collect::<Vec<_>>(), vec![a6400.display().to_string(), a6100.display().to_string()]);
        for path in [&a6400, &a6100] {
            let blob = std::fs::read(path).unwrap();
            assert_eq!(&blob[..4], b"SMOD");
            assert_eq!(u32::from_le_bytes(blob[4..8].try_into().unwrap()), 2);
            assert_eq!(&blob[12..26], b"Classic Chrome");
        }

        // Re-render with the status the window shows, so the reported paths are on screen.
        let mut actions = Vec::new();
        let _ = ctx.run(egui::RawInput::default(), |ctx| {
            egui::CentralPanel::default().show(ctx, |ui| {
                mod_export_ui(ui, &mut state, &chrome.name, "", &mut actions);
            });
        });
        assert!(state.status.contains("ClassicChrome.mod"));
        assert!(actions.is_empty());
    }

    #[test]
    fn user_stage_bypass_wins_after_mode_defaults() {
        let look = bionz_preset::load_bzlook(&presets().join("gold-200.bzlook")).unwrap();
        let (movie, _) = look.movie_table_or_photo("a6400").unwrap();
        let mut params = bionz_preset::integer_params_from_compiled(movie);
        bionz_preset::apply_movie_look(&mut params, movie, None, true);
        assert!(!params.bypass[6] && params.bypass[7], "Movie defaults");
        let mut user = [false; 16];
        user[6] = true;
        apply_user_bypass(&mut params, &user);
        assert!(params.bypass[6], "a user EC bypass must survive the Movie defaults");
        // Photo: the still chain sets EC on / FC off; a user FC bypass must survive.
        let mut photo = IntegerParams::default();
        photo.bypass[6] = true;
        photo.bypass[7] = false;
        let mut user = [false; 16];
        user[7] = true;
        apply_user_bypass(&mut photo, &user);
        assert!(photo.bypass[7]);
    }

    #[test]
    fn same_stem_matches_raw_and_jpeg_pairs() {        assert!(same_stem(Path::new("E:/DCIM/_DSC1479.ARW"), Path::new("C:/x/_dsc1479.JPG")));
        assert!(!same_stem(Path::new("a.ARW"), Path::new("b.JPG")));
    }

    #[test]
    fn camera_base_matrix_fills_identity_and_preserves_an_embedded_matrix() {
        let cam = [1241, -205, -12, 62, 1116, -154, 30, -251, 1245];
        let mut params = IntegerParams::default();
        fill_camera_base_matrix(&mut params, Some(cam));
        assert_eq!(params.base_matrix, cam);
        // A preset that embeds its own matrix keeps it.
        let embedded = [1000, 10, 14, -2, 1010, 16, 0, -20, 1044];
        params.base_matrix = embedded;
        fill_camera_base_matrix(&mut params, Some(cam));
        assert_eq!(params.base_matrix, embedded);
        // No RAW matrix: identity stays identity.
        let mut params = IntegerParams::default();
        fill_camera_base_matrix(&mut params, None);
        assert_eq!(params.base_matrix, [1024, 0, 0, 0, 1024, 0, 0, 0, 1024]);
    }

    #[test]
    fn paired_jpeg_finds_a_same_stem_file_and_reports_none_otherwise() {        let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(format!("../../../target/test-pairing-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let raw = dir.join("_DSC0001.ARW");
        std::fs::write(&raw, b"x").unwrap();
        assert_eq!(paired_jpeg(&raw), None, "no JPEG yet");
        let jpg = dir.join("_DSC0001.JPG");
        std::fs::write(&jpg, b"x").unwrap();
        assert_eq!(paired_jpeg(&raw), Some(jpg));
        let other = dir.join("_DSC0002.ARW");
        std::fs::write(&other, b"x").unwrap();
        assert_eq!(paired_jpeg(&other), None, "a different stem must not pair");
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
