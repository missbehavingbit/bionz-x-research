/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Broadcom BCM4339 Wi-Fi (SDIO on mmc1 @ 0xF0301000, IRQ 221) and
 * Bluetooth 4.1 H4 HCI + HCD firmware-loader controller (on PL011 uart3 @
 * 0xF2003000, IRQ 120 = /dev/ttyAM1 = /dev/ttyBT).
 *
 * Hardware contracts derived from:
 *   - vmlinux.bin UDIF resource table: mmc1 @ 0xF0301000 (size 0x1000, IRQ 221)
 *     and uart3 @ 0xF2003000 (size 0x1000, IRQ 120 -> ttyAM1 because uart1/2
 *     are not registered on Linux AMBA bus).
 *   - initrd.img: /dev/ttyBT -> ttyAM1; startBTCore.sh launches
 *     /usr/bin/bsa_server -d /dev/ttyBT -p /usr/bin/bt_firm.hcd.
 *   - BK4 preset 0x013E000C = 0x01 (SDIO Wi-Fi on slot 1 = mmc1).
 *   - mmc_drv.ko (mmc_io_detect CMD5 R4, CMD3, CMD7, CMD52, CMD53),
 *     mmcioWrapper.ko, wlanIrq.ko, and bcmdhd.ko (dhdsdio_probe +0x18184:
 *     Broadcom vendor 0x02D0, device 0x4339, Silicon Backplane ChipCommon
 *     0x18000000 = 0x10044339 SOCI_AI rev 4, AI EROM @ 0x1810A000, TCM RAM
 *     upload, and Fn2 SDPCM/BCDC control channel).
 */

#include "qemu/osdep.h"
#include "qemu/cutils.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "hw/core/sysbus.h"
#include "hw/core/irq.h"
#include "hw/misc/cxd90045_peripherals.h"
#include "chardev/char.h"
#include "system/address-spaces.h"
#include "qemu/fifo8.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"
#include "qom/object.h"

/* =========================================================================
 * Part 1: BCM4339 SDIO Wi-Fi + SDHCI v3.0 Host at mmc1 (0xF0301000, IRQ 221)
 * ========================================================================= */

#define TYPE_CXD90045_BCM4339_SDIO "cxd90045-bcm4339-sdio"
OBJECT_DECLARE_SIMPLE_TYPE(Bcm4339SdioState, CXD90045_BCM4339_SDIO)

#define BCM4339_MMIO_SIZE     0x1000
#define BCM4339_TCM_SIZE      (768 * 1024) /* 768 KiB CR4 TCM for fw_bcmdhd */
#define BCM4339_F2_QUEUE_MAX  4096

/* Standard SDHCI register offsets */
enum {
    SDHCI_SDMA_ADDR    = 0x00,
    SDHCI_BLK_SIZE     = 0x04,
    SDHCI_BLK_COUNT    = 0x06,
    SDHCI_ARG          = 0x08,
    SDHCI_XFER_MODE    = 0x0C,
    SDHCI_CMD          = 0x0E,
    SDHCI_RSP0         = 0x10,
    SDHCI_RSP1         = 0x14,
    SDHCI_RSP2         = 0x18,
    SDHCI_RSP3         = 0x1C,
    SDHCI_BUF_DATA     = 0x20,
    SDHCI_PRNSTS       = 0x24,
    SDHCI_HOSTCTL      = 0x28,
    SDHCI_PWRCTL       = 0x29,
    SDHCI_CLKCTL       = 0x2C,
    SDHCI_TIMEOUT      = 0x2E,
    SDHCI_SWRST        = 0x2F,
    SDHCI_NORINTSTS    = 0x30,
    SDHCI_ERRINTSTS    = 0x32,
    SDHCI_NORINTSTSEN  = 0x34,
    SDHCI_ERRINTSTSEN  = 0x36,
    SDHCI_NORINTSIGEN  = 0x38,
    SDHCI_ERRINTSIGEN  = 0x3A,
    SDHCI_ACMD12ERRSTS = 0x3C,
    SDHCI_HOSTCTL2     = 0x3E,
    SDHCI_CAPAB        = 0x40,
    SDHCI_CAPAB_HI     = 0x44,
    SDHCI_MAXCURR      = 0x48,
    SDHCI_ADMA_ERR     = 0x54,
    SDHCI_ADMA_ADDR    = 0x58,
    SDHCI_SLOT_INT     = 0xFC,
    SDHCI_HCVER        = 0xFE,
};

#define SDHCI_INT_CMD_DONE    0x0001u
#define SDHCI_INT_XFER_DONE   0x0002u
#define SDHCI_INT_DMA_END     0x0008u
#define SDHCI_INT_BUF_WR_RDY  0x0010u
#define SDHCI_INT_BUF_RD_RDY  0x0020u
#define SDHCI_INT_CARD_INT    0x0100u
#define SDHCI_INT_ERROR_SUM   0x8000u

/* Broadcom Silicon Backplane & SDIO constants */
#define BCM_VENDOR_BROADCOM   0x02D0u
#define BCM_CHIP_4339         0x4339u
#define BCM_CHIPCOMMON_ID     0x10044339u /* SOCI_AI (1<<28) | rev 4 | 0x4339 */
#define BCM_EROM_BASE         0x1810A000u

struct Bcm4339SdioState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    qemu_irq irq;
    DeviceState *gpio;

    /* SDHCI host state */
    uint32_t sdma_addr;
    uint16_t blk_size;
    uint16_t blk_count;
    uint32_t arg;
    uint16_t xfer_mode;
    uint16_t cmd_reg;
    uint32_t rsp[4];
    uint8_t hostctl;
    uint8_t pwrctl;
    uint16_t clkctl;
    uint8_t timeout_ctl;
    uint16_t norintsts;
    uint16_t errintsts;
    uint16_t norintstsen;
    uint16_t errintstsen;
    uint16_t norintsigen;
    uint16_t errintsigen;
    uint16_t hostctl2;
    uint32_t adma_addr;

    /* PIO data staging buffer */
    uint8_t pio_buf[65536];
    uint32_t pio_len;
    uint32_t pio_pos;
    bool pio_is_write;
    uint8_t pending_fn;
    uint32_t pending_addr;
    bool pending_inc;

    /* BCM4339 SDIO Function 0/1/2 state */
    uint16_t rca;
    bool selected;
    uint8_t f0_regs[0x300];
    uint8_t cis_rom[0x100];
    uint16_t fn_blksize[3];

    /* Function 1 SBSDIO window & registers */
    uint8_t sbaddr_low;
    uint8_t sbaddr_mid;
    uint8_t sbaddr_high;
    uint8_t chipclkcsr;
    uint8_t sdiopullup;
    uint8_t devctl;

    /* Silicon Backplane cores + 768 KiB TCM RAM */
    uint8_t *tcm;
    uint32_t sdio_intstatus;
    uint32_t sdio_hostintmask;
    uint32_t sdio_tohostmbox;
    uint32_t sdio_tosbmbox;
    uint32_t sdio_tosbmboxdata;
    uint32_t sdio_tohostmboxdata;
    uint32_t ai_ioctrl[4];
    uint32_t ai_resetctrl[4];
    uint32_t pmu_ctl;

    /* Function 2 SDPCM / BCDC frame queue */
    uint8_t f2_rx[BCM4339_F2_QUEUE_MAX];
    uint32_t f2_rx_len;
    uint8_t sdpcm_tx_max_seq;
    uint8_t sdpcm_rx_seq;

    /* Telemetry */
    uint64_t cmd5_count;
    uint64_t cmd52_count;
    uint64_t cmd53_count;
    uint64_t fw_bytes_written;
    uint64_t bcdc_ioctls;
};

