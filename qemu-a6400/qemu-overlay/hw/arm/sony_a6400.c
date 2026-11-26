/*
 * Sony ILCE-6400 (BIONZ X / CXD90045 / ARM-CXD900X0) Machine Model
 *
 * Initial Milestone 1 bring-up machine derived from verified CXD90045
 * L2 hardware contracts (src/a6400/machine/soc_cxd90045.py):
 *   - Machine ID: 0x00000DA9 (3497, ARM-CXD900X0)
 *   - RAM bank 0: 0x00000000 .. 0x40000000 (1 GiB)
 *   - RAM bank 1: 0x80000000 .. 0xBF800000 (1016 MiB)
 *   - SRAM:       0xFE000000 .. 0xFE100000 (1 MiB)
 *   - A9MPCore:   0xF8000000 (GIC CPU @ 0xF8000100, GIC Dist @ 0xF8001000)
 *   - PL011 UART0:0xF2000000 (IRQ 150 -> SPI 118)
 *   - PL011 UART3:0xF2003000 (IRQ 120 -> SPI 88, null chardev)
 *   - CXD4115 timer: 0xF2403000, channel n -> IRQ 153 + n (E0522)
 *   - 4 x Cortex-A5 (E0522: secondaries released through SRAM mailbox
 *     0xFE005010 + 4 * (cpu - 1) by cxd900x0_smp_init)
 *   - CRCDC Clk:  0xF2A31000 (+0x30 bit 0 ready)
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "hw/arm/boot.h"
#include "hw/arm/machines-qom.h"
#include "hw/core/loader.h"
#include "qemu/bswap.h"
#include "qemu/error-report.h"
#include "system/reset.h"
#include "hw/core/boards.h"
#include "hw/core/irq.h"
#include "hw/core/split-irq.h"
#include "hw/char/pl011.h"
#include "hw/cpu/a9mpcore.h"
#include "hw/misc/unimp.h"
#include "hw/core/qdev-properties.h"
#include "monitor/qdev.h"
#include "hw/sd/sd.h"
#include "hw/sd/sdhci.h"
#include "hw/misc/cxd90014_sio.h"
#include "hw/misc/cxd90014_adc.h"
#include "hw/misc/cxd90045_cam.h"
#include "hw/misc/cxd90045_i2c.h"
#include "hw/misc/cxd90045_peripherals.h"
#include "hw/core/sysbus.h"
#include "system/block-backend-global-state.h"
#include "system/blockdev.h"
#include "qemu/log.h"
#include "qemu/timer.h"
#include "qemu/units.h"
#include "system/address-spaces.h"
#include "system/memory.h"
#include "system/system.h"
#include "exec/translation-block.h"
#include "target/arm/cpu.h"
#include "ui/input.h"

#define CXD90045_MACHINE_ID      0x00000DA9
#define CXD90045_RAM0_BASE       0x00000000ULL
#define CXD90045_RAM0_SIZE       (1 * GiB)
#define CXD90045_RAM1_BASE       0x80000000ULL
#define CXD90045_RAM1_SIZE       (1016 * MiB)
#define CXD90045_SRAM_BASE       0xFE000000ULL
#define CXD90045_SRAM_SIZE       (1 * MiB)

#define CXD90045_PERIPH_BASE     0xF8000000ULL
#define CXD90045_UART0_BASE      0xF2000000ULL
#define CXD90045_UART0_SPI       (150 - 32)
#define CXD90045_UART3_BASE      0xF2003000ULL
#define CXD90045_UART3_SPI       (120 - 32)
#define CXD90045_TIMER_BASE      0xF2403000ULL
#define CXD90045_TIMER_IRQ0      153
#define CXD90045_WDT_BASE        0xF2402000ULL
#define CXD90045_MAX_CPUS        4
#define CXD90045_SIO_BASE        0xF2405000ULL
#define CXD90045_SIO_IRQ0        201
#define CXD90045_GPIO_BASE       0xF2910000ULL
#define CXD90045_ADC_BASE        0xF2406000ULL
#define CXD90045_ADC_IRQ0        215

/*
 * GPIO input levels at reset (E0526, E0651), "port.pin" pins that read 1:
 *  - input.ko GPIO keys marked active low ("L") in the a6400 key table
 *    (/proc/udm/input): P0.3 (0x70FD), P0.4 (0x70FE), P0.7 (0x70DC),
 *    P0.10 (0x1F0 MOVIE), P0.11 (0x70C6), P1.1 (0x70EF) -> released;
 *  - P0.2 (rear-controls only): EVF eye-contact sensor (Backup F90427/28,
 *    polled by av-cam 0x6361ac28 -> KEY_OTHER_EYE_CONTACT 0x708B); active
 *    low, high = no eye contact so stock FINDER/MONITOR selects the rear LCD;
 *  - P0.1 and P1.5: body shutter S1/S2, idle high; the captured av-cam
 *    port map gives IRQs 122 and 142. P0.13 keeps its existing idle level.
 *  - P1.4: E-mount lens detect (a6400 Backup F9050C/0D = port 1 pin 4,
 *    IRQ 141 registered); active low on the a5000, high = empty mount
 *    [inference for the polarity]. Holding it low (lens present, unanswered)
 *    did not make av-cam start an SA function before SetContentTarget.
 * P9.22 carries both an "H" key (0x4C) and an "L" key (0x4B), i.e. a
 * two-position switch; it is left at 0 (0x4B side) [assumption].
 *
 * Sub-micro link lines (a6400 Backup ids, cf. a5000 RE_SUBMICRO_LINK.md):
 * request F904CA/CB = P2.5 (a5000 p3.17), strobe F90465/66 = P0.8 / IRQ 129
 * (a5000 p0.12), release-lock F9045F/60 = P0.9 / IRQ 130 (a5000 p0.7),
 * exposure F90200/01 = P1.2 / IRQ 139 (a5000 p0.16), S2REQ F90455/56 =
 * P7.15 (CPU output), CS F900B1/6D = P5.14.  All idle low.
 */
#define CXD90045_GPIO_RESET_HIGH "0.1,0.3,0.4,0.7,0.10,0.11,0.13,1.1,1.4,1.5"
#define CXD90045_SHUTTER_S1_LINE  1
#define CXD90045_SHUTTER_S2_LINE  (32 + 5)
#define CXD90045_SM_REQ_LINE     (2 * 32 + 5)
#define CXD90045_SM_STROBE_LINE  (0 * 32 + 8)
#define CXD90045_CRCDC_BASE      0xF2A31000ULL
#define CXD90045_GPES_BAR_BASE   0xF1000000ULL
#define CXD90045_GPES_SPI        (76 - 32)
#define CXD90045_SUGI_CLK_BASE   0xF3400000ULL
#define CXD90045_DFS_CTRL_BASE   0xF2916000ULL
#define CXD90045_L2C_STUB_BASE   0xF8002000ULL
#define CXD90045_EMMC0_BASE      0xF0304000ULL
#define CXD90045_EMMC0_SPI       (227 - 32)
#define CXD90045_MMC0_BASE       0xF0300000ULL
#define CXD90045_MMC0_SPI        (218 - 32)
#define CXD90045_MMC1_BASE       0xF0301000ULL
#define CXD90045_MMC1_SPI        (221 - 32)
#define CXD90045_MMC2_BASE       0xF0302000ULL
#define CXD90045_MMC2_SPI        (224 - 32)
#define CXD90045_EFUSE_CHIPID    0xF2915000ULL
#define CXD90045_SD_MODEREAD     0xF2915010ULL
#define CXD90045_ASV_BASE        0xF2927000ULL
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

/*
 * Opt-in stateful CRCDC clock model (E0423): cxd900x0_update_crdm writes 1 to
 * [0xF2A31000 + 0x30], executes dsb, and polls bit 0 until it clears (0),
 * logging "CRCDC timeout" if bit 0 stays 1.  Off by default so historical
 * boot-scorer counts on the legacy +0x30 -> 0x1 stub do not change.
 */
typedef struct CxdCrcdcModel {
    MemoryRegion mmio;
    uint32_t regs[0x400];
} CxdCrcdcModel;

static bool sony_a6400_crcdc_model;

static uint64_t cxd90045_crcdc_model_read(void *opaque, hwaddr offset,
                                          unsigned size)
{
    CxdCrcdcModel *s = opaque;
    size_t idx = offset >> 2;

    if (offset == 0x30) {
        return s->regs[0x30 >> 2] & ~1U;
    }
    if (idx < ARRAY_SIZE(s->regs)) {
        return s->regs[idx];
    }
    return 0;
}

static void cxd90045_crcdc_model_write(void *opaque, hwaddr offset,
                                       uint64_t value, unsigned size)
{
    CxdCrcdcModel *s = opaque;
    size_t idx = offset >> 2;

    if (offset == 0x30) {
        s->regs[0x30 >> 2] = (uint32_t)value & ~1U;
        return;
    }
    if (idx < ARRAY_SIZE(s->regs)) {
        s->regs[idx] = (uint32_t)value;
    }
}

static const MemoryRegionOps cxd90045_crcdc_model_ops = {
    .read = cxd90045_crcdc_model_read,
    .write = cxd90045_crcdc_model_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void cxd90045_crcdc_model_reset(void *opaque)
{
    CxdCrcdcModel *s = opaque;

    memset(s->regs, 0, sizeof(s->regs));
}

static void cxd90045_create_crcdc_model(MemoryRegion *sysmem)
{
    CxdCrcdcModel *s = g_new0(CxdCrcdcModel, 1);

    memory_region_init_io(&s->mmio, NULL, &cxd90045_crcdc_model_ops, s,
                          "cxd90045.crcdc-model", 0x1000);
    memory_region_add_subregion(sysmem, CXD90045_CRCDC_BASE, &s->mmio);
    qemu_register_reset(cxd90045_crcdc_model_reset, s);
}

static bool sony_get_crcdc_model(Object *obj, Error **errp)
{
    return sony_a6400_crcdc_model;
}

static void sony_set_crcdc_model(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "crcdc-model is a startup option");
        return;
    }
    sony_a6400_crcdc_model = enabled;
}

static bool sony_a6400_pl310;

static bool sony_get_pl310(Object *obj, Error **errp)
{
    return sony_a6400_pl310;
}

static void sony_set_pl310(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "pl310 is a startup option");
        return;
    }
    sony_a6400_pl310 = enabled;
}

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
                                         hwaddr base, size_t size, int priority)
{
    CxdRwStub *s = g_new0(CxdRwStub, 1);
    s->num_regs = size / sizeof(uint32_t);
    s->regs = g_new0(uint32_t, s->num_regs);
    memory_region_init_io(&s->mmio, NULL, &cxd90045_rw_stub_ops, s, name, size);
    memory_region_add_subregion_overlap(sysmem, base, &s->mmio, priority);
    return s;
}

#define DFS_STATUS       0x610
#define DFS_ENABLE       0x620
#define DFS_START        0x700
#define DFS_DIVIDER      0x720
#define DFS_ENABLE_CPU   0x101
#define DFS_COMPLETE     BIT(0)

typedef struct CxdDfsControl {
    CxdRwStub stub;
    bool divider_staged;
} CxdDfsControl;

static bool sony_a6400_dfs_control;

static uint64_t cxd90045_dfs_read(void *opaque, hwaddr offset, unsigned size)
{
    CxdDfsControl *s = opaque;

    return cxd90045_rw_stub_read(&s->stub, offset, size);
}

static void cxd90045_dfs_write(void *opaque, hwaddr offset, uint64_t value,
                              unsigned size)
{
    CxdDfsControl *s = opaque;
    uint32_t *regs = s->stub.regs;

    switch (offset) {
    case DFS_STATUS:
        regs[DFS_STATUS >> 2] &= ~(value & DFS_COMPLETE);
        return;
    case DFS_ENABLE:
        s->divider_staged = false;
        break;
    case DFS_DIVIDER:
        s->divider_staged = regs[DFS_ENABLE >> 2] == DFS_ENABLE_CPU;
        break;
    case DFS_START:
        /*
         * The stock UPM waits for (status & 0x101) == 1, then clears bit 0.
         * Complete the switch synchronously without changing virtual clocks.
         */
        if (value == 1 && s->divider_staged &&
            regs[DFS_ENABLE >> 2] == DFS_ENABLE_CPU &&
            !(regs[DFS_STATUS >> 2] & DFS_COMPLETE)) {
            s->divider_staged = false;
            regs[DFS_STATUS >> 2] |= DFS_COMPLETE;
        } else {
            qemu_log_mask(LOG_UNIMP, "cxd90045.dfs: unsupported switch 0x%"
                          PRIx64 "\n", value);
        }
        break;
    default:
        break;
    }
    cxd90045_rw_stub_write(&s->stub, offset, value, size);
}

static const MemoryRegionOps cxd90045_dfs_ops = {
    .read = cxd90045_dfs_read,
    .write = cxd90045_dfs_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void cxd90045_dfs_reset(void *opaque)
{
    CxdDfsControl *s = opaque;

    memset(s->stub.regs, 0, s->stub.num_regs * sizeof(*s->stub.regs));
    s->divider_staged = false;
}

static void cxd90045_create_dfs(MemoryRegion *sysmem)
{
    CxdDfsControl *s = g_new0(CxdDfsControl, 1);
    CxdRwStub *stub = &s->stub;

    stub->num_regs = 0x1000 / sizeof(*stub->regs);
    stub->regs = g_new0(uint32_t, stub->num_regs);
    memory_region_init_io(&stub->mmio, NULL, &cxd90045_dfs_ops, s,
                          "cxd90045.dfs_ctrl", 0x1000);
    memory_region_add_subregion_overlap(sysmem, CXD90045_DFS_CTRL_BASE,
                                       &stub->mmio, 0);
    qemu_register_reset(cxd90045_dfs_reset, s);
}

static bool sony_get_dfs_control(Object *obj, Error **errp)
{
    return sony_a6400_dfs_control;
}

static void sony_set_dfs_control(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "dfs-control is a startup option");
        return;
    }
    sony_a6400_dfs_control = enabled;
}

/*
 * Sugilite / DMP SMAPH-S30 is hw/display/dmp_smaph.c (ported from
 * Oein/a5000-emulator).  It executes PSC fills, PPF copies and P3D
 * command lists, and raises IRQ 76 from status +0x34.  The previous
 * register stub completed jobs without writing a pixel.
 */

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
    address_space_read(&address_space_memory, ssa, MEMTXATTRS_UNSPECIFIED,
                       s->in_buf + dsa_off, len);
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
            address_space_write(&address_space_memory, dst_phys,
                                MEMTXATTRS_UNSPECIFIED, out_buf, decoded);
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

/*
 * Leo clock generator at 0xFC021000 (av-cam 0x63782020).  Enable bits are
 * written at +0x000; PLL lock is polled at +0x050/+0x054/+0x058 bit0, up to
 * 1001 tight reads.  Unmapped reads are 0, so that poll logs
 * "IMG:Timeout:Leo Clkgen PLL Wait" and the imager init stops there (E0534).
 * Lock is always reported set.  Other offsets read back what was written.
 */
static uint64_t cxd90045_leo_read(void *opaque, hwaddr offset, unsigned size)
{
    uint32_t *regs = opaque;
    uint32_t v = 0;

    if ((offset >> 2) < 0x400) {
        v = regs[offset >> 2];
    }
    /* +0x050/054/058 bit0: Leo PLL lock (av-cam 0x63782020). */
    if (offset == 0x50 || offset == 0x54 || offset == 0x58) {
        v |= 1;
    }
    return v;
}

static void cxd90045_leo_write(void *opaque, hwaddr offset, uint64_t value,
                               unsigned size)
{
    uint32_t *regs = opaque;

    if ((offset >> 2) < 0x400) {
        regs[offset >> 2] = (uint32_t)value;
    }
}

static const MemoryRegionOps cxd90045_leo_ops = {
    .read = cxd90045_leo_read,
    .write = cxd90045_leo_write,
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
                    address_space_read(&address_space_memory, ssa + off,
                                       MEMTXATTRS_UNSPECIFIED, buf, chunk);
                    address_space_write(&address_space_memory, dsa + off,
                                        MEMTXATTRS_UNSPECIFIED, buf, chunk);
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
 * Opt-in work DMA copy engine at 0xF0102000 (E0641), from the only stock
 * user, av-cam 0x635DC8B8..0x635DC93E (E0640):
 *   +0x030 bit0 engine enable; +0x008/+0x00C = 0x40 (role not established)
 *   channel block +0x1C0: +0x00 source PA, +0x04 destination PA, +0x08 = 0,
 *   +0x0C config = 0x1C491000 | count (count bits [11:0] in 32-bit words,
 *   0 = 4096: av-cam writes 0x1C491000 for its 16 KiB chunks and
 *   0x1C491000 | rest for the last one), +0x10 = 1 start
 *   +0x01C bit6 busy for that channel; av-cam polls until it clears.
 * Only that configuration is executed, as a plain word copy [I: the source
 * is an ARM vector table copied out of the av-cam image to a run address].
 * Any other config, a start while disabled, or a start on another offset is
 * logged under -d unimp and performs no copy, so an unknown mode is never
 * reported as done with data.  Executed copies are also logged under -d unimp
 * (first and partial chunks) because the model covers one mode only.  The copy completes synchronously, so busy
 * reads 0 (timing not modelled).  Registers otherwise read back.
 */
#define CXD90045_WDMA_BASE       0xF0102000ULL
#define WDMA_EN                  0x030
#define WDMA_BUSY                0x01C
#define WDMA_CH_SRC              0x1C0
#define WDMA_CH_DST              0x1C4
#define WDMA_CH_CFG              0x1CC
#define WDMA_CH_START            0x1D0
#define WDMA_CFG_MODE_MASK       0xFFFFF000u
#define WDMA_CFG_MODE_COPY       0x1C491000u

static bool sony_a6400_work_dma;

typedef struct CxdWorkDma {
    MemoryRegion mmio;
    uint32_t regs[0x1000 >> 2];
    uint64_t copies, bytes, refused;
} CxdWorkDma;

static uint64_t cxd90045_wdma_read(void *opaque, hwaddr offset, unsigned size)
{
    CxdWorkDma *s = opaque;
    if (offset == WDMA_BUSY) {
        return s->regs[WDMA_BUSY >> 2] & ~0x40u;   /* copy already finished */
    }
    return s->regs[offset >> 2];
}

static void cxd90045_wdma_start(CxdWorkDma *s)
{
    uint32_t src = s->regs[WDMA_CH_SRC >> 2];
    uint32_t dst = s->regs[WDMA_CH_DST >> 2];
    uint32_t cfg = s->regs[WDMA_CH_CFG >> 2];
    uint32_t words = (cfg & 0xFFFu) ? (cfg & 0xFFFu) : 0x1000u;
    uint8_t buf[4096];

    if (!(s->regs[WDMA_EN >> 2] & 1) ||
        (cfg & WDMA_CFG_MODE_MASK) != WDMA_CFG_MODE_COPY || (src | dst) & 3) {
        s->refused++;
        qemu_log_mask(LOG_UNIMP, "cxd90045.work-dma: refused start en=0x%x "
                      "cfg=0x%08x src=0x%08x dst=0x%08x (unsupported, no copy)\n",
                      s->regs[WDMA_EN >> 2], cfg, src, dst);
        return;
    }
    for (uint32_t off = 0, rem = words * 4; rem; ) {
        uint32_t chunk = MIN(rem, (uint32_t)sizeof(buf));
        address_space_read(&address_space_memory, src + off,
                           MEMTXATTRS_UNSPECIFIED, buf, chunk);
        address_space_write(&address_space_memory, dst + off,
                            MEMTXATTRS_UNSPECIFIED, buf, chunk);
        off += chunk;
        rem -= chunk;
    }
    s->copies++;
    s->bytes += words * 4;
    if (s->copies == 1 || (cfg & 0xFFFu)) {
        qemu_log_mask(LOG_UNIMP, "cxd90045.work-dma: copy #%" PRIu64
                      " 0x%08x -> 0x%08x, %u bytes (total %" PRIu64 ")\n",
                      s->copies, src, dst, words * 4, s->bytes);
    }
}

static void cxd90045_wdma_write(void *opaque, hwaddr offset, uint64_t value,
                                unsigned size)
{
    CxdWorkDma *s = opaque;
    s->regs[offset >> 2] = (uint32_t)value;
    if (offset == WDMA_CH_START && (value & 1)) {
        cxd90045_wdma_start(s);
        s->regs[WDMA_CH_START >> 2] = 0;
    } else if (offset >= 0x040 && offset < 0x1000 &&
               (offset & 0x3F) == 0x10 && offset != WDMA_CH_START && (value & 1)) {
        s->refused++;
        qemu_log_mask(LOG_UNIMP, "cxd90045.work-dma: start at +0x%03" HWADDR_PRIx
                      " is not a modelled channel (no copy)\n", offset);
    }
}

static const MemoryRegionOps cxd90045_wdma_ops = {
    .read = cxd90045_wdma_read,
    .write = cxd90045_wdma_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
};

static void cxd90045_create_work_dma(MemoryRegion *sysmem)
{
    CxdWorkDma *s = g_new0(CxdWorkDma, 1);
    memory_region_init_io(&s->mmio, NULL, &cxd90045_wdma_ops, s,
                          "cxd90045.work-dma", 0x1000);
    memory_region_add_subregion_overlap(sysmem, CXD90045_WDMA_BASE, &s->mmio, 0);
}

static bool sony_get_work_dma(Object *obj, Error **errp)
{
    return sony_a6400_work_dma;
}

/*
 * Opt-in 0xF30A memory-fill engine (E0642 section 1). The routine is av-cam's
 * entry_pre hardware path: the packaged image (90d515cf) calls 0x635DC810 via
 * the 0x635C6044 stub, the captured ORIL image (dd8f8d0c) 0x635DC898. Both
 * program the same registers:
 *   +0x1280 CTRL: 0x20 prepare, then 0x21 start
 *   +0x1284 STATUS: bit 31 busy, clear once the fill has completed
 *   +0x128C written 0 once (role not established), read back
 *   +0x12A0 DST_PA, +0x12A4 BYTE_LEN
 *   +0x12AC..+0x12C4 four 32-bit fill words (0 in the only observation)
 * The routine zeroes LIRO's bss (package 0x0403A000 + 0x497A26, captured
 * 0x04052000 + 0x49D625) and then runs the work DMA copy of av-cam's
 * initialised RW data.  A cold QEMU boot already has that region zero (it is
 * part of the preloaded av-cam image), so the unmodelled engine is harmless
 * there.  A RAM-to-RAM handover from the stock OS starts with av-cam's previous
 * bss and needs the fill, otherwise the 1,878 captured references read stale
 * data (E0644, E0681).
 *
 * Only the observed zero fill is executed.  A non-zero fill word, a zero or
 * oversized length, or an unaligned destination is refused and logged under
 * -d unimp: E0642 records no non-zero fill, alignment or interrupt semantics,
 * so no invented behaviour is reported as done with data.  The fill completes
 * synchronously, so busy reads 0 (timing is not modelled).
 *
 * The engine's registers sit inside the 0xF30A1000 AIF group's second 0x80-byte
 * channel block.  The model is a 0x2000 container at 0xF30A0000 whose only
 * leaf is the 0x80-byte fill window at +0x1280, at a priority above the AIF
 * groups, so every other register in that group (and the rest of 0xF30A0000)
 * still reaches its own model.
 */
#define CXD90045_F30A_BASE       0xF30A0000ULL
#define CXD90045_F30A_SIZE       0x2000ULL
#define CXD90045_F30A_WIN_OFF    0x1280ULL
#define CXD90045_F30A_WIN_SIZE   0x80ULL
/* Window-relative offsets: the leaf MemoryRegion starts at container +0x1280, so
 * E0642's absolute +0x1280 CTRL is offset 0 here. */
#define F30A_CTRL                0x00
#define F30A_STATUS              0x04
#define F30A_DST                 0x20
#define F30A_LEN                 0x24
#define F30A_FILL0               0x2C
#define F30A_FILL1               0x34
#define F30A_FILL2               0x3C
#define F30A_FILL3               0x44
#define F30A_MAX_LEN             (64u << 20)
#define F30A_REG(off)            ((off) >> 2)

static bool sony_a6400_f30a_fill;

typedef struct CxdF30aFill {
    MemoryRegion container;
    MemoryRegion window;
    uint32_t regs[CXD90045_F30A_WIN_SIZE >> 2];
    uint64_t fills, bytes, refused;
} CxdF30aFill;

static uint64_t cxd90045_f30a_read(void *opaque, hwaddr offset, unsigned size)
{
    CxdF30aFill *s = opaque;
    uint32_t v = s->regs[F30A_REG(offset)];

    qemu_log_mask(LOG_UNIMP, "cxd90045.f30a-fill: read +0x%" HWADDR_PRIx
                  " size %u -> 0x%x\n", offset, size, v);
    if (offset == F30A_STATUS) {
        v &= ~0x80000000u;              /* the fill already finished */
    }
    return size == 4 ? v : (v >> ((offset & 3) * 8)) & ((1u << (size * 8)) - 1);
}

static void cxd90045_f30a_run(CxdF30aFill *s)
{
    uint32_t dst = s->regs[F30A_REG(F30A_DST)];
    uint32_t len = s->regs[F30A_REG(F30A_LEN)];
    uint32_t fill[4] = {
        s->regs[F30A_REG(F30A_FILL0)], s->regs[F30A_REG(F30A_FILL1)],
        s->regs[F30A_REG(F30A_FILL2)], s->regs[F30A_REG(F30A_FILL3)],
    };
    uint8_t zeros[4096] = { 0 };

    if (fill[0] || fill[1] || fill[2] || fill[3] || !len ||
        len > F30A_MAX_LEN || (dst & 3)) {
        s->refused++;
        qemu_log_mask(LOG_UNIMP, "cxd90045.f30a-fill: refused fill dst=0x%08x "
                      "len=0x%x words=%08x,%08x,%08x,%08x (not modelled, "
                      "no fill)\n", dst, len, fill[0], fill[1], fill[2],
                      fill[3]);
        return;
    }
    for (uint32_t off = 0; off < len; ) {
        uint32_t chunk = MIN(len - off, (uint32_t)sizeof(zeros));

        address_space_write(&address_space_memory, dst + off,
                            MEMTXATTRS_UNSPECIFIED, zeros, chunk);
        off += chunk;
    }
    s->fills++;
    s->bytes += len;
    qemu_log_mask(LOG_UNIMP, "cxd90045.f30a-fill: fill #%" PRIu64
                  " 0x%08x + 0x%x bytes zero (total %" PRIu64 ")\n",
                  s->fills, dst, len, s->bytes);
}

static void cxd90045_f30a_write(void *opaque, hwaddr offset, uint64_t value,
                                unsigned size)
{
    CxdF30aFill *s = opaque;
    uint32_t v = (uint32_t)value;

    qemu_log_mask(LOG_UNIMP, "cxd90045.f30a-fill: write +0x%" HWADDR_PRIx
                  " size %u <- 0x%x\n", offset, size, v);
    s->regs[F30A_REG(offset)] = v;
    if (offset == F30A_CTRL && v == 0x20) {
        qemu_log_mask(LOG_UNIMP, "cxd90045.f30a-fill: prepare (CTRL=0x20)\n");
    } else if (offset == F30A_CTRL && v == 0x21) {
        cxd90045_f30a_run(s);
    } else if (offset == F30A_CTRL) {
        qemu_log_mask(LOG_UNIMP, "cxd90045.f30a-fill: CTRL=0x%x not modelled\n",
                      v);
    }
}

static const MemoryRegionOps cxd90045_f30a_ops = {
    .read = cxd90045_f30a_read,
    .write = cxd90045_f30a_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
};

static void cxd90045_create_f30a_fill(MemoryRegion *sysmem)
{
    CxdF30aFill *s = g_new0(CxdF30aFill, 1);

    memory_region_init(&s->container, NULL, "cxd90045.f30a-fill",
                       CXD90045_F30A_SIZE);
    memory_region_init_io(&s->window, NULL, &cxd90045_f30a_ops, s,
                          "cxd90045.f30a-fill-window", CXD90045_F30A_WIN_SIZE);
    memory_region_add_subregion(&s->container, CXD90045_F30A_WIN_OFF,
                                &s->window);
    /* Above the AIF groups (-300) and the unmodelled catch-all (-1000); the
     * container's other addresses have no leaf and stay transparent. */
    memory_region_add_subregion_overlap(sysmem, CXD90045_F30A_BASE,
                                        &s->container, -100);
}

static bool sony_get_f30a_fill(Object *obj, Error **errp)
{
    return sony_a6400_f30a_fill;
}

static void sony_set_f30a_fill(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "f30a-fill is a startup option");
        return;
    }
    sony_a6400_f30a_fill = enabled;
}

