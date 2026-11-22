/* Film Texture: SMOD v2 lifecycle mod for ILCE-6100 and ILCE-6400 (identical /usr/sabin/ paths).
 * Bind-mounts tuned /tmp_bt/mods/sabin/{dcv_psf,spica_musashi}_*.bin over /usr/sabin/
 * on ON, and detaches (umount2 MNT_DETACH) on OFF.
 * Expects staged files in /tmp_bt/mods/sabin/ (c_tap zero file created automatically if missing).
 */
#include <stdint.h>

static long sc3(long n, long a, long b, long c) {
    register long r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c;
    register long r7 __asm__("r7") = n;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return r0;
}

static long sc5(long n, long a, long b, long c, long d, long e) {
    register long r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c,
                  r3 __asm__("r3") = d, r4 __asm__("r4") = e;
    register long r7 __asm__("r7") = n;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r3), "r"(r4), "r"(r7) : "memory");
    return r0;
}

#ifndef SABIN_DIR
#define SABIN_DIR "/tmp_bt/mods/sabin"
#endif
#ifndef LOG_PATH
#define LOG_PATH "/tmp_bt/mods/film_texture.log"
#endif
#ifndef LOG_TAG
#define LOG_TAG "film_texture"
#endif

static const char *sabin_src(int i) {
    switch (i) {
        case 0:  return SABIN_DIR "/dcv_psf_table_still.bin";
        case 1:  return SABIN_DIR "/dcv_psf_table_movie_bayer.bin";
        case 2:  return SABIN_DIR "/dcv_psf_table_movie_4k.bin";
        case 3:
        case 4:
        case 5:
        case 6:  return SABIN_DIR "/spica_c_zero.bin";
        case 7:  return SABIN_DIR "/spica_musashi_still_y_tap_step1.bin";
        case 8:  return SABIN_DIR "/spica_musashi_still_y_tap_step2.bin";
        case 9:  return SABIN_DIR "/spica_musashi_mov_4k_y_tap_step1.bin";
        default: return SABIN_DIR "/spica_musashi_mov_4k_y_tap_step2.bin";
    }
}

static const char *sabin_dst(int i) {
    switch (i) {
        case 0:  return "/usr/sabin/dcv_psf_table_still.bin";
        case 1:  return "/usr/sabin/dcv_psf_table_movie_bayer.bin";
        case 2:  return "/usr/sabin/dcv_psf_table_movie_4k.bin";
        case 3:  return "/usr/sabin/spica_musashi_still_c_tap_step1.bin";
        case 4:  return "/usr/sabin/spica_musashi_still_c_tap_step2.bin";
        case 5:  return "/usr/sabin/spica_musashi_mov_4k_c_tap_step1.bin";
        case 6:  return "/usr/sabin/spica_musashi_mov_4k_c_tap_step2.bin";
        case 7:  return "/usr/sabin/spica_musashi_still_y_tap_step1.bin";
        case 8:  return "/usr/sabin/spica_musashi_still_y_tap_step2.bin";
        case 9:  return "/usr/sabin/spica_musashi_mov_4k_y_tap_step1.bin";
        default: return "/usr/sabin/spica_musashi_mov_4k_y_tap_step2.bin";
    }
}

static void ensure_zero_c_tap(void) {
    const char *p = SABIN_DIR "/spica_c_zero.bin";
    long fd = sc3(5, (long)p, 0, 0);
    if (fd >= 0) { sc3(6, fd, 0, 0); return; }
    fd = sc3(5, (long)p, 01 | 0100 | 01000, 0644);
    if (fd < 0) return;
    uint8_t zeros[256];
    for (int i = 0; i < 256; i++) zeros[i] = 0;
    for (uint32_t left = 40000u; left > 0; ) {
        uint32_t c = left > 256u ? 256u : left;
        if (sc3(4, fd, (long)zeros, c) <= 0) break;
        left -= c;
    }
    sc3(6, fd, 0, 0);
}

__attribute__((section(".text.arri_life"), used))
void texture_life(int on) {
    char res = 'u';
    if (on) {
        ensure_zero_c_tap();
        long probe = sc3(5, (long)sabin_src(0), 0, 0);
        if (probe < 0) {
            res = 'n';
        } else {
            sc3(6, probe, 0, 0);
            res = 'm';
            for (int i = 0; i < 11; i++) {
                long r = sc5(21 /* mount */, (long)sabin_src(i), (long)sabin_dst(i), 0, 4096 /* MS_BIND */, 0);
                if (r < 0) res = 'e';
            }
        }
    } else {
        for (int i = 0; i < 11; i++)
            sc3(52 /* umount2 */, (long)sabin_dst(i), 2 /* MNT_DETACH */, 0);
    }
    char line[] = LOG_TAG " on=? sabin=?\n";
    int n = (int)sizeof(line) - 1;
    line[n - 10] = (char)('0' + (on ? 1 : 0));
    line[n - 2] = res;
    long fd = sc3(5, (long)LOG_PATH, 01 | 0100 | 02000, 0600);
    if (fd >= 0) { sc3(4, fd, (long)line, n); sc3(6, fd, 0, 0); }
}
