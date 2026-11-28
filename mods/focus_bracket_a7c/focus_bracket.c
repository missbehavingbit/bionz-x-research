/* Focus Bracketing SMOD v2 mod for Sony ILCE-7C (A7C)
 *
 * Uses:
 *   - ObjCamera MSGID_SYNC_STEP_FOCUS_CMD (0x3100, 0x6530) via MWF::ObjIf::IssueCommandSync
 *   - EventManager key push (0x15010032 / 0x15010031) for shutter S2 release
 *   - WriteStillComp hook (libObj.so + 0x902548) for save-complete synchronization
 */
#include <stdint.h>

#define LIBOBJ_REF_BASE 0x20000000u
#define LIBOBJ_ADDR(off) (LIBOBJ_REF_BASE + (off))

/* libObj.so function & data addresses (reference base 0x20000000, relocated via RLC1) */
#define FN_GET_OBJ_HANDLE   ((void *(*)(uint32_t))LIBOBJ_ADDR(0x0034d60du))
#define FN_OBJMSG_CTOR      ((void (*)(void *, uint32_t, uint32_t))LIBOBJ_ADDR(0x0010ab64u))
#define FN_OBJMSG_DTOR      ((void (*)(void *))LIBOBJ_ADDR(0x0010a340u))
#define FN_OBJMSG_ADDPARAM  ((void (*)(void *, uint32_t, const void *))LIBOBJ_ADDR(0x0010c950u))
#define FN_PARAM_INT_CTOR   ((void (*)(void *, int32_t))LIBOBJ_ADDR(0x0010b6d8u))
#define FN_PARAM_INT_DTOR   ((void (*)(void *))LIBOBJ_ADDR(0x00109c84u))
#define FN_ISSUE_CMD_SYNC   ((int (*)(void *, void *, void *))LIBOBJ_ADDR(0x0010b2a4u))
#define FN_PTHREAD_CREATE   ((int (*)(uint32_t *, void *, void *(*)(void *), void *))LIBOBJ_ADDR(0x0010be54u))
#define FN_USLEEP           ((int (*)(uint32_t))LIBOBJ_ADDR(0x0010f34cu))

#define PTR_G_PEVTMGR       ((void *volatile *)LIBOBJ_ADDR(0x01488ea0u))
#define FN_EVENT_NEW        ((void *(*)(uint32_t))LIBOBJ_ADDR(0x001084d0u))
#define FN_EVENT_CTOR       ((void (*)(void *, uint32_t, uint8_t, uint8_t))LIBOBJ_ADDR(0x00898b11u))
#define FN_EVENT_PUSH       ((void (*)(void *, void *, int))LIBOBJ_ADDR(0x0089a0bdu))

#define OBJID_CAMERA        8u
#define MSG_CAT_CAMERA      0x3100u
#define MSG_ID_STEP_FOCUS   0x6530u
#define PARAM_STEP_DIR      0x9c40u
#define VAL_STEP_NEAR       0x68cu
#define VAL_STEP_FAR        0x68du

#define KEY_S2_PRESS        0x15010032u
#define KEY_S2_RELEASE      0x15010031u
#define KEY_MENU_EVENT      0x15010102u

/* Writable state in .text (mod_v2.ld folds .data* into .text; SMOD v2 maps mods RWX). */
__attribute__((section(".data"), used)) uint8_t fb_cfg[8] = {0};
__attribute__((section(".data"), used)) volatile uint32_t fb_running = 0;
__attribute__((section(".data"), used)) volatile uint32_t fb_save_seq = 0;
__attribute__((section(".data"), used)) volatile uint32_t fb_shots_done = 0;
__attribute__((section(".data"), used)) volatile uint32_t fb_steps_done = 0;

/* SMNU writes selected indices into (__smod_bss + bss_cfg_off); copy on on()/cfg(). */
__attribute__((visibility("hidden"))) extern uint8_t __smod_bss[];

static void post_key_event(uint32_t ev_id) {
    void *mgr = *PTR_G_PEVTMGR;
    if (!mgr) return;
    void *ev = FN_EVENT_NEW(0x28u);
    if (!ev) return;
    FN_EVENT_CTOR(ev, ev_id, 4u, 1u);
    FN_EVENT_PUSH(mgr, ev, 1);
}

static int step_focus_once(int far) {
    void *cam = FN_GET_OBJ_HANDLE(OBJID_CAMERA);
    if (!cam) return -1;
    uint32_t cmd[4];
    uint32_t reply[4];
    uint32_t param[4];
    FN_OBJMSG_CTOR(cmd, MSG_CAT_CAMERA, MSG_ID_STEP_FOCUS);
    FN_OBJMSG_CTOR(reply, 0u, 0u);
    FN_PARAM_INT_CTOR(param, far ? (int32_t)VAL_STEP_FAR : (int32_t)VAL_STEP_NEAR);
    FN_OBJMSG_ADDPARAM(cmd, PARAM_STEP_DIR, param);
    FN_PARAM_INT_DTOR(param);
    int rc = FN_ISSUE_CMD_SYNC(cam, cmd, reply);
    FN_OBJMSG_DTOR(reply);
    FN_OBJMSG_DTOR(cmd);
    return rc;
}

