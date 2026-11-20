/*
 * Sony ILCE-7C (alpha 7C, BIONZ X / ARM-CXD900X0) Machine Model
 *
 * Derived from the sony-a6400 machine (same CXD900X0 SoC family).  A7C
 * values below were checked against the A7C dump (kernel 3.0.27_nl-rt106+,
 * built 2021-11-15, proc_cmdline / proc_iomem / proc_cpuinfo):
 *   - same machine ID 0xDA9 and boot contract (Image @0x108000,
 *     initrd @0x700000, kemco params @0x103000)
 *   - RAM bank 1 is 256 MiB on the A7C ("mem=256M@0x80000000@1"),
 *     not 1016 MiB as on the A6400
 *   - 4 x Cortex-A5 (CPU part 0xc05, VFPv4-D16)
 *   - eMMC user area = /dev/nflasha (Sony "82461.00" partition table),
 *     boot0/boot1 = nflashaB0/nflashaB1 (4 MiB each)
 * Inherited A6400 contract notes:
 *   - Machine ID: 0x00000DA9 (3497, ARM-CXD900X0)
 *   - RAM bank 0: 0x00000000 .. 0x40000000 (1 GiB)
 *   - RAM bank 1: 0x80000000 .. 0xBF800000 (1016 MiB)
 *   - SRAM:       0xFE000000 .. 0xFE100000 (1 MiB)
 *   - A9MPCore:   0xF8000000 (GIC CPU @ 0xF8000100, GIC Dist @ 0xF8001000)
 *   - PL011 UART0:0xF2000000 (IRQ 150 -> SPI 118)
 *   - CXD4115 timer: 0xF2403000, channel n -> IRQ 153 + n (E0522)
 *   - 4 x Cortex-A5 (E0522: secondaries released through SRAM mailbox
 *     0xFE005010 + 4 * (cpu - 1) by cxd900x0_smp_init)
 *   - CRCDC Clk:  0xF2A31000 (+0x30 bit 0 ready)
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "hw/arm/boot.h"
#include "hw/loader.h"
#include "qemu/error-report.h"
#include "system/reset.h"
#include "hw/boards.h"
#include "hw/irq.h"
#include "hw/char/pl011.h"
#include "hw/cpu/a9mpcore.h"
#include "hw/misc/unimp.h"
#include "hw/qdev-properties.h"
#include "hw/qdev-properties-system.h"
#include "monitor/qdev.h"
#include "hw/sd/sd.h"
#include "hw/sd/sdhci.h"
#include "ui/input.h"
#include "hw/misc/cxd90014_adc.h"
#include "hw/sysbus.h"
#include "system/blockdev.h"
#include "qemu/cutils.h"
#include "system/rtc.h"
#include "hw/misc/cxd90014_sio.h"
#include "qemu/bswap.h"
#include "system/block-backend.h"
#include "qemu/log.h"
#include "qemu/units.h"
#include "qemu/timer.h"
#include "system/address-spaces.h"
#include "system/system.h"
#include "target/arm/cpu.h"

/* CXD90045 DWC3 gadget model (hw/usb/cxd90045_dwc3.c, from bionz-x-research qemu-a6400) */
#define TYPE_CXD90045_DWC3 "cxd90045-dwc3"

#define CXD90045_MACHINE_ID      0x00000DA9
#define CXD90045_RAM0_BASE       0x00000000ULL
#define CXD90045_RAM0_SIZE       (1 * GiB)
#define CXD90045_RAM1_BASE       0x80000000ULL
#define CXD90045_RAM1_SIZE       (256 * MiB)   /* A7C: mem=256M@0x80000000 */
#define CXD90045_SRAM_BASE       0xFE000000ULL
#define CXD90045_SRAM_SIZE       (1 * MiB)

#define CXD90045_PERIPH_BASE     0xF8000000ULL
#define CXD90045_UART0_BASE      0xF2000000ULL
#define CXD90045_UART0_SPI       (150 - 32)
#define CXD90045_TIMER_BASE      0xF2403000ULL
#define CXD90045_TIMER_IRQ0      153
#define CXD90045_MAX_CPUS        4
#define CXD90045_CRCDC_BASE      0xF2A31000ULL
#define CXD90045_GPES_BAR_BASE   0xF1000000ULL
#define CXD90045_GPES_SPI        (76 - 32)
#define CXD90045_SUGI_CLK_BASE   0xF3400000ULL
#define CXD90045_DFS_CTRL_BASE   0xF2916000ULL
#define CXD90045_L2C_STUB_BASE   0xF8002000ULL
#define CXD90045_EMMC0_BASE      0xF0304000ULL
#define CXD90045_EMMC0_SPI       (227 - 32)
#define CXD90045_XDMAC0_BASE     0xF0000000ULL
#define CXD90045_XDMAC0_SPI_BASE (189 - 32)
#define CXD90045_XDMAC1_BASE     0xF0010000ULL
#define CXD90045_XDMAC1_SPI_BASE (193 - 32)
#define CXD90045_LDEC_BASE       0xF2903000ULL
#define CXD90045_LDEC_IN_BASE    0xF0020000ULL
#define CXD90045_LDEC_IN_SIZE    0x10000ULL
#define CXD90045_LDEC_SPI        (171 - 32)
#define CXD90045_F2A01_BASE      0xF2A01000ULL

#define CXD90045_NUM_IRQS        256

static uint64_t cxd90045_crcdc_read(void *opaque, hwaddr offset, unsigned size)
{
    /* +0x30 bit 0 ready after clock control writes (E0423) */
    if (offset == 0x30) {
        return 0x1;
    }
    return 0;
}

static void cxd90045_crcdc_write(void *opaque, hwaddr offset, uint64_t value,
                                 unsigned size)
{
}

