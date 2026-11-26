/* WiFiSync: 5 GHz background .JPG/.ARW transfer for Sony ILCE-6400 im.elf.
 *
 * SMOD v2 mod (RAM-only). Responsibilities:
 *   - one ON/OFF row in the MLV2 Mods tab plus a SMNU submenu for the AP
 *     channel/width and standby mode;
 *   - ConPutWriteStill interposition: O(1) capture notification;
 *   - a single worker thread (SYS_clone) that owns the HTTP server, the
 *     read-only Sony Fsys scan/CRC pass and the power state machine;
 *   - 5 GHz AP control through /tmp_bt/wifisync/ap.sh (stock bcmdhd +
 *     wpa_supplicant + dhcpd), with the WL_REG_ON GPIO ioctl done in C.
 *
 * Safety boundaries:
 *   - no /dev/mem, no flash access, no SD mount;
 *   - every written path is under /tmp_bt/wifisync/;
 *   - config and the AP script are read from A:/mods/ read-only;
 *   - Fsys entry points are identity-checked (library size + FNV + prologue);
 *   - any failed check leaves the mod OFF (fail-closed).
 *
 * ILCE-6400 2.00 reference layout (tools/native_camera/mlv2/reference_layout_ilce6400.txt):
 *   libmpr.so          base 0x07144000
 *   libInfraFsys.so    base 0x04cde000
 */
#include <stdint.h>

#include "wifisync_core.h"

/* ---------------------------------------------------------------- addresses */

#define MPR_REF_BASE        0x07144000u
#define MPR_CON_PUT_WS_OFF  0x00869D24u   /* MprMonCtrl::ConPutWriteStill (live camera) */
#define MPR_REF_CON_PUT_WS  (MPR_REF_BASE + MPR_CON_PUT_WS_OFF + 1u)

#define FSY_REF_BASE        0x04CDE000u
#define FSY_OPEN_OFF        0x0001BF84u
#define FSY_READ_OFF        0x00018588u
#define FSY_CLOSE_OFF       0x00018388u
#define FSY_STAT_OFF        0x0001A544u
#define FSY_FINDFIRST_OFF   0x00019478u
#define FSY_FINDNEXT_OFF    0x00019634u

/* Pinned identities of the live ILCE-6400 2.00 libraries. */
#define LIBMPR_LEN          12665872u
#define LIBMPR_FNV4096      0x409F7B77u
#define LIBFSYS_LEN         411504u
#define LIBFSYS_FNV4096     0xE1C5B626u

/* ------------------------------------------------------------ raw syscalls */

#define SYS_exit        1
#define SYS_fork        2
#define SYS_read        3
#define SYS_write       4
#define SYS_open        5
#define SYS_close       6
#define SYS_lseek       19
#define SYS_ioctl       54
#define SYS_chmod       15
#define SYS_mkdir       39
#define SYS_wait4       114
#define SYS_clone       120
#define SYS_execve      11
#define SYS_nanosleep   162
#define SYS_poll        168
#define SYS_mmap2       192
#define SYS_clock_gettime 263
#define SYS_socket      281
#define SYS_bind        282
#define SYS_listen      284
#define SYS_accept      285
#define SYS_sendto      290
#define SYS_recvfrom    292
#define SYS_setsockopt  294

#define O_RDONLY        0
#define O_WR_APPEND     0x441
#define O_WR_TRUNC      0x241

#define AF_INET         2
#define SOCK_STREAM     1
#define SOL_SOCKET      1
#define SO_REUSEADDR    2
#define POLLIN          1
#define MSG_NOSIGNAL    0x4000

#define CLONE_VM        0x00000100
#define CLONE_FS        0x00000200
#define CLONE_FILES     0x00000400
#define CLONE_SIGHAND   0x00000800
#define CLONE_THREAD    0x00010000
#define CLONE_SYSVSEM   0x00040000
#define CLONE_FLAGS     (CLONE_VM | CLONE_FS | CLONE_FILES | CLONE_SIGHAND | CLONE_THREAD | CLONE_SYSVSEM)

struct sockaddr_in {
    uint16_t sin_family;
    uint16_t sin_port;      /* network order */
    uint32_t sin_addr;      /* network order */
    uint8_t  pad[8];
};

struct pollfd_ {
    int fd;
    short events;
    short revents;
};

struct timespec_ {
    int32_t tv_sec;
    int32_t tv_nsec;
};

static inline int sc3(int n, int a, int b, int c) {
    register int r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c;
    register int r7 __asm__("r7") = n;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return r0;
}

static inline int sc4(int n, int a, int b, int c, int d) {
    register int r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c;
    register int r3 __asm__("r3") = d, r7 __asm__("r7") = n;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r3), "r"(r7) : "memory");
    return r0;
}

static inline int sc6(int n, int a, int b, int c, int d, int e, int f) {
    register int r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c;
    register int r3 __asm__("r3") = d, r4 __asm__("r4") = e, r5 __asm__("r5") = f;
    register int r7 __asm__("r7") = n;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r3), "r"(r4), "r"(r5), "r"(r7)
                     : "memory");
    return r0;
}

static int ml_open(const char *path, int flags) { return sc3(SYS_open, (int)path, flags, 0); }
static int ml_read(int fd, void *buf, uint32_t n) { return sc3(SYS_read, fd, (int)buf, (int)n); }
static int ml_write(int fd, const void *buf, uint32_t n) { return sc3(SYS_write, fd, (int)buf, (int)n); }
static int ml_close(int fd) { return sc3(SYS_close, fd, 0, 0); }
static void ml_exit(int code) { (void)sc3(SYS_exit, code, 0, 0); for (;;) {} }
static int ml_mkdir(const char *path) { return sc3(SYS_mkdir, (int)path, 0755, 0); }

static uint32_t ml_mmap(uint32_t len) {
    int r = sc6(SYS_mmap2, 0, (int)((len + 0xFFFu) & ~0xFFFu), 7 /*RWX*/, 0x22 /*ANON|PRIV*/,
                -1, 0);
    return (r < 0) ? 0u : (uint32_t)r;
}

static int ml_write_all(int fd, const void *buf, uint32_t len) {
    const uint8_t *p = (const uint8_t *)buf;
    while (len) {
        int n = ml_write(fd, p, len);
        if (n <= 0) return -1;
        p += n;
        len -= (uint32_t)n;
    }
    return 0;
}

static int write_file(const char *path, const char *data, uint32_t len, int mode) {
    int fd = ml_open(path, O_WR_TRUNC);
    if (fd < 0) return -1;
    if (ml_write_all(fd, data, len) != 0) { ml_close(fd); return -1; }
    ml_close(fd);
    if (mode) (void)sc3(SYS_chmod, (int)path, 0755, 0);
    return 0;
}

