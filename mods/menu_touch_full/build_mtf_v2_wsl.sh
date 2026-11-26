#!/bin/bash
# Build MenuTouchFull as an SMOD v2 (relocatable) mod for Mod Loader v2.
# Usage: build_mtf_v2_wsl.sh NEW_OUTPUT_DIR [--target ilce_6400|ilce_6400a]
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=$1
shift
TARGET="${MOD_TARGET:-ilce_6400}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --target=*) TARGET="${1#*=}"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
mkdir "$OUT"
NC=mods/menu_touch_full
common=(-mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin -nostdlib
        -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror
        -fpie -fvisibility=hidden)
target_defs=()
if [[ "$TARGET" == "ilce_6400a" ]]; then
  target_defs=(-DTARGET_ILCE6400A=1)
  HOOK_REL=0x0659ae98
  HOOK_PRS=0x0659afa4
  LAYOUT=tools/native_camera/mlv2/reference_layout_ilce6400a.txt
elif [[ "$TARGET" == "ilce_6400" ]]; then
  HOOK_REL=0x06309d48
  HOOK_PRS=0x06309e54
  LAYOUT=tools/native_camera/mlv2/reference_layout_ilce6400.txt
else
  echo "Unsupported target: $TARGET" >&2; exit 2
fi
arm-linux-gnueabi-gcc "${common[@]}" "${target_defs[@]}" -DMT_SMOD_V2 -DMT_MAX_TABS=7u -c "$NC/menu_touch_full.c" -o "$OUT/mtf.o"
arm-linux-gnueabi-gcc "${common[@]}" "${target_defs[@]}" -c "$NC/menu_touch_full_trampoline.S" -o "$OUT/mtf_tramp.o"
for o in "$OUT/mtf.o" "$OUT/mtf_tramp.o"; do
  if arm-linux-gnueabi-readelf -rW "$o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32|JUMP_SLOT|GLOB_DAT)"; then
    echo "$o has absolute or GOT relocations; it would not be position-independent" >&2; exit 1
  fi
done
arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld "$OUT/mtf.o" "$OUT/mtf_tramp.o" -o "$OUT/mtf.elf"
if arm-linux-gnueabi-objdump -d "$OUT/mtf.elf" | grep -qE "<(memcpy|memset|__aeabi_[a-z]*)>"; then
  echo "unexpected runtime helper in mtf.elf" >&2; exit 1
fi
arm-linux-gnueabi-objdump -d "$OUT/mtf.elf" > "$OUT/mtf.dis"
arm-linux-gnueabi-nm -n "$OUT/mtf.elf" > "$OUT/mtf.sym"
# Hooks: the same 12-byte prologues the v1 build patches (E0520). The trampoline replays them.
# The relocation table lets the same file run in another process layout of these libraries (QEMU).
python3 -m tools.research.pack_mod_v2 --elf "$OUT/mtf.elf" --name "Menu Touch Full" \
  --on mt_life --off mt_life --bss 632 \
  --hook "${HOOK_REL}:2de9f04788b04ff014080122:mt_full_release" \
  --hook "${HOOK_PRS}:2de9f04788b04ff014090122:mt_full_press" \
  --reloc-layout "$LAYOUT" \
  --out "$OUT/MenuTouchFull.mod" | tee "$OUT/pack.json"
