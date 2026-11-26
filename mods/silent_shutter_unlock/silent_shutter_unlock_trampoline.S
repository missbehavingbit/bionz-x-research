.syntax unified
.thumb
.section .text

.balign 4
.global silent_orig_check
.hidden silent_orig_check
.thumb_func
silent_orig_check:
    push.w {r4, r5, r6, r7, r8, lr}
    mov r4, r0
    movw r0, #0xc0b6
    add r7, sp, #0
    ldr.w pc, [pc, #0]
    .word 0x069888ad

.balign 4
.global silent_get_inh_ref_tbl
.hidden silent_get_inh_ref_tbl
.thumb_func
silent_get_inh_ref_tbl:
    ldr r0, .Linh_ref_tbl
    bx lr
.align 2
.Linh_ref_tbl:
    .word 0x056918b8

.section .note.GNU-stack,"",%progbits
