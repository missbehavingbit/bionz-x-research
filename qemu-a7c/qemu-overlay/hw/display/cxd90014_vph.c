/*
 * CXD90014 video output: VPH display controller + SYSV frame timer (a5000).
 *
 *   VPH  PA 0xF3440000..0xF3446FFF  (docs/RE_DISPLAY_SCANOUT.md)
 *        0x028 = 0x111 clock enable (we start vsync once it is non-zero)
 *        0x144 bit0 panel YUV layer on, bit1 OSD [?]; 0x404 bit0 OSD 4 B/px
 *        AIF DMA ch51..58 @ 0xF3441200 + 0x80*k: +0x00 bit0 start/busy
 *        (cleared by hardware at end of frame), +0x04 bit31 running,
 *        +0x20 start address, +0x24 bytes per line, +0x2C lines-1,
 *        +0x48 signed pitch.  ch51 = panel YUV (live view), ch53 = panel OSD.
 *        IRQ 101 / 102 = output vsync (both pulsed; which one is the panel is
 *        unknown), IRQ 79 = status at +0xA0 (not raised).
 *   SYSV PA 0xF3402000 (docs/RE_AVCAM_IRQ_BOOT.md): +0x98 V counter,
 *        +0x28 bit0 V phase, +0x3C period (av-cam writes 64800); IRQ 98
 *        every V (33367 us, master clock, drives the evsel engine frame).
 *
 * Every 1/60 s: SYSV counter++ and IRQ 98 pulse; if VPH is clocked, IRQ
 * 101/102 pulses, AIF ch51..58 start bits cleared, and the panel is composed
 * (live-view luma plane under the OSD) into QEMU graphic console 0.
 * OSD at 16 bpp = RGBA4444 (A low nibble); ch51 = 8-bit luma plane.
 *
 * HDMI = the VPH line output (docs/RE_HDMI.md §2): while the HDMI TX model
 * reports TMDS active (GPIO in "hdmi-tmds"), the line layers ch52 (YUV,
 * enable +0x184 bit0) and ch54 (OSD, 4 B/px when +0x414 bit0) are composed
 * into QEMU graphic console 1, sized from +0x454 ([11:0] width, [27:16]
 * lines per field; doubled for the interlaced formats of +0x410 [18:16]).
 * Without a signal console 1 shows a dark "no signal" surface.
 *
 * Test images go through guest memory: -global cxd90014-aif.lv-source=...
 * fills the buffer at the resize output (hw/dma/cxd90014_aif.c).
 *
 * v2.1 (from bionz-x-research qemu-a6400, all opt-in, defaults unchanged):
 *   arm-gate=on      compose a layer only in a frame the guest armed (AIF +0x00
 *                    bit0 written since the last vsync), composed at vsync as the
 *                    hardware scans out; without it the A7C model keeps the last
 *                    drawn OSD (empty-run hold)
 *   stage-sticky=on  +0x140 bits 28-31 write-1-to-set (panel input stage mode,
 *                    0x30000000 = running) and the YUV layer follows that mode too
 *   debug=on         [VPH] lines on stderr: ch51/ch53/ch54 programming, 0x144
 *                    changes, arm counts per second of guest time
 */

#include "qemu/osdep.h"
#include "hw/sysbus.h"
#include "hw/irq.h"
#include "hw/qdev-properties.h"
#include "qemu/timer.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "ui/console.h"
#include "system/address-spaces.h"
#include "qom/object.h"

#define TYPE_CXD_VPH "cxd90014-vph"
OBJECT_DECLARE_SIMPLE_TYPE(VphState, CXD_VPH)

#define VPH_SIZE  0x7000
#define AIF(k)    (0x1200 + 0x80 * (k))     /* k = 0 (ch51) .. 7 (ch58) */

