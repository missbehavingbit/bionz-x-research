/* FilmSimLive: Live-View Film Simulation preset cycler (SMOD v2 + ACTION)
 * for ILCE-6100 (Ver.1.00) and ILCE-6400 (Ver.2.00 / Ver.1.00).
 *
 * Activated from MENU -> Mods -> FilmSim Live:
 * - Closes MENU via core.c post_menu_key() back to Live View.
 * - While armed, Left/Up and Right/Down cycle Stock + all 6 Film Sims live
 *   in ISP RAM (EC, FC, A0, A1, B2, RefAdr[0] 3D LUT + CC_TUNING_PA).
 * - Draws a compact translucent pill banner on OSD_A & OSD_B.
 * - Center (ENTER) locks the chosen look and disarms key capture.
 */
#include <stdint.h>
#include "film_sim_live_data.h"

#define LIVE_MAGIC 0x46534c31u /* "FSL1" */

#define KEY_ENTER_PRESS 0x15010052u
#define KEY_ENTER_REL   0x15010051u
#define KEY_UP_PRESS    0x15010062u
#define KEY_UP_REL      0x15010061u
#define KEY_DOWN_PRESS  0x15010072u
#define KEY_DOWN_REL    0x15010071u
#define KEY_RIGHT_PRESS 0x15010082u
#define KEY_RIGHT_REL   0x15010081u
#define KEY_LEFT_PRESS  0x15010092u
#define KEY_LEFT_REL    0x15010091u
#define KEY_MENU_EVENT  0x15010102u

#define OSD_W 640u
#define OSD_H 480u
#define BANNER_X 120u
#define BANNER_Y 42u
#define BANNER_W 400u
#define BANNER_H 26u

struct live_state {
    uint32_t magic;
    uint32_t armed;
    uint32_t skip_first_menu;
    uint32_t swallow_enter_rel;
    uint32_t idx;
    uint32_t osd_a;
    uint32_t osd_b;
};

extern uint8_t __smod_bss[] __attribute__((visibility("hidden")));
#define LIVE_STATE ((volatile struct live_state *)__smod_bss)

extern uint32_t live_original_event_push(void *mgr, void *ev, uint32_t notify)
    __attribute__((visibility("hidden")));

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

static void log_line(const char *tag, uint32_t v) {
    char buf[48];
    int n = 0;
    while (tag[n] && n < 24) { buf[n] = tag[n]; n++; }
    buf[n++] = ' ';
    for (int s = 28; s >= 0; s -= 4) {
        unsigned d = (v >> s) & 0xfu;
        buf[n++] = (char)(d < 10u ? '0' + d : 'a' + d - 10u);
    }
    buf[n++] = '\n';
    int fd = (int)sc3(5, (long)"/tmp_bt/mods/film_sim_live.log", 0x441, 0644);
    if (fd >= 0) {
        sc3(4, fd, (long)buf, n);
        sc3(6, fd, 0, 0);
    }
}

static uint32_t parse_hex(const char *p, int *len) {
    uint32_t v = 0;
    int i = 0;
    for (;;) {
        char c = p[i];
        uint32_t d;
        if (c >= '0' && c <= '9') d = (uint32_t)(c - '0');
        else if (c >= 'a' && c <= 'f') d = (uint32_t)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') d = (uint32_t)(c - 'A' + 10);
        else break;
        v = (v << 4) | d;
        i++;
    }
    *len = i;
    return v;
}

