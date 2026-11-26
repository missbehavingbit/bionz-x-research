/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * QEMU TCG plugin: kexec / soft_restart handover guard (Custom OS prerequisite 4, E0638).
 *
 * TCG does not model cache contents, so a teardown that forgets to clean the
 * D-cache, leaves the MMU on, or skips a barrier still "works" in QEMU and
 * fails on hardware.  This plugin checks, when the handover CPU first
 * executes the declared entry address, that the architectural steps of the
 * mainline ARMv7 sequence (arch/arm/kernel/reboot.c __soft_restart ->
 * cpu_v7_proc_fin -> v7_flush_kern_cache_all -> cpu_v7_reset) were executed:
 *
 *   G1 SCTLR.M == 0          (MMU off)
 *   G2 SCTLR.C == 0          (D-cache off)
 *   G3 SCTLR.I == 0          (I-cache off)
 *   G4 >= min_setway unconditional DCCISW (c7,c14,2) after the last SCTLR
 *      write that left C == 0 (one full L1 set/way clean+invalidate pass)
 *   G5 ICIALLU / ICIALLUIS after the last DCCISW
 *   G6 DSB after the last cache-maintenance op
 *   G7 ISB after the last SCTLR write
 *   G8 CPSR.I and CPSR.F set (IRQ/FIQ masked)
 *   G9 every other vCPU that has run is parked: its last WFE/WFI is at most
 *      park_window instructions ago and it had SCTLR.C == 0 there
 *
 *   G10 (with dma=FILE) every DMA contract is quiescent: see dma_load() below
 *   G11 (with tlb=1 or strict_kernel=1) unconditional TLBI on the handover
 *       CPU after turning SCTLR.C off (or SCTLR.M off) and before the final DSB
 *   G12 (with gic=1 or strict_kernel=1) GICv1 distributor (0xF8001000) and CPU
 *       interface (0xF8000100) disabled at entry if either was ever enabled
 *   G13 (with l2=1 or strict_kernel=1) PL310 L2C-310 (0xF8002000) cleaned and
 *       invalidated by way, cache-synced, and disabled if ever enabled
 *
 * Unconfigured optional checks (outer L2, DMA, GIC, TLB, and DMA engines
 * outside the contract file) are reported explicitly in the summary footer.
 *
 * Arguments:
 *   entry=0xADDR     handover target virtual address (required)
 *   cpu=N            handover vCPU (default 0)
 *   skip=N           ignore the first N privileged executions of entry (default 0;
 *                    1 when the stock kernel itself first boots at the same address).
 *                    User-mode (CPSR.M == USR) executions of entry are always ignored.
 *   min_setway=N     required DCCISW count (default 1024: QEMU cortex-a5
 *                    CCSIDR 0xe01fe019 = 32 KiB, 4 ways x 256 sets)
 *   park_window=N    secondary park-loop bound in instructions (default 8)
 *   exit=1           _exit(0) on PASS, _exit(3) on FAIL after the report
 *   events=N         log the first N tracked events (default 64)
 *   dma=FILE         DMA quiescence contracts (E0639); adds MMIO tracking
 *   strict_unknown=0 report "observe" engines without failing (default 1)
 *   tlb=1            enable G11 TLB invalidation check (default 0)
 *   gic=1            enable G12 GICv1 quiescence check (default 0)
 *   l2=1             enable G13 PL310 outer cache clean & disable check (default 0)
 *   strict_kernel=1  shorthand enabling tlb=1, gic=1, l2=1 (default 0)
 *   trace=START:END  log PC/PA/value of IO accesses in [START, END) (E0640)
 *   trace_limit=N    maximum traced accesses (default 256)
 *   trace_ram=1      trace CPU RAM accesses in the range as well (E0642); device
 *                    (DMA) writes are not CPU accesses and are not seen
 *   exec_range=START:END  log executed translation blocks starting in the
 *                    virtual range, with LR (E0643); exec_limit=N (default 200)
 *
 * Decodes A32 and T32 MCR p15, DSB/ISB (and the CP15 c7 barrier forms),
 * WFE/WFI.  Conditional (cond != AL) maintenance is logged but not counted.
 */
#include <glib.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <qemu-plugin.h>

QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;

#define MAX_CPUS 8

enum {
    EV_SCTLR = 1, EV_DCCISW, EV_DC_OTHER, EV_ICIALL, EV_DSB, EV_ISB,
    EV_WFX, EV_ENTRY, EV_TLBI,
};

typedef struct {
    uint8_t kind;
    bool cond;      /* conditional encoding (cond != AL) */
    uint8_t rt;
} Op;

typedef struct {
    struct qemu_plugin_register *r[16];
    struct qemu_plugin_register *cpsr;
    struct qemu_plugin_register *sctlr;
    bool init;
} RegSet;

static uint64_t g_entry;
static unsigned g_skip;
static uint64_t g_entry_user_hits;
static bool g_have_entry;
static unsigned g_cpu;
static unsigned g_min_setway = 1024;
static unsigned g_park_window = 8;
static bool g_exit;
static unsigned g_events_max = 64;
static bool g_check_tlb, g_check_gic, g_check_l2;

#define GICC_CTLR_PA            0xF8000100ULL
#define GICD_CTLR_PA            0xF8001000ULL
#define L2X0_CTRL_PA            0xF8002100ULL
#define L2X0_CACHE_SYNC_PA      0xF8002730ULL
#define L2X0_CLEAN_INV_WAY_PA   0xF80027FCULL

static RegSet g_regs[MAX_CPUS];
static struct qemu_plugin_scoreboard *g_sb;
static qemu_plugin_u64 g_icount;

