/*
 * RAM-only 7th "Mods" tab & SD /mods .mod loader for Sony A6400 (viewUnified2.so).
 * The combined build uses libmprctrl.so RX padding with an anchor at 0x1615ec40.
 */
#include <stdint.h>
#include "mods_tab_look_layout.h"

#define VIEW_BASE       0x155e6000u
#define ANCHOR_ADDR     ML_ANCHOR_ADDR
#define SET_TEXT_DIRECT (VIEW_BASE + 0x14f6d0u + 1u)
#define SET_MODE_DIRECT (VIEW_BASE + 0x155a68u + 1u)

#define SMOD_MAGIC      ML_ANCHOR_MAGIC
#define SMOD_VERSION    1u
#define SMOD_MAX_HOOKS  8u
#define SMOD_MAX_PATCH  16u
#define ML_MAX_MODS     12u
#define ML_MAX_PAGES    2u

#define O_RDONLY        0
#define O_WR_CREAT_APP  0x441
#define O_DIRECTORY     040000
#define PROT_RWX        7
#define MAP_ANON_PRIV   0x22

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint32_t version;
    uint32_t flags;
    char     name[32];
    uint32_t code_addr;
    uint32_t code_size;
    uint32_t state_addr;
    uint32_t state_size;
    uint32_t hook_count;
    uint32_t fnv1a_32;
} SmodHeader;

typedef struct __attribute__((packed)) {
    uint32_t target_addr;
    uint32_t patch_size;
    uint8_t  orig_bytes[SMOD_MAX_PATCH];
    uint8_t  hook_bytes[SMOD_MAX_PATCH];
} SmodHook;

typedef struct {
    void     **vtable;
    void     **children;
    int32_t  child_count;
    void     *props;
    void     *parent;
    uint32_t layer_depth;   /* +0x14, CmnViewSettingNode::getLayerDepth: root 1, tab 2, page 3 */
    int32_t  selected_idx;
    uint32_t state_1c;
    uint32_t state_20;
    uint32_t state_24;
} FakeSettingNode;

typedef struct {
    char     name[32];
    char     path[64];
    uint8_t  enabled;
    uint8_t  hook_count;
    uint8_t  invalid;
    uint8_t  pad;
    uint32_t code_addr;
    uint32_t code_size;
    uint32_t state_addr;
    uint32_t state_size;
    uint32_t fnv1a_32;
    SmodHook hooks[SMOD_MAX_HOOKS];
} ModEntry;

typedef struct {
    uint32_t        magic;
    uint32_t        mod_count;
    uint32_t        page_count;
    uint8_t         scanned;
    uint8_t         vtable_copied;
    uint8_t         pad[2];
    FakeSettingNode *orig_root;
    FakeSettingNode *orig_tab5;
    void            **orig_children;
    uint32_t        last_menu;
    void            *vtable[72];
    void            *root_children[8];
    FakeSettingNode fake_root;
    FakeSettingNode mods_tab_node;
    FakeSettingNode page_nodes[ML_MAX_PAGES];
    FakeSettingNode item_nodes[ML_MAX_MODS];
    void            *page_ptrs[ML_MAX_PAGES];
    void            *item_ptrs[ML_MAX_MODS];
    ModEntry        mods[ML_MAX_MODS];
    /* Two shared dummy option children: ViewSettingMenu cond 4 (OK -> Action1, 0x20c95c) requires
     * item int prop 12 == 0 and getCountSelectable > 1 before it calls enter_selected. */
    FakeSettingNode opt_nodes[2];
    void            *opt_ptrs[2];
} ModLoaderState;

_Static_assert(sizeof(ModLoaderState) <= MTL_CACHE_OFF, "ModLoaderState overlaps the icon cache");

typedef struct {
    uint32_t magic;
    ModLoaderState *state;
} ModLoaderAnchor;

struct linux_dirent64 {
    uint64_t d_ino;
    int64_t  d_off;
    uint16_t d_reclen;
    uint8_t  d_type;
    char     d_name[1];
};

static inline int sys_open3(const char *path, int flags, int mode) {
    register int r0 __asm__("r0") = (int)path;
    register int r1 __asm__("r1") = flags;
    register int r2 __asm__("r2") = mode;
    register int r7 __asm__("r7") = 5;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return r0;
}

static inline int sys_open(const char *path, int flags) {
    return sys_open3(path, flags, 0);
}

