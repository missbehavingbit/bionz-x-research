/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * QEMU 10.1 TCG Oracle Plugin v3 (Plugin API v5) for Unicorn parity & MMIO synthesis.
 *
 * Modes:
 *   mode=insn  Record executed instructions (with optional regs=1, dump_pages=1,
 *              trigger_pc=0x..., triggers=0xPC1[:LIM1];0xPC2[:LIM2],
 *              trigger_cpsr_mode=0x10) up to limit.
 *   mode=mmio  Record MMIO accesses in [pa_start, pa_end) with device name and poll summaries.
 */
#include <glib.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <qemu-plugin.h>

QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;

#define MAX_CPUS 4
#define DEFAULT_LIMIT 4096
#define MAX_DUMP_PAGES 64
#define MAX_TRIGGERS 128

typedef enum {
    MODE_MMIO = 0,
    MODE_INSN = 1,
} OracleMode;

typedef struct {
    uint64_t pc;
    unsigned limit;
    unsigned samples[MAX_CPUS];
    bool active[MAX_CPUS];
    bool done[MAX_CPUS];
    uint64_t dumped_pages[MAX_CPUS][MAX_DUMP_PAGES];
    unsigned dumped_count[MAX_CPUS];
} TriggerSlot;

static OracleMode g_mode = MODE_MMIO;
static uint64_t g_start_pc = 0;
static uint64_t g_end_pc = UINT64_MAX;
static int g_trigger_cpsr_mode = -1;
static uint64_t g_pa_start = 0xF0000000ULL;
static uint64_t g_pa_end = 0xFFFFFFFFULL;
static unsigned g_limit = DEFAULT_LIMIT;
static bool g_regs = false;
static bool g_dump_pages = false;

static TriggerSlot g_triggers[MAX_TRIGGERS];
static unsigned g_num_triggers = 0;
static bool g_has_explicit_triggers = false;
static unsigned g_mmio_samples[MAX_CPUS];

static const char *REG_NAMES[16] = {
    "r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
    "r8", "r9", "r10", "r11", "r12", "sp", "lr", "cpsr"
};
static struct qemu_plugin_register *g_reg_handles[MAX_CPUS][16];
static bool g_regs_init[MAX_CPUS];

typedef struct {
    uint64_t pc;
    uint32_t insn_bytes;
    uint8_t insn_size;
} InsnMeta;

typedef struct {
    uint64_t pc;
    uint64_t pa;
    uint64_t last_val;
    unsigned count;
    char dev[64];
} PollTracker;

static PollTracker g_last_read[MAX_CPUS];

#if QEMU_PLUGIN_VERSION >= 7
static void on_vcpu_init(unsigned int cpu, void *userdata)
#else
static void on_vcpu_init(qemu_plugin_id_t id, unsigned int cpu)
#endif
{
    GArray *regs;
    if (cpu >= MAX_CPUS) {
        return;
    }
    regs = qemu_plugin_get_registers();
    if (!regs) {
        return;
    }
    for (guint i = 0; i < regs->len; i++) {
        qemu_plugin_reg_descriptor *desc =
            &g_array_index(regs, qemu_plugin_reg_descriptor, i);
        for (int r = 0; r < 16; r++) {
            if (desc->name && strcmp(desc->name, REG_NAMES[r]) == 0) {
                g_reg_handles[cpu][r] = desc->handle;
            }
        }
    }
    g_array_free(regs, TRUE);
    g_regs_init[cpu] = true;
}

static uint32_t read_u32_reg(unsigned cpu, int idx)
{
    uint32_t val = 0;
    GByteArray *buf;
    if (cpu >= MAX_CPUS || !g_regs_init[cpu] || !g_reg_handles[cpu][idx]) {
        return 0;
    }
    buf = g_byte_array_sized_new(4);
    if (qemu_plugin_read_register(g_reg_handles[cpu][idx], buf) && buf->len >= 4) {
        memcpy(&val, buf->data, 4);
    }
    g_byte_array_free(buf, TRUE);
    return val;
}