/*
 * Opt-in bus error inside the reserved first megabyte (E0647).  On a physical
 * ILCE-6400 a user-space /dev/mem read of PA 0x000F8000 returned an external
 * abort ("external abort on non-linefetch (0x008)") and Sony's exception
 * monitor warm-rebooted the camera; the 0x00000000 page read before it did
 * not fault.  The camera's FSR had ExT = 0 (Cortex-A5: AXI DECERR, no slave
 * decodes the address), so the model answers MEMTX_DECODE_ERROR, which QEMU
 * reports with the same FSR (0x008) rather than 0x1008 (SLVERR).  Without this option the board backs the whole megabyte with RAM,
 * so the same access silently succeeds in QEMU.  Only the proven page is
 * modelled; the rest of the megabyte stays RAM [unknown on hardware].
 *
 * The machine ignores transaction failures (unassigned space reads 0).  To raise
 * an abort for this one page without changing any other access, the option
 * re-enables transaction failures on every CPU and maps a whole-space
 * unimplemented device at the lowest priority, so every other unassigned
 * address still reads 0 and drops writes exactly as before.
 */
#define CXD90045_LOWMEM_ABORT_BASE 0x000F8000ULL
#define CXD90045_LOWMEM_ABORT_SIZE 0x1000ULL

static bool sony_a6400_lowmem_abort;
static bool sony_a6400_strict_bus_abort;

static MemTxResult cxd90045_lowmem_abort_read(void *opaque, hwaddr addr,
                                              uint64_t *data, unsigned size,
                                              MemTxAttrs attrs)
{
    qemu_log_mask(LOG_GUEST_ERROR, "cxd90045.lowmem-abort: read PA 0x%08"
                  HWADDR_PRIx " size %u -> decode error (E0647)\n",
                  (hwaddr)(CXD90045_LOWMEM_ABORT_BASE + addr), size);
    *data = 0;
    return MEMTX_DECODE_ERROR;
}

static MemTxResult cxd90045_lowmem_abort_write(void *opaque, hwaddr addr,
                                               uint64_t data, unsigned size,
                                               MemTxAttrs attrs)
{
    qemu_log_mask(LOG_GUEST_ERROR, "cxd90045.lowmem-abort: write PA 0x%08"
                  HWADDR_PRIx " size %u -> decode error (E0647)\n",
                  (hwaddr)(CXD90045_LOWMEM_ABORT_BASE + addr), size);
    return MEMTX_DECODE_ERROR;
}

static const MemoryRegionOps cxd90045_lowmem_abort_ops = {
    .read_with_attrs = cxd90045_lowmem_abort_read,
    .write_with_attrs = cxd90045_lowmem_abort_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 8 },
};

static void sony_a6400_create_lowmem_abort(MemoryRegion *sysmem, ARMCPU **cpus,
                                           unsigned ncpu)
{
    MemoryRegion *mr = g_new(MemoryRegion, 1);

    memory_region_init_io(mr, NULL, &cxd90045_lowmem_abort_ops, NULL,
                          "cxd90045.lowmem-abort", CXD90045_LOWMEM_ABORT_SIZE);
    memory_region_add_subregion_overlap(sysmem, CXD90045_LOWMEM_ABORT_BASE,
                                        mr, 10);
    if (!sony_a6400_strict_bus_abort) {
        create_unimplemented_device("cxd90045.unassigned", 0, 1ULL << 32);
    }
    for (unsigned c = 0; c < ncpu; c++) {
        CPU(cpus[c])->ignore_memory_transaction_failures = false;
    }
}

static bool sony_get_lowmem_abort(Object *obj, Error **errp)
{
    return sony_a6400_lowmem_abort;
}

static void sony_set_lowmem_abort(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "lowmem-abort is a startup option");
        return;
    }
    sony_a6400_lowmem_abort = enabled;
}

static bool sony_get_strict_bus_abort(Object *obj, Error **errp)
{
    return sony_a6400_strict_bus_abort;
}

static void sony_set_strict_bus_abort(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "strict-bus-abort is a startup option");
        return;
    }
    sony_a6400_strict_bus_abort = enabled;
}

static void sony_set_work_dma(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "work-dma is a startup option");
        return;
    }
    sony_a6400_work_dma = enabled;
}

/*
 * The direct boot hand-off starts with initialized, idle virtual DDR-B.
 * Model only the UPM idle/active sequence; the register changes finish
 * synchronously. RAM contents remain intact in both modes.
 */
#define CXD90045_DDRB_BASE      0xF0105000ULL
#define DDRB_CLOCK_GATE        0x068
#define DDRB_MODE              0x090
#define DDRB_RANK0             0x0A0
#define DDRB_RANK1             0x0A4
#define DDRB_LIMIT0            0x118
#define DDRB_CONTROL           0x138
#define DDRB_STATUS            0x148
#define DDRB_STATUS_CLEAR      0x14C
#define DDRB_LIMIT1            0x218
#define DDRB_IDLE_RANK0        (1u << 24)
#define DDRB_IDLE_RANK1        1u
#define DDRB_RANK_CONFIG_MASK  0x00030300u
#define DDRB_ACCESS_CONTROL    0x00010000u
#define DDRB_ENABLE_RANKS      0x00000300u
#define DDRB_IDLE_REQUEST      0x01u
#define DDRB_ACTIVE_REQUEST    0x26u
#define DDRB_ACTIVE_MODE       0x24u
#define DDRB_COMMAND_READY     (1u << 29)
#define DDRB_RANK_DONE         (1u << 21)
#define DDRB_MODE_DONE         (1u << 4)

typedef struct CxdDdrbControl {
    MemoryRegion mmio;
    uint32_t rank_config;
    uint32_t gate;
    uint32_t control;
    uint32_t status;
    uint32_t mode;
    uint32_t limit[2];
} CxdDdrbControl;

static bool sony_a6400_ddrb_control;

static uint64_t cxd90045_ddrb_read(void *opaque, hwaddr offset, unsigned size)
{
    CxdDdrbControl *s = opaque;

    switch (offset) {
    case DDRB_RANK0:
        return s->rank_config | DDRB_IDLE_RANK0;
    case DDRB_RANK1:
        return DDRB_IDLE_RANK1;
    case DDRB_CLOCK_GATE:
        return s->gate;
    case DDRB_CONTROL:
        return s->control;
    case DDRB_STATUS:
        return s->status;
    case DDRB_MODE:
        return s->mode | (s->mode << 16);
    case DDRB_LIMIT0:
    case DDRB_LIMIT1:
        return s->limit[offset == DDRB_LIMIT1];
    default:
        qemu_log_mask(LOG_UNIMP, "cxd90045.ddrb: unsupported read at +0x%" HWADDR_PRIx "\n",
                      offset);
        return 0;
    }
}

static void cxd90045_ddrb_write(void *opaque, hwaddr offset, uint64_t value,
                               unsigned size)
{
    CxdDdrbControl *s = opaque;
    const uint32_t active_control = DDRB_ACCESS_CONTROL | DDRB_ENABLE_RANKS;

    switch (offset) {
    case DDRB_RANK0:
        s->rank_config = value & DDRB_RANK_CONFIG_MASK;
        return;
    case DDRB_CLOCK_GATE:
        s->gate = value & DDRB_ACCESS_CONTROL;
        return;
    case DDRB_LIMIT0:
    case DDRB_LIMIT1:
        s->limit[offset == DDRB_LIMIT1] = value;
        return;
    case DDRB_CONTROL:
        if (value == active_control) {
            if (s->control == DDRB_ACCESS_CONTROL) {
                s->status |= DDRB_COMMAND_READY | DDRB_RANK_DONE;
            }
            s->control = value;
            return;
        }
        if (value == DDRB_ACCESS_CONTROL && s->control == active_control &&
            !(s->status & DDRB_COMMAND_READY)) {
            s->control = value;
            return;
        }
        break;
    case DDRB_MODE:
        if (value == DDRB_ACTIVE_REQUEST && !s->gate &&
            (s->status & DDRB_COMMAND_READY) &&
            !(s->status & DDRB_RANK_DONE)) {
            s->mode = DDRB_ACTIVE_MODE;
            s->status |= DDRB_MODE_DONE;
            return;
        }
        if (value == DDRB_IDLE_REQUEST && s->mode == DDRB_ACTIVE_MODE) {
            s->mode = 0;
            s->status = 0;
            return;
        }
        break;
    case DDRB_STATUS_CLEAR:
        s->status &= ~(value & (DDRB_RANK_DONE | DDRB_MODE_DONE));
        return;
    default:
        break;
    }
    qemu_log_mask(LOG_UNIMP, "cxd90045.ddrb: unsupported write +0x%" HWADDR_PRIx
                  " = 0x%" PRIx64 "\n", offset, value);
}

static const MemoryRegionOps cxd90045_ddrb_ops = {
    .read = cxd90045_ddrb_read,
    .write = cxd90045_ddrb_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void cxd90045_ddrb_reset(void *opaque)
{
    CxdDdrbControl *s = opaque;

    s->rank_config = DDRB_ENABLE_RANKS;
    s->control = DDRB_ACCESS_CONTROL;
    s->gate = 0;
    s->status = 0;
    s->mode = 0;
    s->limit[0] = 0;
    s->limit[1] = 0;
}

static void cxd90045_create_ddrb(MemoryRegion *sysmem)
{
    CxdDdrbControl *s = g_new0(CxdDdrbControl, 1);

    cxd90045_ddrb_reset(s);
    memory_region_init_io(&s->mmio, NULL, &cxd90045_ddrb_ops, s,
                          "cxd90045.ddrb", 0x21c);
    memory_region_add_subregion_overlap(sysmem, CXD90045_DDRB_BASE, &s->mmio, 0);
    qemu_register_reset(cxd90045_ddrb_reset, s);
}

static bool sony_get_ddrb_control(Object *obj, Error **errp)
{
    return sony_a6400_ddrb_control;
}

static void sony_set_ddrb_control(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "ddrb-control is a startup option");
        return;
    }
    sony_a6400_ddrb_control = enabled;
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
/* Adapted ZV-E10 UI kernel layout; never selected by the stock profile. */
static bool sony_a6400_zve10_menu_boot;

static bool sony_get_zve10_menu_boot(Object *obj, Error **errp)
{
    return sony_a6400_zve10_menu_boot;
}

static void sony_set_zve10_menu_boot(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "zve10-menu-boot is a startup-only policy");
        return;
    }
    sony_a6400_zve10_menu_boot = enabled;
}

/* Standard ARMv7 Linux ATAGS/DTB boot contract for custom kernels/OSes. */
static bool sony_a6400_linux_boot;

static bool sony_get_linux_boot(Object *obj, Error **errp)
{
    return sony_a6400_linux_boot;
}

static void sony_set_linux_boot(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "linux-boot is a startup-only policy");
        return;
    }
    sony_a6400_linux_boot = enabled;
}

#define CXD90045_KERNEL_LOAD       0x00108000ULL
#define CXD90045_INITRD_LOAD       0x00700000ULL
#define CXD90045_INITRD_MAX        0x00800000ULL
#define CXD90045_LINUX_KERNEL_MAX  0x02000000ULL
#define CXD90045_LINUX_DTB_LOAD    0x0FF00000ULL
#define CXD90045_LINUX_DTB_MAX     0x00100000ULL
#define CXD90045_LINUX_INITRD_LOAD 0x10000000ULL
#define CXD90045_LINUX_INITRD_MAX  0x08000000ULL
#define CXD90045_BOOT_PARAMS       0x00103000ULL
#define CXD90045_BOOT_PARAMS_MAX   (CXD90045_KERNEL_LOAD - CXD90045_BOOT_PARAMS)

/*
 * Boot stubs in the reserved first megabyte (memrsv=1M@0), standing in for
 * what the boot ROM / bootloader does before the kernel runs (E0522):
 *
 *   CPU0 (CXD90045_STUB0): program GICD_ITARGETSR8..63, make the
 *     ack-less frame pulses SYSV 98/99 and vsync 101/102 edge-triggered
 *     (GICD_ICFGR6 = 0x28A0, as the a5000 board stub does), then r0 = 0,
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
#define CXD90045_SHUTTER_STUB    0x00001140ULL
#define CXD90045_SMPSTUB         0x00001200ULL
#define CXD90045_SMP_MBOX        0xFE005010ULL

static bool sony_a6400_shutter_edge_irq;

static const uint32_t cxd90045_stub0[20] = {
    0xe59f3030,             /* ldr r3, =0xF8001820 (ITARGETSR8)  */
    0xe59f4030,             /* ldr r4, =0xF8001900               */
    0xe28f6040,             /* add r6, pc, #0x40 (table @ +0x50) */
    0xe4965004,             /* 1: ldr r5, [r6], #4               */
    0xe4835004,             /* str r5, [r3], #4                  */
    0xe1530004,             /* cmp r3, r4                        */
    0x3afffffb,             /* blo 1b                            */
    0xe59f301c,             /* ldr r3, =0xF8001C18 (ICFGR6)      */
    0xe59f501c,             /* ldr r5, =0x28A0                   */
    0xe5835000,             /* str r5, [r3]                      */
    0xe3a00000,             /* mov r0, #0                        */
    0xe59f1014,             /* ldr r1, =CXD90045_MACHINE_ID      */
    0xe59f2014,             /* ldr r2, =CXD90045_BOOT_PARAMS     */
    0xe3a0f942,             /* mov pc, #0x108000 (kernel entry)  */
    0xf8001820,
    0xf8001900,
    0xf8001c18,
    0x000028a0,             /* IRQ 98, 99, 101, 102 edge (E0529) */
    CXD90045_MACHINE_ID,
    0x00103000,
};

/*
 * Optional bootloader policy: latch the body S1/S2 edges in the GIC.
 * The timed GPIO pulses can otherwise disappear before a CPU acknowledges
 * them. This is not a measurement of the camera's GPIO interrupt latch.
 */
static const uint32_t cxd90045_shutter_stub[] = {
    0xe59f3018,             /* ldr r3, =0xF8001C1C (ICFGR7) */
    0xe5934000,             /* ldr r4, [r3] */
    0xe3844602,             /* orr r4, r4, #0x00200000 (IRQ 122) */
    0xe5834000,             /* str r4, [r3] */
    0xe5934004,             /* ldr r4, [r3, #4] (ICFGR8) */
    0xe3844202,             /* orr r4, r4, #0x20000000 (IRQ 142) */
    0xe5834004,             /* str r4, [r3, #4] */
    0xeaffffa7,             /* b 0x1000 (ordinary CPU0 boot stub) */
    0xf8001c1c,
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

static void sony_a6400_write_stubs(unsigned ncpu, bool linux_dtb)
{
    uint32_t blob0[20 + 56];
    uint32_t smp[11];

    QEMU_BUILD_BUG_ON(CXD90045_STUB0 + sizeof(blob0) > CXD90045_SHUTTER_STUB);
    QEMU_BUILD_BUG_ON(CXD90045_SHUTTER_STUB + sizeof(cxd90045_shutter_stub) >
                      CXD90045_SMPSTUB);
    for (int i = 0; i < 20; i++) {
        blob0[i] = cpu_to_le32(cxd90045_stub0[i]);
    }
    if (sony_a6400_zve10_menu_boot) {
        /* ARM immediate encoding: mov pc, #0x138000. */
        blob0[13] = cpu_to_le32(0xe3a0f94e);
        blob0[19] = cpu_to_le32(0x100); /* adapted kernel uses Linux ATAGS */
    } else if (sony_a6400_linux_boot && linux_dtb) {
        blob0[19] = cpu_to_le32(CXD90045_LINUX_DTB_LOAD);
    }
    cxd90045_spi_targets(&blob0[20], ncpu);
    rom_add_blob_fixed("sony_a6400.stub0", blob0, sizeof(blob0),
                       CXD90045_STUB0);
    if (sony_a6400_shutter_edge_irq) {
        uint32_t shutter[ARRAY_SIZE(cxd90045_shutter_stub)];

        for (size_t i = 0; i < ARRAY_SIZE(shutter); i++) {
            shutter[i] = cpu_to_le32(cxd90045_shutter_stub[i]);
        }
        rom_add_blob_fixed("sony_a6400.shutter_stub", shutter, sizeof(shutter),
                           CXD90045_SHUTTER_STUB);
    }
    for (int i = 0; i < 11; i++) {
        smp[i] = cpu_to_le32(cxd90045_smpstub[i]);
    }
    rom_add_blob_fixed("sony_a6400.smpstub", smp, sizeof(smp),
                       CXD90045_SMPSTUB);
}

static void sony_a6400_cpu_reset(void *opaque)
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
        cpu_set_pc(cs, sony_a6400_shutter_edge_irq ?
                   CXD90045_SHUTTER_STUB : CXD90045_STUB0);
    } else {
        cpu_set_pc(cs, CXD90045_SMPSTUB);
    }
}

/* Standard Linux ATAGS for the rebuilt UI kernel, not the stock Sony ABI. */
static void sony_a6400_menu_atags(MachineState *machine, uint32_t initrd_size)
{
    const char *cmdline = machine->kernel_cmdline ? machine->kernel_cmdline : "";
    size_t cmd_words = (strlen(cmdline) + 1 + 3) / 4;
    size_t count = 5 + 4 + 4 + 2 + cmd_words + 2;
    if (count * 4 > CXD90045_STUB0 - 0x100) {
        error_report("sony-a6400: adapted menu ATAGS exceed reserved space");
        exit(1);
    }
    g_autofree uint32_t *tags = g_new0(uint32_t, count);
    uint32_t *p = tags;

    *p++ = cpu_to_le32(5);
    *p++ = cpu_to_le32(0x54410001); /* ATAG_CORE */
    p += 3;
    *p++ = cpu_to_le32(4);
    *p++ = cpu_to_le32(0x54410002); /* ATAG_MEM */
    *p++ = cpu_to_le32(0x40000000);
    *p++ = 0;
    *p++ = cpu_to_le32(4);
    *p++ = cpu_to_le32(0x54420005); /* ATAG_INITRD2 */
    *p++ = cpu_to_le32(0x10000000);
    *p++ = cpu_to_le32(initrd_size);
    *p++ = cpu_to_le32(2 + cmd_words);
    *p++ = cpu_to_le32(0x54410009); /* ATAG_CMDLINE */
    memcpy(p, cmdline, strlen(cmdline) + 1);
    rom_add_blob_fixed("sony_a6400.menu_atags", tags, count * 4, 0x100);
}

/*
 * Standard ARMv7 Linux ATAGS for custom kernels and OS images (linux-boot=on):
 * placed at 0x00103000 (CXD90045_BOOT_PARAMS) instead of "kemco ...",
 * advertising both CXD90045 DRAM banks (1024 MiB @ 0x00000000, 1016 MiB @
 * 0x80000000, E0416), optional initrd @ 0x10000000, NUL-terminated cmdline,
 * and ATAG_NONE.
 */
static void sony_a6400_linux_atags(MachineState *machine, uint32_t initrd_size)
{
    const char *cmdline = machine->kernel_cmdline ? machine->kernel_cmdline : "";
    size_t cmd_words = (strlen(cmdline) + 1 + 3) / 4;
    size_t count = 5 + 4 + 4 + (initrd_size ? 4 : 0) + 2 + cmd_words + 2;

    if (count * 4 > CXD90045_BOOT_PARAMS_MAX) {
        error_report("sony-a6400: linux-boot ATAGS exceed reserved space");
        exit(1);
    }

    g_autofree uint32_t *tags = g_new0(uint32_t, count);
    uint32_t *p = tags;

    *p++ = cpu_to_le32(5);
    *p++ = cpu_to_le32(0x54410001); /* ATAG_CORE */
    *p++ = 0;
    *p++ = 0;
    *p++ = 0;

    *p++ = cpu_to_le32(4);
    *p++ = cpu_to_le32(0x54410002); /* ATAG_MEM #0 (RAM bank 0: 1024 MiB) */
    *p++ = cpu_to_le32((uint32_t)CXD90045_RAM0_SIZE);
    *p++ = cpu_to_le32((uint32_t)CXD90045_RAM0_BASE);

    *p++ = cpu_to_le32(4);
    *p++ = cpu_to_le32(0x54410002); /* ATAG_MEM #1 (RAM bank 1: 1016 MiB) */
    *p++ = cpu_to_le32((uint32_t)CXD90045_RAM1_SIZE);
    *p++ = cpu_to_le32((uint32_t)CXD90045_RAM1_BASE);

    if (initrd_size > 0) {
        *p++ = cpu_to_le32(4);
        *p++ = cpu_to_le32(0x54420005); /* ATAG_INITRD2 */
        *p++ = cpu_to_le32((uint32_t)CXD90045_LINUX_INITRD_LOAD);
        *p++ = cpu_to_le32(initrd_size);
    }

    *p++ = cpu_to_le32(2 + cmd_words);
    *p++ = cpu_to_le32(0x54410009); /* ATAG_CMDLINE */
    memcpy(p, cmdline, strlen(cmdline) + 1);
    p += cmd_words;

    *p++ = 0; /* ATAG_NONE */
    *p++ = 0;
    g_assert((size_t)(p - tags) == count);

    rom_add_blob_fixed("sony_a6400.linux_atags", tags, count * 4,
                       CXD90045_BOOT_PARAMS);
}

static void sony_a6400_linux_load_dtb(const char *dtb_path)
{
    g_autofree gchar *dtb_data = NULL;
    gsize dtb_len = 0;
    g_autoptr(GError) gerr = NULL;

    if (!g_file_get_contents(dtb_path, &dtb_data, &dtb_len, &gerr)) {
        error_report("sony-a6400: could not read DTB '%s': %s",
                     dtb_path, gerr ? gerr->message : "read error");
        exit(1);
    }
    if (dtb_len < 40 || dtb_len > CXD90045_LINUX_DTB_MAX) {
        error_report("sony-a6400: invalid DTB '%s' size %" G_GSIZE_FORMAT
                     " (expected 40..%" PRIu64 " bytes)",
                     dtb_path, dtb_len, (uint64_t)CXD90045_LINUX_DTB_MAX);
        exit(1);
    }
    if ((uint32_t)ldl_be_p(dtb_data) != 0xD00DFEEDU) {
        error_report("sony-a6400: invalid DTB '%s': bad FDT magic 0x%08x "
                     "(expected 0xd00dfeed)",
                     dtb_path, (uint32_t)ldl_be_p(dtb_data));
        exit(1);
    }
    rom_add_blob_fixed("sony_a6400.linux_dtb", dtb_data, dtb_len,
                       CXD90045_LINUX_DTB_LOAD);
}

static void sony_a6400_load_boot_contract(MachineState *machine,
                                          ARMCPU **cpus, unsigned ncpu)
{
    const char *append = machine->kernel_cmdline ? machine->kernel_cmdline : "";
    bool has_dtb = machine->dtb && *machine->dtb;
    g_autofree char *params = NULL;
    ssize_t size;
    ssize_t initrd_size = 0;
    hwaddr kernel_load = sony_a6400_zve10_menu_boot ?
                          0x00138000 : CXD90045_KERNEL_LOAD;
    hwaddr initrd_load = sony_a6400_linux_boot ?
                          CXD90045_LINUX_INITRD_LOAD :
                         (sony_a6400_zve10_menu_boot ?
                          0x10000000 : CXD90045_INITRD_LOAD);
    uint64_t initrd_max = sony_a6400_linux_boot ?
                          CXD90045_LINUX_INITRD_MAX :
                          (sony_a6400_zve10_menu_boot ?
                           0x10000000 : CXD90045_INITRD_MAX);
    uint64_t kernel_max = sony_a6400_linux_boot ?
                          CXD90045_LINUX_KERNEL_MAX :
                          (initrd_load - kernel_load);

    if (sony_a6400_linux_boot && sony_a6400_zve10_menu_boot) {
        error_report("sony-a6400: linux-boot and zve10-menu-boot are mutually "
                     "exclusive");
        exit(1);
    }
    if (sony_a6400_zve10_menu_boot && ncpu != 1) {
        error_report("sony-a6400: adapted ZV-E10 menu requires -smp 1");
        exit(1);
    }
    for (unsigned i = 0; i < ncpu; i++) {
        qemu_register_reset(sony_a6400_cpu_reset, cpus[i]);
    }
    sony_a6400_write_stubs(ncpu, has_dtb);

    if (machine->kernel_filename) {
        size = load_image_targphys(machine->kernel_filename, kernel_load,
                                   kernel_max, &error_fatal);
        if (size <= 0) {
            error_report("sony-a6400: could not load kernel '%s'",
                         machine->kernel_filename);
            exit(1);
        }
    } else if (!sony_a6400_linux_boot) {
        return;
    }

    if (machine->initrd_filename) {
        initrd_size = load_image_targphys(machine->initrd_filename,
                                          initrd_load, initrd_max,
                                          &error_fatal);
        if (initrd_size <= 0) {
            error_report("sony-a6400: could not load initrd '%s'",
                         machine->initrd_filename);
            exit(1);
        }
    }

    if (sony_a6400_linux_boot) {
        if (has_dtb) {
            sony_a6400_linux_load_dtb(machine->dtb);
        } else {
            sony_a6400_linux_atags(machine, (uint32_t)initrd_size);
        }
        return;
    }

    if (sony_a6400_zve10_menu_boot) {
        sony_a6400_menu_atags(machine, (uint32_t)initrd_size);
    }

    params = g_str_has_prefix(append, "kemco") ? g_strdup(append)
                                                : g_strdup_printf("kemco %s", append);
    if (strlen(params) + 1 > CXD90045_BOOT_PARAMS_MAX) {
        error_report("sony-a6400: kemco boot parameters too long");
        exit(1);
    }
    rom_add_blob_fixed("sony_a6400.kemco", params, strlen(params) + 1,
                       CXD90045_BOOT_PARAMS);
}

/*
 * SIO slave used until a channel has a real peer model (E0526): logs every
 * transfer under -d unimp ("sony-a6400 sio chN") and leaves the FIFO as is,
 * which is what the a5000 SIO model does for a channel without a slave.
 */
/*
 * Sub-micro strobe (E0526), the a5000 board's a5k_submicro_tick applied to
 * the a6400 pins: the request line P2.5 stays low (frame exchange, not
 * command), and the strobe P0.8 gives a 1 ms pulse every 50 ms.  Each edge
 * is a link event 0 (IRQ 129).  The strobe must be low when the engine tick
 * samples it, or the CPU skips its slot-A exchange ("FrtComSkip").  The real
 * micro's timing is unknown [assumption: a5000 values].
 */
