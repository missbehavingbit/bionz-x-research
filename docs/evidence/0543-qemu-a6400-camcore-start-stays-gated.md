# E0543 — QEMU `sony-a6400`: CamCore start is still gated, so PowerOn never runs

Date: 2026-11-16. **Strictly offline.** Builds on E0535. The viewfinder-versus-monitor skin is left as recorded in E0542.

Labels: [C] confirmed by disassembly or a run receipt, [I] inference, [A] assumption.

## 0. Short answer

The frame loop reaches `HS_Sequence`, and `en144` still ends at `0x3000232`. Imager `PowerOn` is a virtual method with no direct call. The `VD_Pre` path that would start CamCore returns before that call unless two gates pass, and the second gate's candidate flag is still zero.

## 1. What was measured

`av-cam.bin` is the ORIL image linked at `0x635C6000` and loaded at PA `0x035C6000`.

`IMG:PowerOn Fail !!` is logged from `0x6378f474`. The `PowerOn` body is the function at `0x6379093c` [C]. Its only reference is the thumb pointer `0x6379093d` at `0x644FB4E4`, inside a function table. There is no `BL` to `0x6379093c` [C]. The boot log still has no `PowerOn`, `P2S`, or `SA` start line.

The function at `0x6391e614` is called from `0x6392738e` on the way through `VD_Pre` [C]. It does three things:

1. Calls virtual slot `+0x28`. A zero return leaves the function immediately.
2. Copies the word at object `+0xE8` over `+0xEC`, then calls virtual slot `+0x34`.
3. Tests bit 1 of a byte at offset `0x33` in the object whose pointer is stored at link address `0x645A6BE0`. When that bit is set it calls virtual slot `+0x38` and logs `FW: VD_Pre CamCore start`.

`tools/a6400_probe.py --until SetContentTarget` reached the checkpoint at 47.7 s, status 0. `pmemsave 0x043334E8 256` (the PA that corresponds to link VA `0x643334E8` if the load window is VA minus `0x60000000`) is 256 zero bytes [C]. That is also what the file contains at that offset. `en144` ends at `0x3000232`, ch51 base 0 [C].

## 2. What this does not show

The zero page matches the untouched file image, so it confirms the flag only together with the VA-to-PA assumption [A]. Slot `+0x28` was not sampled. No model change.

## 3. Next boundary

What virtual slot `+0x28` returns on each `VD_Pre`, and which write sets bit 1 of the byte at `0x645A6BE0 -> +0x33`. CamCore start, `PowerOn`, and the P2S kick stay behind those two gates.
