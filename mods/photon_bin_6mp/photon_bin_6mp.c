/* PhotonBin 6MP (Low-Light Photon Convergence + IMX284 Sensor Analog Overrides)
 *
 * Combines:
 *   - Option 3 (Hardware Analog Readout Overrides in av-cam.bin LIRO .text via /dev/mem):
 *       * IMX284 P2S reg 0x099C bit 1/2 Mconv (Floating-Diffusion Conversion Gain):
 *         Main (PA 0x0379FEC6, 6 B) & ZAF (PA 0x0379FF7C, 6 B):
 *           Force HCG (+9.805 dB / 3.092x) | Stock Auto (ISO 400 crossover) | Force LCG
 *       * CaptureUtilityImager 14-bit 1H2H (ADnum = 2) gate:
 *         Silent Shutter (PA 0x038ECC48, 2 B) & Bulb (PA 0x038ECC56, 2 B):
 *           Bulb + Silent (retains 14-bit 1H2H) | Stock Gate
 *   - Option 1 (Pre-Demosaic 4x4 -> 2x2 Bayer / 2x2 Mono Photon Convergence into .ARW):
 *       * Hooks ImageBuilder::MakeRawInfoArea (libObj.so + 0x00D7861C) in im.elf.
 *       * Decodes Sony ARW 2.3 cRAW (16-byte RDC blocks + per-shot Tag 0x7010 SonyToneCurve)
 *         or 16-bit Uncompressed RAW, subtracts optical black (512), converges 4 photosites
 *         into a 6.0 MP (3008x2008 total, 3000x2000 active) RAW frame, and writes out a
 *         self-consistent .ARW (16-bit Uncompressed, 14-bit Uncompressed, or re-encoded
 *         14-bit Sony ARW 2.3 cRAW) with updated SubIFD dimensions and crop metadata.
 *
 * Nested Submenu Configuration (__smod_bss + 4, 6 x uint8_t):
 *   cfg[0] Binning Mode   : 0 = 4x4->2x2 Bayer, 1 = 2x2 Mono Quad, 2 = Sensor Only (24MP)
 *   cfg[1] Photon Gain    : 0 = +2 EV Sum (4x), 1 = +1 EV Mid (2x), 2 = +0 EV Avg (1x)
 *   cfg[2] Spatial Kernel : 0 = [1,2,1] Phase,  1 = 2x2 Box Sum
 *   cfg[3] ARW Output     : 0 = 16-bit Uncomp,  1 = 14-bit Uncomp,  2 = 14-bit cRAW
 *   cfg[4] FD Conv Gain   : 0 = Force HCG,      1 = Stock Auto,     2 = Force LCG
 *   cfg[5] 14b 1H2H Read  : 0 = Bulb + Silent,  1 = Stock Gate
 *
 * Log: /tmp_bt/mods/photon_bin_6mp.log
 */
#include <stdint.h>

#define PB_MAGIC            0x36424850u /* "PHB6" */
#define PB_BLACK_LEVEL      512
#define PB_MAX_IN_W         6048u
#define PB_MAX_OUT_W        3024u

#if defined(TARGET_A6100) || defined(TARGET_ILCE6100)
/* ILCE-6100 1.00 av-cam.bin (ORIL_BASE 0x635C6000 -> LIRO PA = VA - 0x60000000) */
#define PA_MAIN_MCONV       0x0379EC9Au
#define PA_ZAF_MCONV        0x0379ED50u
#define PA_1H2H_SILENT      0x038EA8FCu
#define PA_1H2H_BULB        0x038EA90Au
#else
/* ILCE-6400A 1.01 av-cam.bin (ORIL_BASE 0x635C6000 -> LIRO PA = VA - 0x60000000) */
#define PA_MAIN_MCONV       0x0379FEC6u
#define PA_ZAF_MCONV        0x0379FF7Cu
#define PA_1H2H_SILENT      0x038ECC48u
#define PA_1H2H_BULB        0x038ECC56u
#endif

struct pb_phys_ext {
    uint32_t phys_addr;
    uint32_t size;
};

struct pb_bss_state {
    uint32_t magic;
    uint8_t  cfg[8];         /* offset 4: synced by Mod Loader v2 submenu (cfg[0..5]) */
    uint32_t active;
    uint32_t frames_binned;
    uint32_t frames_skipped;
    uint32_t last_in_wh;
    uint32_t last_out_wh;
    uint32_t last_out_bytes;
    uint32_t opt3_apply_cnt;
    uint32_t last_ext_cnt;                  /* extents kept after Call 1 trim */
    uint32_t add_valid;                     /* add_buf holds the patched rawAdditionalInfo */
    uint16_t curve_lut[2048];             /* 11-bit (0..2047) -> 14-bit linear (0..16383) */
    uint16_t row_ring[8][PB_MAX_IN_W];    /* 8-row decoded 14-bit input ring buffer */
    uint16_t out_row[PB_MAX_OUT_W];       /* 1-row converged pixel staging buffer */
    uint8_t  in_raw_row[PB_MAX_IN_W * 2u];  /* cached input row staging for /dev/mem */
    uint8_t  out_raw_row[PB_MAX_OUT_W * 2u]; /* cached output row staging for /dev/mem */
    uint8_t  add_buf[1024];                 /* writable copy of rawAdditionalInfo */
};

extern uint8_t __smod_bss[] __attribute__((visibility("hidden")));
#define PB_BSS ((struct pb_bss_state *)__smod_bss)

extern int pb_orig_make_raw_info_area(uint8_t *raw_info_area, void *src, void *out)
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

