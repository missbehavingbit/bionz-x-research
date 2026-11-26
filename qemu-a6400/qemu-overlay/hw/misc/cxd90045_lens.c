/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Sony E-Mount Body-Lens (BL) Physical Bus & Lens Motor Emulator.
 *
 * Hardware contracts derived from av-cam.bin E-Mount BL subsystem
 * (0x63827fb4..0x6382947a, 0x63a59aec..0x63a60660, 0x63d0cb58..0x63d12400),
 * BK4 preset 0x00F9050C/0D (P1.4 lens detect, GIC IRQ 141), and E0648:
 *
 *   1. Mount detect GPIO P1.4 (active-low: 0 = lens attached, 1 = bare mount).
 *   2. E-mount serial controller at UART1 (0xF2001000, size 0x1000, Linux IRQ
 *      0x97 = 151 -> GIC SPI 119).  PrimeCell ID bytes at +0xFE0..+0xFFC MUST
 *      remain 0x00 so Linux's AMBA bus skips uart1 during kernel boot and
 *      assigns ttyAM1 (/dev/ttyBT) to uart3 (0xF2003000)!
 *   3. PL080 DMAC Unit 0 at 0xF0102000 (Linux IRQ 0xAE = 174 -> GIC SPI 142):
 *      - Channel 0 (+0x100): M2P TX from guest RAM to UART1 DR (0xF2001000).
 *      - Channel 1 (+0x120): optional P2M RX from UART1 DR to guest RAM.
 *      - Channel 6 (+0x1C0): preserves E0641 work-dma M2M copy (0x1C491000)
 *        when work-dma=on is also active.
 *   4. Sony E-Mount BL Wire Framing (0x63d0ccc6):
 *      - Start byte:   0xF0
 *      - Length (LE16): N = payload_len + 8 (total wire frame bytes)
 *      - Flags/seq:    byte[3] (bits [2:0] = sequence/type)
 *      - Command ID:   byte[4] (0x01, 0x05, 0x06, 0x09, 0x0A, 0x1C, 0x1D,
 *                      0x1F, 0x3A, 0x3B, 0x3C, 0x41, 0x43, 0x44, 0x49, 0x4A,
 *                      0x5B)
 *      - Payload:      byte[5 .. N-4] (payload_len = N - 8 bytes)
 *      - Checksum16:   byte[N-3 .. N-2] = 16-bit LE sum of byte[1 .. N-4]
 *      - End byte:     byte[N-1] = 0x55
 *   5. Stateful Lens Motors (updated at 1 kHz virtual motor tick):
 *      - Focus stepping/linear motor: position (steps), target, velocity
 *        (steps/ms), limits [0 .. focus_max_steps], in-motion status flag.
 *      - Iris / aperture actuator: current F-number * 100 (e.g. 280 = F2.8)
 *        slewing smoothly toward commanded target aperture.
 *      - Power-zoom / zoom encoder: focal length (mm * 10) slewing smoothly
 *        within [min_focal_mm .. max_focal_mm] on PZ lenses (e.g. SELP1650).
 *      - Optical SteadyShot (OSS) shift stage: X/Y decentering status.
 */

#include "qemu/osdep.h"
#include "qemu/cutils.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "hw/core/sysbus.h"
#include "hw/core/irq.h"
#include "hw/misc/cxd90045_peripherals.h"
#include "system/address-spaces.h"
#include "qemu/fifo8.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"
#include "qom/object.h"

#define TYPE_CXD90045_EMOUNT_LENS "cxd90045-emount-lens"
OBJECT_DECLARE_SIMPLE_TYPE(Cxd90045LensState, CXD90045_EMOUNT_LENS)

#define LENS_UART1_BASE     0xF2001000ULL
#define LENS_UART1_SIZE     0x00001000ULL
#define LENS_PL080_BASE     0xF0102000ULL
#define LENS_PL080_SIZE     0x00001000ULL

#define LENS_DETECT_LINE    (1 * 32 + 4) /* GPIO P1.4 (active-low) */
#define LENS_RX_FIFO_CAP    2048
#define LENS_TX_BUF_CAP     1024
#define LENS_MOTOR_TICK_NS  (1000 * 1000LL) /* 1 ms motor physics step */

/* PL011-compatible UART1 bit definitions (without PrimeCell IDs at +0xFE0) */
#define UART_DR             0x00
#define UART_RSR            0x04
#define UART_FR             0x18
#define UART_IBRD           0x24
#define UART_FBRD           0x28
#define UART_LCR_H          0x2C
#define UART_CR             0x30
#define UART_IFLS           0x34
#define UART_IMSC           0x38
#define UART_RIS            0x3C
#define UART_MIS            0x40
#define UART_ICR            0x44
#define UART_DMACR          0x48

#define UART_FR_RXFE        0x10u
#define UART_FR_TXFF        0x20u
#define UART_FR_RXFF        0x40u
#define UART_FR_TXFE        0x80u

#define UART_INT_RX         0x10u
#define UART_INT_TX         0x20u
#define UART_INT_RT         0x40u

typedef struct EmountLensSpec {
    const char *id;
    const char *display_name;
    uint16_t lens_id;
    uint16_t min_focal_mm10;  /* mm * 10 (e.g. 160 = 16.0 mm) */
    uint16_t max_focal_mm10;  /* mm * 10 (e.g. 500 = 50.0 mm) */
    uint16_t min_fnum100;     /* F-number * 100 at wide end (e.g. 350 = F3.5) */
    uint16_t max_fnum100;     /* minimum aperture * 100 (e.g. 2200 = F22) */
    int32_t focus_max_steps;
    uint8_t actuator_type;    /* 0 = stepping motor, 1 = linear motor (E0649) */
    bool has_power_zoom;
    bool has_oss;
} EmountLensSpec;

