/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * CXD90014 / CXD90045 GPIO, 18 ports (gpio_p0..p17) at 0xF2910000 + 0x100*n.
 *
 * Adapted from Oein/a5000-emulator qemu-overlay/hw/gpio/cxd90014_gpio.c
 * (GPL-2.0, Park SungHyun).  Changes for the a6400 (E0526): trace points
 * removed; pin IRQ pulse 50 us instead of 1 ms; the a5000 board-specific reset levels (keys, shutter, lens
 * detect) are replaced by the "reset-high" property ("port.pin,..."), set
 * by the board from the a6400 key table.
 *
 * Register triples (value / set / clear), from the kernel hw_gpio_* code:
 *   +0x00/04/08 direction (1 = output [?])   +0x10 pin input data
 *   +0x20/24/28 input enable                  +0x30/34/38 mode/pull
 *   +0x40/44/48 output latch
 * Input data = output latch on output pins, external level on inputs.
 * External levels come from qdev GPIO inputs (32 per port, port-major).
 * Named GPIO outputs "out" follow the output latch of each pin.
 * Pin interrupts: p0.0..15 -> GIC 121+pin, p0.16 -> 144, p1.0..6 ->
 * 137+pin; pulsed on every edge of the pin.  The a6400 av-cam registers the
 * same lines (liro-122/129/130/133/135/136/139/141/142/143, E0526).
 */
#include "qemu/osdep.h"
#include "qapi/error.h"
#include "hw/core/sysbus.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "qemu/log.h"
#include "qemu/timer.h"
#include "qemu/module.h"
#include "qom/object.h"

#define TYPE_CXD_GPIO "cxd90014-gpio"
OBJECT_DECLARE_SIMPLE_TYPE(GpioState, CXD_GPIO)

#define NPORT 18
#define NIRQ (17 + 7)

struct GpioState {
    SysBusDevice parent_obj;
    MemoryRegion mr;
    uint32_t dir[NPORT], ien[NPORT], mode[NPORT], out[NPORT], ext[NPORT];
    uint32_t misc[NPORT][0x100 / 4];
    qemu_irq irq[NIRQ];
    qemu_irq out_line[NPORT * 32];
    QEMUTimer *drop[NIRQ];
    char *reset_high;
    uint32_t ext_reset[NPORT];
};

static void drop_cb(void *opaque);
typedef struct { struct GpioState *s; int n; } DropArg;
static DropArg drop_args[NIRQ];

static uint32_t pin_in(GpioState *s, int p)
{
    return (s->out[p] & s->dir[p]) | (s->ext[p] & ~s->dir[p]);
}

static uint32_t *triple(GpioState *s, int p, hwaddr o)
{
    switch (o & 0xf0) {
    case 0x00: return &s->dir[p];
    case 0x20: return &s->ien[p];
    case 0x30: return &s->mode[p];
    case 0x40: return &s->out[p];
    }
    return NULL;
}

static void pulse(GpioState *s, int port, int pin)
{
    int n = -1;
    if (port == 0 && pin <= 16) {
        n = pin;                    /* irq[0..16] */
    } else if (port == 1 && pin <= 6) {
        n = 17 + pin;               /* irq[17..23] */
    }
    if (n >= 0) {
        /*
         * Hold the line so a level-configured GIC input sees it.  The a5000
         * model holds 1 ms; at level trigger the a6400 av-cam ISR re-enters
         * for the whole pulse (about 2.7 M IRQ 129 per run), so the a6400
         * port holds 50 us [deviation, E0527: the real GPIO interrupt
         * latch/ack is not modelled].
         */
        qemu_set_irq(s->irq[n], 1);
        timer_mod(s->drop[n], qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                  50 * SCALE_US);
    }
}

static void edges(GpioState *s, int p, uint32_t before)
{
    uint32_t ch = before ^ pin_in(s, p);
    for (int b = 0; ch; b++, ch >>= 1) {
        if (ch & 1) {
            pulse(s, p, b);
        }
    }
}

/* report output latch changes on the "out" lines */
static void drive_edges(GpioState *s, int p, uint32_t before)
{
    uint32_t now = s->out[p];
    uint32_t ch = before ^ now;
    for (int b = 0; ch; b++, ch >>= 1) {
        if (ch & 1) {
            qemu_set_irq(s->out_line[p * 32 + b], (now >> b) & 1);
        }
    }
}

