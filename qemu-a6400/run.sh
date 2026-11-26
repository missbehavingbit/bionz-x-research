#!/bin/bash
# Boot the main (nflasha3) a6400 firmware on the sony-a6400 QEMU machine.
#
# Reproduces what the Sony bootloader leaves in RAM before entering the
# kernel (E0521; same contract as Oein/a5000-emulator docs/BOOT.md):
#   - kernel Image at PA 0x00108000, initrd at 0x00700000 (board code)
#   - "kemco"+cmdline block at 0x00103000 (board code, from -append);
#     the cmdline is the main-boot /setting/kemco.txt, with only
#     console=ttynull swapped for the ttyAM0 console
#   - /setting/Backup.bin copied to the PA in its header word 0
#     (backup.ko Backup_load=0 expects it already in RAM)
#   - /system/av-cam.bin copied to 0x035C6000 (liro.ko adrs=035c6000)
# The eMMC image is never written: every run uses a fresh qcow2 overlay.
#
# Env overrides: A6400_QEMU_DIR, A6400_QEMU_DATA_DIR (verified prepared inputs),
# RUN_DIR, AVCAM (legacy mode only: path to av-cam.bin),
# TIMEOUT (seconds, 0 = none), EXTRA (appended cmdline tokens),
# QEMU_EXTRA (extra QEMU options; default "-icount shift=0,sleep=on" ties
# virtual time to executed instructions, E0522; set QEMU_EXTRA= to disable),
# EVSEL_LATE_US (opt-in source-30 delay in us, E0557; unset = off),
# CAMPIG_ENABLE_READBACK=1 (opt-in CamPig IRQ enable register, E0566),
# SGC1_EVENT_A=1 (opt-in Sgc1 event A and IRQ 35, E0569).
# GATE_FORCE_PROBE=1 (declared stimulus cond. PROBE gate-force: one-shot patch
# of the av-cam step-26 producer cbz at PA 0x03874fd0 to a NOP; not a fix).
# ENTRY_FORCE_PROBE=1 (declared stimulus cond. PROBE entry-force: while the
# camera-control state is 240, force the first av-cam entry to r0 = 1; not a fix).
# LIVEVIEW_SOURCE (opt-in bars, ramp, none, or pgm:PATH, E0591).
# P2S_TRACE=1 (log the first 64 sensor-writer transfers).
# USB_VBUS_TEST=1 (enable the runtime usb-vbus QMP property).
# USB_CID_HIGH=1 (opt-in FUSB CID bit 1 high / peripheral idle, E0587).
# DWC3_GADGET (default on, E0590): CXD90045 DWC3 gadget at 0xF020C000 and IRQ 229.
# Set DWC3_GADGET=0 to disable it.
# WATCHDOG=1 (opt-in CXD4115 hardware watchdog at 0xF2402000, E0636; expiry follows
# QEMU -action watchdog=, add e.g. QEMU_EXTRA="-icount shift=0,sleep=on -action watchdog=pause").
# WORK_DMA=1 (opt-in work DMA copy channel at 0xF0102000, E0641).
# F30A_FILL=1 (opt-in 0xF30A memory-fill engine at 0xF30A1280, E0642).
# DDRB_CONTROL=1 (opt-in initialized DDR-B idle/active-mode sequence).
# DFS_CONTROL=1 (opt-in DFS clock-switch completion handshake).
# ZMA1_STARTUP=1 (opt-in captured ZMA1 startup interrupt, not JPEG decode).
# ZMA1_JPEG_FILL=1 (opt-in DGI neutral fill; requires ZMA1_STARTUP=1).
# LOWMEM_ABORT=1 (opt-in bus error on PA 0x000F8000 in the reserved first MiB, as on the
# camera, E0647; guest log under -d guest_errors).
# STRICT_BUS_ABORT=1 (opt-in synchronous external abort on PA 0x000F8000 and all unmapped
# physical address holes, E0647).
# PL310=1 (opt-in ARM PL310 L2 cache controller at 0xF8002000, E0416/E0655).
# CRCDC_MODEL=1 (opt-in stateful CRCDC clock block at 0xF2A31000 with self-clearing +0x30 bit 0, E0423).
# MISCREG=1 (opt-in CXD90045 EFUSE_CHIPID 0xF2915000, MODEREAD 0xF2915010, and ASV 0xF2927000, E0643).
# LINUX_BOOT=1 (opt-in standard ARMv7 Linux ATAGS/DTB boot contract on 1..4 cores with initrd at
# 0x10000000; optional DTB=/path/to/board.dtb passes -dtb "$DTB").
# REAR_CONTROLS=1 (route host keys to emulated rear camera controls).
# SHUTTER_EDGE_IRQ=1 (opt-in latched body shutter edges in the GIC).
# REAR_LCD_DIAGNOSTIC=1 (E0572 device select on the run's qcow2 overlay).
# PP_UNLOCK=1 (opt-in, default off, E9102): redirect the Soft Skin menu node to
# the Picture Profile node with one 4-byte .dynsym st_value write on the run's
# disposable overlay; PP_PARTITION_OFFSET overrides the rootfs partition start.
# SUBMICRO_PAGE (E0527 capture, empty for no micro) or SUBMICRO_FRAMES
# (the verified 384-byte A/C/B extract from that same capture).
# SUBMICRO_LENS_POWER=1 (opt-in lens rail status in slot B; no lens protocol).
# IDT_CAM (opt-in original sabin/idt_cam.bin, captured boot profile only).
# SD_IMAGE and SD_SHA256 (opt-in checked raw card; starts ejected).
# KERNEL and INITRD (Custom OS tests only): boot this kernel/initrd pair instead of the captured
# ones; the SHA-256 of each override is printed for the receipt. The captured inputs are still
# verified, so the override is one declared file each, not a replacement data dir. With INITRD set
# the cmdline's initrd=0x...,size token is rewritten to the file's real size.
# SERIAL (QEMU -serial backend for ttyAM0; default mon:stdio). The mod tools use a unix socket.
# SETTING_FILES ("HOST_PATH:NAME ..." added to /setting on the run's disposable overlay only;
# host paths must not contain spaces). Mod development stages the guard and request this way.
# The machine models 4 Cortex-A5 cores by default (E0522); -smp 1 still works.
set -euo pipefail
E="$(cd "$(dirname "$0")" && pwd)"

