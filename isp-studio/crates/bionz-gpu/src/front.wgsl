// Spatial front end. Mirrors bionz_core::spatial (CPU reference). f32 throughout: these stages
// precede the integer chain. Coordinates in a band are offset by y_off into the full frame so zone
// selection and the CFA phase match a single full-frame dispatch.

struct FrontParams {
    w: u32,
    h: u32,
    y_off: u32,
    full_w: u32,
    full_h: u32,
    p0: u32, p1: u32, p2: u32, p3: u32,
    mode: u32,          // 0 directional, 1 bilinear
    zone_bilinear: u32,
    has_dcv: u32,
    gain: f32,
    knee: f32,
    crispening: f32,
    pad1: u32,
}

@group(0) @binding(0) var<storage, read> fp: FrontParams;
@group(0) @binding(1) var<storage, read> src: array<f32>;
@group(0) @binding(2) var<storage, read> coef: array<f32>;
@group(0) @binding(3) var<storage, read_write> dst: array<f32>;
@group(0) @binding(4) var<storage, read_write> aux: array<f32>;

fn idx(gid: vec3<u32>) -> u32 {
    return gid.x + gid.y * (65535u * 64u);
}

// Detail crispening soft-threshold: identity above 1.5t, suppressed below t/2, smoothstep between.
// Mirrors bionz_core::spatial::crispening_soft_threshold.
fn soften(x: f32, t: f32) -> f32 {
    if (t <= 0.0) { return x; }
    let a = abs(x);
    let lo = t * 0.5;
    let hi = t * 1.5;
    if (a <= lo) { return 0.0; }
    if (a >= hi) { return x; }
    let u = (a - lo) / (hi - lo);
    return x * u * u * (3.0 - 2.0 * u);
}

fn color(x: i32, gy: i32) -> u32 {
    let k = u32(gy & 1) * 2u + u32(x & 1);
    var c = fp.p0;
    if (k == 1u) { c = fp.p1; }
    if (k == 2u) { c = fp.p2; }
    if (k == 3u) { c = fp.p3; }
    return c;
}

fn cfa_src(x: i32, y: i32) -> f32 {
    let xx = clamp(x, 0, i32(fp.w) - 1);
    let yy = clamp(y, 0, i32(fp.h) - 1);
    return src[u32(yy) * fp.w + u32(xx)];
}

// The band's CFA after DCV is in `aux` for the demosaic passes.
fn cfa(x: i32, y: i32) -> f32 {
    let xx = clamp(x, 0, i32(fp.w) - 1);
    let yy = clamp(y, 0, i32(fp.h) - 1);
    return aux[u32(yy) * fp.w + u32(xx)];
}

struct ZoneW {
    z: vec4<u32>,
    w: vec4<f32>,
}

fn zones(x: u32, gy: u32) -> ZoneW {
    var r: ZoneW;
    if (fp.zone_bilinear == 0u) {
        let zx = min(x * 9u / max(fp.full_w, 1u), 8u);
        let zy = min(gy * 7u / max(fp.full_h, 1u), 6u);
        r.z = vec4<u32>(zy * 9u + zx, 0u, 0u, 0u);
        r.w = vec4<f32>(1.0, 0.0, 0.0, 0.0);
        return r;
    }
    let fx = clamp((f32(x) + 0.5) / f32(fp.full_w) * 9.0 - 0.5, 0.0, 8.0);
    let fy = clamp((f32(gy) + 0.5) / f32(fp.full_h) * 7.0 - 0.5, 0.0, 6.0);
    let x0 = u32(floor(fx));
    let y0 = u32(floor(fy));
    let x1 = min(x0 + 1u, 8u);
    let y1 = min(y0 + 1u, 6u);
    let tx = fx - f32(x0);
    let ty = fy - f32(y0);
    r.z = vec4<u32>(y0 * 9u + x0, y0 * 9u + x1, y1 * 9u + x0, y1 * 9u + x1);
    r.w = vec4<f32>((1.0 - tx) * (1.0 - ty), tx * (1.0 - ty), (1.0 - tx) * ty, tx * ty);
    return r;
}

