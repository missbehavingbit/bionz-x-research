/* Exact-version, reversible RAM-only dual-hook menu touch guard — Sony ZV-E10.
 * Operates purely in DRAM via ptrace on im.elf. Zero flash writes.
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
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define OFFSET_CODE         0x011964f0u  /* libObj.so RX zero padding (4-byte aligned, 16,252 bytes) */
#define OFFSET_STATE        0x013f8000u  /* libObj.so .data zero padding (2,923 bytes) */
#define OFFSET_HOOK_REL     0x00618000u  /* dispatchMouseRelease (matches a6400 0x6309d48) */
#define OFFSET_HOOK_PRS     0x0061810cu  /* dispatchMousePress (matches a6400 0x6309e54) */

#define HOOK_SIZE           12u
#define CODE_CAP            8192u
#define STATE_CAP           1760u
#define STATE_SIZE          656u
#define REQ_MAGIC           0x4d544652u  /* "MTFR" */
#define STATE_MAGIC         0x4d544632u  /* "MTF2" */
#define PROTO_VER           2u
#define EVENT_CAP           16u

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
    struct mt_full_event events[EVENT_CAP];
};

/* Verified 12-byte original prologues (identical to a6400 MTF2 architecture) */
static const unsigned char orig_rel[12] = {
    0x2d, 0xe9, 0xf0, 0x47, 0x88, 0xb0, 0x4f, 0xf0, 0x14, 0x08, 0x01, 0x22
};
static const unsigned char orig_prs[12] = {
    0x2d, 0xe9, 0xf0, 0x47, 0x88, 0xb0, 0x4f, 0xf0, 0x14, 0x09, 0x01, 0x22
};

static unsigned char payload[CODE_CAP], zeros[CODE_CAP], buffer[CODE_CAP];

static pid_t freeze_tids[512];
static unsigned freeze_count, freeze_attached;
static unsigned char freeze_owned[512], freeze_stopped[512];
static unsigned long long freeze_begin;

static unsigned long long freeze_ms(void) {
    struct timespec ts = {0, 0};
    if (clock_gettime(CLOCK_MONOTONIC, &ts)) return 0;
    return (unsigned long long)ts.tv_sec * 1000ull + (unsigned long long)ts.tv_nsec / 1000000ull;
}

static pid_t find_im_pid(void) {
    DIR *dir = opendir("/proc");
    if (!dir) return 0;
    struct dirent *ent;
    pid_t target = 0;
    while ((ent = readdir(dir))) {
        char *end;
        long pid = strtol(ent->d_name, &end, 10);
        if (pid <= 0 || *end) continue;
        char comm_path[64], comm[32];
        snprintf(comm_path, sizeof(comm_path), "/proc/%ld/comm", pid);
        FILE *f = fopen(comm_path, "r");
        if (f) {
            if (fgets(comm, sizeof(comm), f) && (!strcmp(comm, "im.elf\n") || !strcmp(comm, "im.elf"))) {
                target = (pid_t)pid;
                fclose(f);
                break;
            }
            fclose(f);
        }
    }
    closedir(dir);
    return target;
}

static uint32_t find_lib_base(pid_t pid, const char *lib_name) {
    char maps_path[64];
    snprintf(maps_path, sizeof(maps_path), "/proc/%d/maps", pid);
    FILE *f = fopen(maps_path, "r");
    if (!f) return 0;
    char line[256];
    uint32_t base = 0;
    while (fgets(line, sizeof(line), f)) {
        if (strstr(line, lib_name) && strstr(line, " r-xp ")) {
            base = (uint32_t)strtoul(line, NULL, 16);
            break;
        }
    }
    fclose(f);
    return base;
}

static int freeze_enumerate(pid_t pid, pid_t *out, unsigned *count) {
    char task_path[64];
    snprintf(task_path, sizeof(task_path), "/proc/%d/task", pid);
    DIR *dir = opendir(task_path);
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
    printf("FREEZE threads=%u attached=%u resumed=%u result=%d elapsed_ms=%llu\n",
           freeze_count, freeze_attached, resumed, result, freeze_ms() - freeze_begin);
    return result;
}

