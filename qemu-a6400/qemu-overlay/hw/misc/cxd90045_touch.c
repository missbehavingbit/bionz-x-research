/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * CXD90045 Capacitive Touchscreen Controller (FocalTech 0x20 on i2c0 + P5.9).
 *
 * Hardware contract derived from av-cam.bin Driver Table Entry 5 (0x645206f4,
 * functions 0x636d8cb4..0x636db3e0) and SYSASTRA-DSLR BK4 presets
 * (CX62600_UC2.bin / CX77600_UC2.bin):
 *   - Backup 0x00F90819 = 0       -> I2C channel 0 (0xF2401000, IRQ 199)
 *   - Backup 0x0101003E = 0x20    -> 7-bit I2C slave address 0x20
 *   - Backup 0x00F9037B/37C = 5.9 -> Touch INT GPIO P5.9 (active-low; idle 1)
 *   - Coordinate domain (0x636da604): x_byte in [0, 255], y_byte in [0, 255]
 *   - 13-byte burst read at reg 0x02..0x0E (0x636d95c8):
 *       reg[0x02] = TD_STATUS (active finger count 0..2)
 *       reg[0x03..0x08] = Finger 0, reg[0x09..0x0E] = Finger 1:
 *         pt[0] = (event_flag << 6) | ((x10 >> 8) & 0x0F)
 *                 event_flag: 0 = Put Down (contact=1), 2 = Contact (contact=1),
 *                             1 = Lift Up (contact=0), 3 = No Event (contact=0)
 *         pt[1] = x10 & 0xFF   (0x63c7ec10 extracts x_byte = x10 >> 2)
 *         pt[2] = ((touch_id & 0x0F) << 4) | ((y10 >> 8) & 0x0F)
 *         pt[3] = y10 & 0xFF   (0x63c7ec22 extracts y_byte = y10 >> 2)
 *         pt[4] = weight, pt[5] = area
 *   - reg[0x91]: heartbeat counter read by 0x636d9be8; increments on every read
 *     so 3 consecutive polls are never identical (avoids ESD chip reset).
 *   - reg[0x00]: device mode (0x00 working, 0x40 factory/test mode).
 *   - reg[0xA5]: power mode, reg[0xA6]: firmware version (0x01).
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "hw/core/qdev.h"
#include "hw/core/sysbus.h"
#include "hw/core/irq.h"
#include "hw/misc/cxd90045_i2c.h"
#include "hw/misc/cxd90045_peripherals.h"
#include "ui/console.h"
#include "ui/input.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qom/object.h"

#define TYPE_CXD90045_TOUCH "cxd90045-touch"
OBJECT_DECLARE_SIMPLE_TYPE(Cxd90045TouchState, CXD90045_TOUCH)

#define TOUCH_I2C_ADDR7       0x20
#define TOUCH_IRQ_GPIO_LINE   (0 * 32 + 12) /* P0.12 -> GIC IRQ 133 (0x85) */
#define TOUCH_RST_GPIO_PORT   5
#define TOUCH_RST_GPIO_PIN    9
#define TOUCH_GPIO_LINE       (TOUCH_RST_GPIO_PORT * 32 + TOUCH_RST_GPIO_PIN)

enum {
    FT_EVENT_PUT_DOWN = 0,
    FT_EVENT_LIFT_UP  = 1,
    FT_EVENT_CONTACT  = 2,
    FT_EVENT_NONE     = 3,
};

struct Cxd90045TouchState {
    SysBusDevice parent_obj;
    DeviceState *gpio;
    QemuInputHandlerState *input_state;

    uint8_t regs[256];
    uint8_t reg_ptr;
    uint8_t heartbeat;

    /* Host pointer state in av-cam 0..255 panel space */
    uint8_t cur_x;
    uint8_t cur_y;
    bool host_down;

    /* Sample-synchronized touch state so sub-20ms taps are never lost */
    bool pending_down;
    bool active_contact;
    bool deferred_release;
    bool pending_up;

    /* Telemetry exposed via QOM for deterministic verification */
    uint64_t i2c_bursts;
    uint64_t pointer_events;
};

static void touch_update_int_line(Cxd90045TouchState *s, bool retrigger_edge)
{
    bool assert_low;

    if (!s->gpio) {
        return;
    }
    assert_low = s->pending_down || s->active_contact || s->pending_up;
    /* P0.12 (IRQ 133 / 0x85) and P5.9: active-low (0 = active, 1 = idle).
     * Retrigger a high->low edge on P0.12 when new pointer samples arrive. */
    if (assert_low && retrigger_edge) {
        qemu_set_irq(qdev_get_gpio_in(s->gpio, TOUCH_IRQ_GPIO_LINE), 1);
    }
    qemu_set_irq(qdev_get_gpio_in(s->gpio, TOUCH_IRQ_GPIO_LINE),
                 assert_low ? 0 : 1);
    qemu_set_irq(qdev_get_gpio_in(s->gpio, TOUCH_GPIO_LINE),
                 assert_low ? 0 : 1);
}