static const EmountLensSpec emount_lens_catalog[] = {
    {
        .id              = "selp1650",
        .display_name    = "E PZ 16-50mm F3.5-5.6 OSS",
        .lens_id         = 0x801Du,
        .min_focal_mm10  = 160,
        .max_focal_mm10  = 500,
        .min_fnum100     = 350,
        .max_fnum100     = 2200,
        .focus_max_steps = 16384,
        .actuator_type   = 0,
        .has_power_zoom  = true,
        .has_oss         = true,
    },
    {
        .id              = "sigma1850",
        .display_name    = "Sigma 18-50mm F2.8 DC DN",
        .lens_id         = 0x8214u,
        .min_focal_mm10  = 180,
        .max_focal_mm10  = 500,
        .min_fnum100     = 280,
        .max_fnum100     = 2200,
        .focus_max_steps = 24000,
        .actuator_type   = 0,
        .has_power_zoom  = false,
        .has_oss         = false,
    },
    {
        .id              = "sel2470gm",
        .display_name    = "FE 24-70mm F2.8 GM",
        .lens_id         = 0x8027u,
        .min_focal_mm10  = 240,
        .max_focal_mm10  = 700,
        .min_fnum100     = 280,
        .max_fnum100     = 2200,
        .focus_max_steps = 32768,
        .actuator_type   = 1,
        .has_power_zoom  = false,
        .has_oss         = false,
    },
    {
        .id              = "fe50f18",
        .display_name    = "FE 50mm F1.8",
        .lens_id         = 0x8028u,
        .min_focal_mm10  = 500,
        .max_focal_mm10  = 500,
        .min_fnum100     = 180,
        .max_fnum100     = 2200,
        .focus_max_steps = 12000,
        .actuator_type   = 0,
        .has_power_zoom  = false,
        .has_oss         = false,
    },
};

struct Cxd90045LensState {
    SysBusDevice parent_obj;
    MemoryRegion uart_mr;
    MemoryRegion dmac_mr;
    qemu_irq uart_irq;
    qemu_irq dmac_irq;
    DeviceState *gpio;
    QEMUTimer *motor_timer;

    const EmountLensSpec *spec;
    bool attached;
    bool work_dma_enabled;

    /* UART1 state */
    uint32_t uart_ibrd;
    uint32_t uart_fbrd;
    uint32_t uart_lcr_h;
    uint32_t uart_cr;
    uint32_t uart_ifls;
    uint32_t uart_imsc;
    uint32_t uart_ris;
    uint32_t uart_dmacr;
    Fifo8 rx_fifo;

    /* Wire TX parser buffer (body -> lens) */
    uint8_t tx_buf[LENS_TX_BUF_CAP];
    uint32_t tx_len;

    /* PL080 DMAC Unit 0 @ 0xF0102000 (8 channels + work-dma ch6 coexistence) */
    uint32_t dmac_regs[LENS_PL080_SIZE >> 2];
    uint32_t dmac_tc_status;
    uint32_t dmac_err_status;

    /* Stateful physical lens motors */
    int32_t focus_pos;
    int32_t focus_target;
    int32_t focus_vel_steps_ms;
    bool focus_moving;

    uint16_t iris_fnum100;
    uint16_t iris_target_fnum100;
    bool iris_moving;

    uint16_t zoom_focal_mm10;
    uint16_t zoom_target_mm10;
    int16_t zoom_speed;
    bool zoom_moving;

    bool oss_active;
    int16_t oss_shift_x;
    int16_t oss_shift_y;

    /* Telemetry */
    uint64_t packets_tx;
    uint64_t packets_rx;
    uint8_t last_opcode;
};

static const EmountLensSpec *lens_lookup_spec(const char *name)
{
    if (!name || !*name || strcmp(name, "none") == 0 ||
        strcmp(name, "off") == 0) {
        return NULL;
    }
    if (strcmp(name, "on") == 0 || strcmp(name, "default") == 0) {
        return &emount_lens_catalog[0];
    }
    for (size_t i = 0; i < ARRAY_SIZE(emount_lens_catalog); i++) {
        if (g_ascii_strcasecmp(name, emount_lens_catalog[i].id) == 0) {
            return &emount_lens_catalog[i];
        }
    }
    return &emount_lens_catalog[0];
}

static uint16_t lens_checksum16(const uint8_t *buf, size_t count)
{
    uint16_t sum = 0;
    for (size_t i = 0; i < count; i++) {
        sum = (uint16_t)(sum + buf[i]);
    }
    return sum;
}

static void lens_sync_mount_gpio(Cxd90045LensState *s)
{
    if (!s->gpio) {
        return;
    }
    /* P1.4 is active-low: 0 = lens mounted, 1 = no lens */
    qemu_set_irq(qdev_get_gpio_in(s->gpio, LENS_DETECT_LINE),
                 (s->attached && s->spec) ? 0 : 1);
}

static void lens_update_uart_irq(Cxd90045LensState *s)
{
    if (!fifo8_is_empty(&s->rx_fifo)) {
        s->uart_ris |= UART_INT_RX | UART_INT_RT;
    } else {
        s->uart_ris &= ~(UART_INT_RX | UART_INT_RT);
    }
    uint32_t mis = s->uart_ris & s->uart_imsc;
    qemu_set_irq(s->uart_irq, mis != 0 ? 1 : 0);
}

