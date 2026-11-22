#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
export PATH="$PWD/artifacts/toolchain/arm-linux-gnueabi/bin:$PATH"
TARGET=${1:-a6400}
OUT=${2:-/tmp/film-sim-live-$TARGET}
rm -rf "$OUT"
mkdir -p "$OUT"
LP=mods/film_sims/live_picker
common=(-mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin -nostdlib
        -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror
        -fpie -fvisibility=hidden)
case "$TARGET" in
  a6400)
    TFLAG="-DTARGET_A6400"
    HOOK_ADDR="0x0655a528"
    ;;
  a6100)
    TFLAG="-DTARGET_A6100"
    HOOK_ADDR="0x0655a528"
    ;;
  a6400-v1)
    TFLAG="-DTARGET_A6400_V1"
    HOOK_ADDR="0x0655a598"
    ;;
  *)
    echo "Unknown target '$TARGET' (expected a6400, a6100, or a6400-v1)" >&2
    exit 1
    ;;
esac
arm-linux-gnueabi-gcc "${common[@]}" "$TFLAG" -c "$LP/film_sim_live.c" -o "$OUT/live.o"
arm-linux-gnueabi-gcc "${common[@]}" "$TFLAG" -c "$LP/film_sim_live_trampoline.S" -o "$OUT/live_tramp.o"
for o in "$OUT/live.o" "$OUT/live_tramp.o"; do
  if arm-linux-gnueabi-readelf -rW "$o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32|JUMP_SLOT|GLOB_DAT)"; then
    echo "$o has absolute or GOT relocations" >&2; exit 1
  fi
done
arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld "$OUT/live.o" "$OUT/live_tramp.o" -o "$OUT/live.elf"
python3 -m tools.research.pack_mod_v2 --elf "$OUT/live.elf" --name "FilmSim Live" \
  --on live_life --off live_life --bss 32 --action \
  --hook "${HOOK_ADDR}:2de9f04184b0044602af0846:live_event_push" \
  --reloc-layout tools/native_camera/mlv2/reference_layout_ilce6400.txt \
  --data-symbol LIVE_NAMES --data-symbol LIVE_LUT_IDX \
  --data-symbol EC_GAMMA_STOCK --data-symbol FC_GAMMA_STOCK \
  --data-symbol COLOR_A0_STOCK --data-symbol COLOR_A1_STOCK --data-symbol COLOR_B2_STOCK \
  --data-symbol LIVE_EC --data-symbol LIVE_FC \
  --data-symbol LIVE_A0 --data-symbol LIVE_A1 --data-symbol LIVE_B2 --data-symbol LIVE_REFADR \
  --out "$OUT/FilmSimLive.mod" | tee "$OUT/pack.json"
