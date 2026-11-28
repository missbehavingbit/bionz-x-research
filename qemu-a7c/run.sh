#!/bin/bash
# Boot the stock Sony ILCE-7C firmware on the sony-a7c QEMU machine.
#
#   ./run.sh                    boot; camera console (kernel log + root shell) on
#                               stdio, screen in a QEMU window when a desktop is
#                               available (WSLg / X11 / Wayland)
#   A7C_DISPLAY=none|gtk|sdl    force the display backend
#   A7C_GDB=1 ./run.sh          start halted, gdb stub on tcp::1234
#   A7C_SUBMICRO_FILE=fw/submicro_frames_live.bin ./run.sh
#                               power-micro frames from the running camera
#   A7C_HANDOFF_FILE=dump/live-mem-<time>/pa_20021000_handoff.bin ./run.sh
#                               experimental: captured bootloader hand-off page
#   A7C_MACHINE_OPTS=evsel=on,evsel-sources=0 ./run.sh
#                               extra -M options (here: evsel timers, no frame events)
#   A7C_ICOUNT=0 A7C_MACHINE_OPTS=evsel=on ./run.sh
#   A7C_ICOUNT=2 A7C_MACHINE_OPTS=evsel=on ./run.sh       same, several times faster (v1.8)
#   A7C_CH0_FILE=dump/live-mem-<time>/pa_0414db00_ch0_buffers.bin ./run.sh
#                               power-chip ch0 replies 05..08 captured from the camera
#   A7C_FULL=1 ./run.sh         camera app to its own screen ("NO CARD"): icount 2, evsel,
#                               slot C, memory-card reader, buttons (M = MENU, arrows,
#                               Enter, P = playback, F = Fn, R = movie, H/Space = shutter,
#                               [ ] = wheel, 1-0 = mode dial AUTO P A S M MR1-3 movie S&Q,
#                               = - = EV dial, C = C1, A = AEL), VPH screen first
#   A7C_SLOTC=1 ./run.sh        answer power-micro slot C (needs A7C_CARD_SLOT=1 too)
#   A7C_CARD_SLOT=1 ./run.sh    PCIe1 root port + O2 Micro card reader (no card inserted)
#   A7C_GPIO_LIVE=1 ./run.sh    GPIO inputs at the switched-on camera's levels
#                               instruction-counted time: frame timing as on the camera
#   ./run.sh -d unimp           any extra args go to QEMU
#
# Screens: console 0 = "sony-fbview", the 640x480 OSD frames the camera UI
# hands to the GPU display transfer (works today, black until the UI draws);
# console 1 = VPH panel output (real scan-out, once av-cam programs it).
# In the GTK window: View menu or Ctrl+Alt+1 / Ctrl+Alt+2.
# Headless: tools/mon.py out/monitor.sock 'screendump "out/s.png" -f png'.
#
# Inputs come from tools/a7c_extract.py (fw/, img/).  The eMMC image is used
# through a copy-on-write overlay (out/emmc.qcow2) so img/a7c_emmc.img -- your
# camera's flash -- is never modified.  A7C_FRESH=1 discards the overlay.
#
# What the machine does in place of Sony's bootloader (hw/arm/sony_a7c.c):
#   kernel  fw/VMLINUX.BIN -> 0x00108000, initrd fw/INITRD.IMG -> 0x00700000,
#   "kemco <cmdline>" -> 0x00103000, r1 = machine id 0xDA9
#   at every reset, from the eMMC itself: /setting/Backup.bin -> 0x02E80000
#   (its header), /system/av-cam.bin -> 0x035C6000 (its vector table)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
QEMU="${A7C_QEMU:-$HERE/qemu/build/qemu-system-arm}"
QIMG="${A7C_QEMU_IMG:-$(dirname "$QEMU")/qemu-img}"
OUT="$HERE/out"; mkdir -p "$OUT"

for f in fw/VMLINUX.BIN fw/INITRD.IMG fw/proc_cmdline img/a7c_emmc.img; do
    [ -f "$HERE/$f" ] || { echo "missing $f - run: python3 tools/a7c_extract.py \"<dump dir>\"" >&2; exit 1; }
done

if [ "${A7C_FRESH:-0}" = "1" ] || [ ! -f "$OUT/emmc.qcow2" ]; then
    rm -f "$OUT/emmc.qcow2"
    "$QIMG" create -q -f qcow2 -F raw -b "$HERE/img/a7c_emmc.img" "$OUT/emmc.qcow2"
fi

# Stock cmdline from the camera.  console=ttynull -> ttyAM0 so the kernel
# console goes to UART0 (A7C_STOCK_CONSOLE=1 keeps the camera's setting).
CMDLINE="$(sed 's/^ *//' "$HERE/fw/proc_cmdline")"
if [ "${A7C_STOCK_CONSOLE:-0}" != "1" ]; then
    CMDLINE="${CMDLINE/console=ttynull/console=ttyAM0,115200 amba2.console=1}"
fi
# A7C_APPEND="..." adds kernel parameters (e.g. log_buf_len=4M for a longer dmesg)
[ -n "${A7C_APPEND:-}" ] && CMDLINE="$CMDLINE $A7C_APPEND"

