/* Arrivederci: ARRI Classic look (tone curve + color matrix) as one lifecycle mod for the
 * RAM-only Mod Loader (SMOD flag bit 1; requires the lifecycle loader build).
 *
 * The loader calls arri_life(1) after ON and arri_life(0) before OFF. Two independent,
 * guarded swaps in LIRO RAM through /dev/mem (one page each):
 *   gamma : stock EC record 31 (S-Log3), 96 curve words at PA 0x03e5363c
 *   color : per-Color-Mode matrix entry B8 (S-Gamut3.Cine, index 23), 9 int16 at PA 0x0444b1d4
 * ON writes the ARRI data only where the stock data is present; OFF restores stock only
 * where the ARRI data is present; anything else is left untouched. The matrix applies on
 * the camera's next recompute (for example a Color Mode round trip). RAM-only.
 * Log: /tmp_bt/mods/arrivederci.log, "arrivederci on=N gamma=C color=C"
 * (w written, a already, x foreign content untouched, v verify failed, o/m open/mmap,
 *  l written over the previous Arrivederci matrix COLOR_LEGACY, when the data header has one).
 */
#include <stdint.h>
#include "arrivederci_data.h"   /* GAMMA_*[96], COLOR_STOCK/ARRI[9], optional COLOR_LEGACY[9] */

#ifdef TARGET_ILCE6400A
#define GAMMA_PA     0x03e560c4u
#define COLOR_PA     0x0444fdc8u
#define CC_TUNING_PA 0x0444ed96u
#define REFADR0_PA   0x044b191bu
#else
#define GAMMA_PA     0x03e5363cu
#define COLOR_PA     0x0444b1d4u
#define CC_TUNING_PA 0x0442d8b8u
#define REFADR0_PA   0x04490025u
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

static uint32_t get(const volatile void *p, int sz, int i) {
    if (sz == 4) return ((const volatile uint32_t *)p)[i];
    if (sz == 2) return ((const volatile uint16_t *)p)[i];
    return p ? ((const volatile uint8_t *)p)[i] : 0u;
}

static uint32_t get_ref(const void *p, int sz, int i) {
    return p ? get(p, sz, i) : 0u;
}

/* Guarded swap of n elements of size sz (1, 2, or 4 bytes) at physical address pa.
 * Passing from==0 or to==0 represents an all-zero reference buffer. */
static char swap(uint32_t pa, int sz, int n, const void *from, const void *alt, const void *to) {
    long fd = sc3(5, (long)"/dev/mem", 02 | 04010000 /* O_RDWR | O_SYNC */, 0);
    if (fd < 0) return 'o';
    long base = sc6(192 /* mmap2 */, 0, 4096, 3, 1 /* MAP_SHARED */, fd, (long)(pa >> 12));
    if (base < 0 && base > -4096) { sc3(6, fd, 0, 0); return 'm'; }
    volatile uint8_t *p = (volatile uint8_t *)(base + (pa & 0xfffu));
    int is_to = 1, is_from = 1, is_alt = alt != 0;
    for (int i = 0; i < n; i++) {
        uint32_t v = get(p, sz, i);
        if (v != get_ref(to, sz, i)) is_to = 0;
        if (v != get_ref(from, sz, i)) is_from = 0;
        if (alt && v != get_ref(alt, sz, i)) is_alt = 0;
    }
    char code = 'x';
    if (is_to) {
        code = 'a';
    } else if (is_from || is_alt) {
        for (int i = 0; i < n; i++) {
            uint32_t val = get_ref(to, sz, i);
            if (sz == 4) ((volatile uint32_t *)p)[i] = val;
            else if (sz == 2) ((volatile uint16_t *)p)[i] = (uint16_t)val;
            else ((volatile uint8_t *)p)[i] = (uint8_t)val;
        }
        __asm__ volatile("dsb" ::: "memory");
        code = is_from ? 'w' : 'l';
        for (int i = 0; i < n; i++)
            if (get(p, sz, i) != get_ref(to, sz, i)) code = 'v';
    }
    sc3(91 /* munmap */, base, 4096, 0);
    sc3(6, fd, 0, 0);
    return code;
}

static long sc5(long n, long a, long b, long c, long d, long e) {
    register long r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c,
                  r3 __asm__("r3") = d, r4 __asm__("r4") = e;
    register long r7 __asm__("r7") = n;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r3), "r"(r4), "r"(r7) : "memory");
    return r0;
}

static const char *sabin_src(int i) {
    switch (i) {
        case 0: return "/tmp_bt/mods/sabin/dcv_psf_table_movie_bayer.bin";
        case 1: return "/tmp_bt/mods/sabin/dcv_psf_table_movie_4k.bin";
        case 2:
        case 3: return "/tmp_bt/mods/sabin/spica_c_zero.bin";
        case 4: return "/tmp_bt/mods/sabin/spica_musashi_mov_4k_y_tap_step1.bin";
        default: return "/tmp_bt/mods/sabin/spica_musashi_mov_4k_y_tap_step2.bin";
    }
}

static const char *sabin_dst(int i) {
    switch (i) {
        case 0: return "/usr/sabin/dcv_psf_table_movie_bayer.bin";
        case 1: return "/usr/sabin/dcv_psf_table_movie_4k.bin";
        case 2: return "/usr/sabin/spica_musashi_mov_4k_c_tap_step1.bin";
        case 3: return "/usr/sabin/spica_musashi_mov_4k_c_tap_step2.bin";
        case 4: return "/usr/sabin/spica_musashi_mov_4k_y_tap_step1.bin";
        default: return "/usr/sabin/spica_musashi_mov_4k_y_tap_step2.bin";
    }
}

