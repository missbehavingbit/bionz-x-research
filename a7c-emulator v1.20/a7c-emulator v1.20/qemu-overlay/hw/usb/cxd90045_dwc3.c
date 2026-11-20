/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Contract-driven Synopsys DWC3 (2.90a / 0x5533290A) gadget model for
 * Sony CXD90045 (sony-a6400).
 *
 * MMIO window: 0xF020C000..0xF020FFFF (page-aligned 16 KiB; DWC3 core
 * registers begin at +0x100 = 0xF020C100), GIC IRQ 229.
 *
 * Implements the register, GEVNT DMA ring, DEPCMD, and TRB transfer
 * contracts observed in ILCE-6400 nflasha3-Image (E0579, E0590) with an
 * optional CharBackend virtual USB host socket for EP0/Bulk testing.
 */

#include "qemu/osdep.h"
#include "hw/sysbus.h"
#include "hw/irq.h"
#include "hw/qdev-properties.h"
#include "hw/qdev-properties-system.h"
#include "chardev/char-fe.h"
#include "system/dma.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qom/object.h"

#define TYPE_CXD90045_DWC3 "cxd90045-dwc3"
OBJECT_DECLARE_SIMPLE_TYPE(CxdDwc3State, CXD90045_DWC3)

#define DWC3_MMIO_SIZE      0x4000
#define DWC3_CORE_BASE      0x0100
#define DWC3_MAX_EPS        16

/* Global registers (relative to 0xF020C000) */
#define REG_GCTL            0x0110
#define REG_GGPIO           0x0124
#define REG_GUID            0x0128
#define REG_GSNPSID         0x0120
#define REG_GHWPARAMS0      0x0140
#define REG_GHWPARAMS7      0x015C
#define REG_GUSB2PHYCFG0    0x0200
#define REG_GUSB3PIPECTL0   0x02C0
#define REG_GEVNTADRLO0     0x0400
#define REG_GEVNTADRHI0     0x0404
#define REG_GEVNTSIZ0       0x0408
#define REG_GEVNTCOUNT0     0x040C
#define REG_GHWPARAMS8      0x0600

/* Device registers */
#define REG_DCFG            0x0700
#define REG_DCTL            0x0704
#define REG_DEVTEN          0x0708
#define REG_DSTS            0x070C
#define REG_DGCMDPAR        0x0710
#define REG_DGCMD           0x0714
#define REG_DALEPENA        0x0720

/* Physical EP command block: 0x0800 + ep * 0x10 */
#define REG_EP_BASE         0x0800
#define REG_EP_END          0x0900

/* Bit definitions */
#define DCTL_RUN_STOP       (1U << 31)
#define DCTL_CSFTRST        (1U << 30)
#define DSTS_DEVCTRLHLT     (1U << 22)
#define GEVNTSIZ_INTMASK    (1U << 31)

#define DEPCMD_CMDACT       (1U << 10)
#define DEPCMD_CMDIOC       (1U << 8)
#define DEPCMD_CMD_MASK     0x0FU

#define DEPCMD_SETEPCONFIG      1U
#define DEPCMD_SETTRANSFRES     2U
#define DEPCMD_GETSEQNUMBER     3U
#define DEPCMD_SETSTALL         4U
#define DEPCMD_CLEARSTALL       5U
#define DEPCMD_STARTTRANSFER    6U
#define DEPCMD_UPDATETRANSFER   7U
#define DEPCMD_ENDTRANSFER      8U
#define DEPCMD_STARTNEWCONFIG   9U

/* Event types */
#define DEVT_DISCONNECT     0U
#define DEVT_USBRESET       1U
#define DEVT_CONNECTDONE    2U

#define DEPEVT_XFERCOMPLETE 1U
#define DEPEVT_XFERINPROG   2U
#define DEPEVT_XFERNOTREADY 3U
#define DEPEVT_EPCMDCMPLT   7U

#define TRB_CTRL_HWO        (1U << 0)
#define TRB_CTRL_LST        (1U << 1)
#define TRB_CTRL_IOC        (1U << 11)

