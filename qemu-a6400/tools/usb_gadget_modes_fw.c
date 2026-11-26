/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Freestanding Cortex-A5 bare-metal DWC3 multi-mode USB gadget firmware for
 * QEMU sony-a6400 (CXD90045).
 *
 * Drives the real cxd90045-dwc3 controller at 0xF020C000 and FUSB VBUS/CID
 * status register at 0xF2920010 across all five Sony USB connection modes:
 *   Mode 1 (msc):     054C:0CA8 Mass Storage Bulk-Only Transport (64-sector RAM disk)
 *   Mode 2 (ptp):     054C:0CAA PC Remote / Still Image PTP + Interrupt-IN events
 *   Mode 3 (uvc):     054C:0322 Composite UVC 1.1 MJPEG + Still Image PTP (IAD)
 *   Mode 4 (hid):     054C:096F Bus-Power / HID (exact E0598 descriptors + report)
 *   Mode 5 (jiritsu): 054C:0336 Jiritsu Service Vendor Bulk IN/OUT (E0525/E0643)
 *
 * Compile:
 *   arm-linux-gnueabi-gcc -mcpu=cortex-a5 -marm -mno-unaligned-access \
 *     -ffreestanding -nostdlib -O2 -Wall -Wextra -Werror \
 *     -Wl,-Ttext=0x00108000 -Wl,--build-id=none \
 *     qemu-a6400/tools/usb_gadget_modes_fw.c -o fw.elf
 *   arm-linux-gnueabi-objcopy -O binary fw.elf fw.bin
 */

#include <stdint.h>
#include <stddef.h>

/* Boot stub placed at 0x00108000 (.text.startup precedes *(.text) in .text) */
__asm__(
    ".pushsection .text.startup, \"ax\"\n"
    ".arm\n"
    ".global _start\n"
    "_start:\n"
    "    cpsid   if\n"
    "    msr     cpsr_c, #0xD2\n"
    "    ldr     sp, =0x00178000\n"
    "    msr     cpsr_c, #0xD3\n"
    "    ldr     sp, =0x00180000\n"
    "    ldr     r0, =__bss_start__\n"
    "    ldr     r1, =__bss_end__\n"
    "    mov     r2, #0\n"
    "1:  cmp     r0, r1\n"
    "    strlo   r2, [r0], #4\n"
    "    blo     1b\n"
    "    bl      fw_main\n"
    "2:  wfi\n"
    "    b       2b\n"
    "    .ltorg\n"
    ".popsection\n"
);

/* Freestanding memory primitives (also satisfy compiler-synthesized calls) */
void *memcpy(void *dst, const void *src, size_t n)
{
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    while (n--) {
        *d++ = *s++;
    }
    return dst;
}

void *memset(void *dst, int c, size_t n)
{
    uint8_t *d = (uint8_t *)dst;
    uint8_t v = (uint8_t)c;
    while (n--) {
        *d++ = v;
    }
    return dst;
}

/* Hardware MMIO & RAM layout on sony-a6400 */
#define DWC3_BASE               0xF020C000UL
#define FUSB_VBUS_STATUS        0xF2920010UL
#define UART0_BASE              0xF2000000UL
#define BOOT_PARAMS_ADDR        0x00103000UL
#define SRAM_MBOX_ADDR          0xFE006000UL

/* Non-overlapping 16-byte-aligned DMA buffers inside RAM0 (< 0x40000000) */
#define GEVNT_RING_PA           0x01000000UL
#define GEVNT_RING_BYTES        0x00001000UL  /* 4096 bytes (power of two) */

#define TRB_EP0_PA              0x01002000UL
#define TRB_EP1_PA              0x01002100UL
#define TRB_EP3_PA              0x01002300UL  /* 16 TRBs (256 B) */
#define TRB_EP4_PA              0x01002400UL
#define TRB_EP7_PA              0x01002700UL
#define TRB_EP9_PA              0x01002900UL  /* 16 TRBs (256 B) */

#define BUF_EP0_OUT_PA          0x01010000UL  /* 1024 B Control-OUT */
#define BUF_EP0_STAT_PA         0x01010400UL  /* 64 B Status dummy */
#define BUF_EP0_SETUP_PA        0x01010800UL  /* 64 B SETUP packet */
#define BUF_EP1_IN_PA           0x01011000UL  /* 1024 B Control-IN */
#define BUF_EP3_IN_PA           0x01020000UL  /* 60 KiB Bulk/Intr-IN data */
#define BUF_EP3_RESP_PA         0x0102F000UL  /* 4 KiB PTP Response / CSW */
#define BUF_EP4_OUT_PA          0x01030000UL  /* 64 KiB Bulk-OUT data */
#define BUF_EP7_INTR_PA         0x01040000UL  /* 4 KiB PTP Interrupt-IN */
#define BUF_EP9_UVC_PA          0x01050000UL  /* 64 KiB UVC Bulk-IN */

/* 64-sector (32 KiB) RAM disk in RAM0 */
#define RAMDISK_PA              0x02000000UL
#define RAMDISK_SECTORS         64U
#define RAMDISK_SECTOR_SIZE     512U
#define RAMDISK_BYTES           (RAMDISK_SECTORS * RAMDISK_SECTOR_SIZE)

/* DWC3 register offsets from 0xF020C000 */
#define REG_GCTL                0x0110UL
#define REG_GSNPSID             0x0120UL
#define REG_GHWPARAMS0          0x0140UL
#define REG_GUSB2PHYCFG0        0x0200UL
#define REG_GUSB3PIPECTL0       0x02C0UL
#define REG_GEVNTADRLO0         0x0400UL
#define REG_GEVNTADRHI0         0x0404UL
#define REG_GEVNTSIZ0           0x0408UL
#define REG_GEVNTCOUNT0         0x040CUL
#define REG_DCFG                0x0700UL
#define REG_DCTL                0x0704UL
#define REG_DEVTEN              0x0708UL
#define REG_DSTS                0x070CUL
#define REG_DALEPENA            0x0720UL
#define REG_EP_BASE             0x0800UL

#define DCTL_RUN_STOP           (1U << 31)
#define DCTL_CSFTRST            (1U << 30)
#define DSTS_DEVCTRLHLT         (1U << 22)

#define DEPCMD_CMDACT           (1U << 10)
#define DEPCMD_SETEPCONFIG      1U
#define DEPCMD_SETTRANSFRES     2U
#define DEPCMD_SETSTALL         4U
#define DEPCMD_CLEARSTALL       5U
#define DEPCMD_STARTTRANSFER    6U
#define DEPCMD_ENDTRANSFER      8U
#define DEPCMD_STARTNEWCONFIG   9U

#define DEVT_DISCONNECT         0U
#define DEVT_USBRESET           1U
#define DEVT_CONNECTDONE        2U

#define DEPEVT_XFERCOMPLETE     1U
#define DEPEVT_XFERNOTREADY     3U

#define TRB_CTRL_HWO            (1U << 0)
#define TRB_CTRL_LST            (1U << 1)
#define TRB_CTRL_IOC            (1U << 11)

#define TRBCTL_NORMAL           1U
#define TRBCTL_CONTROL_SETUP    2U
#define TRBCTL_CONTROL_STATUS2  3U
#define TRBCTL_CONTROL_STATUS3  4U
#define TRBCTL_CONTROL_DATA     5U
#define TRBCTL_LINK             8U

/* USB modes */
#define USB_MODE_NONE           0U
#define USB_MODE_MSC            1U  /* 054C:0CA8 */
#define USB_MODE_PTP            2U  /* 054C:0CAA */
#define USB_MODE_COMPOSITE      3U  /* 054C:0322 */
#define USB_MODE_HID            4U  /* 054C:096F */
#define USB_MODE_JIRITSU        5U  /* 054C:0336 */

#define MBOX_MAGIC              0x5553424DU  /* 'USBM' */
#define MBOX_FLAG_MSC_WP        (1U << 0)
#define MBOX_FLAG_PREARM_STAT3  (1U << 1)

typedef struct {
    volatile uint32_t magic;                /* +0x00 */
    volatile uint32_t req_mode;             /* +0x04 */
    volatile uint32_t active_mode;          /* +0x08 */
    volatile uint32_t flags;                /* +0x0C */
    volatile uint32_t ghwparams0;           /* +0x10 */
    volatile uint32_t gsnpsid;              /* +0x14 */
    volatile uint32_t fusb_last;            /* +0x18 */
    volatile uint32_t vbus_attach_count;    /* +0x1C */
    volatile uint32_t vbus_detach_count;    /* +0x20 */
    volatile uint32_t mode_switch_count;    /* +0x24 */
    volatile uint32_t halt_confirmed_count; /* +0x28 */
    volatile uint32_t usb_reset_count;      /* +0x2C */
    volatile uint32_t ep0_setup_count;      /* +0x30 */
    volatile uint32_t last_dsts;            /* +0x34 */
    volatile uint32_t uvc_frames_sent;      /* +0x38 */
    volatile uint32_t ptp_ops_count;        /* +0x3C */
} UsbFwMailbox;

typedef struct {
    uint32_t bpl;
    uint32_t bph;
    uint32_t size;
    uint32_t ctrl;
} Dwc3Trb;

static inline uint32_t mmio_read32(uintptr_t addr)
{
    return *(volatile uint32_t *)addr;
}

static inline void mmio_write32(uintptr_t addr, uint32_t val)
{
    *(volatile uint32_t *)addr = val;
}

static inline uint32_t dwc3_read(uint32_t off)
{
    return mmio_read32(DWC3_BASE + off);
}

static inline void dwc3_write(uint32_t off, uint32_t val)
{
    mmio_write32(DWC3_BASE + off, val);
}

static inline void dmb_barrier(void)
{
    __asm__ volatile("dmb sy" ::: "memory");
}

static inline uint16_t get_le16(const uint8_t *p)
{
    const volatile uint8_t *vp = p;
    return (uint16_t)vp[0] | ((uint16_t)vp[1] << 8);
}

static inline uint32_t get_le32(const uint8_t *p)
{
    const volatile uint8_t *vp = p;
    return (uint32_t)vp[0] | ((uint32_t)vp[1] << 8) |
           ((uint32_t)vp[2] << 16) | ((uint32_t)vp[3] << 24);
}

static inline uint32_t get_be32(const uint8_t *p)
{
    const volatile uint8_t *vp = p;
    return ((uint32_t)vp[0] << 24) | ((uint32_t)vp[1] << 16) |
           ((uint32_t)vp[2] << 8) | (uint32_t)vp[3];
}

static inline uint16_t get_be16(const uint8_t *p)
{
    const volatile uint8_t *vp = p;
    return ((uint16_t)vp[0] << 8) | (uint16_t)vp[1];
}

static inline void put_le16(uint8_t *p, uint16_t v)
{
    volatile uint8_t *vp = p;
    vp[0] = (uint8_t)(v & 0xFFU);
    vp[1] = (uint8_t)(v >> 8);
}

static inline void put_le32(uint8_t *p, uint32_t v)
{
    volatile uint8_t *vp = p;
    vp[0] = (uint8_t)(v & 0xFFU);
    vp[1] = (uint8_t)((v >> 8) & 0xFFU);
    vp[2] = (uint8_t)((v >> 16) & 0xFFU);
    vp[3] = (uint8_t)((v >> 24) & 0xFFU);
}

