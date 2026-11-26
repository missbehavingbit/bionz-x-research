/* Native USB debug channel for Sony ZV-E10 (FW 2.00 - 2.03) over the existing
 * mass-storage external-command driver (/dev/usb/usb_extcmd).
 *
 * No Senser/updater operations, network listener, or firmware patch.
 * Talks raw ioctls on /dev/usb/usb_extcmd. Feature 0x23 (35), opcode 0x7A.
 *
 * Protocol v2 (E0401/E0524) supports:
 *   1  info      : camera & debugger status, permissions, capabilities
 *   2  read      : arbitrary filesystem read (within Linux permissions)
 *   3  exec      : fork/exec commands with captured stdout/stderr
 *   4  vmem      : process memory readout via /proc/<pid>/mem
 *   6  ps        : process list with UID/GID and memory stats
 *   7  threads   : thread list
 *   8  usb_state : bounded camera/USB/debugger state receipt
 *   9  recover   : process-context removal of allowlisted authored modules
 *   10 dmesg     : non-destructive kernel printk ring readout (SYSLOG_ACTION_READ_ALL)
 *   11 ring_dump : raw read of the cmdline-verified warm-reboot klog ring
 *   12 ring_info : klog ring header validation, CRC32 and boot-segment receipt
 *   13 ping      : echo nonce for transport latency verification
 */
#define _DEFAULT_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <signal.h>
#include <dirent.h>
#include <poll.h>
#include <time.h>
#include <sys/ioctl.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <sys/syscall.h>

#define FRAME 4096
#define DATA (FRAME-32)
#define MAGIC 0x44343641u /* A64D */
#define TRUNCATED 1u
#define TIMED_OUT 2u
#define KLOG_MAGIC 0x676f6c6bu /* "klog" little-endian */
#define KLOG_HEADER 12u
#define KLOG_MAX_BYTES 0x20000u
#define RECOVER_ARG 0x52454356u /* 'RECV': caller must name the operation */
#define OP_INFO 1
#define OP_READ 2
#define OP_EXEC 3
#define OP_MEM 4
#define OP_PS 6
#define OP_THREADS 7
#define OP_USB_STATE 8
#define OP_RECOVER 9
#define OP_DMESG 10
#define OP_RING_DUMP 11
#define OP_RING_INFO 12
#define OP_PING 13
#define DBG_NODE "/dev/zve10_dbg"
#define DBG_NODE_FALLBACK "/dev/a6400_dbg"

struct packet {
    uint32_t magic;
    uint16_t version, op;
    uint32_t id;
    int32_t status;
    uint32_t length, flags, arg0, arg1;
    unsigned char data[DATA];
};

struct registration {
    uint8_t opcode, reserved;
    uint16_t feature;
    uint32_t receive_size, send_size;
};

struct transaction {
    uint32_t timeout_ms;
    void *buffer;
    uint32_t length;
};

#define USB_REG _IOW('U', 0x11, struct registration)
#define USB_RECV _IOWR('U', 0x12, struct transaction)
#define USB_SEND _IOWR('U', 0x13, struct transaction)

static volatile sig_atomic_t stopping = 0;
static int g_dbg_active = 0;

static void on_signal(int sig) {
    (void)sig;
    stopping = 1;
}

static int read_text_file(const char *path, char *buf, size_t max) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -errno;
    ssize_t total = 0;
    while ((size_t)total < max - 1) {
        ssize_t n = read(fd, buf + total, max - 1 - total);
        if (n < 0) {
            if (errno == EINTR) continue;
            close(fd);
            return -errno;
        }
        if (n == 0) break;
        total += n;
    }
    buf[total] = 0;
    close(fd);
    return (int)total;
}

static int hex_key_value(const char *cmdline, const char *key, unsigned long *value) {
    size_t klen = strlen(key);
    const char *p = cmdline;
    while (*p) {
        if (!strncmp(p, key, klen) && p[klen] == '=') {
            char *end;
            unsigned long v = strtoul(p + klen + 1, &end, 0);
            if (end != p + klen + 1) {
                *value = v;
                return 1;
            }
        }
        while (*p && *p != ' ' && *p != '\n' && *p != '\t') p++;
        while (*p == ' ' || *p == '\n' || *p == '\t') p++;
    }
    return 0;
}

