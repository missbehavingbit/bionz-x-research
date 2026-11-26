# E0545 — Unicorn and QEMU match the updater return window except CPSR.A

Date: 2026-11-16. **Strictly offline.** Same captured `nflasha1` kernel and initrd on Unicorn and the patched QEMU `sony-a6400` machine. No camera, USB, disk, or network.

Labels: [C] confirmed by a run receipt, [I] inference.

## 0. Short answer

At the first instruction of `ret_to_user` (`0x6010DF34`), both engines have the same instruction bytes and the same `r0`–`r12`, `sp`, and `lr`. The one differing register is CPSR: Unicorn `0x40000053`, QEMU `0x40000153`. The difference is bit 8, the async-abort mask [C].

The historical loader address `0x0402E790` is not where this updater enters user mode. Unicorn enters Thumb user mode at a stable `0x0400C790` (`CPSR=0x30`). QEMU also enters Thumb user mode (`CPSR=0x30`), but the virtual address changes on every boot. Each observed address ends in `0x790` [C].

## 1. Inputs and tools

Both engines used `init=/bin/ash console=ttyAM0,115200n8 amba2.console=1`.

| Input | SHA-256 |
| --- | --- |
| Decompressed `nflasha1` kernel | `ae0d4448a803850b082a80f3b9fc8dcc733d692c9d1125bf838258b7c84ff925` |
| `nflasha1` initrd | `e6315bf0c84300b763b33dc3a9a5e9ac2b4fdb18090ecd80e136f86afa4f1845` |
| `qemu-system-arm` | `98ed7df350a76281b886bccb19b99c3381ff8317891c43a86649c0da32d79b1e` |
| GDB 17.1 `gdb-multiarch` | `450401555504d3cd80e67a1a4b993e7e5b830f5d7a129153a29b01459b102039` |

QEMU was one Cortex-A5, `-icount shift=0,sleep=on`, no disk and no network. GDB reported `qqemu.sstep` = `0x7` (single-step with IRQ and timer inhibited) [C]. This GDB target does not expose `$spsr` (the value reads as void). Unicorn did record SPSR [C].

Receipts: `artifacts/analysis/thumb-unicorn-01/`, `thumb-unicorn-02/`, `thumb-qemu-return-02/`, `thumb-qemu-return-03/`, `thumb-compare-return-01/`.

## 2. Return window

The window is `0x6010DF34`–`0x6010DF64` (`bl` through `movs pc, lr`). Sixteen instructions were kept. The first visit walks the window in order; the next three rows are the start of a second visit [C].

Unicorn run 2 repeated run 1's sixteen return rows exactly. QEMU run `thumb-qemu-return-03` repeated `thumb-qemu-return-02` exactly [C].

Rows 0–12 differ only in CPSR bit 8 (QEMU has A set). Rows 13–15 differ only in CPSR bit 6 (Unicorn has F set). The comparison report stops at row 0, which is the earliest difference [C]. Both stops are the same PC afterwards: Unicorn hits its instruction limit in `cpu_v7_do_idle+0x8` (`0x6011A23C`), and QEMU's monitor dump at shutdown is the same PC [C].

Unicorn's serial log reaches `BusyBox v1.13.4` and `/ # ` (`user_restores = 292`, `serial_bytes = 9727`) [C].

## 3. Loader window

`0x0402E790` produced no Unicorn rows and was not used. The user frame Unicorn restores is `PC=0x0400C790`, `CPSR=0x30`. The first four bytes there are `df f8 68 a0`. Run 2 captured the same entry [C].

QEMU's `ldr lr, [sp, #0x3c]!` value at the user-mode `msr` (`r1=0x30`) was, on separate boots, `0x04002790`, `0x0405F790`, `0x040EF790`, and `0x0404A790` [C]. Stepping the following `movs pc, lr` landed on that same address with `CPSR=0x30` [C]. A breakpoint planted at one boot's address does not fire on the next boot [C]. GDB could stop on the landed PC and could not read the instruction bytes (`Cannot access memory`) [C].

The shared page offset is `0x790`. The slide is the difference, not a second instruction stream that this GDB session could read [I].

## 4. Next boundary

Do not patch the return path to hide CPSR.A. The next reduction is the CPSR value each engine has at kernel entry, before `ret_to_user`. The loader comparison needs a capture that reads the bytes at QEMU's per-boot user PC, then compares those bytes with Unicorn's `0x0400C790` window.