static void maybe_dump_page(unsigned cpu, unsigned win, uint64_t vaddr)
{
    TriggerSlot *slot = &g_triggers[win];
    uint64_t page = vaddr & ~0xFFFULL;
    GByteArray *buf;
    char *out;
    size_t pos;

    if (!g_dump_pages || cpu >= MAX_CPUS || slot->dumped_count[cpu] >= MAX_DUMP_PAGES) {
        return;
    }
    for (unsigned i = 0; i < slot->dumped_count[cpu]; i++) {
        if (slot->dumped_pages[cpu][i] == page) {
            return;
        }
    }
    buf = g_byte_array_sized_new(4096);
    if (qemu_plugin_read_memory_vaddr(page, buf, 4096) && buf->len == 4096) {
        slot->dumped_pages[cpu][slot->dumped_count[cpu]++] = page;
        out = g_malloc(4096 * 2 + 160);
        pos = (size_t)snprintf(out, 160,
                               "{\"type\":\"page\",\"win\":%u,\"cpu\":%u,\"vaddr\":\"0x%08" PRIx64 "\",\"hex\":\"",
                               win, cpu, page);
        for (size_t i = 0; i < 4096; i++) {
            static const char hex[] = "0123456789abcdef";
            uint8_t b = buf->data[i];
            out[pos++] = hex[b >> 4];
            out[pos++] = hex[b & 0xF];
        }
        memcpy(out + pos, "\"}\n", 4);
        qemu_plugin_outs(out);
        g_free(out);
    }
    g_byte_array_free(buf, TRUE);
}

static void flush_poll(unsigned cpu)
{
    if (g_last_read[cpu].count > 2) {
        char buf[320];
        snprintf(buf, sizeof(buf),
                 "{\"type\":\"poll_summary\",\"cpu\":%u,\"pc\":\"0x%08" PRIx64
                 "\",\"pa\":\"0x%08" PRIx64 "\",\"dev\":\"%s\",\"count\":%u,\"last_val\":\"0x%08" PRIx64 "\"}\n",
                 cpu, g_last_read[cpu].pc, g_last_read[cpu].pa,
                 g_last_read[cpu].dev, g_last_read[cpu].count, g_last_read[cpu].last_val);
        qemu_plugin_outs(buf);
    }
    g_last_read[cpu].count = 0;
}

static void on_mmio_access(unsigned cpu, qemu_plugin_meminfo_t info,
                           uint64_t va, void *opaque)
{
    struct qemu_plugin_hwaddr *hwaddr;
    qemu_plugin_mem_value val;
    uint64_t pa, data;
    uint64_t pc = (uint64_t)(uintptr_t)opaque;
    const char *dev;
    bool is_write;
    char buf[320];

    if (cpu >= MAX_CPUS || g_mmio_samples[cpu] >= g_limit) {
        return;
    }
    hwaddr = qemu_plugin_get_hwaddr(info, va);
    if (!hwaddr) {
        return;
    }
    pa = qemu_plugin_hwaddr_phys_addr(hwaddr);
    if (pa < g_pa_start || pa > g_pa_end) {
        return;
    }
    dev = qemu_plugin_hwaddr_device_name(hwaddr);
    if (!dev) {
        dev = "unknown";
    }
    val = qemu_plugin_mem_get_value(info);
    switch (val.type) {
    case QEMU_PLUGIN_MEM_VALUE_U8:
        data = val.data.u8;
        break;
    case QEMU_PLUGIN_MEM_VALUE_U16:
        data = val.data.u16;
        break;
    case QEMU_PLUGIN_MEM_VALUE_U32:
        data = val.data.u32;
        break;
    case QEMU_PLUGIN_MEM_VALUE_U64:
        data = val.data.u64;
        break;
    default:
        return;
    }

    is_write = qemu_plugin_mem_is_store(info);
    if (!is_write) {
        if (g_last_read[cpu].count > 0 &&
            g_last_read[cpu].pc == pc &&
            g_last_read[cpu].pa == pa &&
            g_last_read[cpu].last_val == data) {
            g_last_read[cpu].count++;
            return;
        }
        flush_poll(cpu);
        g_last_read[cpu].pc = pc;
        g_last_read[cpu].pa = pa;
        g_last_read[cpu].last_val = data;
        g_last_read[cpu].count = 1;
        snprintf(g_last_read[cpu].dev, sizeof(g_last_read[cpu].dev), "%s", dev);
    } else {
        flush_poll(cpu);
    }

    snprintf(buf, sizeof(buf),
             "{\"type\":\"mmio\",\"cpu\":%u,\"sample\":%u,\"pc\":\"0x%08" PRIx64
             "\",\"op\":\"%c%u\",\"pa\":\"0x%08" PRIx64 "\",\"va\":\"0x%08" PRIx64
             "\",\"val\":\"0x%08" PRIx64 "\",\"dev\":\"%s\"}\n",
             cpu, ++g_mmio_samples[cpu], pc, is_write ? 'W' : 'R',
             1u << qemu_plugin_mem_size_shift(info), pa, va, data, dev);
    qemu_plugin_outs(buf);
}

static void on_insn_mem(unsigned cpu, qemu_plugin_meminfo_t info,
                        uint64_t va, void *opaque)
{
    if (cpu >= MAX_CPUS) {
        return;
    }
    for (unsigned w = 0; w < g_num_triggers; w++) {
        if (g_triggers[w].active[cpu] && !g_triggers[w].done[cpu]) {
            maybe_dump_page(cpu, w, va);
        }
    }
}