static inline void put_be32(uint8_t *p, uint32_t v)
{
    volatile uint8_t *vp = p;
    vp[0] = (uint8_t)((v >> 24) & 0xFFU);
    vp[1] = (uint8_t)((v >> 16) & 0xFFU);
    vp[2] = (uint8_t)((v >> 8) & 0xFFU);
    vp[3] = (uint8_t)(v & 0xFFU);
}

/* -------------------------------------------------------------------------
 * Authentic Sony USB Descriptors across Modes 1..5
 * ------------------------------------------------------------------------- */

/* Mode 1: MSC 054C:0CA8 (E0084) */
static const uint8_t desc_dev_msc[18] = {
    0x12, 0x01, 0x00, 0x02, 0x00, 0x00, 0x00, 0x40,
    0x4C, 0x05, 0xA8, 0x0C, 0x00, 0x01, 0x01, 0x02, 0x03, 0x01
};
static const uint8_t desc_cfg_msc[32] = {
    0x09, 0x02, 0x20, 0x00, 0x01, 0x01, 0x00, 0xC0, 0xFA,
    0x09, 0x04, 0x00, 0x00, 0x02, 0x08, 0x06, 0x50, 0x00,
    0x07, 0x05, 0x81, 0x02, 0x00, 0x02, 0x00,
    0x07, 0x05, 0x02, 0x02, 0x00, 0x02, 0x00
};

/* Mode 2: PC Remote / PTP 054C:0CAA (E0152, E0258, E0315) */
static const uint8_t desc_dev_ptp[18] = {
    0x12, 0x01, 0x00, 0x02, 0x00, 0x00, 0x00, 0x40,
    0x4C, 0x05, 0xAA, 0x0C, 0x00, 0x01, 0x01, 0x02, 0x03, 0x01
};
static const uint8_t desc_cfg_ptp[39] = {
    0x09, 0x02, 0x27, 0x00, 0x01, 0x01, 0x00, 0x80, 0xFA,
    0x09, 0x04, 0x00, 0x00, 0x03, 0x06, 0x01, 0x01, 0x00,
    0x07, 0x05, 0x81, 0x02, 0x00, 0x02, 0x00,
    0x07, 0x05, 0x02, 0x02, 0x00, 0x02, 0x00,
    0x07, 0x05, 0x83, 0x03, 0x20, 0x00, 0x07
};

/* Mode 3: Composite UVC 1.1 + PTP 054C:0322 (E0154, E0210, E0258, E0347) */
static const uint8_t desc_dev_comp[18] = {
    0x12, 0x01, 0x00, 0x02, 0xEF, 0x02, 0x01, 0x40,
    0x4C, 0x05, 0x22, 0x03, 0x00, 0x01, 0x01, 0x02, 0x03, 0x01
};
static const uint8_t desc_cfg_comp[173] = {
    /* Configuration header (173 = 0x00AD, 3 interfaces) */
    0x09, 0x02, 0xAD, 0x00, 0x03, 0x01, 0x00, 0x80, 0xFA,
    /* Interface 0: Still Image PTP (06/01/01) + 0x81, 0x02, 0x83 */
    0x09, 0x04, 0x00, 0x00, 0x03, 0x06, 0x01, 0x01, 0x00,
    0x07, 0x05, 0x81, 0x02, 0x00, 0x02, 0x00,
    0x07, 0x05, 0x02, 0x02, 0x00, 0x02, 0x00,
    0x07, 0x05, 0x83, 0x03, 0x20, 0x00, 0x07,
    /* IAD: UVC Video Interface Collection (interfaces 1..2, class 0E/03) */
    0x08, 0x0B, 0x01, 0x02, 0x0E, 0x03, 0x00, 0x00,
    /* Interface 1: UVC VideoControl (0E/01) + class-specific VC descriptors */
    0x09, 0x04, 0x01, 0x00, 0x00, 0x0E, 0x01, 0x00, 0x00,
    0x0D, 0x24, 0x01, 0x00, 0x01, 0x28, 0x00, 0x00, 0x6C, 0xDC, 0x02, 0x01, 0x02,
    0x12, 0x24, 0x02, 0x01, 0x01, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x03, 0x00, 0x00, 0x00,
    0x09, 0x24, 0x03, 0x02, 0x01, 0x01, 0x00, 0x01, 0x00,
    /* Interface 2: UVC VideoStreaming (0E/02) + MJPEG 1280x720@30 + Bulk-IN 0x84 */
    0x09, 0x04, 0x02, 0x00, 0x01, 0x0E, 0x02, 0x00, 0x00,
    0x0E, 0x24, 0x01, 0x01, 0x3D, 0x00, 0x84, 0x00, 0x02, 0x00, 0x00, 0x00, 0x01, 0x00,
    0x0B, 0x24, 0x06, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
    0x1E, 0x24, 0x07, 0x01, 0x00, 0x00, 0x05, 0xD0, 0x02, 0x00,
    0x00, 0x5E, 0x1A, 0x00, 0x00, 0x5E, 0x1A, 0x00, 0x00, 0xE1, 0x00,
    0x15, 0x16, 0x05, 0x00, 0x01, 0x15, 0x16, 0x05, 0x00,
    0x06, 0x24, 0x0D, 0x01, 0x01, 0x04,
    0x07, 0x05, 0x84, 0x02, 0x00, 0x02, 0x00
};

/* Mode 4: Bus-Power / HID 054C:096F (exact E0598 bytes + 19-byte HID report) */
static const uint8_t desc_dev_hid[18] = {
    0x12, 0x01, 0x00, 0x02, 0x00, 0x00, 0x00, 0x40,
    0x4C, 0x05, 0x6F, 0x09, 0x00, 0x01, 0x01, 0x02, 0x03, 0x01
};
static const uint8_t desc_cfg_hid[34] = {
    0x09, 0x02, 0x22, 0x00, 0x01, 0x01, 0x00, 0x80, 0xFA,
    0x09, 0x04, 0x00, 0x00, 0x01, 0x03, 0x00, 0x00, 0x00,
    0x09, 0x21, 0x11, 0x01, 0x00, 0x01, 0x22, 0x13, 0x00,
    0x07, 0x05, 0x81, 0x03, 0x20, 0x00, 0x01
};
static const uint8_t desc_hid_report[19] = {
    0x05, 0x01,  /* Usage Page (Generic Desktop) */
    0x09, 0x00,  /* Usage (Undefined) */
    0xA1, 0x01,  /* Collection (Application) */
    0x09, 0x01,  /*   Usage (Vendor 1) */
    0x15, 0x00,  /*   Logical Minimum (0) */
    0x25, 0x7F,  /*   Logical Maximum (127) */
    0x75, 0x08,  /*   Report Size (8) */
    0x95, 0x08,  /*   Report Count (8) */
    0x81, 0x02,  /*   Input (Data, Variable, Absolute) */
    0xC0         /* End Collection */
};

/* Mode 5: Jiritsu Service 054C:0336 (Vendor FF/00/00, E0525/E0643) */
static const uint8_t desc_dev_jiritsu[18] = {
    0x12, 0x01, 0x00, 0x02, 0x00, 0x00, 0x00, 0x40,
    0x4C, 0x05, 0x36, 0x03, 0x00, 0x01, 0x01, 0x02, 0x03, 0x01
};
static const uint8_t desc_cfg_jiritsu[32] = {
    0x09, 0x02, 0x20, 0x00, 0x01, 0x01, 0x00, 0xC0, 0xFA,
    0x09, 0x04, 0x00, 0x00, 0x02, 0xFF, 0x00, 0x00, 0x00,
    0x07, 0x05, 0x81, 0x02, 0x00, 0x02, 0x00,
    0x07, 0x05, 0x02, 0x02, 0x00, 0x02, 0x00
};

/* Standard USB String descriptors */
static const uint8_t desc_str_lang[4] = { 0x04, 0x03, 0x09, 0x04 };
static const uint8_t desc_str_mfg[10] = {
    10, 0x03, 'S', 0, 'o', 0, 'n', 0, 'y', 0
};
static const uint8_t desc_str_prod[20] = {
    20, 0x03,
    'I', 0, 'L', 0, 'C', 0, 'E', 0, '-', 0, '6', 0, '4', 0, '0', 0, '0', 0
};
static const uint8_t desc_str_ser[18] = {
    18, 0x03,
    '0', 0, '0', 0, '0', 0, '0', 0, '0', 0, '0', 0, '0', 0, '1', 0
};

/* -------------------------------------------------------------------------
 * Firmware State
 * ------------------------------------------------------------------------- */

enum Ep0Stage {
    EP0_STAGE_SETUP = 0,
    EP0_STAGE_DATA_IN = 1,
    EP0_STAGE_STATUS3_OUT = 2,
    EP0_STAGE_DATA_OUT = 3,
    EP0_STAGE_STATUS3_IN = 4,
    EP0_STAGE_STATUS2_IN = 5,
};

enum BotState {
    BOT_IDLE = 0,
    BOT_DATA_OUT = 1,
    BOT_STALLED_IN = 2,
};

enum PtpRxState {
    PTP_RX_CMD = 0,
    PTP_RX_DATA = 1,
};

static uint32_t g_gevnt_tail;
static uint8_t g_config_value;
static enum Ep0Stage g_ep0_stage;
static uint8_t g_ep0_req_type;
static uint8_t g_ep0_req;
static uint16_t g_ep0_wvalue;
static uint16_t g_ep0_windex;
static uint16_t g_ep0_wlength;
static uint32_t g_ep4_armed_bytes;

/* MSC state */
static enum BotState g_bot_state;
static uint32_t g_bot_tag;
static uint32_t g_bot_lba;
static uint32_t g_bot_blocks;
static uint32_t g_bot_xfer_len;
static int g_bot_wp_reject;
static uint8_t g_sense_key;
static uint8_t g_sense_asc;
static uint8_t g_sense_ascq;
static uint8_t g_pending_csw[13];
static int g_has_pending_csw;

/* PTP state */
static enum PtpRxState g_ptp_rx_state;
static int g_ptp_session_open;
static uint32_t g_ptp_session_id;
static uint32_t g_ptp_sdio_phase;
static uint16_t g_ptp_pending_opcode;
static uint32_t g_ptp_pending_tx;
static uint32_t g_ptp_pending_param0;
static uint32_t g_ptp_last_canceled_tx;
static uint32_t g_ptp_lv_seq;

/* UVC state */
static uint8_t g_uvc_probe_ctrl[26];
static uint8_t g_uvc_commit_ctrl[26];
static int g_uvc_streaming;
static uint8_t g_uvc_fid;
static uint32_t g_uvc_pts;

/* HID state */
static uint8_t g_hid_seq;

/* Synthetic valid 1280x720 baseline JPEG (built once in RAM) */
static uint8_t g_jpeg_buf[1120];
static uint32_t g_jpeg_len;

/* -------------------------------------------------------------------------
 * DWC3 Low-Level Helpers
 * ------------------------------------------------------------------------- */

static void dwc3_ep_cmd(uint32_t phys_ep, uint32_t cmd,
                        uint32_t par0, uint32_t par1, uint32_t par2)
{
    uint32_t base = (uint32_t)REG_EP_BASE + (phys_ep << 4);
    dwc3_write(base + 0x0U, par2);
    dwc3_write(base + 0x4U, par1);
    dwc3_write(base + 0x8U, par0);
    dwc3_write(base + 0xCU, cmd | DEPCMD_CMDACT);
}