static DeviceState *sony_a6400_gpio;
static QEMUTimer *sony_a6400_sm_timer;
static int sony_a6400_sm_step;

static void sony_a6400_submicro_tick(void *opaque)
{
    qemu_set_irq(qdev_get_gpio_in(sony_a6400_gpio, CXD90045_SM_REQ_LINE), 0);
    sony_a6400_sm_step ^= 1;
    qemu_set_irq(qdev_get_gpio_in(sony_a6400_gpio, CXD90045_SM_STROBE_LINE),
                 sony_a6400_sm_step);
    timer_mod(sony_a6400_sm_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
              (sony_a6400_sm_step ? SCALE_MS : 49 * SCALE_MS));
}

static void sony_a6400_sio_log(void *opaque, int ch, uint32_t mode,
                               uint8_t *buf, int len)
{
    char hex[3 * 48 + 1];
    int n = len < 48 ? len : 48;

    for (int i = 0; i < n; i++) {
        snprintf(hex + 3 * i, 4, "%02x ", buf[i]);
    }
    hex[3 * n] = 0;
    qemu_log_mask(LOG_UNIMP, "sony-a6400 sio ch%d mode=0x%08x len=%d tx: %s\n",
                  ch, mode, len, hex);
    /*
     * Diagnostic only (A6400_SIO_MARKER=1): answer with a recognisable
     * marker so the RX buffer can be located in a RAM dump (E0526).
     */
    if (getenv("A6400_SIO_MARKER") && len >= 128) {
        /* type 0x30 slot-B frame, one TLV id 0x13 (12 B) = marker, CRC */
        static const char mark[] = "A6400RXMARK!";
        uint16_t c = 0xffff;
        memset(buf, 0, len);
        buf[0] = 0x30;
        buf[2] = 4 + 12;
        buf[4] = 0x13;
        buf[5] = 12;
        memcpy(buf + 8, mark, 12);
        for (int i = 0; i < 126; i++) {
            c ^= buf[i] << 8;
            for (int b = 0; b < 8; b++) {
                c = (c & 0x8000) ? (c << 1) ^ 0x1021 : c << 1;
            }
        }
        buf[126] = c;
        buf[127] = c >> 8;
    }
}

/*
 * Sub-micro side of the link (E0527): replay the real camera's frames.
 * "submicro-frames" names a 384-byte file = av-cam's micro->CPU link RX
 * buffer as captured from a live ILCE-6400 at PA 0x034433A4..0x03443523
 * (one read-only 4 KiB /dev/mem page): slot A (type 0x20, task-48 packet),
 * slot C (type 0x60) and slot B (type 0x30, TLV status frame), 128 B each,
 * all CRC-16/CCITT-FALSE valid.  The CPU's TX buffer holds 0x21 / 0x61 /
 * 0x31 in the same order.  Like the a5000 board (a5k_submicro_xfer), each
 * CPU frame is answered by its pair: 0x31 -> slot B, 0x61 -> slot C, any
 * other 128-byte frame -> slot A; a 256-byte exchange gets slot A + B.
 * The board updates only its modeled slot-A inputs and the CRC. Clock
 * bytes and other device state remain at their capture values.
 */
static char *sony_a6400_sm_file;
static uint8_t sony_a6400_sm_frames[384];
static bool sony_a6400_sm_loaded;
static bool sony_a6400_sm_lens_power;
static SDBus *sony_a6400_sd_bus;
static bool sony_a6400_rear_controls;
static DeviceState *sony_a6400_rear_adc;
static QKeyCode sony_a6400_adc_owner[2];
static uint16_t sony_a6400_front_pressed;
static bool sony_a6400_power_off;
static bool sony_a6400_af_lever_low;
static bool sony_a6400_eye_sensor_low;
static bool sony_a6400_shutter_half;
static bool sony_a6400_shutter_full;
static uint16_t sony_a6400_wheel_count;

static uint64_t sony_a6400_sd_mode_read(void *opaque, hwaddr offset,
                                       unsigned size)
{
    SDBus *bus = opaque;
    /* No Memory Stick is attached to the shared slot (bit 16 is active low). */
    return BIT(16) |
           (sdbus_get_inserted(bus) ? 0 : BIT(24)) |
           (sdbus_get_readonly(bus) ? BIT(25) : 0);
}

static void sony_a6400_sd_mode_write(void *opaque, hwaddr offset,
                                    uint64_t value, unsigned size)
{
    qemu_log_mask(LOG_UNIMP, "sony-a6400: write to SD MODEREAD ignored\n");
}

static const MemoryRegionOps sony_a6400_sd_mode_ops = {
    .read = sony_a6400_sd_mode_read,
    .write = sony_a6400_sd_mode_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
};

/*
 * Opt-in CXD90045 MISCREG/eFuse and ASV words (E0643):
 *   0xF2915000 EFUSE_CHIPID / EFUSE_TYPEID -> 0x00000500
 *   0xF2915010 MODEREAD -> 0x0C01002B (live ILCE-6400 with SD inserted;
 *              BIT(24) set -> 0x0D01002B when empty/ejected; BIT(25) set when
 *              write-protected)
 *   0xF2927000 ASV voltage-binning -> 0x00000000
 */
static bool sony_a6400_miscreg;

static uint64_t cxd90045_efuse_chipid_read(void *opaque, hwaddr offset,
                                           unsigned size)
{
    return (0x00000500ULL >> (offset * 8)) & MAKE_64BIT_MASK(0, size * 8);
}

static void cxd90045_efuse_chipid_write(void *opaque, hwaddr offset,
                                        uint64_t value, unsigned size)
{
    qemu_log_mask(LOG_GUEST_ERROR,
                  "cxd90045.miscreg: write to EFUSE_CHIPID (0xF2915000+0x%"
                  HWADDR_PRIx ") = 0x%" PRIx64 " ignored\n", offset, value);
}

static const MemoryRegionOps cxd90045_efuse_chipid_ops = {
    .read = cxd90045_efuse_chipid_read,
    .write = cxd90045_efuse_chipid_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static uint64_t cxd90045_miscreg_moderead(void *opaque, hwaddr offset,
                                          unsigned size)
{
    uint32_t val = 0x0C01002BU |
        ((sony_a6400_sd_bus && sdbus_get_inserted(sony_a6400_sd_bus)) ?
         0 : BIT(24)) |
        ((sony_a6400_sd_bus && sdbus_get_readonly(sony_a6400_sd_bus)) ?
         BIT(25) : 0);

    return ((uint64_t)val >> (offset * 8)) & MAKE_64BIT_MASK(0, size * 8);
}

static void cxd90045_miscreg_modewrite(void *opaque, hwaddr offset,
                                       uint64_t value, unsigned size)
{
    qemu_log_mask(LOG_GUEST_ERROR | LOG_UNIMP,
                  "cxd90045.miscreg: write to MODEREAD (0xF2915010+0x%"
                  HWADDR_PRIx ") = 0x%" PRIx64 " ignored\n", offset, value);
}

static const MemoryRegionOps cxd90045_miscreg_mode_ops = {
    .read = cxd90045_miscreg_moderead,
    .write = cxd90045_miscreg_modewrite,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static uint64_t cxd90045_asv_read(void *opaque, hwaddr offset, unsigned size)
{
    return 0;
}

static void cxd90045_asv_write(void *opaque, hwaddr offset, uint64_t value,
                              unsigned size)
{
    qemu_log_mask(LOG_GUEST_ERROR,
                  "cxd90045.miscreg: write to ASV (0xF2927000+0x%"
                  HWADDR_PRIx ") = 0x%" PRIx64 " ignored\n", offset, value);
}

static const MemoryRegionOps cxd90045_asv_ops = {
    .read = cxd90045_asv_read,
    .write = cxd90045_asv_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static void cxd90045_create_miscreg(MemoryRegion *sysmem)
{
    MemoryRegion *efuse = g_new(MemoryRegion, 1);
    MemoryRegion *moderead = g_new(MemoryRegion, 1);
    MemoryRegion *asv = g_new(MemoryRegion, 1);

    memory_region_init_io(efuse, NULL, &cxd90045_efuse_chipid_ops, NULL,
                          "cxd90045.efuse-chipid", 4);
    memory_region_add_subregion_overlap(sysmem, CXD90045_EFUSE_CHIPID,
                                        efuse, 0);

    memory_region_init_io(moderead, NULL, &cxd90045_miscreg_mode_ops, NULL,
                          "cxd90045.miscreg-moderead", 4);
    memory_region_add_subregion_overlap(sysmem, CXD90045_SD_MODEREAD,
                                        moderead, 0);

    memory_region_init_io(asv, NULL, &cxd90045_asv_ops, NULL,
                          "cxd90045.asv", 4);
    memory_region_add_subregion_overlap(sysmem, CXD90045_ASV_BASE, asv, 0);
}

static bool sony_get_miscreg(Object *obj, Error **errp)
{
    return sony_a6400_miscreg;
}

static void sony_set_miscreg(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "miscreg is a startup option");
        return;
    }
    sony_a6400_miscreg = enabled;
}

static uint64_t sony_a6400_wheel_read(void *opaque, hwaddr offset, unsigned size)
{
    return sony_a6400_wheel_count;
}

static void sony_a6400_wheel_write(void *opaque, hwaddr offset, uint64_t value,
                                  unsigned size)
{
    sony_a6400_wheel_count = value;
}

static const MemoryRegionOps sony_a6400_wheel_ops = {
    .read = sony_a6400_wheel_read,
    .write = sony_a6400_wheel_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 2, .max_access_size = 2 },
    .impl = { .min_access_size = 2, .max_access_size = 2 },
};

static void sony_a6400_rear_reset(void *opaque)
{
    memset(sony_a6400_adc_owner, 0, sizeof(sony_a6400_adc_owner));
    sony_a6400_front_pressed = 0;
    sony_a6400_power_off = false;
    sony_a6400_af_lever_low = false;
    sony_a6400_eye_sensor_low = false;
    sony_a6400_shutter_half = false;
    sony_a6400_shutter_full = false;
    sony_a6400_wheel_count = 0;
}

static void sony_a6400_shutter_key(QKeyCode key, bool down)
{
    qemu_irq s1 = qdev_get_gpio_in(sony_a6400_gpio, CXD90045_SHUTTER_S1_LINE);
    qemu_irq s2 = qdev_get_gpio_in(sony_a6400_gpio, CXD90045_SHUTTER_S2_LINE);

    if (key == Q_KEY_CODE_SPC) {
        sony_a6400_shutter_full = down;
        if (!down) {
            qemu_set_irq(s2, 1);
        }
    } else {
        sony_a6400_shutter_half = down;
    }
    /* Full travel closes both contacts; S2 releases before S1. */
    qemu_set_irq(s1, !(sony_a6400_shutter_half || sony_a6400_shutter_full));
    if (key == Q_KEY_CODE_SPC && down) {
        qemu_set_irq(s2, 0);
    }
}

static void sony_a6400_rear_key(DeviceState *dev, QemuConsole *src,
                               QemuInputEvent *event)
{
    static const struct {
        QKeyCode key;
        unsigned channel;
        uint16_t level;
    } keys[] = {
        { Q_KEY_CODE_RET,   3, 45  },
        { Q_KEY_CODE_UP,    3, 182 },
        { Q_KEY_CODE_LEFT,  3, 356 },
        { Q_KEY_CODE_DOWN,  3, 520 },
        { Q_KEY_CODE_RIGHT, 3, 686 },
        { Q_KEY_CODE_DELETE, 0, 45 },
        { Q_KEY_CODE_F,     0, 520 },
    };
    QemuInputKeyEvent *key = &event->key;
    QKeyCode code = qemu_input_linux_to_qcode(key->key);
    uint16_t front = 0;

    switch (code) {
    case Q_KEY_CODE_POWER:
        if (key->down) {
            sony_a6400_power_off = !sony_a6400_power_off;
        }
        return;
    case Q_KEY_CODE_S:
    case Q_KEY_CODE_SPC:
        sony_a6400_shutter_key(code, key->down);
        return;
    case Q_KEY_CODE_BRACKET_LEFT:
    case Q_KEY_CODE_BRACKET_RIGHT:
        if (key->down) {
            /* The captured JogTpu path divides the phase count by four. */
            sony_a6400_wheel_count += code == Q_KEY_CODE_BRACKET_RIGHT ? 4 : -4;
        }
        return;
    case Q_KEY_CODE_A:
        qemu_set_irq(qdev_get_gpio_in(sony_a6400_gpio, 7), !key->down);
        return;
    case Q_KEY_CODE_B:
        if (key->down) {
            sony_a6400_af_lever_low = !sony_a6400_af_lever_low;
            qemu_set_irq(qdev_get_gpio_in(sony_a6400_gpio, 11),
                         !sony_a6400_af_lever_low);
        }
        return;
    case Q_KEY_CODE_E:
        if (key->down) {
            sony_a6400_eye_sensor_low = !sony_a6400_eye_sensor_low;
            qemu_set_irq(qdev_get_gpio_in(sony_a6400_gpio, 2),
                         !sony_a6400_eye_sensor_low);
        }
        return;
    case Q_KEY_CODE_M:
        front = BIT(2);
        break;
    case Q_KEY_CODE_P:
        front = BIT(1);
        break;
    case Q_KEY_CODE_L:
        front = BIT(9);
        break;
    case Q_KEY_CODE_R:
        qemu_set_irq(qdev_get_gpio_in(sony_a6400_gpio, 10), !key->down);
        return;
    default:
        break;
    }
    if (front) {
        if (key->down) {
            sony_a6400_front_pressed |= front;
        } else {
            sony_a6400_front_pressed &= ~front;
        }
        return;
    }
    for (size_t i = 0; i < ARRAY_SIZE(keys); i++) {
        if (keys[i].key != code) {
            continue;
        }
        unsigned channel = keys[i].channel;
        QKeyCode *owner = &sony_a6400_adc_owner[channel != 0];
        if (key->down) {
            *owner = code;
            cxd90014_adc_set(sony_a6400_rear_adc, channel, keys[i].level);
        } else if (*owner == code) {
            *owner = Q_KEY_CODE_UNMAPPED;
            cxd90014_adc_set(sony_a6400_rear_adc, channel, 1023);
        }
        return;
    }
}

static const QemuInputHandler sony_a6400_rear_handler = {
    .name = "Sony a6400 rear controls",
    .mask = INPUT_EVENT_MASK_KEY,
    .event = sony_a6400_rear_key,
};

static bool sony_a6400_get_rear_controls(Object *obj, Error **errp)
{
    return sony_a6400_rear_controls;
}

static void sony_a6400_set_rear_controls(Object *obj, bool value, Error **errp)
{
    sony_a6400_rear_controls = value;
}

static bool sony_get_shutter_edge_irq(Object *obj, Error **errp)
{
    return sony_a6400_shutter_edge_irq;
}

static void sony_set_shutter_edge_irq(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "shutter-edge-irq is a startup option");
        return;
    }
    sony_a6400_shutter_edge_irq = enabled;
}

static bool sony_a6400_campig_enable_readback;
static bool sony_a6400_sgc1_event_a;

/*
 * E0568: Sgc1 input sync experiment. The 36 MHz clock is inferred from
 * the programmed a6400 geometry and the nominal 33367 us frame period.
 */
#define SGC1_BASE 0xF3120000ULL
#define SGC1_CLOCK_HZ 36000000ULL
#define SGC1_START BIT(0)
#define SGC1_CONTINUOUS BIT(8)
#define SGC1_EVENT_A BIT(0)

typedef enum SonySgc1Register {
    SGC1_STATUS, SGC1_ENABLE, SGC1_CONTROL, SGC1_GEOMETRY, SGC1_COMPARE,
    SGC1_REGISTER_COUNT,
} SonySgc1Register;

typedef struct SonySgc1 SonySgc1;

typedef struct SonySgc1Word {
    MemoryRegion mr;
    SonySgc1 *sg;
    SonySgc1Register reg;
} SonySgc1Word;

struct SonySgc1 {
    SonySgc1Word words[SGC1_REGISTER_COUNT];
    uint32_t regs[SGC1_REGISTER_COUNT];
    QEMUTimer *timer;
    qemu_irq irq;
    int64_t frame_end;
    bool compare_pending;
};

static void sony_sgc1_update_irq(SonySgc1 *sg)
{
    qemu_set_irq(sg->irq, (sg->regs[SGC1_STATUS] &
                         sg->regs[SGC1_ENABLE] & SGC1_EVENT_A) != 0);
}

static void sony_sgc1_start_frame(SonySgc1 *sg, int64_t now)
{
    uint32_t geometry = sg->regs[SGC1_GEOMETRY];
    uint64_t lines = ((geometry >> 16) & 0x3fff) + 1;
    uint64_t clocks_per_line = (geometry & 0x3fff) + 1;
    uint64_t compare = sg->regs[SGC1_COMPARE];

    if (!geometry) {
        sg->regs[SGC1_CONTROL] &= ~SGC1_START;
        return;
    }

    sg->frame_end = now + DIV_ROUND_UP(lines * clocks_per_line *
                                       NANOSECONDS_PER_SECOND, SGC1_CLOCK_HZ);
    sg->compare_pending = compare < lines;
    if (sg->compare_pending) {
        timer_mod(sg->timer, now +
                  DIV_ROUND_UP(compare * clocks_per_line *
                               NANOSECONDS_PER_SECOND, SGC1_CLOCK_HZ));
    } else {
        timer_mod(sg->timer, sg->frame_end);
    }
}

static void sony_sgc1_tick(void *opaque)
{
    SonySgc1 *sg = opaque;

    if (sg->compare_pending) {
        sg->compare_pending = false;
        sg->regs[SGC1_STATUS] |= SGC1_EVENT_A;
        sony_sgc1_update_irq(sg);
        timer_mod(sg->timer, sg->frame_end);
    } else if (sg->regs[SGC1_CONTROL] & SGC1_CONTINUOUS) {
        sony_sgc1_start_frame(sg, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
    } else {
        sg->regs[SGC1_CONTROL] &= ~SGC1_START;
    }
}

static uint64_t sony_sgc1_read(void *opaque, hwaddr off, unsigned size)
{
    SonySgc1Word *word = opaque;

    return word->sg->regs[word->reg];
}

static void sony_sgc1_write(void *opaque, hwaddr off, uint64_t value,
                             unsigned size)
{
    SonySgc1Word *word = opaque;
    SonySgc1 *sg = word->sg;
    uint32_t *reg = &sg->regs[word->reg];
    bool running;

    switch (word->reg) {
    case SGC1_STATUS:
        *reg &= ~(value & SGC1_EVENT_A);
        sony_sgc1_update_irq(sg);
        break;
    case SGC1_ENABLE:
        *reg = value & SGC1_EVENT_A;
        sony_sgc1_update_irq(sg);
        break;
    case SGC1_CONTROL:
        running = *reg & SGC1_START;
        *reg = value & (SGC1_START | SGC1_CONTINUOUS);
        if (!(*reg & SGC1_START)) {
            timer_del(sg->timer);
        } else if (!running) {
            sony_sgc1_start_frame(sg, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
        }
        break;
    case SGC1_COMPARE:
        *reg = value & 0x3fff;
        break;
    case SGC1_GEOMETRY:
        *reg = value;
        break;
    default:
        g_assert_not_reached();
    }
}

static const MemoryRegionOps sony_sgc1_ops = {
    .read = sony_sgc1_read,
    .write = sony_sgc1_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void sony_sgc1_reset(void *opaque)
{
    SonySgc1 *sg = opaque;

    timer_del(sg->timer);
    memset(sg->regs, 0, sizeof(sg->regs));
    sg->compare_pending = false;
    sony_sgc1_update_irq(sg);
}

static void sony_sgc1_create(MachineState *machine, qemu_irq irq)
{
    static const hwaddr offsets[SGC1_REGISTER_COUNT] = {
        [SGC1_STATUS] = 0x48, [SGC1_ENABLE] = 0x50, [SGC1_CONTROL] = 0x210,
        [SGC1_GEOMETRY] = 0x220, [SGC1_COMPARE] = 0x224,
    };
    SonySgc1 *sg = g_new0(SonySgc1, 1);

    sg->irq = irq;
    sg->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, sony_sgc1_tick, sg);
    for (size_t i = 0; i < ARRAY_SIZE(offsets); i++) {
        SonySgc1Word *word = &sg->words[i];

        word->sg = sg;
        word->reg = i;
        memory_region_init_io(&word->mr, OBJECT(machine), &sony_sgc1_ops, word,
                              "cxd90045.sgc1-event-a", 4);
        memory_region_add_subregion_overlap(get_system_memory(),
                                            SGC1_BASE + offsets[i],
                                            &word->mr, -500);
    }
    qemu_register_reset(sony_sgc1_reset, sg);
}

static bool sony_a6400_get_sgc1_event_a(Object *obj, Error **errp)
{
    return sony_a6400_sgc1_event_a;
}

static void sony_a6400_set_sgc1_event_a(Object *obj, bool value, Error **errp)
{
    sony_a6400_sgc1_event_a = value;
}

/*
 * Declared stimulus (cond. PROBE gate-force), opt-in and off by default. This
 * is NOT a model or a fix: when enabled it patches the av-cam step-26 producer
 * cbz halfword (0xB103 -> Thumb NOP 0xBF00) at guest PA 0x03874fd0, so the
 * producer always stores 1. That is the same force as F-0047's debugger probe,
 * applied without a debugger. Only the exact av-cam build whose six producer
 * bytes are 03 b1 01 23 ab 71 is patched; the check repeats every 100 ms of
 * virtual time until it has patched once, then the timer stops.
 */
#define GATE_FORCE_PA 0x03874FD0ULL
#define GATE_FORCE_PERIOD_NS (100 * 1000 * 1000LL)

static bool sony_a6400_gate_force_probe;
static QEMUTimer *sony_a6400_gate_force_timer;

static void sony_a6400_gate_force_tick(void *opaque)
{
    static const uint8_t expected[6] = { 0x03, 0xb1, 0x01, 0x23, 0xab, 0x71 };
    uint8_t bytes[6];
    uint16_t nop = 0xbf00;

    address_space_read(&address_space_memory, GATE_FORCE_PA,
                       MEMTXATTRS_UNSPECIFIED, bytes, sizeof(bytes));
    if (memcmp(bytes, expected, sizeof(expected)) != 0) {
        timer_mod(sony_a6400_gate_force_timer,
                  qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + GATE_FORCE_PERIOD_NS);
        return;
    }
    address_space_write(&address_space_memory, GATE_FORCE_PA,
                        MEMTXATTRS_UNSPECIFIED, &nop, sizeof(nop));
    fprintf(stderr, "a6400: gate-force-probe patched 0x03874fd0\n");
}

static bool sony_a6400_get_gate_force_probe(Object *obj, Error **errp)
{
    return sony_a6400_gate_force_probe;
}

static void sony_a6400_set_gate_force_probe(Object *obj, bool value,
                                            Error **errp)
{
    sony_a6400_gate_force_probe = value;
}

/*
 * Declared stimulus (cond. PROBE entry-force), opt-in and off by default. This
 * is NOT a model or a fix: while the camera-control state S (VA 0x6431B594)
 * is 240, it patches the av-cam entry dispatch at VA 0x6386CDC0 from
 * "add.w r3, r0, #-1" to "movs r0, #1; movs r3, #0". The tbh at 0x6386CDD8
 * then selects index 0, so the first entry takes the r0 = 1 path exactly as
 * F-0060's debugger write did (r0 = 1, r10 = 1). Only the exact av-cam bytes
 * 00 f1 ff 33 are patched. The original bytes are restored and the translation
 * blocks invalidated as soon as S leaves 240, so later entries behave
 * naturally. The timer polls every 100 us of virtual time: the S = 240
 * window at the first entry is only tens of ms long (L0068 DBG2).
 */
#define ENTRY_FORCE_PA 0x0386CDC0ULL
#define ENTRY_FORCE_S_PA 0x0431B594ULL
#define ENTRY_FORCE_PERIOD_NS (100 * 1000LL)
#define ENTRY_FORCE_S_READY 0xf0

static bool sony_a6400_entry_force_probe;
static QEMUTimer *sony_a6400_entry_force_timer;

static void sony_a6400_entry_force_tick(void *opaque)
{
    static const uint8_t orig[4] = { 0x00, 0xf1, 0xff, 0x33 };
    static const uint8_t forced[4] = { 0x01, 0x20, 0x00, 0x23 };
    uint8_t bytes[4];
    uint8_t s;

    address_space_read(&address_space_memory, ENTRY_FORCE_S_PA,
                       MEMTXATTRS_UNSPECIFIED, &s, sizeof(s));
    address_space_read(&address_space_memory, ENTRY_FORCE_PA,
                       MEMTXATTRS_UNSPECIFIED, bytes, sizeof(bytes));
    if (s == ENTRY_FORCE_S_READY && memcmp(bytes, orig, sizeof(orig)) == 0) {
        address_space_write(&address_space_memory, ENTRY_FORCE_PA,
                            MEMTXATTRS_UNSPECIFIED, forced, sizeof(forced));
        tb_invalidate_phys_range(NULL, ENTRY_FORCE_PA,
                                 ENTRY_FORCE_PA + sizeof(forced) - 1);
        fprintf(stderr, "a6400: entry-force-probe applied "
                "0x6386cdc0=0x2001,0x2300 (dispatch add.w r3,r0,#-1)\n");
    } else if (s != ENTRY_FORCE_S_READY &&
               memcmp(bytes, forced, sizeof(forced)) == 0) {
        address_space_write(&address_space_memory, ENTRY_FORCE_PA,
                            MEMTXATTRS_UNSPECIFIED, orig, sizeof(orig));
        tb_invalidate_phys_range(NULL, ENTRY_FORCE_PA,
                                 ENTRY_FORCE_PA + sizeof(orig) - 1);
    }
    timer_mod(sony_a6400_entry_force_timer,
              qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + ENTRY_FORCE_PERIOD_NS);
}

static bool sony_a6400_get_entry_force_probe(Object *obj, Error **errp)
{
    return sony_a6400_entry_force_probe;
}

static void sony_a6400_set_entry_force_probe(Object *obj, bool value,
                                             Error **errp)
{
    sony_a6400_entry_force_probe = value;
}

static bool sony_a6400_get_campig_enable_readback(Object *obj, Error **errp)
{
    return sony_a6400_campig_enable_readback;
}

static void sony_a6400_set_campig_enable_readback(Object *obj, bool value,
                                                Error **errp)
{
    sony_a6400_campig_enable_readback = value;
}

/*
 * E0580: the original FUSB handler samples bit 0 at 0xF2920010.
 * Its UDIF descriptor requests rising-edge IRQ 230. A virtual cable
 * transition updates the input before pulsing that interrupt.
 */
typedef struct SonyUsbVbus {
    MemoryRegion status;
    qemu_irq irq;
    bool present;
} SonyUsbVbus;

static bool sony_a6400_usb_vbus_test;
static bool sony_a6400_usb_cid_high;
/* L0154: default on so the E0590 model serves the guest's GCTL/PHY/DCTL
 * accesses; unknown offsets log as cxd90045-dwc3 unimp and the certified
 * boot produces none. */
static bool sony_a6400_dwc3_gadget = true;
static bool sony_a6400_pl330_dma;
static SonyUsbVbus *sony_a6400_usb_vbus;

static uint64_t sony_usb_vbus_read(void *opaque, hwaddr addr, unsigned size)
{
    SonyUsbVbus *vbus = opaque;

    return (vbus->present ? 1U : 0U) | (sony_a6400_usb_cid_high ? 2U : 0U);
}

static bool sony_usb_vbus_accepts(void *opaque, hwaddr addr, unsigned size,
                                  bool is_write, MemTxAttrs attrs)
{
    return !is_write;
}

static const MemoryRegionOps sony_usb_vbus_ops = {
    .read = sony_usb_vbus_read,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
        .accepts = sony_usb_vbus_accepts,
    },
};

static void sony_usb_vbus_reset(void *opaque)
{
    SonyUsbVbus *vbus = opaque;

    vbus->present = false;
    qemu_irq_lower(vbus->irq);
}

static void sony_usb_vbus_create(MachineState *machine, qemu_irq irq)
{
    SonyUsbVbus *vbus = g_new0(SonyUsbVbus, 1);

    vbus->irq = irq;
    memory_region_init_io(&vbus->status, OBJECT(machine), &sony_usb_vbus_ops,
                          vbus, "cxd90045.usb-vbus-status", 4);
    memory_region_add_subregion_overlap(get_system_memory(), 0xF2920010ULL,
                                        &vbus->status, 0);
    qemu_register_reset(sony_usb_vbus_reset, vbus);
    sony_a6400_usb_vbus = vbus;
}

static bool sony_get_usb_vbus_test(Object *obj, Error **errp)
{
    return sony_a6400_usb_vbus_test;
}

static void sony_set_usb_vbus_test(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "usb-vbus-test is a startup option");
        return;
    }
    sony_a6400_usb_vbus_test = enabled;
}

