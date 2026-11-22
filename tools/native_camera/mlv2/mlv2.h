/* Mod Loader v2: shared ABI between the shim (fixed cave), the core (position-independent,
 * loaded into mmap memory) and SMOD v2 mods (position-independent, loaded by the core).
 * See docs/MOD_LOADER_V2_DESIGN.md.
 */
#ifndef MLV2_H
#define MLV2_H
#include <stdint.h>

/* ---- Firmware addresses (ILCE-6400 reference layout, identity-checked by the guard) ----
 * Another process layout (QEMU) runs the same libraries at other bases. The guard relocates the
 * shim (see package.py: the shim is linked twice and every word that moves with the view base or
 * with the shim address is recorded), and the shim publishes the differences in ShimState. */
#define MLV2_REF_VIEW_BASE    0x155e6000u
#define MLV2_REF_TESTCMD_BASE 0x05660000u
#ifndef MLV2_VIEW_BASE
#define MLV2_VIEW_BASE        MLV2_REF_VIEW_BASE
#endif
#ifdef MLV2_TARGET_A6100
#define MLV2_SET_TEXT_OFF     0x150030u
#define MLV2_SET_MODE_OFF     0x1563c8u
#define MLV2_V1_ANCHOR        (MLV2_REF_VIEW_BASE + 0xb7a658u)
#else
#define MLV2_SET_TEXT_OFF     0x14f6d0u
#define MLV2_SET_MODE_OFF     0x155a68u
#define MLV2_V1_ANCHOR        0x1615ec40u   /* v1 anchor {magic, state}; also used by the tab icon */
#endif
#define MLV2_SET_TEXT         (MLV2_VIEW_BASE + MLV2_SET_TEXT_OFF + 1u)
#define MLV2_SET_MODE         (MLV2_VIEW_BASE + MLV2_SET_MODE_OFF + 1u)
#define MLV2_V1_MAGIC         0x444f4d53u   /* "SMOD" */
#define MLV2_V1_STATE_BYTES   8192u         /* tab icon cache lives at state + 8180 */

/* ---- Shim ---- */
#ifndef MLV2_SHIM_ADDR
#define MLV2_SHIM_ADDR        0x05662800u   /* /usr/lib/libtestcmd.so RX zero tail (>0x2790 EOF), 2048 bytes */
#endif
#define MLV2_REF_SHIM_ADDR    0x05662800u
#define MLV2_SHIM_CAP         2048u
#define MLV2_SHIM_SLOT        MLV2_SHIM_ADDR  /* first word of the shim: ShimState pointer */
#define MLV2_CORE_PATH        "/tmp_bt/mlv2/core.bin"
#define MLV2_SD_MANAGED       "/tmp_bt/mlv2/sd-managed"
#define MLV2_SD_DISABLED      "/tmp_bt/mlv2/sd-disabled"
#define MLV2_SD_STOPPED       "/tmp_bt/mlv2/sd-stopped"
/* Reload request: one mod file name (no directory). The core consumes it on the UI thread. */
#define MLV2_RELOAD_REQ       "/tmp_bt/mlv2/reload"
#define MLV2_RELOAD_RESULT    "/tmp_bt/mlv2/reload.result"

/* ---- Core image file ---- */
#define MLV2_CORE_MAGIC       0x52434c4du   /* "MLCR" */
#define MLV2_CORE_VERSION     2u
#define MLV2_CORE_MAX         (64u * 1024u)
typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t code_size;      /* bytes following this header */
    uint32_t fnv1a_32;       /* of the code bytes */
    uint32_t init_off;       /* Thumb entry offset (bit 0 set) of core_init(ShimState *) */
} MlCoreHeader;

/* ---- State owned by the shim (one mmap page, never freed) ---- */
#define MLV2_SHIM_MAGIC       0x4853324du   /* "M2SH" */
typedef struct ShimState {
    uint32_t magic;
    uint32_t core_fnv;                     /* fnv of the core currently in use (0 = none) */
    uint32_t core_base;                    /* mmap base of the current core (old cores stay mapped) */
    uint32_t core_loads;                   /* number of successful core loads */
    void    *ctx;                          /* core context (versioned, survives core upgrades) */
    uint32_t (*before_set_root)(uint32_t menu, uint32_t root);
    void     (*after_refresh)(uint32_t menu);
    int      (*before_enter)(uint32_t menu, uint32_t leaf);
    /* Layout ABI 1 (zero in states created by older shims): runtime base - reference base. */
    uint32_t layout_magic;                 /* MLV2_LAYOUT_MAGIC */
    uint32_t view_bias;                    /* viewUnified2.so */
    uint32_t testcmd_bias;                 /* libtestcmd.so (the shim cave) */
} ShimState;
#define MLV2_LAYOUT_MAGIC     0x3159414cu   /* "LAY1" */

