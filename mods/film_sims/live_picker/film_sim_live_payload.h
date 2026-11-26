/* Layout of the Live Picker payload (SMOD v2 "FilmSimulations.mod").
 *
 * `film_sim_live_template.c` keeps one `struct live_payload` in `.rodata.live_payload`; the host
 * packer (`isp-studio/crates/bionz-preset/src/smod_v2.rs`, `emit_live_smod_v2`) writes the body's
 * physical addresses, the look names, the stock tables and up to LIVE_MAX_LOOKS preset tables into it.
 * Slot 0 is always "Stock (Off)"; slots 1..n are the looks in library order.
 *
 * Keep the arrays fixed-size and naturally aligned. The offsets are read back from the compiled
 * object through an ARM `offsetof` probe, so nothing here is duplicated by hand.
 */
#ifndef FILM_SIM_LIVE_PAYLOAD_H
#define FILM_SIM_LIVE_PAYLOAD_H

#include <stdint.h>

#define LIVE_PAYLOAD_MAGIC 0x4C564C31u /* "LVL1" */
/* Looks the payload can carry; slot 0 is "Stock (Off)", so the picker shows up to 16 entries. */
#define LIVE_MAX_LOOKS 15
#define LIVE_MAX_SLOTS (LIVE_MAX_LOOKS + 1)

struct live_payload {
    uint32_t magic;
    uint32_t ec_gamma_pa;
    uint32_t fc_gamma_pa;
    uint32_t color_a0_pa;
    uint32_t color_a1_pa;
    uint32_t color_b2_pa;
    uint32_t cc_tuning_pa;
    uint32_t refadr0_pa;
    /* LIVE_N_PRESETS = n_looks + 1 (slot 0 is Stock). */
    uint32_t n_presets;
    uint32_t n_looks;
    char     log_path[64];
    char     names[LIVE_MAX_SLOTS][16];
    int8_t   lut_idx[LIVE_MAX_SLOTS];
    uint32_t ec_stock[96];
    uint16_t fc_stock[96];
    int16_t  a0_stock[9];
    int16_t  a1_stock[9];
    int16_t  b2_stock[9];
    uint32_t ec[LIVE_MAX_LOOKS][96];
    uint16_t fc[LIVE_MAX_LOOKS][96];
    int16_t  a0[LIVE_MAX_LOOKS][9];
    int16_t  a1[LIVE_MAX_LOOKS][9];
    int16_t  b2[LIVE_MAX_LOOKS][9];
    uint8_t  refadr[LIVE_MAX_LOOKS][1215];
};

#endif /* FILM_SIM_LIVE_PAYLOAD_H */
