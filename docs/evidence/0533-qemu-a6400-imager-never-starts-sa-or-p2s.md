# E0533 — QEMU `sony-a6400`: the imager never starts an SA function or a sensor transfer

Date: 2026-11-16. **Strictly offline.** Builds on E0532.

Labels: [C] confirmed by a run receipt, [I] inference, [A] assumption.

## 0. Short answer

1. **The load path is idle, and the program file is present [C].** At
   `SetContentTarget`, `/usr/sabin/sa_defect.bin` (37,124 bytes) and
   `sa_zphdefdet.bin` (18,820 bytes) are on the guest. `/system/sabin` does not
   exist. `Backup.bin` in `setting.tar` holds a 32-byte-slot name table that
   includes `sa_defect.bin`. In the E0530 thread dump, `InfraOnDmdLdr` (im.elf)
   is in `osal_rcv_msg_tmo` and av-cam task 44 (`liro-kliro_44`, the a5000 SA
   manager task number) is in the same wait. A `grep BD017A /proc/osal/uipc`
   at the checkpoint prints nothing.
2. **Lens detect is not the switch [C].** P1.4 is the a6400 lens-detect pin
   (Backup F9050C/0D). The a5000 treats that class of pin as active low
   (`RE_LENS_EMOUNT.md`: level 0 means mounted). Holding P1.4 at 0, with no
   lens replies, still reaches `SetContentTarget` (47.7 s wall). The SA log is
   the same sixteen ID reads and no GO. `0x144` becomes `0x3000232` (bit0
   clear, ch51 base 0). The pin is back at 1 (empty mount) after the run.
3. **The sensor serializer is never kicked [C].** a6400 `av-cam.bin` contains
   one literal each of `0xF3C00300`, `0xF3C00800` and `0xF3C00900`, the a5000
   P2S unit-0 table (`RE_SENSOR_ISP.md` §1.3). The a5000 completion model is
   wired at `0xF3C00000`, IRQ 38: a write of `0x908` bit0 completes in 50 µs,
   clears busy, sets `0x904` bit0 and raises IRQ 38. Through `SetContentTarget`
   plus 2 guest seconds the log has no `[P2S]` line. On the a5000 this kick is
   the imager `PowerOn` / `AsyncInitial` path, which runs before ZIT asks the
   SA core to start.
4. **Where that leaves live view [C].** The shooting-screen OSD path is
   unchanged (`en144` ends at `0x3000232`). The two devices a5000 needed next,
   SA completion and P2S completion, are both waiting for a guest that does
   not program them in this window.

## 1. Commands

```bash
python3 tools/a6400_probe.py --until SetContentTarget --timeout 420 --poll 2 \
  --cmd 'ls -l /usr/sabin/sa_defect.bin /usr/sabin/sa_zphdefdet.bin /system/sabin/sa_defect.bin; echo EXIT:$?' \
  --cmd 'grep BD017A /proc/osal/uipc; echo EXIT:$?' \
  --log /home/administrator/a6400-runs/e0532b.log
# P1.4 held low, then restored:
python3 tools/a6400_probe.py --until SetContentTarget --timeout 420 --poll 2 \
  --cmd 'sleep 3' --log /home/administrator/a6400-runs/e0532-lens.log
# P2S model in place:
python3 tools/a6400_probe.py --until SetContentTarget --timeout 420 --poll 2 \
  --cmd 'sleep 2' --log /home/administrator/a6400-runs/e0532-p2s.log
```

Checkpoint wall times: file probe 49.4 s, lens probe 47.7 s, P2S probe 47.4 s.
Each returned status 0.

## 2. Assumptions and limits

- Task 44 being the SA manager, and `InfraOnDmdLdr` being the ODL peer of
  queue `0xBD017A`, is carried over from the a5000 and from E0309 [I]. The
  waits match "no request in progress". They do not by themselves name the
  function those tasks would run.
- "No P2S kick" is bounded by the logged starts (first 8) through the
  checkpoint plus 2 guest seconds [C]. A later kick was not watched.
- The P2S completion time of 50 µs is the a5000 hypothesis [A]. No transfer
  ran, so that number is untested on this firmware.
- The `BD017A` grep misses a queue whose proc line uses another spelling [A].

## 3. Next boundary

Why av-cam does not reach imager `PowerOn` (the P2S kick) or ZIT `CalcStart`
(the SA GO). a5000 `RE_LIVEVIEW_REQUEST.md` §2: the mode sequencer can stop at
step 12, before the imager is asked for anything, or at step 33, inside the
imager job. This run is still before both device starts.
