//! GPU spatial front end (Bayer DCV, demosaic, 9x9 DCV). CPU reference: `bionz_core::spatial`.

use bionz_core::spatial::{DemosaicMode, FrontOptions, SHADOW_KNEE};
use bytemuck::{Pod, Zeroable};
use wgpu::util::DeviceExt;

const SHADER: &str = include_str!("front.wgsl");
/// Rows of context each band carries above and below: DCV 4 + green 2 + diagonal pass 2 + green-site
/// pass 1 + median 1 = 10, rounded up.
const HALO: usize = 12;

#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
struct FrontParams {
    w: u32,
    h: u32,
    y_off: u32,
    full_w: u32,
    full_h: u32,
    pattern: [u32; 4],
    mode: u32,
    zone_bilinear: u32,
    has_dcv: u32,
    gain: f32,
    knee: f32,
    crispening: f32,
    pad: u32,
}

struct FrontContext {
    layout: wgpu::BindGroupLayout,
    dcv_bayer: wgpu::ComputePipeline,
    green: wgpu::ComputePipeline,
    opposite: wgpu::ComputePipeline,
    gsites: wgpu::ComputePipeline,
    median: wgpu::ComputePipeline,
    plane9: wgpu::ComputePipeline,
    still: wgpu::ComputePipeline,
}

static FRONT: std::sync::OnceLock<Result<FrontContext, String>> = std::sync::OnceLock::new();

fn front() -> anyhow::Result<(&'static crate::GpuContext, &'static FrontContext)> {
    let ctx = crate::context()?;
    let f = FRONT
        .get_or_init(|| {
            let device = &ctx.device;
            let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor { label: Some("bionz-front"), source: wgpu::ShaderSource::Wgsl(SHADER.into()) });
            let entry = |binding: u32, read_only: bool| wgpu::BindGroupLayoutEntry {
                binding,
                visibility: wgpu::ShaderStages::COMPUTE,
                ty: wgpu::BindingType::Buffer { ty: wgpu::BufferBindingType::Storage { read_only }, has_dynamic_offset: false, min_binding_size: None },
                count: None,
            };
            let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
                label: Some("front"),
                entries: &[entry(0, true), entry(1, true), entry(2, true), entry(3, false), entry(4, false)],
            });
            let pl = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor { label: None, bind_group_layouts: &[&layout], push_constant_ranges: &[] });
            let pipe = |name: &str| {
                device.create_compute_pipeline(&wgpu::ComputePipelineDescriptor {
                    label: Some(name),
                    layout: Some(&pl),
                    module: &shader,
                    entry_point: Some(name),
                    compilation_options: Default::default(),
                    cache: None,
                })
            };
            Ok(FrontContext { dcv_bayer: pipe("dcv_bayer"), green: pipe("demosaic_green"), opposite: pipe("demosaic_opposite"), gsites: pipe("demosaic_gsites"), median: pipe("demosaic_median"), plane9: pipe("dcv_plane9"), still: pipe("dcv_still"), layout })
        })
        .as_ref()
        .map_err(|e| anyhow::anyhow!(e.clone()))?;
    Ok((ctx, f))
}

fn params(w: usize, h: usize, y_off: usize, full_w: usize, full_h: usize, pattern: [u8; 4], has_dcv: bool, opt: &FrontOptions) -> FrontParams {
    FrontParams {
        w: w as u32,
        h: h as u32,
        y_off: y_off as u32,
        full_w: full_w as u32,
        full_h: full_h as u32,
        pattern: pattern.map(|c| c as u32),
        mode: if opt.demosaic == DemosaicMode::Bilinear { 1 } else { 0 },
        zone_bilinear: u32::from(opt.zone_bilinear),
        has_dcv: u32::from(has_dcv),
        gain: opt.dcv_gain,
        knee: SHADOW_KNEE,
        crispening: opt.crispening_threshold,
        pad: 0,
    }
}

fn read_back(ctx: &crate::GpuContext, buffer: &wgpu::Buffer, offset: u64, size: u64, mut encoder: wgpu::CommandEncoder) -> anyhow::Result<Vec<f32>> {
    let read = ctx.device.create_buffer(&wgpu::BufferDescriptor { label: None, size, usage: wgpu::BufferUsages::MAP_READ | wgpu::BufferUsages::COPY_DST, mapped_at_creation: false });
    encoder.copy_buffer_to_buffer(buffer, offset, &read, 0, size);
    ctx.queue.submit(Some(encoder.finish()));
    let slice = read.slice(..);
    let (tx, rx) = std::sync::mpsc::channel();
    slice.map_async(wgpu::MapMode::Read, move |r| {
        let _ = tx.send(r);
    });
    ctx.device.poll(wgpu::Maintain::Wait);
    rx.recv()?.map_err(|e| anyhow::anyhow!(e.to_string()))?;
    let out = bytemuck::cast_slice(&slice.get_mapped_range()).to_vec();
    read.unmap();
    Ok(out)
}

