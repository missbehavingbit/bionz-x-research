#!/bin/bash
# Bring a WSL copy of the emulator up to date with this folder and rebuild QEMU.
#   bash tools/rebuild_wsl.sh [WSL_COPY]       (rebuild_qemu_wsl.bat runs this)
# WSL_COPY: the emulator copy that has the qemu/ build. Default: searched for under $HOME;
# if none is found, one is created at ~/a7c-emulator (QEMU cannot be built in a path with
# spaces, like "Sony A7C Emulator/a7c-emulator v.1"). Its fw/, img/ and dump/ are links
# back to this folder, so the firmware and the eMMC image are not copied.
# Only source files are synced (qemu-overlay, patches, tools, docs, scripts); fw/, img/, out/
# and dump/ are never touched. The copy's previous files are saved in a .tar.gz first.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
FILES=(qemu-overlay patches tools docs run.sh setup-qemu.sh README.md CHANGES.md VERSION .gitignore)

find_copies() {
    find "$HOME" -maxdepth 7 -path '*/qemu/hw/arm/sony_a7c.c' 2>/dev/null |
        sed 's|/qemu/hw/arm/sony_a7c.c$||' | while read -r d; do
            [ -f "$d/setup-qemu.sh" ] && [ "$(cd "$d" && pwd)" != "$SRC" ] && echo "$d"
        done
}