static inline int sys_write(int fd, const void *buf, uint32_t len) {
    register int r0 __asm__("r0") = fd;
    register int r1 __asm__("r1") = (int)buf;
    register int r2 __asm__("r2") = (int)len;
    register int r7 __asm__("r7") = 4;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return r0;
}

static inline int sys_close(int fd) {
    register int r0 __asm__("r0") = fd;
    register int r7 __asm__("r7") = 6;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r7) : "memory");
    return r0;
}

static inline int sys_read(int fd, void *buf, uint32_t len) {
    register int r0 __asm__("r0") = fd;
    register int r1 __asm__("r1") = (int)buf;
    register int r2 __asm__("r2") = (int)len;
    register int r7 __asm__("r7") = 3;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return r0;
}

static inline int sys_lseek(int fd, uint32_t off) {
    register int r0 __asm__("r0") = fd;
    register int r1 __asm__("r1") = (int)off;
    register int r2 __asm__("r2") = 0;
    register int r7 __asm__("r7") = 19;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return r0;
}

static inline int sys_getdents64(int fd, void *buf, uint32_t len) {
    register int r0 __asm__("r0") = fd;
    register int r1 __asm__("r1") = (int)buf;
    register int r2 __asm__("r2") = (int)len;
    register int r7 __asm__("r7") = 217;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return r0;
}

static inline void *sys_mmap2(uint32_t len) {
    register int r0 __asm__("r0") = 0;
    register int r1 __asm__("r1") = (int)len;
    register int r2 __asm__("r2") = PROT_RWX;
    register int r3 __asm__("r3") = MAP_ANON_PRIV;
    register int r4 __asm__("r4") = -1;
    register int r5 __asm__("r5") = 0;
    register int r7 __asm__("r7") = 192;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r3), "r"(r4), "r"(r5), "r"(r7) : "memory");
    return (void *)r0;
}

static inline void sys_cacheflush(uint32_t beg, uint32_t end) {
    register uint32_t r0 __asm__("r0") = beg;
    register uint32_t r1 __asm__("r1") = end;
    register uint32_t r2 __asm__("r2") = 0;
    register uint32_t r7 __asm__("r7") = 0x0f0002u;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
}

static inline int sys_affinity(int nr, uint32_t *mask) {
    register int r0 __asm__("r0") = 0; /* calling thread */
    register uint32_t r1 __asm__("r1") = 4;
    register uint32_t *r2 __asm__("r2") = mask;
    register int r7 __asm__("r7") = nr; /* 241 set, 242 get */
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return r0;
}

/* The cacheflush syscall only maintains the calling CPU on this kernel (4x Cortex-A5, 3.0.27):
 * other cores kept executing the old hook prologues. Flush on each online CPU in turn. */
static void ml_flush_all(uint32_t beg, uint32_t end) {
    uint32_t old = 0, mask;
    sys_affinity(242, &old);
    for (uint32_t cpu = 0; cpu < 4u; cpu++) {
        mask = 1u << cpu;
        if (sys_affinity(241, &mask) == 0) sys_cacheflush(beg, end);
    }
    if (old) sys_affinity(241, &old);
}

static inline int sys_mprotect(uint32_t addr, uint32_t len, int prot) {
    register uint32_t r0 __asm__("r0") = addr;
    register uint32_t r1 __asm__("r1") = len;
    register int r2 __asm__("r2") = prot;
    register int r7 __asm__("r7") = 125;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return (int)r0;
}

static int ml_make_rwx(uint32_t addr, uint32_t len) {
    uint32_t beg = addr & ~0xfffu;
    uint32_t end = (addr + len + 0xfffu) & ~0xfffu;
    return sys_mprotect(beg, end - beg, PROT_RWX);
}

static int ml_memcmp(const void *a, const void *b, uint32_t n) {
    const uint8_t *pa = (const uint8_t *)a;
    const uint8_t *pb = (const uint8_t *)b;
    while (n--) {
        if (*pa != *pb) return (int)*pa - (int)*pb;
        pa++;
        pb++;
    }
    return 0;
}

static uint32_t ml_fnv1a(const uint8_t *data, uint32_t n) {
    uint32_t h = 0x811c9dc5u;
    while (n--) {
        h ^= *data++;
        h *= 0x01000193u;
    }
    return h;
}

