# E0676 — Unicorn SYSV/VPH frame clock and evsel `ext-vd`

Date: 2026-11-20. Offline. No camera, USB, or firmware image was executed for this record.

## Result

With `--qemu-peripherals`, `UnicornMachine` now advances the same display timing contract that [E0529](0529-qemu-a6400-adf-decode-sysv-frame-clock-unblocks-objmic.md) used to get QEMU `av-cam` past the `ObjMic` stall:

- SYSV at `0xF3402000`: frame counter `+0x98`, phase `+0x28` bit 0, IRQ 98 every `33367` µs.
- VPH at `0xF3440000`: independent 60 Hz vsync pulses IRQ 101 and 102, clears AIF channel start bits, and can compose a panel from ch51 luma and ch53 RGBA4444/32-bit OSD.
- Evsel at `0xF3C30000` with `ext-vd`: each SYSV frame fires enabled group-A channels (level IRQ 83). Group-B comparators use the same 1 MHz tick (level IRQ 84).

The clock uses the existing Unicorn rule of one timer tick per instruction. Edges stay latched until `GICD_ISENABLER` actually enables the line, and that enable register is write-1-to-set so IRQ 98 and IRQ 101 can both stay enabled. Once any of IRQ 83, 98, 101, or 102 is enabled, run slices stop on the next frame or vsync so the counter advances by one event per delivery.

## What this does not show

No captured `nflasha3` replay was run here. The `emmc.img` / `inputs-pr-preflight` set used by [E0656](0656-unicorn-main-firmware-boot.md) is not in this workspace. Nothing in this record shows `ObjMic` completing, `modelCamera ACTIVE`, or a Unicorn rear-LCD frame. CPUs 1–3 are still absent. The QEMU late-source evsel phase (`late-src` / `late-src2`) is not ported.

## Check

```powershell
.\venv\Scripts\python.exe -m unittest tests.emulator.test_cxd_peripherals tests.emulator.test_timer_gic_model -q
```

That run covers the frame counter and phase, vsync start-bit clear, evsel frame and comparator, one opaque-red RGBA4444 pixel, GIC enable of IRQ 98 before delivery, and a guest load of SYSV `+0x98`.
