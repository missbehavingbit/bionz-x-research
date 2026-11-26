#!/bin/bash
# Build the ARRI gamma SMOD. Usage: build_wsl.sh NEW_OUTPUT_DIR PRESET(classic|logc3)
set -euo pipefail
cd "$(dirname "$0")/../../.."
OUT=$1; PRESET=${2:-classic}
mkdir "$OUT"
python3 mods/arri_isp/arri_gamma_mod/make_data.py "$PRESET" "$OUT/arri_gamma_data.h"
arm-linux-gnueabi-gcc -mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin \
  -nostdlib -nostartfiles -fno-pic -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror \
  -I"$OUT" -Wl,--build-id=none -T mods/arri_isp/arri_gamma_mod/arri_gamma_mod.ld \
  mods/arri_isp/arri_gamma_mod/arri_gamma_mod.c -o "$OUT/arri_gamma_mod.elf"
if arm-linux-gnueabi-objdump -d "$OUT/arri_gamma_mod.elf" | grep -q "__aeabi\|bl.*<memcpy\|bl.*<memset"; then
  echo "Unexpected runtime helper" >&2; exit 1; fi
python3 mods/arri_isp/arri_gamma_mod/pack.py "$OUT/arri_gamma_mod.elf" "$PRESET" "$OUT"
