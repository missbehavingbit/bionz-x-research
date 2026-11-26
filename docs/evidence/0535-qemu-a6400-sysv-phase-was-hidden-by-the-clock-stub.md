# E0535 — QEMU `sony-a6400`: the SYSV phase bit was hidden by the clock stub

Date: 2026-11-16. **Strictly offline.** Builds on E0534.

Labels: [C] confirmed by a run receipt, [I] inference, [A] assumption.

## 0. Short answer

The 64 KiB read/write stub at `0xF3400000` overlaps SYSV at `0xF3402000`. At the same priority it answered `0xF3402028` and returned 0 [C]. SYSV's phase bit (bit0 = frame-counter parity) was never read. Mapping the stub at priority -1 makes SYSV answer that address. The bit then alternates (215 of the first 500 reads were 1) [C]. The panel YUV layer stays off: `en144` ends at `0x3000232`, and there is still no P2S kick and no SA GO [C].

## 1. What was measured

Guest: `tools/a6400_probe.py --until SetContentTarget`, default `run.sh` (`-icount shift=0,sleep=on`). Checkpoint 46.8 s, shell 4.2 s, status 0. Ulog saved with `pmemsave 0x03085000 4685824 e0538-ulog.bin` (relative name; a leading `/` is parsed as division). The dump was deleted after the strings were read.

| Observation | Result |
| --- | --- |
| Before the priority change | Four reads of `0xF3402028` logged from the clock stub, value 0. No read reached the SYSV device [C]. |
| After priority -1 | Reads reach SYSV. Read 1–4 at frame 20 are bit0 = 0. By read 500, 215 reads had bit0 = 1, frame 37 [C]. |
| `en144` | Ends at `0x3000232`. ch51 base stays 0. ch53 is the OSD at `0x23A80000` / `0x23B16000` [C]. |
| P2S / SA | No `[P2S] start`, no `[SA] start` [C]. |
| Ulog | `FW: HS_Sequence: start delayed 27V #3 => 28V` and the same shape through `70V #3 => …`, each slot's V one behind the stored counter. `IMG:Error : GetImagerTemperature`. Lens `LoadLensAberrationData` error `0x2` / `wrong message received`. No `P2S`, `ZIT`, `PowerOn`, `CalcStart` [C]. Leo and D-PHY timeout lines stay gone [C]. |

A second build lengthened the engine frame from 33367 µs to 33567 µs. Slot 3 was still `NV #3 => (N+1)V` (88 delayed lines, same shape), `en144` was unchanged, and port 2's output latch never changed [C]. That period is not kept. The frame stays 33367 µs, av-cam's constant `0x8257`.

## 2. Model change that is kept

`cxd90045.sugi_clk` (`0xF3400000`, 64 KiB, `+0x80 = 0x15`) is now mapped with overlap priority -1. SYSV (`0xF3402000`, priority 0, size `0x1000`) wins `0xF3402000`–`0xF3402FFF`, including phase `+0x28` and the V counter `+0x98`. Clock words `+0x080` and `+0x280` sit below `0xF3402000`, so they stay on the stub [C].

## 3. What this does not show

a5000 `RE_ENGINE_STABILITY.md` §1.4 says a phase bit stuck at 0 stops VD_Pre copying mode requests, and toggling it lets imager `PowerOn` run. Here the bit toggles and the imager still does not start a transfer [C]. The a5000 note is not, by itself, the rest of this stall [I]. The one-V-late HS slot 3 is the same class of warning a5000 `RE_LIVEVIEW_PIPELINE.md` §4 calls not the blocker; lengthening the frame did not clear it [C].

## 4. Next boundary

Why av-cam still never programs P2S or an SA core after the phase bit is visible on both parities. The rear LCD is already console 0; ch51 turns on only when the firmware sets `0x144` bit0 and a non-zero base.
