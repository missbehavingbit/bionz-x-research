#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
export PATH="$PWD/artifacts/toolchain/arm-linux-gnueabi/bin:$PATH"
OUT=${1:-"$PWD/artifacts/a6100-film-sims/film_texture"}
GOUT=${2:-"$(dirname "$OUT")/luma_grain"}
mkdir -p "$OUT"
arm-linux-gnueabi-gcc -mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin \
  -nostdlib -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror \
  -fpie -fvisibility=hidden -c mods/film_sims/film_texture/film_texture.c -o "$OUT/film_texture.o"
if arm-linux-gnueabi-readelf -rW "$OUT/film_texture.o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32)"; then
  echo "absolute relocations: not position-independent" >&2; exit 1
fi
arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld "$OUT/film_texture.o" -o "$OUT/film_texture.elf"
python3 -m tools.research.pack_mod_v2 --elf "$OUT/film_texture.elf" --name "Film Texture" \
  --on texture_life --off texture_life --out "$OUT/FilmTexture.mod"

mkdir -p "$GOUT"
arm-linux-gnueabi-gcc -mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin \
  -nostdlib -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror \
  -fpie -fvisibility=hidden -DSABIN_DIR='"/tmp_bt/mods/grain"' -DLOG_PATH='"/tmp_bt/mods/luma_grain.log"' \
  -DLOG_TAG='"luma_grain"' -c mods/film_sims/film_texture/film_texture.c -o "$GOUT/luma_grain.o"
if arm-linux-gnueabi-readelf -rW "$GOUT/luma_grain.o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32)"; then
  echo "absolute relocations: not position-independent" >&2; exit 1
fi
arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld "$GOUT/luma_grain.o" -o "$GOUT/luma_grain.elf"
python3 -m tools.research.pack_mod_v2 --elf "$GOUT/luma_grain.elf" --name "Luma Grain" \
  --on texture_life --off texture_life --out "$GOUT/LumaGrain.mod"