/* ---- SMOD file formats ---- */
#define SMOD_MAGIC            0x444f4d53u   /* "SMOD" */
#define SMOD_MAX_HOOKS        8u
#define SMOD_MAX_PATCH        16u
#define SMOD_V1_FLAG_STATE_INIT 1u
#define SMOD_V1_FLAG_LIFECYCLE  2u

/* v1 header (68 bytes) and hook record (40 bytes): fixed-address mods. */
typedef struct __attribute__((packed)) {
    uint32_t magic, version, flags;
    char     name[32];
    uint32_t code_addr, code_size, state_addr, state_size, hook_count, fnv1a_32;
} SmodV1Header;
typedef struct __attribute__((packed)) {
    uint32_t target_addr, patch_size;
    uint8_t  orig_bytes[SMOD_MAX_PATCH];
    uint8_t  hook_bytes[SMOD_MAX_PATCH];
} SmodV1Hook;

/* v2 header (68 bytes) and hook record (40 bytes): position-independent mods.
 * Code follows the hooks; bss_size zero bytes follow the code in the same RWX mapping.
 * on_off / off_off are Thumb entry offsets (bit 0 set) or 0 for "none".
 * Hook: the core copies tmpl into the target. If fixup_off < patch_size - 3, the 32-bit word
 * at tmpl[fixup_off] is an offset into the mod and is replaced by (base + offset). */
#define SMOD_V2_NO_FIXUP      0xffu
/* flags bit 2: a relocation table follows the code in the file (not loaded):
 *   u32 "RLC1", u16 nlib, u16 nrel,
 *   nlib x { u32 ref_lo, ref_hi; char name[24] }      reference-layout ranges by library file name
 *   nrel x u32 (code offset | lib << 24)              32-bit words that hold reference addresses
 *   u32 fnv1a_32 of the table bytes before it.
 * The core adds (runtime base - reference base) of the named library, taken from /proc/self/maps
 * (base = first mapping at file offset 0), to each listed word after the code hash check, and to
 * every hook target that falls inside a listed range. A library that is not mapped refuses the mod. */
#define SMOD_V2_FLAG_RELOC    4u
/* flags bit 3: switch ON automatically the first time the core lists the file (once per
 * appearance; a user OFF sticks until the file is removed and added again). */
#define SMOD_V2_FLAG_AUTO_ON  8u
/* flags bit 4: command item instead of an ON/OFF switch. The Mods row shows no ON/OFF value
 * (empty while idle, "Active" while running). Pressing an idle item switches it ON and, when ON
 * succeeded, posts the MENU key so the menu closes to the shooting screen. Pressing a running item
 * switches it OFF and keeps the menu open. Auto-on is ignored for command items. */
#define SMOD_V2_FLAG_ACTION   16u
#define SMOD_RELOC_MAGIC      0x31434c52u   /* "RLC1" */
#define SMOD_RELOC_MAX_LIBS   8u
#define SMOD_RELOC_MAX        1024u
typedef struct __attribute__((packed)) {
    uint32_t ref_lo, ref_hi;
    char     name[24];
} SmodRelocLib;
typedef struct __attribute__((packed)) {
    uint32_t magic, version, flags;
    char     name[32];
    uint32_t code_size, fnv1a_32, bss_size, on_off, off_off, hook_count;
} SmodV2Header;
typedef struct __attribute__((packed)) {
    uint32_t target_addr;
    uint8_t  patch_size, fixup_off, pad[2];
    uint8_t  orig_bytes[SMOD_MAX_PATCH];
    uint8_t  tmpl[SMOD_MAX_PATCH];
} SmodV2Hook;

_Static_assert(sizeof(SmodV1Header) == 68 && sizeof(SmodV2Header) == 68, "SMOD header size");
_Static_assert(sizeof(SmodV1Hook) == 40 && sizeof(SmodV2Hook) == 40, "SMOD hook size");

#endif
