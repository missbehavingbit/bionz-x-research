/* a6400 Wi-Fi liveview streaming and control TCP server. */
#define _POSIX_C_SOURCE 200809L
#include "a6400_wifi.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/ip.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
#ifdef __linux__
#include <linux/sockios.h>
#endif

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif

static void close_fd(int *fd)
{
    if (fd && *fd >= 0) {
        close(*fd);
        *fd = -1;
    }
}

static int set_nonblock(int fd)
{
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0)
        return -errno;
    if (fcntl(fd, F_SETFL, flags | O_NONBLOCK) != 0)
        return -errno;
    return 0;
}

static void set_nodelay(int fd)
{
    int one = 1;
    int sndbuf = 65536;
    int tos = IPTOS_LOWDELAY | 0x80; /* WMM Video/Interactive AC_VI priority */
    (void)setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    (void)setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof sndbuf);
    (void)setsockopt(fd, IPPROTO_IP, IP_TOS, &tos, sizeof tos);
}

static void disable_wifi_powersave(void)
{
#ifdef __linux__
    /* Best-effort WEXT SIOCSIWPOWER (0x8B2C) on wlan0 to disable 802.11 PM
     * sleep between 33 ms video frames. Ignored if unsupported (e.g. host test). */
    struct {
        char ifname[16];
        int32_t value;
        uint8_t fixed;
        uint8_t disabled;
        uint16_t flags;
        uint8_t pad[8];
    } iwr;
    int s = socket(AF_INET, SOCK_DGRAM, 0);
    if (s < 0)
        return;
    memset(&iwr, 0, sizeof iwr);
    memcpy(iwr.ifname, "wlan0", 5);
    iwr.disabled = 1;
    (void)ioctl(s, 0x8B2Cul, &iwr);
    close(s);
#endif
}

static int open_listener(uint16_t port, uint16_t *bound_port)
{
    int fd;
    int one = 1;
    struct sockaddr_in addr;
    socklen_t alen = sizeof addr;

    fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0)
        return -errno;

    (void)setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    if (set_nonblock(fd) != 0) {
        int err = -errno;
        close(fd);
        return err;
    }

    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(port);

    if (bind(fd, (struct sockaddr *)&addr, sizeof addr) != 0) {
        int err = -errno;
        close(fd);
        return err;
    }
    if (listen(fd, 4) != 0) {
        int err = -errno;
        close(fd);
        return err;
    }
    if (bound_port) {
        if (getsockname(fd, (struct sockaddr *)&addr, &alen) == 0)
            *bound_port = ntohs(addr.sin_port);
        else
            *bound_port = port;
    }
    return fd;
}

/* Sends all `len` bytes once the first byte/header has been accepted, using a
 * bounded retry budget so partial writes never corrupt frame boundaries. */
static int send_all_bounded(int fd, const void *buf, size_t len, int allow_first_eagain)
{
    const uint8_t *p = (const uint8_t *)buf;
    size_t sent = 0;
    int spins = 0;

    while (sent < len) {
        ssize_t n = send(fd, p + sent, len - sent, MSG_NOSIGNAL);
        if (n > 0) {
            sent += (size_t)n;
            continue;
        }
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            struct pollfd pfd;
            int prc;
            if (sent == 0 && allow_first_eagain)
                return -EAGAIN;
            if (++spins > 50)
                return -ETIMEDOUT;
            pfd.fd = fd;
            pfd.events = POLLOUT;
            pfd.revents = 0;
            prc = poll(&pfd, 1, 5);
            if (prc < 0 && errno != EINTR)
                return -errno;
            if (pfd.revents & (POLLERR | POLLHUP | POLLNVAL))
                return -EPIPE;
            continue;
        }
        if (n < 0 && errno == EINTR)
            continue;
        return n < 0 ? -errno : -EPIPE;
    }
    return 0;
}

int a6400_wifi_server_open_ex(struct a6400_wifi_server *srv,
                              unsigned enable_mask,
                              uint16_t stream_port,
                              uint16_t http_port,
                              uint16_t ctrl_port)
{
    if (!srv)
        return -EINVAL;

    memset(srv, 0, sizeof *srv);
    srv->stream_listen_fd = -1;
    srv->stream_client_fd = -1;
    srv->http_listen_fd = -1;
    srv->http_client_fd = -1;
    srv->ctrl_listen_fd = -1;
    srv->ctrl_client_fd = -1;
    a6400_ctl_reader_reset(&srv->ctrl_reader);
    disable_wifi_powersave();

    if (enable_mask & 1u) {
        srv->stream_listen_fd = open_listener(stream_port, &srv->bound_stream_port);
        if (srv->stream_listen_fd < 0) {
            int err = srv->stream_listen_fd;
            a6400_wifi_server_close(srv);
            return err;
        }
    }
    if (enable_mask & 2u) {
        srv->http_listen_fd = open_listener(http_port, &srv->bound_http_port);
        if (srv->http_listen_fd < 0) {
            int err = srv->http_listen_fd;
            a6400_wifi_server_close(srv);
            return err;
        }
    }
    if (enable_mask & 4u) {
        srv->ctrl_listen_fd = open_listener(ctrl_port, &srv->bound_ctrl_port);
        if (srv->ctrl_listen_fd < 0) {
            int err = srv->ctrl_listen_fd;
            a6400_wifi_server_close(srv);
            return err;
        }
    }
    return 0;
}

