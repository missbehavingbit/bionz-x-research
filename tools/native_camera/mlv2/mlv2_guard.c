/* Mod Loader v2 guard (RAM-only): installs the v2 shim and points the three view hooks at it.
 *
 * Usage: mlv2_guard check|install|upgrade REQUEST_FILE   (or: mlv2_guard fnv FILE)
 *   check   : report hook state (stock / v1 / v2) and shim cave state; no writes
 *   install : stock hooks -> write shim, tab rectangle and hooks (fresh camera)
 *   upgrade : v1 hooks    -> write shim and redirect the hooks. The v1 code, v1 state and
 *             anchor stay untouched, so menu nodes built by v1 stay valid. No restart and no
 *             "menu closed" condition is needed.
 * All writes happen with every im.elf thread stopped, are read back, and roll back on error.
 *
 * Request file: "ML2Q", version 1 or 2, shim_addr, shim_size, v2 entries (set_root, refresh,
 * open_item), shim fnv, v1 build count N (1..4), N v1 entry triples (set_root, refresh,
 * open_item: one per known v1 build), then the shim bytes. Version 2 appends a relocation count
 * and that many words (offset | kind << 30; kind 0 = libtestcmd.so, 1 = viewUnified2.so).
 *
 * Target: the single process whose comm is "im.elf". All addresses above are the ILCE-6400
 * reference layout (viewUnified2.so text at 0x155e6000, libtestcmd.so at 0x05660000). The guard
 * reads the target's maps, takes each library's base (its first mapping at file offset 0) and
 * adds the difference to every hook, table, shim and entry address; version 2 shim words are
 * relocated by kind. A version 1 request is accepted only when both differences are zero.
 * The original byte checks below are unchanged, so a different library build is refused.
 * Before writing, every stopped thread's PC is checked: a thread paused inside a patched hook
 * window or the cave refuses the write (-17, retry), so no thread resumes in half-patched code.
 */
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

#define SHIM_ADDR             0x05662800u
#define SHIM_CAP              2048u
#define REQ_MAGIC             0x5132484du /* "ML2Q" */
#define REF_VIEW_BASE         0x155e6000u
#define REF_TESTCMD_BASE      0x05660000u
#ifdef MLV2_TARGET_A6100
#define HOOK_SET_ROOT         (REF_VIEW_BASE + 0x5acc0cu)
#define HOOK_REFRESH          (REF_VIEW_BASE + 0x5ad038u)
#define HOOK_OPEN_ITEM        (REF_VIEW_BASE + 0x5ad5fcu)
#define RECT_TBL1_SLOT6       (REF_VIEW_BASE + 0x803984u)
#define RECT_TBL2_SLOT6       (REF_VIEW_BASE + 0x803a04u)
#define RECT_TBL1_SLOT6_FW200 (REF_VIEW_BASE + 0x803984u)
#define RECT_TBL2_SLOT6_FW200 (REF_VIEW_BASE + 0x803a04u)
#else
#define HOOK_SET_ROOT         0x15b91654u
#define HOOK_REFRESH          0x15b91a80u
#define HOOK_OPEN_ITEM        0x15b92044u
#define RECT_TBL1_SLOT6       0x15de8a04u
#define RECT_TBL2_SLOT6       0x15de8a84u
#define RECT_TBL1_SLOT6_FW200 0x15de8a64u
#define RECT_TBL2_SLOT6_FW200 0x15de8ae4u
#endif
#define MAX_RELOCS            256u

static const unsigned char orig_set_root[16] = {
    0x04, 0x46, 0x00, 0xaf, 0x51, 0xb1, 0x00, 0xf5, 0xc2, 0x33, 0x03, 0xf5, 0x2b, 0x73, 0x19, 0x60};
static const unsigned char orig_refresh[16] = {
    0x2d, 0xe9, 0xf0, 0x4f, 0x9b, 0xb0, 0x06, 0xaf, 0x04, 0x46, 0x07, 0xf1, 0x20, 0x00, 0x4f, 0xf0};
