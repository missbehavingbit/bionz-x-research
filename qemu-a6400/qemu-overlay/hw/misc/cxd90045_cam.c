/*
 * Sony CXD90045 (BIONZ X) Opt-In Live-View Front-End & AIF DMA Models
 *
 * Adapted in part from Oein/a5000-emulator hw/misc/cxd90014_campig.c and
 * hw/dma/cxd90014_aif.c (GPL-2.0, Park SungHyun), re-derived against the
 * ILCE-6400 CXD90045 av-cam.bin contracts:
 *   - Sgc1 + Iif at 0xF3120000..0xF3121FFF (IRQ 35): Event A (+0x48/+0x50),
 *     Event B (+0x4C/+0x54, ordered after Event A), 36 MHz sync generator
 *     (+0x210, +0x220, +0x224), and CXD90045 line latch (+0x214/+0x218).
 *   - CamPig front-end units at 0xF3000000 (IRQ 36) and 0xF3100000 (IRQ 37):
 *     Sgc1 frame end completes armed paths (+0x10 -> bits 24..27 at +0x48)
 *     after latching scene-derived PQD_Ydet 8x5 (40-word) luminance
 *     statistics at +0x15700..+0x1579C for the programmed window (+0x15848).
 *   - CXD90045 AIF DMA channel groups (IRQs 40..50): RC0 (0xF3881000,
 *     IRQs 40/41) completes asynchronously after both ch3 (DRAM writer)
 *     and ch0 (IIB reader) are armed. The test-source scaler writes Y8
 *     using the guest-programmed destination and stride, not VPH state.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/misc/cxd90045_cam.h"
#include "hw/core/sysbus.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "qapi/error.h"
#include "qemu/ctype.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"
#include "qom/object.h"
#include "system/address-spaces.h"
#include "system/memory.h"

/* -------------------------------------------------------------------------
 * Deterministic test-image source (bars, ramp, none, pgm:PATH)
 * ------------------------------------------------------------------------- */

static bool parse_pgm_token(const char **p, const char *end, uint32_t *out)
{
    const char *s = *p;
    uint64_t val = 0;
    bool digits = false;

    while (s < end) {
        if (*s == '#') {
            while (s < end && *s != '\n') {
                s++;
            }
        } else if (qemu_isspace((unsigned char)*s)) {
            s++;
        } else {
            break;
        }
    }
    while (s < end && qemu_isdigit((unsigned char)*s)) {
        digits = true;
        val = val * 10 + (*s - '0');
        if (val > 0xFFFFu) {
            return false;
        }
        s++;
    }
    if (!digits) {
        return false;
    }
    *p = s;
    *out = (uint32_t)val;
    return true;
}

bool cxd90045_liveview_source_parse(Cxd90045LiveViewSource *src,
                                    const char *spec, Error **errp)
{
    g_free(src->pixels);
    memset(src, 0, sizeof(*src));
    src->width = 1600;
    src->height = 570;

    if (!spec || !*spec || !strcmp(spec, "none")) {
        src->kind = CXD90045_LV_NONE;
        return true;
    }
    if (!strcmp(spec, "bars")) {
        src->kind = CXD90045_LV_BARS;
        return true;
    }
    if (!strcmp(spec, "ramp")) {
        src->kind = CXD90045_LV_RAMP;
        return true;
    }
    if (g_str_has_prefix(spec, "pgm:")) {
        const char *path = spec + 4;
        g_autofree gchar *buf = NULL;
        gsize len = 0;
        GError *gerr = NULL;
        const char *p, *end;
        uint32_t w, h, maxv;
        size_t need;

        if (!g_file_get_contents(path, &buf, &len, &gerr)) {
            error_setg(errp, "liveview-source cannot read '%s': %s",
                       path, gerr->message);
            g_error_free(gerr);
            return false;
        }
        if (len < 7 || buf[0] != 'P' || buf[1] != '5' ||
            !qemu_isspace((unsigned char)buf[2])) {
            error_setg(errp, "liveview-source '%s' is not a binary P5 PGM",
                       path);
            return false;
        }
        p = buf + 2;
        end = buf + len;
        if (!parse_pgm_token(&p, end, &w) ||
            !parse_pgm_token(&p, end, &h) ||
            !parse_pgm_token(&p, end, &maxv) ||
            w == 0 || h == 0 || w > 8192 || h > 8192 ||
            maxv == 0 || maxv > 255 ||
            p >= end || !qemu_isspace((unsigned char)*p)) {
            error_setg(errp, "liveview-source '%s' has invalid PGM header",
                       path);
            return false;
        }
        p++; /* single whitespace byte after maxval */
        need = (size_t)w * h;
        if ((size_t)(end - p) < need) {
            error_setg(errp, "liveview-source '%s' is truncated", path);
            return false;
        }
        src->kind = CXD90045_LV_PGM;
        src->width = w;
        src->height = h;
        src->pixels = g_malloc(need);
        if (maxv == 255) {
            memcpy(src->pixels, p, need);
        } else {
            for (size_t i = 0; i < need; i++) {
                src->pixels[i] = ((uint32_t)(uint8_t)p[i] * 255u) / maxv;
            }
        }
        return true;
    }

    error_setg(errp, "unsupported liveview-source '%s' "
               "(expected bars, ramp, none, or pgm:PATH)", spec);
    return false;
}