static void ml_log(const char *msg, uint32_t val) {
    int fd = sys_open3("/tmp_bt/mods/mod_loader.log", O_WR_CREAT_APP, 0644);
    if (fd < 0) return;
    char buf[96];
    uint32_t i = 0;
    while (*msg && i + 12 < sizeof(buf)) buf[i++] = *msg++;
    buf[i++] = ' ';
    for (int s = 28; s >= 0; s -= 4) {
        uint32_t d = (val >> s) & 0xfu;
        buf[i++] = (char)(d < 10 ? ('0' + d) : ('a' + d - 10));
    }
    buf[i++] = '\n';
    sys_write(fd, buf, i);
    sys_close(fd);
}

static void ml_memcpy(void *dst, const void *src, uint32_t n) {
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    while (n--) *d++ = *s++;
}

static void ml_memset(void *dst, uint8_t v, uint32_t n) {
    uint8_t *d = (uint8_t *)dst;
    while (n--) *d++ = v;
}

static int ml_ends_with_mod(const char *s) {
    int len = 0;
    while (s[len]) len++;
    return len > 4 && s[len - 4] == '.' && s[len - 3] == 'm' && s[len - 2] == 'o' && s[len - 1] == 'd';
}

static void ml_join_path(char *out, uint32_t cap, const char *dir, const char *file) {
    uint32_t i = 0;
    while (*dir && i + 2 < cap) out[i++] = *dir++;
    out[i++] = '/';
    while (*file && i + 1 < cap) out[i++] = *file++;
    out[i] = '\0';
}

static int node_ret0(void) { return 0; }

static int node_ret1(void) { return 1; }

static int node_get_child_count(FakeSettingNode *n, int32_t *out) {
    *out = n->child_count;
    return 1;
}

static int node_get_child(FakeSettingNode *n, int32_t idx1, void **out) {
    if (idx1 >= 1 && idx1 <= n->child_count) {
        *out = n->children[idx1 - 1];
        return 1;
    }
    *out = 0;
    return 0;
}

static int node_get_cur_child(FakeSettingNode *n, void **out) {
    if (n->child_count > 0) {
        *out = n->children[0];
        return 1;
    }
    *out = 0;
    return 0;
}

static int node_get_cur_idx(FakeSettingNode *n, int32_t *out) {
    (void)n;
    *out = 1;
    return 1;
}

static int node_get_int_prop(FakeSettingNode *n, int32_t prop_id, int32_t *out) {
    if (prop_id == 11) {
        *out = 0;
        return 1;
    }
    ModLoaderAnchor *a = (ModLoaderAnchor *)ANCHOR_ADDR;
    ModLoaderState *st = a->state;
    if (st && prop_id == 12 && (uint32_t)n >= (uint32_t)&st->item_nodes[0] &&
        (uint32_t)n < (uint32_t)&st->item_nodes[ML_MAX_MODS]) {
        /* Item type 0 = option item; cond 4 (0x20c95c) maps OK to Action1 only for type 0. */
        *out = 0;
        return 1;
    }
    if (st && n == &st->mods_tab_node && st->orig_tab5) {
        typedef int (*get_int_fn)(void *, int32_t, int32_t *);
        get_int_fn fn = (get_int_fn)(st->orig_tab5->vtable[23]);
        return fn(st->orig_tab5, prop_id, out);
    }
    return 0;
}

static int node_get_resid_prop(FakeSettingNode *n, int32_t prop_id, uint32_t *out) {
    ModLoaderAnchor *a = (ModLoaderAnchor *)ANCHOR_ADDR;
    ModLoaderState *st = a->state;
    /* E0522: Sony requests 29 for other tabs, 30 for the current tab and 31 for tab-bar focus. */
    if (st && n == &st->mods_tab_node && (prop_id == 29 || prop_id == 30 || prop_id == 31)) {
#ifdef MTL_BUILTIN
        *out = prop_id == 29 ? 0x020cc012u : 0x020cc000u + (uint32_t)prop_id - 5u;
        return 1;
#else
        if (*(volatile uint32_t *)(MTL_HOOK_ADDR + 2u) == 0xf000f8dfu) {
            *out = 0x020cc012u;
            if (prop_id != 29 && *(volatile uint32_t *)MTL_CODE_ADDR == MTL_MARKER) {
                *out = 0x020cc000u + (uint32_t)prop_id - 5u;
            }
            return 1;
        }
#endif
    }
    if (st && n == &st->mods_tab_node && (prop_id == 29 || prop_id == 30 || prop_id == 31 || prop_id == 2) && st->orig_tab5) {
        typedef int (*get_resid_fn)(void *, int32_t, uint32_t *);
        get_resid_fn fn = (get_resid_fn)(st->orig_tab5->vtable[32]);
        return fn(st->orig_tab5, prop_id, out);
    }
    *out = 0;
    return 0;
}

