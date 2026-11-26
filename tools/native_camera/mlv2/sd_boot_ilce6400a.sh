#!/bin/sh
# The package builder supplies the archive digest as the first argument.
set -eu
[ "$#" -eq 1 ] || exit 2
digest=$1
bb=/bin/busybox
stage=/tmp_bt/mlv2
mods=/tmp_bt/mods
reader=/setting/mlv2_card_read.elf
if "$bb" timeout 1 "$bb" true >/dev/null 2>&1; then
    run_tmo() { "$bb" timeout 8 "$@"; }
else
    run_tmo() { "$bb" timeout -t 8 "$@"; }
fi

remaining=5
while ! "$bb" grep -q ' /tmp_bt tmpfs rw[, ]' /proc/mounts; do
    if [ "$remaining" -le 0 ]; then
        "$bb" mount -t tmpfs -o size=4096k tmpfs /tmp_bt 2>/dev/null || true
        break
    fi
    remaining=$((remaining - 1))
    "$bb" sleep 1
done
"$bb" grep -q ' /tmp_bt tmpfs rw[, ]' /proc/mounts || exit 0
umask 077
"$bb" mkdir "$stage" || exit 0
owns_mods=0
guard_started=0
find_im_pid() {
    found=""
    for c in /proc/[0-9]*/comm; do
        name=$("$bb" cat "$c" 2>/dev/null) || continue
        [ "$name" = "im.elf" ] || continue
        p="${c#/proc/}"
        p="${p%/comm}"
        if [ -n "$found" ]; then return 1; fi
        found="$p"
    done
    [ -n "$found" ] || return 1
    printf '%s\n' "$found"
}
cleanup() {
    if [ "$guard_started" -eq 1 ]; then
        : > "$stage/sd-disabled"
    else
        if [ "$owns_mods" -eq 1 ]; then
            "$bb" rm -f "$mods"/*.mod "$mods/ModsTabIcon.rle"
            "$bb" rmdir "$mods" 2>/dev/null || true
        fi
        "$bb" rm -f "$stage/core.bin" "$stage/receipt" "$stage/sd-managed"
    fi
    "$bb" rm -f "$stage/card.tar" "$stage/guard" "$stage/mlv2.req" "$stage/check.log" \
        "$stage"/*.mod "$stage/ModsTabIcon.rle"
    if [ "$guard_started" -eq 0 ]; then "$bb" rmdir "$stage"; fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
exec > "$stage/receipt" 2>&1

remaining=120
im_pid=""
while :; do
    if [ -b /dev/mmca1 ] && im_pid=$(find_im_pid) &&
        "$bb" grep -q '/libtestcmd\.so$' "/proc/$im_pid/maps" &&
        "$bb" grep -q '/viewUnified2\.so$' "/proc/$im_pid/maps"; then
        break
    fi
    if [ "$remaining" -eq 0 ]; then echo MLV2_SD_NOT_READY; exit 0; fi
    remaining=$((remaining - 1))
    "$bb" sleep 1
done

card_tries=20
while :; do
    rc=0
    run_tmo "$reader" || rc=$?
    if [ "$rc" -eq 0 ]; then break; fi
    if [ "$rc" -ne 3 ] || [ "$card_tries" -le 0 ]; then
        echo MLV2_SD_PACKAGE_UNAVAILABLE
        exit 0
    fi
    card_tries=$((card_tries - 1))
    "$bb" sleep 1
done
cd "$stage"
printf '%s  card.tar\n' "$digest" | "$bb" sha256sum -c -
"$bb" tar -xf card.tar
"$bb" rm -f card.tar
"$bb" chmod 700 guard
./guard check mlv2.req > check.log
"$bb" cat check.log
if ! "$bb" grep -q '^STATE hooks=stock v1_build=-1 shim=zero$' check.log; then
    "$bb" rm -f check.log
    exit 1
fi
"$bb" rm -f check.log
if [ ! -d "$mods" ]; then
    "$bb" mkdir "$mods"
    owns_mods=1
fi
for item in *.mod ModsTabIcon.rle; do
    [ -f "$item" ] || continue
    "$bb" mv -f "$item" "$mods/$item"
done
run_tmo "$reader" --present
: > sd-managed
guard_started=1
./guard install mlv2.req
"$bb" rm -f card.tar guard mlv2.req
echo MLV2_SD_INSTALLED

validate_returned_card() {
    "$bb" rm -f card.tar
    if run_tmo "$reader" &&
        printf '%s  card.tar\n' "$digest" | "$bb" sha256sum -c -; then
        "$bb" rm -f card.tar
        return 0
    fi
    "$bb" rm -f card.tar
    return 1
}

while [ "$("$bb" cat "/proc/$im_pid/comm" 2>/dev/null || true)" = im.elf ]; do
    if [ -e sd-disabled ]; then
        # Wait for the menu to finish OFF and retire the Mods root. A rapid
        # reinsertion must not cancel a pending disable or a failed rollback.
        if [ -e sd-stopped ] && validate_returned_card; then
            "$bb" rm -f sd-stopped sd-disabled
            echo MLV2_SD_REACTIVATED
        fi
    elif ! run_tmo "$reader" --present; then
        "$bb" rm -f sd-stopped
        : > sd-disabled
        echo MLV2_SD_PACKAGE_UNAVAILABLE
    fi
    "$bb" sleep 2
done
