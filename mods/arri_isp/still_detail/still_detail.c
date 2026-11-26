/*
 * StillDetail: SMOD v2 mod for ILCE-6400 still-photo detail enhancement.
 *
 * Writes the embedded natural low-halo dcv_psf_table_still.bin to
 * /tmp_bt/mods/sabin/dcv_psf_table_still.bin and bind-mounts it over
 * /usr/sabin/dcv_psf_table_still.bin on ON; detaches the bind mount on OFF.
 * Logs every lifecycle call to /tmp_bt/mods/still_detail.log.
 */
#include "still_detail_data.h"

static int sc3(int nr, unsigned a0, unsigned a1, unsigned a2) {
    register int r7 __asm__("r7") = nr;
    register unsigned r0 __asm__("r0") = a0;
    register unsigned r1 __asm__("r1") = a1;
    register unsigned r2 __asm__("r2") = a2;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return (int)r0;
}

static int sc5(int nr, unsigned a0, unsigned a1, unsigned a2, unsigned a3, unsigned a4) {
    register int r7 __asm__("r7") = nr;
    register unsigned r0 __asm__("r0") = a0;
    register unsigned r1 __asm__("r1") = a1;
    register unsigned r2 __asm__("r2") = a2;
    register unsigned r3 __asm__("r3") = a3;
    register unsigned r4 __asm__("r4") = a4;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r3), "r"(r4), "r"(r7) : "memory");
    return (int)r0;
}

static unsigned slen(const char *s) {
    unsigned n = 0;
    while (s[n]) n++;
    return n;
}

static char *append_str(char *p, const char *s) {
    while (*s) *p++ = *s++;
    return p;
}

static char *append_int(char *p, int v) {
    char tmp[16];
    int i = 0;
    unsigned uv;
    if (v < 0) {
        *p++ = '-';
        uv = (unsigned)(-v);
    } else {
        uv = (unsigned)v;
    }
    do {
        unsigned q = 0;
        while (uv >= 10U) { uv -= 10U; q++; }
        tmp[i++] = (char)('0' + uv);
        uv = q;
    } while (uv);
    while (i--) *p++ = tmp[i];
    return p;
}

static void write_log(const char *msg) {
    int fd = sc3(5 /* open */, (unsigned)"/tmp_bt/mods/still_detail.log",
                 1 /* O_WRONLY */ | 64 /* O_CREAT */ | 1024 /* O_APPEND */, 0644);
    if (fd >= 0) {
        (void)sc3(4 /* write */, (unsigned)fd, (unsigned)msg, slen(msg));
        (void)sc3(6 /* close */, (unsigned)fd, 0, 0);
    }
}

static int ensure_sabin_file(void) {
    static const char dst[] = "/tmp_bt/mods/sabin/dcv_psf_table_still.bin";
    int fd = sc3(5 /* open */, (unsigned)dst, 0 /* O_RDONLY */, 0);
    if (fd >= 0) {
        sc3(6 /* close */, (unsigned)fd, 0, 0);
        return 0;
    }
    sc3(39 /* mkdir */, (unsigned)"/tmp_bt/mods", 0755, 0);
    sc3(39 /* mkdir */, (unsigned)"/tmp_bt/mods/sabin", 0755, 0);
    fd = sc3(5 /* open */, (unsigned)dst,
             1 /* O_WRONLY */ | 64 /* O_CREAT */ | 512 /* O_TRUNC */, 0644);
    if (fd < 0) return fd;
    unsigned off = 0;
    while (off < STILL_DCV_LEN) {
        int w = sc3(4 /* write */, (unsigned)fd,
                    (unsigned)(STILL_DCV_TABLE + off), STILL_DCV_LEN - off);
        if (w <= 0) {
            sc3(6 /* close */, (unsigned)fd, 0, 0);
            return w < 0 ? w : -5;
        }
        off += (unsigned)w;
    }
    sc3(6 /* close */, (unsigned)fd, 0, 0);
    return 0;
}

__attribute__((section(".text.still_detail_life"), visibility("default")))
int still_detail_life(int on) {
    static const char src[] = "/tmp_bt/mods/sabin/dcv_psf_table_still.bin";
    static const char dst[] = "/usr/sabin/dcv_psf_table_still.bin";
    char buf[96];
    char *p = buf;
    if (on) {
        int prep = ensure_sabin_file();
        int mnt = (prep == 0) ? sc5(21 /* mount */, (unsigned)src, (unsigned)dst, 0, 4096 /* MS_BIND */, 0) : prep;
        p = append_str(p, "still_detail ON prep=");
        p = append_int(p, prep);
        p = append_str(p, " mount=");
        p = append_int(p, mnt);
        p = append_str(p, "\n");
        *p = '\0';
        write_log(buf);
        return mnt;
    } else {
        int um = sc3(52 /* umount2 */, (unsigned)dst, 2 /* MNT_DETACH */, 0);
        p = append_str(p, "still_detail OFF umount=");
        p = append_int(p, um);
        p = append_str(p, "\n");
        *p = '\0';
        write_log(buf);
        return um;
    }
}