static const MemoryRegionOps cxd90045_crcdc_ops = {
    .read = cxd90045_crcdc_read,
    .write = cxd90045_crcdc_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static uint64_t cxd90045_l2c_stub_read(void *opaque, hwaddr offset, unsigned size)
{
    /* Observed read stub 0xF800277C -> 0x100 */
    if (offset == 0x77C) {
        return 0x100;
    }
    return 0;
}

static void cxd90045_l2c_stub_write(void *opaque, hwaddr offset, uint64_t value,
                                    unsigned size)
{
}

static const MemoryRegionOps cxd90045_l2c_stub_ops = {
    .read = cxd90045_l2c_stub_read,
    .write = cxd90045_l2c_stub_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

typedef struct CxdRwStub {
    MemoryRegion mmio;
    uint32_t *regs;
    size_t num_regs;
} CxdRwStub;

static uint64_t cxd90045_rw_stub_read(void *opaque, hwaddr offset, unsigned size)
{
    CxdRwStub *s = opaque;
    size_t idx = offset >> 2;
    if (idx < s->num_regs) {
        return s->regs[idx];
    }
    return 0;
}

static void cxd90045_rw_stub_write(void *opaque, hwaddr offset, uint64_t value,
                                   unsigned size)
{
    CxdRwStub *s = opaque;
    size_t idx = offset >> 2;
    if (idx < s->num_regs) {
        s->regs[idx] = (uint32_t)value;
    }
}

static const MemoryRegionOps cxd90045_rw_stub_ops = {
    .read = cxd90045_rw_stub_read,
    .write = cxd90045_rw_stub_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static CxdRwStub *cxd90045_create_rw_stub(MemoryRegion *sysmem, const char *name,
                                         hwaddr base, size_t size)
{
    CxdRwStub *s = g_new0(CxdRwStub, 1);
    s->num_regs = size / sizeof(uint32_t);
    s->regs = g_new0(uint32_t, s->num_regs);
    memory_region_init_io(&s->mmio, NULL, &cxd90045_rw_stub_ops, s, name, size);
    memory_region_add_subregion(sysmem, base, &s->mmio);
    return s;
}

typedef struct CxdSugilite {
    MemoryRegion mmio;
    qemu_irq irq;
    uint32_t regs[0x10000 / 4];
} CxdSugilite;

static uint64_t cxd90045_sugilite_read(void *opaque, hwaddr offset,
                                       unsigned size)
{
    CxdSugilite *s = opaque;
    size_t idx = offset >> 2;
    if (idx < ARRAY_SIZE(s->regs)) {
        return s->regs[idx];
    }
    return 0;
}

static void cxd90045_sugilite_write(void *opaque, hwaddr offset, uint64_t value,
                                    unsigned size)
{
    CxdSugilite *s = opaque;
    size_t idx = offset >> 2;
    if (idx < ARRAY_SIZE(s->regs)) {
        s->regs[idx] = (uint32_t)value;
    }
    if (offset == 0x1000 || offset == 0x1800) {
        if (value) {
            if (offset == 0x1800) {
                static int cmd_log = 0;
                uint32_t addr = s->regs[0x1804 >> 2];
                uint32_t buf[16] = {0};
                cpu_physical_memory_read(addr, buf, sizeof(buf));
                if (buf[7] != 0x12345678 && cmd_log < 20) {
                    fprintf(stderr,
                            "[SUGI-RENDER #%d] addr=0x%08x val=0x%08x :",
                            cmd_log, addr, (unsigned)value);
                    for (int k = 0; k < 16; k++) {
                        fprintf(stderr, " %08x", buf[k]);
                    }
                    fprintf(stderr, "\n");
                    cmd_log++;
                }
            }
            s->regs[0x34 >> 2] |= 0x80000000U;
            qemu_irq_raise(s->irq);
        } else if (offset == 0x1000) {
            s->regs[0x34 >> 2] &= ~0x80000000U;
            if ((s->regs[0x34 >> 2] & 0xC400000FU) == 0) {
                qemu_irq_lower(s->irq);
            }
        }
    } else if (offset == 0x0C18) {
        if (value) {
            s->regs[0x34 >> 2] |= 0x40000000U;
            qemu_irq_raise(s->irq);
        } else {
            s->regs[0x34 >> 2] &= ~0x40000000U;
            if ((s->regs[0x34 >> 2] & 0xC400000FU) == 0) {
                qemu_irq_lower(s->irq);
            }
        }
    } else if (offset == 0x001C) {
        if (value) {
            s->regs[0x34 >> 2] |= 0x04000000U;
            qemu_irq_raise(s->irq);
        } else {
            s->regs[0x34 >> 2] &= ~0x04000000U;
            if ((s->regs[0x34 >> 2] & 0xC400000FU) == 0) {
                qemu_irq_lower(s->irq);
            }
        }
    }
}

static const MemoryRegionOps cxd90045_sugilite_ops = {
    .read = cxd90045_sugilite_read,
    .write = cxd90045_sugilite_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static void G_GNUC_UNUSED cxd90045_create_sugilite(MemoryRegion *sysmem, hwaddr base,
                                     qemu_irq irq)
{
    CxdSugilite *s = g_new0(CxdSugilite, 1);
    s->irq = irq;
    memory_region_init_io(&s->mmio, NULL, &cxd90045_sugilite_ops, s,
                          "cxd90045.sugilite_bar", 0x10000);
    memory_region_add_subregion(sysmem, base, &s->mmio);
}

typedef struct CxdLdec {
    MemoryRegion mmio;
    qemu_irq irq;
    uint32_t regs[0x100 >> 2];
    uint8_t *in_buf;
    size_t in_cap;
    size_t in_len;
} CxdLdec;

static CxdLdec *s_cxd_ldec;

static bool cxd90045_ldec_lz77_decode(const uint8_t *in, size_t in_len,
                                      uint8_t *out, size_t max_out,
                                      size_t *out_len_p)
{
    static const uint32_t lengths[16] = {
        3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 32, 64
    };
    size_t pos = 0;
    size_t out_len = 0;
    while (pos < in_len && out_len < max_out) {
        uint8_t kind = in[pos++];
        if (kind == 0x0f) {
            if (pos + 3 > in_len) {
                return false;
            }
            pos++; /* skip 1 byte */
            uint32_t count = in[pos] | ((uint32_t)in[pos + 1] << 8);
            pos += 2;
            if (pos + count > in_len || out_len + count > max_out) {
                return false;
            }
            memcpy(out + out_len, in + pos, count);
            pos += count;
            out_len += count;
        } else if (kind == 0xf0) {
            bool done = false;
            while (!done) {
                if (pos >= in_len) {
                    return false;
                }
                uint8_t flags = in[pos++];
                if (flags == 0) {
                    if (pos + 8 > in_len || out_len + 8 > max_out) {
                        return false;
                    }
                    memcpy(out + out_len, in + pos, 8);
                    pos += 8;
                    out_len += 8;
                    continue;
                }
                for (int bit = 0; bit < 8; bit++) {
                    if ((flags >> bit) & 1) {
                        if (pos + 2 > in_len) {
                            return false;
                        }
                        uint8_t a = in[pos++];
                        uint8_t b = in[pos++];
                        uint32_t dist = ((uint32_t)(a & 0x0f) << 8) | b;
                        if (dist == 0) {
                            done = true;
                            break;
                        }
                        uint32_t len = lengths[a >> 4];
                        if (dist > out_len || out_len + len > max_out) {
                            return false;
                        }
                        for (uint32_t k = 0; k < len; k++) {
                            out[out_len] = out[out_len - dist];
                            out_len++;
                        }
                    } else {
                        if (pos >= in_len || out_len >= max_out) {
                            return false;
                        }
                        out[out_len++] = in[pos++];
                    }
                }
            }
        } else {
            return false;
        }
    }
    *out_len_p = out_len;
    return true;
}

static void cxd90045_ldec_feed(uint32_t dsa_off, uint32_t ssa, uint32_t len)
{
    CxdLdec *s = s_cxd_ldec;
    if (!s || len == 0 || len > (16 * 1024 * 1024) || dsa_off > (16 * 1024 * 1024)) {
        return;
    }
    if (dsa_off == 0) {
        s->in_len = 0;
    }
    size_t need = (size_t)dsa_off + (size_t)len;
    if (need > s->in_cap) {
        s->in_cap = need < 65536 ? 65536 : need * 2;
        s->in_buf = g_realloc(s->in_buf, s->in_cap);
    }
    cpu_physical_memory_read(ssa, s->in_buf + dsa_off, len);
    if (need > s->in_len) {
        s->in_len = need;
    }
    uint32_t records = s->regs[0x0c >> 2];
    uint32_t dst_phys = s->regs[0x10 >> 2];
    size_t max_out = (records > 0 && records <= 4096) ? (size_t)records * 4096 : (16 * 1024 * 1024);
    if (dst_phys != 0) {
        uint8_t *out_buf = g_malloc0(max_out);
        size_t decoded = 0;
        bool ok = cxd90045_ldec_lz77_decode(s->in_buf, s->in_len, out_buf, max_out, &decoded);
        if (ok && decoded > 0) {
            static int ldec_ok = 0;
            if (ldec_ok < 5) {
                fprintf(stderr,
                        "[LDEC OK #%d] in=0x%zx -> dst=0x%x out=0x%zx (rec=%u)\n",
                        ldec_ok, s->in_len, dst_phys, decoded, records);
                ldec_ok++;
            }
            cpu_physical_memory_write(dst_phys, out_buf, decoded);
            s->in_len = 0;
            s->regs[0x34 >> 2] = 0x1;
            qemu_irq_raise(s->irq);
        }
        g_free(out_buf);
    }
}

static uint64_t cxd90045_ldec_read(void *opaque, hwaddr offset, unsigned size)
{
    CxdLdec *s = opaque;
    size_t idx = offset >> 2;
    if (idx < ARRAY_SIZE(s->regs)) {
        return s->regs[idx];
    }
    return 0;
}

static void cxd90045_ldec_write(void *opaque, hwaddr offset, uint64_t value,
                                unsigned size)
{
    CxdLdec *s = opaque;
    size_t idx = offset >> 2;
    if (idx < ARRAY_SIZE(s->regs)) {
        s->regs[idx] = (uint32_t)value;
    }
    if (offset == 0x30) {
        s->regs[0x34 >> 2] &= ~(uint32_t)value;
        if ((s->regs[0x34 >> 2] & 0x7bU) == 0) {
            qemu_irq_lower(s->irq);
        }
    }
}

static const MemoryRegionOps cxd90045_ldec_ops = {
    .read = cxd90045_ldec_read,
    .write = cxd90045_ldec_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static uint64_t cxd90045_f2a01_read(void *opaque, hwaddr offset, unsigned size)
{
    uint32_t *regs = opaque;
    if (offset == 0x04) {
        return regs[0x04 >> 2] | 0x1U;
    }
    if (offset == 0x2c) {
        return regs[0x2c >> 2] | 0x2U;
    }
    if (offset == 0x30) {
        return 0;
    }
    if ((offset >> 2) < 0x400) {
        return regs[offset >> 2];
    }
    return 0;
}

static void cxd90045_f2a01_write(void *opaque, hwaddr offset, uint64_t value,
                                 unsigned size)
{
    uint32_t *regs = opaque;
    if (offset == 0x30) {
        return; /* self-clearing trigger */
    }
    if ((offset >> 2) < 0x400) {
        regs[offset >> 2] = (uint32_t)value;
    }
}

static const MemoryRegionOps cxd90045_f2a01_ops = {
    .read = cxd90045_f2a01_read,
    .write = cxd90045_f2a01_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static void cxd90045_create_ldec(MemoryRegion *sysmem, hwaddr base, qemu_irq irq)
{
    CxdLdec *s = g_new0(CxdLdec, 1);
    s->irq = irq;
    s_cxd_ldec = s;
    memory_region_init_io(&s->mmio, NULL, &cxd90045_ldec_ops, s,
                          "cxd90045.ldec", 0x1000);
    memory_region_add_subregion(sysmem, base, &s->mmio);
}

typedef struct CxdXdmac {
    MemoryRegion mmio;
    qemu_irq irqs[4];
    uint32_t regs[0x100 >> 2];
} CxdXdmac;

static uint64_t cxd90045_xdmac_read(void *opaque, hwaddr offset, unsigned size)
{
    CxdXdmac *s = opaque;
    size_t idx = offset >> 2;
    if (idx < ARRAY_SIZE(s->regs)) {
        return s->regs[idx];
    }
    return 0;
}

static void cxd90045_xdmac_write(void *opaque, hwaddr offset, uint64_t value,
                                 unsigned size)
{
    CxdXdmac *s = opaque;
    size_t idx = offset >> 2;
    if (idx < ARRAY_SIZE(s->regs)) {
        s->regs[idx] = (uint32_t)value;
    }
    if (offset >= 0x10 && offset < 0x10 + 4 * 0x30) {
        int ch = (int)((offset - 0x10) / 0x30);
        hwaddr ch_off = (offset - 0x10) % 0x30;
        hwaddr ch_base = 0x10 + (hwaddr)ch * 0x30;
        if (ch_off == 0x18 && value != 0) {
            uint32_t tbc = s->regs[(ch_base + 0x00) >> 2] + 1;
            uint32_t ssa = s->regs[(ch_base + 0x04) >> 2];
            uint32_t dsa = s->regs[(ch_base + 0x08) >> 2];
            if (dsa >= CXD90045_LDEC_IN_BASE &&
                dsa < CXD90045_LDEC_IN_BASE + CXD90045_LDEC_IN_SIZE) {
                cxd90045_ldec_feed((uint32_t)(dsa - CXD90045_LDEC_IN_BASE),
                                   ssa, tbc);
            } else if (tbc > 0 && tbc <= (16 * 1024 * 1024)) {
                uint8_t buf[4096];
                uint32_t rem = tbc;
                uint32_t off = 0;
                while (rem > 0) {
                    uint32_t chunk = rem > sizeof(buf) ? sizeof(buf) : rem;
                    cpu_physical_memory_read(ssa + off, buf, chunk);
                    cpu_physical_memory_write(dsa + off, buf, chunk);
                    off += chunk;
                    rem -= chunk;
                }
            }
            s->regs[(ch_base + 0x20) >> 2] = 0x8;
            qemu_irq_raise(s->irqs[ch]);
        } else if (ch_off == 0x20 && value == 0) {
            s->regs[(ch_base + 0x20) >> 2] = 0;
            qemu_irq_lower(s->irqs[ch]);
        }
    }
}

static const MemoryRegionOps cxd90045_xdmac_ops = {
    .read = cxd90045_xdmac_read,
    .write = cxd90045_xdmac_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static void cxd90045_create_xdmac(MemoryRegion *sysmem, hwaddr base,
                                  qemu_irq *irqs)
{
    CxdXdmac *s = g_new0(CxdXdmac, 1);
    for (int i = 0; i < 4; i++) {
        s->irqs[i] = irqs[i];
    }
    memory_region_init_io(&s->mmio, NULL, &cxd90045_xdmac_ops, s,
                          "cxd90045.xdmac", 0x10000);
    memory_region_add_subregion(sysmem, base, &s->mmio);
}

/*
 * A7C PCIe root complexes (memory card slot sits behind PCIe1: proc_iomem
 * "PCI Bus 0000:81 ... mmc-pci").  From PCIeRC_Initialize (0x60122a80):
 * channel n has DBI at 0xF0140000 + n * 0x40000 and its control block at
 * DBI + 0x20000; after programming the control block the driver polls
 * ctrl+0x000 bit 30 (link up) for 1e6 iterations, then BUG()s.  This model
 * keeps every register as plain storage and reports the link as trained.
 * No endpoint is modelled yet, so config reads return 0 (empty bus).
 */
#define A7C_PCIE0_DBI            0xF0140000ULL
#define A7C_PCIE_STRIDE          0x40000ULL
#define A7C_PCIE_CTRL_OFF        0x20000ULL
#define A7C_PCIE_LINK_UP         0x40000000U

/*
 * v1.10: memory-card reader behind PCIe1 (opt-in, A7C_CARD_SLOT=1).
 * Kernel (stock A7C vmlinux): f_pcie2_dme_read_config 0x601215D8 reads the
 * root port's own config space straight from DBI (bus == root bus, slot 0,
 * pcie_ctl_root_config 0x60122894); any other bus goes through
 * pcie_ctl_cfg_io_set 0x601228B8, which writes the target
 * (type << 16 | bus << 8 | slot << 3 | func) to DBI+0x1110, then a 4 KiB
 * config window (ch0 PA 0xBFA00000, ch1 PA 0xBFE00000, ioremapped at
 * 0xB0808000 / 0xB080A000).  pcie_ctl_cfg_read 0x601228F0 writes ~0 to
 * DBI+0x1000 first and treats a non-zero value there afterwards as
 * "unsupported request" (no device).  The A7C's reader is mmc_drv.ko's
 * mmc-pci: ids 1217:8520 / 8750 / 8751 (O2 Micro), BARs at 0xE8000000 (4 KiB)
 * and 0xE8001000 (2 KiB) on the camera (proc_iomem).  INTA of ch1 =
 * Linux/GIC irq (ch + 0x1D) * 8 = 240 (f_pcie2_dme_map_irq 0x601213C4).
 * Without a root-port header, pcie_root_ctrl (0x60121BD8) finds bus 0x80 but
 * no device 80:00.0 and BUG()s at pcie.c:841 ("RC not found") as soon as
 * the card slot is powered (after the micro's slot C is answered).
 */
#define A7C_PCIE_CFGWIN0         0xBFA00000ULL
#define A7C_PCIE_CFGWIN_STRIDE   0x400000ULL
#define A7C_PCIE_ERR_OFF         0x1000
#define A7C_PCIE_TARGET_OFF      0x1110
#define A7C_PCIE_RP_CAP          0x70

typedef struct A7cPcieEp {
    uint32_t cfg[64];
    MemoryRegion *sdhci_mr;         /* the SDHCI's registers (BAR0) */
    SDBus *sdbus;                   /* card bus, NULL without a card */
    QEMUTimer *cd_timer;
    bool cd_signalled;
    uint32_t bar_mask[2];
    MemoryRegion bar[2];
    MemoryRegion bar1_regs;
    uint32_t bar1_store[0x800 / 4];
    MemoryRegion *sysmem;
} A7cPcieEp;

typedef struct A7cPcie {
    MemoryRegion mmio;
    MemoryRegion cfgwin;
    uint32_t regs[A7C_PCIE_STRIDE / 4];
    bool root_port;                 /* DBI config space = a root port header */
    A7cPcieEp *ep;                  /* endpoint on the secondary bus, or NULL */
} A7cPcie;

static uint32_t a7c_merge(uint32_t old, unsigned off, uint64_t value, unsigned size)
{
    unsigned sh = (off & 3) * 8;
    uint32_t mask = size >= 4 ? 0xFFFFFFFFu : ((1u << (size * 8)) - 1) << sh;
    return (old & ~mask) | (((uint32_t)value << sh) & mask);
}

static uint32_t a7c_pick(uint32_t v, unsigned off, unsigned size)
{
    if (size < 4) {
        v >>= (off & 3) * 8;
        v &= (1u << (size * 8)) - 1;
    }
    return v;
}

static void a7c_pcie_rp_init(A7cPcie *s)
{
    uint32_t *c = s->regs;
    const unsigned cap = A7C_PCIE_RP_CAP / 4;

    c[0x00 / 4] = 0x200010CF;       /* vendor/device: placeholder ids [?] */
    c[0x04 / 4] = 0x00100000;       /* status: capabilities list */
    c[0x08 / 4] = 0x06040001;       /* PCI-PCI bridge */
    c[0x0C / 4] = 0x00010000;       /* header type 1 */
    c[0x34 / 4] = A7C_PCIE_RP_CAP;
    c[0x3C / 4] = 0x00000100;       /* INTA */
    /*
     * v1.13: PCIe cap v2, root port, slot implemented: Sony's pcie_slot_ctrl
     * power-cycles the card reader through /sys/bus/pci/slots/2/power,
     * which pciehp (pciehp_force=Y on the camera's cmdline) creates for a
     * hot-plug slot.  Slot cap: physical slot 2, no command-completed
     * events (NCCS), hot-plug capable, power controller.  Slot status:
     * presence detected (the reader is soldered on).
     */
    c[cap + 0] = 0x01420010;
    c[cap + 5] = (2u << 19) | BIT(18) | BIT(6) | BIT(1);
    /*
     * Slot power starts off (slot control bit 10): the camera's kernel does
     * not scan below the root port at boot (Sony hook 0x60121F70 in
     * pci_scan_bridge returns without pci_scan_child_bus), so the reader is
     * found only when pciehp powers the slot on (pciehp_force=Y enables an
     * occupied slot that is off; one that is already on is left alone,
     * "Already enabled on slot(2)", and never scanned).
     */
    c[cap + 6] = (BIT(6) << 16) | BIT(10);
    c[cap + 1] = 0x00008000;        /* device cap: role-based error reporting */
    c[cap + 3] = 0x00100011;        /* link cap: 2.5 GT/s x1, DLL active reporting */
    c[cap + 4] = 0x20110000;        /* link status: x1 2.5 GT/s, DLL active */
}

static bool a7c_pcie_rp_cap_write(A7cPcie *s, unsigned off, uint64_t value,
                                  unsigned size)
{
    unsigned reg = off & ~3u, idx = reg / 4;
    uint32_t v = a7c_merge(s->regs[idx], off, value, size);

    if (reg == A7C_PCIE_RP_CAP + 0x18) {
        /* slot control RW; slot status RW1C, presence detect state kept */
        uint32_t clr = ((uint32_t)value << ((off & 3) * 8)) >> 16;
        s->regs[idx] = (v & 0xFFFF) |
                       ((((s->regs[idx] >> 16) & ~clr) | BIT(6)) << 16);
        return true;
    }
    if (reg == A7C_PCIE_RP_CAP + 0x08 || reg == A7C_PCIE_RP_CAP + 0x10 ||
        reg == A7C_PCIE_RP_CAP + 0x1C) {
        /* device / link / root control: low half writable */
        s->regs[idx] = (s->regs[idx] & 0xFFFF0000u) | (v & 0xFFFF);
        return true;
    }
    return false;
}

static bool a7c_pcie_rp_ro(unsigned reg)
{
    /* root port: no BARs, no expansion ROM; ids/class/header read-only */
    return reg == 0x00 || reg == 0x08 || reg == 0x10 || reg == 0x14 ||
           reg == 0x34 || reg == 0x38 || reg >= A7C_PCIE_RP_CAP;
}

static void a7c_pcie_ep_update_bars(A7cPcieEp *e)
{
    bool mem = e->cfg[1] & 2;

    for (int i = 0; i < 2; i++) {
        uint32_t addr = e->cfg[4 + i] & ~0xFu;
        if (memory_region_is_mapped(&e->bar[i])) {
            memory_region_del_subregion(e->sysmem, &e->bar[i]);
        }
        if (mem && addr && addr != (e->bar_mask[i] & ~0xFu)) {
            memory_region_add_subregion_overlap(e->sysmem, addr, &e->bar[i], 2);
        }
    }
}

static uint32_t a7c_pcie_ep_read(A7cPcieEp *e, unsigned reg)
{
    return reg < 0x100 ? e->cfg[reg / 4] : 0;
}

static void a7c_pcie_ep_write(A7cPcieEp *e, unsigned off, uint64_t value,
                              unsigned size)
{
    unsigned reg = off & ~3u;
    uint32_t v;

    if (reg >= 0x100) {
        return;
    }
    v = a7c_merge(e->cfg[reg / 4], off, value, size);
    switch (reg) {
    case 0x04:                      /* command; status bits are RO here */
        e->cfg[1] = (e->cfg[1] & 0xFFFF0000u) | (v & 0x0547);
        a7c_pcie_ep_update_bars(e);
        return;
    case 0x10:
    case 0x14:
        e->cfg[reg / 4] = v & e->bar_mask[(reg - 0x10) / 4];
        a7c_pcie_ep_update_bars(e);
        return;
    case 0x0C:
        e->cfg[3] = (e->cfg[3] & ~0xFFu) | (v & 0xFF);   /* cache line only */
        return;
    case 0x3C:
        e->cfg[15] = (e->cfg[15] & ~0xFFu) | (v & 0xFF); /* interrupt line */
        return;
    default:
        return;                     /* everything else read-only */
    }
}

static uint64_t a7c_bar1_read(void *opaque, hwaddr offset, unsigned size)
{
    A7cPcieEp *e = opaque;
    return a7c_pick(e->bar1_store[(offset & 0x7FF) >> 2], offset, size);
}

static void a7c_bar1_write(void *opaque, hwaddr offset, uint64_t value,
                           unsigned size)
{
    A7cPcieEp *e = opaque;
    unsigned i = (offset & 0x7FF) >> 2;
    e->bar1_store[i] = a7c_merge(e->bar1_store[i], offset, value, size);
}

static const MemoryRegionOps a7c_bar1_ops = {
    .read = a7c_bar1_read,
    .write = a7c_bar1_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 1,
    .valid.max_access_size = 4,
    .impl.min_access_size = 1,
    .impl.max_access_size = 4,
};

static uint64_t a7c_pcie_read(void *opaque, hwaddr offset, unsigned size)
{
    A7cPcie *s = opaque;
    uint32_t v = s->regs[(offset & (A7C_PCIE_STRIDE - 1)) >> 2];

    if (offset == A7C_PCIE_CTRL_OFF) {
        v |= A7C_PCIE_LINK_UP;
    }
    if (s->root_port && offset < 0x100 && getenv("A7C_PCIE_TRACE")) {
        fprintf(stderr, "a7c pcie1 dbi rd %03x/%u = %08x\n", (unsigned)offset,
                size, a7c_pick(v, offset, size));
    }
    return a7c_pick(v, offset, size);
}

static void a7c_pcie_write(void *opaque, hwaddr offset, uint64_t value,
                           unsigned size)
{
    A7cPcie *s = opaque;
    size_t idx = (offset & (A7C_PCIE_STRIDE - 1)) >> 2;
    unsigned reg = offset & ~3u;

    if (s->root_port && reg >= A7C_PCIE_RP_CAP && reg < 0x100 &&
        a7c_pcie_rp_cap_write(s, offset, value, size)) {
        return;
    }
    if (s->root_port && reg < 0x100 && a7c_pcie_rp_ro(reg)) {
        return;
    }
    if (s->root_port && reg == 0x04) {
        /* command RW; status: capabilities list, error bits not modelled */
        s->regs[1] = (a7c_merge(s->regs[1], offset, value, size) & 0xFFFF) |
                     0x00100000;
        return;
    }
    if (s->root_port && reg == A7C_PCIE_ERR_OFF) {
        s->regs[idx] &= ~(uint32_t)value;   /* write 1 to clear */
        return;
    }
    s->regs[idx] = a7c_merge(s->regs[idx], offset, value, size);
}

static const MemoryRegionOps a7c_pcie_ops = {
    .read = a7c_pcie_read,
    .write = a7c_pcie_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
    .valid.min_access_size = 1,
    .valid.max_access_size = 4,
    .impl.min_access_size = 1,
    .impl.max_access_size = 4,
};

/* the endpoint answers type-0 accesses to slot 0 function 0 */
static A7cPcieEp *a7c_pcie_target(A7cPcie *s)
{
    uint32_t t = s->regs[A7C_PCIE_TARGET_OFF / 4];

    if (!s->ep || ((t >> 16) & 1) || (t & 0xFF) != 0) {
        return NULL;
    }
    return s->ep;
}

static uint64_t a7c_cfgwin_read(void *opaque, hwaddr offset, unsigned size)
{
    A7cPcie *s = opaque;
    A7cPcieEp *e = a7c_pcie_target(s);

    if (getenv("A7C_PCIE_TRACE")) {
        fprintf(stderr, "a7c pcie1 cfg rd target %08x off %03x/%u -> %s\n",
                s->regs[A7C_PCIE_TARGET_OFF / 4], (unsigned)offset, size,
                e ? "ep" : "none");
    }
    if (!e) {
        s->regs[A7C_PCIE_ERR_OFF / 4] |= 1;   /* unsupported request */
        return size >= 4 ? 0xFFFFFFFFu : (1u << (size * 8)) - 1;
    }
    return a7c_pick(a7c_pcie_ep_read(e, offset & 0xFFC), offset, size);
}

static void a7c_cfgwin_write(void *opaque, hwaddr offset, uint64_t value,
                             unsigned size)
{
    A7cPcie *s = opaque;
    A7cPcieEp *e = a7c_pcie_target(s);

    if (!e) {
        s->regs[A7C_PCIE_ERR_OFF / 4] |= 1;
        return;
    }
    a7c_pcie_ep_write(e, offset & 0xFFF, value, size);
}

static const MemoryRegionOps a7c_cfgwin_ops = {
    .read = a7c_cfgwin_read,
    .write = a7c_cfgwin_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 1,
    .valid.max_access_size = 4,
    .impl.min_access_size = 1,
    .impl.max_access_size = 4,
};

/*
 * Card detect (v1.13): QEMU's SDHCI raises "card inserted" only when the
 * card is plugged in, i.e. at machine creation, long before the camera
 * powers the reader and enables that interrupt.  A real controller reports
 * a card that is already in as soon as card-detect interrupts are enabled.
 * Every 20 ms: once the driver has enabled the insert interrupt
 * (NORINTSTSEN 0x34 bit 6), signal the insertion; again after it disables it.
 */
static void a7c_card_cd_tick(void *opaque)
{
    A7cPcieEp *e = opaque;
    uint64_t v = 0;

    memory_region_dispatch_read(e->sdhci_mr, 0x34, &v, MO_32,
                                MEMTXATTRS_UNSPECIFIED);
    if (!(v & 0x40)) {
        e->cd_signalled = false;
    } else if (!e->cd_signalled) {
        e->cd_signalled = true;
        sdbus_set_inserted(e->sdbus, true);
    }
    timer_mod(e->cd_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 20 * SCALE_MS);
}

/*
 * Card detect, v1.13: mcmn_drv.ko mcmnd_core_checkinsertedmedia (.text+0xBF0)
 * -> .text+0x828 reads the 32-bit input register at 0xF2915010 (a GPIO
 * bank beyond the 18 ports at 0xF2910000; "MMC0 CD/WP GPIO_IN") and takes
 * bit 16 = 1 as "MMC inserted" (bits 24/26 are the MS slot, .text+0x738).
 * The register reads 0 from the catch-all, so the camera saw no card and
 * powered the reader off.  Read-back storage, with bit 16 set while a card
 * image is attached.
 */
static uint32_t a7c_gpiox[0x100 / 4];
static bool a7c_card_present;

static uint64_t a7c_gpiox_read(void *opaque, hwaddr offset, unsigned size)
{
    uint32_t v = a7c_gpiox[(offset & 0xFF) >> 2];
    if ((offset & ~3) == 0x10 && a7c_card_present) {
        v |= BIT(16);
        /*
         * v1.20: bit 25 = the SD write-protect switch line
         * (mcmnd_core_getmmcwpdata .text+0x648: targets 0x20/0x101 -> bit 25,
         * 0x21/0x102 (MS) -> bit 27).  A7C_CARD_WP=1 sets it (polarity under test).
         */
        const char *wp = getenv("A7C_CARD_WP");
        if (wp && *wp == '1') {
            v |= BIT(25);
        }
    }
    if (getenv("A7C_PCIE_TRACE")) {
        fprintf(stderr, "a7c gpio-cd rd %02x = %08x\n", (unsigned)offset, v);
    }
    return a7c_pick(v, offset, size);
}

static void a7c_gpiox_write(void *opaque, hwaddr offset, uint64_t value,
                            unsigned size)
{
    unsigned i = (offset & 0xFF) >> 2;
    if (getenv("A7C_PCIE_TRACE")) {
        fprintf(stderr, "a7c gpio-cd wr %02x = %08x\n", (unsigned)offset,
                (unsigned)value);
    }
    a7c_gpiox[i] = a7c_merge(a7c_gpiox[i], offset, value, size);
}

static const MemoryRegionOps a7c_gpiox_ops = {
    .read = a7c_gpiox_read,
    .write = a7c_gpiox_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 1,
    .valid.max_access_size = 4,
    .impl.min_access_size = 1,
    .impl.max_access_size = 4,
};

static A7cPcieEp *a7c_create_card_reader(MemoryRegion *sysmem, qemu_irq irq)
{
    A7cPcieEp *e = g_new0(A7cPcieEp, 1);
    DeviceState *sdhci = qdev_new(TYPE_SYSBUS_SDHCI);

    e->sysmem = sysmem;
    e->cfg[0x00 / 4] = 0x85201217;  /* O2 Micro 8520 (first id in mmc_drv.ko) */
    e->cfg[0x04 / 4] = 0x00000000;
    e->cfg[0x08 / 4] = 0x08050101;  /* SD host controller, SDHCI w/ DMA, rev 1 */
    e->cfg[0x0C / 4] = 0x00000000;
    e->cfg[0x2C / 4] = 0x85201217;  /* subsystem ids [?] */
    e->cfg[0x3C / 4] = 0x00000100;  /* INTA */
    /*
     * mmc_drv.ko mmc_pci probe (.text+0x8738): 8750 -> chip type 2, 8751 -> 3,
     * 8520 -> type 1 only if config byte 0xDF is 0x11 or 0x12 ("FUJIN2"),
     * else "unsupported".  Which chip the A7C has is not known [?]:
     * A7C_CARD_ID=8750/8751 selects the others.
     */
    e->cfg[0xDC / 4] = 0x11000000;
    {
        const char *id = getenv("A7C_CARD_ID");
        if (id && *id) {
            e->cfg[0] = (strtoul(id, NULL, 16) << 16) | 0x1217;
        }
    }
    e->bar_mask[0] = ~(0x1000u - 1);
    e->bar_mask[1] = ~(0x800u - 1);

    /* BAR0: a standard SDHCI with no card inserted (no SD image) */
    qdev_prop_set_uint8(sdhci, "sd-spec-version", 3);
    qdev_prop_set_uint64(sdhci, "capareg", 0x05e830b0);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(sdhci), &error_fatal);
    sysbus_connect_irq(SYS_BUS_DEVICE(sdhci), 0, irq);
    memory_region_init(&e->bar[0], NULL, "a7c.card.bar0", 0x1000);
    memory_region_add_subregion(&e->bar[0], 0,
                                sysbus_mmio_get_region(SYS_BUS_DEVICE(sdhci), 0));
    /*
     * v1.13: a memory card in the reader when a second -drive if=sd
     * (index 1) is given (run.sh: A7C_SD_IMAGE, created blank by A7C_FULL).
     */
    {
        DriveInfo *di = drive_get(IF_SD, 0, 1);
        if (di) {
            DeviceState *card = qdev_new(TYPE_SD_CARD);
            qdev_prop_set_drive_err(card, "drive", blk_by_legacy_dinfo(di),
                                    &error_fatal);
            qdev_realize_and_unref(card, qdev_get_child_bus(sdhci, "sd-bus"),
                                   &error_fatal);
            e->sdbus = SD_BUS(qdev_get_child_bus(sdhci, "sd-bus"));
            e->sdhci_mr = sysbus_mmio_get_region(SYS_BUS_DEVICE(sdhci), 0);
            a7c_card_present = true;
            e->cd_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, a7c_card_cd_tick, e);
            timer_mod(e->cd_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                      100 * SCALE_MS);
        }
    }
    {
        MemoryRegion *gx = g_new0(MemoryRegion, 1);
        memory_region_init_io(gx, NULL, &a7c_gpiox_ops, NULL, "a7c.gpio-cd",
                              0x100);
        memory_region_add_subregion_overlap(sysmem, 0xF2915000, gx, 1);
    }
    /* BAR1: O2-specific registers, plain storage [?] */
    memory_region_init_io(&e->bar[1], NULL, &a7c_bar1_ops, e, "a7c.card.bar1",
                          0x800);
    return e;
}

static void a7c_create_pcie(MemoryRegion *sysmem, qemu_irq *pic)
{
    const char *cs = getenv("A7C_CARD_SLOT");
    bool card = cs && *cs && strcmp(cs, "0");

    for (int ch = 0; ch < 2; ch++) {
        A7cPcie *s = g_new0(A7cPcie, 1);
        memory_region_init_io(&s->mmio, NULL, &a7c_pcie_ops, s,
                              ch ? "a7c.pcie1" : "a7c.pcie0", A7C_PCIE_STRIDE);
        memory_region_add_subregion(sysmem, A7C_PCIE0_DBI + ch * A7C_PCIE_STRIDE,
                                    &s->mmio);
        if (ch == 1 && card) {
            s->root_port = true;
            a7c_pcie_rp_init(s);
            s->ep = a7c_create_card_reader(sysmem, pic[240 - 32]);
            memory_region_init_io(&s->cfgwin, NULL, &a7c_cfgwin_ops, s,
                                  "a7c.pcie1.cfg", 0x1000);
            memory_region_add_subregion(sysmem, A7C_PCIE_CFGWIN0 +
                                        ch * A7C_PCIE_CFGWIN_STRIDE, &s->cfgwin);
        }
    }
}

/*
 * Generic "storage plus hooks" register block used by the small A7C models
 * below: every register reads back what was last written, except where the
 * per-block read hook overrides it.
 */
typedef struct A7cRegs A7cRegs;
typedef uint32_t (*A7cReadHook)(A7cRegs *s, hwaddr off, uint32_t stored);
struct A7cRegs {
    MemoryRegion mmio;
    A7cReadHook read_hook;
    int64_t t0_ns;          /* free-running counter epoch (timer block) */
    uint32_t regs[0x1000 / 4];
};

static uint64_t a7c_regs_read(void *opaque, hwaddr off, unsigned size)
{
    A7cRegs *s = opaque;
    uint32_t v = s->regs[(off & 0xfff) >> 2];
    if (s->read_hook) {
        v = s->read_hook(s, off & 0xffc, v);
    }
    return v;
}

static void a7c_regs_write(void *opaque, hwaddr off, uint64_t value,
                           unsigned size)
{
    A7cRegs *s = opaque;
    s->regs[(off & 0xfff) >> 2] = (uint32_t)value;
}

static const MemoryRegionOps a7c_regs_ops = {
    .read = a7c_regs_read,
    .write = a7c_regs_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
};

static A7cRegs *a7c_create_regs(MemoryRegion *sysmem, const char *name,
                                hwaddr base, A7cReadHook hook)
{
    A7cRegs *s = g_new0(A7cRegs, 1);
    s->read_hook = hook;
    memory_region_init_io(&s->mmio, NULL, &a7c_regs_ops, s, name, 0x1000);
    memory_region_add_subregion(sysmem, base, &s->mmio);
    return s;
}

static bool a7c_evsel;
static uint64_t a7c_evsel_sources = UINT64_MAX;

static void a7c_get_evsel_sources(Object *obj, Visitor *v, const char *name,
                                  void *opaque, Error **errp)
{
    visit_type_uint64(v, name, &a7c_evsel_sources, errp);
}

static void a7c_set_evsel_sources(Object *obj, Visitor *v, const char *name,
                                  void *opaque, Error **errp)
{
    visit_type_uint64(v, name, &a7c_evsel_sources, errp);
}

/* Camera units ported from the a5000/a6400 projects: ADC, SA DSP cores, P2S. */
static bool a7c_units = true;

static bool a7c_get_units(Object *obj, Error **errp)
{
    return a7c_units;
}

static void a7c_set_units(Object *obj, bool v, Error **errp)
{
    a7c_units = v;
}

/*
 * ADC units 0/1 (0xF2406000 + 0x1000*u, IRQ 215/216): every channel reads 1023,
 * i.e. all resistor-ladder keys released.  SA DSP cores ("null SA": a started
 * function completes after delay-us and raises its IRQ) with the core ID
 * strings av-cam compares at init.  P2S sensor register writer (page
 * 0xF3C00000, IRQ 38).  Bases/IRQs as on the a5000 and a6400; all four are
 * referenced by the A7C av-cam.bin and active on the live camera.
 */
static struct {
    DeviceState *adc;               /* ADC unit 1 (0xF2407000) */
    uint16_t cakey;                 /* pressed CAkey1 bits */
    QKeyCode adc_owner[2];          /* key holding ch0 / ch3 */
    bool on;
    uint16_t wheel[2];              /* TPU counters 0xF2404014 + 0x100 * ch */
    int dial;                       /* mode dial position 0..9, -1 = none */
    int ev;                         /* exposure compensation dial -9..+9 */
} a7c_rear;
static const QemuInputHandler a7c_rear_handler;
static const MemoryRegionOps a7c_wheel_ops;
static void a7c_rear_reset(void *opaque);

static void a7c_create_camera_units(qemu_irq *pic)
{
    static const struct {
        hwaddr base;
        int irq[3];
        const char *id;
    } sa_cores[] = {
        { 0xF34A0000ULL, { 51, 52, 53 }, "ZMA0V1.0SA5_V1.0" },
        { 0xF34B0000ULL, { 54, 55, 56 }, "ZMA1V1.0SA5_V1.0" },
        { 0xF34C0000ULL, { 61, 62, 63 }, "SA0_V1.0SA5_V1.0" },
        { 0xF35C0000ULL, { 64, 65, 66 }, "SA1_V1.0SA5_V1.0" },
    };

    for (int u = 0; u < 2; u++) {
        DeviceState *adc = sysbus_create_simple("cxd90014-adc",
                                                0xF2406000ULL + u * 0x1000,
                                                pic[215 + u - 32]);
        if (u == 1) {
            const char *rc = getenv("A7C_REAR_CONTROLS");
            a7c_rear.adc = adc;
            if (rc && *rc && strcmp(rc, "0")) {
                a7c_rear.on = true;
                qemu_input_handler_register(NULL, &a7c_rear_handler);
                {
                    MemoryRegion *w = g_new0(MemoryRegion, 1);
                    memory_region_init_io(w, NULL, &a7c_wheel_ops, NULL,
                                          "a7c.wheel-counters", 0x200);
                    memory_region_add_subregion_overlap(get_system_memory(),
                                                        0xF2404000, w, -500);
                }
                qemu_register_reset(a7c_rear_reset, NULL);
            }
        }
    }
    for (int n = 0; n < ARRAY_SIZE(sa_cores); n++) {
        DeviceState *sa = qdev_new("cxd90014-sa");
        qdev_prop_set_uint32(sa, "index", n);
        qdev_prop_set_uint64(sa, "base", sa_cores[n].base);
        qdev_prop_set_string(sa, "core-id", sa_cores[n].id);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(sa), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(sa), 0, sa_cores[n].base);
        for (int k = 0; k < 3; k++) {
            sysbus_connect_irq(SYS_BUS_DEVICE(sa), k, pic[sa_cores[n].irq[k] - 32]);
        }
    }
    DeviceState *p2s = qdev_new("cxd90014-p2s");
    sysbus_realize_and_unref(SYS_BUS_DEVICE(p2s), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(p2s), 0, 0xF3C00000ULL);
    sysbus_connect_irq(SYS_BUS_DEVICE(p2s), 0, pic[38 - 32]);
}

static bool a7c_get_evsel(Object *obj, Error **errp)
{
    return a7c_evsel;
}

static void a7c_set_evsel(Object *obj, bool v, Error **errp)
{
    a7c_evsel = v;
}

/*
 * av-cam free-running timer, 0xF3C30000.  av-cam's get-time routine
 * (0x63784A34) is a bare read of +0xE0; its init writes +0xE8 = 0x17
 * (prescaler), +0xF8 = 0x3FF, then +0xEC = 1 (enable).  Every av-cam
 * polling loop measures its timeout with this counter, so a frozen value
 * turns each timeout into a hang (seen: pollingWait_Denali_0E8).
 * [rate assumed 1 MHz = 24 MHz / (0x17 + 1); makes the 2000001-tick
 *  Denali timeout 2 s]
 */
#define A7C_AVTMR_BASE   0xF3C30000ULL

static uint32_t a7c_avtmr_read(A7cRegs *s, hwaddr off, uint32_t v)
{
    if (off == 0xE0) {
        return (uint32_t)((qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) - s->t0_ns) /
                          1000);
    }
    return v;
}

/*
 * DFE DDR3 controller (Cadence Denali), 0xFC01C000.  "IMG:DFE DDR3 Init"
 * programs the timing registers, starts it (+0x000 = 0x700 ...) and
 * pollingWait_Denali_0E8 (0x637A51C4) waits for INT_STATUS (+0x0E8)
 * bit 16 for ID 1/2/3.  The model reports that bit as set: the DRAM behind
 * it is not modelled; anything that relies on its contents will show up
 * as the next stop point.
 */
#define A7C_DENALI_BASE  0xFC01C000ULL
#define A7C_DENALI_INT_DONE (1u << 16)

static uint32_t a7c_denali_read(A7cRegs *s, hwaddr off, uint32_t v)
{
    if (off == 0xE8) {
        return v | A7C_DENALI_INT_DONE;
    }
    return v;
}

/*
 * Catch-all for every peripheral not modelled yet, 0xF0000000..0xFCFFFFFF
 * at the lowest priority.  It is a sparse register file: a read returns
 * the last value written (0 if never written), which is what av-cam's
 * write-then-verify sequences expect (the a5000 project found the same for
 * AudioIp, docs/RE_AUDIO.md there).  Every access is logged with
 * "-d unimp" like TYPE_UNIMPLEMENTED_DEVICE, so tools/hwscan.sh still works.
 */
#define A7C_IO_BASE  0xF0000000ULL
#define A7C_IO_SIZE  (0xFD000000ULL - 0xF0000000ULL)

typedef struct A7cCatchall {
    MemoryRegion mmio;
    GHashTable *regs;           /* word offset -> value */
} A7cCatchall;

/*
 * Self-clearing trigger registers inside the catch-all: written 1 by the
 * firmware, hardware clears them when done, and the firmware busy-waits on
 * that.  With read-back they would spin forever.
 *   0xF3C081E0: av-cam 0x638659F4 (sensor-interface block, called from the
 *   evsel frame-event path) writes 1 and spins while it reads 1, then reads
 *   +0x1E4 - the "evsel stalls libgpe" hang (CPU2 pinned at 0x638659FE).
 */
static const hwaddr a7c_selfclear[] = {
    0xF3C081E0,
};

static uint64_t a7c_catchall_read(void *opaque, hwaddr off, unsigned size)
{
    A7cCatchall *s = opaque;

    for (size_t i = 0; i < ARRAY_SIZE(a7c_selfclear); i++) {
        if ((off & ~3) == a7c_selfclear[i] - A7C_IO_BASE) {
            g_hash_table_remove(s->regs, GUINT_TO_POINTER((uint32_t)(off >> 2)));
        }
    }
    uint32_t w = GPOINTER_TO_UINT(g_hash_table_lookup(s->regs,
                                  GUINT_TO_POINTER((uint32_t)(off >> 2))));
    uint64_t v = (w >> ((off & 3) * 8)) & (size == 4 ? 0xffffffffu :
                                           (1u << (size * 8)) - 1);
    qemu_log_mask(LOG_UNIMP, "a7c.io: unimplemented device read  "
                  "(size %u, offset 0x%07" HWADDR_PRIx ")\n", size, off);
    return v;
}

static void a7c_catchall_write(void *opaque, hwaddr off, uint64_t value,
                               unsigned size)
{
    A7cCatchall *s = opaque;
    gpointer key = GUINT_TO_POINTER((uint32_t)(off >> 2));
    uint32_t w = GPOINTER_TO_UINT(g_hash_table_lookup(s->regs, key));
    unsigned sh = (off & 3) * 8;
    uint32_t mask = size == 4 ? 0xffffffffu : ((1u << (size * 8)) - 1) << sh;

    w = (w & ~mask) | (((uint32_t)value << sh) & mask);
    g_hash_table_insert(s->regs, key, GUINT_TO_POINTER(w));
    qemu_log_mask(LOG_UNIMP, "a7c.io: unimplemented device write "
                  "(size %u, offset 0x%07" HWADDR_PRIx ", value 0x%0*" PRIx64
                  ")\n", size, off, size * 2, value);
}

static const MemoryRegionOps a7c_catchall_ops = {
    .read = a7c_catchall_read,
    .write = a7c_catchall_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
    .valid.min_access_size = 1,
    .valid.max_access_size = 4,
    .impl.min_access_size = 1,
    .impl.max_access_size = 4,
};

static void a7c_create_catchall(MemoryRegion *sysmem)
{
    A7cCatchall *s = g_new0(A7cCatchall, 1);
    s->regs = g_hash_table_new(g_direct_hash, g_direct_equal);
    memory_region_init_io(&s->mmio, NULL, &a7c_catchall_ops, s, "a7c.io",
                          A7C_IO_SIZE);
    memory_region_add_subregion_overlap(sysmem, A7C_IO_BASE, &s->mmio, -1000);
}

/*
 * ---------------------------------------------------------------------------
 * Sub-micro (power / front micro) HLE.
 *
 * av-cam talks to the camera's power-management micro over SIO with GPIO
 * handshake lines and 128-byte CRC-16 status frames.  The A7C's av-cam has
 * the same link driver as the a5000 (docs/RE_SUBMICRO_LINK.md in
 * a5000-emulator; same strings and Backup ids), wired differently.  A7C
 * board lines, read from av-cam's own globals in a running guest (they are
 * filled from Backup by 0x6376D618):
 *   strobe   F90465/66 = p0.8  (micro -> CPU, each edge = link event 0)
 *   request  F904CA/CB = p2.5  (micro -> CPU; low = frame exchange)
 *   event16  F9045F/60 = p0.9,  F90200/01 = p1.2,  S2REQ F90455/56 = p7.15
 *   SIO CS   F900B1/6D = p5.14, header channel F900D4 = 3; frames on ch0
 * Frames: the micro's side is your own camera's frames from its warm-boot
 * snapshot (nflasha5; RX slot A at PA 0x034433A4, slot B right after),
 * extracted by tools/a7c_extract.py with the "powering off" flag the
 * snapshot was taken with (slot B TLV 3 data[1] bit 6) cleared.  Slot A
 * byte 28 bit 0 = power lever (1 = ON, as in the snapshot); slot A bytes
 * 6..12 = the micro's battery-backed clock, served from the QEMU RTC.
 * Everything else (battery, temperatures, ...) is replayed as captured.
 * ---------------------------------------------------------------------------
 */
#define A7C_SM_STROBE   (0 * 32 + 8)    /* p0.8 */
#define A7C_SM_REQUEST  (2 * 32 + 5)    /* p2.5 */
#define A7C_SIO_BASE    0xF2405000ULL
#define A7C_GPIO_BASE   0xF2910000ULL

static struct {
    uint8_t frames[256];            /* RX slot A, RX slot B */
    uint8_t slot_c[128];            /* RX slot C (type 0x60), answer to 0x61 */
    bool have_frames;
    DeviceState *gpio;
    QEMUTimer *tick;
    int step;
    bool lever_on;
    /* clock */
    time_t rtc_offset;
    uint8_t rtc_set[6];
    bool rtc_set_valid;
    int rtc_ack;
    /* micro memory window (slot-A 0x41 packets) */
    bool rd_pending;
    uint8_t rd_hdr[5];
    uint8_t mem[4][0x180];
    /* power-off request from the CPU (slot-B TX TLV 0x0A data[1]) */
    uint8_t poff_req;
    bool poff_logged;
} a7c_sm;

static char *a7c_submicro_file;

static void a7c_sm_crc(uint8_t *f)
{
    uint16_t c = 0xffff;
    for (int i = 0; i < 126; i++) {
        c ^= f[i] << 8;
        for (int b = 0; b < 8; b++) {
            c = (c & 0x8000) ? (c << 1) ^ 0x1021 : c << 1;
        }
    }
    f[126] = c;
    f[127] = c >> 8;
}

/* micro clock: {yy, mon, day, hour, min, sec}, binary, year 2000 + yy */
static void a7c_sm_rtc_now(uint8_t *b)
{
    struct tm tm;

    qemu_get_timedate(&tm, a7c_sm.rtc_offset);
    /* [? range] kept inside 2020..2037 like the a5000 clamp (its udate
     * rewrites the clock when outside the Backup default year .. 2037) */
    int year = MIN(MAX(tm.tm_year + 1900, 2020), 2037);
    b[0] = year - 2000;
    b[1] = tm.tm_mon + 1;
    b[2] = tm.tm_mday;
    b[3] = tm.tm_hour;
    b[4] = tm.tm_min;
    b[5] = tm.tm_sec;
}

/* CPU "set clock": TX slot A (0x21) bytes 4..10 = {2, yy..ss}; echoed
 * until the CPU drops the request (a5000 RE_RTC.md section 7) */
static void a7c_sm_rtc_tx(const uint8_t *tx)
{
    const uint8_t *w = tx + 5;
    struct tm want = { 0 }, now;

    if (tx[0] != 0x21) {
        return;
    }
    if (tx[4] != 2) {
        a7c_sm.rtc_ack = 0;
        a7c_sm.rtc_set_valid = false;
        return;
    }
    if ((a7c_sm.rtc_set_valid && !memcmp(a7c_sm.rtc_set, w, 6)) ||
        w[1] < 1 || w[1] > 12 || w[2] < 1 || w[2] > 31 || w[3] > 23 ||
        w[4] > 59 || w[5] > 59) {
        return;
    }
    want.tm_year = 100 + w[0];
    want.tm_mon = w[1] - 1;
    want.tm_mday = w[2];
    want.tm_hour = w[3];
    want.tm_min = w[4];
    want.tm_sec = w[5];
    qemu_get_timedate(&now, 0);
    a7c_sm.rtc_offset = mktimegm(&want) - mktimegm(&now);
    memcpy(a7c_sm.rtc_set, w, 6);
    a7c_sm.rtc_set_valid = true;
    a7c_sm.rtc_ack = 20;
    info_report("sony-a7c: sub-micro clock set to 20%02u-%02u-%02u "
                "%02u:%02u:%02u", w[0], w[1], w[2], w[3], w[4], w[5]);
}

/*
 * Slot-A 0x41 = CPU memory request: bytes 1..4 micro address, byte 5 =
 * length (bit 7 = write, data at 6..); answered in the next exchange with
 * type 0x40.  Region table from the a5000 (av-cam 0x43EA797C there) [? A7C
 * table not checked]; contents unknown, zero-filled.
 */
static const struct { uint32_t addr, len; } a7c_sm_regions[4] = {
    { 0x00010100, 0x080 }, { 0x00020280, 0x180 },
    { 0x20000500, 0x100 }, { 0x00030100, 0x100 },
};

static uint8_t *a7c_sm_mem(uint32_t addr, int n)
{
    for (int i = 0; i < 4; i++) {
        uint32_t off = addr - a7c_sm_regions[i].addr;
        if (addr >= a7c_sm_regions[i].addr && off + n <= a7c_sm_regions[i].len) {
            return a7c_sm.mem[i] + off;
        }
    }
    return NULL;
}

/*
 * Rear controls (v1.11, A7C_REAR_CONTROLS=1 or A7C_FULL=1), the method of
 * bionz-x-research qemu-a6400 E0573, with the A7C's own key table (guest
 * /proc/udm/input, names from libObj.so's key catalog):
 *   ADC unit 1 ch3 (port 0x0F): ENTER 0-0x5B, UP 0x5C-0x111, LEFT 0x112-0x1B7,
 *                               DOWN 0x1B8-0x257, RIGHT 0x258-0x304
 *   ADC unit 1 ch0 (port 0x0C): FN 0-0x5B, DELETE 0x1B8-0x257
 *   slot A CAkey1 (bytes 28/29): MENU bit 2, PLAY bit 1 (high = pressed)
 *   GPIO P0.10 (low = pressed): MOVIE (KEY_TACT_STARTSTOP)
 * Host keys: Enter, arrows, M = MENU, P = PLAY, F = Fn, Delete, R = MOVIE.
 */

/* centre of band k (k = 0 lowest) of the 5-band ladders and 10-band dials */
static const uint16_t a7c_band5[5] = { 45, 182, 356, 520, 686 };
static const uint16_t a7c_band10[10] = {
    29, 116, 232, 346, 460, 572, 684, 797, 909, 1010,
};

/*
 * Mode dial bands (v1.13, from screenshots of each band): band index
 * 0..4 = port 0x10 bands 0..4, 5..9 = port 0x11 bands 0..4.
 *   0 P, 1 AUTO, 2 MR3, 3 MR2, 4 MR1, 5 S&Q, 6 movie, 7 M, 8 S, 9 A
 */
static const int a7c_dial_key[10] = { 1, 0, 9, 8, 7, 4, 3, 2, 6, 5 };
static const char *const a7c_dial_name[10] = {
    "P", "AUTO", "MR3", "MR2", "MR1", "SQ", "MOVIE", "M", "S", "A",
};

static int a7c_dial_parse(const char *v)
{
    if (!v || !*v) {
        return 0;                   /* P */
    }
    for (int i = 0; i < 10; i++) {
        if (!g_ascii_strcasecmp(v, a7c_dial_name[i])) {
            return i;
        }
    }
    return !strcmp(v, "none") ? -1 : 0;
}

static void a7c_rear_dial(int pos)
{
    a7c_rear.dial = pos;
    if (!a7c_rear.adc) {
        return;
    }
    cxd90014_adc_set(a7c_rear.adc, 4, pos >= 0 && pos < 5 ? a7c_band5[pos] : 1023);
    cxd90014_adc_set(a7c_rear.adc, 5, pos >= 5 ? a7c_band5[pos - 5] : 1023);
}

static void a7c_rear_ev(int ev)
{
    a7c_rear.ev = ev;
    if (!a7c_rear.adc) {
        return;
    }
    /* table rows: value 9 = band 0 (0-0x3A) ... value 0 = band 9 (released) */
    cxd90014_adc_set(a7c_rear.adc, 6, ev > 0 ? a7c_band10[9 - ev] : 1023);
    cxd90014_adc_set(a7c_rear.adc, 7, ev < 0 ? a7c_band10[9 + ev] : 1023);
}

/* the rest of the TPU block keeps read-back storage, as the catch-all did */
static uint32_t a7c_tpu_store[0x200 / 4];

static uint64_t a7c_wheel_read(void *opaque, hwaddr offset, unsigned size)
{
    unsigned ch = offset >> 8;
    if (ch < 2 && (offset & 0xFF) == 0x14) {
        return a7c_rear.wheel[ch];
    }
    return a7c_tpu_store[(offset & 0x1FF) >> 2] >> ((offset & 3) * 8);
}

static void a7c_wheel_write(void *opaque, hwaddr offset, uint64_t value,
                            unsigned size)
{
    unsigned ch = offset >> 8, i = (offset & 0x1FF) >> 2;
    if (ch < 2 && (offset & 0xFF) == 0x14) {
        a7c_rear.wheel[ch] = value;
        return;
    }
    a7c_tpu_store[i] = a7c_merge(a7c_tpu_store[i], offset, value, size);
}

static const MemoryRegionOps a7c_wheel_ops = {
    .read = a7c_wheel_read,
    .write = a7c_wheel_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 2,
    .valid.max_access_size = 4,
};

static void a7c_rear_key_do(QKeyCode code, bool down)
{
    static const struct {
        QKeyCode key;
        int ch;
        uint16_t level;
    } adc_keys[] = {
        { Q_KEY_CODE_RET,    3, 45 },
        { Q_KEY_CODE_KP_ENTER, 3, 45 },
        { Q_KEY_CODE_UP,     3, 182 },
        { Q_KEY_CODE_LEFT,   3, 356 },
        { Q_KEY_CODE_DOWN,   3, 520 },
        { Q_KEY_CODE_RIGHT,  3, 686 },
        { Q_KEY_CODE_F,      0, 45 },
        { Q_KEY_CODE_DELETE, 0, 520 },
    };
    uint16_t bit = 0;

    /*
     * v1.12: shutter, control wheel, mode dial, exposure dial.
     *   S1 (half press) = GPIO P0.1, S2 (full press) = P1.5, active low:
     *     the a5000's wiring (a5000-emulator RE_ENGINE_TICK.md §12.1); both
     *     are high on the switched-on A7C (live GPIO) [? not traced in the
     *     A7C av-cam].  Host: H = half press, Space = full press (S1 + S2).
     *   Control wheel: av-cam 0x63AA9508 reads the 16-bit counter at
     *     0xF2404014 + (ch << 8) (same as the a6400, bionz-x E0573);
     *     channel 0 [?].  Host: [ and ] = one click each way.
     *   Mode dial: ADC unit 1 ch4/ch5 (ports 0x10/0x11), five bands each
     *     (KEY_POS_MODE_DIAL 3,1,0x14,0x13,7 / 0x15,0xA,6,4,5); which band
     *     is which position is not decoded [?].  Host: 1..9, 0 = positions.
     *   Exposure compensation dial: ports 0x12 (+1..+9) / 0x13 (-1..-9),
     *     released = 0.  Host: + / - one step.
     */
    if (down) {
        switch (code) {
        case Q_KEY_CODE_BRACKET_LEFT:
        case Q_KEY_CODE_BRACKET_RIGHT:
            a7c_rear.wheel[0] += code == Q_KEY_CODE_BRACKET_RIGHT ? 1 : -1;
            return;
        case Q_KEY_CODE_1 ... Q_KEY_CODE_0:
            /* keys 1..9, 0 = AUTO P A S M MR1 MR2 MR3 movie S&Q */
            a7c_rear_dial(a7c_dial_key[code == Q_KEY_CODE_0 ? 9
                                       : code - Q_KEY_CODE_1]);
            return;
        case Q_KEY_CODE_EQUAL:
        case Q_KEY_CODE_KP_ADD:
            a7c_rear_ev(MIN(a7c_rear.ev + 1, 9));
            return;
        case Q_KEY_CODE_MINUS:
        case Q_KEY_CODE_KP_SUBTRACT:
            a7c_rear_ev(MAX(a7c_rear.ev - 1, -9));
            return;
        default:
            break;
        }
    }
    /*
     * v1.13, GPIO scan with qom-set /machine/gpio poke (lines that are high
     * on the switched-on camera, pulled low one at a time):
     *   P0.2 low -> the screen changes to the exposure scale view, back on
     *     release: taken as AEL (default "AEL hold") [?];
     *   P0.3 low -> "This operation or setting not available: Focus Mode
     *     Continuous AF": taken as C1 [?].
     * Host: A = AEL, C = C1.
     */
    if (a7c_sm.gpio && (code == Q_KEY_CODE_A || code == Q_KEY_CODE_C)) {
        qemu_set_irq(qdev_get_gpio_in(a7c_sm.gpio, code == Q_KEY_CODE_A ? 2 : 3),
                     !down);
        return;
    }
    if (a7c_sm.gpio) {
        if (code == Q_KEY_CODE_H || code == Q_KEY_CODE_SPC) {
            qemu_set_irq(qdev_get_gpio_in(a7c_sm.gpio, 0 * 32 + 1), !down);
            if (code == Q_KEY_CODE_SPC) {
                qemu_set_irq(qdev_get_gpio_in(a7c_sm.gpio, 1 * 32 + 5),
                             !down);
            }
            return;
        }
    }

    switch (code) {
    case Q_KEY_CODE_M:
        bit = BIT(2);
        break;
    case Q_KEY_CODE_P:
        bit = BIT(1);
        break;
    case Q_KEY_CODE_R:
        if (a7c_sm.gpio) {
            qemu_set_irq(qdev_get_gpio_in(a7c_sm.gpio, 10), !down);
        }
        return;
    default:
        break;
    }
    if (bit) {
        a7c_rear.cakey = down ? (a7c_rear.cakey | bit)
                                   : (a7c_rear.cakey & ~bit);
        return;
    }
    for (size_t i = 0; i < ARRAY_SIZE(adc_keys); i++) {
        QKeyCode *owner;

        if (adc_keys[i].key != code || !a7c_rear.adc) {
            continue;
        }
        owner = &a7c_rear.adc_owner[adc_keys[i].ch != 0];
        if (down) {
            *owner = code;
            cxd90014_adc_set(a7c_rear.adc, adc_keys[i].ch, adc_keys[i].level);
        } else if (*owner == code) {
            *owner = Q_KEY_CODE_UNMAPPED;
            cxd90014_adc_set(a7c_rear.adc, adc_keys[i].ch, 1023);
        }
        return;
    }
}

/*
 * v1.19: minimum press length in the camera's own (virtual) time.
 * The ADC/CAkey/GPIO keys are levels the camera samples; how long a host
 * press lasts in guest time depends on how fast the guest runs (icount: an
 * idle guest jumps ahead), so a fixed host hold was missed at times and
 * auto-repeated at others.  A release that comes sooner than
 * A7C_KEY_MS (default 150) virtual ms after the press is delayed until then;
 * a longer host hold stays a hold.
 */
static struct {
    int64_t down_ns[Q_KEY_CODE__MAX];
    bool pending[Q_KEY_CODE__MAX];
    int64_t min_ns;
    QEMUTimer *timer;
} a7c_keyhold;

static void a7c_keyhold_tick(void *opaque)
{
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL), next = INT64_MAX;

    for (int c = 0; c < Q_KEY_CODE__MAX; c++) {
        if (!a7c_keyhold.pending[c]) {
            continue;
        }
        int64_t due = a7c_keyhold.down_ns[c] + a7c_keyhold.min_ns;
        if (due <= now) {
            a7c_keyhold.pending[c] = false;
            a7c_rear_key_do(c, false);
        } else {
            next = MIN(next, due);
        }
    }
    if (next != INT64_MAX) {
        timer_mod(a7c_keyhold.timer, next);
    }
}

static void a7c_rear_key(DeviceState *dev, QemuConsole *src, InputEvent *event)
{
    InputKeyEvent *key = event->u.key.data;
    QKeyCode code = qemu_input_key_value_to_qcode(key->key);
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    if (!a7c_keyhold.timer) {
        const char *ms = getenv("A7C_KEY_MS");
        a7c_keyhold.min_ns = (ms ? atoi(ms) : 150) * (int64_t)SCALE_MS;
        a7c_keyhold.timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, a7c_keyhold_tick, NULL);
    }
    if (code >= Q_KEY_CODE__MAX) {
        return;
    }
    if (key->down) {
        if (a7c_keyhold.pending[code]) {
            /* pressed again before the delayed release: release first */
            a7c_keyhold.pending[code] = false;
            a7c_rear_key_do(code, false);
        }
        a7c_keyhold.down_ns[code] = now;
        a7c_rear_key_do(code, true);
        return;
    }
    if (now - a7c_keyhold.down_ns[code] < a7c_keyhold.min_ns) {
        a7c_keyhold.pending[code] = true;
        a7c_keyhold_tick(NULL);
        return;
    }
    a7c_rear_key_do(code, false);
}

static const QemuInputHandler a7c_rear_handler = {
    .name = "Sony A7C rear controls",
    .mask = INPUT_EVENT_MASK_KEY,
    .event = a7c_rear_key,
};

static void a7c_rear_reset(void *opaque)
{
    a7c_rear.cakey = 0;
    a7c_rear.wheel[0] = a7c_rear.wheel[1] = 0;
    {
        a7c_rear_dial(a7c_dial_parse(getenv("A7C_MODE_DIAL")));
    }
    a7c_rear_ev(0);
    memset(a7c_rear.adc_owner, 0, sizeof(a7c_rear.adc_owner));
    if (a7c_rear.adc) {
        cxd90014_adc_set(a7c_rear.adc, 0, 1023);
        cxd90014_adc_set(a7c_rear.adc, 3, 1023);
    }
}

static void a7c_sm_slot_a(const uint8_t *tx, uint8_t *out)
{
    if (a7c_sm.rd_pending) {
        uint32_t addr = ldl_le_p(a7c_sm.rd_hdr);
        bool wr = a7c_sm.rd_hdr[4] & 0x80;
        int n = wr ? 0 : MIN(a7c_sm.rd_hdr[4] & 0x7f, 0x78);
        uint8_t *m = n ? a7c_sm_mem(addr, n) : NULL;

        memset(out, 0, 128);
        out[0] = 0x40;
        memcpy(out + 1, a7c_sm.rd_hdr, 4);
        out[5] = n;
        if (m) {
            memcpy(out + 6, m, n);
        }
    } else {
        memcpy(out, a7c_sm.frames, 128);
        out[28] = (out[28] & ~1) | a7c_sm.lever_on;
        /*
         * v1.11: front keys (CAkey1, guest /proc/udm/input FRONT(CA) port
         * 6000) are slot-A bytes 28/29 as on the a5000/a6400: bit 0 POWER
         * (the lever, forced on above), bit 1 PLAY and bit 2 MENU (high =
         * pressed), bit 9 flash pop-up and bit 10 BT-LE power (low =
         * pressed).  Checked: MENU opens the camera's menu.
         */
        out[28] |= a7c_rear.cakey & 0x06;
        out[29] &= ~(a7c_rear.cakey >> 8);
        out[6] = 2;                     /* clock valid */
        if (a7c_sm.rtc_ack > 0) {
            a7c_sm.rtc_ack--;
            memcpy(out + 7, a7c_sm.rtc_set, 6);
        } else {
            a7c_sm_rtc_now(out + 7);
        }
    }
    a7c_sm_crc(out);
    /* this TX: a memory request is answered in the next exchange */
    a7c_sm.rd_pending = tx[0] == 0x41;
    if (a7c_sm.rd_pending) {
        memcpy(a7c_sm.rd_hdr, tx + 1, 5);
        if (tx[5] & 0x80) {
            int n = MIN(tx[5] & 0x7f, 0x78);
            uint8_t *m = a7c_sm_mem(ldl_le_p(tx + 1), n);
            if (m) {
                memcpy(m, tx + 6, n);
            }
        }
    }
}

/* TLV @id in a slot-B frame: {id, len, 0, 0, data[len]} from byte 4 */
static uint8_t *a7c_sm_tlv(uint8_t *f, int id, int minlen)
{
    int end = MIN(4 + f[2], 126);

    for (int o = 4; o + 4 <= end; o += 4 + f[o + 1]) {
        if (f[o] == id) {
            return f[o + 1] >= minlen && o + 4 + minlen <= end ? f + o + 4
                                                                 : NULL;
        }
    }
    return NULL;
}

/*
 * Power mode (a5000 RE_REBOOT.md A.2/A.3): the CPU asks for cold power-off
 * with TX TLV 0x0A data[1] bit 6 (0x0D09 with bit 1); the micro confirms in
 * RX TLV 0x03 data[1] bit 6 (bit 0).  Acknowledged while requested.
 */
static void a7c_sm_slot_b(uint8_t *tx, uint8_t *out)
{
    uint8_t *t;

    if (tx[0] == 0x31 && (t = a7c_sm_tlv(tx, 0x0a, 2))) {
        a7c_sm.poff_req = t[1] & 0x42;
    }
    memcpy(out, a7c_sm.frames + 128, 128);
    if (a7c_sm.poff_req && (t = a7c_sm_tlv(out, 0x03, 2))) {
        t[1] |= (a7c_sm.poff_req & 0x40) | ((a7c_sm.poff_req & 0x02) >> 1);
        if (!a7c_sm.poff_logged) {
            a7c_sm.poff_logged = true;
            info_report("sony-a7c: camera asked the sub-micro to power off "
                        "(TLV 0x0a data[1] = 0x%02x)", a7c_sm.poff_req);
        }
    }
    a7c_sm_crc(out);
}

/*
 * SIO ch0: the second power-micro channel ("0x39" protocol, header builders in
 * av-cam 0x638755A2 / 0x63875650).  Every request starts with 0x39, byte 1 is the
 * command: 0x0A query, 0x0E start, 0x0F write a data chunk (address in bytes 2-3,
 * XOR check bytes), 0x0D finish (sequence in byte 3).  The transfer buffer is shared
 * TX/RX, so with nothing attached the CPU read its own request back as the reply,
 * decided the micro needs its program and uploaded ~26 KB that was never accepted
 * (then error 9).  Replies seen on the live camera (dump live-mem-20261117-120716,
 * RX buffer after the 0x0A template at av-cam 0x643AD6B0):
 *   0x0A query -> FF 7F 00 00 00 00 00 AA
 * Anything else: zeros until its reply is known.
 */
static unsigned a7c_ch0_log;

/*
 * Note (v1.6): the ch0 upload (0x0F chunks) is the micro's own program.  av-cam
 * stops with error 9 when ch3 status traffic arrives while that program is still
 * being loaded (link handler 0x6386D110: "waiting" flag 0x6453B935 set, upload
 * state word 0x643AD988 > 4, link state [0x644C4736] == 3).  Holding the micro
 * HLE back alone did not help: the replies must also carry the micro's running
 * checksum of the program (v1.7, below); with both, the script (step counter
 * 0x6453B9E2) runs to step 7.  See docs §7.11-7.12.
 */
static bool a7c_ch0_upload_seen;
static uint8_t a7c_ch0_seq;     /* last sequence number seen in a 0x0D poll */
static uint16_t a7c_ch0_sum;    /* 16-bit sum of the uploaded program (BE halfwords) */

/*
 * Run-time exchange after the start-up (v1.8): the CPU sends 128-byte
 * commands 0x06, 0x07, 0x08 (39 0N 0N 00 00 30 00 01 ...) in turn, each
 * followed by a 2-byte F5 00.  Their replies land in av-cam 0x6414DCA0
 * (06), 0x6414DBA0 (07) and 0x6414DD20 (08).  Until the real replies are
 * known they are zeros; A7C_CH0_FILE=<capture>/pa_0414db00_ch0_buffers.bin
 * (live-capture tool, VA 0x6414DB00..0x6414DE00) replays the camera's own.
 */
static uint8_t a7c_ch0_rt[4][128];
static bool a7c_ch0_have_rt;
/* offsets in the capture (VA 0x6414DB00 +): replies to 05, 06, 07, 08 */
static const unsigned a7c_ch0_rt_off[4] = { 0x020, 0x1A0, 0x0A0, 0x220 };

static void a7c_ch0_load_rt(void)
{
    const char *fn = getenv("A7C_CH0_FILE");
    g_autofree uint8_t *f = NULL;
    gsize len;

    if (!fn || !*fn) {
        return;
    }
    if (!g_file_get_contents(fn, (gchar **)&f, &len, NULL) || len < 0x2A0) {
        error_report("sony-a7c: A7C_CH0_FILE=%s: need the 768-byte "
                     "pa_0414db00_ch0_buffers.bin from the live-capture tool", fn);
        exit(1);
    }
    for (int i = 0; i < 4; i++) {
        memcpy(a7c_ch0_rt[i], f + a7c_ch0_rt_off[i], 128);
    }
    a7c_ch0_have_rt = true;
    info_report("sony-a7c: power-chip ch0 replies 05..08 from %s", fn);
}
static int64_t a7c_ch0_last_chunk_ns;

static void a7c_ch0_xfer(void *opaque, int ch, uint8_t *buf, int len)
{
    static const uint8_t query_reply[8] = { 0xFF, 0x7F, 0, 0, 0, 0, 0, 0xAA };
    uint8_t cmd = len > 1 ? buf[1] : 0;
    uint8_t tx3 = len > 3 ? buf[3] : 0;
    bool known = buf[0] == 0x39;

    if (a7c_ch0_log < 400 && (cmd != 0x0F || a7c_ch0_log < 8)) {
        a7c_ch0_log++;
        if (getenv("A7C_CH0_LOG")) {
            fprintf(stderr, "a7c ch0: len %d tx %02x %02x %02x %02x %02x %02x %02x %02x\n",
                    len, buf[0], buf[1], buf[2], buf[3], buf[4], buf[5], buf[6], buf[7]);
        }
    }
    if (known && cmd >= 0x05 && cmd <= 0x08 && len >= 128 &&
        getenv("A7C_SM_TXLOG")) {
        static uint8_t last[4][128];
        static bool seen[4];
        int k = cmd - 0x05;
        if (!seen[k] || memcmp(last[k], buf, 126)) {
            seen[k] = true;
            memcpy(last[k], buf, 128);
            fprintf(stderr, "a7c ch0 tx %02x t=%" PRId64 "ms:", cmd,
                    qemu_clock_get_ms(QEMU_CLOCK_VIRTUAL));
            for (int i = 0; i < 126; i++) {
                fprintf(stderr, "%s%02x", (i % 16) ? "" : " ", buf[i]);
            }
            fprintf(stderr, "\n");
        }
    }
    uint16_t add = 0;
    if (known && cmd == 0x0F && len > 8) {
        /*
         * 0x0F chunk: 39 0F arg code cnt_hi cnt_lo data[len-8] chk chk.  The CPU's
         * chunk builder (0x638755A2) adds the data as big-endian halfwords into
         * 0x644C4F7A; reply handler 0x63876BA4 compares that with the running
         * sum the micro returns in rx[4..5].  The micro can only report what it
         * had before this transfer, so the new data is added after the reply.
         */
        for (int i = 0; i < len - 8; i++) {
            add += (i & 1) ? buf[6 + i] : (uint16_t)(buf[6 + i] << 8);
        }
        a7c_ch0_upload_seen = true;
        a7c_ch0_last_chunk_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    }
    if (known && cmd == 0x0E) {
        a7c_ch0_sum = 0;
    }
    memset(buf, 0, len);
    if (known && cmd >= 0x05 && cmd <= 0x08 && len >= 128) {
        /*
         * av-cam 0x6387176E checks a reply with 0x63D77EC0(buf, 0x40): the
         * XOR of its 64 little-endian halfwords must be 0x0F0F, and byte 2
         * must echo the command.  Without a capture: an empty valid reply.
         */
        uint16_t x = 0;

        if (a7c_ch0_have_rt) {
            memcpy(buf, a7c_ch0_rt[cmd - 0x05], 128);
        } else {
            buf[2] = cmd;
        }
        buf[0] = buf[1] = 0;            /* the CPU clears them before the check */
        for (int i = 0; i < 126; i += 2) {
            x ^= lduw_le_p(buf + i);
        }
        stw_le_p(buf + 126, x ^ 0x0F0F);
    } else if (known && cmd == 0x0A && len >= 8) {
        memcpy(buf, query_reply, sizeof(query_reply));
    } else if (known && len >= 8) {
        /*
         * av-cam 0x63876B00 accepts a reply when ((rx[3] ^ (seq << 4)) & 0x7F) == 0,
         * seq = av-cam 0x6453B9DF, the sequence byte the 0x0D polls carry in tx[3]
         * (39 0D 00 seq arg 00 chk chk).  The same check follows the 0x0E start
         * commands, whose tx[3] is 0, so the model keeps the last poll's seq.
         * A wrong reply makes the CPU repeat start + polls and, after 29 tries,
         * give up (state 7, event 0x3A, later error 9).
         */
        if (cmd == 0x0D) {
            a7c_ch0_seq = tx3;
        }
        buf[3] = (a7c_ch0_seq << 4) & 0x7F;
        buf[4] = a7c_ch0_sum >> 8;
        buf[5] = a7c_ch0_sum & 0xFF;
    }
    a7c_ch0_sum += add;
}

/*
 * The micro is busy loading its program while 0x0F chunks arrive: hold its
 * status exchange back (ch3 traffic then is error 9, see docs §7.10) until the
 * chunks have stopped for 300 ms.
 */
static bool a7c_ch0_loading(void)
{
    return a7c_ch0_upload_seen &&
           qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) - a7c_ch0_last_chunk_ns <
           300 * SCALE_MS;
}

