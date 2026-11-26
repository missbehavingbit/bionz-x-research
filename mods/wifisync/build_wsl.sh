#!/bin/bash
# Build WiFiSync.mod (SMOD v2, ILCE-6400 2.00) on WSL.
# Usage: build_wsl.sh OUTPUT_DIR
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=${1:?usage: build_wsl.sh OUTPUT_DIR}
mkdir -p "$OUT"

MD=mods/wifisync
common=(-mthumb -mcpu=cortex-a5 -mno-unaligned-access -mfpu=vfpv3-d16 -mfloat-abi=softfp -O2
        -ffreestanding -fno-builtin -nostdlib -fno-stack-protector
        -fno-tree-vectorize -fno-unwind-tables -fno-asynchronous-unwind-tables
        -Wall -Wextra -Werror -fpie -fvisibility=hidden)

python3 -m tools.research.wifisync_embed --out "$OUT/wifisync_ap_script.h"

arm-linux-gnueabi-gcc "${common[@]}" -I"$MD" -I"$OUT" -c "$MD/wifisync_core.c" -o "$OUT/wifisync_core.o"
arm-linux-gnueabi-gcc "${common[@]}" -I"$MD" -I"$OUT" -c "$MD/wifisync_mod.c"  -o "$OUT/wifisync_mod.o"
arm-linux-gnueabi-gcc "${common[@]}" -c "$MD/wifisync_hook.S" -o "$OUT/wifisync_hook.o"

for o in "$OUT/wifisync_core.o" "$OUT/wifisync_mod.o" "$OUT/wifisync_hook.o"; do
  if arm-linux-gnueabi-readelf -rW "$o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32|JUMP_SLOT|GLOB_DAT|MOV32|PREL31)"; then
    echo "$o has non-position-independent relocations" >&2
    exit 1
  fi
done

arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld \
  "$OUT/wifisync_core.o" "$OUT/wifisync_mod.o" "$OUT/wifisync_hook.o" \
  -o "$OUT/wifisync.elf"

if arm-linux-gnueabi-objdump -d "$OUT/wifisync.elf" | grep -qE "<(__aeabi_[a-z0-9]+|memcpy|memset|memcmp|strlen|strcmp)>"; then
  echo "unexpected runtime helper imported by wifisync.elf" >&2
  arm-linux-gnueabi-objdump -d "$OUT/wifisync.elf" | grep -E "<(__aeabi_[a-z0-9]+|memcpy|memset|memcmp|strlen|strcmp)>" >&2 || true
  exit 1
fi

arm-linux-gnueabi-objdump -d "$OUT/wifisync.elf" > "$OUT/wifisync.dis"
arm-linux-gnueabi-nm -n "$OUT/wifisync.elf" > "$OUT/wifisync.sym"

# The mod embeds sizeof(struct ws_mod_state) as a 4-byte rodata word. The ELF is
# linked at 0 and .text starts at 0, so the symbol value is the file offset.
BSS_ADDR=$(arm-linux-gnueabi-readelf -sW "$OUT/wifisync.elf" |
           awk '$8 == "ws_bss_size_bytes" {print $2}')
if [ -z "$BSS_ADDR" ]; then
  echo "ws_bss_size_bytes symbol missing" >&2
  exit 1
fi
BSS_DEC=$((16#$BSS_ADDR))
SEG_OFF=$(arm-linux-gnueabi-readelf -lW "$OUT/wifisync.elf" |
          awk '/LOAD/ {print $2}')
BSS=$(od -An -tu4 -j $((SEG_OFF + BSS_DEC)) -N4 "$OUT/wifisync.elf" | tr -d ' \n')
if [ -z "$BSS" ] || [ "$BSS" -le 0 ]; then
  echo "could not read ws_bss_size_bytes" >&2
  exit 1
fi
BSS_ALIGNED=$(( (BSS + 4095) / 4096 * 4096 ))
echo "bss=$BSS aligned=$BSS_ALIGNED"

python3 -m tools.research.pack_mod_v2 \
  --elf "$OUT/wifisync.elf" \
  --name "WiFiSync" \
  --on ws_on --off ws_on \
  --bss "$BSS_ALIGNED" \
  --submenu-bss-off 4 \
  --submenu-cfg ws_cfg \
  --hook "0x079add24:2de9f0410d4600af04464049:ws_capture_hook" \
  --reloc-layout tools/native_camera/mlv2/reference_layout_ilce6400.txt \
  --data-symbol ws_ap_script \
  --submenu-item "Standby Mode:0:Low-Power Wi-Fi|Deep Sleep (BLE)" \
  --submenu-item "AP Width:0:80 MHz|40 MHz|20 MHz" \
  --submenu-item "AP Channel:0:Ch 36|Ch 40|Ch 44|Ch 48" \
  --out "$OUT/WiFiSync.mod" | tee "$OUT/pack.json"

sha256sum "$OUT/WiFiSync.mod" > "$OUT/WiFiSync.mod.sha256"