typedef struct CxdDwc3Trb {
    uint32_t bpl;
    uint32_t bph;
    uint32_t size;
    uint32_t ctrl;
} CxdDwc3Trb;

typedef struct CxdDwc3Ep {
    uint32_t par2;
    uint32_t par1;
    uint32_t par0;
    uint32_t cmd;
    uint32_t cfg0;
    uint32_t cfg1;
    uint64_t trb_addr;
    bool trb_active;
    bool stalled;
    uint8_t pending_out[1024];
    uint32_t pending_out_len;
    bool has_pending_out;
} CxdDwc3Ep;

struct CxdDwc3State {
    SysBusDevice parent_obj;
    MemoryRegion mr;
    qemu_irq irq;
    CharBackend chr;

    uint32_t gctl;
    uint32_t ggpio;
    uint32_t guid;
    uint32_t gusb2phycfg0;
    uint32_t gusb3pipectl0;

    uint32_t gevntadrlo;
    uint32_t gevntadrhi;
    uint32_t gevntsiz;
    uint32_t gevntcount;
    uint32_t gevnt_head;

    uint32_t dcfg;
    uint32_t dctl;
    uint32_t devten;
    uint32_t dsts;
    uint32_t dgcmdpar;
    uint32_t dgcmd;
    uint32_t dalepena;

    bool host_connected;
    CxdDwc3Ep eps[DWC3_MAX_EPS];

    char rx_line[2200];
    size_t rx_len;
};

static void dwc3_update_irq(CxdDwc3State *s)
{
    bool masked = (s->gevntsiz & GEVNTSIZ_INTMASK) != 0;
    int level = (!masked && s->gevntcount > 0) ? 1 : 0;
    qemu_set_irq(s->irq, level);
}

static void dwc3_push_event(CxdDwc3State *s, uint32_t ev)
{
    uint32_t ring_bytes = s->gevntsiz & 0xFFFCU;
    uint64_t base = ((uint64_t)s->gevntadrhi << 32) | s->gevntadrlo;
    uint32_t le_ev = cpu_to_le32(ev);

    if (ring_bytes < 4 || base == 0) {
        return;
    }
    if (s->gevntcount + 4 > ring_bytes) {
        return;
    }

    dma_memory_write(&address_space_memory, base + s->gevnt_head,
                     &le_ev, sizeof(le_ev), MEMTXATTRS_UNSPECIFIED);
    s->gevnt_head = (s->gevnt_head + 4) % ring_bytes;
    s->gevntcount += 4;
    dwc3_update_irq(s);
}

static void dwc3_push_devt(CxdDwc3State *s, uint32_t type, uint32_t info)
{
    if (!(s->devten & (1U << type))) {
        return;
    }
    uint32_t ev = 1U | ((type & 0xFU) << 8) | ((info & 0xFFFFU) << 16);
    dwc3_push_event(s, ev);
}

static void dwc3_push_depevt(CxdDwc3State *s, uint32_t ep, uint32_t type,
                             uint32_t status, uint32_t param)
{
    uint32_t ev = ((ep & 0x1FU) << 1) | ((type & 0xFU) << 6) |
                  ((status & 0xFU) << 12) | ((param & 0xFFFFU) << 16);
    dwc3_push_event(s, ev);
}

static void dwc3_emit_host_in(CxdDwc3State *s, uint32_t ep,
                              const uint8_t *buf, uint32_t len)
{
    if (!qemu_chr_fe_backend_connected(&s->chr)) {
        return;
    }
    GString *msg = g_string_new(NULL);
    g_string_append_printf(msg, "IN %u ", ep);
    for (uint32_t i = 0; i < len; i++) {
        g_string_append_printf(msg, "%02x", buf[i]);
    }
    g_string_append_c(msg, '\n');
    qemu_chr_fe_write_all(&s->chr, (const uint8_t *)msg->str, msg->len);
    g_string_free(msg, true);
}

static bool dwc3_ep_is_in(uint32_t ep)
{
    return (ep & 1U) != 0;
}