/* debug (v1.16): A7C_SM_TXLOG=1 prints each CPU frame type when it changes */
static void a7c_sm_txlog(const uint8_t *tx)
{
    static uint8_t last[3][128];
    static bool seen[3];
    int k = tx[0] == 0x21 ? 0 : tx[0] == 0x61 ? 1 : tx[0] == 0x31 ? 2 : -1;

    if (k < 0 || !getenv("A7C_SM_TXLOG")) {
        return;
    }
    /* ignore the CRC (126/127) */
    if (seen[k] && !memcmp(last[k], tx, 126)) {
        return;
    }
    seen[k] = true;
    memcpy(last[k], tx, 128);
    fprintf(stderr, "a7c sm tx %02x t=%" PRId64 "ms:", tx[0],
            qemu_clock_get_ms(QEMU_CLOCK_VIRTUAL));
    for (int i = 0; i < 126; i++) {
        fprintf(stderr, "%s%02x", (i % 16) ? "" : " ", tx[i]);
    }
    fprintf(stderr, "\n");
}

static void a7c_sm_xfer(void *opaque, int ch, uint8_t *buf, int len)
{
    uint8_t out[256];

    if (len < 128) {
        memset(buf, 0, len);            /* 8-byte header phase: idle */
        return;
    }
    a7c_sm_txlog(buf);
    if (buf[0] == 0x31) {
        a7c_sm_slot_b(buf, out);
    } else if (buf[0] == 0x61 && getenv("A7C_SLOTC")) {
        /*
         * v1.9: the A7C also exchanges a third frame, CPU 0x61 / micro 0x60
         * ("slot C", av-cam RX 0x63443424, between slot A and slot B; same
         * layout as the a6400 machine in bionz-x-research).  Opt-in
         * (A7C_SLOTC=1, set by A7C_FULL=1): once slot C is answered the app
         * stops powering itself off (OFF_BY_KEY) and starts the memory-card
         * reader, which needs A7C_CARD_SLOT=1 (PCIe root port + reader; without
         * it the kernel BUG()s at pcie.c:841).  Docs §7.13-7.14.
         */
        memcpy(out, a7c_sm.slot_c, 128);
        {
            /* experiment: A7C_SLOTC_HEX=<hex bytes from offset 0> */
            const char *hx = getenv("A7C_SLOTC_HEX");
            for (int i = 0; hx && hx[0] && hx[1] && i < 126; i++, hx += 2) {
                unsigned v;
                if (sscanf(hx, "%2x", &v) == 1) {
                    out[i] = v;
                }
            }
        }
        a7c_sm_crc(out);
    } else {
        a7c_sm_rtc_tx(buf);
        a7c_sm_slot_a(buf, out);
    }
    if (len > 128) {                    /* 256-byte exchange: A then B */
        a7c_sm_slot_b(buf + 128, out + 128);
    }
    memcpy(buf, out, MIN(len, 256));
}

