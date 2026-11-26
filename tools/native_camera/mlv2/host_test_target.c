/* Host stand-in for im.elf used only by test_mlv2_guard_host.py (x86_64 Linux / WSL).
 * Maps files named libtestcmd.so and viewUnified2.so at the camera addresses the guard checks,
 * seeds the hook windows with a chosen state, runs a few idle threads, and answers stdin commands:
 *   dump  -> hex of the shim cave, the three hook windows and both rectangle slots
 *   quit  -> exit
 * Usage: host_test_target DIR stock|v1|v1life|v2junk|unknown|cavedirty [VIEW_BIAS TESTCMD_BIAS]
 * The libraries are mapped from file offset 0 at their reference bases plus the optional biases,
 * so the guard derives the same differences from /proc/PID/maps as on a moved layout.
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <unistd.h>

static uint32_t vb, tb;
#define SHIM_ADDR (0x05662800u + tb)
#define SHIM_CAP 2048u
#define HOOK_SET_ROOT (0x15b91654u + vb)
#define HOOK_REFRESH (0x15b91a80u + vb)
#define HOOK_OPEN_ITEM (0x15b92044u + vb)
#define RECT1 (0x15de8a04u + vb)
#define RECT2 (0x15de8a84u + vb)

static const unsigned char o_sr[16] = {0x04, 0x46, 0x00, 0xaf, 0x51, 0xb1, 0x00, 0xf5, 0xc2, 0x33, 0x03, 0xf5, 0x2b, 0x73, 0x19, 0x60};
static const unsigned char o_rf[16] = {0x2d, 0xe9, 0xf0, 0x4f, 0x9b, 0xb0, 0x06, 0xaf, 0x04, 0x46, 0x07, 0xf1, 0x20, 0x00, 0x4f, 0xf0};
static const unsigned char o_oi[12] = {0x00, 0xf5, 0xc2, 0x33, 0x03, 0xf5, 0x2c, 0x73, 0x2d, 0xe9, 0xf0, 0x43};
static const unsigned char rect[8] = {0x15, 0x02, 0x00, 0x00, 0x5b, 0x00, 0x42, 0x00};

static void *map_file(const char *dir, const char *name, uintptr_t at, size_t len) {
    char path[512];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    int fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0600);
    if (fd < 0 || ftruncate(fd, (off_t)len)) exit(10);
    void *p = mmap((void *)at, len, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_FIXED_NOREPLACE, fd, 0);
    close(fd);
    if (p != (void *)at) exit(11);
    return p;
}

static void branch(uint32_t a, uint32_t entry, int kind) {
    unsigned char *p = (unsigned char *)(uintptr_t)a;
    static const unsigned char head[4] = {0xdf, 0xf8, 0x00, 0xf0};
    memcpy(p, head, 4);
    memcpy(p + 4, &entry, 4);
    if (kind == 0) memcpy(p + 8, "\x00\xbf\x00\xbf\x00\xbf\x00\xbf", 8);
    if (kind == 1) memcpy(p + 8, "\x00\xbf\x00\xbf\x00\xbf\x4f\xf0", 8);
    if (kind == 2) memcpy(p + 8, "\x2d\xe9\xf0\x43", 4);
}

static void *idle(void *arg) {
    (void)arg;
    for (;;) pause();
    return NULL;
}

static void hex(uint32_t a, unsigned n) {
    const unsigned char *p = (const unsigned char *)(uintptr_t)a;
    printf("%08x ", a);
    for (unsigned i = 0; i < n; i++) printf("%02x", p[i]);
    printf("\n");
}

int main(int argc, char **argv) {
    if (argc != 3 && argc != 5) return 2;
    if (argc == 5) { vb = (uint32_t)strtoul(argv[3], 0, 0); tb = (uint32_t)strtoul(argv[4], 0, 0); }
    prctl(PR_SET_NAME, "im.elf");
    prctl(PR_SET_PTRACER, PR_SET_PTRACER_ANY);
    map_file(argv[1], "libtestcmd.so", 0x05660000u + tb, 0x3000);
    map_file(argv[1], "viewUnified2.so", 0x155e6000u + vb, 0x8ac000);
    const char *m = argv[2];
    memcpy((void *)(uintptr_t)HOOK_SET_ROOT, o_sr, 16);
    memcpy((void *)(uintptr_t)HOOK_REFRESH, o_rf, 16);
    memcpy((void *)(uintptr_t)HOOK_OPEN_ITEM, o_oi, 12);
    if (!strcmp(m, "v1") || !strcmp(m, "v1life") || !strcmp(m, "cavedirty")) {
        int life = !strcmp(m, "v1life");
        branch(HOOK_SET_ROOT, life ? 0x45e4ea1u : 0x45e4e7du, 0);
        branch(HOOK_REFRESH, life ? 0x45e4ef1u : 0x45e4ecdu, 1);
        branch(HOOK_OPEN_ITEM, life ? 0x45e4f01u : 0x45e4eddu, 2);
        memcpy((void *)(uintptr_t)RECT1, rect, 8);
        memcpy((void *)(uintptr_t)RECT2, rect, 8);
    }
    if (!strcmp(m, "cavedirty")) ((unsigned char *)(uintptr_t)SHIM_ADDR)[100] = 0x5a;
    if (!strcmp(m, "unknown")) ((unsigned char *)(uintptr_t)HOOK_REFRESH)[3] ^= 0xff;
    pthread_t t;
    for (int i = 0; i < 3; i++) pthread_create(&t, NULL, idle, NULL);
    printf("READY %d\n", getpid());
    fflush(stdout);
    char line[64];
    while (fgets(line, sizeof(line), stdin)) {
        if (!strncmp(line, "dump", 4)) {
            hex(SHIM_ADDR, SHIM_CAP);
            hex(HOOK_SET_ROOT, 16);
            hex(HOOK_REFRESH, 16);
            hex(HOOK_OPEN_ITEM, 12);
            hex(RECT1, 8);
            hex(RECT2, 8);
            printf("END\n");
            fflush(stdout);
        } else if (!strncmp(line, "quit", 4)) {
            return 0;
        }
    }
    return 0;
}