uint8_t cxd90045_liveview_sample(const Cxd90045LiveViewSource *src,
                                 uint32_t x, uint32_t y,
                                 uint32_t w, uint32_t h)
{
    static const uint8_t smpte_luma[8] = {
        235, 210, 170, 145, 106, 81, 41, 16
    };

    if (!src || w == 0 || h == 0) {
        return 0;
    }
    if (x >= w) {
        x = w - 1;
    }
    if (y >= h) {
        y = h - 1;
    }

    switch (src->kind) {
    case CXD90045_LV_NONE:
        return 0;
    case CXD90045_LV_BARS:
        if (y < (h * 3u) / 4u) {
            return smpte_luma[((uint64_t)x * 8u) / w];
        }
        if (y < (h * 7u) / 8u) {
            return w > 1 ? (uint8_t)(((uint64_t)x * 255u) / (w - 1)) : 128;
        }
        return ((((uint64_t)x * 16u) / w) & 1u) ? 16 : 235;
    case CXD90045_LV_RAMP: {
        uint8_t v = (uint8_t)((((uint64_t)x * 192u) / w +
                               ((uint64_t)y * 63u) / h) & 0xffu);
        if (x >= (w * 3u) / 8u && x < (w * 5u) / 8u &&
            y >= (h * 3u) / 8u && y < (h * 5u) / 8u) {
            v = (uint8_t)(255u - v);
        }
        return v;
    }
    case CXD90045_LV_PGM: {
        uint32_t sx = (uint32_t)(((uint64_t)x * src->width) / w);
        uint32_t sy = (uint32_t)(((uint64_t)y * src->height) / h);
        return src->pixels[(size_t)sy * src->width + sx];
    }
    default:
        return 0;
    }
}

/* -------------------------------------------------------------------------
 * CXD90045 CamPig front-end & Sgc1/Iif input sync (IRQs 35, 36, 37)
 * ------------------------------------------------------------------------- */

OBJECT_DECLARE_SIMPLE_TYPE(Cxd90045CamPigState, CXD90045_CAMPIG)

#define CAMPIG_MAX_WORDS   (0x20000 / 4)
#define SGC1_CLOCK_HZ      36000000ULL
#define CAMPIG_FRAME_NS    33366667ULL
#define YDET_STAT_OFF      0x15700
#define YDET_WIN_OFF       0x15848
#define YDET_COLS          8
#define YDET_ROWS          5
#define YDET_WORDS         (YDET_COLS * YDET_ROWS)

struct Cxd90045CamPigState {
    SysBusDevice parent_obj;
    MemoryRegion mr;
    QEMUTimer *timer;
    QEMUTimer *iif_timer;
    qemu_irq irq;
    qemu_irq frame_done;

