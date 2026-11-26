/* Reversible RAM-only 7th "Mods" tab & SD /mods loader guard for Sony A6400. */
#define _DEFAULT_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ptrace.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
#include "mods_tab_look_layout.h"

#define CODE            0x0412f3d0u
#define THEMED_CODE     0x045e43a0u
#define CODE_CAP        3120u
#define ANCHOR          ML_ANCHOR_ADDR
#define ANCHOR_SIZE     8u
#define HOOK_SET_ROOT   0x15b91654u
#define HOOK_REFRESH    0x15b91a80u
#if defined(ML_HOOK_PMAIN)
#define HOOK_OPEN_ITEM  0x158d8df4u /* viewUnified2.so + 0x2f2df4 ViewBaseProduct::ProductMain */
#elif defined(ML_HOOK_DISPATCH)
#define HOOK_OPEN_ITEM  0x157f955eu /* viewUnified2.so + 0x21355e action TBH dispatcher */
#else
#define HOOK_OPEN_ITEM  0x15b92044u /* viewUnified2.so + 0x5ac044 enter_selected */
#endif
#define RECT_TBL1_SLOT6 0x15de8a04u
#define RECT_TBL2_SLOT6 0x15de8a84u
#define REQ_MAGIC       0x4d4c4452u /* "MLDR" */
#define PROTO_VER       1u
#define THEME_PROTO_VER 2u

static const unsigned char orig_set_root[16] = {
    0x04, 0x46, 0x00, 0xaf, 0x51, 0xb1, 0x00, 0xf5, 0xc2, 0x33, 0x03, 0xf5, 0x2b, 0x73, 0x19, 0x60
};
static const unsigned char orig_refresh[16] = {
    0x2d, 0xe9, 0xf0, 0x4f, 0x9b, 0xb0, 0x06, 0xaf, 0x04, 0x46, 0x07, 0xf1, 0x20, 0x00, 0x4f, 0xf0
};
#if defined(ML_HOOK_PMAIN)
static const unsigned char orig_open_item[12] = {
    0x2d, 0xe9, 0xf0, 0x4f, 0xa3, 0xb0, 0x04, 0xaf, 0x05, 0x46, 0x88, 0x46
};
#elif defined(ML_HOOK_DISPATCH)
static const unsigned char orig_open_item[12] = {
    0xb0, 0xb5, 0x04, 0x46, 0x00, 0xaf, 0x8e, 0x29, 0x00, 0xf2, 0x0f, 0x83
};
#else
static const unsigned char orig_open_item[12] = {
    0x00, 0xf5, 0xc2, 0x33, 0x03, 0xf5, 0x2c, 0x73, 0x2d, 0xe9, 0xf0, 0x43
};
#endif
static const unsigned char orig_rect[8]  = {0, 0, 0, 0, 0, 0, 0, 0};
static const unsigned char patch_rect[8] = {0x15, 0x02, 0x00, 0x00, 0x5b, 0x00, 0x42, 0x00}; /* x=533, y=0, w=91, h=66 */
static const unsigned char orig_theme_hook[12] = {
    0xf8, 0xb5, 0x00, 0xaf, 0x0d, 0x46, 0x04, 0x46, 0xff, 0xf7, 0xe2, 0xff
};

static unsigned char payload[CODE_CAP], theme[MTL_CODE_CAP], zeros[MTL_CODE_CAP], buffer[MTL_CODE_CAP];
_Static_assert(CODE_CAP <= MTL_CODE_CAP, "guard buffer must hold either payload");
static pid_t freeze_tids[512];
static unsigned freeze_count, freeze_attached;
static unsigned char freeze_owned[512], freeze_stopped[512];
static unsigned long long freeze_begin;

static unsigned long long freeze_ms(void) {
    struct { uint32_t sec; uint32_t nsec; } t = {0, 0};
    if (syscall(SYS_clock_gettime, 1, &t)) return 0;
    return (unsigned long long)t.sec * 1000ull + (unsigned long long)t.nsec / 1000000ull;
}

