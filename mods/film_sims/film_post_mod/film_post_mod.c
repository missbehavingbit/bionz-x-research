/* FilmPost SMOD v2 (Halation -> Film Grain for Sony ILCE-6400 / ILCE-6400A Still JPEG).
 *
 * Hooks MprIf::WriteStill(MprIf::WriteStillParam*, MPR::StillInfo*, unsigned long) in libmpr.so:
 *   1. Locates the main baseline JPEG BufferArea inside stillInfo + 0x08 (slots +0x80/+0x90/+0xB0).
 *   2. Allocates temporary working buffers via mmap2 (zero im.elf heap usage), decodes the
 *      baseline JPEG to Y + interleaved CbCr planes, runs integer Halation -> Film Grain using
 *      pre-baked uint8_t[256] LUTs and a deterministic 256x256 tile, and re-encodes in-place
 *      (preserving APPn/EXIF markers, failing safe if size exceeds original capacity).
 *   3. Munmaps all temporary buffers and tail-calls the original MprIf::WriteStill.
 *
 * Nested Submenu Configuration (__smod_bss + 4, 4 x uint8_t):
 *   cfg[0] Halation     : 0=35mm (Default), 1=35mm No Remjet, 2=16mm, 3=8mm No Remjet, 4=Off
 *   cfg[1] Halation Mix : 0=100%, 1=75%, 2=50%, 3=125%
 *   cfg[2] Film Grain   : 0=35mm ISO 250, 1=35mm ISO 500, 2=16mm ISO 250, 3=8mm ISO 500, 4=Off
 *   cfg[3] JPEG Quality : 0=High (92), 1=Very High (96), 2=Medium (85)
 *
 * Log: /tmp_bt/mods/film_post.log
 */
#include <stddef.h>
#include <stdint.h>

#include "fp_jpeg.h"
#include "film_post_luts.h"

#define FP_MAGIC         0x54504C46u /* "FLPT" */
#define FP_TILE_DIM      256
#define FP_TILE_SIZE     (FP_TILE_DIM * FP_TILE_DIM)
#define FP_LOG_PATH      "/tmp_bt/mods/film_post.log"

struct fp_bss_state {
    uint32_t magic;
    uint8_t  cfg[8];         /* offset 4: synced by Mod Loader v2 SMNU (cfg[0..3]) */
    uint32_t active;         /* offset 12 */
    uint32_t frames_ok;
    uint32_t frames_skip;
    uint32_t seed_counter;
    uint32_t last_in_wh;
    uint32_t last_orig_bytes;
    uint32_t last_out_bytes;
    uint8_t  tile[FP_TILE_SIZE];
};

extern uint8_t __smod_bss[] __attribute__((visibility("hidden")));
#define FP_BSS ((struct fp_bss_state *)__smod_bss)

extern int fp_orig_write_still(void *this_ptr, void *param, uint8_t *still_info, uint32_t flags)
    __attribute__((visibility("hidden")));
extern int fp_orig_encode_still(void *this_ptr, void *param)
    __attribute__((visibility("hidden")));
extern int fp_spawn_thread(void *stack_top, void (*fn)(void *), void *arg, int32_t *ctid)
    __attribute__((visibility("hidden")));

/* ---- Minimal Linux ARM EABI syscall wrappers ---- */
static long sc3(long n, long a, long b, long c) {
    register long r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c;
    register long r7 __asm__("r7") = n;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return r0;
}

static long sc6(long n, long a, long b, long c, long d, long e, long f) {
    register long r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c,
                  r3 __asm__("r3") = d, r4 __asm__("r4") = e, r5 __asm__("r5") = f;
    register long r7 __asm__("r7") = n;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r3), "r"(r4), "r"(r5), "r"(r7) : "memory");
    return r0;
}

static void *fp_mmap_anon(size_t len) {
    long r = sc6(192 /* SYS_mmap2 */, 0, (long)len, 3 /* PROT_READ|PROT_WRITE */,
                 0x22 /* MAP_PRIVATE|MAP_ANONYMOUS */, -1, 0);
    if (r < 0 && r > -4096) return NULL;
    return (void *)(uintptr_t)r;
}

static void fp_munmap_anon(void *ptr, size_t len) {
    if (ptr && len > 0u) {
        sc3(91 /* SYS_munmap */, (long)(uintptr_t)ptr, (long)len, 0);
    }
}

/* Uses SYS_mincore (219) to verify a user page is mapped before dereferencing it. */
static int fp_addr_mapped(uintptr_t addr) {
    if (addr < 0x00100000u) return 0;
    uint8_t vec = 0u;
    long r = sc3(219 /* SYS_mincore */, (long)(addr & ~(uintptr_t)4095u), 4096L, (long)(uintptr_t)&vec);
    return (r == 0);
}

static int fp_range_mapped(uintptr_t addr, size_t len) {
    if (len == 0u || addr < 0x00100000u) return 0;
    uintptr_t end = addr + (uintptr_t)(len - 1u);
    if (end < addr) return 0;
    return fp_addr_mapped(addr) && fp_addr_mapped(end);
}

static uint32_t fp_now_ms(void) {
    uint32_t ts[2] = { 0u, 0u };
    if (sc3(263 /* SYS_clock_gettime */, 1 /* CLOCK_MONOTONIC */, (long)(uintptr_t)ts, 0) < 0) {
        return 0u;
    }
    return ts[0] * 1000u + fp_udiv(ts[1], 1000000u);
}

static void fp_futex_wait(volatile int32_t *addr, int32_t val, int op) {
    sc6(240 /* SYS_futex */, (long)(uintptr_t)addr, op, val, 0, 0, 0);
}

static void fp_futex_wake(volatile int32_t *addr, int32_t n, int op) {
    sc6(240 /* SYS_futex */, (long)(uintptr_t)addr, op, n, 0, 0, 0);
}

/* ---- Bounded ASCII logging ---- */
typedef struct {
    char b[320];
    int  n;
} lbuf_t;

static void lb_ch(lbuf_t *l, char c) {
    if (l->n < (int)sizeof(l->b) - 1) l->b[l->n++] = c;
}

static void lb_str(lbuf_t *l, const char *s) {
    while (*s) lb_ch(l, *s++);
}

static void lb_hex(lbuf_t *l, uint32_t v, int digits) {
    for (int sh = (digits - 1) * 4; sh >= 0; sh -= 4) {
        uint32_t nib = (v >> sh) & 0xfu;
        lb_ch(l, (char)(nib < 10u ? ('0' + nib) : ('a' + nib - 10u)));
    }
}

static void lb_flush(const lbuf_t *l) {
    long fd = sc3(5 /* open */, (long)FP_LOG_PATH, 01 | 0100 | 02000, 0644);
    if (fd >= 0) {
        sc3(4 /* write */, fd, (long)l->b, (long)l->n);
        sc3(6 /* close */, fd, 0, 0);
    }
}

/* Streams /proc/self/maps to find the VMA covering target_va (zero dynamic alloc). */
static void append_vma_for_addr(lbuf_t *l, uint32_t target_va) {
    long fd = sc3(5 /* open */, (long)"/proc/self/maps", 0 /* O_RDONLY */, 0);
    if (fd < 0) {
        lb_str(l, " nomaps");
        return;
    }
    char rbuf[512];
    char line[192];
    int llen = 0;
    int found = 0;
    for (;;) {
        long nr = sc3(3 /* SYS_read */, fd, (long)rbuf, (long)sizeof(rbuf));
        if (nr <= 0) break;
        for (long i = 0; i < nr && !found; ++i) {
            char ch = rbuf[i];
            if (ch != '\n') {
                if (llen < (int)sizeof(line) - 1) line[llen++] = ch;
                continue;
            }
            line[llen] = '\0';
            llen = 0;
            uint32_t lo = 0u, hi = 0u;
            int p = 0;
            while (line[p] && line[p] != '-') {
                char c = line[p++];
                uint32_t d = (c >= '0' && c <= '9') ? (uint32_t)(c - '0')
                           : ((c >= 'a' && c <= 'f') ? (uint32_t)(c - 'a' + 10) : 99u);
                if (d > 15u) break;
                lo = (lo << 4) | d;
            }
            if (line[p] == '-') {
                p++;
                while (line[p] && line[p] != ' ') {
                    char c = line[p++];
                    uint32_t d = (c >= '0' && c <= '9') ? (uint32_t)(c - '0')
                               : ((c >= 'a' && c <= 'f') ? (uint32_t)(c - 'a' + 10) : 99u);
                    if (d > 15u) break;
                    hi = (hi << 4) | d;
                }
                if (line[p] == ' ' && lo <= target_va && target_va < hi) {
                    lb_str(l, " vma=");
                    int prev_sp = 0;
                    for (int k = 0; line[k] && l->n < (int)sizeof(l->b) - 2; ++k) {
                        char c = line[k];
                        if (c == ' ' || c == '\t') {
                            if (!prev_sp) lb_ch(l, ' ');
                            prev_sp = 1;
                        } else {
                            lb_ch(l, c);
                            prev_sp = 0;
                        }
                    }
                    found = 1;
                }
            }
        }
        if (found) break;
    }
    sc3(6 /* close */, fd, 0, 0);
    if (!found) lb_str(l, " vma=none");
}

