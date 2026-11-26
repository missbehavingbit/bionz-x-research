/* WiFiSync offline verification suite.
 *
 * Freestanding (no libc) so the same object runs on the x86-64 host and inside
 * Unicorn as Cortex-A5 Thumb. Every check writes one {name, pass} record into
 * the exported table; the Unicorn driver prints them without trusting stdout.
 */
#include "wifisync_core.h"

#ifdef WS_CHECK_DEBUG
#include <stdio.h>
#define WS_DBG(...) do { fprintf(stderr, __VA_ARGS__); fflush(stderr); } while (0)
#else
#define WS_DBG(...) do { } while (0)
#endif

typedef struct ws_check_result {
    const char *name;
    uint32_t pass;
} ws_check_result;

#define WS_MAX_CHECKS 128u

ws_check_result g_checks[WS_MAX_CHECKS];
uint32_t g_check_count;
uint32_t g_check_fail;

static void chk(const char *name, int cond) {
    if (g_check_count >= WS_MAX_CHECKS) return;
    g_checks[g_check_count].name = name;
    g_checks[g_check_count].pass = cond ? 1u : 0u;
    g_check_count++;
    if (!cond) g_check_fail++;
}

/* ---------------------------------------------------------- fake platform */

typedef struct fake_file {
    uint32_t index;
    uint32_t size;
    uint32_t pos;
    uint8_t  ext;
    uint8_t  used;
} fake_file;

#define FAKE_FILES 16u

typedef struct fake_fs {
    fake_file f[FAKE_FILES];
    uint32_t  now;
    uint32_t  power_calls;
    uint32_t  last_power;
    uint32_t  log_lines;
} fake_fs;

static fake_fs g_fs;
static uint8_t g_io[4096];
static ws_server g_srv;

static const uint8_t *base_name_of(const char *path) {
    const char *p = path, *b = path;
    while (*p) { if (*p == '/') b = p + 1; p++; }
    return (const uint8_t *)b;
}

static int fake_parse(const char *path, uint32_t *index, uint8_t *ext) {
    const uint8_t *n = base_name_of(path);
    /* DSC0<4 digits>.JPG|ARW */
    uint32_t v = 0, i;
    if (n[0] != 'D' || n[1] != 'S' || n[2] != 'C' || n[3] != '0') return -1;
    for (i = 0; i < 4u; i++) {
        if (n[4 + i] < '0' || n[4 + i] > '9') return -1;
        v = v * 10u + (uint32_t)(n[4 + i] - '0');
    }
    if (n[8] != '.') return -1;
    if (n[9] == 'A' && n[10] == 'R' && n[11] == 'W') *ext = WS_EXT_ARW;
    else if (n[9] == 'J' && n[10] == 'P' && n[11] == 'G') *ext = WS_EXT_JPG;
    else return -1;
    *index = v;
    return 0;
}

static fake_file *fake_lookup(uint32_t index, uint8_t ext) {
    uint32_t i;
    for (i = 0; i < FAKE_FILES; i++)
        if (g_fs.f[i].used && g_fs.f[i].index == index && g_fs.f[i].ext == ext)
            return &g_fs.f[i];
    return 0;
}

static void fake_put(uint32_t index, uint8_t ext, uint32_t size) {
    uint32_t i;
    for (i = 0; i < FAKE_FILES; i++) {
        if (!g_fs.f[i].used) {
            g_fs.f[i].used = 1;
            g_fs.f[i].index = index;
            g_fs.f[i].ext = ext;
            g_fs.f[i].size = size;
            return;
        }
    }
}

static int fs_stat(void *ctx, const char *path, uint32_t *size, uint32_t *mtime) {
    uint32_t index;
    uint8_t ext;
    fake_file *f;
    (void)ctx;
    if (fake_parse(path, &index, &ext) != 0) return -1;
    f = fake_lookup(index, ext);
    if (!f) return -1;
    *size = f->size;
    *mtime = 0x20260000u + index;
    return 0;
}

static int fs_open(void *ctx, const char *path, void **handle) {
    uint32_t index;
    uint8_t ext;
    fake_file *f;
    (void)ctx;
    if (fake_parse(path, &index, &ext) != 0) return -1;
    f = fake_lookup(index, ext);
    if (!f) return -2;
    f->pos = 0;
    *handle = f;
    return 0;
}

static uint8_t pattern_byte(uint32_t index, uint32_t pos) {
    return (uint8_t)((index * 131u + pos * 31u + 7u) & 0xFFu);
}