static int freeze_enumerate(pid_t *out, unsigned *count) {
    DIR *dir = opendir("/proc/149/task");
    struct dirent *entry;
    unsigned n = 0;
    if (!dir) return -90;
    while ((entry = readdir(dir))) {
        char *end;
        long id = strtol(entry->d_name, &end, 10);
        if (!*entry->d_name || *end || id <= 0) continue;
        if (n >= 512) { closedir(dir); return -91; }
        out[n++] = (pid_t)id;
    }
    closedir(dir);
    *count = n;
    return 0;
}

static int freeze_resume(void) {
    int result = 0;
    unsigned resumed = 0;
    for (unsigned i = freeze_attached; i; i--) {
        unsigned n = i - 1;
        int status;
        if (!freeze_owned[n]) continue;
        if (!freeze_stopped[n]) {
            pid_t got = waitpid(freeze_tids[n], &status, __WALL | WNOHANG);
            if (got == freeze_tids[n] && WIFSTOPPED(status)) freeze_stopped[n] = 1;
            else if (got == freeze_tids[n] && (WIFEXITED(status) || WIFSIGNALED(status))) {
                freeze_owned[n] = 0;
                continue;
            } else { result = -92; continue; }
        }
        if (ptrace(PTRACE_DETACH, freeze_tids[n], 0, 0)) result = -92;
        else { freeze_owned[n] = freeze_stopped[n] = 0; resumed++; }
    }
    printf("RESUME count=%u result=%d\n", resumed, result);
    return result;
}

static int freeze_process(void) {
    memset(freeze_owned, 0, sizeof(freeze_owned));
    memset(freeze_stopped, 0, sizeof(freeze_stopped));
    freeze_count = freeze_attached = 0;
    freeze_begin = freeze_ms();
    int err = freeze_enumerate(freeze_tids, &freeze_count);
    if (err) return err;
    for (unsigned i = 0; i < freeze_count; i++) {
        if (ptrace(PTRACE_ATTACH, freeze_tids[i], 0, 0)) { freeze_resume(); return -93; }
        freeze_owned[i] = 1;
        freeze_attached++;
        int status;
        if (waitpid(freeze_tids[i], &status, __WALL) != freeze_tids[i] || !WIFSTOPPED(status)) {
            freeze_resume();
            return -94;
        }
        freeze_stopped[i] = 1;
    }
    pid_t after[512];
    unsigned after_count = 0;
    err = freeze_enumerate(after, &after_count);
    if (err || after_count != freeze_count) { freeze_resume(); return err ? err : -95; }
    for (unsigned i = 0; i < freeze_count; i++) {
        if (after[i] != freeze_tids[i]) { freeze_resume(); return -95; }
    }
    printf("FREEZE threads=%u elapsed_ms=%llu\n", freeze_count, freeze_ms() - freeze_begin);
    return 0;
}

static int readmem(int fd, uint32_t addr, void *out, size_t len) {
    return pread(fd, out, len, (off_t)addr) != (ssize_t)len;
}

static int poke(uint32_t addr, const void *data, size_t len) {
    for (size_t off = 0; off < len; off += 4) {
        uint32_t word = 0;
        memcpy(&word, (const unsigned char *)data + off, len - off < 4 ? len - off : 4);
        if (ptrace(PTRACE_POKETEXT, freeze_tids[0], (void *)(uintptr_t)(addr + off), (void *)(uintptr_t)word)) return -1;
    }
    return 0;
}