TARGET="${1:-}"
if [ -z "$TARGET" ]; then
    mapfile -t COPIES < <(find_copies)
    if [ ${#COPIES[@]} -eq 0 ]; then
        TARGET="$HOME/a7c-emulator"
        echo "No WSL copy with a qemu/ build found under $HOME; creating $TARGET"
        mkdir -p "$TARGET"
    else
        # several copies: take the one built most recently
        TARGET=$(for d in "${COPIES[@]}"; do
            printf '%s\t%s\n' "$(stat -c %Y "$d/qemu/build/qemu-system-arm" 2>/dev/null || echo 0)" "$d"
        done | sort -rn | head -1 | cut -f2-)
        [ ${#COPIES[@]} -gt 1 ] && printf 'Found copies:\n%s\n' "$(printf '  %s\n' "${COPIES[@]}")"
        echo "Updating WSL copy: $TARGET"
    fi
fi
TARGET="$(cd "$TARGET" && pwd)"
case "$TARGET" in
    *" "*|*:*) echo "ERROR: QEMU cannot be built in a path with spaces or colons: $TARGET"
               echo "       Run without an argument to use ~/a7c-emulator."; exit 1 ;;
esac
VER="$(cat "$SRC/VERSION" 2>/dev/null || echo '?')"
echo "Source: $SRC (version $VER)"

if [ "$TARGET" != "$SRC" ]; then
    stamp=$(date +%Y%m%d-%H%M%S)
    backup="$TARGET/backup-before-sync-$stamp.tar.gz"
    existing=()
    for f in "${FILES[@]}"; do [ -e "$TARGET/$f" ] && existing+=("$f"); done
    if [ ${#existing[@]} -gt 0 ]; then
        tar -czf "$backup" -C "$TARGET" "${existing[@]}"
        echo "Saved the copy's previous files in $backup"
    fi
    for f in "${FILES[@]}"; do
        [ -e "$SRC/$f" ] || continue
        rm -rf "${TARGET:?}/$f"
        cp -r "$SRC/$f" "$TARGET/$f"
    done
    find "$TARGET/tools" -name '__pycache__' -prune -exec rm -rf {} +
    chmod +x "$TARGET"/run.sh "$TARGET"/setup-qemu.sh "$TARGET"/tools/*.sh 2>/dev/null || true
    echo "Synced: ${FILES[*]}"
    # firmware, eMMC image and captures stay in the Windows folder: link them
    for d in fw img dump; do
        if [ ! -e "$TARGET/$d" ] && [ -d "$SRC/$d" ]; then
            ln -s "$SRC/$d" "$TARGET/$d"
            echo "Linked $TARGET/$d -> $SRC/$d"
        fi
    done
    mkdir -p "$TARGET/out"
    # a QEMU tree left in the Windows folder by an earlier attempt (it cannot be built
    # there): move it here instead of downloading it again
    if [ ! -d "$TARGET/qemu" ] && [ -d "$SRC/qemu/.git" ] && [ ! -f "$SRC/qemu/build/build.ninja" ]; then
        echo "Moving the QEMU source left in $SRC/qemu to $TARGET/qemu (takes a minute)..."
        if mv "$SRC/qemu" "$TARGET/qemu"; then
            rm -rf "$TARGET/qemu/config-temp" "$TARGET/qemu/config.log" "$TARGET/qemu/build"
            if ! (cd "$TARGET/qemu" && git apply --check -R "$TARGET/patches/qemu-v10.1.0-sony-a7c.patch") 2>/dev/null; then
                echo "  it is not fully patched; it will be downloaded again instead"
                rm -rf "$TARGET/qemu"
            fi
        fi
    fi
fi

# Build tools (Ubuntu/Debian package names). Missing ones are listed and, if you agree,
# installed with sudo apt-get (it asks for your WSL password).
check_build_deps() {
    local missing=()
    command -v git        >/dev/null || missing+=(git)
    command -v gcc        >/dev/null || missing+=(build-essential)
    command -v make       >/dev/null || missing+=(build-essential)
    command -v ninja      >/dev/null || missing+=(ninja-build)
    command -v pkg-config >/dev/null || missing+=(pkg-config)
    python3 -c 'import venv, ensurepip' 2>/dev/null || missing+=(python3-venv)
    if command -v pkg-config >/dev/null; then
        pkg-config --exists glib-2.0 || missing+=(libglib2.0-dev)
        pkg-config --exists pixman-1 || missing+=(libpixman-1-dev)
        pkg-config --exists zlib     || missing+=(zlib1g-dev)
        pkg-config --exists gtk+-3.0 || missing+=(libgtk-3-dev)   # emulator window
        pkg-config --exists sdl2     || missing+=(libsdl2-dev)
    else
        missing+=(libglib2.0-dev libpixman-1-dev zlib1g-dev libgtk-3-dev libsdl2-dev)
    fi
    [ -f /usr/include/libfdt.h ] || missing+=(libfdt-dev)
    [ ${#missing[@]} -eq 0 ] && return 0
    mapfile -t missing < <(printf '%s\n' "${missing[@]}" | sort -u)
    echo
    echo "QEMU needs these build tools, which are not installed in WSL:"
    echo "  ${missing[*]}"
    echo "Command: sudo apt-get install -y ${missing[*]}"
    read -r -p "Install them now? sudo will ask for your WSL password. [Y/n] " ans
    case "$ans" in
        n|N|no|No) echo "Not installed; run the command above, then this script again."; exit 1 ;;
    esac
    sudo apt-get update && sudo apt-get install -y "${missing[@]}" || {
        echo "Installing failed; see the messages above."; exit 1; }
}
check_build_deps

cd "$TARGET"
if [ ! -d qemu ]; then
    echo "No qemu/ here yet: running setup-qemu.sh (clones QEMU v10.1.0 and builds; takes a while)"
    exec bash ./setup-qemu.sh
fi

# overlay -> qemu tree (only files that differ)
changed=0
while IFS= read -r -d '' f; do
    rel="${f#qemu-overlay/}"
    if ! cmp -s "$f" "qemu/$rel"; then
        mkdir -p "qemu/$(dirname "$rel")"
        cp "$f" "qemu/$rel"
        echo "  updated qemu/$rel"
        changed=$((changed + 1))
    fi
done < <(find qemu-overlay -type f -print0)
echo "$changed overlay file(s) updated in qemu/"

# new overlay .c files (added in a later version) need a line in their meson.build
while IFS= read -r -d '' f; do
    rel="${f#qemu-overlay/}"; dir="$(dirname "$rel")"; name="$(basename "$rel")"
    mb="qemu/$dir/meson.build"
    [ -f "$mb" ] || continue
    if ! grep -q "'$name'" "$mb"; then
        echo "system_ss.add(when: 'CONFIG_SONY_A7C', if_true: files('$name'))" >> "$mb"
        echo "  added $name to qemu/$dir/meson.build"
    fi
done < <(find qemu-overlay/hw -name '*.c' -print0)

mkdir -p qemu/build
cd qemu/build
[ -f build.ninja ] || ../configure --target-list=arm-softmmu --disable-docs --disable-werror
ninja qemu-system-arm qemu-img
./qemu-system-arm -M help | grep sony-a7c
if ./qemu-system-arm -M sony-a7c,help 2>/dev/null | grep -q units; then
    echo "OK: the sony-a7c machine has v1.2 options (units, handoff, evsel, submicro)."
fi
echo "Built: $PWD/qemu-system-arm"
echo "Run from: $TARGET   e.g.  A7C_ICOUNT=0 A7C_MACHINE_OPTS=evsel=on ./run.sh"