fn dispatch(pass: &mut wgpu::ComputePass, n: usize) {
    let groups = n.div_ceil(64) as u32;
    let (gx, gy) = if groups > 65535 { (65535, groups.div_ceil(65535)) } else { (groups, 1) };
    pass.dispatch_workgroups(gx, gy, 1);
}

/// CFA (black-subtracted, white-balanced, normalized) to interleaved linear RGB, with the optional
/// Bayer DCV (`coef` from `DcvTable::coefficients` of a Bayer table).
pub fn run_front_bayer(cfa: &[f32], w: usize, h: usize, pattern: [u8; 4], coef: Option<&[f32]>, opt: &FrontOptions) -> anyhow::Result<Vec<f32>> {
    run_front_bayer_rows(cfa, w, h, pattern, coef, opt, None)
}

fn run_front_bayer_rows(cfa: &[f32], w: usize, h: usize, pattern: [u8; 4], coef: Option<&[f32]>, opt: &FrontOptions, band_rows: Option<usize>) -> anyhow::Result<Vec<f32>> {
    let (ctx, f) = front()?;
    if cfa.len() != w * h || w < 4 || h < 4 {
        anyhow::bail!("CFA size does not match {w}x{h}");
    }
    let max_px = ((ctx.max_binding / 28) as usize).min(32 << 20);
    let rows = band_rows.unwrap_or_else(|| (max_px / w).saturating_sub(2 * HALO).max(16));
    let dummy = [0.0f32; 4];
    let coef_data = coef.unwrap_or(&dummy);
    let coef_buf = ctx.device.create_buffer_init(&wgpu::util::BufferInitDescriptor { label: None, contents: bytemuck::cast_slice(coef_data), usage: wgpu::BufferUsages::STORAGE });
    let mut out = Vec::with_capacity(w * h * 3);
    let mut y0 = 0;
    while y0 < h {
        let y1 = (y0 + rows).min(h);
        let top = y0.saturating_sub(HALO);
        let bottom = (y1 + HALO).min(h);
        let bh = bottom - top;
        let p = params(w, bh, top, w, h, pattern, coef.is_some(), opt);
        let band = &cfa[top * w..bottom * w];
        let n = w * bh;
        let pbuf = ctx.device.create_buffer_init(&wgpu::util::BufferInitDescriptor { label: None, contents: bytemuck::bytes_of(&p), usage: wgpu::BufferUsages::STORAGE });
        let sbuf = ctx.device.create_buffer_init(&wgpu::util::BufferInitDescriptor { label: None, contents: bytemuck::cast_slice(band), usage: wgpu::BufferUsages::STORAGE });
        let dbuf = ctx.device.create_buffer(&wgpu::BufferDescriptor { label: None, size: (n * 28) as u64, usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_SRC, mapped_at_creation: false });
        let abuf = ctx.device.create_buffer(&wgpu::BufferDescriptor { label: None, size: (n * 4) as u64, usage: wgpu::BufferUsages::STORAGE, mapped_at_creation: false });
        let group = ctx.device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: None,
            layout: &f.layout,
            entries: &[
                wgpu::BindGroupEntry { binding: 0, resource: pbuf.as_entire_binding() },
                wgpu::BindGroupEntry { binding: 1, resource: sbuf.as_entire_binding() },
                wgpu::BindGroupEntry { binding: 2, resource: coef_buf.as_entire_binding() },
                wgpu::BindGroupEntry { binding: 3, resource: dbuf.as_entire_binding() },
                wgpu::BindGroupEntry { binding: 4, resource: abuf.as_entire_binding() },
            ],
        });
        let mut encoder = ctx.device.create_command_encoder(&Default::default());
        for pipeline in [&f.dcv_bayer, &f.green, &f.opposite, &f.gsites, &f.median] {
            let mut pass = encoder.begin_compute_pass(&Default::default());
            pass.set_pipeline(pipeline);
            pass.set_bind_group(0, &group, &[]);
            dispatch(&mut pass, n);
        }
        let skip = y0 - top;
        let keep = y1 - y0;
        let offset = ((4 * n + skip * w * 3) * 4) as u64;
        let rgb = read_back(ctx, &dbuf, offset, (keep * w * 12) as u64, encoder)?;
        out.extend_from_slice(&rgb);
        y0 = y1;
    }
    Ok(out)
}

/// 9x9 DCV on interleaved linear RGB (`coef` from a `movie_4k` / `movie` table).
pub fn run_front_plane9(rgb: &[f32], w: usize, h: usize, coef: &[f32], opt: &FrontOptions) -> anyhow::Result<Vec<f32>> {
    run_front_plane_kernel(rgb, w, h, coef, opt, 4, true)
}