#ifdef SABIN_EMBEDDED
static int write_all(long fd, const uint8_t *buf, uint32_t len) {
    uint32_t pos = 0;
    while (pos < len) {
        long w = sc3(4 /* write */, fd, (long)(buf + pos), (long)(len - pos));
        if (w <= 0) return -1;
        pos += (uint32_t)w;
    }
    return 0;
}

static int ensure_file(const char *path, const uint8_t *data, uint32_t len) {
    long fd = sc3(5 /* open */, (long)path, 0 /* O_RDONLY */, 0);
    if (fd >= 0) {
        sc3(6 /* close */, fd, 0, 0);
        return 0;
    }
    fd = sc3(5 /* open */, (long)path, 01 | 0100 | 01000 /* O_WRONLY|O_CREAT|O_TRUNC */, 0644);
    if (fd < 0) return -1;
    int rc = 0;
    if (data) {
        rc = write_all(fd, data, len);
    } else {
        uint8_t zeros[256];
        for (uint32_t i = 0; i < sizeof(zeros); i++) zeros[i] = 0;
        uint32_t left = len;
        while (left > 0) {
            uint32_t chunk = left > sizeof(zeros) ? sizeof(zeros) : left;
            if (write_all(fd, zeros, chunk) < 0) { rc = -1; break; }
            left -= chunk;
        }
    }
    sc3(6 /* close */, fd, 0, 0);
    return rc;
}
#endif

static char sync_sabin(int on) {
#ifdef SABIN_EMBEDDED
    if (on) {
        sc3(39 /* mkdir */, (long)"/tmp_bt/mods/sabin", 0755, 0);
        if (ensure_file(sabin_src(0), SABIN_DCV_BAYER, sizeof(SABIN_DCV_BAYER)) < 0 ||
            ensure_file(sabin_src(1), SABIN_DCV_4K, sizeof(SABIN_DCV_4K)) < 0 ||
            ensure_file(sabin_src(2), 0, 40000u) < 0 ||
            ensure_file(sabin_src(4), SABIN_Y_TAP_1, sizeof(SABIN_Y_TAP_1)) < 0 ||
            ensure_file(sabin_src(5), SABIN_Y_TAP_2, sizeof(SABIN_Y_TAP_2)) < 0) {
            return 'e';
        }
    }
#else
    long probe = sc3(5 /* open */, (long)sabin_src(0), 0 /* O_RDONLY */, 0);
    if (probe < 0) return 'n';
    sc3(6 /* close */, probe, 0, 0);
#endif
    char res = on ? 'm' : 'u';
    for (int i = 0; i < 6; i++) {
        if (on) {
            long r = sc5(21 /* mount */, (long)sabin_src(i), (long)sabin_dst(i), 0, 4096 /* MS_BIND */, 0);
            if (r < 0) res = 'e';
        } else {
            sc3(52 /* umount2 */, (long)sabin_dst(i), 2 /* MNT_DETACH */, 0);
        }
    }
    return res;
}

__attribute__((section(".text.arri_life"), used))
void arri_life(int on) {
#ifdef GAMMA_HAS_LEGACY
    const uint32_t *g_legacy = GAMMA_LEGACY;
#else
    const uint32_t *g_legacy = 0;
#endif
#ifdef COLOR_HAS_LEGACY
    const int16_t *legacy = COLOR_LEGACY;
#else
    const int16_t *legacy = 0;
#endif
    char g = on ? swap(GAMMA_PA, 4, 96, GAMMA_STOCK, g_legacy, GAMMA_ARRI)
                : swap(GAMMA_PA, 4, 96, GAMMA_ARRI, g_legacy, GAMMA_STOCK);
    char c = on ? swap(COLOR_PA, 2, 9, COLOR_STOCK, legacy, COLOR_ARRI)
                : swap(COLOR_PA, 2, 9, COLOR_ARRI, legacy, COLOR_STOCK);
    char s = sync_sabin(on);
#ifdef REFADR_EMBEDDED
    static const uint16_t cc_bypass = 0xffffu;
    char l3 = on ? swap(REFADR0_PA, 1, sizeof(REFADR_ARRI), 0, 0, REFADR_ARRI)
                 : swap(REFADR0_PA, 1, sizeof(REFADR_ARRI), REFADR_ARRI, 0, 0);
    char lmode = on ? swap(CC_TUNING_PA, 2, 1, &cc_bypass, 0, 0)
                    : swap(CC_TUNING_PA, 2, 1, 0, 0, &cc_bypass);
    if (lmode != 'w' && lmode != 'a') l3 = lmode;
#else
    char l3 = 'n';
#endif
    char line[] = "arrivederci on=? gamma=? color=? sabin=? lut3d=?\n";
    line[15] = (char)('0' + on);
    line[23] = g;
    line[31] = c;
    line[39] = s;
    line[47] = l3;
    long fd = sc3(5, (long)"/tmp_bt/mods/arrivederci.log", 01 | 0100 | 02000, 0600);
    if (fd >= 0) { sc3(4, fd, (long)line, sizeof(line) - 1); sc3(6, fd, 0, 0); }
}