static void dwc3_ep_process_trb(CxdDwc3State *s, uint32_t ep_idx)
{
    CxdDwc3Ep *ep = &s->eps[ep_idx];
    CxdDwc3Trb trb;

    if (!ep->trb_active || ep->trb_addr == 0) {
        return;
    }

    dma_memory_read(&address_space_memory, ep->trb_addr,
                    &trb, sizeof(trb), MEMTXATTRS_UNSPECIFIED);
    trb.bpl = le32_to_cpu(trb.bpl);
    trb.bph = le32_to_cpu(trb.bph);
    trb.size = le32_to_cpu(trb.size);
    trb.ctrl = le32_to_cpu(trb.ctrl);

    if (!(trb.ctrl & TRB_CTRL_HWO)) {
        return;
    }

    uint64_t buf_addr = ((uint64_t)trb.bph << 32) | trb.bpl;
    uint32_t buf_len = trb.size & 0x00FFFFFFU;

    if (dwc3_ep_is_in(ep_idx)) {
        uint8_t tmp[1024];
        uint32_t copy_len = MIN(buf_len, (uint32_t)sizeof(tmp));
        if (copy_len > 0 && buf_addr != 0) {
            dma_memory_read(&address_space_memory, buf_addr,
                            tmp, copy_len, MEMTXATTRS_UNSPECIFIED);
            dwc3_emit_host_in(s, ep_idx, tmp, copy_len);
        }
        trb.size = 0;
        trb.ctrl &= ~TRB_CTRL_HWO;
        CxdDwc3Trb out_trb = {
            .bpl = cpu_to_le32(trb.bpl),
            .bph = cpu_to_le32(trb.bph),
            .size = cpu_to_le32(trb.size),
            .ctrl = cpu_to_le32(trb.ctrl),
        };
        dma_memory_write(&address_space_memory, ep->trb_addr,
                         &out_trb, sizeof(out_trb), MEMTXATTRS_UNSPECIFIED);
        ep->trb_active = false;
        dwc3_push_depevt(s, ep_idx, DEPEVT_XFERCOMPLETE, 0, 0);
        return;
    }

    /* OUT endpoint (including EP0 OUT / SETUP) */
    if (!ep->has_pending_out) {
        return;
    }

    uint32_t xfer = MIN(buf_len, ep->pending_out_len);
    if (xfer > 0 && buf_addr != 0) {
        dma_memory_write(&address_space_memory, buf_addr,
                         ep->pending_out, xfer, MEMTXATTRS_UNSPECIFIED);
    }
    trb.size = (buf_len - xfer) & 0x00FFFFFFU;
    trb.ctrl &= ~TRB_CTRL_HWO;
    CxdDwc3Trb out_trb = {
        .bpl = cpu_to_le32(trb.bpl),
        .bph = cpu_to_le32(trb.bph),
        .size = cpu_to_le32(trb.size),
        .ctrl = cpu_to_le32(trb.ctrl),
    };
    dma_memory_write(&address_space_memory, ep->trb_addr,
                     &out_trb, sizeof(out_trb), MEMTXATTRS_UNSPECIFIED);
    ep->has_pending_out = false;
    ep->pending_out_len = 0;
    ep->trb_active = false;
    dwc3_push_depevt(s, ep_idx, DEPEVT_XFERCOMPLETE, 0, 0);
}

static void dwc3_device_reset(CxdDwc3State *s)
{
    s->dcfg = 0;
    s->dctl = 0;
    s->devten = 0;
    s->dsts = DSTS_DEVCTRLHLT;
    s->dalepena = 0;
    s->gevntcount = 0;
    s->gevnt_head = 0;
    memset(s->eps, 0, sizeof(s->eps));
    dwc3_update_irq(s);
}