EXTRA=()
[ "${A7C_GDB:-0}" = "1" ] && EXTRA+=(-s -S)
MACHINE="sony-a7c"
# power micro: the camera's own status frames (power switch, battery, clock)
# A7C_FULL=1 (default, v1.10; A7C_FULL=0 for minimal boot): everything that takes the
# camera app to its own screen (docs §7.14): icount shift 2, evsel, power-micro slot C,
# memory-card reader, USB controller (A7C_DWC3, v1.16; A7C_DWC3=0 turns it off).
if [ "${A7C_FULL:-1}" = "1" ]; then
    A7C_ICOUNT="${A7C_ICOUNT:-2}"
    case ",${A7C_MACHINE_OPTS:-}," in *,evsel=*) ;; *) A7C_MACHINE_OPTS="${A7C_MACHINE_OPTS:+$A7C_MACHINE_OPTS,}evsel=on" ;; esac
    # the switched-on camera's own micro frames, when captured (fw/, private): with the
    # power-off snapshot frames the camera never reports a memory card (docs §7.18)
    if [ -z "${A7C_SUBMICRO_FILE:-}" ] && [ -f "$HERE/fw/submicro_frames_live.bin" ]; then
        export A7C_SUBMICRO_FILE="$HERE/fw/submicro_frames_live.bin"
    fi
    export A7C_SLOTC="${A7C_SLOTC:-1}" A7C_CARD_SLOT="${A7C_CARD_SLOT:-1}" \
           A7C_REAR_CONTROLS="${A7C_REAR_CONTROLS:-1}" A7C_GPIO_LIVE="${A7C_GPIO_LIVE:-1}" \
           A7C_SCREEN="${A7C_SCREEN:-vph}" A7C_DWC3="${A7C_DWC3:-1}" \
           A7C_LIVEVIEW="${A7C_LIVEVIEW:-1}" A7C_LENS_REPLY="${A7C_LENS_REPLY:-1}"
    A7C_EVSEL_LATE_US="${A7C_EVSEL_LATE_US:-500}"
fi

# A7C_SUBMICRO_FILE=fw/submicro_frames_live.bin uses frames captured from the
# running camera (capture tool: Sony A7C Emulator\a7c-live-capture) instead.
SM_FILE="${A7C_SUBMICRO_FILE:-$HERE/fw/submicro_frames.bin}"
[ -f "$SM_FILE" ] || SM_FILE="$HERE/$SM_FILE"
if [ -f "$SM_FILE" ] && [ "${A7C_SUBMICRO:-1}" != "0" ]; then
    MACHINE="$MACHINE,submicro=$SM_FILE"
fi

# A7C_HANDOFF_FILE=dump/live-mem-.../pa_20021000_handoff.bin: experimental,
# write that captured bootloader hand-off page to PA 0x20021000 at reset.
if [ -n "${A7C_HANDOFF_FILE:-}" ]; then
    HO_FILE="$A7C_HANDOFF_FILE"
    [ -f "$HO_FILE" ] || HO_FILE="$HERE/$HO_FILE"
    [ -f "$HO_FILE" ] || { echo "A7C_HANDOFF_FILE: $A7C_HANDOFF_FILE not found" >&2; exit 1; }
    MACHINE="$MACHINE,handoff=$HO_FILE"
fi
[ -n "${A7C_LIVEVIEW_SOURCE:-}" ] && MACHINE="$MACHINE,liveview-source=$A7C_LIVEVIEW_SOURCE"
[ -n "${HOSTWIN:-}" ] && MACHINE="$MACHINE,hostwin=$HOSTWIN"
[ "${A7C_USB_VBUS:-0}" = "1" ] && MACHINE="$MACHINE,usb-vbus-test=on,usb-cid-high=on"
# A7C_MACHINE_OPTS=evsel=on,evsel-sources=0: extra machine options, appended to -M
[ -n "${A7C_MACHINE_OPTS:-}" ] && MACHINE="$MACHINE,$A7C_MACHINE_OPTS"
# A7C_ICOUNT=0: run with -icount shift=0 (emulated time = 1 ns per guest instruction),
# so the firmware's 1/8-frame timer deadlines are met as on the real camera (§7.7)
[ -n "${A7C_ICOUNT:-}" ] && EXTRA+=(-icount "shift=$A7C_ICOUNT")
# A7C_EVSEL_LATE_US=N (with evsel=on): timing source 30 (HS root) fires N us after
# source 29 (frame start), as bionz-x-research qemu-a6400 EVSEL_LATE_US (E0557).
[ -n "${A7C_EVSEL_LATE_US:-}" ] && EXTRA+=(-global cxd90014-evsel.late-src=30
                                         -global "cxd90014-evsel.late-us=$A7C_EVSEL_LATE_US")

# A7C_SD_IMAGE=file (v1.13, experimental): a memory card in the PCIe card reader (needs
# A7C_CARD_SLOT=1). Not seen by the camera yet: it powers the reader off at ~3.4 s and does
# not power it on again (docs §7.17).
if [ -n "${A7C_SD_IMAGE:-}" ] && [ "$A7C_SD_IMAGE" != none ]; then
    EXTRA+=(-drive if=sd,index=1,format=raw,file="$A7C_SD_IMAGE")
fi

if [ -z "${A7C_DISPLAY:-}" ]; then
    A7C_DISPLAY=none
    if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        "$QEMU" -display help 2>/dev/null | grep -qx gtk && A7C_DISPLAY=gtk
        [ "$A7C_DISPLAY" = none ] && "$QEMU" -display help 2>/dev/null | grep -qx sdl && A7C_DISPLAY=sdl
    fi
fi

exec "$QEMU" -M "$MACHINE" -smp 4 \
    -kernel "$HERE/fw/VMLINUX.BIN" -initrd "$HERE/fw/INITRD.IMG" -append "$CMDLINE" \
    -drive if=sd,format=qcow2,file="$OUT/emmc.qcow2" \
    -serial "${A7C_SERIAL:-mon:stdio}" -display "$A7C_DISPLAY" \
    -monitor unix:"$OUT/monitor.sock",server,nowait \
    "${EXTRA[@]}" "$@"