static void dwc3_config_ep(uint32_t phys_ep, uint32_t ep_type, uint32_t mps)
{
    uint32_t par0 = (mps << 3) | (ep_type << 1);
    uint32_t par1 = (phys_ep << 25) | (1U << 10) | (1U << 8);
    dwc3_ep_cmd(phys_ep, DEPCMD_SETEPCONFIG, par0, par1, 0U);
    dwc3_ep_cmd(phys_ep, DEPCMD_SETTRANSFRES, 1U, 0U, 0U);
}

static void dwc3_arm_single_trb(uint32_t phys_ep, uintptr_t trb_pa,
                                uintptr_t buf_pa, uint32_t len,
                                uint32_t trbctl)
{
    volatile Dwc3Trb *trb = (volatile Dwc3Trb *)trb_pa;
    trb->bpl = (uint32_t)buf_pa;
    trb->bph = 0U;
    trb->size = len & 0x00FFFFFFU;
    trb->ctrl = TRB_CTRL_HWO | TRB_CTRL_LST | (trbctl << 4) | TRB_CTRL_IOC;
    dmb_barrier();
    dwc3_ep_cmd(phys_ep, DEPCMD_STARTTRANSFER, 0U, (uint32_t)trb_pa, 0U);
}

static void dwc3_ep_set_stall(uint32_t phys_ep)
{
    dwc3_ep_cmd(phys_ep, DEPCMD_SETSTALL, 0U, 0U, 0U);
}

static void dwc3_ep_clear_stall(uint32_t phys_ep)
{
    dwc3_ep_cmd(phys_ep, DEPCMD_CLEARSTALL, 0U, 0U, 0U);
}

static void dwc3_ep_end_transfer(uint32_t phys_ep)
{
    dwc3_ep_cmd(phys_ep, DEPCMD_ENDTRANSFER, 0U, 0U, 0U);
}

static void ep0_arm_setup(void)
{
    g_ep0_stage = EP0_STAGE_SETUP;
    dwc3_arm_single_trb(0U, TRB_EP0_PA, BUF_EP0_SETUP_PA, 8U, TRBCTL_CONTROL_SETUP);
}

static void ep4_arm_out(uint32_t len)
{
    g_ep4_armed_bytes = len;
    dwc3_arm_single_trb(4U, TRB_EP4_PA, BUF_EP4_OUT_PA, len, TRBCTL_NORMAL);
}

/* Arm multi-packet Bulk-IN payload on EP3 (512B chunks), optionally appending a
 * separate trailing packet (such as a 12-byte PTP Response container). */
static void ep3_arm_multi(uintptr_t data_pa, uint32_t data_len,
                          uintptr_t tail_pa, uint32_t tail_len,
                          int use_link_trb)
{
    volatile Dwc3Trb *trbs = (volatile Dwc3Trb *)TRB_EP3_PA;
    uint32_t idx = 0U;
    uint32_t off = 0U;

    while (off < data_len || (data_len == 0U && idx == 0U && tail_len == 0U)) {
        uint32_t rem = data_len - off;
        uint32_t chunk = rem > 512U ? 512U : rem;
        int is_last = (off + chunk >= data_len) && (tail_len == 0U);

        if (use_link_trb && idx == 1U) {
            /* Exercise TRBCTL_LINK=8 hopping from TRB[1] -> TRB[4] */
            trbs[1].bpl = (uint32_t)(TRB_EP3_PA + 4U * sizeof(Dwc3Trb));
            trbs[1].bph = 0U;
            trbs[1].size = 0U;
            trbs[1].ctrl = TRB_CTRL_HWO | (TRBCTL_LINK << 4);
            idx = 4U;
        }

        trbs[idx].bpl = (uint32_t)(data_pa + off);
        trbs[idx].bph = 0U;
        trbs[idx].size = chunk;
        trbs[idx].ctrl = TRB_CTRL_HWO | (TRBCTL_NORMAL << 4) | TRB_CTRL_IOC |
                         (is_last ? TRB_CTRL_LST : 0U);
        idx++;
        off += chunk;
        if (data_len == 0U) {
            break;
        }
    }

    if (tail_len > 0U) {
        trbs[idx].bpl = (uint32_t)tail_pa;
        trbs[idx].bph = 0U;
        trbs[idx].size = tail_len;
        trbs[idx].ctrl = TRB_CTRL_HWO | TRB_CTRL_LST |
                         (TRBCTL_NORMAL << 4) | TRB_CTRL_IOC;
    }

    dmb_barrier();
    dwc3_ep_cmd(3U, DEPCMD_STARTTRANSFER, 0U, (uint32_t)TRB_EP3_PA, 0U);
}

/* -------------------------------------------------------------------------
 * Synthetic Valid Baseline JPEG Builder & UVC Frame Arming
 * ------------------------------------------------------------------------- */

static void build_synthetic_jpeg(void)
{
    static const uint8_t hdr[] = {
        /* SOI + APP0 JFIF */
        0xFF, 0xD8,
        0xFF, 0xE0, 0x00, 0x10, 'J', 'F', 'I', 'F', 0x00,
        0x01, 0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00,
        /* DQT (64 bytes, table 0) */
        0xFF, 0xDB, 0x00, 0x43, 0x00,
        16, 11, 10, 16, 24, 40, 51, 61,
        12, 12, 14, 19, 26, 58, 60, 55,
        14, 13, 16, 24, 40, 57, 69, 56,
        14, 17, 22, 29, 51, 87, 80, 62,
        18, 22, 37, 56, 68, 109, 103, 77,
        24, 35, 55, 64, 81, 104, 113, 92,
        49, 64, 78, 87, 103, 121, 120, 101,
        72, 92, 95, 98, 112, 100, 103, 99,
        /* SOF0: 8-bit, height 720 (0x02D0), width 1280 (0x0500), 1 component */
        0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x02, 0xD0, 0x05, 0x00,
        0x01, 0x01, 0x11, 0x00,
        /* Minimal DHT (DC table 0) */
        0xFF, 0xC4, 0x00, 0x15, 0x00,
        0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x01,
        /* SOS */
        0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01, 0x00, 0x00, 0x3F, 0x00
    };
    uint32_t pos = (uint32_t)sizeof(hdr);
    memcpy(g_jpeg_buf, hdr, pos);

    /* Fill entropy scan bytes (avoiding 0xFF marker bytes) so total > 1024 B */
    while (pos < 1098U) {
        g_jpeg_buf[pos] = (uint8_t)(0x21U + (pos & 0x3FU));
        pos++;
    }
    g_jpeg_buf[pos++] = 0xFFU;
    g_jpeg_buf[pos++] = 0xD9U;  /* EOI */
    g_jpeg_len = pos;           /* 1100 bytes */
}

static void init_uvc_default_controls(void)
{
    memset(g_uvc_probe_ctrl, 0, sizeof(g_uvc_probe_ctrl));
    put_le16(&g_uvc_probe_ctrl[0], 0x0001U);         /* bmHint */
    g_uvc_probe_ctrl[2] = 1U;                        /* bFormatIndex */
    g_uvc_probe_ctrl[3] = 1U;                        /* bFrameIndex */
    put_le32(&g_uvc_probe_ctrl[4], 333333U);         /* dwFrameInterval (30fps) */
    put_le32(&g_uvc_probe_ctrl[18], 1280U * 720U * 2U); /* dwMaxVideoFrameSize */
    put_le32(&g_uvc_probe_ctrl[22], 16384U);         /* dwMaxPayloadTransferSize */
    memcpy(g_uvc_commit_ctrl, g_uvc_probe_ctrl, sizeof(g_uvc_probe_ctrl));
}

static void uvc_arm_next_mjpeg_frame(void)
{
    volatile Dwc3Trb *trbs = (volatile Dwc3Trb *)TRB_EP9_PA;
    uint8_t *dst = (uint8_t *)BUF_EP9_UVC_PA;
    uint32_t src_off = 0U;
    uint32_t dst_off = 0U;
    uint32_t trb_idx = 0U;

    while (src_off < g_jpeg_len) {
        uint32_t rem = g_jpeg_len - src_off;
        uint32_t body_len = rem > 500U ? 500U : rem;
        int is_eof = (src_off + body_len >= g_jpeg_len);
        uint8_t *pkt = dst + dst_off;

        /* 12-byte UVC 1.1 Uncompressed/MJPEG Payload Header */
        pkt[0] = 12U;  /* bHeaderLength */
        pkt[1] = 0x80U | 0x04U | 0x08U | (g_uvc_fid & 1U) | (is_eof ? 0x02U : 0U);
        put_le32(&pkt[2], g_uvc_pts);
        put_le32(&pkt[6], g_uvc_pts);
        put_le16(&pkt[10], (uint16_t)(trb_idx & 0x7FFU));
        memcpy(&pkt[12], &g_jpeg_buf[src_off], body_len);

        trbs[trb_idx].bpl = (uint32_t)(BUF_EP9_UVC_PA + dst_off);
        trbs[trb_idx].bph = 0U;
        trbs[trb_idx].size = 12U + body_len;
        trbs[trb_idx].ctrl = TRB_CTRL_HWO | (TRBCTL_NORMAL << 4) | TRB_CTRL_IOC |
                             (is_eof ? TRB_CTRL_LST : 0U);

        src_off += body_len;
        dst_off += 512U;
        trb_idx++;
    }

    dmb_barrier();
    dwc3_ep_cmd(9U, DEPCMD_STARTTRANSFER, 0U, (uint32_t)TRB_EP9_PA, 0U);
}

/* -------------------------------------------------------------------------
 * Mode 4: HID Input Report Arming
 * ------------------------------------------------------------------------- */

static void hid_arm_next_report(void)
{
    uint8_t *buf = (uint8_t *)BUF_EP3_IN_PA;
    buf[0] = 'B';
    buf[1] = 'P';
    buf[2] = 'W';
    buf[3] = 'R';
    buf[4] = ++g_hid_seq;
    buf[5] = 0x01U;
    buf[6] = 0x6FU;
    buf[7] = 0x09U;
    dwc3_arm_single_trb(3U, TRB_EP3_PA, BUF_EP3_IN_PA, 8U, TRBCTL_NORMAL);
}

/* -------------------------------------------------------------------------
 * Mode 1: MSC Bulk-Only Transport & 64-Sector RAM Disk
 * ------------------------------------------------------------------------- */

static void init_ramdisk(void)
{
    uint8_t *disk = (uint8_t *)RAMDISK_PA;
    for (uint32_t s = 0U; s < RAMDISK_SECTORS; s++) {
        uint8_t *sec = disk + (s << 9);
        memset(sec, 0, RAMDISK_SECTOR_SIZE);
        sec[0] = 'S';
        sec[1] = 'O';
        sec[2] = 'N';
        sec[3] = 'Y';
        put_le32(&sec[4], s);
        for (uint32_t i = 8U; i < RAMDISK_SECTOR_SIZE; i++) {
            sec[i] = (uint8_t)((s * 17U + i) & 0xFFU);
        }
    }
}

