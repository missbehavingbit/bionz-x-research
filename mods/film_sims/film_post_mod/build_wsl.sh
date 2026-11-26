#!/usr/bin/env bash
set -euo pipefail

# Build FilmPost.mod (SMOD v2 + SMNU + RLC1) inside WSL for a Sony BIONZ X target.
#
# Usage:
#   bash mods/film_sims/film_post_mod/build_wsl.sh [out_dir] [--target ilce_6400|ilce_6400a]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

TARGET="ilce_6400"
OUT_DIR_ARG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target)
            TARGET="${2:?missing value for --target}"
            shift 2
            ;;
        --target=*)
            TARGET="${1#--target=}"
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [out_dir] [--target ilce_6400|ilce_6400a]"
            exit 0
            ;;
        *)
            if [[ -z "$OUT_DIR_ARG" ]]; then
                OUT_DIR_ARG="$1"
                shift
            else
                echo "Unexpected argument: $1" >&2
                exit 2
            fi
            ;;
    esac
done

case "$TARGET" in
    ilce_6400)
        HOOK_SPEC="0x079d0a1a:b0b582b000af044608463a60:fp_hook_write_still"
        HOOK_ENC_SPEC="0x079d099c:b0b50c46054600af03c9a269:fp_hook_encode_still"
        RELOC_LAYOUT="$REPO_ROOT/tools/native_camera/mlv2/reference_layout_ilce6400.txt"
        TARGET_CFLAGS=""
        FINAL_COPY="$SCRIPT_DIR/FilmPost.mod"
        ;;
    ilce_6400a)
        HOOK_SPEC="0x07e3fbe6:b0b582b000af044608463a60:fp_hook_write_still"
        HOOK_ENC_SPEC="0x07e3fb68:b0b50c46054600af03c9a269:fp_hook_encode_still"
        RELOC_LAYOUT="$REPO_ROOT/tools/native_camera/mlv2/reference_layout_ilce6400a.txt"
        TARGET_CFLAGS="-DTARGET_ILCE6400A=1"
        FINAL_COPY="$SCRIPT_DIR/FilmPost_ilce6400a.mod"
        ;;
    *)
        echo "Unsupported target '$TARGET' (expected ilce_6400 or ilce_6400a)" >&2
        exit 2
        ;;
esac

