/* ARRI gamma mod: /usr/lib/libtestcmd.so RX zero tail (live 0x0566275c..0x05663000). */
SECTIONS
{
    . = 0x05662760;
    .text : {
        *(.text.arri_life)
        *(.text*)
        *(.rodata*)
        *(.data*)
        *(.bss*)
        . = ALIGN(4);
    }
    ASSERT(. <= 0x05663000, "ARRI gamma mod exceeds the libtestcmd.so cave")
    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) *(.note*) }
}
