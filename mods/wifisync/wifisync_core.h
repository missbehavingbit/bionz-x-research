/* WiFiSync camera-side core: portable, freestanding, host-testable.
 *
 * The same code is compiled twice:
 *   - into the SMOD v2 mod for im.elf (Cortex-A5, -nostdlib), and
 *   - into the x86-64 host reference server used by the reference client test.
 *
 * Everything platform-specific (Sony Fsys read API, sockets, threads, Wi-Fi
 * power control) is behind the callbacks below, so the queue, Smart
 * Single-Best filter, HTTP/1.1 subset, Range handling and CRC-32 can be
 * verified offline without a camera.
 */
#ifndef WIFISYNC_CORE_H
#define WIFISYNC_CORE_H

#include <stdint.h>

#define WS_QUEUE_CAP        1024u
#define WS_NAME_MAX         13u          /* "DSC01234.ARW" + NUL */
#define WS_PATH_MAX         72u
#define WS_CHUNK_DEFAULT    (256u * 1024u)
#define WS_REQ_MAX          2048u
#define WS_EVENT_TIMEOUT_MS 20000u
#define WS_EVENT_SLICE_MS   200u
#define WS_BURST_QUIET_MS   300u
#define WS_MAX_RANGE        4096u        /* probe steps for the first catch-up */

/* DCF extension of a queued file. */
enum ws_ext {
    WS_EXT_NONE = 0,
    WS_EXT_JPG  = 1,
    WS_EXT_ARW  = 2
};

/* CRC state of a queued file. */
enum ws_crc_state {
    WS_CRC_NONE    = 0,   /* not computed and not requested */
    WS_CRC_PENDING = 1,   /* enqueued, background CRC not finished */
    WS_CRC_KNOWN   = 2
};

/* Power state machine (plan section 4.1). */
enum ws_power {
    WS_POWER_OFF     = 0,
    WS_POWER_STANDBY = 1,
    WS_POWER_ACTIVE  = 2
};

/* One queue record. 40 bytes; the plan budget allows 128 B, so the ring plus
 * the streaming buffer stays inside the 512 KiB envelope. */
typedef struct ws_entry {
    uint32_t seq;                    /* manifest id, 1-based, never reused */
    uint32_t index;                  /* DCF stem index (DSC0<index>) */
    uint32_t size;                   /* bytes reported by Fsys_Stat */
    uint32_t crc32;                  /* valid when crc_state == WS_CRC_KNOWN */
    uint32_t mtime;                  /* opaque timestamp from Fsys_Stat */
    uint16_t crc_state;
    uint8_t  ext;                    /* enum ws_ext */
    uint8_t  ready;                  /* 1 = size confirmed, safe to serve */
    char     name[WS_NAME_MAX];
} ws_entry;

typedef struct ws_queue {
    ws_entry slot[WS_QUEUE_CAP];
    uint32_t head;                   /* next seq to assign */
    uint32_t ack;                    /* every seq <= ack is acknowledged */
} ws_queue;

/* Read-only filesystem view (Sony Fsys on the camera, a RAM tree on the host). */
typedef struct ws_fs_ops {
    void *ctx;
    /* 0 on success, negative on error. `size` and `mtime` are written only on success. */
    int (*stat)(void *ctx, const char *path, uint32_t *size, uint32_t *mtime);
    /* 0 on success and *handle set; negative on error. Read-only open. */
    int (*open)(void *ctx, const char *path, void **handle);
    /* > 0 bytes read, 0 = EOF, < 0 = error. */
    int (*read)(void *ctx, void *handle, void *buf, uint32_t len);
    int (*close)(void *ctx, void *handle);
} ws_fs_ops;

/* Connection used by the HTTP layer; the adapter owns the socket. */
typedef struct ws_conn {
    void *ctx;
    /* > 0 bytes, 0 = peer closed, < 0 = error. May block. */
    int (*recv)(void *ctx, void *buf, uint32_t len);
    /* 0 on success, negative on error. Must send everything or fail. */
    int (*send)(void *ctx, const void *buf, uint32_t len);
    /* 1 = readable, 0 = timeout, < 0 = error. */
    int (*wait)(void *ctx, uint32_t timeout_ms);
    /* Optional: called between long-poll slices so the adapter can run its
     * maintenance loop (scan, CRC step, power tick) while a client waits.
     * This keeps the long poll single-threaded and race-free. */
    void (*idle)(void *ctx);
} ws_conn;

/* Clock, logging and AP control callbacks supplied by the adapter. */
typedef struct ws_platform {
    void *ctx;
    uint32_t (*now_ms)(void *ctx);
    void (*log)(void *ctx, const char *msg, uint32_t value);
    /* Change the 5 GHz AP power state (OFF / STANDBY / ACTIVE). 0 on success.
     * Called only on a real transition, never for the same state twice. */
    int (*set_power)(void *ctx, uint32_t state);
    /* Non-zero when the adapter wants the long poll to return immediately
     * (MLV2 OFF / USB mode change). May be NULL. */
    int (*should_stop)(void *ctx);
} ws_platform;

