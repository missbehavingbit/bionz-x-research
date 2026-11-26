/* Bounded read-only Mod Loader v2 SD package reader (FAT32 + exFAT on /dev/mmca1 250:11). */
#ifndef MLV2_BARE_ARM
#define _DEFAULT_SOURCE
#define _FILE_OFFSET_BITS 64
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>
#else
#include <asm/stat.h>
#include <stdint.h>
typedef int ssize_t;
#define O_RDONLY   00000000
#define O_WRONLY   00000001
#define O_CREAT    00000100
#define O_EXCL     00000200
#define O_NONBLOCK 00004000
#define O_NOFOLLOW 00400000
#define S_IFMT     00170000
#define S_IFBLK    00060000

static inline long sc1(long n, long a) {
    register long r7 __asm__("r7") = n, r0 __asm__("r0") = a;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7) : "memory", "cc");
    return r0;
}
static inline long sc2(long n, long a, long b) {
    register long r7 __asm__("r7") = n, r0 __asm__("r0") = a, r1 __asm__("r1") = b;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7), "r"(r1) : "memory", "cc");
    return r0;
}
static inline long sc3(long n, long a, long b, long c) {
    register long r7 __asm__("r7") = n, r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7), "r"(r1), "r"(r2) : "memory", "cc");
    return r0;
}
static inline long sc5(long n, long a, long b, long c, long d, long e) {
    register long r7 __asm__("r7") = n, r0 __asm__("r0") = a, r1 __asm__("r1") = b;
    register long r2 __asm__("r2") = c, r3 __asm__("r3") = d, r4 __asm__("r4") = e;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7), "r"(r1), "r"(r2), "r"(r3), "r"(r4) : "memory", "cc");
    return r0;
}
static int sys_open(const char *p, int f, int m) { return (int)sc3(5, (long)p, f, m); }
static int sys_close(int fd) { return (int)sc1(6, fd); }
static int sys_unlink(const char *p) { return (int)sc1(10, (long)p); }
static ssize_t sys_read(int fd, void *b, unsigned n) { return (ssize_t)sc3(3, fd, (long)b, n); }
static ssize_t sys_write(int fd, const void *b, unsigned n) { return (ssize_t)sc3(4, fd, (long)b, n); }
static int sys_fstat64(int fd, struct stat64 *st) { return (int)sc2(197, fd, (long)st); }
static int sys_pread512(int fd, void *out, uint64_t off, unsigned size) {
    uint64_t res = 0;
    if (sc5(140, fd, (long)(uint32_t)(off >> 32), (long)(uint32_t)off, (long)&res, 0) < 0) return -1;
    unsigned char *p = (unsigned char *)out;
    unsigned got = 0;
    while (got < size) {
        ssize_t r = sys_read(fd, p + got, size - got);
        if (r <= 0) return -1;
        got += (unsigned)r;
    }
    return 0;
}
#endif

#define SECTOR          512u
#define IO_CHUNK        4096u
#define MAX_ARCHIVE     (4u * 1024u * 1024u)
#define FAT32_EOC       0x0ffffff8u
#define EXFAT_EOC       0xfffffff8u
#ifndef STAGE
#define STAGE "/tmp_bt/mlv2"
#endif

struct fs_info {
    unsigned is_exfat;
    uint32_t total_sectors;
    uint32_t fat_lba;
    uint32_t fat_sectors;
    uint32_t data_lba;
    uint32_t spc;
    uint32_t max_cluster;
    uint32_t root_cluster;
};

struct entry {
    uint32_t cluster;
    uint32_t bytes;
    unsigned directory;
    unsigned no_fat_chain;
};

static unsigned char io_buf[IO_CHUNK];
static unsigned char sec_buf[SECTOR];

static void zero_mem(void *p, unsigned n) {
    unsigned char *b = (unsigned char *)p;
    for (unsigned i = 0; i < n; i++) b[i] = 0;
}

static int eq_bytes(const void *a, const void *b, unsigned n) {
    const unsigned char *pa = (const unsigned char *)a, *pb = (const unsigned char *)b;
    for (unsigned i = 0; i < n; i++) if (pa[i] != pb[i]) return 0;
    return 1;
}

static char lower_ascii(char c) { return (c >= 'A' && c <= 'Z') ? (char)(c + 32) : c; }