    uint32_t regs[CAMPIG_MAX_WORDS];
    uint32_t st48;
    uint32_t st4c;
    uint32_t armed;
    bool sg_cmp;
    bool sg_hold;
    int64_t sg_end_ns;
    bool ydet_dirty;
    uint32_t frames;

    /* properties */
    bool is_sg;
    uint32_t mmio_size;
    const Cxd90045LiveViewSource *lv_src;
};

static void campig_update_irq(Cxd90045CamPigState *s)
{
    uint32_t p = (s->st48 & s->regs[0x50 >> 2]) |
                 (s->st4c & s->regs[0x54 >> 2]);
    qemu_set_irq(s->irq, p != 0);
}

static void campig_refresh_ydet(Cxd90045CamPigState *s)
{
    uint32_t x0 = s->regs[(YDET_WIN_OFF + 0x00) >> 2];
    uint32_t w  = s->regs[(YDET_WIN_OFF + 0x04) >> 2];
    uint32_t y0 = s->regs[(YDET_WIN_OFF + 0x08) >> 2];
    uint32_t h  = s->regs[(YDET_WIN_OFF + 0x0C) >> 2];
    uint32_t *dst = &s->regs[YDET_STAT_OFF >> 2];
    uint32_t total_w, total_h;

    if (!s->ydet_dirty) {
        return;
    }
    s->ydet_dirty = false;

    if (!s->lv_src || w == 0 || h == 0 || w > 4096 || h > 4096 ||
        x0 > UINT32_MAX - w || y0 > UINT32_MAX - h) {
        memset(dst, 0, YDET_WORDS * sizeof(uint32_t));
        return;
    }

    total_w = MAX(x0 + w, 1628u);
    total_h = MAX(y0 + h, 574u);
    for (uint32_t zy = 0; zy < YDET_ROWS; zy++) {
        uint32_t ys = y0 + (zy * h) / YDET_ROWS;
        uint32_t ye = y0 + ((zy + 1) * h) / YDET_ROWS;
        for (uint32_t zx = 0; zx < YDET_COLS; zx++) {
            uint32_t xs = x0 + (zx * w) / YDET_COLS;
            uint32_t xe = x0 + ((zx + 1) * w) / YDET_COLS;
            uint64_t sum = 0;

            for (uint32_t y = ys; y < ye; y++) {
                for (uint32_t x = xs; x < xe; x++) {
                    sum += cxd90045_liveview_sample(s->lv_src, x, y,
                                                    total_w, total_h);
                }
            }
            dst[zy * YDET_COLS + zx] = (uint32_t)MIN(sum, (uint64_t)UINT32_MAX);
        }
    }
}

static void sgc1_start_frame(Cxd90045CamPigState *s, int64_t now)
{
    uint32_t vh = s->regs[0x220 >> 2];
    uint64_t lines = ((vh >> 16) & 0x3fff) + 1;
    uint64_t hclks = (vh & 0x3fff) + 1;
    uint64_t cmp = s->regs[0x224 >> 2] & 0x3fff;

    if (!vh) {
        s->regs[0x210 >> 2] &= ~1u;
        s->regs[0x218 >> 2] = 0x3fff3fffu;
        return;
    }

    s->regs[0x218 >> 2] = 0x00800080u;
    s->sg_end_ns = now + DIV_ROUND_UP(lines * hclks * NANOSECONDS_PER_SECOND,
                                      SGC1_CLOCK_HZ);
    s->sg_cmp = cmp < lines;
    if (s->sg_cmp) {
        timer_mod(s->timer, now +
                  DIV_ROUND_UP(cmp * hclks * NANOSECONDS_PER_SECOND,
                               SGC1_CLOCK_HZ));
    } else {
        timer_mod(s->timer, s->sg_end_ns);
    }
}

static void iif_vd_tick(void *opaque)
{
    Cxd90045CamPigState *s = opaque;

    if (!(s->regs[0x210 >> 2] & 1u)) {
        s->regs[0x410 >> 2] &= ~1u;
    }
    timer_mod(s->iif_timer,
              qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + CAMPIG_FRAME_NS);
}

