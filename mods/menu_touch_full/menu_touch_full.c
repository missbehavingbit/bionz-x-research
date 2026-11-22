/* Guarded full-menu touch prototype:
 * - Main menu (PAS_MenuListTab): tap tabs, page dots, rows (1st=select, 2nd=enter),
 *   plus horizontal swipe left/right for next/prev page.
 * - Popup menu (PAS_MenuListPopup): tap row to apply+close, vertical swipe to scroll,
 *   tap outside popup panel to cancel/close.
 * SMOD v1 build: linked into owned /libObj.so RX padding at 0x0700d708; state at 0x07143920.
 * SMOD v2 build (-DMT_SMOD_V2): position-independent; the state lives in the loader-allocated
 * bss after the code (__smod_bss, see tools/native_camera/mlv2/mod_v2.ld) and mt_life()
 * initializes it after the hooks are applied and clears it before they are reverted.
 */
#include <stdint.h>

#define MT_FULL_MAGIC 0x4d544632u /* "MTF2" */
#define MT_FULL_VERSION 2u
#ifndef MT_SMOD_V2
#define MT_SYSTEM_ADDR 0x956f0u
#define MT_STATE_ADDR 0x7143920u
#endif
#define MT_EVENT_CAP 16u

#define MT_ACT_NONE 0u
#define MT_ACT_TAB 1u
#define MT_ACT_PAGE 2u
#define MT_ACT_ROW_SELECT 3u
#define MT_ACT_ROW_ENTER 4u
#define MT_ACT_SWIPE_PAGE 5u
#define MT_ACT_POPUP_SELECT 6u
#define MT_ACT_POPUP_SCROLL 7u
#define MT_ACT_POPUP_CANCEL 8u

#ifndef MT_MAX_TABS
#define MT_MAX_TABS 6u /* Mod Loader build: -DMT_MAX_TABS=7u (injected Mods tab) */
#endif
#define MT_MAX_PAGES 15u
#define MT_MAX_ROWS 6u

struct mt_point {
    float x;
    float y;
};

struct mt_full_event {
    uint32_t seq;
    uint32_t tid;
    uint32_t action;
    uint32_t target;
    int32_t x;
    int32_t y;
    uint32_t tab;
    uint32_t page;
    uint32_t row;
};

struct mt_full_state {
    uint32_t magic;
    uint32_t version;
    uint32_t enabled;
    uint32_t count;
    uint32_t inflight;
    uint32_t capacity;
    uint32_t last_action;
    uint32_t last_target;
    int32_t last_x;
    int32_t last_y;
    uint32_t last_err;
    int32_t press_x;
    int32_t press_y;
    uint32_t press_valid;
    struct mt_full_event events[MT_EVENT_CAP];
};

#ifdef MT_SMOD_V2
extern uint8_t __smod_bss[] __attribute__((visibility("hidden")));
#define MT_STATE ((volatile struct mt_full_state *)__smod_bss)
_Static_assert(sizeof(struct mt_full_state) == 632u, "MTF2 state layout");
#else
#define MT_STATE ((volatile struct mt_full_state *)MT_STATE_ADDR)
#endif

extern uint32_t mt_original_release(void *, void *) __attribute__((visibility("hidden")));
extern uint32_t mt_original_press(void *, void *) __attribute__((visibility("hidden")));

static uint32_t rd32(uint32_t addr, unsigned off) {
    return *(const volatile uint32_t *)(addr + off);
}

static unsigned rd8(uint32_t addr, unsigned off) {
    return *(const volatile uint8_t *)(addr + off);
}

static inline uint32_t primary_widget_system(void) {
#ifdef MT_SMOD_V2
    /* This libObj accessor returns the live singleton, whose heap address is not relocatable. */
    uint32_t (*get_instance)(void) = (void *)0x06308d31u;
    return get_instance();
#else
    return MT_SYSTEM_ADDR;
#endif
}

