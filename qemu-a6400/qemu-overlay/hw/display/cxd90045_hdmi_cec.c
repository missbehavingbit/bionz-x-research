/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * CXD90045 HDMI TX (0xF2A40000, size 0x20000) and CEC (0xF3C10000, size 0x190).
 *
 * Hardware contracts derived from hdmi.ko and cec.ko (vmlinux.bin UDIF entries
 * "hdmi" @ 0xF2A40000 size 0x20000 and "cec" @ 0xF3C10000 size 0x190) and VPH
 * line-scanout console 1 (cxd90014_vph.c "hdmi-tmds" GPIO):
 *
 * HDMI TX (32-bit word stride: PA = 0xF2A40000 + reg_idx * 4):
 *   - reg 0x0105 (+0x0414): DDC ready status (bit 1 = 0x02, write-1-to-clear)
 *   - reg 0x0803 / 0x0804:  PHY I2C master status (ready = 0x02)
 *   - reg 0x3004 (+0xC010): HPD live state (bit 1 = 0x02 when sink connected)
 *   - reg 0x3005 (+0xC014): HPD interrupt/event status (bit 1 = 0x02)
 *   - reg 0x3007 (+0xC01C): HPD interrupt clear (bit 1 write-1-to-clear)
 *   - reg 0x4001 (+0x10004): TMDS channel output enable (bits 0..5); drives
 *                            VPH's "hdmi-tmds" GPIO input to enable Console 1
 *   - reg 0x4004 (+0x10010): TMDS PHY PLL lock status (bit 0 = 1 when locked)
 *   - reg 0x7E00 (+0x1F800): DDC I2C slave address (0x50 = EDID)
 *   - reg 0x7E01 (+0x1F804): DDC EDID byte offset (0x00..0xFF)
 *   - reg 0x7E02 (+0x1F808): DDC read length (up to 32 bytes per burst)
 *   - reg 0x7E04 (+0x1F810): DDC command trigger (bit 3 = 0x08 / non-zero ->
 *                            loads reg 0x7E20..0x7E3F, 1 byte per 32-bit word,
 *                            and sets reg 0x0105 bit 1)
 *   - reg 0x7E08 / 0x7E0A:   E-DDC segment slave (0x30) and segment index
 *
 * CEC (32-bit word stride: PA = 0xF3C10000 + reg_idx * 4):
 *   - reg 0x00..0x0F (+0x00..+0x3C): TX frame bytes 0..15
 *   - reg 0x10       (+0x40):        TX frame byte length (1..16)
 *   - reg 0x11       (+0x44):        TX start trigger (bit 0)
 *   - reg 0x15..0x24 (+0x54..+0x90): RX frame bytes 0..15
 *   - reg 0x25       (+0x94):        RX frame byte length
 *   - reg 0x27/0x28  (+0x9C/+0xA0):  Initiator logical address mask/index
 *   - reg 0x60       (+0x180):       Status: bit 0 = RX ready, bit 1 = TX ACK,
 *                                    bit 2 = TX NACK (self-poll allocation)
 *   - reg 0x61       (+0x184):       Status clear (write-1-to-clear)
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "hw/core/sysbus.h"
#include "hw/core/irq.h"
#include "hw/misc/cxd90045_peripherals.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qom/object.h"

#define TYPE_CXD90045_HDMI_CEC "cxd90045-hdmi-cec"
OBJECT_DECLARE_SIMPLE_TYPE(Cxd90045HdmiCecState, CXD90045_HDMI_CEC)

#define HDMI_TX_BASE      0xF2A40000ULL
#define HDMI_TX_SIZE      0x00020000ULL
#define HDMI_TX_WORDS     (HDMI_TX_SIZE >> 2)

#define CEC_BASE          0xF3C10000ULL
#define CEC_SIZE          0x00000190ULL
#define CEC_WORDS         (CEC_SIZE >> 2)

#define CEC_TV_ADDR       0x00 /* Logical address 0 = TV */

struct Cxd90045HdmiCecState {
    SysBusDevice parent_obj;
    MemoryRegion hdmi_mr;
    MemoryRegion cec_mr;
    DeviceState *vph;

