#ifndef ARRI_ISP_STAGE_H
#define ARRI_ISP_STAGE_H

#include <stddef.h>
#include <stdint.h>

enum {
    ARRI_GAMMA_BYTES = 192,
    ARRI_MATRIX_BYTES = 18,
    ARRI_ESRAM_BYTES = 1620,
    ARRI_A6LT_BYTES = 1864
};

enum arri_preset_id { ARRI_NONE, ARRI_709, ARRI_AWG3 };

struct arri_tables {
    uint8_t gamma[ARRI_GAMMA_BYTES];
    uint8_t matrix[ARRI_MATRIX_BYTES];
    uint8_t esram[ARRI_ESRAM_BYTES];
};

struct arri_loaded {
    enum arri_preset_id id;
    struct arri_tables tables;
};

/* Semantic state, supplied by an adapter that has independently verified the ABI. */
struct arri_pp_state {
    uint8_t enabled;
    uint8_t profile;
    uint8_t target_profile;
    uint8_t movie;
    uint8_t slog3;
    uint8_t sgamut3_cine;
    uint8_t knee_off;
    uint8_t neutral_saturation;
    uint8_t neutral_phase;
    uint8_t playback;
};

enum arri_stage_result { ARRI_BYPASS, ARRI_STAGED };

/* Load once outside the ISP task into unpublished RAM. On failure, out is unchanged. */
int arri_load(struct arri_loaded *out, enum arri_preset_id id, const uint8_t *file, size_t size);

/* The destination is a private staging aggregate. Caller owns publication and lifetime. */
enum arri_stage_result arri_stage(struct arri_tables *pending, const struct arri_loaded *loaded,
                                  const struct arri_pp_state *pp);

#endif
