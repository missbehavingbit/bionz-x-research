/* RCRAW_Enable_Test: first functional SMOD v2 test mod for >8-bit RCRAW path.
 *
 * Performs a guarded 32-bit swap in LIRO RAM through /dev/mem:
 *   - RCRAW global enable word @ PA 0x043D1810 (VA 0x643D1810, read by 0x63B3CAC4)
 *     Stock = 0x00000000, Enabled = 0x00000001.
 *     ON  writes 1 only if stock (0) is present; leaves foreign values untouched.
 *     OFF restores 0 only if 1 is present; leaves foreign values untouched.
 *
 * Also performs read-only diagnostic snapshots of:
 *   - RDRAW global enable word @ PA 0x043D1BCC (VA 0x643D1BCC, read by 0x63B3CAD0)
 *   - AIF0 W2 (RCRAW DDR write channel) @ PA 0xF3002480
 *   - RCRAW compressor register 0xF30317C8 @ PA 0xF30317C8
 *
 * Log: /tmp_bt/mods/rcraw_enable_test.log
 */
#include <stdint.h>

#ifdef TARGET_ILCE6400A
#define RCRAW_EN_PA   0x043F2B04u
#define RDRAW_EN_PA   0x043F2EC0u
#define D1534_PA      0x043F2828u
#else
#define RCRAW_EN_PA   0x043D1810u
#define RDRAW_EN_PA   0x043D1BCCu
#define D1534_PA      0x043D1534u
#endif
#define AIF0_W1_PA    0xF3002400u
#define AIF0_W2_PA    0xF3002480u
#define RCRAW_7C8_PA  0xF30317C8u

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

static char swap_u32(uint32_t pa, uint32_t expected, uint32_t target, uint32_t *out_val) {
    long fd = sc3(5 /* open */, (long)"/dev/mem", 02 | 04010000 /* O_RDWR | O_SYNC */, 0);
    if (fd < 0) return 'o';
    long base = sc6(192 /* mmap2 */, 0, 4096, 3 /* PROT_READ | PROT_WRITE */, 1 /* MAP_SHARED */,
                    fd, (long)(pa >> 12));
    if (base < 0 && base > -4096) {
        sc3(6 /* close */, fd, 0, 0);
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
    sc3(6 /* close */, fd, 0, 0);
    return code;
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

static int rcraw_life(int on) {
    uint32_t val = 0;
    char sw = on ? swap_u32(RCRAW_EN_PA, 0u, 1u, &val)
                 : swap_u32(RCRAW_EN_PA, 1u, 0u, &val);

    uint32_t rdraw = 0;
    int rdraw_ok = read_u32_ro(RDRAW_EN_PA, &rdraw);

    uint32_t d534 = 0;
    int d534_ok = read_u32_ro(D1534_PA, &d534);

    uint32_t q7c8 = 0;
    int q7c8_ok = read_u32_ro(RCRAW_7C8_PA, &q7c8);

    aif_snap_t w1 = read_aif_ro(AIF0_W1_PA);
    aif_snap_t w2 = read_aif_ro(AIF0_W2_PA);

    char buf[224];
    char *p = buf;
    p = put_str(p, "rcraw_en on=");
    *p++ = on ? '1' : '0';
    p = put_str(p, " sw=");
    *p++ = sw;
    p = put_str(p, " val=");
    p = put_hex(p, val, 8);
    p = put_str(p, " rdraw=");
    if (rdraw_ok) p = put_hex(p, rdraw, 8);
    else p = put_str(p, "err");
    p = put_str(p, " d534=");
    if (d534_ok) p = put_hex(p, d534, 8);
    else p = put_str(p, "err");
    p = put_str(p, " w1=");
    if (w1.ok) {
        *p++ = (char)('0' + w1.en);
        *p++ = ',';
        *p++ = (char)('0' + w1.run);
        *p++ = ',';
        p = put_hex(p, w1.cu_adrs, 8);
    } else {
        p = put_str(p, "err");
    }
    p = put_str(p, " w2=");
    if (w2.ok) {
        *p++ = (char)('0' + w2.en);
        *p++ = ',';
        *p++ = (char)('0' + w2.run);
        *p++ = ',';
        p = put_hex(p, w2.cu_adrs, 8);
        *p++ = ',';
        p = put_hex(p, w2.size, 8);
        *p++ = ',';
        p = put_hex(p, w2.loop, 4);
    } else {
        p = put_str(p, "err");
    }
    p = put_str(p, " q7c8=");
    if (q7c8_ok) p = put_hex(p, q7c8, 8);
    else p = put_str(p, "err");
    *p++ = '\n';

    long fd = sc3(5 /* open */, (long)"/tmp_bt/mods/rcraw_enable_test.log",
                  01 | 0100 | 02000 /* O_WRONLY | O_CREAT | O_APPEND */, 0644);
    if (fd >= 0) {
        sc3(4 /* write */, fd, (long)buf, (long)(p - buf));
        sc3(6 /* close */, fd, 0, 0);
    }
    return 0;
}

__attribute__((visibility("default")))
int rcraw_on(void) {
    return rcraw_life(1);
}

__attribute__((visibility("default")))
int rcraw_off(void) {
    return rcraw_life(0);
}
