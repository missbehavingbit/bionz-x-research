#!/bin/bash
# Build Arrivederci.mod. Usage: build_wsl.sh NEW_OUTPUT_DIR
set -euo pipefail
cd "$(dirname "$0")/../../.."
OUT=$1
mkdir "$OUT"
python3 mods/arri_isp/arrivederci/make_data.py "$OUT/arrivederci_data.h"
arm-linux-gnueabi-gcc -mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin \
  -nostdlib -nostartfiles -fno-pic -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror \
  -I"$OUT" -Wl,--build-id=none -T mods/arri_isp/arrivederci/arrivederci.ld \
  mods/arri_isp/arrivederci/arrivederci.c -o "$OUT/arrivederci.elf"
if arm-linux-gnueabi-objdump -d "$OUT/arrivederci.elf" | grep -q "__aeabi\|<memcpy\|<memset"; then
  echo "Unexpected runtime helper" >&2
  exit 1
fi
python3 mods/arri_isp/arrivederci/pack.py "$OUT/arrivederci.elf" "$OUT"
