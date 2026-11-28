# FocusBracket.mod — ILCE-7C (SMOD v2)

**Status: built, not run.** It has not run in QEMU (no still capture, no lens model yet) and not on a camera. See [E9202](../../docs/evidence/9202-ilce-7c-menu-after-liveview-and-focus-bracket.md).

The mod adds focus bracketing to the **Mods** tab of Mod Loader v2. The tab is used because stock ILCE-7C `viewUnified2.so` has no `BracketFocus` node.

## Menu

| Item | Values |
| --- | --- |
| Mode | Landscape (step 8, 5 shots) / Macro (step 2, 40 shots, 1 s interval) / Custom |
| Step Width | 1 (Narrow) – 10 (Wide) |
| Shots | 5, 15, 35, 40, 60, 100 |
| Order | `0 → +` / `0 → − → +` |
| Exp Smooth | Off / On |
| Interval | Shortest, 1, 2, 3, 5, 10 s |

## How it works

All addresses are `libObj.so` offsets, from reference base `0x20000000`.

- **Focus step:** `IssueCommandSync` (`0x10b2a4`) on `ObjCamera` (`get_obj_handle(8)`, `0x34d60d`), message `(0x3100, 0x6530)`, `PARAMID_STEP_FOCUS_DIR 0x9c40` = `0x68c` near / `0x68d` far.
- **Shutter:** `EventManager::push` S2 press/release, `0x15010032` / `0x15010031`.
- **Save sync:** hook on `WriteStillComp` (`0x902548`). The trampoline replays the prologue `0x902548..0x902557` and resumes at `0x902558`.

## Build

```sh
mods/focus_bracket_a7c/build.sh        # -> mods/focus_bracket_a7c/FocusBracket.mod
```

The committed `FocusBracket.mod` is byte-identical to the output of this script.

SHA-256: `b48e3f81489d39489ac567aa85b7c2f6f492b999bb687a512555e57067574f30`

## Run in QEMU

```sh
cd qemu-a7c
python3 tools/a7c_panel.py --card none --mod ../mods/focus_bracket_a7c/FocusBracket.mod --env A7C_FRESH=1
```
