/* a6400 live-view daemon core: one process, one video fd.
 *
 * This is the merge of the two pieces that have only ever been run separately:
 * the composite session runner (freeze PID 149 -> publish entry hook -> INIT ->
 * hold -> STOP -> freeze -> retire) and the 30 fps sensor feeder (OSAL -> DMM
 * rule 5 -> SET_PATH -> GET_JPEG -> DMM open -> WAIT -> UPDATE). The single-fd
 * constraint is the reason for the merge: a second open() of the video node
 * failed with -12/ENOMEM (E0339:52-57).
 *
 * STATUS: offline implementation. It has never been executed on a camera. It is
 * gated on a transport-enabled composite module that is auditable and actually
 * streams (open boundaries B3/B5), on the physical test plan in README.md, and
 * on explicit authorization for any camera-module session.
 *
 * SAFETY SHAPE
 *   - Every destructive step (ptrace freeze, INIT, REG_BUF, ENABLE, UPDATE,
 *     SET_PATH start, DMM rule 5) is refused unless --arm-transport is given,
 *     and each one is logged BEFORE it happens.
 *   - --dry-run performs discovery only: open + identity + usb state + DMA
 *     query. It never freezes, initialises, registers, enables or updates.
 *   - Unknown values fail closed with an explicit code; nothing is fabricated.
 *   - Exit codes: 0 clean, 2 stage failure, 3 refused, 4 fatal.
 */
#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#include "a6400_daemon.h"
#include "a6400_frame.h"
#include "a6400_internal.h"
#include "a6400_log.h"
#include "a6400_osal.h"
#include "a6400_recovery.h"
#include "a6400_sdlog.h"
#include "a6400_uvc.h"
#include "a6400_uyvy_jpeg.h"
#include "a6400_wifi.h"

#define A6400_EXIT_CLEAN    0
#define A6400_EXIT_STAGE    2
#define A6400_EXIT_REFUSED  3
#define A6400_EXIT_FATAL    4

#define A6400_GET_JPEG_TIMEOUT_MS 50u
#define A6400_MAX_CONSECUTIVE_ERRORS 30u
#define A6400_PREFIX_MAX 2048u
#define A6400_PATH_MAX 200u

/* ---- process state ------------------------------------------------------- */

static volatile sig_atomic_t g_stop;
static volatile sig_atomic_t g_alarm_fired;
static struct a6400_log g_log;
static struct a6400_daemon_config g_cfg;
static struct a6400_daemon_stats g_stats;
static struct a6400_sd_status g_sd;
static const char *g_stage = "init";
static int g_exit_code = A6400_EXIT_FATAL;
static char g_fail_reason[192];

/* Receipt-only lifecycle/discovery facts. These live here (not in the structs
 * owned by other translation units) so that a6400_daemon_receipt() can render
 * them without exposing internal handles to the control layer. */
static int g_rc_init_attempted;
static int g_rc_session_active;
static int g_rc_hook_published;
static int g_rc_hook_retired;
static int g_rc_stop_verified;
static int g_rc_teardown_verified; /* clean gate OR a verified bounded retry */
static uint32_t g_rc_stop_attempts;
static uint32_t g_rc_tid_count;
static uint32_t g_rc_freeze_ms;
static uint32_t g_rc_attest_completed;
static int32_t g_rc_attest_error;
static int g_rc_dma_query_rc;
static int g_rc_meta_source; /* 0 none, 1 MOD_QUERY_DMA, 2 REG_BUF-acknowledged */
static uint32_t g_rc_dma_phys;
static uint32_t g_rc_dma_total;
static uint32_t g_rc_slot_size;
static uint32_t g_rc_max_payload;
static int g_rc_reg_buf_rc;
static int g_rc_enable_rc;
static uint32_t g_control_errors;
static uint32_t g_control_requests;
static uint32_t g_loop_ticks;
static uint32_t g_deadline_misses;
static uint32_t g_prefix_reads;
static uint32_t g_prefix_unavailable;
static uint32_t g_get_jpeg_errors;
static uint32_t g_wait_timeouts;

/* Operator-supplied fallbacks that a6400_daemon_config does not carry. */
static uint32_t g_dma_phys_opt;
static uint32_t g_dma_size_opt;
static uint32_t g_stats_interval_s;
static int g_dmesg_sd;
static uint32_t g_wait_bound_ms = 1000u;
static int g_read_prefix;
static int g_arm_transport;
static int g_wifi_stream;
static int g_use_liveview_cmd;
static int g_probe_pins;
static int g_splice_scalar;
static uint8_t g_splice_pin_no = 0xffu; /* 0xff = pin 0 */
static uint32_t g_splice_downstream_qid;
static int g_splice_active;
static int g_probe_yuv;
static uint32_t g_yuv_stream_type;
static int g_feed_liveview;
static uint32_t g_feed_liveview_max;
static uint16_t g_feed_last_handle;
static uint32_t g_feed_pushed;
static uint32_t g_connect_pin_qid;
static uint32_t g_connect_pin_no;
static uint32_t g_connect_dst_pin;
static uint16_t g_wifi_stream_port = A6400_WIFI_DEFAULT_STREAM;
static uint16_t g_wifi_http_port = A6400_WIFI_DEFAULT_HTTP;
static uint16_t g_wifi_ctrl_port = A6400_WIFI_DEFAULT_CTRL;
/* E0358: bounded recovery after a failed stop attestation. On by default
 * because the alternative is a guaranteed battery pull; --no-park-recovery
 * restores the old fail-closed park for a conservative run. */
static int g_park_recovery = 1;
static struct a6400_recovery g_recovery;

struct daemon_rt {
    struct a6400_uvc uvc;
    struct a6400_uipc uipc;
    struct a6400_dmm dmm;
    struct a6400_runner runner;
    struct a6400_frame_queue q;
    struct a6400_uvc_dma_meta meta;
    struct a6400_control_ctx *ctl;
    struct a6400_wifi_server wifi;
    int wifi_open;
    int uvc_open;
    int uipc_open;
    int init_attempted;
    int reg_buf_ok;
    int enable_ok;
    int mem_fd;
    int session_active;
    int hook_retired;
    uint32_t exp_w;
    uint32_t exp_h;
    uint32_t frame_seq;
    uint32_t consecutive_errors;
    uint32_t admit_rejects;
};

/* ---- clock --------------------------------------------------------------- */

int64_t a6400_now_ns(void)
{
    struct timespec ts;

    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
        return 0;
    return (int64_t)ts.tv_sec * 1000000000LL + (int64_t)ts.tv_nsec;
}

uint64_t a6400_now_ms(void)
{
    int64_t ns = a6400_now_ns();

    return ns <= 0 ? 0u : (uint64_t)(ns / 1000000LL);
}

/* ---- logging / failure helpers ------------------------------------------- */

static void sig_handler(int sig)
{
    (void)sig;
    g_stop = 1;
}

static void alarm_handler(int sig)
{
    (void)sig;
    g_alarm_fired = 1;
}

static const char *status_name(int code)
{
    switch (code) {
    case A6400_EXIT_CLEAN:    return "clean";
    case A6400_EXIT_STAGE:    return "stage_failed";
    case A6400_EXIT_REFUSED:  return "refused";
    case A6400_EXIT_FATAL:    return "fatal";
    case A6400_EXIT_DEGRADED: return "degraded_teardown";
    default:                  return "unknown";
    }
}

static int stage_fail(const char *stage_name, int code, const char *fmt, ...)
{
    va_list ap;

    g_stage = stage_name;
    g_exit_code = code;
    va_start(ap, fmt);
    vsnprintf(g_fail_reason, sizeof g_fail_reason, fmt, ap);
    va_end(ap);
    a6400_logf(&g_log, A6400_LOG_ERROR, "stage_failed stage=%s code=%d reason=%s",
               stage_name, code, g_fail_reason);
    return code;
}

/* Emits one structured event line. `fields` is a caller-formatted JSON fragment
 * without the enclosing braces, or NULL for no extra fields. */
static void log_event(const char *event, const char *fields_fmt, ...)
{
    char fields[384];
    char line[512];
    va_list ap;
    int n;

    fields[0] = '\0';
    if (fields_fmt) {
        va_start(ap, fields_fmt);
        vsnprintf(fields, sizeof fields, fields_fmt, ap);
        va_end(ap);
    }
    if (fields[0])
        n = snprintf(line, sizeof line, "{\"event\":\"%s\",%s}", event, fields);
    else
        n = snprintf(line, sizeof line, "{\"event\":\"%s\"}", event);
    if (n < 0 || (size_t)n >= sizeof line)
        return;
    a6400_log_json(&g_log, line);
}

/* ---- JSON string builder for the receipt --------------------------------- */

struct sbuf {
    char    *buf;
    uint32_t cap;
    uint32_t len;
    int      overflow;
};

static void sb_init(struct sbuf *s, char *buf, uint32_t cap)
{
    s->buf = buf;
    s->cap = cap;
    s->len = 0;
    s->overflow = 0;
    if (cap)
        buf[0] = '\0';
}

static void sb_add(struct sbuf *s, const char *fmt, ...)
{
    va_list ap;
    int n;

    if (!s || s->overflow || s->len >= s->cap) {
        if (s)
            s->overflow = 1;
        return;
    }
    va_start(ap, fmt);
    n = vsnprintf(s->buf + s->len, s->cap - s->len, fmt, ap);
    va_end(ap);
    if (n < 0 || (uint32_t)n >= s->cap - s->len) {
        s->overflow = 1;
        return;
    }
    s->len += (uint32_t)n;
}

static void sb_path(struct sbuf *s, const char *path)
{
    char esc[2048];

    if (!path) {
        sb_add(s, "null");
        return;
    }
    if (!a6400_log_json_escape(esc, (uint32_t)sizeof esc, path)) {
        sb_add(s, "\"\"");
        return;
    }
    sb_add(s, "\"%s\"", esc);
}

/* ---- receipt ------------------------------------------------------------- */

uint32_t a6400_daemon_receipt(const struct a6400_daemon_stats *st,
                              const struct a6400_daemon_config *cfg,
                              char *buf, uint32_t cap)
{
    struct sbuf s;
    struct a6400_log_sd_stats sdstats;
    uint64_t uptime = 0;

    if (!st || !cfg || !buf || !cap)
        return 0;
    memset(&sdstats, 0, sizeof sdstats);
    a6400_log_sd_get(&g_log, &sdstats);
    if (st->started_monotonic_ns)
        uptime = (uint64_t)(a6400_now_ns() - (int64_t)st->started_monotonic_ns) / 1000000ull;

    sb_init(&s, buf, cap);
    sb_add(&s, "{\"schema\":\"a6400-lived-receipt/1\",");
    sb_add(&s, "\"build_status\":\"offline_unverified\",");
    sb_add(&s, "\"status\":\"%s\",\"exit_code\":%d,", status_name(g_exit_code), g_exit_code);
    sb_add(&s, "\"stage\":\"%s\",", g_stage ? g_stage : "unknown");
    sb_add(&s, "\"fail_reason\":");
    sb_path(&s, g_fail_reason[0] ? g_fail_reason : NULL);
    sb_add(&s, ",\"uptime_ms\":%llu,", (unsigned long long)uptime);
    sb_add(&s, "\"started_monotonic_ns\":%lld,",
           (long long)st->started_monotonic_ns);

    sb_add(&s, "\"config\":{\"device\":");
    sb_path(&s, cfg->device_path);
    sb_add(&s, ",\"log\":");
    sb_path(&s, cfg->log_path);
    sb_add(&s, ",\"receipt\":");
    sb_path(&s, cfg->receipt_path);
    sb_add(&s, ",\"control_fifo\":");
    sb_path(&s, cfg->control_fifo);
    sb_add(&s, ",\"control_ack\":");
    sb_path(&s, cfg->control_ack);
    sb_add(&s, ",\"wait_pcremote\":%d,\"wait_timeout_s\":%d,\"duration_s\":%d,",
           cfg->wait_pcremote, cfg->wait_timeout_s, cfg->duration_s);
    sb_add(&s, "\"target_pid\":%d,\"expected_width\":%d,\"expected_height\":%d,",
           cfg->target_pid, cfg->expected_width, cfg->expected_height);
    sb_add(&s, "\"fps\":%d,\"comp_level\":%d,\"path_width\":%d,",
           cfg->fps, cfg->comp_level, cfg->path_width);
    sb_add(&s, "\"dry_run\":%d,\"verbose\":%d,\"arm_transport\":%d,\"read_prefix\":%d,",
           cfg->dry_run, cfg->verbose, g_arm_transport, g_read_prefix);
    sb_add(&s, "\"park_recovery\":%d,", g_park_recovery);
    sb_add(&s, "\"stats_interval_s\":%u,\"wait_bound_ms\":%u,",
           g_stats_interval_s, g_wait_bound_ms);
    sb_add(&s, "\"dma_phys_opt\":%u,\"dma_size_opt\":%u,", g_dma_phys_opt, g_dma_size_opt);
    sb_add(&s, "\"sd_log\":%d,\"sd_mount\":%d,\"dmesg_sd\":%d,\"sd_dev\":",
           cfg->sd_log, cfg->sd_mount, cfg->dmesg_sd);
    sb_path(&s, cfg->sd_dev);
    sb_add(&s, ",\"sd_mountpoint\":");
    sb_path(&s, cfg->sd_mountpoint);
    sb_add(&s, "},");

    sb_add(&s, "\"lifecycle\":{\"init_attempted\":%d,\"session_active\":%d,",
           g_rc_init_attempted, g_rc_session_active);
    sb_add(&s, "\"hook_published\":%d,\"hook_retired\":%d,\"stop_verified\":%d,",
           g_rc_hook_published, g_rc_hook_retired, g_rc_stop_verified);
    sb_add(&s, "\"stop_attempts\":%u,\"teardown_verified\":%d,",
           g_rc_stop_attempts, g_rc_teardown_verified);
    sb_add(&s, "\"freeze_tid_count\":%u,\"freeze_ms\":%u,",
           g_rc_tid_count, g_rc_freeze_ms);
    sb_add(&s, "\"attest_version\":2,\"attest_owner\":%d,"
               "\"attest_completed\":%u,\"attest_error\":%d},",
           cfg->target_pid, g_rc_attest_completed, g_rc_attest_error);

    sb_add(&s, "\"discovery\":{\"dma_query_rc\":%d,\"dma_meta_source\":%d,",
           g_rc_dma_query_rc, g_rc_meta_source);
    sb_add(&s, "\"dma_phys\":%u,\"dma_total\":%u,\"slot_size\":%u,\"max_frame_payload\":%u,",
           g_rc_dma_phys, g_rc_dma_total, g_rc_slot_size, g_rc_max_payload);
    sb_add(&s, "\"reg_buf_rc\":%d,\"enable_rc\":%d},", g_rc_reg_buf_rc, g_rc_enable_rc);

    sb_add(&s, "\"stats\":{\"phase\":%u,\"session_active\":%u,"
               "\"frames_captured\":%u,\"frames_submitted\":%u,"
               "\"frames_dropped\":%u,\"frames_rejected\":%u,",
           st->phase, st->session_active, st->frames_captured, st->frames_submitted,
           st->frames_dropped, st->frames_rejected);
    sb_add(&s, "\"transport_errors\":%u,\"upload_errors\":%u,"
               "\"get_jpeg_polls\":%u,\"get_jpeg_empty\":%u,",
           st->transport_errors, st->upload_errors, st->get_jpeg_polls, st->get_jpeg_empty);
    sb_add(&s, "\"last_frame_size\":%u,\"last_frame_seq\":%u,"
               "\"last_width\":%u,\"last_height\":%u,",
           st->last_frame_size, st->last_frame_seq, st->last_width, st->last_height);
    sb_add(&s, "\"wait_ms_max\":%u,\"accept_geometry\":%u},", st->wait_ms_max, st->accept_geometry);

    sb_add(&s, "\"diagnostics\":{\"loop_ticks\":%u,\"deadline_misses\":%u,",
           g_loop_ticks, g_deadline_misses);
    sb_add(&s, "\"prefix_reads\":%u,\"prefix_unavailable\":%u,"
               "\"get_jpeg_errors\":%u,\"wait_timeouts\":%u,",
           g_prefix_reads, g_prefix_unavailable, g_get_jpeg_errors, g_wait_timeouts);
    sb_add(&s, "\"control_requests\":%u,\"control_errors\":%u},",
           g_control_requests, g_control_errors);

    /* Bounded park recovery (E0358). `status` is not_attempted on every clean
     * path and on a run that never reached teardown; `pending` is what the
     * durable pre-recovery receipt records, so a battery pull in the middle of
     * the attempt cannot present it as recovered. `state_before/after` are the
     * module's own {phase,pinned,hook_mask,error} words, -1 filled when the
     * status query did not answer (never invented). */
    sb_add(&s, "\"recovery\":{\"enabled\":%d,\"attempted\":%d,\"status\":",
           g_park_recovery, g_recovery.attempted);
    sb_path(&s, a6400_recovery_status_name(g_recovery.status));
    sb_add(&s, ",\"evidence_written\":%d,\"state_before_valid\":%d,",
           g_recovery.evidence_written, g_recovery.state_before_valid);
    sb_add(&s, "\"state_before\":[%d,%d,%d,%d],",
           g_recovery.state_before[0], g_recovery.state_before[1],
           g_recovery.state_before[2], g_recovery.state_before[3]);
    sb_add(&s, "\"stop_retry_rc\":%d,\"state_after_valid\":%d,",
           g_recovery.stop_retry_rc, g_recovery.state_after_valid);
    sb_add(&s, "\"state_after\":[%d,%d,%d,%d],",
           g_recovery.state_after[0], g_recovery.state_after[1],
           g_recovery.state_after[2], g_recovery.state_after[3]);
    sb_add(&s, "\"stop_retry_verified\":%d,\"freeze_rc\":%d,\"frozen_tids\":%u,",
           g_recovery.stop_retry_verified, g_recovery.freeze_rc,
           g_recovery.frozen_tids);
    sb_add(&s, "\"hook_retire_rc\":%d,\"hook_retired\":%d,\"resume_rc\":%d,"
               "\"closed\":%d,\"failed_step\":",
           g_recovery.retire_rc, g_recovery.hook_retired, g_recovery.resume_rc,
           g_recovery.closed);
    sb_path(&s, a6400_recovery_step_name(g_recovery.failed_step));
    sb_add(&s, ",\"detail\":");
    sb_path(&s, g_recovery.detail[0] ? g_recovery.detail : NULL);
    sb_add(&s, "},");

    /* Durable crash-safe SD status (E0350): every field is the observed value,
     * including refusals and failures. `mounted` is 1 only after mount(2), an
     * exact /proc/mounts match and a sentinel round-trip. */
    sb_add(&s, "\"sd_log\":{\"enabled\":%d,\"mount_requested\":%d,\"attempted\":%d,",
           g_sd.enabled, g_sd.mount_requested, g_sd.attempted);
    sb_add(&s, "\"mounted\":%d,\"device_ok\":%d,\"verified\":%d,\"sentinel_ok\":%d,",
           g_sd.mounted, g_sd.device_ok, g_sd.verified, g_sd.sentinel_ok);
    sb_add(&s, "\"mount_rc\":%d,\"unmount_rc\":%d,\"open_rc\":%d,\"sync_mode\":%d,",
           g_sd.mount_rc, g_sd.unmount_rc, g_sd.open_rc, g_sd.sync_mode);
    sb_add(&s, "\"receipt_written\":%d,\"errors\":%u,\"rotations\":%u,",
           g_sd.receipt_written, g_sd.errors, g_sd.rotations);
    sb_add(&s, "\"trace_lines\":%u,\"trace_errors\":%u,\"device\":",
           g_sd.trace_lines, g_sd.trace_errors);
    sb_path(&s, g_sd.device);
    sb_add(&s, ",\"mountpoint\":");
    sb_path(&s, g_sd.mountpoint);
    sb_add(&s, ",\"reason\":");
    sb_path(&s, g_sd.reason[0] ? g_sd.reason : NULL);
    sb_add(&s, "},\"log_sd_lines\":%u,\"log_sd_errors\":%u}\n",
           sdstats.lines, sdstats.errors);

    if (s.overflow)
        return 0;
    return s.len;
}