static void msc_send_csw(uint32_t tag, uint32_t residue, uint8_t status)
{
    uint8_t *csw = (uint8_t *)BUF_EP3_RESP_PA;
    put_le32(&csw[0], 0x53425355U);  /* 'USBS' */
    put_le32(&csw[4], tag);
    put_le32(&csw[8], residue);
    csw[12] = status;
    dwc3_arm_single_trb(3U, TRB_EP3_PA, BUF_EP3_RESP_PA, 13U, TRBCTL_NORMAL);
}

static void msc_send_data_and_csw(const uint8_t *data, uint32_t data_len,
                                  uint32_t tag, uint32_t residue, uint8_t status)
{
    uint8_t *dst = (uint8_t *)BUF_EP3_IN_PA;
    uint8_t *csw = (uint8_t *)BUF_EP3_RESP_PA;
    if (data != NULL && data != dst && data_len > 0U) {
        memcpy(dst, data, data_len);
    }
    put_le32(&csw[0], 0x53425355U);
    put_le32(&csw[4], tag);
    put_le32(&csw[8], residue);
    csw[12] = status;
    ep3_arm_multi(BUF_EP3_IN_PA, data_len, BUF_EP3_RESP_PA, 13U, 0);
}

static void msc_stall_with_sense(uint32_t tag, uint32_t residue,
                                 uint8_t key, uint8_t asc, uint8_t ascq)
{
    g_sense_key = key;
    g_sense_asc = asc;
    g_sense_ascq = ascq;
    put_le32(&g_pending_csw[0], 0x53425355U);
    put_le32(&g_pending_csw[4], tag);
    put_le32(&g_pending_csw[8], residue);
    g_pending_csw[12] = 1U;
    g_has_pending_csw = 1;
    g_bot_state = BOT_STALLED_IN;
    dwc3_ep_set_stall(3U);
}

static void msc_handle_cbw(UsbFwMailbox *mbox, uint32_t rx_len)
{
    const uint8_t *cbw = (const uint8_t *)BUF_EP4_OUT_PA;
    uint8_t *tx = (uint8_t *)BUF_EP3_IN_PA;

    if (rx_len != 31U || get_le32(&cbw[0]) != 0x43425355U) {
        g_bot_state = BOT_STALLED_IN;
        dwc3_ep_set_stall(3U);
        dwc3_ep_set_stall(4U);
        return;
    }

    uint32_t tag = get_le32(&cbw[4]);
    uint32_t xfer_len = get_le32(&cbw[8]);
    uint8_t cb_len = cbw[14] & 0x1FU;
    const uint8_t *cdb = &cbw[15];
    uint8_t op = cdb[0];

    if (cb_len == 0U) {
        msc_stall_with_sense(tag, xfer_len, 0x05U, 0x20U, 0x00U);
        return;
    }

    switch (op) {
    case 0x00U: /* TEST UNIT READY */
        g_sense_key = 0U;
        g_sense_asc = 0U;
        g_sense_ascq = 0U;
        msc_send_csw(tag, 0U, 0U);
        ep4_arm_out(512U);
        break;

    case 0x03U: { /* REQUEST SENSE */
        uint32_t len = xfer_len < 18U ? xfer_len : 18U;
        memset(tx, 0, 18U);
        tx[0] = 0x70U;
        tx[2] = g_sense_key;
        tx[7] = 10U;
        tx[12] = g_sense_asc;
        tx[13] = g_sense_ascq;
        msc_send_data_and_csw(tx, len, tag, xfer_len - len, 0U);
        g_sense_key = 0U;
        g_sense_asc = 0U;
        g_sense_ascq = 0U;
        ep4_arm_out(512U);
        break;
    }

    case 0x12U: { /* INQUIRY (Sony / DSC / 1.00) */
        uint32_t len = xfer_len < 36U ? xfer_len : 36U;
        memset(tx, 0, 36U);
        tx[0] = 0x00U;  /* Direct-access block device */
        tx[1] = 0x80U;  /* Removable */
        tx[2] = 0x04U;  /* SPC-2 */
        tx[3] = 0x02U;
        tx[4] = 31U;
        memcpy(&tx[8],  "Sony    ", 8U);
        memcpy(&tx[16], "DSC             ", 16U);
        memcpy(&tx[32], "1.00", 4U);
        msc_send_data_and_csw(tx, len, tag, xfer_len - len, 0U);
        ep4_arm_out(512U);
        break;
    }

    case 0x1AU: { /* MODE SENSE(6) */
        uint32_t len = xfer_len < 4U ? xfer_len : 4U;
        int wp = (mbox->flags & MBOX_FLAG_MSC_WP) != 0U;
        tx[0] = 0x03U;
        tx[1] = 0x00U;
        tx[2] = wp ? 0x80U : 0x00U;
        tx[3] = 0x00U;
        msc_send_data_and_csw(tx, len, tag, xfer_len - len, 0U);
        ep4_arm_out(512U);
        break;
    }

    case 0x25U: { /* READ CAPACITY(10) */
        put_be32(&tx[0], RAMDISK_SECTORS - 1U);
        put_be32(&tx[4], RAMDISK_SECTOR_SIZE);
        msc_send_data_and_csw(tx, 8U, tag, xfer_len > 8U ? xfer_len - 8U : 0U, 0U);
        ep4_arm_out(512U);
        break;
    }

    case 0x28U: { /* READ(10) */
        uint32_t lba = get_be32(&cdb[2]);
        uint16_t blocks = get_be16(&cdb[7]);
        uint32_t byte_len = (uint32_t)blocks << 9;
        if (lba + blocks > RAMDISK_SECTORS || byte_len != xfer_len) {
            msc_stall_with_sense(tag, xfer_len, 0x05U, 0x21U, 0x00U);
            return;
        }
        const uint8_t *src = (const uint8_t *)(RAMDISK_PA + (lba << 9));
        msc_send_data_and_csw(src, byte_len, tag, 0U, 0U);
        ep4_arm_out(512U);
        break;
    }

    case 0x2AU: { /* WRITE(10) - single multi-packet Bulk-OUT TRB */
        uint32_t lba = get_be32(&cdb[2]);
        uint16_t blocks = get_be16(&cdb[7]);
        uint32_t byte_len = (uint32_t)blocks << 9;
        if (lba + blocks > RAMDISK_SECTORS || byte_len == 0U ||
            byte_len > 32768U || byte_len != xfer_len) {
            g_sense_key = 0x05U;
            g_sense_asc = 0x21U;
            g_sense_ascq = 0x00U;
            g_bot_state = BOT_STALLED_IN;
            dwc3_ep_set_stall(4U);
            dwc3_ep_set_stall(3U);
            return;
        }
        g_bot_tag = tag;
        g_bot_lba = lba;
        g_bot_blocks = blocks;
        g_bot_xfer_len = byte_len;
        g_bot_wp_reject = (mbox->flags & MBOX_FLAG_MSC_WP) ? 1 : 0;
        g_bot_state = BOT_DATA_OUT;
        /* Arm ONE byte_len TRB (e.g. 2048 B) to accumulate all 512B packets */
        ep4_arm_out(byte_len);
        break;
    }

    case 0xFFU:
    case 0x7AU: { /* Read-only Sony vendor GetModelInfo query (E0509/E0646) */
        if (xfer_len < 64U) {
            msc_stall_with_sense(tag, xfer_len, 0x05U, 0x24U, 0x00U);
            return;
        }
        uint32_t send_len = xfer_len > 8192U ? 8192U : xfer_len;
        memset(tx, 0, send_len);
        /* 16-byte Sony extcmd header: size=27, command=1, direction=1 */
        put_le32(&tx[0], 27U);
        put_le16(&tx[4], 1U);
        put_le16(&tx[6], 1U);
        /* Payload: plist_size=0, flags=0, model_len=9, "ILCE-6400", code 0032858730 */
        put_le32(&tx[16], 0U);
        put_le32(&tx[20], 0U);
        tx[24] = 9U;
        memcpy(&tx[25], "ILCE-6400", 9U);
        tx[34] = 0x00U;
        tx[35] = 0x32U;
        tx[36] = 0x85U;
        tx[37] = 0x87U;
        tx[38] = 0x30U;
        msc_send_data_and_csw(tx, send_len, tag, xfer_len - send_len, 0U);
        ep4_arm_out(512U);
        break;
    }

    default:
        /* Unsupported CDB: stall Bulk-IN and latch ILLEGAL REQUEST sense */
        msc_stall_with_sense(tag, xfer_len, 0x05U, 0x20U, 0x00U);
        break;
    }
}

static void msc_handle_data_out_done(uint32_t rx_len)
{
    g_bot_state = BOT_IDLE;
    if (g_bot_wp_reject) {
        /* Write-protected LUN: do NOT modify RAM disk; report DATA PROTECT */
        g_sense_key = 0x07U;   /* DATA PROTECT */
        g_sense_asc = 0x27U;   /* WRITE PROTECTED */
        g_sense_ascq = 0x00U;
        msc_send_csw(g_bot_tag, g_bot_xfer_len, 1U);
        ep4_arm_out(512U);
        return;
    }
    if (rx_len == g_bot_xfer_len) {
        uint8_t *dst = (uint8_t *)(RAMDISK_PA + (g_bot_lba << 9));
        memcpy(dst, (const void *)BUF_EP4_OUT_PA, rx_len);
        msc_send_csw(g_bot_tag, 0U, 0U);
    } else {
        msc_send_csw(g_bot_tag, g_bot_xfer_len - rx_len, 1U);
    }
    ep4_arm_out(512U);
}

/* -------------------------------------------------------------------------
 * Mode 2 & Mode 3: Sony PC Remote / PTP Engine
 * ------------------------------------------------------------------------- */

static uint32_t put_ptp_string(uint8_t *dst, const char *ascii)
{
    uint32_t n = 0U;
    while (ascii[n] != '\0') {
        n++;
    }
    if (n == 0U) {
        dst[0] = 0U;
        return 1U;
    }
    dst[0] = (uint8_t)(n + 1U);
    for (uint32_t i = 0U; i < n; i++) {
        dst[1U + (i << 1)] = (uint8_t)ascii[i];
        dst[2U + (i << 1)] = 0U;
    }
    dst[1U + (n << 1)] = 0U;
    dst[2U + (n << 1)] = 0U;
    return 1U + ((n + 1U) << 1);
}

static uint32_t build_ptp_device_info(uint8_t *out)
{
    static const uint16_t ops[] = {
        0x1001U, 0x1002U, 0x1003U, 0x1004U, 0x1009U, 0x9201U, 0x9202U, 0x9205U
    };
    static const uint16_t evs[] = { 0x4002U, 0x4006U, 0xC201U };
    static const uint16_t props[] = { 0xD25AU, 0xD2C1U, 0xD2C2U };
    static const uint16_t fmts[] = { 0x3801U };
    uint32_t p = 0U;

    put_le16(&out[p], 100U); p += 2U;        /* StandardVersion = 1.00 */
    put_le32(&out[p], 0x00000011U); p += 4U; /* VendorExtensionID = Sony */
    put_le16(&out[p], 100U); p += 2U;        /* VendorExtensionVersion */
    p += put_ptp_string(&out[p], "Sony PTP Extensions");
    put_le16(&out[p], 0U); p += 2U;          /* FunctionalMode */

    put_le32(&out[p], (uint32_t)(sizeof(ops) / 2U)); p += 4U;
    for (uint32_t i = 0U; i < sizeof(ops) / 2U; i++) {
        put_le16(&out[p], ops[i]); p += 2U;
    }
    put_le32(&out[p], (uint32_t)(sizeof(evs) / 2U)); p += 4U;
    for (uint32_t i = 0U; i < sizeof(evs) / 2U; i++) {
        put_le16(&out[p], evs[i]); p += 2U;
    }
    put_le32(&out[p], (uint32_t)(sizeof(props) / 2U)); p += 4U;
    for (uint32_t i = 0U; i < sizeof(props) / 2U; i++) {
        put_le16(&out[p], props[i]); p += 2U;
    }
    put_le32(&out[p], 1U); p += 4U;
    put_le16(&out[p], fmts[0]); p += 2U;
    put_le32(&out[p], 1U); p += 4U;
    put_le16(&out[p], fmts[0]); p += 2U;

    p += put_ptp_string(&out[p], "Sony Corporation");
    p += put_ptp_string(&out[p], "ILCE-6400");
    p += put_ptp_string(&out[p], "2.00");
    p += put_ptp_string(&out[p], "");  /* Omit serial number */
    return p;
}

