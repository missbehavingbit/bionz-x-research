# E0657 - Unicorn L3 allocator ownership, GLES shared-memory initialization, Sugilite GPU execution, and first `viewRootPowerSw.uxc` OSD frames

Date: 2026-11-20. Offline only; no camera, USB, physical SD, or firmware writes.

## Result and claim boundary

**Original allocation/free ownership across unmodified `im.elf` (`1.50B` instructions, `120` threads) is clean without `0x4C -> 0x50` padding; `/dev/dmpgles2` 128 KiB shared-memory initialization is recovered from original kernel/user code; and unmodified `Appframework` executes `24` real Sugilite GPU jobs + `24` IRQ 76 completions to present its first double-buffered `640x480` RGBA4444 `viewRootPowerSw.uxc` frames onto `OSD A` (`0x23A80000`) and `OSD B` (`0x23B16000`).**

The presented root frames are valid all-transparent RGBA4444 (`0x0000`, `614,400` bytes written via Sugilite `PSC0` fill + `PPF` detile/flip) rather than a populated shooting or menu screen: original-code evaluation of all 10 live `DefRootView::CheckerBase` objects proves `CheckerUSBAndClockReady=1` and `CheckerBootKey=1`, while `CheckerBootState=0`, `CheckerBootType=0`, `CheckerUUGUpdateInit=0`, and `CheckerNeedSalvage=0` await external `av-cam.bin` RTOS / sub-micro `ObjPower` (`0x0059000E` -> `0x0055011E..0x00550123`) boot-mode producers. No RTOS or sub-micro replies were fabricated to force a non-transparent child view. Separately, deterministic unit execution verifies `SugiliteGpuEngine`'s `P3D` command-list + DMP unified vertex/fragment shader + triangle rasterizer + `PPF` detile pipeline (`0xF00F` shaded RGBA4444 pixels).

Earlier strict baselines remain intact: the [E0530](./0530-gles-tgid-completion-and-shared-memory-boundary.md) `kernel-bound-tgid` process replay still stops at instruction **146,135,735** (`libObj.so+0x72BF96`, `unsupported_gles_contract` / `131,072` unknown shared bytes), and `gles_completion_probe` still passes **8/8** checks.

## Identities and provenance

