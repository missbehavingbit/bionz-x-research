#!/bin/bash
# Stage the pinned upstream release plus the sony-a6400 overlay.
# Versioned checkouts preserve the previous build and avoid paths with spaces.
set -euo pipefail
E="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$(python3 -c 'import json,sys; q=json.load(open(sys.argv[1]))["qemu"]; print(q["version"], q["tag"], q["commit"], q["repository"])' "$E/../tools/toolchain.json")"
read -r VERSION TAG COMMIT REPOSITORY <<< "$CONFIG"
if [ -n "${A6400_QEMU_DIR:-}" ]; then
    Q="$A6400_QEMU_DIR"
else
    Q="$HOME/sony-a6400-qemu-$VERSION"
fi

if [ ! -d "$Q" ]; then
    git clone --branch "$TAG" --depth 1 "$REPOSITORY" "$Q"
fi

if [ "$(git -C "$Q" rev-parse HEAD)" != "$COMMIT" ]; then
    echo "Refusing to modify a QEMU checkout other than pinned $TAG ($COMMIT): $Q" >&2
    exit 1
fi

PATCH="$E/patches/qemu-v$VERSION-sony-a6400.patch"
if git -C "$Q" apply --check "$PATCH" 2>/dev/null; then
    git -C "$Q" apply "$PATCH"
elif git -C "$Q" apply --reverse --check "$PATCH" 2>/dev/null; then
    echo "Pinned Sony patch already applied."
else
    echo "Sony patch conflicts with $Q; no overlay files were copied." >&2
    exit 1
fi

cp -R "$E/qemu-overlay/." "$Q/"
mkdir -p "$Q/build"
echo "QEMU tree ready at: $Q"
echo "Build with:"
echo "  cd \"$Q/build\" && ../configure --target-list=arm-softmmu --enable-sdl --disable-werror && ninja qemu-system-arm qemu-img"
