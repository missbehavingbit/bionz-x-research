/* Mod Loader v2 shim: /usr/lib/libtestcmd.so RX zero tail (0x05662800..0x05663000, clears FW 2.00 EOF 0x2790). */
SECTIONS
{
    . = 0x05662800;
    .text : {
        *(.text.slot)
        *(.text.slotb)
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
    }
    ASSERT(. <= 0x05663000, "shim exceeds the libtestcmd.so cave")
    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) *(.note*) *(.data*) *(.bss*) }
}