static GMutex g_lock;
static uint64_t g_seq;
static unsigned g_events_logged;
static bool g_checked;

/* Handover-CPU state */
static uint32_t g_sctlr_written;
static bool g_sctlr_known;
static uint64_t g_last_sctlr_seq, g_c_off_seq, g_m_off_seq;
static uint64_t g_dccisw_after_coff, g_dccisw_total, g_dc_cond;
static uint64_t g_last_dccisw_seq, g_last_cmo_seq, g_last_ic_seq;
static uint64_t g_last_dsb_seq, g_last_isb_seq;
static uint64_t g_last_tlbi_seq, g_tlbi_total, g_tlbi_cond;
static uint64_t g_dc_other;

/* GICv1 Distributor & CPU Interface state (G12) */
static bool g_ever_gicc_en, g_ever_gicd_en;
static uint32_t g_last_gicc_ctlr, g_last_gicd_ctlr;

/* PL310 L2C-310 state (G13) */
static bool g_ever_l2_en;
static uint32_t g_last_l2_ctrl, g_last_l2_clean_inv;
static uint64_t g_l2_last_en_seq, g_l2_clean_inv_seq, g_l2_sync_seq, g_l2_off_seq;

/* Per-vCPU park state */
static bool g_ran[MAX_CPUS];
static uint64_t g_wfx_icount[MAX_CPUS];
static bool g_wfx_seen[MAX_CPUS];
static uint32_t g_wfx_sctlr[MAX_CPUS];
static bool g_wfx_sctlr_known[MAX_CPUS];
static uint64_t g_wfx_pc[MAX_CPUS];

static void outf(const char *fmt, ...) G_GNUC_PRINTF(1, 2);
static void outf(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    g_autofree char *s = g_strdup_vprintf(fmt, ap);
    va_end(ap);
    qemu_plugin_outs(s);
}

static bool read_reg(struct qemu_plugin_register *h, uint32_t *out)
{
    if (!h) {
        return false;
    }
    g_autoptr(GByteArray) buf = g_byte_array_sized_new(8);
    if (!qemu_plugin_read_register(h, buf) || buf->len < 4) {
        return false;
    }
    memcpy(out, buf->data, 4);
    return true;
}

static void vcpu_init(unsigned int cpu, void *userdata)
{
    static const char *names[16] = {
        "r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
        "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc"
    };
    if (cpu >= MAX_CPUS) {
        return;
    }
    g_autoptr(GArray) regs = qemu_plugin_get_registers();
    RegSet *rs = &g_regs[cpu];
    for (guint i = 0; regs && i < regs->len; i++) {
        qemu_plugin_reg_descriptor *d =
            &g_array_index(regs, qemu_plugin_reg_descriptor, i);
        if (!d->name) {
            continue;
        }
        for (int r = 0; r < 16; r++) {
            if (!strcmp(d->name, names[r])) {
                rs->r[r] = d->handle;
            }
        }
        if (cpu == 0 && strstr(d->name, "SCTLR")) {
            outf("handover-guard: sysreg candidate %s\n", d->name);
        }
        if (!strcmp(d->name, "cpsr")) {
            rs->cpsr = d->handle;
        } else if (!strcmp(d->name, "SCTLR") || !strcmp(d->name, "SCTLR_EL1")) {
            rs->sctlr = d->handle;
        }
    }
    rs->init = true;
    if (cpu == 0) {
        outf("handover-guard: register sources: cpsr=%s SCTLR=%s\n",
             rs->cpsr ? "gdb" : "missing",
             rs->sctlr ? "gdb sysreg" : "missing (tracked MCR writes only)");
    }
}

/* Decode the instruction; returns an Op with kind 0 when not tracked. */
static Op decode(const uint8_t *b, size_t sz)
{
    Op op = { 0 };
    uint32_t w;
    bool t32 = false;

    if (sz == 2) {
        uint16_t h = b[0] | (b[1] << 8);
        if (h == 0xBF30 || h == 0xBF20) {      /* WFI / WFE (T1) */
            op.kind = EV_WFX;
        }
        return op;
    }
    if (sz != 4) {
        return op;
    }
    w = (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) |
        ((uint32_t)b[3] << 24);
    /*
     * A T32 32-bit instruction is two halfwords, hw1 first.  Its top five
     * bits are 0b11101/0b11110/0b11111; an A32 word stored little-endian has
     * the condition in the top byte of w.  Distinguish by re-assembling.
     */
    uint32_t tw = ((uint32_t)(b[0] | (b[1] << 8)) << 16) | (b[2] | (b[3] << 8));
    uint32_t hw1 = tw >> 16;
    if ((hw1 & 0xE000) == 0xE000 && (hw1 & 0x1800) != 0) {
        /* Candidate T32 form; only accept the encodings we track. */
        if ((tw & 0xFFFFFFF0u) == 0xF3BF8F40u) { op.kind = EV_DSB; return op; }
        if ((tw & 0xFFFFFFF0u) == 0xF3BF8F60u) { op.kind = EV_ISB; return op; }
        if (tw == 0xF3AF8003u || tw == 0xF3AF8002u) { op.kind = EV_WFX; return op; }
        if ((tw & 0xFF100F10u) == 0xEE000F10u) { t32 = true; w = tw; }
    }

    uint32_t cond = w >> 28;
    if (!t32) {
        if ((w & 0xFFFFFFF0u) == 0xF57FF040u) { op.kind = EV_DSB; return op; }
        if ((w & 0xFFFFFFF0u) == 0xF57FF060u) { op.kind = EV_ISB; return op; }
        if ((w & 0x0FFFFFFEu) == 0x0320F002u) {
            op.kind = EV_WFX; op.cond = cond != 0xE; return op;
        }
        if (cond == 0xF || (w & 0x0F100F10u) != 0x0E000F10u) {
            return op;
        }
        op.cond = cond != 0xE;
    }
    unsigned opc1 = (w >> 21) & 7, crn = (w >> 16) & 0xF, rt = (w >> 12) & 0xF;
    unsigned opc2 = (w >> 5) & 7, crm = w & 0xF;
    op.rt = rt;
    if (opc1 != 0) {
        return op;
    }
    if (crn == 1 && crm == 0 && opc2 == 0) {
        op.kind = EV_SCTLR;
    } else if (crn == 7) {
        if (crm == 14 && opc2 == 2) {
            op.kind = EV_DCCISW;
        } else if ((crm == 6 || crm == 10 || crm == 11 || crm == 14) &&
                   (opc2 == 1 || opc2 == 2)) {
            op.kind = EV_DC_OTHER;      /* DCIMVAC/DCISW/DCCMVAC/DCCSW/DCCMVAU/DCCIMVAC */
        } else if ((crm == 5 && opc2 == 0) || (crm == 1 && opc2 == 0)) {
            op.kind = EV_ICIALL;
        } else if (crm == 10 && opc2 == 4) {
            op.kind = EV_DSB;           /* CP15 DSB */
        } else if (crm == 5 && opc2 == 4) {
            op.kind = EV_ISB;           /* CP15 ISB */
        }
    } else if (crn == 8) {
        op.kind = EV_TLBI;
    }
    return op;
}

