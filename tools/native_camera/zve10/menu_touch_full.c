/* Guarded full-menu touch prototype — ZV-E10 port:
 * - Main menu (PAS_MenuListTab): tap tabs, page dots, rows (1st=select, 2nd=enter),
 *   plus horizontal swipe left/right for next/prev page.
 * - Popup menu (PAS_MenuListPopup): tap row to select+confirm, vertical swipe to scroll,
 *   tap outside popup panel to cancel/close.
 *
 * ZV-E10 (BIONZ X / CXD90045, CE Linux 8.2.7.6, FW 2.02 / 2.03).
 * Verified against on-device firmware dump symbols and disassembly.
 * RAM-only injection via ptrace into libObj.so RX zero padding (0x011964f0).
 * Dynamic base addresses resolved via state block in libObj.so .data (0x013f8000).
 */
#include <stdint.h>

#define MT_FULL_MAGIC 0x4d544632u /* "MTF2" */
#define MT_FULL_VERSION 2u

/* libObj.so relative offsets */
#define MT_SYSTEM_ADDR       0x0145cb1cu  /* WidgetSystem instance in .bss */
#define MT_OFFSET_GET_POS    0x006172cdu  /* WidgetSystem::getMousePosInFramebuffer */
#define MT_OFFSET_LOOKUP     0x005f05afu  /* WidgetLayouter::getWidget */
#define MT_OFFSET_IS_HIT     0x00613335u  /* Widget::isHit */
#define MT_OFFSET_INHERITED  0x00613bd1u  /* Widget::calcFinalMode */
#define MT_OFFSET_CREATE_EVT 0x0012c651u  /* Event factory: create_event(ulong id, u8 type) */
#define MT_OFFSET_PUSH_EVT   0x0012c62du  /* Event dispatcher: push_event(Event* ev) */
#define MT_OFFSET_VIEW_MGR_PTR 0x0149221cu /* pViewManager in libObj.so .bss */
#define MT_OFFSET_VIEW_MGR_GOT 0x013dc7b4u /* GOT entry for pViewManager in libObj.so */
#define MT_DEFAULT_VIEW4_BASE  0x2c300000u /* viewUnified4.so base address on ZV-E10 */

/* viewUnified2.so relative offsets */
#define MT_OFFSET_VTV_PAS_TAB 0x00c09e28u /* PAS_MenuListTab vtable addr pt (verified live: 0x168d8e28 - 0x15ccf000) */
#define MT_OFFSET_ENTER       0x0086292du /* PAS_MenuListTab::openLevel2Popup */
#define MT_OFFSET_POPUP_PREV  0x00861b4fu /* PAS_MenuListTab::setPopupPrev */
#define MT_OFFSET_POPUP_NEXT  0x00861b79u /* PAS_MenuListTab::setPopupNext */
#define MT_OFFSET_REFRESH     0x008629f9u /* PAS_MenuListTab::refresh */
#define MT_OFFSET_CLOSE_POPUP 0x008620e1u /* PAS_MenuListTab::closeMenuPopup (closes popup, restores main_state=2, refreshes menu) */
#define MT_OFFSET_VTV_SETTING_MENU 0x00ba2c8cu /* ViewSettingMenu primary vtable addr pt (viewUnified2.so) */
#define MT_OFFSET_ACTION_DISPATCH  0x0030412bu /* ViewSettingMenu::ProductAction(this, action_id) */
#define MT_OFFSET_FN_EXISTENCE     0x0059140du /* CmnForUseMultipleViewStatus::getFnMenuExistenceStatus */

/* viewUnified4.so relative offsets */
#define MT_OFFSET_VTV_VIEW_FN      0x00317bd8u /* ViewFnMenu primary vtable offset */
#define MT_OFFSET_VTV_VIEW_FN_SEC  0x00317d84u /* ViewFnMenu secondary vtable offset */
#define MT_OFFSET_FN_SET_CURSOR    0x00227bccu /* ViewFnMenu::setCursorFocus (Thumb +1) */
#define MT_OFFSET_FN_NOTIFY        0x0022a0ceu /* ViewFnMenu::notifyLayout (Thumb +1) */
#define MT_OFFSET_FN_UPDATE_TILE   0x0022c438u /* ViewFnMenu::updateTileVisual (Thumb +1) */
#define MT_OFFSET_FN_ACTION        0x0023249eu /* ViewFnMenu::ProductAction (Thumb +1) */
#define MT_OFFSET_FN_SHOW_CURSOR   0x002284b4u /* ViewFnMenu::showCursor (Thumb +1) */
#define MT_OFFSET_FN_UPDATE_SEL    0x00227af4u /* ViewFnMenu::updateSelection (Thumb +1) */