/* SOCI_AI EROM table describing ChipCommon, SDIO, ARM CR4, D11, and SOCRAM */
static const uint32_t bcm4339_erom[] = {
    /* 0: ChipCommon (0x800) @ 0x18000000, wrapper @ 0x18100000 */
    (0x4bf << 20) | (0x800 << 8) | 0x01,
    (0x01 << 8) | (0x01 << 4) | 0x03,
    0x18000000u | 0x05u,              /* slave port 0: 4K @ 0x18000000 */
    0x18100000u | (1u << 6) | 0x05u,  /* master wrapper: 4K @ 0x18100000 */

    /* 1: SDIO Device (0x829) @ 0x18001000, wrapper @ 0x18101000 */
    (0x4bf << 20) | (0x829 << 8) | 0x01,
    (0x01 << 8) | (0x01 << 4) | 0x03,
    0x18001000u | 0x05u,
    0x18101000u | (1u << 6) | 0x05u,

    /* 2: ARM CR4 (0x83E) @ 0x18002000 + TCM @ 0x00000000, wrapper 0x18102000 */
    (0x4bf << 20) | (0x83e << 8) | 0x01,
    (0x01 << 8) | (0x02 << 4) | 0x03,
    0x18002000u | 0x05u,
    0x00000000u | (2u << 4) | 0x0du,  /* region size encoded in next word */
    BCM4339_TCM_SIZE,
    0x18102000u | (1u << 6) | 0x05u,

    /* 3: 802.11ac D11 MAC (0x812) @ 0x18003000, wrapper @ 0x18103000 */
    (0x4bf << 20) | (0x812 << 8) | 0x01,
    (0x01 << 8) | (0x01 << 4) | 0x03,
    0x18003000u | 0x05u,
    0x18103000u | (1u << 6) | 0x05u,

    /* End of EROM */
    0x0000000fu,
};

static void bcm4339_sdio_update_irq(Bcm4339SdioState *s)
{
    bool card_int = false;

    if ((s->sdio_intstatus & s->sdio_hostintmask) != 0) {
        s->f0_regs[0x05] |= 0x02; /* F1 interrupt pending */
    } else {
        s->f0_regs[0x05] &= ~0x02;
    }
    if (s->f2_rx_len > 0) {
        s->f0_regs[0x05] |= 0x04; /* F2 packet ready */
    } else {
        s->f0_regs[0x05] &= ~0x04;
    }

    if ((s->f0_regs[0x04] & 0x01) &&
        ((s->f0_regs[0x04] & s->f0_regs[0x05]) & 0x06)) {
        card_int = true;
    }
    if (card_int && (s->norintstsen & SDHCI_INT_CARD_INT)) {
        s->norintsts |= SDHCI_INT_CARD_INT;
    } else {
        s->norintsts &= ~SDHCI_INT_CARD_INT;
    }

    if (s->errintsts) {
        s->norintsts |= SDHCI_INT_ERROR_SUM;
    } else {
        s->norintsts &= ~SDHCI_INT_ERROR_SUM;
    }

    bool line = ((s->norintsts & s->norintsigen) != 0) ||
                ((s->errintsts & s->errintsigen) != 0);
    qemu_set_irq(s->irq, line ? 1 : 0);
}

static uint32_t bcm4339_sb_base(const Bcm4339SdioState *s)
{
    return ((uint32_t)s->sbaddr_high << 24) |
           ((uint32_t)s->sbaddr_mid << 16) |
           ((uint32_t)(s->sbaddr_low & 0x80) << 8);
}

static uint32_t bcm4339_sb_read32(Bcm4339SdioState *s, uint32_t sb_pa)
{
    /* TCM RAM window: 0x00000000 .. 0x000BFFFF */
    if (sb_pa + 4 <= BCM4339_TCM_SIZE) {
        return ldl_le_p(&s->tcm[sb_pa]);
    }
    /* EROM table @ 0x1810A000 */
    if (sb_pa >= BCM_EROM_BASE &&
        sb_pa < BCM_EROM_BASE + sizeof(bcm4339_erom)) {
        return bcm4339_erom[(sb_pa - BCM_EROM_BASE) >> 2];
    }
    /* ChipCommon @ 0x18000000 */
    if ((sb_pa & 0xFFFFF000u) == 0x18000000u) {
        switch (sb_pa & 0xFFFu) {
        case 0x000: return BCM_CHIPCOMMON_ID;
        case 0x004: return 0x40040000u; /* CC_CAP_PMU */
        case 0x0FC: return BCM_EROM_BASE;
        case 0x1E0: return 0x00030000u; /* clk_ctl_st: HT + ALP avail */
        case 0x600: return s->pmu_ctl;
        case 0x604: return 0x00000012u; /* PMU capabilities rev 18 */
        case 0x608: return 0x00000003u; /* PMU status: ALP + HT ready */
        default:    return 0;
        }
    }
    /* SDIO Device core @ 0x18001000 */
    if ((sb_pa & 0xFFFFF000u) == 0x18001000u) {
        switch (sb_pa & 0xFFFu) {
        case 0x000: return 0x00010000u; /* corecontrol */
        case 0x020: return s->sdio_intstatus;
        case 0x024: return s->sdio_hostintmask;
        case 0x028: return s->sdio_tohostmbox;
        case 0x040: return s->sdio_tosbmbox;
        case 0x044: return s->sdio_tohostmboxdata;
        case 0x048: return s->sdio_tosbmboxdata;
        default:    return 0;
        }
    }
    /* ARM CR4 core @ 0x18002000 */
    if ((sb_pa & 0xFFFFF000u) == 0x18002000u) {
        switch (sb_pa & 0xFFFu) {
        case 0x004: return 0x00000201u; /* corecapabilities: 2 TCM banks */
        case 0x040: return 0x0000000Bu; /* bankidx=0 -> 512 KiB / bank 1 256K */
        case 0x044: return 0x00000007u; /* bankinfo: 64 * 8 KiB = 512 KiB */
        case 0x1E0: return 0x00030000u;
        default:    return 0;
        }
    }
    /* AI DMP wrappers @ 0x18100000 .. 0x18103FFF */
    if (sb_pa >= 0x18100000u && sb_pa < 0x18104000u) {
        unsigned idx = (sb_pa - 0x18100000u) >> 12;
        switch (sb_pa & 0xFFFu) {
        case 0x408: return s->ai_ioctrl[idx];
        case 0x500: return 0;
        case 0x800: return s->ai_resetctrl[idx];
        default:    return 0;
        }
    }
    return 0;
}

static void bcm4339_sb_write32(Bcm4339SdioState *s, uint32_t sb_pa,
                               uint32_t val)
{
    if (sb_pa + 4 <= BCM4339_TCM_SIZE) {
        stl_le_p(&s->tcm[sb_pa], val);
        s->fw_bytes_written += 4;
        return;
    }
    if ((sb_pa & 0xFFFFF000u) == 0x18000000u) {
        if ((sb_pa & 0xFFFu) == 0x600) {
            s->pmu_ctl = val;
        }
        return;
    }
    if ((sb_pa & 0xFFFFF000u) == 0x18001000u) {
        switch (sb_pa & 0xFFFu) {
        case 0x020: /* intstatus is write-1-to-clear */
            s->sdio_intstatus &= ~val;
            break;
        case 0x024:
            s->sdio_hostintmask = val;
            break;
        case 0x040: /* tosbmailbox: ACK host handshake immediately */
            s->sdio_tosbmbox = val;
            if (val & 0x0Fu) {
                s->sdio_tohostmboxdata = 0x80000002u; /* FWHALT / ACK */
                s->sdio_intstatus |= 0x00000010u;     /* I_HMB_SW_MASK */
            }
            break;
        case 0x048:
            s->sdio_tosbmboxdata = val;
            break;
        default:
            break;
        }
        return;
    }
    if (sb_pa >= 0x18100000u && sb_pa < 0x18104000u) {
        unsigned idx = (sb_pa - 0x18100000u) >> 12;
        switch (sb_pa & 0xFFFu) {
        case 0x408: s->ai_ioctrl[idx] = val; break;
        case 0x800: s->ai_resetctrl[idx] = val & 1u; break;
        default: break;
        }
    }
}