static int read_file(const char *path, char *buf, uint32_t cap, uint32_t *out_len) {
    int fd = ml_open(path, O_RDONLY);
    uint32_t len = 0;
    int n;
    if (fd < 0) return -1;
    while (len + 1u < cap) {
        n = ml_read(fd, buf + len, cap - 1u - len);
        if (n <= 0) break;
        len += (uint32_t)n;
    }
    ml_close(fd);
    buf[len] = 0;
    if (out_len) *out_len = len;
    return 0;
}

static uint32_t file_len(const char *path) {
    int fd = ml_open(path, O_RDONLY);
    int r;
    if (fd < 0) return 0;
    r = sc3(SYS_lseek, fd, 0, 2 /*SEEK_END*/);
    ml_close(fd);
    return r < 0 ? 0u : (uint32_t)r;
}

/* ---- thread creation ---------------------------------------------------- */

static int ml_clone(void (*fn)(void), uint32_t stack_top) {
    register int r0 __asm__("r0") = CLONE_FLAGS;
    register int r1 __asm__("r1") = (int)stack_top;
    register int r2 __asm__("r2") = 0;
    register int r3 __asm__("r3") = 0;
    register int r4 __asm__("r4") = (int)fn;
    register int r7 __asm__("r7") = SYS_clone;
    __asm__ volatile(
        "svc #0\n\t"
        "cmp r0, #0\n\t"
        "bne 1f\n\t"
        "mov r0, r4\n\t"
        "blx r0\n\t"
        "mov r7, #1\n\t"
        "mov r0, #0\n\t"
        "svc #0\n\t"
        "1:\n\t"
        : "+r"(r0)
        : "r"(r1), "r"(r2), "r"(r3), "r"(r4), "r"(r7)
        : "memory", "lr", "cc");
    return r0;
}

/* ---- clock/log ---------------------------------------------------------- */

static uint32_t platform_now(void) {
    struct timespec_ ts;
    register int r0 __asm__("r0") = 1;   /* CLOCK_MONOTONIC */
    register int r1 __asm__("r1") = (int)&ts;
    register int r7 __asm__("r7") = SYS_clock_gettime;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r7) : "memory");
    if (r0 != 0) return 0;
    return (uint32_t)((uint32_t)ts.tv_sec * 1000u + (uint32_t)(ts.tv_nsec / 1000000));
}

static uint32_t platform_now_cb(void *ctx) { (void)ctx; return platform_now(); }

static void platform_log_raw(const char *msg, uint32_t value) {
    char line[96];
    uint32_t i = 0, v = value;
    char digits[10];
    int d = 0;
    int fd;
    while (*msg && i + 12 < sizeof(line)) line[i++] = *msg++;
    line[i++] = ' ';
    if (!v) digits[d++] = '0';
    while (v) { digits[d++] = (char)('0' + (v % 10u)); v /= 10u; }
    while (d) line[i++] = digits[--d];
    line[i++] = '\n';
    fd = ml_open("/tmp_bt/mods/wifisync.log", O_WR_APPEND);
    if (fd < 0) return;
    (void)ml_write_all(fd, line, i);
    ml_close(fd);
}

static void platform_log(const char *msg, uint32_t value) { platform_log_raw(msg, value); }
static void platform_log_cb(void *ctx, const char *msg, uint32_t value) {
    (void)ctx;
    platform_log_raw(msg, value);
}

/* ---- Sony Fsys read-only backend --------------------------------------- */

typedef int  (*fsys_open_t)(const char *path, uint32_t *handle, int flags);
typedef int  (*fsys_read_t)(int handle, void *buf, int32_t len);
typedef int  (*fsys_close_t)(int handle);
typedef int  (*fsys_stat_t)(const char *path, void *statbuf);
typedef int  (*fsys_findfirst_t)(const char *pattern, uint32_t attr_mask, void *find_info);
typedef int  (*fsys_findnext_t)(void *find_info);

/* Every writable byte lives in the SMOD v2 bss region mapped after the code,
 * so the mod file stays small and the loader's --bss budget is explicit. */
extern uint8_t __smod_bss[] __attribute__((visibility("hidden")));

struct ws_mod_state {
    uint32_t   magic;
    uint8_t    cfg[8];              /* SMNU values: [0] standby, [1] width, [2] channel */
    ws_server  srv;
    uint8_t    io[WS_CHUNK_DEFAULT];
    uint8_t    find_buf[2112] __attribute__((aligned(4)));
    struct {
        fsys_open_t      open;
        fsys_read_t      read;
        fsys_close_t     close;
        fsys_stat_t      stat;
        fsys_findfirst_t find_first;
        fsys_findnext_t  find_next;
        uint32_t         ready;
    } fsys;
    int32_t    listen_fd;
    uint32_t   worker_stack;
    uint32_t   stop_flag;
    uint32_t   worker_running;
    uint32_t   ready;
    uint32_t   saved_ack;
    uint32_t   ap_up;
    uint32_t   standby_deep;
    uint32_t   custom_folder;
    uint32_t   disc_max_idx;
    uint32_t   last_cap_seen;
    uint32_t   last_cap_event_ms;
    uint32_t   rediscover_pending;
};

#define WS_STATE ((struct ws_mod_state *)__smod_bss)
#define WS_BSS_MAGIC 0x53595357u   /* "WSYS" */

/* The build script reads this 4-byte rodata value to pass the exact --bss
 * budget (linked at 0, so the symbol value is also the file offset). */
__attribute__((used, visibility("hidden")))
const uint32_t ws_bss_size_bytes = (uint32_t)sizeof(struct ws_mod_state);

static int ends_with(const char *s, const char *suffix) {
    uint32_t sl = 0, pl = 0;
    while (s[sl]) sl++;
    while (suffix[pl]) pl++;
    if (sl < pl) return 0;
    for (uint32_t i = 0; i < pl; i++)
        if (s[sl - pl + i] != suffix[i]) return 0;
    return 1;
}

static uint32_t parse_hex_u32(const char *s, uint32_t *advance) {
    uint32_t v = 0, n = 0;
    while (*s) {
        char c = *s++;
        int h;
        if (c >= '0' && c <= '9') h = c - '0';
        else if (c >= 'a' && c <= 'f') h = c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') h = c - 'A' + 10;
        else break;
        v = (v << 4) | (uint32_t)h;
        n++;
    }
    if (advance) *advance = n;
    return v;
}

/* Parse one /proc/self/maps line: return the mapping start when the file
 * offset is 0 and the path ends with `suffix`, else 0. */
static uint32_t parse_maps_line(const char *line, const char *suffix) {
    const char *p = line;
    uint32_t adv = 0;
    uint32_t start;
    if (!ends_with(line, suffix)) return 0;
    start = parse_hex_u32(p, &adv);
    p += adv;
    while (*p == '-') p++;
    (void)parse_hex_u32(p, &adv);
    p += adv;
    while (*p == ' ') p++;
    while (*p && *p != ' ') p++;       /* perms */
    while (*p == ' ') p++;
    if (*p == '0' && p[1] == '0' && p[2] == '0' && p[3] == '0' &&
        p[4] == '0' && p[5] == '0' && p[6] == '0' && p[7] == '0' &&
        (p[8] == ' ' || p[8] == '\n' || p[8] == 0))
        return start;
    return 0;
}

