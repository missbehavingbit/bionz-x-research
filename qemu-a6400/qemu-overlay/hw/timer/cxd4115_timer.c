/*
 * Sony CXD4115-style timer block (CXD90014 a5000 / CXD90045 a6400).
 *
 * Adapted from Oein/a5000-emulator qemu-overlay/hw/timer/cxd4115_timer.c
 * (GPL-2.0, Park SungHyun).  Changes for the a6400: trace points removed.
 *
 * The CXD90045 kernel (nflasha3, E0522) uses the same protocol:
 * cxd900x0_timer_early_init writes 0x1031 to ch1 (+0x100) as the 2 MHz
 * clocksource read at +0x10C; per-CPU clockevents use ch0/ch2/ch3/ch4
 * (table 0x604d8d80: base 0xF2403000/200/300/400, IRQ 153 + ch) with
 * CLR = 0x11, periodic CTRL 0x1102 / CMP 10000, one-shot 0x112 and
 * set_next_event CTRL |= 0x1000; hwtimer0..3 are ch5..8 (IRQ 158..161).
 *
 *   base + 0x100*ch
 *     +0x0  CTRL   bit12 START, bit8 RELOAD-on-match, bit4 ONESHOT(with bit8),
 *                  bit1 IRQ enable, bits[1:0] prescaler (4 MHz >> n)
 *     +0x4  CLR    bit0 clear interrupt, bit4 clear counter
 *     +0x8  CMP    compare value
 *     +0xC  COUNT  current count (read)
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/core/sysbus.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "qemu/timer.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qom/object.h"
#include "system/runstate.h"
#include "system/watchdog.h"
#include "qemu/error-report.h"

#define TYPE_CXD4115_TIMER "cxd4115-timer"
OBJECT_DECLARE_SIMPLE_TYPE(CXD4115TimerState, CXD4115_TIMER)

#define CXD_NCH 16
#define CXD_BASE_HZ 4000000ULL

#define CTRL_START  (1u << 12)
#define CTRL_RELOAD (1u << 8)
#define CTRL_ONESHT (1u << 4)
#define CTRL_IRQEN  (1u << 1)
#define CTRL_FREE   (1u << 5)   /* keep counting through a match [hypothesis] */

typedef struct CXDChan {
    struct CXD4115TimerState *s;
    int idx;
    uint32_t ctrl;
    uint32_t cmp;
    uint32_t base_count;   /* count at base_ns */
    int64_t base_ns;
    bool running;
    bool pending;
    QEMUTimer *timer;
    qemu_irq irq;
} CXDChan;

struct CXD4115TimerState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    CXDChan ch[CXD_NCH];
    uint32_t nch;
    bool wdt;          /* watchdog instance: CTRL bit31 = reset on match */
};

static uint64_t chan_hz(CXDChan *c)
{
    return CXD_BASE_HZ >> (c->ctrl & 3);
}

static uint32_t chan_count(CXDChan *c)
{
    if (!c->running) {
        return c->base_count;
    }
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    uint64_t ticks = muldiv64(now - c->base_ns, chan_hz(c),
                              NANOSECONDS_PER_SECOND);
    return c->base_count + (uint32_t)ticks;
}

static void chan_update_irq(CXDChan *c)
{
    qemu_set_irq(c->irq, c->pending && (c->ctrl & CTRL_IRQEN));
}

static void chan_rearm(CXDChan *c)
{
    if (!c->running) {
        timer_del(c->timer);
        return;
    }
    uint32_t now_cnt = chan_count(c);
    uint32_t delta = c->cmp - now_cnt;   /* wraps naturally */
    if (delta == 0) {
        delta = ((c->ctrl & CTRL_RELOAD) && !(c->ctrl & CTRL_FREE)) ?
                c->cmp : 0xffffffffu;
        if (delta == 0) {
            delta = 1;
        }
    }
    int64_t ns = muldiv64(delta, NANOSECONDS_PER_SECOND, chan_hz(c));
    timer_mod(c->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + MAX(ns, 1));
}