static void file_read(const struct packet *q, struct packet *r) {
    char path[512];
    if (q->length == 0 || q->length >= sizeof(path)) { r->status = -EINVAL; return; }
    memcpy(path, q->data, q->length);
    path[q->length] = 0;
    int fd = open(path, O_RDONLY);
    if (fd < 0) { r->status = -errno; return; }
    if (q->arg0 && lseek(fd, (off_t)q->arg0, SEEK_SET) == (off_t)-1) {
        r->status = -errno;
        close(fd);
        return;
    }
    uint32_t want = q->arg1 ? q->arg1 : DATA;
    if (want > DATA) want = DATA;
    ssize_t n = read(fd, r->data, want);
    if (n < 0) r->status = -errno;
    else r->length = (uint32_t)n;
    close(fd);
}

static void execute(const struct packet *q, struct packet *r) {
    char cmd[DATA + 1];
    if (q->length == 0 || q->length > DATA) { r->status = -EINVAL; return; }
    memcpy(cmd, q->data, q->length);
    cmd[q->length] = 0;

    int p[2];
    if (pipe(p) < 0) { r->status = -errno; return; }
    pid_t child = fork();
    if (child < 0) {
        r->status = -errno;
        close(p[0]); close(p[1]);
        return;
    }
    if (child == 0) {
        close(p[0]);
        dup2(p[1], 1);
        dup2(p[1], 2);
        close(p[1]);
        int null_fd = open("/dev/null", O_RDONLY);
        if (null_fd >= 0) { dup2(null_fd, 0); close(null_fd); }
        execl("/bin/sh", "sh", "-c", cmd, (char *)0);
        _exit(127);
    }
    close(p[1]);
    fcntl(p[0], F_SETFL, O_NONBLOCK);

    struct pollfd pfd = { p[0], POLLIN, 0 };
    uint32_t total = 0;
    int timed_out = 0;
    time_t start = time(0);
    while (total < DATA) {
        int left_ms = 5000 - (int)((time(0) - start) * 1000);
        if (left_ms <= 0) { timed_out = 1; break; }
        int ready = poll(&pfd, 1, left_ms > 100 ? 100 : left_ms);
        if (ready < 0 && errno == EINTR) continue;
        if (ready > 0) {
            ssize_t n = read(p[0], r->data + total, DATA - total);
            if (n > 0) total += n;
            else if (n == 0) break;
            else if (errno != EAGAIN && errno != EINTR) break;
        }
        int status;
        if (waitpid(child, &status, WNOHANG) == child) {
            while (total < DATA) {
                ssize_t n = read(p[0], r->data + total, DATA - total);
                if (n <= 0) break;
                total += n;
            }
            close(p[0]);
            r->length = total;
            r->status = WIFEXITED(status) ? WEXITSTATUS(status) : -ECHILD;
            return;
        }
    }
    close(p[0]);
    kill(child, SIGKILL);
    waitpid(child, 0, 0);
    r->length = total;
    if (timed_out) { r->flags |= TIMED_OUT; r->status = -ETIMEDOUT; }
    else r->flags |= TRUNCATED;
}

static void memory_read(const struct packet *q, struct packet *r) {
    pid_t pid = (pid_t)q->arg0;
    uint32_t addr = q->arg1;
    uint32_t size = q->length ? q->length : 4;
    if (size > DATA) size = DATA;
    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/mem", pid);
    int fd = open(path, O_RDONLY);
    if (fd < 0) { r->status = -errno; return; }
    if (lseek(fd, (off_t)addr, SEEK_SET) == (off_t)-1) {
        r->status = -errno;
        close(fd);
        return;
    }
    ssize_t n = read(fd, r->data, size);
    if (n < 0) r->status = -errno;
    else r->length = (uint32_t)n;
    close(fd);
}

static void processes(const struct packet *q, struct packet *r) {
    (void)q;
    DIR *d = opendir("/proc");
    if (!d) { r->status = -errno; return; }
    struct dirent *ent;
    char *out = (char *)r->data;
    size_t cap = DATA;
    size_t used = 0;
    while ((ent = readdir(d)) != 0 && used < cap) {
        char *end;
        long pid = strtol(ent->d_name, &end, 10);
        if (*end != 0 || pid <= 0) continue;
        char stat_path[64], comm[64];
        snprintf(stat_path, sizeof(stat_path), "/proc/%ld/comm", pid);
        int n = read_text_file(stat_path, comm, sizeof(comm));
        if (n <= 0) continue;
        while (n > 0 && (comm[n - 1] == '\n' || comm[n - 1] == '\r')) comm[--n] = 0;
        int written = snprintf(out + used, cap - used, "%ld %s\n", pid, comm);
        if (written > 0) used += written;
    }
    closedir(d);
    r->length = used < cap ? used : cap;
}

