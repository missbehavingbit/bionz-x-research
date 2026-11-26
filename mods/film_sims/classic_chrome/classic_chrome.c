/* Classic Chrome: Fujifilm Classic Chrome film simulation (tone curve + color matrix)
 * as an SMOD v2 lifecycle mod for ILCE-6100 and ILCE-6400 Mod Loader v2.
 *
 * Performs four independent, content-guarded single-page swaps in av-cam LIRO RAM:
 *   ec : EC Record 28 variant 1 (Movie/PQD gamma), 96 x uint32_t at EC_GAMMA_PA
 *   fc : FC Table 0 (Still/Live-View gamma),       96 x uint16_t at FC_GAMMA_PA
 *   a0 : CC Matrix entry A0 (index 0),              9 x int16_t  at COLOR_A0_PA
 *   a1 : CC Matrix entry A1 (index 1, Standard),    9 x int16_t  at COLOR_A1_PA
 * ON writes Classic Chrome data where stock (or v1 legacy) data is present; OFF restores
 * stock where Classic Chrome (or v1 legacy) data is present; foreign content is untouched.
 * Log: /tmp_bt/mods/classic_chrome.log, "classic_chrome on=N ec=C fc=C a0=C a1=C"
 * (w written, l written over v1 legacy, a already, x foreign content untouched, v verify failed).
 */
#include <stdint.h>
#include "classic_chrome_data.h"

#ifndef EC_GAMMA_PA
#define EC_GAMMA_PA 0x03e553b0u
#endif
#ifndef FC_GAMMA_PA
#define FC_GAMMA_PA 0x03e8f1d4u
#endif
#ifndef COLOR_A0_PA
#define COLOR_A0_PA 0x0444a254u
#endif
#ifndef COLOR_A1_PA
#define COLOR_A1_PA 0x0444a266u
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

static char swap(uint32_t pa, int sz, int n, const void *from, const void *alt, const void *to) {
    long fd = sc3(5, (long)"/dev/mem", 02 | 04010000 /* O_RDWR | O_SYNC */, 0);
    if (fd < 0) return 'o';
    long base = sc6(192 /* mmap2 */, 0, 4096, 3, 1 /* MAP_SHARED */, fd, (long)(pa >> 12));
    if (base < 0 && base > -4096) { sc3(6, fd, 0, 0); return 'm'; }
    volatile uint8_t *p = (volatile uint8_t *)(base + (pa & 0xfffu));
    int is_to = 1, is_from = 1, is_alt = alt != 0;
    for (int i = 0; i < n; i++) {
        uint32_t v = get(p, sz, i);
        if (v != get(to, sz, i)) is_to = 0;
        if (v != get(from, sz, i)) is_from = 0;
        if (alt && v != get(alt, sz, i)) is_alt = 0;
    }
    char code = 'x';
    if (is_to) {
        code = 'a';
    } else if (is_from || is_alt) {
        for (int i = 0; i < n; i++) {
            uint32_t val = get(to, sz, i);
            if (sz == 4) ((volatile uint32_t *)p)[i] = val;
            else if (sz == 2) ((volatile uint16_t *)p)[i] = (uint16_t)val;
            else ((volatile uint8_t *)p)[i] = (uint8_t)val;
        }
        __asm__ volatile("dsb" ::: "memory");
        code = is_from ? 'w' : 'l';
        for (int i = 0; i < n; i++)
            if (get(p, sz, i) != get(to, sz, i)) code = 'v';
    }
    sc3(91 /* munmap */, base, 4096, 0);
    sc3(6, fd, 0, 0);
    return code;
}

__attribute__((section(".text.arri_life"), used))
void chrome_life(int on) {
    char ec = on ? swap(EC_GAMMA_PA, 4, 96, EC_GAMMA_STOCK, EC_GAMMA_LEGACY, EC_GAMMA_CHROME)
                 : swap(EC_GAMMA_PA, 4, 96, EC_GAMMA_CHROME, EC_GAMMA_LEGACY, EC_GAMMA_STOCK);
    char fc = on ? swap(FC_GAMMA_PA, 2, 96, FC_GAMMA_STOCK, FC_GAMMA_LEGACY, FC_GAMMA_CHROME)
                 : swap(FC_GAMMA_PA, 2, 96, FC_GAMMA_CHROME, FC_GAMMA_LEGACY, FC_GAMMA_STOCK);
    char a0 = on ? swap(COLOR_A0_PA, 2, 9, COLOR_A0_STOCK, COLOR_A0_LEGACY, COLOR_A0_CHROME)
                 : swap(COLOR_A0_PA, 2, 9, COLOR_A0_CHROME, COLOR_A0_LEGACY, COLOR_A0_STOCK);
    char a1 = on ? swap(COLOR_A1_PA, 2, 9, COLOR_A1_STOCK, COLOR_A1_LEGACY, COLOR_A1_CHROME)
                 : swap(COLOR_A1_PA, 2, 9, COLOR_A1_CHROME, COLOR_A1_LEGACY, COLOR_A1_STOCK);
    char line[] = "classic_chrome on=? ec=? fc=? a0=? a1=?\n";
    line[18] = (char)('0' + (on ? 1 : 0));
    line[23] = ec;
    line[28] = fc;
    line[33] = a0;
    line[38] = a1;
    long fd = sc3(5, (long)"/tmp_bt/mods/classic_chrome.log", 01 | 0100 | 02000, 0600);
    if (fd >= 0) { sc3(4, fd, (long)line, sizeof(line) - 1); sc3(6, fd, 0, 0); }
}
