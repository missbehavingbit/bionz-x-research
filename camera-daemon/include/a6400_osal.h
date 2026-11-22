/* a6400 OSAL (/dev/uipc) client ABI + DMM operations + DataflowInfra messages.
 *
 * Everything in this header is transcribed from physically exercised code in
 * this repository, not invented:
 *   - ioctl numbers, request structs and the dynamic-id protocol:
 *     tools/native_camera/uvc_sensor_feeder_30fps.c:221-323
 *     (physically run on the camera: DMM Rule 5 status=0, SET_PATH 720p30
 *      status=0 - docs/evidence/0339-physical-uvc-ptp-progress.md:48-50,
 *      docs/evidence/0340-uvc-fixedbuffer-and-mode-switch-reboot.md:16-18)
 *   - DMM operation numbers and reply offsets:
 *     tools/native_camera/uvc_sensor_feeder_30fps.c:325-428
 *   - DataflowInfra 20-byte header and SET_PATH/GET_JPEG payloads:
 *     tools/native_camera/uvc_sensor_feeder_30fps.c:431-529
 *   - GET_JPEG reply ABI and ownership transfer:
 *     docs/evidence/0144-native-jpeg-receiver-ownership-and-dma-allocation.md:19-23
 *   - queue ids are *named OSAL message destinations*, not code addresses:
 *     0x0167046a = Scalar DataflowInfra receiver (E0144:9-11);
 *     0x0061003c = DMM service queue (E0148).
 *
 * These numbers are message-ABI constants for this firmware family. They are
 * NOT allocation addresses and must never be used as memory locations.
 *
 * Allocation policy: this layer never allocates the frame buffer; it only
 * asks Scalar for a frame (GET_JPEG), opens the returned DMM unit to obtain
 * {physical address, size}, and must close+free every unit it is handed.
 */
#ifndef A6400_OSAL_H
#define A6400_OSAL_H

#include <stdint.h>
#include <stddef.h>

/* ---- /dev/uipc client ABI (uvc_sensor_feeder_30fps.c:221-323) ------------- */

#define A6400_UIPC_DEV_PATH     "/dev/uipc"

#define A6400_UIPC_IOCTL_VALLOC      0x8000000au
#define A6400_UIPC_IOCTL_FREE        0x8000000bu
#define A6400_UIPC_IOCTL_SEND        0x80000004u
#define A6400_UIPC_IOCTL_RECV        0x80000007u
#define A6400_UIPC_IOCTL_POOL_INIT   0x80000010u
#define A6400_UIPC_IOCTL_DYNAMIC_ID  0x8000000cu

#define A6400_UIPC_DYNID_ACQUIRE     0x2au
#define A6400_UIPC_DYNID_RELEASE     0x4au
/* 729 is the parameter value the physically run feeder used for acquire. */
#define A6400_UIPC_DYNID_ACQUIRE_PARAM 729u

/* Named OSAL queues (message destinations). */
#define A6400_UIPC_Q_SCALAR   0x0167046au   /* Scalar DataflowInfra receiver */
#define A6400_UIPC_Q_DMM      0x0061003cu   /* DMM service queue */

/* A message is allocated from the shared pool; the four bytes immediately
 * before the payload carry the reply queue id (see feeder:336,360,384). */
#define A6400_UIPC_MSG_PREFIX 4u

struct a6400_uipc {
    int      fd;
    uint32_t reply_qid;   /* dynamically acquired, per session, never a constant */
    void    *pool;        /* mmap of the shared pool reported by POOL_INIT */
    uint32_t pool_size;
};

/* Opens /dev/uipc, performs POOL_INIT + mmap and acquires a dynamic reply id.
 * Returns 0 on success, negative errno-style on failure. On failure the struct
 * is left closed (fd = -1). */
int a6400_uipc_open(struct a6400_uipc *u);