typedef struct ws_config {
    char     ssid[33];
    char     psk[64];
    char     dcim_dir[WS_PATH_MAX];  /* e.g. "A:/DCIM/100MSDCF" */
    char     prefix[5];              /* 4-char DCF filename prefix, default "DSC0" */
    uint32_t first_index;            /* fallback start when no cursor is known */
    uint32_t probe_forward;          /* steady-state window per scan */
    uint32_t probe_back;             /* resync window behind the cursor */
    uint32_t discover_span;          /* first-sweep span from first_index */
    uint32_t discover_miss_limit;    /* stop the first sweep after this many misses */
    uint32_t scan_budget;            /* probes per scan call (keeps the worker responsive) */
    uint32_t min_jpg;                /* completeness floor, bytes */
    uint32_t min_arw;                /* completeness floor, bytes */
    uint16_t idle_sec;               /* ACTIVE -> STANDBY timeout */
    uint8_t  channel;                /* non-DFS 5 GHz channel (36/40/44/48) */
    uint8_t  width;                  /* 80 / 40 / 20 */
    uint8_t  standby_mode;           /* 0 = low-power Wi-Fi, 1 = deep sleep */
    uint8_t  reserved;
    uint32_t camera_id;              /* stable identity reported by /status */
} ws_config;

typedef struct ws_server {
    ws_config   cfg;
    ws_queue    q;
    ws_fs_ops   fs;
    ws_platform plat;

    uint8_t  *io;                  /* streaming buffer, WS_CHUNK_DEFAULT */
    uint32_t    io_cap;
    uint32_t    crc_table[4][256];   /* slice-by-4, built at runtime (not .rodata) */
    uint32_t    crc_ready;

    uint32_t    power;               /* enum ws_power, last state applied */
    uint32_t    start_ms;
    uint32_t    last_activity_ms;
    uint32_t    last_capture_ms;
    uint32_t    last_scan_ms;
    uint32_t    events;              /* bumped on enqueue and on ack */
    uint32_t    capture_count;
    uint32_t    files_served;
    uint32_t    bytes_sent;
    uint32_t    errors;
    uint32_t    next_index;          /* DCF cursor: next stem to probe (0 = discovery not started) */
    uint32_t    scan_end;            /* exclusive end of the current probe window */
    uint32_t    miss_run;            /* consecutive misses in the current window */
    uint32_t    acked_index;         /* highest DCF index acknowledged (idempotent rescan) */
    uint8_t     discovering;         /* 1 while the bounded first sweep is running */
    uint8_t     window_added;        /* 1 when the current window produced a hit */
    uint8_t     pad2[2];
    uint32_t    boot_id;
    char        fw[24];              /* firmware/kernel identity string */
} ws_server;

/* ---- initialisation ---------------------------------------------------- */
void ws_init(ws_server *s, const ws_config *cfg, const ws_fs_ops *fs,
             const ws_platform *plat, uint8_t *io, uint32_t io_cap);
void ws_crc_init(ws_server *s);

/* ---- CRC-32 (IEEE 802.3, reflected, init/final 0xFFFFFFFF) -------------- */
uint32_t ws_crc32(const ws_server *s, const void *buf, uint32_t len);
uint32_t ws_crc32_update(const ws_server *s, uint32_t crc, const void *buf, uint32_t len);

/* ---- queue ------------------------------------------------------------- */
void ws_queue_reset(ws_server *s);
/* Append one selected file. Returns the new seq, or 0 when the queue is full,
 * the entry is a duplicate, or the file fails the completeness floor. */
uint32_t ws_queue_push(ws_server *s, uint32_t index, uint8_t ext,
                       const char *name, uint32_t size, uint32_t mtime);
int32_t  ws_queue_find(const ws_server *s, uint32_t seq);       /* slot or -1 */
uint32_t ws_queue_pending(const ws_server *s);
uint32_t ws_queue_next_pending(const ws_server *s);             /* seq or 0 */
/* Advance the watermark; only a contiguous prefix of acknowledgements moves it. */
void ws_queue_ack(ws_server *s, uint32_t seq);

/* ---- capture hook and scanning ---------------------------------------- */
/* Called from the ConPutWriteStill interposition: O(1), no I/O, no locks. */
void ws_note_capture(ws_server *s);
/* Probe the DCIM directory read-only and enqueue newly completed photos.
 * Returns the number of entries added, or a negative error. */
int ws_scan(ws_server *s);
/* One bounded background step: compute the CRC of the oldest pending record.
 * Returns 1 when work was done, 0 when idle, negative on error. */
int ws_crc_step(ws_server *s);

/* ---- power state machine ---------------------------------------------- */
/* OFF -> ACTIVE on the MLV2 menu ON press; ACTIVE -> STANDBY after the idle
 * timeout with an empty queue; any state -> OFF on the MLV2 OFF press. */
void ws_power_on(ws_server *s);
void ws_power_off(ws_server *s);
/* Apply transition rules. `now_ms` comes from the platform clock. */
void ws_power_tick(ws_server *s, uint32_t now_ms);
uint32_t ws_power_state(const ws_server *s);

/* ---- HTTP/1.1 subset --------------------------------------------------- */
/* Serve exactly one request on `c` and return. The adapter closes the socket. */
int ws_http_serve(ws_server *s, ws_conn *c);

/* ---- helpers shared with the adapters and tests ----------------------- */
uint32_t ws_strlen(const char *s);
int      ws_streq(const char *a, const char *b);
int      ws_strneq(const char *a, const char *b, uint32_t n);
void     ws_memcpy(void *dst, const void *src, uint32_t n);
void     ws_memset(void *dst, uint8_t v, uint32_t n);
int      ws_memcmp(const void *a, const void *b, uint32_t n);
int      ws_parse_u32(const char *s, uint32_t *out);
/* Build "<dir>/DSC0<index>.<EXT>" into out (WS_PATH_MAX). Returns 0 on success. */
int      ws_build_path(const ws_config *cfg, uint32_t index, uint8_t ext, char *out);
/* Build "DSC0<index>.<EXT>" into out (WS_NAME_MAX). */
int      ws_build_name(uint32_t index, uint8_t ext, char *out);
const char *ws_ext_name(uint8_t ext);

#endif /* WIFISYNC_CORE_H */