static int fs_read(void *ctx, void *handle, void *buf, uint32_t len) {
    fake_file *f = (fake_file *)handle;
    uint8_t *out = (uint8_t *)buf;
    uint32_t i;
    (void)ctx;
    if (!f) return -1;
    if (f->pos >= f->size) return 0;
    if (len > f->size - f->pos) len = f->size - f->pos;
    for (i = 0; i < len; i++) out[i] = pattern_byte(f->index, f->pos + i);
    f->pos += len;
    return (int)len;
}

static int fs_close(void *ctx, void *handle) { (void)ctx; (void)handle; return 0; }
static uint32_t now_ms(void *ctx) { (void)ctx; return g_fs.now; }
static void plog(void *ctx, const char *msg, uint32_t value) {
    (void)ctx;
#ifdef WS_CHECK_DEBUG
    WS_DBG("log %s %u\n", msg, value);
#else
    (void)msg; (void)value;
#endif
    g_fs.log_lines++;
}
static int set_power(void *ctx, uint32_t state) {
    (void)ctx;
    g_fs.power_calls++;
    g_fs.last_power = state;
    return 0;
}

/* --------------------------------------------------------------- fake conn */

typedef struct fake_conn {
    ws_server *srv;
    char  req[1024];
    uint32_t req_len;
    uint32_t req_pos;
    char  resp[320000];
    uint32_t resp_len;
    uint32_t wait_slices;
    uint32_t wait_limit;
    int   wait_wakes;
    int   overflow;
} fake_conn;

static fake_conn g_conn;

static int fc_recv(void *ctx, void *buf, uint32_t len) {
    fake_conn *c = (fake_conn *)ctx;
    uint32_t left = c->req_len - c->req_pos;
    uint32_t n = (len < left) ? len : left;
    WS_DBG("fc_recv len=%u left=%u\n", len, left);
    if (n == 0) return 0;
    ws_memcpy(buf, c->req + c->req_pos, n);
    c->req_pos += n;
    return (int)n;
}

static int fc_send(void *ctx, const void *buf, uint32_t len) {
    fake_conn *c = (fake_conn *)ctx;
    if (c->resp_len + len > sizeof(c->resp)) { c->overflow = 1; return -1; }
    ws_memcpy(c->resp + c->resp_len, buf, len);
    c->resp_len += len;
    return 0;
}

static int fc_wait(void *ctx, uint32_t ms) {
    fake_conn *c = (fake_conn *)ctx;
    (void)ms;
    c->wait_slices++;
    /* Emulate a wake-up after N slices by fabricating a capture. */
    if (c->wait_wakes && c->wait_slices == c->wait_limit) {
        ws_note_capture(c->srv);
        return 1;
    }
    return 0;
}

static void conn_init(fake_conn *c, ws_server *s, const char *req) {
    ws_memset(c, 0, sizeof(*c));
    c->srv = s;
    c->req_len = ws_strlen(req);
    ws_memcpy(c->req, req, c->req_len);
}

static ws_conn conn_ops(fake_conn *c) {
    ws_conn k;
    k.ctx = c;
    k.recv = fc_recv;
    k.send = fc_send;
    k.wait = fc_wait;
    k.idle = 0;
    return k;
}

/* Independent bitwise CRC-32 reference (no table shared with the core). */
static uint32_t ref_crc32(const uint8_t *p, uint32_t n) {
    uint32_t crc = 0xFFFFFFFFu, i, b;
    for (i = 0; i < n; i++) {
        crc ^= p[i];
        for (b = 0; b < 8u; b++) crc = (crc & 1u) ? (0xEDB88320u ^ (crc >> 1)) : (crc >> 1);
    }
    return crc ^ 0xFFFFFFFFu;
}

static int contains(const char *hay, const char *needle) {
    uint32_t n = ws_strlen(needle);
    if (!n) return 1;
    while (*hay) {
        if (ws_strneq(hay, needle, n)) return 1;
        hay++;
    }
    return 0;
}

static const char *strstr_local(const char *h, const char *n) {
    uint32_t len = ws_strlen(n);
    if (!len) return h;
    while (*h) {
        if (ws_strneq(h, n, len)) return h;
        h++;
    }
    return 0;
}

/* Parse "Content-Length: N" out of a response head. */
static uint32_t resp_content_length(const char *r) {
    const char *p = r;
    while ((p = strstr_local(p, "Content-Length: ")) != 0) {
        uint32_t v = 0;
        if (ws_parse_u32(p + 16, &v) == 0) return v;
        p += 16;
    }
    return 0xFFFFFFFFu;
}

