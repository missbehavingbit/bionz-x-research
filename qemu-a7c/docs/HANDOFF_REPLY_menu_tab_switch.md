# Reply to the A7C 2.03 menu-tab-switch handoff: QEMU status (emulator v1.3)

**From:** the A7C QEMU emulator (`sony-a7c` machine, `a7c-emulator` v1.3), 2026-11-17.
**Short answer:** QEMU has **not** reached settings-menu initialization, so the requested runtime
capture of `0x2e881c` can't be done yet. Below: what blocks it, which boot phase is reached, and
one firmware-version issue that affects all handoff addresses.

## 1. Firmware version mismatch (affects every address in the handoff)

The emulator runs the user's own camera dump, which is **not 2.03**:

| Item | Handoff (2.03) | Emulator dump |
|---|---|---|
| Build | 2.03 | `/version.txt` = `39119_05.2021111501` (Nov 2021) |
| `viewUnified2.so` SHA-256 | `236532d4…3182d77` | `1e524d4e…fad73e2abe` |
| `CautionConfig.so` SHA-256 | `4b236bb5…b7d841` | `aea39e66…659ad8bc44` |

The version-independent anchors are present. The `PAS_MenuListTab` widget ID **`0xA056F604`** is
loaded in 19 places in this build's `viewUnified2.so`, between ELF 0x2C33F0 and 0x2D9A54 (handoff:
binding at 0x2DE950, dispatcher at 0x2EC532). That is roughly **0x1B000 lower** than 2.03, but
the offset varies per function. The 0x76/0x7B dispatcher uses a jump table, so it can't be found by
a constant search. **Every handoff address must be remapped to this build** (or the emulator must be
given a 2.03 dump) before a breakpoint can be set.

## 2. Boot phase reached

| Phase | Default run | `A7C_ICOUNT=0 A7C_MACHINE_OPTS=evsel=on` |
|---|---|---|
| Kernel, 4 CPUs, eMMC, all partitions, ~40 modules | yes | yes |
| av-cam (LIRO µITRON) running, power-micro link | yes | yes |
| `im.elf` boot steps (`/proc/snsc_boot_time`) | up to `Ms 001_libObj.so`, `Os 008_ObjCamera`, then `EV onSuspending` | not reached |
| GPU / OSD transfers | P3D lists + a few PPF transfers, no menu | not reached |
| Camera decides it is ON | no: `OFF_BY_KEY`, `OSAL_DATE_NOTIME`, battery unknown | slot A sent once, then av-cam stops (~1.2 s emulated) |

**Not reached:** settings-model startup, `ViewSettingMenu` initialization, any menu view. No
`viewUnified2.so` load address has been recorded yet.

## 3. What is missing (in order)

1. **SIO channel-0 device (the "0x39" protocol).** av-cam talks to a second serial device on SIO ch0
   (header built at av-cam 0x638756F0: `39 0D 00 seq …`, ~450 IRQ/s on the live camera). It isn't
   modelled. Its handshake stalls (sequence byte 0x6453B9DF: live 6, emulator 4), so the power-micro
   link waits for it and av-cam stops with error 9 (deliberate store to address 0 at 0x6386D64A).
   Next step: identify the device and replay its replies from the live camera's memory.
2. **Power state.** Without (1) the app never gets "power ON / battery / clock" through and suspends
   (`OFF_BY_KEY`), so the UI never builds its menu views.
3. **Frame timing.** Live camera: frame interrupts at 50 Hz, four timing-event sources (23, 30, 29,
   28); the model has ~30 Hz and two sources. Instruction-counted time (`-icount shift=0`) is required.
4. **Sensor front end.** SA DSP cores are a "null" model (ID check passes, no image processing);
   `[IDT]: IDT : Init NG` still logged. Live view is not needed for the settings menu.
5. **Keys.** The ADC ladder reads "released"; the GPIO key wiring is not mapped yet, so menu
   navigation would be via injected actions or gdb, not buttons.

## 4. What the emulator can provide once the menu is up

* gdb with hardware breakpoints (`A7C_GDB=1`; see the emulator notes for the
  `maint set target-async off` setting that batch scripts need), register and memory dumps before
  and after a call, and the `im.elf` load map from `/proc/<pid>/maps` on the console shell.
* The validation list in the handoff (r0–r3, SP, both stack words, `[view+0x1b0] == [widget+0x186ac]`,
  `[widget+0x1e4]` / `[widget+0x1f0]`, the vtable `+0xC0` target) maps directly onto that.
* Live captures from the real camera (read-only `/dev/mem` tool, `Sony A7C Emulator\a7c-live-capture`)
  to compare emulator state with the camera.

## 5. Requests back

* Either the 2.03 offsets re-derived for build `39119_05.2021111501`, or a way to run the Unicorn
  probe against this build, so both sides use the same binary.
* If you have identified the device on SIO ch0 (0x39 protocol) on the A6400/A7C, or have its
  reply format, that is the current blocker.

Emulator notes: `docs/A7C_BRINGUP.md` (§7.7 timing, §7.8 ported units, §7.9 error 9).