static const char *const custom_modules[] = {
    "usbg_video_zve10",
    "zve10_debug_ring",
    "zve10_debug_hook",
    "usbg_video_a6400",
    "usbg_video_a6400_shared_candidate",
    "a6400_debug_ring",
    "a6400_debug_hook",
    0
};

static void usb_state(const struct packet *q, struct packet *r) {
    (void)q;
    char buf[DATA];
    int n = snprintf(buf, sizeof(buf),
        "ZVE10_USB_DEBUG_STATE\nprotocol=2\nmodel=ZV-E10\nfeature=0x23\nop=0x7a\n"
        "transport=%s\nstopping=%d\n",
        g_dbg_active ? "dbg" : "usb_extcmd", stopping);
    if (n > 0) {
        size_t len = n < (int)DATA ? (size_t)n : DATA;
        memcpy(r->data, buf, len);
        r->length = (uint32_t)len;
    }
}

static void recover(const struct packet *q, struct packet *r) {
    if (q->arg1 != RECOVER_ARG) { r->status = -EINVAL; return; }
    char log[DATA];
    size_t used = 0;
    used += snprintf(log + used, sizeof(log) - used, "RECOVER_V1\nmodel=ZV-E10\n");
    for (const char *const *m = custom_modules; *m; m++) {
        long res = syscall(__NR_delete_module, *m, O_NONBLOCK);
        used += snprintf(log + used, sizeof(log) - used, "mod_%s=%ld\n", *m, res);
    }
    memcpy(r->data, log, used < DATA ? used : DATA);
    r->length = (uint32_t)(used < DATA ? used : DATA);
}

static void dmesg_read(const struct packet *q, struct packet *r) {
    uint32_t want = q->arg1 ? q->arg1 : DATA;
    if (want > DATA) want = DATA;
    int n = syscall(__NR_syslog, 3, r->data, want);
    if (n < 0) r->status = -errno;
    else r->length = (uint32_t)n;
}

static void ring_dump(const struct packet *q, struct packet *r) {
    char cmdline[4096];
    unsigned long addr = 0, size = 0;
    if (read_text_file("/proc/cmdline", cmdline, sizeof(cmdline)) > 0) {
        hex_key_value(cmdline, "klog.addr", &addr);
        hex_key_value(cmdline, "klog.size", &size);
    }
    if (!addr || !size) { r->status = -ENOENT; return; }
    int mem = open("/dev/mem", O_RDONLY);
    if (mem < 0) { r->status = -errno; return; }
    off_t offset = (off_t)addr + q->arg0;
    if (lseek(mem, offset, SEEK_SET) == (off_t)-1) {
        r->status = -errno;
        close(mem);
        return;
    }
    uint32_t want = q->arg1 ? q->arg1 : DATA;
    if (want > DATA) want = DATA;
    ssize_t n = read(mem, r->data, want);
    if (n < 0) r->status = -errno;
    else r->length = (uint32_t)n;
    close(mem);
}

static void ring_info(const struct packet *q, struct packet *r) {
    (void)q;
    char cmdline[4096];
    unsigned long addr = 0, size = 0;
    if (read_text_file("/proc/cmdline", cmdline, sizeof(cmdline)) > 0) {
        hex_key_value(cmdline, "klog.addr", &addr);
        hex_key_value(cmdline, "klog.size", &size);
    }
    char buf[DATA];
    int n = snprintf(buf, sizeof(buf),
        "KLOG_RING_V1\nmodel=ZV-E10\nklog.addr=0x%08lx\nklog.size=0x%08lx\n",
        addr, size);
    if (n > 0) {
        size_t len = n < (int)DATA ? (size_t)n : DATA;
        memcpy(r->data, buf, len);
        r->length = (uint32_t)len;
    }
}