static void step_focus_n(int far, uint32_t n) {
    for (uint32_t i = 0; i < n && fb_running; i++) {
        int rc = step_focus_once(far);
        fb_steps_done++;
        if (rc != 0) break;
        FN_USLEEP(40000u);
    }
}

static uint32_t map_step_width(uint32_t idx) {
    switch (idx) {
        case 0: return 1u;
        case 1: return 2u;
        case 2: return 4u;
        case 3: return 6u;
        case 4: return 8u;
        case 5: return 10u;
        default: return 4u;
    }
}

static uint32_t map_shots(uint32_t idx) {
    switch (idx) {
        case 0: return 5u;
        case 1: return 15u;
        case 2: return 35u;
        case 3: return 40u;
        case 4: return 60u;
        case 5: return 100u;
        default: return 35u;
    }
}

static uint32_t map_interval_ms(uint32_t idx) {
    switch (idx) {
        case 0: return 0u;
        case 1: return 1000u;
        case 2: return 2000u;
        case 3: return 3000u;
        case 4: return 5000u;
        case 5: return 10000u;
        default: return 0u;
    }
}

static void resolve_settings(uint32_t *step_w, uint32_t *shots, uint32_t *order,
                             uint32_t *exp_smooth, uint32_t *interval_ms) {
    uint32_t mode = fb_cfg[0];
    *exp_smooth = fb_cfg[4] ? 1u : 0u;
    if (mode == 0u) {
        *step_w = 8u;
        *shots = 5u;
        *order = 0u;
        *interval_ms = 0u;
    } else if (mode == 1u) {
        *step_w = 2u;
        *shots = 40u;
        *order = fb_cfg[3] ? 1u : 0u;
        *interval_ms = 1000u;
    } else {
        *step_w = map_step_width(fb_cfg[1]);
        *shots = map_shots(fb_cfg[2]);
        *order = fb_cfg[3] ? 1u : 0u;
        *interval_ms = map_interval_ms(fb_cfg[5]);
    }
}

static void *fb_worker(void *arg) {
    (void)arg;
    uint32_t step_w, shots, order, exp_smooth, interval_ms;
    resolve_settings(&step_w, &shots, &order, &exp_smooth, &interval_ms);

    post_key_event(KEY_MENU_EVENT);
    FN_USLEEP(800000u);

    for (uint32_t i = 0; i < shots && fb_running; i++) {
        uint32_t prev_seq = fb_save_seq;
        post_key_event(KEY_S2_PRESS);
        FN_USLEEP(80000u);
        post_key_event(KEY_S2_RELEASE);

        for (uint32_t w = 0; w < 30u && fb_running; w++) {
            if (fb_save_seq != prev_seq) break;
            FN_USLEEP(100000u);
        }
        fb_shots_done = i + 1u;

        if (i + 1u >= shots || !fb_running) break;

        if (interval_ms > 0u) {
            uint32_t chunks = interval_ms / 100u;
            for (uint32_t c = 0; c < chunks && fb_running; c++) {
                FN_USLEEP(100000u);
            }
        }
        if (!fb_running) break;

        if (order == 0u) {
            step_focus_n(1, step_w);
        } else {
            int far = (i & 1u) ? 1 : 0;
            step_focus_n(far, (i + 1u) * step_w);
        }
        (void)exp_smooth;
    }

    fb_running = 0u;
    return (void *)0;
}

__attribute__((used)) void fb_mark_saved(void) {
    fb_save_seq++;
}

__attribute__((naked, used)) void fb_write_still_hook(void) {
    __asm__ volatile (
        "push {r0, r1, r2, r3, lr}\n"
        "bl fb_mark_saved\n"
        "pop {r0, r1, r2, r3, lr}\n"
        "stmdb sp!, {r4, r5, r6, r7, r8, r9, sl, fp, lr}\n"
        "mov r5, r1\n"
        "ldr r1, 1f\n"
        "sub sp, #60\n"
        "mov r4, r0\n"
        "add r7, sp, #16\n"      /* 0x902556: replayed too (we resume at 0x902558) */
        "ldr pc, 2f\n"
        ".align 2\n"
        "1: .word %c0\n"
        "2: .word %c1\n"
        : : "i"(LIBOBJ_ADDR(0x0104b3c4u)), "i"(LIBOBJ_ADDR(0x00902558u | 1u))
    );
}

static void sync_cfg(void) {
    for (uint32_t i = 0; i < 6u; i++) {
        fb_cfg[i] = __smod_bss[i];
    }
}

__attribute__((used)) void fb_on(int on) {
    (void)on;
    sync_cfg();
    if (fb_running) return;
    fb_running = 1u;
    fb_shots_done = 0u;
    fb_steps_done = 0u;
    uint32_t tid = 0;
    FN_PTHREAD_CREATE(&tid, (void *)0, fb_worker, (void *)0);
}

__attribute__((used)) void fb_off(int on) {
    (void)on;
    fb_running = 0u;
}

__attribute__((used)) void fb_cfg_changed(int reason) {
    (void)reason;
    sync_cfg();
}