static void chan_tick(void *opaque)
{
    CXDChan *c = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    c->pending = true;
    if (c->s->wdt && (c->ctrl & (1u << 31))) {
        /*
         * Always visible (not only under -d guest_errors): an expiry is a
         * fidelity event that must not pass silently.  The action follows
         * QEMU's -action watchdog= policy (default reset, also pause,
         * shutdown, poweroff, debug, none, inject-nmi).
         */
        error_report("cxd4115-wdt: ch%d expired (ctrl=0x%08x cmp=0x%08x)",
                     c->idx, c->ctrl, c->cmp);
        c->running = false;
        watchdog_perform_action();
        return;
    }
    if ((c->ctrl & CTRL_RELOAD) && !(c->ctrl & CTRL_FREE)) {
        c->base_count = 0;
        c->base_ns = now;
        if (c->ctrl & CTRL_ONESHT) {
            /*
             * A one-shot match ends the run and clears START: av-cam's
             * timer setup (439193f4 / 4391944e) refuses a channel whose
             * START reads 1 (-3 "busy") and nothing in its ISR clears it
             * (docs/RE_LENS_EMOUNT.md §9.1: lens handshake ch10, imager
             * power wait ch11).
             */
            c->running = false;
            c->ctrl &= ~CTRL_START;
        }
    }
    chan_update_irq(c);
    chan_rearm(c);
}

static void chan_set_ctrl(CXDChan *c, uint32_t v)
{
    uint32_t cnt = chan_count(c);
    c->ctrl = v;
    c->base_count = cnt;
    c->base_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    c->running = !!(v & CTRL_START);
    chan_update_irq(c);
    chan_rearm(c);
}

static uint64_t cxd_read(void *opaque, hwaddr off, unsigned size)
{
    CXD4115TimerState *s = opaque;
    int n = off >> 8;
    if (n >= s->nch) {
        qemu_log_mask(LOG_UNIMP, "cxd4115: read 0x%" HWADDR_PRIx "\n", off);
        return 0;
    }
    CXDChan *c = &s->ch[n];
    switch (off & 0xff) {
    case 0x0: return c->ctrl;
    case 0x4: return c->pending;
    case 0x8: return c->cmp;
    case 0xc: return chan_count(c);
    default:
        qemu_log_mask(LOG_UNIMP, "cxd4115: read 0x%" HWADDR_PRIx "\n", off);
        return 0;
    }
}

static void cxd_write(void *opaque, hwaddr off, uint64_t v, unsigned size)
{
    CXD4115TimerState *s = opaque;
    int n = off >> 8;
    if (n >= s->nch) {
        qemu_log_mask(LOG_UNIMP, "cxd4115: write 0x%" HWADDR_PRIx
                      " = 0x%" PRIx64 "\n", off, v);
        return;
    }
    CXDChan *c = &s->ch[n];
    switch (off & 0xff) {
    case 0x0:
        chan_set_ctrl(c, v);
        break;
    case 0x4:
        if (v & 1) {
            c->pending = false;
        }
        if (v & 0x10) {
            c->base_count = 0;
            c->base_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
            chan_rearm(c);
        }
        chan_update_irq(c);
        break;
    case 0x8:
        c->cmp = v;
        chan_rearm(c);
        break;
    default:
        qemu_log_mask(LOG_UNIMP, "cxd4115: write 0x%" HWADDR_PRIx
                      " = 0x%" PRIx64 "\n", off, v);
    }
}

static const MemoryRegionOps cxd_ops = {
    .read = cxd_read,
    .write = cxd_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
};

static void cxd_reset(DeviceState *dev)
{
    CXD4115TimerState *s = CXD4115_TIMER(dev);
    for (int i = 0; i < CXD_NCH; i++) {
        CXDChan *c = &s->ch[i];
        timer_del(c->timer);
        c->ctrl = c->cmp = c->base_count = 0;
        c->running = c->pending = false;
        c->base_ns = 0;
    }
}

static void cxd_init(Object *obj)
{
    CXD4115TimerState *s = CXD4115_TIMER(obj);
    memory_region_init_io(&s->iomem, obj, &cxd_ops, s, "cxd4115-timer",
                          0x1000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->iomem);
    for (int i = 0; i < CXD_NCH; i++) {
        s->ch[i].s = s;
        s->ch[i].idx = i;
        s->ch[i].timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, chan_tick, &s->ch[i]);
        sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->ch[i].irq);
    }
}

static const Property cxd_props[] = {
    DEFINE_PROP_UINT32("num-channels", CXD4115TimerState, nch, CXD_NCH),
    DEFINE_PROP_BOOL("watchdog", CXD4115TimerState, wdt, false),
};

static void cxd_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    device_class_set_legacy_reset(dc, cxd_reset);
    device_class_set_props(dc, cxd_props);
}

static const TypeInfo cxd_info = {
    .name = TYPE_CXD4115_TIMER,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(CXD4115TimerState),
    .instance_init = cxd_init,
    .class_init = cxd_class_init,
};

static void cxd_register(void)
{
    type_register_static(&cxd_info);
}
type_init(cxd_register)
