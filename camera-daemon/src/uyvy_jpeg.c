/* Fast integer fixed-point UYVY (4:2:2) -> Baseline JPEG encoder.
 *
 * Uses the Loeffler-Ligtenberg-Moschytz / AAN 1D 8-point integer DCT with
 * pre-scaled quantization multipliers so each 8x8 block requires no floating
 * point and no division in the inner loop.
 */
#pragma GCC optimize("O3")
#include "a6400_uyvy_jpeg.h"

#include <errno.h>
#include <string.h>
#ifdef __linux__
#include <pthread.h>
#endif

/* Standard JPEG Annex K luminance & chrominance quantization tables (Q=50). */
static const uint8_t k_qtable_y[64] = {
    16, 11, 10, 16,  24,  40,  51,  61,
    12, 12, 14, 19,  26,  58,  60,  55,
    14, 13, 16, 24,  40,  57,  69,  56,
    14, 17, 22, 29,  51,  87,  80,  62,
    18, 22, 37, 56,  68, 109, 103,  77,
    24, 35, 55, 64,  81, 104, 113,  92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103,  99
};

static const uint8_t k_qtable_c[64] = {
    17, 18, 24, 47, 99, 99, 99, 99,
    18, 21, 26, 66, 99, 99, 99, 99,
    24, 26, 56, 99, 99, 99, 99, 99,
    47, 66, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99
};

static const uint8_t k_zigzag[64] = {
     0,  1,  8, 16,  9,  2,  3, 10,
    17, 24, 32, 25, 18, 11,  4,  5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13,  6,  7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63
};

/* Standard Annex K Huffman tables. */
static const uint8_t k_dc_y_bits[16] = {
    0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0
};
static const uint8_t k_dc_y_vals[12] = {
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
};
static const uint8_t k_dc_c_bits[16] = {
    0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0
};
static const uint8_t k_dc_c_vals[12] = {
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
};

