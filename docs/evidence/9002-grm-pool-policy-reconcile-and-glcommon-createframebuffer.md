# E9002 - Checkpoint resume reconciles the GRM pool policy, and the labelled single-pool boot passes CreateFramebuffer

Date: 2026-11-21. Offline process and module execution only; no camera access.

ID: E9002
Claim: On checkpoint resume, the session launcher now reconciles the restored
GRM pool policy with --grm-pools. An unspecified flag adopts the checkpoint's
effective policy and records it in the report; a matching request passes; an
explicit conflicting request fails closed. Before the fix, an explicit
--grm-pools gl-common-only was silently ignored: the restored captured-e0044
service still refused DMM unit 0x4092e with -9, while the report claimed
gl-common-only. Separately, under the explicitly labelled legacy single-pool
policy (fresh boot, checkpoint at 141,980,014 before the known 141,986,619
Unicorn stale-IT fault, then a resume that flushes translation blocks),
CreateFramebuffer(640,480) succeeds and TID 1087 (Appframework) stays runnable
to the 215,001,600-instruction budget with no tgkill and no stella assertion.
This does NOT establish that DMM unit 0x4092e maps to gl_common.
Status: verified for the policy-handling defect, its fix and the run
observation; the unit mapping, physical base and DMM handle remain unknown.

Firmware hash / camera configuration:
- libObj.so 60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1
- kmod/dmm.ko c5cb62579f4719bed9248b8e3dabe4d95c6fa57481e07908f10b68098e54a74f
- kmod/grm_ma.ko 4bd42001061c8c6f163ede217b0cadf117f2e9f4e23723c0658ac5c3d29f163f
- setting/DmmConfig.bin 3e68b4f482ba24fceea1b572c8e2c5ee132f3fdd93ade64e634df47448bd3abd
- Target: ILCE-6400 V2.00 (CXD90045), im.elf debug s g r u l i a lazy-global.
- Source checkpoint gemini-runs/pre-app.ckpt (captured-e0044)
  96ce900e56045fcd729504d7be99b9bcc43374bd13f3baed9ce298500b87b5e8.
- gl-common-only checkpoint gemini-runs/pre-malloc-glcommon.ckpt
  2d95aed4209bb1df239c893b86f3c37a58cdd39f0b12efe8d68e6ea02565bdfd.
All firmware inputs are hash-checked against artifacts/inventory/baseline.json.

Source:
- tools/research/emulator_session.py: restore path and the new
  reconcile_grm_pool_policy(requested, restored, report) helper.
- tools/research/experiment_ui_boot.py: --grm-pools now defaults to None
  (unspecified) instead of gl-common-only.
- src/a6400/execution/guest_checkpoint.py: restore replaces the whole UIPC
  subsystem, which is why the restored service owns the policy.
- Reports: gemini-runs/resume-13-glcommon-only.json (pre-fix),
  gemini-runs/boot-15-stage2.json.

Method:
- Pre-fix experiment: gemini-runs/run_resume_13.sh resumes pre-app.ckpt with an
  explicit --grm-pools gl-common-only and the flags recorded in E9001.
- Post-fix conflicting request: the same command, which now stops before
  execution with an explicit ValueError.
- Fresh boot: gemini-runs/run_boot_14.sh boots with --grm-pools gl-common-only
  and no checkpoint.
- Stage 1: gemini-runs/run_boot_15_stage1.sh boots gl-common-only with
  --stop-instruction 141980000 --save-checkpoint
  gemini-runs/pre-malloc-glcommon.ckpt.
- Stage 2: gemini-runs/run_boot_15_stage2.sh resumes that checkpoint with
  --grm-pools gl-common-only to a 215,000,000-instruction budget.
- Tests: ./venv/bin/python -m unittest discover -s tests -q.

Observation:
1. Pre-fix resume-13 reports grm_pool_policy=gl-common-only, but its only GRM
   transaction is ma_dmm_open_impl_unsupported_unit status -9 (the restored
   captured-e0044 service). The run is instruction-identical to the refusal run:
   200,429,509 and tgkill(1000,1087,6). The flag changed the report but not the
   behaviour.
