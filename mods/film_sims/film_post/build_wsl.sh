#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
OUT_DIR="${1:-$ROOT_DIR/artifacts/tests/film-post}"
mkdir -p "$OUT_DIR"

echo "[1/3] Compiling ARMv7 Cortex-A5 (VFPv3-D16, zero NEON) object..."
arm-linux-gnueabi-gcc -std=c99 -O2 -mcpu=cortex-a5 -mfpu=vfpv3-d16 -mfloat-abi=softfp \
    -Wall -Wextra -Werror -c "$SCRIPT_DIR/film_post.c" -o "$OUT_DIR/film_post_arm.o"

arm-linux-gnueabi-size "$OUT_DIR/film_post_arm.o"

echo "[2/3] Verifying zero NEON instructions in ARM object..."
if arm-linux-gnueabi-objdump -d "$OUT_DIR/film_post_arm.o" | grep -E '\bq[0-9]+\b|\bvdup|\bvld[1-4]|\bvst[1-4]|\bvorr\b|\bvmul\.i|\bvadd\.i' ; then
    echo "ERROR: NEON instructions detected in Cortex-A5 build!" >&2
    exit 1
fi
echo "ARMv7 Cortex-A5 check OK: zero NEON instructions."

echo "[3/3] Building and running host C99 unit tests & 24 MP benchmark..."
gcc -std=c99 -O2 -Wall -Wextra -Werror \
    "$SCRIPT_DIR/film_post.c" "$SCRIPT_DIR/test_film_post.c" -lm \
    -o "$OUT_DIR/test_film_post"
"$OUT_DIR/test_film_post"
