/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * CXD90045 TPU / jog-dial counter block at 0xF2404000.
 *
 * E0573 identifies this page as the timer-pulse unit the rear jog dial uses:
 * the firmware's JogTpu accessor reads the 16-bit counter at
 * 0xF2404014 + (channel << 8) with ldrh.  The av-cam driver (PA 0x035C6000,
 * VA base 0x635C6000) has three channels at 0x100 stride and a block of
 * 16-bit registers at 4-byte stride.  The rules below come from the accessor
 * code; every PC cites the captured bytes (loop/scratch/L0038/rules.txt):
 *
 *  - A (0x63a6db78) writes a u16[3] to +0x00/+0x04/+0x08 and reads back
 *    +0x08 (0x63a6db84/0x63a6db8a/0x63a6db90/0x63a6db92).
 *  - C (0x63a6dbc0) writes one of four compare registers selected by an
 *    argument and reads it back: +0x18 (0x63a6dbd8), +0x1c (0x63a6dbe4),
 *    +0x20 (0x63a6dbf0), +0x24 (0x63a6dc04).
 *  - W0C (0x63a6dce0) writes +0x0c and reads it back (0x63a6dcec/0x63a6dcee).
 *  - W28 (0x63a6dd08) reads the status word +0x10 (0x63a6dd14), writes +0x28
 *    (0x63a6dd1e) and reads it back (0x63a6dd22).
 *  - E (0x63a6dc28) writes +0x30/+0x34 and reads both back
 *    (0x63a6dc3c/0x63a6dc3e/0x63a6dc42/0x63a6dc44); not called in this boot.
 *  - CNT (0x63a6dc5c) reads the counter +0x14 (0x63a6dc62); the JogTpu caller
 *    is 0x63c2735b.
 *  - START (0x63a6dc80) clears the counter (0x63a6dc92) and sets +0x2c bit0
 *    with a read-modify-write (0x63a6dc94/0x63a6dc9a).  STAT (0x63a6dc6c)
 *    returns +0x2c bit0 (0x63a6dc72/0x63a6dc74) and the driver requires it
 *    to be 0 before starting a channel.  STOP (0x63a6dcb4) clears it
 *    (0x63a6dcc0/0x63a6dcc4).
 *
 * Rules:
 *  R1 store: every write is kept; a defined register (RF) reads back the last
 *     write.  There is no pulse source, so the counter holds the last value
 *     the guest wrote (START writes 0).  The opt-in rear-wheel region at
 *     0xF2404014 (priority -500) overlays these 2 bytes and supplies host
 *     rotation when rear controls are enabled; this device is at -600.
 *  R2 +0x2c is the counter-run control/status: bit0 is 1 between a START
 *     write (read, OR 1, write) and a STOP write (read, clear bit0, write).
 *     Storing the driver's writes reproduces that RMW exactly; all other bits
 *     stay 0, as written.
 *  R3 +0x10 is a status word the driver never defines (W28 logs it, GET10
 *     reads bit7 at 0x63a6dd42/0x63a6dd44).  Its idle value is not defined by
 *     code, so it reads 0 and is logged as unmodelled, exactly like the board
 *     catch-all.
 *  R4 every other offset (channels >= 3, other register numbers) is logged in
 *     the board's catch-all format with offset + 0xF2404000 - 0xF0000000, so
 *     an unlisted register still counts as unmodelled.  Writes are stored;
 *     reads return 0.
 *  R5 the off-by-default "trace" property logs every read and write.
 *
 * No IRQ is connected: the driver polls +0x2c bit0 and +0x10 bit7, and no
 * completion interrupt condition is named in the accessor code.
 */

#include "qemu/osdep.h"
#include "hw/core/sysbus.h"
#include "hw/core/qdev-properties.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qom/object.h"

#define TYPE_CXD90045_TPU "cxd90045-tpu"
OBJECT_DECLARE_SIMPLE_TYPE(Cxd90045TpuState, CXD90045_TPU)

#define TPU_PAGE_BASE       0xF2404000ULL
#define TPU_PAGE_SIZE       0x1000
#define TPU_CHANNELS        3
#define TPU_CH_STRIDE       0x100
/* Same base as the board catch-all: log offset = physical - 0xF0000000. */
#define TPU_UNMODELLED_BASE 0xF0000000ULL

/*
 * Per-channel register offsets.  All are 16-bit; the register slot of a byte
 * access is the byte offset rounded down to an even number.
 */
enum {
    TPU_CFG0   = 0x00,  /* A: u16[0] (write 0x63a6db84) */
    TPU_CFG1   = 0x04,  /* A: u16[1] (write 0x63a6db8a) */
    TPU_CFG2   = 0x08,  /* A: u16[2] (write 0x63a6db90, read 0x63a6db92) */
    TPU_CFG3   = 0x0c,  /* W0C (write 0x63a6dcec, read 0x63a6dcee) */
    TPU_STATUS = 0x10,  /* status word, undefined idle (read 0x63a6dd14) */
    TPU_COUNT  = 0x14,  /* counter (read 0x63a6dc62) */
    TPU_CMP0   = 0x18,  /* C sel0 (write 0x63a6dbd8, read 0x63a6dbda) */
    TPU_CMP1   = 0x1c,  /* C sel1 (write 0x63a6dbe4, read 0x63a6dbe6) */
    TPU_CMP2   = 0x20,  /* C sel2 (write 0x63a6dbf0, read 0x63a6dbf2) */
    TPU_CMP3   = 0x24,  /* C sel3 (write 0x63a6dc04, read 0x63a6dc06) */
    TPU_CFG4   = 0x28,  /* W28 (write 0x63a6dd1e, read 0x63a6dd22) */
    TPU_RUN    = 0x2c,  /* control/status bit0 (0x63a6dc72/94/9a, dcc0/c4) */
    TPU_WIDE_L = 0x30,  /* E low half (write 0x63a6dc3c) */
    TPU_WIDE_H = 0x34,  /* E high half (write 0x63a6dc3e) */
};