2. Post-fix resume-13 stops before execution with:
   ValueError: checkpoint GRM pool policy is captured-e0044; requested
   gl-common-only requires a fresh boot (allocator state is policy-specific).
3. Post-fix matching resume (captured-e0044) is unchanged: 200,429,509,
   tgkill(1000,1087,6), report grm_pool_policy=captured-e0044.
4. The fresh gl-common-only boot (no checkpoint) reproduces the known Unicorn
   stale-IT fault instead of a policy boundary: memory_boundary at 141,986,619,
   PC 0x5025B3E8, access 19 to 0x82755166, size 4. This is the same fault as
   the documented 141,986,579 window and is not evidence about the GRM policy.
5. Stage 1 stops and saves at 141,980,014 (status instruction_budget_exhausted
   from the observation-only hard stop).
6. Stage 2 resumes that checkpoint (translation-block flush) to the budget:
   215,001,600 instructions, status instruction_budget_exhausted.
   - TID 1087 (Appframework) is state=runnable.
   - TID 1086 (THREAD_APP) is state=terminated.
   - syscall_name_counts has no tgkill.
   - stderr has no CreateFramebuffer failure and no assertion.
   - framebuffer transactions = 0.
   The run therefore passes instruction 200,429,509, where the captured-e0044
   lineage aborts, and continues for another 14,572,091 instructions.
7. Tests: 2,148 tests ran; the only error is the pre-existing
   test_camera_gui_window ImportError (tkinter is not installed), identical to
   gemini-runs/unittest_baseline_full.log; 129 skipped; no new failure.

Alternative explanations:
- The flag was honoured and the policy itself produced -9: ruled out because the
  pre-fix transaction is the restored captured-e0044 refusal
  (ma_dmm_open_impl_unsupported_unit), and the post-fix command fails closed
  instead of running.
- Appframework passed CreateFramebuffer but stalled while other threads ran:
  TID 1087 is runnable at the budget, not blocked or terminated, and the
  captured-e0044 abort path is absent; a per-thread PC trace would strengthen
  this further.
- Scheduling or input differences caused the extra instructions: the stage-1
  checkpoint is at exactly 141,980,014, the same count as the captured-e0044
  pre-malloc checkpoint, and the boot inputs are unchanged.
- gl-common-only is authentic for unit 0x4092e: not claimed and not supported.
  It is the legacy single-pool approximation; E9001 shows the unit has its own
  DMM extent (0x62/0x3f0, size 0x1fa400).

Confidence and limitations:
- High confidence in the policy-handling defect and fix: the pre-fix report and
  transaction disagree, and the post-fix conflict is rejected explicitly.
- Medium-high confidence that CreateFramebuffer succeeded under the labelled
  policy: no abort, TID 1087 runnable, 14,572,091 further instructions. The
  return value of +0x71F408 was not separately hooked in this run, so the
  success is inferred from behaviour rather than read directly.
- The run stops at an instruction budget, not at a new contract boundary; the
  next genuine boundary is still unknown.
- No camera access, no physical write and no push. The 215M run is a labelled
  approximation, not a fidelity result for the real allocator.

Dependent implementation/scenarios:
- Every checkpoint resume now records the restored GRM policy. An explicit
  conflicting --grm-pools request fails before execution instead of being
  ignored.
- --grm-pools default is now unspecified: a fresh boot resolves to
  gl-common-only, a resume adopts the checkpoint policy.
- The graphics-service policy is also recorded from the restored UIPC and a
  mismatch is printed; it is not yet a hard error.
- Regression tests: tests/test_session_grm_policy.py.

Next experiment:
- Locate the next real boundary by extending the labelled gl-common-only run,
  then return to the authentic path: model the
  DMM timer and reservations and obtain an executed base and handle for unit
  0x4092e before any opt-in GRM mapping is added.
