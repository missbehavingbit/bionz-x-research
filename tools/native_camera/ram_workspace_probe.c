#include <stddef.h>
#include <stdint.h>
#include <asm/stat.h>
#include <asm/unistd.h>
#include <linux/fs.h>

struct __attribute__((packed)) ram_record {
    char path[64];
    int32_t stat_result;
    int32_t statfs_result;
    uint64_t device;
    uint32_t magic;
    uint32_t block_size;
    uint32_t blocks;
    uint32_t available;
    uint32_t flags;
};

struct __attribute__((packed)) ram_report {
    char tag[16];
    uint32_t pid;
    uint32_t uid;
    uint32_t euid;
    uint32_t count;
    uint32_t operation;
    int32_t operation_result;
    int32_t rollback_result;
    struct ram_record records[3];
    uint32_t committed;
};

struct arm_timespec {
    long tv_sec;
    long tv_nsec;
};

struct sha256_ctx {
    uint32_t state[8];
    uint64_t bitlen;
    unsigned char data[64];
    unsigned datalen;
};

_Static_assert(sizeof(struct ram_record) == 100, "Unexpected ARM record layout");
_Static_assert(sizeof(struct ram_report) == 348, "Unexpected ARM report layout");
_Static_assert(sizeof(struct arm_timespec) == 8, "Unexpected ARM timespec layout");
_Static_assert(sizeof(void *) == 4, "ARM32 is required");
_Static_assert(BLKGETSIZE64 == 0x80041272u, "Unexpected ARM32 block-capacity ioctl");
_Static_assert(__NR_fstat64 == 197 && __NR_ioctl == 54 && __NR_lseek == 19 && __NR_rename == 38,
    "Unexpected ARM EABI file syscalls");
_Static_assert(sizeof(struct stat64) == 104 && offsetof(struct stat64, st_mode) == 16
    && offsetof(struct stat64, st_rdev) == 32, "Unexpected ARM fstat64 layout");

enum {
    ARM_O_RDONLY = 0,
    ARM_O_WRONLY = 1,
    ARM_O_CREAT = 0100,
    ARM_O_EXCL = 0200,
    ARM_O_TRUNC = 01000,
    ARM_O_NOFOLLOW = 0100000,
    PHASE_META = 1,
    PHASE_4K = 2,
    PHASE_512K = 3,
    PHASE_CLEAN = 4,
    PHASE_ERROR = 5
};

static _Alignas(8) char io_buf[8192];

static const uint32_t sha256_k[64] = {
    0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
    0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
    0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
    0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
    0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
    0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
    0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
    0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u, 0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u
};

static const char *const own_cache_paths[] = {
    "/usr/lib/libJiritsu.so",
    "/tmp/jiritsu/libJit.so",
    "/tmp/jiritsu/ram_probe.elf",
    "/tmp/jiritsu/probe.cfg",
    NULL
};

static const char *const boot_kmod_paths[] = {
    "/system/av-cam.bin",
    "/system/vmlinux.bin",
    "/system/initrd.img",
    "/system/emmc.ko",
    "/system/nand.ko",
    "/system/lld.ko",
    "/system/idt_cam.bin",
    "/system/bonobo.bin",
    "/system/LensAberrationData.bin",
    "/system/sa_dfdet.bin",
    "/system/dfe_dat.bin",
    "/usr/share/app/image_cmn_169.uxc",
    "/usr/share/app/image_DSC_Only_169.uxc",
    "/usr/share/app/image_Rich_Help_Guide_169.uxc",
    "/usr/share/app/image_EVF.uxc",
    "/usr/share/app/image_DSLR_Only_169.uxc",
    "/usr/share/app/image_cmn_o_direct_169.uxc",
    "/usr/share/app/MYingHeiC-KSX1001-SJ_wIcon.ltt",
    "/usr/share/app/HeiseiKakuGothW5-SJ_wIcon.ltt",
    "/usr/codec/vcd_firmware.bin",
    "/usr/codec/vcd_firmware_testing.bin",
    "/usr/codec/bnl_firmware.bin",
    "/usr/codec/bni_firmware.bin",
    "/usr/sabin/sa_aed.bin",
    "/usr/lib/libObj.so",
    "/usr/lib/libmpr.so",
    "/usr/lib/CautionConfig.so",
    "/usr/lib/viewUnified2.so",
    "/usr/lib/viewUnified4.so",
    "/usr/lib/libcrypto.so.3",
    "/usr/bin/im.elf",
    "/usr/bin/orb-server",
    "/usr/bin/bsa_server",
    "/usr/kmod/dmm.ko",
    "/usr/kmod/liro.ko",
    "/usr/kmod/grm_gles.ko",
    "/usr/kmod/grm_ma.ko",
    "/usr/kmod/usbg_sen.ko",
    NULL
};

__attribute__((used)) void *memset(void *dst, int c, size_t n)
{
    unsigned char *p = dst;
    while (n--) {
        *p++ = (unsigned char)c;
        __asm__ volatile("" ::: "memory");
    }
    return dst;
}

__attribute__((used)) void *memcpy(void *dst, const void *src, size_t n)
{
    unsigned char *d = dst;
    const unsigned char *s = src;
    while (n--) {
        *d++ = *s++;
        __asm__ volatile("" ::: "memory");
    }
    return dst;
}

static long call3(long number, long a, long b, long c)
{
    register long r0 __asm__("r0") = a;
    register long r1 __asm__("r1") = b;
    register long r2 __asm__("r2") = c;
    register long r7 __asm__("r7") = number;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory", "cc");
    return r0;
}

static long call5(long number, long a, long b, long c, long d, long e)
{
    register long r0 __asm__("r0") = a;
    register long r1 __asm__("r1") = b;
    register long r2 __asm__("r2") = c;
    register long r3 __asm__("r3") = d;
    register long r4 __asm__("r4") = e;
    register long r7 __asm__("r7") = number;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r3), "r"(r4), "r"(r7) : "memory", "cc");
    return r0;
}

static long call6(long number, long a, long b, long c, long d, long e, long f)
{
    register long r0 __asm__("r0") = a;
    register long r1 __asm__("r1") = b;
    register long r2 __asm__("r2") = c;
    register long r3 __asm__("r3") = d;
    register long r4 __asm__("r4") = e;
    register long r5 __asm__("r5") = f;
    register long r7 __asm__("r7") = number;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r3), "r"(r4), "r"(r5), "r"(r7) : "memory", "cc");
    return r0;
}

static void zero(void *buffer, unsigned size)
{
    memset(buffer, 0, size);
}

static unsigned slen(const char *s)
{
    unsigned n = 0;
    while (s[n])
        ++n;
    return n;
}