static void sgc1_tick(void *opaque)
{
    Cxd90045CamPigState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    if (s->sg_cmp) {
        s->sg_cmp = false;
        s->st48 |= 1u;
        campig_update_irq(s);
        timer_mod(s->timer, s->sg_end_ns);
        return;
    }

    /* Latch B after A is acknowledged; the raster itself has ended. */
    s->regs[0x410 >> 2] &= ~1u;
    if (s->st48 & s->regs[0x50 >> 2] & 1u) {
        s->sg_hold = true;
    } else {
        s->st4c |= 1u;
    }
    campig_update_irq(s);
    qemu_irq_pulse(s->frame_done);
    if (s->regs[0x210 >> 2] & 0x100u) {
        sgc1_start_frame(s, now);
    } else {
        s->regs[0x210 >> 2] &= ~1u;
        s->regs[0x218 >> 2] = 0x3fff3fffu;
    }
}

static void campig_input_frame(void *opaque, int n, int level)
{
    Cxd90045CamPigState *s = opaque;

    if (!level) {
        return;
    }

    if (s->armed) {
        campig_refresh_ydet(s);
        s->st48 |= s->armed;
        s->armed = 0;
        s->frames++;
        if (s->frames <= 8) {
            fprintf(stderr, "[CamPig] frame #%u st48=0x%08x en50=0x%08x "
                    "ydet0=0x%08x win=(%u,%u,%u,%u)\n",
                    s->frames, s->st48, s->regs[0x50 >> 2],
                    s->regs[YDET_STAT_OFF >> 2],
                    s->regs[(YDET_WIN_OFF + 0) >> 2],
                    s->regs[(YDET_WIN_OFF + 4) >> 2],
                    s->regs[(YDET_WIN_OFF + 8) >> 2],
                    s->regs[(YDET_WIN_OFF + 12) >> 2]);
        }
    }
    qemu_irq_pulse(s->frame_done);
    campig_update_irq(s);
}

static uint64_t campig_read(void *opaque, hwaddr off, unsigned size)
{
    Cxd90045CamPigState *s = opaque;
    hwaddr w = off & ~3ULL;
    unsigned sh = (off & 3) * 8;
    uint32_t v = 0;

    if (w < s->mmio_size) {
        switch (w) {
        case 0x3C:
            v = s->regs[w >> 2] & ~3u;
            break;
        case 0x48:
            v = s->st48;
            break;
        case 0x4C:
            v = s->st4c;
            break;
        case 0x210:
        case 0x310:
        case 0x410:
            v = s->is_sg ? (s->regs[w >> 2] & ~0x10000u) : s->regs[w >> 2];
            break;
        case 0x214:
        case 0x314:
            v = s->is_sg ? 0 : s->regs[w >> 2];
            break;
        default:
            v = s->regs[w >> 2];
            break;
        }
    }
    return size == 4 ? v : (v >> sh) & ((1u << (size * 8)) - 1);
}

