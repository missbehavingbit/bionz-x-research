#!/bin/bash
# Build OpenGate_5400_Test (+ optional OpenGateSensor / OpenGate32) as SMOD v2 mods.
# Usage: build_v2_wsl.sh OUTPUT_DIR [--target ilce_6400|ilce_6400a] [--all-stages]
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=$1
shift
TARGET="${MOD_TARGET:-ilce_6400}"
ALL_STAGES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --target=*) TARGET="${1#*=}"; shift ;;
    --all-stages) ALL_STAGES=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
target_defs=()
if [[ "$TARGET" == "ilce_6400a" ]]; then
  target_defs=(-DTARGET_ILCE6400A=1)
elif [[ "$TARGET" != "ilce_6400" ]]; then
  echo "Unsupported target: $TARGET" >&2; exit 2
fi
mkdir -p "$OUT"
common=(-mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin
        -nostdlib -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror
        -fpie -fvisibility=hidden)
arm-linux-gnueabi-gcc "${common[@]}" "${target_defs[@]}" \
  -c mods/opengate_5400_test/opengate_5400_test.c -o "$OUT/opengate_5400_test.o"
if arm-linux-gnueabi-readelf -rW "$OUT/opengate_5400_test.o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32)"; then
  echo "absolute relocations: not position-independent" >&2; exit 1
fi
arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld "$OUT/opengate_5400_test.o" -o "$OUT/opengate_5400_test.elf"
python3 -m tools.research.pack_mod_v2 --elf "$OUT/opengate_5400_test.elf" --name OpenGate_5400_Test \
  --on og5400_on --off og5400_off --out "$OUT/OpenGate_5400_Test.mod"

if [[ "$ALL_STAGES" == "1" ]]; then
  arm-linux-gnueabi-gcc "${common[@]}" "${target_defs[@]}" -DOG_SENSOR_ONLY=1 \
    -c mods/opengate_5400_test/opengate_5400_test.c -o "$OUT/opengate_sensor.o"
  if arm-linux-gnueabi-readelf -rW "$OUT/opengate_sensor.o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32)"; then
    echo "absolute relocations in opengate_sensor.o" >&2; exit 1
  fi
  arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld "$OUT/opengate_sensor.o" -o "$OUT/opengate_sensor.elf"
  python3 -m tools.research.pack_mod_v2 --elf "$OUT/opengate_sensor.elf" --name "Open Gate Sensor" \
    --on og5400_on --off og5400_off --out "$OUT/OpenGateSensor.mod"

  arm-linux-gnueabi-gcc "${common[@]}" "${target_defs[@]}" -DOG_ENABLE_LIBMPR=1 \
    -c mods/opengate_5400_test/opengate_5400_test.c -o "$OUT/opengate_32.o"
  if arm-linux-gnueabi-readelf -rW "$OUT/opengate_32.o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32)"; then
    echo "absolute relocations in opengate_32.o" >&2; exit 1
  fi
  arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld "$OUT/opengate_32.o" -o "$OUT/opengate_32.elf"
  python3 -m tools.research.pack_mod_v2 --elf "$OUT/opengate_32.elf" --name "Open Gate 3:2" \
    --on og5400_on --off og5400_off --out "$OUT/OpenGate32.mod"
fi