static int streq_ci(const char *a, const char *b) {
    while (*a && *b) {
        if (lower_ascii(*a) != lower_ascii(*b)) return 0;
        a++;
        b++;
    }
    return *a == 0 && *b == 0;
}

static int streq_exact(const char *a, const char *b) {
    while (*a && *b) {
        if (*a != *b) return 0;
        a++;
        b++;
    }
    return *a == 0 && *b == 0;
}

static uint32_t le16(const unsigned char *p) { return (uint32_t)p[0] | ((uint32_t)p[1] << 8); }
static uint32_t le32(const unsigned char *p) { return le16(p) | (le16(p + 2) << 16); }
static uint64_t le64(const unsigned char *p) { return (uint64_t)le32(p) | ((uint64_t)le32(p + 4) << 32); }

static int read_sectors(int fd, void *out, uint32_t lba, uint32_t sectors, uint32_t limit_sectors) {
    if (!sectors || sectors > (IO_CHUNK / SECTOR)) return -1;
    if (limit_sectors && (lba >= limit_sectors || sectors > limit_sectors - lba)) return -1;
    uint64_t off = (uint64_t)lba * SECTOR;
    unsigned bytes = sectors * SECTOR;
#ifdef MLV2_BARE_ARM
    return sys_pread512(fd, out, off, bytes);
#else
    return pread(fd, out, bytes, (off_t)off) == (ssize_t)bytes ? 0 : -1;
#endif
}

static int valid_cluster(const struct fs_info *fs, uint32_t c) {
    return c >= 2u && c <= fs->max_cluster;
}

static uint32_t cluster_lba(const struct fs_info *fs, uint32_t c) {
    return fs->data_lba + (c - 2u) * fs->spc;
}

static int next_cluster(int fd, const struct fs_info *fs, uint32_t *cluster) {
    uint32_t fat_sec_idx = (*cluster) >> 7; /* 128 32-bit entries per 512-byte sector */
    uint32_t ent_off = ((*cluster) & 127u) * 4u;
    if (fat_sec_idx >= fs->fat_sectors) return -1;
    if (read_sectors(fd, sec_buf, fs->fat_lba + fat_sec_idx, 1, fs->total_sectors)) return -1;
    uint32_t v = le32(sec_buf + ent_off);
    *cluster = fs->is_exfat ? v : (v & 0x0fffffffu);
    return 0;
}

static int parse_fs(int fd, struct fs_info *fs) {
    zero_mem(fs, sizeof(*fs));
    if (read_sectors(fd, sec_buf, 0, 1, 0) || le16(sec_buf + 510) != 0xaa55u) return -1;

    /* Check exFAT VBR ("EXFAT   " at offset 3). */
    if (eq_bytes(sec_buf + 3, "EXFAT   ", 8)) {
        unsigned bps_shift = sec_buf[108], spc_shift = sec_buf[109], nfats = sec_buf[110];
        uint64_t vol_len = le64(sec_buf + 72);
        uint32_t fat_off = le32(sec_buf + 80), fat_len = le32(sec_buf + 84);
        uint32_t heap_off = le32(sec_buf + 88), clu_cnt = le32(sec_buf + 92), root_clu = le32(sec_buf + 96);
        if (bps_shift != 9u || spc_shift > 12u || (nfats != 1u && nfats != 2u)) return -1;
        if (fat_off < 24u || !fat_len || !clu_cnt || root_clu < 2u || root_clu > clu_cnt + 1u) return -1;
        uint32_t spc = 1u << spc_shift;
        uint64_t min_heap = (uint64_t)fat_off + (uint64_t)nfats * fat_len;
        uint64_t end_lba = (uint64_t)heap_off + (uint64_t)clu_cnt * spc;
        if (heap_off < min_heap || end_lba > 0xffffffffULL || (vol_len && end_lba > vol_len)) return -1;
        fs->is_exfat = 1;
        fs->total_sectors = vol_len && vol_len <= 0xffffffffULL ? (uint32_t)vol_len : (uint32_t)end_lba;
        fs->fat_lba = fat_off;
        fs->fat_sectors = fat_len;
        fs->data_lba = heap_off;
        fs->spc = spc;
        fs->max_cluster = clu_cnt + 1u;
        fs->root_cluster = root_clu;
        return 0;
    }

    /* Standard FAT32 VBR/BPB. */
    uint32_t bps = le16(sec_buf + 11), spc = sec_buf[13], rsvd = le16(sec_buf + 14);
    uint32_t nfats = sec_buf[16], root_ent = le16(sec_buf + 17), tot16 = le16(sec_buf + 19);
    uint32_t fatsz16 = le16(sec_buf + 22), tot32 = le32(sec_buf + 32);
    uint32_t tot_sec = tot32 ? tot32 : tot16;
    uint32_t fatsz32 = le32(sec_buf + 36), root_clu = le32(sec_buf + 44);
    if (bps != SECTOR || !spc || (spc & (spc - 1u)) || spc > 128u) return -1;
    if (!rsvd || (nfats != 1u && nfats != 2u) || root_ent != 0u || (tot16 && tot32) || fatsz16 != 0u) return -1;
    if (tot_sec < 2048u || !fatsz32 || root_clu < 2u) return -1;
    uint64_t data_lba = (uint64_t)rsvd + (uint64_t)nfats * fatsz32;
    if (data_lba >= tot_sec) return -1;
    uint32_t data_secs = tot_sec - (uint32_t)data_lba;
    uint32_t clu_cnt = 0;
    for (uint32_t s = spc; s > 1u; s >>= 1) data_secs >>= 1;
    clu_cnt = data_secs;
    if (clu_cnt < 1u || root_clu > clu_cnt + 1u) return -1;
    fs->is_exfat = 0;
    fs->total_sectors = tot_sec;
    fs->fat_lba = rsvd;
    fs->fat_sectors = fatsz32;
    fs->data_lba = (uint32_t)data_lba;
    fs->spc = spc;
    fs->max_cluster = clu_cnt + 1u;
    fs->root_cluster = root_clu;
    return 0;
}