static bool sony_get_usb_cid_high(Object *obj, Error **errp)
{
    return sony_a6400_usb_cid_high;
}

static void sony_set_usb_cid_high(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "usb-cid-high is a startup option");
        return;
    }
    sony_a6400_usb_cid_high = enabled;
}

static bool sony_get_dwc3_gadget(Object *obj, Error **errp)
{
    return sony_a6400_dwc3_gadget;
}

static void sony_set_dwc3_gadget(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "dwc3-gadget is a startup option");
        return;
    }
    sony_a6400_dwc3_gadget = enabled;
}

/*
 * Opt-in hardware watchdog (Custom OS prerequisite 3, E0636).  The stock
 * kernel's cxd900x0_wdt_setup/start/stop and watchdog_touch/tick
 * (nflasha3-Image, 0x60120050..0x60120534) drive one CXD4115 channel at
 * 0xF2402000: CTRL 0x80000012, CMP 1001 * timeout_ms, CTRL 0x80001012
 * (START), kick CLR 0x10, stop CTRL 0 + CLR 0x11.  Off by default so the
 * earlier unmodelled (write-dropped) baseline is unchanged.
 */
static bool sony_a6400_watchdog;

static bool sony_get_watchdog(Object *obj, Error **errp)
{
    return sony_a6400_watchdog;
}

static void sony_set_watchdog(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "watchdog is a startup option");
        return;
    }
    sony_a6400_watchdog = enabled;
}

static bool sony_get_pl330_dma(Object *obj, Error **errp)
{
    return sony_a6400_pl330_dma;
}

static void sony_set_pl330_dma(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "pl330-dma is a startup option");
        return;
    }
    sony_a6400_pl330_dma = enabled;
}

static bool sony_get_usb_vbus(Object *obj, Error **errp)
{
    return sony_a6400_usb_vbus && sony_a6400_usb_vbus->present;
}

static void sony_set_usb_vbus(Object *obj, bool present, Error **errp)
{
    SonyUsbVbus *vbus = sony_a6400_usb_vbus;

    if (!vbus) {
        error_setg(errp, "usb-vbus requires a running machine with usb-vbus-test enabled");
        return;
    }
    if (vbus->present != present) {
        vbus->present = present;
        qemu_irq_pulse(vbus->irq);
    }
}

static char *sony_a6400_liveview_source;
static Cxd90045LiveViewSource sony_a6400_lv_src;

static char *sony_a6400_get_liveview_source(Object *obj, Error **errp)
{
    return g_strdup(sony_a6400_liveview_source);
}

static void sony_a6400_set_liveview_source(Object *obj, const char *value,
                                           Error **errp)
{
    g_free(sony_a6400_liveview_source);
    sony_a6400_liveview_source = g_strdup(value);
}

static uint64_t cxd90045_sif_read(void *opaque, hwaddr off, unsigned size)
{
    uint32_t *r = opaque;
    hwaddr w = off & ~3ULL;
    unsigned sh = (off & 3) * 8;
    uint32_t v;

    if (w == 0x08) {
        r[2] += 100;
        v = r[2];
    } else if (w == 0x0C) {
        r[3] += 1000;
        v = r[3];
    } else {
        v = r[w >> 2];
    }
    return size == 4 ? v : (v >> sh) & ((1u << (size * 8)) - 1);
}

static void cxd90045_sif_write(void *opaque, hwaddr off, uint64_t val,
                               unsigned size)
{
    uint32_t *r = opaque;
    hwaddr w = off & ~3ULL;
    unsigned sh = (off & 3) * 8;
    uint32_t m = size == 4 ? 0xFFFFFFFFu : ((1u << (size * 8)) - 1) << sh;

    r[w >> 2] = (r[w >> 2] & ~m) | (((uint32_t)val << sh) & m);
}