if [ "${ZMA1_JPEG_FILL:-0}" = 1 ] && [ "${ZMA1_STARTUP:-0}" != 1 ]; then
    echo "ZMA1_JPEG_FILL=1 requires ZMA1_STARTUP=1" >&2
    exit 1
fi

# Check the replay fixture before spending time staging firmware and an overlay.
# SUBMICRO_FRAMES_SHA declares a different 384-byte extract, e.g. the one
# tools.research.qemu_packaged_inputs derives from the public updater's WBI.
SUBMICRO_FRAME_SHA=${SUBMICRO_FRAMES_SHA:-6d5026e3932f3f46b47cc44a15008ced099dba46041deafbc1c058e4a0fa85ed}
SUBMICRO_RX="${SUBMICRO_FRAMES:-}"
if [ -n "$SUBMICRO_RX" ]; then
    if [ -n "${SUBMICRO_PAGE:-}" ]; then
        echo "Choose either SUBMICRO_PAGE or SUBMICRO_FRAMES, not both." >&2
        exit 1
    fi
    SUBMICRO_SHA=$SUBMICRO_FRAME_SHA
    SUBMICRO_SKIP=0
else
    SUBMICRO_RX="${SUBMICRO_PAGE-$E/../artifacts/qemu-a6400/e0526/camera-page-0x03443000.bin}"
    SUBMICRO_SHA=14fba4e607b0efdb76ced74e0463355a279146c96bbba0abec89776b8972fb54
    SUBMICRO_SKIP=$((0x3A4))
