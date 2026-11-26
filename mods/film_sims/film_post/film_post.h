/* Portable C99 / ARMv7 Cortex-A5 (VFPv3-D16, no NEON) implementation of the
 * Dehancer-derived post-gamma Halation -> Film Grain pipeline for Sony a6400
 * Photo Mode (Stage_EncodeJpeg_MainYC / Look Lab bit-exact counterpart).
 *
 * Strictly English-only comments and identifiers.
 */
#ifndef BIONZ_FILM_POST_H
#define BIONZ_FILM_POST_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FILM_POST_TILE_DIM 256
#define FILM_POST_TILE_SIZE (FILM_POST_TILE_DIM * FILM_POST_TILE_DIM)

typedef struct {
    uint8_t enabled;
    uint8_t mask_mode;
    float low;
    float high;
    float local_diffusion;
    float global_diffusion;
    float scattering;
    float scattering_amp;
    float defringe;
    float impact;
} FilmHalationParams;

typedef struct {
    uint8_t enabled;
    float amount;
    float scale;
    float shadows;
    float midtones;
    float highlights;
    float color;
    uint32_t seed;
} FilmGrainParams;

typedef struct {
    uint8_t mask[256];
    uint8_t contrast[256];
    uint8_t alpha_pow[256];
    uint8_t red_impact[256];
    uint8_t scat_r[256];
    uint8_t scat_g[256];
    uint8_t scat_b[256];
    uint8_t scattering_u8;
    uint8_t defringe_u8;
    uint8_t impact_u8;
    uint8_t mask_mode;
    uint8_t enabled;
    float local_diffusion;
    float global_diffusion;
} FilmHalationPrepared;

typedef struct {
    uint8_t pow08[256];
    uint8_t shd_w[256];
    uint8_t mid_w[256];
    uint8_t hi_w[256];
    uint8_t color_u8;
    uint8_t enabled;
    float scale;
    uint32_t seed;
} FilmGrainPrepared;

typedef struct {
    FilmHalationPrepared halation;
    FilmGrainPrepared grain;
} FilmPostPrepared;

/* Returns default disabled params matching bionz_core::film_post defaults. */
void film_post_default_halation(FilmHalationParams *out);
void film_post_default_grain(FilmGrainParams *out);

/* Generates the deterministic 256x256 seamless clustered grain tile. */
void film_post_init_tile(uint8_t tile[FILM_POST_TILE_SIZE]);

/* Precomputes all u8[256] 1D LUTs once per shot/preset change. */
void film_post_prepare_halation(const FilmHalationParams *in, FilmHalationPrepared *out);
void film_post_prepare_grain(const FilmGrainParams *in, FilmGrainPrepared *out);

/* Returns required scratch bytes for zero-malloc embedded operation. */
size_t film_post_scratch_bytes(int width, int height);

/* Applies Halation -> Film Grain in-place on tightly packed RGBA8 pixels.
 * Pass scratch=NULL to allocate/free internal working buffers automatically.
 */
int film_post_apply_rgba(
    uint8_t *rgba,
    int width,
    int height,
    const FilmPostPrepared *cfg,
    const uint8_t tile[FILM_POST_TILE_SIZE],
    void *scratch,
    size_t scratch_bytes
);

/* Applies Halation -> Film Grain in-place on semi-planar Y + interleaved CbCr 4:2:0 (NV12). */
int film_post_apply_nv12(
    uint8_t *y_plane,
    int y_stride,
    uint8_t *cbcr_plane,
    int cbcr_stride,
    int width,
    int height,
    const FilmPostPrepared *cfg,
    const uint8_t tile[FILM_POST_TILE_SIZE],
    void *scratch,
    size_t scratch_bytes
);

/* Applies Halation -> Film Grain in-place on semi-planar Y + interleaved CbCr 4:2:2 (MainYC). */
int film_post_apply_ycbcr422(
    uint8_t *y_plane,
    int y_stride,
    uint8_t *cbcr_plane,
    int cbcr_stride,
    int width,
    int height,
    const FilmPostPrepared *cfg,
    const uint8_t tile[FILM_POST_TILE_SIZE],
    void *scratch,
    size_t scratch_bytes
);

#ifdef __cplusplus
}
#endif

#endif /* BIONZ_FILM_POST_H */
