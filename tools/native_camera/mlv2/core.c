/* Mod Loader v2 core: position-independent, loaded by the shim into mmap memory.
 *
 * - Builds the 7th "Mods" tab (same fake-node technique as v1).
 * - Rescans the mod folders every time the menu root is set; enabled mods keep their state.
 * - Loads SMOD v1 (fixed address) and SMOD v2 (relocatable, core-allocated memory) mods.
 * - Lifecycle: v2 on()/off() offsets; v1 flag bit 1 (Thumb entry at code_addr).
 * - Survives core upgrades: the context is versioned and adopted by the next core.
 * - Takes over a running v1 loader: imports its enabled mods, leaves its state and code intact.
 * - Reload request (MLV2_RELOAD_REQ): replaces an enabled mod with its new file in one UI-thread
 *   step (load + verify new code, OFF + verify old, ON new). Details at process_reload().
 *
 * - Layout: firmware addresses are ILCE-6400 reference addresses. core_init stores the shim's
 *   viewUnified2.so difference and the relocated shim slot in two words of this image; SMOD v2
 *   mods with a relocation table are moved by library (see SMOD_V2_FLAG_RELOC in mlv2.h).
 *
 * Rules for position independence: no writable globals outside .text.zdata (this image is
 * mapped RWX and written only by core_init), no static pointer tables; function and string
 * addresses are only taken at run time (PC-relative). build_wsl.sh rejects absolute relocations.
 */
#include "mlv2.h"
#include "mlv2_sys.h"
#include "../mods_tab_look_layout.h"

#define CTX_MAGIC    0x58544332u  /* "2CTX" */
#define CTX_VERSION  3u
#define MAX_MODS     16u
#define MAX_PAGES    3u
#define MAX_FOLDERS  4u
#define KIND_V1      1u
#define KIND_V2      2u

typedef struct Node {
    void   **vtable;
    void   **children;
    int32_t  child_count;
    void    *props;
    void    *parent;
    uint32_t layer_depth;
    int32_t  selected_idx;
    uint32_t s1c, s20, s24;
} Node;

typedef struct {
    uint32_t target;
    uint8_t  size, fixup, pad[2];
    uint8_t  orig[SMOD_MAX_PATCH];
    uint8_t  patch[SMOD_MAX_PATCH];   /* v1: final bytes; v2: template (fixup applied at ON) */
} Hook;

typedef struct {
    char     name[32];
    char     path[80];
    char     folder[24];
    uint32_t fnv;
    uint8_t  kind, enabled, invalid, flags;
    uint8_t  hook_count, present, stale, off_called;
    uint32_t code_size;
    uint32_t extra_size;   /* v1: state_size; v2: bss_size */
    uint32_t addr;         /* v1: fixed code_addr; v2: mmap base (0 until first ON) */
    uint32_t map_size;     /* v2: size of the mapping at addr */
    uint32_t state_addr;   /* v1 only */
    uint32_t on_off, off_off;
    uint32_t data_off;     /* file offset of the code bytes */
    Hook     hooks[SMOD_MAX_HOOKS];
} Mod;

typedef struct {
    uint32_t magic, version;
    uint32_t mod_count, page_count;
    uint32_t vtable_ready;
    Node    *orig_root;
    Node    *orig_tab5;
    uint32_t last_menu;
    void    *vtable[72];
    void    *root_children[8];
    Node     tab_node;
    Node     pages[MAX_PAGES];
    Node     items[MAX_MODS];
    Node     opts[2];
    void    *page_ptrs[MAX_PAGES];
    void    *item_ptrs[MAX_MODS];
    void    *opt_ptrs[2];
    Mod      mods[MAX_MODS];
    Mod      scan[MAX_MODS];   /* scratch for rescans */
    Mod      keep[MAX_MODS];   /* merge output (kept off the UI thread stack) */
    Mod      tmp;
    uint32_t scan_count;
    uint8_t  scan_buf[512];
    uint32_t folder_count, view_count;
    char     folder_names[MAX_FOLDERS][24];
    int16_t  view_map[MAX_MODS];
    uint8_t  in_folder;
    void **original_children;
    int32_t original_child_count;
} Ctx;

#define CTX_BYTES ((sizeof(Ctx) + 0xfffu) & ~0xfffu)

/* ---- v1 state layout (read-only, for takeover). Mirrors tools/native_camera/mod_loader.c. ---- */
typedef struct {
    char     name[32];
    char     path[64];
    uint8_t  enabled, hook_count, invalid, pad;
    uint32_t code_addr, code_size, state_addr, state_size, fnv1a_32;
    SmodV1Hook hooks[SMOD_MAX_HOOKS];
} V1Mod;
typedef struct {
    uint32_t magic, mod_count, page_count;
    uint8_t  scanned, vtable_copied, pad[2];
    void    *orig_root, *orig_tab5, **orig_children;
    uint32_t last_menu;
    void    *vtable[72];
    void    *root_children[8];
    Node     fake_root, mods_tab_node, page_nodes[2], item_nodes[12];
    void    *page_ptrs[2], *item_ptrs[12];
    V1Mod    mods[12];
} V1State;
_Static_assert(sizeof(V1Mod) == 440, "v1 ModEntry layout");

/* Written once by core_init (before any hook can reach this core), read PC-relative. */
__attribute__((section(".text.zdata"), used)) static volatile uint32_t core_view_bias = 0;
__attribute__((section(".text.zdata"), used)) static volatile uint32_t core_slot = 0;
__attribute__((section(".text.zdata"), used)) static volatile uint32_t core_libobj_bias = 0;
__attribute__((section(".text.zdata"), used)) static volatile uint32_t core_libobj_ok = 0;
#define VIEW(a) ((uint32_t)(a) + core_view_bias)
#define REF_LIBOBJ_BASE 0x05d18000u

static Ctx *ctx_get(void) {
    if (!core_slot) return 0;
    ShimState *s = *(ShimState *volatile *)core_slot;
    if (!s || s->magic != MLV2_SHIM_MAGIC) return 0;
    Ctx *c = (Ctx *)s->ctx;
    return (c && c->magic == CTX_MAGIC) ? c : 0;
}

/* ---------------- fake menu node vtable entries ---------------- */
static int node_ret0(void) { return 0; }
static int node_ret1(void) { return 1; }
static int node_child_count(Node *n, int32_t *out) { *out = n->child_count; return 1; }
static int node_child(Node *n, int32_t idx1, void **out) {
    if (idx1 >= 1 && idx1 <= n->child_count) { *out = n->children[idx1 - 1]; return 1; }
    *out = 0;
    return 0;
}
static int node_cur_child(Node *n, void **out) {
    if (n->child_count > 0) { *out = n->children[0]; return 1; }
    *out = 0;
    return 0;
}
static int node_cur_idx(Node *n, int32_t *out) { (void)n; *out = 1; return 1; }