static int write_receipt(void)
{
    /* Sized for the full schema plus the sd_log block; an overflow would lose
     * the durable receipt entirely, so the margin is deliberate. */
    char buf[6144];
    uint32_t n;
    int fd, rc = 0;
    uint32_t done = 0;

    if (!g_cfg.receipt_path || !g_cfg.receipt_path[0])
        return -EINVAL;
    n = a6400_daemon_receipt(&g_stats, &g_cfg, buf, (uint32_t)sizeof buf);
    if (!n) {
        a6400_logf(&g_log, A6400_LOG_ERROR, "receipt_serialize_failed cap=%u",
                   (unsigned)sizeof buf);
        return -ENOSPC;
    }

    fd = open(g_cfg.receipt_path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0) {
        a6400_logf(&g_log, A6400_LOG_ERROR, "receipt_open_failed path=%s errno=%d",
                   g_cfg.receipt_path, errno);
        return -errno;
    }
    while (done < n) {
        ssize_t w = write(fd, buf + done, (size_t)(n - done));

        if (w < 0 && errno == EINTR)
            continue;
        if (w <= 0) {
            rc = -EIO;
            break;
        }
        done += (uint32_t)w;
    }
    if (rc == 0 && fsync(fd) != 0)
        rc = -EIO;
    (void)close(fd);
    /* E0350: when the SD card is mounted and verified, copy the same receipt to
     * A6400RCPT.JSON with O_SYNC. This copy is what survives a battery pull; a
     * failure here is counted in g_sd.errors, never hidden. */
    if (rc == 0 && g_sd.enabled && g_sd.mounted)
        (void)a6400_sd_write_receipt(&g_sd, buf, n);
    a6400_logf(&g_log, A6400_LOG_INFO, "receipt_written path=%s bytes=%u rc=%d",
               g_cfg.receipt_path, done, rc);
    return rc;
}

/* ---- /dev/mem bounded prefix read --------------------------------------- */

/* Reads at most A6400_PREFIX_MAX bytes of the frame at `phys`. Never fabricates
 * a byte: on any failure the caller must treat the prefix as unavailable. */
static int read_frame_prefix(int mem_fd, uint32_t phys, uint32_t mapped,
                             uint8_t *out, uint32_t *out_len)
{
    uint32_t want;

    if (!out || !out_len || mem_fd < 0)
        return -1;
    *out_len = 0;
    want = mapped < A6400_PREFIX_MAX ? mapped : A6400_PREFIX_MAX;
    if (want < 2u || phys > 0xffffffffu - want)
        return -1;

    if (g_read_prefix) {
        /* Opt-in: mmap the page range of the DMM window and copy the prefix. */
        uint32_t page = phys & ~0xfffu;
        uint32_t off = phys - page;
        uint32_t map_len = (off + want + 0xfffu) & ~0xfffu;
        void *m = mmap(NULL, map_len, PROT_READ, MAP_SHARED, mem_fd, (off_t)page);

        if (m == MAP_FAILED)
            return -1;
        memcpy(out, (const uint8_t *)m + off, want);
        (void)munmap(m, map_len);
        *out_len = want;
        return 0;
    }

    {
        /* Default: one bounded pread. The frame is at a physical address, so
         * /dev/mem is the only reader; the read is capped at 64 bytes. */
        ssize_t n = pread(mem_fd, out, want, (off_t)phys);

        if (n < 2)
            return -1;
        *out_len = (uint32_t)n;
    }
    return 0;
}

/* ---- bounded WAIT -------------------------------------------------------- */

/* WAIT (0xf002) is a blocking ioctl whose kernel-side maximum is unknown
 * (open boundary B4). Bound it from userspace with an interval timer that is
 * allowed to interrupt the ioctl, and report the measured duration. If the
 * driver sleeps uninterruptibly this bound cannot help; that limitation is
 * documented in README.md and is a physical-test variable. */
static int bounded_wait(struct a6400_uvc *uvc, uint32_t timeout_ms,
                        uint32_t *elapsed_ms)
{
    struct sigaction sa, old_sa;
    struct itimerval it, old_it;
    uint64_t t0, t1;
    int rc;

    if (elapsed_ms)
        *elapsed_ms = 0;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = alarm_handler;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = 0; /* no SA_RESTART: the ioctl must see EINTR */
    if (sigaction(SIGALRM, &sa, &old_sa) != 0)
        return -1;

    memset(&it, 0, sizeof it);
    it.it_value.tv_sec = (time_t)(timeout_ms / 1000u);
    it.it_value.tv_usec = (suseconds_t)((timeout_ms % 1000u) * 1000u);
    g_alarm_fired = 0;
    if (setitimer(ITIMER_REAL, &it, &old_it) != 0) {
        (void)sigaction(SIGALRM, &old_sa, NULL);
        return -1;
    }

    t0 = (uint64_t)a6400_now_ns();
    rc = a6400_uvc_wait(uvc);
    t1 = (uint64_t)a6400_now_ns();

    memset(&it, 0, sizeof it);
    (void)setitimer(ITIMER_REAL, &it, NULL);
    (void)sigaction(SIGALRM, &old_sa, NULL);

    if (elapsed_ms)
        *elapsed_ms = (uint32_t)((t1 - t0) / 1000000ull);
    /* Only a real EINTR is a bound hit. `g_alarm_fired` alone is not enough:
     * the timer can fire between the ioctl returning and the disarm, and a
     * completed wait must not be turned into a false timeout. */
    if (rc == -EINTR)
        return -ETIMEDOUT;
    return rc;
}


/* Resolves the destination window. probe_only=1 never fails and never invents a
 * window: it is the dry-run discovery probe. probe_only=0 either takes
 * MOD_QUERY_DMA or requires the operator-supplied --dma-phys/--dma-size
 * fallback; a fabricated fixed window is refused. */
static int resolve_dma_window(struct daemon_rt *rt, int probe_only)
{
    g_rc_dma_query_rc = a6400_uvc_query_dma(&rt->uvc, &rt->meta);
    if (g_rc_dma_query_rc == 0) {
        /* Slot capacity is total/2 and the driver writes a 12-byte header, so
         * max_frame_payload must be slot-12 (uvc_composite_shim.c:1002-1020). */
        if (rt->meta.max_frame_payload > rt->meta.slot_size) {
            a6400_logf(&g_log, A6400_LOG_WARN,
                       "dma_query_inconsistent slot=%u max_payload=%u",
                       rt->meta.slot_size, rt->meta.max_frame_payload);
            rt->meta.max_frame_payload = rt->meta.slot_size >
                                                 A6400_UVC_HEADER_SIZE
                                             ? rt->meta.slot_size - A6400_UVC_HEADER_SIZE
                                             : 0;
        }
        g_rc_meta_source = 1;
        g_rc_dma_phys = rt->meta.phys_addr;
        g_rc_dma_total = rt->meta.total_size;
        g_rc_slot_size = rt->meta.slot_size;
        g_rc_max_payload = rt->meta.max_frame_payload;
        log_event("dma_window",
                  "\"source\":\"mod_query_dma\",\"phys\":%u,\"total\":%u,"
                  "\"slot\":%u,\"max_payload\":%u",
                  rt->meta.phys_addr, rt->meta.total_size, rt->meta.slot_size,
                  rt->meta.max_frame_payload);
        return 0;
    }
    if (g_rc_dma_query_rc == -ENOTTY) {
        /* Stock driver: no such ioctl (E0340:9-11). A REG_BUF-acknowledged
         * window needs operator-supplied geometry; inventing one would be a
         * fabricated address. */
        a6400_logf(&g_log, A6400_LOG_WARN,
                   "mod_query_dma_unavailable rc=%d (ENOTTY); "
                   "fallback requires --dma-phys/--dma-size",
                   g_rc_dma_query_rc);
    } else {
        a6400_logf(&g_log, A6400_LOG_WARN, "mod_query_dma_failed rc=%d",
                   g_rc_dma_query_rc);
    }
    if (probe_only)
        return -1;

    if (!g_dma_phys_opt || !g_dma_size_opt) {
        (void)stage_fail("dma_window", A6400_EXIT_STAGE,
                         "no DMA window: MOD_QUERY_DMA rc=%d and "
                         "--dma-phys/--dma-size were not supplied",
                         g_rc_dma_query_rc);
        return -1;
    }
    rt->meta.phys_addr = g_dma_phys_opt;
    rt->meta.total_size = g_dma_size_opt;
    rt->meta.slot_size = g_dma_size_opt / 2u;
    if (rt->meta.slot_size <= A6400_UVC_HEADER_SIZE) {
        (void)stage_fail("dma_window", A6400_EXIT_STAGE,
                         "--dma-size %u leaves no room after the %u-byte header",
                         g_dma_size_opt, A6400_UVC_HEADER_SIZE);
        return -1;
    }
    rt->meta.max_frame_payload = rt->meta.slot_size - A6400_UVC_HEADER_SIZE;
    g_rc_meta_source = 2;
    g_rc_dma_phys = rt->meta.phys_addr;
    g_rc_dma_total = rt->meta.total_size;
    g_rc_slot_size = rt->meta.slot_size;
    g_rc_max_payload = rt->meta.max_frame_payload;
    log_event("dma_window",
              "\"source\":\"reg_buf_acknowledged\",\"phys\":%u,\"total\":%u,"
              "\"slot\":%u,\"max_payload\":%u",
              rt->meta.phys_addr, rt->meta.total_size, rt->meta.slot_size,
              rt->meta.max_frame_payload);
    return 0;
}

/* ---- frame loop ---------------------------------------------------------- */

static void release_units(struct daemon_rt *rt, uint32_t handle1, uint32_t handle2)
{
    /* Close then free, both handles (feeder:673-694). The DMM unit must be
     * closed before it is freed; freeing first would be an unmodelled order. */
    if (handle1) {
        (void)a6400_dmm_close_unit(&rt->dmm, handle1);
        (void)a6400_dmm_free_unit(&rt->dmm, handle1);
    }
    if (handle2)
        (void)a6400_dmm_free_unit(&rt->dmm, handle2);
}

