/* Freestanding, zero-libc, zero-float Baseline JPEG (SOF0, 8-bit, 3-component
 * YCbCr 4:2:2 / 4:2:0 / 4:4:4) decoder & encoder for ARMv7 Cortex-A5 SMOD v2 mods.
 *
 * Strictly English-only comments and identifiers.
 */
#ifndef BIONZ_FP_JPEG_H
#define BIONZ_FP_JPEG_H

#include <stddef.h>
#include <stdint.h>

#ifdef __GNUC__
#define FP_NO_LOOP_DIST __attribute__((optimize("no-tree-loop-distribute-patterns")))
#else
#define FP_NO_LOOP_DIST
#endif

static inline uint32_t fp_udivmod(uint32_t n, uint32_t d, uint32_t *rem_out) {
    if (d == 0u) {
        if (rem_out) *rem_out = 0u;
        return 0u;
    }
    if ((d & (d - 1u)) == 0u) {
        uint32_t sh = (uint32_t)__builtin_ctz(d);
        if (rem_out) *rem_out = n & (d - 1u);
        return n >> sh;
    }
    uint32_t q = 0u;
    uint32_t r = 0u;
    for (int i = 31; i >= 0; --i) {
        r = (r << 1) | ((n >> i) & 1u);
        if (r >= d) {
            r -= d;
            q |= (1u << i);
        }
    }
    if (rem_out) *rem_out = r;
    return q;
}

static inline uint32_t fp_udiv(uint32_t n, uint32_t d) {
    return fp_udivmod(n, d, NULL);
}

static inline uint32_t fp_udiv_ceil(uint32_t n, uint32_t d) {
    if (d == 0u || n == 0u) return 0u;
    return fp_udiv(n + d - 1u, d);
}

static FP_NO_LOOP_DIST void fp_memset(void *dst, uint8_t val, size_t n) {
    volatile uint8_t *p = (volatile uint8_t *)dst;
    for (size_t i = 0; i < n; ++i) {
        p[i] = val;
    }
}

static FP_NO_LOOP_DIST void fp_memcpy(void *dst, const void *src, size_t n) {
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    for (size_t i = 0; i < n; ++i) {
        d[i] = s[i];
    }
}

static FP_NO_LOOP_DIST void fp_copy_aligned32(uint8_t *dst, const uint8_t *src, size_t n) {
    if (!dst || !src || n == 0u) return;
    if ((((uintptr_t)dst ^ (uintptr_t)src) & 3u) == 0u) {
        size_t i = 0u;
        while (i < n && (((uintptr_t)(dst + i)) & 3u) != 0u) {
            dst[i] = src[i];
            i++;
        }
        uint32_t *d32 = (uint32_t *)(void *)(dst + i);
        const uint32_t *s32 = (const uint32_t *)(const void *)(src + i);
        size_t words = (n - i) >> 2;
        for (size_t w = 0u; w < words; ++w) {
            d32[w] = s32[w];
        }
        i += words << 2;
        while (i < n) {
            dst[i] = src[i];
            i++;
        }
    } else {
        fp_memcpy(dst, src, n);
    }
}

static FP_NO_LOOP_DIST void fp_zero_i32_64(int32_t coef[64]) {
    for (int i = 0; i < 64; ++i) {
        coef[i] = 0;
    }
}

typedef struct {
    int width;
    int height;
    int is_420;   /* 1 for 4:2:0, 0 for 4:2:2 (or 4:4:4 averaged to 4:2:2) */
    int src_mode; /* 0 = 4:4:4, 1 = 4:2:2, 2 = 4:2:0 */
    int has_soi;  /* 1 for full JFIF/EXIF with FF D8, 0 for headerless marker stream */
} fp_jpeg_info_t;

typedef struct {
    uint8_t bits[17];
    uint8_t values[256];
    int32_t maxcode[18];
    int32_t valoffset[18];
    uint8_t fast_sym[256];
    uint8_t fast_len[256];
    uint8_t valid;
} fp_huff_dec_t;

typedef struct {
    uint8_t id;
    uint8_t hsf;
    uint8_t vsf;
    uint8_t tq;
    uint8_t td;
    uint8_t ta;
} fp_comp_t;

typedef struct {
    uint16_t qtab[4][64];
    uint8_t qvalid[4];
    fp_huff_dec_t dc_huff[4];
    fp_huff_dec_t ac_huff[4];
    fp_comp_t comp[3];
    uint8_t scan_order[3];
    uint16_t restart_interval;
    int width;
    int height;
    int hmax;
    int vmax;
    int is_420;
    int src_mode;
    int has_soi;
    /* Bitstream reader & streaming MCU state */
    const uint8_t *src;
    size_t src_len;
    size_t pos;
    uint32_t bit_buf;
    int bit_cnt;
    int eoi_hit;
    int rst_seen;
    int32_t dc_pred[3];
    uint32_t mcu_cnt;
    uint32_t mcu_in_rst;
    int mcu_w;
    int mcu_h;
    int mcus_x;
    int mcus_y;
    size_t rbuf_base;
    size_t rbuf_end;
    uint8_t rbuf[4096] __attribute__((aligned(4)));
} fp_jdec_ctx_t;

typedef struct {
    uint16_t code[256];
    uint8_t len[256];
} fp_huff_enc_t;

typedef struct {
    uint8_t q_y[64];
    uint8_t q_c[64];
    uint32_t recip_y[64];
    uint32_t recip_c[64];
    fp_huff_enc_t dc_y;
    fp_huff_enc_t ac_y;
    fp_huff_enc_t dc_c;
    fp_huff_enc_t ac_c;
    uint8_t *dst;
    size_t cap;
    size_t pos;
    uint32_t bit_buf;
    int bit_cnt;
    int overflow;
    int width;
    int height;
    int is_420;
    int mcus_x;
    int mcus_y;
    int32_t dc_y_pred;
    int32_t dc_cb_pred;
    int32_t dc_cr_pred;
} fp_jenc_ctx_t;

static const uint8_t FP_ZIGZAG[64] = {
     0,  1,  8, 16,  9,  2,  3, 10,
    17, 24, 32, 25, 18, 11,  4,  5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13,  6,  7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63
};

static const uint8_t FP_STD_QY[64] = {
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68,109,103, 77,
    24, 35, 55, 64, 81,104,113, 92,
    49, 64, 78, 87,103,121,120,101,
    72, 92, 95, 98,112,100,103, 99
};

static const uint8_t FP_STD_QC[64] = {
    17, 18, 24, 47, 99, 99, 99, 99,
    18, 21, 26, 66, 99, 99, 99, 99,
    24, 26, 56, 99, 99, 99, 99, 99,
    47, 66, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99
};

static const uint8_t FP_DC_LUM_BITS[17] = {
    0, 0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0
};
static const uint8_t FP_DC_LUM_VALS[12] = {
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
};

static const uint8_t FP_DC_CHR_BITS[17] = {
    0, 0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0
};
static const uint8_t FP_DC_CHR_VALS[12] = {
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
};