/*
 * The micro strobes p0.8 while its request line p2.5 stays low (frame
 * exchange).  The strobe must be LOW when av-cam's engine tick samples it,
 * otherwise the CPU skips its own slot-A exchange ("FrtComSkip"): 1 ms high
 * every 50 ms, as in the a5000 HLE.
 */
static void a7c_sm_tick(void *opaque)
{
    if (a7c_ch0_loading()) {
        /* strobe high = the CPU skips its slot-A exchange */
        qemu_set_irq(qdev_get_gpio_in(a7c_sm.gpio, A7C_SM_STROBE), 1);
        a7c_sm.step = 1;
        timer_mod(a7c_sm.tick, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + SCALE_MS);
        return;
    }
    qemu_set_irq(qdev_get_gpio_in(a7c_sm.gpio, A7C_SM_REQUEST), 0);
    a7c_sm.step ^= 1;
    qemu_set_irq(qdev_get_gpio_in(a7c_sm.gpio, A7C_SM_STROBE), a7c_sm.step);
    timer_mod(a7c_sm.tick, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
              (a7c_sm.step ? SCALE_MS : 49 * SCALE_MS));
}

static void a7c_sm_reset(void *opaque)
{
    a7c_sm.step = 0;
    a7c_ch0_upload_seen = false;
    a7c_ch0_seq = 0;
    a7c_ch0_sum = 0;
    a7c_ch0_last_chunk_ns = 0;
    a7c_sm.rd_pending = false;
    a7c_sm.poff_req = 0;
    a7c_sm.poff_logged = false;
    timer_mod(a7c_sm.tick, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
              50 * SCALE_MS);
}