/* 0 = continue, 1 = fatal (stage already recorded). */
static int stream_tick(struct daemon_rt *rt, const struct a6400_daemon_config *cfg)
{
    struct a6400_jpeg_result jr;
    struct a6400_frame_candidate cand;
    struct a6400_jpeg_dims dims;
    uint8_t prefix[A6400_PREFIX_MAX];
    uint32_t prefix_len = 0;
    uint32_t phys = 0, mapped = 0;
    int rc, slot;

    (void)cfg;

    g_stats.get_jpeg_polls++;
    rc = a6400_scalar_get_jpeg(&rt->dmm, A6400_GET_JPEG_TIMEOUT_MS, &jr);
    if (rc < 0) {
        g_stats.transport_errors++;
        g_get_jpeg_errors++;
        rt->consecutive_errors++;
        a6400_logf(&g_log, A6400_LOG_WARN, "get_jpeg_transport_error rc=%d consecutive=%u",
                   rc, rt->consecutive_errors);
        if (rt->consecutive_errors >= A6400_MAX_CONSECUTIVE_ERRORS) {
            (void)stage_fail("stream", A6400_EXIT_STAGE,
                             "get_jpeg failed %u times in a row (last rc=%d)",
                             rt->consecutive_errors, rc);
            return 1;
        }
        return 0;
    }
    if (rc == 1) {
        static uint32_t last_err = 0xFFFFFFFFu;
        if (jr.err != last_err || g_stats.get_jpeg_empty == 0u ||
            (g_stats.get_jpeg_empty % 300u) == 0u) {
            last_err = jr.err;
            a6400_logf(&g_log, A6400_LOG_INFO,
                       "get_jpeg_empty count=%u err=%u ready=%u",
                       g_stats.get_jpeg_empty + 1u, jr.err, (unsigned)jr.ready);
        }
        g_stats.get_jpeg_empty++;
        return 2;
    }
    rt->consecutive_errors = 0;

    if (!jr.handle1 || jr.handle1 == 0xffffu || !jr.size_upper_bound) {
        a6400_logf(&g_log, A6400_LOG_WARN,
                   "jpeg_rejected reason=empty_handle handle1=%u size_bound=%u",
                   jr.handle1, jr.size_upper_bound);
        release_units(rt, 0, jr.handle2);
        g_stats.frames_rejected++;
        return 0;
    }

    if (g_wifi_stream) {
        /* Drain any older frames queued inside ScalarInfra so we always send
         * the freshest possible hardware JPEG (zero FIFO backlog). */
        int drain;
        for (drain = 0; drain < 4; drain++) {
            struct a6400_jpeg_result newer;
            int drc = a6400_scalar_get_jpeg(&rt->dmm, 5, &newer);
            if (drc != 0 || !newer.handle1 || newer.handle1 == 0xffffu ||
                !newer.size_upper_bound) {
                if (drc == 0) {
                    (void)a6400_dmm_free_unit(&rt->dmm, newer.handle1);
                    (void)a6400_dmm_free_unit(&rt->dmm, newer.handle2);
                }
                break;
            }
            (void)a6400_dmm_free_unit(&rt->dmm, jr.handle1);
            (void)a6400_dmm_free_unit(&rt->dmm, jr.handle2);
            jr = newer;
            g_stats.frames_dropped++;
        }
    }

    rc = a6400_dmm_open_unit(&rt->dmm, jr.handle1, &phys, &mapped);
    if (rc != 0) {
        a6400_logf(&g_log, A6400_LOG_WARN, "dmm_open_failed handle=%u rc=%d",
                   jr.handle1, rc);
        release_units(rt, jr.handle1, jr.handle2);
        g_stats.transport_errors++;
        return 0;
    }

    if (rt->mem_fd >= 0 &&
        read_frame_prefix(rt->mem_fd, phys, mapped, prefix, &prefix_len) == 0) {
        g_prefix_reads++;
    } else {
        prefix_len = 0;
        g_prefix_unavailable++;
        if (g_prefix_unavailable == 1u || (g_prefix_unavailable % 300u) == 0u) {
            /* Never fabricate an SOI. The admission gate decides what a frame
             * without a prefix is allowed to be. */
            a6400_logf(&g_log, A6400_LOG_WARN,
                       "prefix_unavailable count=%u phys=%u mapped=%u",
                       g_prefix_unavailable, phys, mapped);
        }
    }

    memset(&cand, 0, sizeof cand);
    cand.handle1 = jr.handle1;
    cand.handle2 = jr.handle2;
    cand.phys_addr = phys;
    cand.mapped_size = mapped;
    cand.size_shift = jr.size_shift;
    cand.frame_seq = ++rt->frame_seq;
    cand.capture_ns = a6400_now_ns();
    cand.prefix = prefix_len ? prefix : NULL;
    cand.prefix_len = prefix_len;

    slot = a6400_frame_admit(&rt->q, &cand, &rt->meta,
                             g_wifi_stream ? 0u : rt->exp_w,
                             g_wifi_stream ? 0u : rt->exp_h);
    g_stats.frames_dropped = rt->q.dropped;
    g_stats.frames_rejected = rt->q.rejected;
    if (slot < 0) {
        /* A persistently rejected stream would otherwise log at 30 Hz and burn
         * the 256 KiB cap; keep the first rejection and then every 300th. */
        if (rt->admit_rejects++ == 0u || (rt->admit_rejects % 300u) == 0u) {
            if (slot == A6400_FRAME_ERR_JPEG && !prefix_len)
                a6400_logf(&g_log, A6400_LOG_WARN,
                           "admit_rejected rc=%d seq=%u reason=prefix_unavailable",
                           slot, cand.frame_seq);
            else
                a6400_logf(&g_log, A6400_LOG_WARN,
                           "admit_rejected rc=%d seq=%u size_bound=%u phys=0x%08x mapped=%u "
                           "p0=%02x%02x%02x%02x%02x%02x%02x%02x",
                           slot, cand.frame_seq, jr.size_upper_bound, phys, mapped,
                           prefix[0], prefix[1], prefix[2], prefix[3],
                           prefix[4], prefix[5], prefix[6], prefix[7]);
        }
        release_units(rt, jr.handle1, jr.handle2);
        return 0;
    }
    g_stats.frames_captured++;

    if (prefix_len &&
        a6400_jpeg_parse_dimensions(prefix, prefix_len, &dims) == 0) {
        g_stats.last_width = dims.width;
        g_stats.last_height = dims.height;
        if (!rt->exp_w || (g_wifi_stream && (rt->exp_w != dims.width || rt->exp_h != dims.height))) {
            /* Geometry is discovered from the first real frame, and updated on
             * photo/movie aspect ratio switches (e.g. 1024x680 <-> 1024x576). */
            rt->exp_w = dims.width;
            rt->exp_h = dims.height;
            g_stats.accept_geometry = 1u;
            g_stats.last_frame_size = jr.size_upper_bound;
            log_event("geometry_discovered", "\"width\":%u,\"height\":%u",
                      dims.width, dims.height);
        }
    }

    if (g_wifi_stream) {
        static uint8_t wifi_jpg_buf[262144];
        uint32_t frame_len = jr.size_upper_bound;
        uint64_t t_start = (uint64_t)a6400_now_ns();
        uint64_t t_copy = t_start, t_send = t_start;
        uint32_t actual_len = 0;
        if (!frame_len || frame_len > mapped)
            frame_len = mapped;
        if (frame_len > sizeof wifi_jpg_buf)
            frame_len = (uint32_t)sizeof wifi_jpg_buf;
        if (rt->mem_fd >= 0 && frame_len >= 4u) {
            uint32_t page_off = phys & 0xfffu;
            uint32_t map_len = (page_off + frame_len + 0xfffu) & ~0xfffu;
            void *map = mmap(NULL, map_len, PROT_READ, MAP_SHARED,
                             rt->mem_fd, (off_t)(phys & ~0xfffu));
            if (map != MAP_FAILED) {
                uint32_t k;
                actual_len = frame_len;
                /* Burst-copy from uncached /dev/mem into cached RAM once. */
                memcpy(wifi_jpg_buf, (const uint8_t *)map + page_off, frame_len);
                munmap(map, map_len);
                for (k = frame_len; k >= 2u; k--) {
                    if (wifi_jpg_buf[k - 2u] == 0xffu &&
                        wifi_jpg_buf[k - 1u] == 0xd9u) {
                        actual_len = k;
                        break;
                    }
                }
                t_copy = (uint64_t)a6400_now_ns();
                (void)a6400_wifi_server_send_frame(&rt->wifi, cand.frame_seq,
                                                   cand.capture_ns, wifi_jpg_buf,
                                                   actual_len, 1u);
                t_send = (uint64_t)a6400_now_ns();
                g_stats.frames_submitted++;
                g_stats.last_frame_size = actual_len;
                g_stats.last_frame_seq = cand.frame_seq;
                g_stats.frames_dropped = (uint32_t)(rt->wifi.stream_dropped + rt->wifi.http_dropped);
            } else {
                g_stats.transport_errors++;
            }
        }
        a6400_frame_release(&rt->q, (uint32_t)slot);
        release_units(rt, jr.handle1, jr.handle2);
        if (cand.frame_seq >= 20u && cand.frame_seq <= 25u) {
            uint64_t t_rel = (uint64_t)a6400_now_ns();
            a6400_logf(&g_log, A6400_LOG_INFO,
                       "wifi_timing seq=%u bytes=%u copy_us=%llu send_us=%llu rel_us=%llu",
                       cand.frame_seq, actual_len,
                       (unsigned long long)((t_copy - t_start) / 1000ull),
                       (unsigned long long)((t_send - t_copy) / 1000ull),
                       (unsigned long long)((t_rel - t_send) / 1000ull));
        }
        return 0;
    }

    {
        uint32_t wait_ms = 0;

        rc = bounded_wait(&rt->uvc, g_wait_bound_ms, &wait_ms);
        if (wait_ms > g_stats.wait_ms_max)
            g_stats.wait_ms_max = wait_ms;
        if (rc != 0) {
            if (rc == -EBUSY) {
                /* Tolerated by the physically run feeder (line 718): no UPDATE
                 * for this frame, slot returned, loop continues. */
                a6400_logf(&g_log, A6400_LOG_INFO,
                           "wait_busy seq=%u elapsed_ms=%u", cand.frame_seq, wait_ms);
                g_stats.upload_errors++;
                a6400_frame_abort(&rt->q);
                a6400_frame_release(&rt->q, (uint32_t)slot);
                release_units(rt, jr.handle1, jr.handle2);
                return 0;
            }
            g_stats.transport_errors++;
            g_stats.upload_errors++;
            g_wait_timeouts++;
            rt->consecutive_errors++;
            a6400_logf(&g_log, A6400_LOG_WARN,
                       "wait_failed rc=%d elapsed_ms=%u consecutive=%u",
                       rc, wait_ms, rt->consecutive_errors);
            a6400_frame_abort(&rt->q);
            a6400_frame_release(&rt->q, (uint32_t)slot);
            release_units(rt, jr.handle1, jr.handle2);
            if (rt->consecutive_errors >= A6400_MAX_CONSECUTIVE_ERRORS) {
                (void)stage_fail("stream", A6400_EXIT_STAGE, "WAIT failed repeatedly");
                return 1;
            }
            return 0;
        }
    }

    {
        uint32_t frame_len = jr.size_upper_bound;
        uint32_t slot_phys = rt->meta.phys_addr + (uint32_t)slot * rt->meta.slot_size;

        /* The exact JPEG length is not reported by the camera: size_shift<<7 is
         * an upper bound within 127 bytes (E0144:21). Upload the bound, clamped
         * to the mapped unit so the driver never reads past it. */
        if (!frame_len || frame_len > mapped)
            frame_len = mapped;

        rc = a6400_uvc_update(&rt->uvc, frame_len, slot_phys);
        if (rc == 0) {
            g_stats.frames_submitted++;
            g_stats.last_frame_size = frame_len;
            g_stats.last_frame_seq = cand.frame_seq;
            a6400_frame_release(&rt->q, (uint32_t)slot);
            /* Progress event every 300 submitted frames (~10 s at 30 fps).
             * Never per frame: the log is capped at 256 KiB. */
            if ((g_stats.frames_submitted % 300u) == 1u) {
                log_event("frame_progress",
                          "\"seq\":%u,\"size\":%u,\"slot\":%d,\"phys\":%u,"
                          "\"submitted\":%u",
                          cand.frame_seq, frame_len, slot, slot_phys,
                          g_stats.frames_submitted);
            }
        } else {
            g_stats.transport_errors++;
            g_stats.upload_errors++;
            rt->consecutive_errors++;
            a6400_logf(&g_log, A6400_LOG_WARN,
                       "update_failed rc=%d size=%u phys=%u consecutive=%u",
                       rc, frame_len, slot_phys, rt->consecutive_errors);
            a6400_frame_abort(&rt->q);
            a6400_frame_release(&rt->q, (uint32_t)slot);
            release_units(rt, jr.handle1, jr.handle2);
            if (rt->consecutive_errors >= A6400_MAX_CONSECUTIVE_ERRORS) {
                (void)stage_fail("stream", A6400_EXIT_STAGE, "UPDATE failed repeatedly");
                return 1;
            }
            return 0;
        }
    }

    release_units(rt, jr.handle1, jr.handle2);
    return 0;
}

static void handle_stream_config(struct daemon_rt *rt,
                                 const struct a6400_ctl_stream_config *cfg)
{
    if (cfg->width && cfg->height &&
        (rt->exp_w != cfg->width || rt->exp_h != cfg->height)) {
        /* Retuning geometry means re-issuing SET_PATH, whose accepted width
         * gate for selector 0x50 is NOT decoded (LIVEVIEW_DMA.md:691-697).
         * Refuse to pretend: keep the discovered geometry and say so. */
        a6400_logf(&g_log, A6400_LOG_WARN,
                   "stream_config_geometry_ignored requested=%ux%u accepted=%ux%u",
                   cfg->width, cfg->height, rt->exp_w, rt->exp_h);
    }
    log_event("stream_config", "\"width\":%u,\"height\":%u,\"fps\":%u,\"quality\":%u",
              (unsigned)cfg->width, (unsigned)cfg->height, (unsigned)cfg->fps,
              (unsigned)cfg->quality);
}

static void rearm_scalar_pipeline(struct daemon_rt *rt,
                                  const struct a6400_daemon_config *cfg)
{
    int rc;
    struct a6400_scalar_path_cfg sc;

    if (!rt->uipc_open || !cfg)
        return;
    rc = a6400_dmm_set_rule(&rt->dmm, 5, 0);
    a6400_logf(&g_log, A6400_LOG_INFO, "rearm_dmm_rule_5 status=%d", rc);
    memset(&sc, 0, sizeof sc);
    sc.timeout_ms = 1500;
    rc = a6400_scalar_set_path(&rt->dmm, &sc);
    a6400_logf(&g_log, A6400_LOG_INFO, "rearm_set_path_reset status=%d", rc);
    memset(&sc, 0, sizeof sc);
    sc.enable = 2; /* PinConnect (payload+8 = 6) */
    sc.rate_fps = (uint32_t)cfg->fps;
    sc.width = (uint32_t)cfg->path_width;
    sc.comp_level = (uint32_t)cfg->comp_level;
    sc.param_val = 50;
    sc.timeout_ms = 5000;
    rc = a6400_scalar_set_path(&rt->dmm, &sc);
    a6400_logf(&g_log, A6400_LOG_INFO,
               "rearm_set_path_pinconnect status=%d rate=%u width=%u",
               rc, sc.rate_fps, sc.width);
    memset(&sc, 0, sizeof sc);
    sc.enable = 1;
    sc.rate_fps = (uint32_t)cfg->fps;
    sc.width = (uint32_t)cfg->path_width;
    sc.comp_level = (uint32_t)cfg->comp_level;
    sc.param_val = 50;
    sc.timeout_ms = 5000;
    sc.pin_no = g_splice_scalar ? 0xffu : 0u;
    rc = a6400_scalar_set_path(&rt->dmm, &sc);
    a6400_logf(&g_log, A6400_LOG_INFO,
               "rearm_set_path_start status=%d rate=%u width=%u comp=%u",
               rc, sc.rate_fps, sc.width, sc.comp_level);
    log_event("rearm_scalar_pipeline", "\"status\":%d,\"rate\":%u,\"width\":%u",
              rc, sc.rate_fps, sc.width);
}

static int poll_control(struct daemon_rt *rt,
                        const struct a6400_daemon_config *cfg)
{
    int i;

    if (!rt->ctl)
        return 0;
    if (g_wifi_stream && rt->wifi_open) {
        struct a6400_ctl_action wact;
        memset(&wact, 0, sizeof wact);
        (void)a6400_wifi_server_poll(&rt->wifi, rt->ctl, &wact);
        if (wact.kind == A6400_CTL_ACTION_STREAM_STOP ||
            wact.kind == A6400_CTL_ACTION_SHUTDOWN_REQUEST) {
            a6400_logf(&g_log, A6400_LOG_INFO, "wifi_control_stop kind=%d",
                       (int)wact.kind);
            g_stop = 1;
            return 0;
        }
        if (wact.kind == A6400_CTL_ACTION_STREAM_START) {
            log_event("control_stream_start", NULL);
            if (!g_yuv_stream_type && !g_splice_scalar)
                rearm_scalar_pipeline(rt, cfg);
        } else if (wact.kind == A6400_CTL_ACTION_STREAM_CONFIG) {
            handle_stream_config(rt, &wact.config);
        }
    }
    for (i = 0; i < 4; i++) {
        struct a6400_ctl_action act;
        int rc = a6400_control_poll(rt->ctl, &act);

        if (rc == 0)
            return 0;
        if (rc < 0) {
            g_control_errors++;
            a6400_logf(&g_log, A6400_LOG_WARN, "control_poll_error rc=%d", rc);
            return 0;
        }
        g_control_requests++;
        if (act.kind == A6400_CTL_ACTION_STREAM_STOP ||
            act.kind == A6400_CTL_ACTION_SHUTDOWN_REQUEST) {
            a6400_logf(&g_log, A6400_LOG_INFO, "control_stop_request kind=%d",
                       (int)act.kind);
            g_stop = 1;
            return 0;
        }
        if (act.kind == A6400_CTL_ACTION_STREAM_START)
            log_event("control_stream_start", NULL);
        else if (act.kind == A6400_CTL_ACTION_STREAM_CONFIG)
            handle_stream_config(rt, &act.config);
    }
    return 0;
}

static const uint8_t *yc_find_section(const uint8_t *base, uint32_t len,
                                      uint32_t wanted_id)
{
    uint32_t count = 0, off = 0, i;

    if (!base || len < 24u)
        return NULL;
    memcpy(&count, base + 12, sizeof count);
    memcpy(&off, base + 16, sizeof off);
    if (count > 64u)
        return NULL;
    for (i = 0; i < count; i++) {
        uint32_t id = 0, sec_off = 0, next_off = 0;

        if (off > len - 24u)
            return NULL;
        memcpy(&id, base + off + 4, sizeof id);
        if (id == wanted_id) {
            memcpy(&sec_off, base + off + 12, sizeof sec_off);
            if (sec_off >= len)
                return NULL;
            return base + sec_off;
        }
        memcpy(&next_off, base + off + 20, sizeof next_off);
        off = next_off;
    }
    return NULL;
}

static int find_dmm_handles_by_type(uint32_t want_type, uint16_t *out, int max_n)
{
    static char buf[32768];
    int fd = open("/proc/udm/dmm_units", O_RDONLY);
    size_t total = 0;
    int count = 0;
    char *p, *end;

    if (fd < 0 || !out || max_n <= 0)
        return 0;
    while (total + 1u < sizeof buf) {
        size_t chunk = sizeof buf - 1u - total;
        ssize_t r;

        if (chunk > 4096u)
            chunk = 4096u;
        r = read(fd, buf + total, chunk);
        if (r <= 0)
            break;
        total += (size_t)r;
    }
    close(fd);
    buf[total] = '\0';

    p = buf;
    end = buf + total;
    while (p < end && count < max_n) {
        char *nl = memchr(p, '\n', (size_t)(end - p));
        unsigned int handle = 0, utype = 0, st = 0, alloc = 0, op = 0;
        unsigned int rdv = 0, ser = 0, rel = 0, addr = 0, sz = 0;
        int n;

        if (nl)
            *nl = '\0';
        n = sscanf(p, "%x %x %x %x %x %x %x %x %x %x",
                   &handle, &utype, &st, &alloc, &op,
                   &rdv, &ser, &rel, &addr, &sz);
        if (n >= 10 && utype == want_type &&
            addr >= 0x10000000u && addr < 0x40000000u &&
            sz >= 4096u && handle >= 1u && handle <= 0xfffeu) {
            out[count++] = (uint16_t)handle;
        }
        if (!nl)
            break;
        p = nl + 1;
    }
    return count;
}

static uint16_t find_latest_yc_handle(void)
{
    static char buf[32768];
    int fd = open("/proc/udm/dmm_units", O_RDONLY);
    size_t total = 0;
    uint16_t best_handle = 0;
    uint32_t best_rel = 0;
    int found = 0;
    char *p, *end;

    if (fd < 0)
        return 0;
    while (total + 1u < sizeof buf) {
        size_t chunk = sizeof buf - 1u - total;
        ssize_t r;

        if (chunk > 4096u)
            chunk = 4096u;
        r = read(fd, buf + total, chunk);
        if (r <= 0)
            break;
        total += (size_t)r;
    }
    close(fd);
    buf[total] = '\0';

    p = buf;
    end = buf + total;
    while (p < end) {
        char *nl = memchr(p, '\n', (size_t)(end - p));
        unsigned int handle = 0, utype = 0, st = 0, alloc = 0, op = 0;
        unsigned int rdv = 0, ser = 0, rel = 0, addr = 0, sz = 0;
        int n;

        if (nl)
            *nl = '\0';
        n = sscanf(p, "%x %x %x %x %x %x %x %x %x %x",
                   &handle, &utype, &st, &alloc, &op,
                   &rdv, &ser, &rel, &addr, &sz);
        if (n >= 10 && utype == 0x000e0b00u && sz >= 0x1000u &&
            handle >= 1u && handle <= 0xfffeu) {
            if (!found || (int32_t)(rel - best_rel) > 0) {
                best_rel = rel;
                best_handle = (uint16_t)handle;
                found = 1;
            }
        }
        if (!nl)
            break;
        p = nl + 1;
    }
    return best_handle;
}

