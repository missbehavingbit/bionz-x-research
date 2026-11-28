#!/bin/bash
# Build FocusBracket.mod (ILCE-7C, SMOD v2). Run from anywhere; uses repo tools.
# Needs arm-linux-gnueabi-gcc/ld and Python 3.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$HERE/FocusBracket.mod}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

arm-linux-gnueabi-gcc -mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os \
  -ffreestanding -fno-builtin -nostdlib -nostartfiles -fno-stack-protector \
  -fomit-frame-pointer -Wall -Wextra -Werror -Wno-unused-parameter -fpie \
  -fvisibility=hidden -c "$HERE/focus_bracket.c" -o "$TMP/focus_bracket.o"
arm-linux-gnueabi-ld --build-id=none -T "$ROOT/tools/native_camera/mlv2/mod_v2.ld" \
  "$TMP/focus_bracket.o" -o "$TMP/focus_bracket.elf"

cd "$ROOT"
python3 -m tools.research.pack_mod_v2 --elf "$TMP/focus_bracket.elf" \
  --name 'Focus Bracket' --on fb_on --off fb_off --bss 16 \
  --submenu-bss-off 0 --submenu-cfg fb_cfg_changed \
  --reloc-layout "$HERE/reference_layout_ilce7c.txt" \
  --hook 0x20902548:2de9f04f0d46f5498fb00446:fb_write_still_hook \
  --submenu-item 'Mode:0:Landscape|Macro|Custom' \
  --submenu-item 'Step Width:2:1 (Narrow)|2|4 (Default)|6|8|10 (Wide)' \
  --submenu-item 'Shots:2:5|15|35|40|60|100' \
  --submenu-item 'Order:0:0 -> +|0 -> - -> +' \
  --submenu-item 'Exp Smooth:0:Off|On' \
  --submenu-item 'Interval:0:Shortest|1 s|2 s|3 s|5 s|10 s' \
  --out "$OUT"
echo "built $OUT"