/* ---- Bounded ASCII log helpers ---- */
typedef struct {
    char b[256];
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
    long fd = sc3(5 /* open */, (long)"/tmp_bt/mods/photon_bin_6mp.log",
                  01 | 0100 | 02000 /* O_WRONLY | O_CREAT | O_APPEND */, 0644);
    if (fd >= 0) {
        sc3(4 /* write */, fd, (long)l->b, (long)l->n);
        sc3(6 /* close */, fd, 0, 0);
    }
}

/* ---- Option 3: Halfword-aligned fail-closed LIRO .text swaps via /dev/mem ----
 * Uses strictly 16-bit volatile accesses so O_SYNC mappings never fault on 2-mod-4 PAs. */
static int match_hw3(const uint16_t cur[3], uint16_t a, uint16_t b, uint16_t c) {
    return cur[0] == a && cur[1] == b && cur[2] == c;
}

static char apply_mconv_site(long mem_fd, uint32_t pa, uint8_t mode) {
    /* Known valid 6-byte instruction sequences:
     *   mode 0 (Force HCG) : f1b8 0f00 bf00  (cmp.w r8, #0; nop)
     *   mode 1 (Stock Auto): f1b8 0f00 d003  (cmp.w r8, #0; beq +6)
     *   mode 2 (Force LCG) : f04f 0800 e003  (mov.w r8, #0; b +6) */
    uint16_t want0 = 0xf1b8u, want1 = 0x0f00u, want2 = 0xd003u;
    if (mode == 0u) {
        want2 = 0xbf00u;
    } else if (mode == 2u) {
        want0 = 0xf04fu;
        want1 = 0x0800u;
        want2 = 0xe003u;
    }
    long page = sc6(192 /* mmap2 */, 0, 4096, 3 /* PROT_READ | PROT_WRITE */,
                    1 /* MAP_SHARED */, mem_fd, (long)(pa >> 12));
    if (page < 0 && page > -4096) return 'm';
    volatile uint16_t *p = (volatile uint16_t *)((uint32_t)page + (pa & 0xfffu));
    uint16_t cur[3] = { p[0], p[1], p[2] };
    char st = 'x';
    if (match_hw3(cur, want0, want1, want2)) {
        st = 'a';
    } else if (match_hw3(cur, 0xf1b8u, 0x0f00u, 0xd003u) ||
               match_hw3(cur, 0xf1b8u, 0x0f00u, 0xbf00u) ||
               match_hw3(cur, 0xf04fu, 0x0800u, 0xe003u)) {
        p[0] = want0;
        p[1] = want1;
        p[2] = want2;
        __asm__ volatile("dsb sy" ::: "memory");
        st = (p[0] == want0 && p[1] == want1 && p[2] == want2) ? 'w' : 'v';
    }
    sc3(91 /* munmap */, page, 4096, 0);
    return st;
}

static char apply_u16_site(long mem_fd, uint32_t pa, uint16_t stock_hw, uint16_t patch_hw, int patch_on) {
    uint16_t want = patch_on ? patch_hw : stock_hw;
    long page = sc6(192 /* mmap2 */, 0, 4096, 3 /* PROT_READ | PROT_WRITE */,
                    1 /* MAP_SHARED */, mem_fd, (long)(pa >> 12));
    if (page < 0 && page > -4096) return 'm';
    volatile uint16_t *p = (volatile uint16_t *)((uint32_t)page + (pa & 0xfffu));
    uint16_t cur = p[0];
    char st = 'x';
    if (cur == want) {
        st = 'a';
    } else if (cur == stock_hw || cur == patch_hw) {
        p[0] = want;
        __asm__ volatile("dsb sy" ::: "memory");
        st = (p[0] == want) ? 'w' : 'v';
    }
    sc3(91 /* munmap */, page, 4096, 0);
    return st;
}

static void pb_apply_option3(int enable, int reason) {
    struct pb_bss_state *s = PB_BSS;
    uint8_t mconv_mode = enable ? (s->cfg[4] <= 2u ? s->cfg[4] : 1u) : 1u; /* 1 = Stock Auto */
    int patch_1h2h = enable && (s->cfg[5] == 0u);                          /* 0 = Bulb + Silent */

    long fd = sc3(5 /* open */, (long)"/dev/mem", 02 | 04010000 /* O_RDWR | O_SYNC */, 0);
    char c_mc_main = 'o', c_mc_zaf = 'o', c_sil = 'o', c_bulb = 'o';
    if (fd >= 0) {
        c_mc_main = apply_mconv_site(fd, PA_MAIN_MCONV, mconv_mode);
        c_mc_zaf  = apply_mconv_site(fd, PA_ZAF_MCONV,  mconv_mode);
        c_sil     = apply_u16_site(fd, PA_1H2H_SILENT, 0xb9f2u, 0xbf00u, patch_1h2h);
        c_bulb    = apply_u16_site(fd, PA_1H2H_BULB,   0xe019u, 0xbf00u, patch_1h2h);
        sc3(6 /* close */, fd, 0, 0);
    }
    s->opt3_apply_cnt++;

    lbuf_t l;
    l.n = 0;
    lb_str(&l, "pb_opt3 en=");
    lb_ch(&l, enable ? '1' : '0');
    lb_str(&l, " rsn=");
    lb_ch(&l, (char)('0' + (reason & 7)));
    lb_str(&l, " cfg=");
    for (int i = 0; i < 6; i++) lb_ch(&l, (char)('0' + (s->cfg[i] & 7u)));
    lb_str(&l, " mc=");
    lb_ch(&l, c_mc_main);
    lb_ch(&l, ',');
    lb_ch(&l, c_mc_zaf);
    lb_str(&l, " h2h=");
    lb_ch(&l, c_sil);
    lb_ch(&l, ',');
    lb_ch(&l, c_bulb);
    lb_ch(&l, '\n');
    lb_flush(&l);
}