static uint8_t bcm4339_fn_read_byte(Bcm4339SdioState *s, uint8_t fn,
                                    uint32_t addr)
{
    if (fn == 0) {
        if (addr >= 0x1000 && addr < 0x1000 + sizeof(s->cis_rom)) {
            return s->cis_rom[addr - 0x1000];
        }
        if (addr < sizeof(s->f0_regs)) {
            if (addr == 0x03) {
                /* IORx: enabled functions are immediately ready */
                return s->f0_regs[0x02] & 0x06;
            }
            return s->f0_regs[addr];
        }
        return 0;
    }
    if (fn == 1) {
        if (addr >= 0x10000) {
            switch (addr) {
            case 0x1000A: return s->sbaddr_low;
            case 0x1000B: return s->sbaddr_mid;
            case 0x1000C: return s->sbaddr_high;
            case 0x1000E:
                /* Report ALP_AVAIL (0x40) | HT_AVAIL (0x80) alongside req */
                return (s->chipclkcsr & 0x3F) | 0xC0;
            case 0x1000F: return s->sdiopullup;
            case 0x1001F: return s->devctl;
            default:      return 0;
            }
        }
        uint32_t sb_pa = bcm4339_sb_base(s) + (addr & 0x7FFFu);
        uint32_t w = bcm4339_sb_read32(s, sb_pa & ~3u);
        return (w >> ((sb_pa & 3u) * 8)) & 0xFFu;
    }
    if (fn == 2) {
        if (addr < s->f2_rx_len) {
            return s->f2_rx[addr];
        }
        return 0;
    }
    return 0;
}

static void bcm4339_fn_write_byte(Bcm4339SdioState *s, uint8_t fn,
                                  uint32_t addr, uint8_t val)
{
    if (fn == 0) {
        if (addr < sizeof(s->f0_regs)) {
            s->f0_regs[addr] = val;
            if (addr == 0x06 && (val & 0x08)) {
                /* ASx bit 3 = SDIO soft reset */
                s->f0_regs[0x02] = 0;
                s->f0_regs[0x06] = 0;
            } else if (addr == 0x10 || addr == 0x11) {
                s->fn_blksize[0] = s->f0_regs[0x10] |
                                   ((uint16_t)s->f0_regs[0x11] << 8);
            } else if (addr == 0x110 || addr == 0x111) {
                s->fn_blksize[1] = s->f0_regs[0x110] |
                                   ((uint16_t)s->f0_regs[0x111] << 8);
            } else if (addr == 0x210 || addr == 0x211) {
                s->fn_blksize[2] = s->f0_regs[0x210] |
                                   ((uint16_t)s->f0_regs[0x211] << 8);
            }
        }
        return;
    }
    if (fn == 1) {
        if (addr >= 0x10000) {
            switch (addr) {
            case 0x1000A: s->sbaddr_low = val & 0x80; break;
            case 0x1000B: s->sbaddr_mid = val; break;
            case 0x1000C: s->sbaddr_high = val; break;
            case 0x1000E: s->chipclkcsr = val; break;
            case 0x1000F: s->sdiopullup = val; break;
            case 0x1001F: s->devctl = val; break;
            default: break;
            }
            return;
        }
        uint32_t sb_pa = bcm4339_sb_base(s) + (addr & 0x7FFFu);
        if (sb_pa < BCM4339_TCM_SIZE) {
            s->tcm[sb_pa] = val;
            s->fw_bytes_written++;
        } else {
            uint32_t aligned = sb_pa & ~3u;
            unsigned sh = (sb_pa & 3u) * 8;
            uint32_t cur = bcm4339_sb_read32(s, aligned);
            cur = (cur & ~(0xFFu << sh)) | ((uint32_t)val << sh);
            bcm4339_sb_write32(s, aligned, cur);
        }
    }
}

/* Handle a Function 2 SDPCM + BCDC control packet from bcmdhd.ko */
static void bcm4339_handle_f2_write(Bcm4339SdioState *s, const uint8_t *buf,
                                    uint32_t len)
{
    static const uint8_t default_mac[6] = { 0x02, 0x43, 0x39, 0x64, 0x00, 0x01 };
    uint16_t frm_len, frm_chk;
    uint8_t chan, hdr_len;

    if (len < 12 + 16) {
        return;
    }
    frm_len = lduw_le_p(buf);
    frm_chk = lduw_le_p(buf + 2);
    if ((uint16_t)(frm_len ^ frm_chk) != 0xFFFFu || frm_len > len) {
        return;
    }
    chan = buf[5] & 0x0F;
    hdr_len = buf[7];
    if (chan != 0 || hdr_len < 12 || (uint32_t)hdr_len + 16 > frm_len) {
        return;
    }

    const uint8_t *cdc = buf + hdr_len;
    uint32_t cdc_cmd = ldl_le_p(cdc);
    uint32_t cdc_len = ldl_le_p(cdc + 4);
    uint32_t cdc_flags = ldl_le_p(cdc + 8);
    const uint8_t *payload = cdc + 16;
    uint32_t pay_in_len = frm_len - hdr_len - 16;

    uint8_t resp_pay[128];
    uint32_t resp_pay_len = MIN(cdc_len, (uint32_t)sizeof(resp_pay));
    memset(resp_pay, 0, sizeof(resp_pay));

    if (cdc_cmd == 1) { /* WLC_GET_VERSION */
        stl_le_p(resp_pay, 12);
        resp_pay_len = MAX(resp_pay_len, 4u);
    } else if (cdc_cmd == 262 && pay_in_len > 0) { /* WLC_GET_VAR */
        if (memcmp(payload, "cur_etheraddr",
                   MIN(pay_in_len, (uint32_t)sizeof("cur_etheraddr"))) == 0) {
            memcpy(resp_pay, default_mac, 6);
            resp_pay_len = 6;
        } else if (memcmp(payload, "ver", MIN(pay_in_len, 4u)) == 0) {
            static const char ver[] = "BCM4339 QEMU SDIO 6.37.32\n";
            memcpy(resp_pay, ver, sizeof(ver));
            resp_pay_len = sizeof(ver);
        }
    }

    uint16_t total = (uint16_t)(12 + 16 + resp_pay_len);
    if (total <= sizeof(s->f2_rx)) {
        memset(s->f2_rx, 0, total);
        stw_le_p(&s->f2_rx[0], total);
        stw_le_p(&s->f2_rx[2], (uint16_t)~total);
        s->f2_rx[4] = s->sdpcm_rx_seq++;
        s->f2_rx[5] = 0;  /* control channel */
        s->f2_rx[6] = 0;
        s->f2_rx[7] = 12; /* data offset */
        s->f2_rx[8] = 0;
        s->f2_rx[9] = s->sdpcm_tx_max_seq;

        stl_le_p(&s->f2_rx[12], cdc_cmd);
        stl_le_p(&s->f2_rx[16], resp_pay_len);
        /* Preserve request ID in upper 16 bits of flags */
        stl_le_p(&s->f2_rx[20], cdc_flags & 0xFFFF0000u);
        stl_le_p(&s->f2_rx[24], 0); /* status = 0 OK */
        memcpy(&s->f2_rx[28], resp_pay, resp_pay_len);

        s->f2_rx_len = total;
        s->sdio_intstatus |= 0x00000040u; /* I_HMB_FRAME_IND */
        s->bcdc_ioctls++;
    }
}

static void bcm4339_exec_cmd53_buf(Bcm4339SdioState *s, bool is_write,
                                   uint8_t fn, uint32_t addr, bool inc_addr,
                                   uint8_t *buf, uint32_t total)
{
    if (is_write) {
        if (fn == 2) {
            bcm4339_handle_f2_write(s, buf, total);
            return;
        }
        if (fn == 1 && addr < 0x10000 && inc_addr && (total & 3) == 0) {
            uint32_t sb_pa = bcm4339_sb_base(s) + (addr & 0x7FFFu);
            for (uint32_t off = 0; off < total; off += 4) {
                bcm4339_sb_write32(s, sb_pa + off, ldl_le_p(buf + off));
            }
            return;
        }
        for (uint32_t i = 0; i < total; i++) {
            bcm4339_fn_write_byte(s, fn, inc_addr ? addr + i : addr, buf[i]);
        }
    } else {
        if (fn == 2) {
            memset(buf, 0, total);
            if (s->f2_rx_len > 0) {
                uint32_t copy = MIN(total, s->f2_rx_len);
                memcpy(buf, s->f2_rx, copy);
                s->f2_rx_len = 0;
                s->sdio_intstatus &= ~0x00000040u;
            }
            return;
        }
        if (fn == 1 && addr < 0x10000 && inc_addr && (total & 3) == 0) {
            uint32_t sb_pa = bcm4339_sb_base(s) + (addr & 0x7FFFu);
            for (uint32_t off = 0; off < total; off += 4) {
                stl_le_p(buf + off, bcm4339_sb_read32(s, sb_pa + off));
            }
            return;
        }
        for (uint32_t i = 0; i < total; i++) {
            buf[i] = bcm4339_fn_read_byte(s, fn, inc_addr ? addr + i : addr);
        }
    }
}

