# E0541 — QEMU `sony-a6400`: forcing `isOutputEvf` false does not switch the panel skin

Date: 2026-11-16. **Strictly offline.** Builds on E0540.

Labels: [C] confirmed by a run receipt, [I] inference, [A] assumption.

## 0. Short answer

`CmnViewFocus::isOutputEvf` is the predicate `getOutputTypeInfo` calls first. Forcing it to return 0 changes 765 pixels of the focus marks and leaves the viewfinder readout. It is not the rear-LCD image-set switch.

## 1. What was measured

`viewUnified2.so` defines `CmnViewFocus::isOutputEvf` at file `0x2cc288` (96 bytes) [C]. It reads model `@M006` parameter `0x1200600b` and returns 1 only when that value is 2. `0x1200600b` is not a BK4 unit: subsystem `0x1200` is past the 691 subsystems in `Backup.bin` [C].

One-shot qcow2 overlay only. At disk `0x35F6BECA` (file offset `0x2cc2ca`, `mov r0, r4`) the two bytes `20 46` were replaced with `00 20` (`movs r0, #0`). `emmc.img` was not modified. Probe: `tools/a6400_probe.py --until SetContentTarget`, then `usleep 3000000` and `screendump`. Default `-icount shift=0,sleep=on`.

| Run | Checkpoint | `en144` | Frame SHA-256 |
| --- | --- | --- | --- |
| e0550 | 48.3 s, done 58.3 s, status 0 | `0x3000232` | `e26b3bee28b653c49d4319df22a7d837993ac44c576aac31a73b9a24c2de7ee0` |

The E0531 viewfinder frame is `deec46fe3f50268e26c66ae6293e6722e840f9501fcc3cc9a5cabcc8471fe341`. The two dumps differ in 765 pixels, all inside x 119–455, y 3–241: the focus marks. Mode `P`, `NO CARD`, battery `100%`, and the exposure scale are unchanged [C].

## 2. What this does not show

The 853-wide stretch was not applied. No model change. The live-view layer is still off.

## 3. Next boundary

The loader that opens `image_DSC_Only_169.uxc` instead of `image_EVF.uxc`. Both names are records in the `uxa` catalog (inode 8263 of the `/usr` filesystem, 10092 bytes), and neither name appears as a string in `viewUnified2.so` or `viewUnified4.so` [C].