static void campig_write(void *opaque, hwaddr off, uint64_t val, unsigned size)
{
    Cxd90045CamPigState *s = opaque;
    hwaddr w = off & ~3ULL;
    unsigned sh = (off & 3) * 8;
    uint32_t m = size == 4 ? 0xFFFFFFFFu : ((1u << (size * 8)) - 1) << sh;
    uint32_t v;

    if (w >= s->mmio_size) {
        return;
    }
    v = (s->regs[w >> 2] & ~m) | (((uint32_t)val << sh) & m);

    switch (w) {
    case 0x10:
        if (!s->is_sg) {
            s->armed |= (((uint32_t)val << sh) & m & 0xFu) << 24;
        }
        s->regs[w >> 2] = v;
        break;
    case 0x48:
        s->st48 &= ~(((uint32_t)val << sh) & m);
        if (s->is_sg && s->sg_hold && !(s->st48 & 1u)) {
            s->sg_hold = false;
            s->st4c |= 1u;
        }
        campig_update_irq(s);
        break;
    case 0x4C:
        s->st4c &= ~(((uint32_t)val << sh) & m);
        campig_update_irq(s);
        break;
    case 0x50:
    case 0x54:
        s->regs[w >> 2] = v;
        campig_update_irq(s);
        break;
    case 0x200:
        s->regs[w >> 2] = v;
        if (s->is_sg && !(v & 1u)) {
            timer_del(s->timer);
            s->sg_cmp = false;
            s->sg_hold = false;
            s->regs[0x210 >> 2] &= ~1u;
            s->regs[0x218 >> 2] = 0x3fff3fffu;
        }
        break;
    case 0x210:
        if (s->is_sg) {
            bool was_running = (s->regs[w >> 2] & 1u) != 0;
            s->regs[w >> 2] = v & 0x101u;
            if (!(v & 1u)) {
                timer_del(s->timer);
                s->sg_cmp = false;
                s->sg_hold = false;
                s->regs[0x218 >> 2] = 0x3fff3fffu;
            } else if (!was_running) {
                sgc1_start_frame(s, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
            }
        } else {
            s->regs[w >> 2] = v;
        }
        break;
    case 0x214:
    case 0x228:
        if (s->is_sg) {
            s->regs[w >> 2] = v & ~1u;
            s->regs[0x218 >> 2] = (s->regs[0x210 >> 2] & 1u) ?
                                  0x00800080u : 0x3fff3fffu;
        } else {
            s->regs[w >> 2] = v;
        }
        break;
    case 0x300:
        s->regs[w >> 2] = v;
        if (s->is_sg && !(v & 1u)) {
            s->regs[0x310 >> 2] &= ~1u;
            s->regs[0x318 >> 2] = 0x3fff3fffu;
        }
        break;
    case 0x310:
        s->regs[w >> 2] = v;
        if (s->is_sg) {
            s->regs[0x318 >> 2] = (v & 1u) ? 0x00800080u : 0x3fff3fffu;
        }
        break;
    case 0x314:
        if (s->is_sg) {
            s->regs[w >> 2] = v & ~1u;
            s->regs[0x318 >> 2] = (s->regs[0x310 >> 2] & 1u) ?
                                  0x00800080u : 0x3fff3fffu;
        } else {
            s->regs[w >> 2] = v;
        }
        break;
    default:
        s->regs[w >> 2] = v;
        if (!s->is_sg && w >= 0x15800 && w <= 0x15854) {
            s->ydet_dirty = true;
        }
        break;
    }
}

static const MemoryRegionOps campig_ops = {
    .read = campig_read,
    .write = campig_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 8 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static void campig_reset(DeviceState *dev)
{
    Cxd90045CamPigState *s = CXD90045_CAMPIG(dev);

    if (s->is_sg) {
        timer_del(s->timer);
        timer_del(s->iif_timer);
    }
    memset(s->regs, 0, sizeof(s->regs));
    s->st48 = 0;
    s->st4c = 0;
    s->armed = 0;
    s->sg_cmp = false;
    s->sg_hold = false;
    s->ydet_dirty = true;
    s->frames = 0;
    if (s->is_sg) {
        s->regs[0x218 >> 2] = 0x3fff3fffu;
        s->regs[0x318 >> 2] = 0x3fff3fffu;
        timer_mod(s->iif_timer,
                  qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + CAMPIG_FRAME_NS);
    }
    campig_update_irq(s);
}

static void campig_realize(DeviceState *dev, Error **errp)
{
    Cxd90045CamPigState *s = CXD90045_CAMPIG(dev);

    if (s->mmio_size == 0 || s->mmio_size > sizeof(s->regs) ||
        (s->mmio_size & 0xfff)) {
        error_setg(errp, "cxd90045-campig: invalid mmio-size 0x%x",
                   s->mmio_size);
        return;
    }
    memory_region_init_io(&s->mr, OBJECT(dev), &campig_ops, s,
                          s->is_sg ? "cxd90045.sgc1" : "cxd90045.campig",
                          s->mmio_size);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->mr);
    sysbus_init_irq(SYS_BUS_DEVICE(dev), &s->irq);
    qdev_init_gpio_out_named(dev, &s->frame_done, "frame", 1);
    if (s->is_sg) {
        s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, sgc1_tick, s);
        s->iif_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, iif_vd_tick, s);
    } else {
        qdev_init_gpio_in_named(dev, campig_input_frame, "input-frame", 1);
    }
}