fi
if [ -n "$SUBMICRO_RX" ]; then
    if [ ! -f "$SUBMICRO_RX" ] || [ ! -r "$SUBMICRO_RX" ]; then
        printf 'E0527 sub-micro replay fixture is missing or unreadable: %s\n' "$SUBMICRO_RX" >&2
        echo 'Set SUBMICRO_PAGE to the captured page or SUBMICRO_FRAMES to its A/C/B extract.' >&2
        echo 'SUBMICRO_PAGE= skips replay for serial-only diagnostics; it does not boot the powered menu.' >&2
        exit 1
    fi
    if ! printf '%s  %s\n' "$SUBMICRO_SHA" "$SUBMICRO_RX" | sha256sum -c --status -; then
        printf 'E0527 sub-micro replay SHA-256 mismatch: %s\n' "$SUBMICRO_RX" >&2
        exit 1
    fi
fi

D="$E/data"
PREPARED=0
if [ -n "${A6400_QEMU_DATA_DIR:-}" ]; then
    D="$(cd "$A6400_QEMU_DATA_DIR" && pwd)"
    PREPARED=1
    (cd "$E/.." && python3 -m tools.research.qemu_boot_inputs verify --report-dir "$D")
fi
VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["qemu"]["version"])' "$E/../tools/toolchain.json")"
Q="${A6400_QEMU_DIR:-$HOME/sony-a6400-qemu-$VERSION}"
R="${RUN_DIR:-$HOME/a6400-runs}"
if [ "$PREPARED" = 1 ]; then
    if [ -n "${AVCAM:-}" ] && [ "$AVCAM" != "$D/av-cam.bin" ]; then
        echo "Prepared inputs do not permit an unverified AVCAM override." >&2
        exit 1
    fi
    AVCAM="$D/av-cam.bin"
    R="${RUN_DIR:-$HOME/a6400-runs/$(date -u +%Y%m%dT%H%M%SZ)-$$}"
    if [ -e "$R" ]; then
        echo "Refusing to overwrite an existing prepared-input run: $R" >&2
        exit 1
    fi
else
    AVCAM="${AVCAM:-$E/../firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked/av-cam.bin}"
fi
AVCAM_PA=0x035C6000
# Custom OS tests (build_full_initrd.py): boot a declared kernel/initrd pair
# instead of the captured ones. Every other prepared input stays verified, and
# the override hashes are printed for the receipt. With INITRD set, the
# cmdline's initrd=0x...,size token is rewritten to the file's real size so the
# kernel does not read past the loaded image.
KERNEL_OVERRIDE="${KERNEL:-}"
INITRD_OVERRIDE="${INITRD:-}"
DTB_OVERRIDE="${DTB:-}"
for f in "$KERNEL_OVERRIDE" "$INITRD_OVERRIDE" "$DTB_OVERRIDE"; do
    if [ -n "$f" ] && [ ! -f "$f" ]; then
        echo "KERNEL/INITRD/DTB override is missing: $f" >&2
        exit 1
    fi
done
if [ -n "$DTB_OVERRIDE" ] && [ "${LINUX_BOOT:-0}" != 1 ]; then
    echo "DTB requires LINUX_BOOT=1" >&2
    exit 1
fi
IDT_BOOT=()
if [ -n "${IDT_CAM:-}" ]; then
    if [ "$PREPARED" != 1 ] || ! printf '%s  %s\n' \
        dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209 "$AVCAM" \
        d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618 "$D/nflasha3-Image" \
        | sha256sum --check --status; then
        echo 'IDT_CAM requires the verified captured ILCE-6400 kernel and av-cam.' >&2
        exit 1
    fi
    if [ ! -f "$IDT_CAM" ] || [ "$(stat -Lc '%s' -- "$IDT_CAM")" != 176528 ] ||
        ! printf '%s  %s\n' \
        d21e2c0b6b87f49f70ccf451781093037db8093b5a378587ec0029a16dcaef92 "$IDT_CAM" \
        | sha256sum --check --status; then
        echo 'IDT_CAM is not the original 176528-byte sabin/idt_cam.bin.' >&2
        exit 1
    fi
fi
SD=()
if [ -n "${SD_IMAGE:-}${SD_SHA256:-}" ]; then
    SD_NODE=$(cd "$E/.." && python3 -m tools.research.qemu_media sd-blockdev \
        --image "${SD_IMAGE:-}" --sha256 "${SD_SHA256:-}" --inputs "$D" --run-dir "$R")
    SD=(-drive if=none,id=a6400-sd -blockdev "$SD_NODE")