static void bcm4339_dma_rw(Bcm4339SdioState *s, uint8_t *buf, uint32_t total,
                           bool is_write)
{
    bool use_adma2 = ((s->hostctl >> 3) & 0x3) == 2;

    if (use_adma2 && s->adma_addr != 0) {
        uint32_t desc_pa = s->adma_addr;
        uint32_t done = 0;

        for (unsigned iter = 0; iter < 256 && done < total; iter++) {
            uint8_t desc[8];
            address_space_read(&address_space_memory, desc_pa,
                               MEMTXATTRS_UNSPECIFIED, desc, sizeof(desc));
            uint16_t attr = lduw_le_p(desc);
            uint16_t dlen = lduw_le_p(desc + 2);
            uint32_t daddr = ldl_le_p(desc + 4);
            uint32_t chunk = dlen == 0 ? 65536u : dlen;

            if (!(attr & 0x01)) { /* Valid bit clear */
                break;
            }
            uint8_t act = (attr >> 4) & 0x3;
            if (act == 3) { /* Link */
                desc_pa = daddr;
                continue;
            }
            if (act == 2) { /* Tran */
                uint32_t n = MIN(chunk, total - done);
                if (is_write) {
                    address_space_read(&address_space_memory, daddr,
                                       MEMTXATTRS_UNSPECIFIED, buf + done, n);
                } else {
                    address_space_write(&address_space_memory, daddr,
                                        MEMTXATTRS_UNSPECIFIED, buf + done, n);
                }
                done += n;
            }
            desc_pa += 8;
            if (attr & 0x02) { /* End */
                break;
            }
        }
        s->adma_addr = desc_pa;
    } else {
        if (is_write) {
            address_space_read(&address_space_memory, s->sdma_addr,
                               MEMTXATTRS_UNSPECIFIED, buf, total);
        } else {
            address_space_write(&address_space_memory, s->sdma_addr,
                                MEMTXATTRS_UNSPECIFIED, buf, total);
        }
        s->sdma_addr += total;
    }
}

static void bcm4339_issue_cmd(Bcm4339SdioState *s, uint16_t cmd_val)
{
    uint8_t cmd_idx = (cmd_val >> 8) & 0x3F;
    bool has_data = (cmd_val & 0x0020) != 0;

    s->cmd_reg = cmd_val;
    s->errintsts = 0;

    switch (cmd_idx) {
    case 0: /* GO_IDLE_STATE */
        s->selected = false;
        s->rsp[0] = 0;
        s->norintsts |= SDHCI_INT_CMD_DONE;
        break;

    case 5: /* IO_SEND_OP_COND (SDIO R4) */
        s->cmd5_count++;
        /* Ready (bit31) | NF=2 (0x20000000) | S18A (bit24) | 2.7..3.6V OCR */
        s->rsp[0] = 0xA1FFFF00u;
        s->norintsts |= SDHCI_INT_CMD_DONE;
        break;

    case 3: /* SEND_RELATIVE_ADDR (SDIO R6) */
        s->rca = 0x0001;
        s->rsp[0] = ((uint32_t)s->rca << 16);
        s->norintsts |= SDHCI_INT_CMD_DONE;
        break;

    case 7: /* SELECT/DESELECT_CARD (R1b) */
        s->selected = ((s->arg >> 16) == s->rca);
        s->rsp[0] = 0x00001E00u;
        s->norintsts |= SDHCI_INT_CMD_DONE | SDHCI_INT_XFER_DONE;
        break;

    case 11: /* VOLTAGE_SWITCH (1.8V UHS-I) */
    case 19: /* SEND_TUNING_BLOCK */
        s->rsp[0] = 0x00000900u;
        s->norintsts |= SDHCI_INT_CMD_DONE |
                        (has_data ? SDHCI_INT_XFER_DONE : 0);
        break;

    case 52: { /* IO_RW_DIRECT (R5) */
        bool wr = ((s->arg >> 31) & 1) != 0;
        uint8_t fn = (s->arg >> 28) & 0x7;
        bool raw = ((s->arg >> 27) & 1) != 0;
        uint32_t reg_addr = (s->arg >> 9) & 0x1FFFFu;
        uint8_t wr_val = s->arg & 0xFFu;
        uint8_t rd_val;

        s->cmd52_count++;
        if (wr) {
            bcm4339_fn_write_byte(s, fn, reg_addr, wr_val);
            rd_val = raw ? bcm4339_fn_read_byte(s, fn, reg_addr) : wr_val;
        } else {
            rd_val = bcm4339_fn_read_byte(s, fn, reg_addr);
        }
        /* R5 response: [15:8] = IO_CURRENT_STATE (0x10 = TRN), [7:0] = data */
        s->rsp[0] = 0x00001000u | rd_val;
        s->norintsts |= SDHCI_INT_CMD_DONE;
        break;
    }

    case 53: { /* IO_RW_EXTENDED (R5 + data) */
        bool is_write = ((s->arg >> 31) & 1) != 0;
        uint8_t fn = (s->arg >> 28) & 0x7;
        bool blk_mode = ((s->arg >> 27) & 1) != 0;
        bool inc_addr = ((s->arg >> 26) & 1) != 0;
        uint32_t reg_addr = (s->arg >> 9) & 0x1FFFFu;
        uint32_t count = s->arg & 0x1FFu;
        uint32_t unit = blk_mode ?
            (s->fn_blksize[fn < 3 ? fn : 0] ? s->fn_blksize[fn < 3 ? fn : 0] :
             (s->blk_size & 0x0FFFu)) : 1u;
        uint32_t n_units = (count == 0) ? 512u : count;
        uint32_t total = MIN(n_units * (unit ? unit : 64u),
                             (uint32_t)sizeof(s->pio_buf));

        s->cmd53_count++;
        s->rsp[0] = 0x00001000u; /* R5 TRN state, no error */
        s->norintsts |= SDHCI_INT_CMD_DONE;

        if ((s->xfer_mode & 0x01) != 0) {
            /* DMA mode (SDMA or ADMA2) */
            if (is_write) {
                bcm4339_dma_rw(s, s->pio_buf, total, true);
                bcm4339_exec_cmd53_buf(s, true, fn, reg_addr, inc_addr,
                                       s->pio_buf, total);
            } else {
                bcm4339_exec_cmd53_buf(s, false, fn, reg_addr, inc_addr,
                                       s->pio_buf, total);
                bcm4339_dma_rw(s, s->pio_buf, total, false);
            }
            s->blk_count = 0;
            s->norintsts |= SDHCI_INT_DMA_END | SDHCI_INT_XFER_DONE;
        } else {
            /* PIO mode via SDHCI_BUF_DATA (0x20) */
            s->pio_len = total;
            s->pio_pos = 0;
            s->pio_is_write = is_write;
            s->pending_fn = fn;
            s->pending_addr = reg_addr;
            s->pending_inc = inc_addr;
            if (is_write) {
                s->norintsts |= SDHCI_INT_BUF_WR_RDY;
            } else {
                bcm4339_exec_cmd53_buf(s, false, fn, reg_addr, inc_addr,
                                       s->pio_buf, total);
                s->norintsts |= SDHCI_INT_BUF_RD_RDY;
            }
        }
        break;
    }

    default:
        /* SD memory commands (CMD8/CMD55/ACMD41) time out on an SDIO-only card */
        s->errintsts |= 0x0001u; /* Command Timeout Error */
        break;
    }

    bcm4339_sdio_update_irq(s);
}

