/* Layout shared by the SMOD v2 Film Simulation template and its offset probe.
 *
 * `film_sim_template.c` places one `struct sim_payload` in `.rodata.sim_payload`; the host packer
 * (`isp-studio/crates/bionz-preset/src/smod_v2.rs`) locates it by `SIM_PAYLOAD_MAGIC` and writes the
 * fields at the offsets recorded in `assets/film_sim_template.json`. Keep every field fixed-size and
 * naturally aligned: the probe derives the offsets from the ARM ABI, never from host assumptions.
 */
#ifndef FILM_SIM_PAYLOAD_H
#define FILM_SIM_PAYLOAD_H

#include <stdint.h>

#define SIM_PAYLOAD_MAGIC 0x4B4C5A42u /* "BZLK" */

struct sim_payload {
    uint32_t magic;
    uint32_t ec_gamma_pa;
    uint32_t fc_gamma_pa;
    uint32_t color_a0_pa;
    uint32_t color_a1_pa;
    uint32_t color_b2_pa;
    uint32_t cc_tuning_pa;
    uint32_t refadr0_pa;
    char     mod_tag[32];
    char     mod_log_path[64];
    uint32_t ec_stock[96];
    uint32_t ec_sim[96];
    uint16_t fc_stock[96];
    uint16_t fc_sim[96];
    int16_t  a0_stock[9];
    int16_t  a0_sim[9];
    int16_t  a1_stock[9];
    int16_t  a1_sim[9];
    int16_t  b2_stock[9];
    int16_t  b2_sim[9];
    uint8_t  refadr_sim[1215];
    uint8_t  _pad;
};

#endif /* FILM_SIM_PAYLOAD_H */
