# E0528 — QEMU `sony-a6400`: the `ObjMic` start stall is an unanswered av-cam ADF request from the `AVBB_SCN_START_MICSENSE` scenario

Date: 2026-11-16. **Strictly offline.** No camera access. Builds on E0527.

Labels: [C] confirmed by a run receipt or disassembly, [I] inference, [A] assumption.

## 0. Short answer

1. **Where it waits [C].** At the stall, `snsc_boot_time` has `B Og 001_ObjMic` with no `E`, while every
   other object's `Og` ends. Thread snapshot (`threads-at-objmic-stall.txt`): `ObjMic` waits on a futex
   for its interface thread `ObjMicIThread`, which waits in `osal_rcv_msg_tmo`. No other thread is in an
   unusual wait.
2. **What it waits for [C].** `libObj.so` strings give the chain `AVBIZ::MIC::ObjMic` →
   `SeqProcMicSense` → `BackendController::Process_BbMicSense()` → scenario `AVBB_SCN_START_MICSENSE`
   (`/usr/scenario/AVBB_SCN_START_MICSENSE.so`, `97a41568…`). Its `scenario_run` sends av-cam's
   audio data flow (ADF) `SetPathADF(0xA0)` and `SetInputADF(1, 6)`. The sibling `AVBB_SCN_START_MIC`
   sends `SetPathADF(0xA2)`, `SetInputADF(0, 2)`, `SetOutputADF(2)`, `SetThruADF`. Each command goes
   through `SndDataFlowMsgAsyncCheckCompBase` to dataflow ID `0x0058003F`, then
   `DataflowInfraReceiver::ReceiveASync(reply, msg, 0xFFFFFFFF)`, an **infinite wait for ADF's
   completion message**.
3. **ADF receives, but does not complete [C/I].** UIPC queue `0x3F` has one consumer and no
   pending messages, so av-cam takes the request. The completion never reaches the scenario [C].
   Queues `0xEF` (1), `0x3C4` (2), `0x42C` (1), `0x465` (10) and `0x5A9` (2) hold unconsumed messages
   (C = 0); their owners are not identified [?]. That ADF waits on the AudioIp hardware is an
   inference from a5000 `RE_AUDIO.md` (AIP periodic handler reads DMA position registers) [I].
   Microphone sensing (path 0xA0, external mic jack) has no a5000 counterpart: the a5000 notes found
   audio initialising fully and never blocking.
4. **AudioIp read-back [C].** `cxd90014_regfile.c` was ported from `Oein/a5000-emulator` (GPL-2.0,
   attributed) and mapped as a read-back window over AudioIp `0xF3420000–0xF3427FFF`; the a5000
   needed read-back there for audio init. That **does not** unblock `ObjMic`
   (`objmic-probe-audioip-readback.txt`: `B Og 001_ObjMic` at 1.789 s, no `E` after 80 s). It is kept,
   with no regression seen (shell at 4.0 s, same checkpoints).
5. **Failure preserved [C].** Mapping the read-back register file over all of
   `0xF0000000–0xF7FFFFFF`, as the a5000 board does, broke the a6400 USB core:
   `dwc3 dwc3.0: failed to initialize core` / `probe … failed with error -110`, because a self-clearing
   reset bit read back as set. The shell came up 2.5× later (10.8 s vs 4.0 s)
   (`console-global-readback-dwc3-regression.log`). Read-back is therefore applied per block only.
6. **Tools [C].** `ulogio.elf` in the guest can save every log area: `(echo alog -p /tmp/al/; echo exit)
   | /usr/bin/ulogio.elf` writes `ulogio_blog.dat` (4.6 MB binary, holding av-cam logs),
   `ulogio_dlog.dat`, `ulogio_klog.txt` and `ulogio_tlog.txt`. The tlog file is a copy of
   `snsc_boot_time`. `tools/a6400_probe.py` now gives post-timeout commands their own time budget.
   Guest BusyBox has no `printf`/`head`/`tail`: use `echo` subshells and `grep -A`.

## 1. Receipts

| Artifact | SHA-256 |
| --- | --- |
| `artifacts/qemu-a6400/e0527/uipc-at-objmic-stall.txt` | `c88fd1275bbc22061586dd6f00d590a48d9d57ea71796a0d8932079a1125f404` |
| `artifacts/qemu-a6400/e0527/threads-at-objmic-stall.txt` | `6f2985deeab2be8a524ac8af27214029a38ae741979ee53a1467455879bda53e` |
| `artifacts/qemu-a6400/e0527/objmic-probe-audioip-readback.txt` | `3b7432ec68b0c1fcd118f99c534028c6c8be9a952f27f3dc2646290ca5bea2c7` |
| `artifacts/qemu-a6400/e0527/console-global-readback-dwc3-regression.log` | `db14ba7e8b2cab60ce7c13c2ee4634cc9589b49499ea4e54aea0c0d825bc201d` |
| `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` | `71cb31d7c6d071631c37ad311450dcf3ff64e7da017ef1358f16fcb8da187a78` |
| `qemu-a6400/qemu-overlay/hw/misc/cxd90014_regfile.c` | `5af707173ac15a85729b4e0da771c1278922da760b483c0480f7ccf604bb753a` |
| `qemu-a6400/tools/a6400_probe.py` | `80d018c4de10e94ca97a0e3f99914c251f754c70ea4791401f47153b201498bc` |
| `qemu-a6400/patches/qemu-v10.1.0-sony-a6400.patch` | `8e25df640fc6f4a7828140c41873a543702353f7272683637cb380dd208927fd` |

Commands: `python3 tools/a6400_probe.py --until 'Og 001_ObjMic' --cmd …` (stall snapshots), and
`--until 'E  Og 001_ObjMic' --timeout 80 …` (completion check). Static work used
`arm-linux-gnueabi-objdump -d -C` on the two scenario plugins.

## 2. Next boundary

1. **Find what av-cam ADF needs to complete `PROC_SET_PATH 0xA0` / `INPUT_SET_INPUT (1,6)`.**
   Decode ADF's handler in the a6400 `av-cam.bin` (REA/Ghidra; a5000 puts ADF around 0x43555000 and
   AIP at 0x438A5000 in its build). Or decode av-cam's binary log (`ulogio_blog.dat`, via the av-cam log
   table) for the ADF/AIP lines around the request.
2. Identify the owners of the unconsumed queues `0x465` (10 msgs), `0x3C4`, `0x5A9`, `0xEF`, `0x42C`.
