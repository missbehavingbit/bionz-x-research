/* SMOD v2 mods: position-independent code linked at 0. Writable data must live in .text
 * (the loader maps mods RWX) or in the bss area declared with --bss. */
SECTIONS
{
    . = 0;
    .text : {
        *(.text.arri_life)
        *(.text*)
        *(.rodata*)
        *(.data*)
        . = ALIGN(4);
    }
    /* First byte of the zero-filled --bss area the core maps right after the code. */
    PROVIDE_HIDDEN(__smod_bss = .);
    ASSERT(SIZEOF(.bss) == 0, "SMOD v2: use --bss or .text for writable data")
    .bss : { *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) *(.note*) *(.got*) }
}
