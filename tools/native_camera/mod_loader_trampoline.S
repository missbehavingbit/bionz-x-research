.syntax unified
.thumb

#ifdef MTL_BUILTIN
.global ml_builtin_tab_look
.set ml_builtin_tab_look, 1
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
1:  .word 0x15b91665
3:  .word 0x15b91671

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
2:  .word 0x15b91a8f

.thumb_func
ml_hook_refresh:
    push {r0, lr}
    bl ml_orig_refresh
    ldr r0, [sp, #0]
    bl ml_after_refresh
    add sp, #4
    pop {pc}

#ifdef ML_HOOK_PMAIN
.extern ml_before_pmain
.thumb_func
ml_hook_open_item:
    /* Entry hook for viewUnified2.so + 0x2f2df4 (ViewBaseProduct::ProductMain(this, int*, int*)).
     * Log-only: call ml_before_pmain(this), then run the original. */
    push {r0, r1, r2, lr}
    bl ml_before_pmain
    pop {r0, r1, r2, r3}
    mov lr, r3
    /* Original 12 bytes at viewUnified2.so + 0x2f2df4:
     * stmdb sp!, {r4-r11, lr}; sub sp, #140; add r7, sp, #16; mov r5, r0; mov r8, r1
     */
    stmdb sp!, {r4, r5, r6, r7, r8, r9, sl, fp, lr}
    sub sp, #140
    add r7, sp, #16
    mov r5, r0
    mov r8, r1
    ldr.w ip, 4f
    bx ip
.align 2
4:  .word 0x158d8e01 /* ProductMain + 12 */
#elif defined(ML_HOOK_DISPATCH)
.extern ml_before_dispatch
.extern ml_after_dispatch
.thumb_func
ml_hook_open_item:
    /* Entry hook for viewUnified2.so + 0x21355e (ViewSettingMenu action TBH
     * dispatcher, r0 = view, r1 = action index). Uses ip, not r2, for the
     * return branches because case handlers may consume r2. */
    push {r0, r1, r2, lr}
    bl ml_before_dispatch
    cmp r0, #0
    blt 3f
    add sp, #12
    pop {pc}
3:
    /* Call the original dispatcher as a subroutine, then ml_after_dispatch(view, act). */
    ldmia sp, {r0, r1, r2}
    bl ml_orig_dispatch
    str r0, [sp, #8]          /* keep original result in the saved-r2 slot */
    ldr r0, [sp, #0]
    ldr r1, [sp, #4]
    bl ml_after_dispatch
    ldr r0, [sp, #8]
    add sp, #12
    pop {pc}

.thumb_func
ml_orig_dispatch:
    /* Original 12 bytes at viewUnified2.so + 0x21355e:
     * push {r4, r5, r7, lr}; mov r4, r0; add r7, sp, #0; cmp r1, #142; bhi.w 0x213b88
     */
    push {r4, r5, r7, lr}
    mov r4, r0
    add r7, sp, #0
    cmp r1, #142
    bhi 5f
    ldr.w ip, 4f
    bx ip
5:
    ldr.w ip, 6f
    bx ip
.align 2
4:  .word 0x157f956b /* tbh at +0x21356a */
6:  .word 0x157f9b89 /* default case +0x213b88 */
#else
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
4:  .word 0x15b9204d
#endif