struct VphState {
    SysBusDevice parent_obj;
    MemoryRegion vph_mr, sysv_mr;
    uint32_t vph[VPH_SIZE / 4];
    uint32_t sysv[0x1000 / 4];
    uint32_t frame;
    uint32_t aif_started;           /* bit k: AIF channel k armed since the last vsync */
    uint32_t osd_visible;           /* ch53 armed in the frame that just ended */
    uint32_t line_visible;          /* ch54 armed in the frame that just ended */
    uint32_t arms[8];               /* arm writes per channel (debug) */
    bool arm_gate, stage_sticky, debug;
    QEMUTimer *tick, *lower, *vtick, *vlower;
    qemu_irq irq_sysv, irq_v101, irq_v102;
    qemu_irq vd_out;                /* -> evsel engine frame (source 29) */
    QemuConsole *con;
    uint32_t w, h;
    bool panel_fallback;            /* show the line OSD when the panel is off */
    QemuConsole *hdmi_con;          /* line output (HDMI) */
    bool hdmi_tmds;
};

#define R(o) (s->vph[(o) / 4])

/* +0x140 bits 28-29: panel input stage mode, 3 = running (bionz E0652; opt-in) */
#define VPH_140_PANEL_MODE 0x30000000u
#define VPH_140_STICKY     0xF0000000u

static bool vph_panel_stage_running(VphState *s)
{
    return s->stage_sticky &&
           (R(0x140) & VPH_140_PANEL_MODE) == VPH_140_PANEL_MODE;
}

static void vph_update(void *opaque);
static void vph_debug_tick(VphState *s);

/*
 * SYSV is the master V clock of the camera engine: one V per engine frame
 * (33367 us, av-cam constant 0x8257).  Per V, in this order: counter +0x98
 * increments (so +0x28 bit0, its parity, toggles), IRQ 98 pulses, then the
 * engine frame event (evsel source 29) fires.  av-cam's 2V mode checks the
 * phase bit against its own V counter every frame (VD_Pre 4376D904) and
 * VDF hands a YC pin to VPH only while +0x98 equals the pin's frame
 * (4367EC50); a 60 Hz SYSV drifted against the engine frame and both
 * failed every frame (docs/RE_LIVEVIEW_PIPELINE.md §5).  The panel vsync
 * (IRQ 101/102) keeps its own 60 Hz timer [?].
 */
#define V_US 33367

static void vph_vlower(void *opaque)
{
    VphState *s = opaque;
    qemu_set_irq(s->irq_sysv, 0);
    qemu_set_irq(s->vd_out, 0);
}

static void vph_vtick(void *opaque)
{
    VphState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    s->frame++;
    qemu_set_irq(s->irq_sysv, 1);
    qemu_set_irq(s->vd_out, 1);
    timer_mod(s->vlower, now + 200 * SCALE_US);
    timer_mod(s->vtick, now + V_US * SCALE_US);
}

static void vph_lower(void *opaque)
{
    VphState *s = opaque;
    qemu_set_irq(s->irq_v101, 0);
    qemu_set_irq(s->irq_v102, 0);
}

static void vph_tick(void *opaque)
{
    VphState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    /* output vsync runs whether or not VPH is configured yet: the VDF task
     * waits for IRQ 101/102 before it programs anything */
    for (int k = 0; k < 8; k++) {
        R(AIF(k)) &= ~1u;
    }
    /* latch what the guest armed in the frame that just ended (bionz E0639) */
    s->osd_visible = (s->aif_started >> 2) & 1u;
    s->line_visible = (s->aif_started >> 3) & 1u;
    if (s->arm_gate && s->con) {
        vph_update(s);              /* scan out at vsync with this frame's arms */
    }
    s->aif_started = 0;
    if (s->debug) {
        vph_debug_tick(s);
    }
    qemu_set_irq(s->irq_v101, 1);
    qemu_set_irq(s->irq_v102, 1);
    timer_mod(s->lower, now + 200 * SCALE_US);  /* no ack register: pulse */
    timer_mod(s->tick, now + NANOSECONDS_PER_SECOND / 60);
}

/*
 * Compose one output: YUV layer from AIF channel ky (when yuv_on), OSD from
 * channel ko at bpp bytes per pixel.  The panel OSD is drawn 1:1; the line
 * OSD is scaled to the line size (osd_scale) [? the line resizer at +0x6500
 * is not decoded, nearest-neighbour full-frame here].
 */