static uint64_t bcm4339_sdio_read(void *opaque, hwaddr off, unsigned size)
{
    Bcm4339SdioState *s = opaque;

    if (off == SDHCI_BUF_DATA) {
        uint32_t v = 0;
        for (unsigned i = 0; i < size; i++) {
            uint8_t b = (s->pio_pos < s->pio_len) ? s->pio_buf[s->pio_pos++] : 0;
            v |= (uint32_t)b << (8 * i);
        }
        if (s->pio_len > 0 && s->pio_pos >= s->pio_len) {
            s->pio_len = 0;
            s->pio_pos = 0;
            s->blk_count = 0;
            s->norintsts &= ~SDHCI_INT_BUF_RD_RDY;
            s->norintsts |= SDHCI_INT_XFER_DONE;
            bcm4339_sdio_update_irq(s);
        }
        return v;
    }

    /* Assemble arbitrary 1/2/4-byte little-endian register reads */
    uint8_t bytes[4] = { 0 };
    for (unsigned i = 0; i < size; i++) {
        hwaddr a = off + i;
        uint8_t b = 0;
        switch (a) {
        case 0x00 ... 0x03: b = (s->sdma_addr >> ((a - 0x00) * 8)) & 0xFF; break;
        case 0x04 ... 0x05: b = (s->blk_size >> ((a - 0x04) * 8)) & 0xFF; break;
        case 0x06 ... 0x07: b = (s->blk_count >> ((a - 0x06) * 8)) & 0xFF; break;
        case 0x08 ... 0x0B: b = (s->arg >> ((a - 0x08) * 8)) & 0xFF; break;
        case 0x0C ... 0x0D: b = (s->xfer_mode >> ((a - 0x0C) * 8)) & 0xFF; break;
        case 0x0E ... 0x0F: b = (s->cmd_reg >> ((a - 0x0E) * 8)) & 0xFF; break;
        case 0x10 ... 0x1F:
            b = (s->rsp[(a - 0x10) >> 2] >> (((a - 0x10) & 3) * 8)) & 0xFF;
            break;
        case 0x24 ... 0x27: {
            /* Card inserted + stable + write-enabled + DAT[3:0] high */
            uint32_t prn = 0x01FF0000u;
            if (s->pio_len > 0) {
                prn |= s->pio_is_write ? (1u << 10) : (1u << 11);
            }
            b = (prn >> ((a - 0x24) * 8)) & 0xFF;
            break;
        }
        case 0x28: b = s->hostctl; break;
        case 0x29: b = s->pwrctl; break;
        case 0x2C ... 0x2D: b = (s->clkctl >> ((a - 0x2C) * 8)) & 0xFF; break;
        case 0x2E: b = s->timeout_ctl; break;
        case 0x2F: b = 0; break;
        case 0x30 ... 0x31: b = (s->norintsts >> ((a - 0x30) * 8)) & 0xFF; break;
        case 0x32 ... 0x33: b = (s->errintsts >> ((a - 0x32) * 8)) & 0xFF; break;
        case 0x34 ... 0x35: b = (s->norintstsen >> ((a - 0x34) * 8)) & 0xFF; break;
        case 0x36 ... 0x37: b = (s->errintstsen >> ((a - 0x36) * 8)) & 0xFF; break;
        case 0x38 ... 0x39: b = (s->norintsigen >> ((a - 0x38) * 8)) & 0xFF; break;
        case 0x3A ... 0x3B: b = (s->errintsigen >> ((a - 0x3A) * 8)) & 0xFF; break;
        case 0x3E ... 0x3F: b = (s->hostctl2 >> ((a - 0x3E) * 8)) & 0xFF; break;
        case 0x40 ... 0x43:
            /* Matches CXD90045 mmc0 capareg 0x016830B0 */
            b = (0x016830B0u >> ((a - 0x40) * 8)) & 0xFF;
            break;
        case 0x44 ... 0x47:
            b = (0x00000003u >> ((a - 0x44) * 8)) & 0xFF; /* SDR50/SDR104 */
            break;
        case 0x48 ... 0x4B:
            b = (0x00000040u >> ((a - 0x48) * 8)) & 0xFF;
            break;
        case 0x58 ... 0x5B: b = (s->adma_addr >> ((a - 0x58) * 8)) & 0xFF; break;
        case 0xFE ... 0xFF:
            b = (0x1002u >> ((a - 0xFE) * 8)) & 0xFF; /* SDHCI Spec v3.00 */
            break;
        default: b = 0; break;
        }
        bytes[i] = b;
    }

    uint64_t out = 0;
    for (unsigned i = 0; i < size; i++) {
        out |= (uint64_t)bytes[i] << (8 * i);
    }
    return out;
}

static void bcm4339_sdio_reset_card_regs(Bcm4339SdioState *s)
{
    static const uint8_t cis_init[] = {
        /* 0x1000: Common CIS */
        0x20, 0x04, 0xD0, 0x02, 0x39, 0x43,       /* MANFID: 0x02D0, 0x4339 */
        0x22, 0x04, 0x00, 0x40, 0x00, 0x32,       /* FUNCE0: 64B, 25/50MHz  */
        0xFF,                                     /* END */
    };

    memset(s->f0_regs, 0, sizeof(s->f0_regs));
    memset(s->cis_rom, 0xFF, sizeof(s->cis_rom));
    memcpy(&s->cis_rom[0x00], cis_init, sizeof(cis_init));

    /* 0x1040: Function 1 CIS (FUNCE1 max blksize = 64) */
    s->cis_rom[0x40] = 0x22;
    s->cis_rom[0x41] = 0x2A;
    s->cis_rom[0x42] = 0x01;
    s->cis_rom[0x43] = 0x01;
    s->cis_rom[0x44] = 0x11;
    s->cis_rom[0x4E] = 0x40;
    s->cis_rom[0x4F] = 0x00;
    memset(&s->cis_rom[0x50], 0x00, 0x1C);
    s->cis_rom[0x6C] = 0xFF;

    /* 0x1080: Function 2 CIS (FUNCE2 max blksize = 512) */
    s->cis_rom[0x80] = 0x22;
    s->cis_rom[0x81] = 0x2A;
    s->cis_rom[0x82] = 0x01;
    s->cis_rom[0x83] = 0x01;
    s->cis_rom[0x84] = 0x11;
    s->cis_rom[0x8E] = 0x00;
    s->cis_rom[0x8F] = 0x02;
    memset(&s->cis_rom[0x90], 0x00, 0x1C);
    s->cis_rom[0xAC] = 0xFF;

    s->f0_regs[0x00] = 0x43; /* CCCR v3 / SDIO v3.00 */
    s->f0_regs[0x01] = 0x02; /* SD Spec v2.00 */
    s->f0_regs[0x08] = 0x37; /* Card Capability: SDC|SMB|SRW|SBS|E4MI */
    s->f0_regs[0x09] = 0x00; /* Common CIS @ 0x001000 */
    s->f0_regs[0x0A] = 0x10;
    s->f0_regs[0x0B] = 0x00;
    s->f0_regs[0x10] = 0x40; /* Fn0 block size = 64 */
    s->f0_regs[0x13] = 0x03; /* High-Speed supported + enabled */
    s->f0_regs[0x14] = 0x07; /* UHS-I SDR50/SDR104/DDR50 */

    /* FBR1 @ 0x100 (CIS @ 0x001040, blksize 64) */
    s->f0_regs[0x109] = 0x40;
    s->f0_regs[0x10A] = 0x10;
    s->f0_regs[0x110] = 0x40;

    /* FBR2 @ 0x200 (CIS @ 0x001080, blksize 512) */
    s->f0_regs[0x209] = 0x80;
    s->f0_regs[0x20A] = 0x10;
    s->f0_regs[0x210] = 0x00;
    s->f0_regs[0x211] = 0x02;

    s->fn_blksize[0] = 64;
    s->fn_blksize[1] = 64;
    s->fn_blksize[2] = 512;
}

