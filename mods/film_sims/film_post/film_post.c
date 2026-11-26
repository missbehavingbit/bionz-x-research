/* Portable C99 / ARMv7 Cortex-A5 (VFPv3-D16, no NEON) implementation of the
 * Dehancer-derived post-gamma Halation -> Film Grain pipeline.
 *
 * Strictly English-only comments and identifiers.
 */
#include "film_post.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

static inline uint8_t clamp_u8_int(int v) {
    if (v < 0) return 0;
    if (v > 255) return 255;
    return (uint8_t)v;
}

static inline uint8_t u8_min(uint8_t a, uint8_t b) {
    return a < b ? a : b;
}

static inline uint8_t u8_max(uint8_t a, uint8_t b) {
    return a > b ? a : b;
}

static inline uint8_t sat_add_u8(uint8_t a, uint8_t b) {
    uint32_t s = (uint32_t)a + (uint32_t)b;
    return s > 255u ? 255u : (uint8_t)s;
}

static inline uint8_t sat_sub_u8(uint8_t a, uint8_t b) {
    return a > b ? (uint8_t)(a - b) : 0u;
}

static inline uint8_t mix_u8(uint8_t a, uint8_t b, uint8_t t) {
    int32_t t256 = (int32_t)t + ((int32_t)t >> 7);
    return (uint8_t)(((int32_t)a * (256 - t256) + (int32_t)b * t256 + 128) >> 8);
}

static inline uint8_t mul_u8(uint8_t a, uint8_t b) {
    uint32_t b256 = (uint32_t)b + ((uint32_t)b >> 7);
    return (uint8_t)(((uint32_t)a * b256 + 128u) >> 8);
}

static inline uint8_t blend_overlay_u8(uint8_t base, uint8_t blend) {
    if (base <= 127u) {
        uint32_t v = 2u * (uint32_t)base * (uint32_t)blend;
        uint32_t r = (v * 257u + 32768u) >> 16;
        return r > 255u ? 255u : (uint8_t)r;
    } else {
        uint32_t inv = 2u * (uint32_t)(255u - base) * (uint32_t)(255u - blend);
        uint32_t r = (inv * 257u + 32768u) >> 16;
        return (uint8_t)(255u - (r > 255u ? 255u : r));
    }
}

static float clamp01f(float v) {
    if (v < 0.0f) return 0.0f;
    if (v > 1.0f) return 1.0f;
    return v;
}

static uint8_t float_to_u8(float v) {
    int r = (int)lroundf(clamp01f(v) * 255.0f);
    return clamp_u8_int(r);
}

static float smoothstepf(float edge0, float edge1, float x) {
    if (edge1 <= edge0) {
        return x >= edge1 ? 1.0f : 0.0f;
    }
    float t = clamp01f((x - edge0) / (edge1 - edge0));
    return t * t * (3.0f - 2.0f * t);
}

static uint32_t hash2d_u32(uint32_t x, uint32_t y) {
    uint32_t h = (x * 0x9E3779B1u) ^ (y * 0x85EBCA77u) ^ 0xC2B2AE3Du;
    h ^= h >> 16;
    h *= 0x7FEB352Du;
    h ^= h >> 15;
    h *= 0x846CA68Bu;
    h ^= h >> 16;
    return h;
}

void film_post_default_halation(FilmHalationParams *out) {
    if (!out) return;
    out->enabled = 0;
    out->mask_mode = 0;
    out->low = 0.15f;
    out->high = 0.80f;
    out->local_diffusion = 0.15f;
    out->global_diffusion = 0.25f;
    out->scattering = 0.60f;
    out->scattering_amp = 1.0f;
    out->defringe = 1.0f;
    out->impact = 0.54f;
}

void film_post_default_grain(FilmGrainParams *out) {
    if (!out) return;
    out->enabled = 0;
    out->amount = 0.20f;
    out->scale = 1.0f;
    out->shadows = 0.30f;
    out->midtones = 0.55f;
    out->highlights = 0.65f;
    out->color = 0.65f;
    out->seed = 1u;
}

void film_post_init_tile(uint8_t tile[FILM_POST_TILE_SIZE]) {
    int32_t *raw = (int32_t *)malloc(FILM_POST_TILE_SIZE * sizeof(int32_t));
    int32_t *filtered = (int32_t *)malloc(FILM_POST_TILE_SIZE * sizeof(int32_t));
    if (!raw || !filtered) {
        free(raw);
        free(filtered);
        for (size_t i = 0; i < FILM_POST_TILE_SIZE; ++i) {
            tile[i] = 128u;
        }
        return;
    }

    for (uint32_t y = 0; y < 256u; ++y) {
        uint32_t cy0 = y >> 1;
        uint32_t cy1 = (cy0 + 1u) & 127u;
        int32_t fy = (int32_t)(y & 1u);
        for (uint32_t x = 0; x < 256u; ++x) {
            uint32_t cx0 = x >> 1;
            uint32_t cx1 = (cx0 + 1u) & 127u;
            int32_t fx = (int32_t)(x & 1u);

            int32_t fine = (int32_t)(hash2d_u32(x, y) >> 24) - 128;
            int32_t c00 = (int32_t)(hash2d_u32(cx0, cy0) >> 24) - 128;
            int32_t c10 = (int32_t)(hash2d_u32(cx1, cy0) >> 24) - 128;
            int32_t c01 = (int32_t)(hash2d_u32(cx0, cy1) >> 24) - 128;
            int32_t c11 = (int32_t)(hash2d_u32(cx1, cy1) >> 24) - 128;

            int32_t c0 = c00 * (2 - fx) + c10 * fx;
            int32_t c1 = c01 * (2 - fx) + c11 * fx;
            int32_t coarse = (c0 * (2 - fy) + c1 * fy + 2) >> 2;

            int32_t sum = fine * 2 + coarse;
            raw[y * 256u + x] = sum / 3;
        }
    }

    int64_t sum_all = 0;
    for (int y = 0; y < 256; ++y) {
        for (int x = 0; x < 256; ++x) {
            int32_t s = 0;
            for (int dy = -1; dy <= 1; ++dy) {
                int yy = (y + dy) & 255;
                for (int dx = -1; dx <= 1; ++dx) {
                    int xx = (x + dx) & 255;
                    s += raw[yy * 256 + xx];
                }
            }
            int32_t val = (s * 11) / 36;
            filtered[y * 256 + x] = val;
            sum_all += val;
        }
    }
    int32_t mean = (int32_t)(sum_all / 65536);

    for (size_t i = 0; i < FILM_POST_TILE_SIZE; ++i) {
        int32_t v = 128 + filtered[i] - mean;
        if (v < 24) v = 24;
        if (v > 232) v = 232;
        tile[i] = (uint8_t)v;
    }

    free(raw);
    free(filtered);
}