static int node_int_prop(Node *n, int32_t prop, int32_t *out) {
    if (prop == 11) { *out = 0; return 1; }
    Ctx *c = ctx_get();
    if (!c) return 0;
    if (prop == 12 && (uint32_t)n >= (uint32_t)&c->items[0] && (uint32_t)n < (uint32_t)&c->items[MAX_MODS]) {
        *out = 0;   /* option item type 0: OK maps to Action1 (E0523) */
        return 1;
    }
    if (n == &c->tab_node && c->orig_tab5) {
        typedef int (*fn_t)(void *, int32_t, int32_t *);
        return ((fn_t)c->orig_tab5->vtable[23])(c->orig_tab5, prop, out);
    }
    return 0;
}

/* The Mods tab artwork resources 0x020cc012/19/1a are served only by the ModsTabLook hook in the
 * alcor index lookup (libObj.so + 0x79d4b6, E0522), which the v1 loader installs. Without that hook
 * (a fresh v2 install, QEMU) those resources are empty and the tab has no icon, so the tab then
 * uses the sixth tab's icon instead. The hook is looked up at the live libObj.so base. */
static int mtl_present(void) {
#ifdef MLV2_TARGET_A6100
    return 0;   /* a6100 has no v1 ModsTabLook hook; always use the sixth tab's icon */
#else
    if (!core_libobj_ok) return 0;
    return *(volatile uint32_t *)(MTL_HOOK_ADDR + 2u + core_libobj_bias) == 0xf000f8dfu;
#endif
}

static int node_resid_prop(Node *n, int32_t prop, uint32_t *out) {
    Ctx *c = ctx_get();
    if (c && n == &c->tab_node && (prop == 29 || prop == 30 || prop == 31) && mtl_present()) {
        /* E0522 built-in tab artwork: 29 inactive, 30 current, 31 tab-bar focus. */
        *out = prop == 29 ? 0x020cc012u : 0x020cc000u + (uint32_t)prop - 5u;
        return 1;
    }
    if (c && n == &c->tab_node && (prop == 2 || prop == 29 || prop == 30 || prop == 31) && c->orig_tab5) {
        typedef int (*fn_t)(void *, int32_t, uint32_t *);
        return ((fn_t)c->orig_tab5->vtable[32])(c->orig_tab5, prop, out);
    }
    *out = 0;
    return 0;
}

static int node_is_disabled(Node *n, uint8_t *out) { (void)n; if (out) *out = 0; return 0; }

/* Point the core-owned vtable entries at this core's functions (also after an upgrade). */
static void vtable_bind(Ctx *c) {
    if (!c->vtable_ready) {
        for (uint32_t i = 0; i < 72u; i++) c->vtable[i] = (void *)node_ret0;
        c->vtable[4] = (void *)node_child_count;
        c->vtable[9] = (void *)node_child;
        c->vtable[10] = (void *)node_cur_child;
        c->vtable[12] = (void *)node_cur_idx;
    } else {
        for (uint32_t i = 65u; i < 72u; i++) c->vtable[i] = (void *)node_ret0;
        c->vtable[35] = (void *)node_ret1;
    }
    c->vtable[23] = (void *)node_int_prop;
    c->vtable[32] = (void *)node_resid_prop;
    c->vtable[40] = (void *)node_is_disabled;
}

/* ---------------- scanning ---------------- */
struct dirent64 { uint64_t ino; int64_t off; uint16_t reclen; uint8_t type; char name[1]; };

static int ends_with_mod(const char *s) {
    int n = 0;
    while (s[n]) n++;
    return n > 4 && s[n - 4] == '.' && s[n - 3] == 'm' && s[n - 2] == 'o' && s[n - 1] == 'd';
}

static void join(char *out, uint32_t cap, const char *dir, const char *file) {
    uint32_t i = 0;
    while (*dir && i + 2 < cap) out[i++] = *dir++;
    out[i++] = '/';
    while (*file && i + 1 < cap) out[i++] = *file++;
    out[i] = 0;
}

static const char *base_name(const char *p) {
    const char *b = p;
    for (; *p; p++) if (*p == '/') b = p + 1;
    return b;
}

static int already_listed(Ctx *c, const char *path) {
    for (uint32_t i = 0; i < c->scan_count; i++)
        if (!ml_strcmp(base_name(c->scan[i].path), base_name(path))) return 1;
    return 0;
}


/* ---------------- SMOD v2 relocation (SMOD_V2_FLAG_RELOC) ---------------- */
/* Runtime base of each named library: start of its first mapping at file offset 0. */
static int maps_bases(const SmodRelocLib *libs, uint32_t n, uint32_t *base) {
    for (uint32_t i = 0; i < n; i++) base[i] = 0;
    int fd = ml_open("/proc/self/maps", ML_O_RDONLY);
    if (fd < 0) return 0;
    char buf[512], line[200];
    uint32_t len = 0;
    int got;
    while ((got = ml_read(fd, buf, sizeof(buf))) > 0) {
        for (int k = 0; k < got; k++) {
            char ch = buf[k];
            if (ch != '\n') { if (len + 1u < sizeof(line)) line[len++] = ch; continue; }
            line[len] = 0;
            /* "lo-hi perms offset dev inode path" */
            uint32_t lo = 0, off = 0, i = 0;
            while (line[i] && line[i] != '-') { char c = line[i++]; lo = lo * 16u + (uint32_t)(c <= '9' ? c - '0' : (c | 0x20) - 'a' + 10); }
            while (line[i] && line[i] != ' ') i++;
            while (line[i] == ' ') i++;
            while (line[i] && line[i] != ' ') i++;          /* perms */
            while (line[i] == ' ') i++;
            while (line[i] && line[i] != ' ') { char c = line[i++]; off = off * 16u + (uint32_t)(c <= '9' ? c - '0' : (c | 0x20) - 'a' + 10); }
            const char *path = 0;
            for (uint32_t j = i; line[j]; j++) if (line[j] == '/') { path = line + j; break; }
            if (path && !off) {
                const char *leaf = base_name(path);
                for (uint32_t li = 0; li < n; li++)
                    if (!base[li] && !ml_strcmp(leaf, libs[li].name)) base[li] = lo;
            }
            len = 0;
        }
    }
    ml_close(fd);
    for (uint32_t i = 0; i < n; i++) if (!base[i]) return 0;
    return 1;
}

/* Read and check the table at the current file position; fills libs and bias. Returns nrel or -1.
 * With code != 0 the listed words of the loaded code are relocated. */
