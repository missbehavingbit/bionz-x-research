#!/bin/bash
# Build SilentShutterUnlock as an SMOD v2 (relocatable) mod for Mod Loader v2.
# Usage: build_v2_wsl.sh NEW_OUTPUT_DIR
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=$1
mkdir -p "$OUT"
DIR=mods/silent_shutter_unlock
common=(-mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin -nostdlib
        -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror
        -fpie -fvisibility=hidden)

arm-linux-gnueabi-gcc "${common[@]}" -c "$DIR/silent_shutter_unlock.c" -o "$OUT/silent.o"
arm-linux-gnueabi-gcc "${common[@]}" -c "$DIR/silent_shutter_unlock_trampoline.S" -o "$OUT/silent_tramp.o"

for o in "$OUT/silent.o" "$OUT/silent_tramp.o"; do
  if arm-linux-gnueabi-readelf -rW "$o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32|JUMP_SLOT|GLOB_DAT)"; then
    echo "$o has absolute or GOT relocations; it would not be position-independent" >&2
    exit 1
  fi
done

arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld \
  "$OUT/silent.o" "$OUT/silent_tramp.o" -o "$OUT/silent.elf"

if arm-linux-gnueabi-objdump -d "$OUT/silent.elf" | grep -qE "<(memcpy|memset|__aeabi_[a-z]*)>"; then
  echo "unexpected runtime helper in silent.elf" >&2
  exit 1
fi

arm-linux-gnueabi-objdump -d "$OUT/silent.elf" > "$OUT/silent.dis"
arm-linux-gnueabi-nm -n "$OUT/silent.elf" > "$OUT/silent.sym"

python3 -m tools.research.pack_mod_v2 \
  --elf "$OUT/silent.elf" \
  --name "Silent Unlock" \
  --on silent_on \
  --off silent_off \
  --bss 80 \
  --hook "0x069888a0:2de9f04104464cf2b60000af:silent_unlock_hook" \
  --data-symbol UNLOCK_FEAT_RANGES \
  --data-symbol SILENT_FACTORS \
  --data-symbol SILENT_MODE_FACTOR_RANGES \
  --reloc-layout tools/native_camera/mlv2/reference_layout_ilce6400.txt \
  --out "$OUT/SilentShutterUnlock.mod" | tee "$OUT/pack.json"