static uint8_t axis_to_panel_byte(int64_t val)
{
    if (val <= 0) {
        return 0;
    }
    if (val >= INPUT_EVENT_ABS_MAX) {
        return 255;
    }
    return (uint8_t)((val * 255 + (INPUT_EVENT_ABS_MAX / 2)) /
                     INPUT_EVENT_ABS_MAX);
}

static void touch_pack_point(uint8_t pt[6], unsigned event_flag,
                             uint8_t touch_id, uint8_t x_byte, uint8_t y_byte,
                             uint8_t weight)
{
    uint16_t x10 = (uint16_t)x_byte << 2;
    uint16_t y10 = (uint16_t)y_byte << 2;

    pt[0] = (uint8_t)(((event_flag & 0x3) << 6) | ((x10 >> 8) & 0x0f));
    pt[1] = (uint8_t)(x10 & 0xff);
    pt[2] = (uint8_t)(((touch_id & 0x0f) << 4) | ((y10 >> 8) & 0x0f));
    pt[3] = (uint8_t)(y10 & 0xff);
    pt[4] = weight;
    pt[5] = weight ? 0x01 : 0x00;
}

static void touch_prepare_burst(Cxd90045TouchState *s)
{
    unsigned ev = FT_EVENT_NONE;
    uint8_t td_status = 0;
    uint8_t tid = 0x0f;
    uint8_t weight = 0;

    if (s->pending_down) {
        ev = FT_EVENT_PUT_DOWN;
        td_status = 1;
        tid = 0;
        weight = 0x20;
        s->pending_down = false;
        if (s->deferred_release) {
            s->deferred_release = false;
            s->active_contact = false;
            s->pending_up = true;
        } else {
            s->active_contact = true;
        }
    } else if (s->active_contact) {
        ev = FT_EVENT_CONTACT;
        td_status = 1;
        tid = 0;
        weight = 0x20;
    } else if (s->pending_up) {
        ev = FT_EVENT_LIFT_UP;
        td_status = 0;
        tid = 0;
        weight = 0x00;
        s->pending_up = false;
    }

    s->regs[0x02] = td_status;
    touch_pack_point(&s->regs[0x03], ev, tid, s->cur_x, s->cur_y, weight);
    touch_pack_point(&s->regs[0x09], FT_EVENT_NONE, 0x0f, 0, 0, 0);
    s->i2c_bursts++;
    touch_update_int_line(s, false);
}

static void touch_i2c_write(void *opaque, const uint8_t *buf, unsigned len)
{
    Cxd90045TouchState *s = opaque;

    if (len == 0) {
        return;
    }
    s->reg_ptr = buf[0];
    for (unsigned i = 1; i < len; i++) {
        uint8_t r = (uint8_t)(s->reg_ptr + (i - 1));
        uint8_t val = buf[i];

        if (r == 0x00) {
            /* 0x636db3a0: writing 0x40/0xC0 enters test mode (reads 0x40) */
            s->regs[0x00] = (val & 0x40) ? 0x40 : 0x00;
        } else {
            s->regs[r] = val;
        }
    }
}

static void touch_i2c_read(void *opaque, uint8_t *buf, unsigned len)
{
    Cxd90045TouchState *s = opaque;

    if (s->reg_ptr == 0x02 && len >= 6) {
        touch_prepare_burst(s);
    }

    for (unsigned i = 0; i < len; i++) {
        uint8_t r = (uint8_t)(s->reg_ptr + i);

        if (r == 0x91) {
            /* ESD live counter polled by 0x636d9be8 */
            s->heartbeat++;
            if (s->heartbeat == 0) {
                s->heartbeat = 1;
            }
            s->regs[0x91] = s->heartbeat;
        }
        buf[i] = s->regs[r];
    }
}

static void touch_input_event(DeviceState *dev, QemuConsole *src,
                              QemuInputEvent *evt)
{
    Cxd90045TouchState *s = CXD90045_TOUCH(dev);

    switch (evt->type) {
    case INPUT_EVENT_KIND_ABS:
        if (evt->abs.axis == INPUT_AXIS_X) {
            s->cur_x = axis_to_panel_byte(evt->abs.value);
        } else if (evt->abs.axis == INPUT_AXIS_Y) {
            s->cur_y = axis_to_panel_byte(evt->abs.value);
        }
        s->pointer_events++;
        break;
    case INPUT_EVENT_KIND_BTN:
        if (evt->btn.button == INPUT_BUTTON_LEFT) {
            bool down = evt->btn.down;
            if (down && !s->host_down) {
                s->host_down = true;
                s->pending_down = true;
                s->deferred_release = false;
                s->pending_up = false;
            } else if (!down && s->host_down) {
                s->host_down = false;
                if (s->pending_down) {
                    /* Hold release until Put Down has been read once */
                    s->deferred_release = true;
                } else if (s->active_contact) {
                    s->active_contact = false;
                    s->pending_up = true;
                }
            }
            s->pointer_events++;
        }
        break;
    default:
        break;
    }
}