/* First mapping at file offset 0 of `suffix` in /proc/self/maps. */
static uint32_t find_lib_base(const char *suffix) {
    int fd = ml_open("/proc/self/maps", O_RDONLY);
    char buf[512];
    char line[256];
    uint32_t lp = 0, found = 0;
    int n, i;
    if (fd < 0) return 0;
    while (!found && (n = ml_read(fd, buf, sizeof(buf))) > 0) {
        for (i = 0; i < n && !found; i++) {
            char ch = buf[i];
            if (ch == '\n') {
                line[lp] = 0;
                found = parse_maps_line(line, suffix);
                lp = 0;
            } else if (lp + 1u < sizeof(line)) {
                line[lp++] = ch;
            }
        }
    }
    ml_close(fd);
    return found;
}

static uint32_t fnv_file_prefix(const char *path, uint32_t bytes) {
    int fd = ml_open(path, O_RDONLY);
    char buf[256];
    uint32_t h = 2166136261u, total = 0;
    if (fd < 0) return 0;
    while (total < bytes) {
        uint32_t want = bytes - total;
        int n;
        if (want > sizeof(buf)) want = sizeof(buf);
        n = ml_read(fd, buf, want);
        if (n <= 0) break;
        for (int i = 0; i < n; i++) h = (h ^ (uint8_t)buf[i]) * 16777619u;
        total += (uint32_t)n;
    }
    ml_close(fd);
    return total == bytes ? h : 0;
}

static int same_code(uint32_t address, const char *bytes, uint32_t n) {
    const volatile uint8_t *p = (const volatile uint8_t *)(address & ~1u);
    for (uint32_t i = 0; i < n; i++)
        if (p[i] != (uint8_t)bytes[i]) return 0;
    return 1;
}

static int fsys_resolve(void) {
    uint32_t base, mpr_base;
    if (WS_STATE->fsys.ready) return 0;
    if (file_len("/usr/lib/libInfraFsys.so") != LIBFSYS_LEN ||
        file_len("/usr/lib/libmpr.so") != LIBMPR_LEN) {
        platform_log("lib_size_refused", 0);
        return -1;
    }
    if (fnv_file_prefix("/usr/lib/libInfraFsys.so", 4096) != LIBFSYS_FNV4096 ||
        fnv_file_prefix("/usr/lib/libmpr.so", 4096) != LIBMPR_FNV4096) {
        platform_log("lib_prefix_refused", 0);
        return -1;
    }
    base = find_lib_base("/usr/lib/libInfraFsys.so");
    mpr_base = find_lib_base("/usr/lib/libmpr.so");
    platform_log("fsys_base", base);
    platform_log("mpr_base", mpr_base);
    if (!base || !mpr_base) return -1;
    WS_STATE->fsys.open       = (fsys_open_t)((base + FSY_OPEN_OFF) | 1u);
    WS_STATE->fsys.read       = (fsys_read_t)((base + FSY_READ_OFF) | 1u);
    WS_STATE->fsys.close      = (fsys_close_t)((base + FSY_CLOSE_OFF) | 1u);
    WS_STATE->fsys.stat       = (fsys_stat_t)((base + FSY_STAT_OFF) | 1u);
    WS_STATE->fsys.find_first = (fsys_findfirst_t)((base + FSY_FINDFIRST_OFF) | 1u);
    WS_STATE->fsys.find_next  = (fsys_findnext_t)((base + FSY_FINDNEXT_OFF) | 1u);
    if (!same_code((uint32_t)WS_STATE->fsys.open,       "\x2d\xe9\xf0\x4f\x89\x46", 6) ||
        !same_code((uint32_t)WS_STATE->fsys.read,       "\x2d\xe9\xf0\x4f\x0e\x46", 6) ||
        !same_code((uint32_t)WS_STATE->fsys.close,      "\x7a\x49\x2d\xe9\xf0\x41\x05\x46", 8) ||
        !same_code((uint32_t)WS_STATE->fsys.stat,       "\x2d\xe9\xf0\x41\x0e\x46", 6) ||
        !same_code((uint32_t)WS_STATE->fsys.find_first, "\x2d\xe9\xf0\x43\x88\x46", 6) ||
        !same_code((uint32_t)WS_STATE->fsys.find_next,  "\x53\x49\x2d\xe9\xf8\x43", 6)) {
        platform_log("fsys_prologue_refused", 0);
        WS_STATE->fsys.open = 0; WS_STATE->fsys.read = 0; WS_STATE->fsys.close = 0; WS_STATE->fsys.stat = 0;
        WS_STATE->fsys.find_first = 0; WS_STATE->fsys.find_next = 0;
        return -1;
    }
    if (!same_code(mpr_base + MPR_CON_PUT_WS_OFF, "\x2d\xe9\xf0\x41\x0d\x46\x00\xaf", 8)) {
        /* On ON the loader has already installed the 12-byte jump; accept that
         * exact template shape and reject anything else. */
        if (!same_code(mpr_base + MPR_CON_PUT_WS_OFF,
                       "\xdf\xf8\x04\xf0\x00\xbf\x00\xbf", 8)) {
            platform_log("mpr_hook_refused", 0);
            return -1;
        }
        platform_log("mpr_hook_installed", 0);
    }
    WS_STATE->fsys.ready = 1;
    return 0;
}

static int wfs_stat(void *ctx, const char *path, uint32_t *size, uint32_t *mtime) {
    uint8_t st[32];
    (void)ctx;
    if (!WS_STATE->fsys.ready || WS_STATE->fsys.stat(path, st) != 0) return -1;
    *size = (uint32_t)st[0] | ((uint32_t)st[1] << 8) | ((uint32_t)st[2] << 16) |
            ((uint32_t)st[3] << 24);
    *mtime = (uint32_t)st[0x18] | ((uint32_t)st[0x19] << 8) |
             ((uint32_t)st[0x1a] << 16) | ((uint32_t)st[0x1b] << 24);
    return 0;
}

static int wfs_open(void *ctx, const char *path, void **handle) {
    uint32_t h = 0;
    (void)ctx;
    if (!WS_STATE->fsys.ready) return -1;
    if (WS_STATE->fsys.open(path, &h, 1 /* read-only: bit0 */) != 0) return -2;
    *handle = (void *)h;
    return 0;
}

static int wfs_read(void *ctx, void *handle, void *buf, uint32_t len) {
    (void)ctx;
    if (!WS_STATE->fsys.ready) return -1;
    return WS_STATE->fsys.read((int)(uint32_t)handle, buf, (int32_t)len);
}

static int wfs_close(void *ctx, void *handle) {
    (void)ctx;
    if (!WS_STATE->fsys.ready) return -1;
    return WS_STATE->fsys.close((int)(uint32_t)handle);
}

