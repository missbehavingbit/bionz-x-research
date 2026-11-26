/* SilentShutterUnlock: SMOD v2 RAM-only mod for Sony ILCE-6400 (2.00 captured layout).
 *
 * Hooks ExcMgr's master exclusion evaluator in libObj.so (+0x00c708a0, ref VA 0x069888a0)
 * and clears software mutual-exclusion checks between Silent Shooting and:
 *   - Picture Profile (PARAMID_PP_MODE & PP sub-parameters)
 *   - Picture Effect (PARAMID_PICTURE_EFFECT & gamma mode)
 *   - Auto HDR / DRO (PARAMID_HDR_LEVEL, PARAMID_AUTO_HDR, PARAMID_DRO_LEVEL)
 *   - Multi Frame NR (PARAMID_ISO_SPEED_RATE, PARAMID_MULTI_FRAME_NR, PARAMID_MULTI_SHOT_NR_LEVEL, PARAMID_MULTI_FRAME_NR_LEVEL)
 *   - P/A/S/M Exposure Mode lock (PARAMID_EXPOSURE_MODE, PARAMID_MANUAL_EXPOSURE_MODE)
 *
 * Additionally clears menu grey-out inhibition bits in libSysDef.so's DefInh::sm_refTbl
 * (+0x000208b8, ref VA 0x056918b8):
 *   - All 5 Silent Shooting FactorIds (0x0a83, 0x0de2, 0x114f, 0x1296, 0x13bb) across the
 *     69 verified FeatureIds for Picture Profile (+ sub-items), Picture Effect,
 *     Auto HDR / DRO, and Multi Frame NR.
 *   - Auto / Superior Auto / SCN / Panorama exposure-mode FactorIds (0x094b..0x0962,
 *     0x0967..0x0968, 0x0b1f..0x0b4b) on Silent Shooting (FeatureId 0x0832).
 * Original bits are saved in .bss and restored verbatim on silent_off().
 *
 * Flash, BULB, Long Exposure NR, and Flickerless remain untouched (stock behavior).
 *
 * Log: /tmp_bt/mods/silent_shutter_unlock.log
 */
#include <stdint.h>

#define DEF_INH_WORDS_PER_FEAT         160u
#define FEAT_SILENT_SHUTTER            0x0832u

#define PARAM_SILENT_SHUTTER_MODE      0x9983u
#define PARAM_EXC_SILENT_SHUTTER_MODE  0xa013u

#define PARAM_EXPOSURE_MODE            0x95c8u
#define PARAM_MANUAL_EXPOSURE_MODE     0x95bdu
#define PARAM_PSEUDO_REC_MODE          0x963fu
#define PARAM_SCENE_SELECT             0x9813u
#define PARAM_CAMERA_RECORDING_MODE    0x9b7fu
#define PARAM_EXC_SHOOT_MODE           0xc00du
#define PARAM_EXC_MODE_DIAL            0xc0f1u

#define PARAM_PP_MODE                  0x9a53u
#define PARAM_PP_SUB_FIRST             0x9673u
#define PARAM_PP_SUB_LAST              0x9691u
#define PARAM_PP_CC_FIRST              0x9a47u
#define PARAM_PP_CC_LAST               0x9a52u

#define PARAM_PICTURE_EFFECT           0x9754u
#define PARAM_PICTURE_EFFECT_GAMMA     0x9a19u

#define PARAM_DRO_LEVEL                0x95cbu
#define PARAM_HDR_LEVEL                0x95ceu
#define PARAM_AUTO_HDR                 0x9818u

#define PARAM_ISO_SPEED_RATE           0x951eu
#define PARAM_MULTI_FRAME_NR           0x9811u
#define PARAM_MULTI_SHOT_NR_LEVEL      0x9989u
#define PARAM_MULTI_FRAME_NR_LEVEL     0x9999u

struct silent_unlock_state {
    uint32_t enabled;
    uint32_t bypass_count;
    uint32_t total_count;
    uint32_t inh_patched;
    uint32_t inh_cleared_count;
    uint32_t saved_silent[11]; /* 69 * 5 = 345 bits (<= 352) */
    uint32_t saved_mode[3];    /* 71 bits (<= 96) */
    uint32_t reserved;
};

_Static_assert(sizeof(struct silent_unlock_state) == 80u, "state size must match --bss 80");

extern uint8_t __smod_bss[] __attribute__((visibility("hidden")));
#define S_STATE ((volatile struct silent_unlock_state *)__smod_bss)

extern volatile uint32_t *silent_get_inh_ref_tbl(void);

struct feat_range {
    uint16_t lo;
    uint16_t hi;
};

/* All 5 Silent Shooting FactorIds in DefInh::sm_refTbl. */
static const uint16_t __attribute__((used)) SILENT_FACTORS[5] = {
    0x0a83u,
    0x0de2u,
    0x114fu,
    0x1296u,
    0x13bbu,
};