fi
mkdir -p "$R"

# QEMU's option parser splits on ',' and the repo path may contain spaces,
# so stage the preload inputs under $R.
tar xOf "$D/setting.tar" ./Backup.bin > "$R/Backup.bin"
cp "$AVCAM" "$R/av-cam.bin"
BACKUP_PA=$(printf '0x%08X' "$(od -An -tu4 -N4 "$R/Backup.bin" | tr -d ' ')")
if [ -n "${IDT_CAM:-}" ]; then
    cp -- "$IDT_CAM" "$R/idt_cam.bin"
    if ! printf '%s  %s\n' \
        d21e2c0b6b87f49f70ccf451781093037db8093b5a378587ec0029a16dcaef92 "$R/idt_cam.bin" \
        | sha256sum --check --status; then
        echo 'IDT_CAM changed during staging.' >&2
        exit 1
    fi
    IDT_BOOT=(-device "loader,file=${R//,/,,}/idt_cam.bin,addr=0x20152000,force-raw=on")
fi

if [ "$PREPARED" = 1 ]; then
    CMD=$(cat "$D/serial-cmdline.txt")
else
    CMD=$(tar xOf "$D/setting.tar" ./kemco.txt | tr '\r\n' '  ' | tr -s ' ' \
          | sed 's/ \*[[:space:]]*$//; s/^kemco //; s/console=ttynull/console=ttyAM0,115200n8 amba2.console=1/')
fi

# Keep the initrd= token consistent with a declared initrd override (or LINUX_BOOT=1 @ 0x10000000).
INITRD_LOAD_PA=0x00700000
if [ "${LINUX_BOOT:-0}" = 1 ]; then
    INITRD_LOAD_PA=0x10000000
fi
if [ -n "$INITRD_OVERRIDE" ]; then
    ISZ=$(stat -Lc '%s' -- "$INITRD_OVERRIDE")
    CMD=$(printf '%s\n' "$CMD" | sed -E "s#initrd=0x[0-9a-fA-F]+,[^ ]+#initrd=${INITRD_LOAD_PA},0x$(printf '%08x' "$ISZ")#")
    if ! printf '%s\n' "$CMD" | grep -q "initrd=${INITRD_LOAD_PA},0x$(printf '%08x' "$ISZ")"; then
        echo "Could not size the initrd= token for the INITRD override." >&2
        exit 1
    fi
elif [ "${LINUX_BOOT:-0}" = 1 ] && [ -f "$D/nflasha3-initrd" ]; then
    ISZ=$(stat -Lc '%s' -- "$D/nflasha3-initrd")
    CMD=$(printf '%s\n' "$CMD" | sed -E "s#initrd=0x[0-9a-fA-F]+,[^ ]+#initrd=${INITRD_LOAD_PA},0x$(printf '%08x' "$ISZ")#")
fi

rm -f "$R/emmc-overlay.qcow2"
"$Q/build/qemu-img" create -q -f qcow2 -F raw -b "$D/emmc.img" "$R/emmc-overlay.qcow2"

# /setting/ulogio.bin (E0525): without it osal_ulogio.ko fails, nobody serves
# the ulogio queue 0x13A, its message pool fills and every task that logs
# (av-cam task 65, bootin, im.elf THREAD_INFRA*) blocks in vpool_alloc.
# The a6400 package ships no ulogio.bin; the ZV-E10 (same CXD90045 platform)
# copy is used as a stand-in [assumption]; its LOGB/PRTB buffers fall inside
# the a6400 cmdline's wbimem=0x500000@0x03085000.  Set ULOGIO= to skip.
# nflasha2 = user-area 0x00800000 (+8 MiB of eMMC boot partitions in the file).
ULOGIO="${ULOGIO-$E/../firmware/extracted_zve10_v203_packman/firmware.tar_unpacked/0800_appli/setting/ulogio.bin}"
if [ -n "$ULOGIO" ] || [ -n "${SETTING_FILES:-}" ]; then
    SET_OFF=$((0x1000000)); SET_LEN=$((0x1400000))
    dd if="$D/emmc.img" of="$R/setting-part.img" bs=1M skip=$((SET_OFF >> 20)) \
       count=$((SET_LEN >> 20)) status=none
    if [ -n "$ULOGIO" ]; then
        python3 "$E/tools/fat_add_file.py" --if-absent "$R/setting-part.img" "$ULOGIO" ulogio.bin >&2
    fi
    for item in ${SETTING_FILES:-}; do
        python3 "$E/tools/fat_add_file.py" "$R/setting-part.img" "${item%%:*}" "${item##*:}" >&2
    done
    # qemu-io splits the -c string on whitespace, including within paths.
    # Use a relative source name so a run directory with spaces works.
    QEMU_IO="$(cd "$Q/build" && pwd)/qemu-io"
    (cd "$R" && "$QEMU_IO" -f qcow2 \
        -c "write -q -s setting-part.img $SET_OFF $SET_LEN" emmc-overlay.qcow2)