/* ---- GPIO (WL_REG_ON) --------------------------------------------------- */
/* /dev/gpio ioctl 0xC0104702 (GPIO_CMD_SET_MODE), group 9 bit 18: mode 9 =
 * output high, 17 = output low. Same contract as the physically verified
 * custom_os/launcher/wlan_gpio.c helper; no /dev/mem. */
struct sony_gpio_req {
    uint32_t reserved0;
    uint8_t group;
    uint8_t pad1[3];
    uint8_t bit;
    uint8_t pad2[3];
    uint32_t mode;
};

#define SONY_GPIO_CMD_SET_MODE 0xC0104702u
#define GPIO_MODE_OUT_HIGH 9u
#define GPIO_MODE_OUT_LOW  17u
#define WLAN_GPIO_GROUP 9u
#define WLAN_GPIO_BIT   18u

static int gpio_mode(uint32_t mode) {
    int fd = sc3(SYS_open, (int)"/dev/gpio", 2 /*O_RDWR*/, 0);
    struct sony_gpio_req req;
    int rc;
    if (fd < 0) return -1;
    req.reserved0 = 0;
    req.group = WLAN_GPIO_GROUP;
    req.pad1[0] = req.pad1[1] = req.pad1[2] = 0;
    req.bit = WLAN_GPIO_BIT;
    req.pad2[0] = req.pad2[1] = req.pad2[2] = 0;
    req.mode = mode;
    rc = sc3(SYS_ioctl, fd, (int)SONY_GPIO_CMD_SET_MODE, (int)&req);
    ml_close(fd);
    return rc;
}

static void delay_ms(uint32_t ms) {
    struct timespec_ ts;
    ts.tv_sec = (int32_t)(ms / 1000u);
    ts.tv_nsec = (int32_t)((ms % 1000u) * 1000000u);
    (void)sc3(SYS_nanosleep, (int)&ts, 0, 0);
}

/* ---- AP script -----------------------------------------------------------
 * The canonical script lives in mods/wifisync/wifisync_ap.sh; the build
 * regenerates this header so the embedded fallback cannot drift. */
#include "wifisync_ap_script.h"

/* ---- AP control --------------------------------------------------------- */

static int run_ap(const char *action) {
    const char *argv[4];
    char env0[] = "PATH=/usr/bin:/bin:/usr/sbin:/sbin";
    char *envp[2];
    int pid, status = 0;
    argv[0] = "/bin/sh";
    argv[1] = "/tmp_bt/wifisync/ap.sh";
    argv[2] = action;
    argv[3] = 0;
    envp[0] = env0;
    envp[1] = 0;
    pid = sc3(SYS_fork, 0, 0, 0);
    if (pid == 0) {
        (void)sc3(SYS_execve, (int)argv[0], (int)argv, (int)envp);
        ml_exit(127);
    }
    if (pid < 0) return -1;
    if (sc4(SYS_wait4, pid, (int)&status, 0, 0) < 0) return -1;
    {
        int rc = ((status & 0x7F) == 0) ? ((status >> 8) & 0xFF) : (128 + (status & 0x7F));
        platform_log("ap_rc", (uint32_t)rc);
        return rc == 0 ? 0 : -1;
    }
}

static int wlan_mods_loaded(void) {
    int fd = ml_open("/proc/modules", O_RDONLY);
    char buf[256];
    char line[96];
    uint32_t lp = 0;
    int found = 0;
    int n, i;
    if (fd < 0) return 0;
    while (!found && (n = ml_read(fd, buf, sizeof(buf))) > 0) {
        for (i = 0; i < n && !found; i++) {
            char ch = buf[i];
            if (ch == '\n') {
                line[lp] = 0;
                if (ws_strneq(line, "bcmdhd ", 7) ||
                    ws_strneq(line, "mmcioWrapper ", 13)) {
                    found = 1;
                }
                lp = 0;
            } else if (lp + 1u < sizeof(line)) {
                line[lp++] = ch;
            }
        }
    }
    ml_close(fd);
    return found;
}

static int gpio_power_off_safe(void) {
    if (wlan_mods_loaded()) {
        platform_log("gpio_skip_loaded", 1);
        return -1;
    }
    return gpio_mode(GPIO_MODE_OUT_LOW);
}

static int platform_set_power(void *ctx, uint32_t state) {
    int rc;
    (void)ctx;
    if (state == WS_POWER_OFF) {
        (void)run_ap("down");
        WS_STATE->ap_up = 0;
        (void)gpio_power_off_safe();
        return 0;
    }
    if (state == WS_POWER_STANDBY) {
        if (WS_STATE->standby_deep) {
            (void)run_ap("down");
            WS_STATE->ap_up = 0;
            (void)gpio_power_off_safe();
        } else if (WS_STATE->ap_up) {
            (void)run_ap("standby");
        }
        return 0;
    }
    if (WS_STATE->ap_up) {
        /* Already associated: only leave power save. */
        (void)run_ap("active");
        return 0;
    }
    /* Cold start: reset pulse only when SDIO modules are unloaded. */
    if (wlan_mods_loaded()) {
        platform_log("wlan_mods_present", 1);
        rc = gpio_mode(GPIO_MODE_OUT_HIGH);
    } else {
        rc = gpio_mode(GPIO_MODE_OUT_LOW);
        if (rc == 0) { delay_ms(15); rc = gpio_mode(GPIO_MODE_OUT_HIGH); }
    }
    platform_log("wlan_power_on", (uint32_t)(rc == 0));
    if (rc != 0) return -1;
    delay_ms(150);
    rc = run_ap("up");
    if (rc == 0) WS_STATE->ap_up = 1;
    return rc;
}

/* ---------------------------------------------------------------- server */

static int platform_should_stop(void *ctx) {
    (void)ctx;
    return WS_STATE->stop_flag ? 1 : 0;
}

static int is_ascii_digit(char c) { return c >= '0' && c <= '9'; }

static char upper_ascii(char c) { return (c >= 'a' && c <= 'z') ? (char)(c - 'a' + 'A') : c; }

static int cmp_str(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return (int)(uint8_t)*a - (int)(uint8_t)*b;
}

static uint32_t pack4(const char *s) {
    uint32_t v = 0;
    for (uint32_t i = 0; i < 4 && s[i]; i++) {
        v = (v << 8) | (uint8_t)s[i];
    }
    return v;
}

static int valid_dcim_subdir(const char *name) {
    uint32_t len = ws_strlen(name);
    if (len < 8 || len > 16) return 0;
    if (name[0] < '1' || name[0] > '9' || !is_ascii_digit(name[1]) || !is_ascii_digit(name[2]))
        return 0;
    for (uint32_t i = 0; i < len; i++) {
        if (name[i] == '.') return 0;
    }
    return 1;
}

