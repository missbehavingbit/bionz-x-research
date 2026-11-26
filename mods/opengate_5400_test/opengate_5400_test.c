/* OpenGate_5400_Test: SMOD v2 mod to test 5400x3600 @ 24p IMX284 -> ISP -> 3240x2160.
 *
 * Patches live av-cam.bin (dd8f8d0cc74ad4b7) in LIRO RAM via /dev/mem:
 *   1. IMX284 Mode 16 (0x03E25430) & Mode 17 (0x03E2547C) geometry (5400x3600)
 *   2. Mode 16 NTSC (0x03E2598C) & PAL (0x03E25B54) timings (810x1852, 23.998 fps)
 *   3. 3240x2160 DMM/VPI/stream u32 tables (0x03DF19FC..0x04570198)
 *   4. 30 Movie/VENC u16 (3840, 2160) -> (3240, 2160) profile entries (0x03E11750..0x03E3C41C)
 *
 * Patches live /usr/lib/libmpr.so (bb31351cbd546d88) in im.elf via mprotect:
 *   5. 4K u32 record at VA 0x07B9DE78 (3840 -> 3240)
 *   6. 3 u16 (3840, 2160) records at VA 0x07AAEC34, 0x07AAEC40, 0x07B9E0C4
 *
 * Fail-closed: ON writes only if stock value is present; OFF restores only if
 * patched value is present. Logs to /tmp_bt/mods/opengate_5400_test.log.
 */
#include <stdint.h>

#ifdef TARGET_ILCE6400A
#define M16_BASE_PA    0x03E27DE0u
#define M17_BASE_PA    0x03E27E2Cu
#define T16_NTSC_PA    0x03E2833Cu
#define T16_PAL_PA     0x03E28504u
#define DMM_4K_0_PA    0x03DF45E4u
#define DMM_4K_1_PA    0x03DF468Cu
#define DMM_4K_2_PA    0x03DF4734u
#define VPI_4K_0_PA    0x03DF4B3Cu
#define VPI_4K_1_PA    0x04548678u
#define VPI_4K_2_PA    0x045747A8u
#define LIBMPR_BASE    0x075CE000u
#else
#define M16_BASE_PA    0x03E25430u
#define M17_BASE_PA    0x03E2547Cu
#define T16_NTSC_PA    0x03E2598Cu
#define T16_PAL_PA     0x03E25B54u
#define DMM_4K_0_PA    0x03DF19FCu
#define DMM_4K_1_PA    0x03DF1AA4u
#define DMM_4K_2_PA    0x03DF1B4Cu
#define VPI_4K_0_PA    0x03DF1EC0u
#define VPI_4K_1_PA    0x0454388Cu
#define VPI_4K_2_PA    0x04570198u
#define LIBMPR_BASE    0x07144000u
#endif
#define AIF0_W1_PA     0xF3002400u
#define AIF0_W2_PA     0xF3002480u

#define U16_3840_2160  0x08700F00u
#define U16_3240_2160  0x08700CA8u

typedef struct {
    uint32_t pa;
    uint32_t stock;
    uint32_t patched;
} patch_entry_t;

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

static char swap_u32(long fd, uint32_t pa, uint32_t expected, uint32_t target, uint32_t *out_val) {
    long base = sc6(192 /* mmap2 */, 0, 4096, 3 /* PROT_READ | PROT_WRITE */, 1 /* MAP_SHARED */,
                    fd, (long)(pa >> 12));
    if (base < 0 && base > -4096) {
        return 'm';
    }
    volatile uint32_t *p = (volatile uint32_t *)(base + (pa & 0xfffu));
    uint32_t cur = *p;
    char code = 'x';
    if (cur == target) {
        code = 'a';
    } else if (cur == expected) {
        *p = target;
        __asm__ volatile("dsb" ::: "memory");
        cur = *p;
        code = (cur == target) ? 'w' : 'v';
    }
    *out_val = cur;
    sc3(91 /* munmap */, base, 4096, 0);
    return code;
}

