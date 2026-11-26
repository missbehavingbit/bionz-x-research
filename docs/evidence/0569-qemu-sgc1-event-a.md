# E0569 — Opt-in Sgc1 input sync event A

Date: 2026-11-17. Offline follow-up to [E0568](0568-qemu-campig-dispatch-order-and-input-sync.md).

## Model and provenance

`SGC1_EVENT_A=1` selects `-M sony-a6400,sgc1-event-a=on`. It covers five 32-bit words relative to `0xF3120000`: event A status `+0x48` (bit 0, write-one-to-clear), enable `+0x50` (bit 0), control `+0x210` (start bit 0 and continuous bit 8), geometry `+0x220`, and compare line `+0x224`. When status and enable intersect, the model asserts IRQ 35. Stop and reset cancel the timer. In single-frame mode start clears at frame end. Zero geometry clears start, and an out-of-range compare skips event A for the frame.

The [pinned a5000 CamPig model](https://github.com/Oein/a5000-emulator/blob/6ad36bd7b0411d55a5920447ff51e3de01f8cb9b/qemu-overlay/hw/misc/cxd90014_campig.c) supplies the input-generator programming reference. The a6400 firmware original writes, handler, status acknowledgment and IRQ mapping are in E0568. The QEMU timer runs on the virtual clock. It uses the a6400's programmed counts with 36 MHz, inferred from the nominal frame period. The physical clock, phase and edge timing are still unverified. Each frame samples the current geometry and compare word when it starts. Default runs skip the mapping. The model covers input timing and performs no image or statistics transfer.

## Focused verification

The artifact builder used the existing QEMU compile/link plan and emitted a separate corrected binary SHA-256 `0658c5d73e9ac2b92eed0a65ee41a167dd347a2f27e4ae2b5d72a619d2374c17`; the baseline binary retained SHA-256 `2720703c2b200a1dbaf9ee7c7e4a99e3e6a8c026b21e7517bcd7b836354486a7`.

The synthetic `qtest.py`, using the qtest accelerator and advancing the virtual clock explicitly, confirmed the default no-response path and the opt-in event. With geometry `0x03470595` and compare line 739, status stayed zero through 29,354,722 ns and set at 29,354,723 ns. Writing zero preserved the status, writing one cleared it, and continuous mode latched it again in the next frame. Stop cancelled future events, one-shot mode cleared start by frame end, and reset cleared status, enable, and geometry. The first test attempt ran with `-S` and the paused QEMU clock did not deliver an event. The retained retry without the pause passed. The launcher passed `bash -n`.

## Original firmware and corrected IRQ mapping

- Trial binary `1f8968658c081df674880840471fca34fcb8853e6f935e4ae8f97425b59b9ffc` used input 36 based on the earlier static table interpretation. The guest ran the `0xF3000000` handler at `0x63CC3850` 6,660,073 times, read zero status there, and never acknowledged `0xF3120048`. The input handler at `0x6375B08C` had zero hits. The bounded 70 s run quit with the sequencer still at its initial state.
- The corrected build follows the upstream input pin 35. The guest executed `0x6375B08C`, its dispatch wrapper `0x63CC3B10`, and callback `0x6375BBF0` **231 times each**. Sampled status reads at `0xF3120048` returned 1 and the original handler wrote 1 to acknowledge each recorded event. The `0xF3000000` handler had zero hits in this build. These two replays supersede the prior IRQ assignment for these units. In the raw table, the IRQ ID precedes the handler pointer by eight bytes. The table excerpt in E0568 grouped words starting at the handler pointer and therefore associated it with the next ID.
- The corrected run reached step 32 with module 3 at `0x0326` against `0x032E`. The branch containing the front-end write call at `0x6375BC46`, the EE activation function, the completion load and the Ydet producer each had zero hits. The next boundary is the branch selection inside `0x6375BBF0` and the requirements for CamPig completion and statistics. Both QEMU replays exited 0 via QMP quit.

The generated status synchronization checks passed (39 tests). The Windows suite ran 1,225 tests and reported 146 errors and 39 skips, including Windows native-module load errors.

## Receipts

`artifacts/qemu-a6400/sgc1-event-a/` and `sgc1-event-a-pin35/`: the separate-build recipe, shell output, synthetic test (including the failed paused variant), passive traces and QMP replays. Build and run the scripts with `python3 -u` in the same WSL session from the repository root. Use `A6400_QEMU_DIR` as recorded by `build.py`. The TCG replays use E0561 legacy inputs, unchanged ORIL SHA-256 `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`, fresh overlays, `EVSEL_LATE_US=500`, `CAMPIG_ENABLE_READBACK=1`, `SGC1_EVENT_A=1`, and `-icount shift=0,sleep=on`.
