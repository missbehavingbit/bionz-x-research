# E0566 — EE accepts the Ydet callback and leaves it pending

Date: 2026-11-17. Offline follow-up to [E0565](0565-qemu-superslow-field-and-pqd-came-dispatch.md).

The runtime [E0569](0569-qemu-sgc1-event-a.md) replay supersedes the IRQ 37 assignment below. The `0xF3000000` handler runs on IRQ 36.

## Submission

The first passive replay counted 214 entries at `0x63CBCA06`, 214 returns to the caller's status branch `0x63903150`, and 214 executions of its zero-status target `0x6390316C`. The error path at `0x63903152` had zero hits. The extended replay repeated the outcome for 212 calls. In that run, the lower helper `0x63CBC4AC` always reached the binding call `0x63CBC4F2 → 0x63CBC152` and advanced into `0x63CBC2DA`. Its lower-level error return at `0x63CBC520` and the parent's error propagation at `0x63CBCC0E` had zero hits.

## Pending and active slots

The third replay sampled the actual 32-bit stores from `0x63CBC152` and `0x63CBC156` on controller object `0x641D660C`:

| Offset | Guest VA | Stored Thumb callback |
| --- | --- | --- |
| `0x80` | `0x641D668C` | `0x63902261` |
| `0x84` | `0x641D6690` | `0x639023AD`, the E0565 Ydet job callback |

The binding method executed 194 times. The value sampler was capped at 128 observations across the selected addresses. The promotion function `0x63CBC586` had zero hits. In original instructions it reads `+0x84` at `0x63CBC5A4`, clears that slot, and writes its value to `+0x88` at `0x63CBC5B0`. It also consumes and invokes the other callback at `+0x80`. The completion function `0x63CBBF64` loads `+0x88` at `0x63CBBF6E`, clears the slot when nonzero and branches through it at `0x63CBBF7A`. Its load site and the Ydet callback had zero hits in the third run.

Thumb addresses `0x63CBBF65` and `0x63CBC587` occur in GOT slots `0x645A6098` and `0x645A8550`. Firmware constructs callback descriptors for them at `0x637575A0–0x637575E4`, then passes the descriptors to virtual methods of the object at controller offset `+0x48`, using the masks from `+0x7C` and `+0x78`. A separate routine at `0x637571D8` handles the same callback identities through different virtual slots. These are original instruction findings. The event source and delivery semantics require runtime verification.

The event-owner replay observed firmware write owner VA `0x640BD2F4` at `0x63CBC976`. The primary registration calls at `0x637575BA` and `0x637575E4` each executed once. The separate routine at `0x637571D8` had zero hits. At 70 s, the controller snapshot still had `+0x84 = 0x639023AD` and `+0x88 = 0`. The values of `+0x78 = 0x161` and `+0x7C = 0x3298F` are the final controller masks from that snapshot. The startup values need to be sampled at the actual calls, since subsequent jobs augment the masks.

The loopback RSP replay sampled the completion registration with `r0 = 0x640BD2F4`, `r1 = 0x981`, and Thumb target `r9 = 0x6375ADF5`. It stopped collecting at its 48 s bound after the first registration hit. The snapshot of that owner yielded vptr `0x644F9B38`. Its original RTTI identifies `Camera::CamCore::CamPigInterruption23`, and its slots `+0x08/+0x0C` name promotion method `0x63CC32C5` and completion method `0x6375ADF5`. The subobject at owner `+0x0C` has register base `0xF3000000` at its `+0x04`. The embedded `Iif` has separate base `0xF3121000`. QMP and RSP both exited successfully.

Original registration code sets the promotion path's arm bit 2 at register `+0x10`, writes its parameter at `+0x20`, and uses interrupt enable bit 26 at `+0x50`. The completion path uses arm bit 3, parameter `+0x24`, and enable bit 27. Firmware IRQ table row `0x64504510` associates raw IRQ field 37 with `0x63CC3851`. The handler explicitly reads `0xF3000048` and `0xF300004C` and writes their nonzero values back to acknowledge status.

The `campig-guest-mmio` replay confirmed the original 32-bit accesses in order. Completion wrote `+0x10 = 8`, `+0x24 = 0x981`, read `+0x50 = 0`, wrote `+0x50 = 0x08000000`, and wrote `+0x14 = 8`. Promotion then wrote `+0x10 = 4`, `+0x20 = 0x161`, read `+0x50 = 0` again, wrote `+0x50 = 0x04000000`, and wrote `+0x14 = 4`. The second write to `+0x50` is a read-modify-write. The current catch-all drops the first enable bit, so the guest never computes the combined enable value in this run. Both registration methods ran once, with zero status acknowledgements at the two sampled accessors. The trace selected addresses without depending on the QEMU plugin I/O classification.

The [pinned a5000 CamCore model](https://github.com/Oein/a5000-emulator/blob/6ad36bd7b0411d55a5920447ff51e3de01f8cb9b/qemu-overlay/hw/misc/cxd90014_campig.c) provides the same register offsets and per-path status/enable bit layout for unit 0. It uses IRQ 36 for this base on the a5000. The a6400 firmware table value and the original clear/dispatch instructions govern further tests. No guest interrupt delivery or statistics payload was verified in this record.

## Reproduction

Use `python3 -u` under WSL from the repository root with the retained `replay.py` in `artifacts/qemu-a6400/ee-submit-return/`, `ee-callback-binding/`, `ee-callback-activation/`, `ee-event-owner/`, `ee-event-registration/` and `campig-guest-mmio/`. These use original firmware ORIL `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`, QEMU binary `2720703c2b200a1dbaf9ee7c7e4a99e3e6a8c026b21e7517bcd7b836354486a7`, E0561 legacy inputs, the existing `EVSEL_LATE_US=500` phase assumption and fresh overlays. The TCG replays sampled through 70 s host time and the RSP replay sampled through 58 s. Each run ended via QMP quit. The mode sequencer remained at step 32 with request `0x032E` and report `0x0326`. No model or guest values were changed. Original instructions were checked with bounded GNU Thumb disassembly.