/* ---- Option 1: Sony ARW 2.3 cRAW codec + 4x4->2x2 Bayer / 2x2 Mono convergence ---- */
static uint16_t rd_u16_le(const uint8_t *p) {
    return (uint16_t)((uint32_t)p[0] | ((uint32_t)p[1] << 8));
}

static uint32_t rd_u32_le(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* Byte-wise volatile stores: the compiler must never coalesce these into an unaligned
 * strh/str (ARMv7 alignment fault on uncached DMM mappings). */
static void wr_u16_le(uint8_t *p, uint16_t v) {
    volatile uint8_t *vp = p;
    vp[0] = (uint8_t)(v & 0xffu);
    vp[1] = (uint8_t)(v >> 8);
}

static void wr_u32_le(uint8_t *p, uint32_t v) {
    volatile uint8_t *vp = p;
    vp[0] = (uint8_t)(v & 0xffu);
    vp[1] = (uint8_t)((v >> 8) & 0xffu);
    vp[2] = (uint8_t)((v >> 16) & 0xffu);
    vp[3] = (uint8_t)(v >> 24);
}

/* Build 11-bit (0..2047) -> 14-bit (0..16383) linear LUT from SonyToneCurve (Tag 0x7010) knots.
 * Matches LibRaw::parse_tiff_ifd case 0x7010 + sony_arw2_load_raw curve[pix << 1]. */
static void build_arw2_lut(uint16_t *lut, const uint8_t *src_data) {
    uint32_t knots[6];
    knots[0] = 0u;
    knots[1] = (uint32_t)(rd_u16_le(src_data + 0x40u) >> 2) & 0xfffu;
    knots[2] = (uint32_t)(rd_u16_le(src_data + 0x44u) >> 2) & 0xfffu;
    knots[3] = (uint32_t)(rd_u16_le(src_data + 0x48u) >> 2) & 0xfffu;
    knots[4] = (uint32_t)(rd_u16_le(src_data + 0x4cu) >> 2) & 0xfffu;
    knots[5] = 4095u;
    /* Fallback to Sony's standard IMX284 curve if knots are unpopulated/non-monotonic. */
    if (!(knots[1] > 0u && knots[1] < knots[2] && knots[2] < knots[3] &&
          knots[3] < knots[4] && knots[4] < 4095u)) {
        knots[1] = 520u;
        knots[2] = 1104u;
        knots[3] = 2040u;
        knots[4] = 3064u;
    }
    uint32_t cur = 0u;
    uint32_t idx = 0u;
    lut[0] = 0u;
    for (uint32_t seg = 0u; seg < 5u; seg++) {
        uint32_t step = 1u << seg;
        uint32_t end = knots[seg + 1u];
        while (idx < end && idx < 4095u) {
            idx++;
            cur += step;
            if (cur > 16383u) cur = 16383u;
            if ((idx & 1u) == 0u) lut[idx >> 1] = (uint16_t)cur;
        }
    }
    while ((idx >> 1) < 2047u) {
        idx += 2u;
        lut[idx >> 1] = (uint16_t)cur;
    }
}

/* Inverse LUT lookup: 14-bit linear (0..16383) -> 11-bit nonlinear ARW2 code (0..2047). */
static uint16_t arw2_lin_to_11b(const uint16_t *lut, uint16_t v14) {
    uint32_t lo = 0u, hi = 2047u;
    while (lo < hi) {
        uint32_t mid = (lo + hi + 1u) >> 1;
        if (lut[mid] <= v14) lo = mid;
        else hi = mid - 1u;
    }
    if (lo < 2047u) {
        uint32_t d0 = (uint32_t)v14 - (uint32_t)lut[lo];
        uint32_t d1 = (uint32_t)lut[lo + 1u] - (uint32_t)v14;
        if (d1 < d0) lo++;
    }
    return (uint16_t)lo;
}

/* Decode one 16-byte Sony ARW 2.3 block into 16 same-parity pixels at dst[0, 2, ..., 30]. */
static void decode_arw2_block(const uint8_t *dp, const uint16_t *lut, uint16_t *dst_stride2) {
    uint32_t val = rd_u32_le(dp);
    uint32_t max_v = val & 0x7ffu;
    uint32_t min_v = (val >> 11) & 0x7ffu;
    uint32_t imax  = (val >> 22) & 0x0fu;
    uint32_t imin  = (val >> 26) & 0x0fu;
    uint32_t sh = 0u;
    while (sh < 4u && (0x80u << sh) <= (max_v - min_v)) sh++;

    uint32_t bit = 30u;
    for (uint32_t i = 0u; i < 16u; i++) {
        uint32_t p11;
        if (i == imax) {
            p11 = max_v;
        } else if (i == imin) {
            p11 = min_v;
        } else {
            uint32_t bpos = bit >> 3;
            uint32_t raw2 = (bpos < 15u) ? rd_u16_le(dp + bpos) : (uint32_t)dp[15];
            uint32_t delta = (raw2 >> (bit & 7u)) & 0x7fu;
            p11 = (delta << sh) + min_v;
            if (p11 > 0x7ffu) p11 = 0x7ffu;
            bit += 7u;
        }
        dst_stride2[i << 1] = lut[p11];
    }
}

/* Encode 16 same-parity 14-bit pixels from src_stride2[0, 2, ..., 30] into one 16-byte ARW2 block. */
static void encode_arw2_block(const uint16_t *src_stride2, const uint16_t *lut, uint8_t *dp) {
    uint16_t p11[16];
    uint32_t max_v = 0u, min_v = 0x7ffu, imax = 0u, imin = 0u;
    for (uint32_t i = 0u; i < 16u; i++) {
        uint16_t c = arw2_lin_to_11b(lut, src_stride2[i << 1]);
        p11[i] = c;
        if (i == 0u || c > max_v) { max_v = c; imax = i; }
        if (i == 0u || c < min_v) { min_v = c; imin = i; }
    }
    if (imax == imin) {
        imin = (imax == 0u) ? 1u : 0u;
    }
    uint32_t sh = 0u;
    while (sh < 4u && (0x80u << sh) <= (max_v - min_v)) sh++;

    for (uint32_t k = 0u; k < 16u; k++) dp[k] = 0u;
    uint32_t hdr = (max_v & 0x7ffu) | ((min_v & 0x7ffu) << 11) |
                   ((imax & 0x0fu) << 22) | ((imin & 0x0fu) << 26);
    wr_u32_le(dp, hdr);

    uint32_t bit = 30u;
    uint32_t half_step = sh ? (1u << (sh - 1u)) : 0u;
    for (uint32_t i = 0u; i < 16u; i++) {
        if (i == imax || i == imin) continue;
        uint32_t diff = (uint32_t)p11[i] - min_v;
        uint32_t delta = (diff + half_step) >> sh;
        if (delta > 0x7fu) delta = 0x7fu;
        uint32_t byte_pos = bit >> 3;
        uint32_t bit_off  = bit & 7u;
        if (byte_pos < 15u) {
            uint32_t cur = rd_u16_le(dp + byte_pos) | (delta << bit_off);
            wr_u16_le(dp + byte_pos, (uint16_t)cur);
        } else {
            dp[15] = (uint8_t)(dp[15] | (delta << bit_off));
        }
        bit += 7u;
    }
}

static void copy_mem_words(uint8_t *dst, const uint8_t *src, uint32_t n) {
    uint32_t i = 0u;
    if ((((uintptr_t)dst | (uintptr_t)src) & 3u) == 0u) {
        for (; i + 4u <= n; i += 4u) {
            *(volatile uint32_t *)(dst + i) = *(const volatile uint32_t *)(src + i);
        }
    }
    for (; i < n; i++) dst[i] = src[i];
}

static void decode_input_row(const uint8_t *raw_base, uint32_t in_row_bytes, uint32_t in_w,
                             uint16_t in_comp, const uint16_t *lut, uint32_t y, uint16_t *dst,
                             uint8_t *scratch_row) {
    copy_mem_words(scratch_row, raw_base + y * in_row_bytes, in_row_bytes);
    const uint8_t *rp = scratch_row;
    if (in_comp == 32767u) {
        uint32_t col = 0u;
        const uint8_t *dp = rp;
        for (; col + 31u < in_w; col += 32u, dp += 32u) {
            decode_arw2_block(dp,       lut, dst + col);
            decode_arw2_block(dp + 16u, lut, dst + col + 1u);
        }
        /* Tail columns for widths like 6024 (= 188*32 + 8) stored in a final 32B block pair. */
        if (col < in_w && (uint32_t)(dp - rp) + 32u <= in_row_bytes) {
            uint16_t tmp[32];
            decode_arw2_block(dp,       lut, tmp);
            decode_arw2_block(dp + 16u, lut, tmp + 1u);
            for (uint32_t k = 0u; col + k < in_w; k++) dst[col + k] = tmp[k];
        } else {
            for (; col < in_w; col++) dst[col] = (col >= 2u) ? dst[col - 2u] : PB_BLACK_LEVEL;
        }
    } else {
        for (uint32_t x = 0u; x < in_w; x++) {
            dst[x] = rd_u16_le(rp + (x << 1));
        }
    }
}

static inline uint32_t above_black(uint16_t px) {
    return px > (uint16_t)PB_BLACK_LEVEL ? (uint32_t)(px - (uint16_t)PB_BLACK_LEVEL) : 0u;
}

static inline uint32_t clamp_idx(int32_t idx, uint32_t limit_minus_2, uint32_t parity) {
    if (idx < 0) return parity;
    if ((uint32_t)idx > limit_minus_2 + parity) return limit_minus_2 + parity;
    return (uint32_t)idx;
}

/* Scale photon sum `sum` whose unit-average shift is `avg_sh` according to Photon Gain:
 *   gain_mode 0 (+2 EV 4x): shift = avg_sh - 2
 *   gain_mode 1 (+1 EV 2x): shift = avg_sh - 1
 *   gain_mode 2 (+0 EV 1x): shift = avg_sh
 * Clamped to max_val (65535 for 16-bit Uncomp, 16383 for 14-bit Uncomp / 14-bit cRAW). */
static inline uint16_t finish_sample(uint32_t sum, uint32_t avg_sh, uint8_t gain_mode, uint32_t max_val) {
    uint32_t sub = (gain_mode == 0u) ? 2u : ((gain_mode == 1u) ? 1u : 0u);
    uint32_t sh = avg_sh - sub;
    uint32_t add = sh ? (1u << (sh - 1u)) : 0u;
    uint32_t out = (uint32_t)PB_BLACK_LEVEL + ((sum + add) >> sh);
    if (out > max_val) out = max_val;
    return (uint16_t)out;
}

/* Horizontal 1D phase-aligned filter on same-color Bayer sites:
 *   even output col (ox & 1 == 0): [1, 2, 1] at (x0 - 2, x0 + 0, x0 + 2), centroid = x0 + 0.0, weight = 4
 *   odd  output col (ox & 1 == 1): [1, 3, 3, 1] at (x0 - 1, x0 + 1, x0 + 3, x0 + 5), centroid = x0 + 2.0, weight = 8 */
static inline uint32_t horiz_phase_tap(const uint16_t *row, int32_t x0, uint32_t ox_odd, uint32_t w_m2) {
    if (!ox_odd) {
        uint32_t xm2 = clamp_idx(x0 - 2, w_m2, 0u);
        uint32_t x00 = clamp_idx(x0,     w_m2, 0u);
        uint32_t xp2 = clamp_idx(x0 + 2, w_m2, 0u);
        return above_black(row[xm2]) + (above_black(row[x00]) << 1) + above_black(row[xp2]);
    } else {
        uint32_t xm1 = clamp_idx(x0 - 1, w_m2, 1u);
        uint32_t xp1 = clamp_idx(x0 + 1, w_m2, 1u);
        uint32_t xp3 = clamp_idx(x0 + 3, w_m2, 1u);
        uint32_t xp5 = clamp_idx(x0 + 5, w_m2, 1u);
        uint32_t mid = above_black(row[xp1]) + above_black(row[xp3]);
        return above_black(row[xm1]) + mid + (mid << 1) + above_black(row[xp5]);
    }
}

/* Update crop & white-level IFD entries inside rawAdditionalInfo if present. */
static void update_additional_ifd(uint8_t *raw_info_area, uint16_t out_w, uint16_t out_h,
                                  uint16_t crop_x, uint16_t crop_y, uint16_t crop_w, uint16_t crop_h,
                                  uint16_t white_level) {
    uint32_t add_va = rd_u32_le(raw_info_area + 0x120u);
    uint32_t add_sz = rd_u32_le(raw_info_area + 0x124u);
    if (!add_va || add_sz < 16u) return;
    uint8_t *base = (uint8_t *)(uintptr_t)add_va;
    if (rd_u32_le(base) != 1u) return;
    uint32_t ifd_off = rd_u32_le(base + 8u);
    if (ifd_off + 4u > add_sz) return;
    uint32_t cnt = rd_u32_le(base + ifd_off);
    if (cnt > 64u || ifd_off + 4u + cnt * 12u > add_sz) return;

    /* SetRawAdditionalInfoToIfd resolves out-of-line values as (base + ifd_off) + val. */
    uint8_t *ifd = base + ifd_off;
    uint32_t room = add_sz - ifd_off;
    for (uint32_t i = 0u; i < cnt; i++) {
        uint8_t *ent = ifd + 4u + i * 12u;
        uint16_t tag  = rd_u16_le(ent + 0u);
        uint16_t type = rd_u16_le(ent + 2u);
        uint32_t ecnt = rd_u32_le(ent + 4u);
        uint32_t val  = rd_u32_le(ent + 8u);
        if (tag == 0xC61Fu && ecnt == 2u) { /* DefaultCropOrigin */
            if (type == 3u) {
                wr_u16_le(ent + 8u,  crop_x);
                wr_u16_le(ent + 10u, crop_y);
            } else if (type == 4u && val <= room && room - val >= 8u) {
                wr_u32_le(ifd + val,      crop_x);
                wr_u32_le(ifd + val + 4u, crop_y);
            }
        } else if (tag == 0xC620u && ecnt == 2u) { /* DefaultCropSize */
            if (type == 3u) {
                wr_u16_le(ent + 8u,  crop_w);
                wr_u16_le(ent + 10u, crop_h);
            } else if (type == 4u && val <= room && room - val >= 8u) {
                wr_u32_le(ifd + val,      crop_w);
                wr_u32_le(ifd + val + 4u, crop_h);
            }
        } else if (tag == 0xC68Du && ecnt == 4u && val <= room) { /* ActiveArea */
            if (type == 3u && room - val >= 8u) {
                wr_u16_le(ifd + val + 0u, 0u);
                wr_u16_le(ifd + val + 2u, 0u);
                wr_u16_le(ifd + val + 4u, out_h);
                wr_u16_le(ifd + val + 6u, out_w);
            } else if (type == 4u && room - val >= 16u) {
                wr_u32_le(ifd + val + 0u,  0u);
                wr_u32_le(ifd + val + 4u,  0u);
                wr_u32_le(ifd + val + 8u,  out_h);
                wr_u32_le(ifd + val + 12u, out_w);
            }
        } else if (tag == 0xC61Du && ecnt == 1u) { /* WhiteLevel */
            if (type == 3u) wr_u16_le(ent + 8u, white_level);
            else if (type == 4u) wr_u32_le(ent + 8u, white_level);
        }
    }
}

static void pb_process_raw_info_area(uint8_t *raw_info_area) {
    struct pb_bss_state *s = PB_BSS;
    if (!raw_info_area || !s->active) return;

    uint8_t bin_mode  = s->cfg[0]; /* 0 = 4x4->2x2 Bayer, 1 = 2x2 Mono Quad, 2 = Sensor Only */
    uint8_t gain_mode = s->cfg[1] <= 2u ? s->cfg[1] : 0u;
    uint8_t kern_mode = s->cfg[2] <= 1u ? s->cfg[2] : 0u;
    uint8_t arw_mode  = s->cfg[3] <= 2u ? s->cfg[3] : 0u;
    if (bin_mode >= 2u) {
        s->frames_skipped++;
        return; /* Sensor Only (24 MP stock geometry) */
    }

    uint32_t src_va   = rd_u32_le(raw_info_area + 0x0d0u);
    uint32_t src_sz   = rd_u32_le(raw_info_area + 0x0d4u);
    uint32_t raw_va   = rd_u32_le(raw_info_area + 0x110u);
    uint32_t raw_sz   = rd_u32_le(raw_info_area + 0x114u);
    uint32_t ext_va   = rd_u32_le(raw_info_area + 0x118u);
    uint32_t ext_cnt  = rd_u32_le(raw_info_area + 0x11cu);
    if (!src_va || src_sz < 0x64u || !raw_sz) {
        s->frames_skipped++;
        return;
    }

    uint8_t *src_data = (uint8_t *)(uintptr_t)src_va;
    uint16_t in_w    = rd_u16_le(src_data + 0x00u);
    uint16_t in_h    = rd_u16_le(src_data + 0x04u);
    uint16_t in_bps  = rd_u16_le(src_data + 0x08u);
    uint16_t in_comp = rd_u16_le(src_data + 0x0cu);
    if ((in_w == 3008u && in_h == 2008u) ||
        (s->frames_binned && (((uint32_t)in_w << 16) | in_h) == s->last_out_wh)) {
        /* Already binned on Call 1 (shared RawMmapInfo/src_data), but this call's stack
         * RawInfoArea was rebuilt from the original values: re-apply the trimmed fields. */
        if (s->frames_binned && s->last_out_bytes) {
            wr_u32_le(raw_info_area + 0x114u, s->last_out_bytes);
            if (s->last_ext_cnt) wr_u32_le(raw_info_area + 0x11cu, s->last_ext_cnt);
            if (s->add_valid) wr_u32_le(raw_info_area + 0x120u, (uint32_t)(uintptr_t)s->add_buf);
        }
        s->frames_skipped++;
        return;
    }
    if (in_w < 64u || in_w > PB_MAX_IN_W || (in_w & 3u) != 0u ||
        in_h < 16u || in_h > 4048u || (in_h & 3u) != 0u ||
        (in_comp != 32767u && in_comp != 1u) || (in_bps != 14u && in_bps != 16u)) {
        s->frames_skipped++;
        return;
    }

    uint32_t in_row_bytes;
    if (in_comp == 32767u) {
        uint32_t aligned32 = ((uint32_t)in_w + 31u) & ~31u;
        in_row_bytes = ((uint32_t)in_w * (uint32_t)in_h == raw_sz) ? (uint32_t)in_w : aligned32;
    } else {
        in_row_bytes = (uint32_t)in_w << 1;
    }
    if (in_row_bytes * (uint32_t)in_h > raw_sz) {
        s->frames_skipped++;
        return;
    }

    /* Output geometry: 6024x4024 -> 3008x2008 (94*32 multiple of 32, centered at (4,4) so
     * active 6000x4000 maps to exact 3000x2000 at (4,4) with unchanged RGGB phase). */
    uint32_t x_off = 0u, y_off = 0u;
    uint16_t out_w = (uint16_t)(in_w >> 1);
    uint16_t out_h = (uint16_t)(in_h >> 1);
    uint16_t crop_x = 0u, crop_y = 0u, crop_w = out_w, crop_h = out_h;
    if ((in_w == 6024u || in_w == 6048u) && in_h == 4024u) {
        x_off = 4u;
        y_off = 4u;
        out_w = 3008u;
        out_h = 2008u;
        crop_x = 4u;
        crop_y = 4u;
        crop_w = 3000u;
        crop_h = 2000u;
    } else if ((out_w & 31u) != 0u && arw_mode == 2u) {
        uint16_t trim_w = (uint16_t)(out_w & ~31u);
        x_off = (uint32_t)(out_w - trim_w) & ~1u;
        out_w = trim_w;
        crop_w = out_w;
    }

    uint32_t out_row_bytes = (arw_mode == 2u) ? (uint32_t)out_w : ((uint32_t)out_w << 1);
    uint32_t new_raw_bytes = out_row_bytes * (uint32_t)out_h;
    if (new_raw_bytes > raw_sz) {
        s->frames_skipped++;
        return;
    }

    /* Resolve rawMain virtual pointer (either already mapped or single contiguous physical extent). */
    uint8_t *raw_buf = (uint8_t *)(uintptr_t)raw_va;
    long mapped_fd = -1, mapped_base = 0;
    uint32_t mapped_len = 0u;
    if (!raw_buf && ext_va && ext_cnt == 1u) {
        struct pb_phys_ext *ext = (struct pb_phys_ext *)(uintptr_t)ext_va;
        if (ext[0].phys_addr && ext[0].size >= raw_sz) {
            mapped_fd = sc3(5 /* open */, (long)"/dev/mem", 02 /* O_RDWR */, 0);
            if (mapped_fd >= 0) {
                uint32_t pg_off = ext[0].phys_addr & 0xfffu;
                mapped_len = (raw_sz + pg_off + 0xfffu) & ~0xfffu;
                mapped_base = sc6(192 /* mmap2 */, 0, (long)mapped_len,
                                  3 /* PROT_READ | PROT_WRITE */, 1 /* MAP_SHARED */,
                                  mapped_fd, (long)(ext[0].phys_addr >> 12));
                if (!(mapped_base < 0 && mapped_base > -4096)) {
                    raw_buf = (uint8_t *)((uint32_t)mapped_base + pg_off);
                } else {
                    sc3(6 /* close */, mapped_fd, 0, 0);
                    mapped_fd = -1;
                }
            }
        }
    }
    if (!raw_buf) {
        s->frames_skipped++;
        return;
    }

    if (in_comp == 32767u || arw_mode == 2u) {
        build_arw2_lut(s->curve_lut, src_data);
    }

    uint32_t max_val = (arw_mode == 0u) ? 65535u : 16383u;
    uint32_t w_m2 = (uint32_t)in_w - 2u;
    uint32_t h_m2 = (uint32_t)in_h - 2u;
    uint32_t decoded_next_y = 0u;
    uint32_t qh = (uint32_t)(out_h >> 1);
    uint32_t qw = (uint32_t)(out_w >> 1);

    for (uint32_t qy = 0u; qy < qh; qy++) {
        int32_t y0 = (int32_t)(y_off + (qy << 2));
        uint32_t max_need_y = clamp_idx(y0 + 5, h_m2, 1u);
        while (decoded_next_y <= max_need_y && decoded_next_y < (uint32_t)in_h) {
            decode_input_row(raw_buf, in_row_bytes, in_w, in_comp, s->curve_lut,
                             decoded_next_y, s->row_ring[decoded_next_y & 7u], s->in_raw_row);
            decoded_next_y++;
        }

        for (uint32_t dy = 0u; dy < 2u; dy++) {
            uint32_t oy = (qy << 1) + dy;
            if (bin_mode == 1u) {
                /* 2x2 Mono Quad: sum adjacent (R + Gr + Gb + B) quad at (x_off + 2*ox, y_off + 2*oy). */
                uint32_t ry0 = (uint32_t)y_off + (oy << 1);
                const uint16_t *r0 = s->row_ring[ry0 & 7u];
                const uint16_t *r1 = s->row_ring[(ry0 + 1u) & 7u];
                for (uint32_t ox = 0u; ox < (uint32_t)out_w; ox++) {
                    uint32_t rx0 = x_off + (ox << 1);
                    uint32_t sum4 = above_black(r0[rx0]) + above_black(r0[rx0 + 1u]) +
                                    above_black(r1[rx0]) + above_black(r1[rx0 + 1u]);
                    s->out_row[ox] = finish_sample(sum4, 2u, gain_mode, max_val);
                }
            } else if (kern_mode == 1u) {
                /* 4x4->2x2 Bayer Box Sum: sum 4 same-color photosites inside the 4x4 block. */
                const uint16_t *r_a = s->row_ring[((uint32_t)y0 + dy) & 7u];
                const uint16_t *r_b = s->row_ring[((uint32_t)y0 + dy + 2u) & 7u];
                for (uint32_t qx = 0u; qx < qw; qx++) {
                    uint32_t rx0 = x_off + (qx << 2);
                    uint32_t s_ev = above_black(r_a[rx0 + 0u]) + above_black(r_a[rx0 + 2u]) +
                                    above_black(r_b[rx0 + 0u]) + above_black(r_b[rx0 + 2u]);
                    uint32_t s_od = above_black(r_a[rx0 + 1u]) + above_black(r_a[rx0 + 3u]) +
                                    above_black(r_b[rx0 + 1u]) + above_black(r_b[rx0 + 3u]);
                    s->out_row[(qx << 1) + 0u] = finish_sample(s_ev, 2u, gain_mode, max_val);
                    s->out_row[(qx << 1) + 1u] = finish_sample(s_od, 2u, gain_mode, max_val);
                }
            } else {
                /* 4x4->2x2 Bayer [1,2,1] / [1,3,3,1] Phase-Centered Binomial Filter:
                 * Exact 2.0-pixel uniform Cartesian lattice spacing across all 4 RGGB channels. */
                if (dy == 0u) {
                    const uint16_t *ym2 = s->row_ring[clamp_idx(y0 - 2, h_m2, 0u) & 7u];
                    const uint16_t *y00 = s->row_ring[clamp_idx(y0,     h_m2, 0u) & 7u];
                    const uint16_t *yp2 = s->row_ring[clamp_idx(y0 + 2, h_m2, 0u) & 7u];
                    for (uint32_t qx = 0u; qx < qw; qx++) {
                        int32_t x0 = (int32_t)(x_off + (qx << 2));
                        uint32_t s_r = horiz_phase_tap(ym2, x0, 0u, w_m2) +
                                       (horiz_phase_tap(y00, x0, 0u, w_m2) << 1) +
                                       horiz_phase_tap(yp2, x0, 0u, w_m2);
                        uint32_t s_gr = horiz_phase_tap(ym2, x0, 1u, w_m2) +
                                        (horiz_phase_tap(y00, x0, 1u, w_m2) << 1) +
                                        horiz_phase_tap(yp2, x0, 1u, w_m2);
                        s->out_row[(qx << 1) + 0u] = finish_sample(s_r,  4u, gain_mode, max_val);
                        s->out_row[(qx << 1) + 1u] = finish_sample(s_gr, 5u, gain_mode, max_val);
                    }
                } else {
                    const uint16_t *ym1 = s->row_ring[clamp_idx(y0 - 1, h_m2, 1u) & 7u];
                    const uint16_t *yp1 = s->row_ring[clamp_idx(y0 + 1, h_m2, 1u) & 7u];
                    const uint16_t *yp3 = s->row_ring[clamp_idx(y0 + 3, h_m2, 1u) & 7u];
                    const uint16_t *yp5 = s->row_ring[clamp_idx(y0 + 5, h_m2, 1u) & 7u];
                    for (uint32_t qx = 0u; qx < qw; qx++) {
                        int32_t x0 = (int32_t)(x_off + (qx << 2));
                        uint32_t m_gb = horiz_phase_tap(yp1, x0, 0u, w_m2) +
                                        horiz_phase_tap(yp3, x0, 0u, w_m2);
                        uint32_t s_gb = horiz_phase_tap(ym1, x0, 0u, w_m2) +
                                        m_gb + (m_gb << 1) +
                                        horiz_phase_tap(yp5, x0, 0u, w_m2);
                        uint32_t m_b  = horiz_phase_tap(yp1, x0, 1u, w_m2) +
                                        horiz_phase_tap(yp3, x0, 1u, w_m2);
                        uint32_t s_b  = horiz_phase_tap(ym1, x0, 1u, w_m2) +
                                        m_b + (m_b << 1) +
                                        horiz_phase_tap(yp5, x0, 1u, w_m2);
                        s->out_row[(qx << 1) + 0u] = finish_sample(s_gb, 5u, gain_mode, max_val);
                        s->out_row[(qx << 1) + 1u] = finish_sample(s_b,  6u, gain_mode, max_val);
                    }
                }
            }

            /* Serialize output row oy into cached out_raw_row, then word-copy to raw_buf. */
            uint8_t *wp = s->out_raw_row;
            if (arw_mode == 2u) {
                for (uint32_t col = 0u; col + 31u < (uint32_t)out_w; col += 32u, wp += 32u) {
                    encode_arw2_block(s->out_row + col,      s->curve_lut, wp);
                    encode_arw2_block(s->out_row + col + 1u, s->curve_lut, wp + 16u);
                }
            } else {
                for (uint32_t ox = 0u; ox < (uint32_t)out_w; ox++) {
                    wr_u16_le(wp + (ox << 1), s->out_row[ox]);
                }
            }
            copy_mem_words(raw_buf + oy * out_row_bytes, s->out_raw_row, out_row_bytes);
        }
    }

    if (mapped_fd >= 0) {
        __asm__ volatile("dsb sy" ::: "memory");
        sc3(91 /* munmap */, mapped_base, (long)mapped_len, 0);
        sc3(6 /* close */, mapped_fd, 0, 0);
    }

    /* Update rawMainSrcData (MakeRawMainIfds SubIFD inputs) and rawMain size/extents. */
    uint16_t out_bps  = (arw_mode == 0u) ? 16u : 14u;
    uint16_t out_comp = (arw_mode == 2u) ? 32767u : 1u;
    uint16_t out_srft = (arw_mode == 2u) ? 2u : 1u;
    wr_u16_le(src_data + 0x00u, out_w);
    wr_u16_le(src_data + 0x04u, out_h);
    wr_u16_le(src_data + 0x08u, out_bps);
    wr_u16_le(src_data + 0x0cu, out_comp);
    wr_u16_le(src_data + 0x38u, out_srft);
    wr_u16_le(src_data + 0x60u, out_h);      /* RowsPerStrip */
    wr_u32_le(raw_info_area + 0x114u, new_raw_bytes);

    if (ext_va && ext_cnt > 0u && ext_cnt <= 64u) {
        struct pb_phys_ext *ext = (struct pb_phys_ext *)(uintptr_t)ext_va;
        uint32_t rem = new_raw_bytes;
        uint32_t kept = 0u;
        for (uint32_t i = 0u; i < ext_cnt && rem > 0u; i++) {
            /* libObj validates (phys & 0xfff) == 0 && (size & 0xfff) == 0 on these. */
            uint32_t want = (rem + 0xfffu) & ~0xfffu;
            if (ext[i].size > want) ext[i].size = want;
            rem = (rem > ext[i].size) ? rem - ext[i].size : 0u;
            kept++;
        }
        wr_u32_le(raw_info_area + 0x11cu, kept);
        s->last_ext_cnt = kept;
    }

    uint16_t white_lvl = 16383u;
    if (arw_mode == 0u) {
        white_lvl = (gain_mode == 0u) ? 63996u : ((gain_mode == 1u) ? 32254u : 16383u);
    }
    uint32_t add_va = rd_u32_le(raw_info_area + 0x120u);
    uint32_t add_sz = rd_u32_le(raw_info_area + 0x124u);
    if (add_va && add_sz >= 16u && add_sz <= sizeof(s->add_buf)) {
        copy_mem_words(s->add_buf, (const uint8_t *)(uintptr_t)add_va, add_sz);
        wr_u32_le(raw_info_area + 0x120u, (uint32_t)(uintptr_t)s->add_buf);
        s->add_valid = 1u;
    } else {
        s->add_valid = 0u;
    }
    update_additional_ifd(raw_info_area, out_w, out_h, crop_x, crop_y, crop_w, crop_h, white_lvl);

    s->frames_binned++;
    s->last_in_wh     = ((uint32_t)in_w << 16) | in_h;
    s->last_out_wh    = ((uint32_t)out_w << 16) | out_h;
    s->last_out_bytes = new_raw_bytes;

    lbuf_t l;
    l.n = 0;
    lb_str(&l, "pb_arw #");
    lb_hex(&l, s->frames_binned, 4);
    lb_str(&l, " in=");
    lb_hex(&l, in_w, 4);
    lb_ch(&l, 'x');
    lb_hex(&l, in_h, 4);
    lb_str(&l, " out=");
    lb_hex(&l, out_w, 4);
    lb_ch(&l, 'x');
    lb_hex(&l, out_h, 4);
    lb_str(&l, " sz=");
    lb_hex(&l, new_raw_bytes, 8);
    lb_str(&l, " cfg=");
    for (int i = 0; i < 6; i++) lb_ch(&l, (char)('0' + (s->cfg[i] & 7u)));
    lb_ch(&l, '\n');
    lb_flush(&l);
}

/* ---- SMOD v2 exported entry points ---- */
__attribute__((visibility("default"), used))
int pb_hook_make_raw_info_area(uint8_t *raw_info_area, void *src, void *out) {
    int rc = pb_orig_make_raw_info_area(raw_info_area, src, out);
    if (rc == 0) {
        pb_process_raw_info_area(raw_info_area);
    }
    return rc;
}

__attribute__((visibility("default"), used))
void pb_on(int on) {
    (void)on;
    struct pb_bss_state *s = PB_BSS;
    s->magic = PB_MAGIC;
    s->active = 1u;
    pb_apply_option3(1, 1);
}

__attribute__((visibility("default"), used))
void pb_off(int on) {
    (void)on;
    struct pb_bss_state *s = PB_BSS;
    s->active = 0u;
    pb_apply_option3(0, 0);
    s->magic = 0u;
}

__attribute__((visibility("default"), used))
void pb_cfg(int reason) {
    struct pb_bss_state *s = PB_BSS;
    if (s->active) {
        pb_apply_option3(1, reason);
    }
}
