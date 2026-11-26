SECTIONS
{
    . = 0x011964f0;
    .text : {
        *(.text.mt_original_release)
        *(.text.mt_original_press)
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
    }
    /DISCARD/ : {
        *(.ARM.exidx*)
        *(.ARM.attributes*)
        *(.comment)
        *(.note*)
    }
}