void cxd90045_campig_set_source(DeviceState *dev,
                                const Cxd90045LiveViewSource *src)
{
    Cxd90045CamPigState *s = CXD90045_CAMPIG(dev);
    s->lv_src = src;
    s->ydet_dirty = true;
}

static const Property campig_props[] = {
    DEFINE_PROP_BOOL("is-sg", Cxd90045CamPigState, is_sg, false),
    DEFINE_PROP_UINT32("mmio-size", Cxd90045CamPigState, mmio_size, 0x20000),
};

static void campig_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = campig_realize;
    device_class_set_legacy_reset(dc, campig_reset);
    device_class_set_props(dc, campig_props);
}

static const TypeInfo campig_info = {
    .name = TYPE_CXD90045_CAMPIG,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Cxd90045CamPigState),
    .class_init = campig_class_init,
};

/* -------------------------------------------------------------------------
 * CXD90045 AIF DMA channel group (0xF3881000 RC0, 0xF3501000, etc.)
 * ------------------------------------------------------------------------- */

OBJECT_DECLARE_SIMPLE_TYPE(Cxd90045AifState, CXD90045_AIF)

#define AIF_MAX_CH       6
#define AIF_MMIO_SIZE    0x800
#define AIF_DONE_NS      2000000ULL /* Experimental virtual transfer time. */
#define AIF_CH_BIT(ch)   (1u << (4 * (ch)))

struct Cxd90045AifState {
    SysBusDevice parent_obj;
    MemoryRegion mr;
    QEMUTimer *done_timer;
    qemu_irq irq[2];

    uint32_t regs[AIF_MMIO_SIZE / 4];
    uint32_t st;
    uint32_t armed;
    uint32_t pending;
    uint32_t writes_logged;

    /* properties */
    uint32_t nch;
    bool frame_sync;
    int32_t liveview_ch;
    uint32_t irq0_mask;
    uint32_t irq1_mask;
    const Cxd90045LiveViewSource *lv_src;
};

static void aif_update_irq(Cxd90045AifState *s)
{
    uint32_t p = s->st & s->regs[0x08 >> 2];

    qemu_set_irq(s->irq[0], (p & s->irq0_mask) != 0);
    qemu_set_irq(s->irq[1], (p & s->irq1_mask) != 0);
}

static bool aif_write_liveview_plane(Cxd90045AifState *s, unsigned ch)
{
    hwaddr cb = 0x200 + 0x80 * ch;
    uint32_t ctrl  = s->regs[(cb + 0x00) >> 2];
    uint32_t base  = s->regs[(cb + 0x20) >> 2];
    uint32_t lb    = s->regs[(cb + 0x24) >> 2];
    uint32_t skip  = s->regs[(cb + 0x28) >> 2];
    uint32_t lines = (s->regs[(cb + 0x2C) >> 2] & 0x3fffu) + 1u;
    uint64_t pitch = (uint64_t)lb + skip;
    uint64_t span;
    g_autofree uint8_t *row = NULL;

    if (!s->lv_src || (ctrl & 0x800u) || lb == 0 || lb > 8192 ||
        lines == 0 || lines > 4096 || pitch > 16384) {
        qemu_log_mask(LOG_GUEST_ERROR, "cxd90045-aif: unsupported Y8 layout "
                      "(ctrl=%#x, lb=%u, skip=%u, lines=%u)\n",
                      ctrl, lb, skip, lines);
        return false;
    }
    span = pitch * (lines - 1u) + lb;
    /* Restrict DMA writes strictly to guest heap/framebuffers in RAM0. */
    if (base < 0x04800000u || (uint64_t)base + span > 0x40000000ULL) {
        qemu_log_mask(LOG_GUEST_ERROR, "cxd90045-aif: invalid Y8 PA %#x\n",
                      base);
        return false;
    }

    row = g_malloc(lb);
    for (uint32_t y = 0; y < lines; y++) {
        for (uint32_t x = 0; x < lb; x++) {
            row[x] = cxd90045_liveview_sample(s->lv_src, x, y, lb, lines);
        }
        if (address_space_write(&address_space_memory,
                                (hwaddr)base + (uint64_t)y * pitch,
                                MEMTXATTRS_UNSPECIFIED, row, lb) != MEMTX_OK) {
            qemu_log_mask(LOG_GUEST_ERROR, "cxd90045-aif: Y8 DMA failed "
                          "at PA %#x, row %u\n", base, y);
            return false;
        }
    }
    if (s->writes_logged < 8) {
        s->writes_logged++;
        fprintf(stderr, "[AIF-LV] ch+%#x -> PA 0x%08x lb=0x%x skip=0x%x "
                "lines=%u sample0=0x%02x\n",
                (unsigned)cb, base, lb, skip, lines, row[0]);
    }
    return true;
}

