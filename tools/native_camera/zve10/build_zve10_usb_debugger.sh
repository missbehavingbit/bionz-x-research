#!/bin/bash
# ==============================================================================
# Build script for Sony ZV-E10 Native USB Debugger (Daemon & Launcher)
#
# Targets:
#   1. zve10_usb_debugd.elf  - Background daemon speaking protocol v2 over
#                              /dev/usb/usb_extcmd (feature 0x23, opcode 0x7A)
#   2. usb_debug_launcher.so - ld.so.preload constructor hook
#
# Toolchain: arm-linux-gnueabi-gcc (ARMv7-A, softfp EABI)
# C Runtime: Sony CE Linux 8.2.7.6 glibc 2.11.2 (pulled from ZV-E10 nflasha7)
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

OUT_DIR="$REPO_ROOT/artifacts/native/zve10-usb-debugger"
mkdir -p "$OUT_DIR"

LIBC="$REPO_ROOT/artifacts/hardware/zv_e10-2.02-pull/libc.so.6"
if [ ! -f "$LIBC" ]; then
    echo "[-] ZV-E10 libc not found at $LIBC"
    echo "    Extract from artifacts/onboarding/ZV-E10-*.zip before building."
    exit 1
fi

CC="arm-linux-gnueabi-gcc"
if ! command -v "$CC" &> /dev/null; then
    echo "[-] Toolchain $CC not found in PATH."
    echo "    Install with: sudo apt-get install gcc-arm-linux-gnueabi"
    echo "    (Or use the prebuilt binaries qualified via src/a6400/targets/usb_debugger_compat.py)"
    exit 1
fi

CFLAGS="-std=gnu11 -march=armv7-a -mfloat-abi=softfp -O2 -Wall -Wextra -Werror \
        -nostdlib -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 -U_TIME_BITS -D_TIME_BITS=32 \
        -U_FILE_OFFSET_BITS -D_FILE_OFFSET_BITS=32"

echo "[*] Building zve10_usb_debugd.elf..."
$CC $CFLAGS -fno-stack-protector \
    -Wl,-e,_start,-z,defs,-z,max-page-size=4096,--dynamic-linker=/lib/ld-linux.so.3 \
    -o "$OUT_DIR/zve10_usb_debugd.elf" \
    "$REPO_ROOT/tools/native_camera/debug_start.S" \
    "$SCRIPT_DIR/zve10_usb_debugd.c" \
    "$LIBC" -lgcc

echo "[*] Building usb_debug_launcher.so..."
$CC $CFLAGS -fPIC -shared \
    -Wl,-soname,usb_debug_launcher.so,-z,defs,-z,max-page-size=4096 \
    -o "$OUT_DIR/usb_debug_launcher.so" \
    "$REPO_ROOT/tools/native_camera/hook/usb_debug_launcher.c" \
    "$LIBC"

echo "[+] Built successfully in $OUT_DIR:"
ls -la "$OUT_DIR"
sha256sum "$OUT_DIR"/*
