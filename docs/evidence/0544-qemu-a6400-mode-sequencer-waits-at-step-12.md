# E0544 — QEMU `sony-a6400`: the mode sequencer waits at step 12 on module 1

Date: 2026-11-16. **Strictly offline.** Builds on E0543. The viewfinder skin stays parked.

Labels: [C] confirmed by disassembly or a run receipt, [I] inference, [A] assumption.

## 0. Short answer

The mode sequencer has already copied the startup requests, including imager `0x0A10`, and is sitting on the first wait. The wait mask is `0x0347`. Of the modules in that mask, only module 1 disagrees: the request is `0x0105` and the reported state is `0x0100`.

## 1. What was measured

`av-cam.bin` startup table at `0x63E80F18` is the same shape as the a5000 step-12 table [C]:

```
 0 0105 0285 0320 0400 0500 0600 0780 0910 0a10 0b00 0c01 0d00
12 8347 WAIT
```

`0x638E85A0` is the response check. It runs only when the byte at `0x6448F9F8` is non-zero and the current step has bit 15 set. For each set bit `i` of the low 13 bits it compares the halfword at object `+0x18 + 2i` with the halfword at object `+0x32 + 2i`. A mismatch returns 0 and the index stays put [C].

`tools/a6400_probe.py --until SetContentTarget` reached the checkpoint at 48.0 s, status 0. `pmemsave 0x043305C0 256` shows the object whose fields match those offsets at VA `0x64330600` [C]:

| field | value |
| --- | --- |
| `+0x0C` step table | `0x63E80F18` |
| `+0x10` step count | `0x2B` (43) |
| `+0x14` index | `0x0C` (on the wait) |
| `+0x08` active | 1 |

The checker byte `0x6448F9F8` is 1 [C]. Request word `0x3003` is stored at `0x643305F4` [C]. Masked pairs:

| bit | request | reported | |
| --- | --- | --- | --- |
| 0 module 1 | `0x0105` | `0x0100` | differs |
| 1 | `0x0285` | `0x0285` | |
| 2 | `0x0320` | `0x0320` | |
| 6 | `0x0780` | `0x0780` | |
| 8 | `0x0910` | `0x0910` | |
| 9 imager | `0x0A10` | `0x0A10` | |

## 2. Thumb PC read

16-bit `add rd, pc` is the firmware's PC-relative address form. QEMU `load_reg` of r15 returns the instruction address plus 4 and does not force bit 1 to 0. `add_reg_for_lit` does align. An `add` at an address that is 2 modulo 4 is therefore 2 bytes high [C]. Forcing the clear in `load_reg_var` made `/sbin/init` take a section translation fault at `0xCFD00000` and never reach a shell. That edit was reverted and `qemu-system-arm` rebuilt. The dump above is from the unpatched translator [C].

## 3. What this does not show

`0x0A10` on both sides means the reported halfword matches the request. It does not show that imager `PowerOn` ran. No model change is kept.

## 4. Next boundary

What module 1 is, and why its reported state stays `0x00` when the request is `0x05`. The wait does not move until that pair matches. The Thumb PC alignment stays a separate translator question; a global clear of bit 1 is not a bootable change.