static const MemoryRegionOps cxd90045_sif_ops = {
    .read = cxd90045_sif_read,
    .write = cxd90045_sif_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

/*
 * L0100: 0xF2920000 block, per-offset rule windows for the resolved kernel
 * OTG and av-cam A/E sub-blocks (F-0099 contract table).  The block is not
 * CamCore and not one device, so this is a local rule block instead of a
 * camcore table:
 *
 *   0xF2920000 (0x3000)  kernel FUSB/OTG (udif "otg", PC 0x602AD2A8..;
 *                        4 offsets)
 *   0xF292A000 (0x1000)  av-cam sub-block A (literals at 0x637989C0..;
 *                        5 offsets)
 *   0xF292E000 (0x1000)  av-cam sub-block E (literals at 0x63AA2F18/1C;
 *                        2 offsets)
 *
 * The unresolved 0xF292C000 page is deliberately not mapped (F-0099: no
 * static owner in the bound images; only a carved emmc module names it).
 *
 * Rule kinds: RF reads back the last value written; ZERO reads 0 (F-0099
 * status words the guest never writes, and write-only config words, for
 * which 0 is the certified catch-all baseline).  Writes are stored for
 * every offset.  An offset without a rule is not claimed: it logs through
 * the same "<name>.unmodelled: unimplemented device ..." format and offset
 * base (0xF0000000) as the board catch-all, so an unruled gap in a mapped
 * window still counts as unmodelled.  Each retired offset has its own rule
 * and the F-0099 reading/writing PC.
 */
#define CXD90045_F292_UNMODELLED_BASE 0xF0000000ULL

typedef enum Cxd90045F292Kind {
    CXD90045_F292_ZERO, /* read 0 (status never written, or write-only) */
    CXD90045_F292_RF,   /* read back the last value written */
} Cxd90045F292Kind;

typedef struct Cxd90045F292Rule {
    uint32_t off;
    Cxd90045F292Kind kind;
    const char *why;
} Cxd90045F292Rule;

/* Kernel FUSB/OTG window 0xF2920000..0xF2922FFF (F-0099). */
static const Cxd90045F292Rule cxd90045_f292_rules_otg[] = {
    { 0x0010, CXD90045_F292_ZERO,
      "status VBUS bit0/CID bit1: read 0x602AD2AC/DB74/DB80/DB330, "
      "never written (F-0099)" },
    { 0x0040, CXD90045_F292_RF,
      "RMW ldr 0x602AD5BC / str 0x602AD5E4 (F-0099)" },
    { 0x0050, CXD90045_F292_RF,
      "RMW ldr 0x602AD5EC/0x602AD674 / str 0x602AD694 (F-0099)" },
    { 0x0100, CXD90045_F292_RF,
      "RMW ldr 0x602AD748 / str 0x602AD76C (F-0099)" },
};

/* av-cam sub-block A 0xF292A000..0xF292AFFF (F-0099). */
static const Cxd90045F292Rule cxd90045_f292_rules_avcam_a[] = {
    { 0x010, CXD90045_F292_RF,
      "RMW bfi helper 0x63CEEF86/90/9A/A4/AE; leaf str 0x637989F8 (F-0099)" },
    { 0x044, CXD90045_F292_ZERO,
      "write-only config: str 0x63798A10, helper 0x63CEF008/01C; no "
      "read in the baseline (F-0099)" },
    { 0x070, CXD90045_F292_ZERO,
      "status getter 0x63CEF038 (ubfx #8,#5); never written (F-0099)" },
    { 0x074, CXD90045_F292_ZERO,
      "write-only config: str 0x63CEF030/040; no read in the baseline "
      "(F-0099)" },
    { 0x080, CXD90045_F292_ZERO,
      "status getter 0x63CEF054 (ubfx #8,#5); never written (F-0099)" },
};

/* av-cam sub-block E 0xF292E000..0xF292EFFF (F-0099). */
static const Cxd90045F292Rule cxd90045_f292_rules_avcam_e[] = {
    { 0x008, CXD90045_F292_ZERO,
      "write-only config: 0x63AA2F00 stores 1 via 0x63D99230; no read "
      "in the baseline (F-0099)" },
    { 0x018, CXD90045_F292_ZERO,
      "write-only config: 0x63AA2F0A stores 0x100000 via 0x63D99230; "
      "no read in the baseline (F-0099)" },
};

/*
 * HDMI TX page 0xF292C000..0xF292CFFF (F-0101/L0102).  The page is live:
 * a 120 s certified boot logs 11 distinct (read|write, offset) pairs / 41
 * accesses.  The ten write offsets run at PC 0x5F519BEC / LR 0x5F519BE8
 * (module space) and are write-only in the boot (no read of any of them);
 * +0x160 is a 21-read poll at PC 0x5F519B34 / LR 0x5F519F94 and is never
 * written, so the catch-all returns 0 and ZERO preserves that observed
 * value.  There is no read-follows-write on this page, so no offset is RF.
 * Every rule still stores writes (the shared handler) and reads 0, i.e.
 * the certified catch-all baseline for these offsets.
 */
static const Cxd90045F292Rule cxd90045_f292_rules_c[] = {
    { 0x004, CXD90045_F292_ZERO,
      "2 writes 0x00010000/0x00000000 at PC 0x5F519BEC; no read (F-0101)" },
    { 0x008, CXD90045_F292_ZERO,
      "2 writes 0x001f0000/0x0000000c at PC 0x5F519BEC; no read (F-0101)" },
    { 0x104, CXD90045_F292_ZERO,
      "2 writes 0x1/0x10 at PC 0x5F519BEC; no read (F-0101)" },
    { 0x108, CXD90045_F292_ZERO,
      "2 writes 0x1/0x10 at PC 0x5F519BEC; no read (F-0101)" },
    { 0x114, CXD90045_F292_ZERO,
      "1 write 0x1 at PC 0x5F519BEC; no read (F-0101)" },
    { 0x118, CXD90045_F292_ZERO,
      "1 write 0x1 at PC 0x5F519BEC; no read (F-0101)" },
    { 0x124, CXD90045_F292_ZERO,
      "4 writes 0x001f0000/0x2000/0/0 at PC 0x5F519BEC; no read (F-0101)" },
    { 0x128, CXD90045_F292_ZERO,
      "4 writes 0x007f0000/0x7f00/0x30/0x0f at PC 0x5F519BEC; no read "
      "(F-0101)" },
    { 0x154, CXD90045_F292_ZERO,
      "1 write 0x0e82 at PC 0x5F519BEC; no read (F-0101)" },
    { 0x158, CXD90045_F292_ZERO,
      "1 write 0xffff at PC 0x5F519BEC; no read (F-0101)" },
    { 0x160, CXD90045_F292_ZERO,
      "21 reads at PC 0x5F519B34 / LR 0x5F519F94, never written; returns "
      "0 as the catch-all did (F-0101)" },
};

/*
 * L0105: RCRAW/IDT compressor page 0xF3030000..0xF303FFFF.  A 120 s
 * certified boot (loop/runs/L0105/base-120, qemu sha256 59eaf180...)
 * logs 547 distinct catch-all (read|write, offset) pairs / 9471
 * accesses in the page: 532 write offsets and 15 read offsets, of
 * which 13 are both.  The oracle's `unmodelled_top` list is capped at
 * 200 entries, so only 112 of these pairs appear there (13 reads + 99
 * writes); the full -d unimp log is the ground truth used here.
 *
 * Reader PCs are gdbstub Z3 first hits (readwatch-120/watch.json),
 * writer PCs are Z2 first hits (writewatch-120/watch.json).  Kinds:
 * RF for the 12 offsets where a read follows a write; ZERO for the 2
 * read-only status words and for the 520 non-RF write offsets (519 never
 * read, plus 0x000 read only before its write); 0 is the certified reply.  Writes are
 * stored for every offset.  Unruled offsets in the 64 KiB window still
 * log as cxd90045.f303.unmodelled, so the window is not a blanket zero.
 */
#define CXD90045_F303_BULK(o) \
    { (o), CXD90045_F292_ZERO, \
      "bulk zero-fill word, write 0x63788006/LR 0x63B50B0F, no read (F-0105)" }

static const Cxd90045F292Rule cxd90045_f303_rules[] = {
    { 0x0000, CXD90045_F292_ZERO,
      "read 0x63787F6A/LR 0x63788095, never written; catch-all reply 0 (F-0105)" },
    { 0x0004, CXD90045_F292_RF,
      "write 0x00000001 at 0x63787F80/LR 0x6378808F, read 0x63787F7A/LR 0x6378808F (read-after-write, F-0105)" },
    { 0x0008, CXD90045_F292_ZERO,
      "write-only 0x80000000 at 0x63787F8A/LR 0x6378809D, no read (F-0105)" },
    { 0x000C, CXD90045_F292_RF,
      "write 0x00000001/0x00000002 at 0x63787B34/LR 0x63775513, read 0x63787B2E/LR 0x63775513 (read-after-write, F-0105)" },
    { 0x0030, CXD90045_F292_ZERO,
      "write-only 0x20152000 at 0x63787FA2/LR 0x637880A7, no read (F-0105)" },
    { 0x0084, CXD90045_F292_ZERO,
      "read 0x63787F96/LR 0x63773487, never written; catch-all reply 0 (F-0105)" },
    { 0x0104, CXD90045_F292_ZERO,
      "write-only 0x000000ff at 0x63787B8A/LR 0x63CE7755, no read (F-0105)" },
    { 0x0108, CXD90045_F292_ZERO,
      "write-only 0x000000ff at 0x63787BA2/LR 0x63CE777F, no read (F-0105)" },
    { 0x0110, CXD90045_F292_ZERO,
      "write-only 0x00040000 at 0x63787B4E/LR 0x63CE770B, no read (F-0105)" },
    { 0x0144, CXD90045_F292_ZERO,
      "write-only 0x000000ff at 0x63787BAE/LR 0x63CE775B, no read (F-0105)" },
    { 0x0148, CXD90045_F292_ZERO,
      "write-only 0x000000ff at 0x63787BC6/LR 0x63CE7785, no read (F-0105)" },
    { 0x0204, CXD90045_F292_ZERO,
      "write-only 0x000000ff at 0x63787BD2/LR 0x63CE7761, no read (F-0105)" },
    { 0x0208, CXD90045_F292_ZERO,
      "write-only 0 at 0x63787BEA/LR 0x63CE778B, no read (F-0105)" },
    { 0x0220, CXD90045_F292_ZERO,
      "read 0x63787FEA/LR 0x63B48C71, never written; catch-all reply 0 (F-0105)" },
    { 0x0304, CXD90045_F292_ZERO,
      "write-only 0x000000ff at 0x63787BF6/LR 0x63CE7767, no read (F-0105)" },
    { 0x0308, CXD90045_F292_ZERO,
      "write-only 0 at 0x63787C0E/LR 0x63CE7791, no read (F-0105)" },
    { 0x0404, CXD90045_F292_ZERO,
      "write-only 0x000000ff at 0x63787C1A/LR 0x63CE776D, no read (F-0105)" },
    { 0x0408, CXD90045_F292_ZERO,
      "write-only 0 at 0x63787C32/LR 0x63CE7797, no read (F-0105)" },
    { 0x0504, CXD90045_F292_ZERO,
      "write-only 0x000000ff at 0x63787C3E/LR 0x63CE7773, no read (F-0105)" },
    { 0x0508, CXD90045_F292_ZERO,
      "write-only 0 at 0x63787C56/LR 0x63CE779D, no read (F-0105)" },
    { 0x0604, CXD90045_F292_ZERO,
      "write-only 0x000000ff at 0x63787C62/LR 0x63CE7779, no read (F-0105)" },
    { 0x0608, CXD90045_F292_ZERO,
      "write-only 0 at 0x63787C7A/LR 0x63B50493, no read (F-0105)" },
    CXD90045_F303_BULK(0x1000),
    CXD90045_F303_BULK(0x1004),
    CXD90045_F303_BULK(0x1008),
    CXD90045_F303_BULK(0x100C),
    CXD90045_F303_BULK(0x1010),
    CXD90045_F303_BULK(0x1014),
    CXD90045_F303_BULK(0x1018),
    CXD90045_F303_BULK(0x101C),
    CXD90045_F303_BULK(0x1020),
    CXD90045_F303_BULK(0x1024),
    CXD90045_F303_BULK(0x1028),
    CXD90045_F303_BULK(0x102C),
    CXD90045_F303_BULK(0x1030),
    CXD90045_F303_BULK(0x1034),
    CXD90045_F303_BULK(0x1038),
    CXD90045_F303_BULK(0x103C),
    CXD90045_F303_BULK(0x1040),
    CXD90045_F303_BULK(0x1044),
    CXD90045_F303_BULK(0x1048),
    CXD90045_F303_BULK(0x104C),
    CXD90045_F303_BULK(0x1050),
    CXD90045_F303_BULK(0x1054),
    CXD90045_F303_BULK(0x1058),
    CXD90045_F303_BULK(0x105C),
    CXD90045_F303_BULK(0x1060),
    CXD90045_F303_BULK(0x1064),
    CXD90045_F303_BULK(0x1068),
    CXD90045_F303_BULK(0x106C),
    CXD90045_F303_BULK(0x1070),
    CXD90045_F303_BULK(0x1074),
    CXD90045_F303_BULK(0x1078),
    CXD90045_F303_BULK(0x107C),
    CXD90045_F303_BULK(0x1080),
    CXD90045_F303_BULK(0x1084),
    CXD90045_F303_BULK(0x1088),
    { 0x108C, CXD90045_F292_ZERO,
      "write-only 0x1679dd80/0x1679df00 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x1090, CXD90045_F292_RF,
      "write None at 0x63788006/LR 0x63B50B0F, read 0x63B3FFF0/LR 0x63B3FFED (read-after-write, F-0105)" },
    { 0x1094, CXD90045_F292_RF,
      "write None at 0x63788006/LR 0x63B50B0F, read 0x63B3FFF4/LR 0x63B3FFED (read-after-write, F-0105)" },
    { 0x1098, CXD90045_F292_ZERO,
      "write-only 0x1679e100/0x1679e180 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x109C, CXD90045_F292_ZERO,
      "write-only 0x1679e280 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    CXD90045_F303_BULK(0x10A0),
    { 0x10A4, CXD90045_F292_ZERO,
      "write-only 0x167a1b80/0x167a1c80 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x10A8, CXD90045_F292_RF,
      "write None at 0x63788006/LR 0x63B50B0F, read 0x63B3AFF8/LR 0x63B48CBF (read-after-write, F-0105)" },
    { 0x10AC, CXD90045_F292_RF,
      "write None at 0x63788006/LR 0x63B50B0F, read 0x63B3AFFA/LR 0x63B48CBF (read-after-write, F-0105)" },
    { 0x10B0, CXD90045_F292_ZERO,
      "write-only 0x167a1d80 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    CXD90045_F303_BULK(0x10B4),
    { 0x10B8, CXD90045_F292_RF,
      "write None at 0x63788006/LR 0x63B50B0F, read 0x6378801A/LR 0x63778307 (read-after-write, F-0105)" },
    { 0x10BC, CXD90045_F292_ZERO,
      "write-only 0x167a1e80/0x167a1f00 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x10C0, CXD90045_F292_RF,
      "write None at 0x63788006/LR 0x63B50B0F, read 0x63B3CA9A/LR 0x63B48CC3 (read-after-write, F-0105)" },
    { 0x10C4, CXD90045_F292_RF,
      "write None at 0x63788006/LR 0x63B50B0F, read 0x63B3CAA0/LR 0x63B48CC3 (read-after-write, F-0105)" },
    CXD90045_F303_BULK(0x10C8),
    CXD90045_F303_BULK(0x10CC),
    CXD90045_F303_BULK(0x10D0),
    CXD90045_F303_BULK(0x10D4),
    CXD90045_F303_BULK(0x10D8),
    CXD90045_F303_BULK(0x10DC),
    CXD90045_F303_BULK(0x10E0),
    CXD90045_F303_BULK(0x10E4),
    CXD90045_F303_BULK(0x10E8),
    CXD90045_F303_BULK(0x10EC),
    CXD90045_F303_BULK(0x10F0),
    CXD90045_F303_BULK(0x10F4),
    { 0x10F8, CXD90045_F292_RF,
      "write None at 0x63788006/LR 0x63B50B0F, read 0x63B4EAAC/LR 0x63B48D31 (read-after-write, F-0105)" },
    CXD90045_F303_BULK(0x10FC),
    CXD90045_F303_BULK(0x1100),
    CXD90045_F303_BULK(0x1104),
    CXD90045_F303_BULK(0x1108),
    CXD90045_F303_BULK(0x110C),
    CXD90045_F303_BULK(0x1110),
    CXD90045_F303_BULK(0x1114),
    CXD90045_F303_BULK(0x1118),
    CXD90045_F303_BULK(0x111C),
    CXD90045_F303_BULK(0x1120),
    CXD90045_F303_BULK(0x1124),
    CXD90045_F303_BULK(0x1128),
    CXD90045_F303_BULK(0x112C),
    CXD90045_F303_BULK(0x1130),
    CXD90045_F303_BULK(0x1134),
    CXD90045_F303_BULK(0x1138),
    CXD90045_F303_BULK(0x113C),
    CXD90045_F303_BULK(0x1140),
    CXD90045_F303_BULK(0x1144),
    CXD90045_F303_BULK(0x1148),
    CXD90045_F303_BULK(0x114C),
    CXD90045_F303_BULK(0x1150),
    CXD90045_F303_BULK(0x1154),
    CXD90045_F303_BULK(0x1158),
    CXD90045_F303_BULK(0x115C),
    { 0x1160, CXD90045_F292_ZERO,
      "write-only 0xfe32d500 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x1164, CXD90045_F292_ZERO,
      "write-only 0x17cbc000 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    CXD90045_F303_BULK(0x1168),
    CXD90045_F303_BULK(0x116C),
    CXD90045_F303_BULK(0x1170),
    CXD90045_F303_BULK(0x1174),
    { 0x1178, CXD90045_F292_ZERO,
      "write-only 0x1991f800/0xfe471e80 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x117C, CXD90045_F292_ZERO,
      "write-only 0x00002300 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    CXD90045_F303_BULK(0x1180),
    CXD90045_F303_BULK(0x1184),
    CXD90045_F303_BULK(0x1188),
    CXD90045_F303_BULK(0x118C),
    CXD90045_F303_BULK(0x1190),
    CXD90045_F303_BULK(0x1194),
    CXD90045_F303_BULK(0x1198),
    CXD90045_F303_BULK(0x119C),
    CXD90045_F303_BULK(0x11A0),
    CXD90045_F303_BULK(0x11A4),
    CXD90045_F303_BULK(0x11A8),
    CXD90045_F303_BULK(0x11AC),
    CXD90045_F303_BULK(0x11B0),
    CXD90045_F303_BULK(0x11B4),
    CXD90045_F303_BULK(0x11B8),
    CXD90045_F303_BULK(0x11BC),
    CXD90045_F303_BULK(0x11C0),
    CXD90045_F303_BULK(0x11C4),
    CXD90045_F303_BULK(0x11C8),
    CXD90045_F303_BULK(0x11CC),
    CXD90045_F303_BULK(0x11D0),
    CXD90045_F303_BULK(0x11D4),
    CXD90045_F303_BULK(0x11D8),
    CXD90045_F303_BULK(0x11DC),
    CXD90045_F303_BULK(0x11E0),
    CXD90045_F303_BULK(0x11E4),
    CXD90045_F303_BULK(0x11E8),
    CXD90045_F303_BULK(0x11EC),
    CXD90045_F303_BULK(0x11F0),
    CXD90045_F303_BULK(0x11F4),
    CXD90045_F303_BULK(0x11F8),
    CXD90045_F303_BULK(0x11FC),
    CXD90045_F303_BULK(0x1200),
    CXD90045_F303_BULK(0x1204),
    CXD90045_F303_BULK(0x1208),
    CXD90045_F303_BULK(0x120C),
    CXD90045_F303_BULK(0x1210),
    CXD90045_F303_BULK(0x1214),
    CXD90045_F303_BULK(0x1218),
    CXD90045_F303_BULK(0x121C),
    CXD90045_F303_BULK(0x1220),
    CXD90045_F303_BULK(0x1224),
    CXD90045_F303_BULK(0x1228),
    CXD90045_F303_BULK(0x122C),
    CXD90045_F303_BULK(0x1230),
    CXD90045_F303_BULK(0x1234),
    CXD90045_F303_BULK(0x1238),
    CXD90045_F303_BULK(0x123C),
    CXD90045_F303_BULK(0x1240),
    CXD90045_F303_BULK(0x1244),
    CXD90045_F303_BULK(0x1248),
    CXD90045_F303_BULK(0x124C),
    CXD90045_F303_BULK(0x1250),
    CXD90045_F303_BULK(0x1254),
    CXD90045_F303_BULK(0x1258),
    CXD90045_F303_BULK(0x125C),
    CXD90045_F303_BULK(0x1260),
    CXD90045_F303_BULK(0x1264),
    CXD90045_F303_BULK(0x1268),
    CXD90045_F303_BULK(0x126C),
    CXD90045_F303_BULK(0x1270),
    CXD90045_F303_BULK(0x1274),
    CXD90045_F303_BULK(0x1278),
    CXD90045_F303_BULK(0x127C),
    CXD90045_F303_BULK(0x1280),
    CXD90045_F303_BULK(0x1284),
    CXD90045_F303_BULK(0x1288),
    CXD90045_F303_BULK(0x128C),
    CXD90045_F303_BULK(0x1290),
    CXD90045_F303_BULK(0x1294),
    CXD90045_F303_BULK(0x1298),
    CXD90045_F303_BULK(0x129C),
    CXD90045_F303_BULK(0x12A0),
    CXD90045_F303_BULK(0x12A4),
    CXD90045_F303_BULK(0x12A8),
    CXD90045_F303_BULK(0x12AC),
    CXD90045_F303_BULK(0x12B0),
    CXD90045_F303_BULK(0x12B4),
    CXD90045_F303_BULK(0x12B8),
    CXD90045_F303_BULK(0x12BC),
    CXD90045_F303_BULK(0x12C0),
    CXD90045_F303_BULK(0x12C4),
    CXD90045_F303_BULK(0x12C8),
    CXD90045_F303_BULK(0x12CC),
    CXD90045_F303_BULK(0x12D0),
    CXD90045_F303_BULK(0x12D4),
    CXD90045_F303_BULK(0x12D8),
    CXD90045_F303_BULK(0x12DC),
    CXD90045_F303_BULK(0x12E0),
    CXD90045_F303_BULK(0x12E4),
    CXD90045_F303_BULK(0x12E8),
    CXD90045_F303_BULK(0x12EC),
    CXD90045_F303_BULK(0x12F0),
    CXD90045_F303_BULK(0x12F4),
    CXD90045_F303_BULK(0x12F8),
    CXD90045_F303_BULK(0x12FC),
    CXD90045_F303_BULK(0x1300),
    CXD90045_F303_BULK(0x1304),
    CXD90045_F303_BULK(0x1308),
    CXD90045_F303_BULK(0x130C),
    CXD90045_F303_BULK(0x1310),
    CXD90045_F303_BULK(0x1314),
    CXD90045_F303_BULK(0x1318),
    CXD90045_F303_BULK(0x131C),
    CXD90045_F303_BULK(0x1320),
    CXD90045_F303_BULK(0x1324),
    CXD90045_F303_BULK(0x1328),
    CXD90045_F303_BULK(0x132C),
    CXD90045_F303_BULK(0x1330),
    CXD90045_F303_BULK(0x1334),
    CXD90045_F303_BULK(0x1338),
    CXD90045_F303_BULK(0x133C),
    CXD90045_F303_BULK(0x1340),
    CXD90045_F303_BULK(0x1344),
    CXD90045_F303_BULK(0x1348),
    CXD90045_F303_BULK(0x134C),
    CXD90045_F303_BULK(0x1350),
    CXD90045_F303_BULK(0x1354),
    CXD90045_F303_BULK(0x1358),
    CXD90045_F303_BULK(0x135C),
    CXD90045_F303_BULK(0x1360),
    CXD90045_F303_BULK(0x1364),
    CXD90045_F303_BULK(0x1368),
    CXD90045_F303_BULK(0x136C),
    CXD90045_F303_BULK(0x1370),
    CXD90045_F303_BULK(0x1374),
    CXD90045_F303_BULK(0x1378),
    CXD90045_F303_BULK(0x137C),
    CXD90045_F303_BULK(0x1380),
    CXD90045_F303_BULK(0x1384),
    CXD90045_F303_BULK(0x1388),
    CXD90045_F303_BULK(0x138C),
    CXD90045_F303_BULK(0x1390),
    CXD90045_F303_BULK(0x1394),
    CXD90045_F303_BULK(0x1398),
    CXD90045_F303_BULK(0x139C),
    CXD90045_F303_BULK(0x13A0),
    CXD90045_F303_BULK(0x13A4),
    CXD90045_F303_BULK(0x13A8),
    CXD90045_F303_BULK(0x13AC),
    CXD90045_F303_BULK(0x13B0),
    CXD90045_F303_BULK(0x13B4),
    CXD90045_F303_BULK(0x13B8),
    CXD90045_F303_BULK(0x13BC),
    CXD90045_F303_BULK(0x13C0),
    CXD90045_F303_BULK(0x13C4),
    CXD90045_F303_BULK(0x13C8),
    CXD90045_F303_BULK(0x13CC),
    CXD90045_F303_BULK(0x13D0),
    CXD90045_F303_BULK(0x13D4),
    CXD90045_F303_BULK(0x13D8),
    CXD90045_F303_BULK(0x13DC),
    CXD90045_F303_BULK(0x13E0),
    CXD90045_F303_BULK(0x13E4),
    CXD90045_F303_BULK(0x13E8),
    CXD90045_F303_BULK(0x13EC),
    CXD90045_F303_BULK(0x13F0),
    CXD90045_F303_BULK(0x13F4),
    CXD90045_F303_BULK(0x13F8),
    CXD90045_F303_BULK(0x13FC),
    CXD90045_F303_BULK(0x1400),
    CXD90045_F303_BULK(0x1404),
    CXD90045_F303_BULK(0x1408),
    CXD90045_F303_BULK(0x140C),
    CXD90045_F303_BULK(0x1410),
    CXD90045_F303_BULK(0x1414),
    CXD90045_F303_BULK(0x1418),
    CXD90045_F303_BULK(0x141C),
    CXD90045_F303_BULK(0x1420),
    CXD90045_F303_BULK(0x1424),
    CXD90045_F303_BULK(0x1428),
    CXD90045_F303_BULK(0x142C),
    CXD90045_F303_BULK(0x1430),
    CXD90045_F303_BULK(0x1434),
    CXD90045_F303_BULK(0x1438),
    CXD90045_F303_BULK(0x143C),
    CXD90045_F303_BULK(0x1440),
    CXD90045_F303_BULK(0x1444),
    CXD90045_F303_BULK(0x1448),
    CXD90045_F303_BULK(0x144C),
    CXD90045_F303_BULK(0x1450),
    CXD90045_F303_BULK(0x1454),
    CXD90045_F303_BULK(0x1458),
    CXD90045_F303_BULK(0x145C),
    CXD90045_F303_BULK(0x1460),
    CXD90045_F303_BULK(0x1464),
    CXD90045_F303_BULK(0x1468),
    CXD90045_F303_BULK(0x146C),
    CXD90045_F303_BULK(0x1470),
    CXD90045_F303_BULK(0x1474),
    CXD90045_F303_BULK(0x1478),
    CXD90045_F303_BULK(0x147C),
    CXD90045_F303_BULK(0x1480),
    CXD90045_F303_BULK(0x1484),
    CXD90045_F303_BULK(0x1488),
    CXD90045_F303_BULK(0x148C),
    CXD90045_F303_BULK(0x1490),
    CXD90045_F303_BULK(0x1494),
    CXD90045_F303_BULK(0x1498),
    CXD90045_F303_BULK(0x149C),
    CXD90045_F303_BULK(0x14A0),
    CXD90045_F303_BULK(0x14A4),
    CXD90045_F303_BULK(0x14A8),
    CXD90045_F303_BULK(0x14AC),
    CXD90045_F303_BULK(0x14B0),
    CXD90045_F303_BULK(0x14B4),
    CXD90045_F303_BULK(0x14B8),
    CXD90045_F303_BULK(0x14BC),
    CXD90045_F303_BULK(0x14C0),
    CXD90045_F303_BULK(0x14C4),
    CXD90045_F303_BULK(0x14C8),
    CXD90045_F303_BULK(0x14CC),
    CXD90045_F303_BULK(0x14D0),
    CXD90045_F303_BULK(0x14D4),
    CXD90045_F303_BULK(0x14D8),
    CXD90045_F303_BULK(0x14DC),
    CXD90045_F303_BULK(0x14E0),
    CXD90045_F303_BULK(0x14E4),
    CXD90045_F303_BULK(0x14E8),
    CXD90045_F303_BULK(0x14EC),
    CXD90045_F303_BULK(0x14F0),
    CXD90045_F303_BULK(0x14F4),
    CXD90045_F303_BULK(0x14F8),
    CXD90045_F303_BULK(0x14FC),
    CXD90045_F303_BULK(0x1500),
    CXD90045_F303_BULK(0x1504),
    CXD90045_F303_BULK(0x1508),
    CXD90045_F303_BULK(0x150C),
    CXD90045_F303_BULK(0x1510),
    CXD90045_F303_BULK(0x1514),
    CXD90045_F303_BULK(0x1518),
    CXD90045_F303_BULK(0x151C),
    CXD90045_F303_BULK(0x1520),
    CXD90045_F303_BULK(0x1524),
    CXD90045_F303_BULK(0x1528),
    CXD90045_F303_BULK(0x152C),
    CXD90045_F303_BULK(0x1530),
    CXD90045_F303_BULK(0x1534),
    CXD90045_F303_BULK(0x1538),
    CXD90045_F303_BULK(0x153C),
    CXD90045_F303_BULK(0x1540),
    CXD90045_F303_BULK(0x1544),
    CXD90045_F303_BULK(0x1548),
    CXD90045_F303_BULK(0x154C),
    CXD90045_F303_BULK(0x1550),
    CXD90045_F303_BULK(0x1554),
    CXD90045_F303_BULK(0x1558),
    CXD90045_F303_BULK(0x155C),
    CXD90045_F303_BULK(0x1560),
    CXD90045_F303_BULK(0x1564),
    CXD90045_F303_BULK(0x1568),
    CXD90045_F303_BULK(0x156C),
    CXD90045_F303_BULK(0x1570),
    CXD90045_F303_BULK(0x1574),
    CXD90045_F303_BULK(0x1578),
    CXD90045_F303_BULK(0x157C),
    CXD90045_F303_BULK(0x1580),
    CXD90045_F303_BULK(0x1584),
    CXD90045_F303_BULK(0x1588),
    CXD90045_F303_BULK(0x158C),
    CXD90045_F303_BULK(0x1590),
    CXD90045_F303_BULK(0x1594),
    CXD90045_F303_BULK(0x1598),
    CXD90045_F303_BULK(0x159C),
    CXD90045_F303_BULK(0x15A0),
    CXD90045_F303_BULK(0x15A4),
    CXD90045_F303_BULK(0x15A8),
    CXD90045_F303_BULK(0x15AC),
    CXD90045_F303_BULK(0x15B0),
    CXD90045_F303_BULK(0x15B4),
    CXD90045_F303_BULK(0x15B8),
    CXD90045_F303_BULK(0x15BC),
    CXD90045_F303_BULK(0x15C0),
    CXD90045_F303_BULK(0x15C4),
    CXD90045_F303_BULK(0x15C8),
    CXD90045_F303_BULK(0x15CC),
    CXD90045_F303_BULK(0x15D0),
    CXD90045_F303_BULK(0x15D4),
    CXD90045_F303_BULK(0x15D8),
    CXD90045_F303_BULK(0x15DC),
    CXD90045_F303_BULK(0x15E0),
    CXD90045_F303_BULK(0x15E4),
    CXD90045_F303_BULK(0x15E8),
    CXD90045_F303_BULK(0x15EC),
    CXD90045_F303_BULK(0x15F0),
    CXD90045_F303_BULK(0x15F4),
    CXD90045_F303_BULK(0x15F8),
    CXD90045_F303_BULK(0x15FC),
    CXD90045_F303_BULK(0x1600),
    CXD90045_F303_BULK(0x1604),
    CXD90045_F303_BULK(0x1608),
    CXD90045_F303_BULK(0x160C),
    CXD90045_F303_BULK(0x1610),
    CXD90045_F303_BULK(0x1614),
    CXD90045_F303_BULK(0x1618),
    CXD90045_F303_BULK(0x161C),
    CXD90045_F303_BULK(0x1620),
    CXD90045_F303_BULK(0x1624),
    CXD90045_F303_BULK(0x1628),
    CXD90045_F303_BULK(0x162C),
    CXD90045_F303_BULK(0x1630),
    CXD90045_F303_BULK(0x1634),
    CXD90045_F303_BULK(0x1638),
    CXD90045_F303_BULK(0x163C),
    CXD90045_F303_BULK(0x1640),
    CXD90045_F303_BULK(0x1644),
    CXD90045_F303_BULK(0x1648),
    CXD90045_F303_BULK(0x164C),
    CXD90045_F303_BULK(0x1650),
    CXD90045_F303_BULK(0x1654),
    CXD90045_F303_BULK(0x1658),
    CXD90045_F303_BULK(0x165C),
    CXD90045_F303_BULK(0x1660),
    CXD90045_F303_BULK(0x1664),
    CXD90045_F303_BULK(0x1668),
    CXD90045_F303_BULK(0x166C),
    CXD90045_F303_BULK(0x1670),
    CXD90045_F303_BULK(0x1674),
    CXD90045_F303_BULK(0x1678),
    CXD90045_F303_BULK(0x167C),
    CXD90045_F303_BULK(0x1680),
    CXD90045_F303_BULK(0x1684),
    CXD90045_F303_BULK(0x1688),
    CXD90045_F303_BULK(0x168C),
    CXD90045_F303_BULK(0x1690),
    CXD90045_F303_BULK(0x1694),
    CXD90045_F303_BULK(0x1698),
    CXD90045_F303_BULK(0x169C),
    CXD90045_F303_BULK(0x16A0),
    CXD90045_F303_BULK(0x16A4),
    CXD90045_F303_BULK(0x16A8),
    CXD90045_F303_BULK(0x16AC),
    CXD90045_F303_BULK(0x16B0),
    CXD90045_F303_BULK(0x16B4),
    CXD90045_F303_BULK(0x16B8),
    CXD90045_F303_BULK(0x16BC),
    CXD90045_F303_BULK(0x16C0),
    CXD90045_F303_BULK(0x16C4),
    CXD90045_F303_BULK(0x16C8),
    CXD90045_F303_BULK(0x16CC),
    CXD90045_F303_BULK(0x16D0),
    CXD90045_F303_BULK(0x16D4),
    CXD90045_F303_BULK(0x16D8),
    CXD90045_F303_BULK(0x16DC),
    CXD90045_F303_BULK(0x16E0),
    CXD90045_F303_BULK(0x16E4),
    CXD90045_F303_BULK(0x16E8),
    CXD90045_F303_BULK(0x16EC),
    CXD90045_F303_BULK(0x16F0),
    CXD90045_F303_BULK(0x16F4),
    CXD90045_F303_BULK(0x16F8),
    CXD90045_F303_BULK(0x16FC),
    CXD90045_F303_BULK(0x1700),
    CXD90045_F303_BULK(0x1704),
    CXD90045_F303_BULK(0x1708),
    CXD90045_F303_BULK(0x170C),
    CXD90045_F303_BULK(0x1710),
    CXD90045_F303_BULK(0x1714),
    CXD90045_F303_BULK(0x1718),
    CXD90045_F303_BULK(0x171C),
    CXD90045_F303_BULK(0x1720),
    CXD90045_F303_BULK(0x1724),
    CXD90045_F303_BULK(0x1728),
    CXD90045_F303_BULK(0x172C),
    CXD90045_F303_BULK(0x1730),
    CXD90045_F303_BULK(0x1734),
    CXD90045_F303_BULK(0x1738),
    CXD90045_F303_BULK(0x173C),
    CXD90045_F303_BULK(0x1740),
    CXD90045_F303_BULK(0x1744),
    CXD90045_F303_BULK(0x1748),
    CXD90045_F303_BULK(0x174C),
    CXD90045_F303_BULK(0x1750),
    CXD90045_F303_BULK(0x1754),
    CXD90045_F303_BULK(0x1758),
    CXD90045_F303_BULK(0x175C),
    CXD90045_F303_BULK(0x1760),
    CXD90045_F303_BULK(0x1764),
    CXD90045_F303_BULK(0x1768),
    CXD90045_F303_BULK(0x176C),
    CXD90045_F303_BULK(0x1770),
    CXD90045_F303_BULK(0x1774),
    CXD90045_F303_BULK(0x1778),
    CXD90045_F303_BULK(0x177C),
    CXD90045_F303_BULK(0x1780),
    CXD90045_F303_BULK(0x1784),
    CXD90045_F303_BULK(0x1788),
    CXD90045_F303_BULK(0x178C),
    CXD90045_F303_BULK(0x1790),
    CXD90045_F303_BULK(0x1794),
    CXD90045_F303_BULK(0x1798),
    CXD90045_F303_BULK(0x179C),
    CXD90045_F303_BULK(0x17A0),
    CXD90045_F303_BULK(0x17A4),
    CXD90045_F303_BULK(0x17A8),
    CXD90045_F303_BULK(0x17AC),
    CXD90045_F303_BULK(0x17B0),
    CXD90045_F303_BULK(0x17B4),
    CXD90045_F303_BULK(0x17B8),
    { 0x17BC, CXD90045_F292_ZERO,
      "write-only 0x167a1e00 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x17C0, CXD90045_F292_RF,
      "write 0xfe413e80 at 0x63788006/LR 0x63B50B0F, read 0x63B48C5C/LR 0x63B48C15 (read-after-write, F-0105)" },
    { 0x17C4, CXD90045_F292_ZERO,
      "write-only 0x16796000 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x17C8, CXD90045_F292_RF,
      "write 0x016000f0 at 0x63788006/LR 0x63B50B0F, read 0x63B48C60/LR 0x63B48C15 (read-after-write, F-0105)" },
    CXD90045_F303_BULK(0x17CC),
    CXD90045_F303_BULK(0x17D0),
    { 0x17D4, CXD90045_F292_ZERO,
      "write-only 0x1679af28 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x17D8, CXD90045_F292_ZERO,
      "write-only 0x1679aed8 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x17DC, CXD90045_F292_ZERO,
      "write-only 0x64411be0 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x17E0, CXD90045_F292_ZERO,
      "write-only 0x643cf4f4 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x17E4, CXD90045_F292_ZERO,
      "write-only 0x1679f200 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x17E8, CXD90045_F292_ZERO,
      "write-only 0x1679f230 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x17EC, CXD90045_F292_ZERO,
      "write-only 0x1679f2b0 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x17F0, CXD90045_F292_ZERO,
      "write-only 0x00800080 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x17F4, CXD90045_F292_ZERO,
      "write-only 0x00000004 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x17F8, CXD90045_F292_ZERO,
      "write-only 0x00000004 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
    { 0x17FC, CXD90045_F292_ZERO,
      "write-only 0x00010001 at 0x63788006/LR 0x63B50B0F, no read (F-0105)" },
};
#undef CXD90045_F303_BULK

/*
 * L0108: 0xF3040000 page (PA 0xF3040000..0xF304FFFF).  A 120 s certified
 * boot of the L0105 baseline (loop/runs/L0108/base-120, qemu sha256
 * dea94742...) logs 17 distinct catch-all (read|write, offset) pairs / 67
 * accesses in the low page: 4 read offsets and 13 write offsets, of which
 * 3 are both.  The AIF group at 0xF3041000 (2 channels, 0x800 bytes) stays
 * with the live-view pipeline at -300; the window below is at -600, so it
 * fills only around it.
 *
 * Reader PCs are gdbstub Z3 first hits (loop/runs/L0108/watch-120 and
 * readwatch-004), writer PCs are Z2 first hits.  Kinds: RF for the 3 offsets
 * where a read follows a write (0x108/0x110/0x120); ZERO for the read-only
 * status word 0x004 and for the 10 write-only offsets, where 0 is the
 * certified catch-all reply.  Writes are stored for every offset.  Unruled
 * offsets inside the window still log as cxd90045.f304.unmodelled, so the
 * window is not a blanket zero.
 */
static const Cxd90045F292Rule cxd90045_f304_rules[] = {
    { 0x0004, CXD90045_F292_ZERO,
      "read 0x637884F2/LR 0x63788201, never written; catch-all reply 0 "
      "(F-0108)" },
    { 0x0008, CXD90045_F292_ZERO,
      "write-only 0x00000006/0x00000000 at 0x63788506/LR 0x63788297, no "
      "read (F-0108)" },
    { 0x0100, CXD90045_F292_ZERO,
      "write-only 0x00000001/0x00000000 at 0x6378839A/LR 0x6378818B, no "
      "read (F-0108)" },
    { 0x0108, CXD90045_F292_RF,
      "write 0x00000000/0x00000001 at 0x637883AC/LR 0x63CE78FF, read "
      "0x637883A6/LR 0x63CE78FF (read-after-write, F-0108)" },
    { 0x0110, CXD90045_F292_RF,
      "write 0x00000001 at 0x637883CC/LR 0x637882A5, read 0x637883C6/LR "
      "0x637882A5 (read-after-write, F-0108)" },
    { 0x011C, CXD90045_F292_ZERO,
      "write-only 0x00000000 at 0x637883E2/LR 0x6378817F, no read "
      "(F-0108)" },
    { 0x0120, CXD90045_F292_RF,
      "write 0x00A00000/0x000001AA at 0x637883F6/LR 0x637885D3, read "
      "0x637883F0/LR 0x637885D3 (read-after-write, F-0108)" },
    { 0x0124, CXD90045_F292_ZERO,
      "write-only 0x00000000 at 0x63788422/LR 0x63788279, no read "
      "(F-0108)" },
    { 0x0128, CXD90045_F292_ZERO,
      "write-only 0x167C5200/0x16890A00/0x1695C200/0x16A27A00 at "
      "0x6378842E/LR 0x63CE799F, no read (F-0108)" },
    { 0x012C, CXD90045_F292_ZERO,
      "write-only 0x1685B200/0x16926A00/0x169F2200/0x16ABDA00 at "
      "0x6378843A/LR 0x63CE79BB, no read (F-0108)" },
    { 0x0130, CXD90045_F292_ZERO,
      "write-only 0x16880A00/0x1694C200/0x16A17A00/0x16AE3200 at "
      "0x63788446/LR 0x63CE79D5, no read (F-0108)" },
    { 0x0134, CXD90045_F292_ZERO,
      "write-only 0x1688BE00/0x16957600/0x16A22E00/0x16AEE600 at "
      "0x63788452/LR 0x63CE79EF, no read (F-0108)" },
    { 0x0138, CXD90045_F292_ZERO,
      "write-only 0x1688FA00/0x1695B200/0x16A26A00/0x16AF2200 at "
      "0x6378845E/LR 0x63CE7A09, no read (F-0108)" },
    { 0x013C, CXD90045_F292_ZERO,
      "write-only 0x0000001F at 0x6378846A/LR 0x63CE7A1B, no read "
      "(F-0108)" },
};

/*
 * FUSB block: kernel UDIF resource 'add' at PA 0xF0220000, size 0x20000
 * (F-0106, L0112, L0114).  The bound images show 5 distinct offsets / 9
 * unmodelled pairs.  +0x0, +0x4 and +0x11008 are read-modify-write in the
 * firmware (F-0106), but they are ruled ZERO (read 0, the catch-all value)
 * because RF read-back was non-deterministic in L0112 (F-0112): read-back
 * changed the kernel driver's path.  +0x11080 and +0x11084 are write-only and
 * ZERO.  Writes are stored but never read back.  Probe PC 0x602AD52C, handler
 * 0x602B6778.  Every other offset in the 0x20000 window still logs as
 * unmodelled, so the window is not a blanket zero.  DWC3 (0xF020C000, 0x4000)
 * does not overlap.
 */
static const Cxd90045F292Rule cxd90045_f022_rules[] = {
    { 0x00000, CXD90045_F292_ZERO,
      "read returns 0, the catch-all-equivalent value; RF read-back was "
      "non-deterministic in L0112 (F-0112) (L0114)" },
    { 0x00004, CXD90045_F292_ZERO,
      "read returns 0, the catch-all-equivalent value; RF read-back was "
      "non-deterministic in L0112 (F-0112) (L0114)" },
    { 0x11008, CXD90045_F292_ZERO,
      "read returns 0, the catch-all-equivalent value; RF read-back was "
      "non-deterministic in L0112 (F-0112) (L0114)" },
    { 0x11080, CXD90045_F292_ZERO,
      "write-only; the value is stored but never read back (L0114)" },
    { 0x11084, CXD90045_F292_ZERO,
      "write-only; the value is stored but never read back (L0114)" },
};

/*
 * clkrst3 clock/reset bank at PA 0xF2900000, size 0x1000 (F-0104, L0116).
 * The kernel clk-init and av-cam clock code write these offsets; the reads
 * the catch-all used to answer are 0, and the rows below keep that value.
 * Every row is ZERO (read 0, writes stored and never read back), so the
 * guest sees no change.  +0x030 gets NO row on purpose: its read is
 * branched on (F-0104), so a constant would be a guess, and it must still
 * log as cxd90045.f290.unmodelled.  The 4 KiB page is the only range this
 * window covers; the LDEC page at 0xF2903000 and the 0xF2800000 regfile are
 * separate.  Unruled offsets still log, so the window is not a blanket zero.
 */
static const Cxd90045F292Rule cxd90045_f290_rules[] = {
    { 0x004, CXD90045_F292_ZERO,
      "write/read pair; read returns 0, the catch-all value (F-0104) (L0116)" },
    { 0x008, CXD90045_F292_ZERO,
      "write/read pair; read returns 0, the catch-all value (F-0104) (L0116)" },
    { 0x028, CXD90045_F292_ZERO,
      "kernel clk-init write 0x60122F60..0x60123044; no read (F-0104) (L0116)" },
    { 0x034, CXD90045_F292_ZERO,
      "kernel clk-init write; no read (F-0104) (L0116)" },
    { 0x038, CXD90045_F292_ZERO,
      "kernel clk-init write; no read (F-0104) (L0116)" },
    { 0x044, CXD90045_F292_ZERO,
      "write-only; the value is stored but never read back (F-0104) (L0116)" },
    { 0x0A4, CXD90045_F292_ZERO,
      "write-only; the value is stored but never read back (F-0104) (L0116)" },
    { 0x0A8, CXD90045_F292_ZERO,
      "write-only; the value is stored but never read back (F-0104) (L0116)" },
    { 0x0C4, CXD90045_F292_ZERO,
      "kernel clk-init write/read pair; read returns 0 (F-0104) (L0116)" },
};

/*
 * L0118: four clock/reset-group pages next to the f2a_clk siblings
 * (0xF2A01000..0xF2A30000, f2a_bases[] below, which reads back written values
 * and is NOT extended).  F-0107 names 0xF2A00000 and 0xF2A10000 as the
 * group's offset-0 pages; 0xF2A20000 and 0xF2A32000 are added from the
 * baseline unmodelled list only.  All four are ZERO-only windows: every row
 * reads 0, the catch-all value, and writes are stored but never read back.
 * F-0107 classifies +0x004/+0x008/+0x024/+0x028 as read-modify-write (RF);
 * RF is deferred until their read-back values are captured, so these rows are
 * ZERO.  Write-only rows also answer an (unobserved) read with 0 instead of
 * logging it.  Unruled offsets still log as unmodelled.
 */
static const Cxd90045F292Rule cxd90045_f2a0_rules[] = {
    { 0x008, CXD90045_F292_ZERO,
      "read/write pair; read returns 0, the catch-all value (F-0107) (L0118)" },
    { 0x024, CXD90045_F292_ZERO,
      "read/write pair; read returns 0, the catch-all value (F-0107) (L0118)" },
    { 0x028, CXD90045_F292_ZERO,
      "read/write pair; read returns 0, the catch-all value (F-0107) (L0118)" },
    { 0x0A4, CXD90045_F292_ZERO,
      "write-only; the value is stored but never read back (F-0107) (L0118)" },
};

static const Cxd90045F292Rule cxd90045_f2a1_rules[] = {
    { 0x004, CXD90045_F292_ZERO,
      "read/write pair; read returns 0, the catch-all value (F-0107) (L0118)" },
    { 0x008, CXD90045_F292_ZERO,
      "read/write pair; read returns 0, the catch-all value (F-0107) (L0118)" },
    { 0x024, CXD90045_F292_ZERO,
      "read/write pair; read returns 0, the catch-all value (F-0107) (L0118)" },
    { 0x028, CXD90045_F292_ZERO,
      "read/write pair; read returns 0, the catch-all value (F-0107) (L0118)" },
};

static const Cxd90045F292Rule cxd90045_f2a2_rules[] = {
    { 0x024, CXD90045_F292_ZERO,
      "read/write pair; read returns 0, the catch-all value (F-0107) (L0118)" },
    { 0x028, CXD90045_F292_ZERO,
      "read/write pair; read returns 0, the catch-all value (F-0107) (L0118)" },
};

static const Cxd90045F292Rule cxd90045_f2a3_rules[] = {
    { 0x030, CXD90045_F292_ZERO,
      "write-only; the value is stored but never read back (F-0107) (L0118)" },
    { 0x1A0, CXD90045_F292_ZERO,
      "write-only; the value is stored but never read back (F-0107) (L0118)" },
};

typedef struct Cxd90045F292 {
    MemoryRegion mmio;
    const Cxd90045F292Rule *rules;
    size_t nrules;
    uint32_t *regs;      /* one word per 4 bytes of the mapped window */
    hwaddr base;
    uint64_t size;
    const char *name;
} Cxd90045F292;

static const Cxd90045F292Rule *cxd90045_f292_rule(const Cxd90045F292 *s,
                                                  uint32_t off)
{
    size_t i;

    for (i = 0; i < s->nrules; i++) {
        if (s->rules[i].off == off) {
            return &s->rules[i];
        }
    }
    return NULL;
}

static void cxd90045_f292_log_unlisted(const Cxd90045F292 *s, bool write,
                                       hwaddr off, unsigned size, uint64_t v)
{
    uint64_t o = (s->base + off) - CXD90045_F292_UNMODELLED_BASE;

    if (write) {
        qemu_log_mask(LOG_UNIMP, "%s.unmodelled: unimplemented device write "
                      "(size %u, offset 0x%" PRIx64 ", value 0x%" PRIx64 ")\n",
                      s->name, size, o, v);
    } else {
        qemu_log_mask(LOG_UNIMP, "%s.unmodelled: unimplemented device read "
                      "(size %u, offset 0x%" PRIx64 ")\n",
                      s->name, size, o);
    }
}

static uint64_t cxd90045_f292_read(void *opaque, hwaddr off, unsigned size)
{
    Cxd90045F292 *s = opaque;
    hwaddr w = off & ~3ULL;
    const Cxd90045F292Rule *r = cxd90045_f292_rule(s, w);
    uint32_t v = 0;

    if (!r) {
        cxd90045_f292_log_unlisted(s, false, off, size, 0);
    } else if (r->kind == CXD90045_F292_RF) {
        v = s->regs[w >> 2];
    }
    /* ZERO and unlisted offsets read 0, as the certified catch-all did. */
    v >>= (off & 3) * 8;
    if (size < 4) {
        v &= (1u << (size * 8)) - 1;
    }
    return v;
}

static void cxd90045_f292_write(void *opaque, hwaddr off, uint64_t v,
                                unsigned size)
{
    Cxd90045F292 *s = opaque;
    hwaddr w = off & ~3ULL;
    unsigned sh = (off & 3) * 8;
    uint32_t mask = size >= 4 ? 0xffffffffu : ((1u << (size * 8)) - 1) << sh;
    uint32_t old = s->regs[w >> 2];

    if (!cxd90045_f292_rule(s, w)) {
        cxd90045_f292_log_unlisted(s, true, off, size, v);
    }
    s->regs[w >> 2] = (old & ~mask) | (((uint32_t)v << sh) & mask);
}

static const MemoryRegionOps cxd90045_f292_ops = {
    .read = cxd90045_f292_read,
    .write = cxd90045_f292_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static void cxd90045_f292_create(MemoryRegion *sysmem, hwaddr base,
                                 uint64_t size, const char *name,
                                 const Cxd90045F292Rule *rules,
                                 size_t nrules)
{
    Cxd90045F292 *s = g_new0(Cxd90045F292, 1);

    s->base = base;
    s->size = size;
    s->name = name;
    s->rules = rules;
    s->nrules = nrules;
    s->regs = g_new0(uint32_t, size / sizeof(uint32_t));
    memory_region_init_io(&s->mmio, NULL, &cxd90045_f292_ops, s, name, size);
    memory_region_add_subregion_overlap(sysmem, base, &s->mmio, -600);
}

static void sony_a6400_create_liveview_pipeline(MachineState *machine,
                                                MemoryRegion *sysmem,
                                                qemu_irq *pic)
{
    static const struct {
        hwaddr base;
        uint32_t nch;
        int irq0;
        int irq1;
        uint32_t irq0_mask;
        int32_t lv_ch;
    } aif_groups[] = {
        { 0xF3020000ULL, 6, 0,  0,  0xF0u, -1 },
        { 0xF3041000ULL, 2, 0,  0,  0xF0u, -1 },
        { 0xF3881000ULL, 4, 40, 41, 0xF0u,  3 }, /* RC0 -> live-view DRAM */
        { 0xF3501000ULL, 4, 42, 43, 0xF0u, -1 },
        { 0xF3981000ULL, 4, 44, 45, 0xF0u,  3 }, /* RC1 */
        { 0xF30A1000ULL, 2, 46, 0,  0xF0u, -1 },
        { 0xF30B1000ULL, 2, 47, 0,  0xF0u, -1 },
        { 0xF31A1000ULL, 2, 48, 0,  0xF0u, -1 },
        { 0xF31B1000ULL, 2, 49, 0,  0xF0u, -1 },
        { 0xF31C1000ULL, 2, 50, 0,  0xF0u, -1 },
        { 0xF3811000ULL, 3, 0,  0,  0xF0u, -1 },
        { 0xF3911000ULL, 3, 0,  0,  0xF0u, -1 },
        { 0xF3800000ULL, 5, 0,  0,  0xF0u, -1 },
        { 0xF3900000ULL, 5, 0,  0,  0xF0u, -1 },
        { 0xF31D0000ULL, 4, 0,  0,  0xF0u, -1 },
        { 0xF31E6000ULL, 3, 0,  0,  0xF0u, -1 },
    };
    static const struct {
        const char *name;
        hwaddr base;
    } core_regfiles[] = {
        { "cxd90045.rc0-core",  0xF3880000ULL },
        { "cxd90045.rc1-core",  0xF3980000ULL },
        { "cxd90045.f350-core", 0xF3500000ULL },
    };
    DeviceState *sg, *frames, *pig[2], *fe_aif[2];
    MemoryRegion *sif_mmio = g_new(MemoryRegion, 1);
    uint32_t *sif_regs = g_new0(uint32_t, 0x400);

    cxd90045_liveview_source_parse(&sony_a6400_lv_src,
                                   sony_a6400_liveview_source, &error_fatal);

    sg = qdev_new(TYPE_CXD90045_CAMPIG);
    qdev_prop_set_bit(sg, "is-sg", true);
    qdev_prop_set_uint32(sg, "mmio-size", 0x2000);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(sg), &error_fatal);
    sysbus_mmio_map_overlap(SYS_BUS_DEVICE(sg), 0, 0xF3120000ULL, -400);
    sysbus_connect_irq(SYS_BUS_DEVICE(sg), 0, pic[35 - 32]);

    frames = qdev_new(TYPE_SPLIT_IRQ);
    qdev_prop_set_uint16(frames, "num-lines", ARRAY_SIZE(pig));
    qdev_realize_and_unref(frames, NULL, &error_fatal);
    qdev_connect_gpio_out_named(sg, "frame", 0, qdev_get_gpio_in(frames, 0));

    memory_region_init_io(sif_mmio, OBJECT(machine), &cxd90045_sif_ops,
                          sif_regs, "cxd90045.sif", 0x1000);
    memory_region_add_subregion_overlap(sysmem, 0xF3122000ULL, sif_mmio, -400);

    for (int u = 0; u < 2; u++) {
        hwaddr fe_base = 0xF3000000ULL + (hwaddr)u * 0x100000ULL;

        pig[u] = qdev_new(TYPE_CXD90045_CAMPIG);
        qdev_prop_set_uint32(pig[u], "mmio-size", 0x20000);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(pig[u]), &error_fatal);
        cxd90045_campig_set_source(pig[u], &sony_a6400_lv_src);
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(pig[u]), 0, fe_base, -400);
        sysbus_connect_irq(SYS_BUS_DEVICE(pig[u]), 0, pic[(36 + u) - 32]);
        qdev_connect_gpio_out(frames, u,
                             qdev_get_gpio_in_named(pig[u], "input-frame", 0));

        fe_aif[u] = qdev_new(TYPE_CXD90045_AIF);
        qdev_prop_set_uint32(fe_aif[u], "nch", 6);
        qdev_prop_set_bit(fe_aif[u], "frame-sync", true);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(fe_aif[u]), &error_fatal);
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(fe_aif[u]), 0,
                                fe_base + 0x2000ULL, -300);
        qdev_connect_gpio_out_named(pig[u], "frame", 0,
                                    qdev_get_gpio_in_named(fe_aif[u],
                                                           "frame", 0));
    }

    for (size_t k = 0; k < ARRAY_SIZE(core_regfiles); k++) {
        DeviceState *rf = qdev_new("cxd90014-regfile");

        qdev_prop_set_string(rf, "name", core_regfiles[k].name);
        qdev_prop_set_uint64(rf, "size", 0x1000);
        qdev_prop_set_uint64(rf, "base", core_regfiles[k].base);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(rf), &error_fatal);
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(rf), 0,
                                core_regfiles[k].base, -400);
    }

    for (size_t k = 0; k < ARRAY_SIZE(aif_groups); k++) {
        DeviceState *aif = qdev_new(TYPE_CXD90045_AIF);

        qdev_prop_set_uint32(aif, "nch", aif_groups[k].nch);
        qdev_prop_set_uint32(aif, "irq0-mask", aif_groups[k].irq0_mask);
        qdev_prop_set_int32(aif, "liveview-ch", aif_groups[k].lv_ch);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(aif), &error_fatal);
        if (aif_groups[k].lv_ch >= 0) {
            cxd90045_aif_set_source(aif, &sony_a6400_lv_src);
        }
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(aif), 0,
                                aif_groups[k].base, -300);
        if (aif_groups[k].irq0) {
            sysbus_connect_irq(SYS_BUS_DEVICE(aif), 0,
                               pic[aif_groups[k].irq0 - 32]);
        }
        if (aif_groups[k].irq1) {
            sysbus_connect_irq(SYS_BUS_DEVICE(aif), 1,
                               pic[aif_groups[k].irq1 - 32]);
        }
    }
}