static void dwc3_handle_ep_cmd(CxdDwc3State *s, uint32_t ep_idx, uint32_t val)
{
    CxdDwc3Ep *ep = &s->eps[ep_idx];
    uint32_t cmd = val & DEPCMD_CMD_MASK;

    if (!(val & DEPCMD_CMDACT)) {
        ep->cmd = val;
        return;
    }

    switch (cmd) {
    case DEPCMD_SETEPCONFIG:
        ep->cfg0 = ep->par0;
        ep->cfg1 = ep->par1;
        break;
    case DEPCMD_SETTRANSFRES:
        break;
    case DEPCMD_SETSTALL:
        ep->stalled = true;
        break;
    case DEPCMD_CLEARSTALL:
        ep->stalled = false;
        break;
    case DEPCMD_STARTTRANSFER:
        ep->trb_addr = ((uint64_t)ep->par0 << 32) | ep->par1;
        ep->trb_active = true;
        val = (val & ~0x007F0000U) | ((ep_idx + 1U) << 16);
        ep->cmd = val & ~DEPCMD_CMDACT;
        dwc3_ep_process_trb(s, ep_idx);
        return;
    case DEPCMD_UPDATETRANSFER:
        ep->cmd = val & ~DEPCMD_CMDACT;
        dwc3_ep_process_trb(s, ep_idx);
        return;
    case DEPCMD_ENDTRANSFER:
        ep->trb_active = false;
        if (val & DEPCMD_CMDIOC) {
            dwc3_push_depevt(s, ep_idx, DEPEVT_EPCMDCMPLT, 0, cmd << 8);
        }
        break;
    default:
        break;
    }

    ep->cmd = val & ~DEPCMD_CMDACT;
}

static uint64_t cxd_dwc3_read(void *opaque, hwaddr offset, unsigned size)
{
    CxdDwc3State *s = CXD90045_DWC3(opaque);

    if (offset >= REG_EP_BASE && offset < REG_EP_END) {
        uint32_t ep = (offset - REG_EP_BASE) / 0x10U;
        uint32_t reg = (offset - REG_EP_BASE) % 0x10U;
        switch (reg) {
        case 0x0:
            return s->eps[ep].par2;
        case 0x4:
            return s->eps[ep].par1;
        case 0x8:
            return s->eps[ep].par0;
        case 0xC:
            return s->eps[ep].cmd;
        }
    }

    switch (offset) {
    case REG_GCTL:
        return s->gctl;
    case REG_GSNPSID:
        return 0x5533290AU;
    case REG_GGPIO:
        return s->ggpio;
    case REG_GUID:
        return s->guid;
    case REG_GHWPARAMS0:
        return 0x20204008U;
    case REG_GHWPARAMS0 + 4:
        return 0x00241008U;
    case REG_GHWPARAMS0 + 8:
        return 0x00000000U;
    case REG_GHWPARAMS0 + 0xC:
        return 0x10420089U;
    case REG_GHWPARAMS0 + 0x10:
        return 0x00000000U;
    case REG_GHWPARAMS0 + 0x14:
        return 0x04020200U;
    case REG_GHWPARAMS0 + 0x18:
        return 0x00000400U;
    case REG_GHWPARAMS7:
        return 0x00000000U;
    case REG_GHWPARAMS8:
        return 0x00000000U;
    case REG_GUSB2PHYCFG0:
        return s->gusb2phycfg0;
    case REG_GUSB3PIPECTL0:
        return s->gusb3pipectl0;
    case REG_GEVNTADRLO0:
        return s->gevntadrlo;
    case REG_GEVNTADRHI0:
        return s->gevntadrhi;
    case REG_GEVNTSIZ0:
        return s->gevntsiz;
    case REG_GEVNTCOUNT0:
        return s->gevntcount;
    case REG_DCFG:
        return s->dcfg;
    case REG_DCTL:
        return s->dctl;
    case REG_DEVTEN:
        return s->devten;
    case REG_DSTS:
        return s->dsts;
    case REG_DGCMDPAR:
        return s->dgcmdpar;
    case REG_DGCMD:
        return s->dgcmd;
    case REG_DALEPENA:
        return s->dalepena;
    default:
        qemu_log_mask(LOG_UNIMP, "cxd90045-dwc3: unimp read +0x%04" HWADDR_PRIx "\n",
                      offset);
        return 0;
    }
}