    uint32_t hdmi_regs[HDMI_TX_WORDS];
    uint8_t edid[256];
    bool hpd_plugged;
    bool tmds_active;

    uint32_t cec_regs[CEC_WORDS];
    uint8_t cec_logical_addr;

    /* Telemetry */
    uint64_t edid_reads;
    uint64_t cec_tx_frames;
    uint64_t cec_rx_frames;
    uint8_t last_cec_tx[16];
    uint8_t last_cec_tx_len;
};

/* Build a standards-compliant 256-byte CEA-861 + HDMI VSDB 1080p60 EDID */
static void hdmi_build_edid(uint8_t edid[256])
{
    static const uint8_t blk0_fixed[128] = {
        /* 0x00: EDID 1.3 Header */
        0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00,
        /* 0x08: Manufacturer "SNY" (0x4DD9), Product 0x6400, Serial 1 */
        0x4D, 0xD9, 0x00, 0x64, 0x01, 0x00, 0x00, 0x00,
        /* 0x10: Week 1, Year 2024 (34), EDID v1.3 */
        0x01, 0x22, 0x01, 0x03,
        /* 0x14: Digital input, 16:9 (52cm x 29cm), Gamma 2.20, RGB444+YCbCr */
        0x80, 0x34, 0x1D, 0x78, 0x2A,
        /* 0x19: sRGB chromaticity */
        0xEE, 0x91, 0xA3, 0x54, 0x4C, 0x99, 0x26, 0x0F, 0x50, 0x54,
        /* 0x23: Established timings (640x480@60) + Standard timings */
        0x20, 0x00, 0x00,
        0xD1, 0xC0, 0x81, 0xC0, 0x01, 0x01, 0x01, 0x01,
        0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01,
        /* 0x36: DTD 1: 1920x1080p @ 60Hz (148.50 MHz, VIC 16) */
        0x02, 0x3A, 0x80, 0x18, 0x71, 0x38, 0x2D, 0x40,
        0x58, 0x2C, 0x45, 0x00, 0x09, 0x25, 0x21, 0x00, 0x00, 0x1E,
        /* 0x48: DTD 2: 1280x720p @ 60Hz (74.25 MHz, VIC 4) */
        0x01, 0x1D, 0x00, 0x72, 0x51, 0xD0, 0x1E, 0x20,
        0x6E, 0x28, 0x55, 0x00, 0x09, 0x25, 0x21, 0x00, 0x00, 0x1E,
        /* 0x5A: Monitor Name Descriptor "SONY QEMU TV" */
        0x00, 0x00, 0x00, 0xFC, 0x00,
        'S', 'O', 'N', 'Y', ' ', 'Q', 'E', 'M', 'U', ' ', 'T', 'V', '\n',
        /* 0x6C: Monitor Range Limits (24-120 Hz, 15-135 kHz, 300 MHz) */
        0x00, 0x00, 0x00, 0xFD, 0x00,
        0x18, 0x78, 0x0F, 0x87, 0x1E, 0x00, 0x0A, 0x20, 0x20, 0x20, 0x20,
        0x20, 0x20,
        /* 0x7E: 1 CEA-861 extension block, checksum computed below */
        0x01, 0x00,
    };

    memset(edid, 0, 256);
    memcpy(edid, blk0_fixed, 128);

    /* Extension Block 1: CEA-861 rev 3 with Video, Audio, and HDMI VSDB */
    uint8_t *cea = &edid[128];
    cea[0] = 0x02; /* CEA-861 tag */
    cea[1] = 0x03; /* Revision 3 */
    cea[2] = 0x1A; /* DTD offset = 26 */
    cea[3] = 0x71; /* Underscan, Basic Audio, YCbCr 4:4:4 + 4:2:2, 1 native */

    /* Video Data Block (tag 2, len 6): VIC 16 (1080p60 native), 4 (720p60),
     * 31 (1080p50), 19 (720p50), 3 (480p60 16:9), 1 (640x480p60) */
    cea[4]  = (2 << 5) | 6;
    cea[5]  = 0x80 | 16;
    cea[6]  = 4;
    cea[7]  = 31;
    cea[8]  = 19;
    cea[9]  = 3;
    cea[10] = 1;

    /* Audio Data Block (tag 1, len 3): 2-ch LPCM 32/44.1/48 kHz, 16/20/24b */
    cea[11] = (1 << 5) | 3;
    cea[12] = 0x09;
    cea[13] = 0x07;
    cea[14] = 0x07;

    /* Speaker Allocation Data Block (tag 4, len 3): FL/FR */
    cea[15] = (4 << 5) | 3;
    cea[16] = 0x01;
    cea[17] = 0x00;
    cea[18] = 0x00;

    /* HDMI Vendor-Specific Data Block (tag 3, len 6): OUI 00-0C-03, PA 1.0.0.0 */
    cea[19] = (3 << 5) | 6;
    cea[20] = 0x03;
    cea[21] = 0x0C;
    cea[22] = 0x00;
    cea[23] = 0x10; /* Physical Address 1.0.0.0 */
    cea[24] = 0x00;
    cea[25] = 0x38; /* Deep Color 30/36-bit + Y444 */

    /* Compute 8-bit 2's-complement checksums for both 128-byte blocks */
    for (int blk = 0; blk < 2; blk++) {
        uint8_t sum = 0;
        for (int i = 0; i < 127; i++) {
            sum += edid[blk * 128 + i];
        }
        edid[blk * 128 + 127] = (uint8_t)(0u - sum);
    }
}