static int parse_dcf_media(const char *name, uint32_t *idx_out) {
    if (!name || ws_strlen(name) != 12u || name[8] != '.') return 0;
    if (!is_ascii_digit(name[4]) || !is_ascii_digit(name[5]) ||
        !is_ascii_digit(name[6]) || !is_ascii_digit(name[7])) return 0;
    {
        char e0 = upper_ascii(name[9]), e1 = upper_ascii(name[10]), e2 = upper_ascii(name[11]);
        int is_jpg = (e0 == 'J' && e1 == 'P' && e2 == 'G');
        int is_arw = (e0 == 'A' && e1 == 'R' && e2 == 'W');
        if (!is_jpg && !is_arw) return 0;
    }
    {
        uint32_t idx = (uint32_t)(name[4] - '0') * 1000u +
                       (uint32_t)(name[5] - '0') * 100u +
                       (uint32_t)(name[6] - '0') * 10u +
                       (uint32_t)(name[7] - '0');
        if (idx == 0u || idx > 9999u) return 0;
        *idx_out = idx;
    }
    return 1;
}

/* Discover the newest A:/DCIM/<folder>, 4-char filename prefix, and highest DCF index. */
static void discover_dcim_dir(ws_config *c, uint32_t *max_idx_out) {
    char best[24];
    uint32_t max_idx = 0;
    char best_pfx[5];
    best[0] = 0;
    best_pfx[0] = 0;
    if (max_idx_out) *max_idx_out = 0;
    if (!WS_STATE->fsys.ready || !WS_STATE->fsys.find_first || !WS_STATE->fsys.find_next) return;

    {
        int rc = WS_STATE->fsys.find_first("A:/DCIM/*", 0x80u, WS_STATE->find_buf);
        platform_log("dcim_find_rc", (uint32_t)rc);
        if (rc == 0) {
            for (uint32_t step = 0; step < 256u; step++) {
                const char *entry = *(const char * const *)WS_STATE->find_buf;
                if (entry && valid_dcim_subdir(entry)) {
                    if (!best[0] || cmp_str(entry, best) > 0) {
                        uint32_t k = 0;
                        while (entry[k] && k + 1u < sizeof(best)) { best[k] = entry[k]; k++; }
                        best[k] = 0;
                    }
                }
                if (WS_STATE->fsys.find_next(WS_STATE->find_buf) != 0) break;
            }
        }
    }

    if (best[0]) {
        const char *pre = "A:/DCIM/";
        uint32_t i = 0, j = 0;
        while (pre[i] && i + 1u < sizeof(c->dcim_dir)) { c->dcim_dir[i] = pre[i]; i++; }
        while (best[j] && i + 1u < sizeof(c->dcim_dir)) { c->dcim_dir[i++] = best[j++]; }
        c->dcim_dir[i] = 0;
        platform_log("dcim_dir_len", ws_strlen(c->dcim_dir));
        platform_log("dcim_tag", pack4(best));
    }

    {
        char pat[WS_PATH_MAX + 4];
        uint32_t i = 0;
        while (c->dcim_dir[i] && i + 3u < sizeof(pat)) { pat[i] = c->dcim_dir[i]; i++; }
        pat[i++] = '/';
        pat[i++] = '*';
        pat[i] = 0;

        int rc = WS_STATE->fsys.find_first(pat, 0x80u, WS_STATE->find_buf);
        platform_log("file_find_rc", (uint32_t)rc);
        if (rc == 0) {
            for (uint32_t step = 0; step < 12000u; step++) {
                const char *entry = *(const char * const *)WS_STATE->find_buf;
                uint32_t idx = 0;
                if (entry && parse_dcf_media(entry, &idx) && idx >= max_idx) {
                    max_idx = idx;
                    for (uint32_t k = 0; k < 4u; k++) best_pfx[k] = entry[k];
                    best_pfx[4] = 0;
                }
                if (WS_STATE->fsys.find_next(WS_STATE->find_buf) != 0) break;
            }
        }
    }

    if (best_pfx[0]) {
        for (uint32_t k = 0; k < 5u; k++) c->prefix[k] = best_pfx[k];
        platform_log("dcim_pfx", pack4(best_pfx));
    }
    platform_log("dcim_max_idx", max_idx);
    if (max_idx_out) *max_idx_out = max_idx;
}

static void maintenance(void) {
    uint32_t now = platform_now();
    if (WS_STATE->srv.capture_count != WS_STATE->last_cap_seen) {
        WS_STATE->last_cap_seen = WS_STATE->srv.capture_count;
        WS_STATE->last_cap_event_ms = now;
        WS_STATE->rediscover_pending = 1;
    }
    if ((uint32_t)(now - WS_STATE->srv.last_scan_ms) >= 1000u) {
        int added = ws_scan(&WS_STATE->srv);
        if (added > 0) {
            WS_STATE->rediscover_pending = 0;
        } else if (WS_STATE->rediscover_pending && !WS_STATE->custom_folder &&
                   (uint32_t)(now - WS_STATE->last_cap_event_ms) >= (WS_BURST_QUIET_MS + 1500u)) {
            ws_config tmp;
            uint32_t new_max = 0;
            WS_STATE->rediscover_pending = 0;
            ws_memcpy(&tmp, &WS_STATE->srv.cfg, sizeof(tmp));
            discover_dcim_dir(&tmp, &new_max);
            if (!ws_streq(tmp.dcim_dir, WS_STATE->srv.cfg.dcim_dir) ||
                !ws_streq(tmp.prefix, WS_STATE->srv.cfg.prefix)) {
                int folder_changed = !ws_streq(tmp.dcim_dir, WS_STATE->srv.cfg.dcim_dir);
                ws_memcpy(WS_STATE->srv.cfg.dcim_dir, tmp.dcim_dir, sizeof(WS_STATE->srv.cfg.dcim_dir));
                ws_memcpy(WS_STATE->srv.cfg.prefix, tmp.prefix, sizeof(WS_STATE->srv.cfg.prefix));
                if (folder_changed) {
                    WS_STATE->srv.acked_index = (new_max > 16u) ? (new_max - 1u) : 0u;
                    WS_STATE->srv.next_index = (new_max > 16u) ? new_max : 1u;
                } else if (new_max > 0u) {
                    WS_STATE->srv.acked_index = new_max - 1u;
                    WS_STATE->srv.next_index = new_max;
                }
                WS_STATE->srv.discovering = 0;
                (void)ws_scan(&WS_STATE->srv);
            }
        }
        ws_power_tick(&WS_STATE->srv, now);
    }
}

static int sock_recv(void *ctx, void *buf, uint32_t len) {
    return sc6(SYS_recvfrom, (int)(uint32_t)ctx, (int)buf, (int)len, 0, 0, 0);
}

static int sock_send(void *ctx, const void *buf, uint32_t len) {
    int fd = (int)(uint32_t)ctx;
    const uint8_t *p = (const uint8_t *)buf;
    while (len) {
        int n = sc6(SYS_sendto, fd, (int)p, (int)len, MSG_NOSIGNAL, 0, 0);
        if (n < 0) {
            if (n == -4 /*EINTR*/) continue;
            return -1;
        }
        p += n;
        len -= (uint32_t)n;
    }
    return 0;
}