static void sony_a6400_submicro_crc(uint8_t frame[128])
{
    uint16_t crc = 0xffff;

    for (unsigned i = 0; i < 126; i++) {
        crc ^= frame[i] << 8;
        for (unsigned bit = 0; bit < 8; bit++) {
            crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : crc << 1;
        }
    }
    frame[126] = crc;
    frame[127] = crc >> 8;
}

static const uint8_t *sony_a6400_submicro_tlv(const uint8_t frame[128],
                                             uint8_t id, unsigned min_len)
{
    unsigned end = 4 + frame[2] + ((unsigned)frame[3] << 8);

    if (end > 126) {
        return NULL;
    }
    for (unsigned pos = 4; pos + 4 <= end;) {
        unsigned size = frame[pos + 1];
        if (size > end - pos - 4) {
            return NULL;
        }
        if (frame[pos] == id) {
            return size >= min_len ? frame + pos + 4 : NULL;
        }
        pos += 4 + size;
    }
    return NULL;
}

static void sony_a6400_submicro_lens_reply(const uint8_t tx[128],
                                           uint8_t reply[128])
{
    const uint8_t *request = sony_a6400_submicro_tlv(tx, 0x0a, 2);
    const uint8_t *status = sony_a6400_submicro_tlv(reply, 0x03, 4);
    unsigned pos;

    if (!request || !status) {
        qemu_log_mask(LOG_UNIMP, "sony-a6400: missing lens power TLV\n");
        return;
    }
    /* av-cam 0x6386ACDA requests the rail; 0x6386AC28 reads its status.
     * The virtual supply settles at the SPI exchange. No lens is simulated. */
    pos = (status - reply) + 3;
    reply[pos] = (reply[pos] & ~BIT(0)) | (request[1] & BIT(0));
    sony_a6400_submicro_crc(reply);
}

static void sony_a6400_submicro_xfer(void *opaque, int ch, uint32_t mode,
                                    uint8_t *buf, int len)
{
    uint8_t out[256];

    sony_a6400_sio_log(opaque, ch, mode, buf, len);
    if (!sony_a6400_sm_loaded || len < 128) {
        return;
    }
    memcpy(out, sony_a6400_sm_frames +
           (buf[0] == 0x31 ? 256 : buf[0] == 0x61 ? 128 : 0), 128);
    if (out[0] == 0x20 &&
        (sony_a6400_sd_bus || (sony_a6400_rear_controls &&
         (sony_a6400_front_pressed || sony_a6400_power_off)))) {
        /* Captured ORIL 0x63A755C6 decodes slot A byte 2 bit 4 as
         * SD0 presence in the seven-byte front media report. */
        if (sony_a6400_sd_bus) {
            out[2] &= ~BIT(4);
            if (sdbus_get_inserted(sony_a6400_sd_bus)) {
                out[2] |= BIT(4);
            }
        }
        /* E0526: the OFF position clears CAkey1 bit 0. */
        if (sony_a6400_rear_controls) {
            if (sony_a6400_power_off) {
                out[28] &= ~BIT(0);
            }
            out[28] |= sony_a6400_front_pressed & (BIT(1) | BIT(2));
            out[29] &= ~(sony_a6400_front_pressed >> 8);
        }
        sony_a6400_submicro_crc(out);
    } else if (sony_a6400_sm_lens_power && buf[0] == 0x31 && out[0] == 0x30) {
        sony_a6400_submicro_lens_reply(buf, out);
    }
    if (len >= 256) {
        memcpy(out + 128, sony_a6400_sm_frames + 256, 128);
        if (sony_a6400_sm_lens_power && buf[128] == 0x31 &&
            out[128] == 0x30) {
            sony_a6400_submicro_lens_reply(buf + 128, out + 128);
        }
    }
    memcpy(buf, out, len >= 256 ? 256 : 128);
}

static char *sony_a6400_get_sm_file(Object *obj, Error **errp)
{
    return g_strdup(sony_a6400_sm_file);
}

static bool sony_a6400_get_sm_lens_power(Object *obj, Error **errp)
{
    return sony_a6400_sm_lens_power;
}

static void sony_a6400_set_sm_lens_power(Object *obj, bool enabled, Error **errp)
{
    sony_a6400_sm_lens_power = enabled;
}

/*
 * "hostwin" (developer option, not a hardware model): a host file mapped as shared RAM at
 * PA 0x50000000 (16 MiB, between RAM bank 0 and bank 1, unused by the CXD90045 map). The host
 * writes test files into it and a guest helper copies them out through /dev/mem, so test inputs
 * reach a running guest without the serial console (qemu-a6400/tools/warm.py, hostwin_get).
 */
#define CXD90045_HOSTWIN_PA   0x50000000ULL
#define CXD90045_HOSTWIN_SIZE 0x01000000ULL
static char *sony_a6400_hostwin;

static char *sony_a6400_get_hostwin(Object *obj, Error **errp)
{
    return g_strdup(sony_a6400_hostwin);
}

static void sony_a6400_set_hostwin(Object *obj, const char *value, Error **errp)
{
    g_free(sony_a6400_hostwin);
    sony_a6400_hostwin = g_strdup(value);
}

static void sony_a6400_set_sm_file(Object *obj, const char *value,
                                   Error **errp)
{
    g_free(sony_a6400_sm_file);
    sony_a6400_sm_file = g_strdup(value);
}

/*
 * Multi-body CXD90045 hardware catalog matching DEFAULT_REGISTRY in
 * src/a6400/targets/catalog.py (8 modeled camera bodies).
 */
typedef struct SonyBodyProfile {
    const char *machine_name;
    const char *target_id;
    const char *short_alias;
    const char *desc;
    bool has_evf;       /* false on EVF-less ZV-E10 -> P0.2 idle high */
    bool has_ibis_imu;  /* 6-axis gyro/accel responder on SIO ch4 */
    bool has_dual_sd;   /* second SDHCI removable slot at mmc2 (0xF0302000) */
    bool full_frame;    /* defaults kit E-mount lens to FE 50mm F1.8 vs PZ1650 */
} SonyBodyProfile;

static const SonyBodyProfile sony_body_profiles[] = {
    {
        "sony-a6400",  "ilce_6400",  "a6400",
        "Sony ILCE-6400 (BIONZ X / CXD90045)",
        true,  false, false, false,
    },
    {
        "sony-a6400a", "ilce_6400a", "a6400a",
        "Sony ILCE-6400A (BIONZ X / CXD90045, J3 panel)",
        true,  false, false, false,
    },
    {
        "sony-zve10",  "zv_e10",     "zve10",
        "Sony ZV-E10 (BIONZ X / CXD90045, vari-angle, no EVF)",
        false, true,  false, false,
    },
    {
        "sony-a6100",  "ilce_6100",  "a6100",
        "Sony ILCE-6100 (BIONZ X / CXD90045)",
        true,  false, false, false,
    },
    {
        "sony-a6600",  "ilce_6600",  "a6600",
        "Sony ILCE-6600 (BIONZ X / CXD90045, IBIS)",
        true,  true,  false, false,
    },
    {
        "sony-a7c",    "ilce_7c",    "a7c",
        "Sony ILCE-7C (BIONZ X / CXD90045, Full-Frame IBIS)",
        true,  true,  false, true,
    },
    {
        "sony-a7m3",   "ilce_7m3",   "a7m3",
        "Sony ILCE-7M3 (BIONZ X / CXD90045, Full-Frame IBIS, Dual SD)",
        true,  true,  true,  true,
    },
    {
        "sony-a7rm4a", "ilce_7rm4a", "a7rm4a",
        "Sony ILCE-7RM4A (BIONZ X / CXD90045, Full-Frame IBIS, Dual SD, J3)",
        true,  true,  true,  true,
    },
};

static char *sony_a6400_camera_model;
static const SonyBodyProfile *sony_active_body = &sony_body_profiles[0];
static bool sony_a6400_peripherals;
static bool sony_a6400_touchscreen;
static bool sony_a6400_bcm4339;
static bool sony_a6400_hdmi_cec;
static bool sony_a6400_audio_io;
static bool sony_a6400_audio_mic_plugged;
static char *sony_a6400_audio_input;
static char *sony_a6400_emount_lens;

static const SonyBodyProfile *sony_find_body_profile(const char *key)
{
    if (!key || !*key) {
        return NULL;
    }
    for (size_t i = 0; i < ARRAY_SIZE(sony_body_profiles); i++) {
        const SonyBodyProfile *p = &sony_body_profiles[i];
        if (g_ascii_strcasecmp(key, p->machine_name) == 0 ||
            g_ascii_strcasecmp(key, p->target_id) == 0 ||
            g_ascii_strcasecmp(key, p->short_alias) == 0) {
            return p;
        }
    }
    return NULL;
}

static char *sony_get_camera_model(Object *obj, Error **errp)
{
    if (sony_a6400_camera_model && *sony_a6400_camera_model) {
        const SonyBodyProfile *p = sony_find_body_profile(sony_a6400_camera_model);
        return g_strdup(p ? p->target_id : sony_a6400_camera_model);
    }
    MachineClass *mc = MACHINE_GET_CLASS(obj);
    const SonyBodyProfile *p = sony_find_body_profile(mc->name);
    return g_strdup(p ? p->target_id : "ilce_6400");
}

static void sony_set_camera_model(Object *obj, const char *value, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "camera-model is a startup option");
        return;
    }
    if (value && *value && !sony_find_body_profile(value)) {
        error_setg(errp, "unknown CXD90045 camera-model '%s' (expected "
                   "ilce_6400, ilce_6400a, zv_e10, ilce_6100, ilce_6600, "
                   "ilce_7c, ilce_7m3, or ilce_7rm4a)", value);
        return;
    }
    g_free(sony_a6400_camera_model);
    sony_a6400_camera_model = g_strdup(value);
}

static bool sony_get_peripherals(Object *obj, Error **errp)
{
    return sony_a6400_peripherals;
}

static void sony_set_peripherals(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "peripherals is a startup option");
        return;
    }
    sony_a6400_peripherals = enabled;
}

static bool sony_get_touchscreen(Object *obj, Error **errp)
{
    return sony_a6400_touchscreen || sony_a6400_peripherals;
}

static void sony_set_touchscreen(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "touchscreen is a startup option");
        return;
    }
    sony_a6400_touchscreen = enabled;
}

static bool sony_get_bcm4339(Object *obj, Error **errp)
{
    return sony_a6400_bcm4339 || sony_a6400_peripherals;
}

static void sony_set_bcm4339(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "bcm4339 is a startup option");
        return;
    }
    sony_a6400_bcm4339 = enabled;
}

static bool sony_get_hdmi_cec(Object *obj, Error **errp)
{
    return sony_a6400_hdmi_cec || sony_a6400_peripherals;
}

static void sony_set_hdmi_cec(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "hdmi-cec is a startup option");
        return;
    }
    sony_a6400_hdmi_cec = enabled;
}

static bool sony_get_audio_io(Object *obj, Error **errp)
{
    return sony_a6400_audio_io || sony_a6400_peripherals;
}

static void sony_set_audio_io(Object *obj, bool enabled, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "audio-io is a startup option");
        return;
    }
    sony_a6400_audio_io = enabled;
}

static bool sony_get_audio_mic_plugged(Object *obj, Error **errp)
{
    return sony_a6400_audio_mic_plugged;
}

static void sony_set_audio_mic_plugged(Object *obj, bool enabled, Error **errp)
{
    sony_a6400_audio_mic_plugged = enabled;
}

static char *sony_get_audio_input(Object *obj, Error **errp)
{
    return g_strdup(sony_a6400_audio_input ? sony_a6400_audio_input : "sine440");
}

static void sony_set_audio_input(Object *obj, const char *value, Error **errp)
{
    g_free(sony_a6400_audio_input);
    sony_a6400_audio_input = g_strdup(value);
}

static char *sony_get_emount_lens(Object *obj, Error **errp)
{
    if (sony_a6400_emount_lens && *sony_a6400_emount_lens) {
        return g_strdup(sony_a6400_emount_lens);
    }
    if (sony_a6400_peripherals) {
        return g_strdup(sony_active_body->full_frame ? "fe50f18" : "selp1650");
    }
    return g_strdup("none");
}

static void sony_set_emount_lens(Object *obj, const char *value, Error **errp)
{
    if (phase_check(PHASE_MACHINE_INITIALIZED)) {
        error_setg(errp, "emount-lens is a startup option");
        return;
    }
    g_free(sony_a6400_emount_lens);
    sony_a6400_emount_lens = g_strdup(value);
}

/*
 * Stationary 6-axis MEMS IMU responder on SIO channel 4 (0xF2405800, IRQ 205)
 * for IBIS and active-gyro bodies (ZV-E10, ILCE-6600, ILCE-7C, ILCE-7M3,
 * ILCE-7RM4A): answers SPI register reads (bit 7 = 1) with WHO_AM_I = 0x69,
 * gyro = [0, 0, 0], and accel = [0, 0, +16384 (+1g)].
 */
static void sony_cxd90045_imu_xfer(void *opaque, int ch, uint32_t mode,
                                   uint8_t *buf, int len)
{
    sony_a6400_sio_log(opaque, ch, mode, buf, len);
    if (len < 2 || !(buf[0] & 0x80)) {
        return;
    }
    uint8_t reg = buf[0] & 0x7Fu;
    buf[0] = 0x00;
    for (int i = 1; i < len; i++) {
        uint8_t r = (uint8_t)(reg + (i - 1));
        switch (r) {
        case 0x0F: /* WHO_AM_I (BMI160/LSM6DS-style IMU ID) */
        case 0x75: /* MPU-style WHO_AM_I */
            buf[i] = 0x69;
            break;
        case 0x1E: /* STATUS: gyro + accel data ready */
            buf[i] = 0x03;
            break;
        case 0x2C: /* Accel Z low (+1g = 0x4000 LE) */
            buf[i] = 0x00;
            break;
        case 0x2D: /* Accel Z high */
            buf[i] = 0x40;
            break;
        case 0x3F: /* Big-endian Accel Z high (+1g = 0x4000 BE) */
            buf[i] = 0x40;
            break;
        default:
            buf[i] = 0x00;
            break;
        }
    }
}

/*
 * L0137: SA core 4 (base 0xF35D0000, index 4 of av-cam's SA base pool) ID
 * words.  av-cam 0x63A7ED50 compares them with the firmware constant
 * "SA1_V1.0SA5_V1.0" (F-0136); only the ID is evidenced, so the rest of the
 * core is left unmodelled (no IRQs, no other registers).  Expected value
 * from firmware, not a bus capture.
 */
static const uint8_t cxd90045_sa4_id[16] = {
    'S', 'A', '1', '_', 'V', '1', '.', '0',
    'S', 'A', '5', '_', 'V', '1', '.', '0',
};

static uint64_t cxd90045_sa4_id_read(void *opaque, hwaddr off, unsigned size)
{
    uint64_t v = 0;
    unsigned i;

    for (i = 0; i < size; i++) {
        v |= (uint64_t)cxd90045_sa4_id[(off + i) & 0xF] << (i * 8);
    }
    return v;
}

static void cxd90045_sa4_id_write(void *opaque, hwaddr off, uint64_t v,
                                  unsigned size)
{
    qemu_log_mask(LOG_UNIMP,
                  "cxd90045.sa4-id: write +0x%x size %u value 0x%" PRIx64
                  " ignored\n", (unsigned)off, size, v);
}