static void lens_update_dmac_irq(Cxd90045LensState *s)
{
    bool pending = (s->dmac_tc_status | s->dmac_err_status) != 0;
    qemu_set_irq(s->dmac_irq, pending ? 1 : 0);
}

/* Try draining RX FIFO into any armed PL080 P2M channel targeting UART1 DR */
static void lens_try_p2m_rx_dma(Cxd90045LensState *s)
{
    if (!(s->dmac_regs[0x030 >> 2] & 1u) || fifo8_is_empty(&s->rx_fifo)) {
        return;
    }
    for (unsigned ch = 0; ch < 8; ch++) {
        if (ch == 6) {
            continue;
        }
        uint32_t base = (0x100u + ch * 0x20u) >> 2;
        uint32_t src  = s->dmac_regs[base + 0];
        uint32_t dst  = s->dmac_regs[base + 1];
        uint32_t ctrl = s->dmac_regs[base + 3];
        uint32_t cfg  = s->dmac_regs[base + 4];
        uint32_t flow = (cfg >> 11) & 0x7u;

        if (!(cfg & 1u) || flow != 2u || src != LENS_UART1_BASE) {
            continue;
        }
        uint32_t want = ctrl & 0x0FFFu;
        if (want == 0 || fifo8_num_used(&s->rx_fifo) < want) {
            continue;
        }
        uint8_t tmp[LENS_RX_FIFO_CAP];
        for (uint32_t i = 0; i < want; i++) {
            tmp[i] = fifo8_pop(&s->rx_fifo);
        }
        address_space_write(&address_space_memory, dst,
                            MEMTXATTRS_UNSPECIFIED, tmp, want);
        s->dmac_regs[base + 1] = dst + want;
        s->dmac_regs[base + 3] = ctrl & ~0x0FFFu;
        s->dmac_regs[base + 4] = cfg & ~1u;
        if ((ctrl & (1u << 31)) && (cfg & (1u << 15))) {
            s->dmac_tc_status |= (1u << ch);
        }
        lens_update_dmac_irq(s);
    }
}

/* Frame and enqueue a Sony E-mount BL response packet:
 *   0xF0 <len_lo> <len_hi> <flags> <opcode> <payload...> <csum_lo> <csum_hi> 0x55
 */
static void lens_send_bl_reply(Cxd90045LensState *s, uint8_t flags,
                               uint8_t opcode, const uint8_t *payload,
                               uint16_t payload_len)
{
    uint16_t total_len = (uint16_t)(payload_len + 8u);
    uint8_t pkt[512];

    if (!s->attached || !s->spec || total_len > sizeof(pkt) ||
        fifo8_num_free(&s->rx_fifo) < total_len) {
        return;
    }

    pkt[0] = 0xF0;
    stw_le_p(&pkt[1], total_len);
    pkt[3] = flags & 0x07u;
    pkt[4] = opcode;
    if (payload_len > 0 && payload) {
        memcpy(&pkt[5], payload, payload_len);
    }
    uint16_t csum = lens_checksum16(&pkt[1], (size_t)payload_len + 4u);
    stw_le_p(&pkt[5 + payload_len], csum);
    pkt[7 + payload_len] = 0x55;

    fifo8_push_all(&s->rx_fifo, pkt, total_len);
    s->packets_rx++;
    lens_try_p2m_rx_dma(s);
    lens_update_uart_irq(s);
}

static void lens_schedule_motors(Cxd90045LensState *s)
{
    if (s->focus_moving || s->iris_moving || s->zoom_moving) {
        timer_mod(s->motor_timer,
                  qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + LENS_MOTOR_TICK_NS);
    }
}

static void lens_motor_tick(void *opaque)
{
    Cxd90045LensState *s = opaque;

    if (!s->spec) {
        s->focus_moving = false;
        s->iris_moving = false;
        s->zoom_moving = false;
        return;
    }

    if (s->focus_moving) {
        int32_t step = s->focus_vel_steps_ms ? abs(s->focus_vel_steps_ms) : 32;
        int32_t diff = s->focus_target - s->focus_pos;
        if (abs(diff) <= step) {
            s->focus_pos = s->focus_target;
            s->focus_moving = false;
        } else {
            s->focus_pos += (diff > 0) ? step : -step;
        }
    }

    if (s->iris_moving) {
        int32_t diff = (int32_t)s->iris_target_fnum100 - (int32_t)s->iris_fnum100;
        if (abs(diff) <= 20) {
            s->iris_fnum100 = s->iris_target_fnum100;
            s->iris_moving = false;
        } else {
            s->iris_fnum100 = (uint16_t)((int32_t)s->iris_fnum100 +
                                         (diff > 0 ? 20 : -20));
        }
    }

    if (s->zoom_moving) {
        int32_t step = s->zoom_speed ? MAX(1, abs(s->zoom_speed)) : 4;
        int32_t diff = (int32_t)s->zoom_target_mm10 - (int32_t)s->zoom_focal_mm10;
        if (abs(diff) <= step) {
            s->zoom_focal_mm10 = s->zoom_target_mm10;
            s->zoom_moving = false;
        } else {
            s->zoom_focal_mm10 = (uint16_t)((int32_t)s->zoom_focal_mm10 +
                                            (diff > 0 ? step : -step));
        }
    }

    lens_schedule_motors(s);
}