static void hdmi_sync_vph_tmds(Cxd90045HdmiCecState *s)
{
    bool want = s->hpd_plugged && ((s->hdmi_regs[0x4001] & 0x3Fu) != 0);

    if (s->tmds_active != want) {
        s->tmds_active = want;
        if (s->vph) {
            qemu_set_irq(qdev_get_gpio_in_named(s->vph, "hdmi-tmds", 0),
                         want ? 1 : 0);
        }
    }
}

static void hdmi_run_ddc_edid_read(Cxd90045HdmiCecState *s)
{
    uint32_t seg = s->hdmi_regs[0x7E0A] & 1u;
    uint32_t off = s->hdmi_regs[0x7E01] & 0xFFu;
    uint32_t base_idx = (seg * 256u + off) & 0xFFu;
    uint32_t count = s->hdmi_regs[0x7E02] & 0xFFu;

    if (count == 0 || count > 128) {
        count = 32;
    }
    for (uint32_t i = 0; i < count && (0x7E20u + i) < HDMI_TX_WORDS; i++) {
        s->hdmi_regs[0x7E20u + i] = s->edid[(base_idx + i) & 0xFFu];
    }
    if (s->hpd_plugged) {
        s->hdmi_regs[0x0105] |= 0x02u; /* DDC transfer complete */
        s->edid_reads++;
    }
}

static uint64_t hdmi_tx_read(void *opaque, hwaddr off, unsigned size)
{
    Cxd90045HdmiCecState *s = opaque;
    uint32_t idx = (uint32_t)(off >> 2);

    if (idx >= HDMI_TX_WORDS) {
        return 0;
    }
    switch (idx) {
    case 0x3004:
        /* Report HPD (0x40), RSEN (0x80), and sense bit 1 (0x02) when plugged */
        return (s->hdmi_regs[0x3004] & ~0xC2u) | (s->hpd_plugged ? 0xC2u : 0u);
    case 0x4004:
        /* TMDS PHY PLL lock ready */
        return s->hdmi_regs[0x4004] | 0x01u;
    case 0x0803:
    case 0x0804:
        /* PHY control ready */
        return s->hdmi_regs[idx] | 0x02u;
    default:
        return s->hdmi_regs[idx];
    }
}

