/*
 * Sony CXD90045 (BIONZ X) Opt-In Live-View Front-End & AIF DMA Models
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef HW_MISC_CXD90045_CAM_H
#define HW_MISC_CXD90045_CAM_H

#include "qemu/osdep.h"
#include "qapi/error.h"

#define TYPE_CXD90045_CAMPIG "cxd90045-campig"
#define TYPE_CXD90045_AIF    "cxd90045-aif"

typedef enum Cxd90045LiveViewKind {
    CXD90045_LV_NONE = 0,
    CXD90045_LV_BARS,
    CXD90045_LV_RAMP,
    CXD90045_LV_PGM,
} Cxd90045LiveViewKind;

typedef struct Cxd90045LiveViewSource {
    Cxd90045LiveViewKind kind;
    uint32_t width;
    uint32_t height;
    uint8_t *pixels;
} Cxd90045LiveViewSource;

bool cxd90045_liveview_source_parse(Cxd90045LiveViewSource *src,
                                    const char *spec, Error **errp);
uint8_t cxd90045_liveview_sample(const Cxd90045LiveViewSource *src,
                                 uint32_t x, uint32_t y,
                                 uint32_t w, uint32_t h);

void cxd90045_campig_set_source(DeviceState *dev,
                                const Cxd90045LiveViewSource *src);
void cxd90045_aif_set_source(DeviceState *dev,
                             const Cxd90045LiveViewSource *src);

#endif /* HW_MISC_CXD90045_CAM_H */