struct Cxd90045TpuState {
    SysBusDevice parent_obj;
    MemoryRegion mr;
    uint8_t reg[TPU_PAGE_SIZE];
    uint8_t wmask[TPU_PAGE_SIZE];
    bool trace;
};

/* Registers that keep and read back the guest's write (rules R1/R2). */
static bool tpu_rf_reg(unsigned r)
{
    switch (r) {
    case TPU_CFG0:
    case TPU_CFG1:
    case TPU_CFG2:
    case TPU_CFG3:
    case TPU_COUNT:
    case TPU_CMP0:
    case TPU_CMP1:
    case TPU_CMP2:
    case TPU_CMP3:
    case TPU_CFG4:
    case TPU_RUN:
    case TPU_WIDE_L:
    case TPU_WIDE_H:
        return true;
    default:
        return false;
    }
}

/*
 * True when every 16-bit register slot touched by [off, off+size) is an RF
 * register in one of the three modelled channels.  +0x10 and any unlisted
 * slot fail, so R3/R4 log them.
 */
static bool tpu_rf_access(hwaddr off, unsigned size)
{
    hwaddr first, last, o;
    unsigned ch;

    if (size == 0 || size > 4) {
        return false;
    }
    ch = off / TPU_CH_STRIDE;
    if (ch >= TPU_CHANNELS) {
        return false;
    }
    if (off + size > (hwaddr)(ch + 1) * TPU_CH_STRIDE) {
        return false;
    }
    first = off & ~(hwaddr)1;
    last = (off + size - 1) & ~(hwaddr)1;
    for (o = first; o <= last; o += 2) {
        if (!tpu_rf_reg(o & 0xff)) {
            return false;
        }
    }
    return true;
}

static void tpu_log_unmodelled(hwaddr off, bool write, unsigned size,
                               uint64_t v)
{
    uint64_t pa = TPU_PAGE_BASE + off - TPU_UNMODELLED_BASE;

    if (write) {
        qemu_log_mask(LOG_UNIMP, "cxd90045.tpu.unmodelled: unimplemented "
                      "device write (size %u, offset 0x%" PRIx64
                      ", value 0x%" PRIx64 ")\n", size, pa, v);
    } else {
        qemu_log_mask(LOG_UNIMP, "cxd90045.tpu.unmodelled: unimplemented "
                      "device read (size %u, offset 0x%" PRIx64 ")\n",
                      size, pa);
    }
}

static uint64_t tpu_store_read(Cxd90045TpuState *s, hwaddr off, unsigned size)
{
    uint64_t v = 0;
    unsigned i;

    for (i = 0; i < size; i++) {
        v |= (uint64_t)s->reg[off + i] << (8 * i);
    }
    return v;
}

static void tpu_store_write(Cxd90045TpuState *s, hwaddr off, uint64_t v,
                            unsigned size)
{
    unsigned i;

    for (i = 0; i < size; i++) {
        s->reg[off + i] = (v >> (8 * i)) & 0xff;
        s->wmask[off + i] = 1;
    }
}

static uint64_t tpu_read(void *opaque, hwaddr off, unsigned size)
{
    Cxd90045TpuState *s = opaque;
    uint64_t v;

    if (tpu_rf_access(off, size)) {
        /* R1/R2: stored config, counter, and the run bit read back. */
        v = tpu_store_read(s, off, size);
    } else {
        /* R3/R4: undefined status or unlisted register: log, read 0. */
        tpu_log_unmodelled(off, false, size, 0);
        v = 0;
    }

    if (s->trace) {
        qemu_log("tpu-trace: read off 0x%03x size %u value 0x%08x\n",
                 (unsigned)off, size, (uint32_t)v);
    }
    return v;
}

static void tpu_write(void *opaque, hwaddr off, uint64_t v, unsigned size)
{
    Cxd90045TpuState *s = opaque;

    tpu_store_write(s, off, v, size);
    if (!tpu_rf_access(off, size)) {
        tpu_log_unmodelled(off, true, size, v);
    }
    if (s->trace) {
        qemu_log("tpu-trace: write off 0x%03x size %u value 0x%08x\n",
                 (unsigned)off, size, (uint32_t)v);
    }
}

static const MemoryRegionOps tpu_ops = {
    .read = tpu_read,
    .write = tpu_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static void tpu_reset(DeviceState *dev)
{
    Cxd90045TpuState *s = CXD90045_TPU(dev);

    memset(s->reg, 0, sizeof(s->reg));
    memset(s->wmask, 0, sizeof(s->wmask));
}

static void tpu_realize(DeviceState *dev, Error **errp)
{
    Cxd90045TpuState *s = CXD90045_TPU(dev);

    memory_region_init_io(&s->mr, OBJECT(dev), &tpu_ops, s,
                          "cxd90045-tpu", TPU_PAGE_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->mr);
}

static const Property tpu_props[] = {
    DEFINE_PROP_BOOL("trace", Cxd90045TpuState, trace, false),
};

static void tpu_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = tpu_realize;
    device_class_set_legacy_reset(dc, tpu_reset);
    device_class_set_props(dc, tpu_props);
}

static const TypeInfo tpu_info = {
    .name = TYPE_CXD90045_TPU,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Cxd90045TpuState),
    .class_init = tpu_class_init,
};

static void tpu_register(void)
{
    type_register_static(&tpu_info);
}
type_init(tpu_register)