static int reloc_apply(int fd, uint32_t code_size, uint8_t *code, SmodRelocLib *libs, uint32_t *bias,
                       uint32_t *nlib_out) {
    uint32_t hdr[2], base[SMOD_RELOC_MAX_LIBS];
    if (ml_read(fd, hdr, 8) != 8 || hdr[0] != SMOD_RELOC_MAGIC) return -1;
    uint32_t nlib = hdr[1] & 0xffffu, nrel = hdr[1] >> 16;
    if (!nlib || nlib > SMOD_RELOC_MAX_LIBS || nrel > SMOD_RELOC_MAX) return -1;
    *nlib_out = nlib;
    uint32_t h = ml_fnv1a((const uint8_t *)hdr, 8);
    if (ml_read(fd, libs, nlib * sizeof(SmodRelocLib)) != (int)(nlib * sizeof(SmodRelocLib))) return -1;
    h = ml_fnv1a_more(h, (const uint8_t *)libs, nlib * sizeof(SmodRelocLib));
    for (uint32_t i = 0; i < nlib; i++) {
        libs[i].name[23] = 0;
        if (libs[i].ref_hi <= libs[i].ref_lo) return -1;
    }
    if (!maps_bases(libs, nlib, base)) return -2;
    for (uint32_t i = 0; i < nlib; i++) bias[i] = base[i] - libs[i].ref_lo;
    uint32_t words[64];
    for (uint32_t done = 0; done < nrel;) {
        uint32_t chunk = nrel - done > 64u ? 64u : nrel - done;
        if (ml_read(fd, words, chunk * 4u) != (int)(chunk * 4u)) return -1;
        h = ml_fnv1a_more(h, (const uint8_t *)words, chunk * 4u);
        for (uint32_t k = 0; k < chunk; k++) {
            uint32_t off = words[k] & 0xffffffu, lib = words[k] >> 24;
            if (lib >= nlib || (off & 3u) || off + 4u > code_size) return -1;
            if (code) {
                uint32_t w;
                ml_memcpy(&w, code + off, 4);
                w += bias[lib];
                ml_memcpy(code + off, &w, 4);
            }
        }
        done += chunk;
    }
    uint32_t want;
    if (ml_read(fd, &want, 4) != 4 || want != h) return -1;
    return (int)nrel;
}

/* Parse one .mod header into m; returns 1 if valid. */
static int parse_mod(const char *path, Mod *m) {
    int fd = ml_open(path, ML_O_RDONLY);
    if (fd < 0) return 0;
    uint8_t hdr[68];
    int ok = ml_read(fd, hdr, sizeof(hdr)) == (int)sizeof(hdr);
    const SmodV1Header *h1 = (const SmodV1Header *)hdr;
    const SmodV2Header *h2 = (const SmodV2Header *)hdr;
    ml_memset(m, 0, sizeof(*m));
    if (ok && h1->magic == SMOD_MAGIC && h1->version == 1u && h1->hook_count <= SMOD_MAX_HOOKS &&
        h1->code_size && !(h1->code_size & 3u) && h1->code_addr >= 0x00100000u) {
        m->kind = KIND_V1;
        m->flags = (uint8_t)h1->flags;
        m->hook_count = (uint8_t)h1->hook_count;
        m->code_size = h1->code_size;
        m->extra_size = h1->state_size;
        m->addr = h1->code_addr;
        m->state_addr = h1->state_addr;
        m->fnv = h1->fnv1a_32;
        ml_memcpy(m->name, h1->name, 31);
        for (uint32_t i = 0; i < m->hook_count && ok; i++) {
            SmodV1Hook hk;
            ok = ml_read(fd, &hk, sizeof(hk)) == (int)sizeof(hk) && hk.patch_size >= 1u &&
                 hk.patch_size <= SMOD_MAX_PATCH && hk.target_addr >= 0x00100000u;
            if (!ok) break;
            m->hooks[i].target = hk.target_addr;
            m->hooks[i].size = (uint8_t)hk.patch_size;
            m->hooks[i].fixup = SMOD_V2_NO_FIXUP;
            ml_memcpy(m->hooks[i].orig, hk.orig_bytes, SMOD_MAX_PATCH);
            ml_memcpy(m->hooks[i].patch, hk.hook_bytes, SMOD_MAX_PATCH);
        }
        m->data_off = 68u + m->hook_count * 40u;
    } else if (ok && h2->magic == SMOD_MAGIC && h2->version == 2u && h2->hook_count <= SMOD_MAX_HOOKS &&
               h2->code_size && !(h2->code_size & 3u) && h2->code_size + h2->bss_size <= 1024u * 1024u &&
               (!h2->on_off || ((h2->on_off & 1u) && h2->on_off < h2->code_size)) &&
               (!h2->off_off || ((h2->off_off & 1u) && h2->off_off < h2->code_size))) {
        m->kind = KIND_V2;
        m->flags = (uint8_t)h2->flags;
        m->hook_count = (uint8_t)h2->hook_count;
        m->code_size = h2->code_size;
        m->extra_size = h2->bss_size;
        m->on_off = h2->on_off;
        m->off_off = h2->off_off;
        m->fnv = h2->fnv1a_32;
        ml_memcpy(m->name, h2->name, 31);
        for (uint32_t i = 0; i < m->hook_count && ok; i++) {
            SmodV2Hook hk;
            ok = ml_read(fd, &hk, sizeof(hk)) == (int)sizeof(hk) && hk.patch_size >= 1u &&
                 hk.patch_size <= SMOD_MAX_PATCH && hk.target_addr >= 0x00100000u &&
                 (hk.fixup_off == SMOD_V2_NO_FIXUP || hk.fixup_off + 4u <= hk.patch_size);
            if (!ok) break;
            m->hooks[i].target = hk.target_addr;
            m->hooks[i].size = hk.patch_size;
            m->hooks[i].fixup = hk.fixup_off;
            ml_memcpy(m->hooks[i].orig, hk.orig_bytes, SMOD_MAX_PATCH);
            ml_memcpy(m->hooks[i].patch, hk.tmpl, SMOD_MAX_PATCH);
        }
        m->data_off = 68u + m->hook_count * 40u;
        if (ok && (m->flags & SMOD_V2_FLAG_RELOC)) {
            SmodRelocLib libs[SMOD_RELOC_MAX_LIBS];
            uint32_t bias[SMOD_RELOC_MAX_LIBS];
            ml_lseek(fd, m->data_off + m->code_size);
            uint32_t nlib = 0;
            int nrel = reloc_apply(fd, m->code_size, 0, libs, bias, &nlib);
            if (nrel == -2) {
                m->invalid = 1;               /* a listed library is not mapped: shown as ERR */
                ml_log("reloc_nolib", 0);
            } else if (nrel < 0) {
                ok = 0;
            } else {
                for (uint32_t i = 0; i < m->hook_count; i++)
                    for (uint32_t li = 0; li < nlib; li++)
                        if (m->hooks[i].target >= libs[li].ref_lo && m->hooks[i].target < libs[li].ref_hi) {
                            m->hooks[i].target += bias[li];
                            break;
                        }
            }
        }
    } else {
        ok = 0;
    }
    ml_close(fd);
    if (!ok) { ml_log("scan_bad", h1->magic); return 0; }
    ml_memset(m->path, 0, sizeof(m->path));
    for (uint32_t k = 0; k < sizeof(m->path) - 1u && path[k]; k++) m->path[k] = path[k];
    m->name[31] = 0;
    m->present = 1;
    return 1;
}

