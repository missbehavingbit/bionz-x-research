# E0553 — QEMU `sony-a6400`: the module 1 latch never publishes `0x0105`

Date: 2026-11-17. **Strictly offline.** Builds on E0551. E0552 is a separate ILCE-6400A `nflasha5` receipt, so this result is E0553.

Labels: [C] confirmed by disassembly or a run receipt, [I] inference, [A] assumption.

## 0. Short answer

`0x64351F28` is the reported halfword E0549 copies from `0x64351898 + 0x690`. A write watchpoint caught two instructions. `0x63922304` seeds it with `0x0100`. `0x639267BC` writes it again only after `0x63D3BD56`, and that function returns the old halfword for every request except `0x0112`. At `SetContentTarget` the request halfword at `0x64350F04` is already `0x0105`, the reported halfword is still `0x0100`, and the byte that lets `0x639267BC` run is 0. No device model changed. The sequencer index stays 12.

## 1. What the watchpoint caught

`tools/a6400_probe.py` launched `run.sh` with `QEMU_EXTRA="-icount shift=0,sleep=on -S -gdb tcp::12345 -d unimp -D /home/administrator/a6400-runs/e0552-unimp.log"`. A temporary RSP client armed `Z2,64351f28,4` on threads `p01.01` through `p01.04`. QEMU retraps a TCG watchpoint on the same instruction, so each hit disarmed the watchpoint, single-stepped, and rearmed it [C].

The seed, thread `p01.03`, eight times at the same instruction before the watchpoint was removed [C]:

| field | value |
| --- | --- |
| PC | `0x63922304` (`strh.w r0, [sl, #0x24]`) |
| LR | `0x639222B5` |
| r0 | `0x00000100` |
| r10 (`sl`) | `0x64351F04` |
| r4 | `0x64350F04` |

`0x639222B0` calls `0x638E8410` with the index in r0. That getter returns the sequencer target. The store writes it at `0x64351F04 + 0x24`, which is `0x64351F28` [C]. The memory halfword was still `0x0000` at the stop, because the watchpoint hits before the store [C].

The second writer is `0x639267BC` (`strh r0, [r7, #0x24]`). Thirty-nine stops, all on `p01.03`, had r7 = `0x64351F04`, r3 = `0x64350F04`, and r0 = r1 = `0x0100` [C]. Holding every one of those stops paused the guest: that boot's monitor dump shows `0x04351F28 = 0x0100` and `0x04330614 = 0x00000000`, with CPU2 parked at `R15=0x639267BC`, `PSR=0x80000033` [C]. Those hits are early. They do not sample step 12.

The instructions immediately above the store are [C]:

```
6392679a  ldr.w r3, [pc, #0x648]   ; resolves to 0x6434CA54
639267a0  ldr r3, [r3, #0x24]
639267a2  ldrb r3, [r3, #8]
639267a4  cbz r3, #0x639267be      ; skip the update when the byte is 0
639267aa  ldr.w r3, [pc, #0x63c]   ; resolves to 0x64350F04
639267b4  ldrh r1, [r3]            ; request halfword
639267b6  ldrh r0, [r7, #0x24]     ; reported halfword
639267b8  bl #0x63d3bd56
639267bc  strh r0, [r7, #0x24]
```

`0x63D3BD56` is [C]:

```
63d3bd56  cmp.w r1, #0x112
63d3bd5c  mov r4, r0
63d3bd5e  bne #0x63d3bd70          ; any other request returns the old halfword
63d3bd60  bl #0x638e9480
63d3bd66  ite ne
63d3bd68  movne r0, r4
63d3bd6a  moveq.w r0, #0x112
```

`0x638E9480` and the helper it calls, `0x638E91D8`, load RAM words through PC-relative literals and test bits. They run only when the request is `0x0112` [C]. `0x0112` is not the step-12 request `0x0105`.

## 2. What the stall samples show

Boots that removed the watchpoint, or never stopped on it, reach `SetContentTarget` with status 0 [C].

| log | checkpoint | `0x04351F28` | `0x04350F04` | `0x04330614` | other |
| --- | --- | --- | --- | --- | --- |
| `e0552d-probe.log` | 49.1 s | `0x0100` | `0x0105` | `0x0000000C` | breakpoint at `0x63D38136` never hit |
| `e0552e-probe.log` | 46.8 s | `0x0100` | `0x0105` | `0x0000000C` | `0x0434CA78 = 0x6435416C` |
| `e0553-enable.log` | 48.5 s | not read | not read | not read | `0x04354174 = 0x00`; `0x0435416C = 04 02 00 00` |

`0x6434CA54 + 0x24` is the pointer loaded at `0x639267A0`. Its byte at `+8` is the `cbz` operand, and that byte is 0 [C]. A run of `0x639267A4` at this stall takes the skip and does not call `0x63D3BD56` [I]. The request halfword is already `0x0105`, so the skip is not waiting for the request to be written [C].

`/proc/interrupts` on the 46.8 s boot: `liro-83` 50, `liro-84` 506, `liro-98` 49, `liro-101` 99, `liro-102` 99, `liro-204` 215. `liro-35` through `liro-38` and `liro-51` through `liro-56` are 0 [C]. The same boot printed `en144=0x3000232` [C]. The `-d unimp` log from the first completed boot has 47,366 lines across 52 unmodelled 4 KiB pages. The busiest are `0xF2804000` (3,615), `0xF3C08000` (3,435), `0xF2401000` (845), `0xF2912000` (831), and `0xF3C04000` (711) [C]. None of those addresses is read by `0x6392679A`–`0x639267BC` or by `0x63D3BD56` [C].

## 3. Steps 13–42

The index at `0x04330614` is 12 on every boot that reached the checkpoint [C]. Step 12 is the wait `0x8347` from E0544. `VD_Pre` CamCore start, imager `PowerOn`, P2S (`liro-38`), and SA0 (`liro-51`) stay where E0543 left them: the IRQs are still 0 and `en144` bit 0 is still clear [C]. This record does not add a new observation inside steps 13–42.

## 4. Boundary

No device model changed, and `qemu-system-arm` was not rebuilt. Nothing wrote `0x0105` into guest RAM.

The watchpoint was not held through step 12. A later store to `0x64351F28` is not ruled out [A]. The completed boots still show `0x0100` there. The updater that was caught cannot publish `0x0105`: its enable byte is 0, and its latch returns the old halfword unless the request is `0x0112`.

The next measurement is the writer of the byte at `0x64354174`. Making that byte nonzero would still not publish `0x0105`, because `0x63D3BD56` rejects that request. The unmodelled pages in the `-d unimp` log are not that branch.
