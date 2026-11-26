/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Passive ILCE-6400 DWC3 MMIO observer for QEMU 10.1 TCG.
 * Kernel PC range: fusb_otg_get_base_addr through the DWC3 gadget code
 * in the main kernel identified in E0579.
 */
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <qemu-plugin.h>

QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;

#define DWC3_BASE UINT64_C(0xf0200000)
#define DWC3_END UINT64_C(0xf0300000)
#define FUSB_BASE UINT64_C(0xf2920000)
#define FUSB_END UINT64_C(0xf2921100)
#define DRIVER_START UINT64_C(0x602accc0)
#define DRIVER_END UINT64_C(0x602b3000)
#define MAX_CPUS 4
#define SAMPLE_LIMIT 256

static unsigned samples[MAX_CPUS];

static void access(unsigned cpu, qemu_plugin_meminfo_t info, uint64_t va,
                   void *opaque)
{
    struct qemu_plugin_hwaddr *address;
    qemu_plugin_mem_value value;
    uint64_t pa, data;

    if (cpu >= MAX_CPUS || samples[cpu] >= SAMPLE_LIMIT) {
        return;
    }
    address = qemu_plugin_get_hwaddr(info, va);
    /* QEMU 10.1 can report this stub-backed MMIO range without the IO flag. */
    if (!address) {
        return;
    }
    pa = qemu_plugin_hwaddr_phys_addr(address);
    if ((pa < DWC3_BASE || pa >= DWC3_END) &&
        (pa < FUSB_BASE || pa >= FUSB_END)) {
        return;
    }

    value = qemu_plugin_mem_get_value(info);
    switch (value.type) {
    case QEMU_PLUGIN_MEM_VALUE_U8:
        data = value.data.u8;
        break;
    case QEMU_PLUGIN_MEM_VALUE_U16:
        data = value.data.u16;
        break;
    case QEMU_PLUGIN_MEM_VALUE_U32:
        data = value.data.u32;
        break;
    case QEMU_PLUGIN_MEM_VALUE_U64:
        data = value.data.u64;
        break;
    default:
        return;
    }

    fprintf(stderr, "[DWC3 trace] cpu=%u sample=%u pc=%08" PRIxPTR
            " %c%u pa=%08" PRIx64 " va=%08" PRIx64 " value=%08" PRIx64 "\n",
            cpu, ++samples[cpu], (uintptr_t)opaque,
            qemu_plugin_mem_is_store(info) ? 'W' : 'R',
            1u << qemu_plugin_mem_size_shift(info), pa, va, data);
}

/* QEMU 11 (plugin API 7) passes user data instead of the plugin id to these callbacks. */
#if QEMU_PLUGIN_VERSION >= 7
static void translate(struct qemu_plugin_tb *tb, void *userdata)
#else
static void translate(qemu_plugin_id_t id, struct qemu_plugin_tb *tb)
#endif
{
    for (size_t i = 0; i < qemu_plugin_tb_n_insns(tb); i++) {
        struct qemu_plugin_insn *insn = qemu_plugin_tb_get_insn(tb, i);
        uint64_t pc = qemu_plugin_insn_vaddr(insn);

        if (pc >= DRIVER_START && pc < DRIVER_END) {
            qemu_plugin_register_vcpu_mem_cb(insn, access, QEMU_PLUGIN_CB_NO_REGS,
                                             QEMU_PLUGIN_MEM_RW,
                                             (void *)(uintptr_t)pc);
        }
    }
}

#if QEMU_PLUGIN_VERSION >= 7
static void summarize(void *opaque)
#else
static void summarize(qemu_plugin_id_t id, void *opaque)
#endif
{
    for (unsigned cpu = 0; cpu < MAX_CPUS; cpu++) {
        fprintf(stderr, "[DWC3 trace] cpu=%u samples=%u%s\n", cpu, samples[cpu],
                samples[cpu] == SAMPLE_LIMIT ? " limit reached" : "");
    }
}

QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id,
                                          const qemu_info_t *info,
                                          int argc, char **argv)
{
    if (!info->system_emulation || argc != 0) {
        return -1;
    }
#if QEMU_PLUGIN_VERSION >= 7
    qemu_plugin_register_vcpu_tb_trans_cb(id, translate, NULL);
#else
    qemu_plugin_register_vcpu_tb_trans_cb(id, translate);
#endif
    qemu_plugin_register_atexit_cb(id, summarize, NULL);
    return 0;
}