static void lens_start_focus_move(Cxd90045LensState *s, int32_t target,
                                  int32_t vel_steps_ms, bool relative)
{
    if (!s->spec) {
        return;
    }
    int32_t dst = relative ? (s->focus_pos + target) : target;
    dst = CLAMP(dst, 0, s->spec->focus_max_steps);
    s->focus_target = dst;
    s->focus_vel_steps_ms = vel_steps_ms ? vel_steps_ms : 64;
    s->focus_moving = (s->focus_pos != s->focus_target);
    lens_schedule_motors(s);
}

/* Fill the cyclic lens status record (position, iris, zoom, motor busy bits) */
static uint16_t lens_fill_cyclic_status(Cxd90045LensState *s, uint8_t *out)
{
    memset(out, 0, 32);
    if (!s->spec) {
        return 32;
    }
    uint8_t status_bits = 0x01u; /* bit 0 = lens initialized/ready */
    if (s->focus_moving) {
        status_bits |= 0x02u;
    }
    if (s->iris_moving) {
        status_bits |= 0x04u;
    }
    if (s->zoom_moving) {
        status_bits |= 0x08u;
    }
    if (s->oss_active) {
        status_bits |= 0x10u;
    }
    out[0] = status_bits;
    out[1] = s->spec->actuator_type;
    stw_le_p(&out[2], s->spec->lens_id);
    stl_le_p(&out[4], (uint32_t)s->focus_pos);
    stl_le_p(&out[8], (uint32_t)s->focus_target);
    stw_le_p(&out[12], s->zoom_focal_mm10);
    stw_le_p(&out[14], s->iris_fnum100);
    stw_le_p(&out[16], (uint16_t)s->oss_shift_x);
    stw_le_p(&out[18], (uint16_t)s->oss_shift_y);
    stl_le_p(&out[20], (uint32_t)s->spec->focus_max_steps);
    return 32;
}

