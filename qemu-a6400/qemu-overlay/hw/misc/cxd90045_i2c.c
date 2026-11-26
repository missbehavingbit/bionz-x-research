/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * CXD90045 I2C controller at 0xF2401000 (channel 0 of the i2c-sony driver).
 *
 * The firmware driver is /kmod/i2c.ko, source string
 * ".../Etools-I2C_171H/driver/i2c-sony.c", module text base 0x5f091000 at
 * runtime (E0525: channels at 0xF2401000/0xF2402000, IRQ 199/200).  This
 * model implements only the driver-defined start/config handshake and the
 * message-setup writes; the addressed-slave phase is deliberately not
 * answered.  Register map and ordering evidence:
 * loop/scratch/L0035/{semantics.txt,driver.txt}, loop/scratch/L0035/redteam/
 * {regmap.out,ordering.out} and loop/findings/F-0035.md.  The rules are
 * written out with driver PCs in loop/scratch/L0036/rules.txt.
 *
 * Region-relative offsets are the last digits of the brief's page-relative
 * names (+0x101c = region offset 0x1c, +0x1038 = 0x38, ...), because the
 * device maps the 4 KiB page at 0xF2401000.
 *
 * Rules (each cites the driver instruction, runtime PC = 0x5f091000 +
 * module offset):
 *
 *  R1 store: every write is kept and a read of a written offset returns the
 *     last write unless R2-R4 override it.
 *  R2 +0x101c command/status: bit 15 (busy) reads 0.  After the driver
 *     writes 1 (start, module 0x1bc4 / 0x5f092bc4) bit 0 reads 1 until it
 *     writes 0 (stop, module 0x17c0 / 0x5f0927c0).  The driver polls bit15
 *     at 0x5f092304 and requires bit0 at 0x5f0928a8.
 *  R3 +0x1038 transfer status: reads 3 only while started, after the
 *     driver's config writes, and before the message phase: +0x102c = 1
 *     (0x5f09291c), +0x1010 (0x5f092978), +0x1014 (0x5f092990).  +0x1034
 *     = 1 (0x5f092a2c/0x5f092a5c) is written after the `cmp #3` at module
 *     0x19d4 (ordering.out: cmp/beq -> 0x1a00, message setup only at
 *     0x1a90), so it is not part of the condition.  Once the message phase
 *     starts (write to +0x1004 at 0x5f0923c4 or +0x1024 at 0x5f09250c)
 *     the abort helper's read (module 0x11b0, 0x5f0921b0) must see 0, not
 *     a stale 3 from the pre-message handshake (cmp 0x5f0929d4).  Reads 0
 *     before the config set, after the message phase starts, and after a
 *     stop.
 *  R4 message phase +0x1004 (address, 0x5f0923c4), +0x1008 (length,
 *     0x5f092588), +0x100c (TX FIFO, 0x5f092550), +0x1024 (message start,
 *     0x5f09250c), +0x1028 (IRQ status/enable, 0x5f09242c): stored and
 *     logged, but nothing completes - no IRQ, no status change, exactly the
 *     baseline behaviour of that phase.  One summary line per transfer is
 *     emitted at the address write (address byte, length field, TX bytes).
 *  R5 any offset outside the driver-used set: writes are logged as
 *     cxd90045.i2c.unmodelled (LOG_UNIMP) and still stored; reads without a
 *     prior write and without an R2-R4 rule are logged the same way and
 *     return 0.  This is the same format and offset base (0xF0000000) as
 *     the board's cxd90045.unmodelled catch-all.
 *  R6 the off-by-default "trace" property logs every read and write.
 *
 * IRQ 199 is intentionally not connected: the model stops at the message
 * phase, so the completion interrupt is never raised (brief rule 4).
 */

#include "qemu/osdep.h"
#include "hw/core/sysbus.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "hw/misc/cxd90045_i2c.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qom/object.h"

OBJECT_DECLARE_SIMPLE_TYPE(Cxd90045I2cState, CXD90045_I2C)

