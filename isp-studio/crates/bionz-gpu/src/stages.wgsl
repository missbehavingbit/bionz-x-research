struct Params {
    width: i32,
    gamma_max: i32,
    matrix_q: i32,
    lut_level: i32,
    gain_enable: i32,
    flags: u32,
    legal: i32,
    fc_before_ec: i32,
    ec_full_scale: i32,
    base0: i32, base1: i32, base2: i32,
    base3: i32, base4: i32, base5: i32,
    base6: i32, base7: i32, base8: i32,
    col0: i32, col1: i32, col2: i32,
    col3: i32, col4: i32, col5: i32,
    col6: i32, col7: i32, col8: i32,
    d0: i32, d1: i32, d2: i32,
    d3: i32, d4: i32, d5: i32,
    d6: i32, d7: i32, d8: i32,
    gain_level: i32, pad1: i32, pad2: i32, pad3: i32,
}

@group(0) @binding(0) var<storage, read> params: Params;
@group(0) @binding(1) var<storage, read> input_px: array<i32>;
@group(0) @binding(2) var<storage, read> ec: array<i32>;
@group(0) @binding(3) var<storage, read> fc: array<i32>;
@group(0) @binding(4) var<storage, read> delta: array<i32>;
@group(0) @binding(5) var<storage, read_write> output_px: array<i32>;
@group(0) @binding(6) var<storage, read_write> cache_px: array<i32>;
@group(0) @binding(7) var<storage, read> ec_knots: array<i32>;

fn bypass(bit: u32) -> bool {
    return (params.flags & (1u << bit)) != 0u;
}

fn q10(r: i32, g: i32, b: i32, m0: i32, m1: i32, m2: i32) -> i32 {
    let acc = r * m0 + g * m1 + b * m2;
    return clamp((acc + params.matrix_q / 2) / params.matrix_q, 0, params.gamma_max);
}

fn sample_curve(which: i32, code: i32, full_scale: i32) -> i32 {
    let span = 95;
    let code_c = clamp(code, 0, full_scale);
    let pos_num = code_c * span;
    let i0 = min(pos_num / max(full_scale, 1), 94);
    let rem = pos_num - i0 * full_scale;
    let frac = (rem << 16) / max(full_scale, 1);
    let quant = 65536;
    let low = select(ec[i0], fc[i0], which == 1);
    let high = select(ec[i0 + 1], fc[i0 + 1], which == 1);
    return (low * (quant - frac) + high * frac + quant / 2) / quant;
}

// Mirrors bionz_core::sample_knots (binary search, Q16 fraction, hold below the first knot).
fn sample_ec(code: i32) -> i32 {
    let x = max(code, 0);
    if (x <= ec_knots[0]) { return ec[0]; }
    if (x >= ec_knots[95]) { return ec[95]; }
    var lo = 0;
    var hi = 95;
    loop {
        if (hi - lo <= 1) { break; }
        let mid = (lo + hi) / 2;
        if (ec_knots[mid] <= x) { lo = mid; } else { hi = mid; }
    }
    let span = max(ec_knots[hi] - ec_knots[lo], 1);
    let frac = ((x - ec_knots[lo]) << 16) / span;
    let quant = 65536;
    return (ec[lo] * (quant - frac) + ec[hi] * frac + quant / 2) / quant;
}

fn sample_fc(code: i32) -> i32 {
    let maxv = max(params.gamma_max, 1);
    return sample_curve(1, code * 1023 / maxv, 1023) * maxv / 1023;
}

fn ycc_of(r: i32, g: i32, b: i32) -> vec3<i32> {
    let maxv = max(params.gamma_max, 1);
    let yn = (2126 * r + 7152 * g + 722 * b + 5000) / 10000;
    let y = clamp((yn * 256 + maxv / 2) / maxv, 0, 256);
    let cb = clamp(((b - yn) * 13796) / (maxv * 100), -128, 128);
    let cr = clamp(((r - yn) * 16256) / (maxv * 100), -128, 128);
    return vec3<i32>(y, cb, cr);
}

