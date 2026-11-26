//! GPU execution of the integer stage chain. The CPU reference is `bionz_core::integer_pipeline`.

use std::time::Instant;

mod front;
pub use front::{run_front_bayer, run_front_plane9, run_front_still};

use bionz_core::{IntegerParams, RgbI, StageTap};
use bytemuck::{Pod, Zeroable};

/// Host wait for the two compute submits. This is not a GPU timestamp query.
#[derive(Clone, Copy, Debug)]
pub struct PassTimes {
    pub develop_ms: f64,
    pub finish_ms: f64,
}

const SHADER: &str = include_str!("stages.wgsl");

#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
struct GpuParams {
    width: i32,
    gamma_max: i32,
    matrix_q: i32,
    lut_level: i32,
    gain_enable: i32,
    flags: u32,
    legal: i32,
    fc_before_ec: i32,
    ec_full_scale: i32,
    base: [i32; 9],
    color: [i32; 9],
    detail: [i32; 9],
    gain_level: i32,
    pad: [i32; 3],
}

fn flags_of(bypass: &[bool; 16]) -> u32 {
    let mut flags = 0u32;
    for (i, bit) in bypass.iter().enumerate() {
        if *bit {
            flags |= 1 << i;
        }
    }
    flags
}

pub fn run_gpu(pixels: &[RgbI], width: i32, params: &IntegerParams) -> anyhow::Result<Vec<StageTap>> {
    Ok(run_gpu_parts(pixels, width, params)?.0)
}

pub fn run_gpu_parts(pixels: &[RgbI], width: i32, params: &IntegerParams) -> anyhow::Result<(Vec<StageTap>, Vec<i32>)> {
    let (taps, cache, _, _) = run_gpu_seeded(pixels, width, params, None)?;
    Ok((taps, cache))
}

pub fn run_gpu_times(pixels: &[RgbI], width: i32, params: &IntegerParams) -> anyhow::Result<(Vec<StageTap>, PassTimes)> {
    let (taps, _, develop_ms, finish_ms) = run_gpu_seeded(pixels, width, params, None)?;
    Ok((taps, PassTimes { develop_ms, finish_ms }))
}

/// Re-run detail, the residual LUT, and the encoder from a stored post-gain RGB cache.
pub fn rerun_after_gain(pixels: &[RgbI], width: i32, params: &IntegerParams, cache: &[i32]) -> anyhow::Result<(Vec<StageTap>, Vec<i32>)> {
    let (taps, kept, _, _) = run_gpu_seeded(pixels, width, params, Some(cache))?;
    Ok((taps, kept))
}

/// One device, queue, shader and pipeline set for the whole process. Creating a device per call
/// cost about a second and made every preview slow.
pub(crate) struct GpuContext {
    pub(crate) device: wgpu::Device,
    pub(crate) queue: wgpu::Queue,
    layout: wgpu::BindGroupLayout,
    cache_rgb: wgpu::ComputePipeline,
    finish_cached: wgpu::ComputePipeline,
    display_rgba: wgpu::ComputePipeline,
    info: String,
    pub(crate) max_binding: u64,
}

static CONTEXT: std::sync::OnceLock<Result<GpuContext, String>> = std::sync::OnceLock::new();

pub(crate) fn context() -> anyhow::Result<&'static GpuContext> {
    CONTEXT
        .get_or_init(|| create_context().map_err(|err| err.to_string()))
        .as_ref()
        .map_err(|err| anyhow::anyhow!(err.clone()))
}

/// Adapter name and backend, for example "NVIDIA GeForce RTX 3060 (Dx12)". A name ending in
/// "software" means no hardware adapter was found or `BIONZ_SOFTWARE_GPU=1` forced the fallback.
pub fn adapter_info() -> anyhow::Result<String> {
    Ok(context()?.info.clone())
}