static void scan_mods_in(Ctx *c, const char *dir, const char *folder) {
    int dfd = ml_open(dir, ML_O_RDONLY | ML_O_DIRECTORY);
    if (dfd < 0) return;
    char sub_paths[MAX_FOLDERS][80];
    char sub_names[MAX_FOLDERS][24];
    uint32_t nsub = 0;
    int n;
    while ((n = ml_getdents64(dfd, c->scan_buf, sizeof(c->scan_buf))) > 0 && c->scan_count < MAX_MODS) {
        for (int pos = 0; pos < n && c->scan_count < MAX_MODS;) {
            struct dirent64 *d = (struct dirent64 *)(c->scan_buf + pos);
            pos += d->reclen;
            if (d->name[0] == '.') continue;
            char path[80];
            join(path, sizeof(path), dir, d->name);
            if (!folder && d->type == 4u && !ends_with_mod(d->name)) {
                if (nsub < MAX_FOLDERS) {
                    ml_memcpy(sub_paths[nsub], path, sizeof(sub_paths[nsub]));
                    ml_memset(sub_names[nsub], 0, sizeof(sub_names[nsub]));
                    for (uint32_t k = 0; k < 23u && d->name[k]; k++) sub_names[nsub][k] = d->name[k];
                    nsub++;
                }
                continue;
            }
            if (!ends_with_mod(d->name)) continue;
            if (already_listed(c, path)) continue;   /* first folder wins for a given file name */
            Mod *m = &c->scan[c->scan_count];
            if (parse_mod(path, m)) {
                ml_memset(m->folder, 0, sizeof(m->folder));
                if (folder) {
                    for (uint32_t k = 0; k < 23u && folder[k]; k++) m->folder[k] = folder[k];
                }
                c->scan_count++;
            }
        }
    }
    ml_close(dfd);
    for (uint32_t s = 0; s < nsub && c->scan_count < MAX_MODS; s++)
        scan_mods_in(c, sub_paths[s], sub_names[s]);
}

static void scan_dir(Ctx *c, const char *dir) { scan_mods_in(c, dir, 0); }

/* Rescan every folder and merge: enabled mods always stay; disabled ones follow the files. */
static void rescan(Ctx *c) {
    /* Folders in priority order, NUL-separated (built at run time; no pointer table). */
    const char *dirs = "/mnt/mmca1/mods\0/media/mmca1/mods\0/mnt/sd/mods\0/tmp_bt/mods\0";
    int managed = ml_open(MLV2_SD_MANAGED, ML_O_RDONLY);
    if (managed >= 0) {
        ml_close(managed);
        dirs = "/tmp_bt/mods\0";
    }
    c->scan_count = 0;
    for (const char *d = dirs; *d; ) {
        scan_dir(c, d);
        while (*d) d++;
        d++;
    }
    Mod *out = c->scan;
    Mod *keep = c->keep;
    uint32_t keep_n = 0;
    /* Pass 1: enabled mods stay, refreshed with presence / staleness. */
    for (uint32_t i = 0; i < c->mod_count; i++) {
        Mod *m = &c->mods[i];
        if (!m->enabled) continue;
        m->present = 0;
        m->stale = 0;
        for (uint32_t j = 0; j < c->scan_count; j++)
            if (!ml_strcmp(out[j].path, m->path)) { m->present = 1; m->stale = out[j].fnv != m->fnv; out[j].present = 2; }
        ml_memcpy(&keep[keep_n++], m, sizeof(Mod));
    }
    /* Pass 2: disabled entries are replaced by the current files (keeping any v2 mapping). */
    for (uint32_t j = 0; j < c->scan_count && keep_n < MAX_MODS; j++) {
        if (out[j].present == 2) continue;
        Mod *nm = &out[j];
        int known = 0;
        for (uint32_t i = 0; i < c->mod_count; i++)
            if (!ml_strcmp(c->mods[i].path, nm->path)) known = 1;
        if (!known && nm->kind == KIND_V2 && (nm->flags & SMOD_V2_FLAG_AUTO_ON) &&
            !(nm->flags & SMOD_V2_FLAG_ACTION) && !nm->invalid)
            nm->present = 3;              /* first appearance of an auto-on mod: switched ON below */
        for (uint32_t i = 0; i < c->mod_count; i++) {
            Mod *old = &c->mods[i];
            if (old->enabled || ml_strcmp(old->path, nm->path)) continue;
            if (old->fnv == nm->fnv) nm->invalid = old->invalid;   /* unchanged file keeps its ERR state */
            if (old->kind == KIND_V2 && nm->kind == KIND_V2 && old->addr &&
                old->map_size >= nm->code_size + nm->extra_size) {
                nm->addr = old->addr;
                nm->map_size = old->map_size;
            }
        }
        ml_memcpy(&keep[keep_n++], nm, sizeof(Mod));
    }
    /* Sort by display name for a stable menu order. */
    for (uint32_t i = 1; i < keep_n; i++)
        for (uint32_t k = i; k > 0 && ml_strcmp(keep[k - 1].name, keep[k].name) > 0; k--) {
            ml_memcpy(&c->tmp, &keep[k], sizeof(Mod));
            ml_memcpy(&keep[k], &keep[k - 1], sizeof(Mod));
            ml_memcpy(&keep[k - 1], &c->tmp, sizeof(Mod));
        }
    for (uint32_t i = 0; i < keep_n; i++) ml_memcpy(&c->mods[i], &keep[i], sizeof(Mod));
    c->mod_count = keep_n;
}