static void find_osd_maps(volatile struct live_state *s) {
    int fd = (int)sc3(5, (long)"/proc/self/maps", 0, 0);
    if (fd < 0) return;
    char buf[2048];
    int fill = 0;
    s->osd_a = 0u;
    s->osd_b = 0u;
    for (;;) {
        int r = (int)sc3(3, fd, (long)(buf + fill), (long)(sizeof(buf) - 1u - (unsigned)fill));
        if (r <= 0) break;
        fill += r;
        buf[fill] = '\0';
        int pos = 0;
        for (int i = 0; i < fill; i++) {
            if (buf[i] != '\n') continue;
            buf[i] = '\0';
            const char *line = buf + pos;
            int n1 = 0;
            uint32_t lo = parse_hex(line, &n1);
            if (n1 > 0 && line[n1] == '-') {
                for (int k = n1; line[k]; k++) {
                    if (line[k] == '2' && line[k+1] == '3' && line[k+2] == 'a' &&
                        line[k+3] == '8' && line[k+4] == '0' && line[k+5] == '0' &&
                        line[k+6] == '0' && line[k+7] == '0') s->osd_a = lo;
                    if (line[k] == '2' && line[k+1] == '3' && line[k+2] == 'b' &&
                        line[k+3] == '1' && line[k+4] == '6' && line[k+5] == '0' &&
                        line[k+6] == '0' && line[k+7] == '0') s->osd_b = lo;
                }
            }
            pos = i + 1;
        }
        if (pos == 0 && fill == (int)sizeof(buf) - 1) fill = 0;
        else {
            int rem = fill - pos;
            for (int i = 0; i < rem; i++) buf[i] = buf[pos + i];
            fill = rem;
        }
    }
    sc3(6, fd, 0, 0);
}

/* ---------------- Single-page /dev/mem ISP writers ---------------- */

static void write_table(int fd, uint32_t pa, int n, int sz, const void *src) {
    uint32_t base = pa & ~0xfffu, off = pa & 0xfffu;
    if (off + (uint32_t)(n * sz) > 4096u) return;
    long m = sc6(192, 0, 4096, 3, 1, fd, (long)(base >> 12));
    if (m < 0 && m > -4096) return;
    volatile uint8_t *dst = (volatile uint8_t *)(m + off);
    if (sz == 4) {
        for (int i = 0; i < n; i++) ((volatile uint32_t *)dst)[i] = ((const uint32_t *)src)[i];
    } else {
        for (int i = 0; i < n; i++) ((volatile uint16_t *)dst)[i] = ((const uint16_t *)src)[i];
    }
    __asm__ volatile("dsb" ::: "memory");
    sc3(91, m, 4096, 0);
}

static void write_refadr_bytes(int fd, const uint8_t *lut) {
    uint32_t pa = REFADR0_PA;
    uint32_t base = pa & ~0xfffu;
    uint32_t off = pa & 0xfffu;
    uint32_t first = 4096u - off;
    if (first > 1215u) first = 1215u;
    long m0 = sc6(192, 0, 4096, 3, 1, fd, (long)(base >> 12));
    if (m0 < 0 && m0 > -4096) return;
    for (uint32_t i = 0; i < first; i++) ((volatile uint8_t *)(m0 + off))[i] = lut ? lut[i] : 0u;
    __asm__ volatile("dsb" ::: "memory");
    sc3(91, m0, 4096, 0);
    if (first < 1215u) {
        long m1 = sc6(192, 0, 4096, 3, 1, fd, (long)((base + 4096u) >> 12));
        if (m1 < 0 && m1 > -4096) return;
        for (uint32_t i = first; i < 1215u; i++) ((volatile uint8_t *)m1)[i - first] = lut ? lut[i] : 0u;
        __asm__ volatile("dsb" ::: "memory");
        sc3(91, m1, 4096, 0);
    }
}

static void write_refadr(int fd, int on, const uint8_t *lut) {
    static const uint16_t cc_enable = 0x0000u;
    static const uint16_t cc_bypass = 0xffffu;
    if (on && lut) {
        write_refadr_bytes(fd, lut);
        write_table(fd, CC_TUNING_PA, 1, 2, &cc_enable);
    } else {
        write_table(fd, CC_TUNING_PA, 1, 2, &cc_bypass);
        write_refadr_bytes(fd, 0);
    }
}