static void hdmi_tx_write(void *opaque, hwaddr off, uint64_t val,
                          unsigned size)
{
    Cxd90045HdmiCecState *s = opaque;
    uint32_t idx = (uint32_t)(off >> 2);
    uint32_t v = (uint32_t)val;

    if (idx >= HDMI_TX_WORDS) {
        return;
    }

    switch (idx) {
    case 0x0105:
        /* Write-1-to-clear DDC ready flag */
        s->hdmi_regs[0x0105] &= ~v;
        return;
    case 0x3007:
        /* Write-1-to-clear HPD interrupt flag in 0x3005 */
        s->hdmi_regs[0x3007] = v;
        s->hdmi_regs[0x3005] &= ~v;
        return;
    case 0x4001:
        s->hdmi_regs[0x4001] = v;
        hdmi_sync_vph_tmds(s);
        return;
    case 0x7E04:
        s->hdmi_regs[0x7E04] = v;
        if (v != 0) {
            hdmi_run_ddc_edid_read(s);
        }
        return;
    default:
        s->hdmi_regs[idx] = v;
        break;
    }
}

static const MemoryRegionOps hdmi_tx_ops = {
    .read = hdmi_tx_read,
    .write = hdmi_tx_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

/* Queue a CEC RX frame from the virtual TV (logical address 0) to the camera */
static void cec_queue_rx(Cxd90045HdmiCecState *s, const uint8_t *frame,
                         uint8_t len)
{
    if (len == 0 || len > 16) {
        return;
    }
    for (uint8_t i = 0; i < 16; i++) {
        s->cec_regs[0x15 + i] = (i < len) ? frame[i] : 0;
    }
    s->cec_regs[0x25] = len;
    s->cec_regs[0x60] |= 0x01u; /* RX frame ready */
    s->cec_rx_frames++;
}

static void cec_handle_tx(Cxd90045HdmiCecState *s)
{
    uint8_t len = s->cec_regs[0x10] & 0x1Fu;

    if (len == 0) {
        len = 1;
    }
    if (len > 16) {
        len = 16;
    }
    for (uint8_t i = 0; i < len; i++) {
        s->last_cec_tx[i] = s->cec_regs[i] & 0xFFu;
    }
    s->last_cec_tx_len = len;
    s->cec_tx_frames++;

    uint8_t hdr = s->last_cec_tx[0];
    uint8_t src = (hdr >> 4) & 0x0Fu;
    uint8_t dst = hdr & 0x0Fu;

    /* Polling message (len == 1): ACK only TV (addr 0); NACK candidate playback
     * addresses (4, 8, 11) so camera logical-address allocation claims 4! */
    if (len == 1) {
        if (dst == CEC_TV_ADDR && s->hpd_plugged) {
            s->cec_regs[0x60] |= 0x02u; /* TX ACK */
        } else {
            s->cec_regs[0x60] |= 0x04u; /* TX NACK (address free) */
        }
        return;
    }

    if (!s->hpd_plugged) {
        s->cec_regs[0x60] |= 0x04u; /* NACK when unplugged */
        return;
    }

    /* Directed to TV (0) or Broadcast (0xF): ACK TX */
    if (dst != CEC_TV_ADDR && dst != 0x0Fu) {
        s->cec_regs[0x60] |= 0x04u;
        return;
    }
    s->cec_regs[0x60] |= 0x02u;

    /* Auto-reply from virtual Sony TV (logical addr 0) on discovery opcodes */
    uint8_t opcode = s->last_cec_tx[1];
    uint8_t reply[16];
    uint8_t reply_hdr = (CEC_TV_ADDR << 4) | (src & 0x0Fu);

    switch (opcode) {
    case 0x83: /* Give Physical Address -> Report Physical Address (0.0.0.0 TV) */
        reply[0] = (CEC_TV_ADDR << 4) | 0x0Fu;
        reply[1] = 0x84;
        reply[2] = 0x00;
        reply[3] = 0x00;
        reply[4] = 0x00; /* Device Type = TV */
        cec_queue_rx(s, reply, 5);
        break;

    case 0x8C: /* Give Device Vendor ID -> Device Vendor ID (Sony 08-00-46) */
        reply[0] = (CEC_TV_ADDR << 4) | 0x0Fu;
        reply[1] = 0x87;
        reply[2] = 0x08;
        reply[3] = 0x00;
        reply[4] = 0x46;
        cec_queue_rx(s, reply, 5);
        break;

    case 0x8F: /* Give Device Power Status -> Report Power Status (On = 0x00) */
        reply[0] = reply_hdr;
        reply[1] = 0x90;
        reply[2] = 0x00;
        cec_queue_rx(s, reply, 3);
        break;

    case 0x46: /* Give OSD Name -> Set OSD Name ("QEMU TV") */
        reply[0] = reply_hdr;
        reply[1] = 0x47;
        memcpy(&reply[2], "QEMU TV", 7);
        cec_queue_rx(s, reply, 9);
        break;

    case 0x9F: /* Get CEC Version -> CEC Version (1.4 = 0x05) */
        reply[0] = reply_hdr;
        reply[1] = 0x9E;
        reply[2] = 0x05;
        cec_queue_rx(s, reply, 3);
        break;

    case 0x8D: /* Menu Request -> Menu Status (Activated = 0x00) */
        reply[0] = reply_hdr;
        reply[1] = 0x8E;
        reply[2] = 0x00;
        cec_queue_rx(s, reply, 3);
        break;

    default:
        break;
    }
}

static uint64_t cec_read(void *opaque, hwaddr off, unsigned size)
{
    Cxd90045HdmiCecState *s = opaque;
    uint32_t idx = (uint32_t)(off >> 2);

    if (idx >= CEC_WORDS) {
        return 0;
    }
    return s->cec_regs[idx];
}

static void cec_write(void *opaque, hwaddr off, uint64_t val, unsigned size)
{
    Cxd90045HdmiCecState *s = opaque;
    uint32_t idx = (uint32_t)(off >> 2);
    uint32_t v = (uint32_t)val;

    if (idx >= CEC_WORDS) {
        return;
    }

    switch (idx) {
    case 0x11:
        s->cec_regs[0x11] = v & ~1u; /* TX trigger self-clears once sent */
        if (v & 1u) {
            cec_handle_tx(s);
        }
        return;
    case 0x27:
    case 0x28:
        s->cec_regs[idx] = v;
        if (v > 0 && v < 15) {
            s->cec_logical_addr = (uint8_t)v;
        }
        return;
    case 0x60:
        /* Allow direct W1C on 0x60 as well as 0x61 */
        s->cec_regs[0x60] &= ~v;
        return;
    case 0x61:
        s->cec_regs[0x61] = v;
        s->cec_regs[0x60] &= ~v;
        return;
    default:
        s->cec_regs[idx] = v;
        break;
    }
}

static const MemoryRegionOps cec_ops = {
    .read = cec_read,
    .write = cec_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void hdmi_cec_reset(DeviceState *dev)
{
    Cxd90045HdmiCecState *s = CXD90045_HDMI_CEC(dev);

    memset(s->hdmi_regs, 0, sizeof(s->hdmi_regs));
    memset(s->cec_regs, 0, sizeof(s->cec_regs));
    hdmi_build_edid(s->edid);
    if (s->hpd_plugged) {
        s->hdmi_regs[0x3004] = 0xC2u;
        s->hdmi_regs[0x3005] = 0xC2u;
    }
    s->tmds_active = false;
    s->cec_logical_addr = 4;
    if (s->vph) {
        qemu_set_irq(qdev_get_gpio_in_named(s->vph, "hdmi-tmds", 0), 0);
    }
}

static void hdmi_cec_realize(DeviceState *dev, Error **errp)
{
    Cxd90045HdmiCecState *s = CXD90045_HDMI_CEC(dev);

    memory_region_init_io(&s->hdmi_mr, OBJECT(dev), &hdmi_tx_ops, s,
                          "cxd90045.hdmi-tx", HDMI_TX_SIZE);
    memory_region_init_io(&s->cec_mr, OBJECT(dev), &cec_ops, s,
                          "cxd90045.cec", CEC_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->hdmi_mr);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->cec_mr);
}

static bool hdmi_get_plugged(Object *obj, Error **errp)
{
    return CXD90045_HDMI_CEC(obj)->hpd_plugged;
}

static void hdmi_set_plugged(Object *obj, bool val, Error **errp)
{
    Cxd90045HdmiCecState *s = CXD90045_HDMI_CEC(obj);

    if (s->hpd_plugged != val) {
        s->hpd_plugged = val;
        s->hdmi_regs[0x3005] |= 0xC2u; /* HPD + RSEN transition event */
        hdmi_sync_vph_tmds(s);
    }
}

static bool hdmi_get_tmds(Object *obj, Error **errp)
{
    return CXD90045_HDMI_CEC(obj)->tmds_active;
}

static void hdmi_get_u64(Object *obj, Visitor *v, const char *name,
                         void *opaque, Error **errp)
{
    Cxd90045HdmiCecState *s = CXD90045_HDMI_CEC(obj);
    uint64_t val = 0;

    if (strcmp(name, "edid-reads") == 0) {
        val = s->edid_reads;
    } else if (strcmp(name, "cec-tx-frames") == 0) {
        val = s->cec_tx_frames;
    } else if (strcmp(name, "cec-rx-frames") == 0) {
        val = s->cec_rx_frames;
    }
    visit_type_uint64(v, name, &val, errp);
}

/* Allow QMP injection of a remote CEC key press (e.g. Select=0x00, Up=0x01) */
static void cec_set_inject_key(Object *obj, Visitor *v, const char *name,
                               void *opaque, Error **errp)
{
    Cxd90045HdmiCecState *s = CXD90045_HDMI_CEC(obj);
    uint64_t key_code = 0;

    if (!visit_type_uint64(v, name, &key_code, errp)) {
        return;
    }
    uint8_t dst = s->cec_logical_addr ? s->cec_logical_addr : 4;
    uint8_t frame[3] = {
        (uint8_t)((CEC_TV_ADDR << 4) | (dst & 0x0Fu)),
        0x44, /* User Control Pressed */
        (uint8_t)(key_code & 0xFFu),
    };
    cec_queue_rx(s, frame, 3);
}

static void hdmi_cec_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = hdmi_cec_realize;
    device_class_set_legacy_reset(dc, hdmi_cec_reset);
    object_class_property_add_bool(klass, "hdmi-plugged",
                                   hdmi_get_plugged, hdmi_set_plugged);
    object_class_property_add_bool(klass, "tmds-active",
                                   hdmi_get_tmds, NULL);
    object_class_property_add(klass, "edid-reads", "uint64",
                              hdmi_get_u64, NULL, NULL, NULL);
    object_class_property_add(klass, "cec-tx-frames", "uint64",
                              hdmi_get_u64, NULL, NULL, NULL);
    object_class_property_add(klass, "cec-rx-frames", "uint64",
                              hdmi_get_u64, NULL, NULL, NULL);
    object_class_property_add(klass, "cec-inject-key", "uint64",
                              NULL, cec_set_inject_key, NULL, NULL);
}

static const TypeInfo hdmi_cec_info = {
    .name          = TYPE_CXD90045_HDMI_CEC,
    .parent        = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Cxd90045HdmiCecState),
    .class_init    = hdmi_cec_class_init,
};

static void hdmi_cec_register_types(void)
{
    type_register_static(&hdmi_cec_info);
}
type_init(hdmi_cec_register_types)

DeviceState *cxd90045_hdmi_cec_create(MachineState *machine,
                                      MemoryRegion *sysmem,
                                      DeviceState *vph)
{
    DeviceState *dev = qdev_new(TYPE_CXD90045_HDMI_CEC);
    Cxd90045HdmiCecState *s = CXD90045_HDMI_CEC(dev);

    s->vph = vph;
    s->hpd_plugged = true; /* Default plugged when hdmi-cec=on */
    object_property_add_child(OBJECT(machine), "cxd90045-hdmi-cec",
                              OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map_overlap(SYS_BUS_DEVICE(dev), 0, HDMI_TX_BASE, -400);
    sysbus_mmio_map_overlap(SYS_BUS_DEVICE(dev), 1, CEC_BASE, -400);
    hdmi_cec_reset(dev);
    return dev;
}
