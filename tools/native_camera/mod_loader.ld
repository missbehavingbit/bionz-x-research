SECTIONS
{
    . = DEFINED(ML_CODE_ADDR) ? ML_CODE_ADDR : 0x0412f3d0;
    .text : {
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
    }
    /DISCARD/ : {
        *(.ARM.exidx*)
        *(.ARM.extab*)
        *(.comment*)
        *(.note*)
    }
}