static void bcm4339_sdio_write(void *opaque, hwaddr off, uint64_t val,
                               unsigned size)
{
    Bcm4339SdioState *s = opaque;

    if (off == SDHCI_BUF_DATA) {
        for (unsigned i = 0; i < size; i++) {
            if (s->pio_pos < s->pio_len) {
                s->pio_buf[s->pio_pos++] = (val >> (8 * i)) & 0xFF;
            }
        }
        if (s->pio_len > 0 && s->pio_pos >= s->pio_len) {
            bcm4339_exec_cmd53_buf(s, true, s->pending_fn, s->pending_addr,
                                   s->pending_inc, s->pio_buf, s->pio_len);
            s->pio_len = 0;
            s->pio_pos = 0;
            s->blk_count = 0;
            s->norintsts &= ~SDHCI_INT_BUF_WR_RDY;
            s->norintsts |= SDHCI_INT_XFER_DONE;
            bcm4339_sdio_update_irq(s);
        }
        return;
    }

    bool trigger_cmd = false;
    uint16_t cmd_to_run = 0;

    for (unsigned i = 0; i < size; i++) {
        hwaddr a = off + i;
        uint8_t b = (val >> (8 * i)) & 0xFF;
        unsigned sh;

        switch (a) {
        case 0x00 ... 0x03:
            sh = (a - 0x00) * 8;
            s->sdma_addr = (s->sdma_addr & ~(0xFFu << sh)) | ((uint32_t)b << sh);
            break;
        case 0x04 ... 0x05:
            sh = (a - 0x04) * 8;
            s->blk_size = (s->blk_size & ~(0xFFu << sh)) | ((uint16_t)b << sh);
            break;
        case 0x06 ... 0x07:
            sh = (a - 0x06) * 8;
            s->blk_count = (s->blk_count & ~(0xFFu << sh)) | ((uint16_t)b << sh);
            break;
        case 0x08 ... 0x0B:
            sh = (a - 0x08) * 8;
            s->arg = (s->arg & ~(0xFFu << sh)) | ((uint32_t)b << sh);
            break;
        case 0x0C ... 0x0D:
            sh = (a - 0x0C) * 8;
            s->xfer_mode = (s->xfer_mode & ~(0xFFu << sh)) | ((uint16_t)b << sh);
            break;
        case 0x0E:
            s->cmd_reg = (s->cmd_reg & 0xFF00u) | b;
            break;
        case 0x0F:
            s->cmd_reg = (s->cmd_reg & 0x00FFu) | ((uint16_t)b << 8);
            cmd_to_run = s->cmd_reg;
            trigger_cmd = true;
            break;
        case 0x28:
            s->hostctl = b;
            break;
        case 0x29:
            s->pwrctl = b;
            break;
        case 0x2C:
            /* Set Internal Clock Stable (bit 1) whenever Internal Clock En */
            s->clkctl = (s->clkctl & 0xFF00u) | b | ((b & 0x01) ? 0x02 : 0x00);
            break;
        case 0x2D:
            s->clkctl = (s->clkctl & 0x00FFu) | ((uint16_t)b << 8);
            break;
        case 0x2E:
            s->timeout_ctl = b;
            break;
        case 0x2F:
            if (b & 0x01) { /* Reset All */
                s->norintsts = 0;
                s->errintsts = 0;
                s->pio_len = 0;
                s->pio_pos = 0;
            }
            if (b & 0x02) { /* Reset CMD */
                s->norintsts &= ~SDHCI_INT_CMD_DONE;
                s->errintsts &= ~0x000Fu;
            }
            if (b & 0x04) { /* Reset DAT */
                s->pio_len = 0;
                s->pio_pos = 0;
                s->norintsts &= ~(SDHCI_INT_XFER_DONE | SDHCI_INT_DMA_END |
                                  SDHCI_INT_BUF_RD_RDY | SDHCI_INT_BUF_WR_RDY);
            }
            break;
        case 0x30 ... 0x31:
            /* Write-1-to-clear except CARD_INT (bit 8) */
            sh = (a - 0x30) * 8;
            s->norintsts &= ~(((uint16_t)b << sh) & ~SDHCI_INT_CARD_INT);
            break;
        case 0x32 ... 0x33:
            sh = (a - 0x32) * 8;
            s->errintsts &= ~((uint16_t)b << sh);
            break;
        case 0x34 ... 0x35:
            sh = (a - 0x34) * 8;
            s->norintstsen = (s->norintstsen & ~(0xFFu << sh)) |
                             ((uint16_t)b << sh);
            break;
        case 0x36 ... 0x37:
            sh = (a - 0x36) * 8;
            s->errintstsen = (s->errintstsen & ~(0xFFu << sh)) |
                             ((uint16_t)b << sh);
            break;
        case 0x38 ... 0x39:
            sh = (a - 0x38) * 8;
            s->norintsigen = (s->norintsigen & ~(0xFFu << sh)) |
                             ((uint16_t)b << sh);
            break;
        case 0x3A ... 0x3B:
            sh = (a - 0x3A) * 8;
            s->errintsigen = (s->errintsigen & ~(0xFFu << sh)) |
                             ((uint16_t)b << sh);
            break;
        case 0x3E ... 0x3F:
            /* Clear UHS execute-tuning bit 6 immediately (tuning succeeded) */
            sh = (a - 0x3E) * 8;
            s->hostctl2 = ((s->hostctl2 & ~(0xFFu << sh)) |
                           ((uint16_t)b << sh)) & ~0x0040u;
            break;
        case 0x58 ... 0x5B:
            sh = (a - 0x58) * 8;
            s->adma_addr = (s->adma_addr & ~(0xFFu << sh)) |
                           ((uint32_t)b << sh);
            break;
        default:
            break;
        }
    }

    if (trigger_cmd) {
        bcm4339_issue_cmd(s, cmd_to_run);
    } else {
        bcm4339_sdio_update_irq(s);
    }
}

static const MemoryRegionOps bcm4339_sdio_ops = {
    .read = bcm4339_sdio_read,
    .write = bcm4339_sdio_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static void bcm4339_sdio_reset(DeviceState *dev)
{
    Bcm4339SdioState *s = CXD90045_BCM4339_SDIO(dev);

    s->sdma_addr = 0;
    s->blk_size = 0;
    s->blk_count = 0;
    s->arg = 0;
    s->xfer_mode = 0;
    s->cmd_reg = 0;
    memset(s->rsp, 0, sizeof(s->rsp));
    s->hostctl = 0;
    s->pwrctl = 0;
    s->clkctl = 0;
    s->timeout_ctl = 0;
    s->norintsts = 0;
    s->errintsts = 0;
    s->norintstsen = 0;
    s->errintstsen = 0;
    s->norintsigen = 0;
    s->errintsigen = 0;
    s->hostctl2 = 0;
    s->adma_addr = 0;
    s->pio_len = 0;
    s->pio_pos = 0;
    s->rca = 0x0001;
    s->selected = false;
    s->sbaddr_low = 0x00;
    s->sbaddr_mid = 0x00;
    s->sbaddr_high = 0x18; /* default window 0x18000000 (ChipCommon) */
    s->chipclkcsr = 0;
    s->sdiopullup = 0;
    s->devctl = 0;
    s->sdio_intstatus = 0;
    s->sdio_hostintmask = 0;
    s->sdio_tohostmbox = 0;
    s->sdio_tosbmbox = 0;
    s->sdio_tosbmboxdata = 0;
    s->sdio_tohostmboxdata = 0;
    for (int i = 0; i < 4; i++) {
        s->ai_ioctrl[i] = 0x00000001u;
        s->ai_resetctrl[i] = 0;
    }
    s->pmu_ctl = 0;
    s->f2_rx_len = 0;
    s->sdpcm_tx_max_seq = 8;
    s->sdpcm_rx_seq = 0;
    if (s->tcm) {
        memset(s->tcm, 0, BCM4339_TCM_SIZE);
    }
    bcm4339_sdio_reset_card_regs(s);
    qemu_set_irq(s->irq, 0);
}

static void bcm4339_sdio_realize(DeviceState *dev, Error **errp)
{
    Bcm4339SdioState *s = CXD90045_BCM4339_SDIO(dev);

    s->tcm = g_malloc0(BCM4339_TCM_SIZE);
    memory_region_init_io(&s->iomem, OBJECT(dev), &bcm4339_sdio_ops, s,
                          "cxd90045.bcm4339-sdio", BCM4339_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->iomem);
    sysbus_init_irq(SYS_BUS_DEVICE(dev), &s->irq);
}

static void bcm4339_get_u64(Object *obj, Visitor *v, const char *name,
                            void *opaque, Error **errp)
{
    Bcm4339SdioState *s = CXD90045_BCM4339_SDIO(obj);
    uint64_t val = 0;

    if (strcmp(name, "cmd5-count") == 0) {
        val = s->cmd5_count;
    } else if (strcmp(name, "cmd52-count") == 0) {
        val = s->cmd52_count;
    } else if (strcmp(name, "cmd53-count") == 0) {
        val = s->cmd53_count;
    } else if (strcmp(name, "fw-bytes-written") == 0) {
        val = s->fw_bytes_written;
    } else if (strcmp(name, "bcdc-ioctls") == 0) {
        val = s->bcdc_ioctls;
    }
    visit_type_uint64(v, name, &val, errp);
}

static void bcm4339_sdio_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = bcm4339_sdio_realize;
    device_class_set_legacy_reset(dc, bcm4339_sdio_reset);
    object_class_property_add(klass, "cmd5-count", "uint64",
                              bcm4339_get_u64, NULL, NULL, NULL);
    object_class_property_add(klass, "cmd52-count", "uint64",
                              bcm4339_get_u64, NULL, NULL, NULL);
    object_class_property_add(klass, "cmd53-count", "uint64",
                              bcm4339_get_u64, NULL, NULL, NULL);
    object_class_property_add(klass, "fw-bytes-written", "uint64",
                              bcm4339_get_u64, NULL, NULL, NULL);
    object_class_property_add(klass, "bcdc-ioctls", "uint64",
                              bcm4339_get_u64, NULL, NULL, NULL);
}

