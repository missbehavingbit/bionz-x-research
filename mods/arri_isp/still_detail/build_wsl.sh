#!/bin/bash
# Build StillDetail (SMOD v2) with a designed dcv_psf_table_still.bin.
# Usage: build_wsl.sh OUT_DIR STILL_DCV_BIN
set -euo pipefail
cd "$(dirname "$0")/../../.."
OUT=$1
BIN=$2
mkdir -p "$OUT"
cp "$BIN" "$OUT/dcv_psf_table_still.bin"
python3 mods/arri_isp/still_detail/make_data.py "$OUT/still_detail_data.h" "$OUT/dcv_psf_table_still.bin"
arm-linux-gnueabi-gcc -mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin \
  -nostdlib -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror \
  -fpie -fvisibility=hidden -I"$OUT" -c mods/arri_isp/still_detail/still_detail.c -o "$OUT/still_detail.o"
if arm-linux-gnueabi-readelf -rW "$OUT/still_detail.o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32)"; then
  echo "absolute relocations: not position-independent" >&2
  exit 1
fi
arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld "$OUT/still_detail.o" -o "$OUT/still_detail.elf"
arm-linux-gnueabi-nm -n "$OUT/still_detail.elf" > "$OUT/still_detail.sym"
python3 -m tools.research.pack_mod_v2 --elf "$OUT/still_detail.elf" --name StillDetail \
  --on still_detail_life --off still_detail_life --out "$OUT/StillDetail.mod" | tee "$OUT/pack.json"