#define I2C_PAGE_BASE   0xF2401000ULL
#define I2C_REG_SIZE    0x1000
#define I2C_MAX_SLAVES  128
/* Same base as the board catch-all: log offset = physical - 0xF0000000. */
#define I2C_UNMODELLED_BASE 0xF0000000ULL

enum {
    I2C_STATUS = 0x00,  /* +0x1000 status/error, bit5 bus error (read 0x5f0925c4) */
    I2C_ADDR   = 0x04,  /* +0x1004 slave address (write 0x5f0923c4) */
    I2C_LEN    = 0x08,  /* +0x1008 length/index (write 0x5f092588) */
    I2C_FIFO   = 0x0c,  /* +0x100c TX/RX FIFO (write 0x5f092550) */
    I2C_T10    = 0x10,  /* +0x1010 timing/config (write 0x5f092978) */
    I2C_T14    = 0x14,  /* +0x1014 timing/config (write 0x5f092990) */
    I2C_CMD    = 0x1c,  /* +0x101c command/status (start 0x5f092bc4) */
    I2C_ABORT  = 0x20,  /* +0x1020 abort/reset (write 0x5f09220c) */
    I2C_MSG    = 0x24,  /* +0x1024 message start (write 0x5f09250c) */
    I2C_IRQ    = 0x28,  /* +0x1028 IRQ status/enable (write 0x5f09242c) */
    I2C_AUX    = 0x2c,  /* +0x102c start/stop auxiliary (write 0x5f09291c) */
    I2C_CFG    = 0x34,  /* +0x1034 abort/completion flag (write 0x5f092a2c) */
    I2C_XFER   = 0x38,  /* +0x1038 transfer status (read 0x5f0921b0/0x5f0929d4) */
};

typedef struct CxdI2cSlaveEntry {
    bool present;
    CxdI2cSlaveWriteFn write_fn;
    CxdI2cSlaveReadFn read_fn;
    void *opaque;
} CxdI2cSlaveEntry;

struct Cxd90045I2cState {
    SysBusDevice parent_obj;
    MemoryRegion mr;
    qemu_irq irq;
    uint64_t page_base;
    uint64_t reg_size;
    uint8_t reg[I2C_REG_SIZE];
    uint8_t wmask[I2C_REG_SIZE];   /* byte was written by the guest */
    bool started;                  /* +0x101c start accepted (bit 0 reads 1) */
    bool cfg_aux;                  /* +0x102c written 1 after this start */
    bool cfg_t10;                  /* +0x1010 written after this start */
    bool cfg_t14;                  /* +0x1014 written after this start */
    bool msg_active;               /* between +0x1024 and the address write */
    bool msg_phase;                /* +0x1038 no longer reads 3 once set */
    uint8_t tx[16];
    unsigned tx_count;
    uint8_t rx[16];
    unsigned rx_pos;
    unsigned rx_len;
    uint16_t len_field;
    uint16_t addr;
    bool trace;
    bool complete_transfers;       /* opt-in slave ACK/NACK + IRQ completion */
    bool irq_pending;
    bool nack_err;
    uint8_t xfer_done_nibble;
    bool have_xfer_done;
    uint8_t cur_addr7;
    CxdI2cSlaveEntry slaves[I2C_MAX_SLAVES];
};

void cxd90045_i2c_register_slave(DeviceState *dev, uint8_t addr7,
                                 CxdI2cSlaveWriteFn write_fn,
                                 CxdI2cSlaveReadFn read_fn,
                                 void *opaque)
{
    Cxd90045I2cState *s = CXD90045_I2C(dev);

    if (addr7 < I2C_MAX_SLAVES) {
        s->slaves[addr7].present = true;
        s->slaves[addr7].write_fn = write_fn;
        s->slaves[addr7].read_fn = read_fn;
        s->slaves[addr7].opaque = opaque;
    }
}

/* True for every offset the i2c-sony driver uses (see rules.txt). */
static bool i2c_driver_offset(hwaddr off)
{
    switch (off) {
    case I2C_STATUS:
    case I2C_ADDR:
    case I2C_LEN:
    case I2C_FIFO:
    case I2C_T10:
    case I2C_T14:
    case I2C_CMD:
    case I2C_ABORT:
    case I2C_MSG:
    case I2C_IRQ:
    case I2C_AUX:
    case I2C_CFG:
    case I2C_XFER:
        return true;
    default:
        return false;
    }
}