fi

# E0572 is an opt-in firmware diagnostic applied only to the disposable overlay.
if [ "${REAR_LCD_DIAGNOSTIC:-0}" = 1 ]; then
    LCD_OFF=$(python3 "$E/tools/rear_lcd_overlay.py" "$D/emmc.img" "$R/rear-lcd-sector.bin")
    QEMU_IO="$(cd "$Q/build" && pwd)/qemu-io"
    (cd "$R" && "$QEMU_IO" -f qcow2 \
        -c "write -q -s rear-lcd-sector.bin $LCD_OFF 512" emmc-overlay.qcow2)
fi

# PP_UNLOCK=1 (opt-in, default off, E9102): point the Soft Skin menu node at the
# Picture Profile node by rewriting one .dynsym st_value inside the run's
# disposable overlay. The dump, emmc.img and /setting are never modified; a
# stock run is byte-for-byte unaffected. PP_PARTITION_OFFSET is the rootfs
# partition start in emmc.img (a6100 nflasha15: user area 0x800000 + 0x2d800000).
if [ "${PP_UNLOCK:-0}" = 1 ]; then
    PP_OFFSET_ARGS=(--partition-offset "${PP_PARTITION_OFFSET:-0x2e000000}")
    python3 -m tools.research.a6100_picture_profile unlock-offset \
        --emmc "$D/emmc.img" "${PP_OFFSET_ARGS[@]}" \
        --path /lib/CautionConfig.so --out "$R/pp-unlock.bin" \
        --json "$R/pp-unlock.json" >&2
    PP_OFF=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_offset"])' \
             "$R/pp-unlock.json")
    QEMU_IO="$(cd "$Q/build" && pwd)/qemu-io"
    (cd "$R" && "$QEMU_IO" -f qcow2 \
        -c "write -q -s pp-unlock.bin $PP_OFF 4" emmc-overlay.qcow2)
fi

# E0527: the board replays the captured micro->CPU slots A/C/B on SIO ch3.
MACHINE="${MACHINE_NAME:-sony-a6400}"
if [ -n "$SUBMICRO_RX" ]; then
    # A retained extract can itself be in RUN_DIR; never overwrite it.
    SUBMICRO_STAGED=$(mktemp -- "$R/submicro-frames.XXXXXX")
    dd if="$SUBMICRO_RX" bs=1 skip="$SUBMICRO_SKIP" count=384 status=none \
        > "$SUBMICRO_STAGED"
    # Check what will actually be replayed, even if the source changed after preflight.
    printf '%s  %s\n' "$SUBMICRO_FRAME_SHA" "$SUBMICRO_STAGED" | sha256sum -c --quiet - >&2
    MACHINE="$MACHINE,submicro-frames=$SUBMICRO_STAGED"
fi
if [ -n "${CAMERA_MODEL:-}" ]; then
    MACHINE="$MACHINE,camera-model=$CAMERA_MODEL"
fi
if [ "${PERIPHERALS:-0}" = 1 ]; then
    MACHINE="$MACHINE,peripherals=on"
fi
if [ "${TOUCHSCREEN:-0}" = 1 ]; then
    MACHINE="$MACHINE,touchscreen=on"
fi
if [ "${BCM4339:-0}" = 1 ]; then
    MACHINE="$MACHINE,bcm4339=on"