static const char *kind_name(int k)
{
    static const char *n[] = { "?", "SCTLR", "DCCISW", "DC-other", "ICIALLU",
                               "DSB", "ISB", "WFx", "ENTRY", "TLBI" };
    return k < (int)G_N_ELEMENTS(n) ? n[k] : "?";
}

static void log_event(unsigned cpu, uint64_t pc, Op op, uint32_t val, bool hasval)
{
    if (g_events_logged >= g_events_max) {
        return;
    }
    g_events_logged++;
    if (hasval) {
        outf("handover-guard: ev seq=%" PRIu64 " cpu=%u pc=0x%08" PRIx64
             " %s%s val=0x%08x\n", g_seq, cpu, pc, kind_name(op.kind),
             op.cond ? "(cond)" : "", val);
    } else {
        outf("handover-guard: ev seq=%" PRIu64 " cpu=%u pc=0x%08" PRIx64 " %s%s\n",
             g_seq, cpu, pc, kind_name(op.kind), op.cond ? "(cond)" : "");
    }
}

/*
 * DMA quiescence contracts (Custom OS prerequisite 2, E0639).  One per line:
 *   run     NAME ADDR MASK [STATUS_ADDR SMASK IDLE]
 *       active while the last store to ADDR has bits in MASK; after the store
 *       that clears them, a load of STATUS_ADDR with (v & SMASK) == IDLE must
 *       follow when a status register is given.
 *   region  NAME START END STATUS_ADDR SMASK IDLE
 *       any store to [START, END) needs a later idle load of STATUS_ADDR
 *       (conservative form for engines whose start register is not known).
 *   observe NAME START END
 *       no stop contract is known; any store is UNVERIFIED (fails when
 *       strict_unknown=1, the default).
 * Addresses are guest-physical; only IO (MMIO) accesses are considered.
 * Text after '#' is provenance and is echoed in the report.
 */
enum { DMA_RUN = 1, DMA_REGION, DMA_OBSERVE };

typedef struct {
    int kind;
    char name[48];
    char note[160];
    uint64_t start, end;           /* run: [addr, addr + 4) */
    uint32_t mask;
    bool has_status;
    uint64_t status;
    uint32_t smask, idle;
    /* state */
    bool active, ever_active;
    uint64_t stores, last_store_seq, idle_seq;
    uint64_t stop_seq;             /* run: store that cleared an active engine */
    uint32_t last_value, last_status;
    bool status_seen;
} DmaContract;

#define MAX_DMA 128
static DmaContract g_dma[MAX_DMA];
static unsigned g_ndma;
static char g_dma_path[256];
static int g_strict_unknown = 1;