static char *a7c_get_submicro(Object *obj, Error **errp)
{
    return g_strdup(a7c_submicro_file);
}

static void a7c_set_submicro(Object *obj, const char *v, Error **errp)
{
    g_free(a7c_submicro_file);
    a7c_submicro_file = g_strdup(v);
}

/* GPIO 0xF2910000 + SIO 0xF2405000 (models from a5000-emulator) + micro */
static void a7c_create_gpio_sio_submicro(qemu_irq *pic)
{
    DeviceState *gpio = qdev_new("cxd90014-gpio");
    DeviceState *sio = qdev_new("cxd90014-sio");

    qdev_prop_set_string(gpio, "board", "a7c");
    /* /machine/gpio, for qom-set /machine/gpio poke P.N=L (debug, v1.13) */
    object_property_add_child(qdev_get_machine(), "gpio", OBJECT(gpio));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(gpio), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(gpio), 0, A7C_GPIO_BASE);
    for (int n = 0; n < 24; n++) {
        /* p0.0..15 -> 121+pin, p0.16 -> 144, p1.0..6 -> 137+pin */
        int irq = n < 16 ? 121 + n : n == 16 ? 144 : 137 + (n - 17);
        sysbus_connect_irq(SYS_BUS_DEVICE(gpio), n, pic[irq - 32]);
    }

    sysbus_realize_and_unref(SYS_BUS_DEVICE(sio), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(sio), 0, A7C_SIO_BASE);
    for (int c = 0; c < 5; c++) {
        sysbus_connect_irq(SYS_BUS_DEVICE(sio), c, pic[201 + c - 32]);
    }

    a7c_sm.gpio = gpio;
    if (a7c_submicro_file) {
        g_autofree uint8_t *f = NULL;
        gsize len;

        if (!g_file_get_contents(a7c_submicro_file, (gchar **)&f, &len, NULL)
            || len < 256) {
            error_report("sony-a7c: submicro=%s: need the 256-byte frame file "
                         "made by tools/a7c_extract.py", a7c_submicro_file);
            exit(1);
        }
        memcpy(a7c_sm.frames, f, 256);
        /*
         * Slot C: bytes 256..383 of the file when present (live-capture tool
         * v3 writes A, B, C), otherwise the live camera's frame
         * (live-mem-20261117-190338: 60 00 00 00 07 11, rest zero; an empty
         * 0x60 frame also stops the power-off).
         */
        if (len >= 384) {
            memcpy(a7c_sm.slot_c, f + 256, 128);
        } else {
            static const uint8_t c_head[6] = { 0x60, 0, 0, 0, 0x07, 0x11 };
            memset(a7c_sm.slot_c, 0, 128);
            memcpy(a7c_sm.slot_c, c_head, sizeof(c_head));
        }
        a7c_sm.have_frames = true;
        a7c_sm.lever_on = true;
        /*
         * A7C: headers and 128-byte frames both run on ch3 (trace
         * cxd_sio_xfer: ~3800 ch3 exchanges, CPU byte 0 = 0x31); ch0
         * carries another device (byte 0 = 0x39), so it is not attached.
         */
        cxd90014_sio_attach(sio, 3, a7c_sm_xfer, NULL);
        cxd90014_sio_attach(sio, 0, a7c_ch0_xfer, NULL);
        a7c_ch0_load_rt();
        a7c_sm.tick = timer_new_ms(QEMU_CLOCK_VIRTUAL, a7c_sm_tick, NULL);
        a7c_sm.tick->scale = SCALE_NS;
        qemu_register_reset(a7c_sm_reset, NULL);
    }
}