// Pass 1: Bayer-domain DCV. src = CFA band, aux = CFA after DCV.
@compute @workgroup_size(64)
fn dcv_bayer(@builtin(global_invocation_id) gid: vec3<u32>) {
    let i = idx(gid);
    if (i >= fp.w * fp.h) { return; }
    let x = i32(i % fp.w);
    let y = i32(i / fp.w);
    let gy = y + i32(fp.y_off);
    let v = src[i];
    if (fp.has_dcv == 0u) {
        aux[i] = v;
        return;
    }
    let c = color(x, gy);
    var g = 2u;
    if (c == 1u) { g = 0u; }
    if (c == 0u) { g = 1u; }
    let zw = zones(u32(x), u32(gy));
    var hp = 0.0;
    for (var n = 0u; n < 4u; n++) {
        let wgt = zw.w[n];
        if (wgt == 0.0) { continue; }
        let base = (zw.z[n] * 3u + g) * 41u;
        var acc = 0.0;
        for (var j = 0; j < 5; j++) {
            for (var k = 0; k < 5; k++) {
                acc += coef[base + u32(j * 5 + k)] * cfa_src(x + 2 * k - 4, y + 2 * j - 4);
            }
        }
        if (g == 0u) {
            for (var j = 0; j < 4; j++) {
                for (var k = 0; k < 4; k++) {
                    acc += coef[base + 25u + u32(j * 4 + k)] * cfa_src(x + 2 * k - 3, y + 2 * j - 3);
                }
            }
        }
        hp += wgt * acc;
    }
    aux[i] = max(v + fp.gain * hp, 0.0);
}

fn green_directional(x: i32, y: i32) -> f32 {
    let c0 = cfa(x, y);
    var gh = 0.0;
    var gv = 0.0;
    for (var k = -2; k <= 2; k++) {
        var wgt = 1.0;
        if (k == 0) { wgt = 2.0; }
        let lh = 0.5 * abs(2.0 * cfa(x, y + k) - cfa(x - 2, y + k) - cfa(x + 2, y + k));
        let lv = 0.5 * abs(2.0 * cfa(x + k, y) - cfa(x + k, y - 2) - cfa(x + k, y + 2));
        gh += wgt * (abs(cfa(x - 1, y + k) - cfa(x + 1, y + k)) + lh);
        gv += wgt * (abs(cfa(x + k, y - 1) - cfa(x + k, y + 1)) + lv);
    }
    let avg_h = 0.5 * (cfa(x - 1, y) + cfa(x + 1, y));
    let avg_v = 0.5 * (cfa(x, y - 1) + cfa(x, y + 1));
    let sh = min(avg_h / fp.knee, 1.0);
    let sv = min(avg_v / fp.knee, 1.0);
    let est_h = avg_h + sh * 0.25 * (2.0 * c0 - cfa(x - 2, y) - cfa(x + 2, y));
    let est_v = avg_v + sv * 0.25 * (2.0 * c0 - cfa(x, y - 2) - cfa(x, y + 2));
    let eps = 1e-6;
    let wh = 1.0 / (gh * gh + eps);
    let wv = 1.0 / (gv * gv + eps);
    let n4min = min(min(cfa(x - 1, y), cfa(x + 1, y)), min(cfa(x, y - 1), cfa(x, y + 1)));
    let n4max = max(max(cfa(x - 1, y), cfa(x + 1, y)), max(cfa(x, y - 1), cfa(x, y + 1)));
    let lo = min(min(avg_h, avg_v), n4min);
    let hi = max(max(avg_h, avg_v), n4max);
    let gval = (wh * est_h + wv * est_v) / (wh + wv);
    let slack = 0.5 * (hi - lo);
    return clamp(gval, lo - slack, hi + slack);
}