| Input / Artifact | SHA-256 |
| --- | --- |
| `im.elf` (`nflasha15/bin/im.elf`) | `866729eb0bc1e6f5aba04d8bf8e45491985afe89a078ccb106a1ce2476865225` |
| `libc-2.11.2.so` (`nflasha7/lib/libc-2.11.2.so`) | `bceb5cfb7769b54bf9c9d178b8a5f4579531c0f60e673cbcae872a8c7a3508fb` |
| `libObj.so` (`nflasha15/lib/libObj.so`) | `60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e88b1` — full: `60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1` |
| `grm_gles.ko` (`nflasha15/kmod/grm_gles.ko`) | `a8d1dbe8c66be365d32371b13f35a0e7d598c415c0ff17f2c8f544084ccc6882` |
| `grm_ma.ko` (`nflasha15/kmod/grm_ma.ko`) | `4bd42001061c8c6f163ede217b0cadf117f2e9f4e23723c0658ac5c3d29f163f` |
| `dmm.ko` (`nflasha15/kmod/dmm.ko`) | `c5cb62579f4719bed9248b8e3dabe4d95c6fa57481e07908f10b68098e54a74f` |
| `ldec.ko` (`nflasha7/kmod/ldec.ko`) | `fef49a7ef9a1e3d52e881c303da7423a37556ef9a5fcf1e1d065c514a7c55a18` |
| `libdig_level.so` (`nflasha15/lib/libdig_level.so`) | `dfa75b6020100669d16aa3da48f13218c6e2b702e078925c2b26dbf5a2c308ad` |
| `viewUnified2.so` (`nflasha15/lib/viewUnified2.so`) | `1e2867b6bff2d4fd4d3b93bacf8c7da0b9a86f266b4ba1763230e33badb6e7f2` |
| `CautionConfig.so` (`nflasha15/lib/CautionConfig.so`) | `bfd1bd7bad0ab3478b894da5dbb51c15464b1bedc4201240ccb61cd743150ef7` |
| `viewRootPowerSw.uxc` (`nflasha15/share/app/viewRootPowerSw.uxc`) | `82905912c1791a4d5f4214bf1fbc05ca8350f5190a70510344627025c77f4578` |
| Packaged WBI `nflasha5` | `19a702b030306a32f4287131da314e5c89d66163997f0f9a23b0967981b0001b` |
| Pre-GLES baseline `cold-01.ckpt` (`69,827,675` insns) | `1169ac4a83d91bc1d6b6aa36a6a206eb09c1b48c37ae74d9b7783355a78172d2` |
| `gles-and-allocator-isolation.json` & `-repeat.json` | `f3f086122f74b59d0f293628225268261b2af07b82dc8cdb5d2d8601a1021845` |
| `e0530-regression-baseline.json` | `c16155d236f06f3737ed754f253d00407ca4e0b524ebf81cfa439ffadbd56064` |
| `run-12.json` (`1,150,006,753` insns, `24` GPU jobs) | `7e984076f9e456c6ebe934ba03b93f5dc75c0bad2236a7d125d14d7a6dc7c6a5` |
| `frames-12/osd_a.png` & `frames-12/osd_b.png` | `d614f328a8a46a034df38fffe6a0c492754871e086b3a75d517ec7494cc6a340` |
| `run-15.json` (`1,450,000,152` insns, `THREAD_INFRA1` complete) | `76ee52bf00f1dd50058bed56930d5c54979bd03588c216a338f3d3dc592bd7f7` |
| `run-15.ckpt` (`1,450,000,152` insns) | `bb8bd3461b0bafd76dd0bfe32cafd911b414b7ef13e88152eedb2dcacc3be6dc` |