/* Execute one decoded BL command from av-cam (E0648 1st-gen + 2nd-gen opcodes) */
static void lens_handle_bl_command(Cxd90045LensState *s, uint8_t flags,
                                   uint8_t opcode, const uint8_t *pay,
                                   uint16_t pay_len)
{
    uint8_t rsp[192];
    memset(rsp, 0, sizeof(rsp));

    s->last_opcode = opcode;
    s->packets_tx++;

    if (!s->attached || !s->spec) {
        return;
    }

    switch (opcode) {
    case 0x01: /* Handshake / Protocol Version Init */
        rsp[0] = 0x02; /* E-mount protocol gen 2 supported */
        rsp[1] = 0x01; /* Ready */
        stw_le_p(&rsp[2], s->spec->lens_id);
        lens_send_bl_reply(s, flags, opcode, rsp, 8);
        break;

    case 0x09: /* STARTUP_INFO (E0648/E0649 capability block + command bitmap) */
    case 0x0A: {
        stw_le_p(&rsp[0x00], s->spec->lens_id);
        stw_le_p(&rsp[0x02], s->spec->min_focal_mm10);
        stw_le_p(&rsp[0x04], s->spec->max_focal_mm10);
        stw_le_p(&rsp[0x06], s->spec->min_fnum100);
        stw_le_p(&rsp[0x08], s->spec->max_fnum100);
        stl_le_p(&rsp[0x0C], (uint32_t)s->spec->focus_max_steps);
        rsp[0x10] = s->spec->has_power_zoom ? 1 : 0;
        rsp[0x11] = s->spec->has_oss ? 1 : 0;
        /* Supported 2nd-gen BL command bitmap (0x41,0x43,0x44,0x49,0x4A,0x5B) */
        rsp[0x18] = (1u << 1) | (1u << 3) | (1u << 4);
        rsp[0x19] = (1u << 1) | (1u << 2);
        rsp[0x1B] = s->spec->has_oss ? (1u << 3) : 0;
        pstrcpy((char *)&rsp[0x20], 48, s->spec->display_name);
        /* E0649: msg[+0x76] = FocusActuatorType (0 = stepping, 1 = linear) */
        rsp[0x76] = s->spec->actuator_type;
        lens_send_bl_reply(s, flags, opcode, rsp, 0x80);
        break;
    }

    case 0x3A: /* DividedInfo (segmented lens capability / distortion table) */
    case 0x3B: {
        uint8_t seg = (pay_len >= 1) ? pay[0] : 0;
        rsp[0] = seg;
        rsp[1] = 4; /* 4 total segments */
        stw_le_p(&rsp[2], s->spec->lens_id);
        if (seg == 0) {
            pstrcpy((char *)&rsp[4], 44, s->spec->display_name);
        }
        lens_send_bl_reply(s, flags, opcode, rsp, 48);
        break;
    }

    /* 1st-gen focus drive commands (E0648: 0x1C, 0x1D, 0x1F, 0x3C) */
    case 0x1C: /* Relative focus step drive */
    case 0x1D:
        if (pay_len >= 2) {
            int16_t delta = (int16_t)lduw_le_p(pay);
            int16_t spd   = (pay_len >= 4) ? (int16_t)lduw_le_p(pay + 2) : 48;
            lens_start_focus_move(s, delta, spd, true);
        }
        lens_send_bl_reply(s, flags, opcode, rsp,
                           lens_fill_cyclic_status(s, rsp));
        break;

    case 0x1F: /* Focus motor stop / hold */
        s->focus_target = s->focus_pos;
        s->focus_moving = false;
        lens_send_bl_reply(s, flags, opcode, rsp,
                           lens_fill_cyclic_status(s, rsp));
        break;

    case 0x3C: /* Absolute 1st-gen focus position */
        if (pay_len >= 4) {
            lens_start_focus_move(s, (int32_t)ldl_le_p(pay), 64, false);
        }
        lens_send_bl_reply(s, flags, opcode, rsp,
                           lens_fill_cyclic_status(s, rsp));
        break;

    /* 2nd-gen AF/motor commands (E0648: 0x41, 0x43, 0x44, 0x49, 0x4A, 0x5B) */
    case 0x41: /* POS_CYCLE_A: cyclic position report */
        lens_send_bl_reply(s, flags, opcode, rsp,
                           lens_fill_cyclic_status(s, rsp));
        break;

    case 0x43: /* MOVE_FC_2: absolute focus target + speed */
    case 0x44: /* FC_DRIVE_CYCLE: synchronous AF target */
    case 0x4A: /* MOVE_FC_CAP2: still-capture AF target */
        if (pay_len >= 4) {
            int32_t tgt = (int32_t)ldl_le_p(pay);
            int32_t vel = (pay_len >= 6) ? (int16_t)lduw_le_p(pay + 4) : 96;
            lens_start_focus_move(s, tgt, vel, false);
        } else if (pay_len >= 2) {
            lens_start_focus_move(s, (int16_t)lduw_le_p(pay), 96, false);
        }
        lens_send_bl_reply(s, flags, opcode, rsp,
                           lens_fill_cyclic_status(s, rsp));
        break;

    case 0x49: /* MoveFcVel2: velocity-mode continuous AF/MF drive */
        if (pay_len >= 2) {
            int16_t vel = (int16_t)lduw_le_p(pay);
            if (vel == 0) {
                s->focus_target = s->focus_pos;
                s->focus_moving = false;
            } else {
                s->focus_vel_steps_ms = vel;
                s->focus_target = (vel > 0) ? s->spec->focus_max_steps : 0;
                s->focus_moving = (s->focus_pos != s->focus_target);
                lens_schedule_motors(s);
            }
        }
        lens_send_bl_reply(s, flags, opcode, rsp,
                           lens_fill_cyclic_status(s, rsp));
        break;

    case 0x20: /* Iris / Aperture drive (target F-number * 100) */
    case 0x47:
        if (pay_len >= 2) {
            uint16_t f100 = lduw_le_p(pay);
            s->iris_target_fnum100 = CLAMP(f100, s->spec->min_fnum100,
                                           s->spec->max_fnum100);
            s->iris_moving = (s->iris_fnum100 != s->iris_target_fnum100);
            lens_schedule_motors(s);
        }
        lens_send_bl_reply(s, flags, opcode, rsp,
                           lens_fill_cyclic_status(s, rsp));
        break;

    case 0x28: /* Power-Zoom / Zoom focal target (mm * 10) */
    case 0x4B:
        if (s->spec->has_power_zoom && pay_len >= 2) {
            uint16_t mm10 = lduw_le_p(pay);
            s->zoom_target_mm10 = CLAMP(mm10, s->spec->min_focal_mm10,
                                        s->spec->max_focal_mm10);
            s->zoom_speed = (pay_len >= 4) ? (int16_t)lduw_le_p(pay + 2) : 5;
            s->zoom_moving = (s->zoom_focal_mm10 != s->zoom_target_mm10);
            lens_schedule_motors(s);
        }
        lens_send_bl_reply(s, flags, opcode, rsp,
                           lens_fill_cyclic_status(s, rsp));
        break;

    case 0x5B: /* IS_LENS_SHIFT (Optical SteadyShot control) */
        if (s->spec->has_oss && pay_len >= 1) {
            s->oss_active = (pay[0] & 1u) != 0;
            if (pay_len >= 5 && s->oss_active) {
                s->oss_shift_x = (int16_t)lduw_le_p(pay + 1);
                s->oss_shift_y = (int16_t)lduw_le_p(pay + 3);
            } else if (!s->oss_active) {
                s->oss_shift_x = 0;
                s->oss_shift_y = 0;
            }
        }
        lens_send_bl_reply(s, flags, opcode, rsp,
                           lens_fill_cyclic_status(s, rsp));
        break;

    case 0x05: /* Cyclic VSYNC container (sub-TLVs: [sub_op, sub_len, ...]) */
    case 0x06: {
        uint16_t pos = 0;
        while (pos + 2 <= pay_len) {
            uint8_t sub_op  = pay[pos];
            uint8_t sub_len = pay[pos + 1];
            if (sub_op == 0 || pos + 2 + sub_len > pay_len) {
                break;
            }
            const uint8_t *sp = &pay[pos + 2];
            if ((sub_op == 0x43 || sub_op == 0x44 || sub_op == 0x4A) &&
                sub_len >= 4) {
                int32_t tgt = (int32_t)ldl_le_p(sp);
                int32_t vel = (sub_len >= 6) ? (int16_t)lduw_le_p(sp + 4) : 96;
                lens_start_focus_move(s, tgt, vel, false);
            } else if ((sub_op == 0x20 || sub_op == 0x47) && sub_len >= 2) {
                uint16_t f100 = lduw_le_p(sp);
                s->iris_target_fnum100 = CLAMP(f100, s->spec->min_fnum100,
                                               s->spec->max_fnum100);
                s->iris_moving = (s->iris_fnum100 != s->iris_target_fnum100);
                lens_schedule_motors(s);
            } else if ((sub_op == 0x28 || sub_op == 0x4B) &&
                       s->spec->has_power_zoom && sub_len >= 2) {
                uint16_t mm10 = lduw_le_p(sp);
                s->zoom_target_mm10 = CLAMP(mm10, s->spec->min_focal_mm10,
                                            s->spec->max_focal_mm10);
                s->zoom_moving = (s->zoom_focal_mm10 != s->zoom_target_mm10);
                lens_schedule_motors(s);
            } else if (sub_op == 0x5B && s->spec->has_oss && sub_len >= 1) {
                s->oss_active = (sp[0] & 1u) != 0;
            }
            pos += (uint16_t)(2u + sub_len);
        }
        lens_send_bl_reply(s, flags, opcode, rsp,
                           lens_fill_cyclic_status(s, rsp));
        break;
    }

    default:
        lens_send_bl_reply(s, flags, opcode, rsp,
                           lens_fill_cyclic_status(s, rsp));
        break;
    }
}