static const TypeInfo bcm4339_sdio_info = {
    .name          = TYPE_CXD90045_BCM4339_SDIO,
    .parent        = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Bcm4339SdioState),
    .class_init    = bcm4339_sdio_class_init,
};

DeviceState *cxd90045_bcm4339_sdio_create(MemoryRegion *sysmem, hwaddr base,
                                          qemu_irq irq, DeviceState *gpio)
{
    DeviceState *dev = qdev_new(TYPE_CXD90045_BCM4339_SDIO);
    Bcm4339SdioState *s = CXD90045_BCM4339_SDIO(dev);

    s->gpio = gpio;
    object_property_add_child(OBJECT(qdev_get_machine()),
                              "cxd90045-bcm4339-sdio", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map_overlap(SYS_BUS_DEVICE(dev), 0, base, -400);
    sysbus_connect_irq(SYS_BUS_DEVICE(dev), 0, irq);
    return dev;
}

/* =========================================================================
 * Part 2: BCM4339 Bluetooth 4.1 H4 HCI + HCD Firmware Loader on UART3
 * ========================================================================= */

#define TYPE_CHARDEV_BCM4339_BT "chardev-bcm4339-bt"
OBJECT_DECLARE_SIMPLE_TYPE(Bcm4339BtChardev, CHARDEV_BCM4339_BT)

#define BT_RX_FIFO_SIZE 4096

struct Bcm4339BtChardev {
    Chardev parent;
    uint8_t cmd_buf[516];
    unsigned cmd_pos;
    unsigned cmd_need;

    Fifo8 rx_fifo;
    QEMUTimer *rx_timer;

    uint8_t bd_addr[6];
    char local_name[248];
    uint8_t scan_enable;
    uint32_t baud_rate;
    bool fw_launched;

    uint64_t hci_commands;
    uint64_t hcd_bytes;
};

static void bt_schedule_drain(Bcm4339BtChardev *bt)
{
    if (!fifo8_is_empty(&bt->rx_fifo)) {
        timer_mod(bt->rx_timer,
                  qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 10 * SCALE_US);
    }
}

static void bt_rx_timer_cb(void *opaque)
{
    Bcm4339BtChardev *bt = opaque;
    Chardev *chr = CHARDEV(bt);

    while (!fifo8_is_empty(&bt->rx_fifo)) {
        int can = qemu_chr_be_can_write(chr);
        if (can <= 0) {
            timer_mod(bt->rx_timer,
                      qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 100 * SCALE_US);
            return;
        }
        uint32_t avail = 0;
        const uint8_t *ptr = fifo8_pop_bufptr(&bt->rx_fifo,
                                              MIN((uint32_t)can,
                                                  fifo8_num_used(&bt->rx_fifo)),
                                              &avail);
        if (avail > 0) {
            qemu_chr_be_write(chr, ptr, avail);
        }
    }
}

static void bt_push_event(Bcm4339BtChardev *bt, uint8_t evt_code,
                          const uint8_t *params, uint8_t plen)
{
    uint32_t need = 3u + plen;
    if (fifo8_num_free(&bt->rx_fifo) < need) {
        return;
    }
    fifo8_push(&bt->rx_fifo, 0x04); /* H4 HCI Event indicator */
    fifo8_push(&bt->rx_fifo, evt_code);
    fifo8_push(&bt->rx_fifo, plen);
    if (plen > 0 && params) {
        fifo8_push_all(&bt->rx_fifo, params, plen);
    }
    bt_schedule_drain(bt);
}

static void bt_cmd_complete(Bcm4339BtChardev *bt, uint16_t opcode,
                            uint8_t status, const uint8_t *ret_data,
                            uint8_t ret_len)
{
    uint8_t buf[255];
    uint8_t plen = (uint8_t)(4u + ret_len);

    buf[0] = 0x01; /* Num_HCI_Command_Packets */
    buf[1] = opcode & 0xFF;
    buf[2] = (opcode >> 8) & 0xFF;
    buf[3] = status;
    if (ret_len > 0 && ret_data) {
        memcpy(&buf[4], ret_data, ret_len);
    }
    bt_push_event(bt, 0x0E, buf, plen);
}

static void bt_handle_hci_cmd(Bcm4339BtChardev *bt, uint16_t opcode,
                              const uint8_t *param, uint8_t plen)
{
    uint8_t rsp[248];
    memset(rsp, 0, sizeof(rsp));
    bt->hci_commands++;

    switch (opcode) {
    case 0x0C03: /* HCI_Reset */
        bt->scan_enable = 0;
        bt_cmd_complete(bt, opcode, 0x00, NULL, 0);
        break;

    case 0x1001: /* HCI_Read_Local_Version_Information (BT 4.1 Broadcom 4339) */
        rsp[0] = 0x07;       /* HCI Version: BT Core 4.1 */
        stw_le_p(&rsp[1], 0x210B);
        rsp[3] = 0x07;       /* LMP Version: BT Core 4.1 */
        stw_le_p(&rsp[4], 0x000F); /* Manufacturer: Broadcom (15) */
        stw_le_p(&rsp[6], 0x4103); /* BCM4339 LMP Subversion */
        bt_cmd_complete(bt, opcode, 0x00, rsp, 8);
        break;

    case 0x1002: /* HCI_Read_Local_Supported_Commands (64 bytes) */
        memset(rsp, 0xFF, 28);
        bt_cmd_complete(bt, opcode, 0x00, rsp, 64);
        break;

    case 0x1003: /* HCI_Read_Local_Supported_Features (8 bytes, BR/EDR + LE) */
        rsp[0] = 0xFF; rsp[1] = 0xFE; rsp[2] = 0x8F; rsp[3] = 0xFE;
        rsp[4] = 0xD8; rsp[5] = 0x3D; rsp[6] = 0x5B; rsp[7] = 0x87;
        bt_cmd_complete(bt, opcode, 0x00, rsp, 8);
        break;

    case 0x1004: /* HCI_Read_Local_Extended_Features */
        rsp[0] = plen >= 1 ? param[0] : 0;
        rsp[1] = 1;
        rsp[2] = 0x02; /* Host LE supported */
        bt_cmd_complete(bt, opcode, 0x00, rsp, 10);
        break;

    case 0x1005: /* HCI_Read_Buffer_Size */
        stw_le_p(&rsp[0], 1021); /* ACL len */
        rsp[2] = 64;             /* SCO len */
        stw_le_p(&rsp[3], 8);    /* Total ACL pkts */
        stw_le_p(&rsp[5], 1);    /* Total SCO pkts */
        bt_cmd_complete(bt, opcode, 0x00, rsp, 7);
        break;

    case 0x1009: /* HCI_Read_BD_ADDR */
        memcpy(rsp, bt->bd_addr, 6);
        bt_cmd_complete(bt, opcode, 0x00, rsp, 6);
        break;

    case 0x0C13: /* HCI_Write_Local_Name */
        memcpy(bt->local_name, param, MIN((size_t)plen, sizeof(bt->local_name) - 1));
        bt->local_name[MIN((size_t)plen, sizeof(bt->local_name) - 1)] = '\0';
        bt_cmd_complete(bt, opcode, 0x00, NULL, 0);
        break;

    case 0x0C14: /* HCI_Read_Local_Name */
        memcpy(rsp, bt->local_name, 248);
        bt_cmd_complete(bt, opcode, 0x00, rsp, 248);
        break;

    case 0x0C1A: /* HCI_Write_Scan_Enable */
        if (plen >= 1) {
            bt->scan_enable = param[0];
        }
        bt_cmd_complete(bt, opcode, 0x00, NULL, 0);
        break;

    case 0x2002: /* HCI_LE_Read_Buffer_Size */
        stw_le_p(&rsp[0], 251);
        rsp[2] = 8;
        bt_cmd_complete(bt, opcode, 0x00, rsp, 3);
        break;

    case 0x2003: /* HCI_LE_Read_Local_Supported_Features */
        rsp[0] = 0x1F;
        bt_cmd_complete(bt, opcode, 0x00, rsp, 8);
        break;

    case 0x2007: /* HCI_LE_Read_Advertising_Physical_Channel_Tx_Power */
        rsp[0] = 0x04;
        bt_cmd_complete(bt, opcode, 0x00, rsp, 1);
        break;

    case 0x200F: /* HCI_LE_Read_Filter_Accept_List_Size */
        rsp[0] = 16;
        bt_cmd_complete(bt, opcode, 0x00, rsp, 1);
        break;

    case 0x201C: /* HCI_LE_Read_Supported_States */
        memset(rsp, 0xFF, 5);
        rsp[5] = 0x03;
        bt_cmd_complete(bt, opcode, 0x00, rsp, 8);
        break;

    /* Broadcom BCM4339 vendor HCI commands used by bsa_server & bt_firm.hcd */
    case 0xFC01: /* VSC_Write_BD_ADDR */
        if (plen >= 6) {
            memcpy(bt->bd_addr, param, 6);
        }
        bt_cmd_complete(bt, opcode, 0x00, NULL, 0);
        break;

    case 0xFC18: /* VSC_Update_UART_Baud_Rate */
        if (plen >= 6) {
            bt->baud_rate = ldl_le_p(param + 2);
        }
        bt_cmd_complete(bt, opcode, 0x00, NULL, 0);
        break;

    case 0xFC2E: /* VSC_Download_Minidriver */
        bt->fw_launched = false;
        bt_cmd_complete(bt, opcode, 0x00, NULL, 0);
        break;

    case 0xFC4C: /* VSC_Write_RAM (bt_firm.hcd records) */
        if (plen > 4) {
            bt->hcd_bytes += (plen - 4);
        }
        bt_cmd_complete(bt, opcode, 0x00, NULL, 0);
        break;

    case 0xFC4D: /* VSC_Read_RAM */
        bt_cmd_complete(bt, opcode, 0x00, rsp, plen >= 5 ? param[4] : 4);
        break;

    case 0xFC4E: /* VSC_Launch_RAM */
        bt->fw_launched = true;
        bt_cmd_complete(bt, opcode, 0x00, NULL, 0);
        break;

    case 0xFC79: /* VSC_Read_Verbose_Config_Version_Info */
        rsp[0] = 0x01; /* Chip ID / target */
        rsp[1] = 0x04;
        stw_le_p(&rsp[2], 0x4103);
        bt_cmd_complete(bt, opcode, 0x00, rsp, 6);
        break;

    default:
        /* Generic Command Complete success for host mask/LE adv/sleep VSCs */
        bt_cmd_complete(bt, opcode, 0x00, NULL, 0);
        break;
    }
}

static int bt_chr_write(Chardev *chr, const uint8_t *buf, int len)
{
    Bcm4339BtChardev *bt = CHARDEV_BCM4339_BT(chr);

    for (int i = 0; i < len; i++) {
        uint8_t b = buf[i];

        if (bt->cmd_pos == 0) {
            if (b == 0x01) { /* H4 HCI Command packet */
                bt->cmd_buf[bt->cmd_pos++] = b;
                bt->cmd_need = 4;
            } else if (b == 0x02) { /* H4 ACL Data packet */
                bt->cmd_buf[bt->cmd_pos++] = b;
                bt->cmd_need = 5;
            }
            continue;
        }

        if (bt->cmd_pos < sizeof(bt->cmd_buf)) {
            bt->cmd_buf[bt->cmd_pos++] = b;
        }

        if (bt->cmd_buf[0] == 0x01) {
            if (bt->cmd_pos == 4) {
                bt->cmd_need = 4u + bt->cmd_buf[3];
            }
            if (bt->cmd_pos >= bt->cmd_need) {
                uint16_t op = lduw_le_p(&bt->cmd_buf[1]);
                bt_handle_hci_cmd(bt, op, &bt->cmd_buf[4], bt->cmd_buf[3]);
                bt->cmd_pos = 0;
                bt->cmd_need = 0;
            }
        } else if (bt->cmd_buf[0] == 0x02) {
            if (bt->cmd_pos == 5) {
                uint16_t dlen = lduw_le_p(&bt->cmd_buf[3]);
                bt->cmd_need = MIN((unsigned)sizeof(bt->cmd_buf), 5u + dlen);
            }
            if (bt->cmd_pos >= bt->cmd_need) {
                /* Number Of Completed Packets (0x13) for ACL handle */
                uint16_t hdl = lduw_le_p(&bt->cmd_buf[1]) & 0x0FFFu;
                uint8_t ncp[5] = { 1, hdl & 0xFF, hdl >> 8, 1, 0 };
                bt_push_event(bt, 0x13, ncp, sizeof(ncp));
                bt->cmd_pos = 0;
                bt->cmd_need = 0;
            }
        }
    }
    return len;
}

static bool bt_chr_open(Chardev *chr, ChardevBackend *backend, Error **errp)
{
    Bcm4339BtChardev *bt = CHARDEV_BCM4339_BT(chr);
    static const uint8_t default_bd[6] = { 0x02, 0x00, 0x64, 0x39, 0x43, 0x54 };

    fifo8_create(&bt->rx_fifo, BT_RX_FIFO_SIZE);
    bt->rx_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, bt_rx_timer_cb, bt);
    memcpy(bt->bd_addr, default_bd, sizeof(default_bd));
    pstrcpy(bt->local_name, sizeof(bt->local_name), "ILCE-6400 QEMU");
    bt->baud_rate = 115200;
    qemu_chr_be_event(chr, CHR_EVENT_OPENED);
    return true;
}