static int maps_ok(int themed) {
    FILE *f = fopen("/proc/149/maps", "r");
    if (!f) return 0;
    char line[256], perms[5];
    unsigned lo, hi;
    int code = 0, anchor = 0, view = 0, theme_code = 0, theme_hook = 0;
    unsigned code_base = themed ? THEMED_CODE : CODE;
    const char *code_lib = themed ? "/libmprctrl.so" : "/libMWF.so";
    while (fgets(line, sizeof(line), f)) {
        if (sscanf(line, "%x-%x %4s", &lo, &hi, perms) == 3) {
            if (lo <= code_base && code_base + CODE_CAP <= hi && !strcmp(perms, "r-xp") && strstr(line, code_lib)) code = 1;
            if (lo <= ANCHOR && ANCHOR + ANCHOR_SIZE <= hi && !strcmp(perms, "rw-p")) anchor = 1;
            if (lo <= HOOK_SET_ROOT && HOOK_OPEN_ITEM + 16 <= hi && !strcmp(perms, "r-xp") && strstr(line, "/viewUnified2.so")) view = 1;
            if (lo <= MTL_CODE_ADDR && MTL_CODE_ADDR + MTL_CODE_CAP <= hi &&
                !strcmp(perms, "r-xp") && strstr(line, "/libptp.so")) theme_code = 1;
            if (lo <= MTL_HOOK_ADDR - 2u && MTL_HOOK_ADDR + 14u <= hi &&
                !strcmp(perms, "r-xp") && strstr(line, "/libObj.so")) theme_hook = 1;
        }
    }
    fclose(f);
    return code && anchor && view && (!themed || (theme_code && theme_hook));
}

static void make_branch(unsigned char out[12], uint32_t target) {
    unsigned char tmpl[12] = {0xdf, 0xf8, 0, 0xf0, 0, 0, 0, 0, 0, 0xbf, 0, 0xbf};
    memcpy(tmpl + 4, &target, 4);
    memcpy(out, tmpl, 12);
}

static uint32_t checksum(const unsigned char *bytes, uint32_t size) {
    uint32_t sum = 2166136261u;
    for (uint32_t i = 0; i < size; i++) sum = (sum ^ bytes[i]) * 16777619u;
    return sum;
}

static int thumb_entry(uint32_t entry, uint32_t base, uint32_t size) {
    return (entry & 1u) && entry >= base && entry - base < size;
}

struct ram_patch {
    uint32_t address, size;
    const unsigned char *stock, *installed;
};

static int write_patch(int fd, const struct ram_patch *patch, const unsigned char *data) {
    if (patch->address == MTL_HOOK_ADDR) {
        /* POKETEXT on aligned words, preserving the two adjacent Thumb halfwords. */
        unsigned char words[16];
        if (readmem(fd, MTL_HOOK_ADDR - 2u, words, sizeof(words))) return -1;
        memcpy(words + 2, data, patch->size);
        if (poke(MTL_HOOK_ADDR - 2u, words, sizeof(words))) return -1;
    } else if (poke(patch->address, data, patch->size)) {
        return -1;
    }
    return readmem(fd, patch->address, buffer, patch->size) || memcmp(buffer, data, patch->size);
}

static int themed_operation(int fd, uint32_t op, const struct ram_patch *patches, unsigned count) {
    /* Once the loader builds fake menu nodes the view can retain them. Restart to unload. */
    if (readmem(fd, ANCHOR, buffer, ANCHOR_SIZE) || memcmp(buffer, zeros, ANCHOR_SIZE)) return -24;
    for (unsigned i = 0; i < count; i++) {
        const struct ram_patch *p = &patches[i];
        if (readmem(fd, p->address, buffer, p->size) ||
            memcmp(buffer, op == 2u ? p->installed : p->stock, p->size)) {
            printf("PATCH_MISMATCH address=%08x\n", p->address);
            return -12;
        }
    }
    if (op == 0u) return 0;
    for (unsigned i = 0; i < count; i++) {
        const struct ram_patch *p = &patches[op == 2u ? count - 1u - i : i];
        if (!write_patch(fd, p, op == 2u ? p->stock : p->installed)) continue;
        int rollback_failed = 0;
        for (unsigned j = i + 1u; j; j--) {
            p = &patches[op == 2u ? count - j : j - 1u];
            if (write_patch(fd, p, op == 2u ? p->installed : p->stock)) rollback_failed = 1;
        }
        printf("ROLLBACK result=%d\n", rollback_failed);
        return rollback_failed ? -26 : -25;
    }
    return 0;
}