static void feed_one_liveview_yc(struct daemon_rt *rt)
{
    uint16_t handle;
    uint32_t phys = 0, mapped = 0;
    uint32_t page, offset, map_len;
    void *m;
    const uint8_t *base, *sec0, *sec6;
    uint8_t flag0 = 0, flag2c = 0;
    uint32_t yuv_type = 0;
    uint16_t yuv_handle = 0xffffu, sec6_handle = 0xffffu;
    uint16_t w = 0, h = 0;
    int yuv_need_ref = 0, sec6_valid = 0;
    uint16_t ref_out = 0, yuv_ref_out = 0;
    int rc;

    if (!rt || !rt->uipc_open || rt->mem_fd < 0)
        return;
    if (g_feed_liveview_max && g_feed_pushed >= g_feed_liveview_max)
        return;

    handle = find_latest_yc_handle();
    if (!handle) {
        static int logged_no_yc;
        if (!logged_no_yc) {
            logged_no_yc = 1;
            a6400_logf(&g_log, A6400_LOG_WARN, "feed_yc_no_handle_in_dmm_units");
        }
        return;
    }
    if (handle == g_feed_last_handle)
        return;

    rc = a6400_dmm_open_unit(&rt->dmm, handle, &phys, &mapped);
    if (rc != 0 || !phys || mapped < 0x1000u) {
        if (g_feed_pushed < 3u)
            a6400_logf(&g_log, A6400_LOG_WARN,
                       "feed_yc_open_failed handle=0x%04x rc=%d mapped=%u",
                       handle, rc, mapped);
        return;
    }

    page = phys & ~4095u;
    offset = phys - page;
    map_len = (offset + 0x1000u + 4095u) & ~4095u;
    m = mmap(NULL, map_len, PROT_READ, MAP_SHARED, rt->mem_fd, (off_t)page);
    if (m == MAP_FAILED) {
        static int logged_mmap_err;
        if (!logged_mmap_err) {
            logged_mmap_err = 1;
            a6400_logf(&g_log, A6400_LOG_WARN,
                       "feed_yc_mmap_failed handle=0x%04x phys=0x%08x errno=%d",
                       handle, phys, errno);
        }
        (void)a6400_dmm_close_unit(&rt->dmm, handle);
        return;
    }

    base = (const uint8_t *)m + offset;
    sec0 = yc_find_section(base, 0x1000u, 0u);
    if (sec0 && (size_t)(sec0 - base) + 48u <= 0x1000u) {
        flag0 = sec0[0];
        flag2c = sec0[0x2c];
        memcpy(&yuv_type, sec0 + 4, sizeof yuv_type);
        memcpy(&yuv_handle, sec0 + 8, sizeof yuv_handle);
        memcpy(&w, sec0 + 28, sizeof w);
        memcpy(&h, sec0 + 32, sizeof h);
        if (flag2c != 3u && flag0 == 1u &&
            yuv_handle >= 1u && yuv_handle <= 0xfffeu)
            yuv_need_ref = 1;
    }
    sec6 = yc_find_section(base, 0x1000u, 6u);
    if (sec6 && (size_t)(sec6 - base) + 0xb0u <= 0x1000u) {
        memcpy(&sec6_handle, sec6 + 0xac, sizeof sec6_handle);
        if (sec6_handle >= 1u && sec6_handle <= 0xfffeu)
            sec6_valid = 1;
    }

    munmap(m, map_len);
    (void)a6400_dmm_close_unit(&rt->dmm, handle);

    if (g_feed_pushed < 3u) {
        a6400_logf(&g_log, A6400_LOG_INFO,
                   "feed_yc_desc handle=0x%04x phys=0x%08x %ux%u "
                   "flag0=%u flag2c=%u yuv_type=0x%08x yuv_h=0x%04x "
                   "need_ref=%d sec6_h=0x%04x",
                   handle, phys, w, h, flag0, flag2c, yuv_type, yuv_handle,
                   yuv_need_ref, sec6_handle);
    }
    if (!sec0 || sec6_valid) {
        if (g_feed_pushed < 3u)
            a6400_logf(&g_log, A6400_LOG_WARN,
                       "feed_yc_skip handle=0x%04x sec0=%d sec6_valid=%d",
                       handle, sec0 ? 1 : 0, sec6_valid);
        g_feed_last_handle = handle;
        return;
    }

    rc = a6400_dmm_ref_unit(&rt->dmm, 0x000e0b00u, handle, &ref_out);
    if (rc != 0) {
        a6400_logf(&g_log, A6400_LOG_WARN,
                   "feed_yc_ref_desc_failed handle=0x%04x rc=%d", handle, rc);
        return;
    }
    if (yuv_need_ref) {
        rc = a6400_dmm_ref_unit(&rt->dmm, yuv_type, yuv_handle, &yuv_ref_out);
        if (rc != 0) {
            a6400_logf(&g_log, A6400_LOG_WARN,
                       "feed_yc_ref_yuv_failed type=0x%08x h=0x%04x rc=%d",
                       yuv_type, yuv_handle, rc);
            (void)a6400_dmm_free_unit(&rt->dmm, handle);
            return;
        }
    }

    rc = a6400_scalar_push_yc(&rt->dmm, handle, 0x000e0b00u);
    if (rc != 0) {
        a6400_logf(&g_log, A6400_LOG_WARN,
                   "feed_yc_push_failed handle=0x%04x rc=%d", handle, rc);
        if (yuv_need_ref)
            (void)a6400_dmm_free_unit(&rt->dmm, yuv_handle);
        (void)a6400_dmm_free_unit(&rt->dmm, handle);
        return;
    }

    g_feed_last_handle = handle;
    g_feed_pushed++;
    if (g_feed_pushed <= 3u) {
        a6400_logf(&g_log, A6400_LOG_INFO,
                   "feed_yc_pushed n=%u handle=0x%04x ref_out=0x%04x yuv_ref_out=0x%04x",
                   g_feed_pushed, handle, ref_out, yuv_ref_out);
    }
}

static int apply_scalar_splice(struct daemon_rt *rt,
                               const struct a6400_daemon_config *cfg,
                               const char *reason)
{
    uint32_t w[8];
    uint32_t down_qid = 0x005a0048u;
    uint32_t i;
    uint8_t pno = (g_splice_pin_no == 0xffu) ? 0u : g_splice_pin_no;
    int rc;

    /* Check which downstream display queue is connected (w[5] == 0 means connected).
     * Prefer 0x005a0048:2 (LiveView Display) when connected; fall back to 0x00b201aa:0. */
    memset(w, 0, sizeof w);
    if (a6400_df_get_pin_info(&rt->uipc, 0x005a0048u, 0u, 1u, 2u, w, 500) == 0 &&
        w[5] == 0u) {
        down_qid = 0x005a0048u;
    } else if (a6400_df_get_pin_info(&rt->uipc, 0x00b201aau, 0u, 1u, 0u, w, 500) == 0 &&
               w[5] == 0u) {
        down_qid = 0x00b201aau;
    }
    g_splice_downstream_qid = down_qid;
    for (i = 0; i < 2u; i++) {
        uint32_t dst_pin_no = (down_qid == 0x00b201aau) ? i : (i == 0u ? 0u : 2u);
        uint32_t src_isp[4]    = { 0u, 2u, i, 0x00050004u };
        uint32_t dst_scalar[4] = { 0u, 1u, i, 0x01670464u };
        uint32_t src_scalar[4] = { 0u, 2u, i, 0x01670464u };
        uint32_t dst_disp[4]   = { 0u, 1u, dst_pin_no, down_qid };
        uint32_t st1 = 0xffffffffu, st2 = 0xffffffffu, st3 = 0xffffffffu;
        int rc1 = a6400_df_pin_disconnect(&rt->uipc, down_qid, 0u, 1u, dst_pin_no, &st1, 1500);
        int rc2 = a6400_df_pin_connect(&rt->uipc, 0x00050004u, src_isp, dst_scalar, &st2, 1500);
        int rc3 = a6400_df_pin_connect(&rt->uipc, 0x01670464u, src_scalar, dst_disp, &st3, 1500);
        a6400_logf(&g_log, A6400_LOG_INFO,
                   "splice_scalar reason=%s pin=%u dst=0x%08x:%u dis=(%d,0x%x) isp2sc=(%d,0x%x) sc2disp=(%d,0x%x)",
                   reason ? reason : "init", i, down_qid, dst_pin_no,
                   rc1, st1, rc2, st2, rc3, st3);
    }
    g_splice_active = 1;
    rc = a6400_scalar_set_mode_0464(&rt->dmm, 0x20u,
                                    (uint32_t)cfg->fps,
                                    (uint32_t)cfg->path_width,
                                    (uint8_t)cfg->comp_level,
                                    pno, 3000);
    a6400_logf(&g_log, A6400_LOG_INFO,
               "scalar_mode_0464 reason=%s mode=0x20 width=%u comp=%u pin=%u rc=%d",
               reason ? reason : "init", cfg->path_width, cfg->comp_level, pno, rc);
    return rc;
}

static int stream_loop(struct daemon_rt *rt, const struct a6400_daemon_config *cfg)
{
    uint64_t period_ns = 1000000000ull / (uint64_t)cfg->fps;
    uint64_t deadline = (uint64_t)a6400_now_ns();
    uint64_t end_ns = 0;
    uint64_t next_report = 0;
    uint64_t last_resplice_ns = deadline;
    uint32_t empty_streak = 0;

    if (cfg->duration_s > 0)
        end_ns = deadline + (uint64_t)cfg->duration_s * 1000000000ull;
    if (g_stats_interval_s)
        next_report = deadline + (uint64_t)g_stats_interval_s * 1000000000ull;

    for (;;) {
        struct timespec ts;

        if (g_stop)
            break;
        if (end_ns && (uint64_t)a6400_now_ns() >= end_ns)
            break;

        if (g_feed_liveview)
            feed_one_liveview_yc(rt);
        {
            int tick_rc = stream_tick(rt, cfg);
            if (tick_rc == 1)
                return 1;
            if (tick_rc == 2) {
                empty_streak++;
                /* Auto-recover after Photo <-> Movie <-> Playback mode switches
                 * that rebuild the DataflowInfra graph and detach our splice. */
                if (g_splice_scalar && empty_streak >= 400u) {
                    uint64_t now = (uint64_t)a6400_now_ns();
                    if (now - last_resplice_ns >= 2000000000ull) {
                        last_resplice_ns = now;
                        empty_streak = 0;
                        (void)apply_scalar_splice(rt, cfg, "auto_resplice");
                    }
                }
            } else {
                empty_streak = 0;
            }
            (void)poll_control(rt, cfg);
            g_loop_ticks++;
            /* E0356: mirror the kernel [a6400_*] trace to the SD card every ~150
             * ticks (about 5 s at 30 fps) so a later hang/power cycle cannot lose
             * the CLASS/START evidence. Bounded; failures are counted. */
            if (g_dmesg_sd && g_sd.mounted && (g_loop_ticks % 150u) == 0u)
                (void)a6400_sd_flush_kernel_trace(&g_sd);

            if (g_stats_interval_s && (uint64_t)a6400_now_ns() >= next_report) {
                log_event("stats",
                          "\"captured\":%u,\"submitted\":%u,\"dropped\":%u,"
                          "\"rejected\":%u,\"transport_errors\":%u,\"polls\":%u,"
                          "\"empty\":%u,\"wait_max_ms\":%u",
                          g_stats.frames_captured, g_stats.frames_submitted,
                          g_stats.frames_dropped, g_stats.frames_rejected,
                          g_stats.transport_errors, g_stats.get_jpeg_polls,
                          g_stats.get_jpeg_empty, g_stats.wait_ms_max);
                if (g_dmesg_sd && g_sd.mounted)
                    (void)a6400_sd_flush_kernel_trace(&g_sd);
                (void)write_receipt();
                next_report += (uint64_t)g_stats_interval_s * 1000000000ull;
            }

            if (g_wifi_stream) {
                /* GET_JPEG is non-blocking: sleeping a full 33 ms on empty causes
                 * 29.97 Hz / 30 Hz aliasing (15 FPS). Poll every 2 ms on empty and
                 * 4 ms after a delivered frame so every hardware JPEG is captured
                 * within <2 ms of arrival. */
                uint64_t now = (uint64_t)a6400_now_ns();
                deadline = now + (tick_rc == 2 ? 1000000ull : 2000000ull);
            } else {
                /* Absolute CLOCK_MONOTONIC deadlines only; never a relative sleep. */
                deadline += period_ns;
                {
                    uint64_t now = (uint64_t)a6400_now_ns();

                    if (now >= deadline) {
                        uint64_t missed = 1u + (now - deadline) / period_ns;
                        uint32_t add = missed > 0xffffffffu ? 0xffffffffu : (uint32_t)missed;

                        g_deadline_misses += add;
                        deadline = now; /* re-base instead of bursting to catch up */
                    }
                }
            }
        }
        ts.tv_sec = (time_t)(deadline / 1000000000ull);
        ts.tv_nsec = (long)(deadline % 1000000000ull);
        while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL) == EINTR) {
            if (g_stop)
                break;
        }
    }
    return 0;
}

/* ---- control host hooks -------------------------------------------------- */

static void host_get_stats(void *ctx, struct a6400_daemon_stats *out)
{
    (void)ctx;
    if (out)
        *out = g_stats;
}

static void host_get_caps(void *ctx, struct a6400_ctl_caps_reply *out)
{
    (void)ctx;
    if (!out)
        return;
    out->protocol_version = A6400_CTL_VERSION;
    /* Absolute writes are not effective on this camera, so settings are routed
     * to the host's PTP session (CONTROL_IPC.md:16-20). */
    out->camera_setting_route = (uint32_t)A6400_CTL_ROUTE_PTP;
    out->uvc_transport = (uint32_t)(g_rc_session_active ? 1u : 0u);
    out->max_payload = A6400_CTL_MAX_PAYLOAD;
}

static int host_session_active(void *ctx)
{
    (void)ctx;
    return g_rc_session_active;
}

/* ---- config / argv ------------------------------------------------------- */

static int path_under_tmp_bt(const char *path)
{
    return path && strncmp(path, "/tmp_bt/", 8u) == 0;
}

static int validate_config(const struct a6400_daemon_config *cfg)
{
    if (!cfg->device_path || !cfg->log_path || !cfg->receipt_path)
        return -1;
    if (!path_under_tmp_bt(cfg->device_path))
        return -2; /* the video node lives in /tmp_bt only */
    if (cfg->control_fifo && cfg->control_fifo[0] &&
        !path_under_tmp_bt(cfg->control_fifo))
        return -3;
    if (cfg->control_ack && cfg->control_ack[0] &&
        !path_under_tmp_bt(cfg->control_ack))
        return -4;
    if (strlen(cfg->device_path) >= A6400_PATH_MAX ||
        strlen(cfg->receipt_path) >= A6400_PATH_MAX)
        return -5;
    if (cfg->fps < 1 || cfg->fps > 240)
        return -6;
    if (cfg->comp_level < 1 || cfg->comp_level > 100)
        return -7;
    if (cfg->path_width < 1 || cfg->path_width > 4096)
        return -8;
    if (cfg->target_pid <= 0)
        return -9;
    if (cfg->wait_timeout_s < 0 || cfg->wait_timeout_s > 3600)
        return -10;
    if (cfg->duration_s < 0 || cfg->duration_s > 86400)
        return -11;
    if ((cfg->expected_width == 0) != (cfg->expected_height == 0))
        return -12; /* both zero (discover) or both set */
    if (cfg->expected_width < 0 || cfg->expected_height < 0 ||
        cfg->expected_width > 8192 || cfg->expected_height > 8192)
        return -13;
    if (g_wait_bound_ms < 1u || g_wait_bound_ms > 60000u)
        return -15;
    if (g_stats_interval_s > 3600u)
        return -16;
    if (cfg->dmesg_sd && (!cfg->sd_log || !cfg->sd_mount))
        return -20; /* --dmesg-sd requires --sd-log --sd-mount */
    if (cfg->sd_mount && !cfg->sd_log)
        return -17; /* mounting without the SD sink is never useful */
    if (cfg->sd_log) {
        const char *dev = cfg->sd_dev ? cfg->sd_dev : A6400_SD_DEV_DEFAULT;
        const char *mp = cfg->sd_mountpoint ? cfg->sd_mountpoint
                                            : A6400_SD_MOUNT_DEFAULT;

        if (a6400_sd_device_policy(dev) != A6400_SD_OK)
            return -18; /* internal flash / wrong node / unsafe path */
        if (a6400_sd_mountpoint_policy(mp) != A6400_SD_OK)
            return -19; /* not under /tmp_bt, or a forbidden mount */
    }
    if (!cfg->dry_run && !g_arm_transport)
        return -14; /* conservative default: refuse destructive work */
    /* A DMA window is not required here: MOD_QUERY_DMA may supply it. If it is
     * absent the daemon refuses with an explicit message at that point instead
     * of guessing a physical address. */
    return 0;
}