/* ---------------- menu tree ---------------- */
static void rebuild_view(Ctx *c) {
    c->folder_count = 0;
    for (uint32_t i = 0; i < c->mod_count; i++) {
        const char *f = c->mods[i].folder;
        if (!f[0]) continue;
        int seen = 0;
        for (uint32_t k = 0; k < c->folder_count; k++)
            if (!ml_strcmp(c->folder_names[k], f)) seen = 1;
        if (!seen && c->folder_count < MAX_FOLDERS)
            ml_memcpy(c->folder_names[c->folder_count++], f, 24u);
    }
    if (c->in_folder > c->folder_count) c->in_folder = 0;
    uint32_t n = 0;
    if (!c->in_folder) {
        for (uint32_t f = 0; f < c->folder_count && n < MAX_MODS; f++)
            c->view_map[n++] = (int16_t)(-(int16_t)(f + 2u));
        for (uint32_t i = 0; i < c->mod_count && n < MAX_MODS; i++)
            if (!c->mods[i].folder[0]) c->view_map[n++] = (int16_t)i;
    } else {
        const char *fname = c->folder_names[c->in_folder - 1u];
        c->view_map[n++] = -1;   /* < Back */
        for (uint32_t i = 0; i < c->mod_count && n < MAX_MODS; i++)
            if (!ml_strcmp(c->mods[i].folder, fname)) c->view_map[n++] = (int16_t)i;
    }
    c->view_count = n;
}

static void build_tree(Ctx *c) {
    uint32_t n_items = c->view_count ? c->view_count : 1u;
    uint32_t n_pages = n_items > 12u ? 3u : (n_items > 6u ? 2u : 1u);
    c->page_count = n_pages;
    for (uint32_t i = 0; i < n_items && i < MAX_MODS; i++) {
        uint32_t pg_idx = i >= 12u ? 2u : (i >= 6u ? 1u : 0u);
        uint32_t pos1 = i - pg_idx * 6u + 1u;
        Node *it = &c->items[i];
        it->vtable = c->vtable;
        it->children = c->opt_ptrs;
        it->child_count = 2;
        it->parent = &c->pages[pg_idx];
        it->layer_depth = 4;
        it->selected_idx = 1;
        it->s1c = it->s20 = pos1;
        it->s24 = 1;
        c->item_ptrs[i] = it;
    }
    for (uint32_t o = 0; o < 2u; o++) {
        Node *op = &c->opts[o];
        op->vtable = c->vtable;
        op->children = 0;
        op->child_count = 0;
        op->parent = &c->items[0];
        op->selected_idx = -1;
        op->s1c = op->s20 = o + 1u;
        op->s24 = o ? 1u : 2u;
        c->opt_ptrs[o] = op;
    }
    for (uint32_t p = 0; p < n_pages; p++) {
        uint32_t start = p * 6u, rem = n_items - start;
        Node *pg = &c->pages[p];
        pg->vtable = c->vtable;
        pg->children = &c->item_ptrs[start];
        pg->child_count = (int32_t)(rem > 6u ? 6u : rem);
        pg->parent = &c->tab_node;
        pg->layer_depth = 3;
        pg->selected_idx = 1;
        pg->s1c = pg->s20 = p + 1u;
        pg->s24 = 1;
        c->page_ptrs[p] = pg;
    }
    c->tab_node.vtable = c->vtable;
    c->tab_node.children = c->page_ptrs;
    c->tab_node.child_count = (int32_t)n_pages;
    c->tab_node.layer_depth = 2;
    c->tab_node.selected_idx = 1;
    c->tab_node.s1c = c->tab_node.s20 = 7;
    c->tab_node.s24 = 1;
}

static void toggle(Mod *m);
static int process_reload(Ctx *c);

static int sd_disabled(Ctx *c) {
    int fd = ml_open(MLV2_SD_DISABLED, ML_O_RDONLY);
    if (fd < 0) return 0;
    ml_close(fd);
    for (uint32_t i = 0; i < c->mod_count; i++) {
        Mod *m = &c->mods[i];
        if (m->enabled) toggle(m);
    }
    return 1;
}

static void sd_acknowledge_stop(const Ctx *c) {
    for (uint32_t i = 0; i < c->mod_count; i++)
        if (c->mods[i].enabled) return;
    int fd = ml_open(MLV2_SD_STOPPED, ML_O_WR_CREAT_APP);
    if (fd >= 0) ml_close(fd);
}

static uint32_t core_before_set_root(uint32_t menu, uint32_t root_addr) {
    (void)menu;
    Node *root = (Node *)root_addr;
    if (!root || root_addr < 0x00100000u || (root->child_count != 6 && root->child_count != 7)) return root_addr;
    if (!root->vtable || (uint32_t)root->vtable < 0x00100000u || !root->children ||
        (uint32_t)root->children < 0x00100000u) return root_addr;
    Ctx *c = ctx_get();
    if (!c) return root_addr;
    if (sd_disabled(c)) {
        if (root == c->orig_root && root->children == c->root_children && c->original_children) {
            root->children = c->original_children;
            root->child_count = c->original_child_count;
        }
        if (root->children != c->root_children) sd_acknowledge_stop(c);
        return root_addr;
    }
    if (!c->vtable_ready) {
        for (uint32_t i = 0; i < 65u; i++) c->vtable[i] = root->vtable[i];
        c->vtable_ready = 1;
        vtable_bind(c);
    }
    process_reload(c);
    c->in_folder = 0;
    rescan(c);
    rebuild_view(c);
    for (uint32_t i = 0; i < c->mod_count; i++) {
        Mod *m = &c->mods[i];
        if (m->present != 3) continue;
        m->present = 1;
        if (!m->enabled && !m->invalid) {
            toggle(m);
            ml_log("auto_on", m->enabled ? m->addr : 0u);
        }
    }
    build_tree(c);
    if (root->children != c->root_children) {
        c->orig_root = root;
        c->original_children = root->children;
        c->original_child_count = root->child_count;
        for (uint32_t i = 0; i < 6u; i++) c->root_children[i] = root->children[i];
        c->orig_tab5 = (Node *)root->children[5];
        root->children = c->root_children;
    }
    c->tab_node.parent = root;
    c->root_children[6] = &c->tab_node;
    root->child_count = 7;
    return root_addr;
}

static int is_action(const Mod *m) { return m->kind == KIND_V2 && (m->flags & SMOD_V2_FLAG_ACTION); }

static const char *state_text(const Mod *m) {
    if (m->invalid || (m->enabled && !m->present)) return "ERR";
    if (is_action(m)) return m->enabled ? (m->stale ? "Active*" : "Active") : "";
    if (m->enabled) return m->stale ? "ON*" : "ON";
    return "OFF";
}

/* Post the MENU key to the firmware event manager, as MenuTouchFull does for KEY_MENU (E0520):
 * operator new(16), event constructor (id, type 4, 1), push. ILCE-6400 reference addresses in
 * libObj.so, moved by the live libObj.so base. Returns 1 when the event was queued. */