static void bt_get_u64(Object *obj, Visitor *v, const char *name,
                       void *opaque, Error **errp)
{
    Bcm4339BtChardev *bt = CHARDEV_BCM4339_BT(obj);
    uint64_t val = 0;

    if (strcmp(name, "hci-commands") == 0) {
        val = bt->hci_commands;
    } else if (strcmp(name, "hcd-bytes") == 0) {
        val = bt->hcd_bytes;
    } else if (strcmp(name, "baud-rate") == 0) {
        val = bt->baud_rate;
    }
    visit_type_uint64(v, name, &val, errp);
}

static bool bt_get_fw_launched(Object *obj, Error **errp)
{
    return CHARDEV_BCM4339_BT(obj)->fw_launched;
}

static void bt_chr_class_init(ObjectClass *oc, const void *data)
{
    ChardevClass *cc = CHARDEV_CLASS(oc);

    cc->internal = true;
    cc->chr_open = bt_chr_open;
    cc->chr_write = bt_chr_write;
    object_class_property_add(oc, "hci-commands", "uint64",
                              bt_get_u64, NULL, NULL, NULL);
    object_class_property_add(oc, "hcd-bytes", "uint64",
                              bt_get_u64, NULL, NULL, NULL);
    object_class_property_add(oc, "baud-rate", "uint64",
                              bt_get_u64, NULL, NULL, NULL);
    object_class_property_add_bool(oc, "fw-launched",
                                   bt_get_fw_launched, NULL);
}

static const TypeInfo bt_chr_info = {
    .name          = TYPE_CHARDEV_BCM4339_BT,
    .parent        = TYPE_CHARDEV,
    .instance_size = sizeof(Bcm4339BtChardev),
    .class_init    = bt_chr_class_init,
};

static void bcm4339_register_types(void)
{
    type_register_static(&bcm4339_sdio_info);
    type_register_static(&bt_chr_info);
}
type_init(bcm4339_register_types)

Chardev *cxd90045_bcm4339_bt_chardev_new(const char *id)
{
    Chardev *chr = qemu_chardev_new(id, TYPE_CHARDEV_BCM4339_BT, NULL,
                                    NULL, &error_fatal);
    return chr;
}
