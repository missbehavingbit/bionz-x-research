/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Adapted for sony-a6400 from Oein/a5000-emulator hw/misc/cxd90014_p2s.c
 * (GPL-2.0, Park SungHyun).  Trace events are the first few stderr lines.
 * The a6400 av-cam.bin contains the same unit-0 bases 0xF3C00300,
 * 0xF3C00800 and 0xF3C00900.
 */

/*
 * CXD90014 P2S ("parallel-to-serial") sensor register writer, unit 0 (a5000).
 *
 * PA 0xF3C00300..0xF3C009FF (the driver's unit table av-cam dword_43B1DC5C
 * = {0xF3C00300, 0xF3C00800, 0xF3C00900}), GIC IRQ 38.  The device covers
 * the whole page 0xF3C00000..0xF3C00FFF: QEMU's TLB cannot map an MMIO
 * region whose start is not page aligned across a page (the in-page offset
 * collides with the section index), and the guest page size is 1 KiB here.
 * 0xF3C00000..0x2FF and 0xA00..0xFFF read back as written (-d unimp).
 * The IMX156 imager is configured only through this write-only serialiser
 * (docs/RE_SENSOR_ISP.md §1.3).  Register use by av-cam.bin 1.10:
 *
 *   0x300 bit0 set at config; 0x304 bit0, 0x308 bits0-2 = sync config;
 *   0x30C low byte = byte count/flags (436AE62C/436AE684)
 *   0x340 + 4*i payload, one byte per word (436AE6A4)
 *   0x800 mode bits0-1, 0x808 kick toggle 1,0,1 (436AE62C/436AE734)
 *   0x904 bit0 transfer-done status; written |=1 before every start and by
 *         the IRQ 38 handler as ack -> write-1-to-clear (436AE758,
 *         436AE784 via ISR 436AE3EC)
 *   0x908 bit0 start / busy: the driver sets it to start a transfer and
 *         refuses a new one while it reads 1 (43A49854, 436AE7A8)
 *
 * Model: a start (0x908 bit0 written 1) completes after a short serial
 * transfer time; then 0x908 bit0 clears, 0x904 bit0 sets and IRQ 38 is
 * raised until 0x904 bit0 is written 1.  The transfer time is a
 * hypothesis: the driver waits at most 1-2 ms (436AE4EC / 436AE458).
 * Everything else reads back as written; accesses outside the registers
 * named above are logged under -d unimp.
 */

#include "qemu/osdep.h"
#include "hw/core/sysbus.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "qemu/timer.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qom/object.h"

#define TYPE_CXD_P2S "cxd90014-p2s"
OBJECT_DECLARE_SIMPLE_TYPE(P2SState, CXD_P2S)

#define P2S_BASE 0xF3C00000
#define P2S_SIZE 0x1000
#define O(pa) ((pa) - P2S_BASE)
#define XFER_US 50 /* hypothesis: < the 1 ms driver wait */
#define P2S_TRACE_LIMIT 64
#define P2S_DATA_START 0x340
#define P2S_DATA_END 0x500
#define P2S_DATA_WORDS ((P2S_DATA_END - P2S_DATA_START) / 4)

struct P2SState {
    SysBusDevice parent_obj;
    MemoryRegion mr;
    uint32_t r[P2S_SIZE / 4];
    uint32_t status;            /* 0x904 */
    bool busy;                  /* 0x908 bit0 */
    QEMUTimer *done;
    qemu_irq irq;
    bool trace_transfers;
    unsigned trace_count;
    unsigned traced_transfer;
};

#define R(o) (s->r[(o) / 4])

static bool p2s_known(hwaddr off)
{
    hwaddr pa = off + P2S_BASE;
    return (pa >= 0xF3C00300 && pa <= 0xF3C0030C) ||
           (off >= P2S_DATA_START && off < P2S_DATA_END) ||
           pa == 0xF3C00800 || pa == 0xF3C00808 ||
           pa == 0xF3C00904 || pa == 0xF3C00908;
}

static void p2s_update(P2SState *s)
{
    qemu_set_irq(s->irq, s->status & 1);
}

static void p2s_done(void *opaque)
{
    P2SState *s = opaque;
    static int left = 4;

    s->busy = false;
    s->status |= 1;
    if (s->traced_transfer) {
        fprintf(stderr, "[P2S trace] done id=%u status=0x%08x\n",
                s->traced_transfer, s->status);
        s->traced_transfer = 0;
    }
    if (left > 0) {
        left--;
        fprintf(stderr, "[P2S] done\n");
    }
    p2s_update(s);
}