static void ptp_send_response_only(uint16_t rc, uint32_t tx_id)
{
    uint8_t *resp = (uint8_t *)BUF_EP3_RESP_PA;
    put_le32(&resp[0], 12U);
    put_le16(&resp[4], 3U);
    put_le16(&resp[6], rc);
    put_le32(&resp[8], tx_id);
    dwc3_arm_single_trb(3U, TRB_EP3_PA, BUF_EP3_RESP_PA, 12U, TRBCTL_NORMAL);
}

static void ptp_send_data_and_response(uint16_t opcode, uint32_t tx_id,
                                       uint32_t payload_len, uint16_t rc,
                                       int use_link_trb)
{
    uint8_t *hdr = (uint8_t *)BUF_EP3_IN_PA;
    uint8_t *resp = (uint8_t *)BUF_EP3_RESP_PA;
    uint32_t total_data = 12U + payload_len;

    put_le32(&hdr[0], total_data);
    put_le16(&hdr[4], 2U);
    put_le16(&hdr[6], opcode);
    put_le32(&hdr[8], tx_id);

    put_le32(&resp[0], 12U);
    put_le16(&resp[4], 3U);
    put_le16(&resp[6], rc);
    put_le32(&resp[8], tx_id);

    ep3_arm_multi(BUF_EP3_IN_PA, total_data, BUF_EP3_RESP_PA, 12U, use_link_trb);
}

static void ptp_queue_interrupt_event(uint16_t event_code, uint32_t tx_id,
                                      uint32_t param1)
{
    uint8_t *ev = (uint8_t *)BUF_EP7_INTR_PA;
    put_le32(&ev[0], 16U);
    put_le16(&ev[4], 4U);  /* PTP_CONTAINER_EVENT */
    put_le16(&ev[6], event_code);
    put_le32(&ev[8], tx_id);
    put_le32(&ev[12], param1);
    dwc3_arm_single_trb(7U, TRB_EP7_PA, BUF_EP7_INTR_PA, 16U, TRBCTL_NORMAL);
}

static void ptp_handle_out(UsbFwMailbox *mbox, uint32_t rx_len)
{
    const uint8_t *pkt = (const uint8_t *)BUF_EP4_OUT_PA;
    uint8_t *payload = (uint8_t *)(BUF_EP3_IN_PA + 12U);

    if (g_ptp_rx_state == PTP_RX_DATA) {
        g_ptp_rx_state = PTP_RX_CMD;
        if (rx_len >= 14U && get_le16(&pkt[4]) == 2U &&
            get_le16(&pkt[6]) == g_ptp_pending_opcode) {
            /* Queue ObjectAdded (0x4002) on Interrupt-IN 0x83 (phys_ep 7) */
            ptp_queue_interrupt_event(0x4002U, g_ptp_pending_tx, 0xFFFFC001U);
            ptp_send_response_only(0x2001U, g_ptp_pending_tx);
            mbox->ptp_ops_count++;
        } else {
            ptp_send_response_only(0x2002U, g_ptp_pending_tx);
        }
        ep4_arm_out(512U);
        return;
    }

    if (rx_len < 12U || get_le16(&pkt[4]) != 1U) {
        ep4_arm_out(512U);
        return;
    }

    uint32_t cmd_len = get_le32(&pkt[0]);
    uint16_t opcode = get_le16(&pkt[6]);
    uint32_t tx_id = get_le32(&pkt[8]);
    uint32_t p0 = (cmd_len >= 16U && rx_len >= 16U) ? get_le32(&pkt[12]) : 0U;

    switch (opcode) {
    case 0x1001U: { /* GetDeviceInfo */
        uint32_t len = build_ptp_device_info(payload);
        ptp_send_data_and_response(opcode, tx_id, len, 0x2001U, 0);
        mbox->ptp_ops_count++;
        ep4_arm_out(512U);
        break;
    }

    case 0x1002U: /* OpenSession */
        if (g_ptp_session_open) {
            ptp_send_response_only(0x201EU, tx_id);  /* SessionAlreadyOpen */
        } else {
            g_ptp_session_open = 1;
            g_ptp_session_id = p0;
            ptp_send_response_only(0x2001U, tx_id);
        }
        mbox->ptp_ops_count++;
        ep4_arm_out(512U);
        break;

    case 0x1003U: /* CloseSession */
        if (!g_ptp_session_open) {
            ptp_send_response_only(0x2003U, tx_id);  /* SessionNotOpen */
        } else {
            g_ptp_session_open = 0;
            g_ptp_sdio_phase = 0U;
            ptp_send_response_only(0x2001U, tx_id);
        }
        mbox->ptp_ops_count++;
        ep4_arm_out(512U);
        break;

    case 0x1004U: /* GetStorageIDs */
        put_le32(&payload[0], 1U);
        put_le32(&payload[4], 0x00010001U);
        ptp_send_data_and_response(opcode, tx_id, 8U, 0x2001U, 0);
        mbox->ptp_ops_count++;
        ep4_arm_out(512U);
        break;

    case 0x9201U: /* Sony SDIO_Connect (phases 1, 2, 3) */
        if (!g_ptp_session_open) {
            ptp_send_response_only(0x2003U, tx_id);
        } else if (p0 < 1U || p0 > 3U) {
            ptp_send_response_only(0x201DU, tx_id);
        } else {
            g_ptp_sdio_phase = p0;
            ptp_send_response_only(0x2001U, tx_id);
        }
        mbox->ptp_ops_count++;
        ep4_arm_out(512U);
        break;

    case 0x9202U: { /* Sony SDIO_GetExtDeviceInfo */
        static const uint16_t ext_props[] = {
            0xD25AU, 0xD2C1U, 0xD2C2U, 0xD20DU, 0xD21DU
        };
        uint16_t ver = (p0 != 0U && p0 <= 0xFFFFU) ? (uint16_t)p0 : 0x012CU;
        put_le16(&payload[0], ver);
        put_le32(&payload[2], 5U);
        for (uint32_t i = 0U; i < 5U; i++) {
            put_le16(&payload[6U + (i << 1)], ext_props[i]);
        }
        ptp_send_data_and_response(opcode, tx_id, 16U, 0x2001U, 0);
        mbox->ptp_ops_count++;
        ep4_arm_out(512U);
        break;
    }

    case 0x1009U: { /* GetObject (0xFFFFC002 LiveView multi-TRB + TRBCTL_LINK) */
        if (p0 != 0xFFFFC002U) {
            ptp_send_response_only(0x2009U, tx_id);  /* InvalidObjectHandle */
            ep4_arm_out(512U);
            break;
        }
        /* 136-byte Sony LiveView header + 1100-byte JPEG = 1236 bytes (+12B = 1248B) */
        memset(payload, 0, 136U);
        payload[0] = 0xFFU;
        payload[1] = 0x01U;  /* LiveView payload type */
        g_ptp_lv_seq++;
        payload[2] = (uint8_t)((g_ptp_lv_seq >> 8) & 0xFFU);
        payload[3] = (uint8_t)(g_ptp_lv_seq & 0xFFU);
        /* Payload header magic 0x24 0x35 0x68 0x79 at +8 */
        payload[8] = 0x24U;
        payload[9] = 0x35U;
        payload[10] = 0x68U;
        payload[11] = 0x79U;
        put_be32(&payload[12], g_jpeg_len);
        memcpy(&payload[136], g_jpeg_buf, g_jpeg_len);
        /* Send across 3 Bulk-IN TRBs with TRBCTL_LINK=8 hop on TRB[1] */
        ptp_send_data_and_response(opcode, tx_id, 136U + g_jpeg_len, 0x2001U, 1);
        mbox->ptp_ops_count++;
        ep4_arm_out(512U);
        break;
    }

    case 0x9205U: /* Sony SDIO_ControlDevice (followed by Data-OUT container) */
        g_ptp_pending_opcode = opcode;
        g_ptp_pending_tx = tx_id;
        g_ptp_pending_param0 = p0;
        g_ptp_rx_state = PTP_RX_DATA;
        ep4_arm_out(512U);
        break;

    default:
        ptp_send_response_only(0x2005U, tx_id);  /* OperationNotSupported */
        ep4_arm_out(512U);
        break;
    }
}

/* -------------------------------------------------------------------------
 * Mode 5: Jiritsu Service Vendor Bulk Handler (054C:0336)
 * ------------------------------------------------------------------------- */

static void jiritsu_handle_out(uint32_t rx_len)
{
    const uint8_t *req = (const uint8_t *)BUF_EP4_OUT_PA;
    uint8_t *rep = (uint8_t *)BUF_EP3_IN_PA;

    if (rx_len < 16U) {
        ep4_arm_out(512U);
        return;
    }
    uint32_t body_len = get_le32(&req[0]);
    uint16_t func = get_le16(&req[4]);
    uint16_t seq = get_le16(&req[6]);
    uint16_t category = get_le16(&req[12]);
    uint16_t cmd = get_le16(&req[14]) & 0x0FFFU;

    if (func != 0x40U || category != 0x1001U || 12U + body_len > rx_len) {
        put_le32(&rep[0], 4U);
        put_le16(&rep[4], 0x40U);
        put_le16(&rep[6], seq);
        rep[8] = 3U;  /* version 3 */
        rep[9] = 0U;
        rep[10] = 0U;
        rep[11] = 0U;
        put_le32(&rep[12], 0x80000001U);  /* non-zero error status */
        ep3_arm_multi(BUF_EP3_IN_PA, 16U, 0U, 0U, 0);
        ep4_arm_out(512U);
        return;
    }

    const uint8_t *arg = &req[16];
    uint32_t arg_len = body_len - 4U;

    if (cmd == 0x0002U) {
        /* Ping / version-echo */
        static const char prefix[] = "38117_03.2026041003:";
        uint32_t pfx_len = (uint32_t)(sizeof(prefix) - 1U);
        uint32_t data_len = pfx_len + arg_len;
        put_le32(&rep[0], 4U + data_len);
        put_le16(&rep[4], 0x40U);
        put_le16(&rep[6], seq);
        rep[8] = 3U;
        rep[9] = 0U;
        rep[10] = 0U;
        rep[11] = 0U;
        put_le32(&rep[12], 0U);  /* status OK */
        memcpy(&rep[16], prefix, pfx_len);
        if (arg_len > 0U) {
            memcpy(&rep[16U + pfx_len], arg, arg_len);
        }
        ep3_arm_multi(BUF_EP3_IN_PA, 16U + data_len, 0U, 0U, 0);
    } else if (cmd == 0x0008U && arg_len >= 8U) {
        /* Bounded RAM-disk chunk read */
        uint32_t off = get_le32(&arg[0]);
        uint32_t count = get_le32(&arg[4]);
        if (count == 0U || count > 32768U || off + count > RAMDISK_BYTES) {
            put_le32(&rep[0], 4U);
            put_le16(&rep[4], 0x40U);
            put_le16(&rep[6], seq);
            rep[8] = 3U;
            rep[9] = 0U;
            rep[10] = 0U;
            rep[11] = 0U;
            put_le32(&rep[12], 0x80000002U);
            ep3_arm_multi(BUF_EP3_IN_PA, 16U, 0U, 0U, 0);
        } else {
            put_le32(&rep[0], 4U + count);
            put_le16(&rep[4], 0x40U);
            put_le16(&rep[6], seq);
            rep[8] = 3U;
            rep[9] = 0U;
            rep[10] = 0U;
            rep[11] = 0U;
            put_le32(&rep[12], 0U);
            memcpy(&rep[16], (const void *)(RAMDISK_PA + off), count);
            ep3_arm_multi(BUF_EP3_IN_PA, 16U + count, 0U, 0U, 0);
        }
    } else {
        put_le32(&rep[0], 4U);
        put_le16(&rep[4], 0x40U);
        put_le16(&rep[6], seq);
        rep[8] = 3U;
        rep[9] = 0U;
        rep[10] = 0U;
        rep[11] = 0U;
        put_le32(&rep[12], 0x80000003U);
        ep3_arm_multi(BUF_EP3_IN_PA, 16U, 0U, 0U, 0);
    }

    ep4_arm_out(512U);
}