// Pass 2: green everywhere. `dst` layout: green [0, wh), RGB [wh, 4wh), filtered RGB [4wh, 7wh).
@compute @workgroup_size(64)
fn demosaic_green(@builtin(global_invocation_id) gid: vec3<u32>) {
    let i = idx(gid);
    if (i >= fp.w * fp.h) { return; }
    let x = i32(i % fp.w);
    let y = i32(i / fp.w);
    let gy = y + i32(fp.y_off);
    var g = cfa(x, y);
    if (color(x, gy) != 1u) {
        if (fp.mode == 1u) {
            g = 0.25 * (cfa(x - 1, y) + cfa(x + 1, y) + cfa(x, y - 1) + cfa(x, y + 1));
        } else {
            g = green_directional(x, y);
        }
    }
    dst[i] = g;
}

fn gr(x: i32, y: i32) -> f32 {
    let xx = clamp(x, 0, i32(fp.w) - 1);
    let yy = clamp(y, 0, i32(fp.h) - 1);
    return dst[u32(yy) * fp.w + u32(xx)];
}

fn rgb_index(x: i32, y: i32) -> u32 {
    let xx = clamp(x, 0, i32(fp.w) - 1);
    let yy = clamp(y, 0, i32(fp.h) - 1);
    return fp.w * fp.h + (u32(yy) * fp.w + u32(xx)) * 3u;
}

fn cd(x: i32, y: i32) -> f32 {
    return cfa(x, y) - gr(x, y);
}

// Pass 3: native sample, green, and the opposite colour at R/B sites (better diagonal).
@compute @workgroup_size(64)
fn demosaic_opposite(@builtin(global_invocation_id) gid: vec3<u32>) {
    let i = idx(gid);
    if (i >= fp.w * fp.h) { return; }
    let x = i32(i % fp.w);
    let y = i32(i / fp.w);
    let c = color(x, y + i32(fp.y_off));
    let o = fp.w * fp.h + i * 3u;
    let gv = gr(x, y);
    dst[o + 1u] = gv;
    if (c == 1u) { return; }
    dst[o + c] = cfa(x, y);
    var v = 0.0;
    if (fp.mode == 1u) {
        v = gv + 0.25 * (cd(x - 1, y - 1) + cd(x + 1, y - 1) + cd(x - 1, y + 1) + cd(x + 1, y + 1));
    } else {
        let ne = 0.5 * (cd(x + 1, y - 1) + cd(x - 1, y + 1));
        let nw = 0.5 * (cd(x - 1, y - 1) + cd(x + 1, y + 1));
        let g_ne = abs(cd(x + 1, y - 1) - cd(x - 1, y + 1)) + abs(gr(x + 1, y - 1) - gr(x - 1, y + 1))
            + min(abs(cd(x + 2, y - 2) - cd(x, y)), abs(cd(x - 2, y + 2) - cd(x, y))) * 0.5;
        let g_nw = abs(cd(x - 1, y - 1) - cd(x + 1, y + 1)) + abs(gr(x - 1, y - 1) - gr(x + 1, y + 1))
            + min(abs(cd(x - 2, y - 2) - cd(x, y)), abs(cd(x + 2, y + 2) - cd(x, y))) * 0.5;
        let a = 1.0 / (g_ne * g_ne + 1e-6);
        let b = 1.0 / (g_nw * g_nw + 1e-6);
        v = gv + (a * ne + b * nw) / (a + b);
    }
    dst[o + (2u - c)] = v;
}