static int node_is_disabled(FakeSettingNode *n, uint8_t *out) {
    (void)n;
    if (out) *out = 0;
    return 0;
}

static ModLoaderState *ml_get_state(void) {
    ModLoaderAnchor *a = (ModLoaderAnchor *)ANCHOR_ADDR;
    if (a->magic == SMOD_MAGIC && a->state) return a->state;
    ModLoaderState *st = (ModLoaderState *)sys_mmap2(ML_STATE_BYTES);
    if ((uint32_t)st >= 0xfffff000u) return 0;
    ml_memset(st, 0, sizeof(*st));
    st->magic = SMOD_MAGIC;
    for (uint32_t i = 0; i < 72; i++) st->vtable[i] = (void *)node_ret0;
    st->vtable[4]  = (void *)node_get_child_count;
    st->vtable[9]  = (void *)node_get_child;
    st->vtable[10] = (void *)node_get_cur_child;
    st->vtable[12] = (void *)node_get_cur_idx;
    st->vtable[23] = (void *)node_get_int_prop;
    st->vtable[32] = (void *)node_get_resid_prop;
    st->vtable[40] = (void *)node_is_disabled;
    a->state = st;
    a->magic = SMOD_MAGIC;
    return st;
}

static void ml_scan_dir(ModLoaderState *st, const char *dir) {
    int dfd = sys_open(dir, O_RDONLY | O_DIRECTORY);
    if (dfd < 0) return;
    uint8_t dbuf[512];
    int n;
    while ((n = sys_getdents64(dfd, dbuf, sizeof(dbuf))) > 0 && st->mod_count < ML_MAX_MODS) {
        int pos = 0;
        while (pos < n && st->mod_count < ML_MAX_MODS) {
            struct linux_dirent64 *d = (struct linux_dirent64 *)(dbuf + pos);
            pos += d->d_reclen;
            if (ml_ends_with_mod(d->d_name)) {
                char path[64];
                ml_join_path(path, sizeof(path), dir, d->d_name);
                int ffd = sys_open(path, O_RDONLY);
                if (ffd >= 0) {
                    SmodHeader hdr;
                    if (sys_read(ffd, &hdr, sizeof(hdr)) == (int)sizeof(hdr) &&
                        hdr.magic == SMOD_MAGIC && hdr.version == SMOD_VERSION &&
                        hdr.hook_count <= SMOD_MAX_HOOKS && hdr.code_size > 0 &&
                        (hdr.code_size & 3u) == 0 && hdr.code_addr >= 0x00100000u
#ifdef MTL_BUILTIN
                        && hdr.code_addr != MTL_CODE_ADDR
#endif
                    ) {
                        ModEntry *m = &st->mods[st->mod_count];
                        ml_memset(m, 0, sizeof(*m));
                        ml_memcpy(m->name, hdr.name, 31);
                        ml_memcpy(m->path, path, 63);
                        m->hook_count = (uint8_t)hdr.hook_count;
                        m->code_addr = hdr.code_addr;
                        m->code_size = hdr.code_size;
                        m->state_addr = hdr.state_addr;
                        m->state_size = hdr.state_size;
                        m->fnv1a_32 = hdr.fnv1a_32;
                        m->pad = (uint8_t)hdr.flags; /* bit 0: state-init blob follows code */
                        uint32_t hsz = hdr.hook_count * sizeof(SmodHook);
                        if (hsz == 0 || sys_read(ffd, m->hooks, hsz) == (int)hsz) {
                            st->mod_count++;
                        }
                    } else {
                        ml_log("scan_bad_hdr", hdr.magic);
                    }
                    sys_close(ffd);
                }
            }
        }
    }
    sys_close(dfd);
}