static void touch_input_sync(DeviceState *dev)
{
    Cxd90045TouchState *s = CXD90045_TOUCH(dev);

    touch_update_int_line(s, true);
}

static const QemuInputHandler cxd90045_touch_handler = {
    .name  = "Sony CXD90045 capacitive touchscreen",
    .mask  = INPUT_EVENT_MASK_BTN | INPUT_EVENT_MASK_ABS,
    .event = touch_input_event,
    .sync  = touch_input_sync,
};

static void touch_reset(DeviceState *dev)
{
    Cxd90045TouchState *s = CXD90045_TOUCH(dev);

    memset(s->regs, 0, sizeof(s->regs));
    s->regs[0x00] = 0x00; /* working mode */
    s->regs[0x0a] = 0x01;
    s->regs[0x80] = 0x16; /* touch threshold */
    s->regs[0x88] = 0x06; /* active scan period */
    s->regs[0xa3] = 0x54; /* FocalTech chip vendor/id */
    s->regs[0xa5] = 0x00; /* active power mode */
    s->regs[0xa6] = 0x01; /* firmware version */
    s->regs[0xa8] = 0x11; /* FocalTech vendor ID */
    s->reg_ptr = 0;
    s->heartbeat = 1;
    s->cur_x = 0;
    s->cur_y = 0;
    s->host_down = false;
    s->pending_down = false;
    s->active_contact = false;
    s->deferred_release = false;
    s->pending_up = false;
    touch_pack_point(&s->regs[0x03], FT_EVENT_NONE, 0x0f, 0, 0, 0);
    touch_pack_point(&s->regs[0x09], FT_EVENT_NONE, 0x0f, 0, 0, 0);
    touch_update_int_line(s, false);
}

static void touch_get_u64_prop(Object *obj, Visitor *v, const char *name,
                               void *opaque, Error **errp)
{
    Cxd90045TouchState *s = CXD90045_TOUCH(obj);
    uint64_t val = 0;

    if (strcmp(name, "touch-x") == 0) {
        val = s->cur_x;
    } else if (strcmp(name, "touch-y") == 0) {
        val = s->cur_y;
    } else if (strcmp(name, "touch-bursts") == 0) {
        val = s->i2c_bursts;
    } else if (strcmp(name, "touch-events") == 0) {
        val = s->pointer_events;
    }
    visit_type_uint64(v, name, &val, errp);
}

static bool touch_get_down(Object *obj, Error **errp)
{
    Cxd90045TouchState *s = CXD90045_TOUCH(obj);

    return s->host_down || s->pending_down || s->active_contact;
}

static void touch_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    device_class_set_legacy_reset(dc, touch_reset);
    object_class_property_add(klass, "touch-x", "uint64",
                              touch_get_u64_prop, NULL, NULL, NULL);
    object_class_property_add(klass, "touch-y", "uint64",
                              touch_get_u64_prop, NULL, NULL, NULL);
    object_class_property_add(klass, "touch-bursts", "uint64",
                              touch_get_u64_prop, NULL, NULL, NULL);
    object_class_property_add(klass, "touch-events", "uint64",
                              touch_get_u64_prop, NULL, NULL, NULL);
    object_class_property_add_bool(klass, "touch-down",
                                   touch_get_down, NULL);
}

static const TypeInfo touch_info = {
    .name          = TYPE_CXD90045_TOUCH,
    .parent        = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Cxd90045TouchState),
    .class_init    = touch_class_init,
};

static void touch_register_types(void)
{
    type_register_static(&touch_info);
}
type_init(touch_register_types)

DeviceState *cxd90045_touch_create(MachineState *machine, DeviceState *i2c0,
                                   DeviceState *gpio, DeviceState *vph)
{
    DeviceState *dev = qdev_new(TYPE_CXD90045_TOUCH);
    Cxd90045TouchState *s = CXD90045_TOUCH(dev);

    s->gpio = gpio;
    object_property_add_child(OBJECT(machine), "cxd90045-touch", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);

    cxd90045_i2c_register_slave(i2c0, TOUCH_I2C_ADDR7,
                                touch_i2c_write, touch_i2c_read, s);
    s->input_state = qemu_input_handler_register(dev, &cxd90045_touch_handler);
    if (vph) {
        qemu_input_handler_bind(s->input_state, "vph", 0, &error_fatal);
    }
    qemu_input_handler_activate(s->input_state);
    touch_reset(dev);
    return dev;
}