static void on_insn_exec(unsigned cpu, void *opaque)
{
    InsnMeta *meta = (InsnMeta *)opaque;
    uint64_t pa = 0;
    uint32_t r[16];
    bool any_active = false;
    char buf[512];

    if (cpu >= MAX_CPUS) {
        return;
    }

    for (unsigned w = 0; w < g_num_triggers; w++) {
        TriggerSlot *slot = &g_triggers[w];
        if (slot->done[cpu]) {
            continue;
        }
        if (!slot->active[cpu]) {
            if (g_has_explicit_triggers && meta->pc != slot->pc) {
                continue;
            }
            if (g_trigger_cpsr_mode >= 0) {
                uint32_t cpsr = read_u32_reg(cpu, 15);
                if ((int)(cpsr & 0x1F) != g_trigger_cpsr_mode) {
                    continue;
                }
            }
            slot->active[cpu] = true;
        }
        any_active = true;
    }
    if (!any_active) {
        return;
    }

    qemu_plugin_translate_vaddr(meta->pc, &pa);
    if (g_regs || g_dump_pages) {
        for (int i = 0; i < 16; i++) {
            r[i] = read_u32_reg(cpu, i);
        }
    }

    for (unsigned w = 0; w < g_num_triggers; w++) {
        TriggerSlot *slot = &g_triggers[w];
        if (!slot->active[cpu] || slot->done[cpu]) {
            continue;
        }
        if (g_dump_pages) {
            maybe_dump_page(cpu, w, meta->pc);
            maybe_dump_page(cpu, w, r[13]);
        }
        slot->samples[cpu]++;
        if (g_regs) {
            snprintf(buf, sizeof(buf),
                     "{\"type\":\"insn\",\"win\":%u,\"cpu\":%u,\"sample\":%u,\"pc\":\"0x%08" PRIx64
                     "\",\"pa\":\"0x%08" PRIx64 "\",\"size\":%u,\"bytes\":\"%08x\","
                     "\"regs\":[%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u]}\n",
                     w, cpu, slot->samples[cpu], meta->pc, pa, meta->insn_size, meta->insn_bytes,
                     r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7],
                     r[8], r[9], r[10], r[11], r[12], r[13], r[14], r[15]);
        } else {
            snprintf(buf, sizeof(buf),
                     "{\"type\":\"insn\",\"win\":%u,\"cpu\":%u,\"sample\":%u,\"pc\":\"0x%08" PRIx64
                     "\",\"pa\":\"0x%08" PRIx64 "\",\"size\":%u,\"bytes\":\"%08x\"}\n",
                     w, cpu, slot->samples[cpu], meta->pc, pa, meta->insn_size, meta->insn_bytes);
        }
        qemu_plugin_outs(buf);
        if (slot->samples[cpu] >= slot->limit) {
            slot->active[cpu] = false;
            slot->done[cpu] = true;
        }
    }
}

#if QEMU_PLUGIN_VERSION >= 7
static void on_tb_trans(struct qemu_plugin_tb *tb, void *userdata)
#else
static void on_tb_trans(qemu_plugin_id_t id, struct qemu_plugin_tb *tb)
#endif
{
    size_t n = qemu_plugin_tb_n_insns(tb);
    bool need_regs = g_regs || g_dump_pages || g_trigger_cpsr_mode >= 0;

    for (size_t i = 0; i < n; i++) {
        struct qemu_plugin_insn *insn = qemu_plugin_tb_get_insn(tb, i);
        uint64_t pc = qemu_plugin_insn_vaddr(insn);
        if (pc < g_start_pc || pc >= g_end_pc) {
            continue;
        }
        if (g_mode == MODE_MMIO) {
            qemu_plugin_register_vcpu_mem_cb(
                insn, on_mmio_access, QEMU_PLUGIN_CB_NO_REGS,
                QEMU_PLUGIN_MEM_RW, (void *)(uintptr_t)pc);
        } else {
            InsnMeta *meta = g_new0(InsnMeta, 1);
            size_t sz = qemu_plugin_insn_size(insn);
            uint32_t raw = 0;
            meta->pc = pc;
            meta->insn_size = (uint8_t)sz;
            qemu_plugin_insn_data(insn, &raw, sz > 4 ? 4 : sz);
            meta->insn_bytes = raw;
            qemu_plugin_register_vcpu_insn_exec_cb(
                insn, on_insn_exec,
                need_regs ? QEMU_PLUGIN_CB_R_REGS : QEMU_PLUGIN_CB_NO_REGS,
                meta);
            if (g_dump_pages) {
                qemu_plugin_register_vcpu_mem_cb(
                    insn, on_insn_mem, QEMU_PLUGIN_CB_NO_REGS,
                    QEMU_PLUGIN_MEM_RW, meta);
            }
        }
    }
}