void film_post_prepare_halation(const FilmHalationParams *in, FilmHalationPrepared *out) {
    if (!in || !out) return;
    float low = clamp01f(in->low);
    float high = clamp01f(in->high);
    float scattering = clamp01f(in->scattering);
    float amp = in->scattering_amp;
    if (amp < 0.5f) amp = 0.5f;
    if (amp > 3.0f) amp = 3.0f;

    float exp_alpha = (1.87f - scattering / 1.8f) / 1.2f;
    if (exp_alpha < 0.05f) exp_alpha = 0.05f;
    float exp_scat_r = 0.20f * amp;
    if (exp_scat_r < 0.02f) exp_scat_r = 0.02f;

    for (int i = 0; i < 256; ++i) {
        float x = (float)i / 255.0f;
        out->mask[i] = float_to_u8(1.0f - smoothstepf(low, high, x));
        out->contrast[i] = float_to_u8(smoothstepf(low, 1.0f, x));
        out->alpha_pow[i] = float_to_u8(powf(x, exp_alpha));
        out->red_impact[i] = float_to_u8(powf(x, 2.4f));
        out->scat_r[i] = float_to_u8(powf(x, exp_scat_r));
        out->scat_g[i] = float_to_u8(powf(x, 0.95f));
        out->scat_b[i] = float_to_u8(powf(x, 1.10f));
    }

    out->scattering_u8 = float_to_u8(scattering);
    out->defringe_u8 = float_to_u8(in->defringe);
    out->impact_u8 = float_to_u8(in->impact);
    out->mask_mode = in->mask_mode ? 1u : 0u;
    out->enabled = in->enabled ? 1u : 0u;
    out->local_diffusion = clamp01f(in->local_diffusion);
    out->global_diffusion = clamp01f(in->global_diffusion);
}

void film_post_prepare_grain(const FilmGrainParams *in, FilmGrainPrepared *out) {
    if (!in || !out) return;
    float amount = clamp01f(in->amount);
    float shd = clamp01f(in->shadows);
    float mid = clamp01f(in->midtones);
    float hi = clamp01f(in->highlights);

    for (int i = 0; i < 256; ++i) {
        float luma = (float)i / 255.0f;
        float d_shd = luma / 0.2f;
        float d_mid = (luma - 0.5f) / 0.2f;
        float d_hi = (luma - 1.0f) / 0.2f;
        float g_shd = expf(-0.5f * d_shd * d_shd);
        float g_mid = expf(-0.5f * d_mid * d_mid);
        float g_hi = expf(-0.5f * d_hi * d_hi);
        out->pow08[i] = float_to_u8(powf(luma, 0.8f));
        out->shd_w[i] = float_to_u8(shd * amount * 2.0f * g_shd);
        out->mid_w[i] = float_to_u8(mid * amount * g_mid);
        out->hi_w[i] = float_to_u8(hi * amount * 2.0f * g_hi);
    }

    out->color_u8 = float_to_u8(in->color);
    out->enabled = in->enabled ? 1u : 0u;
    out->scale = in->scale < 1.0f ? 1.0f : (in->scale > 48.0f ? 48.0f : in->scale);
    out->seed = in->seed;
}

typedef struct {
    int i0;
    int i1;
    uint32_t w;
} InterpCoord;

static size_t align16(size_t n) {
    return (n + 15u) & ~(size_t)15u;
}

static void compute_ds_dims(int width, int height, int *ds_out, int *dw_out, int *dh_out) {
    int max_dim = width > height ? width : height;
    int ds = (max_dim + 749) / 750;
    if (ds < 1) ds = 1;
    int dw = (width + ds - 1) / ds;
    int dh = (height + ds - 1) / ds;
    if (dw < 1) dw = 1;
    if (dh < 1) dh = 1;
    *ds_out = ds;
    *dw_out = dw;
    *dh_out = dh;
}

size_t film_post_scratch_bytes(int width, int height) {
    if (width <= 0 || height <= 0) return 0;
    int ds, dw, dh;
    compute_ds_dims(width, height, &ds, &dw, &dh);
    size_t plane_bytes = align16((size_t)dw * (size_t)dh * 3u);
    size_t x_bytes = align16((size_t)width * sizeof(InterpCoord));
    size_t y_bytes = align16((size_t)height * sizeof(InterpCoord));
    size_t grain_x_bytes = align16((size_t)width * sizeof(uint32_t));
    return 4u * plane_bytes + x_bytes + y_bytes + grain_x_bytes + 32u;
}