fn axis_frac(value: i32, nodes: array<i32, 9>, count: i32) -> vec2<i32> {
    if (value <= nodes[0]) { return vec2<i32>(0, 0); }
    let last = nodes[count - 1];
    if (value >= last) { return vec2<i32>(count - 2, 65536); }
    var i = 0;
    loop {
        if (i + 1 >= count || value < nodes[i + 1]) { break; }
        i = i + 1;
    }
    let span = max(nodes[i + 1] - nodes[i], 1);
    return vec2<i32>(i, clamp(((value - nodes[i]) << 16) / span, 0, 65536));
}

fn sample_lut(y: i32, cb: i32, cr: i32) -> vec3<i32> {
    var y_nodes: array<i32, 9> = array<i32, 9>(0, 64, 128, 192, 256, 0, 0, 0, 0);
    var c_nodes: array<i32, 9> = array<i32, 9>(-128, -96, -64, -32, 0, 32, 64, 96, 128);
    let iyf = axis_frac(y, y_nodes, 5);
    let ibf = axis_frac(cb, c_nodes, 9);
    let irf = axis_frac(cr, c_nodes, 9);
    var acc = vec3<i32>(0, 0, 0);
    for (var dz = 0; dz < 2; dz++) {
        for (var dy = 0; dy < 2; dy++) {
            for (var dx = 0; dx < 2; dx++) {
                let wy = select(16 - (iyf.y >> 12), iyf.y >> 12, dx == 1);
                let wb = select(16 - (ibf.y >> 12), ibf.y >> 12, dy == 1);
                let wr = select(16 - (irf.y >> 12), irf.y >> 12, dz == 1);
                let w = wy * wb * wr;
                let idx = ((iyf.x + dx) * 9 + (ibf.x + dy)) * 9 + (irf.x + dz);
                let level = clamp(params.lut_level, 0, 7);
                let d0 = (level * delta[idx * 3]) >> 3;
                let d1 = (level * delta[idx * 3 + 1]) >> 3;
                let d2 = (level * delta[idx * 3 + 2]) >> 3;
                acc = acc + vec3<i32>(w * d0, w * d1, w * d2);
            }
        }
    }
    return (acc + vec3<i32>(2048, 2048, 2048)) / vec3<i32>(4096, 4096, 4096);
}

fn develop(r_in: i32, g_in: i32, b_in: i32) -> vec3<i32> {
    var r = r_in;
    var g = g_in;
    var b = b_in;
    if (!bypass(5u)) {
        let rr = q10(r, g, b, params.base0, params.base1, params.base2);
        let gg = q10(r, g, b, params.base3, params.base4, params.base5);
        let bb = q10(r, g, b, params.base6, params.base7, params.base8);
        r = rr; g = gg; b = bb;
    }
    let scale = max(params.ec_full_scale, 1);
    let maxv = max(params.gamma_max, 1);
    if (params.fc_before_ec != 0) {
        if (!bypass(7u)) {
            r = sample_fc(r);
            g = sample_fc(g);
            b = sample_fc(b);
        }
        if (!bypass(6u)) {
            r = sample_ec(r * scale / maxv);
            g = sample_ec(g * scale / maxv);
            b = sample_ec(b * scale / maxv);
        }
    } else {
        if (!bypass(6u)) {
            r = sample_ec(r * scale / maxv);
            g = sample_ec(g * scale / maxv);
            b = sample_ec(b * scale / maxv);
        }
        if (!bypass(7u)) {
            r = sample_fc(r);
            g = sample_fc(g);
            b = sample_fc(b);
        }
    }
    if (!bypass(8u)) {
        let rr = q10(r, g, b, params.col0, params.col1, params.col2);
        let gg = q10(r, g, b, params.col3, params.col4, params.col5);
        let bb = q10(r, g, b, params.col6, params.col7, params.col8);
        r = rr; g = gg; b = bb;
    }
    if (!bypass(9u) && params.gain_enable != 0) {
        var gain = 256;
        if (params.gain_level <= 4094) {
            gain = clamp(0xFFF00 / max(4095 - params.gain_level, 1), 256, 511);
        }
        r = clamp(r * gain / 256, 0, params.gamma_max);
        g = clamp(g * gain / 256, 0, params.gamma_max);
        b = clamp(b * gain / 256, 0, params.gamma_max);
    }
    return vec3<i32>(r, g, b);
}