static bool dma_load(const char *path)
{
    g_autofree char *text = NULL;
    g_autoptr(GError) err = NULL;
    if (!g_file_get_contents(path, &text, NULL, &err)) {
        fprintf(stderr, "handover-guard: cannot read %s: %s\n", path, err->message);
        return false;
    }
    g_strlcpy(g_dma_path, path, sizeof(g_dma_path));
    g_auto(GStrv) lines = g_strsplit(text, "\n", -1);
    for (int ln = 0; lines[ln]; ln++) {
        char *line = lines[ln], *hash = strchr(line, '#');
        char note[160] = "";
        if (hash) {
            g_strlcpy(note, g_strstrip(hash + 1), sizeof(note));
            *hash = 0;
        }
        g_auto(GStrv) f = g_strsplit_set(g_strstrip(line), " \t", -1);
        int n = 0;
        char *tok[8];
        for (int i = 0; f[i] && n < 8; i++) {
            if (*f[i]) {
                tok[n++] = f[i];
            }
        }
        if (n == 0) {
            continue;
        }
        if (g_ndma >= MAX_DMA) {
            fprintf(stderr, "handover-guard: too many DMA contracts\n");
            return false;
        }
        DmaContract *c = &g_dma[g_ndma];
        memset(c, 0, sizeof(*c));
        g_strlcpy(c->note, note, sizeof(c->note));
        if (n >= 2) {
            g_strlcpy(c->name, tok[1], sizeof(c->name));
        }
        if (!strcmp(tok[0], "run") && (n == 4 || n == 7)) {
            c->kind = DMA_RUN;
            c->start = strtoull(tok[2], NULL, 0);
            c->end = c->start + 4;
            c->mask = strtoul(tok[3], NULL, 0);
            if (n == 7) {
                c->has_status = true;
                c->status = strtoull(tok[4], NULL, 0);
                c->smask = strtoul(tok[5], NULL, 0);
                c->idle = strtoul(tok[6], NULL, 0);
            }
        } else if (!strcmp(tok[0], "region") && n == 7) {
            c->kind = DMA_REGION;
            c->start = strtoull(tok[2], NULL, 0);
            c->end = strtoull(tok[3], NULL, 0);
            c->has_status = true;
            c->status = strtoull(tok[4], NULL, 0);
            c->smask = strtoul(tok[5], NULL, 0);
            c->idle = strtoul(tok[6], NULL, 0);
        } else if (!strcmp(tok[0], "observe") && n == 4) {
            c->kind = DMA_OBSERVE;
            c->start = strtoull(tok[2], NULL, 0);
            c->end = strtoull(tok[3], NULL, 0);
        } else {
            fprintf(stderr, "handover-guard: %s:%d: bad contract line\n", path, ln + 1);
            return false;
        }
        if (c->end <= c->start) {
            fprintf(stderr, "handover-guard: %s:%d: empty range\n", path, ln + 1);
            return false;
        }
        g_ndma++;
    }
    return g_ndma > 0;
}

/* Optional MMIO trace (trace=START:END, trace_limit=N): PC of each IO access. */
static uint64_t g_trace_start, g_trace_end;
static unsigned g_trace_limit = 256, g_trace_logged;
static bool g_trace_ram;        /* trace_ram=1: also log RAM accesses in the range */

static void on_mem(unsigned int cpu, qemu_plugin_meminfo_t info, uint64_t vaddr,
                   void *udata)
{
    struct qemu_plugin_hwaddr *h = qemu_plugin_get_hwaddr(info, vaddr);
    if (!h) {
        return;
    }
    bool io = qemu_plugin_hwaddr_is_io(h);
    uint64_t pa = qemu_plugin_hwaddr_phys_addr(h);
    if (!io && !(g_trace_ram && pa >= g_trace_start && pa < g_trace_end)) {
        return;     /* RAM is only of interest to trace_ram */
    }
    qemu_plugin_mem_value mv = qemu_plugin_mem_get_value(info);
    uint32_t v;
    switch (mv.type) {
    case QEMU_PLUGIN_MEM_VALUE_U8:  v = mv.data.u8; break;
    case QEMU_PLUGIN_MEM_VALUE_U16: v = mv.data.u16; break;
    case QEMU_PLUGIN_MEM_VALUE_U32: v = mv.data.u32; break;
    default:                        v = (uint32_t)mv.data.u64; break;
    }
    bool store = qemu_plugin_mem_is_store(info);

    g_mutex_lock(&g_lock);
    g_seq++;
    if (g_trace_end && pa >= g_trace_start && pa < g_trace_end &&
        g_trace_logged < g_trace_limit) {
        uint32_t lr = 0;
        bool lr_ok = cpu < MAX_CPUS && read_reg(g_regs[cpu].r[14], &lr);
        g_trace_logged++;
        outf("handover-guard: %s cpu=%u pc=0x%08" PRIx64 " lr=%s0x%08x %s pa=0x%08"
             PRIx64 " val=0x%08x\n", io ? "mmio" : "ram", cpu,
             (uint64_t)(uintptr_t)udata, lr_ok ? "" : "?", lr, store ? "W" : "R", pa, v);
    }
    if (io && store) {
        if (pa == GICC_CTLR_PA) {
            g_last_gicc_ctlr = v;
            g_ever_gicc_en |= (v & 1) != 0;
        } else if (pa == GICD_CTLR_PA) {
            g_last_gicd_ctlr = v;
            g_ever_gicd_en |= (v & 1) != 0;
        } else if (pa == L2X0_CTRL_PA) {
            g_last_l2_ctrl = v;
            if (v & 1) {
                g_ever_l2_en = true;
                g_l2_last_en_seq = g_seq;
            } else {
                g_l2_off_seq = g_seq;
            }
        } else if (pa == L2X0_CLEAN_INV_WAY_PA) {
            if (v != 0) {
                g_l2_clean_inv_seq = g_seq;
                g_last_l2_clean_inv = v;
            }
        } else if (pa == L2X0_CACHE_SYNC_PA) {
            g_l2_sync_seq = g_seq;
        }
    }
    for (unsigned i = 0; io && i < g_ndma; i++) {
        DmaContract *c = &g_dma[i];
        if (store && pa >= c->start && pa < c->end) {
            c->stores++;
            c->last_store_seq = g_seq;
            if (c->kind == DMA_RUN) {
                bool now = (v & c->mask) != 0;
                if (c->active && !now) {
                    c->stop_seq = g_seq;
                }
                c->active = now;
                c->ever_active |= now;
            }
            c->last_value = v;
        }
        if (!store && c->has_status && pa == c->status) {
            c->status_seen = true;
            c->last_status = v;
            if ((v & c->smask) == c->idle) {
                c->idle_seq = g_seq;
            }
        }
    }
    g_mutex_unlock(&g_lock);
}

