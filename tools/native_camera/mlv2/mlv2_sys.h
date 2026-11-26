/* Raw Linux EABI syscalls and small helpers for the Mod Loader v2 shim and core.
 * Freestanding: no libc, no globals, everything inline or static (position-independent safe). */
#ifndef MLV2_SYS_H
#define MLV2_SYS_H
#include <stdint.h>

#define ML_O_RDONLY       0
#define ML_O_WR_CREAT_APP 0x441
#define ML_O_WR_CREAT_TRUNC 0x241
#define ML_O_DIRECTORY    040000
#define ML_PROT_RWX       7
#define ML_MAP_ANON_PRIV  0x22

static inline int ml_sc3(int n, int a, int b, int c) {
    register int r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c;
    register int r7 __asm__("r7") = n;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return r0;
}

static inline int ml_open(const char *p, int flags) { return ml_sc3(5, (int)p, flags, 0644); }
static inline int ml_read(int fd, void *b, uint32_t n) { return ml_sc3(3, fd, (int)b, (int)n); }
static inline int ml_write(int fd, const void *b, uint32_t n) { return ml_sc3(4, fd, (int)b, (int)n); }
static inline int ml_close(int fd) { return ml_sc3(6, fd, 0, 0); }
static inline int ml_unlink(const char *p) { return ml_sc3(10, (int)p, 0, 0); }
static inline int ml_lseek(int fd, uint32_t off) { return ml_sc3(19, fd, (int)off, 0); }
static inline int ml_getdents64(int fd, void *b, uint32_t n) { return ml_sc3(217, fd, (int)b, (int)n); }
static inline int ml_mprotect(uint32_t a, uint32_t n, int prot) { return ml_sc3(125, (int)a, (int)n, prot); }

static inline uint32_t ml_mmap_rwx(uint32_t len) {
    register int r0 __asm__("r0") = 0;
    register int r1 __asm__("r1") = (int)((len + 0xfffu) & ~0xfffu);
    register int r2 __asm__("r2") = ML_PROT_RWX;
    register int r3 __asm__("r3") = ML_MAP_ANON_PRIV;
    register int r4 __asm__("r4") = -1;
    register int r5 __asm__("r5") = 0;
    register int r7 __asm__("r7") = 192; /* mmap2 */
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r3), "r"(r4), "r"(r5), "r"(r7) : "memory");
    return (uint32_t)r0 >= 0xfffff000u ? 0u : (uint32_t)r0;
}

static inline void ml_cacheflush(uint32_t beg, uint32_t end) {
    register uint32_t r0 __asm__("r0") = beg, r1 __asm__("r1") = end, r2 __asm__("r2") = 0;
    register uint32_t r7 __asm__("r7") = 0x0f0002u;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
}

static inline int ml_affinity(int nr, uint32_t *mask) { /* 241 set, 242 get; calling thread */
    return ml_sc3(nr, 0, 4, (int)mask);
}

/* cacheflush maintains only the calling CPU on this kernel (4x Cortex-A5, 3.0.27); flush on each. */
static inline void ml_flush_all(uint32_t beg, uint32_t end) {
    uint32_t old = 0, mask;
    ml_affinity(242, &old);
    for (uint32_t cpu = 0; cpu < 4u; cpu++) {
        mask = 1u << cpu;
        if (ml_affinity(241, &mask) == 0) ml_cacheflush(beg, end);
    }
    if (old) ml_affinity(241, &old);
}

static inline int ml_make_rwx(uint32_t addr, uint32_t len) {
    uint32_t beg = addr & ~0xfffu, end = (addr + len + 0xfffu) & ~0xfffu;
    return ml_mprotect(beg, end - beg, ML_PROT_RWX);
}

static inline uint32_t ml_fnv1a_more(uint32_t h, const uint8_t *d, uint32_t n) {
    while (n--) { h ^= *d++; h *= 0x01000193u; }
    return h;
}
static inline uint32_t ml_fnv1a(const uint8_t *d, uint32_t n) { return ml_fnv1a_more(0x811c9dc5u, d, n); }

static inline void ml_memcpy(void *dst, const void *src, uint32_t n) {
    uint8_t *d = (uint8_t *)dst; const uint8_t *s = (const uint8_t *)src;
    while (n--) *d++ = *s++;
}
static inline void ml_memset(void *dst, uint8_t v, uint32_t n) {
    uint8_t *d = (uint8_t *)dst;
    while (n--) *d++ = v;
}
static inline int ml_memcmp(const void *a, const void *b, uint32_t n) {
    const uint8_t *pa = (const uint8_t *)a, *pb = (const uint8_t *)b;
    while (n--) { if (*pa != *pb) return (int)*pa - (int)*pb; pa++; pb++; }
    return 0;
}
static inline int ml_strcmp(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return (int)(uint8_t)*a - (int)(uint8_t)*b;
}

/* Write one "msg 0123abcd" line to path (flags: append or truncate). */
static inline void ml_log_to(const char *path, int flags, const char *msg, uint32_t val) {
    int fd = ml_open(path, flags);
    if (fd < 0) return;
    char buf[64];
    uint32_t i = 0;
    while (*msg && i + 12 < sizeof(buf)) buf[i++] = *msg++;
    buf[i++] = ' ';
    for (int s = 28; s >= 0; s -= 4) {
        uint32_t d = (val >> s) & 0xfu;
        buf[i++] = (char)(d < 10 ? ('0' + d) : ('a' + d - 10));
    }
    buf[i++] = '\n';
    ml_write(fd, buf, i);
    ml_close(fd);
}

static inline void ml_log(const char *msg, uint32_t val) {
    ml_log_to("/tmp_bt/mods/mod_loader.log", ML_O_WR_CREAT_APP, msg, val);
}

#endif
