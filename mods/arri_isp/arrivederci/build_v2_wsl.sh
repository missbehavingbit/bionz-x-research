#!/bin/bash
# Build Arrivederci as an SMOD v2 (relocatable) mod for Mod Loader v2. Usage: build_v2_wsl.sh NEW_OUTPUT_DIR
set -euo pipefail
cd "$(dirname "$0")/../../.."
OUT=$1
mkdir "$OUT"
python3 mods/arri_isp/arrivederci/make_data.py "$OUT/arrivederci_data.h"
arm-linux-gnueabi-gcc -mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin \
  -nostdlib -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror \
  -fpie -fvisibility=hidden -I"$OUT" -c mods/arri_isp/arrivederci/arrivederci.c -o "$OUT/arrivederci.o"
if arm-linux-gnueabi-readelf -rW "$OUT/arrivederci.o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32)"; then
  echo "absolute relocations: not position-independent" >&2; exit 1
fi
arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld "$OUT/arrivederci.o" -o "$OUT/arrivederci.elf"
python3 -m tools.research.pack_mod_v2 --elf "$OUT/arrivederci.elf" --name Arrivederci \
  --on arri_life --off arri_life --out "$OUT/Arrivederci.mod"