static void box_blur_rgb(const uint8_t *src, uint8_t *dst, uint8_t *tmp, int w, int h, int radius) {
    size_t total = (size_t)w * (size_t)h * 3u;
    if (radius <= 0 || w <= 0 || h <= 0) {
        memcpy(dst, src, total);
        return;
    }
    int win = 2 * radius + 1;
    int half = win / 2;
    int max_x = w - 1;
    int max_y = h - 1;
    int stride = w * 3;

    for (int y = 0; y < h; ++y) {
        const uint8_t *row_in = src + (size_t)y * (size_t)stride;
        uint8_t *row_tmp = tmp + (size_t)y * (size_t)stride;
        int32_t sr = 0, sg = 0, sb = 0;
        for (int dx = -radius; dx <= radius; ++dx) {
            int cx = dx < 0 ? 0 : (dx > max_x ? max_x : dx);
            const uint8_t *p = row_in + cx * 3;
            sr += p[0];
            sg += p[1];
            sb += p[2];
        }
        for (int x = 0; x < w; ++x) {
            uint8_t *o = row_tmp + x * 3;
            o[0] = (uint8_t)((sr + half) / win);
            o[1] = (uint8_t)((sg + half) / win);
            o[2] = (uint8_t)((sb + half) / win);

            int left = x - radius;
            int right = x + radius + 1;
            if (left < 0) left = 0;
            if (right > max_x) right = max_x;
            const uint8_t *lp = row_in + left * 3;
            const uint8_t *rp = row_in + right * 3;
            sr += (int32_t)rp[0] - (int32_t)lp[0];
            sg += (int32_t)rp[1] - (int32_t)lp[1];
            sb += (int32_t)rp[2] - (int32_t)lp[2];
        }
    }

    for (int x = 0; x < w; ++x) {
        int col = x * 3;
        int32_t sr = 0, sg = 0, sb = 0;
        for (int dy = -radius; dy <= radius; ++dy) {
            int cy = dy < 0 ? 0 : (dy > max_y ? max_y : dy);
            const uint8_t *p = tmp + (size_t)cy * (size_t)stride + col;
            sr += p[0];
            sg += p[1];
            sb += p[2];
        }
        for (int y = 0; y < h; ++y) {
            uint8_t *o = dst + (size_t)y * (size_t)stride + col;
            o[0] = (uint8_t)((sr + half) / win);
            o[1] = (uint8_t)((sg + half) / win);
            o[2] = (uint8_t)((sb + half) / win);

            int top = y - radius;
            int bot = y + radius + 1;
            if (top < 0) top = 0;
            if (bot > max_y) bot = max_y;
            const uint8_t *tp = tmp + (size_t)top * (size_t)stride + col;
            const uint8_t *bp = tmp + (size_t)bot * (size_t)stride + col;
            sr += (int32_t)bp[0] - (int32_t)tp[0];
            sg += (int32_t)bp[1] - (int32_t)tp[1];
            sb += (int32_t)bp[2] - (int32_t)tp[2];
        }
    }
}

static void build_upsample_coords(int full_len, int small_len, InterpCoord *out) {
    if (small_len <= 1) {
        for (int i = 0; i < full_len; ++i) {
            out[i].i0 = 0;
            out[i].i1 = 0;
            out[i].w = 0u;
        }
        return;
    }
    int64_t max_q8 = (int64_t)(small_len - 1) * 256;
    int64_t fl = (int64_t)full_len;
    int64_t sl = (int64_t)small_len;
    for (int i = 0; i < full_len; ++i) {
        int64_t pos_q8 = (((2 * (int64_t)i + 1) * sl * 128) / fl) - 128;
        if (pos_q8 < 0) pos_q8 = 0;
        if (pos_q8 > max_q8) pos_q8 = max_q8;
        int i0 = (int)(pos_q8 >> 8);
        int i1 = i0 + 1;
        if (i1 >= small_len) i1 = small_len - 1;
        out[i].i0 = i0;
        out[i].i1 = i1;
        out[i].w = (uint32_t)(pos_q8 & 255);
    }
}

static void halation_compute_radii(const FilmHalationPrepared *h, int dw, int dh, int *r_local, int *r_global) {
    float dw_max = (float)(dw > dh ? dw : dh);
    int rl = 0;
    if (h->local_diffusion > 0.0f) {
        int r = (int)lroundf(h->local_diffusion * 24.0f * (dw_max / 1080.0f));
        rl = r < 1 ? 1 : r;
    }
    int rg = rl;
    if (h->global_diffusion > 0.0f) {
        int r = (int)lroundf(h->global_diffusion * 72.0f * (dw_max / 1080.0f));
        rg = r > (rl + 1) ? r : (rl + 1);
    }
    *r_local = rl;
    *r_global = rg;
}