fn create_context() -> anyhow::Result<GpuContext> {
    let instance = wgpu::Instance::new(&wgpu::InstanceDescriptor {
        backends: wgpu::Backends::all(),
        ..Default::default()
    });
    let force_software = std::env::var("BIONZ_SOFTWARE_GPU").map(|v| v == "1").unwrap_or(false);
    // Backend order: BIONZ_GPU_BACKEND (vulkan, dx12, gl) when set, otherwise DX12, Metal, then Vulkan.
    // Vulkan is last because the AMD RX 9070 XT Vulkan driver crashed the parity tests (access violation).
    let order: Vec<wgpu::Backends> = match std::env::var("BIONZ_GPU_BACKEND").unwrap_or_default().to_ascii_lowercase().as_str() {
        "vulkan" => vec![wgpu::Backends::VULKAN],
        "dx12" => vec![wgpu::Backends::DX12],
        "gl" => vec![wgpu::Backends::GL],
        _ => vec![wgpu::Backends::DX12, wgpu::Backends::METAL, wgpu::Backends::VULKAN],
    };
    let hardware = if force_software {
        None
    } else {
        order.iter().find_map(|backend| {
            instance
                .enumerate_adapters(*backend)
                .into_iter()
                .filter(|a| a.get_info().device_type != wgpu::DeviceType::Cpu)
                .max_by_key(|a| match a.get_info().device_type {
                    wgpu::DeviceType::DiscreteGpu => 3,
                    wgpu::DeviceType::IntegratedGpu => 2,
                    _ => 1,
                })
        })
    };
    let (adapter, software) = match hardware {
        Some(adapter) => (adapter, false),
        None => (
            pollster::block_on(instance.request_adapter(&wgpu::RequestAdapterOptions {
                power_preference: wgpu::PowerPreference::LowPower,
                compatible_surface: None,
                force_fallback_adapter: true,
            }))
            .ok_or_else(|| anyhow::anyhow!("no GPU adapter"))?,
            true,
        ),
    };
    let info = adapter.get_info();
    let supported = adapter.limits();
    let limits = wgpu::Limits {
        max_storage_buffer_binding_size: supported.max_storage_buffer_binding_size,
        max_buffer_size: supported.max_buffer_size,
        max_storage_buffers_per_shader_stage: 9.min(supported.max_storage_buffers_per_shader_stage),
        ..wgpu::Limits::downlevel_defaults()
    };
    let (device, queue) = pollster::block_on(adapter.request_device(
        &wgpu::DeviceDescriptor { label: Some("bionz"), required_features: wgpu::Features::empty(), required_limits: limits.clone(), memory_hints: Default::default() },
        None,
    ))
    .map_err(|e| anyhow::anyhow!(e.to_string()))?;
    let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
        label: Some("bionz-stages"),
        source: wgpu::ShaderSource::Wgsl(SHADER.into()),
    });
    let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
        label: None,
        entries: &[
            entry(0, wgpu::BufferBindingType::Storage { read_only: true }),
            entry(1, wgpu::BufferBindingType::Storage { read_only: true }),
            entry(2, wgpu::BufferBindingType::Storage { read_only: true }),
            entry(3, wgpu::BufferBindingType::Storage { read_only: true }),
            entry(4, wgpu::BufferBindingType::Storage { read_only: true }),
            entry(5, wgpu::BufferBindingType::Storage { read_only: false }),
            entry(6, wgpu::BufferBindingType::Storage { read_only: false }),
            entry(7, wgpu::BufferBindingType::Storage { read_only: true }),
        ],
    });
    let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
        label: None,
        bind_group_layouts: &[&layout],
        push_constant_ranges: &[],
    });
    let pipeline = |entry_point: &str| {
        device.create_compute_pipeline(&wgpu::ComputePipelineDescriptor {
            label: Some(entry_point),
            layout: Some(&pipeline_layout),
            module: &shader,
            entry_point: Some(entry_point),
            compilation_options: Default::default(),
            cache: None,
        })
    };
    let cache_rgb = pipeline("cache_rgb");
    let finish_cached = pipeline("finish_cached");
    let display_rgba = pipeline("display_rgba");
    let label = format!("{} ({:?}){}", info.name, info.backend, if software { ", software" } else { "" });
    Ok(GpuContext {
        device,
        queue,
        layout,
        cache_rgb,
        finish_cached,
        display_rgba,
        info: label,
        max_binding: limits.max_storage_buffer_binding_size as u64,
    })
}

