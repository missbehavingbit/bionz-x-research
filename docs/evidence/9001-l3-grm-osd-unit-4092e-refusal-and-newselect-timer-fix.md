# E9001 - Appframework CreateFramebuffer(640,480) is a GRM-MA open refusal for DMM unit 0x4092e, and checkpoint restore no longer fabricates a sys__newselect deadline

Date: 2026-11-20. Offline process and module execution only; no camera access.

ID: E9001
Claim: In the cold-boot L3 lineage, TID 1087 (Appframework) reaches
CreateFramebuffer(640,480), which issues a synchronous GRM-MA (queue
0x014c0424) opcode-1 open for DMM unit 0x0004092e. The opt-in captured-e0044
policy answers MA_ErrNotFound (-9) because only unit 0x0280 (gl_common) has an
executed open-path mapping; the guest propagates -9 into libObj.so+0x64142A
(stella_abort). Separately, checkpoint restore now re-arms a blocked_poll timer
only for sys_poll (r7=168); sys__newselect (r7=142) no longer fabricates a
deadline from its writefds pointer.
Status: verified (wire bytes, caller index, reply status and restore behavior);
the runtime physical base and DMM handle for 0x4092e remain unknown.

Firmware hash / camera configuration:
- libObj.so 60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1
- kmod/dmm.ko c5cb62579f4719bed9248b8e3dabe4d95c6fa57481e07908f10b68098e54a74f
- kmod/grm_ma.ko 4bd42001061c8c6f163ede217b0cadf117f2e9f4e23723c0658ac5c3d29f163f
- setting/DmmConfig.bin 3e68b4f482ba24fceea1b572c8e2c5ee132f3fdd93ade64e634df47448bd3abd
- Target: ILCE-6400 V2.00 (CXD90045), im.elf debug s g r u l i a lazy-global.
- Resume checkpoint gemini-runs/pre-app.ckpt
  96ce900e56045fcd729504d7be99b9bcc43374bd13f3baed9ce298500b87b5e8
  at 195,500,013 instructions, TID 1078.
All firmware inputs are hash-checked against artifacts/inventory/baseline.json.

Source:
- libObj.so (module base 0x51690000): +0x70CD18 record table (0x28-byte stride,
  [record+8] selects the unit), +0x71F408 request object, +0x720620 wire
  construction, +0x720604 send, +0x72005C UIPC tail call, status read at
  [msg+0x1C].
- libObj.so file offset 0x0137a114, record index 2: +0x00=0x00000002,
  +0x04=0x00010002, +0x08=0x0004092e, +0x10=0x01e00280 (width 640, height 480),
  +0x14=0x01e00280.
- setting/DmmConfig.bin offset 0x1057c (header 0x10570 + 0x0c) contains the same
  0x0004092e word; the original dmm.ko parsers place it in the unit table:
  unit 0x4092e -> extent 0x62 -> leaf extent 0x3f0, size 0x1fa400 (2,073,600
  bytes).
- Pre-fix restore logic: src/a6400/execution/guest_checkpoint.py:340-343.

Method:
- Resume the cold-boot checkpoint with taskset -c 0-3, --thread-timeslice 50000,
  --graphics-service authentic-grm --grm-pools captured-e0044,
  --uipc-pool-options kernel-e0301, and the remaining flags recorded in
  gemini-runs/run_resume_11.sh.
- Hook only 0x51690000+0x70CD18 (one instruction address) to read the caller
  index; no guest state is modified.
- Wrap GrmMaService.handle_uipc_sync_message to copy the request and reply bytes
  without changing the reply (gemini-runs/trace_uipc_4092e.py).
- Parse DmmConfig.bin with the original dmm.ko routines in the bounded
  cold-module slice:
  ./venv/bin/python -m tools.research.dmm_unit_lookup 0x4092e 0x280 0xe1700.

Observation:
1. +0x70CD18 is entered exactly once: instruction 196,136,153, r0=0x52a7fda8,
   r1 (index)=2, r2=0x00061438. Index 2 is the 640x480 record whose +0x08 word
   is 0x0004092e.
2. The GRM-MA transaction is snd_sync_msg to queue 0x014c0424, tmo=0, flag=0,
   msg_ptr=0x5033010c, recorded at instruction 196,137,765 in the traced run.
   Request bytes:
   010080002e090400000000004d013e00000000000000000005002e090400020000000100000002000000736f000000006e6f2000
   The service reads opcode 1 at +0 and unit 0x0004092e at +4; the name area is
   empty because the constructor at +0x72067A writes a single zero byte when the
   name pointer is NULL.
