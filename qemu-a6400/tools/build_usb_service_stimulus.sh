#!/bin/bash
# Compile a pre-generated, identity-checked QEMU USB stimulus into a new output.
set -euo pipefail
cd "$(dirname "$0")/../.."
out=$1
test -f "$out/usb_symbols.h"
test ! -e "$out/stimulus.o"
test ! -e "$out/UsbServiceProbe.mod"
arm-linux-gnueabi-gcc --version > "$out/compiler.txt"
sha256sum qemu-a6400/tools/usb_service_stimulus.c tools/research/build_qemu_usb_stimulus.py \
    tools/research/pack_mod_v2.py "$out/usb_symbols.h" > "$out/build-inputs.sha256"
arm-linux-gnueabi-gcc -mthumb -mcpu=cortex-a5 -Os -ffreestanding -fno-builtin -nostdlib \
    -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror -fpie -fvisibility=hidden \
    -Itools/native_camera/mlv2 -I"$out" -c qemu-a6400/tools/usb_service_stimulus.c -o "$out/stimulus.o"
arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld \
    "$out/stimulus.o" -o "$out/stimulus.elf"
if arm-linux-gnueabi-readelf -r "$out/stimulus.o" | grep -E 'R_ARM_(ABS32|GOT|TARGET1|PLT32|JUMP_SLOT|GLOB_DAT)'; then
    echo "Unexpected absolute relocation" >&2
    exit 1
fi
test -z "$(arm-linux-gnueabi-nm -u "$out/stimulus.elf")"
data_args=()
for label in CONTROL_INSTANCE OBJMSG_CTOR OBJMSG_DTOR PARAM_CTOR PARAM_DTOR ADD_PARAM SEND_EVENT MALLOC FREE; do
    data_args+=(--data-symbol "usb_expected_$label")
done
python3 -m tools.research.pack_mod_v2 --elf "$out/stimulus.elf" --name "USB Service Probe" \
    --on usb_probe_on --auto-on --reloc-layout tools/native_camera/mlv2/reference_layout_ilce6400.txt \
    "${data_args[@]}" --out "$out/UsbServiceProbe.mod" > "$out/pack.json"
