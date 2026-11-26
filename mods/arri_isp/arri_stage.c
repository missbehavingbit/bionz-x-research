#include "arri_stage.h"

enum {
    HEADER = 32,
    MATRIX = HEADER + ARRI_GAMMA_BYTES,
    PAD = MATRIX + ARRI_MATRIX_BYTES,
    ESRAM = PAD + 2
};

_Static_assert(ESRAM + ARRI_ESRAM_BYTES == ARRI_A6LT_BYTES, "A6LT layout");

static uint16_t le16(const uint8_t *p) {
    return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}

static uint32_t le32(const uint8_t *p) {
    return (uint32_t)le16(p) | ((uint32_t)le16(p + 2) << 16);
}

static uint32_t payload_crc(const uint8_t *p, size_t n) {
    uint32_t crc = UINT32_C(0xffffffff);
    for (size_t i = 0; i < n; ++i) {
        crc ^= p[i];
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (UINT32_C(0xedb88320) & (0u - (crc & 1u)));
    }
    return ~crc;
}

static int valid_tables(const uint8_t *file) {
    if (le32(file) != UINT32_C(0x544c3641) || le16(file + 4) != 1 ||
        le16(file + 6) != HEADER || le32(file + 8) != 1 ||
        le16(file + 12) != 96 || le16(file + 14) != 4095 || le16(file + 16) != 1024 ||
        file[18] != 5 || file[19] != 9 || file[20] != 9 || file[21] != 0 ||
        le32(file + 22) != ARRI_A6LT_BYTES - HEADER || le16(file + 30) != 0 ||
        le16(file + PAD) != 0)
        return 0;
    if (payload_crc(file + HEADER, ARRI_A6LT_BYTES - HEADER) != le32(file + 26))
        return 0;

    uint16_t previous = 0;
    for (int off = HEADER; off < MATRIX; off += 2) {
        uint16_t sample = le16(file + off);
        if (sample < previous || sample > 4095)
            return 0;
        previous = sample;
    }
    for (int off = MATRIX; off < PAD; off += 6) {
        int32_t sum = 0;
        for (int col = 0; col < 6; col += 2) {
            uint16_t coeff = le16(file + off + col);
            sum += (int32_t)coeff - (coeff >= 0x8000u ? 65536 : 0);
        }
        if (sum != 1024)
            return 0;
    }
    for (int off = ESRAM; off < ARRI_A6LT_BYTES; off += 4) {
        if (le32(file + off) & ~UINT32_C(0x007f7f7f))
            return 0;
    }
    return 1;
}

static void copy_bytes(uint8_t *out, const uint8_t *in, size_t n) {
    for (size_t i = 0; i < n; ++i)
        out[i] = in[i];
}

int arri_load(struct arri_loaded *out, enum arri_preset_id id, const uint8_t *file, size_t size) {
    if (!out || !file || size != ARRI_A6LT_BYTES ||
        (id != ARRI_709 && id != ARRI_AWG3) || !valid_tables(file))
        return 0;

    copy_bytes(out->tables.gamma, file + HEADER, ARRI_GAMMA_BYTES);
    copy_bytes(out->tables.matrix, file + MATRIX, ARRI_MATRIX_BYTES);
    copy_bytes(out->tables.esram, file + ESRAM, ARRI_ESRAM_BYTES);
    out->id = id;
    return 1;
}

enum arri_stage_result arri_stage(struct arri_tables *pending, const struct arri_loaded *loaded,
                                  const struct arri_pp_state *pp) {
    if (!pending || !loaded || !pp || (loaded->id != ARRI_709 && loaded->id != ARRI_AWG3) ||
        pp->enabled != 1 || pp->target_profile == 0 || pp->profile != pp->target_profile ||
        pp->movie != 1 || pp->slog3 != 1 || pp->sgamut3_cine != 1 || pp->knee_off != 1 ||
        pp->neutral_saturation != 1 || pp->neutral_phase != 1 || pp->playback != 0)
        return ARRI_BYPASS;

    copy_bytes(pending->gamma, loaded->tables.gamma, ARRI_GAMMA_BYTES);
    copy_bytes(pending->matrix, loaded->tables.matrix, ARRI_MATRIX_BYTES);
    copy_bytes(pending->esram, loaded->tables.esram, ARRI_ESRAM_BYTES);
    return ARRI_STAGED;
}
