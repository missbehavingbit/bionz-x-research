/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Passive original-code USB startup observer for QEMU plugin API 7. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <qemu-plugin.h>
#include "usb_trace_sites.h"
QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;
static const char *names[]={"r0","r1","r2","r3","r4","r5","r6","r7","lr"};
static struct qemu_plugin_register *regs[4][9];
static unsigned counts[4][G_N_ELEMENTS(sites)];
static void prepare(unsigned cpu,void *opaque) {
    if(cpu>=4)return;
    GArray *a=qemu_plugin_get_registers();
    for(guint i=0;i<a->len;i++) {
        qemu_plugin_reg_descriptor r=g_array_index(a,qemu_plugin_reg_descriptor,i);
        for(unsigned j=0;j<9;j++)if(!strcmp(r.name,names[j]))regs[cpu][j]=r.handle;
    }
    g_array_free(a,true);
}
static void observe(unsigned cpu,void *opaque) {
    uintptr_t packed=(uintptr_t)opaque;
    size_t site=packed>>32;
    uint32_t pc=(uint32_t)packed;
    if(cpu>=4 || counts[cpu][site]>=32)return;
    GByteArray *data=g_byte_array_new();
    if(!qemu_plugin_read_memory_vaddr(pc,data,16) || data->len!=16 ||
       memcmp(data->data,sites[site].bytes,16)) {g_byte_array_unref(data);return;}
#ifdef USB_STOCK_TRACE
    if (sites[site].offset == 0x8622c6 || sites[site].offset == 0x8624b0) {
        uint32_t frame;
        g_byte_array_set_size(data, 0);
        if (!regs[cpu][7] || !qemu_plugin_read_register(regs[cpu][7], data) ||
            data->len != 4) {
            g_byte_array_unref(data);
            return;
        }
        memcpy(&frame, data->data, sizeof(frame));
        frame = GUINT32_FROM_LE(frame);
        /* Original message key at I-InputMain's fixed Thumb frame slot. */
        g_byte_array_set_size(data, 0);
        if (frame < 0x10000 || frame >= 0x5fffff00 ||
            !qemu_plugin_read_memory_vaddr(frame + 0xdc, data, 4) ||
            data->len != 4 || memcmp(data->data, "\x01\x00\x00\x07", 4)) {
            g_byte_array_unref(data);
            return;
        }
    }
#endif
    counts[cpu][site]++;
    GString *row=g_string_new(NULL);
    g_string_append_printf(row,"[USB startup] cpu=%u kind=%s pc=%08x offset=%08x",cpu,
                           sites[site].kernel?"kernel":"infra",pc,sites[site].offset);
    for(unsigned j=0;j<9;j++) {
        g_byte_array_set_size(data,0);
        if(regs[cpu][j] && qemu_plugin_read_register(regs[cpu][j],data) && data->len==4) {
            uint32_t value;memcpy(&value,data->data,4);
            g_string_append_printf(row," %s=%08x",names[j],GUINT32_FROM_LE(value));
        }
    }
    fprintf(stderr,"%s\n",row->str);
    g_string_free(row,true);g_byte_array_unref(data);
}
static void translate(struct qemu_plugin_tb *tb,void *opaque) {
    for(size_t i=0;i<qemu_plugin_tb_n_insns(tb);i++) {
        struct qemu_plugin_insn *ins=qemu_plugin_tb_get_insn(tb,i);
        uint64_t pc=qemu_plugin_insn_vaddr(ins);
        for(size_t j=0;j<G_N_ELEMENTS(sites);j++) {
            if(sites[j].kernel ? pc!=sites[j].offset :
               (pc>=0x60000000 || (pc&0xfff)!=(sites[j].offset&0xfff)))continue;
            uint8_t bytes[16];
            /* Instruction bytes are checked separately at every selected entry. */
            size_t n=qemu_plugin_insn_data(ins,bytes,sizeof(bytes));
            if(n<2 || memcmp(bytes,sites[j].bytes,n))continue;
            qemu_plugin_register_vcpu_insn_exec_cb(ins,observe,QEMU_PLUGIN_CB_R_REGS,
                (void *)(uintptr_t)((j<<32)|(uint32_t)pc));
        }
    }
}
QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id,const qemu_info_t *info,int argc,char **argv) {
    if(!info->system_emulation || argc)return -1;
    qemu_plugin_register_vcpu_init_cb(id,prepare,NULL);
    qemu_plugin_register_vcpu_tb_trans_cb(id,translate,NULL);
    return 0;
}