static const uint8_t k_ac_y_bits[16] = {
    0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7d
};
static const uint8_t k_ac_y_vals[162] = {
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

static const uint8_t k_ac_c_bits[16] = {
    0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77
};
static const uint8_t k_ac_c_vals[162] = {
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

struct huff_lut {
    uint16_t code[256];
    uint8_t  size[256];
};

static struct huff_lut g_dc_y, g_dc_c, g_ac_y, g_ac_c;
static int g_huff_init;

static void build_huff(struct huff_lut *lut, const uint8_t bits[16],
                       const uint8_t *vals)
{
    uint32_t code = 0;
    int idx = 0, len;

    memset(lut, 0, sizeof *lut);
    for (len = 1; len <= 16; len++) {
        int cnt = bits[len - 1];
        while (cnt--) {
            uint8_t sym = vals[idx++];
            lut->code[sym] = (uint16_t)code;
            lut->size[sym] = (uint8_t)len;
            code++;
        }
        code <<= 1;
    }
}

static void ensure_huff_init(void)
{
    if (g_huff_init)
        return;
    build_huff(&g_dc_y, k_dc_y_bits, k_dc_y_vals);
    build_huff(&g_dc_c, k_dc_c_bits, k_dc_c_vals);
    build_huff(&g_ac_y, k_ac_y_bits, k_ac_y_vals);
    build_huff(&g_ac_c, k_ac_c_bits, k_ac_c_vals);
    g_huff_init = 1;
}

struct bit_writer {
    uint8_t *buf;
    size_t   cap;
    size_t   pos;
    uint32_t bitbuf;
    int      bitcnt;
    int      overflow;
};

static inline void bw_put_raw(struct bit_writer *bw, uint8_t b)
{
    if (bw->pos < bw->cap)
        bw->buf[bw->pos++] = b;
    else
        bw->overflow = 1;
}

static inline void bw_put_bits(struct bit_writer *bw, uint32_t bits, int n)
{
    bw->bitbuf = (bw->bitbuf << n) | (bits & ((1u << n) - 1u));
    bw->bitcnt += n;
    while (bw->bitcnt >= 8) {
        bw->bitcnt -= 8;
        {
            uint8_t byte = (uint8_t)((bw->bitbuf >> bw->bitcnt) & 0xffu);
            bw_put_raw(bw, byte);
            if (byte == 0xffu)
                bw_put_raw(bw, 0x00u);
        }
    }
}

static void bw_flush_bits(struct bit_writer *bw)
{
    if (bw->bitcnt > 0) {
        bw_put_bits(bw, (1u << (8 - bw->bitcnt)) - 1u, 8 - bw->bitcnt);
    }
}

/* AAN 1D integer fixed-point DCT (Q8 constants: 0.38268=98, 0.54120=139,
 * 0.70711=181, 1.30656=334). Output needs AAN post-scaling combined into
 * quantization divisors. */
static const uint16_t k_aan_scale[8] = {
    1024, 1420, 1338, 1204, 1024, 804, 554, 283
};

static void fdct_aan(int32_t blk[64])
{
    int i;
    int32_t *p = blk;

    /* Rows */
    for (i = 0; i < 8; i++, p += 8) {
        int32_t t0 = p[0] + p[7], t7 = p[0] - p[7];
        int32_t t1 = p[1] + p[6], t6 = p[1] - p[6];
        int32_t t2 = p[2] + p[5], t5 = p[2] - p[5];
        int32_t t3 = p[3] + p[4], t4 = p[3] - p[4];

        int32_t t10 = t0 + t3, t13 = t0 - t3;
        int32_t t11 = t1 + t2, t12 = t1 - t2;

        p[0] = t10 + t11;
        p[4] = t10 - t11;

        {
            int32_t z1 = ((t12 + t13) * 181) >> 8;
            p[2] = t13 + z1;
            p[6] = t13 - z1;
        }

        t10 = t4 + t5;
        t11 = t5 + t6;
        t12 = t6 + t7;
        {
            int32_t z5 = ((t10 - t12) * 98) >> 8;
            int32_t z2 = ((t10 * 139) >> 8) + z5;
            int32_t z4 = ((t12 * 334) >> 8) + z5;
            int32_t z3 = (t11 * 181) >> 8;
            int32_t z11 = t7 + z3;
            int32_t z13 = t7 - z3;
            p[5] = z13 + z2;
            p[3] = z13 - z2;
            p[1] = z11 + z4;
            p[7] = z11 - z4;
        }
    }

    /* Columns */
    for (i = 0; i < 8; i++) {
        int32_t *c = blk + i;
        int32_t t0 = c[0] + c[56], t7 = c[0] - c[56];
        int32_t t1 = c[8] + c[48], t6 = c[8] - c[48];
        int32_t t2 = c[16] + c[40], t5 = c[16] - c[40];
        int32_t t3 = c[24] + c[32], t4 = c[24] - c[32];

        int32_t t10 = t0 + t3, t13 = t0 - t3;
        int32_t t11 = t1 + t2, t12 = t1 - t2;

        c[0] = t10 + t11;
        c[32] = t10 - t11;

        {
            int32_t z1 = ((t12 + t13) * 181) >> 8;
            c[16] = t13 + z1;
            c[48] = t13 - z1;
        }

        t10 = t4 + t5;
        t11 = t5 + t6;
        t12 = t6 + t7;
        {
            int32_t z5 = ((t10 - t12) * 98) >> 8;
            int32_t z2 = ((t10 * 139) >> 8) + z5;
            int32_t z4 = ((t12 * 334) >> 8) + z5;
            int32_t z3 = (t11 * 181) >> 8;
            int32_t z11 = t7 + z3;
            int32_t z13 = t7 - z3;
            c[40] = z13 + z2;
            c[24] = z13 - z2;
            c[8] = z11 + z4;
            c[56] = z11 - z4;
        }
    }
}

static inline int bit_size(int32_t v)
{
    uint32_t a = (uint32_t)(v < 0 ? -v : v);
    return a ? (32 - __builtin_clz(a)) : 0;
}

static void encode_block(struct bit_writer *bw, int32_t blk[64],
                         const int32_t qmul[64], int32_t *dc_pred,
                         const struct huff_lut *dc_h,
                         const struct huff_lut *ac_h)
{
    int32_t q[64];
    int i, last_nz = 0, run = 0;
    int32_t diff;

    fdct_aan(blk);

    for (i = 0; i < 64; i++) {
        int idx = k_zigzag[i];
        int32_t v = blk[idx];
        int32_t m = qmul[idx];
        int32_t qv;

        if (v >= 0)
            qv = (v * m + (1 << 15)) >> 16;
        else
            qv = -(((-v) * m + (1 << 15)) >> 16);
        q[i] = qv;
        if (i > 0 && qv != 0)
            last_nz = i;
    }

    diff = q[0] - *dc_pred;
    *dc_pred = q[0];
    {
        int s = bit_size(diff);
        bw_put_bits(bw, dc_h->code[s], dc_h->size[s]);
        if (s > 0) {
            uint32_t bits = (uint32_t)(diff < 0 ? diff + (1 << s) - 1 : diff);
            bw_put_bits(bw, bits, s);
        }
    }

    for (i = 1; i <= last_nz; i++) {
        int32_t v = q[i];
        if (v == 0) {
            run++;
            if (run == 16) {
                bw_put_bits(bw, ac_h->code[0xf0], ac_h->size[0xf0]);
                run = 0;
            }
        } else {
            int s = bit_size(v);
            uint8_t sym = (uint8_t)((run << 4) | s);
            uint32_t bits = (uint32_t)(v < 0 ? v + (1 << s) - 1 : v);
            bw_put_bits(bw, ac_h->code[sym], ac_h->size[sym]);
            bw_put_bits(bw, bits, s);
            run = 0;
        }
    }
    if (last_nz < 63)
        bw_put_bits(bw, ac_h->code[0x00], ac_h->size[0x00]);
}

static void write_dqt(struct bit_writer *bw, uint8_t id, const uint8_t qt[64])
{
    int i;
    bw_put_raw(bw, 0xff); bw_put_raw(bw, 0xdb);
    bw_put_raw(bw, 0x00); bw_put_raw(bw, 67);
    bw_put_raw(bw, id);
    for (i = 0; i < 64; i++)
        bw_put_raw(bw, qt[k_zigzag[i]]);
}

static void write_dht(struct bit_writer *bw, uint8_t cls_id,
                      const uint8_t bits[16], const uint8_t *vals, int nvals)
{
    int i;
    uint16_t len = (uint16_t)(19 + nvals);
    bw_put_raw(bw, 0xff); bw_put_raw(bw, 0xc4);
    bw_put_raw(bw, (uint8_t)(len >> 8));
    bw_put_raw(bw, (uint8_t)(len & 0xff));
    bw_put_raw(bw, cls_id);
    for (i = 0; i < 16; i++)
        bw_put_raw(bw, bits[i]);
    for (i = 0; i < nvals; i++)
        bw_put_raw(bw, vals[i]);
}

int a6400_uyvy_to_jpeg(const uint8_t *uyvy,
                       uint32_t width,
                       uint32_t height,
                       uint32_t stride,
                       int quality,
                       uint8_t *out_buf,
                       size_t out_cap,
                       uint32_t *out_len)
{
    uint8_t qt_y[64], qt_c[64];
    int32_t qmul_y[64], qmul_c[64];
    struct bit_writer bw;
    int scale, r, c;
    int32_t dc_y = 0, dc_cb = 0, dc_cr = 0;
    uint32_t my, mx;

    if (!uyvy || !out_buf || !out_len || width == 0 || height == 0 ||
        (width & 1u) != 0 || stride < width * 2u || out_cap < 1024u)
        return -EINVAL;

    ensure_huff_init();

    if (quality < 10)
        quality = 10;
    if (quality > 95)
        quality = 95;
    scale = quality < 50 ? (5000 / quality) : (200 - quality * 2);

    for (r = 0; r < 8; r++) {
        for (c = 0; c < 8; c++) {
            int i = r * 8 + c;
            int qy = (k_qtable_y[i] * scale + 50) / 100;
            int qc = (k_qtable_c[i] * scale + 50) / 100;
            uint64_t aan_denom;

            if (qy < 1) qy = 1;
            if (qy > 255) qy = 255;
            if (qc < 1) qc = 1;
            if (qc > 255) qc = 255;
            qt_y[i] = (uint8_t)qy;
            qt_c[i] = (uint8_t)qc;

            /* fdct_aan produces 8 * aan_scale[r]/1024 * aan_scale[c]/1024 * DCT.
             * Multiply by 32-bit qmul and shift right 16 to divide by (8 * aan * q). */
            aan_denom = (uint64_t)k_aan_scale[r] * (uint64_t)k_aan_scale[c];
            qmul_y[i] = (int32_t)(((1ULL << 16) * (1024ULL * 1024ULL) + (4ULL * aan_denom * (uint64_t)qy))
                                  / (8ULL * aan_denom * (uint64_t)qy));
            qmul_c[i] = (int32_t)(((1ULL << 16) * (1024ULL * 1024ULL) + (4ULL * aan_denom * (uint64_t)qc))
                                  / (8ULL * aan_denom * (uint64_t)qc));
        }
    }

    memset(&bw, 0, sizeof bw);
    bw.buf = out_buf;
    bw.cap = out_cap;

    /* SOI */
    bw_put_raw(&bw, 0xff); bw_put_raw(&bw, 0xd8);
    /* APP0 JFIF */
    {
        static const uint8_t jfif[18] = {
            0xff, 0xe0, 0x00, 0x10, 'J', 'F', 'I', 'F', 0x00,
            0x01, 0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00
        };
        int k;
        for (k = 0; k < 18; k++)
            bw_put_raw(&bw, jfif[k]);
    }
    /* DQT */
    write_dqt(&bw, 0, qt_y);
    write_dqt(&bw, 1, qt_c);
    /* SOF0: Baseline DCT, 3 components, 4:2:2 (Y 2x1, Cb 1x1, Cr 1x1) */
    bw_put_raw(&bw, 0xff); bw_put_raw(&bw, 0xc0);
    bw_put_raw(&bw, 0x00); bw_put_raw(&bw, 17);
    bw_put_raw(&bw, 8);
    bw_put_raw(&bw, (uint8_t)(height >> 8));
    bw_put_raw(&bw, (uint8_t)(height & 0xff));
    bw_put_raw(&bw, (uint8_t)(width >> 8));
    bw_put_raw(&bw, (uint8_t)(width & 0xff));
    bw_put_raw(&bw, 3);
    bw_put_raw(&bw, 1); bw_put_raw(&bw, 0x21); bw_put_raw(&bw, 0); /* Y: 2x1, QT 0 */
    bw_put_raw(&bw, 2); bw_put_raw(&bw, 0x11); bw_put_raw(&bw, 1); /* Cb: 1x1, QT 1 */
    bw_put_raw(&bw, 3); bw_put_raw(&bw, 0x11); bw_put_raw(&bw, 1); /* Cr: 1x1, QT 1 */
    /* DHT */
    write_dht(&bw, 0x00, k_dc_y_bits, k_dc_y_vals, 12);
    write_dht(&bw, 0x10, k_ac_y_bits, k_ac_y_vals, 162);
    write_dht(&bw, 0x01, k_dc_c_bits, k_dc_c_vals, 12);
    write_dht(&bw, 0x11, k_ac_c_bits, k_ac_c_vals, 162);
    /* SOS */
    bw_put_raw(&bw, 0xff); bw_put_raw(&bw, 0xda);
    bw_put_raw(&bw, 0x00); bw_put_raw(&bw, 12);
    bw_put_raw(&bw, 3);
    bw_put_raw(&bw, 1); bw_put_raw(&bw, 0x00);
    bw_put_raw(&bw, 2); bw_put_raw(&bw, 0x11);
    bw_put_raw(&bw, 3); bw_put_raw(&bw, 0x11);
    bw_put_raw(&bw, 0x00); bw_put_raw(&bw, 0x3f); bw_put_raw(&bw, 0x00);

    /* Encode 16x8 MCUs (UYVY: U0 Y0 V0 Y1) */
    for (my = 0; my < height; my += 8u) {
        for (mx = 0; mx < width; mx += 16u) {
            int32_t y0[64], y1[64], cb[64], cr[64];
            uint32_t dy, dx;

            for (dy = 0; dy < 8u; dy++) {
                uint32_t sy = my + dy;
                const uint8_t *row;
                if (sy >= height)
                    sy = height - 1u;
                row = uyvy + sy * stride;
                for (dx = 0; dx < 8u; dx++) {
                    uint32_t sx = mx + dx * 2u;
                    const uint8_t *px;
                    if (sx + 1u >= width)
                        sx = width - 2u;
                    px = row + sx * 2u;
                    cb[dy * 8u + dx] = (int32_t)px[0] - 128;
                    cr[dy * 8u + dx] = (int32_t)px[2] - 128;
                    if (dx < 4u) {
                        y0[dy * 8u + dx * 2u]     = (int32_t)px[1] - 128;
                        y0[dy * 8u + dx * 2u + 1] = (int32_t)px[3] - 128;
                    } else {
                        uint32_t ox = (dx - 4u) * 2u;
                        y1[dy * 8u + ox]     = (int32_t)px[1] - 128;
                        y1[dy * 8u + ox + 1] = (int32_t)px[3] - 128;
                    }
                }
            }
            encode_block(&bw, y0, qmul_y, &dc_y, &g_dc_y, &g_ac_y);
            encode_block(&bw, y1, qmul_y, &dc_y, &g_dc_y, &g_ac_y);
            encode_block(&bw, cb, qmul_c, &dc_cb, &g_dc_c, &g_ac_c);
            encode_block(&bw, cr, qmul_c, &dc_cr, &g_dc_c, &g_ac_c);
            if (bw.overflow)
                return -ENOSPC;
        }
    }

    bw_flush_bits(&bw);
    /* EOI */
    bw_put_raw(&bw, 0xff); bw_put_raw(&bw, 0xd9);
    if (bw.overflow)
        return -ENOSPC;

    *out_len = (uint32_t)bw.pos;
    return 0;
}

#ifdef __linux__

struct strip_job {
    const uint8_t *uyvy;
    uint32_t       width;
    uint32_t       height;
    uint32_t       stride;
    uint32_t       y_start;
    uint32_t       y_end;
    const int32_t *qmul_y;
    const int32_t *qmul_c;
    uint8_t       *out;
    size_t         cap;
    size_t         len;
    int            overflow;
};

static void encode_strip(struct strip_job *j)
{
    struct bit_writer bw;
    int32_t dc_y = 0, dc_cb = 0, dc_cr = 0;
    uint32_t my, mx;

    memset(&bw, 0, sizeof bw);
    bw.buf = j->out;
    bw.cap = j->cap;

    for (my = j->y_start; my < j->y_end; my += 8u) {
        for (mx = 0; mx < j->width; mx += 16u) {
            int32_t y0[64], y1[64], cb[64], cr[64];
            uint32_t dy, dx;

            for (dy = 0; dy < 8u; dy++) {
                uint32_t sy = my + dy;
                const uint8_t *row;
                if (sy >= j->height)
                    sy = j->height - 1u;
                row = j->uyvy + sy * j->stride;
                for (dx = 0; dx < 8u; dx++) {
                    uint32_t sx = mx + dx * 2u;
                    const uint8_t *px;
                    if (sx + 1u >= j->width)
                        sx = j->width - 2u;
                    px = row + sx * 2u;
                    cb[dy * 8u + dx] = (int32_t)px[0] - 128;
                    cr[dy * 8u + dx] = (int32_t)px[2] - 128;
                    if (dx < 4u) {
                        y0[dy * 8u + dx * 2u]     = (int32_t)px[1] - 128;
                        y0[dy * 8u + dx * 2u + 1] = (int32_t)px[3] - 128;
                    } else {
                        uint32_t ox = (dx - 4u) * 2u;
                        y1[dy * 8u + ox]     = (int32_t)px[1] - 128;
                        y1[dy * 8u + ox + 1] = (int32_t)px[3] - 128;
                    }
                }
            }
            encode_block(&bw, y0, j->qmul_y, &dc_y, &g_dc_y, &g_ac_y);
            encode_block(&bw, y1, j->qmul_y, &dc_y, &g_dc_y, &g_ac_y);
            encode_block(&bw, cb, j->qmul_c, &dc_cb, &g_dc_c, &g_ac_c);
            encode_block(&bw, cr, j->qmul_c, &dc_cr, &g_dc_c, &g_ac_c);
            if (bw.overflow) {
                j->overflow = 1;
                j->len = 0;
                return;
            }
        }
    }
    bw_flush_bits(&bw);
    j->overflow = bw.overflow;
    j->len = bw.pos;
}

/* Two persistent worker threads + calling thread = 3 cores. */
static pthread_mutex_t g_mt_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t  g_mt_cv_go = PTHREAD_COND_INITIALIZER;
static pthread_cond_t  g_mt_cv_done = PTHREAD_COND_INITIALIZER;
static int             g_mt_started;
static uint32_t        g_mt_gen;
static int             g_mt_done_mask;
static struct strip_job g_mt_jobs[3];
static uint8_t         g_mt_strip_buf[3][65536];

static void *mt_worker(void *arg)
{
    int idx = (int)(intptr_t)arg;
    uint32_t seen_gen = 0;

    for (;;) {
        pthread_mutex_lock(&g_mt_mu);
        while (g_mt_gen == seen_gen)
            pthread_cond_wait(&g_mt_cv_go, &g_mt_mu);
        seen_gen = g_mt_gen;
        pthread_mutex_unlock(&g_mt_mu);

        encode_strip(&g_mt_jobs[idx]);

        pthread_mutex_lock(&g_mt_mu);
        g_mt_done_mask |= (1 << idx);
        if (g_mt_done_mask == 0x6)
            pthread_cond_signal(&g_mt_cv_done);
        pthread_mutex_unlock(&g_mt_mu);
    }
    return NULL;
}

static void ensure_mt_workers(void)
{
    pthread_t t1, t2;
    if (g_mt_started)
        return;
    g_mt_started = 1;
    (void)pthread_create(&t1, NULL, mt_worker, (void *)(intptr_t)1);
    (void)pthread_create(&t2, NULL, mt_worker, (void *)(intptr_t)2);
    (void)pthread_detach(t1);
    (void)pthread_detach(t2);
}

int a6400_uyvy_to_jpeg_mt(const uint8_t *uyvy,
                          uint32_t width,
                          uint32_t height,
                          uint32_t stride,
                          int quality,
                          uint8_t *out_buf,
                          size_t out_cap,
                          uint32_t *out_len)
{
    uint32_t mcu_rows = (height + 7u) / 8u;
    uint32_t mcu_cols = (width + 15u) / 16u;
    uint8_t qt_y[64], qt_c[64];
    int32_t qmul_y[64], qmul_c[64];
    struct bit_writer bw;
    int scale, r, c, s;
    uint32_t rows_per_strip, dri;

    if (!uyvy || !out_buf || !out_len || width == 0 || height == 0 ||
        (width & 1u) != 0 || stride < width * 2u || out_cap < 2048u)
        return -EINVAL;
    if ((mcu_rows % 3u) != 0u)
        return a6400_uyvy_to_jpeg(uyvy, width, height, stride, quality,
                                  out_buf, out_cap, out_len);

    ensure_huff_init();
    ensure_mt_workers();

    if (quality < 10) quality = 10;
    if (quality > 95) quality = 95;
    scale = quality < 50 ? (5000 / quality) : (200 - quality * 2);

    for (r = 0; r < 8; r++) {
        for (c = 0; c < 8; c++) {
            int i = r * 8 + c;
            int qy = (k_qtable_y[i] * scale + 50) / 100;
            int qc = (k_qtable_c[i] * scale + 50) / 100;
            uint64_t aan_denom;
            if (qy < 1) qy = 1;
            if (qy > 255) qy = 255;
            if (qc < 1) qc = 1;
            if (qc > 255) qc = 255;
            qt_y[i] = (uint8_t)qy;
            qt_c[i] = (uint8_t)qc;
            aan_denom = (uint64_t)k_aan_scale[r] * (uint64_t)k_aan_scale[c];
            qmul_y[i] = (int32_t)(((1ULL << 16) * (1024ULL * 1024ULL) + (4ULL * aan_denom * (uint64_t)qy))
                                  / (8ULL * aan_denom * (uint64_t)qy));
            qmul_c[i] = (int32_t)(((1ULL << 16) * (1024ULL * 1024ULL) + (4ULL * aan_denom * (uint64_t)qc))
                                  / (8ULL * aan_denom * (uint64_t)qc));
        }
    }

    rows_per_strip = mcu_rows / 3u;
    dri = rows_per_strip * mcu_cols;

    for (s = 0; s < 3; s++) {
        g_mt_jobs[s].uyvy = uyvy;
        g_mt_jobs[s].width = width;
        g_mt_jobs[s].height = height;
        g_mt_jobs[s].stride = stride;
        g_mt_jobs[s].y_start = (uint32_t)s * rows_per_strip * 8u;
        g_mt_jobs[s].y_end = (uint32_t)(s + 1) * rows_per_strip * 8u;
        g_mt_jobs[s].qmul_y = qmul_y;
        g_mt_jobs[s].qmul_c = qmul_c;
        g_mt_jobs[s].out = g_mt_strip_buf[s];
        g_mt_jobs[s].cap = sizeof(g_mt_strip_buf[s]);
        g_mt_jobs[s].len = 0;
        g_mt_jobs[s].overflow = 0;
    }

    /* Dispatch strips 1 and 2 to worker threads, encode strip 0 on main thread. */
    pthread_mutex_lock(&g_mt_mu);
    g_mt_done_mask = 0;
    g_mt_gen++;
    pthread_cond_broadcast(&g_mt_cv_go);
    pthread_mutex_unlock(&g_mt_mu);

    encode_strip(&g_mt_jobs[0]);

    pthread_mutex_lock(&g_mt_mu);
    while (g_mt_done_mask != 0x6)
        pthread_cond_wait(&g_mt_cv_done, &g_mt_mu);
    pthread_mutex_unlock(&g_mt_mu);

    if (g_mt_jobs[0].overflow || g_mt_jobs[1].overflow || g_mt_jobs[2].overflow)
        return -ENOSPC;

    memset(&bw, 0, sizeof bw);
    bw.buf = out_buf;
    bw.cap = out_cap;

    bw_put_raw(&bw, 0xff); bw_put_raw(&bw, 0xd8);
    {
        static const uint8_t jfif[18] = {
            0xff, 0xe0, 0x00, 0x10, 'J', 'F', 'I', 'F', 0x00,
            0x01, 0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00
        };
        int k;
        for (k = 0; k < 18; k++)
            bw_put_raw(&bw, jfif[k]);
    }
    write_dqt(&bw, 0, qt_y);
    write_dqt(&bw, 1, qt_c);
    /* DRI (Define Restart Interval) */
    bw_put_raw(&bw, 0xff); bw_put_raw(&bw, 0xdd);
    bw_put_raw(&bw, 0x00); bw_put_raw(&bw, 0x04);
    bw_put_raw(&bw, (uint8_t)(dri >> 8));
    bw_put_raw(&bw, (uint8_t)(dri & 0xff));
    /* SOF0 */
    bw_put_raw(&bw, 0xff); bw_put_raw(&bw, 0xc0);
    bw_put_raw(&bw, 0x00); bw_put_raw(&bw, 17);
    bw_put_raw(&bw, 8);
    bw_put_raw(&bw, (uint8_t)(height >> 8));
    bw_put_raw(&bw, (uint8_t)(height & 0xff));
    bw_put_raw(&bw, (uint8_t)(width >> 8));
    bw_put_raw(&bw, (uint8_t)(width & 0xff));
    bw_put_raw(&bw, 3);
    bw_put_raw(&bw, 1); bw_put_raw(&bw, 0x21); bw_put_raw(&bw, 0);
    bw_put_raw(&bw, 2); bw_put_raw(&bw, 0x11); bw_put_raw(&bw, 1);
    bw_put_raw(&bw, 3); bw_put_raw(&bw, 0x11); bw_put_raw(&bw, 1);
    /* DHT */
    write_dht(&bw, 0x00, k_dc_y_bits, k_dc_y_vals, 12);
    write_dht(&bw, 0x10, k_ac_y_bits, k_ac_y_vals, 162);
    write_dht(&bw, 0x01, k_dc_c_bits, k_dc_c_vals, 12);
    write_dht(&bw, 0x11, k_ac_c_bits, k_ac_c_vals, 162);
    /* SOS */
    bw_put_raw(&bw, 0xff); bw_put_raw(&bw, 0xda);
    bw_put_raw(&bw, 0x00); bw_put_raw(&bw, 12);
    bw_put_raw(&bw, 3);
    bw_put_raw(&bw, 1); bw_put_raw(&bw, 0x00);
    bw_put_raw(&bw, 2); bw_put_raw(&bw, 0x11);
    bw_put_raw(&bw, 3); bw_put_raw(&bw, 0x11);
    bw_put_raw(&bw, 0x00); bw_put_raw(&bw, 0x3f); bw_put_raw(&bw, 0x00);

    for (s = 0; s < 3; s++) {
        if (bw.pos + g_mt_jobs[s].len + 4u > bw.cap)
            return -ENOSPC;
        memcpy(bw.buf + bw.pos, g_mt_jobs[s].out, g_mt_jobs[s].len);
        bw.pos += g_mt_jobs[s].len;
        if (s < 2) {
            bw_put_raw(&bw, 0xff);
            bw_put_raw(&bw, (uint8_t)(0xd0 + s)); /* RST0, RST1 */
        }
    }
    bw_put_raw(&bw, 0xff); bw_put_raw(&bw, 0xd9);
    if (bw.overflow)
        return -ENOSPC;
    *out_len = (uint32_t)bw.pos;
    return 0;
}

#else

int a6400_uyvy_to_jpeg_mt(const uint8_t *uyvy,
                          uint32_t width,
                          uint32_t height,
                          uint32_t stride,
                          int quality,
                          uint8_t *out_buf,
                          size_t out_cap,
                          uint32_t *out_len)
{
    return a6400_uyvy_to_jpeg(uyvy, width, height, stride, quality,
                              out_buf, out_cap, out_len);
}

#endif
