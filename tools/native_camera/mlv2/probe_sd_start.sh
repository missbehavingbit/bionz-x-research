#!/bin/sh
# Invoke from a candidate startup path with a fresh, operator-chosen run token.
set -eu

[ "$#" -eq 1 ] || exit 2
case "$1" in
    ''|*[!A-Za-z0-9_-]*) exit 2 ;;
esac
[ "${#1}" -le 48 ] || exit 2

# The marker is permitted only on the camera's existing RAM filesystem.
ram_ready=0
while read -r source mount filesystem options rest; do
    [ "$mount" = /tmp_bt ] || continue
    [ "$filesystem" = tmpfs ] || exit 3
    case ",$options," in
        *,rw,*) ram_ready=1 ;;
    esac
done < /proc/mounts
[ "$ram_ready" -eq 1 ] || exit 3

read -r boot_id < /proc/sys/kernel/random/boot_id
read -r uptime idle < /proc/uptime

umask 077
receipt="/tmp_bt/mlv2-start-probe-$1"
# Refuse a stale token or a pre-existing path, including a symlink.
mkdir "$receipt" || exit 4
printf 'MLV2_START_PROBE token=%s boot_id=%s uptime=%s pid=%s\n' \
    "$1" "$boot_id" "$uptime" "$$" > "$receipt/receipt"
