/*
 * Thumb-2 trampoline replaying the 12-byte prologue of MprIf::WriteStill
 * before jumping to the original function body + 12.
 *
 * Hooked instruction bytes: b0 b5 82 b0 00 af 04 46 08 46 3a 60
 *   +00: b0 b5   push {r4, r5, r7, lr}
 *   +02: 82 b0   sub sp, #8
 *   +04: 00 af   add r7, sp, #0
 *   +06: 04 46   mov r4, r0
 *   +08: 08 46   mov r0, r1
 *   +10: 3a 60   str r2, [r7, #0]
 */
    .syntax unified
    .thumb
    .section .text
    .balign 4
    .globl fp_orig_write_still
    .hidden fp_orig_write_still
    .thumb_func
fp_orig_write_still:
    .short 0xb5b0, 0xb082, 0xaf00, 0x4604, 0x4608, 0x603a
    ldr.w pc, [pc, #0]
#ifdef TARGET_ILCE6400A
    /* libmpr.so ref base 0x075CE000 + offset 0x00871BE6 + 12 + 1 */
    .word 0x07E3FBF3
#else
    /* libmpr.so ref base 0x07144000 + offset 0x0088CA1A + 12 + 1 */
    .word 0x079D0A27
#endif

/*
 * Thumb-2 trampoline replaying the 12-byte prologue of
 * MprIf::EncodeStill(MprIf::EncodeStillParam*)
 * before jumping to the original function body + 12.
 *
 * Hooked instruction bytes: b0 b5 0c 46 05 46 00 af 03 c9 a2 69
 *   +00: b0 b5   push {r4, r5, r7, lr}
 *   +02: 0c 46   mov r4, r1
 *   +04: 05 46   mov r5, r0
 *   +06: 00 af   add r7, sp, #0
 *   +08: 03 c9   ldmia r1!, {r0, r1}
 *   +10: a2 69   ldr r2, [r4, #24]
 */
    .balign 4
    .globl fp_orig_encode_still
    .hidden fp_orig_encode_still
    .thumb_func
fp_orig_encode_still:
    .short 0xb5b0, 0x460c, 0x4605, 0xaf00, 0xc903, 0x69a2
    ldr.w pc, [pc, #0]
#ifdef TARGET_ILCE6400A
    /* libmpr.so ref base 0x075CE000 + offset 0x00871B68 + 12 + 1 */
    .word 0x07E3FB75
#else
    /* libmpr.so ref base 0x07144000 + offset 0x0088C99C + 12 + 1 */
    .word 0x079D09A9
#endif

/*
 * int fp_spawn_thread(void *stack_top, void (*fn)(void *), void *arg, int32_t *ctid)
 * Spawns a kernel thread sharing VM/FS/files/sighand with CLONE_CHILD_CLEARTID.
 */
    .balign 4
    .globl fp_spawn_thread
    .hidden fp_spawn_thread
    .thumb_func
fp_spawn_thread:
    cmp r0, #0
    beq .Lspawn_inval
    cmp r1, #0
    beq .Lspawn_inval
    bic r0, r0, #7
    subs r0, r0, #8
    str r1, [r0, #0]
    str r2, [r0, #4]
    push {r4, r7, lr}
    mov r1, r0                  @ newsp
    mov r4, r3                  @ child_tidptr
    movw r0, #0x0f00            @ CLONE_VM|FS|FILES|SIGHAND
    movt r0, #0x0021            @ |CLONE_THREAD|CLONE_CHILD_CLEARTID (0x00210F00)
    movs r2, #0                 @ parent_tidptr
    movs r3, #0                 @ tls
    movs r7, #120               @ SYS_clone
    svc #0
    cmp r0, #0
    beq .Lspawn_child
    pop {r4, r7, pc}
.Lspawn_child:
    ldr r3, [sp, #0]            @ fn
    ldr r0, [sp, #4]            @ arg
    add sp, #8
    blx r3
    movs r0, #0
    movs r7, #1                 @ SYS_exit (thread only)
    svc #0
.Lspawn_hang:
    b .Lspawn_hang
.Lspawn_inval:
    mvn r0, #21                 @ -EINVAL
    bx lr
