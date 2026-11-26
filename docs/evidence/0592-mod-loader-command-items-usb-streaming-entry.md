# E0592 — Mod Loader v2 command items: a "USB Streaming" style entry in the Mods tab

Date: 2026-11-18. Offline Unicorn harness and the QEMU `sony-a6400` mod-development
panel with camera-captured inputs. No physical camera, SD card, or USB access.

## Question

The ZV-E10 starts UVC streaming from a menu entry called "USB Streaming". Pressing it returns
to the shooting screen with a "USB Streaming: Standby" notice. Can an a6400 UVC mod use the
same interaction from the Mods tab instead of an ON/OFF switch, and does that make UVC easier?

## Answer

- The interaction is a loader feature, separate from UVC. The Mods tab is drawn by the
  Mod Loader v2 core (`tools/native_camera/mlv2/core.c`), so a command item needs only loader
  code. It does not change what UVC still needs. The a6400 firmware has no UVC gadget stack
  (`usbg_video.ko`, `libInfraStreamingUsb.so` and the `/dev/usb/video` node are absent,
  E0316/E0321). The QEMU USB path stops before gadget start (E0589), and the physical
  EP0 status fix is still waiting for Run B26 (E0476).
- The ZV-E10 notice comes from the ZV-E10's own view code, which the a6400 `viewUnified2.so`
  does not contain. An a6400 mod would have to draw its own shooting-screen notice. That
  OSD path is not established, so this record does not claim a notice.

## Change

- `SMOD_V2_FLAG_ACTION` (16) in `mlv2.h`. `pack_mod_v2 --action` sets it.
- In the Mods row a command item shows no ON/OFF value: empty while idle, `Active` while it
  runs, `ERR` on failure.
- When an idle item is pressed, the core runs the normal ON path (hash check, hooks, `on()`).
  Only when ON succeeded does it queue the MENU key event (`0x15010102`, type 4). It uses the
  same three libObj calls as MenuTouchFull's verified KEY_MENU path (E0520): `operator new`
  `0x05e15974`, event constructor `0x05e14e20`, push `0x05e18d5d`, and the manager slot
  `0x07089984`. Each call is moved by the live libObj base. Without a known libObj base the
  core posts nothing and logs `action_close_unavailable`.
- Pressing a running item runs the normal OFF path and leaves the menu open. Auto-on is ignored
  for command items.
- Loader log: `action_close <base>` / `action_close_unavailable <base>`.

## Identities

| Item | Value |
| --- | --- |
| Build | `artifacts/tests/mlv2-action-build-1` (`build_wsl.sh`) |
| `core.bin` | 8128 B, FNV `0xee60f8ea`, SHA-256 `db869c73e249c670fb3e93185e9245ae1e95abeca2ba0a90b585b9e7abadd269` |
| `mlv2.req` | SHA-256 `99d255c58dc8547b38e1c2a835f122fa88f28a86666e11384b25187b25512f85` |
| `ActionTest.mod` | 116 B, `test_mod.c` packed `--action`, named "USB Streaming Test", SHA-256 `4a89a665362cc8d5806f167e6725f69de6c36258737b9904cb80e3abd2b05960` |
| QEMU | `artifacts/qemu-a6400/launcher-qemu/build/qemu-system-arm`, SHA-256 `200a3623dfb7580e81f0b777c7956a8b0cfaff4b8f223f8e156a93221502a64d` |
| Inputs | `artifacts/qemu-a6400/inputs-moddev-1` (E0524 captured disk) |

## Unicorn harness

```powershell
.\venv\Scripts\python.exe tools/native_camera/mlv2/test_mlv2_unicorn.py artifacts/tests/mlv2-action-build-1 artifacts/tests/arrivederci-v3-build-1/Arrivederci.mod artifacts/tests/mtf-v2-build-1 artifacts/tests/mtf-v2-reloc-build-1/MenuTouchFull.mod artifacts/tests/mtl-v2-build-1/ModsTabLook.mod
```

All 15 scenarios passed (83 checks, `artifacts/tests/mlv2-action-build-1/unicorn.txt`).
Scenarios 1–14 are the earlier ones and still pass. New scenario 15 covers:
the item listed with an empty value; a press that runs `on()` once and queues exactly
`ctor(ev, 0x15010102, 4, 1)` and `push(mgr, ev, 1)` to the live manager; `Active` after the
press and after a rescan; a second press that runs `off()`, posts nothing and clears the value;
and, without `/proc/self/maps`, a started mod with no event and `action_close_unavailable`.

## QEMU run

Receipts: `artifacts/qemu-a6400/e0592-action-item/`.

```bash
A6400_QEMU_DIR="$PWD/artifacts/qemu-a6400/launcher-qemu" EVSEL_LATE_US=500 python3 -u qemu-a6400/tools/rear_panel.py \
  --port 8650 --log artifacts/qemu-a6400/e0592-action-item/moddev.log --inputs artifacts/qemu-a6400/inputs-moddev-1 \
  --loader artifacts/tests/mlv2-action-build-1 --mod artifacts/tests/mlv2-action-build-1/ActionTest.mod \
  --mod artifacts/tests/mtl-v2-build-1/ModsTabLook.mod
```

| Step | Observation |
| --- | --- |
| Install | `view_bias=ff375000 testcmd_bias=001f8000 STATE hooks=stock`, `MLV2_RESULT=0 changed=1`, `core_load ee60f8ea` |
| Mods page | `Mods Tab Look  ON` and `USB Streaming Test` with no value (`02-action-row-selected.png`) |
| Center press | Menu closed to the shooting screen. Log: `mod_on 041da000`, `action_close 041da000` (`03-after-press-shooting.png`) |
| MENU again | Mods page shows `USB Streaming Test  Active` |
| Second center press | Menu stays open, value cleared. Log: `mod_off 041da000` (`04-second-press-stopped.png`, `loader-log-final.json`) |

The first attempt (`run1-moddev-qemu/`) used the separate `~/a6400-qemu-moddev` build.
Its LCD showed the opaque NTSC notice (E0591) throughout. The host-side key presses sent
for that run were not observed to arrive, so it is not evidence either way.
`01b-menu-key-via-host-curl-not-delivered.png` is a frame from the second run in which a host
curl press also had no effect. The browser panel's own requests delivered every key used above.

## Limits and next boundary

- The test mod only counts lifecycle calls. No UVC function, gadget, or streaming exists
  in this run, and no "USB Streaming" notice is drawn.
- The MENU-key close was observed in QEMU from the Mods page. It is not verified on the camera.
  Camera use needs an explicit go-ahead.
- A mod cannot yet stop itself (for example, from the movie button as on the ZV-E10).
  Stopping goes through the Mods row.
- Next: a real `UsbStreaming.mod` whose `on()` starts the RAM-only UVC path (Track B, after
  Run B26) and whose `off()` tears it down. Then a shooting-screen notice hook, and in QEMU a
  gadget-side `cxd90045-dwc3` model past the E0589 boundary.