static inline void halation_composite_pixel(
    const FilmHalationPrepared *hp,
    uint8_t in_r, uint8_t in_g, uint8_t in_b,
    uint8_t ir_r, uint8_t ir_g, uint8_t ir_b,
    uint8_t h_r, uint8_t h_g, uint8_t h_b,
    uint8_t *out_r, uint8_t *out_g, uint8_t *out_b
) {
    uint8_t c_r = u8_max(u8_max(ir_r, h_r), in_r);
    uint8_t c_g = u8_max(u8_max(ir_g, h_g), in_g);
    uint8_t c_b = u8_max(u8_max(ir_b, h_b), in_b);

    uint8_t a_r = (uint8_t)(c_r - in_r);
    uint8_t a_g = (uint8_t)(c_g - in_g);
    uint8_t a_b = (uint8_t)(c_b - in_b);

    if ((a_r | a_g | a_b) == 0u) {
        if (hp->mask_mode) {
            *out_r = 0u;
            *out_g = 0u;
            *out_b = 0u;
        } else {
            *out_r = in_r;
            *out_g = in_g;
            *out_b = in_b;
        }
        return;
    }

    uint32_t ar_boost = ((uint32_t)a_r * 307u + 128u) >> 8;
    if (ar_boost > 255u) ar_boost = 255u;
    a_r = u8_max((uint8_t)ar_boost, a_g);
    a_g = u8_min((uint8_t)(((uint32_t)a_r * 228u + 128u) >> 8), a_g);

    uint8_t ap_r = hp->alpha_pow[a_r];
    uint8_t ap_g = hp->alpha_pow[a_g];
    uint8_t ap_b = hp->alpha_pow[a_b];

    uint32_t m_r = hp->mask[in_r];
    uint32_t m_g = hp->mask[in_g];
    uint32_t m_b = hp->mask[in_b];
    uint8_t m_lum = (uint8_t)((77u * m_r + 150u * m_g + 29u * m_b + 128u) >> 8);

    uint8_t res_r = mix_u8(in_r, sat_add_u8(in_r, ap_r), m_lum);
    uint8_t res_g = mix_u8(in_g, sat_add_u8(in_g, ap_g), m_lum);
    uint8_t res_b = mix_u8(in_b, sat_add_u8(in_b, ap_b), m_lum);

    uint8_t y_scat = (uint8_t)((162u * in_r + 47u * in_g + 47u * in_b + 128u) >> 8);
    uint8_t scat_mask = mul_u8(y_scat, hp->scattering_u8);
    uint8_t red_impact = hp->red_impact[res_r];

    uint8_t sr = mix_u8(res_r, hp->scat_r[res_r], scat_mask);
    uint8_t sg = mix_u8(res_g, hp->scat_g[res_g], scat_mask);
    uint8_t sb = mix_u8(res_b, hp->scat_b[res_b], scat_mask);

    res_r = mix_u8(res_r, sr, red_impact);
    res_g = mix_u8(res_g, sg, red_impact);
    res_b = mix_u8(res_b, sb, red_impact);

    uint8_t fin_r = sat_sub_u8(res_r, in_r);
    uint8_t fin_g = sat_sub_u8(res_g, in_g);
    uint8_t fin_b = sat_sub_u8(res_b, in_b);
    uint8_t damp_g = (uint8_t)((187u * (uint32_t)u8_min(fin_r, fin_g) + 128u) >> 8);
    fin_g = mix_u8(damp_g, fin_g, fin_r);

    res_r = sat_add_u8(in_r, fin_r);
    res_g = sat_add_u8(in_g, fin_g);
    res_b = sat_add_u8(in_b, fin_b);

    if (hp->defringe_u8 > 0u) {
        uint8_t df = mul_u8(hp->defringe_u8, fin_r);
        res_b = mix_u8(res_b, 0u, df);
    }

    res_r = mix_u8(in_r, res_r, hp->impact_u8);
    res_g = mix_u8(in_g, res_g, hp->impact_u8);
    res_b = mix_u8(in_b, res_b, hp->impact_u8);

    if (hp->mask_mode) {
        res_r = sat_sub_u8(res_r, in_r);
        res_g = sat_sub_u8(res_g, in_g);
        res_b = sat_sub_u8(res_b, in_b);
    }

    *out_r = res_r;
    *out_g = res_g;
    *out_b = res_b;
}

static inline uint8_t sample_tile_bilinear(const uint8_t tile[FILM_POST_TILE_SIZE], uint32_t x_q8, uint32_t y_q8) {
    uint32_t x0 = (x_q8 >> 8) & 255u;
    uint32_t x1 = (x0 + 1u) & 255u;
    uint32_t fx = x_q8 & 255u;
    uint32_t ifx = 256u - fx;

    uint32_t y0 = (y_q8 >> 8) & 255u;
    uint32_t y1 = (y0 + 1u) & 255u;
    uint32_t fy = y_q8 & 255u;
    uint32_t ify = 256u - fy;

    const uint8_t *r0 = tile + (y0 << 8);
    const uint8_t *r1 = tile + (y1 << 8);
    uint32_t c00 = r0[x0];
    uint32_t c10 = r0[x1];
    uint32_t c01 = r1[x0];
    uint32_t c11 = r1[x1];

    uint32_t top = c00 * ifx + c10 * fx;
    uint32_t bot = c01 * ifx + c11 * fx;
    return (uint8_t)((top * ify + bot * fy + 32768u) >> 16);
}

static inline void grain_pixel_rgb(
    const FilmGrainPrepared *gp,
    const uint8_t tile[FILM_POST_TILE_SIZE],
    uint32_t x_q8, uint32_t y_q8,
    uint8_t in_r, uint8_t in_g, uint8_t in_b,
    uint8_t *out_r, uint8_t *out_g, uint8_t *out_b
) {
    const uint32_t off_gx = 97u << 8;
    const uint32_t off_gy = 173u << 8;
    const uint32_t off_bx = 193u << 8;
    const uint32_t off_by = 59u << 8;

    uint8_t g_r = sample_tile_bilinear(tile, x_q8, y_q8);
    uint8_t g_g = g_r;
    uint8_t g_b = g_r;
    if (gp->color_u8 > 0u) {
        uint8_t raw_g = sample_tile_bilinear(tile, x_q8 + off_gx, y_q8 + off_gy);
        uint8_t raw_b = sample_tile_bilinear(tile, x_q8 + off_bx, y_q8 + off_by);
        g_g = mix_u8(g_r, raw_g, gp->color_u8);
        g_b = mix_u8(g_r, raw_b, gp->color_u8);
    }

    uint32_t luma = (54u * in_r + 183u * in_g + 18u * in_b + 128u) >> 8;
    if (luma > 255u) luma = 255u;

    uint8_t ws = gp->shd_w[luma];
    uint8_t wm = gp->mid_w[luma];
    uint8_t wh = gp->hi_w[luma];

    uint8_t r = in_r;
    uint8_t g = in_g;
    uint8_t b = in_b;

    if (ws > 0u) {
        r = mix_u8(r, blend_overlay_u8(gp->pow08[r], g_r), ws);
        g = mix_u8(g, blend_overlay_u8(gp->pow08[g], g_g), ws);
        b = mix_u8(b, blend_overlay_u8(gp->pow08[b], g_b), ws);
    }
    if (wm > 0u) {
        r = mix_u8(r, blend_overlay_u8(r, g_r), wm);
        g = mix_u8(g, blend_overlay_u8(g, g_g), wm);
        b = mix_u8(b, blend_overlay_u8(b, g_b), wm);
    }
    if (wh > 0u) {
        r = mix_u8(r, blend_overlay_u8(sat_sub_u8(r, 51u), g_r), wh);
        g = mix_u8(g, blend_overlay_u8(sat_sub_u8(g, 51u), g_g), wh);
        b = mix_u8(b, blend_overlay_u8(sat_sub_u8(b, 51u), g_b), wh);
    }

    *out_r = (uint8_t)(((uint16_t)in_r + (uint16_t)r + 1u) >> 1);
    *out_g = (uint8_t)(((uint16_t)in_g + (uint16_t)g + 1u) >> 1);
    *out_b = (uint8_t)(((uint16_t)in_b + (uint16_t)b + 1u) >> 1);
}