/* Offsets of brief rule 4: stored/logged, never completed. */
static bool i2c_message_offset(hwaddr off)
{
    return off == I2C_ADDR || off == I2C_LEN || off == I2C_FIFO ||
           off == I2C_MSG || off == I2C_IRQ;
}

static void i2c_log_unmodelled(Cxd90045I2cState *s, hwaddr off, bool write,
                               unsigned size, uint64_t v)
{
    uint64_t pa = s->page_base + off - I2C_UNMODELLED_BASE;

    if (write) {
        qemu_log_mask(LOG_UNIMP, "cxd90045.i2c.unmodelled: unimplemented "
                      "device write (size %u, offset 0x%" PRIx64
                      ", value 0x%" PRIx64 ")\n", size, pa, v);
    } else {
        qemu_log_mask(LOG_UNIMP, "cxd90045.i2c.unmodelled: unimplemented "
                      "device read (size %u, offset 0x%" PRIx64 ")\n",
                      size, pa);
    }
}

static uint64_t i2c_store_read(Cxd90045I2cState *s, hwaddr off, unsigned size)
{
    uint64_t v = 0;
    unsigned i;

    for (i = 0; i < size; i++) {
        v |= (uint64_t)s->reg[off + i] << (8 * i);
    }
    return v;
}

static bool i2c_any_written(Cxd90045I2cState *s, hwaddr off, unsigned size)
{
    unsigned i;

    for (i = 0; i < size; i++) {
        if (s->wmask[off + i]) {
            return true;
        }
    }
    return false;
}

static uint64_t i2c_read(void *opaque, hwaddr off, unsigned size)
{
    Cxd90045I2cState *s = opaque;
    uint64_t v;

    if (off == I2C_CMD) {
        /* R2: busy bit 15 reads 0; bit 0 follows the start/stop writes. */
        v = i2c_store_read(s, off, size);
        v &= ~0x8000ULL;
        if (s->started) {
            v |= 1ULL;
        } else {
            v &= ~1ULL;
        }
    } else if (off == I2C_XFER) {
        /* R3: 3 = start/config done, only before the message phase starts. */
        bool done = s->started && s->cfg_aux && s->cfg_t10 && s->cfg_t14 &&
                    !s->msg_phase;
        v = i2c_store_read(s, off, size);
        v = (v & ~0xffffULL) | (done ? 3ULL : 0ULL);
    } else if (s->complete_transfers && off == I2C_STATUS) {
        v = i2c_store_read(s, off, size) & ~0x20ULL;
        if (s->nack_err) {
            v |= 0x20ULL;
        }
    } else if (s->complete_transfers && off == I2C_LEN) {
        v = i2c_store_read(s, off, size);
        if (s->have_xfer_done) {
            v = (v & ~0xf0ULL) | ((uint64_t)(s->xfer_done_nibble & 0xf) << 4);
        }
    } else if (s->complete_transfers && off == I2C_FIFO) {
        v = (s->rx_pos < s->rx_len) ? s->rx[s->rx_pos++] : 0;
    } else if (s->complete_transfers && off == I2C_IRQ) {
        v = i2c_store_read(s, off, size) & ~1ULL;
        if (s->irq_pending) {
            v |= 1ULL;
        }
    } else {
        if (!i2c_any_written(s, off, size) &&
            !i2c_message_offset(off)) {
            /* No R2-R4 rule and never written: R5 unmodelled read. */
            i2c_log_unmodelled(s, off, false, size, 0);
        }
        v = i2c_store_read(s, off, size);
    }

    if (s->trace) {
        qemu_log("i2c-trace: read off 0x%03x size %u value 0x%08x\n",
                 (unsigned)off, size, (uint32_t)v);
    }
    return v;
}