if [[ -n "$OUT_DIR_ARG" ]]; then
    if [[ "$OUT_DIR_ARG" = /* ]]; then
        OUT_DIR="$OUT_DIR_ARG"
    else
        OUT_DIR="$REPO_ROOT/$OUT_DIR_ARG"
    fi
else
    OUT_DIR="$REPO_ROOT/artifacts/mods/$TARGET/film_post"
fi

mkdir -p "$OUT_DIR"
mkdir -p "$(dirname "$FINAL_COPY")"

for tool in arm-linux-gnueabi-gcc arm-linux-gnueabi-ld arm-linux-gnueabi-objdump arm-linux-gnueabi-readelf python3; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "Missing required tool: $tool" >&2
        exit 1
    }
done

echo "[1/5] Generating LUT header for $TARGET..."
python3 "$SCRIPT_DIR/make_luts.py"

CFLAGS=(
    -mthumb
    -mcpu=cortex-a5
    -mno-unaligned-access
    -mfpu=vfpv3-d16
    -mfloat-abi=softfp
    -O2
    -fno-tree-vectorize
    -ffreestanding
    -fno-builtin
    -nostdlib
    -nostartfiles
    -fno-stack-protector
    -fomit-frame-pointer
    -Wall
    -Wextra
    -Werror
    -fpie
    -fvisibility=hidden
)

if [[ -n "$TARGET_CFLAGS" ]]; then
    CFLAGS+=("$TARGET_CFLAGS")
fi

echo "[2/5] Compiling Thumb-2 objects..."
arm-linux-gnueabi-gcc "${CFLAGS[@]}" -c "$SCRIPT_DIR/film_post_mod.c" -o "$OUT_DIR/film_post_mod.o"
arm-linux-gnueabi-gcc "${CFLAGS[@]}" -c "$SCRIPT_DIR/film_post_trampoline.S" -o "$OUT_DIR/film_post_trampoline.o"

echo "[3/5] Checking objects for forbidden relocs, helpers, and NEON..."
if arm-linux-gnueabi-readelf -r "$OUT_DIR/film_post_mod.o" "$OUT_DIR/film_post_trampoline.o" | grep -qE "R_ARM_(GOT|ABS32)"; then
    echo "ERROR: Forbidden GOT or ABS32 relocation detected!" >&2
    arm-linux-gnueabi-readelf -r "$OUT_DIR/film_post_mod.o" "$OUT_DIR/film_post_trampoline.o" >&2
    exit 1
fi

DISASM_TMP="$OUT_DIR/objects.disasm"
arm-linux-gnueabi-objdump -d "$OUT_DIR/film_post_mod.o" "$OUT_DIR/film_post_trampoline.o" > "$DISASM_TMP"

if grep -E "bl[[:space:]].*<(memcpy|memset|memmove|__aeabi_.*)>" "$DISASM_TMP" >&2; then
    echo "ERROR: Forbidden libc or __aeabi_* helper call emitted!" >&2
    exit 1
fi

# Check that no Advanced SIMD / NEON mnemonic or qN register is emitted in disassembly
if grep -iE '\b(vdup|vqmov|vshl|vshr|vld[1-4]|vst[1-4]|vmull|vmlal|vpadd|vext|vtbl|vzip|vuzp|vtrn|q[0-9]+)\b' "$DISASM_TMP" >&2; then
    echo "ERROR: Forbidden NEON instruction or Q-register detected (Cortex-A5 has no NEON)!" >&2
    exit 1
fi

echo "[4/5] Linking freestanding ELF at base 0..."
arm-linux-gnueabi-ld \
    -T "$REPO_ROOT/tools/native_camera/mlv2/mod_v2.ld" \
    -e fp_on \
    "$OUT_DIR/film_post_mod.o" \
    "$OUT_DIR/film_post_trampoline.o" \
    -o "$OUT_DIR/film_post.elf"

if arm-linux-gnueabi-readelf -r "$OUT_DIR/film_post.elf" | grep -qE "R_ARM_(RELATIVE|GLOB_DAT|JUMP_SLOT|ABS32)"; then
    echo "ERROR: Dynamic or non-PC-relative relocations remain in linked ELF!" >&2
    arm-linux-gnueabi-readelf -r "$OUT_DIR/film_post.elf" >&2
    exit 1
fi

arm-linux-gnueabi-objdump -d "$OUT_DIR/film_post.elf" > "$OUT_DIR/film_post.disasm"
arm-linux-gnueabi-readelf -S -s "$OUT_DIR/film_post.elf" > "$OUT_DIR/film_post.readelf.txt"

echo "[5/5] Packing FilmPost.mod (SMOD v2 + SMNU + RLC1)..."
cd "$REPO_ROOT"
python3 -m tools.research.pack_mod_v2 \
    --elf "$OUT_DIR/film_post.elf" \
    --name "Film Post" \
    --on fp_on \
    --off fp_off \
    --bss 69632 \
    --hook "$HOOK_SPEC" \
    --hook "$HOOK_ENC_SPEC" \
    --reloc-layout "$RELOC_LAYOUT" \
    --data-symbol fp_rodata_blob \
    --submenu-bss-off 4 \
    --submenu-cfg fp_cfg \
    --submenu-item "Halation:0:35mm (Default)|35mm No Remjet|16mm|8mm No Remjet|Off" \
    --submenu-item "Halation Mix:0:100%|75%|50%|125%" \
    --submenu-item "Film Grain:0:35mm ISO 250|35mm ISO 500|16mm ISO 250|8mm ISO 500|Off" \
    --submenu-item "JPEG Quality:0:High (92)|Very High (96)|Medium (85)|Diag: Probe Only|Diag: No Write" \
    --out "$OUT_DIR/FilmPost.mod"

cp "$OUT_DIR/FilmPost.mod" "$FINAL_COPY"
echo "Done ($TARGET): $OUT_DIR/FilmPost.mod ($(wc -c < "$OUT_DIR/FilmPost.mod") bytes) -> $FINAL_COPY"