static uint32_t get_tid(void) {
    register uint32_t r0 __asm__("r0") = 0;
    register uint32_t r7 __asm__("r7") = 224;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7) : "memory", "cc");
    return r0;
}

static int iabs(int v) {
    return v < 0 ? -v : v;
}

static void record_event(volatile struct mt_full_state *s, uint32_t action, uint32_t target,
                         int32_t x, int32_t y, uint32_t tab, uint32_t page, uint32_t row) {
    uint32_t idx = __sync_fetch_and_add(&s->count, 1u);
    s->last_action = action;
    s->last_target = target;
    s->last_x = x;
    s->last_y = y;
    if (idx < MT_EVENT_CAP) {
        volatile struct mt_full_event *e = &s->events[idx];
        e->tid = get_tid();
        e->action = action;
        e->target = target;
        e->x = x;
        e->y = y;
        e->tab = tab;
        e->page = page;
        e->row = row;
        __sync_synchronize();
        e->seq = idx + 1u;
    }
}

static void sync_refresh(uint32_t menu) {
    void (*binding)(uint32_t) = (void *)0x15b91431u;
    void (*refresh)(uint32_t, uint32_t) = (void *)0x15b92111u;
    binding(menu);
    refresh(menu, 0u);
}

static uint32_t find_setting_menu_view(uint32_t menu) {
    uint32_t vid = rd32(menu, 0x14cu);
    uint32_t vm = rd32(0x0713D7ECu, 0u);
    if (!vm) {
        return 0u;
    }
    int cnt = (int)rd32(vm, 0u);
    uint32_t entries = rd32(vm, 0x20u);
    if (!entries || cnt <= 0 || cnt > 128) {
        return 0u;
    }
    for (int i = 0; i < cnt; i++) {
        uint32_t ent = entries + (uint32_t)i * 16u;
        uint32_t vwrap = rd32(ent, 4u);
        if (!vwrap || rd32(ent, 8u) != vid) {
            continue;
        }
        if (rd32(vwrap, 0u) == 0x15EC8270u) {
            return vwrap;
        }
        uint32_t inner = rd32(vwrap, 0x18u);
        if (inner && rd32(inner, 0u) == 0x15EC8270u) {
            return inner;
        }
    }
    return 0u;
}

static void push_async_event(uint32_t event_id, uint8_t ev_type) {
    uint32_t mgr = rd32(rd32(0x07089984u, 0u), 0u);
    if (!mgr || !event_id) {
        return;
    }
    void *(*op_new)(uint32_t) = (void *)0x05E15974u;
    void (*ev_ctor)(void *, uint32_t, uint8_t, uint8_t) = (void *)0x05E14E20u;
    void (*evmgr_push)(uint32_t, void *, uint32_t) = (void *)0x05E18D5Du;
    void *ev = op_new(16u);
    if (!ev) {
        return;
    }
    ev_ctor(ev, event_id, ev_type, 1u);
    evmgr_push(mgr, ev, 1u);
}

