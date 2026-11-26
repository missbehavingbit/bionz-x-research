/* ARRI gamma lifecycle mod for the RAM-only Mod Loader (SMOD flag bit 1).
 *
 * The loader calls arri_life(1) after ON and arri_life(0) before OFF. The mod swaps the
 * 96 curve words of stock EC record 31 (S-Log3; VA 0x63e5363c, PA 0x03e5363c) in LIRO RAM:
 *   ON : only if the record equals the stock words, write the ARRI words.
 *   OFF: only if the record equals the ARRI words, write the stock words back.
 * Anything else is left untouched. RAM-only: a restart reloads stock from /system/av-cam.bin.
 * One status line per call goes to /tmp_bt/mods/arri_gamma.log.
 */
#include <stdint.h>
#include "arri_gamma_data.h"   /* ARRI_STOCK[96], ARRI_NEW[96], ARRI_NAME */

#define REC_PA 0x03e5363cu

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

static int equal(const volatile uint32_t *r, const uint32_t *w) {
    for (int i = 0; i < 96; i++) if (r[i] != w[i]) return 0;
    return 1;
}

static void status(int on, char code) {
    char line[] = "arri_gamma on=? result=?\n";
    line[14] = (char)('0' + on);
    line[23] = code;
    long fd = sc3(5, (long)"/tmp_bt/mods/arri_gamma.log", 01 | 0100 | 02000 /* WRONLY|CREAT|APPEND */, 0600);
    if (fd >= 0) { sc3(4, fd, (long)line, sizeof(line) - 1); sc3(6, fd, 0, 0); }
}

__attribute__((section(".text.arri_life"), used))
void arri_life(int on) {
    const uint32_t *from = on ? ARRI_STOCK : ARRI_NEW;
    const uint32_t *to = on ? ARRI_NEW : ARRI_STOCK;
    long fd = sc3(5, (long)"/dev/mem", 02 | 04010000 /* RDWR|SYNC */, 0);
    if (fd < 0) { status(on, 'o'); return; }
    long base = sc6(192 /* mmap2 */, 0, 4096, 3, 1 /* MAP_SHARED */, fd, REC_PA >> 12);
    if (base < 0 && base > -4096) { sc3(6, fd, 0, 0); status(on, 'm'); return; }
    volatile uint32_t *rec = (volatile uint32_t *)(base + (REC_PA & 0xfffu));
    char code;
    if (equal(rec, to)) {
        code = 'a';                                  /* already in the requested state */
    } else if (equal(rec, from)) {
        for (int i = 0; i < 96; i++) rec[i] = to[i];
        __asm__ volatile("dsb" ::: "memory");
        code = equal(rec, to) ? 'w' : 'v';           /* written / verify failed */
    } else {
        code = 'x';                                  /* unknown content: untouched */
    }
    sc3(91 /* munmap */, base, 4096, 0);
    sc3(6, fd, 0, 0);
    status(on, code);
}