static void grain_setup_step_and_x_lut(
    const FilmGrainPrepared *gp,
    int width,
    uint32_t *step_q16_out,
    uint32_t *oy_q16_out,
    uint32_t *x_q8_lut
) {
    float scale = gp->scale < 1.0f ? 1.0f : (gp->scale > 48.0f ? 48.0f : gp->scale);
    float eff_scale = 1.0f + (scale - 1.0f) * 0.06f;
    float raw_step = 65536.0f * (5200.0f / (float)(width > 1 ? width : 1)) / eff_scale * 0.45f;
    float max_step = (float)(128u * 65536u);
    if (raw_step < 4096.0f) raw_step = 4096.0f;
    if (raw_step > max_step) raw_step = max_step;
    uint32_t step_q16 = (uint32_t)lroundf(raw_step);

    uint32_t seed_h = hash2d_u32(gp->seed, gp->seed * 0x9E3779B9u);
    uint32_t ox_q16 = seed_h & 0x00FFFFFFu;
    uint32_t oy_q16 = (seed_h >> 8) & 0x00FFFFFFu;

    uint32_t ax = ox_q16;
    for (int x = 0; x < width; ++x) {
        x_q8_lut[x] = ax >> 8;
        ax += step_q16;
    }

    *step_q16_out = step_q16;
    *oy_q16_out = oy_q16;
}

static inline void ycbcr_to_rgb_bt709(uint8_t y, uint8_t cb, uint8_t cr, uint8_t *r, uint8_t *g, uint8_t *b) {
    int32_t y_i = (int32_t)y;
    int32_t cb_s = (int32_t)cb - 128;
    int32_t cr_s = (int32_t)cr - 128;
    *r = clamp_u8_int(y_i + ((403 * cr_s + 128) >> 8));
    *g = clamp_u8_int(y_i - ((48 * cb_s + 120 * cr_s + 128) >> 8));
    *b = clamp_u8_int(y_i + ((475 * cb_s + 128) >> 8));
}

static inline void rgb_to_ycbcr_bt709(uint8_t r, uint8_t g, uint8_t b, uint8_t *y, uint8_t *cb, uint8_t *cr) {
    int32_t ri = r, gi = g, bi = b;
    *y = clamp_u8_int((54 * ri + 183 * gi + 18 * bi + 128) >> 8);
    *cb = clamp_u8_int(128 + ((-29 * ri - 99 * gi + 128 * bi + 128) >> 8));
    *cr = clamp_u8_int(128 + ((128 * ri - 116 * gi - 12 * bi + 128) >> 8));
}

typedef struct {
    void *owned;
    uint8_t *ds_contrast;
    uint8_t *resampled;
    uint8_t *halation;
    uint8_t *blur_tmp;
    InterpCoord *x_coords;
    InterpCoord *y_coords;
    uint32_t *x_q8_lut;
    int ds;
    int dw;
    int dh;
} ScratchLayout;

static int acquire_scratch(int width, int height, void *scratch, size_t scratch_bytes, ScratchLayout *out) {
    size_t needed = film_post_scratch_bytes(width, height);
    if (needed == 0) return -1;

    uint8_t *base = NULL;
    out->owned = NULL;
    if (scratch != NULL) {
        if (scratch_bytes < needed) return -1;
        base = (uint8_t *)scratch;
    } else {
        out->owned = malloc(needed);
        if (!out->owned) return -1;
        base = (uint8_t *)out->owned;
    }

    uintptr_t p = ((uintptr_t)base + 15u) & ~(uintptr_t)15u;
    compute_ds_dims(width, height, &out->ds, &out->dw, &out->dh);
    size_t plane_bytes = align16((size_t)out->dw * (size_t)out->dh * 3u);
    size_t x_bytes = align16((size_t)width * sizeof(InterpCoord));
    size_t y_bytes = align16((size_t)height * sizeof(InterpCoord));

    out->ds_contrast = (uint8_t *)p;
    p += plane_bytes;
    out->resampled = (uint8_t *)p;
    p += plane_bytes;
    out->halation = (uint8_t *)p;
    p += plane_bytes;
    out->blur_tmp = (uint8_t *)p;
    p += plane_bytes;
    out->x_coords = (InterpCoord *)p;
    p += x_bytes;
    out->y_coords = (InterpCoord *)p;
    p += y_bytes;
    out->x_q8_lut = (uint32_t *)p;
    return 0;
}

static void release_scratch(ScratchLayout *sc) {
    free(sc->owned);
    sc->owned = NULL;
}