static uint32_t aif_liveview_pair(Cxd90045AifState *s)
{
    return s->liveview_ch < 0 ? 0 : AIF_CH_BIT(0) | AIF_CH_BIT(s->liveview_ch);
}

static uint32_t aif_ready_mask(Cxd90045AifState *s)
{
    uint32_t pair = aif_liveview_pair(s);

    return (s->pending & pair) == pair ? s->pending : s->pending & ~pair;
}

static void aif_schedule(Cxd90045AifState *s)
{
    if (aif_ready_mask(s)) {
        timer_mod(s->done_timer,
                  qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + AIF_DONE_NS);
    } else {
        timer_del(s->done_timer);
    }
}

static void aif_complete_mask(Cxd90045AifState *s, uint32_t mask)
{
    uint32_t pair = aif_liveview_pair(s);

    if ((mask & pair) && !aif_write_liveview_plane(s, s->liveview_ch)) {
        mask &= ~pair;
    }
    for (unsigned ch = 0; ch < s->nch; ch++) {
        uint32_t bit = AIF_CH_BIT(ch);
        if (mask & bit) {
            hwaddr cb = 0x200 + 0x80 * ch;
            s->regs[cb >> 2] &= ~1u;
            s->regs[(cb + 4) >> 2] &= ~0x80000000u;
        }
    }
    s->st |= mask;
    aif_update_irq(s);
}

static void aif_done_tick(void *opaque)
{
    Cxd90045AifState *s = opaque;
    uint32_t done = aif_ready_mask(s);

    s->pending &= ~done;
    if (done) {
        aif_complete_mask(s, done);
    }
}

static void aif_frame_in(void *opaque, int n, int level)
{
    Cxd90045AifState *s = opaque;
    uint32_t done = s->armed;

    if (level && done) {
        s->armed = 0;
        aif_complete_mask(s, done);
    }
}

static uint64_t aif_read(void *opaque, hwaddr off, unsigned size)
{
    Cxd90045AifState *s = opaque;
    hwaddr w = off & ~3ULL;
    unsigned sh = (off & 3) * 8;
    uint32_t v = 0;

    if (w < AIF_MMIO_SIZE) {
        if (w == 0x00) {
            v = s->st;
        } else if (w == 0x04) {
            v = 0;
        } else {
            v = s->regs[w >> 2];
        }
    }
    return size == 4 ? v : (v >> sh) & ((1u << (size * 8)) - 1);
}