static uint32_t find_libmpr_base(void) {
    long fd = sc3(5 /* open */, (long)"/proc/self/maps", 0 /* O_RDONLY */, 0);
    if (fd < 0) return 0u;
    char buf[256], line[192];
    int lpos = 0, got;
    uint32_t base = 0u;
    while (!base && (got = (int)sc3(3 /* read */, fd, (long)buf, sizeof(buf))) > 0) {
        for (int k = 0; k < got; k++) {
            char ch = buf[k];
            if (ch != '\n') {
                if (lpos + 1 < (int)sizeof(line)) line[lpos++] = ch;
                continue;
            }
            line[lpos] = 0;
            uint32_t lo = 0u, off = 0u;
            int i = 0;
            while (line[i] && line[i] != '-') {
                char c = line[i++];
                uint32_t d = (c >= '0' && c <= '9') ? (uint32_t)(c - '0') : (uint32_t)((c | 0x20) - 'a' + 10);
                lo = (lo << 4) | d;
            }
            while (line[i] && line[i] != ' ') i++;
            while (line[i] == ' ') i++;
            while (line[i] && line[i] != ' ') i++;
            while (line[i] == ' ') i++;
            while (line[i] && line[i] != ' ') {
                char c = line[i++];
                uint32_t d = (c >= '0' && c <= '9') ? (uint32_t)(c - '0') : (uint32_t)((c | 0x20) - 'a' + 10);
                off = (off << 4) | d;
            }
            if (off == 0u && lpos >= 10) {
                const char *suf = "/libmpr.so";
                int match = 1;
                for (int s = 0; s < 10; s++) {
                    if (line[lpos - 10 + s] != suf[s]) { match = 0; break; }
                }
                if (match) base = lo;
            }
            lpos = 0;
        }
    }
    sc3(6 /* close */, fd, 0, 0);
    return base;
}

static char swap_proc_u32(uint32_t va, uint32_t expected, uint32_t target, uint32_t *out_val) {
    volatile uint32_t *p = (volatile uint32_t *)(uintptr_t)va;
    uint32_t cur = p[0];
    if (cur == target) {
        *out_val = cur;
        return 'a';
    }
    if (cur != expected) {
        *out_val = cur;
        return 'x';
    }
    uint32_t page = va & ~0xfffu;
    if (sc3(125 /* mprotect */, (long)page, 4096, 7 /* RWX */) < 0) {
        *out_val = cur;
        return 'p';
    }
    p[0] = target;
    __asm__ volatile("dsb" ::: "memory");
    cur = p[0];
    sc3(125 /* mprotect */, (long)page, 4096, 5 /* RX */);
    *out_val = cur;
    return (cur == target) ? 'w' : 'v';
}

static int read_u32_ro(uint32_t pa, uint32_t *out_val) {
    long fd = sc3(5 /* open */, (long)"/dev/mem", 00 | 04010000 /* O_RDONLY | O_SYNC */, 0);
    if (fd < 0) return 0;
    long base = sc6(192 /* mmap2 */, 0, 4096, 1 /* PROT_READ */, 1 /* MAP_SHARED */, fd, (long)(pa >> 12));
    if (base < 0 && base > -4096) {
        sc3(6 /* close */, fd, 0, 0);
        return 0;
    }
    *out_val = *(const volatile uint32_t *)(base + (pa & 0xfffu));
    sc3(91 /* munmap */, base, 4096, 0);
    sc3(6 /* close */, fd, 0, 0);
    return 1;
}

typedef struct { char b[384]; int n; } lbuf_t;
static void pc(lbuf_t *l, char c) { if (l->n < 382) l->b[l->n++] = c; }
static void ps(lbuf_t *l, const char *s) { while (*s) pc(l, *s++); }
static void ph(lbuf_t *l, uint32_t v) {
    for (int i = 28; i >= 0; i -= 4) {
        uint32_t d = (v >> i) & 0xfu;
        pc(l, (char)(d < 10 ? '0' + d : 'a' + d - 10));
    }
}

