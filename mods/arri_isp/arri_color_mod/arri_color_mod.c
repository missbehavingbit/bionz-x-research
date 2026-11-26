/* ARRI color-matrix lifecycle mod for the RAM-only Mod Loader (SMOD flag bit 1).
 *
 * The loader calls arri_life(1) after ON and arri_life(0) before OFF. The mod swaps the
 * 9 signed Q10 halfwords of the live per-Color-Mode matrix table entry B8 (S-Gamut3.Cine,
 * selector index 23; VA 0x6444b1d4, PA 0x0444b1d4) in LIRO RAM:
 *   ON : only if the entry equals the stock matrix, write the ARRI matrix.
 *   OFF: only if the entry equals the ARRI matrix, write the stock matrix back.
 * Halfword stores touch exactly these 18 bytes. The camera applies the entry on its next
 * matrix recompute (for example a Color Mode round trip). RAM-only.
 * One status line per call goes to /tmp_bt/mods/arri_color.log.
 */
#include <stdint.h>
#include "arri_color_data.h"   /* COLOR_STOCK[9], COLOR_NEW[9] */

#define ENTRY_PA 0x0444b1d4u

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

static int equal(const volatile int16_t *r, const int16_t *w) {
    for (int i = 0; i < 9; i++) if (r[i] != w[i]) return 0;
    return 1;
}

static void status(int on, char code) {
    char line[] = "arri_color on=? result=?\n";
    line[14] = (char)('0' + on);
    line[23] = code;
    long fd = sc3(5, (long)"/tmp_bt/mods/arri_color.log", 01 | 0100 | 02000, 0600);
    if (fd >= 0) { sc3(4, fd, (long)line, sizeof(line) - 1); sc3(6, fd, 0, 0); }
}

__attribute__((section(".text.arri_life"), used))
void arri_life(int on) {
    const int16_t *from = on ? COLOR_STOCK : COLOR_NEW;
    const int16_t *to = on ? COLOR_NEW : COLOR_STOCK;
    long fd = sc3(5, (long)"/dev/mem", 02 | 04010000, 0);
    if (fd < 0) { status(on, 'o'); return; }
    long base = sc6(192, 0, 4096, 3, 1, fd, ENTRY_PA >> 12);
    if (base < 0 && base > -4096) { sc3(6, fd, 0, 0); status(on, 'm'); return; }
    volatile int16_t *e = (volatile int16_t *)(base + (ENTRY_PA & 0xfffu));
    char code;
    if (equal(e, to)) {
        code = 'a';
    } else if (equal(e, from)) {
        for (int i = 0; i < 9; i++) e[i] = to[i];
        __asm__ volatile("dsb" ::: "memory");
        code = equal(e, to) ? 'w' : 'v';
    } else {
        code = 'x';
    }
    sc3(91, base, 4096, 0);
    sc3(6, fd, 0, 0);
    status(on, code);
}