static void handle_popup_touch(volatile struct mt_full_state *s, uint32_t menu,
                               int16_t x, int16_t y, int dx, int dy, int has_press) {
    uint32_t popup = menu + 0xfb20u;
    uint32_t pnode = rd32(popup, 0x6490u);
    if (!pnode || rd32(pnode, 0u) != 0x16c183e8u) {
        return;
    }

    int (*is_hit)(uint32_t, const struct mt_point *) = (void *)0x6305081u;
    void (*cancel_popup)(uint32_t) = (void *)0x15b9184du;
    void (*popup_up)(uint32_t) = (void *)0x15b9134du;
    void (*popup_down)(uint32_t) = (void *)0x15b91377u;
    void (*set_popup_row)(uint32_t, uint32_t) = (void *)0x15b96f11u;
    void (*confirm_popup)(uint32_t) = (void *)0x15b9638bu;
    void (*apply_popup)(uint32_t) = (void *)0x157f8ca5u;
    uint32_t view = find_setting_menu_view(menu);

    unsigned total = rd32(pnode, 8u);
    unsigned vis_cnt = rd8(popup, 0x6480u);
    if (total < 1u || total > 128u || vis_cnt < 1u || vis_cnt > MT_MAX_ROWS) {
        return;
    }

    if (has_press && iabs(dy) >= 36 && iabs(dy) > iabs(dx)) {
        uint32_t dir = dy < 0 ? 1u : 0u;
        if (dir) {
            popup_down(menu);
        } else {
            popup_up(menu);
        }
        record_event(s, MT_ACT_POPUP_SCROLL, dir, x, y, 0u, 0u, rd8(popup, 0x648du));
        return;
    }

    unsigned top = rd8(popup, 0x648cu);
    struct mt_point pt = {(float)x, (float)y};

    for (unsigned r = 0; r < vis_cnt; r++) {
        unsigned opt = top + r + 1u;
        if (opt > total) {
            break;
        }
        if (is_hit(popup + 0x1070u + r * 0x398u, &pt) ||
            is_hit(popup + 0x36a8u + r * 0x220u, &pt)) {
            set_popup_row(popup, r);
            *(volatile uint32_t *)(menu + 0x1fcu) = top + r;
            if (view) {
                apply_popup(view);
            } else {
                confirm_popup(popup);
            }
            record_event(s, MT_ACT_POPUP_SELECT, opt, x, y, view, top, r + 1u);
            return;
        }
    }

    if ((!is_hit(popup + 0x64cu, &pt) || x < 80 || x > 560 || y < 32 || y > 456) &&
        !is_hit(popup + 0x60d8u, &pt) && !is_hit(popup + 0x5c04u, &pt) &&
        !is_hit(popup + 0x2780u, &pt)) {
        if (view) {
            push_async_event(0x15010102u, 4u);
        } else {
            cancel_popup(menu);
        }
        record_event(s, MT_ACT_POPUP_CANCEL, 0u, x, y, view, 0u, rd8(popup, 0x648du));
    }
}