int film_post_apply_rgba(
    uint8_t *rgba,
    int width,
    int height,
    const FilmPostPrepared *cfg,
    const uint8_t tile[FILM_POST_TILE_SIZE],
    void *scratch,
    size_t scratch_bytes
) {
    if (!rgba || !cfg || width <= 0 || height <= 0) return -1;
    int do_hal = cfg->halation.enabled && (cfg->halation.impact_u8 > 0u || cfg->halation.mask_mode);
    int do_grain = cfg->grain.enabled && tile != NULL;
    if (!do_hal && !do_grain) return 0;

    ScratchLayout sc;
    if (acquire_scratch(width, height, scratch, scratch_bytes, &sc) != 0) return -1;

    if (do_hal) {
        const FilmHalationPrepared *hp = &cfg->halation;
        int ds = sc.ds, dw = sc.dw, dh = sc.dh;
        if (ds == 1) {
            size_t n = (size_t)width * (size_t)height;
            for (size_t i = 0; i < n; ++i) {
                sc.ds_contrast[i * 3 + 0] = hp->contrast[rgba[i * 4 + 0]];
                sc.ds_contrast[i * 3 + 1] = hp->contrast[rgba[i * 4 + 1]];
                sc.ds_contrast[i * 3 + 2] = hp->contrast[rgba[i * 4 + 2]];
            }
        } else {
            for (int dy = 0; dy < dh; ++dy) {
                int y0 = dy * ds;
                int y1 = y0 + ds;
                if (y1 > height) y1 = height;
                for (int dx = 0; dx < dw; ++dx) {
                    int x0 = dx * ds;
                    int x1 = x0 + ds;
                    if (x1 > width) x1 = width;
                    uint32_t count = (uint32_t)((y1 - y0) * (x1 - x0));
                    uint32_t half = count / 2u;
                    uint32_t sr = 0, sg = 0, sb = 0;
                    for (int y = y0; y < y1; ++y) {
                        const uint8_t *row = rgba + (size_t)y * (size_t)width * 4u;
                        for (int x = x0; x < x1; ++x) {
                            const uint8_t *px = row + (size_t)x * 4u;
                            sr += hp->contrast[px[0]];
                            sg += hp->contrast[px[1]];
                            sb += hp->contrast[px[2]];
                        }
                    }
                    uint8_t *dst = sc.ds_contrast + ((size_t)dy * (size_t)dw + (size_t)dx) * 3u;
                    dst[0] = (uint8_t)((sr + half) / count);
                    dst[1] = (uint8_t)((sg + half) / count);
                    dst[2] = (uint8_t)((sb + half) / count);
                }
            }
        }

        int rl = 0, rg = 0;
        halation_compute_radii(hp, dw, dh, &rl, &rg);
        box_blur_rgb(sc.ds_contrast, sc.resampled, sc.blur_tmp, dw, dh, rl);
        box_blur_rgb(sc.ds_contrast, sc.halation, sc.blur_tmp, dw, dh, rg);
        build_upsample_coords(width, dw, sc.x_coords);
        build_upsample_coords(height, dh, sc.y_coords);

        int dw3 = dw * 3;
        for (int y = 0; y < height; ++y) {
            InterpCoord yc = sc.y_coords[y];
            uint32_t wy = yc.w;
            uint32_t iwy = 256u - wy;
            const uint8_t *ir_row0 = sc.resampled + (size_t)yc.i0 * (size_t)dw3;
            const uint8_t *ir_row1 = sc.resampled + (size_t)yc.i1 * (size_t)dw3;
            const uint8_t *h_row0 = sc.halation + (size_t)yc.i0 * (size_t)dw3;
            const uint8_t *h_row1 = sc.halation + (size_t)yc.i1 * (size_t)dw3;
            uint8_t *dst_row = rgba + (size_t)y * (size_t)width * 4u;

            for (int x = 0; x < width; ++x) {
                InterpCoord xc = sc.x_coords[x];
                uint32_t wx = xc.w;
                uint32_t iwx = 256u - wx;
                uint32_t w00 = iwx * iwy;
                uint32_t w10 = wx * iwy;
                uint32_t w01 = iwx * wy;
                uint32_t w11 = wx * wy;
                int x0_3 = xc.i0 * 3;
                int x1_3 = xc.i1 * 3;

                uint8_t ir_r = (uint8_t)((ir_row0[x0_3] * w00 + ir_row0[x1_3] * w10 + ir_row1[x0_3] * w01 + ir_row1[x1_3] * w11 + 32768u) >> 16);
                uint8_t ir_g = (uint8_t)((ir_row0[x0_3 + 1] * w00 + ir_row0[x1_3 + 1] * w10 + ir_row1[x0_3 + 1] * w01 + ir_row1[x1_3 + 1] * w11 + 32768u) >> 16);
                uint8_t ir_b = (uint8_t)((ir_row0[x0_3 + 2] * w00 + ir_row0[x1_3 + 2] * w10 + ir_row1[x0_3 + 2] * w01 + ir_row1[x1_3 + 2] * w11 + 32768u) >> 16);

                uint8_t h_r = (uint8_t)((h_row0[x0_3] * w00 + h_row0[x1_3] * w10 + h_row1[x0_3] * w01 + h_row1[x1_3] * w11 + 32768u) >> 16);
                uint8_t h_g = (uint8_t)((h_row0[x0_3 + 1] * w00 + h_row0[x1_3 + 1] * w10 + h_row1[x0_3 + 1] * w01 + h_row1[x1_3 + 1] * w11 + 32768u) >> 16);
                uint8_t h_b = (uint8_t)((h_row0[x0_3 + 2] * w00 + h_row0[x1_3 + 2] * w10 + h_row1[x0_3 + 2] * w01 + h_row1[x1_3 + 2] * w11 + 32768u) >> 16);

                uint8_t *px = dst_row + (size_t)x * 4u;
                halation_composite_pixel(
                    hp, px[0], px[1], px[2],
                    ir_r, ir_g, ir_b, h_r, h_g, h_b,
                    &px[0], &px[1], &px[2]
                );
            }
        }
    }

    if (do_grain) {
        const FilmGrainPrepared *gp = &cfg->grain;
        uint32_t step_q16 = 0u, oy_q16 = 0u;
        grain_setup_step_and_x_lut(gp, width, &step_q16, &oy_q16, sc.x_q8_lut);

        uint32_t ay = oy_q16;
        for (int y = 0; y < height; ++y) {
            uint32_t y_q8 = ay >> 8;
            uint8_t *row = rgba + (size_t)y * (size_t)width * 4u;
            for (int x = 0; x < width; ++x) {
                uint8_t *px = row + (size_t)x * 4u;
                grain_pixel_rgb(
                    gp, tile, sc.x_q8_lut[x], y_q8,
                    px[0], px[1], px[2],
                    &px[0], &px[1], &px[2]
                );
            }
            ay += step_q16;
        }
    }

    release_scratch(&sc);
    return 0;
}