/* ------------------------------------------------------------------ checks */

static const char *resp_body(const char *resp) {
    const char *p = resp;
    while (*p) {
        if (ws_strneq(p, "\r\n\r\n", 4)) return p + 4;
        p++;
    }
    return 0;
}

static void reset_all(void) {
    ws_config cfg;
    ws_fs_ops fs;
    ws_platform plat;
    ws_memset(&g_fs, 0, sizeof(g_fs));
    ws_memset(&cfg, 0, sizeof(cfg));
    /* deterministic defaults */
    cfg.ssid[0] = 'A'; cfg.ssid[1] = '6'; cfg.ssid[2] = '4';   /* "A64" */
    cfg.psk[0] = 'x';
    cfg.dcim_dir[0] = 'A'; cfg.dcim_dir[1] = ':'; cfg.dcim_dir[2] = '/';
    cfg.dcim_dir[3] = 'D'; cfg.dcim_dir[4] = 'C'; cfg.dcim_dir[5] = 'I';
    cfg.dcim_dir[6] = 'M'; cfg.dcim_dir[7] = '/';
    cfg.dcim_dir[8] = '1'; cfg.dcim_dir[9] = '0'; cfg.dcim_dir[10] = '0';
    cfg.dcim_dir[11] = 'M'; cfg.dcim_dir[12] = 'S'; cfg.dcim_dir[13] = 'D';
    cfg.dcim_dir[14] = 'C'; cfg.dcim_dir[15] = 'F'; cfg.dcim_dir[16] = 0;
    cfg.first_index = 1;
    cfg.probe_forward = 16;
    cfg.probe_back = 4;
    cfg.discover_span = 9999;
    cfg.discover_miss_limit = 32;
    cfg.scan_budget = 128;
    cfg.min_jpg = 1000;
    cfg.min_arw = 100000;
    cfg.idle_sec = 45;
    cfg.channel = 36;
    cfg.width = 80;
    cfg.camera_id = 0xA6400001u;
    fs.ctx = &g_fs; fs.stat = fs_stat; fs.open = fs_open; fs.read = fs_read; fs.close = fs_close;
    ws_memset(&plat, 0, sizeof(plat));
    plat.ctx = &g_fs; plat.now_ms = now_ms; plat.log = plog; plat.set_power = set_power;
    ws_init(&g_srv, &cfg, &fs, &plat, g_io, sizeof(g_io));
    ws_memcpy(g_srv.fw, "test-1", 7);
    g_fs.now = 100000u;
}