static int sock_wait(void *ctx, uint32_t timeout_ms) {
    struct pollfd_ p;
    p.fd = (int)(uint32_t)ctx;
    p.events = POLLIN;
    p.revents = 0;
    return sc3(SYS_poll, (int)&p, 1, (int)timeout_ms);
}

static void sock_idle(void *ctx) { (void)ctx; maintenance(); }

static void save_state(uint32_t ack, uint32_t acked_index) {
    char line[64];
    uint32_t i = 0;
    char d[12];
    int n = 0;
    uint32_t v = ack;
    const char *p = "ack=";
    while (*p) line[i++] = *p++;
    if (!v) d[n++] = '0';
    while (v) { d[n++] = (char)('0' + (v % 10u)); v /= 10u; }
    while (n) line[i++] = d[--n];
    line[i++] = '\n';
    p = "acked_index=";
    while (*p) line[i++] = *p++;
    v = acked_index;
    n = 0;
    if (!v) d[n++] = '0';
    while (v) { d[n++] = (char)('0' + (v % 10u)); v /= 10u; }
    while (n) line[i++] = d[--n];
    line[i++] = '\n';
    (void)write_file("/tmp_bt/wifisync/state.txt", line, i, 0);
}

static void ws_worker(void) {
    WS_STATE->worker_running = 1;
    platform_log("worker_start", 0);
    while (!WS_STATE->stop_flag) {
        struct pollfd_ p;
        p.fd = WS_STATE->listen_fd;
        p.events = POLLIN;
        p.revents = 0;
        if (sc3(SYS_poll, (int)&p, 1, 200) > 0 && (p.revents & POLLIN)) {
            int fd = sc3(SYS_accept, WS_STATE->listen_fd, 0, 0);
            if (fd >= 0) {
                ws_conn k;
                struct timeval_ { int32_t sec; int32_t usec; } tv;
                int sb = 524288;
                int nodelay = 1;
                tv.sec = 5;
                tv.usec = 0;
                (void)sc6(SYS_setsockopt, fd, SOL_SOCKET, 20 /*SO_RCVTIMEO*/,  (int)&tv,      8, 0);
                (void)sc6(SYS_setsockopt, fd, SOL_SOCKET, 21 /*SO_SNDTIMEO*/,  (int)&tv,      8, 0);
                (void)sc6(SYS_setsockopt, fd, SOL_SOCKET, 7  /*SO_SNDBUF*/,    (int)&sb,      4, 0);
                (void)sc6(SYS_setsockopt, fd, 6 /*IPPROTO_TCP*/, 1 /*TCP_NODELAY*/, (int)&nodelay, 4, 0);
                k.ctx = (void *)(uint32_t)fd;
                k.recv = sock_recv;
                k.send = sock_send;
                k.wait = sock_wait;
                k.idle = sock_idle;
                (void)ws_http_serve(&WS_STATE->srv, &k);
                ml_close(fd);
            }
        }
        maintenance();
        if (WS_STATE->srv.q.ack != WS_STATE->saved_ack) {
            save_state(WS_STATE->srv.q.ack, WS_STATE->srv.acked_index);
            WS_STATE->saved_ack = WS_STATE->srv.q.ack;
        }
    }
    platform_log("worker_stop", 0);
    if (WS_STATE->listen_fd >= 0) ml_close(WS_STATE->listen_fd);
    WS_STATE->listen_fd = -1;
    WS_STATE->worker_running = 0;
}

/* -------------------------------------------------------------- config */

static void cfg_defaults(ws_config *c) {
    ws_memset(c, 0, sizeof(*c));
    {
        const char *s = "A6400-5G-SYNC";
        uint32_t i = 0;
        while (s[i]) { c->ssid[i] = s[i]; i++; }
    }
    {
        const char *s = "a6400-wifisync";
        uint32_t i = 0;
        while (s[i]) { c->psk[i] = s[i]; i++; }
    }
    {
        const char *s = "A:/DCIM/100MSDCF";
        uint32_t i = 0;
        while (s[i]) { c->dcim_dir[i] = s[i]; i++; }
    }
    c->first_index = 1;
    c->probe_forward = 8;
    c->probe_back = 8;
    c->discover_span = 9999;
    c->discover_miss_limit = 512;
    c->scan_budget = 128;
    c->min_jpg = 1024;
    c->min_arw = 100000;
    c->idle_sec = 45;
    c->channel = 36;
    c->width = 80;
    c->camera_id = 0xA6400000u;
}

static void copy_val(char *dst, uint32_t cap, const char *v, uint32_t n) {
    uint32_t i = 0;
    while (i < n && v[i] && i + 1u < cap) { dst[i] = v[i]; i++; }
    dst[i] = 0;
}