static void apply_preset(uint32_t idx) {
    if (idx >= LIVE_N_PRESETS) return;
    int fd = (int)sc3(5, (long)"/dev/mem", 0x101002, 0);
    if (fd < 0) return;
    if (idx == 0u) {
        write_table(fd, EC_GAMMA_PA, 96, 4, EC_GAMMA_STOCK);
        write_table(fd, FC_GAMMA_PA, 96, 2, FC_GAMMA_STOCK);
        write_table(fd, COLOR_A0_PA, 9, 2, COLOR_A0_STOCK);
        write_table(fd, COLOR_A1_PA, 9, 2, COLOR_A1_STOCK);
        write_table(fd, COLOR_B2_PA, 9, 2, COLOR_B2_STOCK);
        write_refadr(fd, 0, 0);
    } else {
        uint32_t p = idx - 1u;
        write_table(fd, EC_GAMMA_PA, 96, 4, LIVE_EC[p]);
        write_table(fd, FC_GAMMA_PA, 96, 2, LIVE_FC[p]);
        write_table(fd, COLOR_A0_PA, 9, 2, LIVE_A0[p]);
        write_table(fd, COLOR_A1_PA, 9, 2, LIVE_A1[p]);
        write_table(fd, COLOR_B2_PA, 9, 2, LIVE_B2[p]);
        int8_t li = LIVE_LUT_IDX[idx];
        write_refadr(fd, li >= 0, li >= 0 ? LIVE_REFADR[li] : 0);
    }
    sc3(6, fd, 0, 0);
}

/* ---------------- Compact 5x7 uppercase/symbol font & OSD banner ---------------- */

static const uint8_t *glyph5x7(char c) {
    if (c >= 'a' && c <= 'z') c = (char)(c - 'a' + 'A');
    /* Each glyph is 5 columns (LSB = top row, bit 6 = bottom row). */
    static const uint8_t g_space[5] = {0x00,0x00,0x00,0x00,0x00};
    static const uint8_t g_amp[5]   = {0x36,0x49,0x55,0x22,0x50};
    static const uint8_t g_lparen[5]= {0x00,0x1c,0x22,0x41,0x00};
    static const uint8_t g_rparen[5]= {0x00,0x41,0x22,0x1c,0x00};
    static const uint8_t g_slash[5] = {0x20,0x10,0x08,0x04,0x02};
    static const uint8_t g_colon[5] = {0x00,0x36,0x36,0x00,0x00};
    static const uint8_t g_lt[5]    = {0x08,0x14,0x22,0x41,0x00};
    static const uint8_t g_gt[5]    = {0x41,0x22,0x14,0x08,0x00};
    static const uint8_t g_digits[10][5] = {
        {0x3e,0x51,0x49,0x45,0x3e}, {0x00,0x42,0x7f,0x40,0x00},
        {0x42,0x61,0x51,0x49,0x46}, {0x21,0x41,0x45,0x4b,0x31},
        {0x18,0x14,0x12,0x7f,0x10}, {0x27,0x45,0x45,0x45,0x39},
        {0x3c,0x4a,0x49,0x49,0x30}, {0x01,0x71,0x09,0x05,0x03},
        {0x36,0x49,0x49,0x49,0x36}, {0x06,0x49,0x49,0x29,0x1e},
    };
    static const uint8_t g_alpha[26][5] = {
        {0x7e,0x11,0x11,0x11,0x7e}, {0x7f,0x49,0x49,0x49,0x36},
        {0x3e,0x41,0x41,0x41,0x22}, {0x7f,0x41,0x41,0x22,0x1c},
        {0x7f,0x49,0x49,0x49,0x41}, {0x7f,0x09,0x09,0x09,0x01},
        {0x3e,0x41,0x49,0x49,0x7a}, {0x7f,0x08,0x08,0x08,0x7f},
        {0x00,0x41,0x7f,0x41,0x00}, {0x20,0x40,0x41,0x3f,0x01},
        {0x7f,0x08,0x14,0x22,0x41}, {0x7f,0x40,0x40,0x40,0x40},
        {0x7f,0x02,0x0c,0x02,0x7f}, {0x7f,0x04,0x08,0x10,0x7f},
        {0x3e,0x41,0x41,0x41,0x3e}, {0x7f,0x09,0x09,0x09,0x06},
        {0x3e,0x41,0x51,0x21,0x5e}, {0x7f,0x09,0x19,0x29,0x46},
        {0x46,0x49,0x49,0x49,0x31}, {0x01,0x01,0x7f,0x01,0x01},
        {0x3f,0x40,0x40,0x40,0x3f}, {0x1f,0x20,0x40,0x20,0x1f},
        {0x3f,0x40,0x38,0x40,0x3f}, {0x63,0x14,0x08,0x14,0x63},
        {0x07,0x08,0x70,0x08,0x07}, {0x61,0x51,0x49,0x45,0x43},
    };
    if (c >= 'A' && c <= 'Z') return g_alpha[c - 'A'];
    if (c >= '0' && c <= '9') return g_digits[c - '0'];
    if (c == '&') return g_amp;
    if (c == '(') return g_lparen;
    if (c == ')') return g_rparen;
    if (c == '/') return g_slash;
    if (c == ':') return g_colon;
    if (c == '<') return g_lt;
    if (c == '>') return g_gt;
    return g_space;
}