static int freeze_process(pid_t pid) {
    pid_t check[512];
    unsigned confirmed = 0;
    int status, result;
    for (unsigned i = 0; i < freeze_attached; i++) if (freeze_owned[i]) return -100;
    freeze_count = freeze_attached = 0;
    freeze_begin = freeze_ms();
    if (!freeze_begin) return -93;
    result = freeze_enumerate(pid, freeze_tids, &freeze_count);
    if (result || !freeze_count) return result ? result : -94;
    for (unsigned i = 0; i < freeze_count; i++) {
        if (ptrace(PTRACE_ATTACH, freeze_tids[i], 0, 0)) { result = -95; goto fail; }
        freeze_owned[i] = 1;
        freeze_stopped[i] = 0;
        freeze_attached++;
    }
    for (unsigned i = 0; i < freeze_attached; i++) {
        if (waitpid(freeze_tids[i], &status, __WALL) != freeze_tids[i] || !WIFSTOPPED(status)) {
            result = -96;
            goto fail;
        }
        freeze_stopped[i] = 1;
    }
    result = freeze_enumerate(pid, check, &confirmed);
    if (result || confirmed != freeze_count) { result = -97; goto fail; }
    for (unsigned i = 0; i < freeze_count; i++) {
        if (check[i] != freeze_tids[i]) { result = -98; goto fail; }
    }
    return 0;
fail:
    freeze_resume();
    return result;
}

static int readmem(int fd, uint32_t addr, void *buf, size_t len) {
    return lseek(fd, (off_t)addr, SEEK_SET) == (off_t)addr &&
           read(fd, buf, len) == (ssize_t)len ? 0 : -1;
}

static int writemem(int fd, uint32_t addr, const void *buf, size_t len) {
    return lseek(fd, (off_t)addr, SEEK_SET) == (off_t)addr &&
           write(fd, buf, len) == (ssize_t)len ? 0 : -1;
}

static int check_thread_pc(uint32_t hook_rel, uint32_t hook_prs) {
    for (unsigned i = 0; i < freeze_attached; i++) {
        unsigned long regs[18];
        if (ptrace(PTRACE_GETREGS, freeze_tids[i], 0, regs)) return -99;
        uint32_t pc = (uint32_t)regs[15];
        if ((pc >= hook_rel && pc < hook_rel + HOOK_SIZE) ||
            (pc >= hook_prs && pc < hook_prs + HOOK_SIZE)) {
            return -80;
        }
    }
    return 0;
}

static void make_branch(unsigned char out[12], uint32_t target) {
    /* 12-byte Thumb-2 branch: ldr.w pc, [pc, #0]; target; nop; nop */
    unsigned char tmpl[12] = {
        0xdf, 0xf8, 0, 0xf0,
        0, 0, 0, 0,
        0, 0xbf, 0, 0xbf
    };
    memcpy(tmpl + 4, &target, 4);
    memcpy(out, tmpl, 12);
}

