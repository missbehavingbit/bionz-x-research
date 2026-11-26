# E0529 — QEMU `sony-a6400`: av-cam ADF decode; the `ObjMic` stall was the missing SYSV frame clock. With VPH + SYSV ported, boot reaches `modelCamera ACTIVE`, `EEStart` and `PIN_OPEN`

Date: 2026-11-16. **Strictly offline.** No camera access. Builds on E0528.

Labels: [C] confirmed by a run receipt or disassembly, [I] inference, [A] assumption.

## 0. Short answer

1. **ADF static decode (a6400 `av-cam.bin`, `90d515cf…`) [C].** av-cam is position-independent (no
   absolute string pointers at any base). Strings are reached through `LDR rX,[pc,#]` … `ADD rX,pc`, and
   a scanner for that pattern plus a Thumb-2 `BL` cross-reference scanner located the handlers
   (file offsets = image offsets):
   - `SET_INPUT` entry `0x1FB1C` stores the reply ID at `this+0x34` and checks payload byte 1 ∈ {1,2,6}.
     Main flow `0x1FAB8`: busy check `0x63D21E` (state word `this+4` = 1), then `0x1F13C` accepts byte 0
     ∈ {6, 8, 0xA0–0xA3} for the deferred, asynchronous path. Otherwise it finishes at once through
     `0x63D67C` → `SendComp` `0x1F170` (`[ADF]SET_INPUT SendComp err %x replyId %x`) to `0x58003F`.
   - Hot-shoe sub-sequence: `0x1F6CC` starts `StartShoeOnSeq` for input types 2–4, 8 and 0xA1, and
     `StartShoeOffSeq` when the previous type was 8. `SendMsgSetShoeOn` `0x1F74C` goes to dataflow
     `0x7C0038`. `RecGetShoeId` accepts shoe IDs 0x102/0x103/0x202/0x302/0x402/0x404.
   - The scenario's message (`AVBB_SCN_START_MICSENSE`, `SetInputADF` → ID `0x8401`) has payload
     byte 0 = 1, byte 1 = 6. So `SET_INPUT` completes immediately, and the stall is the first command,
     **`PROC_SET_PATH 0xA0` (ID `0x8102`)**.
   - Path table string `[160:AdjMicSns, 161:AdjChkSnd, 162:AdjChkMic`. Path 0xA0 runs
     `ProcMsgSetPathSequencerAdjMicSns`, which registers an **AudioIp callback**
     (`ProcAudioIpCbWrapper`), audio and YC pin receive callbacks, and an input-state callback.
2. **AudioIp state [C].** One run logged every AudioIp access (106 in total). The a6400 init sequence
   matches the a5000's documented boot writes word for word: IIR coefficients `0x18B7, 0xCA4A, …`,
   table `0x3F85, 0x39F2, …`, `+0x2400` serial setup, `+0x800/+0x810 = 1`, `+0xC50 = 6`. The DMA window
   is read once (`+0x1014` = 0) and never configured. The frame-rate position poll (a5000 `RE_AUDIO.md`
   §1.2, AIP task at frame rate) is not running, and `liro-98` (SYSV) never fired.
3. **SYSV + VPH ported [C].** `cxd90014_vph.c` from `Oein/a5000-emulator` (GPL-2.0, attributed), wired
   like the a5000 board:
   - VPH at `0xF3440000`.
   - SYSV at `0xF3402000`: V counter `+0x98`, phase `+0x28`, IRQ 98 once per engine frame (33,367 µs).
   - Vsync IRQ 101/102 at 60 Hz.
   - SYSV's V output drives the timing-event block's engine frame (`ext-vd`).

   The a6400 av-cam has 66 literals in the SYSV page and many in the VPH pages.