/*
 * Bootloader stand-in: what Sony's bootloader copies from flash into RAM
 * before the kernel runs (the kernel and modules assume it is there):
 *
 *   /setting/Backup.bin -> PA = its header word 0 (0x02E80000 on the A7C);
 *       backup.ko is loaded with Backup_load=0 and reads it from RAM.
 *       Without it backup_init() fails and ~15 modules miss Backup_* symbols.
 *   /system/av-cam.bin  -> its link base, from its own vector table
 *       (+0x0C = entry(pre) VA 0x635C6044 -> base VA 0x635C6000 = PA
 *       0x035C6000); liro.ko checks the "LIRO" magic at adrs=035c6000.
 *
 * Done at every reset (the real bootloader runs on every power-on), reading
 * the eMMC user area through the drive: the Sony "82461.00" partition table
 * at LBA 0 (48 entries at +0x20: start, size in bytes), then the FAT root
 * directory of nflasha2 (/setting) and nflasha3 (/system).  Disable with
 * -M sony-a7c,bootloader=off (then load the files with -device loader).
 */
#define A7C_EMMC_BOOT_PART   (4 * MiB)     /* nflashaB0 / nflashaB1 */
#define A7C_PART_SETTING     2
#define A7C_PART_SYSTEM      3
#define A7C_KERNEL_VA_OFFSET 0x60000000u   /* PAGE_OFFSET - PHYS_OFFSET */

static bool a7c_bootloader = true;
static BlockBackend *a7c_emmc_blk;

static bool a7c_get_bootloader(Object *obj, Error **errp)
{
    return a7c_bootloader;
}

static void a7c_set_bootloader(Object *obj, bool v, Error **errp)
{
    a7c_bootloader = v;
}

static bool a7c_emmc_read(uint64_t off, void *buf, size_t len)
{
    /* user area follows the two boot partitions in the image */
    return blk_pread(a7c_emmc_blk, 2 * A7C_EMMC_BOOT_PART + off, len, buf,
                     0) >= 0;
}

static bool a7c_sony_partition(int n, uint64_t *start, uint64_t *size)
{
    uint8_t hdr[0x400];

    if (!a7c_emmc_read(0, hdr, sizeof(hdr)) || memcmp(hdr, "82461.00", 8)) {
        return false;
    }
    *start = ldl_le_p(hdr + 0x20 + 16 * (n - 1));
    *size = ldl_le_p(hdr + 0x20 + 16 * (n - 1) + 4);
    return *size != 0;
}

