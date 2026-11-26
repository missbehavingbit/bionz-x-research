/* Standalone + joint SD worker preload hook (-nostdlib ARM EABI, zero libc imports). */
#define TARGET_IM       "/usr/bin/im.elf"
#define TARGET_DEBUGGER "/setting/a6400_usb_debugd.elf"
#define DISABLE_FLAG    "/setting/disable_sd_boot"
#define WORKER          "/setting/mlv2_sd_boot.sh"

static inline long sc1(long n, long a) {
    register long r7 __asm__("r7") = n, r0 __asm__("r0") = a;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7) : "memory", "cc");
    return r0;
}
static inline long sc2(long n, long a, long b) {
    register long r7 __asm__("r7") = n, r0 __asm__("r0") = a, r1 __asm__("r1") = b;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7), "r"(r1) : "memory", "cc");
    return r0;
}
static inline long sc3(long n, long a, long b, long c) {
    register long r7 __asm__("r7") = n, r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7), "r"(r1), "r"(r2) : "memory", "cc");
    return r0;
}
static inline long sc4(long n, long a, long b, long c, long d) {
    register long r7 __asm__("r7") = n, r0 __asm__("r0") = a, r1 __asm__("r1") = b;
    register long r2 __asm__("r2") = c, r3 __asm__("r3") = d;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7), "r"(r1), "r"(r2), "r"(r3) : "memory", "cc");
    return r0;
}
static inline long sc0(long n) {
    register long r7 __asm__("r7") = n, r0 __asm__("r0") = 0;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7) : "memory", "cc");
    return r0;
}

static int eq_n(const char *a, const char *b, long n) {
    for (long i = 0; i < n; i++) if (a[i] != b[i]) return 0;
    return 1;
}

__attribute__((constructor)) static void launch(void) {
    char exe[64];
    long n = sc3(85 /* SYS_readlink */, (long)"/proc/self/exe", (long)exe, sizeof(exe));
#ifdef MLV2_TARGET_ILCE6400A
    int match_im = (n == (long)(sizeof(TARGET_IM) - 1) && eq_n(exe, TARGET_IM, n));
    int match_dbg = (n == (long)(sizeof(TARGET_DEBUGGER) - 1) && eq_n(exe, TARGET_DEBUGGER, n));
    if ((!match_im && !match_dbg) ||
#else
    if (n != (long)(sizeof(TARGET_DEBUGGER) - 1) || !eq_n(exe, TARGET_DEBUGGER, n) ||
#endif
        sc0(199 /* SYS_getuid32 */) != 0 ||
        sc2(33 /* SYS_access */, (long)DISABLE_FLAG, 0 /* F_OK */) == 0 ||
        sc2(33 /* SYS_access */, (long)WORKER, 4 /* R_OK */) != 0)
        return;

    long child = sc0(2 /* SYS_fork */);
    if (child < 0) return;
    if (child > 0) {
        while (sc4(114 /* SYS_wait4 */, child, 0, 0, 0) == -4 /* -EINTR */) {}
        return;
    }
    if (sc0(2 /* SYS_fork */) != 0) sc1(1 /* SYS_exit */, 0);
    if (sc0(66 /* SYS_setsid */) < 0) sc1(1 /* SYS_exit */, 1);
    for (long fd = 0; fd < 256; fd++) sc1(6 /* SYS_close */, fd);
    if (sc3(5 /* SYS_open */, (long)"/dev/null", 0 /* O_RDONLY */, 0) != 0 ||
        sc3(5 /* SYS_open */, (long)"/dev/null", 1 /* O_WRONLY */, 0) != 1 ||
        sc2(63 /* SYS_dup2 */, 1, 2) != 2)
        sc1(1 /* SYS_exit */, 1);

    const char *argv[3];
    argv[0] = "sh";
    argv[1] = WORKER;
    argv[2] = 0;
    const char *envp[2];
    envp[0] = "PATH=/usr/bin:/bin:/sbin:/usr/sbin";
    envp[1] = 0;
    sc3(11 /* SYS_execve */, (long)"/bin/sh", (long)argv, (long)envp);
    sc1(1 /* SYS_exit */, 127);
}