static void ml_build_tree(ModLoaderState *st) {
    if (!st->scanned) {
        static const char *const dirs[4] = {
            "/mnt/mmca1/mods", "/media/mmca1/mods", "/mnt/sd/mods", "/tmp_bt/mods"
        };
        for (uint32_t i = 0; i < 4 && st->mod_count == 0; i++) ml_scan_dir(st, dirs[i]);
        st->scanned = 1;
    }
    uint32_t n_items = st->mod_count ? st->mod_count : 1u;
    uint32_t n_pages = (n_items > 6u) ? 2u : 1u;
    if (n_pages > ML_MAX_PAGES) n_pages = ML_MAX_PAGES;
    st->page_count = n_pages;
    for (uint32_t i = 0; i < n_items && i < ML_MAX_MODS; i++) {
        uint32_t pos1 = (i - ((i >= 6u) ? 6u : 0u)) + 1u;
        st->item_nodes[i].vtable = st->vtable;
        st->item_nodes[i].children = st->opt_ptrs;
        st->item_nodes[i].child_count = 2;
        st->item_nodes[i].parent = &st->page_nodes[(i >= 6u) ? 1u : 0u];
        st->item_nodes[i].layer_depth = 4; /* live real option items report depth 4 */
        st->item_nodes[i].selected_idx = 1;
        st->item_nodes[i].state_1c = pos1;
        st->item_nodes[i].state_20 = pos1;
        st->item_nodes[i].state_24 = 1;
        st->item_ptrs[i] = &st->item_nodes[i];
    }
    for (uint32_t o = 0; o < 2u; o++) {
        st->opt_nodes[o].vtable = st->vtable;
        st->opt_nodes[o].children = 0;
        st->opt_nodes[o].child_count = 0;
        st->opt_nodes[o].parent = &st->item_nodes[0];
        st->opt_nodes[o].selected_idx = -1;
        st->opt_nodes[o].state_1c = o + 1u;
        st->opt_nodes[o].state_20 = o + 1u;
        st->opt_nodes[o].state_24 = o ? 1u : 2u;
        st->opt_ptrs[o] = &st->opt_nodes[o];
    }
    for (uint32_t p = 0; p < n_pages; p++) {
        uint32_t start = p * 6u;
        uint32_t rem = n_items - start;
        st->page_nodes[p].vtable = st->vtable;
        st->page_nodes[p].children = &st->item_ptrs[start];
        st->page_nodes[p].child_count = (int32_t)(rem > 6u ? 6u : rem);
        st->page_nodes[p].parent = &st->mods_tab_node;
        st->page_nodes[p].layer_depth = 3; /* live real pages report depth 3; 2 marks a tab */
        st->page_nodes[p].selected_idx = 1;
        st->page_nodes[p].state_1c = p + 1u;
        st->page_nodes[p].state_20 = p + 1u;
        st->page_nodes[p].state_24 = 1;
        st->page_ptrs[p] = &st->page_nodes[p];
    }
    st->mods_tab_node.vtable = st->vtable;
    st->mods_tab_node.children = st->page_ptrs;
    st->mods_tab_node.child_count = (int32_t)n_pages;
    st->mods_tab_node.parent = 0;
    st->mods_tab_node.layer_depth = 2;
    st->mods_tab_node.selected_idx = 1;
    st->mods_tab_node.state_1c = 7;
    st->mods_tab_node.state_20 = 7;
    st->mods_tab_node.state_24 = 1;
}

FakeSettingNode *ml_before_set_root(uint32_t menu, FakeSettingNode *root) {
    (void)menu;
    if (!root || (uint32_t)root < 0x00100000u || (root->child_count != 6 && root->child_count != 7)) return root;
    if (!root->vtable || (uint32_t)root->vtable < 0x00100000u || !root->children || (uint32_t)root->children < 0x00100000u) return root;
    ModLoaderState *st = ml_get_state();
    if (!st) return root;
    if (!st->vtable_copied && root->vtable) {
        for (uint32_t i = 0; i < 65u; i++) st->vtable[i] = root->vtable[i];
        st->vtable[23] = (void *)node_get_int_prop;
        st->vtable[32] = (void *)node_get_resid_prop;
        st->vtable[35] = (void *)node_ret1;
        st->vtable[40] = (void *)node_is_disabled;
        st->vtable_copied = 1;
    }
    ml_build_tree(st);
    if (root->children != st->root_children) {
        st->orig_root = root;
        st->orig_children = root->children;
        for (uint32_t i = 0; i < 6; i++) st->root_children[i] = root->children[i];
        st->orig_tab5 = (FakeSettingNode *)root->children[5];
        root->children = st->root_children;
    }
    st->mods_tab_node.parent = root;
    st->root_children[6] = &st->mods_tab_node;
    root->child_count = 7;
    return root;
}

static const char *ml_state_text(const ModEntry *m) {
    return m->invalid ? "ERR" : (m->enabled ? "ON" : "OFF");
}