/* 69 verified FeatureIds inhibited by Silent Shooting FactorIds. */
static const struct feat_range __attribute__((used)) UNLOCK_FEAT_RANGES[9] = {
    {0x00b6u, 0x00b6u}, /* DRO / Auto HDR */
    {0x00b8u, 0x00c4u}, /* DRO / Auto HDR sub-items */
    {0x036du, 0x0376u}, /* Multi Frame NR / ISO MFNR */
    {0x0379u, 0x0379u}, /* Picture Effect */
    {0x037bu, 0x0387u}, /* Picture Effect sub-modes */
    {0x03e8u, 0x03e9u}, /* Picture Effect extra */
    {0x041bu, 0x041fu}, /* Multi Frame NR / ISO */
    {0x0509u, 0x050au}, /* Picture Profile main */
    {0x07a2u, 0x07b7u}, /* Picture Profile sub-items */
};

/* 80 Auto / Superior Auto / SCN / Panorama exposure-mode FactorIds inhibiting FeatureId 0x0832. */
static const struct feat_range __attribute__((used)) SILENT_MODE_FACTOR_RANGES[6] = {
    {0x094bu, 0x0962u}, /* 24 factors */
    {0x0967u, 0x0968u}, /* 2 factors */
    {0x0b1fu, 0x0b4bu}, /* 45 twin factors */
    {0x0b8bu, 0x0b8bu}, /* 1 factor */
    {0x0b8du, 0x0b8du}, /* 1 factor */
    {0x0cdbu, 0x0ce1u}, /* 7 third-twin factors */
};

static void patch_inh_table(volatile struct silent_unlock_state *st) {
    if (st->inh_patched) {
        return;
    }
    volatile uint32_t *tbl = silent_get_inh_ref_tbl();
    uint32_t cleared = 0u;
    uint32_t idx = 0u;
    uint32_t midx = 0u;

    for (uint32_t i = 0u; i < 11u; i++) {
        st->saved_silent[i] = 0u;
    }
    for (uint32_t i = 0u; i < 3u; i++) {
        st->saved_mode[i] = 0u;
    }

    for (uint32_t r = 0u; r < 9u; r++) {
        uint32_t lo = UNLOCK_FEAT_RANGES[r].lo;
        uint32_t hi = UNLOCK_FEAT_RANGES[r].hi;
        for (uint32_t feat = lo; feat <= hi; feat++) {
            for (uint32_t f = 0u; f < 5u; f++) {
                uint32_t fac = SILENT_FACTORS[f];
                uint32_t word = fac >> 5;
                uint32_t mask = 1u << (fac & 31u);
                volatile uint32_t *cell = tbl + feat * DEF_INH_WORDS_PER_FEAT + word;
                if ((*cell & mask) != 0u) {
                    st->saved_silent[idx >> 5] |= (1u << (idx & 31u));
                    *cell &= ~mask;
                    cleared++;
                }
                idx++;
            }
        }
    }

    for (uint32_t r = 0u; r < 6u; r++) {
        uint32_t lo = SILENT_MODE_FACTOR_RANGES[r].lo;
        uint32_t hi = SILENT_MODE_FACTOR_RANGES[r].hi;
        for (uint32_t fac = lo; fac <= hi; fac++) {
            uint32_t word = fac >> 5;
            uint32_t mask = 1u << (fac & 31u);
            volatile uint32_t *cell = tbl + (uint32_t)FEAT_SILENT_SHUTTER * DEF_INH_WORDS_PER_FEAT + word;
            if ((*cell & mask) != 0u) {
                st->saved_mode[midx >> 5] |= (1u << (midx & 31u));
                *cell &= ~mask;
                cleared++;
            }
            midx++;
        }
    }

    st->inh_cleared_count = cleared;
    st->inh_patched = 1u;
}

static void restore_inh_table(volatile struct silent_unlock_state *st) {
    if (!st->inh_patched) {
        return;
    }
    volatile uint32_t *tbl = silent_get_inh_ref_tbl();
    uint32_t idx = 0u;
    uint32_t midx = 0u;

    for (uint32_t r = 0u; r < 9u; r++) {
        uint32_t lo = UNLOCK_FEAT_RANGES[r].lo;
        uint32_t hi = UNLOCK_FEAT_RANGES[r].hi;
        for (uint32_t feat = lo; feat <= hi; feat++) {
            for (uint32_t f = 0u; f < 5u; f++) {
                if ((st->saved_silent[idx >> 5] & (1u << (idx & 31u))) != 0u) {
                    uint32_t fac = SILENT_FACTORS[f];
                    tbl[feat * DEF_INH_WORDS_PER_FEAT + (fac >> 5)] |= (1u << (fac & 31u));
                }
                idx++;
            }
        }
    }

    for (uint32_t r = 0u; r < 6u; r++) {
        uint32_t lo = SILENT_MODE_FACTOR_RANGES[r].lo;
        uint32_t hi = SILENT_MODE_FACTOR_RANGES[r].hi;
        for (uint32_t fac = lo; fac <= hi; fac++) {
            if ((st->saved_mode[midx >> 5] & (1u << (midx & 31u))) != 0u) {
                tbl[(uint32_t)FEAT_SILENT_SHUTTER * DEF_INH_WORDS_PER_FEAT + (fac >> 5)] |= (1u << (fac & 31u));
            }
            midx++;
        }
    }

    for (uint32_t i = 0u; i < 11u; i++) {
        st->saved_silent[i] = 0u;
    }
    for (uint32_t i = 0u; i < 3u; i++) {
        st->saved_mode[i] = 0u;
    }
    st->inh_cleared_count = 0u;
    st->inh_patched = 0u;
}