/* Purges queued replies, releases the dynamic id and closes the fd. Safe to
 * call on an already-closed struct. */
void a6400_uipc_close(struct a6400_uipc *u);

/* Thin wrappers over the ioctls above. `msg` is a pool pointer, not a copy. */
int a6400_uipc_valloc(struct a6400_uipc *u, uint32_t qid, void **msg, uint32_t size);
int a6400_uipc_free_msg(struct a6400_uipc *u, uint32_t qid, void *msg);
int a6400_uipc_send(struct a6400_uipc *u, uint32_t qid, void *msg);
int a6400_uipc_recv(struct a6400_uipc *u, uint32_t qid, void **msg, uint32_t timeout_ms);

/* Discards every message already queued on the reply id (non-blocking). */
void a6400_uipc_purge(struct a6400_uipc *u);

/* ---- DMM service operations (uvc_sensor_feeder_30fps.c:325-428) ---------- */

#define A6400_DMM_OP_REF        2u
#define A6400_DMM_OP_SET_RULE  20u
#define A6400_DMM_OP_OPEN       5u
#define A6400_DMM_OP_CLOSE      6u
#define A6400_DMM_OP_FREE       4u

/* Reply layout for every DMM op: int16 status at +2. The OPEN reply also
 * carries the frame physical address at +16 and the mapped size at +20
 * (feeder:393-400). A status of 0 means success; anything else is a failure
 * that must not be masked. */
#define A6400_DMM_REPLY_STATUS   2u
#define A6400_DMM_REPLY_OPEN_PHYS 16u
#define A6400_DMM_REPLY_OPEN_SIZE 20u

struct a6400_dmm {
    struct a6400_uipc *uipc;
};

/* Rule 5 is the physically used allocation rule for the 30 fps feeder; rule 0
 * restores the default. mode is forwarded at +0x16 (feeder:335). */
int a6400_dmm_set_rule(struct a6400_dmm *d, uint8_t rule, uint16_t mode);

/* Opens the DMM unit behind a GET_JPEG handle. On success writes the physical
 * address and mapped size. Every successful open must be paired with close+
 * free in the order close-then-free (feeder:673-694). */
int a6400_dmm_open_unit(struct a6400_dmm *d, uint32_t handle,
                        uint32_t *phys, uint32_t *size);
int a6400_dmm_close_unit(struct a6400_dmm *d, uint32_t handle);
int a6400_dmm_free_unit(struct a6400_dmm *d, uint32_t handle);

/* Increments the DMM allocation refcount of `handle` (op 2, matching
 * av-cam 0x63a8c6dc). Returns 0 on success, >0 on DMM status error, <0 on
 * transport error. */
int a6400_dmm_ref_unit(struct a6400_dmm *d, uint32_t unit_type,
                       uint32_t handle, uint16_t *out_val);

/* ---- DataflowInfra messages (uvc_sensor_feeder_30fps.c:431-529) ---------- */

/* 20-byte header: reply id, marker 0xa0a0a0a0, version 1, total length,
 * port 20, 20. The header is followed by the operation payload. */
#define A6400_DATAFLOW_HEADER_SIZE 20u
#define A6400_DATAFLOW_MARKER      0xa0a0a0a0u

/* External request selector byte (+0x0f). 0x20 selects the JPEG getter, 0x50 is
 * the selector the physical feeder uses for SET_PATH (evidence: E0145:38;
 * the width gate for 0x50 is NOT decoded, so never assume 1280x720 is
 * accepted - discover the accepted geometry from the first real frame). */
#define A6400_DF_SELECTOR_JPEG     0x20u
#define A6400_DF_SELECTOR_PATH     0x50u
#define A6400_DF_SELECTOR_LIVEVIEW 0x51u

