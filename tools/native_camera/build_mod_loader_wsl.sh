#!/bin/bash
# Build the combined Mod Loader and tab artwork under WSL.
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=${1:-artifacts/hardware}
NC=tools/native_camera
CC=arm-linux-gnueabi-gcc
mkdir -p "$OUT"

common=(-DMTL_BUILTIN -mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding
        -fno-builtin -nostdlib -nostartfiles -fno-pic -fno-stack-protector
        -fomit-frame-pointer -Wall -Wextra)
"$CC" "${common[@]}" -Wno-unused-parameter \
    -Wl,--defsym,ML_CODE_ADDR=0x045e43a0 \
    -T "$NC/mod_loader.ld" "$NC/mod_loader.c" "$NC/mod_loader_trampoline.S" -o "$OUT/mod_loader.elf"
"$CC" "${common[@]}" -Werror -T "$NC/mods_tab_look.ld" "$NC/mods_tab_look.c" \
    "$NC/mods_tab_look_trampoline.S" -o "$OUT/mods_tab_look_builtin.elf"

for payload in "$OUT/mod_loader.elf" "$OUT/mods_tab_look_builtin.elf"; do
    disassembly=$(arm-linux-gnueabi-objdump -d "$payload")
    if [[ $disassembly =~ __aeabi_.*div ]]; then
        echo "Unexpected runtime division helper in $payload" >&2
        exit 1
    fi
done

"$CC" -static -O2 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=softfp \
    "$NC/native_mod_loader_guard.c" -o "$OUT/native_mod_loader_guard_arm"
python3 -m tools.research.build_mod_loader --elf "$OUT/mod_loader.elf" \
    --theme-elf "$OUT/mods_tab_look_builtin.elf" --out-dir "$OUT"