int main(int argc, char **argv) {
    /* Request header:
     * h[0] = magic ("MTFR")
     * h[1] = proto ver (2)
     * h[2] = op (0=check, 1=install, 2=remove)
     * h[3] = code_bytes
     * h[4] = rel_entry (rel offset in payload | 1)
     * h[5] = prs_entry (prs offset in payload | 1)
     * h[6] = fnv1a checksum
     * h[7] = state_size (644)
     * h[8] = offset_hook_rel (0x00618488)
     * h[9] = offset_hook_prs (0x00618420)
     */
    const char *req_path = (argc > 1) ? argv[1] : "/tmp/menu_touch_full.req";
    FILE *f = fopen(req_path, "rb");
    if (!f) {
        printf("FAILED error=-1 req_open_failed\n");
        return 2;
    }
    uint32_t h[10];
    if (fread(h, 1, sizeof(h), f) != sizeof(h) || h[0] != REQ_MAGIC || h[1] != PROTO_VER ||
        h[2] > 2u || !h[3] || h[3] > sizeof(payload) || (h[3] % 4u) ||
        !(h[4] & 1u) || !(h[5] & 1u) || h[7] != sizeof(struct mt_full_state) ||
        h[8] != OFFSET_HOOK_REL || h[9] != OFFSET_HOOK_PRS ||
        fread(payload, 1, h[3], f) != h[3] || fgetc(f) != EOF) {
        fclose(f);
        printf("FAILED error=-2 invalid_req_packet\n");
        return 3;
    }
    fclose(f);

    uint32_t sum = 2166136261u;
    for (unsigned i = 0; i < h[3]; i++) sum = (sum ^ payload[i]) * 16777619u;
    if (sum != h[6]) {
        printf("FAILED error=-3 checksum_mismatch\n");
        return 4;
    }

    pid_t pid = find_im_pid();
    if (!pid) {
        printf("FAILED error=-4 im_elf_not_found\n");
        return 5;
    }

    uint32_t lib_base = find_lib_base(pid, "libObj.so");
    if (!lib_base) {
        printf("FAILED error=-5 libObj_base_not_found\n");
        return 6;
    }

    uint32_t view_base = find_lib_base(pid, "viewUnified2.so");
    if (!view_base) {
        printf("FAILED error=-6 viewUnified2_base_not_found\n");
        return 7;
    }

    uint32_t caution_base = find_lib_base(pid, "CautionConfig.so");
    if (!caution_base) {
        printf("FAILED error=-7 CautionConfig_base_not_found\n");
        return 8;
    }

    uint32_t view4_base = find_lib_base(pid, "viewUnified4.so");
    if (!view4_base) {
        view4_base = 0x2c300000u; /* Fallback to standard ZV-E10 mapping */
    }

    uint32_t code_addr = lib_base + OFFSET_CODE;
    uint32_t state_addr = lib_base + OFFSET_STATE;
    uint32_t hook_rel = lib_base + OFFSET_HOOK_REL;
    uint32_t hook_prs = lib_base + OFFSET_HOOK_PRS;

    /* Absolute entry points */
    uint32_t abs_rel_entry = code_addr + (h[4] & ~1u) | 1u;
    uint32_t abs_prs_entry = code_addr + (h[5] & ~1u) | 1u;

    /* Dynamically patch the 3 trampoline pointers in payload */
    uint32_t rel_resume = (hook_rel + HOOK_SIZE) | 1u;
    uint32_t prs_resume = (hook_prs + HOOK_SIZE) | 1u;

    for (unsigned i = 0; i <= h[3] - 4; i += 4) {
        uint32_t *slot = (uint32_t *)(payload + i);
        if (*slot == 0x0061800du) *slot = rel_resume;
        else if (*slot == 0x00618119u) *slot = prs_resume;
        else if (*slot == 0x013f8000u) *slot = state_addr;
    }

    unsigned char br_rel[HOOK_SIZE], br_prs[HOOK_SIZE];
    make_branch(br_rel, abs_rel_entry);
    make_branch(br_prs, abs_prs_entry);

    int result = freeze_process(pid);
    if (result) {
        printf("FAILED error=%d freeze_failed\n", result);
        return result;
    }

    char mem_path[64];
    snprintf(mem_path, sizeof(mem_path), "/proc/%d/mem", pid);
    int fd = open(mem_path, h[2] ? O_RDWR : O_RDONLY);
    if (fd < 0) {
        result = -10;
        goto done;
    }

    if (check_thread_pc(hook_rel, hook_prs)) {
        result = -80;
        goto done;
    }

    /* Verify current hook state (original prologue or existing branch) */
    unsigned char cur_rel[12], cur_prs[12];
    if (readmem(fd, hook_rel, cur_rel, HOOK_SIZE) ||
        readmem(fd, hook_prs, cur_prs, HOOK_SIZE)) {
        result = -12;
        goto done;
    }
    int is_orig = (memcmp(cur_rel, orig_rel, HOOK_SIZE) == 0 &&
                   memcmp(cur_prs, orig_prs, HOOK_SIZE) == 0);
    int is_branch = (cur_rel[0] == 0xdf && cur_rel[1] == 0xf8 && cur_rel[2] == 0x00 && cur_rel[3] == 0xf0 &&
                     cur_prs[0] == 0xdf && cur_prs[1] == 0xf8 && cur_prs[2] == 0x00 && cur_prs[3] == 0xf0);
    if (!is_orig && !is_branch) {
        result = -12;
        goto done;
    }

    if (h[2] == 0u) {
        /* Check operation */
        result = 0;
        printf("CHECK op=0 rel=%08x prs=%08x code_bytes=%u is_orig=%d is_branch=%d lib_base=%08x view_base=%08x caution_base=%08x pid=%d result=OK\n",
               abs_rel_entry, abs_prs_entry, h[3], is_orig, is_branch, lib_base, view_base, caution_base, pid);
        goto done;
    }

    if (h[2] == 1u) {
        /* Install operation */
        if (readmem(fd, code_addr, buffer, h[3])) {
            result = -14;
            goto done;
        }
        if (!is_branch && memcmp(buffer, zeros, h[3]) != 0) {
            result = -14;
            goto done;
        }
        if (writemem(fd, code_addr, payload, h[3]) ||
            readmem(fd, code_addr, buffer, h[3]) || memcmp(buffer, payload, h[3])) {
            writemem(fd, code_addr, zeros, h[3]);
            result = -15;
            goto done;
        }

        /* Initialize state block with runtime bases */
        struct mt_full_state state_init;
        memset(&state_init, 0, sizeof(state_init));
        state_init.magic = STATE_MAGIC;
        state_init.version = PROTO_VER;
        state_init.enabled = 2u;
        state_init.capacity = EVENT_CAP;
        state_init.lib_base = lib_base;
        state_init.view_base = view_base;
        state_init.caution_base = caution_base;
        state_init.cached_menu = 0u;
        state_init.view4_base = view4_base;
        state_init.cached_fn_view = 0u;

        if (writemem(fd, state_addr, &state_init, sizeof(state_init))) {
            writemem(fd, code_addr, zeros, h[3]);
            result = -16;
            goto done;
        }

        /* Patch hooks */
        if (writemem(fd, hook_rel, br_rel, HOOK_SIZE) ||
            writemem(fd, hook_prs, br_prs, HOOK_SIZE)) {
            /* Rollback */
            writemem(fd, hook_rel, orig_rel, HOOK_SIZE);
            writemem(fd, hook_prs, orig_prs, HOOK_SIZE);
            writemem(fd, code_addr, zeros, h[3]);
            writemem(fd, state_addr, zeros, sizeof(struct mt_full_state));
            result = -17;
            goto done;
        }

        printf("INSTALLED op=1 rel=%08x prs=%08x code_bytes=%u lib_base=%08x view_base=%08x caution_base=%08x pid=%d result=SUCCESS\n",
               abs_rel_entry, abs_prs_entry, h[3], lib_base, view_base, caution_base, pid);
        result = 0;
        goto done;
    }

    if (h[2] == 2u) {
        /* Remove operation */
        writemem(fd, hook_rel, orig_rel, HOOK_SIZE);
        writemem(fd, hook_prs, orig_prs, HOOK_SIZE);
        writemem(fd, code_addr, zeros, h[3]);
        writemem(fd, state_addr, zeros, sizeof(struct mt_full_state));
        printf("REMOVED op=2 lib_base=%08x pid=%d result=SUCCESS\n", lib_base, pid);
        result = 0;
        goto done;
    }

done:
    if (fd >= 0) close(fd);
    freeze_resume();
    printf("END result=%d errno=%d\n", result, errno);
    return result ? 1 : 0;
}