static void cxd_dwc3_write(void *opaque, hwaddr offset, uint64_t val64,
                           unsigned size)
{
    CxdDwc3State *s = CXD90045_DWC3(opaque);
    uint32_t val = (uint32_t)val64;

    if (offset >= REG_EP_BASE && offset < REG_EP_END) {
        uint32_t ep = (offset - REG_EP_BASE) / 0x10U;
        uint32_t reg = (offset - REG_EP_BASE) % 0x10U;
        switch (reg) {
        case 0x0:
            s->eps[ep].par2 = val;
            return;
        case 0x4:
            s->eps[ep].par1 = val;
            return;
        case 0x8:
            s->eps[ep].par0 = val;
            return;
        case 0xC:
            dwc3_handle_ep_cmd(s, ep, val);
            return;
        }
    }

    switch (offset) {
    case REG_GCTL:
        s->gctl = val;
        return;
    case REG_GGPIO:
        s->ggpio = val;
        return;
    case REG_GUID:
        s->guid = val;
        return;
    case REG_GUSB2PHYCFG0:
        s->gusb2phycfg0 = val;
        return;
    case REG_GUSB3PIPECTL0:
        s->gusb3pipectl0 = val;
        return;
    case REG_GEVNTADRLO0:
        s->gevntadrlo = val;
        return;
    case REG_GEVNTADRHI0:
        s->gevntadrhi = val;
        return;
    case REG_GEVNTSIZ0:
        s->gevntsiz = val;
        dwc3_update_irq(s);
        return;
    case REG_GEVNTCOUNT0: {
        uint32_t ack = val & 0xFFFFU;
        s->gevntcount = (ack >= s->gevntcount) ? 0 : (s->gevntcount - ack);
        dwc3_update_irq(s);
        return;
    }
    case REG_DCFG:
        s->dcfg = val;
        return;
    case REG_DCTL:
        if (val & DCTL_CSFTRST) {
            dwc3_device_reset(s);
            return;
        }
        {
            bool was_run = (s->dctl & DCTL_RUN_STOP) != 0;
            bool now_run = (val & DCTL_RUN_STOP) != 0;
            s->dctl = val;
            if (now_run) {
                s->dsts &= ~DSTS_DEVCTRLHLT;
                if (!was_run && s->host_connected) {
                    dwc3_push_devt(s, DEVT_USBRESET, 0);
                    dwc3_push_devt(s, DEVT_CONNECTDONE, 0);
                }
            } else {
                s->dsts |= DSTS_DEVCTRLHLT;
            }
        }
        return;
    case REG_DEVTEN:
        s->devten = val;
        return;
    case REG_DGCMDPAR:
        s->dgcmdpar = val;
        return;
    case REG_DGCMD:
        s->dgcmd = val & ~DEPCMD_CMDACT;
        return;
    case REG_DALEPENA:
        s->dalepena = val;
        return;
    default:
        qemu_log_mask(LOG_UNIMP, "cxd90045-dwc3: unimp write +0x%04" HWADDR_PRIx
                      " = 0x%08x\n", offset, val);
        return;
    }
}

static const MemoryRegionOps cxd_dwc3_ops = {
    .read = cxd_dwc3_read,
    .write = cxd_dwc3_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
    },
};

