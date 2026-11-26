/* Host (x86-64) reference server for WiFiSync: real sockets, real files.
 *
 * It runs the same wifisync_core.c the camera mod embeds, so the reference
 * client test exercises the production queue/filter/HTTP/Range/CRC code.
 * Offline only: binds 127.0.0.1, reads one directory, writes nothing.
 */
#define _POSIX_C_SOURCE 200809L
#include "wifisync_core.h"

#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

static ws_server   g_srv;
static ws_config   g_cfg;
static char        g_root[512];
static uint8_t     g_io[256u * 1024u];
static volatile sig_atomic_t g_stop;

typedef struct host_file {
    int fd;
    char path[640];
} host_file;

static const char *base_of(const char *p) {
    const char *b = p;
    while (*p) { if (*p == '/') b = p + 1; p++; }
    return b;
}

static void host_join(char *out, size_t cap, const char *name) {
    snprintf(out, cap, "%s/%s", g_root, name);
}

static int host_stat(void *ctx, const char *path, uint32_t *size, uint32_t *mtime) {
    struct stat st;
    char full[640];
    (void)ctx;
    host_join(full, sizeof(full), base_of(path));
    if (stat(full, &st) != 0 || !S_ISREG(st.st_mode)) return -1;
    *size = (uint32_t)st.st_size;
    *mtime = (uint32_t)st.st_mtime;
    return 0;
}

static int host_open(void *ctx, const char *path, void **handle) {
    host_file *hf;
    char full[640];
    (void)ctx;
    host_join(full, sizeof(full), base_of(path));
    hf = (host_file *)calloc(1, sizeof(*hf));
    if (!hf) return -1;
    hf->fd = open(full, O_RDONLY);
    if (hf->fd < 0) { free(hf); return -2; }
    snprintf(hf->path, sizeof(hf->path), "%s", full);
    *handle = hf;
    return 0;
}

static int host_read(void *ctx, void *handle, void *buf, uint32_t len) {
    host_file *hf = (host_file *)handle;
    ssize_t n;
    (void)ctx;
    if (!hf) return -1;
    n = read(hf->fd, buf, len);
    if (n < 0) return -1;
    return (int)n;
}

static int host_close(void *ctx, void *handle) {
    host_file *hf = (host_file *)handle;
    (void)ctx;
    if (!hf) return -1;
    close(hf->fd);
    free(hf);
    return 0;
}

static uint32_t host_now(void *ctx) {
    struct timespec ts;
    (void)ctx;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)((uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u);
}

static void host_log(void *ctx, const char *msg, uint32_t value) {
    (void)ctx;
    fprintf(stderr, "wifisync: %s %u\n", msg, value);
}

static int host_power(void *ctx, uint32_t state) {
    (void)ctx;
    fprintf(stderr, "wifisync: power -> %u\n", state);
    return 0;
}

static int host_should_stop(void *ctx) { (void)ctx; return g_stop ? 1 : 0; }

/* ---- sockets ---- */

typedef struct sock_conn {
    int fd;
} sock_conn;

static int sc_recv(void *ctx, void *buf, uint32_t len) {
    sock_conn *c = (sock_conn *)ctx;
    ssize_t n = recv(c->fd, buf, len, 0);
    if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return -2;
    if (n < 0) return -1;
    return (int)n;
}

