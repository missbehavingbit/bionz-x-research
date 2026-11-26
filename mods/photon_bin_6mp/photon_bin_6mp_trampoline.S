.syntax unified
.thumb
.section .text

/* 12-byte prologue replay of ImageBuilder::MakeRawInfoArea in libObj.so:
 *   ILCE-6400A 1.01: 0x05fc2000 + 0x00d7861c = 0x06d3a61c (continues at 0x06d3a629).
 *   ILCE-6100  1.00: 0x05d18000 + 0x00decbcc = 0x06b04bcc (continues at 0x06b04bd9).
 * Relocated automatically by Mod Loader v2 via RLC1 /proc/self/maps. */
.balign 4
.global pb_orig_make_raw_info_area
.hidden pb_orig_make_raw_info_area
.thumb_func
pb_orig_make_raw_info_area:
    push.w {r4, r5, r6, r7, r8, r9, r10, r11, lr}
    sub sp, #0x14
    add r7, sp, #8
    mov r4, r0
    mov r6, r1
    ldr.w pc, [pc, #0]
#if defined(TARGET_A6100) || defined(TARGET_ILCE6100)
    .word 0x06b04bd9
#else
    .word 0x06d3a629
#endif

.section .note.GNU-stack,"",%progbits