3. The reply writes 0xfffffff7 (-9, MA_ErrNotFound) at +0x1c, with zero base,
   size and handle:
   010080002e090400000000004d013e00000000000000000005002e09f7ffffff000000000000000000000000000000006e6f2000
4. +0x720604 returns [msg+0x1c] = -9; +0x71F408 destroys the temporary and
   returns -9; +0x70CD18 takes the error-log path at +0x70CD7A and returns 1.
   The run still stops at 200,429,509 instructions with sys_tgkill(1000, 1087,
   6), PC 0x50215796.
5. The opt-in transaction label now states the refusal instead of claiming a
   verified allocation: authentic-grm explicit refusal:
   ma_dmm_open_impl_unsupported_unit status -9; no pool or allocation supplied.
6. Original-parser output: 0x4092e -> extent 0x62 -> leaf extent 0x3f0, size
   0x1fa400. Calibration: 0x280 -> leaf extent 0x35d, size 0x2000000 (32 MiB),
   which matches E0035. 0xe1700 -> extent 0xe2 -> leaf extent 0x24c, size
   0x200000.
7. Restore from pre-app.ckpt after the fix reports four blocked_poll threads:
   TID 1004 (ULOGIOD_MAIN) r7=142, r2=0x50c7ec04, deadline=None;
   TID 1033/1035/1036 r7=168, r2=0xffffffff, deadline=None. Before the fix,
   TID 1004 obtained the fabricated deadline 1,355,474,864,013
   (195,500,013 + 0x50c7ec04 * 1000). The post-fix run is instruction-identical
   at the boundary: 200,429,509.

Alternative explanations:
- Heap corruption or a stale Thumb IT latch: the allocator trace
  gemini-runs/resume-05-heap-trace.json covered 195.50M-200.43M with 927 events
  and zero heap-ownership errors; the deterministic -9
  reply and the single opcode-1 request explain the abort without memory
  corruption.
- A different service or queue: the model records target_queue=0x014c0424 and
  the guest reads the status word at [msg+0x1c]; the reply buffer is the same
  message the service wrote.
- 0x4092e is a UIPC pool id or a gl_common alias: it is absent from the E0044
  pool table, it is not a UIPC message-pool id, and the original parser places
  it in the DMM unit table.
- Wrong record index: ruled out by the runtime hook (r1=2).

Confidence and limitations:
- High confidence in the caller chain, wire bytes, -9 status and restore
  behavior; every value above is reproduced by a bounded local run.
- The meaning of the record table is inferred from its values (format word plus
  packed height<<16|width); no decompiled consumer was used to confirm the
  labels.
- DmmConfig.bin provides a size but no runtime physical base and no DMM handle.
  dmm_unit_ctrl_Alloc/Open still stop at the timer contract (E0035), so a
  successful GRM reply for 0x4092e cannot be produced without fabricating those
  two fields. The default therefore stays an explicit refusal.
- The request's 0x8000 header flag and the trailing words after the 21-byte
  payload were not interpreted; they are not needed to identify the failure.

Dependent implementation/scenarios:
- src/a6400/execution/guest_checkpoint.py: restore no longer treats
  sys__newselect as a finite poll. Any checkpoint containing a blocked_poll
  r7=142 thread is affected.
- src/a6400/execution/guest_uipc.py: GRM transaction classification now follows
  the recorded service status; successful replies are byte-identical.
- Regression tests pin both decisions:
  tests/test_guest_checkpoint.py::TestCheckpointPollRearm and
  tests/test_grm_service.py::TestGrmMaCapturedE0044Policy::
  test_open_osd_unit_0x4092e_stays_explicitly_unmapped and
  test_captured_policy_refusal_is_not_classified_as_gl_common;
  tests/test_dmm_unit_lookup.py covers the parser-table decoding.
- Reproducibility: ./venv/bin/python -m unittest discover -s tests -q ran 2,144
  tests; the only error is the pre-existing test_camera_gui_window ImportError
  (tkinter is not installed), identical to gemini-runs/unittest_baseline_full.log
  (2,056 tests, same error). 129 skipped. No new failure.

Next experiment:
- Model the DMM timer service (0x910042) and the shared reservation log, run
  dmm_unit_ctrl_Init plus boot reservations, then Alloc(0x4092e)/Open in the
  isolated module slice. Only an executed base and handle may be added to
  captured-e0044 as an explicit opt-in; until then 0x4092e must keep failing
  closed.