static int find_fat32(int fd, const struct fs_info *fs, const char *wanted, struct entry *match) {
    char lfn[66];
    zero_mem(lfn, sizeof(lfn));
    unsigned remaining = 0, sequence = 0;
    unsigned char checksum = 0;
    static const unsigned offsets[13] = {1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30};
    uint32_t cluster = fs->root_cluster;
    unsigned secs_seen = 0;
    while (cluster < FAT32_EOC && secs_seen < 256u) {
        if (!valid_cluster(fs, cluster)) return -1;
        uint32_t base_lba = cluster_lba(fs, cluster);
        for (uint32_t s = 0; s < fs->spc && secs_seen < 256u; s++, secs_seen++) {
            if (read_sectors(fd, sec_buf, base_lba + s, 1, fs->total_sectors)) return -1;
            for (unsigned off = 0; off < SECTOR; off += 32u) {
                const unsigned char *e = sec_buf + off;
                if (!e[0]) return 0;
                if (e[0] == 0xe5u) { sequence = 0; continue; }
                if (e[11] == 15u) {
                    unsigned order = e[0] & 31u;
                    if (e[0] & 64u) {
                        sequence = remaining = order <= 5u ? order : 0u;
                        checksum = e[13];
                        zero_mem(lfn, sizeof(lfn));
                    }
                    if (!order || order != remaining || e[13] != checksum) { sequence = 0; continue; }
                    for (unsigned i = 0; i < 13u; i++) {
                        uint32_t c = le16(e + offsets[i]);
                        lfn[(order - 1u) * 13u + i] = c == 0xffffu ? 0 : c <= 127u ? (char)c : '?';
                    }
                    remaining--;
                    continue;
                }
                unsigned char actual = 0;
                for (unsigned i = 0; i < 11u; i++)
                    actual = (unsigned char)(((actual & 1u ? 128u : 0u) | (actual >> 1)) + e[i]);
                char short_name[13];
                unsigned n = 0;
                for (unsigned i = 0; i < 8u && e[i] != ' '; i++) short_name[n++] = (char)e[i];
                if (e[8] != ' ') {
                    short_name[n++] = '.';
                    for (unsigned i = 8u; i < 11u && e[i] != ' '; i++) short_name[n++] = (char)e[i];
                }
                short_name[n] = 0;
                const char *name = (sequence && !remaining && actual == checksum) ? lfn : short_name;
                sequence = 0;
                if (!(e[11] & 8u) && streq_ci(name, wanted)) {
                    match->cluster = (le16(e + 20) << 16) | le16(e + 26);
                    match->bytes = le32(e + 28);
                    match->directory = !!(e[11] & 16u);
                    match->no_fat_chain = 0;
                    return 1;
                }
            }
        }
        if (next_cluster(fd, fs, &cluster)) return -1;
    }
    return cluster >= FAT32_EOC ? 0 : -1;
}

