/* Mod Loader v2 core: position-independent image linked at 0, loaded anywhere by the shim. */
SECTIONS
{
    . = 0;
    .text : {
        *(.text.core_init)
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
    }
    ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "core must not have writable data")
    .data : { *(.data*) }
    .bss : { *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) *(.note*) *(.got*) *(.dynamic) }
}