static void handle_menu_touch(volatile struct mt_full_state *s, uint32_t system, uint32_t mouse) {
    int16_t x = 0, y = 0;
    void (*get_pos)(uint32_t, unsigned, int16_t *, int16_t *) = (void *)0x6309019u;
    get_pos(system, mouse, &x, &y);

    int has_press = (s->press_valid != 0u);
    int dx = (int)x - s->press_x;
    int dy = (int)y - s->press_y;
    s->press_valid = 0u;

    if (x == 32767 || y == 32767) {
        return;
    }

    uint32_t err = ~0u;
    uint32_t (*lookup)(uint32_t, uint32_t, uint32_t, uint32_t *) = (void *)0x62e27dfu;
    uint32_t menu = lookup(0xaadca2c7u, 0xdfdc8dddu, 0xa056f604u, &err);
    if (err || !menu || rd32(menu, 0u) != 0x15f1f1d0u || rd32(menu, 0x1cu) != menu) {
        return;
    }

    int (*main_state)(uint32_t) = (void *)0x15b90535u;
    int (*inherited_mode)(uint32_t) = (void *)0x631e815u;
    int (*popup_open)(uint32_t) = (void *)0x15b95b61u;
    if (rd8(menu, 0x21u) != 2u || inherited_mode(menu) > 2) {
        return;
    }

    if (popup_open(menu + 0xfb20u)) {
        if (s->enabled >= 2u) {
            handle_popup_touch(s, menu, x, y, dx, dy, has_press);
        }
        return;
    }

    if (!main_state(menu)) {
        return;
    }

    if (s->enabled < 2u) {
        record_event(s, MT_ACT_NONE, 0u, x, y, menu, 0u, 0u);
        return;
    }

    uint32_t root = rd32(menu, 0x186acu);
    if (!root || rd32(root, 0u) != 0x16c183e8u) {
        return;
    }

    int (*selected_index)(uint32_t, unsigned *) = (void *)0x16925c17u;
    int (*indexed_child)(uint32_t, unsigned, uint32_t *) = (void *)0x16925bc7u;
    int (*set_selected_index)(uint32_t, unsigned) = (void *)0x169260dbu;
    int (*is_hit)(uint32_t, const struct mt_point *) = (void *)0x6305081u;
    int (*enter_selected)(uint32_t, uint32_t) = (void *)0x15b92045u;

    unsigned tabs = rd32(root, 8u), tab_sel = 0;
    if (tabs < 1u || tabs > MT_MAX_TABS ||
        selected_index(root, &tab_sel) != 1 || tab_sel < 1u || tab_sel > tabs) {
        return;
    }

    struct mt_point pt = {(float)x, (float)y};
    int is_swipe = has_press && iabs(dx) >= 48 && iabs(dx) > 2 * iabs(dy);

    /* Tab taps need only the real root: they work with tab-bar focus (menu+0x1d8) and in tabs whose
     * content is not a plain setting tree (My Menu, Mods). Rows/pages/swipe keep the full checks below. */
    if (!is_swipe) {
        for (unsigned i = 0; i < tabs; i++) {
            if (is_hit(menu + 0x0b9cu + i * 0x220u, &pt)) {
                if (i + 1u != tab_sel) {
                    if (set_selected_index(root, i + 1u) == 1) {
                        sync_refresh(menu);
                    } else {
                        s->last_err = 1u;
                    }
                }
                record_event(s, MT_ACT_TAB, i + 1u, x, y, tab_sel, 0u, 0u);
                return;
            }
        }
    }

    if (rd8(menu, 0x1d8u)) {
        return; /* tab-bar focus: only tab taps are handled */
    }

    uint32_t tab_node = 0;
    if (indexed_child(root, tab_sel, &tab_node) != 1 || !tab_node ||
        rd32(tab_node, 0u) != 0x16c183e8u) {
        return;
    }

    unsigned pages = rd32(tab_node, 8u), page_sel = 0;
    uint32_t page_node = 0;
    if (pages < 1u || pages > MT_MAX_PAGES ||
        selected_index(tab_node, &page_sel) != 1 || page_sel < 1u || page_sel > pages ||
        indexed_child(tab_node, page_sel, &page_node) != 1 || !page_node ||
        rd32(page_node, 0u) != 0x16c183e8u) {
        return;
    }

    unsigned rows = rd32(page_node, 8u), row_sel = 0;
    if (rows < 1u || rows > MT_MAX_ROWS ||
        selected_index(page_node, &row_sel) != 1 || row_sel < 1u || row_sel > rows ||
        rd32(menu, 0x1f8u) != row_sel - 1u) {
        return;
    }

    /* Horizontal swipe: left = next page/tab, right = prev page/tab */
    if (is_swipe) {
        if (dx < 0) {
            if (page_sel < pages) {
                set_selected_index(tab_node, page_sel + 1u);
            } else {
                set_selected_index(root, tab_sel < tabs ? tab_sel + 1u : 1u);
            }
        } else {
            if (page_sel > 1u) {
                set_selected_index(tab_node, page_sel - 1u);
            } else {
                set_selected_index(root, tab_sel > 1u ? tab_sel - 1u : tabs);
            }
        }
        sync_refresh(menu);
        record_event(s, MT_ACT_SWIPE_PAGE, dx < 0 ? 1u : 2u, x, y, tab_sel, page_sel, row_sel);
        return;
    }

    for (unsigned p = 0; p < pages; p++) {
        if (is_hit(menu + 0x166b4u + p * 0x220u, &pt)) {
            if (p + 1u != page_sel) {
                if (set_selected_index(tab_node, p + 1u) == 1) {
                    sync_refresh(menu);
                } else {
                    s->last_err = 2u;
                }
            }
            record_event(s, MT_ACT_PAGE, p + 1u, x, y, tab_sel, page_sel, row_sel);
            return;
        }
    }

    for (unsigned r = 0; r < rows; r++) {
        if (is_hit(menu + 0x9350u + r * 0x1118u, &pt)) {
            if (r + 1u != row_sel) {
                if (set_selected_index(page_node, r + 1u) == 1) {
                    sync_refresh(menu);
                } else {
                    s->last_err = 3u;
                }
                record_event(s, MT_ACT_ROW_SELECT, r + 1u, x, y, tab_sel, page_sel, row_sel);
            } else {
                uint32_t view = find_setting_menu_view(menu);
                if (view) {
                    push_async_event(0x15010052u, 4u);
                } else {
                    uint32_t leaf = 0;
                    if (indexed_child(page_node, r + 1u, &leaf) == 1 && leaf) {
                        enter_selected(menu, leaf);
                    } else {
                        s->last_err = 4u;
                    }
                }
                record_event(s, MT_ACT_ROW_ENTER, r + 1u, x, y, view, page_sel, row_sel);
            }
            return;
        }
    }
}