#ifdef MLV2_TARGET_A6100
#define REF_EVMGR_DIRECT (REF_LIBOBJ_BASE + 0x1425c70u)
#define REF_OP_NEW       (REF_LIBOBJ_BASE + 0x000fd974u)
#define REF_EV_CTOR      (REF_LIBOBJ_BASE + 0x00840f7du)
#define REF_EVMGR_PUSH   (REF_LIBOBJ_BASE + 0x00842529u)
#else
#define REF_EVMGR_SLOT   0x07089984u
#define REF_OP_NEW       0x05e15974u
#define REF_EV_CTOR      0x05e14e20u
#define REF_EVMGR_PUSH   0x05e18d5du
#endif
#define KEY_MENU_EVENT   0x15010102u
static int post_menu_key(void) {
    if (!core_libobj_ok) return 0;
    uint32_t b = core_libobj_bias;
#ifdef MLV2_TARGET_A6100
    uint32_t mgr = *(volatile uint32_t *)(REF_EVMGR_DIRECT + b);
#else
    uint32_t slot = *(volatile uint32_t *)(REF_EVMGR_SLOT + b);
    uint32_t mgr = slot ? *(volatile uint32_t *)slot : 0u;
#endif
    if (!mgr) return 0;
    void *ev = ((void *(*)(uint32_t))(REF_OP_NEW + b))(16u);
    if (!ev) return 0;
    ((void (*)(void *, uint32_t, uint8_t, uint8_t))(REF_EV_CTOR + b))(ev, KEY_MENU_EVENT, 4u, 1u);
    ((void (*)(uint32_t, void *, uint32_t))(REF_EVMGR_PUSH + b))(mgr, ev, 1u);
    return 1;
}

static void refresh_rows(Ctx *c, uint32_t menu, int clear_unused) {
    if (!menu || *(volatile uint32_t *)(menu + 0x1f0u) != 7u) return;
    typedef void (*set_text_t)(void *, const char *);
    set_text_t set_text = (set_text_t)VIEW(MLV2_SET_TEXT);
    set_text((void *)(menu + 0x5470u), c->in_folder ? c->folder_names[c->in_folder - 1u] : "Mods");
    uint32_t page = *(volatile uint32_t *)(menu + 0x1f4u);
    if (!page) page = 1;
    uint32_t base = (page - 1u) * 6u;
    for (uint32_t r = 0; r < 6u; r++) {
        uint32_t btn = menu + 0x9350u + r * 0x1118u, idx = base + r;
        if (idx < c->view_count) {
            int16_t v = c->view_map[idx];
            if (v == -1) {
                set_text((void *)(btn + 0x7c8u), "< Back");
                set_text((void *)(btn + 0xb60u), "");
            } else if (v <= -2) {
                const char *fname = c->folder_names[(uint32_t)(-v - 2)];
                const char *val = ">";
                for (uint32_t i = 0; i < c->mod_count; i++)
                    if (c->mods[i].enabled && !ml_strcmp(c->mods[i].folder, fname)) { val = c->mods[i].name; break; }
                set_text((void *)(btn + 0x7c8u), fname);
                set_text((void *)(btn + 0xb60u), val);
            } else {
                set_text((void *)(btn + 0x7c8u), c->mods[v].name);
                set_text((void *)(btn + 0xb60u), state_text(&c->mods[v]));
            }
        } else if (!r && !c->view_count) {
            set_text((void *)(btn + 0x7c8u), "No mods found");
            set_text((void *)(btn + 0xb60u), "");
        } else if (clear_unused) {
            set_text((void *)(btn + 0x7c8u), "");
            set_text((void *)(btn + 0xb60u), "");
        }
    }
}

static void core_after_refresh(uint32_t menu) {
    Ctx *c = ctx_get();
    if (!c) return;
    if (sd_disabled(c)) return;
    process_reload(c);   /* in place: a new file name waits for the next root setup */
    c->last_menu = menu;
    if (c->orig_root && c->orig_root->children == c->root_children) {
        c->root_children[6] = &c->tab_node;
        c->orig_root->child_count = 7;
    }
    refresh_rows(c, menu, 0);
}

/* ---------------- loading and toggling ---------------- */
static void hook_bytes(const Mod *m, const Hook *hk, uint8_t *bytes) {
    ml_memcpy(bytes, hk->patch, SMOD_MAX_PATCH);
    if (m->kind == KIND_V2 && hk->fixup != SMOD_V2_NO_FIXUP) {
        uint32_t entry;
        ml_memcpy(&entry, bytes + hk->fixup, 4);
        entry += m->addr;
        ml_memcpy(bytes + hk->fixup, &entry, 4);
    }
}

static int hooks_revert(Mod *m, uint32_t upto) {
    int failed = 0;
    for (uint32_t h = 0; h < upto; h++) {
        Hook *hk = &m->hooks[h];
        void *target = (void *)hk->target;
        if (!ml_memcmp(target, hk->orig, hk->size)) continue;
        uint8_t bytes[SMOD_MAX_PATCH];
        hook_bytes(m, hk, bytes);
        if (ml_memcmp(target, bytes, hk->size) || ml_make_rwx(hk->target, hk->size) < 0) {
            failed = 1;
            continue;
        }
        ml_memcpy(target, hk->orig, hk->size);
        ml_flush_all(hk->target, hk->target + hk->size);
        if (ml_memcmp(target, hk->orig, hk->size)) failed = 1;
    }
    return failed;
}

static void call_entry(uint32_t addr, int arg) { ((void (*)(int))(addr | 1u))(arg); }

static int load_code(Mod *m) {
    uint32_t dst = m->addr, need = m->code_size + m->extra_size;
    if (m->kind == KIND_V2 && (!dst || m->map_size < need)) {
        dst = ml_mmap_rwx(need);
        if (!dst) return 4;
        m->addr = dst;
        m->map_size = (need + 0xfffu) & ~0xfffu;   /* old mappings are never unmapped */
    } else if (m->kind == KIND_V1 && ml_make_rwx(dst, m->code_size) < 0) {
        return 4;
    }
    int fd = ml_open(m->path, ML_O_RDONLY);
    if (fd < 0) return 3;
    ml_lseek(fd, m->data_off);
    int n = ml_read(fd, (void *)dst, m->code_size);
    if (m->kind == KIND_V1 && (m->flags & SMOD_V1_FLAG_STATE_INIT)) {
        if (ml_read(fd, (void *)m->state_addr, m->extra_size) != (int)m->extra_size) n = -1;
    } else if (m->kind == KIND_V1 && m->state_addr && m->extra_size) {
        ml_memset((void *)m->state_addr, 0, m->extra_size);
    } else if (m->kind == KIND_V2 && m->extra_size) {
        ml_memset((void *)(dst + m->code_size), 0, m->extra_size);
    }
    ml_close(fd);
    if (n != (int)m->code_size) return 5;
    if (ml_fnv1a((const uint8_t *)dst, m->code_size) != m->fnv) return 6;
    if (m->kind == KIND_V2 && (m->flags & SMOD_V2_FLAG_RELOC)) {
        SmodRelocLib libs[SMOD_RELOC_MAX_LIBS];
        uint32_t bias[SMOD_RELOC_MAX_LIBS];
        fd = ml_open(m->path, ML_O_RDONLY);
        if (fd < 0) return 3;
        ml_lseek(fd, m->data_off + m->code_size);
        uint32_t nlib = 0;
        int nrel = reloc_apply(fd, m->code_size, (uint8_t *)dst, libs, bias, &nlib);
        ml_close(fd);
        if (nrel < 0) return 8;
    }
    ml_flush_all(dst, dst + m->code_size);
    return 0;
}