static const uint8_t FP_AC_LUM_BITS[17] = {
    0, 0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7d
};
static const uint8_t FP_AC_LUM_VALS[162] = {
    0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12,
    0x21, 0x31, 0x41, 0x06, 0x13, 0x51, 0x61, 0x07,
    0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xa1, 0x08,
    0x23, 0x42, 0xb1, 0xc1, 0x15, 0x52, 0xd1, 0xf0,
    0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0a, 0x16,
    0x17, 0x18, 0x19, 0x1a, 0x25, 0x26, 0x27, 0x28,
    0x29, 0x2a, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39,
    0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49,
    0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
    0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69,
    0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79,
    0x7a, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
    0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98,
    0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7,
    0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6,
    0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5,
    0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4,
    0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xe1, 0xe2,
    0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea,
    0xf1, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
    0xf9, 0xfa
};

static const uint8_t FP_AC_CHR_BITS[17] = {
    0, 0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77
};
static const uint8_t FP_AC_CHR_VALS[162] = {
    0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21,
    0x31, 0x06, 0x12, 0x41, 0x51, 0x07, 0x61, 0x71,
    0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91,
    0xa1, 0xb1, 0xc1, 0x09, 0x23, 0x33, 0x52, 0xf0,
    0x15, 0x62, 0x72, 0xd1, 0x0a, 0x16, 0x24, 0x34,
    0xe1, 0x25, 0xf1, 0x17, 0x18, 0x19, 0x1a, 0x26,
    0x27, 0x28, 0x29, 0x2a, 0x35, 0x36, 0x37, 0x38,
    0x39, 0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48,
    0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58,
    0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68,
    0x69, 0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78,
    0x79, 0x7a, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
    0x88, 0x89, 0x8a, 0x92, 0x93, 0x94, 0x95, 0x96,
    0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5,
    0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4,
    0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3,
    0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2,
    0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda,
    0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9,
    0xea, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
    0xf9, 0xfa
};

static inline uint8_t fp_clamp_u8(int32_t v) {
    if (v < 0) return 0u;
    if (v > 255) return 255u;
    return (uint8_t)v;
}

static inline uint16_t fp_be16(const uint8_t *p) {
    return (uint16_t)(((uint16_t)p[0] << 8) | p[1]);
}

/* ---- Decoder: Header probe & Huffman builder ---- */
static int fp_jdec_parse_sof0(const uint8_t *seg, size_t len, fp_jpeg_info_t *info, fp_comp_t *comp_out) {
    if (len < 6u) return -1;
    uint8_t prec = seg[0];
    uint16_t h = fp_be16(seg + 1);
    uint16_t w = fp_be16(seg + 3);
    uint8_t ncomp = seg[5];
    if (prec != 8u || ncomp != 3u || w == 0u || h == 0u) return -1;
    if (len < (size_t)(6u + ncomp * 3u)) return -1;

    fp_comp_t c[3];
    for (int i = 0; i < 3; ++i) {
        c[i].id = seg[6 + i * 3];
        uint8_t hv = seg[6 + i * 3 + 1];
        c[i].hsf = (uint8_t)(hv >> 4);
        c[i].vsf = (uint8_t)(hv & 0x0Fu);
        c[i].tq = seg[6 + i * 3 + 2];
        c[i].td = 0u;
        c[i].ta = 0u;
        if (c[i].hsf == 0u || c[i].vsf == 0u || c[i].tq >= 4u) return -1;
    }

    int src_mode = -1;
    int is_420 = 0;
    if (c[0].hsf == 2u && c[0].vsf == 2u && c[1].hsf == 1u && c[1].vsf == 1u && c[2].hsf == 1u && c[2].vsf == 1u) {
        src_mode = 2;
        is_420 = 1;
    } else if (c[0].hsf == 2u && c[0].vsf == 1u && c[1].hsf == 1u && c[1].vsf == 1u && c[2].hsf == 1u && c[2].vsf == 1u) {
        src_mode = 1;
        is_420 = 0;
    } else if (c[0].hsf == 1u && c[0].vsf == 1u && c[1].hsf == 1u && c[1].vsf == 1u && c[2].hsf == 1u && c[2].vsf == 1u) {
        src_mode = 0;
        is_420 = 0;
    } else {
        return -1;
    }

    if (info) {
        info->width = (int)w;
        info->height = (int)h;
        info->is_420 = is_420;
        info->src_mode = src_mode;
        info->has_soi = 1;
    }
    if (comp_out) {
        for (int i = 0; i < 3; ++i) {
            comp_out[i].id  = c[i].id;
            comp_out[i].hsf = c[i].hsf;
            comp_out[i].vsf = c[i].vsf;
            comp_out[i].tq  = c[i].tq;
            comp_out[i].td  = c[i].td;
            comp_out[i].ta  = c[i].ta;
        }
    }
    return 0;
}

static int fp_jpeg_probe(const uint8_t *buf, size_t len, fp_jpeg_info_t *info) {
    if (!buf || len < 32u || buf[0] != 0xFFu) return -1;
    int has_soi = 0;
    size_t pos = 0u;
    if (buf[1] == 0xD8u) {
        has_soi = 1;
        pos = 2u;
    } else if (buf[1] == 0xDBu || buf[1] == 0xC0u || buf[1] == 0xC4u || buf[1] == 0xDDu ||
               (buf[1] >= 0xE0u && buf[1] <= 0xEFu)) {
        has_soi = 0;
        pos = 0u;
    } else {
        return -1;
    }

    size_t scan_lim = len < 262144u ? len : 262144u;
    while (pos + 2u <= scan_lim) {
        while (pos < scan_lim && buf[pos] != 0xFFu) pos++;
        while (pos < scan_lim && buf[pos] == 0xFFu) pos++;
        if (pos >= scan_lim) break;
        uint8_t m = buf[pos++];
        if (m == 0x00u || (m >= 0xD0u && m <= 0xD8u)) continue;
        if (m == 0xD9u || m == 0xDAu) break;
        if (pos + 2u > len) break;
        uint16_t seglen = fp_be16(buf + pos);
        if (seglen < 2u || pos + seglen > len) break;
        if (m == 0xC0u) {
            if (fp_jdec_parse_sof0(buf + pos + 2u, (size_t)seglen - 2u, info, NULL) == 0) {
                if (info) info->has_soi = has_soi;
                return 0;
            }
            return -1;
        }
        if (m >= 0xC1u && m <= 0xCFu && m != 0xC4u && m != 0xCCu) {
            return -1; /* Progressive or unsupported SOF */
        }
        pos += seglen;
    }
    return -1;
}

static void fp_jdec_build_huff(fp_huff_dec_t *h) {
    fp_memset(h->fast_len, 0u, sizeof(h->fast_len));
    fp_memset(h->fast_sym, 0u, sizeof(h->fast_sym));
    int32_t code = 0;
    int idx = 0;
    for (int l = 1; l <= 16; ++l) {
        int cnt = h->bits[l];
        if (cnt == 0) {
            h->maxcode[l] = -1;
            h->valoffset[l] = 0;
        } else {
            h->valoffset[l] = idx - code;
            for (int j = 0; j < cnt; ++j) {
                uint8_t sym = h->values[idx++];
                if (l <= 8) {
                    int shift = 8 - l;
                    int base = code << shift;
                    int reps = 1 << shift;
                    for (int r = 0; r < reps; ++r) {
                        h->fast_sym[base + r] = sym;
                        h->fast_len[base + r] = (uint8_t)l;
                    }
                }
                code++;
            }
            h->maxcode[l] = code - 1;
        }
        code <<= 1;
    }
    h->maxcode[17] = 0x7FFFFFFF;
    h->valid = 1u;
}