uint32_t mt_full_press(void *system, void *message) {
    volatile struct mt_full_state *s = MT_STATE;
    if (s->magic != MT_FULL_MAGIC || s->version != MT_FULL_VERSION) {
        return mt_original_press(system, message);
    }
    __sync_fetch_and_add(&s->inflight, 1u);
    uint32_t result = mt_original_press(system, message);
    if (s->enabled && system && message && (uint32_t)system == primary_widget_system()) {
        unsigned mouse = ((const uint8_t *)message)[0x14];
        if (mouse < 4u) {
            int16_t px = 0, py = 0;
            void (*get_pos)(uint32_t, unsigned, int16_t *, int16_t *) = (void *)0x6309019u;
            get_pos((uint32_t)system, mouse, &px, &py);
            if (px != 32767 && py != 32767) {
                s->press_x = px;
                s->press_y = py;
                s->press_valid = 1u;
            }
        }
    }
    __sync_fetch_and_sub(&s->inflight, 1u);
    return result;
}

uint32_t mt_full_release(void *system, void *message) {
    volatile struct mt_full_state *s = MT_STATE;
    if (s->magic != MT_FULL_MAGIC || s->version != MT_FULL_VERSION) {
        return mt_original_release(system, message);
    }
    __sync_fetch_and_add(&s->inflight, 1u);
    uint32_t result = mt_original_release(system, message);
    if (s->enabled && system && message && (uint32_t)system == primary_widget_system()) {
        unsigned mouse = ((const uint8_t *)message)[0x14];
        if (mouse < 4u) {
            handle_menu_touch(s, (uint32_t)system, mouse);
        } else {
            s->last_err = 99u;
        }
    }
    __sync_fetch_and_sub(&s->inflight, 1u);
    return result;
}

#ifdef MT_SMOD_V2
/* Mod Loader v2 lifecycle: on(1) runs after both hooks are applied, off(0) before they are
 * reverted. Until the magic is published (and after it is cleared) both hooks only forward to
 * the original functions. The mapping is never freed, so a thread still inside a hook after
 * OFF keeps executing valid code. */
void mt_life(int on) {
    volatile struct mt_full_state *s = MT_STATE;
    if (on) {
        s->magic = 0u;
        __sync_synchronize();
        s->version = MT_FULL_VERSION;
        s->capacity = MT_EVENT_CAP;
        s->press_valid = 0u;
        s->enabled = 2u;
        __sync_synchronize();
        s->magic = MT_FULL_MAGIC;
    } else {
        s->enabled = 0u;
        __sync_synchronize();
        s->magic = 0u;
    }
    __sync_synchronize();
}
#endif
