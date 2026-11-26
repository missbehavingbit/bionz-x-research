#!/bin/bash
# Build the position-independent Film Simulation SMOD v2 template and its layout record.
#
# Compiles film_sim_template.c into a Thumb, position-independent `.text` blob (linked at 0 with the
# SMOD v2 linker script), refuses absolute relocations, and writes
#   isp-studio/crates/bionz-preset/assets/film_sim_template.blob
#   isp-studio/crates/bionz-preset/assets/film_sim_template.json
# which `bionz-preset::smod_v2` embeds. Run from Windows with:
#   wsl.exe -d Ubuntu -- bash mods/film_sims/template/build_template_wsl.sh
set -euo pipefail
cd "$(dirname "$0")/../../.."
export PATH="$PWD/artifacts/toolchain/arm-linux-gnueabi/bin:$PATH"
command -v arm-linux-gnueabi-gcc >/dev/null || { echo "arm-linux-gnueabi-gcc not found" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 1; }

OUT=isp-studio/target/film-sim-template
ASSETS=isp-studio/crates/bionz-preset/assets
mkdir -p "$OUT" "$ASSETS"

FLAGS="-mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin -nostdlib -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror -fpie -fvisibility=hidden -Imods/film_sims/template"

arm-linux-gnueabi-gcc $FLAGS -c mods/film_sims/template/film_sim_template.c -o "$OUT/film_sim_template.o"
if arm-linux-gnueabi-readelf -rW "$OUT/film_sim_template.o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32)"; then
  echo "absolute relocations: not position-independent" >&2
  exit 1
fi
arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld "$OUT/film_sim_template.o" -o "$OUT/film_sim_template.elf"
arm-linux-gnueabi-nm -n "$OUT/film_sim_template.elf" > "$OUT/film_sim_template.sym"
grep -q " T sim_life$" "$OUT/film_sim_template.sym" || { echo "sim_life is not in .text" >&2; exit 1; }

# Field offsets for the ARM ABI: a probe object compiled by the same toolchain.
cat > "$OUT/payload_offsets.c" <<'EOF'
#include <stddef.h>
#include "film_sim_payload.h"
__attribute__((section(".probe"), used))
const unsigned sim_payload_offsets[] = {
    sizeof(struct sim_payload),
    offsetof(struct sim_payload, magic),
    offsetof(struct sim_payload, ec_gamma_pa),
    offsetof(struct sim_payload, fc_gamma_pa),
    offsetof(struct sim_payload, color_a0_pa),
    offsetof(struct sim_payload, color_a1_pa),
    offsetof(struct sim_payload, color_b2_pa),
    offsetof(struct sim_payload, cc_tuning_pa),
    offsetof(struct sim_payload, refadr0_pa),
    offsetof(struct sim_payload, mod_tag),
    offsetof(struct sim_payload, mod_log_path),
    offsetof(struct sim_payload, ec_stock),
    offsetof(struct sim_payload, ec_sim),
    offsetof(struct sim_payload, fc_stock),
    offsetof(struct sim_payload, fc_sim),
    offsetof(struct sim_payload, a0_stock),
    offsetof(struct sim_payload, a0_sim),
    offsetof(struct sim_payload, a1_stock),
    offsetof(struct sim_payload, a1_sim),
    offsetof(struct sim_payload, b2_stock),
    offsetof(struct sim_payload, b2_sim),
    offsetof(struct sim_payload, refadr_sim),
    offsetof(struct sim_payload, _pad),
};
EOF
arm-linux-gnueabi-gcc $FLAGS -c "$OUT/payload_offsets.c" -o "$OUT/payload_offsets.o"
arm-linux-gnueabi-objcopy -O binary --only-section=.probe "$OUT/payload_offsets.o" "$OUT/payload_offsets.bin"

COMPILER="$(arm-linux-gnueabi-gcc --version | head -n1)"
python3 mods/film_sims/template/emit_template_assets.py \
  --elf "$OUT/film_sim_template.elf" --offsets "$OUT/payload_offsets.bin" \
  --source mods/film_sims/template/film_sim_template.c --out-dir "$ASSETS" \
  --compiler "$COMPILER" --flags "$FLAGS"