static __attribute__((noinline)) uint8_t fp_jdec_refill(fp_jdec_ctx_t *d, size_t pos) {
    if (pos >= d->src_len) return 0u;
    size_t base = pos & ~(size_t)3u;
    size_t avail = d->src_len - base;
    if (avail > sizeof(d->rbuf)) avail = sizeof(d->rbuf);
    fp_copy_aligned32(d->rbuf, d->src + base, avail);
    d->rbuf_base = base;
    d->rbuf_end = base + avail;
    return d->rbuf[pos - base];
}

static inline __attribute__((always_inline)) uint8_t fp_jdec_read_src(fp_jdec_ctx_t *d, size_t pos) {
    if (pos >= d->rbuf_base && pos < d->rbuf_end) {
        return d->rbuf[pos - d->rbuf_base];
    }
    return fp_jdec_refill(d, pos);
}

static inline uint8_t fp_jdec_next_byte(fp_jdec_ctx_t *d) {
    if (d->eoi_hit || d->rst_seen || d->pos >= d->src_len) {
        if (d->pos >= d->src_len) d->eoi_hit = 1;
        return 0u;
    }
    uint8_t b = fp_jdec_read_src(d, d->pos++);
    if (b == 0xFFu) {
        size_t ff_pos = d->pos - 1u;
        while (d->pos < d->src_len && fp_jdec_read_src(d, d->pos) == 0xFFu) d->pos++;
        if (d->pos >= d->src_len) {
            d->eoi_hit = 1;
            return 0u;
        }
        uint8_t m = fp_jdec_read_src(d, d->pos++);
        if (m == 0x00u) return 0xFFu;
        if (m >= 0xD0u && m <= 0xD7u) {
            d->pos = ff_pos;
            d->rst_seen = 1;
            return 0u;
        }
        if (m == 0xD9u) {
            d->eoi_hit = 1;
            return 0u;
        }
        return 0u;
    }
    return b;
}

static inline __attribute__((always_inline)) void fp_jdec_fill_bits(fp_jdec_ctx_t *d, int need) {
    while (d->bit_cnt < need) {
        uint8_t b = fp_jdec_next_byte(d);
        d->bit_buf = (d->bit_buf << 8) | (uint32_t)b;
        d->bit_cnt += 8;
    }
}

static inline uint32_t fp_jdec_get_bits(fp_jdec_ctx_t *d, int n) {
    if (n == 0) return 0u;
    if (d->bit_cnt < n) fp_jdec_fill_bits(d, n);
    d->bit_cnt -= n;
    return (d->bit_buf >> d->bit_cnt) & ((1u << n) - 1u);
}

static inline int32_t fp_jdec_extend(uint32_t v, int nbits) {
    if (nbits == 0) return 0;
    uint32_t vt = 1u << (nbits - 1);
    if (v < vt) {
        return (int32_t)v + (int32_t)((~0u << nbits) + 1u);
    }
    return (int32_t)v;
}

static inline int fp_jdec_huff_decode(fp_jdec_ctx_t *d, const fp_huff_dec_t *h) {
    if (d->bit_cnt < 16) fp_jdec_fill_bits(d, 16);
    uint32_t peek8 = (d->bit_buf >> (d->bit_cnt - 8)) & 0xFFu;
    uint8_t flen = h->fast_len[peek8];
    if (flen != 0u) {
        d->bit_cnt -= flen;
        return h->fast_sym[peek8];
    }
    int32_t code = (int32_t)peek8;
    d->bit_cnt -= 8;
    for (int l = 9; l <= 16; ++l) {
        if (d->bit_cnt < 1) fp_jdec_fill_bits(d, 8);
        d->bit_cnt -= 1;
        code = (code << 1) | (int32_t)((d->bit_buf >> d->bit_cnt) & 1u);
        if (code <= h->maxcode[l]) {
            int idx = h->valoffset[l] + code;
            if (idx >= 0 && idx < 256) return h->values[idx];
            return 0;
        }
    }
    return 0;
}

/* Integer 8x8 IDCT (Q12 row/col LLM butterfly, matches JPEG Annex A within 1 LSB). */
static void fp_idct_8x8(const int32_t coef[64], uint8_t *out, int out_stride) {
    int32_t ws[64];

    for (int i = 0; i < 8; ++i) {
        const int32_t *in = coef + i;
        int32_t *w = ws + i;
        if (in[8] == 0 && in[16] == 0 && in[24] == 0 && in[32] == 0 &&
            in[40] == 0 && in[48] == 0 && in[56] == 0) {
            int32_t dc = in[0] * 4;
            w[0] = w[8] = w[16] = w[24] = w[32] = w[40] = w[48] = w[56] = dc;
            continue;
        }
        int32_t p2 = in[16];
        int32_t p3 = in[48];
        int32_t p1 = (p2 + p3) * 2217;
        int32_t t2 = p1 + p3 * (-7568);
        int32_t t3 = p1 + p2 * 3135;
        p2 = in[0];
        p3 = in[32];
        int32_t t0 = (p2 + p3) << 12;
        int32_t t1 = (p2 - p3) << 12;
        int32_t x0 = t0 + t3 + 512;
        int32_t x3 = t0 - t3 + 512;
        int32_t x1 = t1 + t2 + 512;
        int32_t x2 = t1 - t2 + 512;

        t0 = in[56]; t1 = in[40]; t2 = in[24]; t3 = in[8];
        p3 = t0 + t2;
        int32_t p4 = t1 + t3;
        p1 = t0 + t3;
        p2 = t1 + t2;
        int32_t p5 = (p3 + p4) * 4816;
        t0 *= 1223;
        t1 *= 8410;
        t2 *= 12586;
        t3 *= 6149;
        p1 = p5 + p1 * (-3686);
        p2 = p5 + p2 * (-10498);
        p3 *= (-8035);
        p4 *= (-1598);
        t3 += p1 + p4;
        t2 += p2 + p3;
        t1 += p2 + p4;
        t0 += p1 + p3;

        w[0]  = (x0 + t3) >> 10;
        w[56] = (x0 - t3) >> 10;
        w[8]  = (x1 + t2) >> 10;
        w[48] = (x1 - t2) >> 10;
        w[16] = (x2 + t1) >> 10;
        w[40] = (x2 - t1) >> 10;
        w[24] = (x3 + t0) >> 10;
        w[32] = (x3 - t0) >> 10;
    }

    for (int i = 0; i < 8; ++i) {
        const int32_t *w = ws + i * 8;
        uint8_t *o = out + i * out_stride;

        if ((w[1] | w[2] | w[3] | w[4] | w[5] | w[6] | w[7]) == 0) {
            uint8_t dc = fp_clamp_u8(((w[0] << 12) + 65536 + (128 << 17)) >> 17);
            o[0] = o[1] = o[2] = o[3] = o[4] = o[5] = o[6] = o[7] = dc;
            continue;
        }

        int32_t p2 = w[2];
        int32_t p3 = w[6];
        int32_t p1 = (p2 + p3) * 2217;
        int32_t t2 = p1 + p3 * (-7568);
        int32_t t3 = p1 + p2 * 3135;
        p2 = w[0];
        p3 = w[4];
        int32_t t0 = (p2 + p3) << 12;
        int32_t t1 = (p2 - p3) << 12;
        int32_t base_off = 65536 + (128 << 17);
        int32_t x0 = t0 + t3 + base_off;
        int32_t x3 = t0 - t3 + base_off;
        int32_t x1 = t1 + t2 + base_off;
        int32_t x2 = t1 - t2 + base_off;

        t0 = w[7]; t1 = w[5]; t2 = w[3]; t3 = w[1];
        p3 = t0 + t2;
        int32_t p4 = t1 + t3;
        p1 = t0 + t3;
        p2 = t1 + t2;
        int32_t p5 = (p3 + p4) * 4816;
        t0 *= 1223;
        t1 *= 8410;
        t2 *= 12586;
        t3 *= 6149;
        p1 = p5 + p1 * (-3686);
        p2 = p5 + p2 * (-10498);
        p3 *= (-8035);
        p4 *= (-1598);
        t3 += p1 + p4;
        t2 += p2 + p3;
        t1 += p2 + p4;
        t0 += p1 + p3;

        o[0] = fp_clamp_u8((x0 + t3) >> 17);
        o[7] = fp_clamp_u8((x0 - t3) >> 17);
        o[1] = fp_clamp_u8((x1 + t2) >> 17);
        o[6] = fp_clamp_u8((x1 - t2) >> 17);
        o[2] = fp_clamp_u8((x2 + t1) >> 17);
        o[5] = fp_clamp_u8((x2 - t1) >> 17);
        o[3] = fp_clamp_u8((x3 + t0) >> 17);
        o[4] = fp_clamp_u8((x3 - t0) >> 17);
    }
}

