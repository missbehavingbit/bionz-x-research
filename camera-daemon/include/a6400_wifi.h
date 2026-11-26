/* a6400 Wi-Fi liveview streaming and control TCP server.
 *
 * Provides:
 *   - Binary depth-1 MJPEG frame stream on `stream_port` (default 9640):
 *     each frame is a 24-byte little-endian header followed by `jpeg_bytes`.
 *   - Direct HTTP multipart/x-mixed-replace MJPEG endpoint on `http_port`
 *     (default 8080) for browser/OBS/FFmpeg consumption.
 *   - Binary control bridge endpoint on `ctrl_port` (default 9641) wrapping
 *     a6400_ctl_header / a6400_ctl_ack via a6400_control_handle().
 *
 * Depth-1 policy: if a stream client's socket buffer cannot accept a new frame
 * header without blocking, the frame is dropped immediately at the server so
 * transport backpressure never builds latency.
 */
#ifndef A6400_WIFI_H
#define A6400_WIFI_H

#include <stdint.h>
#include <stddef.h>

#include "a6400_control.h"
#include "a6400_daemon.h"
#include "a6400_internal.h"

#define A6400_WIFI_FRAME_MAGIC       0xA6405646u
#define A6400_WIFI_DEFAULT_STREAM    9640u
#define A6400_WIFI_DEFAULT_HTTP      8080u
#define A6400_WIFI_DEFAULT_CTRL      9641u

struct a6400_wifi_frame_hdr {
    uint32_t magic;       /* A6400_WIFI_FRAME_MAGIC */
    uint32_t frame_seq;
    uint32_t jpeg_bytes;
    uint32_t flags;       /* bit 0: valid JPEG dimensions */
    uint64_t capture_ns;
} __attribute__((packed));

_Static_assert(sizeof(struct a6400_wifi_frame_hdr) == 24,
               "a6400_wifi_frame_hdr must be 24 bytes");

struct a6400_wifi_server {
    int      stream_listen_fd;
    int      stream_client_fd;
    int      http_listen_fd;
    int      http_client_fd;
    int      http_headers_sent;
    int      ctrl_listen_fd;
    int      ctrl_client_fd;
    uint16_t bound_stream_port;
    uint16_t bound_http_port;
    uint16_t bound_ctrl_port;
    uint64_t stream_sent;
    uint64_t stream_dropped;
    uint64_t http_sent;
    uint64_t http_dropped;
    uint64_t ctrl_requests;
    struct a6400_ctl_frame_reader ctrl_reader;
};

/* Opens non-blocking TCP listeners on the requested ports (0 disables that
 * listener; bind_port==0 with enable flag is not used here: pass >=1 or
 * use a6400_wifi_server_open_ex for ephemeral port 0 binding in tests). */
int a6400_wifi_server_open(struct a6400_wifi_server *srv,
                           uint16_t stream_port,
                           uint16_t http_port,
                           uint16_t ctrl_port);

/* Same as a6400_wifi_server_open, but allows binding port 0 (ephemeral) for
 * enabled listeners (enable_mask bits: 1=stream, 2=http, 4=ctrl). */
int a6400_wifi_server_open_ex(struct a6400_wifi_server *srv,
                              unsigned enable_mask,
                              uint16_t stream_port,
                              uint16_t http_port,
                              uint16_t ctrl_port);

/* Accepts pending connections and processes any queued control requests on
 * ctrl_client_fd. Non-blocking; returns 0 on success. */
int a6400_wifi_server_poll(struct a6400_wifi_server *srv,
                           struct a6400_control_ctx *ctrl_ctx,
                           struct a6400_ctl_action *action);

/* Sends one JPEG frame to connected stream/http clients with depth-1
 * drop-on-backpressure semantics. Returns 0 on success or -EINVAL on bad args. */
int a6400_wifi_server_send_frame(struct a6400_wifi_server *srv,
                                 uint32_t frame_seq,
                                 int64_t capture_ns,
                                 const uint8_t *jpeg,
                                 uint32_t jpeg_len,
                                 uint32_t flags);

void a6400_wifi_server_close(struct a6400_wifi_server *srv);

#endif