extern uint32_t silent_orig_check(
    void *self,
    uint32_t src_param,
    uint32_t src_val,
    uint32_t dst_param,
    uint32_t dst_val,
    uint32_t *out_buf);

static inline int is_silent_param(uint32_t p) {
    return (p == PARAM_SILENT_SHUTTER_MODE) || (p == PARAM_EXC_SILENT_SHUTTER_MODE);
}

static inline int is_unlocked_target(uint32_t p) {
    if (p == PARAM_PP_MODE ||
        (p >= PARAM_PP_SUB_FIRST && p <= PARAM_PP_SUB_LAST) ||
        (p >= PARAM_PP_CC_FIRST && p <= PARAM_PP_CC_LAST)) {
        return 1;
    }
    if (p == PARAM_PICTURE_EFFECT || p == PARAM_PICTURE_EFFECT_GAMMA) {
        return 1;
    }
    if (p == PARAM_HDR_LEVEL || p == PARAM_AUTO_HDR || p == PARAM_DRO_LEVEL) {
        return 1;
    }
    if (p == PARAM_ISO_SPEED_RATE || p == PARAM_MULTI_FRAME_NR ||
        p == PARAM_MULTI_SHOT_NR_LEVEL || p == PARAM_MULTI_FRAME_NR_LEVEL) {
        return 1;
    }
    if (p == PARAM_EXPOSURE_MODE || p == PARAM_MANUAL_EXPOSURE_MODE ||
        p == PARAM_PSEUDO_REC_MODE || p == PARAM_SCENE_SELECT ||
        p == PARAM_CAMERA_RECORDING_MODE || p == PARAM_EXC_SHOOT_MODE ||
        p == PARAM_EXC_MODE_DIAL) {
        return 1;
    }
    return 0;
}

__attribute__((visibility("default")))
uint32_t silent_unlock_hook(
    void *self,
    uint32_t src_param,
    uint32_t src_val,
    uint32_t dst_param,
    uint32_t dst_val,
    uint32_t *out_buf) {
    volatile struct silent_unlock_state *st = S_STATE;
    if (st->enabled) {
        st->total_count++;
        if ((is_silent_param(src_param) && is_unlocked_target(dst_param)) ||
            (is_unlocked_target(src_param) && is_silent_param(dst_param))) {
            st->bypass_count++;
            return 0u;
        }
    }
    return silent_orig_check(self, src_param, src_val, dst_param, dst_val, out_buf);
}

static long sc3(long n, long a, long b, long c) {
    register long r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c;
    register long r7 __asm__("r7") = n;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return r0;
}

static char *put_str(char *dst, const char *s) {
    while (*s) *dst++ = *s++;
    return dst;
}

static char *put_hex(char *dst, uint32_t v) {
    for (int i = 28; i >= 0; i -= 4) {
        uint32_t nib = (v >> i) & 0xfu;
        *dst++ = (char)(nib < 10 ? ('0' + nib) : ('a' + nib - 10));
    }
    return dst;
}

static void write_log(int on, uint32_t bypass, uint32_t total, uint32_t inh_cleared) {
    char buf[112];
    char *p = buf;
    p = put_str(p, "silent_unlock on=");
    *p++ = on ? '1' : '0';
    p = put_str(p, " bypass=0x");
    p = put_hex(p, bypass);
    p = put_str(p, " total=0x");
    p = put_hex(p, total);
    p = put_str(p, " inh=0x");
    p = put_hex(p, inh_cleared);
    *p++ = '\n';

    long fd = sc3(5 /* open */, (long)"/tmp_bt/mods/silent_shutter_unlock.log",
                  01 | 0100 | 02000 /* O_WRONLY | O_CREAT | O_APPEND */, 0644);
    if (fd >= 0) {
        sc3(4 /* write */, fd, (long)buf, (long)(p - buf));
        sc3(6 /* close */, fd, 0, 0);
    }
}

__attribute__((visibility("default")))
int silent_on(void) {
    volatile struct silent_unlock_state *st = S_STATE;
    patch_inh_table(st);
    st->enabled = 1u;
    __sync_synchronize();
    write_log(1, st->bypass_count, st->total_count, st->inh_cleared_count);
    return 0;
}

__attribute__((visibility("default")))
int silent_off(void) {
    volatile struct silent_unlock_state *st = S_STATE;
    st->enabled = 0u;
    restore_inh_table(st);
    __sync_synchronize();
    write_log(0, st->bypass_count, st->total_count, st->inh_cleared_count);
    return 0;
}
