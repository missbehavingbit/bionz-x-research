#!/bin/bash
# Build Mod Loader v2 (shim + core). Usage: build_wsl.sh NEW_OUTPUT_DIR [--target ilce_6400|ilce_6400a|a6100]
set -euo pipefail
cd "$(dirname "$0")/../../.."
OUT=$1
shift
TARGET="${MLV2_TARGET:-ilce_6400}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --target) TARGET="$2"; shift 2 ;;
        --target=*) TARGET="${1#*=}"; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
if [[ "$TARGET" == "a6400" ]]; then
    TARGET="ilce_6400"
elif [[ "$TARGET" == "ilce_6100" ]]; then
    TARGET="a6100"
elif [[ "$TARGET" == "ilce_7c" ]]; then
    TARGET="a7c"
fi
mkdir "$OUT"
D=tools/native_camera/mlv2
CC=arm-linux-gnueabi-gcc
common=(-mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin -nostdlib
        -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror
        -Wno-unused-parameter)
target_defs=()
if [[ "$TARGET" == "ilce_6400a" ]]; then
    target_defs=(-DMLV2_TARGET_ILCE6400A=1)
    SHIM_LD="$OUT/shim-target.ld"
    sed -e 's/0x05662800/0x05d76800/' -e 's/0x05663000/0x05d77000/' "$D/shim.ld" > "$SHIM_LD"
    sed -e 's/0x05662800/0x05d77800/' -e 's/0x05663000/0x05d78000/' "$D/shim.ld" > "$OUT/shim-reloc.ld"
    RELOC_SHIM=0x05d77800u
    RELOC_VIEW=0x12f56000u
elif [[ "$TARGET" == "a6100" ]]; then
    target_defs=(-DMLV2_TARGET_A6100=1)
    SHIM_LD="$D/shim.ld"
    sed -e 's/0x05662800/0x05663800/' -e 's/0x05663000/0x05664000/' "$D/shim.ld" > "$OUT/shim-reloc.ld"
    RELOC_SHIM=0x05663800u
    RELOC_VIEW=0x155f6000u
elif [[ "$TARGET" == "a7c" ]]; then
    target_defs=(-DMLV2_TARGET_A7C=1)
    SHIM_LD="$D/shim.ld"
    sed -e 's/0x05662800/0x05663800/' -e 's/0x05663000/0x05664000/' "$D/shim.ld" > "$OUT/shim-reloc.ld"
    RELOC_SHIM=0x05663800u
    RELOC_VIEW=0x155f6000u
elif [[ "$TARGET" == "ilce_6400" ]]; then
    SHIM_LD="$D/shim.ld"
    sed -e 's/0x05662800/0x05663800/' -e 's/0x05663000/0x05664000/' "$D/shim.ld" > "$OUT/shim-reloc.ld"
    RELOC_SHIM=0x05663800u
    RELOC_VIEW=0x155f6000u
else
    echo "Unsupported target: $TARGET" >&2
    exit 2
fi

# Shim: linked at the reference cave address. A second link moves the shim by 0x1000 and the
# viewUnified2.so base by 0x10000; package.py diffs the two images to record the relocations.
"$CC" "${common[@]}" "${target_defs[@]}" -fno-pic -Wl,--build-id=none -T "$SHIM_LD" \
    "$D/shim.c" "$D/mlv2_trampoline.S" -o "$OUT/shim.elf"
"$CC" "${common[@]}" "${target_defs[@]}" -fno-pic -Wl,--build-id=none -T "$OUT/shim-reloc.ld" \
    -DMLV2_SHIM_ADDR="$RELOC_SHIM" -DMLV2_VIEW_BASE="$RELOC_VIEW" \
    "$D/shim.c" "$D/mlv2_trampoline.S" -o "$OUT/shim-reloc.elf"

# Core: position-independent. Compile to an object first and reject absolute relocations.
"$CC" "${common[@]}" "${target_defs[@]}" -fpie -fvisibility=hidden -c "$D/core.c" -o "$OUT/core.o"
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
python3 tools/native_camera/mlv2/package.py "$OUT" --target "$TARGET"

# On-camera guard (static ARM binary, runs as root over ptrace).
"$CC" -static -O2 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=softfp \
    "${target_defs[@]}" "$D/mlv2_guard.c" -o "$OUT/mlv2_guard_arm"

