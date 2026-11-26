# E0532 — QEMU `sony-a6400`: av-cam reads the four SA DSP IDs and never writes GO, so the YUV layer stays off

Date: 2026-11-16. **Strictly offline.** Builds on E0531.

Labels: [C] confirmed by a run receipt, [I] inference, [A] assumption.

## 0. Short answer

1. **What a5000 needed for the YUV layer [C, from their notes].** `RE_LIVEVIEW_REQUEST.md`
   says the mode sequencer does not request YC output until an imager job finishes.
   That job is ZIT ConvZ1C on SA DSP core 0. It writes GO at `0xF34A0004` and waits
   for source-A IRQ 51. `RE_SA_DSP.md` §5 is the null model: return the 16-byte core
   ID, and a short time after GO set factor A to 0x01. `RE_LIVEVIEW.md` then shows
   `0x144 = 0x01000233` (YUV on) once that path runs. The SYSV phase fix from
   `RE_LIVEVIEW_PIPELINE.md` is already in this machine (E0529).
2. **The same cores are in this firmware [C].** `av-cam.bin` contains
   `ZMA0V1.0SA5_V1.0`, `ZMA1V1.0SA5_V1.0`, `SA0_V1.0SA5_V1.0`, `SA1_V1.0SA5_V1.0`
   and the literals `0xF34A0000`, `0xF34B0000`, `0xF34C0000`, `0xF35C0000`.
   The null model is wired at those addresses with IRQs 51–56, 61–66 and 64–66.
3. **Init reads the IDs. Nothing else touches the cores [C].** By `SetContentTarget`
   the only SA accesses are the sixteen ID reads (`+0x0F0..+0x0FC` on each core).
   The bytes are the four ID strings. There is no write, so GO never happens and
   IRQ 51 is not raised. Five guest seconds after `SetContentTarget` the panel
   PPM is byte-identical to E0531 (`deec46fe…`, 1597 non-black OSD pixels).
   `0x144` moves `0` → `0x230` → `0x3000232`. Bit 0 stays clear. ch51 stays 0.
4. **Next blocker [C].** The completion model is not what is missing yet. The
   firmware never starts a function. a5000 reaches that write during boot
   (`RE_SA_DSP.md` §4: reset, clock, mailbox, entry PA, then GO). Here that
   sequence does not run. The open question is why CalcStart / ZIT is not issued.

## 1. Receipts

| Artifact | SHA-256 |
| --- | --- |
| `artifacts/qemu-a6400/e0531/sa-vph-lines.txt` | `699f8df8375bb6852c7024ed1a8ba1f62eb473ee47e0ee9edfb66ee21578ca07` |
| `artifacts/qemu-a6400/e0531/panel-plus-5s.ppm` (same file hash as E0531 `panel-plus-5s.ppm`) | `deec46fe3f50268e26c66ae6293e6722e840f9501fcc3cc9a5cabcc8471fe341` |
| `qemu-a6400/qemu-overlay/hw/misc/cxd90014_sa.c` | `635c5534baaee01a727feca7b213a06bd6586efe0b907cdb1d54ec3bd1e716cc` |
| `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` | `805a9dbcde066547d4191e364434fbe8a2629a753538518de498bc65dcc865ab` |
| `qemu-a6400/qemu-overlay/hw/misc/cxd90014_vph.c` | `56d4a04c852bcbe3856bd53958bfa30df5460f3a42cfa50099df0065390af302` |

```bash
python3 tools/a6400_probe.py --until 'SetContentTarget' --timeout 420 --poll 2 \
  --cmd 'sleep 5' \
  --monitor 'screendump /home/administrator/a6400-runs/e0531-panel.ppm vph 0' \
  --log /home/administrator/a6400-runs/e0531.log
python3 tools/a6400_probe.py --until 'SetContentTarget' --timeout 240 --poll 2 \
  --log /home/administrator/a6400-runs/e0531b.log
```

The filtered lines are from the second run, which logs the first 24 SA accesses.
The first run had the same device and logged starts only; it recorded none.

## 2. Assumptions and limits

- "Never writes GO" is bounded by these two runs: through `SetContentTarget`, and
  five guest seconds past it on the first run [C]. A later start was not watched.
- That the ID read is task-44 init, as on the a5000, is an inference from the
  same strings and offsets [I].

## 3. Next boundary

Why av-cam does not issue the SA function start. a5000 `RE_SA_DSP.md` §3 (open
and load complete in software, before any MMIO) and `RE_LIVEVIEW_REQUEST.md`
(the sequencer step that launches the imager job).