static const unsigned char orig_open_item[12] = {
    0x00, 0xf5, 0xc2, 0x33, 0x03, 0xf5, 0x2c, 0x73, 0x2d, 0xe9, 0xf0, 0x43};
static const unsigned char orig_rect_prefix[48] = {
    0x11, 0x00, 0x00, 0x00, 0x5b, 0x00, 0x42, 0x00, 0x67, 0x00, 0x00, 0x00, 0x5b, 0x00, 0x42, 0x00,
    0xbd, 0x00, 0x00, 0x00, 0x5b, 0x00, 0x42, 0x00, 0x13, 0x01, 0x00, 0x00, 0x5b, 0x00, 0x42, 0x00,
    0x69, 0x01, 0x00, 0x00, 0x5b, 0x00, 0x42, 0x00, 0xbf, 0x01, 0x00, 0x00, 0x5b, 0x00, 0x42, 0x00};
static const unsigned char orig_rect[8] = {0};
static const unsigned char patch_rect[8] = {0x15, 0x02, 0x00, 0x00, 0x5b, 0x00, 0x42, 0x00};

/* Target process. MLV2_HOST_TEST builds (host test harness only) take the PID from MLV2_PID and
 * the PC register index of the host ABI; camera builds are fixed to im.elf (PID 149), ARM r15. */
#ifdef MLV2_HOST_TEST
#define PC_INDEX 16 /* x86_64 user_regs_struct.rip */
#else
#define PC_INDEX 15 /* ARM r15 */
#endif
static char proc_dir[32];
static uint32_t view_bias, testcmd_bias;   /* runtime base - reference base */
#define SHIM_AT   (SHIM_ADDR + testcmd_bias)
#define V(a)      ((uint32_t)(a) + view_bias)
static char proc_path_buf[64];
static const char *proc_path(const char *leaf) {
    snprintf(proc_path_buf, sizeof(proc_path_buf), "%s/%s", proc_dir, leaf);
    return proc_path_buf;
}

static unsigned char shim[SHIM_CAP], zeros[SHIM_CAP], buffer[SHIM_CAP];
static pid_t tids[512];
static unsigned tid_count, attached;
static unsigned char owned[512], stopped[512];

/* ---- thread freeze (same protocol as the v1 guard) ---- */
static int enumerate(pid_t *out, unsigned *count) {
    DIR *dir = opendir(proc_path("task"));
    struct dirent *e;
    unsigned n = 0;
    if (!dir) return -90;
    while ((e = readdir(dir))) {
        char *end;
        long id = strtol(e->d_name, &end, 10);
        if (!*e->d_name || *end || id <= 0) continue;
        if (n >= 512) { closedir(dir); return -91; }
        out[n++] = (pid_t)id;
    }
    closedir(dir);
    *count = n;
    return 0;
}

static int resume(void) {
    int result = 0;
    unsigned resumed = 0;
    for (unsigned i = attached; i; i--) {
        unsigned n = i - 1;
        int st;
        if (!owned[n]) continue;
        if (!stopped[n]) {
            pid_t got = waitpid(tids[n], &st, __WALL | WNOHANG);
            if (got == tids[n] && WIFSTOPPED(st)) stopped[n] = 1;
            else if (got == tids[n] && (WIFEXITED(st) || WIFSIGNALED(st))) { owned[n] = 0; continue; }
            else { result = -92; continue; }
        }
        if (ptrace(PTRACE_DETACH, tids[n], 0, 0)) result = -92;
        else { owned[n] = stopped[n] = 0; resumed++; }
    }
    printf("RESUME count=%u result=%d\n", resumed, result);
    return result;
}