static void draw_char_2x(volatile uint16_t *fb, uint32_t x, uint32_t y, char c, uint16_t col) {
    const uint8_t *g = glyph5x7(c);
    for (uint32_t cx = 0; cx < 5u; cx++) {
        uint8_t bits = g[cx];
        for (uint32_t ry = 0; ry < 7u; ry++) {
            if (!((bits >> ry) & 1u)) continue;
            uint32_t px = x + cx * 2u;
            uint32_t py = y + ry * 2u;
            fb[py * OSD_W + px] = col;
            fb[py * OSD_W + px + 1u] = col;
            fb[(py + 1u) * OSD_W + px] = col;
            fb[(py + 1u) * OSD_W + px + 1u] = col;
        }
    }
}

static void render_banner_to(uint32_t addr, const char *text, int clear) {
    if (!addr) return;
    volatile uint16_t *fb = (volatile uint16_t *)addr;
    for (uint32_t y = BANNER_Y; y < BANNER_Y + BANNER_H; y++) {
        for (uint32_t x = BANNER_X; x < BANNER_X + BANNER_W; x++) {
            uint16_t c = 0x0000u;
            if (!clear) {
                int border = (y == BANNER_Y || y == BANNER_Y + BANNER_H - 1u ||
                              x == BANNER_X || x == BANNER_X + BANNER_W - 1u);
                c = border ? 0xfc2fu : 0x112bu;
            }
            fb[y * OSD_W + x] = c;
        }
    }
    if (clear) return;
    uint32_t len = 0;
    while (text[len]) len++;
    uint32_t tw = len * 12u;
    uint32_t sx = BANNER_X + (BANNER_W > tw ? (BANNER_W - tw) / 2u : 4u);
    uint32_t sy = BANNER_Y + 6u;
    for (uint32_t i = 0; i < len && sx + 10u < BANNER_X + BANNER_W; i++, sx += 12u) {
        draw_char_2x(fb, sx, sy, text[i], (text[i] == '<' || text[i] == '>') ? 0xfc2fu : 0xffffu);
    }
}

static int append_uint(char *buf, int n, uint32_t v) {
    char tmp[10];
    int k = 0;
    do {
        uint32_t q = 0;
        while (v >= 10u) { v -= 10u; q++; }
        tmp[k++] = (char)('0' + (int)v);
        v = q;
    } while (v && k < 10);
    while (k > 0) buf[n++] = tmp[--k];
    return n;
}