/// Largest pixel count one dispatch can hold (8 output words per pixel).
pub fn max_pixels_per_dispatch() -> anyhow::Result<usize> {
    let ctx = context()?;
    Ok(((ctx.max_binding / 32) as usize).min(4 << 20).max(1024))
}

/// Run a full image in row bands so large frames fit the storage-buffer limit. Each band carries a
/// one-row halo on both sides so the 3x3 detail stage sees the same neighbours as a single dispatch.
/// The result is identical to `integer_pipeline` on the whole image.
pub fn run_gpu_banded(pixels: &[RgbI], width: i32, params: &IntegerParams) -> anyhow::Result<Vec<StageTap>> {
    let w = width.max(1) as usize;
    let h = pixels.len() / w;
    if pixels.len() != w * h {
        anyhow::bail!("pixel count is not a whole number of rows");
    }
    let rows_per_band = (max_pixels_per_dispatch()? / w).saturating_sub(2).max(1);
    if rows_per_band >= h {
        return run_gpu(pixels, width, params);
    }
    let mut out = Vec::with_capacity(pixels.len());
    let mut y0 = 0usize;
    while y0 < h {
        let y1 = (y0 + rows_per_band).min(h);
        let top = y0.saturating_sub(1);
        let bottom = (y1 + 1).min(h);
        let taps = run_gpu(&pixels[top * w..bottom * w], width, params)?;
        let skip = (y0 - top) * w;
        out.extend_from_slice(&taps[skip..skip + (y1 - y0) * w]);
        y0 = y1;
    }
    Ok(out)
}

fn gpu_params_of(width: i32, params: &IntegerParams) -> GpuParams {
    GpuParams {
        width,
        gamma_max: params.gamma_max,
        matrix_q: params.matrix_q,
        lut_level: params.lut_level,
        gain_enable: i32::from(params.gain_enable),
        flags: flags_of(&params.bypass),
        legal: i32::from(params.legal_range),
        fc_before_ec: i32::from(params.fc_before_ec),
        ec_full_scale: params.ec_full_scale,
        base: params.base_matrix,
        color: params.color_matrix,
        detail: params.detail,
        gain_level: params.gain_level,
        pad: [0; 3],
    }
}

/// View path: the full integer chain, then the 8-bit code decoded to RGBA8 on the GPU. Reads back
/// 4 bytes per pixel instead of 32. Bands keep large frames inside the storage-buffer limit.
pub fn run_gpu_display(pixels: &[RgbI], width: i32, params: &IntegerParams) -> anyhow::Result<Vec<u8>> {
    let ctx = context()?;
    let w = width.max(1) as usize;
    let h = pixels.len() / w;
    if pixels.len() != w * h {
        anyhow::bail!("pixel count is not a whole number of rows");
    }
    let max_px = ((ctx.max_binding / 12) as usize).min(8 << 20).max(1024);
    let rows_per_band = (max_px / w).saturating_sub(2).max(1);
    let mut out = Vec::with_capacity(pixels.len() * 4);
    let mut y0 = 0usize;
    while y0 < h {
        let y1 = (y0 + rows_per_band).min(h);
        let top = y0.saturating_sub(1);
        let bottom = (y1 + 1).min(h);
        let band = display_once(ctx, &pixels[top * w..bottom * w], width, params)?;
        let skip = (y0 - top) * w * 4;
        out.extend_from_slice(&band[skip..skip + (y1 - y0) * w * 4]);
        y0 = y1;
    }
    Ok(out)
}