static int parse_cfg(ws_config *c, const char *text) {
    const char *p = text;
    while (*p) {
        const char *line = p;
        uint32_t n = 0;
        char key[24];
        const char *eq;
        while (*p && *p != '\n') p++;
        while (n < (uint32_t)(p - line) && (line[n] == ' ' || line[n] == '\t')) n++;
        if (n >= (uint32_t)(p - line) || line[n] == '#') continue;
        eq = line + n;
        while (eq < p && *eq != '=') eq++;
        if (eq >= p) continue;
        {
            uint32_t kn = (uint32_t)(eq - (line + n));
            if (kn >= sizeof(key)) kn = sizeof(key) - 1u;
            for (uint32_t i = 0; i < kn; i++) key[i] = line[n + i];
            key[kn] = 0;
        }
        {
            const char *v = eq + 1;
            uint32_t vn = (uint32_t)(p - v);
            while (vn && (v[vn - 1] == '\r' || v[vn - 1] == ' ')) vn--;
            if (ws_streq(key, "ssid")) copy_val(c->ssid, sizeof(c->ssid), v, vn);
            else if (ws_streq(key, "psk")) copy_val(c->psk, sizeof(c->psk), v, vn);
            else if (ws_streq(key, "folder")) {
                char tmp[40];
                uint32_t i = 0;
                const char *pre = "A:/DCIM/";
                while (*pre && i + 1u < sizeof(tmp)) tmp[i++] = *pre++;
                copy_val(tmp + i, sizeof(tmp) - i, v, vn);
                copy_val(c->dcim_dir, sizeof(c->dcim_dir), tmp, ws_strlen(tmp));
                WS_STATE->custom_folder = 1;
            }
            else if (ws_streq(key, "channel")) {
                uint32_t x;
                if (ws_parse_u32(v, &x) == 0 && (x == 36 || x == 40 || x == 44 || x == 48))
                    c->channel = (uint8_t)x;
            }
            else if (ws_streq(key, "width")) {
                uint32_t x;
                if (ws_parse_u32(v, &x) == 0 && (x == 20 || x == 40 || x == 80))
                    c->width = (uint8_t)x;
            }
            else if (ws_streq(key, "idle_sec")) {
                uint32_t x;
                if (ws_parse_u32(v, &x) == 0 && x <= 3600) c->idle_sec = (uint16_t)x;
            }
            else if (ws_streq(key, "first_index")) {
                uint32_t x;
                if (ws_parse_u32(v, &x) == 0 && x >= 1 && x <= 9999) c->first_index = x;
            }
            else if (ws_streq(key, "min_jpg")) {
                uint32_t x;
                if (ws_parse_u32(v, &x) == 0) c->min_jpg = x;
            }
            else if (ws_streq(key, "min_arw")) {
                uint32_t x;
                if (ws_parse_u32(v, &x) == 0) c->min_arw = x;
            }
            else if (ws_streq(key, "standby")) {
                c->standby_mode = (vn >= 4 && v[0] == 'd' && v[1] == 'e') ? 1 : 0;
            }
            else if (ws_streq(key, "camera_id")) {
                uint32_t x = 0, i;
                int ok = vn > 0;
                for (i = 0; i < vn; i++) {
                    char ch = v[i];
                    int h;
                    if (ch >= '0' && ch <= '9') h = ch - '0';
                    else if (ch >= 'a' && ch <= 'f') h = ch - 'a' + 10;
                    else if (ch >= 'A' && ch <= 'F') h = ch - 'A' + 10;
                    else { ok = 0; break; }
                    x = (x << 4) | (uint32_t)h;
                }
                if (ok) c->camera_id = x;
            }
        }
        if (*p == '\n') p++;
    }
    {
        uint32_t l = ws_strlen(c->psk);
        if (l < 8 || l > 63) copy_val(c->psk, sizeof(c->psk), "a6400-wifisync", 15);
        l = ws_strlen(c->ssid);
        if (l == 0 || l > 32) copy_val(c->ssid, sizeof(c->ssid), "A6400-5G-SYNC", 13);
    }
    return 0;
}

/* Read A:/mods/wifisync.cfg through Fsys (read-only). */
static int load_config(ws_config *c) {
    char path[WS_PATH_MAX];
    void *h = 0;
    char buf[768];
    const char *pre = "A:/mods/wifisync.cfg";
    uint32_t i = 0;
    WS_STATE->custom_folder = 0;
    cfg_defaults(c);
    while (pre[i]) { path[i] = pre[i]; i++; }
    path[i] = 0;
    if (WS_STATE->fsys.ready && WS_STATE->fsys.open(path, (uint32_t *)&h, 1) == 0) {
        int n = WS_STATE->fsys.read((int)(uint32_t)h, buf, (int)sizeof(buf) - 1);
        WS_STATE->fsys.close((int)(uint32_t)h);
        if (n > 0) {
            buf[n] = 0;
            parse_cfg(c, buf);
            platform_log("config_sd", (uint32_t)n);
        }
    } else {
        platform_log("config_default", 0);
    }
    return 0;
}

/* Write the effective config for ap.sh (submenu choices win over the SD file). */
static void cfg_append(char *out, uint32_t *o, uint32_t cap, const char *s) {
    while (*s && *o + 1u < cap) out[(*o)++] = *s++;
}

static void cfg_append_u2(char *out, uint32_t *o, uint32_t cap, uint32_t v) {
    char d[3];
    d[0] = (char)('0' + (v / 10u) % 10u);
    d[1] = (char)('0' + v % 10u);
    d[2] = 0;
    cfg_append(out, o, cap, d);
}

static void write_effective_cfg(const ws_config *c) {
    char out[256];
    uint32_t o = 0;
    cfg_append(out, &o, sizeof(out), "ssid=");
    cfg_append(out, &o, sizeof(out), c->ssid);
    cfg_append(out, &o, sizeof(out), "\npsk=");
    cfg_append(out, &o, sizeof(out), c->psk);
    cfg_append(out, &o, sizeof(out), "\nchannel=");
    cfg_append_u2(out, &o, sizeof(out), c->channel);
    cfg_append(out, &o, sizeof(out), "\nwidth=");
    cfg_append_u2(out, &o, sizeof(out), c->width);
    cfg_append(out, &o, sizeof(out), "\nstandby=");
    cfg_append(out, &o, sizeof(out), c->standby_mode ? "deep" : "low");
    cfg_append(out, &o, sizeof(out), "\n");
    (void)write_file("/tmp_bt/wifisync/wifisync.cfg", out, o, 0);
}

/* ------------------------------------------------------------- lifecycle */

static void apply_submenu(ws_config *c) {
    uint8_t *cfg = WS_STATE->cfg;
    c->standby_mode = (cfg[0] == 1) ? 1 : 0;
    if (cfg[1] == 1) c->width = 40;
    else if (cfg[1] == 2) c->width = 20;
    else c->width = 80;
    if (cfg[2] == 1) c->channel = 40;
    else if (cfg[2] == 2) c->channel = 44;
    else if (cfg[2] == 3) c->channel = 48;
    else c->channel = 36;
    WS_STATE->standby_deep = (cfg[0] == 1) ? 1u : 0u;
}