@compute @workgroup_size(64)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
    let i = gid.x + gid.y * (65535u * 64u);
    let count = arrayLength(&output_px) / 8u;
    if (i >= count) { return; }
    var rgb = develop(input_px[i * 3u], input_px[i * 3u + 1u], input_px[i * 3u + 2u]);
    if (!bypass(12u)) {
        let w = max(params.width, 1);
        let h = max(i32(count) / w, 1);
        let x = i32(i) % w;
        let y = i32(i) / w;
        var acc = vec3<i32>(0, 0, 0);
        let kernel = array<i32, 9>(params.d0, params.d1, params.d2, params.d3, params.d4, params.d5, params.d6, params.d7, params.d8);
        for (var ky = 0; ky < 3; ky++) {
            for (var kx = 0; kx < 3; kx++) {
                let sx = clamp(x + kx - 1, 0, w - 1);
                let sy = clamp(y + ky - 1, 0, h - 1);
                let ni = u32(sy * w + sx);
                let p = develop(input_px[ni * 3u], input_px[ni * 3u + 1u], input_px[ni * 3u + 2u]);
                let c = kernel[ky * 3 + kx];
                acc = acc + p * c;
            }
        }
        rgb = clamp(acc / vec3<i32>(256, 256, 256), vec3<i32>(0, 0, 0), vec3<i32>(4095, 4095, 4095));
    }
    let r = rgb.x;
    let g = rgb.y;
    let b = rgb.z;
    var ycc = vec3<i32>(0, 0, 0);
    if (!bypass(10u)) {
        ycc = ycc_of(r, g, b);
    }
    if (!bypass(11u)) {
        let d = sample_lut(ycc.x, ycc.y, ycc.z);
        ycc = vec3<i32>(clamp(ycc.x + d.x, 0, 256), clamp(ycc.y + d.y, -128, 128), clamp(ycc.z + d.z, -128, 128));
    }
    var ey = 0;
    var ecb = 128;
    var ecr = 128;
    if (!bypass(13u)) {
        let yg = select(255, 219, params.legal != 0);
        let cg = select(255, 224, params.legal != 0);
        let off = select(0, 16, params.legal != 0);
        let y_lo = select(0, 16, params.legal != 0);
        let y_hi = select(255, 235, params.legal != 0);
        let c_lo = select(0, 16, params.legal != 0);
        let c_hi = select(255, 240, params.legal != 0);
        ey = clamp((ycc.x * yg + 128) / 256 + off, y_lo, y_hi);
        ecb = clamp((ycc.y * cg + 128) / 256 + 128, c_lo, c_hi);
        ecr = clamp((ycc.z * cg + 128) / 256 + 128, c_lo, c_hi);
    }
    output_px[i * 8u] = r;
    output_px[i * 8u + 1u] = g;
    output_px[i * 8u + 2u] = b;
    output_px[i * 8u + 3u] = ycc.x;
    output_px[i * 8u + 4u] = ycc.y;
    output_px[i * 8u + 5u] = ycc.z;
    output_px[i * 8u + 6u] = ey;
    output_px[i * 8u + 7u] = ecb | (ecr << 8);
}

@compute @workgroup_size(64)
fn cache_rgb(@builtin(global_invocation_id) gid: vec3<u32>) {
    let i = gid.x + gid.y * (65535u * 64u);
    let count = arrayLength(&cache_px) / 3u;
    if (i >= count) { return; }
    let rgb = develop(input_px[i * 3u], input_px[i * 3u + 1u], input_px[i * 3u + 2u]);
    cache_px[i * 3u] = rgb.x;
    cache_px[i * 3u + 1u] = rgb.y;
    cache_px[i * 3u + 2u] = rgb.z;
}

struct Finished {
    rgb: vec3<i32>,
    ycc: vec3<i32>,
    enc: vec3<i32>,
}

