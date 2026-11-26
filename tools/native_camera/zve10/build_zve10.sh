#!/bin/bash
# Build the ZV-E10 (FW 2.03) MenuTouchFull first-pass port under WSL.
#
# !!! FIRST-PASS PORT — NOT TESTED ON HARDWARE. Do NOT deploy to a camera. !!!
# This script only cross-compiles the payload and the installer guard. It performs no
# camera access, no flashing, no ptrace of anything but a compile-time check pass.
#
# Toolchain: the a6400 ports use ARM Linux EABI GCC through WSL on Windows
# (`build_mod_loader_wsl.sh`). Same pattern here: `arm-linux-gnueabi-gcc`. Do NOT
# download a new toolchain; use the existing WSL environment. If the binary is missing,
# install nothing automatically — document and stop.
#
# ADDRESS MODEL (blueprint docs/porting/zv-e10/zv-e10-touch-mod-blueprint.md §0):
#   ZV-E10 §1 addresses are FILE OFFSETS / ELF VADRs of the ZV-E10 libraries.
#   .text: file == vaddr. Second segment (.data.rel.ro/.got/.data/.bss):
#   vaddr = file + 0x8000 (CautionConfig.so: +0x7000). Runtime address = load base +
#   offset; the load base is UNKNOWN until boot. The payload keeps the a6400's
#   compile-time-absolute-constant shape, so EVERY replacement below is a file-offset
#   constant that MUST be rebased with the maps-derived load base (or -D overridden)
#   before the mod can run. Struct offsets are load-base independent.
#
# INJECTION HOME: -Ttext is set to the ZV-E10 libObj.so RX zero-run candidate
#   (0x011964ee, largest RX zero run = 16,254 B; ~45 KB total RX zeros in libObj.so;
#   viewUnified2.so alternative: 0x00a6ea30, 12,000 B; CautionConfig.so: NONE).
#   The a6400 used -Ttext=0x0700d708 (absolute); using the file offset here only keeps
#   the build reproducible — it is NOT a bootable image.
#
# -----------------------------------------------------------------------------
# WHAT CHANGED VS THE a6400 ORIGINAL (old -> new), FW 2.00 -> ZV-E10 FW 2.03
# -----------------------------------------------------------------------------
# menu_touch_full.c / menu_touch_native.c:
#   0x956f0      WidgetSystem singleton          -> 0x0145cb1c  (.bss VA; GOT slot 0x013df230 via sys_getInstance @ 0x00616fe4)
#   0x15f1f1d0   PAS_MenuListTab vtable (addr pt)-> 0xc01e28    (_ZTV @ 0xc01e20; RTTI @ 0xc01fe8; name @ 0xa8b2a0)
#   0x15EC8270   ViewSettingMenu vtable (addr pt)-> 0xbaabb0    (_ZTV @ 0xbaaba8; RTTI @ 0xbaada4; name @ 0x8ef14f)
#   0x16c183e8   CmnViewSettingNode vtable       -> 0x00fab968  (CautionConfig.so; VA; FILE 0xfa3968; RTTI @ 0xfab954)
#   0x07089984   EventManager singleton holder   -> 0x014920e0  (g_pEvtMgr, .bss VA; STT_OBJECT, exported; GOT 0x013ded14)
#   0x05E14E20   Event ctor                      -> 0x008a01e8  (Event::Event(ulong,u8,u8); copy 0x008a021f; setParamList 0x008a0269)
#   0x05E15974   operator new                    -> 0x00108e0c  (imported _Znwj PLT stub; GOT 0x013d80a8; NOT locally defined)
#   0x05E18D5D   EventManager::push              -> 0x008a1794  (PLT 0x0010c2a8)
#   0x15b92045   enter_selected                  -> 0x86292c    (PAS_MenuListTab::openLevel2Popup; called from 0x303260; signature TO VERIFY)
#   0x15b9134d   popup_up                        -> 0x861b4e    (candidate setPopupPrev; PAS_MenuListPopup; TO VERIFY)
#   0x15b91377   popup_down                      -> 0x861b78    (candidate setPopupNext; PAS_MenuListPopup; TO VERIFY)
#   0x0700d708   CODE (RX injection home, a6400)  -> 0x011964ee  (libObj.so RX zero run, FILE OFFSET; 16,254 B; rebase required)
#   0x7143920    STATE (RW home, a6400)           -> UNCHANGED, marked TO VERIFY (no ZV-E10 RW home located)
#   0x6309019    get_pos (WidgetSystem pos getter)-> UNCHANGED, TO VERIFY (no blueprint equivalent)
#   0x6305081    is_hit                          -> UNCHANGED, TO VERIFY (blueprint: Widget::isHit TBD)
#   0x15b91431   binding (sync_refresh)          -> UNCHANGED, TO VERIFY (blueprint: binding TBD)
#   0x15b92111   refresh (sync_refresh)          -> UNCHANGED, TO VERIFY (candidates: PAS vtable idx108 @ 0x8629f8; 0x2f5998 post-move refresh)
#   0x16925c17   selected_index                  -> UNCHANGED, TO VERIFY (candidate: getHighlightItemPos @ 0x860b36; signature unverified)
#   0x16925bc7   indexed_child                   -> UNCHANGED, TO VERIFY (no blueprint equivalent)
#   0x169260db   set_selected_index              -> UNCHANGED, TO VERIFY (candidates: idx47 setItemSelectedByIndex (no addr) / idx48 setItemSelectedBySRNum @ 0xb8e60c file; SRNum != index)
#   0x15b90535   main_state                      -> UNCHANGED, TO VERIFY (blueprint: TBD)
#   0x631e815    inherited_mode                  -> UNCHANGED, TO VERIFY (no blueprint equivalent)
#   0x15b95b61   popup_open                      -> UNCHANGED, TO VERIFY (blueprint: TBD)
#   0x15b9184d   cancel_popup                    -> UNCHANGED, TO VERIFY (blueprint: TBD)
#   0x15b9638b   confirm_popup                   -> UNCHANGED, TO VERIFY (blueprint: TBD)
#   0x157f8ca5   apply_popup                     -> UNCHANGED, TO VERIFY (blueprint: TBD)
#   0x15b96f11   set_popup_row                   -> UNCHANGED, TO VERIFY (candidates: CmnViewSettingNode idx47/idx48; see above)
#   0x62e27df    lookup (hash-key registry)      -> UNCHANGED, TO VERIFY (no blueprint equivalent; hash args 0xaadca2c7/0xdfdc8ddd/0xa056f604 expected framework-stable, UNVERIFIED)
#   0x0713D7EC   ViewSettingMenu-instance list holder -> UNCHANGED, TO VERIFY (not in blueprint)
#   0x16c183e8   CmnViewSettingNode vtable (all four compare sites) -> 0x00fab968
#   TID 380      UI thread id                    -> UNCHANGED, TO VERIFY (-DMT_UI_TID=<tid>; ZV-E10 UI tid unknown)
#   0x186ac (menu+0x186ac root-tree ptr)          -> UNCHANGED — struct layout CONFIRMED byte-identical (blueprint §0)
#   Struct offsets (tabs 0x0b9c/0x220, pages 0x166b4/0x220, rows 0x9350/0x1118, popup regions
#     0x1070/0x398, 0x36a8/0x220, 0x64c, 0x60d8, 0x5c04, 0x2780, 0x1f8, 0x1d8, 0x21, 0x14c,
#     0x1c, 0x1fc, 0x6490, 0x6480, 0x648c, 0x648d, 0x6490, 0xfb20)
#                                               -> UNCHANGED — UNVERIFIED on ZV-E10 (geometry/strides "may match, may not", blueprint §2.6)
#   Event ids 0x15010052 (KEY_ENTER-ish), 0x15010102 (KEY_MENU-ish)
#                                               -> UNCHANGED — framework event ids, not addresses
# menu_touch_full_trampoline.S:
#   mt_original_release target 0x06309d55        -> 0x00628495    (postMouseRelease @ 0x00628488 + 0x0c | Thumb; 12-byte prologue UNVERIFIED)
#   mt_original_press   target 0x06309e61        -> 0x0062842d    (postMousePress   @ 0x00628420 + 0x0c | Thumb; 12-byte prologue UNVERIFIED)
# native_menu_touch_full_guard.c (installer):
#   CODE 0x700d708   -> 0x011964ee (libObj.so RX zero run, FILE OFFSET; rebase required)
#   STATE 0x7143920  -> UNCHANGED, TO VERIFY
#   HOOK_REL 0x6309d48 -> 0x00628488 (postMouseRelease implementation; PLT 0x0011a128) — prologue UNVERIFIED, fails closed
#   HOOK_PRS 0x6309e54 -> 0x00628420 (postMousePress implementation; PLT 0x001189b4)   — prologue UNVERIFIED, fails closed
#   orig_rel/orig_prs bytes (a6400 prologues)    -> UNCHANGED placeholder, TO VERIFY (deliberately non-matching -> install fails closed)
#   PID 149                                     -> UNCHANGED default, TO VERIFY (-DMT_ZVE10_PID=<pid>; discover at runtime; comm likely "im.elf")
#   "/libObj.so" maps identity check            -> UNCHANGED, TO VERIFY (+ viewUnified2.so/CautionConfig.so identity checks to be added)
#   "/tmp_bt/menu_touch_full.req"               -> UNCHANGED, TO VERIFY (ZV-E10 /tmp_bt existence unverified)
# menu_touch_policy.c/.h, menu_touch_native.h:    no addresses (policy) — copied unchanged
#   (menu_touch_policy.c comment updated to reference the ZV-E10 hook identities)
# -----------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=${1:-artifacts/hardware/zve10}
NC=tools/native_camera/zve10
CC=arm-linux-gnueabi-gcc
mkdir -p "$OUT"

