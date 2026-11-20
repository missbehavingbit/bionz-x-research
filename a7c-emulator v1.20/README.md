# a7c-emulator v1.15 — Sony ILCE-7C (α7C) on QEMU (`sony-a7c` machine)

Version: see `VERSION` and `CHANGES.md`.

Goal: run the official, unmodified α7C firmware from your own camera's flash dump.
Built on the `sony-a6400` machine from *sony-a6400-research* (same BIONZ X /
ARM-CXD900X0 SoC family) and the boot-flow lessons of *a5000-emulator*.

**Status: Milestone 2: the stock camera app runs; there's a screen window, but it's still black.**
The stock A7C kernel and all its drivers come up, av-cam (the camera core) runs, the camera
application `im.elf` initialises every one of its ~40 subsystems, and there is a **root shell and live
kernel console on UART0**. The emulator opens a 640×480 screen that follows the frames the UI sends to
the GPU. It stays black for now: once init is done, the app decides the **power switch is OFF** and
starts suspending, before it draws its first view. The power switch is reported by the power/front
sub-microcontroller over a serial link that isn't emulated yet. See [Where it stops](#where-it-stops).

## What works (from the stock kernel's and app's own logs)
| Stage | Result |
|---|---|
| Kernel 3.0.27_nl-rt106+, 4 × Cortex-A5, 1 GiB + 256 MiB | boots; `Brought up 4 CPUs` |
| eMMC (your flash image) | Sony table parsed, `/`, `/usr`, `/system`, `/setting` mounted |
| eMMC = your flash image as is | sector-addressed like a real eMMC (no padding); kernel sees `eMMC: 1864MiB` |
| bootloader stand-in | at every reset the machine copies `/setting/Backup.bin` → 0x02E80000 and `/system/av-cam.bin` → 0x035C6000 from the eMMC |
| initrd → init → ~33 stock kernel modules | all load |
| PCIe RC0/RC1 (memory-card bus) | link-up modelled, empty bus |
| av-cam (camera core, ~140 µITRON tasks) | starts, no crash; DFE DDR3 init passes; frame clock (SYSV) ticks |
| UART0 console | kernel log live; **root `ash` shell** (`tools/a7cshell.py`) |
| `im.elf` camera app | ~180 threads; all `Oc`/`Og` object phases complete incl. ObjMic |
| Power micro (sub-micro) link | A7C wiring (strobe p0.8, request p2.5, SIO ch3); your camera's frames; exchanges running |
| GPU (DMP SMAPH "Sugilite") | functional model (fill / display transfer / 3D) from a5000-emulator |
| Screen | QEMU console 0 shows the UI's OSD frames (black until the UI draws) |

## Layout
| Path | Contents |
|---|---|
| `qemu-overlay/hw/arm/sony_a7c.c` | the machine: memory map, GIC, UART, timers, eMMC, DMA, LDEC, clocks, PCIe RCs, on-chip RAM window, DFE Denali DDR, av-cam counter, I/O catch-all register file, boot stubs |
| `qemu-overlay/hw/display/dmp_smaph.c`, `dmp_shader.c` | DMP SMAPH-S30 GPU + shader interpreter (**from a5000-emulator**, unchanged except a PPF hook) |
| `qemu-overlay/hw/display/cxd90014_vph.c` | VPH display controller + SYSV frame clock (**from a5000-emulator**) |
| `qemu-overlay/hw/display/sony_fbview.c` | screen window following the UI's GPU display transfers (from a5000's `a5k_fbview.c`) |
| `qemu-overlay/hw/gpio/cxd90014_gpio.c`, `hw/misc/cxd90014_sio.c` | GPIO and SIO controllers (**from a5000-emulator**, A7C board option / 6 channels) |
| `qemu-overlay/hw/misc/cxd90014_evsel.c` | timing-event selector (**from a5000-emulator**; off by default, `-M sony-a7c,evsel=on`) |
| `qemu-overlay/hw/timer/cxd4115_timer.c` | CXD4115 timer (from the A6400 work) |
| `patches/qemu-v10.1.0-sony-a7c.patch` | edits to upstream files: build glue, Cortex-A5 CPU, A9MPCore, eMMC/SDHCI, **PL011 `tx-level-irq`** |
| `setup-qemu.sh` | upstream v10.1.0 + overlay + patch → builds `qemu/build/qemu-system-arm` |
| `tools/a7c_extract.py` | builds `fw/` and `img/` from your dump (pure Python, read-only on the dump) |
| `run.sh` | boots the camera (stdio = UART0 / Sony exception monitor) |
| `tools/a7cshell.py` | root shell on the camera console (interactive, or `run 'cmd'`) |
| `tools/uicheck.sh` | boot N s → unfinished `im.elf` boot steps, GPU job counts, screenshot |
| `tools/hwscan.sh`, `unimp_stats.py` | which unmodelled registers the firmware touches / polls |
| `tools/wbi.py` | reads the camera's warm-boot RAM snapshot (nflasha5) |
| `tools/ulog.py` | `im.elf`'s debug log (from a RAM dump) |
| `tools/bootlog.sh`, `klogtap.py`, `klog.py`, `mon.py` | headless boot + kernel-log capture, QEMU monitor helper |
| `docs/A7C_BRINGUP.md` | every A7C-specific finding, with the evidence for each |

