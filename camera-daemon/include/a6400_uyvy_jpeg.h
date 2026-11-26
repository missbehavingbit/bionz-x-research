/* Fast integer fixed-point UYVY (YUV 4:2:2) -> Baseline JPEG encoder.
 *
 * Consumes Sony LiveView packed UYVY (U0 Y0 V0 Y1, 2 bytes/pixel) directly
 * into a 4:2:2 Baseline DCT JPEG (2x1 Y, 1x1 Cb, 1x1 Cr) with zero RGB
 * conversion and zero heap allocations.
 */
#ifndef A6400_UYVY_JPEG_H
#define A6400_UYVY_JPEG_H

#include <stddef.h>
#include <stdint.h>

/* Encodes a packed UYVY image (`width` even, `height` > 0, `stride` >= width*2)
 * into `out_buf` (capacity `out_cap`). `quality` is clamped to [10, 95].
 * Returns 0 on success and writes the JPEG byte count to `*out_len`, or a
 * negative errno (-EINVAL, -ENOSPC) on error. */
int a6400_uyvy_to_jpeg(const uint8_t *uyvy,
                       uint32_t width,
                       uint32_t height,
                       uint32_t stride,
                       int quality,
                       uint8_t *out_buf,
                       size_t out_cap,
                       uint32_t *out_len);

/* Multi-threaded 3-strip DRI/RST JPEG encoder for 3-core ARM acceleration. */
int a6400_uyvy_to_jpeg_mt(const uint8_t *uyvy,
                          uint32_t width,
                          uint32_t height,
                          uint32_t stride,
                          int quality,
                          uint8_t *out_buf,
                          size_t out_cap,
                          uint32_t *out_len);

#endif /* A6400_UYVY_JPEG_H */