static int find_exfat(int fd, const struct fs_info *fs, const char *wanted, struct entry *match) {
    uint32_t cluster = fs->root_cluster;
    unsigned secs_seen = 0;
    unsigned state = 0, sec_rem = 0, name_rem = 0, name_pos = 0, is_dir = 0, no_chain = 0;
    uint32_t first_clu = 0;
    uint64_t valid_len = 0, data_len = 0;
    char name_buf[66];
    zero_mem(name_buf, sizeof(name_buf));

    while (cluster < EXFAT_EOC && secs_seen < 256u) {
        if (!valid_cluster(fs, cluster)) return -1;
        uint32_t base_lba = cluster_lba(fs, cluster);
        for (uint32_t s = 0; s < fs->spc && secs_seen < 256u; s++, secs_seen++) {
            if (read_sectors(fd, sec_buf, base_lba + s, 1, fs->total_sectors)) return -1;
            for (unsigned off = 0; off < SECTOR; off += 32u) {
                const unsigned char *e = sec_buf + off;
                if (!e[0]) return 0;
                if (!(e[0] & 0x80u)) { state = 0; continue; }
                if (e[0] == 0x85u) {
                    sec_rem = e[1];
                    is_dir = !!(le16(e + 4) & 0x10u);
                    state = sec_rem >= 2u ? 1u : 0u;
                    zero_mem(name_buf, sizeof(name_buf));
                    name_pos = 0;
                } else if (state == 1u && e[0] == 0xc0u) {
                    no_chain = !!(e[1] & 2u);
                    name_rem = e[3];
                    valid_len = le64(e + 8);
                    first_clu = le32(e + 20);
                    data_len = le64(e + 24);
                    sec_rem--;
                    state = (name_rem > 0u && name_rem <= 64u && sec_rem > 0u) ? 2u : 0u;
                } else if (state == 2u && e[0] == 0xc1u) {
                    for (unsigned i = 0; i < 15u && name_rem > 0u; i++, name_rem--) {
                        uint32_t ch = le16(e + 2u + i * 2u);
                        if (name_pos < 64u) name_buf[name_pos++] = (ch > 0u && ch <= 127u) ? (char)ch : '?';
                    }
                    sec_rem--;
                    if (name_rem == 0u) {
                        name_buf[name_pos] = 0;
                        state = 0;
                        if (streq_ci(name_buf, wanted)) {
                            if (valid_len > data_len || valid_len > 0xffffffffULL) return -1;
                            match->cluster = first_clu;
                            match->bytes = (uint32_t)valid_len;
                            match->directory = is_dir;
                            match->no_fat_chain = no_chain;
                            return 1;
                        }
                    } else if (sec_rem == 0u) {
                        state = 0;
                    }
                } else {
                    state = 0;
                }
            }
        }
        if (next_cluster(fd, fs, &cluster)) return -1;
    }
    return cluster >= EXFAT_EOC ? 0 : -1;
}

static int find_entry(int fd, const struct fs_info *fs, const char *wanted, struct entry *match) {
    return fs->is_exfat ? find_exfat(fd, fs, wanted, match) : find_fat32(fd, fs, wanted, match);
}

static int write_all(int fd, const unsigned char *buf, unsigned len) {
    unsigned off = 0;
    while (off < len) {
#ifdef MLV2_BARE_ARM
        ssize_t w = sys_write(fd, buf + off, len - off);
#else
        ssize_t w = write(fd, buf + off, len - off);
#endif
        if (w <= 0) return -1;
        off += (unsigned)w;
    }
    return 0;
}