int main(void) {
    uint32_t h[9], t[3], marker;
    FILE *f = fopen("/tmp_bt/mod_loader.req", "rb");
    if (!f) return 2;
    if (fread(h, 1, sizeof(h), f) != sizeof(h) || h[0] != REQ_MAGIC ||
        (h[1] != PROTO_VER && h[1] != THEME_PROTO_VER) ||
        h[2] > 2u || h[3] != (h[1] == THEME_PROTO_VER ? THEMED_CODE : CODE) ||
        !h[4] || h[4] > CODE_CAP || (h[4] % 4u) ||
        !thumb_entry(h[5], h[3], h[4]) || !thumb_entry(h[6], h[3], h[4]) ||
        !thumb_entry(h[7], h[3], h[4]) || fread(payload, 1, CODE_CAP, f) != CODE_CAP) {
        fclose(f);
        return 3;
    }
    int themed = h[1] == THEME_PROTO_VER;
    if (themed) {
        if (fread(t, 1, sizeof(t), f) != sizeof(t) || t[0] < 8u ||
            t[0] > MTL_CODE_CAP || t[0] % 4u || !thumb_entry(t[1], MTL_CODE_ADDR + 4u, t[0] - 4u) ||
            fread(theme, 1, t[0], f) != t[0]) {
            fclose(f);
            return 3;
        }
        memcpy(&marker, theme, sizeof(marker));
        if (marker != MTL_BUILTIN_MARKER || checksum(theme, t[0]) != t[2]) {
            fclose(f);
            return 4;
        }
    }
    int trailing = fgetc(f);
    fclose(f);
    if (trailing != EOF) return 3;
    if (checksum(payload, h[4]) != h[8]) return 4;

    char comm[32] = {0};
    f = fopen("/proc/149/comm", "r");
    if (!f) return 5;
    int identity = fgets(comm, sizeof(comm), f) && !strcmp(comm, "im.elf\n");
    fclose(f);
    if (!identity) return 5;

    unsigned char br_sr[16], br_rf[16], br_oi[12];
    make_branch(br_sr, h[5]);
    br_sr[12] = 0x00; br_sr[13] = 0xbf; br_sr[14] = 0x00; br_sr[15] = 0xbf;
    make_branch(br_rf, h[6]);
    br_rf[12] = 0x00; br_rf[13] = 0xbf; br_rf[14] = 0x4f; br_rf[15] = 0xf0;
    {
        /* 4-byte aligned target: ldr.w pc, [pc, #0]; .word entry; original push.w kept intact */
#if defined(ML_HOOK_PMAIN)
        /* 4-aligned: ldr.w pc, [pc, #0]; .word entry; original mov r5, r0; mov r8, r1 kept (skipped) */
        unsigned char tmpl2[12] = {0xdf, 0xf8, 0x00, 0xf0, 0, 0, 0, 0, 0x05, 0x46, 0x88, 0x46};
        memcpy(tmpl2 + 4, &h[7], 4);
#elif defined(ML_HOOK_DISPATCH)
        /* HOOK_OPEN_ITEM is 2 mod 4: nop; ldr.w pc, [pc, #0] (aligned); .word entry; nop */
        unsigned char tmpl2[12] = {0x00, 0xbf, 0xdf, 0xf8, 0x00, 0xf0, 0, 0, 0, 0, 0x00, 0xbf};
        memcpy(tmpl2 + 6, &h[7], 4);
#else
        unsigned char tmpl2[12] = {0xdf, 0xf8, 0x00, 0xf0, 0, 0, 0, 0, 0x2d, 0xe9, 0xf0, 0x43};
        memcpy(tmpl2 + 4, &h[7], 4);
#endif
        memcpy(br_oi, tmpl2, 12);
    }

    int result = freeze_process(), fd = -1, changed = 0;
    if (result) goto end;
    fd = open("/proc/149/mem", h[2] ? O_RDWR : O_RDONLY);
    if (fd < 0) { result = -10; goto done; }
    if (!maps_ok(themed)) { result = -11; goto done; }

    if (themed) {
        unsigned char br_theme[12] = {0x00, 0xbf, 0xdf, 0xf8, 0x00, 0xf0, 0, 0, 0, 0, 0x00, 0xbf};
        memcpy(br_theme + 6, &t[1], 4);
        const struct ram_patch patches[] = {
            {THEMED_CODE, h[4], zeros, payload},
            {MTL_CODE_ADDR, t[0], zeros, theme},
            {MTL_HOOK_ADDR, sizeof(br_theme), orig_theme_hook, br_theme},
            {RECT_TBL1_SLOT6, sizeof(patch_rect), orig_rect, patch_rect},
            {RECT_TBL2_SLOT6, sizeof(patch_rect), orig_rect, patch_rect},
            {HOOK_SET_ROOT, sizeof(br_sr), orig_set_root, br_sr},
            {HOOK_REFRESH, sizeof(br_rf), orig_refresh, br_rf},
            {HOOK_OPEN_ITEM, sizeof(br_oi), orig_open_item, br_oi},
        };
        result = themed_operation(fd, h[2], patches, sizeof(patches) / sizeof(patches[0]));
        if (result) goto done;
        changed = h[2] != 0u;
        printf("THEME_OK code=%08x..%08x hook=%08x\n", MTL_CODE_ADDR, MTL_CODE_ADDR + t[0], MTL_HOOK_ADDR);
        if (changed)
            printf("%s hooks=%08x,%08x,%08x\n", h[2] == 2u ? "REMOVED" : "INSTALLED",
                   HOOK_SET_ROOT, HOOK_REFRESH, HOOK_OPEN_ITEM);
        else
            printf("CHECK_OK code=%08x..%08x bytes=%u\n", THEMED_CODE, THEMED_CODE + h[4], h[4]);
        goto done;
    }

    if (readmem(fd, HOOK_SET_ROOT, buffer, 16) || memcmp(buffer, h[2] == 2u ? br_sr : orig_set_root, 16) ||
        readmem(fd, HOOK_REFRESH, buffer, 16) || memcmp(buffer, h[2] == 2u ? br_rf : orig_refresh, 16) ||
        readmem(fd, HOOK_OPEN_ITEM, buffer, 12) || memcmp(buffer, h[2] == 2u ? br_oi : orig_open_item, 12) ||
        readmem(fd, RECT_TBL1_SLOT6, buffer, 8) || memcmp(buffer, h[2] == 2u ? patch_rect : orig_rect, 8) ||
        readmem(fd, RECT_TBL2_SLOT6, buffer, 8) || memcmp(buffer, h[2] == 2u ? patch_rect : orig_rect, 8)) {
        result = -12;
        goto done;
    }

    if (h[2] == 2u) {
        if (poke(HOOK_SET_ROOT, orig_set_root, 16) || poke(HOOK_REFRESH, orig_refresh, 16) ||
            poke(HOOK_OPEN_ITEM, orig_open_item, 12) ||
            poke(RECT_TBL1_SLOT6, orig_rect, 8) || poke(RECT_TBL2_SLOT6, orig_rect, 8) ||
            poke(CODE, zeros, h[4]) || pwrite(fd, zeros, ANCHOR_SIZE, ANCHOR) != ANCHOR_SIZE) {
            result = -19;
            goto done;
        }
        changed = 1;
        printf("REMOVED hooks=%08x,%08x,%08x\n", HOOK_SET_ROOT, HOOK_REFRESH, HOOK_OPEN_ITEM);
    } else if (h[2] == 1u) {
        if (pwrite(fd, zeros, ANCHOR_SIZE, ANCHOR) != ANCHOR_SIZE ||
            poke(CODE, payload, h[4]) ||
            poke(RECT_TBL1_SLOT6, patch_rect, 8) || poke(RECT_TBL2_SLOT6, patch_rect, 8) ||
            poke(HOOK_SET_ROOT, br_sr, 16) || poke(HOOK_REFRESH, br_rf, 16) ||
            poke(HOOK_OPEN_ITEM, br_oi, 12)) {
            result = -21;
            goto done;
        }
        changed = 1;
        printf("INSTALLED hooks=%08x,%08x,%08x\n", HOOK_SET_ROOT, HOOK_REFRESH, HOOK_OPEN_ITEM);
    } else {
        printf("CHECK_OK code=%08x..%08x bytes=%u\n", CODE, CODE + h[4], h[4]);
    }

done:
    if (fd >= 0) close(fd);
    int rerr = freeze_resume();
    if (!result && rerr) result = rerr;
end:
    printf("MOD_LOADER_RESULT=%d changed=%d\n", result, changed);
    return result ? 1 : 0;
}