fi
if [ "${HDMI_CEC:-0}" = 1 ]; then
    MACHINE="$MACHINE,hdmi-cec=on"
fi
if [ "${AUDIO_IO:-0}" = 1 ]; then
    MACHINE="$MACHINE,audio-io=on"
fi
if [ -n "${AUDIO_INPUT:-}" ]; then
    MACHINE="$MACHINE,audio-input=$AUDIO_INPUT"
fi
if [ "${AUDIO_MIC_PLUGGED:-0}" = 1 ]; then
    MACHINE="$MACHINE,audio-mic-plugged=on"
fi
if [ -n "${EMOUNT_LENS:-}" ]; then
    MACHINE="$MACHINE,emount-lens=$EMOUNT_LENS"
fi
if [ "${SUBMICRO_LENS_POWER:-0}" = 1 ]; then
    MACHINE="$MACHINE,submicro-lens-power=on"
fi
if [ "${CAMPIG_ENABLE_READBACK:-0}" = 1 ]; then
    MACHINE="$MACHINE,campig-enable-readback=on"
fi
if [ "${SGC1_EVENT_A:-0}" = 1 ]; then
    MACHINE="$MACHINE,sgc1-event-a=on"
fi
# GATE_FORCE_PROBE=1 (declared stimulus cond. PROBE gate-force; off by default).
if [ "${GATE_FORCE_PROBE:-0}" = 1 ]; then
    MACHINE="$MACHINE,gate-force-probe=on"
fi
# ENTRY_FORCE_PROBE=1 (declared stimulus cond. PROBE entry-force; off by default).
if [ "${ENTRY_FORCE_PROBE:-0}" = 1 ]; then
    MACHINE="$MACHINE,entry-force-probe=on"
fi
if [ "${REAR_CONTROLS:-0}" = 1 ]; then
    MACHINE="$MACHINE,rear-controls=on"
fi
if [ "${SHUTTER_EDGE_IRQ:-0}" = 1 ]; then
    MACHINE="$MACHINE,shutter-edge-irq=on"
fi
# HOSTWIN=FILE: developer option, FILE mapped as shared RAM at PA 0x50000000 (16 MiB) for fast
# test-file transfer into a running guest (tools/warm.py, tools/hostwin_get.c); not a hardware model.
if [ -n "${HOSTWIN:-}" ]; then
    MACHINE="$MACHINE,hostwin=$HOSTWIN"
fi
if [ "${USB_VBUS_TEST:-0}" = 1 ]; then
    MACHINE="$MACHINE,usb-vbus-test=on"
fi
if [ "${USB_CID_HIGH:-0}" = 1 ]; then
    MACHINE="$MACHINE,usb-cid-high=on"
fi
if [ "${DWC3_GADGET:-1}" = 0 ]; then
    MACHINE="$MACHINE,dwc3-gadget=off"
fi
if [ "${PL330_DMA:-0}" = 1 ]; then
    MACHINE="$MACHINE,pl330-dma=on"
fi
if [ "${WATCHDOG:-0}" = 1 ]; then
    MACHINE="$MACHINE,watchdog=on"
fi
if [ "${WORK_DMA:-0}" = 1 ]; then
    MACHINE="$MACHINE,work-dma=on"
fi
if [ "${DDRB_CONTROL:-0}" = 1 ]; then
    MACHINE="$MACHINE,ddrb-control=on"
fi
if [ "${DFS_CONTROL:-0}" = 1 ]; then
    MACHINE="$MACHINE,dfs-control=on"
fi
if [ "${LOWMEM_ABORT:-0}" = 1 ]; then
    MACHINE="$MACHINE,lowmem-abort=on"
fi
if [ "${STRICT_BUS_ABORT:-0}" = 1 ]; then
    MACHINE="$MACHINE,strict-bus-abort=on"
fi
if [ "${PL310:-0}" = 1 ]; then
    MACHINE="$MACHINE,pl310=on"
fi
if [ "${CRCDC_MODEL:-0}" = 1 ]; then
    MACHINE="$MACHINE,crcdc-model=on"
fi
if [ "${MISCREG:-0}" = 1 ]; then
    MACHINE="$MACHINE,miscreg=on"