/* Apply the hooks of a mod whose code is loaded and verified, then run its on() entry.
 * The caller has checked that every target still holds its original bytes. */
static void enable_loaded(Mod *m) {
    for (uint32_t h = 0; h < m->hook_count; h++) {
        Hook *hk = &m->hooks[h];
        uint8_t bytes[SMOD_MAX_PATCH];
        hook_bytes(m, hk, bytes);
        if (ml_make_rwx(hk->target, hk->size) < 0) {
            m->enabled = hooks_revert(m, h);
            m->off_called = 1;  /* ON lifecycle never ran. */
            ml_log("tgl_err", 7u);
            m->invalid = 1;
            return;
        }
        ml_memcpy((void *)hk->target, bytes, hk->size);
        ml_flush_all(hk->target, hk->target + hk->size);
    }
    m->enabled = 1;
    m->off_called = 0;
    if (m->kind == KIND_V2 && m->on_off) call_entry(m->addr + m->on_off, 1);
    if (m->kind == KIND_V1 && (m->flags & SMOD_V1_FLAG_LIFECYCLE)) call_entry(m->addr, 1);
    ml_log("mod_on", m->addr);
}

static void toggle(Mod *m) {
    if (!m->enabled) {
        if (m->invalid) return;
        if (m->stale) { m->invalid = 1; return; }
        for (uint32_t h = 0; h < m->hook_count; h++)
            if (ml_memcmp((void *)m->hooks[h].target, m->hooks[h].orig, m->hooks[h].size)) {
                ml_log("tgl_err", 2u);   /* target bytes differ: another patch is present */
                m->invalid = 1;
                return;
            }
        int rc = load_code(m);
        if (rc) { ml_log("tgl_err", (uint32_t)rc); if (rc != 3) m->invalid = 1; return; }
        enable_loaded(m);
    } else {
        if (!m->off_called) {
            if (m->kind == KIND_V2 && m->off_off) call_entry(m->addr + m->off_off, 0);
            if (m->kind == KIND_V1 && (m->flags & SMOD_V1_FLAG_LIFECYCLE)) call_entry(m->addr, 0);
            m->off_called = 1;
        }
        if (hooks_revert(m, m->hook_count)) {
            m->invalid = 1;
            ml_log("mod_off_failed", m->addr);
            return;
        }
        m->invalid = 0;
        m->enabled = 0;
        ml_log("mod_off", m->addr);
    }
}


/* ---------------- reload ---------------- */
static int targets_original(const Mod *m) {
    for (uint32_t h = 0; h < m->hook_count; h++)
        if (ml_memcmp((void *)m->hooks[h].target, m->hooks[h].orig, m->hooks[h].size)) return 0;
    return 1;
}

/* Every hook of the new version must either replace an old hook exactly (same target, size and
 * original bytes) or sit on bytes that are original now and do not overlap an old hook. */
static int reload_targets_ok(const Mod *old, const Mod *n) {
    for (uint32_t h = 0; h < n->hook_count; h++) {
        const Hook *hk = &n->hooks[h];
        int covered = 0;
        for (uint32_t k = 0; k < old->hook_count; k++) {
            const Hook *ok = &old->hooks[k];
            if (ok->target == hk->target) {
                if (ok->size != hk->size || ml_memcmp(ok->orig, hk->orig, hk->size)) return 0;
                covered = 1;
            } else if (ok->target < hk->target + hk->size && hk->target < ok->target + ok->size) {
                return 0;
            }
        }
        if (!covered && ml_memcmp((void *)hk->target, hk->orig, hk->size)) return 0;
    }
    return 1;
}

static void reload_result(const char *msg, uint32_t val) {
    ml_log_to(MLV2_RELOAD_RESULT, ML_O_WR_CREAT_TRUNC, msg, val);
    ml_log(msg, val);
}

/* Replace the enabled mod `name` with its current file. Order:
 *   1. parse the new file, require SMOD v2, load it into a fresh mapping and verify its hash;
 *   2. check the new hook targets against the old hooks and the live bytes;
 *   3. switch the old copy OFF (off() entry, revert, readback); stop if that fails;
 *   4. re-check that every new target holds its original bytes, apply the new hooks, run on().
 * Any failure before step 3 leaves the old copy running unchanged. The old mapping is never
 * unmapped, so a thread still inside an old hook finishes in valid code; the new copy starts
 * with fresh (zeroed) bss, so no gesture or other state crosses versions. */
static void reload_mod(Ctx *c, const char *name) {
    uint32_t i = 0;
    while (i < c->mod_count && ml_strcmp(base_name(c->mods[i].path), name)) i++;
    if (i >= c->mod_count || !c->mods[i].enabled) { reload_result("reload_scan", i < c->mod_count); return; }
    Mod *m = &c->mods[i], *n = &c->tmp;
    if (m->invalid) { reload_result("reload_err_state", 0); return; }
    if (!parse_mod(m->path, n)) { reload_result("reload_err_file", 0); return; }
    if (n->kind != KIND_V2) { reload_result("reload_err_kind", n->kind); return; }
    n->addr = 0;
    n->map_size = 0;
    int rc = load_code(n);
    if (rc) { reload_result("reload_err_load", (uint32_t)rc); return; }
    if (!reload_targets_ok(m, n)) { reload_result("reload_err_target", 0); return; }
    toggle(m);
    if (m->enabled || !targets_original(m)) { reload_result("reload_err_off", m->addr); return; }
    ml_memcpy(n->folder, m->folder, sizeof(n->folder));
    ml_memcpy(m, n, sizeof(Mod));
    if (!targets_original(m)) { m->invalid = 1; reload_result("reload_err_target", 1); return; }
    enable_loaded(m);
    if (!m->enabled) { reload_result("reload_err_on", m->addr); return; }
    reload_result("reload_ok", m->addr);
}

