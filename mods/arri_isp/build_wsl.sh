#!/usr/bin/env bash
# Build and test the staging component offline. This target is a relocatable object.
set -euo pipefail
if [[ $# -ne 2 ]]; then
    echo "Usage: $0 NEW_OUTPUT_DIRECTORY PRESET_DIRECTORY" >&2
    exit 2
fi
source_dir=$(cd "$(dirname "$0")" && pwd)
preset_dir=$(cd "$2" && pwd)
mkdir "$1"
out=$(cd "$1" && pwd)
common=(-std=c11 -Wall -Wextra -Werror -Os)
cc "${common[@]}" "$source_dir/arri_stage.c" "$source_dir/test_arri_stage.c" -o "$out/test_arri_stage"
"$out/test_arri_stage" "$preset_dir/arri-classic-rec709.a6lt" "$preset_dir/arri-logc3-awg3.a6lt"
arm-linux-gnueabi-gcc "${common[@]}" -mcpu=cortex-a5 -mthumb -mfloat-abi=softfp \
    -ffreestanding -fno-builtin -fno-stack-protector \
    -c "$source_dir/arri_stage.c" -o "$out/arri_stage.arm.o"
arm-linux-gnueabi-readelf -h "$out/arri_stage.arm.o"
if [[ $(arm-linux-gnueabi-nm -u "$out/arri_stage.arm.o") ]]; then
    echo "Unexpected runtime dependencies" >&2
    exit 1
fi