static void run_mod(int enable) {
    static const patch_entry_t patches[27] = {
        { M16_BASE_PA + 0x14u,    4u,  304u },
        { M16_BASE_PA + 0x1Cu, 6000u, 5400u },
        { M16_BASE_PA + 0x34u,    2u,  200u },
        { M16_BASE_PA + 0x3Cu, 3376u, 3600u },
        { M17_BASE_PA + 0x14u,    4u,  304u },
        { M17_BASE_PA + 0x1Cu, 6000u, 5400u },
        { M17_BASE_PA + 0x34u,    2u,  200u },
        { M17_BASE_PA + 0x3Cu, 3376u, 3600u },
        { T16_NTSC_PA + 0x00u,  862u,  810u },
        { T16_NTSC_PA + 0x04u, 1740u, 1852u },
        { T16_NTSC_PA + 0x0Cu,  862u,  810u },
        { T16_NTSC_PA + 0x10u, 1740u, 1852u },
        { T16_PAL_PA  + 0x00u,  862u,  810u },
        { T16_PAL_PA  + 0x04u, 1740u, 1852u },
        { T16_PAL_PA  + 0x0Cu,  862u,  810u },
        { T16_PAL_PA  + 0x10u, 1740u, 1852u },
        { DMM_4K_0_PA + 0x00u, 3840u, 3240u },
        { DMM_4K_0_PA + 0x08u, 3840u, 3264u },
        { DMM_4K_1_PA + 0x00u, 3840u, 3240u },
        { DMM_4K_1_PA + 0x08u, 3840u, 3264u },
        { DMM_4K_2_PA + 0x00u, 3840u, 3240u },
        { DMM_4K_2_PA + 0x08u, 3840u, 3264u },
        { VPI_4K_0_PA + 0x00u, 3840u, 3240u },
        { VPI_4K_0_PA + 0x0Cu, 3840u, 3240u },
        { VPI_4K_1_PA + 0x00u, 3840u, 3240u },
        { VPI_4K_1_PA + 0x08u, 3840u, 3240u },
        { VPI_4K_2_PA + 0x00u, 3840u, 3240u },
    };
#ifdef TARGET_ILCE6400A
    static const uint32_t venc_u16_pas[14] = {
        0x03E3959Cu, 0x03E395FCu, 0x03E3977Cu, 0x03E397E0u, 0x03E39844u, 0x03E398A8u, 0x03E3990Cu, 0x03E39970u,
        0x03E3A5F0u, 0x03E3A654u, 0x03E3A6B8u, 0x03E3A71Cu, 0x03E3A780u, 0x03E3A7E4u,
    };
    static const uint32_t libmpr_u32_offs[6] = {
        0x00A2F3D4u, 0x00A2F3E4u, 0x00A2F3F4u, 0x00A2F404u, 0x00A2F414u, 0x00A5A130u,
    };
    static const uint32_t libmpr_u16_offs[4] = {
        0x007C81E0u, 0x007C9A78u, 0x008EBE20u, 0x00A5A37Cu,
    };
#else
    static const uint32_t venc_u16_pas[14] = {
        0x03E369F4u, 0x03E36A54u, 0x03E36BD4u, 0x03E36C38u, 0x03E36C9Cu, 0x03E36D00u, 0x03E36D64u, 0x03E36DC8u,
        0x03E37A48u, 0x03E37AACu, 0x03E37B10u, 0x03E37B74u, 0x03E37BD8u, 0x03E37C3Cu,
    };
    static const uint32_t libmpr_u32_offs[6] = {
        0x00A2F114u, 0x00A2F124u, 0x00A2F134u, 0x00A2F144u, 0x00A2F154u, 0x00A59E78u,
    };
    static const uint32_t libmpr_u16_offs[4] = {
        0x007C7FD0u, 0x007C9868u, 0x008EBC28u, 0x00A5A0C4u,
    };
#endif

    lbuf_t l;
    l.n = 0;
    ps(&l, enable ? "OG5400_ON " : "OG5400_OFF ");

    long fd = sc3(5 /* open */, (long)"/dev/mem", 02 | 04010000 /* O_RDWR | O_SYNC */, 0);
    if (fd < 0) {
        ps(&l, "open_err\n");
    } else {
        ps(&l, "c=");
        int ok = 0;
        uint32_t last_w = 0, last_h = 0, mpr_w = 0;
#ifdef OG_SENSOR_ONLY
        const int patch_cnt = 16;
#else
        const int patch_cnt = 27;
#endif
        for (int i = 0; i < patch_cnt; i++) {
            uint32_t exp_v = enable ? patches[i].stock : patches[i].patched;
            uint32_t tgt_v = enable ? patches[i].patched : patches[i].stock;
            uint32_t out_v = 0;
            char c = swap_u32(fd, patches[i].pa, exp_v, tgt_v, &out_v);
            pc(&l, c);
            if (c == 'w' || c == 'a') ok++;
            if (i == 1) last_w = out_v;
            if (i == 3) last_h = out_v;
        }
#ifdef OG_SENSOR_ONLY
        ps(&l, " v=skip");
        (void)venc_u16_pas;
#else
        ps(&l, " v=");
        for (int i = 0; i < 14; i++) {
            uint32_t out_v = 0;
            char c = swap_u32(fd, venc_u16_pas[i],
                              enable ? U16_3840_2160 : U16_3240_2160,
                              enable ? U16_3240_2160 : U16_3840_2160,
                              &out_v);
            pc(&l, c);
            if (c == 'w' || c == 'a') ok++;
        }
#endif
        sc3(6 /* close */, fd, 0, 0);

#ifdef OG_ENABLE_LIBMPR
        ps(&l, " m=");
        uint32_t mpr_base = find_libmpr_base();
        if (!mpr_base) {
            ps(&l, "nomap");
        } else {
            for (int i = 0; i < 6; i++) {
                uint32_t out_v = 0;
                char c = swap_proc_u32(mpr_base + libmpr_u32_offs[i], enable ? 3840u : 3240u, enable ? 3240u : 3840u, &out_v);
                pc(&l, c);
                if (c == 'w' || c == 'a') ok++;
                if (i == 5) mpr_w = out_v;
            }
            for (int i = 0; i < 4; i++) {
                uint32_t out_v = 0;
                char c = swap_proc_u32(mpr_base + libmpr_u16_offs[i],
                                       enable ? U16_3840_2160 : U16_3240_2160,
                                       enable ? U16_3240_2160 : U16_3840_2160,
                                       &out_v);
                pc(&l, c);
                if (c == 'w' || c == 'a') ok++;
            }
        }
#else
        ps(&l, " m=skip");
        (void)libmpr_u32_offs;
        (void)libmpr_u16_offs;
        (void)find_libmpr_base;
        (void)swap_proc_u32;
#endif

        uint32_t w1 = 0, w2_sz = 0;
        read_u32_ro(AIF0_W1_PA + 0x24u, &w1);
        read_u32_ro(AIF0_W2_PA + 0x24u, &w2_sz);
        ps(&l, " ok="); ph(&l, (uint32_t)ok);
        ps(&l, " m16w="); ph(&l, last_w);
        ps(&l, " m16h="); ph(&l, last_h);
        ps(&l, " mprw="); ph(&l, mpr_w);
        ps(&l, " w1="); ph(&l, w1);
        ps(&l, " w2="); ph(&l, w2_sz);
        pc(&l, '\n');
    }

    long logfd = sc3(5 /* open */, (long)"/tmp_bt/mods/opengate_5400_test.log",
                     01 | 0100 | 02000 /* O_WRONLY|O_CREAT|O_APPEND */, 0644);
    if (logfd >= 0) {
        sc3(4 /* write */, logfd, (long)l.b, (long)l.n);
        sc3(6 /* close */, logfd, 0, 0);
    }
}

__attribute__((visibility("default"))) void og5400_on(void) { run_mod(1); }
__attribute__((visibility("default"))) void og5400_off(void) { run_mod(0); }