static int fp_jdec_decode_block(
    fp_jdec_ctx_t *d,
    int32_t *dc_pred,
    const fp_huff_dec_t *dc_h,
    const fp_huff_dec_t *ac_h,
    const uint16_t qtab[64],
    uint8_t *out,
    int out_stride,
    int dc_only
) {
    int s = fp_jdec_huff_decode(d, dc_h);
    int nbits = s & 0x0F;
    int32_t diff = fp_jdec_extend(fp_jdec_get_bits(d, nbits), nbits);
    *dc_pred += diff;
    int32_t coef0 = (*dc_pred) * (int32_t)qtab[0];

    if (dc_only) {
        int k = 1;
        while (k < 64) {
            int rs = fp_jdec_huff_decode(d, ac_h);
            int r = (rs >> 4) & 0x0F;
            int sz = rs & 0x0F;
            if (sz == 0) {
                if (r == 15) {
                    k += 16;
                    continue;
                }
                break;
            }
            k += r;
            if (k >= 64) break;
            (void)fp_jdec_get_bits(d, sz);
            k++;
        }
        out[0] = fp_clamp_u8(((coef0 + 4) >> 3) + 128);
        return 0;
    }

    int32_t coef[64];
    fp_zero_i32_64(coef);
    coef[0] = coef0;

    int k = 1;
    while (k < 64) {
        int rs = fp_jdec_huff_decode(d, ac_h);
        int r = (rs >> 4) & 0x0F;
        int sz = rs & 0x0F;
        if (sz == 0) {
            if (r == 15) {
                k += 16;
                continue;
            }
            break;
        }
        k += r;
        if (k >= 64) break;
        int32_t val = fp_jdec_extend(fp_jdec_get_bits(d, sz), sz);
        coef[FP_ZIGZAG[k]] = val * (int32_t)qtab[k];
        k++;
    }

    fp_idct_8x8(coef, out, out_stride);
    return 0;
}

static void fp_jdec_skip_restart(fp_jdec_ctx_t *d) {
    d->bit_cnt = 0;
    d->bit_buf = 0u;
    d->rst_seen = 0;
    while (d->pos < d->src_len && fp_jdec_read_src(d, d->pos) != 0xFFu) d->pos++;
    while (d->pos < d->src_len && fp_jdec_read_src(d, d->pos) == 0xFFu) d->pos++;
    if (d->pos < d->src_len) {
        uint8_t m = fp_jdec_read_src(d, d->pos);
        if (m >= 0xD0u && m <= 0xD7u) {
            d->pos++;
        }
    }
}

/* Parses JPEG headers through SOS so MCU rows can be streamed one strip at a time. */
static int fp_jdec_begin(
    const uint8_t *src,
    size_t src_len,
    fp_jdec_ctx_t *d,
    fp_jpeg_info_t *info_out
) {
    if (!src || !d || src_len < 32u || src[0] != 0xFFu) return -1;
    int has_soi = 0;
    size_t start_pos = 0u;
    if (src[1] == 0xD8u) {
        has_soi = 1;
        start_pos = 2u;
    } else if (src[1] == 0xDBu || src[1] == 0xC0u || src[1] == 0xC4u || src[1] == 0xDDu ||
               (src[1] >= 0xE0u && src[1] <= 0xEFu)) {
        has_soi = 0;
        start_pos = 0u;
    } else {
        return -1;
    }

    fp_memset(d, 0u, sizeof(*d));
    d->src = src;
    d->src_len = src_len;
    d->pos = start_pos;
    d->has_soi = has_soi;
    d->rbuf_base = 0u;
    d->rbuf_end = 0u;

    int seen_sof = 0;
    int seen_sos = 0;

    while (d->pos + 2u <= src_len) {
        while (d->pos < src_len && src[d->pos] != 0xFFu) d->pos++;
        while (d->pos < src_len && src[d->pos] == 0xFFu) d->pos++;
        if (d->pos >= src_len) break;
        uint8_t m = src[d->pos++];
        if (m == 0x00u || (m >= 0xD0u && m <= 0xD8u)) continue;
        if (m == 0xD9u) break;
        if (d->pos + 2u > src_len) return -1;
        uint16_t seglen = fp_be16(src + d->pos);
        if (seglen < 2u || d->pos + seglen > src_len) return -1;
        const uint8_t *seg = src + d->pos + 2u;
        size_t slen = (size_t)seglen - 2u;
        d->pos += seglen;

        if (m == 0xDBu) { /* DQT */
            size_t p = 0u;
            while (p < slen) {
                uint8_t pq_tq = seg[p++];
                uint8_t pq = pq_tq >> 4;
                uint8_t tq = pq_tq & 0x0Fu;
                if (tq >= 4u || pq > 1u) return -1;
                size_t need = pq ? 128u : 64u;
                if (p + need > slen) return -1;
                for (int i = 0; i < 64; ++i) {
                    d->qtab[tq][i] = pq ? fp_be16(seg + p + i * 2) : (uint16_t)seg[p + i];
                }
                d->qvalid[tq] = 1u;
                p += need;
            }
        } else if (m == 0xC4u) { /* DHT */
            size_t p = 0u;
            while (p < slen) {
                if (p + 17u > slen) return -1;
                uint8_t tc_th = seg[p++];
                uint8_t tc = tc_th >> 4;
                uint8_t th = tc_th & 0x0Fu;
                if (tc > 1u || th >= 4u) return -1;
                fp_huff_dec_t *ht = (tc == 0u) ? &d->dc_huff[th] : &d->ac_huff[th];
                int total = 0;
                ht->bits[0] = 0u;
                for (int l = 1; l <= 16; ++l) {
                    ht->bits[l] = seg[p++];
                    total += ht->bits[l];
                }
                if (total > 256 || p + (size_t)total > slen) return -1;
                fp_memcpy(ht->values, seg + p, (size_t)total);
                p += (size_t)total;
                fp_jdec_build_huff(ht);
            }
        } else if (m == 0xDDu) { /* DRI */
            if (slen >= 2u) d->restart_interval = fp_be16(seg);
        } else if (m == 0xC0u) { /* SOF0 */
            fp_jpeg_info_t info;
            if (fp_jdec_parse_sof0(seg, slen, &info, d->comp) != 0) return -1;
            info.has_soi = d->has_soi;
            d->width = info.width;
            d->height = info.height;
            d->is_420 = info.is_420;
            d->src_mode = info.src_mode;
            d->hmax = (int)d->comp[0].hsf;
            d->vmax = (int)d->comp[0].vsf;
            seen_sof = 1;
            if (info_out) *info_out = info;
        } else if (m >= 0xC1u && m <= 0xCFu) {
            return -1;
        } else if (m == 0xDAu) { /* SOS */
            if (!seen_sof || slen < 1u) return -1;
            uint8_t ns = seg[0];
            if (ns != 3u || slen < (size_t)(1u + ns * 2u + 3u)) return -1;
            for (int i = 0; i < 3; ++i) {
                uint8_t cid = seg[1 + i * 2];
                uint8_t td_ta = seg[1 + i * 2 + 1];
                int matched = -1;
                for (int c = 0; c < 3; ++c) {
                    if (d->comp[c].id == cid) {
                        matched = c;
                        break;
                    }
                }
                if (matched < 0) return -1;
                d->scan_order[i] = (uint8_t)matched;
                d->comp[matched].td = (uint8_t)(td_ta >> 4);
                d->comp[matched].ta = (uint8_t)(td_ta & 0x0Fu);
                if (d->comp[matched].td >= 4u || d->comp[matched].ta >= 4u) return -1;
                if (!d->qvalid[d->comp[matched].tq] ||
                    !d->dc_huff[d->comp[matched].td].valid ||
                    !d->ac_huff[d->comp[matched].ta].valid) {
                    return -1;
                }
            }
            seen_sos = 1;
            break;
        }
    }

    if (!seen_sof || !seen_sos) return -1;

    d->mcu_w = d->hmax * 8;
    d->mcu_h = d->vmax * 8;
    d->mcus_x = (d->width + d->mcu_w - 1) >> (d->hmax == 2 ? 4 : 3);
    d->mcus_y = (d->height + d->mcu_h - 1) >> (d->vmax == 2 ? 4 : 3);
    d->dc_pred[0] = d->dc_pred[1] = d->dc_pred[2] = 0;
    d->mcu_cnt = 0u;
    d->mcu_in_rst = 0u;
    return 0;
}