static int apply_ycbcr_common(
    uint8_t *y_plane,
    int y_stride,
    uint8_t *cbcr_plane,
    int cbcr_stride,
    int width,
    int height,
    int is_420,
    const FilmPostPrepared *cfg,
    const uint8_t tile[FILM_POST_TILE_SIZE],
    void *scratch,
    size_t scratch_bytes
) {
    if (!y_plane || !cbcr_plane || !cfg || width <= 0 || height <= 0) return -1;
    int do_hal = cfg->halation.enabled && (cfg->halation.impact_u8 > 0u || cfg->halation.mask_mode);
    int do_grain = cfg->grain.enabled && tile != NULL;
    if (!do_hal && !do_grain) return 0;

    ScratchLayout sc;
    if (acquire_scratch(width, height, scratch, scratch_bytes, &sc) != 0) return -1;

    if (do_hal) {
        const FilmHalationPrepared *hp = &cfg->halation;
        int ds = sc.ds, dw = sc.dw, dh = sc.dh;
        for (int dy = 0; dy < dh; ++dy) {
            int y0 = dy * ds;
            int y1 = y0 + ds;
            if (y1 > height) y1 = height;
            for (int dx = 0; dx < dw; ++dx) {
                int x0 = dx * ds;
                int x1 = x0 + ds;
                if (x1 > width) x1 = width;
                uint32_t count = (uint32_t)((y1 - y0) * (x1 - x0));
                uint32_t half = count / 2u;
                uint32_t sr = 0, sg = 0, sb = 0;
                for (int y = y0; y < y1; ++y) {
                    const uint8_t *yrow = y_plane + (size_t)y * (size_t)y_stride;
                    const uint8_t *crow = cbcr_plane + (size_t)(is_420 ? (y >> 1) : y) * (size_t)cbcr_stride;
                    for (int x = x0; x < x1; ++x) {
                        int cx = (x & ~1);
                        uint8_t r, g, b;
                        ycbcr_to_rgb_bt709(yrow[x], crow[cx], crow[cx + 1], &r, &g, &b);
                        sr += hp->contrast[r];
                        sg += hp->contrast[g];
                        sb += hp->contrast[b];
                    }
                }
                uint8_t *dst = sc.ds_contrast + ((size_t)dy * (size_t)dw + (size_t)dx) * 3u;
                dst[0] = (uint8_t)((sr + half) / count);
                dst[1] = (uint8_t)((sg + half) / count);
                dst[2] = (uint8_t)((sb + half) / count);
            }
        }

        int rl = 0, rg = 0;
        halation_compute_radii(hp, dw, dh, &rl, &rg);
        box_blur_rgb(sc.ds_contrast, sc.resampled, sc.blur_tmp, dw, dh, rl);
        box_blur_rgb(sc.ds_contrast, sc.halation, sc.blur_tmp, dw, dh, rg);
        build_upsample_coords(width, dw, sc.x_coords);
        build_upsample_coords(height, dh, sc.y_coords);

        int dw3 = dw * 3;
        int y_step = is_420 ? 2 : 1;
        for (int y = 0; y < height; y += y_step) {
            int rows = (is_420 && y + 1 < height) ? 2 : 1;
            uint8_t *crow = cbcr_plane + (size_t)(is_420 ? (y >> 1) : y) * (size_t)cbcr_stride;

            for (int x = 0; x < width; x += 2) {
                int cols = (x + 1 < width) ? 2 : 1;
                uint32_t sum_cb = 0u, sum_cr = 0u, n_px = 0u;
                uint8_t orig_cb = crow[x];
                uint8_t orig_cr = crow[x + 1];
                int any_changed = 0;

                for (int sy = 0; sy < rows; ++sy) {
                    int py = y + sy;
                    InterpCoord yc = sc.y_coords[py];
                    uint32_t wy = yc.w;
                    uint32_t iwy = 256u - wy;
                    const uint8_t *ir_row0 = sc.resampled + (size_t)yc.i0 * (size_t)dw3;
                    const uint8_t *ir_row1 = sc.resampled + (size_t)yc.i1 * (size_t)dw3;
                    const uint8_t *h_row0 = sc.halation + (size_t)yc.i0 * (size_t)dw3;
                    const uint8_t *h_row1 = sc.halation + (size_t)yc.i1 * (size_t)dw3;
                    uint8_t *yrow = y_plane + (size_t)py * (size_t)y_stride;

                    for (int sx = 0; sx < cols; ++sx) {
                        int px = x + sx;
                        InterpCoord xc = sc.x_coords[px];
                        uint32_t wx = xc.w;
                        uint32_t iwx = 256u - wx;
                        uint32_t w00 = iwx * iwy;
                        uint32_t w10 = wx * iwy;
                        uint32_t w01 = iwx * wy;
                        uint32_t w11 = wx * wy;
                        int x0_3 = xc.i0 * 3;
                        int x1_3 = xc.i1 * 3;

                        uint8_t ir_r = (uint8_t)((ir_row0[x0_3] * w00 + ir_row0[x1_3] * w10 + ir_row1[x0_3] * w01 + ir_row1[x1_3] * w11 + 32768u) >> 16);
                        uint8_t ir_g = (uint8_t)((ir_row0[x0_3 + 1] * w00 + ir_row0[x1_3 + 1] * w10 + ir_row1[x0_3 + 1] * w01 + ir_row1[x1_3 + 1] * w11 + 32768u) >> 16);
                        uint8_t ir_b = (uint8_t)((ir_row0[x0_3 + 2] * w00 + ir_row0[x1_3 + 2] * w10 + ir_row1[x0_3 + 2] * w01 + ir_row1[x1_3 + 2] * w11 + 32768u) >> 16);

                        uint8_t h_r = (uint8_t)((h_row0[x0_3] * w00 + h_row0[x1_3] * w10 + h_row1[x0_3] * w01 + h_row1[x1_3] * w11 + 32768u) >> 16);
                        uint8_t h_g = (uint8_t)((h_row0[x0_3 + 1] * w00 + h_row0[x1_3 + 1] * w10 + h_row1[x0_3 + 1] * w01 + h_row1[x1_3 + 1] * w11 + 32768u) >> 16);
                        uint8_t h_b = (uint8_t)((h_row0[x0_3 + 2] * w00 + h_row0[x1_3 + 2] * w10 + h_row1[x0_3 + 2] * w01 + h_row1[x1_3 + 2] * w11 + 32768u) >> 16);

                        if ((ir_r | ir_g | ir_b | h_r | h_g | h_b) == 0u) {
                            if (hp->mask_mode) {
                                yrow[px] = 0u;
                                sum_cb += 128u;
                                sum_cr += 128u;
                                any_changed = 1;
                            } else {
                                sum_cb += orig_cb;
                                sum_cr += orig_cr;
                            }
                            n_px++;
                            continue;
                        }

                        uint8_t in_r, in_g, in_b, out_r, out_g, out_b;
                        ycbcr_to_rgb_bt709(yrow[px], orig_cb, orig_cr, &in_r, &in_g, &in_b);
                        halation_composite_pixel(
                            hp, in_r, in_g, in_b,
                            ir_r, ir_g, ir_b, h_r, h_g, h_b,
                            &out_r, &out_g, &out_b
                        );

                        uint8_t oy, ocb, ocr;
                        rgb_to_ycbcr_bt709(out_r, out_g, out_b, &oy, &ocb, &ocr);
                        yrow[px] = oy;
                        sum_cb += ocb;
                        sum_cr += ocr;
                        any_changed = 1;
                        n_px++;
                    }
                }

                if (any_changed && n_px > 0u) {
                    crow[x] = (uint8_t)((sum_cb + (n_px >> 1)) / n_px);
                    crow[x + 1] = (uint8_t)((sum_cr + (n_px >> 1)) / n_px);
                }
            }
        }
    }

    if (do_grain) {
        const FilmGrainPrepared *gp = &cfg->grain;
        uint32_t step_q16 = 0u, oy_q16 = 0u;
        grain_setup_step_and_x_lut(gp, width, &step_q16, &oy_q16, sc.x_q8_lut);

        if (gp->color_u8 == 0u) {
            /* Monochrome grain fast path: operate directly on Y without CbCr conversion. */
            uint32_t ay = oy_q16;
            for (int y = 0; y < height; ++y) {
                uint32_t y_q8 = ay >> 8;
                uint8_t *yrow = y_plane + (size_t)y * (size_t)y_stride;
                for (int x = 0; x < width; ++x) {
                    uint8_t in_y = yrow[x];
                    uint8_t g_val = sample_tile_bilinear(tile, sc.x_q8_lut[x], y_q8);
                    uint8_t ws = gp->shd_w[in_y];
                    uint8_t wm = gp->mid_w[in_y];
                    uint8_t wh = gp->hi_w[in_y];
                    uint8_t v = in_y;
                    if (ws > 0u) v = mix_u8(v, blend_overlay_u8(gp->pow08[v], g_val), ws);
                    if (wm > 0u) v = mix_u8(v, blend_overlay_u8(v, g_val), wm);
                    if (wh > 0u) v = mix_u8(v, blend_overlay_u8(sat_sub_u8(v, 51u), g_val), wh);
                    yrow[x] = (uint8_t)(((uint16_t)in_y + (uint16_t)v + 1u) >> 1);
                }
                ay += step_q16;
            }
        } else {
            int y_step = is_420 ? 2 : 1;
            for (int y = 0; y < height; y += y_step) {
                int rows = (is_420 && y + 1 < height) ? 2 : 1;
                uint8_t *crow = cbcr_plane + (size_t)(is_420 ? (y >> 1) : y) * (size_t)cbcr_stride;
                for (int x = 0; x < width; x += 2) {
                    int cols = (x + 1 < width) ? 2 : 1;
                    uint8_t orig_cb = crow[x];
                    uint8_t orig_cr = crow[x + 1];
                    uint32_t sum_cb = 0u, sum_cr = 0u, n_px = 0u;
                    for (int sy = 0; sy < rows; ++sy) {
                        int py = y + sy;
                        uint32_t y_q8 = (oy_q16 + (uint32_t)py * step_q16) >> 8;
                        uint8_t *yrow = y_plane + (size_t)py * (size_t)y_stride;
                        for (int sx = 0; sx < cols; ++sx) {
                            int px = x + sx;
                            uint8_t in_r, in_g, in_b, out_r, out_g, out_b;
                            ycbcr_to_rgb_bt709(yrow[px], orig_cb, orig_cr, &in_r, &in_g, &in_b);
                            grain_pixel_rgb(
                                gp, tile, sc.x_q8_lut[px], y_q8,
                                in_r, in_g, in_b,
                                &out_r, &out_g, &out_b
                            );
                            uint8_t oy, ocb, ocr;
                            rgb_to_ycbcr_bt709(out_r, out_g, out_b, &oy, &ocb, &ocr);
                            yrow[px] = oy;
                            sum_cb += ocb;
                            sum_cr += ocr;
                            n_px++;
                        }
                    }
                    if (n_px > 0u) {
                        crow[x] = (uint8_t)((sum_cb + (n_px >> 1)) / n_px);
                        crow[x + 1] = (uint8_t)((sum_cr + (n_px >> 1)) / n_px);
                    }
                }
            }
        }
    }

    release_scratch(&sc);
    return 0;
}

int film_post_apply_nv12(
    uint8_t *y_plane,
    int y_stride,
    uint8_t *cbcr_plane,
    int cbcr_stride,
    int width,
    int height,
    const FilmPostPrepared *cfg,
    const uint8_t tile[FILM_POST_TILE_SIZE],
    void *scratch,
    size_t scratch_bytes
) {
    return apply_ycbcr_common(
        y_plane, y_stride, cbcr_plane, cbcr_stride, width, height, 1,
        cfg, tile, scratch, scratch_bytes
    );
}

int film_post_apply_ycbcr422(
    uint8_t *y_plane,
    int y_stride,
    uint8_t *cbcr_plane,
    int cbcr_stride,
    int width,
    int height,
    const FilmPostPrepared *cfg,
    const uint8_t tile[FILM_POST_TILE_SIZE],
    void *scratch,
    size_t scratch_bytes
) {
    return apply_ycbcr_common(
        y_plane, y_stride, cbcr_plane, cbcr_stride, width, height, 0,
        cfg, tile, scratch, scratch_bytes
    );
}
