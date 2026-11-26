#!/bin/bash
# Build Classic Chrome (SMOD v2) for ILCE-6100 or ILCE-6400. Usage:
#   bash mods/film_sims/classic_chrome/build_wsl.sh OUTPUT_DIR [a6100|a6400]
set -euo pipefail
cd "$(dirname "$0")/../../.."
export PATH="$PWD/artifacts/toolchain/arm-linux-gnueabi/bin:$PATH"
OUT=$1
TARGET=${2:-a6100}
mkdir -p "$OUT"
python3 mods/film_sims/classic_chrome/make_data.py --target "$TARGET" "$OUT/classic_chrome_data.h"
arm-linux-gnueabi-gcc -mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin \
  -nostdlib -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror \
  -fpie -fvisibility=hidden -I"$OUT" -c mods/film_sims/classic_chrome/classic_chrome.c -o "$OUT/classic_chrome.o"
if arm-linux-gnueabi-readelf -rW "$OUT/classic_chrome.o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32)"; then
  echo "absolute relocations: not position-independent" >&2; exit 1
fi
arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld "$OUT/classic_chrome.o" -o "$OUT/classic_chrome.elf"
arm-linux-gnueabi-nm -n "$OUT/classic_chrome.elf" > "$OUT/classic_chrome.sym"
python3 -m tools.research.pack_mod_v2 --elf "$OUT/classic_chrome.elf" --name "Classic Chrome" \
  --on chrome_life --off chrome_life --action --out "$OUT/ClassicChrome.mod" | tee "$OUT/pack.json"