/* Consume a pending reload request. Returns 1 if one was present. */
static int process_reload(Ctx *c) {
    int fd = ml_open(MLV2_RELOAD_REQ, ML_O_RDONLY);
    if (fd < 0) return 0;
    char name[48];
    ml_memset(name, 0, sizeof(name));
    int n = ml_read(fd, name, sizeof(name) - 1u);
    ml_close(fd);
    if (ml_unlink(MLV2_RELOAD_REQ) < 0) { reload_result("reload_err_unlink", 0); return 1; }
    int bad = n <= 0;
    for (int k = 0; k < n && !bad; k++) {
        if (name[k] == '\n' || name[k] == '\r') { name[k] = 0; break; }
        if (name[k] == '/' || name[k] < ' ') bad = 1;
    }
    if (bad || !ends_with_mod(name)) { reload_result("reload_err_request", (uint32_t)n); return 1; }
    reload_mod(c, name);
    return 1;
}

static int core_before_enter(uint32_t menu, uint32_t leaf) {
    Ctx *c = ctx_get();
    if (!c) return -1;
    int disabled = sd_disabled(c);
    uint32_t lo = (uint32_t)&c->items[0], hi = (uint32_t)&c->items[MAX_MODS];
    if (leaf < lo || leaf >= hi) return -1;
    if (disabled) return 0;
    uint32_t idx = 0;
    while (idx < MAX_MODS && (uint32_t)&c->items[idx] != leaf) idx++;
    if (idx >= c->view_count) return 0;
    int16_t v = c->view_map[idx];
    if (v < 0) {
        c->in_folder = (v == -1) ? 0u : (uint8_t)(-v - 1);
        *(volatile uint32_t *)(menu + 0x1f4u) = 1u;
        rebuild_view(c);
        build_tree(c);
        refresh_rows(c, menu, 1);
        return 0;
    }
    Mod *m = &c->mods[v];
    if (!m->enabled && m->folder[0]) {
        for (uint32_t i = 0; i < c->mod_count; i++)
            if (&c->mods[i] != m && c->mods[i].enabled && !ml_strcmp(c->mods[i].folder, m->folder))
                toggle(&c->mods[i]);
    }
    int was_on = m->enabled;
    toggle(m);
    if (is_action(m) && !was_on && m->enabled)
        ml_log(post_menu_key() ? "action_close" : "action_close_unavailable", m->addr);
    refresh_rows(c, menu, 0);
    return 0;
}

/* ---------------- init / takeover / upgrade ---------------- */
static void import_v1(Ctx *c) {
    volatile uint32_t *anchor = (volatile uint32_t *)VIEW(MLV2_V1_ANCHOR);
    if (anchor[0] != MLV2_V1_MAGIC || !anchor[1]) return;
    V1State *v = (V1State *)anchor[1];
    for (uint32_t i = 0; i < v->mod_count && i < 12u && c->mod_count < MAX_MODS; i++) {
        V1Mod *o = &v->mods[i];
        if (!o->enabled) continue;
        Mod *m = &c->mods[c->mod_count++];
        ml_memset(m, 0, sizeof(*m));
        ml_memcpy(m->name, o->name, 31);
        ml_memcpy(m->path, o->path, 63);
        m->kind = KIND_V1;
        m->enabled = 1;
        m->flags = o->pad;          /* v1 stored the SMOD flags in pad */
        m->hook_count = o->hook_count;
        m->code_size = o->code_size;
        m->addr = o->code_addr;
        m->state_addr = o->state_addr;
        m->extra_size = o->state_size;
        m->fnv = o->fnv1a_32;
        m->present = 1;
        m->data_off = 68u + o->hook_count * 40u;
        for (uint32_t h = 0; h < o->hook_count && h < SMOD_MAX_HOOKS; h++) {
            m->hooks[h].target = o->hooks[h].target_addr;
            m->hooks[h].size = (uint8_t)o->hooks[h].patch_size;
            m->hooks[h].fixup = SMOD_V2_NO_FIXUP;
            ml_memcpy(m->hooks[h].orig, o->hooks[h].orig_bytes, SMOD_MAX_PATCH);
            ml_memcpy(m->hooks[h].patch, o->hooks[h].hook_bytes, SMOD_MAX_PATCH);
        }
        ml_log("v1_import", m->addr);
    }
}

__attribute__((section(".text.core_init"), used))
int core_init(ShimState *s) {
    int layout = s->layout_magic == MLV2_LAYOUT_MAGIC;
    core_view_bias = layout ? s->view_bias : 0u;
    core_slot = MLV2_REF_SHIM_ADDR + (layout ? s->testcmd_bias : 0u);
    Ctx *c = (Ctx *)s->ctx;
    if (c && (c->magic != CTX_MAGIC || c->version != CTX_VERSION)) {
        for (uint32_t i = 0; i < MAX_MODS; i++)
            if (c->magic == CTX_MAGIC && i < c->mod_count && c->mods[i].enabled) { ml_log("core_refuse", c->version); return -1; }
        c = 0;
    }
    if (!c) {
        c = (Ctx *)ml_mmap_rwx(CTX_BYTES);
        if (!c) return -2;
        ml_memset(c, 0, sizeof(*c));
        c->magic = CTX_MAGIC;
        c->version = CTX_VERSION;
        import_v1(c);
        /* Fresh install (no v1): provide the anchor block the tab artwork uses for its cache. */
        volatile uint32_t *anchor = (volatile uint32_t *)VIEW(MLV2_V1_ANCHOR);
        if (anchor[0] != MLV2_V1_MAGIC || !anchor[1]) {
            uint32_t blk = ml_mmap_rwx(MLV2_V1_STATE_BYTES);
            if (blk && ml_make_rwx(VIEW(MLV2_V1_ANCHOR), 8u) == 0) { anchor[1] = blk; anchor[0] = MLV2_V1_MAGIC; }
        }
    }
    vtable_bind(c);
    s->ctx = c;
    s->before_set_root = core_before_set_root;
    s->after_refresh = core_after_refresh;
    s->before_enter = core_before_enter;
    ml_log("core_init", CTX_VERSION);
    if (core_view_bias) ml_log("core_view_bias", core_view_bias);
    {
        SmodRelocLib lib;
        uint32_t base = 0;
        ml_memset(&lib, 0, sizeof(lib));
        lib.ref_lo = REF_LIBOBJ_BASE;
        lib.ref_hi = REF_LIBOBJ_BASE + 1u;
        ml_memcpy(lib.name, "libObj.so", 10);
        if (maps_bases(&lib, 1, &base)) {
            core_libobj_bias = base - REF_LIBOBJ_BASE;
            core_libobj_ok = 1;
        }
        ml_log(mtl_present() ? "tab_icon_mtl" : "tab_icon_tab6", core_libobj_bias);
    }
    return 0;
}