Prior related-repository reference consulted before deriving Sugilite P3D/PPF/PSC register and shader contracts: [`Oein/a5000-emulator` at `6ad36bd7b0411d55a5920447ff51e3de01f8cb9b`](https://github.com/Oein/a5000-emulator/tree/6ad36bd7b0411d55a5920447ff51e3de01f8cb9b) (`docs/RE_DISPLAY_GPU.md`, `docs/RE_GPU_P3D_CMD.md`, `docs/RE_GPU_P3D_SHADER.md`, CC BY 4.0). All ILCE-6400 kernel driver (`grm_gles.ko`, `grm_ma.ko`, `dmm.ko`, `ldec.ko`) and user-space (`libObj.so`, `libc-2.11.2.so`, `libdig_level.so`) contracts were verified directly against the hash-pinned ILCE-6400 binaries above.

## 1. Original allocation/free ownership and Unicorn 2.1.4 Thumb-2 stale-`ITSTATE` root cause

Tracing allocation/free ownership with `--heap-ownership-trace` and **without** `--guest-patch-logalloc-0x50` from `cold-01.ckpt` (`69,827,675` instructions) refuted the historical `stella::HeapAllocator` `0x4C` pool-boundary overrun hypothesis ([E0526](./0526-stella-allocator-size-isolation-and-padding-trial.md)) and exposed a systematic **Unicorn 2.1.4 ARM TCG Thumb-2 stale `ITSTATE` (`CPSR` condexec bits) bug**:

1. **`_int_malloc` largebin victim scan (`libc-2.11.2.so+0x59820`)**: When an `IT` block exits via a conditional/unconditional branch inside `malloc.o`, Unicorn 2.1.4 can enter a target basic block with non-zero `env->condexec_bits`. At `+0x59820`, skipping `adds r3, #0x38` leaves `r3 = 0x10` instead of `0x48` (`smallbin[9]`), tripping `malloc.c:4652: Assertion (unsigned long)(size) >= (unsigned long)(nb) failed` on a valid `0x200` allocation.
2. **`_int_free` largebin unlink targets (`+0x57C04`, `+0x57C44`, `+0x589C0`, `+0x589E0`, `+0x58B42`)**: Preceded by `iteee eq / iteee eq / it ne / strne / b.n`, entering these targets with stale `ITSTATE = 0x0F` skipped unlink pointer updates.
3. **`_int_malloc` return epilogue (`+0x59320: mov r0, r6`, observed at `71.67M` and `140.38M`)**: Both the secondary-arena fast exit (`itt ne; strne ...; strne ...; beq.n 0x59320`) and the `main_arena` smallbin exact-fit exit (`itt ne; ...; beq.w 0x59320`) enter `0x50259320: mov r0, r6` with a single-instruction stale `IT` mask (`0x8`), skipping `mov r0, r6` and returning internal bin/arena state (`r0 = 0x5EE00010` or `r0 = 0x0`) instead of the allocated user pointer `r6`.
4. **Cross-library Thumb-2 `IT` exits (`libObj.so+0x55F294 -> +0x57CCC0`, observed at `948.10M`)**: `libObj.so+0x55F294` executes `ite ge; movge r0, r2; movlt r0, r3; pop {r4, r7, pc}` returning to `+0x57CCC0: add.w r7, r7, #0x14`. Because QEMU/Unicorn TCG bakes `env->condexec_bits` into `tb->flags` inside `tb_find` **before** `UC_HOOK_BLOCK` runs, clearing `CPSR` inside `UC_HOOK_BLOCK` alone does not prevent the already-selected TB from conditionally skipping its first instruction.

### Implemented fix ([interpreter_probe.py](../../src/a6400/execution/interpreter_probe.py))

- Signature-checked `install_libc_it_latch_repairs` arms `UC_HOOK_CODE` split/repair hooks on `libc-2.11.2.so` (`LIBC_IT_LATCH_TARGETS`, `+0x59320`, `LIBC_EPILOGUE_MOV_R0_REPAIRS` at `+0x59322`/`+0x5A0EE`/`+0x5BE24`/`+0x5C04A`/`+0x5C2CE`, and `+0x59822`).
- Architectural Thumb-2 block-entry guard in `InterpreterProbe._block` + `run_slice`: whenever a Thumb TB begins with non-zero `CPSR` `ITSTATE` (`0x0600FC00`) and `_thumb_it_legitimately_spans(uc, address)` proves no preceding `IT` instruction covers `address` (including page-start clamping when `address - 18` crosses an unmapped lower page), `_block` clears `ITSTATE`, records `_it_restart_pc = address | 1`, and calls `uc.emu_stop()` so `run_slice` immediately restarts `emu_start` with clean `tb->flags` (respecting a monotonic wall-clock `deadline_s` bound).
- Historical `202,579,425` `stella_abort` (`libObj.so+0x64142A`): once the `ITSTATE` corruptions were eliminated, `im.elf` ran cleanly past every former heap failure with **zero** `HeapOwnershipTrace` errors and revealed that the `202.58M` abort was `D/UXEngine(1000): 0: CreateFramebuffer failed` (`GraphicsSystem_gemini.cpp:185/216`) because `captured-e0044` refused `ma_dmm_open_impl(unit=0x4092E)` for the `640x480` OSD A/B pools.

## 2. Recovered `/dev/dmpgles2` 128 KiB shared-memory initialization contract

Static disassembly and isolated original-code execution ([gles_shared_init_probe.py](../../tools/research/gles_shared_init_probe.py)) establish the exact division of initialization between `grm_gles.ko` and `libObj.so`:

1. **Kernel first-open (`grm_gles.ko` `sugilite_open`, `.text+0x43C..0x548`)**:
   - Increments `dev->open_count` (`atomic_inc_return`).
   - Only when `open_count` transitions **`0 -> 1`** (`+0x47C..+0x49C`): clears `dev+0x44` (`event_seq = 0`), calls `__memzero(dev->shared_area, 4)` (zeroing **only bytes `0..3`** and leaving bytes `0x0004..0x1FFFF` = `131,068` bytes untouched), and resets `dev->sems[0..2]` to count `1`.
   - On subsequent opens (`1 -> 2`), `sugilite_open` leaves `[shared_area + 0]` untouched.
2. **User-space ring-queue init (`libObj.so` `rm_rqueue_init`, `+0x72BF80..0x72C1D2`)**:
   - Locks semaphore `1` (`ioctl 0xC0048211`), `mmap2`s `0x20000` (`MAP_SHARED`), and reads `[shared_va + 0]` at `+0x72BF96` (the exact [E0530](./0530-gles-tgid-completion-and-shared-memory-boundary.md) stop).
   - Sees `0` on first open, calls guest `memset` across the entire `0x20000` region (driving `shared_unknown` from `131,068 -> 0` with zero uninitialized-read faults), writes `1` to `[shared_va + 0]` at `+0x72C164`, initializes the ring-queue headers (`0x1, 0xffffffff, 0xffffffff, 0xffffffff, ...`), and unlocks semaphore `1` (`0xC0048212`).
   - Second opens (`1 -> 2`) map a coherent alias of the same 128 KiB backing, observe `[shared_va + 0] == 1`, and attach without re-zeroing.
3. **IRQ 76 handler (`grm_gles.ko` `sugilite_interrupt`, `.text+0x658..0x738`)**:
   - Reads `r6 = [BAR + 0x34]`; writes `0 -> BAR + 0x1000` (`0x80000000` P3D), `0 -> BAR + 0xC18` (`0x40000000` PPF), `0 -> BAR + 0x1C` (`0x04000000` PSC0), and `1 -> BAR + 0x3C / 0x44` (`r6 & 0xF`); re-reads `r7 = [BAR + 0x34]`; ORs `(r6 | r7) & 0xFFFF0000` into `dev + 0xA8`; and wakes `dev + 0x4C` (`ioctl 0xC0048203`).

Two repeated runs of `python -m tools.research.gles_shared_init_probe` produce byte-identical JSON receipts (`SHA-256 f3f086122f74b59d0f293628225268261b2af07b82dc8cdb5d2d8601a1021845`).

## 3. Coherent physical DRAM, WBI DMM/GRM pools, `/dev/ldec` DMA, Sugilite GPU execution, and `viewRootPowerSw.uxc`

To execute `Appframework` through real GPU submission and OSD scanout without touching default policies, the following opt-in components were added:

- **`GuestPhysicalRam` ([guest_physical_ram.py](../../src/a6400/execution/guest_physical_ram.py))**: Coherent 64 MiB `ctypes`-backed physical DRAM banks (`< 0x40000000`) mapped via `mu.mem_map_ptr` across `/dev/stream`, `/dev/mem`, `/dev/ldec` (verified in `ldec.ko` `.text+0x03C0..0x0408`: `job.dst` is a physical DMA address translated via `phys_to_virt`), and Sugilite GPU DMA, with zlib-compressed checkpoint persistence in [guest_checkpoint.py](../../src/a6400/execution/guest_checkpoint.py).
- **`captured-wbi-dmm` ([grm_service.py](../../src/a6400/execution/grm_service.py))**: Serves all 6 packaged-WBI `nflasha5` + `dmm.ko` (`VA 0x5F1709E0` rows `23, 28..32`) + `grm_ma.ko` pools, including sequential `unit=0x4092E` opens for `OSD A` (`handle 0x041C`, `PA 0x23A80000`, `0x96000`) and `OSD B` (`handle 0x0420`, `PA 0x23B16000`, `0x96000`), whole-unit `flag44 == 0` allocation/free, and mspace sub-allocation/free on `gl_common` (`0x202E0000`), `mpMemoryHeap` (`0x222E0000`), `mpTextHeap` (`0x22880000`), and `mpMemoryHeap2` (`0x226E0000`).
- **`kernel-sugilite` & `SugiliteGpuEngine` ([guest_gles.py](../../src/a6400/execution/guest_gles.py), [sugilite_gpu.py](../../src/a6400/execution/sugilite_gpu.py))**: Implements first-open 4-byte shared zeroing, coherent 128 KiB `_shared_backing` aliases, blocking semaphores `0xC0048211`/`0xC0048212`, `0x80048207`/`0xC0048208`/`0xC004820C`/`0xC004820F`, `sugilite_register_init`, `PSC0` memory fills, `PPF` `8x8` Morton detile/tile/flip/copy transfers, `P3D` command-list + DMP unified shader + rasterizer execution, and IRQ 76 `dev+0xA8` status accumulation + selective `irq_waiters` wakeups.
- **Scheduler & finite `rcv_msg_tmo` fixes ([guest_threads.py](../../src/a6400/execution/guest_threads.py), [experiment_ui_boot.py](../../tools/research/experiment_ui_boot.py))**: Fixed `GuestThreads.get_next_runnable_tid` so idle `clock_floor` fast-forward only triggers when `current_tid` is not `'running'`, and re-armed finite `rcv_msg_tmo` deadlines on `--uipc-rcv-timeout modeled` checkpoint resume. Disassembly of `libdig_level.so` (`+0x3B30..0x3B66`) proved `psm_dig_level_ma` (`TID 1065`) drains `0x00A3035B` with a `1 ms` `rcv_msg_tmo` before sending `{1}` to `0x00A3035A`, resolving the [E0309](../architecture/l3-userspace-and-ipc.md) `THREAD_INFRA1` (`TID 1012`) wait and advancing `main` (`TID 1000`) into steady-state `sleep`.

### Observed L3 progression (`69.8M -> 1,500,010,445` instructions)

| Milestone | Instructions | Verified Guest Observation |
| --- | ---: | --- |
| `cold-01.ckpt` | `69,827,675` | Pre-GLES baseline (`0` GLES owners, `0` padding patches) |
| `run-02..04` | `148,928,428` | `/dev/dmpgles2` first open zeroes 4 bytes; guest `rm_rqueue_init` zeroes all 128 KiB (`shared_unknown = 0`); `/mnt` mounted read-only via `observed-nflasha1` |
| `run-05` | `206,413,762` | `CreateFramebuffer` succeeds on `OSD A` (`0x23A80000`) and `OSD B` (`0x23B16000`); `19` `P3D` setup command lists (`0x202E1800..0x20385B40`) + `1` `PPF` `640x480` RGBA4444 detile/flip -> `OSD A` + `20` IRQ 76 completions; `Appframework` begins loading `viewUnified2.so` (`137,966` relocations) and `CautionConfig.so` (`205,250` relocations) |
| `run-09..11` | `982,986,294` | `CautionConfig.so` finishes relocating; `62` `/dev/ldec` hardware LZ77 decompressions write UI atlases (`image_cmn_169.uxc`, `image_EVF.uxc`, `image_DSLR_Only_169.uxc`) into `mpMemoryHeap2` (`0x226E0000`); loads `string_english.uxc`, `UniversOTS-SJ_wIcon.ltt`, `Sony_DI_Icons.ttf`, `ModelCautionConfig.so`, `CautionRsrcConfig.so`, and `viewRootPowerSw.uxc` |
| `run-12` | `1,150,006,753` | `Appframework` executes `2` `PSC0` 614,400-byte fills (`0x202F0200..0x20386200`) and `2` more `PPF` `640x480` RGBA4444 detile/flip transfers (`-> OSD B` `0x23B16000`, `-> OSD A` `0x23A80000`), reaching **`24` Sugilite GPU jobs and `24` IRQ 76 completions**, exports `frames-12/osd_a.png` and `frames-12/osd_b.png`, and enters `Appframework`'s steady-state `poll(pipe:[3], timeout=2992 ms)` event loop (`0` heap ownership errors) |
| `run-14..16` | `1,500,010,445` | `psm_dig_level_ma` (`TID 1065`) completes `1 ms` `0x00A3035B` drain and sends `{1}` to `0x00A3035A`; `THREAD_INFRA1` (`TID 1012`) finishes and joins `main`; `InfraMediaPwrMgr` (`TID 1119`, `120` threads total) spawns; `main` (`TID 1000`) advances past `0x0094016A` into steady-state `sleep` (`146,220` syscalls, `0` heap ownership errors) |

### Reproduce key L3 GPU segment

```powershell
.\venv\Scripts\python.exe -m tools.research.gles_shared_init_probe `
  --output artifacts\analysis\NEW_L3_GPU\gles-and-allocator-isolation.json

.\venv\Scripts\python.exe -u -m tools.research.experiment_ui_boot `
  --load-checkpoint artifacts\execution\unicorn-l3-gpu-20261119\run-15.ckpt `
  --instructions 1455000000 --timeout-us 120000000 --thread-timeslice 5000 `
  --heap-ownership-trace --gles-device kernel-bound-sugilite `
  --grm-pools captured-wbi-dmm --mount-service observed-nflasha1 `
  --nflasha-image 1=firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha1 `
  --ldec-device lz77 --uipc-rcv-timeout modeled --uipc-timer-service emulated `
  --nanosleep-policy timed --thread-poll-yield `
  --display-contract camera-e0074-e0044 `
  --display-output-dir artifacts\execution\NEW_L3_GPU\frames `
  --quiet-boot --report artifacts\execution\NEW_L3_GPU\report.json
```

## 4. Exact next boundary: why `viewRootPowerSw.uxc` is transparent without RTOS/sub-micro producers

`viewRootPowerSw.uxc` is the sole `viewRoot*.uxc` scene on ILCE-6400. Calling `IsReady()` on all 10 live `DefRootView::CheckerBase` C++ objects in `run-15.ckpt` via original guest instructions shows why `DefRootView` renders a transparent `640x480` RGBA4444 root frame instead of dispatching `ActionOpenBootRecMode` / `ActionOpenInitialSetting` / `ActionOpenSettingMenu`:

- **Ready (`IsReady() == 1`)**: `CheckerUSBAndClockReady`, `CheckerBootKey`.
- **Blocked (`IsReady() == 0`)**:
  1. `CheckerBootState` (`model/ROOT` param `0x104 == 1`, waiting for `0x10B == 4`),
  2. `CheckerBootType` (`model/ROOT` param `0x10B == 0`; `M_ROOT + 0x8C == -1`, logged as `M_ROOT: BootType:Unknown Value 0x1C`, set to `1` at `libObj.so+0x6B6CE8` only when `ObjPower` / `UPM` delivers `(0x3900, 0x3003)` and `av-cam.bin` answers `0x0059000E` -> `0x0055011E..0x00550123`),
  3. `CheckerUUGUpdateInit` (`0x523`),
  4. `CheckerNeedSalvage` (`model/SALVAGE` param `0x103`).

Advancing from the transparent `viewRootPowerSw.uxc` root frame to a populated shooting or menu view in standalone Unicorn L3 requires qualified `av-cam.bin` / sub-micro `UPM` (`0x920572` / `0x920021`) and `ObjPower` (`0x0059000E`) contracts rather than synthetic `model/ROOT` writes or injected key presses (`run-13-menu.json` and `run-16-menu.json` confirm `0x007A0020` key events are ignored while `DefRootView` checkers remain `0`).

## Validation

- `.\venv\Scripts\python.exe -m unittest tests.test_sugilite_gpu tests.test_guest_gles tests.test_grm_service tests.test_interpreter_probe tests.test_heap_ownership_trace -q` -> **PASS (`178` tests, `1` skipped)**.
- `.\venv\Scripts\python.exe -m tools.research.gles_shared_init_probe` (two runs) -> **PASS (identical SHA-256 `f3f086122f74b59d...`)**.
- `.\venv\Scripts\python.exe -m tools.research.gles_completion_probe` -> **PASS (`8/8` checks)**.
- [E0530](./0530-gles-tgid-completion-and-shared-memory-boundary.md) `kernel-bound-tgid` regression replay -> **PASS (stops identically at `146,135,735` instructions, `unsupported_gles_contract`)**.
- `.\venv\Scripts\python.exe -m unittest discover -s tests -q` -> **PASS (`2,185` tests, `129` skipped, `0` failures/errors)**.
- `.\venv\Scripts\python.exe -m tools.research.ci_check --summary` -> **PASS (`5/5` guardrails)**.
