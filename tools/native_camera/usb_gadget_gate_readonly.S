/* Fixed one-page read-only observation of the native gadget-start gate. */
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
    mov r2, #1
    mov r3, #1
    ldr r5, =0xf0200
    mov r7, #192
    svc #0
    cmn r0, #4096
    bcs close_fail
    mov r9, r0
    ldr r2, [r9, #0x24]
    ldr r1, =output
    str r2, [r1]
    mov r0, #1
    mov r2, #4
    mov r7, #4
    svc #0
    cmp r0, #4
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
.bss
.balign 4
output: .space 4
.section .note.GNU-stack,"",%progbits
