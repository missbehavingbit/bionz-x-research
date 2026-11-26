/*
 * ModsTabLook (SMOD, E0522): serve raw RGBA4444 96x66 images for the inactive, active and focused
 * Mods tab. Resource indices 0x12, 0x19 and 0x1a are unused in the DSLR image container block 0x66.
 * Hook: alcor index lookup libObj+0x79d4b6 (live 0x064b54b6). It returns the record offset from
 * the table base. Other lookups run the original code. Code lives in /usr/lib/libptp.so RX padding.
 *
 * Record layout (E0522): {u32 id, u32 format, u16 w, u16 h, u32 size, u32 offset}; data = record + 4 + offset.
 * Format 0x80000000 = raw 16 bpp, RGBA4444 little-endian (R bits 15-12, A bits 3-0).
 */
#include <stdint.h>
#include "mods_tab_look_layout.h"

#define MTL_BLOCK      0x66u
#define MTL_INACTIVE   0x12u
#define MTL_ACTIVE     0x19u
#define MTL_FOCUSED    0x1au
#define MTL_W          96u  /* 192-byte rows; the icon uses the first 91 columns. */
#define MTL_H          66u
#define MTL_PIXELS     (MTL_W * MTL_H)
#define MTL_BUF_BYTES  16384u
#define MTL_PIX_OFF    32u

_Static_assert(MTL_PIX_OFF + MTL_PIXELS * 2u <= MTL_BUF_BYTES, "icon exceeds mapping");

/* u16 npal, u16 nruns, u16 pal[MTL_STATES][npal], {u8 count-1, u8 idx}[nruns]. */
extern const uint8_t mtl_icon_rle[] __attribute__((visibility("hidden")));

#ifndef MTL_BUILTIN
/* The legacy standalone mod stores its cache in its writable code mapping. */
__attribute__((section(".text.mtl_state"))) volatile uint32_t mtl_buf[MTL_STATES] = {0};
#endif

static inline uint32_t mtl_mmap(uint32_t len) {
    register int r0 __asm__("r0") = 0;
    register int r1 __asm__("r1") = (int)len;
    register int r2 __asm__("r2") = 3;      /* PROT_READ | PROT_WRITE */
    register int r3 __asm__("r3") = 0x22;   /* MAP_PRIVATE | MAP_ANONYMOUS */
    register int r4 __asm__("r4") = -1;
    register int r5 __asm__("r5") = 0;
    register int r7 __asm__("r7") = 192;    /* mmap2 */
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r3), "r"(r4), "r"(r5), "r"(r7) : "memory");
    return (uint32_t)r0;
}

static uint32_t mtl_build(uint32_t k, uint32_t state) {
    uint32_t buf = mtl_mmap(MTL_BUF_BYTES);
    if (buf >= 0xfffff000u) return 0;
    const uint8_t *p = mtl_icon_rle;
    uint32_t npal = p[0] | (p[1] << 8), nruns = p[2] | (p[3] << 8);
    const uint8_t *pal = p + 4 + 2u * npal * state;
    const uint8_t *runs = p + 4 + 2u * npal * MTL_STATES;
    volatile uint16_t *px = (volatile uint16_t *)(buf + MTL_PIX_OFF);
    uint32_t n = 0;
    for (uint32_t r = 0; r < nruns; r++) {
        uint32_t cnt = runs[2u * r] + 1u, idx = runs[2u * r + 1u];
        if (idx >= npal) break;
        uint16_t c = (uint16_t)(pal[2u * idx] | (pal[2u * idx + 1u] << 8));
        while (cnt-- && n < MTL_PIXELS) px[n++] = c;
    }
    volatile uint32_t *rec = (volatile uint32_t *)buf;
    rec[0] = 0x1a000000u | (MTL_BLOCK << 13) | k;
    rec[1] = 0x80000000u;
    rec[2] = MTL_W | (MTL_H << 16);
    rec[3] = MTL_PIXELS * 2u;
    rec[4] = MTL_PIX_OFF - 4u;  /* data = record + 4 + offset (libObj 0x5c21a4 / 0x7a5424) */
    return buf;
}

/* Returns 0 when the call is not ours, otherwise the value the original function must return. */
uint32_t mtl_before(uint32_t db, uint32_t k) {
    uint32_t state;
    if (k == MTL_INACTIVE) state = 0;
    else if (k == MTL_ACTIVE || k == MTL_FOCUSED) state = k - MTL_ACTIVE + 1u;
    else return 0;
    if (*(volatile uint16_t *)(db + 2u) != MTL_BLOCK) return 0;
#ifdef MTL_BUILTIN
    const volatile uint32_t *anchor = (const volatile uint32_t *)ML_ANCHOR_ADDR;
    uint32_t loader_state = anchor[1];
    if (anchor[0] != ML_ANCHOR_MAGIC || !loader_state) return 0;
    volatile uint32_t *cache = (volatile uint32_t *)(loader_state + MTL_CACHE_OFF);
#else
    volatile uint32_t *cache = mtl_buf;
#endif
    uint32_t buf = cache[state];
    if (!buf) {
        buf = mtl_build(k, state);
        if (!buf) return 0;
        cache[state] = buf;
    }
    uint32_t table = db + (*(volatile uint8_t *)db & 0x7cu) + ((*(volatile uint32_t *)(db + 4u) >> 11) & 0x7ffcu);
    return buf - table;  /* 0x79d534 turns this into record = table + value = buf */
}