static unsigned vph_opaque;         /* OSD pixels with alpha in the last compose */

static void vph_compose(VphState *s, uint32_t *dst, uint32_t W, uint32_t H,
                        bool yuv_on, int ky, int ko, unsigned bpp,
                        bool osd_scale, bool osd_armed)
{
    memset(dst, 0, (size_t)W * H * 4);
    vph_opaque = 0;

    /* YUV layer (ch51 panel / ch52 line) */
    if (yuv_on) {
        /*
         * ch51 as programmed for live view: one 8-bit luma plane, +0x24
         * bytes per line, +0x28 bytes skipped after each line, +0x2C
         * lines - 1 (1024 x 516, pitch 0x480); no C plane in the layer
         * descriptor (438F6CF4), shown as grey.  The YUV-layer scaler at
         * +0x300 maps it to the panel size; here nearest-neighbour
         * (docs/RE_LIVEVIEW_IMAGE.md).  hypothesis: full-range luma.
         */
        uint32_t base = R(AIF(ky) + 0x20), lb = R(AIF(ky) + 0x24);
        uint32_t pitch = lb + R(AIF(ky) + 0x28);
        uint32_t lines = R(AIF(ky) + 0x2C) + 1;
        if (base && lb && lb <= 8192 && lines < 4096) {
            g_autofree uint8_t *row = g_malloc(lb);
            for (uint32_t y = 0; y < H; y++) {
                uint32_t sy = (uint64_t)y * lines / H;
                address_space_read(&address_space_memory,
                                   base + (uint64_t)sy * pitch,
                                   MEMTXATTRS_UNSPECIFIED, row, lb);
                for (uint32_t x = 0; x < W; x++) {
                    uint8_t l = row[(uint64_t)x * lb / W];
                    dst[y * W + x] = 0xff000000u | l << 16 | l << 8 | l;
                }
            }
        }
    }
    /* OSD layer (ch53 panel / ch54 line) */
    uint32_t base = R(AIF(ko) + 0x20), lb = R(AIF(ko) + 0x24);
    uint32_t lines = R(AIF(ko) + 0x2C) + 1;
    int32_t pitch = (int16_t)R(AIF(ko) + 0x48);
    if (!pitch) {
        pitch = lb;
    }
    if (osd_armed && base && lb && lb <= 16384 && lines < 4096) {
        g_autofree uint8_t *row = g_malloc(lb);
        uint32_t ow = lb / bpp;
        uint32_t oh = osd_scale ? H : MIN(lines, H);
        uint32_t dw = osd_scale ? W : MIN(ow, W);
        int64_t last = -1;
        for (uint32_t y = 0; y < oh; y++) {
            uint32_t sy = osd_scale ? (uint64_t)y * lines / H : y;
            if (sy != last) {
                address_space_read(&address_space_memory,
                                   base + (int64_t)sy * pitch,
                                   MEMTXATTRS_UNSPECIFIED, row, lb);
                last = sy;
            }
            for (uint32_t dx = 0; dx < dw; dx++) {
                uint32_t x = osd_scale ? (uint64_t)dx * ow / W : dx;
                uint8_t r, g, b, a;
                if (bpp == 4) {
                    r = row[4 * x]; g = row[4 * x + 1]; b = row[4 * x + 2];
                    a = row[4 * x + 3];
                } else {
                    /* RGBA4444, A in the low nibble: the UI renders with
                     * P3D colour format 4 and PPF in/out format 4 into
                     * the panel buffers (docs/RE_GPU_P3D_CMD.md) */
                    uint16_t v = row[2 * x] | row[2 * x + 1] << 8;
                    r = (v >> 12) * 17; g = ((v >> 8) & 15) * 17;
                    b = ((v >> 4) & 15) * 17; a = (v & 15) * 17;
                }
                uint32_t *d = &dst[y * W + dx];
                vph_opaque += a != 0;
                uint8_t dr = *d >> 16, dg = *d >> 8, db = *d;
                *d = 0xff000000u | ((r * a + dr * (255 - a)) / 255) << 16 |
                     ((g * a + dg * (255 - a)) / 255) << 8 |
                     ((b * a + db * (255 - a)) / 255);
            }
        }
    }
}

