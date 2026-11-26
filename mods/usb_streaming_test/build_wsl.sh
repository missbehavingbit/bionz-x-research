#!/bin/bash
# Build UsbStreamingTest.mod (--action) and uvc_mod_daemon for QEMU MODS-tab UVC testing.
# Usage: mods/usb_streaming_test/build_wsl.sh [OUT_DIR] [--target ilce_6400|ilce_6400a]
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT="artifacts/mods/usb-streaming-test-1"
if [[ $# -gt 0 && "$1" != --* ]]; then
    OUT="$1"
    shift
fi
TARGET="${MOD_TARGET:-ilce_6400}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --target) TARGET="$2"; shift 2 ;;
        --target=*) TARGET="${1#*=}"; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
mkdir -p "$OUT"

if [[ "$TARGET" == "ilce_6400a" ]]; then
    SYM_DIR="$OUT/usb_symbols_a6400a"
    rm -rf "$SYM_DIR"
    python3 -m tools.research.build_qemu_usb_stimulus --target ilce_6400a --output "$SYM_DIR"
elif [[ "$TARGET" == "ilce_6400" ]]; then
    SYM_DIR="artifacts/tests/qemu-usb-stimulus-e0596-captured-2"
    if [[ ! -f "$SYM_DIR/usb_symbols.h" ]]; then
        echo "Missing $SYM_DIR/usb_symbols.h" >&2
        exit 1
    fi
else
    echo "Unsupported target: $TARGET" >&2
    exit 2
fi

CC=arm-linux-gnueabi-gcc
common=(-mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin -nostdlib
        -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror
        -Wno-unused-parameter)

extra_flags=()
if [[ "${ALLOW_REAL_CAMERA:-0}" == "1" ]]; then
    extra_flags+=(-DUVC_MOD_ALLOW_REAL_CAMERA)
fi

"$CC" "${common[@]}" "${extra_flags[@]}" -fpie -fvisibility=hidden \
    -Itools/native_camera/mlv2 -I"$SYM_DIR" \
    -c mods/usb_streaming_test/usb_streaming_test.c -o "$OUT/usb_streaming_test.o"

if arm-linux-gnueabi-readelf -rW "$OUT/usb_streaming_test.o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32|JUMP_SLOT|GLOB_DAT)"; then
    echo "usb_streaming_test.o has non-PIC relocations" >&2
    exit 1
fi

arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld \
    "$OUT/usb_streaming_test.o" -o "$OUT/usb_streaming_test.elf"

python3 -m tools.research.pack_mod_v2 \
    --elf "$OUT/usb_streaming_test.elf" \
    --name "USB Streaming Test" \
    --on uvc_mod_on --off uvc_mod_on \
    --action \
    --out "$OUT/UsbStreamingTest.mod"

"$CC" -march=armv7-a -marm -mfloat-abi=softfp -O2 -ffreestanding -fno-builtin -nostdlib -static -fno-pie \
    -DUVC_USE_QUERY_DMA -DUVC_DAEMON_MODE -DUVC_TEST_TICKS=18000 \
    tools/native_camera/uvc_stream_feeder.c -lgcc -Wl,--defsym=raise=0 -o "$OUT/uvc_mod_daemon"
