.syntax unified
.thumb
.section .text

.balign 4
.global mt_original_release
.hidden mt_original_release
.thumb_func
mt_original_release:
    push.w {r4,r5,r6,r7,r8,r9,r10,lr}
    sub sp, #0x20
    mov.w r8, #0x14
    movs r2, #1
    ldr.w pc, [pc, #0]
#ifdef TARGET_ILCE6400A
    .word 0x0659aea5
#else
    .word 0x06309d55
#endif

.balign 4
.global mt_original_press
.hidden mt_original_press
.thumb_func
mt_original_press:
    push.w {r4,r5,r6,r7,r8,r9,r10,lr}
    sub sp, #0x20
    mov.w r9, #0x14
    movs r2, #1
    ldr.w pc, [pc, #0]
#ifdef TARGET_ILCE6400A
    .word 0x0659afb1
#else
    .word 0x06309e61
#endif

.section .note.GNU-stack,"",%progbits