/* -------------------------------------------------------------------------
 * Mode Configuration & EP0 Standard / Class Request Dispatcher
 * ------------------------------------------------------------------------- */

static void get_mode_descriptors(uint32_t mode,
                                 const uint8_t **dev_desc, uint32_t *dev_len,
                                 const uint8_t **cfg_desc, uint32_t *cfg_len)
{
    switch (mode) {
    case USB_MODE_PTP:
        *dev_desc = desc_dev_ptp;
        *dev_len = sizeof(desc_dev_ptp);
        *cfg_desc = desc_cfg_ptp;
        *cfg_len = sizeof(desc_cfg_ptp);
        break;
    case USB_MODE_COMPOSITE:
        *dev_desc = desc_dev_comp;
        *dev_len = sizeof(desc_dev_comp);
        *cfg_desc = desc_cfg_comp;
        *cfg_len = sizeof(desc_cfg_comp);
        break;
    case USB_MODE_HID:
        *dev_desc = desc_dev_hid;
        *dev_len = sizeof(desc_dev_hid);
        *cfg_desc = desc_cfg_hid;
        *cfg_len = sizeof(desc_cfg_hid);
        break;
    case USB_MODE_JIRITSU:
        *dev_desc = desc_dev_jiritsu;
        *dev_len = sizeof(desc_dev_jiritsu);
        *cfg_desc = desc_cfg_jiritsu;
        *cfg_len = sizeof(desc_cfg_jiritsu);
        break;
    case USB_MODE_MSC:
    default:
        *dev_desc = desc_dev_msc;
        *dev_len = sizeof(desc_dev_msc);
        *cfg_desc = desc_cfg_msc;
        *cfg_len = sizeof(desc_cfg_msc);
        break;
    }
}

static void configure_mode_endpoints(uint32_t mode)
{
    uint32_t ena = 0x3U;  /* EP0 OUT + EP0 IN */

    /* Start resource configuration for non-EP0 endpoints (resource index 2) */
    dwc3_ep_cmd(0U, DEPCMD_STARTNEWCONFIG | (2U << 16), 0U, 0U, 0U);

    g_bot_state = BOT_IDLE;
    g_has_pending_csw = 0;
    g_ptp_rx_state = PTP_RX_CMD;
    g_uvc_streaming = 0;
    g_uvc_fid = 0U;
    g_uvc_pts = 1000U;

    if (mode == USB_MODE_MSC || mode == USB_MODE_JIRITSU) {
        dwc3_config_ep(3U, 2U, 512U);  /* 0x81 Bulk-IN */
        dwc3_config_ep(4U, 2U, 512U);  /* 0x02 Bulk-OUT */
        ena |= (1U << 3) | (1U << 4);
        dwc3_write(REG_DALEPENA, ena);
        ep4_arm_out(512U);
    } else if (mode == USB_MODE_PTP) {
        dwc3_config_ep(3U, 2U, 512U);  /* 0x81 Bulk-IN */
        dwc3_config_ep(4U, 2U, 512U);  /* 0x02 Bulk-OUT */
        dwc3_config_ep(7U, 3U, 32U);   /* 0x83 Interrupt-IN */
        ena |= (1U << 3) | (1U << 4) | (1U << 7);
        dwc3_write(REG_DALEPENA, ena);
        ep4_arm_out(512U);
    } else if (mode == USB_MODE_COMPOSITE) {
        dwc3_config_ep(3U, 2U, 512U);  /* 0x81 PTP Bulk-IN */
        dwc3_config_ep(4U, 2U, 512U);  /* 0x02 PTP Bulk-OUT */
        dwc3_config_ep(7U, 3U, 32U);   /* 0x83 PTP Interrupt-IN */
        dwc3_config_ep(9U, 2U, 512U);  /* 0x84 UVC Bulk-IN */
        ena |= (1U << 3) | (1U << 4) | (1U << 7) | (1U << 9);
        dwc3_write(REG_DALEPENA, ena);
        ep4_arm_out(512U);
    } else if (mode == USB_MODE_HID) {
        dwc3_config_ep(3U, 3U, 32U);   /* 0x81 HID Interrupt-IN */
        ena |= (1U << 3);
        dwc3_write(REG_DALEPENA, ena);
        hid_arm_next_report();
    }
}

static void ep0_send_in(UsbFwMailbox *mbox, const void *src,
                        uint32_t avail_len, uint16_t wlength)
{
    uint32_t n = avail_len < (uint32_t)wlength ? avail_len : (uint32_t)wlength;
    if (n > 1024U) {
        n = 1024U;
    }
    if (n > 0U && src != (const void *)BUF_EP1_IN_PA) {
        memcpy((void *)BUF_EP1_IN_PA, src, n);
    }
    g_ep0_stage = EP0_STAGE_DATA_IN;
    if (mbox->flags & MBOX_FLAG_PREARM_STAT3) {
        dwc3_arm_single_trb(0U, TRB_EP0_PA, BUF_EP0_STAT_PA, 0U,
                            TRBCTL_CONTROL_STATUS3);
    } else {
        dwc3_ep_end_transfer(0U);
    }
    dwc3_arm_single_trb(1U, TRB_EP1_PA, BUF_EP1_IN_PA, n, TRBCTL_CONTROL_DATA);
}

static void ep0_send_status2(void)
{
    /* Pre-arm next SETUP on EP0 OUT alongside STATUS2 IN on EP1 so both normal
     * completion and allow-setup-abort=on mid-transfer SETUP succeed cleanly. */
    dwc3_arm_single_trb(0U, TRB_EP0_PA, BUF_EP0_SETUP_PA, 8U, TRBCTL_CONTROL_SETUP);
    g_ep0_stage = EP0_STAGE_STATUS2_IN;
    dwc3_arm_single_trb(1U, TRB_EP1_PA, BUF_EP0_STAT_PA, 0U, TRBCTL_CONTROL_STATUS2);
}

static void ep0_stall(void)
{
    dwc3_ep_set_stall(0U);
    dwc3_ep_set_stall(1U);
    ep0_arm_setup();
}

