#!/bin/bash
# Build Mod Loader v2 (shim + core). Usage: build_wsl.sh NEW_OUTPUT_DIR
set -euo pipefail
cd "$(dirname "$0")/../../.."
OUT=$1
mkdir "$OUT"
D=tools/native_camera/mlv2
CC=arm-linux-gnueabi-gcc
common=(-mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin -nostdlib
        -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror
        -Wno-unused-parameter)
target_flags=()
if [[ "${MLV2_TARGET:-a6400}" == "a6100" ]]; then
    target_flags=(-DMLV2_TARGET_A6100=1)
    common+=("${target_flags[@]}")
fi

# Shim: linked at the reference cave address. A second link moves the shim by 0x1000 and the
# viewUnified2.so base by 0x10000; package.py diffs the two images to record the relocations.
"$CC" "${common[@]}" -fno-pic -Wl,--build-id=none -T "$D/shim.ld" \
    "$D/shim.c" "$D/mlv2_trampoline.S" -o "$OUT/shim.elf"
sed -e 's/0x05662800/0x05663800/' -e 's/0x05663000/0x05664000/' "$D/shim.ld" > "$OUT/shim-reloc.ld"
"$CC" "${common[@]}" -fno-pic -Wl,--build-id=none -T "$OUT/shim-reloc.ld" \
    -DMLV2_SHIM_ADDR=0x05663800u -DMLV2_VIEW_BASE=0x155f6000u \
    "$D/shim.c" "$D/mlv2_trampoline.S" -o "$OUT/shim-reloc.elf"

# Core: position-independent. Compile to an object first and reject absolute relocations.
"$CC" "${common[@]}" -fpie -fvisibility=hidden -c "$D/core.c" -o "$OUT/core.o"
if arm-linux-gnueabi-readelf -rW "$OUT/core.o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32|JUMP_SLOT|GLOB_DAT)"; then
    echo "core.o has absolute or GOT relocations; it would not be position-independent" >&2
    exit 1
fi
arm-linux-gnueabi-ld --build-id=none -T "$D/core.ld" "$OUT/core.o" -o "$OUT/core.elf" \
    "$(arm-linux-gnueabi-gcc -mthumb -print-libgcc-file-name)"

for f in "$OUT/shim.elf" "$OUT/core.elf"; do
    if arm-linux-gnueabi-objdump -d "$f" | grep -qE "<(memcpy|memset|__aeabi_[a-z]*div[a-z]*)>"; then
        echo "Unexpected runtime helper in $f" >&2
        exit 1
    fi
done
python3 tools/native_camera/mlv2/package.py "$OUT"

# On-camera guard (static ARM binary, runs as root over ptrace).
"$CC" -static -O2 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=softfp \
    "${target_flags[@]}" "$D/mlv2_guard.c" -o "$OUT/mlv2_guard_arm"

# The joint debugger package invokes this separate worker in the background.
# Only the matching archive can supply executable code to it.
if [[ "${CI_SKIP_SYSROOT:-0}" != "1" ]]; then
    mkdir "$OUT/companion" "$OUT/card"
    "$CC" -static -O2 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=softfp \
        "$D/card_read.c" -o "$OUT/companion/mlv2_card_read.elf"
    "$CC" -O2 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=softfp -nostdlib \
        -fPIC -shared -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 -U_TIME_BITS -D_TIME_BITS=32 \
        -Wl,-soname,mlv2_sd_boot_hook.so,-z,defs,-z,max-page-size=4096 \
        "$D/sd_boot_hook.c" tools/sony_sysroot/lib/libc.so -o "$OUT/companion/mlv2_sd_boot_hook.so"
    python3 -m tools.research.package_mlv2_sd --build "$OUT"
fi

# SMOD v2 test mod for the Unicorn harness (hook target lives only in the harness).
"$CC" "${common[@]}" -fpie -fvisibility=hidden -c "$D/test_mod.c" -o "$OUT/test_mod.o"
arm-linux-gnueabi-ld --build-id=none -T "$D/mod_v2.ld" "$OUT/test_mod.o" -o "$OUT/test_mod.elf"
python3 -m tools.research.pack_mod_v2 --elf "$OUT/test_mod.elf" --name "Test Mod" --on t_life --off t_life     --hook 0x33000100:2de9f04788b04ff014080122:t_hook --out "$OUT/TestMod.mod" > /dev/null
python3 -m tools.research.pack_mod_v2 --elf "$OUT/test_mod.elf" --name "USB Streaming Test" --on t_life --off t_life     --action --out "$OUT/ActionTest.mod" > /dev/null
