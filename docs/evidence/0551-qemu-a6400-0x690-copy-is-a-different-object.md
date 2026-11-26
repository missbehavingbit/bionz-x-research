# E0551 — QEMU `sony-a6400`: the `+0x690` copy at `0x63988EAA` writes a different object, and its gate byte is 0

Date: 2026-11-17. **Strictly offline.** Builds on E0549 and E0550.

Labels: [C] confirmed by disassembly or a run receipt, [I] inference, [A] assumption.

## 0. Short answer

The halfword store at `0x63988EAA` is not the store that keeps module 1 at `0x0100`. It writes `*(0x645A21E4 + 0x3EB0) + 0x690`. At the step-12 stall that pointer is `0x6435CC38`, and the halfword there is `0x0000`. The store runs only when the byte at `*(0x645A21E4 + 0x623C) + 0x1F` equals 5. That byte is 0, and the eight bytes at `0x6433BC70` are zero. The sequencer index is still 12. No device model changed.

## 1. What the instructions do

`av-cam.bin` is the ORIL image linked at `0x635C6000` [C]. A byte search for Thumb-2 `STRH` with immediate `0x690` finds six sites: `0x63988EAA`, `0x63B45E7C`, `0x63B475B4`, `0x63B48AE4`, `0x63BAB0FA`, and `0x63D3D6DC` [C].

`0x63988310` is the function that reaches `0x63988EAA`. Its base register `r6` is `0x645A21E4` [C]. Before the copy:

```
63988b56  ldr r3, [pc, #0x3ec]      ; literal 0x623c at 0x63988f44
63988b58  str.w r0, [r4, #0x128]
63988b5c  ldr r3, [r6, r3]
63988b5e  ldrb r3, [r3, #0x1f]
63988b60  cmp r3, #5
63988b62  bne.w #0x63988f1a
```

A value other than 5 skips the copy [C]. When the byte is 5, bit 1 of `1 << [r4]` selects `0x63988DDE`, which loads `*(r6 + 0x3EB0)` and then executes `ldrh r2, [r4, #0x126]; strh r2, [r3, #0x690]` [C]. The other bit tests in that block write `+0xC8D` or `+0xF08` on other bases, not `+0x690` [C].

`0x63B48AE4` stores a small counter. `0x63B475B4` stores the constant 1. `0x63B45E7C` copies a halfword out of a stacked message. `0x63BAB0FA` copies a halfword from a BSS address. `0x63D3D6DC` copies `+0x690` from one object to another [C]. None of those instructions names `0x64351898`. Which register base is that object is not identified [A].

## 2. What one boot showed

`tools/a6400_probe.py --until SetContentTarget --cmd "sleep 3"`, status 0.

First sample, checkpoint 46.1 s, done 66.0 s, log `/home/administrator/a6400-runs/e0551.log` [C]:

| PA | value |
| --- | --- |
| `0x045A8420` (`0x645A21E4+0x623C`) | `0x6433BC70` |
| `0x045A6094` (`0x645A21E4+0x3EB0`) | `0x6435CC38` |
| `0x04330610` | `0x0000002B`, `0x0000000C` |
| `0x04330618` | `0x0105 0x0285 0x0320 0x0400` |
| `0x04330632` | `0x0100 0x0285 0x0320 0x0400` |
| `0x04351F28` | `0x0100` |

`0x04330614` is the sequencer index. The value `0x0C` is step 12 [C]. The same boot printed `[VPH] YUV layer still off at vsync 600 en144=0x3000232 ch51=0x00000000` [C].

Second sample, checkpoint 45.8 s, done 66.2 s, log `/home/administrator/a6400-runs/e0551b.log` [C]:

| PA | value |
| --- | --- |
| `0x0433BC8F` | `0x00` |
| `0x0433BC70` | eight zero bytes |
| `0x0435CC38` | `0x00000000 0x00000000 0x00000780 0x00000000` |
| `0x0435D2C8` (`0x6435CC38+0x690`) | `0x0000` |
| `0x04351F1C` (`0x64351898+0x684`) | `0x0A 0xC9 0x6C 0x00` |
| `0x04330614` | `0x0000000C` |

`0x0A 0xC9 0x6C 0x00` is bytes 2–5 of CS-command state 5 at `0x63E1F976 + 5*12` (`00 00 0A C9 6C 00 01 00 40 14 35 00`) [C]. Those bytes sit in the module object. The halfword at `+0x690` is still `0x0100` [C]. The zero byte at `0x6433BC8F` is why `0x63988B62` skips the copy into `0x6435CC38`, whose own `+0x690` stays `0x0000` [C].

## 3. Boundary

No device model changed. The step-12 pair is still `0x0105` against `0x0100`.

The open write is the one that should change `0x64351898+0x690` from `0x0100` to `0x0105`. `0x63988EAA` is not that write: its destination object is `0x6435CC38`, and the byte that lets the copy run is 0. The next step is to name which of the other five `STRH #0x690` sites, or the struct copy at `0x63D3D6DC`, holds `0x64351898` in its base register while the low byte is still 0.