static int extract_archive(int fd, const struct fs_info *fs, struct entry item, const char *out_path) {
#ifdef MLV2_BARE_ARM
    int out = sys_open(out_path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
#else
    int out = open(out_path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
#endif
    if (out < 0) return -1;
    int result = 0;
    unsigned clusters_visited = 0;
    while (item.bytes) {
        if (!valid_cluster(fs, item.cluster) || ++clusters_visited > 2048u) {
            result = -1;
            break;
        }
        uint32_t base_lba = cluster_lba(fs, item.cluster);
        uint32_t sec_left = fs->spc;
        uint32_t cur_lba = base_lba;
        while (sec_left && item.bytes) {
            uint32_t chunk_secs = sec_left < (IO_CHUNK / SECTOR) ? sec_left : (IO_CHUNK / SECTOR);
            uint32_t chunk_bytes = chunk_secs * SECTOR;
            if (read_sectors(fd, io_buf, cur_lba, chunk_secs, fs->total_sectors)) {
                result = -1;
                break;
            }
            unsigned to_write = item.bytes < chunk_bytes ? item.bytes : chunk_bytes;
            if (write_all(out, io_buf, to_write)) {
                result = -1;
                break;
            }
            item.bytes -= to_write;
            cur_lba += chunk_secs;
            sec_left -= chunk_secs;
        }
        if (result || !item.bytes) break;
        if (item.no_fat_chain) {
            item.cluster++;
        } else if (next_cluster(fd, fs, &item.cluster)) {
            result = -1;
            break;
        }
    }
#ifdef MLV2_BARE_ARM
    if (sys_close(out)) result = -1;
    if (result) sys_unlink(out_path);
#else
    if (close(out)) result = -1;
    if (result) unlink(out_path);
#endif
    return result;
}

static int package_ok(int fd, struct fs_info *fs, struct entry *archive) {
    struct entry hit;
    if (parse_fs(fd, fs) ||
        find_entry(fd, fs, "disable_hook", &hit) != 0 ||
        find_entry(fd, fs, "disable.txt", &hit) != 0 ||
        find_entry(fd, fs, "MLV2.OFF", &hit) != 0 ||
        find_entry(fd, fs, "MLV2.TAR", archive) != 1 ||
        archive->directory || !archive->bytes || archive->bytes > MAX_ARCHIVE)
        return -1;
    return 0;
}

int card_read_main(int argc, char **argv) {
    int presence_only = argc == 2 && streq_exact(argv[1], "--present");
    if (argc != 1 && !presence_only) return 2;

    const char *dev_path = "/dev/mmca1";
    const char *out_path = STAGE "/card.tar";
#if defined(MLV2_HOST_TEST) && !defined(MLV2_BARE_ARM)
    const char *env_img = getenv("MLV2_CARD_READ_IMAGE");
    const char *env_out = getenv("MLV2_CARD_READ_OUT");
    if (env_img && *env_img) dev_path = env_img;
    if (env_out && *env_out) out_path = env_out;
#endif

#ifdef MLV2_BARE_ARM
    int fd = sys_open(dev_path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK, 0);
    if (fd < 0) return 3;
    struct stat64 st;
    zero_mem(&st, sizeof(st));
    int stat_ok = (sys_fstat64(fd, &st) == 0);
    uint64_t expected_rdev = 0xfa0bULL;
#ifdef MLV2_QEMU_RAM15
    if (stat_ok && st.st_rdev == 0x010fULL) expected_rdev = 0x010fULL;
#endif
    int blk_ok = (stat_ok &&
                  (st.st_mode & S_IFMT) == S_IFBLK &&
                  st.st_rdev == expected_rdev);
#else
    int fd = open(dev_path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return 3;
    struct stat st;
    int blk_ok = (fstat(fd, &st) == 0 && S_ISBLK(st.st_mode) &&
                  major(st.st_rdev) == 250 && minor(st.st_rdev) == 11);
#ifdef MLV2_HOST_TEST
    if (env_img && *env_img) blk_ok = (fstat(fd, &st) == 0 && S_ISREG(st.st_mode));
#endif
#endif

    struct fs_info fs;
    struct entry archive;
    int result = 1;
    if (blk_ok && !package_ok(fd, &fs, &archive))
        result = presence_only ? 0 : (extract_archive(fd, &fs, archive, out_path) ? 2 : 0);
#ifdef MLV2_BARE_ARM
    sys_close(fd);
#else
    close(fd);
#endif
    return result;
}

#ifdef MLV2_BARE_ARM
__attribute__((naked, noreturn)) void _start(void) {
    __asm__ volatile(
        "ldr r0, [sp]\n\t"
        "add r1, sp, #4\n\t"
        "bic sp, sp, #7\n\t"
        "bl card_read_main\n\t"
        "mov r7, #248\n\t"
        "svc 0\n\t"
        "b .\n\t");
}
#else
int main(int argc, char **argv) { return card_read_main(argc, argv); }
#endif
