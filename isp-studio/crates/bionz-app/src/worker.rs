//! Background workers so the UI thread never runs the ISP chain or the fitter.
//! Both workers drop stale requests: only the newest request per slot is processed.

use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::Arc;
use std::time::Instant;

use bionz_core::{encoded_to_rgb8, integer_pipeline, IntegerParams};
use bionz_fit::Recipe;
use bionz_preset::Bzlook;
use bionz_raw::LinearImage;
use eframe::egui;

/// Which image a render is for: A (the look) or B (the same image through the stock tables).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Slot {
    Look,
    Stock,
}

/// Resolution tier. Proxy while a control moves, Preview when idle, Full when zoomed in.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Tier {
    Proxy,
    Preview,
    Full,
}

pub struct RenderJob {
    pub slot: Slot,
    pub generation: u64,
    pub tier: Tier,
    pub image: Arc<LinearImage>,
    pub params: IntegerParams,
    pub ev: f32,
}

pub struct Rendered {
    pub slot: Slot,
    pub generation: u64,
    pub tier: Tier,
    pub width: usize,
    pub height: usize,
    pub rgba: Vec<u8>,
    pub ms: f64,
    pub gpu: bool,
}

pub fn develop_rgba(image: &LinearImage, params: &IntegerParams, ev: f32) -> (Vec<u8>, bool) {
    let pixels = bionz_raw::quantize(image, ev, params.gamma_max);
    match bionz_gpu::run_gpu_display(&pixels, image.width as i32, params) {
        Ok(rgba) => (rgba, true),
        Err(_) => {
            let rgba = integer_pipeline(&pixels, image.width as i32, params)
                .iter()
                .flat_map(|t| {
                    let [r, g, b] = encoded_to_rgb8(t.encoded_y, t.encoded_cb, t.encoded_cr, params.legal_range);
                    [r, g, b, 255]
                })
                .collect();
            (rgba, false)
        }
    }
}

pub fn spawn_renderer(out: Sender<Rendered>, wake: egui::Context) -> Sender<RenderJob> {
    let (tx, rx): (Sender<RenderJob>, Receiver<RenderJob>) = channel();
    std::thread::Builder::new()
        .name("bionz-render".into())
        .spawn(move || {
            while let Ok(first) = rx.recv() {
                let mut look = None;
                let mut stock = None;
                for job in std::iter::once(first).chain(rx.try_iter()) {
                    match job.slot {
                        Slot::Look => look = Some(job),
                        Slot::Stock => stock = Some(job),
                    }
                }
                for job in [look, stock].into_iter().flatten() {
                    let started = Instant::now();
                    let (rgba, gpu) = develop_rgba(&job.image, &job.params, job.ev);
                    let done = Rendered {
                        slot: job.slot,
                        generation: job.generation,
                        tier: job.tier,
                        width: job.image.width,
                        height: job.image.height,
                        rgba,
                        ms: started.elapsed().as_secs_f64() * 1000.0,
                        gpu,
                    };
                    if out.send(done).is_err() {
                        return;
                    }
                    wake.request_repaint();
                }
            }
        })
        .expect("render thread");
    tx
}

pub struct FitJob {
    pub index: usize,
    pub generation: u64,
    pub recipe: Recipe,
    pub base: Bzlook,
}

pub struct Fitted {
    pub index: usize,
    pub generation: u64,
    pub result: Result<(Bzlook, String), String>,
}

pub fn spawn_fitter(out: Sender<Fitted>, wake: egui::Context) -> Sender<FitJob> {
    let (tx, rx): (Sender<FitJob>, Receiver<FitJob>) = channel();
    std::thread::Builder::new()
        .name("bionz-fit".into())
        .spawn(move || {
            while let Ok(first) = rx.recv() {
                let job = rx.try_iter().last().unwrap_or(first);
                let result = crate::looks::compile_recipe(&job.recipe, &job.base);
                if out.send(Fitted { index: job.index, generation: job.generation, result }).is_err() {
                    return;
                }
                wake.request_repaint();
            }
        })
        .expect("fit thread");
    tx
}
