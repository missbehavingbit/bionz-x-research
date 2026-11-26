/* RCRAW_W2_Probe: read-only SMOD v2 diagnostic probe for AIF0/1 W1/W2 (RCRAW)
 * and SET_RAW_EXTERNAL_OUTPUT state in LIRO RAM.
 *
 * Strictly read-only on /dev/mem (O_RDONLY | O_SYNC, PROT_READ):
 *   - AIF0 W1 @ PA 0xF3002400 (+0x00 en, +0x04 run, +0x10 cuAdrs, +0x24 size, +0x2C loop)
 *   - AIF0 W2 @ PA 0xF3002480 (RCRAW DDR write channel)
 *   - AIF1 W2 @ PA 0xF3102480
 *   - SET_RAW_EXTERNAL_OUTPUT state @ PA 0x04407C40 (+0x4AC byte, +0x4B4 word -> page 0x04408000)
 *
 * Log: /tmp_bt/mods/rcraw_w2_probe.log
 */
#include <stdint.h>

#define AIF0_PAGE_PA   0xF3002000u
#define AIF1_PAGE_PA   0xF3102000u
#ifdef TARGET_ILCE6400A
#define RAW_EXT_PA     0x044295BCu /* 0x04429110 + 0x4AC */
#else
#define RAW_EXT_PA     0x044080ECu /* 0x04407C40 + 0x4AC */
#endif

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

typedef struct {
    uint32_t en;
    uint32_t run;
    uint32_t cu_adrs;
    uint32_t size;
    uint32_t loop;
    int ok;
} aif_snap_t;

static aif_snap_t read_aif_ro(uint32_t pa) {
    aif_snap_t out = {0, 0, 0, 0, 0, 0};
    long fd = sc3(5 /* open */, (long)"/dev/mem", 00 | 04010000 /* O_RDONLY | O_SYNC */, 0);
    if (fd < 0) return out;
    long base = sc6(192 /* mmap2 */, 0, 4096, 1 /* PROT_READ */, 1 /* MAP_SHARED */, fd, (long)(pa >> 12));
    if (base < 0 && base > -4096) {
        sc3(6 /* close */, fd, 0, 0);
        return out;
    }
    const volatile uint8_t *p = (const volatile uint8_t *)(base + (pa & 0xfffu));
    out.en      = *(const volatile uint32_t *)(p + 0x00) & 1u;
    out.run     = *(const volatile uint32_t *)(p + 0x04) >> 31;
    out.cu_adrs = *(const volatile uint32_t *)(p + 0x10);
    out.size    = *(const volatile uint32_t *)(p + 0x24);
    out.loop    = *(const volatile uint32_t *)(p + 0x2c) & 0xffffu;
    out.ok      = 1;
    sc3(91 /* munmap */, base, 4096, 0);
    sc3(6 /* close */, fd, 0, 0);
    return out;
}

static int read_raw_ext_ro(uint8_t *out_b, uint32_t *out_w) {
    long fd = sc3(5 /* open */, (long)"/dev/mem", 00 | 04010000 /* O_RDONLY | O_SYNC */, 0);
    if (fd < 0) return 0;
    long base = sc6(192 /* mmap2 */, 0, 4096, 1 /* PROT_READ */, 1 /* MAP_SHARED */, fd, (long)(RAW_EXT_PA >> 12));
    if (base < 0 && base > -4096) {
        sc3(6 /* close */, fd, 0, 0);
        return 0;
    }
    const volatile uint8_t *p = (const volatile uint8_t *)(base + (RAW_EXT_PA & 0xfffu));
    *out_b = p[0];
    *out_w = *(const volatile uint32_t *)(p + 8);
    sc3(91 /* munmap */, base, 4096, 0);
    sc3(6 /* close */, fd, 0, 0);
    return 1;
}

static char *put_str(char *dst, const char *s) {
    while (*s) *dst++ = *s++;
    return dst;
}

static char *put_hex(char *dst, uint32_t v, int digits) {
    for (int i = (digits - 1) * 4; i >= 0; i -= 4) {
        uint32_t nib = (v >> i) & 0xfu;
        *dst++ = (char)(nib < 10 ? ('0' + nib) : ('a' + nib - 10));
    }
    return dst;
}

static char *put_aif(char *p, const char *label, const aif_snap_t *s) {
    p = put_str(p, label);
    if (!s->ok) return put_str(p, "err ");
    *p++ = (char)('0' + s->en);
    *p++ = ',';
    *p++ = (char)('0' + s->run);
    *p++ = ',';
    p = put_hex(p, s->cu_adrs, 8);
    *p++ = ',';
    p = put_hex(p, s->size, 8);
    *p++ = ',';
    p = put_hex(p, s->loop, 4);
    *p++ = ' ';
    return p;
}

__attribute__((visibility("default")))
int probe_life(int on) {
    aif_snap_t w1  = read_aif_ro(AIF0_PAGE_PA + 0x400u);
    aif_snap_t w2  = read_aif_ro(AIF0_PAGE_PA + 0x480u);
    aif_snap_t w2b = read_aif_ro(AIF1_PAGE_PA + 0x480u);
    uint8_t ext_b = 0;
    uint32_t ext_w = 0;
    int ext_ok = read_raw_ext_ro(&ext_b, &ext_w);

    char buf[224];
    char *p = buf;
    p = put_str(p, "rcraw_w2 on=");
    *p++ = on ? '1' : '0';
    *p++ = ' ';
    p = put_aif(p, "w1=", &w1);
    p = put_aif(p, "w2=", &w2);
    p = put_aif(p, "w2b=", &w2b);
    p = put_str(p, "ext=");
    if (ext_ok) {
        p = put_hex(p, ext_b, 2);
        *p++ = ',';
        p = put_hex(p, ext_w, 8);
    } else {
        p = put_str(p, "err");
    }
    *p++ = '\n';

    long fd = sc3(5 /* open */, (long)"/tmp_bt/mods/rcraw_w2_probe.log",
                  01 | 0100 | 02000 /* O_WRONLY | O_CREAT | O_APPEND */, 0644);
    if (fd >= 0) {
        sc3(4 /* write */, fd, (long)buf, (long)(p - buf));
        sc3(6 /* close */, fd, 0, 0);
    }
    return 0;
}