static void usage(const char *argv0)
{
    fprintf(stderr,
        "usage: %s [options]\n"
        "\n"
        "Discovery / safety:\n"
        "  --device PATH         video node (default /tmp_bt/video)\n"
        "  --log PATH            JSONL log (default /tmp_bt/a6400_lived.jsonl, /tmp_bt only)\n"
        "  --receipt PATH        receipt JSON (default /tmp_bt/a6400_lived_receipt.json)\n"
        "  --control-fifo PATH   request FIFO (default /tmp_bt/a6400_ctrl.fifo)\n"
        "  --control-ack PATH    ack file (default /tmp_bt/a6400_ctrl.ack)\n"
        "  --dry-run             discovery/validation only; no destructive step\n"
        "  --arm-transport       REQUIRED for freeze/INIT/REG_BUF/ENABLE/UPDATE/SET_PATH\n"
        "  --wifi-stream         stream 720p30 MJPEG + controls over TCP/HTTP (no USB UVC)\n"
        "  --wifi-stream-port N  TCP binary frame port (default 9640, 0=off)\n"
        "  --wifi-http-port N    HTTP MJPEG port (default 8080, 0=off)\n"
        "  --wifi-ctrl-port N    TCP control port (default 9641, 0=off)\n"
        "  --verbose             debug logging\n"
        "\n"
        "Session:\n"
        "  --wait-pcremote       require 0x054c:0x0caa before INIT\n"
        "  --timeout SEC         PC Remote wait bound (default 120)\n"
        "  --duration SEC        stop after SEC, 0 = until SIGINT (default 0)\n"
        "  --pid N               target process (default 149)\n"
        "  --fps N               pacing hint (default 30)\n"
        "  --comp-level N        SET_PATH component level (default 5)\n"
        "  --path-width N        SET_PATH width hint (default 1280)\n"
        "  --width N             expected frame width, 0 = discover (default 0)\n"
        "  --height N            expected frame height, 0 = discover (default 0)\n"
        "\n"
        "Transport fallback and diagnostics:\n"
        "  --dma-phys HEX        window physical address when MOD_QUERY_DMA is absent\n"
        "  --dma-size N          window size when MOD_QUERY_DMA is absent\n"
        "  --read-prefix         mmap the DMM window for the prefix instead of pread\n"
        "  --wait-ms N           bound on the WAIT ioctl (default 1000)\n"
        "  --stats-interval SEC  log stats and rewrite the receipt every SEC (0 = off)\n"
        "\n"
        "Crash-safe SD logging (E0350; both flags default OFF):\n"
        "  --sd-log              mirror every JSONL line + the receipt to the SD card\n"
        "  --dmesg-sd            mirror kernel [a6400_*] trace to SD every ~5 s\n"
        "                        (requires --sd-log --sd-mount; O_SYNC A6400K.LOG)\n"
        "  --sd-mount            explicit consent to mount the SD (requires --sd-log;\n"
        "                        only after PC Remote 054c:0caa, before INIT, never in MSC)\n"
        "  --sd-dev PATH         block device (default /dev/mmca1; only that node is accepted)\n"
        "  --sd-mountpoint PATH  mount point (default /tmp_bt/sd; must be under /tmp_bt/)\n"
        "\n"
        "Degraded teardown (E0358):\n"
        "  --no-park-recovery    disable the bounded recovery; a failed stop\n"
        "                        attestation parks immediately (default: write the SD\n"
        "                        evidence, retry STOP once, retire/resume, then park\n"
        "                        only if a recovery step fails)\n"
        "  --help\n",
        argv0);
}

static int parse_argv(int argc, char **argv, struct a6400_daemon_config *cfg)
{
    int i;

    for (i = 1; i < argc; i++) {
        const char *a = argv[i];

        if (!strcmp(a, "--help") || !strcmp(a, "-h")) {
            usage(argv[0]);
            return 1;
        } else if (!strcmp(a, "--verbose")) {
            cfg->verbose = 1;
        } else if (!strcmp(a, "--dry-run")) {
            cfg->dry_run = 1;
        } else if (!strcmp(a, "--arm-transport")) {
            g_arm_transport = 1;
        } else if (!strcmp(a, "--wifi-stream")) {
            g_wifi_stream = 1;
        } else if (!strcmp(a, "--use-liveview-cmd")) {
            g_use_liveview_cmd = 1;
        } else if (!strcmp(a, "--probe-pins")) {
            g_probe_pins = 1;
        } else if (!strcmp(a, "--splice-scalar")) {
            g_splice_scalar = 1;
            g_read_prefix = 1;
            g_splice_pin_no = 1u;
        } else if (!strcmp(a, "--pin-no") && i + 1 < argc) {
            int pv = atoi(argv[++i]);
            g_splice_pin_no = (pv == 0) ? 0xffu : (uint8_t)pv;
        } else if (!strcmp(a, "--probe-yuv")) {
            g_probe_yuv = 1;
        } else if (!strcmp(a, "--yuv-stream") && i + 1 < argc) {
            g_yuv_stream_type = (uint32_t)strtoul(argv[++i], NULL, 0);
            g_wifi_stream = 1;
        } else if (!strcmp(a, "--feed-liveview")) {
            g_feed_liveview = 1;
        } else if (!strcmp(a, "--feed-liveview-max") && i + 1 < argc) {
            g_feed_liveview = 1;
            g_feed_liveview_max = (uint32_t)strtoul(argv[++i], NULL, 0);
        } else if (!strcmp(a, "--connect-pin") && i + 1 < argc) {
            const char *spec = argv[++i];
            const char *c1 = strchr(spec, ':');
            const char *c2 = c1 ? strchr(c1 + 1, ':') : NULL;
            g_connect_pin_qid = (uint32_t)strtoul(spec, NULL, 0);
            g_connect_pin_no = c1 ? (uint32_t)strtoul(c1 + 1, NULL, 0) : 0u;
            g_connect_dst_pin = c2 ? (uint32_t)strtoul(c2 + 1, NULL, 0) : 0u;
        } else if (!strcmp(a, "--wifi-stream-port") && i + 1 < argc) {
            g_wifi_stream_port = (uint16_t)atoi(argv[++i]);
        } else if (!strcmp(a, "--wifi-http-port") && i + 1 < argc) {
            g_wifi_http_port = (uint16_t)atoi(argv[++i]);
        } else if (!strcmp(a, "--wifi-ctrl-port") && i + 1 < argc) {
            g_wifi_ctrl_port = (uint16_t)atoi(argv[++i]);
        } else if (!strcmp(a, "--wait-pcremote")) {
            cfg->wait_pcremote = 1;
        } else if (!strcmp(a, "--read-prefix")) {
            g_read_prefix = 1;
        } else if (!strcmp(a, "--device") && i + 1 < argc) {
            cfg->device_path = argv[++i];
        } else if (!strcmp(a, "--log") && i + 1 < argc) {
            cfg->log_path = argv[++i];
        } else if (!strcmp(a, "--receipt") && i + 1 < argc) {
            cfg->receipt_path = argv[++i];
        } else if (!strcmp(a, "--control-fifo") && i + 1 < argc) {
            cfg->control_fifo = argv[++i];
        } else if (!strcmp(a, "--control-ack") && i + 1 < argc) {
            cfg->control_ack = argv[++i];
        } else if (!strcmp(a, "--timeout") && i + 1 < argc) {
            cfg->wait_timeout_s = atoi(argv[++i]);
        } else if (!strcmp(a, "--duration") && i + 1 < argc) {
            cfg->duration_s = atoi(argv[++i]);
        } else if (!strcmp(a, "--pid") && i + 1 < argc) {
            cfg->target_pid = atoi(argv[++i]);
        } else if (!strcmp(a, "--fps") && i + 1 < argc) {
            cfg->fps = atoi(argv[++i]);
        } else if (!strcmp(a, "--comp-level") && i + 1 < argc) {
            cfg->comp_level = atoi(argv[++i]);
        } else if (!strcmp(a, "--path-width") && i + 1 < argc) {
            cfg->path_width = atoi(argv[++i]);
        } else if (!strcmp(a, "--width") && i + 1 < argc) {
            cfg->expected_width = atoi(argv[++i]);
        } else if (!strcmp(a, "--height") && i + 1 < argc) {
            cfg->expected_height = atoi(argv[++i]);
        } else if (!strcmp(a, "--dma-phys") && i + 1 < argc) {
            g_dma_phys_opt = (uint32_t)strtoul(argv[++i], NULL, 0);
        } else if (!strcmp(a, "--dma-size") && i + 1 < argc) {
            g_dma_size_opt = (uint32_t)strtoul(argv[++i], NULL, 0);
        } else if (!strcmp(a, "--stats-interval") && i + 1 < argc) {
            g_stats_interval_s = (uint32_t)strtoul(argv[++i], NULL, 0);
        } else if (!strcmp(a, "--wait-ms") && i + 1 < argc) {
            g_wait_bound_ms = (uint32_t)strtoul(argv[++i], NULL, 0);
        } else if (!strcmp(a, "--sd-log")) {
            cfg->sd_log = 1;
        } else if (!strcmp(a, "--dmesg-sd")) {
            cfg->dmesg_sd = 1;
            g_dmesg_sd = 1;
        } else if (!strcmp(a, "--no-park-recovery")) {
            g_park_recovery = 0;
        } else if (!strcmp(a, "--sd-mount")) {
            cfg->sd_mount = 1;
        } else if (!strcmp(a, "--sd-dev") && i + 1 < argc) {
            cfg->sd_dev = argv[++i];
        } else if (!strcmp(a, "--sd-mountpoint") && i + 1 < argc) {
            cfg->sd_mountpoint = argv[++i];
        } else {
            fprintf(stderr, "unknown or incomplete option: %s\n", a);
            usage(argv[0]);
            return -1;
        }
    }
    return 0;
}

/* ---- teardown ------------------------------------------------------------ */

/* Destructive steps are logged before they happen. */
static void log_destructive(const char *what)
{
    log_event("destructive_step", "\"what\":\"%s\"", what);
}

/* Final SD shutdown for EVERY teardown path (E0350):
 *   1. while the filesystem is still mounted, rewrite the receipt so the
 *      durable copy exists before the process can be killed;
 *   2. close the SD log sink;
 *   3. umount2(), tolerating failure with an explicit status. A failed unmount
 *      leaves mounted=1, so no receipt ever claims a clean unmount it did not
 *      observe.
 * Idempotent: a second call after an attempted unmount does nothing. */
static void sd_teardown(void)
{
    if (!g_sd.enabled || g_sd.unmount_attempted)
        return;
    if (g_sd.mounted) {
        (void)write_receipt();
        log_event("sd_log_teardown", "\"mounted\":1,\"receipt_written\":%d",
                  g_sd.receipt_written);
    }
    a6400_log_sd_close(&g_log);
    if (g_sd.mounted) {
        if (g_dmesg_sd && g_sd.mounted)
        (void)a6400_sd_flush_kernel_trace(&g_sd);
    (void)a6400_sd_unmount(&g_sd);
        log_event("sd_log_unmount",
                  "\"rc\":%d,\"still_mounted\":%d,\"errors\":%u",
                  g_sd.unmount_rc, g_sd.mounted, g_sd.errors);
    }
}

/* ---- bounded park recovery (E0358) --------------------------------------- */

/* (a) Durable evidence, ALWAYS before any recovery step. The kernel trace is
 * the only record of which teardown gate refused, and the SD copy of
 * A6400RCPT.JSON is what a battery pull cannot destroy (E0350/E0357). */
static void recovery_evidence(void)
{
    if (g_sd.enabled && g_sd.mounted) {
        int rc = a6400_sd_flush_kernel_trace(&g_sd);

        log_event("recovery_sd_trace",
                  "\"rc\":%d,\"dmesg_sd\":%d,\"trace_lines\":%u,\"trace_errors\":%u",
                  rc, g_dmesg_sd, g_sd.trace_lines, g_sd.trace_errors);
    } else {
        log_event("recovery_sd_trace", "\"mounted\":0,\"durable\":0");
    }
    g_recovery.evidence_written = write_receipt() == 0 ? 1 : 0;
    if (!g_recovery.evidence_written)
        a6400_logf(&g_log, A6400_LOG_ERROR,
                   "recovery_evidence_failed reason=receipt_write_failed");
}

/* (c) Freeze -> HOOK_RETIRE -> resume. The attestation set is resumed on every
 * path, including a failed retire, so the firmware is never left frozen (the
 * publish path follows the same rule). */
static int recovery_retire_hook(struct daemon_rt *rt)
{
    int rc;

    log_destructive("ptrace_freeze_for_recovery_retire");
    rc = a6400_runner_freeze(&rt->runner);
    g_recovery.freeze_rc = rc;
    g_recovery.frozen_tids = rt->runner.tid_count;
    g_rc_tid_count = rt->runner.tid_count;
    g_rc_freeze_ms = rt->runner.last_freeze_ms;
    a6400_logf(&g_log, A6400_LOG_INFO, "recovery_freeze rc=%d tids=%u",
               rc, rt->runner.tid_count);
    if (rc != 0)
        return rc; /* the freeze failure path already resumed a partial attach */

    log_destructive("hook_retire_recovery");
    rc = a6400_runner_retire_hook(&rt->runner);
    g_recovery.retire_rc = rc;
    g_rc_attest_completed = rt->runner.attest_completed;
    g_rc_attest_error = rt->runner.attest_error;
    a6400_logf(&g_log, A6400_LOG_INFO,
               "recovery_hook_retire rc=%d completed=%u error=%d",
               rc, rt->runner.attest_completed, rt->runner.attest_error);
    if (rc == 0) {
        g_recovery.hook_retired = 1;
        g_rc_hook_retired = 1;
        g_rc_hook_published = 0;
        rt->hook_retired = 1;
    }

    log_destructive("ptrace_resume");
    g_recovery.resume_rc = a6400_runner_resume(&rt->runner);
    a6400_logf(&g_log, A6400_LOG_INFO, "recovery_resume rc=%d",
               g_recovery.resume_rc);
    return rc;
}

/* Closes every descriptor the daemon owns. Used by the recovered path so the
 * camera re-enumerates from a released owner, exactly like a clean teardown. */
static void recovery_close(struct daemon_rt *rt)
{
    if (rt->ctl) {
        a6400_control_destroy(rt->ctl);
        rt->ctl = NULL;
    }
    if (rt->mem_fd >= 0) {
        (void)close(rt->mem_fd);
        rt->mem_fd = -1;
    }
    if (rt->uipc_open) {
        if (g_splice_active && g_splice_downstream_qid) {
            uint32_t i;
            int mrc = a6400_scalar_set_mode_0464(&rt->dmm, 0x01u, 30u, 1024u, 5u, 1u, 1500);
            a6400_logf(&g_log, A6400_LOG_INFO, "scalar_mode_0464_stop rc=%d", mrc);
            for (i = 0; i < 2u; i++) {
                uint32_t dst_pin_no = (g_splice_downstream_qid == 0x00b201aau) ? i : (i == 0u ? 0u : 2u);
                uint32_t src_isp[4] = { 0u, 2u, i, 0x00050004u };
                uint32_t dst_disp[4] = { 0u, 1u, dst_pin_no, g_splice_downstream_qid };
                uint32_t st1 = 0, st2 = 0, st3 = 0;
                (void)a6400_df_pin_disconnect(&rt->uipc, g_splice_downstream_qid, 0u, 1u, dst_pin_no, &st1, 1500);
                (void)a6400_df_pin_disconnect(&rt->uipc, 0x01670464u, 0u, 1u, i, &st2, 1500);
                (void)a6400_df_pin_connect(&rt->uipc, 0x00050004u, src_isp, dst_disp, &st3, 1500);
                a6400_logf(&g_log, A6400_LOG_INFO,
                           "splice_restore pin=%u dst=0x%08x:%u st1=0x%x st2=0x%x st3=0x%x",
                           i, g_splice_downstream_qid, dst_pin_no, st1, st2, st3);
            }
            g_splice_active = 0;
        }
        a6400_uipc_close(&rt->uipc);
        rt->uipc_open = 0;
    }
    if (rt->uvc_open) {
        a6400_uvc_close(&rt->uvc);
        rt->uvc_open = 0;
    }
    g_recovery.closed = 1;
}

static int recovery_fail(int step, const char *detail)
{
    g_recovery.failed_step = step;
    g_recovery.status = A6400_RECOVERY_FAILED;
    if (detail)
        snprintf(g_recovery.detail, sizeof g_recovery.detail, "%s", detail);
    a6400_logf(&g_log, A6400_LOG_ERROR, "recovery_step_failed step=%s detail=%s",
               a6400_recovery_step_name(step), detail ? detail : "");
    return 0;
}

/* Runs the bounded recovery after a failed STOP attestation (E0358).
 * Returns 1 when every gate passed (the caller finishes with degraded_teardown
 * and the descriptors have already been closed), 0 when a step errored (the
 * caller parks as the last resort; the durable evidence was written first). */
