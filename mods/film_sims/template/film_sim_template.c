/* Single SMOD v2 Film Simulation lifecycle binary template for ILCE-6100 and ILCE-6400.
 *
 * This is the same content-guarded, single-page `/dev/mem` swap as film_sim.c, but every target
 * physical address, tag and table lives in one `.rodata.sim_payload` object instead of `#define`
 * constants and `static const` arrays. The build script (`build_template_wsl.sh`) emits the
 * position-independent `.text` blob plus the payload offset and the `sim_life` offset, and
 * `isp-studio/crates/bionz-preset/src/smod_v2.rs` clones that blob, writes the addresses and tables
 * of the selected preset into the payload, and emits a ready-to-copy `.mod`.
 *
 * One binary template covers both bodies because no physical address is baked into the code.
 *
 * Performs five independent, content-guarded single-page swaps in av-cam LIRO RAM:
 *   ec : EC Record 28 variant 1 (Movie/PQD gamma), 96 x uint32_t at ec_gamma_pa
 *   fc : FC Table 0 (Still/Live-View gamma),       96 x uint16_t at fc_gamma_pa
 *   a0 : CC Matrix entry A0 (index 0),              9 x int16_t  at color_a0_pa
 *   a1 : CC Matrix entry A1 (index 1, Standard),    9 x int16_t  at color_a1_pa
 *   b2 : CC Matrix entry B2 (index 17),             9 x int16_t  at color_b2_pa
 * plus the RefAdr[0] 3D LUT and the CC tuning bypass word.
 *
 * The payload is fully zero-filled in the template: an unpacked `.mod` must never be run, because
 * addresses 0 and an empty tag are not a valid target set. `smod_v2.rs` refuses to emit a template
 * whose payload was not overwritten.
 */
#include <stdint.h>
#include "film_sim_payload.h"

/* All fields are written by the host packer; only the magic is meaningful here. */
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wmissing-field-initializers"
__attribute__((section(".rodata.sim_payload"), used, aligned(4)))
static const struct sim_payload sim_payload = { SIM_PAYLOAD_MAGIC };
#pragma GCC diagnostic pop

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

/* `from`/`to` are volatile so a `const` payload is always read at run time: without the qualifier
 * GCC folds the payload's zero-initialized scalars into the object file and every physical address
 * becomes 0. */
static char swap(uint32_t pa, int sz, int n, const volatile void *from, const volatile void *to) {
    long fd = sc3(5, (long)"/dev/mem", 02 | 04010000 /* O_RDWR | O_SYNC */, 0);
    if (fd < 0) return 'o';
    long base = sc6(192 /* mmap2 */, 0, 4096, 3, 1 /* MAP_SHARED */, fd, (long)(pa >> 12));
    if (base < 0 && base > -4096) { sc3(6, fd, 0, 0); return 'm'; }
    volatile uint8_t *p = (volatile uint8_t *)(base + (pa & 0xfffu));
    int is_to = 1, is_from = 1;
    for (int i = 0; i < n; i++) {
        uint32_t v = get(p, sz, i);
        if (v != get(to, sz, i)) is_to = 0;
        if (v != get(from, sz, i)) is_from = 0;
    }
    char code = 'x';
    if (is_to) {
        code = 'a';
    } else if (is_from) {
        for (int i = 0; i < n; i++) {
            uint32_t val = get(to, sz, i);
            if (sz == 4) ((volatile uint32_t *)p)[i] = val;
            else if (sz == 2) ((volatile uint16_t *)p)[i] = (uint16_t)val;
            else ((volatile uint8_t *)p)[i] = (uint8_t)val;
        }
        __asm__ volatile("dsb" ::: "memory");
        code = 'w';
        for (int i = 0; i < n; i++)
            if (get(p, sz, i) != get(to, sz, i)) code = 'v';
    }
    sc3(91 /* munmap */, base, 4096, 0);
    sc3(6, fd, 0, 0);
    return code;
}

