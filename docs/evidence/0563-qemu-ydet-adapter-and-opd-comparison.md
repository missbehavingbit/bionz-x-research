# E0563 — QEMU Ydet adapter caller and OPD comparison

Date: 2026-11-17. Offline analysis and bounded QEMU runs. No physical camera or SD access.

[E0569](0569-qemu-sgc1-event-a.md) found a record alignment issue in this IRQ table for the adjacent CamPig entries. The OPD IRQ associations below require recalculation with the IRQ ID preceding the handler and independent delivery verification.

## Reference leads

- [a5000 OPD model](https://github.com/Oein/a5000-emulator/blob/6ad36bd7b0411d55a5920447ff51e3de01f8cb9b/qemu-overlay/hw/misc/cxd90014_opd.c), ILCE-5000 firmware 1.10. It models IRQs 67–70 for the first four OPD units and uses a hypothesized timer completion on a write to control bit 1. Its completion moves no data. The source identifies the synchronous pass for its third unit as the supported caller.
- [ZV-E10 display patch](https://github.com/cokeenet/zve10-qemu/blob/1ce17f3802aaa736d19ab0fe0d4b6917546baf9d/patches/qemu-cxd90045-vip-display.patch), and [ZV-E10 frame investigation](https://github.com/cokeenet/zve10-qemu/blob/1ce17f3802aaa736d19ab0fe0d4b6917546baf9d/docs/QEMU_FRAME_VERDICT_2026-11-13.md). The inspected patch and frame notes supply no OPD statistics or Ydet completion contract for this boot. The local ZV-E10 2.03 image provides a separate static comparison.

## Original instructions

| Observation | ILCE-6400 evidence |
| --- | --- |
| OPD control uses the same base as the a5000 model | `0x63779240` loads the `0xF3030000` literal, reads `+0xC`, sets bit 1, then stores it at `0x63779248`. Other helpers modify bits 0, 2, 3, 4 and 16. The instruction supports a bit-1 set operation and supplies no completion timing. |
| The firmware uses seven status/enable groups | `0x63CD5D80` writes `0xFF` to each status at group `+4` and zero to each enable at group `+8`. The seven ISR wrappers read and write back the status and call through a firmware callback slot. |
| The firmware IRQ records differ from a5000 | At `0x64504540..0x64504630`, the 16-byte records associate the wrappers for groups `0x100, 0x140, 0x200, 0x300, 0x400, 0x500, 0x600` with raw IRQ fields `89, 90, 91, 73, 88, 94, 70`, respectively. These are firmware table values. Guest delivery and board signal wiring were not exercised. |
| The Ydet producer adapter has a direct caller | Wrapper `0x63D3DD22` invokes `0x63DC6F58` at `0x63D3DD36` using `BL` (`f089 f90f`). It packs the first argument as a handle, derives a byte from the second argument at offset 3, and reads a little-endian halfword from offsets 0–1 through `0x63CE8C70`. The adapter tail-calls the E0561 producer at `0x63BEE670`. This supersedes E0561's negative direct-reference search. |

Raw Thumb disassembly used GNU `arm-linux-gnueabihf-objdump -D -b binary -marm -Mforce-thumb --adjust-vma=0x635c6000` with bounded start/stop addresses. The ORIL image hash for ILCE-6400 is `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`. Short instruction signatures for the wrapper and OPD start writer each match uniquely in local ZV-E10 2.03 at `0x63D91A10` and `0x6379648C`, and in ILCE-6400A 1.01 at `0x63D52514` and `0x6378A0DC`. These are static comparison leads. Full image hashes are in `inventory.txt`.

## Runtime boundary

Runs used QEMU build `2720703c2b200a1dbaf9ee7c7e4a99e3e6a8c026b21e7517bcd7b836354486a7`, the E0561 legacy inputs and `EVSEL_LATE_US=500` (phase assumption), with `-icount shift=0,sleep=on`. Each instance used a fresh temporary qcow2 overlay.

- The QEMU plugin registered memory callbacks only in the ILCE-6400 ORIL VA range and filtered for I/O in physical `0xF3030000..0xF3031FFF`. The replay reached `SetContentTarget` and the same step-32 wait. The plugin emitted zero filtered accesses. This negative has no positive-control access and is limited to its filter and sampled replay.
- The original instruction probe used loopback GDB `-S`, `rsp_hitcount.py --seconds 110 --samples 3 --max-hits 30`, and `a6400_probe.py --until SetContentTarget --timeout 115 --cmd "sleep 15" --monitor "xp /20wx 0x04330610"`. The consumer hit the 30-hit cap. The wrapper, adapter, producer, OPD initialization, bit-1 start and bit-0/16 setters each had zero hits. HMP reproduced step 32, module 3 request `0x032E` and report `0x0326`. The serial observer completed at 101.2 s and stopped QEMU. The breakpoint client recorded the totals when the stub closed and its subsequent detach encountered a broken pipe.
- The second breakpoint run sampled the Ydet setup function `0x63BED758`, reached through `0x63D3DCC4 → 0x63DC6F64`. Its first three samples have caller LR `0x63D3DCDB` and arguments `0x68EEBEC4, 0x6433E538, 0x6433EFD4`. The setup and consumer each reached their 15-hit cap. Serial framing became unreliable, the two observers timed out with exit 124, and the requested HMP snapshots were not produced. The remaining QEMU process was matched by its unique loopback port and overlay path, stopped with SIGTERM and verified exited. This receipt supports the positive hits only.
- The passive retry, `python3 -u artifacts/qemu-a6400/ydet-references/passive-retry/replay.py`, avoided serial commands and breakpoints. TCG instruction callbacks counted 509 visits to setup and 509 visits to the single-window descriptor lookup at `0x63BED97E`. The dual-window calls at `0x63BED87E` and `0x63BED8D8` had zero hits. The consumer ran 556 times. The adapter caller, producer and OPD initialization/start sites all had zero hits. At the 95 s snapshot, the ten words at PA `0x041E3390` containing the OPD callback slots were zero and the sequencer still reported step 32. QMP quit produced a shutdown event and exit 0. The panel PPM SHA-256 was `e26b3bee28b653c49d4319df22a7d837993ac44c576aac31a73b9a24c2de7ee0`, identical to the earlier step-32 capture.

The identified OPD control and IRQ helpers do not establish that OPD drives the Ydet producer. The tested OPD start sites were idle while the Ydet setup ran. The next boundary is the return value of `0x63DC77EA` at `0x63BED982`, and the dispatch that passes a handle and metadata to `0x63D3DD22`. The setup lookup tests the descriptor pointer at `handle + 0x4C0` for window 0, whereas the producer retrieves result descriptors at `handle + 0x2B8` and `handle + 0x2C4`. No guest memory or register values were written by the observers and no modeled statistics, status or IRQs were added.

Receipts and observation tools are under `artifacts/qemu-a6400/ydet-references/`. The initial plugin compile failure and the missing positive-control trace are retained.
