#!/usr/bin/env bash
# Launch the interactive panel with the locally qualified QEMU build.
#
# Mod development mode starts when both inputs exist:
#   A6400_MODDEV_INPUTS  prepared camera-captured inputs (default artifacts/qemu-a6400/inputs-moddev-1)
#   A6400_MODDEV_LOADER  Mod Loader v2 build with an ML2Q v2 request
#                        (default: newest artifacts/tests/mlv2-layout-build-*)
# Then the panel installs the loader in the guest and its toolbar loads and hot-reloads mods.
# Set A6400_MODDEV=0 to start the plain panel on the default disk.
set -euo pipefail
cd "$(dirname "$0")/../.."
local_build="$PWD/artifacts/qemu-a6400/launcher-qemu"
if [[ -z "${A6400_QEMU_DIR:-}" && -x "$local_build/build/qemu-system-arm" ]]; then
    export A6400_QEMU_DIR="$local_build"
fi
export EVSEL_LATE_US="${EVSEL_LATE_US:-500}"
logs=artifacts/qemu-a6400/launcher-logs
mkdir -p "$logs"

extra=()
if [[ "${A6400_MODDEV:-1}" != 0 ]]; then
    inputs="${A6400_MODDEV_INPUTS:-artifacts/qemu-a6400/inputs-moddev-1}"
    is_a6400a=0
    if [[ -f "$inputs/boot-manifest.json" ]] && grep -q '"ilce_6400a"' "$inputs/boot-manifest.json"; then
        is_a6400a=1
    fi
    loader="${A6400_MODDEV_LOADER:-}"
    if [[ -z "$loader" ]]; then
        glob_pat="artifacts/tests/mlv2-layout-build-*"
        [[ "$is_a6400a" == 1 ]] && glob_pat="artifacts/tests/mlv2-ilce6400a-*"
        for d in $(ls -td $glob_pat 2>/dev/null); do
            if [[ -f "$d/core.bin" && -f "$d/mlv2_guard_arm" && -f "$d/mlv2.req" ]] &&
               [[ "$(od -An -tu4 -j4 -N4 "$d/mlv2.req" | tr -d ' ')" == 2 ]]; then
                loader="$d"
                break
            fi
        done
    fi
    if [[ -f "$inputs/boot-manifest.json" && -n "$loader" ]]; then
        echo "Mod development: inputs $inputs, loader $loader"
        extra=(--inputs "$inputs" --loader "$loader")
        # The Mods tab artwork (auto-on SMOD v2); A6400_MODDEV_TABLOOK= skips it.
        if [[ "$is_a6400a" == 1 ]]; then
            default_tablook="mods/ilce_6400a/ModsTabLook.mod"
        else
            default_tablook="$(ls -td artifacts/tests/mtl-v2-build-*/ModsTabLook.mod 2>/dev/null | head -1)"
        fi
        tablook="${A6400_MODDEV_TABLOOK-$default_tablook}"
        if [[ -n "$tablook" && -f "$tablook" ]]; then
            echo "Mods tab artwork: $tablook"
            extra+=(--mod "$tablook")
        fi
        if [[ "${A6400_UVC_TEST:-0}" == 1 ]]; then
            echo "UVC Streaming Test enabled (--uvc-test)"
            extra+=(--uvc-test)
        fi
    else
        echo "Mod development needs prepared inputs ($inputs) and a layout-aware loader build;" \
             "starting the plain panel. See qemu-a6400/MOD_DEVELOPMENT.md."
    fi
fi
exec python3 -u qemu-a6400/tools/rear_panel.py --port "${1:-8641}" \
    --log "$logs/panel-$(date -u +%Y%m%dT%H%M%SZ)-$$.log" "${extra[@]}"