/* Key event codes (category 0x1501: 4=Center/Enter, 5=Up, 6=Down, 7=Left, 8=Right, F=Fn) */
#define MT_EVT_KEY_ENTER           0x15010042u /* Physical Center button (ENTER/DECIDE) */
#define MT_EVT_KEY_UP              0x15010052u /* Physical Up button */
#define MT_EVT_KEY_DOWN            0x15010062u /* Physical Down button */
#define MT_EVT_KEY_LEFT            0x15010072u /* Physical Left button */
#define MT_EVT_KEY_RIGHT           0x15010082u /* Physical Right button */
#define MT_EVT_KEY_FN              0x150100f2u /* Physical Fn key */

/* CautionConfig.so relative offsets */
#define MT_OFFSET_VTV_NODE    0x00fab968u /* CmnViewSettingNode vtable addr pt */
#define MT_OFFSET_SEL_INDEX   0x00b8e127u /* CmnViewSettingNode::getIndexSelectedItem */
#define MT_OFFSET_IDX_CHILD   0x00b8e0cbu /* CmnViewSettingNode::getSubItemByIndex */
#define MT_OFFSET_SET_INDEX   0x00b8e5ebu /* CmnViewSettingNode::setItemSelectedByIndex */

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
#define MT_ACT_FN_SELECT    10u
#define MT_ACT_FN_ENTER     11u
#define MT_ACT_FN_CANCEL    12u

#ifndef MT_MAX_TABS
#define MT_MAX_TABS 6u
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
    uint32_t lib_base;
    uint32_t view_base;
    uint32_t caution_base;
    uint32_t cached_menu;
    uint32_t view4_base;
    uint32_t cached_fn_view;
    struct mt_full_event events[MT_EVENT_CAP];
};

extern uint32_t mt_original_release(void *, void *) __attribute__((visibility("hidden")));
extern uint32_t mt_original_press(void *, void *) __attribute__((visibility("hidden")));
extern volatile struct mt_full_state *mt_get_state_ptr(void) __attribute__((visibility("hidden")));

static inline volatile struct mt_full_state *get_state(void) {
    return mt_get_state_ptr();
}

static inline uint32_t rd32(uint32_t addr, unsigned off) {
    return *(const volatile uint32_t *)(addr + off);
}

static inline unsigned rd8(uint32_t addr, unsigned off) {
    return *(const volatile uint8_t *)(addr + off);
}

static inline void wr32(uint32_t addr, unsigned off, uint32_t val) {
    *(volatile uint32_t *)(addr + off) = val;
}

static inline void wr8(uint32_t addr, unsigned off, uint8_t val) {
    *(volatile uint8_t *)(addr + off) = val;
}

static uint32_t get_tid(void) {
    register uint32_t r0 __asm__("r0") = 0;
    register uint32_t r7 __asm__("r7") = 224;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7) : "memory", "cc");
    return r0;
}

static inline int iabs(int v) {
    return v < 0 ? -v : v;
}