static int sc_send(void *ctx, const void *buf, uint32_t len) {
    sock_conn *c = (sock_conn *)ctx;
    const uint8_t *p = (const uint8_t *)buf;
    uint32_t sent = 0;
    while (sent < len) {
        ssize_t n = send(c->fd, p + sent, len - sent, MSG_NOSIGNAL);
        if (n < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        sent += (uint32_t)n;
    }
    return 0;
}

static int sc_wait(void *ctx, uint32_t timeout_ms) {
    sock_conn *c = (sock_conn *)ctx;
    struct pollfd p;
    int r;
    p.fd = c->fd;
    p.events = POLLIN;
    p.revents = 0;
    r = poll(&p, 1, (int)timeout_ms);
    if (r < 0) return (errno == EINTR) ? 0 : -1;
    return r > 0 ? 1 : 0;
}

static void on_signal(int sig) { (void)sig; g_stop = 1; }

/* One maintenance tick: discover new photos, compute CRCs while idle, apply
 * the power state machine. Called between connections and between long-poll
 * slices, so /events wakes as soon as a scan enqueues a new photo. */
static void maintenance(void *ctx) {
    uint32_t now;
    (void)ctx;
    now = host_now(0);
    if ((uint32_t)(now - g_srv.last_scan_ms) >= 300u) {
        (void)ws_scan(&g_srv);
        (void)ws_crc_step(&g_srv);
        ws_power_tick(&g_srv, now);
    }
}

static void idle_cb(void *ctx) { maintenance(ctx); }

static void defaults(void) {
    memset(&g_cfg, 0, sizeof(g_cfg));
    snprintf(g_cfg.ssid, sizeof(g_cfg.ssid), "A6400-5G-SYNC");
    snprintf(g_cfg.psk, sizeof(g_cfg.psk), "wifisync-test-psk");
    snprintf(g_cfg.dcim_dir, sizeof(g_cfg.dcim_dir), "A:/DCIM/100MSDCF");
    g_cfg.first_index = 1;
    g_cfg.probe_forward = 64;
    g_cfg.probe_back = 8;
    g_cfg.min_jpg = 1024;
    g_cfg.min_arw = 100000;
    g_cfg.idle_sec = 0;              /* disabled in the host harness */
    g_cfg.channel = 36;
    g_cfg.width = 80;
    g_cfg.camera_id = 0xA6400001u;
}

int main(int argc, char **argv) {
    ws_fs_ops fs;
    ws_platform plat;
    uint16_t port = 8765;
    int duration = 20;
    int listener, one = 1;
    struct sockaddr_in addr;
    time_t start;

    defaults();
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--root") && i + 1 < argc) snprintf(g_root, sizeof(g_root), "%s", argv[++i]);
        else if (!strcmp(argv[i], "--port") && i + 1 < argc) port = (uint16_t)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--duration") && i + 1 < argc) duration = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--idle") && i + 1 < argc) g_cfg.idle_sec = (uint16_t)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--first") && i + 1 < argc) g_cfg.first_index = (uint32_t)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--forward") && i + 1 < argc) g_cfg.probe_forward = (uint32_t)atoi(argv[++i]);
        else {
            fprintf(stderr, "usage: %s --root DIR [--port N] [--duration S] [--idle S]\n", argv[0]);
            return 2;
        }
    }
    if (!g_root[0]) { fprintf(stderr, "--root is required\n"); return 2; }

    fs.ctx = 0; fs.stat = host_stat; fs.open = host_open; fs.read = host_read; fs.close = host_close;
    memset(&plat, 0, sizeof(plat));
    plat.ctx = 0; plat.now_ms = host_now; plat.log = host_log; plat.set_power = host_power;
    plat.should_stop = host_should_stop;
    ws_init(&g_srv, &g_cfg, &fs, &plat, g_io, sizeof(g_io));
    snprintf(g_srv.fw, sizeof(g_srv.fw), "host-ref");

    listener = socket(AF_INET, SOCK_STREAM, 0);
    if (listener < 0) { perror("socket"); return 1; }
    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons(port);
    if (bind(listener, (struct sockaddr *)&addr, sizeof(addr)) != 0) { perror("bind"); return 1; }
    if (listen(listener, 4) != 0) { perror("listen"); return 1; }

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    printf("wifisync-host ready port=%u root=%s\n", port, g_root);
    fflush(stdout);

    start = time(0);
    ws_power_on(&g_srv);
    while (!g_stop) {
        struct pollfd p;
        p.fd = listener;
        p.events = POLLIN;
        p.revents = 0;
        if (poll(&p, 1, 200) > 0 && (p.revents & POLLIN)) {
            int fd = accept(listener, 0, 0);
            if (fd >= 0) {
                struct timeval tv;
                sock_conn sc;
                ws_conn k;
                tv.tv_sec = 5;
                tv.tv_usec = 0;
                setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
                setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
                sc.fd = fd;
                k.ctx = &sc;
                k.recv = sc_recv;
                k.send = sc_send;
                k.wait = sc_wait;
                k.idle = idle_cb;
                (void)ws_http_serve(&g_srv, &k);
                close(fd);
            }
        }
        maintenance(0);
        if (duration > 0 && (time(0) - start) > duration) break;
    }
    close(listener);
    printf("wifisync-host done head=%u ack=%u pending=%u captures=%u files_served=%u bytes=%u errors=%u\n",
           g_srv.q.head, g_srv.q.ack, ws_queue_pending(&g_srv), g_srv.capture_count,
           g_srv.files_served, g_srv.bytes_sent, g_srv.errors);
    return 0;
}