static void run_protocol_checks(void) {
    ws_conn k;
    char req[256];
#define c g_conn

    /* /status */
    conn_init(&c, &g_srv, "GET /status HTTP/1.1\r\nHost: x\r\n\r\n");
    k = conn_ops(&c);
    {
        int rc = ws_http_serve(&g_srv, &k);
        chk("http_status_200", rc == 0 && contains(c.resp, "HTTP/1.1 200 OK"));
        chk("http_status_json", contains(c.resp, "\"power_state\":"));
        chk("http_status_identity", contains(c.resp, "a6400001"));
        chk("http_status_queue_len", contains(c.resp, "\"queue_len\":1"));
        chk("http_status_no_overflow", c.overflow == 0);
    }

    /* Manifest lists the queued ARW only. */
    conn_init(&c, &g_srv, "GET /manifest?since=0 HTTP/1.1\r\n\r\n");
    k = conn_ops(&c);
    {
        int rc = ws_http_serve(&g_srv, &k);
        chk("manifest_200", rc == 0 && contains(c.resp, "HTTP/1.1 200 OK"));
        chk("manifest_arw_only", contains(c.resp, "DSC00001.ARW") && !contains(c.resp, "DSC00001.JPG"));
        chk("manifest_size", contains(c.resp, "\"size\":200000"));
    }

    /* Full file: body length, CRC header and payload. */
    conn_init(&c, &g_srv, "GET /file/1 HTTP/1.1\r\n\r\n");
    k = conn_ops(&c);
    {
        int rc = ws_http_serve(&g_srv, &k);
        const char *body = resp_body(c.resp);
        uint32_t body_len = resp_content_length(c.resp) == 0xFFFFFFFFu ? 0 : resp_content_length(c.resp);
        chk("file_200", rc == 0 && contains(c.resp, "HTTP/1.1 200 OK"));
        chk("file_length", body_len == 200000u);
        chk("file_crc_header", contains(c.resp, "X-CRC32: "));
        chk("file_name_header", contains(c.resp, "X-File-Name: DSC00001.ARW"));
        chk("file_connection_close", contains(c.resp, "Connection: close"));
        chk("file_first_byte", body && (uint8_t)body[0] == pattern_byte(1, 0));
        chk("file_last_byte", body && (uint8_t)body[body_len - 1u] == pattern_byte(1, body_len - 1u));
    }

    /* Range resume: bytes=100000- must be the second half of the same pattern. */
    conn_init(&c, &g_srv, "GET /file/1 HTTP/1.1\r\nRange: bytes=100000-\r\n\r\n");
    k = conn_ops(&c);
    {
        int rc = ws_http_serve(&g_srv, &k);
        const char *body = resp_body(c.resp);
        chk("range_206", rc == 0 && contains(c.resp, "HTTP/1.1 206 Partial Content"));
        chk("range_content_range", contains(c.resp, "Content-Range: bytes 100000-199999/200000"));
        chk("range_length", resp_content_length(c.resp) == 100000u);
        chk("range_body_starts_at_offset",
            body && (uint8_t)body[0] == pattern_byte(1, 100000u));
        chk("range_body_ends_at_offset",
            body && (uint8_t)body[99999] == pattern_byte(1, 199999u));
    }

    /* Bounded Range with an explicit end. */
    conn_init(&c, &g_srv, "GET /file/1 HTTP/1.1\r\nRange: bytes=10-19\r\n\r\n");
    k = conn_ops(&c);
    {
        int rc = ws_http_serve(&g_srv, &k);
        const char *body = resp_body(c.resp);
        chk("range_bounded_206", rc == 0 && contains(c.resp, "Content-Range: bytes 10-19/200000"));
        chk("range_bounded_length", resp_content_length(c.resp) == 10u);
        chk("range_bounded_body", body && (uint8_t)body[0] == pattern_byte(1, 10u));
    }

    /* Ack is rejected when the reported size does not match. */
    conn_init(&c, &g_srv, "POST /ack/1 HTTP/1.1\r\nContent-Length: 15\r\n\r\n{\"size\":999999}");
    k = conn_ops(&c);
    {
        int rc = ws_http_serve(&g_srv, &k);
        chk("ack_size_mismatch_409", rc == 0 && contains(c.resp, "409"));
        chk("ack_mismatch_keeps_watermark", g_srv.q.ack == 0u);
    }

    /* Ack idempotence and watermark. */
    conn_init(&c, &g_srv, "POST /ack/1 HTTP/1.1\r\nContent-Length: 15\r\n\r\n{\"size\":200000}");
    k = conn_ops(&c);
    {
        int rc;
        WS_DBG("ack req_len=%u first=%c\n", c.req_len, c.req[0]);
        rc = ws_http_serve(&g_srv, &k);
        WS_DBG("ack rc=%d resp=%.120s\n", rc, c.resp);
        chk("ack_200", rc == 0 && contains(c.resp, "\"acked\":true"));
        chk("ack_watermark", g_srv.q.ack == 1u);
        chk("ack_index_memory", g_srv.acked_index == 1u);
    }
    conn_init(&c, &g_srv, "POST /ack/1 HTTP/1.1\r\nContent-Length: 0\r\n\r\n");
    k = conn_ops(&c);
    {
        int rc = ws_http_serve(&g_srv, &k);
        chk("ack_repeat_ok", rc == 0 && contains(c.resp, "\"acked\":true") &&
                             contains(c.resp, "\"duplicate\":true"));
        chk("ack_repeat_stable", g_srv.q.ack == 1u);
    }

    /* Unknown sequence. */
    conn_init(&c, &g_srv, "GET /file/99 HTTP/1.1\r\n\r\n");
    k = conn_ops(&c);
    {
        int rc = ws_http_serve(&g_srv, &k);
        chk("file_unknown_404", rc == 0 && contains(c.resp, "404"));
    }

    /* Long poll: wakes when a capture is noted inside the wait loop. */
    ws_memset(req, 0, sizeof(req));
    {
        const char *s = "GET /events?since=1 HTTP/1.1\r\n\r\n";
        uint32_t n = ws_strlen(s);
        ws_memcpy(req, s, n);
    }
    conn_init(&c, &g_srv, req);
    c.wait_wakes = 1;
    c.wait_limit = 2;
    k = conn_ops(&c);
    {
        int rc = ws_http_serve(&g_srv, &k);
        WS_DBG("events rc=%d resp=%.160s\n", rc, c.resp);
        chk("events_200", rc == 0 && contains(c.resp, "HTTP/1.1 200 OK"));
        chk("events_woke", contains(c.resp, "\"woke\":true"));
        chk("events_bounded", c.wait_slices <= 3u);
    }

    /* Bad request line. */
    conn_init(&c, &g_srv, "nonsense\r\n\r\n");
    k = conn_ops(&c);
    {
        int rc = ws_http_serve(&g_srv, &k);
        chk("bad_request_400", rc == 0 && contains(c.resp, "400"));
    }
#undef c
}