fn display_once(ctx: &GpuContext, pixels: &[RgbI], width: i32, params: &IntegerParams) -> anyhow::Result<Vec<u8>> {
    let device = &ctx.device;
    let gpu_params = gpu_params_of(width, params);
    let input: Vec<i32> = pixels.iter().flat_map(|p| [p.r, p.g, p.b]).collect();
    let delta: Vec<i32> = params.delta.iter().map(|v| *v as i32).collect();
    let bind = |data: &[u8], usage: wgpu::BufferUsages| {
        device.create_buffer_init(&wgpu::util::BufferInitDescriptor { label: None, contents: data, usage })
    };
    let params_buf = bind(bytemuck::bytes_of(&gpu_params), wgpu::BufferUsages::STORAGE);
    let input_buf = bind(bytemuck::cast_slice(&input), wgpu::BufferUsages::STORAGE);
    let ec_buf = bind(bytemuck::cast_slice(&params.ec), wgpu::BufferUsages::STORAGE);
    let fc_buf = bind(bytemuck::cast_slice(&params.fc), wgpu::BufferUsages::STORAGE);
    let delta_buf = bind(bytemuck::cast_slice(&delta), wgpu::BufferUsages::STORAGE);
    let knots_buf = bind(bytemuck::cast_slice(&params.ec_knots), wgpu::BufferUsages::STORAGE);
    let size = (pixels.len() * 4) as u64;
    let output_buf = device.create_buffer(&wgpu::BufferDescriptor {
        label: None,
        size,
        usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_SRC,
        mapped_at_creation: false,
    });
    let cache_buf = device.create_buffer(&wgpu::BufferDescriptor {
        label: None,
        size: size * 3,
        usage: wgpu::BufferUsages::STORAGE,
        mapped_at_creation: false,
    });
    let read_buf = device.create_buffer(&wgpu::BufferDescriptor {
        label: None,
        size,
        usage: wgpu::BufferUsages::MAP_READ | wgpu::BufferUsages::COPY_DST,
        mapped_at_creation: false,
    });
    let group = device.create_bind_group(&wgpu::BindGroupDescriptor {
        label: None,
        layout: &ctx.layout,
        entries: &[
            wgpu::BindGroupEntry { binding: 0, resource: params_buf.as_entire_binding() },
            wgpu::BindGroupEntry { binding: 1, resource: input_buf.as_entire_binding() },
            wgpu::BindGroupEntry { binding: 2, resource: ec_buf.as_entire_binding() },
            wgpu::BindGroupEntry { binding: 3, resource: fc_buf.as_entire_binding() },
            wgpu::BindGroupEntry { binding: 4, resource: delta_buf.as_entire_binding() },
            wgpu::BindGroupEntry { binding: 5, resource: output_buf.as_entire_binding() },
            wgpu::BindGroupEntry { binding: 6, resource: cache_buf.as_entire_binding() },
            wgpu::BindGroupEntry { binding: 7, resource: knots_buf.as_entire_binding() },
        ],
    });
    let groups = pixels.len().div_ceil(64) as u32;
    let (gx, gy) = if groups > 65535 { (65535, groups.div_ceil(65535)) } else { (groups, 1) };
    let mut encoder = device.create_command_encoder(&Default::default());
    for pipeline in [&ctx.cache_rgb, &ctx.display_rgba] {
        let mut pass = encoder.begin_compute_pass(&Default::default());
        pass.set_pipeline(pipeline);
        pass.set_bind_group(0, &group, &[]);
        pass.dispatch_workgroups(gx, gy, 1);
    }
    encoder.copy_buffer_to_buffer(&output_buf, 0, &read_buf, 0, size);
    ctx.queue.submit(Some(encoder.finish()));
    let slice = read_buf.slice(..);
    let (tx, rx) = std::sync::mpsc::channel();
    slice.map_async(wgpu::MapMode::Read, move |result| {
        let _ = tx.send(result);
    });
    device.poll(wgpu::Maintain::Wait);
    rx.recv()?.map_err(|err| anyhow::anyhow!(err.to_string()))?;
    let bytes = slice.get_mapped_range().to_vec();
    read_buf.unmap();
    Ok(bytes)
}