#if QEMU_PLUGIN_VERSION >= 7
static void on_plugin_exit(void *opaque)
#else
static void on_plugin_exit(qemu_plugin_id_t id, void *opaque)
#endif
{
    for (unsigned cpu = 0; cpu < MAX_CPUS; cpu++) {
        char buf[160];
        flush_poll(cpu);
        unsigned total = g_mode == MODE_MMIO ? g_mmio_samples[cpu] : 0;
        if (g_mode == MODE_INSN) {
            for (unsigned w = 0; w < g_num_triggers; w++) {
                total += g_triggers[w].samples[cpu];
            }
        }
        snprintf(buf, sizeof(buf),
                 "{\"type\":\"summary\",\"cpu\":%u,\"samples\":%u,\"windows\":%u}\n",
                 cpu, total, g_num_triggers);
        qemu_plugin_outs(buf);
    }
}

static void add_trigger_spec(const char *spec)
{
    char *copy = g_strdup(spec);
    char *colon = strchr(copy, ':');
    uint64_t pc;
    unsigned lim = g_limit;
    if (colon) {
        *colon = '\0';
        lim = (unsigned)strtoul(colon + 1, NULL, 0);
    }
    pc = strtoull(copy, NULL, 0);
    if (g_num_triggers < MAX_TRIGGERS) {
        g_triggers[g_num_triggers].pc = pc;
        g_triggers[g_num_triggers].limit = lim;
        g_num_triggers++;
    }
    g_free(copy);
}

QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id,
                                          const qemu_info_t *info,
                                          int argc, char **argv)
{
    if (!info->system_emulation) {
        return -1;
    }
    for (int i = 0; i < argc; i++) {
        if (strcmp(argv[i], "mode=insn") == 0) {
            g_mode = MODE_INSN;
        } else if (strcmp(argv[i], "mode=mmio") == 0) {
            g_mode = MODE_MMIO;
        } else if (strncmp(argv[i], "start_pc=", 9) == 0) {
            g_start_pc = strtoull(argv[i] + 9, NULL, 0);
        } else if (strncmp(argv[i], "end_pc=", 7) == 0) {
            g_end_pc = strtoull(argv[i] + 7, NULL, 0);
        } else if (strncmp(argv[i], "trigger_pc=", 11) == 0) {
            g_has_explicit_triggers = true;
            add_trigger_spec(argv[i] + 11);
        } else if (strncmp(argv[i], "triggers=", 9) == 0) {
            g_has_explicit_triggers = true;
            char **parts = g_strsplit(argv[i] + 9, ";", MAX_TRIGGERS);
            for (int p = 0; parts[p] != NULL; p++) {
                if (parts[p][0] != '\0') {
                    add_trigger_spec(parts[p]);
                }
            }
            g_strfreev(parts);
        } else if (strncmp(argv[i], "trigger_cpsr_mode=", 18) == 0) {
            g_trigger_cpsr_mode = (int)strtol(argv[i] + 18, NULL, 0);
        } else if (strncmp(argv[i], "pa_start=", 9) == 0) {
            g_pa_start = strtoull(argv[i] + 9, NULL, 0);
        } else if (strncmp(argv[i], "pa_end=", 7) == 0) {
            g_pa_end = strtoull(argv[i] + 7, NULL, 0);
        } else if (strncmp(argv[i], "limit=", 6) == 0) {
            g_limit = (unsigned)strtoul(argv[i] + 6, NULL, 0);
        } else if (strcmp(argv[i], "regs=1") == 0) {
            g_regs = true;
        } else if (strcmp(argv[i], "dump_pages=1") == 0) {
            g_dump_pages = true;
        } else {
            fprintf(stderr, "unicorn_oracle_plugin: unknown arg '%s'\n", argv[i]);
            return -1;
        }
    }
    if (!g_has_explicit_triggers) {
        g_triggers[0].pc = 0;
        g_triggers[0].limit = g_limit;
        g_num_triggers = 1;
    } else {
        for (unsigned w = 0; w < g_num_triggers; w++) {
            if (g_triggers[w].limit == 0) {
                g_triggers[w].limit = g_limit;
            }
        }
    }
#if QEMU_PLUGIN_VERSION >= 7
    qemu_plugin_register_vcpu_init_cb(id, on_vcpu_init, NULL);
    qemu_plugin_register_vcpu_tb_trans_cb(id, on_tb_trans, NULL);
#else
    qemu_plugin_register_vcpu_init_cb(id, on_vcpu_init);
    qemu_plugin_register_vcpu_tb_trans_cb(id, on_tb_trans);
#endif
    qemu_plugin_register_atexit_cb(id, on_plugin_exit, NULL);
    qemu_plugin_outs("{\"type\":\"init\",\"status\":\"installed\",\"version\":3}\n");
    return 0;
}
