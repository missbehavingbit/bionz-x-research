#!/bin/bash
# Build Arrivederci (SMOD v2) with the fitted Classic matrix. Usage:
#   build_v3_wsl.sh NEW_OUTPUT_DIR (MATRIX_FIT_JSON | --from-v5-mod) [--target ilce_6400|ilce_6400a]
# MATRIX_FIT_JSON comes from fit_matrix.py (run on Windows with a NumPy Python).
set -euo pipefail
cd "$(dirname "$0")/../../.."
OUT=$1
FIT=$2
shift 2
TARGET="${MOD_TARGET:-ilce_6400}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --target=*) TARGET="${1#*=}"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
target_defs=()
if [[ "$TARGET" == "ilce_6400a" ]]; then
  target_defs=(-DTARGET_ILCE6400A=1)
elif [[ "$TARGET" != "ilce_6400" ]]; then
  echo "Unsupported target: $TARGET" >&2; exit 2
fi
mkdir "$OUT"
if [[ "$FIT" == "--from-v5-mod" ]]; then
  python3 mods/arri_isp/arrivederci/make_data.py "$OUT/arrivederci_data.h" --from-v5-mod
else
  cp "$FIT" "$OUT/arrivederci-matrix-fit.json"
  python3 mods/arri_isp/arrivederci/make_data.py "$OUT/arrivederci_data.h" "$OUT/arrivederci-matrix-fit.json"
fi
arm-linux-gnueabi-gcc -mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin \
  -nostdlib -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror \
  -fpie -fvisibility=hidden "${target_defs[@]}" -I"$OUT" -c mods/arri_isp/arrivederci/arrivederci.c -o "$OUT/arrivederci.o"
if arm-linux-gnueabi-readelf -rW "$OUT/arrivederci.o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32)"; then
  echo "absolute relocations: not position-independent" >&2; exit 1
fi
arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld "$OUT/arrivederci.o" -o "$OUT/arrivederci.elf"
arm-linux-gnueabi-nm -n "$OUT/arrivederci.elf" > "$OUT/arrivederci.sym"
python3 -m tools.research.pack_mod_v2 --elf "$OUT/arrivederci.elf" --name Arrivederci \
  --on arri_life --off arri_life --out "$OUT/Arrivederci.mod" | tee "$OUT/pack.json"
