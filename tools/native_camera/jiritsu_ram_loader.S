/* Freestanding ARM Thumb constructor. Starts /tmp/jiritsu/ram_probe.elf --daemon
 * directly when present, or falls back to /bin/sh /tmp/jiritsu/run.sh.
 * Uses vfork (__NR_vfork = 190) so im.elf does not incur dup_mm/COW overhead.
 * It does not write /setting or /etc/ld.so.preload.
 */
.syntax unified
.thumb
.arch armv7-a
.text
.global launch
.type launch, %function
.thumb_func
launch:
    push {r4, r5, r7, lr}
    movs r7, #190
    svc #0
    cmp r0, #0
    bne done
    movs r5, #3
1:
    movs r7, #6
    mov r0, r5
    svc #0
    adds r5, #1
    cmp r5, #256
    blo 1b
    sub sp, #32
    adr r0, env_path
    str r0, [sp, #16]
    adr r0, env_ram
    str r0, [sp, #20]
    movs r0, #0
    str r0, [sp, #24]
    adr r0, probe_elf
    movw r1, #0x1c0
    movs r7, #15
    svc #0
    cmp r0, #0
    bne 2f
    adr r0, probe_elf
    str r0, [sp]
    adr r0, daemon_arg
    str r0, [sp, #4]
    movs r0, #0
    str r0, [sp, #8]
    adr r0, probe_elf
    mov r1, sp
    add r2, sp, #16
    movs r7, #11
    svc #0
2:
    adr r0, shell
    str r0, [sp]
    adr r0, script
    str r0, [sp, #4]
    movs r0, #0
    str r0, [sp, #8]
    adr r0, shell
    mov r1, sp
    add r2, sp, #16
    movs r7, #11
    svc #0
    movs r7, #1
    movs r0, #127
    svc #0
done:
    pop {r4, r5, r7, pc}
    .balign 4
probe_elf:
    .asciz "/tmp/jiritsu/ram_probe.elf"
    .balign 4
daemon_arg:
    .asciz "--daemon"
    .balign 4
shell:
    .asciz "/bin/sh"
    .balign 4
script:
    .asciz "/tmp/jiritsu/run.sh"
    .balign 4
env_path:
    .asciz "PATH=/bin:/sbin:/usr/bin:/usr/sbin"
    .balign 4
env_ram:
    .asciz "A6400_EPHEMERAL_RAM=1"
    .balign 4

.section .init_array, "aw"
    .word launch