static void i2c_log_transfer(Cxd90045I2cState *s)
{
    char tx[3 * ARRAY_SIZE(s->tx) + 1];
    unsigned i, p = 0;

    for (i = 0; i < s->tx_count; i++) {
        p += snprintf(tx + p, sizeof(tx) - p, "%02x ", s->tx[i]);
    }
    if (p == 0) {
        tx[p++] = '-';
        tx[p] = '\0';
    }
    qemu_log("i2c-transfer: addr 0x%02x len 0x%04x tx[%u] %s\n",
             (unsigned)(s->addr & 0xff), (unsigned)s->len_field,
             s->tx_count, tx);
}

static void i2c_complete_message(Cxd90045I2cState *s, uint16_t cmd)
{
    /*
     * i2c.ko message-phase completion (i2c.ko +0x0d44, +0x1380, +0x15ac):
     *   - TX/address phase (!(cmd & 0x08)): tx[0] holds (addr7 << 1) | rw,
     *     followed by optional payload bytes tx[1..tx_count-1].
     *   - RX data phase ((cmd & 0x08) != 0): reads (len_field & 0x0f) + 1
     *     bytes from the slave selected in the preceding address phase.
     *   - Completion sets I2C_LEN[7:4] = transferred - 1 and raises I2C_IRQ
     *     bit 0 (write-1-to-clear); missing slaves set I2C_STATUS bit 5.
     */
    if (cmd & 0x08) {
        unsigned want = (s->len_field & 0x0f) + 1;
        if (want > ARRAY_SIZE(s->rx)) {
            want = ARRAY_SIZE(s->rx);
        }
        memset(s->rx, 0, sizeof(s->rx));
        s->rx_pos = 0;
        s->rx_len = want;
        if (s->cur_addr7 < I2C_MAX_SLAVES && s->slaves[s->cur_addr7].present) {
            s->nack_err = false;
            if (s->slaves[s->cur_addr7].read_fn) {
                s->slaves[s->cur_addr7].read_fn(
                    s->slaves[s->cur_addr7].opaque, s->rx, want);
            }
            s->xfer_done_nibble = (want - 1) & 0x0f;
            s->have_xfer_done = true;
        } else {
            s->nack_err = true;
            s->have_xfer_done = false;
        }
    } else if (s->tx_count >= 1) {
        uint8_t addr7 = s->tx[0] >> 1;
        bool is_rd = (s->tx[0] & 1) != 0;

        s->cur_addr7 = addr7;
        if (addr7 < I2C_MAX_SLAVES && s->slaves[addr7].present) {
            s->nack_err = false;
            if (!is_rd && s->tx_count > 1 && s->slaves[addr7].write_fn) {
                s->slaves[addr7].write_fn(s->slaves[addr7].opaque,
                                          &s->tx[1], s->tx_count - 1);
            }
            s->xfer_done_nibble = (s->tx_count - 1) & 0x0f;
            s->have_xfer_done = true;
        } else {
            s->nack_err = true;
            s->have_xfer_done = false;
        }
    } else {
        s->nack_err = true;
        s->have_xfer_done = false;
    }

    s->msg_active = false;
    s->irq_pending = true;
    qemu_set_irq(s->irq, 1);
}

