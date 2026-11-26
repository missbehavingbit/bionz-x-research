# E0531 — QEMU `sony-a6400`: Sugilite P3D/PPF draws the shooting-screen OSD; the YUV layer stays off

Date: 2026-11-16. **Strictly offline.** Builds on E0530.

Labels: [C] confirmed by a run receipt, [I] inference, [A] assumption.

## 0. Short answer

1. **The completion-only GPU stub was the black panel [C].** `Oein/a5000-emulator`
   `dmp_smaph.c` and `dmp_shader.c` (GPL-2.0, Park SungHyun) execute the same BAR
   the a6400 driver uses (`0xF1000000`, IRQ 76): PSC fill, PPF copy, P3D command
   list. Replacing `cxd90045.sugilite_bar` with that model does not change the time
   to `SetContentTarget` (50.0 s and 49.3 s wall, same order as the E0530 probe).
2. **OSD scan-out is the a5000 path [C].** The first logged PPF copies are
   614400 bytes (640×480×2) to `0x23A80000` and `0x23B16000`. VPH AIF ch53 then
   alternates between those two addresses, `+0x24 = 0x500`, 480 lines,
   `0x144 = 0x3000232`. Bit 1 of `0x144` is set (OSD on). Bit 0 is clear (YUV off).
3. **The panel is the shooting screen, not a full frame [C].** At `SetContentTarget`
   the console has 102 non-black pixels, the program mark. Five guest seconds later
   it has 1597 non-black pixels: `P` at the top left, a battery icon reading `100%`
   at the top right, and the exposure scale `-5 4 3 2 1 0 1 2 3 4 5+` along the
   bottom. The centre stays black. Continued ch53 flips across those five seconds
   mean VDF is still committing OSD buffers [I], so `PIN_SEND_COMP` is not stuck
   on the second swap.
4. **Next blocker [C].** `0x144` bit 0 is still 0 after those five seconds, so the
   panel YUV layer (AIF ch51) is never enabled. There is no live-view image to
   composite. a5000 `RE_LIVEVIEW.md`, `RE_LIVEVIEW_PIPELINE.md` and
   `RE_LIVEVIEW_IMAGE.md` are the place to look for what turns that bit on.

## 1. Receipts

| Artifact | SHA-256 |
| --- | --- |
| `artifacts/qemu-a6400/e0530/panel-at-setcontenttarget.ppm` (102 non-black pixels) | `e3789bcf7617325fce35575d7d7493aac878c4a0137f4e401fd04f6d5ef60980` |
| `artifacts/qemu-a6400/e0530/panel-plus-5s.ppm` (1597 non-black pixels) | `deec46fe3f50268e26c66ae6293e6722e840f9501fcc3cc9a5cabcc8471fe341` |
| `artifacts/qemu-a6400/e0530/panel-plus-5s-top.png` | `d0526656c16b11d07232d0eec13484394cdb701b9b2ad1d5b9dc3334710e942a` |
| `artifacts/qemu-a6400/e0530/panel-plus-5s-scale.png` | `a7d313ba93f1f002028c209194bb29bc176beb4943b7e62b86af28f6c16afd45` |
| `artifacts/qemu-a6400/e0530/probe-plus-5s.log` | `6d393a613e11ae39540d27ea95f6302091bfb4c734f398ab9d19c9e21500e413` |
| `qemu-a6400/qemu-overlay/hw/display/dmp_smaph.c` | `438e8737a9bc1bad3771259bdfeb68f2a0bd786323122143da494dfa1453b672` |
| `qemu-a6400/qemu-overlay/hw/display/dmp_shader.c` | `15c68b4f43949b15ba0066ce3f936500b8f341fb9f82bd338c6e1cc15838469a` |
| `qemu-a6400/qemu-overlay/include/hw/display/dmp_shader.h` | `bce5449b4837619c788d0d59244df082a0d61eee9177afc42b93d4c71d05bac8` |
| `qemu-a6400/qemu-overlay/hw/misc/cxd90014_vph.c` | `6d0eabddf71e845721f3c4b36196a9b8eb16ec5316532542c0dce23962b02c63` |
| `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` | `dcb78b96cb584ebd4ef3a85b2922a80c6b28fe738a310df46dfef33c501ec01b` |

The probe log is from the build that printed every ch53 base change. The tree now
prints the first four only. The pixel result does not depend on that print.

Commands, from `qemu-a6400/` in WSL, after `ninja qemu-system-arm`:

```bash
python3 tools/a6400_probe.py --until 'SetContentTarget' --timeout 480 --poll 2 \
  --monitor 'screendump /home/administrator/a6400-runs/e0530-panel.ppm vph 0' \
  --log /home/administrator/a6400-runs/e0530.log
python3 tools/a6400_probe.py --until 'SetContentTarget' --timeout 420 --poll 2 \
  --cmd 'sleep 5' \
  --monitor 'screendump /home/administrator/a6400-runs/e0530b-panel.ppm vph 0' \
  --log /home/administrator/a6400-runs/e0530b.log
```

## 2. Assumptions and limits

- Glyph identity (`P`, `100%`, the exposure scale) is read from the panel dump [C].
  That this is the normal shooting screen, rather than a partial draw of some other
  view, is an inference from the layout [I].
- No live-view, menu, or touch path was exercised. Nothing here was run on the camera.

## 3. Next boundary

Why VPH `0x144` bit 0 stays clear, so AIF ch51 never scans a YUV frame. Start from
the a5000 live-view notes named above.