static void vph_update(void *opaque)
{
    VphState *s = opaque;
    DisplaySurface *surf = qemu_console_surface(s->con);
    uint32_t W = s->w, H = s->h;
    static unsigned empty_run;
    if (!surf || surface_width(surf) != W || surface_height(surf) != H) {
        qemu_console_resize(s->con, W, H);
        surf = qemu_console_surface(s->con);
    }
    g_autofree uint32_t *tmp = g_new(uint32_t, (size_t)W * H);
    if (!(R(0x144) & 3) && R(AIF(3) + 0x20) && s->panel_fallback) {
        /*
         * A7C (v1.11): with the rear panel's layers off (+0x144 = 0) the
         * camera shows its UI on the line output instead (ch54, 704 x 240
         * per field at +0x454, OSD pitch 2 lines at +0x48 = 0x0A00): the
         * viewfinder path, as the a6400's default "device 2" (bionz-x
         * E0572).  Show that OSD on console 0 so the window is not black.
         */
        vph_compose(s, tmp, W, H, R(0x184) & 1, 1, 3,
                    (R(0x414) & 1) ? 4 : 2, true,
                    !s->arm_gate || s->line_visible ||
                    ((s->aif_started >> 3) & 1u));
    } else {
        /*
         * A7C (v1.12): +0x404 bit0 is set while the OSD is RGBA4444 (ch53,
         * 0x500 bytes per line for 640 px): a line of exactly 2 bytes per
         * panel pixel is taken as 16 bpp whatever +0x404 says.
         */
        unsigned bpp = (R(0x404) & 1) ? 4 : 2;
        if (R(AIF(2) + 0x24) == W * 2) {
            bpp = 2;
        }
        bool yuv_on = (R(0x144) & 1) || vph_panel_stage_running(s);
        bool armed = !s->arm_gate ||
                     ((R(0x144) & 2) && (s->osd_visible ||
                                         ((s->aif_started >> 2) & 1u)));
        vph_compose(s, tmp, W, H, yuv_on, 0, 2, bpp, false, armed);
    }
    /*
     * The A7C programs the next OSD buffer into the AIF channel before the
     * UI has drawn it, so every other frame here reads an empty buffer (the
     * real block latches at its own frame start [?]).  Keep the last drawn
     * frame over short empty runs (< 1 s); a screen that stays empty longer
     * is shown.
     */
    if (s->arm_gate || vph_opaque || ++empty_run > 60) {
        if (vph_opaque) {
            empty_run = 0;
        }
        memcpy(surface_data(surf), tmp, (size_t)W * H * 4);
        dpy_gfx_update_full(s->con);
    }
}

/* HDMI: line output, console 1 (docs/RE_HDMI.md §2, §4.2) */
static void vph_hdmi_update(void *opaque)
{
    VphState *s = opaque;
    uint32_t W = R(0x454) & 0xfff, H = (R(0x454) >> 16) & 0xfff;
    unsigned fmt = (R(0x410) >> 16) & 7;
    bool sig = s->hdmi_tmds && W >= 64 && H >= 64;

    if (sig && (fmt == 0 || fmt == 2 || fmt == 3)) {
        H *= 2;                         /* interlaced: two fields */
    }
    if (!sig) {
        W = 640;
        H = 480;
    }
    DisplaySurface *surf = qemu_console_surface(s->hdmi_con);
    if (!surf || surface_width(surf) != W || surface_height(surf) != H) {
        qemu_console_resize(s->hdmi_con, W, H);
        surf = qemu_console_surface(s->hdmi_con);
    }
    uint32_t *dst = surface_data(surf);
    if (!sig) {
        /* no signal: dark blue with a grey frame, like a TV's idle screen */
        for (uint32_t y = 0; y < H; y++) {
            for (uint32_t x = 0; x < W; x++) {
                bool edge = x < 8 || y < 8 || x >= W - 8 || y >= H - 8;
                dst[y * W + x] = edge ? 0xff404040u : 0xff000020u;
            }
        }
    } else {
        vph_compose(s, dst, W, H, R(0x184) & 1, 1, 3,
                    (R(0x414) & 1) ? 4 : 2, true,
                    !s->arm_gate || s->line_visible ||
                    ((s->aif_started >> 3) & 1u));
    }
    dpy_gfx_update_full(s->hdmi_con);
}

