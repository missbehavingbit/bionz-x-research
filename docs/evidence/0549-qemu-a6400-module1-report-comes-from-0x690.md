# E0549 — QEMU `sony-a6400`: module 1's report is the halfword at `0x64351898+0x690`

Date: 2026-11-17. **Strictly offline.** Builds on E0544.

Labels: [C] confirmed by disassembly or a run receipt, [I] inference, [A] assumption.

## 0. Short answer

Module index 0 is module id 1. While the step-12 wait is parked, its reported halfword stays `0x0100` because the frame publisher copies that value from `*(0x6434CA54 + 8) + 0x690`. At `SetContentTarget` that halfword is `0x0100`, and the flag that would select the other source is clear.

## 1. What the instructions do

`av-cam.bin` is the ORIL image linked at `0x635C6000` [C].

| helper | address | effect |
| --- | --- | --- |
| set target | `0x63D38186` | halfword at object `+0x18 + 2*index` |
| set reported | `0x63D380F2` | halfword at object `+0x32 + 2*index` |
| initial targets | `0x638E7EEC` | index 0 gets `0x0100`, then `0x0200`, `0x0300`, `0x0400`, `0x0500`, `0x0600`, `0x0700`, index 8 gets `0x0900` |

The high byte is the module id. Index 0 is module 1 [C]. `Oein/a5000-emulator` `docs/RE_LIVEVIEW_REQUEST.md` at `245722f5c0b9cd7f3db702d52cd75542254dbfb1` §2.1 uses the same packing and calls that slot camera module 1. That naming is carried over as the a5000 label [I]. It is not a string in this image.

The publisher at `0x63926B00` runs only when the byte at `*(*(0x6434CA54 + 0x24) + 8)` is non-zero [C]. For index 0 it tests bit 0 of the halfword at `0x643334E8 + 0x8E`. Bit set reads `*0x6434CA54`. Bit clear reads `*(0x6434CA54 + 8) + 0x690` [C]. a5000 §6 describes a flaky step-12 stop where every reported halfword stays `xx00` because the publisher branch is off. The neighbors below are not in that state.

## 2. What one boot showed

`tools/a6400_probe.py --until SetContentTarget` reached the checkpoint, status 0.

One run, checkpoint at 48.3 s wall, `xp /16hx 0x04330600` [C]:

| field | value |
| --- | --- |
| step table | `0x63E80F18` |
| step count | 43 |
| index | 12 |
| first request | `0x0105` |

A later run in the same monitor session, after guest time had moved from about 2.45 s to 3.11 s [C]:

| PA | value |
| --- | --- |
| `0x04330632` reported index 0..3 | `0x0100 0x0285 0x0320 0x0400` |
| `0x04351F28` | `0x0100` |

`0x04351F28` is `0x64351898 + 0x690`, and `0x64351898` is the word at context `0x6434CA54 + 8` (`xp /16wx 0x0434CA54` on the previous run) [C]. `xp /8hx 0x04333570` is eight zero halfwords, so bit 0 of the selector at `+0x8E` is clear and the publisher takes the `+0x690` path [C].

`0x0285` and `0x0320` are the step-0 requests for indexes 1 and 2. Index 0 has not taken `0x0105` [C].

An earlier `xp` in that same session still showed index 0 and requests `0x0100` / `0x0200`. That is the initial fill from `0x638E7EEC`, before this sample. It is not the step-12 image.

## 3. What this does not show

The store that should change `0x64351898 + 0x690` from `0x0100` to `0x0105` is not identified. No device model changed. `pmemsave` with a `/home/...` filename fails in this monitor: the size expression consumes the slash (`invalid char 'h'`). `xp` was used instead.

## 4. Next boundary

Which write replaces the low byte of `0x64351898 + 0x690`, and which condition leaves that byte at 0 while indexes 1 and 2 already show their step-0 states.
