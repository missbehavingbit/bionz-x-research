/* ZV-E10 Touch Mod Trampoline
 * Spliced original execution for WidgetSystem::dispatchMouseRelease and dispatchMousePress.
 * Exactly mirrors the ILCE-6400 (a6400) MTF2 architecture.
 *
 * 12-byte original prologue:
 * dispatchMouseRelease (0x00618000):
 *   push.w {r4, r5, r6, r7, r8, sb, sl, lr}
 *   sub sp, #0x20
 *   mov.w r8, #0x14
 *   movs r2, #1
 *
 * dispatchMousePress (0x0061810c):
 *   push.w {r4, r5, r6, r7, r8, sb, sl, lr}
 *   sub sp, #0x20
 *   mov.w sb, #0x14
 *   movs r2, #1
 */
.syntax unified
.thumb
.section .text

.balign 4
.global mt_original_release
.hidden mt_original_release
.thumb_func
mt_original_release:
    push.w {r4, r5, r6, r7, r8, sb, sl, lr}
    sub sp, #0x20
    mov.w r8, #0x14
    movs r2, #1
    ldr.w pc, [pc, #0]
mt_zve10_rel_resume:
    .word 0x0061800d   /* Relocated dynamically: libObj_base + 0x0061800c | 1 */

.balign 4
.global mt_original_press
.hidden mt_original_press
.thumb_func
mt_original_press:
    push.w {r4, r5, r6, r7, r8, sb, sl, lr}
    sub sp, #0x20
    mov.w sb, #0x14
    movs r2, #1
    ldr.w pc, [pc, #0]
mt_zve10_prs_resume:
    .word 0x00618119   /* Relocated dynamically: libObj_base + 0x00618118 | 1 */

.balign 4
.global mt_get_state_ptr
.hidden mt_get_state_ptr
.thumb_func
mt_get_state_ptr:
    ldr r0, mt_zve10_state_ptr
    bx lr

.balign 4
.global mt_zve10_state_ptr
mt_zve10_state_ptr:
    .word 0x013f8000   /* Relocated dynamically: libObj_base + 0x013f8000 */

.section .note.GNU-stack,"",%progbits
