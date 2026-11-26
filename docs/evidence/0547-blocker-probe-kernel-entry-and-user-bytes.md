# E0547 — The blocker probe reads kernel-entry CPSR and the user instruction

Date: 2026-11-17. **Strictly offline.** Same captured `nflasha1` updater inputs as [E0545](0545-unicorn-qemu-return-window-cpsr-a.md). No camera, USB, disk, or network.

Labels: [C] confirmed by a run receipt.

## 0. Short answer

`--stop kernel-entry` and `--stop user-return` answer the next two questions without a new return-window boot. At physical `0x00108000` both engines execute `d3f021e3` with `r0 = 0`, `r1 = 0xDA9`, and `r2` at the boot params. QEMU's CPSR is `0x600001D3` and Unicorn's is `0xD3`. The named CPSR bits are Z, C, and A. `r3` through `r6` also differ [C].

The restored return snapshot reaches user mode at a stable `0x040C2790`, CPSR `0x30`. GDB cannot read that address. The probe reads `$TTBR0_EL1`, walks the short-descriptor tables, and reads physical `0x42E790`. The four bytes are `dff868a0`, the same bytes Unicorn recorded at `0x0400C790` [C].

## 1. Receipts

| Run | Role | Result |
| --- | --- | --- |
| `artifacts/analysis/thumb-unicorn-kernel-entry-01` | Unicorn, one instruction, `--thumb-stop kernel-entry` | CPSR `0xD3`, bytes `d3f021e3` |
| `artifacts/analysis/thumb-qemu-kernel-entry-01` | QEMU cold boot, saves `thumb-checkpoint-02/kernel-entry` | CPSR `0x600001D3`, same bytes |
| `artifacts/analysis/thumb-compare-kernel-entry-01` | Comparison | `cpu_state`, `cpsr_bits` `Z`, `C`, `A` |
| `artifacts/analysis/thumb-qemu-user-return-01` through `-04` | Restore `thumb-checkpoint-02` | Landed PC `0x040C2790` every time |
| `artifacts/analysis/thumb-qemu-user-return-04` | Physical read | bytes `dff868a0`, CPSR `0x30`, TTBR0 `501088362` |
| `artifacts/analysis/thumb-compare-user-return-01` | Against Unicorn loader row 0 of `thumb-unicorn-02` | Bytes and CPSR match. `r1` and `sp` differ. Addresses `0x0400C790` and `0x040C2790` |

`$ttbr0` is void on this GDB. The populated register is `$TTBR0_EL1` [C].

## 2. What this does not change

Unicorn's initial CPSR stays `0xD3`. The Z, C, and A bits are an observation at kernel entry, which is as early as this boot gets. The user virtual address still differs. Its page offset is `0x790` on both engines, and the instruction bytes match.