static void vph_hdmi_tmds(void *opaque, int n, int level)
{
    VphState *s = opaque;
    s->hdmi_tmds = level;
}

static void vph_invalidate(void *opaque)
{
}

static const GraphicHwOps vph_gfx_ops = {
    .invalidate = vph_invalidate,
    .gfx_update = vph_update,
};

static const GraphicHwOps vph_hdmi_ops = {
    .invalidate = vph_invalidate,
    .gfx_update = vph_hdmi_update,
};

static uint64_t vph_read(void *opaque, hwaddr off, unsigned size)
{
    VphState *s = opaque;
    if (off >= AIF(0) && off < AIF(8) && (off & 0x7f) == 0x04) {
        return R(off) & ~0x80000000u;          /* never "still running" */
    }
    if (off == 0x1004 || off == 0x2004 || off == 0x3004) {
        return 0;                              /* error status */
    }
    return R(off);
}

static void vph_write(void *opaque, hwaddr off, uint64_t v, unsigned size)
{
    VphState *s = opaque;
    if (off == 0x140 && s->stage_sticky) {
        /* write-1-to-set mode field: a guest clear does not stop the stage */
        R(0x140) = (R(0x140) & VPH_140_STICKY) | (uint32_t)v;
        return;
    }
    if (off >= AIF(0) && off < AIF(8) && (off & 0x7f) == 0x00 && (v & 1u)) {
        unsigned k = (unsigned)((off - AIF(0)) / 0x80);
        s->aif_started |= 1u << k;
        s->arms[k]++;
    }
    if (s->debug) {
        static unsigned n144, nbase;
        if (off == 0x144 && (uint32_t)v != R(0x144) && n144 < 64) {
            n144++;
            fprintf(stderr, "[VPH] t=%" PRId64 "ms 0x144 0x%08x -> 0x%08x\n",
                    qemu_clock_get_ms(QEMU_CLOCK_VIRTUAL), R(0x144), (uint32_t)v);
        }
        if (off >= AIF(0) && off < AIF(4) && (off & 0x7f) == 0x20 &&
            (uint32_t)v != R(off) && nbase < 400) {
            nbase++;
            fprintf(stderr, "[VPH] t=%" PRId64 "ms ch%u base 0x%08x -> 0x%08x "
                    "lb=0x%x lines=%u ctrl=0x%x\n",
                    qemu_clock_get_ms(QEMU_CLOCK_VIRTUAL),
                    51 + (unsigned)((off - AIF(0)) / 0x80), R(off), (uint32_t)v,
                    R(off - 0x20 + 0x24), R(off - 0x20 + 0x2C) + 1,
                    R(off - 0x20));
        }
    }
    R(off) = v;
}

/* once per second of guest time: arm counts and layer state */
static void vph_debug_tick(VphState *s)
{
    static unsigned vt;
    if (++vt % 60) {
        return;
    }
    fprintf(stderr, "[VPH] t=%" PRId64 "ms arms/s ch51=%u ch52=%u ch53=%u "
            "ch54=%u 0x140=0x%08x 0x144=0x%08x ch53=0x%08x ch54=0x%08x "
            "sysv=%u\n", qemu_clock_get_ms(QEMU_CLOCK_VIRTUAL),
            s->arms[0], s->arms[1], s->arms[2], s->arms[3], R(0x140),
            R(0x144), R(AIF(2) + 0x20), R(AIF(3) + 0x20), s->frame);
    memset(s->arms, 0, sizeof(s->arms));
}