static int equal(const char *a, const char *b)
{
    while (*a && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

static int starts_with(const char *s, const char *prefix)
{
    while (*prefix) {
        if (*s++ != *prefix++)
            return 0;
    }
    return 1;
}

static void append_str(char *dst, unsigned cap, unsigned *pos, const char *s)
{
    while (*s && *pos + 1 < cap)
        dst[(*pos)++] = *s++;
    dst[*pos] = 0;
}

static void append_u32(char *dst, unsigned cap, unsigned *pos, uint32_t val)
{
    static const uint32_t pow10[10] = {
        1000000000u, 100000000u, 10000000u, 1000000u, 100000u,
        10000u, 1000u, 100u, 10u, 1u
    };
    unsigned i, started = 0;
    for (i = 0; i < 10; ++i) {
        unsigned digit = 0;
        while (val >= pow10[i]) {
            val -= pow10[i];
            ++digit;
        }
        if (digit || started || i == 9) {
            started = 1;
            if (*pos + 1 < cap)
                dst[(*pos)++] = (char)('0' + digit);
        }
    }
    dst[*pos] = 0;
}

static void append_i32(char *dst, unsigned cap, unsigned *pos, int32_t val)
{
    uint32_t u;
    if (val < 0) {
        if (*pos + 1 < cap)
            dst[(*pos)++] = '-';
        u = 0u - (uint32_t)val;
    } else {
        u = (uint32_t)val;
    }
    append_u32(dst, cap, pos, u);
}

static void append_hex(char *dst, unsigned cap, unsigned *pos, const unsigned char *bytes, unsigned len)
{
    static const char hex[16] = "0123456789abcdef";
    unsigned i;
    for (i = 0; i < len && *pos + 2 < cap; ++i) {
        dst[(*pos)++] = hex[bytes[i] >> 4];
        dst[(*pos)++] = hex[bytes[i] & 15u];
    }
    dst[*pos] = 0;
}

static int write_all(int fd, const void *buf, unsigned len)
{
    unsigned done = 0;
    while (done < len) {
        long n = call3(4, fd, (long)((const unsigned char *)buf + done), len - done);
        if (n <= 0 || (unsigned long)n > len - done)
            return -1;
        done += (unsigned)n;
    }
    return 0;
}

static int log_text(int fd, const char *text)
{
    return write_all(fd, text, slen(text));
}

static void sleep_ns(long sec, long nsec)
{
    struct arm_timespec ts = {sec, nsec};
    call3(162, (long)&ts, 0, 0);
}

static uint32_t rotr32(uint32_t x, unsigned n)
{
    return (x >> n) | (x << (32u - n));
}

static void sha256_transform(struct sha256_ctx *ctx, const unsigned char data[64])
{
    uint32_t m[64], a, b, c, d, e, f, g, h, t1, t2;
    unsigned i;
    for (i = 0; i < 16; ++i) {
        m[i] = ((uint32_t)data[i * 4] << 24) | ((uint32_t)data[i * 4 + 1] << 16)
             | ((uint32_t)data[i * 4 + 2] << 8) | (uint32_t)data[i * 4 + 3];
    }
    for (; i < 64; ++i) {
        uint32_t s0 = rotr32(m[i - 15], 7) ^ rotr32(m[i - 15], 18) ^ (m[i - 15] >> 3);
        uint32_t s1 = rotr32(m[i - 2], 17) ^ rotr32(m[i - 2], 19) ^ (m[i - 2] >> 10);
        m[i] = m[i - 16] + s0 + m[i - 7] + s1;
    }
    a = ctx->state[0]; b = ctx->state[1]; c = ctx->state[2]; d = ctx->state[3];
    e = ctx->state[4]; f = ctx->state[5]; g = ctx->state[6]; h = ctx->state[7];
    for (i = 0; i < 64; ++i) {
        uint32_t ep1 = rotr32(e, 6) ^ rotr32(e, 11) ^ rotr32(e, 25);
        uint32_t ch = (e & f) ^ ((~e) & g);
        uint32_t ep0 = rotr32(a, 2) ^ rotr32(a, 13) ^ rotr32(a, 22);
        uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
        t1 = h + ep1 + ch + sha256_k[i] + m[i];
        t2 = ep0 + maj;
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }
    ctx->state[0] += a; ctx->state[1] += b; ctx->state[2] += c; ctx->state[3] += d;
    ctx->state[4] += e; ctx->state[5] += f; ctx->state[6] += g; ctx->state[7] += h;
}

static void sha256_init(struct sha256_ctx *ctx)
{
    ctx->datalen = 0;
    ctx->bitlen = 0;
    ctx->state[0] = 0x6a09e667u; ctx->state[1] = 0xbb67ae85u;
    ctx->state[2] = 0x3c6ef372u; ctx->state[3] = 0xa54ff53au;
    ctx->state[4] = 0x510e527fu; ctx->state[5] = 0x9b05688cu;
    ctx->state[6] = 0x1f83d9abu; ctx->state[7] = 0x5be0cd19u;
}

static void sha256_update(struct sha256_ctx *ctx, const unsigned char *data, unsigned len)
{
    unsigned i;
    for (i = 0; i < len; ++i) {
        ctx->data[ctx->datalen++] = data[i];
        if (ctx->datalen == 64) {
            sha256_transform(ctx, ctx->data);
            ctx->bitlen += 512;
            ctx->datalen = 0;
        }
    }
}

static void sha256_final(struct sha256_ctx *ctx, unsigned char hash[32])
{
    unsigned i = ctx->datalen;
    ctx->bitlen += (uint64_t)ctx->datalen * 8u;
    ctx->data[i++] = 0x80u;
    if (i > 56) {
        while (i < 64)
            ctx->data[i++] = 0;
        sha256_transform(ctx, ctx->data);
        i = 0;
    }
    while (i < 56)
        ctx->data[i++] = 0;
    for (i = 0; i < 8; ++i)
        ctx->data[63 - i] = (unsigned char)(ctx->bitlen >> (i * 8u));
    sha256_transform(ctx, ctx->data);
    for (i = 0; i < 8; ++i) {
        hash[i * 4] = (unsigned char)(ctx->state[i] >> 24);
        hash[i * 4 + 1] = (unsigned char)(ctx->state[i] >> 16);
        hash[i * 4 + 2] = (unsigned char)(ctx->state[i] >> 8);
        hash[i * 4 + 3] = (unsigned char)ctx->state[i];
    }
}

static void inspect(struct ram_record *record, const char *path)
{
    _Alignas(8) unsigned char stat_buffer[512];
    _Alignas(8) uint32_t fs_buffer[128];
    unsigned i;
    zero(record, sizeof(*record));
    zero(stat_buffer, sizeof(stat_buffer));
    zero(fs_buffer, sizeof(fs_buffer));
    for (i = 0; path[i] && i + 1 < sizeof(record->path); ++i)
        record->path[i] = path[i];
    record->stat_result = (int32_t)call3(195, (long)path, (long)stat_buffer, 0);
    record->statfs_result = (int32_t)call3(99, (long)path, (long)fs_buffer, 0);
    if (!record->stat_result)
        for (i = 0; i < 8; ++i)
            ((unsigned char *)&record->device)[i] = stat_buffer[i];
    if (!record->statfs_result) {
        record->magic = fs_buffer[0];
        record->block_size = fs_buffer[1];
        record->blocks = fs_buffer[2];
        record->available = fs_buffer[4];
        record->flags = fs_buffer[11];
    }
}

static int number(const char *text, uint32_t *value)
{
    uint32_t result = 0;
    if (!*text)
        return 0;
    while (*text) {
        unsigned digit = (unsigned char)*text++ - '0';
        if (digit > 9 || result > (UINT32_MAX - digit) / 10)
            return 0;
        result = result * 10 + digit;
    }
    *value = result;
    return 1;
}

static int build_nonce_path(char out[96], const char *prefix, const char *nonce, const char *suffix)
{
    unsigned pos = 0, i;
    append_str(out, 96, &pos, prefix);
    for (i = 0; i < 32; ++i) {
        if (!((nonce[i] >= '0' && nonce[i] <= '9') || (nonce[i] >= 'a' && nonce[i] <= 'f')))
            return 0;
        if (pos + 1 >= 96)
            return 0;
        out[pos++] = nonce[i];
    }
    out[pos] = 0;
    if (nonce[32] != 0)
        return 0;
    if (suffix)
        append_str(out, 96, &pos, suffix);
    return 1;
}

static int private_path(char path[64], const char *nonce)
{
    char tmp[96];
    unsigned i;
    if (!build_nonce_path(tmp, "/tmp/jiritsu/a6400a-", nonce, NULL))
        return 0;
    for (i = 0; tmp[i] && i + 1 < 64; ++i)
        path[i] = tmp[i];
    path[i] = 0;
    return tmp[i] == 0;
}

static int check_binding(const char *path, int private_mode, uint32_t device, uint32_t magic)
{
    struct ram_record rec;
    inspect(&rec, path);
    if (rec.stat_result || rec.statfs_result
        || rec.device != device || rec.magic != magic
        || call3(24, 0, 0, 0) != 0 || call3(49, 0, 0, 0) != 0)
        return 1;
    if (private_mode && (rec.magic != 0x01021994u
        || (rec.flags & 15u) != 14u
        || (uint64_t)rec.block_size * rec.blocks != 2097152ull))
        return 1;
    return 0;
}

static void populate_report(struct ram_report *report, const char *first_path, uint32_t operation)
{
    const char *paths[3] = {first_path, "/tmp_bt", "/tmp"};
    unsigned i;
    zero(report, sizeof(*report));
    report->rollback_result = INT32_MIN;
    report->operation = operation;
    for (i = 0; i < sizeof(report->tag); ++i)
        report->tag[i] = "A6400A_RAM_V2\0\0\0"[i];
    report->pid = (uint32_t)call3(20, 0, 0, 0);
    report->uid = (uint32_t)call3(24, 0, 0, 0);
    report->euid = (uint32_t)call3(49, 0, 0, 0);
    report->count = 3;
    if (report->operation && (report->uid || report->euid)) {
        report->operation_result = -1;
    } else if (report->operation == 1) {
        report->operation_result = (int32_t)call3(39, (long)first_path, 0700, 0);
        if (!report->operation_result) {
            report->operation_result = (int32_t)call5(21, (long)"tmpfs", (long)first_path,
                (long)"tmpfs", 14, (long)"size=2097152,mode=0700");
            if (report->operation_result)
                report->rollback_result = (int32_t)call3(40, (long)first_path, 0, 0);
        }
    } else if (report->operation == 3) {
        inspect(&report->records[0], first_path);
        if (report->records[0].stat_result || report->records[0].statfs_result
                || report->records[0].magic != 0x01021994u) {
            report->operation_result = -22;
        } else {
            report->operation_result = (int32_t)call3(52, (long)first_path, 0, 0);
            if (!report->operation_result)
                report->rollback_result = (int32_t)call3(40, (long)first_path, 0, 0);
        }
    }
    for (i = 0; i < 3; ++i)
        inspect(&report->records[i], paths[i]);
    report->committed = 0x52414d21u;
}

static int read_proc_file(const char *path, char *buf, unsigned cap, unsigned *out_len)
{
    long fd = call3(5, (long)path, ARM_O_RDONLY, 0);
    unsigned total = 0;
    if (fd < 0)
        return -1;
    while (total + 1 < cap) {
        unsigned want = cap - 1 - total;
        long n = call3(3, fd, (long)(buf + total), want);
        if (n < 0 || (unsigned long)n > want) {
            call3(6, fd, 0, 0);
            return -1;
        }
        if (n == 0)
            break;
        total += (unsigned)n;
    }
    if (total + 1 == cap && call3(3, fd, (long)(buf + total), 1) != 0) {
        call3(6, fd, 0, 0);
        return -1;
    }
    if (call3(6, fd, 0, 0) != 0)
        return -1;
    buf[total] = 0;
    *out_len = total;
    return 0;
}

static void evict_path_list(const char *const *paths)
{
    unsigned i;
    for (i = 0; paths[i]; ++i) {
        long fd = call3(5, (long)paths[i], ARM_O_RDONLY | ARM_O_NOFOLLOW, 0);
        if (fd >= 0) {
            call6(270, fd, 4, 0, 0, 0, 0);
            call3(6, fd, 0, 0);
        }
    }
}

static void evict_single_file(const char *path)
{
    const char *list[2] = {path, NULL};
    evict_path_list(list);
}

static int is_real_dir(const char *path)
{
    _Alignas(8) unsigned char st[512];
    uint32_t mode = 0;
    unsigned i;
    zero(st, sizeof(st));
    if (call3(196, (long)path, (long)st, 0) != 0)
        return 0;
    for (i = 0; i < 4; ++i)
        ((unsigned char *)&mode)[i] = st[16 + i];
    return (mode & 0170000u) == 0040000u;
}

static unsigned split_ws(char *line, char *tokens[], unsigned max_tokens)
{
    unsigned count = 0;
    char *p = line;
    while (*p && count < max_tokens) {
        while (*p == ' ' || *p == '\t')
            ++p;
        if (!*p)
            break;
        tokens[count++] = p;
        while (*p && *p != ' ' && *p != '\t')
            ++p;
        if (*p)
            *p++ = 0;
    }
    while (*p == ' ' || *p == '\t')
        ++p;
    if (*p && count == max_tokens)
        return max_tokens + 1;
    return count;
}

static int check_and_log_swap(int log_fd)
{
    unsigned len = 0, pos = 0, line_idx = 0, header = 0, active = 0, valid = 1;
    char msg[96];
    if (read_proc_file("/proc/swaps", io_buf, sizeof(io_buf), &len) != 0) {
        valid = 0;
    } else {
        char *cur = io_buf;
        while (*cur) {
            char *end = cur;
            char *toks[6];
            unsigned nt;
            while (*end && *end != '\n' && *end != '\r')
                ++end;
            if (*end) {
                char saved = *end;
                *end++ = 0;
                if (saved == '\r' && *end == '\n')
                    ++end;
            }
            nt = split_ws(cur, toks, 6);
            if (line_idx == 0) {
                if (nt == 5 && equal(toks[0], "Filename") && equal(toks[1], "Type")
                        && equal(toks[2], "Size") && equal(toks[3], "Used") && equal(toks[4], "Priority"))
                    header = 1;
                else {
                    valid = 0;
                    break;
                }
            } else if (nt > 0) {
                ++active;
                if (active > 16) {
                    valid = 0;
                    break;
                }
            }
            ++line_idx;
            cur = end;
        }
    }
    append_str(msg, sizeof(msg), &pos, "SWAP_STATE Header=");
    append_u32(msg, sizeof(msg), &pos, header);
    append_str(msg, sizeof(msg), &pos, " Active=");
    append_u32(msg, sizeof(msg), &pos, active);
    append_str(msg, sizeof(msg), &pos, " Valid=");
    append_u32(msg, sizeof(msg), &pos, valid);
    append_str(msg, sizeof(msg), &pos, "\n");
    log_text(log_fd, msg);
    return header == 1 && valid == 1 && active == 0;
}

static int read_and_log_meminfo(int log_fd, const char *stage, uint32_t *mem_free_out)
{
    static const char *const keys[] = {
        "MemTotal:", "MemFree:", "Buffers:", "Cached:", "SwapCached:",
        "SwapTotal:", "SwapFree:", "AnonPages:", "Mapped:", "Slab:", "PageTables:", NULL
    };
    uint32_t vals[11];
    unsigned seen[11];
    unsigned len = 0, i, pos = 0;
    char msg[320];
    for (i = 0; i < 11; ++i) {
        vals[i] = 0;
        seen[i] = 0;
    }
    if (read_proc_file("/proc/meminfo", io_buf, sizeof(io_buf), &len) != 0)
        return 0;
    {
        char *cur = io_buf;
        while (*cur) {
            char *end = cur;
            char *toks[4];
            unsigned nt;
            while (*end && *end != '\n' && *end != '\r')
                ++end;
            if (*end) {
                char saved = *end;
                *end++ = 0;
                if (saved == '\r' && *end == '\n')
                    ++end;
            }
            nt = split_ws(cur, toks, 3);
            if (nt >= 2) {
                for (i = 0; keys[i]; ++i) {
                    if (equal(toks[0], keys[i])) {
                        uint32_t v = 0;
                        if (number(toks[1], &v)) {
                            vals[i] = v;
                            seen[i] = 1;
                        }
                        break;
                    }
                }
            }
            cur = end;
        }
    }
    append_str(msg, sizeof(msg), &pos, "MEMINFO ");
    append_str(msg, sizeof(msg), &pos, stage);
    for (i = 0; keys[i]; ++i) {
        if (seen[i]) {
            unsigned klen = slen(keys[i]);
            append_str(msg, sizeof(msg), &pos, " ");
            if (klen > 1 && pos + klen < sizeof(msg)) {
                memcpy(msg + pos, keys[i], klen - 1);
                pos += klen - 1;
                msg[pos] = 0;
            }
            append_str(msg, sizeof(msg), &pos, "=");
            append_u32(msg, sizeof(msg), &pos, vals[i]);
        }
    }
    append_str(msg, sizeof(msg), &pos, "\n");
    log_text(log_fd, msg);
    if (!seen[1])
        return 0;
    *mem_free_out = vals[1];
    return 1;
}

static int log_uptime(int log_fd, const char *phase)
{
    unsigned len = 0, i = 0, pos = 0;
    char up[36], msg[80];
    if (read_proc_file("/proc/uptime", io_buf, sizeof(io_buf), &len) != 0)
        return 0;
    while (io_buf[i] == ' ' || io_buf[i] == '\t')
        ++i;
    {
        unsigned u = 0, digits = 0;
        while (io_buf[i] && io_buf[i] != ' ' && io_buf[i] != '\t' && io_buf[i] != '\n' && io_buf[i] != '\r') {
            char ch = io_buf[i++];
            if (!((ch >= '0' && ch <= '9') || ch == '.') || u + 1 >= sizeof(up))
                return 0;
            if (ch >= '0' && ch <= '9')
                ++digits;
            up[u++] = ch;
        }
        if (!digits)
            return 0;
        up[u] = 0;
    }
    append_str(msg, sizeof(msg), &pos, "UPTIME ");
    append_str(msg, sizeof(msg), &pos, phase);
    append_str(msg, sizeof(msg), &pos, " ");
    append_str(msg, sizeof(msg), &pos, up);
    append_str(msg, sizeof(msg), &pos, "\n");
    return log_text(log_fd, msg) == 0;
}

static void log_status_fields(int log_fd)
{
    unsigned len = 0;
    char *cur;
    if (read_proc_file("/proc/self/status", io_buf, sizeof(io_buf), &len) != 0)
        return;
    cur = io_buf;
    while (*cur) {
        char *end = cur;
        while (*end && *end != '\n')
            ++end;
        if (*end)
            *end++ = 0;
        if (starts_with(cur, "Pid:") || starts_with(cur, "PPid:") || starts_with(cur, "Uid:")
                || starts_with(cur, "SigIgn:") || starts_with(cur, "SigCgt:") || starts_with(cur, "CapEff:")) {
            log_text(log_fd, cur);
            log_text(log_fd, "\n");
        }
        cur = end;
    }
}

static int log_mountinfo(int log_fd)
{
    unsigned len = 0, matches = 0;
    char *cur;
    if (read_proc_file("/proc/self/mountinfo", io_buf, sizeof(io_buf), &len) != 0)
        return 0;
    cur = io_buf;
    while (*cur) {
        char *end = cur;
        char *sep = NULL;
        char *left_toks[6], *right_toks[3];
        unsigned nl, nr, i;
        while (*end && *end != '\n')
            ++end;
        if (*end)
            *end++ = 0;
        for (i = 0; cur[i]; ++i) {
            if (cur[i] == ' ' && cur[i + 1] == '-' && cur[i + 2] == ' ') {
                sep = cur + i;
                break;
            }
        }
        if (sep) {
            *sep = 0;
            nl = split_ws(cur, left_toks, 5);
            nr = split_ws(sep + 3, right_toks, 2);
            if (nl >= 5 && nr >= 2) {
                const char *pt = left_toks[4];
                if (equal(pt, "/") || equal(pt, "/tmp") || equal(pt, "/tmp_bt")
                        || equal(pt, "/tmp_network") || equal(pt, "/tmp/jiritsu")) {
                    char line[280];
                    unsigned pos = 0;
                    ++matches;
                    if (matches > 16)
                        return 0;
                    append_str(line, sizeof(line), &pos, "MI ");
                    append_str(line, sizeof(line), &pos, left_toks[0]);
                    append_str(line, sizeof(line), &pos, " ");
                    append_str(line, sizeof(line), &pos, left_toks[1]);
                    append_str(line, sizeof(line), &pos, " ");
                    append_str(line, sizeof(line), &pos, left_toks[2]);
                    append_str(line, sizeof(line), &pos, " ");
                    append_str(line, sizeof(line), &pos, pt);
                    append_str(line, sizeof(line), &pos, " ");
                    append_str(line, sizeof(line), &pos, right_toks[0]);
                    append_str(line, sizeof(line), &pos, " ");
                    append_str(line, sizeof(line), &pos, right_toks[1]);
                    append_str(line, sizeof(line), &pos, "\n");
                    log_text(log_fd, line);
                }
            }
        }
        cur = end;
    }
    return 1;
}

static int write_report_file(const char *path, const struct ram_report *report)
{
    long fd = call3(5, (long)path, ARM_O_WRONLY | ARM_O_CREAT | ARM_O_TRUNC | ARM_O_NOFOLLOW, 0600);
    int rc;
    if (fd < 0)
        return -1;
    rc = write_all((int)fd, report, sizeof(*report));
    call3(6, fd, 0, 0);
    return rc;
}

static void mark_error(int log_fd, unsigned code, int *phase)
{
    char msg[64];
    unsigned pos = 0;
    append_str(msg, sizeof(msg), &pos, "__A6400A_INIT_ERROR__:");
    append_u32(msg, sizeof(msg), &pos, code);
    append_str(msg, sizeof(msg), &pos, "\n");
    log_text(log_fd, msg);
    *phase = PHASE_ERROR;
}

static int hash_file(const char *path, unsigned expected, unsigned char digest[32])
{
    long fd = call3(5, (long)path, ARM_O_RDONLY | ARM_O_NOFOLLOW, 0);
    unsigned total = 0;
    struct sha256_ctx sha;
    if (fd < 0)
        return (int)fd;
    sha256_init(&sha);
    while (total < expected) {
        unsigned want = expected - total > 4096u ? 4096u : expected - total;
        long n = call3(3, fd, (long)io_buf, want);
        if (n <= 0 || (unsigned long)n > want) {
            call3(6, fd, 0, 0);
            return n < 0 ? (int)n : -5;
        }
        sha256_update(&sha, (const unsigned char *)io_buf, (unsigned)n);
        total += (unsigned)n;
    }
    {
        long eof = call3(3, fd, (long)io_buf, 1);
        long closed = call3(6, fd, 0, 0);
        if (eof != 0 || closed != 0)
            return -5;
    }
    sha256_final(&sha, digest);
    return 0;
}

static int stage_and_hash_pattern(const char *chunk_path, unsigned blocks, unsigned char digest[32])
{
    static const char suffix[10] = "A6400A_RAM";
    long fd;
    unsigned i;
    memset(io_buf, ' ', 4096 - 10);
    memcpy(io_buf + 4096 - 10, suffix, 10);
    fd = call3(5, (long)chunk_path, ARM_O_WRONLY | ARM_O_CREAT | ARM_O_TRUNC | ARM_O_NOFOLLOW, 0600);
    if (fd < 0)
        return 45;
    for (i = 0; i < blocks; ++i) {
        if (write_all((int)fd, io_buf, 4096) != 0) {
            call3(6, fd, 0, 0);
            return 45;
        }
    }
    if (call3(6, fd, 0, 0) != 0)
        return 45;
    return hash_file(chunk_path, blocks * 4096u, digest) ? 46 : 0;
}

static int storage_name(const char *name, int allow_data)
{
    uint32_t index;
    if (!starts_with(name, "nflasha"))
        return 0;
    name += 7;
    if (equal(name, "B0") || equal(name, "B1"))
        return 1;
    return !(*name == '0' && name[1]) && number(name, &index)
        && index <= 255u && (allow_data || index != 23u);
}

static int storage_geometry(const char *name, uint32_t *major, uint32_t *minor, uint32_t *capacity)
{
    char lines[4096], path[96], value[32], *line;
    unsigned len, pos = 0, found = 0;
    uint32_t kib = 0, sectors;
    char *tokens[4];
    if (!storage_name(name, 1) || read_proc_file("/proc/partitions", lines, sizeof(lines), &len))
        return -22;
    line = lines;
    while (*line) {
        char *next = line;
        while (*next && *next != '\n')
            ++next;
        if (*next)
            *next++ = 0;
        if (split_ws(line, tokens, 4) == 4 && equal(tokens[3], name)) {
            if (found++ || !number(tokens[0], major) || !number(tokens[1], minor)
                    || !number(tokens[2], &kib) || kib > 524288u)
                return -22;
        }
        line = next;
    }
    if (found != 1)
        return -22;
    append_str(path, sizeof(path), &pos, "/sys/class/block/");
    append_str(path, sizeof(path), &pos, name);
    append_str(path, sizeof(path), &pos, "/size");
    if (read_proc_file(path, value, sizeof(value), &len))
        return -22;
    if (len && value[len - 1] == '\n')
        value[len - 1] = 0;
    if (split_ws(value, tokens, 1) != 1 || !number(tokens[0], &sectors)
            || sectors > 1048576u || sectors * 512u != kib * 1024u)
        return -22;
    *capacity = sectors * 512u;
    return 0;
}

static int storage_inventory(int fd)
{
    char text[4096], *line, *tokens[4], row[64];
    unsigned len;
    if (read_proc_file("/proc/uptime", text, 128, &len)
            || log_text(fd, "__UPTIME__\n") || write_all(fd, text, len)
            || read_proc_file("/proc/partitions", text, sizeof(text), &len)
            || log_text(fd, "__PARTITIONS__\n") || write_all(fd, text, len)
            || log_text(fd, "__SIZES__\n"))
        return -5;
    line = text;
    while (*line) {
        char *next = line;
        uint32_t major, minor, capacity;
        unsigned pos = 0;
        while (*next && *next != '\n')
            ++next;
        if (*next)
            *next++ = 0;
        if (split_ws(line, tokens, 4) == 4 && storage_name(tokens[3], 1)) {
            if (storage_geometry(tokens[3], &major, &minor, &capacity))
                return -22;
            append_str(row, sizeof(row), &pos, tokens[3]);
            append_str(row, sizeof(row), &pos, " ");
            append_u32(row, sizeof(row), &pos, capacity >> 9);
            append_str(row, sizeof(row), &pos, "\n");
            if (log_text(fd, row))
                return -5;
        }
        line = next;
    }
    return 0;
}

static int stage_partition(const char *source, const char *chunk_path, uint32_t offset,
                           uint32_t count, uint32_t capacity, unsigned char digest[32])
{
    _Alignas(8) struct stat64 status;
    _Alignas(8) uint64_t bytes = 0, device;
    struct sha256_ctx sha;
    unsigned char source_hash[32];
    uint32_t major, minor, observed, done = 0;
    long in_fd, out_fd, result;
    if (!starts_with(source, "/dev/") || !storage_name(source + 5, 0)
            || !count || count > 524288u || !capacity || capacity > 536870912u
            || (offset | count | capacity) & 4095u || offset > capacity || count > capacity - offset
            || storage_geometry(source + 5, &major, &minor, &observed) || observed != capacity)
        return -22;
    in_fd = call3(5, (long)source, ARM_O_RDONLY | ARM_O_NOFOLLOW, 0);
    if (in_fd < 0)
        return (int)in_fd;
    zero(&status, sizeof(status));
    result = call3(__NR_fstat64, in_fd, (long)&status, 0);
    device = status.st_rdev;
    if (result || (status.st_mode & 0170000u) != 0060000u
            || device > UINT32_MAX || ((device >> 8) & 4095u) != major
            || ((device & 255u) | ((device >> 12) & 0xfffff00u)) != minor
            || call3(__NR_ioctl, in_fd, BLKGETSIZE64, (long)&bytes) != 0 || bytes != capacity
            || call6(270, in_fd, 1, 0, 0, 0, 0) != 0
            || call3(__NR_lseek, in_fd, offset, 0) != (long)offset) {
        call3(6, in_fd, 0, 0);
        return -22;
    }
    result = call3(10, (long)chunk_path, 0, 0);
    if (result != 0 && result != -2) {
        call3(6, in_fd, 0, 0);
        return (int)result;
    }
    out_fd = call3(5, (long)chunk_path,
        ARM_O_WRONLY | ARM_O_CREAT | ARM_O_EXCL | ARM_O_NOFOLLOW, 0600);
    if (out_fd < 0) {
        call3(6, in_fd, 0, 0);
        return (int)out_fd;
    }
    sha256_init(&sha);
    while (done < count) {
        unsigned want = count - done > 4096u ? 4096u : count - done;
        result = call3(3, in_fd, (long)io_buf, want);
        if (result <= 0 || (unsigned long)result > want
                || write_all((int)out_fd, io_buf, (unsigned)result)) {
            call3(6, in_fd, 0, 0);
            call3(6, out_fd, 0, 0);
            return result < 0 ? (int)result : -5;
        }
        sha256_update(&sha, (const unsigned char *)io_buf, (unsigned)result);
        done += (unsigned)result;
    }
    sha256_final(&sha, source_hash);
    result = call6(270, in_fd, 4, 0, 0, 0, 0);
    {
        long in_close = call3(6, in_fd, 0, 0);
        long out_close = call3(6, out_fd, 0, 0);
        if (in_close != 0 || out_close != 0 || result != 0)
            return -5;
    }
    result = hash_file(chunk_path, count, digest);
    if (result)
        return (int)result;
    for (done = 0; done < 32; ++done)
        if (digest[done] != source_hash[done])
            return -5;
    return 0;
}

static int native_command(const char *nonce, const char *ram_dir, const char *chunk_path,
                          uint32_t *last_sequence)
{
    char input[96], output[96], output_tmp[96], go[96], request[256], commit[8], msg[256], *tokens[9];
    unsigned pos = 0, len, i, nt;
    uint32_t sequence, device, magic;
    int fd, rc = 0;
    if (!build_nonce_path(input, "/tmp/jiritsu/a6400a-", nonce, "/input")
            || !build_nonce_path(output, "/tmp/jiritsu/a6400a-", nonce, "/output")
            || !build_nonce_path(output_tmp, "/tmp/jiritsu/a6400a-", nonce, "/output.tmp")
            || !build_nonce_path(go, "/tmp/jiritsu/a6400a-", nonce, "/go"))
        return 70;
    if (read_proc_file(go, commit, sizeof(commit), &len) || len == 0)
        return 0;
    if (len != 3 || !equal(commit, "go\n") || call3(10, (long)go, 0, 0) != 0
            || read_proc_file(input, request, sizeof(request), &len) || !len || request[len - 1] != '\n')
        return 70;
    for (i = 0; i + 1 < len; ++i)
        if (request[i] < ' ' || request[i] > '~')
            return 70;
    request[len - 1] = 0;
    nt = split_ws(request, tokens, 9);
    if ((nt != 5 && nt != 9) || !equal(tokens[0], nonce)
            || !number(tokens[2], &sequence) || sequence <= *last_sequence || sequence > 65534u
            || !number(tokens[3], &device) || !number(tokens[4], &magic)
            || check_binding(ram_dir, 1, device, magic))
        return 70;
    *last_sequence = sequence;
    fd = (int)call3(5, (long)output_tmp, ARM_O_WRONLY | ARM_O_CREAT | ARM_O_TRUNC | ARM_O_NOFOLLOW, 0600);
    if (fd < 0)
        return 70;
    append_str(msg, sizeof(msg), &pos, "__A6400A_CMD_");
    append_str(msg, sizeof(msg), &pos, nonce);
    append_str(msg, sizeof(msg), &pos, "_");
    append_u32(msg, sizeof(msg), &pos, sequence);
    append_str(msg, sizeof(msg), &pos, "__\n");
    if (log_text(fd, msg)) {
        call3(6, fd, 0, 0);
        return 70;
    }
    if (nt == 5 && equal(tokens[1], "inventory")) {
        rc = storage_inventory(fd);
    } else if (nt == 5 && equal(tokens[1], "remove")) {
        rc = (int)call3(10, (long)chunk_path, 0, 0);
        if (rc == -2)
            rc = 0;
    } else if (nt == 9 && equal(tokens[1], "chunk")) {
        uint32_t offset, count, capacity, free_kib = 0;
        unsigned char digest[32];
        int have_mem;
        long stale = call3(10, (long)chunk_path, 0, 0);
        if (stale != 0 && stale != -2) {
            rc = (int)stale;
        } else {
            have_mem = read_and_log_meminfo(fd, "DUMP", &free_kib);
            if (have_mem && free_kib < 12288u) {
                evict_single_file("/tmp/jiritsu/out.txt");
                evict_path_list(boot_kmod_paths);
                have_mem = read_and_log_meminfo(fd, "DUMP_AFTER_FADVISE", &free_kib);
            }
            if (!number(tokens[6], &offset) || !number(tokens[7], &count) || !number(tokens[8], &capacity)
                    || !have_mem || free_kib < 8192u
                    || !check_and_log_swap(fd))
                rc = -22;
            else
                rc = stage_partition(tokens[5], chunk_path, offset, count, capacity, digest);
        }
        if (!rc) {
            pos = 0;
            append_str(msg, sizeof(msg), &pos, "__A6400A_CHUNK__\n");
            append_hex(msg, sizeof(msg), &pos, digest, 32);
            append_str(msg, sizeof(msg), &pos, "  ");
            append_str(msg, sizeof(msg), &pos, chunk_path);
            append_str(msg, sizeof(msg), &pos, "\n");
            rc = log_text(fd, msg) ? -5 : 0;
        }
    } else {
        rc = -22;
    }
    pos = 0;
    if (rc) {
        append_str(msg, sizeof(msg), &pos, "DUMP_REFUSED live-capacity/binding/memory/source Result=");
        append_i32(msg, sizeof(msg), &pos, rc);
        append_str(msg, sizeof(msg), &pos, "\n");
    }
    append_str(msg, sizeof(msg), &pos, "\n__A6400A_RC__:");
    append_u32(msg, sizeof(msg), &pos, rc ? 65u : 0u);
    append_str(msg, sizeof(msg), &pos, "\n");
    rc = log_text(fd, msg) ? 70 : 0;
    if (call3(6, fd, 0, 0) != 0)
        rc = 70;
    if (!rc && call3(__NR_rename, (long)output_tmp, (long)output, 0) != 0)
        rc = 70;
    return rc;
}

static int daemon_main(void)
{
    char cfg[64], nonce[33], ram_dir[96], chunk_path[96], meta_path[96], ack_path[96], msg[256];
    unsigned cfg_len = 0, i, pos;
    int log_fd, private_mode = 0, partition_dump = 0, owned_ram = 0, phase = PHASE_META, kmod_evicted = 0;
    uint32_t dummy_free = 0, last_sequence = 0;
    struct ram_report rep;

    log_fd = (int)call3(5, (long)"/tmp/jiritsu/out.txt",
        ARM_O_WRONLY | ARM_O_CREAT | ARM_O_TRUNC | ARM_O_NOFOLLOW, 0600);
    if (log_fd < 0)
        return 30;
    if (read_proc_file("/tmp/jiritsu/probe.cfg", cfg, sizeof(cfg), &cfg_len) != 0
            || (cfg_len != 35 && cfg_len != 40)
            || cfg[32] != ' ' || (cfg[33] != '0' && cfg[33] != '1')
            || !((cfg_len == 35 && cfg[34] == '\n')
                 || (cfg_len == 40 && cfg[33] == '1' && equal(cfg + 34, " dump\n")))) {
        mark_error(log_fd, 30, &phase);
        while (1)
            sleep_ns(0, 250000000L);
    }
    for (i = 0; i < 32; ++i) {
        if (!((cfg[i] >= '0' && cfg[i] <= '9') || (cfg[i] >= 'a' && cfg[i] <= 'f'))) {
            mark_error(log_fd, 30, &phase);
            while (1)
                sleep_ns(0, 250000000L);
        }
        nonce[i] = cfg[i];
    }
    nonce[32] = 0;
    private_mode = cfg[33] == '1';
    partition_dump = cfg_len == 40;
    if (!build_nonce_path(ram_dir, private_mode ? "/tmp/jiritsu/a6400a-" : "/tmp_network/a6400a-", nonce, NULL)
            || !build_nonce_path(chunk_path, private_mode ? "/tmp/jiritsu/a6400a-" : "/tmp_network/a6400a-", nonce, "/chunk")
            || !build_nonce_path(meta_path, "/tmp/jiritsu/a6400a-", nonce, ".meta")
            || !build_nonce_path(ack_path, "/tmp/jiritsu/a6400a-", nonce, ".ack")) {
        mark_error(log_fd, 30, &phase);
        while (1)
            sleep_ns(0, 250000000L);
    }

    pos = 0;
    append_str(msg, sizeof(msg), &pos, "__A6400A_WORKER_LOG__:");
    append_str(msg, sizeof(msg), &pos, nonce);
    append_str(msg, sizeof(msg), &pos, "\n");
    log_text(log_fd, msg);

    read_and_log_meminfo(log_fd, "RAW_START", &dummy_free);
    evict_path_list(own_cache_paths);
    if (partition_dump)
        evict_path_list(boot_kmod_paths);
    read_and_log_meminfo(log_fd, "START", &dummy_free);
    log_status_fields(log_fd);
    if (!log_uptime(log_fd, "START"))
        mark_error(log_fd, 41, &phase);
    if (!check_and_log_swap(log_fd))
        log_text(log_fd, "Swap state is active or unqualified; data staging forbidden\n");
    if (private_mode) {
        if (!is_real_dir("/tmp") || !is_real_dir("/tmp/jiritsu"))
            mark_error(log_fd, 30, &phase);
    } else if (!is_real_dir("/tmp_network")) {
        mark_error(log_fd, 30, &phase);
    }
    if (!log_mountinfo(log_fd) && phase != PHASE_ERROR)
        mark_error(log_fd, 31, &phase);

    if (phase != PHASE_ERROR) {
        populate_report(&rep, private_mode ? ram_dir : "/tmp_network", private_mode ? 1u : 0u);
        if (write_report_file(meta_path, &rep) != 0
                || rep.operation_result != 0) {
            mark_error(log_fd, 42, &phase);
        } else {
            if (private_mode)
                owned_ram = 1;
            pos = 0;
            append_str(msg, sizeof(msg), &pos, "__A6400A_PROBE_META__:");
            append_str(msg, sizeof(msg), &pos, nonce);
            append_str(msg, sizeof(msg), &pos, "\n");
            log_text(log_fd, msg);
        }
    }

    while (1) {
        char ack_buf[128];
        unsigned ack_len = 0;
        if (read_proc_file(ack_path, ack_buf, sizeof(ack_buf), &ack_len) == 0
                && ack_len > 0 && ack_buf[ack_len - 1] == '\n') {
            char *nl = ack_buf;
            char *toks[5];
            unsigned nt;
            while (*nl && *nl != '\n' && *nl != '\r')
                ++nl;
            *nl = 0;
            nt = split_ws(ack_buf, toks, 4);
            if (nt == 4 && equal(toks[0], nonce)) {
                uint32_t ack_dev = 0, ack_magic = 0;
                if (number(toks[2], &ack_dev) && number(toks[3], &ack_magic)) {
                    const char *op = toks[1];
                    if (equal(op, "cleanup")) {
                        if (phase != PHASE_CLEAN) {
                            if (owned_ram == 1) {
                                long urc = call3(10, (long)chunk_path, 0, 0);
                                if (urc != 0 && urc != -2) {
                                    mark_error(log_fd, 49, &phase);
                                } else if (private_mode) {
                                    populate_report(&rep, ram_dir, 3u);
                                    if (write_report_file(meta_path, &rep) == 0
                                            && rep.operation_result == 0 && rep.rollback_result == 0) {
                                        owned_ram = 0;
                                    } else {
                                        mark_error(log_fd, 49, &phase);
                                    }
                                } else if (call3(40, (long)ram_dir, 0, 0) == 0) {
                                    owned_ram = 0;
                                } else {
                                    mark_error(log_fd, 49, &phase);
                                }
                            }
                            if (owned_ram == 0) {
                                phase = PHASE_CLEAN;
                                read_and_log_meminfo(log_fd, "CLEAN", &dummy_free);
                                if (!log_uptime(log_fd, "CLEAN")) {
                                    mark_error(log_fd, 41, &phase);
                                } else {
                                    pos = 0;
                                    append_str(msg, sizeof(msg), &pos, "__A6400A_PROBE_CLEAN__:");
                                    append_str(msg, sizeof(msg), &pos, nonce);
                                    append_str(msg, sizeof(msg), &pos, "\n");
                                    log_text(log_fd, msg);
                                }
                            }
                        }
                    } else if ((equal(op, "4k") && phase == PHASE_META)
                               || (equal(op, "512k") && phase == PHASE_4K)) {
                        int is_512k = equal(op, "512k");
                        unsigned checks = 0;
                        uint32_t free_kib = 0;
                        int have_mem = 0, native_rc, swap_ok;
                        if (is_512k) {
                            long urc = call3(10, (long)chunk_path, 0, 0);
                            if (urc != 0 && urc != -2) {
                                mark_error(log_fd, 45, &phase);
                                sleep_ns(0, 250000000L);
                                continue;
                            }
                        }
                        evict_single_file("/tmp/jiritsu/out.txt");
                        evict_single_file(ack_path);
                        while (1) {
                            have_mem = read_and_log_meminfo(log_fd, is_512k ? "CHECK_512K" : "CHECK_4K", &free_kib);
                            ++checks;
                            if (have_mem && free_kib >= 8192u)
                                break;
                            if (checks == 1 && !kmod_evicted) {
                                kmod_evicted = 1;
                                evict_path_list(boot_kmod_paths);
                                have_mem = read_and_log_meminfo(log_fd, "AFTER_KMOD_FADVISE", &free_kib);
                                if (have_mem && free_kib >= 8192u)
                                    break;
                            }
                            pos = 0;
                            append_str(msg, sizeof(msg), &pos, "MEMORY_WAIT ");
                            append_str(msg, sizeof(msg), &pos, op);
                            append_str(msg, sizeof(msg), &pos, " Attempt=");
                            append_u32(msg, sizeof(msg), &pos, checks);
                            append_str(msg, sizeof(msg), &pos, " MemFreeKiB=");
                            if (have_mem)
                                append_u32(msg, sizeof(msg), &pos, free_kib);
                            append_str(msg, sizeof(msg), &pos, "\n");
                            log_text(log_fd, msg);
                            if (checks >= 5)
                                break;
                            sleep_ns(1, 0);
                        }
                        native_rc = check_binding(private_mode ? ram_dir : "/tmp_network",
                            private_mode, ack_dev, ack_magic);
                        swap_ok = check_and_log_swap(log_fd);
                        pos = 0;
                        append_str(msg, sizeof(msg), &pos, "GATE ");
                        append_str(msg, sizeof(msg), &pos, op);
                        append_str(msg, sizeof(msg), &pos, " MemFreeKiB=");
                        if (have_mem)
                            append_u32(msg, sizeof(msg), &pos, free_kib);
                        append_str(msg, sizeof(msg), &pos, " NativeCheckExit=");
                        append_i32(msg, sizeof(msg), &pos, native_rc);
                        append_str(msg, sizeof(msg), &pos, " Device=");
                        append_u32(msg, sizeof(msg), &pos, ack_dev);
                        append_str(msg, sizeof(msg), &pos, " Magic=");
                        append_u32(msg, sizeof(msg), &pos, ack_magic);
                        append_str(msg, sizeof(msg), &pos, "\n");
                        log_text(log_fd, msg);
                        if (have_mem && free_kib >= 8192u && native_rc == 0 && swap_ok) {
                            int stage_err = 0;
                            unsigned char digest[32];
                            if (owned_ram == 0) {
                                if (call3(39, (long)ram_dir, 0700, 0) == 0)
                                    owned_ram = 1;
                                else
                                    stage_err = 43;
                            }
                            if (!stage_err)
                                stage_err = stage_and_hash_pattern(chunk_path, is_512k ? 128u : 1u, digest);
                            if (stage_err) {
                                mark_error(log_fd, (unsigned)stage_err, &phase);
                            } else {
                                read_and_log_meminfo(log_fd, is_512k ? "AFTER_512K" : "AFTER_4K", &dummy_free);
                                if (!log_uptime(log_fd, op)) {
                                    mark_error(log_fd, 41, &phase);
                                } else {
                                    pos = 0;
                                    append_str(msg, sizeof(msg), &pos, "__A6400A_PROBE_DATA__:");
                                    append_str(msg, sizeof(msg), &pos, op);
                                    append_str(msg, sizeof(msg), &pos, ":");
                                    append_str(msg, sizeof(msg), &pos, nonce);
                                    append_str(msg, sizeof(msg), &pos, "\n");
                                    append_hex(msg, sizeof(msg), &pos, digest, 32);
                                    append_str(msg, sizeof(msg), &pos, "  ");
                                    append_str(msg, sizeof(msg), &pos, chunk_path);
                                    append_str(msg, sizeof(msg), &pos, "\n");
                                    log_text(log_fd, msg);
                                    phase = is_512k ? PHASE_512K : PHASE_4K;
                                }
                            }
                        } else {
                            log_text(log_fd, "RAM binding or free-memory recheck failed\n");
                            mark_error(log_fd, 44, &phase);
                        }
                    }
                }
            }
        }
        if (partition_dump && phase == PHASE_512K) {
            int result = native_command(nonce, ram_dir, chunk_path, &last_sequence);
            if (result)
                mark_error(log_fd, (unsigned)result, &phase);
            sleep_ns(0, 10000000L);
        } else {
            sleep_ns(0, 250000000L);
        }
    }
}

int probe_main(int argc, char **argv)
{
    struct ram_report report;
    const char *first_path = "/tmp_network";
    char workspace[64];
    uint32_t operation = 0;
    unsigned done = 0;
    if (argc == 2 && equal(argv[1], "--daemon"))
        return daemon_main();
    if ((argc == 4 && equal(argv[1], "--check-network"))
            || (argc == 5 && equal(argv[1], "--check-private"))) {
        uint32_t device, magic;
        unsigned first = argc == 5 ? 3 : 2;
        if (argc == 5) {
            if (!private_path(workspace, argv[2]))
                return 2;
            first_path = workspace;
        }
        if (!number(argv[first], &device) || !number(argv[first + 1], &magic))
            return 2;
        return check_binding(first_path, argc == 5, device, magic);
    }
    if (argc == 3) {
        if (!private_path(workspace, argv[2]))
            return 2;
        if (equal(argv[1], "--mount-private"))
            operation = 1;
        else if (equal(argv[1], "--unmount-private"))
            operation = 3;
        else
            return 2;
        first_path = workspace;
    } else if (argc != 1) {
        return 2;
    }
    populate_report(&report, first_path, operation);
    while (done < sizeof(report)) {
        long count = call3(4, 1, (long)((unsigned char *)&report + done), sizeof(report) - done);
        if (count <= 0 || (unsigned long)count > sizeof(report) - done)
            return 3;
        done += (unsigned)count;
    }
    return report.operation_result || (report.operation == 3 && report.rollback_result) ? 1 : 0;
}

__asm__(
    ".text\n"
    ".arm\n"
    ".align 2\n"
    ".global _start\n"
    ".type _start, %function\n"
    "_start:\n"
    "ldr r0, [sp]\n"
    "add r1, sp, #4\n"
    "blx probe_main\n"
    "mov r7, #1\n"
    "svc 0\n"
    "b .\n"
);
