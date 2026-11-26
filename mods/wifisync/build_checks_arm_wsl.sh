#!/bin/bash
# Build the Cortex-A5 check binary used by test_wifisync_unicorn.py.
# Usage: build_checks_arm_wsl.sh [OUTPUT_DIR]
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=${1:-artifacts/tests/wifisync-unicorn}
mkdir -p "$OUT"
MD=mods/wifisync
common=(-mthumb -mcpu=cortex-a5 -mfloat-abi=soft -mgeneral-regs-only -O2 -ffreestanding -fno-builtin
        -fno-common -nostdlib -fno-stack-protector -Wall -Wextra -Werror -I"$MD")
arm-linux-gnueabi-gcc "${common[@]}" -c "$MD/wifisync_core.c" -o "$OUT/wifisync_core.o"
arm-linux-gnueabi-gcc "${common[@]}" -c "$MD/wifisync_checks.c" -o "$OUT/wifisync_checks.o"
arm-linux-gnueabi-ld --build-id=none -Ttext=0x40000000 -e ws_run_checks \
  "$OUT/wifisync_core.o" "$OUT/wifisync_checks.o" -o "$OUT/wifisync_checks_arm.elf"
arm-linux-gnueabi-nm -n "$OUT/wifisync_checks_arm.elf" | grep -E "ws_run_checks|g_check" > "$OUT/wifisync_checks_arm.sym"
echo "built $OUT/wifisync_checks_arm.elf"