/// 11x7 still DCV on interleaved linear RGB (`coef` from `DcvTable::coefficients_at` of a `Still` table).
pub fn run_front_still(rgb: &[f32], w: usize, h: usize, coef: &[f32], opt: &FrontOptions) -> anyhow::Result<Vec<f32>> {
    run_front_plane_kernel(rgb, w, h, coef, opt, 3, false)
}

fn run_front_plane_kernel(rgb: &[f32], w: usize, h: usize, coef: &[f32], opt: &FrontOptions, halo: usize, is_plane9: bool) -> anyhow::Result<Vec<f32>> {
    let (ctx, f) = front()?;
    if rgb.len() != w * h * 3 {
        anyhow::bail!("RGB size does not match {w}x{h}");
    }
    let max_px = ((ctx.max_binding / 12) as usize).min(32 << 20);
    let rows = (max_px / w).saturating_sub(2 * halo).max(16);
    let coef_buf = ctx.device.create_buffer_init(&wgpu::util::BufferInitDescriptor { label: None, contents: bytemuck::cast_slice(coef), usage: wgpu::BufferUsages::STORAGE });
    let mut out = Vec::with_capacity(rgb.len());
    let mut y0 = 0;
    while y0 < h {
        let y1 = (y0 + rows).min(h);
        let top = y0.saturating_sub(halo);
        let bottom = (y1 + halo).min(h);
        let bh = bottom - top;
        let n = w * bh;
        let p = params(w, bh, top, w, h, [0, 1, 1, 2], true, opt);
        let pbuf = ctx.device.create_buffer_init(&wgpu::util::BufferInitDescriptor { label: None, contents: bytemuck::bytes_of(&p), usage: wgpu::BufferUsages::STORAGE });
        let sbuf = ctx.device.create_buffer_init(&wgpu::util::BufferInitDescriptor { label: None, contents: bytemuck::cast_slice(&rgb[top * w * 3..bottom * w * 3]), usage: wgpu::BufferUsages::STORAGE });
        let dbuf = ctx.device.create_buffer(&wgpu::BufferDescriptor { label: None, size: 16, usage: wgpu::BufferUsages::STORAGE, mapped_at_creation: false });
        let abuf = ctx.device.create_buffer(&wgpu::BufferDescriptor { label: None, size: (n * 12) as u64, usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_SRC, mapped_at_creation: false });
        let group = ctx.device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: None,
            layout: &f.layout,
            entries: &[
                wgpu::BindGroupEntry { binding: 0, resource: pbuf.as_entire_binding() },
                wgpu::BindGroupEntry { binding: 1, resource: sbuf.as_entire_binding() },
                wgpu::BindGroupEntry { binding: 2, resource: coef_buf.as_entire_binding() },
                wgpu::BindGroupEntry { binding: 3, resource: dbuf.as_entire_binding() },
                wgpu::BindGroupEntry { binding: 4, resource: abuf.as_entire_binding() },
            ],
        });
        let mut encoder = ctx.device.create_command_encoder(&Default::default());
        {
            let mut pass = encoder.begin_compute_pass(&Default::default());
            pass.set_pipeline(if is_plane9 { &f.plane9 } else { &f.still });
            pass.set_bind_group(0, &group, &[]);
            dispatch(&mut pass, n);
        }
        let skip = y0 - top;
        let part = read_back(ctx, &abuf, (skip * w * 12) as u64, ((y1 - y0) * w * 12) as u64, encoder)?;
        out.extend_from_slice(&part);
        y0 = y1;
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use bionz_core::spatial::{self, ZONES};

    fn noise(n: usize, seed: u32) -> Vec<f32> {
        let mut s = seed;
        (0..n)
            .map(|_| {
                s = s.wrapping_mul(1664525).wrapping_add(1013904223);
                (s >> 8) as f32 / (1u32 << 24) as f32
            })
            .collect()
    }

    fn bayer_coef() -> Vec<f32> {
        let mut k = vec![0.0f32; ZONES * 3 * 41];
        for z in 0..ZONES {
            for g in 0..3 {
                let b = (z * 3 + g) * 41;
                k[b + 12] = 0.45 + z as f32 * 0.001;
                for i in [7, 11, 13, 17] {
                    k[b + i] = -0.03;
                }
                if g == 0 {
                    for i in [30, 31, 34, 35] {
                        k[b + i] = -0.09;
                    }
                }
            }
        }
        k
    }

    fn close(a: &[f32], b: &[f32], tol: f32) {
        assert_eq!(a.len(), b.len());
        let worst = a.iter().zip(b).map(|(x, y)| (x - y).abs()).fold(0.0f32, f32::max);
        assert!(worst <= tol, "max difference {worst}");
    }

    #[test]
    fn gpu_front_matches_cpu_reference_for_both_demosaics_and_zone_modes() {
        let (w, h) = (64, 46);
        let cfa = noise(w * h, 7);
        let coef = bayer_coef();
        for mode in [DemosaicMode::Directional, DemosaicMode::Bilinear] {
            for zone_bilinear in [false, true] {
                let opt = FrontOptions { demosaic: mode, zone_bilinear, ..Default::default() };
                let cpu = spatial::demosaic(&spatial::dcv_bayer(&cfa, w, h, [0, 1, 1, 2], &coef, &opt), w, h, [0, 1, 1, 2], mode);
                let gpu = run_front_bayer(&cfa, w, h, [0, 1, 1, 2], Some(&coef), &opt).unwrap();
                close(&cpu, &gpu, 2e-4);
            }
        }
    }

    #[test]
    fn bands_match_a_single_dispatch() {
        let (w, h) = (48, 70);
        let cfa = noise(w * h, 11);
        let coef = bayer_coef();
        let opt = FrontOptions::default();
        let whole = run_front_bayer_rows(&cfa, w, h, [1, 0, 2, 1], Some(&coef), &opt, Some(h)).unwrap();
        let banded = run_front_bayer_rows(&cfa, w, h, [1, 0, 2, 1], Some(&coef), &opt, Some(9)).unwrap();
        assert_eq!(whole, banded);
    }

    #[test]
    fn plane9_matches_cpu_reference() {
        let (w, h) = (40, 33);
        let rgb = noise(w * h * 3, 3);
        let mut coef = vec![0.0f32; ZONES * 81];
        for z in 0..ZONES {
            coef[z * 81 + 40] = 0.4;
            for i in [31, 39, 41, 49] {
                coef[z * 81 + i] = -0.1;
            }
        }
        let opt = FrontOptions { zone_bilinear: true, ..Default::default() };
        close(&spatial::dcv_plane9(&rgb, w, h, &coef, &opt), &run_front_plane9(&rgb, w, h, &coef, &opt).unwrap(), 2e-5);
    }

    #[test]
    fn still_matches_cpu_reference() {
        let (w, h) = (40, 33);
        let rgb = noise(w * h * 3, 5);
        let mut coef = vec![0.0f32; spatial::STILL_ZONES * 3 * spatial::STILL_KERNEL_TAPS];
        for z in 0..spatial::STILL_ZONES {
            for ch in 0..3 {
                let b = (z * 3 + ch) * spatial::STILL_KERNEL_TAPS;
                coef[b + spatial::STILL_CENTRE_TAP] = 0.4;
                coef[b + spatial::STILL_CENTRE_TAP - 1] = -0.1;
                coef[b + spatial::STILL_CENTRE_TAP + 1] = -0.1;
                coef[b + spatial::STILL_CENTRE_TAP - spatial::STILL_KERNEL_W] = -0.1;
                coef[b + spatial::STILL_CENTRE_TAP + spatial::STILL_KERNEL_W] = -0.1;
            }
        }
        let opt = FrontOptions { zone_bilinear: true, ..Default::default() };
        close(&spatial::dcv_still(&rgb, w, h, &coef, &opt), &run_front_still(&rgb, w, h, &coef, &opt).unwrap(), 2e-5);
    }

    #[test]
    fn still_crispening_matches_cpu_reference() {
        let (w, h) = (40, 33);
        let rgb = noise(w * h * 3, 9);
        let mut coef = vec![0.0f32; spatial::STILL_ZONES * 3 * spatial::STILL_KERNEL_TAPS];
        for z in 0..spatial::STILL_ZONES {
            for ch in 0..3 {
                let b = (z * 3 + ch) * spatial::STILL_KERNEL_TAPS;
                coef[b + spatial::STILL_CENTRE_TAP] = 0.4;
                coef[b + spatial::STILL_CENTRE_TAP - 1] = -0.1;
                coef[b + spatial::STILL_CENTRE_TAP + 1] = -0.1;
                coef[b + spatial::STILL_CENTRE_TAP + spatial::STILL_KERNEL_W] = -0.1;
                coef[b + spatial::STILL_CENTRE_TAP - spatial::STILL_KERNEL_W] = -0.1;
            }
        }
        let opt = FrontOptions { zone_bilinear: true, crispening_threshold: 0.06, ..Default::default() };
        let cpu = spatial::dcv_still(&rgb, w, h, &coef, &opt);
        let gpu = run_front_still(&rgb, w, h, &coef, &opt).unwrap();
        close(&cpu, &gpu, 2e-5);
        // The soft threshold must actually change the result at this strength.
        let plain = spatial::dcv_still(&rgb, w, h, &coef, &FrontOptions { zone_bilinear: true, ..Default::default() });
        assert!(cpu.iter().zip(&plain).any(|(a, b)| (a - b).abs() > 1e-4));
    }
}