static int freeze(void) {
    memset(owned, 0, sizeof(owned));
    memset(stopped, 0, sizeof(stopped));
    tid_count = attached = 0;
    int err = enumerate(tids, &tid_count);
    if (err) return err;
    for (unsigned i = 0; i < tid_count; i++) {
        if (ptrace(PTRACE_ATTACH, tids[i], 0, 0)) { resume(); return -93; }
        owned[i] = 1;
        attached++;
        int st;
        if (waitpid(tids[i], &st, __WALL) != tids[i] || !WIFSTOPPED(st)) { resume(); return -94; }
        stopped[i] = 1;
    }
    pid_t after[512];
    unsigned after_count = 0;
    err = enumerate(after, &after_count);
    if (err || after_count != tid_count) { resume(); return err ? err : -95; }
    for (unsigned i = 0; i < tid_count; i++) if (after[i] != tids[i]) { resume(); return -95; }
    printf("FREEZE threads=%u\n", tid_count);
    return 0;
}

static int readmem(int fd, uint32_t a, void *out, size_t n) { return pread(fd, out, n, (off_t)a) != (ssize_t)n; }

static int poke(uint32_t a, const void *data, size_t n) {
    for (size_t off = 0; off < n; off += 4) {
        unsigned long w = 0;
        if (sizeof(long) > 4) { /* host test ABI: keep the bytes above this 32-bit word */
            errno = 0;
            w = (unsigned long)ptrace(PTRACE_PEEKTEXT, tids[0], (void *)(uintptr_t)(a + off), 0);
            if (errno) return -1;
        }
        memcpy(&w, (const unsigned char *)data + off, n - off < 4 ? n - off : 4);
        if (ptrace(PTRACE_POKETEXT, tids[0], (void *)(uintptr_t)(a + off), (void *)w)) return -1;
    }
    return 0;
}

static uint32_t fnv(const unsigned char *b, uint32_t n) {
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < n; i++) h = (h ^ b[i]) * 16777619u;
    return h;
}

/* Hook byte sequences: ldr.w pc, [pc, #0]; .word entry; then the original tail. */
static void branches(uint32_t sr, uint32_t rf, uint32_t oi, unsigned char b_sr[16], unsigned char b_rf[16],
                     unsigned char b_oi[12]) {
    static const unsigned char head[4] = {0xdf, 0xf8, 0x00, 0xf0};
    memcpy(b_sr, head, 4); memcpy(b_sr + 4, &sr, 4);
    memcpy(b_sr + 8, "\x00\xbf\x00\xbf\x00\xbf\x00\xbf", 8);
    memcpy(b_rf, head, 4); memcpy(b_rf + 4, &rf, 4);
    memcpy(b_rf + 8, "\x00\xbf\x00\xbf\x00\xbf\x4f\xf0", 8);
    memcpy(b_oi, head, 4); memcpy(b_oi + 4, &oi, 4);
    memcpy(b_oi + 8, "\x2d\xe9\xf0\x43", 4);
}

struct patch { uint32_t addr, size; const unsigned char *from, *to; };

/* ARM user_regs: r0..r15, cpsr, orig_r0. Refuse when a thread is paused strictly inside a hook
 * window (its first instruction is fine: it then executes the new branch) or inside the cave. */
static int pcs_clear(void) {
    const uint32_t win[4][2] = {{V(HOOK_SET_ROOT), 16}, {V(HOOK_REFRESH), 16}, {V(HOOK_OPEN_ITEM), 12}, {SHIM_AT, SHIM_CAP}};
    for (unsigned i = 0; i < tid_count; i++) {
        unsigned long regs[64];
        if (ptrace(PTRACE_GETREGS, tids[i], 0, regs)) return -17;
        uint32_t pc = (uint32_t)regs[PC_INDEX] & ~1u;
        for (unsigned w = 0; w < 4; w++) {
            uint32_t lo = win[w][0] + (w < 3 ? 1u : 0u);
            if (pc >= lo && pc < win[w][0] + win[w][1]) {
                printf("BUSY tid=%d pc=%08x\n", (int)tids[i], pc);
                return -17;
            }
        }
    }
    return 0;
}

