#!/bin/bash
# Build PhotonBin 6MP as a relocatable SMOD v2 mod with nested submenu settings (SMNU).
# Usage: build_wsl.sh OUTPUT_DIR [--target ilce_6400a]
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=$1
shift
TARGET="${MOD_TARGET:-ilce_6400a}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --target=*) TARGET="${1#*=}"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
if [[ "$TARGET" == "ilce_6100" ]]; then
  TARGET="a6100"
fi
if [[ "$TARGET" == "ilce_6400a" ]]; then
  TARGET_DEF="-DTARGET_ILCE6400A=1"
  HOOK_TARGET="0x06d3a61c"
  RELOC_LAYOUT="tools/native_camera/mlv2/reference_layout_ilce6400a.txt"
elif [[ "$TARGET" == "a6100" ]]; then
  TARGET_DEF="-DTARGET_A6100=1"
  HOOK_TARGET="0x06b04bcc"
  RELOC_LAYOUT="tools/native_camera/mlv2/reference_layout_ilce6400.txt"
else
  echo "Unsupported target: $TARGET (supported: ilce_6400a, a6100 / ilce_6100)" >&2
  exit 2
fi

mkdir -p "$OUT"
MD=mods/photon_bin_6mp
common=(-mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin -nostdlib
        -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror
        -fpie -fvisibility=hidden)

arm-linux-gnueabi-gcc "${common[@]}" "$TARGET_DEF" -c "$MD/photon_bin_6mp.c" -o "$OUT/photon_bin_6mp.o"
arm-linux-gnueabi-gcc "${common[@]}" "$TARGET_DEF" -c "$MD/photon_bin_6mp_trampoline.S" -o "$OUT/photon_bin_6mp_tramp.o"
for o in "$OUT/photon_bin_6mp.o" "$OUT/photon_bin_6mp_tramp.o"; do
  if arm-linux-gnueabi-readelf -rW "$o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32|JUMP_SLOT|GLOB_DAT)"; then
    echo "$o has absolute or GOT relocations; not position-independent" >&2
    exit 1
  fi
done

arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld \
  "$OUT/photon_bin_6mp.o" "$OUT/photon_bin_6mp_tramp.o" -o "$OUT/photon_bin_6mp.elf"

if arm-linux-gnueabi-objdump -d "$OUT/photon_bin_6mp.elf" | grep -qE "<(memcpy|memset|__aeabi_[a-z]*)>"; then
  echo "unexpected runtime helper in photon_bin_6mp.elf" >&2
  exit 1
fi

arm-linux-gnueabi-objdump -d "$OUT/photon_bin_6mp.elf" > "$OUT/photon_bin_6mp.dis"
arm-linux-gnueabi-nm -n "$OUT/photon_bin_6mp.elf" > "$OUT/photon_bin_6mp.sym"

# sizeof(struct pb_bss_state) = 40 + 4096 + 96768 + 6048 + 12096 + 6048 = 125096 -> 126976 (page-aligned)
python3 -m tools.research.pack_mod_v2 \
  --elf "$OUT/photon_bin_6mp.elf" \
  --name "PhotonBin 6MP" \
  --on pb_on --off pb_off \
  --bss 126976 \
  --submenu-bss-off 4 \
  --submenu-cfg pb_cfg \
  --hook "${HOOK_TARGET}:2de9f04f85b002af04460e46:pb_hook_make_raw_info_area" \
  --reloc-layout "$RELOC_LAYOUT" \
  --submenu-item "Binning Mode:0:4x4->2x2 Bayer|2x2 Mono Quad|Sensor Only" \
  --submenu-item "Photon Gain:0:+2 EV Sum (4x)|+1 EV Mid (2x)|+0 EV Avg (1x)" \
  --submenu-item "Spatial Kernel:0:[1,2,1] Phase|2x2 Box Sum" \
  --submenu-item "ARW Output:0:16-bit Uncomp|14-bit Uncomp|14-bit cRAW" \
  --submenu-item "FD Conv Gain:0:Force HCG|Stock Auto|Force LCG" \
  --submenu-item "14b 1H2H Read:0:Bulb + Silent|Stock Gate" \
  --out "$OUT/PhotonBin6MP.mod" | tee "$OUT/pack.json"