/* Decodes a single MCU row `my` into Y strip (`mcu_h` rows) and interleaved CbCr strip (`8` rows). */
static int fp_jdec_decode_mcu_row(
    fp_jdec_ctx_t *d,
    int my,
    uint8_t *y_strip,
    int y_stride,
    uint8_t *cbcr_strip,
    int cbcr_stride,
    int dc_only
) {
    if (!d || !y_strip || !cbcr_strip || my < 0 || my >= d->mcus_y) return -1;

    uint8_t blk_cb[64];
    uint8_t blk_cr[64];

    for (int mx = 0; mx < d->mcus_x; ++mx) {
        if (d->restart_interval > 0u && d->mcu_in_rst >= d->restart_interval) {
            d->mcu_in_rst = 0u;
            d->dc_pred[0] = d->dc_pred[1] = d->dc_pred[2] = 0;
            fp_jdec_skip_restart(d);
        }

        if (d->src_mode == 0) {
            uint8_t blk_y[64];
            for (int si = 0; si < 3; ++si) {
                int c = d->scan_order[si];
                const fp_comp_t *cp = &d->comp[c];
                uint8_t *dst_blk = (c == 0) ? blk_y : ((c == 1) ? blk_cb : blk_cr);
                fp_jdec_decode_block(d, &d->dc_pred[c], &d->dc_huff[cp->td], &d->ac_huff[cp->ta],
                                     d->qtab[cp->tq], dst_blk, 8, dc_only);
            }
            int bx = mx * 8;
            if (dc_only) {
                y_strip[bx] = blk_y[0];
                cbcr_strip[bx] = blk_cb[0];
                cbcr_strip[bx + 1] = blk_cr[0];
            } else {
                for (int r = 0; r < 8; ++r) {
                    uint8_t *yr = y_strip + (size_t)r * (size_t)y_stride + bx;
                    uint8_t *cr = cbcr_strip + (size_t)r * (size_t)cbcr_stride + bx;
                    for (int k = 0; k < 8; k += 2) {
                        yr[k] = blk_y[r * 8 + k];
                        yr[k + 1] = blk_y[r * 8 + k + 1];
                        cr[k] = (uint8_t)(((uint16_t)blk_cb[r * 8 + k] + blk_cb[r * 8 + k + 1] + 1u) >> 1);
                        cr[k + 1] = (uint8_t)(((uint16_t)blk_cr[r * 8 + k] + blk_cr[r * 8 + k + 1] + 1u) >> 1);
                    }
                }
            }
        } else {
            for (int si = 0; si < 3; ++si) {
                int c = d->scan_order[si];
                const fp_comp_t *cp = &d->comp[c];
                if (c == 0) {
                    for (int vy = 0; vy < (int)cp->vsf; ++vy) {
                        for (int hx = 0; hx < (int)cp->hsf; ++hx) {
                            int py = vy * 8;
                            int px = mx * 16 + hx * 8;
                            fp_jdec_decode_block(d, &d->dc_pred[0], &d->dc_huff[cp->td], &d->ac_huff[cp->ta],
                                                 d->qtab[cp->tq],
                                                 y_strip + (size_t)py * (size_t)y_stride + px,
                                                 y_stride, dc_only);
                        }
                    }
                } else {
                    uint8_t *dst_blk = (c == 1) ? blk_cb : blk_cr;
                    fp_jdec_decode_block(d, &d->dc_pred[c], &d->dc_huff[cp->td], &d->ac_huff[cp->ta],
                                         d->qtab[cp->tq], dst_blk, 8, dc_only);
                }
            }
            int c_col_base = mx * 16;
            if (dc_only) {
                uint8_t cb0 = blk_cb[0];
                uint8_t cr0 = blk_cr[0];
                uint8_t *crow0 = cbcr_strip + c_col_base;
                crow0[0] = cb0;
                crow0[1] = cr0;
                crow0[8] = cb0;
                crow0[9] = cr0;
                if (d->is_420) {
                    uint8_t *crow4 = cbcr_strip + (size_t)4u * (size_t)cbcr_stride + c_col_base;
                    crow4[0] = cb0;
                    crow4[1] = cr0;
                    crow4[8] = cb0;
                    crow4[9] = cr0;
                }
            } else {
                for (int r = 0; r < 8; ++r) {
                    uint8_t *crow = cbcr_strip + (size_t)r * (size_t)cbcr_stride + c_col_base;
                    const uint8_t *cbr = blk_cb + r * 8;
                    const uint8_t *crr = blk_cr + r * 8;
                    for (int k = 0; k < 8; ++k) {
                        crow[k * 2] = cbr[k];
                        crow[k * 2 + 1] = crr[k];
                    }
                }
            }
        }
        d->mcu_cnt++;
        d->mcu_in_rst++;
    }
    return 0;
}