static void p2s_start(P2SState *s)
{
    /* payload words 0x340.. hold one byte each; log the first 8 starts */
    static int left = 8;
    uint32_t b[16];

    for (int i = 0; i < 16; i++) {
        b[i] = R(O(0xF3C00340) + 4 * i) & 0xff;
    }
    if (left > 0) {
        left--;
        fprintf(stderr, "[P2S] start n=0x%02x %08x %08x %08x %08x\n",
                R(O(0xF3C0030C)) & 0xff,
                b[0] << 24 | b[1] << 16 | b[2] << 8 | b[3],
                b[4] << 24 | b[5] << 16 | b[6] << 8 | b[7],
                b[8] << 24 | b[9] << 16 | b[10] << 8 | b[11],
                b[12] << 24 | b[13] << 16 | b[14] << 8 | b[15]);
    }
    s->busy = true;
    if (s->trace_transfers && s->trace_count < P2S_TRACE_LIMIT) {
        char slots[2 * P2S_DATA_WORDS + 1];
        static const char hex[] = "0123456789abcdef";
        unsigned pos = 0;

        for (unsigned off = P2S_DATA_START; off < P2S_DATA_END; off += 4) {
            uint32_t byte = R(off) & 0xff;
            slots[pos++] = hex[byte >> 4];
            slots[pos++] = hex[byte & 0xf];
        }
        slots[pos] = '\0';
        s->traced_transfer = ++s->trace_count;
        fprintf(stderr, "[P2S trace] start id=%u config=0x%08x "
                "mode=0x%08x slots=%s\n",
                s->traced_transfer, R(0x30c), R(0x800), slots);
        if (s->trace_count == P2S_TRACE_LIMIT) {
            fprintf(stderr, "[P2S trace] transfer budget reached\n");
        }
    }
    timer_mod(s->done, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
              XFER_US * SCALE_US);
}

static uint64_t p2s_read(void *opaque, hwaddr off, unsigned size)
{
    P2SState *s = opaque;
    uint32_t v;

    switch (off + P2S_BASE) {
    case 0xF3C00904:
        v = s->status;
        break;
    case 0xF3C00908:
        v = (R(off) & ~1u) | s->busy;
        break;
    default:
        v = R(off);
        break;
    }
    if (!p2s_known(off)) {
        qemu_log_mask(LOG_UNIMP, "cxd90014-p2s: rd 0x%08" HWADDR_PRIx
                      " -> 0x%x\n", off + P2S_BASE, v);
    }
    return v;
}

static void p2s_write(void *opaque, hwaddr off, uint64_t v, unsigned size)
{
    P2SState *s = opaque;

    switch (off + P2S_BASE) {
    case 0xF3C00904:
        s->status &= ~v;
        p2s_update(s);
        return;
    case 0xF3C00908:
        R(off) = v & ~1u;
        if ((v & 1) && !s->busy) {
            p2s_start(s);
        }
        return;
    }
    if (!p2s_known(off)) {
        qemu_log_mask(LOG_UNIMP, "cxd90014-p2s: wr 0x%08" HWADDR_PRIx
                      " = 0x%" PRIx64 "\n", off + P2S_BASE, v);
    }
    R(off) = v;
}

static const MemoryRegionOps p2s_ops = {
    .read = p2s_read, .write = p2s_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
};

static void p2s_reset(DeviceState *dev)
{
    P2SState *s = CXD_P2S(dev);
    memset(s->r, 0, sizeof(s->r));
    s->status = 0;
    s->busy = false;
    timer_del(s->done);
    if (s->traced_transfer) {
        fprintf(stderr, "[P2S trace] reset id=%u\n", s->traced_transfer);
        s->traced_transfer = 0;
    }
    p2s_update(s);
}

static void p2s_init(Object *obj)
{
    P2SState *s = CXD_P2S(obj);
    memory_region_init_io(&s->mr, obj, &p2s_ops, s, "p2s", P2S_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mr);
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq);
    s->done = timer_new_ns(QEMU_CLOCK_VIRTUAL, p2s_done, s);
}

static const Property p2s_props[] = {
    DEFINE_PROP_BOOL("trace-transfers", P2SState, trace_transfers, false),
};

static void p2s_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    device_class_set_props(dc, p2s_props);
    device_class_set_legacy_reset(dc, p2s_reset);
}

static const TypeInfo p2s_info = {
    .name = TYPE_CXD_P2S,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(P2SState),
    .instance_init = p2s_init,
    .class_init = p2s_class_init,
};

static void p2s_register(void)
{
    type_register_static(&p2s_info);
}
type_init(p2s_register)