4. **Result [C].**
   - **`E  Og 001_ObjMic` at 2.1448 s.** `Mi 0DC` ends (`modelRecPbNeutral INIT`).
   - Views load: `Vn 088/08A/02F/0A5_viewUnified2.so`, views 7/8/0/B.
   - `Tc 0E3`, the model chain (`modelCamera INIT` → **`modelCamera ACTIVE`** at 2.239 s,
     `mdlMovRec ModelInit CMP`, `TopologyCreate`), `SET_PANEL_OUT` ×3, `modelCamera ActionSetInit`,
     **`EEStart` B 2.301 s / E 2.446 s**, `PIN_OPEN` ×3, and `SetContentTarget` (E at 3.263 s).
   - The GPU model receives many more command lists (`SUGI-RENDER` #6–#19).
5. **Edge-triggered frame IRQs [C].** At level trigger, the 200 µs ack-less pulses re-entered:
   `liro-98` 53,437 and `liro-101` 67,441 in one run. The CPU0 stub now writes `GICD_ICFGR6 = 0x28A0`
   (IRQ 98/99/101/102 edge), as the a5000 board stub does. After the change, at about 2.2 s of guest
   time: `liro-98` 49 (~30/s), `liro-101/102` 99 (60 Hz).
6. **Display [C].** `screendump` of the VPH panel console: 640×480, all black. No OSD layer is composed
   yet: the AIF channels are not programmed, and the GPU model does not execute drawing commands.

## 1. Receipts

| Artifact | SHA-256 |
| --- | --- |
| `artifacts/qemu-a6400/e0528/audioip-accesses.txt` | `381313c05a195746c741aab465b7eca5948a2a5da8b5eb5124d08677234a45f3` |
| `artifacts/qemu-a6400/e0528/probe-irq-rates-level.txt` (ObjMic E, IRQ storm) | `38e0b2f1114e923ca660619e7bf7932103b21e454d01946a1dad1dc3d38631ca` |
| `artifacts/qemu-a6400/e0528/probe-irq-rates-edge.txt` | `a68c34a0b6683573c1d5a62e54766ebe7ec0e8d36202d6b8ebab1e8b4672680b` |
| `artifacts/qemu-a6400/e0528/probe-after-vph.txt` (checkpoints to `SetContentTarget`) | `3b162f1f29cdb9cba5f5816ef83fdd928d4f243fc9b32eed84b83ee5eb2bdb40` |
| `artifacts/qemu-a6400/e0528/panel-con0.ppm` / `.png` (black) | `a6087ec5…` / `9347fe86…` |
| `artifacts/qemu-a6400/e0528/stub0.S` | `2c02fc55febc34dd0e46365c07e0f4ecdc53aebd70e934e48327df8fe07937ec` |
| `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` | `6f1a28b788da65603db788f8ec82da99627cf1b0739efced387543b1b4ebe072` |
| `qemu-a6400/qemu-overlay/hw/misc/cxd90014_vph.c` | `d8cdf7c3e9c66f33c2a735e437ffd5ee903226fb41759818e541c1f7ce50ceb2` |
| `qemu-a6400/tools/a6400_probe.py` (`--monitor`) | `9c91baf95e2823c3aa07919f48d7512603a69cff0f054c624a8d55631138fe7b` |
| `qemu-a6400/patches/qemu-v10.1.0-sony-a6400.patch` | `97ac57a634c6ae701341fcdb465ae40c6be5a36b47b917c9df7cb55329438bb0` |

Static tooling (scratch): a PC-relative string-reference scanner, a Thumb-2 BL cross-reference
scanner, and a capstone Thumb disassembler with string annotation, run over the a6400 `av-cam.bin`.

## 2. Assumptions and limits

- The SYSV frame period (33,367 µs) and 60 Hz vsync are the a5000 values [A for the a6400].
- The causal link "SYSV frame clock → AIP poll → AdjMicSns callback → `SET_PATH` completion" is inferred
  from the a5000 notes and the before/after runs [I]. The exact callback that completed was not traced.

## 3. Next boundary

1. The checkpoint log stops at `SetContentTarget` (3.263 s) after `EEStart` / `PIN_OPEN`. Find the next
   wait (probably the live-view / VDF pin pipeline: a5000 `RE_LIVEVIEW_PIPELINE.md`, `RE_UI_BRINGUP.md`
   gates B/C).
2. Display: OSD composition needs the VDF OSD pin path, AIF ch53, and GPU drawing (a5000
   `dmp_smaph.c`, `dmp_shader.c`, `cxd90014_aif.c`).
