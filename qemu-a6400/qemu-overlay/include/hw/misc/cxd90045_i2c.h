/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * CXD90045 I2C controller slave registration interface (0xF2401000 / 0xF2401800).
 */
#ifndef HW_MISC_CXD90045_I2C_H
#define HW_MISC_CXD90045_I2C_H

#include "hw/core/qdev.h"

#define TYPE_CXD90045_I2C "cxd90045-i2c"

typedef void (*CxdI2cSlaveWriteFn)(void *opaque, const uint8_t *buf,
                                   unsigned len);
typedef void (*CxdI2cSlaveReadFn)(void *opaque, uint8_t *buf, unsigned len);

void cxd90045_i2c_register_slave(DeviceState *dev, uint8_t addr7,
                                 CxdI2cSlaveWriteFn write_fn,
                                 CxdI2cSlaveReadFn read_fn,
                                 void *opaque);

#endif /* HW_MISC_CXD90045_I2C_H */