fi
if [ "${LINUX_BOOT:-0}" = 1 ]; then
    MACHINE="$MACHINE,linux-boot=on"
fi
if [ "${F30A_FILL:-0}" = 1 ]; then
    MACHINE="$MACHINE,f30a-fill=on"
fi
LIVEVIEW_ARGS=()
if [ -n "${LIVEVIEW_SOURCE:-}" ]; then
    LIVEVIEW_ARGS=(-M "liveview-source=${LIVEVIEW_SOURCE//,/,,}")
fi

# Evsel source order (E0557, opt-in): deliver timing-event source 30 (av-cam
# HS root 0x639242D8) EVSEL_LATE_US microseconds after source 29 (frame start
# 0x63924A38) instead of in the same pulse.  The real phase is unknown
# [assumption]; av-cam only needs 30 after 29 and before the first int_HS slot
# step (~4170 us).  Unset = off (the a5000 single-pulse model).
EVSEL=()
if [ -n "${EVSEL_LATE_US:-}" ]; then
    EVSEL=(-global cxd90014-evsel.late-src=30
           -global cxd90014-evsel.late-us="$EVSEL_LATE_US")
fi

P2S=()
if [ "${P2S_TRACE:-0}" = 1 ]; then
    P2S=(-global cxd90014-p2s.trace-transfers=on)
fi
SA=()
if [ "${ZMA1_STARTUP:-0}" = 1 ]; then
    SA=(-global cxd90014-sa.zma1-startup=on)
fi
if [ "${ZMA1_JPEG_FILL:-0}" = 1 ]; then
    SA+=(-global cxd90014-sa.zma1-jpeg-fill=on)
fi

echo "machine: $MACHINE" >&2
echo "Backup.bin -> $BACKUP_PA, av-cam.bin -> $AVCAM_PA, overlay $R/emmc-overlay.qcow2" >&2
KERNEL_PATH="${KERNEL_OVERRIDE:-$D/nflasha3-Image}"
INITRD_PATH="${INITRD_OVERRIDE:-$D/nflasha3-initrd}"
if [ -n "$KERNEL_OVERRIDE" ]; then
    printf 'kernel override: %s %s\n' \
        "$(sha256sum -- "$KERNEL_PATH" | cut -d' ' -f1)" "$KERNEL_PATH" >&2
fi
if [ -n "$INITRD_OVERRIDE" ]; then
    printf 'initrd override: %s %s (%s bytes)\n' \
        "$(sha256sum -- "$INITRD_PATH" | cut -d' ' -f1)" "$INITRD_PATH" \
        "$(stat -Lc '%s' -- "$INITRD_PATH")" >&2
fi
DTB_ARGS=()
if [ -n "$DTB_OVERRIDE" ]; then
    printf 'dtb override: %s %s (%s bytes)\n' \
        "$(sha256sum -- "$DTB_OVERRIDE" | cut -d' ' -f1)" "$DTB_OVERRIDE" \
        "$(stat -Lc '%s' -- "$DTB_OVERRIDE")" >&2
    DTB_ARGS=(-dtb "$DTB_OVERRIDE")
fi
T=()
if [ "${TIMEOUT:-0}" != 0 ]; then T=(timeout "$TIMEOUT"); fi
exec "${T[@]}" "$Q/build/qemu-system-arm" -M "$MACHINE" -m 1G -nographic -serial "${SERIAL:-mon:stdio}" \
  -kernel "$KERNEL_PATH" -initrd "$INITRD_PATH" "${DTB_ARGS[@]}" \
  -append "$CMD ${EXTRA:-}" \
  -drive if=sd,format=qcow2,file="$R/emmc-overlay.qcow2" \
  -device loader,file="$R/Backup.bin",addr="$BACKUP_PA",force-raw=on \
  -device loader,file="$R/av-cam.bin",addr="$AVCAM_PA",force-raw=on \
  "${IDT_BOOT[@]}" "${EVSEL[@]}" "${P2S[@]}" "${SA[@]}" "${LIVEVIEW_ARGS[@]}" "${SD[@]}" \
  ${QEMU_EXTRA--icount shift=0,sleep=on}