static bool dma_report(void)
{
    bool ok_all = true;
    for (unsigned i = 0; i < g_ndma; i++) {
        DmaContract *c = &g_dma[i];
        const char *state;
        bool ok;
        if (c->stores == 0) {
            ok = true;
            state = "never programmed";
        } else if (c->kind == DMA_OBSERVE) {
            ok = !g_strict_unknown;
            state = "UNVERIFIED (no stop contract)";
        } else if (c->kind == DMA_RUN && c->active) {
            ok = false;
            state = "STILL RUNNING";
        } else if (c->kind == DMA_RUN && !c->ever_active) {
            ok = true;
            state = "never started (stores never set the run bits)";
        } else if (c->kind == DMA_RUN && c->has_status && c->idle_seq <= c->stop_seq) {
            ok = false;
            state = "stop NOT confirmed by an idle status read";
        } else if (c->kind == DMA_REGION && c->idle_seq <= c->last_store_seq) {
            ok = false;
            state = "stop NOT confirmed by an idle status read";
        } else {
            ok = true;
            state = c->has_status ? "stopped, idle confirmed" : "stopped";
        }
        ok_all &= ok;
        outf("handover-guard: G10 %-4s %s: %s (stores=%" PRIu64 " last=0x%08x"
             " status%s=0x%08x) [%s]\n", ok ? "ok" : "BAD", c->name, state,
             c->stores, c->last_value, c->status_seen ? "" : "(never read)",
             c->last_status, c->note);
    }
    return ok_all;
}

static void check_and_report(unsigned cpu, uint64_t pc)
{
    RegSet *rs = &g_regs[cpu];
    uint32_t sctlr = 0, cpsr = 0;
    bool sctlr_ok = read_reg(rs->sctlr, &sctlr);
    const char *src = "gdb sysreg";
    if (!sctlr_ok) {
        sctlr = g_sctlr_written;
        sctlr_ok = g_sctlr_known;
        src = "tracked MCR";
    }
    bool cpsr_ok = read_reg(rs->cpsr, &cpsr);
    int fails = 0;

    outf("handover-guard: SCTLR sources: sysreg=%s0x%08x tracked-MCR=%s0x%08x\n",
         read_reg(rs->sctlr, &(uint32_t){0}) ? "" : "(missing) ", sctlr,
         g_sctlr_known ? "" : "(none) ", g_sctlr_written);
    if (g_sctlr_known && sctlr != g_sctlr_written) {
        /* Banked-register doubt (Secure/Non-secure): fail if either shows M/C/I. */
        outf("handover-guard: SCTLR sources disagree; using the union of M/C/I\n");
        sctlr |= g_sctlr_written & 0x1005u;
    }

#define G(id, cond, fmt, ...) do { \
        bool _p = (cond); fails += !_p; \
        outf("handover-guard: %s %s " fmt "\n", id, _p ? "PASS" : "FAIL", __VA_ARGS__); \
    } while (0)

    outf("handover-guard: entry 0x%08" PRIx64 " reached on cpu%u at seq %" PRIu64 "\n",
         pc, cpu, g_seq);
    G("G1", sctlr_ok && !(sctlr & 1), "SCTLR.M=%u (SCTLR=0x%08x via %s%s)",
      sctlr & 1, sctlr, src, sctlr_ok ? "" : ", unknown");
    G("G2", sctlr_ok && !(sctlr & 4), "SCTLR.C=%u", (sctlr >> 2) & 1);
    G("G3", sctlr_ok && !(sctlr & 0x1000), "SCTLR.I=%u", (sctlr >> 12) & 1);
    G("G4", g_c_off_seq && g_dccisw_after_coff >= g_min_setway,
      "DCCISW after C-off=%" PRIu64 " (need >= %u; total %" PRIu64
      ", conditional %" PRIu64 ", other DC ops %" PRIu64 ")",
      g_dccisw_after_coff, g_min_setway, g_dccisw_total, g_dc_cond, g_dc_other);
    G("G5", g_last_dccisw_seq && g_last_ic_seq > g_last_dccisw_seq,
      "ICIALLU seq=%" PRIu64 " after last DCCISW seq=%" PRIu64,
      g_last_ic_seq, g_last_dccisw_seq);
    G("G6", g_last_cmo_seq && g_last_dsb_seq > g_last_cmo_seq,
      "DSB seq=%" PRIu64 " after last maintenance seq=%" PRIu64,
      g_last_dsb_seq, g_last_cmo_seq);
    G("G7", g_last_sctlr_seq && g_last_isb_seq > g_last_sctlr_seq,
      "ISB seq=%" PRIu64 " after last SCTLR write seq=%" PRIu64,
      g_last_isb_seq, g_last_sctlr_seq);
    G("G8", cpsr_ok && (cpsr & 0xC0) == 0xC0, "CPSR=0x%08x (I=%u F=%u)%s",
      cpsr, (cpsr >> 7) & 1, (cpsr >> 6) & 1, cpsr_ok ? "" : " unknown");

    int ncpu = qemu_plugin_num_vcpus();
    bool park_ok = true;
    for (int c = 0; c < ncpu && c < MAX_CPUS; c++) {
        if ((unsigned)c == cpu) {
            continue;
        }
        if (!g_ran[c]) {
            outf("handover-guard: G9 cpu%d never executed (treated as held in reset)\n", c);
            continue;
        }
        uint64_t since = qemu_plugin_u64_get(g_icount, c) - g_wfx_icount[c];
        bool ok = g_wfx_seen[c] && since <= g_park_window &&
                  g_wfx_sctlr_known[c] && !(g_wfx_sctlr[c] & 4);
        park_ok &= ok;
        outf("handover-guard: G9 cpu%d %s last WFx pc=0x%08" PRIx64
             " insns-since=%" PRIu64 " SCTLR@WFx=0x%08x%s\n", c,
             ok ? "parked" : "NOT parked", g_wfx_pc[c], since, g_wfx_sctlr[c],
             g_wfx_sctlr_known[c] ? "" : " (unknown)");
    }
    G("G9", park_ok, "secondaries parked (window %u)", g_park_window);
    if (g_ndma) {
        G("G10", dma_report(), "DMA engines quiescent (%u contracts from %s, strict_unknown=%d)",
          g_ndma, g_dma_path, g_strict_unknown);
    } else {
        outf("handover-guard: G10 not configured (no dma= contract file)\n");
    }
    if (g_check_tlb) {
        uint64_t tlbi_base = g_c_off_seq ? g_c_off_seq
                             : (!(sctlr & 4) ? g_m_off_seq : 0);
        bool tlbi_ok = tlbi_base && g_last_tlbi_seq > tlbi_base &&
                       g_last_dsb_seq > g_last_tlbi_seq;
        G("G11", tlbi_ok,
          "TLBI seq=%" PRIu64 " after teardown off seq=%" PRIu64
          " and before DSB seq=%" PRIu64 " (total %" PRIu64 ", conditional %" PRIu64 ")",
          g_last_tlbi_seq, tlbi_base, g_last_dsb_seq, g_tlbi_total, g_tlbi_cond);
    }
    if (g_check_gic) {
        if (!(g_ever_gicc_en || g_ever_gicd_en)) {
            G("G12", true, "%s", "GIC never enabled");
        } else {
            bool gicc_ok = !g_ever_gicc_en || !(g_last_gicc_ctlr & 1);
            bool gicd_ok = !g_ever_gicd_en || !(g_last_gicd_ctlr & 1);
            G("G12", gicc_ok && gicd_ok,
              "GIC quiesced (GICD_CTLR=%s0x%08x GICC_CTLR=%s0x%08x)",
              g_ever_gicd_en ? "" : "never-enabled/", g_last_gicd_ctlr,
              g_ever_gicc_en ? "" : "never-enabled/", g_last_gicc_ctlr);
        }
    }
    if (g_check_l2) {
        if (!g_ever_l2_en) {
            G("G13", true, "%s", "PL310 never enabled");
        } else {
            bool clean_ok = g_l2_clean_inv_seq > g_l2_last_en_seq;
            bool sync_ok = clean_ok && g_l2_sync_seq > g_l2_clean_inv_seq;
            bool off_ok = !(g_last_l2_ctrl & 1) && g_l2_off_seq >= g_l2_last_en_seq;
            G("G13", clean_ok && sync_ok && off_ok,
              "PL310 outer cache cleaned, synced and disabled "
              "(en_seq=%" PRIu64 " clean_inv_seq=%" PRIu64 " way=0x%08x "
              "sync_seq=%" PRIu64 " off_seq=%" PRIu64 " CTRL=0x%08x)",
              g_l2_last_en_seq, g_l2_clean_inv_seq, g_last_l2_clean_inv,
              g_l2_sync_seq, g_l2_off_seq, g_last_l2_ctrl);
        }
    }
    {
        const char *unchecked[6];
        int nu = 0;
        if (!g_check_l2) {
            unchecked[nu++] = "outer L2 cache";
        }
        if (!g_ndma) {
            unchecked[nu++] = "DMA quiescence";
        }
        if (!g_check_gic) {
            unchecked[nu++] = "GIC state";
        }
        if (!g_check_tlb) {
            unchecked[nu++] = "TLB";
        }
        if (g_ndma) {
            unchecked[nu++] = "DMA engines outside the contract file";
        }
        g_autoptr(GString) nc = g_string_new(NULL);
        for (int i = 0; i < nu; i++) {
            if (i) {
                g_string_append(nc, ", ");
            }
            g_string_append(nc, unchecked[i]);
        }
        outf("handover-guard: not checked: %s\n", nc->len ? nc->str : "none");
    }
    outf("handover-guard: RESULT %s (%d failing)\n", fails ? "FAIL" : "PASS", fails);
