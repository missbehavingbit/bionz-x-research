# E0550 — QEMU `sony-a6400`: path selector already accepted `0x3003`, module 1 is still state 0

Date: 2026-11-17. **Strictly offline.** Builds on E0549.

Labels: [C] confirmed by disassembly or a run receipt, [I] inference, [A] assumption.

## 0. Short answer

The step-12 mismatch is not a missing `0x3003` request. While module 1 still reports `0x0100` against `0x0105`, the path selector object at `0x643305E4` already holds the result of request `0x3003`: applied flag `1`, next word `0x3003`, and selected value `2` at `0x643305D0`. That value is a different variable from the mode halfword at `0x64351898+0x690`.

## 1. What the instructions do

`av-cam.bin` is the ORIL image linked at `0x635C6000` [C]. The path-selector applicator is `0x638E82DC`. With Thumb `add rd, pc` using the address of that instruction plus 4, its globals are [C]:

| address | use |
| --- | --- |
| `0x6448F9F7` | byte must be non-zero or the applicator returns |
| `0x6448F9F6` | byte must be zero or the request is skipped |
| `0x643305FC` | pending request word; zero takes the already-consumed path |
| `0x643305E4` | selector object |
| `0x643305D0` | word that receives the selected value |

The object is `{+0 table, +4 count, +8 array, +0xC applied flag, +0x10 next}`. `0x63D381C0` walks 16-byte entries `{current, request, function, next}`. On a match it calls the function, stores the return at `+8`, stores `1` at `+0xC`, and stores `next` at `+0x10` [C].

Entry 0 of table `0x6457B5F0` is `{current 0, request 0x3003, function 0x63BE2C54, next 0x3003}` [C]. `0x63BE2C54` is `ldr r0, [pc, #4]; add r0, pc; bx lr` and returns `0x63E826A4` [C]. `0x63D3821C` reads that array: halfword 0 is the index, and the halfword at that index is the selected value. The file bytes are `0x0001, 0x0002`, so index 1 selects `2` [C]. `0x638E8336` stores that value at `0x643305D0` [C].

`Oein/a5000-emulator` `docs/RE_LIVEVIEW_REQUEST.md` at `245722f5c0b9cd7f3db702d52cd75542254dbfb1` §2.2 records the same boot request: one accepted `0x3003`, after which the path value is 2, and the boots that pass step 12 already have module 1 at `0x0105`. The a6400 number `2` is the same kind of path result [I]. It is not the mode state in the low byte of `0x0100`.

## 2. What one boot showed

`tools/a6400_probe.py --until SetContentTarget --cmd "sleep 3"`, status 0, checkpoint at 47.9 s wall, done at 65.0 s. Log: `/home/administrator/a6400-runs/e0550b.log`.

Same monitor session, after the guest sleep [C]:

| PA | value |
| --- | --- |
| `0x04330618` targets | `0x0105 0x0285 0x0320 0x0400 0x0500 0x0600 0x0780 0x0000` |
| `0x04330632` reports | `0x0100 0x0285 0x0320 0x0400 0x0500 0x0600 0x0780 0x0000` |
| `0x043305D0` | `0x00000002 0x00000000 0x00000001 0x00000000` |
| `0x043305E0` | `0x00000002 0x6457B5F0 0x00000188 0x63E826A4` |
| `0x043305F0` | `0x00000001 0x00003003 0x00000000 0x00000000` |
| `0x0448F9F5` bytes | `0x00 0x00 0x01 0x01` |
| `0x04351F28` | `0x0100` |

`0x043305E4` is the object: table `0x6457B5F0`, count `0x188`, array `0x63E826A4`, applied flag `1`, next word `0x3003` [C]. `0x043305D0` is `2`, the value selected from that array [C]. `0x043305FC` is `0`, so the pending request is already clear [C]. `0x0448F9F7` is `1`, so the applicator is enabled [C]. `0x04351F28` is `0x64351898+0x690` and is still `0x0100` [C].

An earlier sample in `e0550.log`, taken without the guest sleep, still showed the initial targets `0x0100 0x0200` at `0x04330618`. That sample is the fill from `0x638E7EEC`, the same early window as E0549. It is not the stalled image.

## 3. Boundary

No device model changed. The sequencer pair is still `0x0105` against `0x0100`. Request `0x3003` has already been accepted, and its result `2` does not replace the low byte of `0x64351898+0x690`.

The open write is the one that should change that low byte from `0` to `5` while the path-selector result stays `2`.
