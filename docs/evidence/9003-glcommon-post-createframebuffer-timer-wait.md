# E9003 - After CreateFramebuffer, the labelled single-pool run idles on a timer wait instead of reaching OSD

Date: 2026-11-21. Offline process execution only; no camera access.

ID: E9003
Claim: Under the labelled legacy single-pool GRM policy, CreateFramebuffer(640,480)
succeeds and Appframework's next stop is a timer-paced wait, not a crash. Without
the UIPC timer service the thread livelocks on snd_sync_direct to the timer
counter/delay queues 0x00910042 (size 32) and 0x00910041 (size 40). With the
existing opt-in --uipc-timer-service emulated, that poll storm stops
(snd_sync_direct 96,472 at the 400M run versus 5,051 at the 300M run) and the
process idles at roughly one thousand syscalls per 25M instructions with TID 1087
runnable, no new modules, threads or file descriptors, and no frame buffer
transactions. No component loads viewUnified2 or an OSD writer. The missing
contract is an external completion/event, not the GRM pool.
Status: verified for the observed loop, its timer queues and the timer-service
effect; the awaited event itself is not identified, and this is a labelled
approximation, not evidence for the DMM unit 0x4092e mapping.

Firmware hash / camera configuration:
- libObj.so 60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1
- libosal_uipc.so: the guest module used in this lineage (hashes in baseline.json)
- Target: ILCE-6400 V2.00 (CXD90045), im.elf debug s g r u l i a lazy-global.
- Checkpoint gemini-runs/pre-malloc-glcommon.ckpt
  2d95aed4209bb1df239c893b86f3c37a58cdd39f0b12efe8d68e6ea02565bdfd.
All firmware inputs are hash-checked against artifacts/inventory/baseline.json.

Source:
- src/a6400/execution/guest_uipc.py: TIMER_COUNTER_QUEUE 0x00910042,
  TIMER_DELAY_QUEUE 0x00910041, handle_snd_sync_direct.
- tools/research/experiment_ui_boot.py: --uipc-timer-service off/emulated.
- Reports: gemini-runs/boot-15-stage3.json (400M, timer off),
  gemini-runs/boot-16-sync-direct.json (206M, queue observer),
  gemini-runs/boot-17-timer-emulated.json (300M, timer emulated).

Method:
- Stage 3: resume pre-malloc-glcommon.ckpt with --grm-pools gl-common-only to a
  400,000,000-instruction budget and no timer service.
- Stage 3b: the same resume to 206,000,000 with an instance wrapper on
  GuestUipc.handle_snd_sync_direct that counts queue/size for calls at or after
  instruction 200,000,000.
- Stage 4: the same resume to 300,000,000 with --uipc-timer-service emulated.
- All runs use taskset -c 0-3 and the flags recorded in E9001/E9002.

Observation:
1. Timer off, 400M budget: 400,042,319 instructions, status
   instruction_budget_exhausted. TID 1087 (Appframework) is runnable; there is no
   tgkill, no CreateFramebuffer failure and no stella assertion. syscalls 120,948;
   ioctl 117,479; snd_sync_direct 96,472 (it was 10,833 at the 215M run, so
   85,639 calls were added after CreateFramebuffer). framebuffer transactions 0.
2. Post-200M queue observer, 206,012,103 instructions: 3,644 snd_sync_direct
   calls. They are only:
   - queue 0x00910042, size 32, count 1,824 (utimer_kif_counter)
   - queue 0x00910041, size 40, count 1,820 (utimer delay queue)
   No other queue appears in the window.
3. Timer emulated, 300M budget: 300,038,329 instructions, status
   instruction_budget_exhausted. syscalls 29,111 (25,175 at 200M, so only 3,936
   in the last 100M); snd_sync_direct 5,051; futex 336. TID 1087 is runnable;
   threads 90; fds 32; snd_sync_msg 5,919; valloc 6,737; snd_msg 788.
   No new module, thread or descriptor appears, and framebuffer transactions
   remain 0.
4. The timer emulation is the only change between the two runs. It removes the
   poll storm but does not advance the module/thread/fd/UIPC mix.

Alternative explanations:
- The process is doing periodic work rather than idling: the module, thread, fd,
  snd_sync_msg and valloc counts do not advance between 215M and 300M; only
  timer polling/blocking advances.
- The idle is caused by the gl-common-only approximation (a wrong surface base or
  handle): possible and not excluded. That is why this record is explicitly
  labelled as an approximation and makes no claim about unit 0x4092e.
- Timer emulation fabricates progress: it supplies only the documented counter
  and delay replies (E0035, E0041) and no display, stream or OSD contract.

Confidence and limitations:
- High confidence in the loop queues and the timer-service effect: both are
  counted directly.
- The awaited condition is not identified. No per-thread PC trace was taken
  after 200M and the timer deadlines were not decoded.
- The 300M/400M runs stop at an instruction budget, not at a new contract
  boundary. They do not demonstrate OSD or viewUnified2 progress.

Dependent implementation/scenarios:
- --uipc-timer-service emulated is required for this post-CreateFramebuffer wait
  to stop spinning; it does not by itself make the app proceed.
- The next contract to model or capture is whatever completion the timer-paced
  wait is waiting for (display/stream submission or a service reply), not GRM.

Next experiment:
- Decode the timer deadlines and the thread's wait target after 200M, and inspect
  the /dev/stream and /dev/fb0 consumers for the awaited event. In parallel, keep
  the authentic path: obtain an executed DMM base and handle for unit 0x4092e
  before replacing the labelled single-pool approximation.