#undef G
    if (g_exit) {
        fflush(stdout);
        fflush(stderr);
        _exit(fails ? 3 : 0);
    }
}

static void on_exec(unsigned int cpu, void *udata)
{
    uintptr_t u = (uintptr_t)udata;
    Op op = { .kind = u & 0xFF, .cond = (u >> 8) & 1, .rt = (u >> 9) & 0xF };
    uint64_t pc = u >> 16;
    if (cpu >= MAX_CPUS) {
        return;
    }

    g_mutex_lock(&g_lock);
    g_seq++;
    if (op.kind == EV_WFX) {
        uint32_t s;
        g_wfx_seen[cpu] = true;
        g_wfx_icount[cpu] = qemu_plugin_u64_get(g_icount, cpu);
        g_wfx_pc[cpu] = pc;
        g_wfx_sctlr_known[cpu] = read_reg(g_regs[cpu].sctlr, &s);
        if (g_wfx_sctlr_known[cpu]) {
            g_wfx_sctlr[cpu] = s;
        } else if (cpu == g_cpu && g_sctlr_known) {
            g_wfx_sctlr[cpu] = g_sctlr_written;
            g_wfx_sctlr_known[cpu] = true;
        }
        g_mutex_unlock(&g_lock);
        return;
    }
    if (op.kind == EV_ENTRY) {
        uint32_t cpsr = 0;
        if (cpu == g_cpu && !g_checked) {
            if (read_reg(g_regs[cpu].cpsr, &cpsr) && (cpsr & 0x1F) == 0x10) {
                /* a user process executing the same virtual address is not a handover */
                g_entry_user_hits++;
            } else if (g_skip) {
                g_skip--;
                outf("handover-guard: entry 0x%08" PRIx64 " hit on cpu%u skipped "
                     "(CPSR=0x%08x, %u skip(s) left)\n", pc, cpu, cpsr, g_skip);
            } else {
                g_checked = true;
                if (g_entry_user_hits) {
                    outf("handover-guard: %" PRIu64 " user-mode execution(s) of the entry "
                         "address ignored\n", g_entry_user_hits);
                }
                check_and_report(cpu, pc);
            }
        }
        g_mutex_unlock(&g_lock);
        return;
    }
    if (cpu != g_cpu) {
        g_mutex_unlock(&g_lock);
        return;
    }
    uint32_t val = 0;
    bool hasval = false;
    switch (op.kind) {
    case EV_SCTLR:
        hasval = read_reg(g_regs[cpu].r[op.rt], &val);
        if (!op.cond) {
            g_last_sctlr_seq = g_seq;
            if (hasval) {
                g_sctlr_written = val;
                g_sctlr_known = true;
                if (!(val & 4)) {
                    /*
                     * Start counting at the write that turns C off; later
                     * writes that keep C off (e.g. clearing M) do not restart.
                     */
                    if (!g_c_off_seq) {
                        g_c_off_seq = g_seq;
                        g_dccisw_after_coff = 0;
                    }
                } else {
                    g_c_off_seq = 0;
                    g_dccisw_after_coff = 0;
                }
                if (!(val & 1)) {
                    if (!g_m_off_seq) {
                        g_m_off_seq = g_seq;
                    }
                } else {
                    g_m_off_seq = 0;
                }
            }
        }
        log_event(cpu, pc, op, val, hasval);
        break;
    case EV_DCCISW:
        if (op.cond) {
            g_dc_cond++;
            break;
        }
        g_dccisw_total++;
        if (g_c_off_seq) {
            g_dccisw_after_coff++;
        }
        g_last_dccisw_seq = g_last_cmo_seq = g_seq;
        if (g_dccisw_total == 1 || g_dccisw_total == g_min_setway) {
            log_event(cpu, pc, op, (uint32_t)g_dccisw_total, true);
        }
        break;
    case EV_DC_OTHER:
        if (!op.cond) {
            g_dc_other++;
            g_last_cmo_seq = g_seq;
        }
        break;
    case EV_ICIALL:
        if (!op.cond) {
            g_last_ic_seq = g_last_cmo_seq = g_seq;
        }
        log_event(cpu, pc, op, 0, false);
        break;
    case EV_DSB:
        g_last_dsb_seq = g_seq;
        break;
    case EV_ISB:
        g_last_isb_seq = g_seq;
        break;
    case EV_TLBI:
        if (!op.cond) {
            g_last_tlbi_seq = g_seq;
            g_tlbi_total++;
        } else {
            g_tlbi_cond++;
        }
        log_event(cpu, pc, op, 0, false);
        break;
    }
    g_mutex_unlock(&g_lock);
}