static int bind_listener(uint16_t port) {
    int fd, one = 1;
    struct sockaddr_in addr;
    fd = sc3(SYS_socket, AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    (void)sc6(SYS_setsockopt, fd, SOL_SOCKET, SO_REUSEADDR, (int)&one, 4, 0);
    ws_memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = (uint16_t)((port >> 8) | (port << 8));   /* htons */
    addr.sin_addr = 0;                                        /* INADDR_ANY */
    if (sc3(SYS_bind, fd, (int)&addr, sizeof(addr)) != 0) { ml_close(fd); return -1; }
    if (sc3(SYS_listen, fd, 2, 0) != 0) { ml_close(fd); return -1; }
    return fd;
}

__attribute__((used, visibility("hidden"))) void ws_hook_note(void) {
    if (WS_STATE->ready) ws_note_capture(&WS_STATE->srv);
}

static int load_state(uint32_t *acked_index) {
    char buf[64];
    uint32_t len = 0, ack = 0, acked = 0;
    *acked_index = 0;
    if (read_file("/tmp_bt/wifisync/state.txt", buf, sizeof(buf), &len) != 0) return 0;
    if (ws_strneq(buf, "ack=", 4)) (void)ws_parse_u32(buf + 4, &ack);
    {
        const char *key = "acked_index=";
        const char *p = buf;
        while (*p) {
            if (ws_strneq(p, key, 12)) {
                (void)ws_parse_u32(p + 12, &acked);
                break;
            }
            p++;
        }
    }
    *acked_index = acked;
    return (int)ack;
}

__attribute__((used)) void ws_on(int on) {
    ws_config cfg;
    ws_fs_ops fs;
    ws_platform plat;
    int saved_ack;

    if (WS_STATE->magic != WS_BSS_MAGIC) {
        uint8_t saved_cfg[8];
        ws_memcpy(saved_cfg, WS_STATE->cfg, sizeof(saved_cfg));
        ws_memset(__smod_bss, 0, sizeof(struct ws_mod_state));
        ws_memcpy(WS_STATE->cfg, saved_cfg, sizeof(saved_cfg));
        WS_STATE->magic = WS_BSS_MAGIC;
    }
    (void)ml_mkdir("/tmp_bt/wifisync");

    if (!on) {
        WS_STATE->stop_flag = 1;
        for (uint32_t i = 0; i < 4000u && WS_STATE->worker_running; i++) delay_ms(1);
        if (WS_STATE->ready) {
            ws_power_off(&WS_STATE->srv);      /* AP down + WL_REG_ON low */
            save_state(WS_STATE->srv.q.ack, WS_STATE->srv.acked_index);
        }
        WS_STATE->ready = 0;
        platform_log("mod_off", 0);
        return;
    }

    platform_log("mod_on", 0);
    if (WS_STATE->ready) {
        platform_log("on_already_active", 0);
        return;
    }
    if (fsys_resolve() != 0) {
        platform_log("on_refused_fsys", 0);
        return;
    }
    load_config(&cfg);
    apply_submenu(&cfg);
    if (!WS_STATE->custom_folder) {
        uint32_t max_idx = 0;
        discover_dcim_dir(&cfg, &max_idx);
        cfg.first_index = (max_idx > 0u && max_idx < 9999u) ? (max_idx + 1u) : 1u;
        cfg.discover_span = 32u;
        WS_STATE->disc_max_idx = max_idx;
    } else {
        WS_STATE->disc_max_idx = 0;
    }
    cfg.camera_id = 0xA6400000u | (uint32_t)(LIBFSYS_FNV4096 & 0x00FFFFFFu);
    write_effective_cfg(&cfg);

    /* Optional SD override of the AP script (read-only), else the embedded one. */
    {
        char *script = (char *)WS_STATE->io;
        const char *src = ws_ap_script;
        uint32_t len = (uint32_t)(sizeof(ws_ap_script) - 1u);
        void *h = 0;
        char path[WS_PATH_MAX];
        const char *pre = "A:/mods/wifisync_ap.sh";
        uint32_t i = 0;
        while (pre[i]) { path[i] = pre[i]; i++; }
        path[i] = 0;
        if (WS_STATE->fsys.open(path, (uint32_t *)&h, 1) == 0) {
            int r = WS_STATE->fsys.read((int)(uint32_t)h, script, 16383);
            WS_STATE->fsys.close((int)(uint32_t)h);
            if (r > 16) {
                script[r] = 0;
                src = script;
                len = (uint32_t)r;
                platform_log("ap_script_sd", len);
            }
        }
        if (write_file("/tmp_bt/wifisync/ap.sh", src, len, 1) != 0) {
            platform_log("ap_script_write_failed", 0);
            return;
        }
    }

    fs.ctx = 0; fs.stat = wfs_stat; fs.open = wfs_open; fs.read = wfs_read; fs.close = wfs_close;
    plat.ctx = 0; plat.now_ms = platform_now_cb; plat.log = platform_log_cb;
    plat.set_power = platform_set_power; plat.should_stop = platform_should_stop;
    ws_init(&WS_STATE->srv, &cfg, &fs, &plat, WS_STATE->io, sizeof(WS_STATE->io));
    {
        char kv[64];
        uint32_t n = 0;
        if (read_file("/proc/version", kv, sizeof(kv), &n) == 0 && n > 8) {
            uint32_t i = 0;
            while (i + 1u < sizeof(WS_STATE->srv.fw) && kv[i] && kv[i] != '\n') { WS_STATE->srv.fw[i] = kv[i]; i++; }
            WS_STATE->srv.fw[i] = 0;
        } else {
            copy_val(WS_STATE->srv.fw, sizeof(WS_STATE->srv.fw), "ILCE-6400", 9);
        }
    }
    saved_ack = load_state(&WS_STATE->srv.acked_index);
    if (saved_ack > 0) {
        WS_STATE->srv.q.ack = (uint32_t)saved_ack;
        WS_STATE->srv.q.head = (uint32_t)saved_ack;
    }
    if (WS_STATE->disc_max_idx > WS_STATE->srv.acked_index && WS_STATE->disc_max_idx < 9999u) {
        WS_STATE->srv.acked_index = WS_STATE->disc_max_idx;
        WS_STATE->srv.next_index = WS_STATE->disc_max_idx + 1u;
        WS_STATE->srv.discovering = 0;
    }
    WS_STATE->last_cap_seen = WS_STATE->srv.capture_count;
    WS_STATE->rediscover_pending = 0;
    WS_STATE->saved_ack = WS_STATE->srv.q.ack;

    WS_STATE->listen_fd = bind_listener(8765);
    platform_log("listen_fd", (uint32_t)WS_STATE->listen_fd);
    if (WS_STATE->listen_fd < 0) {
        platform_log("on_refused_listen", 0);
        return;
    }
    WS_STATE->stop_flag = 0;
    WS_STATE->ready = 1;
    (void)write_file("/proc/sys/net/core/wmem_max", "1048576\n", 8u, 0);
    (void)write_file("/proc/sys/net/core/rmem_max", "1048576\n", 8u, 0);
    (void)write_file("/proc/sys/net/ipv4/tcp_wmem", "4096 131072 1048576\n", 20u, 0);
    (void)write_file("/proc/sys/net/ipv4/tcp_rmem", "4096 131072 1048576\n", 20u, 0);
    ws_power_on(&WS_STATE->srv);              /* cold start: GPIO + ap.sh up */
    if (WS_STATE->srv.power != WS_POWER_ACTIVE) {
        platform_log("on_refused_ap", 0);
        (void)run_ap("down");
        (void)gpio_power_off_safe();
        WS_STATE->ready = 0;
        ml_close(WS_STATE->listen_fd);
        WS_STATE->listen_fd = -1;
        return;
    }
    if (!WS_STATE->worker_stack) {
        WS_STATE->worker_stack = ml_mmap(64u * 1024u);
        if (!WS_STATE->worker_stack) {
            platform_log("stack_mmap_failed", 0);
            WS_STATE->ready = 0;
            return;
        }
    }
    if (ml_clone(ws_worker, WS_STATE->worker_stack + 64u * 1024u - 8u) < 0) {
        platform_log("clone_failed", 0);
        WS_STATE->ready = 0;
        return;
    }
    platform_log("mod_active", 0);
}

__attribute__((used)) void ws_cfg(int reason) {
    ws_config cfg;
    if (!WS_STATE->fsys.ready) return;
    load_config(&cfg);
    apply_submenu(&cfg);
    write_effective_cfg(&cfg);
    platform_log("cfg_changed", (uint32_t)reason);
    /* AP channel/width changes take effect on the next ON. */
}

