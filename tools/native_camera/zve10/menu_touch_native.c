/* ZV-E10 (FW 2.03) fixed bindings — first-pass port, UNTESTED.
 * Original a6400 note: FW2.00 fixed bindings; all navigation/rendering calls remain original.
 * Port status and address model: see zve10/README.md (blueprint
 * docs/porting/zv-e10/zv-e10-touch-mod-blueprint.md; ZV-E10 addresses are file offsets /
 * ELF VADRs, runtime = load base + offset, base unknown until boot).
 * UI thread tid 380 below is the a6400's UI tid — the ZV-E10's UI tid is UNKNOWN
 * (blueprint: "verify live"); override with -DMT_UI_TID=<tid> once verified. */
#include "menu_touch_native.h"
#if UINTPTR_MAX != UINT32_MAX
#error Native camera bindings require a 32-bit target
#endif
#ifndef MT_UI_TID
#define MT_UI_TID 380 /* ZV-E10: TO VERIFY — a6400 UI thread tid; ZV-E10 UI tid unknown */
#endif
struct point { float x,y; };
static uint32_t word(uint32_t p,unsigned offset) {return *(const uint32_t *)(p+offset);}
static unsigned byte(uint32_t p,unsigned offset) {return *(const uint8_t *)(p+offset);}
static unsigned ui_tid(void) {
    register uint32_t r0 __asm__("r0")=0;
    register uint32_t r7 __asm__("r7")=224;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7) : "memory","cc");return r0;
}
int mt_native_context(volatile uint32_t *epoch,struct mt_context *out) {
    if(!out)return 0;
    out->active=0;
    if(!epoch || !*epoch || ui_tid()!=MT_UI_TID)return 0;
    uint32_t generation=*epoch,error=~0u;
    /* ZV-E10: TO VERIFY — a6400 module/object lookup (hash-key registry) @ 0x62e27df;
     * not mapped in the blueprint. Hash-key arguments (framework class hashes) expected
     * unchanged on the ZV-E10 but UNVERIFIED. */
    uint32_t (*lookup)(uint32_t,uint32_t,uint32_t,uint32_t *)=(void *)0x62e27dfu; /* ZV-E10: TO VERIFY */
    uint32_t menu=lookup(0xaadca2c7,0xdfdc8ddd,0xa056f604,&error);
    /* ZV-E10: PAS_MenuListTab vtable address point 0xc01e28 (_ZTV @ 0xc01e20). Was 0x15f1f1d0. */
    if(error || !menu || word(menu,0)!=0xc01e28 || word(menu,0x1c)!=menu)return 0;
    /* ZV-E10: TO VERIFY — main_state / inherited_mode / popup_open have no blueprint
     * equivalents (TBD). a6400 constants kept. */
    int (*main_state)(uint32_t)=(void *)0x15b90535; /* ZV-E10: TO VERIFY */
    int (*inherited_mode)(uint32_t)=(void *)0x631e815; /* ZV-E10: TO VERIFY */
    int (*popup_open)(uint32_t)=(void *)0x15b95b61; /* ZV-E10: TO VERIFY */
    if(!main_state(menu) || byte(menu,0x21)!=2 ||
       popup_open(menu+0xfb20) || byte(menu,0x1d8))return 0;
    /* menu+0x186ac root-tree pointer — struct layout CONFIRMED byte-identical on ZV-E10
     * (blueprint §0). */
    uint32_t page=word(menu,0x186ac);
    /* ZV-E10: TO VERIFY — selected_index / indexed_child. Candidate for selected_index:
     * getHighlightItemPos @ 0x860b36 (signature not verified). a6400 constants kept. */
    int (*selected_index)(uint32_t,unsigned *)=(void *)0x16925c17; /* ZV-E10: TO VERIFY (candidate 0x860b36 getHighlightItemPos) */
    int (*indexed_child)(uint32_t,unsigned,uint32_t *)=(void *)0x16925bc7; /* ZV-E10: TO VERIFY */
    for(unsigned level=0;level<2;level++) {
        /* ZV-E10: CmnViewSettingNode vtable VA 0x00fab968 (CautionConfig.so, file 0xfa3968). Was 0x16c183e8. */
        if(!page || word(page,0)!=0x00fab968)return 0; /* ZV-E10: TO VERIFY (rebase) */
        unsigned index=0,count=word(page,8);uint32_t child=0;
        if(count<1 || count>128 || selected_index(page,&index)!=1 || index<1 || index>count ||
           indexed_child(page,index,&child)!=1 || !child)return 0;
        page=child;
    }
    if(word(page,0)!=0x00fab968)return 0; /* ZV-E10: CmnViewSettingNode vtable VA — TO VERIFY (rebase) */
    unsigned rows=word(page,8),selected=0;
    if(rows<1 || rows>6 || selected_index(page,&selected)!=1 ||
       selected<1 || selected>rows || word(menu,0x1f8)!=selected-1 ||
       inherited_mode(menu)>2 || *epoch!=generation)return 0;
    *out=(struct mt_context){1,menu,page,generation,MT_UI_TID,MT_UI_TID,rows,selected,0};return 1;
}
static int validate(struct mt_native_binding *b) {
    struct mt_context current;
    if(b->fault || !mt_native_context(b->epoch,&current) ||
       current.menu!=b->context.menu || current.page!=b->context.page ||
       current.generation!=b->context.generation || current.current_row!=b->context.current_row ||
       current.rows!=b->context.rows){b->fault=1;return 0;}
    return 1;
}
static int position(void *user,unsigned mouse,int16_t *x,int16_t *y) {
    struct mt_native_binding *b=user;
    if(mouse || !x || !y || !validate(b))return 0;
    /* ZV-E10: TO VERIFY — a6400 mouse-position getter @ 0x6309019; not mapped in blueprint. */
    void (*get)(uint32_t,unsigned,int16_t *,int16_t *)=(void *)0x6309019; /* ZV-E10: TO VERIFY */
    /* ZV-E10: WidgetSystem singleton — .bss VA 0x0145cb1c (GOT slot 0x013df230 via
     * sys_getInstance @ 0x00616fe4). Was a6400 0x956f0. */
    get(0x0145cb1c,mouse,x,y);return *x!=32767 && *y!=32767;
}
static int hit(void *user,unsigned row,int16_t x,int16_t y) {
    struct mt_native_binding *b=user;
    if(!row || row>b->context.rows || !validate(b))return -1;
    struct point p={(float)x,(float)y};
    /* ZV-E10: TO VERIFY — Widget::isHit TBD in blueprint; a6400 constant kept. Row stride
     * 0x1118 UNVERIFIED (blueprint §2.6). */
    int (*original)(uint32_t,const struct point *)=(void *)0x6305081; /* ZV-E10: TO VERIFY */
    return original(b->context.menu+0x9350+(row-1)*0x1118,&p);
}
static int select_row(void *user,unsigned row) {
    struct mt_native_binding *b=user;
    if(!row || row>b->context.rows || !validate(b))return 0;
    /* ZV-E10: TO VERIFY — set_selected_index. Candidates: CmnViewSettingNode idx47
     * setItemSelectedByIndex (address not given) / idx48 setItemSelectedBySRNum @ 0xb8e60c
     * (file offset; SRNum semantics differ from a raw index). */
    int (*original)(uint32_t,unsigned)=(void *)0x169260db; /* ZV-E10: TO VERIFY */
    if(original(b->context.page,row)!=1){b->fault=1;return 0;}
    /* ZV-E10: TO VERIFY — binding @ 0x15b91431 TBD in blueprint; a6400 constant kept. */
    void (*binding)(uint32_t)=(void *)0x15b91431; /* ZV-E10: TO VERIFY */
    binding(b->context.menu);
    b->context.current_row=row;
    return validate(b);
}
static void refresh(void *user) {
    struct mt_native_binding *b=user;
    if(!validate(b))return;
    /* Original ViewSettingMenu call157edd86 uses r1=0 for A056F604.
       The callee forwards this argument into its per-row refresh; do not
       leave r1 unspecified merely because this callback returns void.
       ZV-E10: TO VERIFY — refresh @ 0x15b92111 has no confirmed equivalent; blueprint
       candidates: PAS vtable idx108 @ 0x8629f8 (dirty-flag clear + refresh) and 0x2f5998
       (post-move refresh). Identity and (menu, r1=0) signature NOT verified. */
    void (*original)(uint32_t,uint32_t)=(void *)0x15b92111; /* ZV-E10: TO VERIFY (candidates: 0x8629f8 idx108, 0x2f5998) */
    original(b->context.menu,0);
}
int mt_native_bind(struct mt_native_binding *b,volatile uint32_t *epoch,struct mt_ops *ops) {
    if(!b || !ops)return 0;
    b->epoch=epoch;b->fault=0;
    ops->user=0;ops->position=0;ops->hit=0;ops->select=0;ops->refresh=0;
    if(!mt_native_context(epoch,&b->context))return 0;
    *ops=(struct mt_ops){b,position,hit,select_row,refresh};return 1;
}