static uint64_t sysv_read(void *opaque, hwaddr off, unsigned size)
{
    VphState *s = opaque;
    if (off == 0x98) {
        return s->frame;
    }
    if (off == 0x28) {
        /*
         * bit0 = frame phase, follows the frame counter parity.  In its 2V
         * frame mode av-cam's VD_Pre (4376CFDC, phase read by 43AB5486)
         * only hands out module requests on frames with bit0 = 1; a
         * constant 0 stalled the mode sequencer at step 12 whenever the
         * first mode request came after the 2V switch
         * (docs/RE_ENGINE_STABILITY.md §1).  hypothesis: polarity/phase
         * relative to the engine frame event.
         */
        return (s->sysv[off / 4] & ~1u) | (s->frame & 1);
    }
    return s->sysv[off / 4];
}

static void sysv_write(void *opaque, hwaddr off, uint64_t v, unsigned size)
{
    VphState *s = opaque;
    s->sysv[off / 4] = v;
}

static const MemoryRegionOps vph_ops = {
    .read = vph_read, .write = vph_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
};
static const MemoryRegionOps sysv_ops = {
    .read = sysv_read, .write = sysv_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
};

static void vph_reset(DeviceState *dev)
{
    VphState *s = CXD_VPH(dev);
    memset(s->vph, 0, sizeof(s->vph));
    memset(s->sysv, 0, sizeof(s->sysv));
    s->frame = 0;
    s->aif_started = 0;
    s->osd_visible = 0;
    s->line_visible = 0;
    timer_mod(s->tick, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
              NANOSECONDS_PER_SECOND / 60);
    timer_mod(s->vtick, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
              V_US * SCALE_US);
}

static void vph_realize(DeviceState *dev, Error **errp)
{
    VphState *s = CXD_VPH(dev);
    s->w = 640;
    s->h = 480;
    s->con = graphic_console_init(dev, 0, &vph_gfx_ops, s);
    qemu_console_resize(s->con, s->w, s->h);
    s->hdmi_con = graphic_console_init(dev, 1, &vph_hdmi_ops, s);
    qemu_console_resize(s->hdmi_con, 640, 480);
}

static void vph_init(Object *obj)
{
    VphState *s = CXD_VPH(obj);
    SysBusDevice *sbd = SYS_BUS_DEVICE(obj);
    memory_region_init_io(&s->vph_mr, obj, &vph_ops, s, "vph", VPH_SIZE);
    memory_region_init_io(&s->sysv_mr, obj, &sysv_ops, s, "sysv", 0x1000);
    sysbus_init_mmio(sbd, &s->vph_mr);
    sysbus_init_mmio(sbd, &s->sysv_mr);
    sysbus_init_irq(sbd, &s->irq_sysv);
    sysbus_init_irq(sbd, &s->irq_v101);
    sysbus_init_irq(sbd, &s->irq_v102);
    s->tick = timer_new_ns(QEMU_CLOCK_VIRTUAL, vph_tick, s);
    s->lower = timer_new_ns(QEMU_CLOCK_VIRTUAL, vph_lower, s);
    s->vtick = timer_new_ns(QEMU_CLOCK_VIRTUAL, vph_vtick, s);
    s->vlower = timer_new_ns(QEMU_CLOCK_VIRTUAL, vph_vlower, s);
    qdev_init_gpio_out(DEVICE(obj), &s->vd_out, 1);
    qdev_init_gpio_in_named(DEVICE(obj), vph_hdmi_tmds, "hdmi-tmds", 1);
}

static const Property vph_props[] = {
    DEFINE_PROP_BOOL("panel-fallback", VphState, panel_fallback, true),
    DEFINE_PROP_BOOL("arm-gate", VphState, arm_gate, false),
    DEFINE_PROP_BOOL("stage-sticky", VphState, stage_sticky, false),
    DEFINE_PROP_BOOL("debug", VphState, debug, false),
};

static void vph_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    dc->realize = vph_realize;
    device_class_set_props(dc, vph_props);
    device_class_set_legacy_reset(dc, vph_reset);
}

static const TypeInfo vph_info = {
    .name = TYPE_CXD_VPH,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(VphState),
    .instance_init = vph_init,
    .class_init = vph_class_init,
};

static void vph_register(void)
{
    type_register_static(&vph_info);
}
type_init(vph_register)