static void on_first_exec(unsigned int cpu, void *udata)
{
    if (cpu < MAX_CPUS) {
        g_ran[cpu] = true;
    }
}

/* exec_range=START:END (E0643): log executed blocks whose first PC is in range. */
static uint64_t g_exec_start, g_exec_end;
static unsigned g_exec_limit = 200, g_exec_logged;
static uint64_t g_exec_count, g_exec_last_pc;
static unsigned g_exec_last_cpu;

static void on_range_exec(unsigned int cpu, void *udata)
{
    uint32_t lr = 0;
    bool lr_ok = cpu < MAX_CPUS && read_reg(g_regs[cpu].r[14], &lr);
    g_mutex_lock(&g_lock);
    g_exec_count++;
    g_exec_last_pc = (uint64_t)(uintptr_t)udata;
    g_exec_last_cpu = cpu;
    if (g_exec_logged < g_exec_limit) {
        g_exec_logged++;
        outf("handover-guard: exec cpu=%u pc=0x%08" PRIx64 " lr=%s0x%08x n=%" PRIu64 "\n",
             cpu, (uint64_t)(uintptr_t)udata, lr_ok ? "" : "?", lr, g_exec_count);
    }
    g_mutex_unlock(&g_lock);
}

static void tb_trans(struct qemu_plugin_tb *tb, void *userdata)
{
    size_t n = qemu_plugin_tb_n_insns(tb);
    for (size_t i = 0; i < n; i++) {
        struct qemu_plugin_insn *insn = qemu_plugin_tb_get_insn(tb, i);
        uint8_t b[4] = { 0 };
        size_t sz = qemu_plugin_insn_size(insn);
        uint64_t pc = qemu_plugin_insn_vaddr(insn);
        qemu_plugin_insn_data(insn, b, sz > 4 ? 4 : sz);

        qemu_plugin_register_vcpu_insn_exec_inline_per_vcpu(
            insn, QEMU_PLUGIN_INLINE_ADD_U64, g_icount, 1);
        if (g_ndma || g_trace_end || g_check_gic || g_check_l2) {
            enum qemu_plugin_mem_rw rw = (g_ndma || g_trace_end)
                                         ? QEMU_PLUGIN_MEM_RW : QEMU_PLUGIN_MEM_W;
            qemu_plugin_register_vcpu_mem_cb(insn, on_mem,
                                             g_trace_end ? QEMU_PLUGIN_CB_R_REGS
                                                         : QEMU_PLUGIN_CB_NO_REGS,
                                             rw, (void *)(uintptr_t)pc);
        }
        if (i == 0) {
            qemu_plugin_register_vcpu_insn_exec_cb(insn, on_first_exec,
                                                   QEMU_PLUGIN_CB_NO_REGS, NULL);
            if (g_exec_end && pc >= g_exec_start && pc < g_exec_end) {
                qemu_plugin_register_vcpu_insn_exec_cb(insn, on_range_exec,
                                                       QEMU_PLUGIN_CB_R_REGS,
                                                       (void *)(uintptr_t)pc);
            }
        }
        Op op = decode(b, sz);
        if (pc == g_entry) {
            op.kind = EV_ENTRY;
        }
        if (!op.kind) {
            continue;
        }
        uintptr_t u = op.kind | ((uintptr_t)op.cond << 8) |
                      ((uintptr_t)op.rt << 9) | ((uintptr_t)pc << 16);
        qemu_plugin_register_vcpu_insn_exec_cb(insn, on_exec,
                                               QEMU_PLUGIN_CB_R_REGS,
                                               (void *)u);
    }
}

