# Full-Menu Touch Mod (`MTF2`)

RAM-only dual-hook touch mod for Sony `ILCE-6400` (`im.elf`). Original standalone version: [E0520](../../docs/evidence/0520-mtf2-full-menu-touch-verified-on-a6400.md).
Loader installation and combined physical test: [E0522](../../docs/evidence/0522-mods-tab-icon-and-uxd-image-container.md#13-default-artwork-in-the-mod-loader-and-menutouchfull-coexistence).

## Verified Mod Loader build (v3, 2026-11-15)

| File | SHA-256 |
| --- | --- |
| `MenuTouchFull.mod` (SMOD, 2940 B, code 2160 B at `0x0700d708`, state 632 B at `0x07143920`) | `334a3dd491d04a87e5318694ac652fca65dfe7cd2575e7e6886620bc282b1b80` |

Copy [MenuTouchFull.mod](MenuTouchFull.mod) to the SD card's `mods/` directory, or stage it under
`/tmp_bt/mods/` for a single boot. Install the [Mod Loader](../README.md#option-2-install-on-a-physical-ilce-6400-ram-only),
then open MENU → Mods → `Menu Touch Full` and press the center button. The `.mod` carries its MTF2
state header (`SMOD_FLAG_STATE_INIT`).

## Capabilities

- Tab taps in every tab, including with the cursor on the tab bar and from My Menu / Mods
- Page-dot taps and horizontal swipe paging (`|dx| >= 48`) in ordinary setting tabs
- 1st-tap row select, 2nd-tap popup open (`0x15010052u` `KEY_ENTER`)
- Popup option tap: live select + apply without closing (`menu + 0x1fc`, `0x157f8ca5u`)
- Popup vertical swipe scrolling (`|dy| >= 36`)
- Outside-popup tap to close only the popup (`0x15010102u` `KEY_MENU`)
- Rows/options inside My Menu and Mods are intentionally not touch-handled

## Rebuild

```bash
wsl -e bash mods/menu_touch_full/build_mtf2_wsl.sh artifacts/hardware/mtf2_v3/menu_touch_full.elf -DMT_MAX_TABS=7u
```

```bash
.\venv\Scripts\python.exe -m tools.research.pack_mod --elf artifacts/hardware/mtf2_v3/menu_touch_full.elf --no-tab-patch
```

`MT_MAX_TABS=7u` accounts for the Mod Loader's injected 7th tab. The standalone E0520 build (6 tabs) is preserved as
`artifacts/hardware/menu_touch_full.elf`.

## SMOD v2 build for Mod Loader v2 (2026-11-17)

Status: **usable** as part of modset v2 (see `docs/MOD_LOADER_V2_DESIGN.md`).

`-DMT_SMOD_V2` builds the same handlers position-independently. The 632-byte MTF2 state moves
from the fixed `0x07143920` into the zero-filled bss that Mod Loader v2 maps after the code
(`__smod_bss`). The `mt_life` lifecycle entry publishes the state after both hooks are applied
and clears it before they are reverted. Both hooks keep the verified v1 targets and 12-byte
prologues (`0x06309d48`, `0x06309e54`). The v1 source path is unchanged: a rebuild with the v1
command still yields the shipped code (FNV `0xbf967582`).

```powershell
wsl.exe --exec bash mods/menu_touch_full/build_mtf_v2_wsl.sh artifacts/tests/NEW_MTF_V2_BUILD
.\venv\Scripts\python.exe mods/menu_touch_full/test_mtf_v2_unicorn.py artifacts/tests/NEW_MTF_V2_BUILD
```

Build `artifacts/tests/mtf-v2-build-1`: `MenuTouchFull.mod` 2764 B (code 2616 B, bss 632 B),
SHA-256 `5ac0918001477dad1194d3f5ca68bc18a3398eec29881d46969ea5c90cd54c48`. The mod-level
Unicorn test loads it at two bases and checks hook fixups, forwarding before ON and after OFF,
the replayed prologue registers, the press capture, and the early release guards. Scenario 10 of
`tools/native_camera/mlv2/test_mlv2_unicorn.py` runs it through the real v2 core: an enabled
v1 copy imported from a v1 loader, the file replaced (`ON*`), v1 OFF, v2 ON/OFF. Menu actions
past the early guards still call stubs only. On the camera (2026-11-17, RAM only) it loaded
through the v2 core and handled tab taps in all seven tabs. Page, row, swipe, popup and OFF still
need a camera pass. The SD package still ships the v1 file.

### QEMU native touch replay

The relocatable v2 build now checks the receiver against the live
`WidgetSystem::sys_getInstance()` result instead of a fixed camera heap
address. Only the original singleton is accepted. The verified v1
binary still rebuilds byte-for-byte.

[Matched guest replays](../../docs/evidence/mods/2026-11-19-firmware-qualification.md#native-touch-follow-up)
confirmed a tab tap and horizontal swipe through Sony's native touch
callback. Additional matched native reports changed page dots, selected
and opened a row, and moved a popup highlight by tap or vertical swipe
only with v2 ON. An outside tap dismissed the popup only with v2 ON.
The popup did not commit the selected value in that guest, even with
the stock keys. Scrolling a list with hidden options and physical
digitizer input were not qualified by these runs.