// Stages 13 (detail), 11 (YCbCr), 12 (residual LUT) and 14 (encoder) from the post-gain cache.
fn finish_at(i: u32, count: u32) -> Finished {
    var rgb = vec3<i32>(cache_px[i * 3u], cache_px[i * 3u + 1u], cache_px[i * 3u + 2u]);
    if (!bypass(12u)) {
        let w = max(params.width, 1);
        let h = max(i32(count) / w, 1);
        let x = i32(i) % w;
        let y = i32(i) / w;
        var acc = vec3<i32>(0, 0, 0);
        let kernel = array<i32, 9>(params.d0, params.d1, params.d2, params.d3, params.d4, params.d5, params.d6, params.d7, params.d8);
        for (var ky = 0; ky < 3; ky++) {
            for (var kx = 0; kx < 3; kx++) {
                let sx = clamp(x + kx - 1, 0, w - 1);
                let sy = clamp(y + ky - 1, 0, h - 1);
                let ni = u32(sy * w + sx);
                let p = vec3<i32>(cache_px[ni * 3u], cache_px[ni * 3u + 1u], cache_px[ni * 3u + 2u]);
                let c = kernel[ky * 3 + kx];
                acc = acc + p * c;
            }
        }
        rgb = clamp(acc / vec3<i32>(256, 256, 256), vec3<i32>(0, 0, 0), vec3<i32>(4095, 4095, 4095));
    }
    var ycc = vec3<i32>(0, 0, 0);
    if (!bypass(10u)) {
        ycc = ycc_of(rgb.x, rgb.y, rgb.z);
    }
    if (!bypass(11u)) {
        let d = sample_lut(ycc.x, ycc.y, ycc.z);
        ycc = vec3<i32>(clamp(ycc.x + d.x, 0, 256), clamp(ycc.y + d.y, -128, 128), clamp(ycc.z + d.z, -128, 128));
    }
    var enc = vec3<i32>(0, 128, 128);
    if (!bypass(13u)) {
        let yg = select(255, 219, params.legal != 0);
        let cg = select(255, 224, params.legal != 0);
        let off = select(0, 16, params.legal != 0);
        let y_lo = select(0, 16, params.legal != 0);
        let y_hi = select(255, 235, params.legal != 0);
        let c_lo = select(0, 16, params.legal != 0);
        let c_hi = select(255, 240, params.legal != 0);
        enc = vec3<i32>(
            clamp((ycc.x * yg + 128) / 256 + off, y_lo, y_hi),
            clamp((ycc.y * cg + 128) / 256 + 128, c_lo, c_hi),
            clamp((ycc.z * cg + 128) / 256 + 128, c_lo, c_hi),
        );
    }
    return Finished(rgb, ycc, enc);
}

@compute @workgroup_size(64)
fn finish_cached(@builtin(global_invocation_id) gid: vec3<u32>) {
    let i = gid.x + gid.y * (65535u * 64u);
    let count = arrayLength(&output_px) / 8u;
    if (i >= count) { return; }
    let f = finish_at(i, count);
    output_px[i * 8u] = f.rgb.x;
    output_px[i * 8u + 1u] = f.rgb.y;
    output_px[i * 8u + 2u] = f.rgb.z;
    output_px[i * 8u + 3u] = f.ycc.x;
    output_px[i * 8u + 4u] = f.ycc.y;
    output_px[i * 8u + 5u] = f.ycc.z;
    output_px[i * 8u + 6u] = f.enc.x;
    output_px[i * 8u + 7u] = f.enc.y | (f.enc.z << 8);
}

// View output: the same integer chain, then the 8-bit code decoded to RGBA8 for display only
// (mirrors bionz_core::encoded_to_rgb8). One word per pixel instead of eight.
@compute @workgroup_size(64)
fn display_rgba(@builtin(global_invocation_id) gid: vec3<u32>) {
    let i = gid.x + gid.y * (65535u * 64u);
    let count = arrayLength(&cache_px) / 3u;
    if (i >= count) { return; }
    let f = finish_at(i, count);
    var yn: f32;
    var cbn: f32;
    var crn: f32;
    if (params.legal != 0) {
        yn = (f32(f.enc.x) - 16.0) / 219.0;
        cbn = (f32(f.enc.y) - 128.0) / 224.0;
        crn = (f32(f.enc.z) - 128.0) / 224.0;
    } else {
        yn = f32(f.enc.x) / 255.0;
        cbn = (f32(f.enc.y) - 128.0) / 255.0;
        crn = (f32(f.enc.z) - 128.0) / 255.0;
    }
    let r = yn + 1.5748 * crn;
    let b = yn + 1.8556 * cbn;
    let g = (yn - 0.2126 * r - 0.0722 * b) / 0.7152;
    let c = vec3<u32>(clamp(vec3<f32>(r, g, b), vec3<f32>(0.0), vec3<f32>(1.0)) * 255.0 + vec3<f32>(0.5));
    output_px[i] = bitcast<i32>(c.x | (c.y << 8u) | (c.z << 16u) | (255u << 24u));
}