/* Feed one byte written to UART1 DR (either CPU PIO or PL080 M2P DMA) */
static void lens_feed_tx_byte(Cxd90045LensState *s, uint8_t b)
{
    if (s->tx_len == 0 && b != 0xF0) {
        return; /* Hunt for start-of-frame 0xF0 */
    }
    if (s->tx_len < sizeof(s->tx_buf)) {
        s->tx_buf[s->tx_len++] = b;
    } else {
        s->tx_len = 0;
        return;
    }

    if (s->tx_len >= 3) {
        uint16_t frame_len = lduw_le_p(&s->tx_buf[1]);
        if (frame_len < 8 || frame_len > sizeof(s->tx_buf)) {
            s->tx_len = 0;
            return;
        }
        if (s->tx_len == frame_len) {
            uint16_t pay_len = (uint16_t)(frame_len - 8u);
            uint16_t want_csum = lens_checksum16(&s->tx_buf[1], pay_len + 4u);
            uint16_t got_csum = lduw_le_p(&s->tx_buf[5 + pay_len]);

            if (s->tx_buf[frame_len - 1] == 0x55 && want_csum == got_csum) {
                lens_handle_bl_command(s, s->tx_buf[3], s->tx_buf[4],
                                       &s->tx_buf[5], pay_len);
            }
            s->tx_len = 0;
        }
    }
}

static uint64_t lens_uart1_read(void *opaque, hwaddr off, unsigned size)
{
    Cxd90045LensState *s = opaque;
    uint32_t v = 0;

    switch (off & ~3ULL) {
    case UART_DR:
        if (!fifo8_is_empty(&s->rx_fifo)) {
            v = fifo8_pop(&s->rx_fifo);
            lens_update_uart_irq(s);
        }
        break;
    case UART_RSR:
        v = 0;
        break;
    case UART_FR:
        v = UART_FR_TXFE |
            (fifo8_is_empty(&s->rx_fifo) ? UART_FR_RXFE : 0u) |
            (fifo8_is_full(&s->rx_fifo) ? UART_FR_RXFF : 0u);
        break;
    case UART_IBRD:  v = s->uart_ibrd; break;
    case UART_FBRD:  v = s->uart_fbrd; break;
    case UART_LCR_H: v = s->uart_lcr_h; break;
    case UART_CR:    v = s->uart_cr; break;
    case UART_IFLS:  v = s->uart_ifls; break;
    case UART_IMSC:  v = s->uart_imsc; break;
    case UART_RIS:   v = s->uart_ris; break;
    case UART_MIS:   v = s->uart_ris & s->uart_imsc; break;
    case UART_DMACR: v = s->uart_dmacr; break;
    default:
        /* +0xFE0..+0xFFC MUST read 0 so Linux AMBA skips uart1! */
        v = 0;
        break;
    }
    return v;
}

static void lens_uart1_write(void *opaque, hwaddr off, uint64_t val,
                             unsigned size)
{
    Cxd90045LensState *s = opaque;
    uint32_t v = (uint32_t)val;

    switch (off & ~3ULL) {
    case UART_DR:
        lens_feed_tx_byte(s, (uint8_t)(v & 0xFFu));
        s->uart_ris |= UART_INT_TX;
        lens_update_uart_irq(s);
        break;
    case UART_RSR:
        break;
    case UART_IBRD:  s->uart_ibrd = v & 0xFFFFu; break;
    case UART_FBRD:  s->uart_fbrd = v & 0x3Fu; break;
    case UART_LCR_H: s->uart_lcr_h = v & 0xFFu; break;
    case UART_CR:    s->uart_cr = v & 0xFFFFu; break;
    case UART_IFLS:  s->uart_ifls = v & 0x3Fu; break;
    case UART_IMSC:
        s->uart_imsc = v & 0x7FFu;
        lens_update_uart_irq(s);
        break;
    case UART_ICR:
        s->uart_ris &= ~(v & 0x7FFu);
        lens_update_uart_irq(s);
        break;
    case UART_DMACR:
        s->uart_dmacr = v & 0x07u;
        lens_try_p2m_rx_dma(s);
        break;
    default:
        break;
    }
}