/* Decodes full Baseline JPEG into padded Y and semi-planar interleaved CbCr planes. */
static __attribute__((unused)) int fp_jpeg_decode(
    const uint8_t *src,
    size_t src_len,
    uint8_t *y_plane,
    int y_stride,
    uint8_t *cbcr_plane,
    int cbcr_stride,
    fp_jdec_ctx_t *d,
    fp_jpeg_info_t *info_out
) {
    if (!y_plane || !cbcr_plane) return -1;
    if (fp_jdec_begin(src, src_len, d, info_out) != 0) return -1;
    for (int my = 0; my < d->mcus_y; ++my) {
        if (fp_jdec_decode_mcu_row(
                d, my,
                y_plane + (size_t)my * (size_t)d->mcu_h * (size_t)y_stride, y_stride,
                cbcr_plane + (size_t)my * 8u * (size_t)cbcr_stride, cbcr_stride, 0) != 0) {
            return -1;
        }
    }
    return 0;
}

/* ---- Encoder: Integer 8x8 FDCT (LLM / libjpeg jfdctint), Huffman, Writer ---- */
static void fp_jenc_build_huff(const uint8_t bits[17], const uint8_t *vals, fp_huff_enc_t *out) {
    fp_memset(out, 0u, sizeof(*out));
    uint16_t code = 0u;
    int idx = 0;
    for (int l = 1; l <= 16; ++l) {
        for (int j = 0; j < (int)bits[l]; ++j) {
            uint8_t sym = vals[idx++];
            out->code[sym] = code;
            out->len[sym] = (uint8_t)l;
            code++;
        }
        code <<= 1;
    }
}

static inline void fp_jenc_put_byte(fp_jenc_ctx_t *e, uint8_t b) {
    if (e->pos < e->cap) {
        e->dst[e->pos++] = b;
    } else {
        e->overflow = 1;
    }
}

static inline void fp_jenc_put_bits(fp_jenc_ctx_t *e, uint32_t bits, int nbits) {
    e->bit_buf = (e->bit_buf << nbits) | (bits & ((1u << nbits) - 1u));
    e->bit_cnt += nbits;
    while (e->bit_cnt >= 8) {
        e->bit_cnt -= 8;
        uint8_t b = (uint8_t)((e->bit_buf >> e->bit_cnt) & 0xFFu);
        fp_jenc_put_byte(e, b);
        if (b == 0xFFu) fp_jenc_put_byte(e, 0x00u);
    }
}

static inline void fp_jenc_flush_bits(fp_jenc_ctx_t *e) {
    if (e->bit_cnt > 0) {
        int pad = 8 - e->bit_cnt;
        fp_jenc_put_bits(e, (1u << pad) - 1u, pad);
    }
    e->bit_buf = 0u;
    e->bit_cnt = 0;
}

static void fp_jenc_put_be16(fp_jenc_ctx_t *e, uint16_t v) {
    fp_jenc_put_byte(e, (uint8_t)(v >> 8));
    fp_jenc_put_byte(e, (uint8_t)(v & 0xFFu));
}

static void fp_jenc_emit_dht(fp_jenc_ctx_t *e, uint8_t tc_th, const uint8_t bits[17], const uint8_t *vals, int nvals) {
    fp_jenc_put_byte(e, 0xFFu);
    fp_jenc_put_byte(e, 0xC4u);
    fp_jenc_put_be16(e, (uint16_t)(2 + 1 + 16 + nvals));
    fp_jenc_put_byte(e, tc_th);
    for (int i = 1; i <= 16; ++i) fp_jenc_put_byte(e, bits[i]);
    for (int i = 0; i < nvals; ++i) fp_jenc_put_byte(e, vals[i]);
}

static void fp_jenc_emit_dqt(fp_jenc_ctx_t *e, uint8_t tq, const uint8_t q[64]) {
    fp_jenc_put_byte(e, 0xFFu);
    fp_jenc_put_byte(e, 0xDBu);
    fp_jenc_put_be16(e, 2u + 1u + 64u);
    fp_jenc_put_byte(e, tq);
    for (int i = 0; i < 64; ++i) fp_jenc_put_byte(e, q[i]);
}

/* Pure integer 8x8 FDCT (libjpeg jfdctint, Q13 CONST_BITS=13, PASS1_BITS=2). */
static void fp_fdct_8x8(const uint8_t *in, int in_stride, int32_t out[64]) {
    int32_t ws[64];

    for (int r = 0; r < 8; ++r) {
        const uint8_t *p = in + r * in_stride;
        int32_t *d = ws + r * 8;
        int32_t tmp0 = (int32_t)p[0] + (int32_t)p[7] - 256;
        int32_t tmp7 = (int32_t)p[0] - (int32_t)p[7];
        int32_t tmp1 = (int32_t)p[1] + (int32_t)p[6] - 256;
        int32_t tmp6 = (int32_t)p[1] - (int32_t)p[6];
        int32_t tmp2 = (int32_t)p[2] + (int32_t)p[5] - 256;
        int32_t tmp5 = (int32_t)p[2] - (int32_t)p[5];
        int32_t tmp3 = (int32_t)p[3] + (int32_t)p[4] - 256;
        int32_t tmp4 = (int32_t)p[3] - (int32_t)p[4];

        int32_t tmp10 = tmp0 + tmp3;
        int32_t tmp13 = tmp0 - tmp3;
        int32_t tmp11 = tmp1 + tmp2;
        int32_t tmp12 = tmp1 - tmp2;

        d[0] = (tmp10 + tmp11) << 2;
        d[4] = (tmp10 - tmp11) << 2;

        int32_t z1 = (tmp12 + tmp13) * 4433; /* 0.5411961 * 8192 */
        d[2] = (z1 + tmp13 * 6270 + 1024) >> 11;  /* +0.765366865 * 8192 */
        d[6] = (z1 + tmp12 * (-15137) + 1024) >> 11; /* -1.847759065 * 8192 */

        z1 = tmp4 + tmp7;
        int32_t z2 = tmp5 + tmp6;
        int32_t z3 = tmp4 + tmp6;
        int32_t z4 = tmp5 + tmp7;
        int32_t z5 = (z3 + z4) * 9633; /* 1.175875602 * 8192 */

        tmp4 *= 2446;   /* 0.298631336 * 8192 */
        tmp5 *= 16819;  /* 2.053119869 * 8192 */
        tmp6 *= 25172;  /* 3.072711026 * 8192 */
        tmp7 *= 12299;  /* 1.501321110 * 8192 */
        z1 *= (-7373);  /* -0.899976223 * 8192 */
        z2 *= (-20995); /* -2.562915447 * 8192 */
        z3 *= (-16069); /* -1.961570560 * 8192 */
        z4 *= (-3196);  /* -0.390180644 * 8192 */

        z3 += z5;
        z4 += z5;
        d[7] = (tmp4 + z1 + z3 + 1024) >> 11;
        d[5] = (tmp5 + z2 + z4 + 1024) >> 11;
        d[3] = (tmp6 + z2 + z3 + 1024) >> 11;
        d[1] = (tmp7 + z1 + z4 + 1024) >> 11;
    }

    for (int c = 0; c < 8; ++c) {
        int32_t *w = ws + c;
        int32_t *d = out + c;
        int32_t tmp0 = w[0] + w[56];
        int32_t tmp7 = w[0] - w[56];
        int32_t tmp1 = w[8] + w[48];
        int32_t tmp6 = w[8] - w[48];
        int32_t tmp2 = w[16] + w[40];
        int32_t tmp5 = w[16] - w[40];
        int32_t tmp3 = w[24] + w[32];
        int32_t tmp4 = w[24] - w[32];

        int32_t tmp10 = tmp0 + tmp3;
        int32_t tmp13 = tmp0 - tmp3;
        int32_t tmp11 = tmp1 + tmp2;
        int32_t tmp12 = tmp1 - tmp2;

        d[0]  = (tmp10 + tmp11 + 16) >> 5;
        d[32] = (tmp10 - tmp11 + 16) >> 5;

        int32_t z1 = (tmp12 + tmp13) * 4433;
        d[16] = (z1 + tmp13 * 6270 + 131072) >> 18;
        d[48] = (z1 + tmp12 * (-15137) + 131072) >> 18;

        z1 = tmp4 + tmp7;
        int32_t z2 = tmp5 + tmp6;
        int32_t z3 = tmp4 + tmp6;
        int32_t z4 = tmp5 + tmp7;
        int32_t z5 = (z3 + z4) * 9633;

        tmp4 *= 2446;
        tmp5 *= 16819;
        tmp6 *= 25172;
        tmp7 *= 12299;
        z1 *= (-7373);
        z2 *= (-20995);
        z3 *= (-16069);
        z4 *= (-3196);

        z3 += z5;
        z4 += z5;
        d[56] = (tmp4 + z1 + z3 + 131072) >> 18;
        d[40] = (tmp5 + z2 + z4 + 131072) >> 18;
        d[24] = (tmp6 + z2 + z3 + 131072) >> 18;
        d[8]  = (tmp7 + z1 + z4 + 131072) >> 18;
    }
}

