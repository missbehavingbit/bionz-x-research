/* SMOD v2 test mod for the Mod Loader v2 Unicorn harness: a lifecycle entry and a hook target. */
#include <stdint.h>

/* Writable counter inside the code mapping (Mod Loader v2 maps mods RWX). */
__attribute__((section(".text.zdata"), used)) volatile uint32_t t_counter = 0;

__attribute__((section(".text.arri_life"), used)) void t_life(int on) {
    t_counter += on ? 1u : 0x100u;
}

__attribute__((used)) void t_cfg(int reason) {
    if (reason == 2) t_counter += 0x1000u;
}

__attribute__((used)) int t_hook(void) {
    return 42 + (int)t_counter;
}
