#!/bin/bash
# Build the Live Picker SMOD v2 skeleton ("FilmSimulations.mod") and its layout record.
#
# Compiles film_sim_live_template.c (+ the event_push trampoline) with the SMOD v2 linker script,
# packs it exactly like mods/film_sims/live_picker/build_live_wsl.sh (ACTION flag, 48-byte bss, the
# event_push hook and the RLC1 relocation table), and writes
#   isp-studio/crates/bionz-preset/assets/film_sim_live_template.mod
#   isp-studio/crates/bionz-preset/assets/film_sim_live_template.json
# The Rust packer clones the skeleton and only rewrites the name, the payload and the code checksum,
# so the hook, the bss and the relocation table stay exactly as verified here.
#
# Run from Windows with:
#   wsl.exe -d Ubuntu -- bash mods/film_sims/live_picker/build_live_template_wsl.sh
set -euo pipefail
cd "$(dirname "$0")/../../.."
export PATH="$PWD/artifacts/toolchain/arm-linux-gnueabi/bin:$PATH"
command -v arm-linux-gnueabi-gcc >/dev/null || { echo "arm-linux-gnueabi-gcc not found" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 1; }

OUT=isp-studio/target/film-sim-live-template
ASSETS=isp-studio/crates/bionz-preset/assets
LP=mods/film_sims/live_picker
HOOK_ADDR=0x0655a528
HOOK_ORIG=2de9f04184b0044602af0846
mkdir -p "$OUT" "$ASSETS"

FLAGS="-mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin -nostdlib -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror -fpie -fvisibility=hidden -I$LP"

arm-linux-gnueabi-gcc $FLAGS -c "$LP/film_sim_live_template.c" -o "$OUT/live.o"
arm-linux-gnueabi-gcc $FLAGS -c "$LP/film_sim_live_trampoline.S" -o "$OUT/live_tramp.o"
for o in "$OUT/live.o" "$OUT/live_tramp.o"; do
  if arm-linux-gnueabi-readelf -rW "$o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32|JUMP_SLOT|GLOB_DAT)"; then
    echo "$o has absolute or GOT relocations" >&2
    exit 1
  fi
done
arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld "$OUT/live.o" "$OUT/live_tramp.o" -o "$OUT/live.elf"
arm-linux-gnueabi-nm -n "$OUT/live.elf" > "$OUT/live.sym"
grep -q " T live_life$" "$OUT/live.sym" || { echo "live_life is not in .text" >&2; exit 1; }

python3 -m tools.research.pack_mod_v2 --elf "$OUT/live.elf" --name "Film Simulations" \
  --on live_life --bss 48 --action \
  --hook "${HOOK_ADDR}:${HOOK_ORIG}:live_event_push" \
  --reloc-layout tools/native_camera/mlv2/reference_layout_ilce6400.txt \
  --data-symbol live_payload --data-symbol live_font \
  --out "$OUT/FilmSimulations.mod" > "$OUT/pack.json"
python3 -c "import json,sys; d=json.load(open('$OUT/pack.json')); sys.exit(0 if d.get('reloc_words') is not None else 1)" \
  || { echo "the live template must carry a relocation table" >&2; exit 1; }

# Field offsets for the ARM ABI: a probe object compiled by the same toolchain.
cat > "$OUT/payload_offsets.c" <<'EOF'
#include <stddef.h>
#include "film_sim_live_payload.h"
__attribute__((section(".probe"), used))
const unsigned live_payload_offsets[] = {
    sizeof(struct live_payload),
    offsetof(struct live_payload, magic),
    offsetof(struct live_payload, ec_gamma_pa),
    offsetof(struct live_payload, fc_gamma_pa),
    offsetof(struct live_payload, color_a0_pa),
    offsetof(struct live_payload, color_a1_pa),
    offsetof(struct live_payload, color_b2_pa),
    offsetof(struct live_payload, cc_tuning_pa),
    offsetof(struct live_payload, refadr0_pa),
    offsetof(struct live_payload, n_presets),
    offsetof(struct live_payload, n_looks),
    offsetof(struct live_payload, log_path),
    offsetof(struct live_payload, names),
    offsetof(struct live_payload, lut_idx),
    offsetof(struct live_payload, ec_stock),
    offsetof(struct live_payload, fc_stock),
    offsetof(struct live_payload, a0_stock),
    offsetof(struct live_payload, a1_stock),
    offsetof(struct live_payload, b2_stock),
    offsetof(struct live_payload, ec),
    offsetof(struct live_payload, fc),
    offsetof(struct live_payload, a0),
    offsetof(struct live_payload, a1),
    offsetof(struct live_payload, b2),
    offsetof(struct live_payload, refadr),
};
EOF
arm-linux-gnueabi-gcc $FLAGS -c "$OUT/payload_offsets.c" -o "$OUT/payload_offsets.o"
arm-linux-gnueabi-objcopy -O binary --only-section=.probe "$OUT/payload_offsets.o" "$OUT/payload_offsets.bin"

COMPILER="$(arm-linux-gnueabi-gcc --version | head -n1)"
python3 "$LP/emit_live_template_assets.py" \
  --mod "$OUT/FilmSimulations.mod" --elf "$OUT/live.elf" --offsets "$OUT/payload_offsets.bin" \
  --source "$LP/film_sim_live_template.c" --out-dir "$ASSETS" \
  --compiler "$COMPILER" --flags "$FLAGS" --hook "$HOOK_ADDR"
