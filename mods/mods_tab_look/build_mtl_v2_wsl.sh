#!/bin/bash
# Build ModsTabLook (E0522 Mods tab artwork) as a relocatable SMOD v2 mod for Mod Loader v2.
# Usage: build_mtl_v2_wsl.sh NEW_OUTPUT_DIR [--target ilce_6400|ilce_6400a]
# Same C source and trampoline as the v1 standalone build; position-independent, the icon cache
# stays in the code mapping (.text.mtl_state), the hook is the alcor index lookup.
# The relocation table moves the hook and the two libObj return addresses for any process layout.
# --auto-on: the core switches it ON the first time it sees the file (the Mods tab then shows the
# MODS icon instead of the sixth tab's icon); it can still be switched OFF in the Mods tab.
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
NC=tools/native_camera
common=(-mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin -nostdlib
        -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror
        -fpie -fvisibility=hidden)
target_defs=()
if [[ "$TARGET" == "ilce_6400a" ]]; then
  target_defs=(-DTARGET_ILCE6400A=1)
  HOOK_ADDR=0x067a862e
  LAYOUT=tools/native_camera/mlv2/reference_layout_ilce6400a.txt
elif [[ "$TARGET" == "ilce_6400" ]]; then
  HOOK_ADDR=0x064b54b6
  LAYOUT=tools/native_camera/mlv2/reference_layout_ilce6400.txt
else
  echo "Unsupported target: $TARGET" >&2; exit 2
fi
arm-linux-gnueabi-gcc "${common[@]}" "${target_defs[@]}" -c "$NC/mods_tab_look.c" -o "$OUT/mtl.o"
arm-linux-gnueabi-gcc "${common[@]}" "${target_defs[@]}" -c "$NC/mods_tab_look_trampoline.S" -o "$OUT/mtl_tramp.o"
for o in "$OUT/mtl.o" "$OUT/mtl_tramp.o"; do
  if arm-linux-gnueabi-readelf -rW "$o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32|JUMP_SLOT|GLOB_DAT)"; then
    echo "$o has absolute or GOT relocations; it would not be position-independent" >&2; exit 1
  fi
done
arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld "$OUT/mtl.o" "$OUT/mtl_tramp.o" -o "$OUT/mtl.elf"
if arm-linux-gnueabi-objdump -d "$OUT/mtl.elf" | grep -qE "<(memcpy|memset|__aeabi_[a-z]*)>"; then
  echo "unexpected runtime helper in mtl.elf" >&2; exit 1
fi
arm-linux-gnueabi-objdump -d "$OUT/mtl.elf" > "$OUT/mtl.dis"
arm-linux-gnueabi-nm -n "$OUT/mtl.elf" > "$OUT/mtl.sym"
python3 -m tools.research.pack_mod_v2 --elf "$OUT/mtl.elf" --name "Mods Tab Look" --auto-on --data-symbol mtl_icon_rle \
  --hook "${HOOK_ADDR}:f8b500af0d460446fff7e2ff:mtl_hook" \
  --reloc-layout "$LAYOUT" \
  --out "$OUT/ModsTabLook.mod" | tee "$OUT/pack.json"
