/*
 * DMP SMAPH-S30 GPU: state shared with the debug frame viewer.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef HW_DISPLAY_DMP_SMAPH_H
#define HW_DISPLAY_DMP_SMAPH_H

/* PPF formats: 0 RGBA8, 1 RGB8, 2 RGB565, 3 RGB5A1, 4 RGBA4 */
typedef struct DmpPpfFrame {
    uint64_t addr;
    uint32_t w, h, fmt;
    uint64_t seq;
} DmpPpfFrame;

extern DmpPpfFrame dmp_ppf_last;

#endif
