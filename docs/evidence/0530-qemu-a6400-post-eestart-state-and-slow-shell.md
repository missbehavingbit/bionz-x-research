# E0530 — QEMU `sony-a6400`: after `SetContentTarget` the UI render loop keeps running (display path missing); the "silent shell" is a delayed `/proc` answer, not a hang

Date: 2026-11-16. **Strictly offline.** Builds on E0529.

Labels: [C] confirmed by a run receipt, [I] inference, [A] assumption.

## 0. Short answer

1. **State after `SetContentTarget` (3.26 s) [C].** The checkpoint log adds nothing more, but the
   guest keeps working: GPU completions (IRQ 76) rise from 50 at about 2.2 s of guest time (E0529 run)
   to **466 at about 34 s** (`liro-98` = 1,022 frames), i.e. roughly 13 GPU jobs per second.
   `liro-101/102` keep their 60 Hz rate. The UI render loop is alive [C]. That this is a steady
   shooting-screen state, and not a stall, is an inference [I]. The a5000 emulator reached the same point
   (`RE_ENGINE_TICK.md` §5: UI rendering frames, buffers zero because P3D drawing is not executed).
2. **Waits at that point [C].**
   - `Appframework` is in `osal_wai_sem_tmo`. `/proc/osal/uipc` lists one semaphore with a waiter:
     global **`0x4EE`** (`L=1 W=1 O=161`). Thread 161 is av-cam task 8 (`liro-kliro_8`, in
     `tsk_thread_helper`). Whether this is the app's normal event wait or a gate is not decided [?].
   - `THREAD_INFRA0` is in `sugilite_ioctl` (GPU kick-thread sleep or job wait [?]).
   - av-cam task 43 is in `twai_flg`, while vsync IRQs 101/102 are delivered.
   - `UPM_DFS` is in `msleep`.
3. **Display [C].** The VPH panel console is still all black (E0529). The OSD path is not modelled:
   VDF OSD pin → AIF ch53, plus GPU P3D/PPF execution.
4. **"Silent shell" explained [C].** In a run where the runner declared the shell silent (no answer
   for 30 s at about 48 s wall time, near `EEStart`), the pending poll **was answered later**
   (`RES=1`, `slow-answer-console.log` line 2216). While it was blocked, all four vCPUs were at
   `cpu_v7_do_idle+0x8` (`0x6011A33C`), called from `default_idle`, with IRQs masked, in both samples.
   So the shell's `grep /proc/snsc_boot_time` was blocked inside the guest (most likely on the
   boot-time log lock during a long boot step [I]). It is not a guest hang or lost UART input. The
   earlier E0527/E0528 "intermittent silent shell" cases are explained the same way [I].
   `tools/a6400_probe.py` now waits up to 120 s for an answer.

## 1. Receipts

| Artifact | SHA-256 |
| --- | --- |
| `artifacts/qemu-a6400/e0529/state-after-setcontenttarget.txt` (threads, interrupts, checkpoints) | `c1ccb9506262ae16b491973df532d8633c2ceb40bc6a968631cd8caeb2ce1f39` |
| `artifacts/qemu-a6400/e0529/uipc-semaphores.txt` (GSEM/LSEM; `0x4EE` W=1) | `8d2b6a814fc78a7cd689324c203fec68382adfb7c48f2da337e3191a80bbc46d` |
| `artifacts/qemu-a6400/e0529/slow-answer-vcpu-dump.txt` | `a0a3aa3a638046ef4dd6a0996b7154e0ccbfd8effea81eb0e393056d29abab0b` |
| `artifacts/qemu-a6400/e0529/slow-answer-console.log` | `0157fa7dd039d7cee8b570869ed58ed410ff5cd90ea6941a99244bde28f6896c` |
| `qemu-a6400/tools/a6400_probe.py` (answer timeout 120 s) | `92281a5d689bf420d15a7e9ea2fdd7eb83dcc08531454f5ec412652da4193db5` |

## 2. Next boundary

**Display path.** In a5000 order (`RE_UI_BRINGUP.md` §7, `RE_DISPLAY_GPU.md`, `RE_DISPLAY_SCANOUT.md`):
- GPU PPF transfers that really copy (tiled → linear);
- P3D drawing (a5000 `dmp_shader.c`);
- av-cam VDF answering the OSD pin (`PIN_SEND_COMP`);
- VPH latching AIF ch53 at vsync.

The a5000 repo has `dmp_smaph.c`, `dmp_shader.c` and `cxd90014_aif.c`. Compare them with the
current `cxd90045.sugilite_bar` stub before porting.
