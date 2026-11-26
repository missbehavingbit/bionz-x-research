/* Fixed one-page, read-only DWC3 identity observation. ARM Linux EABI. */
.syntax unified
.arm
.global _start
.text
_start:
    ldr r0, =device
    mov r1, #0
    mov r2, #0
    mov r7, #5
    svc #0
    cmp r0, #0
    blt fail
    mov r8, r0
    mov r4, r0
    mov r0, #0
    mov r1, #4096
    mov r2, #1                  /* PROT_READ */
    mov r3, #1                  /* MAP_SHARED */
    ldr r5, =0xf020c            /* mmap2 page offset */
    mov r7, #192
    svc #0
    cmn r0, #4096
    bcs close_fail
    mov r9, r0
    ldr r10, =offsets
    ldr r11, =output
    mov r6, #10
read_word:
    ldr r1, [r10], #4
    ldr r2, [r9, r1]
    str r2, [r11], #4
    subs r6, r6, #1
    bne read_word
    mov r0, #1
    ldr r1, =output
    mov r2, #40
    mov r7, #4
    svc #0
    cmp r0, #40
    movne r6, #1
    moveq r6, #0
    mov r0, r9
    mov r1, #4096
    mov r7, #91
    svc #0
    b close
close_fail:
    mov r6, #1
close:
    mov r0, r8
    mov r7, #6
    svc #0
    mov r0, r6
    b exit
fail:
    mov r0, #1
exit:
    mov r7, #1
    svc #0
.section .rodata
device: .asciz "/dev/mem"
.balign 4
offsets: .word 0x120, 0x140, 0x144, 0x148, 0x14c, 0x150, 0x154, 0x158, 0x15c, 0x600
.bss
.balign 4
output: .space 40
.section .note.GNU-stack,"",%progbits
