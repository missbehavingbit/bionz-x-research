/* ModsTabLook: code, data and state packed into /usr/lib/libptp.so RX padding (live 0x11434398..0x11435000). */
SECTIONS
{
    . = 0x11434398;
    .text : {
        *(.text.mtl_hook*)
        *(.text*)
        *(.rodata*)
        *(.data*)
        *(.bss*)
        . = ALIGN(4);
    }
    /DISCARD/ : {
        *(.ARM.exidx*)
        *(.ARM.extab*)
        *(.comment*)
        *(.note*)
    }
}