void ml_after_refresh(uint32_t menu) {
    typedef void (*set_mode_fn)(void *, int);
    set_mode_fn set_mode = (set_mode_fn)SET_MODE_DIRECT;
    set_mode((void *)(menu + 0x97cu), 3);
    ModLoaderState *st = ml_get_state();
    if (st) {
        st->last_menu = menu;
#if defined(ML_HOOK_DISPATCH) || defined(ML_HOOK_PMAIN)
        /* Cache the tab index while the menu widget is known to be live, so the
         * dispatcher diagnostic never dereferences a possibly unmapped last_menu. */
        st->pad[0] = (uint8_t)*(volatile uint32_t *)(menu + 0x1f0u);
#endif
        if (st->orig_root && st->orig_root->children == st->root_children) {
            st->root_children[6] = &st->mods_tab_node;
            st->orig_root->child_count = 7;
        }
#ifdef ML_DIAG_OSD
        /* Log-only diagnostic (E0522): copy the tab-header strip of OSD A (im.elf mapping of
         * /dev/stream PA 0x23a80000, VA 0x0bfd0000, 640x480 RGBA4444, E0298) on the 3rd/6th/9th
         * refresh, when earlier menu frames are on screen. 640x70x2 = 89600 B per file. */
        uint32_t c = ++st->pad[1];
        if (c == 3u || c == 6u || c == 9u) {
            char path[] = "/tmp_bt/mods/osd0.bin";
            path[16] = (char)('0' + c / 3u);
            int fd = sys_open3(path, 0x241 /* O_WRONLY|O_CREAT|O_TRUNC */, 0644);
            if (fd >= 0) {
                sys_write(fd, (const void *)0x0bfd0000u, 640u * 70u * 2u);
                sys_close(fd);
            }
            ml_log("osd_dump", (c << 16) | *(volatile uint32_t *)(menu + 0x1f0u));
        }
#endif
    }
    if (*(volatile uint32_t *)(menu + 0x1f0u) != 7u) return;
    if (!st) return;
    typedef void (*set_text_fn)(void *, const char *);
    set_text_fn set_text = (set_text_fn)SET_TEXT_DIRECT;
    set_text((void *)(menu + 0x5470u), "Mods");
    uint32_t page = *(volatile uint32_t *)(menu + 0x1f4u);
    if (page == 0) page = 1;
    uint32_t base = (page - 1u) * 6u;
    for (uint32_t r = 0; r < 6; r++) {
        uint32_t btn = menu + 0x9350u + r * 0x1118u;
        uint32_t idx = base + r;
        if (idx < st->mod_count) {
            set_text((void *)(btn + 0x7c8u), st->mods[idx].name);
            set_text((void *)(btn + 0xb60u), ml_state_text(&st->mods[idx]));
        } else if (r == 0 && st->mod_count == 0) {
            set_text((void *)(btn + 0x7c8u), "No mods in /mods");
            set_text((void *)(btn + 0xb60u), "");
        }
    }
}

/* SMOD flag bit 1: lifecycle mod; Thumb entry at code_addr gets 1 after ON, 0 before OFF. */
static void __attribute__((noinline)) ml_life(ModEntry *m, int on) {
    if (m->pad & 2u) ((void (*)(int))(m->code_addr | 1u))(on);
    ml_log("mod", m->code_addr | (uint32_t)on); /* low bit: 1 = ON, 0 = OFF */
}