static void draw_banner(volatile struct live_state *s) {
    char msg[36];
    int n = 0;
    msg[n++] = '<'; msg[n++] = ' ';
    const char *name = LIVE_NAMES[s->idx];
    for (int i = 0; name[i] && n < 22; i++) msg[n++] = name[i];
    msg[n++] = ' '; msg[n++] = '(';
    n = append_uint(msg, n, s->idx);
    msg[n++] = '/';
    n = append_uint(msg, n, LIVE_N_PRESETS - 1u);
    msg[n++] = ')'; msg[n++] = ' '; msg[n++] = '>';
    msg[n] = '\0';
    render_banner_to(s->osd_a, msg, 0);
    render_banner_to(s->osd_b, msg, 0);
}

static void clear_banner(volatile struct live_state *s) {
    render_banner_to(s->osd_a, "", 1);
    render_banner_to(s->osd_b, "", 1);
}

uint32_t live_event_push(void *mgr, void *ev, uint32_t notify) {
    volatile struct live_state *s = LIVE_STATE;
    if (s->magic == LIVE_MAGIC && ev) {
        uint32_t id = *(const volatile uint32_t *)((uintptr_t)ev + 4u);
        if (!s->armed) {
            if (s->swallow_enter_rel && id == KEY_ENTER_REL) {
                s->swallow_enter_rel = 0u;
                return 0u;
            }
            return live_original_event_push(mgr, ev, notify);
        }
        if (id == KEY_MENU_EVENT) {
            if (s->skip_first_menu) {
                s->skip_first_menu = 0u;
                return live_original_event_push(mgr, ev, notify);
            }
            s->armed = 0u;
            clear_banner(s);
            log_line("menu_disarm", s->idx);
            return live_original_event_push(mgr, ev, notify);
        }
        if (id == KEY_LEFT_PRESS || id == KEY_UP_PRESS) {
            s->skip_first_menu = 0u;
            s->idx = (s->idx == 0u) ? (LIVE_N_PRESETS - 1u) : (s->idx - 1u);
            apply_preset(s->idx);
            draw_banner(s);
            log_line("preset", s->idx);
            return 0u;
        }
        if (id == KEY_RIGHT_PRESS || id == KEY_DOWN_PRESS) {
            s->skip_first_menu = 0u;
            s->idx = (s->idx + 1u >= LIVE_N_PRESETS) ? 0u : (s->idx + 1u);
            apply_preset(s->idx);
            draw_banner(s);
            log_line("preset", s->idx);
            return 0u;
        }
        if (id == KEY_LEFT_REL || id == KEY_RIGHT_REL ||
            id == KEY_UP_REL || id == KEY_DOWN_REL) {
            return 0u;
        }
        if (id == KEY_ENTER_PRESS) {
            s->armed = 0u;
            s->swallow_enter_rel = 1u;
            clear_banner(s);
            log_line("lock", s->idx);
            return 0u;
        }
        if (id == 0x15070300u || id == 0x15070190u) {
            draw_banner(s);
        }
    }
    return live_original_event_push(mgr, ev, notify);
}

void live_life(int on) {
    volatile struct live_state *s = LIVE_STATE;
    if (on) {
        s->magic = 0u;
        __sync_synchronize();
        s->armed = 1u;
        s->skip_first_menu = 1u;
        s->swallow_enter_rel = 0u;
        s->idx = 1u; /* Start on first film sim (Classic Chrome) */
        find_osd_maps(s);
        apply_preset(s->idx);
        draw_banner(s);
        __sync_synchronize();
        s->magic = LIVE_MAGIC;
        log_line("live_on", (s->idx << 16) | (uint32_t)(LIVE_LUT_IDX[s->idx] >= 0 ? 1u : 0u));
    } else {
        s->armed = 0u;
        __sync_synchronize();
        s->magic = 0u;
        clear_banner(s);
        apply_preset(0u); /* Restore stock on OFF */
        log_line("live_off", 0u);
    }
    __sync_synchronize();
}
