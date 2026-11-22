#!/bin/bash
# Create ./qemu = upstream QEMU v10.1.0 + qemu-overlay/ + patches/, then build
# qemu-system-arm with the sony-a7c machine.  Linux or WSL2 (Ubuntu 22.04+).
#
# Build deps (Ubuntu): sudo apt install git build-essential ninja-build \
#   python3-venv pkg-config libglib2.0-dev libpixman-1-dev libfdt-dev flex bison \
#   libgtk-3-dev libsdl2-dev          (for the screen window; optional)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
case "$HERE" in
    *" "*|*:*) echo "ERROR: QEMU cannot be built in a path with spaces: $HERE"
               echo "On Windows, run rebuild_qemu_wsl.bat instead: it builds in ~/a7c-emulator in WSL."
               exit 1 ;;
esac

if [ ! -d qemu ]; then
    git clone --depth 1 --branch v10.1.0 https://gitlab.com/qemu-project/qemu.git qemu ||
        git clone --depth 1 --branch v10.1.0 https://github.com/qemu/qemu.git qemu
    # Subprojects normally come from gitlab; fall back to the GitHub mirrors.
    cd qemu/subprojects
    for spec in keycodemapdb:f5772a62ec52591ff6870b7e8ef32482371f22c6 \
                berkeley-softfloat-3:b64af41c3276f97f0e181920400ee056b9c88037 \
                berkeley-testfloat-3:e7af9751d9f9fd3b47911f51a5cfd08af256a9ab; do
        n=${spec%%:*}; r=${spec##*:}
        [ -d "$n" ] && continue
        git init -q "$n"
        (cd "$n" && git fetch -q --depth 1 "https://github.com/qemu/$n.git" "$r" &&
            git checkout -q FETCH_HEAD)
        [ -d "packagefiles/$n" ] && cp -r "packagefiles/$n/." "$n/"
    done
    cd "$HERE"
    cp -r qemu-overlay/. qemu/
    (cd qemu && git apply "$HERE/patches/qemu-v10.1.0-sony-a7c.patch")
fi

# Every run: copy overlay files that changed since the last build into qemu/
while IFS= read -r -d '' f; do
    rel="${f#qemu-overlay/}"
    cmp -s "$f" "qemu/$rel" || { mkdir -p "qemu/$(dirname "$rel")"; cp "$f" "qemu/$rel"; echo "updated qemu/$rel"; }
done < <(find qemu-overlay -type f -print0)

# Overlay .c files the patch does not list (added in a later version) need a meson.build line
while IFS= read -r -d '' f; do
    rel="${f#qemu-overlay/}"; dir="$(dirname "$rel")"; name="$(basename "$rel")"
    mb="qemu/$dir/meson.build"
    [ -f "$mb" ] || continue
    if ! grep -q "'$name'" "$mb"; then
        echo "system_ss.add(when: 'CONFIG_SONY_A7C', if_true: files('$name'))" >> "$mb"
        echo "added $name to qemu/$dir/meson.build"
    fi
done < <(find qemu-overlay/hw -name '*.c' -print0)

mkdir -p qemu/build
cd qemu/build
[ -f build.ninja ] || ../configure --target-list=arm-softmmu --disable-docs --disable-werror
ninja qemu-system-arm qemu-img
./qemu-system-arm -M help | grep sony-a7c
echo "Built: $PWD/qemu-system-arm"
