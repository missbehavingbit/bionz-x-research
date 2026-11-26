#!/bin/sh
# The package builder supplies the archive digest as the first argument.
set -eu
[ "$#" -eq 1 ] || exit 2
digest=$1
bb=/bin/busybox
stage=/tmp_bt/mlv2
mods=/tmp_bt/mods
reader=/setting/mlv2_card_read.elf

remaining=120
while ! "$bb" grep -q ' /tmp_bt tmpfs rw[, ]' /proc/mounts; do
    [ "$remaining" -gt 0 ] || exit 0
    remaining=$((remaining - 1))
    "$bb" sleep 1
done
"$bb" mount -o remount,size=4096k /tmp_bt 2>/dev/null || true
umask 077
"$bb" mkdir "$stage" || exit 0
owns_mods=0
guard_started=0
cleanup() {
    if [ "$guard_started" -eq 1 ]; then
        : > "$stage/sd-disabled"
    else
        if [ "$owns_mods" -eq 1 ]; then
            "$bb" rm -rf "$mods"
        fi
        "$bb" rm -f "$stage/core.bin" "$stage/receipt" "$stage/sd-managed"
    fi
    "$bb" rm -rf "$stage/card.tar" "$stage/guard" "$stage/mlv2.req" "$stage/check.log" \
        "$stage"/*.mod "$stage"/*.rle "$stage/sabin" "$stage/grain"
    if [ "$guard_started" -eq 0 ]; then "$bb" rmdir "$stage"; fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
exec > "$stage/receipt" 2>&1

remaining=120
until [ -b /dev/mmca1 ] && "$bb" grep -q '^im\.elf$' /proc/149/comm 2>/dev/null &&
    "$bb" grep -q '/libtestcmd\.so$' /proc/149/maps &&
    "$bb" grep -q '/viewUnified2\.so$' /proc/149/maps; do
    if [ "$remaining" -eq 0 ]; then echo MLV2_SD_NOT_READY; exit 0; fi
    remaining=$((remaining - 1))
    "$bb" sleep 1
done

if ! "$bb" timeout -t 8 "$reader"; then echo MLV2_SD_PACKAGE_UNAVAILABLE; exit 0; fi
cd "$stage"
printf '%s  card.tar\n' "$digest" | "$bb" sha256sum -c -
"$bb" tar -xf card.tar
"$bb" chmod 700 guard
./guard check mlv2.req > check.log
"$bb" cat check.log
if ! "$bb" grep -q '^STATE hooks=stock v1_build=-1 shim=zero$' check.log; then
    "$bb" rm -f check.log
    exit 1
fi
"$bb" rm -f check.log
"$bb" mkdir -p "$mods"
owns_mods=1
for f in *.mod *.rle sabin grain; do
    if [ -e "$f" ]; then "$bb" mv "$f" "$mods/"; fi
done
"$bb" timeout -t 8 "$reader" --present
: > sd-managed
guard_started=1
./guard install mlv2.req
"$bb" rm -f card.tar guard mlv2.req
echo MLV2_SD_INSTALLED

validate_returned_card() {
    "$bb" rm -f card.tar
    if "$bb" timeout -t 8 "$reader" &&
        printf '%s  card.tar\n' "$digest" | "$bb" sha256sum -c -; then
        "$bb" rm -f card.tar
        return 0
    fi
    "$bb" rm -f card.tar
    return 1
}

while [ "$("$bb" cat /proc/149/comm 2>/dev/null)" = im.elf ]; do
    if [ -e sd-disabled ]; then
        # Wait for the menu to finish OFF and retire the Mods root. A rapid
        # reinsertion must not cancel a pending disable or a failed rollback.
        if [ -e sd-stopped ] && validate_returned_card; then
            "$bb" rm -f sd-stopped sd-disabled
            echo MLV2_SD_REACTIVATED
        fi
    elif ! "$bb" timeout -t 8 "$reader" --present; then
        "$bb" rm -f sd-stopped
        : > sd-disabled
        echo MLV2_SD_PACKAGE_UNAVAILABLE
    fi
    "$bb" sleep 2
done