static const MemoryRegionOps lens_uart1_ops = {
    .read = lens_uart1_read,
    .write = lens_uart1_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

/* PL080 DMAC Unit 0 @ 0xF0102000: supports BL M2P/P2M on ch0..5 + E0641 ch6 */
static uint64_t lens_dmac_read(void *opaque, hwaddr off, unsigned size)
{
    Cxd90045LensState *s = opaque;

    switch (off) {
    case 0x000: /* DMACIntStatus */
        return s->dmac_tc_status | s->dmac_err_status;
    case 0x004: /* DMACIntTCStatus */
        return s->dmac_tc_status;
    case 0x00C: /* DMACIntErrorStatus */
        return s->dmac_err_status;
    case 0x01C: /* DMACEnbldChns (synchronous completion -> ch6 clear) */
        return s->dmac_regs[0x01C >> 2] & ~0x40u;
    default:
        return s->dmac_regs[off >> 2];
    }
}

static void lens_dmac_run_channel(Cxd90045LensState *s, unsigned ch)
{
    uint32_t base = (0x100u + ch * 0x20u) >> 2;
    uint32_t src  = s->dmac_regs[base + 0];
    uint32_t dst  = s->dmac_regs[base + 1];
    uint32_t ctrl = s->dmac_regs[base + 3];
    uint32_t cfg  = s->dmac_regs[base + 4];

    if (!(s->dmac_regs[0x030 >> 2] & 1u)) {
        return;
    }

    /* Preserve E0641 work-dma channel 6 M2M copy when work_dma_enabled */
    if (ch == 6 && s->work_dma_enabled) {
        if ((ctrl & 0xFFFFF000u) == 0x1C491000u && ((src | dst) & 3u) == 0) {
            uint32_t words = (ctrl & 0xFFFu) ? (ctrl & 0xFFFu) : 0x1000u;
            uint8_t buf[4096];
            for (uint32_t off = 0, rem = words * 4u; rem > 0; ) {
                uint32_t chunk = MIN(rem, (uint32_t)sizeof(buf));
                address_space_read(&address_space_memory, src + off,
                                   MEMTXATTRS_UNSPECIFIED, buf, chunk);
                address_space_write(&address_space_memory, dst + off,
                                    MEMTXATTRS_UNSPECIFIED, buf, chunk);
                off += chunk;
                rem -= chunk;
            }
        }
        s->dmac_regs[base + 4] = 0;
        return;
    }

    uint32_t flow = (cfg >> 11) & 0x7u;
    uint32_t count = ctrl & 0x0FFFu;

    if (flow == 1u && dst == LENS_UART1_BASE && count > 0) {
        /* M2P TX from guest RAM buffer to E-Mount UART1 DR */
        uint8_t buf[4096];
        for (uint32_t off = 0, rem = count; rem > 0; ) {
            uint32_t chunk = MIN(rem, (uint32_t)sizeof(buf));
            address_space_read(&address_space_memory, src + off,
                               MEMTXATTRS_UNSPECIFIED, buf, chunk);
            for (uint32_t i = 0; i < chunk; i++) {
                lens_feed_tx_byte(s, buf[i]);
            }
            off += chunk;
            rem -= chunk;
        }
        s->dmac_regs[base + 0] = src + count;
        s->dmac_regs[base + 3] = ctrl & ~0x0FFFu;
        s->dmac_regs[base + 4] = cfg & ~1u;
        if ((ctrl & (1u << 31)) || (cfg & (1u << 15))) {
            s->dmac_tc_status |= (1u << ch);
            lens_update_dmac_irq(s);
        }
    } else if (flow == 2u && src == LENS_UART1_BASE) {
        /* P2M RX from UART1 DR: drain immediately if bytes already queued */
        lens_try_p2m_rx_dma(s);
    }
}

static void lens_dmac_write(void *opaque, hwaddr off, uint64_t val,
                            unsigned size)
{
    Cxd90045LensState *s = opaque;
    uint32_t v = (uint32_t)val;

    switch (off) {
    case 0x008: /* DMACIntTCClear */
        s->dmac_tc_status &= ~v;
        lens_update_dmac_irq(s);
        return;
    case 0x010: /* DMACIntErrClr */
        s->dmac_err_status &= ~v;
        lens_update_dmac_irq(s);
        return;
    default:
        break;
    }

    s->dmac_regs[off >> 2] = v;

    if (off >= 0x100 && off < 0x200 && ((off - 0x100) & 0x1F) == 0x10) {
        unsigned ch = (unsigned)((off - 0x100) >> 5);
        if (v & 1u) {
            lens_dmac_run_channel(s, ch);
        }
    }
}

static const MemoryRegionOps lens_dmac_ops = {
    .read = lens_dmac_read,
    .write = lens_dmac_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
};

static void lens_reset(DeviceState *dev)
{
    Cxd90045LensState *s = CXD90045_EMOUNT_LENS(dev);

    timer_del(s->motor_timer);
    fifo8_reset(&s->rx_fifo);
    s->tx_len = 0;
    s->uart_ibrd = 0;
    s->uart_fbrd = 0;
    s->uart_lcr_h = 0;
    s->uart_cr = 0x0300;
    s->uart_ifls = 0x12;
    s->uart_imsc = 0;
    s->uart_ris = 0;
    s->uart_dmacr = 0;
    memset(s->dmac_regs, 0, sizeof(s->dmac_regs));
    s->dmac_tc_status = 0;
    s->dmac_err_status = 0;

    s->focus_pos = 0;
    s->focus_target = 0;
    s->focus_vel_steps_ms = 64;
    s->focus_moving = false;
    s->iris_fnum100 = s->spec ? s->spec->min_fnum100 : 350;
    s->iris_target_fnum100 = s->iris_fnum100;
    s->iris_moving = false;
    s->zoom_focal_mm10 = s->spec ? s->spec->min_focal_mm10 : 160;
    s->zoom_target_mm10 = s->zoom_focal_mm10;
    s->zoom_speed = 0;
    s->zoom_moving = false;
    s->oss_active = s->spec ? s->spec->has_oss : false;
    s->oss_shift_x = 0;
    s->oss_shift_y = 0;

    lens_sync_mount_gpio(s);
    lens_update_uart_irq(s);
    lens_update_dmac_irq(s);
}

static void lens_realize(DeviceState *dev, Error **errp)
{
    Cxd90045LensState *s = CXD90045_EMOUNT_LENS(dev);

    fifo8_create(&s->rx_fifo, LENS_RX_FIFO_CAP);
    s->motor_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, lens_motor_tick, s);
    memory_region_init_io(&s->uart_mr, OBJECT(dev), &lens_uart1_ops, s,
                          "cxd90045.emount-uart1", LENS_UART1_SIZE);
    memory_region_init_io(&s->dmac_mr, OBJECT(dev), &lens_dmac_ops, s,
                          "cxd90045.emount-pl080", LENS_PL080_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->uart_mr);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->dmac_mr);
    sysbus_init_irq(SYS_BUS_DEVICE(dev), &s->uart_irq);
    sysbus_init_irq(SYS_BUS_DEVICE(dev), &s->dmac_irq);
}