/* Library base = start of its first mapping at file offset 0. Returns 0 if missing. */
static uint32_t lib_base(const char *leaf) {
    FILE *f = fopen(proc_path("maps"), "r");
    if (!f) return 0;
    char line[512];
    unsigned lo, hi, off;
    uint32_t base = 0;
    size_t n = strlen(leaf);
    while (!base && fgets(line, sizeof(line), f)) {
        size_t len = strcspn(line, "\n");
        line[len] = 0;
        if (sscanf(line, "%x-%x %*4s %x", &lo, &hi, &off) == 3 && !off && len > n &&
            !strcmp(line + len - n, leaf) && line[len - n - 1] == '/')
            base = lo;
    }
    fclose(f);
    return base;
}

/* Is [a, a + n) inside one mapping of the named library (readable and executable if perm is set)? */
static int mapped(uint32_t a, uint32_t n, const char *leaf, const char *perm) {
    FILE *f = fopen(proc_path("maps"), "r");
    if (!f) return 0;
    char line[512], perms[5];
    unsigned lo, hi;
    int ok = 0;
    while (!ok && fgets(line, sizeof(line), f))
        if (sscanf(line, "%x-%x %4s", &lo, &hi, perms) == 3 && lo <= a && a + n <= hi &&
            (!*perm || (perms[0] == 'r' && perms[2] == 'x')) && strstr(line, leaf)) ok = 1;
    fclose(f);
    return ok;
}

/* Select the single im.elf process and compute the library differences. */
static int find_target(void) {
#ifdef MLV2_HOST_TEST
    if (!getenv("MLV2_PID")) return -1;
    snprintf(proc_dir, sizeof(proc_dir), "/proc/%s", getenv("MLV2_PID"));
#else
    DIR *d = opendir("/proc");
    struct dirent *e;
    int found = 0;
    if (!d) return -1;
    while ((e = readdir(d))) {
        char *end, path[64], comm[32] = {0};
        long id = strtol(e->d_name, &end, 10);
        if (!*e->d_name || *end || id <= 0) continue;
        snprintf(path, sizeof(path), "/proc/%ld/comm", id);
        FILE *f = fopen(path, "r");
        if (!f) continue;
        if (fgets(comm, sizeof(comm), f) && !strcmp(comm, "im.elf\n")) {
            found++;
            snprintf(proc_dir, sizeof(proc_dir), "/proc/%ld", id);
        }
        fclose(f);
    }
    closedir(d);
    if (found != 1) return -1;
#endif
    char comm[32] = {0};
    FILE *f = fopen(proc_path("comm"), "r");
    int is_im = f && fgets(comm, sizeof(comm), f) && !strcmp(comm, "im.elf\n");
    if (f) fclose(f);
    uint32_t vb = lib_base("viewUnified2.so"), tb = lib_base("libtestcmd.so");
    if (!is_im || !vb || !tb) return -1;
    view_bias = vb - REF_VIEW_BASE;
    testcmd_bias = tb - REF_TESTCMD_BASE;
    return 0;
}

static int maps_ok(void) {
    return mapped(SHIM_AT, SHIM_CAP, "/libtestcmd.so", "x") &&
           mapped(V(HOOK_SET_ROOT), V(HOOK_OPEN_ITEM) + 16 - V(HOOK_SET_ROOT), "/viewUnified2.so", "x") &&
           mapped(V(RECT_TBL1_SLOT6), V(RECT_TBL2_SLOT6_FW200) + 8 - V(RECT_TBL1_SLOT6), "/viewUnified2.so", "");
}

/* "fnv FILE": size and FNV-1a of a file, for checking transfers on guests without hash tools. */
static int file_fnv(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) { printf("FNV missing\n"); return 6; }
    uint32_t h = 2166136261u, size = 0;
    int c;
    while ((c = fgetc(f)) != EOF) { h = (h ^ (uint32_t)c) * 16777619u; size++; }
    fclose(f);
    printf("FNV size=%u fnv=%08x\n", size, h);
    return 0;
}