/* Minimal FAT12/16 root-directory reader (8.3 names); NULL if not found. */
static uint8_t *a7c_fat_read(uint64_t base, const char *name83,
                             uint32_t *out_len)
{
    uint8_t bs[512];
    g_autofree uint8_t *fat = NULL, *dir = NULL;
    uint32_t bps, spc, res, nfat, nroot, spf, tot, root_off, data_off;
    uint32_t nclus, csz;
    bool fat12;

    if (!a7c_emmc_read(base, bs, sizeof(bs)) || bs[510] != 0x55 ||
        bs[511] != 0xAA) {
        return NULL;
    }
    bps = lduw_le_p(bs + 11);
    spc = bs[13];
    res = lduw_le_p(bs + 14);
    nfat = bs[16];
    nroot = lduw_le_p(bs + 17);
    tot = lduw_le_p(bs + 19) ? lduw_le_p(bs + 19) : ldl_le_p(bs + 32);
    spf = lduw_le_p(bs + 22);
    if (!bps || !spc || !spf || !nroot || (bps & 511)) {
        return NULL;                        /* not FAT12/16 */
    }
    root_off = (res + nfat * spf) * bps;
    data_off = root_off + DIV_ROUND_UP(nroot * 32, bps) * bps;
    nclus = (tot - data_off / bps) / spc;
    fat12 = nclus < 4085;
    csz = spc * bps;

    fat = g_malloc(spf * bps);
    dir = g_malloc(nroot * 32);
    if (!a7c_emmc_read(base + res * bps, fat, spf * bps) ||
        !a7c_emmc_read(base + root_off, dir, nroot * 32)) {
        return NULL;
    }
    for (uint32_t i = 0; i < nroot; i++) {
        uint8_t *e = dir + 32 * i;
        uint32_t clus, size, done = 0, end = fat12 ? 0xFF8 : 0xFFF8;
        uint8_t *buf;

        if (e[0] == 0) {
            break;
        }
        if (e[0] == 0xE5 || (e[11] & 0x08) || memcmp(e, name83, 11)) {
            continue;
        }
        clus = lduw_le_p(e + 26);
        size = ldl_le_p(e + 28);
        buf = g_malloc(ROUND_UP(size, csz) + csz);
        while (clus >= 2 && clus < end && done < size) {
            if (!a7c_emmc_read(base + data_off + (uint64_t)(clus - 2) * csz,
                               buf + done, csz)) {
                g_free(buf);
                return NULL;
            }
            done += csz;
            if (fat12) {
                uint16_t v = lduw_le_p(fat + clus + clus / 2);
                clus = (clus & 1) ? v >> 4 : v & 0xFFF;
            } else {
                clus = lduw_le_p(fat + 2 * clus);
            }
        }
        if (done < size) {
            g_free(buf);
            return NULL;
        }
        *out_len = size;
        return buf;
    }
    return NULL;
}

static bool a7c_bootload_file(int part, const char *name83, const char *what,
                              hwaddr (*place)(const uint8_t *, uint32_t))
{
    uint64_t start, size;
    uint32_t len = 0;
    g_autofree uint8_t *data = NULL;
    hwaddr pa;

    if (!a7c_sony_partition(part, &start, &size)) {
        warn_report("sony-a7c bootloader: no nflasha%d in the eMMC partition "
                    "table (not an A7C flash image?)", part);
        return false;
    }
    data = a7c_fat_read(start, name83, &len);
    if (!data) {
        warn_report("sony-a7c bootloader: %s not found on nflasha%d", what,
                    part);
        return false;
    }
    pa = place(data, len);
    if (pa == (hwaddr)-1 || pa + len > CXD90045_RAM0_SIZE) {
        warn_report("sony-a7c bootloader: %s: bad load address", what);
        return false;
    }
    address_space_write(&address_space_memory, pa, MEMTXATTRS_UNSPECIFIED,
                        data, len);
    return true;
}

static hwaddr a7c_place_backup(const uint8_t *d, uint32_t len)
{
    return len >= 4 ? ldl_le_p(d) : (hwaddr)-1;
}

static hwaddr a7c_place_avcam(const uint8_t *d, uint32_t len)
{
    uint32_t entry_pre;

    if (len < 0x40 || memcmp(d + 4, "ORIL", 4)) {   /* LE "LIRO" magic */
        return (hwaddr)-1;
    }
    entry_pre = ldl_le_p(d + 0x0C);
    return (entry_pre & ~0xFFFu) - A7C_KERNEL_VA_OFFSET;
}

static void a7c_bootloader_reset(void *opaque)
{
    if (!a7c_bootloader || !a7c_emmc_blk) {
        return;
    }
    a7c_bootload_file(A7C_PART_SETTING, "BACKUP  BIN", "/setting/Backup.bin",
                      a7c_place_backup);
    a7c_bootload_file(A7C_PART_SYSTEM, "AV-CAM  BIN", "/system/av-cam.bin",
                      a7c_place_avcam);
}

/*
 * Optional bootloader hand-off page (-M sony-a7c,handoff=FILE).
 *
 * Sony's bootloader leaves a 4 KiB page at PA 0x20021000 (cmdline
 * wbimem=0x1000@0x20021000; pm.resume=0x20021054, pm.sus_error=0x20021060,
 * wbi_memclr=0x200210e8, em_console=0x200210ec).  Captured from the running
 * camera (docs/A7C_BRINGUP.md section 10.2) it holds the WBI resume vector,
 * the DDR-init log and a register list.  The model leaves it zero by
 * default; with handoff= it writes FILE (at most 4 KiB, e.g.
 * dump/live-mem-.../pa_20021000_handoff.bin) there at every reset.
 * Experimental: what the kernel does with these values on a cold boot has
 * not been tested.
 */
#define A7C_HANDOFF_PA   0x20021000ULL
#define A7C_HANDOFF_MAX  0x1000

static char *a7c_handoff_file;
static uint8_t *a7c_handoff_data;
static gsize a7c_handoff_len;

static char *a7c_get_handoff(Object *obj, Error **errp)
{
    return g_strdup(a7c_handoff_file);
}

static void a7c_set_handoff(Object *obj, const char *v, Error **errp)
{
    g_free(a7c_handoff_file);
    a7c_handoff_file = g_strdup(v);
}

static void a7c_handoff_reset(void *opaque)
{
    address_space_write(&address_space_memory, A7C_HANDOFF_PA,
                        MEMTXATTRS_UNSPECIFIED, a7c_handoff_data,
                        a7c_handoff_len);
}

static void a7c_handoff_init(void)
{
    if (!a7c_handoff_file || !*a7c_handoff_file) {
        return;
    }
    if (!g_file_get_contents(a7c_handoff_file, (gchar **)&a7c_handoff_data,
                             &a7c_handoff_len, NULL)
        || a7c_handoff_len == 0 || a7c_handoff_len > A7C_HANDOFF_MAX) {
        error_report("sony-a7c: handoff=%s: need a file of 1..4096 bytes "
                     "(the captured page at PA 0x20021000)", a7c_handoff_file);
        exit(1);
    }
    qemu_register_reset(a7c_handoff_reset, NULL);
    info_report("sony-a7c: hand-off page %s (%u bytes) -> PA 0x%llx at reset",
                a7c_handoff_file, (unsigned)a7c_handoff_len,
                (unsigned long long)A7C_HANDOFF_PA);
}

/*
 * Sony boot contract (boot/loadaddr.txt "0x00108000,0x00700000,0x00103000",
 * E0425): the raw kernel Image runs at physical 0x00108000 (TEXT_OFFSET),
 * the initrd sits at 0x00700000, and r2 points at a NUL-terminated
 * "kemco ..." parameter string at 0x00103000 instead of ATAGS/DTB.
 * setup_arch() checks strncmp(ptr, "kemco", 5) and parses from ptr+5.
 * QEMU's generic arm_load_kernel() cannot express this layout, so the
 * machine loads the images itself and enters the kernel on CPU reset.
 */
#define CXD90045_KERNEL_LOAD     0x00108000ULL
#define CXD90045_INITRD_LOAD     0x00700000ULL
#define CXD90045_INITRD_MAX      0x00800000ULL
#define CXD90045_BOOT_PARAMS     0x00103000ULL
#define CXD90045_BOOT_PARAMS_MAX (CXD90045_KERNEL_LOAD - CXD90045_BOOT_PARAMS)

/*
 * Boot stubs in the reserved first megabyte (memrsv=1M@0), standing in for
 * what the boot ROM / bootloader does before the kernel runs (E0522):
 *
 *   CPU0 (CXD90045_STUB0): program GICD_ITARGETSR8..63, then r0 = 0,
 *     r1 = machine id, r2 = kemco block, jump to the kernel.  Sony's
 *     gic_dist_init never writes SPI targets and its irq_select_affinity
 *     (0x6011feb0) only reads the ITARGETSR byte back into the affinity
 *     mask, so the targets must already be set when the kernel starts.
 *   CPU1..3 (CXD90045_SMPSTUB): WFE until cxd900x0_smp_init stores the
 *     physical cxd900x0_secondary_entry at 0xFE005010 + 4 * (cpu - 1)
 *     and issues SEV, then jump there.  The kernel's own park loop then
 *     waits for pen_release (boot_secondary).
 *
 * Encodings assembled with arm-linux-gnueabi-as (receipt in E0522).
 */
#define CXD90045_STUB0           0x00001000ULL
#define CXD90045_SMPSTUB         0x00001200ULL
#define CXD90045_SMP_MBOX        0xFE005010ULL

static const uint32_t cxd90045_stub0[23] = {
    0xe59f3038,             /* ldr r3, =0xF8001820 (ITARGETSR8)  */
    0xe59f4038,             /* ldr r4, =0xF8001900               */
    0xe28f604c,             /* add r6, pc, #0x4c (table @ +0x5c) */
    0xe4965004,             /* 1: ldr r5, [r6], #4               */
    0xe4835004,             /* str r5, [r3], #4                  */
    0xe1530004,             /* cmp r3, r4                        */
    0x3afffffb,             /* blo 1b                            */
    0xe59f3024,             /* ldr r3, =0xF8001C18 (ICFGR6)      */
    0xe59f5024,             /* ldr r5, =0x28A0                   */
    0xe5835000,             /* str r5, [r3]                      */
    0xe59f5020,             /* ldr r5, =0x00800028               */
    0xe5835008,             /* str r5, [r3, #8] (ICFGR8)         */
    0xe3a00000,             /* mov r0, #0                        */
    0xe59f1018,             /* ldr r1, =CXD90045_MACHINE_ID      */
    0xe59f2018,             /* ldr r2, =CXD90045_BOOT_PARAMS     */
    0xe3a0f942,             /* mov pc, #0x108000 (kernel entry)  */
    0xf8001820,
    0xf8001900,
    0xf8001c18,
    0x000028a0,             /* IRQ 98, 99, 101, 102 edge-triggered (a5000 stub) */
    0x00800028,             /* IRQ 129, 130, 139 edge: sub-micro GPIO strobes */
    CXD90045_MACHINE_ID,
    0x00103000,
};

static const uint32_t cxd90045_smpstub[11] = {
    0xee103fb0,             /* mrc p15, 0, r3, c0, c0, 5 (MPIDR) */
    0xe2033003,             /* and r3, r3, #3                    */
    0xe2433001,             /* sub r3, r3, #1                    */
    0xe59f2014,             /* ldr r2, =0xFE005010               */
    0xe0822103,             /* add r2, r2, r3, lsl #2            */
    0xe320f002,             /* 1: wfe                            */
    0xe5920000,             /* ldr r0, [r2]                      */
    0xe3500000,             /* cmp r0, #0                        */
    0x0afffffb,             /* beq 1b                            */
    0xe12fff10,             /* bx r0                             */
    0xfe005010,
};

/*
 * SPI target bytes for IRQ 32..255.  Per-CPU sources come from this kernel:
 * clockevent channels ch0/ch2/ch3/ch4 = IRQ 153/155/156/157 for CPU0..3
 * (table 0x604d8d80) and hwtimer0..3 = ch5..8 = IRQ 158..161
 * (hwtimer_devs 0x603c9354; hwtimerN serving CPU N is inferred, and agrees
 * with the a5000 real-camera GICD dump in Oein/a5000-emulator).  Every
 * other SPI targets CPU0 [assumption: no CXD90045 GICD dump yet].
 */
static void cxd90045_spi_targets(uint32_t *words, unsigned ncpu)
{
    uint8_t t[256 - 32];
    static const struct { int irq; unsigned cpu; } percpu[] = {
        { 153, 0 }, { 155, 1 }, { 156, 2 }, { 157, 3 },
        { 158, 0 }, { 159, 1 }, { 160, 2 }, { 161, 3 },
    };

    memset(t, 0x01, sizeof(t));
    for (size_t i = 0; i < ARRAY_SIZE(percpu); i++) {
        if (percpu[i].cpu < ncpu) {
            t[percpu[i].irq - 32] = 1u << percpu[i].cpu;
        }
    }
    for (int i = 0; i < 56; i++) {
        words[i] = cpu_to_le32(t[4 * i] | (t[4 * i + 1] << 8) |
                               (t[4 * i + 2] << 16) |
                               ((uint32_t)t[4 * i + 3] << 24));
    }
}

static void sony_a7c_write_stubs(unsigned ncpu)
{
    uint32_t blob0[23 + 56];
    uint32_t smp[11];

    for (int i = 0; i < 23; i++) {
        blob0[i] = cpu_to_le32(cxd90045_stub0[i]);
    }
    cxd90045_spi_targets(&blob0[23], ncpu);
    rom_add_blob_fixed("sony_a7c.stub0", blob0, sizeof(blob0),
                       CXD90045_STUB0);
    for (int i = 0; i < 11; i++) {
        smp[i] = cpu_to_le32(cxd90045_smpstub[i]);
    }
    rom_add_blob_fixed("sony_a7c.smpstub", smp, sizeof(smp),
                       CXD90045_SMPSTUB);
}

static void sony_a7c_cpu_reset(void *opaque)
{
    ARMCPU *cpu = opaque;
    CPUState *cs = CPU(cpu);

    cpu_reset(cs);
    if (cs->cpu_index == 0) {
        /*
         * A reset sends the secondaries back to the mailbox loop; a stale
         * entry left by the previous kernel must not release them early.
         * SRAM is zero at power-on, which is the state reproduced here.
         */
        uint32_t mbox[3] = { 0 };
        address_space_write(&address_space_memory, CXD90045_SMP_MBOX,
                            MEMTXATTRS_UNSPECIFIED, mbox, sizeof(mbox));
        cpu_set_pc(cs, CXD90045_STUB0);
    } else {
        cpu_set_pc(cs, CXD90045_SMPSTUB);
    }
}

static void sony_a7c_load_boot_contract(MachineState *machine,
                                          ARMCPU **cpus, unsigned ncpu)
{
    const char *append = machine->kernel_cmdline ? machine->kernel_cmdline : "";
    g_autofree char *params = NULL;
    ssize_t size;

    for (unsigned i = 0; i < ncpu; i++) {
        qemu_register_reset(sony_a7c_cpu_reset, cpus[i]);
    }
    sony_a7c_write_stubs(ncpu);
    if (!machine->kernel_filename) {
        return;
    }

    size = load_image_targphys(machine->kernel_filename, CXD90045_KERNEL_LOAD,
                               CXD90045_INITRD_LOAD - CXD90045_KERNEL_LOAD);
    if (size <= 0) {
        error_report("sony-a7c: could not load kernel '%s'",
                     machine->kernel_filename);
        exit(1);
    }

    if (machine->initrd_filename) {
        size = load_image_targphys(machine->initrd_filename,
                                   CXD90045_INITRD_LOAD, CXD90045_INITRD_MAX);
        if (size <= 0) {
            error_report("sony-a7c: could not load initrd '%s'",
                         machine->initrd_filename);
            exit(1);
        }
    }

    params = g_str_has_prefix(append, "kemco") ? g_strdup(append)
                                                : g_strdup_printf("kemco %s", append);
    if (strlen(params) + 1 > CXD90045_BOOT_PARAMS_MAX) {
        error_report("sony-a7c: kemco boot parameters too long");
        exit(1);
    }
    rom_add_blob_fixed("sony_a7c.kemco", params, strlen(params) + 1,
                       CXD90045_BOOT_PARAMS);
}

static void a7c_create_fbview(MachineState *machine)
{
    DeviceState *fb = qdev_new("sony-fbview");
    object_property_add_child(OBJECT(machine), "fbview", OBJECT(fb));
    object_property_set_str(OBJECT(fb), "fb", "ppf", &error_fatal);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(fb), &error_fatal);
}

