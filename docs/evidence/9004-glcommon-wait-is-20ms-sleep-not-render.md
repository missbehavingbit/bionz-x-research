# E9004 - The post-CreateFramebuffer wait is a 20 ms sleep-poll, not a render loop

Date: 2026-11-21. Offline process execution only; no camera access.

ID: E9004
Claim: With the opt-in UIPC timer service enabled under the labelled
gl-common-only policy, every post-200M delay arm is a fixed 20 ms sleep: the
deadline is the current counter plus 20,000 us and the measured delta is 19,815
us. The 32 MiB stream surface at physical 0x202E0000, visible as aliases
0x1BCD0000 and 0x549D0000, is byte-identical between the first tick after
200,000,000 instructions and the tick after 205,500,000 instructions. The app
therefore stays in a periodic wait, not a frame renderer, with no new module,
thread, file descriptor or UIPC request.
Status: verified for the 20 ms period and the unchanged surface; the condition
the loop polls and its exact call chain are not identified.

Firmware hash / camera configuration:
- libObj.so 60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1
- libosal_uipc.so: the guest module used in this lineage (hashes in baseline.json)
- Target: ILCE-6400 V2.00 (CXD90045), im.elf debug s g r u l i a lazy-global.
- Checkpoint gemini-runs/pre-malloc-glcommon.ckpt
  2d95aed4209bb1df239c893b86f3c37a58cdd39f0b12efe8d68e6ea02565bdfd.
All firmware inputs are hash-checked against artifacts/inventory/baseline.json.

Source:
- src/a6400/execution/guest_uipc.py: TIMER_DELAY_QUEUE 0x00910041,
  handle_snd_sync_direct (deadline at message +24).
- libObj.so +0x862CB4: movw r2, #0x4e20 (20,000) added to a 64-bit counter
  before a UIPC request.
- Reports: gemini-runs/boot-18-timer-deadlines.json,
  gemini-runs/boot-20-surface-diff.json.

Method:
- Timer-deadline observer: resume the gl-common-only checkpoint with
  --uipc-timer-service emulated, wrap handle_snd_sync_direct, and for calls at
  or after 200,000,000 record queue, size, now_us and the u64 deadline at
  message +24.
- Surface diff: the same resume, reading both aliases at the first delay call
  after 200,000,000 and the first delay call at or after 205,500,000, then
  hashing and comparing the full 32 MiB.
- Both runs use taskset -c 0-3 and the flags recorded in E9001/E9002.

Observation:
1. Post-200M timer calls in a 210,000,000-instruction run: 267 total, split as
   counter queue 0x00910042 size 32 (139) and delay queue 0x00910041 size 40
   (128). The rate is a steady 40 calls per 1M instructions.
2. Every delay arm reports the same delta: deadline - now_us = 19,815 us
   (128 of 128). now_us equals the instruction count in this model, and the
   constant 20,000 us is added at libObj+0x862CB4; the 185 us difference is the
   work done between reading the counter and arming the delay. This is a
   ~50 Hz sleep, not a vsync-locked 16.7 ms frame tick.
3. The two surface aliases 0x1BCD0000 and 0x549D0000 map the same 32 MiB
   buffer (identical SHA-256 at both snapshots) with physical start 0x202E0000.
   First snapshot at the first tick after 200M; second at about 205.5M. Both
   hashes are 04c29f680f58114a (first 16 hex digits) and are equal, so nothing
   in the surface changed across roughly 5.5M instructions and about 220 ticks.
4. In the first 1,228,800 bytes only 3,369 bytes are nonzero in both snapshots.
   The surface is not being drawn into during the wait.
5. TID 1087 remains runnable; module, thread and descriptor counts do not
   change; snd_sync_msg stays 5,919 and valloc 6,737.

Alternative explanations:
- The app is rendering but to a different buffer: the two known aliases of the
  mapped surface are unchanged and no framebuffer transaction occurs; another
  target would need a separate mapping that the report does not show.
- The snapshot window missed the draw: the window spans about 220 ticks over
  5.5M instructions, and the surface hash is identical at both ends; a draw
  that completed before the first snapshot would still leave the second
  snapshot unchanged only if playback stopped, which matches the wait.
- The timer service fakes the 20 ms period: it only supplies the documented
  counter and delay replies (E0035, E0041); the period is computed by the guest
  from its own 20,000 us constant at libObj+0x862CB4.

Confidence and limitations:
- High confidence in the 20 ms period and the unchanged surface: both are
  measured directly.
- The polled condition is not identified. A caller-tracing attempt using the
  assumed sleep-helper entry was inconclusive (zero entries at 0x51EF2C6C and
  six at 0x51D9FADC), so the exact call chain and the flag being awaited remain
  open.
- Labelled gl-common-only approximation; no claim about DMM unit 0x4092e.

Dependent implementation/scenarios:
- --uipc-timer-service emulated is now known to pace a 20 ms wait; without it the
  same code busy-spins.
- Do not treat this wait as a rendered frame or as UI progress.

Next experiment:
- Unwind the 20 ms wait reliably (frame-pointer chain or a debugger-style stack
  walk) and identify the flag/message it polls; only then decide whether a new
  contract is needed. Keep the authentic DMM path (unit 0x4092e base/handle) as
  the alternative.
