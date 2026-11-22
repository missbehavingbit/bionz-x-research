/* Mod Loader v2 shim: code in the libtestcmd.so cave (see mlv2.h), relocated by the guard.
 *
 * The three view hooks (mlv2_trampoline.S) land here. The shim keeps a ShimState in mmap
 * memory (pointer in the first word of the cave) and loads the core from MLV2_CORE_PATH.
 * When the menu root is set it re-reads the core header; a different hash loads the new core
 * into fresh memory and lets it adopt the context. Old cores are never unmapped, so code that
 * the menu still references stays valid. Without a core the shim behaves like stock firmware.
 */
#include "mlv2.h"
#include "mlv2_sys.h"

__attribute__((section(".text.slot"), used)) const volatile uint32_t mlv2_slot = 0;
/* Both words move with their base when the guard relocates the shim (package.py records them). */
__attribute__((section(".text.slotb"), used)) const volatile uint32_t mlv2_view_bias = MLV2_VIEW_BASE - MLV2_REF_VIEW_BASE;
__attribute__((section(".text.slotb"), used)) const volatile uint32_t mlv2_testcmd_bias = MLV2_SHIM_ADDR - MLV2_REF_SHIM_ADDR;

static ShimState *shim_state(int create) {
    volatile uint32_t *slot = (volatile uint32_t *)&mlv2_slot;
    ShimState *s = (ShimState *)*slot;
    if (s && s->magic == MLV2_SHIM_MAGIC) {
        if (s->layout_magic != MLV2_LAYOUT_MAGIC) {      /* state from an older shim */
            s->view_bias = mlv2_view_bias;
            s->testcmd_bias = mlv2_testcmd_bias;
            s->layout_magic = MLV2_LAYOUT_MAGIC;
        }
        return s;
    }
    if (!create) return 0;
    uint32_t m = ml_mmap_rwx(4096u);
    if (!m || ml_make_rwx((uint32_t)&mlv2_slot, 4u) < 0) return 0;
    s = (ShimState *)m;
    ml_memset(s, 0, sizeof(*s));
    s->magic = MLV2_SHIM_MAGIC;
    s->view_bias = mlv2_view_bias;
    s->testcmd_bias = mlv2_testcmd_bias;
    s->layout_magic = MLV2_LAYOUT_MAGIC;
    *slot = m;
    return s;
}

static void shim_try_core(ShimState *s) {
    int fd = ml_open(MLV2_CORE_PATH, ML_O_RDONLY);
    if (fd < 0) return;
    MlCoreHeader h;
    if (ml_read(fd, &h, sizeof(h)) != (int)sizeof(h) || h.magic != MLV2_CORE_MAGIC ||
        h.version != MLV2_CORE_VERSION || !h.code_size || h.code_size > MLV2_CORE_MAX ||
        (h.code_size & 3u) || !(h.init_off & 1u) || h.init_off >= h.code_size || h.fnv1a_32 == s->core_fnv) {
        ml_close(fd);
        return;
    }
    uint32_t base = ml_mmap_rwx(h.code_size);
    int n = base ? ml_read(fd, (void *)base, h.code_size) : -1;
    ml_close(fd);
    if (n != (int)h.code_size || ml_fnv1a((const uint8_t *)base, h.code_size) != h.fnv1a_32) {
        ml_log("core_bad", h.fnv1a_32);
        return;
    }
    ml_flush_all(base, base + h.code_size);
    int rc = ((int (*)(ShimState *))(base + h.init_off))(s);
    if (rc) { ml_log("core_reject", (uint32_t)rc); return; }   /* previous core stays active */
    s->core_fnv = h.fnv1a_32;
    s->core_base = base;
    s->core_loads++;
    ml_log("core_load", h.fnv1a_32);
}

uint32_t ml_before_set_root(uint32_t menu, uint32_t root) {
    ShimState *s = shim_state(1);
    if (!s) return root;
    shim_try_core(s);
    return s->before_set_root ? s->before_set_root(menu, root) : root;
}

void ml_after_refresh(uint32_t menu) {
    int fd = ml_open(MLV2_SD_DISABLED, ML_O_RDONLY);
    if (fd < 0)
        ((void (*)(void *, int))MLV2_SET_MODE)((void *)(menu + 0x97cu), 3);   /* as in v1 */
    else
        ml_close(fd);
    ShimState *s = shim_state(0);
    if (s && s->after_refresh) s->after_refresh(menu);
}

int ml_before_enter(uint32_t menu, uint32_t leaf) {
    ShimState *s = shim_state(0);
    return (s && s->before_enter) ? s->before_enter(menu, leaf) : -1;
}