static void handle_ep0_setup(UsbFwMailbox *mbox, uint32_t mode)
{
    const uint8_t *s = (const uint8_t *)BUF_EP0_SETUP_PA;
    uint8_t req_type = s[0];
    uint8_t req = s[1];
    uint16_t wvalue = get_le16(&s[2]);
    uint16_t windex = get_le16(&s[4]);
    uint16_t wlength = get_le16(&s[6]);
    uint8_t *in_buf = (uint8_t *)BUF_EP1_IN_PA;

    mbox->ep0_setup_count++;
    g_ep0_req_type = req_type;
    g_ep0_req = req;
    g_ep0_wvalue = wvalue;
    g_ep0_windex = windex;
    g_ep0_wlength = wlength;

    /* Standard Device/Interface/Endpoint requests */
    if ((req_type & 0x60U) == 0x00U) {
        if (req_type == 0x80U && req == 0x06U) {
            /* GET_DESCRIPTOR (Device / Config / String) */
            uint8_t dtype = (uint8_t)(wvalue >> 8);
            uint8_t dindex = (uint8_t)(wvalue & 0xFFU);
            const uint8_t *dev_d = NULL;
            const uint8_t *cfg_d = NULL;
            uint32_t dev_l = 0U;
            uint32_t cfg_l = 0U;
            get_mode_descriptors(mode, &dev_d, &dev_l, &cfg_d, &cfg_l);

            if (dtype == 0x01U && dindex == 0U) {
                ep0_send_in(mbox, dev_d, dev_l, wlength);
                return;
            }
            if (dtype == 0x02U && dindex == 0U) {
                ep0_send_in(mbox, cfg_d, cfg_l, wlength);
                return;
            }
            if (dtype == 0x03U) {
                if (dindex == 0U) {
                    ep0_send_in(mbox, desc_str_lang, sizeof(desc_str_lang), wlength);
                    return;
                }
                if (dindex == 1U) {
                    ep0_send_in(mbox, desc_str_mfg, sizeof(desc_str_mfg), wlength);
                    return;
                }
                if (dindex == 2U) {
                    ep0_send_in(mbox, desc_str_prod, sizeof(desc_str_prod), wlength);
                    return;
                }
                if (dindex == 3U) {
                    ep0_send_in(mbox, desc_str_ser, sizeof(desc_str_ser), wlength);
                    return;
                }
            }
            ep0_stall();
            return;
        }

        if (req_type == 0x81U && req == 0x06U) {
            /* GET_DESCRIPTOR to Interface (HID Report 0x22) */
            uint8_t dtype = (uint8_t)(wvalue >> 8);
            if (mode == USB_MODE_HID && dtype == 0x22U && windex == 0U) {
                ep0_send_in(mbox, desc_hid_report, sizeof(desc_hid_report), wlength);
                return;
            }
            ep0_stall();
            return;
        }

        if (req_type == 0x00U && req == 0x05U && wlength == 0U) {
            /* SET_ADDRESS */
            uint32_t dcfg = dwc3_read(REG_DCFG);
            dcfg = (dcfg & ~(0x7FU << 3)) | (((uint32_t)wvalue & 0x7FU) << 3);
            dwc3_write(REG_DCFG, dcfg);
            ep0_send_status2();
            return;
        }

        if (req_type == 0x00U && req == 0x09U && wlength == 0U) {
            /* SET_CONFIGURATION */
            uint8_t cfg = (uint8_t)(wvalue & 0xFFU);
            if (cfg == 0U) {
                g_config_value = 0U;
                dwc3_write(REG_DALEPENA, 0x3U);
                ep0_send_status2();
                return;
            }
            if (cfg == 1U) {
                g_config_value = 1U;
                configure_mode_endpoints(mode);
                ep0_send_status2();
                return;
            }
            ep0_stall();
            return;
        }

        if (req_type == 0x80U && req == 0x08U && wlength == 1U) {
            /* GET_CONFIGURATION */
            in_buf[0] = g_config_value;
            ep0_send_in(mbox, in_buf, 1U, 1U);
            return;
        }

        if (req_type == 0x80U && req == 0x00U && wlength == 2U) {
            /* GET_STATUS (Device) */
            in_buf[0] = 0x01U;
            in_buf[1] = 0x00U;
            ep0_send_in(mbox, in_buf, 2U, 2U);
            return;
        }

        if (req_type == 0x01U && req == 0x0BU && wlength == 0U) {
            /* SET_INTERFACE */
            if (wvalue == 0U) {
                ep0_send_status2();
                return;
            }
            ep0_stall();
            return;
        }

        if (req_type == 0x02U && req == 0x01U && wvalue == 0U && wlength == 0U) {
            /* CLEAR_FEATURE(ENDPOINT_HALT) */
            uint32_t ep_num = (uint32_t)windex & 0x0FU;
            uint32_t dir_in = ((uint32_t)windex >> 7) & 1U;
            uint32_t phys = (ep_num << 1) | dir_in;
            if (phys >= 2U && phys < 16U) {
                dwc3_ep_clear_stall(phys);
                if (mode == USB_MODE_MSC && phys == 3U && g_has_pending_csw) {
                    memcpy((void *)BUF_EP3_RESP_PA, g_pending_csw, 13U);
                    g_has_pending_csw = 0;
                    g_bot_state = BOT_IDLE;
                    dwc3_arm_single_trb(3U, TRB_EP3_PA, BUF_EP3_RESP_PA, 13U,
                                        TRBCTL_NORMAL);
                    ep4_arm_out(512U);
                } else if (mode == USB_MODE_MSC && phys == 4U &&
                           g_bot_state == BOT_IDLE) {
                    ep4_arm_out(512U);
                }
            }
            ep0_send_status2();
            return;
        }

        ep0_stall();
        return;
    }

    /* Class-specific Interface requests */
    if ((req_type & 0x60U) == 0x20U) {
        /* MSC GET_MAX_LUN (A1 FE) & Bulk-Only Mass Storage Reset (21 FF) */
        if (mode == USB_MODE_MSC && req_type == 0xA1U && req == 0xFEU &&
            wvalue == 0U && windex == 0U && wlength == 1U) {
            in_buf[0] = 0U;  /* 1 LUN (index 0) */
            ep0_send_in(mbox, in_buf, 1U, 1U);
            return;
        }
        if (mode == USB_MODE_MSC && req_type == 0x21U && req == 0xFFU &&
            wvalue == 0U && windex == 0U && wlength == 0U) {
            g_bot_state = BOT_IDLE;
            g_has_pending_csw = 0;
            dwc3_ep_end_transfer(3U);
            dwc3_ep_end_transfer(4U);
            ep0_send_status2();
            return;
        }

        /* PTP Still Image GET_DEVICE_STATUS (A1 67) & CANCEL (21 64) */
        if ((mode == USB_MODE_PTP || mode == USB_MODE_COMPOSITE) && windex == 0U) {
            if (req_type == 0xA1U && req == 0x67U && wlength >= 4U) {
                put_le16(&in_buf[0], 4U);
                put_le16(&in_buf[2], 0x2001U);  /* PTP_RC_OK */
                ep0_send_in(mbox, in_buf, 4U, wlength);
                return;
            }
            if (req_type == 0x21U && req == 0x64U && wlength == 6U) {
                g_ep0_stage = EP0_STAGE_DATA_OUT;
                dwc3_arm_single_trb(0U, TRB_EP0_PA, BUF_EP0_OUT_PA, 6U,
                                    TRBCTL_CONTROL_DATA);
                if (mbox->flags & MBOX_FLAG_PREARM_STAT3) {
                    dwc3_arm_single_trb(1U, TRB_EP1_PA, BUF_EP0_STAT_PA, 0U,
                                        TRBCTL_CONTROL_STATUS3);
                }
                return;
            }
        }

        /* UVC 1.1 VS_PROBE_CONTROL (CS=0x01) & VS_COMMIT_CONTROL (CS=0x02) on Intf 2 */
        if (mode == USB_MODE_COMPOSITE && windex == 2U) {
            uint8_t cs = (uint8_t)(wvalue >> 8);
            if ((cs == 0x01U || cs == 0x02U) && wlength == 26U) {
                if (req_type == 0x21U && req == 0x01U) {
                    /* SET_CUR: arm 26B Control-OUT + pre-arm STATUS3 on EP1 (E0476) */
                    g_ep0_stage = EP0_STAGE_DATA_OUT;
                    dwc3_arm_single_trb(0U, TRB_EP0_PA, BUF_EP0_OUT_PA, 26U,
                                        TRBCTL_CONTROL_DATA);
                    if (mbox->flags & MBOX_FLAG_PREARM_STAT3) {
                        dwc3_arm_single_trb(1U, TRB_EP1_PA, BUF_EP0_STAT_PA, 0U,
                                            TRBCTL_CONTROL_STATUS3);
                    }
                    return;
                }
                if (req_type == 0xA1U &&
                    (req == 0x81U || req == 0x82U || req == 0x83U)) {
                    const uint8_t *src = (cs == 0x02U) ? g_uvc_commit_ctrl
                                                       : g_uvc_probe_ctrl;
                    ep0_send_in(mbox, src, 26U, 26U);
                    return;
                }
            }
        }
    }

    ep0_stall();
}

static void handle_ep0_control_out_data(UsbFwMailbox *mbox, uint32_t mode)
{
    const uint8_t *buf = (const uint8_t *)BUF_EP0_OUT_PA;

    if ((mode == USB_MODE_PTP || mode == USB_MODE_COMPOSITE) &&
        g_ep0_req_type == 0x21U && g_ep0_req == 0x64U && g_ep0_wlength == 6U) {
        if (get_le16(&buf[0]) == 0x4001U) {
            g_ptp_last_canceled_tx = get_le32(&buf[2]);
        }
    } else if (mode == USB_MODE_COMPOSITE && g_ep0_req_type == 0x21U &&
               g_ep0_req == 0x01U && g_ep0_windex == 2U && g_ep0_wlength == 26U) {
        uint8_t cs = (uint8_t)(g_ep0_wvalue >> 8);
        uint8_t fmt = buf[2] ? buf[2] : 1U;
        uint8_t frm = buf[3] ? buf[3] : 1U;
        uint32_t interval = get_le32(&buf[4]);
        if (interval == 0U) {
            interval = 333333U;
        }
        uint8_t *target = (cs == 0x02U) ? g_uvc_commit_ctrl : g_uvc_probe_ctrl;
        put_le16(&target[0], get_le16(&buf[0]));
        target[2] = fmt;
        target[3] = frm;
        put_le32(&target[4], interval);
        put_le32(&target[18], 1280U * 720U * 2U);
        put_le32(&target[22], 16384U);
        if (cs == 0x02U) {
            /* VS_COMMIT_CONTROL: start arming multi-TRB UVC MJPEG frames on 0x84 */
            g_uvc_streaming = 1;
            g_uvc_fid = 0U;
            g_uvc_pts = 1000U;
            dwc3_ep_end_transfer(9U);
            uvc_arm_next_mjpeg_frame();
        }
    }

    g_ep0_stage = EP0_STAGE_STATUS3_IN;
    if (!(mbox->flags & MBOX_FLAG_PREARM_STAT3)) {
        dwc3_arm_single_trb(1U, TRB_EP1_PA, BUF_EP0_STAT_PA, 0U,
                            TRBCTL_CONTROL_STATUS3);
    }
}

/* -------------------------------------------------------------------------
 * DWC3 Lifecycle, Clean Soft-Disconnect, and GEVNT Event Loop
 * ------------------------------------------------------------------------- */

static void dwc3_quiesce_and_stop(UsbFwMailbox *mbox)
{
    uint32_t dctl = dwc3_read(REG_DCTL);
    if (!(dctl & DCTL_RUN_STOP)) {
        uint32_t dsts = dwc3_read(REG_DSTS);
        mbox->last_dsts = dsts;
        return;
    }

    /* Unconfigure-first teardown: retire active endpoint transfers, drop RUN_STOP,
     * and poll DSTS.DEVCTRLHLT until controller halt is confirmed (E0352/E0639). */
    for (uint32_t ep = 0U; ep < 10U; ep++) {
        if (dwc3_read(REG_DALEPENA) & (1U << ep)) {
            dwc3_ep_end_transfer(ep);
        }
    }
    dwc3_write(REG_DALEPENA, 0U);
    dwc3_write(REG_DCTL, dctl & ~DCTL_RUN_STOP);

    for (uint32_t spin = 0U; spin < 100000U; spin++) {
        uint32_t dsts = dwc3_read(REG_DSTS);
        if (dsts & DSTS_DEVCTRLHLT) {
            mbox->last_dsts = dsts;
            mbox->halt_confirmed_count++;
            break;
        }
    }
    g_config_value = 0U;
    g_uvc_streaming = 0;
}

static void dwc3_start_mode(uint32_t mode)
{
    memset((void *)GEVNT_RING_PA, 0, GEVNT_RING_BYTES);
    memset((void *)TRB_EP0_PA, 0, 0x1000U);
    g_gevnt_tail = 0U;
    g_config_value = 0U;
    g_ep0_stage = EP0_STAGE_SETUP;
    g_bot_state = BOT_IDLE;
    g_has_pending_csw = 0;
    g_ptp_rx_state = PTP_RX_CMD;
    g_ptp_session_open = 0;
    g_uvc_streaming = 0;
    init_uvc_default_controls();

    /* Core/device soft-reset + GEVNT DMA ring setup */
    dwc3_write(REG_DCTL, DCTL_CSFTRST);
    dwc3_write(REG_GCTL, 0x00002000U);
    dwc3_write(REG_GEVNTADRLO0, (uint32_t)GEVNT_RING_PA);
    dwc3_write(REG_GEVNTADRHI0, 0U);
    dwc3_write(REG_GEVNTSIZ0, (uint32_t)GEVNT_RING_BYTES);
    dwc3_write(REG_DCFG, 0U);      /* High-speed, address 0 */
    dwc3_write(REG_DEVTEN, 0x07U); /* DISCONNECT | USBRESET | CONNECTDONE */

    /* Configure EP0 OUT (phys 0) and EP0 IN (phys 1) */
    dwc3_ep_cmd(0U, DEPCMD_STARTNEWCONFIG, 0U, 0U, 0U);
    dwc3_config_ep(0U, 0U, 64U);
    dwc3_config_ep(1U, 0U, 64U);
    dwc3_write(REG_DALEPENA, 0x3U);

    ep0_arm_setup();
    dwc3_write(REG_DCTL, DCTL_RUN_STOP);
    (void)mode;
}