common=(-mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding
        -fno-builtin -nostdlib -nostartfiles -fno-pic -fno-stack-protector
        -fomit-frame-pointer -Wall -Wextra)

"$CC" "${common[@]}" -Wno-unused-parameter -Werror \
    -Wl,-Ttext=0x011964ee -Wl,--build-id=none -Wl,-e,mt_full_release \
    "$NC/menu_touch_full_trampoline.S" "$NC/menu_touch_full.c" -o "$OUT/menu_touch_full_zve10.elf"

"$CC" "${common[@]}" -Werror -DMT_UI_TID=380 \
    -c "$NC/menu_touch_native.c" -o "$OUT/menu_touch_native_zve10.o"
"$CC" "${common[@]}" -Werror -c "$NC/menu_touch_policy.c" -o "$OUT/menu_touch_policy_zve10.o"

"$CC" -static -O2 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=softfp \
    "$NC/native_menu_touch_full_guard.c" -o "$OUT/native_menu_touch_full_guard_zve10_arm"

for payload in "$OUT/menu_touch_full_zve10.elf"; do
    disassembly=$(arm-linux-gnueabi-objdump -d "$payload")
    if [[ $disassembly =~ __aeabi_.*div ]]; then
        echo "Unexpected runtime division helper in $payload" >&2
        exit 1
    fi
done

echo "Built (UNTESTED first-pass port): $OUT/menu_touch_full_zve10.elf"
echo "NOTE: -Ttext=0x011964ee is the libObj.so RX zero-run FILE OFFSET, not a runtime address."
echo "A maps-derived rebase (or -D overrides once the load base is known) is REQUIRED."
echo "Do NOT deploy to a camera. See $NC/README.md."