static void ml_toggle_mod(ModEntry *m) {
    if (m->invalid) return; /* shown as "ERR" in the menu */
    if (!m->enabled) {
        for (uint32_t h = 0; h < m->hook_count; h++) {
            SmodHook *hk = &m->hooks[h];
            if (hk->target_addr < 0x00100000u || hk->patch_size == 0 || hk->patch_size > SMOD_MAX_PATCH) {
                ml_log("tgl_err", 1u);
                m->invalid = 1;
                return;
            }
            if (ml_memcmp((void *)hk->target_addr, hk->orig_bytes, hk->patch_size) != 0) {
                ml_log("tgl_err", 2u);
                m->invalid = 1;
                return;
            }
        }
        int fd = sys_open(m->path, O_RDONLY);
        if (fd < 0) {
            ml_log("tgl_err", 3u);
            return;
        }
        uint32_t off = sizeof(SmodHeader) + (uint32_t)m->hook_count * sizeof(SmodHook);
        sys_lseek(fd, off);
        int rc = ml_make_rwx(m->code_addr, m->code_size);
        if (rc < 0) {
            ml_log("tgl_err", 4u);
            m->invalid = 1;
            sys_close(fd);
            return;
        }
        int n = sys_read(fd, (void *)m->code_addr, m->code_size);
        if (m->pad & 1u) {
            /* SMOD flag bit 0: state_size bytes of initial state follow the code; they replace
             * the zero fill below (MTF2 expects its magic/version/enabled header). */
            if (sys_read(fd, (void *)m->state_addr, m->state_size) != (int)m->state_size) n = -1;
        } else if (m->state_addr && m->state_size) {
            ml_memset((void *)m->state_addr, 0, m->state_size);
        }
        sys_close(fd);
        if (n != (int)m->code_size) {
            ml_log("tgl_err", 5u);
            m->invalid = 1;
            return;
        }
        uint32_t crc = ml_fnv1a((const uint8_t *)m->code_addr, m->code_size);
        if (crc != m->fnv1a_32) {
            ml_log("tgl_err", 6u);
            m->invalid = 1;
            return;
        }
        ml_flush_all(m->code_addr, m->code_addr + m->code_size);
        for (uint32_t h = 0; h < m->hook_count; h++) {
            SmodHook *hk = &m->hooks[h];
            rc = ml_make_rwx(hk->target_addr, hk->patch_size);
            if (rc < 0) {
                ml_log("tgl_err", 7u);
                for (uint32_t r = 0; r < h; r++) {
                    SmodHook *rh = &m->hooks[r];
                    ml_memcpy((void *)rh->target_addr, rh->orig_bytes, rh->patch_size);
                    ml_flush_all(rh->target_addr, rh->target_addr + rh->patch_size);
                }
                m->invalid = 1;
                return;
            }
            ml_memcpy((void *)hk->target_addr, hk->hook_bytes, hk->patch_size);
            ml_flush_all(hk->target_addr, hk->target_addr + hk->patch_size);
        }
        m->enabled = 1;
        ml_life(m, 1);
    } else {
        ml_life(m, 0);
        for (uint32_t h = 0; h < m->hook_count; h++) {
            SmodHook *hk = &m->hooks[h];
            if (ml_make_rwx(hk->target_addr, hk->patch_size) == 0) {
                ml_memcpy((void *)hk->target_addr, hk->orig_bytes, hk->patch_size);
                ml_flush_all(hk->target_addr, hk->target_addr + hk->patch_size);
            }
        }
        m->enabled = 0;
    }
}

/*
 * Called at entry of the menu "enter selected item" routine
 * (viewUnified2.so + 0x5ac044, reached from the OK/decide path at 0x2127e8).
 * Returns >= 0 to consume the call (value becomes the routine's result),
 * or -1 to run the original routine.
 */
#ifdef ML_HOOK_DISPATCH
/*
 * Log-only diagnostic at ViewSettingMenu's action TBH dispatcher
 * (viewUnified2.so + 0x21355e, r0 = view, r1 = action index; 1 = Action1).
 * Logs every action (capped) with the cached tab index and view+0x14c state.
 * Only dereferences the live view argument. Always returns -1 (run original).
 */
int ml_before_dispatch(uint32_t view, uint32_t act) {
    ModLoaderAnchor *a = (ModLoaderAnchor *)ANCHOR_ADDR;
    if (a->magic != SMOD_MAGIC || !a->state) return -1;
    ModLoaderState *st = a->state;
    if (st->pad[1] >= 120u) return -1;
    st->pad[1]++;
    ml_log("d_act", act);
    ml_log("d_tab", st->pad[0]);
    ml_log("d_v14c", *(volatile uint32_t *)(view + 0x14cu));
    if (act >= 1u && act <= 5u) ml_log("d_view", view);
    if (act == 1u) {
        /* Same getter Action1 (0x2127e8) calls before enter_selected: 0x207c4a(view). */
        typedef uint32_t (*sel_fn)(uint32_t);
        uint32_t sel = ((sel_fn)0x157edc4bu)(view);
        ml_log("d_sel", sel);
        ml_log("d_items_lo", (uint32_t)&st->item_nodes[0]);
    }
    return -1;
}
#endif

#ifdef ML_HOOK_DISPATCH
/*
 * Called after the original dispatcher returns (menu still open, state already updated).
 * For move/OK actions (1..5) writes /tmp_bt/mods/vNN.bin = view[0..0x300) + menu[0x1d0..0x210),
 * NN = log sequence number. Capped by the same counter as ml_before_dispatch.
 */