fn run_gpu_seeded(pixels: &[RgbI], width: i32, params: &IntegerParams, seed: Option<&[i32]>) -> anyhow::Result<(Vec<StageTap>, Vec<i32>, f64, f64)> {
    let ctx = context()?;
    let device = &ctx.device;
    let queue = &ctx.queue;
    if pixels.is_empty() {
        return Ok((Vec::new(), Vec::new(), 0.0, 0.0));
    }
    let gpu_params = GpuParams {
        width,
        gamma_max: params.gamma_max,
        matrix_q: params.matrix_q,
        lut_level: params.lut_level,
        gain_enable: i32::from(params.gain_enable),
        flags: flags_of(&params.bypass),
        legal: i32::from(params.legal_range),
        fc_before_ec: i32::from(params.fc_before_ec),
        ec_full_scale: params.ec_full_scale,
        base: params.base_matrix,
        color: params.color_matrix,
        detail: params.detail,
        gain_level: params.gain_level,
        pad: [0; 3],
    };
    let mut input = Vec::with_capacity(pixels.len() * 3);
    for px in pixels {
        input.extend([px.r, px.g, px.b]);
    }
    let delta: Vec<i32> = params.delta.iter().map(|v| *v as i32).collect();
    let cache = match seed {
        Some(words) if words.len() == pixels.len() * 3 => words.to_vec(),
        Some(_) => anyhow::bail!("post-gain cache length does not match the pixel count"),
        None => vec![0i32; pixels.len() * 3],
    };
    let bind = |data: &[u8], usage: wgpu::BufferUsages| {
        device.create_buffer_init(&wgpu::util::BufferInitDescriptor { label: None, contents: data, usage })
    };
    let output_size = (pixels.len() * 8 * 4) as u64;
    if output_size > ctx.max_binding {
        anyhow::bail!("{} pixels exceed one dispatch; use run_gpu_banded", pixels.len());
    }
    let params_buf = bind(bytemuck::bytes_of(&gpu_params), wgpu::BufferUsages::STORAGE);
    let input_buf = bind(bytemuck::cast_slice(&input), wgpu::BufferUsages::STORAGE);
    let ec_buf = bind(bytemuck::cast_slice(&params.ec), wgpu::BufferUsages::STORAGE);
    let fc_buf = bind(bytemuck::cast_slice(&params.fc), wgpu::BufferUsages::STORAGE);
    let delta_buf = bind(bytemuck::cast_slice(&delta), wgpu::BufferUsages::STORAGE);
    let knots_buf = bind(bytemuck::cast_slice(&params.ec_knots), wgpu::BufferUsages::STORAGE);
    let output_buf = device.create_buffer(&wgpu::BufferDescriptor {
        label: None,
        size: output_size,
        usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_SRC,
        mapped_at_creation: false,
    });
    let cache_buf = bind(bytemuck::cast_slice(&cache), wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_SRC);
    let read_buf = device.create_buffer(&wgpu::BufferDescriptor {
        label: None,
        size: output_size,
        usage: wgpu::BufferUsages::MAP_READ | wgpu::BufferUsages::COPY_DST,
        mapped_at_creation: false,
    });
    let cache_read = device.create_buffer(&wgpu::BufferDescriptor {
        label: None,
        size: cache_buf.size(),
        usage: wgpu::BufferUsages::MAP_READ | wgpu::BufferUsages::COPY_DST,
        mapped_at_creation: false,
    });
    let group = device.create_bind_group(&wgpu::BindGroupDescriptor {
        label: None,
        layout: &ctx.layout,
        entries: &[
            wgpu::BindGroupEntry { binding: 0, resource: params_buf.as_entire_binding() },
            wgpu::BindGroupEntry { binding: 1, resource: input_buf.as_entire_binding() },
            wgpu::BindGroupEntry { binding: 2, resource: ec_buf.as_entire_binding() },
            wgpu::BindGroupEntry { binding: 3, resource: fc_buf.as_entire_binding() },
            wgpu::BindGroupEntry { binding: 4, resource: delta_buf.as_entire_binding() },
            wgpu::BindGroupEntry { binding: 5, resource: output_buf.as_entire_binding() },
            wgpu::BindGroupEntry { binding: 6, resource: cache_buf.as_entire_binding() },
            wgpu::BindGroupEntry { binding: 7, resource: knots_buf.as_entire_binding() },
        ],
    });
    let groups = pixels.len().div_ceil(64) as u32;
    let (gx, gy) = if groups > 65535 { (65535, groups.div_ceil(65535)) } else { (groups, 1) };
    let develop_ms = if seed.is_none() {
        let mut encoder = device.create_command_encoder(&Default::default());
        {
            let mut pass = encoder.begin_compute_pass(&Default::default());
            pass.set_pipeline(&ctx.cache_rgb);
            pass.set_bind_group(0, &group, &[]);
            pass.dispatch_workgroups(gx, gy, 1);
        }
        let started = Instant::now();
        queue.submit(Some(encoder.finish()));
        device.poll(wgpu::Maintain::Wait);
        started.elapsed().as_secs_f64() * 1000.0
    } else {
        0.0
    };
    let mut encoder = device.create_command_encoder(&Default::default());
    {
        let mut pass = encoder.begin_compute_pass(&Default::default());
        pass.set_pipeline(&ctx.finish_cached);
        pass.set_bind_group(0, &group, &[]);
        pass.dispatch_workgroups(gx, gy, 1);
    }
    encoder.copy_buffer_to_buffer(&output_buf, 0, &read_buf, 0, read_buf.size());
    encoder.copy_buffer_to_buffer(&cache_buf, 0, &cache_read, 0, cache_read.size());
    let started = Instant::now();
    queue.submit(Some(encoder.finish()));
    device.poll(wgpu::Maintain::Wait);
    let finish_ms = started.elapsed().as_secs_f64() * 1000.0;
    let words = map_i32(device, &read_buf)?;
    let cached = map_i32(device, &cache_read)?;
    let taps = words
        .chunks(8)
        .map(|w| StageTap {
            rgb: RgbI { r: w[0], g: w[1], b: w[2] },
            ycc: bionz_core::YccI { y: w[3], cb: w[4], cr: w[5] },
            encoded_y: w[6] as u8,
            encoded_cb: (w[7] & 0xff) as u8,
            encoded_cr: ((w[7] >> 8) & 0xff) as u8,
        })
        .collect();
    Ok((taps, cached, develop_ms, finish_ms))
}