static inline int fp_bit_category(int32_t v) {
    uint32_t a = v < 0 ? (uint32_t)(-v) : (uint32_t)v;
    if (a == 0u) return 0;
    return 32 - __builtin_clz(a);
}

static inline uint32_t fp_encode_val(int32_t v, int nbits) {
    return (v < 0) ? (uint32_t)(v - 1) & ((1u << nbits) - 1u) : (uint32_t)v;
}

static void fp_jenc_encode_block(
    fp_jenc_ctx_t *e,
    const uint8_t *in,
    int in_stride,
    int32_t *dc_pred,
    const uint8_t q[64],
    const uint32_t recip[64],
    const fp_huff_enc_t *dc_h,
    const fp_huff_enc_t *ac_h
) {
    int32_t coef[64];
    int32_t qcoef[64];
    fp_fdct_8x8(in, in_stride, coef);

    int last_nz = 0;
    for (int i = 0; i < 64; ++i) {
        int32_t v = coef[FP_ZIGZAG[i]];
        if (v == 0) {
            qcoef[i] = 0;
        } else {
            uint32_t av = (uint32_t)(v < 0 ? -v : v) + ((uint32_t)q[i] >> 1);
            int32_t qv = (int32_t)((av * recip[i]) >> 16);
            int32_t sq = (v < 0) ? -qv : qv;
            qcoef[i] = sq;
            if (sq != 0) last_nz = i;
        }
    }

    int32_t diff = qcoef[0] - *dc_pred;
    *dc_pred = qcoef[0];
    int cat = fp_bit_category(diff);
    fp_jenc_put_bits(e, dc_h->code[cat], dc_h->len[cat]);
    if (cat > 0) fp_jenc_put_bits(e, fp_encode_val(diff, cat), cat);

    int zero_run = 0;
    for (int i = 1; i <= last_nz; ++i) {
        int32_t v = qcoef[i];
        if (v == 0) {
            zero_run++;
            if (zero_run == 16) {
                fp_jenc_put_bits(e, ac_h->code[0xF0], ac_h->len[0xF0]);
                zero_run = 0;
            }
        } else {
            int sz = fp_bit_category(v);
            uint8_t sym = (uint8_t)((zero_run << 4) | sz);
            fp_jenc_put_bits(e, ac_h->code[sym], ac_h->len[sym]);
            fp_jenc_put_bits(e, fp_encode_val(v, sz), sz);
            zero_run = 0;
        }
    }
    if (last_nz < 63) {
        fp_jenc_put_bits(e, ac_h->code[0x00], ac_h->len[0x00]);
    }
}

static void fp_jenc_build_qtable(int quality, const uint8_t base[64], uint8_t out_q[64], uint32_t out_recip[64]) {
    if (quality < 1) quality = 1;
    if (quality > 100) quality = 100;
    uint32_t scale = (quality < 50) ? fp_udiv(5000u, (uint32_t)quality) : (200u - (uint32_t)quality * 2u);
    for (int i = 0; i < 64; ++i) {
        uint32_t v = fp_udiv((uint32_t)base[FP_ZIGZAG[i]] * scale + 50u, 100u);
        if (v < 1u) v = 1u;
        if (v > 255u) v = 255u;
        out_q[i] = (uint8_t)v;
        out_recip[i] = fp_udiv((1u << 16) + v - 1u, v);
    }
}

/* Copies APPn (0xE0..0xEF) and COM (0xFE) segments verbatim from orig_jpg. */
static void fp_jenc_copy_app_segments(fp_jenc_ctx_t *e, const uint8_t *orig, size_t orig_len, int has_soi) {
    if (!orig || orig_len < 4u || orig[0] != 0xFFu) return;
    if (has_soi && orig[1] != 0xD8u) return;
    size_t p = has_soi ? 2u : 0u;
    size_t scan_lim = orig_len < 262144u ? orig_len : 262144u;
    while (p + 2u <= scan_lim) {
        while (p < scan_lim && orig[p] != 0xFFu) p++;
        while (p < scan_lim && orig[p] == 0xFFu) p++;
        if (p >= scan_lim) break;
        uint8_t m = orig[p++];
        if (m == 0xD9u || m == 0xDAu) break;
        if (m == 0x00u || (m >= 0xD0u && m <= 0xD8u)) continue;
        if (p + 2u > orig_len) break;
        uint16_t seglen = fp_be16(orig + p);
        if (seglen < 2u || p + seglen > orig_len) break;
        if ((m >= 0xE0u && m <= 0xEFu) || m == 0xFEu) {
            if (e->pos + 2u + seglen > e->cap) {
                e->overflow = 1;
                return;
            }
            fp_jenc_put_byte(e, 0xFFu);
            fp_jenc_put_byte(e, m);
            fp_copy_aligned32(e->dst + e->pos, orig + p, seglen);
            e->pos += seglen;
        }
        p += seglen;
    }
}