void ml_after_dispatch(uint32_t view, uint32_t act) {
    ModLoaderAnchor *a = (ModLoaderAnchor *)ANCHOR_ADDR;
    if (a->magic != SMOD_MAGIC || !a->state) return;
    ModLoaderState *st = a->state;
    if (act < 1u || act > 5u || st->pad[1] >= 120u) return;
    uint32_t seq = st->pad[1];
    char path[] = "/tmp_bt/mods/v00.bin";
    uint32_t hi = (seq >> 4) & 0xfu, lo = seq & 0xfu;
    path[14] = (char)(hi < 10 ? '0' + hi : 'a' + hi - 10);
    path[15] = (char)(lo < 10 ? '0' + lo : 'a' + lo - 10);
    int fd = sys_open3(path, 0x241 /* O_WRONLY|O_CREAT|O_TRUNC */, 0644);
    if (fd < 0) return;
    sys_write(fd, (const void *)view, 0x300u);
    if (st->last_menu) sys_write(fd, (const void *)(st->last_menu + 0x1d0u), 0x40u);
    sys_close(fd);
    ml_log("d_dump", (act << 16) | seq);
}
#endif

#ifdef ML_HOOK_PMAIN
#define VSM_VPTR        0x15ec8270u /* live ViewSettingMenu vptr (viewUnified2 file 0x8e2270) */
#define GET_EVENT_ID    0x157397b8u /* viewUnified2 PLT stub for ViewBase::GetEventId (ARM) */
/*
 * Log-only diagnostic at ViewBaseProduct::ProductMain (viewUnified2.so + 0x2f2df4), which runs for
 * every view. Only ViewSettingMenu instances are logged: raw event ID + cached tab (cap 250).
 */
void ml_before_pmain(uint32_t view) {
    ModLoaderAnchor *a = (ModLoaderAnchor *)ANCHOR_ADDR;
    if (a->magic != SMOD_MAGIC || !a->state) return;
    if (*(volatile uint32_t *)view != VSM_VPTR) return;
    ModLoaderState *st = a->state;
    if (st->pad[1] >= 250u) return;
    st->pad[1]++;
    typedef uint32_t (*get_ev_fn)(uint32_t);
    uint32_t ev = ((get_ev_fn)GET_EVENT_ID)(view);
    ml_log("p_ev", ev);
    ml_log("p_tab", st->pad[0]);
}
#endif

int ml_before_enter(uint32_t menu, FakeSettingNode *leaf) {
#if defined(ML_HOOK_DISPATCH) || defined(ML_HOOK_PMAIN) || defined(ML_DIAG_OSD)
    /* Unused when the third hook targets the dispatcher; keeps the payload within CODE_CAP. */
    return -1;
#endif
#ifdef ML_DIAG_ENTER
    /* Log-only diagnostic: record every enter_selected call, never consume it. */
    {
        ModLoaderAnchor *da = (ModLoaderAnchor *)ANCHOR_ADDR;
        ml_log("ent_tab", *(volatile uint32_t *)(menu + 0x1f0u));
        ml_log("ent_leaf", (uint32_t)leaf);
        ml_log("ent_m1f8", *(volatile uint32_t *)(menu + 0x1f8u));
        ml_log("ent_186b0", *(volatile uint32_t *)(menu + 0x186b0u));
        if (da->magic == SMOD_MAGIC && da->state) {
            ml_log("ent_nodes_lo", (uint32_t)&da->state->item_nodes[0]);
            ml_log("ent_nodes_hi", (uint32_t)&da->state->item_nodes[ML_MAX_MODS]);
        } else {
            ml_log("ent_no_state", da->magic);
        }
        return -1;
    }
#endif
    ModLoaderAnchor *a = (ModLoaderAnchor *)ANCHOR_ADDR;
    if (a->magic != SMOD_MAGIC || !a->state) return -1;
    ModLoaderState *st = a->state;
    uint32_t lo = (uint32_t)&st->item_nodes[0];
    uint32_t hi = (uint32_t)&st->item_nodes[ML_MAX_MODS];
    uint32_t lp = (uint32_t)leaf;
    if (lp < lo || lp >= hi) return -1;
    uint32_t idx = 0;
    while (idx < ML_MAX_MODS && (uint32_t)&st->item_nodes[idx] != lp) idx++;
    ml_log("enter_idx", idx);
    if (idx >= st->mod_count) return 0;
    ModEntry *m = &st->mods[idx];
    ml_toggle_mod(m);
    typedef void (*set_text_fn)(void *, const char *);
    set_text_fn set_text = (set_text_fn)SET_TEXT_DIRECT;
    uint32_t row = (idx >= 6u) ? idx - 6u : idx; /* no hardware divide on Cortex-A5 */
    uint32_t btn = menu + 0x9350u + row * 0x1118u;
    set_text((void *)(btn + 0xb60u), ml_state_text(m));
    return 0;
}