int main(int argc, char **argv) {
    if (argc == 3 && !strcmp(argv[1], "fnv")) return file_fnv(argv[2]);
    int op = argc == 3 ? (!strcmp(argv[1], "check") ? 0 : !strcmp(argv[1], "install") ? 1 :
                          !strcmp(argv[1], "upgrade") ? 2 : -1) : -1;
    if (op < 0) { fprintf(stderr, "usage: mlv2_guard check|install|upgrade REQUEST\n"); return 2; }
    uint32_t h[9], v1[4][3], nrel = 0, rel[MAX_RELOCS];
    FILE *f = fopen(argv[2], "rb");
    int ok = f && fread(h, 4, 9, f) == 9 && h[0] == REQ_MAGIC && (h[1] == 1u || h[1] == 2u) && h[2] == SHIM_ADDR &&
        h[3] && h[3] <= SHIM_CAP && !(h[3] & 3u) && h[8] && h[8] <= 4u && fread(v1, 12, h[8], f) == h[8] &&
        fread(shim, 1, h[3], f) == h[3];
    if (ok && h[1] == 2u)
        ok = fread(&nrel, 4, 1, f) == 1 && nrel <= MAX_RELOCS && fread(rel, 4, nrel, f) == nrel;
    for (uint32_t i = 0; ok && i < nrel; i++) {
        uint32_t off = rel[i] & 0x3fffffffu;
        ok = off >= 4u && off + 4u <= h[3] && !(off & 3u) && (rel[i] >> 30) <= 1u;
    }
    if (!ok || fgetc(f) != EOF || fnv(shim, h[3]) != h[7] || memcmp(shim, zeros, 4)) {
        if (f) fclose(f);
        printf("MLV2_RESULT=-3 request invalid\n");
        return 3;
    }
    fclose(f);
    for (int i = 4; i < 7; i++)
        if (!(h[i] & 1u) || h[i] < SHIM_ADDR || h[i] >= SHIM_ADDR + h[3]) { printf("MLV2_RESULT=-3 entries\n"); return 3; }
    if (find_target() || !maps_ok() || (h[1] == 1u && (view_bias || testcmd_bias))) {
        printf("MLV2_RESULT=-5 identity/maps\n");
        return 5;
    }
    printf("TARGET %s view_bias=%08x testcmd_bias=%08x\n", proc_dir, view_bias, testcmd_bias);
    for (uint32_t i = 0; i < nrel; i++) {
        uint32_t off = rel[i] & 0x3fffffffu, w;
        memcpy(&w, shim + off, 4);
        w += (rel[i] >> 30) ? view_bias : testcmd_bias;
        memcpy(shim + off, &w, 4);
    }
    for (int i = 4; i < 7; i++) h[i] += testcmd_bias;

    unsigned char v2_sr[16], v2_rf[16], v2_oi[12], v1_sr[16], v1_rf[16], v1_oi[12];
    branches(h[4], h[5], h[6], v2_sr, v2_rf, v2_oi);

    int fd = open(proc_path("mem"), O_RDONLY);
    if (fd < 0) { printf("MLV2_RESULT=-10\n"); return 10; }
    int result = 0, changed = 0;
    if (op && (result = freeze())) { close(fd); printf("MLV2_RESULT=%d freeze\n", result); return 1; }

    unsigned char cur_sr[16], cur_rf[16], cur_oi[12], cur_rect1[8], cur_rect2[8];
    if (readmem(fd, V(HOOK_SET_ROOT), cur_sr, 16) || readmem(fd, V(HOOK_REFRESH), cur_rf, 16) ||
        readmem(fd, V(HOOK_OPEN_ITEM), cur_oi, 12) || readmem(fd, V(RECT_TBL1_SLOT6), cur_rect1, 8) ||
        readmem(fd, V(RECT_TBL2_SLOT6), cur_rect2, 8) || readmem(fd, SHIM_AT, buffer, h[3])) { result = -11; goto done; }
    const char *state = !memcmp(cur_sr, orig_set_root, 16) && !memcmp(cur_rf, orig_refresh, 16) && !memcmp(cur_oi, orig_open_item, 12) ? "stock"
                      : !memcmp(cur_sr, v2_sr, 16) && !memcmp(cur_rf, v2_rf, 16) && !memcmp(cur_oi, v2_oi, 12) ? "v2" : "unknown";
    int v1_build = -1;
    for (unsigned i = 0; i < h[8] && !strcmp(state, "unknown"); i++) {
        branches(v1[i][0], v1[i][1], v1[i][2], v1_sr, v1_rf, v1_oi);
        if (!memcmp(cur_sr, v1_sr, 16) && !memcmp(cur_rf, v1_rf, 16) && !memcmp(cur_oi, v1_oi, 12)) { state = "v1"; v1_build = (int)i; }
    }
    const char *cave = !memcmp(buffer, zeros, h[3]) ? "zero" : !memcmp(buffer + 4, shim + 4, h[3] - 4) ? "shim" : "other";
    printf("STATE hooks=%s v1_build=%d shim=%s\n", state, v1_build, cave);
    if (op == 0) goto done;
    if (!strcmp(state, "v2") && !strcmp(cave, "shim")) { printf("ALREADY_INSTALLED\n"); goto done; }
    if (strcmp(cave, "zero")) { result = -12; goto done; }
    if ((op == 1 && strcmp(state, "stock")) || (op == 2 && strcmp(state, "v1"))) { result = -13; goto done; }
    const unsigned char *rect_from = op == 1 ? orig_rect : patch_rect;
    uint32_t rect1_addr = V(RECT_TBL1_SLOT6), rect2_addr = V(RECT_TBL2_SLOT6);
    if (memcmp(cur_rect1, rect_from, 8) || memcmp(cur_rect2, rect_from, 8)) {
        unsigned char pre1[48], pre2[48], fw_r1[8], fw_r2[8];
        if (readmem(fd, V(RECT_TBL1_SLOT6_FW200) - 48u, pre1, 48) ||
            readmem(fd, V(RECT_TBL1_SLOT6_FW200), fw_r1, 8) ||
            readmem(fd, V(RECT_TBL2_SLOT6_FW200) - 48u, pre2, 48) ||
            readmem(fd, V(RECT_TBL2_SLOT6_FW200), fw_r2, 8)) { result = -11; goto done; }
        if (memcmp(pre1, orig_rect_prefix, 48) || memcmp(pre2, orig_rect_prefix, 48) ||
            memcmp(fw_r1, rect_from, 8) || memcmp(fw_r2, rect_from, 8)) { result = -14; goto done; }
        rect1_addr = V(RECT_TBL1_SLOT6_FW200);
        rect2_addr = V(RECT_TBL2_SLOT6_FW200);
    }
    if ((result = pcs_clear())) goto done;

    struct patch plan[6] = {
        {SHIM_AT, h[3], zeros, shim},
        {rect1_addr, 8, rect_from, patch_rect},
        {rect2_addr, 8, rect_from, patch_rect},
        {V(HOOK_SET_ROOT), 16, op == 1 ? orig_set_root : v1_sr, v2_sr},
        {V(HOOK_REFRESH), 16, op == 1 ? orig_refresh : v1_rf, v2_rf},
        {V(HOOK_OPEN_ITEM), 12, op == 1 ? orig_open_item : v1_oi, v2_oi},
    };
    for (unsigned i = 0; i < 6; i++) {
        if (poke(plan[i].addr, plan[i].to, plan[i].size) || readmem(fd, plan[i].addr, buffer, plan[i].size) ||
            memcmp(buffer, plan[i].to, plan[i].size)) {
            int rb = 0;
            for (unsigned j = i + 1; j; j--) if (poke(plan[j - 1].addr, plan[j - 1].from, plan[j - 1].size)) rb = 1;
            printf("ROLLBACK step=%u failed=%d\n", i, rb);
            result = rb ? -16 : -15;
            goto done;
        }
    }
    changed = 1;
    printf("%s shim=%08x..%08x hooks=%08x,%08x,%08x\n", op == 1 ? "INSTALLED" : "UPGRADED",
           SHIM_AT, SHIM_AT + h[3], h[4], h[5], h[6]);
done:
    if (op) { int r = resume(); if (!result) result = r; }
    close(fd);
    printf("MLV2_RESULT=%d changed=%d\n", result, changed);
    return result ? 1 : 0;
}
