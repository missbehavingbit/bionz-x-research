/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * CXD90045 opt-in multi-body peripheral constructors and helpers:
 *   - Capacitive touchscreen IC (FocalTech 0x20 on i2c0 + P5.9 GPIO + VPH ABS/BTN)
 *   - BCM4339 Wi-Fi (SDIO on mmc1 @ 0xF0301000, IRQ 221) & Bluetooth H4 HCI (uart3)
 *   - HDMI TX (@ 0xF2A40000, DDC EDID + HPD + VPH hdmi-tmds) & CEC (@ 0xF3C10000)
 *   - AudioIp (@ 0xF3420000..0xF3428FFF, capture/playback DMA rings + PCM mic)
 *   - E-mount Body-Lens BL controller (UART1 @ 0xF2001000 + PL080 M2P + P1.4 + motors)
 */
#ifndef HW_MISC_CXD90045_PERIPHERALS_H
#define HW_MISC_CXD90045_PERIPHERALS_H

#include "hw/core/boards.h"
#include "hw/core/irq.h"
#include "chardev/char.h"
#include "system/memory.h"

/* FocalTech-compatible 0x20 touch IC on i2c0 + P5.9 INT + QEMU pointer input */
DeviceState *cxd90045_touch_create(MachineState *machine, DeviceState *i2c0,
                                   DeviceState *gpio, DeviceState *vph);

/* BCM4339 Wi-Fi SDIO host+card at mmc1 (0xF0301000, IRQ 221) + BT H4 chardev */
DeviceState *cxd90045_bcm4339_sdio_create(MemoryRegion *sysmem, hwaddr base,
                                          qemu_irq irq, DeviceState *gpio);
Chardev *cxd90045_bcm4339_bt_chardev_new(const char *id);

/* CXD90045 HDMI TX (0xF2A40000) + CEC (0xF3C10000) wired to VPH hdmi-tmds */
DeviceState *cxd90045_hdmi_cec_create(MachineState *machine,
                                      MemoryRegion *sysmem,
                                      DeviceState *vph);

/* CXD90045 AudioIp (0xF3420000..0xF3428FFF) with DMA ring + synthetic PCM mic */
DeviceState *cxd90045_audio_create(MachineState *machine, MemoryRegion *sysmem,
                                   const char *input_mode, bool mic_plugged);

/* Sony E-Mount Body-Lens (BL) controller & stepping/iris/zoom motor model */
DeviceState *cxd90045_emount_lens_create(MachineState *machine,
                                         MemoryRegion *sysmem,
                                         qemu_irq uart1_irq,
                                         qemu_irq dmac_irq,
                                         DeviceState *gpio,
                                         const char *lens_model,
                                         bool work_dma_enabled);

#endif /* HW_MISC_CXD90045_PERIPHERALS_H */