static uint64_t gpio_read(void *opaque, hwaddr off, unsigned size)
{
    GpioState *s = opaque;
    int p = off / 0x100;
    hwaddr o = off % 0x100;
    if (o == 0x10) {
        return pin_in(s, p);
    }
    uint32_t *t = triple(s, p, o);
    if (t && (o & 0xf) == 0) {
        return *t;
    }
    return s->misc[p][o / 4];
}

static void gpio_write(void *opaque, hwaddr off, uint64_t v, unsigned size)
{
    GpioState *s = opaque;
    int p = off / 0x100;
    hwaddr o = off % 0x100;
    uint32_t *t = triple(s, p, o);
    uint32_t before = pin_in(s, p);
    uint32_t drv = s->out[p];
    if (t) {
        switch (o & 0xf) {
        case 0x0: *t = v; break;
        case 0x4: *t |= v; break;
        case 0x8: *t &= ~v; break;
        default: s->misc[p][o / 4] = v; return;
        }
        edges(s, p, before);
        drive_edges(s, p, drv);
        return;
    }
    s->misc[p][o / 4] = v;
}

static void drop_cb(void *opaque)
{
    DropArg *a = opaque;
    qemu_set_irq(a->s->irq[a->n], 0);
}

static void gpio_set_ext(void *opaque, int line, int level)
{
    GpioState *s = opaque;
    int p = line / 32, b = line % 32;
    uint32_t before = pin_in(s, p);
    s->ext[p] = (s->ext[p] & ~(1u << b)) | ((uint32_t)!!level << b);
    edges(s, p, before);
}

static const MemoryRegionOps gpio_ops = {
    .read = gpio_read, .write = gpio_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
};

static void gpio_reset(DeviceState *dev)
{
    GpioState *s = CXD_GPIO(dev);
    memset(s->dir, 0, sizeof(s->dir)); memset(s->ien, 0, sizeof(s->ien));
    memset(s->mode, 0, sizeof(s->mode)); memset(s->out, 0, sizeof(s->out));
    memset(s->misc, 0, sizeof(s->misc));
    memcpy(s->ext, s->ext_reset, sizeof(s->ext));
}

static void gpio_realize(DeviceState *dev, Error **errp)
{
    GpioState *s = CXD_GPIO(dev);
    const char *p = s->reset_high;

    memset(s->ext_reset, 0, sizeof(s->ext_reset));
    while (p && *p) {
        char *end;
        long port = strtol(p, &end, 10);
        long pin;

        if (*end != '.') {
            error_setg(errp, "reset-high: expected port.pin at '%s'", p);
            return;
        }
        pin = strtol(end + 1, &end, 10);
        if (port < 0 || port >= NPORT || pin < 0 || pin > 31) {
            error_setg(errp, "reset-high: %ld.%ld out of range", port, pin);
            return;
        }
        s->ext_reset[port] |= 1u << pin;
        p = *end == ',' ? end + 1 : end;
    }
}

static void gpio_init(Object *obj)
{
    GpioState *s = CXD_GPIO(obj);
    memory_region_init_io(&s->mr, obj, &gpio_ops, s, "cxd-gpio", 0x100 * NPORT);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mr);
    for (int i = 0; i < NIRQ; i++) {
        sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq[i]);
        drop_args[i].s = s;
        drop_args[i].n = i;
        s->drop[i] = timer_new_ns(QEMU_CLOCK_VIRTUAL, drop_cb, &drop_args[i]);
    }
    qdev_init_gpio_in(DEVICE(obj), gpio_set_ext, NPORT * 32);
    qdev_init_gpio_out_named(DEVICE(obj), s->out_line, "out", NPORT * 32);
}

static const Property gpio_props[] = {
    DEFINE_PROP_STRING("reset-high", GpioState, reset_high),
};

static void gpio_class_init(ObjectClass *klass, const void *data)
{
    DEVICE_CLASS(klass)->realize = gpio_realize;
    device_class_set_legacy_reset(DEVICE_CLASS(klass), gpio_reset);
    device_class_set_props(DEVICE_CLASS(klass), gpio_props);
}

static const TypeInfo gpio_info = {
    .name = TYPE_CXD_GPIO,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(GpioState),
    .instance_init = gpio_init,
    .class_init = gpio_class_init,
};

static void gpio_register(void)
{
    type_register_static(&gpio_info);
}
type_init(gpio_register)
