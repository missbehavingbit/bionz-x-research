# E0548 — The boot-stub registers die before `__v7_setup`; MIDR and CPSR.A remain

Date: 2026-11-17. **Strictly offline.** Same captured `nflasha1` updater inputs as [E0545](0545-unicorn-qemu-return-window-cpsr-a.md) and [E0547](0547-blocker-probe-kernel-entry-and-user-bytes.md). No camera, USB, disk, or network.

Labels: [C] confirmed by a run receipt or the kernel bytes, [I] inference.

## 0. Short answer

The `r3`–`r6` values at physical `0x00108000` are leftovers from the QEMU GIC stub. By `movs sl, r5` at `0x00108010` those leftovers are gone, and `r0`, `r1`, `r2`, `r6`, `r7`, `r8`, `sp`, and `lr` match. CPSR at that instruction differs only in the A bit [C].

Unicorn's MIDR is `0x410FC090` (Cortex-A9). QEMU's is `0x410FC051` (Cortex-A5). The lookup therefore returns adjacent `proc_info` slots. Both slots branch to the same setup at `0x0035DA20`, and that setup's first instruction overwrites the slot pointer. At the `bne` after the shared `ID_PFR0` test (`0x0035DAC8`) the only differing registers are `r9` and CPSR.A. `ID_PFR0` itself is `0x00001031` on both engines, and the page-table registers match [C].

## 1. Why a physical `pc:` stop used to miss

A `pc:` address below `0x60000000` was treated as a user-mode stop. The kernel head executes in SVC with the MMU off, inside the 3,581,952-byte image at `0x00108000`. Those addresses now stay SVC, and the byte read stays physical. A user address above that image, such as `0x0400C790`, is unchanged [C].

## 2. Receipts

QEMU restores `artifacts/analysis/thumb-checkpoint-02/kernel-entry`. The binary hash is still `78da642848ed5ce4ce1762dbfb4212f262663c956d45412607846506a742cfe5`. Each restore reports migration `completed` [C].

| Stop | Instruction | Comparison | What still differs |
| --- | --- | --- | --- |
| `0x00108010` | `movs sl, r5` | `thumb-compare-head-01` | `r3`, `r4`, `r5`, `r9`, CPSR.A |
| `0x0035DA20` | `mov sl, #1` | `thumb-compare-v7setup-01` | `r9`, `r10`, CPSR.A |
| `0x0035DAC0` | `and r0, r0, #0xf000` | `thumb-qemu-pfr0-01` | `r9`, CPSR Z and A. `r0` is `0x1031` on both |
| `0x0035DAC8` | `bne` | `thumb-compare-pfr-bne-01` | `r9`, CPSR.A |

The bytes at each stop match. `r4` and `r8` at the `bne` are both `0x0010406A` [C].

## 3. What the kernel bytes say

`stext` begins with `msr CPSR_c, #0xD3`. That write covers bits 0–7 only, so it cannot clear A or the condition flags [C].

The two `proc_info` slots are 52 bytes apart at `0x00364FE4` (value `0x410FC050`, mask `0xFF0FFFF0`) and `0x00365018` (value `0x410FC090`, same mask). Every pointer word matches. The branch at offset 16 encodes a different immediate only because the instruction addresses differ; both targets are `0x0035DA20` [C].

Inside that setup, the part-number tests for `0xC08` and `0xC09` leave Z set on the A9 path and clear on the A5 path. The following `teq` against `ID_PFR0` bit 12 sets Z again on both, which is why Z matches at `0x0035DAC8` and did not match one instruction earlier [C].

## 4. What this does not change

Unicorn still has no Cortex-A5 model, so its boot CPU remains Cortex-A9. CPSR.A is still the bit that [E0545](0545-unicorn-qemu-return-window-cpsr-a.md) found at `ret_to_user`. No boot register was changed.

## 5. Next boundary

`r9` still holds the MIDR when setup returns to `0x00108044`. The next stop is the first instruction after that return that reads `r9`. CPSR.A stays a separate entry-state fact: QEMU's `cpu_reset` sets it, and this kernel never writes that bit in the head.
