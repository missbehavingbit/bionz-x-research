/* ARRI color mod: /usr/lib/libcompassutil.so RX zero tail (live 0x0414f7f0..0x04150000). */
SECTIONS
{
    . = 0x0414f7f0;
    .text : {
        *(.text.arri_life)
        *(.text*)
        *(.rodata*)
        *(.data*)
        *(.bss*)
        . = ALIGN(4);
    }
    ASSERT(. <= 0x04150000, "ARRI color mod exceeds the libcompassutil.so cave")
    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) *(.note*) }
}
