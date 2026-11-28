/* hostwin_get DST: QEMU guest helper for the sony-a6400 "hostwin" developer option.
 *
 * The host writes { u32 magic 'HWIN', u32 seq, u32 len, u32 fnv1a } at PA 0x50000000 and the file
 * data at PA 0x50001000 (a host file mapped as shared RAM by QEMU). This helper maps the window
 * read-only through /dev/mem, checks length and FNV-1a, and writes the data to DST. It runs only
 * in the disposable QEMU guest (the window does not exist on the camera).
 *
 * Freestanding (raw syscalls, no libc):
 *   arm-linux-gnueabi-gcc -marm -march=armv5te -Os -static -nostdlib -ffreestanding \
 *       -fno-builtin -o hostwin_get hostwin_get.c
 */
#include <stdint.h>

#define WIN_PA     0x50000000u
#define WIN_SIZE   0x01000000u
#define DATA_OFF   0x1000u
#define MAGIC      0x4E495748u        /* "HWIN" little-endian */

static long sys3(long n, long a, long b, long c) {
    register long r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c;
    register long r7 __asm__("r7") = n;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return r0;
}
static long sys6(long n, long a, long b, long c, long d, long e, long f) {
    register long r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c,
                  r3 __asm__("r3") = d, r4 __asm__("r4") = e, r5 __asm__("r5") = f;
    register long r7 __asm__("r7") = n;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r3), "r"(r4), "r"(r5), "r"(r7) : "memory");
    return r0;
}
static void out(const char *s) { long n = 0; while (s[n]) n++; sys3(4, 1, (long)s, n); }
static void out_hex(uint32_t v) {
    char b[9];
    for (int i = 0; i < 8; i++) { uint32_t x = (v >> (28 - 4 * i)) & 15u; b[i] = (char)(x < 10 ? '0' + x : 'a' + x - 10); }
    b[8] = 0;
    out(b);
}
__attribute__((noreturn)) static void quit(int code) { sys3(1, code, 0, 0); for (;;) {} }

static int run(const char *dst) {
    long fd = sys3(5, (long)"/dev/mem", 00 | 04010000 /* O_RDONLY | O_SYNC */, 0);
    if (fd < 0) { out("HWIN ERR open /dev/mem\n"); return 2; }
    long base = sys6(192 /* mmap2 */, 0, WIN_SIZE, 1 /* PROT_READ */, 1 /* MAP_SHARED */, fd, WIN_PA >> 12);
    if (base < 0 && base > -4096) { out("HWIN ERR mmap\n"); return 3; }
    const volatile uint32_t *hdr = (const volatile uint32_t *)base;
    uint32_t magic = hdr[0], len = hdr[2], want = hdr[3];
    if (magic != MAGIC || len > WIN_SIZE - DATA_OFF) { out("HWIN ERR header\n"); return 4; }
    const uint8_t *src = (const uint8_t *)base + DATA_OFF;
    uint32_t h = 0x811C9DC5u;
    for (uint32_t i = 0; i < len; i++) h = (h ^ src[i]) * 0x01000193u;
    if (h != want) { out("HWIN ERR fnv "); out_hex(h); out("\n"); return 5; }
    long o = sys3(5, (long)dst, 01 | 0100 | 01000 /* O_WRONLY | O_CREAT | O_TRUNC */, 0755);
    if (o < 0) { out("HWIN ERR open dst\n"); return 6; }
    for (uint32_t done = 0; done < len;) {
        long w = sys3(4, o, (long)(src + done), len - done > 65536u ? 65536 : (long)(len - done));
        if (w <= 0) { out("HWIN ERR write\n"); return 7; }
        done += (uint32_t)w;
    }
    sys3(6, o, 0, 0);
    out("HWIN OK len="); out_hex(len); out(" fnv="); out_hex(h); out("\n");
    return 0;
}

__attribute__((naked, noreturn)) void _start(void) {
    __asm__ volatile(
        "ldr r0, [sp]\n\t"        /* argc */
        "add r1, sp, #4\n\t"      /* argv */
        "bl c_start\n\t");
}

__attribute__((noreturn, used)) void c_start(int argc, char **argv) {
    if (argc != 2) { out("usage: hostwin_get DST\n"); quit(1); }
    quit(run(argv[1]));
}