fn map_i32(device: &wgpu::Device, buffer: &wgpu::Buffer) -> anyhow::Result<Vec<i32>> {
    let slice = buffer.slice(..);
    let (tx, rx) = std::sync::mpsc::channel();
    slice.map_async(wgpu::MapMode::Read, move |result| tx.send(result).unwrap());
    device.poll(wgpu::Maintain::Wait);
    rx.recv().unwrap().map_err(|err| anyhow::anyhow!(err.to_string()))?;
    let data = slice.get_mapped_range();
    let words: Vec<i32> = bytemuck::cast_slice(&data).to_vec();
    drop(data);
    buffer.unmap();
    Ok(words)
}

fn entry(binding: u32, ty: wgpu::BufferBindingType) -> wgpu::BindGroupLayoutEntry {
    wgpu::BindGroupLayoutEntry {
        binding,
        visibility: wgpu::ShaderStages::COMPUTE,
        ty: wgpu::BindingType::Buffer { ty, has_dynamic_offset: false, min_binding_size: None },
        count: None,
    }
}

trait BufferInit {
    fn create_buffer_init(&self, desc: &wgpu::util::BufferInitDescriptor) -> wgpu::Buffer;
}

impl BufferInit for wgpu::Device {
    fn create_buffer_init(&self, desc: &wgpu::util::BufferInitDescriptor) -> wgpu::Buffer {
        wgpu::util::DeviceExt::create_buffer_init(self, desc)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bionz_core::integer_pipeline;

    #[test]
    fn cpu_and_gpu_match_on_rails() {
        let mut params = IntegerParams::default();
        params.delta[3] = 20;
        params.delta[4] = -10;
        params.delta[5] = 7;
        params.gain_enable = true;
        params.gain_level = 128;
        params.detail = [0, 0, 0, 32, 192, 32, 0, 0, 0];
        let pixels = [
            RgbI { r: 0, g: 0, b: 0 },
            RgbI { r: 4095, g: 4095, b: 4095 },
            RgbI { r: 2000, g: 1000, b: 500 },
            RgbI { r: 8191, g: 1, b: 0 },
        ];
        let cpu = integer_pipeline(&pixels, 4, &params);
        let gpu = run_gpu(&pixels, 4, &params).expect("software adapter");
        assert_eq!(cpu, gpu);
    }

    #[test]
    fn develop_cache_ignores_lut_level() {
        let mut low = IntegerParams::default();
        for node in 0..405 { low.delta[node * 3] = 40; }
        low.lut_level = 0;
        let mut high = low.clone();
        high.lut_level = 7;
        let pixels = [RgbI { r: 1000, g: 2000, b: 3000 }, RgbI { r: 4095, g: 100, b: 100 }];
        let (low_taps, low_cache) = run_gpu_parts(&pixels, 2, &low).expect("software adapter");
        let (high_taps, high_cache) = run_gpu_parts(&pixels, 2, &high).expect("software adapter");
        assert_eq!(low_cache, high_cache);
        assert_ne!(low_taps, high_taps);
        assert_eq!(low_taps, integer_pipeline(&pixels, 2, &low));
        assert_eq!(high_taps, integer_pipeline(&pixels, 2, &high));
    }

    #[test]
    fn compiled_classic_chrome_matches_cpu() {
        let path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../mods/film_sims/presets/classic-chrome.bzlook");
        let look = bionz_preset::load_bzlook(&path).unwrap();
        let table = &look.compiled.iter().find(|(key, _)| key == "a6400").unwrap().1;
        let params = bionz_preset::integer_params_from_compiled(table);
        let pixels = [
            RgbI { r: 0, g: 0, b: 0 },
            RgbI { r: 1000, g: 2000, b: 3000 },
            RgbI { r: 4095, g: 4095, b: 4095 },
            RgbI { r: 512, g: 2048, b: 1024 },
        ];
        let cpu = integer_pipeline(&pixels, 2, &params);
        let gpu = run_gpu(&pixels, 2, &params).expect("software adapter");
        assert_eq!(cpu, gpu);
    }

    #[test]
    fn lut_edit_reruns_only_the_cached_tail() {
        let mut low = IntegerParams::default();
        for node in 0..405 { low.delta[node * 3] = 40; }
        low.lut_level = 0;
        let pixels = [RgbI { r: 1000, g: 2000, b: 3000 }, RgbI { r: 4095, g: 100, b: 100 }];
        let (_, cache) = run_gpu_parts(&pixels, 2, &low).expect("software adapter");
        let mut high = low.clone();
        high.lut_level = 7;
        let (taps, kept) = rerun_after_gain(&pixels, 2, &high, &cache).expect("software adapter");
        assert_eq!(kept, cache);
        assert_eq!(taps, integer_pipeline(&pixels, 2, &high));
        assert_ne!(taps, integer_pipeline(&pixels, 2, &low));
    }

    #[test]
    fn display_path_matches_cpu_decode() {
        let path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../mods/film_sims/presets/classic-chrome.bzlook");
        let look = bionz_preset::load_bzlook(&path).unwrap();
        let params = bionz_preset::integer_params_from_compiled(&look.compiled[0].1);
        let pixels: Vec<RgbI> = (0..96 * 64).map(|i| RgbI { r: (i * 7) % 4096, g: (i * 13) % 4096, b: (i * 29) % 4096 }).collect();
        let rgba = run_gpu_display(&pixels, 96, &params).unwrap();
        let cpu = integer_pipeline(&pixels, 96, &params);
        for (i, t) in cpu.iter().enumerate() {
            let want = bionz_core::encoded_to_rgb8(t.encoded_y, t.encoded_cb, t.encoded_cr, params.legal_range);
            for c in 0..3 {
                assert!((rgba[i * 4 + c] as i32 - want[c] as i32).abs() <= 1, "pixel {i}");
            }
            assert_eq!(rgba[i * 4 + 3], 255);
        }
    }

    #[test]
    fn pass_times_keep_cpu_parity() {
        let params = IntegerParams::default();
        let pixels = [RgbI { r: 10, g: 20, b: 30 }, RgbI { r: 4000, g: 10, b: 10 }];
        let (taps, times) = run_gpu_times(&pixels, 2, &params).expect("software adapter");
        assert_eq!(taps, integer_pipeline(&pixels, 2, &params));
        assert!(times.develop_ms.is_finite() && times.finish_ms.is_finite());
    }
}