static void sony_a7c_init(MachineState *machine)
{
    MemoryRegion *sysmem = get_system_memory();
    MemoryRegion *ram0 = g_new(MemoryRegion, 1);
    MemoryRegion *ram1 = g_new(MemoryRegion, 1);
    MemoryRegion *sram = g_new(MemoryRegion, 1);
    MemoryRegion *crcdc_mmio = g_new(MemoryRegion, 1);
    MemoryRegion *l2c_mmio = g_new(MemoryRegion, 1);
    CxdRwStub *sugi_clk;
    DeviceState *a9mp;
    SysBusDevice *sbd;
    qemu_irq pic[CXD90045_NUM_IRQS];
    unsigned ncpu = machine->smp.cpus;
    ARMCPU *cpus[CXD90045_MAX_CPUS];
    DeviceState *timer;
    int i;

    for (unsigned c = 0; c < ncpu; c++) {
        Object *cpuobj = object_new(machine->cpu_type);
        object_property_set_bool(cpuobj, "has_el3", false, &error_fatal);
        if (object_property_find(cpuobj, "reset-cbar")) {
            object_property_set_int(cpuobj, "reset-cbar",
                                    CXD90045_PERIPH_BASE, &error_fatal);
        }
        /* secondaries run from reset too; they park in the mailbox stub */
        qdev_realize(DEVICE(cpuobj), NULL, &error_fatal);
        cpus[c] = ARM_CPU(cpuobj);
    }

    memory_region_init_ram(ram0, NULL, "sony_a7c.ram0",
                           CXD90045_RAM0_SIZE, &error_fatal);
    memory_region_add_subregion(sysmem, CXD90045_RAM0_BASE, ram0);

    memory_region_init_ram(ram1, NULL, "sony_a7c.ram1",
                           CXD90045_RAM1_SIZE, &error_fatal);
    memory_region_add_subregion(sysmem, CXD90045_RAM1_BASE, ram1);

    memory_region_init_ram(sram, NULL, "sony_a7c.sram",
                           CXD90045_SRAM_SIZE, &error_fatal);
    memory_region_add_subregion(sysmem, CXD90045_SRAM_BASE, sram);

    /*
     * A7C: the kernel maps 0xFD000000..0xFEFFFFFF 1:1 (gva2gpa) and av-cam
     * keeps live data well past the 1 MiB SRAM, e.g. a pointer table at
     * 0xFE5F0B00 (+0x4948..0x4960 feed the image-pipeline objects; a NULL
     * there crashed liro-kliro_65 at 0x63DAF23A).  Back the rest of the
     * window with RAM at low priority so modelled devices still win.
     * [size/contents of the real on-chip memory: not yet known]
     */
    {
        MemoryRegion *xram = g_new(MemoryRegion, 1);
        memory_region_init_ram(xram, NULL, "sony_a7c.fe_xram",
                               0xFF000000ULL - (CXD90045_SRAM_BASE + CXD90045_SRAM_SIZE),
                               &error_fatal);
        memory_region_add_subregion_overlap(sysmem,
                                            CXD90045_SRAM_BASE + CXD90045_SRAM_SIZE,
                                            xram, -1);
    }

    a9mp = qdev_new(TYPE_A9MPCORE_PRIV);
    qdev_prop_set_uint32(a9mp, "num-cpu", ncpu);
    qdev_prop_set_uint32(a9mp, "num-irq", CXD90045_NUM_IRQS);
    sbd = SYS_BUS_DEVICE(a9mp);
    sysbus_realize_and_unref(sbd, &error_fatal);
    sysbus_mmio_map(sbd, 0, CXD90045_PERIPH_BASE);
    for (unsigned c = 0; c < ncpu; c++) {
        sysbus_connect_irq(sbd, c,
                           qdev_get_gpio_in(DEVICE(cpus[c]), ARM_CPU_IRQ));
        sysbus_connect_irq(sbd, c + ncpu,
                           qdev_get_gpio_in(DEVICE(cpus[c]), ARM_CPU_FIQ));
    }

    for (i = 0; i < CXD90045_NUM_IRQS - 32; i++) {
        pic[i] = qdev_get_gpio_in(a9mp, i);
    }

    {
        DeviceState *uart = qdev_new(TYPE_PL011);
        qdev_prop_set_chr(uart, "chardev", serial_hd(0));
        qdev_prop_set_bit(uart, "tx-level-irq", true);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(uart), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(uart), 0, CXD90045_UART0_BASE);
        sysbus_connect_irq(SYS_BUS_DEVICE(uart), 0, pic[CXD90045_UART0_SPI]);
    }

    timer = qdev_new("cxd4115-timer");
    sysbus_realize_and_unref(SYS_BUS_DEVICE(timer), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(timer), 0, CXD90045_TIMER_BASE);
    for (i = 0; i < 16; i++) {
        sysbus_connect_irq(SYS_BUS_DEVICE(timer), i,
                           pic[CXD90045_TIMER_IRQ0 + i - 32]);
    }

    memory_region_init_io(crcdc_mmio, NULL, &cxd90045_crcdc_ops, NULL,
                          "cxd90045.crcdc", 0x1000);
    memory_region_add_subregion(sysmem, CXD90045_CRCDC_BASE, crcdc_mmio);

    memory_region_init_io(l2c_mmio, NULL, &cxd90045_l2c_stub_ops, NULL,
                          "cxd90045.l2c_stub", 0x1000);
    memory_region_add_subregion(sysmem, CXD90045_L2C_STUB_BASE, l2c_mmio);

    /*
     * GPU: DMP SMAPH-S30 "SUGILITE" (grm_gles.ko / libObj rm_*), the same
     * core and driver contract as the a5000; functional model from
     * a5000-emulator (PSC fill, PPF transfer, P3D renderer + shaders).
     */
    sysbus_create_simple("dmp-smaph-s30", CXD90045_GPES_BAR_BASE,
                         pic[CXD90045_GPES_SPI]);

    /*
     * Debug screen (QEMU console 0): the OSD frames im.elf hands to the
     * display path, i.e. the latest linear PPF destination (640x480).
     * Not hardware: no MMIO, the firmware cannot see it.
     * HMP: qom-set /machine/fbview fb "addr,w,h,stride,fmt" to pin a buffer.
     */
    /*
     * A7C_SCREEN=vph (set by A7C_FULL): the VPH scan-out becomes console 0
     * (the window's first tab) and this viewer console 1.  The PPF viewer
     * shows whichever buffer was transferred last, which alternates with an
     * empty one once the UI runs; the VPH shows what the camera scans out.
     */
    const char *scr = getenv("A7C_SCREEN");
    bool vph_first = scr && !strcmp(scr, "vph");
    if (!vph_first) {
        a7c_create_fbview(machine);
    }

    sugi_clk = cxd90045_create_rw_stub(sysmem, "cxd90045.sugi_clk",
                                       CXD90045_SUGI_CLK_BASE, 0x10000);
    /* sugilite_clock_up_internal expects 0x15 at 0xF3400080 */
    sugi_clk->regs[0x80 >> 2] = 0x15;

    cxd90045_create_rw_stub(sysmem, "cxd90045.dfs_ctrl",
                            CXD90045_DFS_CTRL_BASE, 0x1000);

    cxd90045_create_xdmac(sysmem, CXD90045_XDMAC0_BASE,
                          &pic[CXD90045_XDMAC0_SPI_BASE]);
    cxd90045_create_xdmac(sysmem, CXD90045_XDMAC1_BASE,
                          &pic[CXD90045_XDMAC1_SPI_BASE]);
    cxd90045_create_ldec(sysmem, CXD90045_LDEC_BASE,
                         pic[CXD90045_LDEC_SPI]);

    {
        static const hwaddr f2a_bases[] = {
            CXD90045_F2A01_BASE, 0xF2A11000ULL, 0xF2A12000ULL,
            0xF2A21000ULL, 0xF2A22000ULL, 0xF2A30000ULL
        };
        for (size_t k = 0; k < ARRAY_SIZE(f2a_bases); k++) {
            uint32_t *regs = g_new0(uint32_t, 0x400);
            MemoryRegion *mmio = g_new(MemoryRegion, 1);
            memory_region_init_io(mmio, NULL, &cxd90045_f2a01_ops, regs,
                                  "cxd90045.f2a_clk", 0x1000);
            memory_region_add_subregion(sysmem, f2a_bases[k], mmio);
        }
    }

    {
        DeviceState *sdhci = qdev_new(TYPE_SYSBUS_SDHCI);
        DriveInfo *di = drive_get(IF_SD, 0, 0);
        BlockBackend *blk = di ? blk_by_legacy_dinfo(di) : NULL;
        qdev_prop_set_uint8(sdhci, "sd-spec-version", 3);
        qdev_prop_set_uint64(sdhci, "capareg", 0x05e830b0);
        sbd = SYS_BUS_DEVICE(sdhci);
        sysbus_realize_and_unref(sbd, &error_fatal);
        sysbus_mmio_map(sbd, 0, CXD90045_EMMC0_BASE);
        sysbus_connect_irq(sbd, 0, pic[CXD90045_EMMC0_SPI]);
        if (blk) {
            DeviceState *card = qdev_new(TYPE_EMMC);
            qdev_prop_set_uint64(card, "boot-partition-size", 4 * MiB);
            qdev_prop_set_uint8(card, "boot-config", 0x08);
            /*
             * Real eMMC parts are sector-addressed and Sony's ALFReD driver
             * always sends sector numbers; QEMU would otherwise byte-address
             * a <= 2 GiB card (only LBA 0, the partition table, then reads
             * right and nothing mounts).
             */
            qdev_prop_set_bit(card, "sector-addressing", true);
            qdev_prop_set_drive_err(card, "drive", blk, &error_fatal);
            a7c_emmc_blk = blk;
            qdev_realize_and_unref(card, qdev_get_child_bus(sdhci, "sd-bus"),
                                   &error_fatal);
        }
    }

    a7c_create_pcie(sysmem, pic);
    /*
     * Frame timing and display output, same blocks and IRQ numbers as the
     * a5000 (av-cam registers liro-83/84/98/99/101/102 on the A7C too, and
     * touches 0xF3402000 / 0xF3440000 / 0xF3C30000).  Models from
     * a5000-emulator: VPH display controller + SYSV frame timer
     * (cxd90014_vph.c, panel = QEMU console 0) and the timing-event
     * selector (cxd90014_evsel.c, +0xE0 = the 1 MHz counter av-cam's
     * timeouts use).
     */
    {
        DeviceState *vph = qdev_new("cxd90014-vph");
        DeviceState *ev;
        qdev_set_id(vph, g_strdup("vph"), &error_fatal);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(vph), &error_fatal);
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(vph), 0, 0xF3440000, 1);
        /* SYSV sits inside the 0xF3400000 clock stub window: must win */
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(vph), 1, 0xF3402000, 1);
        sysbus_connect_irq(SYS_BUS_DEVICE(vph), 0, pic[98 - 32]);
        sysbus_connect_irq(SYS_BUS_DEVICE(vph), 1, pic[101 - 32]);
        sysbus_connect_irq(SYS_BUS_DEVICE(vph), 2, pic[102 - 32]);
        if (vph_first) {
            a7c_create_fbview(machine);
        }

        /*
         * The a5000 evsel model (frame events IRQ 83 + 1/8-frame
         * comparators IRQ 84) stalls the A7C's libgpe init (boot stops at
         * "Ii 082_libgpe.so"), so by default 0xF3C30000 is only the 1 MHz
         * counter.  -M sony-a7c,evsel=on enables the a5000 model.
         */
        if (!a7c_evsel) {
            a7c_create_regs(sysmem, "a7c.avcam_timer", A7C_AVTMR_BASE,
                            a7c_avtmr_read)->t0_ns =
                qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
            goto no_evsel;
        }
        ev = qdev_new("cxd90014-evsel");
        qdev_prop_set_bit(ev, "ext-vd", true);
        qdev_prop_set_uint64(ev, "sources", a7c_evsel_sources);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(ev), &error_fatal);
        qdev_connect_gpio_out(vph, 0, qdev_get_gpio_in(ev, 0));
        sysbus_mmio_map(SYS_BUS_DEVICE(ev), 0, A7C_AVTMR_BASE);
        sysbus_connect_irq(SYS_BUS_DEVICE(ev), 0, pic[83 - 32]);
        sysbus_connect_irq(SYS_BUS_DEVICE(ev), 1, pic[84 - 32]);
    no_evsel:
        ;
    }
    a7c_create_regs(sysmem, "a7c.dfe_denali", A7C_DENALI_BASE, a7c_denali_read);

    a7c_create_catchall(sysmem);
    /*
     * A7C_DWC3=1: USB device controller at 0xF020C000, GIC 229 (same layout as
     * the a6400, E0528/E0590).  Without it the catch-all reads back the
     * DCTL soft-reset bit and the kernel logs "dwc3: Reset Timed Out".
     */
    if (getenv("A7C_DWC3") && *getenv("A7C_DWC3") == '1') {
        DeviceState *dwc3 = qdev_new(TYPE_CXD90045_DWC3);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(dwc3), &error_fatal);
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(dwc3), 0, 0xF020C000ULL, 0);
        sysbus_connect_irq(SYS_BUS_DEVICE(dwc3), 0, pic[229 - 32]);
    }
    a7c_create_gpio_sio_submicro(pic);
    if (a7c_units) {
        a7c_create_camera_units(pic);
    }

    sony_a7c_load_boot_contract(machine, cpus, ncpu);
    qemu_register_reset(a7c_bootloader_reset, NULL);
    a7c_handoff_init();
}

static void sony_a7c_machine_init(MachineClass *mc)
{
    mc->desc = "Sony ILCE-7C (BIONZ X / CXD900X0)";
    mc->init = sony_a7c_init;
    mc->max_cpus = CXD90045_MAX_CPUS;
    mc->default_cpus = CXD90045_MAX_CPUS;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-a5");
    mc->default_ram_size = CXD90045_RAM0_SIZE;
    mc->ignore_memory_transaction_failures = true;
    object_class_property_add_str(OBJECT_CLASS(mc), "submicro",
                                  a7c_get_submicro, a7c_set_submicro);
    object_class_property_set_description(OBJECT_CLASS(mc), "submicro",
        "sub-micro (power micro) frames from tools/a7c_extract.py "
        "(fw/submicro_frames.bin); without it the camera sees no power "
        "switch / battery and suspends after init");
    object_class_property_add_bool(OBJECT_CLASS(mc), "bootloader",
                                   a7c_get_bootloader, a7c_set_bootloader);
    object_class_property_set_description(OBJECT_CLASS(mc), "bootloader",
        "copy /setting/Backup.bin and /system/av-cam.bin from the eMMC into "
        "RAM at reset, like Sony's bootloader (default on)");
    object_class_property_add_str(OBJECT_CLASS(mc), "handoff",
                                  a7c_get_handoff, a7c_set_handoff);
    object_class_property_set_description(OBJECT_CLASS(mc), "handoff",
        "experimental: file (<= 4 KiB) written to the bootloader hand-off page "
        "at PA 0x20021000 at every reset, e.g. a live capture "
        "pa_20021000_handoff.bin; default: left zero");
    object_class_property_add(OBJECT_CLASS(mc), "evsel-sources", "uint64",
                              a7c_get_evsel_sources, a7c_set_evsel_sources,
                              NULL, NULL);
    object_class_property_set_description(OBJECT_CLASS(mc), "evsel-sources",
        "bit mask of camera timing-event source ids (0..63) the evsel model "
        "fires each frame");
    object_class_property_add_bool(OBJECT_CLASS(mc), "units", a7c_get_units,
                                   a7c_set_units);
    object_class_property_set_description(OBJECT_CLASS(mc), "units",
        "camera units ported from the a5000/a6400 projects: ADC (keys "
        "released), SA DSP cores, P2S sensor writer (default on)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "evsel", a7c_get_evsel,
                                   a7c_set_evsel);
    object_class_property_set_description(OBJECT_CLASS(mc), "evsel",
        "a5000 timing-event selector model at 0xF3C30000 (IRQ 83/84); "
        "off by default, it stalls libgpe init on the A7C");
}

DEFINE_MACHINE("sony-a7c", sony_a7c_machine_init)
