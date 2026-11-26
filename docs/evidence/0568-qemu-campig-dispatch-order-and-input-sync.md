# E0568 — QEMU CamPig dispatch order and input sync programming

Date: 2026-11-17. Offline follow-up to [E0567](0567-qemu-campig-enable-register-readback.md).

Follow-up [E0569](0569-qemu-sgc1-event-a.md) corrected the IRQ table alignment. The input block at `0xF3120000` uses IRQ 35, the front end at `0xF3000000` IRQ 36, and the next table record identifies IRQ 37 for `0xF3100000`. The callback order and input setup below remain applicable.

## Callback order

The firmware dispatcher at `0x6375AB70` resolves its table at VA `0x641DCC10` and count at VA `0x641DCD90` through the original GOT. A QMP snapshot from the real-firmware replay with CamPig enable readback on showed three live records in order. The table stride is 24 bytes and the last 20 bytes of each entry form the dispatch descriptor.

| Index | Required tagged mask | Wrapper | Target | Context |
| --- | --- | --- | --- | --- |
| 0 | `0x10000001` | `0x63CC3B11` | `0x6375BBF1` | `0x640BDB78` |
| 1 | `0x28000000` | `0x63CBB43D` | `0x63CBBF65` (EE completion) | `0x641D660C` |
| 2 | `0x24000000` | `0x63CBB43D` | `0x63CBC587` (EE promotion) | `0x641D660C` |

The IRQ 37 handler for `0xF3000000` tags its `+0x48` status with `0x20000000`. The dispatcher tests full mask inclusion and copies matching entries in table order before invoking them. If the guest handler observes both bit 26 and bit 27 in the same status sample, completion therefore runs before promotion. This is a conditional instruction/table result. Neither bit was delivered in either QEMU replay, and their physical arrival order remains unknown.

The pending activation callback `0x63902260` copies 12 bytes from `0x6434C108` to `0x6434C09C` and a byte from `0x6459159F` to `0x645915A0`. The pending completion callback at `0x639023AC` passes `0x6434C09C` as metadata to `0x6392280C` (E0565). This confirms a firmware metadata handoff from promotion to a subsequent completion. It supports preserving the observed dispatch order if events coalesce.

## Input sync generator

The replay logged these original CPU writes at the input unit `0xF3120000`:

| PC | Register | Value |
| --- | --- | --- |
| `0x6375A9D2` | status `+0x48` | `0x1`, acknowledgment |
| `0x63CC3222` | enable `+0x50` | `0x1` |
| `0x63CC2D32` | line compare `+0x224` | `0x2E3` |
| `0x63CC2D2C` | geometry `+0x220` | `0x03470595` |
| `0x63CC3C34` | control `+0x210` | `0x101` |

IRQ table entry `0x64504500` names raw IRQ field 36 and Thumb handler `0x6375B08D`. This handler reads the `0xF3120000` status pair, acknowledges the nonzero status, and tags it with `0x10000000` before dispatch. Table entry `0x64504520` is IRQ 38 for `0xF3100000`, a different front-end unit.

The registered IRQ 36 callback at `0x6375BBF0` checks its object field `+0x20` and the low two bits of `0xF312003C` (via `0x6375BA3C`). One branch reads `0xF3000038` at `0x6375BC14` and writes it through `0x63CC3B6E` at `0x6375BC46`. This gives an original-code handoff from the input dispatcher into the front-end control block, but the branch and its effect remain unobserved. The second replay counted two visits at the start-control store instruction `0x63CC3C34`, zero at the IRQ 36/37/38 handler entries, and zero at `0x6375BBF0`, the CamPig mask dispatcher, and the Ydet producer.

The [pinned a5000 CamPig model](https://github.com/Oein/a5000-emulator/blob/6ad36bd7b0411d55a5920447ff51e3de01f8cb9b/qemu-overlay/hw/misc/cxd90014_campig.c) uses `+0x210` start/continuous bits 0/8, `+0x220` geometry, `+0x224` event-A line, and status/enable bit 0 at `+0x48/+0x50` for its input unit. Those offsets and the a6400 writes align. Its IRQ is 35 and its calculation uses 27 MHz for 819 × 1100 source clocks. Applying that geometry encoding to the a6400 yields 840 × 1430 = 1,201,200 clocks per frame and compare line 739. A 36 MHz source gives the same approximate 33,367 µs period as the firmware timing constant and the existing EVSEL model. The a6400 clock and its physical phase remain inferred. An input event at IRQ 36 and the relation between that event and path 2/3 statuses at IRQ 37 still require validation.

## Ydet data boundary

The first result getter's `0xA0`-byte read resolves through the Thumb-to-ARM veneer at `0x63DBA0C8` to the original copy routine `0x63AB8098`. The consumer uses all 40 words per selected array. For one path `0x63BEDE1C–0x63BEDE38` sums two arrays after separate shifts. Other branches retain and shift one array. No observed boot produced a result. These instructions qualify the size and downstream arithmetic, while the hardware's per-word meaning, source data and scale remain open.

## Reproduction

The E0567 experimental QEMU binary SHA-256 was `87c324f41fdc42dc88d479bf87eb63a0feaf839b8b07fd523439ec2056d26264`; ILCE-6400 ORIL SHA-256 `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`. E0561 legacy inputs, fresh overlay, `CAMPIG_ENABLE_READBACK=1`, `EVSEL_LATE_US=500`, and `icount shift=0,sleep=on`. Replay and passive TCG traces are under `artifacts/qemu-a6400/campig-frame-source/` and `campig-sync-boundary/`. Run the retained `replay.py` in WSL from the repository root with `A6400_QEMU_DIR` pointing to the E0567 experimental build. Both traces used a 128-access budget for the input range and captured the table at 70 s. Both reached step 32 with module 3 at `0x0326` against `0x032E` and exited 0 via QMP quit. The sampled callbacks were examined with bounded GNU Thumb disassembly and the result veneer also with ARM disassembly. No event, result or device behaviour was injected.