int ws_run_checks(void);

int ws_run_checks(void) {
    g_check_count = 0;
    g_check_fail = 0;

    /* ---- CRC-32 vectors ---- */
    reset_all();
    chk("crc32_empty", ws_crc32(&g_srv, "", 0) == 0u);
    chk("crc32_check_vector", ws_crc32(&g_srv, "123456789", 9) == 0xCBF43926u);
    {
        uint8_t buf[64];
        uint32_t i;
        for (i = 0; i < sizeof(buf); i++) buf[i] = (uint8_t)(i * 7u + 3u);
        chk("crc32_matches_bitwise", ws_crc32(&g_srv, buf, sizeof(buf)) == ref_crc32(buf, sizeof(buf)));
    }

    /* ---- names/paths ---- */
    {
        char name[WS_NAME_MAX];
        char path[WS_PATH_MAX];
        chk("name_arw", ws_build_name(1234, WS_EXT_ARW, name) == 0 &&
                       ws_streq(name, "DSC01234.ARW"));
        chk("name_jpg", ws_build_name(7, WS_EXT_JPG, name) == 0 &&
                        ws_streq(name, "DSC00007.JPG"));
        chk("name_rejects_5digit", ws_build_name(10000, WS_EXT_JPG, name) != 0);
        chk("path_build", ws_build_path(&g_srv.cfg, 42, WS_EXT_ARW, path) == 0 &&
                          ws_streq(path, "A:/DCIM/100MSDCF/DSC00042.ARW"));
    }

    /* ---- queue mechanics ---- */
    reset_all();
    chk("queue_empty", ws_queue_pending(&g_srv) == 0u && ws_queue_next_pending(&g_srv) == 0u);
    chk("queue_push_seq1", ws_queue_push(&g_srv, 1, WS_EXT_JPG, "DSC00001.JPG", 5000, 1) == 1u);
    chk("queue_duplicate_rejected",
        ws_queue_push(&g_srv, 1, WS_EXT_JPG, "DSC00001.JPG", 5000, 1) == 0u);
    chk("queue_below_floor_rejected",
        ws_queue_push(&g_srv, 2, WS_EXT_JPG, "DSC00002.JPG", 10, 1) == 0u);
    chk("queue_pending_one", ws_queue_pending(&g_srv) == 1u);
    chk("queue_find", ws_queue_find(&g_srv, 1) == 0);
    ws_queue_ack(&g_srv, 1);
    chk("queue_ack_moves", g_srv.q.ack == 1u && ws_queue_pending(&g_srv) == 0u);
    chk("queue_find_acked_gone", ws_queue_find(&g_srv, 1) < 0);

    /* ---- Smart Single-Best ---- */
    reset_all();
    fake_put(10, WS_EXT_JPG, 8000);
    fake_put(10, WS_EXT_ARW, 300000);
    chk("scan_both_enqueues", ws_scan(&g_srv) >= 0);
    {
        uint32_t seq;
        int arw = 0, jpg = 0;
        for (seq = 1; seq <= g_srv.q.head; seq++) {
            if (g_srv.q.slot[seq - 1u].ext == WS_EXT_ARW) arw++;
            if (g_srv.q.slot[seq - 1u].ext == WS_EXT_JPG) jpg++;
        }
        chk("smart_both_arw_only", arw == 1 && jpg == 0);
        chk("smart_both_name", arw && ws_streq(g_srv.q.slot[0].name, "DSC00010.ARW"));
    }

    reset_all();
    fake_put(20, WS_EXT_JPG, 8000);
    ws_scan(&g_srv);
    chk("smart_jpg_only", g_srv.q.head == 1u && g_srv.q.slot[0].ext == WS_EXT_JPG);

    reset_all();
    fake_put(30, WS_EXT_ARW, 300000);
    ws_scan(&g_srv);
    chk("smart_arw_only", g_srv.q.head == 1u && g_srv.q.slot[0].ext == WS_EXT_ARW);

    /* Late .ARW supersedes a queued .JPG and the watermark skips it. */
    reset_all();
    fake_put(40, WS_EXT_JPG, 8000);
    ws_scan(&g_srv);
    fake_put(40, WS_EXT_ARW, 300000);
    g_srv.next_index = 40;          /* re-probe the same stem */
    g_srv.scan_end = 41;
    ws_scan(&g_srv);
    chk("smart_late_arw_head2", g_srv.q.head == 2u);
    chk("smart_late_arw_supersedes",
        g_srv.q.slot[0].ext == WS_EXT_JPG && g_srv.q.slot[0].ready == 0u &&
        g_srv.q.slot[1].ext == WS_EXT_ARW && g_srv.q.slot[1].ready == 1u);
    ws_queue_ack(&g_srv, 2);
    chk("smart_superseded_skipped", g_srv.q.ack == 2u && g_srv.acked_index == 40u);

    /* Burst quiet window: no probe while a capture is under 300 ms old. */
    reset_all();
    fake_put(50, WS_EXT_ARW, 300000);
    ws_note_capture(&g_srv);        /* now == 100000 */
    g_fs.now += 100u;
    ws_scan(&g_srv);
    chk("burst_quiet_blocks_probe", g_srv.q.head == 0u);
    g_fs.now += 400u;
    g_srv.next_index = 50;
    g_srv.scan_end = 51;
    ws_scan(&g_srv);
    chk("burst_quiet_expires", g_srv.q.head == 1u);

    /* A growing file updates the queued record and re-arms its CRC. */
    reset_all();
    fake_put(60, WS_EXT_ARW, 300000);
    ws_scan(&g_srv);
    chk("growth_first_size", g_srv.q.head == 1u && g_srv.q.slot[0].size == 300000u);
    g_srv.q.slot[0].crc_state = WS_CRC_KNOWN;
    g_srv.q.slot[0].crc32 = 0x12345678u;
    fake_lookup(60, WS_EXT_ARW)->size = 400000;
    g_srv.next_index = 60;
    g_srv.scan_end = 61;
    ws_scan(&g_srv);
    chk("growth_updates_size", g_srv.q.head == 1u && g_srv.q.slot[0].size == 400000u);
    chk("growth_resets_crc", g_srv.q.slot[0].crc_state == WS_CRC_PENDING);

    /* ---- HTTP ---- */
    reset_all();
    fake_put(1, WS_EXT_JPG, 5000);
    fake_put(1, WS_EXT_ARW, 200000);
    ws_scan(&g_srv); g_fs.now += 400u; ws_scan(&g_srv);
    chk("http_setup_one_pending", ws_queue_pending(&g_srv) == 1u);
    run_protocol_checks();

    /* ---- power machine ---- */
    reset_all();
    chk("power_initial_off", ws_power_state(&g_srv) == WS_POWER_OFF);
    ws_power_on(&g_srv);
    chk("power_on_active", ws_power_state(&g_srv) == WS_POWER_ACTIVE &&
                          g_fs.last_power == WS_POWER_ACTIVE);
    g_fs.now += 46000u;
    ws_power_tick(&g_srv, g_fs.now);
    chk("power_idle_standby", ws_power_state(&g_srv) == WS_POWER_STANDBY);
    ws_note_capture(&g_srv);
    g_fs.now += 1000u;
    ws_power_tick(&g_srv, g_fs.now);
    chk("power_capture_active", ws_power_state(&g_srv) == WS_POWER_ACTIVE);
    ws_power_off(&g_srv);
    chk("power_off", ws_power_state(&g_srv) == WS_POWER_OFF);

    /* ---- cursor/discovery ---- */
    reset_all();
    fake_put(5000, WS_EXT_ARW, 300000);
    g_srv.cfg.first_index = 4990;
    g_srv.cfg.discover_span = 32;
    g_srv.next_index = 0;
    ws_scan(&g_srv);
    chk("discovery_window_finds_5000",
        g_srv.q.head == 1u && g_srv.q.slot[0].index == 5000u);
    chk("discovery_cursor_advances", g_srv.next_index > 5000u);

    reset_all();
    chk("scan_budget_bounds_work", g_srv.cfg.scan_budget == 128u);

    return (int)g_check_fail;
}