static int recovery_attempt(struct daemon_rt *rt)
{
    int stop_rc = 0, state_valid = 0, verified, i;

    memset(&g_recovery, 0, sizeof g_recovery);
    g_recovery.attempted = 1;
    g_recovery.status = A6400_RECOVERY_PENDING;
    g_recovery.failed_step = A6400_REC_NONE;
    for (i = 0; i < 4; i++) {
        g_recovery.state_before[i] = -1;
        g_recovery.state_after[i] = -1;
    }
    log_event("recovery_begin", "\"reason\":\"stop_attestation_failed\"");

    /* (a) Evidence first: never skipped, even when every later step fails. */
    recovery_evidence();
    if (!g_recovery.evidence_written)
        return recovery_fail(A6400_REC_EVIDENCE, "receipt_write_failed");

    /* Step 2: read-only status. This is the module-supplied diagnostic the old
     * park path never had: state=-1,-1,-1,-1 only ever meant "STOP failed
     * before the query"; this call reports {phase,pinned,hooks,error}. */
    g_recovery.state_before_valid =
        a6400_runner_query_session_state(rt->uvc.fd, g_recovery.state_before);
    log_event("recovery_state",
              "\"source\":\"after_failed_stop\",\"valid\":%d,\"phase\":%d,"
              "\"pin\":%d,\"hooks\":%d,\"error\":%d",
              g_recovery.state_before_valid, g_recovery.state_before[0],
              g_recovery.state_before[1], g_recovery.state_before[2],
              g_recovery.state_before[3]);

    /* Step 3: one bounded STOP retry. The module's STOP runs the ordered
     * 9-phase unconfigure-first teardown (cleanup -> session_end ->
     * a6400_uvc_soft_disconnect); HOOK_RETIRE does not run it and the module
     * refuses HOOK_RETIRE while the session is held. */
    if (a6400_recovery_retry_allowed(g_recovery.state_before_valid,
                                     g_recovery.state_before)) {
        log_destructive("uvc_stop_retry");
        g_rc_stop_attempts = 2u;
        verified = a6400_runner_retry_stop(rt->uvc.fd, g_recovery.state_after,
                                           &stop_rc, &state_valid);
        g_recovery.stop_retry_rc = stop_rc;
        g_recovery.state_after_valid = state_valid;
        g_recovery.stop_retry_verified = verified;
        log_event("recovery_stop_retry",
                  "\"ioctl_rc\":%d,\"state_valid\":%d,\"phase\":%d,\"pin\":%d,"
                  "\"hooks\":%d,\"error\":%d,\"verified\":%d",
                  stop_rc, state_valid, g_recovery.state_after[0],
                  g_recovery.state_after[1], g_recovery.state_after[2],
                  g_recovery.state_after[3], verified);
        if (stop_rc != 0)
            return recovery_fail(A6400_REC_STOP_RETRY,
                                 "stop_retry_ioctl_failed");
        if (!verified)
            return recovery_fail(A6400_REC_STOP_RETRY,
                                 "state_not_zero_after_retry");
    } else {
        /* The read-only state already proved the session idle: no second
         * destructive STOP is issued. */
        for (i = 0; i < 4; i++)
            g_recovery.state_after[i] = g_recovery.state_before[i];
        g_recovery.state_after_valid = g_recovery.state_before_valid;
        g_recovery.stop_retry_verified = 1;
        log_event("recovery_stop_retry", "\"skipped\":\"state_already_zero\"");
    }

    /* Step 4: retire the entry hook under a verified ptrace stop scope and
     * always resume the attestation set (the 181 TIDs of E0357 are this set). */
    if (recovery_retire_hook(rt) != 0) {
        if (g_recovery.freeze_rc != 0)
            return recovery_fail(A6400_REC_FREEZE, "freeze_failed");
        return recovery_fail(A6400_REC_HOOK_RETIRE, "hook_retire_failed");
    }
    if (g_recovery.resume_rc != 0)
        return recovery_fail(A6400_REC_RESUME, "resume_failed");

    /* Step 5: close, then let the pure classifier decide. */
    log_destructive("recovery_close");
    recovery_close(rt);
    g_recovery.status = a6400_recovery_outcome(&g_recovery);
    if (g_recovery.status != A6400_RECOVERY_RECOVERED) {
        g_recovery.failed_step = A6400_REC_CLOSE;
        snprintf(g_recovery.detail, sizeof g_recovery.detail, "%s",
                 "close_incomplete");
        return 0;
    }
    return 1;
}

static void teardown(struct daemon_rt *rt, const struct a6400_daemon_config *cfg,
                     int *code)
{
    int32_t state[4];
    int rc;

    g_stage = "teardown";
    log_event("teardown_begin", NULL);

    /* 1. The stream is already stopped: the loop has returned. */
    if (rt->uipc_open) {
        struct a6400_scalar_path_cfg stop_cfg;

        log_destructive("set_path_stop");
        memset(&stop_cfg, 0, sizeof stop_cfg);
        stop_cfg.timeout_ms = 1500;
        rc = a6400_scalar_set_path(&rt->dmm, &stop_cfg);
        a6400_logf(&g_log, A6400_LOG_INFO, "set_path_stop status=%d", rc);
        if (g_connect_pin_qid) {
            uint32_t st1 = 0, st2 = 0;
            (void)a6400_df_pin_disconnect(&rt->uipc, g_connect_pin_qid, 0u, 2u, g_connect_pin_no, &st1, 1000);
            (void)a6400_df_pin_disconnect(&rt->uipc, 0x01670464u, 0u, 1u, g_connect_dst_pin, &st2, 1000);
            a6400_logf(&g_log, A6400_LOG_INFO, "pin_disconnect q=0x%08x st1=0x%x st2=0x%x",
                       g_connect_pin_qid, st1, st2);
        }

        /* 2. Restore the default DMM allocation rule. */
        log_destructive("dmm_rule_0");
        rc = a6400_dmm_set_rule(&rt->dmm, 0, 0);
        a6400_logf(&g_log, A6400_LOG_INFO, "dmm_rule_0 status=%d", rc);
    }

    if (rt->reg_buf_ok) {
        log_destructive("uvc_unreg");
        rc = a6400_uvc_unreg(&rt->uvc);
        a6400_logf(&g_log, A6400_LOG_INFO, "uvc_unreg rc=%d", rc);
    }

    if (rt->wifi_open) {
        a6400_wifi_server_close(&rt->wifi);
        rt->wifi_open = 0;
        a6400_logf(&g_log, A6400_LOG_INFO, "wifi_server_closed");
    }

    /* 3. STOP plus the all-zero session-state attestation. This is the gate
     *    that permits hook retirement (uvc_ptp_runner_stop.h:7-19). A failure
     *    first runs the bounded E0358 recovery; only a recovery error parks. */
    if (rt->init_attempted) {
        log_destructive("uvc_stop");
        g_rc_stop_attempts = 1u;
        g_rc_stop_verified = a6400_runner_stop_verified(rt->uvc.fd, state);
        g_rc_session_active = 0;
        g_stats.session_active = 0;
        a6400_logf(&g_log, A6400_LOG_INFO,
                   "uvc_stop verified=%d state=%d,%d,%d,%d",
                   g_rc_stop_verified, state[0], state[1], state[2], state[3]);
        if (!g_rc_stop_verified) {
            int recovered = 0;

            /* `state` is the daemon's -1 sentinel here: STOP failed before the
             * status query, so no module state was read (E0358 analysis). */
            snprintf(g_fail_reason, sizeof g_fail_reason,
                     "stop attestation failed state=%d,%d,%d,%d",
                     state[0], state[1], state[2], state[3]);
            if (g_park_recovery) {
                /* (a)-(c): evidence first, then one bounded STOP retry that
                 * re-enters the 9-phase teardown, then retire/resume/close. */
                g_stage = "recovery";
                g_exit_code = A6400_EXIT_DEGRADED;
                recovered = recovery_attempt(rt);
            } else {
                a6400_logf(&g_log, A6400_LOG_WARN,
                           "recovery_disabled flag=--no-park-recovery");
            }
            if (recovered) {
                g_rc_teardown_verified = 1;
                g_exit_code = A6400_EXIT_DEGRADED;
                *code = A6400_EXIT_DEGRADED;
                g_stage = "teardown_degraded";
                snprintf(g_fail_reason, sizeof g_fail_reason,
                         "stop attestation failed state=%d,%d,%d,%d; "
                         "recovered by one bounded STOP retry",
                         state[0], state[1], state[2], state[3]);
                log_event("recovery_complete",
                          "\"status\":\"recovered\",\"stop_attempts\":%u",
                          g_rc_stop_attempts);
                (void)write_receipt();
                sd_teardown();
                return;
            }
            /* Fail closed: park remains the last resort. The durable evidence
             * was already written by the evidence step; this receipt records
             * which recovery step failed so a future run is diagnosable. */
            g_stage = "parked";
            g_exit_code = A6400_EXIT_FATAL;
            *code = A6400_EXIT_FATAL;
            if (g_recovery.attempted) {
                snprintf(g_fail_reason, sizeof g_fail_reason,
                         "stop attestation failed state=%d,%d,%d,%d; recovery "
                         "failed step=%s rc=%d detail=%s",
                         state[0], state[1], state[2], state[3],
                         a6400_recovery_step_name(g_recovery.failed_step),
                         g_recovery.stop_retry_rc,
                         g_recovery.detail[0] ? g_recovery.detail : "none");
            }
            a6400_logf(&g_log, A6400_LOG_ERROR,
                       "parked reason=stop_attestation_failed recovery=%s "
                       "failed_step=%s",
                       a6400_recovery_status_name(g_recovery.status),
                       a6400_recovery_step_name(g_recovery.failed_step));
            /* Defensive, before any slow SD work: never park while this process
             * holds a ptrace stop scope. The recovery path always resumes; this
             * catches a state the logic above believes impossible. */
            if (rt->runner.frozen) {
                int resume_rc = a6400_runner_resume(&rt->runner);

                a6400_logf(&g_log, A6400_LOG_WARN,
                           "park_resume_guard rc=%d", resume_rc);
            }
            (void)write_receipt();
            /* Make the durable receipt and unmount BEFORE parking: a parked
             * process can sit indefinitely and the camera may be battery-pulled
             * at any moment (E0350). */
            sd_teardown();
            /* Park: keep the descriptor and the hook state exactly as they are.
             * A signal is the operator's explicit decision to leave. */
            if (a6400_runner_park(&g_stop)) {
                a6400_logf(&g_log, A6400_LOG_WARN,
                           "park_released_by_signal; descriptor left open");
            }
            (void)write_receipt();
            return;
        }
    }

    if (g_rc_hook_published) {
        /* 4. Freeze, retire the entry hook, resume, then close. */
        log_destructive("ptrace_freeze_for_retire");
        rc = a6400_runner_freeze(&rt->runner);
        a6400_logf(&g_log, A6400_LOG_INFO, "retire_freeze rc=%d tids=%u",
                   rc, rt->runner.tid_count);
        g_rc_tid_count = rt->runner.tid_count;
        g_rc_freeze_ms = rt->runner.last_freeze_ms;
        if (rc != 0) {
            g_exit_code = A6400_EXIT_FATAL;
            *code = A6400_EXIT_FATAL;
            g_stage = "teardown";
            snprintf(g_fail_reason, sizeof g_fail_reason,
                     "freeze before retire failed rc=%d", rc);
            a6400_logf(&g_log, A6400_LOG_ERROR,
                       "retire_aborted reason=freeze_failed rc=%d", rc);
            (void)write_receipt();
            sd_teardown();
            return;
        }

        log_destructive("hook_retire");
        rc = a6400_runner_retire_hook(&rt->runner);
        g_rc_attest_completed = rt->runner.attest_completed;
        g_rc_attest_error = rt->runner.attest_error;
        a6400_logf(&g_log, A6400_LOG_INFO,
                   "hook_retire rc=%d completed=%u error=%d",
                   rc, rt->runner.attest_completed, rt->runner.attest_error);
        if (rc == 0) {
            g_rc_hook_retired = 1;
            g_rc_hook_published = 0;
        } else {
            g_exit_code = A6400_EXIT_FATAL;
            *code = A6400_EXIT_FATAL;
            g_stage = "teardown";
            snprintf(g_fail_reason, sizeof g_fail_reason,
                     "hook retire failed rc=%d", rc);
        }

        log_destructive("ptrace_resume");
        rc = a6400_runner_resume(&rt->runner);
        a6400_logf(&g_log, A6400_LOG_INFO, "retire_resume rc=%d", rc);
        if (rc != 0) {
            g_exit_code = A6400_EXIT_FATAL;
            *code = A6400_EXIT_FATAL;
            g_stage = "teardown";
            snprintf(g_fail_reason, sizeof g_fail_reason,
                     "resume after retire failed rc=%d", rc);
        }
        rt->hook_retired = g_rc_hook_retired;
        /* The clean path is verified only when the STOP gate AND the retire
         * both succeeded; the recovered (degraded) path sets the same field. */
        g_rc_teardown_verified = g_rc_hook_retired ? 1 : 0;
    }

    /* 5. Close everything. The node is not unlinked (see a6400_uvc_close). */
    if (rt->ctl) {
        a6400_control_destroy(rt->ctl);
        rt->ctl = NULL;
    }
    if (rt->mem_fd >= 0) {
        (void)close(rt->mem_fd);
        rt->mem_fd = -1;
    }
    if (rt->uipc_open) {
        if (g_splice_active && g_splice_downstream_qid) {
            uint32_t i;
            int mrc = a6400_scalar_set_mode_0464(&rt->dmm, 0x01u, 30u, 1024u, 5u, 1u, 1500);
            a6400_logf(&g_log, A6400_LOG_INFO, "scalar_mode_0464_stop rc=%d", mrc);
            for (i = 0; i < 2u; i++) {
                uint32_t dst_pin_no = (g_splice_downstream_qid == 0x00b201aau) ? i : (i == 0u ? 0u : 2u);
                uint32_t src_isp[4] = { 0u, 2u, i, 0x00050004u };
                uint32_t dst_disp[4] = { 0u, 1u, dst_pin_no, g_splice_downstream_qid };
                uint32_t st1 = 0, st2 = 0, st3 = 0;
                (void)a6400_df_pin_disconnect(&rt->uipc, g_splice_downstream_qid, 0u, 1u, dst_pin_no, &st1, 1500);
                (void)a6400_df_pin_disconnect(&rt->uipc, 0x01670464u, 0u, 1u, i, &st2, 1500);
                (void)a6400_df_pin_connect(&rt->uipc, 0x00050004u, src_isp, dst_disp, &st3, 1500);
                a6400_logf(&g_log, A6400_LOG_INFO,
                           "splice_restore pin=%u dst=0x%08x:%u st1=0x%x st2=0x%x st3=0x%x",
                           i, g_splice_downstream_qid, dst_pin_no, st1, st2, st3);
            }
            g_splice_active = 0;
        }
        a6400_uipc_close(&rt->uipc);
        rt->uipc_open = 0;
    }
    if (rt->uvc_open) {
        a6400_uvc_close(&rt->uvc);
        rt->uvc_open = 0;
    }
    (void)cfg;
    g_exit_code = *code;
    log_event("teardown_complete", "\"exit_code\":%d", *code);
    /* Durable receipt + unmount in the normal teardown path (E0350). */
    sd_teardown();
}

/* ---- run ----------------------------------------------------------------- */

static int open_control(struct daemon_rt *rt, const struct a6400_daemon_config *cfg)
{
    struct a6400_control_host host;
    struct a6400_control_config ccfg;

    if (!cfg->control_fifo || !cfg->control_fifo[0])
        return 0;

    /* Create the FIFO if absent. mkfifo is idempotent for our purposes: an
     * existing node (EEXIST) is reused, any other error is reported by the
     * control layer's open result and surfaced through poll(). */
    if (mkfifo(cfg->control_fifo, 0600) != 0 && errno != EEXIST) {
        a6400_logf(&g_log, A6400_LOG_WARN, "control_fifo_create_failed path=%s errno=%d",
                   cfg->control_fifo, errno);
    }

    memset(&host, 0, sizeof host);
    host.get_stats = host_get_stats;
    host.get_caps = host_get_caps;
    host.session_active = host_session_active;

    memset(&ccfg, 0, sizeof ccfg);
    ccfg.fifo_path = cfg->control_fifo;
    ccfg.ack_path = cfg->control_ack;
    ccfg.video_node = cfg->device_path;
    ccfg.enable_ring = 0; /* no decoded ring ioctl; a second open is forbidden */
    ccfg.verbose = cfg->verbose;

    rt->ctl = a6400_control_create(&ccfg, &host);
    if (!rt->ctl) {
        a6400_logf(&g_log, A6400_LOG_ERROR, "control_create_failed");
        return -1;
    }
    a6400_logf(&g_log, A6400_LOG_INFO, "control_ready fifo=%s ack=%s",
               cfg->control_fifo,
               (cfg->control_ack && cfg->control_ack[0]) ? cfg->control_ack : "(none)");
    return 0;
}

static int wait_for_pcremote(struct daemon_rt *rt,
                             const struct a6400_daemon_config *cfg)
{
    int waited = 0;
    uint16_t ids[2] = {0, 0};
    int rc = 0;

    while (waited < cfg->wait_timeout_s && !g_stop) {
        rc = a6400_uvc_query_identity(&rt->uvc, ids);
        if (rc == 0 && ids[0] == 0x054cu && ids[1] == 0x0caau) {
            struct timespec ts;

            a6400_logf(&g_log, A6400_LOG_INFO,
                       "pcremote_detected after_s=%d id=%04x:%04x (settling 2 s)",
                       waited, ids[0], ids[1]);
            /* Settle exactly like the physically run runner (line 146). */
            ts.tv_sec = 2;
            ts.tv_nsec = 0;
            while (nanosleep(&ts, &ts) != 0 && errno == EINTR && !g_stop) {
            }
            return 0;
        }
        {
            struct timespec ts;

            ts.tv_sec = 1;
            ts.tv_nsec = 0;
            while (nanosleep(&ts, &ts) != 0 && errno == EINTR && !g_stop) {
            }
        }
        waited++;
    }
    a6400_logf(&g_log, A6400_LOG_ERROR,
               "pcremote_timeout waited_s=%d last=%04x:%04x rc=%d",
               waited, ids[0], ids[1], rc);
    return -ETIMEDOUT;
}

