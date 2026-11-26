/* Guarded single-buffer writer for the live Movie gamma buffer (ILCE-6400, retained
 * av-cam image dd8f8d0c...). Freestanding: raw Linux EABI syscalls, no libc.
 *
 * Usage: gamma_poke PA_HEX EXPECTED.bin NEW.bin
 *   Both files must have the same size, a multiple of 4 bytes, at most 420 (v4: was fixed
 *   at 420). PA must be 4-byte aligned and the range must lie in one 4 KiB page, inside the
 *   LIRO av-cam alias window 0x035c6000..0x045cc4e8. Maps exactly that page of
 *   /dev/mem (O_SYNC, MAP_SHARED), compares the range with EXPECTED and writes NEW
 *   only on an exact match. Reports readbacks through the mapping (immediately, +20 ms, +200 ms)
 *   and through pread64 on /dev/mem.
 * Exit: 0 written and visible on both paths; 2 usage/file; 3 guard mismatch (no write);
 *       4 open/mmap failure; 5 written but not visible on a read path.
 */
#include <stdint.h>

#define MAXW      105u
static unsigned WORDS, BYTES;

static long sys1(long n, long a) { register long r0 asm("r0") = a; register long r7 asm("r7") = n;
    asm volatile("svc #0" : "+r"(r0) : "r"(r7) : "memory"); return r0; }
static long sys3(long n, long a, long b, long c) { register long r0 asm("r0") = a, r1 asm("r1") = b,
    r2 asm("r2") = c; register long r7 asm("r7") = n;
    asm volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory"); return r0; }
static long sys6(long n, long a, long b, long c, long d, long e, long f) { register long r0 asm("r0") = a,
    r1 asm("r1") = b, r2 asm("r2") = c, r3 asm("r3") = d, r4 asm("r4") = e, r5 asm("r5") = f;
    register long r7 asm("r7") = n;
    asm volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r3), "r"(r4), "r"(r5), "r"(r7) : "memory");
    return r0; }

static void say(const char *s) { long n = 0; while (s[n]) n++; sys3(4, 1, (long)s, n); }
static void quit(int code) { sys1(1, code); for (;;) {} }
static void nap_ms(long ms) { long ts[2] = { 0, ms * 1000000L }; sys3(162, (long)ts, 0, 0); }

static long load(const char *path, uint32_t *out) {
    long fd = sys3(5, (long)path, 0 /* O_RDONLY */, 0);
    if (fd < 0) return -1;
    long got = sys3(3, fd, (long)out, MAXW * 4u);
    uint8_t extra;
    long more = sys3(3, fd, (long)&extra, 1);
    sys1(6, fd);
    return (got > 0 && (got & 3) == 0 && more == 0) ? got : -1;
}

static int same(const volatile uint32_t *a, const uint32_t *b) {
    for (unsigned i = 0; i < WORDS; i++) if (a[i] != b[i]) return 0;
    return 1;
}

static const char *state(const volatile uint32_t *p, const uint32_t *old, const uint32_t *neu) {
    return same(p, neu) ? "new" : same(p, old) ? "expected" : "other";
}

static int hex(const char *s, uint32_t *out) {
    uint32_t v = 0;
    int n = 0;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) s += 2;
    for (; *s; s++, n++) {
        int d = (*s >= '0' && *s <= '9') ? *s - '0' : (*s >= 'a' && *s <= 'f') ? *s - 'a' + 10 :
                (*s >= 'A' && *s <= 'F') ? *s - 'A' + 10 : -1;
        if (d < 0 || n >= 8) return -1;
        v = (v << 4) | (uint32_t)d;
    }
    *out = v;
    return n ? 0 : -1;
}

int main(int argc, char **argv) {
    static uint32_t expect[MAXW], neu[MAXW], rb[MAXW];
    uint32_t pa;
    long n1, n2;
    if (argc != 4 || hex(argv[1], &pa) || (n1 = load(argv[2], expect)) < 0 ||
        (n2 = load(argv[3], neu)) != n1) {
        say("ERR usage or input file\n"); quit(2);
    }
    BYTES = (unsigned)n1;
    WORDS = BYTES / 4u;
    if ((pa & 3) || (pa >> 12) != ((pa + BYTES - 1) >> 12) || pa < 0x035c6000u || pa + BYTES > 0x045cc4e8u) {
        say("ERR address outside policy\n"); quit(2);
    }
    const uint32_t PAGE_PA = pa & ~0xfffu, BUF_OFF = pa & 0xfffu;
    long fd = sys3(5, (long)"/dev/mem", 2 /* O_RDWR */ | 0x101000 /* O_SYNC */, 0);
    if (fd < 0) { say("ERR open /dev/mem\n"); quit(4); }
    long base = sys6(192 /* mmap2 */, 0, 4096, 3 /* RW */, 1 /* MAP_SHARED */, fd, PAGE_PA >> 12);
    if (base < 0 && base > -4096) { say("ERR mmap2\n"); quit(4); }
    volatile uint32_t *buf = (volatile uint32_t *)(base + BUF_OFF);
    if (!same(buf, expect)) { say("GUARD mismatch: no write\n"); quit(3); }
    for (unsigned i = 0; i < WORDS; i++) buf[i] = neu[i];
    asm volatile("dsb" ::: "memory");
    say("WROTE\nmap+0ms "); say(state(buf, expect, neu));
    nap_ms(20);  say("\nmap+20ms "); say(state(buf, expect, neu));
    nap_ms(180); say("\nmap+200ms "); say(state(buf, expect, neu));
    long got = sys6(180 /* pread64 */, fd, (long)rb, BYTES, 0, PAGE_PA + BUF_OFF, 0);
    say("\npread "); say(got == (long)BYTES ? state(rb, expect, neu) : "short");
    say("\n");
    int ok = same(buf, neu) && got == (long)BYTES && same(rb, neu);
    sys3(91 /* munmap */, base, 4096, 0);
    sys1(6, fd);
    quit(ok ? 0 : 5);
    return 0;
}
