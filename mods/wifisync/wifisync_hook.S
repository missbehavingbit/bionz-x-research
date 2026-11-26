/* ConPutWriteStill interposition for WiFiSync.
 *
 * The SMOD hook replaces the first 12 bytes of MprMonCtrl::ConPutWriteStill
 * (ILCE-6400 2.00 live libmpr.so +0x00869D24, prologue
 * `2de9f041 0d46 00af 0446 4049`) with a 12-byte Thumb jump.
 *
 *   +0  push.w {r4,r5,r6,r7,r8,lr}
 *   +4  mov    r5, r1
 *   +6  add    r7, sp, #0
 *   +8  mov    r4, r0
 *   +a  ldr    r1, [pc, #0x100]  (loads 0x00b1b624 from +0x10c)
 *   +c  mov    r8, r5            <- resume here (+0x0C)
 *
 * This trampoline saves the caller-saved registers, calls ws_hook_note()
 * (O(1) capture counter + timestamp), replays the overwritten prologue
 * (+0..+b) including the PC-relative literal load, and resumes at +0x0C.
 *
 * The resume literal holds the reference-layout address with the Thumb bit;
 * Mod Loader v2's RLC1 table shifts it by (runtime libmpr base - reference).
 */
.syntax unified
.thumb
.section .text

.balign 4
.global ws_capture_hook
.hidden ws_capture_hook
.thumb_func
ws_capture_hook:
    push    {r0, r1, r2, r3, r12, lr}
    bl      ws_hook_note
    pop     {r0, r1, r2, r3, r12, lr}
    push.w  {r4, r5, r6, r7, r8, lr}
    mov     r5, r1
    add     r7, sp, #0
    mov     r4, r0
    ldr     r1, ws_lit_r1
    ldr.w   pc, ws_resume
.balign 4
ws_lit_r1:
    .word   0x00b1b624        /* libmpr +0x00869E30 constant (< 0x04000000, unrelocated) */
ws_resume:
    .word   0x079add31        /* libmpr 0x07144000 + 0x00869D30, Thumb bit set */

.section .note.GNU-stack,"",%progbits