static const MemoryRegionOps cxd90045_sa4_id_ops = {
    .read = cxd90045_sa4_id_read,
    .write = cxd90045_sa4_id_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static void sony_a6400_init(MachineState *machine)
{
    MachineClass *mc = MACHINE_GET_CLASS(machine);
    const SonyBodyProfile *body =
        sony_find_body_profile(sony_a6400_camera_model);
    if (!body) {
        body = sony_find_body_profile(mc->name);
    }
    if (!body) {
        body = &sony_body_profiles[0];
    }
    sony_active_body = body;

    bool touch_on = sony_a6400_touchscreen || sony_a6400_peripherals;
    bool bcm4339_on = sony_a6400_bcm4339 || sony_a6400_peripherals;
    bool hdmi_cec_on = sony_a6400_hdmi_cec || sony_a6400_peripherals;
    bool audio_io_on = sony_a6400_audio_io || sony_a6400_peripherals;
    const char *lens_cfg = (sony_a6400_emount_lens && *sony_a6400_emount_lens) ?
        sony_a6400_emount_lens :
        (sony_a6400_peripherals ?
         (body->full_frame ? "fe50f18" : "selp1650") : NULL);
    bool lens_on = lens_cfg && strcmp(lens_cfg, "none") != 0 &&
                   strcmp(lens_cfg, "off") != 0;
    if (lens_on) {
        sony_a6400_sm_lens_power = true;
    }

    bool liveview = sony_a6400_liveview_source && *sony_a6400_liveview_source;
    MemoryRegion *sysmem = get_system_memory();
    MemoryRegion *ram0 = g_new(MemoryRegion, 1);
    MemoryRegion *ram1 = g_new(MemoryRegion, 1);
    MemoryRegion *sram = g_new(MemoryRegion, 1);
    CxdRwStub *sugi_clk;
    DeviceState *a9mp;
    DeviceState *i2c0_dev = NULL;
    DeviceState *vph_dev = NULL;
    SysBusDevice *sbd;
    qemu_irq pic[CXD90045_NUM_IRQS];
    unsigned ncpu = machine->smp.cpus;
    ARMCPU *cpus[CXD90045_MAX_CPUS];
    DeviceState *timer;
    BlockBackend *sd_blk;
    BlockBackend *sd2_blk;
    int i;

    sony_a6400_sd_bus = NULL;

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

    memory_region_init_ram(ram0, NULL, "sony_a6400.ram0",
                           CXD90045_RAM0_SIZE, &error_fatal);
    memory_region_add_subregion(sysmem, CXD90045_RAM0_BASE, ram0);

    memory_region_init_ram(ram1, NULL, "sony_a6400.ram1",
                           CXD90045_RAM1_SIZE, &error_fatal);
    memory_region_add_subregion(sysmem, CXD90045_RAM1_BASE, ram1);

    memory_region_init_ram(sram, NULL, "sony_a6400.sram",
                           CXD90045_SRAM_SIZE, &error_fatal);
    memory_region_add_subregion(sysmem, CXD90045_SRAM_BASE, sram);

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

    pl011_create(CXD90045_UART0_BASE, pic[CXD90045_UART0_SPI], serial_hd(0));
    /*
     * UART3 is a second, independent PL011.  The kernel's udif table names it
     * "uart3" at 0xF2003000 (size 0x1000, IRQ 120) and AMBA-probes it as
     * uart_devs[3]; without the device the PrimeCell ID reads return 0 and
     * registration fails.  The stock pl011 already exposes PID 0x00141011 /
     * CID 0xB105F00D, matching the kernel's expected AMBA IDs.  No second
     * -serial is passed, so the chardev is NULL and UART0/console is untouched.
     */
    {
        Chardev *uart3_chr = serial_hd(1);
        if (!uart3_chr && bcm4339_on) {
            uart3_chr = cxd90045_bcm4339_bt_chardev_new("bcm4339-bt");
        }
        pl011_create(CXD90045_UART3_BASE, pic[CXD90045_UART3_SPI], uart3_chr);
    }

    timer = qdev_new("cxd4115-timer");
    sysbus_realize_and_unref(SYS_BUS_DEVICE(timer), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(timer), 0, CXD90045_TIMER_BASE);
    for (i = 0; i < 16; i++) {
        sysbus_connect_irq(SYS_BUS_DEVICE(timer), i,
                           pic[CXD90045_TIMER_IRQ0 + i - 32]);
    }

    if (sony_a6400_watchdog) {
        /*
         * One-channel CXD4115 instance with CTRL bit 31 = reset on match.
         * Expiry runs QEMU's -action watchdog= policy (default reset).  No
         * IRQ is connected: the kernel's wdt_intr is reached through GPIO
         * 0xF2915050, not through this channel [I].
         */
        DeviceState *wdt = qdev_new("cxd4115-timer");

        qdev_prop_set_uint32(wdt, "num-channels", 1);
        qdev_prop_set_bit(wdt, "watchdog", true);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(wdt), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(wdt), 0, CXD90045_WDT_BASE);
    }
    if (sony_a6400_work_dma && !lens_on) {
        cxd90045_create_work_dma(get_system_memory());
    }
    if (sony_a6400_f30a_fill) {
        cxd90045_create_f30a_fill(get_system_memory());
    }
    if (sony_a6400_ddrb_control) {
        cxd90045_create_ddrb(sysmem);
    }
    if (sony_a6400_lowmem_abort || sony_a6400_strict_bus_abort) {
        sony_a6400_create_lowmem_abort(sysmem, cpus, ncpu);
    }
    if (sony_a6400_hostwin && *sony_a6400_hostwin) {
#ifdef _WIN32
        error_report("sony-a6400: hostwin requires a POSIX host");
        exit(1);
#else
        MemoryRegion *win = g_new0(MemoryRegion, 1);
        if (!memory_region_init_ram_from_file(win, NULL, "a6400.hostwin", CXD90045_HOSTWIN_SIZE,
                                              0, RAM_SHARED, sony_a6400_hostwin, 0, &error_fatal)) {
            exit(1);
        }
        memory_region_add_subregion_overlap(sysmem, CXD90045_HOSTWIN_PA, win, 10);
#endif
    }

    /*
     * Catch-all for unmodelled peripherals (E0525): same behaviour as the
     * unassigned space it covers (reads 0, writes dropped, see
     * ignore_memory_transaction_failures), but every access is logged
     * under -d unimp so missing devices are visible instead of silent.
     * A global read-back register file here broke dwc3 (a self-clearing
     * reset bit read back as set: "failed to initialize core", -110), so
     * read-back is applied per block below (E0528).
     */
    create_unimplemented_device("cxd90045.unmodelled", 0xF0000000ULL,
                                0x08000000ULL);
    if (liveview) {
        sony_a6400_create_liveview_pipeline(machine, sysmem, pic);
    } else if (sony_a6400_sgc1_event_a) {
        sony_sgc1_create(machine, pic[35 - 32]);
    }
    if (sony_a6400_gate_force_probe) {
        sony_a6400_gate_force_timer =
            timer_new_ns(QEMU_CLOCK_VIRTUAL, sony_a6400_gate_force_tick, NULL);
        timer_mod(sony_a6400_gate_force_timer,
                  qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + GATE_FORCE_PERIOD_NS);
    }
    if (sony_a6400_entry_force_probe) {
        sony_a6400_entry_force_timer =
            timer_new_ns(QEMU_CLOCK_VIRTUAL, sony_a6400_entry_force_tick, NULL);
        timer_mod(sony_a6400_entry_force_timer,
                  qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                  ENTRY_FORCE_PERIOD_NS);
    }
    if (sony_a6400_usb_vbus_test) {
        sony_usb_vbus_create(machine, pic[230 - 32]);
    }
    if (sony_a6400_dwc3_gadget) {
        DeviceState *dwc3 = qdev_new("cxd90045-dwc3");
        sysbus_realize_and_unref(SYS_BUS_DEVICE(dwc3), &error_fatal);
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(dwc3), 0, 0xF020C000ULL, -500);
        sysbus_connect_irq(SYS_BUS_DEVICE(dwc3), 0, pic[229 - 32]);
    }
    if (sony_a6400_pl330_dma) {
        DeviceState *pl330 = qdev_new("pl330");
        SysBusDevice *pl330_sbd = SYS_BUS_DEVICE(pl330);
        object_property_set_link(OBJECT(pl330), "memory",
                                 OBJECT(sysmem), &error_fatal);
        qdev_prop_set_uint32(pl330, "num_chnls", 4);
        qdev_prop_set_uint8(pl330, "num_periph_req", 0);
        qdev_prop_set_uint8(pl330, "num_events", 4);
        sysbus_realize_and_unref(pl330_sbd, &error_fatal);
        sysbus_mmio_map_overlap(pl330_sbd, 0, 0xF0030000ULL, 1);
        sysbus_connect_irq(pl330_sbd, 0, pic[119 - 32]); /* dm330_abort */
        for (int ev = 0; ev < 4; ev++) {
            sysbus_connect_irq(pl330_sbd, ev + 1, pic[(115 + ev) - 32]); /* dm330_0..3 */
        }
    }

    /*
     * E0566: CamPig IRQ setup reads back +0x50 before setting its next
     * enable bit. Bind only the observed word in this opt-in experiment.
     */
    if (!liveview && sony_a6400_campig_enable_readback) {
        DeviceState *rf = qdev_new("cxd90014-regfile");

        qdev_prop_set_string(rf, "name", "cxd90045.campig-enable");
        qdev_prop_set_uint64(rf, "size", 4);
        qdev_prop_set_uint64(rf, "base", 0xF3000050ULL);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(rf), &error_fatal);
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(rf), 0, 0xF3000050ULL, -500);
    }

    /*
     * AudioIp (E0528): the av-cam audio driver writes and reads back plain
     * config registers in 0xF3420000-0xF3423FFF (a5000 RE_AUDIO.md section
     * 1.1, where a read-back register file let audio initialise fully); the
     * a6400 av-cam also touches 0xF3428000.  Read-back over 32 KiB, logged.
     */
    {
        DeviceState *rf = qdev_new("cxd90014-regfile");
        qdev_prop_set_string(rf, "name", "cxd90045.audioip");
        qdev_prop_set_uint64(rf, "size", 0x8000);
        qdev_prop_set_uint64(rf, "base", 0xF3420000ULL);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(rf), &error_fatal);
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(rf), 0, 0xF3420000ULL, -500);
    }
    if (audio_io_on) {
        cxd90045_audio_create(machine, sysmem, sony_a6400_audio_input,
                              sony_a6400_audio_mic_plugged);
    }

    /*
     * CXD90045 clock generator (docs/architecture/cxd90045-sugilite-cross-gen-
     * parity.md, "Clock-Up & Divider Sequence"): sugilite_clock_up_internal
     * does a read-modify-write on the clock dividers
     * 0xF28040C4/0xF28041A8/0xF28050B8/0xF280518C, `(val & ~0xF) | 6`, so
     * they read back what was written.  av-cam touches only the 4 KiB pages
     * 0xF2800000, 0xF2802000 and 0xF2804000-0xF2807FFF; cover them with one
     * contiguous 32 KiB read-back window.  No `clkgen` property: this block
     * has no lock/self-clear status words, those live at 0xF2A0xxxx.
     */
    {
        DeviceState *rf = qdev_new("cxd90014-regfile");

        qdev_prop_set_string(rf, "name", "cxd90045.clkgen-f28");
        qdev_prop_set_uint64(rf, "size", 0x8000);
        qdev_prop_set_uint64(rf, "base", 0xF2800000ULL);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(rf), &error_fatal);
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(rf), 0, 0xF2800000ULL, -500);
    }

    /*
     * Unidentified configuration block at 0xF3500000.  No documentation
     * names it; the only evidence is the av-cam access log:
     * 439 distinct (read|write, offset) pairs in 477 accesses across the
     * 64 KiB block, almost all one-time writes.  Every read either follows a
     * write to the same word or hits a never-written word (14 offsets), and
     * no word is polled (longest run of consecutive reads is 2).  Plain
     * read-back therefore matches the current all-zero reads except where
     * the guest checks what it wrote.  The block's identity is not known.
     */
    {
        DeviceState *rf = qdev_new("cxd90014-regfile");

        qdev_prop_set_string(rf, "name", "cxd90045.f35-cfg");
        qdev_prop_set_uint64(rf, "size", 0x10000);
        qdev_prop_set_uint64(rf, "base", 0xF3500000ULL);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(rf), &error_fatal);
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(rf), 0, 0xF3500000ULL, -500);
    }

    /*
     * Unidentified configuration block at 0xF3880000.  No documentation
     * names it; the only evidence is the av-cam access log: 94 distinct
     * (read|write, offset) pairs in 236 accesses, one-time configuration
     * followed by write-then-read verification.  No word is polled (longest
     * run of consecutive reads is 2) and every read either follows a write
     * to the same word or hits a never-written word (11 offsets), so plain
     * read-back matches the current all-zero reads except where the guest
     * checks what it wrote.  Four words (offsets 0x1008, 0x1200, 0x1380,
     * 0x2004) are read-modify-written: with read-back the guest keeps the
     * bits it set earlier (e.g. +0x1200 0x10802 | 0x1 = 0x10803), where the
     * all-zero read made it drop them.  The block's identity is not known.
     */
    {
        DeviceState *rf = qdev_new("cxd90014-regfile");

        qdev_prop_set_string(rf, "name", "cxd90045.f388-cfg");
        qdev_prop_set_uint64(rf, "size", 0x10000);
        qdev_prop_set_uint64(rf, "base", 0xF3880000ULL);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(rf), &error_fatal);
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(rf), 0, 0xF3880000ULL, -500);
    }

    /*
     * CamCore block at 0xF3010000 (64 KiB).  The av-cam core maps it 1:1
     * (virtual 0xF30x0000 = physical, E0564).  The baseline access log
     * (loop/runs/L0016/run1/qemu-d.log) has 1012 distinct (kind, offset)
     * pairs, 219810 accesses, but only 28 read offsets; the other 984
     * offsets are write-only.  The 28 reads are: 16 read-modify-write words
     * (0x4b00/04, 0x5a04/14/18/1c/28/2c/30/48/4c, 0x5ac8/cc/d0/d4, 0x5e00)
     * that read back the last write; 0x5020, which the guest writes
     * 0x01030001 and reads back (nibble getters at 0x63cd0a1c/0x63cd0a28);
     * and 11 status words (0x5600/04, 0x5b80/84/8c/90/94/98/9c/a0/a4) that
     * the guest never writes and whose getters only mask (PCs in
     * cxd90045_camcore.c), so they stay 0 as in the baseline.  The identity
     * is CamCore: it is the av-cam block E0564's ydet window reaches.
     */
    {
        DeviceState *cc = qdev_new("cxd90045-camcore");

        qdev_prop_set_string(cc, "name", "cxd90045.camcore-f301");
        qdev_prop_set_uint64(cc, "size", 0x10000);
        qdev_prop_set_uint64(cc, "base", 0xF3010000ULL);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(cc), &error_fatal);
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(cc), 0, 0xF3010000ULL, -500);
    }

    /*
     * CamCore block at 0xF3000000 (64 KiB), the same device with the
     * per-block rule table selected by "base".  The fresh v11 baseline log
     * (loop/runs/L0028/base/qemu-d.log) has 789 distinct (kind, offset)
     * pairs, 57 read offsets and 732 write-only offsets.  23 read offsets
     * are read-back (every baseline read is followed by a write: ldr/bfi/
     * str RMW words); the other 34 return 0 (never-written status words,
     * 0x2008/0x2280/0x2400/0x2480 whose reads are mostly not followed by a
     * write, and the guest-written one-shot words, all 0 on the baseline
     * path).  Mapped at -600, below the E0566 CamPig read-back word at
     * 0xF3000050 (-500), so that opt-in model still wins.
     */
    {
        DeviceState *cc = qdev_new("cxd90045-camcore");

        qdev_prop_set_string(cc, "name", "cxd90045.camcore-f300");
        qdev_prop_set_uint64(cc, "size", 0x10000);
        qdev_prop_set_uint64(cc, "base", 0xF3000000ULL);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(cc), &error_fatal);
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(cc), 0, 0xF3000000ULL, -600);
    }

    /*
     * CamCore block at 0xF3120000 (64 KiB).  The fresh v11 baseline log has
     * 100 distinct (kind, offset) pairs, 13 read offsets and 87 write-only
     * offsets.  5 read offsets are read-back (every baseline read is
     * followed by a write); the other 8 return 0 (never-written status,
     * guest-written one-shot words, and +0x0410, whose read-back=1 enables
     * extra channels and stalls YUV, so it keeps the baseline 0).  Mapped at
     * -600, below the SGC1 words
     * at +0x48/+0x50/+0x210/+0x220/+0x224 (-500), so sony_sgc1_create still
     * owns those.
     */
    {
        DeviceState *cc = qdev_new("cxd90045-camcore");

        qdev_prop_set_string(cc, "name", "cxd90045.camcore-f312");
        qdev_prop_set_uint64(cc, "size", 0x10000);
        qdev_prop_set_uint64(cc, "base", 0xF3120000ULL);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(cc), &error_fatal);
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(cc), 0, 0xF3120000ULL, -600);
    }

    /*
     * L0031: uncovered sub-windows of two more register blocks, modelled by
     * the same generic cxd90045-camcore rule file (base selects the block
     * table, window says where this sub-window starts).  Each window is
     * mapped at -600: above the cxd90045.unmodelled catch-all (-1000) and
     * below every existing device model, which therefore keep winning:
     *   (0xF2400000 is left unmodelled: guest-written status words, see
     *   cxd90045_camcore.c.)
     *   - 0xF3C00000: 0xF3C04000..0xF3C08FFF; the P2S page
     *     0xF3C00000..0xF3C00FFF and the evsel block 0xF3C30000 are not
     *     covered.
     *   - 0xF2910000: 0xF2912000/0x4000/0x5000; the 18-port GPIO model covers
     *     only 0x100*18 = 0x1200 bytes (0xF2910000..0xF29111FF), so these
     *     three pages are outside it, and DFS 0xF2916000 is not covered.
     *   - 0xF3020000: 0xF3020800..0xF3020BFF (L0090); the AIF model at
     *     0xF3020000 covers only its first 0x800 bytes.  L0093 adds the rest
     *     of the page above it (0xF3020C00..0xF3020FFF), and L0096 the 4 KiB
     *     above that (0xF3021000..0xF3021FFF); the 120 s baseline writes
     *     all of it only.
     */
    {
        static const struct {
            hwaddr base;
            hwaddr window;
            uint64_t size;
            const char *name;
        } cc_windows[] = {
            { 0xF3C00000ULL, 0xF3C04000ULL, 0x5000, "cxd90045.camcore-f3c0-4000" },
            { 0xF2910000ULL, 0xF2912000ULL, 0x1000, "cxd90045.camcore-f291-2000" },
            { 0xF2910000ULL, 0xF2914000ULL, 0x1000, "cxd90045.camcore-f291-4000" },
            { 0xF2910000ULL, 0xF2915000ULL, 0x1000, "cxd90045.camcore-f291-5000" },
            { 0xF3020000ULL, 0xF3020800ULL, 0x400,  "cxd90045.camcore-f302-0800" },
            { 0xF3020000ULL, 0xF3020C00ULL, 0x400,  "cxd90045.camcore-f302-rest" },
            { 0xF3020000ULL, 0xF3021000ULL, 0x1000, "cxd90045.camcore-f302-1000" },
        };
        size_t k;

        for (k = 0; k < ARRAY_SIZE(cc_windows); k++) {
            DeviceState *cc = qdev_new("cxd90045-camcore");

            qdev_prop_set_string(cc, "name", cc_windows[k].name);
            qdev_prop_set_uint64(cc, "size", cc_windows[k].size);
            qdev_prop_set_uint64(cc, "base", cc_windows[k].base);
            qdev_prop_set_uint64(cc, "window", cc_windows[k].window);
            sysbus_realize_and_unref(SYS_BUS_DEVICE(cc), &error_fatal);
            sysbus_mmio_map_overlap(SYS_BUS_DEVICE(cc), 0,
                                    cc_windows[k].window, -600);
        }
    }

    /*
     * L0100: the 0xF2920000 block's resolved kernel OTG (0x3000) and av-cam
     * A/E (0x1000 each) windows, from the F-0099 contract.  Each window is a
     * local per-offset rule block mapped at -600, above the
     * cxd90045.unmodelled catch-all (-1000); the 0xF292C000 page was not
     * mapped then (F-0099: no static owner).
     * L0102: F-0101 found that page live (11 pairs / 41 accesses, PCs in
     * module space), so it gets its own 0x1000 rule window below.
     * Unruled offsets inside any of these windows still log as unmodelled
     * (see cxd90045_f292_* above).
     */
    cxd90045_f292_create(sysmem, 0xF2920000ULL, 0x3000,
                         "cxd90045.f292-otg", cxd90045_f292_rules_otg,
                         ARRAY_SIZE(cxd90045_f292_rules_otg));
    cxd90045_f292_create(sysmem, 0xF292A000ULL, 0x1000,
                         "cxd90045.f292-avcam-a",
                         cxd90045_f292_rules_avcam_a,
                         ARRAY_SIZE(cxd90045_f292_rules_avcam_a));
    cxd90045_f292_create(sysmem, 0xF292E000ULL, 0x1000,
                         "cxd90045.f292-avcam-e",
                         cxd90045_f292_rules_avcam_e,
                         ARRAY_SIZE(cxd90045_f292_rules_avcam_e));
    cxd90045_f292_create(sysmem, 0xF292C000ULL, 0x1000,
                         "cxd90045.f292-c", cxd90045_f292_rules_c,
                         ARRAY_SIZE(cxd90045_f292_rules_c));

    /*
     * L0105: RCRAW/IDT hardware compressor page 0xF3030000..0xF303FFFF.
     * The certified 120 s boot logs 547 distinct catch-all pairs here; the
     * table above rules every one of the 534 distinct offsets, and one
     * 64 KiB window at -600 (above cxd90045.unmodelled at -1000) retires
     * them.  The CAMPIG windows at 0xF3000000/0xF3100000 end below this
     * page.  Unruled offsets inside the window still log (see
     * cxd90045_f292_* above), so the window is not a blanket zero.
     */
    cxd90045_f292_create(sysmem, 0xF3030000ULL, 0x10000ULL,
                         "cxd90045.f303", cxd90045_f303_rules,
                         ARRAY_SIZE(cxd90045_f303_rules));

    /*
     * L0108: 0xF3040000 page.  The certified 120 s boot logs 17 distinct
     * catch-all pairs (4 reads + 13 writes, 3 both); the table above rules
     * every one.  One 64 KiB window at -600 sits above the
     * cxd90045.unmodelled catch-all (-1000) and below the live-view AIF
     * group at 0xF3041000 (-300), which therefore keeps its 0x800-byte
     * register block.  Unruled offsets inside the window still log (see
     * cxd90045_f292_* above), so the window is not a blanket zero.
     */
    cxd90045_f292_create(sysmem, 0xF3040000ULL, 0x10000ULL,
                         "cxd90045.f304", cxd90045_f304_rules,
                         ARRAY_SIZE(cxd90045_f304_rules));

    /*
     * L0112: FUSB block at 0xF0220000 (F-0106), priority -600 as in L0108.
     * Rules above; unruled offsets still log as cxd90045.f022.unmodelled.
     */
    cxd90045_f292_create(sysmem, 0xF0220000ULL, 0x20000ULL,
                         "cxd90045.f022", cxd90045_f022_rules,
                         ARRAY_SIZE(cxd90045_f022_rules));

    /*
     * L0116: clkrst3 clock/reset bank at 0xF2900000 (F-0104), priority -600
     * as in L0108.  Rules above; +0x030 has no rule and still logs.
     */
    cxd90045_f292_create(sysmem, 0xF2900000ULL, 0x1000ULL,
                         "cxd90045.f290", cxd90045_f290_rules,
                         ARRAY_SIZE(cxd90045_f290_rules));

    /*
     * L0118: clock/reset-group pages beside f2a_clk, ZERO-only, priority -600.
     * The f2a_clk siblings at 0xF2A01000/0xF2A11000/... are separate pages.
     */
    cxd90045_f292_create(sysmem, 0xF2A00000ULL, 0x1000ULL,
                         "cxd90045.f2a0", cxd90045_f2a0_rules,
                         ARRAY_SIZE(cxd90045_f2a0_rules));
    cxd90045_f292_create(sysmem, 0xF2A10000ULL, 0x1000ULL,
                         "cxd90045.f2a1", cxd90045_f2a1_rules,
                         ARRAY_SIZE(cxd90045_f2a1_rules));
    cxd90045_f292_create(sysmem, 0xF2A20000ULL, 0x1000ULL,
                         "cxd90045.f2a2", cxd90045_f2a2_rules,
                         ARRAY_SIZE(cxd90045_f2a2_rules));
    cxd90045_f292_create(sysmem, 0xF2A32000ULL, 0x1000ULL,
                         "cxd90045.f2a3", cxd90045_f2a3_rules,
                         ARRAY_SIZE(cxd90045_f2a3_rules));

    /*
     * I2C controller channel 0 at 0xF2401000 (4 KiB, E0525: i2c-sony driver
     * /kmod/i2c.ko, channel 0/1 at 0xF2401000/0xF2402000, IRQ 199/200).
     * Only the driver-defined start/config handshake and the message-setup
     * writes are modelled (L0036); the addressed-slave phase is not, so no
     * IRQ is connected and nothing completes.  Mapped at -500, above the
     * cxd90045.unmodelled catch-all (-1000); nothing else maps this page.
     */
    {
        DeviceState *i2c = qdev_new("cxd90045-i2c");

        if (touch_on) {
            qdev_prop_set_uint64(i2c, "base", 0xF2401000ULL);
            qdev_prop_set_uint64(i2c, "size", 0x800);
            qdev_prop_set_bit(i2c, "complete-transfers", true);
        }
        sysbus_realize_and_unref(SYS_BUS_DEVICE(i2c), &error_fatal);
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(i2c), 0, 0xF2401000ULL, -500);
        if (touch_on) {
            sysbus_connect_irq(SYS_BUS_DEVICE(i2c), 0, pic[199 - 32]);

            DeviceState *i2c1 = qdev_new("cxd90045-i2c");
            qdev_prop_set_uint64(i2c1, "base", 0xF2401800ULL);
            qdev_prop_set_uint64(i2c1, "size", 0x800);
            qdev_prop_set_bit(i2c1, "complete-transfers", true);
            sysbus_realize_and_unref(SYS_BUS_DEVICE(i2c1), &error_fatal);
            sysbus_mmio_map_overlap(SYS_BUS_DEVICE(i2c1), 0, 0xF2401800ULL, -500);
            sysbus_connect_irq(SYS_BUS_DEVICE(i2c1), 0, pic[200 - 32]);
        }
        i2c0_dev = i2c;
    }

    /*
     * TPU / jog-dial counter block at 0xF2404000 (4 KiB, E0573: the JogTpu
     * accessor reads the 16-bit counter at 0xF2404014 + (channel << 8)).
     * Three channels at 0x100 stride; driver-defined 16-bit registers at
     * +0x00..+0x34 store and read back, the counter holds the last write,
     * and +0x2c bit 0 is the run bit (L0038, cxd90045_tpu.c).  No IRQ is
     * connected: the driver polls +0x2c bit 0 / +0x10 bit 7 and names no
     * completion interrupt.  The opt-in rear-wheel counter (rear-controls=on)
     * maps its 2 bytes at 0xF2404014 with priority -500, so it overlays this
     * page's counter when enabled; this model is at -600, above the
     * cxd90045.unmodelled catch-all (-1000).  Nothing else maps this page.
     */
    {
        DeviceState *tpu = qdev_new("cxd90045-tpu");

        sysbus_realize_and_unref(SYS_BUS_DEVICE(tpu), &error_fatal);
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(tpu), 0, 0xF2404000ULL, -600);
    }

    /*
     * Imager / ComboPHY window (E0534).  av-cam keeps absolute pointers
     * here (Leo clk 0xFC021000, MIPI D-PHY register blocks, sensor tables
     * under 0xFC0Axxxx).  Reads of unassigned space are 0, so a
     * read-after-write such as set_mptx_fbmode (wants 0x7c back) fails.
     * Plain read-back, same device as the audio block.  The Leo page is
     * mapped on top because its lock bits are status, not a stored write.
     */
    {
        DeviceState *rf = qdev_new("cxd90014-regfile");
        uint32_t *leo = g_new0(uint32_t, 0x400);
        MemoryRegion *leo_mmio = g_new(MemoryRegion, 1);

        qdev_prop_set_string(rf, "name", "cxd90045.imager");
        qdev_prop_set_uint64(rf, "size", 0x200000);
        qdev_prop_set_uint64(rf, "base", 0xFC000000ULL);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(rf), &error_fatal);
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(rf), 0, 0xFC000000ULL, -500);
        memory_region_init_io(leo_mmio, NULL, &cxd90045_leo_ops, leo,
                              "cxd90045.leo_clk", 0x1000);
        memory_region_add_subregion_overlap(sysmem, 0xFC021000ULL,
                                            leo_mmio, 0);
    }

    /* GPIO p0..p17; pin IRQs p0.0-15 -> 121+, p0.16 -> 144, p1.0-6 -> 137+ */
    {
        DeviceState *gpio = qdev_new("cxd90014-gpio");
        bool evf_idle_high = sony_a6400_rear_controls || !body->has_evf;
        g_autoptr(GString) rst = g_string_new("");

        /* P0.2 is the active-low EVF eye contact input; EVF-less bodies
         * (ZV-E10) and rear-controls keep P0.2 high so rear LCD stays active.
         * P1.4 is active-low lens detect: high when mount is empty, low when
         * an E-mount lens is attached.  P5.9 is active-low touch INT (high). */
        if (evf_idle_high) {
            g_string_append(rst, "0.2,");
        }
        g_string_append(rst, lens_on ?
                        "0.1,0.3,0.4,0.7,0.10,0.11,0.13,1.1,1.5" :
                        CXD90045_GPIO_RESET_HIGH);
        if (touch_on) {
            g_string_append(rst, ",0.12,5.9");
        }
        qdev_prop_set_string(gpio, "reset-high", rst->str);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(gpio), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(gpio), 0, CXD90045_GPIO_BASE);
        for (i = 0; i <= 16; i++) {
            sysbus_connect_irq(SYS_BUS_DEVICE(gpio), i,
                               pic[(i < 16 ? 121 + i : 144) - 32]);
        }
        for (i = 0; i <= 6; i++) {
            sysbus_connect_irq(SYS_BUS_DEVICE(gpio), 17 + i,
                               pic[137 + i - 32]);
        }
        sony_a6400_gpio = gpio;
        sony_a6400_sm_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL,
                                           sony_a6400_submicro_tick, NULL);
        timer_mod(sony_a6400_sm_timer, 50 * SCALE_MS);
    }

    /* ADC units 0/1: all channels start at 1023 (released). */
    for (i = 0; i < 2; i++) {
        DeviceState *adc = sysbus_create_simple(
            "cxd90014-adc", CXD90045_ADC_BASE + i * 0x1000,
            pic[CXD90045_ADC_IRQ0 + i - 32]);
        if (i == 1) {
            sony_a6400_rear_adc = adc;
        }
    }
    if (sony_a6400_rear_controls) {
        MemoryRegion *counter = g_new0(MemoryRegion, 1);
        memory_region_init_io(counter, OBJECT(machine), &sony_a6400_wheel_ops,
                              NULL, "a6400.rear-wheel-counter", 2);
        memory_region_add_subregion_overlap(sysmem, 0xF2404014, counter, -500);
        qemu_input_handler_activate(
            qemu_input_handler_register(NULL, &sony_a6400_rear_handler));
        qemu_register_reset(sony_a6400_rear_reset, NULL);
    }

    if (sony_a6400_sm_file) {
        gchar *data = NULL;
        gsize flen = 0;

        if (!g_file_get_contents(sony_a6400_sm_file, &data, &flen, NULL) ||
            flen != sizeof(sony_a6400_sm_frames)) {
            error_report("sony-a6400: submicro-frames %s: need a 384-byte "
                         "file (slot A, C, B)", sony_a6400_sm_file);
            exit(1);
        }
        memcpy(sony_a6400_sm_frames, data, sizeof(sony_a6400_sm_frames));
        g_free(data);
        sony_a6400_sm_loaded = true;
    }

    /*
     * Camera timing-event block (E0527): engine frame events (group A,
     * IRQ 83) and 1 MHz one-shot comparators (group B, IRQ 84).  The engine's
     * 1/8-frame "int_HS" comparator chain sets the sub-micro slot-A permit
     * flag (a5000 RE_ENGINE_TICK.md sections 1-2).
     */
    {
        /*
         * VPH display controller + SYSV frame timer (E0529, a5000 wiring):
         * SYSV 0xF3402000 = master V clock (IRQ 98 every engine frame,
         * V counter +0x98, phase +0x28), panel/line vsync IRQ 101/102 at
         * 60 Hz, VPH 0xF3440000.  SYSV's V output drives the engine frame
         * of the timing-event block (ext-vd), as on the a5000.  av-cam's
         * AudioIp position poll runs from this frame clock.
         */
        DeviceState *vph = qdev_new("cxd90014-vph");
        DeviceState *ev = qdev_new("cxd90014-evsel");
        qdev_set_id(vph, g_strdup("vph"), &error_fatal);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(vph), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(vph), 0, 0xF3440000ULL);
        sysbus_mmio_map(SYS_BUS_DEVICE(vph), 1, 0xF3402000ULL);
        sysbus_connect_irq(SYS_BUS_DEVICE(vph), 0, pic[98 - 32]);
        sysbus_connect_irq(SYS_BUS_DEVICE(vph), 1, pic[101 - 32]);
        sysbus_connect_irq(SYS_BUS_DEVICE(vph), 2, pic[102 - 32]);
        qdev_prop_set_bit(ev, "ext-vd", true);
        if (liveview) {
            qdev_prop_set_uint32(ev, "late-src2", 23);
            qdev_prop_set_uint32(ev, "late-us2", 250);
        }
        sysbus_realize_and_unref(SYS_BUS_DEVICE(ev), &error_fatal);
        qdev_connect_gpio_out(vph, 0, qdev_get_gpio_in(ev, 0));
        sysbus_mmio_map(SYS_BUS_DEVICE(ev), 0, 0xF3C30000ULL);
        sysbus_connect_irq(SYS_BUS_DEVICE(ev), 0, pic[83 - 32]);
        sysbus_connect_irq(SYS_BUS_DEVICE(ev), 1, pic[84 - 32]);
        vph_dev = vph;
    }

    if (touch_on) {
        cxd90045_touch_create(machine, i2c0_dev, sony_a6400_gpio, vph_dev);
    }
    if (hdmi_cec_on) {
        cxd90045_hdmi_cec_create(machine, sysmem, vph_dev);
    }
    if (bcm4339_on) {
        cxd90045_bcm4339_sdio_create(sysmem, CXD90045_MMC1_BASE,
                                     pic[CXD90045_MMC1_SPI], sony_a6400_gpio);
    }
    if (lens_on) {
        cxd90045_emount_lens_create(machine, sysmem, pic[151 - 32],
                                    pic[174 - 32], sony_a6400_gpio, lens_cfg,
                                    sony_a6400_work_dma);
    }

    /* SIO: 5 channels, 0xF2405000 + 0x200 * ch, IRQ 201 + ch (E0526) */
    {
        DeviceState *sio = qdev_new("cxd90014-sio");
        bool imu_ch4 = body->has_ibis_imu || sony_a6400_peripherals;
        sysbus_realize_and_unref(SYS_BUS_DEVICE(sio), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(sio), 0, CXD90045_SIO_BASE);
        for (i = 0; i < 5; i++) {
            CXDSioSlaveXferDir cb = sony_a6400_sio_log;
            if (i == 3) {
                cb = sony_a6400_submicro_xfer;
            } else if (i == 4 && imu_ch4) {
                cb = sony_cxd90045_imu_xfer;
            }
            sysbus_connect_irq(SYS_BUS_DEVICE(sio), i,
                               pic[CXD90045_SIO_IRQ0 + i - 32]);
            cxd90014_sio_attach_dir(sio, i, cb, NULL);
        }
    }

    if (sony_a6400_crcdc_model) {
        cxd90045_create_crcdc_model(sysmem);
    } else {
        MemoryRegion *crcdc_mmio = g_new(MemoryRegion, 1);
        memory_region_init_io(crcdc_mmio, NULL, &cxd90045_crcdc_ops, NULL,
                              "cxd90045.crcdc", 0x1000);
        memory_region_add_subregion(sysmem, CXD90045_CRCDC_BASE, crcdc_mmio);
    }

    if (sony_a6400_pl310) {
        DeviceState *l2x0 = qdev_new("l2x0");
        sysbus_realize_and_unref(SYS_BUS_DEVICE(l2x0), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(l2x0), 0, CXD90045_L2C_STUB_BASE);
    } else {
        MemoryRegion *l2c_mmio = g_new(MemoryRegion, 1);
        memory_region_init_io(l2c_mmio, NULL, &cxd90045_l2c_stub_ops, NULL,
                              "cxd90045.l2c_stub", 0x1000);
        memory_region_add_subregion(sysmem, CXD90045_L2C_STUB_BASE, l2c_mmio);
    }

    {
        DeviceState *gpu = qdev_new("dmp-smaph-s30");
        sysbus_realize_and_unref(SYS_BUS_DEVICE(gpu), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(gpu), 0, CXD90045_GPES_BAR_BASE);
        sysbus_connect_irq(SYS_BUS_DEVICE(gpu), 0, pic[CXD90045_GPES_SPI]);
    }

    /*
     * SA DSP cores (a5000 null-SA model).  av-cam's imager start waits on
     * SA0 source A (IRQ 51) after ZIT ConvZ1C.  Without that completion the
     * mode sequencer never requests the YC output, so VPH 0x144 bit0 stays
     * clear.  The four ID strings are in this av-cam.bin.
     */
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
        for (int n = 0; n < ARRAY_SIZE(sa_cores); n++) {
            DeviceState *sa = qdev_new("cxd90014-sa");
            qdev_prop_set_uint32(sa, "index", n);
            qdev_prop_set_uint64(sa, "base", sa_cores[n].base);
            qdev_prop_set_string(sa, "core-id", sa_cores[n].id);
            sysbus_realize_and_unref(SYS_BUS_DEVICE(sa), &error_fatal);
            sysbus_mmio_map(SYS_BUS_DEVICE(sa), 0, sa_cores[n].base);
            for (int k = 0; k < 3; k++) {
                sysbus_connect_irq(SYS_BUS_DEVICE(sa), k,
                                   pic[sa_cores[n].irq[k] - 32]);
            }
        }
    }

    /*
     * L0137: ID-only window for SA core 4 (see cxd90045_sa4_id above).
     * Mapped at -600, above the cxd90045.unmodelled catch-all (-1000).
     */
    {
        MemoryRegion *sa4_id = g_new0(MemoryRegion, 1);

        memory_region_init_io(sa4_id, NULL, &cxd90045_sa4_id_ops, NULL,
                              "cxd90045.sa4-id", 0x10);
        memory_region_add_subregion_overlap(sysmem, 0xF35D00F0ULL, sa4_id,
                                            -600);
    }

    /*
     * Sensor register writer (a5000 P2S).  av-cam has the same unit-0
     * bases.  A transfer that never clears 0xF3C00908 bit0 is refused
     * forever after the first one (RE_SENSOR_ISP.md).  IRQ 38.
     */
    {
        DeviceState *p2s = qdev_new("cxd90014-p2s");
        sysbus_realize_and_unref(SYS_BUS_DEVICE(p2s), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(p2s), 0, 0xF3C00000ULL);
        sysbus_connect_irq(SYS_BUS_DEVICE(p2s), 0, pic[38 - 32]);
    }

    /*
     * The 64 KiB window also covers SYSV at 0xF3402000.  Priority -1 lets
     * that device answer 0xF3402028.  At priority 0 the stub answered the
     * phase read with 0 (E0535).
     */
    sugi_clk = cxd90045_create_rw_stub(sysmem, "cxd90045.sugi_clk",
                                       CXD90045_SUGI_CLK_BASE, 0x10000, -1);
    /* sugilite_clock_up_internal expects 0x15 at 0xF3400080 */
    sugi_clk->regs[0x80 >> 2] = 0x15;

    if (sony_a6400_dfs_control) {
        cxd90045_create_dfs(sysmem);
    } else {
        cxd90045_create_rw_stub(sysmem, "cxd90045.dfs_ctrl",
                                CXD90045_DFS_CTRL_BASE, 0x1000, 0);
    }

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
            qdev_prop_set_bit(card, "x-sony-legacy-backend", true);
            qdev_prop_set_uint64(card, "boot-partition-size", 4 * MiB);
            qdev_prop_set_uint8(card, "boot-config", 0x08);
            qdev_prop_set_drive_err(card, "drive", blk, &error_fatal);
            qdev_realize_and_unref(card, qdev_get_child_bus(sdhci, "sd-bus"),
                                   &error_fatal);
        }
    }

    /*
     * The captured UDIF mmc0 resource is the removable slot; mmc1 is SDIO
     * and emmc0 above is internal storage. An explicitly named backend
     * enables this experimental slot even when it contains no medium.
     */
    sd_blk = blk_by_name("a6400-sd");
    if (sd_blk) {
        DeviceState *sdhci = qdev_new(TYPE_SYSBUS_SDHCI);
        DeviceState *card = qdev_new(TYPE_SD_CARD);
        SDBus *bus = &SYSBUS_SDHCI(sdhci)->sdbus;

        qdev_prop_set_uint8(sdhci, "sd-spec-version", 3);
        qdev_prop_set_uint64(sdhci, "capareg", 0x016830b0);
        sbd = SYS_BUS_DEVICE(sdhci);
        sysbus_realize_and_unref(sbd, &error_fatal);
        sysbus_mmio_map(sbd, 0, CXD90045_MMC0_BASE);
        sysbus_connect_irq(sbd, 0, pic[CXD90045_MMC0_SPI]);
        qdev_prop_set_drive_err(card, "drive", sd_blk, &error_fatal);
        qdev_realize_and_unref(card, BUS(bus), &error_fatal);
        sony_a6400_sd_bus = bus;

        if (!sony_a6400_miscreg) {
            MemoryRegion *mode_read = g_new(MemoryRegion, 1);

            /* mcmn_drv polls active-low card detect in MISCREG, not SDHCI. */
            memory_region_init_io(mode_read, OBJECT(machine),
                                  &sony_a6400_sd_mode_ops, bus,
                                  "cxd90045.sd-moderead", 4);
            memory_region_add_subregion(sysmem, CXD90045_SD_MODEREAD,
                                        mode_read);
        }
    }
    sd2_blk = blk_by_name("a6400-sd2");
    if (sd2_blk || (body->has_dual_sd && sony_a6400_peripherals)) {
        DeviceState *sdhci2 = qdev_new(TYPE_SYSBUS_SDHCI);
        SDBus *bus2 = &SYSBUS_SDHCI(sdhci2)->sdbus;

        qdev_prop_set_uint8(sdhci2, "sd-spec-version", 3);
        qdev_prop_set_uint64(sdhci2, "capareg", 0x016830b0);
        sbd = SYS_BUS_DEVICE(sdhci2);
        sysbus_realize_and_unref(sbd, &error_fatal);
        sysbus_mmio_map(sbd, 0, CXD90045_MMC2_BASE);
        sysbus_connect_irq(sbd, 0, pic[CXD90045_MMC2_SPI]);
        if (sd2_blk) {
            DeviceState *card2 = qdev_new(TYPE_SD_CARD);
            qdev_prop_set_drive_err(card2, "drive", sd2_blk, &error_fatal);
            qdev_realize_and_unref(card2, BUS(bus2), &error_fatal);
        }
    }
    if (sony_a6400_miscreg) {
        cxd90045_create_miscreg(sysmem);
    }

    sony_a6400_load_boot_contract(machine, cpus, ncpu);
}

