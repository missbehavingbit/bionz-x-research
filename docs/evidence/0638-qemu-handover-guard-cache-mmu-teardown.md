# E0638 — QEMU handover guard: ARMv7 cache/MMU teardown checks before a kexec-style jump

Date: 2026-11-19. Offline only: QEMU TCG plugin, constructed bare-metal fixtures,
one bounded stock-firmware boot. No camera access.

## Why

Custom OS prerequisite 4 (local roadmap section "Custom OS prerequisites"): QEMU TCG
has no cache contents, so a teardown that forgets to clean the D-cache, leaves the MMU
on or skips a barrier still reaches the new kernel in QEMU but would fail on hardware.
The emulator therefore has to check the *architectural steps*, not the outcome. The
secondary-core part of prerequisite 2 is covered by the same guard (G9); DMA
quiescence is not.

## Tool

`qemu-a6400/tools/handover_guard.c` (QEMU plugin API v7, SHA-256
`d1ad7498ce79f21190f39ea19666d120079d9e217be49a490ccee20b78d34fa4`). Argument
`entry=0xADDR` names the handover target. When the handover vCPU first executes it,
the guard reports:

| Item | Requirement (mainline `__soft_restart` → `cpu_v7_proc_fin` → `v7_flush_kern_cache_all` → `cpu_v7_reset`) |
| --- | --- |
| G1–G3 | SCTLR.M, .C, .I all 0 (read from the gdb sysreg `SCTLR_EL1`, cross-checked with decoded `MCR p15,0,Rt,c1,c0,0` values; on disagreement the union of M/C/I is used) |
| G4 | ≥ `min_setway` unconditional DCCISW after the SCTLR write that turned C off (default 1024 = QEMU cortex-a5 CCSIDR `0xe01fe019`: 32 KiB, 4 ways × 256 sets × 32 B; CLIDR LoC = 1) |
| G5 | ICIALLU/ICIALLUIS after the last DCCISW |
| G6 | DSB after the last cache-maintenance operation |
| G7 | ISB after the last SCTLR write |
| G8 | CPSR.I and CPSR.F set |
| G9 | every other vCPU that ran is in a WFE/WFI loop (≤ `park_window` = 8 instructions since its last WFx) with SCTLR.C = 0 there |

`exit=1` ends QEMU with status 0 (PASS) or 3 (FAIL). Decoding covers A32 and T32
`MCR p15`, `DSB`/`ISB` (including the CP15 c7 forms) and `WFE`/`WFI`; conditional
maintenance is logged but not counted. T32 versus A32 is decided from the encoding
alone (the plugin does not see CPSR.T at translation time) [limitation].

Explicitly **not** checked (printed in every report): outer L2 (the cmdline has
`cache_l2x0.wait=3`, so a PL310-style controller is likely [I], but it is not modelled),
DMA engine quiescence, GIC state, TLB maintenance.

## Fixture

`qemu-a6400/tools/handover_payload.S` (`29c0ac98…aa936b`) is loaded as a raw
"kernel" at `0x00108000`; CPU1–3 stay in the board's SMP mailbox WFE loop. It enables an
identity-mapped MMU with I/D caches, copies a WFI-loop "new kernel" to `0x00600000`
with the D-cache on, and tears down in mainline order, including a transcription of
`arch/arm/mm/cache-v7.S` `v7_flush_dcache_all`. Each `--defsym` drops one step.

## Results

Toolchain: gcc / arm-linux-gnueabi-gcc 15.2.0 (Ubuntu 15.2.0-16ubuntu1); QEMU
`qemu-system-arm` SHA-256 `7a8caef8…68b70f` (11.1.3 tree with the overlay at upstream
`0419ce4` plus E0636). Command per variant:
`qemu-system-arm -M sony-a6400 -m 1G -kernel V.bin -display none -serial none -monitor none -net none -plugin handover_guard.so,entry=0x600000,exit=1 -d plugin -D V.log`

| Variant | Exit | Failing items |
| --- | --- | --- |
| GOOD (mainline order) | 0 | none — 1024 DCCISW after C-off, SCTLR `0x00c50078` from both sources, CPSR `0x600001d3`, CPU1–3 parked |
| KEEP_MMU | 3 | G1 |
| KEEP_DCACHE | 3 | G2, G4 |
| KEEP_ICACHE | 3 | G3 |
| NO_CLEAN | 3 | G4, G5 |
| NO_ICINV | 3 | G5 |
| NO_DSB | 3 | G6 |
| NO_ISB | 3 | G7 |
| IRQ_ON | 3 | G8 |
| ROGUE_CPU1 (CPU1 released into `b .`) | 3 | G9 |

Every broken variant still reached `0x00600000` in QEMU, which is the hazard this
guard exists for. `tests/test_qemu_handover_guard.py` (`5470d1fe…936b`) rebuilds the
plugin and all ten payloads and asserts exactly these sets: 2 tests OK in WSL.

Stock firmware sanity (`run.sh`, `TIMEOUT=90`, `-icount shift=0,sleep=on`, guard with
an unreachable `entry=0xfffffff0`): exit 124 (timeout), 1,153 console lines, last kernel
stamp 3.63 s, report `RESULT NOT-REACHED`. The guard also decoded the stock early boot:
a full 1024-op DCCISW pass at `0x00119cf8`, ICIALLU, TLBI and the MMU-on SCTLR write
`0x10c5387d`. So the decoder recognises the original kernel's own cache code. Not a
paired comparison; it only shows the plugin does not stop the boot.

Receipts: `artifacts/qemu-a6400/e0638-handover-guard/` (all payload binaries and logs,
`summary.txt`, `toolchain.txt`, firmware console/guard/stderr logs, `SHA256SUMS`).

## Assumptions and limits

- QEMU's cortex-a5 cache geometry stands in for the CXD90045 L1; the real CCSIDR has not
  been read from the camera. `min_setway` must follow it.
- The guard checks order and presence, not cache contents; it cannot prove the data
  reached PoC on hardware. It also cannot see L2.
- The fixture is constructed code, not a Custom OS kernel or `kexec` from the stock kernel.

## Next boundary

Prerequisite 2's DMA part: per-engine "idle" contracts (OSD/display, USB DWC3, PL330,
sensor AIF) that the guard can check at `entry`. Prerequisite 1: verified models for the
read-back windows. Also: an outer-cache model or at least PL310 register tracking, once
its base is identified.