static void handle_bus_reset(UsbFwMailbox *mbox)
{
    mbox->usb_reset_count++;
    for (uint32_t ep = 2U; ep < 10U; ep++) {
        if (dwc3_read(REG_DALEPENA) & (1U << ep)) {
            dwc3_ep_end_transfer(ep);
            dwc3_ep_clear_stall(ep);
        }
    }
    dwc3_ep_end_transfer(0U);
    dwc3_ep_end_transfer(1U);
    dwc3_ep_clear_stall(0U);
    dwc3_ep_clear_stall(1U);
    dwc3_write(REG_DCFG, 0U);
    dwc3_write(REG_DALEPENA, 0x3U);
    g_config_value = 0U;
    g_bot_state = BOT_IDLE;
    g_has_pending_csw = 0;
    g_ptp_rx_state = PTP_RX_CMD;
    g_ptp_session_open = 0;
    g_uvc_streaming = 0;
    ep0_arm_setup();
}

static void drain_dwc3_events(UsbFwMailbox *mbox, uint32_t mode)
{
    while (dwc3_read(REG_GEVNTCOUNT0) >= 4U) {
        uint32_t ev = mmio_read32(GEVNT_RING_PA + g_gevnt_tail);
        g_gevnt_tail = (g_gevnt_tail + 4U) & (GEVNT_RING_BYTES - 1U);
        dwc3_write(REG_GEVNTCOUNT0, 4U);

        if (ev & 1U) {
            /* Device-specific event (DEVT) */
            uint32_t devt = (ev >> 8) & 0xFU;
            if (devt == DEVT_USBRESET) {
                handle_bus_reset(mbox);
            } else if (devt == DEVT_DISCONNECT) {
                g_config_value = 0U;
                g_uvc_streaming = 0;
            }
            continue;
        }

        /* Endpoint-specific event (DEPEVT) */
        uint32_t phys_ep = (ev >> 1) & 0x1FU;
        uint32_t evt_type = (ev >> 6) & 0xFU;
        uint32_t status = (ev >> 12) & 0xFU;

        if (phys_ep == 0U) {
            if (evt_type == DEPEVT_XFERNOTREADY && status == 0U) {
                /* Setup-abort recovery: retire stale stage and re-arm SETUP */
                dwc3_ep_end_transfer(0U);
                dwc3_ep_end_transfer(1U);
                ep0_arm_setup();
                continue;
            }
            if (evt_type == DEPEVT_XFERCOMPLETE) {
                if (g_ep0_stage == EP0_STAGE_SETUP) {
                    handle_ep0_setup(mbox, mode);
                } else if (g_ep0_stage == EP0_STAGE_STATUS2_IN) {
                    /* Host aborted pending STATUS2 IN with a new SETUP */
                    dwc3_ep_end_transfer(1U);
                    g_ep0_stage = EP0_STAGE_SETUP;
                    handle_ep0_setup(mbox, mode);
                } else if (g_ep0_stage == EP0_STAGE_DATA_OUT) {
                    handle_ep0_control_out_data(mbox, mode);
                } else if (g_ep0_stage == EP0_STAGE_STATUS3_OUT ||
                           g_ep0_stage == EP0_STAGE_DATA_IN) {
                    /* STATUS3 OUT completed (possibly before EP1 XFERCOMPLETE was drained) */
                    ep0_arm_setup();
                }
            } else if (evt_type == DEPEVT_XFERNOTREADY && status == 2U &&
                       g_ep0_stage == EP0_STAGE_DATA_IN) {
                g_ep0_stage = EP0_STAGE_STATUS3_OUT;
                if (!(mbox->flags & MBOX_FLAG_PREARM_STAT3)) {
                    dwc3_arm_single_trb(0U, TRB_EP0_PA, BUF_EP0_STAT_PA, 0U,
                                        TRBCTL_CONTROL_STATUS3);
                }
            }
        } else if (phys_ep == 1U) {
            if (evt_type == DEPEVT_XFERCOMPLETE) {
                if (g_ep0_stage == EP0_STAGE_DATA_IN) {
                    g_ep0_stage = EP0_STAGE_STATUS3_OUT;
                    if (!(mbox->flags & MBOX_FLAG_PREARM_STAT3)) {
                        dwc3_arm_single_trb(0U, TRB_EP0_PA, BUF_EP0_STAT_PA, 0U,
                                            TRBCTL_CONTROL_STATUS3);
                    }
                } else if (g_ep0_stage == EP0_STAGE_STATUS2_IN) {
                    /* EP0 OUT already has next SETUP TRB pre-armed */
                    g_ep0_stage = EP0_STAGE_SETUP;
                } else if (g_ep0_stage == EP0_STAGE_STATUS3_IN) {
                    ep0_arm_setup();
                }
            }
        } else if (phys_ep == 4U && evt_type == DEPEVT_XFERCOMPLETE) {
            volatile Dwc3Trb *trb = (volatile Dwc3Trb *)TRB_EP4_PA;
            uint32_t residual = trb->size & 0x00FFFFFFU;
            uint32_t rx_len = g_ep4_armed_bytes >= residual
                                  ? (g_ep4_armed_bytes - residual)
                                  : 0U;
            if (mode == USB_MODE_MSC) {
                if (g_bot_state == BOT_DATA_OUT) {
                    msc_handle_data_out_done(rx_len);
                } else {
                    msc_handle_cbw(mbox, rx_len);
                }
            } else if (mode == USB_MODE_PTP || mode == USB_MODE_COMPOSITE) {
                ptp_handle_out(mbox, rx_len);
            } else if (mode == USB_MODE_JIRITSU) {
                jiritsu_handle_out(rx_len);
            }
        } else if (phys_ep == 3U && evt_type == DEPEVT_XFERCOMPLETE) {
            if (mode == USB_MODE_HID && (status & 8U)) {
                hid_arm_next_report();
            }
        } else if (phys_ep == 9U && evt_type == DEPEVT_XFERCOMPLETE) {
            /* Last TRB of UVC MJPEG frame completed: toggle FID & arm next */
            if (mode == USB_MODE_COMPOSITE && g_uvc_streaming && (status & 8U)) {
                mbox->uvc_frames_sent++;
                g_uvc_fid ^= 1U;
                g_uvc_pts += 3000U;
                uvc_arm_next_mjpeg_frame();
            }
        }
    }
}

/* -------------------------------------------------------------------------
 * Boot Parameter Parsing & Optional UART0 Mailbox Command Helper
 * ------------------------------------------------------------------------- */

static int str_contains(const char *haystack, const char *needle)
{
    for (; *haystack != '\0'; haystack++) {
        const char *h = haystack;
        const char *n = needle;
        while (*n != '\0' && *h == *n) {
            h++;
            n++;
        }
        if (*n == '\0') {
            return 1;
        }
    }
    return 0;
}

static uint32_t parse_boot_mode(uint32_t *out_flags)
{
    const char *cmd = (const char *)BOOT_PARAMS_ADDR;
    uint32_t flags = MBOX_FLAG_PREARM_STAT3;
    uint32_t mode = USB_MODE_MSC;

    if (str_contains(cmd, "mode=ptp")) {
        mode = USB_MODE_PTP;
    } else if (str_contains(cmd, "mode=uvc") || str_contains(cmd, "mode=composite")) {
        mode = USB_MODE_COMPOSITE;
    } else if (str_contains(cmd, "mode=hid")) {
        mode = USB_MODE_HID;
    } else if (str_contains(cmd, "mode=jiritsu")) {
        mode = USB_MODE_JIRITSU;
    } else if (str_contains(cmd, "mode=msc")) {
        mode = USB_MODE_MSC;
    }
    if (str_contains(cmd, "ro=1")) {
        flags |= MBOX_FLAG_MSC_WP;
    }
    *out_flags = flags;
    return mode;
}

/* Non-blocking PL011 UART0 line parser ("M <1..5>\n", "W <0|1>\n") so tests
 * can steer SRAM mailbox via either GDB/SRAM writes or serial stdin. */
static char g_uart_line[32];
static uint32_t g_uart_len;

static void poll_uart_commands(UsbFwMailbox *mbox)
{
    while ((mmio_read32(UART0_BASE + 0x18UL) & (1U << 4)) == 0U) {
        char c = (char)(mmio_read32(UART0_BASE + 0x00UL) & 0xFFU);
        if (c == '\r') {
            continue;
        }
        if (c == '\n') {
            g_uart_line[g_uart_len] = '\0';
            if (g_uart_len >= 3U && g_uart_line[0] == 'M' && g_uart_line[1] == ' ') {
                uint32_t m = (uint32_t)(g_uart_line[2] - '0');
                if (m <= 5U) {
                    mbox->req_mode = m;
                }
            } else if (g_uart_len >= 3U && g_uart_line[0] == 'W' && g_uart_line[1] == ' ') {
                if (g_uart_line[2] == '1') {
                    mbox->flags |= MBOX_FLAG_MSC_WP;
                } else if (g_uart_line[2] == '0') {
                    mbox->flags &= ~MBOX_FLAG_MSC_WP;
                }
            }
            g_uart_len = 0U;
        } else if (g_uart_len + 1U < sizeof(g_uart_line)) {
            g_uart_line[g_uart_len++] = c;
        }
    }
}

void fw_main(void)
{
    UsbFwMailbox *mbox = (UsbFwMailbox *)SRAM_MBOX_ADDR;
    uint32_t init_flags = 0U;
    uint32_t init_mode = parse_boot_mode(&init_flags);
    int prev_vbus = 0;

    build_synthetic_jpeg();
    init_ramdisk();
    init_uvc_default_controls();

    memset((void *)mbox, 0, sizeof(*mbox));
    mbox->req_mode = init_mode;
    mbox->active_mode = USB_MODE_NONE;
    mbox->flags = init_flags;
    mbox->ghwparams0 = dwc3_read(REG_GHWPARAMS0);
    mbox->gsnpsid = dwc3_read(REG_GSNPSID);
    mbox->last_dsts = dwc3_read(REG_DSTS);
    mbox->fusb_last = mmio_read32(FUSB_VBUS_STATUS);
    dmb_barrier();
    mbox->magic = MBOX_MAGIC;

    for (;;) {
        poll_uart_commands(mbox);

        uint32_t fusb = mmio_read32(FUSB_VBUS_STATUS);
        mbox->fusb_last = fusb;
        int vbus = (fusb & 1U) != 0U;

        if (vbus && !prev_vbus) {
            mbox->vbus_attach_count++;
        } else if (!vbus && prev_vbus) {
            mbox->vbus_detach_count++;
            if (mbox->active_mode != USB_MODE_NONE) {
                dwc3_quiesce_and_stop(mbox);
                mbox->active_mode = USB_MODE_NONE;
            }
        }
        prev_vbus = vbus;

        uint32_t req = mbox->req_mode;
        if (vbus) {
            if (req == USB_MODE_NONE) {
                if (mbox->active_mode != USB_MODE_NONE) {
                    dwc3_quiesce_and_stop(mbox);
                    mbox->active_mode = USB_MODE_NONE;
                }
            } else if (req <= USB_MODE_JIRITSU && mbox->active_mode != req) {
                uint32_t was_active = mbox->active_mode;
                if (was_active != USB_MODE_NONE) {
                    dwc3_quiesce_and_stop(mbox);
                    mbox->mode_switch_count++;
                }
                dwc3_start_mode(req);
                mbox->active_mode = req;
            }
        }

        if (mbox->active_mode != USB_MODE_NONE) {
            drain_dwc3_events(mbox, mbox->active_mode);
        }
    }
}
