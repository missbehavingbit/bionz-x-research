/* Offline replay only. The linker addresses are fixture placements. */
.syntax unified
.thumb

.section .patch,"ax",%progbits
.ifdef wide
    ldr.w pc, [pc]
    .word matrix_source_fixture
.else
    bl matrix_source_fixture
.endif

.section .fixture,"ax",%progbits
.global matrix_source_fixture
.thumb_func
matrix_source_fixture:
.ifdef wide
    /* Replay the displaced MOVs and recreate the original BL return address. */
    mov r0, r8
    mov r1, sb
    ldr.w lr, =0x63ae4eb1
.endif
    /* r1 is already the original source. Keep the original call's LR. */
    push.w {r2, r3, r12, lr}
    mrs r12, APSR
    cmp r5, #23
    bne restore
    ldr r2, =0x6444b1d4
    cmp r1, r2
    bne restore
    adr r3, fixture_enabled
    ldrb r2, [r3]
    cbz r2, restore
    adr r1, fixture_matrix
restore:
    msr APSR_nzcvq, r12
    pop.w {r2, r3, r12, lr}
    ldr.w pc, =0x63da22e9
    .ltorg

.balign 4
.global fixture_enabled
fixture_enabled:
    .byte 0
    .balign 4
.global fixture_matrix
fixture_matrix:
    .space 18