int a6400_daemon_run(const struct a6400_daemon_config *cfg)
{
    struct daemon_rt rt;
    int code = A6400_EXIT_CLEAN;
    int pcremote_confirmed = 0;
    int rc;
    int log_rc;

    memset(&rt, 0, sizeof rt);
    memset(&g_stats, 0, sizeof g_stats);
    memset(&g_sd, 0, sizeof g_sd);
    g_stats.started_monotonic_ns = (uint64_t)a6400_now_ns();
    g_stats.phase = (uint32_t)A6400_PHASE_IDLE;
    g_stage = "init";
    g_fail_reason[0] = '\0';
    g_exit_code = A6400_EXIT_CLEAN;
    /* E0358: no recovery state exists unless a failed STOP created it. The
     * state arrays stay at -1 so the receipt can never present an unanswered
     * status read as an all-zero (verified) session. */
    memset(&g_recovery, 0, sizeof g_recovery);
    memset(g_recovery.state_before, 0xff, sizeof g_recovery.state_before);
    memset(g_recovery.state_after, 0xff, sizeof g_recovery.state_after);
    g_rc_teardown_verified = 0;
    g_rc_stop_attempts = 0;
    g_cfg = *cfg;
    /* Initialise the SD status even when the feature is off: the receipt then
     * says "enabled:0" instead of omitting the block. */
    a6400_sd_status_init(&g_sd, cfg->sd_log, cfg->sd_mount, cfg->sd_dev,
                         cfg->sd_mountpoint);
    rt.uvc.fd = -1;
    rt.uipc.fd = -1;
    rt.mem_fd = -1;
    a6400_frame_queue_init(&rt.q);
    a6400_runner_init(&rt.runner, -1, cfg->target_pid);
    rt.exp_w = (uint32_t)cfg->expected_width;
    rt.exp_h = (uint32_t)cfg->expected_height;

    rc = validate_config(cfg);
    if (rc != 0) {
        log_rc = a6400_log_open(&g_log, cfg->log_path,
                                cfg->verbose ? A6400_LOG_DEBUG : A6400_LOG_INFO);
        (void)log_rc;
        if (rc == -14)
            code = stage_fail("refused", A6400_EXIT_REFUSED,
                              "destructive steps require --arm-transport "
                              "(or use --dry-run)");
        else if (rc == -17)
            code = stage_fail("refused", A6400_EXIT_REFUSED,
                              "--sd-mount requires --sd-log (mounting without a "
                              "sink is never useful)");
        else if (rc == -18)
            code = stage_fail("refused", A6400_EXIT_REFUSED,
                              "--sd-dev refused: only /dev/mmca1 is allowed "
                              "(internal flash is never mountable)");
        else if (rc == -19)
            code = stage_fail("refused", A6400_EXIT_REFUSED,
                              "--sd-mountpoint refused: must be a child of /tmp_bt");
        else
            code = stage_fail("refused", A6400_EXIT_REFUSED,
                              "invalid configuration (validate rc=%d)", rc);
        (void)write_receipt();
        a6400_log_close(&g_log);
        return code;
    }

    log_rc = a6400_log_open(&g_log, cfg->log_path,
                            cfg->verbose ? A6400_LOG_DEBUG : A6400_LOG_INFO);
    if (log_rc != 0) {
        /* Logging failure is not fatal by contract (a6400_log.h:39-40); the
         * receipt still records the run because it is written separately. */
        fprintf(stderr, "a6400-lived: log open failed rc=%d path=%s\n",
                log_rc, cfg->log_path);
    }
    log_event("start",
              "\"pid\":%d,\"dry_run\":%d,\"arm_transport\":%d,\"read_prefix\":%d,"
              "\"fps\":%d,\"path_width\":%d,\"comp_level\":%d,\"duration_s\":%d",
              cfg->target_pid, cfg->dry_run, g_arm_transport, g_read_prefix,
              cfg->fps, cfg->path_width, cfg->comp_level, cfg->duration_s);

    {
        struct sigaction sa;

        memset(&sa, 0, sizeof sa);
        sa.sa_handler = sig_handler;
        sigemptyset(&sa.sa_mask);
        sa.sa_flags = 0;
        (void)sigaction(SIGINT, &sa, NULL);
        (void)sigaction(SIGTERM, &sa, NULL);
        (void)signal(SIGPIPE, SIG_IGN);
    }

    if (g_wifi_stream) {
        if (cfg->dry_run || !g_arm_transport) {
            code = stage_fail("wifi_stream", A6400_EXIT_REFUSED,
                              "wifi-stream requires --arm-transport (and not --dry-run)");
            goto finish;
        }
        rc = a6400_wifi_server_open(&rt.wifi, g_wifi_stream_port,
                                    g_wifi_http_port, g_wifi_ctrl_port);
        if (rc != 0) {
            code = stage_fail("wifi_open", A6400_EXIT_STAGE,
                              "wifi server open failed rc=%d", rc);
            goto finish;
        }
        rt.wifi_open = 1;
        rt.meta.slot_size = 262144u;
        rt.meta.max_frame_payload = 262144u - A6400_UVC_HEADER_SIZE;
        rt.meta.total_size = 262144u * 2u;
        /* Non-zero, non-wrapping, disjoint from System RAM DMM frames. */
        rt.meta.phys_addr = 0xf0000000u;
        g_stats.slot_size = rt.meta.slot_size;
        g_stats.max_frame_payload = rt.meta.max_frame_payload;
        a6400_logf(&g_log, A6400_LOG_INFO,
                   "wifi_server_open stream=%u http=%u ctrl=%u",
                   (unsigned)rt.wifi.bound_stream_port,
                   (unsigned)rt.wifi.bound_http_port,
                   (unsigned)rt.wifi.bound_ctrl_port);
        goto wifi_after_uvc;
    }

    /* --- device open (always; read-only discovery is the whole point of
     *     --dry-run and the identity query needs the node) ------------------ */
    g_stage = "device_open";
    g_stats.phase = (uint32_t)A6400_PHASE_IDLE;
    rc = a6400_uvc_open(&rt.uvc, cfg->device_path);
    if (rc != 0) {
        code = stage_fail("device_open", A6400_EXIT_STAGE,
                          "open %s failed rc=%d", cfg->device_path, rc);
        goto finish;
    }
    rt.uvc_open = 1;
    rt.runner.uvc_fd = rt.uvc.fd;
    a6400_logf(&g_log, A6400_LOG_INFO, "device_open path=%s node_created=%d",
               rt.uvc.path, rt.uvc.node_created);

    {
        uint16_t ids[2] = {0, 0};
        uint32_t state[4] = {0, 0, 0, 0};
        int id_rc = a6400_uvc_query_identity(&rt.uvc, ids);
        int st_rc = a6400_uvc_query_usb_state(&rt.uvc, state);

        log_event("usb_identity",
                  "\"rc\":%d,\"vid\":%u,\"pid\":%u,\"state_rc\":%d,"
                  "\"flags\":%u,\"config\":%u,\"mode\":%u,\"speed\":%u",
                  id_rc, ids[0], ids[1], st_rc, state[0], state[1], state[2],
                  state[3]);
    }

    if (cfg->wait_pcremote) {
        g_stage = "wait_pcremote";
        g_stats.phase = (uint32_t)A6400_PHASE_WAIT_PCREMOTE;
        if (wait_for_pcremote(&rt, cfg) != 0) {
            code = stage_fail("wait_pcremote", A6400_EXIT_STAGE,
                              "PC Remote 0x054c:0x0caa not seen in %d s",
                              cfg->wait_timeout_s);
            goto finish;
        }
        pcremote_confirmed = 1;
    }

    /* --- optional crash-safe SD log (E0350) -------------------------------- */
    /* The camera app owns the SD card in Mass Storage. The only safe window is:
     * PC Remote confirmed (054c:0caa) AND before INIT/streaming. --sd-log alone
     * keeps the RAM log only; --sd-mount is the explicit mount(2) consent. A
     * refusal is recorded in the receipt's sd_log block - it is never silently
     * skipped and never retried outside the window. */
    if (cfg->sd_log) {
        struct a6400_sd_window w;

        memset(&w, 0, sizeof w);
        w.sd_log = cfg->sd_log;
        w.sd_mount = cfg->sd_mount;
        w.dry_run = cfg->dry_run;
        w.pcremote_confirmed = pcremote_confirmed;
        w.init_started = 0;
        w.streaming = 0;
        /* msc_mode is not guessed: unconfirmed PC Remote is refused by the
         * pcremote gate, which is the observable "camera app may own the SD"
         * condition. */
        w.msc_mode = 0;
        g_stage = "sd_log";
        if (!cfg->sd_mount) {
            g_sd.mount_rc = A6400_SD_ERR_NO_CONSENT;
            log_event("sd_log_ram_only",
                      "\"reason\":\"mount_consent_missing\"");
        } else {
            rc = a6400_sd_mount(&g_sd, &w);
            if (rc == A6400_SD_OK) {
                struct a6400_log_sd_stats sds;

                g_sd.open_rc = a6400_log_sd_open(&g_log, g_sd.mountpoint);
                memset(&sds, 0, sizeof sds);
                a6400_log_sd_get(&g_log, &sds);
                g_sd.sync_mode = sds.sync_mode;
                if (g_sd.open_rc != 0) {
                    a6400_logf(&g_log, A6400_LOG_WARN,
                               "sd_log_open_failed rc=%d path=%s/%s",
                               g_sd.open_rc, g_sd.mountpoint, A6400_SD_LOG_NAME);
                }
                log_event("sd_log_mounted",
                          "\"device\":\"%s\",\"mountpoint\":\"%s\","
                          "\"open_rc\":%d,\"sync_mode\":%d,\"verified\":%d,"
                          "\"sentinel_ok\":%d",
                          g_sd.device, g_sd.mountpoint, g_sd.open_rc,
                          g_sd.sync_mode, g_sd.verified, g_sd.sentinel_ok);
            } else {
                a6400_logf(&g_log, A6400_LOG_WARN,
                           "sd_log_mount_refused rc=%d reason=%s device=%s mountpoint=%s",
                           rc, g_sd.reason, g_sd.device, g_sd.mountpoint);
                log_event("sd_log_unavailable",
                          "\"rc\":%d,\"reason\":\"%s\",\"mounted\":0",
                          rc, g_sd.reason);
            }
        }
        g_stage = "init";
    }

    /* --- DMA window discovery --------------------------------------------- */
    /* Dry-run probes the read-only query before any session exists; the armed
     * path asks again after INIT, because the authored module's shared ioctl is
     * not guaranteed to answer before the session is up. */
    if (cfg->dry_run) {
        rc = resolve_dma_window(&rt, 1);
        g_stage = "dry_run";
        g_stats.phase = (uint32_t)A6400_PHASE_IDLE;
        log_event("dry_run_complete",
                  "\"dma_query_rc\":%d,\"dma_meta_source\":%d,"
                  "\"arm_transport_needed\":1",
                  g_rc_dma_query_rc, g_rc_meta_source);
        code = A6400_EXIT_CLEAN;
        (void)rc;
        goto finish;
    }

    /* --- everything below is destructive ---------------------------------- */
    /* --- freeze + entry hook publish ------------------------------------- */
    g_stage = "freeze";
    g_stats.phase = (uint32_t)A6400_PHASE_FREEZE;
    log_destructive("ptrace_freeze");
    rc = a6400_runner_freeze(&rt.runner);
    g_rc_tid_count = rt.runner.tid_count;
    g_rc_freeze_ms = rt.runner.last_freeze_ms;
    a6400_logf(&g_log, A6400_LOG_INFO, "freeze rc=%d tids=%u elapsed_ms=%u",
               rc, rt.runner.tid_count, rt.runner.last_freeze_ms);
    if (rc != 0) {
        code = stage_fail("freeze", A6400_EXIT_STAGE,
                          "freeze of pid %d failed rc=%d", cfg->target_pid, rc);
        goto finish;
    }

    g_stage = "hook_publish";
    log_destructive("hook_publish");
    rc = a6400_runner_publish_hook(&rt.runner);
    g_rc_attest_completed = rt.runner.attest_completed;
    g_rc_attest_error = rt.runner.attest_error;
    a6400_logf(&g_log, A6400_LOG_INFO,
               "hook_publish rc=%d completed=%u error=%d",
               rc, rt.runner.attest_completed, rt.runner.attest_error);
    {
        /* Resume even when publish failed: never leave the camera frozen. */
        int resume_rc = a6400_runner_resume(&rt.runner);

        a6400_logf(&g_log, A6400_LOG_INFO, "publish_resume rc=%d", resume_rc);
        if (resume_rc != 0) {
            code = stage_fail("hook_publish", A6400_EXIT_FATAL,
                              "resume after publish failed rc=%d", resume_rc);
            goto finish;
        }
    }
    if (rc != 0) {
        code = stage_fail("hook_publish", A6400_EXIT_STAGE,
                          "entry hook publish failed rc=%d", rc);
        goto finish;
    }
    g_rc_hook_published = 1;
    g_stats.phase = (uint32_t)A6400_PHASE_HOOK_PUBLISHED;

    /* --- INIT ------------------------------------------------------------- */
    g_stage = "uvc_init";
    g_stats.phase = (uint32_t)A6400_PHASE_INIT;
    {
        /* g_interfaces is the 4-byte list the physically run feeder and runner
         * both used; interface_count is 2 (uvc_sensor_feeder_30fps.c:91,599). */
        static const uint8_t interfaces[A6400_UVC_INTERFACE_LIST_LEN] =
            { 0, 0, 1, 1 };

        log_destructive("uvc_init");
        rt.init_attempted = 1;
        g_rc_init_attempted = 1;
        rc = a6400_uvc_init(&rt.uvc, interfaces, 2);
    }
    a6400_logf(&g_log, A6400_LOG_INFO, "uvc_init rc=%d", rc);
    if (rc != 0) {
        code = stage_fail("uvc_init", A6400_EXIT_STAGE,
                          "IOCTL_UVC_INIT failed rc=%d (EINVAL in Mass Storage)", rc);
        goto finish;
    }
    rt.session_active = 1;
    g_rc_session_active = 1;
    g_stats.session_active = 1;
    g_stats.phase = (uint32_t)A6400_PHASE_BUFFER_READY;

    /* --- DMA window (post-INIT: see the dry-run note above) --------------- */
    g_stage = "dma_query";
    if (resolve_dma_window(&rt, 0) != 0) {
        code = g_exit_code != A6400_EXIT_CLEAN ? g_exit_code : A6400_EXIT_STAGE;
        goto finish;
    }
    g_stats.slot_size = rt.meta.slot_size;
    g_stats.max_frame_payload = rt.meta.max_frame_payload;

    /* --- REG_BUF + ENABLE ------------------------------------------------- */
    g_stage = "uvc_reg_buf";
    log_destructive("uvc_reg_buf");
    rc = a6400_uvc_reg_buf(&rt.uvc, rt.meta.total_size, rt.meta.phys_addr);
    g_rc_reg_buf_rc = rc;
    a6400_logf(&g_log, A6400_LOG_INFO, "uvc_reg_buf rc=%d size=%u phys=%u",
               rc, rt.meta.total_size, rt.meta.phys_addr);
    if (rc != 0) {
        code = stage_fail("uvc_reg_buf", A6400_EXIT_STAGE,
                          "REG_BUF rejected rc=%d (window not acknowledged)", rc);
        goto finish;
    }
    rt.reg_buf_ok = 1;

    g_stage = "uvc_enable";
    log_destructive("uvc_enable");
    rc = a6400_uvc_enable(&rt.uvc);
    g_rc_enable_rc = rc;
    a6400_logf(&g_log, A6400_LOG_INFO, "uvc_enable rc=%d", rc);
    if (rc != 0) {
        code = stage_fail("uvc_enable", A6400_EXIT_STAGE,
                          "ENABLE failed rc=%d", rc);
        goto finish;
    }
    rt.enable_ok = 1;

wifi_after_uvc:
    /* --- control FIFO ----------------------------------------------------- */
    g_stage = "control";
    if (open_control(&rt, cfg) != 0) {
        code = stage_fail("control", A6400_EXIT_STAGE, "control bridge unavailable");
        goto finish;
    }

    /* --- OSAL + DMM rule 5 ------------------------------------------------ */
    g_stage = "osal_open";
    rc = a6400_uipc_open(&rt.uipc);
    a6400_logf(&g_log, A6400_LOG_INFO, "osal_open rc=%d reply_qid=%u pool=%u",
               rc, rt.uipc.reply_qid, rt.uipc.pool_size);
    if (rc != 0) {
        code = stage_fail("osal_open", A6400_EXIT_STAGE, "OSAL open failed rc=%d", rc);
        goto finish;
    }
    rt.uipc_open = 1;
    rt.dmm.uipc = &rt.uipc;

    if (g_probe_pins) {
        static const uint32_t qs[] = {
            0x00050004u, 0x0058003fu, 0x0059000eu,
            0x005700f9u, 0x005a0048u, 0x00b201aau
        };
        uint32_t w[8];
        uint32_t pin, qi;
        for (pin = 0; pin < 3; pin++) {
            rc = a6400_df_get_pin_info(&rt.uipc, 0x01670464u, 0, 1, pin, w, 300);
            printf("PIN_INFO q=0x01670464 dir=1 pin=%u rc=%d w=%08x %08x %08x %08x %08x %08x %08x %08x\n",
                   pin, rc, w[0], w[1], w[2], w[3], w[4], w[5], w[6], w[7]);
        }
        for (pin = 0; pin < 3; pin++) {
            rc = a6400_df_get_pin_info(&rt.uipc, 0x01670464u, 0, 2, pin, w, 300);
            printf("PIN_INFO q=0x01670464 dir=2 pin=%u rc=%d w=%08x %08x %08x %08x %08x %08x %08x %08x\n",
                   pin, rc, w[0], w[1], w[2], w[3], w[4], w[5], w[6], w[7]);
        }
        for (qi = 0; qi < sizeof(qs) / sizeof(qs[0]); qi++) {
            uint32_t dir;
            for (dir = 1; dir <= 2; dir++) {
                for (pin = 0; pin < 3; pin++) {
                    rc = a6400_df_get_pin_info(&rt.uipc, qs[qi], 0, dir, pin, w, 300);
                    if (rc == 0)
                        printf("PIN_INFO q=0x%08x dir=%u pin=%u rc=%d w=%08x %08x %08x %08x %08x %08x %08x %08x\n",
                               qs[qi], dir, pin, rc, w[0], w[1], w[2], w[3], w[4], w[5], w[6], w[7]);
                }
            }
        }
        fflush(stdout);
        a6400_uipc_close(&rt.uipc);
        rt.uipc_open = 0;
        if (rt.wifi_open) {
            a6400_wifi_server_close(&rt.wifi);
            rt.wifi_open = 0;
        }
        return 0;
    }

    if (g_probe_yuv) {
        static const uint32_t types[] = {
            0x000002c5u, 0x00030700u, 0x00000298u, 0x000001b9u, 0x00000296u
        };
        uint32_t ti;
        rt.mem_fd = open("/dev/mem", O_RDONLY | O_CLOEXEC);
        for (ti = 0; ti < sizeof(types) / sizeof(types[0]); ti++) {
            uint16_t hs[8];
            int cnt = find_dmm_handles_by_type(types[ti], hs, 8);
            if (cnt <= 0) {
                printf("YUV_PROBE type=0x%08x count=0\n", types[ti]);
                continue;
            }
            {
                uint32_t phys = 0, mapped = 0;
                int orc = a6400_dmm_open_unit(&rt.dmm, hs[0], &phys, &mapped);
                if (orc != 0 || !phys || mapped < 4096u || rt.mem_fd < 0) {
                    printf("YUV_PROBE type=0x%08x count=%d h=0x%04x open_rc=%d\n",
                           types[ti], cnt, hs[0], orc);
                    continue;
                }
                {
                    uint32_t page = phys & ~4095u;
                    uint32_t off = phys - page;
                    uint32_t mlen = (off + mapped + 4095u) & ~4095u;
                    void *m = mmap(NULL, mlen, PROT_READ, MAP_SHARED,
                                   rt.mem_fd, (off_t)page);
                    if (m != MAP_FAILED) {
                        const uint8_t *b = (const uint8_t *)m + off;
                        uint8_t s1[4096], s2[4096];
                        uint32_t mid = mapped / 2u;
                        uint32_t k, diff = 0, nonzero = 0;
                        struct timespec ts = { 0, 150000000L };
                        if (mid + 4096u > mapped)
                            mid = 0;
                        memcpy(s1, b + mid, sizeof s1);
                        nanosleep(&ts, NULL);
                        memcpy(s2, b + mid, sizeof s2);
                        for (k = 0; k < sizeof s1; k++) {
                            if (s1[k] != s2[k])
                                diff++;
                            if (s2[k] != 0)
                                nonzero++;
                        }
                        printf("YUV_PROBE type=0x%08x count=%d h=0x%04x phys=0x%08x "
                               "size=%u diff=%u/4096 nz=%u/4096 "
                               "b0=%02x%02x%02x%02x%02x%02x%02x%02x\n",
                               types[ti], cnt, hs[0], phys, mapped, diff, nonzero,
                               s2[0], s2[1], s2[2], s2[3], s2[4], s2[5], s2[6], s2[7]);
                        munmap(m, mlen);
                    }
                    (void)a6400_dmm_close_unit(&rt.dmm, hs[0]);
                }
            }
        }
        fflush(stdout);
        if (rt.mem_fd >= 0) {
            close(rt.mem_fd);
            rt.mem_fd = -1;
        }
        a6400_uipc_close(&rt.uipc);
        rt.uipc_open = 0;
        if (rt.wifi_open) {
            a6400_wifi_server_close(&rt.wifi);
            rt.wifi_open = 0;
        }
        return 0;
    }

    if (g_yuv_stream_type) {
        uint16_t hs[8];
        void *maps[8];
        uint32_t mlens[8], offs[8], sizes[8];
        uint8_t prev_fp[8][64];
        int cnt, k, mapped_cnt = 0;
        uint32_t seq = 0;
        uint64_t period_ns = 1000000000ull / (uint64_t)cfg->fps;
        uint64_t deadline = (uint64_t)a6400_now_ns();
        uint64_t end_ns = cfg->duration_s > 0
            ? deadline + (uint64_t)cfg->duration_s * 1000000000ull : 0;

        memset(maps, 0, sizeof maps);
        memset(prev_fp, 0, sizeof prev_fp);
        rt.mem_fd = open("/dev/mem", O_RDONLY | O_CLOEXEC);
        cnt = find_dmm_handles_by_type(g_yuv_stream_type, hs, 8);
        for (k = 0; k < cnt; k++) {
            uint32_t phys = 0, mapped = 0;
            if (a6400_dmm_open_unit(&rt.dmm, hs[k], &phys, &mapped) == 0 &&
                phys && mapped >= 4096u && rt.mem_fd >= 0) {
                uint32_t page = phys & ~4095u;
                uint32_t off = phys - page;
                uint32_t mlen = (off + mapped + 4095u) & ~4095u;
                void *m = mmap(NULL, mlen, PROT_READ, MAP_SHARED,
                               rt.mem_fd, (off_t)page);
                if (m != MAP_FAILED) {
                    maps[mapped_cnt] = m;
                    mlens[mapped_cnt] = mlen;
                    offs[mapped_cnt] = off;
                    sizes[mapped_cnt] = mapped;
                    hs[mapped_cnt] = hs[k];
                    mapped_cnt++;
                } else {
                    (void)a6400_dmm_close_unit(&rt.dmm, hs[k]);
                }
            }
        }
        a6400_logf(&g_log, A6400_LOG_INFO,
                   "yuv_stream_open type=0x%08x buffers=%d size=%u fps=%d",
                   g_yuv_stream_type, mapped_cnt,
                   mapped_cnt > 0 ? sizes[0] : 0u, cfg->fps);
        if (mapped_cnt > 0) {
            int last_idx = 0;
            while (!g_stop) {
                struct timespec ts;
                int pick = -1;
                uint64_t now;

                if (end_ns && (uint64_t)a6400_now_ns() >= end_ns)
                    break;
                (void)poll_control(&rt, cfg);
                if (g_stop)
                    break;
                for (k = 0; k < mapped_cnt; k++) {
                    const uint8_t *b = (const uint8_t *)maps[k] + offs[k];
                    uint32_t mid = sizes[k] / 2u;
                    if (mid + 64u > sizes[k])
                        mid = 0;
                    if (memcmp(prev_fp[k], b + mid, 64u) != 0) {
                        memcpy(prev_fp[k], b + mid, 64u);
                        pick = k;
                    }
                }
                if (pick < 0)
                    pick = last_idx;
                else
                    last_idx = pick;

                seq++;
                g_stats.frames_captured++;
                {
                    static uint8_t uyvy_cached[545280];
                    static uint8_t jpeg_buf[131072];
                    uint32_t jpeg_len = 0;
                    const uint8_t *src = (const uint8_t *)maps[pick] + offs[pick];

                    if (g_yuv_stream_type == 0x00030700u && sizes[pick] >= 545280u) {
                        uint64_t t0 = (uint64_t)a6400_now_ns();
                        uint64_t t1, t2, t3;
                        /* Burst-copy from uncached /dev/mem into cached RAM once
                         * so the JPEG encoder runs entirely out of L1/L2 cache. */
                        memcpy(uyvy_cached, src, 545280u);
                        t1 = (uint64_t)a6400_now_ns();
                        if (a6400_uyvy_to_jpeg_mt(uyvy_cached, 640u, 426u, 1280u, 75,
                                                  jpeg_buf, sizeof jpeg_buf, &jpeg_len) == 0 &&
                            jpeg_len > 0) {
                            t2 = (uint64_t)a6400_now_ns();
                            (void)a6400_wifi_server_send_frame(
                                &rt.wifi, seq, a6400_now_ns(),
                                jpeg_buf, jpeg_len, 1u);
                            t3 = (uint64_t)a6400_now_ns();
                            if (seq <= 5u) {
                                a6400_logf(&g_log, A6400_LOG_INFO,
                                           "yuv_timing seq=%u copy_us=%llu enc_us=%llu send_us=%llu jpg=%u",
                                           seq,
                                           (unsigned long long)((t1 - t0) / 1000ull),
                                           (unsigned long long)((t2 - t1) / 1000ull),
                                           (unsigned long long)((t3 - t2) / 1000ull),
                                           jpeg_len);
                            }
                        }
                    } else {
                        (void)a6400_wifi_server_send_frame(
                            &rt.wifi, seq, a6400_now_ns(),
                            src, sizes[pick], 0x10u);
                    }
                }
                g_stats.frames_submitted = (uint32_t)rt.wifi.stream_sent;
                g_stats.frames_dropped = (uint32_t)rt.wifi.stream_dropped;

                deadline += period_ns;
                now = (uint64_t)a6400_now_ns();
                if (now >= deadline)
                    deadline = now;
                ts.tv_sec = (time_t)(deadline / 1000000000ull);
                ts.tv_nsec = (long)(deadline % 1000000000ull);
                while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL) == EINTR) {
                    if (g_stop)
                        break;
                }
            }
            for (k = 0; k < mapped_cnt; k++) {
                munmap(maps[k], mlens[k]);
                (void)a6400_dmm_close_unit(&rt.dmm, hs[k]);
            }
        }
        a6400_logf(&g_log, A6400_LOG_INFO,
                   "yuv_stream_closed captured=%u sent=%llu dropped=%llu",
                   g_stats.frames_captured,
                   (unsigned long long)rt.wifi.stream_sent,
                   (unsigned long long)rt.wifi.stream_dropped);
        if (rt.mem_fd >= 0) {
            close(rt.mem_fd);
            rt.mem_fd = -1;
        }
        a6400_uipc_close(&rt.uipc);
        rt.uipc_open = 0;
        if (rt.wifi_open) {
            a6400_wifi_server_close(&rt.wifi);
            rt.wifi_open = 0;
        }
        return 0;
    }

    g_stage = "dmm_rule";
    log_destructive("dmm_rule_5");
    rc = a6400_dmm_set_rule(&rt.dmm, 5, 0);
    a6400_logf(&g_log, A6400_LOG_INFO, "dmm_rule_5 status=%d", rc);
    if (rc != 0) {
        code = stage_fail("dmm_rule", A6400_EXIT_STAGE,
                          "DMM rule 5 rejected status=%d", rc);
        goto finish;
    }

    /* --- SET_PATH --------------------------------------------------------- */
    g_stage = "set_path";
    {
        struct a6400_scalar_path_cfg sc;

        log_destructive("set_path_reset");
        (void)a6400_scalar_set_liveview(&rt.dmm, 0, 1500);
        memset(&sc, 0, sizeof sc);
        sc.timeout_ms = 1500;
        rc = a6400_scalar_set_path(&rt.dmm, &sc);
        a6400_logf(&g_log, A6400_LOG_INFO, "set_path_reset status=%d", rc);
    }
    if (g_connect_pin_qid) {
        uint32_t src_pin[4] = { 0u, 2u, g_connect_pin_no, g_connect_pin_qid };
        uint32_t dst_pin[4] = { 0u, 1u, g_connect_dst_pin, 0x01670464u };
        uint32_t st1 = 0xffffffffu;
        int rc1 = a6400_df_pin_connect(&rt.uipc, g_connect_pin_qid, src_pin, dst_pin, &st1, 1500);
        a6400_logf(&g_log, A6400_LOG_INFO,
                   "pin_connect q=0x%08x pin=%u dst=%u src_rc=%d src_st=0x%x",
                   g_connect_pin_qid, g_connect_pin_no, g_connect_dst_pin, rc1, st1);
    }
    {
        struct a6400_scalar_path_cfg sc;

        if (g_use_liveview_cmd) {
            rc = a6400_scalar_set_liveview(&rt.dmm, 1, 5000);
            a6400_logf(&g_log, A6400_LOG_INFO, "set_liveview_start status=%d", rc);
        } else if (g_splice_scalar) {
            rc = apply_scalar_splice(&rt, cfg, "init");
        } else {
                memset(&sc, 0, sizeof sc);
                sc.enable = 2; /* PinConnect (payload+8 = 6) */
                sc.rate_fps = (uint32_t)cfg->fps;
                sc.width = (uint32_t)cfg->path_width;
                sc.comp_level = (uint32_t)cfg->comp_level;
                sc.param_val = 50;
                sc.timeout_ms = 5000;
                rc = a6400_scalar_set_path(&rt.dmm, &sc);
                a6400_logf(&g_log, A6400_LOG_INFO,
                           "set_path_pinconnect status=%d rate=%u width=%u",
                           rc, sc.rate_fps, sc.width);

                log_destructive("set_path_start");
                memset(&sc, 0, sizeof sc);
                sc.enable = 1;
                sc.rate_fps = (uint32_t)cfg->fps;
                sc.width = (uint32_t)cfg->path_width;
                sc.comp_level = (uint32_t)cfg->comp_level;
                sc.param_val = 50; /* libInfraStreamingServer: 50 */
                sc.timeout_ms = 5000;
                sc.pin_no = 0u;
                rc = a6400_scalar_set_path(&rt.dmm, &sc);
                a6400_logf(&g_log, A6400_LOG_INFO,
                           "set_path_start status=%d rate=%u width=%u comp=%u",
                           rc, sc.rate_fps, sc.width, sc.comp_level);
        }
    }
    if (rc != 0) {
        code = stage_fail("set_path", A6400_EXIT_STAGE,
                          "SET_PATH start rejected status=%d", rc);
        goto finish;
    }

    /* --- /dev/mem reader (optional; never fabricated) --------------------- */
    rt.mem_fd = open("/dev/mem", O_RDONLY | O_CLOEXEC);
    if (rt.mem_fd < 0) {
        a6400_logf(&g_log, A6400_LOG_WARN,
                   "dev_mem_unavailable errno=%d; JPEG prefix cannot be read", errno);
    }

    /* --- stream ----------------------------------------------------------- */
    g_stage = "stream";
    g_stats.phase = (uint32_t)A6400_PHASE_STREAMING;
    log_event("stream_begin",
              "\"fps\":%d,\"slot\":%u,\"max_payload\":%u,\"duration_s\":%d,"
              "\"expected\":\"%ux%u\"",
              cfg->fps, rt.meta.slot_size, rt.meta.max_frame_payload,
              cfg->duration_s, rt.exp_w, rt.exp_h);
    if (stream_loop(&rt, cfg) != 0) {
        code = g_exit_code != A6400_EXIT_CLEAN ? g_exit_code : A6400_EXIT_STAGE;
        goto finish;
    }

    g_stats.phase = (uint32_t)A6400_PHASE_STOPPING;
    if (g_stop)
        log_event("stream_stopped", "\"reason\":\"signal_or_control\"");
    else
        log_event("stream_stopped", "\"reason\":\"duration_elapsed\"");
    code = A6400_EXIT_CLEAN;