/* ---- Integer helpers ---- */
static inline __attribute__((always_inline)) uint8_t u8_min(uint8_t a, uint8_t b) { return a < b ? a : b; }
static inline __attribute__((always_inline)) uint8_t u8_max(uint8_t a, uint8_t b) { return a > b ? a : b; }
static inline __attribute__((always_inline)) uint8_t sat_add_u8(uint8_t a, uint8_t b) {
    uint32_t s = (uint32_t)a + (uint32_t)b;
    return s > 255u ? 255u : (uint8_t)s;
}
static inline __attribute__((always_inline)) uint8_t sat_sub_u8(uint8_t a, uint8_t b) {
    return a > b ? (uint8_t)(a - b) : 0u;
}
static inline __attribute__((always_inline)) uint8_t mix_u8(uint8_t a, uint8_t b, uint8_t t) {
    int32_t t256 = (int32_t)t + ((int32_t)t >> 7);
    return (uint8_t)(((int32_t)a * (256 - t256) + (int32_t)b * t256 + 128) >> 8);
}
static inline __attribute__((always_inline)) uint8_t mul_u8(uint8_t a, uint8_t b) {
    uint32_t b256 = (uint32_t)b + ((uint32_t)b >> 7);
    return (uint8_t)(((uint32_t)a * b256 + 128u) >> 8);
}
static inline __attribute__((always_inline)) uint8_t blend_overlay_u8(uint8_t base, uint8_t blend) {
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

static uint32_t hash2d_u32(uint32_t x, uint32_t y) {
    uint32_t h = (x * 0x9E3779B1u) ^ (y * 0x85EBCA77u) ^ 0xC2B2AE3Du;
    h ^= h >> 16;
    h *= 0x7FEB352Du;
    h ^= h >> 15;
    h *= 0x846CA68Bu;
    h ^= h >> 16;
    return h;
}

static void init_grain_tile(uint8_t tile[FP_TILE_SIZE]) {
    size_t work_bytes = (FP_TILE_SIZE * 2u * sizeof(int32_t) + 4095u) & ~(size_t)4095u;
    int32_t *raw = (int32_t *)fp_mmap_anon(work_bytes);
    if (!raw) {
        fp_memset(tile, 128u, FP_TILE_SIZE);
        return;
    }
    int32_t *filtered = raw + FP_TILE_SIZE;

    for (uint32_t y = 0u; y < 256u; ++y) {
        uint32_t cy0 = y >> 1;
        uint32_t cy1 = (cy0 + 1u) & 127u;
        int32_t fy = (int32_t)(y & 1u);
        for (uint32_t x = 0u; x < 256u; ++x) {
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
            /* Divide by 3 without __aeabi_idiv: sign-preserving / 3 via 0x55555556 multiply. */
            raw[y * 256u + x] = (int32_t)(((int64_t)sum * 0x55555556LL) >> 32) - (sum >> 31);
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
            int32_t val = (s >= 0) ? (int32_t)fp_udiv((uint32_t)(s * 11), 36u)
                                   : -(int32_t)fp_udiv((uint32_t)(-s * 11), 36u);
            filtered[y * 256 + x] = val;
            sum_all += val;
        }
    }
    int32_t mean = (sum_all >= 0) ? (int32_t)(sum_all >> 16) : -(int32_t)((-sum_all) >> 16);

    for (size_t i = 0; i < FP_TILE_SIZE; ++i) {
        int32_t v = 128 + filtered[i] - mean;
        if (v < 24) v = 24;
        if (v > 232) v = 232;
        tile[i] = (uint8_t)v;
    }

    fp_munmap_anon(raw, work_bytes);
}

typedef struct {
    int i0;
    int i1;
    uint32_t w;
} fp_interp_t;

static void box_blur_rgb(const uint8_t *src, uint8_t *dst, uint8_t *tmp, int w, int h, int radius) {
    size_t total = (size_t)w * (size_t)h * 3u;
    if (radius <= 0 || w <= 0 || h <= 0) {
        fp_memcpy(dst, src, total);
        return;
    }
    uint32_t win = (uint32_t)(2 * radius + 1);
    uint32_t half = win >> 1;
    uint32_t recip = fp_udiv((1u << 24) + win - 1u, win);
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
            o[0] = (uint8_t)(((uint64_t)((uint32_t)sr + half) * recip) >> 24);
            o[1] = (uint8_t)(((uint64_t)((uint32_t)sg + half) * recip) >> 24);
            o[2] = (uint8_t)(((uint64_t)((uint32_t)sb + half) * recip) >> 24);

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
            o[0] = (uint8_t)(((uint64_t)((uint32_t)sr + half) * recip) >> 24);
            o[1] = (uint8_t)(((uint64_t)((uint32_t)sg + half) * recip) >> 24);
            o[2] = (uint8_t)(((uint64_t)((uint32_t)sb + half) * recip) >> 24);

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

static void build_upsample_coords(int full_len, int small_len, fp_interp_t *out) {
    if (small_len <= 1) {
        for (int i = 0; i < full_len; ++i) {
            out[i].i0 = 0;
            out[i].i1 = 0;
            out[i].w = 0u;
        }
        return;
    }
    int32_t max_q8 = (int32_t)(small_len - 1) * 256;
    uint32_t fl = (uint32_t)full_len;
    uint32_t sl = (uint32_t)small_len;
    for (int i = 0; i < full_len; ++i) {
        uint32_t num = (uint32_t)(2 * i + 1) * sl * 128u;
        int32_t pos_q8 = (int32_t)fp_udiv(num, fl) - 128;
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

/* Standard JPEG ITU-R BT.601 full-range YCbCr <-> RGB (matches Sony sYCC / JFIF / libjpeg). */
static inline __attribute__((always_inline)) void ycbcr_to_rgb_bt601(uint8_t y, uint8_t cb, uint8_t cr, uint8_t *r, uint8_t *g, uint8_t *b) {
    int32_t y_i = (int32_t)y;
    int32_t cb_s = (int32_t)cb - 128;
    int32_t cr_s = (int32_t)cr - 128;
    *r = fp_clamp_u8(y_i + ((359 * cr_s + 128) >> 8));
    *g = fp_clamp_u8(y_i - ((88 * cb_s + 183 * cr_s + 128) >> 8));
    *b = fp_clamp_u8(y_i + ((454 * cb_s + 128) >> 8));
}

static inline __attribute__((always_inline)) void rgb_to_ycbcr_bt601(uint8_t r, uint8_t g, uint8_t b, uint8_t *y, uint8_t *cb, uint8_t *cr) {
    int32_t ri = r, gi = g, bi = b;
    *y = fp_clamp_u8((77 * ri + 150 * gi + 29 * bi + 128) >> 8);
    *cb = fp_clamp_u8(128 + ((-43 * ri - 85 * gi + 128 * bi + 128) >> 8));
    *cr = fp_clamp_u8(128 + ((128 * ri - 107 * gi - 21 * bi + 128) >> 8));
}

static inline __attribute__((always_inline)) void halation_composite_pixel(
    const fp_hal_preset_t *hp,
    uint8_t impact_u8,
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
        *out_r = in_r;
        *out_g = in_g;
        *out_b = in_b;
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

    *out_r = mix_u8(in_r, res_r, impact_u8);
    *out_g = mix_u8(in_g, res_g, impact_u8);
    *out_b = mix_u8(in_b, res_b, impact_u8);
}

static void build_grain_scanline(const uint8_t tile[FP_TILE_SIZE], uint32_t y_q8, uint8_t out[256]) {
    uint32_t y0 = (y_q8 >> 8) & 255u;
    uint32_t y1 = (y0 + 1u) & 255u;
    uint32_t fy = y_q8 & 255u;
    uint32_t ify = 256u - fy;
    const uint8_t *r0 = tile + (y0 << 8);
    const uint8_t *r1 = tile + (y1 << 8);
    for (int i = 0; i < 256; ++i) {
        out[i] = (uint8_t)((r0[i] * ify + r1[i] * fy + 128u) >> 8);
    }
}

/* Halation Pass 1 accumulator: merges one decoded MCU strip into `ds_contrast`. */
typedef struct {
    int ds;
    int dw;
    int dh;
    uint32_t ds2;
    uint32_t recip_ds2;
    int cur_dy;
    int y_in_cell;
} fp_ds_state_t;

static void ds_accumulate_strip(
    const uint8_t *y_strip,
    int y_stride,
    const uint8_t *cbcr_strip,
    int cbcr_stride,
    int y_start,
    int strip_h,
    int width,
    int height,
    int is_420,
    int dc_only,
    const fp_hal_preset_t *hp,
    uint8_t *ds_contrast,
    uint32_t *acc,
    fp_ds_state_t *st
) {
    int ds = st->ds;
    int dw = st->dw;
    int dh = st->dh;
    uint32_t ds2 = st->ds2;
    uint32_t recip_ds2 = st->recip_ds2;

    if (dc_only) {
        for (int sy = 0; sy < strip_h; sy += 8) {
            int gy = y_start + sy;
            const uint8_t *yrow = y_strip + (size_t)sy * (size_t)y_stride;
            const uint8_t *crow = cbcr_strip + (size_t)(is_420 ? (sy >> 1) : sy) * (size_t)cbcr_stride;

            int dx = 0;
            int next_x = ds;
            for (int x = 0; x < width; x += 8) {
                while (x >= next_x && dx + 1 < dw) {
                    dx++;
                    next_x += ds;
                }
                int cx = x & ~1;
                uint8_t r, g, b;
                ycbcr_to_rgb_bt601(yrow[x], crow[cx], crow[cx + 1], &r, &g, &b);
                uint32_t *cell = acc + (size_t)dx * 4u;
                cell[0] += hp->contrast[r];
                cell[1] += hp->contrast[g];
                cell[2] += hp->contrast[b];
                cell[3] += 1u;
            }

            st->y_in_cell += 8;
            int at_end = (gy + 8 >= height) ? 1 : 0;
            if (st->y_in_cell >= ds || at_end) {
                if (st->cur_dy < dh) {
                    uint8_t *dst_row = ds_contrast + (size_t)st->cur_dy * (size_t)dw * 3u;
                    for (int dxi = 0; dxi < dw; ++dxi) {
                        const uint32_t *cell = acc + (size_t)dxi * 4u;
                        uint32_t count = cell[3];
                        uint8_t *o = dst_row + dxi * 3;
                        if (count == 0u) {
                            if (dxi > 0) {
                                o[0] = o[-3]; o[1] = o[-2]; o[2] = o[-1];
                            } else {
                                o[0] = o[1] = o[2] = 0u;
                            }
                        } else if (count == 1u) {
                            o[0] = (uint8_t)cell[0];
                            o[1] = (uint8_t)cell[1];
                            o[2] = (uint8_t)cell[2];
                        } else if (count == 2u) {
                            o[0] = (uint8_t)((cell[0] + 1u) >> 1);
                            o[1] = (uint8_t)((cell[1] + 1u) >> 1);
                            o[2] = (uint8_t)((cell[2] + 1u) >> 1);
                        } else if (count == 4u) {
                            o[0] = (uint8_t)((cell[0] + 2u) >> 2);
                            o[1] = (uint8_t)((cell[1] + 2u) >> 2);
                            o[2] = (uint8_t)((cell[2] + 2u) >> 2);
                        } else {
                            uint32_t half = count >> 1;
                            o[0] = (uint8_t)fp_udiv(cell[0] + half, count);
                            o[1] = (uint8_t)fp_udiv(cell[1] + half, count);
                            o[2] = (uint8_t)fp_udiv(cell[2] + half, count);
                        }
                    }
                }
                fp_memset(acc, 0u, (size_t)dw * 4u * sizeof(uint32_t));
                st->y_in_cell = (st->y_in_cell >= ds) ? (st->y_in_cell - ds) : 0;
                st->cur_dy++;
                if (at_end) {
                    while (st->cur_dy < dh) {
                        uint8_t *dst_row = ds_contrast + (size_t)st->cur_dy * (size_t)dw * 3u;
                        if (st->cur_dy > 0) {
                            fp_memcpy(dst_row, dst_row - (size_t)dw * 3u, (size_t)dw * 3u);
                        } else {
                            fp_memset(dst_row, 0u, (size_t)dw * 3u);
                        }
                        st->cur_dy++;
                    }
                }
            }
        }
        return;
    }

    for (int sy = 0; sy < strip_h; ++sy) {
        int gy = y_start + sy;
        const uint8_t *yrow = y_strip + (size_t)sy * (size_t)y_stride;
        const uint8_t *crow = cbcr_strip + (size_t)(is_420 ? (sy >> 1) : sy) * (size_t)cbcr_stride;

        int dx = 0;
        int x_rem = 0;
        for (int x = 0; x < width; ++x) {
            int cx = x & ~1;
            uint8_t r, g, b;
            ycbcr_to_rgb_bt601(yrow[x], crow[cx], crow[cx + 1], &r, &g, &b);
            uint32_t *cell = acc + (size_t)dx * 4u;
            cell[0] += hp->contrast[r];
            cell[1] += hp->contrast[g];
            cell[2] += hp->contrast[b];
            cell[3] += 1u;
            if (++x_rem == ds) {
                dx++;
                x_rem = 0;
            }
        }

        st->y_in_cell++;
        if (st->y_in_cell == ds || gy == height - 1) {
            if (st->cur_dy < dh) {
                uint8_t *dst_row = ds_contrast + (size_t)st->cur_dy * (size_t)dw * 3u;
                for (int dxi = 0; dxi < dw; ++dxi) {
                    const uint32_t *cell = acc + (size_t)dxi * 4u;
                    uint32_t count = cell[3];
                    uint32_t half = count >> 1;
                    uint8_t *o = dst_row + dxi * 3;
                    if (count == 0u) {
                        o[0] = o[1] = o[2] = 0u;
                    } else if (count == ds2) {
                        o[0] = (uint8_t)(((uint64_t)(cell[0] + half) * recip_ds2) >> 24);
                        o[1] = (uint8_t)(((uint64_t)(cell[1] + half) * recip_ds2) >> 24);
                        o[2] = (uint8_t)(((uint64_t)(cell[2] + half) * recip_ds2) >> 24);
                    } else {
                        o[0] = (uint8_t)fp_udiv(cell[0] + half, count);
                        o[1] = (uint8_t)fp_udiv(cell[1] + half, count);
                        o[2] = (uint8_t)fp_udiv(cell[2] + half, count);
                    }
                }
            }
            fp_memset(acc, 0u, (size_t)dw * 4u * sizeof(uint32_t));
            st->y_in_cell = 0;
            st->cur_dy++;
        }
    }
}

/* Applies Halation (if active) and Film Grain (if active) in a single fused MCU-strip pass. */
static void process_strip_post(
    uint8_t *y_strip,
    int y_stride,
    uint8_t *cbcr_strip,
    int cbcr_stride,
    int y_start,
    int strip_h,
    int width,
    int is_420,
    int dw,
    const fp_hal_preset_t *hp,
    uint8_t hal_impact_u8,
    const uint8_t *ir_plane,
    const uint8_t *hal_plane,
    const uint8_t *hal_nz,
    const fp_interp_t *x_coords,
    const fp_interp_t *y_coords,
    const fp_grain_preset_t *gp,
    uint32_t step_q16,
    uint32_t oy_q16,
    const uint32_t *x_q8_lut,
    const uint8_t tile[FP_TILE_SIZE]
) {
    int use_hal = (hp && hal_impact_u8 > 0u) ? 1 : 0;
    if (!use_hal && !gp) return;

    int y_step = is_420 ? 2 : 1;
    int dw3 = dw * 3;
    uint8_t grain_color = gp ? gp->color_u8 : 0u;

    uint8_t gr_rows[2][256];
    uint8_t gg_rows[2][256];
    uint8_t gb_rows[2][256];

    for (int y = 0; y < strip_h; y += y_step) {
        int rows = (is_420 && y + 1 < strip_h) ? 2 : 1;
        uint8_t *crow = cbcr_strip + (size_t)(is_420 ? (y >> 1) : y) * (size_t)cbcr_stride;

        const uint8_t *ir_r0[2] = { NULL, NULL }, *ir_r1[2] = { NULL, NULL };
        const uint8_t *h_r0[2]  = { NULL, NULL }, *h_r1[2]  = { NULL, NULL };
        const uint8_t *nz_r[2]  = { NULL, NULL };
        uint32_t wy_v[2] = { 0u, 0u }, iwy_v[2] = { 0u, 0u };
        uint8_t *y_r[2] = { NULL, NULL };

        for (int sy = 0; sy < rows; ++sy) {
            int gy = y_start + y + sy;
            y_r[sy] = y_strip + (size_t)(y + sy) * (size_t)y_stride;
            if (use_hal) {
                fp_interp_t yc = y_coords[gy];
                wy_v[sy] = yc.w;
                iwy_v[sy] = 256u - yc.w;
                ir_r0[sy] = ir_plane  + (size_t)yc.i0 * (size_t)dw3;
                ir_r1[sy] = ir_plane  + (size_t)yc.i1 * (size_t)dw3;
                h_r0[sy]  = hal_plane + (size_t)yc.i0 * (size_t)dw3;
                h_r1[sy]  = hal_plane + (size_t)yc.i1 * (size_t)dw3;
                nz_r[sy]  = hal_nz    + (size_t)yc.i0 * (size_t)dw;
            }
            if (gp) {
                uint32_t y_q8 = (oy_q16 + (uint32_t)gy * step_q16) >> 8;
                build_grain_scanline(tile, y_q8, gr_rows[sy]);
                if (grain_color > 0u) {
                    build_grain_scanline(tile, y_q8 + (173u << 8), gg_rows[sy]);
                    build_grain_scanline(tile, y_q8 + (59u << 8),  gb_rows[sy]);
                }
            }
        }

        for (int x = 0; x < width; x += 2) {
            int cols = (x + 1 < width) ? 2 : 1;
            uint32_t sum_cb = 0u, sum_cr = 0u, n_px = 0u;
            uint8_t orig_cb = crow[x];
            uint8_t orig_cr = crow[x + 1];
            int any_changed = 0;

            for (int sy = 0; sy < rows; ++sy) {
                uint8_t *yrow = y_r[sy];
                const uint8_t *gr_row = gr_rows[sy];
                const uint8_t *gg_row = gg_rows[sy];
                const uint8_t *gb_row = gb_rows[sy];

                for (int sx = 0; sx < cols; ++sx) {
                    int px = x + sx;
                    int hal_active_px = 0;
                    uint8_t ir_r = 0u, ir_g = 0u, ir_b = 0u;
                    uint8_t h_r = 0u, h_g = 0u, h_b = 0u;
                    fp_interp_t xc;

                    if (use_hal) {
                        xc = x_coords[px];
                        if (nz_r[sy][xc.i0]) {
                            uint32_t wx = xc.w;
                            uint32_t iwx = 256u - wx;
                            uint32_t iwy = iwy_v[sy];
                            uint32_t wy = wy_v[sy];
                            uint32_t w00 = iwx * iwy;
                            uint32_t w10 = wx * iwy;
                            uint32_t w01 = iwx * wy;
                            uint32_t w11 = wx * wy;
                            int x0_3 = xc.i0 * 3;
                            int x1_3 = xc.i1 * 3;

                            const uint8_t *ir0 = ir_r0[sy];
                            const uint8_t *ir1 = ir_r1[sy];
                            const uint8_t *hr0 = h_r0[sy];
                            const uint8_t *hr1 = h_r1[sy];

                            ir_r = (uint8_t)((ir0[x0_3] * w00 + ir0[x1_3] * w10 + ir1[x0_3] * w01 + ir1[x1_3] * w11 + 32768u) >> 16);
                            ir_g = (uint8_t)((ir0[x0_3 + 1] * w00 + ir0[x1_3 + 1] * w10 + ir1[x0_3 + 1] * w01 + ir1[x1_3 + 1] * w11 + 32768u) >> 16);
                            ir_b = (uint8_t)((ir0[x0_3 + 2] * w00 + ir0[x1_3 + 2] * w10 + ir1[x0_3 + 2] * w01 + ir1[x1_3 + 2] * w11 + 32768u) >> 16);

                            h_r = (uint8_t)((hr0[x0_3] * w00 + hr0[x1_3] * w10 + hr1[x0_3] * w01 + hr1[x1_3] * w11 + 32768u) >> 16);
                            h_g = (uint8_t)((hr0[x0_3 + 1] * w00 + hr0[x1_3 + 1] * w10 + hr1[x0_3 + 1] * w01 + hr1[x1_3 + 1] * w11 + 32768u) >> 16);
                            h_b = (uint8_t)((hr0[x0_3 + 2] * w00 + hr0[x1_3 + 2] * w10 + hr1[x0_3 + 2] * w01 + hr1[x1_3 + 2] * w11 + 32768u) >> 16);

                            if ((ir_r | ir_g | ir_b | h_r | h_g | h_b) != 0u) {
                                hal_active_px = 1;
                            }
                        }
                    }

                    if (!hal_active_px && !gp) {
                        sum_cb += orig_cb;
                        sum_cr += orig_cr;
                        n_px++;
                        continue;
                    }

                    uint8_t r, g, b;
                    ycbcr_to_rgb_bt601(yrow[px], orig_cb, orig_cr, &r, &g, &b);

                    if (hal_active_px) {
                        halation_composite_pixel(
                            hp, hal_impact_u8, r, g, b,
                            ir_r, ir_g, ir_b, h_r, h_g, h_b,
                            &r, &g, &b
                        );
                    }

                    if (gp) {
                        uint32_t xq = x_q8_lut[px];
                        uint32_t x0 = (xq >> 8) & 255u;
                        uint32_t x1 = (x0 + 1u) & 255u;
                        uint32_t fx = xq & 255u;
                        uint32_t ifx = 256u - fx;

                        uint8_t g_r = (uint8_t)((gr_row[x0] * ifx + gr_row[x1] * fx + 128u) >> 8);
                        uint8_t g_g = g_r;
                        uint8_t g_b = g_r;
                        if (grain_color > 0u) {
                            uint32_t gx0 = (x0 + 97u) & 255u, gx1 = (gx0 + 1u) & 255u;
                            uint32_t bx0 = (x0 + 193u) & 255u, bx1 = (bx0 + 1u) & 255u;
                            uint8_t raw_g = (uint8_t)((gg_row[gx0] * ifx + gg_row[gx1] * fx + 128u) >> 8);
                            uint8_t raw_b = (uint8_t)((gb_row[bx0] * ifx + gb_row[bx1] * fx + 128u) >> 8);
                            g_g = mix_u8(g_r, raw_g, grain_color);
                            g_b = mix_u8(g_r, raw_b, grain_color);
                        }

                        uint32_t luma = (54u * r + 183u * g + 18u * b + 128u) >> 8;
                        if (luma > 255u) luma = 255u;
                        uint32_t w_tot = (uint32_t)gp->shd_w[luma] + (uint32_t)gp->mid_w[luma] + (uint32_t)gp->hi_w[luma];
                        if (w_tot > 255u) w_tot = 255u;

                        int32_t sr = (r < 128u) ? (int32_t)r : (int32_t)(255u - r);
                        int32_t sg = (g < 128u) ? (int32_t)g : (int32_t)(255u - g);
                        int32_t sb = (b < 128u) ? (int32_t)b : (int32_t)(255u - b);

                        r = fp_clamp_u8((int32_t)r + (((int32_t)g_r - 128) * (int32_t)w_tot * sr >> 16));
                        g = fp_clamp_u8((int32_t)g + (((int32_t)g_g - 128) * (int32_t)w_tot * sg >> 16));
                        b = fp_clamp_u8((int32_t)b + (((int32_t)g_b - 128) * (int32_t)w_tot * sb >> 16));
                    }

                    uint8_t oy, ocb, ocr;
                    rgb_to_ycbcr_bt601(r, g, b, &oy, &ocb, &ocr);
                    yrow[px] = oy;
                    sum_cb += ocb;
                    sum_cr += ocr;
                    any_changed = 1;
                    n_px++;
                }
            }

            if (any_changed && n_px > 0u) {
                if (n_px == 4u) {
                    crow[x]     = (uint8_t)((sum_cb + 2u) >> 2);
                    crow[x + 1] = (uint8_t)((sum_cr + 2u) >> 2);
                } else if (n_px == 2u) {
                    crow[x]     = (uint8_t)((sum_cb + 1u) >> 1);
                    crow[x + 1] = (uint8_t)((sum_cr + 1u) >> 1);
                } else {
                    crow[x]     = (uint8_t)sum_cb;
                    crow[x + 1] = (uint8_t)sum_cr;
                }
            }
        }
    }
}

#define FP_WORKERS_MAX   3
#define FP_BATCH_MCUS    8
#define FP_WORKER_STACK  8192u

typedef struct {
    volatile int32_t ctid[FP_WORKERS_MAX];
    volatile int32_t job_seq;
    volatile int32_t workers_done;
    volatile int32_t quit;
    int              n_workers;

    uint8_t *y_strip;
    uint8_t *cbcr_strip;
    size_t   y_row_bytes;
    size_t   c_row_bytes;
    int      pw;
    int      width;
    int      height;
    int      is_420;
    int      mcu_h;
    int      dw;
    const fp_hal_preset_t *hp;
    uint8_t  hal_impact_u8;
    const uint8_t *ir_plane;
    const uint8_t *hal_plane;
    const uint8_t *hal_nz;
    const fp_interp_t *x_coords;
    const fp_interp_t *y_coords;
    const fp_grain_preset_t *gp;
    uint32_t step_q16;
    uint32_t oy_q16;
    const uint32_t *x_q8_lut;
    const uint8_t *tile;

    int      batch_my0;
    int      batch_cnt;
} fp_pool_t;

typedef struct {
    fp_pool_t *pool;
    int        wid;
} fp_worker_arg_t;

static void fp_process_batch_slice(fp_pool_t *p, int tid, int stride) {
    for (int b = tid; b < p->batch_cnt; b += stride) {
        int my = p->batch_my0 + b;
        int y_start = my * p->mcu_h;
        int strip_h = p->height - y_start;
        if (strip_h > p->mcu_h) strip_h = p->mcu_h;
        if (strip_h > 0) {
            process_strip_post(
                p->y_strip + (size_t)b * p->y_row_bytes, p->pw,
                p->cbcr_strip + (size_t)b * p->c_row_bytes, p->pw,
                y_start, strip_h, p->width, p->is_420,
                p->dw, p->hp, p->hal_impact_u8, p->ir_plane, p->hal_plane, p->hal_nz,
                p->x_coords, p->y_coords,
                p->gp, p->step_q16, p->oy_q16, p->x_q8_lut, p->tile
            );
        }
    }
}

static void fp_worker_main(void *arg) {
    const fp_worker_arg_t *wa = (const fp_worker_arg_t *)arg;
    fp_pool_t *p = wa->pool;
    int tid = wa->wid + 1;
    int32_t seen_seq = 0;

    for (;;) {
        int32_t seq;
        for (;;) {
            seq = __atomic_load_n(&p->job_seq, __ATOMIC_ACQUIRE);
            if (seq != seen_seq) break;
            fp_futex_wait(&p->job_seq, seen_seq, 128 /* FUTEX_WAIT_PRIVATE */);
        }
        seen_seq = seq;
        __sync_synchronize();
        if (__atomic_load_n(&p->quit, __ATOMIC_ACQUIRE)) break;

        fp_process_batch_slice(p, tid, 1 + p->n_workers);

        __sync_synchronize();
        int32_t done = __atomic_add_fetch(&p->workers_done, 1, __ATOMIC_ACQ_REL);
        if (done == p->n_workers) {
            fp_futex_wake(&p->workers_done, 1, 129 /* FUTEX_WAKE_PRIVATE */);
        }
    }
}

static void log_skip(struct fp_bss_state *s, const char *tag, uint32_t v0, uint32_t v1) {
    s->frames_skip++;
    lbuf_t l;
    l.n = 0;
    lb_str(&l, "fp_skip ");
    lb_str(&l, tag);
    lb_ch(&l, ' ');
    lb_hex(&l, v0, 8);
    lb_ch(&l, ':');
    lb_hex(&l, v1, 8);
    lb_ch(&l, '\n');
    lb_flush(&l);
}

/* ---- MPR::StillInfo -> Main JPEG BufferArea discovery & streaming post-process ---- */
static void process_still_info(uint8_t *still_info) {
    struct fp_bss_state *s = FP_BSS;
    if (!s->active || s->magic != FP_MAGIC) return;

    uint8_t hal_idx   = s->cfg[0];
    uint8_t hal_mix   = s->cfg[1];
    uint8_t grain_idx = s->cfg[2];
    uint8_t qual_idx  = s->cfg[3];
    if (hal_idx >= 4u && grain_idx >= 4u) return;

    int si_ok = (still_info && (((uintptr_t)still_info & 3u) == 0u) &&
                 fp_range_mapped((uintptr_t)still_info, 0xC4u));
    if (!si_ok) {
        if (qual_idx == 3u) {
            log_skip(s, "badsi", (uint32_t)(uintptr_t)still_info, 0u);
        }
        return;
    }

    uint32_t t0_ms = fp_now_ms();

    /* Main MPR::JpgInfoArea sits at still_info + 0x08; BufferArea slots at +0x80, +0x90, +0xB0. */
    uint8_t *jpg_area = still_info + 0x08u;
    static const uint32_t SLOT_OFFS[3] = { 0x80u, 0x90u, 0xB0u };

    uint32_t *best_slot = NULL;
    int best_slot_idx = -1;
    fp_jpeg_info_t best_info = { 0, 0, 0, 0, 0 };
    uint64_t best_score = 0u;
    uint16_t slot_sig[3] = { 0u, 0u, 0u };

    lbuf_t prb;
    prb.n = 0;
    if (qual_idx == 3u) {
        lb_str(&prb, "fp_prb si=");
        lb_hex(&prb, (uint32_t)(uintptr_t)still_info, 8);
    }

    uint32_t prb_va[3] = { 0u, 0u, 0u };
    for (int i = 0; i < 3; ++i) {
        uint32_t *slot = (uint32_t *)(jpg_area + SLOT_OFFS[i]);
        uint32_t vaddr = slot[0];
        uint32_t size = slot[1];
        prb_va[i] = vaddr;
        int in_range = (vaddr >= 0x01000000u && size >= 512u && size <= 28u * 1024u * 1024u);
        int mapped = in_range ? fp_range_mapped((uintptr_t)vaddr, (size_t)size) : 0;
        if (mapped) {
            const uint8_t *sbuf = (const uint8_t *)(uintptr_t)vaddr;
            slot_sig[i] = (uint16_t)(((uint16_t)sbuf[0] << 8) | sbuf[1]);
        }
        if (qual_idx == 3u) {
            lb_ch(&prb, ' ');
            lb_ch(&prb, (char)('0' + i));
            lb_ch(&prb, ':');
            lb_hex(&prb, vaddr, 8);
            lb_ch(&prb, ':');
            lb_hex(&prb, size, 8);
            lb_ch(&prb, ':');
            lb_ch(&prb, mapped ? '1' : '0');
            if (mapped) {
                const uint8_t *pbuf = (const uint8_t *)(uintptr_t)vaddr;
                fp_jpeg_info_t pinfo;
                if (fp_jpeg_probe(pbuf, size, &pinfo) == 0) {
                    lb_ch(&prb, ':');
                    lb_hex(&prb, (uint32_t)pinfo.width, 4);
                    lb_ch(&prb, 'x');
                    lb_hex(&prb, (uint32_t)pinfo.height, 4);
                } else {
                    lb_str(&prb, ":no");
                    lb_hex(&prb, slot_sig[i], 4);
                }
            }
            continue;
        }
        if (!mapped) continue;
        const uint8_t *buf = (const uint8_t *)(uintptr_t)vaddr;
        fp_jpeg_info_t info;
        if (fp_jpeg_probe(buf, size, &info) != 0) continue;
        if (info.width < 64 || info.height < 64 || info.width > 8192 || info.height > 8192) continue;
        uint64_t score = ((uint64_t)(uint32_t)info.width * (uint64_t)(uint32_t)info.height << 32) | (uint64_t)size;
        if (score > best_score) {
            best_score = score;
            best_slot = slot;
            best_slot_idx = i;
            best_info = info;
        }
    }

    if (qual_idx == 3u) {
        uint32_t target_va = prb_va[1] ? prb_va[1] : prb_va[0];
        if (target_va) append_vma_for_addr(&prb, target_va);
        lb_ch(&prb, '\n');
        lb_flush(&prb);
        return;
    }

    if (!best_slot) {
        log_skip(s, "noslot", (uint32_t)(uintptr_t)still_info, 0u);
        return;
    }

    uint8_t *orig_buf = (uint8_t *)(uintptr_t)best_slot[0];
    uint32_t orig_size = best_slot[1];
    int W = best_info.width;
    int H = best_info.height;
    int is_420 = best_info.is_420;
    int pw = (W + 15) & ~15;

    const fp_hal_preset_t *hp = (hal_idx < 4u) ? &fp_rodata_blob.hal[hal_idx] : NULL;
    uint8_t impact_u8 = 0u;
    if (hp) {
        uint32_t imp = hp->impact_u8;
        if (hal_mix == 1u) imp = (imp * 3u + 2u) >> 2;
        else if (hal_mix == 2u) imp = (imp + 1u) >> 1;
        else if (hal_mix == 3u) { imp = (imp * 5u + 2u) >> 2; if (imp > 255u) imp = 255u; }
        impact_u8 = (uint8_t)imp;
        if (impact_u8 == 0u) hp = NULL;
    }
    const fp_grain_preset_t *gp = (grain_idx < 4u) ? &fp_rodata_blob.grain[grain_idx] : NULL;
    if (!hp && !gp) return;

    int ds = 0, dw = 0, dh = 0;
    size_t plane_bytes = 0u, hal_nz_bytes = 0u, x_coords_bytes = 0u, y_coords_bytes = 0u, acc_bytes = 0u;
    if (hp) {
        int max_dim = W > H ? W : H;
        ds = (int)fp_udiv_ceil((uint32_t)max_dim, 512u);
        if (ds < 1) ds = 1;
        dw = (int)fp_udiv_ceil((uint32_t)W, (uint32_t)ds);
        dh = (int)fp_udiv_ceil((uint32_t)H, (uint32_t)ds);
        if (dw < 1) dw = 1;
        if (dh < 1) dh = 1;
        plane_bytes = ((size_t)dw * (size_t)dh * 3u + 15u) & ~(size_t)15u;
        hal_nz_bytes = ((size_t)dw * (size_t)dh + 15u) & ~(size_t)15u;
        x_coords_bytes = ((size_t)W * sizeof(fp_interp_t) + 15u) & ~(size_t)15u;
        y_coords_bytes = ((size_t)H * sizeof(fp_interp_t) + 15u) & ~(size_t)15u;
        acc_bytes = ((size_t)dw * 4u * sizeof(uint32_t) + 15u) & ~(size_t)15u;
    }

    size_t ctx_dec_bytes  = (sizeof(fp_jdec_ctx_t) + 15u) & ~(size_t)15u;
    size_t ctx_enc_bytes  = (sizeof(fp_jenc_ctx_t) + 15u) & ~(size_t)15u;
    size_t pool_bytes     = (sizeof(fp_pool_t) + 15u) & ~(size_t)15u;
    size_t wargs_bytes    = (sizeof(fp_worker_arg_t) * FP_WORKERS_MAX + 15u) & ~(size_t)15u;
    size_t wstacks_bytes  = (size_t)FP_WORKERS_MAX * FP_WORKER_STACK;
    size_t y_strip_bytes  = ((size_t)FP_BATCH_MCUS * (size_t)pw * 16u + 15u) & ~(size_t)15u;
    size_t c_strip_bytes  = ((size_t)FP_BATCH_MCUS * (size_t)pw * 8u + 15u) & ~(size_t)15u;
    size_t x_q8_bytes     = gp ? (((size_t)W * sizeof(uint32_t) + 15u) & ~(size_t)15u) : 0u;
    size_t out_bytes      = ((size_t)orig_size + 15u) & ~(size_t)15u;

    size_t total_mmap = ctx_dec_bytes + ctx_enc_bytes + pool_bytes + wargs_bytes + wstacks_bytes
                      + y_strip_bytes + c_strip_bytes + x_q8_bytes + 3u * plane_bytes
                      + hal_nz_bytes + x_coords_bytes + y_coords_bytes + acc_bytes + out_bytes;
    total_mmap = (total_mmap + 4095u) & ~(size_t)4095u;

    uint8_t *mem = (uint8_t *)fp_mmap_anon(total_mmap);
    if (!mem) {
        log_skip(s, "oom", (uint32_t)total_mmap, orig_size);
        return;
    }

    size_t off = 0u;
    fp_jdec_ctx_t *jdec      = (fp_jdec_ctx_t *)(mem + off); off += ctx_dec_bytes;
    fp_jenc_ctx_t *jenc      = (fp_jenc_ctx_t *)(mem + off); off += ctx_enc_bytes;
    fp_pool_t *pool          = (fp_pool_t *)(mem + off);     off += pool_bytes;
    fp_worker_arg_t *wargs   = (fp_worker_arg_t *)(mem + off); off += wargs_bytes;
    uint8_t *w_stacks        = mem + off; off += wstacks_bytes;
    uint8_t *y_strip         = mem + off; off += y_strip_bytes;
    uint8_t *cbcr_strip      = mem + off; off += c_strip_bytes;
    uint32_t *x_q8_lut       = gp ? (uint32_t *)(mem + off) : NULL; off += x_q8_bytes;

    uint8_t *ds_contrast  = hp ? (mem + off) : NULL; off += plane_bytes;
    uint8_t *resampled    = hp ? (mem + off) : NULL; off += plane_bytes;
    uint8_t *blur_tmp     = hp ? (mem + off) : NULL; off += plane_bytes;
    uint8_t *hal_nz       = hp ? (mem + off) : NULL; off += hal_nz_bytes;
    fp_interp_t *x_coords = hp ? (fp_interp_t *)(mem + off) : NULL; off += x_coords_bytes;
    fp_interp_t *y_coords = hp ? (fp_interp_t *)(mem + off) : NULL; off += y_coords_bytes;
    uint32_t *acc         = hp ? (uint32_t *)(mem + off) : NULL; off += acc_bytes;
    uint8_t *out_jpg      = mem + off;

    fp_jpeg_info_t dec_info;
    if (hp) {
        if (fp_jdec_begin(orig_buf, orig_size, jdec, &dec_info) != 0) {
            fp_munmap_anon(mem, total_mmap);
            log_skip(s, "dec1", orig_size, 0u);
            return;
        }
        fp_memset(acc, 0u, (size_t)dw * 4u * sizeof(uint32_t));
        fp_ds_state_t dst;
        dst.ds = ds;
        dst.dw = dw;
        dst.dh = dh;
        dst.ds2 = (uint32_t)(ds * ds);
        dst.recip_ds2 = fp_udiv((1u << 24) + dst.ds2 - 1u, dst.ds2);
        dst.cur_dy = 0;
        dst.y_in_cell = 0;

        int dc_only = (ds >= 8) ? 1 : 0;
        for (int my = 0; my < jdec->mcus_y; ++my) {
            if (fp_jdec_decode_mcu_row(jdec, my, y_strip, pw, cbcr_strip, pw, dc_only) != 0) {
                fp_munmap_anon(mem, total_mmap);
                log_skip(s, "row1", (uint32_t)my, orig_size);
                return;
            }
            int y_start = my * jdec->mcu_h;
            int strip_h = H - y_start;
            if (strip_h > jdec->mcu_h) strip_h = jdec->mcu_h;
            if (strip_h > 0) {
                ds_accumulate_strip(
                    y_strip, pw, cbcr_strip, pw, y_start, strip_h,
                    W, H, is_420, dc_only, hp, ds_contrast, acc, &dst
                );
            }
        }

        int dw_max = dw > dh ? dw : dh;
        int rl = 0;
        if (hp->local_diff_q8 > 0u) {
            uint32_t num = (uint32_t)hp->local_diff_q8 * 24u * (uint32_t)dw_max + 128u * 1080u;
            rl = (int)fp_udiv(num, 256u * 1080u);
            if (rl < 1) rl = 1;
        }
        int rg = rl;
        if (hp->global_diff_q8 > 0u) {
            uint32_t num = (uint32_t)hp->global_diff_q8 * 72u * (uint32_t)dw_max + 128u * 1080u;
            int g = (int)fp_udiv(num, 256u * 1080u);
            rg = g > (rl + 1) ? g : (rl + 1);
        }

        box_blur_rgb(ds_contrast, resampled, blur_tmp, dw, dh, rl);
        /* Safe alias: box_blur_rgb completes horizontal read of src before writing dst. */
        box_blur_rgb(ds_contrast, ds_contrast, blur_tmp, dw, dh, rg);
        int dw3 = dw * 3;
        for (int hy = 0; hy < dh; ++hy) {
            int y1 = (hy + 1 < dh) ? (hy + 1) : hy;
            const uint8_t *ir0 = resampled   + (size_t)hy * (size_t)dw3;
            const uint8_t *ir1 = resampled   + (size_t)y1 * (size_t)dw3;
            const uint8_t *h0  = ds_contrast + (size_t)hy * (size_t)dw3;
            const uint8_t *h1  = ds_contrast + (size_t)y1 * (size_t)dw3;
            uint8_t *nz = hal_nz + (size_t)hy * (size_t)dw;
            for (int hx = 0; hx < dw; ++hx) {
                int x1 = (hx + 1 < dw) ? (hx + 1) : hx;
                int i0 = hx * 3;
                int i1 = x1 * 3;
                uint8_t any = (uint8_t)(ir0[i0] | ir0[i0 + 1] | ir0[i0 + 2]
                                      | ir0[i1] | ir0[i1 + 1] | ir0[i1 + 2]
                                      | ir1[i0] | ir1[i0 + 1] | ir1[i0 + 2]
                                      | ir1[i1] | ir1[i1 + 1] | ir1[i1 + 2]
                                      | h0[i0]  | h0[i0 + 1]  | h0[i0 + 2]
                                      | h0[i1]  | h0[i1 + 1]  | h0[i1 + 2]
                                      | h1[i0]  | h1[i0 + 1]  | h1[i0 + 2]
                                      | h1[i1]  | h1[i1 + 1]  | h1[i1 + 2]);
                nz[hx] = any ? 1u : 0u;
            }
        }
        build_upsample_coords(W, dw, x_coords);
        build_upsample_coords(H, dh, y_coords);
    }

    uint32_t step_q16 = 0u, oy_q16 = 0u;
    if (gp) {
        s->seed_counter++;
        uint32_t denom = ((uint32_t)(W > 1 ? W : 1) * (uint32_t)gp->eff_scale_q8 + 128u) >> 8;
        if (denom < 1u) denom = 1u;
        uint32_t num = 256u * 5200u * 115u; /* 0.45 * 256 = 115.2 */
        step_q16 = fp_udiv(num + (denom >> 1), denom);
        if (step_q16 < 4096u) step_q16 = 4096u;
        if (step_q16 > (128u << 16)) step_q16 = 128u << 16;

        uint32_t seed_h = hash2d_u32(s->seed_counter, s->seed_counter * 0x9E3779B9u);
        uint32_t ax = seed_h & 0x00FFFFFFu;
        oy_q16 = (seed_h >> 8) & 0x00FFFFFFu;
        for (int x = 0; x < W; ++x) {
            x_q8_lut[x] = ax >> 8;
            ax += step_q16;
        }
    }

    fp_memset(pool, 0u, sizeof(*pool));
    pool->y_strip = y_strip;
    pool->cbcr_strip = cbcr_strip;
    pool->mcu_h = is_420 ? 16 : 8;
    pool->y_row_bytes = (size_t)pw * (size_t)pool->mcu_h;
    pool->c_row_bytes = (size_t)pw * 8u;
    pool->pw = pw;
    pool->width = W;
    pool->height = H;
    pool->is_420 = is_420;
    pool->dw = dw;
    pool->hp = hp;
    pool->hal_impact_u8 = impact_u8;
    pool->ir_plane = resampled;
    pool->hal_plane = ds_contrast;
    pool->hal_nz = hal_nz;
    pool->x_coords = x_coords;
    pool->y_coords = y_coords;
    pool->gp = gp;
    pool->step_q16 = step_q16;
    pool->oy_q16 = oy_q16;
    pool->x_q8_lut = x_q8_lut;
    pool->tile = s->tile;

    for (int w = 0; w < FP_WORKERS_MAX; ++w) {
        wargs[w].pool = pool;
        wargs[w].wid = w;
        pool->ctid[w] = 1;
        __sync_synchronize();
        uint8_t *stk_top = w_stacks + (size_t)(w + 1) * FP_WORKER_STACK;
        if (fp_spawn_thread(stk_top, fp_worker_main, &wargs[w], (int32_t *)&pool->ctid[w]) <= 0) {
            pool->ctid[w] = 0;
            break;
        }
        pool->n_workers = w + 1;
    }

    int base_q = (qual_idx == 1u) ? 96 : ((qual_idx == 2u) ? 85 : 92);
    int q_tries[3] = { base_q, base_q > 78 ? 78 : base_q - 8, 65 };
    size_t new_size = 0u;
    for (int t = 0; t < 3; ++t) {
        if (fp_jdec_begin(orig_buf, orig_size, jdec, &dec_info) != 0) break;
        if (fp_jenc_begin(W, H, is_420, q_tries[t], best_info.has_soi, orig_buf, orig_size, out_jpg, orig_size, jenc) != 0) {
            continue;
        }
        int ok = 1;
        for (int my0 = 0; my0 < jdec->mcus_y; my0 += FP_BATCH_MCUS) {
            int b_cnt = jdec->mcus_y - my0;
            if (b_cnt > FP_BATCH_MCUS) b_cnt = FP_BATCH_MCUS;

            for (int b = 0; b < b_cnt; ++b) {
                if (fp_jdec_decode_mcu_row(
                        jdec, my0 + b,
                        y_strip + (size_t)b * pool->y_row_bytes, pw,
                        cbcr_strip + (size_t)b * pool->c_row_bytes, pw, 0) != 0) {
                    ok = 0;
                    break;
                }
            }
            if (!ok) break;

            pool->batch_my0 = my0;
            pool->batch_cnt = b_cnt;
            if (pool->n_workers > 0 && b_cnt > 1) {
                __atomic_store_n(&pool->workers_done, 0, __ATOMIC_RELEASE);
                __sync_synchronize();
                int32_t next_seq = pool->job_seq + 1;
                __atomic_store_n(&pool->job_seq, next_seq, __ATOMIC_RELEASE);
                fp_futex_wake(&pool->job_seq, pool->n_workers, 129 /* FUTEX_WAKE_PRIVATE */);

                fp_process_batch_slice(pool, 0, 1 + pool->n_workers);

                for (;;) {
                    int32_t done = __atomic_load_n(&pool->workers_done, __ATOMIC_ACQUIRE);
                    if (done >= pool->n_workers) break;
                    fp_futex_wait(&pool->workers_done, done, 128 /* FUTEX_WAIT_PRIVATE */);
                }
            } else {
                fp_process_batch_slice(pool, 0, 1);
            }

            for (int b = 0; b < b_cnt; ++b) {
                if (fp_jenc_encode_mcu_row(
                        jenc,
                        y_strip + (size_t)b * pool->y_row_bytes, pw,
                        cbcr_strip + (size_t)b * pool->c_row_bytes, pw) != 0) {
                    ok = 0;
                    break;
                }
            }
            if (!ok) break;
        }
        if (ok) {
            size_t cand = fp_jenc_finish(jenc);
            if (cand > 0u && cand <= orig_size) {
                new_size = cand;
                break;
            }
        }
    }

    int n_workers_used = pool->n_workers;
    if (n_workers_used > 0) {
        __atomic_store_n(&pool->quit, 1, __ATOMIC_RELEASE);
        __sync_synchronize();
        __atomic_add_fetch(&pool->job_seq, 1, __ATOMIC_ACQ_REL);
        fp_futex_wake(&pool->job_seq, n_workers_used, 129 /* FUTEX_WAKE_PRIVATE */);
        for (int w = 0; w < n_workers_used; ++w) {
            for (;;) {
                int32_t tid = __atomic_load_n(&pool->ctid[w], __ATOMIC_ACQUIRE);
                if (tid == 0) break;
                fp_futex_wait(&pool->ctid[w], tid, 0 /* FUTEX_WAIT */);
            }
        }
    }

    if (new_size > 0u && new_size <= orig_size) {
        if (qual_idx != 4u) {
            fp_copy_aligned32(orig_buf, out_jpg, new_size);
            best_slot[1] = (uint32_t)new_size;
        }
        s->frames_ok++;
        s->last_in_wh = ((uint32_t)W << 16) | (uint32_t)H;
        s->last_orig_bytes = orig_size;
        s->last_out_bytes = (uint32_t)new_size;

        lbuf_t l;
        l.n = 0;
        lb_str(&l, (qual_idx == 4u) ? "fp_dry #" : "fp_jpg #");
        lb_hex(&l, s->frames_ok, 4);
        lb_str(&l, " wh=");
        lb_hex(&l, (uint32_t)W, 4);
        lb_ch(&l, 'x');
        lb_hex(&l, (uint32_t)H, 4);
        lb_str(&l, " sz=");
        lb_hex(&l, orig_size, 8);
        lb_str(&l, "->");
        lb_hex(&l, (uint32_t)new_size, 8);
        lb_str(&l, " mm=");
        lb_hex(&l, (uint32_t)total_mmap, 8);
        lb_str(&l, " cfg=");
        for (int i = 0; i < 4; ++i) lb_ch(&l, (char)('0' + (s->cfg[i] & 7u)));
        uint32_t dt_ms = fp_now_ms() - t0_ms;
        lb_str(&l, " nw=");
        lb_ch(&l, (char)('0' + (n_workers_used & 7)));
        lb_str(&l, " sl=");
        lb_ch(&l, (char)('0' + (best_slot_idx & 3)));
        lb_str(&l, " ms=");
        lb_hex(&l, dt_ms, 6);
        lb_str(&l, " sig=");
        lb_hex(&l, slot_sig[0], 4);
        lb_ch(&l, ',');
        lb_hex(&l, slot_sig[1], 4);
        lb_ch(&l, ',');
        lb_hex(&l, slot_sig[2], 4);
        lb_ch(&l, '\n');
        lb_flush(&l);
    } else {
        log_skip(s, "ovfl", orig_size, (uint32_t)total_mmap);
    }

    fp_munmap_anon(mem, total_mmap);
}

/* ---- SMOD v2 exported entry points ---- */
__attribute__((visibility("default"), used))
int fp_hook_write_still(void *this_ptr, void *param, uint8_t *still_info, uint32_t flags) {
    process_still_info(still_info);
    return fp_orig_write_still(this_ptr, param, still_info, flags);
}

__attribute__((visibility("default"), used))
int fp_hook_encode_still(void *this_ptr, uint32_t *param) {
    struct fp_bss_state *s = FP_BSS;
    if (s->active && s->magic == FP_MAGIC && param &&
        (((uintptr_t)param & 3u) == 0u) &&
        fp_range_mapped((uintptr_t)param, 32u)) {
        uint32_t raddr = param[0];
        lbuf_t l;
        l.n = 0;
        lb_str(&l, "fp_enc p=");
        lb_hex(&l, (uint32_t)(uintptr_t)param, 8);
        lb_str(&l, " r=");
        lb_hex(&l, raddr, 8);
        if (raddr >= 0x01000000u && ((raddr & 3u) == 0u) &&
            fp_range_mapped((uintptr_t)raddr, 64u)) {
            const uint32_t *reg = (const uint32_t *)(uintptr_t)raddr;
            lb_str(&l, " w0=");
            for (int i = 0; i < 6; ++i) {
                if (i > 0) lb_ch(&l, ',');
                lb_hex(&l, reg[i], 8);
            }
            lb_str(&l, " w9=");
            for (int i = 9; i < 12; ++i) {
                if (i > 9) lb_ch(&l, ',');
                lb_hex(&l, reg[i], 8);
            }
            uint32_t va = 0u;
            for (int i = 0; i < 16; ++i) {
                if (reg[i] >= 0x20000000u && reg[i] < 0x40000000u) {
                    va = reg[i];
                    break;
                }
            }
            if (!va && param[3] >= 0x10000000u) va = param[3];
            if (va) append_vma_for_addr(&l, va);
        }
        lb_ch(&l, '\n');
        lb_flush(&l);
    }
    return fp_orig_encode_still(this_ptr, param);
}

__attribute__((visibility("default"), used))
void fp_on(int on) {
    (void)on;
    struct fp_bss_state *s = FP_BSS;
    if (s->magic != FP_MAGIC) {
        init_grain_tile(s->tile);
        s->magic = FP_MAGIC;
    }
    s->active = 1u;

    lbuf_t l;
    l.n = 0;
    lb_str(&l, "fp_on cfg=");
    for (int i = 0; i < 4; ++i) lb_ch(&l, (char)('0' + (s->cfg[i] & 7u)));
    lb_ch(&l, '\n');
    lb_flush(&l);
}

__attribute__((visibility("default"), used))
void fp_off(int on) {
    (void)on;
    struct fp_bss_state *s = FP_BSS;
    s->active = 0u;

    lbuf_t l;
    l.n = 0;
    lb_str(&l, "fp_off ok=");
    lb_hex(&l, s->frames_ok, 4);
    lb_str(&l, " skip=");
    lb_hex(&l, s->frames_skip, 4);
    lb_ch(&l, '\n');
    lb_flush(&l);
}

__attribute__((visibility("default"), used))
void fp_cfg(int reason) {
    struct fp_bss_state *s = FP_BSS;
    lbuf_t l;
    l.n = 0;
    lb_str(&l, "fp_cfg rsn=");
    lb_hex(&l, (uint32_t)reason, 2);
    lb_str(&l, " cfg=");
    for (int i = 0; i < 4; ++i) lb_ch(&l, (char)('0' + (s->cfg[i] & 7u)));
    lb_ch(&l, '\n');
    lb_flush(&l);
}