/* Begins streaming JPEG encode: emits optional SOI + preserved APPn/COM + DQT + SOF0 + DHT + SOS. */
static int fp_jenc_begin(
    int width,
    int height,
    int is_420,
    int quality,
    int emit_soi,
    const uint8_t *orig_jpg,
    size_t orig_len,
    uint8_t *dst,
    size_t dst_cap,
    fp_jenc_ctx_t *e
) {
    if (!dst || !e || dst_cap < 1024u || width <= 0 || height <= 0) return -1;
    fp_memset(e, 0u, sizeof(*e));
    e->dst = dst;
    e->cap = dst_cap;
    e->width = width;
    e->height = height;
    e->is_420 = is_420 ? 1 : 0;

    int mcu_h = e->is_420 ? 16 : 8;
    e->mcus_x = (width + 15) >> 4;
    e->mcus_y = (height + mcu_h - 1) >> (e->is_420 ? 4 : 3);

    fp_jenc_build_qtable(quality, FP_STD_QY, e->q_y, e->recip_y);
    fp_jenc_build_qtable(quality, FP_STD_QC, e->q_c, e->recip_c);
    fp_jenc_build_huff(FP_DC_LUM_BITS, FP_DC_LUM_VALS, &e->dc_y);
    fp_jenc_build_huff(FP_AC_LUM_BITS, FP_AC_LUM_VALS, &e->ac_y);
    fp_jenc_build_huff(FP_DC_CHR_BITS, FP_DC_CHR_VALS, &e->dc_c);
    fp_jenc_build_huff(FP_AC_CHR_BITS, FP_AC_CHR_VALS, &e->ac_c);

    if (emit_soi) {
        fp_jenc_put_byte(e, 0xFFu);
        fp_jenc_put_byte(e, 0xD8u);
    }
    fp_jenc_copy_app_segments(e, orig_jpg, orig_len, emit_soi);
    if (e->overflow) return -1;

    fp_jenc_emit_dqt(e, 0u, e->q_y);
    fp_jenc_emit_dqt(e, 1u, e->q_c);

    fp_jenc_put_byte(e, 0xFFu);
    fp_jenc_put_byte(e, 0xC0u);
    fp_jenc_put_be16(e, 17u);
    fp_jenc_put_byte(e, 8u);
    fp_jenc_put_be16(e, (uint16_t)height);
    fp_jenc_put_be16(e, (uint16_t)width);
    fp_jenc_put_byte(e, 3u);
    fp_jenc_put_byte(e, 1u); fp_jenc_put_byte(e, e->is_420 ? 0x22u : 0x21u); fp_jenc_put_byte(e, 0u);
    fp_jenc_put_byte(e, 2u); fp_jenc_put_byte(e, 0x11u); fp_jenc_put_byte(e, 1u);
    fp_jenc_put_byte(e, 3u); fp_jenc_put_byte(e, 0x11u); fp_jenc_put_byte(e, 1u);

    fp_jenc_emit_dht(e, 0x00u, FP_DC_LUM_BITS, FP_DC_LUM_VALS, 12);
    fp_jenc_emit_dht(e, 0x10u, FP_AC_LUM_BITS, FP_AC_LUM_VALS, 162);
    fp_jenc_emit_dht(e, 0x01u, FP_DC_CHR_BITS, FP_DC_CHR_VALS, 12);
    fp_jenc_emit_dht(e, 0x11u, FP_AC_CHR_BITS, FP_AC_CHR_VALS, 162);

    fp_jenc_put_byte(e, 0xFFu);
    fp_jenc_put_byte(e, 0xDAu);
    fp_jenc_put_be16(e, 12u);
    fp_jenc_put_byte(e, 3u);
    fp_jenc_put_byte(e, 1u); fp_jenc_put_byte(e, 0x00u);
    fp_jenc_put_byte(e, 2u); fp_jenc_put_byte(e, 0x11u);
    fp_jenc_put_byte(e, 3u); fp_jenc_put_byte(e, 0x11u);
    fp_jenc_put_byte(e, 0u); fp_jenc_put_byte(e, 63u); fp_jenc_put_byte(e, 0u);
    return e->overflow ? -1 : 0;
}

static int fp_jenc_encode_mcu_row(
    fp_jenc_ctx_t *e,
    const uint8_t *y_strip,
    int y_stride,
    const uint8_t *cbcr_strip,
    int cbcr_stride
) {
    if (!e || e->overflow || !y_strip || !cbcr_strip) return -1;
    int y_blocks_v = e->is_420 ? 2 : 1;
    uint8_t blk_cb[64];
    uint8_t blk_cr[64];

    for (int mx = 0; mx < e->mcus_x; ++mx) {
        for (int vy = 0; vy < y_blocks_v; ++vy) {
            for (int hx = 0; hx < 2; ++hx) {
                int py = vy * 8;
                int px = mx * 16 + hx * 8;
                fp_jenc_encode_block(
                    e,
                    y_strip + (size_t)py * (size_t)y_stride + px,
                    y_stride,
                    &e->dc_y_pred,
                    e->q_y,
                    e->recip_y,
                    &e->dc_y,
                    &e->ac_y
                );
            }
        }

        int c_col_base = mx * 16;
        for (int r = 0; r < 8; ++r) {
            const uint8_t *crow = cbcr_strip + (size_t)r * (size_t)cbcr_stride + c_col_base;
            uint8_t *cbr = blk_cb + r * 8;
            uint8_t *crr = blk_cr + r * 8;
            for (int k = 0; k < 8; ++k) {
                cbr[k] = crow[k * 2];
                crr[k] = crow[k * 2 + 1];
            }
        }
        fp_jenc_encode_block(e, blk_cb, 8, &e->dc_cb_pred, e->q_c, e->recip_c, &e->dc_c, &e->ac_c);
        fp_jenc_encode_block(e, blk_cr, 8, &e->dc_cr_pred, e->q_c, e->recip_c, &e->dc_c, &e->ac_c);
        if (e->overflow) return -1;
    }
    return 0;
}

static size_t fp_jenc_finish(fp_jenc_ctx_t *e) {
    if (!e || e->overflow) return 0u;
    fp_jenc_flush_bits(e);
    fp_jenc_put_byte(e, 0xFFu);
    fp_jenc_put_byte(e, 0xD9u);
    return e->overflow ? 0u : e->pos;
}

static __attribute__((unused)) size_t fp_jpeg_encode(
    const uint8_t *y_plane,
    int y_stride,
    const uint8_t *cbcr_plane,
    int cbcr_stride,
    int width,
    int height,
    int is_420,
    int quality,
    const uint8_t *orig_jpg,
    size_t orig_len,
    uint8_t *dst,
    size_t dst_cap,
    fp_jenc_ctx_t *e
) {
    if (!y_plane || !cbcr_plane) return 0u;
    if (fp_jenc_begin(width, height, is_420, quality, 1, orig_jpg, orig_len, dst, dst_cap, e) != 0) {
        return 0u;
    }
    int mcu_h = e->is_420 ? 16 : 8;
    for (int my = 0; my < e->mcus_y; ++my) {
        if (fp_jenc_encode_mcu_row(
                e,
                y_plane + (size_t)my * (size_t)mcu_h * (size_t)y_stride, y_stride,
                cbcr_plane + (size_t)my * 8u * (size_t)cbcr_stride, cbcr_stride) != 0) {
            return 0u;
        }
    }
    return fp_jenc_finish(e);
}

#endif /* BIONZ_FP_JPEG_H */