static int hex_nibble(char c)
{
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

static size_t decode_hex(const char *hex, uint8_t *out, size_t max_out)
{
    size_t n = 0;
    while (hex[0] && hex[1] && n < max_out) {
        int hi = hex_nibble(hex[0]);
        int lo = hex_nibble(hex[1]);
        if (hi < 0 || lo < 0) {
            break;
        }
        out[n++] = (uint8_t)((hi << 4) | lo);
        hex += 2;
    }
    return n;
}

static void dwc3_handle_host_line(CxdDwc3State *s, const char *line)
{
    if (strcmp(line, "CONN") == 0) {
        s->host_connected = true;
        if (s->dctl & DCTL_RUN_STOP) {
            dwc3_push_devt(s, DEVT_USBRESET, 0);
            dwc3_push_devt(s, DEVT_CONNECTDONE, 0);
        }
    } else if (strcmp(line, "DISC") == 0) {
        s->host_connected = false;
        if (s->dctl & DCTL_RUN_STOP) {
            dwc3_push_devt(s, DEVT_DISCONNECT, 0);
        }
    } else if (strncmp(line, "SETUP ", 6) == 0) {
        CxdDwc3Ep *ep0 = &s->eps[0];
        size_t n = decode_hex(line + 6, ep0->pending_out, sizeof(ep0->pending_out));
        if (n > 0) {
            ep0->pending_out_len = (uint32_t)n;
            ep0->has_pending_out = true;
            dwc3_ep_process_trb(s, 0);
        }
    } else if (strncmp(line, "OUT ", 4) == 0) {
        unsigned ep_idx = 0;
        const char *p = line + 4;
        char *end = NULL;
        ep_idx = (unsigned)strtoul(p, &end, 10);
        if (end && *end == ' ' && ep_idx < DWC3_MAX_EPS && !dwc3_ep_is_in(ep_idx)) {
            CxdDwc3Ep *ep = &s->eps[ep_idx];
            size_t n = decode_hex(end + 1, ep->pending_out, sizeof(ep->pending_out));
            if (n > 0) {
                ep->pending_out_len = (uint32_t)n;
                ep->has_pending_out = true;
                dwc3_ep_process_trb(s, ep_idx);
            }
        }
    }
}

static int cxd_dwc3_chr_can_read(void *opaque)
{
    return 1024;
}

static void cxd_dwc3_chr_read(void *opaque, const uint8_t *buf, int size)
{
    CxdDwc3State *s = CXD90045_DWC3(opaque);
    for (int i = 0; i < size; i++) {
        char c = (char)buf[i];
        if (c == '\r') {
            continue;
        }
        if (c == '\n') {
            s->rx_line[s->rx_len] = '\0';
            if (s->rx_len > 0) {
                dwc3_handle_host_line(s, s->rx_line);
                s->rx_len = 0;
            }
        } else if (s->rx_len + 1 < sizeof(s->rx_line)) {
            s->rx_line[s->rx_len++] = c;
        }
    }
}

static void cxd_dwc3_reset(DeviceState *dev)
{
    CxdDwc3State *s = CXD90045_DWC3(dev);
    s->gctl = 0;
    s->ggpio = 0;
    s->guid = 0;
    s->gusb2phycfg0 = 0;
    s->gusb3pipectl0 = 0;
    s->gevntadrlo = 0;
    s->gevntadrhi = 0;
    s->gevntsiz = 0;
    s->host_connected = false;
    s->rx_len = 0;
    dwc3_device_reset(s);
}

static void cxd_dwc3_realize(DeviceState *dev, Error **errp)
{
    CxdDwc3State *s = CXD90045_DWC3(dev);
    SysBusDevice *sbd = SYS_BUS_DEVICE(dev);

    memory_region_init_io(&s->mr, OBJECT(s), &cxd_dwc3_ops, s,
                          TYPE_CXD90045_DWC3, DWC3_MMIO_SIZE);
    sysbus_init_mmio(sbd, &s->mr);
    sysbus_init_irq(sbd, &s->irq);

    if (qemu_chr_fe_backend_connected(&s->chr)) {
        qemu_chr_fe_set_handlers(&s->chr, cxd_dwc3_chr_can_read,
                                 cxd_dwc3_chr_read, NULL, NULL, s, NULL, true);
    }
}

static const Property cxd_dwc3_props[] = {
    DEFINE_PROP_CHR("chardev", CxdDwc3State, chr),
};

static void cxd_dwc3_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    device_class_set_legacy_reset(dc, cxd_dwc3_reset);
    dc->realize = cxd_dwc3_realize;
    device_class_set_props(dc, cxd_dwc3_props);
}

static const TypeInfo cxd_dwc3_info = {
    .name          = TYPE_CXD90045_DWC3,
    .parent        = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(CxdDwc3State),
    .class_init    = cxd_dwc3_class_init,
};

static void cxd_dwc3_register_types(void)
{
    type_register_static(&cxd_dwc3_info);
}

type_init(cxd_dwc3_register_types)