// Pass 4: R and B at green sites; the 4-neighbours are R/B sites completed by pass 3.
@compute @workgroup_size(64)
fn demosaic_gsites(@builtin(global_invocation_id) gid: vec3<u32>) {
    let i = idx(gid);
    if (i >= fp.w * fp.h) { return; }
    let x = i32(i % fp.w);
    let y = i32(i / fp.w);
    let gy = y + i32(fp.y_off);
    if (color(x, gy) != 1u) { return; }
    let o = fp.w * fp.h + i * 3u;
    let gv = gr(x, y);
    for (var t = 0u; t < 3u; t += 2u) {
        let dl = dst[rgb_index(x - 1, y) + t] - gr(x - 1, y);
        let dr = dst[rgb_index(x + 1, y) + t] - gr(x + 1, y);
        let du = dst[rgb_index(x, y - 1) + t] - gr(x, y - 1);
        let dd = dst[rgb_index(x, y + 1) + t] - gr(x, y + 1);
        var v = 0.0;
        if (fp.mode == 1u) {
            if (color(x - 1, gy) == t) { v = gv + 0.5 * (dl + dr); } else { v = gv + 0.5 * (du + dd); }
        } else {
            let hval = 0.5 * (dl + dr);
            let vval = 0.5 * (du + dd);
            let gh = abs(dl - dr) + abs(gr(x - 1, y) - gr(x + 1, y)) + 0.5 * abs(gr(x - 2, y) + gr(x + 2, y) - 2.0 * gv);
            let gvv = abs(du - dd) + abs(gr(x, y - 1) - gr(x, y + 1)) + 0.5 * abs(gr(x, y - 2) + gr(x, y + 2) - 2.0 * gv);
            let a = 1.0 / (gh * gh + 1e-6);
            let b = 1.0 / (gvv * gvv + 1e-6);
            v = gv + (a * hval + b * vval) / (a + b);
        }
        dst[o + t] = v;
    }
}

fn median9(v: array<f32, 9>) -> f32 {
    var a = v;
    for (var i = 1; i < 9; i++) {
        let key = a[i];
        var j = i - 1;
        loop {
            if (j < 0) { break; }
            if (a[j] <= key) { break; }
            a[j + 1] = a[j];
            j = j - 1;
        }
        a[j + 1] = key;
    }
    return a[4];
}

// Pass 5: false-colour suppression (directional mode): 3x3 median of R-G and B-G on interpolated
// channels; native samples stay. Bilinear mode copies. Output at [4wh, 7wh), clamped at zero.
@compute @workgroup_size(64)
fn demosaic_median(@builtin(global_invocation_id) gid: vec3<u32>) {
    let i = idx(gid);
    if (i >= fp.w * fp.h) { return; }
    let x = i32(i % fp.w);
    let y = i32(i / fp.w);
    let c = color(x, y + i32(fp.y_off));
    let s = fp.w * fp.h + i * 3u;
    let d = 4u * fp.w * fp.h + i * 3u;
    var out = vec3<f32>(dst[s], dst[s + 1u], dst[s + 2u]);
    if (fp.mode == 0u) {
        for (var t = 0u; t < 3u; t += 2u) {
            if (c == t) { continue; }
            var v: array<f32, 9>;
            var n = 0;
            for (var dy = -1; dy <= 1; dy++) {
                for (var dx = -1; dx <= 1; dx++) {
                    let j = rgb_index(x + dx, y + dy);
                    v[n] = dst[j + t] - dst[j + 1u];
                    n++;
                }
            }
            let m = median9(v) + dst[s + 1u];
            if (t == 0u) { out.x = m; } else { out.z = m; }
        }
    }
    out = max(out, vec3<f32>(0.0));
    dst[d] = out.x;
    dst[d + 1u] = out.y;
    dst[d + 2u] = out.z;
}

