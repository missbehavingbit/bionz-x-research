#!/bin/bash
# Usage: build_mtf2_wsl.sh <out_elf> [extra -D flags]
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=$1; shift
NC=mods/menu_touch_full
arm-linux-gnueabi-gcc "$@" -mthumb -mcpu=cortex-a5 -mfloat-abi=softfp ${MTF2_OPT:--Os} -ffreestanding -fno-builtin -nostdlib -nostartfiles \
  -fno-pic -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror \
  -Wl,-Ttext=0x0700d708 -Wl,--build-id=none -Wl,-e,mt_full_release \
  $NC/menu_touch_full_trampoline.S $NC/menu_touch_full.c -o "$OUT"
