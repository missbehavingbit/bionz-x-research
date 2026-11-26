#!/usr/bin/env bash
# Build the freestanding gamma_poke helper (static, no libc). Usage: build_wsl.sh NEW_OUTPUT_DIR
set -euo pipefail
src=$(cd "$(dirname "$0")" && pwd)
mkdir "$1"; out=$(cd "$1" && pwd)
arm-linux-gnueabi-gcc -std=gnu11 -marm -march=armv7-a -mfloat-abi=softfp -Os -Wall -Wextra -Werror \
  -ffreestanding -fno-builtin -fno-stack-protector -fno-pic -fno-pie -nostdlib -static \
  -Wl,-e,_start,-z,max-page-size=4096,--build-id=none \
  -o "$out/gamma_poke" "$src/start.S" "$src/gamma_poke.c" -lgcc
arm-linux-gnueabi-readelf -h -l "$out/gamma_poke" | grep -E "Type|Machine|Entry|LOAD|INTERP" || true
sha256sum "$out/gamma_poke"