# The joint debugger package invokes this separate worker in the background.
# Only the matching archive can supply executable code to it.
if [[ "${CI_SKIP_SYSROOT:-0}" != "1" && "$TARGET" != "a6100" && "$TARGET" != "a7c" ]]; then
    if [[ "$TARGET" == "ilce_6400" || -f "mods/ilce_6400a/MenuTouchFull.mod" ]]; then
        mkdir "$OUT/companion" "$OUT/card"
        "$CC" -DMLV2_BARE_ARM -nostdlib -static -O2 -ffreestanding -fno-builtin \
            -fno-tree-loop-distribute-patterns -Wall -Wextra -Werror \
            -march=armv7-a -mfloat-abi=softfp \
            -Wl,-e,_start,--build-id=none,-z,max-page-size=4096 \
            "$D/card_read.c" -lgcc -o "$OUT/companion/mlv2_card_read.elf"
        arm-linux-gnueabi-strip -s "$OUT/companion/mlv2_card_read.elf"
        "$CC" -O2 -ffreestanding -fno-builtin -fno-tree-loop-distribute-patterns \
            -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=softfp -nostdlib -fPIC -shared \
            "${target_defs[@]}" \
            -Wl,-soname,mlv2_sd_boot_hook.so,-Bsymbolic,-z,defs,-z,noexecstack,--build-id=none,-z,max-page-size=4096,--hash-style=both \
            "$D/sd_boot_hook.c" -lgcc -o "$OUT/companion/mlv2_sd_boot_hook.so"
        arm-linux-gnueabi-strip -s "$OUT/companion/mlv2_sd_boot_hook.so"
        A6400A_LIB_DIR="firmware/extracted_a6400a_v101/firmware.tar_unpacked/0700_part_image/dev/nflasha7_unpacked/lib"
        if [[ "$TARGET" == "ilce_6400a" && -f "$A6400A_LIB_DIR/libc-2.11.2.so" ]]; then
            "$CC" -O2 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=softfp \
                -nostdlib -fno-stack-protector \
                -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 -U_TIME_BITS -D_TIME_BITS=32 \
                -U_FILE_OFFSET_BITS -D_FILE_OFFSET_BITS=32 \
                "${target_defs[@]}" \
                -Wl,-e,_start,-z,defs,-z,max-page-size=4096,--dynamic-linker=/lib/ld-linux.so.3,--build-id=none,--hash-style=both \
                tools/native_camera/probe_start.S "$D/mlv2_guard.c" \
                "$A6400A_LIB_DIR/libc-2.11.2.so" -lgcc \
                -o "$OUT/companion/mlv2_guard_compact.elf"
            arm-linux-gnueabi-strip -s "$OUT/companion/mlv2_guard_compact.elf"
        fi
        python3 -m tools.research.package_mlv2_sd --build "$OUT" --target "$TARGET"
    fi
fi

# SMOD v2 test mod for the Unicorn harness (hook target lives only in the harness).
"$CC" "${common[@]}" -fpie -fvisibility=hidden -c "$D/test_mod.c" -o "$OUT/test_mod.o"
arm-linux-gnueabi-ld --build-id=none -T "$D/mod_v2.ld" "$OUT/test_mod.o" -o "$OUT/test_mod.elf"
python3 -m tools.research.pack_mod_v2 --elf "$OUT/test_mod.elf" --name "Test Mod" --on t_life --off t_life     --hook 0x33000100:2de9f04788b04ff014080122:t_hook --out "$OUT/TestMod.mod" > /dev/null
python3 -m tools.research.pack_mod_v2 --elf "$OUT/test_mod.elf" --name "USB Streaming Test" --on t_life --off t_life     --action --out "$OUT/ActionTest.mod" > /dev/null
python3 -m tools.research.pack_mod_v2 --elf "$OUT/test_mod.elf" --name "Submenu Test" --on t_life --off t_life \
    --bss 16 --submenu-bss-off 4 --submenu-cfg t_cfg \
    --hook 0x33000100:2de9f04788b04ff014080122:t_hook \
    --submenu-item "Binning Mode:0:2x2 Bayer|2x2 Mono|Sensor Only" \
    --submenu-item "Photon Gain:0:+2 EV (4x)|+1 EV (2x)|+0 EV (1x)" \
    --submenu-item "Spatial Kernel:1:Binomial|Box Sum" \
    --submenu-item "ARW Output:0:16-bit Uncomp|14-bit cRAW" \
    --submenu-item "FD Conv Gain:0:Force HCG|Stock Auto|Force LCG" \
    --out "$OUT/SubmenuTest.mod" > /dev/null
