.syntax unified
.thumb

/* Mod Loader v2: the v1 view-hook trampolines (verbatim), default enter_selected variant.
 * Generated from tools/native_camera/mod_loader_trampoline.S. Return addresses are written as
 * viewUnified2.so offsets so package.py can record them for relocation. */
#ifndef MLV2_VIEW_BASE
#define MLV2_VIEW_BASE 0x155e6000
#endif
#ifdef MLV2_TARGET_A6100
#define HOOK_SET_ROOT_OFF  0x5acc0c
#define HOOK_REFRESH_OFF   0x5ad038
#define HOOK_OPEN_ITEM_OFF 0x5ad5fc
#else
#define HOOK_SET_ROOT_OFF  0x5ab654
#define HOOK_REFRESH_OFF   0x5aba80
#define HOOK_OPEN_ITEM_OFF 0x5ac044
#endif

.global ml_hook_set_root
.global ml_hook_refresh
.global ml_hook_open_item
.extern ml_before_set_root
.extern ml_after_refresh
.extern ml_before_enter

.thumb_func
ml_hook_set_root:
    cbz r1, 2f
    push {r0, r1, lr}
    bl ml_before_set_root
    mov r3, r0
    pop {r0, r1, r2}
    mov lr, r2
    cmp r3, #0
    it ne
    movne r1, r3
    /* Original 16 bytes at viewUnified2.so + 0x5ab654 (after push {r3, r4, r7, lr}):
     * mov r4, r0
     * add r7, sp, #0
     * cbz r1, 5ab670
     * add.w r3, r0, #99328 (0x18400)
     * add.w r3, r3, #684   (0x2ac)
     * str r1, [r3, #0]
     */
    mov r4, r0
    add r7, sp, #0
    add.w r3, r0, #99328
    add.w r3, r3, #684
    str r1, [r3, #0]
    ldr r2, 1f
    bx r2
2:
    mov r4, r0
    add r7, sp, #0
    ldr r2, 3f
    bx r2
.align 2
1:  .word MLV2_VIEW_BASE + HOOK_SET_ROOT_OFF + 0x11
3:  .word MLV2_VIEW_BASE + HOOK_SET_ROOT_OFF + 0x1d

.thumb_func
ml_orig_refresh:
    /* Original 14 bytes at viewUnified2.so + 0x5aba80:
     * stmdb sp!, {r4, r5, r6, r7, r8, r9, sl, fp, lr}
     * sub sp, #108
     * add r7, sp, #24
     * mov r4, r0
     * add.w r0, r7, #32
     */
    stmdb sp!, {r4, r5, r6, r7, r8, r9, sl, fp, lr}
    sub sp, #108
    add r7, sp, #24
    mov r4, r0
    add.w r0, r7, #32
    ldr r2, 2f
    bx r2
.align 2
2:  .word MLV2_VIEW_BASE + HOOK_REFRESH_OFF + 0x0f

.thumb_func
ml_hook_refresh:
    push {r0, lr}
    bl ml_orig_refresh
    ldr r0, [sp, #0]
    bl ml_after_refresh
    add sp, #4
    pop {pc}


.thumb_func
ml_hook_open_item:
    /* Entry hook for viewUnified2.so + 0x5ac044 (menu enter_selected(menu, leaf)). */
    push {r0, r1, r2, lr}
    bl ml_before_enter
    cmp r0, #0
    blt 3f
    add sp, #12
    pop {pc}
3:
    pop {r0, r1, r2, r3}
    mov lr, r3
    /* Original 8 bytes at viewUnified2.so + 0x5ac044:
     * add.w r3, r0, #0x18400
     * add.w r3, r3, #0x2b0
     */
    add.w r3, r0, #99328
    add.w r3, r3, #688
    ldr.w ip, 4f
    bx ip
.align 2
4:  .word MLV2_VIEW_BASE + HOOK_OPEN_ITEM_OFF + 0x09