static void i2c_write(void *opaque, hwaddr off, uint64_t v, unsigned size)
{
    Cxd90045I2cState *s = opaque;
    unsigned i;

    for (i = 0; i < size; i++) {
        s->reg[off + i] = (v >> (8 * i)) & 0xff;
        s->wmask[off + i] = 1;
    }

    switch (off) {
    case I2C_STATUS:
        if (s->complete_transfers && (v & 0x20)) {
            s->nack_err = false;
            s->reg[I2C_STATUS] &= ~0x20;
        }
        break;
    case I2C_CMD:
        /* R2: 1 starts a transaction, 0 stops/aborts; both reset the set. */
        s->started = (v & 1) != 0;
        s->cfg_aux = false;
        s->cfg_t10 = false;
        s->cfg_t14 = false;
        s->msg_phase = false;
        s->have_xfer_done = false;
        break;
    case I2C_ABORT:
        if (s->complete_transfers && (v & 1)) {
            s->msg_active = false;
            s->nack_err = false;
            s->irq_pending = false;
            qemu_set_irq(s->irq, 0);
        }
        break;
    case I2C_AUX:
        s->cfg_aux = (v & 1) != 0;
        break;
    case I2C_T10:
        s->cfg_t10 = true;
        break;
    case I2C_T14:
        s->cfg_t14 = true;
        break;
    case I2C_MSG:
        s->msg_phase = true;
        s->msg_active = true;
        s->tx_count = 0;
        s->len_field = 0;
        s->addr = 0;
        s->have_xfer_done = false;
        break;
    case I2C_FIFO:
        if (s->msg_active && s->tx_count < ARRAY_SIZE(s->tx)) {
            s->tx[s->tx_count++] = (uint8_t)v;
        }
        break;
    case I2C_LEN:
        s->len_field = (uint16_t)v;
        s->have_xfer_done = false;
        break;
    case I2C_IRQ:
        if (s->complete_transfers && (v & 1)) {
            s->irq_pending = false;
            s->reg[I2C_IRQ] &= ~1;
            qemu_set_irq(s->irq, 0);
        }
        break;
    case I2C_ADDR:
        s->msg_phase = true;
        s->addr = (uint16_t)v;
        i2c_log_transfer(s);
        if (s->complete_transfers) {
            i2c_complete_message(s, (uint16_t)v);
        }
        break;
    default:
        break;
    }

    if (!i2c_driver_offset(off)) {
        i2c_log_unmodelled(s, off, true, size, v);
    }
    if (s->trace) {
        qemu_log("i2c-trace: write off 0x%03x size %u value 0x%08x\n",
                 (unsigned)off, size, (uint32_t)v);
    }
}

static const MemoryRegionOps i2c_ops = {
    .read = i2c_read,
    .write = i2c_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static void i2c_reset(DeviceState *dev)
{
    Cxd90045I2cState *s = CXD90045_I2C(dev);

    memset(s->reg, 0, sizeof(s->reg));
    memset(s->wmask, 0, sizeof(s->wmask));
    s->started = false;
    s->cfg_aux = false;
    s->cfg_t10 = false;
    s->cfg_t14 = false;
    s->msg_active = false;
    s->msg_phase = false;
    s->tx_count = 0;
    s->rx_pos = 0;
    s->rx_len = 0;
    s->len_field = 0;
    s->addr = 0;
    s->irq_pending = false;
    s->nack_err = false;
    s->xfer_done_nibble = 0;
    s->have_xfer_done = false;
    s->cur_addr7 = 0;
    if (s->complete_transfers) {
        qemu_set_irq(s->irq, 0);
    }
}

static void i2c_realize(DeviceState *dev, Error **errp)
{
    Cxd90045I2cState *s = CXD90045_I2C(dev);
    uint64_t sz = s->reg_size ? s->reg_size : I2C_REG_SIZE;

    if (sz > I2C_REG_SIZE) {
        sz = I2C_REG_SIZE;
    }
    memory_region_init_io(&s->mr, OBJECT(dev), &i2c_ops, s,
                          "cxd90045-i2c", sz);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->mr);
    sysbus_init_irq(SYS_BUS_DEVICE(dev), &s->irq);
}

static const Property i2c_props[] = {
    DEFINE_PROP_BOOL("trace", Cxd90045I2cState, trace, false),
    DEFINE_PROP_BOOL("complete-transfers", Cxd90045I2cState,
                     complete_transfers, false),
    DEFINE_PROP_UINT64("base", Cxd90045I2cState, page_base, I2C_PAGE_BASE),
    DEFINE_PROP_UINT64("size", Cxd90045I2cState, reg_size, I2C_REG_SIZE),
};

static void i2c_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = i2c_realize;
    device_class_set_legacy_reset(dc, i2c_reset);
    device_class_set_props(dc, i2c_props);
}

static const TypeInfo i2c_info = {
    .name = TYPE_CXD90045_I2C,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Cxd90045I2cState),
    .class_init = i2c_class_init,
};

static void i2c_register(void)
{
    type_register_static(&i2c_info);
}
type_init(i2c_register)
