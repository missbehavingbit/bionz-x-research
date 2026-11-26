/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Bare-metal handover fixture for handover_guard.c (E0638).
 *
 * Loaded by the sony-a6400 machine as a raw "kernel" at 0x00108000 (CPU0 enters
 * from the board stub; CPU1..3 stay in the SMP mailbox WFE loop).  It enables
 * an identity-mapped MMU with I/D caches, copies a "new kernel" entry stub to
 * NEW_ENTRY while the D-cache is on (dirty lines), then tears down with the
 * mainline ARMv7 order:
 *   cpsid if -> cpu_v7_proc_fin (clear C, I) -> v7_flush_dcache_all (set/way
 *   DCCISW, dsb, isb) -> ICIALLU, dsb, isb -> cpu_v7_reset (clear M, isb, bx).
 *
 * Each --defsym switch removes exactly one step, to prove the guard catches it:
 *   KEEP_MMU, KEEP_DCACHE, KEEP_ICACHE, NO_CLEAN, NO_ICINV, NO_DSB, NO_ISB,
 *   IRQ_ON, ROGUE_CPU1 (release CPU1 into a loop without WFx).
 * DMA switches (E0639, guard dma=handover_dma_contracts.txt):
 *   DWC3 (start DCTL.RUN_STOP, then stop and poll DSTS.DEVCTRLHLT; needs
 *   -M sony-a6400,dwc3-gadget=on), with DWC3_KEEP_RUNNING or DWC3_NO_POLL to
 *   break it; WORK_DMA writes an unused work DMA channel block (no stop contract).
 *   WDMA replays the av-cam work DMA sequence (E0640); WDMA_NO_POLL skips the
 *   +0x01C busy poll and WDMA_KEEP_ENABLED skips the final disable.
 *   XDMAC0_COPY runs a 16-byte XDMAC0 ch0 copy, clears +0x30 IRQ status, and
 *   polls +0x30 unless XDMAC0_NO_POLL is set (XDMAC0_KEEP_PENDING skips clear).
 * Strict kernel teardown switches (guard tlb=1 / gic=1 / l2=1 / strict_kernel=1):
 *   TEARDOWN_TLBI (or THUMB_TEARDOWN_TLBI; TLBI_AFTER_DSB places TLBI after DSB),
 *   GIC_ENABLE + GIC_DISABLE (GIC_KEEP_GICC / GIC_KEEP_GICD leave one side on),
 *   PL310_ENABLE + PL310_TEARDOWN (PL310_NO_CLEAN / PL310_NO_SYNC / PL310_KEEP_ON).
 *
 * Build: arm-linux-gnueabi-gcc -mcpu=cortex-a5 -marm -nostdlib -Wl,-Ttext=0x108000
 *        [-Wa,--defsym,NO_DSB=1] handover_payload.S -o p.elf && objcopy -O binary
 */
    .syntax unified
    .arm
    .equ TTB,         0x00400000
    .equ NEW_ENTRY,   0x00600000
    .equ ROGUE_LOOP,  0x00610000
    .equ SMP_MBOX,    0xFE005010
    .equ XDMAC0_BASE, 0xF0000000
    .equ DWC3_DCTL,   0xF020C704
    .equ DWC3_DSTS,   0xF020C70C
    .equ WDMA_BASE,   0xF0102000
    .equ GICC_CTLR,   0xF8000100
    .equ GICD_CTLR,   0xF8001000
    .equ L2X0_BASE,   0xF8002000

    .text
    .global _start
_start:
    cpsid   if

    /* Identity sections: first 1 GiB normal WB (0xC0E), rest strongly ordered (0xC02). */
    ldr     r0, =TTB
    mov     r1, #0