int a6400_wifi_server_open(struct a6400_wifi_server *srv,
                           uint16_t stream_port,
                           uint16_t http_port,
                           uint16_t ctrl_port)
{
    unsigned mask = 0;
    if (stream_port)
        mask |= 1u;
    if (http_port)
        mask |= 2u;
    if (ctrl_port)
        mask |= 4u;
    return a6400_wifi_server_open_ex(srv, mask, stream_port, http_port, ctrl_port);
}

static void accept_latest(int listen_fd, int *client_fd, int *reset_flag)
{
    for (;;) {
        int cfd = accept(listen_fd, NULL, NULL);
        if (cfd < 0)
            break;
        close_fd(client_fd);
        (void)set_nonblock(cfd);
        set_nodelay(cfd);
        *client_fd = cfd;
        if (reset_flag)
            *reset_flag = 0;
    }
}

static void poll_http_handshake(struct a6400_wifi_server *srv)
{
    char req[512];
    ssize_t n;

    if (srv->http_client_fd < 0 || srv->http_headers_sent)
        return;

    n = recv(srv->http_client_fd, req, sizeof req - 1, 0);
    if (n > 0) {
        static const char k_http_hdr[] =
            "HTTP/1.1 200 OK\r\n"
            "Connection: close\r\n"
            "Cache-Control: no-store, no-cache, must-revalidate\r\n"
            "Pragma: no-cache\r\n"
            "Content-Type: multipart/x-mixed-replace; boundary=a640frame\r\n\r\n";
        if (send_all_bounded(srv->http_client_fd, k_http_hdr, sizeof k_http_hdr - 1, 0) == 0)
            srv->http_headers_sent = 1;
        else
            close_fd(&srv->http_client_fd);
    } else if (n == 0 || (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) {
        close_fd(&srv->http_client_fd);
    }
}

static void poll_ctrl_client(struct a6400_wifi_server *srv,
                             struct a6400_control_ctx *ctrl_ctx,
                             struct a6400_ctl_action *action)
{
    uint8_t rx[256];
    ssize_t n;

    if (srv->ctrl_client_fd < 0)
        return;

    for (;;) {
        n = recv(srv->ctrl_client_fd, rx, sizeof rx, 0);
        if (n > 0) {
            uint8_t frame[A6400_CTL_HEADER_SIZE + A6400_CTL_MAX_PAYLOAD];
            uint32_t frame_len = 0;
            int rc;

            a6400_ctl_reader_feed(&srv->ctrl_reader, rx, (uint32_t)n);
            while ((rc = a6400_ctl_reader_next(&srv->ctrl_reader,
                                               frame, sizeof frame,
                                               &frame_len)) != 0) {
                if (rc == 1 && ctrl_ctx) {
                    struct a6400_ctl_header hdr;
                    const uint8_t *payload = NULL;
                    uint32_t payload_len = 0;
                    struct a6400_ctl_ack ack;
                    struct a6400_ctl_action local_act;
                    const uint8_t *reply_payload = NULL;
                    uint32_t reply_len = 0;

                    memset(&ack, 0, sizeof ack);
                    if (a6400_ctl_parse_request(frame, frame_len, &hdr,
                                                &payload, &payload_len) == 0) {
                        (void)a6400_control_apply(ctrl_ctx, &hdr,
                                                  payload, payload_len,
                                                  &ack,
                                                  action ? action : &local_act);
                        reply_len = a6400_control_last_reply(ctrl_ctx, &reply_payload);
                    } else {
                        ack.magic = A6400_CTL_MAGIC;
                        ack.version = A6400_CTL_VERSION;
                        ack.status = A6400_CTL_STATUS_BAD_LENGTH;
                    }
                    srv->ctrl_requests++;
                    if (send_all_bounded(srv->ctrl_client_fd, &ack, sizeof ack, 0) != 0 ||
                        (reply_len > 0 && reply_payload &&
                         send_all_bounded(srv->ctrl_client_fd, reply_payload, reply_len, 0) != 0)) {
                        close_fd(&srv->ctrl_client_fd);
                        return;
                    }
                }
            }
        } else if (n == 0) {
            close_fd(&srv->ctrl_client_fd);
            a6400_ctl_reader_reset(&srv->ctrl_reader);
            return;
        } else {
            if (errno == EAGAIN || errno == EWOULDBLOCK)
                break;
            if (errno == EINTR)
                continue;
            close_fd(&srv->ctrl_client_fd);
            a6400_ctl_reader_reset(&srv->ctrl_reader);
            return;
        }
    }
}

int a6400_wifi_server_poll(struct a6400_wifi_server *srv,
                           struct a6400_control_ctx *ctrl_ctx,
                           struct a6400_ctl_action *action)
{
    if (!srv)
        return -EINVAL;

    if (srv->stream_listen_fd >= 0)
        accept_latest(srv->stream_listen_fd, &srv->stream_client_fd, NULL);
    if (srv->http_listen_fd >= 0)
        accept_latest(srv->http_listen_fd, &srv->http_client_fd, &srv->http_headers_sent);
    if (srv->ctrl_listen_fd >= 0) {
        int prev = srv->ctrl_client_fd;
        accept_latest(srv->ctrl_listen_fd, &srv->ctrl_client_fd, NULL);
        if (srv->ctrl_client_fd != prev)
            a6400_ctl_reader_reset(&srv->ctrl_reader);
    }

    poll_http_handshake(srv);
    poll_ctrl_client(srv, ctrl_ctx, action);
    return 0;
}

int a6400_wifi_server_send_frame(struct a6400_wifi_server *srv,
                                 uint32_t frame_seq,
                                 int64_t capture_ns,
                                 const uint8_t *jpeg,
                                 uint32_t jpeg_len,
                                 uint32_t flags)
{
    if (!srv || !jpeg || jpeg_len == 0)
        return -EINVAL;

    if (srv->stream_client_fd >= 0) {
        static uint8_t tx_coalesce[262144 + sizeof(struct a6400_wifi_frame_hdr)];
        struct a6400_wifi_frame_hdr hdr;
        int rc;
#ifdef __linux__
        int unsent = 0;
        if (jpeg_len > 4096u &&
            ioctl(srv->stream_client_fd, SIOCOUTQ, &unsent) == 0 &&
            unsent > 16384) {
            /* Depth-1 latency guard: never queue a new frame behind an unsent
             * frame still sitting in the Wi-Fi kernel TX queue. */
            srv->stream_dropped++;
            goto send_http;
        }
#endif

        hdr.magic = A6400_WIFI_FRAME_MAGIC;
        hdr.frame_seq = frame_seq;
        hdr.jpeg_bytes = jpeg_len;
        hdr.flags = flags;
        hdr.capture_ns = (uint64_t)capture_ns;

        if (jpeg_len <= 262144u) {
            memcpy(tx_coalesce, &hdr, sizeof hdr);
            memcpy(tx_coalesce + sizeof hdr, jpeg, jpeg_len);
            rc = send_all_bounded(srv->stream_client_fd, tx_coalesce,
                                  sizeof hdr + jpeg_len, 1);
            if (rc == 0) {
                srv->stream_sent++;
            } else if (rc == -EAGAIN) {
                srv->stream_dropped++;
            } else {
                close_fd(&srv->stream_client_fd);
                srv->stream_dropped++;
            }
        } else {
            rc = send_all_bounded(srv->stream_client_fd, &hdr, sizeof hdr, 1);
            if (rc == -EAGAIN) {
                srv->stream_dropped++;
            } else if (rc != 0) {
                close_fd(&srv->stream_client_fd);
                srv->stream_dropped++;
            } else {
                rc = send_all_bounded(srv->stream_client_fd, jpeg, jpeg_len, 0);
                if (rc == 0)
                    srv->stream_sent++;
                else {
                    close_fd(&srv->stream_client_fd);
                    srv->stream_dropped++;
                }
            }
        }
    }

#ifdef __linux__
send_http:
#endif
    if (srv->http_client_fd >= 0 && srv->http_headers_sent) {
        char part_hdr[128];
        int hlen = snprintf(part_hdr, sizeof part_hdr,
                            "--a640frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
                            (unsigned)jpeg_len);
        int rc = send_all_bounded(srv->http_client_fd, part_hdr, (size_t)hlen, 1);
        if (rc == -EAGAIN) {
            srv->http_dropped++;
        } else if (rc != 0) {
            close_fd(&srv->http_client_fd);
            srv->http_headers_sent = 0;
            srv->http_dropped++;
        } else {
            rc = send_all_bounded(srv->http_client_fd, jpeg, jpeg_len, 0);
            if (rc == 0 && send_all_bounded(srv->http_client_fd, "\r\n", 2, 0) == 0) {
                srv->http_sent++;
            } else {
                close_fd(&srv->http_client_fd);
                srv->http_headers_sent = 0;
                srv->http_dropped++;
            }
        }
    }

    return 0;
}

void a6400_wifi_server_close(struct a6400_wifi_server *srv)
{
    if (!srv)
        return;
    close_fd(&srv->stream_client_fd);
    close_fd(&srv->stream_listen_fd);
    close_fd(&srv->http_client_fd);
    close_fd(&srv->http_listen_fd);
    close_fd(&srv->ctrl_client_fd);
    close_fd(&srv->ctrl_listen_fd);
}