struct a6400_scalar_path_cfg {
    int      enable;     /* 0 = stop/reset, 1 = start */
    uint32_t rate_fps;   /* encoded as rate_fps << 8 (feeder:460) */
    uint32_t width;      /* request hint; the accepted value is discovered */
    uint32_t comp_level; /* feeder uses 5 for 720p30 */
    uint32_t param_val;  /* feeder uses 300 */
    uint32_t timeout_ms; /* reply wait bound; never 0 for a start request */
    uint8_t  pin_no;     /* 0 = default (1); set 0xff for pin 0 (ISP LiveView) */
};

/* One GET_JPEG result. `size_upper_bound` is size_shift << 7: the original
 * handler reports (size+127)>>7, so the real JPEG length is within 127 bytes
 * below this value (E0144:21). Never treat it as the exact frame length. */
struct a6400_jpeg_result {
    uint32_t err;               /* reply payload +12; 0 = accepted */
    uint8_t  ready;             /* reply payload +16; 0 = no frame available */
    uint32_t handle1;           /* reply +20 */
    uint32_t handle2;           /* reply +24 (0 or 0xffff when absent) */
    uint32_t size_shift;        /* reply +22 */
    uint32_t size_upper_bound;  /* size_shift << 7 */
};

/* Sends the SET_PATH request and returns the original status int (0 = ok). */
int a6400_scalar_set_path(struct a6400_dmm *d, const struct a6400_scalar_path_cfg *cfg);

/* Sends the FuncCmdLiveViewMsg (0x51, liveViewOnOff) request and returns status (0 = ok). */
int a6400_scalar_set_liveview(struct a6400_dmm *d, int enable, uint32_t timeout_ms);

/* Read-only DataflowInfra GetPinInfo (0x1000/0x0000) query on target queue. */
int a6400_df_get_pin_info(struct a6400_uipc *u, uint32_t qid,
                          uint32_t pin_type, uint32_t pin_dir, uint32_t pin_no,
                          uint32_t out_words[8], uint32_t timeout_ms);

/* DataflowInfra PinConnect (0x1000/0x0001) on target queue (e.g. VDF 0x0059000e). */
int a6400_df_pin_connect(struct a6400_uipc *u, uint32_t qid,
                         const uint32_t src_pin[4], const uint32_t dst_pin[4],
                         uint32_t *out_status, uint32_t timeout_ms);

/* DataflowInfra PinDisconnect (0x1000/0x0002) on target queue. */
int a6400_df_pin_disconnect(struct a6400_uipc *u, uint32_t qid,
                            uint32_t pin_type, uint32_t pin_dir, uint32_t pin_no,
                            uint32_t *out_status, uint32_t timeout_ms);

/* Sends the ObjRemote 64-byte ScalarInfra mode command (0x2009/0x0001,
 * libObj.so:0x2fe0f8) directly to 0x01670464 (mode=0x20 starts JPEG streaming,
 * mode=0x01 returns to idle). */
int a6400_scalar_set_mode_0464(struct a6400_dmm *d, uint16_t mode,
                               uint32_t rate_fps, uint32_t width,
                               uint8_t comp_level, uint8_t pin_no,
                               uint32_t timeout_ms);

/* Sends a DataflowInfra 0x9010 frame-descriptor notification to ScalarInfra
 * (0x0167046a, matching av-cam 0x63a9f7f4). Caller MUST increment DMM refcounts
 * of both the descriptor and any inner YUV unit before calling. */
int a6400_scalar_push_yc(struct a6400_dmm *d, uint16_t handle,
                         uint32_t unit_type);

/* Polls one JPEG. Return values:
 *   0  frame available, result filled, ownership of handle1/handle2 handed to
 *      the caller (must close+free both, in that order)
 *   1  no frame available (err != 0 or ready == 0) - not an error
 *  <0  transport failure (send/recv); no ownership transferred
 */
int a6400_scalar_get_jpeg(struct a6400_dmm *d, uint32_t timeout_ms,
                          struct a6400_jpeg_result *out);

#endif /* A6400_OSAL_H */