// Separate dispatch: 9x9 DCV on interleaved RGB. src = RGB band, aux = RGB out.
@compute @workgroup_size(64)
fn dcv_plane9(@builtin(global_invocation_id) gid: vec3<u32>) {
    let i = idx(gid);
    if (i >= fp.w * fp.h) { return; }
    let x = i32(i % fp.w);
    let y = i32(i / fp.w);
    let gy = y + i32(fp.y_off);
    let zw = zones(u32(x), u32(gy));
    var hp = vec3<f32>(0.0);
    for (var n = 0u; n < 4u; n++) {
        let wgt = zw.w[n];
        if (wgt == 0.0) { continue; }
        let base = zw.z[n] * 81u;
        for (var j = 0; j < 9; j++) {
            for (var k = 0; k < 9; k++) {
                let sx = clamp(x + k - 4, 0, i32(fp.w) - 1);
                let sy = clamp(y + j - 4, 0, i32(fp.h) - 1);
                let s = (u32(sy) * fp.w + u32(sx)) * 3u;
                let t = coef[base + u32(j * 9 + k)] * wgt;
                hp += t * vec3<f32>(src[s], src[s + 1u], src[s + 2u]);
            }
        }
    }
    let d = i * 3u;
    aux[d] = max(src[d] + fp.gain * hp.x, 0.0);
    aux[d + 1u] = max(src[d + 1u] + fp.gain * hp.y, 0.0);
    aux[d + 2u] = max(src[d + 2u] + fp.gain * hp.z, 0.0);
}

fn zones_still(x: u32, gy: u32) -> ZoneW {
    var r: ZoneW;
    if (fp.zone_bilinear == 0u) {
        let zx = min(x * 5u / max(fp.full_w, 1u), 4u);
        let zy = min(gy * 4u / max(fp.full_h, 1u), 3u);
        r.z = vec4<u32>(zy * 5u + zx, 0u, 0u, 0u);
        r.w = vec4<f32>(1.0, 0.0, 0.0, 0.0);
        return r;
    }
    let fx = clamp((f32(x) + 0.5) / f32(fp.full_w) * 5.0 - 0.5, 0.0, 4.0);
    let fy = clamp((f32(gy) + 0.5) / f32(fp.full_h) * 4.0 - 0.5, 0.0, 3.0);
    let x0 = u32(floor(fx));
    let y0 = u32(floor(fy));
    let x1 = min(x0 + 1u, 4u);
    let y1 = min(y0 + 1u, 3u);
    let tx = fx - f32(x0);
    let ty = fy - f32(y0);
    r.z = vec4<u32>(y0 * 5u + x0, y0 * 5u + x1, y1 * 5u + x0, y1 * 5u + x1);
    r.w = vec4<f32>((1.0 - tx) * (1.0 - ty), tx * (1.0 - ty), (1.0 - tx) * ty, tx * ty);
    return r;
}

// Separate dispatch: still 11x7 DCV on interleaved RGB (5x4 zones x 3 channels x 77 taps).
@compute @workgroup_size(64)
fn dcv_still(@builtin(global_invocation_id) gid: vec3<u32>) {
    let i = idx(gid);
    if (i >= fp.w * fp.h) { return; }
    let x = i32(i % fp.w);
    let y = i32(i / fp.w);
    let gy = y + i32(fp.y_off);
    let zw = zones_still(u32(x), u32(gy));
    var hp = vec3<f32>(0.0);
    for (var n = 0u; n < 4u; n++) {
        let wgt = zw.w[n];
        if (wgt == 0.0) { continue; }
        let zbase = zw.z[n] * 3u * 77u;
        for (var j = 0; j < 7; j++) {
            let sy = clamp(y + j - 3, 0, i32(fp.h) - 1);
            for (var k = 0; k < 11; k++) {
                let sx = clamp(x + k - 5, 0, i32(fp.w) - 1);
                let s = (u32(sy) * fp.w + u32(sx)) * 3u;
                let t_idx = u32(j * 11 + k);
                let kr = coef[zbase + t_idx] * wgt;
                let kg = coef[zbase + 77u + t_idx] * wgt;
                let kb = coef[zbase + 154u + t_idx] * wgt;
                hp += vec3<f32>(kr * src[s], kg * src[s + 1u], kb * src[s + 2u]);
            }
        }
    }
    let d = i * 3u;
    aux[d] = max(src[d] + soften(fp.gain * hp.x, fp.crispening), 0.0);
    aux[d + 1u] = max(src[d + 1u] + soften(fp.gain * hp.y, fp.crispening), 0.0);
    aux[d + 2u] = max(src[d + 2u] + soften(fp.gain * hp.z, fp.crispening), 0.0);
}