1:  cmp     r1, #0x400
    movlo   r2, #0xC00
    orrlo   r2, r2, #0x0E
    movhs   r2, #0xC00
    orrhs   r2, r2, #0x02
    orr     r2, r2, r1, lsl #20
    str     r2, [r0, r1, lsl #2]
    add     r1, r1, #1
    cmp     r1, #0x1000
    blo     1b

    mov     r1, #0
    mcr     p15, 0, r1, c2, c0, 2       @ TTBCR = 0
    mcr     p15, 0, r0, c2, c0, 0       @ TTBR0 = TTB
    mvn     r1, #0
    mcr     p15, 0, r1, c3, c0, 0       @ DACR: all manager
    mov     r1, #0
    mcr     p15, 0, r1, c8, c7, 0       @ TLBIALL
    mcr     p15, 0, r1, c7, c5, 0       @ ICIALLU
    dsb
    isb
    mrc     p15, 0, r1, c1, c0, 0
    orr     r1, r1, #0x1000             @ I
    orr     r1, r1, #0x0005             @ C | M
    mcr     p15, 0, r1, c1, c0, 0
    isb

    /* Copy the new-kernel stub with the D-cache on. */
    adr     r3, entry_stub
    ldr     r4, =NEW_ENTRY
    ldm     r3, {r5-r8}
    stm     r4, {r5-r8}

.ifdef DWC3
    ldr     r4, =DWC3_DCTL
    mov     r5, #0x80000000
    str     r5, [r4]                    @ DCTL.RUN_STOP = 1 (needs dwc3-gadget=on)
.endif
.ifdef DWC3_SOFTRESET
    ldr     r4, =DWC3_DCTL
    mov     r5, #0x40000000
    str     r5, [r4]                    @ DCTL.CSFTRST only, as the stock kernel does (E0639)
.endif
.ifdef WORK_DMA
    ldr     r4, =0xF0102080
    mov     r5, #1
    str     r5, [r4]                    @ work DMA channel block with no known stop contract
.endif
.ifdef WDMA
    /* av-cam 0x635DC8B8.. (E0640): one 16-byte copy on the +0x1C0 channel. */
    ldr     r4, =WDMA_BASE
    mov     r5, #1
    str     r5, [r4, #0x030]            @ enable
    mov     r5, #0x40
    str     r5, [r4, #0x008]
    str     r5, [r4, #0x00C]
    mov     r5, #0
    str     r5, [r4, #0x1C8]
    ldr     r5, =0x00500000
    str     r5, [r4, #0x1C0]            @ source
    ldr     r5, =0x00500100
    str     r5, [r4, #0x1C4]            @ destination
    ldr     r5, =0x1C491004
    str     r5, [r4, #0x1CC]            @ config | 4 words
    mov     r5, #1
    str     r5, [r4, #0x1D0]            @ start
.ifndef WDMA_NO_POLL
4:  ldr     r5, [r4, #0x01C]
    tst     r5, #0x40                   @ channel busy
    bne     4b
.endif
.ifndef WDMA_KEEP_ENABLED
    mov     r5, #0
    str     r5, [r4, #0x030]            @ disable
.endif
.endif
.ifdef XDMAC0_COPY
    ldr     r4, =XDMAC0_BASE
    mov     r5, #15
    str     r5, [r4, #0x010]            @ TBC - 1 = 15 (16 bytes)
    ldr     r5, =0x00500200
    str     r5, [r4, #0x014]            @ SSA
    ldr     r5, =0x00500300
    str     r5, [r4, #0x018]            @ DSA
    mov     r5, #1
    str     r5, [r4, #0x028]            @ trigger ch0
.ifndef XDMAC0_KEEP_PENDING
    mov     r5, #0
    str     r5, [r4, #0x030]            @ clear ch0 IRQ status
.endif
.ifndef XDMAC0_NO_POLL
    ldr     r5, [r4, #0x030]            @ confirm ch0 status == 0
.endif
.endif
.ifdef GIC_ENABLE
    mov     r5, #1
    ldr     r4, =GICD_CTLR
    str     r5, [r4]
    ldr     r4, =GICC_CTLR
    str     r5, [r4]
.endif
.ifdef PL310_ENABLE
    ldr     r4, =L2X0_BASE
    mov     r5, #1
    str     r5, [r4, #0x100]            @ L2X0_CTRL = 1
.endif

.ifdef ROGUE_CPU1
    ldr     r4, =ROGUE_LOOP
    ldr     r5, rogue_insn
    str     r5, [r4]
    ldr     r4, =SMP_MBOX
    ldr     r5, =ROGUE_LOOP
    str     r5, [r4]                    @ CPU1 mailbox
    dsb
    sev
    mov     r5, #0x100000               @ let CPU1 reach its loop
2:  subs    r5, r5, #1
    bne     2b
.endif

    /* ---- teardown (mainline order) ---- */
    cpsid   if
.ifdef GIC_DISABLE
    mov     r5, #0
.ifndef GIC_KEEP_GICC
    ldr     r4, =GICC_CTLR
    str     r5, [r4]
.endif
.ifndef GIC_KEEP_GICD
    ldr     r4, =GICD_CTLR
    str     r5, [r4]
.endif
.endif
.ifdef DWC3
.ifndef DWC3_KEEP_RUNNING
    ldr     r4, =DWC3_DCTL                @ dwc3_gadget_run_stop(0)
    mov     r5, #0
    str     r5, [r4]
.ifndef DWC3_NO_POLL
    ldr     r4, =DWC3_DSTS
3:  ldr     r5, [r4]
    tst     r5, #0x00400000             @ DSTS.DEVCTRLHLT
    beq     3b
.endif
.endif
.endif
    mrc     p15, 0, r0, c1, c0, 0       @ cpu_v7_proc_fin
.ifndef KEEP_ICACHE
    bic     r0, r0, #0x1000
.endif
.ifndef KEEP_DCACHE
    bic     r0, r0, #0x0006
.endif
    mcr     p15, 0, r0, c1, c0, 0
.ifndef NO_CLEAN
    bl      v7_flush_dcache_all
.endif
.ifdef PL310_TEARDOWN
    ldr     r4, =L2X0_BASE
.ifndef PL310_NO_CLEAN
    movw    r5, #0xFFFF
    str     r5, [r4, #0x7FC]            @ L2X0_CLEAN_INV_WAY
.endif
.ifndef PL310_NO_SYNC
    mov     r5, #0
    str     r5, [r4, #0x730]            @ L2X0_CACHE_SYNC
.endif
.ifndef PL310_KEEP_ON
    mov     r5, #0
    str     r5, [r4, #0x100]            @ L2X0_CTRL = 0
.endif
.endif
    mov     r0, #0
.ifndef NO_ICINV
    mcr     p15, 0, r0, c7, c5, 0       @ ICIALLU
.endif
.ifdef TEARDOWN_TLBI
    mcr     p15, 0, r0, c8, c7, 0       @ TLBIALL
.endif
.ifdef THUMB_TEARDOWN_TLBI
    adr     r2, .Lthumb_tlbi + 1
    bx      r2
    .thumb
.Lthumb_tlbi:
    mcr     p15, 0, r0, c8, c7, 0       @ T32 TLBIALL (0xEE08 0x0F17)
    adr     r2, .Larm_after_tlbi
    bx      r2
    .balign 4
    .arm
.Larm_after_tlbi:
.endif
.ifndef NO_DSB
    dsb
.endif
.ifdef TLBI_AFTER_DSB
    mcr     p15, 0, r0, c8, c7, 0       @ TLBIALL missing post-TLBI DSB
.endif
    isb
    ldr     r0, =NEW_ENTRY              @ cpu_v7_reset(NEW_ENTRY)
    mrc     p15, 0, r1, c1, c0, 0
.ifndef KEEP_MMU
    bic     r1, r1, #0x1
.endif
    mcr     p15, 0, r1, c1, c0, 0
.ifndef NO_ISB
    isb
.endif
.ifdef IRQ_ON
    cpsie   i
.endif
    bx      r0

/* arch/arm/mm/cache-v7.S v7_flush_dcache_all, ARM mode, clean+invalidate to LoC. */
v7_flush_dcache_all:
    dmb
    mrc     p15, 1, r0, c0, c0, 1       @ CLIDR
    mov     r3, r0, lsr #23
    ands    r3, r3, #7 << 1             @ LoC * 2
    beq     finished
    mov     r10, #0
flush_levels:
    add     r2, r10, r10, lsr #1
    mov     r1, r0, lsr r2
    and     r1, r1, #7
    cmp     r1, #2
    blt     skip
    mcr     p15, 2, r10, c0, c0, 0      @ CSSELR
    isb
    mrc     p15, 1, r1, c0, c0, 0       @ CCSIDR
    and     r2, r1, #7
    add     r2, r2, #4                  @ log2(line bytes)
    movw    r4, #0x3ff
    ands    r4, r4, r1, lsr #3          @ max way
    clz     r5, r4
    movw    r7, #0x7fff
    ands    r7, r7, r1, lsr #13         @ max set
loop1:
    mov     r9, r7
loop2:
    orr     r11, r10, r4, lsl r5
    orr     r11, r11, r9, lsl r2
    mcr     p15, 0, r11, c7, c14, 2     @ DCCISW
    subs    r9, r9, #1
    bge     loop2
    subs    r4, r4, #1
    bge     loop1
skip:
    add     r10, r10, #2
    cmp     r3, r10
    bgt     flush_levels
finished:
    mov     r10, #0
    mcr     p15, 2, r10, c0, c0, 0
.ifndef NO_DSB
    dsb
.endif
    isb
    bx      lr

    .balign 4
entry_stub:                             @ copied to NEW_ENTRY
    wfi
    b       entry_stub
    nop
    nop
rogue_insn:
    b       .                           @ position-independent self-loop
    .ltorg