static char *lens_get_model(Object *obj, Error **errp)
{
    Cxd90045LensState *s = CXD90045_EMOUNT_LENS(obj);
    return g_strdup(s->spec ? s->spec->id : "none");
}

static bool lens_get_attached(Object *obj, Error **errp)
{
    Cxd90045LensState *s = CXD90045_EMOUNT_LENS(obj);
    return s->attached && s->spec != NULL;
}

static void lens_set_attached(Object *obj, bool val, Error **errp)
{
    Cxd90045LensState *s = CXD90045_EMOUNT_LENS(obj);

    if (s->attached != val) {
        s->attached = val;
        lens_sync_mount_gpio(s);
    }
}

static bool lens_get_oss(Object *obj, Error **errp)
{
    return CXD90045_EMOUNT_LENS(obj)->oss_active;
}

static void lens_get_u64(Object *obj, Visitor *v, const char *name,
                         void *opaque, Error **errp)
{
    Cxd90045LensState *s = CXD90045_EMOUNT_LENS(obj);
    uint64_t val = 0;

    if (strcmp(name, "focus-position") == 0) {
        val = (uint64_t)s->focus_pos;
    } else if (strcmp(name, "focus-target") == 0) {
        val = (uint64_t)s->focus_target;
    } else if (strcmp(name, "aperture-f100") == 0) {
        val = s->iris_fnum100;
    } else if (strcmp(name, "zoom-focal-mm10") == 0) {
        val = s->zoom_focal_mm10;
    } else if (strcmp(name, "bl-packets-tx") == 0) {
        val = s->packets_tx;
    } else if (strcmp(name, "bl-packets-rx") == 0) {
        val = s->packets_rx;
    }
    visit_type_uint64(v, name, &val, errp);
}

static void lens_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = lens_realize;
    device_class_set_legacy_reset(dc, lens_reset);
    object_class_property_add_str(klass, "lens-model", lens_get_model, NULL);
    object_class_property_add_bool(klass, "lens-attached",
                                   lens_get_attached, lens_set_attached);
    object_class_property_add_bool(klass, "oss-active", lens_get_oss, NULL);
    object_class_property_add(klass, "focus-position", "uint64",
                              lens_get_u64, NULL, NULL, NULL);
    object_class_property_add(klass, "focus-target", "uint64",
                              lens_get_u64, NULL, NULL, NULL);
    object_class_property_add(klass, "aperture-f100", "uint64",
                              lens_get_u64, NULL, NULL, NULL);
    object_class_property_add(klass, "zoom-focal-mm10", "uint64",
                              lens_get_u64, NULL, NULL, NULL);
    object_class_property_add(klass, "bl-packets-tx", "uint64",
                              lens_get_u64, NULL, NULL, NULL);
    object_class_property_add(klass, "bl-packets-rx", "uint64",
                              lens_get_u64, NULL, NULL, NULL);
}

static const TypeInfo lens_info = {
    .name          = TYPE_CXD90045_EMOUNT_LENS,
    .parent        = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Cxd90045LensState),
    .class_init    = lens_class_init,
};

static void lens_register_types(void)
{
    type_register_static(&lens_info);
}
type_init(lens_register_types)

DeviceState *cxd90045_emount_lens_create(MachineState *machine,
                                         MemoryRegion *sysmem,
                                         qemu_irq uart1_irq,
                                         qemu_irq dmac_irq,
                                         DeviceState *gpio,
                                         const char *lens_model,
                                         bool work_dma_enabled)
{
    DeviceState *dev = qdev_new(TYPE_CXD90045_EMOUNT_LENS);
    Cxd90045LensState *s = CXD90045_EMOUNT_LENS(dev);

    s->gpio = gpio;
    s->spec = lens_lookup_spec(lens_model);
    s->attached = (s->spec != NULL);
    s->work_dma_enabled = work_dma_enabled;

    object_property_add_child(OBJECT(machine), "cxd90045-emount-lens",
                              OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map_overlap(SYS_BUS_DEVICE(dev), 0, LENS_UART1_BASE, 1);
    sysbus_mmio_map_overlap(SYS_BUS_DEVICE(dev), 1, LENS_PL080_BASE, 1);
    sysbus_connect_irq(SYS_BUS_DEVICE(dev), 0, uart1_irq);
    sysbus_connect_irq(SYS_BUS_DEVICE(dev), 1, dmac_irq);
    lens_reset(dev);
    return dev;
}