/* Single-page-safe writer for RefAdr[0] (splits across a 4 KiB page boundary). */
static char write_refadr_page(uint32_t pa, const volatile uint8_t *data, int n) {
    long fd = sc3(5, (long)"/dev/mem", 02 | 04010000, 0);
    if (fd < 0) return 'o';
    long base = sc6(192, 0, 4096, 3, 1, fd, (long)(pa >> 12));
    if (base < 0 && base > -4096) { sc3(6, fd, 0, 0); return 'm'; }
    volatile uint8_t *p = (volatile uint8_t *)(base + (pa & 0xfffu));
    for (int i = 0; i < n; i++) p[i] = data ? data[i] : 0u;
    __asm__ volatile("dsb" ::: "memory");
    sc3(91, base, 4096, 0);
    sc3(6, fd, 0, 0);
    return 'w';
}

static char write_refadr(uint32_t pa, const volatile uint8_t *data, int n) {
    uint32_t off = pa & 0xfffu;
    int first = (int)(4096u - off);
    if (first >= n) return write_refadr_page(pa, data, n);
    char c1 = write_refadr_page(pa, data, first);
    if (c1 != 'w') return c1;
    return write_refadr_page((pa & ~0xfffu) + 0x1000u, data ? data + first : 0, n - first);
}

static int append_str(char *dst, int pos, const volatile char *s) {
    while (*s) dst[pos++] = *s++;
    return pos;
}

__attribute__((section(".text.arri_life"), used)) void sim_life(int on) {
    /* Volatile: every address and table must be read from the payload the host packer filled in,
       never folded from this template's zero initializer. */
    const volatile struct sim_payload *sp = &sim_payload;
    static const uint16_t cc_bypass = 0xffffu;
    static const uint16_t cc_enable = 0x0000u;
    char ec = on ? swap(sp->ec_gamma_pa, 4, 96, sp->ec_stock, sp->ec_sim)
                 : swap(sp->ec_gamma_pa, 4, 96, sp->ec_sim, sp->ec_stock);
    char fc = on ? swap(sp->fc_gamma_pa, 2, 96, sp->fc_stock, sp->fc_sim)
                 : swap(sp->fc_gamma_pa, 2, 96, sp->fc_sim, sp->fc_stock);
    char a0 = on ? swap(sp->color_a0_pa, 2, 9, sp->a0_stock, sp->a0_sim)
                 : swap(sp->color_a0_pa, 2, 9, sp->a0_sim, sp->a0_stock);
    char a1 = on ? swap(sp->color_a1_pa, 2, 9, sp->a1_stock, sp->a1_sim)
                 : swap(sp->color_a1_pa, 2, 9, sp->a1_sim, sp->a1_stock);
    char b2 = on ? swap(sp->color_b2_pa, 2, 9, sp->b2_stock, sp->b2_sim)
                 : swap(sp->color_b2_pa, 2, 9, sp->b2_sim, sp->b2_stock);
    char l3 = on ? write_refadr(sp->refadr0_pa, sp->refadr_sim, (int)sizeof(sp->refadr_sim))
                 : write_refadr(sp->refadr0_pa, 0, (int)sizeof(sp->refadr_sim));
    char lmode = on ? swap(sp->cc_tuning_pa, 2, 1, &cc_bypass, &cc_enable)
                    : swap(sp->cc_tuning_pa, 2, 1, &cc_enable, &cc_bypass);
    if (lmode != 'w' && lmode != 'a') l3 = lmode;
    char buf[96];
    int p = 0;
    p = append_str(buf, p, sp->mod_tag);
    p = append_str(buf, p, " on=");
    buf[p++] = (char)('0' + (on ? 1 : 0));
    p = append_str(buf, p, " ec="); buf[p++] = ec;
    p = append_str(buf, p, " fc="); buf[p++] = fc;
    p = append_str(buf, p, " a0="); buf[p++] = a0;
    p = append_str(buf, p, " a1="); buf[p++] = a1;
    p = append_str(buf, p, " b2="); buf[p++] = b2;
    p = append_str(buf, p, " lut3d="); buf[p++] = l3;
    buf[p++] = '\n';
    long fd = sc3(5, (long)sp->mod_log_path, 01 | 0100 | 02000, 0600);
    if (fd >= 0) { sc3(4, fd, (long)buf, p); sc3(6, fd, 0, 0); }
}