static void aif_write(void *opaque, hwaddr off, uint64_t val, unsigned size)
{
    Cxd90045AifState *s = opaque;
    hwaddr w = off & ~3ULL;
    unsigned sh = (off & 3) * 8;
    uint32_t m = size == 4 ? 0xFFFFFFFFu : ((1u << (size * 8)) - 1) << sh;
    uint32_t v;

    if (w >= AIF_MMIO_SIZE) {
        return;
    }
    v = (s->regs[w >> 2] & ~m) | (((uint32_t)val << sh) & m);

    if (w == 0x00) {
        s->st &= ~(((uint32_t)val << sh) & m);
        aif_update_irq(s);
        return;
    }
    if (w == 0x08 || w == 0x0C) {
        s->regs[w >> 2] = v;
        aif_update_irq(s);
        return;
    }
    if (w >= 0x200 && w < 0x200 + 0x80 * s->nch) {
        unsigned ch = (w - 0x200) >> 7;
        uint32_t bit = AIF_CH_BIT(ch);
        hwaddr reg = (w - 0x200) & 0x7f;

        if (reg == 0x00) {
            bool was = (s->regs[w >> 2] & 1u) != 0;
            s->regs[w >> 2] = v;
            if ((v & 1u) && !was) {
                s->regs[(w + 4) >> 2] |= 0x80000000u;
                if (s->frame_sync) {
                    s->armed |= bit;
                } else {
                    s->pending |= bit;
                    aif_schedule(s);
                }
            } else if (!(v & 1u) && was) {
                s->armed &= ~bit;
                s->pending &= ~bit;
                s->regs[(w + 4) >> 2] &= ~0x80000000u;
                if (!s->frame_sync) {
                    aif_schedule(s);
                }
            }
            return;
        }
        if (reg == 0x04) {
            s->regs[w >> 2] = (v & ~0x80000000u) |
                              (s->regs[w >> 2] & 0x80000000u);
            return;
        }
    }
    s->regs[w >> 2] = v;
}

static const MemoryRegionOps aif_ops = {
    .read = aif_read,
    .write = aif_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 8 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static void aif_reset(DeviceState *dev)
{
    Cxd90045AifState *s = CXD90045_AIF(dev);

    timer_del(s->done_timer);
    memset(s->regs, 0, sizeof(s->regs));
    s->st = 0;
    s->armed = 0;
    s->pending = 0;
    s->writes_logged = 0;
    aif_update_irq(s);
}

static void aif_realize(DeviceState *dev, Error **errp)
{
    Cxd90045AifState *s = CXD90045_AIF(dev);

    if (s->nch == 0 || s->nch > AIF_MAX_CH) {
        error_setg(errp, "cxd90045-aif: invalid nch %u", s->nch);
        return;
    }
    if (s->liveview_ch < -1 || s->liveview_ch == 0 ||
        s->liveview_ch >= (int32_t)s->nch ||
        (s->liveview_ch > 0 && s->frame_sync)) {
        error_setg(errp, "cxd90045-aif: invalid liveview-ch %d",
                   s->liveview_ch);
        return;
    }
    memory_region_init_io(&s->mr, OBJECT(dev), &aif_ops, s,
                          "cxd90045.aif", AIF_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->mr);
    sysbus_init_irq(SYS_BUS_DEVICE(dev), &s->irq[0]);
    sysbus_init_irq(SYS_BUS_DEVICE(dev), &s->irq[1]);
    qdev_init_gpio_in_named(dev, aif_frame_in, "frame", 1);
    s->done_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, aif_done_tick, s);
}

void cxd90045_aif_set_source(DeviceState *dev,
                             const Cxd90045LiveViewSource *src)
{
    Cxd90045AifState *s = CXD90045_AIF(dev);
    s->lv_src = src;
}

static const Property aif_props[] = {
    DEFINE_PROP_UINT32("nch", Cxd90045AifState, nch, 4),
    DEFINE_PROP_BOOL("frame-sync", Cxd90045AifState, frame_sync, false),
    DEFINE_PROP_INT32("liveview-ch", Cxd90045AifState, liveview_ch, -1),
    DEFINE_PROP_UINT32("irq0-mask", Cxd90045AifState, irq0_mask, 0x000000F0u),
    DEFINE_PROP_UINT32("irq1-mask", Cxd90045AifState, irq1_mask, 0x0000F000u),
};

static void aif_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = aif_realize;
    device_class_set_legacy_reset(dc, aif_reset);
    device_class_set_props(dc, aif_props);
}

static const TypeInfo aif_info = {
    .name = TYPE_CXD90045_AIF,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Cxd90045AifState),
    .class_init = aif_class_init,
};

/* -------------------------------------------------------------------------
 * SIF monotonic timing counters (0xF3122000)
 * ------------------------------------------------------------------------- */

static void cxd90045_cam_register_types(void)
{
    type_register_static(&campig_info);
    type_register_static(&aif_info);
}

type_init(cxd90045_cam_register_types)