static inline int is_valid_ptr(uint32_t p) {
    return p >= 0x00010000u && p < 0xc0000000u && (p & 3u) == 0u;
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

static void sync_refresh(volatile struct mt_full_state *s, uint32_t menu) {
    void (*refresh)(uint32_t, uint32_t) = (void *)(s->view_base + MT_OFFSET_REFRESH);
    refresh(menu, 0u);
}

static void push_async_event(volatile struct mt_full_state *s, uint32_t event_id, uint8_t ev_type) {
    if (!event_id) {
        return;
    }
    void *(*create_evt)(uint32_t, uint8_t) = (void *)(s->lib_base + MT_OFFSET_CREATE_EVT);
    void (*push_evt)(void *) = (void *)(s->lib_base + MT_OFFSET_PUSH_EVT);
    void *ev = create_evt(event_id, ev_type);
    if (!ev) {
        return;
    }
    push_evt(ev);
}

static uint32_t find_setting_menu_view(volatile struct mt_full_state *s, uint32_t menu) {
    uint32_t vid = rd32(menu, 0x14cu);
    uint32_t vm = rd32(s->lib_base + MT_OFFSET_VIEW_MGR_PTR, 0u);
    if (!is_valid_ptr(vm)) {
        uint32_t got_vm = rd32(s->lib_base + MT_OFFSET_VIEW_MGR_GOT, 0u);
        if (is_valid_ptr(got_vm)) vm = rd32(got_vm, 0u);
    }
    if (!is_valid_ptr(vm)) {
        return 0u;
    }
    int cnt = (int)rd32(vm, 0u);
    if (cnt <= 0 || cnt > 128) {
        return 0u;
    }
    uint32_t vtv = s->view_base + MT_OFFSET_VTV_SETTING_MENU;

    /* Path 1: Check views pointer array at [vm + 4] */
    uint32_t views_arr = rd32(vm, 4u);
    if (is_valid_ptr(views_arr)) {
        for (int i = 0; i < cnt; i++) {
            uint32_t vwrap = rd32(views_arr, (uint32_t)i * 4u);
            if (is_valid_ptr(vwrap)) {
                if (rd32(vwrap, 0u) == vtv) {
                    return vwrap;
                }
                uint32_t inner = rd32(vwrap, 0x18u);
                if (is_valid_ptr(inner) && rd32(inner, 0u) == vtv) {
                    return inner;
                }
            }
        }
    }

    /* Path 2: Check entries table at [vm + 0x20] */
    uint32_t entries = rd32(vm, 0x20u);
    if (is_valid_ptr(entries)) {
        for (int i = 0; i < cnt; i++) {
            uint32_t ent = entries + (uint32_t)i * 16u;
            uint32_t vwrap = rd32(ent, 4u);
            if (!is_valid_ptr(vwrap) || (vid && rd32(ent, 8u) != vid)) {
                continue;
            }
            if (rd32(vwrap, 0u) == vtv) {
                return vwrap;
            }
            uint32_t inner = rd32(vwrap, 0x18u);
            if (is_valid_ptr(inner) && rd32(inner, 0u) == vtv) {
                return inner;
            }
            /* Fallback: if vid matched, vwrap is our owning view instance */
            return vwrap;
        }
    }
    return 0u;
}

static void handle_popup_touch(volatile struct mt_full_state *s, uint32_t menu,
                               int16_t x, int16_t y, int dx, int dy, int has_press) {
    uint32_t lib = s->lib_base;
    uint32_t view = s->view_base;
    uint32_t caution = s->caution_base;

    uint32_t popup = menu + 0xfb20u;
    uint32_t pnode = rd32(popup, 0x6490u);
    if (!pnode) {
        s->last_err = 201u; /* popup node null */
        return;
    }
    uint32_t pvtv = rd32(pnode, 0u);
    /* In C++, setting items use derived vtables located in CautionConfig.so (.data.rel.ro / .data) */
    if (pvtv < caution || pvtv >= (caution + 0x01220000u)) {
        s->last_err = 202u; /* popup node vtable mismatch */
        s->last_target = pvtv;
        return;
    }

    int (*is_hit)(uint32_t, const struct mt_point *) = (void *)(lib + MT_OFFSET_IS_HIT);
    void (*close_popup)(uint32_t) = (void *)(view + MT_OFFSET_CLOSE_POPUP);
    void (*popup_up)(uint32_t) = (void *)(view + MT_OFFSET_POPUP_PREV);
    void (*popup_down)(uint32_t) = (void *)(view + MT_OFFSET_POPUP_NEXT);

    unsigned total = rd32(pnode, 8u);
    unsigned vis_cnt = rd8(popup, 0x6480u);
    if (total < 1u || total > 128u || vis_cnt < 1u || vis_cnt > MT_MAX_ROWS) {
        s->last_err = 203u; /* popup counts out of range */
        s->last_target = (total << 16) | vis_cnt;
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

    int (*set_node_index)(uint32_t, unsigned) = (void *)(caution + MT_OFFSET_SET_INDEX);
    unsigned top = rd8(popup, 0x648cu);
    struct mt_point pt = {(float)x, (float)y};

    for (unsigned r = 0; r < vis_cnt; r++) {
        unsigned opt = top + r + 1u;
        if (opt > total) {
            break;
        }
        if (is_hit(popup + 0x1070u + r * 0x398u, &pt) ||
            is_hit(popup + 0x36a8u + r * 0x220u, &pt)) {
            *(volatile uint32_t *)(menu + 0x1fcu) = top + r;
            set_node_index(pnode, opt);
            close_popup(menu);
            record_event(s, MT_ACT_POPUP_SELECT, opt, x, y, 0u, top, r + 1u);
            return;
        }
    }

    if ((!is_hit(popup + 0x64cu, &pt) || x < 80 || x > 560 || y < 32 || y > 456) &&
        !is_hit(popup + 0x60d8u, &pt) && !is_hit(popup + 0x5c04u, &pt) &&
        !is_hit(popup + 0x2780u, &pt)) {
        close_popup(menu);
        record_event(s, MT_ACT_POPUP_CANCEL, 0u, x, y, 0u, 0u, rd8(popup, 0x648du));
        return;
    }

    s->last_err = 204u; /* popup touch missed hit targets */
    s->last_target = ((uint32_t)(uint16_t)x << 16) | (uint16_t)y;
}

static void handle_menu_touch(volatile struct mt_full_state *s, uint32_t mouse) {
    uint32_t lib = s->lib_base;
    uint32_t view = s->view_base;
    uint32_t caution = s->caution_base;

    int16_t x = 0, y = 0;
    void (*get_pos)(uint32_t, unsigned, int16_t *, int16_t *) = (void *)(lib + MT_OFFSET_GET_POS);
    get_pos(lib + MT_SYSTEM_ADDR, mouse, &x, &y);

    int has_press = (s->press_valid != 0u);
    int dx = (int)x - s->press_x;
    int dy = (int)y - s->press_y;
    s->press_valid = 0u;

    if (x == 32767 || y == 32767) {
        s->last_err = 99u; /* invalid coordinate */
        return;
    }

    s->last_x = x;
    s->last_y = y;
    s->last_action = 100u; /* reached handle_menu_touch */

    uint32_t expected_vtv = view + MT_OFFSET_VTV_PAS_TAB;
    uint32_t err = ~0u;
    uint32_t (*lookup)(uint32_t, uint32_t, uint32_t, uint32_t *) = (void *)(lib + MT_OFFSET_LOOKUP);
    uint32_t menu = lookup(0xaadca2c7u, 0xdfdc8dddu, 0xa056f604u, &err);
    if (!err && menu) {
        s->cached_menu = menu;
    } else {
        /* If lookup failed because popup/modal dialog is active, fallback to cached menu */
        uint32_t cached = s->cached_menu;
        if (is_valid_ptr(cached) && rd32(cached, 0u) == expected_vtv && rd32(cached, 0x1cu) == cached) {
            menu = cached;
            err = 0;
        } else {
            s->last_err = 101u; /* lookup failed */
            s->last_target = err;
            return;
        }
    }
    s->last_target = menu;
    uint32_t menu_vtv = rd32(menu, 0u);
    if (menu_vtv != expected_vtv) {
        s->last_err = 103u; /* menu vtable mismatch */
        s->last_target = menu_vtv;
        return;
    }
    if (rd32(menu, 0x1cu) != menu) {
        s->last_err = 104u; /* self pointer mismatch */
        s->last_target = rd32(menu, 0x1cu);
        return;
    }

    int (*inherited_mode)(uint32_t) = (void *)(lib + MT_OFFSET_INHERITED);
    uint8_t m21 = rd8(menu, 0x21u);
    int inh = inherited_mode(menu);
    if (m21 != 2u) {
        s->last_err = 105u; /* mode byte != 2 */
        s->last_target = (uint32_t)m21;
        return;
    }
    if (inh > 2) {
        s->last_err = 106u; /* inherited_mode > 2 */
        s->last_target = (uint32_t)inh;
        return;
    }

    uint32_t m_state = rd32(menu, 0x1e0u);
    uint8_t popup_open_byte = rd8(menu + 0xfb20u, 0x648eu);
    if (m_state == 3u || popup_open_byte != 0u) { /* popup_open */
        s->last_action = 110u; /* in popup */
        if (s->enabled >= 2u) {
            handle_popup_touch(s, menu, x, y, dx, dy, has_press);
        }
        return;
    }

    if (m_state != 2u) {
        s->last_err = 107u; /* main_state != 2 */
        s->last_target = m_state;
        return;
    }

    if (s->enabled < 2u) {
        record_event(s, MT_ACT_NONE, 0u, x, y, menu, 0u, 0u);
        return;
    }

    uint32_t root = rd32(menu, 0x186acu);
    if (!root) {
        s->last_err = 108u; /* root node null */
        return;
    }
    uint32_t root_vtv = rd32(root, 0u);
    uint32_t expected_node = caution + MT_OFFSET_VTV_NODE;
    if (root_vtv != expected_node) {
        s->last_err = 109u; /* root node vtable mismatch */
        s->last_target = root_vtv;
        return;
    }

    int (*selected_index)(uint32_t, unsigned *) = (void *)(caution + MT_OFFSET_SEL_INDEX);
    int (*indexed_child)(uint32_t, unsigned, uint32_t *) = (void *)(caution + MT_OFFSET_IDX_CHILD);
    int (*set_selected_index)(uint32_t, unsigned) = (void *)(caution + MT_OFFSET_SET_INDEX);
    int (*is_hit)(uint32_t, const struct mt_point *) = (void *)(lib + MT_OFFSET_IS_HIT);
    int (*enter_selected)(uint32_t, uint32_t) = (void *)(view + MT_OFFSET_ENTER);

    unsigned tabs = rd32(root, 8u), tab_sel = 0;
    if (tabs < 1u || tabs > MT_MAX_TABS) {
        s->last_err = 112u; /* tabs count out of range */
        s->last_target = tabs;
        return;
    }
    if (selected_index(root, &tab_sel) != 1 || tab_sel < 1u || tab_sel > tabs) {
        s->last_err = 113u; /* tab_sel out of range */
        s->last_target = tab_sel;
        return;
    }

    s->last_action = 120u; /* reached hit tests */

    struct mt_point pt = {(float)x, (float)y};
    int is_swipe = has_press && iabs(dx) >= 48 && iabs(dx) > 2 * iabs(dy);

    uint32_t sm_view = find_setting_menu_view(s, menu);

    if (!is_swipe) {
        for (unsigned i = 0; i < tabs; i++) {
            if (is_hit(menu + 0x0b9cu + i * 0x220u, &pt)) {
                if (i + 1u != tab_sel) {
                    if (set_selected_index(root, i + 1u) == 1) {
                        sync_refresh(s, menu);
                    } else {
                        s->last_err = 1u;
                    }
                }
                record_event(s, MT_ACT_TAB, i + 1u, x, y, tab_sel, 0u, 0u);
                return;
            }
        }
    }

    uint32_t tab_node = 0;
    if (indexed_child(root, tab_sel, &tab_node) != 1 || !tab_node ||
        rd32(tab_node, 0u) != (caution + MT_OFFSET_VTV_NODE)) {
        s->last_err = 115u; /* tab_node invalid */
        return;
    }

    unsigned pages = rd32(tab_node, 8u), page_sel = 0;
    uint32_t page_node = 0;
    if (pages < 1u || pages > MT_MAX_PAGES ||
        selected_index(tab_node, &page_sel) != 1 || page_sel < 1u || page_sel > pages ||
        indexed_child(tab_node, page_sel, &page_node) != 1 || !page_node ||
        rd32(page_node, 0u) != (caution + MT_OFFSET_VTV_NODE)) {
        s->last_err = 116u; /* page_node invalid */
        return;
    }

    unsigned rows = rd32(page_node, 8u), row_sel = 0;
    if (rows > MT_MAX_ROWS) {
        s->last_err = 117u; /* rows count invalid */
        return;
    }
    if (selected_index(page_node, &row_sel) != 1 || row_sel < 1u || row_sel > rows) {
        row_sel = 0u; /* no row currently highlighted/selected */
    }
    if (row_sel > 0u && rd32(menu, 0x1f8u) != row_sel - 1u) {
        *(volatile uint32_t *)(menu + 0x1f8u) = row_sel - 1u;
    }

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
        sync_refresh(s, menu);
        record_event(s, MT_ACT_SWIPE_PAGE, dx < 0 ? 1u : 2u, x, y, tab_sel, page_sel, row_sel);
        return;
    }

    for (unsigned p = 0; p < pages; p++) {
        if (is_hit(menu + 0x166b4u + p * 0x220u, &pt)) {
            if (p + 1u != page_sel) {
                if (set_selected_index(tab_node, p + 1u) == 1) {
                    sync_refresh(s, menu);
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
                    *(volatile uint32_t *)(menu + 0x1f8u) = r;
                    *(volatile uint32_t *)(menu + 0x1fcu) = r;
                    *(volatile uint8_t *)(menu + 0x1d8u) = 0;
                    if (sm_view) {
                        *(volatile uint32_t *)(sm_view + 0x164u) = 2u;
                        *(volatile uint32_t *)(sm_view + 0x174u) = r;
                    }
                    sync_refresh(s, menu);
                } else {
                    s->last_err = 3u;
                }
                record_event(s, MT_ACT_ROW_SELECT, r + 1u, x, y, tab_sel, page_sel, row_sel);
            } else {
                uint32_t leaf = 0;
                int is_popup = 0;
                if (indexed_child(page_node, r + 1u, &leaf) == 1 && leaf) {
                    int (*get_selectable)(uint32_t, int *) = (void *)rd32(rd32(leaf, 0u), 0x10u);
                    int sel_count = 0;
                    if (get_selectable && get_selectable(leaf, &sel_count) == 1 && sel_count > 0) {
                        is_popup = 1;
                    }
                }
                set_selected_index(page_node, r + 1u);
                *(volatile uint32_t *)(menu + 0x1f8u) = r;
                *(volatile uint32_t *)(menu + 0x1fcu) = r;
                *(volatile uint8_t *)(menu + 0x1d8u) = 0;
                if (sm_view) {
                    *(volatile uint32_t *)(sm_view + 0x164u) = 2u;
                    *(volatile uint32_t *)(sm_view + 0x174u) = r;
                }

                if (is_popup) {
                    enter_selected(menu, leaf);
                } else {
                    if (sm_view) {
                        s->last_err = 110u; /* Action 11 dispatched */
                        void (*action)(uint32_t, int) = (void *)(s->view_base + MT_OFFSET_ACTION_DISPATCH);
                        action(sm_view, 11);
                    } else {
                        s->last_err = 111u; /* sm_view not found */
                        push_async_event(s, MT_EVT_KEY_ENTER, 4u);
                        push_async_event(s, MT_EVT_KEY_ENTER, 2u);
                    }
                }
                record_event(s, MT_ACT_ROW_ENTER, r + 1u, x, y, (uint32_t)is_popup, page_sel, row_sel);
            }
            return;
        }
    }

    s->last_err = 199u; /* no widget hit at coordinates */
}

static void push_key_click(volatile struct mt_full_state *s, uint32_t key) {
    push_async_event(s, key, 4u); /* Key down */
    push_async_event(s, key, 2u); /* Key up */
}

static inline uint32_t get_heap_end(void) {
    register uint32_t r0 asm("r0") = 0;
    register uint32_t r7 asm("r7") = 45; /* __NR_brk */
    asm volatile("svc 0" : "=r"(r0) : "r"(r0), "r"(r7) : "memory");
    return r0;
}

static inline uint32_t get_v4_base(volatile struct mt_full_state *s) {
    if (s->view4_base && s->view4_base != 0x2c400000u && s->view4_base != 0x2c100000u) {
        return s->view4_base;
    }
    return MT_DEFAULT_VIEW4_BASE;
}

static uint32_t find_fn_menu_view(volatile struct mt_full_state *s) {
    uint32_t v4_base = get_v4_base(s);
    uint32_t vtv = v4_base + MT_OFFSET_VTV_VIEW_FN;
    uint32_t sec_vtv = v4_base + MT_OFFSET_VTV_VIEW_FN_SEC;

    /* 1. Fast path: check cached instance */
    uint32_t cached = s->cached_fn_view;
    if (is_valid_ptr(cached) && rd32(cached, 0u) == vtv && rd32(cached, 0x28u) == sec_vtv) {
        return cached;
    }
    s->cached_fn_view = 0u;

    /* 2. ViewManager lookup (ViewFnMenu inherits from ViewBase and is registered in ViewManager) */
    uint32_t vm = rd32(s->lib_base + MT_OFFSET_VIEW_MGR_PTR, 0u);
    if (!is_valid_ptr(vm)) {
        uint32_t got_vm = rd32(s->lib_base + MT_OFFSET_VIEW_MGR_GOT, 0u);
        if (is_valid_ptr(got_vm)) vm = rd32(got_vm, 0u);
    }
    if (is_valid_ptr(vm)) {
        int cnt = (int)rd32(vm, 0u);
        if (cnt > 0 && cnt <= 128) {
            uint32_t views_arr = rd32(vm, 4u);
            if (is_valid_ptr(views_arr)) {
                for (int i = 0; i < cnt; i++) {
                    uint32_t vwrap = rd32(views_arr, (uint32_t)i * 4u);
                    if (is_valid_ptr(vwrap)) {
                        if (rd32(vwrap, 0u) == vtv && rd32(vwrap, 0x28u) == sec_vtv) {
                            s->cached_fn_view = vwrap;
                            return vwrap;
                        }
                        uint32_t inner = rd32(vwrap, 0x18u);
                        if (is_valid_ptr(inner) && rd32(inner, 0u) == vtv && rd32(inner, 0x28u) == sec_vtv) {
                            s->cached_fn_view = inner;
                            return inner;
                        }
                    }
                }
            }
        }
    }

    /* 3. Scan heap downwards from heap top to find active ViewFnMenu */
    uint32_t heap_top = get_heap_end();
    if (heap_top < 0x00020000u || heap_top > 0x01000000u) {
        heap_top = 0x00450000u;
    }
    heap_top = (heap_top - 4u) & ~3u;

    for (uint32_t p = heap_top; p >= 0x00016000u; p -= 4u) {
        if (rd32(p, 0u) == vtv && rd32(p, 0x28u) == sec_vtv) {
            uint32_t idx = rd32(p, 0x17cu);
            if (idx <= 11u) {
                s->cached_fn_view = p;
                return p;
            }
        }
    }

    return 0u;
}

static inline void step_fn_action(uint32_t view_fn, int action, void (*fn_action)(uint32_t, int)) {
    /* Ensure focus mode is active and clear inhibit/busy flags on every step */
    wr8(view_fn, 0x221u, 1u);
    wr8(view_fn, 0x3b4u, 0u);
    wr8(view_fn, 0x230u, 0u);
    wr8(view_fn, 0x231u, 0u);
    fn_action(view_fn, action);
}

static void handle_fn_touch(volatile struct mt_full_state *s, uint32_t mouse) {
    uint32_t lib = s->lib_base;
    int16_t x = 0, y = 0;
    void (*get_pos)(uint32_t, unsigned, int16_t *, int16_t *) = (void *)(lib + MT_OFFSET_GET_POS);
    get_pos(lib + MT_SYSTEM_ADDR, mouse, &x, &y);

    if (x == 32767 || y == 32767) {
        s->last_err = 99u;
        s->press_valid = 0u;
        return;
    }

    /* Stabilize coordinates with press coords if within tap tolerance */
    if (s->press_valid) {
        int dx = (int)x - (int)s->press_x;
        int dy = (int)y - (int)s->press_y;
        if (dx >= -25 && dx <= 25 && dy >= -25 && dy <= 25) {
            x = s->press_x;
            y = s->press_y;
        }
    }
    s->press_valid = 0u;

    s->last_x = x;
    s->last_y = y;

    /* 1. Tap outside Fn menu grid (upper screen area: y < 260) -> CANCEL / DISMISS Fn menu or submenu */
    if (y < 260) {
        push_key_click(s, MT_EVT_KEY_FN);
        record_event(s, MT_ACT_FN_CANCEL, 0u, x, y, 0u, 0u, 0u);
        s->last_action = MT_ACT_FN_CANCEL;
        s->last_err = 0u;
        return;
    }

    /* 2. Locate ViewFnMenu instance */
    uint32_t view_fn = find_fn_menu_view(s);
    if (!view_fn) {
        s->last_err = 301u; /* ViewFnMenu not found */
        return;
    }

    /* 3. Compute target tile index (2 rows of 6 tiles)
     * Calibrated 6-column widths to ensure full LCD edge coverage:
     * Col 0: < 110 (extreme left edge down to 0)
     * Col 1: < 215
     * Col 2: < 320
     * Col 3: < 425
     * Col 4: < 530
     * Col 5: >= 530 (extreme right edge up to 640+)
     */
    int col;
    if (x < 110) {
        col = 0;
    } else if (x < 215) {
        col = 1;
    } else if (x < 320) {
        col = 2;
    } else if (x < 425) {
        col = 3;
    } else if (x < 530) {
        col = 4;
    } else {
        col = 5;
    }

    int row = (y >= 370) ? 1 : 0;
    uint32_t target_idx = (uint32_t)(row * 6 + col);
    if (target_idx > 11u) target_idx = 11u;

    uint32_t cur_idx = rd32(view_fn, 0x17cu);
    if (cur_idx > 11u) cur_idx = 0u;

    uint32_t v4_base = get_v4_base(s);
    void (*fn_action)(uint32_t, int) = (void *)(v4_base + MT_OFFSET_FN_ACTION + 1u);

    uint8_t focus_active = (uint8_t)rd8(view_fn, 0x221u);

    if (focus_active && cur_idx == target_idx) {
        /* Second tap on same tile: open setting dialogue */
        step_fn_action(view_fn, 4, fn_action); /* Action 4 = DECIDE / ENTER */
        record_event(s, MT_ACT_FN_ENTER, target_idx, x, y, cur_idx, target_idx, 0u);
        s->last_action = MT_ACT_FN_ENTER;
        s->last_target = target_idx;
        s->last_err = 0u;
        return;
    }

    /* If focus is not yet active (menu just opened without D-pad movement), activate it */
    if (!focus_active) {
        wr8(view_fn, 0x221u, 1u);
        wr8(view_fn, 0x3b4u, 0u);
        void (*show_cursor)(uint32_t) = (void *)(v4_base + MT_OFFSET_FN_SHOW_CURSOR + 1u);
        show_cursor(view_fn);
        void (*update_visual)(uint32_t) = (void *)(v4_base + MT_OFFSET_FN_UPDATE_TILE + 1u);
        update_visual(view_fn);
    }

    /* Ring navigation: shortest circular path on 12-tile continuous ring */
    int diff = ((int)target_idx - (int)cur_idx + 12) % 12;
    if (diff <= 6) {
        for (int k = 0; k < diff; k++) {
            step_fn_action(view_fn, 1, fn_action); /* Action 1 = RIGHT (+1) */
        }
    } else {
        int steps = 12 - diff;
        for (int k = 0; k < steps; k++) {
            step_fn_action(view_fn, 0, fn_action); /* Action 0 = LEFT (-1) */
        }
    }

    uint32_t new_idx = rd32(view_fn, 0x17cu);
    s->last_target = new_idx;
    s->last_action = MT_ACT_FN_SELECT;
    record_event(s, MT_ACT_FN_SELECT, new_idx, x, y, cur_idx, target_idx, 0u);
    s->last_err = 0u;
}

uint32_t mt_full_press(void *system, void *message) {
    volatile struct mt_full_state *s = get_state();
    if (s->magic != MT_FULL_MAGIC || s->version != MT_FULL_VERSION) {
        return mt_original_press(system, message);
    }
    __sync_fetch_and_add(&s->inflight, 1u);
    uint32_t result = mt_original_press(system, message);
    if (s->enabled && message) {
        unsigned mouse = ((const uint8_t *)message)[0x14];
        if (mouse < 4u) {
            int16_t px = 0, py = 0;
            void (*get_pos)(uint32_t, unsigned, int16_t *, int16_t *) = (void *)(s->lib_base + MT_OFFSET_GET_POS);
            get_pos(s->lib_base + MT_SYSTEM_ADDR, mouse, &px, &py);
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
    volatile struct mt_full_state *s = get_state();
    if (s->magic != MT_FULL_MAGIC || s->version != MT_FULL_VERSION) {
        return mt_original_release(system, message);
    }
    __sync_fetch_and_add(&s->inflight, 1u);
    uint32_t result = mt_original_release(system, message);
    if (s->enabled && message) {
        unsigned mouse = ((const uint8_t *)message)[0x14];
        if (mouse < 4u) {
            int (*get_fn_active)(void) = (void *)(s->view_base + MT_OFFSET_FN_EXISTENCE);
            if (get_fn_active && get_fn_active()) {
                handle_fn_touch(s, mouse);
            } else {
                handle_menu_touch(s, mouse);
            }
        } else {
            s->last_err = 98u; /* mouse index >= 4 */
        }
    }
    __sync_fetch_and_sub(&s->inflight, 1u);
    return result;
}
