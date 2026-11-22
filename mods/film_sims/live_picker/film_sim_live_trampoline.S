.syntax unified
.thumb
.section .text

.balign 4
.global live_original_event_push
.hidden live_original_event_push
.thumb_func
live_original_event_push:
    push.w {r4, r5, r6, r7, r8, lr}
    sub sp, #16
    mov r4, r0
    add r7, sp, #8
    mov r0, r1
    ldr.w pc, [pc, #0]
#ifdef TARGET_A6400_V1
    .word 0x0655a5a5
#else
    /* Both ILCE-6400 Ver.2.00 (live/captured) and ILCE-6100 Ver.1.00 resume at libObj + 0x842535. */
    .word 0x0655a535
#endif

.section .note.GNU-stack,"",%progbits