static void sony_cxd90045_common_machine_init(MachineClass *mc,
                                              const char *desc)
{
    mc->desc = desc;
    mc->init = sony_a6400_init;
    mc->max_cpus = CXD90045_MAX_CPUS;
    mc->default_cpus = CXD90045_MAX_CPUS;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-a5");
    mc->default_ram_size = CXD90045_RAM0_SIZE;
    mc->ignore_memory_transaction_failures = true;
    object_class_property_add_str(OBJECT_CLASS(mc), "submicro-frames",
                                  sony_a6400_get_sm_file,
                                  sony_a6400_set_sm_file);
    object_class_property_set_description(OBJECT_CLASS(mc), "submicro-frames",
        "384-byte file: captured sub-micro RX slots A, C, B (E0527)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "submicro-lens-power",
                                   sony_a6400_get_sm_lens_power,
                                   sony_a6400_set_sm_lens_power);
    object_class_property_set_description(OBJECT_CLASS(mc), "submicro-lens-power",
        "Model the sub-micro lens power rail status (no lens protocol)");
    object_class_property_add_str(OBJECT_CLASS(mc), "hostwin",
                                  sony_a6400_get_hostwin, sony_a6400_set_hostwin);
    object_class_property_set_description(OBJECT_CLASS(mc), "hostwin",
        "Developer option: host file mapped as shared RAM at PA 0x50000000 (16 MiB) for "
        "fast test-file transfer into a running guest; not a hardware model");
    object_class_property_add_bool(OBJECT_CLASS(mc), "campig-enable-readback",
                                   sony_a6400_get_campig_enable_readback,
                                   sony_a6400_set_campig_enable_readback);
    object_class_property_set_description(OBJECT_CLASS(mc),
        "campig-enable-readback", "Retain the CamPig IRQ enable word (E0566)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "sgc1-event-a",
                                   sony_a6400_get_sgc1_event_a,
                                   sony_a6400_set_sgc1_event_a);
    object_class_property_set_description(OBJECT_CLASS(mc), "sgc1-event-a",
        "Enable experimental Sgc1 input sync event A (E0568)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "gate-force-probe",
                                   sony_a6400_get_gate_force_probe,
                                   sony_a6400_set_gate_force_probe);
    object_class_property_set_description(OBJECT_CLASS(mc),
        "gate-force-probe", "Declared stimulus (cond. PROBE gate-force): patch "
        "the av-cam step-26 producer cbz to a NOP once; not a fix");
    object_class_property_add_bool(OBJECT_CLASS(mc), "entry-force-probe",
                                   sony_a6400_get_entry_force_probe,
                                   sony_a6400_set_entry_force_probe);
    object_class_property_set_description(OBJECT_CLASS(mc),
        "entry-force-probe", "Declared stimulus (cond. PROBE entry-force): "
        "while S is 240, make the first av-cam entry take the r0 = 1 path; "
        "not a fix");
    object_class_property_add_bool(OBJECT_CLASS(mc), "rear-controls",
                                   sony_a6400_get_rear_controls,
                                   sony_a6400_set_rear_controls);
    object_class_property_set_description(OBJECT_CLASS(mc), "rear-controls",
        "Connect host keys to a6400 ADC, GPIO and sub-micro inputs");
    object_class_property_add_bool(OBJECT_CLASS(mc), "shutter-edge-irq",
                                   sony_get_shutter_edge_irq,
                                   sony_set_shutter_edge_irq);
    object_class_property_set_description(OBJECT_CLASS(mc), "shutter-edge-irq",
        "Latch body shutter IRQs 122 and 142 as edge-triggered at boot");
    object_class_property_add_str(OBJECT_CLASS(mc), "liveview-source",
                                  sony_a6400_get_liveview_source,
                                  sony_a6400_set_liveview_source);
    object_class_property_set_description(OBJECT_CLASS(mc), "liveview-source",
        "Enable opt-in CXD90045 CamPig/PQD_Ydet/RC0 live-view pipeline "
        "(bars, ramp, none, or pgm:PATH)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "usb-vbus-test",
                                   sony_get_usb_vbus_test,
                                   sony_set_usb_vbus_test);
    object_class_property_set_description(OBJECT_CLASS(mc), "usb-vbus-test",
        "Enable the experimental FUSB cable status and IRQ 230 (E0580)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "usb-cid-high",
                                   sony_get_usb_cid_high,
                                   sony_set_usb_cid_high);
    object_class_property_set_description(OBJECT_CLASS(mc), "usb-cid-high",
        "Keep FUSB CID bit 1 high (floating / peripheral idle) at 0xF2920010 (E0587)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "dwc3-gadget",
                                   sony_get_dwc3_gadget,
                                   sony_set_dwc3_gadget);
    object_class_property_set_description(OBJECT_CLASS(mc), "dwc3-gadget",
        "Enable the CXD90045 DWC3 gadget controller at 0xF020C000 and IRQ 229 (E0590, default on)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "pl330-dma",
                                   sony_get_pl330_dma,
                                   sony_set_pl330_dma);
    object_class_property_set_description(OBJECT_CLASS(mc), "pl330-dma",
        "Enable the PL330 DMAC controller at 0xF0030000 and IRQs 115..119");
    object_class_property_add_bool(OBJECT_CLASS(mc), "watchdog",
                                   sony_get_watchdog, sony_set_watchdog);
    object_class_property_set_description(OBJECT_CLASS(mc), "watchdog",
        "Model the CXD4115 hardware watchdog at 0xF2402000; expiry follows "
        "-action watchdog= (E0636)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "work-dma",
                                   sony_get_work_dma, sony_set_work_dma);
    object_class_property_set_description(OBJECT_CLASS(mc), "work-dma",
        "Model the work DMA copy channel at 0xF0102000 (+0x1C0, mode "
        "0x1C491000 only; E0641)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "f30a-fill",
                                   sony_get_f30a_fill, sony_set_f30a_fill);
    object_class_property_set_description(OBJECT_CLASS(mc), "f30a-fill",
        "Model the 0xF30A memory-fill engine at +0x1280 (zero fill only; "
        "E0642)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "zve10-menu-boot",
                                   sony_get_zve10_menu_boot,
                                   sony_set_zve10_menu_boot);
    object_class_property_set_description(OBJECT_CLASS(mc), "zve10-menu-boot",
        "Opt in to the adapted ZV-E10 UI kernel layout (single CPU, "
        "kernel 0x138000, initrd 0x10000000, maximum 256 MiB)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "ddrb-control",
                                   sony_get_ddrb_control, sony_set_ddrb_control);
    object_class_property_set_description(OBJECT_CLASS(mc), "ddrb-control",
        "Model the DDR-B controller's initialized idle/active-mode sequence");
    object_class_property_add_bool(OBJECT_CLASS(mc), "dfs-control",
                                   sony_get_dfs_control, sony_set_dfs_control);
    object_class_property_set_description(OBJECT_CLASS(mc), "dfs-control",
        "Complete the observed DFS clock switch register sequence");
    object_class_property_add_bool(OBJECT_CLASS(mc), "lowmem-abort",
                                   sony_get_lowmem_abort, sony_set_lowmem_abort);
    object_class_property_set_description(OBJECT_CLASS(mc), "lowmem-abort",
        "Bus error (external abort) on PA 0x000F8000-0x000F8FFF in the reserved "
        "first megabyte, as observed on the ILCE-6400 (E0647)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "strict-bus-abort",
                                   sony_get_strict_bus_abort,
                                   sony_set_strict_bus_abort);
    object_class_property_set_description(OBJECT_CLASS(mc), "strict-bus-abort",
        "Raise synchronous external aborts (FSR 0x008) on PA 0x000F8000 and all "
        "unmapped physical address holes instead of returning 0 (E0647)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "pl310",
                                   sony_get_pl310, sony_set_pl310);
    object_class_property_set_description(OBJECT_CLASS(mc), "pl310",
        "Instantiate the ARM PL310 L2 cache controller (l2x0) at 0xF8002000 "
        "instead of the +0x77C read stub (E0416, E0655)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "crcdc-model",
                                   sony_get_crcdc_model, sony_set_crcdc_model);
    object_class_property_set_description(OBJECT_CLASS(mc), "crcdc-model",
        "Model the CRCDC clock block at 0xF2A31000 with self-clearing +0x30 "
        "bit 0 trigger (E0423)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "miscreg",
                                   sony_get_miscreg, sony_set_miscreg);
    object_class_property_set_description(OBJECT_CLASS(mc), "miscreg",
        "Model CXD90045 EFUSE_CHIPID (0xF2915000), MODEREAD (0xF2915010), and "
        "ASV (0xF2927000) registers (E0643)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "linux-boot",
                                   sony_get_linux_boot, sony_set_linux_boot);
    object_class_property_set_description(OBJECT_CLASS(mc), "linux-boot",
        "Boot standard ARMv7 Linux kernels / custom OS images (1..4 cores, "
        "ATAGS at 0x103000 or -dtb at 0x0FF00000, initrd at 0x10000000)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "usb-vbus",
                                   sony_get_usb_vbus, sony_set_usb_vbus);
    object_class_property_set_description(OBJECT_CLASS(mc), "usb-vbus",
        "Set virtual cable presence at runtime");
    object_class_property_add_str(OBJECT_CLASS(mc), "camera-model",
                                  sony_get_camera_model, sony_set_camera_model);
    object_class_property_set_description(OBJECT_CLASS(mc), "camera-model",
        "Select modeled CXD90045 camera body profile (ilce_6400, ilce_6400a, "
        "zv_e10, ilce_6100, ilce_6600, ilce_7c, ilce_7m3, ilce_7rm4a)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "peripherals",
                                   sony_get_peripherals, sony_set_peripherals);
    object_class_property_set_description(OBJECT_CLASS(mc), "peripherals",
        "Enable all modeled CXD90045 camera peripherals (touchscreen, bcm4339, "
        "hdmi-cec, audio-io, emount-lens, and 6-axis IMU)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "touchscreen",
                                   sony_get_touchscreen, sony_set_touchscreen);
    object_class_property_set_description(OBJECT_CLASS(mc), "touchscreen",
        "Enable virtual FocalTech 0x20 I2C capacitive touchscreen on i2c0 + "
        "P5.9 INT + VPH LCD pointer handler");
    object_class_property_add_bool(OBJECT_CLASS(mc), "bcm4339",
                                   sony_get_bcm4339, sony_set_bcm4339);
    object_class_property_set_description(OBJECT_CLASS(mc), "bcm4339",
        "Enable Broadcom BCM4339 SDIO Wi-Fi at mmc1 (0xF0301000, IRQ 221) and "
        "Bluetooth 4.1 H4 HCI + HCD firmware controller on uart3 (/dev/ttyBT)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "hdmi-cec",
                                   sony_get_hdmi_cec, sony_set_hdmi_cec);
    object_class_property_set_description(OBJECT_CLASS(mc), "hdmi-cec",
        "Enable CXD90045 HDMI TX (0xF2A40000, DDC EDID + HPD + VPH Console 1) "
        "and CEC (0xF3C10000, virtual TV responder)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "audio-io",
                                   sony_get_audio_io, sony_set_audio_io);
    object_class_property_set_description(OBJECT_CLASS(mc), "audio-io",
        "Enable CXD90045 AudioIp (0xF3420000..0xF3428FFF) capture/playback DMA "
        "ring buffers, mic-jack detect, and synthetic PCM capture");
    object_class_property_add_str(OBJECT_CLASS(mc), "audio-input",
                                  sony_get_audio_input, sony_set_audio_input);
    object_class_property_set_description(OBJECT_CLASS(mc), "audio-input",
        "Synthetic PCM microphone source when audio-io=on (sine440, silence, "
        "or none)");
    object_class_property_add_bool(OBJECT_CLASS(mc), "audio-mic-plugged",
                                   sony_get_audio_mic_plugged,
                                   sony_set_audio_mic_plugged);
    object_class_property_set_description(OBJECT_CLASS(mc), "audio-mic-plugged",
        "Report an external 3.5 mm microphone plugged into AudioIp");
    object_class_property_add_str(OBJECT_CLASS(mc), "emount-lens",
                                  sony_get_emount_lens, sony_set_emount_lens);
    object_class_property_set_description(OBJECT_CLASS(mc), "emount-lens",
        "Attach a physical E-mount lens model with focus/iris/zoom/OSS motors "
        "(selp1650, sigma1850, sel2470gm, fe50f18, or none)");
}

#define DEFINE_SONY_CXD90045_BODY(fn_suffix, mname, mdesc)                  \
    static void sony_##fn_suffix##_machine_init(MachineClass *mc)           \
    {                                                                       \
        sony_cxd90045_common_machine_init(mc, mdesc);                       \
    }                                                                       \
    DEFINE_MACHINE_ARM(mname, sony_##fn_suffix##_machine_init)

DEFINE_SONY_CXD90045_BODY(a6400,  "sony-a6400",
    "Sony ILCE-6400 (BIONZ X / CXD90045)")
DEFINE_SONY_CXD90045_BODY(a6400a, "sony-a6400a",
    "Sony ILCE-6400A (BIONZ X / CXD90045, J3 panel)")
DEFINE_SONY_CXD90045_BODY(zve10,  "sony-zve10",
    "Sony ZV-E10 (BIONZ X / CXD90045, vari-angle, no EVF)")
DEFINE_SONY_CXD90045_BODY(a6100,  "sony-a6100",
    "Sony ILCE-6100 (BIONZ X / CXD90045)")
DEFINE_SONY_CXD90045_BODY(a6600,  "sony-a6600",
    "Sony ILCE-6600 (BIONZ X / CXD90045, IBIS)")
DEFINE_SONY_CXD90045_BODY(a7c,    "sony-a7c",
    "Sony ILCE-7C (BIONZ X / CXD90045, Full-Frame IBIS)")
DEFINE_SONY_CXD90045_BODY(a7m3,   "sony-a7m3",
    "Sony ILCE-7M3 (BIONZ X / CXD90045, Full-Frame IBIS, Dual SD)")
DEFINE_SONY_CXD90045_BODY(a7rm4a, "sony-a7rm4a",
    "Sony ILCE-7RM4A (BIONZ X / CXD90045, Full-Frame IBIS, Dual SD, J3)")