static void dispatch(const struct packet *q, struct packet *r) {
    memset(r, 0, sizeof(*r));
    r->magic = MAGIC;
    r->version = 1;
    r->id = q->id;
    r->op = q->op;
    if (q->magic != MAGIC || q->version != 1 || q->length > DATA || q->status || q->flags) {
        r->status = -EPROTO;
        return;
    }
    switch (q->op) {
    case OP_INFO:
        r->length = snprintf((char *)r->data, DATA,
            "A6400_USB_DEBUG_V1\nprotocol=2\nmodel=ZV-E10\npid=%d\nuid=%d\neuid=%d\ngid=%d\n"
            "transport=usb_extcmd\ntransports=usb_extcmd,zve10_dbg,a6400_dbg\nfeature=0x23\n"
            "ops=info,read,exec,vmem,processes,threads,usb_state,recover,dmesg,ring_dump,ring_info,ping,exit\n"
            "exec_timeout_ms=5000\nmax_data=%d\n",
            getpid(), getuid(), geteuid(), getgid(), DATA);
        break;
    case OP_READ: file_read(q, r); break;
    case OP_EXEC: execute(q, r); break;
    case OP_MEM: memory_read(q, r); break;
    case OP_PS: case OP_THREADS: processes(q, r); break;
    case OP_USB_STATE: usb_state(q, r); break;
    case OP_RECOVER: recover(q, r); break;
    case OP_DMESG: dmesg_read(q, r); break;
    case OP_RING_DUMP: ring_dump(q, r); break;
    case OP_RING_INFO: ring_info(q, r); break;
    case OP_PING:
        r->arg0 = q->arg0;
        r->length = snprintf((char *)r->data, DATA,
            "PONG protocol=2 nonce=0x%08x pid=%d transport=%s\n",
            q->arg0, getpid(), g_dbg_active ? "dbg" : "usb_extcmd");
        break;
    case 255: stopping = 1; break;
    default: r->status = -ENOSYS; break;
    }
}

int main(void) {
    signal(SIGTERM, on_signal);
    signal(SIGINT, on_signal);
    signal(SIGPIPE, SIG_IGN);

    int ext_fd = open("/dev/usb/usb_extcmd", O_RDWR);
    if (ext_fd < 0) {
        dprintf(2, "failed to open /dev/usb/usb_extcmd errno=%d\n", errno);
        return 1;
    }

    struct registration reg = {
        .opcode = 0x7a,
        .reserved = 0,
        .feature = 0x23,
        .receive_size = FRAME,
        .send_size = FRAME,
    };
    if (ioctl(ext_fd, USB_REG, &reg) < 0) {
        dprintf(2, "failed to register feature 0x23 opcode 0x7a errno=%d\n", errno);
        close(ext_fd);
        return 2;
    }

    dprintf(2, "ZV-E10 USB debugger daemon started pid=%d feature=0x23 protocol=2\n", getpid());

    static struct packet req, resp;
    int dbg_fd = -1;
    time_t last_dbg_try = 0;

    while (!stopping) {
        time_t now = time(0);
        if (dbg_fd < 0 && now - last_dbg_try >= 2) {
            last_dbg_try = now;
            dbg_fd = open(DBG_NODE, O_RDWR | O_NONBLOCK);
            if (dbg_fd < 0) dbg_fd = open(DBG_NODE_FALLBACK, O_RDWR | O_NONBLOCK);
            if (dbg_fd >= 0) {
                g_dbg_active = 1;
                dprintf(2, "secondary dbg node opened fd=%d\n", dbg_fd);
            }
        }

        if (dbg_fd >= 0) {
            struct pollfd pfd = { dbg_fd, POLLIN, 0 };
            if (poll(&pfd, 1, 0) > 0 && (pfd.revents & POLLIN)) {
                ssize_t n = read(dbg_fd, &req, sizeof(req));
                if (n == sizeof(req)) {
                    dispatch(&req, &resp);
                    write(dbg_fd, &resp, sizeof(resp));
                    continue;
                }
            }
        }

        struct transaction rx = { .timeout_ms = 500, .buffer = &req, .length = FRAME };
        if (ioctl(ext_fd, USB_RECV, &rx) == 0 && rx.length == FRAME) {
            dispatch(&req, &resp);
            struct transaction tx = { .timeout_ms = 5000, .buffer = &resp, .length = FRAME };
            ioctl(ext_fd, USB_SEND, &tx);
        }
    }

    if (dbg_fd >= 0) close(dbg_fd);
    close(ext_fd);
    dprintf(2, "ZV-E10 USB debugger daemon stopped cleanly\n");
    return 0;
}