finish:
    g_exit_code = code;
    teardown(&rt, cfg, &code);
    g_exit_code = code;
    if (code == A6400_EXIT_CLEAN)
        g_stage = g_rc_hook_published ? "teardown" : "complete";
    (void)write_receipt();
    log_event("exit", "\"status\":\"%s\",\"code\":%d", status_name(code), code);
    a6400_log_close(&g_log);
    return code;
}

int main(int argc, char **argv)
{
    struct a6400_daemon_config cfg;
    int rc;

    memset(&cfg, 0, sizeof cfg);
    cfg.device_path = A6400_UVC_NODE_PATH;
    cfg.log_path = "/tmp_bt/a6400_lived.jsonl";
    cfg.receipt_path = "/tmp_bt/a6400_lived_receipt.json";
    cfg.control_fifo = "/tmp_bt/a6400_ctrl.fifo";
    cfg.control_ack = "/tmp_bt/a6400_ctrl.ack";
    cfg.wait_pcremote = 0;
    cfg.wait_timeout_s = 120;
    cfg.duration_s = 0;
    cfg.target_pid = A6400_DAEMON_TARGET_PID;
    cfg.expected_width = 0;
    cfg.expected_height = 0;
    cfg.fps = 30;
    cfg.comp_level = 5;
    cfg.path_width = 1280;
    cfg.dry_run = 0;
    cfg.verbose = 0;
    cfg.sd_log = 0;                    /* E0350: opt-in, default OFF */
    cfg.sd_mount = 0;                  /* explicit consent, default OFF */
    cfg.sd_dev = A6400_SD_DEV_DEFAULT;
    cfg.sd_mountpoint = A6400_SD_MOUNT_DEFAULT;
    g_park_recovery = 1;               /* E0358: bounded recovery is the default */

    rc = parse_argv(argc, argv, &cfg);
    if (rc == 1)
        return A6400_EXIT_CLEAN;
    if (rc != 0)
        return A6400_EXIT_REFUSED;

    return a6400_daemon_run(&cfg);
}