static void at_exit(void *userdata)
{
    if (g_exec_end) {
        outf("handover-guard: exec_range 0x%08" PRIx64 "-0x%08" PRIx64 " total blocks %"
             PRIu64 ", last pc=0x%08" PRIx64 " cpu%u\n", g_exec_start, g_exec_end,
             g_exec_count, g_exec_last_pc, g_exec_last_cpu);
    }
    if (!g_checked) {
        outf("handover-guard: entry 0x%08" PRIx64 " never reached on cpu%u; "
             "RESULT NOT-REACHED\n", g_entry, g_cpu);
        if (g_ndma) {
            outf("handover-guard: DMA contract state at exit (informational, "
                 "not a handover verdict):\n");
            dma_report();
        }
    }
    qemu_plugin_scoreboard_free(g_sb);
}

QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id,
                                           const qemu_info_t *info,
                                           int argc, char **argv)
{
    if (sizeof(uintptr_t) < 8) {
        fprintf(stderr, "handover-guard: needs a 64-bit host\n");
        return -1;
    }
    for (int i = 0; i < argc; i++) {
        char *v = strchr(argv[i], '=');
        if (!v) {
            fprintf(stderr, "handover-guard: bad argument %s\n", argv[i]);
            return -1;
        }
        *v++ = 0;
        if (!strcmp(argv[i], "entry")) {
            g_entry = strtoull(v, NULL, 0);
            g_have_entry = true;
        } else if (!strcmp(argv[i], "cpu")) {
            g_cpu = strtoul(v, NULL, 0);
        } else if (!strcmp(argv[i], "skip")) {
            g_skip = strtoul(v, NULL, 0);
        } else if (!strcmp(argv[i], "min_setway")) {
            g_min_setway = strtoul(v, NULL, 0);
        } else if (!strcmp(argv[i], "park_window")) {
            g_park_window = strtoul(v, NULL, 0);
        } else if (!strcmp(argv[i], "exit")) {
            g_exit = strtoul(v, NULL, 0) != 0;
        } else if (!strcmp(argv[i], "events")) {
            g_events_max = strtoul(v, NULL, 0);
        } else if (!strcmp(argv[i], "dma")) {
            if (!dma_load(v)) {
                return -1;
            }
        } else if (!strcmp(argv[i], "trace")) {
            char *colon = strchr(v, ':');
            if (!colon) {
                fprintf(stderr, "handover-guard: trace=START:END\n");
                return -1;
            }
            g_trace_start = strtoull(v, NULL, 0);
            g_trace_end = strtoull(colon + 1, NULL, 0);
        } else if (!strcmp(argv[i], "trace_limit")) {
            g_trace_limit = strtoul(v, NULL, 0);
        } else if (!strcmp(argv[i], "trace_ram")) {
            g_trace_ram = strtoul(v, NULL, 0) != 0;
        } else if (!strcmp(argv[i], "exec_range")) {
            char *colon = strchr(v, ':');
            if (!colon) {
                fprintf(stderr, "handover-guard: exec_range=START:END\n");
                return -1;
            }
            g_exec_start = strtoull(v, NULL, 0);
            g_exec_end = strtoull(colon + 1, NULL, 0);
        } else if (!strcmp(argv[i], "exec_limit")) {
            g_exec_limit = strtoul(v, NULL, 0);
        } else if (!strcmp(argv[i], "strict_unknown")) {
            g_strict_unknown = strtoul(v, NULL, 0) != 0;
        } else if (!strcmp(argv[i], "tlb")) {
            g_check_tlb = strtoul(v, NULL, 0) != 0;
        } else if (!strcmp(argv[i], "gic")) {
            g_check_gic = strtoul(v, NULL, 0) != 0;
        } else if (!strcmp(argv[i], "l2")) {
            g_check_l2 = strtoul(v, NULL, 0) != 0;
        } else if (!strcmp(argv[i], "strict_kernel")) {
            bool en = strtoul(v, NULL, 0) != 0;
            g_check_tlb = g_check_gic = g_check_l2 = en;
        } else {
            fprintf(stderr, "handover-guard: unknown argument %s\n", argv[i]);
            return -1;
        }
    }
    if (!g_have_entry || g_cpu >= MAX_CPUS) {
        fprintf(stderr, "handover-guard: entry=0xADDR is required (cpu < %d)\n",
                MAX_CPUS);
        return -1;
    }
    g_sb = qemu_plugin_scoreboard_new(sizeof(uint64_t));
    g_icount = qemu_plugin_scoreboard_u64(g_sb);
    qemu_plugin_register_vcpu_init_cb(id, vcpu_init, NULL);
    qemu_plugin_register_vcpu_tb_trans_cb(id, tb_trans, NULL);
    qemu_plugin_register_atexit_cb(id, at_exit, NULL);
    return 0;
}