## Quick start (Linux or WSL2 Ubuntu)
```sh
sudo apt install git build-essential ninja-build python3-venv pkg-config \
     libglib2.0-dev libpixman-1-dev libfdt-dev flex bison libgtk-3-dev libsdl2-dev
./setup-qemu.sh                                            # ~10-20 min the first time
python3 tools/a7c_extract.py "/mnt/c/Users/Admin/Desktop/sony dumps/Sony A7C dump"
./run.sh                     # window = camera screen; terminal = camera console. Ctrl-A X quits
tools/uicheck.sh 200         # headless: how far the app got + out/screen.png
```
`run.sh` never writes to `img/a7c_emmc.img` (your camera's flash). Guest writes go
to a copy-on-write overlay, `out/emmc.qcow2`. `A7C_FRESH=1 ./run.sh` resets it.

The terminal running `run.sh` is the camera's UART0: kernel messages, then a root shell
(`/bin/ash`, started by the stock init because the cmdline has `console=ttyAM0`). Useful there:
`ps -ef`, `cat /proc/snsc_boot_time` (every app boot step, "B"/"E" pairs), `cat /proc/interrupts`,
`echo t > /proc/sysrq-trigger` (all task backtraces). On the real camera this console is `ttynull`;
`A7C_STOCK_CONSOLE=1 ./run.sh` keeps it that way.

## Where it stops
**Windows, one click: `start_a7c_full.bat`** (rebuild, memory card, start).
`A7C_FULL=1 ./run.sh` (Windows: `run_emulator_wsl.bat A7C_FULL=1`) takes the camera app to its own
screen: after about 20 s of emulated time (a few minutes on a PC) the firmware draws **"NO CARD"**
and the focus-area marks, and stays on. That mode uses instruction-counted time (`-icount shift=2`),
the frame-event model (evsel), the power micro's third status frame (slot C) and a memory-card
reader on PCIe (no card inserted). Without `A7C_FULL` the app still powers itself off
(`OFF_BY_KEY`) after its start-up, as before.

Buttons (click into the emulator window first, hold each key about a second):
M = MENU, arrow keys = up/down/left/right, Enter = centre button, P = playback, F = Fn,
Delete = delete, R = movie, H = shutter half press, Space = shutter full press, [ and ] = control
wheel, = and - = exposure compensation dial, C = C1, A = AEL, Up = DISP. Mode dial: 1 AUTO, 2 P,
3 A, 4 S, 5 M, 6 MR1, 7 MR2, 8 MR3, 9 movie, 0 S&Q. MENU opens the camera's own menus.
Memory card: `A7C_SD_IMAGE=out/sdcard.img` (create it with `create_sdcard_wsl.bat`, or `truncate -s 4G out/sdcard.img`);
the camera detects it and offers to format it (formatting does not finish yet). Windows:
`run_emulator_wsl.bat "A7C_FULL=1 A7C_SD_IMAGE=out/sdcard.img"`.
Card with pictures (v1.20, experimental): `python3 tools/make_sdcard.py out/sdcard_photo.img` (FAT32,
one test picture or `--image my.jpg`); the camera reads it but does not reach its first screen yet.
Web panel (v1.17): `start_a7c_panel.bat` starts everything and opens http://localhost:8642 with the
camera screen and its buttons (a click is a short press; also `python3 tools/a7c_panel.py` in WSL; `--card none`, `--env A7C_MODE_DIAL=A`).
USB controller (v1.16, `A7C_DWC3`, on in `A7C_FULL`): the kernel's USB driver now starts; no PC connection yet.
Next: card writes and the live image.
On Windows: `rebuild_qemu_wsl.bat` builds (first time) or updates the WSL copy in `~/a7c-emulator`, and `run_emulator_wsl.bat` starts it. Details and next steps: `docs/A7C_BRINGUP.md` §7–8.

Machine options: `submicro=FILE` (set by `run.sh`), `evsel=on` (frame events + timers; frame events
currently fault), `evsel-sources=MASK` (which timing sources fire; `0` = timers only), `bootloader=off`.
`units=off` (ADC, SA DSP cores and P2S are on by default). `run.sh` appends `A7C_MACHINE_OPTS` to `-M`; `A7C_ICOUNT=N` adds `-icount shift=N`. `-global cxd90014-evsel.start-ms=N` holds back frame events.
Experimental: `handoff=FILE` / `A7C_HANDOFF_FILE=dump/live-mem-<time>/pa_20021000_handoff.bin` writes a
captured bootloader hand-off page to PA 0x20021000 at reset; `A7C_SUBMICRO_FILE=FILE` uses other
power-micro frames (e.g. live ones) instead of `fw/submicro_frames.bin`.

## Inputs (not in this repository)
Your own dump in `camera_dump_gui` layout (`manifest.json`, `partitions/*.raw`,
`metadata/proc_*`). The extractor checks `nflasha.raw` against `manifest.json`.
The dump contains your camera's settings, Wi-Fi/Bluetooth data and certificates
(`/setting`, `/cert`), so don't publish `fw/`, `img/` or `out/`.

## Credits
The GPU (`dmp_smaph.c`, `dmp_shader.c`), display/frame-clock (`cxd90014_vph.c`), timing-event
(`cxd90014_evsel.c`), GPIO and SIO models and the sub-micro HLE design and frame-viewer (`a5k_fbview.c` → `sony_fbview.c`) models come from
**a5000-emulator** (GPL-2.0); the WBI format follows fwtool.py (MIT), and the base machine, Cortex-A5 and CXD4115 timer from
**sony-a6400-research**. Their reverse-engineering notes guided most of the A7C work.
